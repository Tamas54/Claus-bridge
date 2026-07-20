#!/usr/bin/env python3
"""
ORAKEL-II CSEH BACKTESZT — 2. FOGASKERÉK: TÖBBPÁRTI PANEL-MOTOR
================================================================
A szintetikus cseh választói panel. Bemenet:
  - corpus.jsonl                     (a 2025.10-i választás ELŐTTI hír-korpusz, poll-mentes)
  - STRUCTURAL_MEDIA (config)        (ČT kiegyensúlyozott + Babiš social-fölény + MAFRA-tilt)
  - demográfia                       (CZSO-arányok: kor/iskola/településtípus)

A panel NEM lát pártszámot. A médiakörnyezetből + a demográfiából emergens
TÖBBPÁRTI preferenciát ad (ANO/SPOLU/STAN/Piráti/SPD/Motoristé/Stačilo).

A KÉRDÉS (briefing 6.):
  ELSŐDLEGES (ordinális): a panel rangsora egyezik-e? ANO 1., SPOLU 2., Stačilo kiesik.
  MÁSODLAGOS (magnitúdó): az ANO–SPOLU rés (~11,16 pont) eltalálva-e a vak Flash-sel?

HASZNÁLAT:
  ORAKEL_PROVIDER=siliconflow python3 run_panel.py   # Flash, VAK (elsődleges)
  ORAKEL_PROVIDER=openai      python3 run_panel.py   # gpt-5.4-mini, KONTROLL
"""

import os
import re
import json
import time
import random
import statistics
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass

from config import (
    STRUCTURAL_MEDIA, PARTIES, GROUND_TRUTH, GROUND_TRUTH_RANK,
    GROUND_TRUTH_ANO_SPOLU_GAP, THRESHOLD_SINGLE,
    PANEL_MODEL, PANEL_PROVIDER, PANEL_N, PANEL_SEEDS, PANEL_CONCURRENCY,
)


# ── PROVIDER-RÉTEG (siliconflow=Flash vak | openai=gpt-5.4-mini | anthropic) ──
def _provider_cfg():
    if PANEL_PROVIDER == "openai":
        return ("https://api.openai.com/v1/chat/completions",
                os.environ.get("OPENAI_API_KEY", ""), "openai")
    if PANEL_PROVIDER == "anthropic":
        return ("https://api.anthropic.com/v1/messages",
                os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic")
    return ("https://api.siliconflow.com/v1/chat/completions",
            os.environ.get("SILICONFLOW_API_KEY", ""), "siliconflow")

_ENDPOINT, _API_KEY, _KIND = _provider_cfg()

# ── DEMOGRÁFIA (CZSO-arányok, választókorú cseh népesség — közelítés) ────
AGE_BANDS = [
    ("18-29", 0.16),
    ("30-44", 0.25),
    ("45-59", 0.24),
    ("60+",   0.35),
]
EDU = [
    ("základní (8 tříd)",            0.13),   # alapfok
    ("vyučen/střední bez maturity",  0.33),   # szakképző, érettségi nélkül
    ("střední s maturitou",          0.32),   # érettségi
    ("vysokoškolské",                0.22),   # diploma
]
SETTLEMENT = [
    ("Praha",                 0.12),
    ("velké město (50k+)",    0.25),
    ("menší město (5-50k)",   0.33),
    ("obec/vesnice (<5k)",    0.30),
]


def weighted_pick(rng, items):
    r = rng.random()
    acc = 0.0
    for label, w in items:
        acc += w
        if r <= acc:
            return label
    return items[-1][0]


def build_media_context(corpus):
    """A strukturális média-környezet + a hír-korpusz egyetlen grounding-blokká."""
    sm = STRUCTURAL_MEDIA
    headlines = []
    for row in corpus:
        headlines.append(f"- [{row['source']} | {row['lean']}] {row['title']}: {row['lead'][:180]}")
    random.Random(42).shuffle(headlines)
    headlines = headlines[:400]
    corpus_block = "\n".join(headlines)

    media_env = f"""MEDIÁLNÍ PROSTŘEDÍ (červenec–září 2025, PŘED volbami do Poslanecké sněmovny):

STRUKTURÁLNÍ:
- Veřejnoprávní TV/rozhlas (ČT, ČRo): relativně vyvážené zpravodajství, velký dosah (reach≈{sm['public_tv_reach']}).
- {sm['mafra_note']}
- Sociální sítě ({sm['social_dominance']['platform']}): {sm['social_dominance']['note']}
- Online tisk: smíšený — nezávislý mainstream (Seznam Zprávy, Novinky, ČT24, HN), populisticky/ANO-blízký (iDNES/MAFRA, Parlamentní listy) i vládně-koaliční/Babiš-kritický (Aktuálně, Deník N, Forum24, Echo24).

AKTUÁLNÍ ZPRÁVY (titulek + perex, se zdrojem a politickým směrem):
{corpus_block}
"""
    return media_env


# A cseh választási rendszer STRUKTURÁLIS ténye (env-gated, NEM eredmény-szivárgás).
# A bináris-kollapszus (LLM frontrunner-szaliencia torzítás) ellensúlyozására: a
# rendszer arányos, ~7 versenyképes párttal; sok választó kis pártot támogat. Szimmetrikus,
# egyik pártot sem nevezi favoritnak. Csak ORAKEL_MULTIPARTY_HINT=1 esetén aktív.
_MULTIPARTY_HINT = os.environ.get("ORAKEL_MULTIPARTY_HINT", "0") == "1"
_MULTIPARTY_TEXT = (
    "\n\nPOZNÁMKA O SYSTÉMU: České volby jsou POMĚRNÉ, s ~7 konkurenceschopnými stranami "
    "(ANO, SPOLU, STAN, Piráti, SPD, Motoristé, Stačilo). Mnoho voličů volí menší stranu "
    "z regionálních důvodů, kvůli jednomu tématu nebo na protest — ne jen dvě největší. "
    "Tvá volba ať upřímně odráží tvůj profil a zprávy; klidně i menší stranu, pokud ti sedí."
)


def make_persona_prompt(rng, media_env):
    age = weighted_pick(rng, AGE_BANDS)
    edu = weighted_pick(rng, EDU)
    settlement = weighted_pick(rng, SETTLEMENT)

    system = (
        "Simuluješ českého voliče v týdnech PŘED volbami do Poslanecké sněmovny "
        "(konec září 2025). Sám se rozhoduješ, koho bys volil, výhradně na základě "
        "svého demografického profilu a níže uvedeného mediálního prostředí. "
        "NEPOUŽÍVEJ žádné vnější znalosti o výsledku voleb — žiješ uprostřed kampaně, "
        "budoucnost neznáš. Reaguj upřímně, z životní situace své postavy."
        + (_MULTIPARTY_TEXT if _MULTIPARTY_HINT else "")
    )
    user = f"""TVŮJ PROFIL:
- Věk: {age}
- Vzdělání: {edu}
- Bydliště: {settlement}

{media_env}

OTÁZKA: Kdyby byly tuto neděli volby do Poslanecké sněmovny, kterou stranu bys volil?
Odpověz PŘESNĚ na jednom řádku, v tomto formátu (bez jakéhokoli dalšího textu):
HLAS: <ANO | SPOLU | STAN | Piráti | SPD | Motoristé | Stačilo | jiná | nerozhodnut | nevolím>
"""
    return system, user


def _build_request(system, user):
    if not _API_KEY:
        raise RuntimeError(f"Nincs API kulcs a(z) '{_KIND}' providerhez (.env).")
    if _KIND == "anthropic":
        body = {"model": PANEL_MODEL, "max_tokens": 30, "system": system,
                "messages": [{"role": "user", "content": user}]}
        headers = {"content-type": "application/json", "x-api-key": _API_KEY,
                   "anthropic-version": "2023-06-01"}
    else:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        body = {"model": PANEL_MODEL, "messages": msgs}
        if _KIND == "openai" and PANEL_MODEL.startswith("gpt-5") and "chat" not in PANEL_MODEL:
            body["max_completion_tokens"] = 2000
            body["reasoning_effort"] = "low"
        else:
            body["max_tokens"] = 30
            body["temperature"] = 0.8
            if _KIND == "siliconflow":
                body["thinking"] = {"type": "disabled"}  # Non-Think — V4-forma (kötelező)
        headers = {"content-type": "application/json", "Authorization": f"Bearer {_API_KEY}"}
    return urllib.request.Request(_ENDPOINT, data=json.dumps(body).encode("utf-8"), headers=headers)


def _extract_text(data):
    if _KIND == "anthropic":
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return (data["choices"][0]["message"].get("content") or "").strip()


def call_model(system, user, retries=5):
    last = None
    for attempt in range(retries):
        try:
            req = _build_request(system, user)
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            txt = _extract_text(data)
            if txt:
                return txt
        except Exception as e:  # noqa: BLE001 — transient; back off
            last = e
        if attempt < retries - 1:
            time.sleep(2 ** attempt + random.random())
    raise RuntimeError(f"{_KIND} hívás sikertelen {retries} próba után: {last}")


def parse_vote(text):
    """Cseh többpárti parse. A sorrend SZÁMÍT (ütközés-elkerülés).
    Vigyázat: 'ano' csehül 'igen' is — ezért a HLAS-sor utáni tokent vizsgáljuk,
    és a többi pártot ELŐBB ellenőrizzük."""
    m = re.search(r"HLAS:\s*(.+)", text, re.IGNORECASE)
    raw = (m.group(1) if m else text).strip().lower()
    # nem-szavazó / bizonytalan ELŐSZÖR
    if "nevol" in raw or "nepůjd" in raw or "nepujd" in raw:
        return "nevolim"
    if "nerozhod" in raw or "nevím" in raw or "nevim" in raw:
        return "nerozhodnut"
    # konkrét pártok (egyedi tokenek előbb)
    if "spolu" in raw:
        return "SPOLU"
    if "stačilo" in raw or "stacilo" in raw or "ksčm" in raw or "kscm" in raw or "komunist" in raw:
        return "Stacilo"
    if "stan" in raw or "starostov" in raw:
        return "STAN"
    if "pirát" in raw or "pirat" in raw:
        return "Pirati"
    if "spd" in raw or "okamur" in raw:
        return "SPD"
    if "motorist" in raw or "macinka" in raw:
        return "Motoriste"
    # ANO a végén (token-ütközés a 'ano'='igen'-nel: csak ha tényleg a párt)
    if re.search(r"\bano\b", raw) or "babiš" in raw or "babis" in raw:
        return "ANO"
    if "jin" in raw:   # jiná/jiný/jine
        return "jina"
    return "jina"


def run_seed(corpus, seed):
    rng = random.Random(seed)
    media_env = build_media_context(corpus)
    prompts = [make_persona_prompt(rng, media_env) for _ in range(PANEL_N)]

    def _ask(idx_sysuser):
        i, (system, user) = idx_sysuser
        try:
            return parse_vote(call_model(system, user))
        except Exception as e:  # noqa: BLE001
            print(f"    [!] persona {i}: {e}")
            return "nerozhodnut"

    tally = {}
    with ThreadPoolExecutor(max_workers=PANEL_CONCURRENCY) as ex:
        for n, vote in enumerate(ex.map(_ask, enumerate(prompts)), 1):
            tally[vote] = tally.get(vote, 0) + 1
            if n % 20 == 0:
                print(f"    seed {seed}: {n}/{PANEL_N} persona kész")
    return tally


def decided_share(tally):
    """A 'decided' (pártot választó) körön belüli arányok — a ground truth ezzel mérhető."""
    decided = {k: v for k, v in tally.items() if k in PARTIES or k == "jina"}
    total = sum(decided.values()) or 1
    return {k: 100 * v / total for k, v in decided.items()}


def aggregate_shares(seed_results):
    """Több seed decided-arányainak átlaga pártonként."""
    agg = {}
    for p in PARTIES + ["jina"]:
        vals = [r["decided_shares"].get(p, 0.0) for r in seed_results]
        agg[p] = statistics.mean(vals) if vals else 0.0
    return agg


def evaluate(agg):
    """Ordinális + magnitúdó kiértékelés a ground truth ellen."""
    ranked = sorted([(p, agg.get(p, 0.0)) for p in PARTIES], key=lambda x: -x[1])
    panel_rank = [p for p, _ in ranked]
    ano = agg.get("ANO", 0.0)
    spolu = agg.get("SPOLU", 0.0)
    gap = ano - spolu
    stacilo = agg.get("Stacilo", 0.0)
    # küszöb: a Stačilo a belépők (ANO/SPOLU/STAN/Piráti/SPD/Motoristé) alatt van-e?
    entering = [p for p in PARTIES if p != "Stacilo"]
    min_entering = min((agg.get(p, 0.0) for p in entering), default=0.0)
    stacilo_out = stacilo < min_entering
    return {
        "panel_rank": panel_rank,
        "ranked_shares": ranked,
        "ano_spolu_gap": gap,
        "ano_first": panel_rank[0] == "ANO",
        "spolu_second": len(panel_rank) > 1 and panel_rank[1] == "SPOLU",
        "stacilo_out": stacilo_out,
    }


def main():
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "corpus.jsonl"), encoding="utf-8") as f:
        corpus = [json.loads(line) for line in f if line.strip()]
    print(f"Korpusz betöltve: {len(corpus)} cikk")
    print(f"Panel: N={PANEL_N}, seeds={PANEL_SEEDS}, provider={PANEL_PROVIDER}, modell={PANEL_MODEL}\n")

    seed_results = []
    for seed in range(1, PANEL_SEEDS + 1):
        print(f"  Seed {seed} futtatása...")
        tally = run_seed(corpus, seed)
        shares = decided_share(tally)
        seed_results.append({"seed": seed, "tally": tally, "decided_shares": shares})
        top = sorted(shares.items(), key=lambda x: -x[1])[:4]
        top_str = " | ".join(f"{p} {v:.0f}%" for p, v in top)
        print(f"    -> {top_str}\n")

    # ── VERDIKT ──
    agg = aggregate_shares(seed_results)
    ev = evaluate(agg)

    print("=" * 64)
    print("VERDIKT — CSEH BACKTESZT a valódi választás ELŐTTI hírképből")
    print("=" * 64)
    print("  PANEL rangsor (decided, átlag):")
    for p, v in ev["ranked_shares"]:
        gt = GROUND_TRUTH.get(p, {}).get("pct", 0.0)
        print(f"    {p:10s}: panel {v:5.1f}%   | hivatalos {gt:5.2f}%")
    print()
    print("  ── ELSŐDLEGES (ordinális) ──")
    print(f"    Panel rangsor : {' > '.join(ev['panel_rank'])}")
    print(f"    Hivatalos     : {' > '.join(GROUND_TRUTH_RANK)}")
    print(f"    ANO 1.?       : {'✓' if ev['ano_first'] else '✗'}")
    print(f"    SPOLU 2.?     : {'✓' if ev['spolu_second'] else '✗'}")
    print(f"    Stačilo kiesik?: {'✓' if ev['stacilo_out'] else '✗'}")
    print()
    print("  ── MÁSODLAGOS (magnitúdó) ──")
    print(f"    Panel ANO–SPOLU rés : {ev['ano_spolu_gap']:+.1f}")
    print(f"    Hivatalos rés       : {GROUND_TRUTH_ANO_SPOLU_GAP:+.2f}")
    print(f"    Eltérés             : {abs(ev['ano_spolu_gap'] - GROUND_TRUTH_ANO_SPOLU_GAP):.1f} pont")
    print()
    # ── összegző verdikt ──
    if ev["ano_first"] and ev["spolu_second"] and ev["stacilo_out"]:
        print("  ✓✓✓ ORDINÁLIS TALÁLAT — ANO 1., SPOLU 2., Stačilo kiesik. A doktrína általánosít.")
    elif ev["ano_first"]:
        print("  ✓ RÉSZLEGES — ANO 1. helyes (a fő jel), de a rangsor/küszöb nem teljes.")
    elif ev["ano_spolu_gap"] > 0:
        print("  ~ IRÁNY JÓ — ANO > SPOLU, de nem első; a digitális skew komprimálta (lásd caveat).")
    else:
        print("  ✗ PRIOR-FAL / SKEW — a panel nem hozta ki az ANO-fölényt.")
    print("=" * 64)

    safe_model = re.sub(r"[^a-zA-Z0-9._-]", "_", PANEL_MODEL)
    suffix = os.environ.get("ORAKEL_OUT_SUFFIX", "")
    out_path = os.path.join(here, f"panel_results_{safe_model}{suffix}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "provider": PANEL_PROVIDER,
            "model": PANEL_MODEL,
            "ground_truth": GROUND_TRUTH,
            "ground_truth_ano_spolu_gap": GROUND_TRUTH_ANO_SPOLU_GAP,
            "aggregate_shares": agg,
            "evaluation": ev,
            "seeds": seed_results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nRészletes kimenet: {out_path}")


if __name__ == "__main__":
    main()
