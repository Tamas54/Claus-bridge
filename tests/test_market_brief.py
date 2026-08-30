"""Tests for feldwebel.market_brief — the Bridge side of PLAN_20260531.md.

Covers:
- §3 schema validation (validate_brief): accepts a valid brief, rejects each
  structural violation.
- JSON extraction from a messy model response (markdown fences, surrounding prose).
- End-to-end generate_market_brief with a stubbed ai_query and NOFX_BRIEF_URL unset
  → local-file fallback (never crashes, returns ok=True).
- Retry loop: first response invalid JSON → second valid → ok with attempts=2.
- All attempts invalid → ok=False (cron loop stays alive).
"""

import asyncio
import json
import os

import pytest

from feldwebel import market_brief as mb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _valid_brief():
    return {
        "asof": "2026-06-05T13:00Z",
        "session": "morning",
        "valid_until": "2026-06-05T19:00Z",
        "regime": "risk_on",
        "risk_budget_pct": 55,
        "bias": {"equity": "long_small"},
        "tradeable": ["NVDA", "AAPL"],
        "avoid": ["NFP 2026-06-06 08:30 ET -> no equity entries +/-1h"],
        "events": ["US Consumer Sentiment today"],
        "crowd": "complacent",
        "exposure_caps": [{"cluster": "semis", "members": ["NVDA", "MU"], "max_units": 1}],
        "exit_flags": [
            {"symbol": "NVDA", "reason": "earnings tonight", "deadline": "2026-06-05T20:00Z"}
        ],
        "note": "Watch CPI surprise.",
        # A ket EMBERI mezo. 2026-08-30 ota kotelezo: a brief egyetlen valodi
        # fogyasztoja a Kommandant a Telegramon, es nelkuluk a kikuldott uzenet
        # gepi mezok listaja — vagyis szamara URES, akkor is, ha a sema hibatlan.
        "macro_review": ("MAGYARORSZAG: az inflacio 4,1% [KSH ara0002, 2026-07].\n\n"
                         "EUROZONA: HICP 2,2% [ECB ICP, 2026-07].\n\n"
                         "USA: a Fed 3,75% [FRED, 2026-08]."),
        "telegram_digest": "A piacot ma a CPI-varakozas mozgatja.",
    }


def _stub_ai_query(responses):
    """Return a fake ai_query coroutine yielding the given responses in order.

    Each item is the *inner* response text; we wrap it in the ai_query envelope.
    """
    calls = {"n": 0}

    async def _fake(**kwargs):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return json.dumps({"model": "deepseek-ai/DeepSeek-V4-Pro", "response": responses[i]})

    _fake.calls = calls
    return _fake


# ---------------------------------------------------------------------------
# validate_brief
# ---------------------------------------------------------------------------
def test_validate_accepts_valid_brief():
    assert mb.validate_brief(_valid_brief()) == []


def test_validate_rejects_non_object():
    errs = mb.validate_brief(["not", "a", "dict"])
    assert errs and "object" in errs[0]


def test_validate_missing_field():
    b = _valid_brief()
    del b["regime"]
    errs = mb.validate_brief(b)
    assert any("regime" in e for e in errs)


def test_validate_bad_session():
    b = _valid_brief()
    b["session"] = "evening"
    errs = mb.validate_brief(b)
    assert any("session" in e for e in errs)


def test_validate_risk_budget_range():
    b = _valid_brief()
    b["risk_budget_pct"] = 150
    errs = mb.validate_brief(b)
    assert any("risk_budget_pct" in e for e in errs)


def test_validate_exposure_caps_shape():
    b = _valid_brief()
    b["exposure_caps"] = [{"cluster": "semis"}]  # missing members + max_units
    errs = mb.validate_brief(b)
    assert any("members" in e for e in errs)
    assert any("max_units" in e for e in errs)


def test_validate_exit_flags_shape():
    b = _valid_brief()
    b["exit_flags"] = [{"symbol": "NVDA", "reason": "x"}]  # missing deadline
    errs = mb.validate_brief(b)
    assert any("deadline" in e for e in errs)


# ---------------------------------------------------------------------------
# _extract_json_object
# ---------------------------------------------------------------------------
def test_extract_plain_json():
    obj = mb._extract_json_object(json.dumps(_valid_brief()))
    assert obj["regime"] == "risk_on"


def test_extract_from_markdown_fence():
    raw = "Here is the brief:\n```json\n" + json.dumps(_valid_brief()) + "\n```\nDone."
    obj = mb._extract_json_object(raw)
    assert obj is not None and obj["session"] == "morning"


def test_extract_with_surrounding_prose():
    raw = "Sure! " + json.dumps(_valid_brief()) + " (let me know if you need changes)"
    obj = mb._extract_json_object(raw)
    assert obj is not None and obj["tradeable"] == ["NVDA", "AAPL"]


def test_extract_handles_braces_in_strings():
    b = _valid_brief()
    b["note"] = "uses {curly} braces in the note"
    obj = mb._extract_json_object(json.dumps(b))
    assert obj["note"] == "uses {curly} braces in the note"


def test_extract_returns_none_on_garbage():
    assert mb._extract_json_object("no json here at all") is None


# ---------------------------------------------------------------------------
# generate_market_brief — end to end with stubs
# ---------------------------------------------------------------------------
def test_generate_success_local_file(monkeypatch, tmp_path):
    # NOFX unset → local-file fallback.
    monkeypatch.setattr(mb, "NOFX_BRIEF_URL", "")
    monkeypatch.setattr(mb, "_BRIEF_DIR", tmp_path / "brief")
    # No statdata configured → calendar fetch returns "".
    monkeypatch.setattr(mb, "statdata_client", None)
    mb.set_ai_query(_stub_ai_query([json.dumps(_valid_brief())]))

    result = asyncio.run(mb.generate_market_brief("morning"))

    assert result["ok"] is True
    assert result["session"] == "morning"
    assert result["attempts"] == 1
    assert result["push"]["pushed"] is False
    # File was written and is valid against the schema.
    out = tmp_path / "brief" / "morning.json"
    assert out.exists()
    written = json.loads(out.read_text())
    # A WIRE-PAYLOAD alakja: az emberi mezok NELKUL, mert azokat a push elott
    # szandekosan kiszedjuk. Ezert `require_human=False` — a sajat kiszedesunket
    # nem jelenthetjuk hianynak.
    assert mb.validate_brief(written, require_human=False) == []
    assert "macro_review" not in written and "telegram_digest" not in written, \
        "az emberi mezok bekerultek a NOFX wire-payloadba"


def test_generate_forces_contract_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "NOFX_BRIEF_URL", "")
    monkeypatch.setattr(mb, "_BRIEF_DIR", tmp_path / "brief")
    monkeypatch.setattr(mb, "statdata_client", None)
    # Model drifts the session; generator must overwrite it to the requested one.
    drifted = _valid_brief()
    drifted["session"] = "afternoon"
    mb.set_ai_query(_stub_ai_query([json.dumps(drifted)]))

    result = asyncio.run(mb.generate_market_brief("morning"))
    assert result["ok"] is True
    assert result["brief"]["session"] == "morning"


def test_generate_retries_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "NOFX_BRIEF_URL", "")
    monkeypatch.setattr(mb, "_BRIEF_DIR", tmp_path / "brief")
    monkeypatch.setattr(mb, "statdata_client", None)
    # First response = no JSON; second = valid.
    fake = _stub_ai_query(["sorry, I could not produce JSON", json.dumps(_valid_brief())])
    mb.set_ai_query(fake)

    result = asyncio.run(mb.generate_market_brief("afternoon"))
    assert result["ok"] is True
    assert result["attempts"] == 2
    assert fake.calls["n"] == 2


def test_generate_all_invalid_returns_not_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "NOFX_BRIEF_URL", "")
    monkeypatch.setattr(mb, "_BRIEF_DIR", tmp_path / "brief")
    monkeypatch.setattr(mb, "statdata_client", None)
    mb.set_ai_query(_stub_ai_query(["garbage", "still garbage", "nope"]))

    result = asyncio.run(mb.generate_market_brief("morning"))
    assert result["ok"] is False
    assert result["attempts"] == 3
    assert "validation_errors" in result


def test_generate_bad_session():
    result = asyncio.run(mb.generate_market_brief("evening"))
    assert result["ok"] is False
    assert "session" in result["error"]


def test_generate_no_ai_query_wired(monkeypatch):
    monkeypatch.setattr(mb, "_ai_query_func", None)
    result = asyncio.run(mb.generate_market_brief("morning"))
    assert result["ok"] is False
    assert "ai_query" in result["error"]


# ===========================================================================
# ECONOMIC BRIEF — 2026-08-30
# ===========================================================================
#
# MIERT VALTOZOTT. Ket lelet ugyanarrol a briefrol:
#   * a `NOFX_BRIEF_URL` a produkcioban NINCS beallitva, es a `MARKET_BRIEF_DIR`
#     sem — a gepi payload egy efemer konyvtarba ment, amit minden deploy
#     elmosott. A NOFX-fogyaszto NEM LETEZETT;
#   * a Kommandant viszont Telegramon MEGKAPJA es EMBERKENT olvassa.
# Vagyis a brief valodi kozonsege vegig egy ember volt, mikozben a neve, a
# tartalma es a menetrendje egy botnak szolt. Ezek a tesztek az uj sulypontot
# orzik: magyar/EU makro-szemle elol, gepi mezok hatul, EGY renderelo.

def test_a_makro_kontextus_lefedi_magyarorszagot():
    """A brief HU/EU makrot is kap, nem csak amerikait. Enelkul a "magyar
    gazdasagi szemle" rovat forras nelkul maradna — es a modell a betanitasi
    memoriabol potolna."""
    spec = json.loads(mb._build_data_context())
    for kell in ("hu_macro", "eu_macro", "us_macro"):
        assert kell in spec["presets"], f"hianyzik a preset: {kell}"
    assert "hu_markets" in spec["presets"], "nincs magyar tozsdei adat"
    regiok = {c["args"].get("region") for c in spec["series"]
              if c["tool"] == "get_economic_calendar"}
    assert {"US", "EU"} <= regiok, "csak az amerikai naptart huzzuk"


def test_az_emberi_mezok_nelkul_a_brief_bukik():
    """FAIL-CLOSED: gepi mezok hibatlanul, emberi resz nelkul = a Kommandant
    egy mezolistat kap. A retry-hurok ezt hibanak veszi es ujraprobal."""
    for hianyzo in ("macro_review", "telegram_digest"):
        b = _valid_brief()
        del b[hianyzo]
        errs = mb.validate_brief(b)
        assert any(hianyzo in e for e in errs), f"{hianyzo} hianya atment"
    ures = _valid_brief()
    ures["macro_review"] = "   "
    assert mb.validate_brief(ures), "az URES makro-szemle atment"


def test_egy_renderelo_minden_feluletre(monkeypatch, tmp_path):
    """A Kommandant Telegramon kapja, de itt is olvassa. Ha a ket felulet
    kulon allitana elo a szoveget, ket kulon briefre csusznanak szet — ezert a
    generalas eredmenye MAGA hordozza a kikuldott szoveget."""
    monkeypatch.setattr(mb, "NOFX_BRIEF_URL", "")
    monkeypatch.setattr(mb, "_BRIEF_DIR", tmp_path / "brief")
    monkeypatch.setattr(mb, "statdata_client", None)
    kikuldott = []

    async def _fake_push(text):
        kikuldott.append(text)
    monkeypatch.setattr(mb, "_telegram_push_func", _fake_push)
    mb.set_ai_query(_stub_ai_query([json.dumps(_valid_brief())]))

    result = asyncio.run(mb.generate_market_brief("morning"))

    assert result["ok"] is True
    assert kikuldott, "semmi nem ment ki Telegramra"
    assert result["telegram_text"] == kikuldott[0], \
        "a hivo mas szoveget kap, mint ami Telegramra ment"


def test_a_telegram_uzenet_a_gazdasagi_szemleval_kezdodik(monkeypatch):
    """A SORREND TERMEK-DONTES: a brief celja az emberi szemle; a rezsim/
    kockazati keret egy botnak keszult mezo, ami ma senkihez nem jut el."""
    b = _valid_brief()
    digest = b.pop("telegram_digest")
    makro = b.pop("macro_review")
    text = mb.format_brief_telegram(b, digest, makro)

    assert "Economic Brief" in text
    assert "NOFX" not in text, "a bot neve maradt a fejlecben"
    assert text.index("Gazdasági szemle") < text.index("Rezsim:"), \
        "a gepi mezok elore kerultek az emberi szemle ele"
    assert "KSH ara0002" in text, "a makro-szemle forrascimkei elvesztek"
    assert len(text) < 4000, "a Telegram-plafon folott vagyunk"


def test_a_makro_szemle_nelkuli_regi_brief_sem_dob(monkeypatch):
    """Visszafele: egy archivalt, emberi mezok nelkuli regi brief renderelese
    nem eshet szet — csak rovidebb lesz."""
    b = _valid_brief()
    b.pop("telegram_digest")
    b.pop("macro_review")
    text = mb.format_brief_telegram(b)
    assert "Economic Brief" in text and "Rezsim:" in text
