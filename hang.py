"""HANGÜZENET (2026-11-22) — a Kommandant Telegramon diktál; a hangot a Studio írja át, a szöveg begépelt üzenetként megy.

A Kommandant a Telegramon diktál, a Bridge viszont a `voice` / `audio` / `video_note` üzenetet eddig NÉMÁN eldobta
(a poll-hurok csak a szöveget, a fotót és a dokumentumot nézte). És: „ha már megkapta, használja” — az átírást a Studio
végzi, nem új szolgáltatás.

AZ ÚT
-----
1. A Telegram-fájl (diktálás: OGG/Opus, hangfájl: mp3/m4a…, videóüzenet: mp4) a Kommandant Studiójába megy
   (`melleklet.studioba` — ugyanaz a feltöltés, mint a mellékleté).
2. `video(action="transcript", file_id, language="hu", vocabulary=<a Bridge nevei>)` → job; a `video(action="status",
   job_id, wait=30)` kivárja. A Studio Whisper large-v3-turbo-t futtat a saját gépén: KREDITBE NEM KERÜL (csak a
   `correct=true` AI-javítás kerülne — azt nem kérjük). A szókincs a Studio determinisztikus javítása: a nevek
   („Feldwebel, …”, „Kimi, …”) a megszólításhoz kellenek (cimzett.py), ezeket ne hallja félre.
3. A mondatok szövege = az átirat. A server visszaküldi („🎙 Ezt értettem: …”, hogy a félrehallást lássa), aztán
   ugyanúgy a `_handle_telegram_message`-be teszi, mint egy begépelt üzenetet.

MÉRVE (2026-11-22, élesben, a Studio éles példányán)
----------------------------------------------------
* Piper-felolvasás (hu_HU-imre), 40 szó, Telegram-szerű OGG/Opus 32 kbit/s: 36 szó hibátlan (90 %), a hibák:
  „jött-e levél” → „jött el levéle”, „írd” → „íd”, „tizenötödikére” → „15-igére”. Zajjal, telefon-sávszélességen
  (24 kbit/s): ugyanennyi, más szavakon. A szám számjeggyel jön („10 órára”). ~25 s egy 15 s-os felvételre.
* Valódi emberi beszéd (25 s, laza társalgás): folyó, olvasható magyar szöveg, mondatonként legfeljebb egy-két
  félrehallott szóval. A large-v3 nem jobb egyértelműen (a valódi felvétel elején rosszabb), ezért marad a turbo.
* A szókincs (`vocabulary`) a mintán semmit nem rontott; hosszú diktálásnál a prompt (`prompt`) semmit nem javított.
* RÖVID VÁLASZ („Mehet.”, „Igen.”, 1-2 s): prompt nélkül rossz — „Matt”, „Egen”, „Igen, mert”, „Nem, neid be”; egy
  semleges prompttal („röviden válaszol…”) is. A várható szavakat felsoroló prompttal (`ROVID_VALASZ_PROMPT`) 4/4
  helyes döntés: „Mehet”, „igen.”, „Igen, mehet.”, „nem, nem, rendben.” (a prompt „rendben”-je beszivárgott, de a
  tagadás erősebb: elvetés). Ezért csak akkor kapja, ha a felvétel rövid ÉS a Feldwebel kérdésre vár. Tiszta zajra
  a Studio VAD-ja „nincs beszéd”-et ad, a prompt nem hallucinál bele igent; egy rendes rövid mondatot (4 s,
  „Kimi, nézz utána…”) a prompt nem torzít.

Bukásnál `HangHiba` olvasható üzenettel: a Telegramra kimondott hiba megy, nem némaság.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import kulso_mcp
import melleklet

logger = logging.getLogger("hang")

NYELV = "hu"
#: A Studio szókincse (Whisper hotwords + a hasonló hangzású félrehallás javítása, AI nélkül): a Bridge nevei.
SZOKINCS = "Feldwebel, Kimi, DeepSeek, GLM, Faber, Claus, Bridge, Agora, Echolot"
#: Ennél hosszabb felvételnél előre szólunk a várható időről (a rövidnél a „gépel…” jelzés elég).
HOSSZU_S = 45
#: Legfeljebb ilyen hosszú felvétel lehet egy igen/nem-válasz (a Whisper initial promptja ilyenkor a várható szavak).
ROVID_S = 6
ROVID_VALASZ_PROMPT = "Rövid válasz a Feldwebel kérdésére: igen, mehet, nem, rendben."
#: Állapot-lekérdezésenként a Studio ennyit vár a kész eredményre (long-poll).
STATUSZ_VAR_S = 30
#: Az átirat JSON-ja hosszú felvételnél túlnő a Feldwebel-eszközök 15 ezres sapkáján — itt az egész kell.
EREDMENY_MAX_KAR = 400_000

_KITERJESZTES = {"audio/ogg": "oga", "audio/opus": "opus", "audio/mpeg": "mp3", "audio/mp3": "mp3",
                 "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac", "audio/wav": "wav",
                 "audio/x-wav": "wav", "audio/flac": "flac", "video/mp4": "mp4", "video/quicktime": "mov",
                 "video/webm": "webm"}


_ora = time.monotonic             # a tesztek ezt cserélik (a globális time.monotonic az eseményhurok órája)


class HangHiba(ValueError):
    """Az átírás nem sikerült — az üzenet a Kommandantnak szól (magyarul, teendővel)."""


def becsult_s(hossz_s: int) -> int:
    """A várható átírási idő: a Studio CPU-n ~1,6× valós idő, plusz a feltöltés és a sorban állás."""
    return int(20 + 1.6 * max(0, hossz_s))


def ido_szoveg(mp: int) -> str:
    mp = max(0, int(mp))
    if mp < 60:
        return f"{mp} mp"
    perc, mp = divmod(mp, 60)
    return f"{perc} perc" + (f" {mp} mp" if mp else "")


def fajlnev(ut: str, mime: str = "", most: datetime | None = None) -> str:
    """A Studióba menő név: `hanguzenet_2026-11-22_1750.oga` — a kiterjesztés a Telegram-útvonalból, vagy a MIME-ból
    (a Studio a kiterjesztésből tudja, hogy hang vagy videó)."""
    most = most or datetime.now(timezone.utc)
    utolso = (ut or "").rsplit("/", 1)[-1]
    ext = utolso.rsplit(".", 1)[-1].lower() if "." in utolso else ""
    if not ext or len(ext) > 5:
        ext = _KITERJESZTES.get((mime or "").split(";")[0].strip().lower(), "oga")
    return f"hanguzenet_{most.strftime('%Y-%m-%d_%H%M')}.{ext}"


def _json(szoveg: str) -> dict:
    try:
        v = json.loads(szoveg)
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def _szoveg_az_eredmenybol(eredmeny: dict) -> str:
    mondatok = eredmeny.get("sentences") or eredmeny.get("subtitles") or []
    return " ".join(str(m.get("text") or "").strip() for m in mondatok
                    if isinstance(m, dict) and str(m.get("text") or "").strip())


async def atir(raw: bytes, nev: str, *, hossz_s: int = 0, rovid_valasz: bool = False) -> str:
    """Egy felvétel magyar átirata a Studióval. → a szöveg; bukásnál `HangHiba` (olvasható ok).
    `rovid_valasz`: a Feldwebel kérdésre vár, és a felvétel rövid — a Whisper a várható igen/nem szavakat kapja."""
    if not raw:
        raise HangHiba("üres felvétel jött")
    if not kulso_mcp.bekotott("studio"):
        raise HangHiba("a Studio nincs bekötve a Bridge-en (hiányzik a STUDIO_MCP_KEY)")
    fajl = await melleklet.studioba(raw, nev)
    if not fajl or not fajl.get("file_id"):
        raise HangHiba("a felvétel nem jutott fel a Studióba (a Studio most nem érhető el)")
    t0 = _ora()
    hatarido = t0 + 120 + 3 * max(0, hossz_s)
    keres = {"action": "transcript", "file_id": fajl["file_id"], "language": NYELV, "vocabulary": SZOKINCS}
    if rovid_valasz:
        keres["prompt"] = ROVID_VALASZ_PROMPT
    v = _json(await kulso_mcp.hiv("studio", "video", keres))
    while True:
        if v.get("hiba"):
            raise HangHiba(f"a Studio hibát adott: {str(v['hiba'])[:200]}")
        allapot = v.get("status")
        if allapot == "done":
            break
        if v.get("error") or allapot in ("failed", "cancelled"):
            raise HangHiba(f"a Studio átírása nem sikerült: {str(v.get('error') or allapot)[:200]}")
        if "sentences" in v:            # azonnali (gyorsítótárból jött) eredmény, job nélkül
            v = {"status": "done", "result": v}
            break
        jid = v.get("job_id") or v.get("id")
        if not jid:
            raise HangHiba("a Studio nem indított átírást (nem értelmezhető válasz)")
        if _ora() > hatarido:
            raise HangHiba(f"az átírás {ido_szoveg(int(_ora() - t0))} alatt sem készült el")
        v = _json(await kulso_mcp.hiv("studio", "video", {"action": "status", "job_id": jid, "wait": STATUSZ_VAR_S},
                                      max_kar=EREDMENY_MAX_KAR))
        if v.get("status") in ("running", "queued"):
            await asyncio.sleep(1)      # ha a Studio nem várt (long-poll nélkül felelt), ne pörögjön a hurok
    szoveg = _szoveg_az_eredmenybol(v.get("result") or {})
    if not szoveg:
        raise HangHiba("nem hallottam beszédet a felvételen")
    logger.info("hang: %s átírva (%d karakter, %.1f s, Studio file_id=%s)", nev, len(szoveg),
                _ora() - t0, fajl.get("file_id"))
    return szoveg


def nyugta(szoveg: str) -> str:
    """A Kommandantnak visszaküldött átirat (HTML-escape a hívóé)."""
    return f"🎙 Ezt értettem: „{szoveg}”"
