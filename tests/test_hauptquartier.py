#!/usr/bin/env python3
"""OPERATION HAUPTQUARTIER — lépcsős jog, PIN, és a FÉLÍGÉRET tilalma.

    python tests/test_hauptquartier.py

A 8-as a legfontosabb: a Hauptquartier előbb azt mondta, „szükség esetén
megnézhetem ott", majd a következő üzenetben, hogy nincs hozzáférése.
Ígért egy képességet, amivel nem rendelkezett.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for k, v in (("YR_TOKEN", "REKA_TOKEN_1234567890abcdefgh"),
             ("AN_TOKEN", "ANNA_TOKEN_zyxwvu0987654321ab"),
             ("BL_TOKEN", "BELLA_TOKEN_qwerty0987654321"),
             ("HQ_TOKEN", "HQ_TOKEN_abcdefgh1234567890"),
             ("HQ_PIN", "483927")):
    os.environ[k] = v

import youngereka_chat as chat   # noqa: E402
import youngereka_hq as hq       # noqa: E402
from youngereka_access import CHAT_PROFILES, chat_profile   # noqa: E402

hibak: list[str] = []


def ok(f, c):
    print(("  ✓ " if f else "  ✗ ") + c)
    if not f:
        hibak.append(c)


def szakasz(c):
    print(f"\n── {c} " + "─" * max(0, 54 - len(c)))


tmp = pathlib.Path(tempfile.mkdtemp())
conn = sqlite3.connect(tmp / "hq.db")
conn.row_factory = sqlite3.Row
chat.ensure_schema(conn)


# ============================================================
szakasz("8. FÉLÍGÉRET — a képességlista kódból")

for prof in ("YoungeReka", "AnnaKatheder", "Bella"):
    blokk = hq.capability_block(prof)
    ok("NINCS eszközöd" in blokk, f"{prof}: kimondja, hogy nincs eszköze")
    ok("ELSŐ mondatod legyen a nemleges" in blokk,
       f"{prof}: az első mondat legyen a nemleges")
    ok("megnézem" in blokk and "szükség esetén megnézhetem" in blokk,
       f"{prof}: a konkrét tiltott fordulatok nevesítve")

hq_blokk = hq.capability_block("kommandant", conn)
ok("jelenlet" in hq_blokk and "beszelgetes_olvasas" in hq_blokk,
   "HQ: a valódi eszközök szerepelnek")
ok("NE AJÁNLD FEL" in hq_blokk, "HQ: a tartalom-olvasást nem ajánlja fel")
ok("email, naptár" in hq_blokk, "HQ: kimondja, mi NINCS bekötve")

# A LÉNYEG: a lista a KÓDBÓL jön, nem kézzel írt szövegből
nevek = {t["name"] for t in hq.TOOLS}
ok(all(n in hq_blokk for n in nevek),
   f"minden katalógus-eszköz megjelenik ({len(nevek)} db)")
hq.TOOLS.append({"name": "kiserleti_eszkoz", "pin": False,
                 "leiras": "teszt", "params": {"type": "object", "properties": {}}})
ok("kiserleti_eszkoz" in hq.capability_block("kommandant", conn),
   "új eszköz AUTOMATIKUSAN megjelenik a promptban (nincs kézi lista)")
hq.TOOLS.pop()

# egyik chat-prompt sem sorol tool-nevet kézzel
for nev, prof in CHAT_PROFILES.items():
    talalt = [t for t in ("family_presence", "family_chat", "oversight_open",
                          "upload_file", "ai_task") if t in prof["prompt"]]
    ok(not talalt, f"{nev}: a prompt NEM sorol tool-nevet kézzel ({talalt})")


# ============================================================
szakasz("1–4. PIN — lépcsős jogosultság")

ok(hq.pin_configured(), "a PIN be van állítva")
ok(not hq.unlocked(conn, "kommandant"), "1: alapból ZÁRVA")


async def _hiv(nev, args):
    return await hq.dispatch(conn, "kommandant", nev, args)


# 4: jelenlét PIN nélkül OK
r = asyncio.run(_hiv("jelenlet", {}))
ok("hiba" not in r and "instances" in r, "4: jelenlét PIN NÉLKÜL is megy")
ok("titkos" not in str(r), "4: …és tartalom nincs benne")
r = asyncio.run(_hiv("vendeglista", {}))
ok("hiba" not in r, "4: vendéglista PIN nélkül is megy")

# 1: nyers olvasás PIN nélkül MEGTAGADVA
r = asyncio.run(_hiv("beszelgetes_olvasas", {"kire": "YoungeReka", "indok": "x"}))
ok(r.get("zarva") is True, "1: nyers olvasás PIN NÉLKÜL megtagadva")
r = asyncio.run(_hiv("ablak_nyitas", {"kire": "YoungeReka", "indok": "x"}))
ok(r.get("zarva") is True, "1: ablaknyitás PIN nélkül megtagadva")
r = asyncio.run(_hiv("vendeg_kiloves", {"vendeg_id": "g", "indok": "x"}))
ok(r.get("zarva") is True, "1: vendég-kilövés PIN nélkül megtagadva")

# 2: PIN után OK
tiszta, volt = hq.strip_pin("nézd meg Réka beszélgetését 483927 kérlek")
ok(volt, "a PIN felismerve az üzenetben")
ok("483927" not in tiszta, "2: a PIN KIESETT az üzenetből (nem megy a modellhez)")
ok(tiszta == "nézd meg Réka beszélgetését kérlek", f"a maradék tiszta: {tiszta!r}")

_, rossz = hq.strip_pin("a mintaszám 123456 volt")
ok(not rossz, "rossz szám NEM old fel")

hq.unlock(conn, "kommandant")
ok(hq.unlocked(conn, "kommandant"), "2: PIN után feloldva")
r = asyncio.run(_hiv("beszelgetes_olvasas", {"kire": "YoungeReka", "indok": "Réka kérte"}))
ok(r.get("zarva") is not True and "hiba" not in r, "2: PIN után az olvasás megy")
ok("_ertesites" in r, "7: a válasz kimondja, hogy az érintett értesítést kap")
sor = conn.execute("SELECT * FROM oversight_audit ORDER BY created_at DESC").fetchone()
ok(sor and sor["reason"] == "Réka kérte", "2: oversight_audit sor keletkezett")

# indok nélkül nem megy, PIN után sem
r = asyncio.run(_hiv("beszelgetes_olvasas", {"kire": "YoungeReka"}))
ok("indok" in str(r.get("hiba", "")).lower(), "PIN után is KÖTELEZŐ az indok")

# 3: lejárat
conn.execute("UPDATE hq_unlock SET expires_at='2000-01-01T00:00:00+00:00'")
conn.commit()
ok(not hq.unlocked(conn, "kommandant"), "3: 15 perc után újra ZÁRVA")
r = asyncio.run(_hiv("beszelgetes_olvasas", {"kire": "YoungeReka", "indok": "x"}))
ok(r.get("zarva") is True, "3: …és az olvasás megint megtagadva")

# más profil egyáltalán nem kap eszközt
r = asyncio.run(hq.dispatch(conn, "YoungeReka", "jelenlet", {}))
ok("hiba" in r, "más profilon SEMMILYEN eszköz nem fut")


# ============================================================
szakasz("6. Rövidebb cookie a HQ-nak")

ok(chat._cookie_max_age("kommandant") == 7 * 24 * 3600, "6: HQ cookie 7 nap")
ok(chat._cookie_max_age("YoungeReka") == 90 * 24 * 3600, "6: a lányoké 90 nap")
ok(chat._cookie_max_age("Bella") == 90 * 24 * 3600, "6: Belláé 90 nap")


# ============================================================
szakasz("Értesítés — az önvédelem")

kapott = []
asyncio.run(hq.dispatch(conn, "kommandant", "jelenlet", {},
                        ertesit=lambda *a: kapott.append(a)))
ok(kapott == [], "jelenlét-lekérdezés NEM küld push-t (az rutin)")

hq.unlock(conn, "kommandant")
asyncio.run(hq.dispatch(conn, "kommandant", "beszelgetes_olvasas",
                        {"kire": "YoungeReka", "indok": "ellenőrzés"},
                        ertesit=lambda *a: kapott.append(a)))
ok(len(kapott) == 1 and kapott[0][0] == "olvasas",
   "nyers olvasás PUSH-t küld a Kommandantnak")

conn.close()

print("\n" + "═" * 60)
if hibak:
    print(f"PIROS — {len(hibak)} bukás:")
    for h in hibak:
        print("   ·", h)
    sys.exit(1)
print("MIND ZÖLD — teljes jog, de nem félígéretekkel.")
