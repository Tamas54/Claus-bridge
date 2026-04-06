import json
from datetime import datetime
from pathlib import Path
from typing import List

RAG_BASE_PATH = Path(__file__).parent.parent / "data" / "pyramid_rag"


def _agent_rag_path(agent_id: str) -> Path:
    RAG_BASE_PATH.mkdir(parents=True, exist_ok=True)
    return RAG_BASE_PATH / f"{agent_id}_rag.json"


def load_agent_rag(agent_id: str) -> List[dict]:
    path = _agent_rag_path(agent_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return []
    return []


def save_agent_rag(agent_id: str, rag: list):
    path = _agent_rag_path(agent_id)
    path.write_text(
        json.dumps(rag, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def add_to_agent_rag(agent_id: str, content: str, task_title: str, category: str = "result") -> dict:
    rag = load_agent_rag(agent_id)
    entry = {
        "id": f"rag_{len(rag)+1:04d}",
        "task_title": task_title,
        "category": category,
        "content": content[:5000],
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    rag.append(entry)
    save_agent_rag(agent_id, rag)
    return entry


def search_agent_rag(agent_id: str, query: str, max_results: int = 5) -> List[dict]:
    rag = load_agent_rag(agent_id)
    query_lower = query.lower()
    keywords = query_lower.split()

    scored = []
    for entry in rag:
        text = (entry["content"] + " " + entry["task_title"]).lower()
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored[:max_results]]


def get_agent_rag_summary(agent_id: str, max_items: int = 10) -> str:
    rag = load_agent_rag(agent_id)
    if not rag:
        return ""

    recent = sorted(rag, key=lambda x: x["timestamp"], reverse=True)[:max_items]

    lines = [f"## Saját korábbi munkáid ({agent_id} RAG)"]
    for item in recent:
        lines.append(f"- [{item['task_title']}] {item['content'][:200]}...")

    return "\n".join(lines)


def cleanup_agent_rag(agent_id: str, max_entries: int = 200):
    rag = load_agent_rag(agent_id)
    if len(rag) > max_entries:
        rag = sorted(rag, key=lambda x: x["timestamp"], reverse=True)[:max_entries]
        save_agent_rag(agent_id, rag)
