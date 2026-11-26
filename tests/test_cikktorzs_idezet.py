"""A 11-26-i reggeli eset — hálózat nélkül.

A Kommandant a reggeli stratégiai nyitóra (Opus) egy index.hu-cikket küldött (a GKI-felmérés). Három hiba:
  1. A Haiku `web_scrape`-je a brave-markdown első 6000 karakterét kapta (menü, megosztógombok); a cikk a 9192.
     karakternél kezdődött → „a cikk törzse nem jött be”. Most a hosszú lapnál a renderelt HTML cikktörzse megy.
  2. A link miatt az üzenet „web_melyebb” lett → műveleti kérés → nem stratégiai folytatás → Haiku az Opus helyett.
  3. Az „Ez érdekel” a Telegram Válasz gombjával ment; a Bridge csak a két szót kapta, az idézetet nem.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import server
from feldwebel import eszkoz_ut

CIKK_MONDAT = "Novemberben az üzleti és a fogyasztói bizalom egyaránt enyhén, de romlott"


def _cikk_html() -> str:
    menu = "".join(f'<li><a href="https://index.hu/rovat{i}/">Rovat {i}</a></li>' for i in range(300))
    bekezdesek = "".join(
        f"<p>{CIKK_MONDAT}, a GKI konjunktúraindexe egy ponttal csökkent ({i}). A vállalkozások foglalkoztatási "
        f"hajlandósága jelentősebben, áremelési szándéka kisebb mértékben mérséklődött.</p>" for i in range(12))
    return (f"<html><head><title>GKI: a cégek leépítésekre készülnek</title></head><body>"
            f"<nav><ul>{menu}</ul></nav><article><h1>GKI: a cégek leépítésekre készülnek</h1>{bekezdesek}"
            f"</article><footer>{menu}</footer></body></html>")


def _menu_elol_markdown() -> str:
    menu = "\n".join(f"-   [Rovat {i}](https://index.hu/rovat{i}/)" for i in range(300))
    return menu + "\n\n" + CIKK_MONDAT + " …"           # a cikk a 6000. karakter UTÁN kezdődik


@pytest.fixture
def brave(monkeypatch):
    hivasok = []

    async def hivas(eszkoz, args, timeout=60.0):
        hivasok.append((eszkoz, dict(args)))
        return {"url": args["url"], "title": "GKI", "markdown": _menu_elol_markdown(), "html": _cikk_html()}
    monkeypatch.setattr(server, "BRAVE_MCP_ENABLED", True)
    monkeypatch.setattr(server, "_brave_mcp_call", hivas)
    return hivasok


# ══ 1. A CIKKTÖRZS ══════════════════════════════════════════════════════════

def test_a_hosszu_lapnal_a_cikktorzs_megy_nem_a_menu(brave):
    md = _menu_elol_markdown()
    assert md.find(CIKK_MONDAT) > 6000                    # a régi út ezt vágta le
    r = asyncio.run(server._brave_mcp_scrape("https://index.hu/gazdasag/x/"))
    assert brave[0][1]["includeHtml"] is True
    assert "html" not in r                                # a HTML nem megy tovább
    assert r["markdown"].find(CIKK_MONDAT) < 300 and "Rovat 17" not in r["markdown"]
    assert r["extractor"] in ("trafilatura", "readability") and r["oldal_hossz"] == len(md)


def test_a_feldwebel_web_scrape_je_a_cikket_adja(brave):
    from feldwebel import responder
    v = json.loads(asyncio.run(responder._execute_tool("web_scrape", {"url": "https://index.hu/gazdasag/x/"}, None)))
    assert CIKK_MONDAT in v["markdown"]


def test_a_rovid_lap_markdownja_marad():
    r = {"url": "https://x.hu", "markdown": "rövid lap " * 50, "html": _cikk_html()}
    out = asyncio.run(server._brave_cikktorzs(dict(r), "https://x.hu"))
    assert out["markdown"] == r["markdown"] and "html" not in out and "extractor" not in out


def test_ha_nincs_cikk_a_markdown_marad(monkeypatch):
    monkeypatch.setattr(server, "extract_article_text", lambda html, url="": ("lecsupaszított lap", "regex"))
    r = {"url": "https://x.hu", "markdown": "x" * 7000, "html": "<html>…</html>"}
    out = asyncio.run(server._brave_cikktorzs(dict(r), "https://x.hu"))
    assert out["markdown"] == "x" * 7000


def test_a_hibas_valasz_atmegy():
    assert asyncio.run(server._brave_cikktorzs(None, "https://x.hu")) is None


# ══ 2. A LINK A STRATÉGIAI BESZÉLGETÉSBEN ═══════════════════════════════════

URL = "https://index.hu/gazdasag/2026/11/26/gki-felmeres-gazdasagi-kilatasok-bizalmi-index-kormany-magyar-peter-beruhazas-inflacio/"


def test_a_nyitora_kuldott_link_strategiai_folytatas():
    assert eszkoz_ut.strategiai_szandek(URL, elozo_strategiai=True) == "folytatás"
    assert eszkoz_ut.strategiai_szandek(f"ez ide tartozik: {URL}", elozo_strategiai=True) == "folytatás"


def test_a_link_mellett_a_muvelet_muvelet_marad():
    assert eszkoz_ut.strategiai_szandek(f"írj levelet Lacinak erről: {URL}", elozo_strategiai=True) is None
    assert eszkoz_ut.strategiai_szandek(URL) is None      # előzmény nélkül a link nem stratégiai


def test_link_nelkul_az_oldalfigyeles_muvelet():
    assert eszkoz_ut.strategiai_szandek("figyeld az oldalt, ha változik", elozo_strategiai=True) is None


# ══ 3. A TELEGRAM-VÁLASZ IDÉZETE ════════════════════════════════════════════

def test_a_valasz_gomb_idezete_atmegy():
    msg = {"text": "Ez érdekel", "reply_to_message": {"text": "1. Gazdaság — Hitelminősítői ítélet"}}
    s = server._valasz_idezettel(msg, "Ez érdekel")
    assert s.startswith("Ez érdekel") and "«1. Gazdaság — Hitelminősítői ítélet»" in s


def test_a_kijelolt_resz_az_idezet_es_plafonos():
    msg = {"text": "ez", "quote": {"text": "Matolcsy feltételhez köti"}, "reply_to_message": {"text": "hosszú " * 999}}
    assert "«Matolcsy feltételhez köti»" in server._valasz_idezettel(msg, "ez")
    hosszu = server._valasz_idezettel({"reply_to_message": {"caption": "k" * 2000}}, "ez")
    assert len(hosszu) < server.IDEZET_MAX_KAR + 60 and hosszu.endswith("…»)")


@pytest.mark.parametrize("msg, text", [
    ({"text": "sima üzenet"}, "sima üzenet"),
    ({"text": "/faber javítsd", "reply_to_message": {"text": "x"}}, "/faber javítsd"),   # a parancs érintetlen
    ({"reply_to_message": {"photo": [{}]}}, "ez"),                                    # szöveg nélküli előzmény
])
def test_idezet_nelkul_a_szoveg_valtozatlan(msg, text):
    assert server._valasz_idezettel(msg, text) == text
