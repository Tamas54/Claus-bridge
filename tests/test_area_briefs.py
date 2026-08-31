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
import json

import pytest

from plugins import area_briefs as ab


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
    assert ab.validate_review("1,6 " * 900, b)


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
    assert ab.REVIEW_MAX_KAR >= 2500, "a szemle megint chat-buborekra van szabva"
