#!/usr/bin/env python3
"""
ORAKEL-II BACKTESZT — 2. FOGASKERÉK: PANEL-MOTOR
=================================================
A szintetikus választói panel. Bemenet:
  - corpus.jsonl                     (a választás ELŐTTI hír-korpusz, poll-mentes)
  - STRUCTURAL_MEDIA (config)        (közmédia=gov áprilisig + Tisza social-fölény)
  - demográfia                       (KSH-arányok: kor/iskola/településtípus)

A panel NEM lát pártszámot. A médiakörnyezetből + a demográfiából
emergens párt-preferenciát ad. A kérdés: vajon a választás ELŐTTI hírképből
kijön-e a tényleges Tisza +14,6 (53,18% vs 38,61%)?

HASZNÁLAT:
  export ANTHROPIC_API_KEY="..."
  python3 run_panel.py
  -> kimenet: panel_results.json  +  konzol-összefoglaló a Tisza-Fidesz résről
"""

import os
import re
import json
import time
import random
import statistics
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# A Bridge .env-jét töltjük (egy szinttel feljebb) — SILICONFLOW/OPENAI/ANTHROPIC kulcsok.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass

from config import (
    STRUCTURAL_MEDIA, PANEL_MODEL, PANEL_PROVIDER, PANEL_N, PANEL_SEEDS, PANEL_CONCURRENCY,
)


# ── PROVIDER-RÉTEG (siliconflow=Flash vak | openai=gpt-5.4-mini | anthropic) ──
# A pollster.py bevált mintája: SiliconFlow Non-Think, gpt-5 reasoning-kezelés.
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

# ── DEMOGRÁFIA (KSH-arányok, választókorú népesség) ──────────────────────
# A #3 receptje: kor=mun0005, iskola=mun0006 (korrigált), településtípus=2022 cenzus.
# Cellák: a panel personáit ezekből az arányokból mintázzuk.
AGE_BANDS = [
    ("18-29", 0.16),
    ("30-39", 0.17),
    ("40-49", 0.18),
    ("50-64", 0.27),
    ("65+",   0.22),
]
EDU = [
    ("max 8 általános",      0.22),
    ("szakmunkás/szakképző", 0.27),
    ("érettségi",            0.30),
    ("diploma",              0.21),
]
SETTLEMENT = [
    ("Budapest",        0.18),
    ("megyei jogú város",0.20),
    ("egyéb város",     0.32),
    ("község/falu",     0.30),
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
        # forrás + lean címkével, hogy a panel lássa, KI mondja
        headlines.append(f"- [{row['source']} | {row['lean']}] {row['title']}: {row['lead'][:180]}")
    # ne fújjuk túl a kontextust: max ~400 headline
    random.Random(42).shuffle(headlines)
    headlines = headlines[:400]
    corpus_block = "\n".join(headlines)

    media_env = f"""MÉDIAKÖRNYEZET (2026 január–március, a választás ELŐTT):

STRUKTURÁLIS:
- Közmédia (M1, Kossuth Rádió, hírado.hu): a kormányoldal (Fidesz) felé elfogult, nagy elérésű. reach≈{sm['kozmedia_reach']}.
- Közösségi média ({sm['social_dominance']['platform']}): {sm['social_dominance']['note']}
- Online sajtó: vegyes; kormányközeli (Origo, Magyar Nemzet, Index, Mandiner) és ellenzéki/független (Telex, 444, HVG, 24.hu, Népszava) lapok egyaránt.

AKTUÁLIS HÍREK (cím + lead, forrás és politikai irány megjelölve):
{corpus_block}
"""
    return media_env


def make_persona_prompt(rng, media_env):
    age = weighted_pick(rng, AGE_BANDS)
    edu = weighted_pick(rng, EDU)
    settlement = weighted_pick(rng, SETTLEMENT)

    system = (
        "Egy magyar választópolgárt szimulálsz a 2026-os országgyűlési választás "
        "ELŐTTI hetekben (2026 március vége). Te magad döntöd el, kire szavaznál, "
        "kizárólag a megadott demográfiai jellemződ és az alább látott médiakörnyezet "
        "alapján. NE használj semmilyen külső tudást a választás kimeneteléről — "
        "te a kampány közepén élsz, a jövőt nem ismered. Őszintén, a karaktered "
        "élethelyzetéből reagálj a hírekre."
    )
    user = f"""A TE PROFILOD:
- Életkor: {age}
- Iskolai végzettség: {edu}
- Lakóhely: {settlement}

{media_env}

KÉRDÉS: Ha most vasárnap lenne az országgyűlési választás, melyik pártra szavaznál?
Válaszj PONTOSAN egy sorban, ebben a formában (más szöveg nélkül):
SZAVAZAT: <Fidesz-KDNP | Tisza | Mi Hazánk | DK | egyéb | bizonytalan | nem szavazna>
"""
    return system, user


def _build_request(system, user):
    """Provider-specifikus HTTP request összeállítás (urllib.Request)."""
    if not _API_KEY:
        raise RuntimeError(f"Nincs API kulcs a(z) '{_KIND}' providerhez (.env).")
    if _KIND == "anthropic":
        body = {"model": PANEL_MODEL, "max_tokens": 30, "system": system,
                "messages": [{"role": "user", "content": user}]}
        headers = {"content-type": "application/json", "x-api-key": _API_KEY,
                   "anthropic-version": "2023-06-01"}
    else:
        # OpenAI-kompatibilis (SiliconFlow + OpenAI): system+user üzenet
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        body = {"model": PANEL_MODEL, "messages": msgs}
        if _KIND == "openai" and PANEL_MODEL.startswith("gpt-5") and "chat" not in PANEL_MODEL:
            # gpt-5 reasoning: max_completion_tokens + headroom, low effort (rövid válasz)
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


def call_model(system, user, retries=4):
    """Egy panel-hívás a kiválasztott provideren, exponenciális backoff-fal."""
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
    m = re.search(r"SZAVAZAT:\s*(.+)", text, re.IGNORECASE)
    raw = (m.group(1) if m else text).strip().lower()
    if "fidesz" in raw: return "Fidesz-KDNP"
    if "tisza" in raw: return "Tisza"
    if "hazánk" in raw or "hazank" in raw: return "Mi Hazánk"
    if raw.startswith("dk") or "demokratikus" in raw: return "DK"
    if "bizonytalan" in raw: return "bizonytalan"
    if "nem szav" in raw: return "nem szavazna"
    return "egyéb"


def run_seed(corpus, seed):
    rng = random.Random(seed)
    media_env = build_media_context(corpus)
    # A personákat előre legeneráljuk (seed-determinisztikus), majd konkurensen kérdezzük.
    prompts = [make_persona_prompt(rng, media_env) for _ in range(PANEL_N)]

    def _ask(idx_sysuser):
        i, (system, user) = idx_sysuser
        try:
            return parse_vote(call_model(system, user))
        except Exception as e:  # noqa: BLE001
            print(f"    [!] persona {i}: {e}")
            return "bizonytalan"

    tally = {}
    with ThreadPoolExecutor(max_workers=PANEL_CONCURRENCY) as ex:
        for n, vote in enumerate(ex.map(_ask, enumerate(prompts)), 1):
            tally[vote] = tally.get(vote, 0) + 1
            if n % 20 == 0:
                print(f"    seed {seed}: {n}/{PANEL_N} persona kész")
    return tally


def decided_share(tally):
    """A 'decided' (pártot választó) körön belüli arányok — a ground truth ezzel mérhető."""
    decided = {k: v for k, v in tally.items()
               if k in ("Fidesz-KDNP", "Tisza", "Mi Hazánk", "DK", "egyéb")}
    total = sum(decided.values()) or 1
    return {k: 100 * v / total for k, v in decided.items()}


def main():
    with open("corpus.jsonl", encoding="utf-8") as f:
        corpus = [json.loads(line) for line in f if line.strip()]
    print(f"Korpusz betöltve: {len(corpus)} cikk")
    print(f"Panel: N={PANEL_N}, seeds={PANEL_SEEDS}, provider={PANEL_PROVIDER}, modell={PANEL_MODEL}\n")

    all_results = []
    tisza_fidesz_gaps = []
    for seed in range(1, PANEL_SEEDS + 1):
        print(f"  Seed {seed} futtatása...")
        tally = run_seed(corpus, seed)
        shares = decided_share(tally)
        tisza = shares.get("Tisza", 0)
        fidesz = shares.get("Fidesz-KDNP", 0)
        gap = tisza - fidesz
        tisza_fidesz_gaps.append(gap)
        all_results.append({"seed": seed, "tally": tally, "decided_shares": shares,
                            "tisza_fidesz_gap": gap})
        print(f"    -> Tisza {tisza:.1f}% | Fidesz {fidesz:.1f}% | RÉS: {gap:+.1f}\n")

    # ── VERDIKT ──
    mean_gap = statistics.mean(tisza_fidesz_gaps)
    stdev = statistics.pstdev(tisza_fidesz_gaps) if len(tisza_fidesz_gaps) > 1 else 0.0
    GROUND_TRUTH_GAP = 14.57   # Tisza 53,18 - Fidesz 38,61 (NVI, listás, hivatalos)

    print("=" * 60)
    print("VERDIKT — BACKTESZT a valódi választás ELŐTTI hírképből")
    print("=" * 60)
    print(f"  Panel Tisza-Fidesz rés (átlag): {mean_gap:+.1f}  (szórás {stdev:.1f})")
    print(f"  VALÓDI eredmény (NVI):          {GROUND_TRUTH_GAP:+.1f}")
    print(f"  Eltérés:                        {abs(mean_gap - GROUND_TRUTH_GAP):.1f} pont")
    print()
    if abs(mean_gap - GROUND_TRUTH_GAP) <= 6:
        print("  ✓✓✓ TALÁLAT — a panel a tényleges eredmény ±6 pontos sávjában.")
        print("      A médiakörnyezetből KIOLVASHATÓ volt az eredmény. Doktrína igazolva.")
    elif abs(mean_gap - GROUND_TRUTH_GAP) <= 12:
        print("  ✓ KÖZELÍT — helyes irány, a sávon kívül de a közelben. Finomítható.")
    elif mean_gap > 0:
        print("  ~ IRÁNY JÓ, MAGNITÚDÓ NEM — Tisza-előnyt ad, de nem a valódit.")
    else:
        print("  ✗ PRIOR-FAL — a panel nem fordult át; a média-grounding nem fogott.")
    print("=" * 60)

    safe_model = re.sub(r"[^a-zA-Z0-9._-]", "_", PANEL_MODEL)
    out_path = f"panel_results_{safe_model}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "provider": PANEL_PROVIDER,
            "model": PANEL_MODEL,
            "ground_truth_gap": GROUND_TRUTH_GAP,
            "mean_gap": mean_gap,
            "stdev": stdev,
            "seeds": all_results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nRészletes kimenet: {out_path}")


if __name__ == "__main__":
    main()
