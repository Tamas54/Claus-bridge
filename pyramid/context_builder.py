from pyramid.agents import (
    get_kommandant_profile,
    get_bridge_info,
    get_team_info,
    get_agent_personality
)
from pyramid.memory_shared import get_shared_memory_summary
from pyramid.memory_rag import get_agent_rag_summary


def build_agent_context(
    agent_id: str,
    custom_system_prompt: str = None,
    include_shared_memory: bool = True,
    include_rag: bool = True,
    inbox_summary: str = None,
) -> str:
    """
    Összeállítja egy agent TELJES system promptját.

    Rétegek:
    1. Személyiség (ki vagy te)
    2. Ki a Kommandant (a főnököd)
    3. Mi a Bridge + ki kicsoda a csapatban
    4. Shared Memory (közös tudás)
    5. Egyéni RAG (saját korábbi munkák)
    6. Inbox — friss emailek és naptár események (ha van)
    7. Custom system prompt (ha van felülírás)
    8. Viselkedési szabályok
    """

    sections = []

    # 1. Személyiség
    sections.append(f"# IDENTITÁSOD\n{get_agent_personality(agent_id)}")

    # 2. Kommandant
    sections.append(f"# A KOMMANDANT (a főnököd)\n{get_kommandant_profile()}")

    # 3. Bridge és csapat
    sections.append(f"# A CLAUS-BRIDGE RENDSZER\n{get_bridge_info()}")
    sections.append(f"# A CSAPAT\n{get_team_info()}")

    # 4. Shared Memory
    if include_shared_memory:
        shared = get_shared_memory_summary(max_items=15)
        if shared:
            sections.append(shared)

    # 5. Egyéni RAG
    if include_rag:
        rag = get_agent_rag_summary(agent_id, max_items=10)
        if rag:
            sections.append(rag)

    # 6. Inbox (email + calendar)
    if inbox_summary:
        sections.append(f"# KOMMANDANT INBOX (friss emailek és naptár)\n{inbox_summary}")

    # 7. Custom system prompt (felülírás, ha van)
    if custom_system_prompt:
        sections.append(f"# SPECIÁLIS INSTRUKCIÓ\n{custom_system_prompt}")

    # 8. Viselkedési szabályok
    sections.append("""# VISELKEDÉSI SZABÁLYOK
- Magyarul válaszolj, hacsak nem kérnek mást.
- Légy tömör és lényegretörő — a Kommandant utálja a felesleges szöveget.
- Ha nem tudsz valamit, mondd meg — ne hallucináj.
- Az eredményeid automatikusan mentésre kerülnek. Dolgozz minőségben.
- Ha egy feladat túl nagy, mondd meg és javasolj bontást.
- Ismered a Kommandantot, a Bridge-et, és a csapatot. Használd ezt a tudást.""")

    return "\n\n---\n\n".join(sections)
