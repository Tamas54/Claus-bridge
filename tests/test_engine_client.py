# -*- coding: utf-8 -*-
"""Claus-Bridge → Echolot Engine kliens (engine_client.py) + az `echolot_engine` tool.

    .venv/bin/python -m pytest -q tests/test_engine_client.py

Hálózat NINCS: a httpx-hívást monkeypatcheljük.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def ec(monkeypatch):
    monkeypatch.setenv("ECHOLOT_ENGINE_URL", "https://motor.example/mcp")
    monkeypatch.setenv("ECHOLOT_ENGINE_KEY", "eng_teszt_kulcs_0123456789")
    import importlib
    import engine_client as m
    importlib.reload(m)
    return m


def test_url_normalizalas_es_enabled(ec):
    # a .env az /mcp-végpontot tartja — a REST a gyökéren van
    assert ec.ENGINE_URL == "https://motor.example"
    assert ec.enabled() is True


def test_kotelezo_mezo_es_ismeretlen_action(ec, monkeypatch):
    import asyncio
    r = asyncio.run(ec.hivas("scrape", {}))
    assert r["error"] == "missing_field" and "url" in r["detail"]
    r = asyncio.run(ec.hivas("nincs_ilyen", {"url": "https://x.example"}))
    assert r["error"] == "unknown_action"


def test_hivas_utvonal_es_vagas(ec, monkeypatch):
    import asyncio
    hivasok = {}

    class Valasz:
        status_code = 200

        def json(self):
            return {"success": True, "data": {"markdown": "x" * 5000,
                                              "metadata": {"statusCode": 200,
                                                           "sourceURL": "https://x.example"}}}

    class Kliens:
        def __init__(self, **kw):
            hivasok["init"] = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, metodus, url, headers=None, json=None):
            hivasok.update(metodus=metodus, url=url, headers=headers, torzs=json)
            return Valasz()

    monkeypatch.setattr(ec.httpx, "AsyncClient", Kliens)
    r = asyncio.run(ec.hivas("scrape", {"url": "https://x.example"}, max_chars=100))
    assert hivasok["url"] == "https://motor.example/v2/scrape"
    assert hivasok["headers"]["Authorization"].startswith("Bearer eng_")
    assert len(r["data"]["markdown"]) < 200 and "+4900 jel" in r["data"]["markdown"]
    assert "✅ scrape" in ec.osszefoglal("scrape", r)


def test_status_ut(ec, monkeypatch):
    import asyncio
    latott = {}

    class Valasz:
        status_code = 200

        def json(self):
            return {"success": True, "status": "completed"}

    class Kliens:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, metodus, url, headers=None, json=None):
            latott["url"] = url
            return Valasz()

    monkeypatch.setattr(ec.httpx, "AsyncClient", Kliens)
    asyncio.run(ec.hivas("status", {}, kind="crawl", job_id="abc123"))
    assert latott["url"].endswith("/v2/crawl/abc123")


def test_hiba_maszkolja_a_kulcsot(ec, monkeypatch):
    import asyncio

    class Kliens:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **kw):
            raise RuntimeError("csatlakozas eng_teszt_kulcs_0123456789 mellett bukott")

    monkeypatch.setattr(ec.httpx, "AsyncClient", Kliens)
    r = asyncio.run(ec.hivas("scrape", {"url": "https://x.example"}))
    assert "eng_teszt" not in json.dumps(r) and "***" in r["detail"]


def test_nincs_konfiguralva(monkeypatch):
    monkeypatch.delenv("ECHOLOT_ENGINE_URL", raising=False)
    monkeypatch.delenv("ECHOLOT_ENGINE_KEY", raising=False)
    import importlib
    import engine_client as m
    importlib.reload(m)
    import asyncio
    r = asyncio.run(m.hivas("scrape", {"url": "https://x.example"}))
    assert r["error"] == "engine_not_configured"


def test_osszefoglal_formatumonkent_szamol(ec):
    """2026-09-23: JSON-os scrape fejléce „0 jel"-et írt hiánytalan adat mellett."""
    adat = {"data": {"json": {"cim": "x", "ar": 12}, "links": ["a", "b"],
                     "metadata": {"sourceURL": "https://p.example", "statusCode": 200}}}
    s = ec.osszefoglal("scrape", adat)
    assert "0 jel" not in s and "json" in s and "links 2 db" in s and s.startswith("✅")


def test_osszefoglal_blokk_hangos(ec):
    adat = {"data": {"markdown": "Ihre Anfrage wurde blockiert.",
                     "metadata": {"sourceURL": "https://z.example", "statusCode": 200,
                                  "blockReason": "interstitial:tdm_reservation"}}}
    s = ec.osszefoglal("scrape", adat)
    assert s.startswith("⛔") and "tdm_reservation" in s and "NEM a kért tartalom" in s


def test_osszefoglal_a_vagas_elotti_hosszt_irja(ec):
    adat = ec._vag({"data": {"markdown": "x" * 5000, "metadata": {}}}, 400)
    assert "markdown 5000 jel" in ec.osszefoglal("scrape", adat)
