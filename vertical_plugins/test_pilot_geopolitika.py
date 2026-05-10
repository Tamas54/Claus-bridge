"""A/B pilot — geopolitika vertical (mirrors test_pilot.py for the makro arm).

Both arms get the IDENTICAL Echolot-style sphere-grouped news block plus a
narrative_divergence block. Only the prompting differs:

  - Arm A (baseline): a hypothetical `weekly_geopolitics_brief` recipe template
    in the same style as the existing `weekly_macro_report` seed (flat one-line
    workflow, generic system prompt).
  - Arm B (pilot):  vertical_plugins/geopolitika/commands/heti-jelentes.md as
    the system prompt, with skills/*.md (sphere-divergencia, narrative-tracking,
    source-citation) concatenated underneath.

Run:
    SILICONFLOW_API_KEY=sk-... python -m vertical_plugins.test_pilot_geopolitika
"""
from __future__ import annotations

import asyncio
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
# FIXED NEWS BLOCK — Echolot-style sphere-grouped output, ~13 articles
# across 6 spheres. Topics chosen to expose sphere-divergence opportunities:
# (1) Ukraine front, (2) EU enlargement Western Balkans, (3) Iran nuclear.
# Cikk-címek és lead-ek plauzibilisek, NEM tényleges 2026-05-10 cikkekből.
# A pilot a struktúra-használatot méri, nem a tényhelyességet.
# ────────────────────────────────────────────────────────────────────────

NEWS_BLOCK = """--- FRISS HÍRKONTEXTUS (geopolitics, 3 nap, 13 cikk) ---

### sphere: global_anchor (4 cikk)
- [2026-05-09 06:30] **Reuters**: EU summit ends without Western Balkans accession breakthrough — Council unable to bridge member-state objections; next checkpoint December [https://reuters.com/world/europe/2026-eu-summit-balkans]
- [2026-05-09 11:15] **AP**: Russia launches missile barrage on Kharkiv energy grid; three substations hit, civilian casualties reported [https://apnews.com/article/2026-05-09-kharkiv-missile]
- [2026-05-08 17:40] **BBC News**: IAEA submits Iran enrichment report to Board — uranium stockpile at 60% remains above JCPOA limits [https://bbc.com/news/world-2026-iaea-iran]
- [2026-05-08 09:20] **AFP**: Hungary's Orbán signals continued opposition to Ukraine EU accession negotiations chapter on rule-of-law grounds [https://afp.com/2026-05-08-orban-ukraine-veto]

### sphere: global_analysis (2 cikk)
- [2026-05-09 14:00] **Financial Times**: Why the Western Balkans enlargement push is faltering — analysis points to enlargement fatigue plus Bulgarian-North Macedonian dispute, not Hungary [https://ft.com/content/2026-balkans-enlargement]
- [2026-05-08 19:30] **Economist**: The Iran nuclear stockpile is now a fait accompli — IAEA findings and the diplomatic gap [https://economist.com/2026/iran-nuclear-fait-accompli]

### sphere: regional_russian (2 cikk)
- [2026-05-09 12:00] **TASS**: NATO escalation on Ukraine front continues; Russian forces respond to Kyiv's provocations on energy infrastructure [no URL in dataset]
- [2026-05-09 13:30] **RIA Novosti**: EU's Western Balkans deadlock confirms internal disunity; Bulgaria identified as primary blocker on North Macedonia chapter [https://ria.ru/2026/05/09/balkans]

### sphere: ua_front_osint (2 cikk)
- [2026-05-09 15:45] **DeepState**: Three substations confirmed hit in Kharkiv oblast; OSINT geo-coordinates verified; civilian district adjacent [https://deepstatemap.live/2026-05-09]
- [2026-05-09 08:00] **ASTRA**: Russian air-launched cruise missile salvo from Tu-95MS bombers, ~30 missiles, 70% intercept rate per UA AF [no URL in dataset]

### sphere: iran_regime (1 cikk)
- [2026-05-08 22:10] **PressTV**: Iran's nuclear program remains peaceful; IAEA report politicized at Western behest, says Foreign Ministry spokesman [https://presstv.ir/2026/05/08/iaea-statement]

### sphere: hu_press (2 cikk)
- [2026-05-09 07:00] **Index**: Orbán: nem támogatjuk az ukrán uniós csatlakozási tárgyalások jogállamiság fejezetét [https://index.hu/2026-05-09-orban-ukrajna]
- [2026-05-08 18:15] **Telex**: Brüsszeli csúcs: a magyar veto hangsúlyos, de a nyugat-balkáni blokk nem rajtunk múlik [https://telex.hu/2026-05-08-eu-csucs]

--- NARRATIVE-DIVERGENCE BLOCK (query="ukraine russia kharkiv attack", 3 sphere) ---

### sphere: global_anchor — frame: "missile barrage on civilian infrastructure"
- AP (2026-05-09): Russia launches missile barrage on Kharkiv energy grid; three substations hit, civilian casualties reported
- Reuters (2026-05-09): Kharkiv strikes mark intensification of Russian winter-grid campaign extending into spring

### sphere: regional_russian — frame: "NATO-driven escalation, Russian response"
- TASS (2026-05-09): NATO escalation on Ukraine front continues; Russian forces respond to Kyiv's provocations on energy infrastructure
- RIA (2026-05-09): Western support for Ukrainian provocations forces calibrated response

### sphere: ua_front_osint — frame: "verified OSINT, intercept stats, harm to civilian district"
- DeepState (2026-05-09): Three substations confirmed hit, civilian district adjacent
- ASTRA (2026-05-09): 30-missile cruise salvo from Tu-95MS, 70% intercept rate

(NOTE: regional_us, regional_chinese, israel_press_center, global_conflict — nincs cikk a fenti topic-okra ebben a 3-napos ablakban.)
"""

# ────────────────────────────────────────────────────────────────────────
# Arm A — baseline. Not a real seed recipe today; constructed to match the
# style of `weekly_macro_report` (recipes.py:113) so the comparison is fair.
# ────────────────────────────────────────────────────────────────────────

ARM_A_USER_PROMPT_TEMPLATE = (
    "Keszits heti geopolitikai osszefoglalot: "
    "1) Fo nemzetkozi fejlemenyek "
    "2) Sphere-szerinti elteresek "
    "3) Magyar vonatkozas, max 600 szo.\n\n"
    "[Mai datum: 2026-05-10. Az adatoknak FRISSNEK kell lenniuk!]\n\n"
    "{news_block}\n"
    "SZIGORU SZABALY: MINDEN allitasnak a fenti hirblokkbol kell szarmaznia. "
    "Ha valami nincs ott, ird: 'adat nem elerheto'."
)

ARM_A_SYSTEM_PROMPT = (
    "Te a Claus-Bridge egyik sub-agentje vagy. Magyarul valaszolj, tomoren. "
    "A mai datum 2026-05-10. Ne talalj ki adatot."
)

# ────────────────────────────────────────────────────────────────────────
# Arm B — pilot
# ────────────────────────────────────────────────────────────────────────

def build_arm_b_prompts(news_block: str) -> tuple[str, str]:
    command = load_command("geopolitika", "heti-jelentes")
    skills = load_skills("geopolitika")
    system_prompt = (
        f"{command}\n\n"
        "═══ DOMAIN SKILLS (alkalmazd a workflow lépés 2-ben) ═══\n\n"
        f"{skills}\n\n"
        "═══ END DOMAIN SKILLS ═══"
    )
    user_prompt = (
        "Itt a friss Echolot hírblokk + narrative-divergence kivonat. "
        "Csinálj heti geopolitikai briefet a system promptban leírt 4-szekciós "
        "struktúrában (+ kötelező 'Hiányzó források' záró rész), max 600 szó.\n\n"
        f"{news_block}"
    )
    return system_prompt, user_prompt


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
        return {"content": data["choices"][0]["message"]["content"], "usage": data.get("usage", {})}
    except (KeyError, IndexError) as e:
        return {"error": f"parse: {e}", "raw": data}


async def main():
    if not SF_API_KEY:
        print("ERROR: SILICONFLOW_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    arm_a_system = ARM_A_SYSTEM_PROMPT
    arm_a_user = ARM_A_USER_PROMPT_TEMPLATE.format(news_block=NEWS_BLOCK)
    arm_b_system, arm_b_user = build_arm_b_prompts(NEWS_BLOCK)

    print(f"[{ts}] geopolitika pilot — model={MODEL_ID}")
    print(f"  arm A: system={len(arm_a_system)} chars, user={len(arm_a_user)} chars")
    print(f"  arm B: system={len(arm_b_system)} chars, user={len(arm_b_user)} chars")
    print(f"  arm B has +{len(arm_b_system) - len(arm_a_system)} system-prompt chars")

    arm_a_result, arm_b_result = await asyncio.gather(
        call_sf(arm_a_system, arm_a_user),
        call_sf(arm_b_system, arm_b_user),
    )

    def write_arm(label: str, system: str, user: str, result: dict, path: Path):
        with path.open("w", encoding="utf-8") as f:
            f.write(f"# Pilot run {ts} — geopolitika arm {label}\n\n")
            f.write(f"**Model:** `{MODEL_ID}`\n\n")
            if "error" in result:
                f.write(f"## ERROR\n\n```\n{result.get('error')}\n{result.get('body', '')[:1500]}\n```\n\n")
            else:
                u = result.get("usage", {})
                f.write(f"**Usage:** prompt={u.get('prompt_tokens', '?')}, "
                        f"completion={u.get('completion_tokens', '?')}\n\n")
                f.write("## Output\n\n")
                f.write(result["content"])
                f.write("\n\n---\n\n")
            f.write("## System prompt\n\n```\n")
            f.write(system)
            f.write("\n```\n\n## User prompt\n\n```\n")
            f.write(user)
            f.write("\n```\n")

    arm_a_path = RESULTS_DIR / f"{ts}_geopolitika_arm_A.md"
    arm_b_path = RESULTS_DIR / f"{ts}_geopolitika_arm_B.md"
    write_arm("A (baseline)", arm_a_system, arm_a_user, arm_a_result, arm_a_path)
    write_arm("B (pilot)", arm_b_system, arm_b_user, arm_b_result, arm_b_path)

    diff_path = RESULTS_DIR / f"{ts}_geopolitika_compare.md"
    with diff_path.open("w", encoding="utf-8") as f:
        f.write(f"# Geopolitika pilot compare {ts} — model `{MODEL_ID}`\n\n")
        f.write("## Arm A (baseline — recipe-template style)\n\n")
        f.write(arm_a_result.get("content", f"ERROR: {arm_a_result.get('error', '?')}"))
        f.write("\n\n---\n\n")
        f.write("## Arm B (pilot — vertical_plugins/geopolitika)\n\n")
        f.write(arm_b_result.get("content", f"ERROR: {arm_b_result.get('error', '?')}"))
        f.write("\n\n---\n\n")
        f.write("## Verdict checklist (manual review)\n\n")
        f.write("- [ ] Citation discipline: minden állítás után `[Echolot, sphere=..., source=..., published=...]`?\n")
        f.write("- [ ] Sphere-szerű olvasás (nem csak forrás-felsorolás)?\n")
        f.write("- [ ] Narrative-divergence pár-szembeállítás (frame X / frame Y)?\n")
        f.write("- [ ] Magyar visszhang külön szekció?\n")
        f.write("- [ ] Hiányzó források záró szakasz (KÖTELEZŐ a B-nél)?\n")
        f.write("- [ ] URL-fabrikálás? (pl. olyan URL ami nincs az adatblokkban)?\n")
        f.write("- [ ] Forrás-fabrikálás? (Reuters/AP/FT idézés ha nincs az adatblokkban)?\n")
        f.write("- [ ] Tréning-memóriából húz tényt? (pl. 'általában az X sphere ...')?\n")
        f.write("- [ ] Hossz ≤600 szó?\n")

    print(f"  → {arm_a_path}")
    print(f"  → {arm_b_path}")
    print(f"  → {diff_path}")


if __name__ == "__main__":
    asyncio.run(main())
