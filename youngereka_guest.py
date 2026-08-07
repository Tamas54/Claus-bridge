"""
OPERATION GÄSTEZIMMER — vendég-hozzáférés.
===========================================

Kommandant: „És hívhassanak meg — önálló linkkel — fiúkat, lányokat,
ha épp olyanjuk van."

A SZAVATOSSÁG ELVE
------------------
A meghívó szavatol a vendégért. Nem kell hozzá jóváhagyás — Réka és Anna
felnőttek —, de a vendég profilja a meghívóhoz KÖTVE jön létre, bármikor
visszavonható, és a meghívóval együtt hal.

A SZIMMETRIA-SZABÁLY — a legfontosabb pont
-------------------------------------------
A meghívó és a vendég közül EGYIK SEM lát bele a másik beszélgetéseibe.
Soha, semmilyen jogcímen. Réka VISSZAVONHATJA Áron hozzáférését, de EL
NEM OLVASHATJA — se beszélgetést, se jelenlétet, se költést.

Ezt nem lehet lazítani. Egy rendszer, ahol „a párod kap egy linket, amit
te felügyelsz", egyetlen elcsúszásra van attól, hogy kontroll-eszköz
legyen — és az sokkal gyakoribb kár, mint az, ami ellen az egészet
építjük.

Ezért a `sponsor` mező NEM AD OLVASÁSI JOGOT. Kizárólag három dologra
kell: a visszavonáshoz, a kvótához és a kaszkád-halálhoz. Ha valaha
olyan lekérdezést írsz, ami a `sponsor` alapján ad vissza vendég-adatot,
azzal ezt a szabályt szeged meg.

A NYERS TOKENT NEM TÁROLJUK
----------------------------
Csak a `token_hash`-t. A link EGYSZER jelenik meg a meghívónak; ha
elveszti, újat kell generálnia. Így a DB elszivárgása önmagában nem ad
belépést senkinek.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
import string
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("bridge.yr_guest")

#: Egy meghívónak EGY aktív vendége lehet. Új meghívás előtt a régit
#: vissza kell vonni — ez nem technikai korlát, hanem az, hogy a
#: „vendégszoba" egy szoba.
MAX_AKTIV_VENDEG = 1

NAPI_KERET_USD = 2.0        # a családi 5.0 helyett
TETLENSEGI_LEJARAT_NAP = 60
ABSZOLUT_LEJARAT_NAP = 180

TOKEN_HOSSZ = 32
PREFIX = "gs-"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """A vendég-profilok. TEXT uuid kulcs, TIMESTAMP — Postgres-kész.

    A családi profilok NEM ide kerülnek: azok env-tokenből élnek és
    élesben futnak. Egy működő linket nem migrálunk séma-egységesség
    kedvéért.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_profiles (
          id           TEXT PRIMARY KEY,
          kind         TEXT NOT NULL DEFAULT 'family',
          sponsor      TEXT,
          display_name TEXT,
          token_hash   TEXT,
          created_at   TIMESTAMP NOT NULL,
          expires_at   TIMESTAMP,
          last_seen    TIMESTAMP,
          revoked_at   TIMESTAMP
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_prof_hash "
                 "ON chat_profiles(token_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_prof_sponsor "
                 "ON chat_profiles(sponsor, revoked_at)")
    conn.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat()


def _hash(token: str) -> str:
    return hashlib.sha256(("yr-guest-v1|" + token).encode()).hexdigest()


def _uj_token() -> str:
    ab = string.ascii_letters + string.digits
    return "".join(secrets.choice(ab) for _ in range(TOKEN_HOSSZ))


# ============================================================
# MEGHÍVÁS
# ============================================================

def invite(conn, sponsor: str, display_name: str = "") -> dict:
    """Új vendég. Visszaadja a NYERS tokent — EGYSZER, itt.

    A hívó felelőssége megmutatni a meghívónak; utána már csak a hash
    marad meg, tehát senki (a Kommandant sem) nem tudja visszafejteni.
    """
    ensure_schema(conn)
    if aktiv_vendegek(conn, sponsor):
        return {"error": "Már van egy aktív vendéged. Előbb vond vissza, "
                         "aztán hívhatsz meg valaki mást."}

    token = _uj_token()
    vid = "guest-" + uuid.uuid4().hex[:12]
    most = _now()
    conn.execute(
        "INSERT INTO chat_profiles (id, kind, sponsor, display_name, "
        "token_hash, created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
        (vid, "guest", sponsor, (display_name or "").strip()[:60] or "Vendég",
         _hash(token), _iso(most),
         _iso(most + timedelta(days=ABSZOLUT_LEJARAT_NAP))))
    conn.commit()
    logger.info("vendég meghívva: %s ← %s", vid, sponsor)
    return {"id": vid, "token": PREFIX + token,
            "display_name": (display_name or "Vendég"),
            "expires_at": _iso(most + timedelta(days=ABSZOLUT_LEJARAT_NAP))}


def revoke(conn, sponsor: str, guest_id: str = "") -> int:
    """Visszavonás. CSAK a saját vendégét — a `sponsor` mindig szűr.

    A vendég NEM kap értesítést. A link egyszerűen nem él tovább,
    semleges szöveggel. Egy „X visszavonta a hozzáférésedet" üzenet
    konfrontációt gyárt — pont akkor, amikor a legkevésbé kell.
    """
    ensure_schema(conn)
    hol = "sponsor=? AND revoked_at IS NULL"
    param = [sponsor]
    if guest_id:
        hol += " AND id=?"
        param.append(guest_id)
    cur = conn.execute(f"UPDATE chat_profiles SET revoked_at=? WHERE {hol}",
                       [_iso(_now())] + param)
    conn.commit()
    if cur.rowcount:
        logger.info("vendég visszavonva: %s (%s)", guest_id or "mind", sponsor)
    return cur.rowcount


def aktiv_vendegek(conn, sponsor: str) -> list[dict]:
    """A meghívó SAJÁT vendégei — pusztán a létezésük.

    FIGYELEM: ez a függvény szándékosan NEM ad vissza semmit a vendég
    tevékenységéről: se beszélgetést, se utolsó belépést, se költést.
    A meghívó annyit lát, hogy a meghívás ÉL-E. Lásd a modul fejlécét.
    """
    ensure_schema(conn)
    return [{"id": r["id"], "display_name": r["display_name"],
             "created_at": r["created_at"], "expires_at": r["expires_at"]}
            for r in conn.execute(
                "SELECT id, display_name, created_at, expires_at "
                "FROM chat_profiles WHERE sponsor=? AND kind='guest' "
                "AND revoked_at IS NULL ORDER BY created_at DESC", (sponsor,))]


# ============================================================
# FELOLDÁS
# ============================================================

def resolve(conn, token: str, sponsor_el: callable) -> str | None:
    """`gs-` token → vendég instance-id, vagy None.

    Négy kapun kell átjutnia, és mindegyik önállóan zár:
      1. létezik és nincs visszavonva
      2. az abszolút lejárat nem telt le (180 nap)
      3. tétlenségi lejárat: 60 napja nem járt itt → vége
      4. KASZKÁD: a meghívó hozzáférése még él
    """
    if not token:
        return None
    ensure_schema(conn)
    sor = conn.execute(
        "SELECT * FROM chat_profiles WHERE token_hash=? AND kind='guest'",
        (_hash(token),)).fetchone()
    if not sor:
        return None
    if sor["revoked_at"]:
        return None

    most = _now()
    if sor["expires_at"] and _parse(sor["expires_at"]) < most:
        return None
    if sor["last_seen"]:
        utolso = _parse(sor["last_seen"])
        if utolso + timedelta(days=TETLENSEGI_LEJARAT_NAP) < most:
            return None

    # KASZKÁD: ha a meghívó hozzáférése megszűnt (token forgatva, profil
    # törölve), a vendégé vele együtt hal. Ez nem takarítási feladat,
    # hanem belépéskori ellenőrzés — így redeploy nélkül azonnal hat.
    if not sponsor_el(sor["sponsor"]):
        logger.info("vendég %s elutasítva: a meghívója (%s) már nem él",
                    sor["id"], sor["sponsor"])
        return None

    conn.execute("UPDATE chat_profiles SET last_seen=? WHERE id=?",
                 (_iso(most), sor["id"]))
    conn.commit()
    return sor["id"]


def _parse(s: str) -> datetime:
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def is_guest(conn, instance: str) -> bool:
    if not instance or not instance.startswith("guest-"):
        return False
    ensure_schema(conn)
    r = conn.execute("SELECT 1 FROM chat_profiles WHERE id=? AND kind='guest'",
                     (instance,)).fetchone()
    return r is not None


def guest_meta(conn, instance: str) -> dict | None:
    """A vendég saját metaadata (a Stellwerk-kilövéshez és a kerethez).

    NEM tartalmaz beszélgetés-tartalmat.
    """
    ensure_schema(conn)
    r = conn.execute("SELECT id, sponsor, display_name, created_at, "
                     "expires_at, last_seen, revoked_at FROM chat_profiles "
                     "WHERE id=? AND kind='guest'", (instance,)).fetchone()
    return dict(r) if r else None


def all_guests(conn) -> list[dict]:
    """Minden vendég, a Kommandantnak — LÉTEZÉS és állapot, tartalom nélkül.

    A spec szerint a Kommandant ÉRTESÜL a meghívásról és ki tudja lőni a
    vendéget, de olvasási jogot nem kap rá: a vészüveg hatálya a
    vendégekre NEM terjed ki (v2.2 §B.5). Ezért itt sincs se cím, se
    üzenet — csak az, hogy ki hívott meg kit és mikor.
    """
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT id, sponsor, display_name, created_at, expires_at, "
        "last_seen, revoked_at FROM chat_profiles WHERE kind='guest' "
        "ORDER BY created_at DESC")]


def kill(conn, guest_id: str) -> int:
    """Stellwerk-kilövés: a Kommandant bármelyik vendéget megszünteti."""
    ensure_schema(conn)
    cur = conn.execute("UPDATE chat_profiles SET revoked_at=? WHERE id=? "
                       "AND kind='guest' AND revoked_at IS NULL",
                       (_iso(_now()), guest_id))
    conn.commit()
    return cur.rowcount
