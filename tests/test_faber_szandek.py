"""Faber-SZÁNDÉK perjel nélkül (2026-11-22) — felismerés, tartalék-heurisztika, továbbítás, bekötés.

A Kommandant: „én szóban akarom elmondani neki, hogy mi a parancs - nem akarok ilyen / parancsokat.”

Amit ezek a tesztek őriznek:
  1. az LLM (Haiku 5.5, sémára kötött kimenet) szándéka dönt; az `egyeb` None, vagyis a Bridge rendes beszélgetése,
  2. a kérelemszámot determinisztikusan ellenőrizzük (a kitalált szám nem megy át),
  3. kulcs nélkül vagy hibánál a magyar kulcsszavas heurisztika dönt, kétes esetben `egyeb`,
  4. ha a Feldwebel épp kérdezett, egy puszta „mehet” NEKI felel, nem a Fabernek,
  5. a továbbított tétel aláírt, `tipus="szandek"`, és a Faber ugyanúgy olvassa, mint a parancsot,
  6. a `_handle_telegram_message` az elején felismer, továbbít, és természetes nyugtát küld — a perjeles parancs
     és a Bridge-beszélgetés változatlan.
"""
import asyncio
import json
import sqlite3

import pytest

import faber_relay as f


def _run(coro):
    return asyncio.run(coro)


def _llm(szandek="egyeb", repo=None, kerelem=None, hiba=None):
    hivasok = []

    async def llm(system, szoveg, sema, **kw):
        hivasok.append({"system": system, "szoveg": szoveg, **kw})
        if hiba:
            raise hiba
        return sema(szandek=szandek, repo=repo, kerelem=kerelem), None
    llm.hivasok = hivasok
    return llm


# ── 1. LLM-út ───────────────────────────────────────────────────────────

def test_kivansag_repoval():
    llm = _llm("kivansag", repo="echolot")
    d = _run(f.szandek("Tanítsd meg az Echolotot, hogy a 403-as oldalt is megpróbálja újra", llm=llm))
    assert d == {"szandek": "kivansag", "repo": "echolot", "kerelem": None,
                 "szoveg": "Tanítsd meg az Echolotot, hogy a 403-as oldalt is megpróbálja újra"}
    h = llm.hivasok[0]
    assert h["szint"] == "olcso" and h["max_tokens"] <= 300        # olcsó és rövid
    assert len(h["system"]) < 1500


def test_egyeb_az_bridge_beszelgetes():
    assert _run(f.szandek("Mi a helyzet ma a forinttal?", llm=_llm("egyeb"))) is None


def test_kitalalt_kerelemszam_nem_megy_at():
    d = _run(f.szandek("élesítsd", llm=_llm("jovahagy", kerelem=7)))
    assert d["szandek"] == "jovahagy" and d["kerelem"] is None


def test_kimondott_kerelemszam_determinisztikusan():
    d = _run(f.szandek("a 3-ast élesítsd", llm=_llm("jovahagy")))
    assert d["kerelem"] == 3


@pytest.mark.parametrize("szoveg,szam", [("#12 mehet", 12), ("a 3-ast élesítsd", 3), ("kérelem 5 mégse", 5),
                                         ("a 4. kérelmet csináld vissza", 4), ("mehet", None)])
def test_kerelemszam(szoveg, szam):
    assert f._kerelem_szam(szoveg) == szam


def test_perjel_es_mention_nem_szandek():
    llm = _llm("kivansag")
    assert _run(f.szandek("/brief", llm=llm)) is None
    assert _run(f.szandek("@kimi mit gondolsz?", llm=llm)) is None
    assert _run(f.szandek("   ", llm=llm)) is None
    assert llm.hivasok == []


# ── 2. Tartalék-heurisztika ─────────────────────────────────────────────

@pytest.mark.parametrize("szoveg,elvart", [
    ("Élesítsd", "jovahagy"),
    ("mehet", "jovahagy"),
    ("igen, csináld", "jovahagy"),
    ("mégse", "elvet"),
    ("ne", "elvet"),
    ("csináld vissza", "vissza"),
    ("mi van a faberrel?", "allapot"),
    ("Mit tanultál ma?", "allapot"),
    ("Faber, tanuld meg beolvasni a ChatGPT-exportot a tudásbázisba", "kivansag"),
    ("tanuld meg a Lexonomicsban a bírósági határozatok letöltését", "kivansag"),
    ("Mi a helyzet ma a forinttal?", "egyeb"),
    ("mehet a holnapi brief is, de rövidebben és több számmal kérem", "egyeb"),   # hosszú: kétes
    ("nem", "egyeb"),                                                             # kétes
])
def test_heurisztika(szoveg, elvart):
    assert f.heurisztika(szoveg) == elvart


def test_kulcs_nelkul_a_heurisztika_dont(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    d = _run(f.szandek("Faber, tanuld meg beolvasni a ChatGPT-exportot a tudásbázisba"))
    assert d["szandek"] == "kivansag" and d["repo"] == "memoria-rag"
    assert _run(f.szandek("Szia, mi újság?")) is None


def test_llm_hibanal_a_heurisztika_dont():
    d = _run(f.szandek("csináld vissza", llm=_llm(hiba=RuntimeError("hálózat"))))
    assert d["szandek"] == "vissza"


# ── 3. A Feldwebel kérdésére adott „mehet” nem a Faberé ─────────────────

def test_feldwebel_kerdesere_a_puszta_mehet_a_bridge_e():
    assert _run(f.szandek("mehet", llm=_llm("jovahagy"), feldwebel_kerdezett=True)) is None
    assert _run(f.szandek("mégse", llm=_llm("elvet"), feldwebel_kerdezett=True)) is None
    # Kimondott Faber-jel mellett viszont a Faberé:
    assert _run(f.szandek("élesítsd", llm=_llm("jovahagy"), feldwebel_kerdezett=True))["szandek"] == "jovahagy"
    assert _run(f.szandek("a 3-ast mehet", llm=_llm("jovahagy"), feldwebel_kerdezett=True))["kerelem"] == 3


# ── 4. Aláírt továbbítás ────────────────────────────────────────────────

def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, sender TEXT, "
                "recipient TEXT, subject TEXT, message TEXT, priority TEXT, thread_id INTEGER, reply_to INTEGER, "
                "status TEXT)")
    return con


def test_szandek_tetel_alairva_a_faber_olvassa():
    con = _con()
    d = {"szandek": "jovahagy", "szoveg": "a 3-ast élesítsd", "repo": None, "kerelem": 3}
    mid = f.tovabbit(con, "123:TOKEN", "szandek", f.szandek_adat(d), "2026-11-22T12:00:00")
    sor = con.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    assert sor["recipient"] == "faber" and sor["subject"] == "faber:szandek"
    torzs = f.ellenoriz(f.kulcs("123:TOKEN"), sor["message"])
    assert torzs["tipus"] == "szandek"
    assert json.loads(torzs["adat"]) == d          # a Faber: szandek(json.loads(t["adat"]))


def test_minden_szandeknek_van_termeszetes_nyugtaja():
    for sz in f.SZANDEKOK:
        if sz != "egyeb":
            assert f.NYUGTA[sz] and not f.NYUGTA[sz].startswith("/")


# ── 5. Bekötés a Telegram-útba ──────────────────────────────────────────

import server  # noqa: E402


@pytest.fixture
def telegram(monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, sender TEXT, "
                "recipient TEXT, subject TEXT, message TEXT, priority TEXT, thread_id INTEGER, reply_to INTEGER, "
                "status TEXT)")
    con.execute("CREATE TABLE telegram_history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, role TEXT, "
                "agent_id TEXT, content TEXT, timestamp TEXT)")
    con.commit()
    con.close()

    def get_db():
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        return c
    pushed = []

    async def push(szoveg, *a, **kw):
        pushed.append(szoveg)
    monkeypatch.setattr(server, "get_db", get_db)
    monkeypatch.setattr(server, "_telegram_push", push)
    monkeypatch.setattr(server, "TELEGRAM_BOT_TOKEN", "123:TOKEN")
    monkeypatch.setattr(server, "FELDWEBEL_ENABLED", False)
    monkeypatch.setattr(server, "PYRAMID_ENABLED", False)
    return get_db, pushed


def test_a_telegram_ut_szandeket_tovabbit_es_nyugtaz(monkeypatch, telegram):
    get_db, pushed = telegram
    kapott = {}

    async def fake_szandek(text, *, feldwebel_kerdezett=False, **kw):
        kapott["fk"] = feldwebel_kerdezett
        return {"szandek": "kivansag", "szoveg": text, "repo": "studio", "kerelem": None}
    monkeypatch.setattr(server.faber_relay, "szandek", fake_szandek)
    _run(server._handle_telegram_message("Tanítsd meg a Studiónak a feliratozást", "42"))
    sorok = get_db().execute("SELECT recipient, subject, message FROM messages").fetchall()
    assert [(r["recipient"], r["subject"]) for r in sorok] == [("faber", "faber:szandek")]   # a Bridge-be NEM
    torzs = f.ellenoriz(f.kulcs("123:TOKEN"), sorok[0]["message"])
    assert json.loads(torzs["adat"])["repo"] == "studio"
    assert pushed == ["Rendben, szólok a Fabernek, hogy tanulja meg."]
    assert kapott["fk"] is False


def test_egyeb_eseten_a_bridge_beszelgetes_megy(monkeypatch, telegram):
    get_db, pushed = telegram

    async def fake_szandek(text, **kw):
        return None
    monkeypatch.setattr(server.faber_relay, "szandek", fake_szandek)
    _run(server._handle_telegram_message("Mi a helyzet a forinttal?", "42"))
    sorok = get_db().execute("SELECT recipient FROM messages").fetchall()
    assert [r["recipient"] for r in sorok] == ["bridge"]
    assert pushed == []


def test_a_szandekfelismeres_hibaja_nem_allitja_meg_az_utat(monkeypatch, telegram):
    get_db, _ = telegram

    async def rossz(text, **kw):
        raise RuntimeError("bumm")
    monkeypatch.setattr(server.faber_relay, "szandek", rossz)
    _run(server._handle_telegram_message("Mi a helyzet?", "42"))
    assert [r["recipient"] for r in get_db().execute("SELECT recipient FROM messages")] == ["bridge"]


def test_a_perjeles_parancs_valtozatlan(monkeypatch, telegram):
    get_db, pushed = telegram

    async def tilos(text, **kw):
        raise AssertionError("perjeles parancsra nem kell szándékfelismerés")
    monkeypatch.setattr(server.faber_relay, "szandek", tilos)
    _run(server._handle_telegram_message("/tanulj olvasd be a ChatGPT-exportot", "42"))
    sor = get_db().execute("SELECT subject FROM messages").fetchone()
    assert sor["subject"] == "faber:parancs"


def test_feldwebel_kerdezett_a_tortenetbol(telegram):
    get_db, _ = telegram
    from datetime import datetime, timedelta, timezone
    c = get_db()
    c.execute("INSERT INTO telegram_history (chat_id, role, content, timestamp) VALUES ('42','assistant',"
              "'Posztoljam a kommentet?', ?)", (datetime.now(timezone.utc).isoformat(),))
    c.execute("INSERT INTO telegram_history (chat_id, role, content, timestamp) VALUES ('43','assistant',"
              "'Posztoljam?', ?)", ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),))
    c.execute("INSERT INTO telegram_history (chat_id, role, content, timestamp) VALUES ('44','assistant',"
              "'Elküldtem.', ?)", (datetime.now(timezone.utc).isoformat(),))
    c.commit()
    c.close()
    assert server._feldwebel_kerdezett("42") is True
    assert server._feldwebel_kerdezett("43") is False      # régi kérdés
    assert server._feldwebel_kerdezett("44") is False      # nem kérdés
    assert server._feldwebel_kerdezett("99") is False      # nincs előzmény
