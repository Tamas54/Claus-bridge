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


# ══════════════════════════════════════════════════════════════════════════
# NAPI PIACI RÉTEG — ENÉLKÜL A BLOKK NEM NAPI, HANEM HAVI
# ══════════════════════════════════════════════════════════════════════════
#
# Kommandant, 2026-08-31: "ez a Brief lényegében a hét minden napján
# megjelenhet ugyanezzel — ugyanis nem rendelkezik aktuális információval …
# a statok gyorstájékoztatók előző havi adatait taglalja. Másrészt nincs
# nemzetközi része."
#
# Igaza volt, és a kritika PONTOS. A makró-táblázat júliusi CPI-t, Q2 GDP-t
# és Q1 béreket mutat: ugyanez a szöveg augusztus 5. és 25. között bármelyik
# nap megállta volna a helyét. Egy NAPI briefben kell lennie valaminek, ami
# MA más, mint tegnap volt — és a havi statisztikai kiadványokban ilyen
# nincs.
#
# A Telegramra menő Economic Brief azért napi, mert PIACI adatai vannak:
# deviza, tőzsdeindexek, olaj, arany, hozamok, VIX. Ugyanez hiányzott innen.
#
# A VÁLTOZÁS GÉPI. A yfinance `price` és `previous_close` mezőt is ad, tehát
# a napi elmozdulás SZÁMOLHATÓ — nem a modell mondja meg, hanem a két szám.
# Ugyanaz az elv, mint a makró-nyilaknál.

#: A KÖZÖS piaci blokk — mind a 12 kiadásban UGYANAZ. Ez a "nemzetközi rész",
#: ami eddig hiányzott: nem viszonyítási pont a hazai számok mellett, hanem
#: önálló világgazdasági kép.
MARKET_GLOBAL: tuple[tuple[str, str], ...] = (
    ("sp500",     "^GSPC"),
    ("vix",       "^VIX"),
    ("oil_wti",   "CL=F"),
    ("gold",      "GC=F"),
    ("eur_usd",   "EURUSD=X"),
    ("us_10y",    "^TNX"),
)

#: A HAZAI piaci blokk kiadásonként. Mérve 2026-08-31, mind él.
#: ⚠️ Magyarországra a `^BUX` és a `BUX.BD` is HALOTT a Yahoo-n (STALE / not
#: found), ezért a legnagyobb tőzsdei papír áll helyette. Oroszra az
#: `IMOEX.ME` szintén halott — az ru/uk kiadásnak amúgy sincs hazai országa.
MARKET_HOME: dict[str, tuple[tuple[str, str], ...]] = {
    # ⚠️ A BUX NEM A YAHOO-ROL JON. Nincs elo Yahoo-szimboluma (`^BUX` STALE,
    # `^BUXI`/`BUX.BD`/`BUX.BUD` not found), a stooq sem viszi. A StatData
    # `bet_index` toolja adja, a bet.hu beagyazott JSON-blobjabol — lasd az
    # `INDEX_TOOL_HOME` leképezest lentebb.
    # A negy blue chip MELLETTE marad: kulon-kulon tobbet mondanak az
    # olvasonak, mint egy indexszam, es a Telegram-brief is igy csinalja.
    "hu": (("stocks_otp", "OTP.BD"), ("stocks_mol", "MOL.BD"),
           ("stocks_richter", "RICHT.BD"), ("stocks_mtel", "MTEL.BD"),
           ("fx_eur", "EURHUF=X")),
    "de": (("index_dax", "^GDAXI"),),
    "it": (("index_ftsemib", "FTSEMIB.MI"),),
    "fr": (("index_cac", "^FCHI"),),
    "es": (("index_ibex", "^IBEX"),),
    "pl": (("index_wig20", "WIG20.WA"), ("fx_eur", "EURPLN=X")),
    "el": (("index_athex", "GD.AT"),),
    "tr": (("index_bist", "XU100.IS"), ("fx_usd", "USDTRY=X")),
    "zh": (("index_sse", "000001.SS"), ("fx_usd", "USDCNY=X")),
    "en": (("index_ftse", "^FTSE"), ("fx_usd", "GBPUSD=X")),
    "ru": (),
    "uk": (),
}

#: Kiadások, ahol a hazai INDEX nem a yfinance-ről jön, hanem sajat
#: StatData-toolbol. Ma egy ilyen van; a szerkezet viszont keszen all, ha
#: mas tozsdenel is kiderul, hogy a Yahoo nem viszi.
INDEX_TOOL_HOME: dict[str, tuple[str, str, dict]] = {
    "hu": ("index_bux", "bet_index", {"index": "BUX"}),
}


#: A piaci jegyzések memória-cache-e. A tőzsde percenként mozog, de egy napi
#: briefhez a tíz perces frissesség bőven elég — és megvéd attól, hogy tizenkét
#: kiadás tizenkétszer kérje le ugyanazt a hat globális tickert.
_MARKET_CACHE: dict[str, tuple[float, dict]] = {}
_MARKET_TTL = 600.0


async def fetch_quote(statdata_call, symbol: str) -> dict | None:
    """Egy jegyzés + a NAPI VÁLTOZÁS. Sose dob; hibánál None.

    A változás a `price` és a `previous_close` hányadosa — gépi szám, nem
    modell-állítás. Ha bármelyik hiányzik, a `change_pct` None marad, és a
    megjelenítés nem rajzol irányt: a "nem tudom" itt is teljes értékű.
    """
    import time as _t
    hit = _MARKET_CACHE.get(symbol)
    if hit and (_t.time() - hit[0]) < _MARKET_TTL:
        return hit[1]
    try:
        res = await statdata_call("yfinance", {"symbol": symbol, "action": "quote"})
        if isinstance(res, str):
            res = json.loads(res)
    except Exception as e:  # noqa: BLE001
        logger.warning("piaci jegyzes (%s) elbukott: %s", symbol, e)
        return None
    if not isinstance(res, dict) or res.get("error") or res.get("price") is None:
        logger.info("piaci jegyzes (%s) ures: %s", symbol,
                    str((res or {}).get("error"))[:90])
        return None
    ar, elozo = res.get("price"), res.get("previous_close")
    valtozas = None
    try:
        if elozo:
            valtozas = round((float(ar) / float(elozo) - 1.0) * 100.0, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        valtozas = None
    ki = {"symbol": symbol, "name": res.get("name") or symbol,
          "price": ar, "prev_close": elozo, "change_pct": valtozas,
          "currency": res.get("currency"),
          "as_of": res.get("last_trade_at")}
    _MARKET_CACHE[symbol] = (_t.time(), ki)
    return ki


async def fetch_market(statdata_call, lang: str) -> dict:
    """A kiadás piaci blokkja: KÖZÖS globális + hazai. Sose dob."""
    ki: dict = {"global": {}, "home": {}}
    for cimke, sym in MARKET_GLOBAL:
        q = await fetch_quote(statdata_call, sym)
        if q:
            ki["global"][cimke] = q
    # A sajat toolbol jovo index ELOL all: az a nyelvterulet fo mutatoja.
    tool = INDEX_TOOL_HOME.get(lang)
    if tool:
        cimke, tool_nev, args = tool
        q = await fetch_index_tool(statdata_call, tool_nev, args)
        if q:
            ki["home"][cimke] = q
    for cimke, sym in MARKET_HOME.get(lang, ()):
        q = await fetch_quote(statdata_call, sym)
        if q:
            ki["home"][cimke] = q
    return ki


async def fetch_index_tool(statdata_call, tool_nev: str, args: dict) -> dict | None:
    """Tozsdeindex sajat StatData-toolbol (ma: `bet_index` a BUX-hoz).

    A visszaadott alak AZONOS a `fetch_quote`-eval, hogy a renderelo es a
    szemle ne tudja megkulonboztetni, honnan jott — kulonben minden fogyasztot
    ket agra kellene bontani.

    ⚠️ A `market_cap_bnft` mezot SZANDEKOSAN nem vesszuk at. A bet.hu lapjan
    az a baziskapitalizacio (14.639.314.708 Ft), es az elso probam pont azt
    szedte fel indexertekkent. Ami nem kell, azt ne is hozzuk magunkkal.
    """
    import time as _t
    kulcs = f"{tool_nev}:{json.dumps(args, sort_keys=True)}"
    hit = _MARKET_CACHE.get(kulcs)
    if hit and (_t.time() - hit[0]) < _MARKET_TTL:
        return hit[1]
    try:
        res = await statdata_call(tool_nev, args)
        if isinstance(res, str):
            res = json.loads(res)
    except Exception as e:  # noqa: BLE001
        logger.warning("index-tool (%s) elbukott: %s", tool_nev, e)
        return None
    if not isinstance(res, dict) or res.get("error") or res.get("value") is None:
        logger.info("index-tool (%s) ures: %s", tool_nev,
                    str((res or {}).get("error"))[:120])
        return None
    ki = {"symbol": res.get("index") or tool_nev,
          "name": res.get("index") or tool_nev,
          "price": res.get("value"),
          "prev_close": res.get("prev_close"),
          "change_pct": res.get("change_pct"),
          "currency": "pont",
          "as_of": None,
          "source": res.get("source")}
    _MARKET_CACHE[kulcs] = (_t.time(), ki)
    return ki


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


async def build_area_briefs(statdata_call, piaccal: bool = True) -> dict:
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
    if piaccal:
        # A PIACI BLOKK a makró UTÁN jön, külön ágon: ha a tőzsdei lekérés
        # bukik, a makró-táblázat akkor is teljes. Fordítva nem igaz —
        # ezért nem közös try-ág.
        for lang in out:
            try:
                out[lang]["market"] = await fetch_market(statdata_call, lang)
            except Exception as e:  # noqa: BLE001
                logger.warning("piaci blokk (%s) elbukott: %s", lang, e)
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

#: Legfeljebb ennyi mondat.
#:
#: Kommandant, 2026-08-31: "amit el tudott nekem küldeni a bridge szöveget
#: miért a tizede van ott?" — igaza volt. Az öt mondat egy chat-buborékra
#: volt szabva, a lap viszont OLVASÓFELÜLET: van hely egy rendes szemlére,
#: és a Telegramra menő Economic Brief makró-része is 6-10 mondat.
#:
#: A korlát értelme nem a rövidség, hanem az, hogy a szemle NE csússzon át
#: értelmezésbe, amihez nincs adatunk. Ezt viszont a tartalmi szabályok
#: (irány csak `prev`-vel, ok SOHA, kitalált szám tilos) tartják meg, nem a
#: mondatszám. Tizenkét mondatban is lehet fegyelmezett — és tizenkét
#: mutatóhoz öt mondat kevés: a felét meg sem említi.
REVIEW_MAX_MONDAT = 12

#: Hány karakteren felül vágjuk el biztonságból (a modell néha "elszabadul").
REVIEW_MAX_KAR = 4200   # ket bekezdes (vilag + hazai), 12 mondat gorogul is elfer


# ══════════════════════════════════════════════════════════════════════════
# AMI NEM VÁLTOZOTT, AZT NE ÍRJUK ÚJRA
# ══════════════════════════════════════════════════════════════════════════
#
# Kommandant, 2026-08-31: "ami állandó, azt meg se kell csinálni többször,
# addig amíg új adat nem jön (mondjuk a jegybanki alapkamat változása) — ami
# érdekes, az a VÁLTOZÁS, és az azonnali hír."
#
# Két dolgot old meg egyszerre:
#
# 1. KÖLTSÉG. Egy havi statisztika a hónap huszonöt napján ugyanaz. Tizenkét
#    kiadás × naponta kétszer újraíratni róla ugyanazt a szöveget tiszta
#    pazarlás — és a kimenet MÉG INGADOZIK is, mert a modell nem
#    determinisztikus: ugyanabból az adatból naponta más megfogalmazás jön,
#    ami az olvasónak VÁLTOZÁSNAK látszik, holott nem az.
#
# 2. ÚJSÁGÍRÁS. Ami hír, az a változás. Ha az MNB kamatot vág, annak a szemle
#    ELEJÉN a helye — nem elveszve a tizenkét mutató között.
#
# Ezért a makró-tények ujjlenyomatot kapnak. Változatlan ujjlenyomatnál a
# tárolt szemle marad (nulla token); ha változott, az ÚJ prompt megkapja,
# PONTOSAN MI változott, és azzal kell kezdenie.

def makro_ujjlenyomat(brief: dict) -> str:
    """A kiadás makró-tényeinek lenyomata: mutató → érték + időszak.

    A piaci adat SZÁNDÉKOSAN nincs benne: a tőzsde minden nap mozog, tehát
    ha beleszámítana, sose lenne „változatlan" — és pont az elv veszne el.
    A piaci mozgás külön kaput kap (`_piac_mozdult`).
    """
    import hashlib
    tetelek = sorted(
        (r.get("country", ""), r.get("indicator", ""), r.get("value"),
         r.get("period"))
        for r in (brief.get("home") or []) + (brief.get("anchor") or []))
    return hashlib.sha256(
        json.dumps(tetelek, sort_keys=True, default=str).encode()).hexdigest()[:16]


def valtozasok(uj: dict, regi: dict | None) -> list[dict]:
    """MI VÁLTOZOTT az előző kiadás óta — gépi összevetés, nulla token.

    Új mutató (korábban hiányzott) is változás: az olvasónak az is hír, hogy
    egy adat végre megjött.
    """
    if not regi:
        return []
    def _map(b):
        return {(r.get("country", ""), r.get("indicator", "")): r
                for r in (b.get("home") or []) + (b.get("anchor") or [])}
    r_map, u_map = _map(regi), _map(uj)
    ki = []
    for kulcs, r in u_map.items():
        elozo = r_map.get(kulcs)
        if elozo is None:
            ki.append({"country": kulcs[0], "indicator": kulcs[1],
                       "new_value": r.get("value"), "new_period": r.get("period"),
                       "kind": "uj"})
            continue
        if (elozo.get("value") != r.get("value")
                or elozo.get("period") != r.get("period")):
            ki.append({"country": kulcs[0], "indicator": kulcs[1],
                       "old_value": elozo.get("value"),
                       "old_period": elozo.get("period"),
                       "new_value": r.get("value"),
                       "new_period": r.get("period"),
                       "kind": "valtozott"})
    return ki


#: Ekkora napi piaci elmozdulás fölött akkor is új szemle kell, ha a makró
#: változatlan. A küszöbök a szokásos napi szórás fölött vannak: egy 0,3%-os
#: indexmozgás nem hír.
_PIAC_KUSZOB = {"sp500": 1.0, "vix": 10.0, "oil_wti": 2.0, "gold": 1.5,
                "eur_usd": 0.8, "us_10y": 3.0}


def _piac_mozdult(brief: dict) -> list[str]:
    """Volt-e ma ÉRDEMI piaci mozgás? Visszaadja, melyik eszközön."""
    m = (brief.get("market") or {}).get("global") or {}
    ki = []
    for kulcs, kuszob in _PIAC_KUSZOB.items():
        v = (m.get(kulcs) or {}).get("change_pct")
        if isinstance(v, (int, float)) and abs(v) >= kuszob:
            ki.append(f"{kulcs} {v:+.2f}%")
    return ki


def _valtozas_blokk(valt: list[dict], piac: list[str]) -> str:
    if not valt and not piac:
        return "(no macro figure changed since the previous edition)"
    sorok = []
    for v in valt:
        if v["kind"] == "uj":
            sorok.append(f"NEW DATA {v['country']} {v['indicator']}: "
                         f"{v['new_value']} @{v['new_period']} (was missing)")
        else:
            sorok.append(f"CHANGED {v['country']} {v['indicator']}: "
                         f"{v['old_value']} @{v['old_period']} → "
                         f"{v['new_value']} @{v['new_period']}")
    for x in piac:
        sorok.append(f"LARGE MARKET MOVE {x}")
    return "\n".join(sorok)


def _piac_tenyek(brief: dict) -> str:
    """A NAPI piaci tények a szemle promptjához.

    Enélkül a szemle havi statisztikákból dolgozik, és — ahogy a Kommandant
    kimondta — "augusztus 5. és 25. között bármelyik nap" megírható lenne.
    """
    m = brief.get("market") or {}
    sorok = []
    for cimke, tetelek in (("VILAGPIAC", m.get("global") or {}),
                           ("HAZAI PIAC", m.get("home") or {})):
        for kulcs, q in tetelek.items():
            v = q.get("change_pct")
            valt = f"{v:+.2f}% ma" if isinstance(v, (int, float)) else "napi valtozas: NINCS"
            sorok.append(f"{cimke} {kulcs}: {q.get('price')} "
                         f"({q.get('currency') or ''}) | {valt}")
    return "\n".join(sorok)


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


def _szam_jeloltek(szoveg: str) -> list[tuple[str, set]]:
    """Szám-TOKENENKÉNT az összes érvényes olvasat.

    ⚠️ MIÉRT TOKENENKÉNT, ÉS NEM EGY LAPOS HALMAZBAN. A „2.467" (amerikai
    maginfláció) KÉTÉRTELMŰ: angolul 2,467 — németül 2467. Mindkét olvasat
    érvényes alak, tehát mindkettőt elő kell állítani. Ha viszont laposan
    ellenőrizzük őket, a 2467 külön számként bukik el, és a validátor eldobja
    a HELYES szemlét. Élesben ez 12-ből 9-et vitt el.

    A helyes szabály: egy TOKEN akkor rendben, ha BÁRMELYIK olvasata ismerős.
    """
    import re
    # Az ezres SZÓKÖZT előbb tüntetjük el — de csak ha tényleg tagoló: három
    # számjegy áll utána, és nem több. Enélkül a minta a szóközön keresztül
    # összeolvasna két szomszédos számot („S&P 500 7683,24").
    tiszta = re.sub(r"(?<=\d)[ \u00a0](?=\d{3}(?!\d))", "", szoveg or "")
    ki = []
    for tok in re.findall(r"-?\d+(?:[.,]\d+)+", tiszta):
        def _olvasat(tizedes: str, ezres: str) -> str | None:
            if tok.count(tizedes) > 1:
                return None                    # ket tizedesjel: ertelmetlen
            if tizedes in tok:
                egesz, _, tort = tok.rpartition(tizedes)
            else:
                egesz, tort = tok, ""
            if ezres in tort:
                return None                    # tagolo a tizedes reszben
            reszek = egesz.split(ezres)
            if len(reszek) > 1 and any(len(r) != 3 for r in reszek[1:]):
                return None                    # az ezres csoport 3 jegyu
            mag = egesz.replace(ezres, "")
            return f"{mag}.{tort}" if tort else mag

        jeloltek = set()
        for j in (_olvasat(",", "."), _olvasat(".", ",")):
            if not j:
                continue
            try:
                jeloltek.add(round(float(j), 2))
            except ValueError:
                continue
        if jeloltek:
            ki.append((tok, jeloltek))
    return ki


def _szamok(szoveg: str) -> set:
    """Minden olvasat laposan — csak diagnosztikara es teszthez."""
    ki = set()
    for _, jeloltek in _szam_jeloltek(szoveg):
        ki |= jeloltek
    return ki


def _ismert_szamok(brief: dict) -> set:
    """Minden szám, amit a szemle JOGGAL leírhat.

    Három forrásból: a cellák értékei, az ELŐZŐ megfigyelések, és a köztük
    számolt KÜLÖNBSÉGEK (a „2,1 ponttal magasabb" típusú mondatokhoz).

    ⚠️ Mindegyik EGY TIZEDESRE kerekítve is bekerül, mert a modell az
    OLVASÓNAK kerekít: a török infláció a panelben 31,754, a szemlében 31,8.
    Az első változatom emiatt dobta el a török, a görög és a kínai szemlét —
    a mérőeszköz volt szigorúbb a valóságnál, nem a modell pontatlanabb.
    """
    ertekek = []
    m = brief.get("market") or {}
    for tetelek in ((m.get("global") or {}), (m.get("home") or {})):
        for q in tetelek.values():
            for k in ("price", "prev_close", "change_pct"):
                if q.get(k) is not None:
                    try:
                        ertekek.append(float(q[k]))
                    except (TypeError, ValueError):
                        pass
    for r in (brief.get("home") or []) + (brief.get("anchor") or []):
        for k in ("value", "prev_value"):
            if r.get(k) is not None:
                try:
                    ertekek.append(float(r[k]))
                except (TypeError, ValueError):
                    pass
    ismert = set()

    def _felvesz(x: float) -> None:
        # ⚠️ ABSZOLÚT ÉRTÉKKEL IS. Egy negatív rátát a próza pozitív
        # nagyságként ír le, és az előjelet az IGE hordozza: „房价下跌6.3%",
        # „az ipari termelés 0,5 százalékkal csökkent". Előjel-szigorúan a
        # kínai szemle bukott el, pedig helyes volt.
        for y in (x, abs(x)):
            ismert.add(round(y, 2))
            ismert.add(round(y, 1))

    for a in ertekek:
        _felvesz(a)
        for b in ertekek:
            _felvesz(abs(a - b))
    return ismert


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
    ismert = _ismert_szamok(brief)
    idegen = []
    for tok, jeloltek in _szam_jeloltek(szoveg):
        # EGY TOKEN akkor rendben, ha BARMELYIK olvasata ismeros.
        if any(j in ismert or round(j, 1) in ismert for j in jeloltek):
            continue
        idegen.append(tok)
    if idegen:
        hibak.append(f"a bemenetben nem szereplo szam(ok): {idegen[:6]}")
    # ── CJK-szivargas nem-kinai kiadasban ──────────────────────────────
    if brief.get("lang") != "zh" and any("一" <= ch <= "鿿" for ch in szoveg):
        hibak.append("CJK karakter nem-kinai kiadasban")
    return hibak


def _review_prompt(brief: dict, nyelv_nev: str, valtozas: str = "") -> str:
    orszag = brief.get("country") or "-"
    valtozas = valtozas or "(not computed)"
    piac = _piac_tenyek(brief)
    return f"""MONTHLY / QUARTERLY STATISTICS (the ONLY macro numbers you may use):
{_tenyblokk(brief)}

TODAY'S MARKETS (these are the numbers that changed since yesterday):
{piac or "(no market data available today)"}

WHAT IS NEW SINCE THE PREVIOUS EDITION:
{valtozas}

TASK: Write a macro commentary in {nyelv_nev} for readers in {orszag},
in TWO paragraphs separated by a blank line:

  PARAGRAPH 1 — THE WORLD. What moved on global markets today, and what the
  euro-area and US figures say. This paragraph is about the world economy in
  its own right, NOT a set of yardsticks for the home country.

  PARAGRAPH 2 — HOME. The home economy: its own statistics and its own
  market, and where it stands against the anchor.

⚠️ LEAD WITH WHAT IS NEW. If the "WHAT IS NEW" block names a changed figure
— a rate decision, a fresh inflation print — that is the news, and it belongs
in the FIRST sentence of the paragraph it concerns. A reader who saw
yesterday's commentary must be able to tell in one sentence what is different.
If nothing changed, say so plainly in one clause and move on; do not
manufacture novelty out of a figure that stood still.

⚠️ THE FIRST PARAGRAPH MUST CARRY TODAY. Monthly statistics (a July CPI, a
Q2 GDP) are the same on every day of the month — a commentary built only on
them could be published any day between the 5th and the 25th, and would tell
the reader nothing about today. The market numbers ARE today's: lead with
what actually moved.

Of {REVIEW_MAX_MONDAT} sentences at most — but WRITE A FULL COMMENTARY, not a
summary: the reader has the table right below, so repeating three numbers adds
nothing. Cover the indicators that actually say something, and where several
move together, say what they say together. Plain prose, no headings, no bullet
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
6. MARKET MOVES: the daily change is given for each quote. Use it as given;
   never infer a move from a price alone, and never explain WHY a market
   moved — you have no news here, only numbers.
7. A move under about 0.3% on an index or 0.5% on a commodity is noise. Say
   the market was quiet rather than dressing up a rounding difference as an
   event.

WHAT IS WORTH SAYING in the HOME paragraph, roughly in this order:
  - prices: headline vs core, and where the two diverge;
  - the policy rate against inflation (is policy tight or loose in real
    terms?) and against the euro-area/US anchor;
  - the real economy: growth, unemployment, industry, retail — and whether
    they point the same way or contradict each other;
  - incomes: wages against inflation is the real-wage direction, and that is
    what a reader feels;
  - anything that stands out sharply against the anchor, in either direction;
  - where the same quantity has two measures (harmonised vs national core),
    say plainly that they are DIFFERENT MEASURES, not that one is wrong.
Skip an indicator rather than padding a sentence about it. Write for an
ordinary newspaper reader, not an economist.

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


async def build_area_review(brief: dict, ai_query, valtozas: str = "") -> str:
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
            prompt=_review_prompt(brief, nyelv, valtozas),
            max_tokens=1200,
            # ⚠️ A `caller` EGYBEN IDENTITAS is: a permissions-kapu ebbol
            # dont. Az "area_briefs" nev nem volt regisztralva, ezert a
            # kapu mind a 12 hivast megtagadta ("Unbekannter Soldat").
            # A javitas NEM uj core-instance mintazasa — ez ugyanolyan
            # utemezett Bridge-belso generator, mint az Economic Brief,
            # tehat ugyanazt az identitast hasznalja. Egy uj korlatlan
            # identitas egyetlen tool kedveert rossz csere lenne.
            caller="feldwebel",
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
    """A modell válaszának kibontása — az `ai_query` alakja hívónként eltér.

    ⚠️ A HIBÁT KI KELL MONDANI. Az `ai_query` a hozzáférés-megtagadást
    NORMÁL VÁLASZKÉNT adja vissza (`{"error": "ZUGANG VERWEIGERT…",
    "status": "denied"}`), nem kivételként. Az első változatom erre üres
    sztringet adott, a fail-closed ág pedig szó nélkül elhagyta a szemlét —
    tizenkét kiadás maradt kommentár nélkül, és a log annyit mondott, hogy
    "ures". A hibaüzenet ELVESZETT a hibatűrésben.
    """
    if isinstance(nyers, str):
        try:
            d = json.loads(nyers)
        except (ValueError, TypeError):
            return nyers
    elif isinstance(nyers, dict):
        d = nyers
    else:
        return str(nyers or "")
    if d.get("error") or d.get("status") == "denied":
        logger.error("area_review: a modellhivas ELUTASITVA — %s",
                     str(d.get("error") or d)[:200])
        return ""
    for kulcs in ("response", "content", "text", "answer", "result"):
        v = d.get(kulcs)
        if isinstance(v, str) and v.strip():
            return v
    logger.warning("area_review: ismeretlen valasz-alak, kulcsok: %s",
                   sorted(d)[:8])
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
            # ── AMI NEM VÁLTOZOTT, AZT NE ÍRJUK ÚJRA ──────────────────
            regi = load_area_brief(get_db, lang)
            b["fingerprint"] = makro_ujjlenyomat(b)
            valt = valtozasok(b, regi)
            piac = _piac_mozdult(b)
            b["changes"] = valt
            valtozatlan = (regi is not None
                           and regi.get("fingerprint") == b["fingerprint"]
                           and not piac and (regi.get("review") or ""))
            if valtozatlan:
                # A tárolt szemle marad. NULLA token — és stabilabb is: a
                # modell nem determinisztikus, tehát ugyanabból az adatból
                # naponta MÁS megfogalmazást adna, ami az olvasónak
                # változásnak látszana, holott nem az.
                b["review"] = regi["review"]
                b["review_reused"] = True
                logger.info("area_review(%s): valtozatlan makro es csendes "
                            "piac — a tarolt szemle marad", lang)
                continue
            b["review"] = await build_area_review(
                b, ai_query, _valtozas_blokk(valt, piac))
    n = store_area_briefs(get_db, briefs)
    teljes = sum(1 for b in briefs.values() if b.get("home"))
    szemles = sum(1 for b in briefs.values() if b.get("review"))
    ujrahasznalt = sum(1 for b in briefs.values() if b.get("review_reused"))
    logger.info("area_briefs: %d kiadas tarolva, ebbol %d-nek van HAZAI blokkja "
                "(a tobbi csak a kozos horgonyt kapja), %d-nek szoveges szemleje",
                n, teljes, szemles)
    return {"stored": n, "with_home": teljes, "with_review": szemles,
            "review_reused": ujrahasznalt,
            "langs": sorted(briefs), "ok": n > 0}
