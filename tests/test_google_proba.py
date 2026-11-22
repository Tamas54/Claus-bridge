"""A Google-próba (önvizsgálat) a VALÓDI okot méri — Faber #2, 2026-11-22.

A hamis lelet: „TARTÓS: a(z) `google` MOST IS hibás — Első ok: nincs szolgáltatás: 2026-11-22T16:08:00.757499+00:00”.
A régi próba a `gmail_last_error`-ból olvasta az okot, de abban a recipe_health liveness-IDŐBÉLYEGE van. Ezért az
`invalid_grant`-ot (lejárt belépés) sosem ismerte fel, és az önjavítás az emberi teendőből Faber-kérelmet írt.

Amit ezek a tesztek őriznek:
  1. a bukott Google-indítás OKA eljut a próbáig, és az `invalid_grant` → emberi teendő (Telegram-mondat);
  2. a próba okként sosem egy puszta időbélyeget mond;
  3. az élő kliensre a TÉNYLEGES hívások nyoma dönt (utolsó hívás bukott → piros; sikeres → zöld);
  4. beállítatlan Google-token nem hiba, hanem „nincs beállítva” (ok=None).
Hálózat nincs: a token kitalált, a Google-frissítés álhibát dob.
"""
from __future__ import annotations

import json

import pytest

import onjavitas
import selfdiag
import server

KULCSOK = ("gmail_service", "calendar_service", "gmail_last_ok", "gmail_last_error", "gmail_last_error_msg",
           "calendar_last_ok", "calendar_last_error", "calendar_last_error_msg")

#: Kitalált, lejárt token — csak a google-auth formátuma miatt kell; hálózatra nem megy ki.
KAMU_TOKEN = json.dumps({"token": "kamu", "refresh_token": "kamu-r", "client_id": "kamu.apps",
                         "client_secret": "kamu-s", "expiry": "2020-01-01T00:00:00Z"})


@pytest.fixture(autouse=True)
def _tiszta_allapot(monkeypatch):
    for k in KULCSOK:
        monkeypatch.setitem(server._capture_state, k, None)
    # Saját próba-regiszter: a server importkori regisztrációját nem rontjuk el más teszteknek.
    monkeypatch.setattr(selfdiag, "_PROBES", {})
    monkeypatch.setattr(selfdiag, "_KNOWN_COMPONENTS", set())
    selfdiag.register_probe("google", "gmail", server._probe_google(
        "gmail", "gmail_service", "gmail_last_ok", "gmail_last_error", "gmail_last_error_msg"))
    selfdiag.register_probe("google", "calendar", server._probe_google(
        "calendar", "calendar_service", "calendar_last_ok", "calendar_last_error", "calendar_last_error_msg"))


def _proba(nev: str) -> selfdiag.ProbeResult:
    return selfdiag._PROBES["google"][nev]()


def test_lejart_belepes_emberi_teendo_lesz_nem_faber_kerelem(monkeypatch):
    """A 11-22-i eset végig: a token frissítése invalid_grant-tal bukik → a próba kimondja → Telegram-mondat."""
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    def _bukik(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.",
                           {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})

    monkeypatch.setattr(server, "GOOGLE_TOKEN_JSON", KAMU_TOKEN)
    monkeypatch.setattr(Credentials, "refresh", _bukik)
    assert server._init_google_services() is False
    assert "invalid_grant" in server._capture_state["gmail_last_error_msg"]

    d = selfdiag.diagnose("google").as_dict()
    assert d["verdict"] == "persistent"
    for e in d["evidence"]:
        assert e["ok"] is False and "invalid_grant" in e["detail"], e
    assert "kamu" not in json.dumps(d), "a token (vagy része) kiszivárgott a leletbe"

    tipus, szoveg = onjavitas.osztalyoz("google", d)
    assert tipus == "ember", "a lejárt Google-belépés emberi teendő, nem Faber-kérelem"
    assert "Google-belépés" in szoveg


def test_az_ok_sosem_egy_puszta_idobelyeg(monkeypatch):
    """A régi hiba újrajátszva: csak a liveness-időbélyeg van meg, ok nincs — a próba ezt nem adhatja el oknak."""
    monkeypatch.setattr(server, "GOOGLE_TOKEN_JSON", KAMU_TOKEN)
    server._capture_state["gmail_last_error"] = "2026-11-22T16:08:00.757499+00:00"
    r = _proba("gmail")
    assert r.ok is False
    assert "2026-11-22T16:08" not in r.detail
    assert "nincs feljegyzett ok" in r.detail


def test_mas_inditasi_hiba_okkal_megy_tovabb_a_fabernek(monkeypatch):
    """Ami nem belépési hiba (pl. hálózat), az kódos/konfigos ügy marad — de a valódi okkal."""
    monkeypatch.setattr(server, "GOOGLE_TOKEN_JSON", KAMU_TOKEN)
    for k in ("gmail_last_error_msg", "calendar_last_error_msg"):
        server._capture_state[k] = "TransportError: Connection reset by peer"
    d = selfdiag.diagnose("google").as_dict()
    assert d["verdict"] == "persistent"
    assert "Connection reset" in d["summary"]
    tipus, szoveg = onjavitas.osztalyoz("google", d)
    assert tipus == "kod" and "Connection reset" in szoveg


def test_elo_kliens_a_hivasok_nyoma_dont():
    """A kliens puszta létezése nem bizonyíték: ha az utolsó hívás bukott, a szerv piros."""
    allapot = server._capture_state
    allapot["gmail_service"] = object()
    allapot["gmail_last_ok"] = "2026-11-22T10:00:00+00:00"
    allapot["gmail_last_error"] = "2026-11-22T12:00:00+00:00"
    allapot["gmail_last_error_msg"] = "HttpError: 401 Unauthorized"
    r = _proba("gmail")
    assert r.ok is False and "401" in r.detail

    allapot["gmail_last_ok"] = "2026-11-22T13:00:00+00:00"   # azóta sikerült → él (a régi ok nem számít)
    r = _proba("gmail")
    assert r.ok is True and "13:00" in r.detail

    allapot["gmail_last_ok"] = allapot["gmail_last_error"] = None   # még nem hívtuk → nem tudjuk, nem hazudunk
    assert _proba("gmail").ok is None


def test_elo_kliens_lejart_tokennel_is_emberi_teendo():
    """Ha a token futás közben jár le, a lekérdezés bukik invalid_grant-tal — ugyanaz az emberi út."""
    for p in ("gmail", "calendar"):
        server._capture_state[f"{p}_service"] = object()
        server._capture_state[f"{p}_last_ok"] = "2026-11-21T08:00:00+00:00"
        server._capture_state[f"{p}_last_error"] = "2026-11-22T08:00:00+00:00"
        server._capture_state[f"{p}_last_error_msg"] = "RefreshError: ('invalid_grant: Token has been expired or revoked.')"
    d = selfdiag.diagnose("google").as_dict()
    assert d["verdict"] == "persistent"
    assert onjavitas.osztalyoz("google", d)[0] == "ember"


def test_beallitatlan_token_nem_hiba(monkeypatch):
    """Google-token nélkül a Gmail/Naptár ki van kapcsolva — ez nem tartós hiba, nem indít önjavítást."""
    monkeypatch.setattr(server, "GOOGLE_TOKEN_JSON", "")
    d = selfdiag.diagnose("google")
    assert d.verdict is selfdiag.Verdict.UNKNOWN
    assert all(e.ok is None and "nincs beállítva" in e.detail for e in d.evidence)


def test_a_lekerdezes_hibaja_is_feljegyzi_az_okot(monkeypatch):
    """A capture_gmail_poll / capture_calendar_poll bukásakor az ok is a próbához ér, nem csak az időbélyeg."""
    import asyncio

    class _Halott:
        def __getattr__(self, _nev):
            raise RuntimeError("halott kliens")

    monkeypatch.setattr(server, "_enforce", lambda *a, **k: None)
    server._capture_state["gmail_service"] = _Halott()
    server._capture_state["calendar_service"] = _Halott()
    asyncio.run(server.capture_gmail_poll())
    asyncio.run(server.capture_calendar_poll())
    assert "halott kliens" in server._capture_state["gmail_last_error_msg"]
    assert "halott kliens" in server._capture_state["calendar_last_error_msg"]
    assert server._capture_state["gmail_last_error"] and server._capture_state["calendar_last_error"]
