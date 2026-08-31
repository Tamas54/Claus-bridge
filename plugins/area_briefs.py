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
import os
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

#: OPCIONÁLIS hazai mutatók: megjelennek, ahol vannak, de a hiányuk NEM hiány.
#:
#: Kommandant, 2026-08-31: "tegyük hozzá a magyarokhoz a szar saját KSH-s
#: adatokat is." — a harmonizált marad a mérce (mert csak az hasonlítható
#: össze a 12 kiadás között), de a HAZAI olvasó a saját statisztikai hivatala
#: számát látja a hírekben, és a kettő MESSZE eltérhet:
#:     HU maginfláció, harmonizált (Eurostat HICP): 3,7%
#:     HU maginfláció, nemzeti     (KSH):           1,9%
#: Mindkettő igaz — MÁS KOSÁR. Ha csak az egyiket mutatnánk, az olvasó azt
#: hinné, tévedünk; ha címke nélkül mutatnánk mindkettőt, azt, hogy egyikük
#: hibás. Ezért külön mutatónév és külön sor.
#:
#: Miért nem kerülnek a `gaps`-be: nemzeti maginfláció-sorozat CSAK ott van,
#: ahol a hivatal külön közli (mérve: HU, FR, IT). Ha ezt hiányként
#: jelentenénk, kilenc kiadás kapna egy örökre kitölthetetlen hiánysort — a
#: hiánylista pedig pontosan attól hasznos, hogy MEGJAVÍTHATÓ dolgokat sorol.
HOME_OPTIONAL = ("core_cpi_national",)

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
      * `confidence != official` — a StatData webkeresésből is tud értéket
        regexelni, és az UGYANÚGY NÉZ KI, mint egy hivatalos sorozat: ugyanaz
        a mező, ugyanaz a "fresh". Mérve 2026-08-31: a kínai munkanélküliségre
        12,5%-ot adott — az az IFJÚSÁGI ráta egy találati listáról, az
        országos ~5%. Egy tizenkét kiadásban megjelenő táblázatba csak az
        kerülhet, aminek NEVE VAN a forrásoldalon.
    """
    return [r for r in rows
            if r.get("status") == "fresh" and r.get("unit_kind") == "rate"
            and r.get("confidence", "official") == "official"]


def iranyok(rows: list) -> list:
    """`delta` mezo minden cellara, ahol VAN elozo ertek.

    Nulla token: a nyil a szamokbol adodik, nem modellbol. Ahol nincs elozo
    megfigyeles, ott a `delta` None marad — es a megjelenites NEM rajzol
    nyilat. A "nem tudom" itt is teljes erteku valasz.
    """
    for r in rows:
        e, u = r.get("prev_value"), r.get("value")
        try:
            r["delta"] = round(float(u) - float(e), 2) if e is not None else None
        except (TypeError, ValueError):
            r["delta"] = None
    return rows


async def build_area_briefs(statdata_call) -> dict:
    """Mind a 12 kiadás makró-adata. `statdata_call(tool, args)` a hívó dolga.

    Visszaad: {lang: {country, home: [...], anchor: [...], asof, gaps: [...]}}
    Sose dob — hibánál üres dict, és a hívó ezt LÁTJA.
    """
    orszagok = [c for c in dict.fromkeys(
        [v for v in AREA_COUNTRY.values() if v] + list(ANCHOR_COUNTRIES))]
    mutatok = list(dict.fromkeys(HOME_INDICATORS + HOME_OPTIONAL + ANCHOR_INDICATORS))

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
        # A nemzeti maginfláció közvetlenül a harmonizált MÖGÉ kerül: így a
        # két szám egymás mellett olvasható, és az eltérés magyarázza magát.
        sorrend = []
        for i in HOME_INDICATORS:
            sorrend.append(i)
            if i == "core_cpi":
                sorrend += list(HOME_OPTIONAL)
        hazai = iranyok(_fresh_only([per.get(cc, {})[i] for i in sorrend
                                     if i in per.get(cc, {})])) if cc else []
        horgony = []
        for ac in ANCHOR_COUNTRIES:
            horgony += iranyok(_fresh_only(
                [per.get(ac, {})[i] for i in ANCHOR_INDICATORS
                 if i in per.get(ac, {})]))
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


# ══════════════════════════════════════════════════════════════════════════
# SZÖVEGES MAKRÓ-SZEMLE KIADÁSONKÉNT
# ══════════════════════════════════════════════════════════════════════════
#
# Kommandant, 2026-08-31: "és a gazdasági elemzés? Nem kerül alá?" —
# "mármint a szöveges?"
#
# A táblázat megmondja, MENNYI. Ez a blokk megmondja, MIT JELENT. A kettő
# között az a különbség, hogy a második ÍTÉLET, tehát el lehet rontani —
# ezért a szabályok szigorúbbak, mint bárhol máshol a rendszerben.
#
# NÉGY SZABÁLY, MIND EGY-EGY MÉRT HIBÁBÓL
# ---------------------------------------
# 1. AZ IRÁNYT NEM TALÁLJUK KI. Egy 3,7%-os maginflációról nem lehet tudni,
#    emelkedik-e. A prompt CSAK ott engedi az irány kimondását, ahol a cella
#    hozza az előző megfigyelést is. Ahol nincs `prev`, ott a modellnek
#    tilos irányt állítania — és ezt a validátor is ellenőrzi.
# 2. AZ OKOT SEM TALÁLHATJUK KI. A Telegram-briefben a modell egyszer egy
#    Warsh-narratívát írt a Fed kamata mellé, egy háromsoros tool-válaszból.
#    Itt nincs hírkontextus, tehát ok sincs — csak az, ami a számokból
#    LEVEZETHETŐ (pl. reálbér = bér − infláció).
# 3. AMI NINCS A BEMENETBEN, AZ NEM KERÜLHET A SZÖVEGBE. A validátor
#    kiszedi a szövegből a számokat, és ha olyan van, ami egyik cellában sem
#    szerepel, a szemle ELDOBÓDIK. Két korábbi élesben megjelent kitalált
#    táblázatsor (NBP 3,5 / BNR 3,3) pontosan így keletkezett.
# 4. FORRÁSMEGJELÖLÉS NEM MEGY A SZÖVEGBE. A táblázat minden sora hozza a
#    saját forrását — a mondatba tett hivatkozás csak zavar (a Kommandant
#    ezt a Telegram-briefnél külön kérte).

#: Legfeljebb ennyi mondat. Nem stílus-kérdés: egy hosszabb szemle
#: elkerülhetetlenül átcsúszik értelmezésbe, és az értelmezéshez itt nincs
#: adatunk.
REVIEW_MAX_MONDAT = 5

#: Hány karakteren felül vágjuk el biztonságból (a modell néha "elszabadul").
REVIEW_MAX_KAR = 900


def _tenyblokk(brief: dict) -> str:
    """A modellnek átadott TÉNYEK — és semmi más.

    Minden sor egy cella: mutató, érték, időszak, és ha van, az ELŐZŐ érték.
    Ahol nincs előző, ott ezt KIÍRJUK (`elozo: NINCS`), mert a hiány
    csendben ugyanúgy néz ki, mint a nulla.
    """
    sorok = []
    for cimke, tetelek in (("HAZAI", brief.get("home") or []),
                           ("HORGONY", brief.get("anchor") or [])):
        for r in tetelek:
            elozo = (f"{r['prev_value']} ({r.get('prev_period')})"
                     if r.get("prev_value") is not None else "NINCS")
            sorok.append(
                f"{cimke} {r.get('country', '')} {r['indicator']}: "
                f"{r['value']} @{r.get('period')} | elozo: {elozo}")
    return "\n".join(sorok)


def _szamok(szoveg: str) -> set:
    """A szövegben szereplő számok — a kitalált érték kiszűréséhez."""
    import re
    ki = set()
    for m in re.findall(r"-?\d+(?:[.,]\d+)?", szoveg or ""):
        try:
            ki.add(round(float(m.replace(",", ".")), 2))
        except ValueError:
            continue
    return ki


def validate_review(szoveg: str, brief: dict) -> list[str]:
    """A szemle ellenőrzése. Üres lista = rendben.

    Fail-closed: ha bármi gyanús, a szemle NEM jelenik meg. Egy hiányzó
    bekezdés bosszantó; egy kitalált szám a tizenkét kiadásban hazugság.
    """
    hibak = []
    if not szoveg or not szoveg.strip():
        return ["ures"]
    if len(szoveg) > REVIEW_MAX_KAR:
        hibak.append(f"tul hosszu ({len(szoveg)} kar)")
    # ── kitalált szám ──────────────────────────────────────────────────
    ismert = set()
    for r in (brief.get("home") or []) + (brief.get("anchor") or []):
        for k in ("value", "prev_value"):
            if r.get(k) is not None:
                try:
                    ismert.add(round(float(r[k]), 2))
                except (TypeError, ValueError):
                    pass
        # az idoszakbol jovo evszamok/negyedevek nem "kitalalt szamok"
        for darab in str(r.get("period") or "").replace("-Q", "-").split("-"):
            if darab.isdigit():
                ismert.add(round(float(darab), 2))
        for darab in str(r.get("prev_period") or "").replace("-Q", "-").split("-"):
            if darab.isdigit():
                ismert.add(round(float(darab), 2))
    # a kulonbsegek (pl. "2,1 ponttal magasabb") legitim szarmaztatott ertekek
    ertekek = [round(float(r["value"]), 2)
               for r in (brief.get("home") or []) + (brief.get("anchor") or [])
               if r.get("value") is not None]
    for a in ertekek:
        for b in ertekek:
            ismert.add(round(abs(a - b), 2))
    idegen = {x for x in _szamok(szoveg) if x not in ismert}
    if idegen:
        hibak.append(f"a bemenetben nem szereplo szam(ok): {sorted(idegen)[:6]}")
    # ── CJK-szivargas nem-kinai kiadasban ──────────────────────────────
    if brief.get("lang") != "zh" and any("一" <= ch <= "鿿" for ch in szoveg):
        hibak.append("CJK karakter nem-kinai kiadasban")
    return hibak


def _review_prompt(brief: dict, nyelv_nev: str) -> str:
    orszag = brief.get("country") or "-"
    return f"""FACTS (the ONLY numbers you may use):
{_tenyblokk(brief)}

TASK: Write a short macro commentary in {nyelv_nev} for readers in {orszag},
at most {REVIEW_MAX_MONDAT} sentences, plain prose, no headings, no bullet
points, no markdown.

HARD RULES — breaking any of these makes the whole commentary unusable:
1. DIRECTION: you may say a figure rose or fell ONLY where the fact line
   gives an `elozo` value. Where it says `elozo: NINCS`, you do NOT know the
   direction — state the level only, never imply a trend.
2. CAUSE: never explain WHY a number is what it is. You have no news, no
   policy statements, no context — only these numbers. An invented cause
   ("driven by energy prices", "after the central bank's decision") is the
   worst error you can make here.
3. NUMBERS: use ONLY numbers from the FACTS block, or differences you
   compute between two of them. Never introduce a figure that is not there.
4. NO SOURCES in the text: the table above already carries a source for
   every row. Do not name datasets, agencies or URLs.
5. If two measures of the same thing differ (e.g. harmonised vs national
   core inflation), say plainly that they are DIFFERENT MEASURES, not that
   one is wrong.

WHAT IS WORTH SAYING: which figure stands out against the euro-area/US
anchor; where the home economy is tighter or looser; a relationship the
numbers themselves support (e.g. wages minus inflation = real wage
direction). Write for an ordinary newspaper reader, not an economist.

Return ONLY the commentary text. No JSON, no preamble."""


#: nyelv → a modellnek adott nyelvnév. A modell a nyelv NEVÉT érti, nem a
#: kódot — "el" helyett "Greek".
_NYELV_NEV = {
    "hu": "Hungarian", "en": "English", "de": "German", "es": "Spanish",
    "zh": "Chinese", "fr": "French", "pl": "Polish", "ru": "Russian",
    "uk": "Ukrainian", "it": "Italian", "el": "Greek", "tr": "Turkish",
}

#: A szemle modellje. Flash, mert ez rövid, kötött feladat — és a
#: gondolkodás KI, ugyanazon a mért alapon, mint az Economic Briefnél.
REVIEW_MODEL = os.environ.get("AREA_REVIEW_MODEL", "dsflash").strip() or "dsflash"


async def build_area_review(brief: dict, ai_query) -> str:
    """Egy kiadás szöveges makró-szemléje. Sose dob; hibánál ÜRES sztring.

    Az üres sztring itt teljes értékű kimenet: a táblázat ugyanúgy megjelenik,
    csak kommentár nélkül. Egy hibás szemle rosszabb, mint a hiánya — ezért
    minden hibaágon eldobjuk, nem "javítjuk".
    """
    if not ai_query:
        return ""
    tenyek = (brief.get("home") or []) + (brief.get("anchor") or [])
    if len(tenyek) < 4:
        # Négy szám alatt nincs mit összevetni; egy kétsoros "szemle" csak
        # újramondaná a táblázatot.
        return ""
    nyelv = _NYELV_NEV.get(brief.get("lang"), "English")
    try:
        # ⚠️ ALAK-SZERZODES: az `ai_query` elso parametere a MODELL, nem a
        # prompt. Elesben ez "got multiple values for argument 'model'"-t
        # adott mind a 12 kiadasra — es a fail-closed ag miatt szo nelkul
        # ures szemle lett belole. A nevesitett atadas ezt kizarja.
        nyers = await ai_query(
            model=REVIEW_MODEL,
            prompt=_review_prompt(brief, nyelv),
            max_tokens=1200,
            caller="area_briefs",
            no_thinking=True,   # rövid, kötött feladat — nem elmélkedés
            no_tools=True,      # minden adat a promptban van; a tool itt csábítás
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("area_review(%s): a hivas elbukott: %s",
                       brief.get("lang"), e)
        return ""
    szoveg = _valasz_szovege(nyers).strip()
    hibak = validate_review(szoveg, brief)
    if hibak:
        logger.warning("area_review(%s) ELDOBVA: %s | szoveg: %s",
                       brief.get("lang"), "; ".join(hibak), szoveg[:200])
        return ""
    return szoveg


def _valasz_szovege(nyers) -> str:
    """A modell válaszának kibontása — az `ai_query` alakja hívónként eltér."""
    if isinstance(nyers, str):
        try:
            d = json.loads(nyers)
        except (ValueError, TypeError):
            return nyers
    elif isinstance(nyers, dict):
        d = nyers
    else:
        return str(nyers or "")
    for kulcs in ("response", "content", "text", "answer", "result"):
        v = d.get(kulcs)
        if isinstance(v, str) and v.strip():
            return v
    return ""


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


async def cron_entry(get_db, statdata_call, ai_query=None) -> dict:
    """Napi futas: 12 kiadas eloallitasa, szemleje es tarolasa. Sose dob.

    Az `ai_query` OPCIONALIS: nelkule a szamok ugyanugy elkeszulnek, csak a
    szoveges szemle marad el. A tablazat a termek, a szemle a raadas — ha a
    modell nem elerheto, az elsotol nem eshetunk el.
    """
    briefs = await build_area_briefs(statdata_call)
    if ai_query:
        for lang, b in briefs.items():
            b["review"] = await build_area_review(b, ai_query)
    n = store_area_briefs(get_db, briefs)
    teljes = sum(1 for b in briefs.values() if b.get("home"))
    szemles = sum(1 for b in briefs.values() if b.get("review"))
    logger.info("area_briefs: %d kiadas tarolva, ebbol %d-nek van HAZAI blokkja "
                "(a tobbi csak a kozos horgonyt kapja), %d-nek szoveges szemleje",
                n, teljes, szemles)
    return {"stored": n, "with_home": teljes, "with_review": szemles,
            "langs": sorted(briefs), "ok": n > 0}
