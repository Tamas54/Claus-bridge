#!/usr/bin/env python3
"""Összefűzi a 9 forrás-alügynök által gyűjtött (WebSearch, vak, poll-szűrt)
korpusz-darabokat egyetlen corpus.jsonl-é. A darabok külön JSON-fájlokban
(parts/*.json) ülnek; ez a script validál + JSONL-t ír + balansz-összegzést ad."""
import json, glob, os

parts_dir = os.path.join(os.path.dirname(__file__), "parts")
rows, seen = [], set()
for path in sorted(glob.glob(os.path.join(parts_dir, "*.json"))):
    with open(path, encoding="utf-8") as f:
        arr = json.load(f)
    for r in arr:
        u = r.get("url", "")
        if u in seen:
            continue
        seen.add(u)
        # minimál-validáció: kell title, lead(>=30), source, lean, date, url
        if not (r.get("title") and r.get("source") and r.get("lean") and r.get("url")):
            continue
        if len(r.get("lead", "")) < 30:
            continue
        rows.append({"title": r["title"], "lead": r["lead"], "source": r["source"],
                     "lean": r["lean"], "date": r.get("date", ""), "url": r["url"]})

with open(os.path.join(os.path.dirname(__file__), "corpus.jsonl"), "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

by_lean, by_src = {}, {}
for r in rows:
    by_lean[r["lean"]] = by_lean.get(r["lean"], 0) + 1
    by_src[r["source"]] = by_src.get(r["source"], 0) + 1
print(f"ÖSSZESEN: {len(rows)} cikk -> corpus.jsonl")
print("Forrásonként:", json.dumps(by_src, ensure_ascii=False))
print("Lean-balansz:", json.dumps(by_lean, ensure_ascii=False))
for lean, n in sorted(by_lean.items()):
    print(f"  {lean:12s}: {n:3d} ({100*n/max(1,len(rows)):.0f}%)")
