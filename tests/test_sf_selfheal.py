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
