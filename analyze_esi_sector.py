#!/usr/bin/env python3
"""ORAKEL-II ESI szektor backteszt — ELEMZÉS a brief metrika-prioritása szerint.
Bemenet: esi_sector_result.json (panel) + esi_sector_ground_truth_LOCKED.json (zárolt vonalzó).
A NÉGY MÉRÉS:
 1) BASELINE-DELTA: between-sector Spearman a country-mean / persistence / sector-rank-perzisztencia FÖLÖTT.
 2) STATISZTIKAI ERŐ: pooled N szignifikancia (p), seed-szórás vs jel.
 3) WITHIN vs BETWEEN: between-sector (a tét) vs between-country (kontroll).
 4) PLACEBO: esi_sector_placebo_result.json — rossz korpusznál ROMLANIA kell.
HASZNÁLAT: python3 analyze_esi_sector.py
"""
import json, sys, os
import numpy as np
from scipy.stats import spearmanr

SECTORS = ["industry", "services", "retail", "construction"]
MONTHS = ["2025-09", "2025-11", "2026-01"]
PREV = {"2025-09": "2025-08", "2025-11": "2025-10", "2026-01": "2025-12"}

LK = json.load(open("esi_sector_ground_truth_LOCKED.json", encoding="utf-8"))
GT = LK["gt"]                      # GT[country][month][sector]
PANEL = json.load(open("esi_sector_result.json", encoding="utf-8"))
COUNTRIES = list(PANEL.keys())

def gtvec(c, m): return [GT[c][m][s] for s in SECTORS]
def pvec(c, m):  return [PANEL[c][m][s]["mean"] for s in SECTORS]

def sp(a, b):
    """Spearman; None ha bármelyik konstans (definiálatlan rang)."""
    if len(set(a)) < 2 or len(set(b)) < 2: return None
    return spearmanr(a, b)[0]

def main():
    print("="*84)
    print("ORAKEL-II — ESI SZEKTORÁLIS ÜZLETI BIZALOM — EREDMÉNY")
    print(f"  ground truth zárolva: {LK['_meta']['locked_at']}  ({LK['_meta']['source']})")
    print(f"  panel: {len(COUNTRIES)} ország × {len(MONTHS)} hó × {len(SECTORS)} szektor")
    print("="*84)

    # ---------- 1+3: BETWEEN-SECTOR (a tét) cellánként + baseline-delták ----------
    print("\n[1] BETWEEN-SECTOR Spearman cellánként (panel 4-szektor sorrend vs valós) + BASELINE-ek")
    print("    baseline-ek UGYANARRA a GT-re: country-mean (papagáj), persistence(M-1), rank-perzisztencia\n")
    rows = []  # (panel_rho, cmean_rho, persist_rho, rankpersist_rho)
    pooled_panel_pred, pooled_panel_gt = [], []   # pooled between-sector korreláció (z-score/cella)
    for c in COUNTRIES:
        for m in MONTHS:
            g = gtvec(c, m); p = pvec(c, m)
            r_panel = sp(p, g)
            # country-mean baseline: minden szektor = országos átlag → konstans → Spearman definiálatlan (0 diszkrim.)
            r_cmean = sp([np.mean(g)]*4, g)            # None (konstans)
            # persistence: szektor(M)=szektor(M-1) → a valós M-1 vektort használja prediktornak
            g_prev = [GT[c][PREV[m]][s] for s in SECTORS]
            r_persist = sp(g_prev, g)
            rows.append((c, m, r_panel, r_cmean, r_persist))
            # pooled: cellán belül z-score-olt szektorértékek (hogy az országszint ne dominálja)
            gz = (np.array(g)-np.mean(g))/(np.std(g)+1e-9)
            pz = (np.array(p)-np.mean(p))/(np.std(p)+1e-9)
            pooled_panel_gt.extend(gz.tolist()); pooled_panel_pred.extend(pz.tolist())
            cm = "  (konst.)" if r_cmean is None else f"{r_cmean:+.2f}"
            print(f"  {c} {m}: panel ρ={fmt(r_panel)} | country-mean={cm} | persistence ρ={fmt(r_persist)}")

    panel_rhos = [r for *_, in [(r[2],) for r in rows] if r is not None]
    persist_rhos = [r[4] for r in rows if r[4] is not None]
    print("\n  ── ÖSSZEGZÉS (between-sector) ──")
    print(f"   panel  átlag Spearman = {np.mean(panel_rhos):+.3f}  (±{np.std(panel_rhos):.3f}, n={len(panel_rhos)} cella)")
    print(f"   persistence átlag      = {np.mean(persist_rhos):+.3f}  (a kemény baseline)")
    print(f"   country-mean (papagáj) = 0.000  (konstans → definíció szerint 0 diszkrimináció)")
    print(f"   ► DELTA a country-mean fölött = {np.mean(panel_rhos):+.3f}  (a brief FŐ száma)")
    print(f"   ► DELTA a persistence fölött  = {np.mean(panel_rhos)-np.mean(persist_rhos):+.3f}")

    # ---------- 2: POOLED szignifikancia ----------
    rho_pool, p_pool = spearmanr(pooled_panel_pred, pooled_panel_gt)
    print(f"\n[2] STATISZTIKAI ERŐ — pooled between-sector (cellán belül z-score-olt, N={len(pooled_panel_pred)} szektor-pont)")
    print(f"   Spearman ρ = {rho_pool:+.3f},  p = {p_pool:.2e}  → {'SZIGNIFIKÁNS (p<0,05)' if p_pool<0.05 else 'NEM szignifikáns'}")
    # zaj-diagnózis: átlagos seed-szórás vs cellák közti jel
    all_means = [PANEL[c][m][s]["mean"] for c in COUNTRIES for m in MONTHS for s in SECTORS]
    all_stds  = [PANEL[c][m][s]["std"]  for c in COUNTRIES for m in MONTHS for s in SECTORS]
    sig = np.std(all_means)
    print(f"   átlagos seed-szórás = {np.mean(all_stds):.1f}  vs  cellák közti jel-szórás = {sig:.1f}"
          f"  → {'ZAJOS (szórás > jel/2)' if np.mean(all_stds) > sig/2 else 'OK (jel > 2×zaj)'}")

    # ---------- 3: BETWEEN-COUNTRY (kontroll) ----------
    print("\n[3] WITHIN vs BETWEEN kontroll — BETWEEN-COUNTRY Spearman (egy szektoron belül az országsorrend)")
    bc = []
    for m in MONTHS:
        for s in SECTORS:
            g = [GT[c][m][s] for c in COUNTRIES]; p = [PANEL[c][m][s]["mean"] for c in COUNTRIES]
            r = sp(p, g)
            if r is not None: bc.append(r)
    print(f"   between-country átlag Spearman = {np.mean(bc):+.3f}  (±{np.std(bc):.3f}, n={len(bc)})  [kontroll, a CCI-nél bizonyított]")

    # ---------- irány-hit (cross-time delta) szektoronként ----------
    print("\n[4] IRÁNY-HIT szektoronként (panel Δ vs valós Δ, 2025-09→2026-01)")
    for s in SECTORS:
        hit = tot = 0
        for c in COUNTRIES:
            dp = PANEL[c]["2026-01"][s]["mean"] - PANEL[c]["2025-09"][s]["mean"]
            dg = GT[c]["2026-01"][s] - GT[c]["2025-09"][s]
            tot += 1
            if (dp > 0) == (dg > 0) or abs(dg) < 1.5: hit += 1
        print(f"   {s:12s}: {hit}/{tot} ország irány-egyezés")

    # ---------- PLACEBO ----------
    print("\n[5] PLACEBO (negatív kontroll) — rossz (rossz-ablakú) korpusz → ROMLANIA kell")
    if os.path.exists("esi_sector_placebo_result.json"):
        PL = json.load(open("esi_sector_placebo_result.json", encoding="utf-8"))
        for c in PL:
            for m in PL[c]:
                g = gtvec(c, m); p = [PL[c][m][s]["mean"] for s in SECTORS]
                r_pl = sp(p, g); r_real = sp(pvec(c, m), g)
                worse = "✓ romlott" if (r_pl is not None and r_real is not None and r_pl < r_real) else "✗ NEM romlott"
                print(f"   {c} {m}: valós-korpusz ρ={fmt(r_real)} → placebo ρ={fmt(r_pl)}   {worse}")
    else:
        print("   (esi_sector_placebo_result.json hiányzik — futtasd: ESI_PLACEBO=1 ... )")
    print("\n"+"="*84)

def fmt(x): return " n/a " if x is None else f"{x:+.2f}"

if __name__ == "__main__":
    main()
