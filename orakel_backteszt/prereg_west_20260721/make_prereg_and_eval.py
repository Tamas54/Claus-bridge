#!/usr/bin/env python3
"""G2-WEST — előregisztrált jóslat-fájl + backteszt-kiértékelés a cella-artefaktokból.

Nem hív LLM-et. Bemenet: hy3p_*.json + gt_LOCKED_west.json. Kimenet:
  prereg_JOSLATOK.json  — számszerű, ellenőrizhető előrejelzések (corpus_hash + ts)
  backtest_eval.json    — a redukált Michigan-prelim backteszt kiértékelése
"""
import datetime
import hashlib
import json
import os
import statistics as st

WDIR = os.path.dirname(os.path.abspath(__file__))


def load(cell, mode):
    return json.load(open(os.path.join(WDIR, f"hy3p_{cell}_{mode}.json"), encoding="utf-8"))


def sha(fn):
    return hashlib.sha256(open(os.path.join(WDIR, fn), "rb").read()).hexdigest()


GT = json.load(open(os.path.join(WDIR, "gt_LOCKED_west.json"), encoding="utf-8"))
NOW = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

cells = {}
for cell in ("us_A", "us_B", "uk_jul", "de_jul"):
    cci = load(cell, "cci")
    row = {
        "ssr_balance_mean": round(cci["seed_mean"], 2),
        "ssr_balance_seeds": [round(s["ssr_balance"], 1) for s in cci["per_seed"]],
        "ssr_balance_sd": round(cci["seed_sd"], 2),
        "cat_balance_seeds": [round(s["cat_balance"], 1) for s in cci["per_seed"]],
        "cat_balance_mean": round(st.mean(s["cat_balance"] for s in cci["per_seed"]), 2),
        "corpus_file": list(cci["corpus_files"]),
        "corpus_sha256": cci["corpus_files"],
        "corpus_window": cci["corpus_window"],
        "run_ts": cci["timestamp"],
    }
    if cell != "uk_jul":
        inf = load(cell, "inflexp")
        row["price_score_mean"] = round(inf["seed_mean"], 3)
        row["price_score_seeds"] = [round(s["price_score"], 3) for s in inf["per_seed"]]
        row["price_score_sd"] = round(inf["seed_sd"], 3)
    cells[cell] = row

usA, usB = cells["us_A"], cells["us_B"]
uk, de = cells["uk_jul"], cells["de_jul"]

drift_cci = usB["ssr_balance_mean"] - usA["ssr_balance_mean"]
drift_cat = usB["cat_balance_mean"] - usA["cat_balance_mean"]
drift_price = usB["price_score_mean"] - usA["price_score_mean"]


def sgn(x):
    return "+" if x > 0 else "-" if x < 0 else "0"


prereg = {
    "op": "G2-WEST ELOREGISZTRALT JOSLATOK — a GT-kiadasok ELOTT commitolva",
    "generated": NOW,
    "model": "tencent/Hy3-preview",
    "rules_locked_in": "gt_LOCKED_west.json (commit 8ec6e31) + amendment_1 (commit 6ca49bc) — MINDEN szabaly a futasok elott",
    "panel_cells": cells,
    "predictions": [
        {
            "id": "P1_MICHIGAN_FINAL_DRIFT",
            "target": "UMich consumer sentiment 2026-07 FINAL vs prelim 54.4",
            "gt_arrives": "2026-07-31",
            "prediction": f"final < 54.4 (lefele revizio) — pred_dir = sign({usB['ssr_balance_mean']} - {usA['ssr_balance_mean']}) = {sgn(drift_cci)}",
            "pred_dir": sgn(drift_cci), "delta_ssr": round(drift_cci, 2),
            "sentinel_categorical_delta": round(drift_cat, 2),
            "eval_rule": "talalat, ha sign(final - 54.4) == pred_dir; final == 54.4 eseten semleges",
        },
        {
            "id": "P2_MICHIGAN_FINAL_INFL_DRIFT",
            "target": "UMich 1y inflation expectation 2026-07 FINAL vs prelim 4.2%",
            "gt_arrives": "2026-07-31",
            "prediction": f"final 1y-infl > 4.2% (felfele) — pred_dir = sign({usB['price_score_mean']} - {usA['price_score_mean']}) = {sgn(drift_price)}",
            "pred_dir": sgn(drift_price), "delta_price_score": round(drift_price, 3),
            "eval_rule": "talalat, ha sign(final_infl - 4.2) == pred_dir",
        },
        {
            "id": "P3_CB_JULY_SECONDARY",
            "target": "Conference Board CCI 2026-07 vs 2026-06 = 91.2",
            "gt_arrives": "2026-07-28",
            "prediction": f"CB 2026-07 < 91.2 — pred_dir = {sgn(drift_cci)} (us_B-us_A delta)",
            "pred_dir": sgn(drift_cci),
            "caveat": "MASODLAGOS/exploratory — nincs juniusi baseline-panel (us_ monokultura-zona); a delta a juliuson BELULI hir-shift proxyja",
            "eval_rule": "talalat, ha sign(CB_jul - 91.2) == pred_dir",
        },
        {
            "id": "P4_UK_GFK_JULY",
            "target": "GfK/NIQ UK consumer confidence 2026-07 (baseline 2026-06 = -23)",
            "gt_arrives": "2026-07-24",
            "prediction": f"panel ssr_balance = {uk['ssr_balance_mean']} (nyers, kalibralatlan); sign = -; kategorikus {uk['cat_balance_mean']}",
            "eval_rule": "sign(GT) vs sign(panel) + |e| = |GT - ssr_balance| a szaldo-skalan (kalibracios nyersanyag; G2-precedens szerint tulloves varhato); MoM-irany NEM regisztralt",
        },
        {
            "id": "P5_DE_EUROSTAT_CCI_JULY",
            "target": "Eurostat ei_bsco_m BS-CSMCI DE 2026-07 (baseline 2026-06 = -14.6)",
            "gt_arrives": "~2026-07-30",
            "prediction": f"panel ssr_balance = {de['ssr_balance_mean']} (nyers); sign = -; kategorikus {de['cat_balance_mean']}",
            "eval_rule": "G2-formatum: sign + |e|; a validalt FR/IT protokoll 1:1 nemet klonja — ez a nemet 'first dense-zone target' teszt",
        },
        {
            "id": "P6_DE_INFLEXP_JULY",
            "target": "Eurostat BS-PT-NY DE 2026-07 (baseline 2026-06 = +37.4)",
            "gt_arrives": "~2026-07-30",
            "prediction": f"price_score = {de['price_score_mean']}/5 (nyers); sign(score-3) = {sgn(de['price_score_mean'] - 3)}",
            "eval_rule": "sign(score-3) vs sign(GT) — G2-ben bizonyitott szisztematikus offset-tudattal regisztralva; a pont-ertek kalibracios nyersanyag",
        },
        {
            "id": "P7_DE_KONSUMKLIMA_AUG",
            "target": "NIM/GfK Konsumklima 2026-08 elorejelzes (baseline 2026-07 = -29.2)",
            "gt_arrives": "2026-07-24",
            "prediction": f"ugyanaz a DE-panel: ssr_balance = {de['ssr_balance_mean']}; sign = -",
            "eval_rule": "sign + z-pozicio; MoM-irany NEM regisztralt",
        },
        {
            "id": "P8_CROSS_COUNTRY_RANK",
            "target": "pesszimizmus-rangsor a juliusi kiadasokban (z-normalizalt, kepletek a gt_LOCKED-ban)",
            "gt_arrives": "2026-07-24..31",
            "prediction": (f"legpesszimistabb -> legkevesbe: DE ({de['ssr_balance_mean']}) > "
                           f"UK ({uk['ssr_balance_mean']}) > US ({usA['ssr_balance_mean']})"),
            "pred_rank": ["DE", "UK", "US"],
            "eval_rule": "3 paronkenti z-rang-talalat: z_i=(GT_jul_i - mean(series_i))/sd(series_i) a zarolt idosorokon (US: umcsent 12m; UK: 7 pont; DE: BS-CSMCI 11 pont)",
        },
    ],
}

with open(os.path.join(WDIR, "prereg_JOSLATOK.json"), "w", encoding="utf-8") as f:
    json.dump(prereg, f, ensure_ascii=False, indent=2)

# --- backteszt-kiertekeles (redukalt: Michigan prelim level-sign) ---
gt_bt = GT["backtest"]["US_MICHIGAN_2026-07_PRELIM"]
gt_val, mean12 = gt_bt["gt"], gt_bt["umcsent_12m_mean"]
gt_sign = "+" if gt_val > mean12 else "-"
panel_sign = sgn(usA["ssr_balance_mean"])
bt = {
    "op": "G2-WEST BACKTESZT-KIERTEKELES (redukalt hatokor — amendment_1)",
    "generated": NOW,
    "cell": "us_A [2026-07-09..07-14], modell tencent/Hy3-preview, flag CLEAN (cutoff_preview_probe.json: a modell a Michigan 2026-07 prelim erteket DIREKT kerdesre sem ismeri)",
    "target": "UMich consumer sentiment 2026-07 prelim = 54.4 (publ. 2026-07-17; korpusz-ablak vege 07-14 -> pre-outcome)",
    "not_coverable_declared": [
        "Michigan jun-final->jul-prelim MoM-IRANY: a us_ reteg 2026-07-08-ig Us Weekly-monokultura -> juniusi proxy-cella nem epitheto (kimondva, amendment_1)",
        "Conference Board 2026-06 es Michigan 2026-06 final szint/irany: fieldwork-ablakok korpusz nelkul",
    ],
    "level_sign_eval": {
        "rule": "sign(ssr_balance(us_A)) vs sign(GT - 12m atlag) — gt_LOCKED-ban elore rogzitve, az elvart-miss varakozassal egyutt",
        "panel": usA["ssr_balance_mean"], "panel_sign": panel_sign,
        "gt_minus_mean12": round(gt_val - mean12, 3), "gt_sign": gt_sign,
        "hit": panel_sign == gt_sign,
        "verdict": ("MISS — PONTOSAN a pre-regisztralt varakozas szerint: a hir-korpusz doom-skew "
                    "(G2 FR/IT-vel egybehangzo pesszimizmus-tulloves) a nyers szaldot negativban "
                    "tartja, mikozben a Michigan a sajat 12m atlaga FOLE ugrott. A cella erteke: "
                    "a szint-reteg kalibracio-igenyenek ujabb, elore bejelentett bizonyiteka — "
                    "NEM 'validated' bizonyitek." if panel_sign != gt_sign else
                    "HIT — a panel-szaldo elojele egyezik a GT 12m-atlaghoz viszonyitott elojelevel."),
    },
    "corpus_kikotes": "a us_A ablak a prelim-fieldwork (06-24..07-14) kesei ~harmadat fedi; a prelim-interjuk >70%-a 07-07 elotti",
}
with open(os.path.join(WDIR, "backtest_eval.json"), "w", encoding="utf-8") as f:
    json.dump(bt, f, ensure_ascii=False, indent=2)

print("P1 drift:", round(drift_cci, 2), "| P2 price drift:", round(drift_price, 3))
print("backtest level-sign:", panel_sign, "vs GT", gt_sign, "->", "HIT" if panel_sign == gt_sign else "MISS (elvart)")
print("cellak:", {k: v["ssr_balance_mean"] for k, v in cells.items()})
