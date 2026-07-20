"""plugins/delphoi_brands.py — DELPHOI brand-registry (A1-VÁZ, MODIFIKATION 2).

"Egy motor, két arc": a DELPHOI-Core brand-agnosztikus — az Echolot-kirakat és
a SaaS-arc (aipolling.io) ugyanazt a motort kapja, más köntösben.

VASSZABÁLY:
  - ÚJ BRAND = ÚJ CONFIG-SOR ebben a registry-ben (vagy env-felülírás), NEM kód.
  - Echolot-specifikumot (név, láb-szöveg, URL, logó) a Core-ba égetni TILOS —
    minden brand-függő megjelenítési elem INNEN jön (a B2 riport-réteg fogja
    használni; most még SEMMI nem hivatkozik rá, ez szándékos).
  - Ismeretlen brand_key = HANGOS hiba (nincs csendes default-ra csúszás).

Default brand: 'echolot' (DELPHOI_DEFAULT_BRAND env írja felül).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("plugins.delphoi_brands")

__plugin_meta__ = {
    "name": "delphoi_brands",
    "version": "1.0.0",
    "description": "DELPHOI brand-registry — brand-agnosztikus Core, arc-onkenti config (A1-vaz)",
}

# brand_key → megjelenítési config. A 'echolot' a MAI értékekkel (a FOGÁS-UI
# disclaimere + a nyilvános irányfal-terminológia); az Echolot logója szöveg-
# logó ("ECHOLOT" — echolot_dashboard CSS), ezért a logo_path üres.
# A SaaS-arc (aipolling.io) sora AKKOR kerül be, amikor a Kommandant a nevét/
# arculatát rögzíti — soronként, kód-változtatás nélkül.
BRANDS: dict = {
    # P6 #15 brand-próba: teszt-brand KIZÁRÓLAG config-sorként (kód nem változik)
    "sibylle-dev": {
        "name": "DELPHOI (dev)",
        "logo_path": "",
        "footer_text": "DELPHOI — synthetic polling engine (dev brand)",
        "disclaimer_text": ("Synthetic direction signal — complementary to "
                            "probability-sample polls, not a substitute."),
        "public_base_url": "https://aipolling-production.up.railway.app",
    },
    "echolot": {
        "name": "Echolot",
        "logo_path": "",   # szöveg-logó (ECHOLOT) — nincs képfájl
        "footer_text": "Echolot — szintetikus fókuszcsoport / narratíva-hatás szimuláció",
        "disclaimer_text": ("Szintetikus panel relatív jelzése, nem abszolút mérés "
                            "és nem közvélemény-kutatás."),
        "public_base_url": os.environ.get("ECHOLOT_PUBLIC_ORIGIN", "").rstrip("/")
                           or "https://echolotnews.com",
    },
}

_REQUIRED_FIELDS = ("name", "logo_path", "footer_text", "disclaimer_text",
                    "public_base_url")

# ── MOD2/A6 — a PUBLIKUS adat-API (nowcast/verify) BRAND-SEMLEGES disclaimere.
# Nem brand-config-sor: a kirakat-feedet BÁRMELY arc (Echolot, SaaS) fogyasztja,
# ezért a payloadban brand-név/URL nem szerepelhet — az EGY közös, semleges
# szöveg itt, a registry mellett él (egy igazságforrás, a brandek e köré
# öltöztetik a saját köntösüket).
NEUTRAL_DISCLAIMER = ("Szintetikus panel relatív irányjelzése — nem "
                      "közvélemény-kutatás és nem abszolút mérés.")


def public_disclaimer() -> str:
    """A publikus (auth nélküli) delphoi adat-végpontok disclaimere —
    brand-semleges, a payload sosem hordoz arc-specifikus szöveget."""
    return NEUTRAL_DISCLAIMER


def get_brand(key: str | None = None) -> dict:
    """A brand-config MÁSOLATA (a hívó nem tudja elrontani a registry-t).
    key=None → DELPHOI_DEFAULT_BRAND env (default: 'echolot').
    Ismeretlen kulcs → hangos ValueError a ismert kulcsok listájával."""
    key = (key or os.environ.get("DELPHOI_DEFAULT_BRAND", "echolot")).strip().lower()
    if key not in BRANDS:
        raise ValueError(
            f"ismeretlen DELPHOI brand: {key!r} — ismert: {sorted(BRANDS)} "
            "(új brand = új config-sor a plugins/delphoi_brands.py BRANDS-ében)")
    brand = dict(BRANDS[key])
    brand["brand_key"] = key
    missing = [f for f in _REQUIRED_FIELDS if f not in brand]
    if missing:
        raise ValueError(f"hiányos brand-config ({key}): {missing}")
    return brand


def register_tools(app, deps):
    """Nem regisztrál MCP-toolt (tool-count fegyelem) — a registry könyvtár-modul."""
    logger.info("delphoi_brands betöltve (%d brand, default: %s)",
                len(BRANDS), os.environ.get("DELPHOI_DEFAULT_BRAND", "echolot"))
