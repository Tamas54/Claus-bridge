"""
Shared test fixtures for the 5 new Claus-Bridge plugins.

- Puts the repo root on sys.path so `import plugins.xxx` works.
- Provides a temp SQLite DB (with the relevant DDL copied from server.py / recipes.py)
  and a `get_db` callable with sqlite3.Row row_factory.
- Provides a FakeApp that collects @app.tool() registered coroutine functions by name.
"""

import atexit
import os
import shutil
import socket
import sqlite3
import sys
import tempfile

import pytest

# --- Repo root on sys.path (so `plugins.*` imports resolve) ---
# NOTE: superseded by `pythonpath = .` in pytest.ini. Kept until the whole
# batch of hand-rolled path inserts across tests/ is removed in one go.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ===========================================================================
# SANDBOX GUARDS
#
# These exist because the app resolves two things in a way that makes the test
# suite dangerous by default. Both were observed, not theorised:
#
#  1. DATABASE. Every module that touches storage does
#         DB_PATH = os.environ.get("BRIDGE_DB_PATH", "bridge.db")
#     — a RELATIVE path, resolved against the working directory. Worse,
#     pyramid/memory_shared.py calls _ensure_table() at IMPORT time (line 35),
#     so merely importing it opens that database and runs CREATE TABLE. Before
#     this guard, a plain `pytest tests/` mutated the repo's own bridge.db on
#     every run (md5 changed 589ffc19… -> 635cc296… across a single run). In
#     production BRIDGE_DB_PATH points at the Railway volume (/data/bridge.db),
#     so anything that inherits a production environment and runs the suite
#     writes to live data. Pinning it here means the suite CANNOT reach a real
#     database no matter what the surrounding environment says.
#
#  2. NETWORK. The suite currently makes zero outbound connections (measured:
#     0 connects, 0 DNS lookups across all tests). This guard's job is to keep
#     that true, and to make the day it stops being true a loud failure rather
#     than a slow, flaky, credential-dependent one.
# ===========================================================================

# --- 1. Database: force every run onto a throwaway file --------------------
_SANDBOX_DIR = tempfile.mkdtemp(prefix="claus-bridge-tests-")
os.environ["BRIDGE_DB_PATH"] = os.path.join(_SANDBOX_DIR, "sandbox_bridge.db")
atexit.register(shutil.rmtree, _SANDBOX_DIR, True)


# --- 2. Credentials: never let a real key reach a test ---------------------
# Two ways a live key gets in:
#   a) the developer's shell already exports it;
#   b) IMPORTING A TEST MODULE LOADS THE REPO'S .env. tests/test_delphoi_en_-
#      nowcast.py imports run_first_nowcast_uk_us, whose module body calls
#      load_dotenv(<repo>/.env) — so collection alone pulls the real
#      SILICONFLOW / OPENAI / MOONSHOT / TWITTER credentials and ECHOLOT_URL
#      into os.environ for the whole session.
# (b) is why scrubbing once at import time is not enough, and why the scrub is
# ALSO re-applied before every test below. A test that passes because a real
# credential happened to be present is a test that will behave differently in
# CI; any test needing a credential must set a fake one itself, which is what
# all of them already do.
_LIVE_CREDENTIAL_ENV = (
    "AGORA_OP_KEY_DER_KARTOGRAPH", "AGORA_OP_KEY_FRAU_LUPE", "AGORA_OP_KEY_VON_TAKT",
    "BRAVE_SEARCH_API_KEY", "DELPHOI_ANCHOR_AGORA_KEY", "DELPHOI_BRIDGE_KEY",
    "DELPHOI_SAAS_SECRET", "ECHOLOT_URL", "GOOGLE_TOKEN_JSON", "HIRMAGNET_API_KEY",
    "INKLING_API_KEY", "MOONSHOT_API_KEY", "OPENAI_API_KEY", "SILICONFLOW_API_KEY",
    "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "TELEGRAM_BOT_TOKEN",
    "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN_SECRET", "TWITTER_API_KEY",
    "TWITTER_API_SECRET", "TWITTER_BEARER_TOKEN", "TWITTER_CLIENT_ID",
    "TWITTER_CLIENT_SECRET",
)
for _name in _LIVE_CREDENTIAL_ENV:
    os.environ.pop(_name, None)


# --- 3. Network: block outbound sockets unless the test opts in ------------
_LOOPBACK = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "::", ""}


def _is_loopback(host):
    return not isinstance(host, str) or host in _LOOPBACK or host.startswith("127.")


@pytest.fixture(autouse=True)
def _sandbox(request):
    """Re-scrub credentials and block outbound sockets, per test.

    Opt out of the network block with @pytest.mark.network for a test that
    genuinely needs a live service. Those are excluded in CI via
    `-m "not network"`, so they are reported as deselected rather than
    quietly passing without the credentials they require.
    """
    # Runs at setup, i.e. before the test body and before any monkeypatch the
    # test makes — so a test setting its own fake key still wins.
    for name in _LIVE_CREDENTIAL_ENV:
        os.environ.pop(name, None)
    os.environ["BRIDGE_DB_PATH"] = os.path.join(_SANDBOX_DIR, "sandbox_bridge.db")

    if request.node.get_closest_marker("network"):
        yield
        return

    real_connect = socket.socket.connect

    def guarded_connect(self, address):
        # Only police IP sockets. AF_UNIX addresses are filesystem paths and
        # are none of this guard's business.
        if self.family in (socket.AF_INET, socket.AF_INET6):
            host = address[0] if isinstance(address, tuple) else address
            if not _is_loopback(host):
                raise RuntimeError(
                    "Blocked outbound network connection to %r from test %s.\n"
                    "Unit tests must not talk to live services: they turn a red "
                    "build into a coin flip and depend on credentials CI does not "
                    "have. Stub the client, or mark the test "
                    "@pytest.mark.network if a real call is genuinely the point."
                    % (address, request.node.nodeid)
                )
        return real_connect(self, address)

    socket.socket.connect = guarded_connect
    try:
        yield
    finally:
        socket.socket.connect = real_connect


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
