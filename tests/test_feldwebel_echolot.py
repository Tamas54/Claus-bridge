"""Tests for the Feldwebel Echolot/Agora toolset (Telegram-driven agent path).

Covers the fix for shared_memory 'echolot_mcp_endpoint_referencia' (2026-07-13):
the Feldwebel bot's tool registry lacked the echolot_*/agora_* wrappers, so the
Kommandant could not command Agora comments from Telegram.

- Tool registration: echolot_query / agora / story_comments present in TOOLS.
- Dispatch: each tool routes to the right _echolot_client call (fake module).
- ECHOLOT_URL gate: disabled integration returns a clear error.
- Operator key: writes without a key fail with the human-readable message;
  with AGORA_OP_KEY_FELDWEBEL env set, agora comment goes out with the
  'pub-' story_id prefix and agent_label='Feldwebel'.
- Read paths (feed/comments/story wall read) never require an operator key.
"""

import asyncio
import json
import sys
import types

import pytest

from feldwebel.responder import TOOLS, _execute_tool, _NO_OP_KEY_MSG


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_names():
    return {t["function"]["name"] for t in TOOLS}


def _make_fake_ec(calls: dict, url: str = "https://echolot.test"):
    """Fake _echolot_client module recording every call."""
    fake = types.ModuleType("_echolot_client")
    fake.ECHOLOT_URL = url

    async def search_news(**kw):
        calls["search_news"] = kw
        return {"articles": [{"title": "T1", "source_name": "S", "url": "u",
                              "published_at": "2026-07-13T10:00", "sphere": "hu_politics"}]}

    async def fetch_news(**kw):
        calls["fetch_news"] = kw
        return {"articles": []}

    async def get_top_story_links(limit=12):
        calls["get_top_story_links"] = {"limit": limit}
        return [{"story_id": "abc123", "slug": "test-story", "url": f"{url}/story/abc123/test-story"}]

    async def agora_action(action, **kw):
        calls.setdefault("agora_action", []).append({"action": action, **kw})
        return {"ok": True, "action": action,
                "posts": [{"post_id": "p42", "title": "Essay"}] if action == "feed" else []}

    async def get_agora_comments(post_id, limit=50):
        calls["get_agora_comments"] = {"post_id": post_id, "limit": limit}
        return {"comments": [{"id": "c1", "body": "hello"}]}

    async def get_story_comments(story_id, limit=50):
        calls["get_story_comments"] = {"story_id": story_id, "limit": limit}
        return {"comments": []}

    async def post_comment(**kw):
        calls["post_comment"] = kw
        return {"ok": True, "comment_id": "c99"}

    def format_news_block(articles, label="", group_by_sphere=True):
        calls["format_news_block"] = {"n": len(articles), "label": label}
        return f"--- FRISS HÍRKONTEXTUS ({label}) --- {len(articles)} cikk"

    fake.search_news = search_news
    fake.fetch_news = fetch_news
    fake.get_top_story_links = get_top_story_links
    fake.agora_action = agora_action
    fake.get_agora_comments = get_agora_comments
    fake.get_story_comments = get_story_comments
    fake.post_comment = post_comment
    fake.format_news_block = format_news_block
    return fake


class _Ctx:
    """Minimal ctx: get_db over an in-memory shared_memory table."""

    def __init__(self, mem: dict | None = None):
        self._mem = mem or {}

    def get_db(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE shared_memory (key TEXT PRIMARY KEY, value TEXT)")
        for k, v in self._mem.items():
            conn.execute("INSERT INTO shared_memory (key, value) VALUES (?, ?)", (k, v))
        return conn


@pytest.fixture()
def no_op_key_env(monkeypatch):
    monkeypatch.delenv("AGORA_OP_KEY_FELDWEBEL", raising=False)
    monkeypatch.delenv("ECHOLOT_OPERATOR_KEY", raising=False)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_echolot_tools_registered():
    names = _tool_names()
    assert {"echolot_query", "agora", "story_comments"} <= names


def test_agora_actions_enum():
    agora = next(t for t in TOOLS if t["function"]["name"] == "agora")
    actions = agora["function"]["parameters"]["properties"]["action"]["enum"]
    assert set(actions) == {"feed", "read", "comments", "comment"}


# ---------------------------------------------------------------------------
# ECHOLOT_URL gate
# ---------------------------------------------------------------------------

def test_disabled_without_echolot_url(monkeypatch):
    fake = _make_fake_ec({}, url="")
    monkeypatch.setitem(sys.modules, "_echolot_client", fake)
    out = json.loads(asyncio.run(_execute_tool("echolot_query", {}, _Ctx())))
    assert "ECHOLOT_URL" in out["error"]


# ---------------------------------------------------------------------------
# echolot_query
# ---------------------------------------------------------------------------

def test_echolot_query_search(monkeypatch):
    calls = {}
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec(calls))
    out = asyncio.run(_execute_tool(
        "echolot_query", {"query": "Tisza", "days": 2, "limit": 5, "language": "hu"}, _Ctx()))
    assert calls["search_news"]["query"] == "Tisza"
    assert calls["search_news"]["limit"] == 5
    assert "FRISS HÍRKONTEXTUS" in out


def test_echolot_query_latest_no_query(monkeypatch):
    calls = {}
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec(calls))
    asyncio.run(_execute_tool("echolot_query", {"spheres": "hu_economy, global_economy"}, _Ctx()))
    assert calls["fetch_news"]["spheres"] == ["hu_economy", "global_economy"]
    assert "search_news" not in calls


def test_echolot_query_top_stories(monkeypatch):
    calls = {}
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec(calls))
    out = json.loads(asyncio.run(_execute_tool("echolot_query", {"top_stories": True}, _Ctx())))
    assert out["top_stories"][0]["story_id"] == "abc123"


# ---------------------------------------------------------------------------
# agora — read paths (no key needed)
# ---------------------------------------------------------------------------

def test_agora_feed(monkeypatch, no_op_key_env):
    calls = {}
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec(calls))
    out = json.loads(asyncio.run(_execute_tool("agora", {"action": "feed", "limit": 7}, _Ctx())))
    assert out["ok"] is True
    assert calls["agora_action"][0] == {"action": "feed", "limit": 7}


def test_agora_read_requires_post_id(monkeypatch):
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec({}))
    out = json.loads(asyncio.run(_execute_tool("agora", {"action": "read"}, _Ctx())))
    assert "post_id" in out["error"]


def test_agora_comments_read(monkeypatch, no_op_key_env):
    calls = {}
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec(calls))
    out = json.loads(asyncio.run(_execute_tool(
        "agora", {"action": "comments", "post_id": "p42"}, _Ctx())))
    assert calls["get_agora_comments"]["post_id"] == "p42"
    assert out["comments"][0]["id"] == "c1"


# ---------------------------------------------------------------------------
# agora — comment (write path)
# ---------------------------------------------------------------------------

def test_agora_comment_without_key_fails(monkeypatch, no_op_key_env):
    calls = {}
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec(calls))
    out = json.loads(asyncio.run(_execute_tool(
        "agora", {"action": "comment", "post_id": "p42", "body": "szia"}, _Ctx())))
    assert out["error"] == _NO_OP_KEY_MSG
    assert "post_comment" not in calls


def test_agora_comment_with_env_key(monkeypatch, no_op_key_env):
    calls = {}
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec(calls))
    monkeypatch.setenv("AGORA_OP_KEY_FELDWEBEL", "eop_testkey")
    out = json.loads(asyncio.run(_execute_tool(
        "agora", {"action": "comment", "post_id": "p42", "body": "Jó esszé."}, _Ctx())))
    assert out["ok"] is True
    pc = calls["post_comment"]
    assert pc["story_id"] == "pub-p42"          # pub- prefix applied
    assert pc["operator_key"] == "eop_testkey"
    assert pc["agent_label"] == "Feldwebel"


def test_agora_comment_pub_prefix_not_doubled(monkeypatch, no_op_key_env):
    calls = {}
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec(calls))
    monkeypatch.setenv("AGORA_OP_KEY_FELDWEBEL", "eop_testkey")
    asyncio.run(_execute_tool(
        "agora", {"action": "comment", "post_id": "pub-p42", "body": "x"}, _Ctx()))
    assert calls["post_comment"]["story_id"] == "pub-p42"


def test_agora_comment_key_from_shared_memory(monkeypatch, no_op_key_env):
    calls = {}
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec(calls))
    ctx = _Ctx({"echolot_operator_key": "eop_memkey"})
    out = json.loads(asyncio.run(_execute_tool(
        "agora", {"action": "comment", "post_id": "p42", "body": "x"}, ctx)))
    assert out["ok"] is True
    assert calls["post_comment"]["operator_key"] == "eop_memkey"


# ---------------------------------------------------------------------------
# story_comments
# ---------------------------------------------------------------------------

def test_story_comments_read(monkeypatch, no_op_key_env):
    calls = {}
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec(calls))
    asyncio.run(_execute_tool("story_comments", {"story_id": "abc123"}, _Ctx()))
    assert calls["get_story_comments"]["story_id"] == "abc123"
    assert "post_comment" not in calls


def test_story_comments_write(monkeypatch, no_op_key_env):
    calls = {}
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec(calls))
    monkeypatch.setenv("AGORA_OP_KEY_FELDWEBEL", "eop_testkey")
    out = json.loads(asyncio.run(_execute_tool(
        "story_comments", {"story_id": "abc123", "body": "Fontos adalék."}, _Ctx())))
    assert out["ok"] is True
    assert calls["post_comment"]["story_id"] == "abc123"   # no pub- prefix on stories
    assert calls["post_comment"]["agent_label"] == "Feldwebel"


def test_story_comments_write_without_key_fails(monkeypatch, no_op_key_env):
    calls = {}
    monkeypatch.setitem(sys.modules, "_echolot_client", _make_fake_ec(calls))
    out = json.loads(asyncio.run(_execute_tool(
        "story_comments", {"story_id": "abc123", "body": "x"}, _Ctx())))
    assert out["error"] == _NO_OP_KEY_MSG


# ===========================================================================
# ALAPKAMAT: A STATDATA-N AT, NEM A NYERS TUKORROL — 2026-08-31
# ===========================================================================
#
# ELES KAR (00:22): a Kommandant azt kapta a bottol, hogy "Alapkamatok
# (legutolsó ismert: 2025. június): MNB 6,5% · ECB 2,0% · Fed 4,375%". Mind a
# harom ROSSZ — a valosag MNB 5,5% (2026-08-25-i dontes), ECB 2,25%, Fed
# 3,50–3,75%.
#
# AZ OK NEM ADATHIANY VOLT: a StatData `get_policy_rates` ugyanerre a kerdesre
# HELYESEN felelt (MNB-scrape + ECB Data Portal + FRED celsav). A Feldwebelnek
# csak volt egy SAJAT implementacioja, ami kozvetlenul a dbnomics BIS-tukret
# hivta — az pedig MERTEN ~14 honapot kesik.
#
# Ez ma este a HARMADIK eset ugyanebbol az osztalybol (lasd meg:
# `schedule_recipe` nema felulirasa, `web_search` szerv-valasztas). A minta
# mindig ugyanaz: van egy jo megoldas, es a Kommandant az orizetlen agat
# hasznalja.

def test_MINDEN_econ_tool_a_statdatan_at_megy():
    """EGY UT, NEM OT. A Feldwebelnek OT sajat gazdasagi implementacioja volt,
    mind kozvetlenul egy kulso API-ra — es egyik sem kapta meg azt, ami a
    StatData-ban megvan (rate-limit kapu, ujraprobalas, `unit` mezo,
    hihetosegi kapu, friss forraslanc, hetvege-javitott naptar).

    Ez a teszt azt orzi, hogy a kozos elagazas MINDEGYIKET lefedi, es hogy az
    egyedi (nyers API-s) agak ELE kerul.
    """
    import inspect
    from feldwebel import responder
    src = inspect.getsource(responder._execute_tool)

    i_elagazas = src.index("_econ_via_statdata")
    for tool in ("mnb_rates", "market_quote", "fred_data", "policy_rates", "econ_calendar"):
        assert tool in responder._ECON_STATDATA_MAP, f"{tool} kimaradt a terkepbol"
        i_sajat = src.index(f'if name == "{tool}":')
        assert i_elagazas < i_sajat, \
            f"a(z) {tool} sajat, nyers API-s aga MEGELOZI a StatData-t"


def test_a_tartalek_ut_KIMONDJA_hogy_keshet():
    """Ha a StatData nem elerheto, a nyers utra esunk vissza — de NEM neman.

    ELES KAR (2026-08-31, 00:22): a Kommandant azt kapta, hogy "Alapkamatok
    (legutolsó ismert: 2025. június): MNB 6,5% · ECB 2,0% · Fed 4,375%" — mind
    a harom rossz, miközben a StatData ugyanerre 5,5% / 2,25% / 3,75%-ot
    felelt. Nem adathiany volt: rossz ajton kopogtunk.
    """
    import inspect
    from feldwebel import responder
    src = inspect.getsource(responder._execute_tool)
    assert "TARTALEK ut" in src, "a visszaeses nem naplozott"
    blokk = src.split('if name == "policy_rates":', 1)[1].split("if name ==", 1)[0]
    assert "KESHET" in blokk, "a tartalek valasz nem jeloli meg a teteleket"


def test_a_rendszerprompt_tiltja_a_tablazat_kiegesziteset():
    """MEGTORTENT (2026-08-31): a bot valasza OT jegybankot sorolt fel egy
    tablazatban; a `policy_rates` tool HARMAT adott vissza. A lengyel es a
    roman sort a modell irta hozza emlekezetbol — mindketto ROSSZ volt
    (NBP 3,5 a valos 3,75 helyett, BNR 3,3 a valos 6,5 helyett).

    A kitalalt sor ugyanugy nez ki, mint a tobbi: ugyanabban a tablazatban,
    ugyanolyan formaban, forrasjelzes nelkul. Az olvaso nem tudja
    megkulonboztetni.
    """
    from feldwebel.responder import SYSTEM_PROMPT_TEMPLATE as P
    assert "NEM KERÜL A TÁBLÁZATBA" in P
    assert "HÍVD ÚJRA a toolt" in P, "nincs megadva a HELYES teendo"
    assert "AZ OKOT SEM TALÁLHATOD KI" in P, \
        "a kitalalt OK-ra (Warsh-eset) nincs szabaly"
