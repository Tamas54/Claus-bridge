"""Kész fájl Telegramra dokumentumként (telegram_fajl.py, 11-25)."""
import asyncio
import base64
import json

import telegram_fajl as T

SID = "68126a532da746cab77c04b5cb5597a4.pptx"


def _studio(valasz, lineage=None):
    hivasok = []

    async def hiv(eszkoz, parameterek):
        if eszkoz == "history":
            return json.dumps(lineage or {"descendants": []})
        hivasok.append((eszkoz, parameterek))
        return valasz if isinstance(valasz, str) else json.dumps(valasz)
    return hiv, hivasok


def _kuldo(ok=True):
    kuldott = []

    async def send(bajtok, nev, mime, felirat):
        kuldott.append((bajtok, nev, mime, felirat))
        return ok
    return send, kuldott


async def _letolt(url):
    assert url.startswith("https://studio/files/")
    return b"PK\x03\x04pakli"


def test_studio_id_a_szerkeszto_linkbol_is():
    assert T.studio_id(SID) == SID
    assert T.studio_id(f"https://ai-conversioneditor-production.up.railway.app/app/file/{SID}/edit") == SID
    assert T.studio_id("pakli.pptx") is None and T.studio_id("") is None


def test_studio_pakli_dokumentumkent_megy_a_studio_export_linkjen():
    hiv, hivasok = _studio({"files": [{"file_id": SID, "filename": "Mire a robot – mozgó.pptx",
                                       "url": f"https://studio/files/{SID}?t=1.abc", "size_bytes": 6891504,
                                       "edit_url": "https://studio/app/file/x/edit"}]})
    send, kuldott = _kuldo()
    r = asyncio.run(T.kuld({"studio_file_id": SID}, send_file=send, studio_hiv=hiv, get_db=None, letolt=_letolt))
    assert r["status"] == "sent" and r["nev"] == "Mire a robot – mozgó.pptx"
    assert hivasok == [("transfer", {"action": "export", "provider": "url", "file_ids": [SID]})]
    bajtok, nev, mime, felirat = kuldott[0]
    assert bajtok.startswith(b"PK") and nev.endswith(".pptx")
    assert mime == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert felirat == "📎 Mire a robot – mozgó.pptx"


def test_50_mb_folott_nem_tolt_le_hanem_kimondja_es_a_szerkeszto_link_marad():
    hiv, _ = _studio({"files": [{"url": "https://studio/files/x?t=1", "size_bytes": 60 * 1024 * 1024,
                                 "edit_url": "https://studio/app/file/x/edit"}]})
    send, kuldott = _kuldo()

    async def tilos(url):
        raise AssertionError("nem tölthet le")
    r = asyncio.run(T.kuld({"studio_file_id": SID}, send_file=send, studio_hiv=hiv, get_db=None, letolt=tilos))
    assert "50 MB" in r["error"] and r["edit_url"].endswith("/edit") and not kuldott


def test_studio_hiba_es_ures_valasz_kimondva():
    for valasz in ({"hiba": "a(z) studio most nem érhető el"}, {"files": []}, "nem json"):
        hiv, _ = _studio(valasz)
        send, kuldott = _kuldo()
        r = asyncio.run(T.kuld({"studio_file_id": SID}, send_file=send, studio_hiv=hiv, get_db=None,
                               letolt=_letolt))
        assert r.get("error") and not kuldott


def test_bridge_feltoltes_is_kuldheto():
    class Conn:
        def execute(self, sql, par):
            assert par == (12,)
            return self

        def fetchone(self):
            return {"filename": "jegyzet.pdf", "content_base64": base64.b64encode(b"%PDF-1.7").decode()}

        def close(self):
            pass
    send, kuldott = _kuldo()
    r = asyncio.run(T.kuld({"upload_id": "#12", "caption": "a jegyzet"}, send_file=send, studio_hiv=None,
                           get_db=Conn))
    assert r["status"] == "sent" and kuldott[0][1:] == ("jegyzet.pdf", "application/pdf", "a jegyzet")


def test_forras_nelkul_es_kuldo_nelkul_kimondott_hiba():
    send, _ = _kuldo()
    assert "studio_file_id" in asyncio.run(T.kuld({}, send_file=send, studio_hiv=None, get_db=None))["error"]
    assert "nem elérhető" in asyncio.run(T.kuld({"studio_file_id": SID}, send_file=None, studio_hiv=None,
                                                get_db=None))["error"]


def test_a_feldwebel_latja_a_dokumentum_kategoriaban_a_rizsraketak_nem():
    from feldwebel import eszkoz_ut
    from feldwebel.responder import TOOLS
    assert "telegram_fajl" in {t["function"]["name"] for t in TOOLS}
    assert "telegram_fajl" in eszkoz_ut.KATEGORIAK["dokumentum"]["eszkozok"]
    import server
    assert "telegram_fajl" in server.SUBAGENT_TILTOTT
    assert "telegram_fajl" not in server.SUBAGENT_TOOL_NAMES


UJ = "1e5289cf176243b7bc9face02aa83704.pptx"


def test_a_regi_azonositora_is_a_legujabb_verzio_megy():
    """11-25: a Feldwebel a régi (javítás előtti) azonosítót adta → „fájlformátum érvénytelen” az iPhone-on."""
    lineage = {"descendants": [
        {"file_id": UJ, "created_at": "2026-11-25T07:18:09+00:00", "deleted_at": None},
        {"file_id": "aaaa530a639b4891ac747ac8620c0e49.pdf", "created_at": "2026-11-25T09:00:00+00:00"},
        {"file_id": "bbbb530a639b4891ac747ac8620c0e49.pptx", "created_at": "2026-11-25T10:00:00+00:00",
         "deleted_at": "2026-11-25T10:05:00+00:00"}]}
    hiv, hivasok = _studio({"files": [{"filename": "mozgó, iPhone-Keynote.pptx",
                                       "url": f"https://studio/files/{UJ}?t=1"}]}, lineage)
    send, kuldott = _kuldo()
    r = asyncio.run(T.kuld({"studio_file_id": SID}, send_file=send, studio_hiv=hiv, get_db=None, letolt=_letolt))
    assert hivasok[0][1]["file_ids"] == [UJ] and UJ in r["verzio"] and kuldott
    hiv, hivasok = _studio({"files": [{"filename": "régi.pptx", "url": f"https://studio/files/{SID}?t=1"}]}, lineage)
    asyncio.run(T.kuld({"studio_file_id": SID, "pontosan_ezt": True}, send_file=send, studio_hiv=hiv, get_db=None,
                       letolt=_letolt))
    assert hivasok[0][1]["file_ids"] == [SID]


def test_szoveg_is_mehet_telegramra_fajl_nelkul():
    """11-25 este, a Kommandant: „de kérésre mehet a telegramra is!” — a kabin válasza a kabinban marad, kérésre a
    szöveg a Telegramra is kimegy (a kabin elfogása nélkül)."""
    elkuldott = []

    async def send_text(t):
        elkuldott.append(t)

    r = asyncio.run(T.kuld({"szoveg": "A HDD most nincs csatlakoztatva."}, send_file=None, studio_hiv=None,
                           get_db=None, send_text=send_text))
    assert r["status"] == "sent" and r["karakter"] == 32 and elkuldott == ["A HDD most nincs csatlakoztatva."]

    async def rossz(t):
        raise RuntimeError("Telegram HTTP 400")

    r = asyncio.run(T.kuld({"szoveg": "x"}, send_file=None, studio_hiv=None, get_db=None, send_text=rossz))
    assert "nem fogadta el" in r["error"]
    r = asyncio.run(T.kuld({"szoveg": "x"}, send_file=None, studio_hiv=None, get_db=None))
    assert "nem elérhető" in r["error"]
    r = asyncio.run(T.kuld({"szoveg": "x" * (T.SZOVEG_PLAFON + 1)}, send_file=None, studio_hiv=None, get_db=None,
                           send_text=send_text))
    assert "túl hosszú" in r["error"] and len(elkuldott) == 1
