"""Tests for feldwebel.market_brief — the Bridge side of PLAN_20260531.md.

Covers:
- §3 schema validation (validate_brief): accepts a valid brief, rejects each
  structural violation.
- JSON extraction from a messy model response (markdown fences, surrounding prose).
- End-to-end generate_market_brief with a stubbed ai_query and NOFX_BRIEF_URL unset
  → local-file fallback (never crashes, returns ok=True).
- Retry loop: first response invalid JSON → second valid → ok with attempts=2.
- All attempts invalid → ok=False (cron loop stays alive).
"""

import asyncio
import json
import os

import pytest

from feldwebel import market_brief as mb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _valid_brief():
    return {
        "asof": "2026-06-05T13:00Z",
        "session": "morning",
        "valid_until": "2026-06-05T19:00Z",
        "regime": "risk_on",
        "risk_budget_pct": 55,
        "bias": {"equity": "long_small"},
        "tradeable": ["NVDA", "AAPL"],
        "avoid": ["NFP 2026-06-06 08:30 ET -> no equity entries +/-1h"],
        "events": ["US Consumer Sentiment today"],
        "crowd": "complacent",
        "exposure_caps": [{"cluster": "semis", "members": ["NVDA", "MU"], "max_units": 1}],
        "exit_flags": [
            {"symbol": "NVDA", "reason": "earnings tonight", "deadline": "2026-06-05T20:00Z"}
        ],
        "note": "Watch CPI surprise.",
        # A ket EMBERI mezo. 2026-08-30 ota kotelezo: a brief egyetlen valodi
        # fogyasztoja a Kommandant a Telegramon, es nelkuluk a kikuldott uzenet
        # gepi mezok listaja — vagyis szamara URES, akkor is, ha a sema hibatlan.
        "macro_review": ("MAGYARORSZAG: az inflacio 4,1 szazalek.\n\n"
                         "EUROZONA: a HICP 2,2 szazalek.\n\n"
                         "USA: a Fed alapkamata 3,75 szazalek."),
        "telegram_digest": "A piacot ma a CPI-varakozas mozgatja.",
        # A PROVENANCIA egy blokkban, a VEGEN — nem a mondatok kozott.
        "sources": ["KSH — HU infláció 2026-07", "ECB — betéti kamat 2026-08-29"],
    }


def _stub_ai_query(responses):
    """Return a fake ai_query coroutine yielding the given responses in order.

    Each item is the *inner* response text; we wrap it in the ai_query envelope.
    """
    calls = {"n": 0}

    async def _fake(**kwargs):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return json.dumps({"model": "deepseek-ai/DeepSeek-V4-Pro", "response": responses[i]})

    _fake.calls = calls
    return _fake


# ---------------------------------------------------------------------------
# validate_brief
# ---------------------------------------------------------------------------
def test_validate_accepts_valid_brief():
    assert mb.validate_brief(_valid_brief()) == []


def test_validate_rejects_non_object():
    errs = mb.validate_brief(["not", "a", "dict"])
    assert errs and "object" in errs[0]


def test_validate_missing_field():
    b = _valid_brief()
    del b["regime"]
    errs = mb.validate_brief(b)
    assert any("regime" in e for e in errs)


def test_validate_bad_session():
    b = _valid_brief()
    b["session"] = "evening"
    errs = mb.validate_brief(b)
    assert any("session" in e for e in errs)


def test_validate_risk_budget_range():
    b = _valid_brief()
    b["risk_budget_pct"] = 150
    errs = mb.validate_brief(b)
    assert any("risk_budget_pct" in e for e in errs)


def test_validate_exposure_caps_shape():
    b = _valid_brief()
    b["exposure_caps"] = [{"cluster": "semis"}]  # missing members + max_units
    errs = mb.validate_brief(b)
    assert any("members" in e for e in errs)
    assert any("max_units" in e for e in errs)


def test_validate_exit_flags_shape():
    b = _valid_brief()
    b["exit_flags"] = [{"symbol": "NVDA", "reason": "x"}]  # missing deadline
    errs = mb.validate_brief(b)
    assert any("deadline" in e for e in errs)


# ---------------------------------------------------------------------------
# _extract_json_object
# ---------------------------------------------------------------------------
def test_extract_plain_json():
    obj = mb._extract_json_object(json.dumps(_valid_brief()))
    assert obj["regime"] == "risk_on"


def test_extract_from_markdown_fence():
    raw = "Here is the brief:\n```json\n" + json.dumps(_valid_brief()) + "\n```\nDone."
    obj = mb._extract_json_object(raw)
    assert obj is not None and obj["session"] == "morning"


def test_extract_with_surrounding_prose():
    raw = "Sure! " + json.dumps(_valid_brief()) + " (let me know if you need changes)"
    obj = mb._extract_json_object(raw)
    assert obj is not None and obj["tradeable"] == ["NVDA", "AAPL"]


def test_extract_handles_braces_in_strings():
    b = _valid_brief()
    b["note"] = "uses {curly} braces in the note"
    obj = mb._extract_json_object(json.dumps(b))
    assert obj["note"] == "uses {curly} braces in the note"


def test_extract_returns_none_on_garbage():
    assert mb._extract_json_object("no json here at all") is None


# ---------------------------------------------------------------------------
# generate_market_brief — end to end with stubs
# ---------------------------------------------------------------------------
def test_generate_success_local_file(monkeypatch, tmp_path):
    # NOFX unset → local-file fallback.
    monkeypatch.setattr(mb, "NOFX_BRIEF_URL", "")
    monkeypatch.setattr(mb, "_BRIEF_DIR", tmp_path / "brief")
    # No statdata configured → calendar fetch returns "".
    monkeypatch.setattr(mb, "statdata_client", None)
    mb.set_ai_query(_stub_ai_query([json.dumps(_valid_brief())]))

    result = asyncio.run(mb.generate_market_brief("morning"))

    assert result["ok"] is True
    assert result["session"] == "morning"
    assert result["attempts"] == 1
    assert result["push"]["pushed"] is False
    # File was written and is valid against the schema.
    out = tmp_path / "brief" / "morning.json"
    assert out.exists()
    written = json.loads(out.read_text())
    # A WIRE-PAYLOAD alakja: az emberi mezok NELKUL, mert azokat a push elott
    # szandekosan kiszedjuk. Ezert `require_human=False` — a sajat kiszedesunket
    # nem jelenthetjuk hianynak.
    assert mb.validate_brief(written, require_human=False) == []
    assert "macro_review" not in written and "telegram_digest" not in written, \
        "az emberi mezok bekerultek a NOFX wire-payloadba"


def test_generate_forces_contract_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "NOFX_BRIEF_URL", "")
    monkeypatch.setattr(mb, "_BRIEF_DIR", tmp_path / "brief")
    monkeypatch.setattr(mb, "statdata_client", None)
    # Model drifts the session; generator must overwrite it to the requested one.
    drifted = _valid_brief()
    drifted["session"] = "afternoon"
    mb.set_ai_query(_stub_ai_query([json.dumps(drifted)]))

    result = asyncio.run(mb.generate_market_brief("morning"))
    assert result["ok"] is True
    assert result["brief"]["session"] == "morning"


def test_generate_retries_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "NOFX_BRIEF_URL", "")
    monkeypatch.setattr(mb, "_BRIEF_DIR", tmp_path / "brief")
    monkeypatch.setattr(mb, "statdata_client", None)
    # First response = no JSON; second = valid.
    fake = _stub_ai_query(["sorry, I could not produce JSON", json.dumps(_valid_brief())])
    mb.set_ai_query(fake)

    result = asyncio.run(mb.generate_market_brief("afternoon"))
    assert result["ok"] is True
    assert result["attempts"] == 2
    assert fake.calls["n"] == 2


def test_generate_all_invalid_returns_not_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "NOFX_BRIEF_URL", "")
    monkeypatch.setattr(mb, "_BRIEF_DIR", tmp_path / "brief")
    monkeypatch.setattr(mb, "statdata_client", None)
    mb.set_ai_query(_stub_ai_query(["garbage", "still garbage", "nope"]))

    result = asyncio.run(mb.generate_market_brief("morning"))
    assert result["ok"] is False
    assert result["attempts"] == 3
    assert "validation_errors" in result


def test_generate_bad_session():
    result = asyncio.run(mb.generate_market_brief("evening"))
    assert result["ok"] is False
    assert "session" in result["error"]


def test_generate_no_ai_query_wired(monkeypatch):
    monkeypatch.setattr(mb, "_ai_query_func", None)
    result = asyncio.run(mb.generate_market_brief("morning"))
    assert result["ok"] is False
    assert "ai_query" in result["error"]


# ===========================================================================
# ECONOMIC BRIEF — 2026-08-30
# ===========================================================================
#
# MIERT VALTOZOTT. Ket lelet ugyanarrol a briefrol:
#   * a `NOFX_BRIEF_URL` a produkcioban NINCS beallitva, es a `MARKET_BRIEF_DIR`
#     sem — a gepi payload egy efemer konyvtarba ment, amit minden deploy
#     elmosott. A NOFX-fogyaszto NEM LETEZETT;
#   * a Kommandant viszont Telegramon MEGKAPJA es EMBERKENT olvassa.
# Vagyis a brief valodi kozonsege vegig egy ember volt, mikozben a neve, a
# tartalma es a menetrendje egy botnak szolt. Ezek a tesztek az uj sulypontot
# orzik: magyar/EU makro-szemle elol, gepi mezok hatul, EGY renderelo.

def test_a_makro_kontextus_lefedi_magyarorszagot():
    """A brief HU/EU makrot is kap, nem csak amerikait. Enelkul a "magyar
    gazdasagi szemle" rovat forras nelkul maradna — es a modell a betanitasi
    memoriabol potolna."""
    spec = json.loads(mb._build_data_context())
    # A LEFEDETTSEGET merjuk, nem a preset NEVET: a HU/EA reszt a magas szintu
    # `get_macro_indicator` adja, mert a `hu_macro` preset 46 hivasa a HETI
    # riportra van meretezve. Ha valaki holnap masik forrasra cserel, a teszt
    # attol meg jo kerdest tesz fel: benne van-e Magyarorszag?
    orszagok = {c["args"].get("country") for c in spec["series"]
                if c["tool"] == "get_macro_indicator"}
    assert {"HU", "EA"} <= orszagok, "a magyar/eurozonas makro nincs a briefben"
    hu_mutatok = {c["args"]["indicator"] for c in spec["series"]
                  if c["tool"] == "get_macro_indicator" and c["args"].get("country") == "HU"}
    for kell in ("cpi", "policy_rate", "unemployment", "gdp_growth"):
        assert kell in hu_mutatok, f"hianyzik a magyar mutato: {kell}"
    assert ("US", "cpi") in {(c["args"].get("country"), c["args"].get("indicator"))
                             for c in spec["series"]
                             if c["tool"] == "get_macro_indicator"}, "nincs amerikai makró"
    assert "hu_markets" in spec["presets"], "nincs magyar tozsdei adat"
    regiok = {c["args"].get("region") for c in spec["series"]
              if c["tool"] == "get_economic_calendar"}
    assert {"US", "EU"} <= regiok, "csak az amerikai naptart huzzuk"
    # A MERET is allitas: 69 hivassal a szintezis elesben prozat adott.
    import _statdata_client as _sd
    n = sum(len(_sd.DATA_PRESETS[p]) for p in spec["presets"]) + len(spec["series"])
    assert n <= 45, f"{n} adathivas — a prompt megint elnyomja a szintezist"


def test_az_emberi_mezok_nelkul_a_brief_bukik():
    """FAIL-CLOSED: gepi mezok hibatlanul, emberi resz nelkul = a Kommandant
    egy mezolistat kap. A retry-hurok ezt hibanak veszi es ujraprobal."""
    for hianyzo in ("macro_review", "telegram_digest"):
        b = _valid_brief()
        del b[hianyzo]
        errs = mb.validate_brief(b)
        assert any(hianyzo in e for e in errs), f"{hianyzo} hianya atment"
    ures = _valid_brief()
    ures["macro_review"] = "   "
    assert mb.validate_brief(ures), "az URES makro-szemle atment"


def test_egy_renderelo_minden_feluletre(monkeypatch, tmp_path):
    """A Kommandant Telegramon kapja, de itt is olvassa. Ha a ket felulet
    kulon allitana elo a szoveget, ket kulon briefre csusznanak szet — ezert a
    generalas eredmenye MAGA hordozza a kikuldott szoveget."""
    monkeypatch.setattr(mb, "NOFX_BRIEF_URL", "")
    monkeypatch.setattr(mb, "_BRIEF_DIR", tmp_path / "brief")
    monkeypatch.setattr(mb, "statdata_client", None)
    kikuldott = []

    async def _fake_push(text):
        kikuldott.append(text)
    monkeypatch.setattr(mb, "_telegram_push_func", _fake_push)
    mb.set_ai_query(_stub_ai_query([json.dumps(_valid_brief())]))

    result = asyncio.run(mb.generate_market_brief("morning"))

    assert result["ok"] is True
    assert kikuldott, "semmi nem ment ki Telegramra"
    assert result["telegram_text"] == kikuldott[0], \
        "a hivo mas szoveget kap, mint ami Telegramra ment"


def test_a_telegram_uzenet_a_gazdasagi_szemleval_kezdodik(monkeypatch):
    """A SORREND TERMEK-DONTES: a brief celja az emberi szemle; a rezsim/
    kockazati keret egy botnak keszult mezo, ami ma senkihez nem jut el."""
    b = _valid_brief()
    digest = b.pop("telegram_digest")
    makro = b.pop("macro_review")
    forrasok = b.pop("sources")
    text = mb.format_brief_telegram(b, digest, makro, forrasok)

    assert "Economic Brief" in text
    assert "NOFX" not in text, "a bot neve maradt a fejlecben"
    assert text.index("Gazdasági szemle") < text.index("Rezsim:"), \
        "a gepi mezok elore kerultek az emberi szemle ele"
    assert "KSH" in text, "a forrasok elvesztek a renderelesbol"
    assert len(text) < 4000, "a Telegram-plafon folott vagyunk"


def test_a_makro_szemle_nelkuli_regi_brief_sem_dob(monkeypatch):
    """Visszafele: egy archivalt, emberi mezok nelkuli regi brief renderelese
    nem eshet szet — csak rovidebb lesz."""
    b = _valid_brief()
    b.pop("telegram_digest")
    b.pop("macro_review")
    text = mb.format_brief_telegram(b)
    assert "Economic Brief" in text and "Rezsim:" in text


def test_a_forrasok_a_vegen_vannak_egy_blokkban():
    """A HAZSZABALY (feedback_citation_at_end): emberi formatum, EGY blokkban,
    a VEGEN — nem inline.

    A KIVALTO ESET: az elso eles Economic Brief minden szam moge kiirt egy
    `[forras, idoszak]` cimket. A Kommandant lelete: "ezek a forrásmegjelölések
    a szövegben zavaróak. A forrásoknak a szöveg végén kell megjelenni."
    A promptba EN irtam bele a rossz szabalyt.
    """
    b = _valid_brief()
    digest = b.pop("telegram_digest")
    makro = b.pop("macro_review")
    forrasok = b.pop("sources")
    text = mb.format_brief_telegram(b, digest, makro, forrasok)

    assert "📚" in text and "Források" in text
    # A forras-blokk a szoveg VEGEN all, a makro-szemle UTAN:
    assert text.index("Gazdasági szemle") < text.index("Források")
    for f in forrasok:
        assert f in text
    # És a szemle SZOVEGE tiszta: nincs benne szogletes-zarojeles cimke.
    assert "[" not in makro, "a makro-szemle megint inline forrascimkeket hordoz"


def test_forrasok_nelkul_a_brief_bukik():
    """A provenancia nem opcio: csak a HELYE valtozott, nem a letezese."""
    b = _valid_brief()
    del b["sources"]
    assert any("sources" in e for e in mb.validate_brief(b))
    b["sources"] = []
    assert any("sources" in e for e in mb.validate_brief(b))
    # A wire-payloadban viszont nem kerjuk szamon (ott emberi mezo nincs):
    b2 = _valid_brief()
    for k in ("sources", "macro_review", "telegram_digest"):
        del b2[k]
    assert mb.validate_brief(b2, require_human=False) == []


def test_a_rendkivuli_elmozdulas_ket_forrast_kovetel():
    """A 2026-08-27-i lelet ('egyidejű kockázatkerülés' narratíva egy meg nem
    erosített 5%-os aranymozgásra) eddig CSAK a memoriaban elt, a promptban nem."""
    p = mb._build_prompt("morning", "2026-08-30T06:30Z", "2026-08-30T12:30Z", "")
    assert "3%" in p and "1.5%" in p, "nincs anomalia-kuszob a promptban"
    assert "single quote" in p or "single Yahoo" in p


def test_a_gdp_novekedes_kell_nem_a_szint():
    """Elesben merve: `gdp` a HU-ra 36376.0 millio EUR SZINTET adott. Egy
    briefben ez hasznalhatatlan — az olvasot az 1,7% erdekli."""
    spec = json.loads(mb._build_data_context())
    mutatok = {(c["args"]["country"], c["args"]["indicator"])
               for c in spec["series"] if c["tool"] == "get_macro_indicator"}
    assert ("HU", "gdp_growth") in mutatok
    assert ("HU", "gdp") not in mutatok, "a nyers GDP-SZINT visszakerult"


def test_a_forras_fejlec_PONTOSAN_EGYSZER_jelenik_meg():
    """AZ ELSO ELES VALTOZAT HAROM "📚 Források" BLOKKOT KULDOTT.

    A fejlec-dedup az utolso HAROM sorban kereste a fejlecet; harom forras utan
    az kicsuszott az ablakbol, es ujra kiirodott. Szabotazs-teszt: 3-nal TOBB
    forrassal fut, mert harommal a hiba meg nem latszott."""
    b = _valid_brief()
    digest = b.pop("telegram_digest")
    makro = b.pop("macro_review")
    b.pop("sources")
    sok = [f"Forras {i} — adat {i}" for i in range(1, 10)]
    text = mb.format_brief_telegram(b, digest, makro, sok)
    assert text.count("Források") == 1, \
        f"{text.count('Források')} forras-fejlec keletkezett egy helyett"
    for f in sok:
        assert f in text


def test_a_cjk_szivargas_bukast_okoz():
    """ELESBEN MEGTORTENT: "a低 VIX vonzó a részvénypiacnak" — igy ment ki a
    Kommandantnak. Nem stilus-kerdes: az olvaso nem tudja elolvasni a sajat
    briefjet. A retry-hurok javitsa, ne a szem."""
    b = _valid_brief()
    b["macro_review"] = "MAGYARORSZAG: a低 VIX vonzo a reszvenypiacnak."
    errs = mb.validate_brief(b)
    assert any("CJK" in e for e in errs), "a kinai karakter atment"
    # A tiszta magyar szoveg NEM bukhat el (szabotazs: ne legyen tulzo a kapu):
    tiszta = _valid_brief()
    tiszta["macro_review"] = "MAGYARORSZÁG: az árfolyam 364,67 — ő, ű, ő, éáí."
    assert mb.validate_brief(tiszta) == []


def test_az_irany_ket_megfigyelest_kovetel():
    """A SZINT NEM MONDJA MEG AZ IRANYT.

    A brief azt irta: "az MNB augusztus 25-en 5,5%-on TARTOTTA az alapkamatot"
    — egyetlen `policy_rate: 5.5` szintbol. Az MNB CSOKKENTETTE oda. Egy
    kitalalt irany es egy kitalalt datum, egy mondatban.
    """
    p = mb._build_prompt("morning", "2026-08-30T06:30Z", "2026-08-30T12:30Z", "")
    assert "TWO observations" in p and "tartotta" in p, \
        "nincs irany-szabaly a promptban"
    assert "decision_date" in p, "a dontesi datumra nincs szabaly"
    # És az ADAT is meglegyen hozzá, ne csak a tiltás:
    spec = json.loads(mb._build_data_context())
    assert any(c["tool"] == "get_policy_rates" for c in spec["series"]), \
        "nincs olyan forras, amibol az irany LEVEZETHETO — a tiltas onmagaban " \
        "csak elnemitja a briefet, nem teszi pontosabba"


def test_a_tartalek_motor_az_utolso_proban_lep_be(monkeypatch, tmp_path):
    """AZ SF_CHAT MODELLVÁLTÁSA HTTP-HIBÁRA INDUL. Van rosszabb eset: 200-as
    válasz használhatatlan tartalommal (üres törzs, próza a JSON helyett).
    Akkor semmi nem vált, mert semmi nem "hibázott" — a "használható"-t csak a
    HÍVÓ tudja megítélni, mert nála van a séma.

    2026-08-30: három próba, ÜRES / PRÓZA / ÜRES, mind HTTP 200, nulla brief.
    """
    monkeypatch.setattr(mb, "NOFX_BRIEF_URL", "")
    monkeypatch.setattr(mb, "_BRIEF_DIR", tmp_path / "brief")
    monkeypatch.setattr(mb, "statdata_client", None)
    monkeypatch.setattr(mb, "_BRIEF_MODEL", "deepseek")
    monkeypatch.setattr(mb, "_BRIEF_FALLBACK", "dsflash")

    hasznalt = []

    async def _fake(**kw):
        hasznalt.append(kw["model"])
        # Az elsődleges kétszer prózát ad; a tartalék érvényes briefet.
        if kw["model"] == "deepseek":
            return json.dumps({"model": "x", "response": "Sajnos nem tudok JSON-t."})
        return json.dumps({"model": "x", "response": json.dumps(_valid_brief())})

    mb.set_ai_query(_fake)
    result = asyncio.run(mb.generate_market_brief("morning"))

    assert result["ok"] is True, "a tartalék motor nem mentette meg a briefet"
    assert hasznalt == ["deepseek", "deepseek", "dsflash"], \
        f"rossz motor-sorrend: {hasznalt} — a váltás az UTOLSÓ próbán a helye"
    assert result["served_by"] == "dsflash"
    # S-009: NÉMA CSERE NINCS.
    assert "tartalék motor" in result["telegram_text"], \
        "a Kommandant nem tudja meg, hogy nem az elsődleges motor írta"
    assert "dsflash" in result["telegram_text"]


def test_sikeres_elsodleges_eseten_nincs_csere_uzenet(monkeypatch, tmp_path):
    """Szabotázs: ha minden briefre ráírnánk a csere-figyelmeztetést, a jelzés
    elértéktelenedne."""
    monkeypatch.setattr(mb, "NOFX_BRIEF_URL", "")
    monkeypatch.setattr(mb, "_BRIEF_DIR", tmp_path / "brief")
    monkeypatch.setattr(mb, "statdata_client", None)
    mb.set_ai_query(_stub_ai_query([json.dumps(_valid_brief())]))
    result = asyncio.run(mb.generate_market_brief("morning"))
    assert result["ok"] is True
    assert "tartalék motor" not in result["telegram_text"]


def test_minden_regio_RATAT_kap_nem_szintet():
    """EGY ELV, HAROM REGIO. A `us_macro` preset nyers FRED-sorokat ad: GDP
    SZINTET milliard dollarban es CPI INDEXPONTOT. A brief ebbol azt irta, hogy
    "az amerikai inflacio juliusban 332,813-as indexerteken allt" es "a masodik
    negyedeves amerikai GDP 32 486 milliard dollar" — mindketto igaz szam es
    hasznalhatatlan mondat. Ratakent: US CPI 3,30%, core 2,47%.
    """
    spec = json.loads(mb._build_data_context())
    par = {(c["args"].get("country"), c["args"].get("indicator"))
           for c in spec["series"] if c["tool"] == "get_macro_indicator"}
    for orszag in ("HU", "EA", "US"):
        for mutato in ("cpi", "core_cpi", "policy_rate", "unemployment"):
            assert (orszag, mutato) in par, f"hianyzik: {orszag}/{mutato}"
    assert "us_macro" not in spec["presets"], \
        "a nyers FRED-szintek visszakerultek — a modell indexpontot ir inflacionak"


def test_a_szint_es_a_forras_szabaly_a_promptban_van():
    p = mb._build_prompt("morning", "2026-08-30T06:30Z", "2026-08-30T12:30Z", "")
    assert "INDEX-SZINT NEM INFLACIO" in p
    assert "MINDEN SZAMNAK LEGYEN FORRAS-SORA" in p, \
        "egy szam forras-sor nelkul is bekerulhet a szemlebe"


def test_a_gyorstajekoztatok_mindket_forrasrol_bejonnek():
    """AZ INTRA-YEAR IGAZSAG A FLASH-KIADVANYOKBAN VAN.

    A strukturalt idosorok az utolso LEZART idoszakig tartanak. A brief EA
    maginflacioja ezert allt 2025 DECEMBEREN, nyolc honappal a valosag mogott —
    mikozben az Eurostat flash 2026 JULIUSAT hozta. Amikor a `hu_macro`
    presetet kivettem, vele estek ki az Eurostat-flashek is; ez a teszt orzi,
    hogy ne tunjenek el megegyszer.

    Es tobbet ernek, mint amennyinek latszanak: a `description` mezo (merve
    2026-08-30) ertek + idoszak + ELOZO ERTEK + datum — vagyis az IRANY is
    levezetheto belole.
    """
    spec = json.loads(mb._build_data_context())
    forrasok = {c["args"]["source"] for c in spec["series"]
                if c["tool"] == "get_flash_releases"}
    assert {"ksh", "eurostat"} <= forrasok, \
        f"hianyzo flash-forras: {forrasok} — az intra-year adat elveszik"
    temak = {(c["args"]["source"], c["args"]["query"]) for c in spec["series"]
             if c["tool"] == "get_flash_releases"}
    assert ("eurostat", "inflation") in temak, "nincs EA inflacios gyorstajekoztato"


def test_a_prompt_a_frissebb_idoszakot_valasztja():
    """A modellnek KI KELL MONDANI, hogy a flash frissebb lehet — kulonben a
    strukturalt sort veszi ervenyesnek, mert az 'hivatalosabbnak' latszik."""
    p = mb._build_prompt("morning", "2026-08-30T06:30Z", "2026-08-30T12:30Z", "")
    assert "GYORSTAJEKOZTATO FRISSEBB" in p
    assert "Compare the `period` fields" in p, \
        "nincs eldontesi szabaly a ket forras kozott"


def test_a_kitalalt_OK_tiltva_van():
    """A SZAM JO VOLT, A TORTENET KITALALT — es a gepi mezok a tortenetet
    kovettek.

    2026-08-31, eles reggeli brief: "a Fed-elnök Warsh inflációellenes
    retorikája nyomán emelkedő hozamok", regime=risk_off, kockazati keret 25%.
    A kapott adatban NEM VOLT semmilyen Fed-nyilatkozat — es a szamok az
    ellenkezojet mutattak: VIX 14,43, S&P az 52 hetes csucs kozeleben,
    crowd=complacent.

    Az anomalia-szabaly csak az ARAKRA vonatkozott; az OKOT nem vedte senki.
    """
    p = mb._build_prompt("morning", "2026-08-31T06:30Z", "2026-08-31T12:30Z", "")
    assert "AZ OKOT SEM TALALHATOD KI" in p
    assert "NAMED person" in p
    assert "A REZSIM A SZAMOKBOL KOVETKEZZEN" in p, \
        "a rezsim tovabbra is egy kitalalt narrativabol johet"
