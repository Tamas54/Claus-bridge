#!/usr/bin/env python3
"""CCI-backteszt V4.1-Flash — a célzott cutoff-próba determinisztikus pontozója (PREREG.json `proba.pontozas`).

  python3 grade_proba.py   → proba_pontozas.json + a tiszta cellák listája a képernyőre
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PREREG = json.load(open(os.path.join(HERE, "PREREG.json"), encoding="utf-8"))
GT = json.load(open(os.path.join(HERE, "gt_LOCKED_cci.json"), encoding="utf-8"))
TOL = PREREG["proba"]["turesek"]

NUM = re.compile(r"(?<![\d.,])(?:(mínusz|minus)\s*)?([−\-–]?)\s?(\d{1,3}(?:[.,]\d+)?)(?![\d])", re.I)
UP = re.compile(r"javult|emelked|nőtt|növeked|improv|\brose\b|\brise\b|increas|\bup\b|higher|gained|rebound|\bbetter\b|kedvezőbb", re.I)
DOWN = re.compile(r"romlott|csökken|esett|visszaes|mérsékl|worsen|\bfell\b|\bfall\b|declin|decreas|dropp|lower|"
                  r"deteriorat|weaken|\bworse\b|rosszabb|kedvezőtlenebb", re.I)
FLAT = re.compile(r"változatlan|stagnál|unchanged|\bflat\b|stable|steady|no change", re.I)
DONT = re.compile(r"^\W*(NEM TUDOM|I DON.?T KNOW)", re.I)


def parse_value(txt, us):
    lo, hi = (30, 120) if us else (-60, 30)
    for m in NUM.finditer(txt):
        word, sign, num = m.groups()
        v = float(num.replace(",", "."))
        if 1900 <= v <= 2100:
            continue
        if word or sign:
            v = -v
        if lo <= v <= hi:
            return v
    return None


def parse_dir(txt):
    up, down, flat = bool(UP.search(txt)), bool(DOWN.search(txt)), bool(FLAT.search(txt))
    if up and down:
        return None
    if up:
        return 1
    if down:
        return -1
    if flat:
        return 0
    return None


def main():
    resp = json.load(open(os.path.join(HERE, "proba_valaszok.json"), encoding="utf-8"))
    rows = []
    for r in resp["valaszok"]:
        c, m = r["orszag"], r["honap"]
        us = c == "US"
        txt = r.get("answer") or ""
        g = GT["values"][c].get(m)
        gd = (GT["direction"].get(f"{c}_{m}") or {}).get("irany")
        dont = bool(DONT.search(txt)) or not txt
        v = None if dont else parse_value(txt, us)
        d = None if dont else parse_dir(txt)
        tol = TOL["US_michigan_indexpont"] if us else TOL["EU_balance_pont"]
        vhit = (v is not None and g is not None and abs(v - g) <= tol)
        dhit = (d is not None and gd is not None and d == gd)
        rows.append({"id": r["id"], "orszag": c, "honap": m, "nyelv": r["nyelv"], "tipus": r["tipus"],
                     "nem_tudja": dont, "ertek": v, "gt": g, "irany": d, "gt_irany": gd,
                     "ertek_talalat": vhit, "irany_talalat": dhit, "valasz": txt})
    cells = {}
    for x in rows:
        k = f"{x['orszag']}_{x['honap']}"
        cl = cells.setdefault(k, {"orszag": x["orszag"], "honap": x["honap"], "tipus": x["tipus"], "valtozatok": []})
        cl["valtozatok"].append(x)
    for k, cl in cells.items():
        vs = cl["valtozatok"]
        cl["tudta"] = any(x["ertek_talalat"] and x["irany_talalat"] for x in vs)
        cl["gyanus"] = (not cl["tudta"]) and any(x["ertek_talalat"] for x in vs)
        cl["nem_tudja_mind"] = all(x["nem_tudja"] for x in vs)
        cl["irany_egyezes_szam_nelkul"] = any(x["irany_talalat"] and x["ertek"] is None for x in vs)
        cl["verdikt"] = "TUDTA (kiesik)" if cl["tudta"] else ("GYANÚS" if cl["gyanus"] else "tiszta")
    out = {"model": resp["model"], "cost_usd": resp["cost_usd"], "cellak": cells, "sorok": rows}
    json.dump(out, open(os.path.join(HERE, "proba_pontozas.json"), "w", encoding="utf-8"), ensure_ascii=False,
              indent=1)
    for k in sorted(cells, key=lambda k: (cells[k]["tipus"], k)):
        cl = cells[k]
        ans = " | ".join(f"{x['nyelv']}: {'NT' if x['nem_tudja'] else (x['ertek'], x['irany'])}"
                         for x in cl["valtozatok"])
        print(f"{cl['tipus']:<11} {k:<11} {cl['verdikt']:<15} GT={cl['valtozatok'][0]['gt']} {ans}")


if __name__ == "__main__":
    main()
