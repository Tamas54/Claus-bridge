"""A sub-agent Echolot MCP-proxyja (`echolot` tool).

MIÉRT LÉTEZIK (Kommandant, 2026-09-18): „A jövendő usereinknek egy működő
mcp-t ajánlunk. Nomarmost a Bridge agentjeinek is kell tudni használni
mcp-n keresztül." Eddig az al-ügynök (DeepSeek, Kimi, GLM) az Echolot 53
eszközéből EGYET látott (`echolot_query`), és az is REST-en ment, cím+lead
szinten. Mérve: a DeepSeek szó szerint azt válaszolta, hogy egyetlen
Echolot-eszköze van — és visszamondta a `echolot_query` leírásába kézzel
beírt, elavult „315 forrás / 63 sphere" számot is.

⛔ A PROXY ALAPÉRTELMEZÉSE A TILTÁS. Egy önállóan futó al-ügynök nem
publikálhat, nem küldhet levelet és nem törölhet; csak a kézzel
ellenőrzött OLVASÓ lista hívható.
"""
import asyncio
import json
import sys
import types


def _server(monkeypatch, *, enabled=True, hivasok=None):
    """A `server` modul, hamis `_echolot_client`-tel."""
    hivasok = hivasok if hivasok is not None else []

    hamis = types.ModuleType("_echolot_client")

    async def mcp_call(tool, arguments, timeout=None):
        hivasok.append((tool, arguments))
        return {"ok": True, "tool": tool}

    async def mcp_list_tools(timeout=None):
        return [("search_news", "Full-text press search."),
                ("search_corpus", "Transcript and Reddit search."),
                ("hog_publish", "PUBLISHES a permanent public page.")]

    hamis.mcp_call = mcp_call
    hamis.mcp_list_tools = mcp_list_tools
    monkeypatch.setitem(sys.modules, "_echolot_client", hamis)
    import server
    monkeypatch.setattr(server, "echolot_client", hamis, raising=False)
    monkeypatch.setattr(server, "ECHOLOT_ENABLED", enabled, raising=False)
    return server, hivasok


def _hiv(server, tool, args=None):
    # `asyncio.run` es nem `get_event_loop`: utobbi a mas tesztfajlok altal
    # lezart hurok utan RuntimeError-t dob (egyutt futtatva mert hiba).
    return asyncio.run(
        server._dispatch_subagent_tool("echolot",
                                       {"tool": tool, "args": args or {}}))


# ── A TOOL LÉTEZIK ÉS OTT VAN A KÉSZLETBEN ──────────────────────────────
def test_a_proxy_benne_van_a_subagent_keszletben(monkeypatch):
    server, _ = _server(monkeypatch)
    nevek = [t["function"]["name"] for t in server.SUBAGENT_TOOL_DEFS]
    assert "echolot" in nevek, nevek
    assert "echolot_query" in nevek, "a gyors cím-pásztázás maradjon meg"


def test_a_direktiva_kimondja(monkeypatch):
    server, _ = _server(monkeypatch)
    d = server.SUBAGENT_TOOLS_DIRECTIVE
    assert "`echolot`" in d
    assert "__list__" in d
    assert "search_corpus" in d


# ── OLVASÓ HÍVÁS ÁTMEGY ─────────────────────────────────────────────────
def test_olvaso_eszkoz_athalad(monkeypatch):
    server, hivasok = _server(monkeypatch)
    ki = json.loads(_hiv(server, "search_corpus", {"query": "Orbán"}))
    assert ki["ok"] is True
    assert hivasok == [("search_corpus", {"query": "Orbán"})]


def test_a_trend_social_aga_is_athalad(monkeypatch):
    """A Reddit/X kulcsszótrend a `get_trending` source= ágán él."""
    server, hivasok = _server(monkeypatch)
    _hiv(server, "get_trending", {"source": "reddit", "language": "hu"})
    assert hivasok[0][1]["source"] == "reddit"


# ── ÍRÓ HÍVÁS ELBUKIK, TIPIZÁLT OKKAL ───────────────────────────────────
def test_publikalo_eszkoz_nem_hivhato(monkeypatch):
    """Ez a teszt a lényeg: egy önállóan futó al-ügynök NE tehessen ki
    örökre nyilvános oldalt."""
    server, hivasok = _server(monkeypatch)
    for tiltott in ("hog_publish", "agora", "post_comment", "newsletter",
                    "press_pub_new", "press_pub_delete", "periskop_profile"):
        ki = json.loads(_hiv(server, tiltott, {}))
        assert ki.get("error") == "tool_not_allowed", (tiltott, ki)
    assert hivasok == [], "egyetlen tiltott hívás sem mehetett tovább"


def test_ismeretlen_nev_is_elbukik(monkeypatch):
    server, _ = _server(monkeypatch)
    ki = json.loads(_hiv(server, "nincs_ilyen_eszkoz", {}))
    assert ki.get("error") == "tool_not_allowed"


def test_az_engedelylistan_nincs_iro_eszkoz(monkeypatch):
    """A lista kézzel készült, mert a heurisztika megbukott: a
    docstring/törzs alapú vizsgálat a `press_pub_delete`-et TISZTA
    OLVASÓNAK mondta (a törlés egy importált helperben történik)."""
    server, _ = _server(monkeypatch)
    tilos = {"agora", "agora_editor", "post_comment", "hog_prepare",
             "hog_publish", "newsletter", "press_pub_new", "press_pub_set",
             "press_pub_delete", "periskop_profile", "periskop_report",
             "creator_publish", "creator_brief", "scrape_url"}
    atfedes = tilos & server.ECHOLOT_MCP_READ_TOOLS
    assert not atfedes, atfedes


# ── A KATALÓGUS ÉLŐ, ÉS SZŰR ────────────────────────────────────────────
def test_a_lista_csak_olvaso_eszkozoket_ad(monkeypatch):
    """A katalógus a SZERVERTŐL jön (nem tud elavulni), de a tiltott
    eszközöket nem mutatja meg — különben az al-ügynök nekifutna."""
    server, _ = _server(monkeypatch)
    ki = json.loads(_hiv(server, "__list__"))
    egyben = " ".join(ki["tools"])
    assert "search_news" in egyben and "search_corpus" in egyben
    assert "hog_publish" not in egyben
    assert ki["count"] == 2


# ── KIKAPCSOLT INTEGRÁCIÓ ───────────────────────────────────────────────
def test_echolot_url_nelkul_tiszta_hiba(monkeypatch):
    server, _ = _server(monkeypatch, enabled=False)
    ki = json.loads(_hiv(server, "search_news", {}))
    assert "disabled" in ki.get("error", "")
