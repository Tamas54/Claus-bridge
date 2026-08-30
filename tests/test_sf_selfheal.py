"""
ÖNJAVÍTÁS a SiliconFlow-hívásokon — task #407 tanulsága.

A HIBA
------
2026-08-30 14:14 UTC: a `daily_news_brief` cron 0,55 másodperc alatt elbukott.
A Kommandant annyit látott, hogy `empty_response`. A Railway-logból derült ki,
mi történt valójában:

    POST .../chat/completions "HTTP/1.1 400 Bad Request"
    ERROR Synthesis returned empty content + empty reasoning (finish_reason=)

A kód `json.loads(resp.text)`-et hívott STÁTUSZ-VIZSGÁLAT NÉLKÜL. A 400-as
hibatörzs simán "értelmes" dictté vált, csak épp `choices` kulcs nélkül — így a
szolgáltató hibaüzenete nyomtalanul eltűnt, és üres tartalom lett belőle.

A recept előtte HAT NAPON át hibátlanul futott (task #396-406), tehát egyszeri,
átmeneti szolgáltatói hiba volt: egy újrapróbálkozás elintézte volna.

Két külön baj, két külön javítás:
  1. a HTTP-státuszt senki nem nézte  → tipizált hiba, a törzs megőrizve,
  2. átmeneti hiba VÉGLEGES bukás lett → újrapróbálás, majd modellváltás.

Amit ezek a tesztek őriznek:
  1. a 400/429/5xx NEM válik üres tartalommá,
  2. az átmeneti hiba után újrapróbálunk, és a második kör sikere ELÉG,
  3. tartós kiesésnél MODELLT VÁLTUNK — de KIMONDVA, nem némán,
  4. a rossz kérésen (auth, bad_request) NEM próbálkozunk újra: az csak kvótát ég,
  5. a tartalék-modell a SAJÁT paramétereit kapja, nem az eredetiét,
  6. a hibás futás `ERROR:`-ral kezdődik, tehát a recept-osztályozó HIBÁSNAK
     látja — nem "értékelhető válasznak".
"""

import asyncio

import pytest

import server
from plugins.recipes import row_error_code


def _run(coro):
    return asyncio.run(coro)


# ── A hamis SiliconFlow ─────────────────────────────────────────────────

class FakeResp:
    def __init__(self, status_code, text, payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("nem JSON")
        return self._payload


def _ok_payload(text="kész brief", model="zai-org/GLM-5.2"):
    return {"model": model,
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


class FakeSF:
    """Sorban adja vissza a beállított válaszokat, és naplózza a payloadokat."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        # ⚠️ MÁSOLAT, nem referencia. A `sf_chat` HELYBEN módosítja a payloadot
        # (keret-emelés, paraméter-levétel), ezért referenciát naplózva minden
        # rögzített hívás a VÉGÁLLAPOTOT mutatná — és a teszt a saját
        # mérőeszköze miatt bukna meg, nem a termék miatt.
        self.calls.append(dict(json or {}))
        r = self.responses.pop(0) if self.responses else FakeResp(200, "", _ok_payload())
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """A visszalépő várakozás valódi másodperceket aludna."""
    async def _instant(_s):
        return None
    monkeypatch.setattr(asyncio, "sleep", _instant)


def _wire(monkeypatch, responses):
    import httpx
    fake = FakeSF(responses)
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    monkeypatch.setattr(server, "SILICONFLOW_API_KEY", "teszt-kulcs")
    return fake


PAYLOAD = {"model": "zai-org/GLM-5.2", "messages": [{"role": "user", "content": "x"}],
           "max_tokens": 16000}


# ── 1. A hibakód TIPIZÁLT, a törzs megőrizve ────────────────────────────

@pytest.mark.parametrize("status,body,expected", [
    (400, '{"code":20015,"message":"parameter is invalid","data":null}', "model_unavailable"),
    (400, '{"message":"messages must not be empty"}', "bad_request"),
    (429, '{"message":"rate limit exceeded"}', "rate_limit"),
    (500, "internal", "server_error"),
    (503, "unavailable", "server_error"),
    (401, "bad key", "auth"),
])
def test_classification(status, body, expected):
    code, message = server._sf_classify(status, body)
    assert code == expected
    assert body[:30] in message or "SILICONFLOW_API_KEY" in message, \
        "a szolgáltató üzenete elveszett — pont ez volt az eredeti hiba"


def test_a_400_never_becomes_empty_content(monkeypatch):
    """A task #407 pontos esete: 400 → NEM üres tartalom, hanem tipizált hiba."""
    _wire(monkeypatch, [FakeResp(400, '{"code":20015,"message":"parameter is invalid"}')] * 9)
    data, err, notes = _run(server.sf_chat(PAYLOAD, purpose="teszt"))
    assert data is None
    assert err["code"] == "model_unavailable"
    assert "parameter is invalid" in err["message"], "a szolgáltató üzenete elveszett"


# ── 2. Újrapróbálás ─────────────────────────────────────────────────────

def test_transient_failure_is_retried(monkeypatch):
    fake = _wire(monkeypatch, [
        FakeResp(503, "unavailable"),
        FakeResp(200, "", _ok_payload("megvan")),
    ])
    data, err, notes = _run(server.sf_chat(PAYLOAD, purpose="teszt"))
    assert err is None and data["choices"][0]["message"]["content"] == "megvan"
    assert len(fake.calls) == 2, "nem próbálta újra"
    assert any("2. próbálkozásra" in n for n in notes), "az újrapróbálkozás nem került a jegyzetbe"


def test_first_try_success_leaves_no_note(monkeypatch):
    """Zaj sincs: ha elsőre ment, nincs mit kimondani."""
    _wire(monkeypatch, [FakeResp(200, "", _ok_payload())])
    data, err, notes = _run(server.sf_chat(PAYLOAD, purpose="teszt"))
    assert err is None and notes == []


# ── 3. Modellváltás — KIMONDVA ──────────────────────────────────────────

def test_failover_to_another_model_is_spoken(monkeypatch):
    """Három bukás a GLM-en → átvált, és ezt BEÍRJA a jegyzetbe. A néma
    modellváltás ugyanaz a hibaosztály, mint a néma kereső-csere."""
    fake = _wire(monkeypatch, [FakeResp(503, "down")] * 3 + [FakeResp(200, "", _ok_payload())])
    data, err, notes = _run(server.sf_chat(PAYLOAD, purpose="teszt"))
    assert err is None
    assert fake.calls[-1]["model"] == "deepseek-ai/DeepSeek-V4-Pro", "nem a tartalék-modellre váltott"
    assert any("MODELLVÁLTÁS" in n for n in notes), "NÉMA modellváltás történt"
    assert any("zai-org/GLM-5.2" in n for n in notes), "a jegyzet nem nevezi meg a KÉRT modellt"


def test_failover_gives_the_substitute_its_own_params(monkeypatch):
    """A modellváltás nem csak a névé: a Kimi thinking=disabled nélkül
    időtúllépésbe fut, a V4-Pro pedig más viselkedést ad reasoning_effort
    nélkül. Az eredeti modell paraméterei nem örökölhetők."""
    payload = dict(PAYLOAD, model="moonshotai/Kimi-K2.7-Code",
                   thinking={"type": "disabled"})
    fake = _wire(monkeypatch, [FakeResp(503, "down")] * 3 + [FakeResp(200, "", _ok_payload())])
    _run(server.sf_chat(payload, purpose="teszt"))
    last = fake.calls[-1]
    assert last["model"] == "deepseek-ai/DeepSeek-V4-Pro"
    assert "thinking" not in last, "a Kimi paramétere ráragadt a V4-Pro-ra"
    assert last.get("reasoning_effort") == "medium", "a tartalék nem kapta meg a saját paraméterét"


def test_all_models_down_returns_the_last_error(monkeypatch):
    _wire(monkeypatch, [FakeResp(503, "down")] * 30)
    data, err, notes = _run(server.sf_chat(PAYLOAD, purpose="teszt"))
    assert data is None and err["code"] == "server_error"


# ── 4. Amin NEM próbálkozunk újra ───────────────────────────────────────

@pytest.mark.parametrize("resp,expected", [
    (FakeResp(401, "bad key"), "auth"),
    (FakeResp(400, '{"message":"messages must not be empty"}'), "bad_request"),
])
def test_non_retryable_fails_fast(monkeypatch, resp, expected):
    """Egy hibás kérésen az újrapróbálkozás csak a kvótát égeti, a
    modellváltás pedig ugyanazt a hibát hozza egy másik modelltől."""
    fake = _wire(monkeypatch, [resp] * 30)
    data, err, notes = _run(server.sf_chat(PAYLOAD, purpose="teszt"))
    assert err["code"] == expected
    assert len(fake.calls) == 1, f"{len(fake.calls)} hívás — nem szabadott volna újrapróbálni"


def test_transport_error_is_retryable(monkeypatch):
    fake = _wire(monkeypatch, [ConnectionError("reset"), FakeResp(200, "", _ok_payload())])
    data, err, notes = _run(server.sf_chat(PAYLOAD, purpose="teszt"))
    assert err is None and len(fake.calls) == 2


def test_200_without_choices_is_an_error(monkeypatch):
    """A szolgáltató 200-zal is adhat használhatatlan törzset. A 200 nem
    bizonyíték — ugyanaz az elv, mint a Lightpanda-kapunál."""
    _wire(monkeypatch, [FakeResp(200, "", {"id": "x"})] * 9)
    data, err, notes = _run(server.sf_chat(PAYLOAD, purpose="teszt"))
    assert data is None and err["code"] == "no_choices"


# ── 5. A hibás futás HIBÁSNAK is látszik ────────────────────────────────

def test_error_content_is_classified_as_failure():
    """A tipizált hiba `ERROR:`-ral kezdődik, tehát a recept-osztályozó
    hibaként látja — nem "értékelhető válaszként", ahogy a régi
    `API error: {...}` szöveg átcsúszott volna rajta."""
    row = {"content": "ERROR: SiliconFlow model_unavailable — HTTP 400 …", "error_code": ""}
    assert row_error_code(row) == "error_response"
    # ellenpróba: a régi alak NEM bukott volna meg
    assert row_error_code({"content": "API error: {'code': 20015}", "error_code": ""}) == "", \
        "a régi 'API error:' szöveg értékelhető válasznak számított — ezért kellett az ERROR: prefix"


def test_notes_are_appended_to_the_output():
    out = server._with_sf_notes("A brief szövege.", ["MODELLVÁLTÁS: a kért X nem élt, Y adta"])
    assert out.startswith("A brief szövege.")
    assert "MODELLVÁLTÁS" in out


def test_no_notes_leaves_the_output_untouched():
    assert server._with_sf_notes("szöveg", []) == "szöveg"


def test_notes_are_deduplicated():
    out = server._with_sf_notes("x", ["ugyanaz", "ugyanaz", "más"])
    assert out.count("ugyanaz") == 1


# ── 6. ÖNSZABÁLYOZÓ TOKEN-KERET ─────────────────────────────────────────
# A `max_tokens` értékeink még a 156k-s kontextusablakok korából valók. A mai
# frontier-modellek 1M kontextussal és 100k+ kimeneti kerettel dolgoznak — a
# fix 8000 ma nem óvatosság, hanem CSONKOLÁS. És a csonkolás NÉMA: HTTP 200,
# `finish_reason="length"`, a gondolkodó modelleknél ÜRES `content`.

def _trunc_payload(reason="length", content="", reasoning="hosszan gondolkodtam"):
    return {"choices": [{"message": {"content": content, "reasoning_content": reasoning},
                         "finish_reason": reason}]}


def test_truncation_is_detected_even_with_200():
    """A 200 nem bizonyíték — ugyanaz az elv, mint a Lightpanda-kapunál."""
    assert server._sf_is_truncated(_trunc_payload()) is True
    assert server._sf_is_truncated(_trunc_payload(reason="stop")) is True, \
        "üres content + tele reasoning: ez is csonkolás, akkor is, ha 'stop'"
    assert server._sf_is_truncated(_ok_payload("kész válasz")) is False


def test_budget_doubles_on_truncation(monkeypatch):
    server._SF_LEARNED_BUDGET.clear()
    fake = _wire(monkeypatch, [
        FakeResp(200, "", _trunc_payload()),
        FakeResp(200, "", _trunc_payload()),
        FakeResp(200, "", _ok_payload("megvan")),
    ])
    data, err, notes = _run(server.sf_chat(dict(PAYLOAD, max_tokens=8000), attempts=6))
    assert err is None
    budgets = [c["max_tokens"] for c in fake.calls]
    assert budgets == [8000, 16000, 32000], f"nem duplázott: {budgets}"
    assert any("KEVÉS volt" in n for n in notes), "a keret-emelés nem került a jegyzetbe"


def test_budget_never_exceeds_the_model_ceiling(monkeypatch):
    server._SF_LEARNED_BUDGET.clear()
    fake = _wire(monkeypatch, [FakeResp(200, "", _trunc_payload())] * 10)
    _run(server.sf_chat(dict(PAYLOAD, model="moonshotai/Kimi-K2.7-Code", max_tokens=8000),
                        attempts=8, failover=False))
    ceiling = server._sf_ceiling("moonshotai/Kimi-K2.7-Code")
    assert max(c["max_tokens"] for c in fake.calls) <= ceiling, \
        "a plafon fölé kért keretet — az 400-at ér"


def test_learned_budget_is_reused_next_time(monkeypatch):
    """A tanulás a lényeg: a következő hívás ne kezdje elölről a csonkolást."""
    server._SF_LEARNED_BUDGET.clear()
    _wire(monkeypatch, [FakeResp(200, "", _trunc_payload()),
                        FakeResp(200, "", _ok_payload())])
    _run(server.sf_chat(dict(PAYLOAD, max_tokens=8000), attempts=4))
    assert server._SF_LEARNED_BUDGET["zai-org/GLM-5.2"] == 16000

    fake2 = _wire(monkeypatch, [FakeResp(200, "", _ok_payload())])
    _run(server.sf_chat(dict(PAYLOAD, max_tokens=8000), attempts=2))
    assert fake2.calls[0]["max_tokens"] == 16000, \
        "a második hívás megint 8000-rel indult — a tanulás elveszett"
    server._SF_LEARNED_BUDGET.clear()


def test_ceiling_reached_is_said_not_hidden(monkeypatch):
    server._SF_LEARNED_BUDGET.clear()
    _wire(monkeypatch, [FakeResp(200, "", _trunc_payload())] * 10)
    ceiling = server._sf_ceiling("tencent/Hy3")
    data, err, notes = _run(server.sf_chat(
        dict(PAYLOAD, model="tencent/Hy3", max_tokens=ceiling), attempts=3, failover=False))
    assert any("PLAFONJÁN" in n for n in notes), \
        "a plafonon is csonkolt válasz csendben sikernek látszott"
    server._SF_LEARNED_BUDGET.clear()


def test_glm53_has_a_modern_ceiling():
    """A GLM-5.3 131 000 kimeneti tokent enged; a régi 8000-es gondolkodás
    ezen a modellen garantált csonkolás (mérve: ~47 000 token/feladat)."""
    assert server._sf_ceiling("zai-org/GLM-5.3-Flash") >= 100000
    assert "glm53f" in server.SILICONFLOW_MODELS


# ── 7. FLASH-TIER (Kommandant-döntés, 2026-08-30) ───────────────────────
# A flash-modellek elsőosztályú fogalommá váltak, nem plusz aliasokká.
# MÉRVE, ugyanarra az egymondatos kérdésre:
#   DeepSeek V4-Flash 4182 ms / 224 token · V4-Pro 7377 / 342
#   GLM-5.3 8982 / 617 · GLM-5.2 10750 / 1039

def test_flash_models_are_registered():
    assert server.SILICONFLOW_MODELS["dsflash"] == "deepseek-ai/DeepSeek-V4-Flash"
    assert server.SILICONFLOW_MODELS["glm53f"] == "zai-org/GLM-5.3-Flash"
    assert set(server.models_in_tier("flash")) == {"dsflash", "glm53f"}


def test_every_model_has_a_tier():
    """Egy tier nélküli modell némán kimaradna minden osztály-alapú hívásból."""
    missing = set(server.SILICONFLOW_MODELS) - set(server.MODEL_TIER)
    assert not missing, f"tier nélküli modell(ek): {missing}"


def test_deepseek_flash_gets_thinking_disabled_not_reasoning_effort():
    """EZ A FLASH LÉNYEGE. A `DeepSeek` általános ága `reasoning_effort`-ot ad
    — ami MÉRVE 4620 ms / 89 tokenről 11 281 ms / 467-re rontja. Egy flash,
    ami hosszan gondolkodik, már nem flash."""
    extra = server._model_extra("deepseek-ai/DeepSeek-V4-Flash")
    assert extra == {"thinking": {"type": "disabled"}}, extra
    # a Pro viszont MEGTARTJA a sajátját
    assert server._model_extra("deepseek-ai/DeepSeek-V4-Pro") == {"reasoning_effort": "medium"}


def test_glm53_family_gets_no_thinking_param():
    """A GLM-5.3 400/20015-öt ad a `thinking`-re ("该模型始终思考")."""
    assert server._model_extra("zai-org/GLM-5.3-Flash") == {}
    assert server._model_extra("zai-org/GLM-5.3") == {}


def test_flash_falls_back_to_flash_first(monkeypatch):
    """Az olcsó osztály maradjon olcsó: a flash tartaléka ELŐSZÖR egy másik
    flash. A GLM-5.3-Flash most erősen rate-limitelt, tehát ez nem elmélet."""
    chain = server._SF_FAILOVER["zai-org/GLM-5.3-Flash"]
    assert chain[0] == "deepseek-ai/DeepSeek-V4-Flash"
    fake = _wire(monkeypatch, [FakeResp(429, '{"code":50610,"message":"too busy"}')] * 3
                 + [FakeResp(200, "", _ok_payload())])
    data, err, notes = _run(server.sf_chat(
        dict(PAYLOAD, model="zai-org/GLM-5.3-Flash"), attempts=3))
    assert err is None
    assert fake.calls[-1]["model"] == "deepseek-ai/DeepSeek-V4-Flash"


def test_nonexistent_model_switches_without_retrying(monkeypatch):
    """`code 20012` = a modell NEM LÉTEZIK (mérve: DeepSeek-V4-Lite).
    Ugyanazt újrahívni sosem segít — de MODELLT VÁLTANI pontosan a helyes
    lépés. Ezért nem `bad_request`."""
    code, msg = server._sf_classify(
        400, '{"code":20012,"message":"Model does not exist. Please check it carefully."}')
    assert code == "model_missing"
    assert code not in server._SF_RETRYABLE, "nem szabad ugyanazt újrahívni"
    assert code in server._SF_FAILOVER_WORTHY, "de váltani KELL"

    fake = _wire(monkeypatch, [FakeResp(400, '{"code":20012,"message":"Model does not exist."}'),
                              FakeResp(200, "", _ok_payload())])
    data, err, notes = _run(server.sf_chat(PAYLOAD, attempts=3))
    assert err is None
    assert len(fake.calls) == 2, "egyszer próbálta az elsőt, aztán váltott — helyes"
    assert fake.calls[1]["model"] != fake.calls[0]["model"]


def test_bad_request_still_does_not_failover(monkeypatch):
    """A KÉRÉS hibáján a váltás csak kvótát égetne: minden modell ugyanazt
    mondaná."""
    fake = _wire(monkeypatch, [FakeResp(400, '{"message":"messages must not be empty"}')] * 9)
    _run(server.sf_chat(PAYLOAD, attempts=3))
    assert len(fake.calls) == 1


def test_pyramid_knows_the_flash_tier():
    from pyramid.agents import AGENT_REGISTRY, agents_in_tier
    assert set(agents_in_tier("flash")) == {"dsflash", "glm53f"}
    # a token-éhes flash NEM indulhat 8000-ről
    assert AGENT_REGISTRY["glm53f"]["default_max_tokens"] >= 48000
    assert AGENT_REGISTRY["dsflash"]["model_id"] in server.SILICONFLOW_MODELS.values()


# ── 8. ALIAS-FELOLDÁS a dispatch eszközös ágán ──────────────────────────
# A pyramid dispatcher az AGENT-ALIAST adja át (`glm5`, `dsflash`, `kimi`), nem
# a szolgáltató modell-azonosítóját. A `_run_agent_with_tools` eddig nyersen
# küldte tovább — és a SiliconFlow MINDEN aliasra 400/20012-t ad. MÉRVE:
#   model=glm5 → 400 · model=dsflash → 400 · model=kimi → 400
# A régi kód ezt `"API error: {...}"` szövegként adta vissza VÁLASZKÉNT, és a
# `row_error_code` értékelhető tartalomnak látta. A hibás futás sikeresnek
# látszott, és a hibaszöveg mehetett ki briefként.

def test_agent_alias_is_resolved_to_a_model_id(monkeypatch):
    fake = _wire(monkeypatch, [FakeResp(200, "", _ok_payload("kész"))])
    _run(server._run_agent_with_tools("dsflash", [{"role": "user", "content": "x"}],
                                      max_rounds=1))
    sent = fake.calls[0]["model"]
    assert sent == "deepseek-ai/DeepSeek-V4-Flash", \
        f"a nyers alias ment ki modellnévként: {sent!r} — a SiliconFlow erre 400/20012-t ad"


@pytest.mark.parametrize("alias", ["kimi", "deepseek", "glm5", "glm53f", "dsflash"])
def test_every_alias_resolves(monkeypatch, alias):
    fake = _wire(monkeypatch, [FakeResp(200, "", _ok_payload("kész"))])
    _run(server._run_agent_with_tools(alias, [{"role": "user", "content": "x"}], max_rounds=1))
    assert fake.calls[0]["model"] == server.SILICONFLOW_MODELS[alias]


def test_a_real_model_id_passes_through_unchanged(monkeypatch):
    """A feloldás nem ronthatja el azt, ami már feloldott — a szintézis-út
    valódi model_id-t ad át."""
    fake = _wire(monkeypatch, [FakeResp(200, "", _ok_payload("kész"))])
    _run(server._run_agent_with_tools("zai-org/GLM-5.2", [{"role": "user", "content": "x"}],
                                      max_rounds=1))
    assert fake.calls[0]["model"] == "zai-org/GLM-5.2"


# ===========================================================================
# A KERULOUT — 2026-08-30
# ===========================================================================
#
# A KOMMANDANT LELETE: "az önjavító funkció és az automatikus token adagoló IS
# elbukott. Meg az is, h saját maga megmondja mi a baja."
#
# Igaza volt, de nem ugy, ahogy latszott: NEM romlottak el. SOHA NEM VOLTAK
# AZON AZ UTON. A `server.py`-ban 12 kozvetlen SiliconFlow-POST allt, es ebbol
# KETTO ment at az `sf_chat`-en. Az `ai_query` — a fo alugynok-belepesi pont,
# amit a receptek, a briefek es a Feldwebel is hasznal — harom sajat POST-tal
# kerulte meg az ujraprobalast, a modell-atvaltast, a parameter-ledobast, a
# csonkolas-erzekelest ES az onszabalyozo token-keretet.
#
# Elesben ez ugy nezett ki, hogy az Economic Brief haromszor egymas utan JSON
# nelkuli valaszt adott, es semmi nem szolalt meg.
#
# Ezek a tesztek a HUZALOZAST merik, nem a logikat: a legjobb onjavito reteg is
# nulla, ha nincs rakotve arra a csore, ami elromlik.

import ast as _ast
import os as _os


def _server_tree():
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    with open(_os.path.join(root, "server.py"), encoding="utf-8") as fh:
        src = fh.read()
    return src, _ast.parse(src)


def _func_source(name: str) -> str:
    src, tree = _server_tree()
    lines = src.split("\n")
    for n in _ast.walk(tree):
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and n.name == name:
            return "\n".join(lines[n.lineno - 1:n.end_lineno])
    raise AssertionError(f"nincs {name}() a server.py-ban")


def test_ai_query_nem_kerul_ki_az_sf_chat_mellett():
    """AZ ONJAVITAS CSAK AZON A CSOVON VED, AMIRE RA VAN KOTVE."""
    src = _func_source("ai_query")
    assert "chat/completions" not in src, (
        "az `ai_query` megint kozvetlenul POST-ol a SiliconFlow-ra — ezzel "
        "kikerüli az ujraprobalast, a modell-atvaltast es az onszabalyozo "
        "token-keretet. Hivd az `sf_chat`-et.")
    assert src.count("await sf_chat(") >= 3, (
        "az `ai_query` haromfele hivast indit (fo kor + ket szintezis); "
        "mindharomnak a vedett uton kell mennie")


def test_a_kerulout_nem_novekedhet():
    """RACSNI: a maradek kozvetlen POST-ok szama nem nohet.

    2026-08-30-i allapot: 8 kerulout maradt (`_reply`, `_discuss_agent`,
    2x `_api_call`, `_p`, `_analyze_image`, 2x `_handle_telegram_message`),
    plusz maga az `_sf_post_once`, ami AZ `sf_chat` belseje. Ezek megnevezett,
    hatralevo munkak — de uj nem jöhet hozzajuk eszrevetlenul.
    """
    src, tree = _server_tree()
    fns = [(n.lineno, n.end_lineno, n.name) for n in _ast.walk(tree)
           if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))]
    hol = []
    for i, line in enumerate(src.split("\n"), 1):
        if "chat/completions" in line:
            o = sorted((f for f in fns if f[0] <= i <= f[1]), key=lambda x: x[1] - x[0])
            hol.append(o[0][2] if o else "(modul)")
    kerulout = [h for h in hol if h != "_sf_post_once"]
    assert len(kerulout) <= 8, (
        f"uj SiliconFlow-kerulout keletkezett ({len(kerulout)} > 8): {kerulout}. "
        f"Minden uj hivas az `sf_chat`-en menjen at.")
    assert "ai_query" not in kerulout


def test_a_brief_hibajelzese_onvizsgalatot_is_kuld():
    """A jelzes mondja meg, MI a baj — ne csak azt, hogy baj van."""
    import inspect
    from feldwebel import market_brief as _mb
    src = inspect.getsource(_mb.generate_market_brief)
    assert "selfdiag" in src and "diagnose(" in src, \
        "a brief bukasakor nem fut onvizsgalat"
    assert "Önvizsgálat" in src, "a diagnozis nem kerul bele a Telegram-uzenetbe"
    # És ha maga a mérőeszköz hasal el, a jelzés attól még menjen ki:
    assert "a mérőeszköz maga hibázott" in src, \
        "az onvizsgalat kivetele elnemithatja a hibajelzest"


def test_a_megtanult_keret_tuleli_az_ujrainditast():
    """EGY TANULAS, AMIT MINDEN UJRAINDITAS ELFELEJT, NEM TANULAS.

    A `_SF_LEARNED_BUDGET` eredetileg processz-elettartamu volt, azzal az
    indoklassal (amit EN irtam), hogy "a tanulas egy kor alatt visszaall". A
    2026-08-30-i eles meres megcafolta: az az "egy kor" a V4-Pro-n HAROM teljes
    generalas es ~3,5 PERC (16000 -> 32000 -> 64000) — egy felhasznalonak meno
    briefen, es MINDEN DEPLOY UTAN ujra. Ketszer egymas utan meg is tortent.
    """
    src, _ = _server_tree()
    assert "_sf_budget_load" in src and "_sf_budget_remember" in src, \
        "a megtanult keret megint csak memoriaban el"
    # A betoltes RA IS VAN KOTVE, nem csak letezik:
    loop = _func_source("_cron_loop")
    assert "_sf_budget_load()" in loop, \
        "a visszatoltes sosem fut le — a perzisztencia igy fel javitas"
    # Es a rogzites a sikeres agon tortenik, nem a hibasan:
    chat = _func_source("sf_chat")
    assert "_sf_budget_remember(" in chat
