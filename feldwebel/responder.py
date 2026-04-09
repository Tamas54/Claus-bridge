"""
DeepSeek response engine with conversation history, Pyramid context,
and real email/calendar/task access via Bridge tools.
The Feldwebel — operational assistant for the Kommandant.
"""

import json
import logging
from datetime import datetime, timezone
from html import escape as html_escape

import httpx

from feldwebel import get_ctx
from feldwebel.history import add_message, get_history, trim_history

logger = logging.getLogger("feldwebel.responder")

FELDWEBEL_SYSTEM_PROMPT_TEMPLATE = """Te a Feldwebel vagy — a Kommandant személyes Telegram asszisztense a Claus-Bridge rendszeren belül.

Mai dátum: {date_str} ({weekday_hu})
Időzóna: CET (Budapest)

Szereped:
- Gyors, lényegre törő válaszok magyarul
- Kontextusban vagy: LÁTOD az emaileket (feladó, tárgy, tartalom), a naptárat és a feladatokat
- Ha a Kommandant kérdez egy emailről, keresd meg a kontextusban és válaszolj pontosan
- Ha a Kommandant parancsot ad (pl. "küld el email-t", "hozz létre taskot"), felismered és végrehajtod

Ha a Kommandant emailre akar válaszolni:
- Írd meg a draft választ magyarul, a Kommandant stílusában (hivatalos de nem merev)
- Kérd a megerősítését: "Küldhetem? Írd /send vagy módosítsd."
- Aláírás: "Üdvözlettel, Kende Tamás"

Szabályok:
- Magyar nyelv, hacsak nem kérnek mást
- Tömör válaszok (max 3-5 mondat, kivéve ha elemzést kérnek)
- Ha nem tudsz valamit, mondd meg — ne hallucináj
- Az email kontextus amit kapsz VALÓS és FRISS — használd bátran"""


WEEKDAYS_HU = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]


def _get_feldwebel_system_prompt() -> str:
    """Build system prompt with current date injected."""
    now = datetime.now(timezone.utc)
    return FELDWEBEL_SYSTEM_PROMPT_TEMPLATE.format(
        date_str=now.strftime("%Y.%m.%d"),
        weekday_hu=WEEKDAYS_HU[now.weekday()],
    )


async def respond(text: str, chat_id: str, agent_id: str = "deepseek") -> str:
    """
    Generate a DeepSeek response with conversation history and live data.
    """
    ctx = get_ctx()

    # 1. Store user message in history
    add_message(chat_id, "user", text)

    # 2. Gather live context (emails, tasks, calendar)
    live_context = await _gather_live_context(ctx, text)

    # 3. Build system prompt with Pyramid + live context
    system_prompt = _build_system_prompt(ctx, agent_id, live_context)

    # 4. Build messages array with history
    history = get_history(chat_id, limit=8)
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    # 5. Call SiliconFlow API
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
                    "max_tokens": 2000,
                },
            )
            data = json.loads(resp.text)
    except Exception as e:
        logger.error("SiliconFlow API error: %s", e)
        await ctx.telegram_push(f"<b>FELDWEBEL</b> — API hiba: {html_escape(str(e)[:500])}")
        return ""

    if "error" in data:
        error_msg = str(data["error"])
        logger.error("SiliconFlow returned error: %s", error_msg)
        await ctx.telegram_push(f"<b>FELDWEBEL</b> — API hiba: {html_escape(error_msg[:500])}")
        return ""

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content or not content.strip():
        await ctx.telegram_push("<b>FELDWEBEL</b> — üres válasz")
        return ""

    # 6. Store assistant response in history
    add_message(chat_id, "assistant", content, agent_id)

    # 7. Push to Telegram
    safe_content = html_escape(content[:3800])
    await ctx.telegram_push(f"<b>FELDWEBEL</b>\n\n{safe_content}")

    # 8. Store reply in Bridge DB
    _store_bridge_reply(ctx, text, content, agent_id)

    # 9. Trim old history
    trim_history(chat_id, max_entries=30)

    logger.info("Feldwebel responded (%d chars)", len(content))
    return content


# ── Live context gathering ─────────────────────────────────────────


async def _gather_live_context(ctx, user_text: str) -> str:
    """
    Gather real-time data from Gmail, Calendar, Tasks based on what
    the user is asking about. Always includes recent emails.
    """
    sections = []

    # Always: fresh emails from Gmail API (last 15)
    email_ctx = await _fetch_recent_emails(ctx, limit=15)
    if email_ctx:
        sections.append(email_ctx)

    # Always: open tasks
    task_ctx = _fetch_open_tasks(ctx)
    if task_ctx:
        sections.append(task_ctx)

    # If user mentions email-related keywords, do a targeted Gmail search too
    text_lower = user_text.lower()
    email_keywords = ["email", "levél", "mail", "írj", "válasz", "reply", "küld", "forward"]
    if any(kw in text_lower for kw in email_keywords):
        # Try to extract a person's name for targeted search
        search_ctx = await _search_emails_for_query(ctx, user_text)
        if search_ctx:
            sections.append(search_ctx)

    return "\n\n".join(sections) if sections else ""


async def _fetch_recent_emails(ctx, limit: int = 15) -> str:
    """Fetch recent emails via Gmail API (capture_inbox tool)."""
    if not ctx.capture_state.get("gmail_service"):
        # Fallback: use Bridge DB
        return _fetch_emails_from_db(ctx, limit)

    try:
        # Call capture_inbox directly (it's an MCP tool function)
        result_json = await ctx.capture_state["_capture_inbox_func"](
            limit=limit, query="category:primary", caller="feldwebel"
        )
        data = json.loads(result_json)
        if "error" in data:
            return _fetch_emails_from_db(ctx, limit)

        messages = data.get("messages", [])
        if not messages:
            return ""

        lines = [f"# FRISS EMAILEK ({len(messages)} db)"]
        for m in messages:
            sender = m.get("from", "?")
            sender_email = m.get("from_email", "")
            subject = m.get("subject", "(nincs tárgy)")
            snippet = m.get("snippet", "")
            date = m.get("date", "")
            att = f" 📎{len(m['attachments'])} csatolmány" if m.get("attachments") else ""
            lines.append(
                f"- **{sender}** <{sender_email}> ({date[:16]})\n"
                f"  Tárgy: {subject}\n"
                f"  Tartalom: {snippet[:200]}{att}"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Gmail API fetch failed, using DB fallback: %s", e)
        return _fetch_emails_from_db(ctx, limit)


def _fetch_emails_from_db(ctx, limit: int = 15) -> str:
    """Fallback: fetch emails from Bridge DB capture-daemon entries."""
    try:
        conn = ctx.get_db()
        rows = conn.execute(
            "SELECT subject, message, priority, timestamp FROM messages "
            "WHERE sender = 'capture-daemon' AND subject LIKE '%Gmail%' "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()

        if not rows:
            return ""

        lines = [f"# FRISS EMAILEK ({len(rows)} db, Bridge DB-ből)"]
        for r in rows:
            prio = {"urgent": "🔴", "important": "🟠", "normal": "🔵"}.get(r["priority"], "⚪")
            subj = r["subject"].replace("Gmail: ", "").replace("Gmail — ", "")
            msg_preview = r["message"][:200] if r["message"] else ""
            lines.append(f"{prio} [{r['timestamp'][:16]}] {subj}\n   {msg_preview}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("DB email fetch failed: %s", e)
        return ""


async def _search_emails_for_query(ctx, user_text: str) -> str:
    """Search Gmail for emails related to the user's query."""
    if not ctx.capture_state.get("gmail_service"):
        return ""

    # Extract potential search terms (names, keywords) from user text
    # Remove common Hungarian words to get search-worthy terms
    stop_words = {"a", "az", "és", "is", "nem", "hogy", "egy", "van", "volt",
                  "mi", "mit", "már", "még", "csak", "de", "ha", "vagy",
                  "emailt", "emailje", "levele", "levelét", "emailjét",
                  "kaptam", "küldött", "írt", "írta", "érkezett",
                  "keresem", "keresd", "találd", "meg", "most", "tegnap", "ma"}
    words = [w.strip(".,!?:;\"'()") for w in user_text.split()
             if len(w.strip(".,!?:;\"'()")) > 2]
    search_terms = [w for w in words if w.lower() not in stop_words]

    if not search_terms:
        return ""

    query = " ".join(search_terms[:4])  # Max 4 terms for Gmail search
    logger.info("Gmail search for Feldwebel: %s", query)

    try:
        result_json = await ctx.capture_state["_capture_inbox_func"](
            limit=5, query=query, caller="feldwebel"
        )
        data = json.loads(result_json)
        messages = data.get("messages", [])
        if not messages:
            return ""

        lines = [f"# KERESÉSI EREDMÉNYEK: \"{query}\" ({len(messages)} találat)"]
        for m in messages:
            sender = m.get("from", "?")
            subject = m.get("subject", "(nincs tárgy)")
            snippet = m.get("snippet", "")
            msg_id = m.get("id", "")
            lines.append(
                f"- **{sender}** — {subject}\n"
                f"  {snippet[:300]}\n"
                f"  [message_id: {msg_id}]"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Gmail search failed: %s", e)
        return ""


def _fetch_open_tasks(ctx) -> str:
    """Fetch open tasks from Bridge DB."""
    try:
        conn = ctx.get_db()
        rows = conn.execute(
            "SELECT id, title, status, priority, assigned_to FROM tasks "
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


# ── System prompt builder ──────────────────────────────────────────


def _build_system_prompt(ctx, agent_id: str, live_context: str = "") -> str:
    """Build full system prompt: Feldwebel base + Pyramid + live data."""
    parts = [_get_feldwebel_system_prompt()]

    # Add Pyramid context if available
    try:
        from pyramid.context_builder import build_agent_context
        pyramid_ctx = build_agent_context(
            agent_id=agent_id,
            inbox_summary="",  # We provide our own richer inbox below
        )
        parts.append(pyramid_ctx)
    except (ImportError, Exception) as e:
        logger.debug("Pyramid context unavailable: %s", e)

    # Add live context (emails, tasks, calendar)
    if live_context:
        parts.append(f"# ÉLŐ ADATOK (friss, valós)\n\n{live_context}")

    return "\n\n---\n\n".join(parts)


# ── Bridge DB storage ──────────────────────────────────────────────


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
             f"Re: Feldwebel válasz",
             reply,
             msg_id, msg_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed to store Bridge reply: %s", e)
