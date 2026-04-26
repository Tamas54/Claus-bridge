import json
from pathlib import Path
from typing import Dict, Any

PROFILES_DIR = Path(__file__).parent / "profiles"

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
