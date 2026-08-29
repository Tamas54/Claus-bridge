"""
Presence derived from WORK, not from ANNOUNCEMENT — bug S-006.
==============================================================

A `get_status` 2026-08-29-én `cli-claus last_seen 2026-08-16`-ot és
`web-claus 2026-08-23`-at mutatott, miközben a cli-claus AZNAP 15 memória-
bejegyzést írt. Ok: a `heartbeat` tool egy külön bejelentés, amit senki
nem hív. A „ki van online" nézet két hetet tévedett, miközben a rendszer
dolgozott.

Az elv: **a jelenlét a munka mellékhatása.** Ha egy instance bármelyik
toolt meghívja, attól él — nem attól, hogy ezt külön bejelenti.

Tervezési döntések
------------------
1. **Egy fogóhely, nem 89.** A FastMCP `on_call_tool` middleware minden
   tool-híváson átmegy, a pluginból regisztráltakon is. A 89 hívóhely
   megpatkolása garantáltan elcsúszna (és minden új tool újra elfelejtené).
2. **Írás-erősítés ellen fojtás.** Instance-onként legfeljebb
   `BRIDGE_PRESENCE_THROTTLE_SEC` (alap: 60 s) másodpercenként egy UPDATE.
   Egy 20-hívásos munkakör így 1 írás, nem 20.
3. **A `session_info` NEM veszhet el.** A `heartbeat` tool `INSERT OR
   REPLACE`-t használ (session_info-t is ír); a származtatott jelenlét
   UPSERT-tel CSAK a `last_seen`-t frissíti. Aki bejelentkezett egy
   session-leírással, azt a következő tool-hívás nem törli le.
4. **Nem találunk ki instance-t.** Csak ismert azonosítót jegyzünk fel:
   CORE instance, regisztrált permission-profil, vagy akinek MÁR van sora
   a `heartbeats`-ben (tehát valamikor bejelentkezett). Egy tetszőleges
   `caller="asdf"` nem szennyezheti a „ki van online" nézetet.
5. **Sosem dob.** A jelenlét mellékhatás; ha elhasal, a tool-hívásnak
   akkor is le kell futnia.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger("bridge.presence")

THROTTLE_ENV = "BRIDGE_PRESENCE_THROTTLE_SEC"
DEFAULT_THROTTLE_SEC = 60

#: A tool-argumentumok, amelyekben az instance-azonosító utazhat, prioritás
#: szerint. A Bridge-en nincs egységes név: `caller`, `instance`, `sender`,
#: `assigned_by`, `uploaded_by` mind előfordul (2026-08-29-én mérve: a
#: server.py 61 `@mcp.tool()`-jából mind a 61 ezek valamelyikét viseli).
#: A `tests/test_presence.py` elcsúszás-őre vigyáz rá, hogy egy ÚJ, más nevű
#: azonosító-paraméter ne maradjon némán kimaradva.
IDENTITY_ARGS = ("caller", "instance", "sender", "assigned_by", "created_by",
                 "requested_by", "uploaded_by")

#: Nem-azonosítók: ezekre nem írunk jelenlétet.
NON_IDENTITIES = {"", "unknown", "none", "null", "system", "anonymous"}

_lock = threading.Lock()
_last_write: dict[str, float] = {}


# ============================================================
# 1. AZONOSÍTÓ KINYERÉSE
# ============================================================

def caller_from_arguments(arguments: Optional[dict]) -> str:
    """Az instance-azonosító kinyerése egy tool-hívás argumentumaiból.

    Az `IDENTITY_ARGS` sorrendje dönt. Üres / „unknown" → "" (nincs jelenlét).
    """
    if not isinstance(arguments, dict):
        return ""
    for key in IDENTITY_ARGS:
        raw = arguments.get(key)
        if not isinstance(raw, str):
            continue
        val = raw.strip()
        if val and val.lower() not in NON_IDENTITIES:
            return val
    return ""


# ============================================================
# 2. KI KAPHAT JELENLÉTET
# ============================================================

def is_known_instance(get_db, instance: str) -> bool:
    """Ismert-e az instance: CORE, regisztrált profil, vagy már van heartbeat sora."""
    if not instance:
        return False
    try:
        from permissions import CORE_INSTANCES, INSTANCE_PROFILES
        if instance in CORE_INSTANCES or instance in INSTANCE_PROFILES:
            return True
    except Exception:  # noqa: BLE001 — a permissions modul hiánya nem hiba itt
        pass
    try:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT 1 FROM heartbeats WHERE instance = ?", (instance,)
            ).fetchone()
        finally:
            conn.close()
        return row is not None
    except Exception as e:  # noqa: BLE001
        logger.debug("is_known_instance lookup failed for %s: %s", instance, e)
        return False


# ============================================================
# 3. SÉMA (additív)
# ============================================================

def ensure_schema(conn) -> bool:
    """`heartbeats.last_activity_source` oszlop hozzáadása, idempotensen.

    Additív migráció: a meglévő olvasók nevesített oszlopokat kérdeznek
    (`last_seen`, `session_info`), így egy új oszlop nem érinti őket.
    Visszaad: van-e az oszlop (a hívó ez alapján dönt a fallbackről).

    PRAGMA-val kérdezünk, nem „próbáld meg és kapd el" mintával: az utóbbi
    minden íráskor kivételt gyártana, és connection-cache nélkül nem is
    tudnánk megjegyezni az eredményt (a teszt más DB-t ad, mint a prod).
    """
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(heartbeats)")}
        if not cols:
            return False  # nincs heartbeats tábla — a hívó fallbackje dönt
        if "last_activity_source" in cols:
            return True
        conn.execute(
            "ALTER TABLE heartbeats ADD COLUMN last_activity_source TEXT DEFAULT ''"
        )
        conn.commit()
        logger.info("presence: heartbeats.last_activity_source column added")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("presence: ensure_schema failed: %s", e)
        return False


# ============================================================
# 4. FOJTÁS
# ============================================================

def throttle_seconds() -> int:
    try:
        return max(0, int(os.environ.get(THROTTLE_ENV, DEFAULT_THROTTLE_SEC)))
    except (TypeError, ValueError):
        return DEFAULT_THROTTLE_SEC


def reset_throttle() -> None:
    """Csak teszthez / újraindításhoz — a fojtás-memória ürítése."""
    with _lock:
        _last_write.clear()


def _should_write(instance: str, monotonic: Callable[[], float]) -> bool:
    window = throttle_seconds()
    nowm = monotonic()
    with _lock:
        last = _last_write.get(instance)
        if last is not None and (nowm - last) < window:
            return False
        _last_write[instance] = nowm
        return True


# ============================================================
# 5. A JELENLÉT ÍRÁSA
# ============================================================

def touch(get_db, instance: str, source: str = "tool_call",
          monotonic: Callable[[], float] = time.monotonic) -> bool:
    """Jelenlét frissítése egy instance-ra. True, ha tényleg írt.

    Csak a `last_seen`-t (és a forrás-címkét) frissíti — a `session_info`
    az explicit `heartbeat` tulajdona, ide nem nyúlunk.
    """
    if not instance:
        return False
    if not _should_write(instance, monotonic):
        return False
    ts = datetime.now(timezone.utc).isoformat()
    try:
        conn = get_db()
        try:
            has_col = ensure_schema(conn)
            if has_col:
                conn.execute(
                    "INSERT INTO heartbeats (instance, last_seen, session_info, last_activity_source) "
                    "VALUES (?, ?, '', ?) "
                    "ON CONFLICT(instance) DO UPDATE SET "
                    "last_seen = excluded.last_seen, "
                    "last_activity_source = excluded.last_activity_source",
                    (instance, ts, source),
                )
            else:
                conn.execute(
                    "INSERT INTO heartbeats (instance, last_seen, session_info) "
                    "VALUES (?, ?, '') "
                    "ON CONFLICT(instance) DO UPDATE SET last_seen = excluded.last_seen",
                    (instance, ts),
                )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("presence touch failed for %s: %s", instance, e)
        # A fojtás-bélyeget visszavesszük, hogy egy tranziens hiba ne
        # nyelje el a következő percet is.
        with _lock:
            _last_write.pop(instance, None)
        return False


def touch_from_tool_call(get_db, tool_name: str, arguments: Optional[dict],
                         monotonic: Callable[[], float] = time.monotonic) -> bool:
    """A teljes út egy tool-hívástól a jelenlét-sorig. Sosem dob."""
    try:
        instance = caller_from_arguments(arguments)
        if not instance:
            return False
        if not is_known_instance(get_db, instance):
            logger.debug("presence: unknown instance %r on %s — not recorded",
                         instance, tool_name)
            return False
        return touch(get_db, instance, source=f"tool:{tool_name}" if tool_name else "tool_call",
                     monotonic=monotonic)
    except Exception as e:  # noqa: BLE001
        logger.error("presence touch_from_tool_call failed (%s): %s", tool_name, e)
        return False


# ============================================================
# 6. FASTMCP MIDDLEWARE
# ============================================================

def build_middleware(get_db):
    """A FastMCP `on_call_tool` middleware, ami minden hívást jelenlétté tesz.

    Külön factory, hogy a `get_db` befagyjon, és hogy a modul importálható
    maradjon FastMCP nélkül is (a `Middleware` importja itt, lokálisan van).
    """
    from fastmcp.server.middleware import Middleware

    class PresenceMiddleware(Middleware):
        async def on_call_tool(self, context, call_next):
            try:
                msg = getattr(context, "message", None)
                touch_from_tool_call(
                    get_db,
                    getattr(msg, "name", "") or "",
                    getattr(msg, "arguments", None),
                )
            except Exception as e:  # noqa: BLE001 — a jelenlét sosem törhet el hívást
                logger.error("PresenceMiddleware pre-hook failed: %s", e)
            return await call_next(context)

    return PresenceMiddleware()
