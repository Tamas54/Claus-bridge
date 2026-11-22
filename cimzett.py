"""CÍMZETT ÉS FEJLÉC (2026-11-22) — ki kinek beszél a Telegramon.

A Kommandant: „A telegrammomon keveredik melyik ügynök mit küld” és „szabad szövegesen mondhassam meg, hogy melyik
ügynöknek megy az üzi, ha véletlen keveredés lenne”.

* `cimzett(szoveg)` — az üzenet ELEJÉN álló megszólítás („Kimi, …”, „DeepSeeknek: …”, „Kérdezd meg a GLM-et: …”,
  „Feldwebel, …”, „Faber, …”, „cli-Claus, …”) → az ügynök kulcsa. Csak az elején: „mit mondott tegnap a Kimi?” nem megszólítás.
* `fejlec(kulcs, modell)` — minden válasz egységes feje: ikon + NÉV + a TÉNYLEGESEN válaszoló modell
  („🔭 <b>KIMI</b> · Kimi K2.7”). A webes felület (`ui_kabin.tg_szoveg`) ugyanebből olvassa, ki beszél.
"""
from __future__ import annotations

import re

UGYNOKOK = {
    "feldwebel": {"nev": "FELDWEBEL", "ikon": "🪖"},
    "kimi": {"nev": "KIMI", "ikon": "🔭"},
    "deepseek": {"nev": "DEEPSEEK", "ikon": "⚡"},
    "glm5": {"nev": "GLM", "ikon": "🧩"},
    "faber": {"nev": "FABER", "ikon": "🔧"},
    "claus": {"nev": "CLAUS · a gépen", "ikon": "🖥"},
}
#: A Bridge ügynökei, akik név szerint szólíthatók (a SiliconFlow-n, a saját modelljükön válaszolnak).
LLM_UGYNOKOK = ("kimi", "deepseek", "glm5")

_NEVEK = (
    (r"kimi", "kimi"),
    (r"deep[\s-]?seek|d[ií]psz[ií]k", "deepseek"),
    (r"glm(?:[\s-]?5(?:[.,]\d)?)?|dzsíelem", "glm5"),
    (r"feldwebel|feldvébel|feldvebel", "feldwebel"),
    (r"faber|fáber", "faber"),
    # cli-Claus: a Kommandant gépén futó Claude Code (11-22: „ha be van kapcsolva a gépen, akkor itt is látnom kéne”).
    # A puszta „Claus” a Bridge (Feldwebel) marad — a bot neve is Claus.
    (r"cli[\s-]?claus|kli[\s-]?klausz|claude[\s-]?code|g[ée]pi\s+claus", "claus"),
)
_NEV = "|".join(f"(?:{n})" for n, _ in _NEVEK)
_RAG = r"(?:-?(?:nek|nak|et|ot|t|tól|től|hoz|hez|höz))?"
_ELEJE = re.compile(rf"^\s*@?(?P<nev>{_NEV}){_RAG}(?=[\s,:!–\-]|$)[\s,:!–\-]*", re.IGNORECASE)
_KERD = re.compile(rf"^\s*(?:kérdezd\s+meg|kérdezzük\s+meg|szólj|írj|mondd\s+meg|üzend\s+meg|add\s+át|"
                   rf"továbbítsd)\s+(?:a|az)\s+(?P<nev>{_NEV}){_RAG}\b", re.IGNORECASE)


def _kulcs(nev: str) -> str | None:
    for minta, kulcs in _NEVEK:
        if re.fullmatch(minta, nev, re.IGNORECASE):
            return kulcs
    return None


def cimzett(szoveg: str) -> str | None:
    """Az üzenet elején megszólított ügynök kulcsa, vagy None."""
    s = szoveg or ""
    m = _ELEJE.match(s) or _KERD.match(s)
    return _kulcs(m.group("nev")) if m else None


def modell_rovid(modell: str) -> str:
    """'claude-sonnet-5-5' → 'Sonnet 5.5'; SiliconFlow-azonosítónál az utolsó tag ('Kimi-K2.7-Code')."""
    m = (modell or "").strip()
    if m.startswith("claude-"):
        reszek = m[len("claude-"):].split("-")
        return f"{reszek[0].capitalize()} {'.'.join(reszek[1:])}".strip()
    return m.rsplit("/", 1)[-1]


def fejlec(kulcs: str, modell: str | None = None) -> str:
    """„🔭 <b>KIMI</b> · Kimi-K2.7-Code” — ikon, név, és a TÉNYLEGESEN válaszoló modell (ha ismert)."""
    u = UGYNOKOK.get(kulcs, {"nev": (kulcs or "?").upper(), "ikon": "•"})
    return f"{u['ikon']} <b>{u['nev']}</b>" + (f" · {modell_rovid(modell)}" if modell else "")


_BIZTATAS = re.compile(r"\b(hajr[áa]|mehet|rajta|csin[áa]ld|igen|ok[ée]?|j[óo]|[ée]les[íi]tsd|go|let'?s go)\b", re.IGNORECASE)
_TAGADAS = re.compile(r"\b(m[ée]gse|ne\b|[áa]llj|v[áa]rj|nem)\b", re.IGNORECASE)


def biztatas(szoveg: str) -> bool:
    """„Oké Faber, hajrá!”, „mehet”, „csináld” — igenlő biztatás, tagadás nélkül."""
    s = szoveg or ""
    return bool(_BIZTATAS.search(s)) and not _TAGADAS.search(s)


_ALLAPOT = re.compile(r"\?|\b(mi a helyzet|hol tartasz|hol tart|[áa]llapot|mit csin[áa]lsz|k[ée]sz vagy)\b", re.IGNORECASE)


def allapot_kerdes(szoveg: str) -> bool:
    return bool(_ALLAPOT.search(szoveg or ""))
