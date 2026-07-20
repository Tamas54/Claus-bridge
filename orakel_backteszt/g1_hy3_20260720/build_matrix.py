#!/usr/bin/env python3
"""G1 EREDMÉNY-MÁTRIX összeállító — a hat futam artefaktjaiból + a történeti
Flash-artefaktokból (Flash-hívás NÉLKÜL). Kimenet: g1_matrix.json (+ a .md-t a
jegyzőkönyv-író tölti)."""
import json
import os
import statistics

from scipy.stats import spearmanr

G1 = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(G1, "..", ".."))


def j(path):
    return json.load(open(path, encoding="utf-8"))


def main():
    M = {"date": "2026-07-20", "model_new": "tencent/Hy3",
         "model_hist": "deepseek-ai/DeepSeek-V4-Flash (történeti artefaktokból, futtatás nélkül)",
         "cells": []}

    # ── 1. HU pre-election ──
    a = j(os.path.join(G1, "hu_pre_election/hy3_panel_g1.json"))
    M["cells"].append({
        "domain": "election", "country": "HU", "cell": "HU pre-election Tisza-Fidesz rés",
        "flag": a["flag"], "gt": a["ground_truth_gap"],
        "hy3": {"mean_gap": a["mean_gap"], "per_seed": [s["tisza_fidesz_gap"] for s in a["seeds"]],
                "abs_err": a["abs_err"], "direction_hit": a["mean_gap"] > 0},
        "flash": {"mean_gap": 20.888, "abs_err": 6.32, "direction_hit": True, "seeds": 2},
    })

    # ── 2. CZ election (base + multiparty) ──
    for var in ("base", "multiparty"):
        a = j(os.path.join(G1, f"cz_election/hy3_panel_g1_{var}.json"))
        fl = j(os.path.join(REPO, "orakel_backteszt_cz",
                            f"panel_results_deepseek-ai_DeepSeek-V4-Flash{'_multiparty' if var == 'multiparty' else ''}.json"))
        gt_rank = ["ANO", "SPOLU", "STAN", "Pirati", "SPD", "Motoriste", "Stacilo"]
        gt_shares = {p: v["pct"] for p, v in a["ground_truth"].items()}

        def rank_rho(agg):
            ps = [p for p in gt_rank]
            return float(spearmanr([agg.get(p, 0.0) for p in ps], [gt_shares[p] for p in ps])[0])

        M["cells"].append({
            "domain": "election", "country": "CZ", "cell": f"CZ election ({var})",
            "flag": a["flag"], "gt": {"rank": gt_rank, "ano_spolu_gap": a["ground_truth_ano_spolu_gap"]},
            "hy3": {"ano_first": a["evaluation"]["ano_first"], "spolu_second": a["evaluation"]["spolu_second"],
                    "stacilo_out": a["evaluation"]["stacilo_out"], "panel_rank": a["evaluation"]["panel_rank"],
                    "ano_spolu_gap": a["evaluation"]["ano_spolu_gap"],
                    "gap_abs_err": abs(a["evaluation"]["ano_spolu_gap"] - a["ground_truth_ano_spolu_gap"]),
                    "share_spearman_vs_gt": rank_rho(a["aggregate_shares"]), "seeds": len(a["seeds"])},
            "flash": {"ano_first": fl["evaluation"]["ano_first"], "spolu_second": fl["evaluation"]["spolu_second"],
                      "stacilo_out": fl["evaluation"]["stacilo_out"], "panel_rank": fl["evaluation"]["panel_rank"],
                      "ano_spolu_gap": fl["evaluation"]["ano_spolu_gap"],
                      "gap_abs_err": abs(fl["evaluation"]["ano_spolu_gap"] - a["ground_truth_ano_spolu_gap"]),
                      "share_spearman_vs_gt": rank_rho(fl["aggregate_shares"]), "seeds": len(fl["seeds"])},
        })

    # ── 3. CCI headline ──
    for cell, gt_key in (("hu", None), ("cz", None), ("pt", None)):
        a = j(os.path.join(G1, f"cci_headline/hy3_{cell}_cci.json"))
        gt = a["gt"] if not isinstance(a["gt"], dict) else a["gt"]["gt_window"]
        flash_bal = {"hu": a.get("flash_reference", {}).get("recomputed_offline_on_tracked_sentences", -18.7),
                     "cz": -2.5, "pt": -28.32}[cell]
        M["cells"].append({
            "domain": "cci_headline", "country": cell.upper(), "cell": f"{cell.upper()} CCI headline",
            "flag": a["flag"], "gt": gt,
            "hy3": {"balance": a["mean_ssr_balance"], "per_seed": [s["ssr_balance"] for s in a["per_seed"]],
                    "abs_err": abs(a["mean_ssr_balance"] - gt), "direction_hit": (a["mean_ssr_balance"] < 0) == (gt < 0)},
            "flash": {"balance": flash_bal, "abs_err": abs(flash_bal - gt), "direction_hit": (flash_bal < 0) == (gt < 0)},
        })

    # ── 4. Nowcast-ív ──
    for co, months in (("hu", ["2025-06", "2025-12", "2026-05"]),
                       ("cz", ["2025-julsep", "2026-01", "2026-05"])):
        cells = [j(os.path.join(G1, f"cci_nowcast/hy3_{co}_{m}.json")) for m in months]
        hy3_vals = [c["mean_nowcast"] for c in cells]
        gts = [c["gt"] for c in cells]
        flash_vals = [c["flash_reference"]["nowcast"] for c in cells]
        M["cells"].append({
            "domain": "cci_nowcast_arc", "country": co.upper(), "cell": f"{co.upper()} nowcast-ív {months}",
            "flag": [c["flag"] for c in cells], "gt": gts,
            "hy3": {"nowcast": hy3_vals, "per_seed": {m: [s["nowcast"] for s in c["per_seed"]] for m, c in zip(months, cells)},
                    "arc_spearman": float(spearmanr(hy3_vals, gts)[0]),
                    "mae": statistics.mean(abs(v - g) for v, g in zip(hy3_vals, gts))},
            "flash": {"nowcast": flash_vals, "arc_spearman": float(spearmanr(flash_vals, gts)[0]),
                      "mae": statistics.mean(abs(v - g) for v, g in zip(flash_vals, gts))},
        })

    # ── 5. Inflexp ──
    a = j(os.path.join(G1, "inflexp/hy3_inflexp.json"))
    fl = j(os.path.join(REPO, "inflexp_result.json"))
    C = ["HU", "CZ", "PT", "PL"]
    fl_rho_p = [float(spearmanr([fl["price_score"][c][ti] for c in C], [fl["price_gt"][c][ti] for c in C])[0]) for ti in range(3)]
    fl_rho_c = [float(spearmanr([fl["cci_balance"][c][ti] for c in C], [fl["cci_gt"][c][ti] for c in C])[0]) for ti in range(3)]
    fl_delta = {c: ((fl["price_score"][c][1] - fl["price_score"][c][0] > 0) == (fl["price_gt"][c][1] - fl["price_gt"][c][0] > 0)
                    or abs(fl["price_gt"][c][1] - fl["price_gt"][c][0]) < 2) for c in C}
    hy_delta = {c: a["per_seed"][0]["cross_time_delta"][c]["ok"] for c in C}
    # seed-átlag deltából számolt irány
    hy_delta_mean = {c: ((a["seed_mean"]["price_score"][c][1] - a["seed_mean"]["price_score"][c][0] > 0)
                         == (a["gt"]["price_gt"][c][1] - a["gt"]["price_gt"][c][0] > 0)
                         or abs(a["gt"]["price_gt"][c][1] - a["gt"]["price_gt"][c][0]) < 2) for c in C}
    M["cells"].append({
        "domain": "inflation_expectation", "country": "HU/CZ/PT/PL", "cell": "inflexp 4 ország x 3 hó",
        "flag": a["flag"], "gt": a["gt"],
        "hy3": {"spearman_price_by_month_seedmean": a["seed_mean"]["spearman_price"],
                "spearman_cci_by_month_seedmean": a["seed_mean"]["spearman_cci"],
                "spearman_price_per_seed": [s["spearman_price"] for s in a["per_seed"]],
                "spearman_cci_per_seed": [s["spearman_cci"] for s in a["per_seed"]],
                "cross_time_delta_dir_ok_seedmean": hy_delta_mean},
        "flash": {"spearman_price_by_month": fl_rho_p, "spearman_cci_by_month": fl_rho_c,
                  "cross_time_delta_dir_ok": fl_delta},
    })

    # ── 6. YT ──
    a = j(os.path.join(G1, "yt_title/hy3_harvester.json"))
    M["cells"].append({
        "domain": "yt_title_appeal", "country": "EN", "cell": "YT hit@3 (7 csatorna)",
        "flag": a["flag"], "gt": "valós nézettség/hó rangsor (channels_data.json)",
        "hy3": {"mean_rho": a["aggregate"]["mean_rho"], "hit3": a["aggregate"]["hit3"],
                "n_channels": a["aggregate"]["n_channels"],
                "per_channel": [{"label": r["label"], "rho": r["rho"], "hit3": r["hit3"],
                                 "flag": r["flag"]} for r in a["results"]]},
        "flash": {"mean_rho": a["flash_reference"]["mean_rho"], "hit3": a["flash_reference"]["hit3"],
                  "n_channels": a["flash_reference"]["n_channels"]},
    })

    # ── költség-összesítés ──
    cost = {}
    for root, _, files in os.walk(G1):
        for f in files:
            if f.endswith(".json") and f.startswith("hy3_"):
                d = j(os.path.join(root, f))
                if "cost" in d:
                    cost[os.path.relpath(os.path.join(root, f), G1)] = d["cost"]
    total_calls = sum(c["n_calls"] for c in cost.values())
    total_ptok = sum(c["est_prompt_tokens"] for c in cost.values())
    total_ctok = sum(c["est_completion_tokens"] for c in cost.values())
    M["cost_log"] = {"by_artifact": cost,
                     "total": {"n_calls": total_calls, "est_prompt_tokens": total_ptok,
                               "est_completion_tokens": total_ctok},
                     "note": "token = karakter/3,2 becslés; Hy3 = $0 (NULLTARIF); embedding: OpenAI text-embedding-3-small"}

    out = os.path.join(G1, "g1_matrix.json")
    json.dump(M, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("g1_matrix.json kész:", out)
    print(json.dumps(M["cost_log"]["total"], indent=1))


if __name__ == "__main__":
    main()
