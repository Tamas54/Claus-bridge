"""ÖNJAVÍTÁS (2026-11-22) — az önvizsgálat leleteiből Faber-kérelem; ami emberi kéz, abból egyetlen Telegram-mondat.

A Kommandant (a webes Claus átvizsgálása után): „ezeket a hibákat pl a Bridge-nek magának is meg kéne tudnia
oldania majd”. A SIA/Faber-koncepció: ha hibát talál, megjavítja magában — a Kommandant csak jóváhagy.

A KÖR (a cron-szálból, `PERIODUS_S`-enként)
-------------------------------------------
1. `selfdiag.diagnose_all()` — minden szerv próbája élesben.
2. Minden TARTÓS (`persistent`) vagy RÉSZLEGES (`degraded`) leletnél:
   * EMBERI teendő (lejárt Google-belépés, elfogyott egyenleg) → egy Telegram-mondat: mi a baj, mi az EGY lépés;
   * minden más → Faber-kérelem a `bridge` műhelynek: derítse ki az okát (rossz próba? kód? konfig?), javítsa
     teszttel, és kérjen jóváhagyást. A Faber a [Mehet] után merge-öl, füstteszt, push → a Railway deployol.
3. Ugyanarra a leletre `ISMETLES_TILTAS_S`-en belül nem szól újra (a jelzés ideje a `shared_memory`-ben, így egy
   deploy-újraindulás sem ismétli meg).

Szándékosan NEM javít magától élesben: a kód a Faber munkafájában készül, és a Kommandant [Mehet]-je nélkül nem
kerül ki. „Javaslat követett változásként, az ember felel.”
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable

logger = logging.getLogger("onjavitas")

PERIODUS_S = 6 * 3600
ISMETLES_TILTAS_S = 24 * 3600
KULCS_ELOTAG = "onjavitas_jelzes_"

#: (komponens, jel a leletben, az EGY emberi lépés) — ezeket kód nem javítja meg.
EMBERI = (
    ("google", "invalid_grant",
     "A Gmail és a Naptár belépése lejárt (invalid_grant), ezért a reggeli brief kimarad. Egy lépés: szólj "
     "Clausnak a gépen („csináld meg a Google-belépést”), ő elindítja, neked csak az engedélyezésre kell kattintanod. "
     "Hogy ne járjon le hetente: Google Cloud → OAuth consent screen → Publish app (Production)."),
    ("siliconflow", "insufficient",
     "A SiliconFlow-egyenleg elfogyott (402). A Bridge az Anthropic-kapun megy tovább; a SiliconFlow-modellekhez "
     "(Agora, rizsrakéták) tölts fel, ha kell."),
    ("siliconflow", "402",
     "A SiliconFlow-egyenleg elfogyott (402). A Bridge az Anthropic-kapun megy tovább; a SiliconFlow-modellekhez "
     "(Agora, rizsrakéták) tölts fel, ha kell."),
)


def _szoveg(lelet: dict) -> str:
    return json.dumps(lelet, ensure_ascii=False).lower()


def osztalyoz(komponens: str, lelet: dict) -> tuple[str, str]:
    """('ember', teendő) vagy ('kod', a Faber kívánsága)."""
    sz = _szoveg(lelet)
    for k, jel, teendo in EMBERI:
        if k == komponens and jel in sz:
            return "ember", teendo
    bukott = [e for e in lelet.get("evidence") or [] if e.get("ok") is False]
    bizonyitek = "; ".join(f"{e.get('probe')}: {str(e.get('detail'))[:160]}" for e in bukott[:5])
    kivansag = (
        f"A Bridge önvizsgálata (self_diagnose) szerint a(z) `{komponens}` szerv "
        f"{'TARTÓSAN hibás' if lelet.get('verdict') == 'persistent' else 'RÉSZBEN hibás'}: "
        f"{lelet.get('summary', '')[:400]}"
        + (f" Bukott próbák: {bizonyitek}." if bizonyitek else "")
        + " Derítsd ki az okát: ha a PRÓBA mér rosszul (pl. hiányzó hitelesítés, csak konfigurációt néz), a próbát "
          "javítsd (a selfdiag-próbák a server.py-ban vannak); ha a kód vagy a konfiguráció hibás, azt. "
          "Teszttel, a meglévő tesztek zöldek maradjanak."
    )
    return "kod", kivansag


_ATMENETI_JELEK = ("timeout", "timed out", "readtimeout", "connecttimeout", "rate_limit", "429", "temporarily")


def atmeneti_kulso(lelet: dict) -> bool:
    """RÉSZLEGES ítélet, ahol MINDEN bukott próba átmeneti szállítási/torlódási hiba → nem kódhiba."""
    if lelet.get("verdict") != "degraded":
        return False
    bukott = [e for e in lelet.get("evidence") or [] if e.get("ok") is False]
    return bool(bukott) and all(any(j in str(e.get("detail", "")).lower() for j in _ATMENETI_JELEK) for e in bukott)


def _mar_jelezve(get_db, kulcs: str, most: float) -> bool:
    try:
        conn = get_db()
        try:
            r = conn.execute("SELECT value FROM shared_memory WHERE key = ?", (KULCS_ELOTAG + kulcs,)).fetchone()
        finally:
            conn.close()
        if not r:
            return False
        return most - float(json.loads(r[0]).get("ido", 0)) < ISMETLES_TILTAS_S
    except Exception as e:  # noqa: BLE001 — ha nem tudjuk, inkább szóljunk egyszer többször, mint soha
        logger.warning("onjavitas: a korábbi jelzés nem olvasható (%s): %s", kulcs, e)
        return False


def _megjegyez(get_db, memoria_ir, kulcs: str, most: float, osszegzes: str) -> None:
    """A jelzés ideje a shared_memory-be — a közös írási úton (`server.memoria_ir`), nem saját SQL-lel."""
    try:
        conn = get_db()
        try:
            memoria_ir(conn, KULCS_ELOTAG + kulcs,
                       json.dumps({"ido": most, "osszegzes": osszegzes[:300]}, ensure_ascii=False),
                       category="onjavitas", tags="onjavitas", updated_by="onjavitas")
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("onjavitas: a jelzés nem menthető (%s): %s", kulcs, e)


async def kor(diagnozis: dict, *, get_db, memoria_ir: Callable, faber_kerelem: Callable, telegram: Callable,
              most: float | None = None) -> dict:
    """Egy önjavító kör egy kész `diagnose_all()` eredményen. → {faber: [...], ember: [...], kihagyva: [...]}."""
    most = time.time() if most is None else most
    out = {"faber": [], "ember": [], "kihagyva": []}
    for komp, lelet in sorted((diagnozis.get("components") or {}).items()):
        if lelet.get("verdict") not in ("persistent", "degraded"):
            continue
        if atmeneti_kulso(lelet):
            # Egy külső szolgáltató pillanatnyi időtúllépése vagy torlódása (pl. egyetlen SiliconFlow-modell ReadTimeout-ja
            # RÉSZLEGES ítélettel) nem kódhiba — Faber-kérelmet nem ér (11-22: ebből lett egy fölösleges #4).
            out["kihagyva"].append(komp)
            continue
        tipus, szoveg = osztalyoz(komp, lelet)
        kulcs = f"{komp}_{tipus}"
        if _mar_jelezve(get_db, kulcs, most):
            out["kihagyva"].append(komp)
            continue
        if tipus == "ember":
            await telegram(f"🩺 <b>Bridge-önvizsgálat</b>\n\n{szoveg}")
            out["ember"].append(komp)
        else:
            mid = faber_kerelem(szoveg)
            if not mid:
                logger.warning("onjavitas: a Faber-kérelem nem ment ki (%s)", komp)
                continue
            await telegram(f"🩺 <b>Bridge-önvizsgálat</b>\n\nA(z) <code>{komp}</code> hibás; átadtam a Fabernek, "
                           f"kijavítja és jóváhagyást kér.")
            out["faber"].append(komp)
        _megjegyez(get_db, memoria_ir, kulcs, most, lelet.get("summary", ""))
    if out["faber"] or out["ember"]:
        logger.info("onjavitas kör: Faber=%s, ember=%s, kihagyva=%s", out["faber"], out["ember"], out["kihagyva"])
    return out
