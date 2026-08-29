"""
Recipe tool-health gate — bug S-002 ("fail-closed scheduled outputs").
=====================================================================

A `pyramid_recipes.required_tools` mező 2026-08-29-ig DEKLARÁLT, DE
HASZNÁLATLAN volt: a `daily_briefing` recipe `["gmail_poll",
"calendar_poll", "list_tasks"]`-t követelt, és 5,5 héten át minden reggel
lefutott ANÉLKÜL, hogy a Gmail vagy a Naptár élt volna (a Google OAuth
refresh token 2026-07-21-én visszavonásra került). Briefet gyártott a két
fő bemenete nélkül, és semmit nem jelzett.

Doktrína: **„jelentsd a hiányt, ne töltsd ki."**
Nincs kimenet jobb, mint hiányzó bemenetekből épített kimenet.

Tervezési döntések (szándékosan itt, nem a devlogban)
-----------------------------------------------------
1. **A kapu csak POZITÍV halál-bizonyítékra zár.** Három állapot van:
   HEALTHY / DEAD / UNKNOWN. Kizárólag a DEAD blokkol. Egy „nem tudom"
   próba SOSEM állítja meg az ütemezést — különben az első deploy után
   MINDEN recipe elnémulna (a `web_search`, a `list_tasks` stb. mögött
   nincs próba), és a javítás rosszabb lenne a hibánál.
2. **A UNKNOWN nem tűnik el.** A verdikt felsorolja a nem-próbázott
   toolokat is, hogy látszódjon, meddig ér a mérőeszköz.
3. **A tipizált ok gépi.** `SkipReason` enum → `reason_code` string;
   a szabad szöveg külön `message` mezőben van, sosem ő a döntés hordozója.
4. **A kihagyás nyoma tartós.** `recipe_skips` tábla (additív, új tábla) —
   egy log-sor a Railway-en 7 nap után eltűnik, egy DB-sor nem.
5. **Vészkapcsoló.** `BRIDGE_RECIPE_TOOL_GATE=off` kikapcsolja. Egy
   produkciós rendszerben a néma új kapu is kockázat; legyen kézi féke.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger("bridge.recipe_health")

GATE_ENV = "BRIDGE_RECIPE_TOOL_GATE"

#: Emberi magyarázat — a `reason_code` a gépi mező, ez csak kíséri.
DOCTRINE = "report the gap, don't fill it"


# ============================================================
# 1. TÍPUSOK
# ============================================================

class ToolHealth(Enum):
    """Egy required_tool állapota a futás pillanatában."""

    HEALTHY = "healthy"    # bizonyítottan él
    DEAD = "dead"          # bizonyítottan NEM él → blokkol
    UNKNOWN = "unknown"    # nincs próba / nem eldönthető → NEM blokkol


class SkipReason(Enum):
    """Tipizált, gépi ok arra, hogy egy ütemezett recipe nem adott kimenetet."""

    REQUIRED_TOOL_DEAD = "required_tool_dead"


@dataclass(frozen=True)
class ToolProbeResult:
    """Egy tool egyetlen egészség-próbájának eredménye."""

    tool: str
    state: ToolHealth
    detail: str = ""

    def to_dict(self) -> dict:
        return {"tool": self.tool, "state": self.state.value, "detail": self.detail}


@dataclass(frozen=True)
class GateVerdict:
    """A kapu döntése egy recipe-re. `ok=False` → NEM szabad kimenetet gyártani."""

    recipe: str
    ok: bool
    checked_at: str
    probes: tuple[ToolProbeResult, ...] = ()
    reason: Optional[SkipReason] = None

    def _by_state(self, state: ToolHealth) -> list[str]:
        return [p.tool for p in self.probes if p.state is state]

    @property
    def dead_tools(self) -> list[str]:
        return self._by_state(ToolHealth.DEAD)

    @property
    def unknown_tools(self) -> list[str]:
        return self._by_state(ToolHealth.UNKNOWN)

    @property
    def healthy_tools(self) -> list[str]:
        return self._by_state(ToolHealth.HEALTHY)

    @property
    def reason_code(self) -> str:
        return self.reason.value if self.reason else ""

    def message(self) -> str:
        """Egy mondat embernek — SOHA nem ez a döntés hordozója."""
        if self.ok:
            return f"'{self.recipe}': minden kötelező tool átment az ellenőrzésen."
        dead = ", ".join(self.dead_tools) or "?"
        return (
            f"'{self.recipe}' KIMARADT: a kötelező tool(ok) nem működnek — {dead}. "
            f"Nem készült kimenet ({DOCTRINE})."
        )

    def to_dict(self) -> dict:
        """Gépi alak. A hívók ezt teszik a válaszba / a skip-ledgerbe."""
        return {
            "status": "ok" if self.ok else "skipped",
            "recipe": self.recipe,
            "reason_code": self.reason_code,
            "checked_at": self.checked_at,
            "dead_tools": self.dead_tools,
            "unknown_tools": self.unknown_tools,
            "healthy_tools": self.healthy_tools,
            "probes": [p.to_dict() for p in self.probes],
            "doctrine": DOCTRINE,
            "message": self.message(),
        }


# ============================================================
# 2. PRÓBA-REGISZTER
# ============================================================

_PROBES: dict[str, Callable[[], ToolProbeResult]] = {}


def register_probe(tool_name: str, fn: Callable[[], ToolProbeResult]) -> None:
    """Egészség-próba bekötése egy required_tool névhez.

    A regisztráció a hívó dolga (a server.py köti be a Google-próbákat),
    így ez a modul importálható a server.py nélkül — teszthez, pluginból.
    """
    _PROBES[tool_name] = fn


def registered_probes() -> list[str]:
    return sorted(_PROBES)


def clear_probes() -> None:
    """Csak teszthez — a globális regiszter ürítése."""
    _PROBES.clear()


def probe(tool_name: str) -> ToolProbeResult:
    """Egy tool megpróbálása. Próba nélkül UNKNOWN; a próba hibája is UNKNOWN.

    Egy elhasalt MÉRŐESZKÖZ nem bizonyítja a mért dolog halálát — ezért
    a próba kivétele UNKNOWN, nem DEAD. (Máskülönben egy elgépelt próba
    az egész ütemezést leállítaná.)
    """
    fn = _PROBES.get(tool_name)
    if fn is None:
        return ToolProbeResult(tool_name, ToolHealth.UNKNOWN, "no_probe_registered")
    try:
        result = fn()
    except Exception as e:  # noqa: BLE001
        logger.error("Health probe raised for %s: %s", tool_name, e)
        return ToolProbeResult(
            tool_name, ToolHealth.UNKNOWN, f"probe_error: {type(e).__name__}: {e}"
        )
    if not isinstance(result, ToolProbeResult):
        logger.error("Health probe for %s returned %r, not ToolProbeResult", tool_name, result)
        return ToolProbeResult(tool_name, ToolHealth.UNKNOWN, "probe_contract_violation")
    # A verdikt MINDIG a recipe-ben deklarált néven beszél, akkor is, ha a
    # próba a mögöttes szolgáltatásról nevezte el magát (alias-próbák).
    return ToolProbeResult(tool_name, result.state, result.detail)


# ============================================================
# 3. A KAPU
# ============================================================

def gate_enabled() -> bool:
    """A kapu alapból BE van kapcsolva; `BRIDGE_RECIPE_TOOL_GATE=off` kikapcsolja."""
    raw = os.environ.get(GATE_ENV, "on").strip().lower()
    return raw not in ("off", "0", "false", "no", "disabled")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_required_tools(raw) -> list[str]:
    """A `required_tools` oszlop tolerálható beolvasása.

    A DB-ben JSON-lista szövegként él, de látott már NULL-t, üres stringet
    és (a régi sorokban) listát is. Bármi más → üres lista, mert egy
    értelmezhetetlen deklaráció NEM halál-bizonyíték.
    """
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        try:
            items = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("required_tools not valid JSON: %r", raw)
            return []
        if not isinstance(items, list):
            logger.warning("required_tools not a list: %r", raw)
            return []
    return [str(x).strip() for x in items if str(x).strip()]


def check_required_tools(recipe_name: str, required_tools) -> GateVerdict:
    """A kapu. `ok=False` → a hívó NEM gyárthat kimenetet.

    Csak DEAD blokkol. UNKNOWN átengedi (lásd a modul fejlécének 1. pontját).
    """
    tools = parse_required_tools(required_tools)
    checked_at = _now_iso()

    if not tools:
        return GateVerdict(recipe=recipe_name, ok=True, checked_at=checked_at)

    if not gate_enabled():
        logger.warning(
            "Recipe tool gate DISABLED via %s — '%s' runs unchecked", GATE_ENV, recipe_name
        )
        return GateVerdict(recipe=recipe_name, ok=True, checked_at=checked_at)

    probes = tuple(probe(t) for t in tools)
    dead = [p for p in probes if p.state is ToolHealth.DEAD]
    if dead:
        return GateVerdict(
            recipe=recipe_name,
            ok=False,
            checked_at=checked_at,
            probes=probes,
            reason=SkipReason.REQUIRED_TOOL_DEAD,
        )
    return GateVerdict(recipe=recipe_name, ok=True, checked_at=checked_at, probes=probes)


# ============================================================
# 4. SKIP-LEDGER — hogy a kihagyás ne csak egy log-sor legyen
# ============================================================

_SKIP_DDL = """
CREATE TABLE IF NOT EXISTS recipe_skips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    trigger TEXT NOT NULL DEFAULT 'cron',
    detail_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recipe_skips_occurred ON recipe_skips(occurred_at);
"""


def ensure_schema(conn) -> None:
    """Idempotens táblakészítés. Additív: új tábla, meglévőhöz nem nyúl."""
    conn.executescript(_SKIP_DDL)
    conn.commit()


def record_skip(get_db, verdict: GateVerdict, trigger: str = "cron") -> Optional[int]:
    """Kihagyás rögzítése. Sosem dob — a naplózás hibája nem ölheti meg a loopot."""
    try:
        conn = get_db()
        try:
            ensure_schema(conn)
            cur = conn.execute(
                "INSERT INTO recipe_skips (recipe, reason_code, trigger, detail_json, occurred_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    verdict.recipe,
                    verdict.reason_code,
                    trigger,
                    json.dumps(verdict.to_dict(), ensure_ascii=False),
                    verdict.checked_at,
                ),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.error("record_skip failed for %s: %s", verdict.recipe, e)
        return None


def recent_skips(get_db, hours: int = 24, limit: int = 20) -> list[dict]:
    """Az utolsó `hours` óra kihagyásai — a get_status / dashboard számára."""
    try:
        conn = get_db()
        try:
            # Az OLVASÓ nem futtat DDL-t: a `get_status` forró út, és a tábla
            # a bootkor (init_db) vagy az első `record_skip`-kor létrejön.
            # Ha még nincs, a SELECT dob, és üres listát adunk vissza.
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            rows = conn.execute(
                "SELECT recipe, reason_code, trigger, detail_json, occurred_at "
                "FROM recipe_skips WHERE occurred_at >= ? "
                "ORDER BY occurred_at DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.error("recent_skips failed: %s", e)
        return []

    out = []
    for r in rows:
        try:
            detail = json.loads(r["detail_json"])
        except (TypeError, ValueError, KeyError, IndexError):
            detail = {}
        out.append({
            "recipe": r["recipe"],
            "reason_code": r["reason_code"],
            "trigger": r["trigger"],
            "occurred_at": r["occurred_at"],
            "dead_tools": detail.get("dead_tools", []),
            "message": detail.get("message", ""),
        })
    return out


def last_skip_by_recipe(get_db) -> dict[str, dict]:
    """Recipe-nként a LEGUTÓBBI kihagyás — a `list_recipes` kártyáira."""
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT recipe, reason_code, occurred_at, detail_json FROM recipe_skips "
                "WHERE id IN (SELECT MAX(id) FROM recipe_skips GROUP BY recipe)"
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.error("last_skip_by_recipe failed: %s", e)
        return {}

    out: dict[str, dict] = {}
    for r in rows:
        try:
            detail = json.loads(r["detail_json"])
        except (TypeError, ValueError):
            detail = {}
        out[r["recipe"]] = {
            "reason_code": r["reason_code"],
            "occurred_at": r["occurred_at"],
            "dead_tools": detail.get("dead_tools", []),
        }
    return out


# ============================================================
# 5. PRÓBA-GYÁR a capture (Google) szolgáltatásokhoz
# ============================================================

def capture_service_probe(state: dict, service_key: str,
                          ok_key: str, error_key: str) -> Callable[[], ToolProbeResult]:
    """Próba egy `_capture_state`-ben élő Google szolgáltatásra.

    A puszta `service is not None` HAZUDHAT: a `_init_google_services` a
    `build()` eredményét ELŐBB írja be, mint hogy a `getProfile()` hívással
    hitelesítené — egy visszavont tokennél a mező beállítva maradhat egy
    halott klienssel. Ezért a próba a TÉNYLEGES hívások nyomát is nézi:

      * nincs szolgáltatás                       → DEAD (biztos)
      * az utolsó hívás HIBA volt (frissebb, mint az utolsó siker) → DEAD
      * volt már sikeres hívás                   → HEALTHY
      * van kliens, de még sosem hívtuk          → UNKNOWN (nem blokkol)
    """
    def _probe() -> ToolProbeResult:
        name = service_key
        if state.get(service_key) is None:
            return ToolProbeResult(name, ToolHealth.DEAD, "google_service_not_initialised")
        last_ok = state.get(ok_key)
        last_err = state.get(error_key)
        if last_err and (not last_ok or str(last_err) > str(last_ok)):
            return ToolProbeResult(
                name, ToolHealth.DEAD,
                f"last_call_failed_at={last_err}; last_success={last_ok or 'never'}",
            )
        if last_ok:
            return ToolProbeResult(name, ToolHealth.HEALTHY, f"last_success={last_ok}")
        return ToolProbeResult(name, ToolHealth.UNKNOWN, "service_present_but_never_exercised")

    return _probe
