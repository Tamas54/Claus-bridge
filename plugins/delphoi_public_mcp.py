"""plugins/delphoi_public_mcp.py — PYTHIA B3: publikus SaaS MCP-kapu (Minds-paritás).

KÜLÖN, IZOLÁLT FastMCP-app a saját útvonalán (/saas/mcp) — a belső Bridge-toolok
(memory, gmail, taskok, MINDEN más) ezen a felületen NEM LÉTEZNEK. A publikus
felület KIZÁRÓLAG 4 toolt ad:

  - delphoi_submit_brief(api_key, spec_json) → job_id + kredit-becslés + scope-verdikt
  - delphoi_job_status(api_key, job_id)
  - delphoi_get_report(api_key, job_id, format) → lejáró tokenes letöltő-URL
  - delphoi_credits(api_key)

Mind a brief-sémát eszi (plugins.delphoi_brief — EGY igazságforrás), mind
AGGREGÁTUMOT ad; nyers persona-adat SEMMILYEN úton nem megy ki (a get_job/
riport-mű eleve aggregátum-only).

AUTH (MOD2/A2): delphoi_api_keys tábla — key_hash (SHA256), user_id →
delphoi_users.user_id, label, created_at, revoked_at. A kulcs PLAINTEXT alakja
EGYSZER látszik (generáláskor); a DB csak hash-t tárol. Kulcsonkénti token-bucket
rate-limit (env-konfig) + kreditellenőrzés a job-felvétel ELŐTT; a
refund-vasszabály (delphoi._fail_job / watchdog) változatlanul érvényes.

MOUNT (server.py, PYTHIA B3): a FastMCP custom-route listájára (Starlette
BaseRoute-ok) egy Mount("/saas", app=...) kerül — a második FastMCP http_app-ja
saját lifespan-nel él, amelyet a LazyLifespanASGI az ELSŐ kérésnél indít el
(a Starlette a mountolt al-app lifespan-jét nem futtatja; a wrapper pótolja).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone

logger = logging.getLogger("plugins.delphoi_public_mcp")

__plugin_meta__ = {
    "name": "delphoi_public_mcp",
    "version": "1.0.0",
    "description": "DELPHOI publikus SaaS MCP-kapu (B3) — 4 tool, api-kulcs auth, izolalt mount",
}

_DEPS: dict | None = None

# A publikus felület TELJES tool-készlete — az izolációs teszt erre esküszik.
PUBLIC_TOOL_NAMES = ("delphoi_submit_brief", "delphoi_job_status",
                     "delphoi_get_report", "delphoi_credits")

PUBLIC_MOUNT_PATH = "/saas"     # a server.py Mount-ja
PUBLIC_MCP_PATH = "/mcp"        # az al-app MCP-útja → publikusan /saas/mcp

REPORT_FORMATS = ("html", "pdf", "json", "csv")

# ---------------------------------------------------------------------------
# SÉMA — delphoi_api_keys (a migrate_pythia_b3.py ugyanEZT hozza létre éles
# DB-n; itt az app-indulás/tesztek idempotens útja, delphoi-minta). A
# delphoi_users DDL igazságforrása a migrate_pythia_p1 (A2) — importtal jön.
# ---------------------------------------------------------------------------
API_KEYS_INIT_SQL = """
CREATE TABLE IF NOT EXISTS delphoi_api_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash   TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL DEFAULT '',
    user_id    INTEGER NOT NULL,
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_delphoi_api_keys_user
    ON delphoi_api_keys(user_id);
"""


def _users_sql() -> str:
    """A delphoi_users DDL EGY igazságforrásból (migrate_pythia_p1 — A2)."""
    from migrate_pythia_p1 import _USERS_SQL
    return _USERS_SQL


def ensure_key_tables(conn) -> None:
    """Idempotens séma — delphoi_users (A2) + delphoi_api_keys (B3)."""
    conn.executescript(_users_sql())
    conn.executescript(API_KEYS_INIT_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# IDENTITÁS (MOD2/A2) — a kulcs a delphoi_users.user_id-hoz kötődik; a kredit/
# job-könyvelés kulcsa (account_ref) az eredet-oldali external_id marad
# (Echolot: 'user:<id>'), így az Echolot-UI és a SaaS-kulcs UGYANAZT az
# egyenleget látja. SaaS-origin external_id nélkül: 'saas:<user_id>'.
# ---------------------------------------------------------------------------
VALID_ORIGINS = ("echolot", "saas")


def get_or_create_user(get_db, origin: str, external_id: str) -> int:
    if origin not in VALID_ORIGINS:
        raise ValueError(f"ismeretlen origin: {origin!r} — engedélyezett: {VALID_ORIGINS}")
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO delphoi_users (origin, external_id, created_at) "
            "VALUES (?, ?, ?)",
            (origin, str(external_id), datetime.now(timezone.utc).isoformat()))
        conn.commit()
        row = conn.execute(
            "SELECT user_id FROM delphoi_users WHERE origin=? AND external_id=?",
            (origin, str(external_id))).fetchone()
        return int(row["user_id"])
    finally:
        conn.close()


def account_ref(user_row: dict) -> str:
    """A kredit/job-táblák TEXT user_id-je egy delphoi_users-sorhoz."""
    ext = user_row.get("external_id")
    return str(ext) if ext else f"saas:{user_row['user_id']}"


# ---------------------------------------------------------------------------
# API-KULCSOK — a kulcs EGYSZER látszik (generáláskor); a DB csak SHA256-ot tárol.
# ---------------------------------------------------------------------------
KEY_PREFIX = "dlph_sk_"


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(str(api_key).encode("utf-8")).hexdigest()


def generate_api_key(get_db, origin: str, external_id: str, label: str = "") -> dict:
    """Új kulcs a userhez (get-or-create a delphoi_users-ben). A visszaadott
    api_key PLAINTEXT — ez az EGYETLEN pillanat, amikor látszik."""
    uid = get_or_create_user(get_db, origin, external_id)
    api_key = KEY_PREFIX + secrets.token_urlsafe(24)
    prefix = api_key[: len(KEY_PREFIX) + 6] + "…"
    ts = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO delphoi_api_keys (key_hash, key_prefix, user_id, label, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_hash_key(api_key), prefix, uid, str(label or "")[:80], ts))
        conn.commit()
        key_id = cur.lastrowid
    finally:
        conn.close()
    return {"ok": True, "api_key": api_key, "key_id": key_id,
            "key_prefix": prefix, "user_id": uid, "label": str(label or "")[:80],
            "created_at": ts}


def list_api_keys(get_db, origin: str, external_id: str) -> list:
    """Kulcs-lista a userhez — plaintext SOSEM, csak prefix+meta."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT user_id FROM delphoi_users WHERE origin=? AND external_id=?",
            (origin, str(external_id))).fetchone()
        if not row:
            return []
        return [dict(r) for r in conn.execute(
            "SELECT id, key_prefix, label, created_at, revoked_at "
            "FROM delphoi_api_keys WHERE user_id=? ORDER BY id DESC",
            (int(row["user_id"]),)).fetchall()]
    finally:
        conn.close()


def revoke_api_key(get_db, origin: str, external_id: str, key_id: int) -> dict:
    """Revoke — CSAK a tulajdonos userének kulcsára (authz a user-mappingen)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT k.id FROM delphoi_api_keys k "
            "JOIN delphoi_users u ON u.user_id = k.user_id "
            "WHERE k.id=? AND u.origin=? AND u.external_id=? AND k.revoked_at IS NULL",
            (int(key_id), origin, str(external_id))).fetchone()
        if not row:
            return {"ok": False, "error": "not_found"}
        conn.execute("UPDATE delphoi_api_keys SET revoked_at=? WHERE id=?",
                     (datetime.now(timezone.utc).isoformat(), int(key_id)))
        conn.commit()
        return {"ok": True, "key_id": int(key_id)}
    finally:
        conn.close()


def resolve_api_key(get_db, api_key: str) -> dict | None:
    """Érvényes (nem revokált) kulcs → user-sor; minden más → None (deny)."""
    if not api_key or not str(api_key).startswith(KEY_PREFIX):
        return None
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT k.id AS key_id, k.key_hash, u.user_id, u.origin, u.external_id "
            "FROM delphoi_api_keys k JOIN delphoi_users u ON u.user_id = k.user_id "
            "WHERE k.key_hash=? AND k.revoked_at IS NULL",
            (_hash_key(api_key),)).fetchone()
    except Exception:  # noqa: BLE001 — tábla-hiány = zárt kapu
        row = None
    finally:
        conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# RATE-LIMIT — kulcsonkénti token-bucket (in-memory; env hívás-időben olvasva).
# ---------------------------------------------------------------------------
_BUCKETS: dict = {}


def _rate_conf() -> tuple[float, float]:
    """(kapacitás, feltöltés/sec). DELPHOI_SAAS_RATE_PER_MIN default 30."""
    try:
        per_min = max(1, int(os.environ.get("DELPHOI_SAAS_RATE_PER_MIN", "30")))
    except ValueError:
        per_min = 30
    try:
        burst = max(1, int(os.environ.get("DELPHOI_SAAS_BURST", str(per_min))))
    except ValueError:
        burst = per_min
    return float(burst), per_min / 60.0


def rate_limit_allow(key_hash: str, now: float | None = None) -> bool:
    """True = mehet; False = 429-jellegű deny. Egyszerű token-bucket."""
    cap, refill = _rate_conf()
    t = now if now is not None else time.monotonic()
    tokens, last = _BUCKETS.get(key_hash, (cap, t))
    tokens = min(cap, tokens + (t - last) * refill)
    if tokens < 1.0:
        _BUCKETS[key_hash] = (tokens, t)
        return False
    _BUCKETS[key_hash] = (tokens - 1.0, t)
    return True


# ---------------------------------------------------------------------------
# LEJÁRÓ RIPORT-TOKEN — HMAC-aláírt letöltő-URL (a token maga a felhatalmazás,
# rövid életű; secret: DELPHOI_SAAS_SECRET, fallback DELPHOI_BRIDGE_KEY).
# ---------------------------------------------------------------------------
def _token_secret() -> str:
    return (os.environ.get("DELPHOI_SAAS_SECRET", "")
            or os.environ.get("DELPHOI_BRIDGE_KEY", ""))


def report_token_ttl() -> int:
    try:
        return max(60, int(os.environ.get("DELPHOI_SAAS_REPORT_TTL", "3600")))
    except ValueError:
        return 3600


def make_report_token(job_id: str, fmt: str, secret: str | None = None,
                      now: float | None = None) -> str:
    secret = secret if secret is not None else _token_secret()
    if not secret:
        raise RuntimeError("nincs riport-token secret (DELPHOI_SAAS_SECRET | DELPHOI_BRIDGE_KEY)")
    exp = int((now if now is not None else time.time()) + report_token_ttl())
    sig = hmac.new(secret.encode(), f"{job_id}|{fmt}|{exp}".encode(),
                   hashlib.sha256).hexdigest()[:32]
    return f"{job_id}.{fmt}.{exp}.{sig}"


def verify_report_token(token: str, secret: str | None = None,
                        now: float | None = None) -> tuple[str, str] | None:
    """Érvényes, nem lejárt token → (job_id, fmt); különben None."""
    secret = secret if secret is not None else _token_secret()
    if not secret:
        return None
    parts = (token or "").rsplit(".", 3)
    if len(parts) != 4:
        return None
    job_id, fmt, exp_s, sig = parts
    try:
        exp = int(exp_s)
    except ValueError:
        return None
    if (now if now is not None else time.time()) > exp:
        return None
    want = hmac.new(secret.encode(), f"{job_id}|{fmt}|{exp}".encode(),
                    hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(want, sig):
        return None
    return job_id, fmt


def _public_base_url() -> str:
    """A letöltő-URL bázisa — a Bridge PUBLIKUS originje (env); üresen relatív út."""
    return (os.environ.get("DELPHOI_SAAS_PUBLIC_URL") or "").rstrip("/")


# ---------------------------------------------------------------------------
# A 4 PUBLIKUS TOOL — közös auth-kapu + a meglévő motor-utak IMPORTTAL.
# ---------------------------------------------------------------------------
def _deny(reason: str = "auth") -> str:
    return json.dumps({"ok": False, "error": reason}, ensure_ascii=False)


def _auth(get_db, api_key: str) -> tuple[dict | None, str | None]:
    """(user-sor, None) érvényes kulcsra; (None, deny-JSON) minden másra."""
    user = resolve_api_key(get_db, api_key)
    if user is None:
        return None, _deny("invalid_api_key")
    if not rate_limit_allow(user["key_hash"]):
        return None, _deny("rate_limited")
    return user, None


def build_public_mcp(deps: dict):
    """A KÜLÖN, izolált publikus FastMCP-app — KIZÁRÓLAG a 4 delphoi-toollal.
    A deps-ből csak get_db + siliconflow_* kell (a process_job háttér-útjához)."""
    from fastmcp import FastMCP

    get_db = deps["get_db"]
    pub = FastMCP("DELPHOI SaaS")

    conn = get_db()
    try:
        ensure_key_tables(conn)
    finally:
        conn.close()

    @pub.tool()
    async def delphoi_submit_brief(api_key: str, spec_json: str) -> str:
        """Submit a measurement brief to the DELPHOI synthetic focus-group engine.
        spec_json: JSON brief (goal, instrument, stimuli, country, n, dimensions,
        segments, custom_questions, tracking, report). Validates against the brief
        schema, runs a scope check, verifies credits BEFORE intake, then queues the
        job (atomic credit charge; failed runs are auto-refunded). Returns job_id +
        credit estimate + scope verdict. Output is always aggregate-level — raw
        panel responses are never exposed."""
        from plugins import delphoi, delphoi_brief, delphoi_scopegate
        user, deny = _auth(get_db, api_key)
        if deny:
            return deny
        try:
            spec = json.loads(spec_json)
            if not isinstance(spec, dict):
                raise ValueError("a brief-spec JSON-objektum kell legyen")
        except Exception as e:  # noqa: BLE001
            return _deny(f"bad_spec_json: {type(e).__name__}: {e}")
        errors = delphoi_brief.validate_brief(spec)
        if errors:
            return json.dumps({"ok": False, "error": "invalid_brief",
                               "errors": errors}, ensure_ascii=False)
        canonical = delphoi_brief.canonicalize_spec(spec)
        # Scope-verdikt heurisztikával (LLM-mentes út — a Hy3-ítész a
        # process_job-ban úgyis lefut; piros NEM tilt, csak figyelmeztet).
        stim_text = "\n".join([canonical["goal"], *canonical["stimuli"]])
        scope = await delphoi_scopegate.scope_verdict(stim_text, use_judge=False)
        kind, text, panel_spec, variants = delphoi_brief.brief_to_job_args(canonical)
        ref = account_ref(user)
        cost = delphoi.job_cost(panel_spec, variants)
        estimate = delphoi_brief.estimate_credits(
            canonical["n"], len(canonical["dimensions"]),
            len(canonical["custom_questions"]), max(1, len(canonical["stimuli"])))
        # KREDITELLENŐRZÉS a job-felvétel ELŐTT (a create_job levonása így is atomi).
        delphoi.ensure_welcome(get_db, ref)
        balance = delphoi.get_credits(get_db, ref)["balance"]
        if balance < cost:
            return json.dumps({"ok": False, "error": "insufficient_credits",
                               "cost": cost, "balance": balance,
                               "credit_estimate": estimate}, ensure_ascii=False)
        saved = delphoi_brief.save_brief(get_db, ref, canonical)
        brief_id = saved.get("brief_id") if saved.get("ok") else ""
        panel_spec["brief_id"] = brief_id
        created = delphoi.create_job(get_db, ref, kind, text, panel_spec, variants)
        if not created.get("ok"):
            return json.dumps(created, ensure_ascii=False)
        job_id = created["job_id"]
        run_deps = {k: deps.get(k) for k in (
            "get_db", "siliconflow_api_key", "siliconflow_base_url",
            "siliconflow_timeout", "siliconflow_models")}
        asyncio.create_task(delphoi.process_job(run_deps, job_id))
        return json.dumps({
            "ok": True, "job_id": job_id, "brief_id": brief_id,
            "status": "queued", "credits_charged": created["cost"],
            "credit_estimate": estimate, "balance": created["balance"],
            "scope_verdict": {"verdict": scope["verdict"],
                              "warning": scope.get("warning")},
        }, ensure_ascii=False)

    @pub.tool()
    async def delphoi_job_status(api_key: str, job_id: str) -> str:
        """Status of a submitted DELPHOI job (owner only). When done, returns the
        aggregated result (relative/ordinal signal — never absolute percentages,
        never raw panel responses). Failed runs are refunded automatically."""
        from plugins import delphoi
        user, deny = _auth(get_db, api_key)
        if deny:
            return deny
        return json.dumps(delphoi.get_job(get_db, job_id, account_ref(user)),
                          ensure_ascii=False)

    @pub.tool()
    async def delphoi_get_report(api_key: str, job_id: str, format: str = "pdf") -> str:
        """Download link for a finished job's report (owner only). format:
        html|pdf|json|csv. Returns a signed URL that expires (default 1 hour) —
        fetch it promptly or request a fresh link."""
        from plugins import delphoi
        user, deny = _auth(get_db, api_key)
        if deny:
            return deny
        fmt = str(format or "pdf").lower()
        if fmt not in REPORT_FORMATS:
            return _deny(f"bad_format: {fmt} (elérhető: {','.join(REPORT_FORMATS)})")
        job = delphoi.get_job(get_db, job_id, account_ref(user))
        if not job.get("ok"):
            return _deny("not_found")
        if job.get("status") != "done":
            return json.dumps({"ok": False, "error": "not_ready",
                               "status": job.get("status")}, ensure_ascii=False)
        try:
            token = make_report_token(job_id, fmt)
        except RuntimeError as e:
            return _deny(str(e))
        url = f"{_public_base_url()}/api/delphoi/saas/report/{token}"
        exp = int(time.time()) + report_token_ttl()
        return json.dumps({"ok": True, "job_id": job_id, "format": fmt,
                           "download_url": url,
                           "expires_at": datetime.fromtimestamp(
                               exp, tz=timezone.utc).isoformat()},
                          ensure_ascii=False)

    @pub.tool()
    async def delphoi_credits(api_key: str) -> str:
        """Credit balance + recent ledger entries for the API key's account."""
        from plugins import delphoi
        user, deny = _auth(get_db, api_key)
        if deny:
            return deny
        ref = account_ref(user)
        delphoi.ensure_welcome(get_db, ref)
        out = delphoi.get_credits(get_db, ref)
        out["ok"] = True
        out["calls_per_credit"] = delphoi.CALLS_PER_CREDIT
        return json.dumps(out, ensure_ascii=False)

    return pub


# ---------------------------------------------------------------------------
# MOUNT-SEGÉD — a Starlette a mountolt al-app lifespan-jét NEM futtatja;
# ez a wrapper az ELSŐ kérésnél indítja el (és a processz életéig nyitva tartja).
# ---------------------------------------------------------------------------
class LazyLifespanASGI:
    def __init__(self, app):
        self._app = app
        self._lock = asyncio.Lock()
        self._started = False
        self._cm = None

    async def _ensure_started(self):
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            self._cm = self._app.router.lifespan_context(self._app)
            await self._cm.__aenter__()
            self._started = True
            logger.info("PYTHIA B3: publikus MCP al-app lifespan elindítva")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._app(scope, receive, send)
            return
        await self._ensure_started()
        await self._app(scope, receive, send)

    async def aclose(self):
        """Tiszta lezárás (teszt-higiénia; élesben a processz-halál zár)."""
        if self._started and self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except BaseException:  # noqa: BLE001 — task-átfedő cancel-scope zaj
                logger.debug("LazyLifespanASGI aclose noise", exc_info=True)
            self._started, self._cm = False, None


def build_public_asgi(deps: dict):
    """A mountolható ASGI-app: izolált FastMCP → http_app (stateless) →
    lazy-lifespan wrapper. A server.py ezt Mount(PUBLIC_MOUNT_PATH, app=...)-ként
    fűzi a fő app custom-route listájára → publikus út: /saas/mcp."""
    pub = build_public_mcp(deps)
    sub_app = pub.http_app(path=PUBLIC_MCP_PATH, stateless_http=True)
    return LazyLifespanASGI(sub_app)


def register_tools(app, deps):
    """Library-plugin (tool-count fegyelem): a FŐ appra SEMMILYEN toolt nem
    regisztrál — a 4 publikus tool KIZÁRÓLAG az izolált /saas/mcp al-appon él
    (a mountot a server.py B3-blokkja végzi). Itt csak deps-tárolás + séma."""
    global _DEPS
    _DEPS = deps
    try:
        conn = deps["get_db"]()
        try:
            ensure_key_tables(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — táblák nélkül a kulcs-út hangosan hasal
        logger.exception("delphoi_public_mcp ensure_key_tables failed")
    logger.info("delphoi_public_mcp betöltve (mount a server.py-ban: %s%s; toolok: %s)",
                PUBLIC_MOUNT_PATH, PUBLIC_MCP_PATH, ",".join(PUBLIC_TOOL_NAMES))
