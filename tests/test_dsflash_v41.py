"""
FABER #4 — a `dsflash` önvizsgálati bukása (2026-11-22).

A TÜNET
-------
A `self_diagnose` RÉSZLEGES hibát mondott a `siliconflow` szervre: 7 zöld, 1 bukott,
egyedül a `dsflash`, `transport: ReadTimeout: The read operation timed out` — egy
„ping"-re, 45 másodpercen belül sem jött válasz.

AZ OK
-----
Nem a próba mért rosszul (a hitelesítés megvolt, a másik 7 modell ugyanazzal a
kulccsal zöld volt), hanem a KONFIGURÁCIÓ maradt le: a `dsflash` a verziótlan
`deepseek-ai/DeepSeek-V4-Flash`-re, a júniusi ELŐNÉZETI SKU-ra mutatott. Az Echolot
08-04-én a hivatalos `-0731`-re, 09-15-én a V4.1-re váltott (Kommandant: „már nem
V4-et kéne használni, megjelent a V4.1 Flash"), a Studio 10-12-én. A Bridge nem.

Amit ezek a tesztek őriznek:
  1. a `dsflash` a V4.1-Flash, és a pyramid-regiszter ugyanezt mondja,
  2. a V4.1 (és a teljes DeepSeek-Flash-család) `thinking=disabled`-et kap — a pontos
     azonosító-egyezés a cserénél némán `reasoning_effort`-ot adott volna,
  3. a V4.1 tartaléka flash-sel indul, és sosem az előnézeti SKU,
  4. az LLM-kapu ugyanabba a szintbe sorolja, mint a régit,
  5. a bukott próba a MODELL-AZONOSÍTÓT is kimondja, nem csak az aliast.
Hálózat nincs: a SiliconFlow-t hamis `httpx.post` helyettesíti.
"""

import httpx
import pytest

import server

V41 = "deepseek-ai/DeepSeek-V4.1-Flash"
ELONEZETI = "deepseek-ai/DeepSeek-V4-Flash"


# ── 1. KONFIGURÁCIÓ ─────────────────────────────────────────────────────

def test_dsflash_a_v41_nem_az_elonezeti_sku():
    assert server.SILICONFLOW_MODELS["dsflash"] == V41
    assert ELONEZETI not in server.SILICONFLOW_MODELS.values(), \
        "a verziótlan előnézeti SKU még mindig élő alias mögött van"


def test_a_pyramid_regiszter_ugyanazt_mondja():
    from pyramid.agents import AGENT_REGISTRY
    assert AGENT_REGISTRY["dsflash"]["model_id"] == server.SILICONFLOW_MODELS["dsflash"]


@pytest.mark.parametrize("mid", [V41, "deepseek-ai/DeepSeek-V4-Flash-0731", ELONEZETI])
def test_a_deepseek_flash_csalad_gondolkodas_nelkul_megy(mid):
    assert server._model_extra(mid) == {"thinking": {"type": "disabled"}}


def test_a_pro_megtartja_a_sajat_parameteret():
    assert server._model_extra("deepseek-ai/DeepSeek-V4-Pro") == {"reasoning_effort": "medium"}
    assert server._model_extra("deepseek-ai/DeepSeek-V4-Pro-0813") == {"reasoning_effort": "medium"}


def test_a_v41_tartaleka_flash_sel_indul_es_nem_az_elonezeti():
    lanc = server._SF_FAILOVER[V41]
    assert "Flash" in lanc[0], f"az olcsó osztály tartaléka ne pro-modell legyen elsőnek: {lanc}"
    assert ELONEZETI not in lanc
    assert server._SF_FAILOVER["zai-org/GLM-5.3-Flash"][0] == V41


def test_az_llm_kapu_flashnek_latja():
    import llm_kapu
    assert llm_kapu.SF_SZINT[V41] == llm_kapu.SF_SZINT["dsflash"] == "sf_flash"


# ── 2. A PRÓBA ──────────────────────────────────────────────────────────

class _HamisPost:
    def __init__(self, valasz=None, hiba=None):
        self.valasz, self.hiba, self.hivasok = valasz, hiba, []

    def __call__(self, url, **kw):
        self.hivasok.append(kw)
        if self.hiba:
            raise self.hiba
        return self.valasz


class _Valasz:
    def __init__(self, status_code, text=""):
        self.status_code, self.text = status_code, text


@pytest.fixture
def kulcs(monkeypatch):
    # kitalált kulcs: a próba kulcs nélkül el sem indul
    monkeypatch.setattr(server, "SILICONFLOW_API_KEY", "teszt-kulcs")


def test_a_bukott_proba_megnevezi_a_modellt(monkeypatch, kulcs):
    hamis = _HamisPost(hiba=httpx.ReadTimeout("The read operation timed out"))
    monkeypatch.setattr(httpx, "post", hamis)
    r = server._probe_siliconflow_model("dsflash", V41)()
    assert r.ok is False
    assert "ReadTimeout" in r.detail
    assert V41 in r.detail, f"a modell-azonosító hiányzik a leletből: {r.detail}"
    assert f"{server._SF_PROBE_TIMEOUT} s" in r.detail


def test_a_proba_a_termek_parametereivel_hiv(monkeypatch, kulcs):
    hamis = _HamisPost(valasz=_Valasz(200))
    monkeypatch.setattr(httpx, "post", hamis)
    r = server._probe_siliconflow_model("dsflash", server.SILICONFLOW_MODELS["dsflash"])()
    assert r.ok is True
    kuldott = hamis.hivasok[0]
    assert kuldott["json"]["model"] == V41
    assert kuldott["json"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in kuldott["json"]
    assert kuldott["timeout"] == server._SF_PROBE_TIMEOUT


def test_a_rate_limit_bizonytalan_es_megnevezi_a_modellt(monkeypatch, kulcs):
    monkeypatch.setattr(httpx, "post", _HamisPost(valasz=_Valasz(429, '{"code":50610,"message":"too busy"}')))
    r = server._probe_siliconflow_model("dsflash", V41)()
    assert r.ok is None
    assert r.detail.startswith("rate_limit") and V41 in r.detail
