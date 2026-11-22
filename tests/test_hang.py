"""HANGÜZENET (hang.py + server._hang_fogad) — diktálás → a Studio átírja → „🎙 Ezt értettem” → begépelt üzenetként.

1. Az átírás a Studio `video(action="transcript")`-jével megy, magyarul, a Bridge neveivel szókincsként; a job-ot az
   állapot-lekérdezés kivárja, az eredmény egészében (nem a Feldwebel 15 ezres sapkájával).
2. Minden bukás olvasható `HangHiba` — a Telegramra kimondott hiba megy, nem némaság.
3. Telegram: voice / audio / video_note → visszaküldött átirat → `_handle_telegram_message` (mint a begépelt szöveg);
   20 MB fölött és hosszú felvételnél kimondott jelzés. A poll-hurok a hangot nem dobja el.
Hálózat nincs: a Studio és a Telegram hamisítvány.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timezone

import pytest

import hang as H
import server


def _run(c):
    return asyncio.run(c)


@pytest.fixture
def studio(monkeypatch):
    """A Studio helyett: a feltöltés és az MCP-hívások naplózva, a válaszok sorban a `valaszok` listából."""
    naplo = {"hivasok": [], "feltolt": []}
    valaszok: list = []

    async def studioba(raw, nev):
        naplo["feltolt"].append((nev, len(raw)))
        return naplo.get("feltoltes", {"file_id": "abc123.oga", "edit_url": "https://studio/app/file/abc123"})

    async def hiv(nev, eszkoz, parameterek, *, max_kar=None):
        naplo["hivasok"].append((nev, eszkoz, dict(parameterek or {}), max_kar))
        return json.dumps(valaszok.pop(0), ensure_ascii=False)

    async def nincs_alvas(*a, **k):
        return None

    monkeypatch.setattr(H.melleklet, "studioba", studioba)
    monkeypatch.setattr(H.kulso_mcp, "hiv", hiv)
    monkeypatch.setattr(H.kulso_mcp, "bekotott", lambda n: n == "studio")
    monkeypatch.setattr(H.asyncio, "sleep", nincs_alvas)
    return naplo, valaszok


def _fut(jid="j1"):
    return {"id": jid, "job_id": jid, "status": "running", "result": None, "error": None}


def _kesz(*mondatok, jid="j1"):
    return {"id": jid, "job_id": jid, "status": "done", "error": None,
            "result": {"language": "hu", "sentences": [{"at": "00:00", "text": m} for m in mondatok],
                       "subtitles": [{"text": "NEM EZ"}]}}


# ── 1. Az átírás ─────────────────────────────────────────────────────────

def test_atiras_a_studioval_magyarul_szokinccsel(studio):
    naplo, valaszok = studio
    valaszok += [_fut(), _kesz("Feldwebel, nézd meg a leveleket.", " Köszönöm. ")]
    szoveg = _run(H.atir(b"OggS...", "hanguzenet_x.oga", hossz_s=6))
    assert szoveg == "Feldwebel, nézd meg a leveleket. Köszönöm."      # a mondatokból, nem a feliratsorokból
    assert naplo["feltolt"] == [("hanguzenet_x.oga", 7)]
    (sz1, e1, p1, _), (sz2, e2, p2, mk2) = naplo["hivasok"]
    assert (sz1, e1) == ("studio", "video") and p1["action"] == "transcript" and p1["file_id"] == "abc123.oga"
    assert p1["language"] == "hu" and "Feldwebel" in p1["vocabulary"] and "Kimi" in p1["vocabulary"]
    assert "correct" not in p1                     # a Studio AI-javítása kreditbe kerülne — nem kérjük
    assert "prompt" not in p1                      # hosszú diktálásnál a prompt nem javított (mérve)
    assert p2 == {"action": "status", "job_id": "j1", "wait": H.STATUSZ_VAR_S}
    assert mk2 == H.EREDMENY_MAX_KAR > 15_000      # hosszú felvétel JSON-ja sem csonkul félbe


def test_rovid_igen_nem_valasznal_a_varhato_szavak_a_promptban(studio):
    naplo, valaszok = studio
    valaszok += [_kesz("Mehet.")]
    assert _run(H.atir(b"x", "a.oga", hossz_s=1, rovid_valasz=True)) == "Mehet."
    p = naplo["hivasok"][0][2]
    assert p["prompt"] == H.ROVID_VALASZ_PROMPT and "mehet" in p["prompt"] and "igen" in p["prompt"]


def test_azonnali_eredmeny_job_nelkul(studio):
    _, valaszok = studio
    valaszok += [{"sentences": [{"text": "Igen."}]}]
    assert _run(H.atir(b"x", "a.oga")) == "Igen."


@pytest.mark.parametrize("valasz,jel", [
    ({"hiba": "a(z) studio most nem érhető el"}, "Studio hibát adott"),
    ({"id": "j1", "status": "failed", "error": "ffmpeg: invalid data"}, "ffmpeg: invalid data"),
    ({"id": "j1", "status": "done", "result": {"sentences": []}}, "nem hallottam beszédet"),
    ({"valami": "más"}, "nem indított átírást"),
])
def test_bukas_kimondott_hiba(studio, valasz, jel):
    _, valaszok = studio
    valaszok += [valasz]
    with pytest.raises(H.HangHiba, match=jel):
        _run(H.atir(b"x", "a.oga"))


def test_bukott_job_a_lekerdezesnel(studio):
    _, valaszok = studio
    valaszok += [_fut(), {"id": "j1", "status": "cancelled", "error": "Stopped."}]
    with pytest.raises(H.HangHiba, match="Stopped"):
        _run(H.atir(b"x", "a.oga"))


def test_studio_nelkul_es_feltoltes_nelkul(studio, monkeypatch):
    naplo, _ = studio
    monkeypatch.setattr(H.kulso_mcp, "bekotott", lambda n: False)
    with pytest.raises(H.HangHiba, match="STUDIO_MCP_KEY"):
        _run(H.atir(b"x", "a.oga"))
    monkeypatch.setattr(H.kulso_mcp, "bekotott", lambda n: True)
    naplo["feltoltes"] = None
    with pytest.raises(H.HangHiba, match="nem jutott fel a Studióba"):
        _run(H.atir(b"x", "a.oga"))
    with pytest.raises(H.HangHiba, match="üres felvétel"):
        _run(H.atir(b"", "a.oga"))


def test_idotullepes(studio, monkeypatch):
    _, valaszok = studio
    valaszok += [_fut()] * 50
    ora = iter(range(0, 100_000, 100))
    monkeypatch.setattr(H, "_ora", lambda: next(ora))
    with pytest.raises(H.HangHiba, match="alatt sem készült el"):
        _run(H.atir(b"x", "a.oga", hossz_s=10))


def test_fajlnev_a_kiterjesztes_a_telegram_utbol_vagy_a_mimebol():
    t = datetime(2026, 11, 22, 17, 50, tzinfo=timezone.utc)
    assert H.fajlnev("voice/file_12.oga", most=t) == "hanguzenet_2026-11-22_1750.oga"
    assert H.fajlnev("video_notes/file_3.mp4", most=t).endswith(".mp4")
    assert H.fajlnev("music/file_7", "audio/mpeg", most=t).endswith(".mp3")
    assert H.fajlnev("", "", most=t).endswith(".oga")


def test_ido_es_becsles():
    assert H.ido_szoveg(40) == "40 mp" and H.ido_szoveg(120) == "2 perc" and H.ido_szoveg(150) == "2 perc 30 mp"
    assert H.becsult_s(120) > 120


# ── 2. Telegram ──────────────────────────────────────────────────────────

@pytest.fixture
def tg(monkeypatch):
    push, kezelt, letolt, atirt = [], [], [], []
    allapot = {"atirat": "Kimi, mi a helyzet a forinttal?", "hiba": None}

    async def _push(s, *a, **k):
        push.append(s)

    async def _fajl(fid):
        letolt.append(fid)
        return b"OggS-hang", {"v1": "voice/file_1.oga", "a1": "music/file_2"}.get(fid, f"video_notes/{fid}.mp4")

    async def _handle(text, chat_id, csatorna="telegram"):
        kezelt.append((text, chat_id))

    async def _atir(raw, nev, *, hossz_s=0, rovid_valasz=False):
        atirt.append((nev, hossz_s, rovid_valasz))
        if allapot["hiba"]:
            raise allapot["hiba"]
        return allapot["atirat"]

    monkeypatch.setattr(server, "_telegram_push", _push)
    monkeypatch.setattr(server, "_telegram_fajl", _fajl)
    monkeypatch.setattr(server, "_handle_telegram_message", _handle)
    monkeypatch.setattr(server.hang, "atir", _atir)
    monkeypatch.setattr(server, "TELEGRAM_BOT_TOKEN", "")          # a „gépel…” jelzés ne menjen ki
    monkeypatch.setattr(server, "_feldwebel_kerdezett", lambda chat_id, percen_belul=30: allapot.get("kerdezett", False))
    monkeypatch.setattr(server, "_feldwebel_naptarra_var", lambda chat_id: False)
    return push, kezelt, letolt, atirt, allapot


def test_hanguzenet_atirva_visszaigazolva_es_begepeltkent_tovabb(tg):
    push, kezelt, letolt, atirt, _ = tg
    _run(server._hang_fogad({"voice": {"file_id": "v1", "duration": 4, "mime_type": "audio/ogg"}}, "42"))
    assert letolt == ["v1"] and atirt[0][0].endswith(".oga") and atirt[0][1] == 4
    assert push == ["🎙 Ezt értettem: „Kimi, mi a helyzet a forinttal?”"]
    assert kezelt == [("Kimi, mi a helyzet a forinttal?", "42")]   # ugyanaz az út, mint a begépelt szövegé


def test_rovid_valasz_csak_ha_a_feldwebel_kerdezett(tg):
    push, kezelt, _, atirt, allapot = tg
    allapot["atirat"] = "Mehet."
    _run(server._hang_fogad({"voice": {"file_id": "v1", "duration": 1}}, "42"))     # nem kérdezett: sima átírás
    allapot["kerdezett"] = True
    _run(server._hang_fogad({"voice": {"file_id": "v1", "duration": 1}}, "42"))     # „Beírjam?” után
    _run(server._hang_fogad({"voice": {"file_id": "v1", "duration": 20}}, "42"))    # hosszú: diktálás, nem válasz
    assert [a[2] for a in atirt] == [False, True, False]
    assert kezelt[1] == ("Mehet.", "42")


def test_hangfajl_es_videouzenet_is(tg):
    push, kezelt, letolt, atirt, _ = tg
    _run(server._hang_fogad({"audio": {"file_id": "a1", "duration": 10, "mime_type": "audio/mpeg"}}, "42"))
    _run(server._hang_fogad({"video_note": {"file_id": "vn1", "duration": 8}}, "42"))
    assert [a[0].rsplit(".", 1)[-1] for a in atirt] == ["mp3", "mp4"] and len(kezelt) == 2


def test_felirat_az_atirat_ele_kerul_es_a_html_escapelve(tg):
    push, kezelt, _, _, allapot = tg
    allapot["atirat"] = "írd be <b>péntekre</b> & szólj"
    _run(server._hang_fogad({"voice": {"file_id": "v1", "duration": 3}, "caption": "Feldwebel"}, "42"))
    assert kezelt == [("Feldwebel\n\nírd be <b>péntekre</b> & szólj", "42")]
    assert "&lt;b&gt;péntekre&lt;/b&gt; &amp; szólj" in push[0]


def test_bukasnal_kimondott_hiba_nem_nemasag(tg):
    push, kezelt, _, _, allapot = tg
    allapot["hiba"] = H.HangHiba("nem hallottam beszédet a felvételen")
    _run(server._hang_fogad({"voice": {"file_id": "v1", "duration": 2}}, "42"))
    assert kezelt == [] and len(push) == 1
    assert "nem tudtam átírni" in push[0] and "nem hallottam beszédet" in push[0] and "szövegben" in push[0]


def test_letoltesi_hiba_is_kimondva(tg, monkeypatch):
    push, kezelt, _, _, _ = tg

    async def rossz(fid):
        raise ValueError("a Telegram nem adta ki a fájlt")
    monkeypatch.setattr(server, "_telegram_fajl", rossz)
    _run(server._hang_fogad({"voice": {"file_id": "v1"}}, "42"))
    assert kezelt == [] and "nem adta ki a fájlt" in push[0]


def test_tul_nagy_es_hosszu_felvetel(tg):
    push, kezelt, letolt, _, _ = tg
    _run(server._hang_fogad({"audio": {"file_id": "nagy", "file_size": 25 * 1024 * 1024}}, "42"))
    assert letolt == [] and "20 MB" in push[0] and kezelt == []
    push.clear()
    _run(server._hang_fogad({"voice": {"file_id": "v1", "duration": 150}}, "42"))
    assert push[0].startswith("🎙 Megkaptam (2 perc 30 mp), átírom — kb.") and push[1].startswith("🎙 Ezt értettem")


def test_poll_hurok_a_hangot_a_hangaghoz_viszi():
    src = inspect.getsource(server._telegram_poll_loop)
    assert 'msg.get("voice") or msg.get("audio") or msg.get("video_note")' in src
    assert src.index("_hang_fogad") < src.index("_melleklet_fogad")


def test_az_atirat_a_rendes_telegram_uton_megy(monkeypatch, get_db):
    """A valódi `_handle_telegram_message`: az átirat a Faber-szándékfelismerőhöz is eljut, mint a begépelt szöveg."""
    latott = []

    async def szandek(text, **k):
        latott.append(text)
        return None

    async def _push(*a, **k):
        pass

    async def _atir(raw, nev, *, hossz_s=0, rovid_valasz=False):
        return "Faber, javítsd meg a reggeli briefet"

    async def _fajl(fid):
        return b"x", "voice/f.oga"
    monkeypatch.setattr(server.faber_relay, "szandek", szandek)
    monkeypatch.setattr(server, "_telegram_push", _push)
    monkeypatch.setattr(server, "_telegram_fajl", _fajl)
    monkeypatch.setattr(server.hang, "atir", _atir)
    monkeypatch.setattr(server, "FELDWEBEL_ENABLED", False)
    monkeypatch.setattr(server, "PYRAMID_ENABLED", False)
    monkeypatch.setattr(server, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(server, "get_db", get_db)
    _run(server._hang_fogad({"voice": {"file_id": "v1", "duration": 3}}, "42"))
    assert latott == ["Faber, javítsd meg a reggeli briefet"]
    conn = get_db()
    assert conn.execute("SELECT message FROM messages").fetchone()[0] == "Faber, javítsd meg a reggeli briefet"
    conn.close()
