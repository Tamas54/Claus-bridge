"""
Recipe/Workflow Layer Plugin — Operation Zahnrad Phase 2
Deklarativ workflow leirasok SQLite-ban. Az agentek is letrehozhatnak recipe-ket.
A rendszer deploy nelkul tanul uj kepessegeket.
"""

import json
import logging
import os
from datetime import datetime, timezone

# S-002 — a `required_tools` fail-closed ellenőrzése. Lásd recipe_health.py.
import recipe_health

logger = logging.getLogger("plugins.recipes")

__plugin_meta__ = {
    "name": "recipes",
    "version": "1.0.0",
    "description": "Recipe/Workflow rendszer -- deklarativ workflow-k letrehozasa, listazasa, vegrehajtasa",
}

# SQL for table creation
_INIT_SQL = """
CREATE TABLE IF NOT EXISTS pyramid_recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    required_tools TEXT DEFAULT '[]',
    prompt_template TEXT NOT NULL,
    created_by TEXT DEFAULT 'kommandant',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    enabled BOOLEAN DEFAULT 1,
    cron_schedule TEXT DEFAULT NULL,
    cron_model TEXT DEFAULT 'glm5',
    cron_enabled BOOLEAN DEFAULT 0,
    cron_delivery TEXT DEFAULT 'both',
    cron_last_run TIMESTAMP DEFAULT NULL,
    cron_deep_research INTEGER DEFAULT 0,
    cron_deep_thinking INTEGER DEFAULT 0,
    vertical TEXT DEFAULT NULL,
    vertical_command TEXT DEFAULT NULL
);
"""

_MIGRATE_SQL = """
ALTER TABLE pyramid_recipes ADD COLUMN cron_schedule TEXT DEFAULT NULL;
ALTER TABLE pyramid_recipes ADD COLUMN cron_model TEXT DEFAULT 'glm5';
ALTER TABLE pyramid_recipes ADD COLUMN cron_enabled BOOLEAN DEFAULT 0;
ALTER TABLE pyramid_recipes ADD COLUMN cron_delivery TEXT DEFAULT 'both';
ALTER TABLE pyramid_recipes ADD COLUMN cron_last_run TIMESTAMP DEFAULT NULL;
ALTER TABLE pyramid_recipes ADD COLUMN cron_deep_research INTEGER DEFAULT 0;
ALTER TABLE pyramid_recipes ADD COLUMN cron_deep_thinking INTEGER DEFAULT 0;
ALTER TABLE pyramid_recipes ADD COLUMN vertical TEXT DEFAULT NULL;
ALTER TABLE pyramid_recipes ADD COLUMN vertical_command TEXT DEFAULT NULL;
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# KI NYULHAT A RECEPTEKHEZ (2026-08-29)
# ============================================================
# A HIBA, AMIT EZ ZAR
# -------------------
# A `create/update/delete/execute_recipe` toolokon EGYETLEN jogosultsagi
# ellenorzes sem volt. Nem tiltas volt, hanem HIANY: a `_enforce()` a
# `server.py`-ban 34 helyen fut, de a recipe-plugin `deps` dictjeben nem
# volt semmilyen permission-hivhato, tehat a plugin nem is TUDTA meghivni.
#
# Kozben a `/mcp` vegpont hitelesitetlen (`FastMCP("Claus Bridge")`, semmi
# auth), a `/mcp/{xx}-{token}` ut pedig a `force_caller()`-rel `caller`+`auth`
# kwargot injektal MINDEN toolba — amit a FastMCP 3.2.4 pydantic-validacioja
# elutasit annal a toolnal, amelyik nem deklaralja oket (MERVE). Vagyis ez a
# negy tool ma KIZAROLAG a hitelesitetlen `/mcp` uton erheto el.
# A `caller: str = ""` + `auth: str = ""` parameterek hozzaadasa ezert nem
# szigoritas, hanem az elso alkalom, hogy a TOKENES ut egyaltalan mukodik.
#
# HAROM SZINT, SZANDEKOSAN NEM EGY
# --------------------------------
#  1. `_enforce(caller, verb)` mind a negy igen — pontosan az a hivas, ami a
#     server.py 34 helyen all. Nincs masodik mechanizmus.
#  2. ANONIM HIVO: az `execute_recipe` (es a `list_recipes`) atengedi, a
#     harom IRO ige NEM. Indok lent, `_gate()`.
#  3. TOKENES UT: csak a cron BEKAPCSOLASA. Az az egyetlen muvelet, ami utan
#     soha tobbe nincs ember a hurokban — a `caller` viszont szabad szoveg a
#     nyilt `/mcp`-n, tehat onmagaban nem bizonyit semmit (ugyanaz az ervelés,
#     mint az `oversight_open`-nel a server.py-ban).

#: „Nincs azonossag" ket irasmodja. Az `execute_recipe` a `caller`
#: alapertelmezeset `"unknown"`-nak deklaralja, a tobbi harom eddig sehogy —
#: mindketto ugyanazt jelenti: a hivo nem mondta meg, ki o.
ANONYMOUS_CALLERS = ("", "unknown")


def _is_anonymous(caller) -> bool:
    return str(caller or "").strip().lower() in ANONYMOUS_CALLERS


def _denied(reason_code: str, message: str, **extra) -> str:
    """A tiltas alakja megegyezik a `_enforce()`-eval: {error, status:denied}.

    A `reason_code` a tobblet — gepi ok, nem szabad szoveg (S-002 doktrina).
    """
    out = {"error": message, "status": "denied", "reason_code": reason_code}
    out.update(extra)
    return json.dumps(out, ensure_ascii=False)


def _cron_token_required() -> bool:
    """A cron-bekapcsolashoz kell-e a tokenes ut. Alapbol IGEN.

    Veszkapcsolo: `BRIDGE_RECIPE_CRON_REQUIRE_TOKEN=off` — a Railway env-jet
    csak a Kommandant irja, tehat ez operator-vezerelt, nem tamado-vezerelt.
    „Off" eseten is marad a 2. szint: NEVESITETT, profilban engedett hivo
    kell. Anonim utemezes SEMMILYEN beallitassal nem lehetseges.
    """
    return (os.environ.get("BRIDGE_RECIPE_CRON_REQUIRE_TOKEN", "on")
            .strip().lower() not in ("0", "off", "false", "no"))


def _gate(deps, verb: str, caller: str, auth: str, *,
          mutating: bool, require_token: bool = False) -> str | None:
    """None = mehet. Kulonben a tiltas JSON-je (ugyanaz az alak, mint `_enforce`)."""
    enforce = deps.get("enforce_func")

    # ── 1. Maga a kapu megvan-e? ────────────────────────────────────────
    # A hianyzo huzalozas az IRO igeknel tiltas, nem elnezes: ez az egyetlen
    # ok, amiert a lyuk letezett, es egy nema visszaeses pont ugyanigy nezne
    # ki. Az `execute_recipe` viszont atmegy — ott a huzalozatlan allapot ma
    # is a normalis (a ket meglevo teszt-fajl igy hivja), es a rontas
    # nagysagrenddel kisebb: mar bent levo, egyszer jovahagyott promptot
    # futtat, uj utasitast nem tud beirni.
    if not callable(enforce):
        if mutating:
            logger.error("Recipe %s DENIED: a permission-reteg nincs behuzalozva "
                         "(deps['enforce_func'] hianyzik)", verb)
            return _denied(
                "permission_layer_unwired",
                f"ZUGANG VERWEIGERT: {verb} — a jogosultsagi reteg nincs "
                "behuzalozva (deps['enforce_func']). Iro muvelet nem futhat "
                "ellenorizetlenul.")
        logger.warning("Recipe %s: nincs enforce_func a deps-ben — atengedve", verb)
        return None

    # ── 2. Anonim hivo ──────────────────────────────────────────────────
    # A `_enforce()` az ures callert ATENGEDI, es ezt SZANDEKOSAN NEM
    # valtoztatjuk meg globalisan: a 34 hivohelybol 25-nek `caller: str = ""`
    # az alapertelmezese, es az asztali hej (openmausbot/server/bridge.ts)
    # MERTEN `caller` NELKUL hivja mind a 47 olvaso toolt. Globalis zaras =
    # az egesz integracio elesik.
    # Az IRO igeknel viszont nincs olyan jogos hivo, akinek ne lenne neve:
    # itt az „ismeretlen" pontosan a tamado allapota.
    if _is_anonymous(caller):
        if mutating:
            logger.error("Recipe %s DENIED: anonim hivo", verb)
            return _denied(
                "anonymous_caller",
                f"ZUGANG VERWEIGERT: {verb} nevesitett hivot kovetel. "
                "Add meg a `caller`-t (regisztralt instance vagy core), vagy "
                "hivd a tokenes uton (/mcp/km-{token}), ami magatol beirja.",
                caller=str(caller or ""))
        return None

    # ── 3. A MEGLEVO profil-reteg ───────────────────────────────────────
    denied = enforce(caller, verb)
    if denied:
        logger.warning("Recipe %s DENIED profillal: caller=%s", verb, caller)
        return denied

    # ── 4. Tokenes ut — csak a cron bekapcsolasara ──────────────────────
    if require_token and _cron_token_required():
        authed = deps.get("authenticated_func")
        if not callable(authed):
            logger.error("Recipe %s DENIED: nincs authenticated_func a deps-ben", verb)
            return _denied(
                "permission_layer_unwired",
                f"ZUGANG VERWEIGERT: {verb} — a hitelesites-ellenorzo nincs "
                "behuzalozva (deps['authenticated_func']).")
        if not authed({"auth": auth}):
            logger.error("Recipe %s DENIED: cron-bekapcsolas token nelkul "
                         "(caller=%s)", verb, caller)
            return _denied(
                "token_path_required",
                "ZUGANG VERWEIGERT: cron-utemezes bekapcsolasahoz a TOKENES ut "
                "kell (/mcp/km-{token}). A `caller` a nyilt /mcp vegponton "
                "szabad szoveg — egy utemezett prompt viszont ember nelkul fut "
                "tovabb, hataridő nelkul. A cron KIKAPCSOLASA "
                "(cron_enabled=False / cron_schedule='none') nem igenyel tokent.",
                caller=caller)

    return None


# ============================================================
# CRON-KIFEJEZES: SZINTAXIS + GYAKORISAGI KORLAT
# ============================================================
# Az eddigi ellenorzes annyi volt, hogy „ot mezo". Ket kovetkezmenye volt:
#
#  1. `*/5 * * * *` atment, de a `server._cron_matches` a step-szintaxist nem
#     ismeri: `int("*/5")` → ValueError, ami a `_cron_loop` KOZOS try-agan
#     landolt. EGY ilyen sor MINDEN utemezett recipe-t megallitott, percenkent,
#     csendben. (A matcher azota nem dob — ez itt a beiras oldali fek.)
#  2. `* * * * *` egy dragan futo prompton napi 1440 agent-futas.

#: (lo, hi) mezonkent, a `_cron_matches` szemantikaja szerint.
CRON_FIELD_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
CRON_FIELD_NAMES = ("perc", "ora", "nap", "honap", "hetnap")

#: Ket egymast koveto futas kozott megkovetelt legkisebb tavolsag, percben.
DEFAULT_CRON_MIN_INTERVAL_MIN = 15


def cron_min_interval_min() -> int:
    try:
        return max(1, int(os.environ.get("BRIDGE_RECIPE_CRON_MIN_INTERVAL_MIN",
                                         DEFAULT_CRON_MIN_INTERVAL_MIN)))
    except (TypeError, ValueError):
        return DEFAULT_CRON_MIN_INTERVAL_MIN


def _cron_field_values(field: str, lo: int, hi: int):
    """A mezore illeszkedo ertekek halmaza, vagy None ha a mezo ervenytelen.

    PONTOSAN azt a nyelvtant fogadja el, amit a `server._cron_matches` KI TUD
    ertekelni — beleertve a furcsasagait is. Pl. a `1-3,5` azert ervenytelen,
    mert a matcher eloszor a `-`-t nezi, es `int("3,5")`-ot probalna.
    """
    field = (field or "").strip()
    if field == "*":
        return set(range(lo, hi + 1))
    if "-" in field:
        a, _, b = field.partition("-")
        if not (a.isdigit() and b.isdigit()):
            return None
        a, b = int(a), int(b)
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            return None
        return set(range(a, b + 1))
    out = set()
    for piece in field.split(","):
        piece = piece.strip()
        if not piece.isdigit():
            return None
        v = int(piece)
        if not (lo <= v <= hi):
            return None
        out.add(v)
    return out or None


def _min_gap_minutes(minutes: set, hours: set) -> int:
    """A ket legkozelebbi futas kozti perc egy napon belul.

    A nap/honap/hetnap mezoket SZANDEKOSAN figyelmen kivul hagyja: azok csak
    RITKITANAK (egesz napokat vesznek ki), a napon beluli legkisebb tavolsagot
    nem csokkentik. Amit igy szamolunk, az tehat also becsles — a korlat a
    biztonsagos iranyba teved.
    """
    fires = sorted(h * 60 + m for h in hours for m in minutes)
    if len(fires) < 2:
        return 24 * 60
    gaps = [b - a for a, b in zip(fires, fires[1:])]
    gaps.append(fires[0] + 24 * 60 - fires[-1])  # atfordulas ejfelen
    return min(gaps)


def validate_cron_schedule(schedule: str):
    """(ok: bool, hibauzenet: str). Ures hibauzenet, ha ok."""
    parts = (schedule or "").strip().split()
    if len(parts) != 5:
        return False, ("cron_schedule: 5 mezo kell (perc ora nap honap hetnap), "
                       "pl. '0 7 * * *'")

    values = []
    for field, (lo, hi), fname in zip(parts, CRON_FIELD_BOUNDS, CRON_FIELD_NAMES):
        vals = _cron_field_values(field, lo, hi)
        if vals is None:
            return False, (
                f"cron_schedule: a(z) '{fname}' mezo ervenytelen: {field!r}. "
                f"Megengedett: '*', szam ({lo}-{hi}), tartomany ('{lo}-{hi}') "
                f"vagy lista ('{lo},{hi}'). A lepes-szintaxist (pl. '*/15') ez a "
                "cron-motor NEM ismeri — ird ki felsorolassal ('0,15,30,45').")
        values.append(vals)

    floor = cron_min_interval_min()
    gap = _min_gap_minutes(values[0], values[1])
    if gap < floor:
        return False, (
            f"cron_schedule: ez a kifejezes {gap} percenkent futna, a "
            f"megengedett legsurubb {floor} perc. Egy utemezett recipe minden "
            "futasa agent-hivas (penz + kvota), es ember nelkul fut tovabb. "
            "Allitsd ritkabbra, vagy emeld a "
            "BRIDGE_RECIPE_CRON_MIN_INTERVAL_MIN kuszobot.")

    return True, ""


# ============================================================
# S-002 (2. fel) — A HIBAS FUTAS NEM "EXECUTED"
# ============================================================
# A `pyramid.task_dispatcher` mar ma is TIPIZALT hibat ad vissza
# (`unhandled_tool_call`), de a poller eddig csak a `content`-et olvasta, es
# minden befejezett taskra "executed"-et mondott — akkor is, ha az egyetlen
# kimenet egy "ERROR: UnhandledToolCall..." szoveg volt. A nema siker nem
# tunt el, csak feljebb koltozott egy reteggel.

#: Tipizalatlan, de bizonyitottan hibas tartalom-kezdetek (a regi, oszlop
#: nelkuli sorokra is mukodik).
ERROR_CONTENT_PREFIXES = ("ERROR:", "TIMEOUT", "(no response)")


def _row_get(row, key, default=""):
    """sqlite3.Row / dict egysegesen."""
    try:
        if hasattr(row, "keys"):
            return row[key] if key in row.keys() else default
        return row.get(key, default)
    except Exception:  # noqa: BLE001
        return default


def row_error_code(row) -> str:
    """Egy ai_task_results sor tipizalt hibakodja; "" ha ertekelheto eredmeny."""
    code = (_row_get(row, "error_code", "") or "").strip()
    if code:
        return code
    content = (_row_get(row, "content", "") or "").strip()
    if not content:
        return "empty_response"
    if content.startswith(ERROR_CONTENT_PREFIXES):
        return "error_response"
    return ""


def failed_agents(rows) -> dict:
    """{agent: hibakod} a hibas sorokra. Ures dict = minden sor ertekelheto."""
    out = {}
    for r in rows or []:
        code = row_error_code(r)
        if code:
            out[_row_get(r, "agent", "?")] = code
    return out


def _select_with_error_code(conn, sql_with, sql_without, params):
    """Ugyanaz a lekerdezes az `error_code` oszloppal, es nelkule.

    A migralatlan (regi) DB-n a nevesitett oszlop OperationalError-t dobna —
    az olvaso sosem torhet el emiatt.
    """
    try:
        return conn.execute(sql_with, params).fetchall()
    except Exception as e:  # noqa: BLE001 — sqlite3.OperationalError: no such column
        logger.debug("error_code column unavailable (%s) — legacy query", e)
        return conn.execute(sql_without, params).fetchall()


def _task_status(conn, task_id):
    rows = _select_with_error_code(
        conn,
        "SELECT status, COALESCE(error_code, '') AS error_code FROM ai_tasks WHERE id = ?",
        "SELECT status FROM ai_tasks WHERE id = ?",
        (task_id,),
    )
    return rows[0] if rows else None


def _result_rows(conn, task_id, agent=None, limit=None):
    where = "task_id = ?"
    params = [task_id]
    if agent is not None:
        where += " AND agent = ?"
        params.append(agent)
    tail = " ORDER BY id" + (f" LIMIT {int(limit)}" if limit else "")
    return _select_with_error_code(
        conn,
        f"SELECT agent, content, COALESCE(error_code, '') AS error_code "
        f"FROM ai_task_results WHERE {where}{tail}",
        f"SELECT agent, content FROM ai_task_results WHERE {where}{tail}",
        tuple(params),
    )


def _apply_template(template: str, context: dict) -> str:
    """Substitute {var} and {var:default} placeholders in template."""
    import re
    # Extract defaults: {var:default_value}
    defaults = {}
    def replace_defaults(match):
        var_name, default_val = match.group(1), match.group(2)
        defaults[var_name] = default_val
        return "{" + var_name + "}"
    template = re.sub(r'\{(\w+):([^}]+)\}', replace_defaults, template)
    # Merge: context overrides defaults
    merged = {**defaults, **context}
    try:
        return template.format(**merged)
    except KeyError:
        return template  # Missing vars → return as-is


def register_tools(app, deps):
    """Register recipe CRUD + execute MCP tools."""
    get_db = deps["get_db"]

    # Ensure table exists + migrate if needed
    conn = get_db()
    conn.executescript(_INIT_SQL)
    # Migrate: add cron columns if missing (safe to run multiple times)
    for stmt in _MIGRATE_SQL.strip().split("\n"):
        stmt = stmt.strip().rstrip(";")
        if not stmt:
            continue
        try:
            conn.execute(stmt)
        except Exception:
            pass  # Column already exists
    conn.commit()
    conn.close()
    logger.info("pyramid_recipes table ensured")

    # Seed default recipes if table is empty
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM pyramid_recipes").fetchone()[0]
    if count == 0:
        ts = _now()
        _seed_recipes = [
            ("daily_briefing",
             "Napi reggeli brief: email + naptar + taskok + hirek",
             '["gmail_poll", "calendar_poll", "list_tasks"]',
             "Keszits tomor napi briefet a Kommandantnak: "
             "1) Fontos emailek (felado + targy + urgencia) "
             "2) Mai naptar esemenyek "
             "3) Nyitott taskok "
             "4) Trending hirek. "
             "Maximum 300 szo, prioritas szerint rendezve.",
             "system", None, None),
            ("weekly_macro_report",
             "Heti makrogazdasagi osszefoglalo (vertikum: makro)",
             '["web_search"]',
             "(vertikum-vezérelt — runtime a vertical_plugins/makro/commands/makro-brief.md-t használja)",
             "system", "makro", "makro-brief"),
            ("weekly_geopolitics_brief",
             "Heti geopolitikai brief (vertikum: geopolitika)",
             '["web_search"]',
             "(vertikum-vezérelt — runtime a vertical_plugins/geopolitika/commands/heti-jelentes.md-t használja)",
             "system", "geopolitika", "heti-jelentes"),
        ]
        for name_s, desc, tools, prompt, by, vert, vert_cmd in _seed_recipes:
            conn.execute(
                "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, created_by, created_at, updated_at, "
                "cron_schedule, cron_model, cron_enabled, cron_delivery, vertical, vertical_command) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'glm5', 0, 'both', ?, ?)",
                (name_s, desc, tools, prompt, by, ts, ts, vert, vert_cmd),
            )
        conn.commit()
        logger.info("Seeded %d default recipes", len(_seed_recipes))
    conn.close()

    # Idempotens vertikum-migráció: a meglévő (production) Bridge-eken a
    # `weekly_macro_report` recipe már létezik, de vertikum nélkül. Itt
    # bekapcsoljuk vertikumosra. A `weekly_geopolitics_brief`-et beillesztjük,
    # ha még nincs. Mindkettő idempotens — minden plugin-load-on biztonságos.
    conn = get_db()
    try:
        ts2 = _now()
        # 1) weekly_macro_report → makro vertikum (csak ha még nincs vertikuma)
        cur = conn.execute(
            "UPDATE pyramid_recipes SET vertical='makro', vertical_command='makro-brief', updated_at=? "
            "WHERE name='weekly_macro_report' AND (vertical IS NULL OR vertical='')",
            (ts2,)
        )
        if cur.rowcount:
            logger.info("Vertikum-migration: weekly_macro_report → makro/makro-brief")
        # 2) weekly_geopolitics_brief INSERT (ha hiányzik)
        exists = conn.execute(
            "SELECT 1 FROM pyramid_recipes WHERE name='weekly_geopolitics_brief'"
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, "
                "created_by, created_at, updated_at, cron_model, cron_enabled, cron_delivery, "
                "vertical, vertical_command) "
                "VALUES (?, ?, ?, ?, 'system', ?, ?, 'glm5', 0, 'both', 'geopolitika', 'heti-jelentes')",
                (
                    "weekly_geopolitics_brief",
                    "Heti geopolitikai brief (vertikum: geopolitika)",
                    '["web_search"]',
                    "(vertikum-vezérelt — runtime a vertical_plugins/geopolitika/commands/heti-jelentes.md-t használja)",
                    ts2, ts2,
                ),
            )
            logger.info("Vertikum-seed: weekly_geopolitics_brief beillesztve")

        # 3) market_brief schedule rows (PLAN_20260531.md §4) — idempotent.
        #    These rows ONLY carry the cron SCHEDULE; the server's _cron_loop
        #    special-cases name-prefix "market_brief" → generate_market_brief()
        #    (strict §3 JSON + push to NOFX), NOT the generic ai_task path.
        #    Cron is interpreted in Europe/Budapest time. US open 09:00 ET ≈
        #    15:00 Budapest (CEST); early afternoon ~13:30 ET ≈ 19:30 Budapest.
        _mb_rows = [
            ("market_brief_morning",
             "NOFX stratégiai brief — reggel, US nyitás előtt (push /brief-re)",
             "0 15 * * 1-5"),
            ("market_brief_afternoon",
             "NOFX stratégiai brief — kora délután (push /brief-re)",
             "30 19 * * 1-5"),
        ]
        for mb_name, mb_desc, mb_cron in _mb_rows:
            mb_exists = conn.execute(
                "SELECT 1 FROM pyramid_recipes WHERE name=?", (mb_name,)
            ).fetchone()
            if not mb_exists:
                conn.execute(
                    "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, "
                    "created_by, created_at, updated_at, cron_schedule, cron_model, cron_enabled, cron_delivery) "
                    "VALUES (?, ?, '[]', ?, 'system', ?, ?, ?, 'deepseek', 1, 'none')",
                    (mb_name, mb_desc,
                     "(special-cased — runtime: feldwebel.market_brief.generate_market_brief)",
                     ts2, ts2, mb_cron),
                )
                logger.info("market_brief-seed: %s (cron=%s) beillesztve", mb_name, mb_cron)

        # 4) daily_press_review — pure fetch+store of the Echolot daily brief
        #    into the unified RAG (NO LLM). Special-cased in _cron_loop by name.
        #    06:00 Budapest daily; the row only carries the schedule.
        pr_exists = conn.execute(
            "SELECT 1 FROM pyramid_recipes WHERE name='daily_press_review'"
        ).fetchone()
        if not pr_exists:
            conn.execute(
                "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, "
                "created_by, created_at, updated_at, cron_schedule, cron_model, cron_enabled, cron_delivery) "
                "VALUES (?, ?, '[]', ?, 'system', ?, ?, ?, 'deepseek', 1, 'none')",
                ("daily_press_review",
                 "Napi sajtószemle (Echolot brief VILÁG+ITTHON) → RAG (no-LLM fetch+store)",
                 "(special-cased — runtime: plugins.daily_press_review.fetch_and_store_press_review)",
                 ts2, ts2, "0 6 * * *"),
            )
            logger.info("daily_press_review-seed: beillesztve (cron=0 6 * * *)")
        conn.commit()
    except Exception as me:
        logger.error("Vertikum-migration error: %s", me)
    finally:
        conn.close()

    @app.tool()
    async def create_recipe(name: str, description: str, prompt_template: str,
                            required_tools: str = "[]", created_by: str = "kommandant",
                            vertical: str = "", vertical_command: str = "",
                            caller: str = "", auth: str = "") -> str:
        """Create a new recipe (declarative workflow).

        Recipes are reusable workflow templates that any agent can execute.
        They live in SQLite — no code, no deploy needed.

        Args:
            name: Unique recipe name (e.g. 'daily_briefing', 'weekly_macro_report')
            description: Human-readable description of what the recipe does
            prompt_template: The full prompt that will be sent to the executing agent
                (ignored at runtime if vertical+vertical_command set)
            required_tools: JSON list of tool names needed (e.g. '["gmail_poll", "calendar_poll"]')
            created_by: Who created it (kommandant, web-claus, cli-claus, or agent name)
            vertical: Optional vertical_plugins folder name (e.g. 'makro', 'geopolitika').
                If set, the runtime loads vertical_plugins/<vertical>/commands/<vertical_command>.md
                as system prompt + skills/*.md concatenated, instead of using prompt_template.
            vertical_command: Required if vertical set. The commands/<name>.md basename.
            caller: Instance ID for the permission check. MANDATORY — a recipe is a
                prompt the system will later run on its own, so it may not be
                written by an unnamed caller. Filled automatically on the
                /mcp/{prefix}-{token} path.
            auth: Filled automatically on the token path. Not required here.

        NOTE: `created_by` is PROVENANCE (a label stored on the row), not
        identity — it is not forced by the token path. The permission check
        reads `caller`.
        """
        denied = _gate(deps, "create_recipe", caller, auth, mutating=True)
        if denied:
            return denied
        if (vertical and not vertical_command) or (vertical_command and not vertical):
            return json.dumps({"error": "vertical and vertical_command must be set together"})
        # Validate required_tools is valid JSON
        try:
            tools_list = json.loads(required_tools) if isinstance(required_tools, str) else required_tools
            if not isinstance(tools_list, list):
                return json.dumps({"error": "required_tools must be a JSON list"})
            required_tools = json.dumps(tools_list, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "required_tools must be valid JSON list"})

        conn = get_db()
        try:
            ts = _now()
            conn.execute(
                "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, created_by, created_at, updated_at, vertical, vertical_command) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, description, required_tools, prompt_template, created_by, ts, ts,
                 vertical or None, vertical_command or None),
            )
            conn.commit()
            recipe_id = conn.execute("SELECT id FROM pyramid_recipes WHERE name = ?", (name,)).fetchone()[0]
            conn.close()
            logger.info("Recipe created: %s (id=%d) by %s", name, recipe_id, created_by)
            return json.dumps({
                "status": "created",
                "recipe_id": recipe_id,
                "name": name,
                "message": f"Recipe '{name}' letrehozva. Futtatas: execute_recipe(name='{name}')",
            }, ensure_ascii=False)
        except Exception as e:
            conn.close()
            if "UNIQUE constraint" in str(e):
                return json.dumps({"error": f"Recipe '{name}' mar letezik. Hasznald az update_recipe-t."})
            return json.dumps({"error": str(e)})

    @app.tool()
    async def list_recipes(enabled_only: bool = True) -> str:
        """List available recipes (workflow templates).

        Args:
            enabled_only: If true, only show enabled recipes (default: true)
        """
        conn = get_db()
        q = ("SELECT id, name, description, required_tools, created_by, created_at, enabled, "
             "cron_schedule, cron_model, cron_enabled, cron_delivery, cron_last_run, "
             "cron_deep_research, cron_deep_thinking, vertical, vertical_command "
             "FROM pyramid_recipes ")
        if enabled_only:
            rows = conn.execute(q + "WHERE enabled = 1 ORDER BY name").fetchall()
        else:
            rows = conn.execute(q + "ORDER BY name").fetchall()
        conn.close()

        # S-002: a kihagyások ott legyenek, ahol az ember a recipe-ekre néz.
        # Egy log-sor nem látszik; a recipe kártyáján a "last_skip" igen.
        skips = recipe_health.last_skip_by_recipe(get_db)

        recipes = []
        for r in rows:
            entry = {
                "id": r[0], "name": r[1], "description": r[2],
                "required_tools": json.loads(r[3]) if r[3] else [],
                "created_by": r[4], "created_at": r[5],
                "enabled": bool(r[6]),
            }
            if r[1] in skips:
                entry["last_skip"] = skips[r[1]]
            if r[7]:  # cron_schedule exists
                entry["cron"] = {
                    "schedule": r[7], "model": r[8] or "glm5",
                    "enabled": bool(r[9]), "delivery": r[10] or "both",
                    "last_run": r[11],
                    "deep_research": bool(r[12]) if len(r) > 12 else False,
                    "deep_thinking": bool(r[13]) if len(r) > 13 else False,
                }
            if len(r) > 14 and r[14]:  # vertical
                entry["vertical"] = r[14]
                entry["vertical_command"] = r[15]
            recipes.append(entry)

        return json.dumps({"count": len(recipes), "recipes": recipes}, ensure_ascii=False)

    @app.tool()
    async def execute_recipe(name: str, context: str = "", model: str = "deepseek",
                             caller: str = "unknown", auth: str = "",
                             deep_research: bool = False, deep_thinking: bool = False) -> str:
        """Execute a recipe via ai_task — results appear on the dashboard, web search enabled.

        Single-agent (default): model='kimi', 'deepseek', or 'glm5' — fast, one agent works.
        Multi-agent: model='all' — all 3 agents work in parallel + synthesis. Slower but thorough.

        Args:
            name: Recipe name to execute
            context: Optional extra context to append to the prompt
            model: 'kimi', 'deepseek', 'glm5' for single agent, or 'all' for multi-agent broadcast
            caller: Who triggered the execution
            deep_research: Multi-round web_search loop with `[forrás N]` citations + URL list.
                Use for press review / fact-checking. ~3-5x slower per agent.
            deep_thinking: Enable explicit reasoning (Kimi thinking, V4-Pro effort=high).
                Combinable with deep_research (very slow, very thorough).
            auth: Filled automatically on the /mcp/{prefix}-{token} path.

        PERMISSION: a NAMED caller is checked against its profile (same
        `_enforce` as the 34 server.py call sites). An unnamed caller
        (`""` / `"unknown"`) still passes — deliberately. This verb cannot
        introduce a new instruction; it runs a prompt that is already in the
        table. Closing it would break the desktop shell, which calls the
        Bridge without a `caller` by design, and the two existing test files.
        """
        denied = _gate(deps, "execute_recipe", caller, auth, mutating=False)
        if denied:
            return denied
        conn = get_db()
        row = conn.execute(
            "SELECT id, name, description, required_tools, prompt_template, enabled, "
            "vertical, vertical_command "
            "FROM pyramid_recipes WHERE name = ?", (name,)
        ).fetchone()
        conn.close()

        if not row:
            return json.dumps({"error": f"Recipe '{name}' nem talalhato."})
        if not row[5]:
            return json.dumps({"error": f"Recipe '{name}' le van tiltva."})

        # ══ S-002: FAIL-CLOSED KAPU ═══════════════════════════════════
        # Ugyanaz a doktrína, mint a cron-úton (server.py _cron_loop): ha egy
        # KÖTELEZŐ tool bizonyítottan halott, nem gyártunk kimenetet belőle.
        # A kézi futtatás hangos, tipizált választ kap — nem egy briefet,
        # aminek a fele hiányzó bemenetből lett kitalálva.
        verdict = recipe_health.check_required_tools(name, row[3])
        if not verdict.ok:
            recipe_health.record_skip(get_db, verdict, trigger="manual")
            logger.error("Recipe SKIP (manual): %s — reason=%s dead=%s",
                         name, verdict.reason_code, verdict.dead_tools)
            return json.dumps(verdict.to_dict(), ensure_ascii=False)

        vertical = row[6]
        vertical_command = row[7]
        prompt = row[4]

        # ────────────────────────────────────────────────────────────
        # VERTIKUM-ROUTE: ha a recipe vertikum-vezérelt, megkerüljük a
        # flat prompt_template-et és a vertical_plugins/<vertical>/commands
        # + skills szabványos szerkezetét adjuk system promptként mind
        # a 3 agentnek (3-agent + szintézis = "verhetetlen, ha adatolt
        # és cikkelt", lásd feedback_bridge_3agent_synthesis memo).
        # ────────────────────────────────────────────────────────────
        if vertical and vertical_command:
            try:
                from vertical_plugins import load_command, load_skills
                cmd_md = load_command(vertical, vertical_command)
                skills_md = load_skills(vertical)
            except Exception as e:
                return json.dumps({"error": f"vertikum betöltés sikertelen: {vertical}/{vertical_command}: {e}"})

            vertical_system = (
                f"{cmd_md}\n\n"
                "═══ DOMAIN SKILLS (alkalmazd a workflow-ban) ═══\n\n"
                f"{skills_md}\n\n"
                "═══ END DOMAIN SKILLS ═══"
            )

            # Prefetch — vertikum-recipe-hez kötelező friss adatblokk
            factual_context = ""
            try:
                from plugins._recipe_prefetch import run_prefetch
                factual_context = await run_prefetch(name, deps) or ""
                if factual_context:
                    logger.info("Vertikum-recipe prefetch: %s (%d chars)", name, len(factual_context))
            except Exception as pe:
                logger.error("Vertikum-recipe prefetch failed for %s: %s", name, pe)

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            user_prompt = (
                f"[Mai dátum: {today}]\n\n"
                "Itt a friss adatblokk. Csinálj briefet a system promptban leírt struktúrában.\n\n"
                "=== FACTUAL CONTEXT ===\n"
                f"{factual_context if factual_context else '(prefetch nem futott vagy nem érhető el — csak a saját tudásodra hagyatkozhatsz, jelöld a hiányokat a záró szekcióban)'}\n"
                "=== END FACTUAL CONTEXT ===\n\n"
                "SZIGORÚ SZABÁLY: minden szám/állítás a fenti CONTEXT blokkból. "
                "Ha valami nincs ott, a 'Hiányzó / nem-elérhető források' záró szekcióba flaggeld."
            )

            ai_task_func = deps.get("ai_task_func")
            if not ai_task_func:
                return json.dumps({"error": "ai_task nem elerheto"})

            agents_to_use = ("kimi", "deepseek", "glm5") if model == "all" else (model,)
            agent_tasks_dict = {
                aid: {
                    "prompt": user_prompt,
                    "system_prompt": vertical_system,
                    "minimal": True,
                    "max_tokens": 4000,
                }
                for aid in agents_to_use
            }

            try:
                result_json = await ai_task_func(
                    title=f"Recipe: {name} (vertikum: {vertical})",
                    description=user_prompt,
                    assigned_by=caller or "recipe-system",
                    agent_tasks=json.dumps(agent_tasks_dict),
                    deep_research=deep_research,
                    deep_thinking=deep_thinking,
                )
                return result_json
            except Exception as e:
                logger.error("Vertikum-recipe execute failed for %s: %s", name, e)
                return json.dumps({"error": f"vertikum-recipe végrehajtás sikertelen: {e}"})

        # Template variables: if context is JSON dict, substitute {var} and {var:default}
        if context:
            try:
                ctx_dict = json.loads(context) if isinstance(context, str) else context
                if isinstance(ctx_dict, dict):
                    prompt = _apply_template(prompt, ctx_dict)
                else:
                    prompt += f"\n\nKONTEXTUS:\n{context}"
            except (json.JSONDecodeError, TypeError):
                prompt += f"\n\nKONTEXTUS:\n{context}"

        required_tools = json.loads(row[3]) if row[3] else []
        if required_tools:
            prompt += f"\n\nELERHETO TOOL-OK: {', '.join(required_tools)}"

        # Inject current date
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        prompt += f"\n\n[Mai datum: {today}. Az adatoknak FRISSNEK kell lenniuk!]"

        # ── Operation Kabare: pre-fetch real data in Python ──
        # A sub-agentek csak web_search-t kapnak. Ha van prefetcher a recipe-hez,
        # itt lehuzzuk a valodi adatokat (ECB, Yahoo, Calendar, Gmail, DB) es
        # CONTEXT blokkkent injektaljuk. A prompt template koti, hogy csak ezt
        # hasznalja — igy nem tud arfolyamot halucinalni.
        try:
            from plugins._recipe_prefetch import run_prefetch
            factual_context = await run_prefetch(name, deps)
            if factual_context:
                prompt += (
                    "\n\n=== FACTUAL CONTEXT (Python-ban lehuzott valos adatok) ===\n"
                    f"{factual_context}\n"
                    "=== END FACTUAL CONTEXT ===\n\n"
                    "SZIGORU SZABALY: MINDEN szamadatnak (arfolyam, ar, index, idopont, "
                    "nev, cim) a fenti FACTUAL CONTEXT blokkbol kell szarmaznia. "
                    "TILOS fejbol szamot, adatot, forrast irni. Ha valami nincs a "
                    "CONTEXT-ben, ird: 'adat nem elerheto'. SOHA ne talalj ki semmit."
                )
                logger.info("Recipe prefetch injected for: %s (%d chars)",
                            name, len(factual_context))
        except Exception as e:
            logger.error("Recipe prefetch injection failed for %s: %s", name, e)

        # Route through ai_task for dashboard visibility + web search
        ai_task_func = deps.get("ai_task_func")
        if not ai_task_func:
            return json.dumps({"error": "ai_task nem elerheto"})

        multi_agent = model == "all"

        try:
            if multi_agent:
                # BROADCAST: all 3 agents + synthesis
                result_json = await ai_task_func(
                    title=f"Recipe: {name} (multi-agent)",
                    description=prompt,
                    assigned_by=caller or "recipe-system",
                    deep_research=deep_research,
                    deep_thinking=deep_thinking,
                )
            else:
                # DISPATCH: single agent
                max_tokens = 16000 if model == "glm5" else 8000
                agent_tasks = json.dumps({model: {"prompt": prompt, "max_tokens": max_tokens}})
                result_json = await ai_task_func(
                    title=f"Recipe: {name}",
                    description=prompt,
                    assigned_by=caller or "recipe-system",
                    agent_tasks=agent_tasks,
                    deep_research=deep_research,
                    deep_thinking=deep_thinking,
                )

            result = json.loads(result_json)
            task_id = result.get("task_id")

            if not task_id:
                return result_json

            # Poll for results (ai_task runs in background thread)
            import asyncio
            max_wait = 300 if multi_agent else 180  # multi-agent gets more time
            for _ in range(max_wait // 2):
                await asyncio.sleep(2)
                conn = get_db()
                status = _task_status(conn, task_id)

                if status and status["status"] == "completed":
                    # Grab all results
                    rows = _result_rows(conn, task_id)
                    conn.close()

                    if multi_agent:
                        parts = {}
                        for r in rows:
                            parts[r["agent"]] = r["content"]
                        failed = failed_agents(rows)
                        if failed and len(failed) == len(parts):
                            # S-002 (2. fel): minden agent hibas volt — ez NEM
                            # vegrehajtott futas. A tipizalt ok a valaszban van,
                            # nem csak egy log-sorban.
                            logger.error("Recipe FAILED (all agents): %s (task #%d, %s)",
                                         name, task_id, failed)
                            return json.dumps({
                                "status": "failed",
                                "recipe": name,
                                "mode": "multi-agent",
                                "task_id": task_id,
                                "reason_code": "agent_run_failed",
                                "failed_agents": failed,
                                "message": ("Egyetlen agent sem adott ertekelheto eredmenyt — "
                                            "nem keszult kimenet."),
                            }, ensure_ascii=False)
                        logger.info("Recipe multi-agent: %s by %s (task #%d, %d agents)",
                                    name, caller, task_id, len(parts))
                        out = {
                            "status": "executed",
                            "recipe": name,
                            "mode": "multi-agent",
                            "task_id": task_id,
                            "agents": parts,
                        }
                        if failed:
                            out["failed_agents"] = failed  # reszleges hiba is lathato
                        return json.dumps(out, ensure_ascii=False)
                    else:
                        content = rows[0]["content"] if rows else "(nincs eredmeny)"
                        agent = rows[0]["agent"] if rows else model
                        failed = failed_agents(rows)
                        if not rows or (failed and len(failed) == len(rows)):
                            logger.error("Recipe FAILED: %s (task #%d, %s)", name, task_id, failed)
                            return json.dumps({
                                "status": "failed",
                                "recipe": name,
                                "model": agent,
                                "task_id": task_id,
                                "reason_code": "agent_run_failed",
                                "failed_agents": failed,
                                "message": ("A futas nem adott ertekelheto eredmenyt — "
                                            "nem keszult kimenet."),
                            }, ensure_ascii=False)
                        logger.info("Recipe executed: %s by %s via %s (task #%d)",
                                    name, caller, agent, task_id)
                        return json.dumps({
                            "status": "executed",
                            "recipe": name,
                            "model": agent,
                            "task_id": task_id,
                            "result": content,
                        }, ensure_ascii=False)

                if status and status["status"] == "failed":
                    code = status["error_code"] if "error_code" in status.keys() else ""
                    conn.close()
                    return json.dumps({
                        "status": "failed",
                        "recipe": name,
                        "task_id": task_id,
                        "reason_code": code or "task_failed",
                        "error": f"Recipe task #{task_id} failed",
                        "message": "A futas hibas volt — nem keszult kimenet.",
                    }, ensure_ascii=False)

                # For single-agent, check if our agent's result is already in
                if not multi_agent:
                    row2 = _result_rows(conn, task_id, agent=model, limit=1)
                    conn.close()
                    if row2:
                        # S-002 (2. fel): ez az ELSO-EREDMENY rovidzar korabban
                        # akkor is "executed"-et mondott, ha az egyetlen sor egy
                        # UnhandledToolCall hibaszoveg volt.
                        failed = failed_agents(row2)
                        if failed:
                            logger.error("Recipe FAILED (early row): %s (task #%d, %s)",
                                         name, task_id, failed)
                            return json.dumps({
                                "status": "failed",
                                "recipe": name,
                                "model": model,
                                "task_id": task_id,
                                "reason_code": "agent_run_failed",
                                "failed_agents": failed,
                                "message": ("A futas nem adott ertekelheto eredmenyt — "
                                            "nem keszult kimenet."),
                            }, ensure_ascii=False)
                        logger.info("Recipe executed: %s by %s via %s (task #%d)",
                                    name, caller, model, task_id)
                        return json.dumps({
                            "status": "executed",
                            "recipe": name,
                            "model": model,
                            "task_id": task_id,
                            "result": row2[0]["content"],
                        }, ensure_ascii=False)
                else:
                    conn.close()

            return json.dumps({"status": "running", "task_id": task_id,
                               "message": f"Recipe task #{task_id} meg fut. Eredmeny a dashboardon."})

        except Exception as e:
            logger.error("Recipe execution failed: %s — %s", name, e)
            return json.dumps({"error": f"Vegrehajtasi hiba: {e}"})

    @app.tool()
    async def update_recipe(name: str, description: str = "", prompt_template: str = "",
                            required_tools: str = "", enabled: bool = True,
                            cron_schedule: str = "", cron_model: str = "",
                            cron_enabled: bool = False, cron_delivery: str = "",
                            cron_deep_research: bool = False, cron_deep_thinking: bool = False,
                            vertical: str = "", vertical_command: str = "",
                            caller: str = "", auth: str = "") -> str:
        """Update an existing recipe. Supports cron scheduling.

        Args:
            name: Recipe name to update
            description: New description (empty = keep current)
            prompt_template: New prompt template (empty = keep current)
            required_tools: New tool list as JSON (empty = keep current)
            enabled: Enable/disable the recipe
            cron_schedule: Cron expression e.g. '0 7 * * *' (empty = keep current, 'none' = remove)
            cron_model: Which agent for cron: kimi/deepseek/glm5/all (empty = keep current)
            cron_enabled: Enable/disable cron scheduling
            cron_delivery: Where to send results: dashboard/telegram/both (empty = keep current)
            cron_deep_research: When the cron triggers, run with deep_research=True
                (multi-round web_search for the designated research-agent only).
                Default False. NOTE: this flag is always written explicitly (like
                cron_enabled), so pass the desired final state on every update.
            cron_deep_thinking: When the cron triggers, run with deep_thinking=True
                (Kimi thinking ON, V4-Pro reasoning_effort=high). Default False.
            caller: Instance ID for the permission check. MANDATORY.
            auth: Filled automatically on the /mcp/{prefix}-{token} path.
                REQUIRED to turn cron ON — see below.

        PERMISSION, two steps:
          * every update needs a NAMED caller allowed by its profile;
          * TURNING CRON ON additionally needs the token path. That single
            transition is the one after which no human is ever in the loop
            again, and `caller` is free text on the open /mcp endpoint.
            Turning cron OFF needs no token: a safety valve must never
            require a key.
        """
        # A cron BEKAPCSOLASA a szigorubb ag. A kikapcsolas nem az.
        # A `"none"` osszehasonlitas SZO SZERINT ugyanaz, mint lent a torlo
        # agban — kulonben a kapu es a termek kulon utra menne.
        cron_turning_on = bool(cron_enabled) and cron_schedule != "none"
        denied = _gate(deps, "update_recipe", caller, auth, mutating=True,
                       require_token=cron_turning_on)
        if denied:
            return denied

        conn = get_db()
        row = conn.execute("SELECT id FROM pyramid_recipes WHERE name = ?", (name,)).fetchone()
        if not row:
            conn.close()
            return json.dumps({"error": f"Recipe '{name}' nem talalhato."})

        updates = []
        params = []

        if description:
            updates.append("description = ?")
            params.append(description)
        if prompt_template:
            updates.append("prompt_template = ?")
            params.append(prompt_template)
        if required_tools:
            try:
                tools_list = json.loads(required_tools) if isinstance(required_tools, str) else required_tools
                updates.append("required_tools = ?")
                params.append(json.dumps(tools_list, ensure_ascii=False))
            except (json.JSONDecodeError, TypeError):
                conn.close()
                return json.dumps({"error": "required_tools must be valid JSON list"})

        updates.append("enabled = ?")
        params.append(1 if enabled else 0)

        # Cron fields
        if cron_schedule == "none":
            updates.append("cron_schedule = NULL")
            updates.append("cron_enabled = 0")
        elif cron_schedule:
            # Az „ot mezo" ellenorzes helyett teljes szintaxis + gyakorisagi
            # korlat. Lasd `validate_cron_schedule` a fajl elejen.
            ok, cron_err = validate_cron_schedule(cron_schedule)
            if not ok:
                conn.close()
                logger.warning("Recipe %s: cron_schedule elutasitva (%r): %s",
                               name, cron_schedule, cron_err)
                return json.dumps({"error": cron_err,
                                   "status": "rejected",
                                   "reason_code": "invalid_cron_schedule"},
                                  ensure_ascii=False)
            updates.append("cron_schedule = ?")
            params.append(cron_schedule.strip())
        if cron_model:
            updates.append("cron_model = ?")
            params.append(cron_model)
        # cron_enabled always set explicitly
        updates.append("cron_enabled = ?")
        params.append(1 if cron_enabled else 0)
        if cron_delivery:
            updates.append("cron_delivery = ?")
            params.append(cron_delivery)
        # cron_deep_research / cron_deep_thinking — always set explicitly,
        # same convention as cron_enabled.
        updates.append("cron_deep_research = ?")
        params.append(1 if cron_deep_research else 0)
        updates.append("cron_deep_thinking = ?")
        params.append(1 if cron_deep_thinking else 0)

        # Vertikum-mezők. "none" = NULL-re törlés, "" = nem érintjük, egyébként set.
        if vertical == "none":
            updates.append("vertical = NULL")
            updates.append("vertical_command = NULL")
        else:
            if vertical:
                updates.append("vertical = ?")
                params.append(vertical)
            if vertical_command:
                updates.append("vertical_command = ?")
                params.append(vertical_command)

        updates.append("updated_at = ?")
        params.append(_now())
        params.append(name)

        conn.execute(f"UPDATE pyramid_recipes SET {', '.join(updates)} WHERE name = ?", params)
        conn.commit()
        conn.close()

        cron_info = ""
        if cron_schedule and cron_schedule != "none" and cron_enabled:
            cron_info = f", cron={cron_schedule} ({cron_model or 'glm5'})"
        logger.info("Recipe updated: %s (enabled=%s%s)", name, enabled, cron_info)
        return json.dumps({"status": "updated", "name": name, "enabled": enabled,
                           "cron_enabled": cron_enabled, "cron_schedule": cron_schedule or None})

    @app.tool()
    async def delete_recipe(name: str, caller: str = "", auth: str = "") -> str:
        """Delete a recipe permanently.

        Args:
            name: Recipe name to delete
            caller: Instance ID for the permission check. MANDATORY.
            auth: Filled automatically on the /mcp/{prefix}-{token} path.

        PERMISSION: needs a NAMED caller allowed by its profile. No token
        requirement — deleting is loud and recoverable from a backup, unlike
        an unattended schedule, and the Kommandant's own client must keep
        working on the plain /mcp path.
        """
        denied = _gate(deps, "delete_recipe", caller, auth, mutating=True)
        if denied:
            return denied
        conn = get_db()
        row = conn.execute("SELECT id FROM pyramid_recipes WHERE name = ?", (name,)).fetchone()
        if not row:
            conn.close()
            return json.dumps({"error": f"Recipe '{name}' nem talalhato."})

        conn.execute("DELETE FROM pyramid_recipes WHERE name = ?", (name,))
        conn.commit()
        conn.close()
        logger.info("Recipe deleted: %s", name)
        return json.dumps({"status": "deleted", "name": name})
