"""Agora-esszé KIJELÖLT sztorira (`story_id`).

Kommandant (2026-09-11): „írjunk egy próbapublit képpel … erről a hírről."
Eddig az esszé-futás CSAK a nap 20 legerősebb sztorija közül választhatott.
A `story_id` a jelöltet erre az egyre szűkíti; a kvóták és a szűrők élnek.

Hálózat és LLM nélkül: a sztori-gyűjtő és a modellhívás dublőr; a futás a
kiválasztás után (a dublőr modell „hibájával") megáll — a teszt a
kiválasztás bemenetét méri, nem az esszét.
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import plugins.agora_duty as AD  # noqa: E402


def _mkdb():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    def get_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    conn = get_db()
    conn.executescript(AD._INIT_SQL)
    conn.execute("CREATE TABLE IF NOT EXISTS shared_memory (key TEXT, value TEXT)")
    conn.commit()
    conn.close()
    return get_db


_STORY = {"story_id": "a1ae4e1b9fb1b", "title": "Az egyik leggazdagabb magyart "
          "is meggyanúsították az óbudai korrupciós ügyben",
          "languages": ["hu"], "sphere": "hu_economy", "sources_count": 7,
          "markdown": "**Nyelvek:** hu"}


def _futtat(monkeypatch, story_id):
    hivas = {"gyujto": [], "prompt": []}

    async def _collect(limit=20, story_url=""):
        hivas["gyujto"].append({"limit": limit, "story_url": story_url})
        return [dict(_STORY)] if story_url else [
            dict(_STORY, story_id="bbbbbbbbbbbbb", title="Más hír")]

    async def _sf_chat(deps, agent_id, system, user, **k):
        hivas["prompt"].append(user)
        raise RuntimeError("dublőr: a kiválasztás után megállunk")
    monkeypatch.setattr(AD, "collect_stories", _collect)
    monkeypatch.setattr(AD, "_sf_chat", _sf_chat)
    monkeypatch.setattr(AD, "story_lang", lambda s: "hu")
    monkeypatch.setenv(AD.AGORA_AGENTS["von_takt"]["env_key"], "op-kulcs")
    rep = asyncio.run(AD.run_agora_essay({"get_db": _mkdb()}, "von_takt",
                                         dry_run=True, story_id=story_id))
    return rep, hivas


def test_a_kijelolt_story_az_egyetlen_jelolt(monkeypatch):
    rep, h = _futtat(monkeypatch, "a1ae4e1b9fb1b")
    assert h["gyujto"] == [{"limit": 20, "story_url": "/story/a1ae4e1b9fb1b"}]
    assert "id=a1ae4e1b9fb1b" in h["prompt"][0]
    assert "id=bbbbbbbbbbbbb" not in h["prompt"][0]
    assert "A TÉMA KIJELÖLT" in h["prompt"][0]
    assert rep["story_id"] == "a1ae4e1b9fb1b"


def test_kijeloles_nelkul_a_regi_valasztas(monkeypatch):
    rep, h = _futtat(monkeypatch, "")
    assert h["gyujto"] == [{"limit": 20, "story_url": ""}]
    assert "A TÉMA KIJELÖLT" not in h["prompt"][0]
    assert "LEGERŐSEBB témát" in h["prompt"][0]


def test_a_story_id_tisztitva_megy_tovabb(monkeypatch):
    rep, h = _futtat(monkeypatch, " A1AE4E1B9FB1B/../x ")
    assert h["gyujto"][0]["story_url"] == "/story/a1ae4e1b9fb1bx"
