#!/usr/bin/env python3
"""OPERATION PYTHIA — G3: FALSZIFIKÁCIÓ-MEGERŐSÍTÉS futtató (ESI szektorális teszt Hy3-mal).

EGYETLEN kérdés: modellfüggetlen-e a narratív/strukturális scope-határ?
Történeti eredmény (DeepSeek-V4-Flash, esi_sector_result.json): between-sector
diszkrimináció 9 cella panel-ρ átlag = −0.378 → FALSZIFIKÁLVA. Várt kimenet:
Hy3-mal IS bukik → a doktrína erősödik. (TILOS-lista 8.: ez NEM "hátha bug volt" futás.)

Protokoll: a történeti harness (run_esi_sector_backtest.py, pre-reg d4de015)
VÁLTOZATLANUL importálva — run_cell, promptok, korpusz-mapping, SSR-linear +
text-embedding-3-small, N=40/seed mind azonos. CSAK a modell és a seed-szám mozog:
  modell = tencent/Hy3 (SF, thinking:disabled, response_format nélkül, temp=0.8)
  seeds  = 3 (0,1,2 — a történeti 0..4 számozás első három tagja, rng azonos)
Pacing: G0a-szabály (g1_lib.Pacer): 100 hívás/perc token-bucket + a harness saját
backoffja; konkurrencia a harness-beli 12 (a harness nem módosul).

Kontamináció-flag (cutoff_jegyzokonyv.md 5. pont): target-ablakok 2025-09/2025-11/
2026-01 mind ≥ 2025-08 → CLEAN.

HASZNÁLAT (szeletenként, előtérben, repo-gyökérből):
  G3_COUNTRY=HU G3_MONTH=2025-09 python3 -u orakel_backteszt/g3_hy3_20260720/run_g3.py
SMOKE (plumbing-teszt, külön kimenetbe):
  G3_SMOKE=1 G3_COUNTRY=HU G3_MONTH=2025-09 python3 -u .../run_g3.py
"""
import json
import os
import random
import sys
import time

import numpy as np

G3DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(G3DIR, "..", "g1_hy3_20260720"))
import g1_lib  # noqa: E402  (Pacer + pace_for + load_env + sha256 — G1 változatlan)

g1_lib.load_env()
SMOKE = os.environ.get("G3_SMOKE") == "1"

# harness-paraméterezés MÉG az import előtt (a harness modul-szinten olvassa)
os.environ["ORAKEL_PANEL_MODEL"] = g1_lib.MODEL          # tencent/Hy3
os.environ["ESI_SEEDS"] = "1" if SMOKE else "3"
os.environ["SSR_DEMO_N"] = "4" if SMOKE else "40"
os.environ.setdefault("ORAKEL_CACHE", "1")

sys.path.insert(0, g1_lib.REPO)
os.chdir(g1_lib.REPO)  # a harness relatív korpusz-útjai miatt
import run_esi_sector_backtest as H  # noqa: E402  (a történeti harness, változatlan)

assert H.MODEL == "tencent/Hy3", f"model-guard: {H.MODEL}"
assert "Flash" not in H.MODEL and "deepseek" not in H.MODEL.lower(), "Flash TILOS"

COUNTRY = os.environ["G3_COUNTRY"]
MONTH = os.environ["G3_MONTH"]
MI = H.MONTHS.index(MONTH)
SECTORS = os.environ.get("G3_SECTORS", ",".join(H.SECTORS)).split(",")
OUT = os.path.join(G3DIR, "g3_smoke_result.json" if SMOKE else "g3_hy3_result.json")
META = os.path.join(G3DIR, "g3_smoke_meta.json" if SMOKE else "g3_run_meta.json")

corpus_file = H.CORP[(COUNTRY, MI)]
corpus = json.load(open(corpus_file, encoding="utf-8"))

# pacing a reprezentatív cella-promptra méretezve (16 mintacikk + system)
_rng = random.Random(0)
_news = "\n".join("- " + x for x in _rng.sample(corpus, min(16, len(corpus))))
_sys = H.SYS[COUNTRY].format(role=H.ROLE[COUNTRY]["industry"], y=MONTH,
                             size=H.FIRMSIZE[COUNTRY][0], reg=H.REGION[COUNTRY][0],
                             focus=H.FOCUS[COUNTRY]["industry"])
_user = f"{H.HDR[COUNTRY]}\n{_news}{H.ASK[COUNTRY]}"
pacer = g1_lib.Pacer(g1_lib.pace_for(g1_lib.est_tokens(_sys, _user, 160)))
H.sf = pacer.wrap(H.sf)  # a harness hívója pacingelve; a harness maga változatlan


def _load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main():
    print(f"G3 ESI/Hy3 — {COUNTRY} {MONTH} — modell={H.MODEL}, N={H.N}, SEEDS={H.SEEDS}, "
          f"pace={pacer.calls_per_min:.0f}/perc, korpusz={corpus_file} (n={len(corpus)})")
    results = _load(OUT)
    results.setdefault(COUNTRY, {}).setdefault(MONTH, {})
    t0 = time.time()
    for sector in SECTORS:
        if sector in results[COUNTRY][MONTH] and results[COUNTRY][MONTH][sector].get("mean") is not None:
            print(f"  {COUNTRY} {MONTH} {sector}: már kész, kihagyva")
            continue
        bals = H.run_cell(COUNTRY, MI, sector, corpus)
        mean = float(np.mean(bals)) if bals else None
        std = float(np.std(bals)) if len(bals) > 1 else 0.0
        results[COUNTRY][MONTH][sector] = {"balances": bals, "mean": mean,
                                           "std": std, "corpus": corpus_file}
        m = f"{mean:+6.1f}" if mean is not None else "  n/a "
        print(f"  {COUNTRY} {MONTH} {sector:12s}: mean={m}  std={std:4.1f}  (seeds={bals})")
        json.dump(results, open(OUT, "w"), ensure_ascii=False, indent=2)  # checkpoint

    meta = _load(META)
    meta.setdefault("model", H.MODEL)
    meta.setdefault("protocol", "run_esi_sector_backtest.run_cell VÁLTOZATLAN (pre-reg d4de015); "
                                "csak modell=Hy3 + seeds=3 (0,1,2) mozog")
    meta.setdefault("flag", "CLEAN")
    meta.setdefault("flag_note", "cutoff_jegyzokonyv.md 5. pont: target-ablak >= 2025-08 -> CLEAN; "
                                 "Hy3 szisztematikus cutoff ~2025-04, a 2025-09/11 es 2026-01 "
                                 "targetek melyen a tiszta zonaban")
    meta.setdefault("slices", {})
    meta["slices"][f"{COUNTRY}/{MONTH}"] = {
        "corpus_file": corpus_file, "corpus_sha256": g1_lib.sha256_file(corpus_file),
        "corpus_n": len(corpus), "sectors": SECTORS,
        "elapsed_s": round(time.time() - t0, 1), "cost": pacer.stats(),
    }
    json.dump(meta, open(META, "w"), ensure_ascii=False, indent=2)
    print(f"[g3] kész {time.time()-t0:.0f}s alatt → {OUT}")


if __name__ == "__main__":
    main()
