"""
DeepSeek response engine with conversation history and Pyramid context.
The Feldwebel — operational assistant for the Kommandant.
"""

import json
import logging
from html import escape as html_escape

import httpx

from feldwebel import get_ctx
from feldwebel.history import add_message, get_history, trim_history

logger = logging.getLogger("feldwebel.responder")

FELDWEBEL_SYSTEM_PROMPT = """Te a Feldwebel vagy — a Kommandant személyes Telegram asszisztense a Claus-Bridge rendszeren belül.

Szereped:
- Gyors, lényegre törő válaszok magyarul
- Kontextusban vagy: látod az emaileket, naptárat, feladatokat
- Ha a Kommandant parancsot ad (pl. "küld el email-t", "hozz létre taskot"), felismered és végrehajtod
- Emlékszel a közelmúlt beszélgetésre (utolsó néhány üzenet)

Parancsok amiket felismersz (de slash paranccsal is elérhetők):
- Napi összefoglaló kérése → /brief
- Email küldés → /email
- Task létrehozása → /task
- Naptár lekérése → /cal
- Emlékeztető → /remind
- Claude Opus eszkaláció → /opus

Szabályok:
- Magyar nyelv, hacsak nem kérnek mást
- Tömör válaszok (max 3-5 mondat, kivéve ha elemzést kérnek)
- Ha nem tudsz valamit, mondd meg — ne hallucináj
- Ha parancsot felismersz szabad szövegben, hajtsd végre és jelezd vissza
- Aláírás: "— Feldwebel" (csak ha kérik, alapból nem kell)"""


async def respond(text: str, chat_id: str, agent_id: str = "deepseek") -> str:
    """
    Generate a DeepSeek response with conversation history.
    Returns the response text.
    """
    ctx = get_ctx()

    # 1. Store user message in history
    add_message(chat_id, "user", text)

    # 2. Build system prompt with Pyramid context
    system_prompt = _build_system_prompt(ctx, agent_id)

    # 3. Build messages array with history
    history = get_history(chat_id, limit=8)
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    # 4. Call SiliconFlow API
    model_id = ctx.siliconflow_models.get(agent_id, "deepseek-ai/DeepSeek-V3.2")

    try:
        async with httpx.AsyncClient(timeout=ctx.siliconflow_timeout) as client:
            resp = await client.post(
                f"{ctx.siliconflow_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {ctx.siliconflow_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_id,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 1500,
                },
            )
            data = json.loads(resp.text)
    except Exception as e:
        logger.error("SiliconFlow API error: %s", e)
        await ctx.telegram_push(f"<b>FELDWEBEL</b> — API hiba: {html_escape(str(e)[:500])}")
        return ""

    # Check for API error
    if "error" in data:
        error_msg = str(data["error"])
        logger.error("SiliconFlow returned error: %s", error_msg)
        await ctx.telegram_push(f"<b>FELDWEBEL</b> — API hiba: {html_escape(error_msg[:500])}")
        return ""

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content or not content.strip():
        await ctx.telegram_push("<b>FELDWEBEL</b> — üres válasz")
        return ""

    # 5. Store assistant response in history
    add_message(chat_id, "assistant", content, agent_id)

    # 6. Push to Telegram
    safe_content = html_escape(content[:3800])
    await ctx.telegram_push(f"<b>FELDWEBEL</b>\n\n{safe_content}")

    # 7. Store reply in Bridge DB
    _store_bridge_reply(ctx, text, content, agent_id)

    # 8. Trim old history
    trim_history(chat_id, max_entries=30)

    logger.info("Feldwebel responded (%d chars)", len(content))
    return content


def _build_system_prompt(ctx, agent_id: str) -> str:
    """Build full system prompt: Feldwebel base + Pyramid context."""
    parts = [FELDWEBEL_SYSTEM_PROMPT]

    # Add Pyramid context if available
    try:
        from pyramid.context_builder import build_agent_context
        pyramid_ctx = build_agent_context(
            agent_id=agent_id,
            inbox_summary=ctx.get_inbox_summary(8) if ctx.get_inbox_summary else "",
        )
        parts.append(pyramid_ctx)
    except (ImportError, Exception) as e:
        logger.debug("Pyramid context unavailable: %s", e)

    return "\n\n---\n\n".join(parts)


def _store_bridge_reply(ctx, user_text: str, reply: str, agent_id: str):
    """Store Feldwebel reply in Bridge messages DB."""
    try:
        from datetime import datetime, timezone
        conn = ctx.get_db()
        # Find the original message ID
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
             f"Re: Feldwebel válasz",
             reply,
             msg_id, msg_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed to store Bridge reply: %s", e)
