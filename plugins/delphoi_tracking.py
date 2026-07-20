"""plugins/delphoi_tracking.py — PYTHIA B1: tracking-runner (gördülő brief-mérés).

Napi cron-tick: az esedékes (active=1, next_run <= most) tracking-briefek a
NORMÁL job-úton futnak le — delphoi.create_job (ATOMI kredit-levonás) +
delphoi.process_job IMPORTTAL (nem másolat!) —, majd idősor-pont íródik a
delphoi_brief_runs táblába (a riport-mű idősor-fejezetének adatforrása).

KREDIT-VASSZABÁLY: elégtelen kredit → SKIP + hangos log, a next_run a következő
kadencia-pontra lép (a kihagyott periódus nem torlódik fel). Az egyenleg SOSEM
mehet negatívba — ezt a delphoi.charge atomi `balance >= cost` feltétele
garantálja DB-szinten, a runner nem is tudná megkerülni.
(E-mail-értesítés elégtelen kreditnél: terv a B1-jelentésben — a notify_email
a brief-specben már gyűlik, a küldő-út a deploy #2 köre.)

Cron-minta: a nulltarif_guard.py bevált párosa — seed_cron DEPLOYKOR (a
register_tools szándékosan NEM hívja) + cron_entry a server _cron_loop
special-case-éből. FIGYELEM: a recept neve delphoi_ prefixű, ezért a server
_cron_loop-jában a delphoi_tracking special-case-nek a generikus delphoi_
special-case ELÉ kell kerülnie (a pontos diff a B1-jelentésben).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("plugins.delphoi_tracking")

__plugin_meta__ = {
    "name": "delphoi_tracking",
    "version": "1.0.0",
    "description": "DELPHOI tracking-runner (B1) — esedekes briefek normal job-uton + idosor-pont",
}

_DEPS: dict | None = None

CRON_RECIPE_NAME = "delphoi_tracking_daily"
CRON_SCHEDULE = "50 6 * * *"   # napi 06:50 UTC — a NULLTARIF-őr (06:20) után,
                               # a heti nowcast (hétfő 07:30) előtt
CRON_DESC = ("DELPHOI tracking-runner — esedékes gördülő briefek futtatása "
             "(normál job-út, kredit-levonással) + idősor-pont")

CADENCE_DAYS = {"weekly": 7, "monthly": 30}   # KISS: fix napszám, nem naptári hó


def next_run_after(cadence: str, from_dt: datetime) -> str:
    days = CADENCE_DAYS.get(cadence)
    if not days:
        raise ValueError(f"ismeretlen kadencia: {cadence!r}")
    return (from_dt + timedelta(days=days)).isoformat()


def due_briefs(get_db, now: datetime | None = None) -> list[dict]:
    """Az esedékes tracking-sorok a brief-törzzsel joinolva."""
    now = now or datetime.now(timezone.utc)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT t.brief_id, t.cadence, t.next_run, "
            "       b.user_id, b.spec_json, b.spec_hash "
            "FROM delphoi_tracking t "
            "JOIN delphoi_briefs b ON b.brief_id = t.brief_id "
            "WHERE t.active = 1 AND t.next_run <= ? "
            "ORDER BY t.next_run ASC", (now.isoformat(),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _advance(get_db, brief_id: str, cadence: str, now: datetime) -> None:
    conn = get_db()
    try:
        conn.execute("UPDATE delphoi_tracking SET next_run=? WHERE brief_id=?",
                     (next_run_after(cadence, now), brief_id))
        conn.commit()
    finally:
        conn.close()


def record_run(get_db, brief_id: str, job_id: str, spec_hash: str,
               overall_score: float | None, run_at: str | None = None) -> None:
    """Idősor-pont a delphoi_brief_runs-ba (a riport idősor-fejezete olvassa)."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO delphoi_brief_runs (brief_id, job_id, run_at, spec_hash, overall_score) "
            "VALUES (?, ?, ?, ?, ?)",
            (brief_id, job_id, run_at or datetime.now(timezone.utc).isoformat(),
             spec_hash, overall_score))
        conn.commit()
    finally:
        conn.close()


async def run_due_briefs(deps: dict, now: datetime | None = None,
                         process_fn=None) -> dict:
    """A tick: esedékes briefek → create_job (atomi levonás) → process_job
    (IMPORT a delphoi-ból; process_fn injektálható a tesztekhez) → idősor-pont
    → next_run léptetés. Egy brief hibája nem töri a többit."""
    from plugins import delphoi, delphoi_brief

    get_db = deps["get_db"]
    now = now or datetime.now(timezone.utc)
    results = []
    for row in due_briefs(get_db, now):
        brief_id = row["brief_id"]
        entry: dict = {"brief_id": brief_id, "cadence": row["cadence"]}
        try:
            spec = json.loads(row["spec_json"])
            kind, text, panel_spec, variants = delphoi_brief.brief_to_job_args(
                spec, brief_id)
            created = delphoi.create_job(get_db, row["user_id"], kind, text,
                                         panel_spec, variants)
            if not created.get("ok"):
                # Elégtelen kredit / validációs hiba: SKIP + hangos log, a
                # periódus kimarad (next_run lép), egyenleg sosem negatív.
                entry.update({"ok": False, "skipped": True,
                              "error": created.get("error")})
                logger.warning("delphoi tracking SKIP %s (%s): %s — a periódus "
                               "kimarad, kredit nem mozgott",
                               brief_id, row["user_id"], created.get("error"))
                _advance(get_db, brief_id, row["cadence"], now)
                results.append(entry)
                continue
            job_id = created["job_id"]
            rep = await (process_fn(deps, job_id) if process_fn
                         else delphoi.process_job(deps, job_id))
            overall = None
            if rep.get("ok"):
                overall = (rep.get("result") or {}).get("overall_score")
            record_run(get_db, brief_id, job_id, row["spec_hash"], overall,
                       run_at=now.isoformat())
            _advance(get_db, brief_id, row["cadence"], now)
            entry.update({"ok": bool(rep.get("ok")), "job_id": job_id,
                          "overall_score": overall})
            if not rep.get("ok"):
                entry["error"] = rep.get("error")
        except Exception as e:  # noqa: BLE001 — egy brief hibája nem töri a tick-et
            logger.exception("delphoi tracking brief %s crashed", brief_id)
            entry.update({"ok": False, "error": f"{type(e).__name__}: {e}"})
        results.append(entry)
    ran = sum(1 for r in results if r.get("ok"))
    logger.info("delphoi tracking tick kész: %d esedékes, %d lefutott, %d skip/hiba",
                len(results), ran, len(results) - ran)
    return {"ok": True, "due": len(results), "ran": ran, "results": results}


# ── Cron-előkészítés (a seed a B1-deploy lépése — itt NEM fut automatikusan) ──

def seed_cron(conn) -> bool:
    """Idempotens recept-seed a pyramid_recipes-be (nulltarif_guard-minta).
    DEPLOYKOR hívandó — a register_tools szándékosan nem hívja."""
    exists = conn.execute("SELECT 1 FROM pyramid_recipes WHERE name=?",
                          (CRON_RECIPE_NAME,)).fetchone()
    if exists:
        return False
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, "
        "created_by, created_at, updated_at, cron_schedule, cron_model, cron_enabled, cron_delivery) "
        "VALUES (?, ?, '[]', ?, 'system', ?, ?, ?, 'deepseek', 1, 'none')",
        (CRON_RECIPE_NAME, CRON_DESC,
         "(special-cased — runtime: plugins.delphoi_tracking.cron_entry)",
         ts, ts, CRON_SCHEDULE))
    conn.commit()
    logger.info("delphoi_tracking recipe seed: %s (cron=%s)",
                CRON_RECIPE_NAME, CRON_SCHEDULE)
    return True


async def cron_entry(recipe_name: str, deps: dict | None = None) -> None:
    """A server _cron_loop special-case belépési pontja (bekötés deploykor —
    a delphoi_ generikus special-case ELÉ, mert a név delphoi_ prefixű)."""
    d = deps or _DEPS
    if not d:
        logger.error("delphoi_tracking cron_entry: nincs deps — skip")
        return
    try:
        rep = await run_due_briefs(d)
        logger.info("delphoi_tracking cron kész: %s",
                    json.dumps({k: rep[k] for k in ("due", "ran")}, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.exception("delphoi_tracking cron_entry (%s) failed", recipe_name)


def register_tools(app, deps):
    """Nem regisztrál MCP-toolt (tool-count fegyelem) — csak a deps-t tárolja.
    A cron-seed (seed_cron) és a _cron_loop-bekötés a B1-deploy lépése."""
    global _DEPS
    _DEPS = deps
    logger.info("delphoi_tracking betöltve (cron-seed deploykor: %s)", CRON_RECIPE_NAME)
