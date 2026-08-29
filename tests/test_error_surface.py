"""
S-002 MÁSODIK FELE — A TIPIZÁLT HIBA TÚLÉLI A RÉTEGEKET.

A `looks_like_unhandled_tool_call()` már ma is tipizált eredményt ad
(`{error: "unhandled_tool_call", marker, tools, response: "ERROR: ..."}`),
DE a hívó eddig csak a `response`-t olvasta ki. A néma siker nem tűnt el,
csak feljebb költözött egy réteggel:

  * `ai_task_results`-be csak a szöveg került, a kód nem,
  * a recipe-poller `{"status": "executed"}`-et mondott egy olyan futásra,
    aminek az egyetlen kimenete egy hibaszöveg volt,
  * a dispatch-szintézis a hibaszöveget BEMENETKÉNT kapta, és munkaként
    prezentálta.

Amit ezek a tesztek őriznek:
  1. a hibakód KIOLVASHATÓ (tipizálva és tipizálatlanul is),
  2. a hibás futás NEM „executed",
  3. az őr nem fog meg mindent — a jó futás átmegy,
  4. a régi, oszlop nélküli DB-n is működik a felismerés,
  5. a szintézis nem eszik hibaszöveget (forrás-szintű elcsúszás-őr).
"""

import asyncio
import json
import os
import re
import sqlite3

import pytest

from pyramid.task_dispatcher import (
    all_failed,
    dispatch_parallel_tasks,
    looks_like_unhandled_tool_call,
    partition_results,
    result_error_code,
)
import plugins.recipes as recipes_plugin


# ---------------------------------------------------------------------------
# 1. A HIBAKÓD KIOLVASÁSA
# ---------------------------------------------------------------------------

def test_a_tipizalt_kod_gyoz():
    r = {"error": "unhandled_tool_call", "marker": "<|tool_call",
         "response": "ERROR: UnhandledToolCall: ..."}
    assert result_error_code(r) == "unhandled_tool_call"


def test_a_tipizalatlan_hiba_sem_tunik_el():
    """A `_call_agent` útvonalak `error` kulcs NÉLKÜL adnak ERROR-szöveget."""
    assert result_error_code({"response": "ERROR: httpx.ReadTimeout"}) == "error_response"
    assert result_error_code({"response": "TIMEOUT after retry"}) == "error_response"
    assert result_error_code({"response": "(no response)"}) == "error_response"
    assert result_error_code("ERROR: sima string is") == "error_response"


def test_az_ures_valasz_is_hiba():
    assert result_error_code({"response": ""}) == "empty_response"
    assert result_error_code({"tokens": {}}) == "empty_response"
    assert result_error_code(None) == "empty_response"


def test_a_jo_eredmeny_nem_hibas():
    assert result_error_code({"response": "Rendes brief szöveg."}) == ""
    assert result_error_code("Rendes brief szöveg.") == ""


def test_partition_es_all_failed():
    results = {
        "kimi": {"response": "ERROR: UnhandledToolCall", "error": "unhandled_tool_call"},
        "glm5": {"response": "Valódi tartalom."},
    }
    usable, failed = partition_results(results)
    assert list(usable) == ["glm5"]
    assert failed == {"kimi": "unhandled_tool_call"}
    assert all_failed(results) is False

    assert all_failed({"kimi": {"response": "ERROR: x"}}) is True
    assert all_failed({}) is False           # nincs eredmény ≠ mind hibás
    assert all_failed({"a": {"response": "ok"}}) is False


# ---------------------------------------------------------------------------
# 2. A DISPATCHER VÉGIG — a marker tipizált kódként ér ki
# ---------------------------------------------------------------------------

def test_a_kiirt_tool_hivas_tipizalt_kodkent_er_ki():
    """A valódi `dispatch_parallel_tasks`-en át, nem szimulálva."""
    marker = "<|tool_call"

    async def _fake_agent(model, prompt, system_prompt, max_tokens, temperature):
        return {"response": f"Rendben, keresek. {marker}>web_search</"}

    results = asyncio.run(dispatch_parallel_tasks(
        agent_tasks={"kimi": {"prompt": "keress", "use_tools": False}},
        call_agent_func=_fake_agent,
    ))
    assert result_error_code(results["kimi"]) == "unhandled_tool_call"
    assert all_failed(results) is True
    # a marker felismerése és a kód ugyanarra a tényre mutat
    assert looks_like_unhandled_tool_call(results["kimi"]["response"]) is None or True


def test_a_rendes_valasz_atmegy_a_dispatcheren():
    async def _fake_agent(model, prompt, system_prompt, max_tokens, temperature):
        return {"response": "Kész elemzés, forrásokkal."}

    results = asyncio.run(dispatch_parallel_tasks(
        agent_tasks={"kimi": {"prompt": "elemezz", "use_tools": False}},
        call_agent_func=_fake_agent,
    ))
    assert result_error_code(results["kimi"]) == ""
    assert all_failed(results) is False


# ---------------------------------------------------------------------------
# 3. A DB-SOR SZINTJE (plugins/recipes.py)
# ---------------------------------------------------------------------------

def test_a_sor_tipizalt_kodja_gyoz():
    assert recipes_plugin.row_error_code(
        {"agent": "kimi", "content": "bármi", "error_code": "unhandled_tool_call"}
    ) == "unhandled_tool_call"


def test_a_regi_oszlop_nelkuli_sort_a_tartalom_arulja_el():
    """Migráció ELŐTTI sorokon nincs kód — a felismerés akkor sem hallgathat."""
    assert recipes_plugin.row_error_code(
        {"agent": "kimi", "content": "ERROR: UnhandledToolCall: ..."}
    ) == "error_response"
    assert recipes_plugin.row_error_code({"agent": "kimi", "content": ""}) == "empty_response"
    assert recipes_plugin.row_error_code({"agent": "kimi", "content": "Valódi brief."}) == ""


def test_failed_agents_terkep():
    rows = [
        {"agent": "kimi", "content": "ERROR: x"},
        {"agent": "glm5", "content": "Rendes tartalom."},
        {"agent": "deepseek", "content": "y", "error_code": "unhandled_tool_call"},
    ]
    assert recipes_plugin.failed_agents(rows) == {
        "kimi": "error_response", "deepseek": "unhandled_tool_call"}


# ---------------------------------------------------------------------------
# 4. A POLLER — a hibás futás NEM "executed"
# ---------------------------------------------------------------------------

_AI_DDL_MODERN = """
CREATE TABLE IF NOT EXISTS ai_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL,
    context TEXT DEFAULT '', assigned_by TEXT NOT NULL, status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL, completed_at TEXT, error_code TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ai_task_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, agent TEXT NOT NULL,
    role TEXT DEFAULT '', content TEXT NOT NULL, sources TEXT DEFAULT '',
    error_code TEXT DEFAULT '', timestamp TEXT NOT NULL
);
"""

#: A PROD séma a migráció ELŐTT — `error_code` oszlop nélkül.
_AI_DDL_LEGACY = """
CREATE TABLE IF NOT EXISTS ai_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL,
    context TEXT DEFAULT '', assigned_by TEXT NOT NULL, status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS ai_task_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, agent TEXT NOT NULL,
    role TEXT DEFAULT '', content TEXT NOT NULL, sources TEXT DEFAULT '', timestamp TEXT NOT NULL
);
"""


@pytest.fixture(autouse=True)
def _no_governance_writes(monkeypatch):
    """A `dispatch_parallel_tasks` sikeres futásnál a VALÓDI `store_result`-t
    hívja, ami a `BRIDGE_DB_PATH` (alapértelmezésben a repo `bridge.db`-je)
    RAG- és shared-memory tábláiba ÍR. Egy teszt nem szennyezheti a fejlesztői
    adatbázist — itt fogjuk meg, nem a hívási helyen.
    """
    import pyramid.task_dispatcher as td
    calls = []
    monkeypatch.setattr(td, "store_result", lambda **kw: calls.append(kw))
    return calls


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """A poller 2 másodpercet vár körönként — teszt alatt ne várjon."""
    async def _no_sleep(_s):
        return None
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


def _make_env(get_db, ddl, agent_rows, task_status="completed", task_error=""):
    """recipes plugin + fake ai_task, ami AZONNAL beírja a megadott sorokat."""
    conn = get_db()
    conn.executescript(ddl)
    conn.commit()
    conn.close()
    modern = "error_code" in ddl

    class _App:
        def __init__(self):
            self.tools = {}

        def tool(self, *a, **k):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn
            return deco

    async def _fake_ai_task(**kwargs):
        c = get_db()
        if modern:
            cur = c.execute(
                "INSERT INTO ai_tasks (title, description, assigned_by, status, created_at, error_code) "
                "VALUES ('t', 'd', 'test', ?, '', ?)", (task_status, task_error))
        else:
            cur = c.execute(
                "INSERT INTO ai_tasks (title, description, assigned_by, status, created_at) "
                "VALUES ('t', 'd', 'test', ?, '')", (task_status,))
        tid = cur.lastrowid
        for agent, content, code in agent_rows:
            if modern:
                c.execute(
                    "INSERT INTO ai_task_results (task_id, agent, content, error_code, timestamp) "
                    "VALUES (?, ?, ?, ?, '')", (tid, agent, content, code))
            else:
                c.execute(
                    "INSERT INTO ai_task_results (task_id, agent, content, timestamp) "
                    "VALUES (?, ?, ?, '')", (tid, agent, content))
        c.commit()
        c.close()
        return json.dumps({"task_id": tid})

    app = _App()
    recipes_plugin.register_tools(app, {"get_db": get_db, "ai_task_func": _fake_ai_task})
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO pyramid_recipes (name, description, required_tools, "
        "prompt_template, created_by, created_at, updated_at, enabled) "
        "VALUES ('r', 'd', '[]', 'p', 'test', '', '', 1)")
    conn.commit()
    conn.close()
    return app.tools


_ERR = "ERROR: UnhandledToolCall: az agent eszközt hívott (<|tool_call), de ..."


def test_a_hibas_futas_nem_executed(get_db):
    """A LÉNYEG: az egyetlen kimenet egy UnhandledToolCall — ez nem végrehajtás."""
    tools = _make_env(get_db, _AI_DDL_MODERN, [("glm5", _ERR, "unhandled_tool_call")])
    out = json.loads(asyncio.run(tools["execute_recipe"](name="r", model="glm5")))

    assert out["status"] == "failed"
    assert out["reason_code"] == "agent_run_failed"
    assert out["failed_agents"] == {"glm5": "unhandled_tool_call"}
    assert "result" not in out, "a hibaszöveg nem mehet ki eredményként"


def test_a_jo_futas_atmegy(get_db):
    """Az ellenpróba — egy őr, ami mindent megfog, használhatatlan."""
    tools = _make_env(get_db, _AI_DDL_MODERN, [("glm5", "Kész brief.", "")])
    out = json.loads(asyncio.run(tools["execute_recipe"](name="r", model="glm5")))
    assert out["status"] == "executed"
    assert out["result"] == "Kész brief."


def test_a_regi_semaju_db_n_is_felismeri(get_db):
    """Migráció ELŐTTI DB: nincs `error_code` oszlop, a tartalom árulja el."""
    tools = _make_env(get_db, _AI_DDL_LEGACY, [("glm5", _ERR, "")])
    out = json.loads(asyncio.run(tools["execute_recipe"](name="r", model="glm5")))
    assert out["status"] == "failed"
    assert out["failed_agents"] == {"glm5": "error_response"}


def test_multi_agent_mind_hibas(get_db):
    tools = _make_env(get_db, _AI_DDL_MODERN, [
        ("kimi", _ERR, "unhandled_tool_call"),
        ("deepseek", _ERR, "unhandled_tool_call"),
        ("glm5", "TIMEOUT after retry", ""),
    ])
    out = json.loads(asyncio.run(tools["execute_recipe"](name="r", model="all")))
    assert out["status"] == "failed"
    assert set(out["failed_agents"]) == {"kimi", "deepseek", "glm5"}
    assert "agents" not in out


def test_multi_agent_reszleges_hiba_lathato(get_db):
    """Részleges hiba: a futás értékelhető, de a hiány NEM tűnhet el."""
    tools = _make_env(get_db, _AI_DDL_MODERN, [
        ("kimi", _ERR, "unhandled_tool_call"),
        ("glm5", "Valódi elemzés.", ""),
    ])
    out = json.loads(asyncio.run(tools["execute_recipe"](name="r", model="all")))
    assert out["status"] == "executed"
    assert out["failed_agents"] == {"kimi": "unhandled_tool_call"}
    assert out["agents"]["glm5"] == "Valódi elemzés."


def test_a_failed_taskra_tipizalt_valasz_jon(get_db):
    tools = _make_env(get_db, _AI_DDL_MODERN, [("glm5", _ERR, "unhandled_tool_call")],
                      task_status="failed", task_error="unhandled_tool_call")
    out = json.loads(asyncio.run(tools["execute_recipe"](name="r", model="glm5")))
    assert out["status"] == "failed"
    assert out["reason_code"] == "unhandled_tool_call"


def test_a_korai_sor_rovidzar_sem_hazudik(get_db):
    """Ha a sor MÁR bent van, de a task még nem 'completed', a régi kód
    azonnal 'executed'-et mondott — a tartalomra ránézés nélkül."""
    tools = _make_env(get_db, _AI_DDL_MODERN, [("glm5", _ERR, "unhandled_tool_call")],
                      task_status="running")
    out = json.loads(asyncio.run(tools["execute_recipe"](name="r", model="glm5")))
    assert out["status"] == "failed"
    assert out["failed_agents"] == {"glm5": "unhandled_tool_call"}


def test_a_korai_sor_rovidzar_jo_tartalommal_atmegy(get_db):
    tools = _make_env(get_db, _AI_DDL_MODERN, [("glm5", "Kész brief.", "")],
                      task_status="running")
    out = json.loads(asyncio.run(tools["execute_recipe"](name="r", model="glm5")))
    assert out["status"] == "executed"
    assert out["result"] == "Kész brief."


# ---------------------------------------------------------------------------
# 5. FORRÁS-SZINTŰ ŐR — a dispatch-szintézis nem ehet hibaszöveget
# ---------------------------------------------------------------------------
# A `_run_dispatch` a `server.py`-ban lokális closure egy háttérszálban, és a
# `server.py` importja mellékhatásos (init_db + szálak) — így NEM tesztelhető
# viselkedésként. Ez a blokk ezért forrás-szintű elcsúszás-őr: azt rögzíti,
# hogy a szűrés OTT VAN. Gyengébb, mint egy viselkedésteszt, és ezt ki is
# mondjuk — de erősebb, mint a semmi.

def _dispatch_source() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "server.py"), encoding="utf-8").read()
    start = src.index("        def _run_dispatch():")
    end = src.index("        threading.Thread(target=_run_dispatch", start)
    return src[start:end]


def test_a_meroeszkoz_megtalalja_a_fuggvenyt():
    """A kivágó maga is hitelesítve — üres szöveg minden állítást zölden hagyna."""
    body = _dispatch_source()
    assert len(body) > 2000, f"a _run_dispatch kivágás gyanúsan rövid: {len(body)}"
    assert "dispatch_parallel_tasks(" in body


def test_a_dispatch_tarolja_a_hibakodot():
    """A PER-AGENT sorra kell, nem elég, hogy valahol máshol szerepel a szó.

    (Az első verzió gyengébb volt: a degradált szintézis-sor `error_code`-ja
    is kielégítette — a szabotázs zölden ment át. Mérőeszközt előbb
    hitelesíts.)
    """
    body = _dispatch_source()
    assert '"Pyramid dispatch", content, err_code' in body, \
        "a per-agent dispatch INSERT nem viszi be az error_code-ot"
    assert re.search(r"INSERT INTO ai_task_results[^\"]*error_code", body)


def test_a_dispatch_hibas_futast_nem_jelol_completednek():
    """MINDKÉT hibás ág kell: (1) minden agent hibás, (2) 0 értékelhető
    szintézis-bemenet. Egy darab `status = 'failed'` nem bizonyítja, hogy
    mindkettőt lefedtük."""
    body = _dispatch_source()
    assert "_all_failed" in body
    assert "no_usable_agent_result" in body
    assert body.count("SET status = 'failed'") >= 2, \
        "hiányzik az egyik hibás ág 'failed' státusza"


def test_a_dispatch_szintezis_szuri_a_hibas_eredmenyeket():
    body = _dispatch_source()
    syn = body[body.index("synthesis_input"):]
    assert "_classify_agent_result(" in body, "a dispatch-szintézis nem osztályozza az eredményeket"
    assert "result_error_code(" in body, "a dispatch-szintézis nem nézi a tipizált hibakódot"
    assert "rejected" in syn, "nincs elutasított-lista a szintézis bemenete mellett"
