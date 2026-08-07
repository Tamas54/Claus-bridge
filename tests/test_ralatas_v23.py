#!/usr/bin/env python3
"""v2.3 §F — web-claus rálátása, és a `caller`-hamisítás lezárása.

    python tests/test_ralatas_v23.py

A 38-as a legfontosabb: 2026-08-07-ig a `caller="web-claus"` SZABAD SZÖVEG
volt, tehát bárki, aki elérte a /mcp-t, kiolvashatta a beszélgetéseket.
Élesben igazolt lyuk. Ez a teszt őrzi, hogy be is maradjon zárva.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import sys
import tempfile
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["YR_TOKEN"] = "REKA_TOKEN_1234567890abcdefgh"
os.environ["AN_TOKEN"] = "ANNA_TOKEN_zyxwvu0987654321ab"
os.environ["WC_TOKEN"] = "WEBCLAUS_TOKEN_abcdefgh1234"
os.environ["KM_TOKEN"] = "KOMMANDANT_TOKEN_abcdef1234"

import youngereka_chat as chat        # noqa: E402
from youngereka_access import (AUTH_FIELD, AUTH_NONCE,   # noqa: E402
                               authenticated, force_caller,
                               resolve_instance_from_path)

hibak: list[str] = []


def ok(f, c):
    print(("  ✓ " if f else "  ✗ ") + c)
    if not f:
        hibak.append(c)


def szakasz(c):
    print(f"\n── {c} " + "─" * max(0, 54 - len(c)))


# ============================================================
szakasz("Stáb-tokenek")

ok(resolve_instance_from_path("WEBCLAUS_TOKEN_abcdefgh1234") == "web-claus",
   "WC_TOKEN → web-claus")
ok(resolve_instance_from_path("KOMMANDANT_TOKEN_abcdef1234") == "kommandant",
   "KM_TOKEN → kommandant")
ok(resolve_instance_from_path("WEBCLAUS_TOKEN_abcdefgh1234", staff=False) is None,
   "a chat-úton stáb-token NEM lép be")
ok(resolve_instance_from_path("REKA_TOKEN_1234567890abcdefgh") == "YoungeReka",
   "a családi tokenek változatlanul élnek")


# ============================================================
szakasz("38. A hitelesítés jelölője — a lyuk")

hamis = {"method": "tools/call", "params": {"name": "family_chat",
         "arguments": {"caller": "web-claus", "full": True}}}
ok(not authenticated(hamis["params"]["arguments"]),
   "38: a PUSZTÁN beírt caller='web-claus' NEM hitelesített")
ok(not authenticated({"auth": "tippelt"}), "tippelt jelölő → nem hitelesített")
ok(not authenticated({}), "hiányzó jelölő → nem hitelesített")
ok(not authenticated(None), "nem-dict → nem hitelesített")

javitott = force_caller(hamis, "web-claus")
ok(authenticated(javitott["params"]["arguments"]),
   "a TOKENES úton átment kérés hitelesített")
ok(javitott["params"]["arguments"][AUTH_FIELD] == AUTH_NONCE,
   "…mert a force_caller beírta a folyamat-jelölőt")
ok(len(AUTH_NONCE) >= 32, f"a jelölő elég hosszú ({len(AUTH_NONCE)} karakter)")

# A tool SAJÁT `instance` paramétere NEM eshet áldozatul: több tool
# `instance` néven a CÉLT nevezi meg, nem a hívót.
cel = {"method": "tools/call", "params": {"name": "oversight_open",
       "arguments": {"caller": "web-claus", "instance": "YoungeReka",
                     "reason": "teszt"}}}
ok(force_caller(cel, "kommandant")["params"]["arguments"]["instance"] == "YoungeReka",
   "a tool `instance` CÉL-paramétere ÉRINTETLEN marad")
ok(force_caller(cel, "kommandant")["params"]["arguments"]["caller"] == "kommandant",
   "…miközben a caller felülíródik")

# A hívó által BEÍRT auth mezőt is felülírja
sajat = {"method": "tools/call", "params": {"name": "family_chat",
         "arguments": {"caller": "web-claus", "auth": "sajat-tipp"}}}
ok(force_caller(sajat, "web-claus")["params"]["arguments"][AUTH_FIELD] == AUTH_NONCE,
   "a hívó saját `auth` mezőjét is FELÜLÍRJA")


# ============================================================
szakasz("Ablak — nyitás, lejárat, hatókör")

tmp = pathlib.Path(tempfile.mkdtemp())
conn = sqlite3.connect(tmp / "o.db")
conn.row_factory = sqlite3.Row
chat.ensure_schema(conn)
chat.ensure_oversight_schema(conn)

sid = str(uuid.uuid4())
conn.execute("INSERT INTO yr_chat_sessions (id,instance,title,created_at,"
             "updated_at) VALUES (?,?,?,?,?)",
             (sid, "YoungeReka", "Réka dolga", chat._now(), chat._now()))
conn.execute("INSERT INTO yr_chat_messages (id,session_id,role,content,created_at) "
             "VALUES (?,?,?,?,?)",
             (str(uuid.uuid4()), sid, "user", "titkos szöveg", chat._now()))
conn.commit()

ok(not chat.window_is_open(conn, "YoungeReka"), "39: alapból NINCS nyitott ablak")
chat.window_open(conn, "YoungeReka", "Réka kérte, hogy nézzük meg együtt", 30)
ok(chat.window_is_open(conn, "YoungeReka"), "40: nyitás után van ablak")
ok(not chat.window_is_open(conn, "AnnaKatheder"),
   "az ablak instance-re szól — Annára NEM nyílt")
ok(chat.window_is_open(conn, "", sid),
   "session_id-ből is feloldja, kire szól az ablak")

# lejárt ablak
conn.execute("UPDATE oversight_windows SET expires_at='2000-01-01T00:00:00+00:00'")
conn.commit()
ok(not chat.window_is_open(conn, "YoungeReka"), "lejárt ablak → zárva")

chat.window_open(conn, "*", "mindenre", 30)
ok(chat.window_is_open(conn, "AnnaKatheder"), "a '*' ablak mindenkire szól")


# ============================================================
szakasz("Napló")

chat.audit(conn, "web-claus", "YoungeReka", sid, "Réka kérte")
sor = conn.execute("SELECT * FROM oversight_audit").fetchone()
ok(sor["caller"] == "web-claus" and sor["reason"] == "Réka kérte",
   "40: az oversight_audit sor caller='web-claus'-szal keletkezik")
ok(sor["session_id"] == sid, "…a session_id-vel együtt")

chat.audit(conn, "kommandant", "YoungeReka", sid, "ellenőrzés")
sorok = conn.execute("SELECT caller FROM oversight_audit ORDER BY created_at").fetchall()
ok({r["caller"] for r in sorok} == {"web-claus", "kommandant"},
   "42: mindkét olvasó UGYANABBA a naplóba kerül, azonos alakban")
mezok_wc = set(dict(conn.execute(
    "SELECT * FROM oversight_audit WHERE caller='web-claus'").fetchone()))
mezok_km = set(dict(conn.execute(
    "SELECT * FROM oversight_audit WHERE caller='kommandant'").fetchone()))
ok(mezok_wc == mezok_km,
   "42: a két esemény MEGKÜLÖNBÖZTETHETETLEN alakú (a lány ugyanazt látná)")


# ============================================================
szakasz("41. Jelenlét — tartalom NÉLKÜL")

p = chat.presence(conn)
ok(set(p["instances"]) == {"YoungeReka", "AnnaKatheder"}, "mindkét lány benne")
r = p["instances"]["YoungeReka"]
ok(r["uzenet_osszesen"] == 1, f"üzenetszám: {r['uzenet_osszesen']}")
ok(r["napja_nem_irt"] is not None, "„napja nem írt” kiszámolva")
ok("valtozas" in r and "uzenet_utolso_14_nap" in r and "uzenet_elozo_14_nap" in r,
   "a VÁLTOZÁS mérhető (14 nap vs. előző 14 nap)")
ok("hajnali_uzenet_14_nap" in r, "hajnali aktivitás mérve")
ok("napi_keret_usd" in r and "kereses" in r, "keret és keresési kvóta")

# A LÉNYEG: semmi tartalom
egesz = __import__("json").dumps(p, ensure_ascii=False, default=str)
ok("titkos szöveg" not in egesz, "41: a jelenlét-nézetben NINCS üzenet-szöveg")
ok("Réka dolga" not in egesz, "41: …és beszélgetés-cím sincs")
for tiltott in ("content", "messages", "sessions", "title"):
    ok(tiltott not in set(r), f"41: a mezők közt nincs {tiltott!r}")
ok("nyitott_ablakok" in p and "utolso_olvasasok" in p,
   "a Kommandant látja a nyitott ablakokat és a legutóbbi olvasásokat")
ok("nem őrszem" in p["megjegyzes"], "a nézet kimondja, hogy nem őrszem")

conn.close()

print("\n" + "═" * 60)
if hibak:
    print(f"PIROS — {len(hibak)} bukás:")
    for h in hibak:
        print("   ·", h)
    sys.exit(1)
print("MIND ZÖLD — a caller-hamisítás lezárva, a jelenlét tartalom nélkül.")
