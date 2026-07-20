"""test_delphoi_public_mcp.py — PYTHIA B3: a publikus SaaS MCP-kapu.

IZOLÁCIÓS CÉL-BIZONYÍTÉK:
  - a publikus felület tool-listájában a 4 delphoi-toolon kívül SEMMI nincs
    (in-memory MCP-kliens ÉS HTTP-transzport szinten is);
  - belső toolnevek (write_memory, capture_gmail_poll, …) a publikus felületen
    NEM LÉTEZNEK;
  - érvénytelen/revokált kulcs = deny; rate-limit él; kreditellenőrzés a
    job-felvétel ELŐTT; nyers persona-adat nem megy ki.
"""
import asyncio
import json
import sqlite3

import pytest

from plugins import delphoi, delphoi_brief, delphoi_public_mcp as dpub

# Belső Bridge-toolnevek, amelyeknek a publikus felületen TILOS létezniük.
INTERNAL_TOOL_NAMES = (
    "write_memory", "read_memory", "search_memory", "list_memory",
    "capture_gmail_poll", "capture_send_email", "capture_inbox",
    "create_task", "list_tasks", "ai_task", "send_message", "read_messages",
    "delphoi_run_focus_group", "delphoi_entity_nowcast_run",
    "delphoi_verify_ledger", "delphoi_get_credits",
)

VALID_SPEC = {
    "goal": "Melyik csomagolás-szöveg győzi meg a városi 30-asokat?",
    "instrument": "concept",
    "stimuli": ["Új zöld csomagolás, 40% kevesebb műanyaggal."],
    "country": "HU", "n": 30, "dimensions": ["appeal"],
    "tracking": "none", "report": {"lang": "hu", "formats": ["pdf"]},
}


@pytest.fixture
def pub_db(get_db):
    conn = get_db()
    delphoi.ensure_tables(conn)
    delphoi.ensure_fg_tables(conn)
    delphoi_brief.ensure_brief_tables(conn)
    dpub.ensure_key_tables(conn)
    conn.close()
    dpub._BUCKETS.clear()
    return get_db


@pytest.fixture
def pub_mcp(pub_db):
    return dpub.build_public_mcp({"get_db": pub_db})


def _key(pub_db, ext="user:7", label="teszt") -> str:
    return dpub.generate_api_key(pub_db, "echolot", ext, label)["api_key"]


async def _call(mcp, tool, args):
    from fastmcp import Client
    async with Client(mcp) as c:
        res = await c.call_tool(tool, args)
        return json.loads(res.content[0].text)


# ── IZOLÁCIÓ (cél-bizonyíték) ──────────────────────────────────────────────

def test_public_tool_list_is_exactly_the_four(pub_mcp):
    from fastmcp import Client

    async def _list():
        async with Client(pub_mcp) as c:
            return sorted(t.name for t in await c.list_tools())

    names = asyncio.run(_list())
    assert names == sorted(dpub.PUBLIC_TOOL_NAMES)
    for internal in INTERNAL_TOOL_NAMES:
        assert internal not in names, f"belső tool a publikus felületen: {internal}"


def test_http_transport_isolation(pub_db):
    """HTTP-szinten (a mount-út: LazyLifespanASGI + Starlette Mount) is
    KIZÁRÓLAG a 4 tool látszik a /saas/mcp úton."""
    import httpx
    from starlette.applications import Starlette
    from starlette.routing import Mount

    wrapper = dpub.build_public_asgi({"get_db": pub_db})
    app = Starlette(routes=[Mount(dpub.PUBLIC_MOUNT_PATH, app=wrapper)])

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post(
                "/saas/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={"accept": "application/json, text/event-stream"})
            assert r.status_code == 200
            body = r.text
            if "data: " in body:
                body = body.split("data: ", 1)[1].split("\n")[0]
            tools = json.loads(body)["result"]["tools"]
            await wrapper.aclose()   # teszt-higiénia (élesben processz-halál zár)
            return sorted(t["name"] for t in tools)

    names = asyncio.run(_run())
    assert names == sorted(dpub.PUBLIC_TOOL_NAMES)
    for internal in INTERNAL_TOOL_NAMES:
        assert internal not in names


def test_server_wiring_mount_is_isolated():
    """A server.py a públikus appot KÜLÖN Mount-ként fűzi be (nem a fő mcp-re
    regisztrál), és a deps-ből csak get_db+siliconflow megy át."""
    import os
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "server.py"), encoding="utf-8").read()
    assert "build_public_asgi" in src
    assert "_additional_http_routes.append" in src
    assert "PUBLIC_MOUNT_PATH" in src
    # a publikus deps-ben nincs ai_task_func / capture_state (belső képességek)
    import re
    m = re.search(r"_saas_deps = \{(.*?)\}", src, re.S)
    assert m, "nincs _saas_deps blokk a server.py-ban"
    assert "ai_task" not in m.group(1)
    assert "capture_state" not in m.group(1)


def test_register_tools_registers_nothing_on_main_app(pub_db, fake_app):
    """Library-plugin konvenció: a fő appra NULLA tool megy."""
    dpub.register_tools(fake_app, {"get_db": pub_db})
    assert fake_app.tools == {}


# ── AUTH: kulcs-életciklus + deny ──────────────────────────────────────────

def test_invalid_key_denied(pub_mcp):
    res = asyncio.run(_call(pub_mcp, "delphoi_credits", {"api_key": "hamis-kulcs"}))
    assert res == {"ok": False, "error": "invalid_api_key"}


def test_key_lifecycle_generate_list_revoke(pub_db):
    made = dpub.generate_api_key(pub_db, "echolot", "user:7", "gépem")
    assert made["ok"] and made["api_key"].startswith(dpub.KEY_PREFIX)
    # resolve: érvényes kulcs → user-sor, a kredit-ref a meglévő owner_key minta
    user = dpub.resolve_api_key(pub_db, made["api_key"])
    assert user is not None and dpub.account_ref(user) == "user:7"
    # lista: plaintext SOHA, csak prefix+meta
    keys = dpub.list_api_keys(pub_db, "echolot", "user:7")
    assert len(keys) == 1 and keys[0]["label"] == "gépem"
    assert made["api_key"] not in json.dumps(keys)
    # revoke → resolve None (deny)
    rev = dpub.revoke_api_key(pub_db, "echolot", "user:7", made["key_id"])
    assert rev["ok"]
    assert dpub.resolve_api_key(pub_db, made["api_key"]) is None


def test_revoke_other_users_key_denied(pub_db):
    made = dpub.generate_api_key(pub_db, "echolot", "user:7", "enyém")
    res = dpub.revoke_api_key(pub_db, "echolot", "user:999", made["key_id"])
    assert res == {"ok": False, "error": "not_found"}
    assert dpub.resolve_api_key(pub_db, made["api_key"]) is not None


def test_revoked_key_denied_on_tool(pub_db, pub_mcp):
    made = dpub.generate_api_key(pub_db, "echolot", "user:7", "x")
    dpub.revoke_api_key(pub_db, "echolot", "user:7", made["key_id"])
    res = asyncio.run(_call(pub_mcp, "delphoi_credits", {"api_key": made["api_key"]}))
    assert res["error"] == "invalid_api_key"


def test_db_stores_only_sha256_hash(pub_db):
    made = dpub.generate_api_key(pub_db, "echolot", "user:7", "h")
    conn = pub_db()
    row = conn.execute("SELECT key_hash FROM delphoi_api_keys WHERE id=?",
                       (made["key_id"],)).fetchone()
    conn.close()
    import hashlib
    assert row["key_hash"] == hashlib.sha256(made["api_key"].encode()).hexdigest()
    assert made["api_key"] not in row["key_hash"]


# ── RATE-LIMIT (token-bucket, env-konfig) ──────────────────────────────────

def test_rate_limit_bucket(monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_RATE_PER_MIN", "60")
    monkeypatch.setenv("DELPHOI_SAAS_BURST", "2")
    dpub._BUCKETS.clear()
    assert dpub.rate_limit_allow("kh", now=100.0) is True
    assert dpub.rate_limit_allow("kh", now=100.0) is True
    assert dpub.rate_limit_allow("kh", now=100.0) is False    # burst kimerült
    assert dpub.rate_limit_allow("kh", now=101.5) is True     # 1/s feltöltés
    assert dpub.rate_limit_allow("más-kulcs", now=100.0) is True  # kulcsonkénti


def test_rate_limited_tool_call(pub_db, pub_mcp, monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_RATE_PER_MIN", "1")
    monkeypatch.setenv("DELPHOI_SAAS_BURST", "1")
    dpub._BUCKETS.clear()
    key = _key(pub_db)
    ok = asyncio.run(_call(pub_mcp, "delphoi_credits", {"api_key": key}))
    assert ok["ok"] is True
    denied = asyncio.run(_call(pub_mcp, "delphoi_credits", {"api_key": key}))
    assert denied == {"ok": False, "error": "rate_limited"}


# ── SUBMIT: brief-séma + scope + kredit-kapu + háttér-feldolgozás ──────────

def test_submit_invalid_brief_errors(pub_db, pub_mcp):
    key = _key(pub_db)
    bad = dict(VALID_SPEC, segments={"gender": ["nő"]})
    res = asyncio.run(_call(pub_mcp, "delphoi_submit_brief",
                            {"api_key": key, "spec_json": json.dumps(bad)}))
    assert res["error"] == "invalid_brief"
    assert any("tiltott szegmens-tengely" in e for e in res["errors"])


def test_submit_credit_check_before_intake(pub_db, pub_mcp):
    """n=500 → 17 kredit > a 2 kredites signup-grant → insufficient_credits,
    és SEMMILYEN job-sor nem íródik (a kapu a felvétel ELŐTT áll)."""
    key = _key(pub_db)
    big = dict(VALID_SPEC, n=500)
    res = asyncio.run(_call(pub_mcp, "delphoi_submit_brief",
                            {"api_key": key, "spec_json": json.dumps(big)}))
    assert res["error"] == "insufficient_credits"
    assert res["cost"] > res["balance"]
    conn = pub_db()
    assert conn.execute("SELECT COUNT(*) FROM delphoi_jobs").fetchone()[0] == 0
    bal = conn.execute("SELECT balance FROM delphoi_credits WHERE user_id='user:7'"
                       ).fetchone()[0]
    conn.close()
    assert bal == delphoi.WELCOME_CREDITS   # egyenleg nem mozgott


def test_submit_flow_job_status_ownership(pub_db, pub_mcp, monkeypatch):
    processed = {}

    async def fake_process(deps, job_id, **kw):
        processed["job_id"] = job_id
        conn = deps["get_db"]()
        conn.execute("UPDATE delphoi_jobs SET status='done', result_json=? WHERE id=?",
                     (json.dumps({"overall_score": 3.4,
                                  "disclaimer": "relatív jel"}), job_id))
        conn.commit()
        conn.close()
        return {"ok": True}

    monkeypatch.setattr(delphoi, "process_job", fake_process)
    key = _key(pub_db)

    async def _flow():
        res = await _call(pub_mcp, "delphoi_submit_brief",
                          {"api_key": key, "spec_json": json.dumps(VALID_SPEC)})
        assert res["ok"] is True
        assert res["scope_verdict"]["verdict"] == "zöld"
        assert res["credits_charged"] >= 1 and res["credit_estimate"] >= 1
        assert res["brief_id"].startswith("brf-")
        await asyncio.sleep(0.05)          # háttér-task lefut
        st = await _call(pub_mcp, "delphoi_job_status",
                         {"api_key": key, "job_id": res["job_id"]})
        assert st["ok"] and st["status"] == "done"
        assert st["result"]["overall_score"] == 3.4
        # MÁS user kulcsa: not_found (tulaj-vasszabály)
        other = _key(pub_db, ext="user:999", label="idegen")
        st2 = await _call(pub_mcp, "delphoi_job_status",
                          {"api_key": other, "job_id": res["job_id"]})
        assert st2 == {"ok": False, "error": "not_found"}
        return res["job_id"]

    job_id = asyncio.run(_flow())
    assert processed["job_id"] == job_id


def test_submit_red_scope_warns_but_does_not_block(pub_db, pub_mcp, monkeypatch):
    async def fake_process(deps, job_id, **kw):
        return {"ok": True}
    monkeypatch.setattr(delphoi, "process_job", fake_process)
    key = _key(pub_db)
    red = dict(VALID_SPEC, goal="Szektorális GDP-bontás iparáganként",
               stimuli=["Ágazati GDP-előrejelzés a feldolgozóiparra."])
    res = asyncio.run(_call(pub_mcp, "delphoi_submit_brief",
                            {"api_key": key, "spec_json": json.dumps(red)}))
    assert res["ok"] is True                       # piros NEM tilt
    assert res["scope_verdict"]["verdict"] == "piros"
    assert res["scope_verdict"]["warning"]


def test_no_raw_persona_data_in_status(pub_db, pub_mcp, monkeypatch):
    """A publikus felület aggregátumot ad — a delphoi_panel_responses nyers
    mondatai a job_status-ban nem jelenhetnek meg."""
    async def fake_process(deps, job_id, **kw):
        conn = deps["get_db"]()
        conn.execute(
            "INSERT INTO delphoi_panel_responses (job_id, persona_idx, segment, "
            "raw_reaction, created_at) VALUES (?, 0, 's', 'NYERS-PERSONA-MONDAT-XYZ', 't')",
            (job_id,))
        conn.execute("UPDATE delphoi_jobs SET status='done', result_json=? WHERE id=?",
                     (json.dumps({"overall_score": 3.0}), job_id))
        conn.commit()
        conn.close()
        return {"ok": True}

    monkeypatch.setattr(delphoi, "process_job", fake_process)
    key = _key(pub_db)

    async def _flow():
        res = await _call(pub_mcp, "delphoi_submit_brief",
                          {"api_key": key, "spec_json": json.dumps(VALID_SPEC)})
        await asyncio.sleep(0.05)
        return await _call(pub_mcp, "delphoi_job_status",
                           {"api_key": key, "job_id": res["job_id"]})

    st = asyncio.run(_flow())
    assert st["ok"] is True
    assert "NYERS-PERSONA-MONDAT-XYZ" not in json.dumps(st)


# ── RIPORT: lejáró token ───────────────────────────────────────────────────

def test_report_token_roundtrip_expiry_tamper(monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_SECRET", "titok")
    monkeypatch.setenv("DELPHOI_SAAS_REPORT_TTL", "3600")
    tok = dpub.make_report_token("dlph-abc", "pdf", now=1000.0)
    assert dpub.verify_report_token(tok, now=2000.0) == ("dlph-abc", "pdf")
    assert dpub.verify_report_token(tok, now=1000.0 + 3601) is None   # lejárt
    assert dpub.verify_report_token(tok + "x", now=2000.0) is None    # rontott sig
    other = tok.replace("pdf", "csv")
    assert dpub.verify_report_token(other, now=2000.0) is None        # formátum-csere


def test_get_report_not_ready_and_url(pub_db, pub_mcp, monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_SECRET", "titok")
    monkeypatch.setenv("DELPHOI_SAAS_PUBLIC_URL", "https://bridge.example")

    async def fake_process(deps, job_id, **kw):
        return {"ok": True}
    monkeypatch.setattr(delphoi, "process_job", fake_process)
    key = _key(pub_db)

    async def _flow():
        res = await _call(pub_mcp, "delphoi_submit_brief",
                          {"api_key": key, "spec_json": json.dumps(VALID_SPEC)})
        # queued → not_ready
        nr = await _call(pub_mcp, "delphoi_get_report",
                         {"api_key": key, "job_id": res["job_id"], "format": "pdf"})
        assert nr == {"ok": False, "error": "not_ready", "status": "queued"}
        conn = pub_db()
        conn.execute("UPDATE delphoi_jobs SET status='done', result_json='{}' WHERE id=?",
                     (res["job_id"],))
        conn.commit()
        conn.close()
        rep = await _call(pub_mcp, "delphoi_get_report",
                          {"api_key": key, "job_id": res["job_id"], "format": "pdf"})
        assert rep["ok"] is True
        assert rep["download_url"].startswith(
            "https://bridge.example/api/delphoi/saas/report/")
        token = rep["download_url"].rsplit("/", 1)[1]
        assert dpub.verify_report_token(token) == (res["job_id"], "pdf")
        bad = await _call(pub_mcp, "delphoi_get_report",
                          {"api_key": key, "job_id": res["job_id"], "format": "docx"})
        assert bad["error"].startswith("bad_format")

    asyncio.run(_flow())
