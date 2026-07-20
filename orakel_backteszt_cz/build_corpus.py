#!/usr/bin/env python3
"""Összefűzi a forrás-alügynökök (WebSearch, vak, poll-szűrt) korpusz-darabjait
(parts/*.json) egyetlen corpus.jsonl-é. Védőháló: ÚJRA lefuttatja a poll-szűrőt és
az időablak-ellenőrzést, dedupol URL szerint, validál, és lean-balanszt összegez."""
import json, glob, os, re
from datetime import datetime
from config import POLL_BLOCKLIST, WINDOW_START, WINDOW_END

here = os.path.dirname(__file__)
parts_dir = os.path.join(here, "parts")

_poll_re = re.compile("|".join(re.escape(p) for p in POLL_BLOCKLIST), re.IGNORECASE)
# konkrét cseh párt + százalék minta (pl. "ANO 30 %", "Spolu 21 procent")
_num_re = re.compile(r"(ano|spolu|stan|pirát|pirat|spd|motorist|stačilo|stacilo)\D{0,12}\d{2}\s*(%|procent)", re.IGNORECASE)


def is_poll(title, lead):
    text = f"{title} {lead}"
    return bool(_poll_re.search(text) or _num_re.search(text))


def in_window(date_str):
    try:
        d = datetime.fromisoformat(str(date_str)[:10])
        return datetime.fromisoformat(WINDOW_START) <= d < datetime.fromisoformat(WINDOW_END)
    except Exception:
        return False  # dátum nélkül nem fogadjuk el (szigorú ablak)


rows, seen = [], set()
dropped = {"dup": 0, "field": 0, "lead": 0, "poll": 0, "window": 0}
for path in sorted(glob.glob(os.path.join(parts_dir, "*.json"))):
    with open(path, encoding="utf-8") as f:
        try:
            arr = json.load(f)
        except Exception as e:
            print(f"[!] {os.path.basename(path)}: JSON hiba — {e}")
            continue
    for r in arr:
        u = r.get("url", "")
        if u in seen:
            dropped["dup"] += 1; continue
        if not (r.get("title") and r.get("source") and r.get("lean") and u):
            dropped["field"] += 1; continue
        if len(r.get("lead", "")) < 30:
            dropped["lead"] += 1; continue
        if is_poll(r.get("title", ""), r.get("lead", "")):
            dropped["poll"] += 1; continue
        if not in_window(r.get("date", "")):
            dropped["window"] += 1; continue
        seen.add(u)
        rows.append({"title": r["title"], "lead": r["lead"], "source": r["source"],
                     "lean": r["lean"], "date": r["date"], "url": u})

with open(os.path.join(here, "corpus.jsonl"), "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

by_lean, by_src = {}, {}
for r in rows:
    by_lean[r["lean"]] = by_lean.get(r["lean"], 0) + 1
    by_src[r["source"]] = by_src.get(r["source"], 0) + 1
print(f"ÖSSZESEN: {len(rows)} cikk -> corpus.jsonl")
print(f"Kiszűrve: {dropped}")
print("Forrásonként:", json.dumps(by_src, ensure_ascii=False))
print("Lean-balansz:")
for lean, n in sorted(by_lean.items()):
    print(f"  {lean:10s}: {n:3d} ({100*n/max(1,len(rows)):.0f}%)")
