"""test_migrate_pythia_p1.py — a P1 ledger-migráció (ALTER-only, append-only-barát).

Pre-P1 sémán (ledger model_id nélkül, jobs az új oszlopok nélkül, ÉLES
append-only triggerekkel): dry-run nem ír; apply backupol + oszlopokat ad
hozzá + verify_ledger_chain zöld; régi sorok model_id=NULL-lal érintetlenek;
második apply no-op (idempotens).
"""
import os
import sqlite3

import pytest

import migrate_pythia_p1 as mig
from plugins.delphoi import GENESIS, compute_content_hash, verify_ledger_chain

_PRE_P1_SQL = """
CREATE TABLE delphoi_nowcast_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_key TEXT NOT NULL, country TEXT NOT NULL,
    predicted_at TEXT NOT NULL, target_window TEXT NOT NULL,
    direction REAL NOT NULL, direction_prev REAL,
    corpus_hash TEXT NOT NULL, segment_json TEXT,
    prev_hash TEXT NOT NULL, content_hash TEXT NOT NULL);
CREATE TRIGGER delphoi_ledger_no_update BEFORE UPDATE ON delphoi_nowcast_ledger
BEGIN SELECT RAISE(ABORT, 'delphoi_nowcast_ledger is append-only'); END;
CREATE TRIGGER delphoi_ledger_no_delete BEFORE DELETE ON delphoi_nowcast_ledger
BEGIN SELECT RAISE(ABORT, 'delphoi_nowcast_ledger is append-only'); END;
CREATE TABLE delphoi_jobs (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
    input_kind TEXT NOT NULL, input_text TEXT NOT NULL, input_variants TEXT,
    vision_ref TEXT, panel_spec TEXT NOT NULL, credits_cost INTEGER NOT NULL,
    result_json TEXT, error TEXT, created_at TEXT NOT NULL,
    started_at TEXT, completed_at TEXT, deleted_at TEXT);
CREATE TABLE delphoi_credits (
    user_id TEXT PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL);
"""


@pytest.fixture
def pre_p1_db(tmp_path):
    p = str(tmp_path / "pre_p1.db")
    conn = sqlite3.connect(p)
    conn.executescript(_PRE_P1_SQL)
    prev = GENESIS
    for i, d in enumerate((-0.29, 0.11, -0.21)):
        ts = f"2026-07-{13 + i}T07:30:00+00:00"
        ch = compute_content_hash("Q1", ts, "2026-W29", d, f"ch{i}", prev)
        conn.execute(
            "INSERT INTO delphoi_nowcast_ledger (entity_key, country, predicted_at, "
            "target_window, direction, corpus_hash, prev_hash, content_hash) "
            "VALUES ('Q1','HU',?,?,?,?,?,?)", (ts, "2026-W29", d, f"ch{i}", prev, ch))
        prev = ch
    # user-hivatkozások az A2-backfillhez: az Echolot-proxy 'user:<id>' formát
    # küld; a 'user:2' MINDKÉT forrás-táblában szerepel (union-dedup teszt).
    for jid, uid in (("dlph-old", "user:1"), ("dlph-old2", "user:2")):
        conn.execute("INSERT INTO delphoi_jobs (id, user_id, input_kind, input_text, "
                     "panel_spec, credits_cost, created_at) "
                     "VALUES (?, ?, 'pitch', 'x', '{}', 1, '2026-07-10T00:00:00+00:00')",
                     (jid, uid))
    for uid in ("user:2", "user:3"):
        conn.execute("INSERT INTO delphoi_credits (user_id, balance, updated_at) "
                     "VALUES (?, 2, '2026-07-10T00:00:00+00:00')", (uid,))
    conn.commit()
    conn.close()
    return p


def _cols(db, table):
    conn = sqlite3.connect(db)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def test_dry_run_default_writes_nothing(pre_p1_db):
    rc = mig.main(["--db", pre_p1_db])
    assert rc == 0
    assert "model_id" not in _cols(pre_p1_db, "delphoi_nowcast_ledger")
    assert not [f for f in os.listdir(os.path.dirname(pre_p1_db)) if ".bak" in f]
    # A2: dry-runban a users-tábla sem jön létre
    conn = sqlite3.connect(pre_p1_db)
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='delphoi_users'").fetchone() is None
    conn.close()


def test_apply_migrates_backups_and_verifies(pre_p1_db):
    rc = mig.main(["--db", pre_p1_db, "--apply"])
    assert rc == 0
    # oszlopok megvannak
    assert "model_id" in _cols(pre_p1_db, "delphoi_nowcast_ledger")
    for c in ("model_id", "panel_version", "scope_verdict", "coverage_score"):
        assert c in _cols(pre_p1_db, "delphoi_jobs")
    # backup-fájl készült
    baks = [f for f in os.listdir(os.path.dirname(pre_p1_db)) if ".pre_pythia_p1." in f]
    assert len(baks) == 1
    # régi sorok érintetlenek: model_id=NULL (= Flash-korszak), lánc zöld
    conn = sqlite3.connect(pre_p1_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT model_id, direction FROM delphoi_nowcast_ledger ORDER BY id").fetchall()
    conn.close()
    assert [r["model_id"] for r in rows] == [None, None, None]
    assert [r["direction"] for r in rows] == [-0.29, 0.11, -0.21]

    def get_db():
        c = sqlite3.connect(pre_p1_db)
        c.row_factory = sqlite3.Row
        return c
    rep = verify_ledger_chain(get_db)
    assert rep["ok"] is True
    assert rep["checked"] == 3


def test_apply_is_idempotent(pre_p1_db):
    assert mig.main(["--db", pre_p1_db, "--apply"]) == 0
    assert mig.main(["--db", pre_p1_db, "--apply"]) == 0
    # nem duplikálódik oszlop
    assert _cols(pre_p1_db, "delphoi_jobs").count("model_id") == 1
    # A2: a user-backfill sem duplikálódik
    conn = sqlite3.connect(pre_p1_db)
    assert conn.execute("SELECT COUNT(*) FROM delphoi_users").fetchone()[0] == 3
    conn.close()


def test_a2_users_backfill(pre_p1_db):
    """delphoi_users: séma + origin='echolot' backfill a credits∪jobs unióból,
    dedupolva ('user:2' két táblában is szerepel → egy sor)."""
    assert mig.main(["--db", pre_p1_db, "--apply"]) == 0
    conn = sqlite3.connect(pre_p1_db)
    conn.row_factory = sqlite3.Row
    cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(delphoi_users)")}
    assert set(cols) == {"user_id", "origin", "external_id", "email", "created_at"}
    assert cols["user_id"] == "INTEGER"
    rows = conn.execute(
        "SELECT origin, external_id, email FROM delphoi_users ORDER BY external_id").fetchall()
    assert [(r["origin"], r["external_id"]) for r in rows] == [
        ("echolot", "user:1"), ("echolot", "user:2"), ("echolot", "user:3")]
    assert all(r["email"] is None for r in rows)   # backfill nem talál ki emailt
    # user_id INTEGER PK autó-kiosztás
    ids = [r[0] for r in conn.execute("SELECT user_id FROM delphoi_users ORDER BY user_id")]
    assert ids == [1, 2, 3]
    # origin-CHECK: csak 'echolot'|'saas'
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO delphoi_users (origin, external_id) VALUES ('rossz', 'x')")
    # UNIQUE(origin, external_id): dupla backfill DB-szinten is kizárt
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO delphoi_users (origin, external_id) VALUES ('echolot', 'user:1')")
    # 'saas' eredetű sor mehet (a SaaS-regisztráció útja)
    conn.execute("INSERT INTO delphoi_users (origin, external_id, email) "
                 "VALUES ('saas', 'acct_123', 'x@y.io')")
    conn.close()


def test_a2_new_user_ref_backfilled_on_rerun(pre_p1_db):
    """Új Echolot-user a két apply KÖZÖTT → a második futás felveszi."""
    assert mig.main(["--db", pre_p1_db, "--apply"]) == 0
    conn = sqlite3.connect(pre_p1_db)
    conn.execute("INSERT INTO delphoi_credits (user_id, balance, updated_at) "
                 "VALUES ('user:4', 2, '2026-07-20T00:00:00+00:00')")
    conn.commit()
    conn.close()
    assert mig.main(["--db", pre_p1_db, "--apply"]) == 0
    conn = sqlite3.connect(pre_p1_db)
    got = {r[0] for r in conn.execute("SELECT external_id FROM delphoi_users WHERE origin='echolot'")}
    conn.close()
    assert got == {"user:1", "user:2", "user:3", "user:4"}


def test_missing_db_is_loud():
    assert mig.main(["--db", "/nonexistent/nope.db"]) == 1


def test_triggers_still_enforce_append_only_after_migration(pre_p1_db):
    mig.main(["--db", pre_p1_db, "--apply"])
    conn = sqlite3.connect(pre_p1_db)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE delphoi_nowcast_ledger SET direction=0 WHERE id=1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM delphoi_nowcast_ledger WHERE id=1")
    conn.close()
