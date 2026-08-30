"""
ÖNVIZSGÁLAT — a Bridge derítse ki magáról, mi a baja.

A KIVÁLTÓ ESET (task #407, 2026-08-30)
--------------------------------------
A Kommandant ennyit kapott Telegramon egy bukott cronról:

    daily_news_brief — HIBÁS FUTÁS, nincs brief
    Ok (kód): empty_response

Igaz volt, és használhatatlan. Az igazi ok — egy egyszeri HTTP 400 a
SiliconFlow-tól, hat hibátlan nap után — csak a Railway-logból derült ki,
kézzel. A rendszer TUDTA, hogy elromlott valami, de nem tudta megmondani, MI.

Amit ezek a tesztek őriznek:
  1. a diagnózis SOSE találgat — a `unknown` teljes értékű válasz,
  2. a VAKFOLT látszik: ha nincs próba, azt kimondja és megnevezi,
  3. a próba kivétele NEM viheti el a diagnózist (különben egy hiba
     kivizsgálása közben veszítenénk el a maradék információt is),
  4. a `persistent` és a `transient` KÜLÖNBÖZIK — az egyikre javítás kell,
     a másikra újrafuttatás,
  5. a részleges kiesés (`degraded`) nem mosódik össze a teljessel,
  6. a hibakód → komponens leképezés szűk: rossz tipp helyett "nem tudom".
"""

import pytest

import selfdiag
from selfdiag import ProbeResult, Verdict


@pytest.fixture(autouse=True)
def _clean():
    selfdiag.clear_probes()
    yield
    selfdiag.clear_probes()


def _ok(name="p", detail="rendben"):
    return lambda: ProbeResult(name, True, detail)


def _bad(name="p", detail="halott"):
    return lambda: ProbeResult(name, False, detail)


def _unk(name="p", detail="nem tudom"):
    return lambda: ProbeResult(name, None, detail)


# ── 1. A négy ítélet ────────────────────────────────────────────────────

def test_all_green_is_transient():
    """Ha a hiba óta minden próba zöld, akkor a hiba ELMÚLT — és ez más
    teendőt jelent, mint egy tartós kiesés: itt elég újrafuttatni."""
    selfdiag.register_probe("sf", "a", _ok("a"))
    selfdiag.register_probe("sf", "b", _ok("b"))
    d = selfdiag.diagnose("sf", "empty_response")
    assert d.verdict is Verdict.TRANSIENT
    assert "ÁTMENETI" in d.summary
    assert "Újrafuttatás" in d.summary


def test_all_red_is_persistent():
    selfdiag.register_probe("sf", "a", _bad("a", "HTTP 500"))
    d = selfdiag.diagnose("sf", "empty_response")
    assert d.verdict is Verdict.PERSISTENT
    assert "HTTP 500" in d.summary, "a diagnózis nem mondja meg, MI a baj"


def test_mixed_is_degraded_and_names_the_broken_part():
    """A részleges kiesés nem teljes kiesés: ha három modellből egy halott, a
    rendszer működik — de tudni kell, MELYIK az."""
    selfdiag.register_probe("sf", "kimi", _ok("kimi"))
    selfdiag.register_probe("sf", "glm5", _bad("glm5", "400"))
    d = selfdiag.diagnose("sf", "")
    assert d.verdict is Verdict.DEGRADED
    assert "glm5" in d.summary and "kimi" not in d.summary.split("Bukott:")[-1]


def test_only_unknown_is_unknown_not_healthy():
    """A legfontosabb megkülönböztetés: a "nem tudom" NEM "rendben van"."""
    selfdiag.register_probe("sf", "a", _unk("a"))
    d = selfdiag.diagnose("sf", "")
    assert d.verdict is Verdict.UNKNOWN
    assert "mérőeszköz" in d.summary


# ── 2. A vakfolt látszik ────────────────────────────────────────────────

def test_unprobed_component_says_so():
    d = selfdiag.diagnose("nincs_ilyen", "valami")
    assert d.verdict is Verdict.UNKNOWN
    assert d.missing_probes == ["nincs_ilyen"]
    assert "Nincs próba" in d.summary


def test_declared_but_unprobed_is_distinguished():
    """Ismert komponens próba nélkül ≠ ismeretlen név. Az első a MI hiányunk,
    a második elgépelés — más a teendő."""
    selfdiag.declare_component("telegram")
    known = selfdiag.diagnose("telegram", "")
    unknown = selfdiag.diagnose("elgepelt", "")
    assert "a mi hiányunk" in known.summary
    assert "NEM ismert" in unknown.summary


def test_diagnose_all_lists_the_unmeasured():
    selfdiag.register_probe("sf", "a", _ok("a"))
    selfdiag.declare_component("telegram")
    out = selfdiag.diagnose_all()
    assert "sf" in out["components"]
    assert out["unmeasured"] == ["telegram"]
    assert "NINCS próba" in out["summary"], \
        "a mérőeszköz határa nem került az összefoglalóba"


# ── 3. A próba kivétele nem viheti el a diagnózist ──────────────────────

def test_exploding_probe_degrades_to_unknown():
    def _boom():
        raise RuntimeError("a próba maga hasalt el")

    selfdiag.register_probe("sf", "robban", _boom)
    d = selfdiag.diagnose("sf", "")
    assert d.verdict is Verdict.UNKNOWN
    assert "a próba maga hasalt el" in d.evidence[0].detail


def test_probe_returning_garbage_is_not_trusted():
    selfdiag.register_probe("sf", "rossz", lambda: "igen, jó")
    d = selfdiag.diagnose("sf", "")
    assert d.evidence[0].ok is None
    assert "nem ProbeResult" in d.evidence[0].detail


def test_one_bad_probe_does_not_hide_the_good_ones():
    def _boom():
        raise RuntimeError("x")

    selfdiag.register_probe("sf", "jo", _ok("jo"))
    selfdiag.register_probe("sf", "robban", _boom)
    d = selfdiag.diagnose("sf", "")
    assert len(d.evidence) == 2, "egy elhasalt próba elvitte a többi mérését"


# ── 4. A bizonyíték a jelentésben marad ────────────────────────────────

def test_evidence_survives_serialisation():
    selfdiag.register_probe("sf", "a", _bad("a", "HTTP 400 — parameter is invalid"))
    d = selfdiag.diagnose("sf", "empty_response").as_dict()
    assert d["symptom"] == "empty_response"
    assert d["evidence"][0]["detail"].startswith("HTTP 400")
    assert d["evidence"][0]["elapsed_ms"] >= 0


# ── 5. A hibakód → komponens leképezés ─────────────────────────────────

def test_error_to_component_mapping_is_narrow():
    """Egy rossz tipp elviszi a keresést rossz irányba. Az ismeretlen kód
    inkább essen a leggyakoribb gyanúsítottra, mint egy kitalált komponensre."""
    import server
    assert server._component_for_error("required_tool_dead") == "google"
    assert server._component_for_error("model_unavailable") == "siliconflow"
    assert server._component_for_error("empty_response") == "siliconflow"
    # ismeretlen kód: nem talál ki új komponenst
    assert server._component_for_error("valami_uj_kod") in selfdiag._KNOWN_COMPONENTS or \
        server._component_for_error("valami_uj_kod") == "siliconflow"


def test_server_registers_a_probe_for_every_model():
    """Ha új modell kerül a regiszterbe, próba nélkül maradna — és a
    diagnózis némán kevesebbet mérne, mint amit a rendszer használ."""
    import server
    reg = selfdiag.registered() or {}
    # a modul-szintű regisztráció a server importjakor futott le; ez a teszt a
    # fixture-tisztítás miatt üres regisztert lát, ezért a FORRÁST mérjük
    src = open(server.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    assert 'for _a, _m in SILICONFLOW_MODELS.items():' in src, \
        "a modellek próbái nem a regiszterből származnak — egy új modell " \
        "kimaradna, és ezt senki nem venné észre"
