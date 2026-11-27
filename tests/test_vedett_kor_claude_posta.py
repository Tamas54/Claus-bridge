"""TUDÁSBÁZIS-KÖRBŐL IS MEHET ÜZENET A CLAUDE-PÉLDÁNYNAK (Faber #10, 2026-11-27).

A hiba: a Kommandant kétszer kérte, hogy a cli-Claus (Claude Code a gépen) nézze meg a
github.com/google-gemini/gemini-api-cli repót. A beszélgetésben korábbi tudásbázis-válasz volt (`:claude-csak` jelű
előzmény), ezért a kör védett lett, és a kapu (`futar_kliens.sf_fele`) a `send_message`-et címzettől függetlenül
tiltotta — mintha a cli-Claus kínai ügynök volna. A cli-Claus és a web-Claus Claude-modell, a teljes tudásbázist maga
is eléri; a nekik szóló üzenet nem visz semmit a kínai modellekhez.

A próbák (hálózat nélkül):
1. a kapu: Claude-példány címzett mehet, minden más (kimi, deepseek, glm5, kommandant, üres) tiltott marad;
2. végig, a Feldwebel valódi körével: jelölt előzmény → Claude-kör → `send_message` a cli-Claus-nak → a parancslista
   a cli-Claus postájában; ugyanabban a körben a Kimi-címzett elutasítva;
3. a továbbút zárva: a Claude-posták sorai a SiliconFlow-kontextusból (`SF_KIZARAS_SQL`) és a DeepSeek-formázású
   reggeli összefoglalóból kimaradnak (a cli-Claus postáját a gépi figyelő nem jelöli olvasottnak).
"""
from __future__ import annotations

import asyncio
import json

import pytest

import futar_kliens as fk

#: A Kommandant kért ellenőrző listája (Faber #10) — ezt kell megkapnia a cli-Claus-nak.
PARANCSLISTA = ("Kérlek, ellenőrizd a gépen a github.com/google-gemini/gemini-api-cli-t:\n"
                "1. which gemini-api\n2. gemini-api --version\n3. gemini-api whoami\n"
                "4. GEMINI_API_KEY jelen van-e (a kulcsot NE írd ki)\n"
                "5. mit ad a gemini-api-cli a meglévő eszközeinken túl")


def _run(c):
    return asyncio.run(c)


def _tablak(get_db):
    c = get_db()
    c.executescript(
        "CREATE TABLE IF NOT EXISTS telegram_history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, "
        "role TEXT NOT NULL, agent_id TEXT DEFAULT 'deepseek', content TEXT NOT NULL, timestamp TEXT NOT NULL);"
        "CREATE TABLE IF NOT EXISTS heartbeats (instance TEXT PRIMARY KEY, last_seen TEXT, session_info TEXT);"
        "CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, status TEXT, "
        "priority TEXT, assigned_to TEXT, created_at TEXT);")
    c.commit()
    c.close()


def _uzenetek(get_db):
    """A Feldwebel `send_message` eszközével küldött sorok (a kör saját válasznaplója — `feldwebel-deepseek` →
    kommandant — nem ide tartozik)."""
    c = get_db()
    try:
        return [dict(r) for r in c.execute("SELECT sender, recipient, subject, message FROM messages "
                                           "WHERE sender = 'feldwebel' ORDER BY id")]
    finally:
        c.close()


# ── 1. A kapu ───────────────────────────────────────────────────────────

def test_claude_peldanynak_mehet_masnak_nem():
    for cimzett in ("cli-claus", "web-claus", " CLI-Claus ", "Web-Claus"):
        assert not fk.sf_fele("send_message", {"recipient": cimzett}), cimzett
    for cimzett in ("kimi", "deepseek", "glm5", "kommandant", "bridge", "faber", "", None):
        assert fk.sf_fele("send_message", {"recipient": cimzett}), cimzett
    assert fk.sf_fele("send_message", {}) and fk.sf_fele("send_message", None)     # címzett nélkül: tiltott
    # a többi tiltás változatlan
    for nev in ("ai_query", "ai_task", "create_task", "execute_recipe", "create_recipe", "schedule_recipe"):
        assert fk.sf_fele(nev, {"recipient": "cli-claus"}), nev
    assert "cli-claus" in fk.SF_FELE_HIBA                   # a hibaszöveg megmondja, kinek MEHET


def test_vedett_eszkozhivas_a_cli_claus_postajaba_er_a_kimihez_nem(get_db, monkeypatch):
    import server
    from feldwebel import responder
    _tablak(get_db)
    monkeypatch.setattr(server, "get_db", get_db)

    class Ctx:
        capture_state = {"_send_message_func": server.send_message}
    Ctx.get_db = staticmethod(get_db)

    v = json.loads(_run(responder._execute_tool(
        "send_message", {"recipient": "cli-claus", "subject": "gemini-api-cli", "message": PARANCSLISTA}, Ctx(),
        modell="claude-sonnet-5-5", vedett=True)))
    assert v.get("status") == "sent", v
    v = json.loads(_run(responder._execute_tool(
        "send_message", {"recipient": "kimi", "subject": "x", "message": "TITKOS-KB"}, Ctx(),
        modell="claude-sonnet-5-5", vedett=True)))
    assert v["error"] == fk.SF_FELE_HIBA
    assert _uzenetek(get_db) == [{"sender": "feldwebel", "recipient": "cli-claus", "subject": "gemini-api-cli",
                                  "message": PARANCSLISTA}]


def test_a_claude_cimzett_egy_alakban_kerul_a_postaba(get_db, monkeypatch):
    """A kapu a „ CLI-Claus ” alakot is engedi; a sor ezért `cli-claus` címzettel kerül a táblába — különben a gépi
    figyelő (recipient=cli-claus) nem látná, és a SiliconFlow-kizárás (SF_KIZARAS_SQL) sem fogná meg."""
    import server
    from feldwebel import responder
    _tablak(get_db)
    monkeypatch.setattr(server, "get_db", get_db)

    class Ctx:
        capture_state = {"_send_message_func": server.send_message}
    Ctx.get_db = staticmethod(get_db)

    for cimzett in (" CLI-Claus ", "Web-Claus"):
        v = json.loads(_run(responder._execute_tool(
            "send_message", {"recipient": cimzett, "subject": "s", "message": "TITKOS-KB"}, Ctx(),
            modell="claude-sonnet-5-5", vedett=True)))
        assert v.get("status") == "sent", v
    assert [u["recipient"] for u in _uzenetek(get_db)] == ["cli-claus", "web-claus"]
    c = get_db()
    try:
        assert c.execute(f"SELECT COUNT(*) FROM messages WHERE {fk.SF_KIZARAS_SQL}").fetchone()[0] == 0
    finally:
        c.close()


# ── 2. Végig, a Feldwebel körével ───────────────────────────────────────

def _eszkozhivas(nev, args, modell, azon="t1"):
    return {"model": modell, "choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": "", "tool_calls": [
            {"id": azon, "type": "function", "function": {"name": nev, "arguments": json.dumps(args)}}]}}]}


def _szoveg(s, modell):
    return {"model": modell, "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": s}}]}


@pytest.fixture
def feldwebel(get_db, monkeypatch):
    """Feldwebel-kör hamis LLM-mel (a tests/test_futar.py mintája): `kapu` = a közös kapu (SiliconFlow-tartalékkal),
    `claude` = a közvetlen Anthropic-kapu. A `send_message` a valódi server.send_message-en megy."""
    import feldwebel as fw
    import llm_kapu
    import server
    from feldwebel import BridgeContext, responder
    monkeypatch.setenv("ANTHROPIC_API_KEY", "teszt-kulcs")
    monkeypatch.delenv("LLM_ELSODLEGES", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TOKEN")
    _tablak(get_db)
    monkeypatch.setattr(server, "get_db", get_db)
    hivasok = {"kapu": [], "claude": [], "push": []}
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
    ctx = BridgeContext(telegram_push=push, get_db=get_db, llm_chat=kapu,
                        capture_state={"_send_message_func": server.send_message})
    monkeypatch.setattr(fw, "_ctx", ctx)
    monkeypatch.setattr(llm_kapu, "chat", claude)
    monkeypatch.setattr(responder, "_kulso_eszkozok", ures)
    monkeypatch.setattr(responder, "_build_system_prompt", lambda ctx, query="": "rendszer")
    return responder, get_db, hivasok, sor


def _eszkoz_valaszok(payload) -> list:
    from feldwebel import eszkoz_ut
    return [m for m in payload["messages"] if m.get("role") == "tool" and m.get("tool_call_id") != eszkoz_ut.ELOTOLTES_ID]


def test_jelolt_elozmenyes_korben_a_cli_claus_megkapja_a_parancslistat(feldwebel):
    """A 11-27-i eset: a beszélgetésben korábbi tudásbázis-válasz van; a Kommandant azt kéri, a cli-Claus nézze meg a
    gemini-api-cli-t. Eddig: „…Bridge-üzenetbe … nem adom tovább”. Most: a lista a cli-Claus postájában."""
    responder, get_db, hivasok, sor = feldwebel
    c = get_db()
    c.execute("INSERT INTO telegram_history (chat_id, role, agent_id, content, timestamp) VALUES "
              "('42','assistant','deepseek:claude-csak','TITKOS-KB-ELOZMENY: a Gemini-fiók a gépen','2026-11-27T09:00:01')")
    c.commit()
    c.close()
    sor["claude"] = [
        _eszkozhivas("send_message", {"recipient": "cli-claus", "subject": "gemini-api-cli ellenőrzés",
                                      "message": PARANCSLISTA}, "claude-sonnet-5-5"),
        _szoveg("Elküldtem a cli-Claus-nak az ellenőrző listát.", "claude-sonnet-5-5")]
    valasz = _run(responder.respond("küldd el a cli-Claus-nak, hogy nézze meg a gemini-api-cli-t", "42"))
    assert valasz.startswith("Elküldtem")
    assert hivasok["kapu"] == []                            # a védett kör a SiliconFlow-tartalékos kaput nem is látta
    eredmeny = json.loads(_eszkoz_valaszok(hivasok["claude"][1])[0]["content"])
    assert eredmeny.get("status") == "sent", eredmeny
    assert "cli_claus_figyel" in eredmeny                   # a nyugta megmondja, figyel-e a gép
    uz = _uzenetek(get_db)
    assert uz == [{"sender": "feldwebel", "recipient": "cli-claus", "subject": "gemini-api-cli ellenőrzés",
                   "message": PARANCSLISTA}]
    for parancs in ("which gemini-api", "gemini-api --version", "gemini-api whoami", "GEMINI_API_KEY"):
        assert parancs in uz[0]["message"]


def test_jelolt_elozmenyes_korben_a_kinai_ugynoknek_tovabbra_sem(feldwebel):
    responder, get_db, hivasok, sor = feldwebel
    c = get_db()
    c.execute("INSERT INTO telegram_history (chat_id, role, agent_id, content, timestamp) VALUES "
              "('42','assistant','deepseek:claude-csak','TITKOS-KB-ELOZMENY','2026-11-27T09:00:01')")
    c.commit()
    c.close()
    sor["claude"] = [_eszkozhivas("send_message", {"recipient": "deepseek", "subject": "x",
                                                   "message": "TITKOS-KB-ELOZMENY"}, "claude-sonnet-5-5"),
                     _szoveg("Ezt nem adom tovább.", "claude-sonnet-5-5")]
    _run(responder.respond("küldd át a DeepSeeknek", "42"))
    assert json.loads(_eszkoz_valaszok(hivasok["claude"][1])[0]["content"])["error"] == fk.SF_FELE_HIBA
    assert _uzenetek(get_db) == []


# ── 3. A továbbút zárva ─────────────────────────────────────────────────

def _posta(get_db):
    c = get_db()
    c.executemany("INSERT INTO messages (timestamp, sender, recipient, subject, message, status) VALUES "
                  "(?, ?, ?, ?, ?, 'unread')", [
                      ("2026-11-27T09:01:00", "feldwebel", "cli-claus", "TITKOS-KB-TARGY", "TITKOS-KB-TORZS"),
                      ("2026-11-27T09:02:00", "cli-claus", "kommandant", "TITKOS-GEPI-VALASZ", "TITKOS-GEPI"),
                      ("2026-11-27T09:03:00", "web-claus", "cli-claus", "TITKOS-WEB-TARGY", "TITKOS-WEB"),
                      ("2026-11-27T09:04:00", "bridge", "kommandant", "Rendes Bridge-üzenet", "rendes")])
    c.commit()
    c.close()


def test_a_claude_postak_sorai_sf_kontextusba_nem_kerulnek(get_db):
    _posta(get_db)
    c = get_db()
    try:
        lathato = [r[0] for r in c.execute(f"SELECT subject FROM messages WHERE {fk.SF_KIZARAS_SQL}")]
    finally:
        c.close()
    assert lathato == ["Rendes Bridge-üzenet"]
    for regi in ("'futar'", "'nachtschicht'", "'faber'", "'ugynok:%'"):  # a korábbi kizárások megmaradtak
        assert regi in fk.SF_KIZARAS_SQL


def test_a_reggeli_osszefoglalo_a_deepseeknek_nem_adja_at_a_claude_postat(get_db, monkeypatch):
    from feldwebel import BridgeContext, briefing
    _tablak(get_db)
    _posta(get_db)
    kapott = []

    async def kapu(payload, purpose=""):
        kapott.append(json.dumps(payload, ensure_ascii=False))
        return {"choices": [{"message": {"content": "Jó reggelt, Kommandant! ☕"}}]}, None, []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "teszt-kulcs")
    ctx = BridgeContext(get_db=get_db, llm_chat=kapu, siliconflow_api_key="teszt")
    szakasz = briefing._get_message_section(ctx)
    assert "Rendes Bridge-üzenet" in szakasz and "TITKOS" not in szakasz
    _run(briefing.assemble_briefing(ctx))
    assert len(kapott) == 1 and "Rendes Bridge-üzenet" in kapott[0] and "TITKOS" not in kapott[0]
