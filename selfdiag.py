"""
ÖNVIZSGÁLAT — a Bridge derítse ki magáról, mi a baja.
======================================================

A KIVÁLTÓ ESET (task #407, 2026-08-30)
--------------------------------------
A `daily_news_brief` cron 0,55 másodperc alatt elbukott. A Kommandant ennyit
kapott Telegramon:

    daily_news_brief — HIBÁS FUTÁS, nincs brief
    Ok (kód): empty_response

Ez igaz volt, és **használhatatlan**. Az igazi ok — a SiliconFlow HTTP 400-at
adott a GLM-5.2-re, egyetlen egyszer, hat hibátlan nap után — csak a
Railway-logból derült ki, kézzel. A rendszer TUDTA, hogy elromlott valami, de
nem tudta megmondani, MI.

MIT CSINÁL EZ A MODUL
---------------------
Amikor bárhol tipizált hiba keletkezik, a hívó ideszól, és a modul **lemér**:
végigfuttatja az érintett komponens próbáit, és a mérésből ítéletet mond.

    diagnose("siliconflow", "model_unavailable")
      → "ÁTMENETI: a hiba óta a komponens minden próbája ZÖLD"
        + bizonyíték: mit mért, mennyi idő alatt

HÁROM SZABÁLY, AMI NÉLKÜL EZ TÖBBET ÁRTANA, MINT HASZNÁL
--------------------------------------------------------
1. **A diagnózis SOSE találgat.** Három ítélet van: `persistent` (a komponens
   MOST is halott), `transient` (most él, tehát a hiba elmúlt) és `unknown`
   (nincs próba, vagy nem eldönthető). A harmadikat nem szégyelljük — a
   `recipe_health` UNKNOWN-elve ugyanez: egy „nem tudom" sosem álcázhatja
   magát tudásnak.

2. **A vakfolt LÁTSZIK.** Ha egy komponensre nincs próba, a diagnózis ezt
   KIMONDJA, és megnevezi, mi hiányzik. Enélkül a csendből azt hinnénk, hogy
   minden rendben — pontosan az a hibaosztály, ami ellen ez a modul készült.

3. **A próba SOSE dobhat.** Egy önvizsgálat, ami maga esik szét egy hiba
   diagnosztizálása közben, elveszi a maradék információt is. Minden próba
   kivétele elnyelve, `unknown`-ként.

A modul KÖNYVTÁR-jellegű: nincs benne se hálózat, se DB-séma-ismeret. A
próbákat a `server.py` regisztrálja induláskor, a `recipe_health` mintájára —
így ez a fájl teszteléshez teljesen izolálható.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger("bridge.selfdiag")


class Verdict(Enum):
    """A diagnózis kimenete. `UNKNOWN` teljes értékű válasz, nem kudarc."""

    PERSISTENT = "persistent"   # a komponens MOST is elromlott állapotban van
    TRANSIENT = "transient"     # a hiba óta minden próba zöld → elmúlt
    DEGRADED = "degraded"       # egy része él, egy része nem
    UNKNOWN = "unknown"         # nincs próba / nem eldönthető


@dataclass
class ProbeResult:
    """Egy próba eredménye. `ok=None` = a próba maga nem tudott dönteni."""

    name: str
    ok: Optional[bool]
    detail: str = ""
    elapsed_ms: int = 0

    def as_dict(self) -> dict:
        return {"probe": self.name, "ok": self.ok,
                "detail": self.detail[:400], "elapsed_ms": self.elapsed_ms}


@dataclass
class Diagnosis:
    component: str
    symptom: str
    verdict: Verdict
    summary: str
    evidence: list = field(default_factory=list)
    missing_probes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "component": self.component,
            "symptom": self.symptom,
            "verdict": self.verdict.value,
            "summary": self.summary,
            "evidence": [e.as_dict() for e in self.evidence],
            # A vakfolt nem tűnhet el: ha nincs próba, azt a diagnózis
            # KIMONDJA, nem hallgatja el.
            "unmeasured": self.missing_probes,
        }


# ── Próba-regiszter ──────────────────────────────────────────────────────
# A `server.py` tölti fel induláskor. Komponensenként több próba lehet
# (pl. a `siliconflow` alatt modellenként egy).
_PROBES: dict[str, dict[str, Callable[[], ProbeResult]]] = {}

#: Komponensek, amikről TUDJUK, hogy léteznek, de még nincs rájuk próba.
#: Enélkül egy nem regisztrált komponens úgy nézne ki, mintha nem is lenne —
#: a hiányzó mérés és a hiánytalan működés nem keverhető össze.
_KNOWN_COMPONENTS: set[str] = set()


def register_probe(component: str, name: str, fn: Callable[[], ProbeResult]) -> None:
    _PROBES.setdefault(component, {})[name] = fn
    _KNOWN_COMPONENTS.add(component)


def declare_component(component: str) -> None:
    """Komponens bejelentése próba NÉLKÜL — hogy a hiánya látszódjon."""
    _KNOWN_COMPONENTS.add(component)


def clear_probes() -> None:
    _PROBES.clear()
    _KNOWN_COMPONENTS.clear()


def registered() -> dict:
    return {c: sorted(p) for c, p in sorted(_PROBES.items())}


def _run_probe(name: str, fn) -> ProbeResult:
    """Egy próba futtatása úgy, hogy a kivétele NE vigye el a diagnózist."""
    t0 = time.time()
    try:
        r = fn()
        if not isinstance(r, ProbeResult):
            return ProbeResult(name, None, f"a próba nem ProbeResult-ot adott: {type(r).__name__}",
                               int((time.time() - t0) * 1000))
        r.elapsed_ms = r.elapsed_ms or int((time.time() - t0) * 1000)
        return r
    except Exception as e:  # noqa: BLE001 — lásd a modul-docstring 3. szabályát
        return ProbeResult(name, None, f"a próba maga hasalt el: {type(e).__name__}: {e}",
                           int((time.time() - t0) * 1000))


def diagnose(component: str, symptom: str = "") -> Diagnosis:
    """Egy komponens ÁLLAPOTÁNAK megmérése egy észlelt hiba után.

    A `symptom` a hívó tipizált hibakódja (pl. `model_unavailable`). Nem a
    diagnózis bemenete, hanem a kontextusa: azt írjuk le, MI romlott el, a
    mérés pedig azt, hogy MOST mi a helyzet.
    """
    probes = _PROBES.get(component, {})
    if not probes:
        known = component in _KNOWN_COMPONENTS
        return Diagnosis(
            component, symptom, Verdict.UNKNOWN,
            summary=(
                f"Nincs próba a(z) `{component}` komponensre, ezért a hibát nem tudom "
                f"visszavezetni okra. " +
                ("A komponens ismert, csak mérőeszköz nincs hozzá — ez a mi hiányunk, "
                 "nem a komponensé." if known else
                 "A komponens NEM ismert: vagy elgépelt név, vagy olyan rész, amiről "
                 "az önvizsgálat még nem tud.")
            ),
            missing_probes=[component],
        )

    results = [_run_probe(n, fn) for n, fn in sorted(probes.items())]
    ok = [r for r in results if r.ok is True]
    bad = [r for r in results if r.ok is False]
    unk = [r for r in results if r.ok is None]

    if bad and not ok:
        verdict = Verdict.PERSISTENT
        summary = (f"TARTÓS: a(z) `{component}` MOST IS hibás — "
                   f"{len(bad)}/{len(results)} próba bukott. "
                   f"Első ok: {bad[0].detail[:200]}")
    elif bad and ok:
        verdict = Verdict.DEGRADED
        summary = (f"RÉSZLEGES: a(z) `{component}` egy része él, egy része nem "
                   f"({len(ok)} zöld, {len(bad)} bukott). "
                   f"Bukott: {', '.join(r.name for r in bad)}")
    elif ok:
        # ⚠️ AZ ELSŐ ÉLES FUTÁS TANULSÁGA: a korábbi alak `ok and not unk`-ot
        # kért, és ettől 7 modellből 6 zöld mellett "NEM ELDÖNTHETŐ"-t mondott,
        # mert EGY modell rate-limitelt volt. Ez a mérés eldobása: ha van zöld
        # próba és NINCS piros, a komponens működik — a bizonytalan részt
        # megnevezzük, de nem tesszük úgy, mintha semmit nem tudnánk.
        verdict = Verdict.TRANSIENT
        summary = (f"ÁTMENETI: a(z) `{component}` most zöld "
                   f"({len(ok)}/{len(results)} próba), tehát a hiba azóta elmúlt. "
                   f"Újrafuttatás valószínűleg sikerülne.")
        if unk:
            summary += (f" Bizonytalan: {', '.join(r.name for r in unk)} "
                        f"({unk[0].detail[:120]})")
    else:
        verdict = Verdict.UNKNOWN
        summary = (f"NEM ELDÖNTHETŐ: a(z) `{component}` próbái nem adtak ítéletet "
                   f"({len(unk)} bizonytalan). A mérőeszköz nem elég, nem a komponens néma.")

    return Diagnosis(component, symptom, verdict, summary, evidence=results,
                     missing_probes=[])


def diagnose_all() -> dict:
    """Minden regisztrált komponens megmérése — a „mi bajod van?" kérdésre.

    A regisztrált, de próba nélküli komponensek KÜLÖN listában jelennek meg:
    a csend és a jó egészség nem ugyanaz.
    """
    out = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "components": {}, "unmeasured": []}
    for comp in sorted(_KNOWN_COMPONENTS):
        if comp in _PROBES and _PROBES[comp]:
            out["components"][comp] = diagnose(comp).as_dict()
        else:
            out["unmeasured"].append(comp)
    healthy = sum(1 for d in out["components"].values() if d["verdict"] == "transient")
    broken = [c for c, d in out["components"].items()
              if d["verdict"] in ("persistent", "degraded")]
    out["summary"] = (
        f"{len(out['components'])} komponens mérve, {healthy} zöld, "
        f"{len(broken)} hibás{': ' + ', '.join(broken) if broken else ''}"
        + (f" · {len(out['unmeasured'])} komponensre NINCS próba: "
           f"{', '.join(out['unmeasured'])}" if out["unmeasured"] else "")
    )
    return out
