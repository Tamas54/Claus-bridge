import os
import sqlite3
from datetime import datetime
from typing import List

DB_PATH = os.environ.get("BRIDGE_DB_PATH", "bridge.db")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_table():
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pyramid_agent_rag (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            entry_id TEXT NOT NULL,
            task_title TEXT NOT NULL,
            category TEXT DEFAULT 'result',
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rag_agent ON pyramid_agent_rag(agent_id)
    """)
    conn.commit()
    conn.close()


_ensure_table()


def load_agent_rag(agent_id: str) -> List[dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM pyramid_agent_rag WHERE agent_id = ? ORDER BY timestamp DESC",
        (agent_id,)
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["entry_id"], "task_title": r["task_title"],
            "category": r["category"], "content": r["content"], "timestamp": r["timestamp"]
        }
        for r in rows
    ]


def add_to_agent_rag(agent_id: str, content: str, task_title: str, category: str = "result") -> dict:
    conn = _get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM pyramid_agent_rag WHERE agent_id = ?", (agent_id,)).fetchone()["c"]
    entry_id = f"rag_{count+1:04d}"
    ts = datetime.utcnow().isoformat() + "Z"
    conn.execute(
        "INSERT INTO pyramid_agent_rag (agent_id, entry_id, task_title, category, content, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (agent_id, entry_id, task_title, category, content[:5000], ts)
    )
    conn.commit()
    conn.close()
    return {"id": entry_id, "task_title": task_title, "category": category, "content": content[:5000], "timestamp": ts}


def search_agent_rag(agent_id: str, query: str, max_results: int = 5) -> List[dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM pyramid_agent_rag WHERE agent_id = ?", (agent_id,)
    ).fetchall()
    conn.close()
    query_lower = query.lower()
    keywords = query_lower.split()

    scored = []
    for r in rows:
        text = (r["content"] + " " + r["task_title"]).lower()
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scored.append((score, {
                "id": r["entry_id"], "task_title": r["task_title"],
                "category": r["category"], "content": r["content"], "timestamp": r["timestamp"]
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored[:max_results]]


def get_agent_rag_summary(agent_id: str, max_items: int = 10) -> str:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM pyramid_agent_rag WHERE agent_id = ? ORDER BY timestamp DESC LIMIT ?",
        (agent_id, max_items)
    ).fetchall()
    conn.close()
    if not rows:
        return ""

    lines = [f"## Saját korábbi munkáid ({agent_id} RAG)"]
    for r in rows:
        lines.append(f"- [{r['task_title']}] {r['content'][:200]}...")
    return "\n".join(lines)


def cleanup_agent_rag(agent_id: str, max_entries: int = 200):
    conn = _get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM pyramid_agent_rag WHERE agent_id = ?", (agent_id,)).fetchone()["c"]
    if count > max_entries:
        conn.execute(
            "DELETE FROM pyramid_agent_rag WHERE agent_id = ? AND id NOT IN "
            "(SELECT id FROM pyramid_agent_rag WHERE agent_id = ? ORDER BY timestamp DESC LIMIT ?)",
            (agent_id, agent_id, max_entries)
        )
        conn.commit()
    conn.close()
