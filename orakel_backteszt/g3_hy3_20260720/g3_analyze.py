#!/usr/bin/env python3
"""OPERATION PYTHIA — G3 elemző: modell-invariáns-e a scope-határ?

Ugyanazokat a metrikákat számolja a történeti Flash-panelre (esi_sector_result.json,
pre-reg d4de015) és a G3 Hy3-panelre (g3_hy3_result.json), az analyze_esi_sector.py
történeti protokollja szerint (annak [1] utáni display-bugja nélkül):
  1) between-sector Spearman cellánként + átlag  (A KULCSSZÁM)
  2) pooled (cellán belül z-score-olt) Spearman + p
  3) persistence baseline (GT M-1) — modelltől független vonalzó
  4) between-country kontroll (szektoron belüli országsorrend)
  5) irány-hit szektoronként (2025-09→2026-01, |ΔGT|<1.5 tolerancia — történeti szabály)
  6) seed-stabilitás: seedenkénti between-sector átlag-ρ
  7) kereszt-modell egyezés: Hy3 vs Flash cella-ρ-k és cellán belüli predikció-rangok
Kimenet: g3_falszifikacio.json (stdout mellett).
"""
import json
import os

import numpy as np
from scipy.stats import spearmanr

G3DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(G3DIR, "..", ".."))

SECTORS = ["industry", "services", "retail", "construction"]
MONTHS = ["2025-09", "2025-11", "2026-01"]
PREV = {"2025-09": "2025-08", "2025-11": "2025-10", "2026-01": "2025-12"}

LK = json.load(open(os.path.join(REPO, "esi_sector_ground_truth_LOCKED.json"), encoding="utf-8"))
GT = LK["gt"]
FLASH = json.load(open(os.path.join(REPO, "esi_sector_result.json"), encoding="utf-8"))
HY3 = json.load(open(os.path.join(G3DIR, "g3_hy3_result.json"), encoding="utf-8"))
COUNTRIES = list(HY3.keys())


def sp(a, b):
    if len(set(a)) < 2 or len(set(b)) < 2:
        return None
    return float(spearmanr(a, b)[0])


def gtvec(c, m):
    return [GT[c][m][s] for s in SECTORS]


def panel_metrics(panel, n_seeds):
    out = {"cells": [], "pooled_pred": [], "pooled_gt": []}
    for c in COUNTRIES:
        for m in MONTHS:
            g = gtvec(c, m)
            p = [panel[c][m][s]["mean"] for s in SECTORS]
            rho = sp(p, g)
            out["cells"].append({"country": c, "month": m, "rho": rho})
            gz = (np.array(g) - np.mean(g)) / (np.std(g) + 1e-9)
            pz = (np.array(p) - np.mean(p)) / (np.std(p) + 1e-9)
            out["pooled_gt"].extend(gz.tolist())
            out["pooled_pred"].extend(pz.tolist())
    rhos = [r["rho"] for r in out["cells"] if r["rho"] is not None]
    out["between_sector_mean_rho"] = float(np.mean(rhos))
    out["between_sector_sd_rho"] = float(np.std(rhos))
    pr, pp = spearmanr(out["pooled_pred"], out["pooled_gt"])
    out["pooled_rho"], out["pooled_p"] = float(pr), float(pp)

    # between-country kontroll
    bc = []
    for m in MONTHS:
        for s in SECTORS:
            g = [GT[c][m][s] for c in COUNTRIES]
            p = [panel[c][m][s]["mean"] for c in COUNTRIES]
            r = sp(p, g)
            if r is not None:
                bc.append(r)
    out["between_country_mean_rho"] = float(np.mean(bc))
    out["between_country_sd_rho"] = float(np.std(bc))

    # irány-hit (történeti szabály: egyező előjel VAGY |ΔGT|<1.5)
    dh = {}
    for s in SECTORS:
        hit = tot = 0
        for c in COUNTRIES:
            dp = panel[c]["2026-01"][s]["mean"] - panel[c]["2025-09"][s]["mean"]
            dg = GT[c]["2026-01"][s] - GT[c]["2025-09"][s]
            tot += 1
            if (dp > 0) == (dg > 0) or abs(dg) < 1.5:
                hit += 1
        dh[s] = f"{hit}/{tot}"
    out["direction_hits"] = dh

    # seed-stabilitás: seedenkénti between-sector átlag-ρ
    per_seed = []
    for si in range(n_seeds):
        rs = []
        for c in COUNTRIES:
            for m in MONTHS:
                bals = [panel[c][m][s]["balances"] for s in SECTORS]
                if any(len(b) <= si for b in bals):
                    continue
                r = sp([b[si] for b in bals], gtvec(c, m))
                if r is not None:
                    rs.append(r)
        per_seed.append(float(np.mean(rs)))
    out["per_seed_between_sector_mean_rho"] = per_seed
    del out["pooled_pred"], out["pooled_gt"]
    return out


def main():
    persist = [sp([GT[c][PREV[m]][s] for s in SECTORS], gtvec(c, m))
               for c in COUNTRIES for m in MONTHS]
    persist = [r for r in persist if r is not None]

    flash = panel_metrics(FLASH, 5)
    hy3 = panel_metrics(HY3, 3)

    # kereszt-modell egyezés
    fr = [r["rho"] for r in flash["cells"]]
    hr = [r["rho"] for r in hy3["cells"]]
    cell_rho_agree = sp(fr, hr)
    # cellán belüli predikció-rang egyezés (36 cella poolban, z-score-olt)
    fp, hp = [], []
    for c in COUNTRIES:
        for m in MONTHS:
            f = np.array([FLASH[c][m][s]["mean"] for s in SECTORS])
            h = np.array([HY3[c][m][s]["mean"] for s in SECTORS])
            fp.extend(((f - f.mean()) / (f.std() + 1e-9)).tolist())
            hp.extend(((h - h.mean()) / (h.std() + 1e-9)).tolist())
    pred_agree, pred_agree_p = spearmanr(fp, hp)

    result = {
        "question": "modell-invarians-e a narrativ/strukturalis scope-hatar (between-sector)?",
        "gt_locked": LK["_meta"],
        "persistence_baseline_mean_rho": float(np.mean(persist)),
        "flash_historical": flash,
        "hy3_g3": hy3,
        "cross_model": {
            "cell_rho_spearman": cell_rho_agree,
            "pooled_prediction_spearman": float(pred_agree),
            "pooled_prediction_p": float(pred_agree_p),
        },
    }
    out = os.path.join(G3DIR, "g3_falszifikacio.json")
    json.dump(result, open(out, "w"), ensure_ascii=False, indent=2)

    print("=" * 80)
    print("G3 — MODELL-INVARIANCIA TESZT (between-sector diszkrimináció)")
    print("=" * 80)
    print(f"{'cella':<14}{'Flash ρ':>9}{'Hy3 ρ':>9}")
    for f, h in zip(flash["cells"], hy3["cells"]):
        print(f"{f['country']} {f['month']:<10}{f['rho']:>+9.2f}{h['rho']:>+9.2f}")
    print("-" * 80)
    print(f"between-sector átlag ρ : Flash {flash['between_sector_mean_rho']:+.3f}"
          f" (±{flash['between_sector_sd_rho']:.3f})  |  Hy3 {hy3['between_sector_mean_rho']:+.3f}"
          f" (±{hy3['between_sector_sd_rho']:.3f})")
    print(f"pooled ρ (p)           : Flash {flash['pooled_rho']:+.3f} (p={flash['pooled_p']:.3f})"
          f"  |  Hy3 {hy3['pooled_rho']:+.3f} (p={hy3['pooled_p']:.3f})")
    print(f"persistence baseline   : {np.mean(persist):+.3f}  (GT M-1, modell-független)")
    print(f"between-country kontroll: Flash {flash['between_country_mean_rho']:+.3f}"
          f"  |  Hy3 {hy3['between_country_mean_rho']:+.3f}")
    print(f"irány-hit               : Flash {flash['direction_hits']}  |  Hy3 {hy3['direction_hits']}")
    print(f"seed-ρ (per seed)       : Flash {[round(x,3) for x in flash['per_seed_between_sector_mean_rho']]}")
    print(f"                          Hy3   {[round(x,3) for x in hy3['per_seed_between_sector_mean_rho']]}")
    print(f"kereszt-modell          : cella-ρ egyezés {cell_rho_agree:+.3f}; "
          f"predikció-rang egyezés {pred_agree:+.3f} (p={pred_agree_p:.2e})")
    print(f"[g3] artefakt: {out}")


if __name__ == "__main__":
    main()
