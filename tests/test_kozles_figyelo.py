"""KÖZLÉS-FIGYELŐ (kozles_figyelo.py, 11-23) — hálózat nélkül: a Makronóm hamis, az óra hamis.

1. A napi terv: a következő 24 óra figyelt közlései, hivatalos időponttal, előrejelzés-pillanatképpel, alapvonallal;
   a hivatalos lista győz a StatData becslése fölött; a már megjelent közlést nem figyeli.
2. Az észlelés az új érték megjelenésekor történik, és nem előtte; az ablak végén lezár.
3. Idempotencia: egy közlés = egy jelzés (újraindulás után is).
4. Az `elso_kozles`-be írás (a B-felület ebből olvas).
5. A sablon szövege (Telegram, X EN/HU, 280 jel).
6. A késés-mérés és a heti összesítő.
7. Kapcsoló KI → semmi.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone

import pytest

import elso_kozles as ek
import kozles_figyelo as kf
import twitter_poszt


def _run(c):
    return asyncio.run(c)


def _t(s: str) -> datetime:
    """'2026-12-08T08:30:00+01:00' → UTC."""
    return datetime.fromisoformat(s).astimezone(timezone.utc)


CFG = kf.konfig()


@pytest.fixture(autouse=True)
def _tiszta(monkeypatch):
    kf._alaphelyzet()
    monkeypatch.delenv("ELSO_KOZLES_PG_DSN", raising=False)
    monkeypatch.delenv("ELSO_KOZLES_NAPLO", raising=False)
    monkeypatch.delenv("KOZLES_FIGYELO", raising=False)
    monkeypatch.delenv("KOZLES_FIGYELO_TERV", raising=False)
    yield
    kf._alaphelyzet()


class Ora:
    def __init__(self, t: datetime):
        self.t = t

    def most(self) -> datetime:
        return self.t

    async def alvas(self, s: float) -> None:
        self.t += timedelta(seconds=s)


def macro(ertek, period, *, url=None, source="Eurostat", **tobb) -> dict:
    return {"value": ertek, "period": period, "period_returned": period, "status": "fresh", "confidence": "official",
            "source_used": source, "source_url": url, **tobb}


KSH = "https://www.ksh.hu/gyorstajekoztatok"
PALYAK = {("HU", "inflation"): [{"period": "2026-11", "value": 2.62, "low": 2.11, "high": 3.13}],
          ("HU", "unemployment"): [{"period": "2026-10", "value": 4.6, "low": 4.5, "high": 4.7}],
          ("DE", "inflation"): [{"period": "2026-11", "value": 2.8, "low": 2.6, "high": 3.1}],
          ("US", "inflation"): [{"period": "2026-11", "value": 3.53, "low": 3.27, "high": 3.78}],
          ("EA", "inflation"): [{"period": "2026-11", "value": 3.9, "low": 3.6, "high": 4.2}]}


class Makronom:
    """Hamis Makronóm: a `kozles` szótár (ország, mutató) → (régi, új, mikor jelenik meg UTC-ben)."""

    def __init__(self, ora: Ora, kozles: dict | None = None, naptar: list | None = None):
        self.ora, self.kozles, self.naptar = ora, kozles or {}, naptar or []
        self.hivasok: list[tuple] = []

    async def __call__(self, eszkoz: str, par: dict) -> dict:
        self.hivasok.append((eszkoz, dict(par)))
        if eszkoz == "get_economic_calendar":
            return {"events": self.naptar}
        if eszkoz == "forecast_path":
            return {"status": "ok", "generated_at": "2026-11-23T20:00:00Z",
                    "path": PALYAK.get((par["country"], par["indicator"]), [])}
        k = (par.get("country"), par.get("indicator")) if eszkoz == "get_macro_indicator" else (eszkoz, par.get("query")
                                                                                             or par.get("series_id"))
        if k not in self.kozles:
            raise kf.KozlesHiba(f"nincs minta: {k}")
        regi, uj, mikor = self.kozles[k]
        return uj if (mikor is not None and self.ora.t >= mikor) else regi

    def db(self, eszkoz: str) -> int:
        return sum(1 for e, _ in self.hivasok if e == eszkoz)


HU_CPI = {("HU", "cpi"): (macro(2.0, "2026-10", url=f"{KSH}/far/far2610.html", source="KSH flash far 2026-10",
                                period_is_fetch_date=True),
                          macro(2.4, "2026-11", url=f"{KSH}/far/far2611.html", source="KSH flash far 2026-11",
                                period_is_fetch_date=True),
                          _t("2026-12-08T08:30:31+01:00"))}


def _terv(most: str, makronom: Makronom, orak: int = 24) -> dict:
    return _run(kf.tervez(most=_t(most), hiv=makronom, cfg=CFG, orak=orak))


def _hu_cpi_esemeny(ora: Ora, makronom: Makronom) -> dict:
    t = _terv("2026-12-08T07:00:00+01:00", makronom)
    [e] = [e for e in t["esemenyek"] if e["sorozat"] == "HU_CPI"]
    return e


class Telegram:
    def __init__(self):
        self.uzenetek: list[str] = []

    async def __call__(self, szoveg: str) -> None:
        self.uzenetek.append(szoveg)


# ── 1. A NAPI TERV ────────────────────────────────────────────────────────────

def test_napi_terv_a_kovetkezo_24_ora_figyelt_kozlesei():
    ora = Ora(_t("2026-11-25T09:00:00+01:00"))
    m = Makronom(ora, {("HU", "unemployment"): (macro(4.6, "2026-09", source="Eurostat une_rt_m"), None, None)},
                 naptar=[{"date": "2026-11-25", "time": "08:30 CET", "indicator": "Hitelintézetek mérlege",
                          "source": "MNB", "region": "HU"},
                         {"date": "2026-11-26", "time": "10:00 ET", "indicator": "US Consumer Sentiment (UMich)",
                          "series_id": "UMCSENT", "source": "FRED"}])
    t = _terv("2026-11-25T09:00:00+01:00", m)
    assert [e["sorozat"] for e in t["esemenyek"]] == ["HU_MUNKANELKULISEG"]        # a nem figyelt naptár-tétel kimarad
    [e] = t["esemenyek"]
    assert e["allapot"] == "tervezett" and e["idoszak"] == "2026-10"
    assert e["tervezett"] == "2026-11-26T07:30:00+00:00"                          # 08:30 Budapest = 07:30 UTC
    assert e["idopont_forras"].startswith("hivatalos: KSH fem2609")
    assert e["elozo"]["ertek"] == 4.6 and e["elozo"]["idoszak"] == "2026-09"
    assert e["elorejelzes"] == {"pont": 4.6, "also": 4.5, "felso": 4.7, "idoszak": "2026-10",
                                "mertek": CFG["sorozatok"][1]["elorejelzes"]["mertek"],
                                "forras": "Makronóm forecast_path", "keszult": "2026-11-23T20:00:00Z"}
    # 7 nap: a Destatis-közlés is benne van, de olvasó nélkül — kimondva, és nincs alapvonal-hívás rá
    t7 = _terv("2026-11-25T09:00:00+01:00", m, orak=24 * 7)
    de = [e for e in t7["esemenyek"] if e["sorozat"] == "DE_CPI"]
    assert de and de[0]["allapot"] == "nincs_olvaso" and "Destatis" in de[0]["megjegyzes"]
    assert de[0]["elorejelzes"]["pont"] == 2.8


def test_a_hivatalos_idopont_gyoz_a_statdata_becslese_folott():
    ora = Ora(_t("2026-12-13T09:00:00+01:00"))
    m = Makronom(ora, {}, naptar=[{"date": "2026-12-14", "time": "08:30 ET", "indicator": "US CPI",
                                   "series_id": "CPIAUCSL", "source": "FRED"}])
    t = _terv("2026-12-13T09:00:00+01:00", m, orak=48)
    assert not [e for e in t["esemenyek"] if e["sorozat"] == "US_CPI"]          # BLS: 12-10, nem 12-14
    assert any("US_CPI: a StatData-naptár 12-14 14:30" in x for x in t["figyelmeztetesek"])
    # ahol a hivatalos lista már nem fedi az ablakot, a StatData becslése jön — kimondva
    t2 = _terv("2027-05-01T09:00:00+02:00", Makronom(ora, {}, naptar=[
        {"date": "2027-05-12", "time": "08:30 ET", "indicator": "US CPI", "series_id": "CPIAUCSL", "source": "FRED"}]),
        orak=24 * 14)
    [us] = [e for e in t2["esemenyek"] if e["sorozat"] == "US_CPI"]
    assert us["idopont_forras"].startswith("StatData-naptár") and us["idoszak"] == "2027-04"
    assert us["tervezett"] == "2027-05-12T12:30:00+00:00"                         # 08:30 EDT


def test_a_mar_megjelent_kozlest_nem_figyeli():
    ora = Ora(_t("2026-12-08T09:00:00+01:00"))
    m = Makronom(ora, {("HU", "cpi"): (HU_CPI[("HU", "cpi")][1], None, None)})     # már a novemberi látszik
    t = _terv("2026-12-08T07:00:00+01:00", m)
    [e] = [e for e in t["esemenyek"] if e["sorozat"] == "HU_CPI"]
    assert e["allapot"] == "korabban_megjelent"


# ── 2. AZ ÉSZLELÉS ────────────────────────────────────────────────────────────

def test_eszleles_az_uj_ertek_megjelenesekor_es_nem_elotte(get_db):
    ora = Ora(_t("2026-12-08T07:00:00+01:00"))
    m = Makronom(ora, dict(HU_CPI))
    e = _hu_cpi_esemeny(ora, m)
    tar, tg, naplo = kf.MemoriaTar(), Telegram(), ek.MemoriaNaplo()
    tar.beszur(e)
    ora.t = _t("2026-12-08T08:27:00+01:00")
    hivas_elotte = m.db("get_macro_indicator")
    v = _run(kf.figyel(e, cfg=CFG, hiv=m, tar=tar, telegram=tg, get_db=get_db, naplo=naplo, most_fn=ora.most,
                       alvas=ora.alvas))
    assert v == "eszlelve"
    sor = tar.esemeny(e["kulcs"])
    # T−1 perctől 25 mp-enként: 08:29:00, :25, :50, 08:30:15 (még régi), 08:30:40 (az új, 08:30:31-kor jelent meg)
    assert m.db("get_macro_indicator") - hivas_elotte == 5
    assert sor["eszleles"] == _t("2026-12-08T08:30:40+01:00").isoformat()
    assert sor["ertek"] == 2.4 and sor["allapot"] == "eszlelve"
    assert sor["hivatalos_ido"] == _t("2026-12-08T08:30:00+01:00").isoformat() and sor["hivatalos_ido_forras"] == "naptár"
    assert sor["keses_mp"] == 40.0
    assert len(tg.uzenetek) == 1


def test_az_ablak_vegen_lezar_jelzes_nelkul(get_db):
    ora = Ora(_t("2026-12-08T07:00:00+01:00"))
    m = Makronom(ora, {("HU", "cpi"): (HU_CPI[("HU", "cpi")][0], None, None)})    # meg sem jelenik
    e = _hu_cpi_esemeny(ora, m)
    tar, tg = kf.MemoriaTar(), Telegram()
    tar.beszur(e)
    ora.t = _t("2026-12-08T08:29:00+01:00")
    assert _run(kf.figyel(e, cfg=CFG, hiv=m, tar=tar, telegram=tg, get_db=get_db, naplo=ek.MemoriaNaplo(),
                          most_fn=ora.most, alvas=ora.alvas)) == "nem_jelent_meg"
    assert ora.t >= _t("2026-12-08T08:50:00+01:00") and ora.t < _t("2026-12-08T08:51:00+01:00")
    assert tar.esemeny(e["kulcs"])["allapot"] == "nem_jelent_meg" and tg.uzenetek == []


def test_potlas_kesve_lattuk_kimondva(get_db):
    ora = Ora(_t("2026-12-08T07:00:00+01:00"))
    m = Makronom(ora, dict(HU_CPI))
    e = _hu_cpi_esemeny(ora, m)
    tar, tg = kf.MemoriaTar(), Telegram()
    tar.beszur(e)                                                    # az ablak kimaradt (pl. újraindulás)
    ora.t = _t("2026-12-08T12:00:00+01:00")
    assert _run(kf.potlas(most=ora.t, cfg=CFG, hiv=m, tar=tar, telegram=tg, get_db=get_db,
                          naplo=ek.MemoriaNaplo())) == [e["kulcs"]]
    sor = tar.esemeny(e["kulcs"])
    assert sor["kesve"] is True and sor["keses_mp"] == 3.5 * 3600
    assert "Késve" in tg.uzenetek[0] and "+3 ó 30 p" in tg.uzenetek[0]


def test_kamatdontes_valtozas_es_valtozatlan(get_db):
    ora = Ora(_t("2026-12-15T10:00:00+01:00"))
    regi = macro(5.5, "2026-12-15", url="https://www.mnb.hu/Jegybanki_alapkamat_alakulasa", source="scrape mnb",
                 decision_date="2026-11-17", period_is_fetch_date=True)
    uj = {**regi, "value": 5.25, "decision_date": "2026-12-15"}
    m = Makronom(ora, {("HU", "policy_rate"): (regi, uj, _t("2026-12-15T14:00:20+01:00"))})
    [e] = _terv("2026-12-15T10:00:00+01:00", m)["esemenyek"]
    assert e["sorozat"] == "HU_MNB" and e["idoszak"] == "2026-12-15" and e["tipus"] == "kamat"
    tar, tg = kf.MemoriaTar(), Telegram()
    tar.beszur(e)
    ora.t = _t("2026-12-15T13:59:00+01:00")
    assert _run(kf.figyel(e, cfg=CFG, hiv=m, tar=tar, telegram=tg, get_db=get_db, naplo=ek.MemoriaNaplo(),
                          most_fn=ora.most, alvas=ora.alvas)) == "eszlelve"
    assert tg.uzenetek[0].startswith("🇭🇺 MNB kamatdöntés: 5,25% (−25 bp; előző 5,50%) · 14:00-kor jelent meg (naptár), "
                                     "14:00:40-kor láttuk (+40 mp).")
    # változatlan kamat: a forrás nem mutat változást → kimondott, igazolatlan állapot, X-tervezet nélkül
    ora2 = Ora(_t("2026-12-15T10:00:00+01:00"))
    m2 = Makronom(ora2, {("HU", "policy_rate"): (regi, None, None)})
    [e2] = _terv("2026-12-15T10:00:00+01:00", m2)["esemenyek"]
    tar2, tg2 = kf.MemoriaTar(), Telegram()
    tar2.beszur(e2)
    ora2.t = _t("2026-12-15T13:59:00+01:00")
    assert _run(kf.figyel(e2, cfg=CFG, hiv=m2, tar=tar2, telegram=tg2, get_db=get_db, naplo=ek.MemoriaNaplo(),
                          most_fn=ora2.most, alvas=ora2.alvas)) == "valtozatlan_nem_igazolt"
    assert len(tg2.uzenetek) == 1 and "nem mutatott változást (5,50%)" in tg2.uzenetek[0]
    assert len(twitter_poszt.fuggok(get_db)) == 2                     # csak a változás két tervezete (EN, HU)


# ── 3. IDEMPOTENCIA ───────────────────────────────────────────────────────────

def test_egy_kozles_egy_jelzes_ujrainditas_utan_is(get_db):
    ora = Ora(_t("2026-12-08T07:00:00+01:00"))
    m = Makronom(ora, dict(HU_CPI))
    e = _hu_cpi_esemeny(ora, m)
    tar, tg = kf.MemoriaTar(), Telegram()
    tar.beszur(e)
    olv = kf.olvasat(kf.sorozat_cfg(CFG, "HU_CPI"), HU_CPI[("HU", "cpi")][1])
    t1 = _t("2026-12-08T08:30:40+01:00")
    assert _run(kf.eszlelt(dict(e), olv, t1, cfg=CFG, tar=tar, telegram=tg, get_db=get_db, naplo=ek.MemoriaNaplo()))
    assert not _run(kf.eszlelt(dict(e), olv, t1 + timedelta(seconds=25), cfg=CFG, tar=tar, telegram=tg, get_db=get_db,
                               naplo=ek.MemoriaNaplo()))
    assert len(tg.uzenetek) == 1
    assert tar.esemeny(e["kulcs"])["eszleles"] == t1.isoformat()                # az első észlelés marad
    assert len(twitter_poszt.fuggok(get_db)) == 2                               # EN + HU, egyszer
    # újraindulás Postgres nélkül: üres memória-tár, de az action_drafts-ban már ott a tervezet → nincs új jelzés
    tar2 = kf.MemoriaTar()
    tar2.beszur(e)
    assert not _run(kf.eszlelt(dict(e), olv, t1 + timedelta(minutes=5), cfg=CFG, tar=tar2, telegram=tg,
                               get_db=get_db, naplo=ek.MemoriaNaplo()))
    assert len(tg.uzenetek) == 1 and len(twitter_poszt.fuggok(get_db)) == 2


# ── 4. AZ ELSO_KOZLES-BE ÍRÁS ÉS A B-FELÜLET ──────────────────────────────────

def test_elso_kozles_naplo_es_a_b_felulet(get_db):
    ora = Ora(_t("2026-12-08T07:00:00+01:00"))
    m = Makronom(ora, dict(HU_CPI))
    e = _hu_cpi_esemeny(ora, m)
    tar, naplo = kf.MemoriaTar(), ek.MemoriaNaplo()
    tar.beszur(e)
    olv = kf.olvasat(kf.sorozat_cfg(CFG, "HU_CPI"), HU_CPI[("HU", "cpi")][1])
    t1 = _t("2026-12-08T08:30:40+01:00")
    _run(kf.eszlelt(e, olv, t1, cfg=CFG, tar=tar, telegram=None, get_db=get_db, naplo=naplo))
    [sor] = naplo.sorok("kozles:HU:cpi", "2026-11")
    assert sor["ertek"] == 2.4 and sor["elso_lattuk"] == t1 and sor["forras_url"].endswith("far2611.html")
    assert kf.tenyleges_ertek("HU", "cpi", "2026-11", naplo=naplo) == {
        "sorozat": "kozles:HU:cpi", "idoszak": "2026-11", "ertek": 2.4, "elso_lattuk": t1.isoformat(),
        "forras_url": f"{KSH}/far/far2611.html"}
    [lz] = kf.lezart_kozlesek(sorozat="HU_CPI", tar=tar)
    assert lz["ertek"] == 2.4 and lz["idoszak"] == "2026-11" and lz["keses_mp"] == 40.0
    assert lz["elorejelzes"]["pont"] == 2.62 and lz["elorejelzes"]["mertek"] == "HICP"
    assert lz["mertek"] == "nemzeti CPI (KSH)" and lz["elozo"]["ertek"] == 2.0
    k = _run(kf.kozelgo_kozlesek(orak=24 * 7, most=_t("2026-11-25T09:00:00+01:00"), hiv=Makronom(ora), cfg=CFG))
    assert [(x["sorozat"], x["olvashato"]) for x in k] == [("HU_MUNKANELKULISEG", True), ("DE_CPI", False),
                                                            ("HU_GDP", True), ("EA_HICP", True)]
    assert k[0]["elorejelzes"]["also"] == 4.5 and k[0]["tervezett"] == "2026-11-26T07:30:00+00:00"


# ── 5. A SABLON ───────────────────────────────────────────────────────────────

def _kesz_esemeny(cfg: dict, **tobb) -> dict:
    e = {"kulcs": "HU_CPI|2026-10|2026-11-11", "sorozat": "HU_CPI", "orszag": "HU", "mutato": "cpi", "tipus": "szint",
         "idoszak": "2026-10", "tervezett": _t("2026-11-11T10:00:00+01:00").isoformat(),
         "mertek": kf.sorozat_cfg(cfg, "HU_CPI")["olvaso"]["mertek"], "ertek": 4.1,
         "elozo": {"ertek": 4.3, "idoszak": "2026-09"},
         "elorejelzes": {"pont": 4.2, "also": 3.9, "felso": 4.5, "mertek": "nemzeti CPI (KSH)"},
         "hivatalos_ido": _t("2026-11-11T10:00:08+01:00").isoformat(), "hivatalos_ido_forras": "forrás",
         "eszleles": _t("2026-11-11T10:00:31+01:00").isoformat(), "keses_mp": 23.0,
         "forras_url": f"{KSH}/far/far2610.html"}
    e.update(tobb)
    return e


def test_a_telegram_sablon_szovege():
    s = kf.sorozat_cfg(CFG, "HU_CPI")
    e = _kesz_esemeny(CFG, konszenzus=4.3)
    assert kf.telegram_szoveg(s, e, tervezetek=[{"nyelv": "en", "draft_id": 7}, {"nyelv": "hu", "draft_id": 8}]) == (
        "🇭🇺 KSH infláció (okt.): 4,1% — várakozás 4,3%, a mi előrejelzésünk 4,2% (sáv 3,9–4,5) · előző 4,3% (szept.) · "
        "meglepetés: −0,2 százalékpont a várakozáshoz képest, a sávunkon belül · 10:00:08-kor jelent meg, "
        "10:00:31-kor láttuk (+23 mp).\n"
        f"{KSH}/far/far2610.html\n"
        "X-tervezet: #7 (en), #8 (hu) — jóváhagyás a kabinban.")
    # eltérő mérték (KSH nemzeti CPI ↔ HICP-pálya): kiírja a mértéket, és nem vet össze
    e2 = _kesz_esemeny(CFG, elorejelzes={"pont": 2.62, "also": 2.11, "felso": 3.13, "mertek": "HICP"})
    t2 = kf.telegram_szoveg(s, e2)
    assert "a mi előrejelzésünk (HICP) 2,6% (sáv 2,1–3,1)" in t2 and "meglepetés" not in t2


def test_az_x_tervezet_szovege_es_hossza():
    s = kf.sorozat_cfg(CFG, "HU_CPI")
    x = kf.x_szovegek(s, _kesz_esemeny(CFG))
    assert x["en"] == ("🇭🇺 Hungary CPI, Oct 2026: 4.1% (Sep 2026: 4.3%). Our forecast: 4.2%, 80% band 3.9–4.5. "
                       "Source: KSH")
    assert x["hu"] == ("🇭🇺 KSH infláció, 2026. október: 4,1% (2026. szeptember: 4,3%). A mi előrejelzésünk: 4,2%, "
                       "80%-os sáv 3,9–4,5. Forrás: KSH")
    # minden figyelt sorozat tervezete a linkkel együtt belefér a 280 jelbe
    for sc in CFG["sorozatok"]:
        e = _kesz_esemeny(CFG, sorozat=sc["kulcs"], tipus=sc.get("tipus", "szint"), mertek="x", ertek=-123.45,
                          konszenzus=-123.4, kiadas="második becslés",
                          elorejelzes={"pont": -123.4, "also": -125.5, "felso": -120.25, "mertek": "nagyon hosszú mérték"},
                          elozo={"ertek": -124.0, "idoszak": "2026-09"})
        for szoveg in kf.x_szovegek(sc, e).values():
            osszes = twitter_poszt.osszerak(szoveg, "https://ec.europa.eu/eurostat/product?code=2-04112026-ap")
            assert twitter_poszt.ellenoriz(osszes) is None, (sc["kulcs"], twitter_poszt.hossz(osszes))


# ── 6. KÉSÉS-MÉRÉS, HETI ÖSSZESÍTŐ, OLVASÓK ───────────────────────────────────

def test_keses_meres_forras_idovel_es_heti_osszesito(get_db):
    ora = Ora(_t("2026-12-01T09:00:00+01:00"))
    regi = {"items": [{"title": "Euro area annual inflation up to 4.0%", "link": "https://ec.europa.eu/x/2-04112026-ap",
                       "pub_date": "2026-11-04T10:00:00Z", "description": "Euro area annual inflation is expected to be "
                       "4.0% in October 2026, up from 3.8% in September according to a flash estimate from Eurostat."}]}
    uj = {"items": [{"title": "Euro area annual inflation down to 3.7%", "link": "https://ec.europa.eu/x/2-01122026-ap",
                     "pub_date": "2026-12-01T10:00:02Z", "description": "Euro area annual inflation is expected to be "
                     "3.7% in November 2026, down from 4.0% in October according to a flash estimate from Eurostat."}]
          + regi["items"]}
    m = Makronom(ora, {("get_flash_releases", "inflation"): (regi, uj, _t("2026-12-01T11:00:02+01:00"))})
    [e] = [x for x in _terv("2026-12-01T09:00:00+01:00", m)["esemenyek"] if x["sorozat"] == "EA_HICP"]
    tar, tg = kf.MemoriaTar(), Telegram()
    tar.beszur(e)
    ora.t = _t("2026-12-01T10:59:00+01:00")
    _run(kf.figyel(e, cfg=CFG, hiv=m, tar=tar, telegram=tg, get_db=get_db, naplo=ek.MemoriaNaplo(), most_fn=ora.most,
                   alvas=ora.alvas))
    sor = tar.esemeny(e["kulcs"])
    # a hivatalos idő a forrásé (Eurostat pub_date 10:00:02Z), nem a naptári 11:00:00
    assert sor["hivatalos_ido"] == "2026-12-01T10:00:02+00:00" and sor["hivatalos_ido_forras"] == "forrás"
    assert sor["eszleles"] == _t("2026-12-01T11:00:15+01:00").isoformat() and sor["keses_mp"] == 13.0
    assert "11:00:02-kor jelent meg, 11:00:15-kor láttuk (+13 mp)" in tg.uzenetek[0]
    assert "meglepetés: −0,2 százalékpont a mi előrejelzésünkhöz képest, a sávunkon belül" in tg.uzenetek[0]
    h = kf.heti_osszesito(tar, most=_t("2026-12-03T12:00:00+01:00"))
    assert h["eszlelt"] == 1 and h["keses_median_mp"] == 13.0 and h["keses_max_mp"] == 13.0
    assert h["kozlesek"][0]["sorozat"] == "EA_HICP" and h["tartos_tar"] is False


def test_olvasok():
    hu = kf.sorozat_cfg(CFG, "HU_CPI")
    assert kf.olvasat(hu, macro(2.0, "2026-11-23", url=f"{KSH}/far/far2610.html", period_is_fetch_date=True)
                      )["idoszak"] == "2026-10"                                  # a KSH-cím hónapja, nem a lekérés napja
    assert kf.olvasat(hu, macro(3.0, "2026-11-23", url="https://www.bls.gov/", period_is_fetch_date=True)) is None
    assert kf.olvasat(hu, {**macro(3.0, "2026-10"), "confidence": "web_unverified"}) is None
    gdp = kf.sorozat_cfg(CFG, "HU_GDP")
    assert kf.olvasat(gdp, macro(1.5, "x", url=f"{KSH}/gdp/gdp2609.html"))["idoszak"] == "2026-Q3"
    us = kf.sorozat_cfg(CFG, "US_CPI")
    o = kf.olvasat(us, {"series": {"id": "CPIAUCNS", "last_updated": "2026-11-10 08:12:46-06"},
                        "data": [{"date": "2026-10-01", "value": None}, {"date": "2026-09-01", "value": 3.74}]})
    assert o["idoszak"] == "2026-09" and o["ertek"] == 3.74                     # az üres (null) megfigyelés kimarad
    ea = kf.sorozat_cfg(CFG, "EA_GDP")
    o = kf.olvasat(ea, {"items": [{"link": "u", "pub_date": "2026-10-30T10:00:00Z", "description":
                                   "In the third quarter of 2026, seasonally adjusted GDP increased by 0.4% in the euro "
                                   "area and by 0.5% in the EU, compared with the previous quarter, according to a "
                                   "preliminary flash estimate published by Eurostat"}]})
    assert (o["ertek"], o["idoszak"], o["hivatalos_ido"]) == (0.4, "2026-Q3", "2026-10-30T10:00:00+00:00")


# ── 7. KAPCSOLÓ ÉS ÜTEMEZŐ ────────────────────────────────────────────────────

def test_kapcsolo_ki_semmi_nem_fut(monkeypatch, get_db):
    monkeypatch.setenv("KOZLES_FIGYELO", "0")
    m, tg = Makronom(Ora(_t("2026-11-26T08:29:00+01:00"))), Telegram()
    kf.beallit(get_db=get_db, telegram=tg, hiv=m, tar=kf.MemoriaTar(), cfg=CFG)

    async def lep():
        n = kf.cron_lepes(_t("2026-11-26T08:29:00+01:00").astimezone(kf._zona(kf.BP)))
        await asyncio.sleep(0)
        return n
    assert _run(lep()) == 0 and m.hivasok == [] and tg.uzenetek == [] and not kf._FUTO


def test_utemezo_terv_induleskor_es_ablak_inditas(monkeypatch, get_db):
    ora = Ora(_t("2026-11-26T06:00:00+01:00"))
    m = Makronom(ora, {("HU", "unemployment"): (macro(4.6, "2026-09", source="Eurostat une_rt_m"), None, None)})
    tg, tar = Telegram(), kf.MemoriaTar()
    kf.beallit(get_db=get_db, telegram=tg, hiv=m, tar=tar, cfg=CFG)
    indult = []

    async def hamis_figyel(e, **kw):
        indult.append(e["kulcs"])
        kw["tar"].frissit(e["kulcs"], {"allapot": "eszlelve"})
        return "eszlelve"
    monkeypatch.setattr(kf, "figyel", hamis_figyel)

    async def kivar():
        while kf._FUTO:
            await asyncio.gather(*list(kf._FUTO))

    async def menet():
        bpz = kf._zona(kf.BP)
        monkeypatch.setattr(kf, "_most", lambda: _t("2026-11-26T06:00:00+01:00"))
        assert kf.cron_lepes(_t("2026-11-26T06:00:00+01:00").astimezone(bpz)) == 1       # induláskor: terv
        await kivar()                    # a tervkör azonnal elindítja a figyelőt (az ablakig alszik)
        assert indult == ["HU_MUNKANELKULISEG|2026-10|2026-11-26"]
        assert kf.cron_lepes(_t("2026-11-26T06:01:00+01:00").astimezone(bpz)) == 0       # se terv, se új figyelő
        assert kf.cron_lepes(_t("2026-11-26T07:00:00+01:00").astimezone(bpz)) == 1       # a 07:00-s tervkör
        await kivar()
    _run(menet())
    assert indult == ["HU_MUNKANELKULISEG|2026-10|2026-11-26"]                         # egyszer
    assert len(tg.uzenetek) == 1 and tg.uzenetek[0].startswith("📅 Közlés-figyelő — a következő 24 óra:\n"
                                                               "• 11-26 08:30 🇭🇺 KSH munkanélküliség (okt.)")
