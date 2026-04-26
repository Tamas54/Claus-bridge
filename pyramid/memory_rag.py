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


def get_agent_rag_summary(agent_id: str, max_items: int = 25) -> str:
    """Recency-first RAG summary for a single agent."""
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


def get_smart_rag_summary(agent_id: str, query: str = "", max_items: int = 25) -> str:
    """Hybrid RAG: query-relevant hits first, then recency fill.

    If query is empty, equivalent to get_agent_rag_summary. With a query,
    the most relevant N entries (by keyword match) come first, then the
    remaining slots are filled with the latest entries that weren't
    already included. Useful when the agent has hundreds of past entries
    and only a small subset is relevant to the current task.
    """
    if not query or not query.strip():
        return get_agent_rag_summary(agent_id, max_items=max_items)

    relevant = search_agent_rag(agent_id, query, max_results=min(15, max_items))
    seen_ids = {r["id"] for r in relevant}

    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM pyramid_agent_rag WHERE agent_id = ? ORDER BY timestamp DESC LIMIT ?",
        (agent_id, max_items * 2)
    ).fetchall()
    conn.close()
    recent = [
        {"id": r["entry_id"], "task_title": r["task_title"],
         "category": r["category"], "content": r["content"], "timestamp": r["timestamp"]}
        for r in rows if r["entry_id"] not in seen_ids
    ]

    combined = (relevant + recent)[:max_items]
    if not combined:
        return ""

    lines = [f"## Saját korábbi munkáid ({agent_id} RAG, relevancia + frissesség sorrendben)"]
    for r in combined:
        lines.append(f"- [{r['task_title']}] {r['content'][:200]}...")
    return "\n".join(lines)


def get_combined_rag_summary(max_per_agent: int = 12, query: str = "") -> str:
    """Multi-agent RAG view — Kimi + DeepSeek + GLM5 history in one block.

    Used by orchestrators (Feldwebel, future cross-agent dashboards) so that
    a single instance can see what the entire rizsrakéta flotta has worked
    on without importing each agent's view separately.
    """
    sections = []
    for agent in ("kimi", "deepseek", "glm5"):
        if query and query.strip():
            relevant = search_agent_rag(agent, query, max_results=min(8, max_per_agent))
            seen = {r["id"] for r in relevant}
            recent_rows = load_agent_rag(agent)
            tail = [r for r in recent_rows if r["id"] not in seen][:max_per_agent - len(relevant)]
            entries = relevant + tail
        else:
            entries = load_agent_rag(agent)[:max_per_agent]
        if not entries:
            continue
        agent_block = [f"### {agent.upper()} korábbi munkái:"]
        for r in entries:
            agent_block.append(f"- [{r['task_title']}] {r['content'][:200]}...")
        sections.append("\n".join(agent_block))

    if not sections:
        return ""
    header = "## RIZSRAKÉTA RAG — közös al-agent munkanapló (Kimi + DeepSeek + GLM5)"
    return header + "\n\n" + "\n\n".join(sections)


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
