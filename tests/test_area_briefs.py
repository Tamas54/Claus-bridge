"""NYELVTERULETI MAKRO-BRIEFEK — 12 kiadas, mindegyik a SAJATJAVAL.

Kommandant, 2026-08-31: "a magyarra vonatkozo politikai es gazdasagi adatok
nem fognak egy torokot erdekelni — vagy epp egy olaszt. Ot a sajatja
erdekelne." Es: "a globalis kozos, de a nyelvteruleti resz kulonbozo."

Amit ezek a tesztek orzik:
  1. minden kiadas a SAJAT orszagat kapja, nem a magyart leforditva,
  2. az ELAVULT es a NEM-RATA cella KIMARAD (nem "majdnem jo" adat megy ki),
  3. a HIANYT kimondjuk, nem hallgatjuk el,
  4. a kozos horgony MINDEN kiadasban ugyanaz.
"""

import asyncio
import pytest
import json

import pytest

from plugins import area_briefs as ab


@pytest.fixture(autouse=True)
def _tiszta_cache():
    """⚠️ A PIACI CACHE MODUL-SZINTU, tehat ATSZIVAROG a tesztek kozott: egy
    korabbi teszt lekerese elfedi a kesobbiet, es a teszt "zolden" fut ugy,
    hogy egyetlen hivast sem tett. Ez elesben is hibaosztaly — a cache
    elettartama ott a TTL, itt viszont a teljes futas."""
    ab._MARKET_CACHE.clear()
    yield
    ab._MARKET_CACHE.clear()



def _panel(rows):
    async def _call(tool, args):
        assert tool == "get_macro_panel"
        return {"rows": rows, "summary": {"cells": len(rows)}}
    return _call


def _cell(cc, ind, val, status="fresh", kind="rate", period="2026-07"):
    return {"country": cc, "indicator": ind, "value": val, "period": period,
            "status": status, "unit_kind": kind, "unit": "annual % change",
            "source_used": "teszt"}


def test_minden_kiadas_a_SAJAT_orszagat_kapja():
    rows = [_cell("HU", "cpi", 1.6), _cell("IT", "cpi", 2.9),
            _cell("TR", "cpi", 31.8), _cell("EA", "cpi", 2.9),
            _cell("US", "cpi", 3.3)]
    out = asyncio.run(ab.build_area_briefs(_panel(rows)))
    assert out["hu"]["home"][0]["value"] == 1.6
    assert out["it"]["home"][0]["value"] == 2.9
    assert out["tr"]["home"][0]["value"] == 31.8
    # SZABOTAZS: a magyar szam NEM szivaroghat at az olasz kiadasba
    for lang in ("it", "tr"):
        assert all(r["country"] == ab.AREA_COUNTRY[lang] for r in out[lang]["home"])


def test_az_elavult_cella_KIMARAD():
    """Egy 8 honapos nemzeti maginflacio rosszabb, mint a hianya: az olvaso
    nem tudja, hogy regi. (Merve: az ECB nemzeti core-sorozatai 2025-12-nel
    allnak.)"""
    rows = [_cell("HU", "cpi", 1.6),
            _cell("HU", "core_cpi", 5.08, status="stale", period="2025-12")]
    out = asyncio.run(ab.build_area_briefs(_panel(rows)))
    mutatok = {r["indicator"] for r in out["hu"]["home"]}
    assert "cpi" in mutatok
    assert "core_cpi" not in mutatok, "elavult cella ment ki friss cimke alatt"
    assert "core_cpi" in out["hu"]["gaps"], "a hianyt elhallgattuk"


def test_a_SZINT_nem_megy_ki_ratakent():
    """A `gdp` millio euroban, a `wages` US-re USD/oraban — ugyanugy szam,
    de az olvaso szazalekkent olvasna."""
    rows = [_cell("HU", "cpi", 1.6),
            _cell("HU", "wages", 49343.0, kind="level")]
    out = asyncio.run(ab.build_area_briefs(_panel(rows)))
    assert {r["indicator"] for r in out["hu"]["home"]} == {"cpi"}


def test_a_kozos_horgony_MINDEN_kiadasban_ugyanaz():
    rows = [_cell("HU", "cpi", 1.6), _cell("IT", "cpi", 2.9),
            _cell("EA", "cpi", 2.9), _cell("US", "cpi", 3.3)]
    out = asyncio.run(ab.build_area_briefs(_panel(rows)))
    hu = [(r["country"], r["value"]) for r in out["hu"]["anchor"]]
    it = [(r["country"], r["value"]) for r in out["it"]["anchor"]]
    assert hu == it == [("EA", 2.9), ("US", 3.3)]


def test_hazai_forras_nelkuli_kiadas_CSAK_a_horgonyt_kapja():
    """`ru`/`uk`: merve nincs megbizhato hazai forras. Ilyenkor a kiadas a
    kozos reszt kapja — es NEM egy talalati listabol szedett szamot."""
    rows = [_cell("EA", "cpi", 2.9), _cell("US", "cpi", 3.3)]
    out = asyncio.run(ab.build_area_briefs(_panel(rows)))
    assert out["ru"]["home"] == [] and out["uk"]["home"] == []
    assert out["ru"]["anchor"], "a horgony is elveszett"
    assert ab.AREA_COUNTRY["ru"] == "" and ab.AREA_COUNTRY["uk"] == ""


def test_a_panel_bukasa_nem_visz_el_mindent():
    async def _boom(tool, args):
        raise RuntimeError("StatData halott")
    assert asyncio.run(ab.build_area_briefs(_boom)) == {}



def test_mind_a_12_nyelv_szerepel():
    """Az Echolot 12 nyelvu. Ha egy kiadas kimarad a terkepbol, az a nyelv
    NEMAN makro-blokk nelkul marad — es senki nem venne eszre."""
    assert set(ab.AREA_COUNTRY) == {
        "hu", "en", "de", "es", "zh", "fr", "pl", "ru", "uk", "it", "el", "tr"}


def test_a_kotegeles_nem_lepi_tul_a_panel_plafonjat():
    """ELESBEN MEGTORTENT (2026-08-31): 12 orszag × 11 mutato = 132 cella, a
    panel plafonja 120 — a hivas ELUTASITVA, nulla brief keszult.

    A plafon NEM onkenyes: minden cella kulon kulso lekeres, a forrasoknal
    rate limit van. Tehat nem a plafont emeljuk, hanem kotegelunk."""
    hivasok = []

    async def _call(tool, args):
        n = len(args["countries"].split(",")) * len(args["indicators"].split(","))
        hivasok.append(n)
        assert n <= 120, f"{n} cella egy kotegben — a panel elutasitana"
        return {"rows": [_cell(c, "cpi", 1.0)
                         for c in args["countries"].split(",")]}

    out = asyncio.run(ab.build_area_briefs(_call))
    assert hivasok, "egyetlen hivas sem tortent"
    assert out, "a kotegelt eredmeny ures"
    # es minden orszag benne van valamelyik kotegben
    assert out["hu"]["home"] and out["tr"]["home"]


def test_egy_bukott_koteg_nem_viszi_el_a_tobbit():
    """A halozat egy kotegnel elszallhat. A tobbi kiadas ettol meg keszuljon
    el — a resz-eredmeny jobb, mint a semmi."""
    hivas = {"n": 0}

    async def _call(tool, args):
        hivas["n"] += 1
        if hivas["n"] == 1:
            raise RuntimeError("halozat")
        return {"rows": [_cell(c, "cpi", 2.0) for c in args["countries"].split(",")]}

    out = asyncio.run(ab.build_area_briefs(_call))
    assert out, "egy bukott koteg elvitte az egeszet"


# ══════════════════════════════════════════════════════════════════════════
# NEMZETI vs HARMONIZÁLT — a két szám sose keveredhet
# ══════════════════════════════════════════════════════════════════════════
#
# Kommandant, 2026-08-31: "tegyük hozzá a magyarokhoz a szar saját KSH-s
# adatokat is. Tehát harmonizáltan dolgozzunk."
#
# HU maginfláció 2026-07: harmonizált 3,7% · nemzeti 1,9%. Kétszeres eltérés,
# és MINDKETTŐ helyes. Ez a legveszélyesebb adatpár a rendszerben: két igaz
# szám, ugyanarra a névre.





def _nemzeti_cell(cc, ind, val):
    """A nemzeti mutato cellaja — SAJAT egysegcimkevel, mint elesben."""
    c = _cell(cc, ind, val)
    c["unit"] = "annual % change (YoY), NATIONAL definition — NOT comparable"
    return c


def test_nemzeti_core_a_harmonizalt_MOGE_kerul():
    """A sorrend nem kozmetika: egymás mellett a két szám összehasonlítás, a
    táblázat két távoli pontján viszont ellentmondás."""
    rows = [_cell("HU", "cpi", 1.6), _cell("HU", "core_cpi", 3.7),
            _nemzeti_cell("HU", "core_cpi_national", 1.9), _cell("HU", "policy_rate", 5.5)]
    b = asyncio.run(ab.build_area_briefs(_panel(rows)))
    nevek = [r["indicator"] for r in b["hu"]["home"]]
    assert nevek.index("core_cpi_national") == nevek.index("core_cpi") + 1


def test_a_nemzeti_hianya_NEM_hiany():
    """Nemzeti maginfláció csak ott van, ahol a hivatal külön közli. Ha ezt
    hiányként jelentenénk, kilenc kiadás kapna egy SOHA nem javítható
    hiánysort — és a hiánylista pont attól hasznos, hogy javítható."""
    rows = [_cell("DE", i, 2.0) for i in ab.HOME_INDICATORS]
    b = asyncio.run(ab.build_area_briefs(_panel(rows)))
    assert b["de"]["gaps"] == [], "a nemzeti mutató hiánya hiánynak számított"
    assert "core_cpi_national" not in b["de"]["gaps"]


def test_a_nemzeti_nem_helyettesiti_a_harmonizaltat():
    """A LEGROSSZABB kimenet: hiányzó harmonizált mellett a nemzeti szám
    csendben átveszi a helyét, és a 12 kiadás összehasonlíthatatlan lesz —
    anélkül, hogy bárki észrevenné."""
    rows = [_cell("HU", "cpi", 1.6), _nemzeti_cell("HU", "core_cpi_national", 1.9)]
    b = asyncio.run(ab.build_area_briefs(_panel(rows)))
    nevek = [r["indicator"] for r in b["hu"]["home"]]
    assert "core_cpi_national" in nevek
    assert "core_cpi" not in nevek
    assert "core_cpi" in b["hu"]["gaps"], \
        "a nemzeti szám elfedte a harmonizált hiányát"


def test_a_ketto_kulon_nevvel_es_kulon_egyseggel_megy_ki():
    """Az Echolot 12 nyelven rendereli. Ha a két sor neve vagy egysége
    azonos, a fordító `t()` UGYANAZT a címkét adja mindkettőre — és az
    olvasó két egymásnak ellentmondó 'maginflációt' lát."""
    rows = [_cell("HU", "core_cpi", 3.7),
            _nemzeti_cell("HU", "core_cpi_national", 1.9)]
    b = asyncio.run(ab.build_area_briefs(_panel(rows)))
    h = {r["indicator"]: r for r in b["hu"]["home"]}
    assert h["core_cpi"]["value"] != h["core_cpi_national"]["value"]
    assert h["core_cpi"]["unit"] != h["core_cpi_national"]["unit"], \
        "a két mutató azonos egységcímkét visz — az olvasó nem tudja szétválasztani"


def test_a_webkeresesbol_regexelt_szam_NEM_kerul_kiadasba():
    """MÉRT ESET (2026-08-31): a StatData a kínai munkanélküliségre 12,5%-ot
    adott — az az IFJÚSÁGI ráta, amit egy regex szedett fel egy találati lista
    első százalékából; az országos ~5%. A cella `status='fresh'` és
    `unit_kind='rate'` volt, tehát MINDKÉT korábbi szűrőn átment. Egy rossz
    szám, ami hivatalosnak látszik, rosszabb, mint egy üres sor."""
    rows = [dict(_cell("CN", "unemployment", 12.5), confidence="web_unverified"),
            dict(_cell("CN", "cpi", 0.5), confidence="official")]
    out = asyncio.run(ab.build_area_briefs(_panel(rows)))
    nevek = [r["indicator"] for r in out["zh"]["home"]]
    assert "unemployment" not in nevek
    assert "cpi" in nevek
    assert "unemployment" in out["zh"]["gaps"], \
        "a kiszűrt cella eltűnt a hiánylistából is — a hiány láthatatlan lett"


def test_a_confidence_hianya_HIVATALOSNAK_szamit():
    """Visszafelé kompatibilitás: a régi panel-válaszokban nincs `confidence`.
    Ha a hiányát bizonytalanságnak vennénk, egyetlen deploy-eltolódás
    KIÜRÍTENÉ mind a 12 kiadást — némán."""
    rows = [_cell("HU", "cpi", 1.6)]
    assert rows[0].get("confidence") is None
    out = asyncio.run(ab.build_area_briefs(_panel(rows)))
    assert out["hu"]["home"] and out["hu"]["home"][0]["value"] == 1.6


# ══════════════════════════════════════════════════════════════════════════
# SZÖVEGES MAKRÓ-SZEMLE — a táblázat megmondja MENNYI, ez hogy MIT JELENT
# ══════════════════════════════════════════════════════════════════════════
#
# Kommandant, 2026-08-31: "és a gazdasági elemzés? Nem kerül alá?" —
# "mármint a szöveges?"
#
# Ez a blokk ÍTÉLET, tehát el lehet rontani. A validátor fail-closed: bármi
# gyanú esetén a szemle eldobódik, és marad a puszta táblázat.


def _b(rows, lang="hu", anchor=None):
    return {"lang": lang, "country": "HU", "home": rows, "anchor": anchor or []}


def test_a_kitalalt_szam_ELDOBJA_a_szemlet():
    """MÉRT HIBAOSZTÁLY: a Telegram-briefben a modell két jegybanki sort írt
    hozzá egy háromsoros tool-válaszhoz (NBP 3,5 / BNR 3,3), mindkettőt
    rosszul. Ami nincs a bemenetben, az nem kerülhet a szövegbe."""
    b = _b([_cell("HU", "cpi", 1.6), _cell("HU", "core_cpi", 3.7),
            _cell("HU", "policy_rate", 5.5), _cell("HU", "unemployment", 4.5)])
    jo = "Az infláció 1.6 százalék, a maginfláció 3.7, az alapkamat 5.5."
    rossz = jo + " A lengyel alapkamat 3.5 százalék."
    assert ab.validate_review(jo, b) == []
    assert ab.validate_review(rossz, b), "a kitalált 3.5 átment a validátoron"


def test_a_kulonbseg_LEGITIM_szarmaztatott_ertek():
    """A „2,1 ponttal magasabb" nem kitalált szám: két adott érték
    különbsége. Ha ezt kizárnánk, minden összevetést megtiltanánk — és pont
    az összevetés lenne a szemle értelme."""
    b = _b([_cell("HU", "cpi", 1.6), _cell("HU", "core_cpi", 3.7),
            _cell("HU", "policy_rate", 5.5), _cell("HU", "unemployment", 4.5)])
    assert ab.validate_review(
        "A maginfláció 3.7, ami 2.1 ponttal a fejlődés 1.6 felett van.", b) == []


def test_az_idoszak_evszama_nem_kitalalt_szam():
    b = _b([_cell("HU", "cpi", 1.6), _cell("HU", "core_cpi", 3.7),
            _cell("HU", "policy_rate", 5.5), _cell("HU", "unemployment", 4.5)])
    assert ab.validate_review("2026 júliusában az infláció 1.6 volt.", b) == []


def test_a_CJK_szivargas_ELDOBJA_a_szemlet():
    b = _b([_cell("HU", "cpi", 1.6), _cell("HU", "core_cpi", 3.7),
            _cell("HU", "policy_rate", 5.5), _cell("HU", "unemployment", 4.5)])
    assert ab.validate_review("Az infláció 1.6 százalék, a 低 érték jó.", b)
    kinai = _b([_cell("CN", "cpi", 1.6), _cell("CN", "core_cpi", 3.7),
                _cell("CN", "policy_rate", 5.5), _cell("CN", "unemployment", 4.5)],
               lang="zh")
    assert ab.validate_review("通胀为 1.6。", kinai) == [], \
        "a kínai kiadásban a kínai írásjel nem hiba"


def test_ures_es_tul_hosszu_szemle_ELDOBODIK():
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    assert ab.validate_review("", b) == ["ures"]
    assert ab.validate_review("1,6 " * 3600, b)


def test_a_prompt_KIMONDJA_hogy_irany_csak_elozovel():
    """A LEGFONTOSABB SZABÁLY. Egy 3,7%-os maginflációról nem lehet tudni,
    emelkedik-e — ha a modell mégis ír róla, KITALÁLJA."""
    b = _b([dict(_cell("HU", "cpi", 1.6), prev_value=1.9, prev_period="2026-06"),
            _cell("HU", "core_cpi", 3.7)])
    pr = ab._review_prompt(b, "Hungarian")
    assert "elozo: 1.9 (2026-06)" in pr
    assert "elozo: NINCS" in pr, "a hiányzó előző érték nem látszik a promptban"
    assert "ONLY where the fact line" in pr
    assert "never explain WHY" in pr


def test_a_prompt_TILTJA_a_forrasmegjelolest_a_szovegben():
    """A táblázat minden sora hozza a saját forrását; a mondatba tett
    hivatkozás csak zavar — a Kommandant ezt a Telegram-briefnél kérte."""
    b = _b([_cell("HU", "cpi", 1.6)] * 2)
    assert "NO SOURCES in the text" in ab._review_prompt(b, "Hungarian")


def test_negy_szam_alatt_NINCS_szemle():
    """Két-három számból a „szemle" csak újramondaná a táblázatot."""
    async def _ai(*a, **k):
        raise AssertionError("nem lett volna szabad modellt hívni")
    b = _b([_cell("HU", "cpi", 1.6)])
    assert asyncio.run(ab.build_area_review(b, _ai)) == ""


def test_a_bukott_modellhivas_NEM_viszi_el_a_tablazatot():
    async def _ai(*a, **k):
        raise RuntimeError("SiliconFlow 429")
    b = _b([_cell("HU", "cpi", 1.6)] * 5)
    assert asyncio.run(ab.build_area_review(b, _ai)) == ""


def test_a_rossz_szemle_ELDOBODIK_nem_javul():
    async def _ai(*a, **k):
        return {"response": "Az infláció 1.6. A cseh alapkamat 9.9 százalék."}
    b = _b([_cell("HU", "cpi", 1.6), _cell("HU", "core_cpi", 3.7),
            _cell("HU", "policy_rate", 5.5), _cell("HU", "unemployment", 4.5)])
    assert asyncio.run(ab.build_area_review(b, _ai)) == "", \
        "a kitalált 9.9-es szemle megjelent volna"


# ── irányok (nulla token) ────────────────────────────────────────────────

def test_az_irany_a_SZAMOKBOL_jon_nem_modellbol():
    sorok = ab.iranyok([dict(_cell("HU", "cpi", 1.6), prev_value=1.9),
                        dict(_cell("HU", "core_cpi", 3.7), prev_value=3.5)])
    assert sorok[0]["delta"] == -0.3
    assert sorok[1]["delta"] == 0.2


def test_elozo_ertek_nelkul_NINCS_irany():
    """A „nem tudom" itt is teljes értékű válasz: nyíl nélkül a szám
    önmagában áll, és senki nem olvas bele trendet."""
    sorok = ab.iranyok([_cell("HU", "cpi", 1.6)])
    assert sorok[0]["delta"] is None


def test_a_validator_MAGA_ne_legyen_a_hibafelulet():
    """⚠️ EZ A TESZT EGY MÉRT SAJÁT HIBÁBÓL SZÜLETETT (2026-08-31).

    A validátor első változatában a szám-regex duplán escape-elve került a
    fájlba (`\\\\d` a `\\d` helyett), a CJK-tartomány szintén. Következmény:
    `_szamok()` MINDIG üres halmazt adott, a CJK-vizsgálat SOHA nem talált —
    a validátor tehát mindent átengedett, és közben zölden futott.

    Ez a néma-siker hibaosztály legrosszabb alakja: a HIBAFELÜLET maga volt
    a hibás. Ezért a mérőeszközt itt POZITÍV KONTROLLAL hitelesítjük — előbb
    bizonyítsa be, hogy egyáltalán talál valamit."""
    assert ab._szamok("1.6 és 3,7 meg -0.5") == {1.6, 3.7, -0.5}, \
        "a szám-felismerő nem talál számot — a validátor vak"
    assert ab._szamok("nincs itt szam") == set()
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    assert ab.validate_review("低", b), "a CJK-vizsgálat nem talál CJK-t"


def test_az_ai_query_hivas_ALAKJA_helyes():
    """ÉLES HIBA (2026-08-31): az `ai_query` első paramétere a MODELL, nem a
    prompt — a pozicionális átadás mind a 12 kiadásra „got multiple values
    for argument 'model'"-t adott, a fail-closed ág pedig szó nélkül üres
    szemlét csinált belőle. A táblázatok kimentek, a szemle némán elmaradt:
    a hibatűrés elrejtette a hibát."""
    kapott = {}

    async def _ai(*args, **kw):
        kapott.update(kw)
        kapott["pozicionalis"] = args
        return {"response": "Az infláció 1.6 százalék."}

    b = _b([_cell("HU", "cpi", 1.6)] * 5)
    asyncio.run(ab.build_area_review(b, _ai))
    assert kapott["pozicionalis"] == (), "pozicionális átadás — a szerződés törékeny"
    assert "model" in kapott and "prompt" in kapott
    assert kapott["no_thinking"] is True and kapott["no_tools"] is True


def test_a_hozzaferes_megtagadas_NEM_tunhet_el_a_hibaturesben(caplog):
    """ÉLES HIBA (2026-08-31): az `ai_query` a hozzáférés-megtagadást NORMÁL
    válaszként adja vissza (`{"error": "ZUGANG VERWEIGERT…"}`), nem
    kivételként. Az első kibontóm erre üres sztringet adott, a fail-closed ág
    pedig szó nélkül elhagyta a szemlét — 12 kiadás maradt kommentár nélkül,
    és a log annyit mondott: „ures". A hibaüzenet elveszett a hibatűrésben."""
    import logging
    with caplog.at_level(logging.ERROR):
        assert ab._valasz_szovege(
            '{"error": "ZUGANG VERWEIGERT: x", "status": "denied"}') == ""
    assert any("ELUTASITVA" in r.message for r in caplog.records), \
        "a megtagadás némán ment át"


def test_ismeretlen_valasz_alak_is_NYOMOT_hagy(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        assert ab._valasz_szovege({"valami_uj_mezo": "x"}) == ""
    assert any("ismeretlen valasz-alak" in r.message for r in caplog.records)


# ── A VALIDÁTOR HAMIS RIASZTÁSAI — mind ÉLES futásból (2026-08-31) ───────
# Az első éles körben 12-ből 6 szemle bukott el, és MIND A HAT JOGOS VOLT.
# A mérőeszköz volt szigorúbb a valóságnál, nem a modell pontatlanabb.

def _tr_brief():
    return {"lang": "tr", "country": "TR",
            "home": [_cell("TR", "cpi", 31.754), _cell("TR", "core_cpi", 29.796),
                     _cell("TR", "retail_trade", 13.432), _cell("TR", "unemployment", 7.6)],
            "anchor": [_cell("EA", "cpi", 2.9), _cell("EA", "core_cpi", 2.5)]}


def test_a_modell_az_OLVASONAK_kerekit():
    """A török infláció a panelben 31,754, a szemlében 31,8. Az egzakt
    egyezést követelő validátor a török, a görög ÉS a kínai szemlét is
    eldobta — pedig mind helyes volt."""
    sz = ("Türkiye'de enflasyon yüzde 31,8'e gerilerken, çekirdek yüzde 29,8'e "
          "düştü; euro bölgesi ortalaması yüzde 2,9 ve yüzde 2,5.")
    assert ab.validate_review(sz, _tr_brief()) == []


def test_a_negativ_rata_POZITIV_nagysagkent_is_leirhato():
    """Az előjelet az IGE hordozza: „房价下跌6.3%", „0,5 százalékkal csökkent".
    Előjel-szigorúan a kínai szemle bukott el."""
    b = {"lang": "zh", "country": "CN",
         "home": [dict(_cell("CN", "house_prices", -6.3), prev_value=-5.7),
                  _cell("CN", "cpi", 0.5), _cell("CN", "gdp_growth", 4.3),
                  _cell("CN", "bond_yield_10y", 1.71)],
         "anchor": [_cell("EA", "cpi", 2.9)]}
    assert ab.validate_review("房价下跌6.3%,较5.7%扩大。", b) == []


def test_az_egesz_szamok_NEM_szamitanak_kitalaltnak():
    """A kínai „10年期国债" (10 ÉVES kötvény) „10"-e, a negyedév-számok és az
    évszámok mind kitalált számként buktak. Egy makró-ráta a prózában
    gyakorlatilag mindig tizedesjeggyel szerepel."""
    assert ab._szamok("10年期国债收益率1.71%,二季度GDP") == {1.71}
    assert ab._szamok("2026 júliusában, a 3. negyedévben") == set()


def test_a_kitalalt_szam_MEG_MINDIG_fennakad():
    """A lazítás nem lyukaszthatja ki a szűrőt: ez a teszt az egész
    javításnak a fékje."""
    b = {"lang": "hu", "country": "HU",
         "home": [_cell("HU", "cpi", 1.6), _cell("HU", "core_cpi", 3.7)],
         "anchor": []}
    assert ab.validate_review("Az infláció 1,6. A cseh alapkamat 9,9 százalék.", b)


def test_a_hosszkorlat_elfer_ot_mondat_franciaul():
    """Mérve: egy helyes francia szemle 913 karakter volt, a korlát 900."""
    assert ab.REVIEW_MAX_KAR >= 10000, "a szemle megint chat-buborekra van szabva"


# ══════════════════════════════════════════════════════════════════════════
# NAPI PIACI RÉTEG — enélkül a blokk nem napi, hanem havi
# ══════════════════════════════════════════════════════════════════════════
#
# Kommandant, 2026-08-31: „ez a Brief lényegében a hét minden napján
# megjelenhet ugyanezzel — nem rendelkezik aktuális információval … a statok
# gyorstájékoztatók előző havi adatait taglalja. Másrészt nincs nemzetközi
# része."
#
# A kritika pontos volt: júliusi CPI, Q2 GDP, Q1 bérek — ugyanez a szöveg
# augusztus 5. és 25. között bármelyik nap megállta volna a helyét.


def _q(sym, price, prev, name=None):
    return {"symbol": sym, "name": name or sym, "price": price,
            "previous_close": prev, "currency": "USD",
            "last_trade_at": "2026-08-31 18:25 UTC"}


def test_a_napi_valtozas_GEPI_szamitas():
    """Nem a modell mondja meg, hogy esett-e — a két szám."""
    hivas = {}

    async def _sd(tool, args):
        hivas["sym"] = args["symbol"]
        return _q("^GSPC", 7683.24, 7711.76, "S&P 500")

    ab._MARKET_CACHE.clear()
    q = asyncio.run(ab.fetch_quote(_sd, "^GSPC"))
    assert q["change_pct"] == -0.37
    assert q["name"] == "S&P 500"


def test_elozo_zaro_nelkul_NINCS_valtozas():
    async def _sd(tool, args):
        return _q("X", 100.0, None)

    ab._MARKET_CACHE.clear()
    assert asyncio.run(ab.fetch_quote(_sd, "X"))["change_pct"] is None


def test_a_jegyzes_CACHE_ELODIK():
    """Tizenkét kiadás ugyanazt a hat globális tickert kérné le — a cache
    nélkül hetvenkét felesleges hívás."""
    n = {"i": 0}

    async def _sd(tool, args):
        n["i"] += 1
        return _q("^VIX", 15.07, 15.2)

    ab._MARKET_CACHE.clear()
    asyncio.run(ab.fetch_quote(_sd, "^VIX"))
    asyncio.run(ab.fetch_quote(_sd, "^VIX"))
    assert n["i"] == 1


def test_a_KOZOS_piaci_blokk_minden_kiadasban_ugyanaz():
    assert len(ab.MARKET_GLOBAL) >= 5
    kulcsok = {k for k, _ in ab.MARKET_GLOBAL}
    assert {"sp500", "vix", "oil_wti", "gold"} <= kulcsok


def test_minden_kiadasnak_van_piaci_bejegyzese():
    """A ru/uk üres — nincs hazai ország —, de a KULCSNAK léteznie kell,
    különben egy hiányzó nyelv KulcsHibát dobna a generálásban."""
    for lg in ab.AREA_COUNTRY:
        assert lg in ab.MARKET_HOME, f"hiányzik a piaci leképezés: {lg}"
    assert ab.MARKET_HOME["ru"] == () and ab.MARKET_HOME["uk"] == ()


def test_a_piaci_bukas_NEM_viszi_el_a_makro_tablazatot():
    """A makró a termék, a piac a ráadás. Fordítva nem igaz."""
    async def _panel(tool, args):
        if tool == "yfinance":
            raise RuntimeError("Yahoo 503")
        return {"rows": [_cell("HU", "cpi", 1.6)]}

    out = asyncio.run(ab.build_area_briefs(_panel))
    assert out["hu"]["home"], "a piaci hiba elvitte a makró-blokkot"


def test_a_szemle_prompt_KOVETELI_a_napi_reszt():
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    b["market"] = {"global": {"sp500": {"price": 7683.24, "change_pct": -0.37,
                                        "currency": "USD"}}, "home": {}}
    pr = ab._review_prompt(b, "Hungarian")
    assert "TODAY'S MARKETS" in pr
    assert "-0.37% ma" in pr
    assert "THE FIRST PARAGRAPH MUST CARRY TODAY" in pr
    assert "could be published any day between the 5th and the 25th" in pr


def test_a_szemle_HAROM_bekezdes_vilag_hazai_es_amit_figyelni_kell():
    """Kommandant: „nincs nemzetközi része" — majd a kétbekezdéses változatra:
    „KEVÉS". Jogosan: az első verzió felsorolás volt, nem elemzés."""
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    pr = ab._review_prompt(b, "Hungarian")
    assert "PARAGRAPH 1 — THE WORLD" in pr
    assert "PARAGRAPH 2 — HOME" in pr
    assert "PARAGRAPH 3 — WHAT TO WATCH" in pr
    assert "NOT as a set of yardsticks" in pr


def test_a_prompt_TILTJA_a_tablazat_felmondasat():
    """A számok MÁR OTT VANNAK a szöveg alatti táblázatban, időszakkal és
    forrással. Egy mondat, ami visszamondja őket, nulla információt ad — a
    szemle dolga az, hogy mit JELENTENEK EGYÜTT."""
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    pr = ab._review_prompt(b, "Hungarian")
    assert "DO NOT RECITE THE TABLE" in pr
    assert "adds NOTHING" in pr
    assert "MEAN TOGETHER" in pr


def test_az_52_hetes_sav_bekerul_a_tenyekbe():
    """Egy 15,02-es VIX önmagában üres adat; hogy az ÉVES SÁV ALJÁN van, már
    állítás a piac állapotáról. Enélkül a szemle felsorolás marad."""
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    b["market"] = {"global": {"vix": {"price": 15.02, "change_pct": 3.9,
                                      "low_52w": 11.0, "high_52w": 51.0}},
                   "home": {}}
    pr = ab._review_prompt(b, "Hungarian")
    assert "52-week range 11.0-51.0" in pr
    assert "10% of the way up that range" in pr


def test_a_validator_ELFOGADJA_a_piaci_szamokat():
    """A szemle a mai tőzsdei számokat írja le — ha a validátor nem ismerné
    őket, minden napi szemlét eldobna kitalált számként."""
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    b["market"] = {"global": {"sp500": {"price": 7683.24, "prev_close": 7711.76,
                                        "change_pct": -0.37}}, "home": {}}
    assert ab.validate_review(
        "Az S&P 500 7683,24 ponton áll, 0,37 százalékos eséssel.", b) == []


# ══════════════════════════════════════════════════════════════════════════
# AMI NEM VÁLTOZOTT, AZT NE ÍRJUK ÚJRA
# ══════════════════════════════════════════════════════════════════════════
#
# Kommandant, 2026-08-31: „ami állandó, azt meg se kell csinálni többször,
# addig amíg új adat nem jön (mondjuk a jegybanki alapkamat változása) — ami
# érdekes, az a VÁLTOZÁS, és az azonnali hír."


def test_az_ujjlenyomat_a_MAKRORA_all_nem_a_piacra():
    """A tőzsde minden nap mozog. Ha beleszámítana, sose lenne
    „változatlan" — és pont az elv veszne el."""
    a = _b([_cell("HU", "cpi", 1.6)])
    b = dict(a, market={"global": {"sp500": {"price": 1, "change_pct": 9.9}}})
    assert ab.makro_ujjlenyomat(a) == ab.makro_ujjlenyomat(b)


def test_az_ujjlenyomat_ERZEKENY_az_ertekre_es_az_idoszakra():
    a = _b([_cell("HU", "policy_rate", 5.5)])
    assert ab.makro_ujjlenyomat(a) != ab.makro_ujjlenyomat(
        _b([_cell("HU", "policy_rate", 5.25)]))
    mas_ido = _b([dict(_cell("HU", "policy_rate", 5.5), period="2026-08")])
    assert ab.makro_ujjlenyomat(a) != ab.makro_ujjlenyomat(mas_ido)


def test_a_kamatvagas_VALTOZASKENT_jelenik_meg():
    regi = _b([_cell("HU", "policy_rate", 6.0)])
    uj = _b([_cell("HU", "policy_rate", 5.5)])
    v = ab.valtozasok(uj, regi)
    assert len(v) == 1 and v[0]["kind"] == "valtozott"
    assert v[0]["old_value"] == 6.0 and v[0]["new_value"] == 5.5


def test_az_UJ_adat_is_valtozas():
    """Az olvasónak az is hír, hogy egy hiányzó adat végre megjött."""
    v = ab.valtozasok(_b([_cell("TR", "wages", 42.0)]), _b([]))
    assert v and v[0]["kind"] == "uj"


def test_az_elso_kiadasnak_nincs_mihez_kepest():
    assert ab.valtozasok(_b([_cell("HU", "cpi", 1.6)]), None) == []


def test_a_KIS_piaci_mozgas_nem_indokol_ujrairast():
    b = _b([_cell("HU", "cpi", 1.6)])
    b["market"] = {"global": {"sp500": {"change_pct": -0.37},
                              "gold": {"change_pct": 0.4}}}
    assert ab._piac_mozdult(b) == []


def test_a_NAGY_piaci_mozgas_igen():
    b = _b([_cell("HU", "cpi", 1.6)])
    b["market"] = {"global": {"sp500": {"change_pct": -2.4}}}
    assert ab._piac_mozdult(b) == ["sp500 -2.40%"]


def test_a_valtozas_blokk_KIMONDJA_ha_nincs_valtozas():
    assert "no macro figure changed" in ab._valtozas_blokk([], [])


def test_a_prompt_szerint_a_VALTOZASSAL_kell_kezdeni():
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    pr = ab._review_prompt(b, "Hungarian",
                           "CHANGED HU policy_rate: 6.0 @2026-07 → 5.5 @2026-08")
    assert "LEAD WITH WHAT IS NEW" in pr
    assert "CHANGED HU policy_rate" in pr
    assert "do not\nmanufacture novelty" in pr or "manufacture novelty" in pr


def test_magyarorszagra_KOSAR_all_index_helyett():
    """MÉRVE 2026-08-31: a BUX-nak nincs élő Yahoo-szimbóluma (`^BUX` STALE,
    `^BUXI`/`BUX.BD`/`BUX.BUD` not found), a stooq sem viszi, a bet.hu pedig
    JS-portletből tölti az értéket.

    ⚠️ A bet.hu statikus HTML-jében EGY szám szerepel: 14.639.314.708 Ft —
    a BÁZISKAPITALIZÁCIÓ. Pont az a fajta érték, ami hihető indexszámként
    becsúszott volna a briefbe. Ezért a négy blue chip áll helyette; saját
    súlyozású „indexet" gyártani hamisítás lenne."""
    hu = dict(ab.MARKET_HOME["hu"])
    assert {"OTP.BD", "MOL.BD", "RICHT.BD", "MTEL.BD"} <= set(hu.values())
    assert not any("BUX" in v for v in hu.values()), "halott BUX-szimbólum a listán"


# ── A BUX SAJÁT TOOLBÓL JÖN ─────────────────────────────────────────────
# A BUX-nak nincs élő Yahoo-szimbóluma; a StatData `bet_index` toolja adja,
# a bet.hu beágyazott JSON-blobjából.

def test_a_BUX_sajat_toolbol_jon_nem_a_yahoorol():
    hivasok = []

    async def _sd(tool, args):
        hivasok.append(tool)
        if tool == "bet_index":
            return {"index": "BUX", "value": 149161.68, "change_pct": -1.0,
                    "prev_close": 150666.43, "market_cap_bnft": 27969.0,
                    "source": "Budapesti Ertektozsde (bet.hu)"}
        return {"symbol": args["symbol"], "price": 1.0, "previous_close": 1.0}

    ab._MARKET_CACHE.clear()
    m = asyncio.run(ab.fetch_market(_sd, "hu"))
    assert "bet_index" in hivasok
    bux = m["home"]["index_bux"]
    assert bux["price"] == 149161.68 and bux["change_pct"] == -1.0
    assert not any("BUX" in s for s in
                   (x[1] for x in ab.MARKET_HOME["hu"])), "halott BUX-szimbólum"


def test_az_index_tool_ALAKJA_azonos_a_jegyzesevel():
    """Ha a két forrás más alakot adna, minden fogyasztót (renderelő, szemle,
    validátor) két ágra kellene bontani — és az egyik ág elfelejtődne."""
    async def _sd(tool, args):
        return {"index": "BUX", "value": 149161.68, "change_pct": -1.0,
                "prev_close": 150666.43}

    async def _yf(tool, args):
        return {"symbol": "^GSPC", "price": 7683.24, "previous_close": 7711.76}

    ab._MARKET_CACHE.clear()
    a = asyncio.run(ab.fetch_index_tool(_sd, "bet_index", {"index": "BUX"}))
    ab._MARKET_CACHE.clear()
    b = asyncio.run(ab.fetch_quote(_yf, "^GSPC"))
    assert {"symbol", "name", "price", "prev_close", "change_pct",
            "currency"} <= set(a) & set(b)


def test_a_baziskapitalizacio_NEM_kerul_at():
    """A bet.hu lapján a 14.639.314.708 Ft a BÁZISKAPITALIZÁCIÓ, és az első
    próbám pont azt szedte fel indexértékként. Ami nem kell, azt ne is hozzuk
    magunkkal — egy fölösleges mező előbb-utóbb valakinek a táblázatában
    landol."""
    async def _sd(tool, args):
        return {"index": "BUX", "value": 149161.68, "change_pct": -1.0,
                "prev_close": 150666.43, "market_cap_bnft": 27969.0}

    ab._MARKET_CACHE.clear()
    q = asyncio.run(ab.fetch_index_tool(_sd, "bet_index", {"index": "BUX"}))
    assert "market_cap_bnft" not in q


def test_a_bet_index_bukasa_nem_viszi_el_a_tobbi_papirt():
    async def _sd(tool, args):
        if tool == "bet_index":
            return {"error": "A bet.hu lapjan nem talalhato az index-blob"}
        return {"symbol": args["symbol"], "price": 45240.0,
                "previous_close": 46000.0}

    ab._MARKET_CACHE.clear()
    m = asyncio.run(ab.fetch_market(_sd, "hu"))
    assert "index_bux" not in m["home"]
    assert m["home"]["stocks_otp"]["change_pct"] == -1.65


def test_az_EZRES_TAGOLAS_nem_tor_ketto_egy_szamot():
    """ÉLES BUKÁS (2026-08-31): a francia szemle „8 334,5"-öt írt (CAC 40), és
    a naiv minta ebből „334,5"-öt látott — egy számot, ami sehol nem szerepel
    a bemenetben. A szemle eldobódott, pedig hibátlan volt.

    A tagolás nyelvenként más: „8 334,5" (fr/hu), „8,334.5" (en),
    „8.334,5" (de). Ezért a tokent MINDKÉT olvasatban előállítjuk, és elég,
    ha az egyik ismerős."""
    assert 8334.5 in ab._szamok("8 334,5 points")
    assert 8334.5 in ab._szamok("8,334.5 points")
    assert 8334.5 in ab._szamok("8.334,5 Punkte")
    assert 149161.68 in ab._szamok("A BUX 149 161,68 ponton zárt")


def test_a_valodi_francia_szemle_ATMEGY():
    fr = ("Le pétrole a bondi de 2,79 % à 85,73 dollars — l'or a cédé 1,05 %, "
          "le S&P 500 a perdu 0,37 %. Le CAC 40 a terminé à 8 334,5 points.")
    b = {"lang": "fr", "country": "FR", "home": [], "anchor": [],
         "market": {"global": {"oil_wti": {"price": 85.73, "change_pct": 2.79},
                               "gold": {"price": 4482.5, "change_pct": -1.05},
                               "sp500": {"price": 7683.38, "change_pct": -0.37}},
                    "home": {"index_cac": {"price": 8334.5, "change_pct": -0.2}}}}
    assert ab.validate_review(fr, b) == []
    # ⚠️ A FEK: a lazitas nem lyukaszthatja ki a szurot
    assert ab.validate_review(fr + " Le taux tchèque est de 9,91 %.", b)


def test_a_tagolo_PONTOSAN_harom_jegyet_tagol():
    """⚠️ A JAVITAS SAJAT HIBAJA. Az elso kétolvasatos változat az „1,6"-ból
    „16"-ot is előállított (vessző = ezres tagoló) — vagyis a validátor SAJÁT
    MAGA gyártott nem létező számokat, és tíz teszt bukott el tőle.

    Egy ezres tagoló PONTOSAN három számjegyet fog. Ha nem, az az olvasat
    érvénytelen, és el kell dobni — nem elég „mindkét értelmezést" felvenni."""
    assert ab._szamok("1,6") == {1.6}
    assert ab._szamok("1.71") == {1.71}
    assert ab._szamok("7683,24") == {7683.24}
    # a valodi nyelvi valtozatok mind ugyanoda vezetnek
    for alak in ("8 334,5", "8,334.5", "8.334,5"):
        assert 8334.5 in ab._szamok(alak), alak


def test_a_KETERTELMU_szam_tokenkent_dol_el():
    """⚠️ NEGYEDIK ITERACIO UGYANAZON A VALIDATORON, ES EZ A LEGTANULSAGOSABB.

    A „2.467" (amerikai maginfláció) KÉTÉRTELMŰ: angolul 2,467 — németül
    2467. Mindkét olvasat érvényes ALAK, tehát mindkettőt elő kell állítani.
    Csakhogy én laposan, KÜLÖN SZÁMKÉNT ellenőriztem őket, és a 2467 sehol
    nem szerepelt a bemenetben → a validátor eldobta a HELYES szemlét.
    Élesben ez 12-ből 9-et vitt el.

    A helyes szabály: egy TOKEN akkor rendben, ha BÁRMELYIK olvasata
    ismerős."""
    b = {"lang": "en", "country": "GB", "home": [], "anchor": [
            {"country": "US", "indicator": "core_cpi", "value": 2.467}],
         "market": {"global": {}, "home": {}}}
    assert ab.validate_review("US core inflation is 2.467%.", b) == []
    # a token mindket olvasata eloall — ezt a lapos nezet is mutatja
    assert ab._szamok("2.467") == {2.47, 2467.0}   # ket tizedesre kerekitve
    # de a DONTES tokenenkent tortenik
    assert [t for t, _ in ab._szam_jeloltek("2.467")] == ["2.467"]


def test_a_ketertelmuseg_NEM_lyukasztja_ki_a_szurot():
    """A megengedőbb szabály fék nélkül átengedne bármit: ha egy kitalált
    szám VALAMELYIK olvasata véletlenül ismerős, átcsúszna. Ezt elfogadjuk —
    de a nyilvánvalóan idegen számnak fenn kell akadnia."""
    b = {"lang": "en", "country": "GB", "home": [], "anchor": [
            {"country": "US", "indicator": "core_cpi", "value": 2.467}],
         "market": {"global": {}, "home": {}}}
    assert ab.validate_review("The Czech rate is 9.91%.", b)


def test_a_KESKENY_szokoz_is_ezres_tagolo():
    """ÉLES: az orosz szemle „4 494,0"-t írt keskeny nem-törő szóközzel
    (U+202F), amit az első változat nem ismert fel — „494,0" maradt belőle,
    és a helyes szemle eldobódott. Az orosz és a francia tipográfia ezt
    használja, nem a sima szóközt."""
    for sp in (" ", " ", " ", " ", " "):
        assert 4494.0 in ab._szamok(f"4{sp}494,0"), repr(sp)


def test_a_modell_EGESZRE_is_kerekithet():
    """ÉLES: a spanyol szemle az IBEX 19 974,1-et „19.974"-ként írta — levágta
    a tizedest, ahogy egy újságíró is tenné egy ötjegyű indexnél."""
    b = {"lang": "es", "country": "ES", "home": [], "anchor": [],
         "market": {"global": {}, "home": {"index_ibex": {"price": 19974.1}}}}
    assert ab.validate_review("El IBEX 35 cerró en 19.974 puntos.", b) == []


def test_a_kerekitesi_turelem_NEM_engedi_at_a_kitalalt_szamot():
    b = {"lang": "es", "country": "ES", "home": [], "anchor": [],
         "market": {"global": {}, "home": {"index_ibex": {"price": 19974.1}}}}
    assert ab.validate_review("La tasa checa es 9,91%.", b)


def test_a_PROMPT_valtozasa_is_ujrairast_indokol():
    """⚠️ MÉRT ESET (2026-08-31): átírtam a promptot (tiltás a táblázat
    felmondására, 52 hetes sáv, harmadik bekezdés), deployoltam, újragenerál­
    tam — és a szemle VÁLTOZATLAN maradt. A saját „ami nem változott, azt ne
    írjuk újra" logikám tartotta vissza: a makró-ujjlenyomat ugyanaz volt, a
    piac csendes.

    Élesben ez a helyes viselkedés — de a PROMPTOT is változásnak kell
    tekinteni, különben minden jövőbeli javítás csendben elvész."""
    import inspect
    src = inspect.getsource(ab.cron_entry)
    assert "review_prompt_version" in src
    assert "REVIEW_PROMPT_VERSION" in src
    # a verzio a tarolt payloadba is bekerul, kulonben nincs mihez hasonlitani
    assert 'b["review_prompt_version"] = REVIEW_PROMPT_VERSION' in src


def test_a_TAVLAT_bekerul_a_tenyekbe():
    """⚠️ EZ A VALASZ A „KEVES"-RE. Egy „az olaj ma 3,49%-kal emelkedett"
    mondat LEIRAS; egy „ma 3,49%, egy hónap alatt 12%" mondat ÁLLÍTÁS a
    trendről — és abból már lehet kérdezni, hogy a mai nap folytatás-e vagy
    fordulat. Két számból nem lesz elemzés, háromból igen."""
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    b["market"] = {"global": {"oil_wti": {"price": 86.31, "change_pct": 3.49,
                                          "w1": 5.2, "m1": 12.4, "m3": -3.1}},
                   "home": {}, "calendar": []}
    pr = ab._review_prompt(b, "Hungarian")
    assert "1 week +5.20%" in pr and "1 month +12.40%" in pr
    assert "USE THE HORIZONS" in pr
    assert "the day AGAINST\n   the month is a story" in pr.replace("  ", "  ")


def test_a_NAPTAR_a_PULZUSE_nem_a_szemlee():
    """A naptár NAPI ütemű: mi jön a következő napokban. A hetes érvényességű
    makró-szemlébe téve mindkét szöveg ugyanazt mondaná — a fájl saját elve
    („a két szöveg NEM átfedő") pedig épp ezt tiltja.

    A szemle harmadik bekezdése helyette a makró-feszültségeké: reálkamat,
    reálbér, harmonizált vs nemzeti mag, ellentmondó reálgazdasági mutatók."""
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    b["market"] = {"global": {"sp500": {"price": 1, "change_pct": 0.1}},
                   "home": {}, "news": [], "calendar": [
        {"date": "2026-09-10", "time": "14:15 CET", "region": "EUR",
         "indicator": "ECB Governing Council decision", "importance": "high"}]}
    pulzus = ab._pulse_prompt(b, "Hungarian")
    assert "UPCOMING RELEASES" in pulzus
    assert "ECB Governing Council decision" in pulzus
    szemle = ab._review_prompt(b, "Hungarian")
    assert "THE CALENDAR BELONGS TO THE DAILY REPORT" in szemle


def test_a_tavlat_a_ZARO_arakbol_szamol():
    async def _sd(tool, args):
        return {"data": [{"close": 100.0 + i} for i in range(70)]}
    ab._MARKET_CACHE.clear()
    t = asyncio.run(ab.fetch_tavlat(_sd, "^GSPC"))
    assert t["w1"] == round((169 / 164 - 1) * 100, 2)
    assert t["m1"] == round((169 / 148 - 1) * 100, 2)


def test_keves_adatpont_eseten_NINCS_tavlat():
    async def _sd(tool, args):
        return {"data": [{"close": 100.0}, {"close": 101.0}]}
    ab._MARKET_CACHE.clear()
    assert asyncio.run(ab.fetch_tavlat(_sd, "X")) is None


def test_a_piaci_ok_kitalalas_KET_MERT_esete_nevesitve():
    """A „ne találj ki okot" szabály ott volt, de a PIACI bekezdésnél nem
    fogott. Két valódi eset a v3 kimenetéből:
      · „az arany euróban nézve alig változott az erősödő dollár miatt" —
        eurós aranyárunk NINCS; egy meg nem adott devizanem kitalált szám,
        akármilyen hihetően hangzik a mondat;
      · „a BUX 1%-os csökkenése az olajhírt tükrözi" — semmi nem köti össze
        őket. Két dolog együttmozgása nem ok-okozat, és az olvasó nem tudja
        megkülönböztetni a következtetésedet a ténytől.
    Egy általános tiltást a modell megkerül; a KONKRÉT trükköt kell
    megnevezni — ez ma már egyszer eldőlt a hír-blokknál is."""
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    pr = ab._review_prompt(b, "Hungarian")
    assert "in EURO terms it barely moved" in pr
    assert "REFLECTING THE OIL NEWS" in pr
    assert "moving on the same day is not one causing the other" in pr
    assert ab.REVIEW_PROMPT_VERSION >= 4


# ══════════════════════════════════════════════════════════════════════════
# NAPI PIACI HELYZET — külön réteg, külön frissességgel
# ══════════════════════════════════════════════════════════════════════════
#
# Kommandant, 2026-08-31: „most már elég hosszú, de ez még mindig egy HETES
# érvényességű … nekünk kell a NAPI piaci helyzet is hozzá, ami PLUSZ
# frissességű."


def _b_piac(lang="hu"):
    b = _b([_cell("HU", "cpi", 1.6)] * 4, lang=lang)
    b["market"] = {"global": {
        "sp500": {"price": 7683.4, "change_pct": -0.33, "w1": 1.2, "m1": 3.4},
        "vix": {"price": 14.92, "change_pct": 3.88},
        "oil_wti": {"price": 86.31, "change_pct": 3.49, "m1": 12.4}},
        "home": {"index_bux": {"price": 149161.68, "change_pct": -1.0}},
        "calendar": [{"date": "2026-09-04", "region": "US",
                      "indicator": "US Non-Farm Payrolls", "importance": "high"}]}
    return b


def test_a_pulzus_prompt_TILTJA_a_makro_ismetleset():
    """A két szöveg NEM átfedő: a makró-szemle a havi statisztikát viszi, a
    pulzus a mai kereskedést. Ha a pulzus újra elmondaná az inflációt, megint
    csak ismétlés lenne."""
    pr = ab._pulse_prompt(_b_piac(), "Hungarian")
    assert "it is NOT a macro commentary" in pr
    assert "you are writing the wrong text" in pr
    assert "US Non-Farm Payrolls" in pr


def test_a_pulzus_ROVIDEBB_mint_a_makro_szemle():
    """Ez helyzetjelentés, nem elemzés — de a hírek megjelenésével bővült:
    ha okot is mondhat, ahhoz mondat kell. A lényeg a KÜLÖNBSÉG: a pulzus
    érdemben rövidebb marad a makró-szemlénél, különben a kettő
    összemosódik, és megint egy szöveg lesz belőle."""
    assert ab.PULSE_MAX_MONDAT < ab.REVIEW_MAX_MONDAT
    assert ab.PULSE_MAX_KAR < ab.REVIEW_MAX_KAR / 2


def test_harom_jegyzes_alatt_NINCS_pulzus():
    async def _ai(*a, **k):
        raise AssertionError("nem lett volna szabad modellt hivni")
    b = _b([_cell("HU", "cpi", 1.6)])
    b["market"] = {"global": {"sp500": {"price": 1, "change_pct": 0.1}}, "home": {}}
    assert asyncio.run(ab.build_market_pulse(b, _ai)) == ""


def test_a_tul_hosszu_pulzus_ELDOBODIK():
    async def _ai(*a, **k):
        return {"response": "A piac ma 7683,4 ponton. " * 200}
    assert asyncio.run(ab.build_market_pulse(_b_piac(), _ai)) == ""


def test_a_VALTOZATLAN_makroju_kiadas_IS_kap_napi_pulzust():
    """⚠️ KET SAJAT HIBAT FOGOTT MEG EZ AZ EGY TESZT.

    1. A reuse-ág eredetileg `continue`-val zárult, a pulzus pedig ALATTA
       készül — így pont azok a kiadások maradtak volna napi szöveg nélkül,
       ahol a makró nem változik, vagyis a hónap közepétől szinte mind.
    2. A javítás után a három pulzus-sor a CIKLUSON KÍVÜLRE került, így csak
       az UTOLSÓ kiadás kapott napi szöveget — a másik tizenegy némán
       kimaradt. Behúzási hiba, amit semmilyen szintaktikai ellenőrzés nem
       fog meg.

    Ezért VISELKEDÉST mérünk, nem forrásszöveget: az első változatom a
    `"continue" not in src` feltételt használta, és a saját KOMMENTÁROM
    tartalmazta a szót."""
    def _c(cc, ind, val=1.6):
        return {"country": cc, "indicator": ind, "value": val,
                "period": "2026-07", "status": "fresh", "unit_kind": "rate",
                "source_used": "t"}

    async def _panel(tool, args):
        if tool == "yfinance":
            return {"symbol": args.get("symbol", "X"), "price": 100.0,
                    "previous_close": 100.0}
        if tool in ("get_economic_calendar", "bet_index"):
            return {"events": []} if tool == "get_economic_calendar" else {"error": "-"}
        return {"rows": [_c(c, i) for c in ("HU", "DE", "EA", "US")
                         for i in ("cpi", "core_cpi", "policy_rate", "unemployment")]}

    hivott = {"review": 0, "pulse": 0}

    async def _ai(*a, **k):
        if "market situation report" in k.get("prompt", ""):
            hivott["pulse"] += 1
            return {"response": "A kereskedés csendes volt, 100 ponton."}
        hivott["review"] += 1
        return {"response": "Az infláció 1,6 százalék."}

    tarolo = {}
    eredeti = (ab.store_area_briefs, ab.load_area_brief)
    try:
        ab.store_area_briefs = lambda db, br: (tarolo.update(br), len(br))[1]
        ab.load_area_brief = lambda db, lang: tarolo.get(lang)
        r1 = asyncio.run(ab.cron_entry(None, _panel, _ai))
        assert r1["with_pulse"] == 12, f"nem mind a 12 kapott pulzust: {r1}"
        elso = hivott["review"]
        r2 = asyncio.run(ab.cron_entry(None, _panel, _ai))
        assert hivott["review"] == elso, "a valtozatlan makrot ujrairta"
        assert r2["with_pulse"] == 12, "a valtozatlan kiadasok nem kaptak pulzust"
        assert tarolo["hu"].get("market_pulse")
    finally:
        ab.store_area_briefs, ab.load_area_brief = eredeti


def test_a_pulzusnak_KULON_belepesi_pontja_van():
    """Két külön ütem, két külön cron: egy közös futás vagy feleslegesen
    íratná újra a makrót, vagy elavultan hagyná a piacot."""
    assert callable(ab.cron_pulse)
    import inspect
    src = inspect.getsource(ab.cron_pulse)
    assert "build_area_review" not in src, "a pulzus-cron a makrót is újraírja"
    assert "fetch_market" in src and "build_market_pulse" in src


def test_a_napi_pulzusnak_HAROM_kulon_receptje_van():
    """A `cron_schedule` EGYETLEN oszlop, tehát egy recept egy időpontban fut.
    A reggeli és délutáni hírszemlét 2026-04-10-én ugyanarra a sorra írták, és
    a második NÉMÁN eltörölte az elsőt — ugyanez a csapda áll itt is."""
    import inspect
    from plugins import recipes
    src = inspect.getsource(recipes)
    for cron in ("10 7 * * 1-5", "10 13 * * 1-5", "30 21 * * 1-5"):
        assert cron in src, f"hiányzó ütemezés: {cron}"
    assert "cron_pulse" in src


def test_az_UTOLSO_pulzus_a_ZARAS_UTAN_fut():
    """Kommandant, 2026-09-01: „A nap záróképe az érdekes." — a napon belüli
    pulzusok felülírják egymást, és ami megmarad, az a zárókép.

    ⚠️ EHHEZ VISZONT AZ UTOLSÓ FUTÁSNAK TÉNYLEG ZÁRÁS UTÁN KELL LENNIE. Az
    első verzió 19:10 UTC-t adott, ami az amerikai piacon 15:10 ET — vagyis
    KERESKEDÉS KÖZBEN. Az archívum így sosem látta volna a záróértéket, és
    ez senkinek nem tűnt volna fel: a szöveg ott lett volna, csak épp a
    délutánt írta volna le zárókép gyanánt.

    A NYSE 16:00 ET-kor zár = nyáron 20:00, télen 21:00 UTC. A 21:30
    mindkettőnél zárás után van."""
    import inspect
    from plugins import recipes
    src = inspect.getsource(recipes)
    assert "10 19 * * 1-5" not in src, "az utolsó pulzus kereskedés közben fut"
    assert "30 21 * * 1-5" in src


def test_a_pulzus_cron_NEM_hivja_a_makro_generalast():
    """Egy közös futás vagy feleslegesen íratná újra a makrót, vagy elavultan
    hagyná a piacot."""
    import inspect, pathlib
    src = pathlib.Path(inspect.getfile(ab)).read_text(encoding="utf-8")
    i = src.index("async def cron_pulse(")
    j = src.index("async def cron_entry(")
    assert "build_area_review" not in src[i:j]
    assert "cron_entry" not in src[i:j]


def test_amit_a_prompt_KER_azt_a_validator_ELFOGADJA():
    """⚠️ ÉLES BUKÁS (2026-09-01): az olasz és a lengyel NAPI helyzetjelentés
    eldobódott olyan számokon, mint 6,37 és −6,37 — miközben a prompt 10.
    szabálya KIFEJEZETTEN arra biztat, hogy az árat az éves sávhoz mérje
    („2%-kal a csúcs alatt", „a sáv alján").

    Amit kérek a modelltől, azt a validátornak ismernie kell. Különben a
    saját utasításom miatt bukik el a helyes szöveg — és ez a hetedik
    alkalom ma, hogy a mérőeszköz szigorúbb a valóságnál."""
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    b["market"] = {"global": {"sp500": {"price": 7683.4, "change_pct": -0.33,
                                        "low_52w": 6316.9, "high_52w": 7816.7}},
                   "home": {}}
    # a csucstol valo tavolsag: 7683,4 / 7816,7 - 1 = -1,71%
    assert ab.validate_review(
        "Az S&P 500 az éves csúcstól 1,71 százalékkal marad el.", b) == []
    # a sav aljatol: +21,63%
    assert ab.validate_review(
        "Az index az éves mélyponthoz képest 21,63 százalékkal áll magasabban.", b) == []
    # ⚠️ A FEK: ami tenyleg sehonnan nem vezetheto le, tovabbra is fennakad
    assert ab.validate_review("A cseh alapkamat 9,91 százalék.", b)


# ══════════════════════════════════════════════════════════════════════════
# NaN — A HIBA, AMI CSAK A HTTP-HATARON JELENTKEZIK
# ══════════════════════════════════════════════════════════════════════════
#
# Kommandant, 2026-09-01: „Az angol verzión láttam szöveges részt, a magyaron
# nem."
#
# A magyar payloadban NaN-ok voltak (`stocks_otp.m1`, `stocks_mol.w1` …): a
# BÉT-papírok történetében hiányzó záróárak vannak, és a százalékszámítás
# abból NaN-t ad. A `json.dumps` ALAPÉRTELMEZÉSBEN kiírja `NaN`-ként, a
# `json.loads` visszaolvassa — a hiba CSAK a HTTP-határon jelentkezett, ahol
# a Starlette `allow_nan=False`-szal kódol: HTTP 500, EGYETLEN nyelven.
#
# Csak a magyar érintett, mert csak ott vannak egyedi RÉSZVÉNYEK — a többi
# kiadásban index áll. Az Echolot órás frissítője így 11 kiadást frissített
# és a magyart kihagyta.


def test_a_hianyzo_zaroarbol_NEM_lesz_NaN():
    async def _sd(tool, args):
        # BET-papir unnepnapokkal: NaN-ok a sorozatban
        return {"data": [{"close": float("nan") if i % 7 == 0 else 100.0 + i}
                         for i in range(70)]}
    ab._MARKET_CACHE.clear()
    t = asyncio.run(ab.fetch_tavlat(_sd, "OTP.BD"))
    import math
    assert t is not None
    assert all(math.isfinite(v) for v in t.values()), t


def test_csupa_NaN_eseten_NINCS_tavlat():
    async def _sd(tool, args):
        return {"data": [{"close": float("nan")} for _ in range(70)]}
    ab._MARKET_CACHE.clear()
    assert asyncio.run(ab.fetch_tavlat(_sd, "X")) is None


def test_a_NaN_a_TAROLAS_elott_is_kiszurodik():
    """Végső védőháló: a forrás-szintű szűrés mellé. Egy NaN nem robban —
    csendben él tovább, és két napig is észrevétlen maradhat."""
    import math
    p = {"a": float("nan"), "b": [1.0, float("inf")], "c": {"d": 2.5}}
    t = ab._nan_mentes(p)
    assert t["a"] is None and t["b"] == [1.0, None] and t["c"]["d"] == 2.5
    # es a tarolt JSON mar allow_nan=False mellett is kodolhato
    json.dumps(t, allow_nan=False)


def test_a_tarolas_ALLOW_NAN_FALSE_mellett_kodol():
    """A Starlette ezzel kódol. Ha a tárolás megengedőbb, a hiba a
    HTTP-határig rejtve marad — pontosan ez történt."""
    import inspect
    src = inspect.getsource(ab.store_area_briefs)
    assert "allow_nan=False" in src
    assert "_nan_mentes" in src


# ══════════════════════════════════════════════════════════════════════════
# PIACI HÍREK — a pulzus eddig egyetlen hírt sem kapott
# ══════════════════════════════════════════════════════════════════════════
#
# Kommandant, 2026-09-01: „a streetaccount nevű piaci portál elég sok
# szigorúan piaci hírt generál … nyilvánvalóan több információ kellene a
# piaci briefbe."
#
# A StreetAccount FactSet-termék, előfizetéses, nincs nyilvános feedje. Ami
# helyette van: CNBC, Investing.com, Seeking Alpha, Bloomberg HT — mind ott
# van az Echolot forrásai között, csak a pulzus nem látta őket.


def test_a_hirek_bekerulnek_a_tenyekbe():
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    b["market"] = {"global": {"oil_wti": {"price": 86.3, "change_pct": 3.5}},
                   "home": {}, "calendar": [],
                   "news": [{"title": "OPEC+ tightens supply guidance",
                             "source": "CNBC"}]}
    pr = ab._pulse_prompt(b, "Hungarian")
    assert "MARKET HEADLINES FROM THE LAST 24 HOURS" in pr
    assert "OPEC+ tightens supply guidance" in pr
    assert "[CNBC]" in pr


def test_a_HIR_megvaltoztatja_az_ok_szabalyt():
    """⚠️ A tilalom HELYES VOLT az adott bemenetre — de közben pont azt
    tiltotta, amiért egy piaci szemlét olvasnak. Hírekkel a szabály
    megváltozik: okot mondani szabad, de KIZÁRÓLAG olyat, ami a kapott
    címekben benne van."""
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    b["market"] = {"global": {}, "home": {}, "calendar": [], "news": []}
    pr = ab._pulse_prompt(b, "Hungarian")
    assert "THE RULE DEPENDS ON WHAT YOU WERE GIVEN" in pr
    assert "ONLY one that a headline actually states" in pr
    assert 'Never write "probably because"' in pr


def test_a_hirlekeres_SOSE_dob():
    async def _robban(**kw):
        raise RuntimeError("Echolot 503")
    assert asyncio.run(ab.fetch_piaci_hirek(_robban, "hu", {})) == []
    assert asyncio.run(ab.fetch_piaci_hirek(None, "hu", {})) == []


def test_a_hirlekeres_TOBBFELE_valasz_alakot_kezel():
    """Az `echolot_query` alakja verziónként eltérhet; a lista, az
    `articles` és az `items` kulcs mind előfordul."""
    for nyers in ([{"title": "A"}],
                  {"articles": [{"title": "A"}]},
                  {"items": [{"headline": "A"}]},
                  {"results": [{"title": "A"}]}):
        async def _q(**kw):
            return nyers
        assert asyncio.run(ab.fetch_piaci_hirek(_q, "hu", {}))[0]["title"] == "A"


def test_a_reszvenykosarak_MINDEN_kiadasra_megvannak():
    """Kommandant: „további részvények, részvénycsoportok is kellene."""
    for lg in ("de", "fr", "it", "es", "pl", "el", "tr", "zh", "en", "hu"):
        papirok = [k for k, _ in ab.MARKET_HOME[lg] if k.startswith("stocks_")]
        assert len(papirok) >= 3, f"{lg}: csak {len(papirok)} papir"


def test_a_reszvenyekhez_NEM_kerunk_tortenetet():
    """A távlat értékes, de minden tickerre lekérni MEGDUPLÁZNÁ a hívásokat —
    és a teljes futás már így is a határon van. Az indexek megkapják."""
    assert ab._kell_tavlat("index_dax") is True
    assert ab._kell_tavlat("sp500") is True
    assert ab._kell_tavlat("stocks_sap") is False


# ── A HÍRLEKÉRÉS CÉLZOTT — arról kérdez, ami mozgott ────────────────────
# ⚠️ MÉRVE 2026-09-01: recency-rendben, `query` nélkül kérve a 06:00 UTC-s
# lekérés AUSZTRÁL ÉS ÚJ-ZÉLANDI helyi üzleti hírt hozott — magántőkealapok
# Sydney-ben, kamionbaleset Tasmániában, egy edzőtermi bérlet. Egyetlen sem
# szólt az olajról, az S&P-ről vagy a hozamokról, és a modell HELYESEN
# hagyta figyelmen kívül mind a tizenkettőt.
#
# Az ok NEM a szféra-választás volt: abban az órában a csendes-óceáni sajtó
# az aktív, míg Európa és az USA alszik. A RENDEZÉS volt rossz.

def test_a_hirlekeres_a_NAP_MOZGATOIROL_kerdez():
    m = {"global": {"sp500": {"change_pct": -0.33},
                    "oil_wti": {"change_pct": 3.49},
                    "gold": {"change_pct": 0.03}},
         "home": {"stocks_otp": {"change_pct": -1.65}}}
    mozgatok = ab._mozgatok(m)
    assert mozgatok[0] == "oil_wti", "nem a legnagyobb mozgás vezet"
    assert "gold" not in mozgatok[:2], "a 0,03%-os arany előrébb került"


def test_a_HAZAI_piac_elonyt_kap():
    """Egy magyar olvasónak az OTP esése fontosabb, mint egy azonos mértékű
    S&P-mozgás."""
    m = {"global": {"sp500": {"change_pct": -1.5}},
         "home": {"stocks_otp": {"change_pct": -1.5}}}
    assert ab._mozgatok(m)[0] == "stocks_otp"


def test_a_hirlekeres_MINDEN_mozgatorol_kerdez_kulon():
    kerdesek = []

    async def _q(**kw):
        kerdesek.append(kw.get("query"))
        return {"articles": [{"title": f"cikk-{len(kerdesek)}"}]}

    m = {"global": {"oil_wti": {"change_pct": 3.5}, "sp500": {"change_pct": -1.0}},
         "home": {}}
    h = asyncio.run(ab.fetch_piaci_hirek(_q, "hu", m))
    assert len(kerdesek) >= 2
    assert any("oil" in (q or "") for q in kerdesek)
    assert all(x.get("about") for x in h), "nincs megjelölve, miről szól a hír"


def test_az_azonos_cim_NEM_ismetlodik():
    async def _q(**kw):
        return {"articles": [{"title": "Ugyanaz a cim"}]}
    m = {"global": {"oil_wti": {"change_pct": 3.5}, "gold": {"change_pct": -2.0}},
         "home": {}}
    h = asyncio.run(ab.fetch_piaci_hirek(_q, "hu", m))
    assert len(h) == 1


def test_ures_piac_eseten_is_kerdez_valamit():
    """Piaci adat nélkül sem maradhat hír nélkül a pulzus."""
    kerdesek = []

    async def _q(**kw):
        kerdesek.append(kw.get("query"))
        return {"articles": []}

    asyncio.run(ab.fetch_piaci_hirek(_q, "hu", {}))
    assert kerdesek, "üres piacnál egyetlen kérdést sem tett fel"


def test_a_hazai_es_a_nemzetkozi_AZONOS_SULLYAL_esik_latba():
    """Kommandant, 2026-09-01: „a magyar ÉS nemzetközi MINDEN SZEMPONTBÓL
    AZONOS SÚLLYAL kell latba esnie". A korábbi 1,4x hazai előny eltávolítva:
    a nap legnagyobb elmozdulása számít, bárhol történt."""
    import inspect
    src = inspect.getsource(ab._mozgatok)
    assert "1.4" not in src, "a hazai suly visszakerult"
    # azonos merteku mozgas: egyik sem elozheti meg a masikat SULY miatt
    m = {"global": {"sp500": {"change_pct": -2.0}},
         "home": {"stocks_otp": {"change_pct": -1.9}}}
    assert ab._mozgatok(m)[0] == "sp500", "a kisebb hazai mozgas elore kerult"


def test_MINDEN_globalis_eszkoznek_van_hirkulcsa():
    """Hírkulcs nélkül az eszköz sosem kap hírt — némán kimarad a lekérésből,
    és a mérőszám („N mozgatóról kérdeztünk") közben zöld."""
    hiany = [k for k, _ in ab.MARKET_GLOBAL if k not in ab.NEWS_QUERY]
    assert hiany == [], f"hírkulcs nélküli eszközök: {hiany}"


def test_a_globalis_blokk_ELEG_SZELES():
    """Hat számmal nem lehet piaci képet festeni. Három kontinens
    részvénypiaca, hat nyersanyag, három deviza, két hozam — ezek együtt már
    ELLENTMONDHATNAK egymásnak, és a szemle ereje ebből jön."""
    kulcsok = {k for k, _ in ab.MARKET_GLOBAL}
    assert len(kulcsok) >= 18
    assert {"nikkei", "hangseng", "stoxx50"} <= kulcsok, "hiányzik egy kontinens"
    assert {"oil_brent", "copper", "silver", "gas"} <= kulcsok
    assert {"dxy", "usd_jpy"} <= kulcsok


def test_a_hir_kulcsszavak_LEGFELJEBB_KET_SZOSAK():
    """⚠️ MÉRT ESET (2026-09-01): az FTS a többszavas kifejezést
    ÉS-kapcsolatnak veszi. Az „S&P 500 Wall Street stocks equities" NULLA
    találatot adott, mert a négy kifejezés együtt egyetlen cikkben sem
    szerepel — a pulzus így 19 eszközzel és 0 hírrel készült.

    Mérve: többszavas → 0, egyszavas („oil") → 5, kétszavas („crude oil")
    → 5. A hosszabb kérdés nem pontosabb, hanem ÜRES."""
    hosszu = {k: v for k, v in ab.NEWS_QUERY.items() if len(v.split()) > 2}
    assert hosszu == {}, f"kettőnél több szavas kulcsszavak: {hosszu}"


def test_MINDEN_eszkoznek_van_hirkulcsa_a_hazaiaknak_is():
    hiany = [k for k, _ in ab.MARKET_GLOBAL if k not in ab.NEWS_QUERY]
    assert hiany == [], f"globális hiány: {hiany}"
    hazai = set()
    for t in ab.MARKET_HOME.values():
        hazai |= {k for k, _ in t}
    hiany2 = sorted(k for k in hazai
                    if k not in ab.NEWS_QUERY and not k.startswith("fx_"))
    assert hiany2 == [], f"hazai hiány: {hiany2}"


# ══════════════════════════════════════════════════════════════════════════
# A HÍRLEKÉRÉS PARAMÉTEREI MÉRÉSBŐL VALÓK (2026-09-01, 40 hívás)
# ══════════════════════════════════════════════════════════════════════════

def test_EGY_szfera_hivasonkent_nem_lista():
    """⚠️ HÁROM KÜLÖN HIBA VOLT AZ EREDETI SZFERA-STRINGBEN:
      1. a `global_business` szféra NEM LÉTEZIK (106 szféra van, ez nincs),
      2. `query` mellett az `echolot_query` CSAK az elsőt használja, tehát a
         vesszős lista csendben egyetlen szférára szűkült,
      3. a `global_press` nem piaci szféra (kamionbaleset, „cutest arrivals").
    """
    import inspect
    # a nem letezo szfera NEVE szerepelhet a magyarazo kommentarban —
    # az ERTEKEKBEN nem szabad
    assert "global_business" != ab.GLOBAL_NEWS_SPHERE
    assert "global_business" not in ab.HOME_NEWS_SPHERE.values()
    assert "," not in ab.GLOBAL_NEWS_SPHERE, "vesszős szféra-lista"
    for sz in ab.HOME_NEWS_SPHERE.values():
        assert "," not in sz


def test_a_hazai_eszkozok_HAZAI_szferabol_kerdeznek():
    """Mérve: a „BUX" `global_economy`-val NULLA találat, `hu_economy`-val
    8/8 tiszta. Az „OTP" szféra nélkül Aadhaar-OTP-t és bankkártya-OTP-t hoz."""
    kerdesek = []

    async def _q(**kw):
        kerdesek.append((kw.get("spheres"), kw.get("query")))
        return {"articles": [{"title": f"c{len(kerdesek)}"}]}

    m = {"global": {"sp500": {"change_pct": -2.0}},
         "home": {"stocks_otp": {"change_pct": -1.5}}}
    asyncio.run(ab.fetch_piaci_hirek(_q, "hu", m))
    otp = [k for k in kerdesek if k[1] == ab.NEWS_QUERY["stocks_otp"]]
    assert otp and otp[0][0] == "hu_economy", f"az OTP nem hazai szférából: {otp}"


def test_a_MOZGATOK_mennek_elol_nem_a_fix_kerdesek():
    """⚠️ MÉRT HIBA: kvóta nélkül a tizenkét FIX kérdés (négy széles + nyolc
    tematikus) háromszor háromig kitöltötte a huszonhatos keretet, és a
    MOZGATÓKRÓL — a nap tényleges sztorijáról — egyetlen hír sem fért be.
    A kimenetben csak „market" és „macro" címke szerepelt.

    A sorrend: 1. a nap mozgatói, 2. a valódi gazdasági hírek, 3. az átfogó
    kép. Mindegyik saját kvótával, hogy egyik se nyomja el a másikat."""
    kerdesek = []

    async def _q(**kw):
        kerdesek.append(kw.get("query"))
        return {"articles": [{"title": f"{kw.get('query')}-{i}"} for i in range(3)]}

    m = {"global": {"oil_wti": {"change_pct": 3.5}}, "home": {}}
    h = asyncio.run(ab.fetch_piaci_hirek(_q, "hu", m))
    assert kerdesek[0] == ab.NEWS_QUERY["oil_wti"], "nem a mozgató az első"
    temak = {x["about"] for x in h}
    assert {"oil_wti", "macro"} <= temak, f"hiányzó csoport: {temak}"


def test_a_RITKA_kifejezesek_hosszabb_ablakot_kapnak():
    """Mérve (d1/d2/d3): Treasury yield 3/4/4, ECB 11/15/25."""
    ablakok = {}

    async def _q(**kw):
        ablakok[kw.get("query")] = kw.get("days")
        return {"articles": []}

    m = {"global": {"us_10y": {"change_pct": 2.0}, "sp500": {"change_pct": 1.0}},
         "home": {}}
    asyncio.run(ab.fetch_piaci_hirek(_q, "hu", m))
    assert ablakok[ab.NEWS_QUERY["us_10y"]] == 3
    assert ablakok[ab.NEWS_QUERY["sp500"]] == 2


def test_a_nyelvszures_NINCS_bekapcsolva():
    """Az `en`-szűrés a Nikkei találatait 22-ről 12-re vitte le, és épp az
    ANSA/Infobae/FinanzNachrichten piaci jelentéseket dobta ki. A szűrés nem
    tisztít, hanem CSONKÍT."""
    kapott = {}

    async def _q(**kw):
        kapott.update(kw)
        return {"articles": []}

    asyncio.run(ab.fetch_piaci_hirek(_q, "hu", {}))
    assert not kapott.get("language")
    assert kapott.get("limit", 0) <= 50, "a szerver 50-nél vág"


def test_egy_bo_talalatu_kifejezes_NEM_nyomja_el_a_tobbit():
    """A „stocks" 50 találatot ad; korlát nélkül az egész keretet elvinné."""
    async def _q(**kw):
        return {"articles": [{"title": f"{kw.get('query')}-{i}"} for i in range(50)]}

    m = {"global": {"oil_wti": {"change_pct": 3.0}, "gold": {"change_pct": 2.0}},
         "home": {}}
    h = asyncio.run(ab.fetch_piaci_hirek(_q, "hu", m))
    temak = {x["about"] for x in h}
    assert len(temak) >= 3, f"csak {temak} témáról van hír"


# ══════════════════════════════════════════════════════════════════════════
# MINŐSÉGI AUDIT UTÁNI JAVÍTÁSOK (2026-09-01)
# ══════════════════════════════════════════════════════════════════════════
#
# Mérés: a pulzus 855 karaktert használt a 3400-ból, és 25 jegyzésből 8-at
# nevezett meg. A keret tehát nem szorított — a szűk keresztmetszet az volt,
# hogy a pulzus-prompt hat szabályából ÖT tiltás volt, és a szemle
# „levezethető összefüggések" listájának NEM volt megfelelője.


def _b_teljes():
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    b["anchor"] = [dict(_cell("US", "cpi", 3.3), prev_value=3.2),
                   _cell("EA", "policy_rate", 2.25)]
    b["market"] = {"global": {
        "sp500": {"price": 7686, "change_pct": -0.33, "m1": 3.3, "currency": "USD"},
        "us_10y": {"price": 4.758, "change_pct": 0.81, "m3": 6.32, "currency": "USD"},
        "gold": {"price": 4483, "change_pct": 0.03, "m1": 11.16, "currency": "USD"},
        "oil_wti": {"price": 86.9, "change_pct": 1.05, "m1": 7.87, "currency": "USD"}},
        "home": {}, "calendar": [], "news": []}
    return b


def test_a_szuperlativusz_GEPI_rangsorbol_jon():
    """⚠️ MÉRT ÖNELLENTMONDÁS: a jelentés a WTI havi +7,87%-át „a legmarkánsabb
    emelkedő áramlat a piacon"-nak nevezte, majd a KÖVETKEZŐ mondatban leírta
    az arany +11,16%-át. Két mondaton belül cáfolta magát, mert a 19 eszköz
    `m1` mezőit nem vetette össze. A rangsort ezért GÉP készíti."""
    pr = ab._pulse_prompt(_b_teljes(), "Hungarian")
    assert "RANKINGS (computed" in pr
    assert "ONE MONTH — largest gains: gold +11.16%" in pr
    assert "SUPERLATIVES COME FROM THE RANKINGS BLOCK ONLY" in pr


def test_a_pulzus_MEGKAPJA_a_horgony_makrot():
    """A pulzus korábban KIZÁRÓLAG piaci számokat látott, és ettől olyat
    állított, amiről adata sem volt: „a kamatkörnyezet fokozatos szigorodását
    mutatja" — egyetlen jegybanki kamat nélkül."""
    pr = ab._pulse_prompt(_b_teljes(), "Hungarian")
    assert "MACRO ANCHOR" in pr and "US cpi: 3.3" in pr
    assert "FOR REFERENCE ONLY" in pr, "a horgony témává válhat"
    assert "REAL YIELD:" in pr


def test_a_hozam_NEM_dollarban_van():
    """A yfinance a ^TNX-re is `USD`-t ad, és a ténysorban „4.758 (USD)" állt."""
    pr = ab._pulse_prompt(_b_teljes(), "Hungarian")
    assert "us_10y: 4.758 (%)" in pr
    assert "us_10y: 4.758 (USD)" not in pr


def test_a_hozam_tavlata_RELATIV_valtozas():
    """A „+6,32% 3 months" valójában 4,475 → 4,758, azaz +28 bázispont. Az
    olvasó a két százalékot egymás mellett 6,32 SZÁZALÉKPONTNAK érti."""
    pr = ab._pulse_prompt(_b_teljes(), "Hungarian")
    assert "[relative change, NOT percentage points]" in pr


def test_a_pulzus_ISMERI_a_levezetheto_osszefuggeseket():
    """Ez volt az egyetlen legnagyobb hiány: a szemlének volt ilyen listája,
    a pulzusnak nem."""
    pr = ab._pulse_prompt(_b_teljes(), "Hungarian")
    for jel in ("BREADTH —", "RISK APPETITE:", "THE CURVE:", "REAL YIELD:",
                "CORRELATION BREAKS:", "CURRENCY CONSISTENCY:",
                "TREND LADDER:",
                # 2026-09-01: a cimke kiegeszult („(the SECOND paragraph
                # only)"), mert a vilag es a haza kulon bekezdes lett. A
                # teszt szandeka valtozatlan: a prompt TANITSA a hazai
                # szam vilaghoz meresét.
                "HOME AGAINST THE WORLD"):
        assert jel in pr, f"hiányzik: {jel}"


def test_a_POLARITAS_ki_van_mondva():
    """A ténysor minden eszközre ugyanúgy írja, hogy „a sáv 92%-ánál áll" —
    de a VIX ott félelem, az EUR/HUF ott GYENGE forint, a hozam ott szigorú
    pénzügyi feltétel. A prompt eddig sehol nem mondta meg, melyik mit
    jelent: néma siker volt, hogy a modell eltalálta."""
    pr = ab._pulse_prompt(_b_teljes(), "Hungarian")
    assert "WHAT A LEVEL MEANS" in pr
    assert "a WEAK home currency" in pr
    assert "VIX at the top of its range = fear" in pr


def test_az_ar_NEM_kereslet_es_az_egyuttmozgas_NEM_okozas():
    """Két mért kitalált ok: „az arany +11,16%-a erős keresletet jelez"
    (nincs volumen-adatunk) és „az OTP esése húzta le a BUX-ot" (nincs
    indexsúly)."""
    pr = ab._pulse_prompt(_b_teljes(), "Hungarian")
    assert "PRICE IS NOT DEMAND" in pr, "az ar!=kereslet szabaly hianyzik"
    assert "NOT ONE CAUSING THE OTHER" in pr


def test_a_csendes_nap_NEM_ures_jelentes():
    pr = ab._pulse_prompt(_b_teljes(), "Hungarian")
    assert "A QUIET DAY IS NOT AN EMPTY REPORT" in pr


def test_a_szemle_prompt_belso_ellentmondasai_javitva():
    """Három mért ellentmondás: „TWO paragraphs" majd három felsorolva; egy
    csonka mondat („Of 30 sentences at most"); és a 2. szabály azt állította,
    hogy „you have no news", miközben ugyanaz a prompt átadta a címeket."""
    pr = ab._review_prompt(_b_teljes(), "Hungarian")
    assert "in THREE paragraphs" in pr
    assert "Use 30 sentences at most" in pr
    # a szemle 2. szabalya mar NEM tagadja a sajat hir-bemenetet
    assert "You have no news, no" not in pr
    assert "THE CALENDAR BELONGS TO THE DAILY REPORT" in pr


def test_a_naptar_LATJA_a_kamatdontest():
    """⚠️ MÉRT HIÁNY: a 4 napos ablak KIHAGYTA a szeptember 10-i EKB
    kamatdöntést — a hónap legfontosabb eurózónai eseményét —, mert az
    kilenc nappal volt. Egy „előre néző" blokk, ami a kamatdöntést nem
    látja, nem előre néző.

    Mérve: 3 nap → 3 esemény, 7 nap → 3, 14 nap → 9."""
    import inspect
    src = inspect.getsource(ab.fetch_naptar)
    assert '"days_ahead": 14' in src
    assert '"all"' in src, "a régiónként külön lekérés kimarad eseményeket"
    assert '"EU", "US"' not in src


def test_a_szelesseg_HAROM_mertekkel_merheto():
    """A Yahoo NEM ad advance/decline-t, új csúcs/mélypont számot, sem
    put/call rátát — mind a 15 szimbólum mérve halott (^ADD ^TRIN ^TICK
    ^BPSPX ^CPC …). Helyette három proxy, mind mérve működik."""
    kulcsok = {k for k, _ in ab.MARKET_GLOBAL}
    assert "sp500_ew" in kulcsok, "hiányzik az egyensúlyozott index"
    assert len(ab.MARKET_SECTORS) == 11
    b = _b_teljes()
    b["market"]["sectors"] = {"energia": 2.04, "technologia": 0.44,
                              "kozmu": -1.17, "kommunikacio": -1.35}
    pr = ab._pulse_prompt(b, "Hungarian")
    assert "US SECTOR BREADTH: 2 of 4 sectors closed higher" in pr
    assert "Best: energia +2.04%" in pr


def test_a_hazug_Yahoo_nevet_FELULIRJUK():
    """A Yahoo a ^MOVE-ra „Northern Trust iBoxx 5-Year Tar"-t ad vissza.
    Mérve. A nevet MI adjuk, nem a szolgáltató."""
    b = _b_teljes()
    b["market"]["global"]["move"] = {"price": 75.32, "change_pct": 6.1,
                                     "currency": "USD"}
    pr = ab._pulse_prompt(b, "Hungarian")
    assert "MOVE (kotvenypiaci volatilitas)" in pr
    assert "Northern Trust" not in pr


def test_a_volatilitas_TOBB_mint_a_reszvenye():
    kulcsok = {k for k, _ in ab.MARKET_GLOBAL}
    assert {"vix", "move", "ovx", "gvz"} <= kulcsok
    pr = ab._pulse_prompt(_b_teljes(), "Hungarian")
    assert "VOLATILITY BEYOND EQUITIES" in pr


# ══════════════════════════════════════════════════════════════════════════
# HÍREK: ESEMÉNY vs VÉLEMÉNY, ÉS A VALÓDI GAZDASÁGI HÍR
# ══════════════════════════════════════════════════════════════════════════

def test_a_befektetesi_tipp_MEG_VAN_JELOLVE_de_nem_dobjuk_el():
    """Kommandant, 2026-09-01: „a befektetési tippek is előfordulhatnak, de
    jelezni kell, hogy ezek befektetési tippek hírekből."

    Egy megjelölt vélemény hasznos — mutatja, miről beszél a piac. Egy
    megjelöletlen vélemény TÉNYNEK látszik, és akkor a szemle befektetési
    tanácsot ad egy hírportál nevében."""
    assert ab._hir_tipus("Software Is Back: 5 Stocks To Buy", "Seeking Alpha") == "opinion"
    assert ab._hir_tipus("4 Stocks to Go ALL IN September", "YouTube") == "opinion"
    assert ab._hir_tipus("Nebius Stock Deserves A Pause After Rally", "SA") == "opinion"
    assert ab._hir_tipus("US stocks close lower as oil rises", "Investing.com") == "event"
    assert ab._hir_tipus("Treasury Selloff Sends Yields Jumping", "Bloomberg") == "event"


def test_a_velemeny_JELOLVE_kerul_a_promptba():
    b = _b_teljes()
    b["market"]["news"] = [
        {"title": "5 Stocks To Buy", "source": "Seeking Alpha",
         "about": "market", "kind": "opinion"},
        {"title": "Stocks close lower", "source": "Investing.com",
         "about": "market", "kind": "event"}]
    pr = ab._pulse_prompt(b, "Hungarian")
    assert "5 Stocks To Buy [Seeking Alpha] (about: market) ⟨OPINION" in pr
    assert "Stocks close lower [Investing.com] (about: market)\n" in pr
    assert "HEADLINES MARKED ⟨OPINION/RECOMMENDATION⟩ ARE NOT EVENTS" in pr
    assert "giving investment advice in a newspaper's name" in pr


def test_az_ESEMENYT_leiro_ige_hoz_esemenyt():
    """Mérve: egy esemény-IGE („fell", „close lower", „selloff") eseményt
    talál; egy FŐNÉV („stocks") ajánlócikket. A korábbi egyetlen „stocks"
    horgony csupa tippet hozott."""
    assert "stocks" not in ab.BROAD_NEWS_QUERIES
    assert "close lower" in ab.BROAD_NEWS_QUERIES
    assert "selloff" in ab.BROAD_NEWS_QUERIES


def test_a_VALODI_gazdasagi_hirek_is_bejonnek():
    """Kommandant: „hozza be a rendes gazd/piaci híreket is". Az eddigi
    lekérés CSAK ESZKÖZÖKRŐL kérdezett — arról, ami mozgott. De egy
    kamatdöntés vagy egy inflációs adat MAGA a hír, akkor is, ha aznap
    egyetlen szám sem mozdult miatta."""
    kerdesek = []

    async def _q(**kw):
        kerdesek.append(kw.get("query"))
        return {"articles": []}

    ab._MARKET_CACHE.clear()
    asyncio.run(ab.fetch_piaci_hirek(_q, "hu", {}))
    for t in ("inflation", "Federal Reserve", "ECB", "central bank", "tariffs"):
        assert t in kerdesek, f"nem kérdez rá: {t}"


def test_a_globalis_hirek_CACHE_ELODNEK_a_12_kiadasra():
    """22 kérdés × 12 kiadás = 264 fölösleges hívás naponta háromszor."""
    n = {"i": 0}

    async def _q(**kw):
        n["i"] += 1
        return {"articles": []}

    ab._MARKET_CACHE.clear()
    asyncio.run(ab.fetch_piaci_hirek(_q, "hu", {}))
    elso = n["i"]
    asyncio.run(ab.fetch_piaci_hirek(_q, "de", {}))
    assert n["i"] == elso, "a második kiadás újra lekérdezte a globális híreket"


def test_a_forras_ONMAGABAN_nem_belyegez_velemenynek():
    """⚠️ MÉRT HIBA: az első változat a Seeking Alpha MINDEN cikkét
    véleménynek jelölte, így a „German Inflation Edges Up In August"
    ténykölés kapott ⟨OPINION⟩ címkét. Egy eseményt véleménynek bélyegezni
    ugyanolyan hiba, mint fordítva: az első elrejti a hírt, a második
    tanácsot ad tényként."""
    assert ab._hir_tipus("German Inflation Edges Up In August", "Seeking Alpha") == "event"
    assert ab._hir_tipus("No Rate-Hiking Map From The Federal Reserve", "Seeking Alpha") == "event"
    # a cim maga dont; a forras csak sulyosbit
    assert ab._hir_tipus("Is the gold rally over?", "Seeking Alpha") == "opinion"
    assert ab._hir_tipus("Why the rally could run through 2027", "Seeking Alpha") == "opinion"


# ═══ A VILAG EGY — a haza nem (Kommandant, 2026-09-01) ═══════════════════

def _brief_ket_kiadas():
    """Ugyanaz a vilagpiac, KULONBOZO MERETU hazai keszlet — ez a lenyeg."""
    vilag = {f"g{i}": {"price": 100.0 + i, "change_pct": (i % 5) * 0.4 - 0.8}
             for i in range(26)}
    a = {"lang": "hu", "country": "HU", "market": {
        "global": dict(vilag),
        "home": {f"h{i}": {"price": 10.0, "change_pct": 0.5} for i in range(6)}}}
    b = {"lang": "de", "country": "DE", "market": {
        "global": dict(vilag),
        "home": {f"h{i}": {"price": 10.0, "change_pct": 0.5} for i in range(3)}}}
    return a, b


def test_a_vilagpiaci_tenyek_kiadastol_FUGGETLENEK():
    """⛔ A MERT HIBA (2026-09-01). A `_rang` a `global` es a `home`
    jegyzeseket EGYBE szamolta; mivel a hazai keszlet kiadasonkent mas
    meretu, egy VILAGPIACI allitas kiadasfuggove valt. Elesben, ugyanaz a
    nap, ugyanaz a vilagpiac:  hu „32 jegyzesbol 9" · de „9 von 30" ·
    tr „31 kotasyonun 10'u". A modell nem szamolt rosszul — a tenyblokk
    volt kiadasfuggo."""
    from plugins.area_briefs import _piac_tenyek
    a, b = _brief_ket_kiadas()
    wa = [l for l in _piac_tenyek(a).split("\n") if l.startswith("WORLD")]
    wb = [l for l in _piac_tenyek(b).split("\n") if l.startswith("WORLD")]
    assert wa == wb, "a vilagpiaci tenyek kiadasonkent elternek"
    assert any("26 quotes" in l for l in wa), (
        "a vilag-darabszam nem a 26 globalis jegyzesbol jon")


def test_a_hazai_tenyek_kiadasonkent_KULONBOZNEK():
    """A masik fele: ami nyelvteruleti, az maradjon az."""
    from plugins.area_briefs import _piac_tenyek
    a, b = _brief_ket_kiadas()
    ha = [l for l in _piac_tenyek(a).split("\n") if l.startswith("HOME")]
    hb = [l for l in _piac_tenyek(b).split("\n") if l.startswith("HOME")]
    assert ha and hb and ha != hb


def test_a_zajkuszob_CSAK_a_napi_ablakra_szol():
    """⚠️ A 0,3%-os kuszob NAPI fogalom. Eddig mindharom ablak kiirta a sajat
    zaj-sorat — HAROM kulonbozo szammal (elesben 32/9, 27/3, 27/0) —, a
    prompt viszont egyetlenkent hivatkozott rajuk. A modell valasztott."""
    from plugins.area_briefs import _piac_tenyek
    a, _ = _brief_ket_kiadas()
    zaj = [l for l in _piac_tenyek(a).split("\n") if "noise threshold" in l]
    assert all(l.split(" —")[0].endswith("TODAY") for l in zaj), (
        f"zaj-sor nem-napi ablakon: {zaj}")


def test_a_prompt_verzio_emelkedett():
    """A tarolt pulzus ujrahasznalodik, ha a verzio egyezik — prompt-valtozas
    verzio-emeles NELKUL nem lep eletbe."""
    from plugins.area_briefs import PULSE_PROMPT_VERSION
    assert PULSE_PROMPT_VERSION >= 6
