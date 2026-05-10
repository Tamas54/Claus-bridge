"""Vertical plugins — domain-specific skill bundles for Bridge sub-agents.

Pilot status (2026-05-10): only `makro/` exists. If A/B test shows lift over
the flat recipe-template approach, expand to {geopolitika, jog, trading}.

Layout:
    vertical_plugins/<vertical>/
        skills/*.md      — domain knowledge fragments (concatenated into prompt)
        commands/*.md    — orchestrator system prompts (single file selected per task)

Public API:
    load_skills(vertical) -> str
    load_command(vertical, command_name) -> str
    list_verticals() -> list[str]
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).parent


def list_verticals() -> list[str]:
    return sorted(p.name for p in _ROOT.iterdir() if p.is_dir() and not p.name.startswith("_") and p.name != "pilot_results")


def load_skills(vertical: str) -> str:
    """Concatenate all skill markdown files for a vertical into one block.

    Returns empty string if vertical has no skills/ dir or it's empty.
    """
    skills_dir = _ROOT / vertical / "skills"
    if not skills_dir.is_dir():
        return ""
    files = sorted(skills_dir.glob("*.md"))
    if not files:
        return ""
    parts = []
    for f in files:
        parts.append(f"<!-- skill: {f.stem} -->\n{f.read_text(encoding='utf-8').strip()}")
    return "\n\n---\n\n".join(parts)


def load_command(vertical: str, command_name: str) -> str:
    """Load a single command markdown (orchestrator system prompt).

    Raises FileNotFoundError if missing — this is a programmer error, not
    a runtime fallback case.
    """
    path = _ROOT / vertical / "commands" / f"{command_name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"vertical_plugins/{vertical}/commands/{command_name}.md")
    return path.read_text(encoding="utf-8").strip()
