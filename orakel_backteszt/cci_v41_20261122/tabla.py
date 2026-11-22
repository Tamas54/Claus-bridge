#!/usr/bin/env python3
"""eredmeny.json + proba_pontozas.json → a jegyzőkönyv cella-táblája (markdown, a képernyőre)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
E = json.load(open(os.path.join(HERE, "eredmeny.json"), encoding="utf-8"))
PR = json.load(open(os.path.join(HERE, "proba_pontozas.json"), encoding="utf-8"))["cellak"]
NEV = {"HU": "Magyarország", "FR": "Franciaország", "IT": "Olaszország", "DE": "Németország", "US": "USA (Michigan)"}


def f(x, d=1, plus=True):
    if x is None:
        return "—"
    s = f"{x:+.{d}f}" if plus else f"{x:.{d}f}"
    return s.replace(".", ",").replace("-", "−")


print("| Ország | Hónap | Tudta a modell? | Valós index | Valós változás | Panel (SSR-szaldó ± SE) | Panel-változás "
      "| Irány-találat | Szinthiba | Szinthiba eltolással |")
print("|---|---|---|---|---|---|---|---|---|---|")
for r in E["cellak"]:
    k = r["cella"]
    tud = "nem" if not PR[k]["tudta"] else "IGEN (kiesik)"
    if "dG" in r:
        irany = "lapos hónap" if r["lapos"] else ("igen" if r["talalat"] else "nem")
        dg, dp = f(r["dG"]), f(r["dP"])
    else:
        irany, dg, dp = "bázis", "—", "—"
    us = r["orszag"] == "US"
    hiba = "más skála" if us else f(r["szinthiba"])
    korr = f(r.get("korrigalt_hiba")) if "korrigalt_hiba" in r else "—"
    if us and korr != "—":
        korr += " (tájékoztató)"
    print(f"| {NEV[r['orszag']]} | {r['honap']} | {tud} | {f(r['gt'], plus=not us)} | {dg} | "
          f"{f(r['panel'])} ± {f(r['se'], plus=False)} | {dp} | {irany} | {hiba} | {korr} |")
