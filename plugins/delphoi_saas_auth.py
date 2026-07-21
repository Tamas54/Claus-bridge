"""plugins/delphoi_saas_auth.py — OPERATION SIBYLLE S1: önálló SaaS auth + fiók.

E-mail magic-link auth az aipolling.io-hoz — a Bridge-oldali fele:

  POST /saas/auth/request-link {email}
      delphoi_users lookup/create (origin='saas', external_id=normalizált email),
      egyszer-használatos HMAC-token (15 perc), magic-link e-mail a Bridge
      meglévő Gmail-útján. NOTSTROM: ha küldés nem elérhető, a link CSAK
      DELPHOI_SAAS_DEV_MODE=1 esetén tér vissza a válaszban — élesben hangos 503.
  GET  /saas/auth/verify?token=
      token-verify (HMAC + lejárat + egyszer-használat a DB-ben) →
      aláírt session-token (30 nap) + idempotens signup-grant
      (delphoi.ensure_welcome — a delphoi_get_credits ÚTJA, nem másolat).
  GET  /saas/me
      session-token → user + kredit-egyenleg.
  GET/POST /saas/keys , POST /saas/keys/{key_id}/revoke
      vékony híd a B3 kulcs-logikára (delphoi_public_mcp.generate/list/revoke)
      session-token → (origin='saas', external_id=email) — NEM új tábla.
  POST /saas/submit {spec} , GET /saas/jobs/{job_id}
      az /ask-út: a B3 delphoi_submit_brief LÉPÉSEI session-auth mögött
      (delphoi_brief validál, delphoi.create_job atomi kredit-levonással,
      process_job háttérben) — a brief/motor-réteg az egy igazságforrás.
  POST /saas/precheck {question, n?}
      K1 élő kérdés-ellenőr: LLM-MENTES scope-heurisztika (+ EN-kiegészítés)
      + kredit- és futásidő-becslés. Nyitott, de rate-limitelt (gépelés
      közben hívja a storefront); piros verdikt konkrét átfogalmazási
      javaslattal tér vissza — sosem tilt, csak figyelmeztet.

REGISZTRÁCIÓ: a register_tools(app, deps) az app.custom_route()-tal veszi fel a
route-okat — server.py-módosítás NÉLKÜL. SORREND-INVARIÁNS: a plugin-discovery
(server.py, Operation Zahnrad blokk) a B3 Mount("/saas") append-je ELŐTT fut,
így a konkrét /saas/auth/* Route-ok megelőzik a Mount-ot a Starlette
route-listában — a Mount nem nyeli el őket.

BIZTONSÁG:
  - minden token HMAC-elve a DELPHOI_SAAS_SECRET-tel (fallback:
    DELPHOI_BRIDGE_KEY — a B3 _token_secret mintája);
  - a login-token egyszer-használatos (delphoi_saas_login_tokens.used_at,
    atomi UPDATE) és 15 perc múlva lejár;
  - e-mail-cím a logokba CSAK maszkolva kerül;
  - rate-limit a request-linkre (e-mail + IP szerinti egyszerű ablak);
  - a session-cookie (httponly+secure+samesite) a frontend (aipolling) dolga —
    ez a réteg Bearer-tokent ad és fogad.

MCP-toolt NEM regisztrál (tool-count fegyelem) — csak HTTP-route-okat.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone

from starlette.responses import JSONResponse

logger = logging.getLogger("plugins.delphoi_saas_auth")

__plugin_meta__ = {
    "name": "delphoi_saas_auth",
    "version": "1.0.0",
    "description": "SIBYLLE S1 — e-mail magic-link auth + fiok-API az aipolling.io-hoz (/saas/auth, /saas/me, /saas/keys)",
}

_DEPS: dict | None = None

LOGIN_TOKEN_TTL = 60 * 60            # magic-link: 60 perc (KLARTEXT: nyugodt első látogató se fusson lejáratba)
SESSION_TOKEN_TTL = 30 * 24 * 3600   # session: 30 nap

LOGIN_PREFIX = "lgn"
SESSION_PREFIX = "ses"

ROUTE_PATHS = ("/saas/auth/request-link", "/saas/auth/verify", "/saas/me",
               "/saas/keys", "/saas/keys/{key_id}/revoke",
               "/saas/submit", "/saas/jobs/{job_id}", "/saas/precheck")

# ---------------------------------------------------------------------------
# SÉMA — egyszer-használatos login-tokenek (a token maga sosem kerül DB-be,
# csak a SHA256-a; a used_at az egyszer-használat garanciája).
# ---------------------------------------------------------------------------
LOGIN_TOKENS_INIT_SQL = """
CREATE TABLE IF NOT EXISTS delphoi_saas_login_tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    email      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_delphoi_saas_login_email
    ON delphoi_saas_login_tokens(email);
"""


def ensure_auth_tables(conn) -> None:
    """Idempotens séma — login-tokenek + a B3 user/kulcs-táblák (egy útra)."""
    from plugins import delphoi_public_mcp as dpub
    dpub.ensure_key_tables(conn)
    conn.executescript(LOGIN_TOKENS_INIT_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# E-MAIL — normalizálás + maszkolás (log-higiénia: cím CSAK maszkolva logba).
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    """Kisbetűs, trimmelt alak — ez az external_id a delphoi_users-ben.
    Érvénytelen cím → üres string (a hívó dönt a hangos hibáról)."""
    e = str(email or "").strip().lower()
    if len(e) > 254 or not _EMAIL_RE.match(e):
        return ""
    return e


def mask_email(email: str) -> str:
    """'tamas.x@gmail.com' → 't***@g***' — logba KIZÁRÓLAG ez mehet."""
    e = str(email or "")
    if "@" not in e:
        return "***"
    local, _, domain = e.partition("@")
    return f"{local[:1]}***@{domain[:1]}***"


# ---------------------------------------------------------------------------
# TOKENEK — HMAC a DELPHOI_SAAS_SECRET-tel (B3 _token_secret minta).
# ---------------------------------------------------------------------------
def _secret() -> str:
    return (os.environ.get("DELPHOI_SAAS_SECRET", "")
            or os.environ.get("DELPHOI_BRIDGE_KEY", ""))


def _b64e(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def _b64d(s: str) -> str:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad).decode("utf-8")


def _sig(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_login_token(get_db, email: str, now: float | None = None) -> str:
    """Egyszer-használatos magic-link token (15 perc). A DB-be a hash megy."""
    secret = _secret()
    if not secret:
        raise RuntimeError("nincs SaaS-secret (DELPHOI_SAAS_SECRET | DELPHOI_BRIDGE_KEY)")
    t = now if now is not None else time.time()
    exp = int(t + LOGIN_TOKEN_TTL)
    nonce = secrets.token_urlsafe(12)
    token = ".".join([LOGIN_PREFIX, _b64e(email), str(exp), nonce,
                      _sig(f"saas-login|{email}|{exp}|{nonce}", secret)])
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO delphoi_saas_login_tokens "
            "(token_hash, email, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_hash_token(token), email,
             datetime.now(timezone.utc).isoformat(),
             datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()
    return token


def consume_login_token(get_db, token: str, now: float | None = None) -> str | None:
    """Érvényes, nem lejárt, MÉG NEM HASZNÁLT token → email; különben None.
    Az egyszer-használat atomi (UPDATE ... WHERE used_at IS NULL)."""
    secret = _secret()
    if not secret:
        return None
    parts = (token or "").split(".")
    if len(parts) != 5 or parts[0] != LOGIN_PREFIX:
        return None
    _, email_b64, exp_s, nonce, sig = parts
    try:
        email = _b64d(email_b64)
        exp = int(exp_s)
    except (ValueError, UnicodeDecodeError):
        return None
    if (now if now is not None else time.time()) > exp:
        return None
    want = _sig(f"saas-login|{email}|{exp}|{nonce}", secret)
    if not hmac.compare_digest(want, sig):
        return None
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE delphoi_saas_login_tokens SET used_at=? "
            "WHERE token_hash=? AND used_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), _hash_token(token)))
        conn.commit()
        if not cur.rowcount:
            return None      # nem kiadott VAGY már elhasznált token
    finally:
        conn.close()
    return email


def issue_session_token(user_id: int, email: str, now: float | None = None) -> str:
    """Aláírt session-token (30 nap) — állapotmentes, a HMAC a felhatalmazás."""
    secret = _secret()
    if not secret:
        raise RuntimeError("nincs SaaS-secret (DELPHOI_SAAS_SECRET | DELPHOI_BRIDGE_KEY)")
    exp = int((now if now is not None else time.time()) + SESSION_TOKEN_TTL)
    return ".".join([SESSION_PREFIX, str(int(user_id)), _b64e(email), str(exp),
                     _sig(f"saas-session|{int(user_id)}|{email}|{exp}", secret)])


def verify_session_token(token: str, now: float | None = None) -> dict | None:
    """Érvényes session-token → {'user_id', 'email', 'expires_at'}; más → None."""
    secret = _secret()
    if not secret:
        return None
    parts = (token or "").split(".")
    if len(parts) != 5 or parts[0] != SESSION_PREFIX:
        return None
    _, uid_s, email_b64, exp_s, sig = parts
    try:
        uid = int(uid_s)
        exp = int(exp_s)
        email = _b64d(email_b64)
    except (ValueError, UnicodeDecodeError):
        return None
    if (now if now is not None else time.time()) > exp:
        return None
    want = _sig(f"saas-session|{uid}|{email}|{exp}", secret)
    if not hmac.compare_digest(want, sig):
        return None
    return {"user_id": uid, "email": email,
            "expires_at": datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# RATE-LIMIT — egyszerű fix ablak a request-linkre (e-mail + IP szerint).
# ---------------------------------------------------------------------------
_RL_HITS: dict = {}     # kulcs → [timestamp, ...] az ablakon belül


def _rl_conf() -> tuple[int, int, int]:
    """(max/e-mail, max/IP, ablak-sec). Env: DELPHOI_SAAS_LINK_MAX (5),
    DELPHOI_SAAS_LINK_WINDOW_SEC (900); IP-küszöb a 3×e-mail-küszöb."""
    try:
        per_email = max(1, int(os.environ.get("DELPHOI_SAAS_LINK_MAX", "5")))
    except ValueError:
        per_email = 5
    try:
        window = max(10, int(os.environ.get("DELPHOI_SAAS_LINK_WINDOW_SEC", "900")))
    except ValueError:
        window = 900
    return per_email, per_email * 3, window


def rate_limit_link(email: str, ip: str, now: float | None = None) -> bool:
    """True = mehet; False = 429. Fix ablak, in-memory (a B3 bucket-minta rokona)."""
    per_email, per_ip, window = _rl_conf()
    t = now if now is not None else time.monotonic()
    ok = True
    for key, limit in ((f"e:{email}", per_email), (f"i:{ip}", per_ip)):
        hits = [h for h in _RL_HITS.get(key, []) if t - h < window]
        if len(hits) >= limit:
            ok = False
        else:
            hits.append(t)
        _RL_HITS[key] = hits
    return ok


# ---------------------------------------------------------------------------
# E-MAIL-KÜLDÉS — a Bridge meglévő Gmail-útján (reuse, nem másolat):
#   1) capture_state['_send_email_func'] (server.py capture_send_email —
#      a Feldwebel-wiring ugyanígy hívja: await fn(to=, subject=, body=));
#   2) fallback: capture_state['gmail_service'] direkt (MIMEText, to_thread).
# ---------------------------------------------------------------------------
def dev_mode() -> bool:
    return os.environ.get("DELPHOI_SAAS_DEV_MODE", "") == "1"


def _login_url_base() -> str:
    """A magic-link célja: a frontend callbackje (DELPHOI_SAAS_LOGIN_URL, pl.
    https://aipolling.io/auth/callback); fallback a Bridge saját verify-útja."""
    base = (os.environ.get("DELPHOI_SAAS_LOGIN_URL") or "").rstrip("/")
    if base:
        return base
    pub = (os.environ.get("DELPHOI_SAAS_PUBLIC_URL") or "").rstrip("/")
    return f"{pub}/saas/auth/verify"


def magic_link(token: str) -> str:
    return f"{_login_url_base()}?token={token}"


_EMAIL_SUBJECT = "Your sign-in link"
_EMAIL_BODY_HTML = """\
<p>Hi,</p>
<p>Click the link below to sign in. It works once and expires in 60 minutes.</p>
<p><a href="{link}">{link}</a></p>
<p>If you didn't request this, you can safely ignore this email.</p>
"""


async def send_magic_email(deps: dict, email: str, link: str) -> tuple[bool, str]:
    """(ok, hiba-ok). A cím a logokba CSAK maszkolva kerül."""
    body = _EMAIL_BODY_HTML.format(link=link)
    cs = (deps or {}).get("capture_state") or {}
    fn = cs.get("_send_email_func")
    if fn is not None:
        try:
            res = await fn(to=email, subject=_EMAIL_SUBJECT, body=body,
                           body_type="html")
            data = json.loads(res)
            if data.get("status") == "sent":
                return True, ""
            return False, str(data.get("error") or "send_failed")
        except Exception as e:  # noqa: BLE001
            logger.error("SIBYLLE: magic-link kuldes hiba (%s): %s",
                         mask_email(email), type(e).__name__)
            return False, f"{type(e).__name__}"
    svc = cs.get("gmail_service")
    if svc is None:
        return False, "email_transport_unavailable"
    try:
        from email.mime.text import MIMEText

        def _send():
            msg = MIMEText(body, "html")
            msg["to"] = email
            msg["subject"] = _EMAIL_SUBJECT
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            return svc.users().messages().send(userId="me", body={"raw": raw}).execute()

        await asyncio.to_thread(_send)
        return True, ""
    except Exception as e:  # noqa: BLE001
        logger.error("SIBYLLE: gmail direkt kuldes hiba (%s): %s",
                     mask_email(email), type(e).__name__)
        return False, f"{type(e).__name__}"


# ---------------------------------------------------------------------------
# SESSION-KIVÉTEL a kérésből — Authorization: Bearer <tok> vagy saas_session cookie.
# ---------------------------------------------------------------------------
def _session_from_request(request) -> dict | None:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        token = request.cookies.get("saas_session", "")
    return verify_session_token(token) if token else None


def _client_ip(request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _err(reason: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": reason}, status_code=status)


# ---------------------------------------------------------------------------
# HANDLEREK — vékonyak; a logika a fenti könyvtár-függvényekben él.
# ---------------------------------------------------------------------------
async def handle_request_link(request) -> JSONResponse:
    """POST /saas/auth/request-link {email} → user get-or-create + magic-link."""
    deps = _DEPS or {}
    get_db = deps["get_db"]
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return _err("bad_json")
    email = normalize_email(data.get("email"))
    if not email:
        return _err("invalid_email")
    if not rate_limit_link(email, _client_ip(request)):
        logger.warning("SIBYLLE: rate-limited request-link (%s)", mask_email(email))
        return _err("rate_limited", 429)
    from plugins import delphoi_public_mcp as dpub
    user_id = dpub.get_or_create_user(get_db, "saas", email)
    try:
        token = issue_login_token(get_db, email)
    except RuntimeError as e:
        logger.error("SIBYLLE: token-kiadas hiba: %s", e)
        return _err("server_misconfigured", 503)
    link = magic_link(token)
    sent, why = await send_magic_email(deps, email, link)
    if sent:
        logger.info("SIBYLLE: magic-link elkuldve (%s, user_id=%d)",
                    mask_email(email), user_id)
        return JSONResponse({"ok": True, "sent": True})
    if dev_mode():
        # NOTSTROM (teszt-út): a link CSAK dev-módban tér vissza a válaszban.
        logger.warning("SIBYLLE DEV: kuldes nem ment (%s) — link a valaszban", why)
        return JSONResponse({"ok": True, "sent": False, "dev_mode": True,
                             "magic_link": link})
    logger.error("SIBYLLE: magic-link kuldes SIKERTELEN (%s): %s",
                 mask_email(email), why)
    return _err("email_send_unavailable", 503)


async def handle_verify(request) -> JSONResponse:
    """GET /saas/auth/verify?token= → session-token + idempotens signup-grant."""
    deps = _DEPS or {}
    get_db = deps["get_db"]
    email = consume_login_token(get_db, request.query_params.get("token", "")[:512])
    if not email:
        return _err("invalid_or_expired_token", 403)
    from plugins import delphoi, delphoi_public_mcp as dpub
    user_id = dpub.get_or_create_user(get_db, "saas", email)
    # Signup-grant: a MEGLÉVŐ idempotens út (delphoi.ensure_welcome) — az
    # account_ref saas+email userre maga a normalizált email (B3 account_ref).
    delphoi.ensure_welcome(get_db, email)
    session = issue_session_token(user_id, email)
    info = verify_session_token(session)
    logger.info("SIBYLLE: sikeres verify (%s, user_id=%d)", mask_email(email), user_id)
    return JSONResponse({"ok": True, "session_token": session,
                         "user_id": user_id, "email": email,
                         "expires_at": info["expires_at"] if info else ""})


async def handle_me(request) -> JSONResponse:
    """GET /saas/me → user + kredit-egyenleg (a session-token a felhatalmazás)."""
    deps = _DEPS or {}
    get_db = deps["get_db"]
    sess = _session_from_request(request)
    if not sess:
        return _err("invalid_session", 401)
    from plugins import delphoi
    ref = sess["email"]
    delphoi.ensure_welcome(get_db, ref)
    credits = delphoi.get_credits(get_db, ref)
    return JSONResponse({"ok": True,
                         "user": {"user_id": sess["user_id"],
                                  "email": sess["email"], "origin": "saas"},
                         "balance": credits["balance"],
                         "ledger": credits["ledger"],
                         "calls_per_credit": delphoi.CALLS_PER_CREDIT,
                         "session_expires_at": sess["expires_at"]})


async def handle_keys(request) -> JSONResponse:
    """GET /saas/keys → kulcs-lista (plaintext SOHA);
    POST /saas/keys {label} → új kulcs — a plaintext EGYSZER látszik.
    Vékony híd a B3 kulcs-logikára (origin='saas', external_id=email)."""
    deps = _DEPS or {}
    get_db = deps["get_db"]
    sess = _session_from_request(request)
    if not sess:
        return _err("invalid_session", 401)
    from plugins import delphoi_public_mcp as dpub
    if request.method == "GET":
        return JSONResponse({"ok": True,
                             "keys": dpub.list_api_keys(get_db, "saas", sess["email"])})
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    return JSONResponse(dpub.generate_api_key(get_db, "saas", sess["email"],
                                              str(data.get("label") or "")))


async def handle_key_revoke(request) -> JSONResponse:
    """POST /saas/keys/{key_id}/revoke — csak a session tulajdonosának kulcsára."""
    deps = _DEPS or {}
    get_db = deps["get_db"]
    sess = _session_from_request(request)
    if not sess:
        return _err("invalid_session", 401)
    try:
        key_id = int(request.path_params.get("key_id", "0"))
    except (TypeError, ValueError):
        return _err("bad_key_id")
    from plugins import delphoi_public_mcp as dpub
    res = dpub.revoke_api_key(get_db, "saas", sess["email"], key_id)
    return JSONResponse(res, status_code=200 if res.get("ok") else 404)


# ---------------------------------------------------------------------------
# K1 — ÉLŐ KÉRDÉS-ELLENŐR (/saas/precheck). LLM-MENTES: a delphoi_scopegate
# HEURISZTIKA-útja + EN-kiegészítő kulcsszó-réteg (a storefront angol kérdéseit
# a HU-központú alaplista gyengén fedné). Nyitott végpont, saját rate-limittel
# (gépelés közben hívja a storefront — session ilyenkor még nincs).
# ---------------------------------------------------------------------------
_EN_STRUCTURAL_HARD = (
    "market share", "stock price", "share price", "exchange rate",
    "interest rate", "yield curve", "quarterly earnings", "revenue growth",
)
_EN_STRUCTURAL_SOFT = (
    "inflation rate", "unemployment rate", "growth rate", "price target",
    "how much will", "what percentage", "by how many percent",
)

# Piros verdikt → KONKRÉT átfogalmazási javaslat a talált kulcsszó-osztályhoz.
_RED_SUGGESTIONS = (
    (("gdp", "growth", "forecast", "előrejelzés", "elorejelzes", "inflation",
      "unemployment", "kibocsátás", "kibocsatas"),
     "Do people feel the economy is getting better or worse?"),
    (("stock", "share price", "árfolyam", "arfolyam", "exchange rate",
      "interest rate", "yield", "hozamgörbe", "hozamgorbe"),
     "How confident do people feel about their own finances right now?"),
    (("szektor", "sector", "iparág", "iparag", "industry", "b2b",
      "supply chain", "beszállító", "beszallito", "értéklánc", "erteklanc",
      "vertikum", "ágazat", "agazat"),
     "How would this message land with the people who buy from this industry?"),
)
_RED_DEFAULT_SUGGESTION = "How do people feel about this — hopeful or worried?"

PRECHECK_MESSAGES = {
    "green": "Opinion question — we can measure this.",
    "yellow": ("Partly measurable — we can read the mood around this, "
               "but not the hard numbers in it. Treat with care."),
    "red": "This depends on hard data, not public mood — our method can't read it.",
}


def precheck_scope(text: str) -> dict:
    """A scopegate-heurisztika + EN-kiegészítés, EN verdikt-kulcsokkal.
    LLM-hívás NINCS (a Hy3-ítész a job-felvétel útján marad)."""
    from plugins import delphoi_scopegate as sg
    heur = sg.heuristic_scope(text)
    low = " ".join(str(text or "").lower().split())
    en_hard = sorted({p for p in _EN_STRUCTURAL_HARD if p in low})
    en_soft = sorted({p for p in _EN_STRUCTURAL_SOFT if p in low})
    n_soft = len(heur["structural_soft"]) + len(en_soft)
    if heur["structural_hard"] or en_hard or n_soft >= 2:
        verdict = "red"
    elif n_soft:
        verdict = "yellow"
    else:
        verdict = "green"
    found = heur["structural_hard"] + heur["structural_soft"] + en_hard + en_soft
    return {"verdict": verdict, "found": found}


def _red_suggestion(found: list[str]) -> str:
    haystack = " ".join(found)
    for keys, suggestion in _RED_SUGGESTIONS:
        if any(k in haystack for k in keys):
            return suggestion
    return _RED_DEFAULT_SUGGESTION


def rate_limit_precheck(ip: str, now: float | None = None) -> bool:
    """Külön ablak a prechecknek (gépelés közbeni hívások): env
    DELPHOI_SAAS_PRECHECK_MAX (120) / DELPHOI_SAAS_PRECHECK_WINDOW_SEC (60)."""
    try:
        limit = max(1, int(os.environ.get("DELPHOI_SAAS_PRECHECK_MAX", "120")))
    except ValueError:
        limit = 120
    try:
        window = max(5, int(os.environ.get("DELPHOI_SAAS_PRECHECK_WINDOW_SEC", "60")))
    except ValueError:
        window = 60
    t = now if now is not None else time.monotonic()
    key = f"p:{ip}"
    hits = [h for h in _RL_HITS.get(key, []) if t - h < window]
    ok = len(hits) < limit
    if ok:
        hits.append(t)
    _RL_HITS[key] = hits
    return ok


async def handle_precheck(request) -> JSONResponse:
    """POST /saas/precheck {question, n?, dimensions?, custom_questions?,
    stimuli?} → LLM-mentes elő-verdikt + kredit- és idő-becslés. Nyitott,
    de rate-limitelt; a végleges kapu a submit-út scope/validátor-rétege."""
    deps = _DEPS or {}
    if not rate_limit_precheck(_client_ip(request)):
        return _err("rate_limited", 429)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return _err("bad_json")
    question = str(data.get("question") or "").strip()[:800]
    if not question:
        return _err("empty_question")

    from plugins import delphoi, delphoi_brief

    def _num(key: str, dflt: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(data.get(key) or dflt)))
        except (TypeError, ValueError):
            return dflt

    n = _num("n", 100, 1, delphoi.n_max())
    n_dims = _num("dimensions", 1, 1, 5)
    n_custom = _num("custom_questions", 0, 0, 3)
    n_stim = _num("stimuli", 1, 1, 8)

    scope = precheck_scope(question)
    verdict = scope["verdict"]
    eta_s = delphoi.estimate_runtime_seconds(delphoi.job_call_count({"n_per_cell": n}))
    out = {
        "ok": True, "verdict": verdict,
        "message": PRECHECK_MESSAGES[verdict],
        "credit_estimate": delphoi_brief.estimate_credits(n, n_dims, n_custom, n_stim),
        "eta_seconds": eta_s, "eta_minutes": max(1, round(eta_s / 60)),
        "n": n, "n_max": delphoi.n_max(),
    }
    if verdict == "red":
        out["suggestion"] = _red_suggestion(scope["found"])
    # Kapacitás-előjelzés: a felvételkori kemény kapu (handle_submit) emberi
    # előképe — itt csak warning, ott 429 a levonás ELŐTT.
    try:
        if (delphoi.estimate_job_embed_tokens({"n_per_cell": n})
                > delphoi.embed_budget_remaining(deps["get_db"])):
            out["capacity_warning"] = (
                "Today's measuring capacity is nearly used up — a panel this "
                "size would exceed it. Try a smaller panel, or run it tomorrow.")
    except Exception:  # noqa: BLE001 — a kapacitás-jelzés hiánya nem hiba
        pass
    return JSONResponse(out)


async def handle_submit(request) -> JSONResponse:
    """POST /saas/submit {spec} — az /ask-út. A B3 delphoi_submit_brief
    LÉPÉS-SORRENDJE session-auth mögött: validál → scope-verdikt (LLM-mentes)
    → kredit-ellenőrzés a felvétel ELŐTT → save_brief → create_job (atomi
    levonás) → process_job háttérben (a refund-vasszabály változatlan)."""
    deps = _DEPS or {}
    get_db = deps["get_db"]
    sess = _session_from_request(request)
    if not sess:
        return _err("invalid_session", 401)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return _err("bad_json")
    spec = data.get("spec")
    if not isinstance(spec, dict):
        return _err("missing_spec")
    from plugins import delphoi, delphoi_brief, delphoi_scopegate
    errors = delphoi_brief.validate_brief(spec)
    if errors:
        return JSONResponse({"ok": False, "error": "invalid_brief",
                             "errors": errors}, status_code=400)
    canonical = delphoi_brief.canonicalize_spec(spec)
    stim_text = "\n".join([canonical["goal"], *canonical["stimuli"]])
    scope = await delphoi_scopegate.scope_verdict(stim_text, use_judge=False)
    kind, text, panel_spec, variants = delphoi_brief.brief_to_job_args(canonical)
    ref = sess["email"]
    cost = delphoi.job_cost(panel_spec, variants)
    estimate = delphoi_brief.estimate_credits(
        canonical["n"], len(canonical["dimensions"]),
        len(canonical["custom_questions"]), max(1, len(canonical["stimuli"])))
    delphoi.ensure_welcome(get_db, ref)
    balance = delphoi.get_credits(get_db, ref)["balance"]
    if balance < cost:
        return JSONResponse({"ok": False, "error": "insufficient_credits",
                             "cost": cost, "balance": balance,
                             "credit_estimate": estimate}, status_code=402)
    # MOD1-C: embed-büdzsé-kapu a FELVÉTELKOR (N×k-becslés) — a levonás ELŐTT,
    # emberi üzenettel. A futás-közbeni charge_embed_budget marad a végső őr;
    # ez a kapu a "levontuk, aztán bukott" utat zárja ki.
    if (delphoi.estimate_job_embed_tokens(panel_spec)
            > delphoi.embed_budget_remaining(get_db)):
        return JSONResponse({
            "ok": False, "error": "capacity_exhausted",
            "message": ("Today's measuring capacity is nearly used up — a panel "
                        "this size would exceed it. Nothing was charged. Try a "
                        "smaller panel now, or run this one tomorrow "
                        "(capacity resets daily)."),
        }, status_code=429)
    saved = delphoi_brief.save_brief(get_db, ref, canonical)
    brief_id = saved.get("brief_id") if saved.get("ok") else ""
    panel_spec["brief_id"] = brief_id
    created = delphoi.create_job(get_db, ref, kind, text, panel_spec, variants)
    if not created.get("ok"):
        return JSONResponse(created, status_code=400)
    job_id = created["job_id"]
    run_deps = {k: deps.get(k) for k in (
        "get_db", "siliconflow_api_key", "siliconflow_base_url",
        "siliconflow_timeout", "siliconflow_models")}
    asyncio.create_task(delphoi.process_job(run_deps, job_id))
    logger.info("SIBYLLE: /saas/submit job=%s (user=%s, cost=%d)",
                job_id, mask_email(ref), created["cost"])
    return JSONResponse({
        "ok": True, "job_id": job_id, "brief_id": brief_id,
        "status": "queued", "credits_charged": created["cost"],
        "credit_estimate": estimate, "balance": created["balance"],
        "scope_verdict": {"verdict": scope["verdict"],
                          "warning": scope.get("warning")},
    })


async def handle_job(request) -> JSONResponse:
    """GET /saas/jobs/{job_id} — állapot/eredmény, CSAK a tulajdonosnak.
    Aggregátum megy ki (a delphoi.get_job eleve aggregátum-only)."""
    deps = _DEPS or {}
    get_db = deps["get_db"]
    sess = _session_from_request(request)
    if not sess:
        return _err("invalid_session", 401)
    from plugins import delphoi
    res = delphoi.get_job(get_db, request.path_params.get("job_id", ""),
                          sess["email"])
    return JSONResponse(res, status_code=200 if res.get("ok") else 404)


# ---------------------------------------------------------------------------
# REGISZTRÁCIÓ — custom_route-ok a FŐ appra, server.py-módosítás NÉLKÜL.
# ---------------------------------------------------------------------------
def register_tools(app, deps):
    global _DEPS
    _DEPS = deps
    try:
        conn = deps["get_db"]()
        try:
            ensure_auth_tables(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("delphoi_saas_auth ensure_auth_tables failed")

    custom_route = getattr(app, "custom_route", None)
    if not callable(custom_route):
        # FakeApp/teszt-környezet — a könyvtár-függvények így is élnek.
        logger.warning("delphoi_saas_auth: az app nem tud custom_route-ot — "
                       "HTTP-utak NEM regisztralodtak")
        return

    custom_route("/saas/auth/request-link", methods=["POST"])(handle_request_link)
    custom_route("/saas/auth/verify", methods=["GET"])(handle_verify)
    custom_route("/saas/me", methods=["GET"])(handle_me)
    custom_route("/saas/keys", methods=["GET", "POST"])(handle_keys)
    custom_route("/saas/keys/{key_id}/revoke", methods=["POST"])(handle_key_revoke)
    custom_route("/saas/precheck", methods=["POST"])(handle_precheck)
    custom_route("/saas/submit", methods=["POST"])(handle_submit)
    custom_route("/saas/jobs/{job_id}", methods=["GET"])(handle_job)
    logger.info("delphoi_saas_auth betoltve — utak: %s (dev_mode=%s)",
                ", ".join(ROUTE_PATHS), dev_mode())
