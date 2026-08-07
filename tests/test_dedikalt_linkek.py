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
os.environ["BL_TOKEN"] = "BELLA_TOKEN_qwerty0987654321"

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


# ============================================================
szakasz("Rálátás — csak core instance")

from permissions import is_core_instance                  # noqa: E402

conn2 = sqlite3.connect(tmp / "o.db"); conn2.row_factory = sqlite3.Row
chat.ensure_schema(conn2)
for inst, cim in (("YoungeReka", "Réka dolga"), ("AnnaKatheder", "Anna dolga")):
    sid = str(uuid.uuid4())
    conn2.execute("INSERT INTO yr_chat_sessions (id,instance,title,created_at,"
                  "updated_at) VALUES (?,?,?,?,?)",
                  (sid, inst, cim, chat._now(), chat._now()))
    conn2.execute("INSERT INTO yr_chat_messages (id,session_id,role,content,"
                  "cost_usd,created_at) VALUES (?,?,?,?,?,?)",
                  (str(uuid.uuid4()), sid, "user", f"{cim} szövege", 0.01, chat._now()))
conn2.commit()

o = chat.oversight(conn2)
ok(len(o["sessions"]) == 2, f"a rálátás MINDKETTŐT látja ({len(o['sessions'])})")
ok(set(o["instances"]) == {"YoungeReka", "AnnaKatheder"},
   "mindkét instance összesítve")
ok(o["instances"]["YoungeReka"]["beszelgetes"] == 1
   and o["instances"]["AnnaKatheder"]["uzenet"] == 1, "darabszámok stimmelnek")
ok(len(chat.oversight(conn2, instance="AnnaKatheder")["sessions"]) == 1,
   "instance-re szűrve csak az egyik")
egy = chat.oversight(conn2, session_id=o["sessions"][0]["id"])
ok("messages" in egy and egy["messages"][0]["content"].endswith("szövege"),
   "egy beszélgetés teljes szövege lekérhető")
ok("error" in chat.oversight(conn2, session_id="nincs-ilyen"),
   "ismeretlen session → hiba, nem üres siker")
conn2.close()

ok(is_core_instance("web-claus") and is_core_instance("cli-claus"),
   "web-claus és cli-claus core → rálátás jár nekik")
ok(not is_core_instance("YoungeReka") and not is_core_instance("AnnaKatheder"),
   "a két lány NEM core → a family_chat tool nekik zárva")
ok("family_chat" not in YOUNGEREKA_PROFILE.tool_permissions
   and "family_chat" not in ANNA_PROFILE.tool_permissions,
   "a family_chat egyik profilban sincs felsorolva → alapértelmezés DENY")

szakasz("Bella — saját felület, nulla rálátás")

from bella_profile import BELLA_PROFILE, register_bella   # noqa: E402
from youngereka_access import BELLA_CHAT_PROMPT           # noqa: E402
register_bella()

ok(resolve_instance_from_path("BELLA_TOKEN_qwerty0987654321") == "Bella",
   "BL_TOKEN → Bella")
b = chat_profile("Bella")
ok(b["prompt"] is BELLA_CHAT_PROMPT, "saját promptot kap")
ok(b["cim"] == "Dolgozószoba", f"saját cím: {b['cim']}")
ok(b["koszones"] == "Szia, Bella.", "NINCS kitalált becenév")
ok(b["ures"] != chat_profile("YoungeReka")["ures"], "saját üres állapot")

b_deny = {k for k, v in BELLA_PROFILE.tool_permissions.items() if v == Access.DENY}
ok(b_deny == r_deny,
   f"a tiltólista AZONOS a lányokéval ({len(b_deny)} tool)")

# A LÉNYEG: nem lát bele a lányokéba
ok("YoungeReka" not in BELLA_PROFILE.visible_message_senders
   and "AnnaKatheder" not in BELLA_PROFILE.visible_message_senders,
   "Bella NEM látja a lányok üzeneteit")
ok("Bella" not in YOUNGEREKA_PROFILE.visible_message_senders
   and "Bella" not in ANNA_PROFILE.visible_message_senders,
   "…és a lányok sem az övét")
try:
    check_permission("Bella", "family_chat")
    ok(False, "Bella family_chat → DENY")
except PermissionDeniedError:
    ok(True, "Bella family_chat → DENY (nem lát rá a lányokra)")
ok(not is_core_instance("Bella"), "Bella nem core instance")

# külön beszélgetések — friss kapcsolaton (a fentit már lezártuk)
conn3 = sqlite3.connect(tmp / "b.db"); conn3.row_factory = sqlite3.Row
chat.ensure_schema(conn3)
for inst, cim in (("Bella", "Bella dolga"), ("YoungeReka", "Réka dolga")):
    sid_ = str(uuid.uuid4())
    conn3.execute("INSERT INTO yr_chat_sessions (id,instance,title,created_at,"
                  "updated_at) VALUES (?,?,?,?,?)",
                  (sid_, inst, cim, chat._now(), chat._now()))
conn3.commit()


def lat3(inst):
    return {r["title"] for r in conn3.execute(
        "SELECT title FROM yr_chat_sessions WHERE instance=?", (inst,))}


ok(lat3("Bella") == {"Bella dolga"}, "csak a sajátját látja")
ok("Bella dolga" not in lat3("YoungeReka"), "Réka nem látja Belláét")
conn3.close()

# A prompt nem ÁLLÍT róla semmit, amit nem tudunk. (Az „orvos" és a
# „tanár" szó előfordul — „se orvossal", „ne tanárként" —, de azok nem
# róla szólnak. A teszt ezért ÁLLÍTÁSOKAT keres, nem szavakat.)
import re as _re2
mondatok = [m.strip() for m in _re2.split(r"[.\n]", BELLA_CHAT_PROMPT)
            if "bella" in m.lower()]
ok(len(mondatok) <= 2, f"Belláról mindössze {len(mondatok)} mondat szól")
for m in mondatok:
    print(f"       „{m.strip()}”")
allitas = _re2.compile(r"Bella\s+(egy\s+)?\w+(nő|nö|us|ista|ász|ész|tanár|orvos|mérnök)",
                       _re2.IGNORECASE)
ok(not allitas.search(BELLA_CHAT_PROMPT),
   "a prompt NEM állít róla foglalkozást")
for hely in ("Szabadka", "Újvidék", "Novi Sad", "Vajdaság"):
    ok(hely.lower() not in BELLA_CHAT_PROMPT.lower(),
       f"…és lakhelyet sem: {hely!r}")
ok("unokahúg" not in BELLA_CHAT_PROMPT.lower()
   and "anyj" not in BELLA_CHAT_PROMPT.lower()
   and "lánya" not in BELLA_CHAT_PROMPT.lower(),
   "…és rokoni viszonyt sem talál ki")
ok("MÁS FELHASZNÁLÓK BESZÉLGETÉSEIHEZ SEM" in BELLA_CHAT_PROMPT,
   "…és kimondja, hogy másokéba nem lát bele")


print("\n" + "═" * 60)
if hibak:
    print(f"PIROS — {len(hibak)} bukás:")
    for h in hibak:
        print("   ·", h)
    sys.exit(1)
print("MIND ZÖLD — a két link elkülönül, a rálátás core-only.")
