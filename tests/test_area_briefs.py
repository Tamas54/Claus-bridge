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
