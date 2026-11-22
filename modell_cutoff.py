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
