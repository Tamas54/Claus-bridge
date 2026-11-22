"""CÍMZETT, FEJLÉC, NÉV SZERINTI ÚTVÁLASZTÁS (2026-11-22).

A Kommandant: „Kimi még nem tudja ő kicsoda” (a Haiku válaszolt Kimi-jelmezben), „a telegrammomon keveredik melyik
ügynök mit küld”, „szabad szövegesen mondhassam meg, hogy melyik ügynöknek megy az üzi”.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import cimzett as C
import llm_kapu


@pytest.mark.parametrize("szoveg,elvart", [
    ("Kimi, mit gondolsz a forintról?", "kimi"),
    ("Kiminek: nézd meg ezt", "kimi"),
    ("kimi mit gondolsz", "kimi"),
    ("DeepSeek: elemezd", "deepseek"),
    ("Kérdezd meg a GLM-et a kódról", "glm5"),
    ("Feldwebel, mi a helyzet?", "feldwebel"),
    ("Faber, tanuld meg", "faber"),
    ("@deepseek szia", "deepseek"),
    ("mit mondott tegnap a Kimi?", None),          # nem megszólítás, csak említés
    ("kiminél jobb?", None),
    ("Szia, hogy vagy?", None),
])
def test_megszolitas_az_elejen(szoveg, elvart):
    assert C.cimzett(szoveg) == elvart


def test_egyseges_fejlec_a_valaszolo_modellel():
    assert C.fejlec("kimi", "moonshotai/Kimi-K2.7-Code") == "🔭 <b>KIMI</b> · Kimi-K2.7-Code"
    assert C.fejlec("feldwebel", "claude-haiku-5-5") == "🪖 <b>FELDWEBEL</b> · Haiku 5.5"
    assert C.fejlec("glm5") == "🧩 <b>GLM</b>"


@pytest.mark.parametrize("purpose,elvart", [
    ("telegram/chat/kimi", "kimi"),
    ("ai_query/deepseek", "deepseek"),
    ("dispatch/glm5", "glm5"),
    ("agent_with_tools/moonshotai/Kimi-K2.7-Code/round0", "moonshotai/Kimi-K2.7-Code"),
    ("ai_query/kimi/synth", None),                 # a Bridge saját összegzése marad a kapun
    ("feldwebel/telegram", None),
    ("melleklet/atiras", None),
])
def test_nev_szerinti_ugynok(purpose, elvart):
    assert llm_kapu.nevesitett_ugynok(purpose) == elvart


def test_a_megszolitott_ugynok_a_sajat_modelljen_valaszol(monkeypatch):
    """Anthropic-elsődlegesség mellett is: a `telegram/chat/kimi` a SiliconFlow-ra megy, nem a Claude-ra."""
    import server
    monkeypatch.setattr(server.llm_kapu, "elsodleges", lambda: "anthropic")
    monkeypatch.setattr(server.llm_kapu, "elerheto", lambda: True)
    monkeypatch.setattr(server, "_sf_keys", lambda: ["k"])
    hivott = []

    async def anthropic_chat(payload, *, purpose=""):
        hivott.append(("anthropic", purpose))
        return {"choices": [{"message": {"content": "Claude"}}], "model": "claude-haiku-5-5"}, None, []
    monkeypatch.setattr(server.llm_kapu, "chat", anthropic_chat)

    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "Kimi vagyok"}, "finish_reason": "stop"}],
                    "model": "moonshotai/Kimi-K2.7-Code", "usage": {}}

    class Kliens:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            hivott.append(("siliconflow", k.get("json", {}).get("model")))
            return R()
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Kliens)
    data, err, _ = asyncio.run(server.sf_chat({"model": "moonshotai/Kimi-K2.7-Code", "messages": [
        {"role": "user", "content": "szia"}]}, purpose="telegram/chat/kimi"))
    assert err is None and hivott[0] == ("siliconflow", "moonshotai/Kimi-K2.7-Code")
    assert all(h[0] != "anthropic" for h in hivott)
    # a Feldwebel továbbra is a kapun megy
    hivott.clear()
    asyncio.run(server.sf_chat({"model": "x", "messages": [{"role": "user", "content": "szia"}]},
                               purpose="feldwebel/telegram"))
    assert hivott[0][0] == "anthropic"


def test_a_csapat_tudja_ki_kicsoda():
    t = json.load(open("pyramid/profiles/team.json", encoding="utf-8"))
    assert "feldwebel" in t and "faber" in t and "agora_personak" in t
    assert "Swarm" not in t["kimi"]["note"] and "helyetted" in t["kimi"]["note"]
    b = json.load(open("pyramid/profiles/bridge_info.json", encoding="utf-8"))
    assert b["database"].startswith("SQLite")


def test_a_gondolatmenet_nem_kerul_a_valaszba():
    import server
    d = {"choices": [{"message": {"content": "A felhasználó kérdez… gondolkodom.</think>Kimi vagyok."}}]}
    server._gondolat_levag(d)
    assert d["choices"][0]["message"]["content"] == "Kimi vagyok."
    d = {"choices": [{"message": {"content": "<think>belső</think>\n\nVálasz."}}]}
    server._gondolat_levag(d)
    assert d["choices"][0]["message"]["content"] == "Válasz."
    d = {"choices": [{"message": {"content": "Sima válasz."}}]}
    server._gondolat_levag(d)
    assert d["choices"][0]["message"]["content"] == "Sima válasz."


def test_k3_nem_kap_temperaturet_es_a_tartalek_nem_adja_ki_magat(monkeypatch):
    import server
    monkeypatch.setattr(server.llm_kapu, "elsodleges", lambda: "anthropic")
    monkeypatch.setattr(server, "_sf_keys", lambda: ["k"])
    kuldott = []

    class R:
        def __init__(self, ok):
            self.status_code = 200 if ok else 400
            self.text = "" if ok else '{"code":20012,"message":"Model does not exist"}'

        def json(self):
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}

    class Kliens:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            j = k.get("json", {})
            kuldott.append(j)
            return R(ok=not j["model"].startswith("moonshotai/Kimi-K3.1"))
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Kliens)
    payload = {"model": "moonshotai/Kimi-K3.1-Pro", "temperature": 0.7,
               "messages": [{"role": "system", "content": "Te Kimi vagy."}, {"role": "user", "content": "ki vagy?"}]}
    data, err, notes = asyncio.run(server.sf_chat(payload, purpose="ai_query/kimi"))
    assert "temperature" not in kuldott[0]                             # a K3 el sem kapja
    tartalek = kuldott[-1]
    assert tartalek["model"] == "deepseek-ai/DeepSeek-V4-Pro-0813"
    assert "HELYETTESÍTŐKÉNT" in tartalek["messages"][0]["content"] and "Te Kimi vagy." in tartalek["messages"][0]["content"]
    assert payload["messages"][0]["content"] == "Te Kimi vagy."      # a hívó üzenetei érintetlenek
    assert any("MODELLVÁLTÁS" in n for n in notes)
