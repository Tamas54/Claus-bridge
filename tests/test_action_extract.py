"""Tests for plugins/action_extract.py — extract, list, decide (never auto-creates)."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

import plugins.action_extract as ax


class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeAsyncClient:
    content = json.dumps([
        {"kind": "reminder", "title": "Szerzodes alairas", "due_at": "2026-06-12",
         "details": "hatarido"},
    ], ensure_ascii=False)

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


def _deps(get_db, key="sk-test", calendar_service=None, gmail_service=None):
    return {
        "get_db": get_db,
        "siliconflow_api_key": key,
        "siliconflow_base_url": "https://example.test/v1",
        "siliconflow_models": {"deepseek": "deepseek-ai/DeepSeek-V4-Pro"},
        "siliconflow_timeout": 5,
        "capture_state": {
            "calendar_service": calendar_service,
            "gmail_service": gmail_service,
        },
    }


def _tools(fake_app, deps):
    ax.register_tools(fake_app, deps)
    return fake_app.tools


# ---------------------------------------------------------------------------
# 1) LLM happy path — draft saved as pending.
# ---------------------------------------------------------------------------
def test_extract_llm_happy_path(fake_app, get_db, patched_httpx):
    tools = _tools(fake_app, _deps(get_db))
    out = json.loads(asyncio.run(tools["action_extract"](
        text="Kerjuk a szerzodest 2026-06-12-ig alairni.", source_message_id="msg-7")))
    assert out["method"] == "llm"
    assert len(out["draft_ids"]) == 1
    assert out["extracted"][0]["title"] == "Szerzodes alairas"

    conn = get_db()
    row = conn.execute(
        "SELECT kind, title, status, source_message_id FROM action_drafts").fetchone()
    conn.close()
    assert row["status"] == "pending"
    assert row["kind"] == "reminder"
    assert row["source_message_id"] == "msg-7"


# ---------------------------------------------------------------------------
# 2) Degraded — no key -> regex fallback (ISO date found).
# ---------------------------------------------------------------------------
def test_extract_regex_fallback_no_key(fake_app, get_db):
    tools = _tools(fake_app, _deps(get_db, key=""))
    out = json.loads(asyncio.run(tools["action_extract"](
        text="Talalkozo 2026-07-01 napon.")))
    assert out["method"] == "regex_fallback"
    assert len(out["draft_ids"]) >= 1
    assert any("2026-07-01" in a["due_at"] for a in out["extracted"])


# ---------------------------------------------------------------------------
# 2b) LLM returns non-JSON -> _extract_with_llm returns None -> regex fallback.
# ---------------------------------------------------------------------------
def test_extract_llm_bad_json_falls_back(fake_app, get_db, patched_httpx):
    patched_httpx.content = "ez nem json"
    tools = _tools(fake_app, _deps(get_db))
    out = json.loads(asyncio.run(tools["action_extract"](text="holnap delben talalkozunk")))
    assert out["method"] == "regex_fallback"
    # 'holnap' keyword should yield a reminder
    assert any("holnap" in a["title"].lower() for a in out["extracted"])


# ---------------------------------------------------------------------------
# 3a) Bad input — empty text and no thread_id -> error.
# ---------------------------------------------------------------------------
def test_extract_empty_input(fake_app, get_db):
    tools = _tools(fake_app, _deps(get_db, key=""))
    out = json.loads(asyncio.run(tools["action_extract"]()))
    assert "error" in out


# ---------------------------------------------------------------------------
# 3b) thread_id given but no Gmail service -> graceful error.
# ---------------------------------------------------------------------------
def test_extract_thread_no_gmail(fake_app, get_db):
    tools = _tools(fake_app, _deps(get_db, key="", gmail_service=None))
    out = json.loads(asyncio.run(tools["action_extract"](thread_id="t1")))
    assert "error" in out


# ---------------------------------------------------------------------------
# 4a) Read-only list tool returns pending drafts.
# ---------------------------------------------------------------------------
def test_drafts_list(fake_app, get_db, patched_httpx):
    tools = _tools(fake_app, _deps(get_db))
    asyncio.run(tools["action_extract"](text="2026-06-12 hatarido"))
    out = json.loads(asyncio.run(tools["action_drafts_list"](status="pending")))
    assert out["count"] == 1
    assert out["drafts"][0]["status"] == "pending"


# ---------------------------------------------------------------------------
# 4b) decide reject -> status rejected, no calendar event.
# ---------------------------------------------------------------------------
def test_decide_reject(fake_app, get_db, patched_httpx):
    tools = _tools(fake_app, _deps(get_db))
    ex = json.loads(asyncio.run(tools["action_extract"](text="2026-06-12 hatarido")))
    did = ex["draft_ids"][0]
    out = json.loads(asyncio.run(tools["action_draft_decide"](draft_id=did, decision="reject")))
    assert out["status"] == "rejected"
    assert out["calendar_created"] is False


# ---------------------------------------------------------------------------
# decide approve, reminder kind, no calendar service -> approved, no event.
# ---------------------------------------------------------------------------
def test_decide_approve_reminder(fake_app, get_db, patched_httpx):
    tools = _tools(fake_app, _deps(get_db))
    ex = json.loads(asyncio.run(tools["action_extract"](text="2026-06-12 hatarido")))
    did = ex["draft_ids"][0]
    out = json.loads(asyncio.run(tools["action_draft_decide"](draft_id=did, decision="approve")))
    assert out["status"] == "approved"
    assert out["calendar_created"] is False


# ---------------------------------------------------------------------------
# decide approve, calendar kind + live service -> real event created.
# ---------------------------------------------------------------------------
def test_decide_approve_calendar_creates_event(fake_app, get_db, patched_httpx):
    patched_httpx.content = json_dumps([
        {"kind": "calendar", "title": "Megbeszeles", "due_at": "2026-06-12T10:00:00",
         "details": "iroda"},
    ])
    cal = MagicMock()
    cal.events.return_value.insert.return_value.execute.return_value = {
        "id": "evt-1", "htmlLink": "https://cal/evt-1"}
    tools = _tools(fake_app, _deps(get_db, calendar_service=cal))
    ex = json.loads(asyncio.run(tools["action_extract"](text="2026-06-12 10:00 megbeszeles")))
    did = ex["draft_ids"][0]
    out = json.loads(asyncio.run(tools["action_draft_decide"](draft_id=did, decision="approve")))
    assert out["status"] == "created"
    assert out["calendar_created"] is True
    assert out["event_id"] == "evt-1"
    cal.events().insert.assert_called()


# ---------------------------------------------------------------------------
# decide invalid decision + nonexistent draft.
# ---------------------------------------------------------------------------
def test_decide_invalid_decision(fake_app, get_db):
    tools = _tools(fake_app, _deps(get_db, key=""))
    out = json.loads(asyncio.run(tools["action_draft_decide"](draft_id=1, decision="maybe")))
    assert "error" in out


def test_decide_missing_draft(fake_app, get_db):
    tools = _tools(fake_app, _deps(get_db, key=""))
    out = json.loads(asyncio.run(tools["action_draft_decide"](draft_id=999, decision="approve")))
    assert "error" in out
