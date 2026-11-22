#!/usr/bin/env python3
"""CCI-backteszt V4.1-Flash — korpusz-lefedettség országonként 2026-07…11 (a júliusi korpusz_audit.md mintája).

A get_news napi „pool" mezője = az adott nap, adott ország/nyelv cikkeiből lekért sorok száma, legfeljebb 500
(a get_news felső korlátja). 500 = legalább 500 cikk aznap (sűrű nap); kevesebb = a nap teljes cikkszáma.
  python3 korpusz_audit.py  → korpusz_audit.md
"""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ORSZ = ["HU", "FR", "IT", "DE", "US"]
HON = ["2026-07", "2026-08", "2026-09", "2026-10", "2026-11"]


def main():
    lines = ["# KORPUSZ-AUDIT — CCI-backteszt V4.1-Flash (2026-11-23)", "",
             "Forrás: Echolot MCP `get_news` (nyilvános, csak olvas), a cikk `published_at` napja szerint (as_of), "
             "`place=<ország>` + nyelv. Ablak: EU [M−1 hó 15., M hó 14.], US [M−1 hó 8., M hó 7.], minden 2. nap.",
             "A napi pool felső korlátja 500 (a get_news határa): az 500 sűrű napot jelent.", "",
             "| Ország | Cél-hónap | Ablak | Napok | Napi pool (min–átlag–max) | Nyers sor | Forrás | Szivárgás-szűrő | "
             "Korpusz n | Lean (n=60) | Fő kategóriák (nyers) |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in ORSZ:
        for m in HON:
            p = os.path.join(HERE, "korpusz", f"corpus_{c.lower()}_{m}.json")
            if not os.path.exists(p):
                lines.append(f"| {c} | {m} | — | — | — | — | — | — | **NINCS** | — | — |")
                continue
            d = json.load(open(p, encoding="utf-8"))
            pools = [v.get("pool") or 0 for v in d["per_day"].values()]
            cats = sorted(d["raw_cat_dist"].items(), key=lambda kv: -kv[1])[:3]
            tot = sum(d["raw_cat_dist"].values()) or 1
            lean = ", ".join(f"{k} {v}" for k, v in sorted(d["lean_dist"].items(), key=lambda kv: -kv[1]))
            err = f" (hibás nap: {d['mcp_errors']})" if d["mcp_errors"] else ""
            lines.append(
                f"| {c} | {m} | {d['corpus_window'][0][5:]}→{d['corpus_window'][1][5:]} | {len(pools)}{err} | "
                f"{min(pools)}–{round(sum(pools) / len(pools))}–{max(pools)} | {d['raw_rows']} | "
                f"{d['distinct_sources_raw']} | −{d['leak_dropped']} | {d['n']} | {lean} | "
                + ", ".join(f"{k} {round(100 * v / tot)}%" for k, v in cats) + " |")
    open(os.path.join(HERE, "korpusz_audit.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    lock = {}
    kd = os.path.join(HERE, "korpusz")
    for fn in sorted(os.listdir(kd)):
        d = json.load(open(os.path.join(kd, fn), encoding="utf-8"))
        lock[fn] = {"sha256": hashlib.sha256(open(os.path.join(kd, fn), "rb").read()).hexdigest(), "n": d["n"],
                    "window": d["corpus_window"], "raw_rows": d["raw_rows"], "pool": d["pool_after_dedupe"],
                    "leak_dropped": d["leak_dropped"], "distinct_sources_raw": d["distinct_sources_raw"],
                    "mcp_errors": d["mcp_errors"]}
    json.dump({"op": "CCI-backteszt V4.1-Flash — KORPUSZ-ZÁROLÁS (a panel ELŐTT)", "fajlok": lock},
              open(os.path.join(HERE, "korpusz_LOCKED.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
