"""
Daily briefing assembly — gathers calendar, emails, tasks, and Bridge messages.
Can be triggered manually (/brief) or automatically at morning time.
"""

import json
import logging
from datetime import datetime, timezone
from html import escape as html_escape

import httpx

logger = logging.getLogger("feldwebel.briefing")


async def assemble_briefing(ctx) -> str:
    """
    Assemble a comprehensive daily briefing.
    Gathers: calendar + emails + tasks + Bridge messages.
    Optionally uses DeepSeek to format.
    """
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    weekday_hu = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"][now.weekday()]
    date_str = f"{now.strftime('%Y.%m.%d')} ({weekday_hu})"

    sections = []

    # 1. Calendar events
    cal = _get_calendar_section(ctx, today)
    if cal:
        sections.append(cal)

    # 2. Email summary
    email = _get_email_section(ctx, today)
    if email:
        sections.append(email)

    # 3. Open tasks
    tasks = _get_task_section(ctx)
    if tasks:
        sections.append(tasks)

    # 4. Unread Bridge messages
    msgs = _get_message_section(ctx)
    if msgs:
        sections.append(msgs)

    if not sections:
        return f"<b>☀️ BRIEFING — {html_escape(date_str)}</b>\n\nTiszta nap — semmi említésre méltó. ☕"

    raw = "\n\n".join(sections)

    # Try DeepSeek formatting for a polished brief
    if ctx.siliconflow_api_key:
        formatted = await _deepseek_format(ctx, raw, date_str)
        if formatted:
            return formatted

    # Fallback: raw sections
    return f"<b>☀️ BRIEFING — {html_escape(date_str)}</b>\n\n{raw}"


def _get_calendar_section(ctx, today: str) -> str:
    """Calendar events from Bridge DB (capture-daemon entries)."""
    try:
        conn = ctx.get_db()
        rows = conn.execute(
            "SELECT subject, message, timestamp FROM messages "
            "WHERE sender = 'capture-daemon' AND (subject LIKE '%aptár%' OR subject LIKE '%alendar%') "
            "AND timestamp >= ? ORDER BY timestamp ASC LIMIT 15",
            (today,)
        ).fetchall()
        conn.close()

        if not rows:
            return ""

        lines = ["<b>📅 NAPTÁR</b>"]
        for r in rows:
            # Extract time if available from the message
            msg = r["message"][:150] if r["message"] else r["subject"]
            lines.append(f"• {html_escape(msg[:120])}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Calendar section failed: %s", e)
        return ""


def _get_email_section(ctx, today: str) -> str:
    """Email summary from Bridge DB."""
    try:
        conn = ctx.get_db()
        rows = conn.execute(
            "SELECT subject, message, priority, timestamp FROM messages "
            "WHERE sender = 'capture-daemon' AND subject LIKE '%Gmail%' "
            "ORDER BY timestamp DESC LIMIT 10"
        ).fetchall()
        conn.close()

        if not rows:
            return ""

        urgent = sum(1 for r in rows if r["priority"] == "urgent")
        important = sum(1 for r in rows if r["priority"] == "important")

        lines = [f"<b>📧 EMAILEK</b> ({len(rows)} db, {urgent} sürgős, {important} fontos)"]
        priority_emoji = {"urgent": "🔴", "important": "🟠", "normal": "🔵"}
        for r in rows[:8]:
            emoji = priority_emoji.get(r["priority"], "⚪")
            subj = r["subject"].replace("Gmail: ", "").replace("Gmail — ", "")[:80]
            lines.append(f"{emoji} {html_escape(subj)}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Email section failed: %s", e)
        return ""


def _get_task_section(ctx) -> str:
    """Open tasks from Bridge DB."""
    try:
        conn = ctx.get_db()
        rows = conn.execute(
            "SELECT id, title, status, priority, assigned_to FROM tasks "
            "WHERE status IN ('pending', 'in_progress') "
            "ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'normal' THEN 2 ELSE 3 END, created_at DESC LIMIT 10"
        ).fetchall()
        conn.close()

        if not rows:
            return ""

        in_progress = sum(1 for r in rows if r["status"] == "in_progress")
        pending = sum(1 for r in rows if r["status"] == "pending")

        lines = [f"<b>📋 FELADATOK</b> ({in_progress} folyamatban, {pending} várakozik)"]
        for r in rows:
            status = "🔄" if r["status"] == "in_progress" else "⏳"
            lines.append(f"{status} #{r['id']} {html_escape(r['title'][:80])}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Task section failed: %s", e)
        return ""


def _get_message_section(ctx) -> str:
    """Unread Bridge messages (not capture-daemon)."""
    try:
        conn = ctx.get_db()
        rows = conn.execute(
            "SELECT sender, subject, timestamp FROM messages "
            "WHERE status = 'unread' AND sender NOT IN ('capture-daemon', 'kommandant', 'feldwebel-deepseek') "
            "ORDER BY timestamp DESC LIMIT 5"
        ).fetchall()
        conn.close()

        if not rows:
            return ""

        lines = [f"<b>💬 BRIDGE ÜZENETEK</b> ({len(rows)} olvasatlan)"]
        for r in rows:
            sender = r["sender"] or "?"
            lines.append(f"• <b>{html_escape(sender)}</b>: {html_escape(r['subject'][:60])}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Message section failed: %s", e)
        return ""


async def _deepseek_format(ctx, raw: str, date_str: str) -> str:
    """Ask DeepSeek to format the raw briefing into a polished Hungarian summary."""
    try:
        model_id = ctx.siliconflow_models.get("deepseek", "deepseek-ai/DeepSeek-V4-Pro")
        prompt = (
            f"Formázd meg az alábbi napi briefinget tömören és áttekinthetően. "
            f"Dátum: {date_str}. Használj emoji-kat. Max 20 sor. "
            f"Zárd ezzel: 'Jó reggelt, Kommandant! ☕'\n\n{raw}"
        )

        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": "Te egy briefing formázó vagy. Csak a formázott briefinget add vissza, semmi mást."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 800,
        }
        if "Kimi" in model_id:
            payload["thinking"] = {"type": "disabled"}
        elif "DeepSeek" in model_id:
            payload["reasoning_effort"] = "medium"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{ctx.siliconflow_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {ctx.siliconflow_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            data = json.loads(resp.text)

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if content and content.strip():
            return f"<b>☀️ BRIEFING — {html_escape(date_str)}</b>\n\n{html_escape(content[:3500])}"
    except Exception as e:
        logger.warning("DeepSeek briefing format failed: %s", e)

    return ""
