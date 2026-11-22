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
    yield
    k._LISTAK.clear()
    k._HIBAK.clear()


def test_kulcs_nelkul_csak_a_nyilvanos_szerver_jelenik_meg(monkeypatch):
    nevek = [d["function"]["name"] for d in _run(k.eszkoz_definiciok())]
    assert nevek == ["medical"]
    monkeypatch.setenv("LEXONOMICS_MCP_KEY", "nxk_teszt")
    monkeypatch.setenv("STUDIO_MCP_KEY", "studio_teszt")
    nevek = [d["function"]["name"] for d in _run(k.eszkoz_definiciok())]
    assert nevek == ["lexonomics", "medical", "studio"]          # állandó sorrend (prompt-cache)


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
    assert hivasok == ["medical"]                                  # a hibát LISTA_HIBA_TTL_S-ig megjegyzi
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
    assert [d["function"]["name"] for d in ures] == ["medical"]


def test_nincs_nevutkozes_a_feldwebel_eszkozeivel():
    from feldwebel import responder
    sajat = {t["function"]["name"] for t in responder.TOOLS}
    assert not sajat & set(k.SZERVEREK)


def test_sok_eszkoznel_tomor_lista(monkeypatch):
    monkeypatch.setattr(k, "MAX_LEIRAS_KAR", 10)
    d = _run(k.eszkoz_definiciok())[0]["function"]["description"]
    assert "kereses(query, …), hosszu()" in d and "Keresés a teszt" not in d
