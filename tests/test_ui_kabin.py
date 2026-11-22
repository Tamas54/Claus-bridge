"""Bridge UI v2 — „kabinüveg” (2026-11-22): belépés, munkamenet, a webes kör, a Faber-kártya szerződése.

Amit ezek a tesztek őriznek:
  1. a belépőlink aláírt, 10 percig él, EGYSZER használható; a megerősítő lap (GET) nem fogyasztja el — a link-előnéző
     nem léptetheti be helyettünk és nem is égetheti el; a süti httpOnly, Secure, SameSite=Strict, 30 nap;
  2. a link és a süti aláírása külön tartomány: egyik sem használható a másik helyett;
  3. minden /api/ui/* út süti nélkül 401 — a kezelő le sem fut;
  4. a link címe SOSEM a Host-fejlécből jön élesben (különben bárki a saját domainjére irányíthatná a tokent);
  5. a kódos belépés (iPhone kezdőképernyő) kérésenként 5 próbát enged;
  6. a webes kör UGYANAZT az agyat futtatja, mint a Telegram: a Faber-szándék (mockolt LLM-mel) aláírva megy a
     Fabernek, a nyugta a webre jön, Telegramra nem; a Feldwebel válasza a modellel és az eszközökkel együtt jön vissza;
  7. a Faber-kártya: `faber:kapu` → döntésre vár; `faber:esemeny` → az állapot; a döntés aláírt `gomb`
     „{dontes}:{id}:{kod}”, a kódot a szerver keresi ki, és ugyanarra a kérelemre másodszor nem megy;
  8. aláíratlan 'faber'-címzettű sor nem jelenik meg a Kommandant szavaként.
"""
import asyncio
import json
import re

import pytest

import faber_relay
import kimenet
import ui_kabin as ui

TOKEN = "123456:TESZT-BOT-TOKEN"
K = ui.kulcs(TOKEN)

_TOVABBI_DDL = """
CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT DEFAULT '',
  assigned_to TEXT NOT NULL, assigned_by TEXT NOT NULL, priority TEXT DEFAULT 'normal', status TEXT DEFAULT 'pending',
  deadline TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discussions (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, context TEXT DEFAULT '',
  status TEXT DEFAULT 'open', resolution TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS discussion_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, discussion_id INTEGER NOT NULL,
  instance TEXT NOT NULL, content TEXT NOT NULL, timestamp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS heartbeats (instance TEXT PRIMARY KEY, last_seen TEXT NOT NULL, session_info TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS telegram_history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, role TEXT NOT NULL,
  agent_id TEXT DEFAULT 'deepseek', content TEXT NOT NULL, timestamp TEXT NOT NULL);
"""


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _tiszta_allapot():
    for tar in (ui._FELHASZNALT, ui._KODOK, ui._KOROK):
        tar.clear()
    ui._LINK_KERESEK.clear()
    yield


@pytest.fixture
def db(get_db):
    c = get_db()
    c.executescript(_TOVABBI_DDL)
    c.commit()
    c.close()
    return get_db


def _app(db, **felulir):
    from fastmcp import FastMCP
    kuldott = []

    async def telegram_kuld(szoveg):
        kuldott.append(szoveg)

    async def asszisztens(szoveg, k, csatorna):      # alap: visszhang (a valódi agy a 6. szakaszban)
        k.tetelek.append({"ido": "2026-11-22T12:00:00+00:00", "html": f"<b>FELDWEBEL</b>\n\nvisszhang: {szoveg}"})

    async def draft_dont(did, dontes):
        return {"draft_id": did, "status": "rejected" if dontes == "reject" else "approved"}

    deps = {"get_db": db, "bot_token": lambda: TOKEN, "chat_id": lambda: "42", "telegram_kuld": telegram_kuld,
            "asszisztens": asszisztens, "ugynokok": lambda: {}, "llm_allapot": lambda: {"elerheto": False},
            "jelenlet": lambda rows: {}, "draft_dont": draft_dont}
    deps.update(felulir)
    mcp = FastMCP("ui-teszt")
    ui.regisztral(mcp, deps)
    return mcp.http_app(stateless_http=True), kuldott


async def _keres(app, method, path, *, suti=True, host="t", **kw):
    import httpx
    fej = dict(kw.pop("headers", {}) or {})
    if suti:
        fej["cookie"] = f"{ui.SUTI_NEV}={ui.suti_ertek(K)}"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}") as c:
        return await c.request(method, path, headers=fej, **kw)


# ── 1–2. Token és süti ──────────────────────────────────────────────────

def test_belepo_token_egyszer_hasznalhato():
    t = ui.belepo_token(K)
    assert ui.belepo_ervenyes(K, t)
    assert ui.belepo_ervenyes(K, t)                   # a puszta ellenőrzés nem fogyaszt
    assert ui.belepo_felhasznal(K, t) is True
    assert ui.belepo_felhasznal(K, t) is False        # másodszor nem
    assert ui.belepo_ervenyes(K, t) is False


def test_belepo_token_10_perc_utan_lejar():
    t = ui.belepo_token(K, most=1_000_000)
    assert ui.belepo_ervenyes(K, t, most=1_000_000 + ui.LINK_ERVENY - 1)
    assert not ui.belepo_ervenyes(K, t, most=1_000_000 + ui.LINK_ERVENY + 1)
    assert not ui.belepo_felhasznal(K, t, most=1_000_000 + ui.LINK_ERVENY + 1)


def test_hamis_alairas_es_idegen_kulcs():
    t = ui.belepo_token(K)
    fej, _, sig = t.rpartition(".")
    assert not ui.belepo_ervenyes(K, fej + "." + "0" * len(sig))
    assert not ui.belepo_ervenyes(ui.kulcs("masik:token"), t)
    assert not ui.belepo_ervenyes(K, "szemet")
    assert not ui.belepo_ervenyes(None, t)
    # a kulcs más előtaggal képzett, mint a Faberé: a Faber-kulccsal aláírt nem jó itt
    assert ui.kulcs(TOKEN) != faber_relay.kulcs(TOKEN)


def test_link_es_suti_kulon_tartomany():
    t, s = ui.belepo_token(K), ui.suti_ertek(K)
    assert ui.suti_ervenyes(K, s) and not ui.suti_ervenyes(K, t)
    assert not ui.belepo_ervenyes(K, s)


def test_suti_30_nap_utan_lejar():
    s = ui.suti_ertek(K, most=0)
    assert ui.suti_ervenyes(K, s, most=ui.SUTI_ERVENY - 5)
    assert not ui.suti_ervenyes(K, s, most=ui.SUTI_ERVENY + 5)


# ── 3. 401 süti nélkül ──────────────────────────────────────────────────

def test_minden_api_ui_ut_401_suti_nelkul(db):
    lefutott = []

    async def asszisztens(*a):
        lefutott.append(a)
    app, _ = _app(db, asszisztens=asszisztens)
    assert len(ui.API_UTAK) >= 8
    for ut, modok, fn in ui.API_UTAK:
        assert getattr(fn, "vedett", False), ut
        valodi = re.sub(r"\{[^}]+\}", "1", ut)
        for m in modok:
            r = _run(_keres(app, m, valodi, suti=False, json={"szoveg": "x", "id": 1, "dontes": "mehet"}))
            assert r.status_code == 401, (m, ut, r.status_code)
            hamis = _run(_keres(app, m, valodi, suti=False, headers={"cookie": f"{ui.SUTI_NEV}=1.2.3"},
                                json={"szoveg": "x"}))
            assert hamis.status_code == 401, (m, ut)
    assert lefutott == []
    assert _run(_keres(app, "GET", "/api/ui/allapot")).status_code == 200


# ── 1. Belépés a linkkel ────────────────────────────────────────────────

def test_link_get_nem_fogyaszt_post_sutit_ad(db):
    app, _ = _app(db)
    t = ui.belepo_token(K)
    for _ in range(2):                                   # pl. a Telegram link-előnézője, aztán a Kommandant
        r = _run(_keres(app, "GET", f"/app/belepes?t={t}", suti=False))
        assert r.status_code == 200 and "belepes-urlap" in r.text
        assert "set-cookie" not in r.headers
        assert r.headers["referrer-policy"] == "no-referrer"
    r = _run(_keres(app, "POST", "/app/belepes", suti=False, content=f"t={t}",
                    headers={"content-type": "application/x-www-form-urlencoded"}))
    assert r.status_code == 303 and r.headers["location"] == "/app/"
    suti = r.headers["set-cookie"]
    assert suti.startswith(f"{ui.SUTI_NEV}=")
    low = suti.lower()
    assert "httponly" in low and "secure" in low and "samesite=strict" in low
    assert f"max-age={ui.SUTI_ERVENY}" in low and "path=/" in low
    ertek = suti.split(";")[0].split("=", 1)[1]
    assert ui.suti_ervenyes(K, ertek)
    # másodszor ugyanaz a link nem megy
    r2 = _run(_keres(app, "POST", "/app/belepes", suti=False, content=f"t={t}",
                     headers={"content-type": "application/x-www-form-urlencoded"}))
    assert r2.status_code == 403 and "set-cookie" not in r2.headers
    assert _run(_keres(app, "GET", f"/app/belepes?t={t}", suti=False)).status_code == 403


def test_lejart_link_nem_lep_be(db):
    app, _ = _app(db)
    t = ui.belepo_token(K, most=0)
    r = _run(_keres(app, "POST", "/app/belepes", suti=False, content=f"t={t}",
                    headers={"content-type": "application/x-www-form-urlencoded"}))
    assert r.status_code == 403 and "set-cookie" not in r.headers


def test_belepolink_a_nyilvanos_cimre_nem_a_host_fejlecre(db, monkeypatch):
    monkeypatch.setenv("BRIDGE_PUBLIC_URL", "https://bridge.pelda.hu/")
    app, kuldott = _app(db)
    r = _run(_keres(app, "POST", "/app/belepolink", suti=False, host="gonosz.example"))
    assert r.status_code == 200 and r.json()["ok"] and r.json()["keres"]
    assert len(kuldott) == 1
    m = re.search(r"(https://\S+/app/belepes\?t=(\S+))", kuldott[0])
    assert m and m.group(1).startswith("https://bridge.pelda.hu/app/belepes?t=")
    assert "gonosz" not in kuldott[0]
    assert ui.belepo_ervenyes(K, m.group(2))
    assert re.search(r"<b>\d{6}</b>", kuldott[0])                 # a kód is benne van
    # fojtás: azonnal újra nem küld
    assert _run(_keres(app, "POST", "/app/belepolink", suti=False)).status_code == 429
    assert len(kuldott) == 1


def test_belepolink_nyilvanos_cim_nelkul_csak_helyben(db, monkeypatch):
    monkeypatch.delenv("BRIDGE_PUBLIC_URL", raising=False)
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    app, kuldott = _app(db)
    assert _run(_keres(app, "POST", "/app/belepolink", suti=False, host="gonosz.example")).status_code == 503
    assert kuldott == []
    r = _run(_keres(app, "POST", "/app/belepolink", suti=False, host="localhost:8077"))
    assert r.status_code == 200 and "http://localhost:8077/app/belepes?t=" in kuldott[0]


def test_belepolink_chat_nelkul_503(db, monkeypatch):
    monkeypatch.setenv("BRIDGE_PUBLIC_URL", "https://b.hu")
    app, kuldott = _app(db, chat_id=lambda: "")
    assert _run(_keres(app, "POST", "/app/belepolink", suti=False)).status_code == 503 and kuldott == []


def test_kodos_belepes_es_probakorlat(db, monkeypatch):
    monkeypatch.setenv("BRIDGE_PUBLIC_URL", "https://b.hu")
    app, kuldott = _app(db)
    keres = _run(_keres(app, "POST", "/app/belepolink", suti=False)).json()["keres"]
    kod = re.search(r"<b>(\d{6})</b>", kuldott[0]).group(1)
    rossz = "000000" if kod != "000000" else "111111"
    for _ in range(ui.KOD_PROBA):
        assert _run(_keres(app, "POST", "/app/belepes/kod", suti=False, json={"keres": keres, "kod": rossz})
                    ).status_code == 403
    r = _run(_keres(app, "POST", "/app/belepes/kod", suti=False, json={"keres": keres, "kod": kod}))
    assert r.status_code == 403                                     # elfogytak a próbák: a jó kód sem megy

    ui._LINK_KERESEK.clear()
    kuldott.clear()
    keres = _run(_keres(app, "POST", "/app/belepolink", suti=False)).json()["keres"]
    kod = re.search(r"<b>(\d{6})</b>", kuldott[0]).group(1)
    r = _run(_keres(app, "POST", "/app/belepes/kod", suti=False, json={"keres": keres, "kod": kod}))
    assert r.status_code == 200 and "samesite=strict" in r.headers["set-cookie"].lower()
    assert _run(_keres(app, "POST", "/app/belepes/kod", suti=False, json={"keres": keres, "kod": kod})
                ).status_code == 403                                # egyszer használható


def test_statikus_feherlista_es_fejlecek(db):
    app, _ = _app(db)
    r = _run(_keres(app, "GET", "/app", suti=False))
    assert r.status_code == 307 and r.headers["location"] == "/app/"
    r = _run(_keres(app, "GET", "/app/", suti=False))
    assert r.status_code == 200 and "/app/app.js" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    r = _run(_keres(app, "GET", "/app/app.js", suti=False))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    r = _run(_keres(app, "GET", "/app/manifest.webmanifest", suti=False))
    assert r.json()["start_url"] == "/app/" and r.json()["display"] == "standalone"
    # nem fehérlistás út: a héj, nem a fájl
    r = _run(_keres(app, "GET", "/app/..%2Fserver.py", suti=False))
    assert r.status_code == 200 and "def get_db" not in r.text


# ── 7. Faber-kártya: a szerződés ────────────────────────────────────────

def _faber(db, subject, adat):
    c = db()
    c.execute("INSERT INTO messages (timestamp, sender, recipient, subject, message, priority) "
              "VALUES (datetime('now'), 'faber', 'kommandant', ?, ?, 'normal')", (subject, json.dumps(adat)))
    c.commit()
    c.close()


KAPU = {"id": 7, "repo": "echolot", "kivansag": "A 403-as oldalt is próbálja újra", "jelentes": "3 teszt zöld",
        "diffstat": "1 file changed, 12 insertions(+)", "kod": "AbC123xyz"}


def test_faber_kartya_allapotai(db):
    _faber(db, "faber:kapu", KAPU)
    c = db()
    k = ui.faber_kartyak(c, faber_relay.kulcs(TOKEN))
    assert len(k) == 1 and k[0]["var_dontesre"] and k[0]["repo"] == "echolot"
    c.close()
    _faber(db, "faber:esemeny", {"id": 7, "allapot": "elesitve", "szoveg": "kész, éles"})
    c = db()
    k = ui.faber_kartyak(c, faber_relay.kulcs(TOKEN))
    assert not k[0]["var_dontesre"] and k[0]["allapot"] == "elesitve" and k[0]["szoveg"] == "kész, éles"
    c.close()
    # új kapu ugyanarra a kérelemre (javított változat): újra döntésre vár, új kóddal
    _faber(db, "faber:kapu", {**KAPU, "kod": "Uj456kod"})
    c = db()
    k = ui.faber_kartyak(c, faber_relay.kulcs(TOKEN))
    assert k[0]["var_dontesre"] and k[0]["_kod"] == "Uj456kod"
    c.close()


def test_faber_dont_alairt_gomb_egyszer(db):
    _faber(db, "faber:kapu", KAPU)
    app, _ = _app(db)
    a = _run(_keres(app, "GET", "/api/ui/allapot")).json()
    kartya = a["jovahagyas"]["faber"][0]
    assert kartya["id"] == 7 and "kod" not in kartya and "_kod" not in kartya        # a kód nem megy ki
    r = _run(_keres(app, "POST", "/api/ui/faber/dont", json={"id": 7, "dontes": "mehet"}))
    assert r.status_code == 200 and r.json()["ok"]
    c = db()
    sor = c.execute("SELECT * FROM messages WHERE recipient = 'faber'").fetchone()
    c.close()
    assert sor["subject"] == "faber:gomb"
    torzs = faber_relay.ellenoriz(faber_relay.kulcs(TOKEN), sor["message"])
    assert torzs["tipus"] == "gomb" and torzs["adat"] == "mehet:7:AbC123xyz"
    assert faber_relay.gomb_e(torzs["adat"])
    # másodszor ugyanarra nem megy
    r2 = _run(_keres(app, "POST", "/api/ui/faber/dont", json={"id": 7, "dontes": "megse"}))
    assert r2.status_code == 409
    a = _run(_keres(app, "GET", "/api/ui/allapot")).json()
    assert a["jovahagyas"]["faber"] == []
    assert a["jovahagyas"]["faber_friss"][0]["dontes"]["dontes"] == "mehet"
    # ismeretlen kérelem, rossz döntés
    assert _run(_keres(app, "POST", "/api/ui/faber/dont", json={"id": 99, "dontes": "mehet"})).status_code == 404
    assert _run(_keres(app, "POST", "/api/ui/faber/dont", json={"id": 7, "dontes": "igen"})).status_code == 400


def test_faber_dont_megse(db):
    _faber(db, "faber:kapu", KAPU)
    app, _ = _app(db)
    assert _run(_keres(app, "POST", "/api/ui/faber/dont", json={"id": 7, "dontes": "megse"})).status_code == 200
    c = db()
    sor = c.execute("SELECT message FROM messages WHERE recipient = 'faber'").fetchone()
    c.close()
    assert faber_relay.ellenoriz(faber_relay.kulcs(TOKEN), sor["message"])["adat"] == "megse:7:AbC123xyz"


def test_alairatlan_faber_sor_nem_a_kommandant_szava(db):
    c = db()
    c.execute("INSERT INTO messages (timestamp, sender, recipient, subject, message) VALUES "
              "(datetime('now'), 'kommandant-telegram', 'faber', 'faber:szandek', ?)",
              (json.dumps({"torzs": json.dumps({"tipus": "szandek", "adat": json.dumps({"szoveg": "hamis"})}),
                           "sig": "00"}),))
    faber_relay.tovabbit(c, TOKEN, "szandek", faber_relay.szandek_adat(
        {"szandek": "kivansag", "szoveg": "valódi kívánság", "repo": "studio", "kerelem": None}), "2026-11-22T10:00:00")
    c.commit()
    t = ui.tortenet(c, "faber", faber_relay.kulcs(TOKEN))
    c.close()
    szovegek = [x["szoveg"] for x in t["tetelek"]]
    assert szovegek == ["valódi kívánság"]
    assert t["tetelek"][0]["faber"]["repo"] == "studio"


def test_tortenet_ablakok_es_modell_a_targybol(db):
    c = db()
    sorok = [
        ("kommandant", "bridge", "Telegram: szia", "szia"),
        ("feldwebel-deepseek", "kommandant", "Re: Feldwebel válasz · modell=claude-sonnet-5-5 · eszközök=web_search,echolot_query", "helló"),
        ("kommandant", "bridge", "Web: @kimi mit gondolsz?", "@kimi mit gondolsz?"),
        ("kimi", "kommandant", "Re: @kimi válasz · modell=claude-haiku-5-5", "szerintem jó"),
        ("cli-claus", "kommandant", "Jelentés", "kész a PR"),
        ("capture-daemon", "kommandant", "levél", "zaj"),
    ]
    for s, r, subj, m in sorok:
        c.execute("INSERT INTO messages (timestamp, sender, recipient, subject, message) VALUES (datetime('now'),?,?,?,?)",
                  (s, r, subj, m))
    c.commit()
    fw = ui.tortenet(c, "feldwebel", None)["tetelek"]
    assert [(x["ki"], x["szoveg"]) for x in fw] == [("kommandant", "szia"), ("feldwebel", "helló")]
    assert fw[0]["csatorna"] == "telegram"
    assert fw[1]["modell"] == "claude-sonnet-5-5" and fw[1]["eszkozok"] == ["web_search", "echolot_query"]
    km = ui.tortenet(c, "kimi", None)["tetelek"]
    assert [(x["ki"], x["szoveg"]) for x in km] == [("kommandant", "mit gondolsz?"), ("kimi", "szerintem jó")]
    assert km[0]["csatorna"] == "web" and km[1]["modell"] == "claude-haiku-5-5"
    pl = ui.tortenet(c, "postalada", None)["tetelek"]
    assert [(x["ki"], x["szoveg"]) for x in pl] == [("cli-claus", "kész a PR")]
    c.close()


def test_tg_szoveg():
    v = ui.tg_szoveg("<b>FELDWEBEL</b>\n\nA <b>forint</b> 1 &lt; 2 <i>[MODELLVÁLTÁS]</i>", "feldwebel")
    assert v == {"ki": "feldwebel", "szoveg": "A **forint** 1 < 2 [MODELLVÁLTÁS]", "hiba": False}
    v = ui.tg_szoveg("<b>KIMI</b> — API hiba: 402", "kimi")
    assert v["ki"] == "kimi" and v["hiba"] and v["szoveg"] == "API hiba: 402"
    v = ui.tg_szoveg("Rendben, szólok a Fabernek, hogy élesítse.", "feldwebel")
    assert v["ki"] == "feldwebel" and v["szoveg"].startswith("Rendben")
    v = ui.tg_szoveg("<b>📋 Nyitott feladatok (2)</b>\n\n• <b>#1</b> x", "feldwebel")
    assert v["ki"] == "feldwebel" and v["szoveg"].startswith("**📋 Nyitott feladatok (2)**")


def test_draft_dontes_a_bridge_eszkozen_at(db):
    kapott = []

    async def draft_dont(did, dontes):
        kapott.append((did, dontes))
        return {"error": "A draft mar eldontott allapotban van"} if did == 2 else {"draft_id": did, "status": "rejected"}
    app, _ = _app(db, draft_dont=draft_dont)
    assert _run(_keres(app, "POST", "/api/ui/draft/dont", json={"id": 1, "dontes": "reject"})).status_code == 200
    assert _run(_keres(app, "POST", "/api/ui/draft/dont", json={"id": 2, "dontes": "approve"})).status_code == 409
    assert _run(_keres(app, "POST", "/api/ui/draft/dont", json={"id": 3, "dontes": "torol"})).status_code == 400
    assert kapott == [(1, "reject"), (2, "approve")]


def test_vita_hozzaszolas(db):
    c = db()
    c.execute("INSERT INTO discussions (topic, status, created_by, created_at) VALUES ('Umbau', 'open', 'web-claus', "
              "'2026-11-22T09:00:00')")
    c.commit()
    c.close()
    app, _ = _app(db)
    r = _run(_keres(app, "POST", "/api/ui/vita/1", json={"szoveg": "Mehet az F6."}))
    assert r.status_code == 200
    utolso = r.json()["bejegyzesek"][-1]
    assert (utolso["ki"], utolso["szoveg"]) == ("kommandant", "Mehet az F6.")
    assert _run(_keres(app, "GET", "/api/ui/vita/9")).status_code == 404


# ── 6. A webes kör: ugyanaz az agy, a válasz a webre ───────────────────

async def _kor(app, szoveg, ugynok="feldwebel"):
    import httpx
    fej = {"cookie": f"{ui.SUTI_NEV}={ui.suti_ertek(K)}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/ui/chat", json={"szoveg": szoveg, "ugynok": ugynok}, headers=fej)
        assert r.status_code == 200, r.text
        kid = r.json()["kor_id"]
        for _ in range(200):
            await asyncio.sleep(0.01)
            j = (await c.get(f"/api/ui/chat/{kid}", headers=fej)).json()
            if j["allapot"] != "fut":
                return j
    raise AssertionError("a kör nem ért véget")


def test_chat_ures_es_ismeretlen_ugynok(db):
    app, _ = _app(db)
    assert _run(_keres(app, "POST", "/api/ui/chat", json={"szoveg": "  "})).status_code == 400
    assert _run(_keres(app, "POST", "/api/ui/chat", json={"szoveg": "x", "ugynok": "skynet"})).status_code == 400
    assert _run(_keres(app, "GET", "/api/ui/chat/nincs")).status_code == 404


def test_kimenet_lezart_kor_utan_a_telegrame():
    k = kimenet.Kimenet()

    async def kor():
        tok = kimenet.AKTUALIS.set(k)
        try:
            assert kimenet.elkap("<b>FELDWEBEL</b>\n\nszia") is True
            assert kimenet.nyom() is k.nyom
            k.nyitva = False
            assert kimenet.elkap("késői eredmény") is False          # lezárt kör: Telegramra megy
            assert kimenet.nyom() is None
        finally:
            kimenet.AKTUALIS.reset(tok)
        assert kimenet.elkap("Telegram-kör") is False
    _run(kor())
    assert [t["html"] for t in k.tetelek] == ["<b>FELDWEBEL</b>\n\nszia"]


import server  # noqa: E402


@pytest.fixture
def valodi_agy(db, monkeypatch):
    """A szerver valódi `_asszisztens_kor`-a, a teszt-DB-n, Telegram nélkül (a push a hálózat előtt hal el)."""
    monkeypatch.setattr(server, "get_db", db)
    monkeypatch.setattr(server, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(server, "TELEGRAM_CHAT_ID", "")              # a valódi _telegram_push nem érne ki
    monkeypatch.setattr(server, "FELDWEBEL_ENABLED", False)
    monkeypatch.setattr(server, "PYRAMID_ENABLED", False)
    telegramra = []
    eredeti = server._telegram_push

    async def figyelt_push(szoveg, *a, **kw):
        await eredeti(szoveg)
        if kimenet.AKTUALIS.get() is None or not kimenet.AKTUALIS.get().nyitva:
            telegramra.append(szoveg)
    monkeypatch.setattr(server, "_telegram_push", figyelt_push)
    app, _ = _app(db, asszisztens=lambda s, k, c: server._asszisztens_kor(s, k, c))
    return app, telegramra


def test_chat_faber_szandek_mockolt_llmmel(valodi_agy, db, monkeypatch):
    app, telegramra = valodi_agy
    import llm_kapu
    hivasok = []

    async def strukturalt(system, szoveg, sema, **kw):
        hivasok.append(szoveg)
        return sema(szandek="jovahagy", repo=None, kerelem=3), None
    monkeypatch.setattr(llm_kapu, "elerheto", lambda: True)
    monkeypatch.setattr(llm_kapu, "strukturalt", strukturalt)
    j = _run(_kor(app, "a 3-ast élesítsd"))
    assert j["allapot"] == "kesz", j
    assert hivasok == ["a 3-ast élesítsd"]
    assert [(v["ki"], v["szoveg"]) for v in j["valaszok"]] == [("feldwebel", faber_relay.NYUGTA["jovahagy"])]
    assert j["faber"] == {"szandek": "jovahagy", "repo": None, "kerelem": 3}
    assert telegramra == []                                          # a nyugta a webre ment, nem a Telegramra
    c = db()
    sor = c.execute("SELECT * FROM messages WHERE recipient = 'faber'").fetchone()
    c.close()
    t = faber_relay.ellenoriz(faber_relay.kulcs(TOKEN), sor["message"])
    assert t["tipus"] == "szandek" and json.loads(t["adat"])["kerelem"] == 3
    # a Feldwebel-ablak előzménye a Kommandant szavát mutatja, „→ Faber” jellel
    c = db()
    fw = ui.tortenet(c, "feldwebel", faber_relay.kulcs(TOKEN))["tetelek"]
    c.close()
    assert fw[-1]["szoveg"] == "a 3-ast élesítsd" and fw[-1]["faber"]["szandek"] == "jovahagy"


def test_chat_faber_ablak_kivansag_ha_nem_ismer_fel(valodi_agy, db, monkeypatch):
    app, telegramra = valodi_agy
    import llm_kapu

    async def strukturalt(system, szoveg, sema, **kw):
        return sema(szandek="egyeb"), None
    monkeypatch.setattr(llm_kapu, "elerheto", lambda: True)
    monkeypatch.setattr(llm_kapu, "strukturalt", strukturalt)
    j = _run(_kor(app, "a Studio tudjon feliratot égetni", ugynok="faber"))
    assert j["allapot"] == "kesz" and j["faber"]["szandek"] == "kivansag" and j["faber"]["repo"] == "studio"
    assert j["valaszok"][0]["szoveg"] == faber_relay.NYUGTA["kivansag"]
    c = db()
    sor = c.execute("SELECT message FROM messages WHERE recipient = 'faber'").fetchone()
    c.close()
    adat = json.loads(faber_relay.ellenoriz(faber_relay.kulcs(TOKEN), sor["message"])["adat"])
    assert adat["szoveg"] == "a Studio tudjon feliratot égetni"
    assert telegramra == []


def test_chat_feldwebel_valasz_modellel_es_eszkozzel(valodi_agy, db, monkeypatch):
    app, telegramra = valodi_agy
    monkeypatch.setattr(server, "FELDWEBEL_ENABLED", True)

    async def nincs_szandek(text, **kw):
        return None
    monkeypatch.setattr(server.faber_relay, "szandek", nincs_szandek)
    import feldwebel.commands as fc
    import feldwebel.responder as fr

    async def nem_parancs(text, chat_id):
        return False

    async def respond(text, chat_id, agent_id="deepseek"):
        e = kimenet.eszkoz_indul("web_search", {"query": "forint árfolyam"})
        kimenet.eszkoz_kesz(e, "3 találat")
        kimenet.modell({"model": "claude-sonnet-5-5", "provider": "anthropic"}, ugynok="feldwebel")
        await server._telegram_push("<b>FELDWEBEL</b>\n\nA forint ma <b>gyengült</b>.")
        return "A forint ma gyengült."
    monkeypatch.setattr(fc, "handle_command", nem_parancs)
    monkeypatch.setattr(fr, "respond", respond)
    j = _run(_kor(app, "mi a helyzet a forinttal?"))
    assert j["allapot"] == "kesz", j
    assert [(v["ki"], v["szoveg"]) for v in j["valaszok"]] == [("feldwebel", "A forint ma **gyengült**.")]
    assert j["modell"] == "claude-sonnet-5-5" and j["szolgaltato"] == "anthropic"
    assert j["eszkozok"][0]["nev"] == "web_search" and j["eszkozok"][0]["allapot"] == "kesz"
    assert telegramra == []
    c = db()
    sor = c.execute("SELECT subject FROM messages WHERE sender = 'kommandant'").fetchone()
    c.close()
    assert sor["subject"] == "Web: mi a helyzet a forinttal?"


def test_telegram_kor_valtozatlanul_telegramra_megy(valodi_agy, db, monkeypatch):
    _, telegramra = valodi_agy

    async def szandek(text, **kw):
        return {"szandek": "kivansag", "szoveg": text, "repo": None, "kerelem": None}
    monkeypatch.setattr(server.faber_relay, "szandek", szandek)
    _run(server._handle_telegram_message("Faber, tanuld meg X-et", "42"))
    assert telegramra == [faber_relay.NYUGTA["kivansag"]]


def test_store_bridge_reply_targy_modellel_es_eszkozokkel(db):
    import feldwebel.responder as fr

    class Ctx:
        get_db = staticmethod(db)
    fr._store_bridge_reply(Ctx, "kérdés", "válasz", "deepseek", modell="claude-sonnet-5-5",
                           eszkozok=["web_search", "web_search", "echolot_query"])
    fr._store_bridge_reply(Ctx, "kérdés", "válasz", "deepseek")
    c = db()
    targyak = [r["subject"] for r in c.execute("SELECT subject FROM messages ORDER BY id")]
    c.close()
    assert targyak == ["Re: Feldwebel válasz · modell=claude-sonnet-5-5 · eszközök=web_search,echolot_query",
                       "Re: Feldwebel válasz"]
    assert ui._subject_nyom(targyak[0]) == ("claude-sonnet-5-5", ["web_search", "echolot_query"])


def test_tortenet_lapozas_vegig_es_kurzor(db):
    c = db()
    for i in range(30):
        c.execute("INSERT INTO messages (timestamp, sender, recipient, subject, message) VALUES (datetime('now'),"
                  "'kommandant','bridge',?,?)", (f"Telegram: {i}", str(i)))
        for _ in range(5):                                   # zaj más ablakokból
            c.execute("INSERT INTO messages (timestamp, sender, recipient, subject, message) VALUES (datetime('now'),"
                      "'cli-claus','web-claus','x','zaj')")
    c.commit()
    elso = ui.tortenet(c, "feldwebel", None, limit=20, adag=7)
    assert [t["szoveg"] for t in elso["tetelek"]] == [str(i) for i in range(10, 30)] and elso["tovabb"]
    masodik = ui.tortenet(c, "feldwebel", None, elotte=elso["legkisebb_id"], limit=20, adag=7)
    assert [t["szoveg"] for t in masodik["tetelek"]] == [str(i) for i in range(10)] and not masodik["tovabb"]
    assert ui.tortenet(c, "faber", None, adag=7) == {"ablak": "faber", "tetelek": [], "tovabb": False,
                                                    "legkisebb_id": 1}
    c.close()
