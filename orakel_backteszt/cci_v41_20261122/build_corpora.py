#!/usr/bin/env python3
"""CCI-backteszt V4.1-Flash (2026-11-22) — korpusz-építő az Echolot MCP-ből (as_of szerint).

A júliusi G2 korpusz-szabály (g2_hy3_20260720/corpus_*.json, prereg_west_20260721/build_corpora_west.py) klónja:
cím > 15 karakter, cím-dedupe, lead 220 karakter, lean-cap (domináns lean <= 60%), dátum-stride ritkítás,
MAXN = 60 egysoros hír-állítás cellánként.

Eltérés a júliustól (a prod-DB közvetlen olvasása helyett a nyilvános Echolot MCP, mert az éles DB azóta
Postgresen, a Hostmanen fut):
  - nyers merítés: get_news(date=nap, language, place=ország, limit=100, diversify, max_per_source=3) minden
    második napra az ablakban (a get_news a nap utolsó <=500 cikkéből válogat, forrásonként <=3);
  - place=<ország> szűrés minden országra (a júliusi DE-lelet: a nyelvi réteg osztrák/svájci forrást is hoz);
  - SZIVÁRGÁS-SZŰRŐ: minden fogyasztói-bizalmi/hangulati index-hír kiesik (lásd LEAK_RE), hogy a panel ne
    lásson indexszámot (a júliusi CCI_SYS is tiltja a külső index-tudást).

Ablak (fieldwork-igazítva, mint júliusban): EU-cél M -> [M-1 hó 15., M hó 14.]; az EU-felmérés terepmunkája a hónap
első 2-3 hete, a GT a hónap végén jelenik meg. USA (Michigan) -> [M-1 hó 8., M hó 7.]: a Michigan előzetes
értéke M hónap 2.-3. péntekén jelenik meg (>= 8.), így az ablak azt sem látja.

  python3 build_corpora.py            # mind az 5 ország x 5 hónap
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ECHOLOT = "https://echolotnews.com/mcp"
MAXN = 60
COUNTRIES = {"HU": "hu", "FR": "fr", "IT": "it", "DE": "de", "US": "en"}
TARGETS = ["2026-07", "2026-08", "2026-09", "2026-10", "2026-11"]

LEAK_RE = re.compile(
    r"consumer (sentiment|confidence)|sentiment index|university of michigan|conference board"
    r"|fogyasztói (bizalom|bizalmi|hangulat)|bizalmi index|konjunktúraindex|\bGKI\b"
    r"|confiance des (ménages|consommateurs)|moral des ménages"
    r"|fiducia (dei consumatori|delle famiglie)|clima di fiducia"
    r"|konsumklima|verbrauchervertrauen|konsumlaune|verbraucherstimmung|konsumbarometer|\bGfK\b",
    re.I)


def window(country, target):
    y, m = map(int, target.split("-"))
    prev = dt.date(y, m, 1) - dt.timedelta(days=1)
    if country == "US":
        return dt.date(prev.year, prev.month, 8), dt.date(y, m, 7)
    return dt.date(prev.year, prev.month, 15), dt.date(y, m, 14)


def mcp_get_news(date, lang, place):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_news", "arguments": {
                "date": date, "language": lang, "place": place, "limit": 100,
                "diversify_results": True, "max_per_source": 3, "max_per_sphere": 40}}}
    req = urllib.request.Request(ECHOLOT, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream",
                                          "User-Agent": "orakel-backteszt/1.0"})
    last = None
    for a in range(6):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read().decode())
            if "result" not in d:
                raise RuntimeError(str(d.get("error"))[:160])
            return json.loads(d["result"]["content"][0]["text"])
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * 2 ** a)
    return {"error": repr(last)[:200], "articles": []}


def norm_title(t):
    return re.sub(r"\W+", " ", (t or "").lower()).strip()


def sample_cell(rows):
    """A júliusi sample_cell (build_corpora_west.py) változatlan logikája."""
    seen, pool, leaks = set(), [], 0
    for r in rows:
        if len(r["t"] or "") <= 15:
            continue
        if LEAK_RE.search((r["t"] or "") + " " + (r["l"] or "")):
            leaks += 1
            continue
        k = norm_title(r["t"])
        if k in seen:
            continue
        seen.add(k)
        pool.append(r)
    by_lean = {}
    for r in pool:
        by_lean.setdefault(r["e"] or "unknown", []).append(r)
    total = len(pool)
    if not total:
        return [], 0, leaks
    leans = sorted(by_lean, key=lambda x: (-len(by_lean[x]), x))
    quota = {ln: max(1, round(MAXN * len(by_lean[ln]) / total)) for ln in leans}
    cap = int(MAXN * 0.6)
    if quota[leans[0]] > cap:
        excess = quota[leans[0]] - cap
        quota[leans[0]] = cap
        rest = leans[1:]
        for i in range(excess):
            ln = rest[i % len(rest)] if rest else leans[0]
            quota[ln] = quota.get(ln, 0) + 1
    while sum(quota.values()) > MAXN:
        quota[max(quota, key=lambda x: quota[x])] -= 1
    while sum(quota.values()) < MAXN and total >= MAXN:
        quota[leans[0]] += 1
    items = []
    for ln in leans:
        rs = sorted(by_lean[ln], key=lambda r: (r["d"], norm_title(r["t"])))
        q = min(quota.get(ln, 0), len(rs))
        if q <= 0:
            continue
        stride = len(rs) / q
        for i in range(q):
            r = rs[min(int(i * stride), len(rs) - 1)]
            text = r["t"].strip()
            if r["l"]:
                text += " — " + r["l"].strip()[:220]
            items.append({"text": text, "source": r["s"], "lean": ln, "date": r["d"], "category": r["c"]})
    items.sort(key=lambda x: (x["date"], x["source"] or ""))
    return items, total, leaks


def build(country, target):
    lang = COUNTRIES[country]
    w0, w1 = window(country, target)
    days, d = [], w0
    while d <= w1:
        days.append(d.isoformat())
        d += dt.timedelta(days=2)
    with ThreadPoolExecutor(max_workers=2) as ex:
        res = list(ex.map(lambda day: (day, mcp_get_news(day, lang, country)), days))
    # pótkör: a hibás napok sorban, még egyszer
    res = [(day, r if not r.get("error") else mcp_get_news(day, lang, country)) for day, r in res]
    rows, per_day, errors, sources = [], {}, 0, set()
    for day, r in res:
        if r.get("error"):
            errors += 1
        arts = r.get("articles") or []
        per_day[day] = {"returned": len(arts), "pool": (r.get("diversity") or {}).get("pool_size"),
                        **({"error": r["error"]} if r.get("error") else {})}
        for a in arts:
            sources.add(a.get("source_name"))
            rows.append({"t": a.get("title") or "", "l": (a.get("lead") or "")[:220],
                         "s": a.get("source_name"), "e": a.get("lean") or "unknown",
                         "c": a.get("category"), "d": (a.get("published_at") or day)[:10]})
    items, pool_n, leaks = sample_cell(rows)
    lean_dist, cat_dist = {}, {}
    for it in items:
        lean_dist[it["lean"]] = lean_dist.get(it["lean"], 0) + 1
        cat_dist[it["category"]] = cat_dist.get(it["category"], 0) + 1
    raw_cat = {}
    for r in rows:
        raw_cat[r["c"]] = raw_cat.get(r["c"], 0) + 1
    out = {
        "country": country, "lang": lang, "target_month": target,
        "corpus_window": [w0.isoformat(), w1.isoformat()], "days_sampled": days,
        "n": len(items), "lean_dist": lean_dist, "cat_dist": cat_dist,
        "raw_rows": len(rows), "pool_after_dedupe": pool_n, "leak_dropped": leaks,
        "distinct_sources_raw": len(sources), "raw_cat_dist": raw_cat,
        "per_day": per_day, "mcp_errors": errors, "items": items,
        "built": dt.date.today().isoformat(),
        "source_db": "Echolot MCP get_news (https://echolotnews.com/mcp), published_at szerinti napok",
        "build_rule": ("get_news(limit=100, diversify, max_per_source=3, place=ország) minden 2. napra; "
                       "title>15, LEAK_RE-szűrő, cím-dedupe, lead 220 kar., MAXN=60 lean-cap "
                       "(arányos kvóta + domináns lean <=60% + dátum-stride) — a júliusi sample_cell"),
    }
    path = os.path.join(HERE, "korpusz", f"corpus_{country.lower()}_{target}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"{country} {target} {w0}..{w1}: raw={len(rows)} pool={pool_n} leak={leaks} n={len(items)} "
          f"src={len(sources)} err={errors} lean={lean_dist}", flush=True)
    return out


def main():
    only = sys.argv[1:]  # opcionális: HU FR ...
    for c in COUNTRIES:
        if only and c not in only:
            continue
        for t in TARGETS:
            build(c, t)


if __name__ == "__main__":
    main()
