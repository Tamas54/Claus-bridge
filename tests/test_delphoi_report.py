"""test_delphoi_report.py — PYTHIA B2: riport-mű.
Cél-bizonyíték (3): a riport-HTML-ben NINCS nyers persona-mondat; a
brand-mezők a get_brand-ből jönnek; az EN riport EN fejezetcímekkel épül."""
import asyncio
import json
import os
from datetime import datetime, timezone

import pytest

from plugins import delphoi, delphoi_brief, delphoi_report
from plugins.delphoi_brands import BRANDS

RAW_SECRET = "NYERS-PERSONA-MONDAT-XYZZY-91"
USER = "user:42"


@pytest.fixture
def report_db(get_db, tmp_path, monkeypatch):
    monkeypatch.setenv("DELPHOI_REPORTS_DIR", str(tmp_path / "reports"))
    conn = get_db()
    delphoi.ensure_tables(conn)
    delphoi.ensure_fg_tables(conn)
    delphoi_brief.ensure_brief_tables(conn)
    conn.close()
    return get_db


def _mk_done_job(get_db, job_id="dlph-rep1", brief_id="", segments=True) -> str:
    """Kész (done) job beszúrása kézzel: aggregátum + NYERS persona-sorok a
    privát silóban (amelyeknek a riportban TILOS megjelenniük)."""
    agg = {
        "kind": "product_desc", "n": 30, "overall_score": 3.8,
        "baseline": {"type": "skála-középpont", "value": 3.0,
                     "note": "a jel a 3.0 semleges ponthoz mérve értelmezendő"},
        "segments": ([{"segment": "baloldali", "n": 12, "score": 4.1, "vs_baseline": 1.1},
                      {"segment": "jobboldali", "n": 10, "score": 3.4, "vs_baseline": 0.4}]
                     if segments else []),
        "panel": {"country": "HU", "n_requested": 30, "n_completed": 30,
                  "n_seeds": 1, "corpus_hash": "abc123", "model_id": "tencent/Hy3|non-think",
                  "panel_version": "orakel-agora-hu-v2-hy3"},
        "disclaimer": "Szintetikus panel relatív jelzése.",
    }
    panel_spec = {"country": "HU", "n_per_cell": 30, "brief_id": brief_id}
    ts = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO delphoi_jobs (id, user_id, status, input_kind, input_text, "
        "panel_spec, credits_cost, result_json, created_at, completed_at, "
        "model_id, panel_version, coverage_score) "
        "VALUES (?, ?, 'done', 'product_desc', ?, ?, 1, ?, ?, ?, "
        "'tencent/Hy3|non-think', 'orakel-agora-hu-v2-hy3', 1.0)",
        (job_id, USER, "titkos termékleírás", json.dumps(panel_spec),
         json.dumps(agg, ensure_ascii=False), ts, ts))
    conn.execute(
        "INSERT INTO delphoi_panel_responses (job_id, persona_idx, segment, "
        "raw_reaction, ssr_score, created_at) VALUES (?, 0, 'baloldali', ?, 3.8, ?)",
        (job_id, RAW_SECRET, ts))
    conn.commit()
    conn.close()
    return job_id


async def _fake_synth(prompt):
    assert RAW_SECRET not in prompt, "nyers persona-mondat a szintézis-promptban!"
    return json.dumps({"summary": "Első mondat. Második mondat. Harmadik mondat.",
                       "mood": "A panel hangulata mérsékelten pozitív."})


def _gen(get_db, job_id, **kw):
    return asyncio.run(delphoi_report.generate_report(
        {"get_db": get_db}, job_id, USER, synth_fn=_fake_synth, **kw))


# ── cél-bizonyíték (3) ─────────────────────────────────────────────────────

def test_report_html_contains_no_raw_persona_sentence(report_db):
    job_id = _mk_done_job(report_db)
    rep = _gen(report_db, job_id, formats=["json", "csv"])
    assert rep["ok"]
    html = open(rep["artifacts"]["html"], encoding="utf-8").read()
    assert RAW_SECRET not in html
    js = open(rep["artifacts"]["json"], encoding="utf-8").read()
    assert RAW_SECRET not in js
    cs = open(rep["artifacts"]["csv"], encoding="utf-8").read()
    assert RAW_SECRET not in cs


def test_report_brand_fields_from_get_brand(report_db, monkeypatch):
    """A1: a sablon a get_brand()-ből veszi a nevet/láblécet/disclaimert/URL-t —
    új brand = új config-sor, a riport kód-változás nélkül követi."""
    monkeypatch.setitem(BRANDS, "tesztbrand", {
        "name": "Tesztbrand Kutató",
        "logo_path": "",
        "footer_text": "Tesztbrand — szintetikus panel",
        "disclaimer_text": "Tesztbrand-disclaimer: relatív jel.",
        "public_base_url": "https://teszt.example.org",
    })
    job_id = _mk_done_job(report_db)
    rep = _gen(report_db, job_id, formats=[], brand_key="tesztbrand")
    html = open(rep["artifacts"]["html"], encoding="utf-8").read()
    assert "Tesztbrand Kutató" in html
    assert "Tesztbrand — szintetikus panel" in html
    assert "Tesztbrand-disclaimer" in html
    # A4: minden link a brand public_base_url-jéből
    assert "https://teszt.example.org/api/delphoi/verify" in html
    assert "https://teszt.example.org/delphoi" in html
    # Echolot-beégetés nincs a brand-váltott riportban
    assert "echolot" not in html.lower()


def test_en_report_has_en_chapter_titles(report_db):
    job_id = _mk_done_job(report_db, job_id="dlph-rep-en")
    rep = _gen(report_db, job_id, lang="en", formats=[])
    html = open(rep["artifacts"]["html"], encoding="utf-8").read()
    for key in ("ch_exec", "ch_dimensions", "ch_segments", "ch_methodology",
                "ch_scope"):
        assert delphoi_report.STRINGS["en"][key] in html, key
        assert delphoi_report.STRINGS["hu"][key] not in html, key
    assert '<html lang="en">' in html


# ── további B2-invariánsok ─────────────────────────────────────────────────

def test_report_artifacts_and_db_rows(report_db):
    job_id = _mk_done_job(report_db, job_id="dlph-rep2")
    rep = _gen(report_db, job_id)   # default: brief nélkül hu + pdf,json,csv
    assert rep["ok"]
    conn = report_db()
    rows = conn.execute("SELECT format, path FROM delphoi_reports WHERE job_id=?",
                        (job_id,)).fetchall()
    conn.close()
    fmts = {r["format"] for r in rows}
    assert {"html", "pdf", "json", "csv"} <= fmts
    for r in rows:
        assert os.path.exists(r["path"])
    # pdf: vagy igazi PDF, vagy dokumentált HTML-fallback
    pdf_row = next(r for r in rows if r["format"] == "pdf")
    if not rep["pdf_fallback"]:
        assert pdf_row["path"].endswith(".pdf")
        with open(pdf_row["path"], "rb") as f:
            assert f.read(5) == b"%PDF-"


def test_report_owner_only(report_db):
    job_id = _mk_done_job(report_db, job_id="dlph-rep3")
    rep = asyncio.run(delphoi_report.generate_report(
        {"get_db": report_db}, job_id, "user:999", synth_fn=_fake_synth))
    assert not rep["ok"] and rep["error"] == "not_found"


def test_report_not_ready_job(report_db):
    conn = report_db()
    conn.execute(
        "INSERT INTO delphoi_jobs (id, user_id, status, input_kind, input_text, "
        "panel_spec, credits_cost, created_at) VALUES ('dlph-q', ?, 'queued', "
        "'concept', 'x', '{}', 1, ?)",
        (USER, datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()
    rep = asyncio.run(delphoi_report.generate_report(
        {"get_db": report_db}, "dlph-q", USER, synth_fn=_fake_synth))
    assert not rep["ok"] and rep["error"] == "not_ready"


def test_timeseries_chapter_for_tracked_brief(report_db):
    """Tracking-brief: idősor-fejezet a delphoi_brief_runs pontjaiból."""
    r = delphoi_brief.save_brief(report_db, USER, {
        "goal": "Gördülő mérés", "instrument": "product_desc",
        "stimuli": ["Leírás"], "country": "HU", "n": 30,
        "dimensions": ["appeal"], "tracking": "weekly",
        "report": {"lang": "hu", "formats": ["pdf"]}})
    bid = r["brief_id"]
    job_id = _mk_done_job(report_db, job_id="dlph-rep4", brief_id=bid)
    from plugins import delphoi_tracking
    delphoi_tracking.record_run(report_db, bid, job_id, r["spec_hash"], 3.8)
    rep = _gen(report_db, job_id, formats=[])
    html = open(rep["artifacts"]["html"], encoding="utf-8").read()
    assert delphoi_report.STRINGS["hu"]["ch_timeseries"] in html
    assert "dlph-rep4" in html


def test_exploratory_chapter_flagged_not_validated(report_db):
    r = delphoi_brief.save_brief(report_db, USER, {
        "goal": "Exploratív teszt", "instrument": "concept",
        "stimuli": ["Koncepció"], "country": "HU", "n": 30,
        "dimensions": ["appeal"],
        "custom_questions": ["Mi zavarna a leginkább?"],
        "report": {"lang": "hu", "formats": ["pdf"]}})
    job_id = _mk_done_job(report_db, job_id="dlph-rep5", brief_id=r["brief_id"])
    rep = _gen(report_db, job_id, formats=[])
    html = open(rep["artifacts"]["html"], encoding="utf-8").read()
    assert delphoi_report.STRINGS["hu"]["ch_exploratory"] in html
    assert delphoi_report.STRINGS["hu"]["exploratory_badge"] in html
    assert "Mi zavarna a leginkább?" in html


def test_methodology_sheet_fields(report_db):
    job_id = _mk_done_job(report_db, job_id="dlph-rep6")
    rep = _gen(report_db, job_id, formats=[])
    html = open(rep["artifacts"]["html"], encoding="utf-8").read()
    s = delphoi_report.STRINGS["hu"]
    for key in ("meth_n", "meth_quota", "meth_panel_version", "meth_model",
                "meth_grounding", "meth_corpus_hash", "meth_coverage",
                "meth_scope", "meth_calibration", "meth_contamination"):
        assert s[key] in html, key
    assert "abc123" in html            # corpus_hash
    assert "orakel-agora-hu-v2-hy3" in html


def test_synthesis_notstrom_on_llm_failure(report_db):
    """LLM-hiba: determinista NOTSTROM-összefoglaló, a riport nem hasal el."""
    async def broken_synth(prompt):
        raise RuntimeError("motor halott")

    job_id = _mk_done_job(report_db, job_id="dlph-rep7")
    rep = asyncio.run(delphoi_report.generate_report(
        {"get_db": report_db}, job_id, USER, synth_fn=broken_synth, formats=[]))
    assert rep["ok"] and rep["synthesis_fallback"] is True
    html = open(rep["artifacts"]["html"], encoding="utf-8").read()
    assert "3.80" in html or "3,80" in html or "3.8" in html


def test_parse_llm_json_lenient():
    p = delphoi_report.parse_llm_json
    assert p('{"summary": "a", "mood": "b"}') == {"summary": "a", "mood": "b"}
    assert p('```json\n{"summary": "a"}\n```') == {"summary": "a"}
    assert p('Íme a kért JSON:\n{"summary": "a"} — kész.') == {"summary": "a"}
    assert p("nem json") == {}
    assert p("") == {}


def test_latest_artifact_owner_and_mime(report_db):
    job_id = _mk_done_job(report_db, job_id="dlph-rep8")
    rep = _gen(report_db, job_id)
    assert rep["ok"]
    got = delphoi_report.latest_artifact(report_db, job_id, USER, "html")
    assert got["ok"] and got["mime"].startswith("text/html")
    assert not delphoi_report.latest_artifact(report_db, job_id, "user:999", "html")["ok"]
    assert not delphoi_report.latest_artifact(report_db, job_id, USER, "exe")["ok"]


def test_report_from_real_processed_job(report_db):
    """Integráció: valódi process_job-kimenetből (fake-LLM) épül a riport —
    a nyers reakció ott is kimarad."""
    delphoi.add_credits(report_db, USER, 10, "riport-integ")
    created = delphoi.create_job(report_db, USER, "product_desc",
                                 RAW_SECRET, {"country": "HU", "n_per_cell": 30})
    assert created["ok"]
    anchors = delphoi.REFERENCE_SETS_APPEAL["hu"]

    async def chat_fn(prompt):
        return anchors[3]

    async def embed_fn(texts):
        out = []
        for t in texts:
            vec = [0.0] * 5
            vec[anchors.index(t) if t in anchors else 2] = 1.0
            out.append(vec)
        return out

    # press_snapshots kell a korpuszhoz
    conn = report_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS press_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date_iso TEXT NOT NULL,
        lang TEXT NOT NULL DEFAULT 'hu', signal_type TEXT NOT NULL,
        content TEXT NOT NULL, created_at TEXT NOT NULL)""")
    conn.execute(
        "INSERT INTO press_snapshots (date_iso, lang, signal_type, content, created_at) "
        "VALUES ('2026-07-19', 'hu', 'brief', ?, ?)",
        (json.dumps({"lead": "Nyugodt nap.", "topics": []}),
         datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()

    prep = asyncio.run(delphoi.process_job({"get_db": report_db},
                                           created["job_id"],
                                           chat_fn=chat_fn, embed_fn=embed_fn))
    assert prep["ok"]
    rep = _gen(report_db, created["job_id"], formats=["json"])
    assert rep["ok"]
    html = open(rep["artifacts"]["html"], encoding="utf-8").read()
    assert RAW_SECRET not in html   # a beküldött nyers input sem szivárog
