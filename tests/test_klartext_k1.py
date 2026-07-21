"""test_klartext_k1.py — OPERATION KLARTEXT K1 + MOD1: kérdező-út + panelméret-motor.

CÉL-BIZONYÍTÉKOK:
  - MOD1-C pacer: N=1000 × k=3 (3000 hívás) BIZONYÍTOTTAN az L1-keretben marad
    (10k RPM / 400k TPM) — szimulált órával, bármely 60 mp-es ablakra;
  - AIMD: 429-re felezés, sikersorozatra +1;
  - futásidő-becslés a pacing-KONFIGBÓL számolva (nem tipp) — a watchdog
    határideje f(N): max(30 perc, becslés×2);
  - DELPHOI_N_MAX env-plafon (API-default 2000) a briefben ÉS a motorban;
  - /saas/precheck: LLM-MENTES zöld/sárga/piros verdikt + kredit/idő-becslés,
    piros KONKRÉT átfogalmazási javaslattal; nyitott, de rate-limitelt;
  - embed-büdzsé-kapu a job-FELVÉTELKOR: emberi üzenet a levonás ELŐTT;
  - a heti nowcast N-je env-ből jön (DELPHOI_NOWCAST_N), nem user-paraméter.
"""
import asyncio
import inspect
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from plugins import delphoi, delphoi_brief, delphoi_saas_auth as saa, pollster

SECRET = "test-klartext-secret"


@pytest.fixture
def k1_db(get_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_SECRET", SECRET)
    monkeypatch.setenv("DELPHOI_SAAS_DEV_MODE", "1")
    conn = get_db()
    delphoi.ensure_tables(conn)
    delphoi.ensure_fg_tables(conn)
    delphoi_brief.ensure_brief_tables(conn)
    saa.ensure_auth_tables(conn)
    conn.close()
    saa._RL_HITS.clear()
    return get_db


def _client_app(k1_db):
    """Valódi FastMCP + register_tools + http_app (a SIBYLLE-teszt mintája)."""
    from fastmcp import FastMCP
    mcp = FastMCP("klartext-test")
    saa.register_tools(mcp, {"get_db": k1_db, "capture_state": {}})
    return mcp.http_app(stateless_http=True)


async def _req(app, method, path, **kw):
    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await getattr(c, method)(path, **kw)


# ── MOD1-C: pacer — szimulált órás keret-bizonyítás ────────────────────────

def test_pacer_n1000_k3_stays_within_l1_budget():
    """3000 hívás (N=1000 × k=3), 750 token/hívás: BÁRMELY 60 mp-es ablakban
    kérés ≤ 10 000 és token ≤ 400 000 — és a futás nem 'ingyen' gyors."""
    RPM, TPM, SAFETY, TOKENS = 10_000, 400_000, 0.8, 750
    t = [0.0]

    def clock():
        return t[0]

    async def fake_sleep(s):
        t[0] += max(float(s), 0.001)

    pacer = pollster.RatePacer(rpm=RPM * SAFETY, tpm=TPM * SAFETY,
                               conc_start=32, conc_min=2, conc_max=64,
                               clock=clock, sleep=fake_sleep)
    events: list[float] = []

    async def _run():
        for _ in range(3000):
            await pacer.acquire(TOKENS)
            events.append(t[0])
            pacer.release(True)

    asyncio.run(_run())
    assert pacer.stats["acquired"] == 3000

    i = 0
    for j in range(len(events)):
        while events[j] - events[i] >= 60.0:
            i += 1
        window = j - i + 1
        assert window <= RPM, f"RPM-sértés a(z) {j}. hívásnál: {window}"
        assert window * TOKENS <= TPM, f"TPM-sértés a(z) {j}. hívásnál: {window * TOKENS}"
    # a keret-tartás nem lehet nulla-idős trükk: a TPM-fék tempót diktál
    assert t[0] >= (3000 * TOKENS / (TPM * SAFETY) - 1) * 60.0


def test_pacer_aimd_halves_on_429_and_regrows():
    pacer = pollster.RatePacer(rpm=1e9, tpm=1e9, conc_start=16,
                               conc_min=2, conc_max=32)
    pacer.on_rate_limited()
    assert pacer.concurrency == 8
    pacer.on_rate_limited()
    assert pacer.concurrency == 4
    for _ in range(pollster.RatePacer.AIMD_SUCCESS_STEP):
        pacer.release(True)
    assert pacer.concurrency == 5   # additív visszaépülés
    pacer.on_rate_limited()
    pacer.on_rate_limited()
    pacer.on_rate_limited()
    assert pacer.concurrency == 2   # conc_min alá sosem


def test_make_pacer_reads_env(monkeypatch):
    monkeypatch.setenv("DELPHOI_CONCURRENCY_MAX", "12")
    monkeypatch.setenv("DELPHOI_CONCURRENCY", "5")
    p = pollster.make_pacer()
    assert p.concurrency == 5 and p._conc_max == 12


# ── futásidő-becslés + watchdog f(N) ───────────────────────────────────────

def test_estimate_runtime_computed_from_pacing_config():
    rl = pollster.rate_limits()
    cpm = min(rl["conc_max"] * 60.0 / rl["latency_s"],
              rl["tpm"] * rl["safety"] / rl["tokens_per_call"],
              rl["rpm"] * rl["safety"])
    calls = delphoi.job_call_count({"n_per_cell": 100})
    assert calls == 100 * delphoi.fg_samples()
    est = delphoi.estimate_runtime_seconds(calls)
    assert est == math.ceil(calls / cpm * 60.0) + delphoi.RUNTIME_OVERHEAD_S
    # monoton: az 1000-es panel becslése nagyobb, mint a 100-asé
    assert delphoi.estimate_runtime_seconds(
        delphoi.job_call_count({"n_per_cell": 1000})) > est


def _insert_running(get_db, job_id: str, n: int, minutes_ago: int):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO delphoi_jobs (id, user_id, status, input_kind, input_text, "
            "panel_spec, credits_cost, created_at, started_at) "
            "VALUES (?, 'wd-user', 'running', 'concept', 'x', ?, 1, ?, ?)",
            (job_id, json.dumps({"country": "HU", "n_per_cell": n}),
             datetime.now(timezone.utc).isoformat(),
             (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()))
        conn.commit()
    finally:
        conn.close()


def test_watchdog_deadline_is_fn_of_n(k1_db, monkeypatch):
    """Lassú pacing-konfignál a nagy panel határideje jóval 30 perc fölé nő:
    a 35 perce futó N=1000-es job ÉL, a 35 perce futó N=30-as bukik+refund."""
    monkeypatch.setenv("DELPHOI_CONCURRENCY_MAX", "4")
    monkeypatch.setenv("DELPHOI_CALL_LATENCY_S", "10")
    big = delphoi.watchdog_deadline_minutes({"n_per_cell": 1000})
    small = delphoi.watchdog_deadline_minutes({"n_per_cell": 30})
    assert small == delphoi.WATCHDOG_MINUTES        # 30 perces padló
    assert big > 2 * delphoi.WATCHDOG_MINUTES       # f(N) valóban skálázódik

    _insert_running(k1_db, "dlph-wd-big", 1000, 35)
    _insert_running(k1_db, "dlph-wd-small", 30, 35)
    delphoi.watchdog_sweep(k1_db)
    conn = k1_db()
    try:
        rows = {r["id"]: r["status"] for r in conn.execute(
            "SELECT id, status FROM delphoi_jobs").fetchall()}
        refunds = conn.execute(
            "SELECT COUNT(*) c FROM delphoi_credit_ledger WHERE reason LIKE 'refund:%'"
        ).fetchone()["c"]
    finally:
        conn.close()
    assert rows["dlph-wd-big"] == "running"
    assert rows["dlph-wd-small"] == "failed" and refunds == 1


# ── DELPHOI_N_MAX plafon ───────────────────────────────────────────────────

def test_n_max_env_and_create_job_guard(k1_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_N_MAX", "1000")
    assert delphoi.n_max() == 1000
    res = delphoi.create_job(k1_db, "nmax-user", "concept", "x",
                             {"country": "HU", "n_per_cell": 1500})
    assert not res["ok"] and "plafon" in res["error"]


def test_brief_validator_accepts_1000_rejects_above_max():
    spec = {"goal": "Would people buy this?", "instrument": "concept",
            "stimuli": ["s"], "country": "HU", "n": 1000,
            "dimensions": ["appeal"], "tracking": "none",
            "report": {"lang": "en", "formats": ["pdf"]}}
    assert not [e for e in delphoi_brief.validate_brief(spec) if " n " in f" {e} "]
    over = dict(spec, n=delphoi_brief.MAX_N + 1)
    assert any("sávon kívül" in e for e in delphoi_brief.validate_brief(over))


# ── /saas/precheck — élő kérdés-ellenőr (LLM-mentes) ───────────────────────

def test_precheck_green_yellow_red(k1_db):
    app = _client_app(k1_db)

    async def _run():
        g = (await _req(app, "post", "/saas/precheck", json={
            "question": "Would people pay $9 a month for an ad-free news app?",
            "n": 100})).json()
        assert g["ok"] and g["verdict"] == "green"
        assert "we can measure this" in g["message"]
        assert g["credit_estimate"] >= 1 and g["eta_minutes"] >= 1
        assert g["n_max"] == delphoi.n_max()

        y = (await _req(app, "post", "/saas/precheck", json={
            "question": "What do people think about the new GDP numbers?"})).json()
        assert y["verdict"] == "yellow" and "Treat with care" in y["message"]

        r = (await _req(app, "post", "/saas/precheck", json={
            "question": "Which industry sector will grow fastest next quarter?"})).json()
        assert r["verdict"] == "red"
        assert "hard data" in r["message"]
        assert r.get("suggestion")          # KONKRÉT átfogalmazási javaslat
        assert r["suggestion"].endswith("?")

        e = await _req(app, "post", "/saas/precheck", json={"question": ""})
        assert e.status_code == 400

    asyncio.run(_run())


def test_precheck_eta_scales_with_n(k1_db):
    app = _client_app(k1_db)

    async def _run():
        small = (await _req(app, "post", "/saas/precheck",
                            json={"question": "Would people buy this?", "n": 100})).json()
        big = (await _req(app, "post", "/saas/precheck",
                          json={"question": "Would people buy this?", "n": 1000})).json()
        assert big["eta_seconds"] > small["eta_seconds"]
        assert big["credit_estimate"] > small["credit_estimate"]

    asyncio.run(_run())


def test_precheck_open_but_rate_limited(k1_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_PRECHECK_MAX", "2")
    app = _client_app(k1_db)

    async def _run():
        for _ in range(2):
            assert (await _req(app, "post", "/saas/precheck",
                               json={"question": "q?"})).status_code == 200
        r = await _req(app, "post", "/saas/precheck", json={"question": "q?"})
        assert r.status_code == 429 and r.json()["error"] == "rate_limited"

    asyncio.run(_run())


def test_precheck_capacity_warning_when_budget_thin(k1_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_EMBED_BUDGET", "1000")
    app = _client_app(k1_db)

    async def _run():
        r = (await _req(app, "post", "/saas/precheck",
                        json={"question": "Would people buy this?", "n": 500})).json()
        assert "capacity_warning" in r and "tomorrow" in r["capacity_warning"]

    asyncio.run(_run())


# ── embed-büdzsé-kapu a job-FELVÉTELKOR (levonás ELŐTT) ────────────────────

VALID_SPEC = {
    "goal": "Would city 30-somethings buy this?", "instrument": "concept",
    "stimuli": ["New green packaging with 40% less plastic."],
    "country": "HU", "n": 30, "dimensions": ["appeal"],
    "tracking": "none", "report": {"lang": "en", "formats": ["pdf"]},
}


async def _login_and_session(app, email):
    r = await _req(app, "post", "/saas/auth/request-link", json={"email": email})
    token = r.json()["magic_link"].split("token=", 1)[1]
    return (await _req(app, "get",
                       f"/saas/auth/verify?token={token}")).json()["session_token"]


def test_submit_embed_gate_blocks_before_charge(k1_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_EMBED_BUDGET", "1000")   # N=30×k=3 becslés ~9500 > 1000
    app = _client_app(k1_db)

    async def _run():
        sess = await _login_and_session(app, "cap@t.io")
        r = await _req(app, "post", "/saas/submit",
                       headers={"Authorization": f"Bearer {sess}"},
                       json={"spec": VALID_SPEC})
        assert r.status_code == 429
        body = r.json()
        assert body["error"] == "capacity_exhausted"
        assert "Nothing was charged" in body["message"]     # emberi üzenet

    asyncio.run(_run())
    # a levonás ELŐTT állt meg: a welcome-egyenleg érintetlen, job nem született
    balance = delphoi.get_credits(k1_db, "cap@t.io")["balance"]
    assert balance == delphoi.WELCOME_CREDITS
    conn = k1_db()
    try:
        jobs = conn.execute("SELECT COUNT(*) c FROM delphoi_jobs").fetchone()["c"]
    finally:
        conn.close()
    assert jobs == 0


# ── nowcast N: env, nem user-paraméter ─────────────────────────────────────

def test_nowcast_n_env_driven(monkeypatch):
    monkeypatch.setenv("DELPHOI_NOWCAST_N", "25")
    assert delphoi.nowcast_n() == 25
    # a futtató default-ja None → env-feloldás (a cron sosem ad n-t)
    assert inspect.signature(delphoi.run_entity_nowcast).parameters["n"].default is None


# ── get_job: n_panel a payload első szintjén (riport-oldalnak) ─────────────

def test_get_job_exposes_n_panel(k1_db):
    delphoi.add_credits(k1_db, "np-user", 100, "np-test")
    created = delphoi.create_job(k1_db, "np-user", "concept", "x",
                                 {"country": "HU", "n_per_cell": 200})
    assert created["ok"], created
    out = delphoi.get_job(k1_db, created["job_id"], "np-user")
    assert out["ok"] and out["n_panel"] == 200
