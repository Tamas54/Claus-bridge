"""NOTRUF: a riasztás a Bridge üzenet-táblájába is megy, a Telegram-bukás nem néma (2026-09-21).

    .venv/bin/python -m pytest test_notruf_bridge_uzenet.py -q
"""
import asyncio
import sqlite3

import youngereka_notruf as n


def _con():
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, sender TEXT, recipient TEXT, subject TEXT, message TEXT, priority TEXT, thread_id INTEGER, reply_to INTEGER, status TEXT)")
    return con


def _futtat(push):
    con = _con(); esem = []
    r = asyncio.run(n.send(con, "YoungeReka", "Réka", "tamas", "baj van <most> & segíts",
                           telegram_push=push, event=lambda c, i, k, v: esem.append((k, v))))
    sorok = con.execute("SELECT recipient, subject, priority, status, message FROM messages").fetchall()
    return r, sorok, esem


def test_telegram_bukik_de_a_bridge_uzenet_megvan():
    async def rossz(text): raise RuntimeError("HTTP 400")
    r, sorok, esem = _futtat(rossz)
    assert r["sikeres"] is True and "Bridge felületén" in r["uzenet"] and "Telegram" in r["uzenet"]
    assert len(sorok) == 1 and sorok[0]["recipient"] == "kommandant" and sorok[0]["priority"] == "urgent" and sorok[0]["status"] == "unread"
    assert "&lt;most&gt; &amp;" in sorok[0]["message"]
    assert esem and "telegram=False bridge=True" in esem[0][1]


def test_telegram_ok_es_bridge_is():
    kuldott = []
    async def jo(text): kuldott.append(text)
    r, sorok, esem = _futtat(jo)
    assert r["sikeres"] is True and r["uzenet"].startswith("**Szóltam Tamásnak,") and len(sorok) == 1 and kuldott
    assert "telegram=True bridge=True" in esem[0][1]


def test_minden_bukik_nem_nema():
    async def rossz(text): raise RuntimeError("x")
    con = sqlite3.connect(":memory:")
    r = asyncio.run(n.send(con, "YoungeReka", "Réka", "tamas", "", telegram_push=rossz, event=lambda *a: None))
    assert r["sikeres"] is False and "Nem tudtam elérni" in r["uzenet"]
