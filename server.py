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
import base64
import asyncio
import logging
import threading
import pathlib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.utils import parseaddr
from fastmcp import FastMCP

logger = logging.getLogger("claus-bridge")

# --- Server Setup ---
mcp = FastMCP("Claus Bridge")

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

        -- Uploaded files for AI processing
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            content_text TEXT DEFAULT '',
            content_base64 TEXT DEFAULT '',
            uploaded_by TEXT NOT NULL,
            uploaded_at TEXT NOT NULL
        );

        -- AI Tasks (multi-agent task execution)
        CREATE TABLE IF NOT EXISTS ai_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            context TEXT DEFAULT '',
            assigned_by TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS ai_task_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            agent TEXT NOT NULL,
            role TEXT DEFAULT '',
            content TEXT NOT NULL,
            sources TEXT DEFAULT '',
            timestamp TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES ai_tasks(id)
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

    # Auto-trigger AI sub-agents
    thread_text = f"[{instance}]: {initial_position}"
    asyncio.ensure_future(_ai_auto_discuss(disc_id, topic, thread_text))

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

    # Get topic for AI context
    disc = conn.execute("SELECT topic FROM discussions WHERE id = ?", (discussion_id,)).fetchone()
    conn.close()

    result = json.dumps({
        "status": "added",
        "discussion_id": discussion_id,
        "total_entries": len(entries),
        "thread": [dict(e) for e in entries]
    }, ensure_ascii=False)

    # Auto-trigger AI sub-agents (only when Claude or Kommandant adds, not when AIs add)
    if instance not in SILICONFLOW_MODELS:
        thread_text = "\n".join(f"[{e['instance']}]: {e['content']}" for e in entries)
        topic = disc["topic"] if disc else f"Discussion #{discussion_id}"
        asyncio.ensure_future(_ai_auto_discuss(discussion_id, topic, thread_text))

    return result


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
    unread_kmd = conn.execute("SELECT COUNT(*) as c FROM messages WHERE recipient='kommandant' AND status='unread'").fetchone()["c"]
    open_tasks = conn.execute("SELECT COUNT(*) as c FROM tasks WHERE status IN ('pending', 'in_progress')").fetchone()["c"]
    open_discussions = conn.execute("SELECT COUNT(*) as c FROM discussions WHERE status='open'").fetchone()["c"]
    total_messages = conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
    total_memory = conn.execute("SELECT COUNT(*) as c FROM shared_memory").fetchone()["c"]

    last_session = conn.execute("SELECT instance, summary, timestamp FROM session_logs ORDER BY timestamp DESC LIMIT 2").fetchall()

    conn.close()
    return json.dumps({
        "instances": {h["instance"]: {"last_seen": h["last_seen"], "session_info": h["session_info"]} for h in heartbeats_rows},
        "unread": {"cli-claus": unread_cli, "web-claus": unread_web, "kommandant": unread_kmd},
        "open_tasks": open_tasks,
        "open_discussions": open_discussions,
        "total_messages": total_messages,
        "total_memory_entries": total_memory,
        "recent_sessions": [dict(s) for s in last_session]
    }, ensure_ascii=False)


# ============================================================
# LANDING PAGE
# ============================================================

from starlette.responses import HTMLResponse, JSONResponse
from starlette.requests import Request
import pathlib

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
# REST API FOR KOMMANDANT DASHBOARD
# ============================================================

@mcp.custom_route("/api/status", methods=["GET"])
async def api_status(request):
    conn = get_db()
    heartbeats_rows = conn.execute("SELECT * FROM heartbeats").fetchall()
    unread_cli = conn.execute("SELECT COUNT(*) as c FROM messages WHERE recipient='cli-claus' AND status='unread'").fetchone()["c"]
    unread_web = conn.execute("SELECT COUNT(*) as c FROM messages WHERE recipient='web-claus' AND status='unread'").fetchone()["c"]
    unread_kmd = conn.execute("SELECT COUNT(*) as c FROM messages WHERE recipient='kommandant' AND status='unread'").fetchone()["c"]
    open_tasks = conn.execute("SELECT COUNT(*) as c FROM tasks WHERE status IN ('pending', 'in_progress')").fetchone()["c"]
    open_discussions = conn.execute("SELECT COUNT(*) as c FROM discussions WHERE status='open'").fetchone()["c"]
    total_messages = conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
    total_memory = conn.execute("SELECT COUNT(*) as c FROM shared_memory").fetchone()["c"]
    last_session = conn.execute("SELECT instance, summary, timestamp FROM session_logs ORDER BY timestamp DESC LIMIT 3").fetchall()
    conn.close()
    return JSONResponse({
        "instances": {h["instance"]: {"last_seen": h["last_seen"], "session_info": h["session_info"]} for h in heartbeats_rows},
        "unread": {"cli-claus": unread_cli, "web-claus": unread_web, "kommandant": unread_kmd},
        "open_tasks": open_tasks,
        "open_discussions": open_discussions,
        "total_messages": total_messages,
        "total_memory_entries": total_memory,
        "recent_sessions": [dict(s) for s in last_session]
    })


@mcp.custom_route("/api/messages", methods=["GET", "POST"])
async def api_messages(request):
    conn = get_db()
    if request.method == "POST":
        body = await request.json()
        thread_id = None
        reply_to = body.get("reply_to")
        if reply_to:
            row = conn.execute("SELECT thread_id, id FROM messages WHERE id = ?", (reply_to,)).fetchone()
            if row:
                thread_id = row["thread_id"] or row["id"]
        cur = conn.execute(
            "INSERT INTO messages (timestamp, sender, recipient, subject, message, priority, thread_id, reply_to) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now(), body["sender"], body["recipient"], body["subject"], body["message"],
             body.get("priority", "normal"), thread_id, reply_to)
        )
        msg_id = cur.lastrowid
        if not thread_id:
            conn.execute("UPDATE messages SET thread_id = ? WHERE id = ?", (msg_id, msg_id))
        conn.commit()
        conn.close()
        return JSONResponse({"status": "sent", "message_id": msg_id})
    else:
        limit = int(request.query_params.get("limit", "50"))
        rows = conn.execute("SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return JSONResponse([dict(r) for r in rows])


@mcp.custom_route("/api/discussions", methods=["GET", "POST"])
async def api_discussions(request):
    conn = get_db()
    if request.method == "POST":
        body = await request.json()
        ts = now()
        cur = conn.execute(
            "INSERT INTO discussions (topic, context, status, created_by, created_at) VALUES (?, ?, 'open', ?, ?)",
            (body["topic"], body.get("context", ""), body.get("instance", "kommandant"), ts)
        )
        disc_id = cur.lastrowid
        conn.execute(
            "INSERT INTO discussion_entries (discussion_id, instance, content, timestamp) VALUES (?, ?, ?, ?)",
            (disc_id, body.get("instance", "kommandant"), body["initial_position"], ts)
        )
        conn.commit()
        conn.close()
        return JSONResponse({"status": "created", "discussion_id": disc_id})
    else:
        status = request.query_params.get("status")
        query = "SELECT d.*, COUNT(e.id) as entry_count FROM discussions d LEFT JOIN discussion_entries e ON d.id = e.discussion_id"
        params = []
        if status:
            query += " WHERE d.status = ?"
            params.append(status)
        query += " GROUP BY d.id ORDER BY d.created_at DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return JSONResponse([dict(r) for r in rows])


@mcp.custom_route("/api/discussions/{discussion_id}", methods=["GET"])
async def api_discussion_detail(request):
    disc_id = request.path_params["discussion_id"]
    conn = get_db()
    disc = conn.execute("SELECT * FROM discussions WHERE id = ?", (disc_id,)).fetchone()
    if not disc:
        conn.close()
        return JSONResponse({"error": "Not found"}, status_code=404)
    entries = conn.execute(
        "SELECT * FROM discussion_entries WHERE discussion_id = ? ORDER BY timestamp ASC",
        (disc_id,)
    ).fetchall()
    conn.close()
    result = dict(disc)
    result["entries"] = [dict(e) for e in entries]
    return JSONResponse(result)


@mcp.custom_route("/api/discussions/{discussion_id}/reply", methods=["POST"])
async def api_discussion_reply(request):
    disc_id = request.path_params["discussion_id"]
    body = await request.json()
    conn = get_db()
    ts = now()
    conn.execute(
        "INSERT INTO discussion_entries (discussion_id, instance, content, timestamp) VALUES (?, ?, ?, ?)",
        (disc_id, body.get("instance", "kommandant"), body["content"], ts)
    )
    conn.commit()
    entries = conn.execute(
        "SELECT * FROM discussion_entries WHERE discussion_id = ? ORDER BY timestamp ASC",
        (disc_id,)
    ).fetchall()
    conn.close()
    return JSONResponse({"status": "added", "total_entries": len(entries), "entries": [dict(e) for e in entries]})


@mcp.custom_route("/api/memory", methods=["GET", "POST"])
async def api_memory_list(request):
    conn = get_db()
    if request.method == "POST":
        body = await request.json()
        ts = now()
        key = body["key"]
        existing = conn.execute("SELECT id FROM shared_memory WHERE key = ?", (key,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE shared_memory SET value=?, category=?, tags=?, updated_by=?, updated_at=? WHERE key=?",
                (body["value"], body.get("category", "general"), body.get("tags", ""), body.get("instance", "kommandant"), ts, key)
            )
        else:
            conn.execute(
                "INSERT INTO shared_memory (key, value, category, tags, updated_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, body["value"], body.get("category", "general"), body.get("tags", ""), body.get("instance", "kommandant"), ts, ts)
            )
        conn.commit()
        conn.close()
        return JSONResponse({"status": "saved", "key": key})
    rows = conn.execute(
        "SELECT * FROM shared_memory ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return JSONResponse([dict(r) for r in rows])


@mcp.custom_route("/api/memory/{key}", methods=["GET"])
async def api_memory_detail(request):
    key = request.path_params["key"]
    conn = get_db()
    row = conn.execute("SELECT * FROM shared_memory WHERE key = ?", (key,)).fetchone()
    conn.close()
    if row:
        return JSONResponse(dict(row))
    return JSONResponse({"error": "Not found"}, status_code=404)


@mcp.custom_route("/api/tasks", methods=["GET", "POST"])
async def api_tasks(request):
    conn = get_db()
    if request.method == "POST":
        body = await request.json()
        ts = now()
        cur = conn.execute(
            "INSERT INTO tasks (title, description, assigned_to, assigned_by, priority, status, deadline, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
            (body["title"], body.get("description", ""), body["assigned_to"],
             body.get("assigned_by", "kommandant"), body.get("priority", "normal"),
             body.get("deadline"), ts, ts)
        )
        conn.commit()
        conn.close()
        return JSONResponse({"status": "created", "task_id": cur.lastrowid})
    else:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, created_at DESC"
        ).fetchall()
        conn.close()
        return JSONResponse([dict(r) for r in rows])


@mcp.custom_route("/dashboard", methods=["GET"])
async def dashboard(request):
    html_path = pathlib.Path(__file__).parent / "dashboard.html"
    html = html_path.read_text(encoding="utf-8")
    return HTMLResponse(html)


# ============================================================
# SILICONFLOW AI SUB-AGENTS (Kimi-K2.5, DeepSeek V3.2, etc.)
# ============================================================

SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
SILICONFLOW_BASE_URL = "https://api.siliconflow.com/v1"

SILICONFLOW_MODELS = {
    "kimi": "moonshotai/Kimi-K2.5",
    "deepseek": "deepseek-ai/DeepSeek-V3.2",
    "glm5": "zai-org/GLM-5",
}

# Auto-discussion: sub-agents join discussions automatically
AI_DISCUSSION_ENABLED = os.environ.get("AI_DISCUSSION_ENABLED", "true").lower() == "true"


async def _ai_auto_discuss(discussion_id: int, topic: str, thread_so_far: str):
    """Automatically query Kimi and DeepSeek to contribute to a discussion."""
    if not AI_DISCUSSION_ENABLED or not SILICONFLOW_API_KEY:
        return

    conn = get_db()
    system = (
        "Te a Claus multi-agent rendszer al-agentje vagy, egy aktív vitában veszel részt. "
        "A rendszert Claude Opus koordinálja, a Kommandant (Tamás) asszisztenseként. "
        "Röviden, lényegre törően szólj hozzá (max 3-4 mondat). "
        "Ha nincs érdemi mondanivalód, írd hogy 'Nincs hozzáfűznivalóm.' "
        "Magyarul válaszolj."
    )
    prompt = f"Vita témája: {topic}\n\nEddigi hozzászólások:\n{thread_so_far}\n\nMi a véleményed? Szólj hozzá."

    for agent_name, model_id in SILICONFLOW_MODELS.items():
        try:
            import httpx
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{SILICONFLOW_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_id,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.7,
                        "max_tokens": 500,
                    },
                )
                data = json.loads(resp.text)

            if isinstance(data, dict) and data.get("choices"):
                content = data["choices"][0].get("message", {}).get("content", "")
                if content and "nincs hozzáfűznivalóm" not in content.lower():
                    ts = now()
                    conn.execute(
                        "INSERT INTO discussion_entries (discussion_id, instance, content, timestamp) VALUES (?, ?, ?, ?)",
                        (discussion_id, agent_name, content, ts)
                    )
                    conn.commit()
                    logger.info("AI %s contributed to discussion #%d", agent_name, discussion_id)
        except Exception as e:
            logger.error("AI auto-discuss failed for %s: %s", agent_name, e)

    conn.close()


@mcp.tool()
async def ai_query(model: str, prompt: str, system_prompt: str = "", temperature: float = 0.7, max_tokens: int = 2000) -> str:
    """Query a SiliconFlow AI sub-agent (Kimi-K2.5, DeepSeek V3.2, or GLM-5).

    Use for research, analysis, translation, summarization, or second opinions.
    These models run on SiliconFlow cloud — no local resources needed.

    Args:
        model: 'kimi' (256k context, vision) or 'deepseek' (fast reasoning) or 'glm5' (205k, coding+agentic) or full model ID
        prompt: The user message / question
        system_prompt: Optional system instruction (default: Claus sub-agent)
        temperature: Creativity 0.0-1.0 (default 0.7)
        max_tokens: Max response length (default 2000)
    """
    if not SILICONFLOW_API_KEY:
        return json.dumps({"error": "SILICONFLOW_API_KEY not set"})

    model_id = SILICONFLOW_MODELS.get(model, model)

    if not system_prompt:
        system_prompt = (
            "Te a Claus multi-agent rendszer al-agentje vagy. "
            "A rendszert Claude Opus koordinálja. "
            "Lényegre törően, magyarul válaszolj, hacsak nem kérnek mást."
        )

    messages = [{"role": "system", "content": system_prompt}]
    messages.append({"role": "user", "content": prompt})

    try:
        import httpx
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                f"{SILICONFLOW_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_id,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            raw = resp.text
            data = json.loads(raw) if isinstance(raw, str) else raw

        if not isinstance(data, dict):
            return json.dumps({"error": f"Unexpected response type: {type(data).__name__}", "raw": str(data)[:500]})

        if "error" in data:
            return json.dumps({"error": data["error"]})

        choices = data.get("choices", [])
        if not choices:
            return json.dumps({"error": "No choices in response", "raw": str(data)[:500]})

        choice = choices[0]
        msg = choice.get("message", {}) if isinstance(choice, dict) else {}
        content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
        usage = data.get("usage", {})

        return json.dumps({
            "model": model_id,
            "response": content,
            "tokens": {
                "prompt": usage.get("prompt_tokens", 0),
                "completion": usage.get("completion_tokens", 0),
            },
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ============================================================
# FILE UPLOAD & PARSING
# ============================================================

UPLOAD_DIR = pathlib.Path(os.environ.get("BRIDGE_UPLOAD_DIR", "/data/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _parse_file_to_text(filepath: pathlib.Path, mime_type: str) -> str:
    """Extract text content from uploaded files."""
    ext = filepath.suffix.lower()
    try:
        if ext == ".docx":
            from docx import Document
            doc = Document(str(filepath))
            return "\n".join(p.text for p in doc.paragraphs)
        elif ext == ".pdf":
            from PyPDF2 import PdfReader
            reader = PdfReader(str(filepath))
            return "\n".join(page.extract_text() or "" for page in reader.pages[:50])
        elif ext in (".txt", ".md", ".csv", ".json", ".xml", ".html", ".py", ".js", ".ts"):
            return filepath.read_text(encoding="utf-8", errors="replace")[:50000]
        else:
            return ""
    except Exception as e:
        logger.error("File parse error %s: %s", filepath.name, e)
        return f"(File parse error: {e})"


def _is_image(mime_type: str) -> bool:
    return mime_type.startswith("image/")


@mcp.tool()
async def upload_file(filename: str, content_base64: str, mime_type: str = "", uploaded_by: str = "unknown") -> str:
    """Upload a file to the Bridge for AI processing.

    Send files from Telegram, Claude app, or CLI for Kimi/DeepSeek to analyze.
    Supports: docx, pdf, txt, md, csv, images (png, jpg, gif, webp).

    Args:
        filename: Original filename (e.g. 'report.pdf')
        content_base64: File content as base64-encoded string
        mime_type: MIME type (auto-detected if empty)
        uploaded_by: Who uploaded (cli-claus, web-claus, kommandant)
    """
    try:
        file_bytes = base64.b64decode(content_base64)
    except Exception:
        return json.dumps({"error": "Invalid base64 content"})

    # Auto-detect mime
    ext = pathlib.Path(filename).suffix.lower()
    if not mime_type:
        mime_map = {
            ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")

    # Save to disk
    filepath = UPLOAD_DIR / f"{int(time.time())}_{filename}"
    filepath.write_bytes(file_bytes)

    # Parse text content
    content_text = _parse_file_to_text(filepath, mime_type) if not _is_image(mime_type) else ""

    # Store in DB
    conn = get_db()
    ts = now()
    b64_for_db = content_base64 if _is_image(mime_type) else ""
    cur = conn.execute(
        "INSERT INTO uploads (filename, mime_type, content_text, content_base64, uploaded_by, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
        (filename, mime_type, content_text[:50000], b64_for_db[:500000], uploaded_by, ts)
    )
    file_id = cur.lastrowid
    conn.commit()
    conn.close()

    return json.dumps({
        "status": "uploaded",
        "file_id": file_id,
        "filename": filename,
        "mime_type": mime_type,
        "text_length": len(content_text),
        "is_image": _is_image(mime_type),
        "hint": "Use ai_task with context referencing file_id to process this file",
    })


@mcp.custom_route("/api/uploads", methods=["GET"])
async def api_uploads(request):
    conn = get_db()
    uploads = conn.execute("SELECT id, filename, mime_type, uploaded_by, uploaded_at, length(content_text) as text_len FROM uploads ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    return JSONResponse([dict(u) for u in uploads])


# ============================================================
# AI TASK EXECUTION (multi-agent with web search)
# ============================================================

WEB_SEARCH_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current information",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
}


async def _web_search(query: str) -> str:
    """Execute a web search via DuckDuckGo HTML."""
    import httpx, re
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            )
            # Extract titles and snippets
            titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)<', resp.text, re.DOTALL)
            results = []
            for i, (title, snippet) in enumerate(zip(titles[:6], snippets[:6])):
                t = re.sub(r'<[^>]+>', '', title).strip()
                s = re.sub(r'<[^>]+>', '', snippet).strip()
                if t or s:
                    results.append(f"[{i+1}] {t}: {s}")
            return "\n".join(results) or "No results found."
    except Exception as e:
        return f"Search error: {e}"


async def _run_agent_with_tools(model_id: str, messages: list, max_rounds: int = 3) -> str:
    """Run an AI agent with optional tool calls (web_search). Returns final text."""
    import httpx
    for _ in range(max_rounds):
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                f"{SILICONFLOW_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_id,
                    "messages": messages,
                    "temperature": 0.5,
                    "max_tokens": 2000,
                    "tools": [WEB_SEARCH_TOOL_DEF],
                },
            )
        data = json.loads(resp.text)
        if not isinstance(data, dict) or "error" in data:
            return f"API error: {data}"

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})

        # If no tool calls, return the text
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return msg.get("content", "")

        # Execute tool calls
        messages.append(msg)
        for tc in tool_calls:
            fn = tc.get("function", {})
            if fn.get("name") == "web_search":
                args = json.loads(fn.get("arguments", "{}"))
                result = await _web_search(args.get("query", ""))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
                logger.info("AI web_search: %s", args.get("query", "")[:80])

    # Max rounds reached, return last content
    return msg.get("content", "(max tool rounds reached)")


async def _execute_ai_task(task_id: int, title: str, description: str, context: str, assigned_by: str):
    """Background execution of a multi-agent AI task. Agents run in PARALLEL."""
    conn = get_db()
    conn.execute("UPDATE ai_tasks SET status = 'running' WHERE id = ?", (task_id,))
    conn.commit()

    roles = {
        "kimi": ("moonshotai/Kimi-K2.5", "Kutató és elemző. Alapos, részletes munkát végzel."),
        "deepseek": ("deepseek-ai/DeepSeek-V3.2", "Kritikus elemző és ellenőr. Logikai hibákat keresel, ellenérveket fogalmazol."),
        "glm5": ("zai-org/GLM-5", "Végrehajtó és kóder. Konkrét megoldásokat, kódot, strukturált outputot adsz. Ha kell, implementálsz."),
    }

    task_prompt = f"FELADAT: {title}\n\nLEÍRÁS: {description}"
    if context:
        task_prompt += f"\n\nKONTEXTUS:\n{context}"

    async def _run_single_agent(agent_name, model_id, role_desc):
        """Run one agent with hybrid tool-call support — called in parallel."""
        import httpx, re
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            system = (
                f"Te a Claus multi-agent rendszer '{agent_name}' al-agentje vagy. "
                f"Szereped: {role_desc} "
                f"A mai dátum: {today}. "
                f"Magyarul válaszolj, lényegre törően de alaposan. "
                f"Ha aktuális információra van szükséged, használd a web_search tool-t."
            )

            # Step 1: Call WITH tools
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(
                    f"{SILICONFLOW_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {SILICONFLOW_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": model_id,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": task_prompt},
                        ],
                        "temperature": 0.5,
                        "max_tokens": 3000,
                        "tools": [WEB_SEARCH_TOOL_DEF],
                    },
                )
                data = json.loads(resp.text)

            content = ""
            search_results = ""
            if not isinstance(data, dict) or not data.get("choices"):
                logger.error("AI task #%d %s: bad API response", task_id, agent_name)
            else:
                msg = data["choices"][0].get("message", {})
                content = msg.get("content", "") or ""
                tool_calls = msg.get("tool_calls")

                # Case A: Proper JSON tool_calls — execute web search
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        if fn.get("name") == "web_search":
                            query = json.loads(fn.get("arguments", "{}")).get("query", "")
                            if query:
                                sr = await _web_search(query)
                                search_results += f"\n[Web search: {query}]\n{sr}\n"
                                logger.info("AI task #%d %s: web_search '%s'", task_id, agent_name, query[:60])

                # Case B: Text-based tool calls — parse search query from text
                tool_markers = ["<|tool_call", "<｜DSML｜", "function_calls>", "tool_calls_section"]
                if not tool_calls and any(m in content for m in tool_markers):
                    # Extract query from text markers
                    queries = re.findall(r'"query"[:\s]*"([^"]+)"', content)
                    for query in queries[:2]:
                        sr = await _web_search(query)
                        search_results += f"\n[Web search: {query}]\n{sr}\n"
                        logger.info("AI task #%d %s: parsed web_search '%s'", task_id, agent_name, query[:60])
                    content = ""  # Clear broken text

            # Step 2: If we got search results, call again WITH those results as context
            if search_results:
                async with httpx.AsyncClient(timeout=180) as client:
                    resp = await client.post(
                        f"{SILICONFLOW_BASE_URL}/chat/completions",
                        headers={"Authorization": f"Bearer {SILICONFLOW_API_KEY}", "Content-Type": "application/json"},
                        json={
                            "model": model_id,
                            "messages": [
                                {"role": "system", "content": f"{role_desc} Magyarul válaszolj, alaposan. Az alábbi web keresési eredményeket használd fel."},
                                {"role": "user", "content": f"{task_prompt}\n\nWEB KERESÉSI EREDMÉNYEK:\n{search_results}"},
                            ],
                            "temperature": 0.5,
                            "max_tokens": 3000,
                        },
                    )
                    data2 = json.loads(resp.text)
                    if isinstance(data2, dict) and data2.get("choices"):
                        content = data2["choices"][0].get("message", {}).get("content", "")

            # Step 3: Fallback if still empty
            if not content or not content.strip():
                logger.warning("AI task #%d %s: empty after tools, final fallback", task_id, agent_name)
                async with httpx.AsyncClient(timeout=180) as client:
                    resp = await client.post(
                        f"{SILICONFLOW_BASE_URL}/chat/completions",
                        headers={"Authorization": f"Bearer {SILICONFLOW_API_KEY}", "Content-Type": "application/json"},
                        json={
                            "model": model_id,
                            "messages": [
                                {"role": "system", "content": f"{role_desc} Magyarul válaszolj, részletesen. NE használj tool-okat."},
                                {"role": "user", "content": task_prompt},
                            ],
                            "temperature": 0.7,
                            "max_tokens": 3000,
                        },
                    )
                    data3 = json.loads(resp.text)
                    if isinstance(data3, dict) and data3.get("choices"):
                        content = data3["choices"][0].get("message", {}).get("content", "")

            ts = now()
            conn.execute(
                "INSERT INTO ai_task_results (task_id, agent, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                (task_id, agent_name, role_desc, content or "(no response)", ts)
            )
            conn.commit()
            logger.info("AI task #%d: %s done (%d chars)", task_id, agent_name, len(content or ""))
        except Exception as e:
            ts = now()
            conn.execute(
                "INSERT INTO ai_task_results (task_id, agent, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                (task_id, agent_name, role_desc, f"ERROR: {e}", ts)
            )
            conn.commit()
            logger.error("AI task #%d %s failed: %s", task_id, agent_name, e)

    # Run all agents IN PARALLEL
    await asyncio.gather(
        *[_run_single_agent(name, mid, rdesc) for name, (mid, rdesc) in roles.items()]
    )

    # Synthesis by Kimi
    try:
        results = conn.execute(
            "SELECT agent, content FROM ai_task_results WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()
        parts = "\n\n".join(f"[{r['agent']}]:\n{r['content']}" for r in results)
        system = (
            "Te a koordinátor vagy. Az al-agentek elvégezték a feladatot. "
            "Készíts tömör szintézist az eredményeikből: mi az egyetértés, hol térnek el, és mi a végső ajánlás. "
            "Magyarul, strukturáltan."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"FELADAT: {title}\n\nAGENT EREDMÉNYEK:\n{parts}"},
        ]
        synthesis = await _run_agent_with_tools("moonshotai/Kimi-K2.5", messages)
        ts = now()
        conn.execute(
            "INSERT INTO ai_task_results (task_id, agent, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            (task_id, "szintézis", "Koordinátori összefoglaló", synthesis, ts)
        )
        conn.commit()
    except Exception as e:
        logger.error("AI task #%d synthesis failed: %s", task_id, e)

    conn.execute("UPDATE ai_tasks SET status = 'completed', completed_at = ? WHERE id = ?", (now(), task_id))
    conn.commit()
    conn.close()
    logger.info("AI task #%d completed", task_id)


@mcp.tool()
async def ai_task(title: str, description: str, context: str = "", file_id: int = 0, assigned_by: str = "unknown") -> str:
    """Create and execute a multi-agent AI task. Kimi and DeepSeek work on it with web search, then a synthesis is generated.

    Use for research, document analysis, fact-checking, or any task that benefits from multiple AI perspectives.
    Results appear on the Bridge dashboard.

    Args:
        title: Short task title
        description: Detailed task description / instructions
        context: Optional document text or background context
        file_id: Optional uploaded file ID (from upload_file) to include as context
        assigned_by: Who created the task (cli-claus, web-claus, kommandant)
    """
    # Pull in uploaded file content if file_id provided
    if file_id:
        conn = get_db()
        f = conn.execute("SELECT filename, mime_type, content_text, content_base64 FROM uploads WHERE id = ?", (file_id,)).fetchone()
        conn.close()
        if f:
            if f["content_text"]:
                context = f"[Feltöltött fájl: {f['filename']}]\n\n{f['content_text']}\n\n{context}"
            elif f["content_base64"]:
                context = f"[Feltöltött kép: {f['filename']} — base64 kép az agentek számára elérhető]\n\n{context}"
    if not SILICONFLOW_API_KEY:
        return json.dumps({"error": "SILICONFLOW_API_KEY not set"})

    conn = get_db()
    ts = now()
    cur = conn.execute(
        "INSERT INTO ai_tasks (title, description, context, assigned_by, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
        (title, description, context[:10000], assigned_by, ts)
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Execute in background thread
    def _run():
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_execute_ai_task(task_id, title, description, context[:10000], assigned_by))
        loop.close()
    threading.Thread(target=_run, daemon=True).start()

    return json.dumps({"status": "task_created", "task_id": task_id, "message": "Kimi + DeepSeek dolgoznak rajta. Eredmény a dashboardon."})


@mcp.custom_route("/api/upload", methods=["POST"])
async def api_upload(request):
    body = await request.json()
    filename = body.get("filename", "unknown")
    content_base64 = body.get("content_base64", "")
    mime_type = body.get("mime_type", "")
    uploaded_by = body.get("uploaded_by", "kommandant")

    try:
        file_bytes = base64.b64decode(content_base64)
    except Exception:
        return JSONResponse({"error": "Invalid base64"}, status_code=400)

    ext = pathlib.Path(filename).suffix.lower()
    if not mime_type:
        mime_map = {
            ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")

    filepath = UPLOAD_DIR / f"{int(time.time())}_{filename}"
    filepath.write_bytes(file_bytes)

    content_text = _parse_file_to_text(filepath, mime_type) if not _is_image(mime_type) else ""
    b64_for_db = content_base64 if _is_image(mime_type) else ""

    conn = get_db()
    ts = now()
    cur = conn.execute(
        "INSERT INTO uploads (filename, mime_type, content_text, content_base64, uploaded_by, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
        (filename, mime_type, content_text[:50000], b64_for_db[:500000], uploaded_by, ts)
    )
    file_id = cur.lastrowid
    conn.commit()
    conn.close()
    return JSONResponse({"status": "uploaded", "file_id": file_id, "filename": filename, "text_length": len(content_text)})


@mcp.custom_route("/api/ai_tasks", methods=["GET", "POST"])
async def api_ai_tasks(request):
    if request.method == "POST":
        body = await request.json()
        title = body.get("title", "")
        description = body.get("description", "")
        context = body.get("context", "")
        file_id = body.get("file_id", 0)
        assigned_by = body.get("assigned_by", "kommandant")

        # Pull in uploaded file if file_id given
        if file_id:
            conn2 = get_db()
            f = conn2.execute("SELECT filename, content_text FROM uploads WHERE id = ?", (file_id,)).fetchone()
            conn2.close()
            if f and f["content_text"]:
                context = f"[Feltöltött fájl: {f['filename']}]\n\n{f['content_text']}\n\n{context}"

        conn = get_db()
        ts = now()
        cur = conn.execute(
            "INSERT INTO ai_tasks (title, description, context, assigned_by, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
            (title, description, context[:10000], assigned_by, ts)
        )
        task_id = cur.lastrowid
        conn.commit()
        conn.close()

        # Execute in background
        def _run():
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_execute_ai_task(task_id, title, description, context[:10000], assigned_by))
            loop.close()
        threading.Thread(target=_run, daemon=True).start()

        return JSONResponse({"status": "created", "task_id": task_id})

    conn = get_db()
    tasks = conn.execute("SELECT * FROM ai_tasks ORDER BY id DESC LIMIT 20").fetchall()
    result = []
    for t in tasks:
        results = conn.execute(
            "SELECT agent, role, content, timestamp FROM ai_task_results WHERE task_id = ? ORDER BY id",
            (t["id"],)
        ).fetchall()
        result.append({**dict(t), "results": [dict(r) for r in results]})
    conn.close()
    return JSONResponse(result)


@mcp.custom_route("/api/ai_tasks/{task_id}/export", methods=["GET"])
async def api_ai_task_export(request):
    """Export AI task results as a .docx document."""
    task_id = request.path_params["task_id"]
    conn = get_db()
    task = conn.execute("SELECT * FROM ai_tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        conn.close()
        return JSONResponse({"error": "Task not found"}, status_code=404)

    results = conn.execute(
        "SELECT agent, role, content, timestamp FROM ai_task_results WHERE task_id = ? ORDER BY id",
        (task_id,)
    ).fetchall()
    conn.close()

    try:
        from docx import Document as DocxDocument
        from docx.shared import Pt, RGBColor
        from io import BytesIO

        doc = DocxDocument()
        doc.add_heading(task["title"], level=1)
        doc.add_paragraph(f'Feladat: {task["description"]}')
        doc.add_paragraph(f'Kiadta: {task["assigned_by"]} | Dátum: {task["created_at"][:10]} | Státusz: {task["status"]}')
        doc.add_paragraph("—" * 40)

        agent_names = {"kimi": "Kimi-K2.5 (Kutató)", "deepseek": "DeepSeek V3.2 (Kritikus)", "glm5": "GLM-5 (Végrehajtó)", "szintézis": "Koordinátori Szintézis"}
        for r in results:
            name = agent_names.get(r["agent"], r["agent"].upper())
            doc.add_heading(name, level=2)
            for para_text in (r["content"] or "(nincs tartalom)").split("\n"):
                if para_text.strip():
                    doc.add_paragraph(para_text.strip())

        doc.add_paragraph("—" * 40)
        doc.add_paragraph("Készítette: Claus Multi-Agent Rendszer (Kimi-K2.5 + DeepSeek V3.2 + GLM-5)")

        buf = BytesIO()
        doc.save(buf)
        buf.seek(0)

        from starlette.responses import Response
        filename = f"claus_ai_task_{task_id}.docx"
        return Response(
            content=buf.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ============================================================
# GOOGLE API — Gmail + Calendar Capture
# ============================================================

GOOGLE_TOKEN_JSON = os.environ.get("GOOGLE_TOKEN_JSON", "")
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
]
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Capture config (env vars)
GMAIL_POLL_INTERVAL = int(os.environ.get("GMAIL_POLL_INTERVAL", "300"))
CALENDAR_POLL_INTERVAL = int(os.environ.get("CALENDAR_POLL_INTERVAL", "900"))
CALENDAR_REMINDER_MINUTES = int(os.environ.get("CALENDAR_REMINDER_MINUTES", "30"))
MORNING_BRIEFING_HOUR = int(os.environ.get("MORNING_BRIEFING_HOUR", "7"))
IGNORE_SENDERS = [s.strip().lower() for s in os.environ.get("IGNORE_SENDERS", "newsletter,noreply,no-reply,marketing").split(",") if s.strip()]
URGENT_SENDERS = [s.strip().lower() for s in os.environ.get("URGENT_SENDERS", "").split(",") if s.strip()]
URGENT_KEYWORDS = [k.strip().lower() for k in os.environ.get("URGENT_KEYWORDS", "urgent,sürgős,asap,critical,azonnal,fontos").split(",") if k.strip()]

# Runtime state for capture polling
_capture_state = {
    "gmail_history_id": None,
    "calendar_reminded": set(),
    "last_briefing_date": None,
    "gmail_service": None,
    "calendar_service": None,
    "capture_running": False,
}


def _init_google_services():
    """Initialize Google API services from token JSON env var."""
    if not GOOGLE_TOKEN_JSON:
        logger.info("GOOGLE_TOKEN_JSON not set — capture disabled")
        return False
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        # Support both raw JSON and base64-encoded JSON
        try:
            token_data = json.loads(GOOGLE_TOKEN_JSON)
        except json.JSONDecodeError:
            token_data = json.loads(base64.b64decode(GOOGLE_TOKEN_JSON).decode())
        creds = Credentials.from_authorized_user_info(token_data, GOOGLE_SCOPES)

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Update env var in memory with refreshed token
            logger.info("Google token refreshed")

        _capture_state["gmail_service"] = build("gmail", "v1", credentials=creds, cache_discovery=False)
        _capture_state["calendar_service"] = build("calendar", "v3", credentials=creds, cache_discovery=False)

        profile = _capture_state["gmail_service"].users().getProfile(userId="me").execute()
        logger.info("Google services initialized: %s", profile.get("emailAddress"))
        return True
    except Exception as e:
        logger.error("Google init failed: %s", e)
        return False


def _extract_attachments(payload: dict) -> list:
    """Extract attachment metadata from Gmail message payload (format='full')."""
    attachments = []
    _walk_parts(payload.get("parts", []), attachments)
    return attachments


def _walk_parts(parts: list, out: list):
    """Recursively walk MIME parts to find attachments."""
    for part in parts:
        filename = part.get("filename", "")
        if filename:
            body = part.get("body", {})
            out.append({
                "filename": filename,
                "mime_type": part.get("mimeType", "application/octet-stream"),
                "size": body.get("size", 0),
                "attachment_id": body.get("attachmentId", ""),
            })
        if part.get("parts"):
            _walk_parts(part["parts"], out)


def _categorize_email(sender: str, subject: str) -> str:
    """Categorize email: urgent / important / normal / ignore."""
    sender_lower = sender.lower()
    subject_lower = subject.lower()

    for pattern in IGNORE_SENDERS:
        if pattern in sender_lower:
            return "ignore"

    for pattern in URGENT_SENDERS:
        if pattern in sender_lower:
            return "urgent"

    for kw in URGENT_KEYWORDS:
        if kw in subject_lower:
            return "urgent"

    return "normal"


async def _telegram_push(text: str):
    """Send push notification to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text[:4000],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
    except Exception as e:
        logger.error("Telegram push failed: %s", e)


async def _telegram_push_document(message_id: str, attachment: dict, caption: str = ""):
    """Download Gmail attachment and send to Telegram as document."""
    svc = _capture_state.get("gmail_service")
    if not svc or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        att_id = attachment.get("attachment_id", "")
        if not att_id:
            return
        att_data = svc.users().messages().attachments().get(
            userId="me", messageId=message_id, id=att_id
        ).execute()
        file_bytes = base64.urlsafe_b64decode(att_data["data"])
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption[:1024] if caption else "",
                    "parse_mode": "HTML",
                },
                files={
                    "document": (attachment["filename"], file_bytes, attachment["mime_type"]),
                },
            )
            if resp.status_code != 200:
                logger.error("Telegram sendDocument failed: %s", resp.text[:200])
    except Exception as e:
        logger.error("Telegram document push failed: %s", e)


def _bridge_capture_event(subject: str, message: str, priority: str = "normal"):
    """Write a capture event directly into the Bridge DB."""
    conn = get_db()
    ts = now()
    cur = conn.execute(
        "INSERT INTO messages (timestamp, sender, recipient, subject, message, priority, thread_id, reply_to) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
        (ts, "capture-daemon", "kommandant", subject, message, priority)
    )
    msg_id = cur.lastrowid
    conn.execute("UPDATE messages SET thread_id = ? WHERE id = ?", (msg_id, msg_id))
    conn.commit()
    conn.close()


# ============================================================
# CAPTURE MCP TOOLS
# ============================================================

@mcp.tool()
async def capture_gmail_poll(max_results: int = 10) -> str:
    """Poll Gmail for new unread emails. Returns categorized capture events.

    Args:
        max_results: Max emails to fetch (default 10)
    """
    svc = _capture_state.get("gmail_service")
    if not svc:
        return json.dumps({"error": "Gmail not initialized. Set GOOGLE_TOKEN_JSON env var."})

    try:
        history_id = _capture_state.get("gmail_history_id")

        if history_id is None:
            # Initial fetch — recent unread
            results = svc.users().messages().list(
                userId="me", q="is:unread category:primary", maxResults=max_results
            ).execute()
            msg_stubs = results.get("messages", [])
        else:
            # Incremental via History API
            try:
                history = svc.users().history().list(
                    userId="me", startHistoryId=history_id,
                    historyTypes=["messageAdded"], labelId="INBOX"
                ).execute()
                msg_stubs = []
                seen = set()
                for record in history.get("history", []):
                    for added in record.get("messagesAdded", []):
                        m = added.get("message", {})
                        mid = m.get("id")
                        if mid and mid not in seen and "INBOX" in m.get("labelIds", []):
                            msg_stubs.append({"id": mid})
                            seen.add(mid)
            except Exception:
                # History expired, fall back to initial
                results = svc.users().messages().list(
                    userId="me", q="is:unread category:primary", maxResults=max_results
                ).execute()
                msg_stubs = results.get("messages", [])

        # Update history watermark
        profile = svc.users().getProfile(userId="me").execute()
        _capture_state["gmail_history_id"] = int(profile.get("historyId", 0))

        events = []
        for stub in msg_stubs[:max_results]:
            try:
                msg = svc.users().messages().get(
                    userId="me", id=stub["id"], format="full"
                ).execute()
                payload = msg.get("payload", {})
                headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
                sender_raw = headers.get("From", "unknown")
                subject = headers.get("Subject", "(no subject)")
                date_str = headers.get("Date", "")
                snippet = msg.get("snippet", "")
                sender_name, sender_email = parseaddr(sender_raw)
                priority = _categorize_email(sender_raw, subject)

                if priority == "ignore":
                    continue

                # Extract attachments
                attachments = _extract_attachments(payload)
                att_info = [{"filename": a["filename"], "mime_type": a["mime_type"],
                             "size": a["size"]} for a in attachments]

                event = {
                    "message_id": stub["id"],
                    "subject": subject,
                    "sender": sender_name or sender_email,
                    "sender_email": sender_email,
                    "snippet": snippet,
                    "date": date_str,
                    "priority": priority,
                    "attachments": att_info,
                }
                events.append(event)

                # Format attachment line for Bridge/Telegram
                att_line = ""
                if attachments:
                    att_names = ", ".join(a["filename"] for a in attachments)
                    att_line = f"\n📎 Csatolmányok ({len(attachments)}): {att_names}"

                # Write to Bridge DB
                _bridge_capture_event(
                    f"📧 Gmail: {subject}",
                    f"From: {sender_name or sender_email} <{sender_email}>\nSubject: {subject}\nDate: {date_str}\n\n{snippet}{att_line}",
                    priority
                )

                # Telegram push for urgent/important
                if priority in ("urgent", "important"):
                    await _telegram_push(
                        f"🟠 <b>GMAIL</b> — {subject}\n\n"
                        f"<b>From:</b> {sender_name or sender_email}\n"
                        f"{snippet}"
                        + (f"\n\n📎 <b>Csatolmányok:</b> {', '.join(a['filename'] for a in attachments)}" if attachments else "")
                    )
                    # Forward attachments to Telegram
                    for att in attachments:
                        await _telegram_push_document(
                            stub["id"], att,
                            caption=f"📎 <b>{att['filename']}</b>\n{subject}"
                        )
            except Exception as e:
                logger.error("Failed to process message %s: %s", stub.get("id"), e)

        return json.dumps({"count": len(events), "events": events}, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def capture_calendar_poll() -> str:
    """Poll Google Calendar for upcoming events (reminders + briefing).

    Returns reminders for events starting within the configured reminder window,
    and a morning briefing if it's briefing time.
    """
    svc = _capture_state.get("calendar_service")
    if not svc:
        return json.dumps({"error": "Calendar not initialized. Set GOOGLE_TOKEN_JSON env var."})

    try:
        events_out = []
        utc_now = datetime.now(timezone.utc)

        # --- Reminders ---
        reminder_window = utc_now + timedelta(minutes=CALENDAR_REMINDER_MINUTES)
        result = svc.events().list(
            calendarId="primary",
            timeMin=utc_now.isoformat(),
            timeMax=reminder_window.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=10
        ).execute()

        reminded = _capture_state.get("calendar_reminded", set())

        for item in result.get("items", []):
            event_id = item.get("id", "")
            if event_id in reminded:
                continue

            start_info = item.get("start", {})
            dt_str = start_info.get("dateTime", start_info.get("date", ""))
            if "T" in dt_str:
                start_dt = datetime.fromisoformat(dt_str).astimezone(timezone.utc)
                minutes_until = max(0, int((start_dt - utc_now).total_seconds() / 60))
            else:
                minutes_until = -1  # all-day

            summary = item.get("summary", "(no title)")
            location = item.get("location", "")
            meet_link = item.get("hangoutLink", "")
            attendees = [a.get("email", "") for a in item.get("attendees", [])[:5]]

            event_out = {
                "type": "reminder",
                "event_id": event_id,
                "summary": summary,
                "start": dt_str,
                "minutes_until": minutes_until,
                "location": location,
                "meet_link": meet_link,
                "attendees": attendees,
            }
            events_out.append(event_out)
            reminded.add(event_id)

            # Bridge + Telegram
            priority = "urgent" if minutes_until <= 10 else "important"
            lines = [f"Esemény: {summary}", f"Kezdés: {dt_str} ({minutes_until} perc múlva)"]
            if location: lines.append(f"Helyszín: {location}")
            if meet_link: lines.append(f"Link: {meet_link}")

            _bridge_capture_event(f"📅 {minutes_until} perc múlva: {summary}", "\n".join(lines), priority)
            await _telegram_push(
                f"⏰ <b>NAPTÁR</b> — {minutes_until} perc múlva\n\n"
                f"<b>{summary}</b>\n"
                + (f"📍 {location}\n" if location else "")
                + (f"🔗 <a href=\"{meet_link}\">Csatlakozás</a>" if meet_link else "")
            )

        _capture_state["calendar_reminded"] = reminded

        # --- Morning briefing ---
        local_now = datetime.now()
        today_str = local_now.strftime("%Y-%m-%d")

        if local_now.hour == MORNING_BRIEFING_HOUR and _capture_state.get("last_briefing_date") != today_str:
            start_of_day = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + timedelta(days=1)

            day_result = svc.events().list(
                calendarId="primary",
                timeMin=start_of_day.isoformat(),
                timeMax=end_of_day.isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=20
            ).execute()

            day_items = day_result.get("items", [])
            _capture_state["last_briefing_date"] = today_str

            if day_items:
                event_lines = []
                for item in day_items:
                    s = item.get("start", {})
                    t = s.get("dateTime", s.get("date", "?"))
                    if "T" in t: t = t[11:16]
                    event_lines.append(f"  {t} — {item.get('summary', '?')}")

                briefing_body = f"Mai nap: {len(day_items)} esemény:\n\n" + "\n".join(event_lines)
            else:
                briefing_body = "Tiszta nap — nincsenek események a naptárban."

            events_out.append({"type": "briefing", "date": today_str, "event_count": len(day_items)})
            _bridge_capture_event(f"🌅 Reggeli briefing — {today_str}", briefing_body, "normal")
            await _telegram_push(f"🌅 <b>Reggeli briefing</b> — {today_str}\n\n{briefing_body}")

        return json.dumps({"count": len(events_out), "events": events_out}, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def capture_send_email(to: str, subject: str, body: str, body_type: str = "plain") -> str:
    """Send an email via Gmail API.

    Args:
        to: Recipient email address
        subject: Email subject
        body: Email body content
        body_type: 'plain' or 'html' (default: plain)
    """
    svc = _capture_state.get("gmail_service")
    if not svc:
        return json.dumps({"error": "Gmail not initialized. Set GOOGLE_TOKEN_JSON env var."})

    try:
        msg = MIMEText(body, body_type)
        msg["to"] = to
        msg["subject"] = subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        return json.dumps({"status": "sent", "message_id": result.get("id")})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def capture_inbox(limit: int = 10, query: str = "is:unread category:primary") -> str:
    """Read Gmail inbox messages without triggering capture events. For browsing email.

    Args:
        limit: Max messages (default 10)
        query: Gmail search query (default: unread primary)
    """
    svc = _capture_state.get("gmail_service")
    if not svc:
        return json.dumps({"error": "Gmail not initialized."})

    try:
        results = svc.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        messages = []
        for stub in results.get("messages", []):
            msg = svc.users().messages().get(
                userId="me", id=stub["id"], format="full"
            ).execute()
            payload = msg.get("payload", {})
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
            sender_name, sender_email = parseaddr(headers.get("From", ""))
            attachments = _extract_attachments(payload)
            att_info = [{"filename": a["filename"], "mime_type": a["mime_type"],
                         "size": a["size"]} for a in attachments]
            messages.append({
                "id": stub["id"],
                "subject": headers.get("Subject", ""),
                "from": sender_name or sender_email,
                "from_email": sender_email,
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
                "attachments": att_info,
            })
        return json.dumps({"count": len(messages), "messages": messages}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def capture_status() -> str:
    """Get capture daemon status — is Gmail/Calendar connected, last poll info."""
    gmail_ok = _capture_state.get("gmail_service") is not None
    cal_ok = _capture_state.get("calendar_service") is not None
    return json.dumps({
        "gmail_connected": gmail_ok,
        "calendar_connected": cal_ok,
        "gmail_history_id": _capture_state.get("gmail_history_id"),
        "capture_loop_running": _capture_state.get("capture_running", False),
        "calendar_reminded_count": len(_capture_state.get("calendar_reminded", set())),
        "last_briefing_date": _capture_state.get("last_briefing_date"),
        "config": {
            "gmail_poll_interval": GMAIL_POLL_INTERVAL,
            "calendar_poll_interval": CALENDAR_POLL_INTERVAL,
            "calendar_reminder_minutes": CALENDAR_REMINDER_MINUTES,
            "morning_briefing_hour": MORNING_BRIEFING_HOUR,
        }
    })


# ============================================================
# AUTO-POLLING BACKGROUND LOOP
# ============================================================

async def _capture_loop():
    """Background loop that auto-polls Gmail and Calendar."""
    if not _capture_state.get("gmail_service"):
        logger.info("Capture loop not started — Google services not initialized")
        return

    _capture_state["capture_running"] = True
    logger.info("Capture loop started: Gmail=%ds, Calendar=%ds", GMAIL_POLL_INTERVAL, CALENDAR_POLL_INTERVAL)

    _bridge_capture_event(
        "🟢 Capture Daemon elindult (Railway)",
        f"Gmail polling: {GMAIL_POLL_INTERVAL}s\nCalendar polling: {CALENDAR_POLL_INTERVAL}s\nCalendar reminder: {CALENDAR_REMINDER_MINUTES} perccel előtte",
        "info"
    )

    gmail_counter = 0
    calendar_counter = 0
    tick = 60  # check every 60 seconds

    while _capture_state.get("capture_running"):
        try:
            gmail_counter += tick
            calendar_counter += tick

            if gmail_counter >= GMAIL_POLL_INTERVAL:
                gmail_counter = 0
                await capture_gmail_poll()

            if calendar_counter >= CALENDAR_POLL_INTERVAL:
                calendar_counter = 0
                await capture_calendar_poll()

        except Exception as e:
            logger.error("Capture loop error: %s", e)

        await asyncio.sleep(tick)


def _start_capture_background():
    """Start capture loop in a background thread with its own event loop."""
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_capture_loop())

    if _capture_state.get("gmail_service"):
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        logger.info("Capture background thread started")


# ============================================================
# STARTUP
# ============================================================

def _migrate_db_to_volume():
    """If BRIDGE_DB_PATH points to a volume (e.g. /data/bridge.db) but the file
    doesn't exist yet, copy the old working-directory bridge.db there."""
    import shutil
    volume_db = DB_PATH  # e.g. /data/bridge.db
    old_db = os.path.join(os.path.dirname(__file__), "bridge.db")

    if volume_db == old_db or volume_db == "bridge.db":
        return  # no migration needed, same path

    if os.path.exists(volume_db):
        logger.info("Volume DB already exists: %s", volume_db)
        return

    # Ensure volume directory exists
    volume_dir = os.path.dirname(volume_db)
    if volume_dir:
        os.makedirs(volume_dir, exist_ok=True)

    if os.path.exists(old_db):
        shutil.copy2(old_db, volume_db)
        logger.info("Migrated DB: %s -> %s", old_db, volume_db)
    else:
        logger.info("No old DB found at %s, starting fresh at %s", old_db, volume_db)


_migrate_db_to_volume()
init_db()
_init_google_services()
_start_capture_background()

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8003))
    )
