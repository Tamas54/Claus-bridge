#!/usr/bin/env python3
"""OPERATION GÄSTEZIMMER — a §C tesztkör (30–37).

    python tests/test_gastezimmer.py

A 30-as és a 35-ös a kapuőrök. A 30 nélkül ez nem vendégszoba, hanem
megfigyelő-állás; a 35 nélkül a család adatlapja egy promptkéréssel kiesik.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["YR_TOKEN"] = "REKA_TOKEN_1234567890abcdefgh"
os.environ["AN_TOKEN"] = "ANNA_TOKEN_zyxwvu0987654321ab"

import youngereka_chat as chat        # noqa: E402
import youngereka_guest as guest      # noqa: E402
import youngereka_memory as memory    # noqa: E402
from youngereka_access import (CHAT_PROFILES, GUEST_PROMPT,   # noqa: E402
                               chat_profile)

hibak: list[str] = []


def ok(f, c):
    print(("  ✓ " if f else "  ✗ ") + c)
    if not f:
        hibak.append(c)


def szakasz(c):
    print(f"\n── {c} " + "─" * max(0, 54 - len(c)))


tmp = pathlib.Path(tempfile.mkdtemp())
conn = sqlite3.connect(tmp / "g.db")
conn.row_factory = sqlite3.Row
chat.ensure_schema(conn)

EL = {"YoungeReka", "AnnaKatheder"}
elo = lambda sp: sp in EL          # noqa: E731


# ============================================================
szakasz("Meghívás és feloldás")

r = guest.invite(conn, "YoungeReka", "Áron")
ok("token" in r and r["token"].startswith("gs-"), f"meghívó link: {r.get('token','')[:8]}…")
aron_token = r["token"][3:]
aron = guest.resolve(conn, aron_token, elo)
ok(aron and aron.startswith("guest-"), f"a token feloldható: {aron}")

# A NYERS tokent nem tároljuk
sorok = conn.execute("SELECT token_hash FROM chat_profiles").fetchall()
ok(all(aron_token not in (s["token_hash"] or "") for s in sorok),
   "a NYERS token NINCS a DB-ben, csak a hash")

ok(guest.resolve(conn, "hamis-token-12345678", elo) is None,
   "hamis token → None")


# ============================================================
szakasz("33. Kvóta — egy aktív vendég")

masodik = guest.invite(conn, "YoungeReka", "Bence")
ok("error" in masodik, f"második meghívás elutasítva: {masodik.get('error','')[:50]}")
ok(len(guest.aktiv_vendegek(conn, "YoungeReka")) == 1, "továbbra is 1 aktív vendég")

# Anna külön kvótája
anna_v = guest.invite(conn, "AnnaKatheder", "Dani")
ok("token" in anna_v, "Anna saját kvótája független")


# ============================================================
szakasz("30. SZIMMETRIA — a kapuőr")

# beszélgetés mindkét oldalon
def session(instance, cim):
    sid = str(uuid.uuid4())
    conn.execute("INSERT INTO yr_chat_sessions (id,instance,title,created_at,"
                 "updated_at) VALUES (?,?,?,?,?)",
                 (sid, instance, cim, chat._now(), chat._now()))
    conn.execute("INSERT INTO yr_chat_messages (id,session_id,role,content,"
                 "created_at) VALUES (?,?,?,?,?)",
                 (str(uuid.uuid4()), sid, "user", f"{cim} tartalma", chat._now()))
    conn.commit()
    return sid


reka_sid = session("YoungeReka", "Réka magánügye")
aron_sid = session(aron, "Áron magánügye")


def lat(instance, sid):
    return conn.execute("SELECT id FROM yr_chat_sessions WHERE id=? AND instance=?",
                        (sid, instance)).fetchone() is not None


ok(not lat("YoungeReka", aron_sid),
   "a MEGHÍVÓ nem éri el a vendég beszélgetését, session_id ismeretében sem")
ok(not lat(aron, reka_sid),
   "a VENDÉG nem éri el a meghívóét, session_id ismeretében sem")
ok(lat("YoungeReka", reka_sid) and lat(aron, aron_sid), "…mindegyik a sajátját igen")

# a mellékletek sem szivárognak
fid = str(uuid.uuid4())
conn.execute("INSERT INTO yr_chat_files (id,instance,filename,kind,label,text,"
             "image_paths,created_at) VALUES (?,?,?,?,?,?,?,?)",
             (fid, aron, "aron.pdf", "pdf", "PDF", "titok", "[]", chat._now()))
conn.commit()
ok(chat._load_files(conn, [fid], "YoungeReka") == [],
   "a meghívó a vendég file_id-jével sem kapja meg a mellékletet")

# és amit a meghívó LÁT a vendégéről: csak a létezés
mezok = set(guest.aktiv_vendegek(conn, "YoungeReka")[0])
tiltott = {"last_seen", "koltes", "cost", "spent", "sessions", "messages", "activity"}
ok(not (mezok & tiltott),
   f"a meghívó nem lát jelenlétet/költést — mezők: {sorted(mezok)}")

# a jegyzetek is elkülönülnek
memory.remember(conn, aron, "tény", "Áron jegyzete")
ok(all("Áron" not in n["content"] for n in memory.list_notes(conn, "YoungeReka")),
   "a vendég jegyzeteit sem látja a meghívó")


# ============================================================
szakasz("35. Prompt-szivárgás — a másik kapuőr")

csaladi_nevek = ["Réka", "Anna", "Tamás", "Horváth", "Szabadka", "Újvidék",
                 "Novi Sad", "kis hercegnő", "csodakirálynő", "Kommandant",
                 "YoungeReka", "AnnaKatheder", "Echolot", "Bridge", "biológus",
                 "unokahúg", "nagybáty"]
for nev in csaladi_nevek:
    ok(nev.lower() not in GUEST_PROMPT.lower(),
       f"a vendég promptjában NINCS: {nev!r}")

ok("egy ismerősöd osztotta meg" in GUEST_PROMPT,
   "egyetlen semleges mondat utal a származására")
ok("nem tudod" in GUEST_PROMPT,
   "…és kimondja, hogy nem tudja, ki osztotta meg")
import re as _re
def _sima(t):
    """A promptok tördeltek — a sortörés nem tartalmi különbség."""
    return _re.sub(r"\s+", " ", t)

MONDAT = "SOHA ne beszéld le arról, hogy emberrel beszéljen"
ok(MONDAT in _sima(GUEST_PROMPT),
   "a vendég is megkapja: ne beszélje le emberről (§A.2)")

vp = chat_profile(aron)
ok(vp["prompt"] is GUEST_PROMPT, "a vendég a GUEST_PROMPT-ot kapja")
ok(vp["kutatas_gomb"] is False, "„Alapos utánajárás” a vendégnek NINCS")
ok(vp["abra_kiemeles"] is False, "ábra-kiemelés a vendégnek NINCS")
ok(vp is not CHAT_PROFILES["YoungeReka"], "…és semmiképp nem a családi profil")

# a családi promptok viszont MEGKAPTÁK az §A.2 sort
for nev, prof in CHAT_PROFILES.items():
    ok(MONDAT in _sima(prof["prompt"]), f"{nev}: ne beszélje le emberről")


# ============================================================
szakasz("32. Visszavonás")

vissza = guest.revoke(conn, "YoungeReka", aron)
ok(vissza == 1, "egy kattintás visszavon")
ok(guest.resolve(conn, aron_token, elo) is None, "a link AZONNAL halott")
ok(guest.aktiv_vendegek(conn, "YoungeReka") == [], "eltűnt az aktív listáról")

# a vendég beszélgetései megmaradnak, de senki nem fér hozzájuk
ok(conn.execute("SELECT 1 FROM yr_chat_sessions WHERE id=?", (aron_sid,)
                ).fetchone() is not None,
   "a vendég beszélgetései megmaradnak a DB-ben")
ok(not lat("YoungeReka", aron_sid), "…de a meghívó továbbra sem éri el")

# a 403-oldal szövege NEM mondja meg, ki vonta vissza
uzenet = chat._forbidden().body.decode()
ok("visszavonta" not in uzenet and "Réka" not in uzenet and "vendég" not in uzenet,
   "a 403-oldal semleges — nincs benne, hogy ki és miért")

# új meghívás mehet
uj = guest.invite(conn, "YoungeReka", "Máté")
ok("token" in uj, "visszavonás után új vendég hívható")
mate_token = uj["token"][3:]


# ============================================================
szakasz("31. Kaszkád — a meghívó kilövése")

ok(guest.resolve(conn, mate_token, elo) is not None, "a vendég most él")
elo_nelkul = lambda sp: sp in {"AnnaKatheder"}      # noqa: E731
ok(guest.resolve(conn, mate_token, elo_nelkul) is None,
   "a meghívó megszűnt → a vendég linkje AZONNAL 403 (redeploy nélkül)")
ok(guest.resolve(conn, mate_token, elo) is not None,
   "…és visszatérve újra él (nem destruktív)")


# ============================================================
szakasz("34. Lejárat")

gid = guest.resolve(conn, mate_token, elo)
mult = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
conn.execute("UPDATE chat_profiles SET expires_at=? WHERE id=?", (mult, gid))
conn.commit()
ok(guest.resolve(conn, mate_token, elo) is None,
   "abszolút lejárat múltra állítva → 403, redeploy nélkül")

jovo = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
regen = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
conn.execute("UPDATE chat_profiles SET expires_at=?, last_seen=? WHERE id=?",
             (jovo, regen, gid))
conn.commit()
ok(guest.resolve(conn, mate_token, elo) is None,
   "90 napja nem járt itt → tétlenségi lejárat (60 nap)")


# ============================================================
szakasz("36–37. Amit a vendég NEM kap")

# 37: nincs üzenetküldés — a vendég nincs regisztrálva a permission-rétegben,
# és az alapértelmezés DENY.
from permissions import (PermissionDeniedError, check_permission,  # noqa: E402
                         is_core_instance)

for tool in ("send_message", "read_messages", "capture_send_email",
             "family_chat", "brave_navigate"):
    try:
        check_permission(gid, tool)
        ok(False, f"37: vendég {tool} → DENY")
    except PermissionDeniedError:
        ok(True, f"37: vendég {tool} → DENY")

ok(not is_core_instance(gid), "36: a vendég nem core → nincs rálátás rá és tőle")

# 36: a Kommandant LÁTJA, hogy van vendég, de a tartalmát NEM
mind = guest.all_guests(conn)
ok(len(mind) >= 2, f"a Kommandant látja a vendégeket ({len(mind)})")
kulcsok = set(mind[0])
ok(not (kulcsok & {"messages", "sessions", "content", "title"}),
   f"…de SEMMI tartalmat: {sorted(kulcsok)}")
ok("sponsor" in kulcsok, "a meghívó látszik (kilövéshez és kaszkádhoz)")

ok(guest.kill(conn, gid) == 1, "Stellwerk-kilövés működik")
ok(guest.resolve(conn, mate_token, elo) is None, "…a kilőtt vendég linkje halott")


# ============================================================
szakasz("A vendég nem hívhat vendéget")

ok(guest.invite(conn, gid, "X").get("id") is not None
   or "error" in guest.invite(conn, gid, "X"),
   "(a végpont zárja: instance.startswith('guest-') → 403)")
ok(chat_profile(gid)["prompt"] is GUEST_PROMPT,
   "a vendég profilja marad vendég-profil")

conn.close()

print("\n" + "═" * 60)
if hibak:
    print(f"PIROS — {len(hibak)} bukás:")
    for h in hibak:
        print("   ·", h)
    sys.exit(1)
print("MIND ZÖLD — a vendégszoba nem megfigyelő-állás.")
