"""A Bridge az Echolot MCP-jét gépi kulccsal hívja (X-API-Key) — ha az Echolot bekapcsolja a kulcskaput
(MCP_REQUIRE_KEY), a Bridge emberi belépés (OAuth) nélkül, automatikusan bejut. Kulcs nélkül a fejléc sincs ott."""
import _echolot_client as ec


def test_mcp_headers_carry_the_key(monkeypatch):
    monkeypatch.setattr(ec, "ECHOLOT_API_KEY", "eck_proba_kulcs")
    h = ec._mcp_headers()
    assert h["X-API-Key"] == "eck_proba_kulcs"
    assert h["Accept"] == "application/json, text/event-stream" and h["Content-Type"] == "application/json"


def test_no_key_no_header(monkeypatch):
    monkeypatch.setattr(ec, "ECHOLOT_API_KEY", "")
    assert "X-API-Key" not in ec._mcp_headers()
