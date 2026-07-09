"""test_delphoi_isolation.py — a SILÓ-VASSZABÁLY bizonyítéka (D1, KRITIKUS).

A privát user-input (delphoi_jobs) SOHA nem szivárog: (1) nem kerül a
nyilvános korpusz-táblákba; (2) MÁS user jobjának panel-promptjába sem jut be
(a grounding csak a press_snapshots-ból tölt); (3) az Echolot-oldali
search_news/Agora ELVI szinten is elérhetetlen — az a MÁSIK adatbázis
(echolot.db), ez a bridge.db privát silója.
"""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from plugins import delphoi

SECRET_A = "SZIGORUAN-PRIVAT-A-USER-INPUTJA-4242"


@pytest.fixture
def fg_db(get_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_FG_CACHE", "0")
    conn = get_db()
    delphoi.ensure_tables(conn)
    delphoi.ensure_fg_tables(conn)
    delphoi.seed_registry(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS press_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_iso TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'hu',
            signal_type TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(date_iso, lang, signal_type))""")
    conn.execute(
        "INSERT INTO press_snapshots (date_iso, lang, signal_type, content, created_at) VALUES (?,?,?,?,?)",
        ("2026-07-08", "hu", "brief",
         json.dumps({"lead": "Nyilvános hír.", "topics": [{"title": "Közélet", "summary": "Semmi extra."}]}),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return get_db


def test_private_input_not_in_public_tables(fg_db):
    delphoi.add_credits(fg_db, "user:i1", 5, "iso1")
    delphoi.create_job(fg_db, "user:i1", "product_desc", SECRET_A,
                       {"country": "HU", "n_per_cell": 30})
    conn = fg_db()
    # a privát silóban OTT VAN
    assert conn.execute("SELECT 1 FROM delphoi_jobs WHERE input_text=?",
                        (SECRET_A,)).fetchone()
    # a nyilvános/megosztott táblákban NINCS
    for table, col in (("press_snapshots", "content"),
                       ("pyramid_agent_rag", "content"),
                       ("poll_results", "shares"),
                       ("delphoi_nowcast_ledger", "segment_json")):
        try:
            hit = conn.execute(
                f"SELECT 1 FROM {table} WHERE {col} LIKE ?", (f"%{SECRET_A}%",)).fetchone()
        except Exception:  # noqa: BLE001 — nem létező tábla = nincs szivárgás
            hit = None
        assert hit is None, f"a privát input kiszivárgott: {table}.{col}"
    conn.close()


def test_private_input_never_grounds_another_users_job(fg_db):
    """A siló-vasszabály éles pontja: A-user inputja NEM kerülhet B-user
    jobjának panel-promptjába — a grounding csak a nyilvános korpuszból jön."""
    delphoi.add_credits(fg_db, "user:i2", 5, "iso2")
    delphoi.add_credits(fg_db, "user:i3", 5, "iso3")
    delphoi.create_job(fg_db, "user:i2", "product_desc", SECRET_A,
                       {"country": "HU", "n_per_cell": 30})
    created_b = delphoi.create_job(fg_db, "user:i3", "concept", "B-user ártatlan koncepciója",
                                   {"country": "HU", "n_per_cell": 30})
    seen_prompts = []
    anchors = delphoi.REFERENCE_SETS_APPEAL["hu"]

    async def spy_chat(prompt):
        seen_prompts.append(prompt)
        return anchors[2]

    async def embed_fn(texts):
        return [[1.0 if i == (anchors.index(t) if t in anchors else 2) else 0.0
                 for i in range(5)] for t in texts]

    rep = asyncio.run(delphoi.process_job({"get_db": fg_db}, created_b["job_id"],
                                          chat_fn=spy_chat, embed_fn=embed_fn))
    assert rep["ok"] and seen_prompts
    for p in seen_prompts:
        assert SECRET_A not in p, "A-user privát inputja B-user promptjában!"
        assert "B-user ártatlan koncepciója" in p, "a saját input viszont OTT VAN futásidőben"
        assert "Nyilvános hír" in p or "Közélet" in p, "a grounding a NYILVÁNOS korpuszból jön"


def test_nowcast_run_never_sees_private_silo(fg_db):
    """A KIRAKAT (nowcast) promptjaiba sem kerülhet privát job-input."""
    delphoi.add_credits(fg_db, "user:i4", 5, "iso4")
    delphoi.create_job(fg_db, "user:i4", "pitch", SECRET_A, {"country": "HU", "n_per_cell": 30})
    seen = []
    anchors = delphoi.REFERENCE_SETS_REGARD["hu"]

    async def spy_chat(prompt):
        seen.append(prompt)
        return anchors[2]

    async def embed_fn(texts):
        return [[1.0 if i == (anchors.index(t) if t in anchors else 2) else 0.0
                 for i in range(5)] for t in texts]

    asyncio.run(delphoi.run_entity_nowcast(
        {"get_db": fg_db}, entity_key="Q124488292", n=8,
        chat_fn=spy_chat, embed_fn=embed_fn))
    assert seen
    assert all(SECRET_A not in p for p in seen)
