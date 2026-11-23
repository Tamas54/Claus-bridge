#!/usr/bin/env python3
"""A HU kurált próba kiértékelése a PREREG_HU_KURALT.json `osszevetes` szabályai szerint → eredmeny_hu_kuralt.json."""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
G = json.load(open(os.path.join(HERE, "gt_LOCKED_cci.json"), encoding="utf-8"))["values"]["HU"]
HON = ["2026-07", "2026-08", "2026-09", "2026-10"]
PREV = {"2026-07": "2026-06", "2026-08": "2026-07", "2026-09": "2026-08", "2026-10": "2026-09"}
SORS = {"regi": "cellak", "B": "cellak_hu_kuralt_B", "A": "cellak_hu_kuralt_A"}


def load(d):
    out = {}
    for m in HON:
        p = os.path.join(HERE, d, f"v41_hu_{m}.json")
        if os.path.exists(p):
            x = json.load(open(p, encoding="utf-8"))
            vals = [s["ssr_balance"] for s in x["per_seed"]]
            mu = sum(vals) / len(vals)
            se = (sum((v - mu) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5 / math.sqrt(len(vals))
            sl = {}
            for s in x["per_seed"]:
                for k, v in (s.get("szelet_szaldo") or {}).items():
                    sl.setdefault(k, []).append(v)
            out[m] = {"P": mu, "se": se, "kat": x.get("cat_mean"), "cost": x["cost_usd_this_run"],
                      "szelet": {k: round(sum(v) / len(v), 1) for k, v in sl.items()}}
    return out


def sgn(x, flat=1e-9):
    return 0 if abs(x) < flat else (1 if x > 0 else -1)


def main():
    res = {"gt": {m: G[m] for m in HON}, "sorok": {}}
    for name, d in SORS.items():
        rows = load(d)
        if len(rows) < len(HON):
            res["sorok"][name] = {"hianyos": sorted(rows)}
            continue
        off = sum(G[m] - rows[m]["P"] for m in HON) / len(HON)
        mae = sum(abs(rows[m]["P"] - G[m]) for m in HON) / len(HON)
        corr = {}
        for i, m in enumerate(HON[1:], start=1):
            o = sum(G[k] - rows[k]["P"] for k in HON[:i]) / i
            corr[m] = round(rows[m]["P"] + o - G[m], 2)
        irany = {}
        for m in ("2026-08", "2026-10"):
            dP, dG = rows[m]["P"] - rows[PREV[m]]["P"], G[m] - G[PREV[m]]
            irany[m] = {"dP": round(dP, 2), "dG": round(dG, 2), "talalat": sgn(dP) == sgn(dG, 0.5)}
        res["sorok"][name] = {
            "panel": {m: round(rows[m]["P"], 2) for m in HON}, "se": {m: round(rows[m]["se"], 2) for m in HON},
            "kat": {m: None if rows[m]["kat"] is None else round(rows[m]["kat"], 1) for m in HON},
            "szelet": {m: rows[m]["szelet"] for m in HON}, "eltolas": round(off, 2), "nyers_MAE": round(mae, 2),
            "korrigalt_hiba_08_10": corr,
            "korrigalt_MAE_08_10": round(sum(abs(v) for v in corr.values()) / len(corr), 2),
            "irany_08_10": irany, "koltseg": round(sum(rows[m]["cost"] for m in HON), 4)}
    naive = {m: round(G[PREV[m]] - G[m], 2) for m in HON[1:]}
    res["naiv"] = {"hiba_08_10": naive, "MAE_08_10": round(sum(abs(v) for v in naive.values()) / 3, 2)}
    s = res["sorok"]
    if "panel" in s.get("A", {}) and "panel" in s.get("regi", {}):
        a, r = s["A"], s["regi"]
        closer = all(abs(a["panel"][m] - G[m]) < abs(r["panel"][m] - G[m]) for m in HON)
        res["dontes"] = {"koveti": a["nyers_MAE"] <= 10 and closer, "A_minden_honapban_kozelebb": closer,
                         "eltolas_csokken": abs(a["eltolas"]) <= abs(r["eltolas"]) - 10}
        if "panel" in s.get("B", {}):
            res["dontes"]["korpusz_hozadeka"] = round(r["eltolas"] - s["B"]["eltolas"], 2)
            res["dontes"]["priming_hozadeka"] = round(s["B"]["eltolas"] - a["eltolas"], 2)
    json.dump(res, open(os.path.join(HERE, "eredmeny_hu_kuralt.json"), "w", encoding="utf-8"), ensure_ascii=False,
              indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
