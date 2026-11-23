"""A RIZSRAKÉTÁK (Kimi, DeepSeek, GLM) MINDEN SAJÁT ESZKÖZÜNKET ELÉRIK — MINDEN ÚTJUKON (2026-11-23).

A Kommandant: „A Kimi Deepseek és GLM elvileg tud, de nyilván nem árt ha tud használni minden eszközünket - tehát
browser, engine, stb.” Kivétel: a Kommandant tudásbázisa, Gmailje, naptára, és minden kifelé ható művelet.

Amit ez a fájl őriz (hálózat nélkül — a külső MCP-k helyett hamis eszközlista, az LLM helyett hamis `sf_chat`):
  * az eszközlista EGY forrásból jön (`subagent_tool_defs`), benne a külső MCP-k, szűkítve (Studio: csak a
    determinisztikus dokumentumkészítés; Engine: figyelő, futásnapló és a Kommandant fiókja nélkül);
  * minden SF-út ezt kapja: ai_query, deep research, ai_task broadcast és dispatch, a koordinátori szintézis, a
    Telegramos megszólítás („Kimi, …”) és az @-említés;
  * a tiltott eszköz NINCS a listában, és a végrehajtó is elutasítja (két öv) — a böngészőn át sem érhető el a Gmail;
  * a megszólítás eszközt hív, és a hurok korlátos (körszám, eszközhívás-keret);
  * SiliconFlow-kiesésnél a tartalék Claude ugyanazokat az eszközöket kapja.
"""
from __future__ import annotations

import asyncio
import inspect
import json

import pytest

import kulso_mcp
import server


class T:
    """A FastMCP `Tool` helyett: név, leírás, séma."""

    def __init__(self, name, props=("q",)):
        self.name = name
        self.description = f"{name} — teszt-eszköz."
        self.inputSchema = {"type": "object", "properties": {p: {"type": "string"} for p in props}}


HAMIS_LISTA = {
    "lexonomics": [T("search"), T("provision", ("path",))],
    "medical": [T("drug_search")],
    "makronom": [T("get_macro_indicator", ("country", "indicator"))],
    "engine": [T("engine_search"), T("engine_scrape", ("url", "own_account", "profile")), T("engine_monitor"),
               T("engine_run"), T("engine_dataset", ("action", "schedule"))],
    "studio": [T("files", ("action",)), T("convert", ("file_id",)), T("wordcraft"), T("pptx_build", ("spec",)),
               T("edit_with_instruction"),
               T("facts"), T("describe_image")],
    "echolot": [T("search_news"), T("press_pub_new")],
}


@pytest.fixture
def kulso(monkeypatch):
    """A külső MCP-k bekötve (hamis kulcs), az eszközlistájuk a HAMIS_LISTA; a hívásaik naplózva."""
    monkeypatch.setenv("SF_KULSO_MCP", "1")
    for k in ("LEXONOMICS_MCP_KEY", "STUDIO_MCP_KEY", "ECHOLOT_ENGINE_KEY"):
        monkeypatch.setenv(k, "hamis")

    async def lista(nev, *, friss=False):
        return HAMIS_LISTA[nev], ""
    monkeypatch.setattr(kulso_mcp, "_lista", lista)
    hivasok = []

    async def hiv(nev, eszkoz, parameterek, *, max_kar=kulso_mcp.MAX_EREDMENY_KAR):
        hivasok.append((nev, eszkoz, parameterek, max_kar))
        return f"{nev}.{eszkoz} eredménye"
    monkeypatch.setattr(kulso_mcp, "hiv", hiv)
    return hivasok


def _nevek(defs):
    return [t["function"]["name"] for t in defs]


def _enum(defs, nev):
    return set(next(t for t in defs if t["function"]["name"] == nev)["function"]["parameters"]["properties"]
               ["eszkoz"]["enum"])


@pytest.fixture
def sf(monkeypatch):
    """Hamis `sf_chat`: minden hívás payloadja naplózva; a válasz sima szöveg (eszközhívás nélkül)."""
    hivasok = []

    async def sf_chat(payload, *, purpose="", **kw):
        hivasok.append((purpose, json.loads(json.dumps(payload, default=str))))
        return {"choices": [{"message": {"content": "kész válasz"}, "finish_reason": "stop"}],
                "model": payload.get("model")}, None, []
    monkeypatch.setattr(server, "sf_chat", sf_chat)
    monkeypatch.setattr(server, "SILICONFLOW_API_KEY", "k")
    monkeypatch.setattr(server, "pyramid_store_result", lambda **kw: None, raising=False)   # nincs memóriaírás
    return hivasok


def _eszkozos(hivasok, elotag):
    """Az adott célú hívások közül azok eszközlistái, amelyekben volt eszköz."""
    return [set(_nevek(p.get("tools") or [])) for cel, p in hivasok if cel.startswith(elotag) and p.get("tools")]


KULSO_ELVART = {"lexonomics", "medical", "makronom", "engine", "studio"}


# ══ 1. AZ ESZKÖZLISTA: EGY FORRÁS, A KÜLSŐ MCP-K SZŰKÍTVE ═══════════════════

def test_az_eszkozlista_a_kulso_mcp_ket_is_hozza_szukitve(kulso):
    defs = asyncio.run(server.subagent_tool_defs())
    nevek = _nevek(defs)
    assert set(_nevek(server.SUBAGENT_TOOL_DEFS)) | KULSO_ELVART == set(nevek)
    assert nevek.count("echolot") == 1                       # a saját olvasó-proxy, nem a kulso_mcp teljes Echolotja
    assert _enum(defs, "studio") == {"files", "convert", "wordcraft", "pptx_build", "sugo"}  # a Nachtschichté
    assert _enum(defs, "studio") - {"sugo"} == set(server.nachtschicht.STUDIO_ENGEDETT)
    assert not _enum(defs, "engine") & {"engine_monitor", "engine_run"}
    assert {"engine_search", "engine_scrape", "engine_dataset"} <= _enum(defs, "engine")
    assert KULSO_ELVART <= set(server.SUBAGENT_TOOL_NAMES)   # a 7 dispatch-hely engedélylistája ugyanez


def test_kulcs_nelkul_csak_a_nyilvanos_kulso_szerverek(kulso, monkeypatch):
    for k in ("LEXONOMICS_MCP_KEY", "STUDIO_MCP_KEY", "ECHOLOT_ENGINE_KEY"):
        monkeypatch.delenv(k)
    assert set(_nevek(asyncio.run(server.subagent_tool_defs()))) & KULSO_ELVART == {"medical", "makronom"}


def test_kikapcsolhato_es_a_kulso_kiesese_nem_viszi_el(kulso, monkeypatch):
    monkeypatch.setenv("SF_KULSO_MCP", "0")
    assert _nevek(asyncio.run(server.subagent_tool_defs())) == _nevek(server.SUBAGENT_TOOL_DEFS)
    monkeypatch.setenv("SF_KULSO_MCP", "1")

    async def rossz(nev, *, friss=False):
        raise RuntimeError("a szerver nem válaszol")
    monkeypatch.setattr(kulso_mcp, "_lista", rossz)
    assert _nevek(asyncio.run(server.subagent_tool_defs())) == _nevek(server.SUBAGENT_TOOL_DEFS)


# ══ 2. A TILTOTTAK: NINCSENEK A LISTÁBAN, ÉS A VÉGREHAJTÓ IS ELUTASÍTJA ══════

KIMONDOTT_TILTOTT = ("tudasbazis", "chatgpt", "gemini", "gmail_search", "gmail_poll", "read_gmail_attachment",
                     "naptar", "calendar_poll", "calendar_create", "create_calendar_event", "search_memory",
                     "read_memory", "send_email", "send_message", "send_file_email", "agora", "draft_reply",
                     "ai_query", "ai_task", "export_task")


def test_tiltott_nincs_a_listaban(kulso, monkeypatch):
    assert set(KIMONDOTT_TILTOTT) <= server.SUBAGENT_TILTOTT
    nevek = set(_nevek(asyncio.run(server.subagent_tool_defs())))
    assert not nevek & server.SUBAGENT_TILTOTT and not set(server.SUBAGENT_TOOL_NAMES) & server.SUBAGENT_TILTOTT
    # akkor sem kerülhet be, ha egy külső szerver ilyen nevű eszközt hirdetne
    async def csempesz():
        return [{"type": "function", "function": {"name": "gmail_search", "description": "", "parameters": {}}}]
    monkeypatch.setattr(server, "_sf_kulso_eszkozok", csempesz)
    assert "gmail_search" not in _nevek(asyncio.run(server.subagent_tool_defs()))


@pytest.mark.parametrize("nev", sorted(server.SUBAGENT_TILTOTT))
def test_a_vegrehajto_elutasitja_a_tiltottat(nev, kulso):
    v = json.loads(asyncio.run(server._dispatch_subagent_tool(nev, {"query": "x"})))
    assert v["error"] == "tool_not_allowed" and kulso == []


@pytest.mark.parametrize("nev,args", [
    ("web_fetch", {"url": "https://mail.google.com/mail/u/0/#inbox"}),
    ("web_scrape", {"url": "https://calendar.google.com/calendar/r"}),
    ("brave_scrape", {"url": "http://gmail.com"}),
    ("brave_login", {"site": "gmail", "username": "a", "password": "b"}),
    ("brave_action", {"site": "gmail", "actions": [{"type": "screenshot"}]}),
    ("brave_action", {"actions": [{"type": "navigate", "url": "https://inbox.google.com/"}]}),
    ("brave_action", {"actions": [{"type": "session_action", "site": "Gmail", "action": "read"}]}),
    ("engine", {"eszkoz": "engine_scrape", "parameterek": {"url": "https://mail.google.com/"}}),
])
def test_gmail_es_naptar_a_bongeszon_at_sem(nev, args, kulso):
    v = json.loads(asyncio.run(server._dispatch_subagent_tool(nev, args)))
    assert v["error"] == "tool_not_allowed" and "Gmail" in v["message"] and kulso == []


def test_a_gmail_szo_keresesben_nem_tiltott(monkeypatch):
    async def web_search(q, engine="auto"):
        return f"találat: {q}"
    monkeypatch.setattr(server, "_web_search", web_search)
    assert asyncio.run(server._dispatch_subagent_tool("web_search", {"query": "gmail.com leállás ma"})) \
        == "találat: gmail.com leállás ma"


@pytest.mark.parametrize("nev,eszkoz,param", [
    ("studio", "edit_with_instruction", {"file_id": "f1", "instruction": "javítsd"}),
    ("studio", "facts", {"action": "check"}),
    ("studio", "files", {"action": "list"}),                 # a Kommandant fájljai
    ("engine", "engine_monitor", {"action": "create", "email_to": ["x@y.hu"]}),
    ("engine", "engine_run", {"action": "list"}),
    ("engine", "engine_scrape", {"url": "https://x.hu", "own_account": True}),
    ("engine", "engine_scrape", {"url": "https://x.hu", "profile": "kommandant"}),
    ("engine", "engine_dataset", {"action": "create", "query": "x", "schedule": "daily"}),
    ("studio", "sugo", {"nev": "edit_with_instruction"}),
    ("lexonomics", "", {}),
])
def test_kulso_szukites_a_vegrehajtoban(nev, eszkoz, param, kulso):
    v = json.loads(asyncio.run(server._dispatch_subagent_tool(nev, {"eszkoz": eszkoz, "parameterek": param})))
    assert v["error"] == "tool_not_allowed" and kulso == []


@pytest.mark.parametrize("nev,eszkoz,param", [
    ("studio", "files", {"action": "new", "kind": "document"}),
    ("studio", "wordcraft", {"file_id": "f1"}),
    ("studio", "convert", {"source_url": "https://x.hu/a.md", "target_format": "docx"}),
    ("engine", "engine_scrape", {"url": "https://x.hu"}),
    ("engine", "engine_dataset", {"action": "create", "query": "x", "schedule": "manual"}),
    ("lexonomics", "search", {"q": "Ptk. 6:1"}),
    ("medical", "drug_search", {"q": "ibuprofen"}),
    ("makronom", "get_macro_indicator", {"country": "HU", "indicator": "cpi"}),
])
def test_az_engedett_kulso_hivas_atmegy(nev, eszkoz, param, kulso):
    assert asyncio.run(server._dispatch_subagent_tool(nev, {"eszkoz": eszkoz, "parameterek": param})) \
        == f"{nev}.{eszkoz} eredménye"
    assert kulso == [(nev, eszkoz, param, server.SUBAGENT_KULSO_MAX_KAR)]


def test_a_kozzetevo_echolot_eszkoz_tovabbra_is_tiltott(monkeypatch):
    """Az `echolot` a saját olvasó-proxy marad (nem a kulso_mcp teljes Echolotja): közzétenni innen sem lehet."""
    monkeypatch.setattr(server, "ECHOLOT_ENABLED", True, raising=False)
    monkeypatch.setattr(server, "echolot_client", object(), raising=False)
    v = json.loads(asyncio.run(server._dispatch_subagent_tool("echolot", {"tool": "press_pub_new", "args": {}})))
    assert v["error"] == "tool_not_allowed"


# ══ 3. MINDEN SF-ÚT MEGKAPJA AZ ESZKÖZÖKET ═════════════════════════════════

def test_egy_forras_minden_dispatch_helyen():
    """A statikus lista sehol nem megy ki közvetlenül; a 7 hely + a beszélgetős hurok az aszinkron forrásból kér."""
    src = inspect.getsource(server)
    assert 'payload["tools"] = SUBAGENT_TOOL_DEFS' not in src and '"tools": SUBAGENT_TOOL_DEFS' not in src
    assert src.count("await subagent_tool_defs()") >= 8
    for fv, db in ((server.ai_query, 1), (server._deep_research_loop, 1), (server._run_agent_with_tools, 1),
                   (server._execute_ai_task, 2), (server.ai_task, 2), (server._rizs_eszkozhurok, 1)):
        assert inspect.getsource(fv).count("await subagent_tool_defs()") == db, fv.__name__


def test_ai_query_megkapja(kulso, sf):
    asyncio.run(server.ai_query(model="kimi", prompt="mi a hatályos Ptk. 6:1?", system_prompt="teszt"))
    assert KULSO_ELVART <= _eszkozos(sf, "ai_query/kimi")[0]
    rendszer = sf[0][1]["messages"][0]["content"]
    assert "lexonomics" in rendszer and "AMI NEKED NEM ELÉRHETŐ" in rendszer


def test_deep_research_megkapja(kulso, sf):
    asyncio.run(server._deep_research_loop("moonshotai/Kimi-K3.1-Pro", [{"role": "user", "content": "x"}], {},
                                           max_rounds=2, purpose="ai_query/kimi/research"))
    assert KULSO_ELVART <= _eszkozos(sf, "ai_query/kimi/research")[0]


def test_koordinatori_hurok_megkapja(kulso, sf):
    asyncio.run(server._run_agent_with_tools("kimi", [{"role": "user", "content": "x"}], max_rounds=2))
    assert KULSO_ELVART <= _eszkozos(sf, "agent_with_tools/")[0]


def test_ai_task_broadcast_megkapja(kulso, sf, monkeypatch):
    monkeypatch.setattr(server, "build_agent_context", lambda **kw: "SYS")
    monkeypatch.setattr(server, "pyramid_store_result", lambda **kw: None, raising=False)

    async def push(*a, **k):
        return None
    monkeypatch.setattr(server, "_telegram_push", push)
    c = server.get_db()
    tid = c.execute("INSERT INTO ai_tasks (title, description, assigned_by, created_at) VALUES ('t', 'd', 'teszt', "
                    "'2026-11-23')").lastrowid
    c.commit()
    c.close()
    asyncio.run(server._execute_ai_task(tid, "t", "d", "", "teszt"))
    for ugynok in ("kimi", "deepseek", "glm5"):
        assert KULSO_ELVART <= _eszkozos(sf, f"ai_task/{ugynok}")[0], ugynok


# ══ 4. A MEGSZÓLÍTÁS („Kimi, keress rá …”) ÉS AZ @-EMLÍTÉS ESZKÖZT HÍV ════════

def _hurok_sf(monkeypatch, valaszok):
    """Hamis `sf_chat` a hurokhoz: a `valaszok` sorban jönnek (dict = üzenet), a végén mindig sima szöveg."""
    hivasok = []

    async def sf_chat(payload, *, purpose="", **kw):
        hivasok.append((purpose, json.loads(json.dumps(payload, default=str))))
        msg = valaszok.pop(0) if valaszok else {"content": "A forint 391,2 EUR/HUF (web_search)."}
        return {"choices": [{"message": msg, "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
                "model": "moonshotai/Kimi-K3.1-Pro"}, None, []
    monkeypatch.setattr(server, "sf_chat", sf_chat)
    monkeypatch.setattr(server, "SILICONFLOW_API_KEY", "k")
    return hivasok


def _hivas(nev, args, i=0):
    return {"id": f"call_{i}", "type": "function", "function": {"name": nev, "arguments": json.dumps(args)}}


@pytest.fixture
def web(monkeypatch):
    keresesek = []

    async def web_search(q, engine="auto"):
        keresesek.append(q)
        return f"találat: {q} — 391,2"
    monkeypatch.setattr(server, "_web_search", web_search)
    return keresesek


def test_kimi_keress_ra_telegramrol_eszkozt_hiv(kulso, web, get_db, monkeypatch):
    pushok = []

    async def push(s):
        pushok.append(s)
    monkeypatch.setattr(server, "_telegram_push", push)
    monkeypatch.setattr(server, "get_db", get_db)
    monkeypatch.setattr(server, "PYRAMID_ENABLED", True)
    kontextus = []
    monkeypatch.setattr(server, "build_agent_context", lambda **kw: kontextus.append(kw) or "Kimi vagy.")
    monkeypatch.setattr(server, "pyramid_store_result", lambda **kw: None, raising=False)
    sf = _hurok_sf(monkeypatch, [{"content": "", "tool_calls": [_hivas("web_search", {"query": "forint árfolyam"})]}])

    asyncio.run(server._handle_telegram_message("Kimi, keress rá a mai forint-árfolyamra", "42"))

    assert web == ["forint árfolyam"]
    assert [c for c, _ in sf] == ["telegram/chat/kimi"] * 2          # a saját modellje (név szerinti ügynök)
    elso, masodik = sf[0][1], sf[1][1]
    assert KULSO_ELVART | {"web_search", "web_fetch"} <= set(_nevek(elso["tools"]))
    assert "AMI NEKED NEM ELÉRHETŐ" in elso["messages"][0]["content"]
    assert any(m.get("role") == "tool" and "391,2" in m["content"] for m in masodik["messages"])
    assert "391,2" in pushok[-1] and "KIMI" in pushok[-1].upper()
    assert kontextus and not any(k.get("inbox_summary") for k in kontextus)     # a levelek tárgya sem megy Kiminek
    c = get_db()
    assert "391,2" in c.execute("SELECT message FROM messages WHERE sender = 'kimi'").fetchone()[0]
    c.close()


def test_az_emlitesre_adott_valasz_is_eszkozt_hiv(kulso, web, get_db, monkeypatch):
    monkeypatch.setattr(server, "get_db", get_db)
    monkeypatch.setattr(server, "PYRAMID_ENABLED", True)
    monkeypatch.setattr(server, "build_agent_context", lambda **kw: "GLM vagy.")
    monkeypatch.setattr(server, "pyramid_store_result", lambda **kw: None, raising=False)
    sf = _hurok_sf(monkeypatch, [{"content": "", "tool_calls": [
        _hivas("lexonomics", {"eszkoz": "search", "parameterek": {"q": "Mt. 66. §"}}),
        _hivas("web_search", {"query": "felmondási idő"}, 1)]}])

    class Azonnal:                                  # a háttérszál helyett: a cél ugyanabban a szálban fut le
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(server.threading, "Thread", Azonnal)
    server._trigger_agent_replies(7, "cli-claus", "@glm5 mi a felmondási idő az Mt. szerint?", ["glm5"])

    assert kulso == [("lexonomics", "search", {"q": "Mt. 66. §"}, server.SUBAGENT_KULSO_MAX_KAR)]
    assert web == ["felmondási idő"]
    assert {c for c, _ in sf} == {"bridge/mention/glm5"} and KULSO_ELVART <= set(_nevek(sf[0][1]["tools"]))
    c = get_db()
    assert c.execute("SELECT recipient, reply_to FROM messages WHERE sender = 'glm5'").fetchone()[:] == ("cli-claus", 7)
    c.close()


def test_a_hurokban_is_elutasitva_a_tiltott_es_minden_hivasra_van_valasz(kulso, monkeypatch):
    sf = _hurok_sf(monkeypatch, [{"content": "", "tool_calls": [
        _hivas("tudasbazis", {"kerdes": "mit döntöttünk?"}), _hivas("gmail_search", {"query": "x"}, 1),
        _hivas("studio", {"eszkoz": "edit_with_instruction", "parameterek": {}}, 2)]}])
    szoveg, _, err, _, hivott = asyncio.run(server._rizs_eszkozhurok(
        "moonshotai/Kimi-K3.1-Pro", [{"role": "user", "content": "x"}], purpose="telegram/chat/kimi"))
    assert err is None and szoveg and kulso == []
    eszkoz_valaszok = [m for m in sf[1][1]["messages"] if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in eszkoz_valaszok] == ["call_0", "call_1", "call_2"]
    assert all(json.loads(m["content"])["error"] == "tool_not_allowed" for m in eszkoz_valaszok)


def test_szoveges_jelolos_hivas_is_vegrehajtva(kulso, web, monkeypatch):
    jelolos = ('<|tool_calls_section_begin|><|tool_call_begin|>functions.web_search:0<|tool_call_argument_begin|>'
               '{"query": "MNB alapkamat"}<|tool_call_end|><|tool_calls_section_end|>')
    sf = _hurok_sf(monkeypatch, [{"content": jelolos}])
    szoveg, *_ = asyncio.run(server._rizs_eszkozhurok(
        "moonshotai/Kimi-K3.1-Pro", [{"role": "user", "content": "x"}], purpose="telegram/chat/kimi"))
    assert web == ["MNB alapkamat"] and "<|tool" not in szoveg
    assert "Eszköz-eredmények" in sf[1][1]["messages"][-1]["content"]


# ══ 5. KORLÁTOS HUROK: KÖRSZÁM ÉS ESZKÖZHÍVÁS-KERET ══════════════════════════

def test_a_hurok_a_modell_sajat_beallitasaval_megy(monkeypatch):
    """Kimi gondolkodás nélkül (mint az ai_query/ai_task úton): a K3.1 Pro alapból gondolkodna, és a kör keretét elvinné."""
    sf = _hurok_sf(monkeypatch, [])
    asyncio.run(server._rizs_eszkozhurok("moonshotai/Kimi-K3.1-Pro", [{"role": "user", "content": "x"}],
                                         purpose="telegram/chat/kimi"))
    assert sf[0][1]["thinking"] == {"type": "disabled"}


def test_korszam_korlat(web, monkeypatch):
    """A modell minden körben újra keresne: legfeljebb RIZS_HUROK_MAX_KOR kör, az utolsó eszköz nélkül, záró kéréssel."""
    hivasok = []

    async def sf_chat(payload, *, purpose="", **kw):
        hivasok.append(payload)
        if payload.get("tools"):
            return {"choices": [{"message": {"content": "", "tool_calls": [
                _hivas("web_search", {"query": f"kör {len(hivasok)}"})]}}]}, None, []
        return {"choices": [{"message": {"content": "végső válasz"}}]}, None, []
    monkeypatch.setattr(server, "sf_chat", sf_chat)
    szoveg, *_, hivott = asyncio.run(server._rizs_eszkozhurok(
        "m", [{"role": "user", "content": "x"}], purpose="telegram/chat/kimi"))
    assert szoveg == "végső válasz"
    assert len(hivasok) == server.RIZS_HUROK_MAX_KOR and len(web) == server.RIZS_HUROK_MAX_KOR - 1
    assert "tools" not in hivasok[-1] and hivasok[-1]["messages"][-1]["content"] == server.RIZS_HUROK_ZARO
    assert all("tools" in h for h in hivasok[:-1])


def test_eszkozhivas_keret(web, monkeypatch):
    """Körönként 5 hívást kér: a 8-as keret után a többi kimondott elutasítást kap, és a következő kör a válasz."""
    hivasok = []

    async def sf_chat(payload, *, purpose="", **kw):
        hivasok.append(payload)
        if payload.get("tools"):
            return {"choices": [{"message": {"content": "", "tool_calls": [
                _hivas("web_search", {"query": f"{len(hivasok)}/{i}"}, i) for i in range(5)]}}]}, None, []
        return {"choices": [{"message": {"content": "végső"}}]}, None, []
    monkeypatch.setattr(server, "sf_chat", sf_chat)
    szoveg, *_, hivott = asyncio.run(server._rizs_eszkozhurok(
        "m", [{"role": "user", "content": "x"}], purpose="telegram/chat/kimi"))
    assert szoveg == "végső" and len(web) == server.RIZS_HUROK_MAX_HIVAS == len(hivott)
    assert len(hivasok) == 3 and "tools" not in hivasok[-1]
    keret = [m for m in hivasok[-1]["messages"] if m.get("role") == "tool" and "eszközhívás-keret" in m["content"]]
    assert len(keret) == 2


def test_nagy_eredmeny_kivonatolva_es_lapozhato(kulso, monkeypatch):
    """A karcsúsítás kivonat-mechanizmusa: 4000 karakter fölött kivonat megy a kontextusba, a teljes szöveg lapozható."""
    import kivonat
    nagy = "Ptk. 6:1 " + "x" * (kivonat.RESZ_KAR + 9_000)                  # két rész: a 2. is lapozható

    async def hiv(nev, eszkoz, parameterek, *, max_kar=8000):
        return nagy
    monkeypatch.setattr(kulso_mcp, "hiv", hiv)
    import kivonat

    async def olvaso(self, nev, args, szoveg, feladat):
        return "KIVONAT: Ptk. 6:1", "claude-haiku-5-5"
    monkeypatch.setattr(kivonat.EredmenyTar, "_olvas", olvaso)
    sf = _hurok_sf(monkeypatch, [
        {"content": "", "tool_calls": [_hivas("lexonomics", {"eszkoz": "search", "parameterek": {"q": "Ptk"}})]},
        {"content": "", "tool_calls": [_hivas(kivonat.LAPOZ_NEV, {"azon": "e1", "resz": 2}, 1)]}])
    asyncio.run(server._rizs_eszkozhurok("m", [{"role": "user", "content": "Ptk"}], purpose="telegram/chat/kimi"))
    elso_eredmeny = next(m for m in sf[1][1]["messages"] if m.get("role") == "tool")["content"]
    assert elso_eredmeny.startswith("[KIVONAT") and "KIVONAT: Ptk. 6:1" in elso_eredmeny and len(elso_eredmeny) < 1000
    lapozott = [m for m in sf[2][1]["messages"] if m.get("role") == "tool"][-1]["content"]
    assert lapozott.startswith("[e1 — 2. rész")


# ══ 6. SILICONFLOW-KIESÉS: A TARTALÉK CLAUDE UGYANAZOKAT AZ ESZKÖZÖKET KAPJA ══

def test_az_anthropic_tartalek_ugyanazokat_az_eszkozoket_kapja(kulso, monkeypatch):
    monkeypatch.setattr(server.llm_kapu, "elerheto", lambda: True)
    monkeypatch.setattr(server, "_sf_keys", lambda: [])           # nincs SiliconFlow → a kapu az Anthropicra vált
    kapott = []

    async def chat(payload, *, purpose=""):
        kapott.append(set(_nevek(payload.get("tools") or [])))
        return {"choices": [{"message": {"content": "Claude válasza"}}], "model": "claude-haiku-5-5"}, None, []
    monkeypatch.setattr(server.llm_kapu, "chat", chat)
    szoveg, data, err, *_ = asyncio.run(server._rizs_eszkozhurok(
        "moonshotai/Kimi-K3.1-Pro", [{"role": "user", "content": "x"}], purpose="telegram/chat/kimi"))
    assert err is None and szoveg == "Claude válasza" and data["model"] == "claude-haiku-5-5"
    assert KULSO_ELVART | {"web_search", "web_fetch"} <= kapott[0]
    assert not kapott[0] & server.SUBAGENT_TILTOTT


# ══ 7. A LEÍRÁSOK A VALÓSÁGOT MONDJÁK ═══════════════════════════════════════

def test_a_feldwebel_utmutatoja_es_az_ai_query_leirasa_egyezik():
    from feldwebel import eszkoz_ut, responder
    leiras = next(t for t in responder.TOOLS if t["function"]["name"] == "ai_query")["function"]["description"]
    for szoveg in (eszkoz_ut.U_AI, leiras, server.ai_query.__doc__ or inspect.getdoc(server.ai_query) or ""):
        assert "NEM tud webet" not in szoveg
        assert "Lexonomics" in szoveg and "Engine" in szoveg
        assert "Gmail" in szoveg and "naptár" in szoveg              # amit NEM érnek el, az is ki van mondva
    assert "Gmail" not in json.dumps(server.BRAVE_LOGIN_TOOL_DEF["function"]["parameters"])


# ══ 8. A KONTEXTUSBAN SEM: A KOMMANDANT INBOXA (LEVÉL + NAPTÁR) NEM MEGY SF-ÜGYNÖKNEK ═══

def test_az_inbox_osszefoglalo_sf_kontextusba_nem_kerul():
    """Eddig a levelek tárgya és a naptáresemények („KOMMANDANT INBOX”) minden megszólításnál, ai_query-nél és
    ai_task-nál az SF-ügynök rendszerszövegében volt. Most egyik SF-úton sem."""
    src = inspect.getsource(server)
    assert "inbox_summary=_get_inbox_summary()" not in src
    for fv in (server.ai_query, server._execute_ai_task, server._handle_telegram_message,
               server._trigger_agent_replies):
        assert "inbox_summary" not in inspect.getsource(fv), fv.__name__
