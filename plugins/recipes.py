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
    enabled BOOLEAN DEFAULT 1
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def register_tools(app, deps):
    """Register recipe CRUD + execute MCP tools."""
    get_db = deps["get_db"]

    # Ensure table exists
    conn = get_db()
    conn.executescript(_INIT_SQL)
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
                "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
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
        if enabled_only:
            rows = conn.execute(
                "SELECT id, name, description, required_tools, created_by, created_at, enabled "
                "FROM pyramid_recipes WHERE enabled = 1 ORDER BY name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, description, required_tools, created_by, created_at, enabled "
                "FROM pyramid_recipes ORDER BY name"
            ).fetchall()
        conn.close()

        recipes = []
        for r in rows:
            recipes.append({
                "id": r[0], "name": r[1], "description": r[2],
                "required_tools": json.loads(r[3]) if r[3] else [],
                "created_by": r[4], "created_at": r[5],
                "enabled": bool(r[6]),
            })

        return json.dumps({"count": len(recipes), "recipes": recipes}, ensure_ascii=False)

    @app.tool()
    async def execute_recipe(name: str, context: str = "", model: str = "deepseek",
                             caller: str = "unknown") -> str:
        """Execute a recipe — sends the prompt_template to an AI agent via ai_query.

        Args:
            name: Recipe name to execute
            context: Optional extra context to append to the prompt
            model: Which agent to use: 'kimi', 'deepseek', or 'glm5' (default: deepseek)
            caller: Who triggered the execution
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
        if context:
            prompt += f"\n\nKONTEXTUS:\n{context}"

        required_tools = json.loads(row[3]) if row[3] else []
        if required_tools:
            prompt += f"\n\nELERHETO TOOL-OK: {', '.join(required_tools)}"

        # Call ai_query via deps — but we need the actual function
        # Use the SiliconFlow API directly for independence
        sf_key = deps.get("siliconflow_api_key", "")
        sf_base = deps.get("siliconflow_base_url", "https://api.siliconflow.com/v1")
        sf_models = deps.get("siliconflow_models", {})

        if not sf_key:
            return json.dumps({"error": "SILICONFLOW_API_KEY not configured"})

        model_id = sf_models.get(model, model)

        import httpx
        try:
            async with httpx.AsyncClient(timeout=deps.get("siliconflow_timeout", 220)) as client:
                resp = await client.post(
                    f"{sf_base}/chat/completions",
                    headers={"Authorization": f"Bearer {sf_key}", "Content-Type": "application/json"},
                    json={
                        "model": model_id,
                        "messages": [
                            {"role": "system", "content": (
                                "Te a Claus multi-agent rendszer al-agentje vagy. "
                                "Egy recipe (workflow template) vegrehajtasat kaptad feladatul. "
                                "Magyarul valaszolj, lenyegre toroen de alaposan."
                            )},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.5,
                        "max_tokens": 8000,
                    },
                )
                data = json.loads(resp.text)

            if "error" in data:
                return json.dumps({"error": f"API error: {data['error']}"})

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})

            logger.info("Recipe executed: %s by %s via %s (%d tokens)",
                        name, caller, model, usage.get("completion_tokens", 0))

            return json.dumps({
                "status": "executed",
                "recipe": name,
                "model": model,
                "result": content,
                "tokens": {"prompt": usage.get("prompt_tokens", 0), "completion": usage.get("completion_tokens", 0)},
            }, ensure_ascii=False)

        except Exception as e:
            logger.error("Recipe execution failed: %s — %s", name, e)
            return json.dumps({"error": f"Vegrehajtasi hiba: {e}"})

    @app.tool()
    async def update_recipe(name: str, description: str = "", prompt_template: str = "",
                            required_tools: str = "", enabled: bool = True) -> str:
        """Update an existing recipe.

        Args:
            name: Recipe name to update
            description: New description (empty = keep current)
            prompt_template: New prompt template (empty = keep current)
            required_tools: New tool list as JSON (empty = keep current)
            enabled: Enable/disable the recipe
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
        updates.append("updated_at = ?")
        params.append(_now())
        params.append(name)

        conn.execute(f"UPDATE pyramid_recipes SET {', '.join(updates)} WHERE name = ?", params)
        conn.commit()
        conn.close()
        logger.info("Recipe updated: %s (enabled=%s)", name, enabled)
        return json.dumps({"status": "updated", "name": name, "enabled": enabled})

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
