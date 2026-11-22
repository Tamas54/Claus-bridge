"""ANTHROPIC-KAPU (2026-11-22): szintválasztás, fordítóréteg, költés, bekötés — hálózat nélkül.

A HELYZET, AMI EZT KIVÁLTOTTA
-----------------------------
2026-11-20: mindkét SiliconFlow-kulcs 402 (elfogyott az egyenleg), és a Bridge minden LLM-útja megállt. A Kommandant
Anthropic-kulcsa lett az „agy”: okos munkára okos modell, olcsó munkára olcsó — és ha az egyik szolgáltató kiesik,
a másik veszi át, KIMONDVA (néma modellváltás nincs).

Amit ezek a tesztek őriznek:
  1. a szintválasztás a táblázat szerint dönt (cél, kért SF-modell, méret),
  2. a Bridge OpenAI-szerű payloadja helyes Anthropic-hívássá fordul (system, tool_use, tool_result), és vissza,
  3. a tool-use hurok második köre a gondolat-blokkot pontosan visszaküldi,
  4. a refusal, a csonkolás és a szerveroldali tartalék nem néma,
  5. a havi költés számolódik, és a keret fölött a kapu nem hív,
  6. a `sf_chat` a kapura van kötve, mindkét irányú tartalékkal — kulcs nélkül pedig minden a régi úton megy.

Az Anthropic-hívást (`llm_kapu._kuld` / `_parse`) mindenhol hamisítjuk.
"""
import asyncio
import json

import pytest
from anthropic.types.beta import BetaMessage

import llm_kapu as k


def _run(coro):
    return asyncio.run(coro)


def _msg(content, stop="end_turn", model="claude-opus-5-5", usage=None, **extra):
    return BetaMessage.model_validate({
        "id": "msg_teszt", "type": "message", "role": "assistant", "model": model,
        "content": content, "stop_reason": stop, "stop_sequence": None,
        "usage": usage or {"input_tokens": 1000, "output_tokens": 100,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        **extra,
    })


class FakeAnthropic:
    """Sorban adja a válaszokat (vagy kivételt), és naplózza a hívás-kwargokat (MÁSOLATBAN)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, kw):
        self.calls.append(json.loads(json.dumps(kw, default=str)))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def _tiszta_allapot(monkeypatch):
    monkeypatch.setattr(k, "_KOLTES", {"honap": "", "usd": 0.0, "hivasok": 0, "szintenkent": {}})
    k._GONDOLAT_TAR.clear()
    yield
    k._GONDOLAT_TAR.clear()


@pytest.fixture
def kulcs(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt-TITOK")


def _wire(monkeypatch, responses):
    fake = FakeAnthropic(responses)
    monkeypatch.setattr(k, "_kuld", fake)
    return fake


SYSTEM = {"role": "system", "content": "Te a Bridge al-ügynöke vagy."}


# ── 1. SZINTVÁLASZTÁS ────────────────────────────────────────────────────

NAGY_PROMPT = "x" * 10_000


@pytest.mark.parametrize("purpose,model,prompt,tools,elvart", [
    ("ai_query/kimi/synth", "moonshotai/Kimi-K2.7-Code", NAGY_PROMPT, False, "okos"),      # cél: synth
    ("market_brief/economic", "deepseek-ai/DeepSeek-V4-Flash", NAGY_PROMPT, False, "okos"),  # cél: brief
    ("dispatch/glm5", "zai-org/GLM-5.2", NAGY_PROMPT, True, "okos"),                       # cél: dispatch
    ("agora/essay", "zai-org/GLM-5.2", NAGY_PROMPT, False, "okos"),
    ("delphoi/nowcast", "zai-org/GLM-5.2", NAGY_PROMPT, False, "okos"),
    ("feldwebel/telegram", "deepseek-ai/DeepSeek-V4-Pro", NAGY_PROMPT, True, "kozep"),     # a Feldwebel, hosszú kérés
    ("feldwebel/telegram", "deepseek-ai/DeepSeek-V4-Pro", "Szia Claus, mi a helyzet?", True, "olcso"),  # hétköznapi
    ("feldwebel/telegram", "deepseek-ai/DeepSeek-V4-Pro", "Elemezd a mai briefet", True, "kozep"),      # mélyebb
    ("feldwebel/telegram", "deepseek-ai/DeepSeek-V4-Pro", "gondold végig alaposan, mit tegyünk", True, "kozep"),
    ("telegram/chat/kimi", "moonshotai/Kimi-K2.7-Code", NAGY_PROMPT, False, "olcso"),      # rövid chat
    ("szandek/telegram", "", "élesítsd", False, "olcso"),
    ("email/triage", "zai-org/GLM-5.2", NAGY_PROMPT, False, "olcso"),
    ("fordítás/hu-en", "zai-org/GLM-5.2", NAGY_PROMPT, False, "olcso"),
    ("ai_query/hy3", "tencent/Hy3", NAGY_PROMPT, False, "olcso"),                          # FLASH-tier
    ("ai_query/dsflash", "deepseek-ai/DeepSeek-V4-Flash", NAGY_PROMPT, True, "olcso"),
    ("ai_query/glm53f", "zai-org/GLM-5.3-Flash", NAGY_PROMPT, False, "olcso"),
    ("ai_query/deepseek", "deepseek-ai/DeepSeek-V4-Pro", NAGY_PROMPT, True, "okos"),       # nehéz
    ("ai_query/kimi3", "moonshotai/Kimi-K3", NAGY_PROMPT, False, "okos"),
    ("ai_query/glm53", "zai-org/GLM-5.3", NAGY_PROMPT, False, "okos"),
    ("ai_query/kimi", "moonshotai/Kimi-K2.7-Code", NAGY_PROMPT, True, "kozep"),            # pro
    ("ai_query/glm5", "zai-org/GLM-5.2", NAGY_PROMPT, False, "kozep"),
    ("", "valami/ismeretlen", NAGY_PROMPT, False, "kozep"),                                # alap
    ("ai_query/deepseek", "deepseek-ai/DeepSeek-V4-Pro", "Mi a főváros?", False, "kozep"),  # apró kérés
    ("ai_query/deepseek", "deepseek-ai/DeepSeek-V4-Pro", "Mi a főváros?", True, "okos"),   # apró, de eszközzel
    ("ai_query/deepseek/synth", "deepseek-ai/DeepSeek-V4-Pro", "röviden", False, "okos"),  # címke: az apró nem ér
    ("email/triage", "zai-org/GLM-5.2", "y" * 160_000, False, "kozep"),                    # nagy bemenet
])
def test_a_szintvalasztas_tablaja(purpose, model, prompt, tools, elvart):
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if tools:
        payload["tools"] = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}]
    szint, indok = k.szint_valaszt(payload, purpose)
    assert szint == elvart, f"{purpose} / {model} → {szint} ({indok}), várt: {elvart}"


def test_minden_siliconflow_modell_besorolt():
    """Egy új SF-modell nem csúszhat be szint nélkül: a `server.SILICONFLOW_MODELS` minden aliasa ÉS azonosítója
    szerepel a kapu táblájában."""
    import server
    for alias, mid in server.SILICONFLOW_MODELS.items():
        assert alias in k.SF_SZINT, f"a(z) `{alias}` alias nincs besorolva (llm_kapu.SF_SZINT)"
        assert mid in k.SF_SZINT, f"a(z) `{mid}` modell nincs besorolva (llm_kapu.SF_SZINT)"
        assert k.SF_SZINT[alias] == k.SF_SZINT[mid]


def test_a_tabla_minden_sora_letezo_szintre_mutat():
    assert {sz for _, _, sz in k.SZINT_TABLA} <= set(k.SZINTEK)
    assert set(k.SF_SZINT.values()) <= {nev for nev, _, _ in k.SZINT_TABLA}


def test_a_szintek_modelljei_es_effortjai():
    assert k.SZINTEK["okos"] == {"modell": "claude-opus-5-5", "effort": "high", "fallbacks": True}
    assert k.SZINTEK["kozep"] == {"modell": "claude-sonnet-5-5", "effort": "medium", "fallbacks": True}
    # Haiku 5.5: szerveroldali refusal-tartalék NINCS — a `fallbacks` ott nem mehet.
    assert k.SZINTEK["olcso"] == {"modell": "claude-haiku-5-5", "effort": "low", "fallbacks": False}


# ── 2. FORDÍTÓRÉTEG: kérés ──────────────────────────────────────────────

def test_sima_chat_system_prompttal():
    kw, _ = k.fordit_keres({"model": "x", "temperature": 0.7, "max_tokens": 1500, "top_p": 0.9,
                            "thinking": {"type": "disabled"}, "reasoning_effort": "medium",
                            "messages": [SYSTEM, {"role": "user", "content": "Szia!"}]},
                           modell="claude-opus-5-5", effort="high", fallbacks=True)
    assert kw["system"] == "Te a Bridge al-ügynöke vagy."
    assert kw["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Szia!"}]}]
    # Mintavételi és SiliconFlow-specifikus paraméter NEM mehet át (az 5.5-ös modelleken 400).
    for tilos in ("temperature", "top_p", "thinking", "reasoning_effort", "tools"):
        assert tilos not in kw
    assert kw["output_config"] == {"effort": "high"}
    assert kw["max_tokens"] == k.KIMENET_ALSO          # a gondolkodásnak is kell hely
    assert kw["betas"] == [k.FALLBACK_BETA] and kw["fallbacks"] == "default"


def test_olcso_szinten_nincs_fallbacks():
    kw, _ = k.fordit_keres({"messages": [{"role": "user", "content": "x"}]},
                           modell="claude-haiku-5-5", effort="low", fallbacks=False)
    assert "fallbacks" not in kw and "betas" not in kw


def test_kesobbi_system_uzenet_szovegge_valik_es_osszevonodik():
    kw, _ = k.fordit_keres({"messages": [SYSTEM, {"role": "user", "content": "a"},
                                         {"role": "system", "content": "most röviden"},
                                         {"role": "user", "content": "b"}]},
                           modell="m", effort="low", fallbacks=False)
    assert len(kw["messages"]) == 1                       # egymást követő user-üzenetek egybe
    szovegek = [b["text"] for b in kw["messages"][0]["content"]]
    assert szovegek == ["a", "[Rendszer-utasítás]\nmost röviden", "b"]


TOOLS = [{"type": "function", "function": {
    "name": "web_search", "description": "Keresés a weben",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}]


def _tool_hurok_payload(tool_id="call_1", tools=True, extra_tool_calls=()):
    tcs = [{"id": tool_id, "type": "function",
            "function": {"name": "web_search", "arguments": json.dumps({"query": "forint"})}}]
    tcs += list(extra_tool_calls)
    p = {"model": "deepseek-ai/DeepSeek-V4-Pro", "messages": [
        SYSTEM, {"role": "user", "content": "Mi a forint árfolyama?"},
        {"role": "assistant", "content": "Megnézem.", "tool_calls": tcs},
        {"role": "tool", "tool_call_id": tool_id, "content": "EUR/HUF 389,2"},
    ]}
    if tools:
        p["tools"] = TOOLS
    return p


def test_tool_hivas_oda_vissza():
    kw, visszajatszott = k.fordit_keres(_tool_hurok_payload(), modell="claude-sonnet-5-5",
                                        effort="medium", fallbacks=True)
    assert not visszajatszott
    assert kw["tools"] == [{"name": "web_search", "description": "Keresés a weben",
                            "input_schema": TOOLS[0]["function"]["parameters"]}]
    assert kw["tool_choice"] == {"type": "auto"}
    assert kw["cache_control"] == {"type": "ephemeral"}
    u, a, r = kw["messages"]
    assert a == {"role": "assistant", "content": [
        {"type": "text", "text": "Megnézem."},
        {"type": "tool_use", "id": "call_1", "name": "web_search", "input": {"query": "forint"}}]}
    assert r == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "EUR/HUF 389,2"}]}


def test_a_kimi_tool_azonositoja_megtisztul_mindket_oldalon():
    kw, _ = k.fordit_keres(_tool_hurok_payload(tool_id="functions.web_search:0"),
                           modell="m", effort="low", fallbacks=False)
    use = kw["messages"][1]["content"][1]
    res = kw["messages"][2]["content"][0]
    assert use["id"] == res["tool_use_id"] == "functions_web_search_0"


def test_eszkozkeszlet_nelkul_a_hivas_szoveg_lesz():
    """A hurok utolsó, kényszerített szöveges köre eszköz NÉLKÜL megy: ott `tool_use` blokk nem lehet."""
    kw, _ = k.fordit_keres(_tool_hurok_payload(tools=False), modell="m", effort="low", fallbacks=False)
    assert "tools" not in kw
    tipusok = {b["type"] for m in kw["messages"] for b in m["content"]}
    assert tipusok == {"text"}
    a = kw["messages"][1]["content"][0]["text"]
    assert "Megnézem." in a and "[Eszközhívás: web_search(" in a
    assert kw["messages"][2]["content"][0]["text"].startswith("[Eszköz-eredmény (web_search)]")


def test_kihagyott_eszkozhivas_hibas_eredmenyt_kap():
    """A Bridge-hurok kihagyja az ismeretlen nevű hívást — az Anthropic viszont minden `tool_use`-ra
    `tool_result`-ot vár, különben 400."""
    kimaradt = {"id": "call_2", "type": "function", "function": {"name": "ismeretlen", "arguments": "{}"}}
    kw, _ = k.fordit_keres(_tool_hurok_payload(extra_tool_calls=[kimaradt]), modell="m", effort="low",
                           fallbacks=False)
    eredmenyek = kw["messages"][2]["content"]
    assert {b["tool_use_id"] for b in eredmenyek} == {"call_1", "call_2"}
    assert next(b for b in eredmenyek if b["tool_use_id"] == "call_2")["is_error"] is True


def test_tool_result_es_szoveg_egy_uzenetben_a_result_elol():
    p = _tool_hurok_payload()
    p["messages"].append({"role": "user", "content": "Foglald össze."})
    kw, _ = k.fordit_keres(p, modell="m", effort="low", fallbacks=False)
    utolso = kw["messages"][-1]["content"]
    assert [b["type"] for b in utolso] == ["tool_result", "text"]


def test_kenyszeritett_eszkozvalasztas_auto_lesz():
    p = _tool_hurok_payload()
    p["tool_choice"] = "required"
    kw, _ = k.fordit_keres(p, modell="claude-opus-5-5", effort="high", fallbacks=True)
    assert kw["tool_choice"] == {"type": "auto"}        # az any/tool az Opus 5.5-ön 400


def test_az_utolso_asszisztens_fordulo_nem_prefill():
    kw, _ = k.fordit_keres({"messages": [{"role": "user", "content": "a"},
                                         {"role": "assistant", "content": "(tool calls végrehajtva)"}]},
                           modell="m", effort="low", fallbacks=False)
    assert kw["messages"][-1] == {"role": "user", "content": [{"type": "text", "text": "Folytasd."}]}


def test_kep_data_uri_base64_blokk():
    kw, _ = k.fordit_keres({"messages": [{"role": "user", "content": [
        {"type": "text", "text": "Mi ez?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]},
        modell="m", effort="low", fallbacks=False)
    assert kw["messages"][0]["content"][1] == {
        "type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}


# ── 2/B. FORDÍTÓRÉTEG: válasz ───────────────────────────────────────────

def test_valasz_openai_alakra():
    m = _msg([{"type": "thinking", "thinking": "", "signature": "SIG"},
              {"type": "text", "text": "Megnézem."},
              {"type": "tool_use", "id": "toolu_01", "name": "web_search", "input": {"query": "forint"}}],
             stop="tool_use", usage={"input_tokens": 100, "output_tokens": 20,
                                     "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0})
    d = k.fordit_valasz(m)
    ch = d["choices"][0]
    assert ch["finish_reason"] == "tool_calls"          # a Feldwebel erre nézi
    assert ch["message"]["content"] == "Megnézem."
    assert ch["message"]["tool_calls"] == [{"id": "toolu_01", "type": "function", "function": {
        "name": "web_search", "arguments": '{"query": "forint"}'}}]
    assert d["usage"]["prompt_tokens"] == 1000 and d["usage"]["completion_tokens"] == 20
    assert d["model"] == "claude-opus-5-5" and d["provider"] == "anthropic"
    assert "reasoning_content" not in ch["message"]


@pytest.mark.parametrize("stop,finish", [("end_turn", "stop"), ("max_tokens", "length"),
                                         ("stop_sequence", "stop"), ("tool_use", "tool_calls")])
def test_finish_reason(stop, finish):
    assert k.fordit_valasz(_msg([{"type": "text", "text": "x"}], stop=stop))["choices"][0]["finish_reason"] == finish


# ── 3. A TOOL-USE HUROK, gondolat-visszajátszással ──────────────────────

def test_a_hurok_masodik_kore_visszakuldi_a_gondolat_blokkot(monkeypatch, kulcs):
    kor1 = _msg([{"type": "thinking", "thinking": "", "signature": "SIG-1"},
                 {"type": "text", "text": "Megnézem."},
                 {"type": "tool_use", "id": "toolu_01", "name": "web_search", "input": {"query": "forint"}}],
                stop="tool_use", model="claude-sonnet-5-5")
    kor2 = _msg([{"type": "text", "text": "Az EUR/HUF 389,2."}], model="claude-sonnet-5-5")
    fake = _wire(monkeypatch, [kor1, kor2])
    messages = [SYSTEM, {"role": "user", "content": "Mi a forint árfolyama?"}]
    payload = {"model": "moonshotai/Kimi-K2.7-Code", "messages": messages, "tools": TOOLS}

    d1, e1, _ = _run(k.chat(payload, purpose="ai_query/kimi"))
    assert e1 is None
    msg = d1["choices"][0]["message"]
    # A hívó PONTOSAN így folytatja (ai_query, _run_agent_with_tools, Feldwebel): hozzáfűzi az üzenetet.
    messages = messages + [msg, {"role": "tool", "tool_call_id": msg["tool_calls"][0]["id"], "content": "389,2"}]
    d2, e2, n2 = _run(k.chat(dict(payload, messages=messages), purpose="ai_query/kimi"))
    assert e2 is None and d2["choices"][0]["message"]["content"] == "Az EUR/HUF 389,2."
    asszisztens = fake.calls[1]["messages"][1]
    assert asszisztens["content"][0] == {"type": "thinking", "thinking": "", "signature": "SIG-1"}
    assert asszisztens["content"][2]["type"] == "tool_use"
    assert not n2


def test_masik_eszkozkeszlettel_nincs_visszajatszas(monkeypatch, kulcs):
    kor1 = _msg([{"type": "thinking", "thinking": "", "signature": "SIG-1"},
                 {"type": "tool_use", "id": "toolu_01", "name": "web_search", "input": {"query": "x"}}],
                stop="tool_use")
    fake = _wire(monkeypatch, [kor1, _msg([{"type": "text", "text": "kész"}])])
    payload = {"model": "deepseek-ai/DeepSeek-V4-Pro", "messages": [{"role": "user", "content": "x" * 5000}],
               "tools": TOOLS}
    d1, _, _ = _run(k.chat(payload))
    m = d1["choices"][0]["message"]
    tools2 = TOOLS + [{"type": "function", "function": {"name": "web_fetch", "parameters": {"type": "object"}}}]
    _run(k.chat({"model": payload["model"], "tools": tools2, "messages": payload["messages"] + [
        m, {"role": "tool", "tool_call_id": "toolu_01", "content": "r"}]}))
    assert all(b["type"] != "thinking" for b in fake.calls[1]["messages"][1]["content"]), \
        "más előtag mellett a gondolat-blokk 400-at érne"


def test_elutasitott_visszajatszas_utan_nelkule_ujra(monkeypatch, kulcs):
    import anthropic
    import httpx2
    kor1 = _msg([{"type": "thinking", "thinking": "", "signature": "SIG-1"},
                 {"type": "tool_use", "id": "toolu_01", "name": "web_search", "input": {"query": "x"}}],
                stop="tool_use")
    rossz = anthropic.BadRequestError(
        "messages.1.content.0: Invalid `signature` in `thinking` block.",
        response=httpx2.Response(400, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
        body=None)
    fake = _wire(monkeypatch, [kor1, rossz, _msg([{"type": "text", "text": "kész"}])])
    payload = {"model": "deepseek-ai/DeepSeek-V4-Pro", "messages": [{"role": "user", "content": "x" * 5000}],
               "tools": TOOLS}
    m = _run(k.chat(payload))[0]["choices"][0]["message"]
    d, e, notes = _run(k.chat(dict(payload, messages=payload["messages"] + [
        m, {"role": "tool", "tool_call_id": "toolu_01", "content": "r"}])))
    assert e is None and d["choices"][0]["message"]["content"] == "kész"
    assert fake.calls[1]["messages"][1]["content"][0]["type"] == "thinking"
    assert fake.calls[2]["messages"][1]["content"][0]["type"] == "tool_use"
    assert any("gondolat-blokkot" in n for n in notes)


# ── 4. NEM NÉMA: refusal, csonkolás, szerveroldali tartalék ─────────────

def test_refusal_tipizalt_hiba(monkeypatch, kulcs):
    _wire(monkeypatch, [_msg([], stop="refusal", stop_details={"type": "refusal", "category": "cyber",
                                                                "explanation": None})])
    d, e, _ = _run(k.chat({"model": "x", "messages": [{"role": "user", "content": "x"}]}))
    assert d is None and e["code"] == "refusal" and "cyber" in e["message"]


def test_csonkolasnal_dupla_keret_kimondva(monkeypatch, kulcs):
    fake = _wire(monkeypatch, [_msg([{"type": "text", "text": "fél"}], stop="max_tokens"),
                               _msg([{"type": "text", "text": "egész"}])])
    d, e, notes = _run(k.chat({"model": "x", "messages": [{"role": "user", "content": "x"}], "max_tokens": 8000}))
    assert d["choices"][0]["message"]["content"] == "egész"
    assert [c["max_tokens"] for c in fake.calls] == [8000, 16000]
    assert any("KEVÉS" in n for n in notes)


def test_szerveroldali_tartalek_kimondva_es_az_o_aran(monkeypatch, kulcs):
    _wire(monkeypatch, [_msg([{"type": "text", "text": "válasz"}], model="claude-opus-4-8", usage={
        "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        "iterations": [{"type": "message", "model": "claude-opus-5-5", "input_tokens": 1_000_000,
                        "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                       {"type": "fallback_message", "model": "claude-opus-4-8", "input_tokens": 1_000_000,
                        "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}]})])
    d, e, notes = _run(k.chat({"model": "deepseek-ai/DeepSeek-V4-Pro",
                               "messages": [{"role": "user", "content": "x" * 5000}]}))
    assert e is None and d["model"] == "claude-opus-4-8"
    assert any("MODELLVÁLTÁS" in n and "claude-opus-4-8" in n for n in notes)
    assert k.allapot()["koltes_usd"] == pytest.approx(4.0 + 5.0)   # mindkét modell a saját árán


# ── 5. KÖLTÉS ───────────────────────────────────────────────────────────

def test_arak_es_cache():
    class U:
        input_tokens, output_tokens = 1_000_000, 1_000_000
        cache_read_input_tokens, cache_creation_input_tokens = 1_000_000, 1_000_000
        iterations = None
    assert k.koltes_usd(U, "claude-opus-5-5") == pytest.approx(4 + 20 + 0.20 + 5.00)
    assert k.koltes_usd(U, "claude-sonnet-5-5") == pytest.approx(2 + 10 + 0.10 + 2.50)
    # Haiku 5.5: 100K token fölötti prompt → a magasabb árkártya
    assert k.koltes_usd(U, "claude-haiku-5-5") == pytest.approx(0.50 + 2.50 + 0.05 + 0.625)

    class Kicsi:
        input_tokens, output_tokens = 1000, 1000
        cache_read_input_tokens = cache_creation_input_tokens = 0
        iterations = None
    assert k.koltes_usd(Kicsi, "claude-haiku-5-5") == pytest.approx((1000 * 0.10 + 1000 * 0.50) / 1e6)
    assert k.koltes_usd(Kicsi, "ismeretlen") == pytest.approx((1000 * 5 + 1000 * 25) / 1e6)  # inkább túl


def test_keret_folott_nem_hiv(monkeypatch, kulcs):
    monkeypatch.setenv("ANTHROPIC_HAVI_KERET_USD", "1")
    fake = _wire(monkeypatch, [_msg([{"type": "text", "text": "x"}], usage={
        "input_tokens": 300_000, "output_tokens": 0, "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0})])                     # Opus: 300k × $4/M = $1,20
    p = {"model": "deepseek-ai/DeepSeek-V4-Pro", "messages": [{"role": "user", "content": "x" * 5000}]}
    assert _run(k.chat(p))[1] is None
    d, e, _ = _run(k.chat(p))
    assert d is None and e["code"] == "havi_keret"
    assert len(fake.calls) == 1
    st = k.allapot()
    assert st["keret_kimerult"] and st["koltes_usd"] == pytest.approx(1.2) and st["hivasok"] == 1
    assert st["szintenkent"]["okos"]["hivasok"] == 1


def test_uj_honapban_nullarol(monkeypatch, kulcs):
    monkeypatch.setattr(k, "_KOLTES", {"honap": "2026-10", "usd": 500.0, "hivasok": 9, "szintenkent": {}})
    assert not k.keret_kimerult()
    assert k.allapot()["koltes_usd"] == 0.0


def test_az_allapotban_nincs_titok(kulcs):
    s = json.dumps(k.allapot(), ensure_ascii=False)
    assert "TITOK" not in s and "sk-ant" not in s
    assert k.allapot()["elerheto"] and k.allapot()["elsodleges"] == "anthropic"


# ── 6. KÖRNYEZET ────────────────────────────────────────────────────────

def test_kulcs_nelkul_a_kapu_nem_elerheto(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_ELSODLEGES", "anthropic")
    assert not k.elerheto() and k.elsodleges() == "siliconflow"
    d, e, _ = _run(k.chat({"messages": [{"role": "user", "content": "x"}]}))
    assert e["code"] == "nincs_kulcs"


def test_elsodleges_env(monkeypatch, kulcs):
    assert k.elsodleges() == "anthropic"
    monkeypatch.setenv("LLM_ELSODLEGES", "siliconflow")
    assert k.elsodleges() == "siliconflow"


def test_hibak_osztalyozasa():
    import anthropic
    import httpx2
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def st(cls, code, msg="x"):
        return cls(msg, response=httpx2.Response(code, request=req), body=None)
    assert k._hiba(st(anthropic.RateLimitError, 429))["code"] == "rate_limit"
    assert k._hiba(st(anthropic.InternalServerError, 500))["code"] == "server_error"
    assert k._hiba(st(anthropic.AuthenticationError, 401))["code"] == "auth"
    assert k._hiba(st(anthropic.BadRequestError, 400,
                      "Your credit balance is too low to access the Anthropic API."))["code"] == "anthropic_keret"
    assert k._hiba(st(anthropic.BadRequestError, 400, "messages: bad"))["code"] == "bad_request"
    assert k._hiba(anthropic.APIConnectionError(request=req))["code"] == "transport"
    assert k._hiba(ValueError("x"))["code"] == "kapu_hiba"


def test_strukturalt_haiku_gondolkodas_nelkul(monkeypatch, kulcs):
    from pydantic import BaseModel

    class S(BaseModel):
        a: int

    hivas = {}

    class P:
        stop_reason = "end_turn"
        parsed_output = S(a=1)
        usage = type("U", (), {"input_tokens": 300, "output_tokens": 10, "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0, "iterations": None})

    async def fake_parse(kw):
        hivas.update(kw)
        return P
    monkeypatch.setattr(k, "_parse", fake_parse)
    obj, err = _run(k.strukturalt("rendszer", "szöveg", S, max_tokens=200))
    assert err is None and obj.a == 1
    assert hivas["model"] == "claude-haiku-5-5" and hivas["max_tokens"] == 200
    assert hivas["thinking"] == {"type": "disabled"} and hivas["output_config"] == {"effort": "low"}
    assert "fallbacks" not in hivas and hivas["output_format"] is S


# ── 7. BEKÖTÉS: a `sf_chat` a kapu mögött ───────────────────────────────

import server  # noqa: E402

from tests.test_sf_selfheal import FakeResp, _ok_payload, _wire as _sf_wire  # noqa: E402


@pytest.fixture
def _no_sleep(monkeypatch):
    async def _instant(_s):
        return None
    monkeypatch.setattr(asyncio, "sleep", _instant)


PAYLOAD = {"model": "zai-org/GLM-5.2", "messages": [{"role": "user", "content": "x" * 5000}], "max_tokens": 4000}


def test_kulccsal_az_anthropic_valaszol_es_a_siliconflow_hozza_sem_er(monkeypatch, kulcs, _no_sleep):
    sf = _sf_wire(monkeypatch, [])
    _wire(monkeypatch, [_msg([{"type": "text", "text": "Claude válasza"}], model="claude-sonnet-5-5")])
    d, e, notes = _run(server.sf_chat(PAYLOAD, purpose="market_brief/economic"))
    assert e is None and d["choices"][0]["message"]["content"] == "Claude válasza"
    assert sf.calls == [] and notes == []


def test_anthropic_kiesesnel_a_siliconflow_kimondva(monkeypatch, kulcs, _no_sleep):
    sf = _sf_wire(monkeypatch, [FakeResp(200, "", _ok_payload("SF válasza"))])

    async def kieses(payload, purpose=""):
        return None, {"code": "rate_limit", "message": "Anthropic rate_limit: 429"}, []
    monkeypatch.setattr(server.llm_kapu, "chat", kieses)
    d, e, notes = _run(server.sf_chat(PAYLOAD, purpose="market_brief/economic"))
    assert e is None and d["choices"][0]["message"]["content"] == "SF válasza"
    assert len(sf.calls) == 1
    assert any("ANTHROPIC-KIESÉS" in n and "rate_limit" in n for n in notes)


def test_siliconflow_402_nel_az_anthropic_kimondva(monkeypatch, kulcs, _no_sleep):
    """A 2026-11-20-i leállás: mindkét SF-kulcs 402. Ha a SiliconFlow az elsődleges, az Anthropic veszi át."""
    monkeypatch.setenv("LLM_ELSODLEGES", "siliconflow")
    monkeypatch.setattr(server, "SILICONFLOW_API_KEY2", "teszt-kulcs-2")
    ures = '{"code":30001,"message":"Sorry, your account balance is insufficient","data":null}'
    sf = _sf_wire(monkeypatch, [FakeResp(402, ures), FakeResp(402, ures)])
    _wire(monkeypatch, [_msg([{"type": "text", "text": "Claude válasza"}])])
    d, e, notes = _run(server.sf_chat(PAYLOAD, purpose="ai_query/glm5"))
    assert e is None and d["choices"][0]["message"]["content"] == "Claude válasza"
    assert len(sf.calls) == 2, "a 402 után a tartalék SF-kulcsot is meg kell próbálni, modellt váltani nem"
    assert all(c["model"] == "zai-org/GLM-5.2" for c in sf.calls)
    assert any("tartalék kulccsal" in n for n in notes)
    assert any("SILICONFLOW-KIESÉS (payment)" in n for n in notes)


def test_kulcs_nelkul_minden_a_regi_uton(monkeypatch, _no_sleep):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    sf = _sf_wire(monkeypatch, [FakeResp(200, "", _ok_payload("SF"))])

    async def tilos(*a, **kw):
        raise AssertionError("kulcs nélkül az Anthropic-kapu nem hívható")
    monkeypatch.setattr(k, "_kuld", tilos)
    d, e, notes = _run(server.sf_chat(PAYLOAD))
    assert e is None and len(sf.calls) == 1 and notes == []


def test_a_402_kodja_payment():
    code, msg = server._sf_classify(402, '{"code":30001,"message":"Sorry, your account balance is insufficient"}')
    assert code == "payment" and "egyenleg" in msg
    assert "payment" not in server._SF_RETRYABLE and "payment" not in server._SF_FAILOVER_WORTHY


def test_a_status_vegpont_mutatja_a_kaput():
    src = __import__("inspect").getsource(server.api_status)
    assert '"llm_kapu": llm_kapu.allapot()' in src


def test_feldwebel_fejlec_a_valaszolo_modellt_mondja():
    from feldwebel.responder import modell_rovid
    assert modell_rovid("claude-sonnet-5-5") == "Sonnet 5.5"
    assert modell_rovid("claude-haiku-5-5") == "Haiku 5.5"
    assert modell_rovid("claude-opus-5-5") == "Opus 5.5"
    assert modell_rovid("deepseek-ai/DeepSeek-V4-Pro") == "DeepSeek-V4-Pro"
    assert modell_rovid("") == ""
