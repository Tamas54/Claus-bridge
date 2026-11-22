"""JÓVÁHAGYÁS (2026-11-22) — a Feldwebel naptárba írása CSAK a Kommandant kifejezett igenje után; egy szó elég.

A Kommandant: ha a Feldwebel egy mellékletben, levélben vagy üzenetben HATÁRIDŐT talál, ajánlja fel, hogy beírja a
naptárba, emlékeztetővel — és egy szó („igen”, „mehet”) legyen elég.

Eddig a `calendar_create` egyetlen fékje a leírásban álló „MINDIG kérj megerősítést!” volt: a modell hívása rögtön
írt (a `CONFIRM_TOOLS` halmaz deklarált, de használatlan maradt). Most a kód a kapu:

1. A modell `calendar_create`-hívása JAVASLAT: a meglévő `action_drafts` táblába kerül (`kind='calendar'`,
   `status='pending'`, `source_message_id='feldwebel:<chat_id>'`) — ugyanaz a tábla, amit az e-mailből kinyert
   javaslatok és a webes felület jóváhagyó gombja (`action_draft_decide`) használ. Új tábla nincs.
2. A modell elmondja a javaslatot, és megkérdezi. A Kommandant következő üzenete, ha egyetlen igen/nem szó (vagy
   pár: „igen, mehet”, „ne írd be”) — és a javaslat a LEGUTÓBBI körből való —, LLM nélkül dönt: igennél beírja
   (`create_calendar_event`, emlékeztetőkkel), nemnél elveti. Hangüzenetből átírt „Igen.” ugyanígy.
3. Ha a modell előbb csak szövegben kérdezett, és a Kommandant „igen”-jére hívja a `calendar_create`-et, az a kör
   maga az igen: azonnal ír — de csak, ha az előző Feldwebel-üzenet a naptárról szólt.

A régebbi, megválaszolatlan javaslat a táblában marad (`pending`): a webes felületen jóváhagyható, de a puszta
„igen” már nem rá vonatkozik — a „mehet” mindig a legutóbbi kérdésre felel.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("feldwebel.jovahagyas")

FORRAS_ELOTAG = "feldwebel:"
TTL = timedelta(hours=24)
MAX_SZO = 6

#: Egyértelmű igen / nem — és a töltelékszavak, amik mellettük állhatnak („igen, köszi”, „jó, írd be”).
IGEN = {"igen", "mehet", "ok", "oké", "okés", "okay", "jó", "jóvan", "rendben", "persze", "csináld", "írd", "ird",
        "írjad", "beírhatod", "hajrá", "rajta", "yes", "yep", "naná", "természetesen", "igenis", "mehetsz", "jöhet",
        "aha", "ja", "jaja", "pontosan", "stimmt", "jawohl", "beírni"}
NEM = {"nem", "ne", "mégse", "mégsem", "hagyd", "hagyjuk", "töröld", "no", "nope", "felejtsd", "dobd", "nee", "nein"}
TOLTELEK = {"be", "kérem", "kérlek", "légyszi", "légy", "szíves", "köszi", "kösz", "köszönöm", "akkor", "csak",
            "nyugodtan", "szuper", "remek", "és", "a", "naptárba", "naptárban", "el", "azt", "ezt", "is", "most", "már",
            "kell", "szia", "sztem", "szerintem", "please", "thanks"}
IGEN_JEL = ("👍", "✅", "👌", "🆗")
NEM_JEL = ("👎", "❌", "🚫")

_HONAP = ("január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember",
          "október", "november", "december")
_NAP = ("hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap")


# ── Igen / nem ─────────────────────────────────────────────────────────────

def valasz(szoveg: str) -> str | None:
    """'igen' | 'nem' | None — csak akkor dönt, ha az üzenet egésze igen- vagy nem-szó (pár töltelékkel).
    „igen, de két nappal előtte” → None: az a modellé (módosított javaslat)."""
    s = (szoveg or "").strip().lower()
    if not s or len(s) > 80:
        return None
    szavak = re.findall(r"[a-záéíóöőúüű]+", s)
    if not szavak:
        if any(j in s for j in NEM_JEL):
            return "nem"
        return "igen" if any(j in s for j in IGEN_JEL) else None
    if len(szavak) > MAX_SZO or any(w not in IGEN | NEM | TOLTELEK for w in szavak):
        return None
    if any(w in NEM for w in szavak):         # „ne írd be” — a tagadás az erősebb
        return "nem"
    return "igen" if any(w in IGEN for w in szavak) else None


# ── Emlékeztetők ───────────────────────────────────────────────────────────

def emlekezteto_percek(raw) -> list[int]:
    """A modell `reminder_minutes`-e → a Google szerinti érvényes lista (0…40320 perc, legfeljebb 5, csökkenő)."""
    if raw in (None, "", []):
        return []
    if isinstance(raw, (int, float, str)):
        raw = [raw]
    out = set()
    for x in raw if isinstance(raw, (list, tuple)) else []:
        try:
            m = int(float(x))
        except (TypeError, ValueError):
            continue
        if 0 <= m <= 40320:
            out.add(m)
    return sorted(out, reverse=True)[:5]


def google_emlekeztetok(raw) -> dict | None:
    """A Google Calendar `reminders` mezője (felugró értesítés a telefonon), vagy None (a naptár alapbeállítása)."""
    percek = emlekezteto_percek(raw)
    if not percek:
        return None
    return {"useDefault": False, "overrides": [{"method": "popup", "minutes": m} for m in percek]}


def _egesz_napos(start: str) -> bool:
    return "T" not in (start or "")


def emlekezteto_szoveg(percek: list[int], egesz_napos: bool) -> str:
    """[3780, 900] egész napos eseménynél → „3 nappal előtte 9:00-kor, 1 nappal előtte 9:00-kor”."""
    reszek = []
    for m in percek:
        if egesz_napos:
            nap = -(-m // 1440)
            ora, perc = divmod(nap * 1440 - m, 60)
            reszek.append(f"{nap} nappal előtte {ora}:{perc:02d}-kor" if nap else "a napján 0:00-kor")
        elif m == 0:
            reszek.append("a kezdéskor")
        elif m % 1440 == 0:
            reszek.append(f"{m // 1440} nappal előtte")
        elif m % 60 == 0:
            reszek.append(f"{m // 60} órával előtte")
        else:
            reszek.append(f"{m} perccel előtte")
    return ", ".join(reszek)


def _datum(ido: str) -> str:
    try:
        d = datetime.fromisoformat(ido)
    except (TypeError, ValueError):
        return ido or "?"
    s = f"{d.year}. {_HONAP[d.month - 1]} {d.day}., {_NAP[d.weekday()]}"
    return s if _egesz_napos(ido) else f"{s} {d.hour}:{d.minute:02d}"


def javaslat_szoveg(p: dict) -> str:
    """Egy sor a Kommandantnak: mi kerül be, mikorra, milyen emlékeztetővel (ez az, amire az igent mondja)."""
    start = p.get("start") or ""
    ido = _datum(start) + (" (egész nap)" if _egesz_napos(start) else "")
    if p.get("end") and not _egesz_napos(start):
        try:
            v = datetime.fromisoformat(p["end"])
            ido += f"–{v.hour}:{v.minute:02d}"
        except (TypeError, ValueError):
            pass
    percek = emlekezteto_percek(p.get("reminder_minutes"))
    emlek = emlekezteto_szoveg(percek, _egesz_napos(start)) if percek else "a naptár alapbeállítása szerint"
    return f"{p.get('summary') or '?'} — {ido} · emlékeztető: {emlek}"


# ── A javaslat tára: action_drafts ──────────────────────────────────────────

def _most() -> str:
    return datetime.now(timezone.utc).isoformat()


def _forras(chat_id) -> str:
    return f"{FORRAS_ELOTAG}{chat_id or ''}"


def _utolso(conn, chat_id, szerep: str):
    try:
        return conn.execute("SELECT content, timestamp FROM telegram_history WHERE chat_id = ? AND role = ? "
                            "ORDER BY timestamp DESC LIMIT 1", (str(chat_id), szerep)).fetchone()
    except Exception:  # noqa: BLE001 — előzmény nélkül (önálló teszt, új adatbázis) nincs mihez mérni
        return None


def javasol(get_db, chat_id, adat: dict) -> int:
    """A modell naptár-hívása → függő javaslat az `action_drafts`-ban. → a javaslat id-je."""
    payload = {**adat, "details": adat.get("description") or "", "forras": "feldwebel", "chat_id": str(chat_id or "")}
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO action_drafts (source_message_id, kind, title, due_at, payload_json, status, created_at, "
            "decided_at) VALUES (?, 'calendar', ?, ?, ?, 'pending', ?, '')",
            (_forras(chat_id), (adat.get("summary") or "?")[:200], adat.get("start") or "",
             json.dumps(payload, ensure_ascii=False), _most()))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def fuggo(get_db, chat_id) -> list[dict]:
    """A LEGUTÓBBI kör függő naptárjavaslatai ebben a chatben: a Kommandant utolsó üzenete UTÁN születtek (vagyis a
    Feldwebel legutóbbi válaszában kérdezett rájuk), és 24 óránál frissebbek. A hívó a mostani üzenet tárolása ELŐTT
    kérdezi."""
    conn = get_db()
    try:
        hatar = (datetime.now(timezone.utc) - TTL).isoformat()
        u = _utolso(conn, chat_id, "user")
        if u is not None and u["timestamp"] > hatar:
            hatar = u["timestamp"]
        sorok = conn.execute(
            "SELECT id, title, due_at, payload_json, created_at FROM action_drafts WHERE status = 'pending' "
            "AND kind = 'calendar' AND source_message_id = ? AND created_at > ? ORDER BY id",
            (_forras(chat_id), hatar)).fetchall()
    except Exception as e:  # noqa: BLE001 — a tábla hiánya = nincs függő javaslat
        logger.warning("jovahagyas: a függő javaslatok nem olvashatók: %s", e)
        return []
    finally:
        conn.close()
    out = []
    for r in sorok:
        try:
            p = json.loads(r["payload_json"] or "{}")
        except ValueError:
            p = {}
        out.append({"id": r["id"], "adat": {"summary": p.get("summary") or r["title"],
                                            "start": p.get("start") or r["due_at"], "end": p.get("end") or "",
                                            "description": p.get("description") or p.get("details") or "",
                                            "reminder_minutes": p.get("reminder_minutes") or []}})
    return out


def _lezar(get_db, draft_id: int, statusz: str) -> None:
    conn = get_db()
    try:
        conn.execute("UPDATE action_drafts SET status = ?, decided_at = ? WHERE id = ?", (statusz, _most(), draft_id))
        conn.commit()
    finally:
        conn.close()


def azonnal_irhat(get_db, chat_id, szoveg: str) -> bool:
    """A modell ebben a körben a Kommandant igenjére hívja a naptárat (előbb csak szövegben kérdezett)? Akkor a kör
    maga az igen — de csak, ha az előző Feldwebel-üzenet a naptárról szólt. A mostani üzenet már az előzményben van."""
    if valasz(szoveg) != "igen":
        return False
    conn = get_db()
    try:
        a = _utolso(conn, chat_id, "assistant")
    finally:
        conn.close()
    return a is not None and "naptár" in (a["content"] or "").lower()


async def beir(create_func, adat: dict) -> dict:
    """A tényleges írás (`server.create_calendar_event`). → az eszköz JSON-válasza dict-ként."""
    if not create_func:
        return {"error": "a naptár most nem érhető el (nincs Google-kapcsolat a Bridge-en)"}
    try:
        res = await create_func(summary=adat["summary"], start=adat["start"], end=adat.get("end") or "",
                                description=adat.get("description") or "",
                                reminder_minutes=emlekezteto_percek(adat.get("reminder_minutes")) or None,
                                caller="feldwebel")
        return json.loads(res) if isinstance(res, str) else (res or {})
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


async def kezel(ctx, szoveg: str, chat_id) -> str | None:
    """A Kommandant egyszavas válasza a legutóbbi naptárjavaslatra — LLM nélkül. → a válasz szövege (már elküldve
    és az előzménybe írva), vagy None, ha az üzenet nem ilyen (a rendes Feldwebel-kör megy)."""
    from html import escape as html_escape
    dontes = valasz(szoveg)
    if dontes is None:
        return None
    javaslatok = fuggo(ctx.get_db, chat_id)
    if not javaslatok:
        return None
    from cimzett import fejlec
    from feldwebel.history import add_message
    add_message(chat_id, "user", szoveg)
    sorok = []
    if dontes == "nem":
        for j in javaslatok:
            _lezar(ctx.get_db, j["id"], "rejected")
        sorok.append("🚫 Rendben, nem írom be a naptárba: " + "; ".join(j["adat"]["summary"] for j in javaslatok))
    else:
        create = (ctx.capture_state or {}).get("_create_calendar_func")
        for j in javaslatok:
            res = await beir(create, j["adat"])
            if res.get("status") == "created":
                _lezar(ctx.get_db, j["id"], "created")
                sorok.append(f"📅 Beírtam a naptárba: {javaslat_szoveg(j['adat'])}"
                             + (f"\n{res['link']}" if res.get("link") else ""))
            else:
                # a döntés megvan, az írás bukott: 'approved' marad (a webes felületen újra próbálható)
                _lezar(ctx.get_db, j["id"], "approved")
                sorok.append(f"❌ Nem sikerült beírni ({j['adat']['summary']}): "
                             f"{str(res.get('error') or res)[:300]}")
    uzenet = "\n\n".join(sorok)
    await ctx.telegram_push(f"{fejlec('feldwebel')}\n\n{html_escape(uzenet)}")
    add_message(chat_id, "assistant", uzenet, "feldwebel")
    logger.info("jovahagyas: %s → %d naptárjavaslat (chat %s)", dontes, len(javaslatok), chat_id)
    return uzenet


def var_e(get_db, chat_id) -> bool:
    """Vár-e a Feldwebel igenre/nemre egy naptárjavaslatnál (a Faber-szándékfelismerő ilyenkor nem veszi el a
    „mehet”-et)."""
    try:
        return bool(fuggo(get_db, chat_id))
    except Exception:  # noqa: BLE001
        return False
