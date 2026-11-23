"""A CSONKOLÁS-LÉTRA (2026-11-23, task #594) — hálózat nélkül, hamis modellhívással.

A HIBA
------
A 594-es dispatch GLM-ügynökének végén ez állt: 28k → 56k → 112k → 131k kimeneti keret „csonkolt válasz” miatt,
aztán modellváltás (DeepSeek V4-Pro 32k, GLM-5.3-Flash 8k → 16k → 32k → 64k), végül „3. próbálkozásra sikerült”.
Egy ügynökre ~18 nagy hívás ment el 4 helyett (4 eszközkör), mindegyik a teljes promptot újraküldve.

AZ OK (MÉRVE 2026-11-23, élő SiliconFlow-próbával): nem a gondolkodás ette meg a keretet. A GLM-5.3, a GLM-5.3-Flash
és a DeepSeek V4-Pro-0813 ESZKÖZHÍVÁSA `finish_reason="tool_calls"`, ÜRES `content` és NEM üres `reasoning_content`
(GLM-5.3: 44 kimeneti token, ebből 26 gondolat). A `_sf_is_truncated` „üres content + van reasoning” jele ezt
csonkolásnak látta: az eszközhívást eldobta, a keretet megduplázta, újrakérte — és a modell megint eszközt hívott.

Amit ezek a tesztek őriznek:
  1. az eszközhívás NEM csonkolás (egy hívás, az eszközhívás megmarad),
  2. csonkolásnál LEGFELJEBB EGY újrapróbálás (egy bővítés, 2×),
  3. a bővítés teteje 32k (`llm_kapu.BOVITES_PLAFON`), a megtanult keret sem indít efölött,
  4. ha a gondolkodás ette meg a keretet, a második próba gondolkodás nélkül megy, UGYANAZZAL a kerettel,
  5. modellváltásnál a váltott modell is ugyanazt a plafont kapja (és ugyanazt az egy újrapróbálást),
  6. ami így is csonka, az KIMONDVA csonka — nem modellváltás, nem csendes siker,
  7. a költség látszik: a feladat számlája a hívásszámot és a tokent viszi, és a kimenet végére egy sor kerül,
  8. a 594-es forgatókönyv: 4 eszközkör = 4 hívás.
"""
import asyncio
import copy

import pytest

import llm_kapu
import server


def _run(coro):
    return asyncio.run(coro)


def _valasz(content="", reasoning="", finish="stop", tool_calls=None, be=1000, ki=100):
    msg = {"content": content, "reasoning_content": reasoning}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": be, "completion_tokens": ki}}


def _eszkozhivas(be=20000, ki=44):
    """MÉRT alak (GLM-5.3, 11-23): finish=tool_calls, üres content, rövid gondolat."""
    return _valasz(content="", reasoning="A felhasználó friss adatot kér, keresnem kell.", finish="tool_calls",
                   tool_calls=[{"id": "c1", "type": "function",
                                "function": {"name": "web_search", "arguments": '{"query": "EUR/HUF"}'}}],
                   be=be, ki=ki)


def _szoveg_csonka(ki=8000):
    return _valasz(content="hosszú válasz eleje " * 200, reasoning="rövid terv", finish="length", ki=ki)


def _gondolat_csonka(ki=8000):
    return _valasz(content="", reasoning="hosszan gondolkodom " * 500, finish="length", ki=ki)


@pytest.fixture
def sf(monkeypatch):
    """Hamis `_sf_post_once`: a válaszok sorban (dict = 200-as válasz, tuple = (None, hiba)); a kérések MÁSOLATBAN."""
    allapot = {"valaszok": [], "kert": []}

    async def post(client, body, key=""):
        allapot["kert"].append(copy.deepcopy(body))
        r = allapot["valaszok"].pop(0) if allapot["valaszok"] else _valasz("kész")
        if isinstance(r, tuple):
            return r
        d = copy.deepcopy(r)
        d.setdefault("model", body["model"])
        return d, None

    async def nem_alszik(_s):
        return None
    monkeypatch.setattr(server, "_sf_post_once", post)
    monkeypatch.setattr(server, "SILICONFLOW_API_KEY", "k")
    monkeypatch.setattr(server, "SILICONFLOW_API_KEY2", "", raising=False)
    monkeypatch.setattr(server, "_SF_LEARNED_BUDGET", {})
    monkeypatch.setattr(server, "_sf_budget_remember", lambda *a, **k: None)
    monkeypatch.setattr(llm_kapu, "elerheto", lambda: False)
    monkeypatch.setattr(asyncio, "sleep", nem_alszik)
    return allapot


def _chat(model, max_tokens=8000, **kw):
    payload = {"model": model, "messages": [{"role": "user", "content": "x"}], "max_tokens": max_tokens}
    payload.update(kw.pop("extra", {}))
    return _run(server.sf_chat(payload, purpose="teszt", **kw))


# ── 1. AZ ESZKÖZHÍVÁS NEM CSONKOLÁS ──────────────────────────────────────

def test_eszkozhivas_nem_csonkolas():
    assert server._sf_is_truncated(_eszkozhivas()) is False
    # a `finish_reason` nélkül is: ha van tool_calls, az eszközhívás
    d = _eszkozhivas()
    d["choices"][0]["finish_reason"] = "stop"
    assert server._sf_is_truncated(d) is False
    # a valódi csonkolás továbbra is az
    assert server._sf_is_truncated(_gondolat_csonka()) is True
    assert server._sf_is_truncated(_valasz("", "gondolat", finish="stop")) is True
    # félbevágott eszközhívás (length) is csonkolás
    d = _eszkozhivas()
    d["choices"][0]["finish_reason"] = "length"
    assert server._sf_is_truncated(d) is True


def test_eszkozhivas_egy_hivas_es_megmarad(sf):
    sf["valaszok"] = [_eszkozhivas()]
    data, err, notes = _chat("zai-org/GLM-5.3", max_tokens=8000)
    assert err is None and len(sf["kert"]) == 1, "az eszközhívást csonkolásnak nézte és újrakérte"
    assert data["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "web_search"
    assert not any("KEVÉS" in n or "csonk" in n.lower() for n in notes), notes


# ── 2. LEGFELJEBB EGY BŐVÍTÉS ────────────────────────────────────────────

def test_legfeljebb_egy_bovites(sf):
    sf["valaszok"] = [_szoveg_csonka()] * 10
    data, err, notes = _chat("zai-org/GLM-5.2", max_tokens=8000, attempts=6)
    assert [k["max_tokens"] for k in sf["kert"]] == [8000, 16000], "több mint egy bővítés"
    assert err is None and data is not None


# ── 3. A PLAFON ──────────────────────────────────────────────────────────

def test_a_bovites_teteje_32k(sf):
    sf["valaszok"] = [_szoveg_csonka()] * 10
    _chat("zai-org/GLM-5.3", max_tokens=24000, attempts=6)
    assert [k["max_tokens"] for k in sf["kert"]] == [24000, llm_kapu.BOVITES_PLAFON]


def test_a_plafonon_nincs_bovites(sf):
    sf["valaszok"] = [_szoveg_csonka()] * 10
    data, err, notes = _chat("zai-org/GLM-5.3", max_tokens=32000, attempts=6)
    assert [k["max_tokens"] for k in sf["kert"]] == [32000]
    assert any("PLAFONJÁN (32000) is csonkolt" in n for n in notes)


def test_a_megtanult_keret_sem_indit_a_plafon_folott(sf, monkeypatch):
    """A 594 után élesben a GLM-5.3 megtanult kerete 131 000 volt — minden hívás onnan indult."""
    monkeypatch.setattr(server, "_SF_LEARNED_BUDGET", {"zai-org/GLM-5.3": 131000})
    _chat("zai-org/GLM-5.3", max_tokens=8000)
    assert sf["kert"][0]["max_tokens"] == llm_kapu.BOVITES_PLAFON


def test_a_kifejezett_nagy_keres_marad_de_nem_bovul(sf):
    """A hívó kifejezett kérése (pl. a GLM-5.3-Flash 48k-ja) marad — csak a bővítés nem megy fölé."""
    sf["valaszok"] = [_szoveg_csonka()] * 10
    _chat("zai-org/GLM-5.3-Flash", max_tokens=48000, attempts=6)
    assert [k["max_tokens"] for k in sf["kert"]] == [48000]


def test_a_hivo_plafonja_tovabbra_is_vag(sf):
    sf["valaszok"] = [_szoveg_csonka()] * 10
    with llm_kapu.keret_plafon(16000):
        _run(server.sf_chat({"model": "zai-org/GLM-5.3", "messages": [], "max_tokens": 12000}, attempts=6))
    assert max(k["max_tokens"] for k in sf["kert"]) == 16000 and len(sf["kert"]) == 2


def test_a_megtanult_keret_betoltve_is_vagva(monkeypatch):
    monkeypatch.setattr(server, "_SF_LEARNED_BUDGET", {})

    class Sor(dict):
        pass

    class Conn:
        def execute(self, *a):
            return self

        def fetchone(self):
            return Sor(value='{"zai-org/GLM-5.3": 131000, "tencent/Hy3": 20000}')

        def close(self):
            pass
    monkeypatch.setattr(server, "get_db", lambda: Conn())
    server._sf_budget_load()
    assert server._SF_LEARNED_BUDGET == {"zai-org/GLM-5.3": llm_kapu.BOVITES_PLAFON, "tencent/Hy3": 20000}


# ── 4. GONDOLAT-CSONKOLÁS: A MÁSODIK PRÓBA GONDOLKODÁS NÉLKÜL ─────────────

def test_glm_masodik_proba_alacsony_effort_ugyanazzal_a_kerettel(sf):
    sf["valaszok"] = [_gondolat_csonka(), _valasz("kész válasz")]
    data, err, notes = _chat("zai-org/GLM-5.3", max_tokens=8000)
    elso, masodik = sf["kert"]
    assert masodik["max_tokens"] == elso["max_tokens"] == 8000, "a gondolat-csonkolásra nagyobb keretet kért"
    assert masodik.get("reasoning_effort") == "low" and "thinking" not in masodik
    assert data["choices"][0]["message"]["content"] == "kész válasz"
    assert any("GONDOLKODÁS ette meg" in n for n in notes)


def test_deepseek_masodik_proba_gondolkodas_kikapcsolva(sf):
    """MÉRVE: a V4-Pro-0813 `reasoning_effort=low`-val is gondolkodik — a `thinking=disabled` kapcsolja ki."""
    sf["valaszok"] = [_gondolat_csonka(), _valasz("kész")]
    _chat("deepseek-ai/DeepSeek-V4-Pro-0813", max_tokens=16000, extra={"reasoning_effort": "medium"})
    elso, masodik = sf["kert"]
    assert elso["reasoning_effort"] == "medium"
    assert masodik["thinking"] == {"type": "disabled"} and "reasoning_effort" not in masodik
    assert masodik["max_tokens"] == 16000


def test_gondolat_csonkolas_utan_sincs_tobb_proba(sf):
    sf["valaszok"] = [_gondolat_csonka()] * 10
    data, err, notes = _chat("zai-org/GLM-5.3", max_tokens=8000, attempts=6)
    assert len(sf["kert"]) == 2 and err is None
    assert any("CSONKA" in n for n in notes)


def test_ha_mar_gondolkodas_nelkul_ment_akkor_bovit(sf):
    """A Kimi már `thinking=disabled`-del megy (a dispatch-út): ott a második próba a bővítés."""
    sf["valaszok"] = [_gondolat_csonka()] * 10
    _chat("moonshotai/Kimi-K3.1-Pro", max_tokens=8000, extra={"thinking": {"type": "disabled"}})
    assert [k["max_tokens"] for k in sf["kert"]] == [8000, 16000]


# ── 5. MODELLVÁLTÁSNÁL UGYANAZ A PLAFON ──────────────────────────────────

def test_modellvaltasnal_ugyanaz_a_plafon(sf, monkeypatch):
    monkeypatch.setattr(server, "_SF_LEARNED_BUDGET", {"zai-org/GLM-5.3-Flash": 131000})
    kieses = (None, {"code": "server_error", "message": "HTTP 503"})
    sf["valaszok"] = [kieses] * 3 + [_szoveg_csonka()] * 10
    data, err, notes = _chat("zai-org/GLM-5.3", max_tokens=8000, attempts=3)
    tartalek = [k for k in sf["kert"] if k["model"] != "zai-org/GLM-5.3"]
    assert {k["model"] for k in tartalek} == {"zai-org/GLM-5.3-Flash"}, "a csonkolás miatt még egy modellt hívott"
    assert len(tartalek) == 1, "a váltott modell a plafonon kezdett, nem bővíthet"
    assert max(k["max_tokens"] for k in tartalek) <= llm_kapu.BOVITES_PLAFON
    assert err is None and any("MODELLVÁLTÁS" in n for n in notes) and any("CSONKA" in n for n in notes)


def test_modellvaltasnal_is_csak_egy_bovites(sf):
    kieses = (None, {"code": "server_error", "message": "HTTP 503"})
    sf["valaszok"] = [kieses] * 3 + [_szoveg_csonka()] * 10
    _chat("zai-org/GLM-5.3", max_tokens=8000, attempts=3)
    tartalek = [k["max_tokens"] for k in sf["kert"] if k["model"] != "zai-org/GLM-5.3"]
    assert tartalek == [8000, 16000]


# ── 6. A CSONKA JELZÉS ───────────────────────────────────────────────────

def test_a_csonka_valasz_kimondva_csonka_es_nem_valt_modellt(sf):
    sf["valaszok"] = [_szoveg_csonka()] * 10
    data, err, notes = _chat("zai-org/GLM-5.3", max_tokens=8000, attempts=3)   # failover bekapcsolva
    assert {k["model"] for k in sf["kert"]} == {"zai-org/GLM-5.3"}
    assert data is not None and err is None
    assert any("CSONKA" in n for n in notes)
    assert "CSONKA" in server._with_sf_notes("szöveg", notes)


def test_ha_az_ujraprobalas_elbukik_a_csonka_valasz_megy(sf):
    sf["valaszok"] = [_szoveg_csonka(), (None, {"code": "bad_request", "message": "HTTP 400"})]
    data, err, notes = _chat("zai-org/GLM-5.3", max_tokens=8000, attempts=3)
    assert err is None and data["choices"][0]["finish_reason"] == "length"
    assert len(sf["kert"]) == 2
    assert any("korábbi CSONKA válasz" in n for n in notes)


def test_egy_probaval_nincs_ujraprobalas_de_jelzes_van(sf):
    sf["valaszok"] = [_szoveg_csonka()] * 3
    data, err, notes = _chat("zai-org/GLM-5.3", max_tokens=8000, attempts=1)
    assert len(sf["kert"]) == 1 and data is not None and any("CSONKA" in n for n in notes)


# ── 7. A KÖLTSÉG LÁTSZIK ─────────────────────────────────────────────────

def test_a_szamla_szamol_hivast_tokent_arat(sf):
    sf["valaszok"] = [_valasz("x", finish="length", ki=8000, be=20000), _valasz("kész", be=20000, ki=900),
                      ]
    with llm_kapu.hivas_szamla() as feladat:
        with llm_kapu.hivas_szamla() as ugynok:
            _chat("zai-org/GLM-5.3", max_tokens=8000)
    assert (ugynok.hivas, ugynok.be, ugynok.ki) == (2, 40000, 8900)
    assert (feladat.hivas, feladat.be, feladat.ki) == (2, 40000, 8900), "az alszámla a feladatéba nem könyvelt"
    be, ki = llm_kapu.sf_ar("zai-org/GLM-5.3")
    assert feladat.usd == pytest.approx((40000 * be + 8900 * ki) / 1e6)
    assert llm_kapu.HIVAS_SZAMLA.get() is None, "a számla kiszivárgott"


def test_a_hibas_probalkozas_kulon_latszik(sf):
    sf["valaszok"] = [(None, {"code": "rate_limit", "message": "HTTP 429"}), _valasz("kész")]
    with llm_kapu.hivas_szamla() as sz:
        _chat("zai-org/GLM-5.2")
    assert sz.hivas == 1 and sz.hiba == 1
    assert "1 sikertelen" in sz.sor()


def test_a_koltseg_sor_a_jegyzet_blokkba_kerul():
    sz = llm_kapu.Szamla()
    sz.ir(be=405_120, ki=7_225, usd=1.2345)
    kimenet = server._koltseg_jegyzet(server._with_sf_notes("válasz", ["modellváltás"]), sz)
    assert kimenet.count("\n\n---\n") == 1
    assert kimenet.endswith("_[KÖLTSÉG: 1 modellhívás, 412 345 token (be 405 120 / ki 7 225) ≈ $1.234 (becslés)]_")
    # jegyzet nélküli szövegnél új blokk
    assert server._koltseg_jegyzet("válasz", sz).startswith("válasz\n\n---\n_[KÖLTSÉG")
    # üres számla: nincs sor
    assert server._koltseg_jegyzet("válasz", llm_kapu.Szamla()) == "válasz"


def test_az_anthropic_ag_is_a_szamlara_ir(monkeypatch):
    """A `sf_chat` Anthropic-ága (`llm_kapu.chat`) maga könyvel — a SiliconFlow-ág nem írja még egyszer."""
    from anthropic.types.beta import BetaMessage
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt")
    monkeypatch.setattr(llm_kapu, "_KOLTES", {"honap": "", "usd": 0.0, "hivasok": 0, "szintenkent": {}})

    async def kuld(kw):
        return BetaMessage.model_validate({
            "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": "kész"}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 50, "cache_read_input_tokens": 200,
                      "cache_creation_input_tokens": 0}})
    monkeypatch.setattr(llm_kapu, "_kuld", kuld)
    with llm_kapu.hivas_szamla() as sz:
        _run(llm_kapu.chat({"model": "x", "messages": [{"role": "user", "content": "x"}]}, purpose="teszt"))
    assert (sz.hivas, sz.be, sz.ki) == (1, 1200, 50) and sz.usd > 0


# ── 8. A 594-ES FORGATÓKÖNYV: 4 ESZKÖZKÖR = 4 HÍVÁS ──────────────────────

def test_594_forgatokonyv_negy_kor_negy_hivas(sf, monkeypatch):
    """A dispatch GLM-ügynöke (`_run_agent_with_tools`, 4 kör): háromszor eszközt hív, aztán válaszol.
    Régen: minden eszközhívás „csonkolás” volt → ~18 hívás, 131k-s keret, két modellváltás. Most: 4 hívás."""
    async def defs():
        return [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}]

    async def eszkoz(nev, args):
        return "eredmény"
    monkeypatch.setattr(server, "subagent_tool_defs", defs)
    monkeypatch.setattr(server, "_dispatch_subagent_tool", eszkoz)
    sf["valaszok"] = [_eszkozhivas(), _eszkozhivas(), _eszkozhivas(), _valasz("a végső elemzés", ki=6000)]
    with llm_kapu.hivas_szamla() as feladat:
        szoveg = _run(server._eszkozos_ugynok_koltseggel("glm5", [{"role": "user", "content": "x"}],
                                                         max_rounds=4, max_tokens=8000))
    assert len(sf["kert"]) == 4, f"{len(sf['kert'])} hívás 4 helyett"
    assert {k["model"] for k in sf["kert"]} == {"zai-org/GLM-5.3"}, "modellt váltott"
    assert max(k["max_tokens"] for k in sf["kert"]) == 8000, "keretet emelt"
    assert szoveg.startswith("a végső elemzés") and "_[KÖLTSÉG: 4 modellhívás" in szoveg
    assert feladat.hivas == 4


# ── 9. KIMI K2: A VALÓDI GONDOLAT-KAPCSOLÓ (MÉRVE 2026-11-24) ────────────
# Élő próba (2000-es keret, ugyanarra a szintézis-kérésre): a Kimi-K2.7-Code a `thinking=disabled`-et figyelmen kívül
# hagyja (728 gondolat-token, mint paraméter nélkül), 600-as keretnél a gondolat a content-be ömlött és csonkolt — a
# létra ezért bővített (a szintézis 16k → 32k-ja). A `thinking_budget` ÖNMAGÁBAN működik (128 → pontosan 128).

def test_kimi_k2_thinking_disabled_helyett_thinking_budget(sf):
    _chat("moonshotai/Kimi-K2.7-Code", extra={"thinking": {"type": "disabled"}, "enable_thinking": False,
                                               "reasoning_effort": "low"})
    [k] = sf["kert"]
    assert k["thinking_budget"] == server.KIMI_K2_GONDOLAT_KI == 128
    assert not {"thinking", "enable_thinking", "reasoning_effort"} & set(k), "a hatástalan kapcsoló is kiment"


def test_kimi_k2_a_szintezis_utja_is_keretet_kap(sf):
    """A szintézis a `_model_extra`-ból kapja a kapcsolót (`thinking=disabled`) — a tölcsér fordítja le."""
    assert server._model_extra("moonshotai/Kimi-K2.7-Code") == {"thinking": {"type": "disabled"}}
    _chat("moonshotai/Kimi-K2.7-Code", extra=server._model_extra("moonshotai/Kimi-K2.7-Code"))
    assert sf["kert"][0]["thinking_budget"] == 128 and "thinking" not in sf["kert"][0]
    # kifejezett bekapcsolásnál nincs keret
    _chat("moonshotai/Kimi-K2.7-Code", extra={"thinking": {"type": "enabled"}})
    assert "thinking_budget" not in sf["kert"][1] and "thinking" not in sf["kert"][1]


def test_mas_kimi_marad_thinking_disabled(sf):
    """A K3.1 Pro-nál a `thinking=disabled` MŰKÖDIK (mérve 11-22): ahhoz nem nyúlunk."""
    _chat("moonshotai/Kimi-K3.1-Pro", extra={"thinking": {"type": "disabled"}})
    assert sf["kert"][0]["thinking"] == {"type": "disabled"} and "thinking_budget" not in sf["kert"][0]


def test_kimi_k2_gondolat_csonkolasnal_a_kisebb_gondolat_keret_jon(sf):
    sf["valaszok"] = [_gondolat_csonka(), _valasz("kész")]
    _chat("moonshotai/Kimi-K2.7-Code", max_tokens=8000, extra={"thinking_budget": 4096})
    elso, masodik = sf["kert"]
    assert elso["thinking_budget"] == 4096
    assert masodik["thinking_budget"] == 128 and masodik["max_tokens"] == 8000 and "thinking" not in masodik


def test_kimi_k2_minimalis_gondolat_keretnel_a_masodik_proba_bovites(sf):
    sf["valaszok"] = [_szoveg_csonka()] * 10
    _chat("moonshotai/Kimi-K2.7-Code", max_tokens=8000, extra={"thinking": {"type": "disabled"}}, attempts=6)
    assert [k["max_tokens"] for k in sf["kert"]] == [8000, 16000]
    assert all(k["thinking_budget"] == 128 and "thinking" not in k for k in sf["kert"])


def test_modellvaltas_kimi_k2_re_is_a_valodi_kapcsolo(sf):
    kieses = (None, {"code": "server_error", "message": "HTTP 503"})
    sf["valaszok"] = [kieses] * 3 + [_valasz("kész")]
    _chat("deepseek-ai/DeepSeek-V4-Pro", extra={"reasoning_effort": "medium"}, attempts=3)
    [t] = [k for k in sf["kert"] if k["model"] == "moonshotai/Kimi-K2.7-Code"]
    assert t["thinking_budget"] == 128 and "reasoning_effort" not in t and "thinking" not in t


def test_a_szintezis_plafonja_16k_es_nem_bovit_32k_ra(sf, monkeypatch):
    """A szintézis a `keret_plafon(SZINTEZIS_PLAFON)` alatt fut: a megtanult 32k-ról sem indul, és nem bővít fölé."""
    import inspect
    monkeypatch.setattr(server, "_SF_LEARNED_BUDGET", {"moonshotai/Kimi-K2.7-Code": 32000})
    sf["valaszok"] = [_szoveg_csonka()] * 10
    with llm_kapu.keret_plafon(server.SZINTEZIS_PLAFON):
        data, err, notes = _chat("moonshotai/Kimi-K2.7-Code", max_tokens=8000, attempts=6,
                                 extra={"thinking": {"type": "disabled"}})
    assert [k["max_tokens"] for k in sf["kert"]] == [16000]
    assert any("PLAFONJÁN (16000) is csonkolt" in n for n in notes)
    # mindkét szintézis-lánc (broadcast és dispatch) a plafon alatt hívja a szintetizálót
    forras = inspect.getsource(server._execute_ai_task) + inspect.getsource(inspect.unwrap(server.ai_task))
    assert forras.count("keret_plafon(SZINTEZIS_PLAFON)") == 2
