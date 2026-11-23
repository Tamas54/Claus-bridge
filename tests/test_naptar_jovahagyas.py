"""HATÁRIDŐBŐL NAPTÁRBEJEGYZÉS (feldwebel/jovahagyas.py + a responder kódkapuja) — csak a Kommandant igenje után.

1. A Feldwebel `calendar_create`-hívása JAVASLAT: függő sor az `action_drafts`-ban, a naptárba NEM ír.
2. A Kommandant egy szava („igen”, „mehet”, „Igen.” hangból) a legutóbbi javaslatot beírja, emlékeztetővel — LLM
   nélkül; a „nem” elveti; a régebbi körre vonatkozó javaslatot a puszta „igen” nem írja be.
3. Ha a modell előbb szövegben kérdezett a naptárról, a Kommandant „igen”-jére tett hívása azonnal ír.
4. A Google-eseményben az emlékeztető (popup) ott van — a Telegram-úton és a webes felület jóváhagyó gombján is.
5. Függő javaslatnál a „mehet” a Feldwebelé, nem a Fabernek megy.
Hálózat nincs, a naptár hamisítvány: élesben itt semmi nem jön létre.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import feldwebel
from feldwebel import BridgeContext
from feldwebel import jovahagyas as J
from feldwebel import responder
from feldwebel.history import add_message, ensure_history_table

CHAT = "42"
HATARIDO = {"summary": "⏰ Határidő: fellebbezés a végzés ellen", "start_time": "2026-12-15",
            "description": "Végzés #3, kézbesítve 2026-11-30, 15 nap", "reminder_minutes": [900, 3780]}


def _run(c):
    return asyncio.run(c)


@pytest.fixture
def fw(monkeypatch, get_db):
    """Feldwebel-környezet hamis naptárral és Telegrammal."""
    ensure_history_table(get_db)
    naplo = {"push": [], "irt": [], "valasz": {"status": "created", "event_id": "e1",
                                               "link": "https://calendar.google.com/e1"}}

    async def push(s, *a, **k):
        naplo["push"].append(s)

    async def create(**kw):
        naplo["irt"].append(kw)
        return json.dumps(naplo["valasz"], ensure_ascii=False)

    ctx = BridgeContext(telegram_push=push, get_db=get_db, capture_state={"_create_calendar_func": create})
    monkeypatch.setattr(feldwebel, "_ctx", ctx)

    async def nincs_llm(*a, **k):
        raise AssertionError("a megerősítéshez nem kell LLM")
    monkeypatch.setattr(responder, "_call_deepseek", nincs_llm)
    return ctx, naplo


def _javasolt_kor(ctx, kerdes="Itt a végzés, mit tegyek?", args=None):
    """Egy Feldwebel-kör, amiben a modell naptárjavaslatot tett és rákérdezett."""
    add_message(CHAT, "user", kerdes)
    out = json.loads(_run(responder._execute_tool("calendar_create", dict(args or HATARIDO), ctx,
                                                  kor={"chat_id": CHAT, "text": kerdes})))
    add_message(CHAT, "assistant", f"A határidő december 15. {out.get('javaslat')} Beírjam a naptárba? (igen / mehet)")
    return out


def _draftok(get_db):
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT id, status, source_message_id, title, due_at, payload_json "
                                              "FROM action_drafts ORDER BY id")]
    finally:
        conn.close()


# ── 1. Igen / nem ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("szoveg,elvart", [
    ("igen", "igen"), ("Igen.", "igen"), ("mehet", "igen"), ("Mehet!", "igen"), ("ok, írd be", "igen"),
    ("igen, köszi", "igen"), ("jó, írd be a naptárba", "igen"), ("👍", "igen"), ("persze", "igen"),
    ("nem", "nem"), ("ne írd be", "nem"), ("mégse", "nem"), ("nem kell", "nem"), ("👎", "nem"),
    ("igen, de két nappal előtte", None), ("mehet a levél is?", None), ("jó reggelt", None), ("", None),
    ("Kimi, mi a helyzet?", None),
])
def test_valasz(szoveg, elvart):
    assert J.valasz(szoveg) == elvart


def test_emlekeztetok_ervenyesitve_es_szovegben():
    assert J.emlekezteto_percek([900, "3780", 900, -5, 50000, "x"]) == [3780, 900]
    assert J.emlekezteto_percek(None) == [] and J.google_emlekeztetok([]) is None
    assert J.google_emlekeztetok([60]) == {"useDefault": False, "overrides": [{"method": "popup", "minutes": 60}]}
    assert J.emlekezteto_szoveg([3780, 900], True) == "3 nappal előtte 9:00-kor, 1 nappal előtte 9:00-kor"
    assert J.emlekezteto_szoveg([1440, 60, 15], False) == "1 nappal előtte, 1 órával előtte, 15 perccel előtte"
    s = J.javaslat_szoveg({"summary": "Fogorvos", "start": "2026-12-15T10:00:00", "end": "2026-12-15T11:00:00",
                           "reminder_minutes": [60]})
    assert s == "Fogorvos — 2026. december 15., kedd 10:00–11:00 · emlékeztető: 1 órával előtte"


# ── 2. A javaslat nem ír; egy szó dönt ───────────────────────────────────

def test_a_modell_hivasa_csak_javaslat(fw, get_db):
    ctx, naplo = fw
    out = _javasolt_kor(ctx)
    assert out["status"] == "jovahagyasra_var" and out["beirva"] is False and naplo["irt"] == []
    assert "2026. december 15., kedd (egész nap)" in out["javaslat"] and "3 nappal előtte 9:00-kor" in out["javaslat"]
    assert "Beírjam a naptárba?" in out["teendo"]
    d = _draftok(get_db)
    assert len(d) == 1 and d[0]["status"] == "pending" and d[0]["source_message_id"] == f"feldwebel:{CHAT}"
    assert d[0]["title"] == HATARIDO["summary"] and d[0]["due_at"] == "2026-12-15"


@pytest.mark.parametrize("igen", ["mehet", "Igen."])
def test_egy_szo_eleg_es_emlekeztetovel_ir(fw, get_db, igen):
    ctx, naplo = fw
    _javasolt_kor(ctx)
    valasz = _run(responder.respond(igen, CHAT))
    assert len(naplo["irt"]) == 1
    irt = naplo["irt"][0]
    assert irt["summary"] == HATARIDO["summary"] and irt["start"] == "2026-12-15"
    assert irt["reminder_minutes"] == [3780, 900] and irt["caller"] == "feldwebel"
    assert "Beírtam a naptárba" in valasz and "https://calendar.google.com/e1" in valasz
    assert "FELDWEBEL" in naplo["push"][-1] and "Beírtam a naptárba" in naplo["push"][-1]
    assert _draftok(get_db)[0]["status"] == "created"
    # második „igen” már nem ír újra (nincs függő javaslat → a rendes kör jönne)
    assert _run(J.kezel(ctx, "igen", CHAT)) is None and len(naplo["irt"]) == 1


def test_nem_elveti(fw, get_db):
    ctx, naplo = fw
    _javasolt_kor(ctx)
    valasz = _run(responder.respond("ne írd be", CHAT))
    assert naplo["irt"] == [] and "nem írom be" in valasz and _draftok(get_db)[0]["status"] == "rejected"


def test_regebbi_korre_a_puszta_igen_nem_ir(fw, get_db):
    ctx, naplo = fw
    _javasolt_kor(ctx)
    add_message(CHAT, "user", "és mennyi az illeték?")          # közben más kérdés jött
    add_message(CHAT, "assistant", "Az illeték 5000 Ft. Küldjem el a levelet?")
    assert _run(J.kezel(ctx, "igen", CHAT)) is None              # a „mehet” a legutóbbi kérdésre felel
    assert naplo["irt"] == [] and _draftok(get_db)[0]["status"] == "pending"   # a webes felületen még jóváhagyható


def test_modositott_igen_a_modelle(fw):
    ctx, naplo = fw
    _javasolt_kor(ctx)
    assert _run(J.kezel(ctx, "igen, de két nappal előtte szólj", CHAT)) is None
    assert naplo["irt"] == []


def test_ha_az_iras_bukik_kimondja_es_approved_marad(fw, get_db):
    ctx, naplo = fw
    naplo["valasz"] = {"error": "Calendar not initialized"}
    _javasolt_kor(ctx)
    valasz = _run(responder.respond("mehet", CHAT))
    assert "Nem sikerült beírni" in valasz and "Calendar not initialized" in valasz
    assert _draftok(get_db)[0]["status"] == "approved"


# ── 3. Ha a modell előbb szövegben kérdezett ─────────────────────────────

def test_szoveges_kerdes_utani_igenre_azonnal_ir(fw, get_db):
    ctx, naplo = fw
    add_message(CHAT, "user", "Mi a határidő?")
    add_message(CHAT, "assistant", "December 15. Beírjam a naptárba, emlékeztetővel?")
    add_message(CHAT, "user", "igen")
    out = json.loads(_run(responder._execute_tool("calendar_create", dict(HATARIDO), ctx,
                                                  kor={"chat_id": CHAT, "text": "igen"})))
    assert out["status"] == "created" and len(naplo["irt"]) == 1 and _draftok(get_db) == []


def test_igen_masra_nem_ir_naptarba(fw, get_db):
    ctx, naplo = fw
    add_message(CHAT, "user", "Írj levelet Jánosnak")
    add_message(CHAT, "assistant", "Itt a piszkozat. Elküldjem?")
    add_message(CHAT, "user", "igen")
    out = json.loads(_run(responder._execute_tool("calendar_create", dict(HATARIDO), ctx,
                                                  kor={"chat_id": CHAT, "text": "igen"})))
    assert out["status"] == "jovahagyasra_var" and naplo["irt"] == []


def test_kor_nelkul_sosem_ir(fw):
    ctx, naplo = fw
    out = json.loads(_run(responder._execute_tool("calendar_create", dict(HATARIDO), ctx)))
    assert out["status"] == "jovahagyasra_var" and naplo["irt"] == []


def test_a_prompt_es_az_eszkoz_leirasa():
    # 11-23: a naptár-útmutató a `naptar_feladat` kategóriával jön (melléklet esetén előtöltve)
    from feldwebel import eszkoz_ut
    u = eszkoz_ut.KATEGORIAK["naptar_feladat"]["utmutato"]
    assert "HATÁRIDŐ → NAPTÁR" in u and "Beírjam a naptárba? (igen / mehet)" in u
    assert "naptar_feladat" in eszkoz_ut.szandek("[Melléklet #3: hatarozat.pdf] mit kell tennem?")
    d = next(t["function"] for t in responder.TOOLS if t["function"]["name"] == "calendar_create")
    assert "JAVASLATA" in d["description"] and "reminder_minutes" in d["parameters"]["properties"]
    assert "calendar_create" in responder.CONFIRM_TOOLS and "calendar_create" in responder.KODKAPU_TOOLS


# ── 4. Az emlékeztető a Google-eseményben ───────────────────────────────

class _Naptar:
    def __init__(self):
        self.body = None

    def events(self):
        return self

    def insert(self, calendarId, body):
        self.body = body
        return self

    def execute(self):
        return {"id": "e1", "htmlLink": "https://calendar.google.com/e1"}


def test_create_calendar_event_emlekeztetovel(monkeypatch):
    import server
    n = _Naptar()
    monkeypatch.setitem(server._capture_state, "calendar_service", n)
    out = json.loads(_run(server.create_calendar_event(summary="⏰ Határidő", start="2026-12-15",
                                                       reminder_minutes=[3780, 900])))
    assert out["status"] == "created"
    assert n.body["start"] == {"date": "2026-12-15"} and n.body["reminders"] == {
        "useDefault": False, "overrides": [{"method": "popup", "minutes": 3780}, {"method": "popup", "minutes": 900}]}
    _run(server.create_calendar_event(summary="Fogorvos", start="2026-12-15T10:00:00"))
    assert "reminders" not in n.body                              # emlékeztető nélkül: a naptár alapbeállítása


def test_webes_jovahagyas_is_emlekeztetovel_ir(fake_app, get_db):
    import plugins.action_extract as ax
    n = _Naptar()
    ax.register_tools(fake_app, {"get_db": get_db, "capture_state": {"calendar_service": n}})
    did = J.javasol(get_db, CHAT, {"summary": "Tárgyalás", "start": "2026-12-15T10:00:00",
                                   "end": "2026-12-15T12:00:00", "description": "Kúria", "reminder_minutes": [1440]})
    out = json.loads(_run(fake_app.tools["action_draft_decide"](draft_id=did, decision="approve")))
    assert out["calendar_created"] is True
    assert n.body["end"] == {"dateTime": "2026-12-15T12:00:00"} and n.body["description"] == "Kúria"
    assert n.body["reminders"]["overrides"] == [{"method": "popup", "minutes": 1440}]


# ── 5. Függő javaslatnál a „mehet” a Feldwebelé ─────────────────────────

def test_fuggo_naptarjavaslatnal_a_mehet_nem_a_fabere(fw, monkeypatch, get_db):
    import server
    ctx, _ = fw
    _javasolt_kor(ctx)
    latott = {}

    async def szandek(text, **k):
        latott.update(k)
        return None

    kapott = []

    async def respond(text, chat_id, *a, **k):
        kapott.append(text)
        return ""
    monkeypatch.setattr(server.faber_relay, "szandek", szandek)
    monkeypatch.setattr(server, "FELDWEBEL_ENABLED", True)
    monkeypatch.setattr(server, "get_db", get_db)
    monkeypatch.setattr(responder, "respond", respond)
    monkeypatch.setattr(server, "_feldwebel_kerdezett", lambda chat_id, percen_belul=30: False)   # fél óra eltelt
    _run(server._handle_telegram_message("mehet", CHAT))
    assert latott.get("feldwebel_kerdezett") is True and kapott == ["mehet"]


def test_hangbol_atirt_mehet_is_beir(fw, monkeypatch, get_db):
    """A teljes lánc: diktált „Mehet.” → Studio-átirat (hamis) → Telegram-út → a Feldwebel kódkapuja → naptár."""
    import server
    ctx, naplo = fw
    _javasolt_kor(ctx)
    tg_push, rovid = [], []

    async def _push(s, *a, **k):
        tg_push.append(s)

    async def _fajl(fid):
        return b"OggS", "voice/file_9.oga"

    async def _atir(raw, nev, *, hossz_s=0, rovid_valasz=False):
        rovid.append(rovid_valasz)
        return "Mehet."

    async def szandek(text, **k):
        return None
    monkeypatch.setattr(server, "_telegram_push", _push)
    monkeypatch.setattr(server, "_telegram_fajl", _fajl)
    monkeypatch.setattr(server.hang, "atir", _atir)
    monkeypatch.setattr(server.faber_relay, "szandek", szandek)
    monkeypatch.setattr(server, "FELDWEBEL_ENABLED", True)
    monkeypatch.setattr(server, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(server, "get_db", get_db)
    _run(server._hang_fogad({"voice": {"file_id": "v9", "duration": 1}}, CHAT))
    assert rovid == [True]                                         # a Feldwebel igenre várt → rövid-válasz prompt
    assert tg_push == ["🎙 Ezt értettem: „Mehet.”"]
    assert len(naplo["irt"]) == 1 and _draftok(get_db)[0]["status"] == "created"
    assert "Beírtam a naptárba" in naplo["push"][-1]
