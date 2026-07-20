#!/usr/bin/env python3
"""
CSEH CCI — fogyasztói bizalom a cseh kampány-korpuszból, a nyerő SSR-recepttel.

A délelőtti cseh korpuszt (169 cikk, júl-szept 2025) ÚJRAHASZNÁLJUK — MÁS kérdéssel:
a cseh persona a háztartása anyagi kilátását értékeli (szabad mondat + kategória),
amit SSR-rel pontozunk (linear módszer + OpenAI-small embedding — a magyar demón
bizonyított nyerő recept).

GROUND TRUTH (Eurostat ei_bsco_m, geo=CZ, SA, a futás ELŐTT zárolva):
  2025-07 -6,0 | 2025-08 -9,3 | 2025-09 -7,3  →  ablak-átlag ≈ -7,5
DISZKRIMINÁCIÓS TESZT: a csehek KEVÉSBÉ borúlátók (-7,5), mint a magyarok (-18,4) voltak.
  A panel akkor jó, ha ezt a KÜLÖNBSÉGET visszaadja (cseh szaldó < magyar szaldó abszolútban).

HASZNÁLAT: python3 run_cci_ssr.py   (SILICONFLOW: Flash; OPENAI: embedding)
"""
import os
import re
import sys
import json
import random
import statistics
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import run_panel as cz          # cseh demográfia, weighted_pick, call_model, build_media_context
from plugins import ssr
from plugins.llm_cache import LLMCache, cache_key
_CACHE = LLMCache() if os.environ.get("ORAKEL_CACHE") == "1" else None

N = int(os.environ.get("SSR_DEMO_N", "40"))
EMB_MODEL = os.environ.get("SSR_EMB_MODEL", "text-embedding-3-small")
OAI_KEY = os.environ.get("OPENAI_API_KEY", "")
PANEL_MODEL = os.environ.get("ORAKEL_PANEL_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
CONC = int(os.environ.get("SSR_CONC", "4"))
SF_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
import time


def call(system, user, retries=5, iteration=0):
    """Helyi SiliconFlow-hívás MEGFELELŐ max_tokens-szel (160) — a cz.call_model 30-as
    cap-je csonkolta a mondatokat. Modell az ORAKEL_PANEL_MODEL-ből (pl. Nex-N2-Pro)."""
    key = None
    if _CACHE is not None:
        key = cache_key(PANEL_MODEL, {"t": 0.8, "mt": 160}, system, user, iteration)
        hit = _CACHE.get(key)
        if hit is not None:
            return hit
    body = {"model": PANEL_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": 160, "temperature": 0.8, "thinking": {"type": "disabled"}}
    req = urllib.request.Request("https://api.siliconflow.com/v1/chat/completions",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Authorization": f"Bearer {SF_KEY}", "Content-Type": "application/json"})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.loads(r.read().decode("utf-8"))
            txt = (d["choices"][0]["message"].get("content") or "").strip()
            if txt:
                if _CACHE is not None and key is not None:
                    _CACHE.set(key, txt, model=PANEL_MODEL)
                return txt
        except Exception as e:  # noqa: BLE001
            last = e
        if attempt < retries - 1:
            time.sleep(2 ** attempt + random.random())
    raise RuntimeError(f"call failed: {last}")

CZ_CCI_WINDOW = {"2025-07": -6.0, "2025-08": -9.3, "2025-09": -7.3}
CZ_CCI_MEAN = statistics.mean(CZ_CCI_WINDOW.values())   # ≈ -7.53
HU_CCI_MEAN = -18.4   # a magyar összevetéshez


def oai_embed(texts):
    payload = {"model": EMB_MODEL, "input": list(texts)}
    req = urllib.request.Request("https://api.openai.com/v1/embeddings",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": f"Bearer {OAI_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode("utf-8"))
    items = sorted(data["data"], key=lambda d: d.get("index", 0))
    return np.asarray([it["embedding"] for it in items], dtype=np.float64)


def cci_prompt(rng, media_env):
    age = cz.weighted_pick(rng, cz.AGE_BANDS)
    edu = cz.weighted_pick(rng, cz.EDU)
    settlement = cz.weighted_pick(rng, cz.SETTLEMENT)
    system = (
        "Simuluješ českého spotřebitele (osobu rozhodující o financích domácnosti) na konci "
        "září 2025. Reaguj výhradně podle svého demografického profilu a níže uvedeného "
        "zpravodajského prostředí, upřímně, ze své životní situace. NEPOUŽÍVEJ vnější znalosti "
        "o konkrétních ekonomických datech ani indexech."
    )
    user = f"""TVŮJ PROFIL:
- Věk: {age}
- Vzdělání: {edu}
- Bydliště: {settlement}

{media_env}

Jak očekáváš, že se změní finanční situace tvé domácnosti v příštích 12 měsících?
Odpověz PŘESNĚ v tomto dvouřádkovém formátu:
VETA: <1-2 věty vlastními slovy, upřímně>
KATEGORIE: <lepší|stejná|horší>
"""
    return system, user


_CAT = {"lepší": 1, "lepsi": 1, "stejná": 0, "stejna": 0, "horší": -1, "horsi": -1}

def parse(text):
    ms = re.search(r"VETA:\s*(.+?)(?:\nKATEGORIE:|$)", text, re.S | re.I)
    mc = re.search(r"KATEGORIE:\s*(lepší|lepsi|stejná|stejna|horší|horsi)", text, re.I)
    sent = (ms.group(1).strip() if ms else "").replace("\n", " ")
    cat = _CAT.get(mc.group(1).lower()) if mc else None
    return sent, cat


def main():
    if not OAI_KEY:
        print("HIBA: OPENAI_API_KEY kell az embeddinghez."); return
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "corpus.jsonl"), encoding="utf-8") as f:
        corpus = [json.loads(l) for l in f if l.strip()]
    media_env = cz.build_media_context(corpus)
    rng = random.Random(1)
    prompts = [cci_prompt(rng, media_env) for _ in range(N)]
    print(f"Cseh CCI — modell={PANEL_MODEL} (vak) {N} perszóna, embedding={EMB_MODEL}, módszer=linear\n")

    def ask(i_su):
        i, (system, user) = i_su
        try:
            return parse(call(system, user, iteration=i))
        except Exception:
            return ("", None)

    with ThreadPoolExecutor(max_workers=CONC) as ex:
        rows = list(ex.map(ask, enumerate(prompts)))
    sents = [s for s, c in rows if s]
    cats = [c for s, c in rows if c is not None]
    cat_balance = 100 * statistics.mean(cats) if cats else 0.0
    cat_dist = {"lepší": cats.count(1), "stejná": cats.count(0), "horší": cats.count(-1)}

    anchors = ssr.REFERENCE_SETS_CZ["financni_vyhled"]
    res = ssr.rate(sents, anchors, method="linear", embed_fn=oai_embed)
    ssr_balance = (res["survey_score"] - 3) / 2 * 100

    print("=" * 64)
    print("CSEH CCI — fogyasztói bizalom a kampány-korpuszból (vak)")
    print("=" * 64)
    print(f"  Érvényes mondat: {len(sents)} | kategória: {len(cats)}")
    print(f"  RÉGI kategorikus: lepší={cat_dist['lepší']} stejná={cat_dist['stejná']} horší={cat_dist['horší']} → szaldó {cat_balance:+.1f}")
    pmf = " ".join(f"{p*100:4.0f}%" for p in res["survey_pmf"])
    print(f"  SSR (linear) 5-fokú PMF (hodně_zhorší→hodně_zlepší): {pmf}")
    print(f"  SSR átlagpont (1-5): {res['survey_score']:.2f}  →  szaldó: {ssr_balance:+.1f}")
    print()
    print(f"  VALÓS cseh CCI (júl-szept 2025 átlag): {CZ_CCI_MEAN:+.1f}")
    print(f"  (összevetés — magyar CCI ugyanígy:     {HU_CCI_MEAN:+.1f})")
    print()
    print("  ── DISZKRIMINÁCIÓS TESZT ──")
    print(f"    Cseh SSR-szaldó {ssr_balance:+.1f}  vs  magyar -29,9 (a délelőtti demóból)")
    if ssr_balance > -29.9:
        print(f"    ✓ A cseh KEVÉSBÉ negatív, mint a magyar — egyezik a valós {CZ_CCI_MEAN:+.1f} > {HU_CCI_MEAN:+.1f} iránnyal.")
    else:
        print("    ✗ A cseh nem jött ki kevésbé negatívnak — a panel nem diszkriminál.")
    print("=" * 64)
    print("\n  Példa cseh mondatok + SSR pont:")
    for i in range(min(4, len(sents))):
        print(f"    [{res['per_response'][i]['score']:.2f}] {sents[i][:90]}")

    json.dump({"n": len(sents), "cat_balance": cat_balance, "ssr_score": res["survey_score"],
               "ssr_balance": ssr_balance, "survey_pmf": res["survey_pmf"],
               "model": PANEL_MODEL, "cz_cci_truth": CZ_CCI_MEAN, "sentences": sents},
              open(os.path.join(here, f"cci_ssr_{re.sub(r'[^a-zA-Z0-9]', '_', PANEL_MODEL)}_n{len(sents)}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nKimenet: cci_ssr_{re.sub(r'[^a-zA-Z0-9]', '_', PANEL_MODEL)}_n{len(sents)}.json")


if __name__ == "__main__":
    main()
