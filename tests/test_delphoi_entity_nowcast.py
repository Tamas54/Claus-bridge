"""test_delphoi_entity_nowcast.py — a KIRAKAT (N-ág) füstteszt fake-LLM-mel.

Egy entitás nowcast-életciklusa: regiszter-seed → korpusz → panel (injektált
chat_fn) → SSR (injektált embed_fn) → ledger-sor → delta a második futáson.
Plusz: display_label strukturális-tiltás (Akna 1), dry_run, üres-korpusz-védelem,
register_tools (FakeApp) + cron-recept-seed.
"""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from plugins import delphoi


@pytest.fixture
def delphoi_db(get_db):
    conn = get_db()
    delphoi.ensure_tables(conn)
    # press_snapshots — a conftest _DDL-ben nincs, a plugin-könyvtár hozza létre élesben
    conn.execute("""
        CREATE TABLE IF NOT EXISTS press_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_iso TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'hu',
            signal_type TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(date_iso, lang, signal_type))""")
    ts = datetime.now(timezone.utc).isoformat()
    brief = {"lead": "Feszült hét a nagypolitikában.",
             "topics": [{"title": "Uniós csúcs", "summary": "Vita a költségvetésről."}],
             "local_topics": [{"title": "Belpolitikai vita", "summary": "Éles szócsata a parlamentben."}]}
    trending = {"trending": [{"keyword": "uniós csúcs"}, {"keyword": "költségvetés"}]}
    for d in ("2026-07-07", "2026-07-08"):
        conn.execute("INSERT INTO press_snapshots (date_iso, lang, signal_type, content, created_at) VALUES (?,?,?,?,?)",
                     (d, "hu", "brief", json.dumps(brief, ensure_ascii=False), ts))
        conn.execute("INSERT INTO press_snapshots (date_iso, lang, signal_type, content, created_at) VALUES (?,?,?,?,?)",
                     (d, "hu", "trending", json.dumps(trending, ensure_ascii=False), ts))
    delphoi.seed_registry(conn)
    conn.commit()
    conn.close()
    return get_db


# ── fake-LLM + fake-embedding ───────────────────────────────────────────────
# A fake chat a 4-es (kissé pozitív) anchorral azonos mondatot adja vissza →
# az SSR-linear PMF-je one-hot a 4-esen → survey_score=4.0 → direction=+0.5.

def _fake_chat_positive(anchor_set):
    async def chat_fn(prompt):
        return anchor_set[3]  # "kicsit jobb lett róla a véleményem"
    return chat_fn


def _fake_embed(anchor_set):
    """Determinista embedding: az anchor-mondatok ortonormált bázis-vektorok;
    minden más szöveg a hozzá legjobban hasonlító anchor vektorát kapja."""
    async def embed_fn(texts):
        out = []
        for t in texts:
            vec = [0.0] * 5
            idx = anchor_set.index(t) if t in anchor_set else 3
            vec[idx] = 1.0
            out.append(vec)
        return out
    return embed_fn


def test_seed_registry_idempotent(delphoi_db):
    conn = delphoi_db()
    n1 = conn.execute("SELECT COUNT(*) FROM delphoi_entity_nowcast").fetchone()[0]
    delphoi.seed_registry(conn)
    n2 = conn.execute("SELECT COUNT(*) FROM delphoi_entity_nowcast").fetchone()[0]
    conn.close()
    assert n1 == n2 == len(delphoi._SEED_ENTITIES)


def test_display_label_structural_trap_rejected():
    with pytest.raises(ValueError):
        delphoi.validate_display_label("GDP-előrejelzés (HU)")
    with pytest.raises(ValueError):
        delphoi.validate_display_label("Choose-A-Forecast")
    # a seed-címkék mind átmennek (a seed_registry is futtatja)
    for _k, _c, _t, label, _e in delphoi._SEED_ENTITIES:
        delphoi.validate_display_label(label)


def test_nowcast_lifecycle_and_delta(delphoi_db):
    deps = {"get_db": delphoi_db}
    anchors = delphoi.REFERENCE_SETS_REGARD["hu"]
    rep = asyncio.run(delphoi.run_entity_nowcast(
        deps, entity_key="Q124488292", n=12,
        chat_fn=_fake_chat_positive(anchors), embed_fn=_fake_embed(anchors)))
    assert rep["ok"] and rep["ran"] == 1
    r = rep["results"][0]
    assert r["ok"] and r["n"] == 12
    assert r["direction"] == pytest.approx(0.5, abs=1e-6)   # anchor#4 → +0.5
    assert -1.0 <= r["direction"] <= 1.0
    assert r["direction_prev"] is None                       # első jel: nincs delta
    assert r["corpus_hash"] and r["content_hash"]

    # második futás: a delta az előző jelhez képest áll elő, a lánc nő és ép
    rep2 = asyncio.run(delphoi.run_entity_nowcast(
        deps, entity_key="Q124488292", n=12,
        chat_fn=_fake_chat_positive(anchors), embed_fn=_fake_embed(anchors)))
    r2 = rep2["results"][0]
    assert r2["direction_prev"] == pytest.approx(0.5, abs=1e-6)
    chain = delphoi.verify_ledger_chain(delphoi_db)
    assert chain["ok"] and chain["checked"] == 2

    # segment_json: bucket-bontás, minden bucket iránya a [-1,1] sávban
    conn = delphoi_db()
    seg = json.loads(conn.execute(
        "SELECT segment_json FROM delphoi_nowcast_ledger ORDER BY id DESC LIMIT 1").fetchone()[0])
    conn.close()
    assert seg and all(-1.0 <= v["direction"] <= 1.0 for v in seg.values())


def test_dry_run_writes_nothing(delphoi_db):
    deps = {"get_db": delphoi_db}
    anchors = delphoi.REFERENCE_SETS_REGARD["hu"]
    rep = asyncio.run(delphoi.run_entity_nowcast(
        deps, entity_key="Q387006", n=6, dry_run=True,
        chat_fn=_fake_chat_positive(anchors), embed_fn=_fake_embed(anchors)))
    assert rep["ok"] and rep["results"][0]["ok"]
    conn = delphoi_db()
    n = conn.execute("SELECT COUNT(*) FROM delphoi_nowcast_ledger").fetchone()[0]
    conn.close()
    assert n == 0


def test_empty_corpus_blocks_run(delphoi_db):
    """PL-korpusz nincs a teszt-DB-ben → a Tusk-nowcast korpusz nélkül NEM fut."""
    deps = {"get_db": delphoi_db}
    anchors = delphoi.REFERENCE_SETS_REGARD["pl"]
    rep = asyncio.run(delphoi.run_entity_nowcast(
        deps, entity_key="Q948", n=6,
        chat_fn=_fake_chat_positive(anchors), embed_fn=_fake_embed(anchors)))
    assert rep["results"][0]["ok"] is False
    assert "korpusz" in rep["results"][0]["error"]
    conn = delphoi_db()
    n = conn.execute("SELECT COUNT(*) FROM delphoi_nowcast_ledger").fetchone()[0]
    conn.close()
    assert n == 0


def test_register_tools_and_status(delphoi_db, fake_app):
    deps = {"get_db": delphoi_db}
    delphoi.register_tools(fake_app, deps)
    assert {"delphoi_entity_nowcast_run", "delphoi_nowcast_status",
            "delphoi_verify_ledger"} <= set(fake_app.tools)
    # cron-recept seedelve (idempotensen)
    conn = delphoi_db()
    row = conn.execute("SELECT cron_schedule, cron_enabled FROM pyramid_recipes "
                       "WHERE name='delphoi_nowcast_weekly'").fetchone()
    conn.close()
    assert row and row["cron_schedule"] == "30 7 * * 1" and row["cron_enabled"] == 1
    status = json.loads(asyncio.run(fake_app.tools["delphoi_nowcast_status"]()))
    assert status["chain_head"] == delphoi.GENESIS
    assert any(e["entity_key"] == "Q124488292" for e in status["registry"])
    verify = json.loads(asyncio.run(fake_app.tools["delphoi_verify_ledger"]()))
    assert verify["ok"] is True
