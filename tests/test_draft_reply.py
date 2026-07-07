"""Tests for plugins/draft_reply.py — draft_reply (never sends) + draft_reply_log."""

import asyncio
import base64
import json
from unittest.mock import MagicMock

import pytest

import plugins.draft_reply as dr


class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeAsyncClient:
    content = "Kedves Partner,\n\nKoszonom a leveled, visszaigazolom a hataridot.\n\nUdv,\nTamas"

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        import json as _json
        body = {"choices": [{"message": {"content": _FakeAsyncClient.content}}]}
        return _FakeResp(_json.dumps(body, ensure_ascii=False))


@pytest.fixture
def patched_httpx(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


def _b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode()


def _make_gmail_service(thread_payload, draft_id="draft123", few_shot_msgs=None):
    """Build a MagicMock Gmail service whose chained calls return canned payloads."""
    svc = MagicMock()
    users = svc.users.return_value

    # threads().get(...).execute() -> thread_payload
    users.threads.return_value.get.return_value.execute.return_value = thread_payload

    # drafts().create(...).execute() -> {"id": draft_id}
    users.drafts.return_value.create.return_value.execute.return_value = {"id": draft_id}

    # messages().list(...).execute() and messages().get(...).execute() for few-shot
    few_shot_msgs = few_shot_msgs or []
    users.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": m["id"]} for m in few_shot_msgs]
    }

    def _msg_get(userId=None, id=None, format=None):
        payload = next((m["payload"] for m in few_shot_msgs if m["id"] == id),
                       {"mimeType": "text/plain", "body": {"data": _b64("korabbi level")}})
        exec_mock = MagicMock()
        exec_mock.execute.return_value = {"payload": payload}
        return exec_mock

    users.messages.return_value.get.side_effect = _msg_get
    return svc


def _thread_payload():
    return {
        "messages": [{
            "snippet": "Kerlek igazold vissza a hataridot.",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Partner Bela <partner@ceg.hu>"},
                    {"name": "Subject", "value": "Hatarido egyeztetes"},
                    {"name": "Message-ID", "value": "<abc@mail>"},
                ],
                "body": {"data": _b64("Kerlek igazold vissza a hataridot.")},
            },
        }]
    }


def _deps(get_db, gmail_service=None, key="sk-test"):
    return {
        "get_db": get_db,
        "siliconflow_api_key": key,
        "siliconflow_base_url": "https://example.test/v1",
        "siliconflow_models": {"kimi": "moonshotai/Kimi-K2.7-Code"},
        "siliconflow_timeout": 5,
        "capture_state": {"gmail_service": gmail_service},
    }


def _tools(fake_app, deps):
    dr.register_tools(fake_app, deps)
    return fake_app.tools


# ---------------------------------------------------------------------------
# 1) Happy path — draft created, DB row written, no send() ever called.
# ---------------------------------------------------------------------------
def test_draft_reply_happy_path(fake_app, get_db, patched_httpx):
    svc = _make_gmail_service(_thread_payload(), draft_id="draft999")
    tools = _tools(fake_app, _deps(get_db, gmail_service=svc))
    out = asyncio.run(tools["draft_reply"](
        thread_id="t1", instruction="koszond meg", caller="tester"))
    res = json.loads(out)
    assert res["draft_id"] == "draft999"
    assert res["recipient"] == "partner@ceg.hu"
    assert res["subject"].startswith("Re:")
    assert res["body_preview"]

    # drafts().create was used; send() must never be touched.
    svc.users().drafts().create.assert_called()
    assert not svc.users().messages().send.called

    conn = get_db()
    row = conn.execute("SELECT thread_id, draft_id, recipient FROM draft_log").fetchone()
    conn.close()
    assert row["draft_id"] == "draft999"
    assert row["recipient"] == "partner@ceg.hu"


# ---------------------------------------------------------------------------
# 2) Degraded — no Gmail service -> graceful error JSON, nothing written.
# ---------------------------------------------------------------------------
def test_draft_reply_no_service(fake_app, get_db, patched_httpx):
    tools = _tools(fake_app, _deps(get_db, gmail_service=None))
    out = asyncio.run(tools["draft_reply"](thread_id="t1"))
    res = json.loads(out)
    assert "error" in res

    conn = get_db()
    cnt = conn.execute("SELECT COUNT(*) FROM draft_log").fetchone()[0]
    conn.close()
    assert cnt == 0


# ---------------------------------------------------------------------------
# 3a) Bad input — empty thread -> error JSON.
# ---------------------------------------------------------------------------
def test_draft_reply_empty_thread(fake_app, get_db, patched_httpx):
    svc = _make_gmail_service({"messages": []})
    tools = _tools(fake_app, _deps(get_db, gmail_service=svc))
    out = asyncio.run(tools["draft_reply"](thread_id="t1"))
    res = json.loads(out)
    assert "error" in res


# ---------------------------------------------------------------------------
# 3b) No SF key -> body generation empty -> error JSON, no draft created.
# ---------------------------------------------------------------------------
def test_draft_reply_no_sf_key(fake_app, get_db, patched_httpx):
    svc = _make_gmail_service(_thread_payload())
    tools = _tools(fake_app, _deps(get_db, gmail_service=svc, key=""))
    out = asyncio.run(tools["draft_reply"](thread_id="t1"))
    res = json.loads(out)
    assert "error" in res
    assert not svc.users().drafts().create.called


# ---------------------------------------------------------------------------
# 4) Read-only log tool returns rows newest-first.
# ---------------------------------------------------------------------------
def test_draft_reply_log(fake_app, get_db, patched_httpx):
    svc = _make_gmail_service(_thread_payload())
    tools = _tools(fake_app, _deps(get_db, gmail_service=svc))
    asyncio.run(tools["draft_reply"](thread_id="t1", caller="c1"))
    asyncio.run(tools["draft_reply"](thread_id="t2", caller="c2"))

    out = json.loads(asyncio.run(tools["draft_reply_log"](limit=10)))
    assert isinstance(out, list)
    assert len(out) == 2
    # Newest first (id DESC)
    assert out[0]["id"] > out[1]["id"]
    assert {r["created_by"] for r in out} == {"c1", "c2"}
