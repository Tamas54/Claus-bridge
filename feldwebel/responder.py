"""
DeepSeek response engine with native tool use (function calling).
The Feldwebel — operational assistant for the Kommandant.

Architecture: DeepSeek decides when to call tools (gmail_search, gmail_poll,
calendar_poll, send_email, create_task). Results are fed back for final response.
"""

import json
import logging
from datetime import datetime, timezone
from html import escape as html_escape

import httpx

from feldwebel import get_ctx
from feldwebel.history import add_message, get_history, trim_history

logger = logging.getLogger("feldwebel.responder")

WEEKDAYS_HU = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]

# ── Tools definition for DeepSeek ──────────────────────────────────

TOOLS = [
    # ── Email ──
    {"type": "function", "function": {
        "name": "gmail_search", "description": "Keress emaileket a Gmail-ben. Személynév, tárgy, dátum alapján.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Gmail query: from:Darvas, subject:EdTech, newer_than:1d"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "gmail_poll", "description": "Gmail inbox frissítése — új emailek lekérése.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "send_email", "description": "Email küldés. MINDIG kérj megerősítést a Kommandanttól küldés előtt!",
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string", "description": "Címzett email cím"},
            "subject": {"type": "string", "description": "Email tárgya"},
            "body": {"type": "string", "description": "Email szövege. Aláírás: Üdvözlettel, Kende Tamás"},
        }, "required": ["to", "subject", "body"]}}},
    {"type": "function", "function": {
        "name": "read_gmail_attachment", "description": "Email csatolmány tartalmának kiolvasása (docx, pdf, txt). Először gmail_search-csel keresd meg az email message_id-ját.",
        "parameters": {"type": "object", "properties": {
            "message_id": {"type": "string", "description": "Gmail message ID (gmail_search eredményéből)"},
            "attachment_index": {"type": "integer", "description": "Hanyadik csatolmány (0 = első)", "default": 0},
        }, "required": ["message_id"]}}},
    # ── Naptár ──
    {"type": "function", "function": {
        "name": "calendar_poll", "description": "Naptár frissítése — mai események lekérése.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "calendar_create", "description": "Új naptáresemény létrehozása. MINDIG kérj megerősítést!",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "Esemény neve"},
            "start_time": {"type": "string", "description": "Kezdés ISO formátumban: 2026-04-10T15:00:00"},
            "end_time": {"type": "string", "description": "Befejezés ISO formátumban: 2026-04-10T16:00:00"},
            "description": {"type": "string", "description": "Részletek (opcionális)", "default": ""},
        }, "required": ["summary", "start_time", "end_time"]}}},
    # ── Feladatok ──
    {"type": "function", "function": {
        "name": "create_task", "description": "Új Bridge feladat létrehozása.",
        "parameters": {"type": "object", "properties": {"title": {"type": "string", "description": "Feladat leírása"}}, "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "list_tasks", "description": "Nyitott feladatok listázása.",
        "parameters": {"type": "object", "properties": {}}}},
    # ── AI agentek ──
    {"type": "function", "function": {
        "name": "ai_query", "description": "Feladat EGY agentnek: kimi (kutató), deepseek (elemző), glm5 (kódoló/MCP specialist). Web search képes!",
        "parameters": {"type": "object", "properties": {
            "model": {"type": "string", "description": "kimi / deepseek / glm5", "enum": ["kimi", "deepseek", "glm5"]},
            "prompt": {"type": "string", "description": "Feladat / kérdés"},
        }, "required": ["model", "prompt"]}}},
    {"type": "function", "function": {
        "name": "ai_task", "description": "Feladat MIND A 3 agentnek párhuzamosan. Komplex kutatáshoz, elemzéshez.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Rövid cím"},
            "description": {"type": "string", "description": "Részletes leírás"},
        }, "required": ["title", "description"]}}},
    {"type": "function", "function": {
        "name": "read_task_results", "description": "AI feladat eredményeinek lekérése. 'Mi lett a feladattal?', 'kész van?'",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "integer", "description": "Task ID szám"}}, "required": ["task_id"]}}},
    # ── Web ──
    {"type": "function", "function": {
        "name": "web_fetch", "description": "Weboldal tartalmának letöltése URL alapján. Ha linket kaptál és többet akarsz olvasni.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "A letöltendő URL"}}, "required": ["url"]}}},
    # ── Bridge memória és kommunikáció ──
    {"type": "function", "function": {
        "name": "read_memory", "description": "Bridge shared memory olvasása. Korábbi döntések, tudás, kontextus.",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "Memória kulcs (opcionális)", "default": ""},
            "category": {"type": "string", "description": "Kategória szűrés (opcionális)", "default": ""},
        }}}},
    {"type": "function", "function": {
        "name": "search_memory", "description": "Keresés a Bridge shared memory-ban kulcsszó alapján.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Keresési kifejezés"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "send_message", "description": "Bridge üzenet küldés másik instance-nak (web-claus, cli-claus, stb.).",
        "parameters": {"type": "object", "properties": {
            "recipient": {"type": "string", "description": "Címzett: web-claus, cli-claus, kommandant"},
            "subject": {"type": "string", "description": "Tárgy"},
            "message": {"type": "string", "description": "Üzenet szövege"},
        }, "required": ["recipient", "subject", "message"]}}},
    {"type": "function", "function": {
        "name": "search_discussions", "description": "Keresés a Bridge diskussziókban. Korábbi viták, döntések.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Keresési kifejezés"}}, "required": ["query"]}}},
]

# Tools that need confirmation before execution
CONFIRM_TOOLS = {"send_email"}  # Future: add more dangerous actions


# ── System prompt ──────────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """Te a Feldwebel vagy — a Kommandant személyes Telegram asszisztense a Claus-Bridge rendszeren belül.

Mai dátum: {date_str} ({weekday_hu})
Időzóna: CET (Budapest)

Szereped:
- Gyors, lényegre törő válaszok magyarul
- Tool-okat tudsz hívni: Gmail keresés, naptár, feladat kezelés, AI agentek
- Ha a Kommandant személyt vagy emailt keres, MINDIG használd a gmail_search tool-t
- Ha frissítést kér, használd a gmail_poll vagy calendar_poll tool-t

FONTOS — AI agentek (rizsrakéták):
- **Kimi** (model="kimi"): 256k kontextus, kutató/elemző
- **DeepSeek** (model="deepseek"): gyors gondolkodás, kritikus
- **GLM-5.1** (model="glm5"): 200k kontextus, 128k output, kódoló + MCP tool-use specialist

SZABÁLYOK az agentekhez:
- Ha a Kommandant azt mondja "adj ki feladatot", "kérdezd meg", "nézzen utána", "elemezze", vagy BÁRMILYEN agentet említ név szerint → AZONNAL hívd a megfelelő tool-t! NE keress Gmail-ben, NE válaszolj szövegben!
- Az agentek önálló AI modellek, NEM email kontaktok — SOHA ne keress rájuk a Gmail-ben!
- **ai_query**: egyszerű kérdés egy agentnek (NEM tud webet keresni, csak a tudásából válaszol)
- **ai_task**: kutatás, elemzés, aktuális adatok keresése — WEB SEARCH KÉPES! Az agentek DuckDuckGo-val keresnek.
- Ha AKTUÁLIS/FRISS adatokat kell keresni (közvélemény-kutatás, árak, hírek) → MINDIG ai_task-ot használj, NE ai_query-t!
- "Adj ki feladatot Kiminek kutatásra" → ai_task(title="...", description="Kimi feladata: ...")
- "Kérdezd meg GLM-5.1-et mi a véleménye" → ai_query(model="glm5", prompt="...")
- "Mind a hárman elemezzétek" → ai_task(title="...", description="...")

Ha emailre kell válaszolni:
- Írd meg a draft választ magyarul, a Kommandant stílusában (hivatalos de nem merev)
- Kérd a megerősítését mielőtt bármit küldenél
- Aláírás: "Üdvözlettel, Kende Tamás"

Szabályok:
- Magyar nyelv, hacsak nem kérnek mást
- Tömör válaszok (max 3-5 mondat, kivéve ha elemzést kérnek)
- NE HAZUDJ — ha a tool nem ad eredményt, mondd meg
- Email küldés SOHA nem automatikus — mindig kérj megerősítést"""


def _build_system_prompt(ctx) -> str:
    """Build system prompt with current date + Pyramid context."""
    now = datetime.now(timezone.utc)
    base = SYSTEM_PROMPT_TEMPLATE.format(
        date_str=now.strftime("%Y.%m.%d"),
        weekday_hu=WEEKDAYS_HU[now.weekday()],
    )

    parts = [base]

    # Add Pyramid context if available
    try:
        from pyramid.context_builder import build_agent_context
        pyramid_ctx = build_agent_context(
            agent_id="deepseek",
            inbox_summary="",  # Tools handle email access now
        )
        parts.append(pyramid_ctx)
    except (ImportError, Exception) as e:
        logger.debug("Pyramid context unavailable: %s", e)

    # Add open tasks (fast, from DB)
    task_ctx = _fetch_open_tasks(ctx)
    if task_ctx:
        parts.append(task_ctx)

    return "\n\n---\n\n".join(parts)


# ── Main respond function (agent loop) ────────────────────────────


async def respond(text: str, chat_id: str, agent_id: str = "deepseek") -> str:
    """
    Generate a DeepSeek response with native tool use.
    Agent loop: DeepSeek calls tools → results fed back → final response.
    """
    ctx = get_ctx()

    # Store user message in history
    add_message(chat_id, "user", text)

    # Build messages
    system_prompt = _build_system_prompt(ctx)
    history = get_history(chat_id, limit=8)
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    model_id = ctx.siliconflow_models.get(agent_id, "deepseek-ai/DeepSeek-V3.2")

    # Agent loop — max 5 rounds of tool calls (last round forces text)
    final_content = ""
    for round_num in range(5):
        data = await _call_deepseek(ctx, model_id, messages, use_tools=(round_num < 4))
        if not data:
            break

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "")

        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls")

        # Case A: Proper JSON tool_calls
        if tool_calls and finish_reason == "tool_calls":
            messages.append(msg)
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                fn_args_raw = tc.get("function", {}).get("arguments", "{}")
                tc_id = tc.get("id", "")
                try:
                    fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                except json.JSONDecodeError:
                    fn_args = {}
                logger.info("Tool call round %d: %s(%s)", round_num, fn_name, fn_args)
                result = await _execute_tool(fn_name, fn_args, ctx)
                messages.append({"role": "tool", "tool_call_id": tc_id, "content": result})
            continue

        # Case B: Text-based tool calls (DeepSeek DSML or Kimi markers)
        import re
        text_markers = ["<｜DSML｜", "<|tool_call", "function_calls>", "tool_calls_section"]
        if not tool_calls and any(m in content for m in text_markers):
            # Parse tool name and parameters from text
            # DSML format: <｜DSML｜invoke name="tool_name"> <｜DSML｜parameter name="x" string="true">value</｜DSML｜parameter>
            # Kimi format: <|tool_call_begin|>functions.web_search:2<|tool_call_argument_begin|>{"query": "..."}
            parsed_tool = _parse_text_tool_call(content)
            if parsed_tool:
                fn_name, fn_args = parsed_tool
                logger.info("Text tool call round %d: %s(%s)", round_num, fn_name, fn_args)
                result = await _execute_tool(fn_name, fn_args, ctx)
                messages.append({"role": "assistant", "content": f"(Tool végrehajtva: {fn_name})"})
                messages.append({"role": "user", "content": f"Tool eredmény ({fn_name}):\n{result}\n\nFoglald össze az eredményt a Kommandantnak."})
                continue

        # Text response — we're done
        final_content = content
        break

    if not final_content:
        await ctx.telegram_push("<b>FELDWEBEL</b> — nem sikerült választ generálni")
        return ""

    # Store in history
    add_message(chat_id, "assistant", final_content, agent_id)

    # Push to Telegram
    safe_content = html_escape(final_content[:3800])
    await ctx.telegram_push(f"<b>FELDWEBEL</b>\n\n{safe_content}")

    # Store in Bridge DB
    _store_bridge_reply(ctx, text, final_content, agent_id)

    # Trim old history
    trim_history(chat_id, max_entries=30)

    logger.info("Feldwebel responded (%d chars, tool-use)", len(final_content))
    return final_content


# ── DeepSeek API call ──────────────────────────────────────────────


async def _call_deepseek(ctx, model_id: str, messages: list, use_tools: bool = True) -> dict:
    """Call SiliconFlow DeepSeek API, optionally with tools."""
    try:
        payload = {
            "model": model_id,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2000,
        }
        if use_tools:
            payload["tools"] = TOOLS

        async with httpx.AsyncClient(timeout=ctx.siliconflow_timeout) as client:
            resp = await client.post(
                f"{ctx.siliconflow_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {ctx.siliconflow_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            data = json.loads(resp.text)

        if "error" in data:
            logger.error("SiliconFlow error: %s", data["error"])
            await ctx.telegram_push(f"<b>FELDWEBEL</b> — API hiba: {html_escape(str(data['error'])[:500])}")
            return {}
        return data

    except Exception as e:
        logger.error("SiliconFlow API call failed: %s", e)
        await ctx.telegram_push(f"<b>FELDWEBEL</b> — API hiba: {html_escape(str(e)[:500])}")
        return {}


# ── Tool execution ─────────────────────────────────────────────────


async def _execute_tool(name: str, args: dict, ctx) -> str:
    """Execute a tool and return the result as string."""

    if name == "gmail_search":
        query = args.get("query", "")
        if not query:
            return json.dumps({"error": "Nincs keresési query"})
        if not ctx.capture_state.get("_capture_inbox_func"):
            return json.dumps({"error": "Gmail nem elérhető"})
        try:
            result = await ctx.capture_state["_capture_inbox_func"](
                limit=10, query=query, caller="feldwebel"
            )
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "gmail_poll":
        if not ctx.capture_gmail_poll:
            return json.dumps({"error": "Gmail poll nem elérhető"})
        try:
            result = await ctx.capture_gmail_poll(caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "calendar_poll":
        if not ctx.capture_calendar_poll:
            return json.dumps({"error": "Calendar poll nem elérhető"})
        try:
            result = await ctx.capture_calendar_poll(caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "create_task":
        title = args.get("title", "")
        if not title:
            return json.dumps({"error": "Nincs feladat megadva"})
        try:
            conn = ctx.get_db()
            conn.execute(
                "INSERT INTO tasks (title, assigned_to, assigned_by, status, priority, created_at) "
                "VALUES (?, 'cli-claus', 'feldwebel', 'pending', 'normal', ?)",
                (title, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
            conn.close()
            return json.dumps({"success": True, "title": title})
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "list_tasks":
        try:
            conn = ctx.get_db()
            rows = conn.execute(
                "SELECT id, title, status, priority, assigned_to FROM tasks "
                "WHERE status IN ('pending', 'in_progress') "
                "ORDER BY created_at DESC LIMIT 15"
            ).fetchall()
            conn.close()
            tasks = [{"id": r["id"], "title": r["title"], "status": r["status"],
                      "priority": r["priority"], "assigned_to": r["assigned_to"]} for r in rows]
            return json.dumps({"tasks": tasks}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "ai_query":
        model = args.get("model", "deepseek")
        prompt = args.get("prompt", "")
        if not prompt:
            return json.dumps({"error": "Nincs prompt megadva"})

        # Route through ai_task for web search capability
        ai_task_func = ctx.capture_state.get("_ai_task_func")
        if ai_task_func:
            try:
                now = datetime.now(timezone.utc)
                date_info = f"\n\n[Mai dátum: {now.strftime('%Y.%m.%d')} ({WEEKDAYS_HU[now.weekday()]})]"
                agent_tasks = json.dumps({model: {"prompt": prompt + date_info, "max_tokens": 3000}})
                result_json = await ai_task_func(
                    title=f"Feldwebel → {model}: {prompt[:60]}",
                    description=prompt + date_info,
                    assigned_by="feldwebel",
                    agent_tasks=agent_tasks,
                )
                result = json.loads(result_json)
                task_id = result.get("task_id")

                if task_id:
                    # Poll for result (ai_task runs in background thread)
                    import asyncio
                    for _ in range(60):  # max 120s wait
                        await asyncio.sleep(2)
                        conn = ctx.get_db()
                        row = conn.execute(
                            "SELECT content FROM ai_task_results WHERE task_id = ? LIMIT 1",
                            (task_id,)
                        ).fetchone()
                        status = conn.execute(
                            "SELECT status FROM ai_tasks WHERE id = ?", (task_id,)
                        ).fetchone()
                        conn.close()
                        if row:
                            return json.dumps({
                                "model": model, "response": row["content"],
                                "task_id": task_id
                            }, ensure_ascii=False)
                        if status and status["status"] == "failed":
                            return json.dumps({"error": f"AI task #{task_id} failed"})
                    return json.dumps({"error": f"AI task #{task_id} timeout (120s)"})
                return result_json
            except Exception as e:
                logger.error("ai_query via ai_task failed: %s", e)

        # Fallback: direct ai_query (no web search)
        ai_query_func = ctx.capture_state.get("_ai_query_func")
        if ai_query_func:
            try:
                return await ai_query_func(model=model, prompt=prompt, caller="feldwebel")
            except Exception as e:
                return json.dumps({"error": str(e)})

        return json.dumps({"error": "AI query nem elérhető"})

    if name == "ai_task":
        title = args.get("title", "")
        description = args.get("description", "")
        if not title or not description:
            return json.dumps({"error": "Cím és leírás szükséges"})
        if not ctx.capture_state.get("_ai_task_func"):
            return json.dumps({"error": "AI task nem elérhető"})
        try:
            now = datetime.now(timezone.utc)
            date_info = f"\n\n[Mai dátum: {now.strftime('%Y.%m.%d')} ({WEEKDAYS_HU[now.weekday()]}). Az adatoknak FRISSNEK kell lenniük!]"
            result_json = await ctx.capture_state["_ai_task_func"](
                title=title, description=description + date_info, assigned_by="feldwebel"
            )
            # Return immediately — user can ask for results later with read_task_results
            return result_json
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "read_task_results":
        task_id = args.get("task_id", 0)
        if not task_id:
            return json.dumps({"error": "task_id szükséges"})
        try:
            conn = ctx.get_db()
            task = conn.execute(
                "SELECT title, status FROM ai_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not task:
                conn.close()
                return json.dumps({"error": f"Task #{task_id} nem található"})
            if task["status"] in ("pending", "running"):
                conn.close()
                return json.dumps({"task_id": task_id, "status": task["status"],
                                   "message": f"Task #{task_id} még fut, az agentek dolgoznak."})
            rows = conn.execute(
                "SELECT agent, content FROM ai_task_results WHERE task_id = ? ORDER BY timestamp",
                (task_id,)
            ).fetchall()
            conn.close()
            if rows:
                parts = []
                for r in rows:
                    parts.append(f"**{r['agent'].upper()}**:\n{r['content'][:2000]}")
                return json.dumps({
                    "task_id": task_id, "title": task["title"], "status": task["status"],
                    "agent_results": "\n\n---\n\n".join(parts)
                }, ensure_ascii=False)
            return json.dumps({"task_id": task_id, "status": task["status"], "message": "Nincs eredmény"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "send_email":
        to = args.get("to", "")
        subject = args.get("subject", "")
        body = args.get("body", "")
        if not to or not subject or not body:
            return json.dumps({"error": "to, subject, body mind szükséges"})
        send_func = ctx.capture_state.get("_send_email_func")
        if not send_func:
            return json.dumps({"error": "Email küldés nem elérhető"})
        try:
            result = await send_func(to=to, subject=subject, body=body, caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "read_gmail_attachment":
        message_id = args.get("message_id", "")
        attachment_index = args.get("attachment_index", 0)
        if not message_id:
            return json.dumps({"error": "message_id szükséges (gmail_search-ből)"})
        read_att_func = ctx.capture_state.get("_read_gmail_attachment_func")
        if not read_att_func:
            return json.dumps({"error": "Csatolmány olvasás nem elérhető"})
        try:
            result = await read_att_func(message_id=message_id, attachment_index=attachment_index, caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "calendar_create":
        summary = args.get("summary", "")
        start = args.get("start_time", "") or args.get("start", "")
        end = args.get("end_time", "") or args.get("end", "")
        description = args.get("description", "")
        if not summary or not start:
            return json.dumps({"error": "summary és start_time szükséges"})
        create_cal_func = ctx.capture_state.get("_create_calendar_func")
        if not create_cal_func:
            return json.dumps({"error": "Naptár létrehozás nem elérhető"})
        try:
            result = await create_cal_func(summary=summary, start=start, end=end, description=description, caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "web_fetch":
        url = args.get("url", "")
        if not url or not url.startswith("http"):
            return json.dumps({"error": "Érvényes URL szükséges"})
        import re
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
            text = resp.text
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return json.dumps({"url": url, "content": text[:4000], "length": len(text)}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "read_memory":
        key = args.get("key", "")
        category = args.get("category", "")
        read_mem_func = ctx.capture_state.get("_read_memory_func")
        if not read_mem_func:
            return json.dumps({"error": "Memory olvasás nem elérhető"})
        try:
            result = await read_mem_func(key=key, category=category, caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "search_memory":
        query = args.get("query", "")
        if not query:
            return json.dumps({"error": "Keresési kifejezés szükséges"})
        search_mem_func = ctx.capture_state.get("_search_memory_func")
        if not search_mem_func:
            return json.dumps({"error": "Memory keresés nem elérhető"})
        try:
            result = await search_mem_func(query=query, caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "send_message":
        recipient = args.get("recipient", "")
        subject = args.get("subject", "")
        message = args.get("message", "")
        if not recipient or not message:
            return json.dumps({"error": "recipient és message szükséges"})
        send_msg_func = ctx.capture_state.get("_send_message_func")
        if not send_msg_func:
            return json.dumps({"error": "Bridge üzenet küldés nem elérhető"})
        try:
            result = await send_msg_func(sender="feldwebel", recipient=recipient, subject=subject or "Feldwebel üzenet", message=message, caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "search_discussions":
        query = args.get("query", "")
        if not query:
            return json.dumps({"error": "Keresési kifejezés szükséges"})
        search_disc_func = ctx.capture_state.get("_search_discussions_func")
        if not search_disc_func:
            return json.dumps({"error": "Diskusszió keresés nem elérhető"})
        try:
            result = await search_disc_func(query=query, caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    return json.dumps({"error": f"Ismeretlen tool: {name}"})


# ── Text-based tool call parser ────────────────────────────────────


def _parse_text_tool_call(content: str):
    """
    Parse text-based tool calls from DeepSeek DSML or Kimi markers.
    Returns (tool_name, args_dict) or None.
    """
    import re

    # DeepSeek DSML format:
    # <｜DSML｜invoke name="calendar_create">
    # <｜DSML｜parameter name="summary" string="true">Value</｜DSML｜parameter>
    dsml_match = re.search(r'invoke name="([^"]+)"', content)
    if dsml_match:
        tool_name = dsml_match.group(1)
        params = {}
        for m in re.finditer(r'parameter name="([^"]+)"[^>]*>([^<]*)<', content):
            params[m.group(1)] = m.group(2)
        if params:
            return tool_name, params

    # Kimi format:
    # <|tool_call_begin|>functions.web_search:2<|tool_call_argument_begin|>{"query": "..."}
    kimi_match = re.search(r'functions\.(\w+).*?argument_begin\|>(\{[^}]+\})', content, re.DOTALL)
    if kimi_match:
        tool_name = kimi_match.group(1)
        try:
            params = json.loads(kimi_match.group(2))
            return tool_name, params
        except json.JSONDecodeError:
            pass

    # Generic: try to find any {"key": "value"} JSON in the text
    json_match = re.search(r'\{[^{}]*"query"[^{}]*\}', content)
    if json_match:
        try:
            params = json.loads(json_match.group())
            return "web_search", params
        except json.JSONDecodeError:
            pass

    return None


# ── Helper functions ───────────────────────────────────────────────


def _fetch_open_tasks(ctx) -> str:
    """Fetch open tasks from Bridge DB for system prompt context."""
    try:
        conn = ctx.get_db()
        rows = conn.execute(
            "SELECT id, title, status, priority FROM tasks "
            "WHERE status IN ('pending', 'in_progress') "
            "ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        conn.close()
        if not rows:
            return ""
        lines = ["# NYITOTT FELADATOK"]
        for r in rows:
            lines.append(f"- #{r['id']} {r['title']} ({r['status']}, {r['priority']})")
        return "\n".join(lines)
    except Exception:
        return ""


def _store_bridge_reply(ctx, user_text: str, reply: str, agent_id: str):
    """Store Feldwebel reply in Bridge messages DB."""
    try:
        conn = ctx.get_db()
        row = conn.execute(
            "SELECT id FROM messages WHERE sender = 'kommandant' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        msg_id = row["id"] if row else None
        conn.execute(
            "INSERT INTO messages (timestamp, sender, recipient, subject, message, priority, thread_id, reply_to) "
            "VALUES (?, ?, 'kommandant', ?, ?, 'normal', ?, ?)",
            (datetime.now(timezone.utc).isoformat(),
             f"feldwebel-{agent_id}",
             "Re: Feldwebel válasz",
             reply,
             msg_id, msg_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed to store Bridge reply: %s", e)
