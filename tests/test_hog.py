"""OPERATION HOG A BRIDGE-BŐL (hog.py) — X-poszt → Echolot hog_prepare → Sonnet ír → a Kommandant jóváhagyja → oldal +
tweet. Hálózat NINCS: az Echolot MCP, az X API és a Sonnet hamisítvány, a kulcsok hamisak.

1. Képernyőkép → a képelemzés kiolvassa a szöveget, a szerzőt, a @handle-t → a hog_prepare ezt kapja (a kezelésekkel,
   a követőszámmal, a szerkesztői kulccsal).
2. Jóváhagyás nélkül nincs publikálás és nincs tweet (az egyszavas igen, az MCP-eszköz sem hagy jóvá).
3. A sorrend: hog_publish → a válasz (kártyaképpel) → a link a saját válasz alá; a kifejezett mondat is ezt indítja.
4. Ha az X a választ 403-mal elutasítja → idézet (kimondva); mindig ott a „Válasz a telefonról” link.
5. Az elvetés semmit nem tesz ki.
6. A szerkesztői kulcs nem szivárog (válasz, payload, napló, értesítés).
+ a helyi [n]/URL-ellenőrzés és a javítókör, a posztkeresés (csak a szerző handle-je), az X-bővítés (válasz, idézet,
  média, követőszám, 403-szöveg), a közös napi plafon, a séma-átépítés.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

import hog as H
import twitter_poszt as T

SZERK = "szerk-TITOK-9876"
X_KULCSOK = {"TWITTER_API_KEY": "kulcs-AAAA1111", "TWITTER_API_SECRET": "titok-BBBB2222",
             "TWITTER_ACCESS_TOKEN": "123-token-CCCC3333", "TWITTER_ACCESS_TOKEN_SECRET": "tokentitok-DDDD4444"}
CEL_ID = "2124870000000000001"
OLDAL = "https://echolotnews.com/en/hog/nvidia-reinstates-jensen-huang-coverage"
KOMMENT = "200 reports in 12 languages in 3 days.\nUK press first, US press 5 h later.\nWhich angle reached your feed?"
IDEZET = "Nvidia reinstated Jensen Huang: 23 press regions covered it within 3 days. Which version did you see?"


def _run(c):
    return asyncio.run(c)


@pytest.fixture
def kulcs(monkeypatch):
    monkeypatch.setenv("ECHOLOT_EDITOR_KEY", SZERK)
    for k, v in X_KULCSOK.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HOG_LINK_KESES_S", "0")
    monkeypatch.delenv("TWITTER_NAPI_MAX", raising=False)


def _csomag(n=15):
    return {
        "status": "ok", "coverage": 0.8, "draft_id": "hog_2026-11-23_elon_9993", "lang": "en",
        "author": {"label": "Elon Musk @elonmusk"}, "author_kind": "person",
        "protagonist": {"qid": "Q305177", "label": "Jensen Huang"},
        "corpus": {"article_count": 200, "classified": 120, "regions": 23, "languages": ["en", "ru", "es"]},
        "angle_suggestion": "framing_gap", "writing_rules": {"lang": "en", "rules": [{"code": "cite_every_claim"}]},
        "comment_rules": {"template": {"lines": 4}}, "playbook": {"comment_allowed": True},
        "fact_material": {"quotes": []}, "facts": [{"kind": "what", "text": "x", "refs": [1]}],
        "sources": [{"n": i, "source": f"Lap {i}", "title": f"Nvidia reinstates Huang {i}", "lead": "lead",
                     "url": f"https://lap{i}.example/cikk-{i}", "region_label": "US press", "lang": "en"}
                    for i in range(1, n + 1)],
        "web_sources": [],
        "by_region": {"regional_us": {"articles": 27, "headlines": [{"title": "Nvidia reinstates Huang"}]}},
    }


def _iras(**felul):
    d = {"title": "Nvidia reinstates Jensen Huang: how 23 press regions covered it",
         "card_headline": "Huang back at Nvidia: 200 reports, 12 languages", "card_headline_ref": None,
         "analysis_text": ("Echolot counted 200 reports in 12 languages [1].\n\n"
                           "UK press carried the first report [2].\n\nUS outlets quoted the board [3]."),
         "facts": [f"{k}: line {i} [{i + 1}]" for i, k in enumerate(
             ["what", "who", "who", "where_when", "quote", "numbers", "positions", "timeline", "background",
              "reaction", "next", "coverage"])],
         "comment_text": KOMMENT, "quote_text": IDEZET, "exclude_refs": [], "slug": "",
         "komment_hu": "200 tudósítás 12 nyelven 3 nap alatt…", "megjegyzes_hu": "Lefedettségi szög."}
    d.update(felul)
    return d


@pytest.fixture
def sonnet():
    """Hamis Sonnet: a tervező és az író válasza célonként (sorban), a hívások naplózva."""
    allapot = {"hivasok": [], "terv": [], "iras": [], "javitas": []}

    async def chat(payload, purpose="", modell=None):
        allapot["hivasok"].append({"purpose": purpose, "modell": modell, "payload": payload})
        kulcs_ = purpose.split("/")[-1]
        sor = allapot[kulcs_]
        tartalom = sor.pop(0) if sor else json.dumps(
            {"lang": "en", "keywords": ["Nvidia", "Jensen Huang", "reinstates"], "protagonist_hint": "Jensen Huang",
             "anchors": [], "post_text": "The mistake has been corrected\n\nNVIDIA Board reinstates Jensen Huang."}
            if kulcs_ == "terv" else _iras())
        if isinstance(tartalom, dict):
            tartalom = json.dumps(tartalom)
        return {"choices": [{"message": {"content": tartalom}}], "koltes_usd": 0.05}, None, []
    allapot["chat"] = chat
    return allapot


@pytest.fixture
def echolot(monkeypatch):
    """Hamis Echolot MCP: a hívások SORRENDBEN naplózva (a közös `sorrend` listába is)."""
    allapot = {"hivasok": [], "search": [], "prepare": None, "publish": None, "sorrend": None}

    async def hiv(tool, args, timeout):
        allapot["hivasok"].append((tool, dict(args)))
        if allapot["sorrend"] is not None:
            allapot["sorrend"].append(f"echolot:{tool}")
        if tool == "search_social":
            return {"posts": allapot["search"]}
        if tool == "hog_prepare":
            return allapot["prepare"] or _csomag()
        if tool == "hog_publish":
            return allapot["publish"] or {
                "ok": True, "x_card_ready": True, "url": OLDAL, "og_image_url": "https://echolotnews.com/og/hog/x.png",
                "reply_text": f"Breakdown by source: {OLDAL}?utm_source=x&utm_medium=reply&utm_campaign=x",
                "quote_text": f"{args.get('quote_text')} {OLDAL}?utm_source=x&utm_medium=quote&utm_campaign=x",
                "quote_url": f"{OLDAL}?utm_source=x&utm_medium=quote&utm_campaign=x", "published_at": "2026-11-23"}
        if tool == "scrape_url":
            return {"title": "Elon Musk (@elonmusk) on X", "text": "The mistake has been corrected"}
        raise AssertionError(f"váratlan Echolot-eszköz: {tool}")
    monkeypatch.setattr(H, "ECHOLOT_HIV", hiv)

    async def letolt(url):
        if allapot["sorrend"] is not None:
            allapot["sorrend"].append("kep_letoltes")
        return b"\x89PNG\r\n\x1a\nkartya"
    monkeypatch.setattr(H, "LETOLT", letolt)
    return allapot


@pytest.fixture
def x_api(monkeypatch):
    """Hamis X API: minden kérés naplózva; a /2/tweets válasza a `valasz` listából (sorban), különben 201."""
    allapot = {"keresek": [], "valasz": [], "sorrend": None}

    def kezelo(req: httpx.Request) -> httpx.Response:
        allapot["keresek"].append(req)
        if allapot["sorrend"] is not None:
            allapot["sorrend"].append(f"x:{req.method}:{req.url.path}")
        if req.url.path == "/2/users/me":
            return httpx.Response(200, json={"data": {"id": "77", "username": "proba", "name": "Próba",
                                                      "public_metrics": {"followers_count": 312}}})
        if req.url.path == "/2/media/upload":
            return httpx.Response(200, json={"data": {"id": "555", "media_key": "3_555"}})
        if allapot["valasz"]:
            st, adat = allapot["valasz"].pop(0)
            return httpx.Response(st, json=adat)
        n = len(_tweetek(allapot))
        return httpx.Response(201, json={"data": {"id": f"90000{n}", "text": json.loads(req.content)["text"]}})

    async def keres(kliens, method, url, **kw):
        async with httpx.AsyncClient(transport=httpx.MockTransport(kezelo)) as c:
            return await c.request(method, url, **kw)
    monkeypatch.setattr(T, "_keres", keres)
    return allapot


def _tweetek(x):
    return [r for r in x["keresek"] if r.method == "POST" and r.url.path == "/2/tweets"]


def _torzs(r):
    return json.loads(r.content)


def _sor(get_db, did):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM action_drafts WHERE id = ?", (did,)).fetchone()
        return {**dict(r), "payload": json.loads(r["payload_json"])}
    finally:
        conn.close()


def _eszkozok(e):
    return [t for t, _ in e["hivasok"]]


def _futtat(get_db, sonnet, args=None, **kw):
    uzenetek = []

    async def ertesit(chat_id, s):
        uzenetek.append(s)
    args = args or {"poszt_szoveg": "The mistake has been corrected", "szerzo": "Elon Musk @elonmusk",
                    "poszt_url": f"https://x.com/elonmusk/status/{CEL_ID}"}
    out = _run(H.futtat(get_db, args, chat_id="42", ertesit=ertesit, chat=sonnet["chat"], **kw))
    return out, uzenetek


def _feltolt_kep(get_db):
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO uploads (filename, mime_type, content_text, content_base64, uploaded_by, "
                           "uploaded_at) VALUES ('foto.jpg', 'image/jpeg', 'Elon Musk @elonmusk The mistake…', "
                           "'aGVsbG8=', 'kommandant', '2026-11-23')")
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


@pytest.fixture
def uploads_tabla(get_db):
    conn = get_db()
    conn.execute("CREATE TABLE IF NOT EXISTS uploads (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL, "
                 "mime_type TEXT NOT NULL, content_text TEXT DEFAULT '', content_base64 TEXT DEFAULT '', "
                 "uploaded_by TEXT NOT NULL, uploaded_at TEXT NOT NULL)")
    conn.commit()
    conn.close()


# ── 1. Képernyőkép → szöveg → prepare ───────────────────────────────────────

def test_kepernyokep_szoveg_prepare(get_db, kulcs, sonnet, echolot, x_api, uploads_tabla):
    mid = _feltolt_kep(get_db)
    latott = []

    async def kepelemzo(b64, mime, prompt):
        latott.append((b64, mime, "VERBATIM" in prompt))
        return ('```json\n{"platform": "x", "szerzo": "Elon Musk", "handle": "@elonmusk", "szoveg": "The mistake has '
                'been corrected", "idezett": {"szerzo": "NVIDIA Newsroom", "handle": "@nvidianewsroom", "szoveg": '
                '"NVIDIA Board reinstates Jensen Huang as President, CEO and Board Director."}, "nyelv": "en"}\n```')
    echolot["search"] = [
        {"url": "https://x.com/MarioNawfal/status/2124878637698777395/photo/1", "text": "Elon: THE MISTAKE HAS BEEN "
         "CORRECTED"},
        {"url": f"https://x.com/elonmusk/status/{CEL_ID}", "text": "The mistake has been corrected"}]
    out, uz = _futtat(get_db, sonnet, {"melleklet_id": mid}, kepelemzo=kepelemzo)
    assert out["ok"], out
    assert latott == [("aGVsbG8=", "image/jpeg", True)]
    prep = dict(echolot["hivasok"])["hog_prepare"]
    assert prep["author_hint"] == "Elon Musk @elonmusk" and prep["editor_key"] == SZERK
    assert prep["post_url"] == f"https://x.com/elonmusk/status/{CEL_ID}"            # a keresés a SZERZŐ posztját adta
    assert prep["keywords"] == ["Nvidia", "Jensen Huang", "reinstates"] and prep["keywords_mode"] == "replace"
    assert prep["protagonist_hint"] == "Jensen Huang" and prep["own_follower_count"] == 312
    assert prep["platform"] == "x" and prep["mode"] == "comment" and prep["lang"] == "en"
    # a tervező a képből kiolvasott idézett posztot is látta
    assert "NVIDIA Board reinstates" in sonnet["hivasok"][0]["payload"]["messages"][1]["content"]
    assert all(h["modell"] == "claude-sonnet-5-5" for h in sonnet["hivasok"])        # Sonnet, nem a Feldwebel Haiku-ja
    s = _sor(get_db, out["hog_id"])
    assert s["kind"] == "hog" and s["status"] == "pending" and s["payload"]["mod"] == "valasz"
    assert s["payload"]["cel"]["tweet_id"] == CEL_ID and s["payload"]["koltes_usd"] == pytest.approx(0.10)
    assert "🐗 HOG-tervezet kész" in uz[0] and "SEMMI nem ment ki" in uz[0] and "$0.100" in uz[0]


def test_kepelemzes_nelkul_az_atirat_a_tartalek(get_db, uploads_tabla):
    mid = _feltolt_kep(get_db)
    d = _run(H.kep_olvas(get_db, mid, None))
    assert d["forras"] == "atirat" and d["handle"] == "elonmusk" and "mistake" in d["szoveg"]


def test_ha_nincs_meg_a_poszt_sajat_mod_es_kerdez(get_db, kulcs, sonnet, echolot, x_api):
    echolot["search"] = [{"url": "https://x.com/crypto_banter/status/2124880395581686094/video/1",
                          "text": "ELON MUSK: THE MISTAKE HAS BEEN CORRECTED"}]
    out, uz = _futtat(get_db, sonnet, {"poszt_szoveg": "The mistake has been corrected",
                                       "szerzo": "Elon Musk @elonmusk"})
    assert out["ok"] and out["mod"] == "sajat"
    assert dict(echolot["hivasok"])["hog_prepare"]["post_url"] == ""
    assert "SAJÁT POSZT" in uz[0] and "linkjét" in uz[0]
    # a Kommandant utólag elküldi a linket → válasz lesz belőle, újraírás nélkül
    r = H.cel_beallit(get_db, out["hog_id"], f"https://x.com/elonmusk/status/{CEL_ID}")
    p = _sor(get_db, out["hog_id"])["payload"]
    assert r["mod"] == "valasz" and p["mod"] == "valasz" and p["cel"]["tweet_id"] == CEL_ID
    assert len([h for h in sonnet["hivasok"] if h["purpose"] == "hog/iras"]) == 1


def test_posztkereses_csak_a_szerzo_handle_jet_fogadja_el(echolot):
    echolot["search"] = [{"url": "https://x.com/MarioNawfal/status/1", "text": "The mistake has been corrected"},
                         {"url": "https://x.com/nvidianewsroom/status/2124865060397019480", "text": "NVIDIA Board"}]
    assert _run(H.poszt_keres("elonmusk", "The mistake has been corrected")) is None
    echolot["search"].append({"url": "https://x.com/ElonMusk/status/123456", "text": "the mistake has been corrected"})
    assert _run(H.poszt_keres("@elonmusk", "The mistake has been corrected"))["tweet_id"] == "123456"


def test_link_szoveg_nelkul_scrape(get_db, kulcs, sonnet, echolot, x_api):
    out, _ = _futtat(get_db, sonnet, {"poszt_url": f"https://twitter.com/elonmusk/status/{CEL_ID}"})
    assert out["ok"] and "scrape_url" in _eszkozok(echolot) and "search_social" not in _eszkozok(echolot)


def test_kulcs_nelkul_nem_indul(get_db, monkeypatch):
    monkeypatch.delenv("ECHOLOT_EDITOR_KEY", raising=False)

    class Ctx:
        get_db = None
    r = _run(H.inditas(Ctx(), {"poszt_szoveg": "x"}, "42"))
    assert "ECHOLOT_EDITOR_KEY" in r["error"]


# ── 2. Jóváhagyás nélkül nincs publikálás és nincs tweet ─────────────────────

def test_jovahagyas_nelkul_nincs_publish_es_tweet(get_db, kulcs, sonnet, echolot, x_api):
    out, _ = _futtat(get_db, sonnet)
    assert "hog_publish" not in _eszkozok(echolot)
    assert _tweetek(x_api) == [] and all(r.method == "GET" for r in x_api["keresek"])      # csak a követőszám
    assert _sor(get_db, out["hog_id"])["status"] == "pending"


@pytest.mark.parametrize("igen", ["igen", "mehet", "Igen.", "👍"])
def test_egyszavas_igen_nem_hagy_jova(get_db, kulcs, sonnet, echolot, x_api, igen):
    out, _ = _futtat(get_db, sonnet)
    assert T.kifejezett(igen) is None
    assert _tweetek(x_api) == [] and "hog_publish" not in _eszkozok(echolot)
    assert _sor(get_db, out["hog_id"])["status"] == "pending"


def test_az_mcp_eszkoz_nem_hagy_jova_hogot(get_db, kulcs, sonnet, echolot, x_api, fake_app):
    from plugins import action_extract
    action_extract.register_tools(fake_app, {"get_db": get_db, "capture_state": {}})
    out, _ = _futtat(get_db, sonnet)
    r = json.loads(_run(fake_app.tools["action_draft_decide"](draft_id=out["hog_id"], decision="approve",
                                                              caller="web-claus")))
    assert "HOG-ot ez az eszköz nem hagy jóvá" in r["error"]
    assert "hog_publish" not in _eszkozok(echolot) and _tweetek(x_api) == []


# ── 3. A sorrend: publish → válasz (kártyaképpel) → a link a saját válasz alá ─

def test_sorrend_publish_utan_tweet_a_linkkel(get_db, kulcs, sonnet, echolot, x_api):
    out, _ = _futtat(get_db, sonnet)
    sorrend = []
    echolot["sorrend"] = x_api["sorrend"] = sorrend
    uz = []

    async def ertesit(chat_id, s):
        uz.append(s)
    r = _run(H.dont(get_db, out["hog_id"], "approve", ertesit=ertesit))
    assert r["posztolva"] and r["oldal_url"] == OLDAL and r["mod"] == "valasz"
    assert sorrend == ["echolot:hog_publish", "kep_letoltes", "x:POST:/2/media/upload", "x:POST:/2/tweets",
                       "x:POST:/2/tweets"]
    valasz, link = (_torzs(t) for t in _tweetek(x_api))
    assert valasz == {"text": KOMMENT, "reply": {"in_reply_to_tweet_id": CEL_ID}, "media": {"media_ids": ["555"]}}
    assert OLDAL not in valasz["text"]                                   # a komment link NÉLKÜL (doktrína)
    assert link["reply"] == {"in_reply_to_tweet_id": "900001"} and OLDAL in link["text"]   # a link a SAJÁT válasz alá
    pub = dict(echolot["hivasok"])["hog_publish"]
    assert pub["draft_id"] == "hog_2026-11-23_elon_9993" and pub["comment_text"] == KOMMENT and len(pub["facts"]) == 12
    assert pub["editor_key"] == SZERK
    s = _sor(get_db, out["hog_id"])
    assert s["status"] == "created" and s["payload"]["oldal_url"] == OLDAL
    assert s["payload"]["link_valasz"] == "https://x.com/i/status/900002"
    assert "Oldal (állandó)" in uz[0] and "VÁLASZ" in uz[0] and "kiment a válaszod alá" in uz[1]
    # dupla kattintás: nincs második publikálás, nincs második tweet
    r2 = _run(H.dont(get_db, out["hog_id"], "approve"))
    assert "már nem vár döntésre" in r2["error"] and len(_tweetek(x_api)) == 2


def test_kifejezett_mondat_is_ezt_inditja(get_db, kulcs, sonnet, echolot, x_api):
    import feldwebel
    from feldwebel import BridgeContext
    from feldwebel.history import ensure_history_table
    ensure_history_table(get_db)
    out, _ = _futtat(get_db, sonnet)
    push = []

    async def p(s, *a, **k):
        push.append(s)
    ctx = BridgeContext(telegram_push=p, get_db=get_db)
    feldwebel._ctx, elozo = ctx, feldwebel._ctx
    try:
        valasz = _run(T.kezel(ctx, "posztold ki a tervezetet: 200 reports in", "42"))
    finally:
        feldwebel._ctx = elozo
    assert "A HOG kiment" in valasz and OLDAL in valasz
    assert _eszkozok(echolot)[-1] == "hog_publish" and len(_tweetek(x_api)) == 2
    assert _sor(get_db, out["hog_id"])["payload"]["jovahagyta"] == "kifejezett mondat"
    assert any("kiment a válaszod alá" in s for s in push)


def test_publish_hiba_eseten_nincs_tweet(get_db, kulcs, sonnet, echolot, x_api):
    out, _ = _futtat(get_db, sonnet)
    echolot["publish"] = {"ok": False, "error": "invalid_references", "message": "bad", "bad_refs": [{"ref": 99}]}
    r = _run(H.dont(get_db, out["hog_id"], "approve"))
    assert r["publikalva"] is False and _tweetek(x_api) == [] and "invalid_references" in r["error"]
    assert _sor(get_db, out["hog_id"])["status"] == "pending"


def test_ha_a_kartya_nem_el_nincs_tweet(get_db, kulcs, sonnet, echolot, x_api):
    out, _ = _futtat(get_db, sonnet)
    echolot["publish"] = {"ok": False, "url": OLDAL, "reply_text": f"x {OLDAL}", "quote_text": f"q {OLDAL}"}
    r = _run(H.dont(get_db, out["hog_id"], "approve"))
    assert r["publikalva"] and not r["posztolva"] and _tweetek(x_api) == []
    s = _sor(get_db, out["hog_id"])
    assert s["status"] == "pending" and s["payload"]["oldal_url"] == OLDAL and "élő ellenőrzés" in r["error"]


# ── 4. 403 → idézet; a telefon-link ─────────────────────────────────────────

def test_403_valasz_idezetre_esik(get_db, kulcs, sonnet, echolot, x_api):
    out, _ = _futtat(get_db, sonnet)
    x_api["valasz"] = [(403, {"title": "Forbidden", "type": "about:blank", "status": 403, "detail": (
        "Reply to this conversation is not allowed because you have not been mentioned or otherwise engaged by the "
        "author of the post you are replying to.")})]
    uz = []

    async def ertesit(chat_id, s):
        uz.append(s)
    r = _run(H.dont(get_db, out["hog_id"], "approve", ertesit=ertesit))
    valasz, idezet = (_torzs(t) for t in _tweetek(x_api))
    assert "reply" in valasz
    assert idezet["quote_tweet_id"] == CEL_ID and "reply" not in idezet and idezet["text"].startswith(IDEZET)
    assert OLDAL in idezet["text"] and r["mod"] == "idezet" and len(_tweetek(x_api)) == 2     # link-válasz NINCS
    assert "IDÉZETKÉNT posztoltam" in uz[0] and "summoned replies" in uz[0]
    assert "x.com/intent/post?in_reply_to=" + CEL_ID in uz[0]                 # „Válasz a telefonról” ott van


def test_intent_linkek():
    p = {"comment_text": "Sor egy\nSor kettő?", "cel": {"tweet_id": CEL_ID, "url": f"https://x.com/elonmusk/status/{CEL_ID}"}}
    ki = H.intentek(p, {"reply_text": f"Breakdown: {OLDAL}?utm_medium=reply", "quote_text": f"Q {OLDAL}"})
    assert ki["valasz"].startswith(f"https://x.com/intent/post?in_reply_to={CEL_ID}&text=Sor%20egy%0ASor%20kett")
    assert "utm_medium%3Dreply" in ki["valasz"]
    assert ki["idezet"] == ("https://x.com/intent/post?text=Q%20https%3A%2F%2Fecholotnews.com%2Fen%2Fhog%2Fnvidia-"
                            "reinstates-jensen-huang-coverage&url=https%3A%2F%2Fx.com%2Felonmusk%2Fstatus%2F" + CEL_ID)
    assert "sajat" in H.intentek({"comment_text": "x"}, {"quote_text": "Q"})


def test_x_kulcs_nelkul_az_oldal_kimegy_a_telefon_link_marad(get_db, kulcs, sonnet, echolot, x_api, monkeypatch):
    out, _ = _futtat(get_db, sonnet)
    for k in X_KULCSOK:
        monkeypatch.delenv(k)
    r = _run(H.dont(get_db, out["hog_id"], "approve"))
    assert r["publikalva"] and not r["posztolva"] and _tweetek(x_api) == []
    assert "Telefonról egy koppintás" in r["uzenet"] and "in_reply_to=" + CEL_ID in r["uzenet"]
    assert _sor(get_db, out["hog_id"])["status"] == "pending"           # a gomb újrapróbálja, a Lezárom befejezi


def test_idezet_mod_es_sajat_mod(get_db, kulcs, sonnet, echolot, x_api):
    a, _ = _futtat(get_db, sonnet, {"poszt_szoveg": "x y z", "szerzo": "@elonmusk", "mod": "idezet",
                                    "poszt_url": f"https://x.com/elonmusk/status/{CEL_ID}"})
    _run(H.dont(get_db, a["hog_id"], "approve"))
    assert _torzs(_tweetek(x_api)[-1])["quote_tweet_id"] == CEL_ID and len(_tweetek(x_api)) == 1
    b, _ = _futtat(get_db, sonnet, {"poszt_szoveg": "x y z", "szerzo": "Valaki"})
    _run(H.dont(get_db, b["hog_id"], "approve"))
    t = _torzs(_tweetek(x_api)[-1])
    assert set(t) == {"text"} and OLDAL in t["text"] and len(_tweetek(x_api)) == 2


# ── 5. Az elvetés semmit nem tesz ki ──────────────────────────────────────

def test_elvetes_semmit_nem_tesz_ki(get_db, kulcs, sonnet, echolot, x_api):
    out, _ = _futtat(get_db, sonnet)
    elotte = len(echolot["hivasok"])
    r = _run(H.dont(get_db, out["hog_id"], "reject"))
    assert r["status"] == "rejected" and not r["posztolva"]
    assert len(echolot["hivasok"]) == elotte and _tweetek(x_api) == []
    assert "már nem vár" in _run(H.dont(get_db, out["hog_id"], "approve"))["error"]


# ── 6. A szerkesztői kulcs nem szivárog ─────────────────────────────────────

def test_a_szerkesztoi_kulcs_nem_szivarog(get_db, kulcs, sonnet, echolot, x_api, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    out, uz = _futtat(get_db, sonnet)
    sor = json.dumps(_sor(get_db, out["hog_id"]), ensure_ascii=False)
    assert SZERK not in sor and SZERK not in " ".join(uz)
    # a hibaszövegben visszajövő kulcs is kitakarva (pl. egy proxy visszaírja a kérést)

    async def rossz(tool, args, timeout):
        # a kulcs a hibaszöveg 300. karaktere körül áll: a vágás nem hagyhat meg belőle darabot
        raise RuntimeError("502 upstream: " + "x" * 290 + f" key={args.get('editor_key')} {json.dumps(args)}")
    monkeypatch.setattr(H, "ECHOLOT_HIV", rossz)
    r = _run(H.dont(get_db, out["hog_id"], "approve"))
    hiba, _ = _futtat(get_db, sonnet)
    for s in (json.dumps(r, ensure_ascii=False), json.dumps(hiba, ensure_ascii=False), caplog.text,
              json.dumps(_sor(get_db, out["hog_id"]), ensure_ascii=False)):
        assert SZERK not in s
        for v in X_KULCSOK.values():
            assert v not in s
    assert "502 upstream" in r["error"] and "502 upstream" in hiba["error"]
    for darab in (SZERK[:8], SZERK[-6:]):
        assert darab not in r["error"] and darab not in hiba["error"]
    # a kabin-kártya sem viszi ki
    assert SZERK not in json.dumps(H.kartya(_sor(get_db, out["hog_id"])["payload"]), ensure_ascii=False)


# ── Az írás: helyi [n]/URL-ellenőrzés és a javítókör ─────────────────────────

def test_ellenoriz_fogja_a_rossz_hivatkozast_es_az_urlt():
    pkg = _csomag(5)
    h = H.ellenoriz(pkg, _iras(analysis_text="A [1].\n\nB [9].\n\nC lap1.example/cikk-1 és evil.example/fake-report [2].",
                               comment_text="Nézd: https://echolotnews.com #a #b #c", facts=["what: x [1]"] * 3))
    s = " | ".join(h)
    assert "cites [9]" in s and "evil.example/fake-report" in s and "lap1.example" not in s
    assert "at least 12" in s and "contains a link" in s and "3 hashtags" in s
    assert H.ellenoriz(_csomag(), _iras()) == []
    assert any("identical" in x for x in H.ellenoriz(_csomag(), _iras(quote_text=KOMMENT)))
    assert any("excluded" in x for x in H.ellenoriz(_csomag(), _iras(exclude_refs=[{"ref": 2, "reason": "névrokon"}])))


def test_javitokor_a_hibas_hivatkozast_kijavittatja(sonnet):
    sonnet["iras"] = [_iras(analysis_text="A [1].\n\nB [99].\n\nC [2].")]
    iras, koltes, _ = _run(H.ir(_csomag(), {"text": "x"}, chat=sonnet["chat"]))
    celok = [h["purpose"] for h in sonnet["hivasok"]]
    assert celok == ["hog/iras", "hog/javitas"] and iras["_javito_kor"] == 1 and koltes == pytest.approx(0.10)
    assert "cites [99]" in sonnet["hivasok"][1]["payload"]["messages"][1]["content"]


def test_javitokor_utan_is_hibas_kimondott_hiba(sonnet):
    rossz = _iras(analysis_text="A [1].\n\nB [99].\n\nC [2].")
    sonnet["iras"] = [rossz]
    sonnet["javitas"] = [rossz, rossz]
    with pytest.raises(H.HogHiba, match=r"2 javítókör után is hibás"):
        _run(H.ir(_csomag(), {"text": "x"}, chat=sonnet["chat"]))


def test_irasi_csomag_tomor():
    pkg = _csomag(200)
    pkg["material"] = {"x": "y" * 100_000}
    pkg["home_silence"] = {"measured_via": {"fts_query": "z" * 5000, "path": "p"}}
    c = H.irasi_csomag(pkg, {"text": "t"})
    assert "material" not in c and "fts_query" not in json.dumps(c) and len(c["sources"]) == 200
    assert set(c["sources"][0]) >= {"n", "src", "title"} and "url" not in c["sources"][0]


# ── Az X-bővítés (twitter_poszt) ─────────────────────────────────────────

def test_keres_torzs():
    assert T.keres_torzs("a", valasz_id=5) == {"text": "a", "reply": {"in_reply_to_tweet_id": "5"}}
    assert T.keres_torzs("a", idezet_id=6, media_ids=[1]) == {"text": "a", "quote_tweet_id": "6",
                                                              "media": {"media_ids": ["1"]}}
    with pytest.raises(ValueError):
        T.keres_torzs("a", valasz_id=1, idezet_id=2)


def test_media_feltoltes_multipart_es_alairt(kulcs, x_api):
    r = _run(T.media_feltolt(b"\x89PNGkep"))
    q = x_api["keresek"][0]
    assert r == {"ok": True, "media_id": "555"} and q.url.path == "/2/media/upload"
    assert q.headers["content-type"].startswith("multipart/form-data") and b"tweet_image" in q.content
    assert q.headers["authorization"].startswith("OAuth ")


def test_kovetoszam_public_metrics(kulcs, x_api):
    r = _run(T.en(metrika=True))
    assert r["followers_count"] == 312 and x_api["keresek"][0].url.params["user.fields"] == "public_metrics"


def test_a_korlatozott_valasz_403_kimondva():
    s = T._hiba_szoveg(403, {"detail": "You can only reply to or quote posts where you are mentioned or are the "
                                       "author.", "type": "https://api.twitter.com/2/problems/not-authorized-for-resource"})
    assert "summoned replies" in s and "telefonról" in s


def test_a_napi_plafon_kozos(get_db, kulcs, sonnet, echolot, x_api, monkeypatch):
    monkeypatch.setenv("TWITTER_NAPI_MAX", "1")
    a, _ = _futtat(get_db, sonnet)
    b, _ = _futtat(get_db, sonnet)
    assert _run(H.dont(get_db, a["hog_id"], "approve"))["posztolva"]
    r = _run(H.dont(get_db, b["hog_id"], "approve"))
    assert "napi plafont" in r["error"] and _sor(get_db, b["hog_id"])["status"] == "pending"
    t = T.tervezet(get_db, "Holnap KSH.", forras="feldwebel", source_message_id="f:1")
    assert "napi plafont" in _run(T.dont(get_db, t["draft_id"], "approve"))["error"]


def test_a_sema_atepul_a_sorok_megmaradnak(get_db):
    conn = get_db()
    conn.execute("INSERT INTO action_drafts (kind, title, payload_json, created_at) VALUES ('calendar', 'régi', '{}', "
                 "'2026-01-01')")
    conn.commit()
    assert T.schema_biztosit(conn, "hog") is True and T.schema_biztosit(conn, "hog") is False
    assert conn.execute("SELECT title FROM action_drafts WHERE id = 1").fetchone()[0] == "régi"
    conn.execute("INSERT INTO action_drafts (kind, title, payload_json, created_at) VALUES ('hog', 'h', '{}', 'x')")
    conn.close()


def test_a_feldwebel_eszkoze_csak_inditas(get_db, kulcs, monkeypatch):
    from feldwebel import BridgeContext, eszkoz_ut, responder
    assert eszkoz_ut.kategoria("hog_keszit") == "kozzetetel"
    for mondat in ("Erre egy HOG Twitter bejegyzést kérek", "csináljunk erre HOG-ot", "írj rá választ"):
        assert "kozzetetel" in eszkoz_ut.szandek(mondat)
    assert "HOG" in responder.SYSTEM_PROMPT_TEMPLATE and any(
        t["function"]["name"] == "hog_keszit" for t in responder.TOOLS)
    indult = []

    async def futtat(get_db_, args, **kw):
        indult.append(args)
        return {"ok": True}
    monkeypatch.setattr(H, "futtat", futtat)

    async def kor():
        out = json.loads(await responder._execute_tool("hog_keszit", {"poszt_szoveg": "x", "melleklet_id": 8},
                                                       BridgeContext(get_db=get_db), kor={"chat_id": "42"}))
        await asyncio.sleep(0)
        return out
    out = _run(kor())
    assert out["status"] == "elindult" and out["posztolva"] is False and indult == [{"poszt_szoveg": "x",
                                                                                     "melleklet_id": 8}]
    import server
    assert "hog_keszit" in server.SUBAGENT_TILTOTT and "hog_publish" in server.SUBAGENT_TILTOTT


def test_a_kabin_gombja_a_hog_dont_hoz_megy(get_db, kulcs, sonnet, echolot, x_api, monkeypatch):
    import server
    import ui_kabin
    monkeypatch.setattr(server, "get_db", get_db)
    elkuldve = []

    async def push(s):
        elkuldve.append(s)
    monkeypatch.setattr(server, "_telegram_push", push)

    async def tilos(*a, **k):
        raise AssertionError("a HOG nem az action_draft_decide-on megy")
    monkeypatch.setattr(server.mcp, "call_tool", tilos)
    out, _ = _futtat(get_db, sonnet)
    conn = get_db()
    try:
        kartyak = ui_kabin._draftok(conn)
    finally:
        conn.close()
    k = next(d for d in kartyak if d["id"] == out["hog_id"])
    assert k["fajta"] == "hog" and k["hog"]["mod"] == "valasz" and k["hog"]["hossz"]["komment"] <= 280
    assert len(k["hog"]["tenyek"]) == 12 and k["hog"]["cim"].startswith("Nvidia")
    r = _run(server._ui_draft_dont(out["hog_id"], "approve"))
    assert r["posztolva"] and "A HOG kiment" in elkuldve[0] and "FELDWEBEL" in elkuldve[0]


@pytest.mark.parametrize("mondat,vart", [
    ("csináljunk erre HOG-ot", True), ("Erre egy HOG Twitter bejegyzést kérek", True), ("a hogra itt a link", True), ("hogyha kell", False),
    ("Hogy vagy?", False), ("hogyan megy ez", False), ("HOG-ra van szükség", True), ("hog", True),
])
def test_hog_szandek_determinisztikus(mondat, vart):
    assert H.szandek(mondat) is vart
