"""test_delphoi_stripe.py — PYTHIA P4: PÉNZ-ÚT (Stripe test-mode).

Fedés:
  - webhook-aláírás (Stripe v1 séma): érvényes / rossz secret / manipulált
    payload / lejárt timestamp / hiányzó fejléc;
  - CÉL-BIZONYÍTÉK 3 lépésben: (a) szimulált checkout.session.completed
    helyes aláírással → jóváírás; (b) UGYANAZ az event replay → NINCS
    második jóváírás; (c) FG-levonás a jóváírt kreditből → egyenleg stimmel;
  - sikertelen fizetés útvonala: payment_failed → napló, NINCS jóváírás;
  - unpaid completed (async fizetési mód) → nincs jóváírás, pending-napló;
  - árlista-config (MOD2/A5): seed idempotens, upsert-validálás
    (origin-currency fegyelem, HUF-oszthatóság), delete;
  - checkout-paraméterek: metadata + price_data a config-sorból, konfigurált
    stripe_price_id előnye, payment_method_types SOHA;
  - REST NOTSTROM-út httpx.MockTransporttal (SDK-mentes ág bizonyítva);
  - ledger-origin rögzítés + resolve_origin a delphoi_users-ből.
"""
import json

import httpx
import pytest

from plugins import delphoi
from plugins import delphoi_stripe as ds

SECRET = "whsec_test_secret_kommandant"


@pytest.fixture
def stripe_db(get_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_FG_CACHE", "0")
    conn = get_db()
    delphoi.ensure_fg_tables(conn)
    ds.ensure_stripe_tables(conn)
    ds.seed_prices(conn)
    conn.close()
    return get_db


def _event(event_id="evt_test_001", etype="checkout.session.completed",
           session_id="cs_test_abc", user_id="user:42", credits=10,
           origin="echolot", payment_status="paid") -> bytes:
    return json.dumps({
        "id": event_id,
        "type": etype,
        "data": {"object": {
            "id": session_id,
            "object": "checkout.session",
            "payment_status": payment_status,
            "client_reference_id": user_id,
            "metadata": {"user_id": user_id, "origin": origin,
                         "pack_key": "starter", "credits": str(credits)},
        }},
    }).encode()


# ── Aláírás-ellenőrzés ──────────────────────────────────────────────────────

def test_signature_roundtrip():
    payload = _event()
    sig = ds.sign_payload(payload, SECRET)
    assert ds.verify_signature(payload, sig, SECRET) is True


def test_signature_wrong_secret_rejected():
    payload = _event()
    sig = ds.sign_payload(payload, "whsec_masik")
    assert ds.verify_signature(payload, sig, SECRET) is False


def test_signature_tampered_payload_rejected():
    payload = _event(credits=10)
    sig = ds.sign_payload(payload, SECRET)
    tampered = _event(credits=9999)
    assert ds.verify_signature(tampered, sig, SECRET) is False


def test_signature_expired_timestamp_rejected():
    payload = _event()
    sig = ds.sign_payload(payload, SECRET, timestamp=1000)
    assert ds.verify_signature(payload, sig, SECRET, now=1000 + 301) is False
    assert ds.verify_signature(payload, sig, SECRET, now=1000 + 299) is True


def test_signature_missing_or_malformed_header():
    payload = _event()
    assert ds.verify_signature(payload, "", SECRET) is False
    assert ds.verify_signature(payload, "t=abc,v1=zzz", SECRET) is False
    assert ds.verify_signature(payload, "nonsense", SECRET) is False


# ── CÉL-BIZONYÍTÉK: jóváírás → replay → FG-levonás ─────────────────────────

def test_goal_proof_credit_replay_charge(stripe_db):
    user = "user:42"
    payload = _event(user_id=user, credits=10)
    sig = ds.sign_payload(payload, SECRET)

    # (a) szimulált checkout.session.completed HELYES aláírással → jóváírás
    res = ds.process_webhook(stripe_db, payload, sig, SECRET)
    assert res["ok"] is True and res.get("credited") == 10
    assert res["reason"] == "topup:stripe:evt_test_001"     # event-id a reasonben
    assert delphoi.get_credits(stripe_db, user)["balance"] == 10

    # (b) UGYANAZ az event újra (replay) → NINCS második jóváírás
    res2 = ds.process_webhook(stripe_db, payload, sig, SECRET)
    assert res2["ok"] is True and res2.get("duplicate") is True
    c = delphoi.get_credits(stripe_db, user)
    assert c["balance"] == 10
    topups = [l for l in c["ledger"] if l["reason"].startswith("topup:stripe:")]
    assert len(topups) == 1

    # (c) FG-levonás a jóváírt kreditből → egyenleg stimmel
    assert delphoi.charge(stripe_db, user, "dlph-p4-job", 3) is True
    c2 = delphoi.get_credits(stripe_db, user)
    assert c2["balance"] == 7
    assert any(l["reason"] == "job:dlph-p4-job" and l["delta"] == -3
               for l in c2["ledger"])


def test_credit_records_origin_column(stripe_db):
    payload = _event(event_id="evt_orig_1", user_id="user:7", origin="saas",
                     session_id="cs_orig_1")
    sig = ds.sign_payload(payload, SECRET)
    res = ds.process_webhook(stripe_db, payload, sig, SECRET)
    assert res["ok"] and res["origin"] == "saas"
    conn = stripe_db()
    try:
        row = conn.execute(
            "SELECT origin FROM delphoi_credit_ledger WHERE reason=?",
            ("topup:stripe:evt_orig_1",)).fetchone()
    finally:
        conn.close()
    assert row["origin"] == "saas"


def test_session_level_guard_across_event_types(stripe_db):
    """Két KÜLÖNBÖZŐ event-id ugyanarra a sessionre → csak egy jóváírás."""
    p1 = _event(event_id="evt_a", session_id="cs_same")
    p2 = _event(event_id="evt_b", session_id="cs_same",
                etype="checkout.session.async_payment_succeeded")
    assert ds.process_webhook(stripe_db, p1, ds.sign_payload(p1, SECRET),
                              SECRET).get("credited") == 10
    res = ds.process_webhook(stripe_db, p2, ds.sign_payload(p2, SECRET), SECRET)
    assert res["ok"] is True and res.get("duplicate") is True
    assert delphoi.get_credits(stripe_db, "user:42")["balance"] == 10


def test_bad_signature_writes_nothing(stripe_db):
    payload = _event(event_id="evt_bad_sig")
    res = ds.process_webhook(stripe_db, payload, "t=1,v1=hamis", SECRET)
    assert res["ok"] is False and res["http_status"] == 400
    assert delphoi.get_credits(stripe_db, "user:42")["balance"] == 0
    conn = stripe_db()
    try:
        n = conn.execute("SELECT COUNT(*) FROM delphoi_stripe_events").fetchone()[0]
    finally:
        conn.close()
    assert n == 0


# ── Sikertelen fizetés útvonala — napló, NINCS jóváírás ────────────────────

def test_payment_failed_logged_no_credit(stripe_db):
    for etype, eid in (("payment_intent.payment_failed", "evt_pf_1"),
                       ("checkout.session.async_payment_failed", "evt_pf_2"),
                       ("checkout.session.expired", "evt_pf_3")):
        payload = _event(event_id=eid, etype=etype, session_id=f"cs_{eid}")
        res = ds.process_webhook(stripe_db, payload,
                                 ds.sign_payload(payload, SECRET), SECRET)
        assert res["ok"] is True and res.get("logged") == etype
    assert delphoi.get_credits(stripe_db, "user:42")["balance"] == 0
    conn = stripe_db()
    try:
        rows = conn.execute(
            "SELECT status FROM delphoi_stripe_events").fetchall()
    finally:
        conn.close()
    assert len(rows) == 3 and all(r["status"] == "failed_logged" for r in rows)


def test_unpaid_completed_no_credit(stripe_db):
    """Async fizetési mód: completed + payment_status='unpaid' → pending-napló,
    jóváírást majd az async_payment_succeeded hoz."""
    payload = _event(event_id="evt_unpaid", payment_status="unpaid")
    res = ds.process_webhook(stripe_db, payload,
                             ds.sign_payload(payload, SECRET), SECRET)
    assert res["ok"] is True and res.get("pending") is True
    assert delphoi.get_credits(stripe_db, "user:42")["balance"] == 0


def test_unknown_event_ignored(stripe_db):
    payload = _event(event_id="evt_other", etype="customer.created")
    res = ds.process_webhook(stripe_db, payload,
                             ds.sign_payload(payload, SECRET), SECRET)
    assert res["ok"] is True and res.get("ignored") == "customer.created"
    assert delphoi.get_credits(stripe_db, "user:42")["balance"] == 0


def test_bad_metadata_logged_not_credited(stripe_db):
    ev = json.loads(_event(event_id="evt_nometa"))
    ev["data"]["object"]["metadata"] = {}
    ev["data"]["object"]["client_reference_id"] = None
    payload = json.dumps(ev).encode()
    res = ds.process_webhook(stripe_db, payload,
                             ds.sign_payload(payload, SECRET), SECRET)
    assert res["ok"] is False and res["error"] == "bad_metadata"
    assert res["http_status"] == 200     # retry nem segítene — nincs retry-vihar


# ── Árlista-config (MOD2/A5) ────────────────────────────────────────────────

def test_seed_idempotent(stripe_db):
    conn = stripe_db()
    try:
        assert ds.seed_prices(conn) == 0        # a fixture már seedelt
        n = conn.execute("SELECT COUNT(*) FROM delphoi_price_config").fetchone()[0]
    finally:
        conn.close()
    assert n == len(ds.SEED_PACKS)


def test_list_prices_filters(stripe_db):
    all_rows = ds.list_prices(stripe_db)
    ech = ds.list_prices(stripe_db, origin="echolot")
    assert len(all_rows) == len(ds.SEED_PACKS)
    assert all(r["origin"] == "echolot" for r in ech)
    assert {r["currency"] for r in ech} == {"HUF", "EUR"}
    saas = ds.list_prices(stripe_db, origin="saas")
    assert {r["currency"] for r in saas} == {"USD", "EUR"}


def test_upsert_validation(stripe_db):
    base = {"origin": "echolot", "pack_key": "x", "credits": 5,
            "currency": "HUF", "unit_amount": 100000}
    assert ds.upsert_price(stripe_db, {**base, "origin": "tiktok"})["error"] == "bad_origin"
    assert ds.upsert_price(stripe_db, {**base, "currency": "USD"})["error"] == "bad_currency_for_origin"
    assert ds.upsert_price(stripe_db, {**base, "origin": "saas", "currency": "HUF"})["error"] == "bad_currency_for_origin"
    assert ds.upsert_price(stripe_db, {**base, "unit_amount": 100050})["error"] == "huf_not_divisible_by_100"
    assert ds.upsert_price(stripe_db, {**base, "credits": 0})["error"] == "bad_number"
    ok = ds.upsert_price(stripe_db, base)
    assert ok["ok"] and ok["price"]["pack_key"] == "x"


def test_upsert_updates_and_stores_stripe_ids(stripe_db):
    res = ds.upsert_price(stripe_db, {
        "origin": "echolot", "pack_key": "starter", "credits": 12,
        "currency": "HUF", "unit_amount": 599000,
        "stripe_product_id": "prod_TEST", "stripe_price_id": "price_TEST"})
    assert res["ok"]
    row = ds.get_pack(stripe_db, "echolot", "starter", "HUF")
    assert row["credits"] == 12 and row["unit_amount"] == 599000
    assert row["stripe_price_id"] == "price_TEST"
    # az upsert nem szaporított sort
    assert len(ds.list_prices(stripe_db)) == len(ds.SEED_PACKS)


def test_delete_price(stripe_db):
    row = ds.list_prices(stripe_db)[0]
    assert ds.delete_price(stripe_db, row["id"])["ok"] is True
    assert ds.delete_price(stripe_db, row["id"])["error"] == "not_found"
    assert len(ds.list_prices(stripe_db)) == len(ds.SEED_PACKS) - 1


# ── Checkout-paraméterek + REST NOTSTROM-út ────────────────────────────────

def test_build_checkout_params_price_data_and_metadata(stripe_db):
    pack = ds.get_pack(stripe_db, "echolot", "starter", "HUF")
    params = ds.build_checkout_params(pack, "user:42", "https://x/ok", "https://x/no")
    assert params["mode"] == "payment"
    assert "payment_method_types" not in params          # dinamikus fizetési módok
    li = params["line_items"][0]
    assert li["price_data"]["currency"] == "huf"
    assert li["price_data"]["unit_amount"] == pack["unit_amount"]
    m = params["metadata"]
    assert m["user_id"] == "user:42" and m["origin"] == "echolot"
    assert m["credits"] == str(pack["credits"])
    assert params["integration_identifier"].startswith("delphoi-topup-")


def test_build_checkout_params_prefers_configured_price_id(stripe_db):
    ds.upsert_price(stripe_db, {
        "origin": "saas", "pack_key": "starter", "credits": 10,
        "currency": "USD", "unit_amount": 1500, "stripe_price_id": "price_CFG"})
    pack = ds.get_pack(stripe_db, "saas", "starter", "USD")
    params = ds.build_checkout_params(pack, "sk_user", "https://x/ok", "https://x/no")
    li = params["line_items"][0]
    assert li == {"price": "price_CFG", "quantity": 1}


def test_create_checkout_session_rest_fallback(stripe_db, monkeypatch):
    """NOTSTROM-út: httpx.MockTransport — form-encoded mezők + auth ellenőrzés."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["form"] = dict(httpx.QueryParams(request.content.decode()))
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={
            "id": "cs_test_mock", "url": "https://checkout.stripe.com/c/pay/cs_test_mock"})

    res = ds.create_checkout_session(
        stripe_db, "user:42", "echolot", "starter", "HUF",
        "https://echolot.test/delphoi?topup=success",
        "https://echolot.test/delphoi?topup=cancel",
        transport=httpx.MockTransport(handler))
    assert res["ok"] and res["session_id"] == "cs_test_mock"
    assert res["url"].startswith("https://checkout.stripe.com/")
    assert seen["path"] == "/v1/checkout/sessions"
    f = seen["form"]
    assert f["mode"] == "payment"
    assert f["metadata[user_id]"] == "user:42"
    assert f["metadata[credits]"] == "10"
    assert f["line_items[0][price_data][currency]"] == "huf"
    assert f["line_items[0][price_data][unit_amount]"] == "499000"
    assert f["client_reference_id"] == "user:42"
    assert not any(k.startswith("payment_method_types") for k in f)
    assert seen["auth"].startswith("Basic ")            # a kulcs Basic-authban megy


def test_create_checkout_session_guards(stripe_db, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert ds.create_checkout_session(
        stripe_db, "user:1", "echolot", "starter", "HUF", "https://a", "https://b"
    )["error"] == "stripe_disabled"
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    assert ds.create_checkout_session(
        stripe_db, "user:1", "echolot", "nincs_ilyen", "HUF", "https://a", "https://b"
    )["error"] == "unknown_pack"
    assert ds.create_checkout_session(
        stripe_db, "user:1", "echolot", "starter", "USD", "https://a", "https://b"
    )["error"] == "unknown_pack"     # rossz currency az originre = nincs ilyen sor
    assert ds.create_checkout_session(
        stripe_db, "", "echolot", "starter", "HUF", "https://a", "https://b"
    )["error"] == "missing_user_id"
    assert ds.create_checkout_session(
        stripe_db, "user:1", "echolot", "starter", "HUF", "", ""
    )["error"] == "missing_urls"


def test_stripe_api_error_no_key_leak(stripe_db, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_titok_ne_szivarogj")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "No such price"}})

    res = ds.create_checkout_session(
        stripe_db, "user:1", "echolot", "starter", "HUF", "https://a", "https://b",
        transport=httpx.MockTransport(handler))
    assert res["ok"] is False and res["error"] == "stripe_error"
    assert "sk_test_titok" not in json.dumps(res)       # kulcs SOHA a válaszban


# ── resolve_origin (delphoi_users, A2) ─────────────────────────────────────

def test_resolve_origin_from_users_table(stripe_db):
    conn = stripe_db()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS delphoi_users ("
            " user_id INTEGER PRIMARY KEY,"
            " origin TEXT CHECK(origin IN ('echolot','saas')),"
            " external_id TEXT, email TEXT, created_at TEXT)")
        conn.execute(
            "INSERT INTO delphoi_users (origin, external_id, created_at) "
            "VALUES ('saas', 'sk_user_9', '2026-07-20T00:00:00+00:00')")
        conn.commit()
    finally:
        conn.close()
    assert ds.resolve_origin(stripe_db, "sk_user_9") == "saas"
    assert ds.resolve_origin(stripe_db, "user:ismeretlen") == "echolot"
