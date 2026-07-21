#!/usr/bin/env python3
"""OPERATION G2-WEST — korpusz-építő az ÉLES prod Echolot DB-ből (railway ssh, mode=ro).

G2-minta (corpus_fr/it_*.json) klónja US/UK/DE-re: cellánként a prod-ból (lean-)
számlálók + véletlen 1200-as nyers minta jön le, lokálban cím-dedupe + title>15 +
lean-cap (domináns lean ≤60%, dátum-stride ritkítás) → MAXN=60 egysoros hír-állítás.
Ablakok és indoklás: gt_LOCKED_west.json (commit a futás előtt).
"""
import json
import os
import re
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
RAILWAY_DIR = os.path.expanduser("~/Hirmagnetmcp")   # glistening-luck-hoz linkelt
MAXN = 60
SAMPLE = 1200

# AMENDMENT 2026-07-21 (futasok ELOTT): LIKE-wildcard bug fix (ESCAPE '!'), ir
# forrasok ki a uk_-bol (GfK UK panel!), de_ prefix-szures (osztrak/svajci/sport
# forrasok ki), us_ cella-revizio: multi-forras rezsim csak 2026-07-09-tol
# (elotte Us Weekly-monokultura) -> junT/julP cella EJTVE, us_A/us_B valtja.
_IRISH = "('uk_irish_independent','uk_irish_times','uk_rte_news','uk_thejournal_ie')"
CELLS = {
    "us_A":   {"where": "a.language='en' AND a.source_id LIKE 'us!_%' ESCAPE '!'",
               "win": ["2026-07-09", "2026-07-15"], "lang": "en"},
    "us_B":   {"where": "a.language='en' AND a.source_id LIKE 'us!_%' ESCAPE '!'",
               "win": ["2026-07-15", "2026-07-22"], "lang": "en"},
    "uk_jul": {"where": ("a.language='en' AND a.source_id LIKE 'uk!_%' ESCAPE '!' "
                         f"AND a.source_id NOT IN {_IRISH}"),
               "win": ["2026-06-21", "2026-07-16"], "lang": "en"},
    "de_jul": {"where": "a.language='de' AND a.source_id LIKE 'de!_%' ESCAPE '!'",
               "win": ["2026-06-21", "2026-07-15"], "lang": "de"},
}

REMOTE = r'''
import json, sqlite3
c = sqlite3.connect('file:/data/echolot.db?mode=ro', uri=True)
cells = json.loads(%s)
out = {}
for name, cfg in cells.items():
    w = cfg["where"].replace("language=", "a.language=").replace("a.a.", "a.")
    win0, win1 = cfg["win"]
    base = (f"FROM articles a JOIN sources s ON s.id=a.source_id WHERE {w} "
            f"AND COALESCE(a.published_at,a.fetched_at) >= '{win0}' "
            f"AND COALESCE(a.published_at,a.fetched_at) < '{win1}'")
    counts = dict(c.execute(f"SELECT s.lean, COUNT(*) {base} GROUP BY s.lean").fetchall())
    cats = dict(c.execute(f"SELECT a.category, COUNT(*) {base} GROUP BY a.category").fetchall())
    srcpref = dict(c.execute(
        f"SELECT substr(a.source_id,1,3), COUNT(*) {base} GROUP BY substr(a.source_id,1,3)").fetchall())
    rows = c.execute(
        f"SELECT a.title, substr(COALESCE(a.lead,''),1,220), a.source_name, s.lean, a.category, "
        f"substr(COALESCE(a.published_at,a.fetched_at),1,10) {base} ORDER BY RANDOM() LIMIT %d").fetchall()
    out[name] = {"lean_counts_full": counts, "cat_counts_full": cats,
                 "source_prefix_counts": srcpref,
                 "rows": [{"t": r[0], "l": r[1], "s": r[2], "e": r[3], "c": r[4], "d": r[5]} for r in rows]}
print("G2WEST_JSON_START")
print(json.dumps(out, ensure_ascii=False))
'''


def fetch_remote():
    payload = json.dumps({k: {"where": v["where"], "win": v["win"]} for k, v in CELLS.items()})
    script = REMOTE % (json.dumps(payload), SAMPLE)
    import base64
    b64 = base64.b64encode(script.encode()).decode()
    cmd = ["railway", "ssh", f"echo {b64} | base64 -d | python3"]
    r = subprocess.run(cmd, cwd=RAILWAY_DIR, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        sys.exit(f"railway ssh hiba: {r.stderr[:500]}")
    txt = r.stdout.split("G2WEST_JSON_START")[-1].strip()
    return json.loads(txt.splitlines()[0])


def norm_title(t):
    return re.sub(r"\W+", " ", (t or "").lower()).strip()


def sample_cell(name, data):
    seen, pool = set(), []
    for r in data["rows"]:
        if len(r["t"] or "") <= 15:
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
    # proporcionális kvóta, domináns lean cap 60% (36), maradék arányosan visszaosztva
    leans = sorted(by_lean, key=lambda x: -len(by_lean[x]))
    quota = {ln: max(1, round(MAXN * len(by_lean[ln]) / total)) for ln in leans}
    cap = int(MAXN * 0.6)
    if quota[leans[0]] > cap:
        excess = quota[leans[0]] - cap
        quota[leans[0]] = cap
        rest = [ln for ln in leans[1:]]
        for i in range(excess):
            ln = rest[i % len(rest)] if rest else leans[0]
            quota[ln] = quota.get(ln, 0) + 1
    # normalizálás pontosan MAXN-re
    while sum(quota.values()) > MAXN:
        quota[max(quota, key=lambda x: quota[x])] -= 1
    while sum(quota.values()) < MAXN and total >= MAXN:
        quota[leans[0]] += 1
    items = []
    for ln in leans:
        rows = sorted(by_lean[ln], key=lambda r: (r["d"], norm_title(r["t"])))
        q = min(quota.get(ln, 0), len(rows))
        if q <= 0:
            continue
        stride = len(rows) / q
        picked = [rows[min(int(i * stride), len(rows) - 1)] for i in range(q)]
        for r in picked:
            text = r["t"].strip()
            if r["l"]:
                text += " — " + r["l"].strip()
            items.append({"text": text, "source": r["s"], "lean": ln, "date": r["d"], "category": r["c"]})
    items.sort(key=lambda x: (x["date"], x["source"]))
    lean_dist, cat_dist = {}, {}
    for it in items:
        lean_dist[it["lean"]] = lean_dist.get(it["lean"], 0) + 1
        cat_dist[it["category"]] = cat_dist.get(it["category"], 0) + 1
    return {
        "cell": name, "lang": CELLS[name]["lang"],
        "corpus_window": CELLS[name]["win"], "n": len(items),
        "lean_dist": lean_dist, "cat_dist": cat_dist,
        "pool_lean_counts_full": data["lean_counts_full"],
        "pool_cat_counts_full": data["cat_counts_full"],
        "source_prefix_counts": data["source_prefix_counts"],
        "items": items, "built": str(date.today()),
        "source_db": "ELES prod Echolot DB (railway ssh glistening-luck, file:/data/echolot.db?mode=ro)",
        "build_rule": (f"title>15, cim-dedupe, lead 220 char, RANDOM({SAMPLE}) nyers minta a prod-bol, "
                       f"MAXN={MAXN} lean-cap: proporcionalis kvota + dominans lean <=60% + datum-stride ritkitas"),
    }


def main():
    raw = fetch_remote()
    for name, data in raw.items():
        payload = sample_cell(name, data)
        out = os.path.join(HERE, f"corpus_{name}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"{name}: n={payload['n']} lean={payload['lean_dist']} "
              f"pool={sum(data['lean_counts_full'].values())} -> {out}")


if __name__ == "__main__":
    main()
