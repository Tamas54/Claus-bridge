"""test_delphoi_p2_integration.py — PYTHIA P2 integráció a delphoi.py-ban.

CÉL-BIZONYÍTÉK (a P2 definíciója szerint):
  - "szektorális üzleti bizalom" stimulus → PIROS verdikt,
  - "a kormány megítélése" stimulus → ZÖLD verdikt,
  - a verdikt + coverage a job-REKORDBAN (delphoi_jobs oszlopok) ÉS az
    API-válasz payloadjában (get_job — a REST/MCP job-status erre delegál),
  - a Hy3-ítész MOCKOLVA (scope_chat_fn), a heurisztika élesben fut.

Plusz: variancia-őr (k-mintázás nowcast + FOGÁS, KÜLÖN env-ekkel), panel-temp
env-kapu clamp, embed-budget hangos hiba, kalibráció a nowcast-payloadban és
a feedben (nyers + kalibrált, verzió-címkével), FG-kalibráció (anchor_pair).
"""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from plugins import delphoi


# ── fixtúra: teljes P2-DB (nowcast + FG táblák + gazdag press-ablak) ────────

@pytest.fixture
def p2_db(get_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_FG_CACHE", "0")
    for var in ("DELPHOI_COVERAGE_MIN", "DELPHOI_NOWCAST_SAMPLES",
                "DELPHOI_SAMPLES_PER_PERSONA", "DELPHOI_EMBED_BUDGET",
                "DELPHOI_PANEL_TEMP"):
        monkeypatch.delenv(var, raising=False)
    conn = get_db()
    delphoi.ensure_tables(conn)
    delphoi.ensure_fg_tables(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS press_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_iso TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'hu',
            signal_type TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(date_iso, lang, signal_type))""")
    ts = datetime.now(timezone.utc).isoformat()
    brief = {"lead": "Feszült hét.", "topics": [{"title": "Uniós csúcs", "summary": "Vita."}]}
    for d in ("2026-07-07", "2026-07-08"):
        conn.execute("INSERT INTO press_snapshots (date_iso, lang, signal_type, content, created_at) "
                     "VALUES (?,?,?,?,?)", (d, "hu", "brief", json.dumps(brief, ensure_ascii=False), ts))
        arts = [{"title": f"Cikk {d} #{i}", "source": f"forras-{i % 10}",
                 "lean": ("L", "C", "R")[i % 3]} for i in range(30)]
        conn.execute("INSERT INTO press_snapshots (date_iso, lang, signal_type, content, created_at) "
                     "VALUES (?,?,?,?,?)", (d, "hu", "news", json.dumps({"articles": arts}), ts))
    delphoi.seed_registry(conn)
    conn.commit()
    conn.close()
    return get_db


def _one_hot_embed(anchor_set):
    async def embed_fn(texts):
        return [[1.0 if i == (anchor_set.index(t) if t in anchor_set else 3) else 0.0
                 for i in range(5)] for t in texts]
    return embed_fn


def _anchor_chat(anchor_set, idx=3, counter=None):
    async def chat_fn(prompt):
        if counter is not None:
            counter["n"] += 1
        return anchor_set[idx]
    return chat_fn


def _judge(verdict):
    """Mockolt Hy3-ítész: prompt-JSON választ ad (a heurisztika élesben fut)."""
    async def fn(prompt):
        return json.dumps({"verdict": verdict, "indok": "teszt-ítész"})
    return fn


_APPEAL = delphoi.REFERENCE_SETS_APPEAL["hu"]


def _run_fg(p2_db, user, text, scope_chat_fn=None):
    delphoi.add_credits(p2_db, user, 20, f"seed-{user}")
    created = delphoi.create_job(p2_db, user, "concept", text,
                                 {"country": "HU", "n_per_cell": 30})
    assert created["ok"], created
    rep = asyncio.run(delphoi.process_job(
        {"get_db": p2_db}, created["job_id"],
        chat_fn=_anchor_chat(_APPEAL), embed_fn=_one_hot_embed(_APPEAL),
        scope_chat_fn=scope_chat_fn))
    return created["job_id"], rep


# ═══════════════════════════════════════════════════════════════════════════
# CÉL-BIZONYÍTÉK
# ═══════════════════════════════════════════════════════════════════════════

def test_target_evidence_sectoral_stimulus_is_red(p2_db):
    job_id, rep = _run_fg(p2_db, "user:p2a", "szektorális üzleti bizalom mérése",
                          scope_chat_fn=_judge("piros"))
    assert rep["ok"], rep
    # 1) a verdikt + coverage a JOB-REKORDBAN
    conn = p2_db()
    row = conn.execute("SELECT scope_verdict, coverage_score FROM delphoi_jobs WHERE id=?",
                       (job_id,)).fetchone()
    conn.close()
    assert row["scope_verdict"] == "piros"
    assert row["coverage_score"] is not None and 0.0 <= row["coverage_score"] <= 1.0
    # 2) a verdikt + coverage az API-VÁLASZ payloadjában (get_job — a REST
    #    GET /api/delphoi/jobs/{id} és az MCP delphoi_job_status erre delegál)
    st = delphoi.get_job(p2_db, job_id, "user:p2a")
    assert st["scope_verdict"] == "piros"
    assert st["coverage_score"] == row["coverage_score"]
    res = st["result"]
    assert res["scope"]["verdict"] == "piros"
    assert res["scope"]["judge"]["verdict"] == "piros"          # mockolt ítész
    assert res["scope"]["heuristic"]["verdict"] == "piros"      # heurisztika élesben
    # PIROS NEM TILT: van eredmény, de kötelező figyelmeztetés + levonás
    assert "NEM validált" in res["scope"]["warning"]
    assert res["coverage"]["score"] >= 0.5                      # gazdag hír-ablak
    assert res["confidence"] == pytest.approx(0.6, abs=0.05)    # 1.0 − 0.4 piros-büntetés


def test_target_evidence_narrative_stimulus_is_green(p2_db):
    job_id, rep = _run_fg(p2_db, "user:p2b", "a kormány megítélése",
                          scope_chat_fn=_judge("zöld"))
    assert rep["ok"], rep
    conn = p2_db()
    row = conn.execute("SELECT scope_verdict, coverage_score FROM delphoi_jobs WHERE id=?",
                       (job_id,)).fetchone()
    conn.close()
    assert row["scope_verdict"] == "zöld"
    st = delphoi.get_job(p2_db, job_id, "user:p2b")
    assert st["scope_verdict"] == "zöld" and st["coverage_score"] is not None
    res = st["result"]
    assert res["scope"]["verdict"] == "zöld" and res["scope"]["warning"] is None
    assert res["scope"]["heuristic"]["verdict"] == "zöld"
    assert res["confidence"] == pytest.approx(1.0, abs=0.05)


def test_scope_heuristic_alone_decides_without_judge(p2_db):
    """Ítész-mock nélkül (teszt-mód): a heurisztika egyedül is PIROS-t ad a
    szektorális stimulusra — a kapu nem az LLM-en múlik."""
    job_id, rep = _run_fg(p2_db, "user:p2c", "B2B iparági szektor-elemzés")
    assert rep["ok"]
    st = delphoi.get_job(p2_db, job_id, "user:p2c")
    assert st["scope_verdict"] == "piros"
    assert st["result"]["scope"]["judge"] is None               # ítész kimaradt


# ═══════════════════════════════════════════════════════════════════════════
# VARIANCIA-ŐR (k-mintázás) + kalibráció a nowcast-payloadban
# ═══════════════════════════════════════════════════════════════════════════

def test_nowcast_k_sampling_multiplies_calls_not_n(p2_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_NOWCAST_SAMPLES", "2")
    anchors = delphoi.REFERENCE_SETS_REGARD["hu"]
    calls = {"n": 0}
    rep = asyncio.run(delphoi.run_entity_nowcast(
        {"get_db": p2_db}, entity_key="Q124488292", n=10,
        chat_fn=_anchor_chat(anchors, counter=calls), embed_fn=_one_hot_embed(anchors)))
    r = rep["results"][0]
    assert r["ok"], r
    assert calls["n"] == 20                                     # personánként k=2 minta
    assert r["n"] == 10                                         # a panel-N a personák száma
    assert r["sampling"]["k"] == 2 and r["sampling"]["n_calls"] == 20
    assert r["direction"] == pytest.approx(0.5, abs=1e-6)       # az átlagolás nem torzít
    assert r["sampling"]["within_persona_sd"] == pytest.approx(0.0, abs=1e-9)
    assert r["embed_budget"]["charged"] > 0                     # embed-hívás számolva+logolva
    # regard-doménre nincs registry-cella → explicit no_entry, raw megy tovább
    assert r["calibration"]["status"] == "no_entry"
    assert r["calibration"]["raw"] == pytest.approx(50.0, abs=1e-3)
    assert r["calibration"]["calibrated"] == pytest.approx(50.0, abs=1e-3)


def test_nowcast_samples_param_overrides_env(p2_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_NOWCAST_SAMPLES", "3")
    anchors = delphoi.REFERENCE_SETS_REGARD["hu"]
    calls = {"n": 0}
    rep = asyncio.run(delphoi.run_entity_nowcast(
        {"get_db": p2_db}, entity_key="Q387006", n=6, samples=1, dry_run=True,
        chat_fn=_anchor_chat(anchors, counter=calls), embed_fn=_one_hot_embed(anchors)))
    assert rep["results"][0]["ok"]
    assert calls["n"] == 6 and rep["results"][0]["sampling"]["k"] == 1


def test_nowcast_calibration_applied_and_in_feed(p2_db):
    """HU szentiment-entitás (cci-domén): a G4-registry (a=−5.763, b=0.846)
    alkalmazva — NYERS és KALIBRÁLT érték verzió-címkével a payloadban ÉS a
    nyilvános feed 'latest' sorában."""
    anchors = delphoi.REFERENCE_SETS_GROWTH["hu"]
    rep = asyncio.run(delphoi.run_entity_nowcast(
        {"get_db": p2_db}, entity_key="hu-novekedesi-hangulat", n=8,
        chat_fn=_anchor_chat(anchors), embed_fn=_one_hot_embed(anchors)))
    r = rep["results"][0]
    assert r["ok"], r
    cal = r["calibration"]
    assert cal["status"] == "calibrated" and cal["applied"] is True
    assert cal["raw"] == pytest.approx(50.0, abs=1e-3)          # direction 0.5 → szaldó +50
    assert cal["calibrated"] == pytest.approx(-5.763 + 0.846 * 50.0, abs=0.01)
    assert cal["registry_version"] == "1.0"
    assert cal["panel_version"] == delphoi.NOWCAST_CAL_PANEL_VERSION
    # a feed 'latest' sora is hordozza (ledger-sor érintetlen, réteg on-the-fly)
    feed = delphoi.public_nowcast_feed(p2_db, entity_key="hu-novekedesi-hangulat")
    latest = feed["entities"][0]["latest"]
    assert latest["calibration"]["status"] == "calibrated"
    assert latest["calibration"]["raw"] == pytest.approx(50.0, abs=1e-3)


def test_fg_k_sampling_default_three(p2_db):
    """FOGÁS-ág: DELPHOI_SAMPLES_PER_PERSONA default 3 — a hívásszám k-szorozódik,
    a minta-sorok a privát silóban, a riportban válasz-szórás metrika."""
    calls = {"n": 0}
    delphoi.add_credits(p2_db, "user:p2k", 20, "seed-p2k")
    created = delphoi.create_job(p2_db, "user:p2k", "product_desc", "Új termék",
                                 {"country": "HU", "n_per_cell": 30})
    rep = asyncio.run(delphoi.process_job(
        {"get_db": p2_db}, created["job_id"],
        chat_fn=_anchor_chat(_APPEAL, counter=calls), embed_fn=_one_hot_embed(_APPEAL)))
    assert rep["ok"]
    assert calls["n"] == 90                                     # 30 persona × k=3
    panel = rep["result"]["panel"]
    assert panel["n_completed"] == 30 and panel["completion_ratio"] == 1.0
    assert panel["sampling"]["k"] == 3 and panel["sampling"]["n_calls"] == 90
    assert panel["sampling"]["within_persona_sd"] == pytest.approx(0.0, abs=1e-9)
    assert panel["embed_budget"]["charged"] > 0
    conn = p2_db()
    n_rows = conn.execute("SELECT COUNT(*) FROM delphoi_panel_responses WHERE job_id=?",
                          (created["job_id"],)).fetchone()[0]
    conn.close()
    assert n_rows == 90                                         # minden minta a silóban


def test_fg_calibration_anchor_pair_offset(p2_db):
    """FG appeal (HU, Hy3): az agora A/B horgonypár offset-je (−15.125)
    alkalmazva a 0–100 appeal-skálán — nyers ÉS kalibrált a riportban."""
    job_id, rep = _run_fg(p2_db, "user:p2cal", "Prémium kávékapszula koncepció")
    assert rep["ok"]
    cal = rep["result"]["calibration"]
    # overall_score=4.0 → appeal_raw = (4−1)/4·100 = 75.0
    assert cal["raw"] == pytest.approx(75.0, abs=1e-3)
    assert cal["status"] == "anchor_pair" and cal["applied"] is True
    assert cal["calibrated"] == pytest.approx(75.0 - 15.125, abs=1e-3)
    assert cal["warning"] and "horgonypár" in cal["warning"]
    assert cal["panel_version"] == delphoi.FG_PANEL_VERSION


# ═══════════════════════════════════════════════════════════════════════════
# EMBED-BUDGET (napi sapka, hangos hiba) + panel-temp clamp
# ═══════════════════════════════════════════════════════════════════════════

def test_embed_budget_exceeded_is_loud_on_nowcast(p2_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_EMBED_BUDGET", "10")
    anchors = delphoi.REFERENCE_SETS_REGARD["hu"]
    with pytest.raises(RuntimeError, match="DELPHOI_EMBED_BUDGET"):
        asyncio.run(delphoi.run_entity_nowcast(
            {"get_db": p2_db}, entity_key="Q124488292", n=6,
            chat_fn=_anchor_chat(anchors), embed_fn=_one_hot_embed(anchors)))
    # a sapka MEGÁLLÍT, mielőtt ledger-sor születne (nem néma vágás)
    conn = p2_db()
    n = conn.execute("SELECT COUNT(*) FROM delphoi_nowcast_ledger").fetchone()[0]
    conn.close()
    assert n == 0


def test_embed_budget_exceeded_fails_fg_job_with_refund(p2_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_EMBED_BUDGET", "10")
    job_id, rep = _run_fg(p2_db, "user:p2eb", "a kormány megítélése")
    assert rep["ok"] is False and rep["refunded"] is True
    assert "DELPHOI_EMBED_BUDGET" in rep["error"]
    st = delphoi.get_job(p2_db, job_id, "user:p2eb")
    assert st["status"] == "failed"
    assert any(l["reason"] == f"refund:{job_id}"
               for l in delphoi.get_credits(p2_db, "user:p2eb")["ledger"])
    # a scope-verdikt a bukott jobon is él (a kapu a futás ELEJÉN írt)
    assert st["scope_verdict"] == "zöld"


def test_embed_budget_accumulates_per_day(p2_db):
    info1 = delphoi.charge_embed_budget(p2_db, 100, label="t1")
    info2 = delphoi.charge_embed_budget(p2_db, 50, label="t2")
    assert info2["today_total"] == info1["today_total"] + 50
    assert info2["budget"] == 2_000_000                          # G0d-default


def test_panel_temperature_clamped(monkeypatch):
    monkeypatch.delenv("DELPHOI_PANEL_TEMP", raising=False)
    assert delphoi.panel_temperature() == pytest.approx(0.8)
    monkeypatch.setenv("DELPHOI_PANEL_TEMP", "1.5")
    assert delphoi.panel_temperature() == pytest.approx(1.0)     # felső kapu
    monkeypatch.setenv("DELPHOI_PANEL_TEMP", "0.1")
    assert delphoi.panel_temperature() == pytest.approx(0.7)     # alsó kapu
    monkeypatch.setenv("DELPHOI_PANEL_TEMP", "0.9")
    assert delphoi.panel_temperature() == pytest.approx(0.9)


def test_sample_env_defaults(monkeypatch):
    for var in ("DELPHOI_NOWCAST_SAMPLES", "DELPHOI_SAMPLES_PER_PERSONA"):
        monkeypatch.delenv(var, raising=False)
    assert delphoi.nowcast_samples() == 3                        # G1: seed-átlagolás kötelező
    assert delphoi.fg_samples() == 3
    monkeypatch.setenv("DELPHOI_NOWCAST_SAMPLES", "0")
    assert delphoi.nowcast_samples() == 1                        # alsó korlát
