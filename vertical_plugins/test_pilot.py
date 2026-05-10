"""A/B pilot — does the skills+command markdown approach beat the flat recipe template?

Both arms get the IDENTICAL factual data block. Only the prompting differs:
  - Arm A (baseline): the `weekly_macro_report` recipe template as it lives today
    in pyramid_recipes.
  - Arm B (pilot):  vertical_plugins/makro/commands/makro-brief.md as the system
    prompt, with skills/*.md concatenated underneath.

Run:
    SILICONFLOW_API_KEY=sk-... python -m vertical_plugins.test_pilot
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
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from vertical_plugins import load_command, load_skills

SF_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
SF_BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.com/v1")
MODEL_ID = os.environ.get("PILOT_MODEL", "deepseek-ai/DeepSeek-V4-Pro")

ROOT = Path(__file__).parent
RESULTS_DIR = ROOT / "pilot_results"

# ────────────────────────────────────────────────────────────────────────
# REAL DATA BLOCK — pulled live from Makronóm MCP on 2026-05-10. Numbers
# are real, dates are real, URLs are real where exposed by the source.
# Output structured for human readability (the agent should not need to
# decode tool names / table codes).
# ────────────────────────────────────────────────────────────────────────

MAKRO_DATA = {
    "data_pulled_at": "2026-05-10",
    "indicators": [
        {
            "name": "Magyar fogyasztói árindex (CPI)",
            "source": "KSH",
            "table": "A főbb ármutatók havonta (1.2.1.1.)",
            "url": "https://www.ksh.hu/stadat_files/ara/hu/ara0039.csv",
            "unit": "YoY %",
            "data": [
                {"period": "2026. január", "value": 2.1},
                {"period": "2026. február", "value": 1.4},
                {"period": "2026. március", "value": 1.8},
                {"period": "2026. április", "value": 2.1},
            ],
        },
        {
            "name": "Magyar maginfláció (eredeti)",
            "source": "KSH",
            "table": "Az eredeti és szezonálisan kiigazított új típusú maginfláció (1.2.1.9.)",
            "url": "https://www.ksh.hu/stadat_files/ara/hu/ara0045.csv",
            "note": "A KSH bázis-indexet ad — YoY-t a 2026-os és 2025-os azonos havi értékek hányadosaként számoltam.",
            "unit": "YoY % (számított)",
            "data": [
                {"period": "2026. január", "value": 2.7},
                {"period": "2026. február", "value": 2.1},
                {"period": "2026. március", "value": 1.9},
                {"period": "2026. április", "value": 2.2},
            ],
        },
        {
            "name": "Magyar ipari termelői árindex",
            "source": "KSH",
            "table": "A főbb ármutatók havonta (1.2.1.1.)",
            "url": "https://www.ksh.hu/stadat_files/ara/hu/ara0039.csv",
            "unit": "YoY %",
            "data": [
                {"period": "2026. január", "value": -2.9},
                {"period": "2026. február", "value": -3.3},
                {"period": "2026. március", "value": 1.2},
            ],
        },
        {
            "name": "Magyar HICP (EU-harmonizált fogyasztói árindex)",
            "source": "Eurostat",
            "dataset": "prc_hicp_manr (Annual rate of change)",
            "geo": "Magyarország",
            "unit": "YoY %",
            "data": [
                {"period": "2025. szeptember", "value": 4.3},
                {"period": "2025. október", "value": 4.2},
                {"period": "2025. november", "value": 3.7},
                {"period": "2025. december", "value": 3.3},
            ],
            "publishing_lag_note": "Eurostat HU HICP 2025-12-ig publikálva 2026-05-10-én. 2026 januári és későbbi hónapok még nincsenek Eurostat-on.",
        },
        {
            "name": "Eurózóna HICP",
            "source": "Eurostat",
            "dataset": "prc_hicp_manr (Annual rate of change)",
            "geo": "eurózóna",
            "unit": "YoY %",
            "data": [
                {"period": "2025. szeptember", "value": 2.2},
                {"period": "2025. október", "value": 2.1},
                {"period": "2025. november", "value": 2.1},
                {"period": "2025. december", "value": 2.0},
            ],
        },
        {
            "name": "Magyar GDP (chain-linked volume, szezonálisan kiigazított)",
            "source": "Eurostat",
            "dataset": "namq_10_gdp",
            "geo": "Magyarország",
            "unit": "millió euró (2015 = referencia), és YoY % számítva",
            "data": [
                {"period": "2024-Q1", "value_meur": 35693.2},
                {"period": "2024-Q2", "value_meur": 35649.2},
                {"period": "2024-Q3", "value_meur": 35550.9},
                {"period": "2024-Q4", "value_meur": 35652.4},
                {"period": "2025-Q1", "value_meur": 35586.0, "yoy_pct": -0.3},
                {"period": "2025-Q2", "value_meur": 35806.2, "yoy_pct": 0.4},
                {"period": "2025-Q3", "value_meur": 35854.6, "yoy_pct": 0.9},
                {"period": "2025-Q4", "value_meur": 35917.5, "yoy_pct": 0.7},
                {"period": "2026-Q1", "value_meur": 36206.8, "yoy_pct": 1.7},
            ],
        },
        {
            "name": "Eurózóna GDP (chain-linked volume, szezonálisan kiigazított)",
            "source": "Eurostat",
            "dataset": "namq_10_gdp",
            "geo": "eurózóna (EA20)",
            "unit": "millió euró (2015 = referencia), és YoY % számítva",
            "data": [
                {"period": "2024-Q1", "value_meur": 3010872.0},
                {"period": "2024-Q4", "value_meur": 3041918.0},
                {"period": "2025-Q1", "value_meur": 3059963.7, "yoy_pct": 1.6},
                {"period": "2025-Q4", "value_meur": 3079666.2, "yoy_pct": 1.2},
                {"period": "2026-Q1", "value_meur": 3084092.7, "yoy_pct": 0.8},
            ],
        },
        {
            "name": "Magyar munkanélküliségi ráta (ILO definíció, EU-harmonizált)",
            "source": "Eurostat",
            "dataset": "une_rt_m",
            "geo": "Magyarország",
            "unit": "%",
            "data": [
                {"period": "2026. január", "value": 4.6},
                {"period": "2026. február", "value": 4.8},
                {"period": "2026. március", "value": 4.5},
            ],
            "note": "Eurostat csak Hungary-t ad vissza ehhez a lekérdezéshez — eurózóna munkanélküliségi adat nincs ebben az adatblokkban.",
        },
        {
            "name": "MNB hivatalos középárfolyam",
            "source": "MNB",
            "url": "https://www.mnb.hu/arfolyamok",
            "as_of": "2026. május 8.",
            "rates": {"EUR/HUF": 355.14, "USD/HUF": 301.89},
        },
        {
            "name": "EUR/HUF heti záró árfolyam (piaci, intraday-záró)",
            "source": "Yahoo Finance",
            "symbol": "EURHUF=X",
            "interval": "heti",
            "data": [
                {"week_starting": "2026-02-02", "close": 376.72},
                {"week_starting": "2026-02-23", "close": 376.14},
                {"week_starting": "2026-03-02", "close": 399.11, "note": "márciusi csúcs"},
                {"week_starting": "2026-03-30", "close": 382.65},
                {"week_starting": "2026-04-13", "close": 360.69},
                {"week_starting": "2026-04-27", "close": 362.27},
                {"week_starting": "2026-05-04", "close": 353.66},
            ],
        },
        {
            "name": "Magyar jegybanki irányadó kamat (becslés)",
            "source": "Eurostat irt_st_m (Day-to-day money market rate, MNB-policy proxy ~10 bps eltérés)",
            "as_of": "2026. március",
            "value_pct": 6.2,
            "stale_warning": "A BIS WS_CBPOL adatsor 2025. júniusi (~11 hónapja). A pénzpiaci proxy frissebb, de NEM azonos a jegybanki alapkamattal. Pontos érték web-keresésű ellenőrzéssel verifikálható.",
        },
    ],
}


# ────────────────────────────────────────────────────────────────────────
# Arm A — baseline: current weekly_macro_report recipe template.
# Pulled verbatim from plugins/recipes.py:113-117 seed data.
# ────────────────────────────────────────────────────────────────────────

ARM_A_USER_PROMPT_TEMPLATE = (
    "Keszits heti makrogazdasagi osszefoglalot: "
    "1) Magyar es EU GDP, inflacio, munkanelkuliseg legfrissebb adatai "
    "2) Heti fo gazdasagi hirek "
    "3) Szintezis es kitekintes, max 500 szo.\n\n"
    "[Mai datum: 2026-05-10. Az adatoknak FRISSNEK kell lenniuk!]\n\n"
    "=== FACTUAL CONTEXT ===\n"
    "{data_block}\n"
    "=== END FACTUAL CONTEXT ===\n\n"
    "SZIGORU SZABALY: MINDEN szamadatnak a fenti FACTUAL CONTEXT blokkbol "
    "kell szarmaznia. Ha valami nincs ott, ird: 'adat nem elerheto'."
)

ARM_A_SYSTEM_PROMPT = (
    "Te a Claus-Bridge egyik sub-agentje vagy. Magyarul valaszolj, tomoren. "
    "A mai datum 2026-05-10. Ne talalj ki adatot."
)


# ────────────────────────────────────────────────────────────────────────
# Arm B — pilot: vertical_plugins/makro skills + command.
# ────────────────────────────────────────────────────────────────────────

def build_arm_b_prompts(data_block: str) -> tuple[str, str]:
    command = load_command("makro", "makro-brief")
    skills = load_skills("makro")
    system_prompt = (
        f"{command}\n\n"
        "═══ DOMAIN SKILLS (alkalmazd a workflow lépés 2-ben) ═══\n\n"
        f"{skills}\n\n"
        "═══ END DOMAIN SKILLS ═══"
    )
    user_prompt = (
        "Itt a friss makró-adatblokk. Csinálj heti briefet a system promptban "
        "leírt 4-szekciós struktúrában, max 500 szó.\n\n"
        "=== FACTUAL CONTEXT ===\n"
        f"{data_block}\n"
        "=== END FACTUAL CONTEXT ==="
    )
    return system_prompt, user_prompt


# ────────────────────────────────────────────────────────────────────────

async def call_sf(system_prompt: str, user_prompt: str, max_tokens: int = 2500) -> dict:
    payload = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.6,
        "reasoning_effort": "medium",
    }
    async with httpx.AsyncClient(timeout=360) as client:
        resp = await client.post(
            f"{SF_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {SF_API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}", "body": resp.text[:2000]}
    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {"content": content, "usage": usage}
    except (KeyError, IndexError) as e:
        return {"error": f"parse: {e}", "raw": data}


async def main():
    if not SF_API_KEY:
        print("ERROR: SILICONFLOW_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    data_block = json.dumps(MAKRO_DATA, ensure_ascii=False, indent=2)

    arm_a_system = ARM_A_SYSTEM_PROMPT
    arm_a_user = ARM_A_USER_PROMPT_TEMPLATE.format(data_block=data_block)
    arm_b_system, arm_b_user = build_arm_b_prompts(data_block)

    print(f"[{ts}] running pilot — model={MODEL_ID}")
    print(f"  arm A: system={len(arm_a_system)} chars, user={len(arm_a_user)} chars")
    print(f"  arm B: system={len(arm_b_system)} chars, user={len(arm_b_user)} chars")
    print(f"  arm B has +{len(arm_b_system) - len(arm_a_system)} system-prompt chars (skills+command)")

    arm_a_result, arm_b_result = await asyncio.gather(
        call_sf(arm_a_system, arm_a_user),
        call_sf(arm_b_system, arm_b_user),
    )

    def write_arm(label: str, system: str, user: str, result: dict, path: Path):
        with path.open("w", encoding="utf-8") as f:
            f.write(f"# Pilot run {ts} — arm {label}\n\n")
            f.write(f"**Model:** `{MODEL_ID}`\n\n")
            if "error" in result:
                f.write(f"## ERROR\n\n```\n{result.get('error')}\n{result.get('body', '')[:1500]}\n```\n\n")
            else:
                usage = result.get("usage", {})
                f.write(f"**Usage:** prompt={usage.get('prompt_tokens', '?')}, "
                        f"completion={usage.get('completion_tokens', '?')}\n\n")
                f.write("## Output\n\n")
                f.write(result["content"])
                f.write("\n\n---\n\n")
            f.write("## System prompt\n\n```\n")
            f.write(system)
            f.write("\n```\n\n## User prompt\n\n```\n")
            f.write(user)
            f.write("\n```\n")

    arm_a_path = RESULTS_DIR / f"{ts}_arm_A.md"
    arm_b_path = RESULTS_DIR / f"{ts}_arm_B.md"
    write_arm("A (baseline)", arm_a_system, arm_a_user, arm_a_result, arm_a_path)
    write_arm("B (pilot)", arm_b_system, arm_b_user, arm_b_result, arm_b_path)

    diff_path = RESULTS_DIR / f"{ts}_compare.md"
    with diff_path.open("w", encoding="utf-8") as f:
        f.write(f"# Pilot compare {ts} — model `{MODEL_ID}`\n\n")
        f.write("**Adatok**: élő Makronóm MCP lekérdezés 2026-05-10, valós KSH/Eurostat/MNB/Yahoo adatok.\n\n")
        f.write("## Arm A (baseline — current recipe template)\n\n")
        f.write(arm_a_result.get("content", f"ERROR: {arm_a_result.get('error', '?')}"))
        f.write("\n\n---\n\n")
        f.write("## Arm B (pilot — vertical_plugins/makro skills + command)\n\n")
        f.write(arm_b_result.get("content", f"ERROR: {arm_b_result.get('error', '?')}"))
        f.write("\n\n---\n\n")
        f.write("## Verdict checklist (manual review)\n\n")
        f.write("- [ ] Citation **emberi formátum**: `<intézmény>, <tartalmi név>, <dátum>` + URL?\n")
        f.write("- [ ] **NEM** szerepel `get_ksh_stadat`, `_bridge_fetched_at`, `prc_hicp_manr`, `geo=HU` típusú belső tag?\n")
        f.write("- [ ] HICP vs CPI külön kezelése?\n")
        f.write("- [ ] Reál vs nominális GDP megkülönböztetése? Maginfláció vs headline?\n")
        f.write("- [ ] Hiányzó adat → 'nem elérhető' VAGY záró szekcióban flagged?\n")
        f.write("- [ ] Struktúra (4 szekció B-nél)?\n")
        f.write("- [ ] Magyar számformátum (3,4% nem 3.4%)?\n")
        f.write("- [ ] EUR/HUF helyes érték (355,14 vagy 353,66, NEM 388)?\n")
        f.write("- [ ] Hossz ≤500 szó?\n")

    print(f"  → {arm_a_path}")
    print(f"  → {arm_b_path}")
    print(f"  → {diff_path}")


if __name__ == "__main__":
    asyncio.run(main())
