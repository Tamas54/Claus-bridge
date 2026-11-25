"""Kész fájl a Kommandant Telegramjára, DOKUMENTUMKÉNT (2026-11-25).

A Kommandant (11-25 reggel, az előadása napján): „A telegramra a rendszer nem tudja kiküldeni a pptx-et.” A Bridge
eddig csak az AI-feladat exportját (`export_task`) és a Gmail-mellékletet tudta Telegramra küldeni. A Studióban kész
pakli vagy Word csak szerkesztő-linkként ment ki: ez telefonon belépést kér, az aláírt letöltő link pedig egy nap
múlva lejár („This file is gone”).

Most a Feldwebel `telegram_fajl` eszköze a fájlt maga tölti le, és dokumentumként küldi el. Telefonon így egy
koppintással megnyílik (Keynote, Word, PDF-néző), és a csetben megmarad.

Forrás:
- Studio `file_id` (például „68126a53….pptx”): a Studio `transfer(action="export", provider="url")` hívása aláírt
  letöltő linket ad, a fájl ezen jön le;
- a Bridge saját feltöltése (#ID, az `uploads` tábla).

A Telegram bot-API dokumentumkorlátja 50 MB. Ennél nagyobb fájlt nem töltünk le, hanem kimondjuk, és a
szerkesztő-link megy helyette.
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re

logger = logging.getLogger("claus-bridge.telegram_fajl")

TG_MAX_BAJT = 50 * 1024 * 1024
LETOLTES_TIMEOUT_S = 120
_STUDIO_ID = re.compile(r"^[0-9a-f]{32}\.[A-Za-z0-9]{1,8}$")
_MIME_KIEG = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "key": "application/x-iwork-keynote-sffkey",
}


def mime(nev: str) -> str:
    kit = nev.rsplit(".", 1)[-1].lower() if "." in nev else ""
    return _MIME_KIEG.get(kit) or mimetypes.guess_type(nev)[0] or "application/octet-stream"


def studio_id(ertek) -> str | None:
    """A Studio file_id a szerkesztő-linkből vagy magából az azonosítóból („…/app/file/<id>/edit” is jó)."""
    s = str(ertek or "").strip()
    m = re.search(r"/(?:app/file|files)/([0-9a-f]{32}\.[A-Za-z0-9]{1,8})", s)
    if m:
        s = m.group(1)
    return s if _STUDIO_ID.match(s) else None


def _export_valasz(szoveg: str) -> dict:
    """A Studio `transfer` válaszából az első fájl adatai ({url, filename, size_bytes}), vagy {"hiba": …}."""
    try:
        d = json.loads(szoveg)
    except (TypeError, ValueError):
        m = re.search(r'"url"\s*:\s*"([^"]+/files/[^"]+)"', szoveg or "")
        return {"url": m.group(1)} if m else {"hiba": f"a Studio nem adott letöltő linket: {str(szoveg)[:300]}"}
    if isinstance(d, dict) and d.get("hiba"):
        return {"hiba": f"Studio: {str(d['hiba'])[:300]}"}
    fajlok = d.get("files") if isinstance(d, dict) else None
    if not fajlok or not isinstance(fajlok[0], dict) or not fajlok[0].get("url"):
        return {"hiba": "a Studio nem adott letöltő linket ehhez a fájlhoz"}
    return fajlok[0]


async def _letolt(url: str, max_bajt: int = TG_MAX_BAJT) -> bytes:
    import httpx
    async with httpx.AsyncClient(timeout=LETOLTES_TIMEOUT_S, follow_redirects=True) as c:
        async with c.stream("GET", url) as r:
            r.raise_for_status()
            darabok, n = [], 0
            async for d in r.aiter_bytes():
                n += len(d)
                if n > max_bajt:
                    raise ValueError(f"a fájl nagyobb, mint {max_bajt // (1024 * 1024)} MB")
                darabok.append(d)
    return b"".join(darabok)


async def studio_fajl(file_id: str, *, studio_hiv, letolt=_letolt) -> dict:
    """{bajtok, nev, edit_url} vagy {"hiba", "edit_url"?}. `studio_hiv(eszkoz, parameterek)` → a Studio válasza
    szövegként (élesben `kulso_mcp.hiv("studio", …)`)."""
    v = _export_valasz(await studio_hiv("transfer", {"action": "export", "provider": "url", "file_ids": [file_id]}))
    edit = v.get("edit_url")
    if v.get("hiba"):
        return v
    meret = v.get("size_bytes") or v.get("size")
    if isinstance(meret, int) and meret > TG_MAX_BAJT:
        return {"hiba": f"a fájl {meret / 1048576:.1f} MB, a Telegram dokumentumkorlátja 50 MB — küldd a "
                        f"szerkesztő-linket", "edit_url": edit}
    nev = str(v.get("filename") or v.get("name") or file_id)
    if "." not in nev:
        nev += "." + file_id.rsplit(".", 1)[-1]
    try:
        bajtok = await letolt(v["url"])
    except Exception as e:  # noqa: BLE001
        return {"hiba": f"a letöltés nem sikerült: {str(e)[:200]}", "edit_url": edit}
    return {"bajtok": bajtok, "nev": nev, "edit_url": edit}


def feltoltes(upload_id: int, get_db) -> dict:
    """A Bridge saját feltöltése (#ID) → {bajtok, nev} vagy {"hiba"}."""
    conn = get_db()
    try:
        row = conn.execute("SELECT filename, content_base64 FROM uploads WHERE id = ?", (int(upload_id),)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"hiba": f"a #{upload_id} feltöltés nincs meg"}
    if not row["content_base64"]:
        return {"hiba": f"a #{upload_id} feltöltés tartalma nincs a Bridge-ben"}
    bajtok = base64.b64decode(row["content_base64"])
    if len(bajtok) > TG_MAX_BAJT:
        return {"hiba": f"a #{upload_id} feltöltés nagyobb, mint 50 MB"}
    return {"bajtok": bajtok, "nev": row["filename"] or f"feltoltes_{upload_id}"}


async def kuld(args: dict, *, send_file, studio_hiv, get_db, letolt=_letolt) -> dict:
    """A Feldwebel `telegram_fajl` eszköze. Siker: {"status": "sent", "nev", "meret_mb"}; különben {"error"}."""
    if not send_file:
        return {"error": "a Telegram-küldés most nem elérhető (nincs bot-token vagy chat-azonosító)"}
    sid = studio_id(args.get("studio_file_id"))
    uid = args.get("upload_id")
    if sid:
        f = await studio_fajl(sid, studio_hiv=studio_hiv, letolt=letolt)
    elif uid not in (None, "", 0):
        try:
            f = feltoltes(int(str(uid).lstrip("#")), get_db)
        except ValueError:
            f = {"hiba": f"érvénytelen feltöltés-azonosító: {uid!r}"}
    else:
        return {"error": "add meg a Studio file_id-t (studio_file_id, pl. „68126a53….pptx”) vagy a feltöltés "
                         "#ID-ját (upload_id)"}
    if f.get("hiba"):
        return {"error": f["hiba"], **({"edit_url": f["edit_url"]} if f.get("edit_url") else {})}
    nev = f["nev"]
    felirat = str(args.get("caption") or "").strip() or f"📎 {nev}"
    from html import escape
    if not await send_file(f["bajtok"], nev, mime(nev), escape(felirat)[:1024]):
        return {"error": "a Telegram nem fogadta el a fájlt (a Bridge naplójában az ok)",
                **({"edit_url": f["edit_url"]} if f.get("edit_url") else {})}
    logger.info("telegram_fajl: %s (%.1f MB) elküldve", nev, len(f["bajtok"]) / 1048576)
    return {"status": "sent", "nev": nev, "meret_mb": round(len(f["bajtok"]) / 1048576, 1),
            "message": f"Elküldve Telegramra dokumentumként: {nev}"}
