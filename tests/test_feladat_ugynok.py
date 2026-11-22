"""FELADAT-ÜGYNÖKÖK (feladat_ugynok.py, 2026-11-23) — hálózat nélkül, hamis kapuval és hamis forrással.

Amit ezek a tesztek őriznek:
  1. SZÓBAN, a Feldwebelen át: „indíts egy ügynököt, ami…” → recept (ugynok_*), ütemezve; „milyen ügynökeim vannak?”
     → lista; „állítsd le a kamatfigyelőt” → szünetel (ragozott névvel is); „szüntesd meg” → eltűnik. Egyszeri feladat
     azonnal indul. A rossz vagy túl sűrű cron nem megy be.
  2. A KABIN: a két új POST süti nélkül 401 (semmi nem jön létre); süti mellett az ügynök a bal sávba kerül, saját
     ablaka van (a futások naplója), és a gombok (futtat, leállít, újraindít, megszüntet) ugyanazt a belépőt hívják.
  3. A KERET: futásonként (a kapu által könyvelt költségből, a hívás előtt becsülve) megáll, és kimondja; ha a havi
     keret elfogy, az ügynök LEÁLL, és szól; Anthropic-kapu nélkül a futás kimarad (SiliconFlow-ra nem vált).
  4. KIFELÉ CSAK TERVEZET: küldés, publikálás, naptárírás, SiliconFlow-ügynök tiltott (a forrásig sem jut); a naptár
     JAVASLAT az action_drafts-ban (a Feldwebel egyszavas „igen”-je nem hagyhatja jóvá), a levél Gmail-tervezet.
  5. A TUDÁSBÁZIS NEM SZIVÁROG SF FELÉ: a futás csak a közvetlen Claude-kapun megy; a napló sorai az SF-kontextusból
     kizárva; a tudásbázist olvasó ügynök jelentése csak-Claude jellel kerül az előzménybe; az `ugynok_*` receptet az
     execute_recipe (MCP és Feldwebel) nem futtatja, a create_recipe nem hozhatja létre, a cron-szál a Claude-útra viszi.
  6. A leállítás a futó munkát is megszakítja; az egyszeri ügynök a futás után kész.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import feladat_ugynok as F
import futar_kliens
import llm_kapu

GYOKER = Path(__file__).resolve().parent.parent
MOST = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)            # hétfő 10:00 Budapest

_TOVABBI_DDL = """
CREATE TABLE IF NOT EXISTS telegram_history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL,
  role TEXT NOT NULL, agent_id TEXT DEFAULT 'deepseek', content TEXT NOT NULL, timestamp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT DEFAULT '',
  assigned_to TEXT NOT NULL, assigned_by TEXT NOT NULL, priority TEXT DEFAULT 'normal', status TEXT DEFAULT 'pending',
  deadline TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discussions (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, context TEXT DEFAULT '',
  status TEXT DEFAULT 'open', resolution TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS discussion_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, discussion_id INTEGER NOT NULL,
  instance TEXT NOT NULL, content TEXT NOT NULL, timestamp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS heartbeats (instance TEXT PRIMARY KEY, last_seen TEXT NOT NULL, session_info TEXT DEFAULT '');
"""


def _run(c):
    return asyncio.run(c)


@pytest.fixture
def db(get_db):
    c = get_db()
    c.executescript(_TOVABBI_DDL)
    c.commit()
    c.close()
    return get_db


@pytest.fixture(autouse=True)
def _tiszta():
    F._FUTO.clear()
    elozo = dict(F._DEPS)
    yield
    F._FUTO.clear()
    F._DEPS.clear()
    F._DEPS.update(elozo)


@pytest.fixture
def kulcs(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt-TITOK")
    monkeypatch.setattr(llm_kapu, "_KOLTES", {"honap": "", "usd": 0.0, "hivasok": 0, "szintenkent": {}})


def _sor(get_db, rid):
    c = get_db()
    try:
        return c.execute("SELECT * FROM pyramid_recipes WHERE id = ?", (rid,)).fetchone()
    finally:
        c.close()


def _naplo(get_db, rid, targy=None):
    c = get_db()
    try:
        q = "SELECT subject, message FROM messages WHERE sender = ? AND recipient = ?"
        p = [F.azonosito(rid), F.azonosito(rid)]
        if targy:
            q += " AND subject = ?"
            p.append(targy)
        return [(r["subject"], json.loads(r["message"])) for r in c.execute(q + " ORDER BY id", p)]
    finally:
        c.close()


def _letrehoz(get_db, **kw):
    alap = {"nev": "Kamatfigyelő", "feladat": "Nézd meg az MNB-közleményeket, és szólj, ha kamatdöntés van.",
            "utemezes": "0 7 * * *"}
    alap.update(kw)
    c = get_db()
    try:
        out = F.letrehoz(c, **alap)
        c.commit()
    finally:
        c.close()
    assert out.get("ok"), out
    return out["ugynok"]["rid"]


# ── hamis forrás és hamis kapu (a Nachtschicht-tesztek mintájára) ────────────

class Forras:
    def __init__(self, *, tudasbazis=None):
        self.hivasok, self._tudasbazis = [], tudasbazis

    def tudasbazis_elerheto(self):
        return True

    async def kulso_definiciok(self):
        return [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object"}}}
                for n in ("lexonomics", "medical", "makronom", "echolot", "engine", "studio")]

    async def feldwebel_eszkoz(self, nev, args, modell):
        self.hivasok.append(("feldwebel", nev, modell))
        if nev == "tudasbazis":
            return json.dumps({"forras": "tudásbázis", "valasz": self._tudasbazis or "nincs"})
        return json.dumps({"forras": nev, "valasz": "MNB: az alapkamat 6,50% marad (2026-11-23)."})

    async def tervezet(self, **kw):
        self.hivasok.append(("tervezet", kw))
        return {"draft_id": "d1", "to": kw.get("to") or "x@y.hu", "subject": kw.get("subject") or "Re: x",
                "link": "https://mail.google.com/mail/u/0/#drafts?compose=m1", "body": kw["body"]}

    async def naptar(self, tol, ig):
        return []

    async def level(self, mid):
        return {"id": mid, "body": "levél"}


def _valasz(szoveg="", hivasok=None, usd=0.02, modell="claude-sonnet-5-5"):
    msg = {"role": "assistant", "content": szoveg}
    if hivasok:
        msg["tool_calls"] = [{"id": f"t{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                             for i, (n, a) in enumerate(hivasok)]
    return {"model": modell, "koltes_usd": usd,
            "choices": [{"message": msg, "finish_reason": "tool_calls" if hivasok else "stop"}]}


def _zaro(jelzes=True, osszegzes="Ma nem volt kamatdöntés.", eredmeny="Az MNB ma nem döntött; forrás: mnb.hu"):
    return _valasz(json.dumps({"jelzes": jelzes, "osszegzes": osszegzes, "eredmeny": eredmeny}, ensure_ascii=False))


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


def _futtat(get_db, rid, kapu, *, forras=None, **kw):
    szolt, elozmeny = [], []

    async def tg(s):
        szolt.append(s)
    e = _run(F.futas(rid, trigger="cron", get_db=get_db, forras=lambda: forras or Forras(), chat=kapu,
                     telegram=tg, elozmeny=lambda t, cs: elozmeny.append((t, cs)), most=MOST, **kw))
    return e, szolt, elozmeny


# ══ 1. SZÓBAN, A FELDWEBELEN ÁT ═══════════════════════════════════════════════

def _eszkozhivas(nev, args, modell="claude-haiku-5-5"):
    return {"model": modell, "choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": nev, "arguments": json.dumps(args)}}]}}]}


def _szoveg(s, modell="claude-haiku-5-5"):
    return {"model": modell, "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": s}}]}


@pytest.fixture
def feldwebel(db, monkeypatch):
    """Feldwebel-kör hamis LLM-mel (a közös kapun), a feladat-ügynök háttérfutása feljegyezve (nem fut)."""
    import feldwebel as fw
    from feldwebel import BridgeContext, responder
    hivasok, sor, indult = [], [], []

    async def kapu(payload, purpose=""):
        hivasok.append(json.loads(json.dumps(payload)))
        return sor.pop(0), None, []

    async def push(s):
        pass

    async def ures():
        return []
    monkeypatch.setattr(fw, "_ctx", BridgeContext(telegram_push=push, get_db=db, llm_chat=kapu))
    monkeypatch.setattr(responder, "_kulso_eszkozok", ures)
    monkeypatch.setattr(responder, "_build_system_prompt", lambda ctx, query="": "rendszer")
    monkeypatch.setattr(F, "hatterben", lambda rid, trigger="": indult.append((rid, trigger)) or {"indult": True})
    return responder, hivasok, sor, indult


def _eszkoz_eredmeny(payload) -> dict:
    return json.loads([m for m in payload["messages"] if m.get("role") == "tool"][-1]["content"])


def test_szobeli_inditas_lista_leallitas_megszuntetes(feldwebel, db):
    responder, hivasok, sor, indult = feldwebel
    feladat = "Nézd meg az MNB-közleményeket, és szólj, ha kamatdöntés van (mennyi lett, mihez képest)."
    sor += [_eszkozhivas("ugynok", {"muvelet": "indit", "nev": "Kamatfigyelő", "feladat": feladat,
                                    "utemezes": "0 7 * * *"}),
            _szoveg("Elindítottam.")]
    _run(responder.respond("indíts egy ügynököt, ami minden reggel 7-kor megnézi az MNB-közleményeket, és szól, ha "
                           "kamatdöntés van", "42"))
    assert "ugynok" in {t["function"]["name"] for t in hivasok[0]["tools"]}
    v = _eszkoz_eredmeny(hivasok[1])
    assert v["ok"] and "Elindítottam: „Kamatfigyelő”" in v["uzenet"] and "minden nap 7:00" in v["uzenet"]
    assert "$0.50/futás" in v["uzenet"] and "$10/hó" in v["uzenet"]
    rid = v["ugynok"]["rid"]
    assert "feladat" not in v["ugynok"] and feladat not in json.dumps(v, ensure_ascii=False)   # lásd 5.
    r = _sor(db, rid)
    assert r["name"] == "ugynok_kamatfigyelo" and r["cron_schedule"] == "0 7 * * *" and r["cron_enabled"] == 1
    assert r["prompt_template"] == feladat and r["created_by"] == "feldwebel"
    meta = json.loads(r["description"])
    assert meta["tipus"] == "feladat_ugynok" and meta["allapot"] == "aktiv" and meta["ertesites"] == "ha_van"
    assert indult == []                                    # ütemezett: nem fut azonnal

    sor += [_eszkozhivas("ugynok", {"muvelet": "lista"}), _szoveg("Egy ügynököd van.")]
    _run(responder.respond("milyen ügynökeim vannak?", "42"))
    v = _eszkoz_eredmeny(hivasok[3])
    assert [u["nev"] for u in v["ugynokok"]] == ["Kamatfigyelő"] and "köv.:" in v["uzenet"]

    sor += [_eszkozhivas("ugynok", {"muvelet": "leallit", "nev": "a kamatfigyelőt"}), _szoveg("Leállítottam.")]
    _run(responder.respond("állítsd le a kamatfigyelőt", "42"))
    assert _eszkoz_eredmeny(hivasok[5])["uzenet"] == "Leállítottam: „Kamatfigyelő”."
    r = _sor(db, rid)
    assert r["cron_enabled"] == 0 and r["cron_schedule"] == "0 7 * * *"            # az ütemezés megmarad
    assert json.loads(r["description"])["allapot"] == "leallitva"

    sor += [_eszkozhivas("ugynok", {"muvelet": "ujraindit", "nev": "kamatfigyelő"}), _szoveg("Újraindítottam.")]
    _run(responder.respond("indítsd újra a kamatfigyelőt", "42"))
    assert _sor(db, rid)["cron_enabled"] == 1

    sor += [_eszkozhivas("ugynok", {"muvelet": "megszuntet", "nev": f"ugynok:{rid}"}), _szoveg("Megszüntettem.")]
    _run(responder.respond("szüntesd meg a kamatfigyelőt", "42"))
    assert _eszkoz_eredmeny(hivasok[9])["uzenet"] == "Megszüntettem: Kamatfigyelő."
    assert _sor(db, rid) is None
    esemenyek = [d["esemeny"] for _, d in _naplo(db, rid, F.TARGY_ESEMENY)]
    assert esemenyek == ["indit", "leallit", "ujraindit", "megszuntet"]


def test_egyszeri_feladat_azonnal_indul_es_mindig_szol(feldwebel, db):
    responder, hivasok, sor, indult = feldwebel
    sor += [_eszkozhivas("ugynok", {"muvelet": "indit", "nev": "Robotika-források",
                                    "feladat": "Gyűjtsd össze a robotikai tanulmány friss forrásait (2025–2026)."}),
            _szoveg("Dolgozik rajta.")]
    _run(responder.respond("egyszer: gyűjtsd össze a robotikai tanulmány friss forrásait", "42"))
    v = _eszkoz_eredmeny(hivasok[1])
    assert indult == [(v["ugynok"]["rid"], "feldwebel")] and "Most dolgozik rajta" in v["uzenet"]
    r = _sor(db, v["ugynok"]["rid"])
    meta = json.loads(r["description"])
    assert r["cron_schedule"] is None and r["cron_enabled"] == 0
    assert meta["egyszeri"] is True and meta["ertesites"] == "mindig"


def test_az_ugynok_kezelese_nem_faber_kivansag():
    import faber_relay
    for t in ("a kamatfigyelő ügynök már nem kell", "állítsd le az ügynököt", "indíts egy ügynököt, ami figyel",
              "mégse kell az ügynök"):
        assert faber_relay.heurisztika(t) == "egyeb", t
    assert "feladat-ügynök" in faber_relay.SZANDEK_PROMPT
    assert _run(faber_relay.szandek("a kamatfigyelő ügynök már nem kell", llm=None)) is None   # kulcs nélkül: heurisztika


@pytest.mark.parametrize("cron", ["*/15 * * * *", "* * * * *", "0 7 * *", "61 7 * * *"])
def test_rossz_vagy_tul_suru_cron_nem_megy_be(db, cron):
    c = db()
    try:
        out = F.letrehoz(c, nev="Próba", feladat="Valami értelmes feladat szövege.", utemezes=cron)
        assert "error" in out
        assert c.execute("SELECT COUNT(*) FROM pyramid_recipes WHERE name LIKE 'ugynok%'").fetchone()[0] == 0
    finally:
        c.close()


def test_azonos_nev_nem_ketszer_es_a_kereses_ragozva(db):
    rid = _letrehoz(db)
    _letrehoz(db, nev="Kamatfigyelő ECB")
    c = db()
    try:
        assert "már van" in F.letrehoz(c, nev="kamatfigyelő", feladat="Megint ugyanaz a feladat.")["error"]
        assert [r["id"] for r in F.keres(c, "Kamatfigyelő")] == [rid]          # a pontos név nyer
        assert [r["id"] for r in F.keres(c, f"ugynok:{rid}")] == [rid]
        assert len(F.keres(c, "kamat")) == 2                                   # kétértelmű
        assert F.keres(c, "valami más") == []
    finally:
        c.close()
    out = F.vegrehajt("leallit", {"nev": "kamat"}, get_db=db)
    assert "több ügynök" in out["error"] and len(out["jeloltek"]) == 2


def test_kovetkezo_futas_es_utemezes_szoveg():
    assert F.kovetkezo("0 7 * * *", MOST).isoformat() == "2026-11-24T07:00:00+01:00"
    assert F.kovetkezo("30 17 * * 1-5", MOST).isoformat() == "2026-11-23T17:30:00+01:00"
    assert F.kovetkezo("0 8 * * 1", MOST).isoformat() == "2026-11-30T08:00:00+01:00"
    assert F.ido_szoveg(F.kovetkezo("0 7 * * *", MOST), MOST) == "holnap 7:00"
    assert F.utemezes_szoveg("0 7 * * *") == "minden nap 7:00"
    assert F.utemezes_szoveg("30 17 * * 1-5") == "hétköznap 17:30"
    assert F.utemezes_szoveg("0 8 * * 1") == "minden hétfőn 8:00"
    assert F.utemezes_szoveg("0 9 24 11 *", True) == "11-24 9:00 (egyszer)"
    assert F.utemezes_szoveg("15 * * * *") == "óránként, :15-kor"
    assert F.utemezes_szoveg(None, True) == "egyszer, azonnal"


# ══ 2. A KABIN ════════════════════════════════════════════════════════════════

TOKEN = "123456:TESZT-BOT-TOKEN"


def _app(db):
    from fastmcp import FastMCP
    import ui_kabin as ui

    async def semmi(*a, **k):
        return None
    deps = {"get_db": db, "bot_token": lambda: TOKEN, "chat_id": lambda: "42", "telegram_kuld": semmi,
            "asszisztens": semmi, "ugynokok": lambda: {}, "llm_allapot": lambda: {"elerheto": False},
            "jelenlet": lambda rows: {}, "draft_dont": semmi}
    mcp = FastMCP("ui-teszt")
    ui.regisztral(mcp, deps)
    return mcp.http_app(stateless_http=True)


async def _keres(app, method, path, *, suti=True, **kw):
    import httpx
    import ui_kabin as ui
    fej = dict(kw.pop("headers", {}) or {})
    if suti:
        fej["cookie"] = f"{ui.SUTI_NEV}={ui.suti_ertek(ui.kulcs(TOKEN))}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, path, headers=fej, **kw)


def _darab(db):
    c = db()
    try:
        return c.execute("SELECT COUNT(*) FROM pyramid_recipes WHERE name LIKE 'ugynok%'").fetchone()[0]
    finally:
        c.close()


def test_kabin_ugynok_vegpontok_suti_nelkul_401(db, monkeypatch):
    indult = []
    monkeypatch.setattr(F, "hatterben", lambda rid, trigger="": indult.append(rid) or {"indult": True})
    rid = _letrehoz(db)
    app = _app(db)
    for ut, body in (("/api/ui/ugynok", {"nev": "Betolakodó", "feladat": "Küldj levelet mindenkinek."}),
                     ("/api/ui/ugynok/muvelet", {"id": f"ugynok:{rid}", "muvelet": "megszuntet"}),
                     ("/api/ui/ugynok/muvelet", {"id": f"ugynok:{rid}", "muvelet": "futtat"})):
        assert _run(_keres(app, "POST", ut, suti=False, json=body)).status_code == 401
        hamis = {"cookie": "bridge_kabin=1.2.3"}
        assert _run(_keres(app, "POST", ut, suti=False, headers=hamis, json=body)).status_code == 401
    assert _darab(db) == 1 and _sor(db, rid) is not None and indult == []
    assert _run(_keres(app, "GET", f"/api/ui/beszelgetes?ablak=ugynok:{rid}", suti=False)).status_code == 401


def test_kabin_uj_ugynok_ablak_es_gombok(db, monkeypatch):
    indult = []
    monkeypatch.setattr(F, "hatterben", lambda rid, trigger="": indult.append((rid, trigger)) or {"indult": True})
    app = _app(db)
    r = _run(_keres(app, "POST", "/api/ui/ugynok", json={
        "nev": "Kamatfigyelő", "feladat": "Nézd meg az MNB-közleményeket, és szólj, ha kamatdöntés van.",
        "utemezes": "30 17 * * 1-5", "keret_usd": 99, "havi_keret_usd": 3}))
    assert r.status_code == 200, r.text
    u = r.json()["ugynok"]
    assert u["id"].startswith("ugynok:") and u["keret_usd"] == F.KERET_FUTAS_PLAFON and u["havi_keret_usd"] == 3
    uid = u["id"]

    a = _run(_keres(app, "GET", "/api/ui/allapot")).json()
    sajat = [x for x in a["ugynokok"] if x["id"] == uid]
    assert len(sajat) == 1 and sajat[0]["feladat_ugynok"]["utemezes_szoveg"] == "hétköznap 17:30"
    assert sajat[0]["elerheto"] is True and sajat[0]["szerep"] == "hétköznap 17:30"
    assert {"feldwebel", "kimi", "faber"} <= {x["id"] for x in a["ugynokok"]}        # a fixek megmaradnak

    # egy futás a naplóban → az ügynök ablakában, költséggel
    c = db()
    F.naplo(c, u["rid"], F.TARGY_FUTAS, {"esemeny": "futas", "allapot": "kesz", "jelzes": True, "trigger": "cron",
                                         "osszegzes": "Kamatdöntés: 6,25%.", "eredmeny": "Az MNB 25 bázisponttal csökkentett.",
                                         "koltes_usd": 0.04, "keret_usd": 0.5, "modell": "claude-sonnet-5-5",
                                         "eszkozok": ["makronom", "makronom"]})
    c.commit()
    c.close()
    b = _run(_keres(app, "GET", f"/api/ui/beszelgetes?ablak={uid}")).json()
    assert b["ugynok"]["nev"] == "Kamatfigyelő"
    tipusok = [(t["tipus"], t["ki"]) for t in b["tetelek"]]
    assert tipusok == [("rendszer", "rendszer"), ("uzenet", uid)]
    futas = b["tetelek"][1]
    assert "**Kamatdöntés: 6,25%.**" in futas["szoveg"] and futas["futas"]["koltes_usd"] == 0.04
    assert futas["eszkozok"] == ["makronom"] and futas["modell"] == "claude-sonnet-5-5"
    a = _run(_keres(app, "GET", "/api/ui/allapot")).json()
    assert [x for x in a["ugynokok"] if x["id"] == uid][0]["utolso"]["szoveg"] == "Kamatdöntés: 6,25%."   # előnézet
    # a feldwebel ablakába és a postaládába nem kerül
    for ablak in ("feldwebel", "postalada"):
        assert all(not str(t["ki"]).startswith("ugynok:") for t in
                   _run(_keres(app, "GET", f"/api/ui/beszelgetes?ablak={ablak}")).json()["tetelek"])

    def muvelet(m):
        return _run(_keres(app, "POST", "/api/ui/ugynok/muvelet", json={"id": uid, "muvelet": m}))
    assert muvelet("futtat").json()["futas"] == {"indult": True} and indult == [(u["rid"], "kabin")]
    assert muvelet("leallit").json()["ugynok"]["allapot"] == "leallitva"
    assert _sor(db, u["rid"])["cron_enabled"] == 0
    assert muvelet("ujraindit").json()["ugynok"]["allapot"] == "aktiv"
    assert muvelet("torol").status_code == 400
    assert muvelet("megszuntet").json()["megszuntetve"]["nev"] == "Kamatfigyelő"
    assert muvelet("leallit").status_code == 404
    assert _run(_keres(app, "GET", "/api/ui/beszelgetes?ablak=ugynok:x")).status_code == 400
    assert all(x["id"] != uid for x in _run(_keres(app, "GET", "/api/ui/allapot")).json()["ugynokok"])


def test_kabin_rossz_kerest_kimondja(db):
    app = _app(db)
    r = _run(_keres(app, "POST", "/api/ui/ugynok", json={"nev": "", "feladat": "x"}))
    assert r.status_code == 400 and "név" in r.json()["hiba"]
    r = _run(_keres(app, "POST", "/api/ui/ugynok", json={"nev": "Sűrű", "feladat": "Nézz körül percenként.",
                                                          "utemezes": "* * * * *"}))
    assert r.status_code == 400 and "15 perc" in r.json()["hiba"] and _darab(db) == 0


# ══ 3. A KERET ════════════════════════════════════════════════════════════════

def test_futas_kesz_koltes_konyvelve_es_jelzes(db, kulcs):
    rid = _letrehoz(db)
    kapu = Kapu([_valasz(hivasok=[("makronom", {"eszkoz": "mnb_releases", "parameterek": {}})], usd=0.03),
                 _zaro(jelzes=True, osszegzes="Kamatdöntés: az alapkamat 6,50% marad.")])
    e, szolt, elozmeny = _futtat(db, rid, kapu)
    assert e["allapot"] == "kesz" and e["jelzes"] is True and e["koltes_usd"] == pytest.approx(0.05)
    assert [p for p, _ in kapu.hivasok] == [F.cel(rid)] * 2                  # a feladat-ügynök célja: közép szint
    assert llm_kapu.szint_valaszt({"messages": []}, F.cel(rid))[0] == "kozep"
    assert len(szolt) == 1 and "ÜGYNÖK · Kamatfigyelő" in szolt[0] and "Költség: $0.05 / $0.50" in szolt[0]
    assert elozmeny and elozmeny[0][1] is False                               # nem olvasott tudásbázist
    meta = json.loads(_sor(db, rid)["description"])
    assert meta["koltes"]["futasok"] == 1 and meta["koltes"]["honap_usd"] == pytest.approx(0.05)
    assert meta["utolso"]["allapot"] == "kesz"
    # a második futás promptjában ott az első összegzése (ne jelezze újra ugyanazt)
    kapu2 = Kapu([_zaro(jelzes=False)])
    e2, szolt2, _ = _futtat(db, rid, kapu2)
    assert "Kamatdöntés: az alapkamat 6,50% marad." in kapu2.hivasok[0][1]["messages"][0]["content"]
    assert e2["jelzes"] is False and szolt2 == []                            # „ha_van”: nincs új, nem szól


def test_futasonkenti_keret_elott_megall_es_kimondja(db, kulcs):
    rid = _letrehoz(db, keret_usd=0.10)
    kapu = Kapu([_valasz(hivasok=[("web_search", {"query": "MNB kamatdöntés"})], usd=0.08), _zaro()])
    e, szolt, _ = _futtat(db, rid, kapu)
    assert e["allapot"] == "keret" and e["hivasok"] == 1 and e["koltes_usd"] == pytest.approx(0.08)
    assert len(kapu.hivasok) == 1                                            # a második hívás el sem ment
    assert "a futás kerete elfogyott" in e["hiba"] and len(szolt) == 1 and "Költség: $0.08 / $0.10" in szolt[0]


def test_havi_keret_elfogyott_az_ugynok_leall(db, kulcs):
    rid = _letrehoz(db, havi_keret_usd=1)
    c = db()
    row = c.execute("SELECT * FROM pyramid_recipes WHERE id = ?", (rid,)).fetchone()
    m = json.loads(row["description"])
    m["koltes"] = {"honap": F._honap(), "honap_usd": 1.0, "osszes_usd": 4.0, "futasok": 30}
    c.execute("UPDATE pyramid_recipes SET description = ? WHERE id = ?", (json.dumps(m), rid))
    c.commit()
    c.close()
    kapu = Kapu([])
    e, szolt, _ = _futtat(db, rid, kapu)
    assert kapu.hivasok == [] and e["allapot"] == "kimaradt"
    r = _sor(db, rid)
    assert r["cron_enabled"] == 0 and json.loads(r["description"])["allapot"] == "leallitva"
    assert len(szolt) == 1 and "havi keret elfogyott" in szolt[0] and "leállítottam" in szolt[0]


def test_anthropic_nelkul_kimarad_sf_re_nem_valt_es_egyszer_szol(db):
    rid = _letrehoz(db)
    kapu = Kapu([])
    e, szolt, _ = _futtat(db, rid, kapu)
    assert kapu.hivasok == [] and e["allapot"] == "kimaradt" and "SiliconFlow-ra nem váltok" in e["hiba"]
    assert len(szolt) == 1
    _, szolt2, _ = _futtat(db, rid, kapu)
    assert szolt2 == []                                                      # a második kimaradásnál nem szól újra
    assert _sor(db, rid)["cron_enabled"] == 1                                # az ütemezett ügynök marad


# ══ 4. KIFELÉ CSAK TERVEZET ═══════════════════════════════════════════════════

@pytest.mark.parametrize("nev", sorted(F.TILTOTT) + ["valami_uj_eszkoz", "capture_send_email"])
def test_tiltott_eszkoz_elutasitva(nev):
    assert F.engedett(nev, {"eszkoz": "x"}) is not None


@pytest.mark.parametrize("nev", sorted(F.ENGEDETT))
def test_engedett_eszkoz(nev):
    assert F.engedett(nev, {"eszkoz": "x"}) is None


def test_a_kifele_hato_utak_mind_tiltottak():
    for nev in ("send_email", "send_message", "send_file_email", "create_calendar_event", "agora", "story_comments",
                "ai_query", "ai_task", "execute_recipe", "schedule_recipe", "create_task", "export_task", "echolot",
                "engine", "studio", "faber", "ugynok", "chatgpt", "gemini", "gmail_poll", "calendar_poll"):
        assert nev in F.TILTOTT and nev not in F.ENGEDETT


def test_a_modell_nem_is_latja_a_kifele_hato_eszkozoket():
    defs = _run(F.eszkoz_definiciok(Forras()))
    nevek = {t["function"]["name"] for t in defs}
    assert nevek <= F.ENGEDETT and "calendar_create" in nevek and "gmail_tervezet" in nevek
    assert not nevek & {"send_email", "echolot", "engine", "studio", "ai_task", "send_message"}


def test_futas_kifele_csak_tervezet_es_javaslat(db, kulcs):
    from feldwebel import jovahagyas
    rid = _letrehoz(db)
    f = Forras()
    kapu = Kapu([
        _valasz(hivasok=[("send_email", {"to": "x@y.hu", "subject": "s", "body": "b"}),
                         ("calendar_create", {"summary": "MNB kamatdöntés", "start_time": "2026-12-16T14:00:00"}),
                         ("gmail_tervezet", {"to": "elemzo@pelda.hu", "subject": "Kamatdöntés", "body": "Szia, …"})]),
        _zaro()])
    e, szolt, _ = _futtat(db, rid, kapu, forras=f)
    assert e["allapot"] == "kesz" and e["elutasitva"] == ["send_email"]
    assert [h[0] for h in f.hivasok] == ["tervezet"]                          # a küldés a forrásig sem jutott
    c = db()
    d = c.execute("SELECT * FROM action_drafts").fetchall()
    c.close()
    assert len(d) == 1 and d[0]["status"] == "pending" and d[0]["kind"] == "calendar"
    assert d[0]["source_message_id"] == f"ugynok:{rid}" and d[0]["due_at"] == "2026-12-16T14:00:00"
    assert jovahagyas.fuggo(db, "42") == []                                   # az egyszavas „igen” nem éri el
    assert e["javaslatok"][0]["draft_id"] == d[0]["id"] and e["tervezetek"][0]["subject"] == "Kamatdöntés"
    assert len(szolt) == 1 and "a kabin munkapadján hagyhatod jóvá" in szolt[0] and "nem küldtem el" in szolt[0]


# ══ 5. A TUDÁSBÁZIS NEM SZIVÁROG SF FELÉ ════════════════════════════════════════

def test_tudasbazist_olvaso_futas_csak_claude_es_jelolt(db, kulcs):
    rid = _letrehoz(db)
    f = Forras(tudasbazis="TITKOS-KB-777: a kamatdöntésre a Kommandant 2026-10-01-én azt mondta…")
    kapu = Kapu([_valasz(hivasok=[("tudasbazis", {"kerdes": "mit mondtam a kamatdöntésről?"})]),
                 _zaro(osszegzes="TITKOS-KB-777 alapján: nincs új.")])
    e, szolt, elozmeny = _futtat(db, rid, kapu, forras=f)
    assert e["tudasbazis"] is True
    assert ("feldwebel", "tudasbazis", "claude-sonnet-5-5") in f.hivasok   # a futár a VALÓDI modellt kapja
    assert elozmeny and elozmeny[0][1] is True                               # csak-Claude jellel az előzménybe
    assert json.loads(_sor(db, rid)["description"])["tudasbazis_erintett"] is True
    c = db()
    try:
        osszes = c.execute("SELECT COUNT(*) FROM messages WHERE sender LIKE 'ugynok:%'").fetchone()[0]
        sf_lathato = c.execute(f"SELECT COUNT(*) FROM messages WHERE sender LIKE 'ugynok:%' "
                               f"AND {futar_kliens.SF_KIZARAS_SQL}").fetchone()[0]
        szoveg = " ".join(r[0] for r in c.execute(f"SELECT message FROM messages WHERE {futar_kliens.SF_KIZARAS_SQL}"))
    finally:
        c.close()
    assert osszes >= 2 and sf_lathato == 0 and "TITKOS-KB-777" not in szoveg
    # a recept meta-ja (description) szöveget nem tárol: a list_recipes SiliconFlow-on futó körnek is mutatja
    assert "TITKOS-KB-777" not in _sor(db, rid)["description"]


def test_a_feldwebel_eszkoz_nem_adja_vissza_a_feladatot_es_az_eredmenyt(db, kulcs):
    from feldwebel import BridgeContext
    ctx = BridgeContext(get_db=db)
    feladat = "TITKOS-KB-FELADAT: figyeld a Kommandant tudásbázisában talált ügyet a bíróságon."
    v = json.loads(_run(F.feldwebel_hiv({"muvelet": "indit", "nev": "Bírósági figyelő", "feladat": feladat,
                                         "utemezes": "0 8 * * 1-5"}, ctx, vedett=True)))
    rid = v["ugynok"]["rid"]
    assert "TITKOS-KB-FELADAT" not in json.dumps(v, ensure_ascii=False)
    assert json.loads(_sor(db, rid)["description"])["tudasbazis_erintett"] is True   # tudásbázis-körben született
    _futtat(db, rid, Kapu([_zaro(osszegzes="TITKOS-EREDMENY")]))
    lista = json.loads(_run(F.feldwebel_hiv({"muvelet": "lista"}, ctx)))
    assert "TITKOS" not in json.dumps(lista, ensure_ascii=False) and lista["ugynokok"][0]["nev"] == "Bírósági figyelő"
    _, _, elozmeny = _futtat(db, rid, Kapu([_zaro(jelzes=True)]))
    assert elozmeny[-1][1] is True                                           # a jelentése csak-Claude jellel


def test_erintett_ugynok_kesobbi_jelentese_is_jelolt(db, kulcs):
    rid = _letrehoz(db)
    _futtat(db, rid, Kapu([_valasz(hivasok=[("tudasbazis", {"kerdes": "x"})]), _zaro()]),
            forras=Forras(tudasbazis="TITKOS"))
    _, _, elozmeny = _futtat(db, rid, Kapu([_zaro(jelzes=True)]))             # most nem olvasott tudásbázist
    assert elozmeny[-1][1] is True                                           # de az összegzése örökölhet belőle


def test_az_ugynok_recept_sf_uton_nem_fut(db, fake_app):
    from plugins import recipes
    from feldwebel import responder
    rid = _letrehoz(db)
    recipes.register_tools(fake_app, {"get_db": db, "enforce_func": lambda caller, verb: None})
    v = json.loads(_run(fake_app.tools["execute_recipe"](name="ugynok_kamatfigyelo", caller="kommandant")))
    assert v["status"] == "rejected" and v["reason_code"] == "feladat_ugynok"
    v = json.loads(_run(fake_app.tools["create_recipe"](
        name="ugynok_hamis", description="x", prompt_template="y", caller="cli-claus")))
    assert v["reason_code"] == "reserved_prefix"

    class Ctx:
        capture_state = {"_ai_task_func": lambda **k: pytest.fail("SiliconFlow-ügynökre ment")}
    Ctx.get_db = staticmethod(db)
    v = json.loads(_run(responder._execute_tool("execute_recipe", {"name": "ugynok_kamatfigyelo"}, Ctx())))
    assert "csak Claude-on" in v["error"]
    v = json.loads(_run(responder._execute_tool("create_recipe", {"name": "ugynok_x", "description": "d",
                                                                   "prompt_template": "p"}, Ctx())))
    assert "feladat-ügynököké" in v["error"]
    assert _sor(db, rid) is not None


def test_a_cron_szal_a_claude_utra_viszi_a_generikus_ag_elott():
    forras = (GYOKER / "server.py").read_text(encoding="utf-8")
    hurok = forras[forras.index("async def _cron_loop"):]
    elagazas = hurok.index('if feladat_ugynok.ugynok_e(r["name"]):')
    assert elagazas < hurok.index("# Execute via ai_task dispatch")
    assert "feladat_ugynok.cron_indit(r[\"id\"])" in hurok[elagazas:elagazas + 200]


def test_a_futas_soha_nem_hivja_az_sf_tolcsert():
    import inspect
    forras = inspect.getsource(F)
    assert "sf_chat" not in forras.replace("(`sf_chat`)", "") and "claude -p" not in forras.replace("(`claude -p`)", "")
    assert "llm_kapu.chat" in inspect.getsource(F.futas)


# ══ 6. LEÁLLÍTÁS FUTÁS KÖZBEN, EGYSZERI ════════════════════════════════════════

def test_leallitas_a_futo_munkat_megszakitja(db, kulcs):
    rid = _letrehoz(db)
    szolt = []

    async def fo():
        bent = asyncio.Event()

        async def vegtelen(payload, *, purpose=""):
            bent.set()
            await asyncio.sleep(3600)

        async def tg(s):
            szolt.append(s)
        F.beallit(get_db=db, forras=lambda: Forras(), chat=vegtelen, telegram=tg, elozmeny=None)
        assert F.hatterben(rid, trigger="kabin") == {"indult": True}
        assert F.hatterben(rid)["hiba"].startswith("már dolgozik")
        await asyncio.wait_for(bent.wait(), 5)
        assert F.fut_e(rid)
        out = F.vegrehajt("leallit", {"id": f"ugynok:{rid}"}, get_db=db, ki="kabin")
        assert out["futas_megszakitva"] is True
        for _ in range(100):
            if not F.fut_e(rid):
                break
            await asyncio.sleep(0.01)
        assert not F.fut_e(rid)
    _run(fo())
    futasok = [d for _, d in _naplo(db, rid, F.TARGY_FUTAS)]
    assert [d["allapot"] for d in futasok] == ["megszakitva"]
    assert json.loads(_sor(db, rid)["description"])["allapot"] == "leallitva" and szolt == []


def test_egyszeri_ugynok_a_futas_utan_kesz(db, kulcs):
    rid = _letrehoz(db, nev="Holnapi egyszeri", utemezes="0 9 24 11 *", egyszeri=True)
    assert _sor(db, rid)["cron_enabled"] == 1
    e, szolt, _ = _futtat(db, rid, Kapu([_zaro(jelzes=False)]))
    r = _sor(db, rid)
    assert e["allapot"] == "kesz" and r["cron_enabled"] == 0 and json.loads(r["description"])["allapot"] == "kesz"
    assert len(szolt) == 1                                                   # egyszerinél mindig szól
    # újraindítva most fut még egyszer (a régi dátum csak jövőre jönne el)
    c = db()
    out = F.ujraindit(c, c.execute("SELECT * FROM pyramid_recipes WHERE id = ?", (rid,)).fetchone())
    c.commit()
    c.close()
    assert out["azonnal"] is True and _sor(db, rid)["cron_enabled"] == 0


def test_kabin_es_feldwebel_ugyanazt_az_api_utat_vedik():
    """Az új POST-ok a védett listán vannak (a test_ui_kabin 401-es körbejárása is lefedi őket)."""
    import ui_kabin as ui
    utak = {ut: fn for ut, _, fn in ui.API_UTAK}
    for ut in ("/api/ui/ugynok", "/api/ui/ugynok/muvelet"):
        assert getattr(utak[ut], "vedett", False), ut
    assert re.search(r"ugynok", json.dumps(F.ESZKOZ_DEF)) and F.ESZKOZ_DEF["function"]["name"] == "ugynok"
