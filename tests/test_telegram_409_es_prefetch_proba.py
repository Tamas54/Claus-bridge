"""
TELEGRAM 409 + PREFETCH-PRÓBA (2026-11-20)
-----------------------------------------
1. A `python server.py` `__main__` néven fut; a `youngereka_chat` és a `feldwebel/responder` lusta
   `from server import …`-ja MÁSODSZOR is lefuttatta a modult → második Telegram-lekérdező ugyanazzal a
   tokennel → végtelen 409 Conflict, a bot süket lett. Javítás: a futó modult `server` néven is bejegyezzük,
   és egy folyamatban csak egy `telegram-poll` szál indulhat.
2. Az önvizsgálat prefetch-próbája `new_event_loop().run_until_complete`-tel futott, és az MCP
   `self_diagnose` futó hurokból hívta → „Cannot run the event loop while another loop is running”
   (a mérőeszköz hibája volt, nem a briefé). Javítás: külön szál, saját hurokkal.
"""
import asyncio
import re
import threading
import time
from pathlib import Path

import server

SERVER_PY = Path(__file__).resolve().parent.parent / "server.py"


def test_a_futo_modul_server_neven_is_be_van_jegyezve_minden_import_elott():
    forras = SERVER_PY.read_text(encoding="utf-8")
    bejegyzes = forras.index('_sys_boot.modules.setdefault("server", _sys_boot.modules[__name__])')
    elso_import = re.search(r"^import (?!sys as _sys_boot)", forras, re.M).start()
    assert bejegyzes < elso_import, "a bejegyzésnek minden más import előtt kell lennie"


def test_egy_folyamatban_csak_egy_telegram_lekerdezo_indul(monkeypatch):
    allj = threading.Event()

    async def _alszik():
        while not allj.is_set():
            await asyncio.sleep(0.05)

    monkeypatch.setattr(server, "TELEGRAM_BOT_TOKEN", "proba-token")
    monkeypatch.setattr(server, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(server, "_telegram_poll_loop", _alszik)
    try:
        server._start_telegram_polling()
        server._start_telegram_polling()      # a második indítás nem hozhat létre új szálat
        time.sleep(0.2)
        szalak = [t for t in threading.enumerate() if t.name == "telegram-poll" and t.is_alive()]
        assert len(szalak) == 1
    finally:
        allj.set()
        for t in threading.enumerate():
            if t.name == "telegram-poll":
                t.join(timeout=2)


def test_a_prefetch_proba_futo_esemenyhurokbol_is_hivhato(monkeypatch):
    from plugins import _recipe_prefetch as pf

    async def _szekciok(recipe):
        await asyncio.sleep(0)
        return {"hirek": (True, "proba: 3 hír")}

    monkeypatch.setattr(pf, "probe_sections", _szekciok)
    proba = server._probe_prefetch("daily_news_brief")

    async def _mcp_hivas_szimulacio():       # az MCP self_diagnose futó hurokból hívja a próbát
        return proba()

    eredmeny = asyncio.run(_mcp_hivas_szimulacio())
    assert eredmeny.ok is True, eredmeny.detail
    assert "Cannot run the event loop" not in (eredmeny.detail or "")
