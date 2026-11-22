"""A modellek tudás-határa (knowledge cutoff) MÉRVE: a backteszt tisztaság-kapuja.

A Kommandant (2026-11-22): „az ötlet akkor működik, ha további backtestek is mennek olyan modellel, ahol a knowledge
cutoff az esemény előtt volt.” Egy előrejelzés-próba csak akkor ér valamit, ha a futás MINDEN LLM-lépése olyan
modellen megy, amely a megjósolt kimenetelt nem ismerhette.

Minden érték cutoff-szondából jön (`orakel_backteszt/cutoff_szonda_*`: előre commitolt, havi esemény-bank, lágy
elicitálás), NEM a gyártó állításából. Új modell csak szondával kerülhet ide.
"""
from __future__ import annotations

#: model_id → {"cutoff": "ÉÉÉÉ-HH"   az utolsó hónap, ahol a szonda még ≥60%-ban talál,
#:             "tiszta_tol": "ÉÉÉÉ-HH" ettől a hónaptól minden szonda-esemény ismeretlen (CLEAN),
#:             "forras": a szonda-jegyzőkönyv útja és dátuma}
#: A kettő közti sáv GRAY (cutoff-farok, tudás-szigetek: a Hy3 pl. ismeri a 2025. júniusi Izrael–Irán háborút).
CUTOFF: dict[str, dict] = {
    "tencent/Hy3": {"cutoff": "2025-04", "tiszta_tol": "2025-08",
                    "forras": "orakel_backteszt/cutoff_szonda_20260720/cutoff_jegyzokonyv.md (2026-07-20)"},
    "claude-opus-5-5": {"cutoff": "2026-01", "tiszta_tol": "2026-03",
                       "forras": "orakel_backteszt/cutoff_szonda_20261122/CUTOFF_JEGYZOKONYV.md (2026-11-22); sziget: 2026-02 (Anthropic–Pentagon-vita)"},
    "claude-sonnet-5-5": {"cutoff": "2025-08", "tiszta_tol": "2026-02",
                         "forras": "orakel_backteszt/cutoff_szonda_20261122/CUTOFF_JEGYZOKONYV.md (2026-11-22); foltos 40–60% 2025-09…2026-01"},
    "claude-haiku-5-5": {"cutoff": "2025-12", "tiszta_tol": "2026-03",
                        "forras": "orakel_backteszt/cutoff_szonda_20261122/CUTOFF_JEGYZOKONYV.md (2026-11-22); sziget: 2026-02 (Anthropic–Pentagon-vita)"},
    "deepseek-ai/DeepSeek-V4.1-Flash": {"cutoff": "2025-03", "tiszta_tol": "2026-01",
                                       "forras": "orakel_backteszt/cutoff_szonda_20261122/CUTOFF_JEGYZOKONYV.md (2026-11-22); farok 2025-07-ig, sziget: 2025-12"},
    "Qwen/Qwen3-30B-A3B-Instruct-2507": {"cutoff": "2024-07", "tiszta_tol": "2025-02",
                                        "forras": "orakel_backteszt/cutoff_szonda_20261122/CUTOFF_JEGYZOKONYV.md (2026-11-22); alacsony felidézés"},
    "Qwen/Qwen3-14B": {"cutoff": "2023-09", "tiszta_tol": "2024-10",
                      "forras": "orakel_backteszt/cutoff_szonda_20261122/CUTOFF_JEGYZOKONYV.md (2026-11-22); alacsony felidézés"},
    "Qwen/Qwen3.5-9B": {"cutoff": "2023-09", "tiszta_tol": "2024-10",
                       "forras": "orakel_backteszt/cutoff_szonda_20261122/CUTOFF_JEGYZOKONYV.md (2026-11-22); alacsony felidézés"},
    "Qwen/Qwen3-8B": {"cutoff": "2023-03", "tiszta_tol": "2023-12",
                     "forras": "orakel_backteszt/cutoff_szonda_20261122/CUTOFF_JEGYZOKONYV.md (2026-11-22); nagyon alacsony felidézés (3/88)"},
    "openai/gpt-oss-120b": {"cutoff": "2024-05", "tiszta_tol": "2024-06",
                           "forras": "orakel_backteszt/cutoff_szonda_20261122/CUTOFF_JEGYZOKONYV.md (2026-11-22)"},
    "openai/gpt-oss-20b": {"cutoff": "2024-04", "tiszta_tol": "2024-06",
                          "forras": "orakel_backteszt/cutoff_szonda_20261122/CUTOFF_JEGYZOKONYV.md (2026-11-22)"},
}


def tisztasag(model_id: str, esemeny_honap: str) -> str:
    """CLEAN / GRAY / CONTAMINATED / ISMERETLEN a megjósolt kimenetel hónapja ("ÉÉÉÉ-HH") szerint."""
    c = CUTOFF.get(model_id)
    if not c:
        return "ISMERETLEN"
    h = (esemeny_honap or "")[:7]
    if h <= c["cutoff"]:
        return "CONTAMINATED"
    if h < c["tiszta_tol"]:
        return "GRAY"
    return "CLEAN"


def futas_tisztasaga(modellek: list[str], esemeny_honap: str) -> str:
    """Egy futás annyira tiszta, mint a legkevésbé tiszta LLM-lépése (minden használt modellt fel kell sorolni)."""
    rend = ["CLEAN", "GRAY", "ISMERETLEN", "CONTAMINATED"]
    return max((tisztasag(m, esemeny_honap) for m in modellek), key=rend.index, default="ISMERETLEN")
