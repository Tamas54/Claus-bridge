"""X/TWITTER-POSZT (twitter_poszt.py) — tervezet → a Kommandant jóváhagyása → poszt. Hálózat NINCS: az X API
hamisítvány (`httpx.MockTransport`), a kulcsok hamisak.

1. Az OAuth 1.0a aláírás formája (az X dokumentációjának mintáján, bitre) és a JSON-törzs nincs aláírva.
2. A hossz az X szerint: link = 23, ékezetes betű = 1, emoji = 2; 280 fölött kimondott hiba.
3. Tervezet → pending sor (`kind='tweet'`, a régi sémájú táblán is: a CHECK átépül, a sorok megmaradnak).
4. Jóváhagyás → egy POST /2/tweets, az URL vissza, az eredmény a Feldwebel-szálba; elvetés → nincs POST.
5. A Telegram egyszavas „igen”-je nem posztol; az MCP `action_draft_decide` sem; a kifejezett mondat igen.
6. A feladat-ügynök csak tervezetet ír; posztoló eszköz senkinek nincs.
7. Napi plafon; dupla kattintás = egy poszt; bukásnál függő marad, a hibával.
8. A kulcs nem szivárog (válasz, napló, hibaszöveg).
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

import twitter_poszt as T

KULCSOK = {"TWITTER_API_KEY": "kulcs-AAAA1111", "TWITTER_API_SECRET": "titok-BBBB2222",
           "TWITTER_ACCESS_TOKEN": "123-token-CCCC3333", "TWITTER_ACCESS_TOKEN_SECRET": "tokentitok-DDDD4444",
           "TWITTER_BEARER_TOKEN": "bearer-EEEE5555"}
SZOVEG = "Ma az MNB 6,25 százalékon hagyta az alapkamatot. #MNB"


def _run(c):
    return asyncio.run(c)


@pytest.fixture
def kulcs(monkeypatch):
    for k, v in KULCSOK.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TWITTER_NAPI_MAX", raising=False)


@pytest.fixture
def x_api(monkeypatch):
    """Hamis X API: minden kérés naplózva; a válasz a `valasz` listából (sorban), különben 201."""
    allapot = {"keresek": [], "valasz": []}

    def kezelo(req: httpx.Request) -> httpx.Response:
        allapot["keresek"].append(req)
        if allapot["valasz"]:
            st, adat, fej = allapot["valasz"].pop(0)
            return httpx.Response(st, json=adat, headers=fej or {})
        if req.url.path == "/2/users/me":
            return httpx.Response(200, json={"data": {"id": "77", "username": "proba", "name": "Próba Fiók"}})
        n = len([r for r in allapot["keresek"] if r.method == "POST"])
        return httpx.Response(201, json={"data": {"id": f"90000{n}", "text": json.loads(req.content)["text"]}})

    async def keres(kliens, method, url, **kw):
        async with httpx.AsyncClient(transport=httpx.MockTransport(kezelo)) as c:
            return await c.request(method, url, **kw)
    monkeypatch.setattr(T, "_keres", keres)
    return allapot


def _postok(x):
    return [r for r in x["keresek"] if r.method == "POST"]


def _sor(get_db, did):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM action_drafts WHERE id = ?", (did,)).fetchone()
        return {**dict(r), "payload": json.loads(r["payload_json"])}
    finally:
        conn.close()


def _tervezet(get_db, szoveg=SZOVEG, **kw):
    kw.setdefault("forras", "feldwebel")
    kw.setdefault("source_message_id", "feldwebel:42")
    kw.setdefault("chat_id", "42")
    out = T.tervezet(get_db, szoveg, **kw)
    assert out.get("status") == "jovahagyasra_var", out
    return out


# ── 1. OAuth 1.0a ─────────────────────────────────────────────────────────

def test_az_alairas_az_x_dokumentacio_mintajara_bitre_egyezik():
    """developer.x.com „Creating a signature” minta: a várt aláírás hCtSmYh+iHYCEqBWrE7C7hYmtUk=."""
    from oauthlib.oauth1 import Client
    c = Client("xvz1evFS4wEEPTGEFPHBog", client_secret="kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
               resource_owner_key="370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
               resource_owner_secret="LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
               nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp="1318622958")
    _, fej, _ = c.sign("https://api.twitter.com/1.1/statuses/update.json?include_entities=true", http_method="POST",
                       body="status=Hello%20Ladies%20%2B%20Gentlemen%2C%20a%20signed%20OAuth%20request%21",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in fej["Authorization"]


def test_a_fejlec_formaja_es_a_json_torzs_nincs_alairva(kulcs):
    f = T.fejlec("POST", T.TWEET_URL, nonce="n0nce", timestamp="1700000000")
    assert f.startswith("OAuth ")
    mezok = dict(p.split("=", 1) for p in f[6:].split(", "))
    assert set(mezok) == {"oauth_nonce", "oauth_timestamp", "oauth_version", "oauth_signature_method",
                          "oauth_consumer_key", "oauth_token", "oauth_signature"}
    assert mezok["oauth_signature_method"] == '"HMAC-SHA1"' and mezok["oauth_version"] == '"1.0"'
    assert mezok["oauth_consumer_key"] == f'"{KULCSOK["TWITTER_API_KEY"]}"'
    assert mezok["oauth_token"] == f'"{KULCSOK["TWITTER_ACCESS_TOKEN"]}"'
    # determinisztikus (azonos nonce + idő → azonos aláírás), és a metódus / URL része az aláírásnak
    assert f == T.fejlec("POST", T.TWEET_URL, nonce="n0nce", timestamp="1700000000")
    assert f != T.fejlec("GET", T.TWEET_URL, nonce="n0nce", timestamp="1700000000")
    # a titkok maguk nincsenek a fejlécben
    assert KULCSOK["TWITTER_API_SECRET"] not in f and KULCSOK["TWITTER_ACCESS_TOKEN_SECRET"] not in f


def test_kulcs_nelkul_nem_posztol(x_api, monkeypatch):
    for k in T.KULCS_NEVEK:
        monkeypatch.delenv(k, raising=False)
    r = _run(T.posztol("Szia"))
    assert "nincs X-kulcs" in r["error"] and x_api["keresek"] == []


# ── 2. Hossz ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("szoveg,elvart", [
    ("Teszt. #ClausBridge", 19),
    ("árvíztűrő tükörfúrógép", 22),                                   # ő, ű is 1
    ("lásd https://example.com/" + "a" * 200, 5 + 23),               # akármilyen hosszú link: 23
    ("lásd https://example.com/x.", 5 + 23 + 1),                      # a záró pont nem a link része
    ("👍", 2), ("👨‍👩‍👧", 2), ("🇭🇺", 2), ("日本", 4),
    ("echolot.news", 23),                                             # séma nélküli domaint is linkké teszi az X
])
def test_hossz_az_x_szerint(szoveg, elvart):
    assert T.hossz(szoveg) == elvart


def test_280_fole_kimondott_hiba_es_a_link_23():
    assert T.ellenoriz("a" * 280) is None
    h = T.ellenoriz("a" * 281)
    assert "281 karakter" in h and "1 karakterrel" in h
    # 256 + új sor + link (23) = 280 → még mehet
    assert T.ellenoriz(T.osszerak("a" * 256, "https://echolot.news/" + "x" * 300)) is None
    assert T.ellenoriz(T.osszerak("a" * 257, "https://echolot.news/x")) is not None
    assert T.ellenoriz("   ") == "üres a tweet szövege"


# ── 3. Tervezet ───────────────────────────────────────────────────────────

def test_tervezet_pending_sor_a_regi_semaju_tablan_is_es_a_regi_sorok_megmaradnak(get_db, x_api):
    conn = get_db()
    conn.execute("INSERT INTO action_drafts (kind, title, payload_json, status, created_at) VALUES "
                 "('calendar', 'Fogorvos', '{}', 'pending', '2026-11-01T00:00:00+00:00')")
    conn.commit()
    assert "'tweet'" not in conn.execute("SELECT sql FROM sqlite_master WHERE name = 'action_drafts'").fetchone()[0]
    conn.close()
    out = _tervezet(get_db, SZOVEG, link="https://www.mnb.hu/kozlemeny")
    assert out["posztolva"] is False and out["szoveg"] == SZOVEG + "\nhttps://www.mnb.hu/kozlemeny"
    assert out["karakter"] == f"{len(SZOVEG) + 1 + 23}/280" and "kabin munkapadján" in out["teendo"]
    s = _sor(get_db, out["draft_id"])
    assert s["kind"] == "tweet" and s["status"] == "pending" and s["source_message_id"] == "feldwebel:42"
    assert s["payload"]["szoveg"] == out["szoveg"] and s["payload"]["chat_id"] == "42"
    regi = _sor(get_db, 1)
    assert regi["title"] == "Fogorvos" and regi["kind"] == "calendar" and out["draft_id"] == 2
    assert x_api["keresek"] == []                                    # a tervezet NEM hív X-et
    conn = get_db()
    with pytest.raises(Exception):                                   # a CHECK továbbra is él
        conn.execute("INSERT INTO action_drafts (kind, title, created_at) VALUES ('rossz', 'x', 'y')")
    conn.close()
    assert T.schema_biztosit(get_db()) is False                      # másodszorra már semmi


def test_tul_hosszu_vagy_rossz_link_nem_lesz_tervezet(get_db):
    assert "túl hosszú" in T.tervezet(get_db, "a" * 300, forras="f", source_message_id="f")["error"]
    assert "http" in T.tervezet(get_db, "jó", "mnb.hu", forras="f", source_message_id="f")["error"]
    assert T.fuggok(get_db) == []


# ── 4. Jóváhagyás → POST; elvetés → nincs POST ────────────────────────────

def test_jovahagyas_posztol_es_az_url_a_feldwebel_szalba_megy(get_db, kulcs, x_api):
    did = _tervezet(get_db)["draft_id"]
    ertesitve = []

    async def ertesit(chat_id, szoveg):
        ertesitve.append((chat_id, szoveg))
    r = _run(T.dont(get_db, did, "approve", ertesit=ertesit))
    p = _postok(x_api)
    assert len(p) == 1 and str(p[0].url) == T.TWEET_URL
    assert json.loads(p[0].content) == {"text": SZOVEG}
    assert p[0].headers["authorization"].startswith("OAuth ") and "oauth_signature=" in p[0].headers["authorization"]
    assert p[0].headers["content-type"] == "application/json"
    assert r["posztolva"] is True and r["status"] == "created" and r["url"] == "https://x.com/i/status/900001"
    s = _sor(get_db, did)
    assert s["status"] == "created" and s["payload"]["url"] == r["url"] and s["payload"]["tweet_id"] == "900001"
    assert ertesitve == [("42", r["uzenet"])] and r["url"] in r["uzenet"] and SZOVEG in r["uzenet"]


def test_szalba_a_feldwebel_elozmenybe_es_a_kabin_ablakaba(get_db):
    from feldwebel.history import ensure_history_table
    ensure_history_table(get_db)
    T.szalba(get_db, "42", "🐦 Kiposztoltam az X-re: https://x.com/i/status/1")
    conn = get_db()
    h = conn.execute("SELECT chat_id, role, content FROM telegram_history").fetchall()
    m = conn.execute("SELECT sender, recipient, subject, message FROM messages").fetchall()
    conn.close()
    assert [tuple(x) for x in h] == [("42", "assistant", "🐦 Kiposztoltam az X-re: https://x.com/i/status/1")]
    import ui_kabin
    ablak = ui_kabin._osztalyoz({**dict(m[0]), "id": 1, "timestamp": "t"}, {})
    assert ablak[0][0] == "feldwebel" and "x.com/i/status/1" in ablak[0][1]["szoveg"]


def test_elvetes_nem_posztol(get_db, kulcs, x_api):
    did = _tervezet(get_db)["draft_id"]
    r = _run(T.dont(get_db, did, "reject"))
    assert r == {"draft_id": did, "status": "rejected", "posztolva": False} and x_api["keresek"] == []
    assert _sor(get_db, did)["status"] == "rejected"
    assert "már nem vár döntésre" in _run(T.dont(get_db, did, "approve"))["error"] and x_api["keresek"] == []


def test_dupla_kattintas_egy_poszt(get_db, kulcs, x_api):
    did = _tervezet(get_db)["draft_id"]

    async def ketszer():
        return await asyncio.gather(T.dont(get_db, did, "approve"), T.dont(get_db, did, "approve"))
    a, b = _run(ketszer())
    assert len(_postok(x_api)) == 1
    assert sorted([bool(a.get("posztolva")), bool(b.get("posztolva"))]) == [False, True]


def test_bukasnal_fuggo_marad_a_hibaval_es_a_kabin_mutatja(get_db, kulcs, x_api):
    did = _tervezet(get_db)["draft_id"]
    x_api["valasz"].append((402, {"title": "CreditsDepleted", "detail": "Your enrolled account does not have any "
                                  "credits to fulfill this request.", "type": "https://api.twitter.com/2/problems/credits"},
                            None))
    ertesitve = []

    async def ertesit(chat_id, szoveg):
        ertesitve.append(szoveg)
    r = _run(T.dont(get_db, did, "approve", ertesit=ertesit))
    assert r["posztolva"] is False and r["status"] == "pending" and "elfogyott az X API-kredit" in r["error"]
    assert "Nem ment ki" in ertesitve[0] and "újra jóváhagyható" in ertesitve[0]
    s = _sor(get_db, did)
    assert s["status"] == "pending" and "kredit" in s["payload"]["utolso_hiba"] and s["decided_at"] == ""
    import ui_kabin
    d = ui_kabin._draftok(get_db())
    assert d[0]["fajta"] == "tweet" and d[0]["szoveg"] == SZOVEG and d[0]["karakter"] == len(SZOVEG)
    assert d[0]["max"] == 280 and "kredit" in d[0]["hiba"]
    # újra jóváhagyva most kimegy
    assert _run(T.dont(get_db, did, "approve"))["posztolva"] is True


@pytest.mark.parametrize("st,adat,fej,resz", [
    (401, {"title": "Unauthorized", "detail": "Unauthorized"}, None, "hitelesítést (401)"),
    (403, {"title": "Forbidden", "detail": "You are not allowed to create a Tweet with duplicate content."}, None,
     "duplicate content"),
    (403, {"title": "Forbidden", "detail": "Your client app is not configured with the appropriate oauth1 app "
                                           "permissions for this endpoint."}, None, "nincs írásjoga"),
    (403, {"title": "CreditsDepleted", "detail": "no credits"}, None, "elfogyott az X API-kredit (403)"),
    (429, {"title": "Too Many Requests"}, {"x-rate-limit-reset": "1795000000"}, "túl sok kérés (429)"),
    (503, {}, None, "később újra próbálható"),
])
def test_x_hibak_kimondva(kulcs, x_api, st, adat, fej, resz):
    x_api["valasz"].append((st, adat, fej))
    r = _run(T.posztol(SZOVEG))
    assert r["status"] == st and resz in r["error"]
    if st == 429:
        assert "-kor nyílik újra" in r["error"]


def test_users_me_csak_olvas(kulcs, x_api):
    r = _run(T.en())
    assert r == {"ok": True, "id": "77", "username": "proba", "name": "Próba Fiók", "url": "https://x.com/proba"}
    assert [q.method for q in x_api["keresek"]] == ["GET"] and _postok(x_api) == []


# ── 5. A Telegram „igen”-je nem posztol; az MCP-eszköz sem; a kifejezett mondat igen ──

@pytest.fixture
def fw(monkeypatch, get_db):
    import feldwebel
    from feldwebel import BridgeContext, responder
    from feldwebel.history import ensure_history_table
    ensure_history_table(get_db)
    naplo = {"push": [], "llm": 0}

    async def push(s, *a, **k):
        naplo["push"].append(s)

    async def kapu(payload, purpose=""):
        naplo["llm"] += 1
        return {"model": "claude-haiku-5-5", "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Rendben."}}]}, None, []

    async def kulso():
        return []
    monkeypatch.setattr(feldwebel, "_ctx", BridgeContext(telegram_push=push, get_db=get_db, llm_chat=kapu))
    monkeypatch.setattr(responder, "_kulso_eszkozok", kulso)
    monkeypatch.setattr(responder, "_rag_top", lambda q, n=3: [])
    return feldwebel._ctx, naplo, responder


def _feldwebel_tervezet(ctx, responder, szoveg=SZOVEG):
    out = json.loads(_run(responder._execute_tool("tweet_tervezet", {"szoveg": szoveg}, ctx,
                                                  kor={"chat_id": "42", "text": "írj egy tweetet"})))
    assert out["status"] == "jovahagyasra_var", out
    return out


def test_a_feldwebel_eszkoze_csak_tervezet(fw, get_db, kulcs, x_api):
    ctx, _, responder = fw
    out = _feldwebel_tervezet(ctx, responder)
    assert out["posztolva"] is False and x_api["keresek"] == [] and _sor(get_db, out["draft_id"])["status"] == "pending"
    assert "tweet_tervezet" in responder.KODKAPU_TOOLS
    from feldwebel import eszkoz_ut
    assert eszkoz_ut.kategoria("tweet_tervezet") == "kozzetetel"
    assert "kozzetetel" in eszkoz_ut.szandek("írj egy tweetet a mai MNB-döntésről")


@pytest.mark.parametrize("igen", ["igen", "mehet", "Igen.", "👍", "ok, mehet"])
def test_egyszavas_igen_nem_posztol(fw, get_db, kulcs, x_api, igen):
    ctx, naplo, responder = fw
    did = _feldwebel_tervezet(ctx, responder)["draft_id"]
    from feldwebel import jovahagyas
    from feldwebel.history import add_message
    add_message("42", "assistant", f"Itt a tervezet: {SZOVEG} Kiposztoljam? (igen / mehet)")
    assert _run(T.kezel(ctx, igen, "42")) is None
    assert _run(jovahagyas.kezel(ctx, igen, "42")) is None          # a naptár egy szava csak kind='calendar'
    _run(responder.respond(igen, "42"))                               # a teljes kör: a modellhez megy, posztolás nincs
    assert naplo["llm"] >= 1 and x_api["keresek"] == [] and _sor(get_db, did)["status"] == "pending"


def test_az_mcp_eszkoz_nem_hagy_jova_tweetet(get_db, kulcs, x_api, fake_app):
    from plugins import action_extract
    action_extract.register_tools(fake_app, {"get_db": get_db, "capture_state": {}})
    did = _tervezet(get_db)["draft_id"]
    r = json.loads(_run(fake_app.tools["action_draft_decide"](draft_id=did, decision="approve", caller="web-claus")))
    assert "kabin munkapadján" in r["error"] and x_api["keresek"] == [] and _sor(get_db, did)["status"] == "pending"
    r = json.loads(_run(fake_app.tools["action_draft_decide"](draft_id=did, decision="reject", caller="web-claus")))
    assert r["status"] == "rejected" and x_api["keresek"] == []


@pytest.mark.parametrize("mondat", [
    "posztold ki a tervezetet: Ma az MNB",
    "Posztold ki a tervezetet, ma az MNB 6,25 százalékon",
    "Küldd ki a tweetet – Ma az mnb",
])
def test_kifejezett_mondat_posztol(fw, get_db, kulcs, x_api, mondat):
    ctx, naplo, responder = fw
    did = _feldwebel_tervezet(ctx, responder)["draft_id"]
    _feldwebel_tervezet(ctx, responder, "Holnap jön a KSH inflációs adata.")
    valasz = _run(responder.respond(mondat, "42"))
    assert len(_postok(x_api)) == 1 and json.loads(_postok(x_api)[0].content)["text"] == SZOVEG
    assert "Kiposztoltam az X-re: https://x.com/i/status/900001" in valasz and naplo["llm"] == 0
    assert _sor(get_db, did)["status"] == "created" and _sor(get_db, did)["payload"]["jovahagyta"] == "kifejezett mondat"
    assert "FELDWEBEL" in naplo["push"][-1]


@pytest.mark.parametrize("mondat,resz", [
    ("posztold ki a tervezetet: Ma az", "legalább 3 szót"),
    ("posztold ki a tervezetet: Holnap lesz eső", "egyik függő tervezet sem így kezdődik"),
    ("posztold ki a tervezetet", "mondd meg, melyiket"),
    ("posztold ki a tervezetet: Ma az MNB", "2 tervezet is így kezdődik"),
])
def test_kifejezett_mondat_ha_nem_egyertelmu_nem_posztol(fw, get_db, kulcs, x_api, mondat, resz):
    ctx, naplo, responder = fw
    _feldwebel_tervezet(ctx, responder)
    if "2 tervezet" in resz:
        _feldwebel_tervezet(ctx, responder, SZOVEG + " Részletek később.")
    valasz = _run(responder.respond(mondat, "42"))
    assert resz in valasz and x_api["keresek"] == [] and naplo["llm"] == 0


@pytest.mark.parametrize("szoveg", ["ne posztold ki a tervezetet: Ma az MNB", "posztold ki ezt", "mehet",
                                    "kiposztoltad a tervezetet?", "Írj egy tweetet a kamatdöntésről"])
def test_nem_kifejezett_mondat(szoveg):
    assert T.kifejezett(szoveg) is None


# ── 6. Ügynökök: csak tervezet; posztoló eszköz senkinek ─────────────────

def test_a_feladat_ugynok_csak_tervezetet_ir(get_db, kulcs, x_api):
    import feladat_ugynok as F
    assert F.engedett("tweet_tervezet", {}) is None and F.engedett("tweet_tervezet", {}, sf=False) is None
    for nev in ("post_tweet", "tweet_posztol"):
        assert nev in F.TILTOTT and nev not in F.ENGEDETT and F.engedett(nev, {})
    r = F.tweet_javaslat(get_db, 7, "Kamatfigyelő", {"szoveg": SZOVEG})
    s = _sor(get_db, r["draft_id"])
    assert s["status"] == "pending" and s["source_message_id"] == "ugynok:7" and s["payload"]["forras"] == "ügynök: Kamatfigyelő"
    assert "kabin munkapadján" in r["teendo"] and x_api["keresek"] == []
    # a jelentés a teljes szöveggel, és kimondja, hogy nem ment ki
    j = F.jelentes("Kamatfigyelő", {"allapot": "kesz", "osszegzes": "kész", "javaslatok": [
        {"draft_id": r["draft_id"], "javaslat": SZOVEG, "fajta": "tweet", "karakter": r["karakter"]}],
        "koltes_usd": 0.01, "keret_usd": 0.5, "modell": "claude-sonnet-5-5"}, None)
    assert "🐦 tweet-tervezet" in j and "nem ment ki" in j and "Ma az MNB" in j


def test_az_ugynok_vegrehajtoja_tervezetet_ir_es_nyomot_hagy(get_db, kulcs, x_api):
    import feladat_ugynok as F

    class Forras:
        async def feldwebel_eszkoz(self, *a, **k):
            raise AssertionError("a tweet nem a Feldwebel végrehajtóján megy")
    nyom = F.Nyom()
    v = F.vegrehajto(Forras(), nyom, get_db=get_db, szamla=None,
                     u={"rid": 7, "nev": "Kamatfigyelő", "modell": "sonnet", "tudasbazis_erintett": True,
                        "vitapartnerek": [], "utasithat": []})
    e = json.loads(_run(v("tweet_tervezet", {"szoveg": SZOVEG})))
    assert e["status"] == "jovahagyasra_var" and x_api["keresek"] == []
    assert nyom.javaslatok == [{"draft_id": e["draft_id"], "javaslat": SZOVEG, "fajta": "tweet",
                                "karakter": e["karakter"]}]
    assert "nem engedett" in _run(v("post_tweet", {"szoveg": SZOVEG})) and len(nyom.javaslatok) == 1
    # a modell eszközlistájában ott a tervezet, posztoló nincs
    from feldwebel.responder import TOOLS
    defs = [t for t in TOOLS if t["function"]["name"] in (F.FELDWEBEL_ENGEDETT | F.JAVASLAT)]
    assert "tweet_tervezet" in {t["function"]["name"] for t in defs}


def test_sf_ugynok_se_tervezetet_se_posztot():
    import server
    assert {"tweet_tervezet", "post_tweet", "tweet_posztol"} <= server.SUBAGENT_TILTOTT
    v = json.loads(_run(server._dispatch_subagent_tool("tweet_tervezet", {"szoveg": "x"})))
    assert v["error"] == "tool_not_allowed"


def test_posztolo_eszkoz_sehol_nincs():
    from feldwebel import responder
    nevek = {t["function"]["name"] for t in responder.TOOLS}
    assert "tweet_tervezet" in nevek and not nevek & {"post_tweet", "tweet_posztol"}


# ── 7. Napi plafon ────────────────────────────────────────────────────────

def test_napi_plafon(get_db, kulcs, x_api, monkeypatch):
    monkeypatch.setenv("TWITTER_NAPI_MAX", "2")
    ids = [_tervezet(get_db, f"{i}. hír a mai napról, külön szöveggel")["draft_id"] for i in range(3)]
    assert _run(T.dont(get_db, ids[0], "approve"))["posztolva"] and _run(T.dont(get_db, ids[1], "approve"))["posztolva"]
    r = _run(T.dont(get_db, ids[2], "approve"))
    assert "napi plafont" in r["error"] and "TWITTER_NAPI_MAX=2" in r["error"] and r["status"] == "pending"
    assert len(_postok(x_api)) == 2 and _sor(get_db, ids[2])["status"] == "pending"
    # a tegnapi posztok nem számítanak bele
    conn = get_db()
    conn.execute("UPDATE action_drafts SET decided_at = '2020-01-01T00:00:00+00:00' WHERE id IN (?, ?)", ids[:2])
    conn.commit()
    conn.close()
    assert _run(T.dont(get_db, ids[2], "approve"))["posztolva"] is True


# ── 8. A kulcs nem szivárog ───────────────────────────────────────────────

def test_a_kulcs_nem_szivarog(get_db, kulcs, x_api, caplog):
    caplog.set_level(logging.DEBUG)
    did = _tervezet(get_db)["draft_id"]
    # az X (vagy egy proxy) visszaküldené a kérés fejlécét és a tokent a hibában
    x_api["valasz"].append((401, {"title": "Unauthorized", "detail": f"bad token {KULCSOK['TWITTER_ACCESS_TOKEN']} "
                                  f'Authorization: OAuth oauth_consumer_key="{KULCSOK["TWITTER_API_KEY"]}", '
                                  f'oauth_signature="abc%3D" {KULCSOK["TWITTER_BEARER_TOKEN"]}'}, None))
    ertesitve = []

    async def ertesit(chat_id, szoveg):
        ertesitve.append(szoveg)
    r = _run(T.dont(get_db, did, "approve", ertesit=ertesit))
    kimenet = json.dumps(r, ensure_ascii=False) + " ".join(ertesitve) + caplog.text + json.dumps(_sor(get_db, did),
                                                                                                    ensure_ascii=False)
    kimenet += json.dumps(_run(T.en()), ensure_ascii=False)
    for v in KULCSOK.values():
        assert v not in kimenet
    assert "oauth_signature=\"abc" not in kimenet and "hitelesítést (401)" in r["error"]


def test_tisztit():
    import os
    os.environ.update(KULCSOK)
    try:
        s = T._tisztit(f"x {KULCSOK['TWITTER_API_SECRET']} y OAuth oauth_token=\"z\", oauth_signature=\"q\"")
        assert KULCSOK["TWITTER_API_SECRET"] not in s and "oauth_signature=\"q" not in s and s.startswith("x *** y")
    finally:
        for k in KULCSOK:
            os.environ.pop(k, None)


# ── A kabin-út (server): a tweet a twitter_poszt.dont-hoz megy, nem az MCP-eszközhöz ──

def test_a_kabin_gombja_a_twitter_poszt_dont_hoz_megy(get_db, kulcs, x_api, monkeypatch):
    import server
    monkeypatch.setattr(server, "get_db", get_db)
    elkuldve = []

    async def push(s):
        elkuldve.append(s)
    monkeypatch.setattr(server, "_telegram_push", push)

    async def tilos(*a, **k):
        raise AssertionError("a tweet nem az action_draft_decide-on megy")
    monkeypatch.setattr(server.mcp, "call_tool", tilos)
    did = _tervezet(get_db)["draft_id"]
    r = _run(server._ui_draft_dont(did, "approve"))
    assert r["posztolva"] is True and len(_postok(x_api)) == 1
    assert "Kiposztoltam az X-re" in elkuldve[0] and "FELDWEBEL" in elkuldve[0]
