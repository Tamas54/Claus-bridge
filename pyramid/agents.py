import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

PROFILES_DIR = Path(__file__).parent / "profiles"

# Per-agent training cutoffs. Public values where known, conservative estimates
# otherwise. Pattern follows Anthropic Opus 4.7 / Moonshot K2 Thinking system
# prompts: state the cutoff explicitly so the model doesn't silently override
# the runtime date with stale training memory.
TRAINING_CUTOFFS = {
    "kimi":     "2024 vége / 2025 eleje (Kimi K2.6, Moonshot)",
    "deepseek": "2025 közepe (DeepSeek V4-Pro)",
    "glm5":     "2026 első negyedéve (GLM-5.1, ~4 hetes modell)",
}

WEEKDAYS_HU = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]


def temporal_directive(agent_name: str = "") -> str:
    """Strong temporal-grounding block for SF agent system prompts.

    Without this block Kimi K2.6 (and to a lesser extent V4-Pro) silently
    "corrects" any post-cutoff date in the prompt back to what its training
    remembers as 'now', then writes the entire response in that wrong frame.
    The Anthropic Opus 4.7 system prompt and Moonshot's own K2 Thinking
    prompt both solve this with: (1) explicit cutoff, (2) explicit current
    date, (3) hard rule that present-day facts MUST come from web_search.
    """
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    weekday = WEEKDAYS_HU[now.weekday()]
    cutoff = TRAINING_CUTOFFS.get(agent_name, "néhány hónappal a jelenlegi dátum előtt")
    return (
        "\n=== TEMPORAL CONTEXT (TÉNY, NEM KORRIGÁLHATÓ) ===\n"
        f"- A te training cutoff dátumod: kb. {cutoff}.\n"
        f"- A jelenlegi aktuális dátum: {today} ({weekday}).\n"
        "- Ez a runtime tényleges dátuma — NEM elírás, NEM hipotézis, NEM 'jövőbeli'.\n\n"
        "TEMPORAL SZABÁLYOK:\n"
        "- Bármilyen mai vagy a tréning-cutoffod utáni eseményről / hírről kérdés esetén "
        "a web_search tool használata KÖTELEZŐ ELŐSZÖR. A te magabiztosságod egy témában "
        "NEM kifogás a keresés átugrására.\n"
        "- A web_search tool valós, élő, friss eredményt ad. NE feltételezd hogy 'üres' "
        "vagy 'jövőbeli' — várd meg és olvasd el a TÉNYLEGES válaszát.\n"
        "- NE javítsd át a megadott dátumot múltbeli (pl. 2025-ös) értékre. "
        "Ha a rendszer 2026-ot mond, akkor 2026 van.\n"
        "- NE említsd a felhasználónak a knowledge cutoffodat, NE jegyezd meg hogy "
        "'nincs real-time adatod'.\n"
        "- Más agent által hozott friss adatot NE minősítsd 'fiktívnek' vagy 'kitaláltnak' "
        "csak azért mert a saját tréningedben nem szerepel — a többi agent web-grounded.\n"
    )

AGENT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "kimi": {
        "model_id": "moonshotai/Kimi-K2.6",
        "provider": "siliconflow",
        "default_temperature": 0.6,
        "default_max_tokens": 8000,
    },
    "deepseek": {
        "model_id": "deepseek-ai/DeepSeek-V4-Pro",
        "provider": "siliconflow",
        "default_temperature": 0.6,
        "default_max_tokens": 8000,
    },
    "glm5": {
        "model_id": "zai-org/GLM-5.1",
        "provider": "siliconflow",
        "default_temperature": 0.7,
        "default_max_tokens": 16000,
    },
    "qwen3_coder": {
        "model_id": "Qwen/Qwen3-Coder-480B-A35B-Instruct",
        "provider": "siliconflow",
        "default_temperature": 0.5,
        "default_max_tokens": 4000,
    },
}


def load_profile(name: str) -> dict:
    path = PROFILES_DIR / f"{name}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def get_kommandant_profile() -> str:
    profile = load_profile("kommandant")
    if not profile:
        return "Kommandant: Dr. Csizmadia Tamás, senior kutató, Makronóm Intézet."
    return json.dumps(profile, ensure_ascii=False, indent=2)


def get_bridge_info() -> str:
    info = load_profile("bridge_info")
    if not info:
        return "Claus-Bridge: MCP orchestrációs platform, Railway-on fut."
    return json.dumps(info, ensure_ascii=False, indent=2)


def get_team_info() -> str:
    team = load_profile("team")
    if not team:
        return "Csapat: Web-Claus, CLI-Claus, Kimi, DeepSeek, GLM-5.1."
    return json.dumps(team, ensure_ascii=False, indent=2)


def get_agent_personality(agent_id: str) -> str:
    team = load_profile("team")
    agent = team.get(agent_id, {})
    if not agent:
        return f"Te a '{agent_id}' agent vagy a Claus-Bridge rendszerben."

    role = agent.get("role", "általános feladatok")
    strengths = agent.get("strengths", "sokoldalú")
    persona = agent.get("persona", "")

    if isinstance(strengths, list):
        strengths = ", ".join(strengths)

    parts = [f"Te a '{agent_id}' agent vagy a Pyramid rendszerben."]
    if persona:
        parts.append(f"Személyiséged: {persona}")
    parts.append(f"Szereped: {role}")
    parts.append(f"Erősségeid: {strengths}")

    return " ".join(parts)
