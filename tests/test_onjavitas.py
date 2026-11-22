"""ÖNJAVÍTÁS (onjavitas.py) + a selfdiag „EGÉSZSÉGES” ítélete.

1. Kódhiba → Faber-kérelem a `bridge` műhelynek, a leletből írt kívánsággal; emberi teendő → egy Telegram-mondat.
2. Ugyanarra a leletre 24 órán belül nem szól újra (a jelzés a shared_memory-ben, újraindulás után is).
3. Az egészséges és az átmeneti szerv nem vált ki semmit.
4. A selfdiag tünet nélkül EGÉSZSÉGES-t mond, tünettel ÁTMENETI-t.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

import onjavitas as O
import selfdiag
from server import memoria_ir


def _run(c):
    return asyncio.run(c)


@pytest.fixture
def get_db(tmp_path):
    p = tmp_path / "o.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE shared_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, value TEXT NOT "
              "NULL, category TEXT DEFAULT 'general', tags TEXT DEFAULT '', updated_by TEXT NOT NULL, created_at TEXT "
              "NOT NULL, updated_at TEXT NOT NULL)")
    c.execute("CREATE UNIQUE INDEX idx_k ON shared_memory(key)")
    c.commit()
    c.close()
    return lambda: sqlite3.connect(p)


def _diag(**komponensek):
    return {"components": komponensek}


SEARX_BUKOTT = {"verdict": "persistent", "summary": "TARTÓS: a(z) `searxng` MOST IS hibás — 1/1 próba bukott.",
                "evidence": [{"probe": "json_api", "ok": False, "detail": "HTTP 403: Forbidden"}]}
GOOGLE_BUKOTT = {"verdict": "persistent", "summary": "TARTÓS: a(z) `google` MOST IS hibás",
                 "evidence": [{"probe": "gmail", "ok": False, "detail": "invalid_grant — a refresh token VISSZAVONVA"}]}


def _hivasok():
    faber, tg = [], []

    def faber_kerelem(k):
        faber.append(k)
        return 1

    async def telegram(s):
        tg.append(s)
    return faber, tg, faber_kerelem, telegram


def test_kodhiba_fabernek_emberi_teendo_telegramra(get_db):
    faber, tg, fk, t = _hivasok()
    out = _run(O.kor(_diag(searxng=SEARX_BUKOTT, google=GOOGLE_BUKOTT,
                           echolot={"verdict": "healthy", "summary": "EGÉSZSÉGES"},
                           siliconflow={"verdict": "transient", "summary": "ÁTMENETI"}),
                     get_db=get_db, faber_kerelem=fk, telegram=t, memoria_ir=memoria_ir, most=1000.0))
    assert out == {"faber": ["searxng"], "ember": ["google"], "kihagyva": []}
    assert len(faber) == 1 and "`searxng`" in faber[0] and "HTTP 403" in faber[0] and "próbát" in faber[0]
    assert any("Gmail és a Naptár" in x and "Publish app" in x for x in tg)
    assert any("átadtam a Fabernek" in x for x in tg)


def test_24_oran_belul_nem_ismetel_utana_igen(get_db):
    faber, tg, fk, t = _hivasok()
    d = _diag(searxng=SEARX_BUKOTT)
    _run(O.kor(d, get_db=get_db, faber_kerelem=fk, telegram=t, memoria_ir=memoria_ir, most=1000.0))
    out = _run(O.kor(d, get_db=get_db, faber_kerelem=fk, telegram=t, memoria_ir=memoria_ir, most=1000.0 + 3600))
    assert out["kihagyva"] == ["searxng"] and len(faber) == 1
    out = _run(O.kor(d, get_db=get_db, faber_kerelem=fk, telegram=t, memoria_ir=memoria_ir, most=1000.0 + O.ISMETLES_TILTAS_S + 1))
    assert out["faber"] == ["searxng"] and len(faber) == 2


def test_sikertelen_faber_kerelem_nem_jegyzodik_meg(get_db):
    tg = []

    async def telegram(s):
        tg.append(s)
    out = _run(O.kor(_diag(searxng=SEARX_BUKOTT), get_db=get_db, faber_kerelem=lambda k: None, telegram=telegram,
                     memoria_ir=memoria_ir, most=1000.0))
    assert out["faber"] == [] and tg == []
    assert not O._mar_jelezve(get_db, "searxng_kod", 1001.0)       # a következő kör újra próbálja


def test_siliconflow_402_emberi():
    tipus, szoveg = O.osztalyoz("siliconflow", {"verdict": "persistent", "evidence": [
        {"probe": "kimi", "ok": False, "detail": "payment: Sorry, your account balance is insufficient"}]})
    assert tipus == "ember" and "tölts fel" in szoveg


def test_selfdiag_egeszseges_tunet_nelkul_atmeneti_tunettel():
    selfdiag.clear_probes()
    try:
        selfdiag.register_probe("proba", "a", lambda: selfdiag.ProbeResult("a", True, "ok"))
        assert selfdiag.diagnose("proba").verdict == selfdiag.Verdict.HEALTHY
        assert selfdiag.diagnose("proba").summary.startswith("EGÉSZSÉGES")
        assert selfdiag.diagnose("proba", "timeout").verdict == selfdiag.Verdict.TRANSIENT
        osszes = selfdiag.diagnose_all()
        assert "1 zöld" in osszes["summary"]
    finally:
        selfdiag.clear_probes()


def test_a_cron_az_onjavito_kort_hivja():
    forras = open("server.py", encoding="utf-8").read()
    assert "await onjavitas.kor(_diag, get_db=get_db, memoria_ir=memoria_ir" in forras and "_faber_kerelem_onjavitasbol" in forras
    assert 'selfdiag.register_probe("searxng", "json_api", _probe_searxng)' in forras
    assert 'selfdiag.register_probe("brave_mcp", "endpoint", _probe_brave_mcp)' in forras
