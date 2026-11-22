"""DÖNTÉS-SZIMULÁTOR BACKTESZT (szim_backteszt.py) — hálózat nélkül.

Amit ezek a tesztek őriznek:
  1. a kötött modell: a kapu PONTOSAN a kért Claude-modellt hívja, szerveroldali tartalék nélkül; a nem-Claude modell a
     SiliconFlow-útra megy; a futás a VALÓBAN futott modelleket rögzíti, és a tisztaság-kapu mindegyikre kérdez;
  2. az időpont-hű adat: as_of-módban egyetlen motor sem lát as_of utáni adatot (sajtó: dátumos get_news, az aznapi és
     későbbi cikk kiesik; piac: közlési késéssel számolt period, a mai idősor kivágva, forecast nem fut, yfinance csak
     az as_of előtti sorokkal; jog: dátumos állapot, a későbbi állapotok és döntések kivágva), a szivárgás kimondva,
     és az utólagos jóslat NEM kerül a DELPHOI-láncba;
  3. a pontozó: Brier, 50%-os és leave-one-out bázisarány-alapvonal, a hiány kimondva; bináris, küszöbös és
     intervallumos előrejelzés; a CLEAN sor külön; ugyanez az éles `szim:*` ledger-sorokra a határidő után;
  4. a felvétel/visszajátszás (mindkét út ugyanazt az adatot látja), a GT-szabályok, az as_of-panel és a kérdés-lépés.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

import dontes_szimulator as ds
import llm_kapu
import modell_cutoff
import szim_backteszt as sb
from tests.test_dontes_szimulator import TITKOS_LEPES, VALASZOK, hamis_panel

AS_OF = date(2026, 9, 10)


def _run(c):
    return asyncio.run(c)


# ── 1. kötött modell ─────────────────────────────────────────────────────────

def _uzenet(model="claude-opus-5-5"):
    from anthropic.types.beta import BetaMessage
    return BetaMessage.model_validate({
        "id": "m", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 100, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}})


def test_a_kapu_a_kotott_modellt_hivja_tartalek_nelkul(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt")
    monkeypatch.setattr(llm_kapu, "_KOLTES", {"honap": "", "usd": 0.0, "hivasok": 0, "szintenkent": {}})
    kuldott = []

    async def kuld(kw):
        kuldott.append(kw)
        return _uzenet(kw["model"])
    monkeypatch.setattr(llm_kapu, "_kuld", kuld)
    data, err, _ = _run(llm_kapu.chat({"messages": [{"role": "user", "content": "x"}]}, purpose="szimulator/terv",
                                      modell="claude-opus-5-5"))
    assert err is None and data["model"] == "claude-opus-5-5"
    assert kuldott[0]["model"] == "claude-opus-5-5" and "fallbacks" not in kuldott[0]
    assert kuldott[0]["output_config"] == {"effort": "high"}          # az Opus saját effortja
    # ismeretlen modellt a kapu nem hív
    data, err, _ = _run(llm_kapu.chat({"messages": [{"role": "user", "content": "x"}]}, modell="claude-3-opus"))
    assert data is None and err["code"] == "ismeretlen_modell" and len(kuldott) == 1
    # kötött modell nélkül minden a régi: a kapu választ, tartalékkal
    _run(llm_kapu.chat({"messages": [{"role": "user", "content": "x"}]}, purpose="szimulator/terv"))
    assert kuldott[-1]["model"] == "claude-sonnet-5-5" and kuldott[-1]["fallbacks"] == "default"


def test_a_nem_claude_modell_a_siliconflow_utra_megy(monkeypatch):
    hivas = []

    async def sf(payload, modell):
        hivas.append(modell)
        return {"model": modell, "choices": [{"message": {"content": "{}"}}], "usage": {"koltes_usd": 0.001}}, None, []

    async def tilos(*a, **k):
        raise AssertionError("az Anthropic-kapu nem hívódhat")
    monkeypatch.setattr(sb, "sf_chat", sf)
    monkeypatch.setattr(llm_kapu, "chat", tilos)
    data, err, _ = _run(sb.kotott_chat({"messages": []}, purpose="x", modell="tencent/Hy3"))
    assert hivas == ["tencent/Hy3"] and data["model"] == "tencent/Hy3"
    assert sb.becsles_usd("tencent/Hy3", 3000, 1000) < sb.becsles_usd("claude-opus-5-5", 3000, 1000)


# ── hamis motorok as_of-ra ───────────────────────────────────────────────────

def _cikk(forras, cim, nap, nyelv="hu"):
    return {"title": cim, "lead": f"{cim} — lead.", "url": f"https://pelda.hu/{abs(hash((cim, nap))) % 10000}",
            "source_name": forras, "language": nyelv, "published_at": f"{nap}T10:00:00+02:00", "lean": "center",
            "trust_tier": 2, "spheres": ["hu_press"]}


def hamis_asof_mcp(naplo: list):
    async def hiv(szerver, eszkoz, par, max_kar=None):
        naplo.append((szerver, eszkoz, dict(par)))
        if szerver == "echolot":
            assert eszkoz == "get_news", f"as_of-módban tilos: echolot.{eszkoz}"
            nap = par["date"]
            forras = par["source"]
            # a lap aznapi cikkei + egy JÖVŐBELI cikk, amit a szűrőnek ki kell dobnia
            return json.dumps({"articles": [
                _cikk(forras, f"{forras}: kamatemelés jöhet ({nap})", nap),
                _cikk(forras, f"{forras}: más téma {nap}", nap),
                _cikk(forras, f"{forras}: JÖVŐ — euró bevezetése megtörtént", AS_OF.isoformat()),
                _cikk("Tagesspiegel", "nem a Spiegel", nap, "de")]})
        if szerver == "makronom":
            assert eszkoz not in ("forecast", "forecast_path"), "a forecast as_of-módban nem fut"
            if eszkoz == "get_macro_indicator":
                return json.dumps({"country": par["country"], "indicator": par["indicator"], "value": 3.1,
                                   "period": par.get("period"), "status": "historical", "source_used": "KSH",
                                   "time_series": [{"period": "2026-11", "value": 9.9}],
                                   "all_attempts": [{"available_to": "2026-11-22"}]})
            if eszkoz == "yfinance":
                assert par["action"] == "history"
                return json.dumps({"symbol": par["symbol"], "data": [
                    {"date": "2026-09-08", "close": 390.0}, {"date": "2026-09-09", "close": 391.0},
                    {"date": "2026-09-10", "close": 999.0}]})      # a bet.hu-út a végnapot is adja → ki kell esnie
            if eszkoz == "macro_history":
                return json.dumps(_mh(par["indicator"]))
        if szerver == "lexonomics":
            if par.get("scope") == "decisions":
                return json.dumps({"decision_hits": [{"title": "régi", "decided": "2020-01-01", "url": "u1"},
                                                     {"title": "JÖVŐBELI", "decided": "2026-10-01", "url": "u2"}]})
            if eszkoz == "provision":
                return json.dumps({"path": "x", "text": "szöveg", "state": {"state_date": "2020-01-01",
                                                                             "valid_to": "2026-10-15"},
                                   "states": [{"state_date": "2026-10-15"}], "record": {"record_to": "2026-11-01"}})
            return json.dumps({"exact": [], "text_hits": [], "acts_by_title": [{"act": "a", "last_state": "2026-10-15"}]})
        return json.dumps({"hiba": f"ismeretlen: {szerver}.{eszkoz}"})
    return hiv


@pytest.fixture
def kotott_kapu(monkeypatch):
    """Hamis kötött-modellű kapu: a VALASZOK cél szerint, a kért modell néven válaszol."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt")
    monkeypatch.setenv("LEXONOMICS_MCP_KEY", "nxk_teszt")
    hivasok = []

    async def kotott(payload, *, purpose, modell):
        hivasok.append((purpose, modell, json.loads(json.dumps(payload))))
        if purpose == sb.KERDES_CEL:
            obj = {"valaszok": [{"id": "q1", "valoszinuseg": 1.7}, {"id": "q2", "valoszinuseg": 0.3, "indok": "piac"},
                                {"id": "idegen", "valoszinuseg": 0.5}]}
        else:
            obj = VALASZOK[purpose]
        return {"model": modell, "choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}],
                "usage": {"koltes_usd": 0.002}}, None, []

    async def tilos(*a, **k):
        raise AssertionError("kötött modellnél a kapu szintválasztása nem futhat")
    monkeypatch.setattr(sb, "kotott_chat", kotott)
    monkeypatch.setattr(llm_kapu, "chat", tilos)
    return hivasok


def _mh(indikator, *, jovo=9.9):
    """Hamis macro_history: havi sor 2014-től 2026-11-ig; az as_of (09-10) utáni hónapok értéke `jovo` (szivárgás-jel)."""
    obs, ev, h = [], 2014, 1
    while (ev, h) <= (2026, 11):
        per = f"{ev}-{h:02d}"
        obs.append({"period": per, "value": jovo if per >= "2026-08" else round(3.0 + ((ev * 12 + h) % 7) * 0.1, 2)})
        ev, h = (ev + (h == 12), h % 12 + 1)
    return {"country": "HU", "indicator": indikator, "frequency": "M", "source": "KSH", "observations": obs}


def _asof_futtat(get_db, naplo, modellek=None, teljes=True):
    return _run(ds.futtat(TITKOS_LEPES, "2 év", get_db=get_db, hiv=hamis_asof_mcp(naplo), panel_fn=hamis_panel,
                          studio=False, echolot_koz_s=0.0, as_of=AS_OF, modellek=modellek or sb.UTAK["hy3"],
                          teljes=teljes))


# ── 2. időpont-hű adat ───────────────────────────────────────────────────────

def test_asof_futas_semmi_as_of_utani_adatot_nem_lat(get_db, kotott_kapu):
    naplo = []
    r = _asof_futtat(get_db, naplo)
    assert r["as_of"] == "2026-09-10" and r["modellek_kert"]["sajto"] == "tencent/Hy3"
    # sajtó: csak dátumos get_news, minden nap as_of előtt; a jövőbeli cikk sehol
    echo = [p for sz, e, p in naplo if sz == "echolot"]
    assert echo and all(p["date"] < "2026-09-10" for p in echo)
    assert {p["date"] for p in echo} == {"2026-09-09", "2026-09-08", "2026-09-07"}
    sajto = json.dumps(r["sajto"]["adat"], ensure_ascii=False)
    assert "JÖVŐ" not in sajto and "nem a Spiegel" not in sajto
    telex = next(l for l in r["sajto"]["adat"]["lapok"] if l["lap"] == "Telex")
    assert telex["profil"]["lean"] == "center" and telex["profil"]["cikk_naponta"] == 2.0
    # a terv kulcsszava (euró bevezetés) nincs az as_of előtti cikkekben → stílusminta jön, nem témába vágó cikk
    assert telex["temaba_vago_cikkek"] == [] and len(telex["friss_cimek_stilushoz"]) == 4
    # piac: period közlési késéssel, a mai idősor kivágva; forecast nem futott; árfolyam csak as_of előtt
    makro = [p for sz, e, p in naplo if e == "get_macro_indicator"]
    assert {p["indicator"]: p["period"] for p in makro}["cpi"] == "2026-07"
    assert {p["indicator"]: p["period"] for p in makro}["policy_rate"] == "2026-09-09"
    piac = json.dumps(r["piac"]["adat"], ensure_ascii=False)
    assert "time_series" not in piac and "9.9" not in piac and "999" not in piac and "2026-11-22" not in piac
    assert any("forecast" in h and "backteszt" in h for h in r["piac"]["adat"]["hianyok"])
    # a v2 gazdasági alapvonala as_of-hű: a pálya az utolsó as_of-kor közölt értékből (2026-07), az árfolyam az utolsó
    # as_of előtti záróár; a modell hatása erre épül
    alap = {a["kulcs"]: a for a in r["piac"]["adat"]["alapvonal"]}
    assert alap["HU:inflation"]["utolso_teny"]["period"] == "2026-07" and alap["HU:inflation"]["ertek"] != 9.9
    assert alap["HU:inflation"]["also"] < alap["HU:inflation"]["ertek"] < alap["HU:inflation"]["felso"]
    assert alap["EURHUF=X"]["ertek"] == 391.0 and alap["HU:policy_rate"]["naiv"]
    infl = next(t for t in r["gazdasag"] if t["kulcs"] == "HU:inflation")
    assert infl["dontessel"] == pytest.approx(infl["ertek"] + 0.4)
    # jog: dátumos állapot, a későbbi állapotok, a record és a jövőbeli döntés kivágva
    lex = [p for sz, e, p in naplo if sz == "lexonomics"]
    assert all(p.get("date") == "2026-09-09" or p.get("scope") == "decisions" for p in lex)
    jog = json.dumps(r["jog"]["adat"], ensure_ascii=False)
    assert "JÖVŐBELI" not in jog and "2026-10-15" not in jog and "record_to" not in jog
    # a szivárgás kimondva, motoronként
    assert r["sajto"]["adat"]["szivargas"] and r["piac"]["adat"]["szivargas"] and r["jog"]["adat"]["szivargas"]
    # utólagos jóslat NEM kerül a DELPHOI-láncba
    conn = get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM delphoi_nowcast_ledger").fetchone()[0] == 0
    except Exception as e:  # noqa: BLE001 — ha a tábla sem jött létre, az is jó
        assert "no such table" in str(e)
    finally:
        conn.close()
    assert any("nem kerültek a DELPHOI-naplóba" in f for f in r["figyelmeztetesek"])
    # a motorok a „mai” dátumként az as_of-ot kapják
    terv_prompt = next(p for c, m, p in kotott_kapu if c == "szimulator/terv")
    assert "Mai dátum: 2026-09-10" in terv_prompt["messages"][0]["content"]


def test_a_kotott_modellek_a_lepesekhez_es_a_futott_modell_rogzitve(get_db, kotott_kapu, monkeypatch):
    r = _asof_futtat(get_db, [], modellek=sb.UTAK["claude"])
    hivott = {c: m for c, m, _ in kotott_kapu}
    assert hivott["szimulator/terv"] == "claude-sonnet-5-5" and hivott["szimulator/piac"] == "claude-sonnet-5-5"
    assert r["terv_info"]["kert_modell"] == "claude-sonnet-5-5" and r["terv_info"]["modell"] == "claude-sonnet-5-5"
    modellek = sb.hasznalt_modellek(r, {"modell": "claude-sonnet-5-5"})
    assert modellek == ["claude-sonnet-5-5", "tencent/Hy3"]      # a (hamis) panel modellje is
    modellek = modellek + ["claude-opus-5-5"]
    # a kapu: amíg a Claude-sor hiányzik, ISMERETLEN; kitöltve a pontozáskor újraszámol
    assert sb.tisztasag(modellek, {"as_of": "2026-09-10"})["tisztasag"] == "ISMERETLEN"
    monkeypatch.setitem(modell_cutoff.CUTOFF, "claude-sonnet-5-5", {"cutoff": "2026-06", "tiszta_tol": "2026-08"})
    monkeypatch.setitem(modell_cutoff.CUTOFF, "claude-opus-5-5", {"cutoff": "2026-06", "tiszta_tol": "2026-09"})
    assert sb.tisztasag(modellek, {"as_of": "2026-09-10"})["tisztasag"] == "CLEAN"
    assert sb.tisztasag(modellek, {"as_of": "2026-08-25"})["tisztasag"] == "GRAY"
    assert sb.tisztasag(modellek, {"as_of": "2026-06-24"})["tisztasag"] == "CONTAMINATED"


def test_asof_period_kozlesi_keses():
    assert sb.asof_period("cpi", date(2026, 9, 10)) == "2026-07"
    assert sb.asof_period("unemployment", date(2026, 1, 5)) == "2025-11"
    assert sb.asof_period("policy_rate", date(2026, 9, 10)) == "2026-09-09"
    assert sb.asof_period("gdp_growth", date(2026, 9, 10)) == "2026-Q2"        # 06-30 + 60 nap < 09-10
    assert sb.asof_period("gdp_growth", date(2026, 8, 20)) == "2026-Q1"


def test_szurok():
    as_of = date(2026, 9, 10)
    v = sb.makro_szur({"value": 5.5, "period": "2026-09-09", "effective_from": "2026-08-26",
                       "time_series": [1], "cross_check": {"available_to": "2026-11-11"}}, as_of, "2026-09-09")
    assert v == {"value": 5.5, "period": "2026-09-09", "effective_from": "2026-08-26"}
    assert sb.makro_szur({"value": 2.5, "effective_from": "2026-09-16"}, as_of, "2026-09-09").get("hiba")
    yf = sb.yf_szur({"symbol": "X", "data": [{"date": "2026-08-01", "close": 1.0}, {"date": "2026-09-09", "close": 2.0},
                                             {"date": "2026-09-10", "close": 9.0}]}, as_of)
    assert yf["price"] == 2.0 and yf["last_trade_at"] == "2026-09-09" and yf["52w_high"] == 2.0
    lx = sb.lex_szur({"state": {"valid_to": "2026-12-01"}, "states": [1], "record": 1,
                      "acts_by_title": [{"last_state": "2026-10-01", "act": "a"}]}, as_of)
    assert lx == {"state": {"valid_to": None}, "acts_by_title": [{"act": "a"}]}


def test_tema_illeszkedes_toldalekkal():
    assert sb.tema_illeszkedik("Kamatemelést jelentett be az EKB", "EKB kamatemelés")
    assert not sb.tema_illeszkedik("Időjárás", "EKB kamatemelés")
    assert sb.tema_illeszkedik("Autópálya-koncessziót bírál Vitézy", "autópálya koncesszió feljelentés")


# ── 3. pontozó ───────────────────────────────────────────────────────────────

def test_brier_es_kimenetel_tipusok():
    assert sb.brier(0.8, 1) == pytest.approx(0.04) and sb.brier(0.8, 0) == pytest.approx(0.64)
    assert sb.kimenetel({"tipus": "intervallum", "also": 3.2, "felso": 4.1}, 3.2) == 1
    assert sb.kimenetel({"tipus": "intervallum", "also": 3.2, "felso": 4.1}, 4.2) == 0
    assert sb.kimenetel({"tipus": "intervallum", "also": None, "felso": 4.1}, -9) == 1
    assert sb.kimenetel({"tipus": "kuszob", "irany": ">", "kuszob": 3.8}, 3.8) == 0
    assert sb.kimenetel({"tipus": "kuszob", "irany": ">=", "kuszob": 3.8}, 3.8) == 1
    assert sb.kimenetel({"tipus": "binaris", "o": True}) == 1
    assert sb.kimenetel({"tipus": "kuszob", "irany": ">", "kuszob": 1}, None) is None


def test_kuszob_szoveg_ertelmezese():
    assert sb.kuszob_ertelmez("> 3,8%") == {"tipus": "kuszob", "irany": ">", "kuszob": 3.8}
    assert sb.kuszob_ertelmez("≤ 2.5") == {"tipus": "kuszob", "irany": "<=", "kuszob": 2.5}
    assert sb.kuszob_ertelmez("3,2–4,1%") == {"tipus": "intervallum", "also": 3.2, "felso": 4.1}
    assert sb.kuszob_ertelmez("a mutató 4,1 és 3,2 között") == {"tipus": "intervallum", "also": 3.2, "felso": 4.1}
    assert sb.kuszob_ertelmez("vezető anyag") is None


def test_pontozas_alapvonal_hiany_es_clean_kulon():
    sorok = [{"id": "a", "terulet": "piac", "p": 0.9, "o": 1, "tisztasag": "CLEAN"},
             {"id": "b", "terulet": "piac", "p": 0.2, "o": 0, "tisztasag": "CLEAN"},
             {"id": "c", "terulet": "sajto", "p": None, "o": 1, "tisztasag": "CLEAN"},
             {"id": "d", "terulet": "sajto", "p": 0.5, "o": None, "tisztasag": "CLEAN"},
             {"id": "e", "terulet": "sajto", "p": 0.7, "o": 0, "tisztasag": "ISMERETLEN"}]
    p = sb.pontoz(sorok)
    c = p["csak_clean"]
    assert c["n_kerdes"] == 3 and c["n_valaszolt"] == 2 and c["hianyzo"] == ["c"] and c["n_gt_nelkul"] == 1
    assert c["brier"] == pytest.approx((0.01 + 0.04) / 2)
    assert c["brier_alap_50"] == 0.25
    # leave-one-out: a-nál a többi (b=0, c=1) aránya 0,5; b-nél (a=1, c=1) 1,0
    assert c["brier_alap_bazis"] == pytest.approx(((0.5 - 1) ** 2 + (1.0 - 0) ** 2) / 2)
    assert c["brier_hiany_05"] == pytest.approx((0.01 + 0.04 + 0.25) / 3)
    assert c["skill_50"] == pytest.approx(1 - 0.025 / 0.25, abs=1e-3)
    assert set(p["tisztasag_szerint"]) == {"CLEAN", "ISMERETLEN"}
    assert p["tisztasag_szerint"]["ISMERETLEN"]["brier"] == pytest.approx(0.49)
    assert p["osszesen"]["n_valaszolt"] == 3


def test_elo_ledger_sorok_pontozasa_hatarido_utan(get_db, monkeypatch):
    """Az éles szim:* sorok: a valószínűség a láncból, a kimenetel a megadott értékből — bináris, küszöbös és
    intervallumos előrejelzés UGYANAZZAL a pontozóval; a nem esedékes és a kimenetel nélküli sor kimondva."""
    ma = date(2026, 11, 22)
    futas = "sz-0badcafe"
    elore = [{"terulet": "piac", "allitas": "KSH CPI 3,8% fölött", "kuszob": "> 3,8%", "hatarido": "2026-10-31",
              "valoszinuseg": 0.7, "ellenorzes": "KSH", "metrika": "cpi"},
             {"terulet": "piac", "allitas": "EUR/HUF a sávban", "also": 380.0, "felso": 395.0, "kuszob": "",
              "hatarido": "2026-11-01", "valoszinuseg": 0.8, "ellenorzes": "MNB", "metrika": "eurhuf"},
             {"terulet": "jog", "allitas": "módosítják", "kuszob": "", "hatarido": "2026-11-15", "valoszinuseg": 0.3,
              "ellenorzes": "Közlöny", "metrika": "x"},
             {"terulet": "sajto", "allitas": "nyitott", "kuszob": "", "hatarido": "2026-11-20", "valoszinuseg": 0.6,
              "ellenorzes": "Echolot", "metrika": "x"},
             {"terulet": "sajto", "allitas": "jövőbeli", "kuszob": "", "hatarido": "2027-03-01", "valoszinuseg": 0.5,
              "ellenorzes": "Echolot", "metrika": "x"}]
    elore = ds.naploba(get_db, elore, futas=futas, orszag="HU", lepes_hash="h")
    ds._nyom(get_db, futas, "adat", json.dumps({"futas": futas, "lepes_hash": "h", "elorejelzesek": elore},
                                               ensure_ascii=False))
    kulcs = [e["naplo"]["entity_key"] for e in elore]
    ki = sb.elo_pontozas(get_db, {kulcs[0]: {"ertek": 4.1}, kulcs[1]: {"ertek": 401.2}, kulcs[2]: {"o": 0}}, ma=ma)
    assert ki["pontozott"] == 3 and ki["nem_esedekes"] == 1 and ki["nyitott_esedekes"] == [kulcs[3]]
    assert ki["naplo_ellenorzes"][futas] is True
    o = ki["pontszam"]["osszesen"]
    assert o["brier"] == pytest.approx(((0.7 - 1) ** 2 + (0.8 - 0) ** 2 + (0.3 - 0) ** 2) / 3, abs=1e-4)


# ── 4. felvétel, GT, panel, kérdés-lépés ─────────────────────────────────────

def test_felvetel_es_visszajatszas(tmp_path, monkeypatch):
    import kulso_mcp
    hivasok = []

    async def hiv(szerver, eszkoz, par, max_kar=None):
        hivasok.append(eszkoz)
        return json.dumps({"ok": eszkoz})
    monkeypatch.setattr(kulso_mcp, "hiv", hiv)
    fajl = tmp_path / "felvetel.json.gz"
    f = sb.Felvevo(fajl)
    assert _run(f("makronom", "yfinance", {"symbol": "X"})) == '{"ok": "yfinance"}'
    _run(f("makronom", "yfinance", {"symbol": "X"}))
    assert hivasok == ["yfinance"]                       # a második ugyanaz: felvételből
    f.ment()
    vissza = sb.Felvevo(fajl, eles=False)
    assert _run(vissza("makronom", "yfinance", {"symbol": "X"})) == '{"ok": "yfinance"}'
    assert "nincs felvéve" in _run(vissza("makronom", "yfinance", {"symbol": "Y"}))
    assert hivasok == ["yfinance"]


def _gt_hiv():
    async def hiv(szerver, eszkoz, par, max_kar=None):
        if eszkoz == "yfinance":
            return json.dumps({"data": [{"date": "2026-09-09", "close": 100.0}, {"date": "2026-09-10", "close": 150.0},
                                        {"date": "2026-09-24", "close": 100.5}, {"date": "2026-09-25", "close": 999.0}]})
        if eszkoz == "get_macro_indicator":
            return json.dumps({"value": {"2026-09-17": 2.5, "2026-10-30": 2.5}[par["period"]]})
        if eszkoz == "get_news":
            nap = par["date"]
            return json.dumps({"articles": [_cikk(par["source"], f"EKB kamatemelés {nap}", nap),
                                            _cikk(par["source"], f"EKB: Lagarde szerint {nap}", nap),
                                            _cikk(par["source"], "Időjárás", nap)]})
    return hiv


def test_gt_szabalyok():
    es = {"id": "e", "as_of": "2026-09-10"}
    hiv = _gt_hiv()
    irany = _run(sb.gt_felold(hiv, {"szabaly": {"modszer": "arfolyam_irany", "symbol": "EURUSD=X",
                                                "cel_datum": "2026-09-24", "irany": "fel"}}, es))
    assert irany["o"] == 1 and irany["reszlet"]["alap"]["date"] == "2026-09-09" and irany["ertek"] == 0.5
    sav = _run(sb.gt_felold(hiv, {"szabaly": {"modszer": "arfolyam_sav", "symbol": "EURUSD=X",
                                              "cel_datum": "2026-09-24", "sav_szazalek": 0.4}}, es))
    assert sav["o"] == 0
    kam = _run(sb.gt_felold(hiv, {"szabaly": {"modszer": "kamat_osszevetes", "orszag": "EA", "irany": "magasabb",
                                              "referencia_datum": "2026-09-17", "cel_datum": "2026-10-30"}}, es))
    assert kam["o"] == 0
    sajto = _run(sb.gt_felold(hiv, {"szabaly": {"modszer": "sajto_cikkszam", "lapok": ["telex"], "legalabb": 4,
                                                "napok": ["2026-09-10", "2026-09-11"], "regex": "EKB"}}, es))
    assert sajto["ertek"] == 4 and sajto["o"] == 1


def test_asof_panel_injektalt_korpusszal():
    import numpy as np

    async def chat(prompt):
        assert "Friss hírfolyam-címek: Kamat" in prompt
        return "Tetszik is, meg nem is."

    async def embed(szovegek):
        return [list(np.ones(4) * (i + 1)) for i, _ in enumerate(szovegek)]
    korp = {"context": "Friss hírfolyam-címek: Kamat · Forint", "corpus_hash": "abc", "as_of": "2026-09-10"}
    r = _run(sb.asof_panel("Az MNB kamatot csökkent.", "HU", korpusz=korp, n=30, k=1, chat_fn=chat, embed_fn=embed))
    assert r["ok"] and r["eredmeny"]["panel"]["corpus_hash"] == "abc"
    assert r["eredmeny"]["panel"]["model_id"].startswith("tencent/Hy3|") and r["eredmeny"]["overall_score"] is not None
    kiv = ds.panel_kivonat(r)
    assert kiv["ok"] and kiv["modell"].startswith("tencent/Hy3")


def test_kerdes_lepes_ervenyesit_es_kimondja_a_hianyt(kotott_kapu):
    r = {"as_of": "2026-09-10", "lepes": "x", "piac": {"adat": {"adatok": []}}}
    kerdesek = [{"id": "q1", "terulet": "piac", "kerdes": "a", "hatarido": "2026-09-24"},
                {"id": "q2", "terulet": "piac", "kerdes": "b", "hatarido": "2026-09-24"},
                {"id": "q3", "terulet": "sajto", "kerdes": "c", "hatarido": "2026-09-12"}]
    ki, info = _run(sb.kerdes_lepes(r, {}, kerdesek, modell="tencent/Hy3"))
    assert set(ki) == {"q2"}                     # q1: 1,7 nem valószínűség; az idegen azonosító kimarad
    assert ki["q2"] == {"p": 0.3, "indok": "piac"} and info["hianyzo_valasz"] == ["q1", "q3"]
    assert info["modell"] == "tencent/Hy3"


def test_az_elotoltes_kulcsa_azonos_a_sajto_motoreval(tmp_path):
    """Az előtöltött (felvett) napi lapadat a sajtó-motor és a panel-korpusz hívásaira PONTOSAN illeszkedik: a
    visszajátszó felvevő élő hívás nélkül kiszolgálja a teljes as_of-sajtóadatot."""
    f = sb.Felvevo(tmp_path / "f.json.gz", eles=False)
    for lap in ds.LAPOK:
        for nap in sb.napok_elotte(AS_OF, sb.SAJTO_NAPOK):
            if not sb.napi_parameterek(lap, nap):          # a közmédiának nincs saját forrásneve
                continue
            f.tar[sb.Felvevo.kulcs("echolot", "get_news", sb.napi_parameterek(lap, nap))] = {
                "valasz": json.dumps({"articles": [_cikk(lap["forras"], f"{lap['nev']} cím {nap}", nap)]})}
    hivo = ds.Hivo(f, echolot_koz_s=0.0)
    terv = ds.terv_tisztit({"kereses": {"hu": "cím", "en": "cím", "de": "cím"}}, "x" * 20)
    adat = _run(sb.sajto_adat(terv, hivo, AS_OF))
    assert not [h for h in adat["hianyok"] if "nincs felvéve" in h]
    assert any("Közmédia" in h and "nincs saját forrásneve" in h for h in adat["hianyok"])
    korp = _run(sb.panel_korpusz(f, AS_OF))
    assert korp["n_cim"] > 0 and "Telex cím" in korp["context"]


# ── v2: gazdasági mag ────────────────────────────────────────────────────────

def test_asof_palya_naiv_sav_es_kozlesi_keses():
    v = sb.asof_palya(_mh("cpi"), "cpi", AS_OF, date(2026, 11, 20))
    assert v["status"] == "ok" and v["last_actual"]["period"] == "2026-07"
    assert [p["period"] for p in v["path"]] == ["2026-08", "2026-09", "2026-10"]          # a horizont végéig
    assert all(p["value"] == v["last_actual"]["value"] and p["low"] <= p["value"] <= p["high"] for p in v["path"])
    assert v["path"][0]["high"] - v["path"][0]["low"] <= v["path"][-1]["high"] - v["path"][-1]["low"] + 1e-9
    assert sb.asof_palya({"observations": []}, "cpi", AS_OF, date(2026, 11, 20))["status"] == "missing"


def test_motorok_es_horizont():
    es = {"kerdesek": [{"terulet": "piac"}, {"terulet": "sajto"}]}
    assert sb.esemeny_motorjai(es) == {"sajto"}
    assert sb.esemeny_motorjai({"kerdesek": [{"terulet": "piac"}]}) == set()
    assert sb.horizont_nap(date(2026, 7, 21)) == 90 and sb.horizont_nap(date(2026, 10, 28)) == 23


def test_csak_a_kert_motor_fut(get_db, kotott_kapu):
    """teljes={"sajto"}: a gazdasági mag mellett csak a sajtó fut, a jog nem (a backteszt így futtat)."""
    naplo = []
    r = _asof_futtat(get_db, naplo, teljes={"sajto"})
    assert r["sajto"]["eredmeny"] and r["jog"]["info"] == {"nem_kert": True}
    assert not [x for x in naplo if x[0] == "lexonomics"]
    assert "szimulator/jog" not in {c for c, _, _ in kotott_kapu}


def _gazd_hiv():
    async def hiv(szerver, eszkoz, par, max_kar=None):
        if eszkoz == "macro_history":
            return json.dumps({"observations": [{"period": "2026-09", "value": 2.9}]})
        if eszkoz == "get_macro_indicator":
            return json.dumps({"value": 5.5})
        if eszkoz == "yfinance":
            return json.dumps({"data": [{"date": "2026-11-18", "close": 385.0}, {"date": "2026-11-21", "close": 1.0}]})
    return hiv


def test_gazdasagi_sorok_es_szabad_feloldas():
    futas = {"gazdasag": [
        {"kulcs": "HU:inflation", "idoszak": "2026-09", "hatarido": "2026-11-14", "ertek": 3.4, "also": 2.8,
         "felso": 4.0, "naiv": False, "hatas": {"valtozas": -0.3}, "dontessel": 3.1, "dontessel_also": 2.4,
         "dontessel_felso": 3.6},
        {"kulcs": "EURHUF=X", "idoszak": "2026-11-19", "hatarido": "2026-11-19", "ertek": 392.0, "also": None,
         "felso": None, "naiv": True, "hatas": {"valtozas": -4.0}, "dontessel": 388.0, "dontessel_also": 380.0,
         "dontessel_felso": 384.0},
        {"kulcs": "HU:policy_rate", "idoszak": "2027-01-10", "hatarido": "2027-01-10", "ertek": 5.5, "naiv": True}]}
    sorok = _run(sb.gazdasagi_sorok(_gazd_hiv(), futas, date(2026, 11, 22)))
    infl, fx, kamat = sorok
    assert infl["tenyleges"] == 2.9 and infl["o_dontessel"] == 1 and infl["o_alap"] == 1
    assert infl["hiba_dontessel"] == pytest.approx(0.2) and infl["hiba_alap"] == pytest.approx(0.5)
    assert fx["tenyleges"] == 385.0 and fx["o_dontessel"] == 0 and "o_alap" not in fx    # naiv: nincs alap-sáv
    assert kamat["allapot"] == "nem esedékes"
    e = {"allitas": "Az MNB alapkamata 5,5% fölött lesz", "metrika": "alapkamat", "kuszob": "> 5,5%",
         "hatarido": "2026-11-01", "valoszinuseg": 0.3}
    fe = _run(sb.szabad_feloldas(_gazd_hiv(), e, "HU", date(2026, 11, 22)))
    assert fe["allapot"] == "mérve" and fe["o"] == 0 and fe["sor"]["kulcs"] == "HU:policy_rate"
    e2 = {"allitas": "Az infláció 3% alatt", "metrika": "KSH infláció", "kuszob": "< 3", "hatarido": "2026-11-15",
          "valoszinuseg": 0.6}
    fe2 = _run(sb.szabad_feloldas(_gazd_hiv(), e2, "HU", date(2026, 11, 22)))
    assert fe2["sor"]["idoszak"] == "2026-09" and fe2["o"] == 1          # a 11-15-ig megjelent utolsó hónap
    assert _run(sb.szabad_feloldas(_gazd_hiv(), {"allitas": "valami", "kuszob": "", "hatarido": "2026-10-01"},
                                   "HU", date(2026, 11, 22)))["allapot"] == "feloldhatatlan"
