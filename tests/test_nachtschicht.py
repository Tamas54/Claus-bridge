"""NACHTSCHICHT (nachtschicht.py, 2026-11-22) — hálózat nélkül, hamis kapuval és hamis forrással.

Amit ezek a tesztek őriznek:
  1. AZ ENGEDÉLYLISTA: a modell csak olvasó, tervezet- és determinisztikus Studio-eszközt kap; a végrehajtó minden
     küldést, naptárírást, publikálást, SiliconFlow-ügynököt és a Studio AI-lépéseit elutasítja (a forrásig sem jut).
  2. A KERET: a kapu által könyvelt `koltes_usd`-ből számol; a keret előtt megáll, és a jelentés kimondja.
  3. NINCS SILICONFLOW-TARTALÉK: csak `llm_kapu.chat`; kulcs nélkül vagy kapuhibánál kimarad, és kimondja.
  4. A JELÖLÉS: a napló sorai (sender = recipient = 'nachtschicht') a SiliconFlow-ügynökök kontextusába nem kerülnek,
     a reggeli jelentés a Feldwebel-előzménybe csak-Claude jellel megy.
  5. AZ ÜTEMEZÉS: 05:50–06:30 között naponta egyszer, újraindulás után sem ismétel, kikapcsolható.
  6. AZ ANYAGGYŰJTÉS: determinisztikus jelek (hivatalos / határidős / kéréses levél, naptáresemény, határidős feladat);
     erős jel nélkül LLM-hívás sincs, és a jelentés egy mondat.
  7. A TERV szűrése, a panaszból javítás (legfeljebb 2 / éj, 24 órás tiltás), a Gmail-tervezet (küldés nincs), a Studio.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import futar_kliens
import llm_kapu
import nachtschicht as N
from server import memoria_ir

MOST = datetime(2026, 11, 23, 4, 50, tzinfo=timezone.utc)          # 05:50 Budapest (téli idő)


def _run(c):
    return asyncio.run(c)


@pytest.fixture
def get_db(tmp_path):
    ut = tmp_path / "n.db"
    c = sqlite3.connect(ut)
    c.executescript("""
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, sender TEXT NOT NULL,
          recipient TEXT NOT NULL, subject TEXT NOT NULL, message TEXT NOT NULL, priority TEXT DEFAULT 'normal',
          thread_id INTEGER, reply_to INTEGER, status TEXT DEFAULT 'unread');
        CREATE TABLE shared_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, value TEXT NOT NULL,
          category TEXT DEFAULT 'general', tags TEXT DEFAULT '', updated_by TEXT NOT NULL, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX idx_k ON shared_memory(key);
    """)
    c.commit()
    c.close()

    def _g():
        k = sqlite3.connect(ut)
        k.row_factory = sqlite3.Row
        return k
    return _g


@pytest.fixture
def kulcs(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt-TITOK")
    monkeypatch.setattr(llm_kapu, "_KOLTES", {"honap": "", "usd": 0.0, "hivasok": 0, "szintenkent": {}})


# ── hamis forrás és hamis kapu ──────────────────────────────────────────

HIVATALOS = {"id": "L1", "subject": "Átvételi értesítő (Feladó: LTKEING, Dokumentum: határozat)",
             "from": "Tárhely", "from_email": "ertesites@tarhely.gov.hu", "date": "2026-11-21", "snippet": "küldemény"}
KERES = {"id": "L2", "subject": "M+ projekt - GitHub", "from": "Attila", "from_email": "udvattila99@gmail.com",
         "date": "2026-11-19", "snippet": "Kérlek küldjétek el a Psono felhasználó neveteket"}
PROMO = {"id": "L3", "subject": "Black Friday!", "from": "Bolt", "from_email": "newsletter@bolt.hu",
         "date": "2026-11-22", "snippet": "akció"}
GEPI_HATARIDO = {"id": "L4", "subject": "Fizetési felszólítás", "from": "MVM", "date": "2026-11-19",
                 "from_email": "ugyfelszolgalat@online.mvmnext.hu", "snippet": "a fizetési határidő lejárt"}
ESEMENY = {"id": "E1", "summary": "Tárgyalás a Lechnernél", "start": "2026-11-23T10:00:00+01:00",
           "end": "2026-11-23T11:00:00+01:00", "allday": False, "attendees": ["ugyintezo@lechner.hu"],
           "location": "Budapest"}
SZULINAP = {"id": "E2", "summary": "Réka születésnapja", "start": "2026-11-23", "end": "2026-11-24", "allday": True}


class Forras:
    """A BridgeForras szerződése, kitalált adattal; minden hívást feljegyez."""

    def __init__(self, *, levelek=(), naptar=(), uzenetek=(), feladatok=()):
        self._levelek, self._naptar = list(levelek), list(naptar)
        self._uzenetek, self._feladatok = list(uzenetek), list(feladatok)
        self.hivasok = []

    async def uzenetek(self, ota):
        return self._uzenetek

    async def feladatok(self):
        return self._feladatok

    async def teendok(self):
        return []

    async def naptar(self, tol, ig):
        return self._naptar

    async def levelek(self, query, limit):
        return self._levelek

    async def level(self, mid):
        return {"id": mid, "threadId": "T" + mid, "body": f"a(z) {mid} teljes szövege"}

    async def tervezet(self, **kw):
        self.hivasok.append(("tervezet", kw))
        return {"draft_id": "d1", "to": kw.get("to") or "attila@x.hu", "subject": kw.get("subject") or "Re: x",
                "link": "https://mail.google.com/mail/u/0/#drafts?compose=m1", "body": kw["body"]}

    def tudasbazis_elerheto(self):
        return True

    async def kulso_definiciok(self):
        return [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object"}}}
                for n in ("lexonomics", "medical", "makronom", "echolot", "engine", "studio")]

    async def feldwebel_eszkoz(self, nev, args, modell):
        self.hivasok.append(("feldwebel", nev))
        return json.dumps({"forras": nev, "valasz": "találat"})

    async def studio(self, eszkoz, parameterek, modell):
        self.hivasok.append(("studio", eszkoz, parameterek.get("action")))
        if eszkoz == "files":
            return json.dumps({"url": "https://studio/upload-link/TITKOS-EGYSZERI", "method": "PUT"})
        return json.dumps({"file_id": "f2.docx", "name": "Háttér.docx", "edit_url": "https://studio/app/file/f2/edit",
                           "check": {"status": "ok"}})

    async def feltolt(self, url, tartalom):
        self.hivasok.append(("feltolt", len(tartalom)))
        return {"file_id": "f1.md"}


def _valasz(szoveg="", hivasok=None, usd=0.10, modell="claude-sonnet-5-5"):
    msg = {"role": "assistant", "content": szoveg}
    if hivasok:
        msg["tool_calls"] = [{"id": f"t{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                             for i, (n, a) in enumerate(hivasok)]
    return {"model": modell, "koltes_usd": usd,
            "choices": [{"message": msg, "finish_reason": "tool_calls" if hivasok else "stop"}]}


class Kapu:
    """Hamis `llm_kapu.chat`: sorban adja a válaszokat, feljegyzi a célt és a payloadot."""

    def __init__(self, valaszok):
        self.valaszok, self.hivasok = list(valaszok), []

    async def __call__(self, payload, *, purpose=""):
        self.hivasok.append((purpose, json.loads(json.dumps(payload, default=str))))
        v = self.valaszok.pop(0)
        if isinstance(v, dict) and "code" in v:
            return None, v, []
        return v, None, []


TERV = {"tetelek": [{"jel_id": "L:L1", "tipus": "hatteranyag", "cim": "Háttér: a Lechner-határozat",
                     "miert": "hivatalos levél", "mit": "összefoglaló, határidők, forrással"}],
        "panaszok": [], "megjegyzes": "A gázszámlát neked kell befizetned."}


def _munka(*, usd=0.10):
    """Egy tétel munkája: Studio-dokumentum, aztán a záró JSON."""
    return [_valasz(hivasok=[("studio_dokumentum", {"cim": "Háttér", "markdown": "# Háttér\n\nszöveg"})], usd=usd),
            _valasz(json.dumps({"allapot": "kesz", "osszegzes": "Elkészült a háttéranyag."}), usd=usd)]


# ── 1. ENGEDÉLYLISTA ────────────────────────────────────────────────────

@pytest.mark.parametrize("nev", sorted(N.TILTOTT) + ["valami_uj_eszkoz", "capture_send_email", "create_draft"])
def test_tiltott_eszkoz_elutasitva(nev):
    assert N.engedett(nev, {"eszkoz": "x"}) is not None


@pytest.mark.parametrize("nev", sorted(N.ENGEDETT))
def test_engedett_eszkoz(nev):
    assert N.engedett(nev, {"eszkoz": "x"}) is None


def test_a_kifelé_menő_utak_mind_tiltottak():
    for nev in ("send_email", "send_message", "calendar_create", "agora", "story_comments", "ai_query", "ai_task",
                "execute_recipe", "schedule_recipe", "create_task", "send_file_email", "export_task", "echolot",
                "engine", "chatgpt", "gemini", "gmail_poll", "calendar_poll", "draft_reply"):
        assert nev in N.TILTOTT and nev not in N.ENGEDETT


@pytest.mark.parametrize("eszkoz,param,jo", [
    ("files", {"action": "upload_link"}, True), ("files", {"action": "new"}, True),
    ("convert", {"target_format": "docx"}, True), ("wordcraft", {"read": "text"}, True),
    ("files", {"action": "share", "email": "x@y.hu"}, False), ("files", {"action": "list"}, False),
    ("files", {}, False), ("edit_with_instruction", {}, False), ("facts", {}, False),
    ("describe_image", {}, False), ("generate_image", {}, False), ("design", {}, False), ("video", {}, False),
    ("ocr_image", {}, False), ("fill_assistant", {}, False),
])
def test_studio_csak_determinisztikus(eszkoz, param, jo):
    assert (N.studio_engedett(eszkoz, param) is None) == jo


def test_a_modell_csak_az_engedett_eszkozoket_kapja():
    defs = _run(N.eszkoz_definiciok(Forras()))
    nevek = {d["function"]["name"] for d in defs}
    assert nevek <= N.ENGEDETT
    assert {"gmail_search", "read_gmail_attachment", "tudasbazis", "lexonomics", "medical", "makronom", "naptar",
            "gmail_level", "gmail_tervezet", "studio_dokumentum"} == nevek
    assert not nevek & {"echolot", "engine", "studio", "send_email", "calendar_create", "ai_query"}


def test_a_vegrehajto_a_tiltottat_a_forrasig_sem_engedi():
    f, t = Forras(), N.Termes()
    v = N.vegrehajto(f, t)
    for nev, args in (("send_email", {"to": "x@y.hu", "subject": "s", "body": "b"}), ("ai_query", {"prompt": "x"}),
                      ("calendar_create", {"summary": "x"}), ("studio", {"eszkoz": "edit_with_instruction"}),
                      ("agora", {"action": "comment", "body": "x"}), ("send_message", {"recipient": "kimi"})):
        assert "error" in json.loads(_run(v(nev, args, "claude-sonnet-5-5")))
    assert f.hivasok == [] and len(t.elutasitva) == 6


def test_a_studio_ai_lepese_a_dokumentumuton_sem_megy(monkeypatch):
    monkeypatch.setattr(N, "STUDIO_ENGEDETT", {"convert": None})          # pl. a files kiesik a listáról
    r = _run(N.studio_dokumentum(Forras(), "x", "# x"))
    assert "error" in r and "nincs az engedélylistán" in r["error"]


def test_a_feldwebel_vegrehajtoja_vedett_modban_fut(monkeypatch):
    """A BridgeForras a Feldwebel végrehajtóját `vedett=True`-val hívja (SF-felé menő eszköz ott sem futna), a Studiót
    `vedett=False`-szal, de csak a `studio_engedett` után."""
    from feldwebel import responder
    hivasok = []

    async def _exec(nev, args, ctx, *, modell="", vedett=False):
        hivasok.append((nev, vedett))
        return "{}"
    monkeypatch.setattr(responder, "_execute_tool", _exec)
    bf = N.BridgeForras(ctx=None, get_db=None)
    _run(bf.feldwebel_eszkoz("gmail_search", {"query": "x"}, "claude-sonnet-5-5"))
    _run(bf.studio("convert", {"file_id": "x"}, "claude-sonnet-5-5"))
    assert hivasok == [("gmail_search", True), ("studio", False)]


# ── 2. KERET ────────────────────────────────────────────────────────────

def test_keret_env(monkeypatch):
    monkeypatch.delenv("NACHTSCHICHT_MAX_USD", raising=False)
    assert N.keret_usd() == 2.0
    monkeypatch.setenv("NACHTSCHICHT_MAX_USD", "0.5")
    assert N.keret_usd() == 0.5
    monkeypatch.setenv("NACHTSCHICHT_MAX_USD", "sok")
    assert N.keret_usd() == 2.0


def test_a_keret_a_kapu_konyvelesebol_szamol_es_elotte_megall():
    sz = N.Szamla(keret=1.0)
    kapu = Kapu([_valasz("a", usd=0.6), _valasz("b", usd=0.6)])
    p = {"messages": [{"role": "user", "content": "x"}]}
    _run(sz.hiv(kapu, p, N.MUNKA_CEL))
    assert sz.usd == pytest.approx(0.6) and sz.hivasok == 1
    sz.keret = 0.62                                # 0,6 elköltve + a következő hívás becslése (~0,06) > 0,62
    with pytest.raises(N.KeretElfogyott):
        _run(sz.hiv(kapu, p, N.MUNKA_CEL))
    assert sz.elfogyott and sz.hivasok == 1        # a túllépő hívás el sem ment


def test_a_koltes_a_usagebol_ha_a_kapu_nem_adja():
    d = {"model": "claude-sonnet-5-5", "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 100_000,
                                                 "cache_read_tokens": 0, "cache_write_tokens": 0}}
    assert N._usd(d) == pytest.approx(2.0 + 1.0)


def test_a_keret_elfogyasa_az_ejszakaban_kimondva(get_db, kulcs, monkeypatch):
    monkeypatch.setenv("NACHTSCHICHT_MAX_USD", "0.30")
    kapu = Kapu([_valasz(json.dumps(TERV), usd=0.25, modell="claude-opus-5-5")] + _munka(usd=0.25))
    r = _run(N.ejszaka(forras=Forras(levelek=[HIVATALOS]), get_db=get_db, memoria_ir=memoria_ir, chat=kapu, most=MOST))
    assert r["eredmenyek"][0]["allapot"] == "kimaradt" and "keret" in r["eredmenyek"][0]["hiba"]
    assert "elfogyott" in r["jelentes"] and "$0.25 / $0.30" in r["jelentes"]
    assert len(kapu.hivasok) == 1


def test_a_kapu_adja_a_koltest(monkeypatch, kulcs):
    """Az `llm_kapu.chat` a válaszon kimondja a könyvelt költséget (`koltes_usd`) — a keret ebből számol."""
    from anthropic.types.beta import BetaMessage
    msg = BetaMessage.model_validate({
        "id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}})

    async def _kuld(kw):
        assert kw["model"] == "claude-sonnet-5-5"
        return msg
    monkeypatch.setattr(llm_kapu, "_kuld", _kuld)
    d, e, _ = _run(llm_kapu.chat({"messages": [{"role": "user", "content": "x"}]}, purpose=N.MUNKA_CEL))
    assert e is None and d["koltes_usd"] == pytest.approx(2.0)


def test_a_szintek():
    p = {"messages": [{"role": "user", "content": "x"}]}
    assert llm_kapu.szint_valaszt(p, N.TERV_CEL)[0] == "okos"          # a rövid tervező lépés: Opus
    assert llm_kapu.szint_valaszt(p, N.MUNKA_CEL)[0] == "kozep"         # a munka: Sonnet
    assert llm_kapu.szint_valaszt(dict(p, tools=[{}]), N.MUNKA_CEL)[1] == "nachtschicht"


# ── 3. NINCS SILICONFLOW-TARTALÉK ───────────────────────────────────────

def test_kulcs_nelkul_kimarad_es_kimondja(get_db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import server

    async def _sf(*a, **k):
        raise AssertionError("a Nachtschicht nem hívhatja a SiliconFlow-tölcsért")
    monkeypatch.setattr(server, "sf_chat", _sf)
    tg = []

    async def _tg(s):
        tg.append(s)
    r = _run(N.ejszaka(forras=Forras(levelek=[HIVATALOS]), get_db=get_db, memoria_ir=memoria_ir, telegram=_tg,
                       most=MOST))
    assert "kimaradtam" in r["kimaradt"] and "SiliconFlow-ra nem váltok" in r["kimaradt"]
    assert len(tg) == 1 and N.FEJLEC in tg[0] and "kimaradtam" in tg[0]


def test_az_alapertelmezett_motor_a_kapu_es_nem_az_sf_chat(get_db, kulcs, monkeypatch):
    """`chat` nélkül a Nachtschicht az `llm_kapu.chat`-et hívja; kapuhibánál sem vált SiliconFlow-ra."""
    import server

    async def _sf(*a, **k):
        raise AssertionError("a Nachtschicht nem hívhatja a SiliconFlow-tölcsért")
    monkeypatch.setattr(server, "sf_chat", _sf)
    kapu = Kapu([{"code": "server_error", "message": "Anthropic 529"}])
    monkeypatch.setattr(llm_kapu, "chat", kapu)
    r = _run(N.ejszaka(forras=Forras(levelek=[HIVATALOS]), get_db=get_db, memoria_ir=memoria_ir, most=MOST))
    assert len(kapu.hivasok) == 1 and kapu.hivasok[0][0] == N.TERV_CEL
    assert "server_error" in r["kimaradt"] and "SiliconFlow-ra nem váltok" in r["kimaradt"]


def test_a_forrasban_nincs_siliconflow_ut():
    src = inspect.getsource(N)
    assert "sf_chat" not in src.replace("`sf_chat`", "") and "siliconflow_api_key" not in src


def test_vegzetes_kapuhibanal_a_tobbi_tetel_is_kimarad(get_db, kulcs):
    terv = dict(TERV, tetelek=TERV["tetelek"] + [{"jel_id": "L:L2", "tipus": "levelterv", "cim": "Válasz Attilának",
                                                  "miert": "kérés", "mit": "tervezet"}])
    kapu = Kapu([_valasz(json.dumps(terv)), {"code": "havi_keret", "message": "elfogyott"}])
    r = _run(N.ejszaka(forras=Forras(levelek=[HIVATALOS, KERES]), get_db=get_db, memoria_ir=memoria_ir, chat=kapu,
                       most=MOST))
    assert [e["allapot"] for e in r["eredmenyek"]] == ["nem_sikerult", "kimaradt"]
    assert len(kapu.hivasok) == 2


# ── 4. JELÖLÉS / ADATVÉDELEM ────────────────────────────────────────────

def test_a_naplo_sorai_jelolve_es_olvasottak(get_db, kulcs):
    kapu = Kapu([_valasz(json.dumps(TERV), modell="claude-opus-5-5")] + _munka())
    r = _run(N.ejszaka(forras=Forras(levelek=[HIVATALOS]), get_db=get_db, memoria_ir=memoria_ir, chat=kapu, most=MOST))
    c = get_db()
    sorok = [dict(x) for x in c.execute("SELECT * FROM messages ORDER BY id")]
    kizarva = c.execute(f"SELECT COUNT(*) FROM messages WHERE {futar_kliens.SF_KIZARAS_SQL}").fetchone()[0]
    c.close()
    assert len(sorok) == 2 and kizarva == 0
    assert all(s["sender"] == s["recipient"] == "nachtschicht" and s["status"] == "read" for s in sorok)
    tetel = json.loads(sorok[0]["message"])
    assert tetel["allapot"] == "kesz" and tetel["link"] == "https://studio/app/file/f2/edit"
    assert tetel["jel_id"] == "L:L1" and tetel["tipus"] == "hatteranyag" and tetel["datum"] == "2026-11-23"
    assert json.loads(sorok[1]["message"])["jelentes"] == r["jelentes"]


def test_a_naplo_nem_kerul_sf_kontextusba():
    """A SiliconFlow-ügynökök kontextusépítője (build_agent_context + _get_inbox_summary) a Nachtschicht sorait sem
    veszi fel — akkor sem, ha valaki capture-daemonnak álcázná."""
    import server
    from pyramid.context_builder import build_agent_context
    jel = "NACHTJEL5521"
    for felado, cimzett in (("nachtschicht", "nachtschicht"), ("capture-daemon", "nachtschicht")):
        N.naplo(server.get_db, f"nachtschicht:tetel {jel}", {"tetel": jel, "felado": felado})
        c = server.get_db()
        c.execute("UPDATE messages SET sender = ?, recipient = ? WHERE id = (SELECT MAX(id) FROM messages)",
                  (felado, cimzett))
        c.commit()
        c.close()
    inbox = server._get_inbox_summary()
    assert jel not in inbox
    for ugynok in ("kimi", "deepseek", "glm5"):
        assert jel not in build_agent_context(agent_id=ugynok, inbox_summary=inbox, relevance_query=jel,
                                              cross_agent_rag=True)


def test_a_jelentes_csak_claude_jellel_megy_az_elozmenybe(get_db, kulcs):
    kapu = Kapu([_valasz(json.dumps(TERV))] + _munka())
    tg, elozmeny = [], []

    async def _tg(s):
        tg.append(s)
    _run(N.ejszaka(forras=Forras(levelek=[HIVATALOS]), get_db=get_db, memoria_ir=memoria_ir, chat=kapu, telegram=_tg,
                   elozmeny=elozmeny.append, most=MOST))
    assert len(tg) == 1 and len(elozmeny) == 1 and "<b>" not in elozmeny[0]
    # a server így hívja: add_message(…, agent_id = "nachtschicht" + CLAUDE_CSAK_JEL)
    assert futar_kliens.vedett_elozmeny(N.FELADO + futar_kliens.CLAUDE_CSAK_JEL)
    import server
    assert "nachtschicht.FELADO + futar_kliens.CLAUDE_CSAK_JEL" in inspect.getsource(server._nachtschicht_ejszaka)


def test_a_shared_memoryba_csak_datum_es_hash_kerul(get_db, kulcs):
    terv = dict(TERV, panaszok=[{"uzenet_id": 7, "kulcs": "titkos-tema", "repo": "bridge", "kivansag": "TITOKTARTALOM"}])
    kapu = Kapu([_valasz(json.dumps(terv))] + _munka())
    fk = []
    _run(N.ejszaka(forras=Forras(levelek=[HIVATALOS], uzenetek=[
        {"id": 7, "sender": "kommandant", "recipient": "bridge", "timestamp": "2026-11-22T20:00", "message": "JAJBAZMEG"}]),
        get_db=get_db, memoria_ir=memoria_ir, chat=kapu, faber_kerelem=lambda k, r: fk.append(k) or 99, most=MOST))
    c = get_db()
    ertekek = " ".join(r[0] + r[1] for r in c.execute("SELECT key, value FROM shared_memory"))
    c.close()
    assert len(fk) == 1 and "TITOKTARTALOM" not in ertekek and "titkos-tema" not in ertekek


# ── 5. ÜTEMEZÉS ─────────────────────────────────────────────────────────

def _bp(h, m, nap=23):
    return datetime(2026, 11, nap, h, m, tzinfo=N.BP)


def test_utemezes_naponta_egyszer_az_ablakban(get_db, monkeypatch):
    monkeypatch.delenv("NACHTSCHICHT", raising=False)
    indult = []

    async def _futtat():
        indult.append(1)

    async def _menet():
        ki = [N.cron_lepes(t, get_db=get_db, memoria_ir=memoria_ir, futtat=_futtat)
              for t in (_bp(5, 49), _bp(5, 50), _bp(5, 51), _bp(6, 10))]
        ki.append(N.cron_lepes(_bp(5, 55, 24), get_db=get_db, memoria_ir=memoria_ir, futtat=_futtat))
        ki.append(N.cron_lepes(_bp(6, 30, 25), get_db=get_db, memoria_ir=memoria_ir, futtat=_futtat))
        await asyncio.sleep(0)
        return ki
    assert _run(_menet()) == [False, True, False, False, True, False]
    assert len(indult) == 2


def test_utemezes_ujrainditas_utan_sem_ismetel(get_db):
    async def _futtat():
        pass

    async def _menet():
        a = N.cron_lepes(_bp(5, 50), get_db=get_db, memoria_ir=memoria_ir, futtat=_futtat)
        N._FUTO.clear()                                   # „újraindulás”: a memóriában semmi
        b = N.cron_lepes(_bp(5, 52), get_db=get_db, memoria_ir=memoria_ir, futtat=_futtat)
        await asyncio.sleep(0)
        return a, b
    assert _run(_menet()) == (True, False)


def test_utemezes_kikapcsolhato(get_db, monkeypatch):
    monkeypatch.setenv("NACHTSCHICHT", "0")

    async def _futtat():
        raise AssertionError("nem indulhat")

    async def _menet():
        return N.cron_lepes(_bp(5, 50), get_db=get_db, memoria_ir=memoria_ir, futtat=_futtat)
    assert _run(_menet()) is False


def test_a_cron_szal_egy_hivassal_kot_be():
    import server
    src = inspect.getsource(server._cron_loop)
    assert src.count("nachtschicht.cron_lepes(") == 1 and "futtat=_nachtschicht_ejszaka" in src


# ── 6. ANYAGGYŰJTÉS ─────────────────────────────────────────────────────

def test_level_jelek():
    assert N.level_jel(HIVATALOS).erosseg == "eros" and "hivatalos" in N.level_jel(HIVATALOS).ok
    assert N.level_jel(KERES).erosseg == "eros" and "kéréssel" in N.level_jel(KERES).ok
    assert N.level_jel(PROMO) is None
    assert N.level_jel(GEPI_HATARIDO).erosseg == "kozepes"
    assert N.level_jel({"id": "x", "from_email": "barat@gmail.com", "subject": "beadási határidő péntek"}).erosseg == "eros"
    assert N.level_jel({"id": "y", "from_email": "barat@gmail.com", "subject": "??? kezdőknek"}) is None


def test_naptar_es_feladat_jelek():
    assert N.naptar_jel(ESEMENY).erosseg == "eros" and "résztvevőkkel" in N.naptar_jel(ESEMENY).ok
    assert N.naptar_jel(SZULINAP) is None
    assert N.naptar_jel({"id": "z", "summary": "Szabadság", "allday": True}).erosseg == "kozepes"
    f = {"id": 5, "title": "Beadvány", "deadline": (MOST + timedelta(days=1)).isoformat()}
    assert N.feladat_jel(f, MOST).erosseg == "eros"
    assert N.feladat_jel(dict(f, deadline=(MOST + timedelta(days=9)).isoformat()), MOST) is None
    assert N.feladat_jel(dict(f, deadline=None), MOST) is None


def test_gyujtes_jelek_panaszok_es_torzs(get_db):
    uz = [{"id": 1, "sender": "kommandant", "recipient": "bridge", "timestamp": "2026-11-22T10:00", "message": "Szia"},
          {"id": 2, "sender": "feldwebel-deepseek", "recipient": "kommandant", "timestamp": "2026-11-22T10:01",
           "message": "A Kimi szerint…"},
          {"id": 3, "sender": "kommandant", "recipient": "bridge", "timestamp": "2026-11-22T10:02",
           "message": "JAJBAZMEG, megint keveredik a Kimi és a Feldwebel"},
          {"id": 4, "sender": "kommandant", "recipient": "bridge", "timestamp": "2026-11-22T11:00",
           "message": "holnapra kell a beadvány a Lechnernek"}]
    a = _run(N.gyujt(Forras(levelek=[HIVATALOS, KERES, PROMO, GEPI_HATARIDO], naptar=[ESEMENY, SZULINAP],
                            uzenetek=uz), MOST, get_db))
    assert {j.id for j in a.eros()} == {"L:L1", "L:L2", "N:E1", "U:4"}
    assert [p["uzenet_id"] for p in a.panaszok] == [3] and "Kimi szerint" in a.panaszok[0]["elozo_valasz"]
    l1 = next(l for l in a.levelek if l["id"] == "L1")
    assert l1["torzs"] == "a(z) L1 teljes szövege" and l1["threadId"] == "TL1"
    szoveg = N.anyag_szoveg(a)
    assert "[L:L1]" in szoveg and "PANASZ-JELÖLTEK" in szoveg and "Black Friday" in szoveg


def test_eros_jel_nelkul_nincs_llm_hivas_es_egy_mondat(get_db, kulcs):
    kapu = Kapu([])
    tg = []

    async def _tg(s):
        tg.append(s)
    r = _run(N.ejszaka(forras=Forras(levelek=[PROMO, GEPI_HATARIDO]), get_db=get_db, memoria_ir=memoria_ir,
                       chat=kapu, telegram=_tg, most=MOST))
    assert kapu.hivasok == [] and r["koltes_usd"] == 0
    assert N.URES_JELENTES in tg[0] and tg[0].startswith(N.FEJLEC)


def test_egy_forras_kiesese_nem_allitja_meg(get_db):
    class Rossz(Forras):
        async def naptar(self, tol, ig):
            raise RuntimeError("a Google-szolgáltatás nem él")
    a = _run(N.gyujt(Rossz(levelek=[HIVATALOS]), MOST, get_db))
    assert any("naptár" in h for h in a.hianyok) and {j.id for j in a.eros()} == {"L:L1"}


# ── 7. TERV, PANASZ, TERVEZET, STUDIO, JELENTÉS ─────────────────────────

def test_terv_szures(get_db):
    a = _run(N.gyujt(Forras(levelek=[HIVATALOS, KERES, GEPI_HATARIDO], naptar=[ESEMENY]), MOST, get_db))
    a.korabbi = [{"jel_id": "N:E1", "allapot": "kesz"}]
    terv = {"tetelek": [
        {"jel_id": "L:L4", "tipus": "dokumentum", "cim": "közepes jelre"},       # nem erős → ki
        {"jel_id": "L:KITALALT", "tipus": "dokumentum", "cim": "kitalált jel"},  # nincs ilyen → ki
        {"jel_id": "N:E1", "tipus": "hatteranyag", "cim": "már kész"},          # tegnap elkészült → ki
        {"jel_id": "L:L1", "tipus": "fizetes", "cim": "ismeretlen típus"},      # → ki
        {"jel_id": "L:L1", "tipus": "hatteranyag", "cim": "jó"},
        {"jel_id": "L:L1", "tipus": "dokumentum", "cim": "ugyanarra még egy"},  # duplikált jel → ki
        {"jel_id": "L:L2", "tipus": "levelterv", "cim": "jó 2"}],
        "panaszok": [{"uzenet_id": 999, "kivansag": "kitalált panasz"}]}
    tetelek, panaszok = N.terv_szur(terv, a, max_tetel=3)
    assert [t["cim"] for t in tetelek] == ["jó", "jó 2"] and panaszok == []
    assert len(N.terv_szur(terv, a, max_tetel=1)[0]) == 1


def test_panasz_legfeljebb_ketto_es_24_ora(get_db):
    a = N.Anyag(most=MOST, panaszok=[{"uzenet_id": i, "szoveg": f"nem jó {i}", "ido": "t"} for i in (1, 2, 3)])
    p = [{"uzenet_id": i, "kulcs": f"tema-{i}", "repo": "bridge", "kivansag": f"javítsd {i}"} for i in (1, 2, 3)]
    kuldott = []

    def _fk(kivansag, repo):
        kuldott.append((kivansag, repo))
        return len(kuldott)
    ki = N.panaszok_kezel(p, a, get_db=get_db, memoria_ir=memoria_ir, faber_kerelem=_fk, proba=False, most=1000.0)
    assert [x["allapot"] for x in ki] == ["fabernek_atadva", "fabernek_atadva"] and len(kuldott) == 2
    assert "nem jó 1" in kuldott[0][0] and "javítsd 1" in kuldott[0][0] and kuldott[0][1] == "bridge"
    ki = N.panaszok_kezel(p[:1], a, get_db=get_db, memoria_ir=memoria_ir, faber_kerelem=_fk, proba=False,
                          most=1000.0 + 3600)
    assert ki[0]["allapot"] == "kihagyva_24h" and len(kuldott) == 2
    ki = N.panaszok_kezel(p[:1], a, get_db=get_db, memoria_ir=memoria_ir, faber_kerelem=_fk, proba=False,
                          most=1000.0 + N.PANASZ_TILTAS_S + 1)
    assert ki[0]["allapot"] == "fabernek_atadva" and len(kuldott) == 3


def test_panasz_probafutasban_nem_megy_ki(get_db):
    a = N.Anyag(most=MOST, panaszok=[{"uzenet_id": 1, "szoveg": "keveredik", "ido": "t"}])

    def _fk(*a):
        raise AssertionError("próbafutásban nem kérhet")
    ki = N.panaszok_kezel([{"uzenet_id": 1, "kulcs": "k", "repo": "bridge", "kivansag": "x"}], a, get_db=get_db,
                          memoria_ir=memoria_ir, faber_kerelem=_fk, proba=True, most=1.0)
    assert ki[0]["allapot"] == "probafutas_nem_kuldve" and "keveredik" in ki[0]["kivansag_teljes"]


class _GmailHamis:
    """A Google-ügyfél láncolt hívásai; a `send`-et hívni tilos."""

    def __init__(self):
        self.letrehozott = []

    def users(self):
        return self

    def messages(self):
        return self

    def drafts(self):
        return self

    def get(self, **kw):
        return _Exe({"threadId": "T9", "payload": {"headers": [
            {"name": "From", "value": "Attila <udvattila99@gmail.com>"}, {"name": "Subject", "value": "M+ projekt"},
            {"name": "Message-ID", "value": "<abc@mail>"}]}})

    def create(self, userId, body):
        self.letrehozott.append(body)
        return _Exe({"id": "r-1", "message": {"id": "m-1"}})

    def send(self, *a, **k):
        raise AssertionError("a Nachtschicht nem küldhet levelet")


class _Exe:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v


def test_gmail_tervezet_valasz_a_szalban_kuldes_nelkul():
    import base64
    from types import SimpleNamespace
    g = _GmailHamis()
    bf = N.BridgeForras(SimpleNamespace(capture_state={"gmail_service": g}), None)
    r = _run(bf.tervezet(message_id="m0", to="", subject="", body="Szia Attila!\n\nKöszönöm.\n\nTamás"))
    assert r["draft_id"] == "r-1" and r["to"] == "udvattila99@gmail.com" and r["subject"] == "Re: M+ projekt"
    assert r["link"].endswith("#drafts?compose=m-1")
    uz = g.letrehozott[0]["message"]
    assert uz["threadId"] == "T9"
    nyers = base64.urlsafe_b64decode(uz["raw"]).decode()
    assert "In-Reply-To: <abc@mail>" in nyers and "To: udvattila99@gmail.com" in nyers


def test_a_kodban_nincs_kuldo_hivas():
    src = inspect.getsource(N)
    assert ".send(" not in src and "messages().send" not in src and "_send_email_func" not in src


def test_studio_dokumentum_determinisztikus_es_a_feltolto_link_titok():
    f = Forras()
    r = _run(N.studio_dokumentum(f, "Háttér: Lechner/határozat", "# Háttér\n\nszöveg"))
    assert r["edit_url"] == "https://studio/app/file/f2/edit" and r["check"] == "ok"
    assert [h[:2] for h in f.hivasok] == [("studio", "files"), ("feltolt", len("# Háttér\n\nszöveg".encode())),
                                          ("studio", "convert")]
    assert "TITKOS-EGYSZERI" not in json.dumps(r)
    assert "error" in _run(N.studio_dokumentum(f, "x", "   "))


def test_teljes_ejszaka_a_jelentes_formajaval(get_db, kulcs):
    terv = dict(TERV, tetelek=TERV["tetelek"] + [{"jel_id": "L:L2", "tipus": "levelterv", "cim": "Válasz Attilának",
                                                  "miert": "kérés", "mit": "tervezet"}])
    kapu = Kapu([_valasz(json.dumps(terv), modell="claude-opus-5-5")] + _munka() + [
        _valasz(hivasok=[("gmail_search", {"query": "from:me to:udvattila99@gmail.com"}),
                         ("send_email", {"to": "udvattila99@gmail.com", "subject": "x", "body": "y"})]),
        _valasz(hivasok=[("gmail_tervezet", {"message_id": "L2", "body": "Szia Attila! … Tamás"})]),
        _valasz(json.dumps({"allapot": "kesz", "osszegzes": "A válasz a Piszkozatokban."}))])
    f = Forras(levelek=[HIVATALOS, KERES])
    tg = []

    async def _tg(s):
        tg.append(s)
    r = _run(N.ejszaka(forras=f, get_db=get_db, memoria_ir=memoria_ir, chat=kapu, telegram=_tg, most=MOST))
    assert [c for c, _ in kapu.hivasok] == [N.TERV_CEL] + [N.MUNKA_CEL] * 5
    assert [e["allapot"] for e in r["eredmenyek"]] == ["kesz", "kesz"]
    assert r["eredmenyek"][1]["elutasitva"] == ["send_email"]
    assert ("feldwebel", "gmail_search") in f.hivasok and not any("send" in str(h) for h in f.hivasok)
    j = tg[0]
    assert j.startswith(N.FEJLEC) and "Ezt fogod kérni — megcsináltam" in j
    assert '<a href="https://studio/app/file/f2/edit">' in j and "#drafts?compose=m1" in j
    assert "nem küldtem el" in j and "gázszámlát" in j and "$0.60 / $2.00" in j and "PRÓBAFUTÁS" not in j
    # a munka eszközkészlete csak engedett eszköz
    nevek = {t["function"]["name"] for t in kapu.hivasok[1][1]["tools"]}
    assert nevek <= N.ENGEDETT
    # a tervező csak a tudásbázist kapta
    assert [t["function"]["name"] for t in kapu.hivasok[0][1]["tools"]] == ["tudasbazis"]


def test_probafutas_nem_kuld_telegramot_es_jelolt(get_db, kulcs):
    kapu = Kapu([_valasz(json.dumps(TERV))] + _munka())

    async def _tg(s):
        raise AssertionError("próbafutásban nincs Telegram")
    r = _run(N.ejszaka(forras=Forras(levelek=[HIVATALOS]), get_db=get_db, memoria_ir=memoria_ir, chat=kapu,
                       telegram=_tg, proba=True, most=MOST))
    assert "PRÓBAFUTÁS" in r["jelentes"]
    # a próbafutás tétele nem számít „már elkészültnek” a következő éjszakán
    assert N.korabbi_tetelek(get_db, MOST) == []


def test_json_kivesz():
    assert N.json_kivesz('Itt a terv:\n```json\n{"tetelek": [], "a": {"b": 1}}\n```') == {"tetelek": [], "a": {"b": 1}}
    assert N.json_kivesz("nincs benne") is None
    assert N.json_kivesz('{rossz} aztán {"jo": true}') == {"jo": True}


def test_az_idokeret_utan_a_maradek_kimarad_es_a_jelentes_megy(get_db, kulcs):
    terv = dict(TERV, tetelek=TERV["tetelek"] + [{"jel_id": "L:L2", "tipus": "levelterv", "cim": "Válasz Attilának",
                                                  "miert": "kérés", "mit": "tervezet"}])
    kapu = Kapu([_valasz(json.dumps(terv))])
    tg = []

    async def _tg(s):
        tg.append(s)
    r = _run(N.ejszaka(forras=Forras(levelek=[HIVATALOS, KERES]), get_db=get_db, memoria_ir=memoria_ir, chat=kapu,
                       telegram=_tg, most=MOST, idokeret_s=30))
    assert [e["allapot"] for e in r["eredmenyek"]] == ["kimaradt", "kimaradt"] and len(kapu.hivasok) == 1
    assert "időkeret" in tg[0]


def test_eszkozonkenti_hivasi_plafon():
    f, term = Forras(), N.Termes()
    v = N.vegrehajto(f, term)
    for _ in range(N.MAX_HIVAS_ESZKOZONKENT):
        assert "error" not in json.loads(_run(v("lexonomics", {"eszkoz": "kereses"}, "claude-sonnet-5-5")))
    r = json.loads(_run(v("lexonomics", {"eszkoz": "kereses"}, "claude-sonnet-5-5")))
    assert "hívási kerete" in r["error"] and term.elutasitva == ["lexonomics"]
    assert sum(1 for h in f.hivasok if h == ("feldwebel", "lexonomics")) == N.MAX_HIVAS_ESZKOZONKENT
    assert "error" not in json.loads(_run(v("makronom", {"eszkoz": "x"}, "claude-sonnet-5-5")))   # a másik eszköz megy


def test_rovid_osszegzes_link_nelkul_mondathataron():
    s = ("A munkaanyag elkészült a Studióban: https://studio/app/file/x/edit. Határidő: 12-21. "
         + "Hosszú magyarázat következik, sok szóval. " * 20)
    r = N.rovid_osszegzes(s)
    assert "http" not in r and r.startswith("A munkaanyag elkészült a Studióban:") and r.endswith(".")
    assert len(r) <= 420 and "Határidő: 12-21." in r
    # a rövidítés és a számozás nem mondatvég (próbafutás, 11-22: „… igazolt, hogy az Inytv. 54.” — itt vágta el)
    s2 = ("Elkészült a háttéranyag a Lechner-határozatról, a kézbesítésről és a jogorvoslatról, forrásokkal és "
          "ellenőrzőlistával. A Tárhely 2026. 12. 21-én törli. Az Inytv. 54. § szerint per van. "
          + "Biztosan annyi igazolt, hogy az Inytv. 54. § (1) bekezdése egy éves határt szab, és " + "x " * 200)
    r2 = N.rovid_osszegzes(s2)
    assert r2.endswith("Az Inytv. 54. § szerint per van.")


def test_a_kulso_listazas_csak_az_engedett_szervereket_szolitja(monkeypatch):
    import kulso_mcp
    kert = []

    async def _lista(nev, friss=False):
        kert.append(nev)
        return [], ""
    monkeypatch.setattr(kulso_mcp, "_lista", _lista)
    monkeypatch.setattr(kulso_mcp, "bekotott", lambda n: True)
    _run(N.BridgeForras(None, None).kulso_definiciok())
    assert sorted(kert) == sorted(N.KULSO_ENGEDETT)


def test_a_feltolto_link_a_hibauzenetbe_sem_kerul(monkeypatch):
    import urllib.request

    def _hiba(req, timeout=0):
        assert req.get_method() == "PUT" and req.data == b"# x"
        raise OSError(f"kapcsolódási hiba: {req.full_url}")
    monkeypatch.setattr(urllib.request, "urlopen", _hiba)
    with pytest.raises(RuntimeError) as e:
        _run(N.BridgeForras(None, None).feltolt("https://studio/upload-link/TITKOS-EGYSZERI", b"# x"))
    assert "TITKOS" not in str(e.value) and e.value.__cause__ is None
