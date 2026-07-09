"""test_delphoi_ledger_immutable.py — az IDŐBÉLYEG-INVARIÁNS bizonyítéka (v2, N1.5).

Ha ez a teszt nem zöld, a track record nem bizonyíték — ezért BLOKKOLÓ:
  (a) a ledgeren az UPDATE/DELETE a DB-TRIGGER miatt ABORT-tal hasal el;
  (b) a predicted_at szerver-idő, nem paraméterezhető vissza;
  (c) a verify_ledger_chain() ép láncon zöld, turkált MÁSOLATON piros;
  (d) a corpus_hash determinista (ID-sorrend-független).
"""
import asyncio  # noqa: F401 — a többi delphoi-teszt párja, itt csak szinkron út
import inspect
import sqlite3
from datetime import datetime, timezone

import pytest

from plugins import delphoi


@pytest.fixture
def delphoi_db(get_db):
    conn = get_db()
    delphoi.ensure_tables(conn)
    conn.close()
    return get_db


def _row(get_db, key="Q-TESZT", direction=0.25):
    return delphoi.append_ledger_row(
        get_db, key, "HU", "2026-W29", direction,
        delphoi.compute_corpus_hash([1, 2, 3], "2026-07-01", "2026-07-07", "HU"),
        "test-model|non-think")


# ── (a) APPEND-ONLY: a trigger él, nem kódfegyelem ──────────────────────────

def test_update_aborts(delphoi_db):
    _row(delphoi_db)
    conn = delphoi_db()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE delphoi_nowcast_ledger SET direction=0.99")
    conn.close()


def test_delete_aborts(delphoi_db):
    _row(delphoi_db)
    conn = delphoi_db()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM delphoi_nowcast_ledger")
    conn.close()


# ── (b) predicted_at = szerver-idő, nem paraméter ───────────────────────────

def test_predicted_at_is_server_time_and_not_a_parameter(delphoi_db):
    sig = inspect.signature(delphoi.append_ledger_row)
    assert "predicted_at" not in sig.parameters, \
        "a predicted_at nem lehet hívói paraméter (visszadátumozás-tilalom)"
    before = datetime.now(timezone.utc)
    row = _row(delphoi_db)
    after = datetime.now(timezone.utc)
    ts = datetime.fromisoformat(row["predicted_at"])
    assert before <= ts <= after


# ── (c) hash-lánc: ép láncon zöld, turkált másolaton piros ──────────────────

def test_chain_verifies_green_on_intact_ledger(delphoi_db):
    _row(delphoi_db, "Q-A", 0.1)
    _row(delphoi_db, "Q-B", -0.2)
    _row(delphoi_db, "Q-A", 0.3)
    rep = delphoi.verify_ledger_chain(delphoi_db)
    assert rep["ok"] is True and rep["checked"] == 3
    # a lánc tényleg fűzött: a 2. sor prev_hash-e az 1. sor content_hash-e
    conn = delphoi_db()
    rows = conn.execute(
        "SELECT prev_hash, content_hash FROM delphoi_nowcast_ledger ORDER BY id").fetchall()
    conn.close()
    assert rows[0]["prev_hash"] == delphoi.GENESIS
    assert rows[1]["prev_hash"] == rows[0]["content_hash"]
    assert rows[2]["prev_hash"] == rows[1]["content_hash"]


def _copy_without_triggers(src_get_db, tmp_path):
    """Támadó-szimuláció: a ledger másolata trigger NÉLKÜL (ott lehet turkálni)."""
    dst = str(tmp_path / "tampered.db")
    dst_conn = sqlite3.connect(dst)
    dst_conn.execute("""
        CREATE TABLE delphoi_nowcast_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, entity_key TEXT NOT NULL,
            country TEXT NOT NULL, predicted_at TEXT NOT NULL, target_window TEXT NOT NULL,
            direction REAL NOT NULL, direction_prev REAL, corpus_hash TEXT NOT NULL,
            model_id TEXT NOT NULL, segment_json TEXT,
            prev_hash TEXT NOT NULL, content_hash TEXT NOT NULL)""")
    src = src_get_db()
    for r in src.execute("SELECT * FROM delphoi_nowcast_ledger ORDER BY id"):
        dst_conn.execute(
            "INSERT INTO delphoi_nowcast_ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", tuple(r))
    src.close()
    dst_conn.commit()

    def _get():
        c = sqlite3.connect(dst)
        c.row_factory = sqlite3.Row
        return c
    return _get


def test_chain_detects_naive_tampering(delphoi_db, tmp_path):
    """Sor-tartalom átírva, hash-ek érintetlenül → content_hash mismatch."""
    _row(delphoi_db, "Q-A", 0.1)
    _row(delphoi_db, "Q-A", 0.2)
    tampered = _copy_without_triggers(delphoi_db, tmp_path)
    conn = tampered()
    conn.execute("UPDATE delphoi_nowcast_ledger SET direction=0.9 WHERE id=1")
    conn.commit(); conn.close()
    rep = delphoi.verify_ledger_chain(tampered)
    assert rep["ok"] is False and rep["first_bad_id"] == 1


def test_chain_detects_consistent_rewrite(delphoi_db, tmp_path):
    """Az ERŐS támadás: a sor tartalmát ÉS a saját content_hash-ét is konzisztensen
    átírják. prev_hash-lánc nélkül ez láthatatlan lenne — a láncnak detektálnia kell,
    mert a KÖVETKEZŐ sor prev_hash-e már a régi hash-re mutat."""
    _row(delphoi_db, "Q-A", 0.1)
    _row(delphoi_db, "Q-A", 0.2)
    _row(delphoi_db, "Q-A", 0.3)
    tampered = _copy_without_triggers(delphoi_db, tmp_path)
    conn = tampered()
    r = conn.execute("SELECT * FROM delphoi_nowcast_ledger WHERE id=2").fetchone()
    new_hash = delphoi.compute_content_hash(
        r["entity_key"], r["predicted_at"], r["target_window"],
        0.85, r["corpus_hash"], r["prev_hash"])
    conn.execute("UPDATE delphoi_nowcast_ledger SET direction=0.85, content_hash=? WHERE id=2",
                 (new_hash,))
    conn.commit(); conn.close()
    rep = delphoi.verify_ledger_chain(tampered)
    assert rep["ok"] is False
    assert rep["first_bad_id"] == 3, "a 3. sor prev_hash-ének kell buknia (lánc-szakadás)"


# ── (d) corpus_hash determinizmus ───────────────────────────────────────────

def test_corpus_hash_deterministic_and_order_independent():
    h1 = delphoi.compute_corpus_hash([3, 1, 2], "2026-07-01", "2026-07-07", "HU")
    h2 = delphoi.compute_corpus_hash([2, 3, 1], "2026-07-01", "2026-07-07", "HU")
    h3 = delphoi.compute_corpus_hash(["1", "2", "3"], "2026-07-01", "2026-07-07", "HU")
    assert h1 == h2 == h3, "az ID-k rendezve, típus-agnosztikusan hash-elődnek"
    assert h1 != delphoi.compute_corpus_hash([1, 2, 3], "2026-07-01", "2026-07-07", "PL")
    assert h1 != delphoi.compute_corpus_hash([1, 2, 3], "2026-07-02", "2026-07-07", "HU")


def test_content_hash_includes_prev_hash():
    a = delphoi.compute_content_hash("Q1", "t", "w", 0.5, "c", "PREV_A")
    b = delphoi.compute_content_hash("Q1", "t", "w", 0.5, "c", "PREV_B")
    assert a != b, "a prev_hash a content_hash része — enélkül nincs lánc"
