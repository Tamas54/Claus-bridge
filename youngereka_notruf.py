"""
OPERATION NOTRUF — vészjelzés.
===============================

MIÉRT LÉTEZIK
-------------
A Kommandant megírta Rékának: „csak annyit kell beírni, hogy »kurva nagy
baj van«, és a modell értesít." A funkció NEM LÉTEZETT.

Egy vészjelző, amiben valaki hisz, de nem szól, rosszabb a semminél:
elveszi a késztetést, hogy mást csináljon. Ezért ez mindent megelőz.

AZ ELV: Ő INDÍTJA. NEM A GÉP FIGYEL.
------------------------------------
Ez nem sérti azt, hogy a beszélgetések privátak. Itt nem a rendszer
dönt arról, hogy jelentsen-e, hanem a felhasználó kér segítséget.
Automatikus, tartalom-alapú riasztás továbbra SINCS.

AMIT A JELZÉS TARTALMAZ: SEMMIT A BESZÉLGETÉSBŐL
------------------------------------------------
Csak az, amit ő maga ír mellé — ha ír. A rendszer SOHA nem emel ki
magától szöveget. Ha kiemelne, a lábléc-ígéret („amit írsz, azt nem
olvassa") csendben hazugsággá válna, és pont a legrosszabb pillanatban.

ELFOGULTSÁG: INKÁBB SÜLJÖN EL
-----------------------------
Egy téves riasztás ára egy telefonhívás. A kimaradté nem ez.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("bridge.notruf")

#: Kinek szólhat. A vendégeknek NINCS vészjelzésük (nem a mi
#: kapcsolatunk) — nekik a statikus krízis-számok mennek.
JOGOSULT = {"YoungeReka", "AnnaKatheder", "Bella", "kommandant"}

#: Megjelenített nevek a választóban.
CIMZETTEK = {
    "tamas": "Tamás",
    "anyu": "Anyu",
    "mindketto": "Mindkettő",
}


def tamas_szam() -> str:
    """A Kommandant telefonszáma (`NOTRUF_TAMAS_SZAM`), vagy üres string.

    Csak env-ben él. Ha nincs megadva, szám nélkül irányítunk hozzá —
    gyengébb, de nem hazug.
    """
    return (os.environ.get("NOTRUF_TAMAS_SZAM") or "").strip()


def tel_link(szam: str) -> str:
    """Koppintható telefonszám markdownban.

    Vészhelyzetben a koppintás jobb, mint kiolvasni és beütni: kevesebb
    lépés, és nem lehet elgépelni. A `tel:` sémához a szám szóköz és
    kötőjel nélkül kell.
    """
    if not szam:
        return ""
    tiszta = "".join(c for c in szam if c.isdigit() or c == "+")
    return f"[{szam}](tel:{tiszta})"


def anyu_elerheto() -> bool:
    """Kommandant-döntés 2026-08-07: egyelőre csak Tamás. Az „Anyu"
    opció a felületen szürke, amíg ez nincs beállítva."""
    return bool((os.environ.get("NOTRUF_ANYU_CHAT_ID") or "").strip())


#: STATIKUS, ELLENŐRZÖTT krízis-számok.
#:
#: Kizárólag az, amiben biztos vagyok. Egy rossz krízisszám nem
#: kellemetlenség, hanem valódi kár — inkább legyen rövid a lista.
#: A 112 Magyarországon ÉS Szerbiában is él; a 116 123 EU-harmonizált,
#: erre a célra fenntartott lelkisegély-szám.
#: A további országspecifikus vonalak akkor kerülnek be, ha a Kommandant
#: küldi az ellenőrzött listát — addig NEM találjuk ki őket.
KRIZIS_SZAMOK = [
    ("112", "Egységes segélyhívó — Magyarországon és Szerbiában is, "
            "0–24, ingyenes"),
    ("116 123", "Lelkisegély — ingyenes, névtelen, 0–24"),
]


def krizis_blokk() -> str:
    sorok = ["Ha most kell valaki, ezek mindig elérhetők:"]
    sorok += [f"- **{tel_link(sz)}** — {mi}" for sz, mi in KRIZIS_SZAMOK]
    return "\n".join(sorok)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# KÜLDÉS
# ============================================================

async def send(conn, instance: str, display_name: str, cimzett: str,
               kisero: str, *, telegram_push, event) -> dict:
    """Vészjelzés küldése. Visszaad: {sikeres, mikor, uzenet}.

    Sose dob: a hívó minden ágon tudjon mit mondani a felhasználónak.
    A NÉMA RIASZTÁS ROSSZABB A SEMMINÉL — ezért a hiba is beszédes.
    """
    if instance not in JOGOSULT:
        return {"sikeres": False, "jogosulatlan": True,
                "uzenet": "Ezen a felületen nincs vészjelzés."}

    mikor = datetime.now(timezone.utc)
    ora = mikor.strftime("%H:%M")

    # A jelzés törzse. A beszélgetésből SEMMI — csak amit ő maga írt.
    torzs = (f"🚨 VÉSZJELZÉS — {display_name} — {ora}\n\n")
    if (kisero or "").strip():
        torzs += f"Üzenete:\n„{kisero.strip()[:1500]}”\n\n"
    else:
        torzs += "Nem írt mellé semmit.\n\n"
    torzs += ("Ő indította, egy gombbal vagy kéréssel. A rendszer NEM "
              "olvasta a beszélgetését, és nem emelt ki belőle semmit.\n"
              "Hívd fel.")

    sikeres = False
    try:
        await telegram_push(torzs)
        sikeres = True
    except Exception as e:  # noqa: BLE001
        logger.error("NOTRUF Telegram-küldés BUKOTT: %s", e)

    # Napló: hogy MEGTÖRTÉNT. A kísérő üzenet szövege NEM kerül ide —
    # az a címzetté, nem a naplóé.
    try:
        event(conn, instance, "notruf_sent",
              f"cimzett={cimzett} sikeres={sikeres}")
    except Exception as e:  # noqa: BLE001
        logger.warning("notruf_sent naplózás bukott: %s", e)

    logger.warning("NOTRUF: %s → %s, sikeres=%s", instance, cimzett, sikeres)

    szam = tamas_szam()
    if sikeres:
        uz = (f"**Szóltam Tamásnak, {ora}-kor.**\n\n"
              + ("Ha 10 percen belül nem jelentkezik, hívd közvetlenül: "
                 f"**{tel_link(szam)}**\n\n" if szam
                 else "Ha 10 percen belül nem jelentkezik, hívd őt "
                      "közvetlenül.\n\n")
              + krizis_blokk())
    else:
        uz = ("**Nem tudtam elérni Tamást.** "
              + (f"Hívd fel most: **{tel_link(szam)}**\n\n" if szam
                 else "Hívd fel őt most, telefonon.\n\n")
              + krizis_blokk())

    return {"sikeres": sikeres, "mikor": mikor.isoformat(), "ora": ora,
            "uzenet": uz}
