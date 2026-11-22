"""AGORA NE BUKJON NÉMÁN (plugins/agora_duty.py) — ha a SiliconFlow kifogy (402), 5xx-et ad vagy nem felel.

1. A `_sf_chat` a kiesést típusosan jelzi (`SzolgaltatoKieses`): 402-nél újrapróba nélkül, 5xx-nél egy újrapróba
   után, időtúllépésnél és kapcsolati hibánál is; a többi hiba marad a régi RuntimeError.
2. A szolgálati kör kihagyja a perszónát (a többié megy), a kimaradás a kimaradt-futás naplóba kerül
   (`recipe_skips`, reason_code=llm_provider_unavailable) és az agora_activity-be (`skip_llm`).
3. Telegramra EGY jelzés megy, 24 órás ismétlés-tiltással a shared_memory-ben (deploy-újraindulás után sem ismétel).
4. A következő kör magától újrapróbál (nincs tiltó állapot); az esszé ugyanígy.
5. Kimi K3: a `temperature` kimarad a kérésből (a K3 400/20015-tel dobja).
Hálózat nincs: a SiliconFlow, az Echolot és a Telegram hamisítvány.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import httpx
import pytest

import plugins.agora_duty as ad
import recipe_health
from plugins._agora_personas import AGORA_AGENTS


def _run(c):
    return asyncio.run(c)


# ── 1. _sf_chat ──────────────────────────────────────────────────────────

class _Valasz:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body) if isinstance(body, dict) else str(body)

    def json(self):
        if not isinstance(self._body, dict):
            raise ValueError("nem JSON")
        return self._body


@pytest.fixture
def sf(monkeypatch):
    """Hamis SiliconFlow: a `valaszok` sorban jönnek (Exception → dobja), a kérések a `kuldott`-ba."""
    kuldott, valaszok = [], []

    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            kuldott.append(json)
            v = valaszok.pop(0) if len(valaszok) > 1 else valaszok[0]
            if isinstance(v, Exception):
                raise v
            return v

    async def nincs_alvas(*a, **k):
        return None
    monkeypatch.setattr(httpx, "AsyncClient", C)
    monkeypatch.setattr(ad.asyncio, "sleep", nincs_alvas)
    return kuldott, valaszok


DEPS = {"siliconflow_api_key": "k", "siliconflow_models": {"deepseek": "deepseek-ai/DeepSeek-V4-Pro",
                                                            "kimi3": "moonshotai/Kimi-K3.1-Pro",
                                                            "kimi": "moonshotai/Kimi-K2.7-Code"}}
_OK = _Valasz(200, {"choices": [{"message": {"content": "Rendben."}, "finish_reason": "stop"}]})


def test_402_azonnal_kieses_ujraproba_nelkul(sf):
    kuldott, valaszok = sf
    valaszok.append(_Valasz(402, {"code": 30001, "message": "balance insufficient"}))
    with pytest.raises(ad.SzolgaltatoKieses) as e:
        _run(ad._sf_chat(DEPS, "deepseek", "s", "u"))
    assert e.value.ok == "HTTP 402" and "balance insufficient" in e.value.reszlet and len(kuldott) == 1
    assert isinstance(e.value, RuntimeError) and "SF chat failed (deepseek)" in str(e.value)


def test_5xx_egy_ujraproba_utan_kieses(sf):
    kuldott, valaszok = sf
    valaszok.append(_Valasz(503, "<html>Service Unavailable</html>"))
    with pytest.raises(ad.SzolgaltatoKieses, match="HTTP 503"):
        _run(ad._sf_chat(DEPS, "deepseek", "s", "u"))
    assert len(kuldott) == 2


def test_5xx_utan_ha_magahoz_ter_megy(sf):
    _, valaszok = sf
    valaszok += [_Valasz(502, "bad gateway"), _OK]
    assert _run(ad._sf_chat(DEPS, "deepseek", "s", "u")) == "Rendben."


@pytest.mark.parametrize("hiba,ok", [(httpx.ReadTimeout("lassú"), "időtúllépés"),
                                     (httpx.ConnectError("nincs út"), "nincs kapcsolat")])
def test_idotullepes_es_kapcsolati_hiba_is_kieses(sf, hiba, ok):
    kuldott, valaszok = sf
    valaszok.append(hiba)
    with pytest.raises(ad.SzolgaltatoKieses) as e:
        _run(ad._sf_chat(DEPS, "deepseek", "s", "u"))
    assert e.value.ok == ok and len(kuldott) == 2


def test_400_nem_kieses_hanem_a_regi_hiba(sf):
    _, valaszok = sf
    valaszok.append(_Valasz(400, {"code": 20015, "message": "bad param"}))
    with pytest.raises(RuntimeError) as e:
        _run(ad._sf_chat(DEPS, "deepseek", "s", "u"))
    assert not isinstance(e.value, ad.SzolgaltatoKieses) and "SF HTTP 400" in str(e.value)


def test_kimi_k3_temperature_nelkul(sf):
    kuldott, valaszok = sf
    valaszok.append(_OK)
    _run(ad._sf_chat(DEPS, "kimi3", "s", "u", temperature=0.7))
    _run(ad._sf_chat({**DEPS, "siliconflow_models": {"x": "moonshotai/Kimi-K3.1-Pro"}}, "x", "s", "u"))
    _run(ad._sf_chat(DEPS, "kimi", "s", "u", temperature=0.7))
    _run(ad._sf_chat(DEPS, "deepseek", "s", "u", temperature=0.2))
    assert "temperature" not in kuldott[0] and kuldott[0]["model"] == "moonshotai/Kimi-K3.1-Pro"
    assert "temperature" not in kuldott[1]
    assert kuldott[2]["temperature"] == 0.7 and kuldott[3]["temperature"] == 0.2     # a K2.7 és a többi marad


# ── 2-4. A szolgálati kör ────────────────────────────────────────────────

@pytest.fixture
def kor(monkeypatch, tmp_path):
    p = tmp_path / "agora.db"

    def get_db():
        c = sqlite3.connect(p)
        c.row_factory = sqlite3.Row
        return c

    c = get_db()
    c.executescript(ad._INIT_SQL)
    c.execute("CREATE TABLE shared_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, "
              "value TEXT NOT NULL, category TEXT DEFAULT 'general', tags TEXT DEFAULT '', updated_by TEXT, "
              "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    for a in AGORA_AGENTS.values():
        c.execute("INSERT INTO shared_memory (key, value, created_at, updated_at) VALUES (?, 'op-teszt', 'x', 'x')",
                  (a["memory_key"],))
    c.commit()
    c.close()
    recipe_health.ensure_schema(get_db())

    naplo = {"push": [], "posztolt": [], "general": []}
    allapot = {"kieses": {"von_takt": ad.SzolgaltatoKieses("kimi3", "HTTP 402", "balance insufficient")},
               "beat_kieses": None}

    async def push(s):
        naplo["push"].append(s)

    def memoria_ir(conn, key, value, *, category="general", tags="", updated_by="unknown"):
        conn.execute("INSERT INTO shared_memory (key, value, category, tags, updated_by, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, 'most', 'most') ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, value, category, tags, updated_by))

    async def collect_stories(limit=10, story_url=""):
        return [{"story_id": "s1", "languages": ["hu"], "title": "Gyengül a forint, emelkedik az infláció",
                 "markdown": "…"}]

    async def beat_match(deps, stories):
        if allapot["beat_kieses"]:
            raise allapot["beat_kieses"]
        return {k: [{"story_id": "s1", "score": 9, "query": "forint"}] for k in AGORA_AGENTS}

    async def prefetch(agent_key, query, preset=""):
        return "", {}

    async def media(*a, **k):
        return {"media_id": "", "image_md": "", "alt": "", "key_number": "", "status": "skip"}

    async def generate_comment(deps, agent_key, story, tool_block, lang, retry_reason="", chart_note=""):
        naplo["general"].append(agent_key)
        if agent_key in allapot["kieses"]:
            raise allapot["kieses"][agent_key]
        return "A forint gyengülése mögött a kamatkülönbözet szűkülése áll, és ezt a piac már be is árazta."

    class EC:
        async def post_comment(self, **kw):
            naplo["posztolt"].append(kw.get("agent_label"))
            return {"comment_id": f"c{len(naplo['posztolt'])}"}

    monkeypatch.setattr(ad, "collect_stories", collect_stories)
    monkeypatch.setattr(ad, "beat_match", beat_match)
    monkeypatch.setattr(ad, "prefetch_beat_data", prefetch)
    monkeypatch.setattr(ad, "prepare_chart_media", media)
    monkeypatch.setattr(ad, "generate_comment", generate_comment)
    monkeypatch.setattr(ad, "_echolot", lambda: EC())
    monkeypatch.setattr(ad, "_JELZES_HELYBEN", {})
    deps = {"get_db": get_db, "telegram_push": push, "memoria_ir": memoria_ir}
    return deps, naplo, allapot


def _skipek(get_db):
    c = get_db()
    try:
        return [dict(r) for r in c.execute("SELECT recipe, reason_code, trigger, detail_json FROM recipe_skips")]
    finally:
        c.close()


def _aktivitas(get_db, kind):
    c = get_db()
    try:
        return [dict(r) for r in c.execute("SELECT agent, detail FROM agora_activity WHERE kind = ?", (kind,))]
    finally:
        c.close()


def test_kieses_a_perszonat_kihagyja_naplozza_es_egyszer_szol(kor):
    deps, naplo, _ = kor
    rep = _run(ad.run_agora_duty(deps, recipe="agora_duty_morning", trigger="cron", do_reactions=False))
    assert rep["agents"]["von_takt"]["action"] == "kimaradt: HTTP 402" and rep["kimaradt"] == ["von_takt"]
    assert rep["agents"]["frau_lupe"]["action"] == "posted" and rep["agents"]["der_kartograph"]["action"] == "posted"
    assert naplo["general"].count("von_takt") == 1                 # nem próbálja végig a többi jelöltjét
    sk = _skipek(deps["get_db"])
    assert len(sk) == 1 and sk[0]["recipe"] == "agora_duty_morning" and sk[0]["trigger"] == "cron"
    assert sk[0]["reason_code"] == "llm_provider_unavailable"
    d = json.loads(sk[0]["detail_json"])
    assert d["dead_tools"] == ["siliconflow/von_takt"] and "balance insufficient" in d["message"]
    assert "újrapróbálja" in d["message"]
    assert [a["agent"] for a in _aktivitas(deps["get_db"], "skip_llm")] == ["von_takt"]
    assert len(naplo["push"]) == 1 and "HTTP 402" in naplo["push"][0] and "Von Takt" in naplo["push"][0]
    assert "tölts fel" in naplo["push"][0]
    # a get_status ugyanebből olvas
    assert recipe_health.recent_skips(deps["get_db"])[0]["reason_code"] == "llm_provider_unavailable"


def test_24_oran_belul_nem_szol_ujra_deploy_utan_sem_de_utana_igen(kor):
    deps, naplo, _ = kor
    _run(ad.run_agora_duty(deps, recipe="agora_duty_morning", trigger="cron", do_reactions=False))
    ad._JELZES_HELYBEN.clear()                                     # „deploy-újraindulás”: a folyamat memóriája üres
    _run(ad.run_agora_duty(deps, recipe="agora_duty_noon", trigger="cron", do_reactions=False))
    assert len(naplo["push"]) == 1 and len(_skipek(deps["get_db"])) == 2      # a napló minden kimaradást rögzít
    c = deps["get_db"]()
    c.execute("UPDATE shared_memory SET value = ? WHERE key = ?",
              (json.dumps({"ido": time.time() - 25 * 3600}), "onjavitas_jelzes_" + ad.KIESES_JELZES_KULCS))
    c.commit()
    c.close()
    ad._JELZES_HELYBEN.clear()
    _run(ad.run_agora_duty(deps, recipe="agora_duty_evening", trigger="cron", do_reactions=False))
    assert len(naplo["push"]) == 2


def test_a_kovetkezo_kor_magatol_ujraprobal(kor):
    deps, naplo, allapot = kor
    _run(ad.run_agora_duty(deps, recipe="agora_duty_morning", trigger="cron", do_reactions=False))
    assert "Von Takt" not in naplo["posztolt"]
    allapot["kieses"] = {}                                         # feltöltöttek
    rep = _run(ad.run_agora_duty(deps, recipe="agora_duty_noon", trigger="cron", do_reactions=False))
    assert rep["agents"]["von_takt"]["action"] == "posted" and "Von Takt" in naplo["posztolt"]
    assert "kimaradt" not in rep


def test_beat_match_kiesese_minden_perszonat_kihagy_egy_jelzessel(kor):
    deps, naplo, allapot = kor
    allapot["beat_kieses"] = ad.SzolgaltatoKieses("deepseek", "HTTP 503", "upstream")
    rep = _run(ad.run_agora_duty(deps, recipe="agora_duty_morning", trigger="cron"))
    assert rep["status"] == "llm_kieses: HTTP 503" and naplo["general"] == []
    assert sorted(s["recipe"] for s in _skipek(deps["get_db"])) == ["agora_duty_morning"] * len(AGORA_AGENTS)
    assert len(naplo["push"]) == 1 and "átmeneti" in naplo["push"][0]
    assert all(a["label"] in naplo["push"][0] for a in AGORA_AGENTS.values())


def test_essze_kiesese(kor, monkeypatch):
    deps, naplo, _ = kor

    async def sf_chat(*a, **k):
        raise ad.SzolgaltatoKieses("kimi3", "időtúllépés", "ReadTimeout 220 s után")
    monkeypatch.setattr(ad, "_sf_chat", sf_chat)
    rep = _run(ad.run_agora_essay(deps, "von_takt", trigger="cron"))
    assert rep["status"].startswith("kimaradt: időtúllépés")
    sk = _skipek(deps["get_db"])
    assert len(sk) == 1 and sk[0]["recipe"] == "agora_essay_von_takt" and sk[0]["trigger"] == "cron"
    assert len(_aktivitas(deps["get_db"], "skip_llm")) == 1       # egy nyom, nem kettő
    assert len(naplo["push"]) == 1 and "időtúllépés" in naplo["push"][0]


def test_jelzes_telegram_nelkul_nem_dob(kor):
    deps, naplo, _ = kor
    deps = {k: v for k, v in deps.items() if k != "telegram_push"}
    rep = _run(ad.run_agora_duty(deps, recipe="agora_duty_morning", do_reactions=False))
    assert rep["kimaradt"] == ["von_takt"] and len(_skipek(deps["get_db"])) == 1


def test_cron_entry_atadja_a_recept_nevet(monkeypatch):
    latott = {}

    async def run(d, **k):
        latott.update(k)
        return {"agents": {}}

    async def nincs_alvas(*a, **k):
        return None
    monkeypatch.setattr(ad, "run_agora_duty", run)
    monkeypatch.setattr(ad.asyncio, "sleep", nincs_alvas)
    _run(ad.cron_entry("agora_duty_evening", {"get_db": lambda: None}))
    assert latott == {"dry_run": False, "recipe": "agora_duty_evening", "trigger": "cron"}


def test_llm_kieses_verdikt_uzenete():
    v = recipe_health.llm_kieses_verdikt("agora_duty_noon", "siliconflow/frau_lupe", "HTTP 402: balance insufficient")
    d = v.to_dict()
    assert d["status"] == "skipped" and d["reason_code"] == "llm_provider_unavailable"
    assert d["message"] == ("'agora_duty_noon' KIMARADT: az LLM-szolgáltató nem szolgált ki — siliconflow/frau_lupe "
                            "(HTTP 402: balance insufficient). A következő ütemezett kör magától újrapróbálja.")
