"""STATISZTIKA a családi chatben — táblázat (CSV/TSV/XLSX) beolvasása és
determinisztikus próbák (scipy), hogy a modell NE fejben számoljon.

Kommandant 2026-09-21: „Csv, meg minden faszom statek legyen már a
kislányoknak elérhető!!!" — Réka (biológus) statisztikázni akart, a chat a
CSV-t nyers szövegként adta a modellnek (csonkolva), az Excel Unicode
(UTF-16) exportot pedig „olvashatatlan"-ként eldobta.

KÉT RÉTEG:
  1. `tabla_szovegbol` / `tabla_sorokbol` → egységes tábla-szótár
     {oszlopok, tipus, sorok, n} + `osszefoglalo()` magyar leíró szöveg a
     modellnek (oszloponként n, hiány, átlag, szórás, medián, min, max;
     kategóriáknál a szintek gyakorisága; az első 5 sor).
  2. `futtat(conn, instance, args)` → a `statisztika` eszköz: a SAJÁT
     feltöltésre (yr_chat_files.tabla_json) futtatja a kért próbát, és
     az eredményhez MAGYAR magyarázatot + feltevés-figyelmeztetéseket ad.
     Sose dob: a hibát adatként adja vissza (a chat tool-kör így várja).

Csak számol, nem dönt: a próbaválasztás a modellé/felhasználóé, de a
feltevéseket (normalitás, szórás-egyezés, cella-elvárás) kimondja.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import os
import re

logger = logging.getLogger("bridge.statisztika")

MAX_SOR = 20_000          # ennyi sor marad a tabla_json-ban (a mintaszám látszik)
ELSO_SOROK = 5            # a modellnek mutatott első sorok
_SZAM_RX = re.compile(r"^\s*[-+]?(\d{1,3}([ .,]\d{3})+|\d+)([.,]\d+)?([eE][-+]?\d+)?\s*$")

MUVELETEK = ("leiras", "normalitas", "t_proba", "mann_whitney", "anova",
             "kruskal", "khi_negyzet", "korrelacio", "regresszio")


# ── beolvasás ───────────────────────────────────────────────────────────

def szam(cell) -> float | None:
    """Cella → float, ha szám (tizedesvessző, ezres szóköz/pont is)."""
    if cell is None:
        return None
    if isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float)):
        return float(cell) if math.isfinite(float(cell)) else None
    s = str(cell).strip()
    if not s or s.lower() in ("na", "n/a", "nan", "null", "none", "-", "—"):
        return None
    if not _SZAM_RX.match(s):
        return None
    t = s.replace(" ", "")
    if "," in t and "." in t:
        # 1.234,56 (magyar/német) vs 1,234.56 (angol): az utolsó a tizedes
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        darab = t.split(",")
        # 1,234 lehet ezres is — ha pontosan 3 számjegy követi és nincs más, kétséges;
        # a biológiai adatokban a tizedesvessző a gyakori, azt vesszük
        t = t.replace(",", ".") if len(darab) == 2 else t.replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _dekodol(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace")
    for enc in ("utf-8", "iso-8859-2", "cp1250"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _elvalaszto(minta: str, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "tsv":
        return "\t"
    try:
        return csv.Sniffer().sniff(minta, delimiters=";,\t|").delimiter
    except csv.Error:
        pass
    elso = minta.splitlines()[0] if minta.splitlines() else ""
    jeloltek = {d: elso.count(d) for d in (";", ",", "\t", "|")}
    return max(jeloltek, key=jeloltek.get) if any(jeloltek.values()) else ","


def tabla_szovegbol(raw: bytes, filename: str = "adat.csv") -> dict:
    """CSV/TSV bájtok → tábla-szótár. ValueError, ha nincs fejléc + adat."""
    text = _dekodol(raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    sep = _elvalaszto(text[:8000], filename)
    olvaso = csv.reader(io.StringIO(text), delimiter=sep)
    sorok = [s for s in olvaso if any((c or "").strip() for c in s)]
    if len(sorok) < 2:
        raise ValueError("A táblázatban nincs fejléc és legalább egy adatsor.")
    return tabla_sorokbol(sorok, forras=f"{filename} (elválasztó: {'TAB' if sep == chr(9) else sep})")


def tabla_sorokbol(sorok: list, forras: str = "") -> dict:
    """Sorok (első = fejléc) → {oszlokok, tipus, sorok, n, forras, notes}."""
    fej = [str(c).strip() if c is not None else "" for c in sorok[0]]
    fej = [f or f"oszlop_{i + 1}" for i, f in enumerate(fej)]
    # duplikált fejléc-név egyértelműsítése
    latott: dict = {}
    for i, f in enumerate(fej):
        if f in latott:
            latott[f] += 1
            fej[i] = f"{f}_{latott[f]}"
        else:
            latott[f] = 1
    adat = []
    for s in sorok[1:]:
        s = list(s) + [None] * (len(fej) - len(s))
        adat.append(["" if c is None else (c if isinstance(c, (int, float)) and not isinstance(c, bool) else str(c).strip())
                     for c in s[:len(fej)]])
    notes = []
    n_ossz = len(adat)
    if n_ossz > MAX_SOR:
        adat = adat[:MAX_SOR]
        notes.append(f"A táblázat {n_ossz} soros — az első {MAX_SOR} sort tartottam meg a számításhoz.")
    tipus = {}
    for j, f in enumerate(fej):
        ertekek = [r[j] for r in adat if r[j] not in ("", None)]
        if not ertekek:
            tipus[f] = "ures"
            continue
        szamok = sum(1 for v in ertekek if szam(v) is not None)
        tipus[f] = "szam" if szamok >= 0.8 * len(ertekek) else "kategoria"
    return {"oszlopok": fej, "tipus": tipus, "sorok": adat, "n": len(adat),
            "forras": forras, "notes": notes}


def _oszlop_szamok(tabla: dict, nev: str) -> list[float]:
    j = tabla["oszlopok"].index(nev)
    return [v for v in (szam(r[j]) for r in tabla["sorok"]) if v is not None]


def _leiro(x: list[float]) -> dict:
    import numpy as np
    a = np.asarray(x, dtype=float)
    if a.size == 0:
        return {"n": 0}
    q1, med, q3 = (float(v) for v in np.percentile(a, [25, 50, 75]))
    return {"n": int(a.size), "atlag": round(float(a.mean()), 4),
            "szoras": round(float(a.std(ddof=1)), 4) if a.size > 1 else None,
            "medián": round(med, 4), "q1": round(q1, 4), "q3": round(q3, 4),
            "min": round(float(a.min()), 4), "max": round(float(a.max()), 4)}


def osszefoglalo(tabla: dict) -> str:
    """A modellnek szánt leíró szöveg — pontos, számolt, nem becsült."""
    fej, tip, sorok = tabla["oszlopok"], tabla["tipus"], tabla["sorok"]
    ki = [f"TÁBLÁZAT: {tabla['n']} adatsor × {len(fej)} oszlop"
          + (f" — {tabla['forras']}" if tabla.get("forras") else "")]
    ki.append("OSZLOPOK:")
    for j, f in enumerate(fej):
        ossz = len(sorok)
        hiany = sum(1 for r in sorok if r[j] in ("", None))
        if tip[f] == "szam":
            d = _leiro([v for v in (szam(r[j]) for r in sorok) if v is not None])
            ki.append(f"- {f} [szám] n={d['n']}, hiányzik={hiany}, átlag={d['atlag']}, "
                      f"szórás={d['szoras']}, medián={d['medián']}, min={d['min']}, max={d['max']}")
        elif tip[f] == "kategoria":
            gyak: dict = {}
            for r in sorok:
                if r[j] not in ("", None):
                    k = str(r[j])
                    gyak[k] = gyak.get(k, 0) + 1
            top = sorted(gyak.items(), key=lambda kv: -kv[1])[:6]
            ki.append(f"- {f} [kategória] {len(gyak)} szint, hiányzik={hiany}: "
                      + ", ".join(f"{k} ({v})" for k, v in top)
                      + (" …" if len(gyak) > 6 else ""))
        else:
            ki.append(f"- {f} [üres] ({ossz} sorból mind üres)")
    ki.append(f"ELSŐ {min(ELSO_SOROK, len(sorok))} SOR:")
    ki.append(" | ".join(fej))
    for r in sorok[:ELSO_SOROK]:
        ki.append(" | ".join(str(c) for c in r))
    for n in tabla.get("notes") or []:
        ki.append(f"MEGJEGYZÉS: {n}")
    ki.append("A számításokhoz a `statisztika` eszközt kell hívni (file_id-vel), fejben nem szabad számolni.")
    return "\n".join(ki)


# ── próbák ──────────────────────────────────────────────────────────────

def _csoportok(tabla: dict, oszlop: str, csoport: str, valasztott: list | None):
    """{szint: [számok]} a csoport-oszlop szerint; a kért két/több szintre szűkítve."""
    jo, jc = tabla["oszlopok"].index(oszlop), tabla["oszlopok"].index(csoport)
    ki: dict = {}
    for r in tabla["sorok"]:
        v = szam(r[jo])
        k = str(r[jc]).strip() if r[jc] not in ("", None) else ""
        if v is None or not k:
            continue
        ki.setdefault(k, []).append(v)
    if valasztott:
        kert = [str(x).strip() for x in valasztott]
        hiany = [k for k in kert if k not in ki]
        if hiany:
            raise ValueError(f"Nincs ilyen csoport: {', '.join(hiany)}. Létező szintek: {', '.join(sorted(ki))}")
        ki = {k: ki[k] for k in kert}
    return ki


def _p_szoveg(p: float) -> str:
    if p < 0.001:
        return "p < 0,001 — statisztikailag erősen szignifikáns"
    if p < 0.01:
        return f"p = {p:.3f} — statisztikailag szignifikáns (1 %-os szinten)"
    if p < 0.05:
        return f"p = {p:.3f} — statisztikailag szignifikáns (5 %-os szinten)"
    if p < 0.1:
        return f"p = {p:.3f} — nem szignifikáns 5 %-on, de a határon (tendencia)"
    return f"p = {p:.3f} — nem szignifikáns"


def _d_szoveg(d: float) -> str:
    a = abs(d)
    return "elhanyagolható" if a < 0.2 else "kicsi" if a < 0.5 else "közepes" if a < 0.8 else "nagy"


def _cohen_d(a, b) -> float:
    import numpy as np
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.size < 2 or b.size < 2:
        return float("nan")
    sp = math.sqrt(((a.size - 1) * a.var(ddof=1) + (b.size - 1) * b.var(ddof=1)) / (a.size + b.size - 2))
    return float((a.mean() - b.mean()) / sp) if sp > 0 else float("nan")


def _normalitas_jegyzet(cs: dict) -> list[str]:
    from scipy import stats
    ki = []
    for k, x in cs.items():
        if 3 <= len(x) <= 5000:
            w, p = stats.shapiro(x)
            if p < 0.05:
                ki.append(f"„{k}” (n={len(x)}): a Shapiro–Wilk szerint nem normális eloszlású (p={p:.3f}) — "
                          "nemparaméteres próba (Mann–Whitney / Kruskal) is érdemes.")
    return ki


def _oszlop_ellenoriz(tabla: dict, nev: str | None, mezo: str, kell_szam: bool | None = None) -> str:
    if not nev:
        raise ValueError(f"Hiányzik az „{mezo}” paraméter. Oszlopok: {', '.join(tabla['oszlopok'])}")
    if nev not in tabla["oszlopok"]:
        # kis/nagybetű, szóköz eltérés tolerálása
        egyez = [o for o in tabla["oszlopok"] if o.strip().lower() == str(nev).strip().lower()]
        if not egyez:
            raise ValueError(f"Nincs „{nev}” nevű oszlop. Oszlopok: {', '.join(tabla['oszlopok'])}")
        nev = egyez[0]
    if kell_szam is True and tabla["tipus"].get(nev) != "szam":
        raise ValueError(f"A(z) „{nev}” oszlop nem számoszlop (típus: {tabla['tipus'].get(nev)}).")
    return nev


def szamol(tabla: dict, args: dict) -> dict:
    """A próba maga — tiszta függvény, DB nélkül (tesztelhető)."""
    from scipy import stats
    import numpy as np
    muv = (args.get("muvelet") or "leiras").strip().lower()
    if muv not in MUVELETEK:
        return {"hiba": f"Ismeretlen művelet: {muv}. Lehetséges: {', '.join(MUVELETEK)}"}
    figy: list[str] = []
    try:
        if muv == "leiras":
            oszlop = args.get("oszlop")
            csoport = args.get("csoport")
            if oszlop and csoport:
                oszlop = _oszlop_ellenoriz(tabla, oszlop, "oszlop", True)
                csoport = _oszlop_ellenoriz(tabla, csoport, "csoport")
                cs = _csoportok(tabla, oszlop, csoport, args.get("csoportok"))
                return {"muvelet": muv, "oszlop": oszlop, "csoport": csoport,
                        "csoportonkent": {k: _leiro(v) for k, v in cs.items()},
                        "magyarazat": f"„{oszlop}” leíró statisztikája „{csoport}” szerint, {len(cs)} csoport."}
            if oszlop:
                oszlop = _oszlop_ellenoriz(tabla, oszlop, "oszlop", True)
                return {"muvelet": muv, "oszlop": oszlop, "leiro": _leiro(_oszlop_szamok(tabla, oszlop)),
                        "magyarazat": f"„{oszlop}” leíró statisztikája."}
            return {"muvelet": muv, "osszefoglalo": osszefoglalo(tabla),
                    "magyarazat": "A teljes táblázat leíró statisztikája oszloponként."}

        if muv == "normalitas":
            oszlop = _oszlop_ellenoriz(tabla, args.get("oszlop"), "oszlop", True)
            csoport = args.get("csoport")
            cs = (_csoportok(tabla, oszlop, _oszlop_ellenoriz(tabla, csoport, "csoport"), args.get("csoportok"))
                  if csoport else {"(mind)": _oszlop_szamok(tabla, oszlop)})
            ki = {}
            for k, x in cs.items():
                if len(x) < 3:
                    ki[k] = {"n": len(x), "hiba": "legalább 3 érték kell"}
                    continue
                w, p = stats.shapiro(x[:5000])
                ki[k] = {"n": len(x), "shapiro_W": round(float(w), 4), "p": round(float(p), 4),
                         "normalis_5pct": bool(p >= 0.05)}
            return {"muvelet": muv, "oszlop": oszlop, "csoportonkent": ki,
                    "magyarazat": "Shapiro–Wilk normalitás-próba: p ≥ 0,05 esetén nincs bizonyíték a normálistól való eltérésre "
                                  "(kis mintán a próba gyenge, nagy mintán apró eltérésre is szignifikál)."}

        if muv in ("t_proba", "mann_whitney"):
            oszlop = _oszlop_ellenoriz(tabla, args.get("oszlop"), "oszlop", True)
            if args.get("parositott") and args.get("oszlop2"):
                o2 = _oszlop_ellenoriz(tabla, args.get("oszlop2"), "oszlop2", True)
                j1, j2 = tabla["oszlopok"].index(oszlop), tabla["oszlopok"].index(o2)
                parok = [(szam(r[j1]), szam(r[j2])) for r in tabla["sorok"]]
                parok = [(a, b) for a, b in parok if a is not None and b is not None]
                if len(parok) < 3:
                    return {"hiba": "Párosított próbához legalább 3 teljes pár kell."}
                a, b = np.array([p[0] for p in parok]), np.array([p[1] for p in parok])
                if muv == "t_proba":
                    t, p = stats.ttest_rel(a, b)
                    d = float((a - b).mean() / (a - b).std(ddof=1)) if (a - b).std(ddof=1) > 0 else float("nan")
                    return {"muvelet": "parositott_t_proba", "n_par": len(parok), "atlag_kulonbseg": round(float((a - b).mean()), 4),
                            "t": round(float(t), 4), "p": round(float(p), 6), "cohen_d": round(d, 3),
                            "magyarazat": f"Párosított t-próba, {len(parok)} pár: az átlagos különbség {float((a - b).mean()):.4g}; "
                                          f"t = {float(t):.3f}, {_p_szoveg(float(p))}; hatásméret d = {d:.2f} ({_d_szoveg(d)})."}
                w, p = stats.wilcoxon(a, b)
                return {"muvelet": "wilcoxon_parositott", "n_par": len(parok), "W": round(float(w), 4), "p": round(float(p), 6),
                        "medián_kulonbseg": round(float(np.median(a - b)), 4),
                        "magyarazat": f"Wilcoxon előjeles rangpróba (párosított, nemparaméteres), {len(parok)} pár: {_p_szoveg(float(p))}."}
            csoport = _oszlop_ellenoriz(tabla, args.get("csoport"), "csoport")
            cs = _csoportok(tabla, oszlop, csoport, args.get("csoportok"))
            if len(cs) != 2:
                return {"hiba": f"Kétcsoportos próbához pontosan 2 csoport kell, most {len(cs)}: {', '.join(sorted(cs))}. "
                                f"Add meg a „csoportok” listát (2 szint), vagy használj ANOVA/Kruskal próbát."}
            (ka, a), (kb, b) = cs.items()
            figy += _normalitas_jegyzet(cs)
            if muv == "t_proba":
                if len(a) < 2 or len(b) < 2:
                    return {"hiba": "Mindkét csoportban legalább 2 érték kell."}
                lev_w, lev_p = stats.levene(a, b)
                welch = bool(lev_p < 0.05)
                t, p = stats.ttest_ind(a, b, equal_var=not welch)
                d = _cohen_d(a, b)
                if welch:
                    figy.append(f"A szórások eltérnek (Levene p={lev_p:.3f}) — Welch-féle t-próbát számoltam.")
                return {"muvelet": "welch_t_proba" if welch else "student_t_proba", "csoportok": {ka: _leiro(a), kb: _leiro(b)},
                        "t": round(float(t), 4), "p": round(float(p), 6), "cohen_d": round(d, 3),
                        "levene_p": round(float(lev_p), 4), "figyelmeztetes": figy,
                        "magyarazat": f"{'Welch' if welch else 'Student'} t-próba, „{oszlop}” a „{csoport}” két szintje között: "
                                      f"{ka} (n={len(a)}, átlag {np.mean(a):.4g}) vs {kb} (n={len(b)}, átlag {np.mean(b):.4g}); "
                                      f"t = {float(t):.3f}, {_p_szoveg(float(p))}; hatásméret Cohen d = {d:.2f} ({_d_szoveg(d)})."}
            u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
            n1, n2 = len(a), len(b)
            mu, sigma = n1 * n2 / 2, math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
            z = (float(u) - mu) / sigma if sigma > 0 else 0.0
            r = abs(z) / math.sqrt(n1 + n2)
            return {"muvelet": "mann_whitney", "csoportok": {ka: _leiro(a), kb: _leiro(b)}, "U": round(float(u), 2),
                    "p": round(float(p), 6), "hatasmeret_r": round(r, 3), "figyelmeztetes": figy,
                    "magyarazat": f"Mann–Whitney U-próba, „{oszlop}” {ka} (n={n1}, medián {np.median(a):.4g}) vs {kb} (n={n2}, medián {np.median(b):.4g}): "
                                  f"U = {float(u):.1f}, {_p_szoveg(float(p))}; hatásméret r = {r:.2f} ({_d_szoveg(r * 2)})."}

        if muv in ("anova", "kruskal"):
            oszlop = _oszlop_ellenoriz(tabla, args.get("oszlop"), "oszlop", True)
            csoport = _oszlop_ellenoriz(tabla, args.get("csoport"), "csoport")
            cs = _csoportok(tabla, oszlop, csoport, args.get("csoportok"))
            cs = {k: v for k, v in cs.items() if len(v) >= 2}
            if len(cs) < 2:
                return {"hiba": "Legalább 2 csoport kell, csoportonként ≥2 értékkel."}
            nevek, mintak = list(cs), list(cs.values())
            figy += _normalitas_jegyzet(cs)
            if muv == "anova":
                f, p = stats.f_oneway(*mintak)
                osszes = np.concatenate([np.asarray(m, float) for m in mintak])
                ss_between = sum(len(m) * (np.mean(m) - osszes.mean()) ** 2 for m in mintak)
                ss_total = float(((osszes - osszes.mean()) ** 2).sum())
                eta2 = ss_between / ss_total if ss_total > 0 else float("nan")
                lev_w, lev_p = stats.levene(*mintak)
                if lev_p < 0.05:
                    figy.append(f"A szórások eltérnek (Levene p={lev_p:.3f}) — az ANOVA feltevése sérül, a Kruskal–Wallis próba biztonságosabb.")
                paros = None
                if p < 0.05:
                    try:
                        th = stats.tukey_hsd(*mintak)
                        paros = []
                        for i in range(len(nevek)):
                            for j in range(i + 1, len(nevek)):
                                paros.append({"a": nevek[i], "b": nevek[j], "kulonbseg": round(float(np.mean(mintak[i]) - np.mean(mintak[j])), 4),
                                              "p": round(float(th.pvalue[i][j]), 6)})
                    except Exception as e:  # noqa: BLE001
                        figy.append(f"Tukey-utóteszt nem futott: {e}")
                return {"muvelet": "anova_egyutas", "csoportok": {k: _leiro(v) for k, v in cs.items()}, "F": round(float(f), 4),
                        "p": round(float(p), 6), "eta2": round(float(eta2), 4), "levene_p": round(float(lev_p), 4),
                        "tukey_paros": paros, "figyelmeztetes": figy,
                        "magyarazat": f"Egyutas ANOVA, „{oszlop}” a „{csoport}” {len(cs)} szintje között: F = {float(f):.3f}, {_p_szoveg(float(p))}; "
                                      f"η² = {eta2:.3f} (a szórás {eta2 * 100:.1f} %-át magyarázza a csoport)."
                                      + (" A Tukey-utóteszt páronként mutatja, mely csoportok térnek el." if paros else "")}
            h, p = stats.kruskal(*mintak)
            n = sum(len(m) for m in mintak)
            eps2 = (float(h) - len(mintak) + 1) / (n - len(mintak)) if n > len(mintak) else float("nan")
            paros = None
            if p < 0.05 and len(mintak) <= 8:
                paros = []
                m = len(nevek) * (len(nevek) - 1) // 2
                for i in range(len(nevek)):
                    for j in range(i + 1, len(nevek)):
                        u, pu = stats.mannwhitneyu(mintak[i], mintak[j], alternative="two-sided")
                        paros.append({"a": nevek[i], "b": nevek[j], "p_bonferroni": round(min(1.0, float(pu) * m), 6)})
            return {"muvelet": "kruskal_wallis", "csoportok": {k: _leiro(v) for k, v in cs.items()}, "H": round(float(h), 4),
                    "p": round(float(p), 6), "epsilon2": round(float(eps2), 4), "paros_mann_whitney_bonferroni": paros,
                    "figyelmeztetes": figy,
                    "magyarazat": f"Kruskal–Wallis próba, „{oszlop}” a „{csoport}” {len(cs)} szintje között: H = {float(h):.3f}, {_p_szoveg(float(p))}; "
                                  f"ε² = {eps2:.3f}." + (" A páronkénti Mann–Whitney (Bonferroni-korrigált) mutatja, hol a különbség." if paros else "")}

        if muv == "khi_negyzet":
            o1 = _oszlop_ellenoriz(tabla, args.get("oszlop"), "oszlop")
            o2 = _oszlop_ellenoriz(tabla, args.get("oszlop2") or args.get("csoport"), "oszlop2")
            j1, j2 = tabla["oszlopok"].index(o1), tabla["oszlopok"].index(o2)
            kont: dict = {}
            for r in tabla["sorok"]:
                a, b = str(r[j1]).strip(), str(r[j2]).strip()
                if a and b:
                    kont.setdefault(a, {}).setdefault(b, 0)
                    kont[a][b] += 1
            sorn, oszn = sorted(kont), sorted({b for v in kont.values() for b in v})
            if len(sorn) < 2 or len(oszn) < 2:
                return {"hiba": "A khi-négyzethez mindkét változónak legalább 2 szintje kell."}
            m = np.array([[kont[a].get(b, 0) for b in oszn] for a in sorn], float)
            chi2, p, dof, exp = stats.chi2_contingency(m)
            n = m.sum()
            v = math.sqrt(chi2 / (n * (min(m.shape) - 1))) if n > 0 else float("nan")
            kicsi = float((exp < 5).mean())
            if kicsi > 0.2:
                figy.append(f"A cellák {kicsi * 100:.0f} %-ában 5 alatti a várt gyakoriság — a khi-négyzet megbízhatatlan"
                            + (", a Fisher-egzakt próba a mérvadó." if m.shape == (2, 2) else "; vonj össze szinteket."))
            fisher = None
            if m.shape == (2, 2):
                odds, pf = stats.fisher_exact(m)
                fisher = {"esely_hanyados": round(float(odds), 4), "p": round(float(pf), 6)}
            return {"muvelet": muv, "sorok": sorn, "oszlopok": oszn, "kontingencia": m.astype(int).tolist(),
                    "chi2": round(float(chi2), 4), "p": round(float(p), 6), "df": int(dof), "cramer_v": round(v, 3),
                    "fisher": fisher, "figyelmeztetes": figy,
                    "magyarazat": f"Khi-négyzet függetlenségi próba „{o1}” × „{o2}” ({m.shape[0]}×{m.shape[1]} tábla, n={int(n)}): "
                                  f"χ² = {float(chi2):.3f}, df = {int(dof)}, {_p_szoveg(float(p))}; Cramér V = {v:.2f} ({_d_szoveg(v * 2)})."
                                  + (f" Fisher-egzakt: {_p_szoveg(fisher['p'])}." if fisher else "")}

        if muv in ("korrelacio", "regresszio"):
            o1 = _oszlop_ellenoriz(tabla, args.get("oszlop"), "oszlop", True)
            o2 = _oszlop_ellenoriz(tabla, args.get("oszlop2"), "oszlop2", True)
            j1, j2 = tabla["oszlopok"].index(o1), tabla["oszlopok"].index(o2)
            parok = [(szam(r[j1]), szam(r[j2])) for r in tabla["sorok"]]
            parok = [(a, b) for a, b in parok if a is not None and b is not None]
            if len(parok) < 3:
                return {"hiba": "Legalább 3 teljes (x, y) pár kell."}
            x, y = np.array([p[0] for p in parok]), np.array([p[1] for p in parok])
            if muv == "korrelacio":
                r, pr = stats.pearsonr(x, y)
                rho, ps = stats.spearmanr(x, y)
                return {"muvelet": muv, "n": len(parok), "pearson_r": round(float(r), 4), "pearson_p": round(float(pr), 6),
                        "spearman_rho": round(float(rho), 4), "spearman_p": round(float(ps), 6),
                        "magyarazat": f"Korreláció „{o1}” és „{o2}” között (n={len(parok)}): Pearson r = {float(r):.3f} ({_p_szoveg(float(pr))}), "
                                      f"Spearman ρ = {float(rho):.3f} ({_p_szoveg(float(ps))}). "
                                      f"Az összefüggés {'erős' if abs(r) >= 0.7 else 'közepes' if abs(r) >= 0.4 else 'gyenge'}; a korreláció nem ok-okozat."}
            lr = stats.linregress(x, y)
            return {"muvelet": muv, "n": len(parok), "meredekseg": round(float(lr.slope), 6), "tengelymetszet": round(float(lr.intercept), 6),
                    "r2": round(float(lr.rvalue ** 2), 4), "p": round(float(lr.pvalue), 6), "meredekseg_se": round(float(lr.stderr), 6),
                    "magyarazat": f"Lineáris regresszió: {o2} = {float(lr.intercept):.4g} + {float(lr.slope):.4g} × {o1} (n={len(parok)}); "
                                  f"R² = {float(lr.rvalue ** 2):.3f}, a meredekség {_p_szoveg(float(lr.pvalue))}."}
    except ValueError as e:
        return {"hiba": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.exception("statisztika hiba")
        return {"hiba": f"A számítás hibára futott: {type(e).__name__}: {e}"}
    return {"hiba": "ismeretlen művelet"}


def futtat(conn, instance: str, args: dict) -> dict:
    """A `statisztika` eszköz: a SAJÁT feltöltésen (yr_chat_files) számol."""
    fid = (args.get("file_id") or "").strip()
    if not fid:
        return {"hiba": "Hiányzik a file_id — a feltöltött táblázat azonosítója (a rendszerüzenetben szerepel)."}
    try:
        row = conn.execute("SELECT filename, kind, tabla_json FROM yr_chat_files WHERE id=? AND instance=?",
                           (fid, instance)).fetchone()
    except Exception as e:  # noqa: BLE001
        return {"hiba": f"A feltöltés nem olvasható: {e}"}
    if not row:
        return {"hiba": "Nincs ilyen feltöltött fájl ezen a felületen (a file_id nem a tiéd, vagy lejárt)."}
    tj = row["tabla_json"] if isinstance(row, dict) or hasattr(row, "keys") else row[2]
    if not tj:
        return {"hiba": "Ez a fájl nem táblázat (CSV/TSV/XLSX kell a statisztikához)."}
    try:
        tabla = json.loads(tj)
    except Exception:  # noqa: BLE001
        return {"hiba": "A táblázat adata sérült."}
    ki = szamol(tabla, args)
    ki["file"] = row["filename"] if hasattr(row, "keys") else row[0]
    return ki


# ── ÁLTALÁNOS KUTATÁS: homokozott Python ─────────────────────────────────
#
# Kommandant 2026-09-21: „Oldd meg generálisan. Tehát TUDJON ez a szegény,
# ugyanakkor rendkívül intelligens lány kutatni." A fix próba-lista nem
# elég: a modell ÍRJA a kódot (numpy, scipy, matplotlib), a feltöltött
# tábla `adat` néven betöltve, és az stdout + az ábrák visszamennek a
# chatbe. Homokozó: külön processz (`python -I`), hálózat kikapcsolva
# (socket-monkeypatch), erőforrás-korlátok (CPU 30 s, memória 768 MB,
# fájl 20 MB), üres környezet, saját temp könyvtár, 45 s falióra.
# A kódot a modell írja — ezért a korlátok a védelem, nem a bizalom.

PY_IDOKORLAT_S = 45
PY_STDOUT_MAX = 20_000
PY_ABRA_MAX = 6

_RUNNER = r'''
import sys, os, io, json, resource, socket, builtins, contextlib
resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1"); os.environ.setdefault("OMP_NUM_THREADS", "1"); os.environ.setdefault("MKL_NUM_THREADS", "1")
resource.setrlimit(resource.RLIMIT_AS, (2048 * 1024 * 1024, 2048 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_FSIZE, (20 * 1024 * 1024, 20 * 1024 * 1024))
def _tiltva(*a, **k):
    raise OSError("Ebben a homokozóban nincs hálózat.")
socket.socket = _tiltva
socket.create_connection = _tiltva
os.environ["MPLBACKEND"] = "Agg"
import numpy as np
from scipy import stats as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
tabla = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 and sys.argv[1] != "-" else None
oszlopok = tabla["oszlopok"] if tabla else []
def _szam(c):
    try:
        if c is None or c == "":
            return None
        if isinstance(c, (int, float)):
            return float(c)
        s = str(c).strip().replace(" ", "")
        if "," in s and "." not in s:
            s = s.replace(",", ".")
        return float(s)
    except Exception:
        return None
adat = []
if tabla:
    for r in tabla["sorok"]:
        sor = {}
        for j, o in enumerate(oszlopok):
            v = r[j] if j < len(r) else None
            sor[o] = _szam(v) if tabla["tipus"].get(o) == "szam" else (None if v in ("", None) else v)
        adat.append(sor)
def oszlop(nev):
    """Egy oszlop értékei listaként (számoszlopnál float, a hiányzók kihagyva)."""
    return [r[nev] for r in adat if r.get(nev) is not None]
kod = open(sys.argv[2], encoding="utf-8").read()
ki = io.StringIO()
with contextlib.redirect_stdout(ki):
    try:
        exec(compile(kod, "<kod>", "exec"), {"np": np, "st": st, "stats": st, "plt": plt, "adat": adat,
                                              "oszlopok": oszlopok, "tabla": tabla, "oszlop": oszlop,
                                              "json": json, "print": print, "__name__": "__main__"})
    except SystemExit:
        pass
    except Exception as e:
        import traceback
        print("HIBA:", type(e).__name__ + ":", e)
        tb = traceback.format_exc().splitlines()
        print("\n".join(tb[-4:]))
n = 0
for num in plt.get_fignums():
    if n >= %(abra_max)d:
        break
    fig = plt.figure(num)
    fig.savefig(os.path.join(sys.argv[3], "abra_%%d.png" %% n), dpi=110, bbox_inches="tight")
    n += 1
sys.stdout.write(ki.getvalue()[:%(stdout_max)d])
''' % {"abra_max": PY_ABRA_MAX, "stdout_max": PY_STDOUT_MAX}


def python_futtat(conn, instance: str, args: dict, img_dir) -> dict:
    """A `python_futtatas` eszköz. Visszaad: {stdout, hiba?, abrak: [fájlnév]}."""
    import shutil
    import subprocess
    import sys
    import tempfile
    import uuid
    kod = (args.get("kod") or "").strip()
    if not kod:
        return {"hiba": "Üres a kód."}
    if len(kod) > 20_000:
        return {"hiba": "Túl hosszú kód (max 20 000 karakter)."}
    fid = (args.get("file_id") or "").strip()
    tabla_json = None
    if fid:
        try:
            row = conn.execute("SELECT tabla_json FROM yr_chat_files WHERE id=? AND instance=?",
                               (fid, instance)).fetchone()
        except Exception as e:  # noqa: BLE001
            return {"hiba": f"A feltöltés nem olvasható: {e}"}
        if not row:
            return {"hiba": "Nincs ilyen feltöltött fájl ezen a felületen."}
        tabla_json = row["tabla_json"] if hasattr(row, "keys") else row[0]
        if not tabla_json:
            return {"hiba": "Ez a fájl nem táblázat — `adat` nélkül fut a kód, ha ez a cél, hagyd el a file_id-t."}
    munka = tempfile.mkdtemp(prefix="yrpy_")
    try:
        runner = os.path.join(munka, "runner.py")
        kodfajl = os.path.join(munka, "kod.py")
        tablafajl = os.path.join(munka, "tabla.json")
        with open(runner, "w", encoding="utf-8") as f:
            f.write(_RUNNER)
        with open(kodfajl, "w", encoding="utf-8") as f:
            f.write(kod)
        if tabla_json:
            with open(tablafajl, "w", encoding="utf-8") as f:
                f.write(tabla_json)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": munka, "MPLCONFIGDIR": munka,
               "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8",
               # OpenBLAS a CPU-szám szerint foglal szál-puffert → a címtér-korlát alatt
               # „Memory allocation still failed" (mérve a Railway-konténerben 2026-09-21)
               "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
        try:
            cp = subprocess.run([sys.executable, "-I", runner, tablafajl if tabla_json else "-", kodfajl, munka],
                                cwd=munka, env=env, capture_output=True, text=True, timeout=PY_IDOKORLAT_S)
        except subprocess.TimeoutExpired:
            return {"hiba": f"A kód nem fejeződött be {PY_IDOKORLAT_S} másodperc alatt — egyszerűsítsd, vagy szűkítsd az adatot."}
        abrak = []
        for nev in sorted(os.listdir(munka)):
            if nev.startswith("abra_") and nev.endswith(".png"):
                cel = f"{instance}_{uuid.uuid4().hex}.png"
                try:
                    shutil.copyfile(os.path.join(munka, nev), str(img_dir / cel))
                    abrak.append(cel)
                except Exception as e:  # noqa: BLE001
                    logger.warning("ábra mentése bukott: %s", e)
        ki = {"stdout": (cp.stdout or "")[:PY_STDOUT_MAX], "abrak": abrak, "_abrak": abrak}
        if cp.returncode != 0:
            ki["hiba"] = (cp.stderr or "")[-1500:] or f"kilépési kód {cp.returncode}"
        if not ki["stdout"] and not abrak and "hiba" not in ki:
            ki["megjegyzes"] = "A kód lefutott, de nem írt ki semmit (print) és nem rajzolt ábrát."
        if abrak:
            ki["megjegyzes_abra"] = f"{len(abrak)} ábra elkészült, a felhasználó látja a chatben."
        return ki
    finally:
        shutil.rmtree(munka, ignore_errors=True)
