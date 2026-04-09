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
    {
        "type": "function",
        "function": {
            "name": "gmail_search",
            "description": "Keress emaileket a Gmail-ben. Használd ha a Kommandant emailről kérdez, személyt keres, vagy levelet akar látni.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Gmail keresési query. Példák: from:Darvas, from:Tóth, subject:EdTech, newer_than:1d, is:unread"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_poll",
            "description": "Frissítsd a Gmail inbox-ot — új emailek lekérése és kategorizálása. Használd ha a Kommandant frissítést kér.",
            "parameters": {
                "type": "object",
                "properties": {},
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_poll",
            "description": "Naptár frissítése — mai események lekérése. Használd ha naptárról kérdez.",
            "parameters": {
                "type": "object",
                "properties": {},
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Új feladat létrehozása a Bridge rendszerben.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "A feladat leírása"
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "Nyitott feladatok listázása a Bridge-ből.",
            "parameters": {
                "type": "object",
                "properties": {},
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ai_query",
            "description": "Küldj feladatot EGY konkrét AI agentnek. Ők a 'rizsrakéták': Kimi (kutató, 256k context), DeepSeek (gyors elemző), GLM-5 (kódoló, 205k context). Használd ha a Kommandant egy konkrét agentet szólít meg vagy specifikus feladatot ad.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "Agent neve: 'kimi', 'deepseek', vagy 'glm5'",
                        "enum": ["kimi", "deepseek", "glm5"]
                    },
                    "prompt": {
                        "type": "string",
                        "description": "A feladat / kérdés amit az agentnek adsz"
                    }
                },
                "required": ["model", "prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ai_task",
            "description": "Küldj feladatot MIND A HÁROM AI agentnek párhuzamosan (Kimi + DeepSeek + GLM-5). Mindegyik dolgozik rajta, majd szintézis készül. Használd komplex feladatoknál, kutatásnál, elemzésnél.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Rövid feladat cím"
                    },
                    "description": {
                        "type": "string",
                        "description": "Részletes feladat leírás, instrukciók"
                    }
                },
                "required": ["title", "description"]
            }
        }
    },
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

AI agentek (rizsrakéták):
- **Kimi** (moonshotai/Kimi-K2.5): 256k kontextus, kutató/elemző. Használd kutatáshoz, hosszú dokumentumokhoz.
- **DeepSeek** (deepseek-ai/DeepSeek-V3.2): gyors gondolkodás, kritikus/reviewer. Használd elemzéshez, véleményezéshez.
- **GLM-5** (zai-org/GLM-5): 205k kontextus, kódoló/végrehajtó. Használd kódoláshoz, technikai feladatokhoz.
- Ha a Kommandant konkrét agentet említ ("Kimi nézd meg", "kérdezd meg GLM-5-öt"), használd az ai_query tool-t.
- Ha komplex feladat, ahol mind a 3 agent kellene, használd az ai_task tool-t.

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

    # Agent loop — max 3 rounds of tool calls
    final_content = ""
    for round_num in range(3):
        data = await _call_deepseek(ctx, model_id, messages, use_tools=(round_num < 2))
        if not data:
            break

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "")

        # If tool calls requested
        if msg.get("tool_calls") and finish_reason == "tool_calls":
            # Add assistant message with tool calls to conversation
            messages.append(msg)

            # Execute each tool call
            for tc in msg["tool_calls"]:
                fn_name = tc.get("function", {}).get("name", "")
                fn_args_raw = tc.get("function", {}).get("arguments", "{}")
                tc_id = tc.get("id", "")

                try:
                    fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                except json.JSONDecodeError:
                    fn_args = {}

                logger.info("Tool call round %d: %s(%s)", round_num, fn_name, fn_args)

                # Execute tool
                result = await _execute_tool(fn_name, fn_args, ctx)

                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result,
                })

            # Continue loop — DeepSeek will process tool results
            continue

        # Text response — we're done
        final_content = msg.get("content", "")
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
        if not ctx.capture_state.get("_ai_query_func"):
            return json.dumps({"error": "AI query nem elérhető"})
        try:
            result = await ctx.capture_state["_ai_query_func"](
                model=model, prompt=prompt, caller="feldwebel"
            )
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "ai_task":
        title = args.get("title", "")
        description = args.get("description", "")
        if not title or not description:
            return json.dumps({"error": "Cím és leírás szükséges"})
        if not ctx.capture_state.get("_ai_task_func"):
            return json.dumps({"error": "AI task nem elérhető"})
        try:
            result = await ctx.capture_state["_ai_task_func"](
                title=title, description=description, assigned_by="feldwebel"
            )
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    return json.dumps({"error": f"Ismeretlen tool: {name}"})


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
