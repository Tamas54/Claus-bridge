"""test_delphoi_scopegate.py — PYTHIA P2 scope-gate + coverage + konfidencia.

Egység-szint: heurisztika-osztályok, lenient ítész-parse, kombinált verdikt
(a szigorúbb nyer), ítész-hiba → heurisztika dönt, coverage a press_snapshots
'news' rétegéből ÉS korpusz-fallbackból, env-küszöb, konfidencia-számtan.
A CÉL-BIZONYÍTÉK (job-életút) teszt a test_delphoi_p2_integration.py-ban él.
"""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from plugins import delphoi_scopegate as sg


# ── heurisztika ─────────────────────────────────────────────────────────────

def test_heuristic_sectoral_is_red():
    h = sg.heuristic_scope("Szektorális üzleti bizalom az építőiparban")
    assert h["verdict"] == sg.VERDICT_RED
    assert "szektor" in h["structural_hard"]


def test_heuristic_narrative_is_green():
    h = sg.heuristic_scope("A kormány megítélése a mostani hírek fényében")
    assert h["verdict"] == sg.VERDICT_GREEN
    assert h["structural_hard"] == [] and h["structural_soft"] == []
    assert any("megítélés" in m for m in h["narrative"])


def test_heuristic_single_soft_marker_is_yellow():
    h = sg.heuristic_scope("Mit vársz a GDP alakulásától és a közhangulattól?")
    assert h["verdict"] == sg.VERDICT_YELLOW
    assert "gdp" in h["structural_soft"]


def test_heuristic_two_soft_markers_escalate_to_red():
    h = sg.heuristic_scope("GDP és árfolyam együtt")
    assert h["verdict"] == sg.VERDICT_RED


def test_heuristic_b2b_and_iparagi_red():
    assert sg.heuristic_scope("B2B értékesítési pitch")["verdict"] == sg.VERDICT_RED
    assert sg.heuristic_scope("iparági kilátások")["verdict"] == sg.VERDICT_RED


def test_heuristic_never_echoes_raw_input():
    secret = "XTITKOS-STIMULUS-99 a szektor jövője"
    h = sg.heuristic_scope(secret)
    assert "XTITKOS-STIMULUS-99" not in json.dumps(h, ensure_ascii=False)


# ── ítész-parse (lenient, _parse_motifs-minta) ──────────────────────────────

def test_judge_parse_plain_json():
    p = sg._parse_judge('{"verdict": "piros", "indok": "szektorális bontás"}')
    assert p == {"verdict": "piros", "indok": "szektorális bontás"}


def test_judge_parse_code_fence_and_prose():
    p = sg._parse_judge('Íme:\n```json\n{"verdict": "zöld", "indok": "narratív"}\n```')
    assert p["verdict"] == "zöld"


def test_judge_parse_accentless_alias():
    assert sg._parse_judge('{"verdict": "sarga"}')["verdict"] == sg.VERDICT_YELLOW


def test_judge_parse_garbage_returns_none():
    assert sg._parse_judge("nem json") is None
    assert sg._parse_judge('{"verdict": "lila"}') is None
    assert sg._parse_judge("") is None


# ── kombinált verdikt ───────────────────────────────────────────────────────

def _judge_returning(payload):
    async def chat_fn(prompt):
        return payload
    return chat_fn


def test_scope_verdict_judge_escalates_over_heuristic():
    """Heurisztika zöld + ítész piros → piros (a szigorúbb nyer)."""
    out = asyncio.run(sg.scope_verdict(
        "Az új üdítőmárka fogadtatása",
        chat_fn=_judge_returning('{"verdict": "piros", "indok": "rejtett strukturális kérdés"}')))
    assert out["verdict"] == sg.VERDICT_RED
    assert out["warning"] and "NEM validált" in out["warning"]
    assert out["confidence_penalty"] == pytest.approx(0.4)


def test_scope_verdict_judge_cannot_downgrade():
    """Heurisztika piros + ítész zöld → piros marad (konzervatív)."""
    out = asyncio.run(sg.scope_verdict(
        "szektorális üzleti bizalom",
        chat_fn=_judge_returning('{"verdict": "zöld", "indok": "szerintem ok"}')))
    assert out["verdict"] == sg.VERDICT_RED


def test_scope_verdict_judge_failure_falls_back_to_heuristic():
    async def broken(prompt):
        raise RuntimeError("motor elhalt")
    out = asyncio.run(sg.scope_verdict("a kormány megítélése", chat_fn=broken))
    assert out["verdict"] == sg.VERDICT_GREEN and out["judge"] is None
    assert out["warning"] is None and out["confidence_penalty"] == 0.0


def test_scope_verdict_without_judge():
    out = asyncio.run(sg.scope_verdict("ágazati B2B elemzés", use_judge=False))
    assert out["verdict"] == sg.VERDICT_RED and out["judge"] is None


# ── coverage ────────────────────────────────────────────────────────────────

def _seed_news(get_db, lang="hu", days=("2026-07-07", "2026-07-08"),
               n_per_day=30, n_sources=10, leans=("L", "C", "R")):
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS press_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_iso TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'hu',
            signal_type TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(date_iso, lang, signal_type))""")
    ts = datetime.now(timezone.utc).isoformat()
    for di, d in enumerate(days):
        arts = [{"title": f"Cikk {d} #{i}", "source": f"forras-{i % n_sources}",
                 "lean": leans[i % len(leans)]} for i in range(n_per_day)]
        conn.execute(
            "INSERT OR IGNORE INTO press_snapshots (date_iso, lang, signal_type, content, created_at) "
            "VALUES (?,?,?,?,?)", (d, lang, "news", json.dumps({"articles": arts}), ts))
    conn.commit()
    conn.close()


def test_coverage_from_press_snapshots_healthy(get_db):
    _seed_news(get_db, n_per_day=30, n_sources=10)
    cov = sg.coverage_score(get_db, "HU", window_days=7)
    assert cov["basis"] == "press_snapshots"
    assert cov["score"] >= 0.5 and cov["below_min"] is False and cov["warning"] is None
    assert cov["components"]["n_sources"] == 10
    assert cov["components"]["lean_balance"] == pytest.approx(1.0, abs=0.01)


def test_coverage_thin_window_warns(get_db, monkeypatch):
    monkeypatch.delenv("DELPHOI_COVERAGE_MIN", raising=False)
    _seed_news(get_db, days=("2026-07-08",), n_per_day=4, n_sources=1, leans=("L",))
    cov = sg.coverage_score(get_db, "HU", window_days=7)
    assert cov["score"] < 0.5 and cov["below_min"] is True
    assert cov["warning"] and "ALACSONY COVERAGE" in cov["warning"]


def test_coverage_corpus_fallback_when_no_news(get_db):
    """press_snapshots-ban nincs 'news' → a nowcast-korpusz paraméterei adják."""
    corpus = {"snapshot_ids": [1, 2, 3, 4, 5, 6], "days": 2}
    cov = sg.coverage_score(get_db, "HU", window_days=7, corpus=corpus)
    assert cov["basis"] == "corpus_params"
    # 0.6*(2/7) + 0.4*(6/21)
    assert cov["score"] == pytest.approx(0.6 * 2 / 7 + 0.4 * 6 / 21, abs=1e-3)


def test_coverage_none_when_no_data(get_db):
    cov = sg.coverage_score(get_db, "HU", window_days=7, corpus=None)
    assert cov["basis"] == "none" and cov["score"] == 0.0 and cov["below_min"] is True


def test_coverage_min_env_gate(get_db, monkeypatch):
    _seed_news(get_db, n_per_day=10, n_sources=4)   # közepes coverage (~0.56)
    cov = sg.coverage_score(get_db, "HU", window_days=7)
    assert cov["below_min"] is False                 # default 0.5 küszöb alatt nem
    monkeypatch.setenv("DELPHOI_COVERAGE_MIN", "0.99")
    cov = sg.coverage_score(get_db, "HU", window_days=7)
    assert cov["min"] == 0.99 and cov["below_min"] is True


# ── konfidencia ─────────────────────────────────────────────────────────────

def test_confidence_math():
    green = {"confidence_penalty": 0.0}
    red = {"confidence_penalty": 0.4}
    good_cov = {"score": 0.8, "min": 0.5}
    thin_cov = {"score": 0.2, "min": 0.5}
    assert sg.confidence(green, good_cov) == pytest.approx(1.0)
    assert sg.confidence(red, good_cov) == pytest.approx(0.6)
    assert sg.confidence(green, thin_cov) == pytest.approx(0.7)
    assert sg.confidence(red, thin_cov) == pytest.approx(0.3)
    assert sg.confidence(None, None) == pytest.approx(1.0)


def test_confidence_floor():
    assert sg.confidence({"confidence_penalty": 0.4}, {"score": 0.0, "min": 0.99}) == pytest.approx(0.1)
