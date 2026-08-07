"""
OPERATION HAUPTQUARTIER — a Kommandant chat-felületének eszközei.
==================================================================

MIÉRT LÉTEZIK EZ A MODUL
------------------------
A Hauptquartier előbb azt mondta: „szükség esetén megnézhetem ott”, a
következő üzenetben pedig: nincs hozzáférésem. Ígért egy képességet,
amivel nem rendelkezett — mert a promptban szerepeltek a tool-nevek, a
kódban viszont NEM VOLT tool-hívás a chat-úton.

Két javítás kell hozzá, és külön-külön egyik sem elég:
  1. az eszközök tényleges bekötése (ez a modul)
  2. a képességlista KÓDBÓL generálása (`capability_block`), hogy a
     prompt soha ne állíthasson többet, mint ami be van kötve

LÉPCSŐS JOGOSULTSÁG
-------------------
A jog megvan, de nem egy szinten:

  PIN NÉLKÜL   jelenlét, státusz, vendéglista, hibák — TARTALOM NÉLKÜL
  PIN UTÁN     nyers beszélgetés-szöveg, ablaknyitás, vendég-kilövés

Ez az egyetlen reális támadás ellen véd: valaki felveszi a feloldott
telefont. Jelszó nélkül a teljes jog annyit ér, mint a lezáratlan
képernyő. Egy PIN 15 percre old fel.

A PIN SOHA NEM MEGY FEL A MODELLNEK. A `strip_pin()` kiszedi az
üzenetből, mielőtt az bárhová továbbmenne.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("bridge.yr_hq")

#: Egy PIN-megadás ennyi ideig old fel.
UNLOCK_PERC = 15

#: Kinek van eszköze. A vendégnek NINCS — nála a `capability_block`
#: ezt ki is mondja, így nem ígérhet semmit.
CSALAD = {"YoungeReka", "AnnaKatheder", "Bella", "kommandant"}
TOOLS_FOR = CSALAD


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hq_unlock (
          instance   TEXT PRIMARY KEY,
          expires_at TIMESTAMP NOT NULL,
          created_at TIMESTAMP NOT NULL
        )""")
    conn.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
# PIN
# ============================================================

def pin_configured() -> bool:
    return len((os.environ.get("HQ_PIN") or "").strip()) >= 6


_PIN_MINTA = re.compile(r"\b\d{6,12}\b")


def strip_pin(uzenet: str) -> tuple[str, bool]:
    """(PIN nélküli üzenet, volt-e helyes PIN).

    A PIN SOHA nem mehet fel a modellnek — se a SiliconFlow-nak, se a
    naplóba. Ezért itt esik ki, a lehető legkorábban.

    Konstans idejű összehasonlítás: a naiv `==` az első eltérő
    karakternél kilép, amiből időméréssel jegyenként kitalálható.
    """
    pin = (os.environ.get("HQ_PIN") or "").strip()
    if not pin or not uzenet:
        return uzenet, False
    talalt = False
    darabok = []
    utolso = 0
    for m in _PIN_MINTA.finditer(uzenet):
        if hmac.compare_digest(m.group(0), pin):
            talalt = True
            darabok.append(uzenet[utolso:m.start()])
            utolso = m.end()
    if not talalt:
        return uzenet, False
    darabok.append(uzenet[utolso:])
    return re.sub(r"\s{2,}", " ", "".join(darabok)).strip(), True


def unlock(conn, instance: str) -> None:
    ensure_schema(conn)
    veg = _now() + timedelta(minutes=UNLOCK_PERC)
    conn.execute("INSERT INTO hq_unlock (instance, expires_at, created_at) "
                 "VALUES (?,?,?) ON CONFLICT(instance) DO UPDATE SET "
                 "expires_at=excluded.expires_at", (instance, veg.isoformat(),
                                                    _now().isoformat()))
    conn.commit()
    logger.info("HQ feloldva %d percre (%s)", UNLOCK_PERC, instance)


def unlocked(conn, instance: str) -> bool:
    try:
        ensure_schema(conn)
        r = conn.execute("SELECT expires_at FROM hq_unlock WHERE instance=?",
                         (instance,)).fetchone()
        if not r:
            return False
        d = datetime.fromisoformat(r["expires_at"])
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)) > _now()
    except Exception as e:  # noqa: BLE001
        logger.warning("HQ unlock-ellenőrzés bukott (zárva vesszük): %s", e)
        return False


def unlock_remaining(conn, instance: str) -> int:
    """Hány perc van még a feloldásból. 0 = zárva."""
    try:
        ensure_schema(conn)
        r = conn.execute("SELECT expires_at FROM hq_unlock WHERE instance=?",
                         (instance,)).fetchone()
        if not r:
            return 0
        d = datetime.fromisoformat(r["expires_at"])
        d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        return max(0, int((d - _now()).total_seconds() // 60))
    except Exception:  # noqa: BLE001
        return 0


# ============================================================
# ESZKÖZ-KATALÓGUS — EZ az igazságforrás
# ============================================================
#
# A rendszerprompt képességlistája ebből generálódik. Ha egy eszköz
# innen hiányzik, a modell nem is tud róla, tehát nem ígérheti.

TOOLS: list[dict] = [
    {
        # OPERATION NOTRUF. MINDEN családi felületen ott van, mert a
        # vészjelzést a felhasználó indítja — nem a gép figyel.
        # Elfogultság: INKÁBB SÜLJÖN EL. Egy téves riasztás ára egy
        # telefonhívás; a kimaradté nem ez.
        "name": "veszjelzes",
        "pin": False,
        "kinek": CSALAD,
        "leiras": "VÉSZJELZÉS: azonnal szól Tamásnak. Akkor hívd, ha a "
                  "felhasználó bajban van és segítséget kér — pl. „nagy baj "
                  "van”, „segíts”, „szólj Tamásnak”. Ha EGYÉRTELMŰ a kérés, "
                  "hívd AZONNAL, ne kérdezz vissza. Ha bizonytalan a jel, "
                  "előbb kérdezd meg tőle, hogy szóljak-e — és ha nemet mond, "
                  "fogadd el. A beszélgetésből SEMMI nem megy át: csak amit "
                  "ő maga üzen, ha üzen.",
        "params": {"type": "object", "properties": {
            "uzenet": {"type": "string",
                       "description": "Amit ŐMAGA üzen. Üresen is mehet. "
                                      "SOHA ne írj ide olyat, amit te "
                                      "emeltél ki a beszélgetésből."}}},
    },
    {
        "name": "jelenlet",
        "kinek": {"kommandant"},
        "pin": False,
        "leiras": "Ki használja a felületeit, mióta nem írt, változott-e a "
                  "ritmusa, mennyit költött, volt-e hiba. TARTALOM NÉLKÜL.",
        "params": {"type": "object", "properties": {
            "kire": {"type": "string",
                     "description": "'YoungeReka', 'AnnaKatheder', 'Bella' "
                                    "vagy üres (mindenki)"}}},
    },
    {
        "name": "vendeglista",
        "kinek": {"kommandant"},
        "pin": False,
        "leiras": "Kit hívtak meg vendégnek, mikor, él-e még a meghívás. "
                  "A vendégek beszélgetéseit SENKI nem olvashatja.",
        "params": {"type": "object", "properties": {}},
    },
    {
        "name": "beszelgetes_olvasas",
        "kinek": {"kommandant"},
        "pin": True,
        "leiras": "NYERS beszélgetés-szöveg. Az érintett értesítést kap róla, "
                  "és naplósor keletkezik. Csak kifejezett kérésre.",
        "params": {"type": "object", "properties": {
            "kire": {"type": "string", "description": "kinek a beszélgetése"},
            "beszelgetes_id": {"type": "string",
                               "description": "egy konkrét beszélgetés (opcionális)"},
            "indok": {"type": "string",
                      "description": "KÖTELEZŐ — a naplóba kerül"}},
            "required": ["kire", "indok"]},
    },
    {
        "name": "ablak_nyitas",
        "kinek": {"kommandant"},
        "pin": True,
        "leiras": "Ablakot nyit a web-clausnak, hogy ő is olvashasson nyers "
                  "tartalmat. Enélkül web-claus csak jelenlét-adatot lát.",
        "params": {"type": "object", "properties": {
            "kire": {"type": "string"},
            "indok": {"type": "string"},
            "percek": {"type": "integer", "description": "1-240, alap 30"}},
            "required": ["kire", "indok"]},
    },
    {
        "name": "vendeg_kiloves",
        "kinek": {"kommandant"},
        "pin": True,
        "leiras": "Egy vendég hozzáférésének azonnali megszüntetése.",
        "params": {"type": "object", "properties": {
            "vendeg_id": {"type": "string"},
            "indok": {"type": "string"}}, "required": ["vendeg_id", "indok"]},
    },
]

_BY_NAME = {t["name"]: t for t in TOOLS}


def tools_for(instance: str) -> list[dict]:
    """Az adott profil eszközei. Ez az EGYETLEN szűrő — a katalógus
    `kinek` mezője dönt, nem egy külön lista, amit el lehet felejteni."""
    return [t for t in TOOLS if instance in t.get("kinek", set())]


def openai_tools(instance: str) -> list[dict]:
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["leiras"], "parameters": t["params"]}}
        for t in tools_for(instance)]


def capability_block(instance: str, conn=None) -> str:
    """A prompthoz fűzött KÉPESSÉGLISTA — kódból, nem kézzel.

    Ez a §3-as javítás lényege. A prompt nem állíthat többet, mint ami
    be van kötve, mert a listát az `TOOLS` katalógus adja. Ha egy eszköz
    onnan hiányzik, itt sem jelenik meg.
    """
    sajat = tools_for(instance)
    if not sajat:
        return (
            "\n\nAMI A KEZEDBEN VAN\n"
            "Ezen a felületen NINCS eszközöd: nem érsz el emailt, naptárat, "
            "fájlrendszert, más felhasználók beszélgetéseit, és nem tudsz "
            "üzenetet küldeni senkinek. Amit tudsz: a beszélgetés, a "
            "feltöltött fájlok, és — ha a felhasználó a keresés-gombot "
            "nyomja — a hozzád eljuttatott keresési találatok.\n"
            "Ha olyat kérne, amihez eszköz kellene, az ELSŐ mondatod legyen "
            "a nemleges. Tiltott fordulatok: megnézem / utánanézek a "
            "rendszerben / szükség esetén megnézhetem / mindjárt "
            "ellenőrzöm. Ezek ígéretek, és nem tudod betartani. Mondd "
            "meg, mi nincs bekötve, és mit tud helyette csinálni.")

    sorok = ["\n\nAMI A KEZEDBEN VAN",
             "Ezek VALÓDI eszközök, hívd őket, ha kell. Ami nincs a "
             "listán, azt NEM tudod — arra az ELSŐ mondatod legyen a "
             "nemleges, ne ígérj utánanézést."]
    szabad = [t for t in sajat if not t["pin"]]
    zart = [t for t in sajat if t["pin"]]
    if szabad:
        sorok.append("\nBármikor:")
        sorok += [f"- {t['name']} — {t['leiras']}" for t in szabad]
    if zart:
        sorok.append("\nCSAK PIN után (a Kommandant beírja a chatbe):")
        sorok += [f"- {t['name']} — {t['leiras']}" for t in zart]

    if conn is not None and zart:
        maradt = unlock_remaining(conn, instance)
        sorok.append(f"\nPIN-állapot: {'FELOLDVA, még ' + str(maradt) + ' perc'
                                       if maradt else 'ZÁRVA'}.")
        if not maradt:
            sorok.append("Ha zárt eszközt kérne, mondd meg, hogy ehhez be "
                         "kell írnia a PIN-t — és NE hívd meg a tool-t.")

    if instance == "kommandant":
        sorok.append(
            "\nVISELKEDÉS\n"
            "Alapból JELENLÉT-adattal válaszolj („írtak-e?” → számok).\n"
        "Nyers tartalmat CSAK kifejezett kérésre olvass, indokkal. Magadtól "
        "SOHA ne idézz, ne összegezz és ne hozz fel részletet belőle, és NE "
        "AJÁNLD FEL az olvasást — ha kéri, megy, de ne tereld arra.\n"
            "Ami NINCS bekötve: email, naptár, fájlrendszer, "
            "token-forgatás. Ezekre azonnal mondd meg, hogy nem éred el.")
    else:
        sorok.append(
            "\nEZEN KÍVÜL NINCS eszközöd: nem érsz el emailt, naptárat, "
            "fájlrendszert, más felhasználók beszélgetéseit. Ha olyat "
            "kérne, az ELSŐ mondatod legyen a nemleges. Tiltott "
            "fordulatok: megnézem / utánanézek a rendszerben / szükség "
            "esetén megnézhetem / mindjárt ellenőrzöm. Ezek ígéretek, és "
            "nem tudod betartani.")
    return "\n".join(sorok)


# ============================================================
# VÉGREHAJTÁS
# ============================================================

async def dispatch(conn, instance: str, name: str, args: dict,
                   *, ertesit=None) -> dict:
    """Egy tool-hívás. Sose dob — a hibát adatként adja vissza."""
    t = _BY_NAME.get(name)
    if not t:
        return {"hiba": f"Nincs ilyen eszköz: {name}"}

    # NÉV SZERINT kell egyeznie, nem elég, hogy VAN valamilyen eszköze.
    # Egy korábbi változat csak azt nézte, üres-e a készlete — és mivel a
    # `veszjelzes` MINDEN családi felületen ott van, a készlet sosem volt
    # üres, tehát Réka modellje elérhette volna a `jelenlet`-et is,
    # mindenkiről. A teszt fogta meg (2026-08-07).
    if instance not in t.get("kinek", set()):
        return {"hiba": "Ez az eszköz ezen a felületen nincs bekötve."}

    if t["pin"]:
        if not pin_configured():
            return {"hiba": "A PIN nincs beállítva a szerveren (HQ_PIN), "
                            "ezért a védett eszközök nem érhetők el."}
        if not unlocked(conn, instance):
            return {"zarva": True,
                    "hiba": f"Ehhez PIN kell. Írd be a chatbe, és "
                            f"{UNLOCK_PERC} percig nyitva marad."}
        indok = (args.get("indok") or "").strip()
        if not indok:
            return {"hiba": "Ehhez kötelező az indok — a naplóba kerül."}

    import youngereka_chat as yrc
    import youngereka_guest as yrg

    try:
        if name == "veszjelzes":
            # A NOTRUF-ot a hívó (chat-réteg) bonyolítja le, mert neki van
            # kéznél a display_name és a Telegram-küldő. Itt csak jelezzük.
            return {"_notruf": True, "uzenet": (args.get("uzenet") or "")}

        if name == "jelenlet":
            return yrc.presence(conn, instance=(args.get("kire") or "").strip())

        if name == "vendeglista":
            return {"vendegek": yrg.all_guests(conn),
                    "megjegyzes": "A vendégek beszélgetéseit senki nem "
                                  "olvashatja — sem a meghívó, sem te."}

        if name == "beszelgetes_olvasas":
            kire = (args.get("kire") or "").strip()
            bid = (args.get("beszelgetes_id") or "").strip()
            indok = args["indok"].strip()
            adat = yrc.oversight(conn, instance=kire, session_id=bid,
                                 full=not bid, limit=20)
            yrc.audit(conn, caller="kommandant", instance=kire or "*",
                      session_id=bid, reason=indok)
            if ertesit:
                ertesit("olvasas", kire or "*", indok)
            adat["_ertesites"] = ("Az érintett értesítést kap erről az "
                                  "olvasásról, és naplósor keletkezett.")
            return adat

        if name == "ablak_nyitas":
            r = yrc.window_open(conn, (args.get("kire") or "").strip(),
                                args["indok"].strip(),
                                max(1, min(int(args.get("percek") or 30), 240)))
            if ertesit:
                ertesit("ablak", args.get("kire") or "*", args["indok"])
            return r

        if name == "vendeg_kiloves":
            n = yrg.kill(conn, (args.get("vendeg_id") or "").strip())
            if ertesit:
                ertesit("kiloves", args.get("vendeg_id") or "?", args["indok"])
            return {"kilove": n}
    except Exception as e:  # noqa: BLE001
        logger.exception("HQ tool hiba: %s", name)
        return {"hiba": f"Az eszköz hibára futott: {type(e).__name__}"}

    return {"hiba": "ismeretlen eszköz"}
