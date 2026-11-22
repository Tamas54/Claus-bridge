#!/usr/bin/env python3
"""CCI-backteszt V4.1-Flash — kiértékelő (a PREREG.json `kiertekeles` szabályai, AI nélkül).

Bemenet: cellak/v41_<cc>_<hónap>.json, gt_LOCKED_cci.json, proba_pontozas.json (a TUDTA-cellák kiesnek).
  python3 evaluate.py   → eredmeny.json
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
GT = json.load(open(os.path.join(HERE, "gt_LOCKED_cci.json"), encoding="utf-8"))
G, FLAT = GT["values"], GT["flat_threshold"]
EU = ["HU", "FR", "IT", "DE"]
ALL = EU + ["US"]
MONTHS = ["2026-07", "2026-08", "2026-09", "2026-10"]


def prev(m):
    y, mm = map(int, m.split("-"))
    return f"{y - 1}-12" if mm == 1 else f"{y}-{mm - 1:02d}"


def sgn(x, flat=0.0):
    return 0 if abs(x) < flat else (1 if x > 0 else -1)


def rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = rank(a), rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else None


def binom_p(k, n):
    """Egyoldali P(X >= k), X ~ Bin(n, 0.5)."""
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n if n else None


def main():
    probe = json.load(open(os.path.join(HERE, "proba_pontozas.json"), encoding="utf-8"))["cellak"]
    excluded = {k for k, c in probe.items() if c["tudta"]}
    P, SE, CAT = {}, {}, {}
    for c in ALL:
        for m in MONTHS:
            k = f"{c}_{m}"
            fp = os.path.join(HERE, "cellak", f"v41_{c.lower()}_{m}.json")
            if k in excluded or not os.path.exists(fp):
                continue
            d = json.load(open(fp, encoding="utf-8"))
            vals = [s["ssr_balance"] for s in d["per_seed"]]
            P[k] = sum(vals) / len(vals)
            sd = (sum((v - P[k]) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5 if len(vals) > 1 else 0.0
            SE[k] = sd / math.sqrt(len(vals))
            CAT[k] = d.get("cat_mean")

    rows, dir_rows = [], []
    for c in ALL:
        for m in MONTHS:
            k = f"{c}_{m}"
            if k not in P:
                continue
            g = G[c][m]
            row = {"cella": k, "orszag": c, "honap": m, "gt": g, "panel": round(P[k], 2), "se": round(SE[k], 2),
                   "kat": None if CAT[k] is None else round(CAT[k], 1), "szinthiba": round(P[k] - g, 2)}
            kp = f"{c}_{prev(m)}"
            if m != MONTHS[0] and kp in P:
                dG = round(g - G[c][prev(m)], 2)
                dP = P[k] - P[kp]
                se_d = math.sqrt(SE[k] ** 2 + SE[kp] ** 2)
                dC = (CAT[k] - CAT[kp]) if (CAT[k] is not None and CAT[kp] is not None) else None
                pers_prev = G[c][prev(m)] - G[c][prev(prev(m))]
                row.update({"dG": dG, "dP": round(dP, 2), "se_dP": round(se_d, 2), "lapos": abs(dG) < FLAT,
                            "irany_G": sgn(dG, FLAT), "irany_P": sgn(dP), "talalat": sgn(dP) == sgn(dG, FLAT),
                            "zajon_tul": abs(dP) > 2 * se_d,
                            "dKat": None if dC is None else round(dC, 1),
                            "talalat_kat": None if dC is None else (sgn(dC) == sgn(dG, FLAT)),
                            "folytatodik_josol": sgn(pers_prev, FLAT),
                            "folytatodik_talalat": sgn(pers_prev, FLAT) == sgn(dG, FLAT)})
                dir_rows.append(row)
            rows.append(row)

    def hit_rate(rs, key):
        rs = [r for r in rs if not r["lapos"] and r.get(key) is not None]
        k = sum(1 for r in rs if r[key])
        return {"talalat": k, "n": len(rs), "arany": round(k / len(rs), 3) if rs else None,
                "p_egyoldali": None if not rs else round(binom_p(k, len(rs)), 4)}

    flat_rows = [r for r in dir_rows if r["lapos"]]
    a = {"panel_ssr": hit_rate(dir_rows, "talalat"), "panel_kategorikus": hit_rate(dir_rows, "talalat_kat"),
         "alapvonal_folytatodik": hit_rate(dir_rows, "folytatodik_talalat"),
         "alapvonal_nincs_valtozas": {"talalat": 0, "n": hit_rate(dir_rows, "talalat")["n"],
                                      "megjegyzes": "definíció szerint 0 a nem-lapos cellákon"},
         "lapos_cellak": [{"cella": r["cella"], "dG": r["dG"], "dP": r["dP"]} for r in flat_rows],
         "eu_csak": hit_rate([r for r in dir_rows if r["orszag"] in EU], "talalat"),
         "zajon_tuli_valtozasok": sum(1 for r in dir_rows if r["zajon_tul"])}

    # (b) szint
    eu_rows = [r for r in rows if r["orszag"] in EU]
    raw_mae = sum(abs(r["szinthiba"]) for r in eu_rows) / len(eu_rows) if eu_rows else None
    corr = []
    for c in ALL:
        crow = [r for r in rows if r["orszag"] == c]
        for i, r in enumerate(crow):
            if i == 0:
                continue
            off = sum(x["gt"] - x["panel"] for x in crow[:i]) / i
            r["offset"] = round(off, 2)
            r["korrigalt_hiba"] = round(r["panel"] + off - r["gt"], 2)
            r["nincs_valtozas_hiba"] = round(G[c][prev(r["honap"])] - r["gt"], 2)
            corr.append(r)
    eu_corr = [r for r in corr if r["orszag"] in EU]
    us_corr = [r for r in corr if r["orszag"] == "US"]

    def mae(rs, key):
        return round(sum(abs(r[key]) for r in rs) / len(rs), 2) if rs else None
    b = {"eu_nyers_MAE_16": None if raw_mae is None else round(raw_mae, 2),
         "eu_nyers_atlagos_eltolas": round(sum(r["szinthiba"] for r in eu_rows) / len(eu_rows), 2) if eu_rows else None,
         "eu_08_10_nyers_MAE": mae(eu_corr, "szinthiba"), "eu_08_10_korrigalt_MAE": mae(eu_corr, "korrigalt_hiba"),
         "eu_08_10_nincs_valtozas_MAE": mae(eu_corr, "nincs_valtozas_hiba"),
         "us_tajekoztato": {"korrigalt_MAE": mae(us_corr, "korrigalt_hiba"),
                            "nincs_valtozas_MAE": mae(us_corr, "nincs_valtozas_hiba"),
                            "megjegyzes": "a Michigan-index indexpont, a panel szaldó — más skála"},
         "orszagonkenti_eltolas": {c: round(sum(r["gt"] - r["panel"] for r in rows if r["orszag"] == c) /
                                            max(1, sum(1 for r in rows if r["orszag"] == c)), 2) for c in ALL}}

    # (c) sorrend
    c1 = spearman([r["panel"] for r in eu_rows], [r["gt"] for r in eu_rows])
    per_month = {}
    for m in MONTHS:
        rs = [r for r in eu_rows if r["honap"] == m]
        per_month[m] = spearman([r["panel"] for r in rs], [r["gt"] for r in rs])
    pm_vals = [v for v in per_month.values() if v is not None]
    eu_dir = [r for r in dir_rows if r["orszag"] in EU]
    c3 = spearman([r["dP"] for r in eu_dir], [r["dG"] for r in eu_dir])
    c3_all = spearman([r["dP"] for r in dir_rows], [r["dG"] for r in dir_rows])
    per_country = {c: spearman([r["panel"] for r in rows if r["orszag"] == c],
                               [r["gt"] for r in rows if r["orszag"] == c]) for c in ALL}
    c = {"c1_eu_szint_16": c1, "c2_havi_orszagsorrend": per_month,
         "c2_atlag": round(sum(pm_vals) / len(pm_vals), 3) if pm_vals else None,
         "c3_valtozas_eu_12": c3, "c3_valtozas_mind_15": c3_all, "orszagon_beluli_4_honap": per_country}

    out = {"kizart_cellak": sorted(excluded), "cellak": rows, "a_irany": a, "b_szint": b, "c_sorrend": c}
    json.dump(out, open(os.path.join(HERE, "eredmeny.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ("a_irany", "b_szint", "c_sorrend")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
