"""PIAC-ELTÉRÉS ÁRNYÉK-MÓDBAN (piac_elteres.py, 11-23) — hálózat nélkül.

Az ígéretek: (1) a p_mi UGYANABBÓL az eloszlásból jön, amit a CRPS pontoz, kerekítési korrekcióval (kézzel számolt
értékek); (2) a piaci közép-ár a csapdák ellen (üres oldal, a Polymarket kimenet-sorrendje, string vagy tömb mezők, PAV a
létrán); (3) a szűrő csak ott enged jelzést, ahol a mért múlt jó, és a likviditás csak érzékenység; (4) az előre rögzítés
a Telegram és a feloldás ELŐTT történik, a zárás után rögzített sor nem pontozható; (5) pontozás és árnyék-PnL; (6) a
kapcsoló KI állásban semmi nem fut. A napló memóriában (a PgNaplo felülete) — SQLite nincs.
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

import elso_kozles as ek
import piac_elteres as pe
import szim_backteszt as sb

Z90 = 1.2815515655446004            # a standard normál 90%-os kvantilise (kézzel, nem a modulból)


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _run(c):
    return asyncio.run(c)


# ══ 1. p_mi a sávból ═══════════════════════════════════════════════════════════════════════════════════════════

def test_p_mi_kezzel_szamolva_kerekitesi_korrekcioval():
    el = pe.eloszlas_savbol(3.0, 4.0, 0.8)
    sigma = 0.5 / Z90
    assert el["mu"] == pytest.approx(3.5) and el["sigma"] == pytest.approx(sigma)
    # P(Y > 3,6), a hivatal 0,1-re kerekít: 1 − Φ((3,65 − 3,5) / σ)
    assert pe.p_kerdes(el, {"tipus": "nagyobb", "x": 3.6}, 0.1) == pytest.approx(1 - _phi(0.15 / sigma), abs=1e-9)
    assert pe.p_kerdes(el, {"tipus": "nagyobb", "x": 3.6}, 0.1) == pytest.approx(0.3503, abs=1e-4)
    # szó szerinti olvasat (érzékenység): 1 − Φ(0,1/σ)
    assert pe.p_kerdes(el, {"tipus": "nagyobb", "x": 3.6}, 0) == pytest.approx(1 - _phi(0.1 / sigma), abs=1e-9)
    # „legalább 3,6” = „nagyobb 3,5” a 0,1-es rácson
    assert pe.p_kerdes(el, {"tipus": "legalabb", "x": 3.6}, 0.1) == pytest.approx(
        pe.p_kerdes(el, {"tipus": "nagyobb", "x": 3.5}, 0.1))
    # pontosan 3,7: Φ((3,75 − 3,5)/σ) − Φ((3,65 − 3,5)/σ)
    assert pe.p_kerdes(el, {"tipus": "pontosan", "x": 3.7}, 0.1) == pytest.approx(
        _phi(0.25 / sigma) - _phi(0.15 / sigma), abs=1e-9)


def test_p_mi_ugyanaz_az_eloszlas_amit_a_crps_pontoz():
    el = pe.eloszlas_savbol(3.27, 3.78)
    for x in (3.1, 3.5, 3.74, 4.2):
        assert sb.crps_sav(3.27, 3.78, sb.SAV_P, x) == pytest.approx(sb.crps_normal(el["mu"], el["sigma"], x))


def test_kizarolagos_savok_osszege_egy():
    el = pe.eloszlas_savbol(3.27, 3.78)
    savok = ([{"tipus": "legfeljebb", "x": 3.3}] + [{"tipus": "pontosan", "x": round(3.4 + i / 10, 1)} for i in range(10)]
             + [{"tipus": "legalabb", "x": 4.4}])
    assert sum(pe.p_kerdes(el, q, 0.1) for q in savok) == pytest.approx(1.0, abs=1e-9)


def test_diszkret_eloszlas_pontosan_kerekites_nelkul():
    el = {"tipus": "diszkret", "atomok": [[4.0, 0.5], [4.25, 0.3], [4.5, 0.2]]}
    assert pe.p_kerdes(el, {"tipus": "nagyobb", "x": 4.0}, 0.25) == pytest.approx(0.5)
    assert pe.p_kerdes(el, {"tipus": "pontosan", "x": 4.25}, 0.25) == pytest.approx(0.3)


def test_racsra_fel_felfele():
    assert pe.racsra(3.73768, 0.1) == pytest.approx(3.7)
    assert pe.racsra(3.65, 0.1) == pytest.approx(3.7)
    assert pe.racsra(4.149, 0.1) == pytest.approx(4.1)


# ══ 2. a piaci közép-ár és a csapdák ════════════════════════════════════════════════════════════════════════════

def test_kozep_ar_ures_oldal():
    assert pe.kozep_ar(None, "0.04") == {"bid": 0.0, "ask": 0.04, "kozep": 0.02, "spread": 0.04}
    assert pe.kozep_ar("0.97", None)["kozep"] == pytest.approx(0.985)


def test_pav_javitja_a_letrat():
    jav, sertes = pe.pav_nem_novekvo([0.9, 0.5, 0.6, 0.2])
    assert sertes == 1 and jav == pytest.approx([0.9, 0.55, 0.55, 0.2])
    assert pe.pav_nem_novekvo(jav)[0] == pytest.approx(jav)          # idempotens


def _kalshi_m(x, bid, ask, **kw):
    return {"ticker": f"KXCPIYOY-26NOV-T{x}", "strike_type": "greater", "floor_strike": x, "status": "active",
            "yes_bid_dollars": bid, "yes_ask_dollars": ask, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "800.00",
            "close_time": "2026-12-10T13:29:00Z", "title": f"CPI above {x}%", **kw}


def test_kalshi_esemeny_pav_es_idoszak():
    e = {"event_ticker": "KXCPIYOY-26NOV", "series_ticker": "KXCPIYOY", "title": "CPI YoY in November",
         "markets": [_kalshi_m(3.6, "0.40", "0.44"), _kalshi_m(3.5, "0.90", "0.92"), _kalshi_m(3.7, "0.46", "0.50")]}
    es = pe.kalshi_esemeny(e)
    assert es["idoszak"] == "2026-11" and es["pav_sertes"] == 1
    p = {x["piac_id"]: x for x in es["piacok"]}
    assert p["KXCPIYOY-26NOV-T3.6"]["kozep"] == pytest.approx(0.42)                 # a nyers közép marad
    assert p["KXCPIYOY-26NOV-T3.6"]["p_piac"] == pytest.approx(0.45)                # a javított: (0,42 + 0,48) / 2
    assert p["KXCPIYOY-26NOV-T3.7"]["p_piac"] == pytest.approx(0.45)
    assert pe.kalshi_idoszak("KXCBDHUNGARY-26DEC15") == "2026-12"


def test_polymarket_igen_a_neve_szerint_string_json_es_tomb():
    # a kimenetek fordított sorrendben, string-JSON-ként: a bestBid/bestAsk az ELSŐ (NEM) tokené → komplementer
    m = {"slug": "x", "question": "Will annual inflation be 3.7% in November?", "groupItemTitle": "3.7%",
         "outcomes": '["No", "Yes"]', "outcomePrices": '["0.58", "0.42"]', "clobTokenIds": '["111", "222"]',
         "bestBid": 0.56, "bestAsk": 0.60, "active": True, "closed": False}
    p = pe.pm_piac(m)
    assert p["kozep"] == pytest.approx(0.42) and p["bid"] == pytest.approx(0.40) and p["ask"] == pytest.approx(0.44)
    assert p["igen_token"] == "222" and p["kerdes"] == {"tipus": "pontosan", "x": 3.7}
    # tömb-mezők, könyv nélkül: az outcomePrices az IGEN indexén, kimondva, hogy nem közép-ár
    m2 = dict(m, outcomes=["No", "Yes"], outcomePrices=["0.7", "0.3"], bestBid=None, bestAsk=None)
    p2 = pe.pm_piac(m2)
    assert p2["kozep"] == pytest.approx(0.3) and "nem közép" in p2["ar_forras"]
    assert pe.lista('["a","b"]') == ["a", "b"] and pe.lista(["a"]) == ["a"] and pe.lista("") == []


def test_polymarket_sav_cimek():
    assert pe.pm_kerdes("≤3.3%") == {"tipus": "legfeljebb", "x": 3.3}
    assert pe.pm_kerdes("≥4.4%") == {"tipus": "legalabb", "x": 4.4}
    assert pe.pm_kerdes("Will annual inflation be 4.4% or more in November?") == {"tipus": "legalabb", "x": 4.4}
    assert pe.pm_kerdes("3.0–3.2%") == {"tipus": "kozott", "also": 3.0, "felso": 3.2}
    assert pe.pm_kerdes("25 bps decrease") is None
    assert pe.pm_idoszak("November Inflation US - Annual", "2026-12-10T13:30:00Z") == "2026-11"
    assert pe.pm_idoszak("December Inflation US - Annual", "2027-01-13T13:30:00Z") == "2026-12"


# ══ 3. a mi eloszlásunk: frissesség, definíció ═══════════════════════════════════════════════════════════════════

def _palya(**kw):
    hist = [{"period": f"{2024 + (i + 9) // 12}-{(i + 9) % 12 + 1:02d}", "value": round(3.0 + 0.05 * (i % 7), 2)}
            for i in range(24)]
    return {"status": "ok", "source": "FRED CPIAUCNS (CPI-U index)", "method": "SARIMAX", "generated_at":
            "2026-11-23T08:00:00Z", "last_actual": {"period": "2026-09", "value": 3.74}, "history": hist,
            "path": [{"period": "2026-10", "value": 3.8, "low": 3.7, "high": 3.9},
                     {"period": "2026-11", "value": 3.5, "low": 3.0, "high": 4.0}],
            "backtest": {"mae": 0.367, "naive_mae": 0.42, "origins": 12}, **kw}


D_CPI = pe.TERKEP_KALSHI["KXCPIYOY"]


def test_sajat_eloszlas_es_kimondott_okok():
    el = pe.sajat_eloszlas(_palya(), "2026-11", D_CPI, friss_idoszak="2026-09")
    assert el["tipus"] == "normal" and el["also"] == 3.0 and el["naiv"]["h"] >= 1
    assert "elavult" in pe.sajat_eloszlas(_palya(), "2026-11", D_CPI, friss_idoszak="2026-10")["hiba"]
    assert "definíció eltér" in pe.sajat_eloszlas(_palya(source="OECD"), "2026-11", D_CPI, friss_idoszak=None)["hiba"]
    assert "nincs 2027-05" in pe.sajat_eloszlas(_palya(), "2027-05", D_CPI, friss_idoszak=None)["hiba"]


def test_kozolt_idoszak_a_webes_tartalek_nem_kozles():
    assert pe.kozolt_idoszak({"value": 0.4, "period": "2026-11-23", "source_used": "brave_search BLS",
                              "measure": "unverified web figure"}) is None
    assert pe.kozolt_idoszak({"value": 4.1, "period": "2026-10-01", "source_used": "FRED UNRATE"}) == "2026-10"
    assert pe.kozolt_idoszak({"value": 2.0, "period": "2026-Q3", "source_used": "Eurostat"}) == "2026-Q3"


# ══ 4. a képesség-mérleg és a szűrő ═════════════════════════════════════════════════════════════════════════════

K = pe.Konfig()


def test_kepesseg_dontes_okai():
    m = [{"sorozat": "US:inflation", "elorejelzo": "forecast_path", "mertek": "mae", "n": 12, "kulonbseg": -0.13, "p": None}]
    d = pe.kepesseg_dontes(m, "US:inflation", "forecast_path", K)
    assert not d["jogosult"] and "tájékoztató" in d["ok"]
    for rek, ok in (({"n": 3, "kulonbseg": -0.2, "p": 0.1}, "kevés pár"), ({"n": 8, "kulonbseg": 0.05, "p": 0.1}, "nem jobb"),
                    ({"n": 8, "kulonbseg": -0.2, "p": 0.4}, "nem elég biztos")):
        r = [{"sorozat": "S", "elorejelzo": "forecast_path", "mertek": "crps", **rek}]
        d = pe.kepesseg_dontes(r, "S", "forecast_path", K)
        assert not d["jogosult"] and ok in d["ok"]
    jo = [{"sorozat": "S", "elorejelzo": "forecast_path", "mertek": "crps", "n": 8, "kulonbseg": -0.2, "p": 0.05}]
    assert pe.kepesseg_dontes(jo, "S", "forecast_path", K)["jogosult"]
    assert not pe.kepesseg_dontes(jo, "S", "szimulator", K)["jogosult"]      # más előrejelző múltja nem jogosít


def test_merleg_a_szimulator_backtesztbol():
    m = pe.merleg_fajlokbol(pe.ALAP_MERLEG_FAJLOK[:1])
    rek = {r["sorozat"]: r for r in m}
    assert rek["EURHUF=X"]["n"] == 7 and rek["EURHUF=X"]["kulonbseg"] < 0
    assert rek["US:policy_rate"]["n"] == 2
    assert all(r["elorejelzo"] == "szimulator" and r["mertek"] == "crps" for r in m)


def _esemeny(most, *, bid="0.60", ask="0.64", meret="500.00", zaras=None):
    return pe.kalshi_esemeny({"event_ticker": "KXCPIYOY-26NOV", "series_ticker": "KXCPIYOY", "title": "CPI YoY",
                              "markets": [_kalshi_m(3.6, bid, ask, yes_ask_size_fp=meret, yes_bid_size_fp=meret,
                                                    close_time=zaras or (most + timedelta(days=5)).isoformat())]})


def test_szuro_el_kepesseg_likviditas_elozetesseg():
    most = _t("2026-11-25T10:00:00")
    el = pe.eloszlas_savbol(3.0, 4.0, generalt="2026-11-25T08:00:00Z")       # p_mi(> 3,6) ≈ 0,35; piac 0,62 → él ≈ −0,27
    jog = {"jogosult": True, "ok": "x"}
    s = pe.osszevet(_esemeny(most), el, D_CPI, jog, K, most)["piacok"][0]
    assert s["el"] == pytest.approx(0.3503 - 0.62, abs=1e-3) and s["irany"] == "NEM" and s["jelzes"]
    # nincs mért múlt → nincs jelzés
    assert not pe.osszevet(_esemeny(most), el, D_CPI, {"jogosult": False, "ok": "x"}, K, most)["piacok"][0]["jelzes"]
    # kis él → nincs jelzés
    k2 = pe.Konfig(el_kuszob=0.30)
    assert not pe.osszevet(_esemeny(most), el, D_CPI, jog, k2, most)["piacok"][0]["jelzes"]
    # likviditás: széles spread → a jelzés elmarad, de a likviditás nélküli döntés naplózva (érzékenység)
    s = pe.osszevet(_esemeny(most, bid="0.40", ask="0.84"), el, D_CPI, jog, K, most)["piacok"][0]
    assert not s["jelzes"] and s["jelzes_likviditas_nelkul"] and not s["likviditas"]["jo"]
    # a zárás után nincs előzetes összevetés
    ov = pe.osszevet(_esemeny(most, zaras="2026-11-25T09:00:00Z"), el, D_CPI, jog, K, most)
    assert not ov["elozetes"] and not ov["jelzesek"]
    # az előrejelzés későbbi, mint a piaci ár → nem előzetes
    el_kesobb = pe.eloszlas_savbol(3.0, 4.0, generalt="2026-11-25T11:00:00Z")
    assert not pe.osszevet(_esemeny(most), el_kesobb, D_CPI, jog, K, most)["elozetes"]


# ══ 5. a kör: előre rögzítés → csak utána Telegram; feloldás csak előzetes sorra ═════════════════════════════════

def _lekero(most):
    async def lek(url, params):
        if "kalshi" in url:
            if params["series_ticker"] != "KXCPIYOY":
                return {"events": []}
            return {"events": [{"event_ticker": "KXCPIYOY-26NOV", "series_ticker": "KXCPIYOY", "title": "CPI YoY",
                                "markets": [_kalshi_m(3.6, "0.60", "0.64",
                                                      close_time=(most + timedelta(days=5)).isoformat())]}]}
        return []
    return lek


def _hiv(palya=None):
    async def hiv(sz, e, p):
        if e == "forecast_path":
            return json.dumps(palya or _palya(generated_at="2026-11-25T08:00:00Z"))
        if e == "get_macro_indicator":
            return json.dumps({"value": 3.74, "period": "2026-09", "source_used": "FRED CPIAUCNS"})
        raise AssertionError(f"váratlan hívás: {sz}.{e}")
    return hiv


def _mult_feloldasok(nap, n=6, jobb=True):
    """n korábbi, feloldott esemény, ahol a log-pontszámunk jobb (vagy rosszabb) volt a piacénál."""
    for i in range(n):
        nap.hozzafuz("feloldas", f"mult:{i}", {"sorozat": "US:inflation", "idoszak": f"2026-0{i + 1}",
                                               "platform": "kalshi", "esemeny_id": f"KXCPIYOY-26M{i}",
                                               "elore_rogzitve": f"2026-0{i + 1}-10",
                                               "log_mi": -0.5 if jobb else -1.2, "log_piac": -1.0})


def test_elore_rogzites_a_telegram_elott_es_csak_uj_jelzes():
    most = _t("2026-11-25T10:00:00")
    nap = pe.MemoriaNaplo(ora=lambda: most)
    _mult_feloldasok(nap)                                                   # a mért múlt: jobbak vagyunk (p = 2/64)
    uzenetek = []

    async def tg(szoveg):
        assert nap.sorok("elore"), "a Telegram az előre rögzítés ELŐTT ment volna ki"
        uzenetek.append(szoveg)
    k = pe.Konfig(merleg_fajlok=())
    r = _run(pe.kor(_hiv(), lekero=_lekero(most), nap=nap, telegram=tg, most=most, k=k))
    assert r["jelzesek"] == 1 and len(uzenetek) == 1
    assert "nem befektetési tanács" in uzenetek[0] and "Előre rögzítve: #7" in uzenetek[0]
    elore = nap.sorok("elore")[0]["payload"]
    assert elore["kepesseg"]["jogosult"] and elore["osszevetes"]["jelzesek"] == ["KXCPIYOY-26NOV-T3.6"]
    assert pe.lanc_ellenoriz(nap.sorok())["ep"]
    # másnap ugyanaz a jelzés: új napi sor, de Telegram nincs (már elment)
    holnap = most + timedelta(days=1)
    nap._ora = lambda: holnap
    _run(pe.kor(_hiv(), lekero=_lekero(most), nap=nap, telegram=tg, most=holnap, k=k))
    assert len(nap.sorok("elore")) == 2 and len(uzenetek) == 1


def test_naplo_nelkul_es_naplohibanal_nincs_jelzes(monkeypatch):
    most = _t("2026-11-25T10:00:00")
    uzenetek = []

    async def tg(szoveg):
        uzenetek.append(szoveg)
    # a képesség itt átmenne — a napló hiánya / hibája mégis megállítja a jelzést
    monkeypatch.setattr(pe, "kepesseg_dontes", lambda *a, **kw: {"jogosult": True, "ok": "teszt", "rekord": None})
    k = pe.Konfig(merleg_fajlok=())

    class Rossz(pe.MemoriaNaplo):
        def hozzafuz(self, *a, **kw):
            raise RuntimeError("PG nem érhető el")
    for nap in (None, Rossz()):
        r = _run(pe.kor(_hiv(), lekero=_lekero(most), nap=nap, telegram=tg, most=most, k=k))
        e = next(x for x in r["esemenyek"] if x["esemeny_id"] == "KXCPIYOY-26NOV")
        assert r["jelzesek"] == 0 and not uzenetek
        if nap is None:
            assert not any(p.get("jelzes") for p in e["osszevetes"]["piacok"]) and "nincs napló" in e["ok"]
        else:
            assert e["rogzitve"] is False and "a napló hibája" in e["ok"]


def test_feloldas_elso_kozlesbol_es_a_kesoi_sor_kizarva():
    most = _t("2026-11-25T10:00:00")
    nap = pe.MemoriaNaplo(ora=lambda: most)
    k = pe.Konfig(merleg_fajlok=())
    _run(pe.kor(_hiv(), lekero=_lekero(most), nap=nap, most=most, k=k))
    assert len(nap.sorok("elore")) == 1
    ekn = ek.MemoriaNaplo()
    utana = most + timedelta(days=16)
    # az első közlés még nincs → nincs feloldás
    assert _run(pe.felold(nap, None, k, utana, ek_naplo=ekn)) == []
    ekn.rogzit([ek.sor("macro_history:US:cpi", "2026-11", 3.73768, _t("2026-12-10T13:31:00"), "bls")])
    uj = _run(pe.felold(nap, None, k, utana, ek_naplo=ekn))
    assert len(uj) == 1
    f = uj[0]["payload"]
    assert f["y"] == pytest.approx(3.7) and f["piacok"][0]["o"] == 1 and "napló" in f["feloldas"]
    assert f["elore_hash"] == nap.sorok("elore")[0]["sor_hash"]
    # a második futás nem old fel újra
    assert _run(pe.felold(nap, None, k, utana, ek_naplo=ekn)) == []
    # a zárás UTÁN rögzített sor: nem előzetes → kizárva a pontozásból
    nap2 = pe.MemoriaNaplo(ora=lambda: most + timedelta(days=6))            # a rögzítés órája a zárás után jár
    _run(pe.kor(_hiv(), lekero=_lekero(most), nap=nap2, most=most, k=k))
    f2 = _run(pe.felold(nap2, None, k, utana, ek_naplo=ekn))[0]["payload"]
    assert "nem előzetes" in f2["ervenytelen"] and "log_mi" not in f2


def test_lanc_eszreveszi_a_modositast():
    nap = pe.MemoriaNaplo()
    for i in range(3):
        nap.hozzafuz("elore", f"k{i}", {"i": i})
    assert nap.hozzafuz("elore", "k1", {"i": 99}) is None                    # nincs felülírás
    sorok = nap.sorok()
    assert pe.lanc_ellenoriz(sorok)["ep"]
    sorok[1]["payload"] = {"i": 42}
    assert pe.lanc_ellenoriz(sorok) == {"ep": False, "hibas_id": 2, "index": 1}


# ══ 6. pontozás és árnyék-PnL ═════════════════════════════════════════════════════════════════════════════════════

def _elore(platform="kalshi", **kw):
    piacok = [{"piac_id": "T3.6", "kerdes": {"tipus": "nagyobb", "x": 3.6}, "p_mi": 0.30, "p_piac": 0.60, "bid": 0.58,
               "ask": 0.62, "el": -0.30, "jelzes": True, "jelzes_likviditas_nelkul": True},
              {"piac_id": "T3.7", "kerdes": {"tipus": "nagyobb", "x": 3.7}, "p_mi": 0.10, "p_piac": 0.20, "bid": 0.19,
               "ask": 0.21, "el": -0.10, "jelzes": False, "jelzes_likviditas_nelkul": False}]
    return {"platform": platform, "kizarolagos": False, "osszevetes": {"piacok": piacok},
            "eloszlas": {"tipus": "normal", "also": 3.0, "felso": 4.0, "p": 0.8,
                         "naiv": {"also": 3.5, "felso": 3.9, "p": 0.8, "skala": 0.1}}, **kw}


def test_pontozas_kezzel():
    r = pe.pontoz(_elore(), 3.6, K)                    # 3,6 > 3,6 hamis → mindkét „above” NEM
    a, b = r["piacok"]
    assert a["o"] == 0 and a["brier_mi"] == pytest.approx(0.09) and a["brier_piac"] == pytest.approx(0.36)
    assert a["log_mi"] == pytest.approx(math.log(0.7), abs=1e-5) and a["log_piac"] == pytest.approx(math.log(0.4), abs=1e-5)
    # sávok: (−∞, 3,6], (3,6, 3,7], (3,7, ∞): a realizált az első; mi 0,7, piac 0,4
    assert r["sav"] == 0 and r["log_mi"] == pytest.approx(math.log(0.7), abs=1e-4)
    assert r["brier_sav_mi"] == pytest.approx(0.3 ** 2 + 0.2 ** 2 + 0.1 ** 2)
    assert r["crps_mi"] == pytest.approx(sb.crps_sav(3.0, 4.0, 0.8, 3.6), abs=1e-5)
    assert r["crps_naiv"] == pytest.approx(sb.crps_sav(3.5, 3.9, 0.8, 3.6), abs=1e-5)


def test_arnyek_pnl_dijjal_es_spreaddel():
    # NEM-irány (él < 0), o = 0 (a NEM nyert): NEM-ár közép 0,40, spreaden át 1 − bid = 0,42; díj 0,07·p·(1 − p)
    r = pe.pontoz(_elore(), 3.6, K)["piacok"][0]["pnl"]
    assert r["kozepen"] == pytest.approx(1 - 0.40 - 0.07 * 0.40 * 0.60, abs=1e-4)
    assert r["spreaddel"] == pytest.approx(1 - 0.42 - 0.07 * 0.42 * 0.58, abs=1e-4)
    # IGEN-irány, vesztes: −ár − díj
    s = {"el": 0.2, "p_piac": 0.30, "ask": 0.32, "bid": 0.28}
    r = pe.arnyek_pnl("polymarket", s, 0, pe.Konfig(dij_poly=0.0))
    assert r == {"kozepen": -0.30, "spreaddel": -0.32}
    assert "pnl" not in pe.pontoz(_elore(), 3.6, K)["piacok"][1]          # nem jelzés → nincs PnL


def test_padlo_normal_invariansok():
    p = pe.padlo_normal([0.0, 0.0005, 0.9995, 0.0])
    assert sum(p) == pytest.approx(1.0) and min(p) >= pe.P_PADLO - 1e-12


def test_heti_osszesito_esemenyenkent_egyszer():
    nap = pe.MemoriaNaplo(ora=lambda: _t("2026-12-11T00:00:00"))
    for nap_, lm in (("2026-12-08", -2.0), ("2026-12-09", -0.5)):        # ugyanaz az esemény két napon: a késői számít
        nap.hozzafuz("feloldas", f"kalshi:E:{nap_}", {"sorozat": "US:inflation", "idoszak": "2026-11", "platform": "kalshi",
                                                       "esemeny_id": "E", "elore_rogzitve": nap_, "log_mi": lm,
                                                       "log_piac": -1.0, "piacok": []})
    o = pe.heti_osszesito(nap, K, _t("2026-12-11T00:00:00"))
    s = o["sorozatok"]["US:inflation"]
    assert s["n"] == 1 and s["dlog_atlag"] == pytest.approx(0.5) and s["kevés_par"]
    assert "kevés pár" in pe.heti_szoveg(o) and "nem befektetési tanács" in pe.heti_szoveg(o)


# ══ 7. a kapcsoló ═════════════════════════════════════════════════════════════════════════════════════════════════

def test_kapcsolo_ki(monkeypatch):
    monkeypatch.setenv("PIAC_ELTERES", "0")
    hivtak = []

    async def lek(url, params):
        hivtak.append(url)
        return {}
    r = _run(pe.kor(_hiv(), lekero=lek, nap=pe.MemoriaNaplo(), most=_t("2026-11-25T10:00:00")))
    assert r["ki"] and not hivtak
    assert pe.cron_lepes(datetime(2026, 11, 25, 13, 0)) is False


def test_kapcsolo_alapbol_be_es_konfig_envbol(monkeypatch):
    monkeypatch.delenv("PIAC_ELTERES", raising=False)
    assert pe.bekapcsolva()
    monkeypatch.setenv("PIAC_ELTERES_EL_KUSZOB", "0.2")
    monkeypatch.setenv("PIAC_ELTERES_MERCEK", "crps")
    monkeypatch.setenv("PIAC_ELTERES_MIN_N", "nemszam")
    k = pe.konfig()
    assert k.el_kuszob == 0.2 and k.mercek == ("crps",) and k.min_n == 5


# ── a valódi Postgres (csak kérésre: PIAC_ELTERES_TESZT_DSN, eldobható adatbázis) ─────────────────

@pytest.mark.network
@pytest.mark.skipif(not __import__("os").environ.get("PIAC_ELTERES_TESZT_DSN"),
                    reason="PIAC_ELTERES_TESZT_DSN nincs (eldobható PG)")
def test_pg_naplo_hozzafuzes_lanc_es_trigger():
    import os
    psycopg = pytest.importorskip("psycopg")
    n = pe.PgNaplo(os.environ["PIAC_ELTERES_TESZT_DSN"])
    a = n.hozzafuz("elore", "kalshi:E:2026-11-25", {"x": 1, "szoveg": "árnyék"})
    assert a and n.hozzafuz("elore", "kalshi:E:2026-11-25", {"x": 2}) is None     # nincs felülírás
    b = pe.PgNaplo(n.dsn).hozzafuz("feloldas", "kalshi:E:2026-11-25", {"y": 3.7})
    assert b["elozo_hash"] == a["sor_hash"]
    sorok = n.sorok()
    assert pe.lanc_ellenoriz(sorok)["ep"] and [s["tipus"] for s in sorok] == ["elore", "feloldas"]
    with psycopg.connect(n.dsn, autocommit=True) as k:
        with pytest.raises(psycopg.errors.RaiseException):
            k.execute("UPDATE piac_elteres_naplo SET payload = '{}'::jsonb")
        with pytest.raises(psycopg.errors.RaiseException):
            k.execute("DELETE FROM piac_elteres_naplo")
