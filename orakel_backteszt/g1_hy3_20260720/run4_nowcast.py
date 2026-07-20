#!/usr/bin/env python3
"""G1/4. futam — CCI nowcaster-ív Hy3-replikáció (HU emelkedő + CZ csúcs-alak).

Protokoll = orakel_hu_nowcast.py / orakel_cz_nowcast.py változatlanul
(N=60, 18 mintavett hír/persona, temp=0.8, max_tokens=160, SSR linear +
text-embedding-3-small), modell = tencent/Hy3, seeds 1-3 hónaponként.
Cellák + flagek (GT a committed hu_nowcast_*/cz_nowcast_* artefaktokból):
  HU 2025-06 (-29.7) GRAY | HU 2025-12 (-19.4) CLEAN | HU 2026-05 (-2.1) CLEAN
  CZ 2025-julsep (-7.5) GRAY(2025-07) | CZ 2026-01 (+1.1) CLEAN | CZ 2026-05 (-7.6) CLEAN
Hívás: G1_COUNTRY=hu|cz G1_MONTH=<label> python3 run4_nowcast.py
"""
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g1_lib

COUNTRY = os.environ["G1_COUNTRY"]
MONTH = os.environ["G1_MONTH"]

CELLS = {
    ("hu", "2025-06"): ("corpus_hu_2025_06.json", -29.7, "GRAY", "target-ablak 2025-06 a cutoff-farok GRAY sávjában (G0a)"),
    ("hu", "2025-12"): ("corpus_hu_2025_12.json", -19.4, "CLEAN", "target-ablak >= 2025-08"),
    ("hu", "2026-05"): ("corpus_hu_2026_05.json", -2.1, "CLEAN", "target-ablak >= 2025-08"),
    ("cz", "2025-julsep"): ("corpus_cz_2025_julsep.json", -7.5, "GRAY", "ablak 2025-07..09; a 2025-07 GRAY sávban"),
    ("cz", "2026-01"): ("corpus_cz_2026_01.json", 1.1, "CLEAN", "target-ablak >= 2025-08"),
    ("cz", "2026-05"): ("corpus_cz_2026_05.json", -7.6, "CLEAN", "target-ablak >= 2025-08"),
}
CORPUS_JSON, CCI_GT, FLAG, FLAG_NOTE = CELLS[(COUNTRY, MONTH)]

g1_lib.load_env()
os.environ["ORAKEL_PANEL_MODEL"] = g1_lib.MODEL
os.environ["CORPUS_JSON"] = CORPUS_JSON
os.environ["CCI_GT"] = str(CCI_GT)
os.environ["MONTH_LABEL"] = MONTH
os.environ.setdefault("SSR_DEMO_N", "60")
os.environ.setdefault("ORAKEL_CACHE", "1")   # a történeti futások is cache-eltek

sys.path.insert(0, g1_lib.REPO)
if COUNTRY == "hu":
    import orakel_hu_nowcast as m   # noqa: E402
    from plugins import ssr         # noqa: E402
    ANCHORS = ssr.REFERENCE_SETS_HU["anyagi_varakozas"]
else:
    import orakel_cz_nowcast as m   # noqa: E402
    from plugins import ssr         # noqa: E402
    ANCHORS = ssr.REFERENCE_SETS_CZ["financni_vyhled"]

SEEDS = [int(s) for s in os.environ.get("G1_SEEDS", "1,2,3").split(",")]
N = m.N


def main():
    corpus_path = os.path.join(g1_lib.REPO, CORPUS_JSON)
    s0, u0 = m.prompt(random.Random(0))
    pacer = g1_lib.Pacer(g1_lib.pace_for(g1_lib.est_tokens(s0, u0, 160)))
    call = pacer.wrap(m.call)
    print(f"G1/4 nowcast — {COUNTRY.upper()} {MONTH}, modell={g1_lib.MODEL}, N={N}, seeds={SEEDS}")

    per_seed = []
    for seed in SEEDS:
        rng = random.Random(seed)
        prompts = [m.prompt(rng) for _ in range(N)]

        def ask(i):
            try:
                return call(prompts[i][0], prompts[i][1], seed * 100000 + i)
            except Exception:  # noqa: BLE001
                return ""

        with ThreadPoolExecutor(max_workers=g1_lib.CONCURRENCY) as ex:
            sents = [s for s in ex.map(ask, range(N)) if s]
        res = ssr.rate(sents, ANCHORS, method="linear", embed_fn=m.oai_embed)
        bal = (res["survey_score"] - 3) / 2 * 100
        per_seed.append({"seed": seed, "n_sent": len(sents), "ssr_score": res["survey_score"],
                         "nowcast": bal, "sentences": sents})
        print(f"  seed {seed}: nowcast {bal:+.1f} | GT {CCI_GT:+.1f} | hiba {bal - CCI_GT:+.1f} (n={len(sents)})")

    mean_bal = sum(s["nowcast"] for s in per_seed) / len(per_seed)
    hist = json.load(open(os.path.join(g1_lib.REPO, f"{COUNTRY}_nowcast_{MONTH}.json"), encoding="utf-8"))
    g1_lib.write_artifact(f"cci_nowcast/hy3_{COUNTRY}_{MONTH}.json", {
        "run": f"G1/4 {COUNTRY.upper()} CCI nowcast {MONTH}",
        "model": g1_lib.MODEL, "provider": "siliconflow", "date": "2026-07-20",
        "protocol": f"orakel_{COUNTRY}_nowcast.py változatlanul (N={N}, SSR linear + OpenAI-small)",
        "flag": FLAG, "flag_note": FLAG_NOTE,
        "corpus_files": {CORPUS_JSON: g1_lib.sha256_file(corpus_path)},
        "gt": CCI_GT, "per_seed": per_seed, "mean_nowcast": mean_bal,
        "abs_err": abs(mean_bal - CCI_GT),
        "flash_reference": {"file": f"{COUNTRY}_nowcast_{MONTH}.json", "nowcast": hist["nowcast"],
                            "abs_err": abs(hist["nowcast"] - CCI_GT)},
        "cost": pacer.stats(), "raw_stream": pacer.raw_stream,
    })


if __name__ == "__main__":
    main()
