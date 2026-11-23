"""A FETCH-LÁNC (lekeres.py) és a KIVONAT-TÁR (kivonat.py) 11-23-as javításai — hálózat nélkül (httpx.MockTransport és
kicserélt fokok).

A 28-as feladat-ügynök („Pótor József-kutatás”) naplójából:
  1. `portal-admin.neb.hu` PDF: `SSL CERTIFICATE_VERIFY_FAILED` a Tier1 GET-en → most a lánc következő foka jön
     (az Echolot Engine `scrape`-je, `skipTlsVerification`-nel, KIMONDVA), nem áll meg;
  2. YouTube-oldal: 205 karakter → most a LEIRAT (az Echolot `youtube_transcript`-je), oldalletöltés nélkül;
  3. óriás PDF (27 M karakter): a fejléc szerinti méretplafon, és csak az első N oldal / N karakter;
  4. rövid lap (624 karakter, fal): a következő fok; a gazdagabb nyer;
  5. a kivonat a kontextusba plafonnal (~1500 token), forrás-URL-lel;
  6. ugyanaz az URL egy futásban másodszor nem tölt le, és nem ír újra tartalmat (`kerdes`-re új kivonat, letöltés
     nélkül).
"""
from __future__ import annotations

import asyncio
import json
import ssl

import httpx
import pytest

import kivonat
import lekeres


def _run(c):
    return asyncio.run(c)


@pytest.fixture
def fokok(monkeypatch):
    """A Tier2–4 fokok feljegyezve; alapból egyik sem ad semmit."""
    hivott = []

    def _fok(nev, valasz=None):
        async def f(url, **kw):
            hivott.append((nev, url, kw))
            return valasz
        return f
    monkeypatch.setattr(lekeres, "_lightpanda", _fok("lightpanda"))
    monkeypatch.setattr(lekeres, "_brave", _fok("brave"))
    monkeypatch.setattr(lekeres, "_engine", _fok("engine"))
    return hivott, _fok


def _transport(monkeypatch, handler):
    monkeypatch.setattr(lekeres, "_kliens", lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=timeout, follow_redirects=True))


# ── 1. SSL-hiba → a következő fok ─────────────────────────────────────────

def test_ssl_hiba_a_kovetkezo_fokra_megy_kimondva(monkeypatch, fokok):
    hivott, _fok = fokok

    def handler(req):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local "
                                 "issuer certificate (_ssl.c:1018)", request=req)
    _transport(monkeypatch, handler)
    monkeypatch.setattr(lekeres, "_engine", _fok("engine", "Pótor József (1931–2019) cserkészvezető. " * 40))
    url = "https://portal-admin.neb.hu/uploads/beszamolo_2019.pdf"
    r = _run(lekeres.oldal(url))
    assert r["via"] == "echolot-engine" and "Pótor József" in r["content"]
    assert r["tls"] == "nem ellenőrzött" and "TLS-ellenőrzés nélkül" in r["megjegyzes"]
    assert "TLS-tanúsítványa nem ellenőrizhető" in r["megjegyzes"]
    # PDF-URL: a böngészős fokok (PDF-et nem renderelnek) kimaradnak, az Engine TLS-kihagyással kap
    assert [h[0] for h in hivott] == ["engine"] and hivott[0][2] == {"tls_kihagy": True}


def test_ssl_hiba_felismeres_es_az_engine_kerese(monkeypatch):
    belso = ssl.SSLCertVerificationError(1, "certificate verify failed")
    kulso = httpx.ConnectError("hiba")
    kulso.__cause__ = belso
    assert lekeres.tls_hiba(kulso) and not lekeres.tls_hiba(httpx.ConnectError("connection refused"))
    import engine_client
    kert = []

    async def hivas(action, payload, **kw):
        kert.append((action, payload, kw))
        return {"success": True, "data": {"markdown": "szöveg", "metadata": {"statusCode": 200}}}
    monkeypatch.setattr(engine_client, "enabled", lambda: True)
    monkeypatch.setattr(engine_client, "hivas", hivas)
    assert _run(lekeres._engine("https://x.hu/a.pdf", tls_kihagy=True)) == "szöveg"
    action, p, kw = kert[0]
    assert action == "scrape" and p["formats"] == ["markdown"] and p["skipTlsVerification"] is True   # LLM-formátum NINCS
    assert p["parsers"] == [{"type": "pdf", "maxPages": lekeres.PDF_MAX_OLDAL}] and set(p) == {
        "url", "formats", "onlyMainContent", "timeout", "parsers", "skipTlsVerification"}           # csak az URL megy ki
    _run(lekeres._engine("https://x.hu/b", tls_kihagy=False))
    assert "skipTlsVerification" not in kert[1][1]


def test_html_ssl_hiba_eloszor_a_bongeszo_fok(monkeypatch, fokok):
    hivott, _fok = fokok

    def handler(req):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]", request=req)
    _transport(monkeypatch, handler)
    monkeypatch.setattr(lekeres, "_brave", _fok("brave", "Hosszú cikk szövege, valódi mondatokkal. " * 40))
    r = _run(lekeres.oldal("https://pelda.hu/cikk"))
    assert r["via"] == "brave-mcp" and "tls" not in r                    # a böngésző ellenőrzött TLS-sel olvasott
    assert [h[0] for h in hivott] == ["lightpanda", "brave"]             # a gazdag eredmény után nincs több fok


# ── 2. YouTube → leirat ──────────────────────────────────────────────────

def test_youtube_link_a_leirat_utat_hivja(monkeypatch, fokok):
    hivott, _ = fokok

    def handler(req):
        raise AssertionError("YouTube-oldalt nem töltünk le")
    _transport(monkeypatch, handler)
    import _echolot_client as ec
    kert = []

    async def mcp_call(tool, args, timeout=None):
        kert.append((tool, args))
        return {"transcript": "Üdvözlöm, ma Pótor Józsefről beszélünk. " * 30, "has_more": False, "title": "Emlékest"}
    monkeypatch.setattr(ec, "ECHOLOT_URL", "http://echolot.teszt")
    monkeypatch.setattr(ec, "mcp_call", mcp_call)
    for url in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "https://youtu.be/dQw4w9WgXcQ",
                "https://m.youtube.com/shorts/dQw4w9WgXcQ"):
        assert lekeres.youtube_id(url) == "dQw4w9WgXcQ"
    assert lekeres.youtube_id("https://www.youtube.com/@csatorna") is None
    assert lekeres.youtube_id("https://notyoutube.com/watch?v=dQw4w9WgXcQ") is None
    r = _run(lekeres.oldal("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
    assert r["tipus"] == "youtube_leirat" and r["via"] == "echolot" and "Pótor Józsefről" in r["content"]
    assert kert == [("youtube_transcript", {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                                            "max_chars": lekeres.NAGY_MAX_KAR})]
    assert hivott == [] and "LEIRATA" in r["megjegyzes"] and "Emlékest" in r["megjegyzes"]
    # leirat nélkül kimondott hiba, nem a 205 karakteres oldal
    monkeypatch.setattr(ec, "ECHOLOT_URL", "")
    r = _run(lekeres.oldal("https://youtu.be/dQw4w9WgXcQ"))
    assert "error" in r and "leirat" in r["error"]


def test_web_scrape_youtube_linkre_is_a_leiratot_adja(monkeypatch):
    from feldwebel import responder
    kert = []

    async def oldal(url, **kw):
        kert.append(url)
        return {"url": url, "content": "LEIRAT", "tipus": "youtube_leirat"}
    monkeypatch.setattr(lekeres, "oldal", oldal)
    out = json.loads(_run(responder._execute_tool("web_scrape", {"url": "https://youtu.be/dQw4w9WgXcQ"}, None)))
    assert out["content"] == "LEIRAT" and kert == ["https://youtu.be/dQw4w9WgXcQ"]
    out = json.loads(_run(responder._execute_tool("web_fetch", {"url": "https://pelda.hu"}, None)))
    assert kert[-1] == "https://pelda.hu"                                 # a web_fetch a láncon megy


# ── 3. PDF: méretplafon, első N oldal ───────────────────────────────────

def _pdf(oldalak: int, szoveg: str = "Pótor József a cserkészmozgalomban. ") -> bytes:
    import pymupdf
    doc = pymupdf.open()
    for i in range(oldalak):
        doc.new_page().insert_text((40, 60), f"{i + 1}. oldal: {szoveg}")
    return doc.tobytes()


def test_pdf_fejlec_szerinti_plafon_nem_tolti_le(monkeypatch, fokok):
    olvasott = []

    class Torzs(httpx.AsyncByteStream):
        async def __aiter__(self):
            olvasott.append(1)
            yield b"%PDF-1.7 ..."

    def handler(req):
        return httpx.Response(200, headers={"content-type": "application/pdf",
                                            "content-length": str(lekeres.PDF_MAX_BAJT + 1)}, stream=Torzs())
    _transport(monkeypatch, handler)
    r = _run(lekeres.oldal("https://cserkeszhu-data.example/Romania_1990_2020.pdf"))
    assert "túl nagy" in r["error"] and r["tipus"] == "pdf" and olvasott == []    # a törzsből egy bájt sem jött le
    # fejléc nélkül is: letöltés közben a plafonnál megáll
    monkeypatch.setattr(lekeres, "PDF_MAX_BAJT", 1000)

    class Hosszu(httpx.AsyncByteStream):                       # Content-Length nélkül, darabokban
        async def __aiter__(self):
            for _ in range(10):
                yield b"%PDF-" + b"x" * 500
    _transport(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "application/pdf"},
                                                       stream=Hosszu()))
    r = _run(lekeres.oldal("https://x.hu/nagy.pdf"))
    assert "túl nagy" in r["error"] and "MB+" in r["error"]


def test_pdf_csak_az_elso_n_oldal_es_n_karakter(monkeypatch, fokok):
    raw = _pdf(lekeres.PDF_MAX_OLDAL + 10, "Pótor József a cserkészmozgalomban, Kolozsvár, 1931. " * 2)
    _transport(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "application/octet-stream"},
                                                       content=raw))
    r = _run(lekeres.oldal("https://archiv.example.hu/book/export/50"))          # a bájt-mágia dönt, nem az URL
    assert r["via"] == "http/pdf" and f"{lekeres.PDF_MAX_OLDAL}. oldal" in r["content"]
    assert f"{lekeres.PDF_MAX_OLDAL + 1}. oldal" not in r["content"]
    assert f"PDF: {lekeres.PDF_MAX_OLDAL + 10} oldal, ebből az első {lekeres.PDF_MAX_OLDAL}" in r["megjegyzes"]
    szoveg, n, kesz = lekeres.pdf_szoveg(_pdf(30, "x" * 400), max_kar=2000)
    assert len(szoveg) <= 2000 and n == 30 and kesz < 30
    # a nagy szöveg a hurok kivonatolójához megy (a kontextusba csak a kivonat)
    assert len(json.dumps(r, ensure_ascii=False)) > kivonat.KUSZOB_KAR


# ── 4. rövid lap / fal → a következő fok, a gazdagabb nyer ─────────────

def test_rovid_lap_a_kovetkezo_fokot_hivja_a_gazdagabb_nyer(monkeypatch, fokok):
    hivott, _fok = fokok
    html = "<html><body><p>Előfizetőknek. Lépj be a teljes cikkért.</p></body></html>"
    _transport(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                                                       text=html))
    monkeypatch.setattr(lekeres, "_lightpanda", _fok("lightpanda", "Rövid."))
    r = _run(lekeres.oldal("https://www.portfolio.hu/gazdasag/20260626/x"))
    assert [h[0] for h in hivott] == ["lightpanda", "brave", "engine"]
    assert r["via"].startswith("http") and "rövid tartalom" in r["megjegyzes"]   # a Lightpanda szegényebb volt


# ── 5. kivonat-plafon, forrás-URL ──────────────────────────────────────

def test_kivonat_plafon_es_forras():
    async def bobeszedu(payload, *, purpose=""):
        assert "500 szóban" in payload["messages"][0]["content"] and "Az első sor: a forrás" in \
            payload["messages"][0]["content"]
        return {"model": "claude-haiku-5-5", "choices": [{"message": {"content": "Tény. " * 3000}}]}, None, []
    tar = kivonat.EredmenyTar(olvaso=bobeszedu)
    ki = _run(tar.feldolgoz("web_fetch", {"url": "https://Archiv.Cserkesz.hu/book/export/html/50.html#x"},
                            "Nagy oldal. " * 10_000, feladat="Pótor József életrajza"))
    fej, test = ki.split("\n", 1)
    assert "Forrás: https://archiv.cserkesz.hu/book/export/html/50.html." in fej
    assert len(test) <= kivonat.KIVONAT_MAX_KAR + 80 and f"plafon {kivonat.KIVONAT_MAX_KAR}" in test
    assert kivonat.KIVONAT_MAX_KAR / 3 <= 1_600                                    # ~1500 token oldalanként


# ── 6. ismételt letöltés ────────────────────────────────────────────────

def test_masodik_letoltes_nem_ir_ujra_tartalmat_kerdesre_uj_kivonat():
    import nachtschicht as N
    olvasasok = []

    async def olvaso(payload, *, purpose=""):
        olvasasok.append(payload["messages"][0]["content"])
        return {"model": "claude-haiku-5-5", "choices": [{"message": {
            "content": "archiv.cserkesz.hu, 2019\nPótor József 1931-ben született Kolozsváron. Cserkészvezető volt."}}]}, \
            None, []
    tar = kivonat.EredmenyTar(olvaso=olvaso)
    letoltes = []

    async def vegrehajto(nev, args, modell=""):
        letoltes.append((nev, args["url"]))
        return json.dumps({"url": args["url"], "content": "Életrajz. " * 3000})
    url = "https://archiv.cserkesz.hu/book/export/html/50.html"
    hivasok = [[("web_fetch", {"url": url})], [("web_fetch", {"url": url + "/"})],
               [("web_fetch", {"url": url, "kerdes": "a 12. oldal: a díjai"})], [("web_scrape", {"url": url})]]
    valaszok = [{"model": "claude-sonnet-5-5", "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"t{k}{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
        for i, (n, a) in enumerate(h)]}}]} for k, h in enumerate(hivasok)]
    valaszok.append({"model": "claude-sonnet-5-5", "choices": [{"message": {"role": "assistant", "content": "KÉSZ"}}]})
    kuldott = []

    async def chat(payload, purpose=""):
        kuldott.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        return valaszok.pop(0), None, []
    eszkozok = [{"type": "function", "function": {"name": n, "parameters": {"type": "object"}}}
                for n in ("web_fetch", "web_scrape")]
    szoveg, _ = _run(N._hurok([{"role": "system", "content": "FELADAT: Pótor József"}, {"role": "user", "content": "x"}],
                              eszkozok, purpose="ugynok/1/futas", szamla=N.Szamla(keret=1.0), chat=chat,
                              vegrehajto=vegrehajto, max_kor=6, tar=tar))
    assert szoveg == "KÉSZ" and letoltes == [("web_fetch", url)]                 # EGYSZER töltött le
    eredm = [m["content"] for m in kuldott[-1]["messages"] if m.get("role") == "tool"]
    assert eredm[0].startswith("[KIVONAT") and "1931-ben" in eredm[0]
    assert eredm[1].startswith("[MÁR OLVASTAD") and "Pótor József 1931-ben született Kolozsváron." in eredm[1]
    assert len(eredm[1]) < 600 and "Életrajz. Életrajz." not in eredm[1]       # nincs újra tartalom
    assert eredm[2].startswith("[ÚJ KIVONAT") and "a 12. oldal: a díjai" in olvasasok[-1]   # új kivonat, letöltés nélkül
    assert eredm[3].startswith("[MÁR OLVASTAD")                                    # a másik olvasó sem tölt le újra
    assert tar.ismetlesek == 3
    # hibás első letöltés után újra lehet próbálni; rövid első eredmény után a MÁSIK olvasó letölthet
    t2 = kivonat.EredmenyTar(olvaso=olvaso)
    _run(t2.feldolgoz("web_fetch", {"url": "https://a.hu"}, json.dumps({"error": "SSL"})))
    assert _run(t2.ismetles("web_fetch", {"url": "https://a.hu"})) is None
    _run(t2.feldolgoz("web_fetch", {"url": "https://b.hu"}, json.dumps({"url": "https://b.hu", "content": "fal"})))
    assert _run(t2.ismetles("web_scrape", {"url": "https://b.hu"})) is None
    assert _run(t2.ismetles("web_fetch", {"url": "https://b.hu"})).startswith("[MÁR OLVASTAD")
