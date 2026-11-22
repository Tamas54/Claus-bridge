"""MELLÉKLET (melleklet.py + a Telegram- és a webes bekötés) — fénykép, PDF, Word… → Studio + szöveg → Feldwebel.

1. Fénykép: a Sonnet írja át (a Studióba is felmegy); dokumentum: a Studio `extract`-ja; szkennelt PDF / Studio
   nélkül: a helyi feldolgozó, képnél átírással.
2. Az üzenet `[Melléklet #ID: …]` fejléccel megy a Feldwebelnek, a Studio file_id-vel és a szerkesztő-linkkel.
3. Telegram: fotó, dokumentum, album (egy kérés), a Faber-szándékfelismerő átugorja; 20 MB fölött kimondott hiba.
4. Web: feltöltés belépve, a chat a mellékletekkel együtt küldi.
5. Az LLM-kapu tanácskérésre és mellékletre Sonnetet ad; a „fájl” szó nem „fáj”.
"""
from __future__ import annotations

import asyncio
import io
import json
import sqlite3

import pytest

import llm_kapu
import melleklet as M


def _run(c):
    return asyncio.run(c)


@pytest.fixture
def get_db(tmp_path):
    p = tmp_path / "m.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE uploads (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL, mime_type TEXT NOT "
              "NULL, content_text TEXT DEFAULT '', content_base64 TEXT DEFAULT '', uploaded_by TEXT NOT NULL, "
              "uploaded_at TEXT NOT NULL)")
    c.commit()
    c.close()

    def _g():
        k = sqlite3.connect(p)
        k.row_factory = sqlite3.Row
        return k
    return _g


@pytest.fixture
def hamis(monkeypatch):
    """A Studio és az átíró helyett felvevő hamisítványok."""
    naplo = {"studio": [], "extract": [], "atir": []}

    async def studioba(raw, nev):
        naplo["studio"].append(nev)
        return {"file_id": f"s_{nev}", "edit_url": f"https://studio/app/file/s_{nev}"}

    async def studio_szoveg(fid):
        naplo["extract"].append(fid)
        return naplo.get("extract_valasz", "Kinyert szöveg a Studióból, elég hosszú. " * 10)

    async def atir(kepek, nev):
        naplo["atir"].append((len(kepek), nev))
        return "HATÁROZAT\n[bélyegző: Érkezett 2026. nov. 14.]\nA kérelmet elutasítom."

    monkeypatch.setattr(M, "studioba", studioba)
    monkeypatch.setattr(M, "_studio_szoveg", studio_szoveg)
    monkeypatch.setattr(M, "atir", atir)
    M._FRISS.clear()
    return naplo


def _jpeg():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(b, "JPEG")
    return b.getvalue()


def _docx(szoveg):
    import docx
    d = docx.Document()
    d.add_paragraph(szoveg)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def _most():
    return "2026-11-22T15:00:00+00:00"


# ── 1. A feldolgozás útjai ───────────────────────────────────────────────

def test_fenykep_atirva_es_a_studioba_is(get_db, hamis):
    m = _run(M.feldolgoz(_jpeg(), "hatarozat.jpg", "image/jpeg", get_db=get_db, most=_most, kitol="t"))
    assert hamis["atir"] == [(1, "hatarozat.jpg")] and hamis["studio"] == ["hatarozat.jpg"]
    assert hamis["extract"] == []                       # a képet nem a Studio olvassa
    assert "[bélyegző: Érkezett 2026. nov. 14.]" in m["szoveg"] and m["cimke"] == "fénykép · átírva"
    assert m["studio"]["file_id"] == "s_hatarozat.jpg"
    sor = get_db().execute("SELECT filename, content_text, content_base64, uploaded_by FROM uploads").fetchone()
    assert sor["filename"] == "hatarozat.jpg" and "HATÁROZAT" in sor["content_text"] and sor["content_base64"]


def test_dokumentum_a_studio_extractjaval(get_db, hamis):
    m = _run(M.feldolgoz(_docx("Tanulmány a drónokról."), "tanulmany.docx", "", get_db=get_db, most=_most, kitol="t"))
    assert hamis["extract"] == ["s_tanulmany.docx"] and hamis["atir"] == []
    assert m["cimke"] == "Studio-kinyerés" and m["szoveg"].startswith("Kinyert szöveg")


def test_ha_a_studio_keveset_ad_a_helyi_feldolgozo_jon(get_db, hamis):
    hamis["extract_valasz"] = ""
    m = _run(M.feldolgoz(_docx("Helyi olvasás: " + "szöveg " * 40), "a.docx", "", get_db=get_db, most=_most,
                         kitol="t"))
    assert "Helyi olvasás" in m["szoveg"] and m["studio"]["file_id"] == "s_a.docx"


def test_studio_nelkul_is_megy(get_db, hamis, monkeypatch):
    async def nincs(raw, nev):
        return None
    monkeypatch.setattr(M, "studioba", nincs)
    m = _run(M.feldolgoz(_docx("Studio nélkül: " + "x " * 150), "b.docx", "", get_db=get_db, most=_most, kitol="t"))
    assert "Studio nélkül" in m["szoveg"] and m["studio"] is None and hamis["extract"] == []


def test_olvashatatlan_es_studio_nelkul_hiba(get_db, hamis, monkeypatch):
    async def nincs(raw, nev):
        return None
    monkeypatch.setattr(M, "studioba", nincs)
    with pytest.raises(ValueError):
        _run(M.feldolgoz(b"\x00\x01\x02" * 100, "x.bin", "", get_db=get_db, most=_most, kitol="t"))


def test_tul_nagy_fajl(get_db, hamis):
    with pytest.raises(ValueError, match="túl nagy"):
        _run(M.feldolgoz(b"x" * (M.MAX_FAJL + 1), "nagy.pdf", "", get_db=get_db, most=_most, kitol="t"))


def test_hosszu_szoveg_levagva_megjegyzessel(get_db, hamis):
    hamis["extract_valasz"] = "a" * (M.MAX_SZOVEG_KAR + 50)
    m = _run(M.feldolgoz(_docx("x"), "hosszu.docx", "", get_db=get_db, most=_most, kitol="t"))
    assert len(m["szoveg"]) == M.MAX_SZOVEG_KAR and "Studióban" in m["megjegyzesek"][0]


# ── 2. Az üzenet ─────────────────────────────────────────────────────────

def test_uzenet_fejlec_studioval_es_alapkeres(get_db, hamis):
    m = _run(M.feldolgoz(_jpeg(), "irat.jpg", "image/jpeg", get_db=get_db, most=_most, kitol="t"))
    u = M.uzenet("", [m])
    assert u.startswith(M.ALAP_KERES)
    assert f"[Melléklet #{m['id']}: irat.jpg — fénykép · átírva · Studio file_id=s_irat.jpg · " \
           f"szerkesztő: https://studio/app/file/s_irat.jpg]" in u
    assert M.van_melleklet(u) and not M.van_melleklet("sima kérdés")
    assert M.uzenet("Írj rá választ", [m]).startswith("Írj rá választ\n\n[Melléklet #")


def test_betolt_a_friss_tarbol_es_az_adatbazisbol(get_db, hamis):
    m = _run(M.feldolgoz(_jpeg(), "c.jpg", "image/jpeg", get_db=get_db, most=_most, kitol="t"))
    assert M.betolt(get_db, [m["id"]])[0]["studio"]["file_id"] == "s_c.jpg"
    M._FRISS.clear()
    v = M.betolt(get_db, [str(m["id"]), "nem-szam", 99999])
    assert len(v) == 1 and "HATÁROZAT" in v[0]["szoveg"]


# ── 3. Telegram ──────────────────────────────────────────────────────────

import server  # noqa: E402


@pytest.fixture
def tg(monkeypatch, get_db, hamis):
    push, feldwebel, letolt = [], [], []

    async def _push(s, *a, **k):
        push.append(s)

    async def _fajl(fid):
        letolt.append(fid)
        return (_jpeg(), f"photos/{fid}.jpg") if fid.startswith("p") else (_docx("Irat " * 80), f"documents/{fid}")

    async def _handle(text, chat_id, csatorna="telegram"):
        feldwebel.append(text)

    monkeypatch.setattr(server, "_telegram_push", _push)
    monkeypatch.setattr(server, "_telegram_fajl", _fajl)
    monkeypatch.setattr(server, "_handle_telegram_message", _handle)
    monkeypatch.setattr(server, "get_db", get_db)
    monkeypatch.setattr(server, "ALBUM_VARAKOZAS_S", 0.01)
    server._ALBUMOK.clear()
    return push, feldwebel, letolt


def test_telegram_foto_felirattal_a_feldwebelhez(tg):
    push, feldwebel, _ = tg
    _run(server._melleklet_fogad({"photo": [{"file_id": "p_kicsi"}, {"file_id": "p_nagy"}],
                                  "caption": "Írj rá fellebbezést"}, "42"))
    assert push == ["📎 Megkaptam, olvasom…"]
    assert len(feldwebel) == 1 and feldwebel[0].startswith("Írj rá fellebbezést\n\n[Melléklet #")
    assert "HATÁROZAT" in feldwebel[0]


def test_telegram_dokumentum_es_tul_nagy(tg):
    push, feldwebel, letolt = tg
    _run(server._melleklet_fogad({"document": {"file_id": "d1", "file_name": "tanulmany.docx",
                                               "mime_type": "application/vnd.openxmlformats"}}, "42"))
    assert "tanulmany.docx" in feldwebel[0] and feldwebel[0].startswith(M.ALAP_KERES)
    _run(server._melleklet_fogad({"document": {"file_id": "d2", "file_name": "film.mp4",
                                               "file_size": 30 * 1024 * 1024}}, "42"))
    assert "d2" not in letolt and any("20 MB" in p for p in push) and len(feldwebel) == 1


def test_telegram_album_egy_keres_tobb_oldallal(tg):
    push, feldwebel, _ = tg

    async def album():
        for i, cap in enumerate(["Mit írjak erre?", "", ""]):
            await server._melleklet_fogad({"photo": [{"file_id": f"p{i}"}], "caption": cap,
                                           "media_group_id": "g1"}, "42")
        await asyncio.sleep(0.2)
    _run(album())
    assert len(feldwebel) == 1 and feldwebel[0].startswith("Mit írjak erre?")
    assert feldwebel[0].count("[Melléklet #") == 3 and "foto_3.jpg" in feldwebel[0]
    assert push == ["📎 Megkaptam (3 fájl), olvasom…"]


def test_mellekletes_kerest_a_faber_szandekfelismero_atugorja(monkeypatch, get_db):
    hivva = []

    async def szandek(*a, **k):
        hivva.append(a)
        return {"szandek": "kivansag", "szoveg": "x", "repo": "studio", "kerelem": None}

    async def _push(*a, **k):
        pass
    monkeypatch.setattr(server.faber_relay, "szandek", szandek)
    monkeypatch.setattr(server, "_telegram_push", _push)
    monkeypatch.setattr(server, "FELDWEBEL_ENABLED", False)
    monkeypatch.setattr(server, "get_db", get_db)
    try:
        _run(server._handle_telegram_message("Írj rá választ\n\n[Melléklet #1: a.jpg — fénykép]\nszöveg", "42"))
    except Exception:  # noqa: BLE001 — a tesztadatbázisban nincs messages tábla; itt csak a felismerő számít
        pass
    assert hivva == []


# ── 5. Az LLM-kapu szintje ───────────────────────────────────────────────

@pytest.mark.parametrize("szoveg,elvart", [
    ("Szia, mi a helyzet?", "olcso"),
    ("küldd el a fájlt Jánosnak", "olcso"),
    ("fáj a fejem, mit vegyek?", "kozep"),
    ("Írj rá választ\n\n[Melléklet #3: a.jpg — fénykép]\nrövid", "kozep"),
    ("mit tegyek a felmondással?", "kozep"),
])
def test_feldwebel_szint_tanacskeresnel_sonnet(szoveg, elvart):
    payload = {"model": "deepseek-ai/DeepSeek-V4-Pro", "tools": [{"type": "function"}],
               "messages": [{"role": "system", "content": "x"}, {"role": "user", "content": szoveg}]}
    assert llm_kapu.szint_valaszt(payload, "feldwebel/telegram")[0] == elvart
