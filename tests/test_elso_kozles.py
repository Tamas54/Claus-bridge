"""ELSŐ-KÖZLÉS-NAPLÓ ÉS FELOLDÁSI SZABÁLY (elso_kozles.py, 11-23) — hálózat nélkül.

A négy ígéret: (1) az első érték megmarad, a revízió nem írja felül; (2) a pontozás a `first_print`-et használja;
(3) hiányzó első közlésnél kimondott jelölés, nem csendben a mai érték; (4) az as_of-alapvonal nem lát jövőt.
A napló itt memóriában fut (a PgNaplo felülete); a valódi Postgres-próba csak `ELSO_KOZLES_TESZT_DSN`-nel.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from fastmcp import Client, FastMCP

import dontes_szimulator as ds
import elso_kozles as ek
import kulso_mcp
import szim_backteszt as sb


def _run(c):
    return asyncio.run(c)


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _mh(orszag, ind, obs: dict) -> str:
    return json.dumps({"country": orszag, "indicator": ind, "frequency": "M", "source": "BIS",
                       "source_series_id": "M.X.771", "observations": [{"period": p, "value": v} for p, v in obs.items()]})


def _havi(tol=(2014, 1), ig=(2026, 11), ertek=lambda ev, h: round(3.0 + ((ev * 12 + h) % 7) * 0.1, 2)) -> dict:
    ki, (ev, h) = {}, tol
    while (ev, h) <= ig:
        ki[f"{ev}-{h:02d}"] = ertek(ev, h)
        ev, h = ev + (h == 12), h % 12 + 1
    return ki


# ── (1) az első érték megmarad ────────────────────────────────────────────────

def test_az_elso_ertek_megmarad_a_revizio_nem_irja_felul():
    n = ek.MemoriaNaplo()
    elso = ek.kinyer("macro_history", {"country": "HU", "indicator": "cpi"},
                     _mh("HU", "cpi", {"2026-08": 1.3, "2026-09": 1.5}), most=_t("2026-10-10T08:00:00"))
    assert n.rogzit(elso) == 2
    assert n.rogzit(elso) == 0                                     # ugyanaz a válasz még egyszer: semmi új
    rev = ek.kinyer("macro_history", {}, _mh("HU", "cpi", {"2026-08": 1.3, "2026-09": 1.7}),
                    most=_t("2026-11-12T08:00:00"))
    assert n.rogzit(rev) == 1                                      # csak a revízió új sor, a régi marad
    sorok = n.sorok("macro_history:HU:cpi", "2026-09")
    assert [s["ertek"] for s in sorok] == [1.5, 1.7]
    assert ek.valaszt(sorok, "first_print", idoszak="2026-09")["ertek"] == 1.5
    assert ek.valaszt(sorok, "fixed_vintage", idoszak="2026-09", vintage=date(2026, 11, 1))["ertek"] == 1.5
    assert ek.valaszt(sorok, "fixed_vintage", idoszak="2026-09", vintage=date(2026, 11, 20))["ertek"] == 1.7
    # a Bridge KÉSŐN látta (a figyelés az első közlés után indult): az nem első közlés
    keso = ek.kinyer("macro_history", {}, _mh("HU", "cpi", {"2025-01": 4.8}), most=_t("2026-11-12T08:00:00"))
    r = ek.valaszt(keso, "first_print", idoszak="2025-01")
    assert r.get("ertek") is None and "nem első közlés" in r["ok"]
    with pytest.raises(ValueError):
        ek.valaszt(sorok, "legujabb", idoszak="2026-09")


def test_a_kinyero_alakjai():
    assert ek.kinyer("macro_history", {}, '{"country": "HU", "observations": [{"per') == []      # levágott válasz
    assert ek.kinyer("macro_history", {}, {"hiba": "x"}) == []
    assert ek.kinyer("yfinance", {"symbol": "EURHUF=X"}, {"price": 390.0}) == []                  # piaci ár: nem revideálódik
    agg = ek.kinyer("macro_history", {}, json.dumps({"country": "US", "indicator": "cpi", "frequency": "Q",
                                                     "native_frequency": "M",
                                                     "observations": [{"period": "2026-Q2", "value": 3.1}]}))
    assert agg[0]["sorozat"] == "macro_history:US:cpi:Q"                 # aggregált kérés: külön sorozat
    mi = ek.kinyer("get_macro_indicator", {"country": "HU", "indicator": "cpi"},
                   {"country": "HU", "indicator": "cpi", "value": 4.3, "period": "2026-10", "source_used": "ksh",
                    "source_url": "https://ksh.hu/x"})
    assert (mi[0]["sorozat"], mi[0]["idoszak"], mi[0]["forras_url"]) == ("macro_indicator:HU:cpi:ksh", "2026-10",
                                                                         "https://ksh.hu/x")
    fr = ek.kinyer("get_fred_data", {"series_id": "CPIAUCNS", "units": "pc1"},
                   {"series": {"id": "CPIAUCNS"}, "data": [{"date": "2026-09-01", "value": 3.73768},
                                                           {"date": "2026-10-01", "value": None}]})
    assert [(s["sorozat"], s["idoszak"]) for s in fr] == [("fred:CPIAUCNS:pc1", "2026-09-01")]
    assert fr[0]["payload_hash"] == ek.payload_hash("fred:CPIAUCNS:pc1", "2026-09-01", 3.73768)


# ── (2) a pontozás a first_print-et használja ───────────────────────────────────

def _hiv(mai: dict, alfred=None):
    """Hamis nyers hívó: a macro_history a MAI (revideált) sort adja; az `alfred` álszerver vintage-napra."""
    async def hiv(szerver, eszkoz, par, max_kar=None):
        if szerver == "alfred":
            return json.dumps(alfred(par) if alfred else {"hiba": "nincs felvéve (visszajátszás): alfred"})
        if eszkoz == "macro_history":
            return _mh(par["country"], par["indicator"], mai)
        return json.dumps({"hiba": f"nincs: {eszkoz}"})
    return hiv


SAVOS_SOR = {"kulcs": "HU:inflation", "idoszak": "2026-09", "hatarido": "2026-11-14", "ertek": 3.4, "also": 2.8,
             "felso": 4.0, "naiv": False, "hatas": {"valtozas": -0.3}, "dontessel": 3.1, "dontessel_also": 2.4,
             "dontessel_felso": 3.6, "skala": 0.25}


def test_a_pontozas_a_first_printet_hasznalja():
    n = ek.MemoriaNaplo()
    n.rogzit(ek.kinyer("macro_history", {}, _mh("HU", "cpi", {"2026-09": 2.6}), most=_t("2026-10-12T09:00:00")))
    hiv = _hiv({"2026-09": 2.9})                                   # ma: revideálva 2,9
    sor, = _run(sb.gazdasagi_sorok(hiv, {"gazdasag": [dict(SAVOS_SOR)]}, date(2026, 11, 22), naplo=n))
    assert sor["tenyleges"] == 2.6 and sor["tenyleges_mai"] == 2.9
    assert sor["feloldasi_szabaly"] == "first_print" and sor["tenyleges_feloldas"].startswith("first_print: napló")
    assert "tenyleges_jeloles" not in sor
    assert sor["crps_dontessel"] == pytest.approx(sb.crps_sav(2.4, 3.6, 0.8, 2.6), abs=1e-5)
    assert sor["crps_alap"] == pytest.approx(sb.crps_sav(2.8, 4.0, 0.8, 2.6), abs=1e-5)
    # a sor saját szabálya dönt: `final` → a mai érték, kimondva
    vegso, = _run(sb.gazdasagi_sorok(hiv, {"gazdasag": [dict(SAVOS_SOR, feloldasi_szabaly="final")]},
                                     date(2026, 11, 22), naplo=n))
    assert vegso["tenyleges"] == 2.9 and vegso["tenyleges_feloldas"].startswith("final")
    ossz = sb.gazdasagi_osszegzes([dict(sor, esemeny="e1")])
    assert ossz["crps_elso_kozlesen"]["n"] == 1 and ossz["feloldas"]["n_elso_kozles_hianyzik"] == 0


def test_amerikai_sor_elso_kozlese_az_alfredbol():
    """UNRATE 2026-09: az első közlés (10-15-i vintage) 4,3; ma revideálva 4,2. A felezéses keresés a közlés napját
    adja, és a talált első közlés a naplóba is bekerül (a közlés napjával)."""
    lekeresek = []

    def alfred(par):
        lekeresek.append(par["vintage_date"])
        if par["vintage_date"] < "2026-10-15":
            return {"series_id": "UNRATE", "observations": [{"date": "2026-08-01", "value": 4.1}]}
        return {"series_id": "UNRATE", "observations": [{"date": "2026-09-01",
                                                         "value": 4.3 if par["vintage_date"] < "2026-11-07" else 4.2}]}
    n = ek.MemoriaNaplo()
    sor = {"kulcs": "US:unemployment", "idoszak": "2026-09", "hatarido": "2026-11-14"}
    m = _run(sb.gazdasagi_tenyleges(_hiv({"2026-09": 4.2}, alfred), sor, naplo=n, ma=date(2026, 11, 22)))
    assert m["ertek"] == 4.3 and m["mai_ertek"] == 4.2 and m["kozolve"] == "2026-10-15"
    assert m["feloldas"].startswith("first_print: ALFRED UNRATE") and "jeloles" not in m
    assert len(lekeresek) <= 8
    assert [(s["ertek"], s["elso_lattuk"].date()) for s in n.sorok("fred:UNRATE", "2026-09-01")] == \
        [(4.3, date(2026, 10, 15))]
    # másodszorra a napló felel, ALFRED-lekérés nélkül
    lekeresek.clear()
    m2 = _run(sb.gazdasagi_tenyleges(_hiv({"2026-09": 4.2}, alfred), sor, naplo=n, ma=date(2026, 11, 22)))
    assert m2["ertek"] == 4.3 and m2["feloldas"].startswith("first_print: napló (fred:UNRATE") and not lekeresek
    # fixed_vintage: a megadott napon ismert érték
    m3 = _run(sb.gazdasagi_tenyleges(_hiv({"2026-09": 4.2}, alfred),
                                     dict(sor, feloldasi_szabaly="fixed_vintage", vintage="2026-11-10"), naplo=None))
    assert m3["ertek"] == 4.2 and m3["feloldas"].startswith("fixed_vintage: ALFRED")


# ── (3) hiányzó első közlés: kimondva ─────────────────────────────────────────

def test_hianyzo_elso_kozles_kimondott_jelolessel():
    sor, = _run(sb.gazdasagi_sorok(_hiv({"2026-09": 2.9}), {"gazdasag": [dict(SAVOS_SOR)]}, date(2026, 11, 22),
                                   naplo=None))
    assert sor["tenyleges"] == 2.9 and sor["tenyleges_jeloles"] == ek.JELOLES_HIANY
    assert sor["tenyleges_feloldas"] == "a mai (revideált) érték"
    ossz = sb.gazdasagi_osszegzes([dict(sor, esemeny="e1")])
    assert ossz["crps"]["n"] == 1                                          # a fő szám megmarad…
    assert ossz["crps_elso_kozlesen"]["n"] == 0                            # …de első közlésen nincs mért sor
    assert ossz["feloldas"] == {"n_savos": 1, "n_elso_kozles_hianyzik": 1, "jeloles": ek.JELOLES_HIANY,
                                "hianyzik": ["HU:inflation 2026-09"]}
    # amerikai sor, de az ALFRED nincs felvéve (visszajátszás): a hiány oka a lekérés, nem az időszak
    us = _run(sb.gazdasagi_tenyleges(_hiv({"2026-09": 4.2}), {"kulcs": "US:unemployment", "idoszak": "2026-09"},
                                     naplo=None, ma=date(2026, 11, 22)))
    assert us["ertek"] == 4.2 and us["jeloles"] == ek.JELOLES_HIANY and "ALFRED: nem érhető el" in us["jeloles_ok"]
    # az árfolyam és a kamat nem revideálódik: nincs jelölés, de a szabály ott is látszik
    assert ek.JELOLES_HIANY not in json.dumps(sb.NEM_REVIDEALODIK)


# ── (4) az alapvonal nem lát jövőt ────────────────────────────────────────────

AS_OF = date(2026, 9, 10)


def test_az_alapvonal_a_vintage_bol_epul_nem_lat_jovot():
    """A mai sor revideált (a teljes előtörténet +1); a napló az as_of ELŐTT látott értékeket tudja. Az alapvonal a
    naplóból épül; az as_of után látott revízió és az as_of után közölt hónap nem jut be."""
    eredeti = _havi(ig=(2026, 8))
    n = ek.MemoriaNaplo()
    n.rogzit(ek.kinyer("macro_history", {}, _mh("HU", "cpi", {p: v for p, v in eredeti.items() if p <= "2026-07"}),
                       most=_t("2026-08-20T10:00:00")))
    # as_of UTÁN: a 08-as hónap első közlése és a 07-es revíziója — egyik sem látszhat
    n.rogzit(ek.kinyer("macro_history", {}, _mh("HU", "cpi", {"2026-07": 7.7, "2026-08": 8.8}),
                       most=_t("2026-09-15T10:00:00")))
    ma_revidealt = {p: round(v + 1.0, 2) for p, v in _havi().items()}

    class Hivo:                     # a szimulátor Hivo-alakja: (érték, hiba)
        async def __call__(self, szerver, eszkoz, par):
            if eszkoz == "macro_history":
                return json.loads(_mh("HU", "cpi", ma_revidealt)), None
            return None, "nincs"
    proxy = sb.AsOfHivo(Hivo(), AS_OF, date(2026, 11, 20), naplo=n)
    v, hiba = _run(proxy("makronom", "forecast_path", {"country": "HU", "indicator": "inflation"}))
    assert hiba is None and v["vintage"].startswith("elso_kozles-napló")
    assert v["last_actual"] == {"period": "2026-07", "value": eredeti["2026-07"]}        # nem 7,7 és nem +1
    assert all(p["value"] == eredeti["2026-07"] for p in v["path"])
    ism = ek.ismert_ertekek(n.sorok("macro_history:HU:cpi"), AS_OF)
    assert "2026-08" not in ism and ism["2026-07"] == eredeti["2026-07"]
    assert ek.ismert_ertekek(n.sorok("macro_history:HU:cpi"), date(2026, 8, 20)) == {}  # aznap látott: még nem ismert
    # napló nélkül (régi futás, nem amerikai sor): a mai előtörténet, KIMONDVA
    v2, _ = _run(sb.AsOfHivo(Hivo(), AS_OF, date(2026, 11, 20), naplo=None)(
        "makronom", "forecast_path", {"country": "HU", "indicator": "inflation"}))
    assert v2["vintage"].startswith("nincs") and v2["last_actual"]["value"] == ma_revidealt["2026-07"]


def test_az_amerikai_alapvonal_az_alfred_vintage_bol():
    """US cpi: a vintage az as_of ELŐTTI napra (09-09) kér; ami a mai sorban revideált, az a pályába nem kerül."""
    kert = []
    regi = {f"{p}-01": v for p, v in _havi(ig=(2026, 7)).items()}

    class Hivo:
        async def __call__(self, szerver, eszkoz, par):
            if szerver == "alfred":
                kert.append(par)
                return {"series_id": "CPIAUCNS", "observations": [{"date": d, "value": x} for d, x in regi.items()]}, None
            if eszkoz == "macro_history":
                return json.loads(_mh("US", "cpi", {p: v + 1.0 for p, v in _havi().items()})), None
            return None, "nincs"
    v, _ = _run(sb.AsOfHivo(Hivo(), AS_OF, date(2026, 11, 20), naplo=None)(
        "makronom", "forecast_path", {"country": "US", "indicator": "inflation"}))
    assert kert[0]["vintage_date"] == "2026-09-09" and kert[0]["series_id"] == "CPIAUCNS"
    assert kert[0]["transformation"] == "pc1"
    assert v["vintage"] == "ALFRED CPIAUCNS vintage 2026-09-09"
    assert v["last_actual"]["value"] == regi["2026-07-01"]


# ── a gyűjtés: egy közös kinyerő, két szállítási pont ─────────────────────────

makro_szerver = FastMCP("makro")


@makro_szerver.tool
def macro_history(country: str, indicator: str, start: str = "1990") -> str:
    """Hamis macro_history."""
    return _mh(country, indicator, {"2026-09": 1.556445})


def test_a_hook_ki_allasban_semmit_nem_ir_be_allasban_ir(monkeypatch):
    n = ek.MemoriaNaplo()
    monkeypatch.setattr(ek, "naplo", lambda: n)
    monkeypatch.setattr(kulso_mcp, "_kliens", lambda nev: Client(makro_szerver))
    kulso_mcp._LISTAK.clear()
    kulso_mcp._HIBAK.clear()                     # egy korábbi teszt „elérhetetlen” jelzése ne maradjon itt
    monkeypatch.delenv("ELSO_KOZLES_NAPLO", raising=False)

    async def _hiv():
        s = await kulso_mcp.hiv("makronom", "macro_history", {"country": "HU", "indicator": "cpi"})
        await ek.befejez()
        return s
    try:
        assert "1.556445" in _run(_hiv())
        assert n.sorok("macro_history:HU:cpi") == []                 # KI: a kapcsoló nélkül nincs írás
        monkeypatch.setenv("ELSO_KOZLES_NAPLO", "1")
        monkeypatch.setenv("ELSO_KOZLES_PG_DSN", "postgresql://teszt")
        _run(_hiv())
        assert [s["ertek"] for s in n.sorok("macro_history:HU:cpi")] == [1.556445]
    finally:
        kulso_mcp._LISTAK.clear()
        kulso_mcp._HIBAK.clear()


def test_a_rest_ut_is_ugyanabba_a_naploba_ir(monkeypatch):
    import httpx
    import _statdata_client as sd
    n = ek.MemoriaNaplo()
    monkeypatch.setattr(ek, "naplo", lambda: n)
    monkeypatch.setenv("ELSO_KOZLES_NAPLO", "1")
    monkeypatch.setenv("ELSO_KOZLES_PG_DSN", "postgresql://teszt")
    monkeypatch.setattr(sd, "STATDATA_URL", "http://statdata.teszt")
    valasz = {"series": {"id": "UNRATE"}, "data": [{"date": "2026-09-01", "value": 4.2}]}
    atvitel = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True, "result": json.dumps(valasz)}))
    eredeti = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: eredeti(transport=atvitel, **kw))

    async def _hiv():
        r = await sd.get_fred_data(series_id="UNRATE")
        await ek.befejez()
        return r
    _run(_hiv())
    assert [s["ertek"] for s in n.sorok("fred:UNRATE", "2026-09-01")] == [4.2]


def test_az_alfred_alszerver_es_a_csv(monkeypatch):
    async def hamis(par):
        return json.dumps({"series_id": par["series_id"], "observations": []})
    monkeypatch.setattr(ek, "alfred_lekeres", hamis)
    assert json.loads(_run(kulso_mcp.hiv("alfred", "vintage", {"series_id": "UNRATE"})))["series_id"] == "UNRATE"
    assert "alfred" not in kulso_mcp.SZERVEREK                       # a Feldwebel nem látja eszközként
    csv_ = "observation_date,CPIAUCNS_PC1_20261122\n2025-10-01,\n2026-09-01,3.73768\n2026-10-01,.\n"
    assert ek.alfred_csv_ertelmez(csv_) == [{"date": "2026-09-01", "value": 3.73768}]
    with pytest.raises(ValueError):
        ek.alfred_csv_ertelmez("<html>hiba</html>")
    u = ek.alfred_url({"series_id": "UNRATE", "vintage_date": "2026-10-15", "kezdet": "2026-09-01",
                       "veg": "2026-09-01"})
    assert u.startswith(ek.ALFRED_URL) and "vintage_date=2026-10-15" in u and "cosd=2026-09-01" in u
    assert ek.macro_to_fred_datum("2026-Q2") == "2026-04-01" and ek.fred_datum_to_macro("2026-04-01", "Q") == "2026-Q2"


# ── az előrejelzés-sor mezője ─────────────────────────────────────────────────

def test_minden_elorejelzes_sor_kap_feloldasi_szabalyt():
    tabla = [dict(SAVOS_SOR, mutato="infláció (éves, %)", orszag="HU", egyseg="%", modszer="m", ellenorzes="KSH",
                  hatas={"valtozas": -0.3, "csatorna": "x"}, feloldasi_szabaly=ds.FELOLDASI_SZABALY)]
    elore, _ = ds.elorejelzesek_tisztit(ds.gazdasagi_elorejelzesek(tabla), "piac", ma=date(2026, 9, 1),
                                        hatar_napok=120, max_db=8)
    assert elore[0]["feloldasi_szabaly"] == "first_print"
    rossz, _ = ds.elorejelzesek_tisztit([{**ds.gazdasagi_elorejelzesek(tabla)[0], "feloldasi_szabaly": "legujabb"}],
                                        "piac", ma=date(2026, 9, 1), hatar_napok=120, max_db=8)
    assert rossz[0]["feloldasi_szabaly"] == "first_print"
    # a szabály a lánc hash-ében is benne van; a régi, mező nélküli sor hash-e változatlan
    regi = {k: v for k, v in elore[0].items() if k != "feloldasi_szabaly"}
    assert "feloldasi_szabaly" not in ds.elorejelzes_kanon(regi, "sz-0", 1, "h")
    assert ds.elorejelzes_kanon(elore[0], "sz-0", 1, "h") != ds.elorejelzes_kanon(regi, "sz-0", 1, "h")
    alap, _ = ds.alapvonal_sorok([("get_macro_indicator", {"country": "HU", "indicator": "policy_rate"},
                                   {"value": 6.5, "source_used": "mnb", "period": "2026-09-01"})],
                                 date(2026, 11, 20), date(2026, 9, 1))
    assert alap[0]["feloldasi_szabaly"] == "first_print"


# ── a valódi Postgres (csak kérésre: ELSO_KOZLES_TESZT_DSN, eldobható adatbázis) ─────────────────

@pytest.mark.network
@pytest.mark.skipif(not os.environ.get("ELSO_KOZLES_TESZT_DSN"), reason="ELSO_KOZLES_TESZT_DSN nincs (eldobható PG)")
def test_pg_naplo_csak_hozzafuz():
    psycopg = pytest.importorskip("psycopg")
    n = ek.PgNaplo(os.environ["ELSO_KOZLES_TESZT_DSN"])
    s1 = ek.sor("teszt:HU:cpi", "2026-09", 1.5, _t("2026-10-10T08:00:00"), "u")
    assert n.rogzit([s1, s1]) == 1 and ek.PgNaplo(n.dsn).rogzit([s1]) == 0      # ON CONFLICT DO NOTHING
    assert n.rogzit([ek.sor("teszt:HU:cpi", "2026-09", 1.7, _t("2026-11-12T08:00:00"), "u")]) == 1
    assert [r["ertek"] for r in n.sorok("teszt:HU:cpi", "2026-09")] == [1.5, 1.7]
    with psycopg.connect(n.dsn, autocommit=True) as k:
        with pytest.raises(psycopg.errors.RaiseException):
            k.execute("UPDATE elso_kozles SET ertek = 9 WHERE sorozat = 'teszt:HU:cpi'")
        with pytest.raises(psycopg.errors.RaiseException):
            k.execute("DELETE FROM elso_kozles WHERE sorozat = 'teszt:HU:cpi'")
