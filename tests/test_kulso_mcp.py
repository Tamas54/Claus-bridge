"""KÜLSŐ MCP (kulso_mcp.py) — Lexonomics / Medical / Studio a Feldwebelnek, egy-egy eszközként.

A valódi szerverek helyett egy memóriabeli FastMCP-szerver: a kliens ugyanazt a protokollt beszéli vele.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import Client, FastMCP

import kulso_mcp as k

teszt_szerver = FastMCP("teszt", instructions="Ez a teszt-szerver útmutatója.")


@teszt_szerver.tool
def kereses(query: str, limit: int = 5) -> str:
    """Keresés a teszt-adatbázisban. Hosszabb magyarázat, ami a listába már nem kerül."""
    return f"találat: {query} ({limit})"


@teszt_szerver.tool
def hosszu() -> str:
    """Nagyon hosszú válasz."""
    return "x" * (k.MAX_EREDMENY_KAR + 500)


def _run(c):
    return asyncio.run(c)


@pytest.fixture(autouse=True)
def tiszta(monkeypatch):
    k._LISTAK.clear()
    k._HIBAK.clear()
    monkeypatch.setattr(k, "_kliens", lambda nev: Client(teszt_szerver))
    monkeypatch.delenv("LEXONOMICS_MCP_KEY", raising=False)
    monkeypatch.delenv("STUDIO_MCP_KEY", raising=False)
    monkeypatch.delenv("ECHOLOT_ENGINE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    yield
    k._LISTAK.clear()
    k._HIBAK.clear()


def test_kulcs_nelkul_csak_a_nyilvanos_szerver_jelenik_meg(monkeypatch):
    nevek = [d["function"]["name"] for d in _run(k.eszkoz_definiciok())]
    assert nevek == ["medical", "echolot", "makronom"]           # a nyilvánosak
    monkeypatch.setenv("LEXONOMICS_MCP_KEY", "nxk_teszt")
    monkeypatch.setenv("STUDIO_MCP_KEY", "studio_teszt")
    monkeypatch.setenv("ECHOLOT_ENGINE_KEY", "engine_teszt")
    nevek = [d["function"]["name"] for d in _run(k.eszkoz_definiciok())]
    assert nevek == ["lexonomics", "medical", "echolot", "makronom", "engine", "studio"]   # állandó sorrend (cache)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TOKEN")                  # a földgömb kulcsa a bot-tokenből jön
    nevek = [d["function"]["name"] for d in _run(k.eszkoz_definiciok())]
    assert nevek == ["lexonomics", "medical", "echolot", "makronom", "engine", "foldgomb", "studio"]


def test_foldgomb_kulcsa_sajat_tartomany_a_bot_tokenbol(monkeypatch):
    """11-25: a God's Eye View /mcp-kapuja (kapu.mjs) sha256("foldgomb:" + bot-token)-t vár — új titok nélkül."""
    import hashlib
    assert k._kulcs("foldgomb") == "" and not k.bekotott("foldgomb")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TOKEN")
    assert k._kulcs("foldgomb") == hashlib.sha256(b"foldgomb:123:TOKEN").hexdigest() and k.bekotott("foldgomb")
    assert k._kulcs("foldgomb") != hashlib.sha256(b"futar:123:TOKEN").hexdigest()      # más tartomány, mint a futáré
    assert "panel_request" in k.SZERVEREK["foldgomb"]["tiltott"]


def test_a_helyzetkep_kerdes_a_foldgomb_kategoriat_tolti_be():
    from feldwebel import eszkoz_ut
    assert eszkoz_ut.kategoria("foldgomb") == "foldgomb"
    for szoveg in ("mi a helyzet a Bab el-Mandeb-szorosban, látszanak tankerek?", "milyen katonai repülők vannak a "
                   "Balti-tenger felett?", "keresd meg ezt a hajót: Ever Given", "van erdőtűz Görögországban?",
                   "God’s eye view-ban mutasd Tajvant", "Gods eye: Tajvan"):
        assert "foldgomb" in eszkoz_ut.szandek(szoveg), szoveg
    for szoveg in ("mi a helyzet a forinttal?", "írj Attilának", "be van dugva a HDD?"):
        assert "foldgomb" not in eszkoz_ut.szandek(szoveg), szoveg


def test_definicio_a_neveket_es_parametereket_sorolja():
    d = _run(k.eszkoz_definiciok())[0]["function"]
    assert "- kereses(query, limit?): Keresés a teszt-adatbázisban" in d["description"]
    assert "Hosszabb magyarázat" not in d["description"]          # csak az első mondat
    assert d["parameters"]["properties"]["eszkoz"]["enum"] == ["kereses", "hosszu", "sugo"]
    assert d["parameters"]["required"] == ["eszkoz"]


def test_hivas_sugo_es_rossz_parameter():
    assert _run(k.hiv("medical", "kereses", {"query": "ibuprofen"})) == "találat: ibuprofen (5)"
    sugo = json.loads(_run(k.hiv("medical", "sugo", {"nev": "kereses"})))
    assert sugo["sema"]["required"] == ["query"] and "Hosszabb magyarázat" in sugo["leiras"]
    attekintes = json.loads(_run(k.hiv("medical", "sugo", {})))
    assert attekintes["utmutato"] == "Ez a teszt-szerver útmutatója." and "kereses" in attekintes["eszkozok"]
    rossz = json.loads(_run(k.hiv("medical", "kereses", {"rossz": 1})))
    assert "query" in rossz["hiba"] and rossz["sema"]["required"] == ["query"]   # a séma visszamegy
    nincs = json.loads(_run(k.hiv("medical", "nincs_ilyen", {})))
    assert nincs["eszkozok"] == ["hosszu", "kereses"]


def test_hosszu_valasz_levagva():
    v = _run(k.hiv("medical", "hosszu", {}))
    assert len(v) < k.MAX_EREDMENY_KAR + 100 and "levágva" in v


def test_kulcs_nelkuli_szerver_hivasa_kimondott_hiba():
    v = json.loads(_run(k.hiv("lexonomics", "kereses", {"query": "x"})))
    assert "LEXONOMICS_MCP_KEY" in v["hiba"]


def test_elerhetetlen_szerver_kimarad_es_nem_lassit(monkeypatch):
    hivasok = []

    def rossz_kliens(nev):
        hivasok.append(nev)
        return Client("http://127.0.0.1:9/mcp")                   # senki nem figyel ott

    monkeypatch.setattr(k, "_kliens", rossz_kliens)
    assert _run(k.eszkoz_definiciok()) == []
    assert _run(k.eszkoz_definiciok()) == []
    assert hivasok == ["medical", "echolot", "makronom"]           # a hibát LISTA_HIBA_TTL_S-ig megjegyzi
    assert "nem érhető el" in json.loads(_run(k.hiv("medical", "kereses", {"query": "x"})))["hiba"]


def test_a_feldwebel_a_kulso_eszkozt_a_kulso_mcp_nek_adja(monkeypatch):
    from feldwebel import responder
    kapott = {}

    async def hamis_hiv(nev, eszkoz, parameterek):
        kapott.update(nev=nev, eszkoz=eszkoz, parameterek=parameterek)
        return "ok"

    monkeypatch.setattr(k, "hiv", hamis_hiv)
    assert _run(responder._execute_tool("studio", {"eszkoz": "convert", "parameterek": {"a": 1}}, None)) == "ok"
    assert kapott == {"nev": "studio", "eszkoz": "convert", "parameterek": {"a": 1}}
    ures = _run(responder._kulso_eszkozok())
    assert [d["function"]["name"] for d in ures] == ["medical", "echolot", "makronom"]


def test_nincs_nevutkozes_a_feldwebel_eszkozeivel():
    from feldwebel import responder
    sajat = {t["function"]["name"] for t in responder.TOOLS}
    assert not sajat & set(k.SZERVEREK)


def test_sok_eszkoznel_tomor_lista(monkeypatch):
    monkeypatch.setattr(k, "MAX_LEIRAS_KAR", 10)
    d = _run(k.eszkoz_definiciok())[0]["function"]["description"]
    assert "kereses(query, …), hosszu()" in d and "Keresés a teszt" not in d


def test_tiltott_eszkoz_se_a_listaban_se_hivhato(monkeypatch):
    monkeypatch.setitem(k.SZERVEREK["echolot"], "tiltott", {"hosszu"})
    d = [x for x in _run(k.eszkoz_definiciok()) if x["function"]["name"] == "echolot"][0]["function"]
    assert "hosszu" not in d["parameters"]["properties"]["eszkoz"]["enum"]
    assert "nincs ilyen eszköz" in json.loads(_run(k.hiv("echolot", "hosszu", {})))["hiba"]


def test_telegram_darabolas():
    import server
    rovid = "Szia."
    assert server.telegram_darabol(rovid) == [rovid]
    hosszu = "\n\n".join(f"<b>{i}. bekezdés</b> " + "szó " * 300 for i in range(6))
    d = server.telegram_darabol(hosszu)
    assert len(d) > 1 and all(len(x) <= server.TELEGRAM_DARAB for x in d)
    assert all(x.count("<b>") == x.count("</b>") for x in d)          # bekezdéshatáron vág, nem címke közepén
    assert "".join(x.replace(" ", "").replace("\n", "") for x in d) == hosszu.replace(" ", "").replace("\n", "")
    egy_tomb = "x" * 9000
    assert [len(x) for x in server.telegram_darabol(egy_tomb)] == [3900, 3900, 1200]


def test_tobblepeses_feladat_sonnetet_kap():
    import llm_kapu
    p = {"model": "deepseek-ai/DeepSeek-V4-Pro", "tools": [{"type": "function"}],
         "messages": [{"role": "user", "content": "és a MOL?"}]}
    assert llm_kapu.szint_valaszt(p, "feldwebel/telegram")[0] == "olcso"
    assert llm_kapu.szint_valaszt(p, "feldwebel/melyebb/telegram")[0] == "kozep"
