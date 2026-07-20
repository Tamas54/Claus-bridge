#!/usr/bin/env python3
"""G1/6. futam — YT cím/appeal hit@3 Hy3-replikáció a meglévő címkézett készleten.

Protokoll = yt_title_test/run_harvester.py változatlanul (slate-of-4, generikus
50/35/15 persona-blend, N=40, N_SEEDS=3 a harness SAJÁT seed-ciklusával, temp=0.8),
modell = tencent/Hy3 (a base.call az ORAKEL_PANEL_MODEL-t olvassa).
Címkézett készlet: yt_title_test/channels_data.json (7 csatorna, evidence-commit 7068da7).
A base.call-t pacingelt wrapperre cseréljük (harness-fájl érintetlen); az eredményt
a g1 könyvtárba írjuk, a történeti harvester_results.json-t nem írjuk felül.

Flag: csatornánként a címek publikálási hónapjából (referencia: a készlet
2026-06-16-i lehúzása; hónap ~ 2026-06 - age_mo):
  minden cím >= 2025-08 -> CLEAN; van 2025-05..07 cím -> GRAY; van <= 2025-04 cím
  -> CONTAMINATED-PARTIAL (a régi címek relatív teljesítménye a modell tudásán
  belül lehet — a történeti Flash-futásnak ugyanez a kitettsége volt).
"""
import datetime
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g1_lib

g1_lib.load_env()
os.environ["ORAKEL_PANEL_MODEL"] = g1_lib.MODEL
os.environ.setdefault("SSR_DEMO_N", "40")
os.environ.setdefault("N_SEEDS", "3")
os.environ.setdefault("ORAKEL_CACHE", "1")
os.environ.setdefault("WORKERS", str(g1_lib.CONCURRENCY))

YT_DIR = os.path.join(g1_lib.REPO, "yt_title_test")
sys.path.insert(0, YT_DIR)
sys.path.insert(0, g1_lib.REPO)
import run_harvester as h   # noqa: E402
import run_title_test as base  # noqa: E402

REF_MONTH = (2026, 6)   # a channels_data lehúzási hónapja


def title_flag(age_mo):
    y = REF_MONTH[0] + (REF_MONTH[1] - 1 - age_mo) // 12
    mo = (REF_MONTH[1] - 1 - age_mo) % 12 + 1
    ym = f"{y:04d}-{mo:02d}"
    if ym >= "2025-08":
        return "CLEAN", ym
    if ym >= "2025-05":
        return "GRAY", ym
    return "CONTAMINATED", ym


def channel_flag(ch):
    flags = [title_flag(t["age_mo"])[0] for t in ch["titles"]]
    if "CONTAMINATED" in flags:
        return "CONTAMINATED-PARTIAL", f"{flags.count('CONTAMINATED')}/{len(flags)} cím <= 2025-04"
    if "GRAY" in flags:
        return "GRAY", f"{flags.count('GRAY')}/{len(flags)} cím a 2025-05..07 sávban"
    return "CLEAN", "minden cím >= 2025-08"


def main():
    channels = json.load(open(os.path.join(YT_DIR, "channels_data.json"), encoding="utf-8"))
    # pacing: rövid slate-prompt
    s0 = h.sys_prompt("history", "male", "25-34", "the USA", "a casual viewer")
    u0 = h.user_text(channels[0]["titles"], [0, 1, 2, 3])
    pacer = g1_lib.Pacer(g1_lib.pace_for(g1_lib.est_tokens(s0, u0, 120)))
    h.base.call = pacer.wrap(base.call)   # a harness-fájl érintetlen, csak a modul-objektum
    print(f"G1/6 YT harvester — modell={g1_lib.MODEL} (base.MODEL={base.MODEL}), "
          f"N={h.N}, N_SEEDS={h.N_SEEDS}, {len(channels)} csatorna")

    res = []
    for ch in channels:
        r = h.run_channel(ch)
        fl, fnote = channel_flag(ch)
        r["flag"] = fl
        r["flag_note"] = fnote
        res.append(r)
        print(f"  {r['label']:24s} Spearman={r['rho']:+.2f} hit@3={'OK' if r['hit3'] else 'X'} "
              f"(átfedés {r['overlap3']}/3) [{fl}]")

    rhos = [r["rho"] for r in res if r["rho"] == r["rho"]]
    hits = sum(1 for r in res if r["hit3"])
    hist = json.load(open(os.path.join(YT_DIR, "harvester_results.json"), encoding="utf-8"))
    hist_rhos = [r["rho"] for r in hist if r["rho"] == r["rho"]]
    hist_hits = sum(1 for r in hist if r["hit3"])
    print(f"  ÖSSZ: átlag Spearman {statistics.mean(rhos):+.2f} | hit@3 {hits}/{len(res)} "
          f"(Flash történeti: {statistics.mean(hist_rhos):+.2f}, {hist_hits}/{len(hist)})")

    g1_lib.write_artifact("yt_title/hy3_harvester.json", {
        "run": "G1/6 YT cím/appeal hit@3 (téma-harvester)",
        "model": g1_lib.MODEL, "provider": "siliconflow", "date": "2026-07-20",
        "protocol": "yt_title_test/run_harvester.py változatlanul (N=40, N_SEEDS=3, slate-of-4, generikus blend)",
        "flag": "MIXED (csatornánként, ld. results[].flag)",
        "flag_note": "a címek egy része <= 2025-04 publikálású — a történeti Flash-futásnak ugyanez volt a kitettsége",
        "labeled_set": {"yt_title_test/channels_data.json":
                        g1_lib.sha256_file(os.path.join(YT_DIR, "channels_data.json"))},
        "results": res,
        "aggregate": {"mean_rho": statistics.mean(rhos), "stdev_rho": statistics.pstdev(rhos),
                      "hit3": hits, "n_channels": len(res)},
        "flash_reference": {"file": "yt_title_test/harvester_results.json",
                            "mean_rho": statistics.mean(hist_rhos), "hit3": hist_hits,
                            "n_channels": len(hist)},
        "cost": pacer.stats(),
        "raw_stream": pacer.raw_stream,
    })


if __name__ == "__main__":
    main()
