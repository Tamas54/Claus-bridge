"""
Conversation history for Telegram ↔ DeepSeek sessions.
Stores last N messages per chat_id for context injection.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("feldwebel.history")


def ensure_history_table(get_db_func):
    """Create telegram_history table if not exists."""
    conn = get_db_func()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS telegram_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            agent_id TEXT DEFAULT 'deepseek',
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tghist_chat
            ON telegram_history(chat_id, timestamp DESC);
    """)
    conn.commit()
    conn.close()
    logger.info("telegram_history table ensured")


def add_message(chat_id: str, role: str, content: str, agent_id: str = "deepseek"):
    """Store a message in conversation history."""
    from feldwebel import get_ctx
    conn = get_ctx().get_db()
    conn.execute(
        "INSERT INTO telegram_history (chat_id, role, agent_id, content, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(chat_id), role, agent_id, content,
         datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


def get_history(chat_id: str, limit: int = 8) -> list:
    """
    Return last `limit` messages for chat_id, oldest first.
    Returns list of {"role": "user"|"assistant", "content": "...", "agent_id": "..."}
    """
    from feldwebel import get_ctx
    conn = get_ctx().get_db()
    rows = conn.execute(
        "SELECT role, content, agent_id FROM telegram_history "
        "WHERE chat_id = ? ORDER BY timestamp DESC LIMIT ?",
        (str(chat_id), limit)
    ).fetchall()
    conn.close()
    # Reverse to get chronological order (oldest first)
    return [{"role": r["role"], "content": r["content"], "agent_id": r["agent_id"]}
            for r in reversed(rows)]


def clear_history(chat_id: str):
    """Clear all conversation history for a chat."""
    from feldwebel import get_ctx
    conn = get_ctx().get_db()
    conn.execute("DELETE FROM telegram_history WHERE chat_id = ?", (str(chat_id),))
    conn.commit()
    conn.close()
    logger.info("History cleared for chat %s", chat_id)


def trim_history(chat_id: str, max_entries: int = 30):
    """Delete oldest entries beyond max_entries for a chat."""
    from feldwebel import get_ctx
    conn = get_ctx().get_db()
    conn.execute(
        "DELETE FROM telegram_history WHERE chat_id = ? AND id NOT IN "
        "(SELECT id FROM telegram_history WHERE chat_id = ? "
        "ORDER BY timestamp DESC LIMIT ?)",
        (str(chat_id), str(chat_id), max_entries)
    )
    conn.commit()
    conn.close()
