"""API-KAPU (api_kapu.py) — a régi nyitott REST-utak zárása.

1. Belépés és kulcs nélkül minden védett `/api/*` 401, a böngészős lapnyitás a belépésre visz (`?vissza=`).
2. A szándékosan nyilvános utak (healthcheck, belépés, DELPHOI, hírek…) változatlanul nyitva.
3. A belépett süti és a munkáskulcs (X-Bridge-Key / Bearer, DELPHOI_BRIDGE_KEY) átenged.
4. A süti használat közben megújul (csúszó munkamenet), de a friss sütit nem írja újra, és kilépéskor nem újít.
5. Egy új, ismeretlen `/api/…` út alapból zárt.
"""
from __future__ import annotations

import hashlib
import time

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import api_kapu
import ui_kabin as ui

TOKEN = "123456:TESZT-BOT-TOKEN"
K = ui.kulcs(TOKEN)
MUNKAS = hashlib.sha256(("munkas:" + TOKEN).encode()).hexdigest()


async def _ok(request):
    return JSONResponse({"ok": True, "ut": request.url.path})


async def _kilepes(request):
    r = JSONResponse({"ok": True})
    r.delete_cookie(ui.SUTI_NEV, path="/")
    return r


UTAK = ["/", "/dashboard", "/api/send_email", "/api/messages", "/api/memory", "/api/uploads",
        "/api/uploads/7/download", "/api/status", "/api/ai_tasks", "/api/vadonatuj_vegpont",
        "/api/news", "/api/press_snapshots", "/api/poll_results", "/api/delphoi/nowcast",
        "/api/delphoi/stripe/webhook", "/api/brief/area/hu", "/api/ui/allapot", "/chat/api/send",
        "/saas/me", "/app/", "/mcp"]


@pytest.fixture
def kliens(monkeypatch):
    monkeypatch.setitem(ui._DEPS, "bot_token", lambda: TOKEN)
    monkeypatch.delenv("DELPHOI_BRIDGE_KEY", raising=False)
    monkeypatch.delenv("BRIDGE_MUNKAS_KULCSOK", raising=False)
    app = Starlette(routes=[Route(u, _ok, methods=["GET", "POST"]) for u in UTAK]
                    + [Route("/app/kilepes", _kilepes, methods=["POST"])],
                    middleware=[Middleware(api_kapu.ApiKapuMiddleware)])
    return TestClient(app, base_url="https://testserver")


VEDETT = ["/dashboard", "/api/send_email", "/api/messages", "/api/memory", "/api/uploads",
          "/api/uploads/7/download", "/api/status", "/api/ai_tasks", "/api/vadonatuj_vegpont"]
NYITOTT = ["/", "/api/news", "/api/press_snapshots", "/api/poll_results", "/api/delphoi/nowcast",
           "/api/delphoi/stripe/webhook", "/api/brief/area/hu", "/api/ui/allapot", "/chat/api/send",
           "/saas/me", "/app/", "/mcp"]


@pytest.mark.parametrize("ut", VEDETT)
def test_kulcs_nelkul_zart(kliens, ut):
    r = kliens.post(ut, json={"sender": "x", "message": "@kimi szia"})
    assert r.status_code == 401 and r.json()["kod"] == "nincs_munkamenet"
    r = kliens.get(ut)
    assert r.status_code == 401


@pytest.mark.parametrize("ut", NYITOTT)
def test_nyilvanos_utak_nyitva(kliens, ut):
    assert kliens.get(ut).status_code == 200


def test_bongeszos_lapnyitas_a_belepesre_visz_es_vissza(kliens):
    r = kliens.get("/api/uploads/7/download?x=1", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/app/?vissza=%2Fapi%2Fuploads%2F7%2Fdownload%3Fx%3D1"
    r = kliens.get("/dashboard", headers={"accept": "text/html,application/xhtml+xml"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/app/?vissza=%2Fdashboard"


def test_belepett_suti_atenged(kliens):
    kliens.cookies.set(ui.SUTI_NEV, ui.suti_ertek(K))
    for ut in VEDETT:
        assert kliens.get(ut).status_code == 200, ut


def test_hamis_vagy_lejart_suti_nem_enged(kliens):
    kliens.cookies.set(ui.SUTI_NEV, ui.suti_ertek(ui.kulcs("masik:TOKEN")))
    assert kliens.get("/api/messages").status_code == 401
    kliens.cookies.set(ui.SUTI_NEV, ui.suti_ertek(K, most=time.time() - ui.SUTI_ERVENY - 10))
    assert kliens.get("/api/messages").status_code == 401


@pytest.mark.parametrize("fejlec", [{"X-Bridge-Key": MUNKAS}, {"Authorization": f"Bearer {MUNKAS}"}])
def test_munkaskulcs_atenged(kliens, fejlec):
    assert kliens.get("/api/messages", headers=fejlec).status_code == 200
    assert kliens.post("/api/messages", headers=fejlec, json={}).status_code == 200


def test_rossz_kulcs_es_mas_tartomany_kulcsa_nem_enged(kliens):
    assert kliens.get("/api/messages", headers={"X-Bridge-Key": "x" * 64}).status_code == 401
    faber_kulcs = hashlib.sha256(("faber:" + TOKEN).encode()).hexdigest()   # a továbbítás kulcsa ≠ munkáskulcs
    assert kliens.get("/api/messages", headers={"X-Bridge-Key": faber_kulcs}).status_code == 401
    assert kliens.get("/api/messages", headers={"Authorization": f"Basic {MUNKAS}"}).status_code == 401


def test_delphoi_es_kulon_munkaskulcs(kliens, monkeypatch):
    monkeypatch.setenv("DELPHOI_BRIDGE_KEY", "delphoi-kulcs-0123456789")
    monkeypatch.setenv("BRIDGE_MUNKAS_KULCSOK", "rovid, echolot-motor-kulcs-0123456789")
    assert kliens.get("/api/messages", headers={"X-Bridge-Key": "delphoi-kulcs-0123456789"}).status_code == 200
    assert kliens.get("/api/messages", headers={"Authorization": "Bearer echolot-motor-kulcs-0123456789"}).status_code == 200
    assert kliens.get("/api/messages", headers={"X-Bridge-Key": "rovid"}).status_code == 401   # <16 jel: nem kulcs


def test_bot_token_nelkul_munkaskulcs_sincs(kliens, monkeypatch):
    monkeypatch.setitem(ui._DEPS, "bot_token", lambda: "")
    assert kliens.get("/api/messages", headers={"X-Bridge-Key": MUNKAS}).status_code == 401


def test_suti_hasznalat_kozben_megujul(kliens):
    regi = ui.suti_ertek(K, most=time.time() - 3 * 24 * 3600)       # 3 napja kiállítva
    kliens.cookies.set(ui.SUTI_NEV, regi)
    r = kliens.get("/api/status")
    assert r.status_code == 200
    uj = r.cookies.get(ui.SUTI_NEV)
    assert uj and uj != regi and ui.suti_ervenyes(K, uj)
    assert int(uj.split(".")[0]) > int(regi.split(".")[0]) + 2 * 24 * 3600


def test_friss_sutit_nem_irja_ujra_es_kilepeskor_nem_ujit(kliens):
    kliens.cookies.set(ui.SUTI_NEV, ui.suti_ertek(K))
    assert "set-cookie" not in kliens.get("/api/status").headers
    kliens.cookies.set(ui.SUTI_NEV, ui.suti_ertek(K, most=time.time() - 3 * 24 * 3600))
    r = kliens.post("/app/kilepes")
    sutik = r.headers.get_list("set-cookie")
    assert len(sutik) == 1 and ('max-age=0' in sutik[0].lower() or 'expires=' in sutik[0].lower())


def test_vedett_e_tablazat():
    assert api_kapu.vedett_e("/api/send_email") and api_kapu.vedett_e("/dashboard")
    assert api_kapu.vedett_e("/api/vadonatuj")             # új végpont alapból zárt
    assert not api_kapu.vedett_e("/api/news/")
    assert not api_kapu.vedett_e("/")
    assert not api_kapu.vedett_e("/apixyz")


def test_a_szerver_a_kaput_hasznalja():
    """A server.py indításában a kapu a middleware-lánc része (ne maradjon ki egy átírásnál)."""
    forras = open("server.py", encoding="utf-8").read()
    assert "Middleware(ApiKapuMiddleware)" in forras


def test_mcp_ajto_figyel_majd_zar_anthropic_cimrol_enged(monkeypatch, kliens, caplog):
    import logging
    from starlette.applications import Starlette
    from starlette.routing import Route
    app = Starlette(routes=[Route("/mcp", _ok, methods=["GET", "POST", "DELETE"]),
                            Route("/mcp/wc-titok", _ok, methods=["POST"])],
                    middleware=[Middleware(api_kapu.ApiKapuMiddleware)])
    c = TestClient(app, base_url="https://testserver")
    anthropic = {"x-real-ip": "160.79.105.17", "x-forwarded-for": "160.79.105.17"}
    idegen = {"x-real-ip": "5.6.7.8", "x-forwarded-for": "5.6.7.8"}
    # alap: FIGYEL — nem zár, de naplóz (az élesítés előtti mérés)
    monkeypatch.delenv("BRIDGE_MCP_ZARVA", raising=False)
    monkeypatch.delenv("BRIDGE_KLIENS_IP_FORRAS", raising=False)
    with caplog.at_level(logging.INFO, logger="api_kapu"):
        assert c.post("/mcp", json={}, headers=anthropic).status_code == 200
    assert "x-real-ip=160.79.105.17(A)" in caplog.text
    # ZÁR: a megbízható fejléc kijelölése nélkül senki (a hamisítható fejlécnek nem hiszünk)
    monkeypatch.setenv("BRIDGE_MCP_ZARVA", "1")
    assert c.post("/mcp", json={}, headers=anthropic).status_code == 401
    monkeypatch.setenv("BRIDGE_KLIENS_IP_FORRAS", "x-real-ip")
    assert c.post("/mcp", json={}, headers=anthropic).status_code == 200      # a claude.ai-csatlakozó
    assert c.post("/mcp", json={}, headers={"x-real-ip": "153.61.215.1"}).status_code == 200
    for m in ("GET", "POST", "DELETE"):
        assert c.request(m, "/mcp", headers=idegen).status_code == 401       # mindenki más
    assert c.post("/mcp", json={}, headers={**idegen, "X-Bridge-Key": MUNKAS}).status_code == 200
    assert c.post("/mcp/wc-titok", json={}, headers=idegen).status_code == 200   # a példány-utat a YRScope ellenőrzi
    # KIFEJEZETTEN NYITVA
    monkeypatch.setenv("BRIDGE_MCP_ZARVA", "0")
    assert c.post("/mcp", json={}, headers=idegen).status_code == 200


def test_anthropic_cimtartomany():
    assert api_kapu.anthropic_cim("160.79.104.1") and api_kapu.anthropic_cim("160.79.111.254")
    assert api_kapu.anthropic_cim("153.61.208.5") and api_kapu.anthropic_cim("153.61.223.250")
    assert not api_kapu.anthropic_cim("160.79.112.1") and not api_kapu.anthropic_cim("1.2.3.4")
    assert not api_kapu.anthropic_cim("") and not api_kapu.anthropic_cim("nem-ip")


def test_kulso_eszkoz_csak_zart_ajtonal_es_sajat_peldanynak(monkeypatch):
    import asyncio
    import json as _json
    import server
    import kulso_mcp

    async def hamis_hiv(nev, eszkoz, parameterek):
        return f"ok:{nev}.{eszkoz}"
    monkeypatch.setattr(kulso_mcp, "hiv", hamis_hiv)
    fn = getattr(server.kulso_eszkoz, "fn", server.kulso_eszkoz)
    monkeypatch.delenv("BRIDGE_MCP_ZARVA", raising=False)
    assert "zárása után" in _json.loads(asyncio.run(fn("lexonomics", "search", {"q": "x"}, "web-claus")))["hiba"]
    monkeypatch.setenv("BRIDGE_MCP_ZARVA", "1")
    assert asyncio.run(fn("lexonomics", "search", {"q": "x"}, "")) == "ok:lexonomics.search"   # webes Claude, caller nélkül
    assert "saját példány" in _json.loads(asyncio.run(fn("lexonomics", "search", {}, "idegen")))["hiba"]
    assert asyncio.run(fn("lexonomics", "search", {"q": "x"}, "web-claus")) == "ok:lexonomics.search"
    assert "ismeretlen szerver" in _json.loads(asyncio.run(fn("skynet", "x", {}, "web-claus")))["hiba"]
