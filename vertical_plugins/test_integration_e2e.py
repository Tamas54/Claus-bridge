"""End-to-end integration test — 3-agent vertikum-route szimuláció lokálisan.

Reprodukálja azt, amit a Bridge runtime-on a `execute_recipe('weekly_macro_report')`
csinálna a vertikum-route-ban: load_command + load_skills + 3 párhuzamos SF call
(Kimi K2.6, DeepSeek V4-Pro, GLM-5.1) ugyanazzal a system prompt-tal és user
prompt-tal, majd Kimi-szintézis a 3 kimenetből.

A 3-agent + adatolt szintézis a Bridge "verhetetlen" módja (lásd
feedback_bridge_3agent_synthesis memo). Az adat a `test_pilot.py:MAKRO_DATA`
valós lekérdezésű adatblokkja (Makronóm MCP, 2026-05-10).

Run:
    SILICONFLOW_API_KEY=sk-... python -m vertical_plugins.test_integration_e2e
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

try:
    from vertical_plugins import load_command, load_skills
    from vertical_plugins.test_pilot import MAKRO_DATA
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from vertical_plugins import load_command, load_skills
    from vertical_plugins.test_pilot import MAKRO_DATA

SF_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
SF_BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.com/v1")

AGENT_MODELS = {
    "kimi": "moonshotai/Kimi-K2.6",
    "deepseek": "deepseek-ai/DeepSeek-V4-Pro",
    "glm5": "zai-org/GLM-5.1",
}

ROOT = Path(__file__).parent
RESULTS_DIR = ROOT / "pilot_results"


def build_vertical_system(vertical: str, command_name: str) -> str:
    cmd_md = load_command(vertical, command_name)
    skills_md = load_skills(vertical)
    return (
        f"{cmd_md}\n\n"
        "═══ DOMAIN SKILLS (alkalmazd a workflow-ban) ═══\n\n"
        f"{skills_md}\n\n"
        "═══ END DOMAIN SKILLS ═══"
    )


def build_user_prompt(data_block: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"[Mai dátum: {today}]\n\n"
        "Itt a friss adatblokk. Csinálj briefet a system promptban leírt struktúrában.\n\n"
        "=== FACTUAL CONTEXT ===\n"
        f"{data_block}\n"
        "=== END FACTUAL CONTEXT ===\n\n"
        "SZIGORÚ SZABÁLY: minden szám/állítás a fenti CONTEXT blokkból. "
        "Ha valami nincs ott, a 'Hiányzó / nem-elérhető források' záró szekcióba flaggeld."
    )


async def call_agent(model_id: str, system_prompt: str, user_prompt: str,
                     extra: dict, max_tokens: int = 4000) -> dict:
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.6,
    }
    payload.update(extra)
    async with httpx.AsyncClient(timeout=420) as client:
        resp = await client.post(
            f"{SF_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {SF_API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}", "body": resp.text[:1500]}
    data = resp.json()
    try:
        return {
            "content": data["choices"][0]["message"]["content"],
            "usage": data.get("usage", {}),
        }
    except (KeyError, IndexError) as e:
        return {"error": f"parse: {e}", "raw_keys": list(data.keys())}


async def synthesize(title: str, agent_results: dict[str, dict]) -> dict:
    """Kimi-szintézis a 3 agent kimenetéből (Bridge `_run_dispatch`-szerű, egyszerűsített)."""
    parts = []
    for aid, res in agent_results.items():
        content = res.get("content") or f"ERROR: {res.get('error', '?')}"
        parts.append(f"=== [{aid}] EREDMÉNY ===\n{content}")
    synthesis_input = "\n\n---\n\n".join(parts)

    system = (
        "Te a koordinátor vagy. Az al-agentek (kimi, deepseek, glm5) AZONOS feladatot kaptak "
        "(vertikum-vezérelt heti brief), de eltérő modell-szemlélettel dolgoztak. "
        "Készíts egységes szintézist:\n"
        "1) Hol van konszenzus a 3 agent között (ezt kombináld egységes szöveggé).\n"
        "2) Hol térnek el — ha az eltérés érdemi, említsd meg melyik agent emelte ki.\n"
        "3) Tartsd meg a vertikum-skill citation-fegyelmét — minden szám forrással.\n"
        "4) A 'Hiányzó / nem-elérhető források' szekciót egyesítsd: minden agent által flaggelt hiány.\n"
        "Magyarul, lényegre törően, max 600 szó. Ne ismételd meg az agentek szövegét hosszan."
    )
    return await call_agent(
        model_id=AGENT_MODELS["kimi"],
        system_prompt=system,
        user_prompt=f"FELADAT CÍME: {title}\n\nAGENT EREDMÉNYEK:\n{synthesis_input}",
        extra={"thinking": {"type": "disabled"}},
        max_tokens=3500,
    )


AGENT_EXTRAS = {
    "kimi": {"thinking": {"type": "disabled"}},
    "deepseek": {"reasoning_effort": "medium"},
    "glm5": {},
}


async def main():
    if not SF_API_KEY:
        print("ERROR: SILICONFLOW_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    title = "weekly_macro_report (vertikum: makro) — e2e teszt"

    system_prompt = build_vertical_system("makro", "makro-brief")
    data_block = json.dumps(MAKRO_DATA, ensure_ascii=False, indent=2)
    user_prompt = build_user_prompt(data_block)

    print(f"[{ts}] e2e test: 3 agents + szintézis")
    print(f"  system_prompt: {len(system_prompt)} chars")
    print(f"  user_prompt:   {len(user_prompt)} chars")

    # 3 párhuzamos agent call
    coros = {
        aid: call_agent(
            model_id=mid,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            extra=AGENT_EXTRAS[aid],
            max_tokens=4000,
        )
        for aid, mid in AGENT_MODELS.items()
    }
    results = {}
    for aid, res in zip(coros.keys(), await asyncio.gather(*coros.values(), return_exceptions=True)):
        if isinstance(res, Exception):
            results[aid] = {"error": f"{type(res).__name__}: {res}"}
        else:
            results[aid] = res
        size = len(results[aid].get("content", "") or "")
        err = results[aid].get("error")
        print(f"  → {aid}: {'ERROR ' + err if err else f'{size} chars'}")

    # Szintézis
    print("  → szintézis fut...")
    synth = await synthesize(title, results)
    if "error" in synth:
        print(f"  → szintézis ERROR: {synth['error']}")
    else:
        print(f"  → szintézis: {len(synth['content'])} chars")

    # Mentés egy összevont fájlba
    out_path = RESULTS_DIR / f"{ts}_e2e_makro.md"
    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"# E2E integration test — {ts}\n\n")
        f.write(f"**Recipe**: `weekly_macro_report` (vertikum: makro)\n")
        f.write(f"**Mode**: 3-agent + szintézis (Bridge runtime-szimuláció)\n\n")
        f.write(f"**system_prompt**: {len(system_prompt)} chars (vertical_plugins/makro/commands+skills)\n")
        f.write(f"**user_prompt**:   {len(user_prompt)} chars (FACTUAL CONTEXT)\n\n")
        f.write("---\n\n")

        for aid in AGENT_MODELS:
            res = results[aid]
            f.write(f"## Agent: `{aid}` ({AGENT_MODELS[aid]})\n\n")
            if "error" in res:
                f.write(f"**ERROR**: {res['error']}\n\n")
            else:
                u = res.get("usage", {})
                f.write(f"**tokens**: prompt={u.get('prompt_tokens', '?')}, completion={u.get('completion_tokens', '?')}\n\n")
                f.write(res["content"])
                f.write("\n\n")
            f.write("---\n\n")

        f.write("## Szintézis (Kimi koordinátor)\n\n")
        if "error" in synth:
            f.write(f"**ERROR**: {synth['error']}\n")
        else:
            u = synth.get("usage", {})
            f.write(f"**tokens**: prompt={u.get('prompt_tokens', '?')}, completion={u.get('completion_tokens', '?')}\n\n")
            f.write(synth["content"])
            f.write("\n")

    print(f"  → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
