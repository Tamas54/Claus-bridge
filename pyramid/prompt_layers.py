"""Prompt layer loaders — globál szabályok, lencse-personák, synthesis-szerep, polishing.

A `prompts/` mappa a `pyramid/` modul mellett él (NEM bele) — moduláris,
fájlonként verziószámozható (későbbi lépésekben). A loader-helperek
behelyettesítik a `{{output_language}}` és más placeholdereket runtime-on.

A `bridge_vision_2026-05-10.md` 6-lépéses ütemtervéből:
- Lépés 1 (most): `load_global_rules`
- Lépés 2 (következő): `load_synthesis_role`
- Lépés 4 (későbbi): `load_lens_persona`
- Lépés 3 (polishing): a `vertical_plugins/__init__.py` `load_polishing_prompt`-ja
  marad (átfedés-mentes)
"""
from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_VALID_LANGUAGES = {"hu", "en", "de", "zh"}


def load_global_rules(output_language: str = "hu") -> str:
    """Load `prompts/global_rules.md` with `{{output_language}}` substitution.

    Returns empty string if the file doesn't exist (backwards-compat: a Bridge
    instance without the prompts/ folder runs unaffected).
    """
    if output_language not in _VALID_LANGUAGES:
        # Fail safe — fall back to default rather than raise
        output_language = "hu"
    path = _PROMPTS_DIR / "global_rules.md"
    if not path.is_file():
        return ""
    template = path.read_text(encoding="utf-8")
    return template.replace("{{output_language}}", output_language).strip()


def prepend_global_rules(system_prompt: str, output_language: str = "hu") -> str:
    """Prepend `prompts/global_rules.md` to a system prompt.

    Convenience wrapper — every sub-agent-call site can call this with no
    boilerplate. Returns the original prompt unchanged if the rules file
    is missing (graceful no-op for backwards-compat).
    """
    rules = load_global_rules(output_language)
    if not rules:
        return system_prompt or ""
    return rules + "\n\n" + (system_prompt or "")
