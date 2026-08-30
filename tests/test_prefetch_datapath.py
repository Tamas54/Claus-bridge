"""
ADAT-ÚT PRÓBA — "erre már le kellett volna csapnia az önjavító eszköznek".

A KOMMANDANT LELETE (2026-08-30)
--------------------------------
A `daily_news_brief` prefetchere a `hirmagnet.hu/api/trending` végpontot hívta
egy `HIRMAGNET_API_KEY` fejléccel. Az a kulcs a produkcióban NINCS beállítva,
tehát a függvény már az ELSŐ SORÁN `[]`-vel tért vissza. Mindig. Az Echolot
adatai SOHA nem voltak benne a napi hírbriefben.

És az önjavítás nem csaphatott le rá, mert NEM TÖRTÉNT HIBA — csak nem történt
adat. Egy üres lista nem kivétel, nem hibakód, nem 400-as státusz. Csak üres.

    AZ ADAT HIÁNYA NEM HIBA, AMÍG VALAKI KI NEM MONDJA,
    HOGY OTT KELLENE LENNIE.

Ezért deklarálják a receptek a KÖTELEZŐ szekcióikat, és ezért azt méri a próba,
hogy van-e bennük tartalom — nem azt, hogy a függvény lefutott-e.

Amit ezek a tesztek őriznek:
  1. az ÜRES kötelező szekció PIROS (ez a régi hiba, visszajátszva),
  2. a hibajelölt (`_error`) szekció is PIROS, az okával együtt,
  3. a nem deklarált recept nem hazudik zöldet — "nincs mit mérni"-t mond,
  4. a prefetcher összeomlása is PIROS, nem néma,
  5. a KULCSSZAVAS válasz nem elég: a briefbe CÍMEK kellenek.
"""

import asyncio

import pytest

from plugins import _recipe_prefetch as pf


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake_prefetch(monkeypatch):
    """A prefetchert cseréljük, a próbát mérjük."""
    def _set(payload):
        async def _fn(deps):
            import json
            return json.dumps(payload)
        monkeypatch.setattr(pf, "prefetch_daily_news_brief", _fn, raising=False)
    return _set


FULL = {"fx_ecb": {"EUR/HUF": 364.79},
        "market_yahoo": [{"symbol": "GC=F"}],
        "hirmagnet_news": [{"title": "Valódi hírcím", "source": "Telex"}]}


def test_all_sections_present_is_green(fake_prefetch):
    fake_prefetch(FULL)
    out = _run(pf.probe_sections("daily_news_brief"))
    assert all(ok for ok, _ in out.values()), out


def test_the_original_bug_is_caught(fake_prefetch):
    """A RÉGI HIBA VISSZAJÁTSZVA: minden más rendben, csak az Echolot-szekció
    üres — pontosan úgy, ahogy hónapokig ment. Ez PIROS."""
    fake_prefetch(dict(FULL, hirmagnet_news=[]))
    out = _run(pf.probe_sections("daily_news_brief"))
    ok, detail = out["hirmagnet_news"]
    assert ok is False, "az üres Echolot-szekció átcsúszott — ez volt az eredeti hiba"
    assert "URES" in detail
    assert "a brief e nelkul keszulne el" in detail, \
        "a próba nem mondja meg, mi a KÖVETKEZMÉNYE"
    # a többi szekció közben zöld marad: a hiba LOKALIZÁLT
    assert out["fx_ecb"][0] is True and out["market_yahoo"][0] is True


def test_error_marker_section_reports_the_cause(fake_prefetch):
    """Ha a prefetcher tudja, MIÉRT nincs adat, az ok jusson el a diagnózisig."""
    fake_prefetch(dict(FULL, hirmagnet_news=[{"_error": "ECHOLOT_URL nincs beallitva"}]))
    out = _run(pf.probe_sections("daily_news_brief"))
    ok, detail = out["hirmagnet_news"]
    assert ok is False
    assert "ECHOLOT_URL nincs beallitva" in detail


def test_undeclared_recipe_does_not_claim_health():
    """Ami nincs deklarálva, arra nem mondunk zöldet — üres eredményt adunk,
    és a `selfdiag` ebből 'nincs mit mérni'-t csinál."""
    assert _run(pf.probe_sections("nincs_ilyen_recept")) == {}


def test_exploding_prefetcher_is_red(monkeypatch):
    async def _boom(deps):
        raise RuntimeError("a prefetcher elszállt")

    monkeypatch.setattr(pf, "prefetch_daily_news_brief", _boom, raising=False)
    out = _run(pf.probe_sections("daily_news_brief"))
    assert out["_prefetcher"][0] is False
    assert "a prefetcher elszállt" in out["_prefetcher"][1]


def test_headlines_not_keywords():
    """A trending-végpont KULCSSZAVAKAT ad ("peter" 16 forrás, "trump" 14) —
    az egy súlyozott témalista, nem hírlista. Egy hírbriefbe CÍMEK kellenek,
    és a kulcsszavas válasz pont az a "nem üres, tehát jó" adat, ami minden
    naiv ellenőrzésen átcsúszik."""
    import inspect
    # A DOCSTRINGET KIVESSZÜK, és csak a VÉGREHAJTHATÓ törzsre mérünk. A
    # `get_trending` és a `hirmagnet.hu` a docstringben JOGOSAN szerepel: az
    # mondja el, mit váltottunk le és miért. Ha a mérce nem választja szét a
    # kettőt, akkor a jó dokumentációt bünteti — a mérőeszközt előbb kell
    # hitelesíteni, mint a mért dolgot.
    # ⚠️ `inspect.getdoc()` DEDENTEL, ezért a `src.replace(doc, "")` nem talál
    # a behúzott eredetire — a docstring bennmaradna, és a teszt a saját
    # mérőeszköze miatt bukna. AST-tal vágjuk le, sor szerint.
    import ast as _ast, textwrap as _tw
    src = _tw.dedent(inspect.getsource(pf._fetch_hirmagnet_trending))
    _fn = _ast.parse(src).body[0]
    _first = _fn.body[0]
    _skip = (_first.end_lineno
             if isinstance(_first, _ast.Expr) and isinstance(_first.value, _ast.Constant)
             else 0)
    body = "\n".join(src.splitlines()[_skip:])
    assert "MIT VALTOTT LE" not in body, "a docstring bennmaradt — a mérce hibás"
    assert "echolot_client.fetch_news(" in body, "a prefetcher nem cikkeket kér"
    assert "echolot_client.get_trending(" not in body, \
        "a kulcsszavas trending-végpont visszakerült HÍVÁSKÉNT"
    assert "HIRMAGNET_API_KEY" not in body, \
        "visszakerült a nem létező API-kulcs olvasása — ez adott mindig []-t"
    assert "hirmagnet.hu" not in body, "a halott végpont visszakerült a törzsbe"


def test_required_sections_are_declared():
    req = pf.REQUIRED_SECTIONS["daily_news_brief"]
    assert set(req) == {"fx_ecb", "market_yahoo", "hirmagnet_news"}
