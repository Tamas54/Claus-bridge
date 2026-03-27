"""
Claus Bridge MCP — Kommunikációs híd CLI-Claus és Web-Claus között
==================================================================
Railway deploy: SSE transport, SQLite persistent storage, FTS5 full-text search.
18 tool: messaging + threads + tasks + shared memory + discussions + session logs + capabilities.

Deployed on Railway alongside Makronóm, BioMed, CégTár, HírMagnet MCP servers.
"""

import os
import json
import sqlite3
import time
from datetime import datetime, timezone
from fastmcp import FastMCP

# --- Server Setup ---
mcp = FastMCP(
    "Claus Bridge",
    description="Kommunikációs híd Claus CLI és Claus Web instance-ok között. "
                "Üzenetküldés, shared memory, task management, discussions, session logs.",
)

DB_PATH = os.environ.get("BRIDGE_DB_PATH", "bridge.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        -- Messages with threading support
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
            status TEXT DEFAULT 'unread',
            FOREIGN KEY (reply_to) REFERENCES messages(id)
        );

        -- Full-text search on messages
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            subject, message, sender, recipient,
            content=messages, content_rowid=id
        );

        -- Triggers to keep FTS in sync
        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, subject, message, sender, recipient)
            VALUES (new.id, new.subject, new.message, new.sender, new.recipient);
        END;

        -- Shared memory / knowledge base
        CREATE TABLE IF NOT EXISTS shared_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            tags TEXT DEFAULT '',
            updated_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            key, value, category, tags,
            content=shared_memory, content_rowid=id
        );

        CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON shared_memory BEGIN
            INSERT INTO memory_fts(rowid, key, value, category, tags)
            VALUES (new.id, new.key, new.value, new.category, new.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON shared_memory BEGIN
            DELETE FROM memory_fts WHERE rowid = old.id;
            INSERT INTO memory_fts(rowid, key, value, category, tags)
            VALUES (new.id, new.key, new.value, new.category, new.tags);
        END;

        -- Tasks
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            assigned_to TEXT NOT NULL,
            assigned_by TEXT NOT NULL,
            priority TEXT DEFAULT 'normal',
            status TEXT DEFAULT 'pending',
            deadline TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        -- Discussions (collaborative thinking)
        CREATE TABLE IF NOT EXISTS discussions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            context TEXT DEFAULT '',
            status TEXT DEFAULT 'open',
            resolution TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS discussion_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discussion_id INTEGER NOT NULL,
            instance TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (discussion_id) REFERENCES discussions(id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS discussions_fts USING fts5(
            topic, context,
            content=discussions, content_rowid=id
        );

        CREATE TRIGGER IF NOT EXISTS discussions_ai AFTER INSERT ON discussions BEGIN
            INSERT INTO discussions_fts(rowid, topic, context)
            VALUES (new.id, new.topic, new.context);
        END;

        -- Session logs
        CREATE TABLE IF NOT EXISTS session_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instance TEXT NOT NULL,
            summary TEXT NOT NULL,
            key_decisions TEXT DEFAULT '',
            key_learnings TEXT DEFAULT '',
            timestamp TEXT NOT NULL
        );

        -- Capability registry
        CREATE TABLE IF NOT EXISTS capabilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instance TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            description TEXT NOT NULL,
            registered_at TEXT NOT NULL,
            UNIQUE(instance, tool_name)
        );

        -- Heartbeat tracking
        CREATE TABLE IF NOT EXISTS heartbeats (
            instance TEXT PRIMARY KEY,
            last_seen TEXT NOT NULL,
            session_info TEXT DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()


def now():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# MESSAGING TOOLS (1-5)
# ============================================================

@mcp.tool()
async def send_message(sender: str, recipient: str, subject: str, message: str,
                       priority: str = "normal", reply_to: int = None) -> str:
    """Send a message to the other Claus instance.

    Args:
        sender: Who sends it — 'cli-claus' or 'web-claus'
        recipient: Who receives it — 'cli-claus' or 'web-claus'
        subject: Message subject line
        message: Full message content
        priority: info / normal / urgent / critical
        reply_to: Optional message ID to reply to (creates thread)
    """
    conn = get_db()
    thread_id = None
    if reply_to:
        row = conn.execute("SELECT thread_id, id FROM messages WHERE id = ?", (reply_to,)).fetchone()
        if row:
            thread_id = row["thread_id"] or row["id"]

    cur = conn.execute(
        "INSERT INTO messages (timestamp, sender, recipient, subject, message, priority, thread_id, reply_to) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now(), sender, recipient, subject, message, priority, thread_id, reply_to)
    )
    msg_id = cur.lastrowid
    if not thread_id:
        conn.execute("UPDATE messages SET thread_id = ? WHERE id = ?", (msg_id, msg_id))
    conn.commit()
    conn.close()
    return json.dumps({"status": "sent", "message_id": msg_id, "thread_id": thread_id or msg_id})


@mcp.tool()
async def read_messages(recipient: str = None, limit: int = 20, since: str = None,
                        unread_only: bool = False, thread_id: int = None) -> str:
    """Read messages, optionally filtered.

    Args:
        recipient: Filter by recipient ('cli-claus' or 'web-claus')
        limit: Max messages to return (default 20)
        since: ISO timestamp — only messages after this time
        unread_only: If true, only return unread messages
        thread_id: Filter by thread ID to see a conversation
    """
    conn = get_db()
    query = "SELECT * FROM messages WHERE 1=1"
    params = []

    if recipient:
        query += " AND recipient = ?"
        params.append(recipient)
    if since:
        query += " AND timestamp > ?"
        params.append(since)
    if unread_only:
        query += " AND status = 'unread'"
    if thread_id:
        query += " AND thread_id = ?"
        params.append(thread_id)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    messages = [dict(r) for r in rows]
    conn.close()
    return json.dumps(messages, ensure_ascii=False)


@mcp.tool()
async def read_new(instance: str) -> str:
    """Read all unread messages for a specific instance and mark them as read.

    Args:
        instance: 'cli-claus' or 'web-claus'
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM messages WHERE recipient = ? AND status = 'unread' ORDER BY timestamp ASC",
        (instance,)
    ).fetchall()
    messages = [dict(r) for r in rows]

    if messages:
        ids = [m["id"] for m in messages]
        conn.execute(
            f"UPDATE messages SET status = 'read' WHERE id IN ({','.join('?' * len(ids))})",
            ids
        )
        conn.commit()

    conn.close()
    return json.dumps({"count": len(messages), "messages": messages}, ensure_ascii=False)


@mcp.tool()
async def search_messages(query: str, limit: int = 20) -> str:
    """Full-text search across all messages.

    Args:
        query: Search query (supports FTS5 syntax: AND, OR, NOT, "exact phrase")
        limit: Max results (default 20)
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT m.* FROM messages m JOIN messages_fts f ON m.id = f.rowid "
        "WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?",
        (query, limit)
    ).fetchall()
    conn.close()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


@mcp.tool()
async def mark_read(message_id: int) -> str:
    """Mark a specific message as read.

    Args:
        message_id: The message ID to mark as read
    """
    conn = get_db()
    conn.execute("UPDATE messages SET status = 'read' WHERE id = ?", (message_id,))
    conn.commit()
    conn.close()
    return json.dumps({"status": "marked_read", "message_id": message_id})


# ============================================================
# SHARED MEMORY / KNOWLEDGE BASE (6-9)
# ============================================================

@mcp.tool()
async def write_memory(key: str, value: str, category: str = "general",
                       tags: str = "", instance: str = "unknown") -> str:
    """Write or update a shared memory entry. Use for decisions, project context, learnings.

    Args:
        key: Unique key (e.g., 'bridge_mcp_auth_decision')
        value: Content to store
        category: general / decision / project / learning / config
        tags: Comma-separated tags for searchability
        instance: Who wrote it — 'cli-claus' or 'web-claus'
    """
    conn = get_db()
    ts = now()
    existing = conn.execute("SELECT id FROM shared_memory WHERE key = ?", (key,)).fetchone()

    if existing:
        conn.execute(
            "UPDATE shared_memory SET value=?, category=?, tags=?, updated_by=?, updated_at=? WHERE key=?",
            (value, category, tags, instance, ts, key)
        )
    else:
        conn.execute(
            "INSERT INTO shared_memory (key, value, category, tags, updated_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, value, category, tags, instance, ts, ts)
        )
    conn.commit()
    conn.close()
    return json.dumps({"status": "saved", "key": key})


@mcp.tool()
async def read_memory(key: str = None, category: str = None) -> str:
    """Read shared memory entries by key or category.

    Args:
        key: Exact key to look up (returns single entry)
        category: Filter by category (returns all matching)
    """
    conn = get_db()
    if key:
        row = conn.execute("SELECT * FROM shared_memory WHERE key = ?", (key,)).fetchone()
        conn.close()
        return json.dumps(dict(row) if row else {"error": f"Key '{key}' not found"}, ensure_ascii=False)
    elif category:
        rows = conn.execute("SELECT * FROM shared_memory WHERE category = ? ORDER BY updated_at DESC", (category,)).fetchall()
        conn.close()
        return json.dumps([dict(r) for r in rows], ensure_ascii=False)
    else:
        rows = conn.execute("SELECT key, category, updated_by, updated_at FROM shared_memory ORDER BY updated_at DESC LIMIT 50").fetchall()
        conn.close()
        return json.dumps([dict(r) for r in rows], ensure_ascii=False)


@mcp.tool()
async def search_memory(query: str, limit: int = 20) -> str:
    """Full-text search across shared memory.

    Args:
        query: Search query (FTS5 syntax)
        limit: Max results
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT m.* FROM shared_memory m JOIN memory_fts f ON m.id = f.rowid "
        "WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?",
        (query, limit)
    ).fetchall()
    conn.close()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


@mcp.tool()
async def list_memory(category: str = None) -> str:
    """List all shared memory keys, optionally filtered by category.

    Args:
        category: Optional filter — general / decision / project / learning / config
    """
    conn = get_db()
    if category:
        rows = conn.execute(
            "SELECT key, category, tags, updated_by, updated_at FROM shared_memory WHERE category = ? ORDER BY updated_at DESC",
            (category,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT key, category, tags, updated_by, updated_at FROM shared_memory ORDER BY category, updated_at DESC"
        ).fetchall()
    conn.close()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


# ============================================================
# TASK MANAGEMENT (10-12)
# ============================================================

@mcp.tool()
async def create_task(title: str, assigned_to: str, assigned_by: str,
                      description: str = "", priority: str = "normal",
                      deadline: str = None) -> str:
    """Create a task for one of the Claus instances.

    Args:
        title: Task title
        assigned_to: 'cli-claus' or 'web-claus'
        assigned_by: Who created it
        description: Detailed description
        priority: low / normal / high / critical
        deadline: Optional ISO date
    """
    conn = get_db()
    ts = now()
    cur = conn.execute(
        "INSERT INTO tasks (title, description, assigned_to, assigned_by, priority, status, deadline, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
        (title, description, assigned_to, assigned_by, priority, deadline, ts, ts)
    )
    conn.commit()
    task_id = cur.lastrowid
    conn.close()
    return json.dumps({"status": "created", "task_id": task_id})


@mcp.tool()
async def update_task(task_id: int, status: str = None, description: str = None) -> str:
    """Update a task's status or description.

    Args:
        task_id: Task ID
        status: pending / in_progress / completed / cancelled
        description: Updated description (append notes)
    """
    conn = get_db()
    ts = now()
    if status:
        conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, ts, task_id))
    if description:
        conn.execute("UPDATE tasks SET description=?, updated_at=? WHERE id=?", (description, ts, task_id))
    conn.commit()
    conn.close()
    return json.dumps({"status": "updated", "task_id": task_id})


@mcp.tool()
async def list_tasks(assigned_to: str = None, status: str = None) -> str:
    """List tasks, optionally filtered.

    Args:
        assigned_to: Filter by assignee
        status: Filter by status (pending/in_progress/completed/cancelled)
    """
    conn = get_db()
    query = "SELECT * FROM tasks WHERE 1=1"
    params = []
    if assigned_to:
        query += " AND assigned_to = ?"
        params.append(assigned_to)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


# ============================================================
# DISCUSSIONS (13-17)
# ============================================================

@mcp.tool()
async def start_discussion(topic: str, initial_position: str, context: str = "",
                           instance: str = "unknown") -> str:
    """Start a collaborative discussion on a topic. Both instances can add their thoughts.

    Args:
        topic: What are we discussing? (e.g., 'Bridge MCP auth megoldás')
        initial_position: Your opening position/argument
        context: Background context for the discussion
        instance: Who starts it — 'cli-claus' or 'web-claus'
    """
    conn = get_db()
    ts = now()
    cur = conn.execute(
        "INSERT INTO discussions (topic, context, status, created_by, created_at) VALUES (?, ?, 'open', ?, ?)",
        (topic, context, instance, ts)
    )
    disc_id = cur.lastrowid
    conn.execute(
        "INSERT INTO discussion_entries (discussion_id, instance, content, timestamp) VALUES (?, ?, ?, ?)",
        (disc_id, instance, initial_position, ts)
    )
    conn.commit()
    conn.close()
    return json.dumps({"status": "discussion_started", "discussion_id": disc_id})


@mcp.tool()
async def add_to_discussion(discussion_id: int, content: str, instance: str = "unknown") -> str:
    """Add your thoughts/response to an existing discussion.

    Args:
        discussion_id: Discussion ID to contribute to
        content: Your thoughts, arguments, counterpoints
        instance: 'cli-claus' or 'web-claus'
    """
    conn = get_db()
    ts = now()
    conn.execute(
        "INSERT INTO discussion_entries (discussion_id, instance, content, timestamp) VALUES (?, ?, ?, ?)",
        (discussion_id, instance, content, ts)
    )
    conn.commit()

    entries = conn.execute(
        "SELECT instance, content, timestamp FROM discussion_entries WHERE discussion_id = ? ORDER BY timestamp",
        (discussion_id,)
    ).fetchall()
    conn.close()
    return json.dumps({
        "status": "added",
        "discussion_id": discussion_id,
        "total_entries": len(entries),
        "thread": [dict(e) for e in entries]
    }, ensure_ascii=False)


@mcp.tool()
async def resolve_discussion(discussion_id: int, resolution: str, instance: str = "unknown") -> str:
    """Resolve a discussion and save the decision to shared memory.

    Args:
        discussion_id: Discussion to resolve
        resolution: The agreed decision/conclusion
        instance: Who resolves it
    """
    conn = get_db()
    ts = now()

    disc = conn.execute("SELECT topic, context FROM discussions WHERE id = ?", (discussion_id,)).fetchone()
    if not disc:
        conn.close()
        return json.dumps({"error": f"Discussion {discussion_id} not found"})

    conn.execute(
        "UPDATE discussions SET status='resolved', resolution=?, resolved_at=? WHERE id=?",
        (resolution, ts, discussion_id)
    )

    # Auto-save decision to shared memory
    key = f"decision_{discussion_id}_{disc['topic'][:50].replace(' ', '_').lower()}"
    conn.execute(
        "INSERT INTO shared_memory (key, value, category, tags, updated_by, created_at, updated_at) "
        "VALUES (?, ?, 'decision', ?, ?, ?, ?)",
        (key, resolution, f"discussion_{discussion_id}", instance, ts, ts)
    )
    conn.commit()
    conn.close()
    return json.dumps({"status": "resolved", "discussion_id": discussion_id, "memory_key": key})


@mcp.tool()
async def list_discussions(status: str = None, limit: int = 20) -> str:
    """List discussions, optionally filtered by status.

    Args:
        status: open / resolved (default: all)
        limit: Max results
    """
    conn = get_db()
    query = "SELECT d.*, COUNT(e.id) as entry_count FROM discussions d LEFT JOIN discussion_entries e ON d.id = e.discussion_id"
    params = []
    if status:
        query += " WHERE d.status = ?"
        params.append(status)
    query += " GROUP BY d.id ORDER BY d.created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


@mcp.tool()
async def search_discussions(query: str, limit: int = 20) -> str:
    """Full-text search across discussion topics and context.

    Args:
        query: Search query (FTS5 syntax)
        limit: Max results
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT d.*, COUNT(e.id) as entry_count FROM discussions d "
        "JOIN discussions_fts f ON d.id = f.rowid "
        "LEFT JOIN discussion_entries e ON d.id = e.discussion_id "
        "WHERE discussions_fts MATCH ? GROUP BY d.id ORDER BY rank LIMIT ?",
        (query, limit)
    ).fetchall()
    conn.close()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


# ============================================================
# SESSION LOGS & STATUS (18-20)
# ============================================================

@mcp.tool()
async def log_session(instance: str, summary: str, key_decisions: str = "",
                      key_learnings: str = "") -> str:
    """Log a session summary. Call at the end of every session to maintain continuity.

    Args:
        instance: 'cli-claus' or 'web-claus'
        summary: What happened this session
        key_decisions: Important decisions made
        key_learnings: Things learned / gotchas discovered
    """
    conn = get_db()
    ts = now()
    cur = conn.execute(
        "INSERT INTO session_logs (instance, summary, key_decisions, key_learnings, timestamp) VALUES (?, ?, ?, ?, ?)",
        (instance, summary, key_decisions, key_learnings, ts)
    )
    conn.commit()
    conn.close()
    return json.dumps({"status": "logged", "log_id": cur.lastrowid})


@mcp.tool()
async def heartbeat(instance: str, session_info: str = "") -> str:
    """Send a heartbeat to signal this instance is alive. Call at session start.

    Args:
        instance: 'cli-claus' or 'web-claus'
        session_info: Optional context about current session
    """
    conn = get_db()
    ts = now()
    conn.execute(
        "INSERT OR REPLACE INTO heartbeats (instance, last_seen, session_info) VALUES (?, ?, ?)",
        (instance, ts, session_info)
    )
    conn.commit()
    conn.close()
    return json.dumps({"status": "alive", "instance": instance, "timestamp": ts})


@mcp.tool()
async def get_status() -> str:
    """Get system status: who's online, unread counts, open tasks, active discussions."""
    conn = get_db()

    heartbeats_rows = conn.execute("SELECT * FROM heartbeats").fetchall()
    unread_cli = conn.execute("SELECT COUNT(*) as c FROM messages WHERE recipient='cli-claus' AND status='unread'").fetchone()["c"]
    unread_web = conn.execute("SELECT COUNT(*) as c FROM messages WHERE recipient='web-claus' AND status='unread'").fetchone()["c"]
    open_tasks = conn.execute("SELECT COUNT(*) as c FROM tasks WHERE status IN ('pending', 'in_progress')").fetchone()["c"]
    open_discussions = conn.execute("SELECT COUNT(*) as c FROM discussions WHERE status='open'").fetchone()["c"]
    total_messages = conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
    total_memory = conn.execute("SELECT COUNT(*) as c FROM shared_memory").fetchone()["c"]

    last_session = conn.execute("SELECT instance, summary, timestamp FROM session_logs ORDER BY timestamp DESC LIMIT 2").fetchall()

    conn.close()
    return json.dumps({
        "instances": {h["instance"]: {"last_seen": h["last_seen"], "session_info": h["session_info"]} for h in heartbeats_rows},
        "unread": {"cli-claus": unread_cli, "web-claus": unread_web},
        "open_tasks": open_tasks,
        "open_discussions": open_discussions,
        "total_messages": total_messages,
        "total_memory_entries": total_memory,
        "recent_sessions": [dict(s) for s in last_session]
    }, ensure_ascii=False)


# ============================================================
# LANDING PAGE
# ============================================================

from starlette.responses import HTMLResponse

@mcp.custom_route("/", methods=["GET"])
async def landing(request):
    html = """<!DOCTYPE html>
<html><head><title>Claus Bridge MCP</title>
<style>
body { background: #0f172a; color: #e2e8f0; font-family: 'Inter', -apple-system, sans-serif; margin: 0; padding: 2rem; }
h1 { color: #60a5fa; } h2 { color: #94a3b8; } code { background: #1e293b; padding: 2px 6px; border-radius: 4px; }
.tool { background: #1e293b; padding: 1rem; margin: 0.5rem 0; border-radius: 8px; border-left: 3px solid #60a5fa; }
.tool b { color: #60a5fa; }
</style></head><body>
<h1>Claus Bridge MCP</h1>
<p>Kommunikációs híd <b>CLI-Claus</b> (Claude Code) és <b>Web-Claus</b> (Claude.ai) között.</p>
<p><b>Connect:</b> <code>{url}/mcp</code></p>

<h2>Messaging (5 tools)</h2>
<div class="tool"><b>send_message</b> — Üzenet küldés a másik instance-nak</div>
<div class="tool"><b>read_messages</b> — Üzenetek olvasása (szűrőkkel)</div>
<div class="tool"><b>read_new</b> — Olvasatlan üzenetek + auto mark-read</div>
<div class="tool"><b>search_messages</b> — FTS5 full-text keresés üzenetekben</div>
<div class="tool"><b>mark_read</b> — Üzenet olvasottnak jelölése</div>

<h2>Shared Memory (4 tools)</h2>
<div class="tool"><b>write_memory</b> — Közös tudásbázis írás (key-value + tags)</div>
<div class="tool"><b>read_memory</b> — Memória olvasás key/category alapján</div>
<div class="tool"><b>search_memory</b> — FTS5 keresés a tudásbázisban</div>
<div class="tool"><b>list_memory</b> — Összes memória kulcs listázás</div>

<h2>Tasks (3 tools)</h2>
<div class="tool"><b>create_task</b> — Feladat létrehozás (egymásnak adhatók)</div>
<div class="tool"><b>update_task</b> — Feladat státusz frissítés</div>
<div class="tool"><b>list_tasks</b> — Feladatok listázás (szűrőkkel)</div>

<h2>Discussions (5 tools)</h2>
<div class="tool"><b>start_discussion</b> — Közös vita indítás egy témáról</div>
<div class="tool"><b>add_to_discussion</b> — Hozzászólás vitához</div>
<div class="tool"><b>resolve_discussion</b> — Vita lezárás → döntés shared memory-ba</div>
<div class="tool"><b>list_discussions</b> — Nyitott/lezárt viták listázás</div>
<div class="tool"><b>search_discussions</b> — FTS5 keresés vitákban</div>

<h2>System (3 tools)</h2>
<div class="tool"><b>log_session</b> — Session összefoglaló mentés</div>
<div class="tool"><b>heartbeat</b> — "Élek" jelzés</div>
<div class="tool"><b>get_status</b> — Rendszer státusz (ki online, olvasatlan, stb.)</div>

<p style="margin-top:2rem;color:#64748b;">20 tools | SQLite + FTS5 | Railway SSE transport<br>
<i>Die Zahnräder greifen ineinander!</i></p>
</body></html>"""
    return HTMLResponse(html)


# ============================================================
# STARTUP
# ============================================================

init_db()

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8003))
    )
