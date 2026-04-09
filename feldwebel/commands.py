"""
Telegram command router for the Feldwebel system.
Parses slash commands and dispatches to handlers.
"""

import logging
from datetime import datetime, timezone
from html import escape as html_escape
from typing import Optional, Tuple

from feldwebel import get_ctx, BridgeContext

logger = logging.getLogger("feldwebel.commands")

# Command name -> internal action mapping
COMMANDS = {
    "/brief":    "briefing",
    "/cal":      "calendar",
    "/calendar": "calendar",
    "/task":     "create_task",
    "/tasks":    "list_tasks",
    "/email":    "send_email",
    "/reply":    "reply_email",
    "/remind":   "set_reminder",
    "/opus":     "ask_opus",
    "/status":   "system_status",
    "/help":     "show_help",
    "/clear":    "clear_history",
}


def parse_command(text: str) -> Tuple[Optional[str], str]:
    """
    Parse incoming text for slash commands.
    Returns (action_name, args_string) or (None, original_text).
    """
    text = text.strip()
    if not text.startswith("/"):
        return None, text

    parts = text.split(None, 1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    action = COMMANDS.get(cmd)
    return action, args


async def handle_command(text: str, chat_id: str) -> bool:
    """
    Main entry point. Called by server.py's _handle_telegram_message().
    Returns True if a command was handled, False if free text.
    """
    action, args = parse_command(text)
    if action is None:
        return False

    ctx = get_ctx()

    handlers = {
        "briefing":       _cmd_brief,
        "calendar":       _cmd_calendar,
        "create_task":    _cmd_create_task,
        "list_tasks":     _cmd_list_tasks,
        "send_email":     _cmd_send_email,
        "reply_email":    _cmd_reply_email,
        "set_reminder":   _cmd_set_reminder,
        "ask_opus":       _cmd_ask_opus,
        "system_status":  _cmd_status,
        "show_help":      _cmd_help,
        "clear_history":  _cmd_clear_history,
    }

    handler = handlers.get(action)
    if handler:
        try:
            await handler(args, chat_id, ctx)
        except Exception as e:
            logger.error("Command %s failed: %s", action, e)
            await ctx.telegram_push(f"<b>FELDWEBEL</b> — Hiba: {html_escape(str(e)[:500])}")
    return True


# ── Individual command handlers ────────────────────────────────────────


async def _cmd_help(args: str, chat_id: str, ctx: BridgeContext):
    """Show available commands."""
    await ctx.telegram_push("""<b>⚔️ Feldwebel Parancsok</b>

/brief — Napi briefing (email + naptár + feladatok)
/cal — Mai naptár
/task &lt;leírás&gt; — Új feladat létrehozása
/tasks — Nyitott feladatok listája
/email &lt;cím&gt; &lt;tárgy&gt; — Email küldés
/reply — Válasz legutóbbi emailre
/remind &lt;HH:MM&gt; &lt;szöveg&gt; — Emlékeztető
/opus &lt;kérdés&gt; — Eszkaláció Claude Opus-ra
/status — Rendszer állapot
/clear — Beszélgetés törlése
/help — Ez az üzenet

Szabad szöveget is megértek — kérdezz bármit!""")


async def _cmd_status(args: str, chat_id: str, ctx: BridgeContext):
    """System status: services, counts, health."""
    conn = ctx.get_db()

    # Count unread messages
    unread = conn.execute(
        "SELECT COUNT(*) as c FROM messages WHERE status = 'unread' AND sender != 'capture-daemon'"
    ).fetchone()["c"]

    # Count pending tasks
    pending = conn.execute(
        "SELECT COUNT(*) as c FROM tasks WHERE status IN ('pending', 'in_progress')"
    ).fetchone()["c"]

    # Count today's emails
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    emails_today = conn.execute(
        "SELECT COUNT(*) as c FROM messages WHERE sender = 'capture-daemon' AND timestamp >= ?",
        (today,)
    ).fetchone()["c"]

    conn.close()

    # Check Google services
    gmail_ok = bool(ctx.capture_state.get("gmail_service"))
    cal_ok = bool(ctx.capture_state.get("calendar_service"))
    capture_running = ctx.capture_state.get("capture_running", False)

    await ctx.telegram_push(f"""<b>📊 Rendszer Állapot</b>

<b>Szolgáltatások:</b>
• Gmail: {"✅" if gmail_ok else "❌"}
• Calendar: {"✅" if cal_ok else "❌"}
• Capture daemon: {"🟢 fut" if capture_running else "🔴 áll"}
• Feldwebel: ✅ v1.0.0

<b>Számok:</b>
• Olvasatlan üzenetek: {unread}
• Nyitott feladatok: {pending}
• Mai emailek: {emails_today}""")


async def _cmd_clear_history(args: str, chat_id: str, ctx: BridgeContext):
    """Clear conversation history."""
    from feldwebel.history import clear_history
    clear_history(chat_id)
    await ctx.telegram_push("🧹 Beszélgetés törölve. Tiszta lap.")


async def _cmd_brief(args: str, chat_id: str, ctx: BridgeContext):
    """Trigger manual briefing."""
    from feldwebel.briefing import assemble_briefing
    brief = await assemble_briefing(ctx)
    await ctx.telegram_push(brief)


async def _cmd_calendar(args: str, chat_id: str, ctx: BridgeContext):
    """Show today's calendar events."""
    conn = ctx.get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check capture-daemon calendar entries from today
    rows = conn.execute(
        "SELECT subject, message, timestamp FROM messages "
        "WHERE sender = 'capture-daemon' AND subject LIKE '%aptár%' "
        "AND timestamp >= ? ORDER BY timestamp DESC LIMIT 10",
        (today,)
    ).fetchall()
    conn.close()

    if not rows:
        # Try calling capture_calendar_poll directly
        if ctx.capture_calendar_poll:
            try:
                result = await ctx.capture_calendar_poll(caller="feldwebel")
                await ctx.telegram_push(f"<b>📅 Mai naptár</b>\n\n{html_escape(str(result)[:3000])}")
                return
            except Exception as e:
                logger.warning("Calendar poll failed: %s", e)
        await ctx.telegram_push("📅 Nincs mai naptáresemény a rendszerben.")
        return

    lines = []
    for r in rows:
        lines.append(f"• {html_escape(r['subject'][:100])}")
    await ctx.telegram_push(f"<b>📅 Mai naptár</b>\n\n" + "\n".join(lines))


async def _cmd_create_task(args: str, chat_id: str, ctx: BridgeContext):
    """/task <description> — create a Bridge task."""
    if not args.strip():
        await ctx.telegram_push("Használat: /task &lt;feladat leírása&gt;")
        return

    conn = ctx.get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO tasks (title, assigned_to, assigned_by, status, priority, created_at) "
        "VALUES (?, 'cli-claus', 'feldwebel', 'pending', 'normal', ?)",
        (args.strip(), now)
    )
    conn.commit()
    conn.close()

    await ctx.telegram_push(f"✅ Task létrehozva: <b>{html_escape(args.strip()[:100])}</b>")
    logger.info("Task created via Feldwebel: %s", args.strip()[:100])


async def _cmd_list_tasks(args: str, chat_id: str, ctx: BridgeContext):
    """List open tasks."""
    conn = ctx.get_db()
    rows = conn.execute(
        "SELECT id, title, status, priority, assigned_to FROM tasks "
        "WHERE status IN ('pending', 'in_progress') "
        "ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
        "WHEN 'normal' THEN 2 ELSE 3 END, created_at DESC LIMIT 15"
    ).fetchall()
    conn.close()

    if not rows:
        await ctx.telegram_push("📋 Nincs nyitott feladat.")
        return

    priority_emoji = {"urgent": "🔴", "high": "🟠", "normal": "🔵", "low": "⚪"}
    lines = []
    for r in rows:
        emoji = priority_emoji.get(r["priority"], "⚪")
        status = "🔄" if r["status"] == "in_progress" else "⏳"
        lines.append(f"{emoji}{status} #{r['id']} {html_escape(r['title'][:80])} → {r['assigned_to'] or '?'}")

    await ctx.telegram_push(f"<b>📋 Nyitott feladatok ({len(rows)})</b>\n\n" + "\n".join(lines))


async def _cmd_send_email(args: str, chat_id: str, ctx: BridgeContext):
    """/email recipient@email.com Subject and body here."""
    if not args.strip():
        await ctx.telegram_push("Használat: /email &lt;cím&gt; &lt;tárgy és szöveg&gt;")
        return

    parts = args.strip().split(None, 1)
    to_addr = parts[0]
    body = parts[1] if len(parts) > 1 else ""

    if "@" not in to_addr:
        await ctx.telegram_push("❌ Érvénytelen email cím. Használat: /email &lt;cím&gt; &lt;szöveg&gt;")
        return

    if not body:
        await ctx.telegram_push("❌ Adj meg szöveget az email cím után.")
        return

    # Confirmation before sending
    await ctx.telegram_push(
        f"📧 <b>Email küldés megerősítése</b>\n\n"
        f"<b>Címzett:</b> {html_escape(to_addr)}\n"
        f"<b>Szöveg:</b> {html_escape(body[:500])}\n\n"
        f"Küldéshez írd: /send\n"
        f"Elvetéshez írd: /discard"
    )
    # Store draft in DB for /send to pick up
    conn = ctx.get_db()
    conn.execute(
        "INSERT INTO shared_memory (key, value, category, tags, updated_by, updated_at) "
        "VALUES ('email_draft', ?, 'feldwebel_state', 'draft', 'feldwebel', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (f"{to_addr}|||{body}", datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


async def _cmd_reply_email(args: str, chat_id: str, ctx: BridgeContext):
    """/reply — show recent emails for reply selection."""
    conn = ctx.get_db()
    rows = conn.execute(
        "SELECT id, subject, message FROM messages "
        "WHERE sender = 'capture-daemon' AND subject LIKE '%Gmail%' "
        "ORDER BY timestamp DESC LIMIT 5"
    ).fetchall()
    conn.close()

    if not rows:
        await ctx.telegram_push("📧 Nincs friss email a rendszerben.")
        return

    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(f"<b>{i}.</b> {html_escape(r['subject'][:80])}")

    await ctx.telegram_push(
        f"<b>📧 Utolsó emailek</b>\n\n" + "\n".join(lines) +
        "\n\nVálaszhoz írd: /reply &lt;szám&gt; &lt;válasz szöveg&gt;"
    )


async def _cmd_set_reminder(args: str, chat_id: str, ctx: BridgeContext):
    """/remind HH:MM text — set a reminder."""
    if not args.strip():
        await ctx.telegram_push("Használat: /remind &lt;HH:MM&gt; &lt;szöveg&gt;")
        return

    parts = args.strip().split(None, 1)
    time_str = parts[0]
    text = parts[1] if len(parts) > 1 else "Emlékeztető"

    # Validate time format
    try:
        hour, minute = time_str.split(":")
        int(hour), int(minute)
    except (ValueError, AttributeError):
        await ctx.telegram_push("❌ Formátum: /remind &lt;HH:MM&gt; &lt;szöveg&gt;\nPélda: /remind 14:30 Hívd fel Jánost")
        return

    # Store as task with deadline
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    deadline = f"{today}T{time_str}:00"

    conn = ctx.get_db()
    conn.execute(
        "INSERT INTO tasks (title, assigned_to, assigned_by, status, priority, deadline, created_at) "
        "VALUES (?, 'feldwebel', 'kommandant', 'pending', 'high', ?, ?)",
        (f"⏰ {text}", deadline, now.isoformat())
    )
    conn.commit()
    conn.close()

    await ctx.telegram_push(f"⏰ Emlékeztető beállítva: <b>{time_str}</b> — {html_escape(text[:200])}")


async def _cmd_ask_opus(args: str, chat_id: str, ctx: BridgeContext):
    """/opus — placeholder for Claude Opus escalation."""
    if not args.strip():
        await ctx.telegram_push("Használat: /opus &lt;kérdés&gt;")
        return
    await ctx.telegram_push(
        "🧠 Opus eszkaláció jelenleg fejlesztés alatt.\n"
        f"Kérdésed: <i>{html_escape(args[:300])}</i>\n\n"
        "DeepSeek-kel próbálom megválaszolni..."
    )
    # Fall through to DeepSeek responder
    from feldwebel.responder import respond
    await respond(args, chat_id)
