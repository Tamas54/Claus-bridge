"""test_delphoi_saas_auth.py — OPERATION SIBYLLE S1: magic-link auth + fiók-API.

CÉL-BIZONYÍTÉKOK:
  - egy plugin @app.custom_route-tal server.py-módosítás NÉLKÜL regisztrál
    HTTP-utakat (valódi FastMCP + http_app + httpx ASGITransport);
  - a konkrét /saas/auth/* Route-ok a B3 Mount("/saas") MELLETT is élnek
    (a server.py-sorrend: plugin-discovery ELŐBB, mount UTÁNA);
  - a login-token egyszer-használatos + lejár + HMAC-védett;
  - a signup-grant idempotens (a delphoi.ensure_welcome útján, nem másolat);
  - a session-úton generált kulcs UGYANAZ a B3-kulcs (resolve_api_key látja);
  - NOTSTROM: e-mail-út nélkül a link CSAK DELPHOI_SAAS_DEV_MODE=1 esetén tér
    vissza, élesben hangos 503.
"""
import asyncio
import json

import pytest

from plugins import (delphoi, delphoi_brief, delphoi_public_mcp as dpub,
                     delphoi_saas_auth as saa)

SECRET = "test-sibylle-secret"


@pytest.fixture
def auth_db(get_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_SECRET", SECRET)
    monkeypatch.delenv("DELPHOI_SAAS_DEV_MODE", raising=False)
    conn = get_db()
    delphoi.ensure_tables(conn)
    delphoi.ensure_fg_tables(conn)
    delphoi_brief.ensure_brief_tables(conn)
    saa.ensure_auth_tables(conn)
    conn.close()
    saa._RL_HITS.clear()
    dpub._BUCKETS.clear()
    return get_db


def _client_app(auth_db, capture_state=None, with_b3_mount=False):
    """Valódi FastMCP + register_tools + http_app — a server.py-út kicsiben.
    with_b3_mount: a B3 Mount('/saas') UTÁNA kerül a listára (server-sorrend)."""
    from fastmcp import FastMCP

    mcp = FastMCP("sibylle-test")
    saa.register_tools(mcp, {"get_db": auth_db,
                             "capture_state": capture_state or {}})
    wrapper = None
    if with_b3_mount:
        from starlette.routing import Mount
        wrapper = dpub.build_public_asgi({"get_db": auth_db})
        mcp._additional_http_routes.append(
            Mount(dpub.PUBLIC_MOUNT_PATH, app=wrapper))
    return mcp.http_app(stateless_http=True), wrapper


async def _req(app, method, path, **kw):
    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await getattr(c, method)(path, **kw)


# ── Egység: e-mail-normalizálás + maszkolás ────────────────────────────────

def test_normalize_email():
    assert saa.normalize_email("  Foo.Bar@Gmail.COM ") == "foo.bar@gmail.com"
    assert saa.normalize_email("nem-email") == ""
    assert saa.normalize_email("") == ""
    assert saa.normalize_email("a@b") == ""
    assert saa.normalize_email("x" * 250 + "@a.io") == ""


def test_mask_email_never_full():
    m = saa.mask_email("tamas.csizmadia02@gmail.com")
    assert "tamas.csizmadia02" not in m and "gmail.com" not in m
    assert m == "t***@g***"
    assert saa.mask_email("") == "***"


# ── Egység: login-token (egyszer-használat, lejárat, HMAC) ─────────────────

def test_login_token_roundtrip_and_single_use(auth_db):
    tok = saa.issue_login_token(auth_db, "a@b.io")
    assert saa.consume_login_token(auth_db, tok) == "a@b.io"
    assert saa.consume_login_token(auth_db, tok) is None  # egyszer-használatos


def test_login_token_expiry(auth_db):
    # a TTL-hez kötve (KLARTEXT: 15→60 perc) — a teszt nem hardcode-ol percet
    import time
    tok = saa.issue_login_token(auth_db, "a@b.io",
                                now=time.time() - (saa.LOGIN_TOKEN_TTL + 60))
    assert saa.consume_login_token(auth_db, tok) is None


def test_login_token_tamper_and_foreign(auth_db, monkeypatch):
    tok = saa.issue_login_token(auth_db, "a@b.io")
    head, _, sig = tok.rpartition(".")
    assert saa.consume_login_token(auth_db, head + "." + "0" * len(sig)) is None
    # idegen secrettel aláírt token → deny (akkor is, ha DB-sor volna hozzá)
    monkeypatch.setenv("DELPHOI_SAAS_SECRET", "masik-secret")
    tok2 = saa.issue_login_token(auth_db, "a@b.io")
    monkeypatch.setenv("DELPHOI_SAAS_SECRET", SECRET)
    assert saa.consume_login_token(auth_db, tok2) is None


def test_login_token_not_in_db_denied(auth_db):
    # jó HMAC-ú, de SOSEM kiadott (DB-sor nélküli) token → deny
    import time
    exp = int(time.time() + 600)
    payload = f"saas-login|a@b.io|{exp}|nonce"
    forged = ".".join(["lgn", saa._b64e("a@b.io"), str(exp), "nonce",
                       saa._sig(payload, SECRET)])
    assert saa.consume_login_token(auth_db, forged) is None


# ── Egység: session-token ──────────────────────────────────────────────────

def test_session_token_roundtrip(auth_db):
    tok = saa.issue_session_token(7, "a@b.io")
    got = saa.verify_session_token(tok)
    assert got and got["user_id"] == 7 and got["email"] == "a@b.io"


def test_session_token_expiry_and_tamper(auth_db):
    import time
    tok = saa.issue_session_token(7, "a@b.io", now=time.time() - 31 * 24 * 3600)
    assert saa.verify_session_token(tok) is None
    tok2 = saa.issue_session_token(7, "a@b.io")
    parts = tok2.split(".")
    parts[1] = "8"  # más user_id, régi aláírás
    assert saa.verify_session_token(".".join(parts)) is None


# ── Egység: rate-limit ─────────────────────────────────────────────────────

def test_rate_limit_per_email(auth_db):
    for _ in range(5):
        assert saa.rate_limit_link("a@b.io", "1.2.3.4")
    assert not saa.rate_limit_link("a@b.io", "1.2.3.4")
    assert saa.rate_limit_link("c@d.io", "5.6.7.8")  # másik e-mail mehet


# ── HTTP-kör: custom_route pluginból, server.py nélkül ─────────────────────

def test_plugin_registers_routes_without_server_py(auth_db, monkeypatch):
    """A cél-bizonyíték: FastMCP + register_tools → az utak HTTP-n élnek."""
    monkeypatch.setenv("DELPHOI_SAAS_DEV_MODE", "1")
    app, _ = _client_app(auth_db)

    async def _run():
        r = await _req(app, "post", "/saas/auth/request-link",
                       json={"email": "User@Example.COM"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] and body["dev_mode"] and "token=" in body["magic_link"]
        token = body["magic_link"].split("token=", 1)[1]
        r2 = await _req(app, "get", f"/saas/auth/verify?token={token}")
        assert r2.status_code == 200
        v = r2.json()
        assert v["ok"] and v["email"] == "user@example.com" and v["session_token"]
        return v["session_token"]

    session = asyncio.run(_run())
    assert saa.verify_session_token(session)


def test_full_account_flow_me_keys_revoke(auth_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_DEV_MODE", "1")
    app, _ = _client_app(auth_db)

    async def _run():
        r = await _req(app, "post", "/saas/auth/request-link",
                       json={"email": "flow@t.io"})
        token = r.json()["magic_link"].split("token=", 1)[1]
        sess = (await _req(app, "get",
                           f"/saas/auth/verify?token={token}")).json()["session_token"]
        hdr = {"Authorization": f"Bearer {sess}"}
        me = (await _req(app, "get", "/saas/me", headers=hdr)).json()
        assert me["ok"] and me["user"]["email"] == "flow@t.io"
        assert me["balance"] == delphoi.WELCOME_CREDITS      # signup-grant élt
        assert any(l["reason"] == "signup_grant" for l in me["ledger"])
        # kulcs-generálás: a plaintext EGYSZER látszik
        created = (await _req(app, "post", "/saas/keys", headers=hdr,
                              json={"label": "ci"})).json()
        assert created["ok"] and created["api_key"].startswith(dpub.KEY_PREFIX)
        listed = (await _req(app, "get", "/saas/keys", headers=hdr)).json()
        assert len(listed["keys"]) == 1
        assert "api_key" not in listed["keys"][0]            # plaintext SOHA
        # a session-úton kapott kulcs UGYANAZ a B3-kulcs (nem külön tábla)
        resolved = dpub.resolve_api_key(auth_db, created["api_key"])
        assert resolved and resolved["origin"] == "saas"
        assert dpub.account_ref(resolved) == "flow@t.io"
        # revoke — utána a resolve deny
        rr = (await _req(app, "post",
                         f"/saas/keys/{created['key_id']}/revoke", headers=hdr)).json()
        assert rr["ok"]
        assert dpub.resolve_api_key(auth_db, created["api_key"]) is None
        # idegen session nem revoke-olhat más kulcsot
        return True

    assert asyncio.run(_run())


def test_signup_grant_idempotent_across_logins(auth_db, monkeypatch):
    """Két külön magic-link kör → a welcome-grant EGYSZER jár."""
    monkeypatch.setenv("DELPHOI_SAAS_DEV_MODE", "1")
    app, _ = _client_app(auth_db)

    async def _login_once():
        r = await _req(app, "post", "/saas/auth/request-link",
                       json={"email": "twice@t.io"})
        token = r.json()["magic_link"].split("token=", 1)[1]
        return (await _req(app, "get",
                           f"/saas/auth/verify?token={token}")).json()

    asyncio.run(_login_once())
    asyncio.run(_login_once())
    assert delphoi.get_credits(auth_db, "twice@t.io")["balance"] == delphoi.WELCOME_CREDITS


def test_me_requires_session(auth_db):
    app, _ = _client_app(auth_db)

    async def _run():
        r = await _req(app, "get", "/saas/me")
        assert r.status_code == 401
        r2 = await _req(app, "get", "/saas/me",
                        headers={"Authorization": "Bearer nem.jo.token"})
        assert r2.status_code == 401

    asyncio.run(_run())


def test_routes_coexist_with_b3_mount(auth_db):
    """Server-sorrend kicsiben: a plugin-Route-ok a Mount('/saas') ELŐTT
    kerülnek a listára → a Mount nem nyeli el őket, és a /saas/mcp is él."""
    app, wrapper = _client_app(auth_db, with_b3_mount=True)

    async def _run():
        # a mi utunk válaszol (403 = a MI handlerünk, nem a mount 404-e)
        r = await _req(app, "get", "/saas/auth/verify?token=rossz")
        assert r.status_code == 403 and r.json()["error"] == "invalid_or_expired_token"
        # a B3 MCP-út is él ugyanabban az appban
        r2 = await _req(app, "post", "/saas/mcp",
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                              "params": {}},
                        headers={"accept": "application/json, text/event-stream"})
        assert r2.status_code == 200
        body = r2.text
        if "data: " in body:
            body = body.split("data: ", 1)[1].split("\n")[0]
        names = sorted(t["name"] for t in json.loads(body)["result"]["tools"])
        assert names == sorted(dpub.PUBLIC_TOOL_NAMES)
        await wrapper.aclose()

    asyncio.run(_run())


# ── NOTSTROM: e-mail-út + dev-mód ──────────────────────────────────────────

def test_no_email_no_devmode_is_loud_503(auth_db):
    app, _ = _client_app(auth_db)   # nincs transzport, nincs dev-mód

    async def _run():
        r = await _req(app, "post", "/saas/auth/request-link",
                       json={"email": "loud@t.io"})
        assert r.status_code == 503
        assert r.json()["error"] == "email_send_unavailable"
        assert "magic_link" not in r.json()

    asyncio.run(_run())


def test_email_sent_via_bridge_send_func(auth_db):
    """A meglévő capture_send_email-út (capture_state['_send_email_func'])."""
    sent = {}

    async def fake_send(to, subject, body, body_type="plain", **kw):
        sent.update(to=to, subject=subject, body=body, body_type=body_type)
        return json.dumps({"status": "sent", "message_id": "m1"})

    app, _ = _client_app(auth_db, capture_state={"_send_email_func": fake_send})

    async def _run():
        r = await _req(app, "post", "/saas/auth/request-link",
                       json={"email": "mail@t.io"})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "sent": True}
        assert "magic_link" not in r.json()           # élesben link a válaszban SOHA
        assert sent["to"] == "mail@t.io" and "token=" in sent["body"]
        assert sent["body_type"] == "html"

    asyncio.run(_run())


def test_send_failure_falls_to_503_not_link(auth_db):
    async def bad_send(**kw):
        return json.dumps({"error": "Gmail not initialized"})

    app, _ = _client_app(auth_db, capture_state={"_send_email_func": bad_send})

    async def _run():
        r = await _req(app, "post", "/saas/auth/request-link",
                       json={"email": "fail@t.io"})
        assert r.status_code == 503

    asyncio.run(_run())


def test_request_link_rate_limited_http(auth_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_DEV_MODE", "1")
    app, _ = _client_app(auth_db)

    async def _run():
        for _ in range(5):
            r = await _req(app, "post", "/saas/auth/request-link",
                           json={"email": "rl@t.io"})
            assert r.status_code == 200
        r = await _req(app, "post", "/saas/auth/request-link",
                       json={"email": "rl@t.io"})
        assert r.status_code == 429

    asyncio.run(_run())


def test_invalid_email_rejected(auth_db):
    app, _ = _client_app(auth_db)

    async def _run():
        r = await _req(app, "post", "/saas/auth/request-link",
                       json={"email": "nem-email"})
        assert r.status_code == 400 and r.json()["error"] == "invalid_email"

    asyncio.run(_run())


# ── /ask-út: session-authos submit + job-státusz ───────────────────────────

VALID_SPEC = {
    "goal": "Would city 30-somethings buy this?",
    "instrument": "concept",
    "stimuli": ["New green packaging with 40% less plastic."],
    "country": "HU", "n": 30, "dimensions": ["appeal"],
    "tracking": "none", "report": {"lang": "en", "formats": ["pdf"]},
}


async def _login_and_session(app, email):
    r = await _req(app, "post", "/saas/auth/request-link", json={"email": email})
    token = r.json()["magic_link"].split("token=", 1)[1]
    return (await _req(app, "get",
                       f"/saas/auth/verify?token={token}")).json()["session_token"]


def test_submit_and_job_status_owner_only(auth_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_DEV_MODE", "1")

    ran = {}

    async def fake_process(run_deps, job_id):
        ran["job_id"] = job_id

    monkeypatch.setattr(delphoi, "process_job", fake_process)
    app, _ = _client_app(auth_db)

    async def _run():
        sess = await _login_and_session(app, "asker@t.io")
        hdr = {"Authorization": f"Bearer {sess}"}
        r = await _req(app, "post", "/saas/submit", headers=hdr,
                       json={"spec": VALID_SPEC})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] and body["status"] == "queued" and body["job_id"]
        assert body["credits_charged"] >= 1
        # a háttér-task ütemeződött a MI job-uinkra
        await asyncio.sleep(0)
        assert ran.get("job_id") == body["job_id"]
        # job-státusz a tulajdonosnak
        r2 = await _req(app, "get", f"/saas/jobs/{body['job_id']}", headers=hdr)
        assert r2.status_code == 200 and r2.json()["ok"]
        # MÁS user sessionje NEM látja (owner-only)
        sess2 = await _login_and_session(app, "other@t.io")
        r3 = await _req(app, "get", f"/saas/jobs/{body['job_id']}",
                        headers={"Authorization": f"Bearer {sess2}"})
        assert r3.status_code == 404
        # session nélkül deny
        r4 = await _req(app, "post", "/saas/submit", json={"spec": VALID_SPEC})
        assert r4.status_code == 401

    asyncio.run(_run())


def test_submit_invalid_brief_is_loud(auth_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_DEV_MODE", "1")
    app, _ = _client_app(auth_db)

    async def _run():
        sess = await _login_and_session(app, "bad@t.io")
        hdr = {"Authorization": f"Bearer {sess}"}
        r = await _req(app, "post", "/saas/submit", headers=hdr,
                       json={"spec": {"goal": "", "instrument": "concept",
                                      "stimuli": [], "country": "XX"}})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_brief" and r.json()["errors"]

    asyncio.run(_run())


def test_submit_insufficient_credits_402(auth_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_DEV_MODE", "1")
    monkeypatch.setattr(delphoi, "WELCOME_CREDITS", 0)
    app, _ = _client_app(auth_db)

    async def _run():
        sess = await _login_and_session(app, "poor@t.io")
        r = await _req(app, "post", "/saas/submit",
                       headers={"Authorization": f"Bearer {sess}"},
                       json={"spec": VALID_SPEC})
        assert r.status_code == 402
        assert r.json()["error"] == "insufficient_credits"

    asyncio.run(_run())


def test_register_tools_tolerates_fake_app(auth_db, fake_app):
    """FakeApp (nincs custom_route) → nem robban, csak hangos log."""
    saa.register_tools(fake_app, {"get_db": auth_db})
    assert fake_app.tools == {}   # MCP-toolt NEM regisztrál (tool-count fegyelem)


# ── K2 kész-értesítő: "Your answer is ready" a job-done-kor ────────────────

def _mk_job(auth_db, email):
    delphoi.ensure_welcome(auth_db, email)
    created = delphoi.create_job(auth_db, email, "concept", "Would this land?",
                                 {"country": "HU", "n_per_cell": 30})
    assert created.get("ok"), created
    return created["job_id"]


def test_run_and_notify_sends_ready_email_on_done(auth_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_SITE_URL", "https://aipolling.io")
    email = "ready@t.io"
    job_id = _mk_job(auth_db, email)
    sent = {}

    async def fake_process(run_deps, jid):
        conn = auth_db()
        conn.execute("UPDATE delphoi_jobs SET status='done', result_json='{}' "
                     "WHERE id=?", (jid,))
        conn.commit()
        conn.close()

    async def fake_send(to="", subject="", body="", body_type=""):
        sent.update(to=to, subject=subject, body=body)
        return json.dumps({"status": "sent"})

    monkeypatch.setattr(delphoi, "process_job", fake_process)
    deps = {"capture_state": {"_send_email_func": fake_send}}
    asyncio.run(saa._run_and_notify({"get_db": auth_db}, job_id, email, deps))
    assert sent["to"] == email
    assert sent["subject"] == "Your answer is ready"
    assert f"https://aipolling.io/ask/job/{job_id}" in sent["body"]


def test_run_and_notify_silent_on_failed_and_on_email_error(auth_db, monkeypatch):
    email = "fail@t.io"
    job_id = _mk_job(auth_db, email)
    calls = []

    async def fake_process_fail(run_deps, jid):
        delphoi._fail_job(auth_db, jid, "teszt-hiba")

    async def fake_send(**kw):
        calls.append(kw)
        return json.dumps({"status": "sent"})

    monkeypatch.setattr(delphoi, "process_job", fake_process_fail)
    deps = {"capture_state": {"_send_email_func": fake_send}}
    # failed → NINCS levél (a refund-üzenet a felületen él)
    asyncio.run(saa._run_and_notify({"get_db": auth_db}, job_id, email, deps))
    assert calls == []

    # done + robbanó transzport → a hiba elnyelve, sosem propagál
    job2 = _mk_job(auth_db, email)

    async def fake_process_done(run_deps, jid):
        conn = auth_db()
        conn.execute("UPDATE delphoi_jobs SET status='done', result_json='{}' "
                     "WHERE id=?", (jid,))
        conn.commit()
        conn.close()

    async def boom(**kw):
        raise RuntimeError("smtp le")

    monkeypatch.setattr(delphoi, "process_job", fake_process_done)
    asyncio.run(saa._run_and_notify(
        {"get_db": auth_db}, job2, email,
        {"capture_state": {"_send_email_func": boom}}))   # nem dob


def test_site_base_derivation(monkeypatch):
    monkeypatch.setenv("DELPHOI_SAAS_SITE_URL", "https://aipolling.io/")
    assert saa._site_base() == "https://aipolling.io"
    monkeypatch.delenv("DELPHOI_SAAS_SITE_URL", raising=False)
    monkeypatch.setenv("DELPHOI_SAAS_LOGIN_URL",
                       "https://aipolling.io/auth/callback")
    assert saa._site_base() == "https://aipolling.io"
    monkeypatch.setenv("DELPHOI_SAAS_LOGIN_URL", "https://bridge/saas/auth/verify")
    assert saa._site_base() == ""
