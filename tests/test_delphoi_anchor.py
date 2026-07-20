"""test_delphoi_anchor.py — P5/3: az anchor-csatorna MINDKÉT módja kész.

DELPHOI_ANCHOR_CHANNEL=off|git|agora — default OFF (flip Kommandant-szóra).
  - git:   a lánc-fej a repo ledger_anchors.txt-jébe fűzve (append, időbélyeggel);
  - agora: heti lánc-pecsét Agora-poszt a nowcaster-agenttől — loop nélkül
    szinkron ('posted'), futó loopban háttér-task ('scheduled'); operátor-kulcs
    nélkül hangos reason. A poszt-törzs ≥200 karakter (Echolot publish-minimum),
    hordozza a lánc-fejet és a verify-utat.
"""
import asyncio

import pytest

from plugins import delphoi

HY3 = "tencent/Hy3|non-think|temp=0.8|ssr=linear|emb=e"


@pytest.fixture
def chain_db(get_db):
    conn = get_db()
    delphoi.ensure_tables(conn)
    delphoi.seed_registry(conn)
    conn.close()
    delphoi.append_ledger_row(get_db, "Q124488292", "HU", "2026-W31", -0.2, "ch", HY3)
    delphoi.append_ledger_row(get_db, "Q387006", "HU", "2026-W31", 0.1, "ch", HY3)
    return get_db


def test_default_off(chain_db, monkeypatch):
    monkeypatch.delenv("DELPHOI_ANCHOR_CHANNEL", raising=False)
    rep = delphoi.anchor_hash(chain_db)
    assert rep == {"channel": "off", "anchored": False}


def test_unknown_channel_loud(chain_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_ANCHOR_CHANNEL", "postagalamb")
    rep = delphoi.anchor_hash(chain_db)
    assert rep["anchored"] is False
    assert "ismeretlen" in rep["reason"]


def test_empty_chain_not_anchored(get_db, monkeypatch):
    conn = get_db()
    delphoi.ensure_tables(conn)
    conn.close()
    monkeypatch.setenv("DELPHOI_ANCHOR_CHANNEL", "git")
    rep = delphoi.anchor_hash(get_db)
    assert rep["anchored"] is False
    assert "üres" in rep["reason"]


def test_git_mode_appends_head(chain_db, tmp_path, monkeypatch):
    monkeypatch.setenv("DELPHOI_ANCHOR_CHANNEL", "git")
    rep = delphoi.anchor_hash(chain_db, repo_root=str(tmp_path))
    assert rep["anchored"] is True
    head = delphoi.verify_ledger_chain(chain_db)["head"]
    assert rep["head"] == head
    content = (tmp_path / "ledger_anchors.txt").read_text(encoding="utf-8")
    assert head in content
    # append-only: második pecsét ÚJ sort fűz, a régit nem bántja
    delphoi.anchor_hash(chain_db, repo_root=str(tmp_path))
    lines = (tmp_path / "ledger_anchors.txt").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(head in ln for ln in lines)


def test_agora_mode_without_key_loud_reason(chain_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_ANCHOR_CHANNEL", "agora")
    monkeypatch.delenv("DELPHOI_ANCHOR_AGORA_KEY", raising=False)
    rep = delphoi.anchor_hash(chain_db)
    assert rep["anchored"] is False
    assert "DELPHOI_ANCHOR_AGORA_KEY" in rep["reason"]


def test_agora_mode_posts_chain_stamp(chain_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_ANCHOR_CHANNEL", "agora")
    posted = {}

    async def fake_publish(title, body):
        posted["title"], posted["body"] = title, body
        return {"ok": True, "post_id": "pub-42"}

    rep = delphoi.anchor_hash(chain_db, publish_fn=fake_publish)
    head = delphoi.verify_ledger_chain(chain_db)["head"]
    assert rep["anchored"] is True
    assert rep["mode"] == "posted"
    assert rep["post_id"] == "pub-42"
    assert rep["head"] == head
    assert head in posted["body"]                    # a pecsét hordozza a fejet
    assert "/api/delphoi/verify" in posted["body"]   # független ellenőrzés útja
    assert len(posted["body"]) >= 200                # Echolot publish-minimum
    assert "nem közvélemény-kutatás" in posted["body"]  # terminológia-vasszabály


def test_agora_mode_publish_failure_is_loud(chain_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_ANCHOR_CHANNEL", "agora")

    async def failing_publish(title, body):
        return {"ok": False, "error": "operator_key_invalid"}

    rep = delphoi.anchor_hash(chain_db, publish_fn=failing_publish)
    assert rep["anchored"] is False
    assert "operator_key_invalid" in rep["reason"]


def test_agora_mode_schedules_in_running_loop(chain_db, monkeypatch):
    """Cron-kontextus: futó event-loopban a poszt háttér-task (nem blokkol),
    a riport 'scheduled' — a nowcast-futást a hálózati út nem tartja fel."""
    monkeypatch.setenv("DELPHOI_ANCHOR_CHANNEL", "agora")
    calls = []

    async def fake_publish(title, body):
        calls.append(title)
        return {"ok": True, "post_id": "pub-99"}

    async def _run():
        rep = delphoi.anchor_hash(chain_db, publish_fn=fake_publish)
        await asyncio.sleep(0)   # hagyjuk lefutni a háttér-taskot
        await asyncio.sleep(0)
        return rep

    rep = asyncio.run(_run())
    assert rep["anchored"] is True
    assert rep["mode"] == "scheduled"
    assert calls, "a háttér-task nem futott le"
