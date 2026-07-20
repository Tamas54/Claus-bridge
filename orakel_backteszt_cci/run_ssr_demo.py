#!/usr/bin/env python3
"""
SSR-DEMÓ — a kemény kategorikus vs. az SSR (szabad szöveg → PMF) ÉLES összevetése.

Ugyanazon a panelen, ugyanazokon a perszónákon: minden perszóna ad
  (a) egy szabad MONDATOT az anyagi várakozásáról (köv. 12 hónap), és
  (b) a régi 3-opciós KATEGÓRIÁT (jobb/ugyanaz/rosszabb).
Az (a)-t SSR-rel pontozzuk (REFERENCE_SETS_HU['anyagi_varakozas'], mindkét módszer),
a (b)-t a régi módon szaldóként. Megmutatja, hogy az SSR megtartja a SZÓRÁST/árnyalatot,
míg a kategorikus 3 kemény rekeszre omlik.

Ground truth: a valós CCI ablak-átlag ≈ -18,4 (ez maga is egy -100..+100 szaldó).

HASZNÁLAT: python3 run_ssr_demo.py      (Flash, vak; SILICONFLOW_API_KEY kell az embeddinghez is)
"""
import os
import re
import sys
import json
import statistics
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_panel_cci as base          # korpusz, demográfia, média-kontextus, call_model újrahasznosítva
from plugins import ssr

N = int(os.environ.get("SSR_DEMO_N", "40"))
SEED = 1
EMB_MODEL = os.environ.get("SSR_EMB_MODEL", "Qwen/Qwen3-Embedding-0.6B")
SF_BASE = "https://api.siliconflow.com/v1"
SF_KEY = os.environ.get("SILICONFLOW_API_KEY", "")


# ── embed_fn: SiliconFlow /embeddings (bge-m3), szinkron, batch ──────────
def sf_embed(texts):
    payload = {"model": EMB_MODEL, "input": list(texts)}
    req = urllib.request.Request(
        f"{SF_BASE}/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {SF_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode("utf-8"))
    items = sorted(data["data"], key=lambda d: d.get("index", 0))
    import numpy as np
    return np.asarray([it["embedding"] for it in items], dtype=np.float64)


def demo_prompt(rng, media_env):
    age = base.weighted_pick(rng, base.AGE_BANDS)
    edu = base.weighted_pick(rng, base.EDU)
    settlement = base.weighted_pick(rng, base.SETTLEMENT)
    system = (
        "Egy magyar fogyasztót szimulálsz 2026 március végén. Kizárólag a demográfiai "
        "jellemződ és az alábbi médiakörnyezet alapján reagálj, őszintén, a saját "
        "élethelyzetedből. NE használj külső tudást konkrét gazdasági adatokról."
    )
    user = f"""A TE PROFILOD:
- Életkor: {age}
- Iskolai végzettség: {edu}
- Lakóhely: {settlement}

{media_env}

Mire számítasz a háztartásod anyagi helyzetében a következő 12 hónapban?
Válaszolj PONTOSAN ebben a 2-soros formában:
MONDAT: <1-2 mondat a saját szavaiddal, őszintén>
KATEGORIA: <jobb|ugyanaz|rosszabb>
"""
    return system, user


_CAT = {"jobb": 1, "ugyanaz": 0, "rosszabb": -1}

def parse(text):
    ms = re.search(r"MONDAT:\s*(.+?)(?:\nKATEGORIA:|$)", text, re.S | re.I)
    mc = re.search(r"KATEGORIA:\s*(jobb|ugyanaz|rosszabb)", text, re.I)
    sent = (ms.group(1).strip() if ms else "").replace("\n", " ")
    cat = _CAT.get(mc.group(1).lower()) if mc else None
    return sent, cat


def main():
    if not SF_KEY:
        print("HIBA: SILICONFLOW_API_KEY kell (a Flash-hez és az embeddinghez is).")
        return
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "corpus.jsonl"), encoding="utf-8") as f:
        corpus = [json.loads(l) for l in f if l.strip()]
    media_env = base.build_media_context(corpus)

    import random
    from concurrent.futures import ThreadPoolExecutor
    rng = random.Random(SEED)
    prompts = [demo_prompt(rng, media_env) for _ in range(N)]
    print(f"SSR-demó: N={N}, modell={base.PANEL_MODEL} (vak), embedding={EMB_MODEL}\n")

    def ask(su):
        try:
            return parse(base.call_model(*su))
        except Exception as e:  # noqa: BLE001
            print(f"  [!] {e}")
            return ("", None)

    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(ask, prompts))

    sents = [s for s, c in rows if s]
    cats = [c for s, c in rows if c is not None]
    print(f"Érvényes mondat: {len(sents)} | érvényes kategória: {len(cats)}\n")

    # ── (b) RÉGI: kategorikus szaldó ──
    cat_balance = 100 * statistics.mean(cats) if cats else 0.0
    cat_dist = {k: cats.count(v) for k, v in _CAT.items()}

    # ── (a) ÚJ: SSR a szabad mondatokon, mindkét módszerrel ──
    anchors = ssr.REFERENCE_SETS_HU["anyagi_varakozas"]
    results = {}
    for method in ("linear", "softmax"):
        res = ssr.rate(sents, anchors, method=method, temperature=1.0, embed_fn=sf_embed)
        # 1..5 skála → -100..+100 szaldó (a CCI-vel összevethető)
        balance = (res["survey_score"] - 3) / 2 * 100
        results[method] = (res, balance)

    print("=" * 64)
    print("SSR-DEMÓ — anyagi várakozás (köv. 12 hónap)")
    print("=" * 64)
    print("  RÉGI (kemény 3-opciós kategorikus):")
    print(f"    eloszlás: jobb={cat_dist['jobb']} · ugyanaz={cat_dist['ugyanaz']} · rosszabb={cat_dist['rosszabb']}")
    print(f"    szaldó: {cat_balance:+.1f}   (csak 3 rekesz — durva, nincs árnyalat)")
    print()
    for method in ("linear", "softmax"):
        res, bal = results[method]
        pmf = res["survey_pmf"]
        bars = " ".join(f"{p*100:4.0f}%" for p in pmf)
        print(f"  ÚJ — SSR ({method}):")
        print(f"    5-fokú PMF (sokkal_rosszabb→sokkal_jobb): {bars}")
        print(f"    átlagpont (1-5): {res['survey_score']:.2f}  →  szaldó: {bal:+.1f}")
        print()
    print(f"  VALÓS CCI (jan–márc átlag, ground truth): {base.CCI_WINDOW_MEAN:+.1f}")
    print("=" * 64)
    print("\n  Példa szabad mondatok + SSR pont (linear):")
    for i in range(min(4, len(sents))):
        sc = results["linear"][0]["per_response"][i]["score"]
        print(f"    [{sc:.2f}] {sents[i][:90]}")

    out = {"n": len(sents), "cat_balance": cat_balance, "cat_dist": cat_dist,
           "ssr": {m: {"score": results[m][0]["survey_score"], "balance": results[m][1],
                       "survey_pmf": results[m][0]["survey_pmf"]} for m in results},
           "cci_truth": base.CCI_WINDOW_MEAN}
    with open(os.path.join(here, "ssr_demo_result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\nKimenet: ssr_demo_result.json")


if __name__ == "__main__":
    main()
