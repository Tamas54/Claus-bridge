#!/usr/bin/env python3
"""
ORAKEL-II NEM-POLITIKAI VALIDÁCIÓ — FOGYASZTÓI BIZALMI PANEL
=============================================================
A TRÜKK: UGYANAZ a magyar korpusz (245 cikk, jan–márc 2026, poll-szűrt), amit a politikai
teszthez gyűjtöttünk — de MÁS a kérdés. A persona nem pártra szavaz, hanem a saját gazdasági
helyzetét/várakozását értékeli (DG ECFIN CCI 4 komponensét közelítve). Ebből szintetikus
bizalmi szaldó aggregálódik (a valódi CCI-módszertan: pozitív − negatív válaszok különbsége).

GROUND TRUTH (Eurostat ei_bsco_m, SA, szaldó — a futás ELŐTT zárolva):
  Jan 2026 −19,4 | Feb −17,6 | Már −18,2  →  ablak-átlag ≈ −18,4 (közepesen negatív)
  Trajektória: TARTÓS JAVULÁS (2025-08 −27,2 → 2026-05 −2,1). A jan–márc ablak a felívelés közepe.

SIKER-KRITÉRIUM (briefing 5., ELŐRE rögzítve):
  ELSŐDLEGES (irány): a panel előretekintő (várakozás) komponense kevésbé negatív / reményteljesebb-e,
    mint a múltra-tekintő — ez a valós JAVULÓ trajektóriát tükrözné (egy statikus korpuszból ez a
    kinyerhető irány-jel; a valódi havi delta külön, több korpusszal mérhető — lásd thoughts).
  MÁSODLAGOS (szint): a szintetikus szaldó nagyságrendje a valós ~−18 sávban van-e? (A cseh tanulság:
    a szintet mérési váz hajtja → túllövés várható, NEM bukás, ha az irány stimmel.)

HASZNÁLAT:
  ORAKEL_PROVIDER=siliconflow python3 run_panel_cci.py   # Flash, vak (elsődleges)
  ORAKEL_PROVIDER=openai      python3 run_panel_cci.py   # gpt-5.4-mini, kontroll
"""

import os
import re
import sys
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

from config import STRUCTURAL_MEDIA, PANEL_MODEL, PANEL_PROVIDER, PANEL_N, PANEL_SEEDS, PANEL_CONCURRENCY

# ── BEHÚZOTT RÉTEGEK (LOOT #1 SSR + #2 cache) — env-kapcsolóval, additív ──
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root → plugins
from plugins import ssr as _ssr
from plugins.llm_cache import LLMCache, cache_key

_CACHE = LLMCache() if os.environ.get("ORAKEL_CACHE") == "1" else None     # determinista cache
_SSR = os.environ.get("ORAKEL_SSR") == "1"                                 # SSR az anyagi várakozásra
_SSR_EMB = os.environ.get("SSR_EMB_MODEL", "text-embedding-3-small")       # a nyerő recept embeddingje


def _oai_embed(texts):
    payload = {"model": _SSR_EMB, "input": list(texts)}
    req = urllib.request.Request("https://api.openai.com/v1/embeddings", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY','')}", "Content-Type": "application/json"})
    import numpy as _np
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode("utf-8"))
    return _np.asarray([it["embedding"] for it in sorted(data["data"], key=lambda d: d.get("index", 0))], dtype=float)


# ── GROUND TRUTH (a vonalzó — zárolva) ───────────────────────────────────
CCI_WINDOW = {"2026-01": -19.4, "2026-02": -17.6, "2026-03": -18.2}
CCI_WINDOW_MEAN = statistics.mean(CCI_WINDOW.values())   # ≈ -18.4
CCI_TRAJECTORY = "JAVULÓ (2025-08 -27,2 → 2026 jan-márc ~-18 → 2026-05 -2,1)"


# ── PROVIDER-RÉTEG ───────────────────────────────────────────────────────
def _provider_cfg():
    if PANEL_PROVIDER == "openai":
        return ("https://api.openai.com/v1/chat/completions", os.environ.get("OPENAI_API_KEY", ""), "openai")
    if PANEL_PROVIDER == "anthropic":
        return ("https://api.anthropic.com/v1/messages", os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic")
    return ("https://api.siliconflow.com/v1/chat/completions", os.environ.get("SILICONFLOW_API_KEY", ""), "siliconflow")

_ENDPOINT, _API_KEY, _KIND = _provider_cfg()

# ── DEMOGRÁFIA (KSH-arányok — a fogyasztói bizalom ERŐSEN demográfia-függő) ──
AGE_BANDS = [("18-29", 0.16), ("30-39", 0.17), ("40-49", 0.18), ("50-64", 0.27), ("65+", 0.22)]
EDU = [("max 8 általános", 0.22), ("szakmunkás/szakképző", 0.27), ("érettségi", 0.30), ("diploma", 0.21)]
SETTLEMENT = [("Budapest", 0.18), ("megyei jogú város", 0.20), ("egyéb város", 0.32), ("község/falu", 0.30)]


def weighted_pick(rng, items):
    r = rng.random(); acc = 0.0
    for label, w in items:
        acc += w
        if r <= acc:
            return label
    return items[-1][0]


def build_media_context(corpus):
    """A hír-korpusz gazdasági-framing grounding-blokká (a párt-lean kontextusként marad)."""
    headlines = []
    for row in corpus:
        headlines.append(f"- [{row['source']} | {row['lean']}] {row['title']}: {row['lead'][:180]}")
    random.Random(42).shuffle(headlines)
    headlines = headlines[:400]
    corpus_block = "\n".join(headlines)
    media_env = f"""HÍR- ÉS MÉDIAKÖRNYEZET (2026 január–március):
A híreket vegyes irányú lapok adják (kormányközeli és ellenzéki/független egyaránt). A gazdasági
hangulat, drágulás, rezsi, bér, forint, megélhetés témái a közbeszéd részei.

AKTUÁLIS HÍREK (cím + lead, forrás és politikai irány megjelölve):
{corpus_block}
"""
    return media_env


def make_persona_prompt(rng, media_env):
    age = weighted_pick(rng, AGE_BANDS)
    edu = weighted_pick(rng, EDU)
    settlement = weighted_pick(rng, SETTLEMENT)
    system = (
        "Egy magyar fogyasztót (háztartási döntéshozót) szimulálsz 2026 március végén. "
        "Kizárólag a demográfiai jellemződ és az alább látott hír-/médiakörnyezet alapján értékeled "
        "a saját anyagi helyzetedet és gazdasági várakozásaidat. NE használj külső tudást konkrét "
        "indexértékekről vagy a jövő gazdasági adatairól — a saját élethelyzetedből és a hírekből reagálj, őszintén."
    )
    user = f"""A TE PROFILOD:
- Életkor: {age}
- Iskolai végzettség: {edu}
- Lakóhely: {settlement}

{media_env}

KÉRDÉSEK a háztartásod gazdasági helyzetéről. Válaszolj PONTOSAN ebben az 5-soros formában, más szöveg nélkül:
ANYAGI_MULT: <jobb|ugyanaz|rosszabb>     (a háztartásod anyagi helyzete az elmúlt 12 hónapban)
ANYAGI_VAR: <jobb|ugyanaz|rosszabb>      (mire számítasz a háztartásod helyzetében a következő 12 hónapban)
ORSZAG_VAR: <jobb|ugyanaz|rosszabb>      (mire számítasz az ország gazdaságában a következő 12 hónapban)
VASARLAS: <igen|talan|nem>               (tervezel-e nagyobb vásárlást a közeljövőben)
FELRETESZ: <igen|talan|nem>              (tudsz-e mostanában félretenni/megtakarítani)
"""
    if _SSR:
        user += ("ANYAGI_VAR_MONDAT: <1-2 mondat a SAJÁT szavaiddal, őszintén, "
                 "hogyan látod a háztartásod anyagi kilátásait a következő 12 hónapra>\n")
    return system, user


def _build_request(system, user):
    if not _API_KEY:
        raise RuntimeError(f"Nincs API kulcs a(z) '{_KIND}' providerhez (.env).")
    if _KIND == "anthropic":
        body = {"model": PANEL_MODEL, "max_tokens": 120, "system": system,
                "messages": [{"role": "user", "content": user}]}
        headers = {"content-type": "application/json", "x-api-key": _API_KEY, "anthropic-version": "2023-06-01"}
    else:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        body = {"model": PANEL_MODEL, "messages": msgs}
        if _KIND == "openai" and PANEL_MODEL.startswith("gpt-5") and "chat" not in PANEL_MODEL:
            body["max_completion_tokens"] = 2000
            body["reasoning_effort"] = "low"
        else:
            body["max_tokens"] = 220 if _SSR else 120   # SSR-módban a szabad mondat hosszabb
            body["temperature"] = 0.8
            if _KIND == "siliconflow":
                body["thinking"] = {"type": "disabled"}
        headers = {"content-type": "application/json", "Authorization": f"Bearer {_API_KEY}"}
    return urllib.request.Request(_ENDPOINT, data=json.dumps(body).encode("utf-8"), headers=headers)


def _extract_text(data):
    if _KIND == "anthropic":
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return (data["choices"][0]["message"].get("content") or "").strip()


def call_model(system, user, retries=5, iteration=0):
    # LOOT #2 cache: a persona-index az iteration → azonos demográfiájú perszónák is külön
    # cache-bejegyzést kapnak (független mintavétel megőrizve), de újrafuttatáskor hit.
    key = None
    if _CACHE is not None:
        key = cache_key(PANEL_MODEL, {"t": 0.8, "mt": 120}, system, user, iteration)
        hit = _CACHE.get(key)
        if hit is not None:
            return hit
    last = None
    for attempt in range(retries):
        try:
            req = _build_request(system, user)
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            txt = _extract_text(data)
            if txt:
                if _CACHE is not None and key is not None:
                    _CACHE.set(key, txt, model=PANEL_MODEL)
                return txt
        except Exception as e:  # noqa: BLE001
            last = e
        if attempt < retries - 1:
            time.sleep(2 ** attempt + random.random())
    raise RuntimeError(f"{_KIND} hívás sikertelen {retries} próba után: {last}")


_POS3 = {"jobb": 1, "ugyanaz": 0, "rosszabb": -1}
_POS2 = {"igen": 1, "talan": 0, "talán": 0, "nem": -1}

def parse_components(text):
    """5 komponens kinyerése → {kulcs: +1/0/-1}. Hiányzó → None (kihagyjuk az aggregálásból)."""
    out = {}
    for key, mapping in [("ANYAGI_MULT", _POS3), ("ANYAGI_VAR", _POS3), ("ORSZAG_VAR", _POS3),
                         ("VASARLAS", _POS2), ("FELRETESZ", _POS2)]:
        m = re.search(rf"{key}\s*:\s*([a-záéíóöőúüű]+)", text, re.IGNORECASE)
        if m:
            val = m.group(1).lower()
            out[key] = mapping.get(val)
        else:
            out[key] = None
    if _SSR:   # a szabad mondat az SSR-pontozáshoz (a tally-t nem érinti)
        ms = re.search(r"ANYAGI_VAR_MONDAT:\s*(.+)", text, re.IGNORECASE)
        out["_sentence"] = ms.group(1).strip().replace("\n", " ") if ms else ""
    return out


def run_seed(corpus, seed):
    rng = random.Random(seed)
    media_env = build_media_context(corpus)
    prompts = [make_persona_prompt(rng, media_env) for _ in range(PANEL_N)]

    def _ask(idx_sysuser):
        i, (system, user) = idx_sysuser
        try:
            return parse_components(call_model(system, user, iteration=i))
        except Exception as e:  # noqa: BLE001
            print(f"    [!] persona {i}: {e}")
            return {k: None for k in ("ANYAGI_MULT", "ANYAGI_VAR", "ORSZAG_VAR", "VASARLAS", "FELRETESZ")}

    rows = []
    with ThreadPoolExecutor(max_workers=PANEL_CONCURRENCY) as ex:
        for n, comp in enumerate(ex.map(_ask, enumerate(prompts)), 1):
            rows.append(comp)
            if n % 20 == 0:
                print(f"    seed {seed}: {n}/{PANEL_N} persona kész")
    return rows


def balance(rows, key):
    """DG ECFIN-szerű szaldó egy komponensre: (poz% − neg%), a semleges 0. Tartomány -100..+100."""
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return 0.0, 0
    return 100 * sum(vals) / len(vals), len(vals)


def main():
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "corpus.jsonl"), encoding="utf-8") as f:
        corpus = [json.loads(line) for line in f if line.strip()]
    print(f"Korpusz betöltve: {len(corpus)} cikk")
    print(f"Panel: N={PANEL_N}, seeds={PANEL_SEEDS}, provider={PANEL_PROVIDER}, modell={PANEL_MODEL}\n")

    all_rows = []
    seed_summaries = []
    for seed in range(1, PANEL_SEEDS + 1):
        print(f"  Seed {seed} futtatása...")
        rows = run_seed(corpus, seed)
        all_rows.extend(rows)
        comps = {k: balance(rows, k)[0] for k in ("ANYAGI_MULT", "ANYAGI_VAR", "ORSZAG_VAR", "VASARLAS", "FELRETESZ")}
        cci = statistics.mean([comps["ANYAGI_VAR"], comps["ORSZAG_VAR"], comps["FELRETESZ"]])
        seed_summaries.append({"seed": seed, "components": comps, "cci_synth": cci})
        print(f"    -> szintetikus CCI {cci:+.1f} | múlt {comps['ANYAGI_MULT']:+.0f} várakozás(anyagi) {comps['ANYAGI_VAR']:+.0f}\n")

    # ── aggregált (összes persona) ──
    comps = {k: balance(all_rows, k) for k in ("ANYAGI_MULT", "ANYAGI_VAR", "ORSZAG_VAR", "VASARLAS", "FELRETESZ")}
    cci_synth = statistics.mean([comps["ANYAGI_VAR"][0], comps["ORSZAG_VAR"][0], comps["FELRETESZ"][0]])
    # belső irány-jel: előretekintő (anyagi várakozás) vs múltra-tekintő
    direction_delta = comps["ANYAGI_VAR"][0] - comps["ANYAGI_MULT"][0]

    # ── LOOT #1: SSR az anyagi várakozásra (szabad mondat → PMF → várható érték) ──
    ssr_line = None
    if _SSR:
        sents = [r.get("_sentence", "") for r in all_rows if r.get("_sentence")]
        if sents:
            try:
                res = _ssr.rate(sents, _ssr.REFERENCE_SETS_HU["anyagi_varakozas"],
                                method="linear", embed_fn=_oai_embed)
                ssr_bal = (res["survey_score"] - 3) / 2 * 100
                ssr_line = (f"  [SSR] anyagi várakozás (szabad mondat→PMF, linear+{_SSR_EMB}): "
                            f"{ssr_bal:+.1f}   (kemény kategorikus: {comps['ANYAGI_VAR'][0]:+.1f}, valós CCI {CCI_WINDOW_MEAN:+.1f})")
            except Exception as e:  # noqa: BLE001
                ssr_line = f"  [SSR] hiba: {e}"

    print("=" * 64)
    print("VERDIKT — FOGYASZTÓI BIZALOM a politikai korpuszból (vak)")
    print("=" * 64)
    print("  Komponens-szaldók (poz% − neg%, n=válaszadók):")
    label = {"ANYAGI_MULT": "anyagi helyzet (múlt 12h)", "ANYAGI_VAR": "anyagi várakozás (köv 12h)",
             "ORSZAG_VAR": "ország gazd. várakozás", "VASARLAS": "nagyobb vásárlás szándék",
             "FELRETESZ": "tud-e félretenni"}
    for k in ("ANYAGI_MULT", "ANYAGI_VAR", "ORSZAG_VAR", "VASARLAS", "FELRETESZ"):
        b, n = comps[k]
        print(f"    {label[k]:28s}: {b:+6.1f}  (n={n})")
    print()
    print("  ── MÁSODLAGOS (szint) ──")
    print(f"    Szintetikus CCI (3 előretekintő átlaga): {cci_synth:+.1f}")
    print(f"    Valós CCI (jan–márc 2026 átlag)        : {CCI_WINDOW_MEAN:+.1f}")
    print(f"    Valós trajektória                      : {CCI_TRAJECTORY}")
    print()
    print("  ── ELSŐDLEGES (belső irány-jel) ──")
    print(f"    Anyagi VÁRAKOZÁS − anyagi MÚLT = {direction_delta:+.1f}")
    if direction_delta > 3:
        print("    → A várakozás kevésbé negatív a múltnál → JAVULÓ kilátás. Egyezik a valós felívelő CCI-vel. ✓")
    elif direction_delta < -3:
        print("    → A várakozás rosszabb a múltnál → ROMLÓ kilátás. ELLENTÉTES a valós javulással. ✗")
    else:
        print("    → Lapos kilátás (a múlt ~ a várakozás). A valós enyhe javulással gyengén konzisztens. ~")
    if ssr_line:
        print()
        print("  ── SSR-RÉTEG (LOOT #1) ──")
        print(ssr_line)
    print("=" * 64)

    safe_model = re.sub(r"[^a-zA-Z0-9._-]", "_", PANEL_MODEL)
    suffix = os.environ.get("ORAKEL_OUT_SUFFIX", "")
    out_path = os.path.join(here, f"cci_results_{safe_model}{suffix}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "provider": PANEL_PROVIDER, "model": PANEL_MODEL,
            "cci_window": CCI_WINDOW, "cci_window_mean": CCI_WINDOW_MEAN, "cci_trajectory": CCI_TRAJECTORY,
            "components": {k: {"balance": comps[k][0], "n": comps[k][1]} for k in comps},
            "cci_synth": cci_synth, "direction_delta": direction_delta,
            "seeds": seed_summaries,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nRészletes kimenet: {out_path}")


if __name__ == "__main__":
    main()
