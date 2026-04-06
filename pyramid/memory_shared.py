import json
import os
import sqlite3
from datetime import datetime
from typing import List, Optional

DB_PATH = os.environ.get("BRIDGE_DB_PATH", "bridge.db")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_table():
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pyramid_shared_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            added_by TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            relevance_score REAL DEFAULT 0.5,
            endorsed_by TEXT DEFAULT '[]'
        )
    """)
    conn.commit()
    conn.close()


_ensure_table()


def load_shared_memory() -> List[dict]:
    conn = _get_db()
    rows = conn.execute("SELECT * FROM pyramid_shared_memory ORDER BY timestamp DESC").fetchall()
    conn.close()
    return [
        {
            "id": r["entry_id"],
            "category": r["category"],
            "content": r["content"],
            "added_by": r["added_by"],
            "timestamp": r["timestamp"],
            "relevance_score": r["relevance_score"],
            "endorsed_by": json.loads(r["endorsed_by"] or "[]"),
        }
        for r in rows
    ]


def add_to_shared_memory(content: str, category: str, added_by: str) -> dict:
    conn = _get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM pyramid_shared_memory").fetchone()["c"]
    entry_id = f"pk_{count+1:04d}"
    ts = datetime.utcnow().isoformat() + "Z"
    conn.execute(
        "INSERT INTO pyramid_shared_memory (entry_id, category, content, added_by, timestamp, relevance_score, endorsed_by) "
        "VALUES (?, ?, ?, ?, ?, 0.5, '[]')",
        (entry_id, category, content[:2000], added_by, ts)
    )
    conn.commit()
    conn.close()
    return {
        "id": entry_id, "category": category, "content": content[:2000],
        "added_by": added_by, "timestamp": ts, "relevance_score": 0.5, "endorsed_by": []
    }


def endorse_entry(entry_id: str, endorser: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("SELECT * FROM pyramid_shared_memory WHERE entry_id = ?", (entry_id,)).fetchone()
    if not row:
        conn.close()
        return None
    endorsed = json.loads(row["endorsed_by"] or "[]")
    if endorser not in endorsed:
        endorsed.append(endorser)
        score = min(1.0, 0.5 + len(endorsed) * 0.15)
        conn.execute(
            "UPDATE pyramid_shared_memory SET endorsed_by = ?, relevance_score = ? WHERE entry_id = ?",
            (json.dumps(endorsed), score, entry_id)
        )
        conn.commit()
    conn.close()
    return {
        "id": row["entry_id"], "category": row["category"], "content": row["content"],
        "added_by": row["added_by"], "timestamp": row["timestamp"],
        "relevance_score": min(1.0, 0.5 + len(endorsed) * 0.15), "endorsed_by": endorsed
    }


def get_shared_memory_summary(max_items: int = 20) -> str:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM pyramid_shared_memory ORDER BY relevance_score DESC, timestamp DESC LIMIT ?",
        (max_items,)
    ).fetchall()
    conn.close()
    if not rows:
        return ""

    lines = ["## Közös tudásbázis (Pyramid Shared Memory)"]
    for r in rows:
        endorsers = ", ".join(json.loads(r["endorsed_by"] or "[]"))
        endorser_info = f" [jóváhagyta: {endorsers}]" if endorsers else ""
        lines.append(
            f"- [{r['category']}] {r['content'][:300]} "
            f"(forrás: {r['added_by']}{endorser_info})"
        )
    return "\n".join(lines)


def search_shared_memory(query: str, max_results: int = 10) -> List[dict]:
    conn = _get_db()
    rows = conn.execute("SELECT * FROM pyramid_shared_memory").fetchall()
    conn.close()
    query_lower = query.lower()
    keywords = query_lower.split()

    scored = []
    for r in rows:
        text = (r["content"] + " " + r["category"]).lower()
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scored.append((score, {
                "id": r["entry_id"], "category": r["category"], "content": r["content"],
                "added_by": r["added_by"], "timestamp": r["timestamp"],
                "relevance_score": r["relevance_score"],
                "endorsed_by": json.loads(r["endorsed_by"] or "[]"),
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored[:max_results]]
