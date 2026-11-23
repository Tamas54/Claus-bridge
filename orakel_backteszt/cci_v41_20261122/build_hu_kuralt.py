#!/usr/bin/env python3
"""HU KURÁLT, KIEGYENSÚLYOZOTT SAJTÓKORPUSZ (2026-11-23) — a PREREG_HU_KURALT.json szabályai szerint.

A júniusi választási backteszt négy szabálya (orakel_backteszt/README.md) + a Kommandant 11-23-i kérése:
  1. MÉRET: ország-hónaponként ~500 cím + lead, időben egyenletesen (8 nap, egyenlő közzel az ablakban).
  2. TÉMAKVÓTÁK: politika 25, gazdaság 20, bulvár/életmód/celeb 15, sport 12, tech/tudomány 8, külföld 10,
     közélet/helyi/kultúra 10 (%). Kultúrának nincs külön Echolot-kategóriája → a közélet sávban.
  3. LEAN-ARÁNY a panel médiaétrendje szerint (delphoi.COUNTRY_PANEL_CONFIG['HU']['media']): bal 26, jobb 22,
     közmédia 18, közösségi 22, alig 12 (%). Az „alig” szelet a KÖZÖS (pártsemleges) réteg, politika nélkül.
  4. PERSONA-ÉTREND: a persona a saját szeletéből kap 10 címet + 6-ot a közös rétegből (alig: 16-ot a közösből).
  5. SZIVÁRGÁS-SZŰRŐ: a júniusi POLL_BLOCKLIST (config.py) + fogyasztói bizalmi / konjunktúra-index szavak.
Forrás: Echolot MCP get_news (language=hu, a published_at napja = as_of), anon-keret miatt 7 s köz/hívás.

  python3 build_hu_kuralt.py 2026-07 2026-08 2026-09 2026-10
"""
import datetime as dt
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
from config import POLL_BLOCKLIST  # noqa: E402  (a júniusi poll-szűrő, változatlanul)

ECHOLOT = "https://echolotnews.com/mcp"
GAP_S = 7.0
TARGET = 500
N_DAYS = 8
SLICE_SHARE = {"bal": 0.26, "jobb": 0.22, "kozmedia": 0.18, "kozossegi": 0.22, "kozos": 0.12}
TOPIC_SHARE = {"politika": 0.25, "gazdasag": 0.20, "bulvar": 0.15, "sport": 0.12, "tech": 0.08,
               "kulfold": 0.10, "kozelet": 0.10}
CAT2TOPIC = {"politics": "politika", "economy": "gazdasag", "entertainment": "bulvar", "lifestyle": "bulvar",
             "cars": "bulvar", "sport": "sport", "tech": "tech", "foreign": "kulfold", "global": "kulfold",
             "general": "kozelet"}
# Lekérdezések naponta (mind language=hu): (címke, get_news-szűrők)
QUERIES = [
    ("left", {"lean": "left", "limit": 100, "max_per_source": 15}),
    ("center_left", {"lean": "center_left", "limit": 40, "max_per_source": 15}),
    ("right", {"lean": "right", "limit": 100, "max_per_source": 15}),
    ("right_independent", {"lean": "right_independent", "limit": 40, "max_per_source": 15}),
    ("youtube", {"source": "(YouTube)", "limit": 100, "max_per_source": 10}),
    ("x", {"source": "(X)", "limit": 100, "max_per_source": 10}),
    ("infostart", {"source": "Infostart", "limit": 40, "max_per_source": 40}),
    ("unknown", {"lean": "unknown", "limit": 100, "max_per_source": 8}),
    ("analytical", {"lean": "analytical", "limit": 60, "max_per_source": 10}),
    ("center", {"lean": "center", "limit": 60, "max_per_source": 10}),
]
GOV_ACCOUNT_RE = re.compile(r"Magyar Péter \(X\)|Minisztérium|Kormány|Miniszterelnök", re.I)
ABROAD_RE = re.compile(r"Hírek\.sk|Maszol|Krónika|Székelyhon|\.sk\b|Erdély|Felvidék|Vajdaság|Kárpát|Ma7|Bumm|"
                       r"Szabad Magyar Szó|Pannon RTV|Új Szó|Parameter|Transindex|Főtér|Nyugati Jelen|Kolozsvár",
                       re.I)
# szóhatárral: a júniusi lista „IDEA” eleme különben az „ideális”-t, a „Median” a „mediánbér”-t is kivinné
LEAK_RE = re.compile("|".join(r"(?<!\w)" + re.escape(p) + r"(?!\w)" for p in POLL_BLOCKLIST)
                     + r"|fogyasztói (bizalom|bizalmi|hangulat)|bizalmi index|konjunktúra|\bGKI\b", re.I)

_LOCK = threading.Lock()
_NEXT = [0.0]


def get_news(date, **kw):
    args = {"date": date, "language": "hu", "diversify_results": True, "max_per_sphere": 100, **kw}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_news", "arguments": args}}
    for _ in range(8):
        with _LOCK:
            wait = _NEXT[0] - time.time()
            _NEXT[0] = max(time.time(), _NEXT[0]) + GAP_S
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(ECHOLOT, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json, text/event-stream",
                                              "User-Agent": "orakel-backteszt/1.0"})
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=180).read().decode())
            t = d["result"]["content"][0]["text"]
            return json.loads(t)
        except urllib.error.HTTPError as e:
            time.sleep(31 if e.code == 429 else 5)
        except Exception:  # noqa: BLE001
            time.sleep(5)
    return {"error": "failed", "articles": []}


def slice_of(src, lean):
    if ABROAD_RE.search(src or ""):
        return None
    if GOV_ACCOUNT_RE.search(src or "") or (src or "").startswith("Infostart"):
        return "kozmedia"
    if "(YouTube)" in (src or "") or "(X)" in (src or ""):
        return "kozossegi"
    if lean in ("left", "center_left"):
        return "bal"
    if lean in ("right", "right_independent", "center_right"):
        return "jobb"
    return "kozos"


def window(target):
    y, m = map(int, target.split("-"))
    prev = dt.date(y, m, 1) - dt.timedelta(days=1)
    return dt.date(prev.year, prev.month, 15), dt.date(y, m, 14)


def norm(t):
    return re.sub(r"\W+", " ", (t or "").lower()).strip()


def fill_slice(rows, n, topic_share):
    """Témakvóta egy szeleten belül: min(kvóta, elérhető), a maradék arányosan a még bővíthető témákra;
    cellán belül napok szerint körbe járva, mindig a szeletben eddig legkevésbé használt forrásból."""
    by_topic = {}
    for r in rows:
        by_topic.setdefault(r["topic"], []).append(r)
    want = {t: n * s for t, s in topic_share.items()}
    alloc = {t: 0 for t in topic_share}
    remaining = n
    while remaining > 0:
        open_t = [t for t in topic_share if alloc[t] < len(by_topic.get(t, []))]
        if not open_t:
            break
        deficit = {t: want[t] - alloc[t] for t in open_t}
        t = max(open_t, key=lambda x: (deficit[x] / max(topic_share[x], 1e-9), topic_share[x]))
        alloc[t] += 1
        remaining -= 1
    picked, used_src = [], {}
    for t, k in alloc.items():
        pool = sorted(by_topic.get(t, []), key=lambda r: (r["date"], r["source"] or "", norm(r["title"])))
        days = sorted({r["date"] for r in pool})
        per_day = {d: [r for r in pool if r["date"] == d] for d in days}
        out, di = [], 0
        while len(out) < k and any(per_day.values()):
            d = days[di % len(days)]
            di += 1
            if not per_day[d]:
                continue
            per_day[d].sort(key=lambda r: used_src.get(r["source"], 0))
            r = per_day[d].pop(0)
            used_src[r["source"]] = used_src.get(r["source"], 0) + 1
            out.append(r)
        picked.extend(out)
    return picked, alloc


def build(target):
    w0, w1 = window(target)
    span = (w1 - w0).days
    days = [(w0 + dt.timedelta(days=round(i * span / (N_DAYS - 1)))).isoformat() for i in range(N_DAYS)]
    raw, errors = [], 0
    for day in days:
        for qname, kw in QUERIES:
            r = get_news(day, **kw)
            if r.get("error"):
                errors += 1
            for a in r.get("articles") or []:
                raw.append({"title": (a.get("title") or "").strip(), "lead": (a.get("lead") or "").strip()[:220],
                            "source": a.get("source_name"), "lean": a.get("lean") or "unknown",
                            "category": a.get("category"), "date": (a.get("published_at") or day)[:10],
                            "q": qname})
        print(f"  {target} {day}: nyers {len(raw)}", flush=True)
    seen, pool, leaks, abroad = set(), [], 0, 0
    for r in raw:
        if len(r["title"]) <= 15:
            continue
        k = norm(r["title"])
        if k in seen:
            continue
        seen.add(k)
        if LEAK_RE.search(r["title"] + " " + r["lead"]):
            leaks += 1
            continue
        sl = slice_of(r["source"], r["lean"])
        if sl is None:
            abroad += 1
            continue
        r["slice"], r["topic"] = sl, CAT2TOPIC.get(r["category"], "kozelet")
        pool.append(r)
    items, allocs = [], {}
    for sl, share in SLICE_SHARE.items():
        rows = [r for r in pool if r["slice"] == sl]
        if sl == "kozos":  # a közös réteg pártsemleges: politika nélkül, a többi kvóta arányosan
            ts = {t: s for t, s in TOPIC_SHARE.items() if t != "politika"}
            tot = sum(ts.values())
            ts = {t: s / tot for t, s in ts.items()}
            rows = [r for r in rows if r["topic"] != "politika"]
        else:
            ts = TOPIC_SHARE
        got, alloc = fill_slice(rows, round(TARGET * share), ts)
        allocs[sl] = {"cel": round(TARGET * share), "kapott": len(got), "elerheto": len(rows), "temak": alloc}
        items.extend(got)
    for it in items:
        it["text"] = it["title"] + (" — " + it["lead"] if it["lead"] else "")
    out = {"country": "HU", "target_month": target, "corpus_window": [w0.isoformat(), w1.isoformat()],
           "days_sampled": days, "n": len(items), "raw_rows": len(raw), "pool": len(pool),
           "leak_dropped": leaks, "abroad_dropped": abroad, "mcp_errors": errors, "szeletek": allocs,
           "items": items, "built": dt.date.today().isoformat(),
           "source_db": "Echolot MCP get_news (language=hu), published_at napja",
           "rules": "PREREG_HU_KURALT.json"}
    os.makedirs(os.path.join(HERE, "korpusz_kuralt"), exist_ok=True)
    json.dump(out, open(os.path.join(HERE, "korpusz_kuralt", f"corpus_hu_{target}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"HU {target}: n={len(items)} pool={len(pool)} raw={len(raw)} leak=-{leaks} határon túli=-{abroad} "
          f"err={errors} " + " ".join(f"{k}:{v['kapott']}/{v['cel']}" for k, v in allocs.items()), flush=True)


if __name__ == "__main__":
    for t in sys.argv[1:]:
        build(t)
