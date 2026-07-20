"""test_delphoi_tracking.py — PYTHIA B1: tracking-runner.
Cél-bizonyíték (2): egy esedékes brief lefut (process_job MOCKOLVA) és
idősor-pont íródik; elégtelen kreditnél skip + sosem negatív egyenleg."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from plugins import delphoi, delphoi_brief, delphoi_tracking


def _valid_spec(**over) -> dict:
    spec = {
        "goal": "Heti követés — termékleírás",
        "instrument": "product_desc",
        "stimuli": ["Okosóra, amely két hétig bírja egy töltéssel."],
        "country": "HU", "n": 30,
        "dimensions": ["appeal"],
        "tracking": "weekly",
        "report": {"lang": "hu", "formats": ["pdf"]},
    }
    spec.update(over)
    return spec


@pytest.fixture
def track_db(get_db):
    conn = get_db()
    delphoi.ensure_tables(conn)
    delphoi.ensure_fg_tables(conn)
    delphoi_brief.ensure_brief_tables(conn)
    conn.close()
    return get_db


def _mk_tracked_brief(get_db, user="user:7", credits=10, **over) -> str:
    delphoi.add_credits(get_db, user, credits, f"teszt-{user}")
    r = delphoi_brief.save_brief(get_db, user, _valid_spec(**over))
    assert r["ok"]
    return r["brief_id"]


def test_next_run_after():
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    assert delphoi_tracking.next_run_after("weekly", now).startswith("2026-07-27")
    assert delphoi_tracking.next_run_after("monthly", now).startswith("2026-08-19")
    with pytest.raises(ValueError):
        delphoi_tracking.next_run_after("daily", now)


def test_due_briefs_only_due(track_db):
    bid = _mk_tracked_brief(track_db)
    # a mentés next_run=most sort ír → azonnal esedékes
    due = delphoi_tracking.due_briefs(track_db)
    assert [d["brief_id"] for d in due] == [bid]
    # jövőbe tolt next_run → nem esedékes
    conn = track_db()
    conn.execute("UPDATE delphoi_tracking SET next_run=? WHERE brief_id=?",
                 ((datetime.now(timezone.utc) + timedelta(days=3)).isoformat(), bid))
    conn.commit(); conn.close()
    assert delphoi_tracking.due_briefs(track_db) == []


def test_runner_runs_due_brief_and_writes_timeseries(track_db):
    """CÉL-BIZONYÍTÉK (2): esedékes brief → mockolt process_job → idősor-pont
    a delphoi_brief_runs-ban + next_run előre lép."""
    bid = _mk_tracked_brief(track_db)
    calls = []

    async def fake_process(deps, job_id):
        calls.append(job_id)
        return {"ok": True, "job_id": job_id,
                "result": {"overall_score": 3.72}}

    now = datetime.now(timezone.utc)
    rep = asyncio.run(delphoi_tracking.run_due_briefs(
        {"get_db": track_db}, now=now, process_fn=fake_process))
    assert rep["ok"] and rep["due"] == 1 and rep["ran"] == 1
    assert len(calls) == 1 and calls[0].startswith("dlph-")
    conn = track_db()
    runs = conn.execute("SELECT * FROM delphoi_brief_runs WHERE brief_id=?",
                        (bid,)).fetchall()
    tr = conn.execute("SELECT next_run FROM delphoi_tracking WHERE brief_id=?",
                      (bid,)).fetchone()
    conn.close()
    assert len(runs) == 1
    assert runs[0]["job_id"] == calls[0]
    assert runs[0]["overall_score"] == pytest.approx(3.72)
    assert runs[0]["spec_hash"]
    assert tr["next_run"] > now.isoformat()   # kadencia-lépés megtörtént


def test_runner_charges_credits_via_normal_job_path(track_db):
    """A futás a NORMÁL job-úton megy: kredit ténylegesen levonódik."""
    bid = _mk_tracked_brief(track_db, user="user:8", credits=10)

    async def fake_process(deps, job_id):
        return {"ok": True, "result": {"overall_score": 3.0}}

    delphoi.ensure_welcome(track_db, "user:8")   # a signup-grant ne torzítsa a mérést
    before = delphoi.get_credits(track_db, "user:8")["balance"]
    asyncio.run(delphoi_tracking.run_due_briefs(
        {"get_db": track_db}, process_fn=fake_process))
    after = delphoi.get_credits(track_db, "user:8")["balance"]
    assert after == before - 1
    _ = bid


def test_runner_insufficient_credit_skips_never_negative(track_db):
    """Elégtelen kredit → SKIP + nincs idősor-pont + egyenleg SOSEM negatív,
    a next_run lép (a periódus kimarad, nem torlódik)."""
    user = "user:9"
    r = delphoi_brief.save_brief(track_db, user, _valid_spec())
    bid = r["brief_id"]
    # a welcome-grant (2 kredit) elköltése, hogy 0 legyen az egyenleg
    delphoi.ensure_welcome(track_db, user)
    bal = delphoi.get_credits(track_db, user)["balance"]
    if bal:
        assert delphoi.charge(track_db, user, "dlph-drain", bal)

    async def fake_process(deps, job_id):  # pragma: no cover — nem hívódhat
        raise AssertionError("process_job nem futhat elégtelen kreditnél")

    now = datetime.now(timezone.utc)
    rep = asyncio.run(delphoi_tracking.run_due_briefs(
        {"get_db": track_db}, now=now, process_fn=fake_process))
    assert rep["due"] == 1 and rep["ran"] == 0
    assert rep["results"][0]["skipped"] is True
    conn = track_db()
    runs = conn.execute("SELECT COUNT(*) c FROM delphoi_brief_runs WHERE brief_id=?",
                        (bid,)).fetchone()["c"]
    tr = conn.execute("SELECT next_run FROM delphoi_tracking WHERE brief_id=?",
                      (bid,)).fetchone()
    conn.close()
    assert runs == 0
    assert tr["next_run"] > now.isoformat()
    assert delphoi.get_credits(track_db, user)["balance"] == 0   # sosem negatív


def test_runner_failed_job_records_point_without_score(track_db):
    """Bukó futás: a pont a láthatóságért íródik, jel nélkül (a refundot a
    delphoi._fail_job vasszabálya intézi a valós úton)."""
    bid = _mk_tracked_brief(track_db, user="user:10")

    async def fake_process(deps, job_id):
        return {"ok": False, "error": "motorhiba"}

    rep = asyncio.run(delphoi_tracking.run_due_briefs(
        {"get_db": track_db}, process_fn=fake_process))
    assert rep["due"] == 1 and rep["ran"] == 0
    conn = track_db()
    run = conn.execute("SELECT overall_score FROM delphoi_brief_runs "
                       "WHERE brief_id=?", (bid,)).fetchone()
    conn.close()
    assert run is not None and run["overall_score"] is None


def test_seed_cron_idempotent(get_db):
    conn = get_db()
    assert delphoi_tracking.seed_cron(conn) is True
    assert delphoi_tracking.seed_cron(conn) is False
    row = conn.execute("SELECT cron_schedule, cron_enabled FROM pyramid_recipes "
                       "WHERE name=?", (delphoi_tracking.CRON_RECIPE_NAME,)).fetchone()
    conn.close()
    assert row["cron_schedule"] == delphoi_tracking.CRON_SCHEDULE
    assert row["cron_enabled"] == 1


def test_recipe_name_needs_special_case_before_generic_delphoi():
    """A recept delphoi_ prefixű → a server _cron_loop-jában a tracking
    special-case-nek a generikus delphoi_ ág ELÉ kell kerülnie (deploy-diff)."""
    assert delphoi_tracking.CRON_RECIPE_NAME.startswith("delphoi_")
