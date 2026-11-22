"""ANTHROPIC-TARTALÉK (2026-11-22): ha a SiliconFlow nem megy, a Bridge az Anthropicon megy tovább — KIMONDVA.

A Kommandant: „A bridge-nek az anthropic kulcsunkkal is működnie kell tudni, ha rizsrakéták nem mennek, kivéve a
delphoi funkciót.” Aznap este 0,04 $-ra fogyott a SiliconFlow-egyenleg, és a Bridge-funkciók egy része egyszerűen állt:
18 hívóhely a közös kaput megkerülve, közvetlenül POST-olt a SiliconFlow-ra.

Amit ezek a tesztek őriznek (hálózat nélkül — a SiliconFlow és az Anthropic hamisítvány, költés nincs):
  1. 402-nél (minden kulcson) a kapu NEM próbálja végig a tartaléklánc modelljeit: rögtön az Anthropic, kimondva;
  2. a MEGSZAKÍTÓ a második hívásnál már nem próbálja az SF-et; az ablak lejárta után egy próba megy, a siker zár;
  3. a Feldwebel, egy plugin (`draft_reply`), a „Kimi, …” megszólítás, az Agora, a vita és a családi chat az
     Anthropicon válaszol, és a fej / jelvény a VALÓDI modellt mutatja; a helyettesítő nem adja ki magát Kiminek;
  4. a DELPHOI továbbra is SiliconFlow-n — nincs Anthropic-útja;
  5. az embedding (Anthropic-API nincs rá) kimondottan visszalép kulcsszavas besorolásra, okkal.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sqlite3

import pytest

import llm_kapu
import server
from tests.test_llm_kapu import _msg
from tests.test_sf_selfheal import FakeResp, _ok_payload

URES = '{"code":30001,"message":"Sorry, your account balance is insufficient","data":null}'


def _run(c):
    return asyncio.run(c)


class SFHamis:
    """Hamis SiliconFlow (`httpx.AsyncClient` helyén): minden POST ugyanazt adja (alapból 402), és naplóz."""

    def __init__(self, status=402, szoveg=URES, payload=None):
        self.status, self.szoveg, self.payload = status, szoveg, payload
        self.hivasok: list[dict] = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.hivasok.append({"url": url, "kulcs": (headers or {}).get("Authorization", ""), "json": dict(json or {})})
        return FakeResp(self.status, self.szoveg, self.payload)


class ClaudeHamis:
    """Hamis Anthropic (`llm_kapu._kuld` helyén): a KÉRT modell nevében válaszol, és naplózza a kéréseket."""

    def __init__(self, szoveg="Claude válasza."):
        self.szoveg = szoveg
        self.hivasok: list[dict] = []

    async def __call__(self, kw):
        self.hivasok.append(json.loads(json.dumps(kw, default=str)))
        return _msg([{"type": "text", "text": self.szoveg}], model=kw["model"])

    def system(self, i=-1) -> str:
        s = self.hivasok[i].get("system")
        return s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)


@pytest.fixture
def k(monkeypatch):
    """Anthropic-kulcs + két SiliconFlow-kulcs, mindkettőn 402; nincs alvás, a havi számláló tiszta."""
    import httpx
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt")
    monkeypatch.setattr(llm_kapu, "_KOLTES", {"honap": "", "usd": 0.0, "hivasok": 0, "szintenkent": {}})

    async def _azonnal(_s):
        return None
    monkeypatch.setattr(asyncio, "sleep", _azonnal)
    sf = SFHamis()
    monkeypatch.setattr(httpx, "AsyncClient", sf)
    monkeypatch.setattr(server, "SILICONFLOW_API_KEY", "sf-kulcs-1")
    monkeypatch.setattr(server, "SILICONFLOW_API_KEY2", "sf-kulcs-2")
    claude = ClaudeHamis()
    monkeypatch.setattr(llm_kapu, "_kuld", claude)
    # a megtanult token-keret a sandbox-DB `shared_memory`-jébe írna — ezek a tesztek a tartalékot mérik, nem azt
    # (és a sandbox FTS-táblája a main-en is „malformed/locked”-ot ad egymás utáni írásnál: ne tegyük sorrendfüggővé)
    monkeypatch.setattr(server, "_sf_budget_remember", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_SF_LEARNED_BUDGET", {})
    return sf, claude


@pytest.fixture
def kapu():
    """A szerver bejegyzett kapuja (élesben induláskor; a conftest minden tesztet nélküle indít)."""
    llm_kapu.kapu_beallit(server.sf_chat)


def _kiesesek(notes):
    return [n for n in notes if "SILICONFLOW-KIESÉS" in n]


# ── 1. 402: azonnal Anthropic, a lánc nélkül ──────────────────────────────

def test_402_minden_kulcson_azonnal_anthropic_a_tartaleklanc_nelkul(k, monkeypatch):
    sf, claude = k
    monkeypatch.setenv("LLM_ELSODLEGES", "siliconflow")
    payload = {"model": "moonshotai/Kimi-K3.1-Pro", "messages": [{"role": "user", "content": "x"}]}
    assert len(server._SF_FAILOVER[payload["model"]]) == 2          # a lánc létezik — és nem próbáljuk végig
    d, e, notes = _run(server.sf_chat(payload, purpose="email_triage/besorolas"))
    assert e is None and d["choices"][0]["message"]["content"] == "Claude válasza." and d["provider"] == "anthropic"
    assert [h["kulcs"] for h in sf.hivasok] == ["Bearer sf-kulcs-1", "Bearer sf-kulcs-2"], \
        "402-nél csak a másik kulcs segíthet (másik fiók) — másik modell nem, ugyanaz a fiók fizetné"
    assert {h["json"]["model"] for h in sf.hivasok} == {"moonshotai/Kimi-K3.1-Pro"}
    assert any("SILICONFLOW-KIESÉS (payment)" in n for n in notes) and len(claude.hivasok) == 1
    assert llm_kapu.sf_megszakito.nyitva() and llm_kapu.sf_megszakito.ok() == "payment"


def test_a_megszakito_a_masodik_hivasnal_mar_nem_proba_sf(k, monkeypatch):
    sf, claude = k
    monkeypatch.setenv("LLM_ELSODLEGES", "siliconflow")
    payload = {"model": "zai-org/GLM-5.3", "messages": [{"role": "user", "content": "x"}]}
    _run(server.sf_chat(payload, purpose="ai_query/glm53"))
    elso = len(sf.hivasok)
    d, e, notes = _run(server.sf_chat(payload, purpose="ai_query/glm53"))
    assert e is None and d["provider"] == "anthropic"
    assert len(sf.hivasok) == elso, "nyitott megszakítónál a SiliconFlow-t ki sem próbáljuk"
    assert any("megszakító" in n and "Anthropic adja" in n for n in _kiesesek(notes))


def test_az_ablak_utan_egy_proba_megy_es_a_siker_zar(k, monkeypatch):
    sf, claude = k
    llm_kapu.sf_megszakito.kieses("payment", "teszt")
    ido = [1000.0]
    monkeypatch.setattr(llm_kapu, "_most", lambda: ido[0])
    llm_kapu.sf_megszakito.kieses("payment", "teszt")                 # a hamis órán nyit újra
    ido[0] += llm_kapu.sf_megszakito.ablak_s() + 1                    # lejárt az ablak
    sf.status, sf.szoveg, sf.payload = 200, "", _ok_payload("SF él", model="zai-org/GLM-5.3")
    d, e, notes = _run(server.sf_chat({"model": "zai-org/GLM-5.3", "messages": [{"role": "user", "content": "x"}]},
                                      purpose="vita/glm53", sf_elso=True))
    assert e is None and d["choices"][0]["message"]["content"] == "SF él" and len(sf.hivasok) == 1
    assert llm_kapu.sf_megszakito.ok() is None and not claude.hivasok


def test_a_megszakito_allapotgepe(monkeypatch):
    m = llm_kapu.SfMegszakito()
    ido = [0.0]
    monkeypatch.setattr(llm_kapu, "_most", lambda: ido[0])
    # tartós kiesés: a küszöbig számol, utána nyit; egy botlás nem kiesés
    m.kieses("transport")
    m.kieses("server_error")
    assert not m.kihagy() and m.ok() is None
    m.kieses("transport")
    assert m.kihagy() and m.ok() == "transport"
    # félig nyitva: EGY próba megy, a többi addig is kihagy
    ido[0] += m.ablak_s() + 1
    assert m.kihagy() is False and m.kihagy() is True
    # a rossz kérés semleges: a próba-hely felszabadul, de nem zár
    m.kieses("bad_request")
    assert m.ok() == "transport" and m.kihagy() is False
    # a próba bukása újra nyit egy teljes ablakra; a siker zár
    m.kieses("server_error")
    assert m.kihagy() is True
    m.siker()
    assert m.ok() is None and not m.kihagy() and not m.nyitva()


def test_a_megszakito_ablaka_envbol(monkeypatch):
    monkeypatch.setenv("SF_MEGSZAKITO_PERC", "1")
    monkeypatch.setenv("SF_MEGSZAKITO_KUSZOB", "1")
    m = llm_kapu.SfMegszakito()
    assert m.ablak_s() == 60 and m.kuszob() == 1
    m.kieses("transport")
    assert m.nyitva()
    monkeypatch.setenv("SF_MEGSZAKITO_PERC", "nem-szam")
    assert m.ablak_s() == 600


def test_az_allapotban_latszik_a_megszakito(k):
    llm_kapu.sf_megszakito.kieses("payment", "HTTP 402")
    a = llm_kapu.allapot()["siliconflow_megszakito"]
    assert a["nyitva"] and a["ok"] == "payment" and a["hatra_mp"] > 0


def test_anthropic_elsodleges_kiesese_es_nyitott_megszakito_nincs_hova(k, monkeypatch):
    sf, _ = k

    async def kieses(payload, purpose=""):
        return None, {"code": "rate_limit", "message": "429"}, []
    monkeypatch.setattr(llm_kapu, "chat", kieses)
    llm_kapu.sf_megszakito.kieses("payment")
    d, e, notes = _run(server.sf_chat({"model": "zai-org/GLM-5.3", "messages": [{"role": "user", "content": "x"}]},
                                      purpose="market_brief/economic"))
    assert d is None and e["code"] == "rate_limit" and not sf.hivasok
    assert any("nincs hova váltani" in n for n in notes)


# ── 2. A név szerinti ügynök: a fej a valódi modellt mutatja, és nem adja ki magát Kiminek ────────

@pytest.fixture
def telegram(k, monkeypatch, tmp_path):
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
    monkeypatch.setattr(server, "PYRAMID_ENABLED", True)
    monkeypatch.setattr(server, "build_agent_context",
                        lambda **kw: "Te KIMI vagy, a Bridge ügynöke, a Kimi-K3.1-Pro modellen.")
    monkeypatch.setattr(server, "_get_inbox_summary", lambda *a, **kw: "")
    monkeypatch.setattr(server, "pyramid_store_result", lambda **kw: None)
    return get_db, pushed


def test_kimi_megszolitas_402_utan_az_anthropic_valaszol_kimondva(telegram, k):
    get_db, pushed = telegram
    sf, claude = k
    _run(server._handle_telegram_message("Kimi, mi a véleményed a forintról?", "42"))
    assert sf.hivasok and {h["json"]["model"] for h in sf.hivasok} == {"moonshotai/Kimi-K3.1-Pro"}, \
        "a név szerint szólított ügynök a SAJÁT modelljén indul"
    assert len(pushed) == 1
    valaszolo = claude.hivasok[0]["model"]
    from cimzett import fejlec
    assert pushed[0].startswith(fejlec("kimi", valaszolo)), "a fej a TÉNYLEGESEN válaszoló modellt mutatja"
    assert "SILICONFLOW-KIESÉS" in pushed[0]
    assert "RENDSZERJEGYZET" in claude.system() and f"`{valaszolo}`" in claude.system(), \
        "a helyettesítő Claude ne adja ki magát Kiminek"
    targy = [r["subject"] for r in get_db().execute("SELECT subject FROM messages WHERE sender='kimi'")]
    assert targy == [f"Re: @kimi válasz · modell={valaszolo}"]


def test_a_megszolitott_ugynok_nyitott_megszakitonal_is_helyettesito_jegyzetet_kap(k):
    sf, claude = k
    llm_kapu.sf_megszakito.kieses("payment")
    d, e, notes = _run(server.sf_chat({"model": "deepseek-ai/DeepSeek-V4-Pro-0813", "messages": [
        {"role": "system", "content": "Te DEEPSEEK vagy."}, {"role": "user", "content": "x"}]},
        purpose="bridge/mention/deepseek"))
    assert e is None and not sf.hivasok and "RENDSZERJEGYZET" in claude.system()
    assert "DeepSeek-V4-Pro-0813" in claude.system() and _kiesesek(notes)


def test_a_bridge_mention_valasz_a_kapun(k, monkeypatch):
    """A Bridge-üzenetben szólított ügynök (`@kimi`) háttér-válasza is a kapun megy — 402 után Claude, kimondva."""
    sf, claude = k
    tarolt = []

    class C:
        def execute(self, sql, args=()):
            tarolt.append(args)

            class R:
                lastrowid = 1
            return R()

        def commit(self):
            pass

        def close(self):
            pass
    monkeypatch.setattr(server, "get_db", lambda: C())
    monkeypatch.setattr(server, "PYRAMID_ENABLED", False)

    def szinkron_szal(target=None, daemon=None):
        class T:
            def start(self):
                target()
        return T()
    monkeypatch.setattr(server.threading, "Thread", szinkron_szal)
    server._trigger_agent_replies(7, "cli-claus", "@kimi nézd meg", ["kimi"])
    assert claude.hivasok and tarolt
    _ido, felado, cimzett_, targy, uzenet, *_ = tarolt[0]
    assert felado == "kimi" and "modell=claude-" in targy and "SILICONFLOW-KIESÉS" in uzenet


# ── 3. A Feldwebel ────────────────────────────────────────────────────────

def test_feldwebel_402_utan_az_anthropic_valaszol(k, monkeypatch):
    from feldwebel import BridgeContext, responder
    monkeypatch.setenv("LLM_ELSODLEGES", "siliconflow")

    async def push(_s):
        raise AssertionError("sikeres tartaléknál nincs hibaüzenet")
    ctx = BridgeContext(telegram_push=push, llm_chat=server.sf_chat, siliconflow_api_key="sf-kulcs-1")
    notes: list = []
    data = _run(responder._call_deepseek(ctx, "deepseek-ai/DeepSeek-V4-Pro-0813",
                                         [{"role": "user", "content": "Szia, mi a helyzet?"}], notes=notes))
    assert data["choices"][0]["message"]["content"] == "Claude válasza." and data["model"].startswith("claude-")
    assert any("SILICONFLOW-KIESÉS (payment)" in n for n in notes)


def test_feldwebel_triage_bekotes_nelkul_is_a_kapun(k, kapu, monkeypatch):
    """A `ctx.llm_chat` nélküli Feldwebel-segéd is a bejegyzett kapuhoz jut (`llm_kapu.kapun`)."""
    sf, claude = k
    from feldwebel import BridgeContext
    from feldwebel.triage import smart_categorize
    monkeypatch.setenv("LLM_ELSODLEGES", "siliconflow")
    claude.szoveg = '{"priority": "urgent", "category": "work", "summary_hu": "Határidő.", "suggested_action": "reply"}'
    ctx = BridgeContext(siliconflow_api_key="sf-kulcs-1", siliconflow_models=server.SILICONFLOW_MODELS)
    r = _run(smart_categorize("fonok@ceg.hu", "Holnapig kell", "Kérlek nézd meg holnapig.", ctx))
    assert r["priority"] == "urgent" and r["modell"].startswith("claude-haiku"), "triage = olcsó szint, kimondva"
    assert sf.hivasok, "a SiliconFlow volt az elsődleges"


# ── 4. Egy plugin: draft_reply ───────────────────────────────────────────

def test_draft_reply_402_utan_az_anthropic_irja_kimondva_de_nem_a_levelbe(k, kapu, fake_app, get_db, monkeypatch):
    from tests.test_draft_reply import _deps, _make_gmail_service, _thread_payload
    import plugins.draft_reply as dr
    sf, claude = k
    monkeypatch.setenv("LLM_ELSODLEGES", "siliconflow")
    claude.szoveg = "Kedves Béla,\n\nköszönöm, a határidő rendben.\n\nÜdv,\nTamás"
    svc = _make_gmail_service(_thread_payload(), draft_id="d-1")
    dr.register_tools(fake_app, _deps(get_db, gmail_service=svc))
    res = json.loads(_run(fake_app.tools["draft_reply"](thread_id="t1", caller="teszt")))
    assert res["draft_id"] == "d-1" and res["modell"] == "claude-sonnet-5-5", "levéltervezet = közép szint"
    assert any("SILICONFLOW-KIESÉS" in n for n in res["kapu_jegyzet"])
    # a levél törzsébe NEM kerül technikai jegyzet — azt a Kommandant elküldheti
    raw = svc.users().drafts().create.call_args.kwargs["body"]["message"]["raw"]
    torzs = base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
    assert "KIESÉS" not in torzs and "RENDSZERJEGYZET" not in torzs
    sor = get_db().execute("SELECT style_notes FROM draft_log").fetchone()
    assert "modell=claude-sonnet-5-5" in sor["style_notes"]


def test_draft_reply_alapbol_az_elsodleges_anthropic_ir_sf_nelkul(k, kapu, fake_app, get_db):
    from tests.test_draft_reply import _deps, _make_gmail_service, _thread_payload
    import plugins.draft_reply as dr
    sf, claude = k
    svc = _make_gmail_service(_thread_payload())
    dr.register_tools(fake_app, _deps(get_db, gmail_service=svc))
    res = json.loads(_run(fake_app.tools["draft_reply"](thread_id="t1")))
    assert res["modell"].startswith("claude-") and not sf.hivasok


def test_kapu_nelkul_a_regi_kozvetlen_ut_tipizalt_hibaval(monkeypatch):
    import httpx
    sf = SFHamis()
    monkeypatch.setattr(httpx, "AsyncClient", sf)
    d, e, notes = _run(llm_kapu.kapun({"model": "m", "messages": []}, purpose="x", sf_kulcs="kk",
                                      sf_alap="https://pelda.test/v1"))
    assert d is None and e["code"] == "payment" and e["status"] == 402 and notes == []
    assert sf.hivasok[0]["url"] == "https://pelda.test/v1/chat/completions"
    assert sf.hivasok[0]["kulcs"] == "Bearer kk"


# ── 5. Az Agora ──────────────────────────────────────────────────────────

def test_agora_402_utan_az_anthropic_ir_a_jelveny_a_valodi_modell(k, kapu, monkeypatch):
    import plugins.agora_duty as ad
    from plugins._agora_personas import AGORA_AGENTS
    sf, claude = k
    monkeypatch.setattr(ad, "_JELZES_HELYBEN", {})
    pushed = []

    async def push(s):
        pushed.append(s)
    deps = {"siliconflow_api_key": "sf-kulcs-1", "siliconflow_models": server.SILICONFLOW_MODELS,
            "telegram_push": push}
    a = next(v for v in AGORA_AGENTS.values() if v["agent_id"] == "kimi3")

    async def kor():
        szoveg = await ad._sf_chat(deps, "kimi3", f"Te {a['label']} vagy (model badge: {a['model_badge']}).",
                                   "Írj kommentet.", feladat="komment")
        masodik = await ad._sf_chat(deps, "kimi3", "s", "Még egyet.", feladat="komment")
        return szoveg, masodik, ad._jelveny(a), ad._szerzoi_jegyzet(a, "Heti esszé.")
    szoveg, masodik, jelveny, jegyzet = _run(kor())
    assert szoveg == masodik == "Claude válasza."
    assert jelveny == claude.hivasok[0]["model"] == "claude-sonnet-5-5", "Agora-komment = közép szint, valódi jelvény"
    assert jegyzet == "Heti esszé. [model: claude-sonnet-5-5]"
    assert "RENDSZERJEGYZET" in claude.system(0) and "Kimi-K3.1-Pro" in claude.system(0)
    assert len(pushed) == 1 and "claude-sonnet-5-5" in pushed[0], "EGY Telegram-jelzés, nem minden hívásnál"
    assert len(sf.hivasok) == 2, "a második hívás már a megszakítón át, SiliconFlow nélkül"


def test_agora_esszé_okos_szinten(k, kapu, monkeypatch):
    import plugins.agora_duty as ad
    sf, claude = k
    monkeypatch.setattr(ad, "_JELZES_HELYBEN", {})
    llm_kapu.sf_megszakito.kieses("payment")
    deps = {"siliconflow_api_key": "sf-kulcs-1", "siliconflow_models": server.SILICONFLOW_MODELS}
    _run(ad._sf_chat(deps, "glm53", "s", "esszé", max_tokens=6000, feladat="essay"))
    assert claude.hivasok[0]["model"] == "claude-opus-5-5"


def test_agora_ha_az_anthropic_sincs_a_perszona_kimarad_mint_eddig(k, kapu, monkeypatch):
    import plugins.agora_duty as ad
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    deps = {"siliconflow_api_key": "sf-kulcs-1", "siliconflow_models": server.SILICONFLOW_MODELS}
    with pytest.raises(ad.SzolgaltatoKieses) as e:
        _run(ad._sf_chat(deps, "deepseek", "s", "u", feladat="beat"))
    assert e.value.ok == "HTTP 402"


def test_agora_jelveny_minden_kimeno_kommentnel():
    import inspect
    import plugins.agora_duty as ad
    forras = inspect.getsource(ad)
    assert 'model=a["model_badge"]' not in forras and forras.count("model=_jelveny(a)") == 4


# ── 6. A vita: egy kimondott helyettesítő, nem nyolc egyforma Claude ─────────────

def test_vita_siliconflow_kieses_egy_helyettesito_hozzaszolas(k, monkeypatch, tmp_path):
    sf, claude = k
    db = tmp_path / "v.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE discussion_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, discussion_id INTEGER, "
                "instance TEXT, content TEXT, timestamp TEXT)")
    con.commit()
    con.close()

    def get_db():
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(server, "get_db", get_db)
    monkeypatch.setattr(server, "AI_DISCUSSION_ENABLED", True)
    _run(server._ai_auto_discuss(5, "Forint", "kommandant: mi legyen?"))
    sorok = get_db().execute("SELECT instance, content FROM discussion_entries").fetchall()
    assert len(sorok) == 1 and len(claude.hivasok) == 1
    assert sorok[0]["instance"].startswith("claude-") and "SILICONFLOW-KIESÉS" in sorok[0]["content"]


# ── 7. A családi chat streamje ───────────────────────────────────────────

def test_csaladi_chat_stream_402_utan_a_kapun_at_kimondva(k, kapu, monkeypatch):
    import httpx
    import youngereka_chat as yc

    class Valasz402:
        status_code = 402

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aread(self):
            return URES.encode()

    class StreamHamis:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, headers=None, json=None):
            return Valasz402()
    monkeypatch.setattr(httpx, "AsyncClient", StreamHamis)
    monkeypatch.setitem(yc._CFG, "api_key", "sf-kulcs-1")
    monkeypatch.setitem(yc._CFG, "base_url", "https://pelda.test/v1")
    monkeypatch.setitem(yc._CFG, "models", server.SILICONFLOW_MODELS)

    async def gyujt():
        return [x async for x in yc._call_model("kimi", "moonshotai/Kimi-K3.1-Pro",
                                                 [{"role": "user", "content": "Szia!"}])]
    elemek = dict(_run(gyujt()))
    assert elemek["kiszolgalo"].startswith("claude-") and elemek["delta"] == "Claude válasza."
    assert "helyettesítőként" in elemek["megjegyzes"] and "koltes_usd" in elemek["usage"]
    assert "error" not in elemek and llm_kapu.sf_megszakito.ok() == "payment"


# ── 8. A DELPHOI marad SiliconFlow-n ─────────────────────────────────────

def test_a_delphoi_motorja_402_nel_sem_megy_anthropicra(k, kapu, monkeypatch):
    from plugins import pollster
    _, claude = k

    async def tilos(kw):
        raise AssertionError("a DELPHOI-nak nincs Anthropic-útja")
    monkeypatch.setattr(llm_kapu, "_kuld", tilos)

    class Kliens:
        hivasok = 0

        async def post(self, url, json=None):
            Kliens.hivasok += 1
            assert "siliconflow" in url

            class R:
                status_code = 402

                def raise_for_status(self):
                    raise RuntimeError("HTTP 402")
            return R()
    with pytest.raises(RuntimeError, match="orakel motor"):
        _run(pollster._chat(Kliens(), "Mit gondol?", retries=2))
    assert Kliens.hivasok == 2


def test_a_delphoi_modulok_nem_hivjak_a_kaput():
    import pathlib
    gyoker = pathlib.Path(__file__).resolve().parent.parent
    modulok = sorted(gyoker.glob("plugins/delphoi*.py")) + [gyoker / f"plugins/{n}.py"
                                                              for n in ("pollster", "persona_sampler", "ssr")]
    for m in modulok:
        forras = m.read_text(encoding="utf-8")
        assert "llm_kapu" not in forras and "sf_chat" not in forras and "kapun(" not in forras, m.name


# ── 9. Az embedding: kimondott visszalépés (Anthropic embedding-API nincs) ─────────

def _triage_toolok(fake_app, get_db):
    import plugins.semantic_triage as st
    st._CENTROID_CACHE.clear()
    st.register_tools(fake_app, {"siliconflow_api_key": "sf-kulcs-1", "siliconflow_base_url": "https://pelda.test/v1",
                                 "siliconflow_models": {}, "get_db": get_db})
    return fake_app.tools


def test_embedding_nyitott_megszakitonal_kulcsszavas_kimondva(k, fake_app, get_db):
    sf, _ = k
    llm_kapu.sf_megszakito.kieses("payment")
    tools = _triage_toolok(fake_app, get_db)
    r = json.loads(_run(tools["semantic_triage"](sender="a@b.hu", subject="Sürgős: holnapig", body="")))
    assert r["method"] == "keyword_fallback" and "megszakito" in r["fallback_reason"]
    assert "embedding-API" in r["fallback_reason"] and not sf.hivasok


def test_embedding_402_kulcsszavas_okkal_es_a_megszakito_nyit(k, fake_app, get_db):
    sf, _ = k
    tools = _triage_toolok(fake_app, get_db)
    r = json.loads(_run(tools["semantic_triage"](sender="a@b.hu", subject="Számla", body="")))
    assert r["method"] == "keyword_fallback" and "HTTP 402" in r["fallback_reason"]
    assert sf.hivasok[0]["url"].endswith("/embeddings") and llm_kapu.sf_megszakito.ok() == "payment"


# ── 10. Az új célok szintje és név szerinti ügynöke ───────────────────────

@pytest.mark.parametrize("purpose,model,szint", [
    ("levelterv/draft_reply", "moonshotai/Kimi-K3.1-Pro", "kozep"),
    ("email_triage/besorolas", "deepseek-ai/DeepSeek-V4-Pro-0813", "olcso"),
    ("email_triage/hatareset", "deepseek-ai/DeepSeek-V4-Pro-0813", "kozep"),
    ("email_triage/valaszt_igenyel", "deepseek-ai/DeepSeek-V4-Pro-0813", "olcso"),
    ("email_triage/fw_riasztas", "deepseek-ai/DeepSeek-V4-Pro-0813", "olcso"),
    ("jelkapu/signal_gatekeeper", "deepseek-ai/DeepSeek-V4-Pro-0813", "kozep"),
    ("szal_osszegzes/thread_digest", "deepseek-ai/DeepSeek-V4-Pro-0813", "kozep"),
    ("kinyeres/action_extract", "deepseek-ai/DeepSeek-V4-Pro-0813", "olcso"),
    ("formazas/reggeli_osszefoglalo", "deepseek-ai/DeepSeek-V4-Pro-0813", "olcso"),
    ("kepleiras", "moonshotai/Kimi-K3.1-Pro", "kozep"),
    ("kepleiras/feltoltes", "moonshotai/Kimi-K3.1-Pro", "kozep"),
    ("agora/komment/kimi3", "moonshotai/Kimi-K3.1-Pro", "kozep"),
    ("agora/beat/deepseek", "deepseek-ai/DeepSeek-V4-Pro-0813", "kozep"),
    ("agora/essay/glm53", "zai-org/GLM-5.3", "okos"),
    ("vita/tartalek", "moonshotai/Kimi-K3.1-Pro", "kozep"),
    ("youngereka/beszelgetes/kimi", "moonshotai/Kimi-K3.1-Pro", "kozep"),
    ("youngereka/eszkoz/deepseek", "deepseek-ai/DeepSeek-V4-Pro-0813", "kozep"),
])
def test_az_uj_celok_szintje(purpose, model, szint):
    assert llm_kapu.szint_valaszt({"model": model, "messages": [{"role": "user", "content": "x" * 5000}]},
                                  purpose)[0] == szint


@pytest.mark.parametrize("purpose,ugynok", [
    ("agora/komment/kimi3", "kimi3"), ("agora/beat/deepseek", "deepseek"), ("bridge/mention/glm5", "glm5"),
    ("vita/glm53", "glm53"), ("ai_task/kimi", "kimi"),
    ("levelterv/draft_reply", None), ("email_triage/hatareset", None), ("kepleiras", None),
    ("jelkapu/signal_gatekeeper", None), ("formazas/reggeli_osszefoglalo", None),
])
def test_az_uj_celok_nevesitett_ugynoke(purpose, ugynok):
    assert llm_kapu.nevesitett_ugynok(purpose) == ugynok
