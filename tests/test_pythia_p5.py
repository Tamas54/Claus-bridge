"""test_pythia_p5.py — PYTHIA P5: kirakat-polír (MOD2/A6) + G4 entitás-seed.

A6 — a KÉT publikus delphoi adat-végpont (nowcast + verify):
  - CORS-nyitott (Access-Control-Allow-Origin: *, GET+OPTIONS) — forrás-szintű
    ellenőrzés a server.py delphoi-régióján (a teljes szerver-import nehéz);
  - kulcs-kapu (_delphoi_auth) NINCS rajtuk;
  - a feed-payload BRAND-SEMLEGES: a disclaimer a delphoi_brands közös,
    semleges szövege, Echolot-szó a payloadban nincs;
  - chain_head a feedben (a P5 irányfal verify-badge adatforrása).

G4 — entitás-seed: fr/it-novekedesi-hangulat REGISZTRÁLVA enabled=0-val;
pl-inflacios-varakozas feltételes-jelölt, marad 0. FLIP NINCS.
"""
import json
import os
import re

import pytest

from plugins import delphoi
from plugins import delphoi_brands

_SERVER_SRC = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "server.py"), encoding="utf-8").read()


@pytest.fixture
def feed_db(get_db):
    conn = get_db()
    delphoi.ensure_tables(conn)
    delphoi.seed_registry(conn)
    conn.close()
    return get_db


# ── A6: brand-semleges feed ────────────────────────────────────────────────

def test_feed_disclaimer_is_brand_neutral(feed_db):
    feed = delphoi.public_nowcast_feed(feed_db)
    assert feed["disclaimer"] == delphoi_brands.public_disclaimer()
    dump = json.dumps(feed, ensure_ascii=False).lower()
    assert "echolot" not in dump          # arc-specifikus szöveg TILOS
    assert "aipolling" not in dump
    assert "nem közvélemény-kutatás" in feed["disclaimer"]  # terminológia-vasszabály


def test_neutral_disclaimer_has_no_brand_reference():
    d = delphoi_brands.public_disclaimer().lower()
    for brand in ("echolot", "aipolling"):
        assert brand not in d


def test_feed_carries_chain_head(feed_db):
    # üres lánc → GENESIS; sorok után a verify-fej
    feed = delphoi.public_nowcast_feed(feed_db)
    assert feed["chain_head"] == delphoi.GENESIS
    delphoi.append_ledger_row(feed_db, "Q124488292", "HU", "2026-W31",
                              -0.2, "ch", "tencent/Hy3|non-think")
    feed2 = delphoi.public_nowcast_feed(feed_db)
    rep = delphoi.verify_ledger_chain(feed_db)
    assert rep["ok"] is True
    assert feed2["chain_head"] == rep["head"]


# ── A6: server.py huzalozás (forrás-szintű bizonyíték) ─────────────────────

def _handler_block(name: str) -> str:
    m = re.search(rf"async def {name}\(request\):(.*?)\n(?:@|def |# ──)",
                  _SERVER_SRC, re.S)
    assert m, f"nincs {name} handler a server.py-ban"
    return m.group(1)


def test_public_endpoints_cors_open():
    assert '"Access-Control-Allow-Origin": "*"' in _SERVER_SRC
    for route, methods in (("/api/delphoi/nowcast", '["GET", "OPTIONS"]'),
                           ("/api/delphoi/verify", '["GET", "OPTIONS"]')):
        assert f'@mcp.custom_route("{route}", methods={methods})' in _SERVER_SRC, \
            f"{route}: GET+OPTIONS kell"
    for handler in ("api_delphoi_nowcast", "api_delphoi_verify"):
        block = _handler_block(handler)
        assert "_DELPHOI_PUBLIC_CORS" in block, f"{handler}: CORS-fejlécek hiányoznak"


def test_public_endpoints_have_no_key_gate():
    """A nowcast/verify PUBLIKUS adat-API — a _delphoi_auth kulcs-kapu NEM
    élhet rajtuk (a P0-térkép szerint mindkettő nyitott REST)."""
    for handler in ("api_delphoi_nowcast", "api_delphoi_verify"):
        block = _handler_block(handler)
        assert "_delphoi_auth" not in block, f"{handler}: kulcs-kapu került a publikus útra!"


def test_private_endpoints_still_gated():
    """Kontraszt-őr: a jobs/credits/briefs/report utak kulcs-kapuja megmaradt."""
    for handler in ("api_delphoi_jobs", "api_delphoi_credits",
                    "api_delphoi_briefs", "api_delphoi_report"):
        block = _handler_block(handler)
        assert "_delphoi_auth" in block, f"{handler}: eltűnt a kulcs-kapu!"


# ── G4: entitás-seed (regisztrálva enabled=0, flip nincs) ──────────────────

def test_g4_country_entities_registered_disabled(feed_db):
    conn = feed_db()
    rows = {r["entity_key"]: dict(r) for r in conn.execute(
        "SELECT entity_key, country, entity_type, enabled FROM delphoi_entity_nowcast")}
    conn.close()
    for key, country in (("fr-novekedesi-hangulat", "FR"),
                         ("it-novekedesi-hangulat", "IT"),
                         ("pl-inflacios-varakozas", "PL")):
        assert key in rows, f"{key}: nincs regisztrálva"
        assert rows[key]["country"] == country
        assert rows[key]["entity_type"] == "sentiment_expectation"
        assert not rows[key]["enabled"], f"{key}: enabled=0 kell (flip csak Kommandant-szóra)"


def test_enabled_set_unchanged(feed_db):
    """FLIP NINCS: az engedélyezett kör pontosan az első menet 8 entitása."""
    conn = feed_db()
    enabled = {r["entity_key"] for r in conn.execute(
        "SELECT entity_key FROM delphoi_entity_nowcast WHERE enabled=1")}
    conn.close()
    assert enabled == {"Q124488292", "tisza-part", "Q387006", "Q948",
                       "Q3052772", "Q451791", "hu-inflacios-varakozas",
                       "hu-novekedesi-hangulat"}
