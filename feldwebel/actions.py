"""
Tool execution layer for the Feldwebel.
Detects actionable intents, asks for confirmation, executes on approval.
"""

import json
import logging
from datetime import datetime, timezone
from html import escape as html_escape

from feldwebel import get_ctx

logger = logging.getLogger("feldwebel.actions")

# Pending action storage per chat (in-memory, survives within one deploy)
_pending_actions: dict = {}  # chat_id -> {"action": str, "params": dict, "description": str}


# ── Intent detection ───────────────────────────────────────────────

# Keywords that trigger action detection
ACTION_PATTERNS = {
    "gmail_poll": {
        "keywords": ["gmail poll", "email poll", "frissítsd az emailt", "frissítsd az emaileket",
                     "email frissítés", "futtasd a gmail", "futtasd le a gmail",
                     "szinkronizáld az emailt", "email szinkron", "töltsd be az emaileket"],
        "description": "📧 Gmail poll futtatása (friss emailek lekérése)",
    },
    "calendar_poll": {
        "keywords": ["naptár frissítés", "calendar poll", "frissítsd a naptárat",
                     "futtasd a calendar", "naptár szinkron", "szinkronizáld a naptárat"],
        "description": "📅 Naptár frissítése (mai események lekérése)",
    },
    "inbox_search": {
        "keywords": ["keress rá", "keresd meg az emailt", "nézd meg az emailjeit",
                     "keress a levelek között", "keress emailt"],
        "description": "🔍 Gmail keresés",
    },
    "send_email": {
        "keywords": ["küldj emailt", "küldj levelet", "írj emailt", "írj levelet",
                     "küldj neki emailt", "küldj neki levelet"],
        "description": "📤 Email küldés",
    },
}


def detect_action(text: str) -> tuple:
    """
    Detect if user text contains an actionable intent.
    Returns (action_name, params_dict) or (None, None).
    """
    text_lower = text.lower().strip()

    # Check for confirmation of pending action
    if text_lower in ("igen", "yes", "ok", "oké", "hajrá", "mehet", "csináld",
                      "rajta", "futtasd", "küld", "küldés", "/yes", "/igen", "/ok"):
        return "confirm", {}

    # Check for rejection
    if text_lower in ("nem", "no", "ne", "mégsem", "töröld", "hagyd",
                      "/no", "/nem", "/cancel"):
        return "cancel", {}

    # Check action patterns
    for action_name, config in ACTION_PATTERNS.items():
        for kw in config["keywords"]:
            if kw in text_lower:
                return action_name, {"original_text": text}

    return None, None


def has_pending_action(chat_id: str) -> bool:
    """Check if there's a pending action waiting for confirmation."""
    return str(chat_id) in _pending_actions


def get_pending_action(chat_id: str) -> dict:
    """Get the pending action for a chat."""
    return _pending_actions.get(str(chat_id), {})


def clear_pending_action(chat_id: str):
    """Clear pending action for a chat."""
    _pending_actions.pop(str(chat_id), None)


# ── Action handlers ────────────────────────────────────────────────


async def propose_action(action: str, params: dict, chat_id: str) -> bool:
    """
    Propose an action and ask for confirmation.
    Returns True if action was proposed (caller should NOT call DeepSeek).
    """
    ctx = get_ctx()
    chat_id = str(chat_id)

    config = ACTION_PATTERNS.get(action, {})
    description = config.get("description", action)

    # Store as pending
    _pending_actions[chat_id] = {
        "action": action,
        "params": params,
        "description": description,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await ctx.telegram_push(
        f"<b>⚔️ FELDWEBEL — Megerősítés szükséges</b>\n\n"
        f"Végrehajtandó: <b>{html_escape(description)}</b>\n\n"
        f"Megerősíted? Írd: <b>igen</b> vagy <b>nem</b>"
    )

    logger.info("Action proposed: %s for chat %s", action, chat_id)
    return True


async def execute_pending(chat_id: str) -> str:
    """Execute the confirmed pending action. Returns result text."""
    ctx = get_ctx()
    chat_id = str(chat_id)
    pending = _pending_actions.pop(chat_id, None)

    if not pending:
        await ctx.telegram_push("<b>FELDWEBEL</b> — Nincs függőben lévő művelet.")
        return ""

    action = pending["action"]
    params = pending["params"]
    description = pending["description"]

    await ctx.telegram_push(f"⏳ Végrehajtás: <b>{html_escape(description)}</b>...")

    try:
        result = await _run_action(action, params, ctx)
        await ctx.telegram_push(
            f"✅ <b>Kész:</b> {html_escape(description)}\n\n{html_escape(result[:3500])}"
        )
        logger.info("Action executed: %s → %d chars result", action, len(result))
        return result
    except Exception as e:
        error_msg = str(e)[:500]
        await ctx.telegram_push(
            f"❌ <b>Hiba:</b> {html_escape(description)}\n{html_escape(error_msg)}"
        )
        logger.error("Action %s failed: %s", action, e)
        return ""


async def cancel_pending(chat_id: str):
    """Cancel the pending action."""
    ctx = get_ctx()
    chat_id = str(chat_id)
    pending = _pending_actions.pop(chat_id, None)
    if pending:
        await ctx.telegram_push(f"🚫 Törölve: {html_escape(pending['description'])}")
    else:
        await ctx.telegram_push("<b>FELDWEBEL</b> — Nincs mit törölni.")


async def _run_action(action: str, params: dict, ctx) -> str:
    """Execute an action and return the result as text."""

    if action == "gmail_poll":
        if ctx.capture_gmail_poll:
            result = await ctx.capture_gmail_poll(caller="feldwebel")
            return f"Gmail poll eredmény:\n{result}"
        return "Gmail poll nem elérhető (capture daemon nem fut)."

    if action == "calendar_poll":
        if ctx.capture_calendar_poll:
            result = await ctx.capture_calendar_poll(caller="feldwebel")
            return f"Naptár frissítés eredmény:\n{result}"
        return "Calendar poll nem elérhető."

    if action == "inbox_search":
        query = params.get("original_text", "")
        if ctx.capture_state.get("_capture_inbox_func"):
            # Extract search terms
            stop_words = {"keress", "keresd", "meg", "emailt", "emailjeit",
                          "nézd", "levelek", "között", "rá", "az"}
            words = [w.strip(".,!?") for w in query.split() if len(w.strip(".,!?")) > 2]
            terms = [w for w in words if w.lower() not in stop_words]
            gmail_query = " ".join(terms[:4]) if terms else "is:unread"

            result_json = await ctx.capture_state["_capture_inbox_func"](
                limit=10, query=gmail_query, caller="feldwebel"
            )
            data = json.loads(result_json)
            if "error" in data:
                return f"Gmail keresés hiba: {data['error']}"

            messages = data.get("messages", [])
            if not messages:
                return f"Nincs találat: \"{gmail_query}\""

            lines = [f"Gmail keresés: \"{gmail_query}\" — {len(messages)} találat\n"]
            for m in messages:
                lines.append(
                    f"• {m.get('from', '?')} — {m.get('subject', '?')}\n"
                    f"  {m.get('snippet', '')[:200]}"
                )
            return "\n".join(lines)
        return "Gmail keresés nem elérhető."

    if action == "send_email":
        return "Email küldéshez használd: /email <cím> <tárgy és szöveg>"

    return f"Ismeretlen művelet: {action}"
