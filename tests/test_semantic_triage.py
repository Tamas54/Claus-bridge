"""Tests for plugins/semantic_triage.py — semantic_triage + stats tools."""

import asyncio
import json

import pytest

import plugins.semantic_triage as st


class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeAsyncClient:
    """Routes by URL: /embeddings -> embedding payload, /chat -> gatekeeper JSON.

    Embeddings: returns a fixed vector per text. To make 'urgent' win, we make
    every prototype/email vector point near a controllable target via the
    `embed_fn` hook, set per-test.
    """
    embed_fn = None          # callable(text) -> list[float]
    chat_content = None      # str (gatekeeper content) or None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        if url.endswith("/embeddings"):
            texts = json["input"]
            data = [{"index": i, "embedding": _FakeAsyncClient.embed_fn(t)}
                    for i, t in enumerate(texts)]
            return _FakeResp(json_dumps({"data": data}))
        # chat completions (gatekeeper)
        body = {"choices": [{"message": {"content": _FakeAsyncClient.chat_content}}]}
        return _FakeResp(json_dumps(body))


def json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


@pytest.fixture(autouse=True)
def reset_centroids():
    """The centroid cache is module-global; clear it before each test."""
    st._CENTROID_CACHE.clear()
    yield
    st._CENTROID_CACHE.clear()


@pytest.fixture
def patched_httpx(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.chat_content = None
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
    st.register_tools(fake_app, _deps(get_db, key))
    return fake_app.tools


# A deterministic embedder that makes the 'urgent' prototypes (and any text
# containing the marker) map to vector [1,0], everything else to [0,1].
def _urgent_embed(marker="URGENTMARK"):
    urgent_protos = set(st.CATEGORY_PROTOTYPES["urgent"])

    def fn(text):
        if text in urgent_protos or marker in text:
            return [1.0, 0.0]
        return [0.0, 1.0]
    return fn


# ---------------------------------------------------------------------------
# 1) Happy path — embedding classifies email as urgent, DB row written.
# ---------------------------------------------------------------------------
def test_triage_embedding_happy_path(fake_app, get_db, patched_httpx):
    patched_httpx.embed_fn = _urgent_embed()
    tools = _tools(fake_app, get_db)
    out = asyncio.run(tools["semantic_triage"](
        sender="boss@ceg.hu", subject="URGENTMARK kell most", body="URGENTMARK",
        message_id="msg-1"))
    res = json.loads(out)
    assert res["category"] == "urgent"
    assert res["method"] == "embedding"
    assert res["score"] > 0.9

    conn = get_db()
    rows = conn.execute("SELECT message_id, category, method FROM semantic_triage_log").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["category"] == "urgent"
    assert rows[0]["method"] == "embedding"


# ---------------------------------------------------------------------------
# 2) Degraded path — no API key -> keyword fallback, DB logs keyword_fallback.
# ---------------------------------------------------------------------------
def test_triage_keyword_fallback_no_key(fake_app, get_db):
    tools = _tools(fake_app, get_db, key="")
    out = asyncio.run(tools["semantic_triage"](
        sender="ceo@partner.hu", subject="sürgős teendő", body="", message_id="m2"))
    res = json.loads(out)
    assert res["method"] == "keyword_fallback"
    assert res["category"] == "urgent"  # 'sürgős' is an URGENT_KEYWORD

    conn = get_db()
    row = conn.execute("SELECT method, category FROM semantic_triage_log").fetchone()
    conn.close()
    assert row["method"] == "keyword_fallback"


def test_triage_keyword_fallback_ignore_sender(fake_app, get_db):
    tools = _tools(fake_app, get_db, key="")
    out = asyncio.run(tools["semantic_triage"](
        sender="noreply@news.com", subject="hello", body="", message_id="m3"))
    res = json.loads(out)
    assert res["category"] == "ignore"
    assert res["method"] == "keyword_fallback"


# ---------------------------------------------------------------------------
# 3) Borderline + gatekeeper: identical embeddings make all categories tie,
#    so borderline=True; gatekeeper (use_gatekeeper) overrides category.
# ---------------------------------------------------------------------------
def test_triage_borderline_gatekeeper(fake_app, get_db, patched_httpx):
    # All texts map to the same vector -> all cosine scores equal -> borderline.
    patched_httpx.embed_fn = lambda text: [1.0, 1.0]
    patched_httpx.chat_content = json_dumps({
        "verdict": "definial", "category": "important", "reason_hu": "teszt"})
    tools = _tools(fake_app, get_db)
    out = asyncio.run(tools["semantic_triage"](
        sender="x@y.hu", subject="valami", body="szoveg",
        message_id="m4", use_gatekeeper=True))
    res = json.loads(out)
    assert res["borderline"] is True
    assert res["gatekeeper"] is not None
    assert res["gatekeeper"]["verdict"] == "definial"
    assert res["category"] == "important"  # gatekeeper override applied


# ---------------------------------------------------------------------------
# 3b) Embedding API error -> falls back to keyword logic.
# ---------------------------------------------------------------------------
def test_triage_embed_error_falls_back(fake_app, get_db, patched_httpx):
    def boom(text):
        raise RuntimeError("embedding down")
    patched_httpx.embed_fn = boom
    tools = _tools(fake_app, get_db)
    out = asyncio.run(tools["semantic_triage"](
        sender="a@b.hu", subject="normál levél", body="", message_id="m5"))
    res = json.loads(out)
    assert res["method"] == "keyword_fallback"


# ---------------------------------------------------------------------------
# 4) Read-only stats tool aggregates per category.
# ---------------------------------------------------------------------------
def test_stats_aggregates(fake_app, get_db, patched_httpx):
    patched_httpx.embed_fn = _urgent_embed()
    tools = _tools(fake_app, get_db)
    for i in range(3):
        asyncio.run(tools["semantic_triage"](
            sender="s@x.hu", subject="URGENTMARK", body="URGENTMARK", message_id=f"u{i}"))
    out = json.loads(asyncio.run(tools["semantic_triage_stats"](days=7)))
    assert out["total"] == 3
    cats = {c["category"]: c for c in out["by_category"]}
    assert "urgent" in cats
    assert cats["urgent"]["count"] == 3
    assert cats["urgent"]["avg_score"] > 0.9


def test_stats_no_db():
    """get_db missing -> graceful error JSON."""
    app = type("A", (), {"tools": {}})()
    # Build a minimal fake app to capture tools.
    from tests.conftest import FakeApp
    fa = FakeApp()
    st.register_tools(fa, {"siliconflow_api_key": "", "get_db": None})
    out = json.loads(asyncio.run(fa.tools["semantic_triage_stats"](days=7)))
    assert out.get("error") == "no_db"
