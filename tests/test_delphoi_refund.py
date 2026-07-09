"""test_delphoi_refund.py — a REFUND-VASSZABÁLY (v2, D2): a fizetős termék becsülete.
(a) fan-out-halál → failed → EGY refund, egyenleg visszaáll; (b) dupla-refund
kizárva; (c) watchdog a ragadt running jobot failed+refundba billenti;
(d) részeredmény SOHA nem done."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from plugins import delphoi


@pytest.fixture
def fg_db(get_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_FG_CACHE", "0")
    conn = get_db()
    delphoi.ensure_fg_tables(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS press_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_iso TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'hu',
            signal_type TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(date_iso, lang, signal_type))""")
    conn.execute(
        "INSERT INTO press_snapshots (date_iso, lang, signal_type, content, created_at) VALUES (?,?,?,?,?)",
        ("2026-07-08", "hu", "brief", json.dumps({"lead": "Hír.", "topics": []}),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return get_db


def _dead_chat():
    async def chat_fn(prompt):
        raise RuntimeError("flash failed after retries (empty/transient)")
    return chat_fn


def _flaky_chat(fail_every: int):
    """Minden fail_every-edik hívás elhal — a küszöb-teszthez."""
    state = {"i": 0}
    anchors = delphoi.REFERENCE_SETS_APPEAL["hu"]
    async def chat_fn(prompt):
        state["i"] += 1
        if state["i"] % fail_every == 0:
            raise RuntimeError("transient")
        return anchors[3]
    return chat_fn


def _embed():
    anchors = delphoi.REFERENCE_SETS_APPEAL["hu"]
    async def embed_fn(texts):
        return [[1.0 if i == (anchors.index(t) if t in anchors else 2) else 0.0
                 for i in range(5)] for t in texts]
    return embed_fn


def _make_job(fg_db, user="user:r1", n=30):
    delphoi.add_credits(fg_db, user, 10, f"seed-{user}")
    created = delphoi.create_job(fg_db, user, "product_desc", "termék",
                                 {"country": "HU", "n_per_cell": n})
    assert created["ok"]
    return created


def test_total_death_failed_and_single_refund(fg_db):
    created = _make_job(fg_db)
    bal_after_charge = created["balance"]
    rep = asyncio.run(delphoi.process_job({"get_db": fg_db}, created["job_id"],
                                          chat_fn=_dead_chat(), embed_fn=_embed()))
    assert rep["ok"] is False and rep.get("refunded")
    st = delphoi.get_job(fg_db, created["job_id"], "user:r1")
    assert st["status"] == "failed" and st["refunded"] is True
    c = delphoi.get_credits(fg_db, "user:r1")
    assert c["balance"] == bal_after_charge + created["cost"], "az egyenleg visszaáll"
    refunds = [l for l in c["ledger"] if l["reason"] == f"refund:{created['job_id']}"]
    assert len(refunds) == 1, "PONTOSAN egy refund-sor"


def test_double_refund_excluded_even_on_repeat_fail(fg_db):
    created = _make_job(fg_db, "user:r2")
    delphoi._fail_job(fg_db, created["job_id"], "első halál")
    delphoi._fail_job(fg_db, created["job_id"], "második halál (retry)")
    delphoi.refund(fg_db, "user:r2", created["job_id"], created["cost"])  # direkt támadás
    c = delphoi.get_credits(fg_db, "user:r2")
    refunds = [l for l in c["ledger"] if l["reason"].startswith("refund:")]
    assert len(refunds) == 1


def test_watchdog_kills_stuck_running(fg_db):
    created = _make_job(fg_db, "user:r3")
    stale = (datetime.now(timezone.utc)
             - timedelta(minutes=delphoi.WATCHDOG_MINUTES + 10)).isoformat()
    conn = fg_db()
    conn.execute("UPDATE delphoi_jobs SET status='running', started_at=? WHERE id=?",
                 (stale, created["job_id"]))
    conn.commit(); conn.close()
    n = delphoi.watchdog_sweep(fg_db)
    assert n == 1
    st = delphoi.get_job(fg_db, created["job_id"], "user:r3")
    assert st["status"] == "failed"
    c = delphoi.get_credits(fg_db, "user:r3")
    assert any(l["reason"] == f"refund:{created['job_id']}" for l in c["ledger"])
    # friss running jobot NEM bánt
    created2 = _make_job(fg_db, "user:r3b")
    conn = fg_db()
    conn.execute("UPDATE delphoi_jobs SET status='running', started_at=? WHERE id=?",
                 (datetime.now(timezone.utc).isoformat(), created2["job_id"]))
    conn.commit(); conn.close()
    assert delphoi.watchdog_sweep(fg_db) == 0


def test_partial_panel_never_done(fg_db):
    """~33% halálozás (minden 3. hívás) < 90% kitöltés → failed + refund,
    és a részeredmény SOHA nem kerül ki done-ként."""
    created = _make_job(fg_db, "user:r4")
    rep = asyncio.run(delphoi.process_job({"get_db": fg_db}, created["job_id"],
                                          chat_fn=_flaky_chat(3), embed_fn=_embed()))
    assert rep["ok"] is False and rep["error"] == "incomplete_panel"
    st = delphoi.get_job(fg_db, created["job_id"], "user:r4")
    assert st["status"] == "failed" and "result" not in st
    assert any(l["reason"] == f"refund:{created['job_id']}"
               for l in delphoi.get_credits(fg_db, "user:r4")["ledger"])


def test_small_dropout_still_done(fg_db):
    """~3% halálozás (minden 30.) ≥ 90% kitöltés → done, a kitöltés riportolva."""
    created = _make_job(fg_db, "user:r5")
    rep = asyncio.run(delphoi.process_job({"get_db": fg_db}, created["job_id"],
                                          chat_fn=_flaky_chat(30), embed_fn=_embed()))
    assert rep["ok"]
    st = delphoi.get_job(fg_db, created["job_id"], "user:r5")
    assert st["status"] == "done"
    assert st["result"]["panel"]["n_completed"] < st["result"]["panel"]["n_requested"]
    # done jobra nincs refund
    assert not any(l["reason"].startswith("refund:")
                   for l in delphoi.get_credits(fg_db, "user:r5")["ledger"])
