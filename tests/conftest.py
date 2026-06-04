"""
Shared test fixtures for the 5 new Claus-Bridge plugins.

- Puts the repo root on sys.path so `import plugins.xxx` works.
- Provides a temp SQLite DB (with the relevant DDL copied from server.py / recipes.py)
  and a `get_db` callable with sqlite3.Row row_factory.
- Provides a FakeApp that collects @app.tool() registered coroutine functions by name.
"""

import os
import sqlite3
import sys

import pytest

# --- Repo root on sys.path (so `plugins.*` imports resolve) ---
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ---------------------------------------------------------------------------
# DDL copied verbatim (minus IF NOT EXISTS noise) from server.py init_db()
# and plugins/recipes.py _INIT_SQL — the plugins only INSERT/SELECT, never CREATE.
# ---------------------------------------------------------------------------
_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    priority TEXT DEFAULT 'normal',
    thread_id INTEGER,
    reply_to INTEGER,
    status TEXT DEFAULT 'unread'
);

CREATE TABLE IF NOT EXISTS signal_gatekeeper (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_x TEXT NOT NULL,
    outcome_y TEXT NOT NULL,
    verdikt TEXT NOT NULL CHECK(verdikt IN ('definial','oksagi_hid','csak_korrelal')),
    indoklas TEXT DEFAULT '',
    heurisztika_hasznalt TEXT DEFAULT '',
    idobelyeg TEXT NOT NULL,
    created_by TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS semantic_triage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    sender TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    category TEXT NOT NULL,
    score REAL DEFAULT 0.0,
    method TEXT DEFAULT 'embedding',
    borderline INTEGER DEFAULT 0,
    gatekeeper_called INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS draft_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    draft_id TEXT DEFAULT '',
    recipient TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    body_preview TEXT DEFAULT '',
    style_notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    created_by TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS digest_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_date TEXT NOT NULL UNIQUE,
    emails_seen INTEGER DEFAULT 0,
    needs_reply INTEGER DEFAULT 0,
    drafted INTEGER DEFAULT 0,
    summary TEXT DEFAULT '',
    watermark TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_message_id TEXT DEFAULT '',
    kind TEXT NOT NULL CHECK(kind IN ('calendar','reminder')),
    title TEXT NOT NULL,
    due_at TEXT DEFAULT '',
    payload_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','created')),
    created_at TEXT NOT NULL,
    decided_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pyramid_recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    required_tools TEXT DEFAULT '[]',
    prompt_template TEXT NOT NULL,
    created_by TEXT DEFAULT 'kommandant',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    enabled BOOLEAN DEFAULT 1,
    cron_schedule TEXT DEFAULT NULL,
    cron_model TEXT DEFAULT 'glm5',
    cron_enabled BOOLEAN DEFAULT 0,
    cron_delivery TEXT DEFAULT 'both',
    cron_last_run TIMESTAMP DEFAULT NULL,
    cron_deep_research INTEGER DEFAULT 0,
    cron_deep_thinking INTEGER DEFAULT 0,
    vertical TEXT DEFAULT NULL,
    vertical_command TEXT DEFAULT NULL
);
"""


@pytest.fixture
def db_path(tmp_path):
    """Path to a fresh temp SQLite DB with all relevant tables created."""
    p = str(tmp_path / "test_bridge.db")
    conn = sqlite3.connect(p)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def get_db(db_path):
    """A get_db callable returning a sqlite3 connection with Row row_factory."""
    def _get_db():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_db


class FakeApp:
    """Stand-in for the FastMCP app: @app.tool() registers the decorated coroutine
    function into a name->func dict so tests can grab and asyncio.run() them."""

    def __init__(self):
        self.tools = {}

    def tool(self, *dargs, **dkwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def fake_app():
    return FakeApp()
