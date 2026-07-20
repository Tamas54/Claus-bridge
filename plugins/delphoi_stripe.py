"""plugins/delphoi_stripe.py — PYTHIA P4: PÉNZ-ÚT (Stripe test-mode, MOD2/A5).

A topup-stub mögé kerülő valódi pénz-út, a Bridge-oldalon (itt él a
credit_ledger). Három felelősség:

  1. CHECKOUT — Stripe Checkout Session létrehozás a kredit-csomag
     árlistából (delphoi_price_config). Elsődleges út a Stripe-Python SDK;
     NOTSTROM: sima REST-hívás httpx-szel (form-encoded), ha az SDK nem
     elérhető. `payment_method_types`-t SOHA nem adunk át (dinamikus
     fizetési módok — Stripe best practice).
  2. WEBHOOK — aláírás-ellenőrzés (Stripe v1 HMAC-SHA256 séma,
     STRIPE_WEBHOOK_SECRET) + esemény-feldolgozás:
       checkout.session.completed (payment_status=paid) → jóváírás;
       checkout.session.async_payment_succeeded → jóváírás;
       *payment_failed / expired → napló (delphoi_stripe_events), NINCS jóváírás.
  3. IDEMPOTENCIA — a meglévő UNIQUE(user_id, reason) ledger-minta marad:
     a webhook-reason a Stripe EVENT-ID-t tartalmazza
     ('topup:stripe:<event_id>') → replay nem duplikál. Másodlagos őr:
     session-szintű 'credited' journal-ellenőrzés (különböző event-típusok
     sem írhatnak jóvá kétszer ugyanarra a sessionre).

MOD2/A5 — árlista config-DB-táblában (delphoi_price_config): origin/brand
dimenzió ('echolot' → HUF/EUR, 'saas' → USD/EUR), Stripe product/price-ID-k
a config-sorban brand-enként; admin-CRUD REST a server.py-ban X-Delphoi-Key
mögött. A credit_ledger sorai origin-t rögzítenek (ÚJ oszlop — a
delphoi_users external_id-je elvben két origin alatt is létezhetne, ezért a
levezetés nem egyértelmű; az oszlop az). Régi sor NEM ÉRINTHETŐ: origin=NULL
= Stripe-előtti korszak.

MINDEN ÁR PLACEHOLDER — a végleges árakat a Kommandant dönti el.

VASSZABÁLY: a kulcsok (STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET) CSAK env-ből
jönnek, logba/hibaüzenetbe SOHA nem kerülnek.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import random
import string
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("plugins.delphoi_stripe")

__plugin_meta__ = {
    "name": "delphoi_stripe",
    "version": "1.0.0",
    "description": "DELPHOI penz-ut: Stripe checkout + webhook + arlista-config (PYTHIA P4, MOD2/A5)",
}

STRIPE_API_BASE = "https://api.stripe.com"

# origin → engedélyezett pénznemek (MOD2/A5: HUF/EUR az echolot-originen,
# USD/EUR a saas-on). Új origin/pénznem = config-bővítés itt, nem kód másutt.
ORIGIN_CURRENCIES = {
    "echolot": ("HUF", "EUR"),
    "saas": ("USD", "EUR"),
}

# ── Séma (egy igazságforrás — a migrate_pythia_p4 is innen importálja) ──────
_STRIPE_INIT_SQL = """
CREATE TABLE IF NOT EXISTS delphoi_price_config (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    origin            TEXT NOT NULL CHECK(origin IN ('echolot','saas')),
    pack_key          TEXT NOT NULL,
    credits           INTEGER NOT NULL,
    currency          TEXT NOT NULL,
    unit_amount       INTEGER NOT NULL,
    stripe_product_id TEXT NOT NULL DEFAULT '',
    stripe_price_id   TEXT NOT NULL DEFAULT '',
    active            INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE(origin, pack_key, currency)
);

CREATE TABLE IF NOT EXISTS delphoi_stripe_events (
    event_id   TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    user_id    TEXT NOT NULL DEFAULT '',
    credits    INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

# PLACEHOLDER-ÁRAK — Kommandant-döntés! (unit_amount Stripe minor-unitban;
# HUF: Stripe-szabály szerint 100-zal oszthatónak kell lennie.)
SEED_PACKS = [
    # origin,   pack_key,  credits, currency, unit_amount
    ("echolot", "starter", 10, "HUF", 499000),    # 4 990 Ft — PLACEHOLDER, Kommandant-döntés
    ("echolot", "starter", 10, "EUR", 1200),      # 12.00 € — PLACEHOLDER, Kommandant-döntés
    ("echolot", "pro",     50, "HUF", 1990000),   # 19 900 Ft — PLACEHOLDER, Kommandant-döntés
    ("echolot", "pro",     50, "EUR", 4900),      # 49.00 € — PLACEHOLDER, Kommandant-döntés
    ("saas",    "starter", 10, "USD", 1500),      # $15.00 — PLACEHOLDER, Kommandant-döntés
    ("saas",    "starter", 10, "EUR", 1400),      # 14.00 € — PLACEHOLDER, Kommandant-döntés
    ("saas",    "pro",     50, "USD", 5900),      # $59.00 — PLACEHOLDER, Kommandant-döntés
    ("saas",    "pro",     50, "EUR", 5500),      # 55.00 € — PLACEHOLDER, Kommandant-döntés
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_stripe_tables(conn) -> None:
    """Idempotens séma: price-config + event-journal + (ha a credit_ledger már
    létezik) origin oszlop rá. RÉGI SOR NEM ÉRINTHETŐ — az új oszlop minden
    meglévő sorban NULL marad (= Stripe-előtti korszak)."""
    conn.executescript(_STRIPE_INIT_SQL)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='delphoi_credit_ledger'"
    ).fetchone()
    if row:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(delphoi_credit_ledger)")]
        if "origin" not in cols:
            conn.execute("ALTER TABLE delphoi_credit_ledger ADD COLUMN origin TEXT")
    conn.commit()


def seed_prices(conn) -> int:
    """Idempotens placeholder-seed (UNIQUE(origin, pack_key, currency) véd).
    ÁRAK = PLACEHOLDER — Kommandant-döntés."""
    ts = _now()
    n = 0
    for origin, pack_key, credits, currency, unit_amount in SEED_PACKS:
        cur = conn.execute(
            "INSERT OR IGNORE INTO delphoi_price_config "
            "(origin, pack_key, credits, currency, unit_amount, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (origin, pack_key, credits, currency, unit_amount, ts, ts))
        n += cur.rowcount or 0
    conn.commit()
    return n


# ── Árlista-CRUD (admin REST-háttér — X-Delphoi-Key a server.py-ban) ────────

def list_prices(get_db, origin: str = "", only_active: bool = False) -> list[dict]:
    conn = get_db()
    try:
        ensure_stripe_tables(conn)
        q = "SELECT * FROM delphoi_price_config"
        cond, args = [], []
        if origin:
            cond.append("origin=?")
            args.append(origin)
        if only_active:
            cond.append("active=1")
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY origin, pack_key, currency"
        return [dict(r) for r in conn.execute(q, args).fetchall()]
    finally:
        conn.close()


def _validate_pack(data: dict) -> str | None:
    """None = OK, egyébként hiba-kód. Currency-fegyelem originenként."""
    origin = str(data.get("origin") or "").strip().lower()
    if origin not in ORIGIN_CURRENCIES:
        return "bad_origin"
    if not str(data.get("pack_key") or "").strip():
        return "missing_pack_key"
    currency = str(data.get("currency") or "").strip().upper()
    if currency not in ORIGIN_CURRENCIES[origin]:
        return "bad_currency_for_origin"
    try:
        credits = int(data.get("credits") or 0)
        unit_amount = int(data.get("unit_amount") or 0)
    except (TypeError, ValueError):
        return "bad_number"
    if credits <= 0 or unit_amount <= 0:
        return "bad_number"
    if currency == "HUF" and unit_amount % 100 != 0:
        return "huf_not_divisible_by_100"   # Stripe HUF-szabály
    return None


def upsert_price(get_db, data: dict) -> dict:
    """Insert-vagy-update az (origin, pack_key, currency) kulcson. A Stripe
    product/price-ID-k is itt tárolódnak brand-enként (MOD2/A5)."""
    err = _validate_pack(data)
    if err:
        return {"ok": False, "error": err}
    origin = str(data["origin"]).strip().lower()
    pack_key = str(data["pack_key"]).strip()
    currency = str(data["currency"]).strip().upper()
    ts = _now()
    conn = get_db()
    try:
        ensure_stripe_tables(conn)
        conn.execute(
            "INSERT INTO delphoi_price_config "
            "(origin, pack_key, credits, currency, unit_amount, "
            " stripe_product_id, stripe_price_id, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(origin, pack_key, currency) DO UPDATE SET "
            " credits=excluded.credits, unit_amount=excluded.unit_amount, "
            " stripe_product_id=excluded.stripe_product_id, "
            " stripe_price_id=excluded.stripe_price_id, "
            " active=excluded.active, updated_at=excluded.updated_at",
            (origin, pack_key, int(data["credits"]), currency,
             int(data["unit_amount"]), str(data.get("stripe_product_id") or ""),
             str(data.get("stripe_price_id") or ""),
             1 if int(data.get("active", 1)) else 0, ts, ts))
        conn.commit()
        row = conn.execute(
            "SELECT * FROM delphoi_price_config WHERE origin=? AND pack_key=? AND currency=?",
            (origin, pack_key, currency)).fetchone()
        return {"ok": True, "price": dict(row)}
    finally:
        conn.close()


def delete_price(get_db, price_id: int) -> dict:
    conn = get_db()
    try:
        ensure_stripe_tables(conn)
        cur = conn.execute("DELETE FROM delphoi_price_config WHERE id=?", (int(price_id),))
        conn.commit()
        if not cur.rowcount:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "deleted": int(price_id)}
    finally:
        conn.close()


def get_pack(get_db, origin: str, pack_key: str, currency: str) -> dict | None:
    conn = get_db()
    try:
        ensure_stripe_tables(conn)
        row = conn.execute(
            "SELECT * FROM delphoi_price_config "
            "WHERE origin=? AND pack_key=? AND currency=? AND active=1",
            (str(origin).strip().lower(), str(pack_key).strip(),
             str(currency).strip().upper())).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def resolve_origin(get_db, user_id: str) -> str:
    """A user originje a delphoi_users-ből (A2). Ha nem egyértelmű (nincs sor,
    vagy ugyanaz az external_id két origin alatt), a default 'echolot'."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT DISTINCT origin FROM delphoi_users WHERE external_id=?",
            (str(user_id),)).fetchall()
    except Exception:  # noqa: BLE001 — delphoi_users még nem létezik
        rows = []
    finally:
        conn.close()
    if len(rows) == 1 and rows[0][0] in ORIGIN_CURRENCIES:
        return rows[0][0]
    return "echolot"


# ── Checkout Session létrehozás (SDK-elsődleges, httpx-REST NOTSTROM) ──────

def _flatten(params: dict, prefix: str = "") -> list[tuple[str, str]]:
    """Stripe form-encoding: nested dict/list → bracket-kulcsok
    (line_items[0][price_data][currency]=...)."""
    out: list[tuple[str, str]] = []
    for k, v in params.items():
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, dict):
            out.extend(_flatten(v, key))
        elif isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    out.extend(_flatten(item, f"{key}[{i}]"))
                else:
                    out.append((f"{key}[{i}]", str(item)))
        elif isinstance(v, bool):
            out.append((key, "true" if v else "false"))
        elif v is not None:
            out.append((key, str(v)))
    return out


def _rest_create_session(api_key: str, params: dict, transport=None) -> dict:
    """NOTSTROM-út: Checkout Session a nyers REST API-n (httpx, form-encoded).
    A transport injektálható (teszt: httpx.MockTransport)."""
    with httpx.Client(base_url=STRIPE_API_BASE, timeout=30,
                      transport=transport) as client:
        resp = client.post("/v1/checkout/sessions", data=dict(_flatten(params)),
                           auth=(api_key, ""))
        body = resp.json()
        if resp.status_code >= 400:
            msg = (body.get("error") or {}).get("message", "")[:200]
            raise RuntimeError(f"stripe_api_{resp.status_code}: {msg}")
        return body


def _sdk_create_session(api_key: str, params: dict) -> dict:
    import stripe  # a hívó dönt a fallbackról ImportError-nál
    client = stripe.StripeClient(api_key)
    session = client.v1.checkout.sessions.create(params=params)
    return {"id": session.id, "url": session.url}


def _integration_id() -> str:
    # Stripe best practice (2026-03-25.dahlia+): integration_identifier címke
    # 8 véletlen betű suffixszel — Dashboard-oldali flow-követéshez.
    suffix = "".join(random.choices(string.ascii_lowercase, k=8))
    return f"delphoi-topup-{suffix}"


def build_checkout_params(pack: dict, user_id: str, success_url: str,
                          cancel_url: str) -> dict:
    """A Checkout Session paraméterei a config-sorból. Ha a sorban van
    stripe_price_id (brand-enkénti Stripe-katalógus, MOD2/A5), azt használjuk;
    egyébként inline price_data. payment_method_types SOHA (dinamikus
    fizetési módok)."""
    if pack.get("stripe_price_id"):
        line_item = {"price": pack["stripe_price_id"], "quantity": 1}
    else:
        line_item = {
            "price_data": {
                "currency": pack["currency"].lower(),
                "unit_amount": int(pack["unit_amount"]),
                "product_data": {
                    "name": f"DELPHOI credits — {pack['pack_key']} "
                            f"({int(pack['credits'])} credits)",
                },
            },
            "quantity": 1,
        }
    return {
        "mode": "payment",
        "line_items": [line_item],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(user_id),
        "metadata": {
            "user_id": str(user_id),
            "origin": pack["origin"],
            "pack_key": pack["pack_key"],
            "credits": str(int(pack["credits"])),
        },
        "integration_identifier": _integration_id(),
    }


def create_checkout_session(get_db, user_id: str, origin: str, pack_key: str,
                            currency: str, success_url: str, cancel_url: str,
                            transport=None) -> dict:
    """Checkout Session a kredit-csomagra. STRIPE_SECRET_KEY env nélkül a
    pénz-út alszik (stripe_disabled). transport: teszt-injektálás (a REST-utat
    kényszeríti httpx.MockTransporttal)."""
    api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not api_key:
        return {"ok": False, "error": "stripe_disabled"}
    user_id = str(user_id or "").strip()
    if not user_id:
        return {"ok": False, "error": "missing_user_id"}
    if not success_url or not cancel_url:
        return {"ok": False, "error": "missing_urls"}
    pack = get_pack(get_db, origin, pack_key, currency)
    if not pack:
        return {"ok": False, "error": "unknown_pack"}
    params = build_checkout_params(pack, user_id, success_url, cancel_url)
    use_rest = transport is not None or os.environ.get("STRIPE_FORCE_REST") == "1"
    try:
        if not use_rest:
            try:
                session = _sdk_create_session(api_key, params)
            except ImportError:
                # NOTSTROM: nincs Stripe-SDK → nyers REST httpx-szel
                session = _rest_create_session(api_key, params)
        else:
            session = _rest_create_session(api_key, params, transport=transport)
    except Exception as e:  # noqa: BLE001 — kulcsot SOHA nem logolunk
        emsg = str(e)
        if "integration_identifier" in emsg:
            # Régebbi API-verzió nem ismeri a címkét → újra nélküle.
            params.pop("integration_identifier", None)
            try:
                session = (_rest_create_session(api_key, params, transport=transport)
                           if use_rest else _sdk_create_session(api_key, params))
            except Exception as e2:  # noqa: BLE001
                logger.error("stripe checkout create failed: %s", type(e2).__name__)
                return {"ok": False, "error": "stripe_error", "detail": str(e2)[:200]}
        else:
            logger.error("stripe checkout create failed: %s", type(e).__name__)
            return {"ok": False, "error": "stripe_error", "detail": emsg[:200]}
    return {"ok": True, "session_id": session.get("id", ""),
            "url": session.get("url", ""),
            "pack": {"origin": pack["origin"], "pack_key": pack["pack_key"],
                     "credits": int(pack["credits"]),
                     "currency": pack["currency"],
                     "unit_amount": int(pack["unit_amount"])}}


# ── Webhook: aláírás-ellenőrzés (Stripe v1 séma) + esemény-feldolgozás ─────

def sign_payload(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Stripe-Signature fejléc előállítása (teszt-szimuláció / stripe listen
    paritás): t=<ts>,v1=<HMAC-SHA256(secret, '<ts>.<payload>')>."""
    ts = int(timestamp if timestamp is not None else time.time())
    signed = f"{ts}.".encode() + payload
    mac = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify_signature(payload: bytes, sig_header: str, secret: str,
                     tolerance: int = 300, now: int | None = None) -> bool:
    """A Stripe webhook-aláírás v1 sémájának ellenőrzése (HMAC-SHA256 a
    '<t>.<payload>' felett, konstans-idejű összevetés, timestamp-tolerancia
    a replay-ablak ellen)."""
    if not sig_header or not secret:
        return False
    ts, candidates = None, []
    for part in sig_header.split(","):
        k, _, v = part.strip().partition("=")
        if k == "t":
            ts = v
        elif k == "v1":
            candidates.append(v)
    if not ts or not candidates:
        return False
    try:
        ts_int = int(ts)
    except ValueError:
        return False
    now_int = int(now if now is not None else time.time())
    if tolerance and abs(now_int - ts_int) > tolerance:
        return False
    signed = f"{ts_int}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, c) for c in candidates)


def _journal(conn, event_id: str, event_type: str, status: str,
             session_id: str = "", user_id: str = "", credits: int = 0,
             detail: str = "") -> None:
    """Esemény-napló (INSERT OR IGNORE — az első feldolgozás ténye marad)."""
    conn.execute(
        "INSERT OR IGNORE INTO delphoi_stripe_events "
        "(event_id, event_type, session_id, user_id, credits, status, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, event_type, session_id, user_id, int(credits), status,
         detail[:300], _now()))
    conn.commit()


def _credit_for_session(get_db, event_id: str, event_type: str, session: dict) -> dict:
    """Jóváírás a checkout-session metaadataiból. Idempotencia két őrrel:
    (1) ledger UNIQUE(user_id, reason='topup:stripe:<event_id>') — replay;
    (2) session-szintű 'credited' journal-őr — két KÜLÖNBÖZŐ event se
    írhasson jóvá ugyanarra a sessionre."""
    meta = session.get("metadata") or {}
    session_id = str(session.get("id") or "")
    user_id = str(meta.get("user_id") or session.get("client_reference_id") or "").strip()
    try:
        credits = int(meta.get("credits") or 0)
    except (TypeError, ValueError):
        credits = 0
    origin = str(meta.get("origin") or "").strip().lower()
    conn = get_db()
    try:
        ensure_stripe_tables(conn)
        if not user_id or credits <= 0:
            _journal(conn, event_id, event_type, "bad_metadata", session_id,
                     user_id, credits, "missing user_id/credits in metadata")
            logger.error("stripe webhook %s: bad metadata (session=%s)",
                         event_type, session_id)
            return {"ok": False, "error": "bad_metadata"}
        if origin not in ORIGIN_CURRENCIES:
            origin = resolve_origin(get_db, user_id)
        # (2) session-szintű őr
        prior = conn.execute(
            "SELECT event_id FROM delphoi_stripe_events "
            "WHERE session_id=? AND status='credited'", (session_id,)).fetchone()
        if prior and session_id:
            _journal(conn, event_id, event_type, "duplicate_session", session_id,
                     user_id, credits, f"already credited by {prior[0]}")
            return {"ok": True, "duplicate": True}
        # (1) ledger-idempotencia — a reason a Stripe EVENT-ID-t tartalmazza
        reason = f"topup:stripe:{event_id}"
        ts = _now()
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT OR IGNORE INTO delphoi_credit_ledger "
            "(user_id, delta, reason, origin, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, credits, reason, origin, ts))
        if not cur.rowcount:
            conn.rollback()
            _journal(conn, event_id, event_type, "duplicate", session_id,
                     user_id, credits, "ledger reason exists (replay)")
            return {"ok": True, "duplicate": True}
        conn.execute(
            "INSERT INTO delphoi_credits (user_id, balance, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?, updated_at = ?",
            (user_id, credits, ts, credits, ts))
        conn.commit()
        _journal(conn, event_id, event_type, "credited", session_id,
                 user_id, credits)
        logger.info("stripe credited: user=%s +%d credits (origin=%s, event=%s)",
                    user_id, credits, origin, event_id)
        return {"ok": True, "credited": credits, "user_id": user_id,
                "origin": origin, "reason": reason}
    finally:
        conn.close()


# Sikertelen fizetés / lejárt session — NAPLÓ, jóváírás NINCS.
_FAILURE_EVENTS = (
    "checkout.session.async_payment_failed",
    "checkout.session.expired",
    "payment_intent.payment_failed",
)

_CREDIT_EVENTS = (
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
)


def process_webhook(get_db, payload: bytes, sig_header: str, secret: str,
                    now: int | None = None) -> dict:
    """A webhook-végpont teljes logikája (a server.py route ezt hívja).
    Rossz aláírás → http_status 400, SEMMI nem íródik. Minden más ág 200
    (a Stripe ne retry-oljon olyan hibán, amin a retry nem segít)."""
    if not verify_signature(payload, sig_header, secret, now=now):
        return {"ok": False, "error": "bad_signature", "http_status": 400}
    try:
        event = json.loads(payload.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "bad_json", "http_status": 400}
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    obj = ((event.get("data") or {}).get("object")) or {}
    if not event_id or not event_type:
        return {"ok": False, "error": "bad_event", "http_status": 400}

    if event_type == "checkout.session.completed" and \
            str(obj.get("payment_status") or "") != "paid":
        # Async fizetési mód: a completed még 'unpaid' — a jóváírást az
        # async_payment_succeeded event hozza. Napló, jóváírás nincs.
        conn = get_db()
        try:
            ensure_stripe_tables(conn)
            _journal(conn, event_id, event_type, "unpaid_pending",
                     str(obj.get("id") or ""),
                     str((obj.get("metadata") or {}).get("user_id") or ""))
        finally:
            conn.close()
        return {"ok": True, "pending": True, "http_status": 200}

    if event_type in _CREDIT_EVENTS:
        res = _credit_for_session(get_db, event_id, event_type, obj)
        res["http_status"] = 200
        return res

    if event_type in _FAILURE_EVENTS:
        meta = obj.get("metadata") or {}
        conn = get_db()
        try:
            ensure_stripe_tables(conn)
            _journal(conn, event_id, event_type, "failed_logged",
                     str(obj.get("id") or ""), str(meta.get("user_id") or ""),
                     0, str((obj.get("last_payment_error") or {}).get("code") or ""))
        finally:
            conn.close()
        logger.warning("stripe payment failure logged: %s (event=%s) — NINCS jóváírás",
                       event_type, event_id)
        return {"ok": True, "logged": event_type, "http_status": 200}

    # Ismeretlen/nem kezelt event-típus: napló + 200 (ignore).
    conn = get_db()
    try:
        ensure_stripe_tables(conn)
        _journal(conn, event_id, event_type, "ignored", str(obj.get("id") or ""))
    finally:
        conn.close()
    return {"ok": True, "ignored": event_type, "http_status": 200}


def register_tools(app, deps):
    """Nem regisztrál MCP-toolt (tool-count fegyelem) — a pénz-út REST-rétege
    a server.py-ban él, ez a modul a logika-könyvtár."""
    logger.info("delphoi_stripe betöltve (SDK: %s, kulcs: %s, webhook-secret: %s)",
                _sdk_available(), bool(os.environ.get("STRIPE_SECRET_KEY")),
                bool(os.environ.get("STRIPE_WEBHOOK_SECRET")))


def _sdk_available() -> bool:
    try:
        import stripe  # noqa: F401
        return True
    except ImportError:
        return False
