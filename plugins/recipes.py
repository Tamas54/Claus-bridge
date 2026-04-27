"""
Recipe/Workflow Layer Plugin — Operation Zahnrad Phase 2
Deklarativ workflow leirasok SQLite-ban. Az agentek is letrehozhatnak recipe-ket.
A rendszer deploy nelkul tanul uj kepessegeket.
"""

import json
import logging
from datetime import datetime, timezone

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
    cron_deep_thinking INTEGER DEFAULT 0
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
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


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
             "system"),
            ("weekly_macro_report",
             "Heti makrogazdasagi osszefoglalo",
             '["web_search"]',
             "Keszits heti makrogazdasagi osszefoglalot: "
             "1) Magyar es EU GDP, inflacio, munkanelkuliseg legfrissebb adatai "
             "2) Heti fo gazdasagi hirek "
             "3) Szintezis es kitekintes, max 500 szo.",
             "system"),
        ]
        for name_s, desc, tools, prompt, by in _seed_recipes:
            conn.execute(
                "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, created_by, created_at, updated_at, "
                "cron_schedule, cron_model, cron_enabled, cron_delivery) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'glm5', 0, 'both')",
                (name_s, desc, tools, prompt, by, ts, ts),
            )
        conn.commit()
        logger.info("Seeded %d default recipes", len(_seed_recipes))
    conn.close()

    @app.tool()
    async def create_recipe(name: str, description: str, prompt_template: str,
                            required_tools: str = "[]", created_by: str = "kommandant") -> str:
        """Create a new recipe (declarative workflow).

        Recipes are reusable workflow templates that any agent can execute.
        They live in SQLite — no code, no deploy needed.

        Args:
            name: Unique recipe name (e.g. 'daily_briefing', 'weekly_macro_report')
            description: Human-readable description of what the recipe does
            prompt_template: The full prompt that will be sent to the executing agent
            required_tools: JSON list of tool names needed (e.g. '["gmail_poll", "calendar_poll"]')
            created_by: Who created it (kommandant, web-claus, cli-claus, or agent name)
        """
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
                "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, description, required_tools, prompt_template, created_by, ts, ts),
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
             "cron_schedule, cron_model, cron_enabled, cron_delivery, cron_last_run FROM pyramid_recipes ")
        if enabled_only:
            rows = conn.execute(q + "WHERE enabled = 1 ORDER BY name").fetchall()
        else:
            rows = conn.execute(q + "ORDER BY name").fetchall()
        conn.close()

        recipes = []
        for r in rows:
            entry = {
                "id": r[0], "name": r[1], "description": r[2],
                "required_tools": json.loads(r[3]) if r[3] else [],
                "created_by": r[4], "created_at": r[5],
                "enabled": bool(r[6]),
            }
            if r[7]:  # cron_schedule exists
                entry["cron"] = {
                    "schedule": r[7], "model": r[8] or "glm5",
                    "enabled": bool(r[9]), "delivery": r[10] or "both",
                    "last_run": r[11],
                }
            recipes.append(entry)

        return json.dumps({"count": len(recipes), "recipes": recipes}, ensure_ascii=False)

    @app.tool()
    async def execute_recipe(name: str, context: str = "", model: str = "deepseek",
                             caller: str = "unknown",
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
        """
        conn = get_db()
        row = conn.execute(
            "SELECT id, name, description, required_tools, prompt_template, enabled "
            "FROM pyramid_recipes WHERE name = ?", (name,)
        ).fetchone()
        conn.close()

        if not row:
            return json.dumps({"error": f"Recipe '{name}' nem talalhato."})
        if not row[5]:
            return json.dumps({"error": f"Recipe '{name}' le van tiltva."})

        prompt = row[4]

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
                status = conn.execute(
                    "SELECT status FROM ai_tasks WHERE id = ?", (task_id,)
                ).fetchone()

                if status and status["status"] == "completed":
                    # Grab all results
                    rows = conn.execute(
                        "SELECT agent, content FROM ai_task_results WHERE task_id = ? ORDER BY id",
                        (task_id,)
                    ).fetchall()
                    conn.close()

                    if multi_agent:
                        parts = {}
                        for r in rows:
                            parts[r["agent"]] = r["content"]
                        logger.info("Recipe multi-agent: %s by %s (task #%d, %d agents)",
                                    name, caller, task_id, len(parts))
                        return json.dumps({
                            "status": "executed",
                            "recipe": name,
                            "mode": "multi-agent",
                            "task_id": task_id,
                            "agents": parts,
                        }, ensure_ascii=False)
                    else:
                        content = rows[0]["content"] if rows else "(nincs eredmeny)"
                        agent = rows[0]["agent"] if rows else model
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
                    conn.close()
                    return json.dumps({"error": f"Recipe task #{task_id} failed"})

                # For single-agent, check if our agent's result is already in
                if not multi_agent:
                    row2 = conn.execute(
                        "SELECT content FROM ai_task_results WHERE task_id = ? AND agent = ? LIMIT 1",
                        (task_id, model)
                    ).fetchone()
                    conn.close()
                    if row2:
                        logger.info("Recipe executed: %s by %s via %s (task #%d)",
                                    name, caller, model, task_id)
                        return json.dumps({
                            "status": "executed",
                            "recipe": name,
                            "model": model,
                            "task_id": task_id,
                            "result": row2["content"],
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
                            cron_enabled: bool = False, cron_delivery: str = "") -> str:
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
        """
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
            parts = cron_schedule.strip().split()
            if len(parts) != 5:
                conn.close()
                return json.dumps({"error": "cron_schedule: 5 mezo kell (perc ora nap honap hetnap), pl. '0 7 * * *'"})
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
    async def delete_recipe(name: str) -> str:
        """Delete a recipe permanently.

        Args:
            name: Recipe name to delete
        """
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
