#!/usr/bin/env python3
"""A kurált HU-korpusz auditja (téma × szelet × forrás) → kuralt_audit.md + sha256-zár (korpusz_kuralt_LOCKED.json)."""
import hashlib
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
HON = ["2026-07", "2026-08", "2026-09", "2026-10"]
SL = ["bal", "jobb", "kozmedia", "kozossegi", "kozos"]
SLN = {"bal": "bal", "jobb": "jobb", "kozmedia": "közmédia (pótlás)", "kozossegi": "közösségi", "kozos": "közös/alig"}
TO = ["politika", "gazdasag", "bulvar", "sport", "tech", "kulfold", "kozelet"]
TON = {"politika": "politika", "gazdasag": "gazdaság", "bulvar": "bulvár/életmód", "sport": "sport",
       "tech": "tech", "kulfold": "külföld", "kozelet": "közélet/helyi"}


def main():
    L = ["# KURÁLT HU-KORPUSZ — AUDIT (a futás ELŐTT)", "",
         "Szabályok: `PREREG_HU_KURALT.json`, építő: `build_hu_kuralt.py`. Cél: ~500 tétel/hónap; szeletek "
         "bal 130 · jobb 110 · közmédia 90 · közösségi 110 · közös 60; témák 25/20/15/12/8/10/10 %.", ""]
    lock = {}
    tot_topic = Counter()
    for m in HON:
        p = os.path.join(HERE, "korpusz_kuralt", f"corpus_hu_{m}.json")
        if not os.path.exists(p):
            L.append(f"## {m}: NINCS KORPUSZ\n")
            continue
        d = json.load(open(p, encoding="utf-8"))
        lock[os.path.basename(p)] = {"sha256": hashlib.sha256(open(p, "rb").read()).hexdigest(), "n": d["n"]}
        it = d["items"]
        L += [f"## {m} — ablak {d['corpus_window'][0]} → {d['corpus_window'][1]}, napok: "
              + ", ".join(x[5:] for x in d["days_sampled"]), "",
              f"n = **{d['n']}** · nyers {d['raw_rows']} · szűrés után {d['pool']} · szivárgás-szűrő −{d['leak_dropped']}"
              f" · határon túli −{d['abroad_dropped']} · MCP-hiba {d['mcp_errors']}", "",
              "| Szelet | cél | kapott | elérhető | " + " | ".join(TON[t] for t in TO) + " |",
              "|---|---|---|---|" + "---|" * len(TO)]
        for s in SL:
            a = d["szeletek"][s]
            c = Counter(x["topic"] for x in it if x["slice"] == s)
            L.append(f"| {SLN[s]} | {a['cel']} | {a['kapott']} | {a['elerheto']} | "
                     + " | ".join(str(c.get(t, 0)) for t in TO) + " |")
        ct = Counter(x["topic"] for x in it)
        tot_topic.update(ct)
        L.append("| **összesen** | 500 | " + str(len(it)) + " | — | "
                 + " | ".join(f"{ct.get(t, 0)} ({round(100 * ct.get(t, 0) / max(1, len(it)))}%)" for t in TO) + " |")
        L.append("")
        L.append("Források szeletenként (darab):")
        L.append("")
        for s in SL:
            cs = Counter(x["source"] for x in it if x["slice"] == s)
            L.append(f"- **{SLN[s]}** ({len(cs)} forrás): " + ", ".join(f"{k} {v}" for k, v in cs.most_common(14)))
        days = Counter(x["date"] for x in it)
        L.append("")
        L.append("Napok szerint: " + ", ".join(f"{k[5:]} {v}" for k, v in sorted(days.items())))
        L.append("")
    json.dump({"op": "kurált HU-korpusz zárolás (a futás ELŐTT)", "fajlok": lock},
              open(os.path.join(HERE, "korpusz_kuralt_LOCKED.json"), "w", encoding="utf-8"), ensure_ascii=False,
              indent=1)
    open(os.path.join(HERE, "kuralt_audit.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
