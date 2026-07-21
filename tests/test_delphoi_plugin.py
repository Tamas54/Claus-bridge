"""test_delphoi_plugin.py — a fókuszcsoport-életciklus fake-LLM-mel (D7).
queued→running→done, kredit-levonás, elégtelen-kredit ág, register_tools."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from plugins import delphoi

SECRET = "XYZZY-PRIVAT-TERMEKLEIRAS-72"


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
        ("2026-07-08", "hu", "brief",
         json.dumps({"lead": "Nyugodt hírnap.", "topics": [{"title": "Időjárás", "summary": "Kánikula."}]}),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return get_db


def _appeal_chat(level: int):
    anchors = delphoi.REFERENCE_SETS_APPEAL["hu"]
    async def chat_fn(prompt):
        return anchors[level - 1]
    return chat_fn


def _appeal_embed():
    anchors = delphoi.REFERENCE_SETS_APPEAL["hu"]
    async def embed_fn(texts):
        out = []
        for t in texts:
            vec = [0.0] * 5
            vec[anchors.index(t) if t in anchors else 2] = 1.0
            out.append(vec)
        return out
    return embed_fn


def test_full_lifecycle_product_desc(fg_db):
    deps = {"get_db": fg_db}
    delphoi.add_credits(fg_db, "user:10", 10, "teszt-feltoltes")
    created = delphoi.create_job(fg_db, "user:10", "product_desc", SECRET,
                                 {"country": "HU", "n_per_cell": 30})
    assert created["ok"] and created["cost"] == 1
    job_id = created["job_id"]
    assert job_id.startswith("dlph-")
    # queued állapot
    st = delphoi.get_job(fg_db, job_id, "user:10")
    assert st["status"] == "queued"
    # feldolgozás fake-LLM-mel (mindenki a 4-es anchort mondja)
    rep = asyncio.run(delphoi.process_job(deps, job_id,
                                          chat_fn=_appeal_chat(4), embed_fn=_appeal_embed()))
    assert rep["ok"]
    st = delphoi.get_job(fg_db, job_id, "user:10")
    assert st["status"] == "done"
    res = st["result"]
    assert res["overall_score"] == pytest.approx(4.0, abs=1e-6)
    assert res["baseline"]["value"] == 3.0
    assert res["panel"]["n_completed"] == 30
    assert "disclaimer" in res
    # nyers persona-mondat NEM megy ki az eredményben
    assert SECRET not in json.dumps(res, ensure_ascii=False)
    # kredit levonva
    assert delphoi.get_credits(fg_db, "user:10")["balance"] == 10 + delphoi.WELCOME_CREDITS - 1


def test_insufficient_credits_no_job(fg_db):
    # a welcome után mindent elköltünk, majd drága jobot kérünk
    delphoi.ensure_welcome(fg_db, "user:11")
    delphoi.charge(fg_db, "user:11", "dlph-elokoltseg", delphoi.WELCOME_CREDITS)
    created = delphoi.create_job(fg_db, "user:11", "product_desc", "valami",
                                 {"country": "HU", "n_per_cell": 200})
    assert created["ok"] is False and created["error"] == "insufficient_credits"
    conn = fg_db()
    n = conn.execute("SELECT COUNT(*) FROM delphoi_jobs WHERE user_id='user:11'").fetchone()[0]
    conn.close()
    assert n == 0, "elégtelen kreditnél job-sor sem születhet"


def test_ownership_guard(fg_db):
    delphoi.add_credits(fg_db, "user:12", 5, "t2")
    created = delphoi.create_job(fg_db, "user:12", "concept", "koncepció",
                                 {"country": "HU", "n_per_cell": 30})
    assert delphoi.get_job(fg_db, created["job_id"], "user:MASIK")["ok"] is False


def test_invalid_inputs(fg_db):
    delphoi.add_credits(fg_db, "user:13", 5, "t3")
    assert delphoi.create_job(fg_db, "user:13", "hackKind", "x", {})["ok"] is False
    assert delphoi.create_job(fg_db, "user:13", "product_desc", "", {})["ok"] is False
    assert delphoi.create_job(fg_db, "user:13", "ab_test", "", {"country": "HU"},
                              ["csak egy"])["ok"] is False
    # G3: DE már validált FG-ország — a kapu ismeretlen országon él tovább
    assert "nem validált ország" in delphoi.create_job(
        fg_db, "user:13", "product_desc", "x", {"country": "RO"})["error"]


def test_register_tools_full_set(fg_db, fake_app):
    delphoi.register_tools(fake_app, {"get_db": fg_db})
    assert {"delphoi_entity_nowcast_run", "delphoi_nowcast_status", "delphoi_verify_ledger",
            "delphoi_run_focus_group", "delphoi_get_credits", "delphoi_job_status"} <= set(fake_app.tools)
    conn = fg_db()
    recipes = {r["name"] for r in conn.execute(
        "SELECT name FROM pyramid_recipes WHERE name LIKE 'delphoi_%'").fetchall()}
    conn.close()
    assert recipes == {"delphoi_nowcast_weekly", "delphoi_watchdog"}
    credits = json.loads(asyncio.run(fake_app.tools["delphoi_get_credits"]("user:20")))
    assert credits["balance"] == delphoi.WELCOME_CREDITS
