"""
S-002 — FAIL-CLOSED ÜTEMEZETT KIMENETEK.

A hiba: a `daily_briefing` recipe `required_tools=[gmail_poll, calendar_poll,
list_tasks]`-t deklarál, és 5,5 héten át minden reggel lefutott halott Gmail
és Naptár mellett. A mező LÉTEZETT, de senki nem nézte meg.

Amit ezek a tesztek őriznek:
  1. a halott kötelező tool MEGÁLLÍTJA a futást (nincs kimenet),
  2. az ok TIPIZÁLT és gépi (nem szabad szöveg),
  3. a kihagyás LÁTHATÓ marad (perzisztens sor, nem csak log),
  4. az őr NEM fog meg mindent — az egészséges futás átmegy,
  5. a mérőeszköz hibája NEM halál-bizonyíték (UNKNOWN, nem DEAD).
"""

import asyncio
import json
import sqlite3

import pytest

import recipe_health as rh
from recipe_health import SkipReason, ToolHealth, ToolProbeResult


@pytest.fixture(autouse=True)
def _clean_probes():
    """A próba-regiszter globális — minden teszt tiszta lappal indul."""
    rh.clear_probes()
    yield
    rh.clear_probes()


def _healthy(name="x"):
    return lambda: ToolProbeResult(name, ToolHealth.HEALTHY, "ok")


def _dead(name="x", detail="dead"):
    return lambda: ToolProbeResult(name, ToolHealth.DEAD, detail)


# ---------------------------------------------------------------------------
# 1. A KAPU MAGA
# ---------------------------------------------------------------------------

def test_ures_required_tools_atmegy():
    """Aminek nincs deklarált függősége, azt nem lehet függőség miatt megállítani."""
    for raw in (None, "", "[]", []):
        v = rh.check_required_tools("noop", raw)
        assert v.ok, raw
        assert v.reason is None


def test_a_halott_kotelezo_tool_megallitja_a_futast():
    """A LÉNYEG: a daily_briefing forgatókönyve — halott Gmail → nincs kimenet."""
    rh.register_probe("gmail_poll", _dead("gmail_service", "google_service_not_initialised"))
    rh.register_probe("list_tasks", _healthy("list_tasks"))

    v = rh.check_required_tools("daily_briefing", '["gmail_poll", "list_tasks"]')

    assert v.ok is False
    assert v.dead_tools == ["gmail_poll"]
    assert v.healthy_tools == ["list_tasks"]


def test_az_ok_tipizalt_es_gepi():
    """(a) követelmény: az ok enum → stabil kód, nem szabad szöveg."""
    rh.register_probe("gmail_poll", _dead())
    v = rh.check_required_tools("daily_briefing", '["gmail_poll"]')

    assert v.reason is SkipReason.REQUIRED_TOOL_DEAD
    assert v.reason_code == "required_tool_dead"
    # A kód visszaolvasható enummá — ez a "gépi" próbája.
    assert SkipReason(v.reason_code) is SkipReason.REQUIRED_TOOL_DEAD

    d = v.to_dict()
    assert d["status"] == "skipped"
    assert d["reason_code"] == "required_tool_dead"
    assert d["dead_tools"] == ["gmail_poll"]
    # A szabad szöveg KÜLÖN mezőben van, sosem ő hordozza a döntést.
    assert isinstance(d["message"], str) and d["message"]
    assert json.loads(json.dumps(d)) == d  # tényleg szerializálható


def test_az_egeszseges_futas_atmegy():
    """Egy őr, ami mindent megfog, használhatatlan."""
    rh.register_probe("gmail_poll", _healthy())
    rh.register_probe("calendar_poll", _healthy())
    v = rh.check_required_tools("daily_briefing", '["gmail_poll", "calendar_poll"]')
    assert v.ok is True
    assert v.reason is None
    assert v.dead_tools == []


def test_a_probazatlan_tool_nem_blokkol_de_lathato():
    """UNKNOWN átmegy — különben az első deploy elnémítana MINDEN ütemezést.

    De nem tűnik el: a verdikt megnevezi, meddig ér a mérőeszköz.
    """
    v = rh.check_required_tools("weekly_macro_report", '["web_search"]')
    assert v.ok is True
    assert v.unknown_tools == ["web_search"]
    assert v.to_dict()["unknown_tools"] == ["web_search"]


def test_a_meroeszkoz_hibaja_nem_halal_bizonyitek():
    """Ha a próba MAGA hasal el, az UNKNOWN — nem DEAD.

    Máskülönben egy elgépelt próba az egész ütemezést leállítaná.
    """
    def _boom():
        raise RuntimeError("probe blew up")

    rh.register_probe("gmail_poll", _boom)
    v = rh.check_required_tools("daily_briefing", '["gmail_poll"]')
    assert v.ok is True
    assert v.unknown_tools == ["gmail_poll"]
    assert "probe_error" in v.probes[0].detail


def test_a_szerzodest_serto_proba_is_unknown():
    rh.register_probe("gmail_poll", lambda: "igen, él")  # nem ToolProbeResult
    v = rh.check_required_tools("daily_briefing", '["gmail_poll"]')
    assert v.ok is True
    assert v.probes[0].state is ToolHealth.UNKNOWN


def test_a_verdikt_a_deklaralt_neven_beszel():
    """Alias-próba: a `gmail_poll` mögött a `gmail_service` próbája ül, de a
    verdikt annak a NEVÉT mondja, amit a recipe deklarált."""
    rh.register_probe("gmail_poll", _dead("gmail_service"))
    v = rh.check_required_tools("daily_briefing", '["gmail_poll"]')
    assert v.dead_tools == ["gmail_poll"]


def test_a_szemet_required_tools_nem_dob_es_nem_blokkol():
    for raw in ("{nem json", '{"a": 1}', "null", 42):
        assert rh.parse_required_tools(raw) == []
        assert rh.check_required_tools("x", raw).ok is True


def test_veszkapcsolo_kikapcsolja_a_kaput(monkeypatch):
    rh.register_probe("gmail_poll", _dead())
    assert rh.check_required_tools("daily_briefing", '["gmail_poll"]').ok is False

    monkeypatch.setenv(rh.GATE_ENV, "off")
    assert rh.check_required_tools("daily_briefing", '["gmail_poll"]').ok is True


# ---------------------------------------------------------------------------
# 2. A CAPTURE (GOOGLE) PRÓBA — a hazug mérőeszköz ellen
# ---------------------------------------------------------------------------

def _probe_for(state):
    return rh.capture_service_probe(state, "gmail_service", "gmail_last_ok", "gmail_last_error")


def test_nincs_service_az_halott():
    p = _probe_for({"gmail_service": None})
    assert p().state is ToolHealth.DEAD


def test_a_friss_hiba_halott_akkor_is_ha_a_service_objektum_megvan():
    """EZ A HITELESÍTÉS: a `service is not None` önmagában hazudhat.

    A _init_google_services előbb írja be a build() eredményét, mint hogy a
    getProfile()-lal hitelesítené — visszavont tokennél a mező beállítva
    maradhat egy halott kliensre. A próba ezért a TÉNYLEGES hívások nyomát
    is nézi.
    """
    state = {
        "gmail_service": object(),
        "gmail_last_ok": "2026-07-20T06:00:00+00:00",
        "gmail_last_error": "2026-08-29T06:00:00+00:00",
    }
    r = _probe_for(state)()
    assert r.state is ToolHealth.DEAD
    assert "last_call_failed_at" in r.detail


def test_a_frissebb_siker_gyoz_a_regi_hiba_felett():
    state = {
        "gmail_service": object(),
        "gmail_last_error": "2026-08-29T06:00:00+00:00",
        "gmail_last_ok": "2026-08-29T07:00:00+00:00",
    }
    assert _probe_for(state)().state is ToolHealth.HEALTHY


def test_a_soha_nem_hivott_service_unknown():
    """Van kliens, de nincs bizonyíték semmire → UNKNOWN, tehát NEM blokkol."""
    assert _probe_for({"gmail_service": object()})().state is ToolHealth.UNKNOWN


# ---------------------------------------------------------------------------
# 3. A KIHAGYÁS LÁTHATÓSÁGA (perzisztens ledger)
# ---------------------------------------------------------------------------

@pytest.fixture
def skip_db(tmp_path):
    path = str(tmp_path / "skips.db")

    def _get_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    return _get_db


def test_a_kihagyas_perzisztens_nem_csak_log(skip_db):
    rh.register_probe("gmail_poll", _dead())
    v = rh.check_required_tools("daily_briefing", '["gmail_poll"]')
    rowid = rh.record_skip(skip_db, v, trigger="cron")
    assert rowid

    rows = rh.recent_skips(skip_db, hours=24)
    assert len(rows) == 1
    assert rows[0]["recipe"] == "daily_briefing"
    assert rows[0]["reason_code"] == "required_tool_dead"
    assert rows[0]["trigger"] == "cron"
    assert rows[0]["dead_tools"] == ["gmail_poll"]
    assert "daily_briefing" in rows[0]["message"]


def test_recent_skips_ures_ha_meg_nincs_tabla(skip_db):
    """Az olvasó nem robbanhat egy még nem migrált DB-n."""
    assert rh.recent_skips(skip_db, hours=24) == []
    assert rh.last_skip_by_recipe(skip_db) == {}


def test_last_skip_recipenkent_a_legutobbit_adja(skip_db):
    rh.register_probe("gmail_poll", _dead("g", "elso"))
    rh.record_skip(skip_db, rh.check_required_tools("daily_briefing", '["gmail_poll"]'))
    rh.register_probe("gmail_poll", _dead("g", "masodik"))
    rh.record_skip(skip_db, rh.check_required_tools("daily_briefing", '["gmail_poll"]'))
    rh.record_skip(skip_db, rh.check_required_tools("other", '["gmail_poll"]'))

    last = rh.last_skip_by_recipe(skip_db)
    assert set(last) == {"daily_briefing", "other"}
    assert last["daily_briefing"]["reason_code"] == "required_tool_dead"


def test_record_skip_nem_dob_rossz_db_n():
    """A naplózás hibája nem ölheti meg a cron loopot."""
    def _broken():
        raise sqlite3.OperationalError("no such database")

    rh.register_probe("gmail_poll", _dead())
    v = rh.check_required_tools("daily_briefing", '["gmail_poll"]')
    assert rh.record_skip(_broken, v) is None


# ---------------------------------------------------------------------------
# 4. INTEGRÁCIÓ — execute_recipe tényleg NEM gyárt kimenetet
# ---------------------------------------------------------------------------

def _register_recipes(fake_app, get_db, ai_task_calls):
    from plugins import recipes as recipes_plugin

    async def _fake_ai_task(**kwargs):
        ai_task_calls.append(kwargs)
        # task_id nélkül az execute_recipe azonnal visszatér (nincs pollozás)
        return json.dumps({"status": "created"})

    recipes_plugin.register_tools(fake_app, {"get_db": get_db, "ai_task_func": _fake_ai_task})
    return fake_app.tools


def _insert_recipe(get_db, name, tools):
    conn = get_db()
    # OR REPLACE: a register_tools be is seedeli a `daily_briefing`-et —
    # a teszt a SAJÁT required_tools-át akarja ráhúzni.
    conn.execute(
        "INSERT OR REPLACE INTO pyramid_recipes (name, description, required_tools, prompt_template, "
        "created_by, created_at, updated_at, enabled) VALUES (?, ?, ?, ?, 'test', '', '', 1)",
        (name, "d", tools, "Csinálj briefet."),
    )
    conn.commit()
    conn.close()


def test_execute_recipe_nem_gyart_kimenetet_halott_toollal(fake_app, get_db):
    """A doktrína próbája: az ai_task MEG SEM HÍVÓDIK."""
    calls = []
    tools = _register_recipes(fake_app, get_db, calls)
    _insert_recipe(get_db, "daily_briefing", '["gmail_poll", "list_tasks"]')
    rh.register_probe("gmail_poll", _dead("gmail_service"))

    out = json.loads(asyncio.run(tools["execute_recipe"](name="daily_briefing")))

    assert out["status"] == "skipped"
    assert out["reason_code"] == "required_tool_dead"
    assert out["dead_tools"] == ["gmail_poll"]
    assert calls == [], "halott bemenettel EGYETLEN agent-hívás sem indulhat"

    # ...és a kihagyás nyoma megmarad
    assert rh.recent_skips(get_db, hours=24)[0]["recipe"] == "daily_briefing"


def test_execute_recipe_lefut_ha_a_toolok_elnek(fake_app, get_db):
    """Az ellenpróba — az őr nem fog meg mindent."""
    calls = []
    tools = _register_recipes(fake_app, get_db, calls)
    _insert_recipe(get_db, "daily_briefing", '["gmail_poll"]')
    rh.register_probe("gmail_poll", _healthy())

    asyncio.run(tools["execute_recipe"](name="daily_briefing"))
    assert len(calls) == 1
    assert rh.recent_skips(get_db, hours=24) == []


def test_list_recipes_mutatja_a_legutobbi_kihagyast(fake_app, get_db):
    """(b) követelmény: a kihagyás ott van, ahol az ember a recipe-ekre néz."""
    calls = []
    tools = _register_recipes(fake_app, get_db, calls)
    _insert_recipe(get_db, "daily_briefing", '["gmail_poll"]')
    rh.register_probe("gmail_poll", _dead())

    asyncio.run(tools["execute_recipe"](name="daily_briefing"))
    listed = json.loads(asyncio.run(tools["list_recipes"]()))
    entry = [r for r in listed["recipes"] if r["name"] == "daily_briefing"][0]

    assert entry["last_skip"]["reason_code"] == "required_tool_dead"
    assert entry["last_skip"]["dead_tools"] == ["gmail_poll"]


def test_list_recipes_valtozatlan_ha_nincs_kihagyas(fake_app, get_db):
    """Additív mező: kihagyás nélkül a válasz alakja a régi."""
    calls = []
    tools = _register_recipes(fake_app, get_db, calls)
    _insert_recipe(get_db, "sima", "[]")
    entry = [r for r in json.loads(asyncio.run(tools["list_recipes"]()))["recipes"]
             if r["name"] == "sima"][0]
    assert "last_skip" not in entry
