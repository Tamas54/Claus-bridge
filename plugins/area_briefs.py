"""NYELVTERÜLETI MAKRÓ-BRIEFEK — 12 kiadás, mindegyik a SAJÁT országával.

MIÉRT ÍGY (Kommandant, 2026-08-31)
----------------------------------
"A magyarra vonatkozó politikai és gazdasági adatok nem fognak egy törököt
érdekelni — vagy épp egy olaszt. Őt a sajátja érdekelné."
"A globális rész közös, de a nyelvterületi rész különböző."

Ezért NEM egy brief tizenkét fordítása készül, hanem tizenkét kiadás:
  * HAZAI blokk — a nyelvterület saját országának makró-számai,
  * KÖZÖS horgony — eurózóna + USA, minden kiadásban ugyanaz, hogy a hazai
    szám viszonyítható legyen.

MIÉRT NULLA LLM
---------------
Ebben a briefben csak SZÁMOK és CÍMKÉK vannak. A számok nyelvfüggetlenek; a
címkéket az Echolot 12 nyelvű, kézzel írt `t()` szótára adja. Modell nem kell
hozzá — és ez nem csak olcsóbb, hanem BIZTONSÁGOSABB is: egy táblázat, amit
nem modell tölt ki, nem tud kitalálni egy sort.
(Ma reggel épp ez történt a Telegram-válaszban: a modell két jegybankot
hozzáírt egy háromsoros tool-válaszhoz, mindkettőt rosszul.)

MIÉRT EGY HÍVÁS
---------------
A `get_macro_panel` ország × mutató mátrixot ad EGY kérésre, cellánként
forrással, időszakkal és MÉRTÉKEGYSÉGGEL. Tizenkét kiadás adata így egyetlen
StatData-hívás — nem tizenkettő, és nem 12×N.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("bridge.area_briefs")

#: nyelv → a kiadás SAJÁT országa. Ahol nincs megbízható hazai forrás, üres:
#: az a kiadás CSAK a közös horgonyt kapja. A bevallott hiány jobb, mint egy
#: találati listából szedett szám (mérve 2026-08-31: ru/uk web-keresés vagy
#: semmi).
AREA_COUNTRY: dict[str, str] = {
    "hu": "HU", "de": "DE", "es": "ES", "fr": "FR", "it": "IT",
    "pl": "PL", "el": "GR", "tr": "TR", "zh": "CN", "en": "GB",
    "ru": "", "uk": "",
}

#: A HAZAI blokk mutatói, megjelenítési sorrendben. Ami egy országra nem
#: oldódik fel, az egyszerűen kimarad — nem lesz üres sor, és nem lesz
#: helyettesítő szám sem.
HOME_INDICATORS = ("cpi", "core_cpi", "policy_rate", "unemployment",
                   "gdp_growth", "bond_yield_10y", "wages", "gov_debt",
                   "house_prices", "retail_trade", "industrial_production")

#: A KÖZÖS horgony — szűkebb, mert viszonyítás, nem önálló szemle.
ANCHOR_COUNTRIES = ("EA", "US")
ANCHOR_INDICATORS = ("cpi", "core_cpi", "policy_rate", "unemployment")


def _fresh_only(rows: list) -> list:
    """Csak FRISS és RÁTA típusú cellák.

    ⚠️ KÉT SZŰRŐ, KÉT KÜLÖN OK:
      * `status != fresh` — egy 8 hónapos nemzeti maginfláció rosszabb, mint
        a hiánya: az olvasó nem tudja, hogy régi (mérve: az ECB nemzeti
        core-sorozatai 2025-12-nél állnak).
      * `unit_kind != rate` — egy SZINT (millió EUR, USD/óra) ugyanúgy szám,
        de az olvasó százalékként olvasná.
    """
    return [r for r in rows
            if r.get("status") == "fresh" and r.get("unit_kind") == "rate"]


async def build_area_briefs(statdata_call) -> dict:
    """Mind a 12 kiadás makró-adata. `statdata_call(tool, args)` a hívó dolga.

    Visszaad: {lang: {country, home: [...], anchor: [...], asof, gaps: [...]}}
    Sose dob — hibánál üres dict, és a hívó ezt LÁTJA.
    """
    orszagok = [c for c in dict.fromkeys(
        [v for v in AREA_COUNTRY.values() if v] + list(ANCHOR_COUNTRIES))]
    mutatok = list(dict.fromkeys(HOME_INDICATORS + ANCHOR_INDICATORS))

    # ⚠️ KOTEGELES. A panel cella-plafonja 120, es az NEM onkenyes: minden
    # cella kulon kulso lekeres, a forrasoknal (FRED, Eurostat) rate limit
    # van. 12 orszag × 11 mutato = 132 — ELUTASITVA, es jol tette: elesben
    # pontosan ez tortent az elso futasnal.
    # Orszagonkent kotegelunk, mert a mutato-lista fix; egy kotegben legfeljebb
    # 100 cella.
    KOTEG = max(1, 100 // max(1, len(mutatok)))
    kotegek = [orszagok[i:i + KOTEG] for i in range(0, len(orszagok), KOTEG)]
    sorok: list[dict] = []
    for koteg in kotegek:
        try:
            res = await statdata_call("get_macro_panel", {
                "countries": ",".join(koteg),
                "indicators": ",".join(mutatok),
            })
            if isinstance(res, str):
                res = json.loads(res)
        except Exception as e:  # noqa: BLE001
            logger.error("area_briefs: panel-hivas elbukott (%s): %s", koteg, e)
            continue
        if not res or not res.get("rows"):
            logger.error("area_briefs: ures koteg (%s): %s", koteg, str(res)[:180])
            continue
        sorok += res["rows"]
    if not sorok:
        logger.error("area_briefs: egyetlen koteg sem adott adatot")
        return {}

    per: dict[str, dict[str, dict]] = {}
    for sor in sorok:
        per.setdefault(sor["country"], {})[sor["indicator"]] = sor

    now = datetime.now(timezone.utc).isoformat()
    out: dict[str, dict] = {}
    for lang, cc in AREA_COUNTRY.items():
        hazai = _fresh_only([per.get(cc, {})[i] for i in HOME_INDICATORS
                             if i in per.get(cc, {})]) if cc else []
        horgony = []
        for ac in ANCHOR_COUNTRIES:
            horgony += _fresh_only([per.get(ac, {})[i] for i in ANCHOR_INDICATORS
                                    if i in per.get(ac, {})])
        out[lang] = {
            "lang": lang, "country": cc,
            "home": hazai, "anchor": horgony,
            "asof": now,
            # A HIÁNYT KIMONDJUK: melyik hazai mutató nem került be és miért.
            # Enélkül egy rövidebb táblázat úgy nézne ki, mintha az az ország
            # kevesebb mutatót ismerne — pedig csak nekünk nincs meg.
            "gaps": [i for i in HOME_INDICATORS
                     if cc and i not in {r["indicator"] for r in hazai}],
        }
    return out


def store_area_briefs(get_db, briefs: dict) -> int:
    """A 12 kiadás eltárolása a `briefs` táblába, kiadásonként egy sor.

    Ugyanaz a tábla, mint az Economic Briefé (kind='area'), mert ugyanaz a
    kérdés: "mi a legutóbbi X?" — es a fajl-archivum EFEMER, minden deploy
    elmossa. Ami nincs a /data koteten, az nincs.
    """
    if not briefs:
        return 0
    n = 0
    try:
        conn = get_db()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS briefs ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,"
            " session TEXT NOT NULL, asof TEXT NOT NULL,"
            " lang TEXT NOT NULL DEFAULT 'hu', payload TEXT NOT NULL,"
            " created_at TEXT NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_briefs_kind_asof "
                     "ON briefs(kind, session, asof DESC)")
        most = datetime.now(timezone.utc).isoformat()
        for lang, payload in briefs.items():
            conn.execute(
                "INSERT INTO briefs (kind, session, asof, lang, payload, created_at) "
                "VALUES ('area','daily',?,?,?,?)",
                (payload.get("asof", most), lang,
                 json.dumps(payload, ensure_ascii=False), most))
            n += 1
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001
        logger.error("area_briefs: tarolas sikertelen: %s", e)
        return 0
    return n


def load_area_brief(get_db, lang: str) -> dict | None:
    """A `lang` kiadas LEGUTOBBI makro-briefje. Sose dob."""
    try:
        conn = get_db()
        r = conn.execute(
            "SELECT payload, asof, created_at FROM briefs "
            "WHERE kind='area' AND lang=? ORDER BY id DESC LIMIT 1",
            (lang,)).fetchone()
        conn.close()
        if not r:
            return None
        d = json.loads(r["payload"])
        d["created_at"] = r["created_at"]
        return d
    except Exception as e:  # noqa: BLE001
        logger.warning("area_briefs: betoltes sikertelen (%s): %s", lang, e)
        return None


async def cron_entry(get_db, statdata_call) -> dict:
    """Napi futas: 12 kiadas eloallitasa es tarolasa. Sose dob."""
    briefs = await build_area_briefs(statdata_call)
    n = store_area_briefs(get_db, briefs)
    teljes = sum(1 for b in briefs.values() if b.get("home"))
    logger.info("area_briefs: %d kiadas tarolva, ebbol %d-nek van HAZAI blokkja "
                "(a tobbi csak a kozos horgonyt kapja)", n, teljes)
    return {"stored": n, "with_home": teljes,
            "langs": sorted(briefs), "ok": n > 0}
