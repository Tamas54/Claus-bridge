"""plugins/delphoi_brief.py — PYTHIA B1: BRIEF-RÉTEG (a megrendelés nyelve).

A brief-séma az EGYETLEN igazságforrás: az Echolot /delphoi/brief űrlap, a
Bridge REST-végpontok, a tracking-runner és a riport-mű MIND ebből a modulból
validál. A séma kézzel írt validátorral él (nincs jsonschema-függőség — a
requirements-fegyelem miatt), de a szabályok deklaratívak és tesztek alatt.

VASSZABÁLYOK:
  - spec_hash = a KANONIKUS (normalizált, kulcs-rendezett) JSON sha256-a —
    ugyanaz a brief MINDIG ugyanazt a hash-t adja (idempotencia + audit).
  - Szegmens-tengely CSAK kor/település/jövedelem. Nem/etnikum tengely TILOS —
    a validátor hangosan utasítja el (nem csendben ejti).
  - custom_questions: max 3, mindegyik validated:false — SSR-horgony nélkül,
    a riportban KÜLÖN "exploratív — nem validált" fejezetben.
  - Dimenzió-bank: MINDEN engedélyezett dimenzióhoz natív SSR-referenciakészlet
    (1=erősen negatív … 5=erősen pozitív). Az új horgonyok (clarity,
    credibility, purchase_intent-hu, price_sensitivity) kézzel írt natív
    szövegek, nem tükörfordítások (a ssr.py G0d-minőségminta szerint).

A brief → job leképezés a delphoi.create_job/process_job MEGLÉVŐ útját
használja (import, nem másolat); a brief-többlet (dimensions, segments, goal,
brief_id) a panel_spec JSON-ban utazik — a motor a számára ismeretlen kulcsokat
békén hagyja, a riport-mű viszont visszaolvassa.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("plugins.delphoi_brief")

__plugin_meta__ = {
    "name": "delphoi_brief",
    "version": "1.0.0",
    "description": "DELPHOI brief-reteg (B1) — brief-sema + validator + spec_hash + dimenzio-bank",
}

_DEPS: dict | None = None

# ---------------------------------------------------------------------------
# A SÉMA (egyetlen igazságforrás)
# ---------------------------------------------------------------------------
ALLOWED_INSTRUMENTS = ("ab_test", "concept", "product_desc", "yt_title",
                       "pitch", "custom_brief")
ALLOWED_DIMENSIONS = ("appeal", "clarity", "credibility", "purchase_intent",
                      "price_sensitivity")
ALLOWED_SEGMENT_AXES = ("age", "settlement", "income")
# Nem/etnikum (és rokon) tengelyek: HANGOS tiltás — nem szűrünk rájuk.
FORBIDDEN_SEGMENT_AXES = ("gender", "sex", "nem", "ethnicity", "etnikum",
                          "race", "rassz", "nationality", "nemzetiseg")
TRACKING_CADENCES = ("none", "weekly", "monthly")
REPORT_LANGS = ("hu", "en")
REPORT_FORMATS = ("pdf", "json", "csv")
MAX_CUSTOM_QUESTIONS = 3
MIN_N, MAX_N = 30, 500
MAX_STIMULI = 8

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# instrument → a motor (delphoi.VALID_INPUT_KINDS) input_kind-ja.
# A custom_brief a motor felé 'concept'-ként fut (a brief-többlet a specben él).
INSTRUMENT_TO_INPUT_KIND = {
    "ab_test": "ab_test", "concept": "concept", "product_desc": "product_desc",
    "yt_title": "yt_title", "pitch": "pitch", "custom_brief": "concept",
}


def _fg_countries() -> tuple:
    from plugins import delphoi
    return delphoi.FG_COUNTRIES


# ---------------------------------------------------------------------------
# DIMENZIÓ-BANK — natív SSR-referenciakészletek (1 → 5: negatív → pozitív).
# appeal: a delphoi.REFERENCE_SETS_APPEAL (hu/cs/pt/pl) + itt az en;
# purchase_intent: en = ssr.REFERENCE_SETS_EN["purchase_intent"], hu itt;
# clarity / credibility / price_sensitivity: új készletek hu+en
# (a REFERENCE_SETS_PRICE tapasztalati-regiszter mintájára).
# ---------------------------------------------------------------------------
DIMENSION_BANK: dict = {
    "appeal": {
        "en": [
            "This does not appeal to me at all, I would definitely not choose it.",
            "It is not really attractive to me.",
            "I am neutral about it, maybe yes, maybe no.",
            "It is quite attractive, I would probably give it a try.",
            "It is very attractive, I would definitely choose it.",
        ],
        # hu/cs/pt/pl: futásidőben a delphoi.REFERENCE_SETS_APPEAL-ból (import).
    },
    "clarity": {
        "hu": [
            "Egyáltalán nem értem, hogy miről szól — teljesen zavaros.",
            "Nagyrészt homályos maradt, hogy mit ajánl.",
            "Nagyjából értem, de maradtak kérdőjelek.",
            "Többnyire világos, csak egy-két apróság bizonytalan.",
            "Elsőre teljesen világos, pontosan értem, mit kínál.",
        ],
        "en": [
            "I have no idea what this is about — it is completely confusing.",
            "Most of what it offers stayed unclear to me.",
            "I roughly get it, but some question marks remain.",
            "It is mostly clear, only a detail or two feels uncertain.",
            "It is perfectly clear at first read, I know exactly what it offers.",
        ],
    },
    "credibility": {
        "hu": [
            "Ezt egyáltalán nem hiszem el — túlzónak és megalapozatlannak tartom.",
            "Inkább kételkedem benne, hogy igaz, amit állít.",
            "Nem tudom eldönteni, hihető-e.",
            "Nagyjából hitelesnek tűnik, amit állít.",
            "Teljesen hitelesnek tartom, megalapozottnak hat.",
        ],
        "en": [
            "I do not believe this at all — it sounds exaggerated and unfounded.",
            "I rather doubt that what it claims is true.",
            "I cannot decide whether it is believable.",
            "What it claims seems fairly credible.",
            "I find it fully credible, it comes across as well-founded.",
        ],
    },
    "purchase_intent": {
        "hu": [
            "Biztosan nem venném meg.",
            "Valószínűleg nem venném meg.",
            "Lehet, hogy megvenném, lehet, hogy nem.",
            "Valószínűleg megvenném.",
            "Biztosan megvenném.",
        ],
        # en: futásidőben a ssr.REFERENCE_SETS_EN["purchase_intent"]-ből.
    },
    "price_sensitivity": {
        "hu": [
            "Ezen az áron biztosan nem venném meg — túl drágának tartom.",
            "Az árát soknak érzem ahhoz képest, amit kínál.",
            "Az ára nagyjából arányos azzal, amit kínál.",
            "Az árát kedvezőnek érzem ahhoz képest, amit kínál.",
            "Ezen az áron kifejezetten jó vétel — gond nélkül kifizetném.",
        ],
        "en": [
            "At this price I would definitely not buy it — I find it too expensive.",
            "The price feels high for what it offers.",
            "The price feels roughly proportionate to what it offers.",
            "The price feels favourable for what it offers.",
            "At this price it is a really good deal — I would pay it without hesitation.",
        ],
    },
}


def get_dimension_anchors(dimension: str, lang: str) -> list[str]:
    """Natív horgony-készlet egy dimenzióhoz. Feloldási sor: saját bank →
    meglévő ssr/delphoi készletek → en → hu. Ismeretlen dimenzió: hangos hiba."""
    if dimension not in ALLOWED_DIMENSIONS:
        raise ValueError(f"ismeretlen dimenzió: {dimension!r} — engedélyezett: {ALLOWED_DIMENSIONS}")
    bank = DIMENSION_BANK.get(dimension, {})
    if lang in bank:
        return list(bank[lang])
    if dimension == "appeal":
        from plugins import delphoi
        if lang in delphoi.REFERENCE_SETS_APPEAL:
            return list(delphoi.REFERENCE_SETS_APPEAL[lang])
    if dimension == "purchase_intent" and lang == "en":
        from plugins import ssr
        return list(ssr.REFERENCE_SETS_EN["purchase_intent"])
    # fallback-lánc: en → hu (dokumentált; új nyelv = új bank-sor, nem kód)
    for fb in ("en", "hu"):
        if fb in bank:
            return list(bank[fb])
        if dimension == "appeal" and fb == "hu":
            from plugins import delphoi
            return list(delphoi.REFERENCE_SETS_APPEAL["hu"])
        if dimension == "purchase_intent" and fb == "en":
            from plugins import ssr
            return list(ssr.REFERENCE_SETS_EN["purchase_intent"])
    raise ValueError(f"nincs horgony-készlet: {dimension}/{lang}")


# ---------------------------------------------------------------------------
# KREDIT-BECSLÉS — a képlet CONFIG-DICT-ből, PLACEHOLDER számokkal (az árazás
# Kommandant-döntés; a tényleges terhelést a delphoi.job_cost adja job-felvételkor).
# f(N, dimenziószám, custom-szám): calls = N × stimulusszám ×
#   (base + extra_dim×(dim−1) + custom×q) → kredit = ceil(calls / calls_per_credit).
# ---------------------------------------------------------------------------
ESTIMATE_CONFIG = {
    "calls_per_persona_base": 1.0,      # PLACEHOLDER: 1 alap-kérdés / persona
    "calls_per_extra_dimension": 1.0,   # PLACEHOLDER: minden további dimenzió
    "calls_per_custom_question": 1.0,   # PLACEHOLDER: minden exploratív kérdés
    "calls_per_credit": int(os.environ.get("DELPHOI_CALLS_PER_CREDIT", "30")),
}


def estimate_credits(n: int, n_dimensions: int, n_custom: int,
                     n_stimuli: int = 1, config: dict | None = None) -> int:
    import math
    c = config or ESTIMATE_CONFIG
    calls_per_persona = (c["calls_per_persona_base"]
                         + c["calls_per_extra_dimension"] * max(0, int(n_dimensions) - 1)
                         + c["calls_per_custom_question"] * max(0, int(n_custom)))
    calls = max(1, int(n)) * max(1, int(n_stimuli)) * calls_per_persona
    return max(1, math.ceil(calls / max(1, c["calls_per_credit"])))


# ---------------------------------------------------------------------------
# KANONIKALIZÁLÁS + VALIDÁTOR + spec_hash
# ---------------------------------------------------------------------------
def canonicalize_spec(spec: dict) -> dict:
    """Normalizált brief-spec: defaultok kitöltve, listák determinista sorrendben
    (a stimuli sorrendje TARTALMI — az marad), custom_questions egységes
    {text, validated:false} alakban. A spec_hash ERRE a formára számolódik."""
    spec = dict(spec or {})
    dims_in = spec.get("dimensions") or ["appeal"]
    dims = [d for d in ALLOWED_DIMENSIONS if d in dims_in]
    segs_in = spec.get("segments") or {}
    segments = {}
    if isinstance(segs_in, dict):
        for axis in ALLOWED_SEGMENT_AXES:
            vals = segs_in.get(axis)
            if vals:
                segments[axis] = sorted(str(v).strip() for v in vals if str(v).strip())
    customs = []
    for q in (spec.get("custom_questions") or []):
        text = (q.get("text") if isinstance(q, dict) else str(q)).strip()
        if text:
            customs.append({"text": text, "validated": False})
    rep_in = spec.get("report") or {}
    fmts_in = rep_in.get("formats") or ["pdf"]
    report = {
        "lang": str(rep_in.get("lang") or "hu").lower(),
        "formats": [f for f in REPORT_FORMATS if f in fmts_in],
        "notify_email": str(rep_in.get("notify_email") or "").strip(),
    }
    return {
        "goal": str(spec.get("goal") or "").strip(),
        "instrument": str(spec.get("instrument") or "").strip(),
        "stimuli": [str(s).strip() for s in (spec.get("stimuli") or []) if str(s).strip()],
        "country": str(spec.get("country") or "HU").upper(),
        "n": int(spec.get("n") or MIN_N),
        "segments": segments,
        "dimensions": dims,
        "custom_questions": customs,
        "tracking": str(spec.get("tracking") or "none").lower(),
        "report": report,
    }


def validate_brief(spec: dict) -> list[str]:
    """Hibalista a NYERS specre (üres lista = érvényes). A kanonikalizálás előtt
    fut a tiltott-tengely ellenőrzés — a tiltott kulcs nem 'esik ki csendben'."""
    errors: list[str] = []
    raw_segs = (spec or {}).get("segments") or {}
    if isinstance(raw_segs, dict):
        for axis in raw_segs:
            a = str(axis).strip().lower()
            if a in FORBIDDEN_SEGMENT_AXES:
                errors.append(f"tiltott szegmens-tengely: {axis!r} — nem/etnikum "
                              "szerinti szűrés nem támogatott (szegmens-doktrína)")
            elif a not in ALLOWED_SEGMENT_AXES:
                errors.append(f"ismeretlen szegmens-tengely: {axis!r} — "
                              f"engedélyezett: {ALLOWED_SEGMENT_AXES}")
    raw_dims = (spec or {}).get("dimensions")
    for d in (raw_dims or []):
        if d not in ALLOWED_DIMENSIONS:
            errors.append(f"ismeretlen dimenzió: {d!r} — engedélyezett: {ALLOWED_DIMENSIONS}")
    c = canonicalize_spec(spec)
    if not c["goal"]:
        errors.append("hiányzó goal (a brief célja kötelező)")
    if len(c["goal"]) > 500:
        errors.append("goal túl hosszú (max 500 karakter)")
    if c["instrument"] not in ALLOWED_INSTRUMENTS:
        errors.append(f"ismeretlen instrument: {c['instrument']!r} — "
                      f"engedélyezett: {ALLOWED_INSTRUMENTS}")
    n_stim = len(c["stimuli"])
    if c["instrument"] in ("ab_test", "yt_title"):
        if n_stim < 2:
            errors.append(f"{c['instrument']}: legalább 2 stimulus kell")
    elif c["instrument"] in ALLOWED_INSTRUMENTS and n_stim != 1:
        errors.append(f"{c['instrument']}: pontosan 1 stimulus kell (kaptunk: {n_stim})")
    if n_stim > MAX_STIMULI:
        errors.append(f"túl sok stimulus: {n_stim} (max {MAX_STIMULI})")
    if c["country"] not in _fg_countries():
        errors.append(f"nem validált ország: {c['country']} "
                      f"(elérhető: {','.join(_fg_countries())})")
    if not (MIN_N <= c["n"] <= MAX_N):
        errors.append(f"n a megengedett sávon kívül: {c['n']} ({MIN_N}–{MAX_N})")
    if not c["dimensions"]:
        errors.append("legalább 1 dimenzió kell")
    n_customs = len((spec or {}).get("custom_questions") or [])
    if n_customs > MAX_CUSTOM_QUESTIONS:
        errors.append(f"túl sok custom_question: {n_customs} (max {MAX_CUSTOM_QUESTIONS})")
    for q in c["custom_questions"]:
        if len(q["text"]) > 300:
            errors.append("custom_question túl hosszú (max 300 karakter)")
    if c["tracking"] not in TRACKING_CADENCES:
        errors.append(f"ismeretlen tracking: {c['tracking']!r} — {TRACKING_CADENCES}")
    if c["report"]["lang"] not in REPORT_LANGS:
        errors.append(f"ismeretlen riport-nyelv: {c['report']['lang']!r} — {REPORT_LANGS}")
    if not c["report"]["formats"]:
        errors.append("legalább 1 riport-formátum kell (pdf|json|csv)")
    email = c["report"]["notify_email"]
    if email and not _EMAIL_RE.match(email):
        errors.append(f"érvénytelen notify_email: {email!r}")
    return errors


def spec_hash(spec: dict) -> str:
    """A KANONIKUS spec kulcs-rendezett, kompakt JSON-jának sha256-a.
    Ugyanaz a brief (kulcs-sorrendtől, whitespace-től függetlenül) mindig
    ugyanazt a hash-t adja."""
    canonical = json.dumps(canonicalize_spec(spec), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# SÉMA (DB) — a migrate_pythia_b1.py ugyanEZT hozza létre éles DB-n; az
# ensure_brief_tables az app-indulás/tesztek idempotens útja (delphoi-minta).
# ---------------------------------------------------------------------------
BRIEF_INIT_SQL = """
CREATE TABLE IF NOT EXISTS delphoi_briefs (
    brief_id   TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    spec_json  TEXT NOT NULL,
    spec_hash  TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delphoi_briefs_user
    ON delphoi_briefs(user_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_delphoi_briefs_user_hash
    ON delphoi_briefs(user_id, spec_hash);

CREATE TABLE IF NOT EXISTS delphoi_tracking (
    brief_id TEXT PRIMARY KEY REFERENCES delphoi_briefs(brief_id),
    cadence  TEXT NOT NULL CHECK(cadence IN ('weekly','monthly')),
    next_run TEXT NOT NULL,
    active   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS delphoi_reports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    format     TEXT NOT NULL,
    path       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delphoi_reports_job ON delphoi_reports(job_id);

CREATE TABLE IF NOT EXISTS delphoi_brief_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id      TEXT NOT NULL,
    job_id        TEXT NOT NULL,
    run_at        TEXT NOT NULL,
    spec_hash     TEXT NOT NULL,
    overall_score REAL
);
CREATE INDEX IF NOT EXISTS idx_delphoi_brief_runs_brief
    ON delphoi_brief_runs(brief_id, run_at);
"""


def ensure_brief_tables(conn) -> None:
    conn.executescript(BRIEF_INIT_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# CRUD — brief mentés/betöltés (a REST-végpontok és a tracking-runner útja)
# ---------------------------------------------------------------------------
def save_brief(get_db, user_id: str, spec: dict) -> dict:
    """Validálás → kanonikalizálás → mentés. Azonos (user, spec_hash) páros
    IDEMPOTENS: a meglévő brief_id jön vissza (created=False). tracking≠none →
    delphoi_tracking sor (next_run=most — az első futás a következő tickben)."""
    errors = validate_brief(spec)
    if errors:
        return {"ok": False, "error": "invalid_brief", "errors": errors}
    canonical = canonicalize_spec(spec)
    h = spec_hash(canonical)
    ts = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT brief_id FROM delphoi_briefs WHERE user_id=? AND spec_hash=?",
            (str(user_id), h)).fetchone()
        if existing:
            return {"ok": True, "brief_id": existing["brief_id"], "spec_hash": h,
                    "created": False}
        brief_id = "brf-" + uuid.uuid4().hex[:10]
        conn.execute(
            "INSERT INTO delphoi_briefs (brief_id, user_id, spec_json, spec_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (brief_id, str(user_id), json.dumps(canonical, ensure_ascii=False), h, ts))
        if canonical["tracking"] in ("weekly", "monthly"):
            conn.execute(
                "INSERT OR REPLACE INTO delphoi_tracking (brief_id, cadence, next_run, active) "
                "VALUES (?, ?, ?, 1)", (brief_id, canonical["tracking"], ts))
        conn.commit()
        return {"ok": True, "brief_id": brief_id, "spec_hash": h, "created": True}
    finally:
        conn.close()


def get_brief(get_db, brief_id: str, user_id: str) -> dict:
    """CSAK a tulajdonosnak (a FOGÁS-siló authz-mintája)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM delphoi_briefs WHERE brief_id=?",
                           (brief_id,)).fetchone()
        if not row or row["user_id"] != str(user_id):
            return {"ok": False, "error": "not_found"}
        tr = conn.execute("SELECT cadence, next_run, active FROM delphoi_tracking "
                          "WHERE brief_id=?", (brief_id,)).fetchone()
        runs = [dict(r) for r in conn.execute(
            "SELECT job_id, run_at, overall_score FROM delphoi_brief_runs "
            "WHERE brief_id=? ORDER BY run_at ASC", (brief_id,)).fetchall()]
    finally:
        conn.close()
    return {"ok": True, "brief_id": row["brief_id"],
            "spec": json.loads(row["spec_json"]), "spec_hash": row["spec_hash"],
            "created_at": row["created_at"],
            "tracking": dict(tr) if tr else None, "runs": runs}


def list_briefs(get_db, user_id: str, limit: int = 20) -> list:
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT b.brief_id, b.spec_hash, b.created_at, "
            "  json_extract(b.spec_json, '$.goal') AS goal, "
            "  json_extract(b.spec_json, '$.instrument') AS instrument, "
            "  json_extract(b.spec_json, '$.tracking') AS tracking "
            "FROM delphoi_briefs b WHERE b.user_id=? "
            "ORDER BY b.created_at DESC LIMIT ?", (str(user_id), int(limit))).fetchall()]
    finally:
        conn.close()


def brief_to_job_args(spec: dict, brief_id: str = "") -> tuple:
    """Kanonikus brief-spec → a delphoi.create_job argumentumai:
    (input_kind, input_text, panel_spec, input_variants). A brief-többlet
    (brief_id, goal, dimensions, segments, custom_questions) a panel_spec-ben
    utazik — a motor nem nyúl hozzá, a riport-mű visszaolvassa."""
    c = canonicalize_spec(spec)
    kind = INSTRUMENT_TO_INPUT_KIND[c["instrument"]]
    if c["instrument"] in ("ab_test", "yt_title"):
        input_text, variants = "", list(c["stimuli"])
    else:
        input_text, variants = c["stimuli"][0], None
    panel_spec = {
        "country": c["country"], "n_per_cell": c["n"], "n_seeds": 1,
        "brief_id": brief_id, "goal": c["goal"], "instrument": c["instrument"],
        "dimensions": c["dimensions"], "segments": c["segments"],
        "custom_questions": c["custom_questions"], "report": c["report"],
    }
    return kind, input_text, panel_spec, variants


def register_tools(app, deps):
    """Nem regisztrál MCP-toolt (tool-count fegyelem) — séma-őr + könyvtár.
    App-induláskor idempotensen létrehozza a brief-táblákat (delphoi-minta);
    éles DB-n a migrate_pythia_b1.py a kanonikus út (backup + verify)."""
    global _DEPS
    _DEPS = deps
    try:
        conn = deps["get_db"]()
        try:
            ensure_brief_tables(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — táblák nélkül a REST-út hangosan hasal
        logger.exception("delphoi_brief ensure_brief_tables failed")
    logger.info("delphoi_brief betöltve (%d dimenzió, %d instrument)",
                len(ALLOWED_DIMENSIONS), len(ALLOWED_INSTRUMENTS))
