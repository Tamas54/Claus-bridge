#!/usr/bin/env python3
"""
ORAKEL-II BACKTESZT — 1. FOGASKERÉK: KORPUSZ-GYŰJTŐ
====================================================
Vak, eredmény-FÜGGETLEN mintavétel a választás ELŐTTI hír-korpuszhoz.

A NÉGY SZABÁLY (mind vak a végeredményre):
  1. FORRÁS-VEZÉRELT  — a 9 lap domainjére keresünk, nem témákra.
  2. IDŐBEN EGYENLETES — jan/feb/márc arányosan (progresszív lefedés).
  3. LEAN-ARÁNYOS      — a reach_weight szerinti kvóta, nem fele-fele.
  4. POLL-SZŰRŐ        — konkrét pártszámot tartalmazó cikk KIZÁRVA.

A scrape a Brave Search API-n megy (a meglévő Bravebrowser-infra logikája).
Cím + lead kell, NEM teljes szöveg — token-takarékos, framing-elég.

HASZNÁLAT:
  export BRAVE_API_KEY="..."      # vagy a --provider web (lásd alább)
  python3 collect_corpus.py
  -> kimenet: corpus.jsonl  (egy sor = egy cikk: {title, lead, source, lean, date, url})
"""

import os
import re
import sys
import json
import time
import math
import urllib.parse
import urllib.request
from datetime import datetime

from config import (
    SOURCES, MONTHS, WINDOW_START, WINDOW_END,
    TARGET_CORPUS_SIZE, POLL_BLOCKLIST,
)

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

# ── POLL-SZŰRŐ ───────────────────────────────────────────────────────────
_poll_re = re.compile("|".join(re.escape(p) for p in POLL_BLOCKLIST), re.IGNORECASE)

def is_poll_article(title: str, lead: str) -> bool:
    """True, ha a cikk konkrét pártszámot / előrejelzést tartalmaz -> KIZÁRVA."""
    text = f"{title} {lead}"
    if _poll_re.search(text):
        return True
    # számszerű párt-arány minta: "Fidesz 42", "Tisza 37%", "40-28"
    if re.search(r"(fidesz|tisza|mi hazánk|dk)\D{0,12}\d{2}\s*(%|százal|szazal)", text, re.IGNORECASE):
        return True
    return False


def in_window(date_str: str) -> bool:
    """True, ha a cikk a [WINDOW_START, WINDOW_END) ablakban van."""
    try:
        d = datetime.fromisoformat(date_str[:10])
        return datetime.fromisoformat(WINDOW_START) <= d < datetime.fromisoformat(WINDOW_END)
    except Exception:
        return False


# ── KVÓTA-SZÁMÍTÁS (lean-arányos, forrásonként, hónaponként) ─────────────
def compute_quotas():
    """
    Visszaadja: {source_name: {month: db}} — a reach_weight-arányos,
    hónapokra egyenletesen elosztott cél-darabszám.
    """
    total_weight = sum(s["reach_weight"] for s in SOURCES)
    quotas = {}
    for s in SOURCES:
        share = s["reach_weight"] / total_weight
        per_source = round(TARGET_CORPUS_SIZE * share)
        per_month = max(1, round(per_source / len(MONTHS)))
        quotas[s["name"]] = {m: per_month for m in MONTHS}
    return quotas


# ── BRAVE SEARCH HÍVÁS ───────────────────────────────────────────────────
def brave_search(query: str, count: int = 20, offset: int = 0):
    if not BRAVE_API_KEY:
        raise RuntimeError(
            "Nincs BRAVE_API_KEY. Állítsd be: export BRAVE_API_KEY='...'\n"
            "(Vagy futtasd a gépet a Bridge/Bravebrowser MCP-n keresztül — lásd README.)"
        )
    params = urllib.parse.urlencode({
        "q": query,
        "count": count,
        "offset": offset,
        "country": "hu",
        "search_lang": "hu",
        "spellcheck": 0,
    })
    req = urllib.request.Request(
        f"{BRAVE_ENDPOINT}?{params}",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": BRAVE_API_KEY,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_results(raw):
    """Brave web-találatok -> [{title, lead, url, date}]"""
    out = []
    for r in raw.get("web", {}).get("results", []):
        title = r.get("title", "").strip()
        lead = (r.get("description") or "").strip()
        url = r.get("url", "")
        # dátum: Brave 'page_age' vagy 'age' mezőből, vagy URL-ből (/2026/02/...)
        date = ""
        age = r.get("page_age") or r.get("age") or ""
        if age:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", str(age))
            if m:
                date = m.group(1)
        if not date:
            m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
            if m:
                date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        out.append({"title": title, "lead": lead, "url": url, "date": date})
    return out


# ── FŐ GYŰJTŐ-CIKLUS ─────────────────────────────────────────────────────
def collect():
    quotas = compute_quotas()
    corpus = []
    seen_urls = set()

    print("ORAKEL-II korpusz-gyűjtés indul")
    print(f"  Cél: ~{TARGET_CORPUS_SIZE} cikk | ablak: {WINDOW_START} .. {WINDOW_END}")
    print(f"  Lapok: {len(SOURCES)} | hónapok: {MONTHS}\n")

    for s in SOURCES:
        name, domain, lean = s["name"], s["domain"], s["lean"]
        for month in MONTHS:
            need = quotas[name][month]
            got = 0
            # Forrás-vezérelt, TÉMA-FÜGGETLEN lekérdezés:
            # a lap politikai rovata + a hónap, kulcsszó-diktálás NÉLKÜL.
            # A 'választás 2026' csak időbeli/tematikus horgony, nem botrány-szűrő.
            query = f"site:{domain} belföld politika {month} választás 2026"
            offset = 0
            attempts = 0
            while got < need and attempts < 5:
                attempts += 1
                try:
                    raw = brave_search(query, count=20, offset=offset)
                except Exception as e:
                    print(f"  [!] {name}/{month}: hiba — {e}")
                    break
                results = extract_results(raw)
                if not results:
                    break
                for r in results:
                    if r["url"] in seen_urls:
                        continue
                    # SZŰRŐK:
                    if r["date"] and not in_window(r["date"]):
                        continue                       # időablakon kívül
                    if is_poll_article(r["title"], r["lead"]):
                        continue                       # poll-cikk -> KIZÁRVA
                    if len(r["lead"]) < 30:
                        continue                       # üres/töredék lead
                    corpus.append({
                        "title": r["title"],
                        "lead": r["lead"],
                        "source": name,
                        "lean": lean,
                        "date": r["date"] or month,
                        "url": r["url"],
                    })
                    seen_urls.add(r["url"])
                    got += 1
                    if got >= need:
                        break
                offset += 20
                time.sleep(1.1)   # Brave rate-limit barát
            print(f"  {name:16s} {month}: {got:3d}/{need:3d}")

    # ── KIMENET ──
    with open("corpus.jsonl", "w", encoding="utf-8") as f:
        for row in corpus:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ── ÖSSZESÍTŐ + BALANSZ-ELLENŐRZÉS ──
    by_lean = {}
    for row in corpus:
        by_lean[row["lean"]] = by_lean.get(row["lean"], 0) + 1
    print(f"\n  ÖSSZESEN: {len(corpus)} cikk -> corpus.jsonl")
    print("  Lean-eloszlás (balansz-ellenőrzés):")
    for lean, n in sorted(by_lean.items()):
        pct = 100 * n / max(1, len(corpus))
        print(f"    {lean:12s}: {n:3d}  ({pct:.0f}%)")
    print("\n  -> Ha a gov/opposition arány durván a reach-súlyokat tükrözi, a korpusz tiszta.")
    return corpus


if __name__ == "__main__":
    collect()
