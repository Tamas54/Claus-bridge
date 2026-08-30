"""
S-009 — KIFEJEZETT SZERV-VÁLASZTÁS A KERESÉSBEN.

A hiba: a Kommandant azt írta, hogy "ezt searxng-vel", és az utasításnak
NEM VOLT HOVA MENNIE — a láncon (brave-mcp → SearXNG → DDG → Brave API)
egyetlen `engine` paraméter sem létezett. A rendszer másik szervet használt,
és nem szólt. Ugyanaz a hibaosztály, mint az S-002 néma cronja.

Amit ezek a tesztek őriznek:
  1. a kifejezett motor-kérés ELŐRE kerül (nem a lánc-sorrend dönt),
  2. a csere KIMONDOTT — a kimenetben ott a substituted-sor,
  3. a cache nem mossa el a választást (a kulcsban benne a motor),
  4. a cache-találat MEGMONDJA, hogy régi — nem hazudik frissességet,
  5. az ismeretlen motor-név nem néma auto-ra esés,
  6. a `served_by` MINDIG ott van, ha bárki kiszolgált.

A hálózat a conftest-ben tiltva van; minden fok monkeypatch-elve.
"""

import asyncio

import pytest

import server


class _Calls(list):
    """Lista, amire rá lehet akasztani a mért kereteket."""
    budgets: dict = {}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_cache():
    server._WEB_SEARCH_CACHE.clear()
    yield
    server._WEB_SEARCH_CACHE.clear()


@pytest.fixture
def wired(monkeypatch):
    """Mindkét felső fok elérhető és NAPLÓZZA, hogy meghívták-e."""
    calls = _Calls()
    # A dublőrök FOGADJÁK és NAPLÓZZÁK az időkeretet: a `budgets` lista adja a
    # bizonyítékot, hogy a kör összesített kerete tényleg leér a fokokig, és
    # nem csak a `_web_search` fejében létezik.
    budgets = {}

    async def fake_brave(query, limit=5, timeout=None):
        calls.append("brave-mcp")
        budgets["brave-mcp"] = timeout
        return [{"title": "B", "url": "https://b.example/1", "description": "brave hit"}]

    async def fake_searx(query, limit=5, timeout=None):
        calls.append("searxng")
        budgets["searxng"] = timeout
        return [{"title": "S", "url": "https://s.example/1", "snippet": "searx hit"}]

    async def fake_pages(urls, limit=2, timeout=None):
        budgets["pages"] = timeout
        return []

    monkeypatch.setattr(server, "BRAVE_MCP_ENABLED", True)
    monkeypatch.setattr(server, "SEARXNG_ENABLED", True)
    monkeypatch.setattr(server, "_brave_mcp_search", fake_brave)
    monkeypatch.setattr(server, "_searxng_search", fake_searx)
    monkeypatch.setattr(server, "_fetch_page_contents", fake_pages)
    calls.budgets = budgets      # a teszt így fér hozzá a mért keretekhez
    return calls


# ── 1. Alias-feloldás ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("searxng", "searxng"),
    ("searx", "searxng"),
    ("sear", "searxng"),          # a Kommandant így mondja
    ("  SearXNG  ", "searxng"),
    ("brave-mcp", "brave-mcp"),
    ("brave", "brave-mcp"),
    ("duckduckgo", "ddg"),
    ("", "auto"),
    (None, "auto"),
])
def test_engine_aliases(raw, expected):
    engine, note = server._normalize_web_engine(raw)
    assert engine == expected
    assert note is None


def test_unknown_engine_is_not_silent():
    """Ismeretlen név → auto, DE megjelölve. A néma auto-ra esés ugyanaz a
    hiba, mint a néma motor-csere."""
    engine, note = server._normalize_web_engine("gugli")
    assert engine == "auto"
    assert note and "gugli" in note


# ── 2. A kifejezett kérés ELŐRE kerül ────────────────────────────────────

def test_explicit_searxng_runs_first_and_brave_is_not_called(wired):
    out = _run(server._web_search("q", engine="searxng"))
    assert wired == ["searxng"], "a brave-mcp-t meg sem lett volna szabad hívni"
    assert "_bridge_served_by: searxng" in out
    assert "_bridge_engine_substituted" not in out


def test_auto_runs_the_cheapest_organ_first(wired):
    """Kommandant-döntés 2026-08-30: az `auto` a LEGOLCSÓBB szervvel kezd.
    A brave-mcp Puppeteer-lap BRAVE_MAX_CONCURRENCY=2 mögött — az a makacs,
    anti-bot védett oldalaké, nem minden keresésé."""
    out = _run(server._web_search("q"))
    assert wired == ["searxng"], "az auto-lánc nem a legolcsóbb szervvel kezdett"
    assert "_bridge_served_by: searxng" in out
    assert "_bridge_engine_substituted" not in out, "a lánc-sorrend nem csere"


def test_explicit_brave_mcp_runs_first(wired):
    """A kifejezett kérés felülírja a lánc-sorrendet — a SearXNG az auto-ban
    elöl van, de itt meg sem szabad hívni."""
    out = _run(server._web_search("q", engine="brave-mcp"))
    assert wired == ["brave-mcp"], "a kifejezett brave-mcp kérés nem írta felül a sorrendet"
    assert "_bridge_served_by: brave-mcp" in out
    assert "_bridge_engine_substituted" not in out


# ── 3. A csere KIMONDOTT ─────────────────────────────────────────────────

def test_substitution_is_spoken_not_silent(monkeypatch, wired):
    """A kért SearXNG üresen jön vissza → a lánc továbbmegy, DE a kimenet
    megmondja, hogy cserélt, és MIÉRT."""
    async def empty_searx(query, limit=5, timeout=None):
        wired.append("searxng")
        return []

    monkeypatch.setattr(server, "_searxng_search", empty_searx)

    out = _run(server._web_search("q", engine="searxng"))
    assert wired == ["searxng", "brave-mcp"], "előbb a kért motor, aztán a csere"
    assert "_bridge_served_by: brave-mcp" in out
    assert "_bridge_engine_substituted" in out
    assert "searxng" in out and "brave-mcp" in out
    assert "MONDD KI" in out


def test_substitution_names_the_reason(monkeypatch, wired):
    """A csere oka TIPIZÁLT, nem üres. A 'nincs konfigurálva' és a 'nem
    válaszolt' két külön üzemzavar — a Kommandantnak látnia kell, melyik."""
    monkeypatch.setattr(server, "SEARXNG_ENABLED", False)
    out = _run(server._web_search("q", engine="searxng"))
    assert "_bridge_engine_substituted" in out
    assert "nincs konfigurálva" in out


def test_no_substitution_notice_in_auto_mode(wired):
    """Auto-módban a lánc-lépés NEM csere — nem volt kérés, amit felülírt
    volna. A zaj itt éppolyan káros, mint máshol a csend."""
    out = _run(server._web_search("q"))
    assert "_bridge_engine_substituted" not in out


# ── 4. A cache nem mossa el a választást ─────────────────────────────────

def test_cache_key_separates_engines():
    server._web_search_cache_put("q", "SEARX BODY", "searxng", {"served_by": "searxng"})
    assert server._web_search_cache_get("q", "auto") is None, \
        "egy auto-válasz NEM szolgálhat ki egy kifejezett searxng-kérést"
    got = server._web_search_cache_get("q", "searxng")
    assert got is not None and got[0] == "SEARX BODY"


def test_explicit_request_does_not_read_auto_cache(wired):
    _run(server._web_search("q"))                       # auto → brave-mcp, cache-el
    wired.clear()
    out = _run(server._web_search("q", engine="searxng"))
    assert wired == ["searxng"], "a searxng-kérés nem eshet az auto cache-be"
    assert "_bridge_served_by: searxng" in out


# ── 5. A cache-találat megmondja, hogy régi ──────────────────────────────

def test_cache_hit_declares_its_age(wired):
    first = _run(server._web_search("q"))
    assert "_bridge_cache" not in first
    second = _run(server._web_search("q"))
    assert wired == ["searxng"], "a második kör már nem hívta a motort"
    assert "_bridge_cache: HIT" in second
    assert second.count("_bridge_fetched_at") == 1, \
        "a cache-elt válasz nem hordozhat két időbélyeget"


# ── 6. Kudarc: nincs hazug served_by ─────────────────────────────────────

def test_total_failure_claims_no_engine(monkeypatch):
    async def empty(*a, **kw):
        return []

    monkeypatch.setattr(server, "BRAVE_MCP_ENABLED", False)
    monkeypatch.setattr(server, "SEARXNG_ENABLED", False)
    monkeypatch.setattr(server, "_searxng_search", empty)
    monkeypatch.setattr(server, "_fetch_page_contents", empty)

    async def dead_ddg(*a, **kw):
        raise RuntimeError("no network")

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", dead_ddg)

    out = _run(server._web_search("q", engine="searxng"))
    assert "_bridge_served_by" not in out, "ha senki nem szolgált ki, ne állítsuk, hogy igen"
    assert "Nincs találat" in out
    assert "nincs konfigurálva" in out, "a kudarc-üzenet sorolja fel, mi bukott el"


# ── 7. A stamp-funnel ────────────────────────────────────────────────────

def test_stamp_without_served_by_is_unchanged_shape():
    """A `_stamp_fetched_at` sok más toolon is fut — a régi alak nem törhet el."""
    out = server._stamp_fetched_at("body")
    assert out.startswith("body")
    assert "_bridge_fetched_at" in out
    assert "_bridge_served_by" not in out


def test_stamp_served_by_carries_hits_and_ms():
    out = server._stamp_fetched_at("body", served_by="searxng", hits=27, ms=3067)
    assert "_bridge_served_by: searxng · 27 találat · 3067 ms" in out


# ── 8. A tool-felület tényleg kínálja a választást ───────────────────────

def test_subagent_tool_def_exposes_engine():
    props = server.WEB_SEARCH_TOOL_DEF["function"]["parameters"]["properties"]
    assert "engine" in props, "a sub-agent nem tud kérni, amit nem lát"
    assert set(props["engine"]["enum"]) == set(server._WEB_SEARCH_ENGINES)


def test_dispatch_passes_engine_through(monkeypatch):
    seen = {}

    async def spy(query, engine="auto"):
        seen["engine"] = engine
        return "ok"

    monkeypatch.setattr(server, "_web_search", spy)
    _run(server._dispatch_subagent_tool("web_search", {"query": "q", "engine": "searxng"}))
    assert seen["engine"] == "searxng"


def test_legacy_marker_rescues_the_engine():
    """A rosszul formázott tool-hívásból a query-t eddig is kimentettük.
    Ha a motort nem mentjük vele, a néma csere a hátsó ajtón visszajön."""
    content = 'blah {"query": "phocas", "engine": "searxng"} blah'
    assert server._legacy_engine_hint(content) == "searxng"
    assert server._legacy_engine_hint('{"query": "x"}') == "auto"


# ── 9. A FELDWEBEL (Telegram) ugyanazt kapja ─────────────────────────────
# A Feldwebel a `server._web_search`-öt hívja lazy importtal, tehát a láncot,
# a served_by-t és a cache-jelölést automatikusan örökli. Amit NEM örököl
# magától, az a tool-DEFINÍCIÓ és a dispatch — ha az `engine` ott hiányzik,
# a Kommandant "ezt searxng-vel" utasításának megint nincs hova mennie.

def _feldwebel_tool(name):
    from feldwebel import responder
    for t in responder.TOOLS:
        if t.get("function", {}).get("name") == name:
            return t["function"]
    raise AssertionError(f"nincs ilyen Feldwebel-tool: {name}")


def test_feldwebel_web_search_offers_engine():
    props = _feldwebel_tool("web_search")["parameters"]["properties"]
    assert "engine" in props, "a Feldwebel nem tud motort kérni, amit nem lát"
    assert set(props["engine"]["enum"]) == set(server._WEB_SEARCH_ENGINES)


def test_feldwebel_dispatch_passes_engine(monkeypatch):
    from feldwebel import responder
    seen = {}

    async def spy(query, engine="auto"):
        seen["query"], seen["engine"] = query, engine
        return "ok"

    monkeypatch.setattr(server, "_web_search", spy)
    out = _run(responder._execute_tool(
        "web_search", {"query": "phocas", "engine": "searxng"}, None))
    assert out == "ok"
    assert seen == {"query": "phocas", "engine": "searxng"}


def test_feldwebel_defaults_to_auto(monkeypatch):
    from feldwebel import responder
    seen = {}

    async def spy(query, engine="auto"):
        seen["engine"] = engine
        return "ok"

    monkeypatch.setattr(server, "_web_search", spy)
    _run(responder._execute_tool("web_search", {"query": "q"}, None))
    assert seen["engine"] == "auto", "motor-kérés nélkül a lánc dönt"


def test_feldwebel_prompt_orders_it_to_name_the_engine():
    """A paraméter önmagában kevés: a botnak KI IS KELL MONDANIA, ki szolgált
    ki. Enélkül a csere technikailag látszik, a Kommandant felé mégis néma."""
    from feldwebel import responder
    src = open(responder.__file__, encoding="utf-8").read()
    assert "_bridge_served_by" in src, "a rendszerprompt nem hivatkozik a served_by-ra"
    assert "Néma csere TILOS" in src


# ── 10. AZ ÖSSZESÍTETT IDŐKERET ─────────────────────────────────────────
# A fokok eddig egyenként hordták a saját timeoutjukat, fölöttük SEMMIVEL:
# 60 s brave-mcp + 20 s SearXNG + 2x10 s oldal + 15 s DDG (+4 s retry) + 15 s
# Brave API = ~150 s worst case EGY keresésre, és a hívó ebből semmit nem lát.

def test_budget_reaches_the_tiers(wired):
    """A keret nem a `_web_search` fejében él, hanem LEÉR a fokokig."""
    _run(server._web_search("q"))
    assert wired.budgets["searxng"] is not None, "a SearXNG-fok nem kapott keretet"
    assert wired.budgets["searxng"] <= 20.0, "a fok a saját plafonja fölé mehetne"
    assert wired.budgets["pages"] is not None, "a mélytartalom-letöltés keret nélkül ment"


def test_tier_is_skipped_when_the_budget_is_gone(monkeypatch, wired):
    """Elfogyott kerettel a fokot NEM indítjuk el — és ezt ki is mondjuk.
    Egy fok, ami úgyis timeoutolna, csak a maradékot égeti el az alatta lévő
    elől, ami még hozhatna valamit."""
    monkeypatch.setattr(server, "_WEB_SEARCH_TOTAL_BUDGET_S", 0.0)
    out = _run(server._web_search("q"))
    assert wired == [], f"nulla kerettel is elindult egy fok: {list(wired)}"
    assert "elfogyott az időkeret" in out
    assert "_bridge_served_by" not in out, "senki nem szolgált ki — ne állítsuk, hogy igen"


def test_budget_is_configurable_and_sane():
    assert server._WEB_SEARCH_TOTAL_BUDGET_S > server._WEB_SEARCH_MIN_TIER_S
    assert server._WEB_SEARCH_MIN_TIER_S > 0
