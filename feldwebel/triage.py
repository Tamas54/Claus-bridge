"""
Smart email categorization via DeepSeek.
Falls back to string matching on failure.
"""

import json
import logging
import os
from html import escape as html_escape

import httpx

logger = logging.getLogger("feldwebel.triage")

# Reuse server.py's ignore/urgent patterns as fallback
IGNORE_SENDERS = [s.strip().lower() for s in os.environ.get("IGNORE_SENDERS", "noreply,no-reply,mailer-daemon,newsletter").split(",") if s.strip()]
URGENT_SENDERS = [s.strip().lower() for s in os.environ.get("URGENT_SENDERS", "").split(",") if s.strip()]
URGENT_KEYWORDS = [k.strip().lower() for k in os.environ.get("URGENT_KEYWORDS", "urgent,sürgős,fontos,deadline,határidő").split(",") if k.strip()]


async def smart_categorize(sender: str, subject: str, body: str, ctx) -> dict:
    """
    Call DeepSeek to categorize an email with AI.
    Returns: {priority, category, summary_hu, suggested_action}
    Falls back to string matching on failure.
    """
    if not ctx.siliconflow_api_key:
        return _fallback_categorize(sender, subject)

    prompt = f"""Elemezd az alábbi emailt és válaszolj CSAK JSON formátumban.

Feladó: {sender}
Tárgy: {subject}
Tartalom: {body[:1500]}

Válasz (kizárólag valid JSON, semmi más):
{{"priority": "urgent|important|normal|ignore",
  "category": "work|personal|newsletter|invoice|notification|spam",
  "summary_hu": "Max 2 mondat magyarul az email lényege",
  "suggested_action": "reply|archive|forward|flag|null"}}"""

    model_id = ctx.siliconflow_models.get("deepseek", "deepseek-ai/DeepSeek-V4-Pro")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{ctx.siliconflow_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {ctx.siliconflow_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_id,
                    "messages": [
                        {"role": "system", "content": "Te egy email triage rendszer vagy. KIZÁRÓLAG valid JSON-t válaszolj, semmi mást."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 400,
                },
            )
            data = json.loads(resp.text)

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        content = content.strip()

        # Handle markdown code blocks
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result = json.loads(content)

        # Validate required fields
        if "priority" not in result or "summary_hu" not in result:
            return _fallback_categorize(sender, subject)

        logger.info("AI triage: %s → %s (%s)", subject[:40], result["priority"], result.get("category", "?"))
        return result

    except Exception as e:
        logger.warning("AI triage failed, using fallback: %s", e)
        return _fallback_categorize(sender, subject)


def _fallback_categorize(sender: str, subject: str) -> dict:
    """String-matching fallback (same logic as server.py _categorize_email)."""
    sender_lower = sender.lower()
    subject_lower = subject.lower()

    for pattern in IGNORE_SENDERS:
        if pattern in sender_lower:
            return {"priority": "ignore", "category": "spam", "summary_hu": "", "suggested_action": None}

    for pattern in URGENT_SENDERS:
        if pattern in sender_lower:
            return {"priority": "urgent", "category": "work", "summary_hu": subject, "suggested_action": "reply"}

    for kw in URGENT_KEYWORDS:
        if kw in subject_lower:
            return {"priority": "urgent", "category": "work", "summary_hu": subject, "suggested_action": "flag"}

    return {"priority": "normal", "category": "work", "summary_hu": subject, "suggested_action": None}


async def push_triage_result(result: dict, email_meta: dict, ctx):
    """Push categorized email to Telegram with action suggestions."""
    priority_emoji = {"urgent": "🔴", "important": "🟠", "normal": "🔵", "ignore": "⚪"}.get(result["priority"], "⚪")

    text = (
        f"{priority_emoji} <b>EMAIL — {result['priority'].upper()}</b>\n\n"
        f"<b>Feladó:</b> {html_escape(str(email_meta.get('sender', '?'))[:80])}\n"
        f"<b>Tárgy:</b> {html_escape(str(email_meta.get('subject', '?'))[:100])}\n"
    )

    summary = result.get("summary_hu", "")
    if summary:
        text += f"\n<b>Összefoglaló:</b> {html_escape(summary[:300])}\n"

    action = result.get("suggested_action")
    if action == "reply":
        text += "\n💡 Javasolt akció: <b>válaszolj</b> → /reply"
    elif action == "flag":
        text += "\n💡 Javasolt akció: <b>megjelölés</b>"
    elif action == "forward":
        text += "\n💡 Javasolt akció: <b>továbbítás</b>"

    await ctx.telegram_push(text)
