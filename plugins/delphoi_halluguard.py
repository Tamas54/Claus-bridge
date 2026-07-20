"""plugins/delphoi_halluguard.py — HALLUCINÁCIÓ-ŐR (PYTHIA P1).

Heurisztikus jelző az éles nowcast-kimeneten: a persona-válaszokban /
szintézisben FORRÁSON TÚLI konkrét állítás gyanúját jelzi — számok, tulajdon-
nevek, dátumok, amelyek a grounding-korpuszban (datált hír-kontextus + priming
+ entitás-címke) nem szerepelnek.

SZÁNDÉKOSAN OLCSÓ: tiszta regex/string-heurisztika, LLM-hívás NÉLKÜL.
Ez egy JELZŐ (gyanú-lista + log + heti összesítő), nem ítélet — a fals pozitív
vállalt ár (pl. ragozott alak, amit a nyers stem-illesztés nem fog meg).

A cron-integráció (heti összesítő kiküldése) a P1-deploy feladata; itt a
scan/log/summary függvények + a tábla élnek.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("plugins.delphoi_halluguard")

__plugin_meta__ = {
    "name": "delphoi_halluguard",
    "version": "1.0.0",
    "description": "DELPHOI hallucinacio-or — forrason tuli konkret allitasok heurisztikus jelzoje (LLM nelkul)",
}

_DEPS: dict | None = None

_GUARD_SQL = """
CREATE TABLE IF NOT EXISTS delphoi_halluguard_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_kind     TEXT NOT NULL,
    entity_key   TEXT NOT NULL,
    country      TEXT NOT NULL,
    corpus_hash  TEXT,
    n_texts      INTEGER NOT NULL,
    n_suspect_texts INTEGER NOT NULL,
    n_suspects   INTEGER NOT NULL,
    report_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_halluguard_created ON delphoi_halluguard_log(created_at);
"""

# ── Extrakció ───────────────────────────────────────────────────────────────

# Szám: legalább 2 számjegy VAGY tizedes/százalék alak (az 1-5 skálapontok és
# kis sorszámok zaját kihagyjuk). Ezer-elválasztó és tizedesvessző normalizálva.
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?%?")
# Dátum: ISO (2026-07-20), év (19xx/20xx), magyar/európai hó-nap minták nem
# kellenek külön — az év-token önmagában elég erős korszak-jelző.
_DATE_RE = re.compile(r"\b(?:19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?\b")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
_WORD_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*", re.UNICODE)


def _norm(s: str) -> str:
    """Kisbetűs, ékezet-őrző normalizálás membership-checkhez."""
    return unicodedata.normalize("NFC", (s or "")).lower()


def _norm_number(tok: str) -> str:
    """'1 250,5%' → '1250.5%' — kanonikus számalak az összevetéshez."""
    return tok.replace(" ", "").replace(" ", "").replace(",", ".")


def extract_claims(text: str) -> dict:
    """Konkrét állítás-jelöltek egy szövegből: {numbers, dates, names} (set-ek).
    Tulajdonnév-heurisztika: MONDATON BELÜLI nagybetűs token(-sorozat) — a
    mondatkezdő nagybetű nem jelölt. Kötőjeles és többszavas nevek egyben."""
    numbers: set = set()
    dates: set = set()
    names: set = set()
    for m in _DATE_RE.finditer(text or ""):
        dates.add(m.group(0))
    for m in _NUM_RE.finditer(text or ""):
        tok = m.group(0)
        digits = re.sub(r"\D", "", tok)
        if len(digits) >= 2 or "%" in tok or "." in tok or "," in tok:
            if not _DATE_RE.fullmatch(tok):
                numbers.add(_norm_number(tok))
    for sent in _SENT_SPLIT_RE.split(text or ""):
        words = _WORD_RE.findall(sent)
        phrase: list = []
        for i, w in enumerate(words):
            if i > 0 and w[:1].isupper() and len(w) >= 3:
                phrase.append(w)
            else:
                if len(phrase) >= 1:
                    names.add(" ".join(phrase))
                phrase = []
        if phrase:
            names.add(" ".join(phrase))
    return {"numbers": numbers, "dates": dates, "names": names}


def _grounded_name(name: str, corpus_norm: str) -> bool:
    """Név-membership: teljes frázis VAGY minden token (nyers stemmel — az
    utolsó max. 3 karakter ragja levágva) megtalálható a korpuszban."""
    n = _norm(name)
    if n in corpus_norm:
        return True
    for tok in n.split(" "):
        stem = tok[: max(4, len(tok) - 3)]
        if len(stem) < 3 or stem not in corpus_norm:
            return False
    return True


def scan_text(text: str, corpus_norm: str, corpus_numbers: set,
              corpus_dates: set) -> list:
    """Egy szöveg gyanús (forráson túli) konkrétumai: [{claim, type}]."""
    claims = extract_claims(text)
    suspects = []
    for num in claims["numbers"]:
        if num not in corpus_numbers and num.rstrip("%") not in corpus_numbers:
            suspects.append({"claim": num, "type": "number"})
    for d in claims["dates"]:
        if d not in corpus_dates and d not in corpus_norm:
            suspects.append({"claim": d, "type": "date"})
    for name in claims["names"]:
        if not _grounded_name(name, corpus_norm):
            suspects.append({"claim": name, "type": "name"})
    return suspects


def scan_reactions(texts: list, corpus_text: str, extra_grounding: str = "") -> dict:
    """A teljes panel-kimenet átvilágítása a grounding-korpusszal szemben.
    Visszaad: {n_texts, n_suspect_texts, suspects:[{claim,type,count}]}."""
    grounding = (corpus_text or "") + "\n" + (extra_grounding or "")
    corpus_norm = _norm(grounding)
    gclaims = extract_claims(grounding)
    corpus_numbers = set(gclaims["numbers"])
    corpus_dates = set(gclaims["dates"])
    agg: dict = {}
    n_suspect_texts = 0
    for t in texts:
        found = scan_text(t or "", corpus_norm, corpus_numbers, corpus_dates)
        if found:
            n_suspect_texts += 1
        for s in found:
            key = (s["claim"], s["type"])
            agg[key] = agg.get(key, 0) + 1
    suspects = sorted(
        [{"claim": c, "type": t, "count": n} for (c, t), n in agg.items()],
        key=lambda x: -x["count"])
    return {"n_texts": len(texts), "n_suspect_texts": n_suspect_texts,
            "suspects": suspects}


# ── Log + heti összesítő ────────────────────────────────────────────────────

def ensure_guard_table(conn) -> None:
    conn.executescript(_GUARD_SQL)
    conn.commit()


def log_flags(get_db, run_kind: str, entity_key: str, country: str,
              corpus_hash: str, report: dict) -> None:
    """Egy futás őr-jelentésének naplózása (üres gyanú-lista is naplózódik —
    a heti összesítő nevezője)."""
    conn = get_db()
    try:
        ensure_guard_table(conn)
        conn.execute(
            "INSERT INTO delphoi_halluguard_log (run_kind, entity_key, country, "
            "corpus_hash, n_texts, n_suspect_texts, n_suspects, report_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_kind, entity_key, country, corpus_hash or "",
             int(report.get("n_texts", 0)), int(report.get("n_suspect_texts", 0)),
             len(report.get("suspects", [])),
             json.dumps(report, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()
    if report.get("suspects"):
        logger.warning(
            "halluguard [%s/%s %s]: %d/%d szövegben forráson túli konkrétum-gyanú (top: %s)",
            run_kind, entity_key, country, report.get("n_suspect_texts", 0),
            report.get("n_texts", 0),
            ", ".join(s["claim"] for s in report["suspects"][:5]))


def weekly_summary(get_db, days: int = 7) -> dict:
    """Heti összesítő (cron-integráció a P1-deploykor): futás- és gyanú-számok
    entitásonként + a leggyakoribb gyanús konkrétumok."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = get_db()
    try:
        ensure_guard_table(conn)
        rows = conn.execute(
            "SELECT entity_key, country, n_texts, n_suspect_texts, report_json "
            "FROM delphoi_halluguard_log WHERE created_at >= ?", (cutoff,)).fetchall()
    finally:
        conn.close()
    per_entity: dict = {}
    top: dict = {}
    for r in rows:
        k = f"{r['entity_key']}/{r['country']}"
        e = per_entity.setdefault(k, {"runs": 0, "texts": 0, "suspect_texts": 0})
        e["runs"] += 1
        e["texts"] += r["n_texts"]
        e["suspect_texts"] += r["n_suspect_texts"]
        try:
            for s in json.loads(r["report_json"]).get("suspects", []):
                key = (s["claim"], s["type"])
                top[key] = top.get(key, 0) + int(s.get("count", 1))
        except Exception:  # noqa: BLE001
            continue
    return {
        "window_days": days, "runs": len(rows),
        "per_entity": per_entity,
        "top_suspects": sorted(
            [{"claim": c, "type": t, "count": n} for (c, t), n in top.items()],
            key=lambda x: -x["count"])[:20],
    }


def register_tools(app, deps):
    """Nem regisztrál MCP-toolt (tool-count fegyelem) — a deps-t és a táblát
    készíti elő; a heti cron-bekötés a P1-deploy lépése."""
    global _DEPS
    _DEPS = deps
    conn = deps["get_db"]()
    try:
        ensure_guard_table(conn)
    finally:
        conn.close()
    logger.info("delphoi_halluguard betöltve (tábla kész, cron-integráció deploykor)")
