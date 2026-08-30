"""
LIGHTPANDA — a fetch-lánc 2. foka (JS-render → markdown).

MIÉRT VAN KAPU, ÉS MIÉRT AZONNAL
--------------------------------
A Lightpanda a BLOKKOLT oldalt is sikerként jelenti. Mérve 2026-08-30:
a reuters.com DataDome mögött ÜRES stringet adott vissza `isError: false`-szal
és HTTP 200-zal. Nincs MCP-megfelelője a CLI `--fail-on-http-error`-jának, és
HTTP-státuszt egyáltalán nem ad vissza — tehát a hívónak a TÖRZSET kell
megítélnie. A 200 nem bizonyíték.

Rosszabb: hideg szerveren, GYORS hálón 5 egyidejű kérésből 3 jött vissza üresen,
ugyanígy `isError: false`-szal. Minél gyorsabb a hálózat, annál valószínűbb.

Amit ezek a tesztek őriznek:
  1. kikapcsolt Lightpanda = NULLA viselkedés-változás (a fok opt-in),
  2. a kapu: üres / rövid / cookie-fal / isError → degradálás, nem szemét,
  3. a session MINDIG lezárul (35 MB/session, semmi nem takarítja),
  4. a GAZDAGABB eredmény nyer, nem a későbbi,
  5. a Tier1 bukása LÁTHATÓ marad (nem logger.debug),
  6. a fok sose dob — egy letöltő-réteg nem ölhet meg egy keresési kört.
"""

import asyncio

import pytest

import server


def _run(coro):
    return asyncio.run(coro)


# ── Egy hamis httpx.AsyncClient, ami naplózza a hívásokat ────────────────

class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class FakeClient:
    """A `server` modulba injektált httpx-helyettes. Rögzíti a POST-okat és a
    DELETE-eket, hogy a session-higiénia MÉRHETŐ legyen, ne remélt."""

    log: list = []
    response: FakeResponse = FakeResponse()
    raise_on_post: Exception | None = None

    def __init__(self, *a, **kw):
        self.timeout = kw.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        FakeClient.log.append(("POST", url, (headers or {}).get("Mcp-Session-Id"),
                              (headers or {}).get("Content-Type")))
        if FakeClient.raise_on_post:
            raise FakeClient.raise_on_post
        return FakeClient.response

    async def request(self, method, url, headers=None):
        FakeClient.log.append((method, url, (headers or {}).get("Mcp-Session-Id"), None))
        return FakeResponse(202)


@pytest.fixture
def lp(monkeypatch):
    import httpx
    FakeClient.log = []
    FakeClient.raise_on_post = None
    FakeClient.response = FakeResponse()
    monkeypatch.setattr(server, "LIGHTPANDA_ENABLED", True)
    monkeypatch.setattr(server, "LIGHTPANDA_URL", "http://lp.railway.internal:8080/mcp")
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return FakeClient


def _ok(text):
    return FakeResponse(200, {"jsonrpc": "2.0", "id": 1,
                              "result": {"content": [{"type": "text", "text": text}],
                                         "isError": False}})


ARTICLE = "Ez egy valódi cikkszöveg. " * 60      # ~1500 karakter


# ── 1. Kikapcsolva = nulla változás ──────────────────────────────────────

def test_disabled_returns_none_without_touching_the_network(monkeypatch):
    import httpx
    monkeypatch.setattr(server, "LIGHTPANDA_ENABLED", False)

    def explode(*a, **kw):
        raise AssertionError("kikapcsolt Lightpanda NEM nyithat kapcsolatot")

    monkeypatch.setattr(httpx, "AsyncClient", explode)
    assert _run(server._lightpanda_markdown("https://x.example/")) is None


# ── 2. A KAPU ────────────────────────────────────────────────────────────

def test_good_page_passes(lp):
    lp.response = _ok(ARTICLE)
    out = _run(server._lightpanda_markdown("https://index.hu/cikk"))
    assert out and out.startswith("Ez egy valódi cikkszöveg")


def test_empty_body_with_iserror_false_is_rejected(lp):
    """A reuters-eset: 0 karakter, isError:false, HTTP 200. Ez NEM siker."""
    lp.response = _ok("")
    assert _run(server._lightpanda_markdown("https://www.reuters.com/")) is None


def test_short_body_is_rejected(lp):
    """A hideg-indítású versenyhelyzet ugyanígy néz ki: rövid/üres törzs."""
    lp.response = _ok("x" * 499)
    assert _run(server._lightpanda_markdown("https://x.example/")) is None


def test_consent_wall_is_rejected(lp):
    """A 444.hu 48 karakteres cookie-fala isError:false-szal jött. Egy hosszabb
    consent-szöveg is fal, nem cikk."""
    wall = ("Az Ön adatainak védelme fontos a számunkra. TOVÁBBI LEHETŐSÉGEK. "
            "Elfogadom a sütiket. ") * 12          # >500, de <2000 és consent
    assert 500 < len(wall) < 2000
    lp.response = _ok(wall)
    assert _run(server._lightpanda_markdown("https://444.hu/")) is None


def test_long_article_mentioning_cookies_is_kept(lp):
    """A consent-szűrő NEM dobhat ki egy valódi, hosszú cikket, ami történetesen
    a sütikről szól — különben a kapu maga lesz a hibafelület."""
    article = "A cookie-szabályozás új szakaszába lépett az Európai Unió. " * 60
    assert len(article) > 2000
    lp.response = _ok(article)
    assert _run(server._lightpanda_markdown("https://x.example/")) is not None


def test_iserror_true_is_rejected(lp):
    lp.response = FakeResponse(200, {"result": {
        "content": [{"text": "navigation failed: CouldntResolveHost"}], "isError": True}})
    assert _run(server._lightpanda_markdown("https://nope.invalid/")) is None


def test_rpc_error_is_rejected(lp):
    lp.response = FakeResponse(200, {"error": {"code": -32601, "message": "no such tool"}})
    assert _run(server._lightpanda_markdown("https://x.example/")) is None


def test_non_200_is_rejected(lp):
    lp.response = FakeResponse(415, None, "unsupported media type")
    assert _run(server._lightpanda_markdown("https://x.example/")) is None


def test_exception_never_escapes(lp):
    """Egy letöltő-fok nem ölhet meg egy keresési kört."""
    lp.raise_on_post = RuntimeError("connection reset")
    assert _run(server._lightpanda_markdown("https://x.example/")) is None


# ── 3. Session-higiénia ──────────────────────────────────────────────────

def test_session_is_closed_on_success(lp):
    lp.response = _ok(ARTICLE)
    _run(server._lightpanda_markdown("https://index.hu/"))
    methods = [m for m, *_ in lp.log]
    assert methods == ["POST", "DELETE"], f"nem záródott a session: {methods}"
    post_sid, delete_sid = lp.log[0][2], lp.log[1][2]
    assert post_sid and post_sid == delete_sid, "más sessiont zártunk le, mint amit nyitottunk"


def test_session_is_closed_even_when_the_call_fails(lp):
    """35 MB/session — a szivárgás nem függhet attól, sikerült-e a letöltés."""
    lp.raise_on_post = RuntimeError("boom")
    _run(server._lightpanda_markdown("https://x.example/"))
    assert "DELETE" in [m for m, *_ in lp.log], "bukás után bennmaradt a session"


def test_each_call_gets_its_own_session(lp):
    lp.response = _ok(ARTICLE)
    _run(server._lightpanda_markdown("https://a.example/"))
    _run(server._lightpanda_markdown("https://b.example/"))
    sids = {sid for m, _u, sid, _c in lp.log if m == "POST"}
    assert len(sids) == 2, "két hívás ugyanazt a sessiont használta"


def test_content_type_is_sent(lp):
    """Content-Type nélkül a szerver 415-öt ad — mérve."""
    lp.response = _ok(ARTICLE)
    _run(server._lightpanda_markdown("https://x.example/"))
    assert lp.log[0][3] == "application/json"


# ── 4. A gazdagabb eredmény nyer ─────────────────────────────────────────

def test_lightpanda_wins_when_it_returns_more(monkeypatch):
    monkeypatch.setattr(server, "LIGHTPANDA_ENABLED", True)

    async def thin_get(urls, limit=2):
        raise AssertionError("nem ezt kellene hívni")

    async def fake_lp(url, max_bytes=20000, timeout=None):
        return "LP" + "y" * 3000

    monkeypatch.setattr(server, "_lightpanda_markdown", fake_lp)

    import httpx

    class ShellClient(FakeClient):
        async def get(self, url, headers=None):
            return FakeResponse(200, None, '<html><div id="__next"></div></html>')

    monkeypatch.setattr(httpx, "AsyncClient", ShellClient)
    out = _run(server._fetch_page_contents(["https://spa.example/cikk"]))
    assert out and "via lightpanda" in out[0]


def test_plain_get_wins_when_lightpanda_returns_less(monkeypatch):
    """A fallback NEM ronthat: ha a Lightpanda kevesebbet hoz, mint a sima GET,
    a sima GET marad. Enélkül a fok néma minőségromlás lenne."""
    monkeypatch.setattr(server, "LIGHTPANDA_ENABLED", True)

    async def fake_lp(url, max_bytes=20000, timeout=None):
        return "rövidebb"

    monkeypatch.setattr(server, "_lightpanda_markdown", fake_lp)

    import httpx
    body = "<html><div id='__next'></div>" + ("valódi bekezdés " * 100) + "</html>"

    class RichClient(FakeClient):
        async def get(self, url, headers=None):
            return FakeResponse(200, None, body)

    monkeypatch.setattr(httpx, "AsyncClient", RichClient)
    out = _run(server._fetch_page_contents(["https://x.example/"]))
    assert out and "via http" in out[0]


def test_lightpanda_not_called_for_a_healthy_static_page(monkeypatch):
    """A drága fok csak akkor induljon, ha az olcsó tényleg elhasalt."""
    monkeypatch.setattr(server, "LIGHTPANDA_ENABLED", True)
    called = []

    async def fake_lp(url, max_bytes=20000, timeout=None):
        called.append(url)
        return "x" * 5000

    monkeypatch.setattr(server, "_lightpanda_markdown", fake_lp)

    import httpx
    body = "<html><article>" + ("rendes cikkszöveg " * 100) + "</article></html>"

    class StaticClient(FakeClient):
        async def get(self, url, headers=None):
            return FakeResponse(200, None, body)

    monkeypatch.setattr(httpx, "AsyncClient", StaticClient)
    out = _run(server._fetch_page_contents(["https://static.example/"]))
    assert not called, "statikus oldalon fölöslegesen indult a JS-fok"
    assert out and "via http" in out[0]


# ── 5. A JS-váz felismerése ──────────────────────────────────────────────

@pytest.mark.parametrize("text,raw,expected", [
    ("", "", True),                                        # semmi
    ("x" * 100, "<html></html>", True),                    # túl kevés próza
    ("x" * 900, '<div id="root"></div>', True),            # React-váz
    ("x" * 900, "<script>__NEXT_DATA__={}</script>", True),
    ("x" * 900, "<article>rendes</article>", False),       # valódi cikk
])
def test_js_shell_detection(text, raw, expected):
    assert server._looks_js_rendered(text, raw) is expected


# ── 6. Sorosítás ─────────────────────────────────────────────────────────

def test_serialised_by_design():
    """A böngésző-munka egyetlen worker-szálon fut a Lightpandában (V8 isolate
    thread-affin). Az egyidejűség nulla nyereség ÉS aktív kockázat: hideg
    szerveren 5-ből 3 egyidejű kérés üresen jött vissza."""
    assert server._LIGHTPANDA_SEMAPHORE._value == 1
