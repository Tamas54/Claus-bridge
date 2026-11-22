"""FUTÁR (futar_kliens.py): a Bridge keze a Kommandant gépén — tudásbázis, ChatGPT (Codex), Gemini (2026-11-22).

Hálózat nélkül: a gépen futó futárt egy hamis, aláírt válaszokat író korutin helyettesíti.
  - aláírás saját tartományban (futar:), az aláíratlan / hamis / más kérésre szóló válasz eldobva;
  - csak a 3 művelet, a paraméterek fehérlistán (a többi kulcs nem megy tovább);
  - időtúllépés: nyugta nélkül „a géped most nem elérhető (ki van kapcsolva?)”;
  - A TUDÁSBÁZIS CSAK CLAUDE-NAK: SiliconFlow-modell nem kapja meg és nem futtathatja; a tudásbázis-tartalmú kör csak
    Claude-dal megy (tartalék nélkül), nem hív SiliconFlow-ügynököt, és a jelölt előzmény SF-nek rejtve marad;
    a SiliconFlow-ügynökök eszközkészletében és kontextusában (build_agent_context, _get_inbox_summary) nincs benne;
  - a Bridge MCP `tudasbazis` eszköze: saját példány / üres caller igen, idegen példány nem.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import pytest

import faber_relay
import futar_kliens as fk

TOKEN = "123:TOKEN"
K = fk.kulcs(TOKEN)


def _run(c):
    return asyncio.run(c)


def _db(tmp_path):
    ut = tmp_path / "f.db"
    con = sqlite3.connect(ut)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, sender TEXT, "
                "recipient TEXT, subject TEXT, message TEXT, priority TEXT, thread_id INTEGER, reply_to INTEGER, "
                "status TEXT)")
    con.execute("CREATE TABLE telegram_history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, "
                "role TEXT NOT NULL, agent_id TEXT DEFAULT 'deepseek', content TEXT NOT NULL, timestamp TEXT NOT NULL)")
    con.commit()
    con.close()

    def get_db():
        c = sqlite3.connect(ut)
        c.row_factory = sqlite3.Row
        return c
    return get_db


def _futar_alair(tipus, adat, k=K):
    """Ahogy a gépen futó futár (futar.alair) aláír: a „@” \\u0040-ként."""
    import hashlib
    import hmac
    torzs = json.dumps({"tipus": tipus, "adat": json.dumps(adat, ensure_ascii=False), "ts": "t"},
                       ensure_ascii=False).replace("@", "\\u0040")
    return json.dumps({"torzs": torzs, "sig": hmac.new(k, torzs.encode(), hashlib.sha256).hexdigest()})


def _ir(get_db, mid, tipus, adat, *, k=K, felado="futar", cimzett="futar-valasz"):
    c = get_db()
    c.execute("INSERT INTO messages (timestamp, sender, recipient, subject, message, priority, reply_to) "
              "VALUES ('t', ?, ?, ?, ?, 'normal', ?)", (felado, cimzett, f"futar:{tipus}", _futar_alair(tipus, adat, k), mid))
    c.commit()
    c.close()


def _keresek(get_db):
    c = get_db()
    try:
        return c.execute("SELECT * FROM messages WHERE recipient = 'futar' ORDER BY id").fetchall()
    finally:
        c.close()


async def _hamis_gep(get_db, *, nyugta=True, valasz=None, kesleltetes=0.02):
    """A gépen futó futár helyett: megvárja a kérést, ellenőrzi az aláírást, nyugtáz és (ha van) válaszol."""
    for _ in range(200):
        sorok = _keresek(get_db)
        if sorok:
            break
        await asyncio.sleep(0.005)
    sor = sorok[-1]
    t = faber_relay.ellenoriz(K, sor["message"])
    adat = json.loads(t["adat"])
    await asyncio.sleep(kesleltetes)
    if nyugta:
        _ir(get_db, sor["id"], "atvett", {"kerdes": adat["kerdes"], "muvelet": t["tipus"], "sorban": 1})
    if valasz is not None:
        await asyncio.sleep(kesleltetes)
        _ir(get_db, sor["id"], "valasz", {"kerdes": adat["kerdes"], "muvelet": t["tipus"], **valasz})
    return t, adat


async def _hiv_gep_ellen(get_db, muvelet, args, gep_kw, **hiv_kw):
    hiv_kw.setdefault("modell", "claude-sonnet-5-5")
    gep = asyncio.ensure_future(_hamis_gep(get_db, **gep_kw))
    ered = await fk.hiv(muvelet, args, get_db=get_db, token=TOKEN, lepes_s=0.01, **hiv_kw)
    return json.loads(ered), await gep


@pytest.fixture(autouse=True)
def claude_elsodleges(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "teszt-kulcs")
    monkeypatch.delenv("LLM_ELSODLEGES", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)


# ── 1. Aláírás, kérés, válasz ───────────────────────────────────────────

def test_kulcs_sajat_tartomany():
    assert K != faber_relay.kulcs(TOKEN) and fk.kulcs("") is None
    import api_kapu
    assert K.hex() != api_kapu.munkas_kulcs(TOKEN)


def test_a_keres_alairva_a_futarnak_szol(tmp_path):
    get_db = _db(tmp_path)
    c = get_db()
    mid, kerdes = fk.kuld(c, TOKEN, "chatgpt", {"prompt": "x"}, hatarido=time.time() + 60, ts="2026-11-22T18:00:00")
    sor = c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    assert (sor["sender"], sor["recipient"], sor["subject"], sor["thread_id"]) == ("feldwebel", "futar", "futar:chatgpt", mid)
    t = faber_relay.ellenoriz(K, sor["message"])
    adat = json.loads(t["adat"])
    assert t["tipus"] == "chatgpt" and adat["kerdes"] == kerdes and adat["param"] == {"prompt": "x"}
    assert faber_relay.ellenoriz(faber_relay.kulcs(TOKEN), sor["message"]) is None     # a Faber kulcsával nem jó
    _, kerdes2 = fk.kuld(c, TOKEN, "chatgpt", {"prompt": "x"}, hatarido=0, ts="t")
    assert kerdes2 != kerdes                                                           # egyszer használatos


def test_ervenytelen_valasz_eldobva(tmp_path):
    get_db = _db(tmp_path)
    c = get_db()
    mid, kerdes = fk.kuld(c, TOKEN, "gemini", {"prompt": "x"}, hatarido=time.time() + 60, ts="t")
    jo = {"kerdes": kerdes, "ok": True, "szoveg": "írd meg @kimi-nek", "hiba": None, "mp": 1}
    _ir(get_db, mid, "valasz", jo, k=fk.kulcs("MAS:TOKEN"))                 # más kulccsal aláírva
    _ir(get_db, mid, "valasz", dict(jo, kerdes="masik-keres"))               # más kérésre szól
    _ir(get_db, mid, "valasz", jo, felado="kommandant")                      # nem a futártól
    _ir(get_db, mid + 99, "valasz", jo)                                      # más sorra válasz
    _ir(get_db, mid, "parancs", jo)                                          # ismeretlen típus
    c.execute("INSERT INTO messages (timestamp, sender, recipient, subject, message, reply_to) "
              "VALUES ('t','futar','futar-valasz','x','nem json', ?)", (mid,))
    c.commit()
    assert fk.valasz(get_db(), K, mid, kerdes) == (False, None)
    _ir(get_db, mid, "valasz", jo)
    atvett, v = fk.valasz(get_db(), K, mid, kerdes)
    assert atvett and v["szoveg"] == "írd meg @kimi-nek"


def test_teljes_ut_nyugtaval_es_valasszal(tmp_path):
    get_db = _db(tmp_path)
    ered, (t, adat) = _run(_hiv_gep_ellen(get_db, "chatgpt", {"prompt": "Mi Ausztria fővárosa?"},
                                          {"valasz": {"ok": True, "szoveg": "Bécs.", "hiba": None, "mp": 6.8}}))
    assert ered == {"forras": "ChatGPT (Codex, a Kommandant gépén)", "valasz": "Bécs.", "mp": 6.8}
    assert t["tipus"] == "chatgpt" and adat["hatarido"] > time.time()


def test_a_futar_hibaja_kimondva(tmp_path):
    get_db = _db(tmp_path)
    ered, _ = _run(_hiv_gep_ellen(get_db, "gemini", {"prompt": "x"},
                                  {"valasz": {"ok": False, "szoveg": "", "hiba": "a Gemini nincs bejelentkezve", "mp": 0}}))
    assert ered == {"error": "a Gemini nincs bejelentkezve"}


def test_idotullepes_nyugta_nelkul_a_gep_nem_elerheto(tmp_path):
    get_db = _db(tmp_path)
    t0 = time.monotonic()
    ered = json.loads(_run(fk.hiv("chatgpt", {"prompt": "x"}, get_db=get_db, token=TOKEN, atvetel_s=0.1, lepes_s=0.01)))
    assert ered == {"error": "a géped most nem elérhető (ki van kapcsolva?)"} and time.monotonic() - t0 < 2


def test_idotullepes_nyugta_utan(tmp_path):
    get_db = _db(tmp_path)
    ered, _ = _run(_hiv_gep_ellen(get_db, "chatgpt", {"prompt": "x"}, {"nyugta": True}, atvetel_s=0.5, varakozas_s=0.3))
    assert "átvette a kérést" in ered["error"]


def test_csak_a_harom_muvelet_es_parameter_feherlista(tmp_path):
    get_db = _db(tmp_path)
    for m in ("shell", "bash", "faber", ""):
        assert "ismeretlen futár-művelet" in json.loads(_run(fk.hiv(m, {"prompt": "x"}, get_db=get_db, token=TOKEN)))["error"]
    assert fk._param("tudasbazis", {"kerdes": "x", "cmd": "rm -rf ~", "n": 999, "teljes": True}) == (
        {"kerdes": "x", "szurok": [], "tol": None}, None)                  # csak a fehérlistás kulcsok mennek tovább
    assert fk._param("chatgpt", {"prompt": "x", "sandbox": "danger-full-access"}) == ({"prompt": "x"}, None)
    assert fk._param("tudasbazis", {"kerdes": "x", "szurok": ["evil"]})[0] is None
    assert fk._param("tudasbazis", {"kerdes": "x", "tol": "--ig 2020"})[0] is None
    assert fk._param("gemini", {"prompt": "x" * (fk.PROMPT_PLAFON + 1)})[0] is None
    assert fk._param("chatgpt", {})[0] is None
    assert _keresek(get_db) == []                                         # semmi nem ment ki a gépre


def test_token_nelkul_nincs_csatorna_es_eszkoz(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert fk.eszkoz_definiciok() == []
    get_db = _db(tmp_path)
    assert "nincs Telegram-token" in json.loads(_run(fk.hiv("chatgpt", {"prompt": "x"}, get_db=get_db)))["error"]


# ── 2. A tudásbázis csak Claude-nak ─────────────────────────────────────

def test_eszkozok_csak_claude_elsodlegessegnel(monkeypatch):
    assert [d["function"]["name"] for d in fk.eszkoz_definiciok()] == ["tudasbazis", "chatgpt", "gemini"]
    monkeypatch.setenv("LLM_ELSODLEGES", "siliconflow")
    assert [d["function"]["name"] for d in fk.eszkoz_definiciok()] == ["chatgpt", "gemini"]
    monkeypatch.delenv("LLM_ELSODLEGES")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert [d["function"]["name"] for d in fk.eszkoz_definiciok()] == ["chatgpt", "gemini"]


@pytest.mark.parametrize("modell", ["deepseek-ai/DeepSeek-V4-Pro", "moonshotai/Kimi-K3", "zai-org/GLM-5.3-Flash",
                                    "tencent/Hy3", "", None, "gpt-6", "xclaude-3"])
def test_sf_modell_nem_futtathatja_a_tudasbazist(tmp_path, modell):
    get_db = _db(tmp_path)
    ered = json.loads(_run(fk.hiv("tudasbazis", {"kerdes": "mikor döntöttünk?"}, get_db=get_db, token=TOKEN,
                                  modell=modell)))
    assert ered["error"].startswith("a tudásbázis csak Claude-nak megy ki")
    assert _keresek(get_db) == []                                         # a kérés el sem indult


def test_claude_modell_futtathatja_a_tudasbazist(tmp_path):
    get_db = _db(tmp_path)
    ered, (t, adat) = _run(_hiv_gep_ellen(get_db, "tudasbazis", {"kerdes": "mikor döntöttünk a Postgres mellett?",
                                                                 "szurok": ["jegyzet"], "tol": "2026-01-01"},
                                          {"valasz": {"ok": True, "szoveg": "2026-08-14: PG", "hiba": None, "mp": 1}}))
    assert ered["valasz"] == "2026-08-14: PG"
    assert adat["param"] == {"kerdes": "mikor döntöttünk a Postgres mellett?", "szurok": ["jegyzet"], "tol": "2026-01-01"}


def test_sf_fele_eszkozok():
    for n in ("ai_query", "ai_task", "create_task", "execute_recipe", "create_recipe", "schedule_recipe", "send_message"):
        assert fk.sf_fele(n, {})
    assert fk.sf_fele("agora", {"action": "comment"}) and not fk.sf_fele("agora", {"action": "feed"})
    assert fk.sf_fele("story_comments", {"body": "x"}) and not fk.sf_fele("story_comments", {})
    for n in ("tudasbazis", "chatgpt", "gemini", "gmail_search", "send_email", "web_search", "lexonomics"):
        assert not fk.sf_fele(n, {})


def test_a_sf_ugynokok_eszkozkeszleteben_nincs_benne():
    import server
    from feldwebel import responder
    assert not set(fk.MUVELETEK) & set(server.SUBAGENT_TOOL_NAMES)
    assert not set(fk.MUVELETEK) & {t["function"]["name"] for t in server.SUBAGENT_TOOL_DEFS}
    assert not set(fk.MUVELETEK) & {t["function"]["name"] for t in responder.TOOLS}   # a statikus listában sincs
    src = open(server.__file__, encoding="utf-8").read()
    assert "eszkoz_definiciok()" not in src.replace("kulso_mcp.eszkoz_definiciok()", "")
    assert "_futar_eszkozok" not in src
    import inspect
    assert "_futar_eszkozok()" in inspect.getsource(responder._call_deepseek)


def test_a_futar_sorai_nem_kerulnek_sf_kontextusba():
    """A SiliconFlow-ügynökök kontextusépítője (build_agent_context + _get_inbox_summary) a futár sorait nem veszi fel."""
    import server
    from pyramid.context_builder import build_agent_context
    jel = "FUTARJEL7731"
    c = server.get_db()
    for felado, cimzett, uz in (("futar", "futar-valasz", f"{jel} válasz"), ("feldwebel", "futar", f"{jel} kérés"),
                                ("capture-daemon", "futar-valasz", f"{jel} álcázott"),
                                ("capture-daemon", "kommandant", "RENDES-LEVEL-4412")):
        c.execute("INSERT INTO messages (timestamp, sender, recipient, subject, message, priority) "
                  "VALUES (?, ?, ?, ?, ?, 'normal')", ("2099-01-01T00:00:00+00:00", felado, cimzett, f"t {uz}", uz))
    c.commit()
    c.close()
    inbox = server._get_inbox_summary()
    assert "RENDES-LEVEL-4412" in inbox and jel not in inbox
    for ugynok in ("kimi", "deepseek", "glm5"):
        assert jel not in build_agent_context(agent_id=ugynok, inbox_summary=inbox, relevance_query=jel,
                                              cross_agent_rag=True)


# ── 3. A Feldwebel köre ─────────────────────────────────────────────────

def _eszkozhivas(nev, args, modell):
    return {"model": modell, "choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": nev, "arguments": json.dumps(args)}}]}}]}


def _szoveg(s, modell):
    return {"model": modell, "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": s}}]}


@pytest.fixture
def feldwebel(tmp_path, monkeypatch):
    """Feldwebel-kör hamis LLM-mel: `kapu` = a Bridge közös kapuja (sf_chat, SF-tartalékkal), `claude` = a közvetlen
    Anthropic-kapu (llm_kapu.chat). Mindkettő a sorban kapott válaszokat adja, és feljegyzi a payloadot."""
    import feldwebel as fw
    import llm_kapu
    from feldwebel import BridgeContext, responder
    get_db = _db(tmp_path)
    hivasok = {"kapu": [], "claude": [], "hiv": [], "push": []}
    sor = {"kapu": [], "claude": []}

    async def kapu(payload, purpose=""):
        hivasok["kapu"].append(json.loads(json.dumps(payload)))
        return sor["kapu"].pop(0), None, []

    async def claude(payload, purpose=""):
        hivasok["claude"].append(json.loads(json.dumps(payload)))
        return sor["claude"].pop(0), None, []

    async def push(s):
        hivasok["push"].append(s)

    async def ures():
        return []
    ctx = BridgeContext(telegram_push=push, get_db=get_db, llm_chat=kapu)
    monkeypatch.setattr(fw, "_ctx", ctx)
    monkeypatch.setattr(llm_kapu, "chat", claude)
    monkeypatch.setattr(responder, "_kulso_eszkozok", ures)
    monkeypatch.setattr(responder, "_build_system_prompt", lambda ctx, query="": "rendszer")
    return responder, get_db, hivasok, sor


def _elozmeny(get_db):
    c = get_db()
    try:
        return [dict(r) for r in c.execute("SELECT role, agent_id, content FROM telegram_history ORDER BY id")]
    finally:
        c.close()


def test_kor_tudasbazissal_csak_claude_es_jelolt_elozmeny(feldwebel, monkeypatch):
    responder, get_db, hivasok, sor = feldwebel
    kapott = {}

    async def hamis_hiv(muvelet, args, *, get_db, modell=""):
        kapott.update(muvelet=muvelet, modell=modell)
        return json.dumps({"forras": "tudásbázis", "valasz": "TITKOS-KB-123: 2026-08-14"})
    monkeypatch.setattr(fk, "hiv", hamis_hiv)
    sor["kapu"] = [_eszkozhivas("tudasbazis", {"kerdes": "mikor döntöttünk?"}, "claude-sonnet-5-5")]
    sor["claude"] = [_szoveg("Augusztus 14-én (TITKOS-KB-123).", "claude-sonnet-5-5")]
    assert _run(responder.respond("mikor döntöttünk a Postgres mellett?", "42")).startswith("Augusztus 14")
    assert kapott == {"muvelet": "tudasbazis", "modell": "claude-sonnet-5-5"}
    assert len(hivasok["kapu"]) == 1                       # az első kör a közös kapun (az eszközlistával)
    assert "tudasbazis" in {t["function"]["name"] for t in hivasok["kapu"][0]["tools"]}
    assert len(hivasok["claude"]) == 1                     # a tudásbázis-találat UTÁN csak a közvetlen Anthropic-kapu
    assert "TITKOS-KB-123" in json.dumps(hivasok["claude"][0])
    assert all("TITKOS-KB-123" not in json.dumps(p) for p in hivasok["kapu"])
    e = _elozmeny(get_db)
    assert e[-1]["role"] == "assistant" and e[-1]["agent_id"] == "deepseek" + fk.CLAUDE_CSAK_JEL


def test_sf_tartalekra_esett_kor_nem_futtatja_a_tudasbazist(feldwebel):
    responder, get_db, hivasok, sor = feldwebel
    sor["kapu"] = [_eszkozhivas("tudasbazis", {"kerdes": "mikor döntöttünk?"}, "moonshotai/Kimi-K3"),
                   _szoveg("Most nem érem el.", "moonshotai/Kimi-K3")]
    _run(responder.respond("mikor döntöttünk?", "42"))
    eszkoz_valasz = [m for m in hivasok["kapu"][1]["messages"] if m.get("role") == "tool"][0]["content"]
    assert json.loads(eszkoz_valasz)["error"].startswith(fk.CSAK_CLAUDE)
    assert _keresek(get_db) == [] and hivasok["claude"] == []
    assert _elozmeny(get_db)[-1]["agent_id"] == "deepseek"            # nincs mit jelölni


def test_jelolt_elozmeny_sf_elsodlegessegnel_rejtve(feldwebel, monkeypatch):
    responder, get_db, hivasok, sor = feldwebel
    monkeypatch.setenv("LLM_ELSODLEGES", "siliconflow")
    c = get_db()
    c.execute("INSERT INTO telegram_history (chat_id, role, agent_id, content, timestamp) VALUES "
              "('42','user','deepseek','mikor döntöttünk?','2026-11-22T10:00:00'), "
              "('42','assistant','deepseek:claude-csak','TITKOS-KB-ELOZMENY augusztus 14','2026-11-22T10:00:01')")
    c.commit()
    c.close()
    sor["kapu"] = [_szoveg("Rendben.", "deepseek-ai/DeepSeek-V4-Pro")]
    _run(responder.respond("és most mi a helyzet?", "42"))
    p = json.dumps(hivasok["kapu"][0], ensure_ascii=False)
    assert "TITKOS-KB-ELOZMENY" not in p and fk.REJTETT_ELOZMENY in p
    assert "tudasbazis" not in {t["function"]["name"] for t in hivasok["kapu"][0]["tools"]}


def test_jelolt_elozmenynel_a_kor_claude_es_nem_hiv_sf_ugynokot(feldwebel):
    responder, get_db, hivasok, sor = feldwebel
    c = get_db()
    c.execute("INSERT INTO telegram_history (chat_id, role, agent_id, content, timestamp) VALUES "
              "('42','assistant','deepseek:claude-csak','TITKOS-KB-ELOZMENY','2026-11-22T10:00:01')")
    c.commit()
    c.close()
    sor["claude"] = [_eszkozhivas("ai_query", {"agent": "kimi", "prompt": "TITKOS-KB-ELOZMENY"}, "claude-sonnet-5-5"),
                     _szoveg("Ezt nem adom tovább Kiminek.", "claude-sonnet-5-5")]
    _run(responder.respond("kérdezd meg erről Kimit", "42"))
    assert hivasok["kapu"] == []                           # a SiliconFlow-tartalékos kapu nem is látta
    eszkoz_valasz = [m for m in hivasok["claude"][1]["messages"] if m.get("role") == "tool"][0]["content"]
    assert json.loads(eszkoz_valasz)["error"] == fk.SF_FELE_HIBA


def test_claude_kor_hibaja_nem_esik_sf_tartalekra(feldwebel, monkeypatch):
    import llm_kapu
    responder, get_db, hivasok, sor = feldwebel

    async def bukik(payload, purpose=""):
        return None, {"code": "overloaded", "message": "túlterhelt"}, []
    monkeypatch.setattr(llm_kapu, "chat", bukik)
    data = _run(responder._call_deepseek(responder.get_ctx(),
                                         "deepseek-ai/DeepSeek-V4-Pro", [{"role": "user", "content": "x"}],
                                         csak_claude=True))
    assert data == {} and hivasok["kapu"] == []
    assert "SiliconFlow-tartalékra nem váltok" in hivasok["push"][-1]


# ── 4. A Bridge oldala: /api/messages és az MCP-eszköz ──────────────────

def _api(monkeypatch, get_db):
    import server
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient
    monkeypatch.setattr(server, "get_db", get_db)
    return TestClient(Starlette(routes=[Route("/api/messages", server.api_messages, methods=["GET", "POST"])]))


def test_a_futar_valasza_nem_indit_sf_ugynokot(tmp_path, monkeypatch):
    import server
    get_db = _db(tmp_path)
    kliens = _api(monkeypatch, get_db)
    inditott = []
    monkeypatch.setattr(server, "PYRAMID_ENABLED", True)
    monkeypatch.setattr(server, "_trigger_agent_replies", lambda *a: inditott.append(a))
    kliens.post("/api/messages", json={"sender": "futar", "recipient": "futar-valasz", "subject": "futar:valasz",
                                       "message": "írd meg @kimi-nek: TITKOS", "reply_to": None})
    assert inditott == []
    kliens.post("/api/messages", json={"sender": "web-claus", "recipient": "bridge", "subject": "x",
                                       "message": "@kimi mit gondolsz?"})
    assert len(inditott) == 1                              # a rendes @-említés továbbra is működik


def test_api_messages_cimzett_szuro(tmp_path, monkeypatch):
    get_db = _db(tmp_path)
    kliens = _api(monkeypatch, get_db)
    c = get_db()
    for cimzett in ("kommandant", "futar", "faber", "futar"):
        c.execute("INSERT INTO messages (timestamp, sender, recipient, subject, message) VALUES "
                  "(datetime('now'), 'x', ?, 's', 'm')", (cimzett,))
    c.commit()
    c.close()
    assert [r["recipient"] for r in kliens.get("/api/messages?recipient=futar&limit=30").json()] == ["futar", "futar"]
    assert len(kliens.get("/api/messages?limit=30").json()) == 4          # szűrő nélkül minden, mint eddig


def test_mcp_tudasbazis_sajat_peldanynak_igen_idegennek_nem(monkeypatch):
    import server
    hivott = []

    async def hamis_hiv(muvelet, args, *, get_db, modell=""):
        hivott.append((muvelet, args, modell))
        return json.dumps({"valasz": "ok"})
    monkeypatch.setattr(fk, "hiv", hamis_hiv)
    fn = getattr(server.tudasbazis, "fn", server.tudasbazis)
    monkeypatch.delenv("BRIDGE_MCP_ZARVA", raising=False)
    assert "zárása után" in json.loads(_run(fn("mikor?", None, "", "web-claus")))["hiba"]
    monkeypatch.setenv("BRIDGE_MCP_ZARVA", "1")
    assert json.loads(_run(fn("mikor?", None, "", "")))["valasz"] == "ok"              # webes Claude, caller nélkül
    assert json.loads(_run(fn("mikor?", ["jegyzet"], "2026-01-01", "cli-claus")))["valasz"] == "ok"
    for idegen in ("youngereka", "yr-reka", "idegen"):
        assert "saját példány" in json.loads(_run(fn("mikor?", None, "", idegen)))["hiba"]
    assert len(hivott) == 2 and all(fk.claude_modell(m) for _, _, m in hivott)
    assert hivott[1][1] == {"kerdes": "mikor?", "szurok": ["jegyzet"], "tol": "2026-01-01"}
