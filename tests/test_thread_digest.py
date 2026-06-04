"""Tests for plugins/thread_digest.py — thread_summary, email_digest, digest_history."""

import asyncio
import json

import pytest

import plugins.thread_digest as td


class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeAsyncClient:
    content = json.dumps({"summary": "Egy. Ketto. Harom.", "akcio": "valaszolj"},
                         ensure_ascii=False)

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        body = {"choices": [{"message": {"content": _FakeAsyncClient.content}}]}
        return _FakeResp(json_dumps(body))


def json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


@pytest.fixture
def patched_httpx(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


def _deps(get_db, key="sk-test", gmail_service=None):
    return {
        "get_db": get_db,
        "siliconflow_api_key": key,
        "siliconflow_base_url": "https://example.test/v1",
        "siliconflow_models": {"deepseek": "deepseek-ai/DeepSeek-V4-Pro"},
        "siliconflow_timeout": 5,
        "capture_state": {"gmail_service": gmail_service},
    }


def _tools(fake_app, deps):
    td.register_tools(fake_app, deps)
    return fake_app.tools


def _today():
    return td._today()


def _seed_capture_rows(get_db, date, rows):
    """rows: list of (subject, message, priority)."""
    conn = get_db()
    for i, (subj, msg, prio) in enumerate(rows):
        conn.execute(
            "INSERT INTO messages (timestamp, sender, recipient, subject, message, priority) "
            "VALUES (?, 'capture-daemon', 'kommandant', ?, ?, ?)",
            (f"{date}T0{i}:00:00+00:00", subj, msg, prio),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# register_tools seeds the email_digest recipe (idempotent).
# ---------------------------------------------------------------------------
def test_register_seeds_recipe(fake_app, get_db):
    _tools(fake_app, _deps(get_db))
    conn = get_db()
    row = conn.execute(
        "SELECT name FROM pyramid_recipes WHERE name='email_digest'").fetchone()
    conn.close()
    assert row is not None
    # Second registration must not duplicate.
    from tests.conftest import FakeApp
    _tools(FakeApp(), _deps(get_db))
    conn = get_db()
    cnt = conn.execute(
        "SELECT COUNT(*) FROM pyramid_recipes WHERE name='email_digest'").fetchone()[0]
    conn.close()
    assert cnt == 1


# ---------------------------------------------------------------------------
# 1) thread_summary happy path with text + SF key -> parses LLM JSON.
# ---------------------------------------------------------------------------
def test_thread_summary_text_llm(fake_app, get_db, patched_httpx):
    tools = _tools(fake_app, _deps(get_db))
    out = json.loads(asyncio.run(tools["thread_summary"](
        text="Feladó: a@b.hu Tárgy: valami\n\nHosszú levél.")))
    assert out["source"] == "text"
    assert out["summary"] == "Egy. Ketto. Harom."
    assert out["akcio"] == "valaszolj"


# ---------------------------------------------------------------------------
# 2) thread_summary degraded — no SF key -> raw snippet fallback.
# ---------------------------------------------------------------------------
def test_thread_summary_no_key_fallback(fake_app, get_db):
    tools = _tools(fake_app, _deps(get_db, key=""))
    out = json.loads(asyncio.run(tools["thread_summary"](text="Ez egy nyers level szovege.")))
    assert out["source"] == "text"
    assert "LLM nem elérhető" in out["summary"]


# ---------------------------------------------------------------------------
# 3a) thread_summary bad input — neither thread_id nor text -> error.
# ---------------------------------------------------------------------------
def test_thread_summary_no_input(fake_app, get_db):
    tools = _tools(fake_app, _deps(get_db))
    out = json.loads(asyncio.run(tools["thread_summary"]()))
    assert "error" in out


# ---------------------------------------------------------------------------
# 3b) thread_summary thread_id without Gmail -> error.
# ---------------------------------------------------------------------------
def test_thread_summary_thread_no_gmail(fake_app, get_db):
    tools = _tools(fake_app, _deps(get_db, gmail_service=None))
    out = json.loads(asyncio.run(tools["thread_summary"](thread_id="t1")))
    assert "error" in out
    assert "Gmail" in out["error"]


# ---------------------------------------------------------------------------
# 3c) thread_summary LLM returns non-JSON -> raw content becomes summary.
# ---------------------------------------------------------------------------
def test_thread_summary_bad_llm_json(fake_app, get_db, patched_httpx):
    patched_httpx.content = "ez nem json, csak szoveg"
    tools = _tools(fake_app, _deps(get_db))
    out = json.loads(asyncio.run(tools["thread_summary"](text="valami")))
    assert out["summary"] == "ez nem json, csak szoveg"


# ---------------------------------------------------------------------------
# email_digest: messages_fallback happy path, writes digest_state.
# ---------------------------------------------------------------------------
def test_email_digest_messages_fallback(fake_app, get_db):
    date = _today()
    _seed_capture_rows(get_db, date, [
        ("Sürgős ügy", "...", "urgent"),
        ("Sima info", "...", "normal"),
        ("Fontos kérés", "...", "important"),
    ])
    tools = _tools(fake_app, _deps(get_db, key=""))  # no key -> pure heuristic
    out = json.loads(asyncio.run(tools["email_digest"]()))
    assert out["source"] == "messages_fallback"
    assert out["emails_seen"] == 3
    assert out["needs_reply"] == 2  # urgent + important
    assert out["drafted"] == 0

    conn = get_db()
    row = conn.execute(
        "SELECT emails_seen, needs_reply FROM digest_state WHERE digest_date=?",
        (date,)).fetchone()
    conn.close()
    assert row["emails_seen"] == 3
    assert row["needs_reply"] == 2


# ---------------------------------------------------------------------------
# email_digest: counts drafts from draft_log for the date.
# ---------------------------------------------------------------------------
def test_email_digest_counts_drafts(fake_app, get_db):
    date = _today()
    _seed_capture_rows(get_db, date, [("X", "y", "urgent")])
    conn = get_db()
    conn.execute(
        "INSERT INTO draft_log (thread_id, draft_id, recipient, subject, body_preview, "
        "style_notes, created_at, created_by) VALUES ('t','d','r','s','p','n',?, 'c')",
        (f"{date}T05:00:00+00:00",))
    conn.commit()
    conn.close()
    tools = _tools(fake_app, _deps(get_db, key=""))
    out = json.loads(asyncio.run(tools["email_digest"]()))
    assert out["drafted"] == 1


# ---------------------------------------------------------------------------
# 3d) email_digest no data for the day -> error JSON.
# ---------------------------------------------------------------------------
def test_email_digest_no_data(fake_app, get_db):
    tools = _tools(fake_app, _deps(get_db, key=""))
    out = json.loads(asyncio.run(tools["email_digest"](date="2000-01-01")))
    assert "error" in out


# ---------------------------------------------------------------------------
# 4) Read-only digest_history returns rows newest-first.
# ---------------------------------------------------------------------------
def test_digest_history(fake_app, get_db):
    conn = get_db()
    for d in ("2026-06-01", "2026-06-02", "2026-06-03"):
        conn.execute(
            "INSERT INTO digest_state (digest_date, emails_seen, needs_reply, drafted, "
            "summary, watermark, created_at) VALUES (?, 5, 2, 1, 'sum', 'wm', ?)",
            (d, d + "T00:00:00+00:00"))
    conn.commit()
    conn.close()
    tools = _tools(fake_app, _deps(get_db))
    out = json.loads(asyncio.run(tools["digest_history"](limit=7)))
    assert out["count"] == 3
    assert out["history"][0]["date"] == "2026-06-03"  # newest first
    assert out["history"][0]["emails_seen"] == 5
