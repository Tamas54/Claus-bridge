"""test_pythia_p1_model_env.py — PYTHIA P1 modell-env vasszabályok.

- pollster: ORAKEL_MODEL default = tencent/Hy3; _chat kap model paramétert
  (None → a _provider() default-ja az útvonal); kimerült retry = HANGOS hiba.
- delphoi: DELPHOI_NOWCAST_MODEL / DELPHOI_FG_MODEL default = tencent/Hy3;
  FG_PANEL_VERSION = orakel-agora-hu-v2-hy3.
- Flash SEHOL nem default — és a defaultokban 'flash' szubsztring sincs.
"""
import asyncio
import inspect

import pytest

from plugins import delphoi, pollster


def test_pollster_default_model_is_hy3(monkeypatch):
    monkeypatch.delenv("ORAKEL_MODEL", raising=False)
    monkeypatch.delenv("ORAKEL_PROVIDER", raising=False)
    # a modul-szintű default (import-időben rögzül) — env nélkül Hy3
    assert pollster.MODEL == "tencent/Hy3"
    assert "flash" not in pollster.MODEL.lower()
    url, _key, model, use_think = pollster._provider()
    assert model == "tencent/Hy3"
    assert use_think is True  # thinking:disabled útvonal feltétel nélkül él


def test_delphoi_branch_models_default_hy3():
    assert delphoi.NOWCAST_MODEL == "tencent/Hy3"
    assert delphoi.FG_MODEL == "tencent/Hy3"
    assert "flash" not in delphoi.NOWCAST_MODEL.lower()
    assert "flash" not in delphoi.FG_MODEL.lower()


def test_fg_panel_version_bumped():
    assert delphoi.FG_PANEL_VERSION == "orakel-agora-hu-v2-hy3"


def test_chat_has_model_override_param():
    sig = inspect.signature(pollster._chat)
    assert "model" in sig.parameters
    assert sig.parameters["model"].default is None


class _FakeResp:
    def __init__(self, content):
        self._content = content
    def raise_for_status(self):
        pass
    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeClient:
    def __init__(self, content="PÁRT: Tisza\nINDOK: ok"):
        self.bodies = []
        self._content = content
    async def post(self, url, json=None):
        self.bodies.append(json)
        return _FakeResp(self._content)


def test_chat_model_override_goes_into_body():
    client = _FakeClient()
    out = asyncio.run(pollster._chat(client, "kérdés", model="custom/Model-X"))
    assert out.startswith("PÁRT")
    assert client.bodies[0]["model"] == "custom/Model-X"
    assert client.bodies[0]["thinking"] == {"type": "disabled"}  # feltétel nélkül


def test_chat_default_route_is_provider_model():
    client = _FakeClient()
    asyncio.run(pollster._chat(client, "kérdés"))
    assert client.bodies[0]["model"] == pollster._provider()[2]


def test_chat_exhausted_retries_is_loud():
    class _EmptyClient:
        async def post(self, url, json=None):
            return _FakeResp("")   # mindig üres → retry-k után HANGOS hiba
    with pytest.raises(RuntimeError, match="failed after retries"):
        asyncio.run(pollster._chat(_EmptyClient(), "kérdés", retries=2, model="tencent/Hy3"))


def test_no_flash_fallback_in_chat_source():
    """Regressziós őr: a _chat-ben nincs modell-csere kódút (Flash-string sem)."""
    src = inspect.getsource(pollster._chat)
    assert "Flash" not in src
    assert "fallback" not in src.lower() or "NINCS silent fallback" in src
