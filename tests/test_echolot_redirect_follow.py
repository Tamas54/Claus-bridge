"""Az Echolot-kliens redirect-kezelése — a 2026-07-12-i Agora-némulás tesztje.

MI TÖRTÉNT: a `get_top_story_links` httpx-kliense `follow_redirects` NÉLKÜL
épült, az Echolot `/` útvonala viszont 2026-07 óta 301-gyel megy `/hu/`-ra
(`_lang_path_middleware`). A régi `if resp.status_code >= 400` kapu a 301-et
SIKERNEK vette, így a story-regex a 17 bájtos „Moved Permanently" törzsön
futott: 0 találat, üres lista, semmi hiba. Az Agora-sorszolgálat ettől
kezdve MINDEN körben `no_stories`-szal állt le — némán, ~7 hétig.

Élesben mérve (2026-08-29):
    GET /     → 301, 17 bájt,   0 story-találat
    GET /hu/  → 200, 195 KB,   31 story-találat (ugyanazzal a regexszel)

A tesztek ezért KÉT dolgot szögeznek le:
  1. a főoldal-lekérés KÖVETI a redirectet (ha valaki kiveszi a
     `follow_redirects=True`-t, ez a fájl PIROS lesz);
  2. ha egy 3xx MÉGIS átjön oda, ahol nem követünk redirectet, az
     NEVESÍTETT, hangos hiba (`EcholotRedirectError`) — nem üres eredmény.
     Házszabály: a néma siker a legrosszabb hiba.
"""
import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import _echolot_client as ec  # noqa: E402

BASE = "https://echolot.test"

HOMEPAGE_HTML = """<!doctype html><html lang="hu"><body>
<a href="/story/aaa111/elso-sztori-slug">Első</a>
<a href="/story/bbb222/masodik-sztori-slug">Második</a>
<a href="/story/aaa111/elso-sztori-slug">Első (duplikátum)</a>
<a href="/story/ccc333/harmadik-sztori-slug">Harmadik</a>
</body></html>"""

STORY_MD = """# Dunai fenékküszöb

**Nyelvek**: hu, en
**Hírrégió**: `hu_press`
**Domináns keret**: infrastruktura
**Források**: 7
**Időszak**: 2026-08-28 06:00 → 2026-08-29 18:30
"""

# A prod-útvonaltábla mása: minden nyelvfüggetlen oldal 301-gyel megy a
# nyelvi prefixre, és CSAK a prefixelt változat ad 200-at.
_ROUTES = {
    "/": (301, "Moved Permanently", {"location": "/hu/"}),
    "/hu/": (200, HOMEPAGE_HTML, {}),
    "/story/aaa111/elso-sztori-slug":
        (301, "Moved Permanently",
         {"location": "/hu/story/aaa111/elso-sztori-slug"}),
    "/hu/story/aaa111/elso-sztori-slug": (200, STORY_MD, {}),
}


class _FakeResponse:
    def __init__(self, status_code: int, text: str, headers: dict):
        self.status_code = status_code
        self.text = text
        self.headers = headers
        self.content = text.encode("utf-8")

    def json(self):
        import json
        return json.loads(self.text)


class _FakeAsyncClient:
    """httpx.AsyncClient-mása, VALÓDI redirect-szemantikával.

    Csak akkor követi a 3xx-et, ha `follow_redirects=True` — pontosan úgy,
    ahogy a httpx. Ezért ha a fix visszasérül (kiveszik a kwargot), ez a
    fake a 301-et adja vissza, és a tesztek pirosak lesznek.
    """

    #: minden példányosítás kwargjai — a tesztek ebből olvassák ki, hogy
    #: a hívó KÉRTE-e a redirect-követést
    inits: list = []
    #: ha True, a fake SZÁNDÉKOSAN nem követ redirectet (regresszió-szimuláció)
    ignore_follow: bool = False

    def __init__(self, timeout=None, follow_redirects=False, **kw):
        type(self).inits.append({"timeout": timeout,
                                 "follow_redirects": bool(follow_redirects)})
        self.follow_redirects = (bool(follow_redirects)
                                 and not type(self).ignore_follow)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _resolve(self, url: str) -> _FakeResponse:
        path = url[len(BASE):] if url.startswith(BASE) else url
        for _ in range(20):  # httpx alap max_redirects
            status, body, headers = _ROUTES.get(
                path, (404, "not found", {}))
            if 300 <= status < 400 and self.follow_redirects:
                path = headers["location"]
                continue
            return _FakeResponse(status, body, headers)
        raise httpx.TooManyRedirects("too many redirects")

    async def get(self, url, params=None, headers=None):
        return self._resolve(url)

    async def post(self, url, json=None, headers=None):
        return self._resolve(url)


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    """Minden teszt saját, tiszta fake-kliensen és ECHOLOT_URL-en fut."""
    _FakeAsyncClient.inits = []
    _FakeAsyncClient.ignore_follow = False
    ec._inflight.clear()          # a request-coalescing ne szivárogjon át
    monkeypatch.setattr(ec, "ECHOLOT_URL", BASE)
    monkeypatch.setattr(ec.httpx, "AsyncClient", _FakeAsyncClient)
    yield
    ec._inflight.clear()


# ---------------------------------------------------------------------------
# 1) A FŐOLDAL-LEKÉRÉS KÖVETI A REDIRECTET  (a 2026-07-12-i bug)
# ---------------------------------------------------------------------------
def test_top_story_links_kikeri_a_redirect_kovetest():
    """A kliens EXPLICITEN `follow_redirects=True`-vel épül.

    Ez a legpontosabb szög: ha valaki kiveszi a kwargot, ez a teszt bukik
    akkor is, ha a fake-szerver történetesen elnézőbb lenne.
    """
    asyncio.run(ec.get_top_story_links(limit=12))
    assert _FakeAsyncClient.inits, "nem épült httpx-kliens"
    assert all(i["follow_redirects"] for i in _FakeAsyncClient.inits), (
        "a főoldal-lekérés follow_redirects NÉLKÜL épített klienst — "
        "pontosan ez okozta a 7 hetes Agora-némulást")


def test_top_story_links_a_redirect_mogul_szedi_ki_a_sztorikat():
    """A `/` 301 → `/hu/` 200 láncon a story-linkek megvannak, sorrendben,
    duplikátum nélkül — a hiba előtti (és utáni) elvárt viselkedés."""
    links = asyncio.run(ec.get_top_story_links(limit=12))
    assert [l["story_id"] for l in links] == ["aaa111", "bbb222", "ccc333"], (
        f"a 301 mögötti főoldalról nem jöttek meg a sztorik: {links}")
    assert links[0]["url"] == f"{BASE}/story/aaa111/elso-sztori-slug"


def test_top_story_links_limit_tartva():
    links = asyncio.run(ec.get_top_story_links(limit=2))
    assert len(links) == 2


def test_story_markdown_is_koveti_a_redirectet():
    """A testvér-hívás (ez volt eleve helyes) továbbra is átmegy a 301-en."""
    meta = asyncio.run(ec.get_story_markdown("aaa111", "elso-sztori-slug"))
    assert meta["title"] == "Dunai fenékküszöb"
    assert meta["languages"] == ["hu", "en"]
    assert meta["sphere"] == "hu_press"
    assert meta["sources_count"] == 7


# ---------------------------------------------------------------------------
# 2) A NEM KÖVETETT 3xx HANGOS ÉS NEVESÍTETT  (a `>= 400` kapu hibája)
# ---------------------------------------------------------------------------
def test_nem_kovetett_redirect_nevesitett_hiba_nem_ures_lista():
    """REGRESSZIÓ-SZIMULÁCIÓ: a redirect NEM követődik (mintha a fix
    visszasérült volna, vagy egy proxy nyelné el a láncot).

    A régi kód ilyenkor ÜRES LISTÁT adott, hibaüzenet nélkül. Az elvárás:
    nevesített, hangos kivétel — a hívó duty-ciklus így `no_stories` helyett
    valódi hibát naplóz."""
    _FakeAsyncClient.ignore_follow = True
    with pytest.raises(ec.EcholotRedirectError) as exc:
        asyncio.run(ec.get_top_story_links(limit=12))
    msg = str(exc.value)
    assert "301" in msg and "/hu/" in msg, msg
    assert "homepage" in msg, msg


def test_a_redirect_hiba_echolot_hiba_is():
    """A hívók `except EcholotError`-ral fognak — a nevesítés nem törhet
    meg meglévő hibakezelést."""
    assert issubclass(ec.EcholotRedirectError, ec.EcholotError)


def test_json_api_redirectje_nevesitett_hiba():
    """A `/api/*` hívások SZÁNDÉKOSAN nem követnek redirectet (ott a 3xx
    konfigurációs hiba) — de akkor is hangosan kell szólniuk."""
    _FakeAsyncClient.ignore_follow = True
    monkey_routes = dict(_ROUTES)
    monkey_routes["/api/news"] = (302, "Found", {"location": "/hu/api/news"})
    _ROUTES.update(monkey_routes)
    try:
        with pytest.raises(ec.EcholotRedirectError) as exc:
            asyncio.run(ec.fetch_news(days=1, limit=5))
        assert "api" in str(exc.value) and "302" in str(exc.value)
    finally:
        _ROUTES.pop("/api/news", None)


def test_mcp_post_redirectje_nevesitett_hiba():
    """POST-nál a 301 ELDOBNÁ a törzset — itt sosem szabad követni, viszont
    a hívó nem kaphat csendes „üres" választ."""
    _FakeAsyncClient.ignore_follow = True
    _ROUTES["/mcp"] = (307, "Temporary Redirect", {"location": "/hu/mcp"})
    try:
        with pytest.raises(ec.EcholotRedirectError) as exc:
            asyncio.run(ec.mcp_call("agora", {"action": "feed"}))
        assert "MCP" in str(exc.value) and "307" in str(exc.value)
    finally:
        _ROUTES.pop("/mcp", None)


def test_register_post_redirectje_nevesitett_hiba():
    _FakeAsyncClient.ignore_follow = True
    _ROUTES["/operators/register"] = (
        301, "Moved Permanently", {"location": "/hu/operators/register"})
    try:
        with pytest.raises(ec.EcholotRedirectError) as exc:
            asyncio.run(ec.register_operator("Teszt", "t@example.com"))
        assert "register" in str(exc.value)
    finally:
        _ROUTES.pop("/operators/register", None)


def test_a_4xx_uzenet_valtozatlan():
    """A 4xx-ág üzenetformátuma NEM változott (a nevesítés csak a 3xx-et
    érinti) — a meglévő hívók/logok illesztései maradnak."""
    _ROUTES["/api/search"] = (503, "upstream down", {})
    try:
        with pytest.raises(ec.EcholotError) as exc:
            asyncio.run(ec.search_news("teszt"))
        assert not isinstance(exc.value, ec.EcholotRedirectError)
        assert "HTTP 503" in str(exc.value)
    finally:
        _ROUTES.pop("/api/search", None)
