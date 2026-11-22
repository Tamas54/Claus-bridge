"""Egy hálózati botlás nem TARTÓS hiba — Faber #3, 2026-11-22.

A hamis lelet: „TARTÓS: a(z) `echolot` MOST IS hibás — 1/1 próba bukott. Első ok: transport: ConnectTimeout:
_ssl.c:1018: The handshake operation timed out”. Az Echolot közben élt (máshonnan /health 200, kézfogás 0,09 s).
A termék (`_echolot_client._get`) a hálózati hibát egyszer újrapróbálja, a próba nem — így egyetlen botlásból
Faber-kérelem lett.

Amit ezek a tesztek őriznek:
  1. egy botlás után sikerülő kérés ZÖLD, de a botlás látszik a részletben;
  2. ha minden kísérlet bukik, a próba piros, a diagnózis TARTÓS, és a lelet megmondja, hol akadt el
     (TLS-kézfogás vagy már a TCP-kapcsolat) — ez a következő vizsgálónak a fél diagnózis;
  3. a HTTP-hibát (pl. 503) és a rossz URL-t NEM ismétli: az válasz, illetve újrapróbálva sem javul;
  4. a SearXNG- és a brave-mcp-próba ugyanígy viseli a botlást.
Hálózat nincs: a `httpx.request` helyén kitalált válaszok és hibák.
"""
from __future__ import annotations

import httpx
import pytest

import onjavitas
import selfdiag
import server

KEZFOGAS = "_ssl.c:1018: The handshake operation timed out"


@pytest.fixture(autouse=True)
def _gyors(monkeypatch):
    monkeypatch.setattr(server, "_PROBA_SZUNET_S", 0)
    monkeypatch.setattr(selfdiag, "_PROBES", {})
    monkeypatch.setattr(selfdiag, "_KNOWN_COMPONENTS", set())


def _forgatokonyv(monkeypatch, *lepesek):
    """A `httpx.request` helyére: sorban a megadott kivételt dobja vagy választ adja. → a hívások listája."""
    hivasok = []

    def _kamu(method, url, **kw):
        hivasok.append((method, url))
        lepes = lepesek[len(hivasok) - 1]
        if isinstance(lepes, Exception):
            raise lepes
        return lepes

    monkeypatch.setattr(httpx, "request", _kamu)
    return hivasok


def _valasz(status=200, **kw):
    return httpx.Response(status, request=httpx.Request("GET", "https://kamu.test/"), **kw)


def _echolot(monkeypatch, *lepesek):
    hivasok = _forgatokonyv(monkeypatch, *lepesek)
    selfdiag.register_probe("echolot", "api", server._probe_http_get("api", "https://echolot.kamu/health"))
    return hivasok


def test_egy_kezfogas_botlas_utan_zold_es_a_botlas_latszik(monkeypatch):
    hivasok = _echolot(monkeypatch, httpx.ConnectTimeout(KEZFOGAS), _valasz(200, text="ok"))
    d = selfdiag.diagnose("echolot").as_dict()
    assert len(hivasok) == 2
    assert d["verdict"] == "healthy", d
    reszlet = d["evidence"][0]["detail"]
    assert reszlet.startswith("HTTP 200") and "2. kísérletre" in reszlet and "TLS-kézfogás" in reszlet
    # Az önjavító kör erre nem ír Faber-kérelmet.
    assert d["verdict"] not in ("persistent", "degraded")


def test_ha_minden_kiserlet_bukik_tartos_es_megmondja_hol_akadt_el(monkeypatch):
    hivasok = _echolot(monkeypatch, httpx.ConnectTimeout(KEZFOGAS), httpx.ConnectTimeout(KEZFOGAS))
    d = selfdiag.diagnose("echolot").as_dict()
    assert len(hivasok) == 2
    assert d["verdict"] == "persistent"
    reszlet = d["evidence"][0]["detail"]
    assert reszlet.startswith("transport: 2/2 kísérlet bukott")
    assert "a TLS-kézfogás akadt el, a TCP felépült" in reszlet
    tipus, kivansag = onjavitas.osztalyoz("echolot", d)
    assert tipus == "kod"
    assert "TLS-kézfogás" in kivansag, "a hely (kézfogás vs. TCP) a Faber-kérelembe is eljusson"


def test_a_tcp_kapcsolat_ideje_kifut_mas_hely(monkeypatch):
    """A 10-18-i GCP-kimenő IP-tiltás így nézett ki: a SYN el sem jutott a szerverig."""
    _echolot(monkeypatch, httpx.ConnectTimeout("timed out"), httpx.ConnectTimeout("timed out"))
    reszlet = selfdiag.diagnose("echolot").as_dict()["evidence"][0]["detail"]
    assert "a TCP-kapcsolat sem épült fel" in reszlet and "TLS" not in reszlet


def test_http_hibat_nem_ismetel(monkeypatch):
    hivasok = _echolot(monkeypatch, _valasz(503, text="down"))
    d = selfdiag.diagnose("echolot").as_dict()
    assert len(hivasok) == 1
    assert d["verdict"] == "persistent" and d["evidence"][0]["detail"] == "HTTP 503"


def test_rossz_urlt_nem_ismetel(monkeypatch):
    """Nem hálózati hiba (a 08-30-i sortörés az env-ben): újrapróbálva sem javul, egy kísérlet elég."""
    hivasok = _echolot(monkeypatch, httpx.InvalidURL("non-printable ASCII character '\\n'"))
    r = selfdiag._PROBES["echolot"]["api"]()
    assert len(hivasok) == 1
    assert r.ok is False and "InvalidURL" in r.detail and "1/2" in r.detail


def test_url_nelkul_nem_hiba_hanem_nincs_beallitva(monkeypatch):
    hivasok = _forgatokonyv(monkeypatch)
    r = server._probe_http_get("api", "")()
    assert r.ok is None and not hivasok


def test_searxng_proba_is_kibirja_a_botlast(monkeypatch):
    monkeypatch.setattr(server, "SEARXNG_URL", "https://searx.kamu")
    hivasok = _forgatokonyv(monkeypatch, httpx.ReadTimeout("read timed out"),
                            _valasz(200, json={"results": [{"url": "https://pelda.test"}]}))
    r = server._probe_searxng()
    assert len(hivasok) == 2
    assert r.ok is True and r.detail.startswith("HTTP 200, 1 találat") and "ReadTimeout" in r.detail


def test_brave_mcp_proba_is_kibirja_a_botlast(monkeypatch):
    monkeypatch.setattr(server, "BRAVE_MCP_ENABLED", True)
    monkeypatch.setenv("BRAVE_MCP_URL", "https://brave.kamu/mcp")
    hivasok = _forgatokonyv(monkeypatch, httpx.ConnectError("Connection reset by peer"),
                            _valasz(200, text='{"result": {"serverInfo": {"name": "brave"}}}'))
    r = server._probe_brave_mcp()
    assert [m for m, _ in hivasok] == ["POST", "POST"]
    assert r.ok is True and r.detail.startswith("HTTP 200") and "ConnectError" in r.detail


def test_az_echolot_proba_a_kozos_kerovel_megy():
    """Ha valaki visszaírja egylövetűre, ez a teszt szól."""
    forras = open("server.py", encoding="utf-8").read()
    assert 'r, botlasok = _proba_keres("GET", url, timeout=timeout, follow_redirects=True)' in forras
    assert '_probe_http_get("api", f"{_echolot_base}/health" if _echolot_base else "")' in forras
