"""
FELDWEBEL — Telegram parancsnoki asszisztens a Claus-Bridge rendszerhez.
DeepSeek V3.2 alapú válaszadó, parancskezelő, email triage, briefing.
"""

__version__ = "1.0.0"

import os
import sqlite3
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

logger = logging.getLogger("feldwebel")

DB_PATH = os.environ.get("BRIDGE_DB_PATH", "bridge.db")


def get_db():
    """Get a SQLite connection (same pattern as server.py)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@dataclass
class BridgeContext:
    """
    Server.py injects its functions here at startup.
    Feldwebel modules use this instead of importing from server.py.
    """
    # Core
    telegram_push: Callable[[str], Awaitable[None]] = None
    get_inbox_summary: Callable[[int], str] = None
    get_db: Callable[[], sqlite3.Connection] = field(default_factory=lambda: get_db)
    capture_state: dict = field(default_factory=dict)

    # SiliconFlow config
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.com/v1"
    siliconflow_timeout: int = 220
    siliconflow_models: dict = field(default_factory=dict)

    # Bridge MCP tool references (for /email, /cal, /task commands)
    capture_send_email: Optional[Callable] = None
    capture_calendar_poll: Optional[Callable] = None
    create_calendar_event: Optional[Callable] = None
    capture_gmail_poll: Optional[Callable] = None
    list_tasks_func: Optional[Callable] = None
    create_task_func: Optional[Callable] = None

    # Telegram chat ID (for reminders, scheduled pushes)
    telegram_chat_id: str = ""


# Singleton context
_ctx: Optional[BridgeContext] = None


def init_feldwebel(ctx: BridgeContext):
    """Initialize the Feldwebel system. Called by server.py at startup."""
    global _ctx
    _ctx = ctx
    from feldwebel.history import ensure_history_table
    ensure_history_table(ctx.get_db)
    logger.info("Feldwebel v%s initialized", __version__)


def get_ctx() -> BridgeContext:
    """Get the initialized BridgeContext. Raises if not initialized."""
    if _ctx is None:
        raise RuntimeError("Feldwebel not initialized — call init_feldwebel() first")
    return _ctx
