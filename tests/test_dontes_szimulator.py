"""DÖNTÉS-SZIMULÁTOR (dontes_szimulator.py) — hálózat nélkül.

Amit ezek a tesztek őriznek:
  1. a lépések szintje: minden Sonnet 5.5 (az Opus-lépés 11-22-én kimaradt: „túl drága”) — a kapu dönt;
  2. a futásonkénti költségkeret KIKÉNYSZERÍTETT: ami nem fér bele, nem hívódik, és a kimenet kimondja;
  3. alapból GAZDASÁGI mód (terv + panel + gazdasági előrejelzés + összegzés), teljes módban sajtó és jog is;
     a gazdasági alapvonal determinisztikus, a hatás intervallumos előrejelzésként a naplóba kerül;
  4. a mérhető előrejelzések a DELPHOI hash-láncába kerülnek (append-only, lánc ép), a lépés szövege NEM, és az
     utólagos átírás kiderül (naplo_ellenorzes);
  5. a terv fehérlistás (a modell nem hívathat tetszőleges eszközt), a hibás előrejelzés kimarad, kimondva;
  6. a DELPHOI-panel kreditje: pontosan a hiányt írjuk jóvá; időtúllépésnél a job failed + refund;
  7. a bekötés: Feldwebel-eszköz (nyugta, aztán az eredmény), MCP-eszköz (zárt ajtó, saját példány), tudásbázis-kör.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import dontes_szimulator as ds
import llm_kapu
from plugins import delphoi

TITKOS_LEPES = "Magyarország bejelenti, hogy 2030. január 1-jével bevezeti az eurót — PRIVAT-JEL-5521"


def _run(c):
    return asyncio.run(c)


def _jovo(nap: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=nap)).isoformat()


# ── hamis motorok ────────────────────────────────────────────────────────────

def _cikk(forras, cim, nyelv="hu"):
    return {"title": cim, "lead": f"{cim} — lead.", "url": f"https://pelda.hu/{abs(hash(cim)) % 10000}",
            "source_name": forras, "language": nyelv, "published_at": "2026-11-19T10:00:00+00:00"}


def hamis_mcp(naplo: list | None = None, *, studio_ok: bool = True):
    async def hiv(szerver, eszkoz, par, max_kar=None):
        if naplo is not None:
            naplo.append((szerver, eszkoz, dict(par)))
        if szerver == "echolot":
            if eszkoz == "search_news":
                ny = par.get("language")
                lista = {"hu": [_cikk("Telex", "Euró: itt a dátum"), _cikk("Magyar Nemzet", "Feladjuk a forintot?"),
                                _cikk("Index (X)", "Euró 2030"), _cikk("Portfolio", "Mi lesz a hitelekkel?")],
                         "en": [_cikk("BBC News", "Hungary sets euro date", "en")],
                         "de": [_cikk("DER SPIEGEL", "Ungarn will den Euro", "de"),
                                _cikk("Tagesspiegel", "Nicht Spiegel", "de")]}[ny]
                return json.dumps({"items" if ny == "hu" else "articles": lista})
            if eszkoz == "source_profile":
                return json.dumps({"profiles": {par["source"]: {"articles": 300, "lean": "center", "trust_tier": 2,
                                                                "dominant_frame": "economic"}}})
            if eszkoz == "get_news":
                return json.dumps({"articles": [_cikk(par["source"], f"{par['source']} friss címe")]})
            return json.dumps({"status": "empty", "by_sphere": {}, "note": "F1 classifier has not run yet"})
        if szerver == "makronom":
            if eszkoz == "get_macro_indicator":
                return json.dumps({"country": par["country"], "indicator": par["indicator"], "value": 2.0,
                                   "unit": "annual % change (YoY)", "period": "2026-10",
                                   "source_used": "KSH flash", "all_attempts": ["x"] * 50})
            if eszkoz == "forecast":
                return json.dumps({"ultimate_forecast": 2.8, "scenarios": {"pessimistic": 1.8, "realistic": 2.8,
                                                                         "optimistic": 3.8},
                                   "imf_all_years": {str(y): 1.0 for y in range(1980, 2030)}})
            if eszkoz == "forecast_path":
                return json.dumps({"status": "ok", "path": [{"period": "2027-01", "value": 3.1, "low": 2.2,
                                                             "high": 4.0}], "history": [1] * 36})
            return json.dumps({"symbol": par.get("symbol"), "price": 372.79, "52w_high": 399.5, "52w_low": 348.5})
        if szerver == "lexonomics":
            if eszkoz == "provision":
                return json.dumps({"path": "alaptorveny/K", "text": "Magyarország törvényes fizetőeszköze a forint.",
                                   "state": {"source_url": "https://njt.jog.gov.hu/jogszabaly/2011-4301-02-00",
                                             "state_date": "2023-01-01"}})
            if par.get("scope") == "decisions":
                return json.dumps({"decision_hits": [{"title": "ABH 2008", "number": "ABH 2008",
                                                      "url": "https://media.alkotmanybirosag.hu/x.pdf",
                                                      "snippet": "euró bevezetéséig"}]})
            return json.dumps({"exact": [], "text_hits": [{"path": "tv/2013/139/0004.md", "citation": "Mnb. tv.",
                                                           "label": "4. §", "snippet": "árstabilitás",
                                                           "state": {"source_url": "https://njt.jog.gov.hu/x",
                                                                     "state_date": "2025-01-01"}}]})
        if szerver == "studio":
            if eszkoz == "files":
                return json.dumps({"file_id": "uj.docx", "edit_url": "https://studio/uj"})
            if eszkoz == "wordcraft":
                if not studio_ok:
                    return json.dumps({"hiba": "Studio-hiba"})
                return json.dumps({"file_id": "kesz.docx", "edit_url": "https://studio/app/file/kesz.docx/edit",
                                   "name": "Döntés-szimulátor.docx", "steps": [{"ok": True}],
                                   "check": {"status": "needs_check"}})
        return json.dumps({"hiba": f"ismeretlen: {szerver}.{eszkoz}"})
    return hiv


def _elore(allitas, nap=200, p=0.7):
    return {"allitas": allitas, "metrika": "m", "kuszob": "> 1", "hatarido": _jovo(nap), "valoszinuseg": p,
            "ellenorzes": "KSH", "alap": "forecast"}


VALASZOK = {
    "szimulator/terv": {"rovid_cim": "Euró 2030", "orszag": "HU",
                        "kereses": {"hu": "euró bevezetés", "en": "Hungary euro", "de": "Ungarn Euro"},
                        "makro": [{"country": "HU", "indicator": "cpi"}, {"country": "HU", "indicator": "rm -rf"}],
                        "elorejelzes": [{"country": "HU", "indicator": "inflation"}],
                        "piaci": ["EURHUF=X", "rossz szimbólum!!"],
                        "jog": {"hivatkozasok": ["Alaptörvény K) cikk"], "kifejezesek": ["euró bevezetése"],
                                "dontesek": ["euró bevezetése"]}},
    "szimulator/sajto": {"lapok": [{"lap": l["nev"], "cim": f"{l['nev']} címe", "cim_hu": f"{l['nev']} címe",
                                    "lead": "lead", "keretezes": "gazdasági", "belekotes": "2030", "hangnem": "semleges",
                                    "alap": [], "bizonyossag": "közepes"} for l in ds.LAPOK],
                         "kozos_minta": "Egyetértés a dátumban.", "legsebezhetobb_mondat": "2030. január 1-jével",
                         "elorejelzesek": [_elore("A Telex 30 napon belül vezető anyagban ír róla", 30, 0.8)]},
    "szimulator/piac": {"kiindulas": [{"mutato": "infláció", "ertek": "2,0%", "idoszak": "2026-10",
                                       "forras": "get_macro_indicator / KSH flash"}],
                        "hatasok": [{"terulet": "infláció", "irany": "fel", "becsles": "a 3,8%-os pesszimista ágig",
                                     "bizonytalansag": "1,8–3,8%", "forras": "forecast", "modszer": "forgatókönyv"}],
                        "nem_modellezheto": ["az átváltási árfolyam"],
                        "hatas_szamszeru": [
                            {"kulcs": "HU:inflation", "valtozas": 0.4, "also": 0.1, "felso": 0.8,
                             "csatorna": "árfolyam-áttétel", "alap": "analógia: Szlovákia 2009", "bizonyossag": "közepes"},
                            {"kulcs": "EURHUF=X", "valtozas": -6.0, "also": -15.0, "felso": 4.0,
                             "csatorna": "kockázati prémium", "alap": "52 hetes sáv", "bizonyossag": "alacsony"},
                            {"kulcs": "HU:gdp", "valtozas": "sok", "also": 0, "felso": 1},
                            {"kulcs": "nincs-ilyen", "valtozas": 1, "also": 0, "felso": 2}],
                        "elorejelzesek": [_elore("A KSH éves inflációja 2027-ben 3,8% alatt marad", 300, 0.8),
                                          {"allitas": "rossz dátum", "hatarido": "jövőre", "valoszinuseg": 0.5,
                                           "ellenorzes": "x"}]},
    "szimulator/jog": {"utkozesek": [{"szabaly": "Alaptörvény K) cikk", "reszlet": "fizetőeszköze a forint",
                                      "mi_utkozik": "az euró", "mit_kell_modositani": "a K) cikk",
                                      "forras": "https://njt.jog.gov.hu/jogszabaly/2011-4301-02-00",
                                      "allapot_datum": "2023-01-01"}],
                       "dontesek": [], "eljaras": "kétharmados alaptörvény-módosítás", "nem_ellenorzott": [],
                       "elorejelzesek": [_elore("Az Alaptörvény K) cikkét 2027 végéig módosítják", 360, 0.3)]},
    "szimulator/osszegzes": {"osszegzes": "Vegyes fogadtatás.", "fo_megallapitasok": ["jog: kétharmad kell"],
                             "tarsadalom_ertelmezes": "A jobboldali média-étrendűek rosszabbul fogadják.",
                             "bizonytalansag": "Az árfolyam.",
                             "kockazati_terkep": [{"kockazat": "árfolyam-spekuláció", "terulet": "gazdaság",
                                                   "valoszinuseg": "közepes", "sulyossag": "magas",
                                                   "jel": "yfinance 52 hetes sáv"}],
                             "modositasok": [{"javaslat": "árfolyamsáv előre", "mit_csokkent": "spekuláció",
                                              "ara": "kisebb mozgástér"}],
                             "elorejelzesek": [_elore("Egy reprezentatív kutatásban a támogatás 50% fölött", 150, 0.6),
                                               _elore("múltbeli", -5, 0.5)]},
}
MODELLEK: dict = {}


@pytest.fixture
def kapu(monkeypatch):
    """Hamis Anthropic-kapu: célonként a fenti válasz, a hívást és a payloadot feljegyzi; minden hívás 0,01 $."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt-TITOK")
    hivasok = []

    async def chat(payload, purpose=""):
        hivasok.append({"purpose": purpose, "payload": json.loads(json.dumps(payload))})
        return {"model": MODELLEK.get(purpose, "claude-sonnet-5-5"),
                "choices": [{"message": {"content": "```json\n" + json.dumps(VALASZOK[purpose], ensure_ascii=False)
                                         + "\n```"}, "finish_reason": "stop"}],
                "usage": {"koltes_usd": 0.01}}, None, []
    monkeypatch.setattr(llm_kapu, "chat", chat)
    return hivasok


@pytest.fixture
def kulcsok(monkeypatch):
    monkeypatch.setenv("LEXONOMICS_MCP_KEY", "nxk_teszt")
    monkeypatch.setenv("STUDIO_MCP_KEY", "studio_teszt")


async def hamis_panel(lepes, orszag):
    return {"ok": True, "job_id": "dlph-teszt", "kredit": 2, "feltoltes": 0, "n": 60, "orszag": orszag,
            "eredmeny": {"n": 60, "overall_score": 2.6, "baseline": {"value": 3.0},
                         "segments": [{"segment": "jobboldali", "n": 13, "score": 2.1, "vs_baseline": -0.9}],
                         "breakdowns": {"age": [{"group": "18-29", "n": 9, "score": 3.4, "vs_baseline": 0.4}]},
                         "panel": {"n_completed": 60, "model_id": "tencent/Hy3|non-think"},
                         "disclaimer": "Szintetikus panel relatív jelzése."}}


def _futtat(get_db, **kw):
    alap = dict(get_db=get_db, hiv=hamis_mcp(kw.pop("naplo", None)), panel_fn=hamis_panel, echolot_koz_s=0.0)
    alap.update(kw)
    return _run(ds.futtat(TITKOS_LEPES, "2 év", **alap))


# ── 1. szintek, keret, segédek ──────────────────────────────────────────────

def test_minden_lepes_sonnet_nincs_opus():
    szintek = {nev: llm_kapu.szint_valaszt({"messages": [{"role": "user", "content": "x" * 20_000}]}, cel)[0]
               for nev, cel in ds.CELOK.items()}
    assert "kockazat" not in szintek and set(szintek.values()) == {"kozep"}
    assert llm_kapu.SZINTEK["kozep"]["modell"] == "claude-sonnet-5-5"


def test_keret_foglal_konyvel_es_kimond():
    k = ds.Keret(0.30)
    assert k.foglal(0.2, "a") and not k.foglal(0.2, "b")
    k.lezar(0.2, 0.05, "a")
    assert k.foglal(0.2, "c")
    assert k.allapot()["kimaradt"] == ["b"] and k.allapot()["usd"] == 0.05
    # a becslés a kapu tényleges kimeneti plafonjával számol (legrosszabb eset)
    assert ds.becsles_usd("okos", 0, 100) == pytest.approx(llm_kapu.KIMENET_ALSO * 20.0 / 1_000_000)


def test_horizont_napok():
    ma = date(2026, 11, 22)
    assert ds.horizont_napok("12 hónap", ma) == 365
    assert ds.horizont_napok("2 év", ma) == 730
    assert ds.horizont_napok("6 hét", ma) == 42
    assert ds.horizont_napok("2030", ma) == (date(2030, 12, 31) - ma).days
    assert ds.horizont_napok("", ma) == 365 and ds.horizont_napok("50 év", ma) == 3653


def test_json_ki_turi_a_kodblokkot_es_a_zaro_vesszot():
    assert ds.json_ki('```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}
    assert ds.json_ki("Íme: {\"b\": 1} kész") == {"b": 1}
    assert ds.json_ki("nincs json") is None and ds.json_ki("[1]") is None


def test_elorejelzesek_tisztitasa_kimondja_a_kimaradtat():
    ma = datetime.now(timezone.utc).date()
    jo, ki = ds.elorejelzesek_tisztit(
        [_elore("jó", 100, 1.2), _elore("jó2", 100, 0.5), {"allitas": "x", "hatarido": _jovo(10), "valoszinuseg": 0.5},
         _elore("túl messze", 900, 0.5)], "piac", ma=ma, hatar_napok=365)
    assert [e["allitas"] for e in jo] == ["jó2"]
    assert len(ki) == 2 and any("0 és 1" in x for x in ki) and any("ellenőrizhető" in x for x in ki)
    # a motoronkénti plafon: a negyedik már nem is kerül sorra
    jo, _ = ds.elorejelzesek_tisztit([_elore(str(i), 100, 0.5) for i in range(6)], "jog", ma=ma, hatar_napok=365)
    assert len(jo) == ds.MAX_ELOREJELZES_MOTORONKENT


def test_a_terv_feherlistas_es_kiegeszul():
    t = ds.terv_tisztit(VALASZOK["szimulator/terv"], TITKOS_LEPES)
    assert {"country": "HU", "indicator": "rm -rf"} not in t["makro"]
    assert {m["indicator"] for m in t["makro"]} >= {"cpi", "policy_rate", "gdp_growth", "unemployment"}
    assert t["piaci"] == ["EURHUF=X"]
    assert {f["indicator"] for f in t["elorejelzes"]} == {"inflation", "gdp", "unemployment"}
    ures = ds.terv_tisztit({}, TITKOS_LEPES)                 # a modell hibája után is futtatható terv
    assert ures["orszag"] == "HU" and ures["kereses"]["hu"] and ures["jog"]["kifejezesek"]
    rossz = ds.terv_tisztit({"orszag": "../", "kereses": {"hu": 'a" OR 1=1'}}, TITKOS_LEPES)
    assert rossz["orszag"] == "HU" and '"' not in rossz["kereses"]["hu"]


def test_lap_azonosito():
    assert ds.lap_azonosit("Index (X)") == "index" and ds.lap_azonosit("DER SPIEGEL") == "spiegel"
    assert ds.lap_azonosit("Tagesspiegel") is None and ds.lap_azonosit("Portfolio") is None
    assert ds.lap_azonosit("BBC News") == "bbc" and ds.lap_azonosit("Global Times") == "globaltimes"


def test_echolot_429_utan_varakozva_ujraprobal():
    hivasok = []

    async def hiv(szerver, eszkoz, par, max_kar=None):
        hivasok.append(eszkoz)
        if len(hivasok) == 1:
            return json.dumps({"hiba": "HTTPStatusError: Client error '429 Too Many Requests'"})
        return json.dumps({"articles": []})
    h = ds.Hivo(hiv, echolot_koz_s=0.0, varakozas_429_s=0.0)
    v, hiba = _run(h("echolot", "search_news", {"query": "x"}))
    assert hiba is None and hivasok == ["search_news", "search_news"]
    assert h.naplo[-1]["ok"] is True


def test_echolot_eszkozlista_429_utan_frissen_ujraprobal(monkeypatch):
    """Ha az Echolot eszközlistája 429-be fut, a kulso_mcp 2 percig „nem érhető el”-t adna: frissen újrapróbáljuk."""
    import kulso_mcp
    listak = []

    async def lista(nev, *, friss=False):
        listak.append(friss)
        return None if len(listak) == 1 else ([object()], "")

    async def hiv(szerver, eszkoz, par, max_kar=None):
        return json.dumps({"articles": []})
    monkeypatch.setattr(kulso_mcp, "_lista", lista)
    monkeypatch.setattr(kulso_mcp, "hiv", hiv)
    h = ds.Hivo(None, echolot_koz_s=0.0, varakozas_429_s=0.0)
    assert _run(h("echolot", "search_news", {"query": "x"}))[1] is None
    assert _run(h("echolot", "search_news", {"query": "y"}))[1] is None
    assert listak == [False, True]                      # elsőre a cache, aztán friss — és csak egyszer futásonként


def test_a_sajto_motor_echolot_hivasai_egy_munkamenetben(monkeypatch):
    """Élesben mérve: önálló hívásokkal (kézfogás + lezárás hívásonként) 12 mp-es szünettel is 429 jött. A sajtó-motor
    ezért EGY munkamenetben hív; ha a munkamenet megtörik (pl. 429), új kézfogással folytatja."""
    import kulso_mcp
    from fastmcp import Client, FastMCP
    szerver = FastMCP("echolot-teszt")
    hivasok = []

    @szerver.tool
    def search_news(query: str, days: int = 7, limit: int = 10, max_per_source: int = 3, language: str = "") -> str:
        hivasok.append("search_news")
        return json.dumps({"articles": [_cikk("Telex", f"{query} ({language})")]})

    @szerver.tool
    def source_profile(source: str = "", days: int = 30, limit: int = 40) -> str:
        hivasok.append("source_profile")
        return json.dumps({"profiles": {source: {"lean": "center", "articles": 10}}})

    @szerver.tool
    def get_news(source: str = "", limit: int = 5) -> str:
        hivasok.append("get_news")
        return json.dumps({"articles": [_cikk(source, "friss")]})

    @szerver.tool
    def frame_divergence(query: str, days: int = 7) -> str:
        return json.dumps({"status": "empty"})

    @szerver.tool
    def regional_framing(query: str, days: int = 7) -> str:
        return json.dumps({"status": "empty"})
    nyitasok, torve = [], []

    class TorekenyKliens(Client):
        async def call_tool(self, nev, *a, **k):
            if nev == "source_profile" and not torve:              # a hálózat egyszer megtöri a munkamenetet
                torve.append(nev)
                raise RuntimeError("HTTPStatusError: Client error '429 Too Many Requests'")
            return await super().call_tool(nev, *a, **k)

    def kliens(nev):
        nyitasok.append(nev)
        return TorekenyKliens(szerver)
    monkeypatch.setattr(kulso_mcp, "_kliens", kliens)
    h = ds.Hivo(None, echolot_koz_s=0.0, varakozas_429_s=0.0, munkamenet_koz_s=0.0)
    terv = {"kereses": {"hu": "euró", "en": "euro", "de": "Euro"}}
    adat = _run(ds.sajto_adat(terv, h))
    # egy kézfogás a hívássorra, és egy újabb a 429 után — nem hívásonként egy
    assert nyitasok == ["echolot", "echolot"]
    assert hivasok.count("search_news") == 3 and hivasok.count("source_profile") == 7   # a megtört újra ment
    lapok = {l["lap"]: l for l in adat["lapok"]}
    assert lapok["Telex"]["temaba_vago_cikkek"][0]["cim"] == "euró (hu)"
    assert lapok["Telex"]["profil"]["lean"] == "center"
    # a közmédia: rögzített beállítottság (ma a Tisza-kormányt támogatja), az Echolotban nincs → kimondva
    assert "Tisza" in lapok["Közmédia (M1, hirado.hu)"]["profil"]["lean"]
    assert adat["hianyok"] == ["Közmédia (M1, hirado.hu): nincs az Echolot-korpuszban — stílusminta nélkül, "
                               "alacsony bizonyossággal"]
    assert h._kliens is None and not h._munkamenet_aktiv           # a munkamenet lezárva


def test_jog_ismeretlen_hivatkozasra_keresessel_potol(monkeypatch):
    monkeypatch.setenv("LEXONOMICS_MCP_KEY", "nxk_teszt")
    hivasok = []

    async def hiv(szerver, eszkoz, par, max_kar=None):
        hivasok.append((eszkoz, dict(par)))
        if eszkoz == "provision":
            return json.dumps({"hiba": "unknown citation"})
        return json.dumps({"exact": [{"path": "tv/2013/139/0004.md", "citation": "2013. évi CXXXIX. törvény",
                                      "label": "4. §", "snippet": "árstabilitás",
                                      "state": {"source_url": "https://njt.jog.gov.hu/x", "state_date": "2025-01-01"}}],
                           "text_hits": []})
    terv = {"jog": {"hivatkozasok": ["Mnbtv 4. §"], "kifejezesek": [], "dontesek": []}}
    adat = _run(ds.jog_adat(terv, ds.Hivo(hiv)))
    assert ("search", {"q": "Mnbtv 4. §", "limit": 3}) in hivasok
    assert adat["talalatok"][0]["source_url"] == "https://njt.jog.gov.hu/x"
    assert any("Mnbtv" in h for h in adat["hianyok"])                 # a provision-hiba kimondva marad


def test_echolot_idokerete_utan_kimarad_kimondva():
    async def hiv(*a, **k):
        raise AssertionError("nem hívható")
    h = ds.Hivo(hiv, echolot_koz_s=0.0, echolot_max_s=-1)
    v, hiba = _run(h("echolot", "search_news", {"query": "x"}))
    assert "időkerete" in hiba


# ── 2. a teljes futás ────────────────────────────────────────────────────────

def test_teljes_futas_negy_motorral_naplo_es_studio(get_db, kapu, kulcsok):
    naplo = []
    r = _futtat(get_db, naplo=naplo, teljes=True)
    assert r["allapot"] == "kesz" and r["mod"] == "teljes", r["figyelmeztetesek"]
    # minden lépés a kapun ment, a célja szerint (5 Sonnet)
    assert sorted(h["purpose"] for h in kapu) == sorted(ds.CELOK.values())
    # a sajtó-motor mind a hét lapot megkapta, a valódi korábbi cikkeikkel
    lapok = {l["lap"]: l for l in r["sajto"]["adat"]["lapok"]}
    assert set(lapok) == {l["nev"] for l in ds.LAPOK}
    assert lapok["Telex"]["temaba_vago_cikkek"][0]["cim"] == "Euró: itt a dátum"
    assert lapok["Der Spiegel"]["temaba_vago_cikkek"][0]["forras"] == "DER SPIEGEL"
    assert lapok["444"]["friss_cimek_stilushoz"] and not lapok["444"]["temaba_vago_cikkek"]   # stílusminta
    assert ("echolot", "source_profile", {"source": "Global Times", "days": 7, "limit": 3}) in naplo
    # a piac-motor tömörített adatot kap (az audit-mezők nem mennek a modellnek)
    piac_payload = next(h for h in kapu if h["purpose"] == "szimulator/piac")["payload"]
    assert "all_attempts" not in piac_payload["messages"][1]["content"]
    assert "imf_all_years" not in piac_payload["messages"][1]["content"]
    # a terv fehérlistája érvényesült: a rossz mutató és szimbólum nem ment ki
    assert not any(p.get("indicator") == "rm -rf" or p.get("symbol") == "ROSSZ SZIMBÓLUM!!" for _, _, p in naplo)
    # költség: 5 hívás × 0,01 $, a kapu valós költéséből
    assert r["koltseg"]["usd"] == pytest.approx(0.05) and not r["koltseg"]["kimaradt"]
    # Studio: névvel ellátott új dokumentum, aztán EGY wordcraft-hívás
    assert r["studio"]["edit_url"].endswith("/edit")
    wc = [p for sz, e, p in naplo if e == "wordcraft"]
    assert len(wc) == 1 and wc[0]["file_id"] == "uj.docx"
    # az egyoldalas minden szakaszt tartalmaz, a szimulált címek jelölve
    szoveg = ds.egyoldalas(r)
    for darab in ("ÖSSZEGZÉS", "TÁRSADALOM", "nem valódi cikkek", "GAZDASÁGI ELŐREJELZÉS", "JOG", "KOCKÁZATI TÉRKÉP",
                  "Közmédia (M1, hirado.hu) címe",
                  "MÓDOSÍTANI", "MÉRHETŐ ELŐREJELZÉSEK", "Global Times címe", "nem hiteles jogforrás",
                  "18-29", "Studio-dokumentum: https://studio/app/file/kesz.docx/edit"):
        assert darab in szoveg, darab
    assert "<b>" in ds.telegram_html(r) and "&lt;" not in ds.telegram_html({"lepes": "x"}).replace("<b>", "")


def test_az_elorejelzesek_a_delphoi_hash_lancba_kerulnek_a_lepes_szovege_nem(get_db, kapu, kulcsok):
    r = _futtat(get_db, teljes=True)
    elore = r["elorejelzesek"]
    # elöl a 2 gazdasági intervallum (infláció, árfolyam), aztán 1 sajtó + 1 piac (a rossz dátumú kimaradt) + 1 jog +
    # 1 társadalmi (a múltbeli kimaradt)
    assert [e["terulet"] for e in elore] == ["piac", "piac", "sajto", "piac", "jog", "tarsadalom"]
    assert len(r["elorejelzes_kimaradt"]) == 2
    conn = get_db()
    sorok = [dict(x) for x in conn.execute("SELECT * FROM delphoi_nowcast_ledger ORDER BY id")]
    # a lépés szövege és a magánjel sehol a láncban
    for s in sorok:
        assert "PRIVAT-JEL" not in json.dumps(s, ensure_ascii=False)
    conn.close()
    assert [s["entity_key"] for s in sorok] == [f"szim:{r['futas']}:{i:02d}" for i in range(1, 7)]
    assert [s["direction"] for s in sorok] == [e["valoszinuseg"] for e in elore]
    assert sorok[1]["model_id"] == f"{ds.VERZIO}|claude-sonnet-5-5"
    assert sorok[0]["prev_hash"] == delphoi.GENESIS
    assert delphoi.verify_ledger_chain(get_db) == {"ok": True, "checked": 6, "head": sorok[-1]["content_hash"]}
    # append-only: a DELPHOI triggere minden átírást megöl
    conn = get_db()
    with pytest.raises(Exception, match="append-only"):
        conn.execute("UPDATE delphoi_nowcast_ledger SET direction = 0.99")
    conn.close()


def test_naplo_ellenorzes_kiszuri_az_utolagos_atirast(get_db, kapu, kulcsok):
    r = _futtat(get_db)
    ds._nyom(get_db, r["futas"], "adat", json.dumps(r, ensure_ascii=False))
    ell = ds.naplo_ellenorzes(get_db, r["futas"])
    assert ell["ok"] and ell["egyezik"] == 4 and not ell["elter"]
    # valaki átírja a messages-ben tárolt előrejelzést (szebb valószínűség utólag) → kiderül
    r["elorejelzesek"][1]["valoszinuseg"] = 0.99
    ds._nyom(get_db, r["futas"], "adat", json.dumps(r, ensure_ascii=False))
    ell = ds.naplo_ellenorzes(get_db, r["futas"])
    assert not ell["ok"] and ell["elter"] == [f"szim:{r['futas']}:02"]


def test_koltsegkeret_reszeredmeny_kimondva(get_db, kapu, kulcsok):
    # csak a terv fér bele: a többi lépés NEM hívódik, a kimenet kimondja
    keret = ds.becsles_usd("kozep", 6_000, ds.KIMENET["terv"]) * 1.05
    r = _futtat(get_db, max_usd_=keret)
    assert [h["purpose"] for h in kapu] == ["szimulator/terv"]
    assert r["allapot"] == "reszleges"
    assert set(r["koltseg"]["kimaradt"]) == {"piac", "osszegzes"}
    assert any("költségkeret" in f for f in r["figyelmeztetesek"])
    assert "RÉSZEREDMÉNY" in ds.egyoldalas(r)
    assert r["elorejelzesek"] == []


def test_anthropic_kulcs_nelkul_nem_fut(get_db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = _futtat(get_db)
    assert r["allapot"] == "hiba" and "ANTHROPIC_API_KEY" in r["figyelmeztetesek"][0]
    assert ds.eszkoz_definiciok() == []
    assert "ANTHROPIC_API_KEY" in ds.inditas(TITKOS_LEPES, get_db=get_db)["hiba"]


def test_studio_hibaja_nem_viszi_el_az_eredmenyt(get_db, kapu, kulcsok):
    r = _futtat(get_db, hiv=hamis_mcp(studio_ok=False))
    assert r["allapot"] == "kesz" and "Studio-hiba" in r["studio"]["hiba"]


def test_wordcraft_parancsok_szerkezete(get_db, kapu, kulcsok):
    r = _futtat(get_db, studio=False, teljes=True)
    c = ds.wordcraft_parancsok(r)
    assert c[0]["command"] == "para.heading1"
    nevek = [x["command"] for x in c]
    bold = [x["params"]["value"] for x in c if x["command"] == "format.bold"]
    assert bold.count(True) == bold.count(False)                   # minden félkövér rész lezárva
    assert nevek.count("para.bullets") % 2 == 0                    # minden lista lezárva
    tablak = [x["params"] for x in c if x["command"] == "insert.table"]
    assert {"rows": 9, "cols": 4} in tablak                        # a sajtó: fejléc + nyolc lap (közmédiával)
    assert {"rows": 6, "cols": 6} in tablak                        # a gazdaság: fejléc + öt mutató
    # minden táblázat lapszélességű, ismétlődő fejléccel, és egy sora sem törik két oldalra
    props = [x["params"] for x in c if x["command"] == "table.properties"]
    assert sum(1 for p in props if p.get("headerRow")) == len(tablak)
    assert sum(1 for p in props if p.get("allowBreak") is False) == sum(t["rows"] for t in tablak)
    szelessegek = [x["params"]["width"] for x in c if x["command"] == "table.columnWidth"]
    assert len(szelessegek) == sum(t["cols"] for t in tablak)                 # minden oszlopnak kimondott szélesség
    for sz in ds.szakaszok(r):
        if sz.get("tablazat"):
            assert len(sz["tablazat"]["aranyok"]) == len(sz["tablazat"]["fejlec"])
            assert abs(sum(sz["tablazat"]["aranyok"]) - 1.0) < 1e-9
    # elöl a döntéshez kellő oldal, utána a motorok részletei
    cimek = [x["params"]["text"] for i, x in enumerate(c) if x["command"] == "text.insert"
             and i and c[i - 1]["command"] == "para.heading2"]
    assert (cimek.index("Gazdasági előrejelzés — alapvonal és a lépés hatása (Makronóm)")
            < cimek.index("Kockázati térkép") < cimek.index("Társadalom — DELPHOI szintetikus panel"))
    assert all("\n" not in x["params"].get("text", "") for x in c)


# ── 3. DELPHOI: kredit, refund, csoportbontás ───────────────────────────────

@pytest.fixture
def fg_db(get_db):
    conn = get_db()
    delphoi.ensure_tables(conn)
    delphoi.ensure_fg_tables(conn)
    conn.close()
    return get_db


def test_panel_kredit_pontosan_a_hianyt_irja_jova(fg_db, monkeypatch):
    monkeypatch.setenv("SZIMULATOR_PANEL_N", "60")
    kapott = {}

    async def hamis_process(deps, job_id, **kw):
        kapott["_modell"] = kw.get("model")
        conn = fg_db()
        job = dict(conn.execute("SELECT * FROM delphoi_jobs WHERE id=?", (job_id,)).fetchone())
        conn.close()
        kapott.update(job)
        return {"ok": True, "job_id": job_id, "result": {"overall_score": 3.1, "segments": []}}
    monkeypatch.setattr(delphoi, "process_job", hamis_process)
    deps = {"get_db": fg_db, "siliconflow_api_key": "sf-teszt"}
    t = _run(ds.tarsadalom(TITKOS_LEPES, "HU", get_db=fg_db, deps=deps, futas="sz-00000001", max_s=5))
    assert t["ok"] and t["kredit"] == 2 and t["feltoltes"] == 0           # az üdvözlő 2 kredit fedezte
    assert kapott["input_kind"] == "concept" and kapott["input_text"] == TITKOS_LEPES
    assert json.loads(kapott["panel_spec"])["n_per_cell"] == 60
    assert kapott["_modell"] == ds.PANEL_MODELL_ALAP and t["modell"] == ds.PANEL_MODELL_ALAP
    t2 = _run(ds.tarsadalom(TITKOS_LEPES, "XX", get_db=fg_db, deps=deps, futas="sz-00000002", max_s=5))
    assert t2["ok"] and t2["feltoltes"] == 2 and t2["orszag"] == "HU"     # nincs fedezet → pontosan a hiány
    egyenleg = delphoi.get_credits(fg_db, ds.delphoi_felhasznalo())
    assert egyenleg["balance"] == 0
    assert any(s["reason"] == "topup:szimulator-sz-00000002" and s["delta"] == 2 for s in egyenleg["ledger"])


def test_panel_modell_kulon_env_a_delphoi_termektol(fg_db, monkeypatch):
    """A szimulátor saját panelmodellt kap (SZIMULATOR_PANEL_MODEL), a nyilvános DELPHOI FG_MODEL-je érintetlen."""
    from plugins import delphoi as _d
    alap_fg = _d.FG_MODEL
    monkeypatch.setenv("SZIMULATOR_PANEL_MODEL", "Qwen/Proba-Modell")
    kapott = {}

    async def hamis_process(deps, job_id, **kw):
        kapott["model"] = kw.get("model")
        return {"ok": True, "job_id": job_id, "result": {"overall_score": 3.0, "segments": []}}
    monkeypatch.setattr(delphoi, "process_job", hamis_process)
    t = _run(ds.tarsadalom(TITKOS_LEPES, "HU", get_db=fg_db, deps={"get_db": fg_db, "siliconflow_api_key": "x"},
                           futas="sz-00000009", max_s=5))
    assert t["ok"] and kapott["model"] == "Qwen/Proba-Modell" and t["modell"] == "Qwen/Proba-Modell"
    assert _d.FG_MODEL == alap_fg
    monkeypatch.setenv("SZIMULATOR_PANEL_MODEL", "  ")
    assert ds.panel_modell() == ds.PANEL_MODELL_ALAP


def test_process_job_modell_felulirasa_a_job_sorban(fg_db, monkeypatch):
    """process_job(model=…): a chat, a model_id és a panel_version a TÉNYLEGES modellt rögzíti; alapból FG_MODEL."""
    from plugins import delphoi as _d
    monkeypatch.setenv("DELPHOI_FG_CACHE", "0")
    conn = fg_db()
    conn.execute("CREATE TABLE IF NOT EXISTS press_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, date_iso TEXT, "
                 "lang TEXT, signal_type TEXT, content TEXT, created_at TEXT)")
    conn.commit()
    conn.close()
    assert _d.fg_panel_version() == _d.FG_PANEL_VERSION
    assert _d.fg_panel_version("Qwen/X") == "orakel-agora-hu-v2|Qwen/X" != _d.FG_PANEL_VERSION
    c = _d.create_job(fg_db, "teszt:modell", "concept", "Próba-lépés a modellcseréhez.", {"country": "HU", "n_per_cell": 30})
    assert c["ok"]

    async def chat(prompt):
        return "Ez nekem eléggé tetszik."

    async def embed(texts):
        return [[1.0, 0.0, 0.0] for _ in texts]
    rep = _run(_d.process_job({"get_db": fg_db}, c["job_id"], chat_fn=chat, embed_fn=embed, model="Qwen/X"))
    assert rep["ok"], rep
    panel = rep["result"]["panel"]
    assert panel["model_id"].startswith("Qwen/X|") and panel["panel_version"] == "orakel-agora-hu-v2|Qwen/X"
    conn = fg_db()
    job = dict(conn.execute("SELECT model_id, panel_version FROM delphoi_jobs WHERE id=?", (c["job_id"],)).fetchone())
    conn.close()
    assert job["model_id"].startswith("Qwen/X|") and job["panel_version"] == "orakel-agora-hu-v2|Qwen/X"


def test_panel_idotullepesnel_failed_es_refund(fg_db, monkeypatch):
    async def lassu(deps, job_id, **kw):
        conn = fg_db()
        conn.execute("UPDATE delphoi_jobs SET status='running' WHERE id=?", (job_id,))
        conn.commit()
        conn.close()
        await asyncio.sleep(5)
    monkeypatch.setattr(delphoi, "process_job", lassu)
    t = _run(ds.tarsadalom(TITKOS_LEPES, "HU", get_db=fg_db, deps={"siliconflow_api_key": "x"},
                           futas="sz-00000003", max_s=0.05))
    assert not t["ok"] and "visszaírva" in t["hiba"]
    job = delphoi.get_job(fg_db, t["job_id"], ds.delphoi_felhasznalo())
    assert job["status"] == "failed" and job["refunded"]
    assert delphoi.get_credits(fg_db, ds.delphoi_felhasznalo())["balance"] == 2


def test_panel_sf_kulcs_nelkul_kimondva(fg_db, monkeypatch):
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    t = _run(ds.tarsadalom(TITKOS_LEPES, "HU", get_db=fg_db, deps={}, futas="sz-00000004", max_s=1))
    assert not t["ok"] and "SiliconFlow" in t["hiba"]


def test_delphoi_aggregatum_csoportbontas():
    rows = [{"segment": "baloldali", "ssr_score": 4.0, "dims": {"age": "18-29", "settlement": "Budapest"}},
            {"segment": "jobboldali", "ssr_score": 2.0, "dims": {"age": "60+", "settlement": "község"}},
            {"segment": "jobboldali", "ssr_score": 2.5, "dims": {"age": "60+", "settlement": "község"}}]
    agg = delphoi._aggregate("concept", rows)
    assert [s["segment"] for s in agg["segments"]] == ["baloldali", "jobboldali"]     # a régi bontás változatlan
    assert agg["breakdowns"]["age"] == [{"group": "18-29", "n": 1, "score": 4.0, "vs_baseline": 1.0},
                                        {"group": "60+", "n": 2, "score": 2.25, "vs_baseline": -0.75}]
    assert "edu" not in agg["breakdowns"]
    assert "breakdowns" not in delphoi._aggregate("concept", [{"segment": "x", "ssr_score": 3.0}])
    kiv = ds.panel_kivonat({"ok": True, "eredmeny": agg})
    assert kiv["bontasok"]["kor"][0]["csoport"] == "18-29" and kiv["media_etrend"][0]["csoport"] == "baloldali"


# ── 4. bekötés: Feldwebel, MCP, tudásbázis-kör ──────────────────────────────

def test_feldwebel_nyugta_aztan_eredmeny(get_db, kapu, kulcsok, monkeypatch):
    from feldwebel import BridgeContext, responder
    pushok = []

    async def push(s):
        pushok.append(s)
    ctx = BridgeContext(telegram_push=push, get_db=get_db)
    eredeti = ds.futtat

    async def gyors(lepes, horizont="", **kw):
        kw.update(hiv=hamis_mcp(), panel_fn=hamis_panel, echolot_koz_s=0.0)
        return await eredeti(lepes, horizont, **kw)
    monkeypatch.setattr(ds, "futtat", gyors)

    async def kor():
        assert ds.ESZKOZ_NEV in {t["function"]["name"] for t in responder._szimulator_eszkozok()}
        nyugta = json.loads(await responder._execute_tool("dontes_szimulator", {"lepes": TITKOS_LEPES}, ctx))
        assert nyugta["elindult"] and ds.FUTAS_MINTA.match(nyugta["futas"]) and not pushok
        # amíg fut, második nem indul
        masik = json.loads(await responder._execute_tool("dontes_szimulator", {"lepes": TITKOS_LEPES}, ctx))
        assert "már fut" in masik["error"]
        assert ds.allapot(get_db, nyugta["futas"])["allapot"] == "fut"
        await asyncio.gather(*ds._FUTO.values())
        return nyugta["futas"]
    futas = _run(kor())
    # alapból gazdasági mód: a sajtó nem fut, a gazdasági tábla elöl
    assert len(pushok) == 1 and "DÖNTÉS-SZIMULÁTOR" in pushok[0] and "Telex címe" not in pushok[0]
    assert "Gazdasági előrejelzés" in pushok[0]
    a = ds.allapot(get_db, futas)
    assert a["allapot"] == "kesz" and a["studio"]["edit_url"] and a["naplo_ellenorzes"]["ok"]
    assert len(a["elorejelzesek"]) == 4                      # 2 gazdasági intervallum + 1 piac + 1 társadalmi
    # a nyom a meglévő messages táblában: indulás, adat, és az eredmény a Kommandantnak (olvasatlan)
    conn = get_db()
    sorok = [dict(x) for x in conn.execute("SELECT recipient, subject, status FROM messages ORDER BY id")]
    conn.close()
    assert [s["subject"].rsplit("· ", 1)[1] for s in sorok] == ["fut", "adat", "kész"]
    assert sorok[-1]["recipient"] == "kommandant" and sorok[-1]["status"] == "unread"


def test_feldwebel_payloadjaban_a_vegen_a_tools_lista_utan():
    import inspect
    from feldwebel import responder
    assert "_szimulator_eszkozok()" in inspect.getsource(responder._call_deepseek)
    assert ds.ESZKOZ_NEV not in {t["function"]["name"] for t in responder.TOOLS}       # a statikus lista érintetlen
    assert "`dontes_szimulator`" in responder.SYSTEM_PROMPT_TEMPLATE


def test_tudasbazis_korben_nem_futhat():
    import futar_kliens as fk
    assert fk.sf_fele("dontes_szimulator", {"lepes": "x"})


def test_ervenytelen_bemenet_es_futas_azonosito(get_db, kapu):
    assert "túl rövid" in ds.inditas("rövid", get_db=get_db)["hiba"]
    assert "túl hosszú" in ds.inditas("x" * 5000, get_db=get_db)["hiba"]
    assert "érvénytelen" in ds.allapot(get_db, "sz-%' OR 1=1")["hiba"]
    assert ds.allapot(get_db, "sz-00000000")["hiba"] == "nincs ilyen futás"


def test_mcp_eszkoz_zart_ajto_es_sajat_peldany(monkeypatch, kapu):
    import server
    fn = getattr(server.dontes_szimulator, "fn", server.dontes_szimulator)
    monkeypatch.delenv("BRIDGE_MCP_ZARVA", raising=False)
    assert "zárása után" in json.loads(_run(fn(TITKOS_LEPES, "", "", "")))["hiba"]
    monkeypatch.setenv("BRIDGE_MCP_ZARVA", "1")
    for idegen in ("youngereka", "idegen"):
        assert "saját példány" in json.loads(_run(fn(TITKOS_LEPES, "", "", idegen)))["hiba"]
    hivott = {}

    def hamis_inditas(lepes, horizont="", *, get_db, deps=None, kesz_fn=None, **kw):
        hivott.update(lepes=lepes, horizont=horizont, deps=sorted(deps))
        return {"futas": "sz-12345678", "elindult": True}
    monkeypatch.setattr(ds, "inditas", hamis_inditas)
    assert json.loads(_run(fn(TITKOS_LEPES, "2 év", "", "")))["futas"] == "sz-12345678"
    assert hivott["horizont"] == "2 év" and "siliconflow_api_key" in hivott["deps"]
    assert json.loads(_run(fn("", "", "sz-00000000", "cli-claus")))["hiba"] == "nincs ilyen futás"


def test_a_kapu_valasza_hordozza_a_hivas_arat(monkeypatch):
    """A költségkeret a kapu VALÓS költéséből számol: a `usage.koltes_usd` a csonkolás miatti újrapróbálással együtt."""
    from anthropic.types.beta import BetaMessage
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt")
    monkeypatch.setattr(llm_kapu, "_KOLTES", {"honap": "", "usd": 0.0, "hivasok": 0, "szintenkent": {}})

    def msg(stop):
        return BetaMessage.model_validate({
            "id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": "{}"}], "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 1000, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}})
    valaszok = [msg("max_tokens"), msg("end_turn")]

    async def kuld(kw):
        return valaszok.pop(0)
    monkeypatch.setattr(llm_kapu, "_kuld", kuld)
    data, err, _ = _run(llm_kapu.chat({"messages": [{"role": "user", "content": "x"}]}, purpose="szimulator/terv"))
    egy = (1000 * 2.0 + 1000 * 10.0) / 1_000_000
    assert err is None and data["usage"]["koltes_usd"] == pytest.approx(2 * egy)


# ── 5. az élő próba tanulságai ───────────────────────────────────────────────

def test_azonos_elorejelzes_egyszer_es_az_osszegzo_latja_a_motorokeit(get_db, kapu, kulcsok, monkeypatch):
    """Élő próba (11-22): az összegző ugyanazt az inflációs állítást még egyszer megadta. Most látja a motorok
    előrejelzéseit, és ami szó szerint ismétlés, az kimarad — kimondva."""
    ismetles = dict(VALASZOK["szimulator/osszegzes"])
    ismetles["elorejelzesek"] = [_elore("A KSH éves inflációja 2027-ben 3,8% alatt marad!", 300, 0.6),
                                 _elore("Új társadalmi állítás", 150, 0.6)]
    monkeypatch.setitem(VALASZOK, "szimulator/osszegzes", ismetles)
    r = _futtat(get_db)
    allitasok = [e["allitas"] for e in r["elorejelzesek"]]
    assert allitasok.count("A KSH éves inflációja 2027-ben 3,8% alatt marad") == 1
    assert "Új társadalmi állítás" in allitasok
    assert any("ugyanez már szerepel" in x for x in r["elorejelzes_kimaradt"])
    ossz = next(h for h in kapu if h["purpose"] == "szimulator/osszegzes")["payload"]["messages"][1]["content"]
    assert "MÁR MEGADOTT ELŐREJELZÉSEI" in ossz and "3,8% alatt marad" in ossz


def test_elorejelzes_feltetele_es_hash_resze():
    ma = datetime.now(timezone.utc).date()
    jo, _ = ds.elorejelzesek_tisztit([{**_elore("a", 100), "feltetel": "alap"}, _elore("b", 100),
                                      {**_elore("c", 100), "feltetel": "mindegy"}], "piac", ma=ma, hatar_napok=365)
    assert [e["feltetel"] for e in jo] == ["alap", "lepes", "lepes"]       # alapból: csak a lépéssel értékelhető
    h1 = ds.elorejelzes_kanon(jo[0], "sz-00000000", 1, "x")
    h2 = ds.elorejelzes_kanon({**jo[0], "feltetel": "lepes"}, "sz-00000000", 1, "x")
    assert h1 != h2                                                       # a feltétel is a commit része
    assert "a lépéstől függetlenül" in ds.egyoldalas({"elorejelzesek": jo, "lepes": "x"})
    for sema in (ds.SAJTO_SEMA, ds.PIAC_SEMA, ds.JOG_SEMA, ds.TARSADALMI_SEMA):
        assert '"feltetel"' in sema and "{" in sema and "{{" not in sema


def test_studio_hosszu_valaszbol_is_kiolvassa_a_linket(monkeypatch):
    """Élő próba (11-22): 511 parancs → >100 ezer karakteres válasz; az 50 ezres sapka a JSON-t elvágta, és a
    dokumentum elkészült ugyan, de a link elveszett. Most nagy a keret, és csonka JSON-ból is kiolvassuk."""
    monkeypatch.setenv("STUDIO_MCP_KEY", "studio_teszt")
    kapott = {}

    async def hiv(szerver, eszkoz, par, max_kar=None):
        kapott[eszkoz] = max_kar
        if eszkoz == "files":
            return json.dumps({"file_id": "uj.docx"})
        return '{"file_id": "kesz.docx", "edit_url": "https://studio/app/file/kesz.docx/edit", "steps": [{"n": 1, "ok'
    v = _run(ds.studio_dokumentum({"lepes": "x", "terv": {"rovid_cim": "Euró"}}, hiv=hiv))
    assert v["file_id"] == "kesz.docx" and v["edit_url"].endswith("/edit")
    assert kapott["wordcraft"] >= 1_000_000


def test_a_szerver_sajat_hibauzenete_rovid():
    belso = json.dumps({"error": "Mnb. tv. 4. §: not a corpus path", "disclaimer": {"hu": "x" * 500}})
    assert ds._hiba_e({"hiba": belso, "sema": {}}) == "Mnb. tv. 4. §: not a corpus path"


def test_a_motorok_a_jelenlegi_politikai_helyzetet_kapjak(get_db, kapu, kulcsok):
    """Élő próba (11-22): a sajtó-motor a Tisza-kormány idején „Orbán ígér eurót” címet írt, és a Magyar Nemzetet
    kormánypártinak vette. A motorok (a tervező kivételével) a DELPHOI országkonfigjának helyzetleírását kapják."""
    _futtat(get_db, teljes=True)
    helyzet = delphoi.COUNTRY_PANEL_CONFIG["HU"]["priming"]
    for h in kapu:
        system = h["payload"]["messages"][0]["content"]
        assert (helyzet in system) == (h["purpose"] != "szimulator/terv"), h["purpose"]
    assert "Politikus nevét csak akkor" in next(h for h in kapu if h["purpose"] == "szimulator/sajto")[
        "payload"]["messages"][0]["content"]


def test_hivatkozas_magyarazat_nelkul_es_egysoros_hiba():
    t = ds.terv_tisztit({"jog": {"hivatkozasok": ["Alaptörvény 38. cikk (forint, pénzrendszer)", "Mt. 66. § (1)"]}},
                        TITKOS_LEPES)
    assert t["jog"]["hivatkozasok"] == ["Alaptörvény 38. cikk", "Mt. 66. § (1)"]
    assert ds._hiba_e({"hiba": "HTTPStatusError: 503\nFor more information check: https://x"}) == "HTTPStatusError: 503"



# ── v2: gazdasági mag, módok, közmédia (a Kommandant, 11-22) ────────────────

def test_gazdasagi_mod_alapbol_olcso_es_a_gazdasag_elol(get_db, kapu, kulcsok):
    """„túl drága … a közgazdasági közvetlen előrejelzés fontosabb”: alapból csak terv + gazdaság + összegzés (3
    Sonnet), sajtó és jog nem fut, nincs Echolot- és Lexonomics-hívás, a kimenet nem mondja hiánynak."""
    naplo = []
    r = _futtat(get_db, naplo=naplo)
    assert r["mod"] == "gazdasagi" and r["allapot"] == "kesz", r["figyelmeztetesek"]
    assert sorted(h["purpose"] for h in kapu) == ["szimulator/osszegzes", "szimulator/piac", "szimulator/terv"]
    assert not any(sz in ("echolot", "lexonomics") for sz, _, _ in naplo)
    assert r["koltseg"]["usd"] == pytest.approx(0.03) and not r["figyelmeztetesek"]
    # a gazdasági alapvonal: 3 pálya + alapkamat + árfolyam; a hatás ott, ahol a modell érvényes számot adott
    tabla = {t["kulcs"]: t for t in r["gazdasag"]}
    assert set(tabla) == {"HU:inflation", "HU:gdp", "HU:unemployment", "HU:policy_rate", "EURHUF=X"}
    inf = tabla["HU:inflation"]
    assert (inf["ertek"], inf["also"], inf["felso"]) == (3.1, 2.2, 4.0) and inf["idoszak"] == "2027-01"
    assert (inf["dontessel"], inf["dontessel_also"], inf["dontessel_felso"]) == (3.5, 2.3, 4.8)
    fx = tabla["EURHUF=X"]                                  # naiv alapvonal: a modell sávja a teljes sáv
    assert fx["naiv"] and (fx["dontessel"], fx["dontessel_also"], fx["dontessel_felso"]) == (366.8, 357.8, 376.8)
    assert tabla["HU:gdp"]["hatas"] is None                 # „sok” nem szám → nincs belőle előrejelzés
    # a gazdasági intervallumok elöl, a naplóban, a határidő az adat megjelenése
    elore = r["elorejelzesek"]
    assert [e["terulet"] for e in elore] == ["piac", "piac", "piac", "tarsadalom"]
    assert "2,30 és 4,80 %" in elore[0]["allitas"] and elore[0]["valoszinuseg"] == 0.8
    assert elore[0]["hatarido"] == "2027-03-17"
    szoveg = ds.egyoldalas(r)
    assert szoveg.index("GAZDASÁGI ELŐREJELZÉS") < szoveg.index("KOCKÁZATI TÉRKÉP") < szoveg.index("TÁRSADALOM")
    assert "SAJTÓ —" not in szoveg and "JOG —" not in szoveg and "gazdasági mód" in szoveg
    # az összegző a gazdasági táblát kapja elöl, és tudja, hogy a sajtó/jog nem futott (nem hiány)
    ossz = next(h for h in kapu if h["purpose"] == "szimulator/osszegzes")["payload"]["messages"][1]["content"]
    assert '"gazdasagi_elorejelzes"' in ossz and '"nem_futott": [' in ossz


def test_cel_pont_a_horizont_vegeig_megjeleno_adat():
    ma = date(2026, 11, 22)
    havi = [{"period": f"2027-{m:02d}", "value": 3.0 + m / 10, "low": 2.0, "high": 4.0} for m in range(1, 13)]
    # 12 hónap: a 2027-11-22-ig megjelenő utolsó havi adat a szeptemberi (09-30 + 45 nap = 11-14)
    assert ds.cel_pont(havi, date(2027, 11, 22), ma)["period"] == "2027-09"
    negyedeves = [{"period": f"2027-Q{q}", "value": 2.0} for q in range(1, 5)]
    # GDP: 06-30 + 75 nap = 09-13 ≤ 11-22, a Q3 (09-30 + 75 = 12-14) még nem jelenik meg
    assert ds.cel_pont(negyedeves, date(2027, 11, 22), ma)["period"] == "2027-Q2"
    # a pályán túli horizont: az utolsó pont; túl rövid horizont: nincs célpont
    assert ds.cel_pont(havi, date(2029, 1, 1), ma)["period"] == "2027-12"
    assert ds.cel_pont(havi, date(2026, 12, 31), ma) is None


def test_kozmedia_a_lapok_kozott_a_mai_terkeppel():
    """A Kommandant (11-22): „A médiatérkép elavult: a közmédia mára tisza párti lett.”"""
    kozmedia = next(l for l in ds.LAPOK if l["kulcs"] == "kozmedia")
    assert "Tisza" in kozmedia["lean"] and kozmedia["forras"] is None
    assert ds.lap_azonosit("hirado.hu") == "kozmedia" and ds.lap_azonosit("M1") == "kozmedia"
    import inspect
    forras = inspect.getsource(ds.sajto_lepes)
    assert "„gov” címkéje a 2026 előtti" in forras and "2026 tavasza óta a Tisza-kormányt" in forras


def test_a_teljes_mod_a_feldwebel_es_az_mcp_eszkozon(get_db, kapu):
    props = ds.ESZKOZ_DEF["function"]["parameters"]["properties"]
    assert props["teljes"]["type"] == "boolean" and "GAZDASÁGI" in ds.ESZKOZ_DEF["function"]["description"]
    assert ds.varhato_perc() == "2–5 perc" and ds.varhato_perc(True) == "4–8 perc"
    import inspect
    import server
    assert "teljes" in inspect.signature(server.dontes_szimulator).parameters
