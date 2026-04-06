import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

SHARED_MEMORY_PATH = Path(__file__).parent.parent / "data" / "pyramid_shared_memory.json"


def _ensure_path():
    SHARED_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_shared_memory() -> List[dict]:
    _ensure_path()
    if SHARED_MEMORY_PATH.exists():
        try:
            return json.loads(SHARED_MEMORY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return []
    return []


def save_shared_memory(memory: list):
    _ensure_path()
    SHARED_MEMORY_PATH.write_text(
        json.dumps(memory, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def add_to_shared_memory(content: str, category: str, added_by: str) -> dict:
    memory = load_shared_memory()
    entry = {
        "id": f"pk_{len(memory)+1:04d}",
        "category": category,
        "content": content[:2000],
        "added_by": added_by,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "relevance_score": 0.5,
        "endorsed_by": []
    }
    memory.append(entry)
    save_shared_memory(memory)
    return entry


def endorse_entry(entry_id: str, endorser: str) -> Optional[dict]:
    memory = load_shared_memory()
    for entry in memory:
        if entry["id"] == entry_id:
            if endorser not in entry["endorsed_by"]:
                entry["endorsed_by"].append(endorser)
                entry["relevance_score"] = min(1.0, 0.5 + len(entry["endorsed_by"]) * 0.15)
            save_shared_memory(memory)
            return entry
    return None


def get_shared_memory_summary(max_items: int = 20) -> str:
    memory = load_shared_memory()
    if not memory:
        return ""

    sorted_mem = sorted(
        memory,
        key=lambda x: (x.get("relevance_score", 0), x.get("timestamp", "")),
        reverse=True
    )
    top = sorted_mem[:max_items]

    lines = ["## Közös tudásbázis (Pyramid Shared Memory)"]
    for item in top:
        endorsers = ", ".join(item.get("endorsed_by", []))
        endorser_info = f" [jóváhagyta: {endorsers}]" if endorsers else ""
        lines.append(
            f"- [{item['category']}] {item['content'][:300]} "
            f"(forrás: {item['added_by']}{endorser_info})"
        )

    return "\n".join(lines)


def search_shared_memory(query: str, max_results: int = 10) -> List[dict]:
    memory = load_shared_memory()
    query_lower = query.lower()
    keywords = query_lower.split()

    scored = []
    for entry in memory:
        text = (entry["content"] + " " + entry["category"]).lower()
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored[:max_results]]
