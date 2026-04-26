from pyramid.memory_shared import add_to_shared_memory
from pyramid.memory_rag import add_to_agent_rag

AUTO_SHARED_KEYWORDS = [
    "döntés", "decision", "projekt", "project",
    "deadline", "határidő", "stratégia", "strategy",
    "eredmény", "result", "következtetés", "conclusion",
    "GDP", "vámtarifa", "tariff",
    "Makronóm", "Polymarket", "TrendMaster",
    "Bridge", "Railway", "Pyramid",
    "HírMagnet", "VideoForge", "WikiCorrelate"
]


def classify_result(content: str, agent_id: str, task_title: str) -> str:
    """
    Eldönti, hogy egy agent eredménye hová kerüljön:
    - "both" → shared memory + rag (ha fontos kulcsszót tartalmaz)
    - "rag" → csak az agent saját RAG-jába
    """
    combined = (content + " " + task_title).lower()
    shared_score = sum(1 for kw in AUTO_SHARED_KEYWORDS if kw.lower() in combined)

    if shared_score >= 1:
        return "both"
    return "rag"


def store_result(content: str, agent_id: str, task_title: str, category: str = "result", force_shared: bool = False) -> str:
    """
    Az eredményt a governance alapján eltárolja.
    Visszaadja a klasszifikációt ("rag" vagy "both").

    force_shared: ha True, a content keyword-illesztés nélkül is shared memory-ba
    kerül. Ezt a Bridge ai_task / ai_query path-jai használják, hogy a 3 rizsrakéta
    eredménye együttesen elérhető legyen mindenki számára (cross-agent context).
    """
    classification = classify_result(content, agent_id, task_title)
    is_shared = (classification == "both") or force_shared

    # Mindig megy a saját RAG-ba
    add_to_agent_rag(agent_id, content, task_title, category)

    if is_shared:
        # Title elé szúrva, hogy keresésnél azonnal beazonosítható legyen.
        add_to_shared_memory(
            content=f"[{task_title}] {content[:1900]}",
            category=category,
            added_by=agent_id
        )

    return "both" if is_shared else classification
