#!/usr/bin/env python3
"""Determinisztikus pontozás (AI nélkül): kulcsszó-találat a bank `keywords` mezője szerint (szókezdő előtag;
'=X' egész szó), NEM TUDOM / elzárkózás = nem talált; `neg_keywords` találat = téves. Kimenet: havi és gördülő
3 havi találati arány, cutoff-javaslat (utolsó hónap ≥60%-os gördülő aránnyal), tiszta_tol (az a hónap, amelytől
minden későbbi esemény ismeretlen), és a HU-kártya ellenőrzés.

  python3 grade.py resp_<model>.json [...]   → grade_<model>.json + konzol-táblázat
"""
import json, pathlib, re, sys, unicodedata

HERE = pathlib.Path(__file__).resolve().parent
OLD = HERE.parent / "cutoff_szonda_20260720"
BANKS = [OLD / "cutoff_events.json", OLD / "cutoff_events_boundary.json", OLD / "cutoff_events_boundary2.json",
         HERE / "cutoff_events_ladder_2023_2024.json", HERE / "cutoff_events_echolot.json"]
ABSTAIN = re.compile(r"NEM TUDOM|nem tudom|nem rendelkezem|nincs (?:megbízható )?információ|nem áll rendelkezés|"
                     r"tudásom (?:lezárás|határ)|nem ismerem|I don't know|I do not have|not aware", re.I)
HU_TISZA = re.compile(r"Tisza.{0,80}(nyert|győz|kétharmad|won|victory)|Magyar Péter.{0,60}(miniszterelnök|kormányf|prime minister)|"
                      r"(nyert|győz|won).{0,60}Tisza", re.I | re.S)


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower()


def kw_hit(ans, kws):
    a = fold(ans)
    for k in kws or []:
        if k.startswith("="):
            if re.search(r"(?<![\w])" + re.escape(fold(k[1:])) + r"(?![\w])", a):
                return k
        elif re.search(r"(?<![\w])" + re.escape(fold(k)), a):
            return k
    return None


OVR = json.loads((HERE / "kw_override.json").read_text())


def bank_index():
    idx = {}
    for p in BANKS:
        if p.exists():
            b = json.loads(p.read_text())
            for ev in b.get("events", []):
                idx.setdefault(ev["id"], dict(ev))
    for k, kws in OVR["keywords"].items():
        if k in idx:
            idx[k]["keywords"] = kws
    return idx


def months_between(a, b):
    y1, m1 = map(int, a.split("-")); y2, m2 = map(int, b.split("-"))
    return (y2 - y1) * 12 + (m2 - m1)


def add_months(a, k):
    y, m = map(int, a.split("-")); t = y * 12 + (m - 1) + k
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def grade(path):
    d = json.loads(pathlib.Path(path).read_text())
    idx = bank_index()
    rows, hu = [], []
    for r in d["responses"]:
        ans = r.get("answer") or ""
        if r["bucket"] == "featured-HU":
            hu.append({"id": r["id"], "tisza_tudja": bool(HU_TISZA.search(ans)), "answer": ans[:300]})
            continue
        if r["id"] in OVR["kizart"]:
            continue
        ev = idx.get(r["id"]) or {}
        hit = kw_hit(ans, ev.get("keywords"))
        neg = kw_hit(ans, ev.get("neg_keywords"))
        absn = bool(ABSTAIN.search(ans)) and not hit
        # NEM TUDOM-mal KEZDŐDŐ válasz akkor sem találat, ha utána régi tényt említ (pl. „az előző idényt a Slavia
        # nyerte”) — utólagos javítás (11-22, Opus E28/E33 hamis pozitívjai), minden modellre egyformán alkalmazva
        starts_idk = re.match(r"[\s*_\"„]*(NEM TUDOM|nem tudom|I don'?t know)", ans) is not None
        ok = bool(hit) and not neg and not starts_idk and not (ABSTAIN.search(ans) and len(ans) < 60)
        rows.append({"id": r["id"], "month": r["event_month"], "ok": ok, "kw": hit, "abstain": absn,
                     "error": r.get("answer") is None, "answer": ans[:240], "gt": ev.get("gt")})
    months = sorted({x["month"] for x in rows})
    per_m = {m: [x["ok"] for x in rows if x["month"] == m] for m in months}
    lo, hi = months[0], months[-1]
    allm = [add_months(lo, k) for k in range(months_between(lo, hi) + 1)]
    roll = {}
    for m in allm:
        win = [v for k in range(3) for v in per_m.get(add_months(m, -k), [])]
        if win:
            roll[m] = round(sum(win) / len(win), 3)
    # a cutoff-hónapban magában is legyen találat (a gördülő ablak különben a következő üres hónapra is átcsúszik)
    cutoff = max((m for m, v in roll.items() if v >= 0.6 and any(per_m.get(m, []))), default=None)
    # a modell_cutoff.CUTOFF definíciója: az utolsó HÓNAP, ahol a szonda még ≥60%-ban talál (havi, nem gördülő)
    cutoff_havi = max((m for m, v in per_m.items() if v and sum(v) / len(v) >= 0.6), default=None)
    last_hit = max((x["month"] for x in rows if x["ok"]), default=None)
    tiszta_tol = add_months(last_hit, 1) if last_hit else lo
    out = {"model": d["meta"]["model"], "n": len(rows), "n_ok": sum(x["ok"] for x in rows),
           "n_error": sum(x["error"] for x in rows),
           "havi": {m: f"{sum(v)}/{len(v)}" for m, v in per_m.items()}, "gordulo3": roll,
           "cutoff_javaslat": cutoff, "cutoff_havi": cutoff_havi, "utolso_talalat": last_hit, "tiszta_tol_javaslat": tiszta_tol,
           "hu_kartya": {"tudja_db": sum(h["tisza_tudja"] for h in hu), "n": len(hu), "valaszok": hu},
           "rows": rows}
    p = HERE / ("grade_" + pathlib.Path(path).name[len("resp_"):])
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n== {out['model']}: {out['n_ok']}/{out['n']} talált, hiba {out['n_error']}, cutoff(havi≥60%)={cutoff_havi}, gördülő≈{cutoff}, "
          f"utolsó találat {last_hit}, tiszta_tol≈{tiszta_tol}, HU-kártya {out['hu_kartya']['tudja_db']}/{len(hu)}")
    print("  " + " ".join(f"{m[2:]}:{v}" for m, v in out["havi"].items()))
    return out


if __name__ == "__main__":
    for f in sys.argv[1:]:
        grade(f)
