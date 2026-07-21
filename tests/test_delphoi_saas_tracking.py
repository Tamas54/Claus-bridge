"""test_delphoi_saas_tracking.py — KLARTEXT K5: a "Run this weekly" Bridge-fele.

CÉL-BIZONYÍTÉKOK:
  - POST /saas/jobs/{job_id}/track a futás EREDETI kérdéséből weekly-briefet
    ment a B1 brief-úton (delphoi_brief.save_brief) és aktiválja a
    delphoi_tracking sort — az első ismételt futás +7 nap múlva, a mostani
    kész eredmény az idősor első pontja;
  - idempotens (aktív követésre already=True, nincs dupla idősor-pont);
  - a GET /saas/jobs/{job_id} payload 'weekly' blokkja hordozza az állapotot
    és az őszinte futásonkénti kredit-árat (delphoi.job_cost);
  - GET /saas/tracking listáz, POST /saas/tracking/{brief_id} szüneteltet/
    folytat (active flag) — folytatáskor +1 kadencia mostantól;
  - tulaj-only mindenhol; a 06:50-es cron (due_briefs) a bekapcsolt követést
    a kadencia lejártakor látja esedékesnek, előtte nem.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from plugins import (delphoi, delphoi_brief, delphoi_saas_auth as saa,
                     delphoi_tracking)

SECRET = "test-k5-secret"
EMAIL = "weekly@t.io"


@pytest.fixture
def track_db(get_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_SECRET", SECRET)
    conn = get_db()
    delphoi.ensure_tables(conn)
    delphoi.ensure_fg_tables(conn)
    delphoi_brief.ensure_brief_tables(conn)
    saa.ensure_auth_tables(conn)
    conn.close()
    saa._RL_HITS.clear()
    return get_db


def _app(track_db):
    from fastmcp import FastMCP
    mcp = FastMCP("k5-track-test")
    saa.register_tools(mcp, {"get_db": track_db, "capture_state": {}})
    return mcp.http_app(stateless_http=True)


async def _req(app, method, path, **kw):
    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await getattr(c, method)(path, **kw)


def _hdr(email=EMAIL):
    return {"Authorization": f"Bearer {saa.issue_session_token(1, email)}"}


def _mk_done_job(get_db, job_id="dlph-k5t", email=EMAIL,
                 goal="How is the mood about the cost of living?", n=100):
    """Kész job + a hozzá tartozó (tracking=none) brief — a /saas/submit út
    nyoma kicsiben."""
    saved = delphoi_brief.save_brief(get_db, email, {
        "goal": goal, "instrument": "concept", "stimuli": [goal],
        "country": "HU", "n": n, "dimensions": ["appeal"],
        "tracking": "none", "report": {"lang": "en", "formats": ["pdf"]}})
    assert saved["ok"]
    panel_spec = {"country": "HU", "n_per_cell": n,
                  "brief_id": saved["brief_id"], "goal": goal}
    ts = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO delphoi_jobs (id, user_id, status, input_kind, input_text, "
        "panel_spec, credits_cost, result_json, created_at, completed_at) "
        "VALUES (?, ?, 'done', 'concept', ?, ?, 4, ?, ?, ?)",
        (job_id, email, goal, json.dumps(panel_spec),
         json.dumps({"overall_score": 3.4, "n": n}), ts, ts))
    conn.commit()
    conn.close()
    return job_id, saved["brief_id"], panel_spec


def test_track_turns_on_weekly(track_db):
    job_id, orig_brief, panel_spec = _mk_done_job(track_db)
    app = _app(track_db)
    r = asyncio.run(_req(app, "post", f"/saas/jobs/{job_id}/track", headers=_hdr()))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["cadence"] == "weekly" and not body["already"]
    # őszinte ár: ugyanaz a képlet, mint a valódi levonásé
    assert body["cost"] == delphoi.job_cost(panel_spec)
    # az első ismételt futás +7 nap múlva (nem holnap reggel)
    nr = datetime.fromisoformat(body["next_run"])
    assert timedelta(days=6) < nr - datetime.now(timezone.utc) <= timedelta(days=7)
    wid = body["brief_id"]
    assert wid and wid != orig_brief          # új (weekly) brief, nem az eredeti
    conn = track_db()
    spec = json.loads(conn.execute(
        "SELECT spec_json FROM delphoi_briefs WHERE brief_id=?",
        (wid,)).fetchone()["spec_json"])
    tr = conn.execute("SELECT * FROM delphoi_tracking WHERE brief_id=?",
                      (wid,)).fetchone()
    runs = conn.execute("SELECT * FROM delphoi_brief_runs WHERE brief_id=?",
                        (wid,)).fetchall()
    conn.close()
    assert spec["tracking"] == "weekly" and spec["goal"] == panel_spec["goal"]
    assert tr["active"] == 1 and tr["cadence"] == "weekly"
    # idősor-mag: a mostani kész futás az első pont
    assert len(runs) == 1 and runs[0]["job_id"] == job_id
    assert runs[0]["overall_score"] == 3.4


def test_track_idempotent_second_click(track_db):
    job_id, _, _ = _mk_done_job(track_db, job_id="dlph-k5t2")
    app = _app(track_db)
    asyncio.run(_req(app, "post", f"/saas/jobs/{job_id}/track", headers=_hdr()))
    r2 = asyncio.run(_req(app, "post", f"/saas/jobs/{job_id}/track", headers=_hdr()))
    body = r2.json()
    assert body["ok"] and body["already"] is True
    conn = track_db()
    n_runs = conn.execute(
        "SELECT COUNT(*) AS c FROM delphoi_brief_runs WHERE job_id=?",
        (job_id,)).fetchone()["c"]
    conn.close()
    assert n_runs == 1                        # nincs dupla idősor-pont


def test_job_payload_carries_weekly_block(track_db):
    job_id, _, panel_spec = _mk_done_job(track_db, job_id="dlph-k5t3")
    app = _app(track_db)
    before = asyncio.run(_req(app, "get", f"/saas/jobs/{job_id}", headers=_hdr())).json()
    assert before["ok"] and before["weekly"]["tracked"] is False
    assert before["weekly"]["cost"] == delphoi.job_cost(panel_spec)
    asyncio.run(_req(app, "post", f"/saas/jobs/{job_id}/track", headers=_hdr()))
    after = asyncio.run(_req(app, "get", f"/saas/jobs/{job_id}", headers=_hdr())).json()
    assert after["weekly"]["tracked"] is True and after["weekly"]["brief_id"]


def test_tracking_list_pause_resume(track_db):
    job_id, _, _ = _mk_done_job(track_db, job_id="dlph-k5t4")
    app = _app(track_db)
    wid = asyncio.run(_req(app, "post", f"/saas/jobs/{job_id}/track",
                           headers=_hdr())).json()["brief_id"]
    items = asyncio.run(_req(app, "get", "/saas/tracking",
                             headers=_hdr())).json()["items"]
    assert len(items) == 1
    it = items[0]
    assert (it["brief_id"] == wid and it["active"] is True
            and it["cadence"] == "weekly" and it["cost"] >= 1
            and "cost of living" in it["goal"] and it["n_runs"] == 1)
    # szünet
    r = asyncio.run(_req(app, "post", f"/saas/tracking/{wid}",
                         headers=_hdr(), json={"active": False})).json()
    assert r["ok"] and r["active"] is False
    assert asyncio.run(_req(app, "get", "/saas/tracking",
                            headers=_hdr())).json()["items"][0]["active"] is False
    # folytatás: aktív + a következő futás +1 kadencia mostantól
    r2 = asyncio.run(_req(app, "post", f"/saas/tracking/{wid}",
                          headers=_hdr(), json={"active": True})).json()
    assert r2["ok"] and r2["active"] is True
    nr = datetime.fromisoformat(r2["next_run"])
    assert nr - datetime.now(timezone.utc) > timedelta(days=6)


def test_track_owner_only_and_session_required(track_db):
    job_id, _, _ = _mk_done_job(track_db, job_id="dlph-k5t5")
    app = _app(track_db)
    assert asyncio.run(_req(app, "post",
                            f"/saas/jobs/{job_id}/track")).status_code == 401
    r = asyncio.run(_req(app, "post", f"/saas/jobs/{job_id}/track",
                         headers=_hdr("other@t.io")))
    assert r.status_code == 404
    # idegen brief szüneteltetése is deny
    wid = asyncio.run(_req(app, "post", f"/saas/jobs/{job_id}/track",
                           headers=_hdr())).json()["brief_id"]
    r2 = asyncio.run(_req(app, "post", f"/saas/tracking/{wid}",
                          headers=_hdr("other@t.io"), json={"active": False}))
    assert r2.status_code == 404


def test_cron_sees_tracked_brief_when_due(track_db):
    """A meglévő 06:50-es tick (due_briefs) a bekapcsolt követést a kadencia
    lejártakor látja — előtte nem (nem fut dupla mérés a bekapcsolás napján)."""
    job_id, _, _ = _mk_done_job(track_db, job_id="dlph-k5t6")
    app = _app(track_db)
    wid = asyncio.run(_req(app, "post", f"/saas/jobs/{job_id}/track",
                           headers=_hdr())).json()["brief_id"]
    now = datetime.now(timezone.utc)
    assert not [b for b in delphoi_tracking.due_briefs(track_db, now)
                if b["brief_id"] == wid]
    later = now + timedelta(days=8)
    due = [b for b in delphoi_tracking.due_briefs(track_db, later)
           if b["brief_id"] == wid]
    assert len(due) == 1 and due[0]["user_id"] == EMAIL
