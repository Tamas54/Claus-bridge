"""Tests for the sandbox guards in conftest.py.

The guards stop the suite writing to a real database, leaking real
credentials, or calling live services. A guard that has quietly stopped
working looks exactly like a guard that was never needed: the suite passes
either way. These tests make the difference visible, so the safety net is
itself held up by something.
"""

import os
import socket

import pytest


def test_database_is_redirected_away_from_any_real_file():
    """BRIDGE_DB_PATH must point into the throwaway sandbox, never at the
    repo's bridge.db or the production volume (/data/bridge.db)."""
    db = os.environ.get("BRIDGE_DB_PATH", "")
    assert "claus-bridge-tests-" in db, (
        "BRIDGE_DB_PATH is %r — the sandbox redirect in conftest.py is not in "
        "effect, so importing pyramid.memory_shared will CREATE TABLE in a "
        "real database." % db
    )
    assert not db.startswith("/data/"), "pointing at the production volume"


@pytest.mark.parametrize("name", ["SILICONFLOW_API_KEY", "STRIPE_SECRET_KEY",
                                  "TELEGRAM_BOT_TOKEN", "GOOGLE_TOKEN_JSON"])
def test_live_credentials_are_scrubbed_from_the_environment(name):
    assert os.environ.get(name) is None, (
        "%s survived into the test environment; a test could pass by using a "
        "real credential and then behave differently in CI." % name
    )


def test_an_unmarked_test_cannot_open_an_outbound_connection():
    with pytest.raises(RuntimeError, match="Blocked outbound network"):
        socket.create_connection(("example.com", 80), timeout=5)


def test_loopback_still_works_so_local_test_servers_are_unaffected():
    """The guard must block the internet, not all sockets — several tests
    stand up local HTTP servers."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.create_connection(server.getsockname(), timeout=5)
        client.close()
    finally:
        server.close()


@pytest.mark.network
def test_the_network_marker_lifts_the_guard():
    """Proves the opt-out is real, without making a real call: inside a
    network-marked test the guard must not be installed."""
    assert socket.socket.connect.__name__ != "guarded_connect"
