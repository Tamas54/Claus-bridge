"""poll_results — ORAKEL-II ground truth + predictions store.

Holds (a) real Hungarian party-preference polls scraped from partpreferencia.hu
(quarterly PDFs, total-population base) as backtest ground truth, and later (b)
the Flash pollster's own predictions (kind='prediction'). The pollster reads
RAG/press_snapshots and writes here — kept OUTSIDE the unified agent RAG until
the method proves out.

Ground-truth data: partpreferencia.hu 2025 quarterly aggregates, "Teljes
népesség" (whole-population) base, major parties (Fidesz-KDNP, TISZA, DK,
Mi Hazánk) + Bizonytalan/NT-NV. Values vision-transcribed from the published
PDFs/PNG (small sub-1% parties omitted; can be refined from the source sheet).
Only 2025-Q3 (and 2026) are a CLEAN backtest zone — the Flash cutoff (~2025-01)
makes earlier quarters contaminated; stored for prior/trend reference only.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# (period, pollster, date_end, fidesz, tisza, dk, mihazank, bizonytalan)
# base = teljes_nepesseg. Source: partpreferencia.hu quarterly reports.
_GROUND_TRUTH_2025 = [
    # ── 2025-Q1 (Jan–Mar) — contaminated zone (cutoff), reference only ──
    ("2025-Q1", "Republikon",  "2025-04-01", 27, 29, 6, 5, 23),
    ("2025-Q1", "Závecz",      "2025-03-25", 26, 29, 5, 5, 22),
    ("2025-Q1", "Nézőpont",    "2025-03-12", 37, 25, 2, 4, 18),
    ("2025-Q1", "Publicus",    "2025-03-11", 26, 28, 6, 3, 33),
    ("2025-Q1", "Medián",      "2025-03-08", 29, 33, 2, 5, 24),
    ("2025-Q1", "Iránytű",     "2025-02-28", 32, 31, 3, 5, 22),
    ("2025-Q1", "Republikon",  "2025-02-26", 26, 28, 5, 5, 26),
    ("2025-Q1", "Publicus",    "2025-01-29", 25, 28, 6, 4, 32),
    ("2025-Q1", "Republikon",  "2025-01-22", 23, 26, 5, 5, 28),
    ("2025-Q1", "IDEA",        "2025-01-10", 26, 33, 4, 5, 26),
    # ── 2025-Q2 (Apr–Jun) — clean-ish (near cutoff edge) ──
    ("2025-Q2", "Publicus",          "2025-06-25", 25, 31, 6, 3, 30),
    ("2025-Q2", "21 Kutatóközpont",  "2025-06-27", 25, 32, 3, 5, 32),
    ("2025-Q2", "Závecz",            "2025-06-27", 26, 32, 4, 6, 24),
    ("2025-Q2", "Medián",            "2025-06-07", 28, 38, 2, 4, 22),
    ("2025-Q2", "Republikon",        "2025-06-03", 27, 32, 5, 6, 21),
    ("2025-Q2", "IDEA",              "2025-05-23", 25, 34, 4, 3, 28),
    ("2025-Q2", "Publicus",          "2025-05-16", 24, 30, 7, 3, 32),
    ("2025-Q2", "Republikon",        "2025-04-18", 28, 32, 6, 6, 18),
    ("2025-Q2", "Publicus",          "2025-04-09", 25, 28, 7, 3, 31),
    ("2025-Q2", "21 Kutatóközpont",  "2025-04-07", 28, 34, 2, 4, 25),
    # ── 2025-Q3 (Jul–Sep) — CLEAN backtest zone (post-cutoff) ──
    ("2025-Q3", "Publicus",          "2025-09-12", 26, 32, 5, 4, 31),
    ("2025-Q3", "IDEA",              "2025-09-06", 28, 35, 3, 3, 25),
    ("2025-Q3", "Medián",            "2025-09-04", 30, 37, 2, 4, 22),
    ("2025-Q3", "Závecz",            "2025-09-03", 28, 33, 4, 4, 22),
    ("2025-Q3", "21 Kutatóközpont",  "2025-08-31", 26, 30, 3, 5, 30),
    ("2025-Q3", "Republikon",        "2025-08-27", 26, 30, 5, 6, 24),
    ("2025-Q3", "Nézőpont",          "2025-08-18", 39, 26, 3, 4, 27),
    ("2025-Q3", "IDEA",              "2025-08-07", 29, 33, 4, 5, 27),
    ("2025-Q3", "Publicus",          "2025-08-06", 26, 32, 4, 2, 31),
    ("2025-Q3", "Republikon",        "2025-07-29", 25, 30, 5, 4, 27),
    ("2025-Q3", "Republikon",        "2025-07-08", 25, 31, 4, 5, 24),
]

# 2026-Q2 — DECIDED-voter base ("biztos szavazó pártválasztó"), from partpreferencia.hu
# homepage chart (Kommandant 2026-06-14). Fidesz/Tisza accurate; DK/MH small-party
# values are best-effort from the bars. THE clean, overlapping backtest target.
# (period, pollster, date_end, fidesz, tisza, dk, mihazank, bizonytalan=0)
_GROUND_TRUTH_2026Q2_DECIDED = [
    ("2026-Q2", "Závecz",            "2026-06-03", 20, 73, 1, 5, 0),
    ("2026-Q2", "IDEA",              "2026-05-11", 25, 68, 1, 4, 0),
    ("2026-Q2", "Publicus",          "2026-05-14", 20, 73, 1, 5, 0),
    ("2026-Q2", "21 Kutatóközpont",  "2026-05-13", 21, 71, 1, 6, 0),
    ("2026-Q2", "Republikon",        "2026-05-13", 26, 66, 2, 5, 0),
    ("2026-Q2", "Medián",            "2026-05-02", 23, 70, 1, 6, 0),
    ("2026-Q2", "Alapjogokért",      "2026-04-30", 45, 42, 4, 7, 0),
    ("2026-Q2", "Iránytű",           "2026-04-04", 40, 51, 2, 4, 0),
]

# 2026 OGY — ACTUAL election result (NVI, 100%, party-list). Gold ground truth.
# TIMELINE (important): this is the 12 Apr 2026 result (Tisza +14.6). The
# 2026-Q2 rows above are POST-election (May–Jun) and show Tisza +37 NOT because
# the polls erred but because Fidesz COLLAPSED after losing power — a later,
# different reality. Pre-election polls were relatively accurate. A panel
# grounded on CURRENT (June) news should track the post-collapse state
# (~2026-Q2), not this April result.
# (pollster, date_end, fidesz, tisza, dk, mihazank, bizonytalan=0 — list vote = cast votes)
_GROUND_TRUTH_2026_ELECTION = [
    ("NVI (tényleges)", "2026-04-12", 38.61, 53.18, 1.10, 5.63, 0),
]

_PARTY_KEYS = ("fidesz", "tisza", "dk", "mihazank", "bizonytalan")


def _get_db():
    from pyramid.memory_rag import _get_db as db
    return db()


def _ensure_table() -> None:
    conn = _get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS poll_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL DEFAULT 'ground_truth',
                period TEXT NOT NULL,
                pollster TEXT DEFAULT '',
                date_end TEXT DEFAULT '',
                base TEXT DEFAULT 'teljes_nepesseg',
                shares TEXT NOT NULL,
                source TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(kind, period, pollster, date_end, base)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_poll_period ON poll_results(period, kind)")
        conn.commit()
    finally:
        conn.close()


_ensure_table()


def insert_poll(kind: str, period: str, pollster: str, date_end: str,
                shares: dict, base: str = "teljes_nepesseg", source: str = "") -> int:
    """Idempotent insert (UNIQUE key). Returns 1 if new, else 0."""
    conn = _get_db()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO poll_results (kind, period, pollster, date_end, base, shares, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, period, pollster, date_end, base,
             json.dumps(shares, ensure_ascii=False), source,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


def seed_ground_truth() -> int:
    """Idempotently load the partpreferencia.hu 2025 ground truth. Returns # inserted."""
    n = 0
    for period, pollster, date_end, fi, ti, dk, mh, biz in _GROUND_TRUTH_2025:
        shares = {"fidesz": fi, "tisza": ti, "dk": dk, "mihazank": mh, "bizonytalan": biz}
        n += insert_poll("ground_truth", period, pollster, date_end, shares,
                         base="teljes_nepesseg", source="partpreferencia.hu")
    for period, pollster, date_end, fi, ti, dk, mh, biz in _GROUND_TRUTH_2026Q2_DECIDED:
        shares = {"fidesz": fi, "tisza": ti, "dk": dk, "mihazank": mh, "bizonytalan": biz}
        n += insert_poll("ground_truth", period, pollster, date_end, shares,
                         base="biztos_partvalaszto", source="partpreferencia.hu (homepage)")
    for pollster, date_end, fi, ti, dk, mh, biz in _GROUND_TRUTH_2026_ELECTION:
        shares = {"fidesz": fi, "tisza": ti, "dk": dk, "mihazank": mh, "bizonytalan": biz}
        n += insert_poll("ground_truth", "2026-OGY", pollster, date_end, shares,
                         base="valasztas_lista", source="NVI vtr.valasztas.hu (100%)")
    if n:
        logger.info("poll_results: seeded %d ground-truth rows", n)
    return n


def aggregate(period: str, base: str = "teljes_nepesseg", kind: str = "ground_truth") -> dict:
    """Mean party shares across pollsters for a period (+ n and per-party spread)."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT shares FROM poll_results WHERE period=? AND base=? AND kind=?",
            (period, base, kind),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return {"period": period, "n": 0, "mean": {}, "spread": {}}
    vals: dict = {k: [] for k in _PARTY_KEYS}
    for r in rows:
        s = json.loads(r["shares"])
        for k in _PARTY_KEYS:
            if k in s and s[k] is not None:
                vals[k].append(float(s[k]))
    mean = {k: round(sum(v) / len(v), 1) for k, v in vals.items() if v}
    spread = {k: round(max(v) - min(v), 1) for k, v in vals.items() if v}
    return {"period": period, "n": len(rows), "mean": mean, "spread": spread}


_DECIDED_KEYS = ("fidesz", "tisza", "dk", "mihazank")


def _to_decided(shares: dict) -> dict:
    """Renormalize shares over decided parties only (drop undecided)."""
    denom = sum(float(shares.get(k, 0) or 0) for k in _DECIDED_KEYS) or 1.0
    return {k: round(float(shares.get(k, 0) or 0) / denom * 100, 1) for k in _DECIDED_KEYS}


def calibrate(pred_shares: dict, gt_period: str = "2026-Q2",
              gt_base: str = "biztos_partvalaszto") -> dict:
    """Compare a panel prediction to decided-voter ground truth.

    Both sides are converted to the decided base (undecided dropped, renormalized)
    so a total-population panel is comparable to the published decided-voter result.
    Returns per-party delta (pred − ground truth) = the systematic bias to correct.
    """
    gt = aggregate(gt_period, base=gt_base)
    if not gt.get("mean"):
        return {"error": f"no ground truth for {gt_period}/{gt_base}"}
    pred_dec = _to_decided(pred_shares)
    gt_dec = _to_decided(gt["mean"])
    delta = {k: round(pred_dec[k] - gt_dec[k], 1) for k in _DECIDED_KEYS}
    return {
        "gt_period": gt_period, "gt_n": gt["n"],
        "pred_decided": pred_dec, "gt_decided": gt_dec, "delta": delta,
        "gap_pred": round(pred_dec["tisza"] - pred_dec["fidesz"], 1),
        "gap_gt": round(gt_dec["tisza"] - gt_dec["fidesz"], 1),
    }
