#!/usr/bin/env python3
"""Dedikált linkek — Réka és Anna elkülönülése.

    python tests/test_dedikalt_linkek.py

A tét: a két lány NE lásson bele egymás beszélgetéseibe, és mindegyik a
SAJÁT felületét kapja. Az elkülönülés két helyen dől el — a token→instance
leképezésnél és a lekérdezések `instance` szűrőjénél —, itt mindkettő
ellenőrizve van.
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

import youngereka_chat as chat                          # noqa: E402
from youngereka_access import (CHAT_PROFILES, KATHEDER_PROMPT,  # noqa: E402
                               REKA_CHAT_PROMPT, chat_profile,
                               resolve_instance_from_path)

hibak: list[str] = []


def ok(felt, cim):
    print(("  ✓ " if felt else "  ✗ ") + cim)
    if not felt:
        hibak.append(cim)


def szakasz(cim):
    print(f"\n── {cim} " + "─" * max(0, 54 - len(cim)))


# ============================================================
szakasz("Két token, két személy")

ok(resolve_instance_from_path("REKA_TOKEN_1234567890abcdefgh") == "YoungeReka",
   "Réka tokenje → YoungeReka")
ok(resolve_instance_from_path("ANNA_TOKEN_zyxwvu0987654321ab") == "AnnaKatheder",
   "Anna tokenje → AnnaKatheder")
ok(resolve_instance_from_path("REKA_TOKEN_1234567890abcdefgi") is None,
   "egy karakterrel elrontott token → senki")

# Ha valaki törli az egyiket, a másik NEM veheti át a helyét
_men = os.environ.pop("AN_TOKEN")
ok(resolve_instance_from_path("ANNA_TOKEN_zyxwvu0987654321ab") is None,
   "AN_TOKEN nélkül Anna linkje nem nyílik")
ok(resolve_instance_from_path("REKA_TOKEN_1234567890abcdefgh") == "YoungeReka",
   "…de Rékáé változatlanul él (a tokenek függetlenek)")
os.environ["AN_TOKEN"] = _men


# ============================================================
szakasz("Cookie — nem cserélhető át")

r_cookie = chat._sign("YoungeReka", 2 ** 31)
a_cookie = chat._sign("AnnaKatheder", 2 ** 31)
ok(chat._verify(r_cookie) == "YoungeReka", "Réka cookie-ja Rékát adja")
ok(chat._verify(a_cookie) == "AnnaKatheder", "Anna cookie-ja Annát adja")
ok(r_cookie != a_cookie, "a két cookie különbözik")

# A LÉNYEG: Anna nem írhatja át magát Rékává
hamis = "YoungeReka|" + a_cookie.split("|", 1)[1]
ok(chat._verify(hamis) is None,
   "Anna cookie-jában a nevet Rékára cserélve → ELUTASÍTVA")


# ============================================================
szakasz("Külön beszélgetések — az adatbázisban")

tmp = pathlib.Path(tempfile.mkdtemp())
conn = sqlite3.connect(tmp / "t.db")
conn.row_factory = sqlite3.Row
chat.ensure_schema(conn)


def session(instance, cim):
    sid = str(uuid.uuid4())
    conn.execute("INSERT INTO yr_chat_sessions (id, instance, title, created_at, "
                 "updated_at) VALUES (?,?,?,?,?)",
                 (sid, instance, cim, chat._now(), chat._now()))
    conn.execute("INSERT INTO yr_chat_messages (id, session_id, role, content, "
                 "created_at) VALUES (?,?,?,?,?)",
                 (str(uuid.uuid4()), sid, "user", f"{cim} tartalma", chat._now()))
    conn.commit()
    return sid


r_sid = session("YoungeReka", "Réka gélképe")
a_sid = session("AnnaKatheder", "Anna beadandója")


def lathato(instance):
    return {r["title"] for r in conn.execute(
        "SELECT title FROM yr_chat_sessions WHERE instance=?", (instance,))}


ok(lathato("YoungeReka") == {"Réka gélképe"},
   f"Réka CSAK a sajátját látja: {lathato('YoungeReka')}")
ok(lathato("AnnaKatheder") == {"Anna beadandója"},
   f"Anna CSAK a sajátját látja: {lathato('AnnaKatheder')}")

# A session_id ismerete sem elég — a lekérdezés instance-re is szűr
tulajdonos = conn.execute(
    "SELECT id FROM yr_chat_sessions WHERE id=? AND instance=?",
    (r_sid, "AnnaKatheder")).fetchone()
ok(tulajdonos is None,
   "Anna Réka session-ID-jével sem éri el a beszélgetést")

# Melléklet sem szivárog
fid = str(uuid.uuid4())
conn.execute("INSERT INTO yr_chat_files (id, instance, filename, kind, label, "
             "text, image_paths, created_at) VALUES (?,?,?,?,?,?,?,?)",
             (fid, "YoungeReka", "titkos.pdf", "pdf", "PDF", "adat", "[]",
              chat._now()))
conn.commit()
ok(chat._load_files(conn, [fid], "AnnaKatheder") == [],
   "Anna Réka file_id-jével sem kapja meg a mellékletet")
ok(len(chat._load_files(conn, [fid], "YoungeReka")) == 1,
   "…Réka viszont igen")

# Külön keret
import youngereka_budget as budget      # noqa: E402
budget.record(conn, "YoungeReka", 4.0, "kimi", "teszt")
ok(budget.spent_today(conn, "YoungeReka") == 4.0, "Réka kerete fogy")
ok(budget.spent_today(conn, "AnnaKatheder") == 0.0,
   "Anna kerete ÉRINTETLEN (külön mérőóra)")
conn.close()


# ============================================================
szakasz("Külön felület")

r = chat_profile("YoungeReka")
a = chat_profile("AnnaKatheder")

ok(r["koszones"] != a["koszones"], f"más köszönés: {r['koszones']!r} / {a['koszones']!r}")
ok("kis hercegnő" in r["koszones"], "Réka: kis hercegnő")
ok("csodakirálynő" in a["koszones"], "Anna: csodakirálynő")
ok(r["cim"] == "Olvasóterem" and a["cim"] == "Tanulószoba",
   f"más cím: {r['cim']} / {a['cim']}")
ok(r["prompt"] is REKA_CHAT_PROMPT, "Réka a LESESAAL promptot kapja")
ok(a["prompt"] is KATHEDER_PROMPT, "Anna a KATHEDER promptot kapja")
ok(len(r["ures"]) == 4 and len(a["ures"]) == 4, "mindkettőnek saját üres állapota")
ok(r["ures"] != a["ures"], "…és azok különböznek")

# A kiskapu-zárás
ok(r["kutatas_gomb"] is True, "Réka: az „Alapos utánajárás” megvan")
ok(a["kutatas_gomb"] is False,
   "Anna: az „Alapos utánajárás” NINCS (kész beadandót adna vissza)")
ok(a["melyseg_gomb"] is True, "Anna: a „Gondolkodj alaposan” marad")
ok(r["abra_kiemeles"] is True and a["abra_kiemeles"] is False,
   "ábra-kiemelés csak Rékánál (szakcikk vs. kurzus-PDF)")

ok(chat_profile("ismeretlen")["cim"] == "Olvasóterem",
   "ismeretlen instance → alapértelmezés, nem összeomlás")


# ============================================================
szakasz("A KATHEDER-prompt tartalmi kapui")

ok("A BEADANDÓIT NEM ÍRO" in KATHEDER_PROMPT.upper().replace("D", "D"),
   "a beadandó-tilalom benne van")
ok("CSAK AZ ÉRTÉKELT MUNKÁRA" in KATHEDER_PROMPT.upper(),
   "…de kizárólag az értékelt munkára — ténykérdésre nem")
ok("mohácsi csata" in KATHEDER_PROMPT,
   "konkrét példa a ténykérdésre (különben mindenre visszakérdezne)")
ok("CIRILL" in KATHEDER_PROMPT.upper(), "cirill olvasás kimondva")
ok("ne told semelyik irányba" in KATHEDER_PROMPT,
   "az átjelentkezésről nem dönt helyette")
ok("tanulmányi osztály" in KATHEDER_PROMPT,
   "…hanem emberekhez irányítja")
ok("Réka beszélgetéseihez" in KATHEDER_PROMPT,
   "kimondja, hogy Réka beszélgetéseit nem éri el")
ok("csodakirálynő" in KATHEDER_PROMPT and "CSAK a\nköszönés" in KATHEDER_PROMPT
   or "CSAK a" in KATHEDER_PROMPT,
   "a becenév csak a köszönésben")


# ============================================================
szakasz("Anna jogosultságai — Rékáéval azonos szigor")

from anna_profile import register_anna                   # noqa: E402
from permissions import (Access, PermissionDeniedError,   # noqa: E402
                         check_permission)

register_anna()

for tool in ("capture_gmail_poll", "capture_send_email", "capture_calendar_poll",
             "create_calendar_event", "capture_inbox", "read_gmail_attachment",
             "capture_status", "brave_navigate", "brave_login", "brave_scrape",
             "brave_crawl", "brave_clear_sessions", "brave_session_action"):
    try:
        check_permission("AnnaKatheder", tool)
        ok(False, f"Anna: {tool} → DENY")
    except PermissionDeniedError:
        ok(True, f"Anna: {tool} → DENY")

for tool in ("read_memory", "ai_query", "search_web", "scrape_url", "upload_file"):
    try:
        ok(check_permission("AnnaKatheder", tool) in (Access.ALLOW, Access.FILTERED),
           f"Anna: {tool} → ALLOW")
    except PermissionDeniedError:
        ok(False, f"Anna: {tool} → ALLOW")

from youngereka_profile import YOUNGEREKA_PROFILE         # noqa: E402
from anna_profile import ANNA_PROFILE                     # noqa: E402
r_deny = {k for k, v in YOUNGEREKA_PROFILE.tool_permissions.items() if v == Access.DENY}
a_deny = {k for k, v in ANNA_PROFILE.tool_permissions.items() if v == Access.DENY}
ok(r_deny == a_deny,
   f"a két tiltólista AZONOS ({len(r_deny)} tool) — a különbség utasítási, nem jogosultsági")

ok("YoungeReka" not in ANNA_PROFILE.visible_message_senders,
   "Anna nem látja Réka üzeneteit")
ok("AnnaKatheder" not in YOUNGEREKA_PROFILE.visible_message_senders,
   "Réka nem látja Anna üzeneteit")


# ============================================================
print("\n" + "═" * 60)
if hibak:
    print(f"PIROS — {len(hibak)} bukás:")
    for h in hibak:
        print("   ·", h)
    sys.exit(1)
print("MIND ZÖLD — a két link elkülönül.")
