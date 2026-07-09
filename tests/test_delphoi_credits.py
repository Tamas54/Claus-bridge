"""test_delphoi_credits.py — ledger-atomikusság, signup-grant, topup, refund-reason (D7)."""
import pytest

from plugins import delphoi


@pytest.fixture
def fg_db(get_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_FG_CACHE", "0")
    conn = get_db()
    delphoi.ensure_fg_tables(conn)
    conn.close()
    return get_db


def test_signup_grant_idempotent(fg_db):
    delphoi.ensure_welcome(fg_db, "user:1")
    delphoi.ensure_welcome(fg_db, "user:1")
    c = delphoi.get_credits(fg_db, "user:1")
    assert c["balance"] == delphoi.WELCOME_CREDITS
    grants = [l for l in c["ledger"] if l["reason"] == "signup_grant"]
    assert len(grants) == 1


def test_charge_atomic_and_insufficient(fg_db):
    delphoi.ensure_welcome(fg_db, "user:2")
    bal0 = delphoi.get_credits(fg_db, "user:2")["balance"]
    assert delphoi.charge(fg_db, "user:2", "dlph-aaa", bal0) is True
    assert delphoi.get_credits(fg_db, "user:2")["balance"] == 0
    # nincs fedezet → False, SEMMI nem íródik
    assert delphoi.charge(fg_db, "user:2", "dlph-bbb", 1) is False
    c = delphoi.get_credits(fg_db, "user:2")
    assert c["balance"] == 0
    assert not any(l["reason"] == "job:dlph-bbb" for l in c["ledger"])


def test_refund_reason_format_and_idempotency(fg_db):
    delphoi.ensure_welcome(fg_db, "user:3")
    delphoi.charge(fg_db, "user:3", "dlph-ccc", 1)
    assert delphoi.refund(fg_db, "user:3", "dlph-ccc", 1) is True
    assert delphoi.refund(fg_db, "user:3", "dlph-ccc", 1) is False  # dupla-refund kizárva
    c = delphoi.get_credits(fg_db, "user:3")
    assert c["balance"] == delphoi.WELCOME_CREDITS
    refunds = [l for l in c["ledger"] if l["reason"] == "refund:dlph-ccc"]
    assert len(refunds) == 1 and refunds[0]["delta"] == 1 and refunds[0]["job_id"] == "dlph-ccc"


def test_topup_and_duplicate_note(fg_db):
    r1 = delphoi.add_credits(fg_db, "user:4", 10, "stripe:sess_123")
    assert r1["ok"] and r1["reason"] == "topup:stripe:sess_123"
    r2 = delphoi.add_credits(fg_db, "user:4", 10, "stripe:sess_123")  # webhook-replay
    assert r2["ok"] is False and r2["error"] == "duplicate_topup"
    assert delphoi.get_credits(fg_db, "user:4")["balance"] == 10


def test_job_cost_formula():
    assert delphoi.job_cost({"n_per_cell": 30}) == 1                    # gyors jelzés
    assert delphoi.job_cost({"n_per_cell": 200}) == 7                   # megbízható
    assert delphoi.job_cost({"n_per_cell": 30, "n_seeds": 3}) == 3      # robusztus
    assert delphoi.job_cost({"n_per_cell": 30}, ["A", "B"]) == 2        # A/B dupláz
    assert delphoi.job_cost({"n_per_cell": 10}) == 1                    # kemény alsó korlát: 30-ra emel
