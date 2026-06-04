"""Tests for plugins/signal_gatekeeper.py — judge + history tools."""

import asyncio
import json

import pytest

import plugins.signal_gatekeeper as sg


# ---------------------------------------------------------------------------
# Fake httpx layer: a minimal AsyncClient whose .post returns a response whose
# .text is whatever the test wants the "LLM" to say.
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeAsyncClient:
    _next_content = None  # raw string that becomes choices[0].message.content

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        body = {
            "choices": [{"message": {"content": _FakeAsyncClient._next_content}}]
        }
        return _FakeResp(json_dumps(body))


def json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


@pytest.fixture
def patched_httpx(monkeypatch):
    """Patch httpx.AsyncClient inside the plugin's imported httpx module."""
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


def _deps(get_db, key="sk-test"):
    return {
        "siliconflow_api_key": key,
        "siliconflow_base_url": "https://example.test/v1",
        "siliconflow_models": {"deepseek": "deepseek-ai/DeepSeek-V4-Pro"},
        "siliconflow_timeout": 5,
        "get_db": get_db,
    }


def _tools(fake_app, get_db, key="sk-test"):
    sg.register_tools(fake_app, _deps(get_db, key))
    return fake_app.tools


# ---------------------------------------------------------------------------
# 1) Happy path — valid verdict JSON, DB row written.
# ---------------------------------------------------------------------------
def test_judge_happy_path_writes_db(fake_app, get_db, patched_httpx):
    patched_httpx._next_content = json_dumps({
        "verdikt": "oksagi_hid",
        "indoklas": "Aszaly -> kinalatszukules -> aremelkedes.",
        "heurisztika_hasznalt": "kategoria_mozdulat",
        "konfidencia": 0.8,
    })
    tools = _tools(fake_app, get_db)
    out = asyncio.run(tools["signal_gatekeeper_judge"](
        signal_x="tartos aszaly", outcome_y="emelkedik a kenyer ara", caller="tester"))
    res = json.loads(out)
    assert res["verdikt"] == "oksagi_hid"
    assert res["konfidencia"] == 0.8
    assert "id" in res

    conn = get_db()
    rows = conn.execute("SELECT signal_x, verdikt, created_by FROM signal_gatekeeper").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["verdikt"] == "oksagi_hid"
    assert rows[0]["created_by"] == "tester"


# ---------------------------------------------------------------------------
# 2) Degraded path — empty API key -> error JSON, no LLM, no DB write.
# ---------------------------------------------------------------------------
def test_judge_no_api_key(fake_app, get_db):
    tools = _tools(fake_app, get_db, key="")
    out = asyncio.run(tools["signal_gatekeeper_judge"](
        signal_x="x", outcome_y="y"))
    res = json.loads(out)
    assert "error" in res
    assert "SILICONFLOW_API_KEY" in res["error"]

    conn = get_db()
    cnt = conn.execute("SELECT COUNT(*) FROM signal_gatekeeper").fetchone()[0]
    conn.close()
    assert cnt == 0


# ---------------------------------------------------------------------------
# 3a) Bad input — empty signal/outcome -> error JSON.
# ---------------------------------------------------------------------------
def test_judge_empty_inputs(fake_app, get_db, patched_httpx):
    patched_httpx._next_content = json_dumps({"verdikt": "definial"})
    tools = _tools(fake_app, get_db)
    out = asyncio.run(tools["signal_gatekeeper_judge"](signal_x="", outcome_y="  "))
    res = json.loads(out)
    assert "error" in res


# ---------------------------------------------------------------------------
# 3b) Invalid (non-JSON) LLM response -> error JSON with nyers_valasz.
# ---------------------------------------------------------------------------
def test_judge_invalid_llm_json(fake_app, get_db, patched_httpx):
    patched_httpx._next_content = "Sajnalom, nem tudok JSON-t adni."
    tools = _tools(fake_app, get_db)
    out = asyncio.run(tools["signal_gatekeeper_judge"](
        signal_x="x", outcome_y="y"))
    res = json.loads(out)
    assert "error" in res
    assert "nyers_valasz" in res

    conn = get_db()
    cnt = conn.execute("SELECT COUNT(*) FROM signal_gatekeeper").fetchone()[0]
    conn.close()
    assert cnt == 0


# ---------------------------------------------------------------------------
# 3c) Unknown verdict normalizes to csak_korrelal (most conservative).
# ---------------------------------------------------------------------------
def test_judge_unknown_verdict_normalizes(fake_app, get_db, patched_httpx):
    patched_httpx._next_content = json_dumps({
        "verdikt": "marslako", "konfidencia": 5.0})  # invalid verdict + out-of-range conf
    tools = _tools(fake_app, get_db)
    out = asyncio.run(tools["signal_gatekeeper_judge"](signal_x="x", outcome_y="y"))
    res = json.loads(out)
    assert res["verdikt"] == "csak_korrelal"
    assert res["konfidencia"] == 1.0  # clamped to 1.0


# ---------------------------------------------------------------------------
# 3d) Markdown-fenced JSON is still parsed (tests _extract_json).
# ---------------------------------------------------------------------------
def test_judge_markdown_fence(fake_app, get_db, patched_httpx):
    inner = json_dumps({"verdikt": "definial", "konfidencia": 0.99})
    patched_httpx._next_content = f"```json\n{inner}\n```"
    tools = _tools(fake_app, get_db)
    out = asyncio.run(tools["signal_gatekeeper_judge"](signal_x="x", outcome_y="y"))
    res = json.loads(out)
    assert res["verdikt"] == "definial"


# ---------------------------------------------------------------------------
# 4) Read-only history tool: filtering + correct output.
# ---------------------------------------------------------------------------
def test_history_lists_and_filters(fake_app, get_db, patched_httpx):
    tools = _tools(fake_app, get_db)
    for v in ("definial", "oksagi_hid", "oksagi_hid"):
        patched_httpx._next_content = json_dumps({"verdikt": v, "konfidencia": 0.5})
        asyncio.run(tools["signal_gatekeeper_judge"](signal_x="x", outcome_y="y"))

    out_all = json.loads(asyncio.run(tools["signal_gatekeeper_history"](limit=10)))
    assert out_all["count"] == 3

    out_f = json.loads(asyncio.run(tools["signal_gatekeeper_history"](limit=10, verdikt="oksagi_hid")))
    assert out_f["count"] == 2
    assert all(it["verdikt"] == "oksagi_hid" for it in out_f["items"])


def test_history_invalid_filter(fake_app, get_db):
    tools = _tools(fake_app, get_db)
    out = json.loads(asyncio.run(tools["signal_gatekeeper_history"](verdikt="nonsense")))
    assert "error" in out
