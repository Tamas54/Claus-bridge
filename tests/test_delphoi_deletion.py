"""test_delphoi_deletion.py — a GDPR-út (v2, D4): (a) csak tulajdonosnak;
(b) nyers input + nyers reakciók FIZIKAILAG felülírva; (c) aggregátum és
kredit-ledger érintetlen; (d) a nowcast-ledgert a törlés nem éri el."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from plugins import delphoi

SECRET = "TITKOS-PITCH-SZOVEG-99"


@pytest.fixture
def fg_db(get_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_FG_CACHE", "0")
    conn = get_db()
    delphoi.ensure_tables(conn)
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


def _done_job(fg_db, user="user:d1"):
    anchors = delphoi.REFERENCE_SETS_APPEAL["hu"]
    async def chat_fn(prompt):
        return anchors[3]
    async def embed_fn(texts):
        return [[1.0 if i == (anchors.index(t) if t in anchors else 2) else 0.0
                 for i in range(5)] for t in texts]
    delphoi.add_credits(fg_db, user, 5, f"seed-{user}")
    created = delphoi.create_job(fg_db, user, "product_desc", SECRET,
                                 {"country": "HU", "n_per_cell": 30})
    asyncio.run(delphoi.process_job({"get_db": fg_db}, created["job_id"],
                                    chat_fn=chat_fn, embed_fn=embed_fn))
    return created


def test_only_owner_can_delete(fg_db):
    created = _done_job(fg_db)
    assert delphoi.delete_job(fg_db, created["job_id"], "user:BETOLAKODO")["ok"] is False
    conn = fg_db()
    row = conn.execute("SELECT input_text FROM delphoi_jobs WHERE id=?",
                       (created["job_id"],)).fetchone()
    conn.close()
    assert row["input_text"] == SECRET, "idegen törlés-kísérlet nem nyúlhat az adathoz"


def test_physical_overwrite_and_aggregate_survives(fg_db):
    created = _done_job(fg_db, "user:d2")
    job_id = created["job_id"]
    res = delphoi.delete_job(fg_db, job_id, "user:d2")
    assert res["ok"] and res["deleted_at"]
    conn = fg_db()
    job = conn.execute("SELECT * FROM delphoi_jobs WHERE id=?", (job_id,)).fetchone()
    raws = [r["raw_reaction"] for r in conn.execute(
        "SELECT raw_reaction FROM delphoi_panel_responses WHERE job_id=?", (job_id,)).fetchall()]
    conn.close()
    # (b) fizikai felülírás
    assert job["input_text"] == "[deleted]" and job["deleted_at"]
    assert raws and all(r == "[deleted]" for r in raws)
    # (c) aggregátum marad (a szolgáltatás teljesült), kredit-ledger érintetlen
    assert job["result_json"] and SECRET not in job["result_json"]
    ledger = delphoi.get_credits(fg_db, "user:d2")["ledger"]
    assert any(l["reason"] == f"job:{job_id}" for l in ledger), "a levonás-sor marad"
    assert not any(l["reason"] == f"refund:{job_id}" for l in ledger), "törlés ≠ refund"
    # idempotens újra-törlés
    assert delphoi.delete_job(fg_db, job_id, "user:d2").get("already_deleted")


def test_nowcast_ledger_untouched_by_deletion(fg_db):
    # publikus predikció-sor a láncban
    delphoi.append_ledger_row(fg_db, "Q-TESZT", "HU", "2026-W29", 0.2,
                              delphoi.compute_corpus_hash([1], "a", "b", "HU"), "m")
    created = _done_job(fg_db, "user:d3")
    delphoi.delete_job(fg_db, created["job_id"], "user:d3")
    chain = delphoi.verify_ledger_chain(fg_db)
    assert chain["ok"] and chain["checked"] == 1, "a nowcast-ledger nem user-adat, a törlés nem érinti"
