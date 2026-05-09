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
    """Strong temporal-grounding + anti-hallucination block for SF agent system prompts.

    Without this block Kimi K2.6 (and to a lesser extent V4-Pro) silently
    "corrects" any post-cutoff date in the prompt back to what its training
    remembers as 'now', then writes the entire response in that wrong frame.
    The Anthropic Opus 4.7 system prompt and Moonshot's own K2 Thinking
    prompt both solve this with: (1) explicit cutoff, (2) explicit current
    date, (3) hard rule that present-day facts MUST come from web_search.

    Also bundles ANTI-HALLUCINATION rules (added 2026-04-28 after task #127
    where GLM5 fabricated `mnbkozeparfolyam.hu` URL + 311.84 HUF rate when
    DDG returned 202/anomaly). When search fails, agents MUST refuse rather
    than confabulate sources.
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
        f"- Ha a feladatban {now.year}-os vagy későbbi dátum szerepel, az NEM 'jövőbeli'. "
        f"A mai nap {today}. NE írd hogy 'a jelenlegi időponthoz képest a jövőben van'.\n"
        f"- NE írd hogy 'a mai dátum 2025' vagy bármi 2025-ös. {today} van. Pont.\n"
        "- Bármilyen mai vagy a tréning-cutoffod utáni eseményről / hírről kérdés esetén "
        "a web_search tool használata KÖTELEZŐ ELŐSZÖR. A te magabiztosságod egy témában "
        "NEM kifogás a keresés átugrására.\n"
        "- A web_search tool valós, élő, friss eredményt ad. NE feltételezd hogy 'üres' "
        "vagy 'jövőbeli' — várd meg és olvasd el a TÉNYLEGES válaszát.\n"
        "- NE említsd a felhasználónak a knowledge cutoffodat, NE jegyezd meg hogy "
        "'nincs real-time adatod'.\n"
        "- Más agent által hozott friss adatot NE minősítsd 'fiktívnek' vagy 'kitaláltnak' "
        "csak azért mert a saját tréningedben nem szerepel — a többi agent web-grounded.\n\n"
        "=== ANTI-HALLUCINATION (KEMÉNY SZABÁLY) ===\n"
        "- Ha a web_search 'No results found.' VAGY 'DDG status=202, anomaly=True' "
        "VAGY üres listát ad vissza: TILOS forrást, URL-t, domaint vagy konkrét számot "
        "kitalálnod. TILOS hivatkozni olyan oldalra ami nem szerepel a search-output-ban.\n"
        "- TILOS olyan domain-t fabrikálnod amit a search nem hozott vissza "
        "(pl. NEM létező 'mnbkozeparfolyam.hu' típusú gyártott URL-eket).\n"
        "- TILOS 'modellből származtatott', 'kamatparitás-modell alapján', 'piaci konszenzus' "
        "alapú konkrét számot (árfolyam, infláció, GDP) ÉLŐ adatként prezentálnod, "
        "ha nincs hozzá search-forrás. Ha modellt futtatsz, EGYÉRTELMŰEN jelöld: "
        "'Ez NEM piaci adat, hanem modell-becslés.'\n"
        "- Ha a feladat konkrét tényt kér és nincs hozzá web-forrás, a HELYES VÁLASZ: "
        "'A keresés nem hozott eredményt — nincs megbízható forrás erre.' Pont. "
        "Inkább vallj be hiányosságot, mint találj ki hamis forrást.\n\n"
        "=== TOOL-EREDMÉNY CITATION SZABÁLY (KEMÉNY) ===\n"
        "- A `[tool, dátum]` citation-ben szereplő dátum KIZÁRÓLAG lehet: "
        "(a) a tool-eredmény végén szereplő `[_bridge_fetched_at: YYYY-MM-DD HH:MM UTC]` "
        "bélyegző dátuma (Bridge által csatolt objektív lekérési időpont), VAGY "
        "(b) a tool-eredmény tartalmában explicit szereplő adat-dátum "
        "(pl. 'date: 2026-05-08' egy MNB-rátán).\n"
        "- TILOS más dátumot kitalálni — sem 2024-2025-ös 'forrás-dátumokat', "
        "sem becsült/közelített 'körülbelül' időpontokat. Ha az adatnak nincs "
        "explicit dátuma a tool-eredményben, írd hogy 'dátum: a Bridge-bélyegző "
        "szerint X' és idézd a `_bridge_fetched_at` értéket.\n"
        "- Ha a tool-eredmény üres, hibás VAGY az `_bridge_fetched_at` bélyegzőt "
        "nem találod (mert nem hívtad meg a tool-t!), TILOS dátumozott citation-t adni. "
        "Helyette: 'NEM hívtam meg a [tool] tool-t — nincs adat.'\n"
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
