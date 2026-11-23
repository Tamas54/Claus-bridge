"""A bot-token nem kerülhet a naplóba (2026-11-23): a httpx INFO-sora a Telegram-URL-lel együtt kiírta.

Futtatás: ../../../.venv/bin/python -m pytest -q tests/test_naplo_nem_szivarogtat_tokent.py"""
import logging


def test_httpx_keresnaplo_nem_info_szintu():
    import server  # noqa: F401 — az import állítja be a naplózást
    for nev in ("httpx", "httpcore"):
        assert logging.getLogger(nev).getEffectiveLevel() >= logging.WARNING, nev


def test_a_telegram_url_nem_jelenik_meg_info_szinten(caplog):
    import server  # noqa: F401
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info('HTTP Request: GET https://api.telegram.org/bot123:TITOK/getUpdates "200 OK"')
    assert "TITOK" not in caplog.text
