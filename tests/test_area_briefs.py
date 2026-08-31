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


def test_a_NAPTAR_az_elore_nezes():
    """Egy szemle, ami csak a múlt havi adatokat magyarázza, nem mondja meg,
    mire figyeljen az olvasó holnap."""
    b = _b([_cell("HU", "cpi", 1.6)] * 4)
    b["market"] = {"global": {}, "home": {}, "calendar": [
        {"date": "2026-09-03", "time": "11:00 CET", "region": "EUR",
         "indicator": "Euro Area Unemployment", "importance": "high"}]}
    pr = ab._review_prompt(b, "Hungarian")
    assert "UPCOMING RELEASES" in pr
    assert "Euro Area Unemployment" in pr
    assert "THE CALENDAR IS THE FORWARD LOOK" in pr


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
