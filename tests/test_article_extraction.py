"""
CIKK-KIVONATOLÁS — a fetch-réteg minőségi kapuja.

A HIBA, AMIÉRT EZ MEGSZÜLETETT
------------------------------
2026-08-30-ig a repó MINDEN fetch-útja puszta regexszel strippelt, majd 2000
(a Feldwebelnél 6000) karakterre vágott. Mérve 10 valódi hírcikken:

  * a kimenet 68%-a boilerplate volt (medián 7052 -> 2169 karakter),
  * 4/10 cikknél a LEAD ki sem fért a 2000 karakteres ablakba — a Guardian és
    az ANSA cikkéből NULLA bekezdés jutott a modellhez, csak navigáció,
  * a regex nem dekódolta a HTML-entitásokat, ezért a BBC leadje szövegként
    meg sem volt található.

És a legrosszabb: a `trafilatura` a fejlesztői gép ~/.local-jából feloldódott,
miközben a `requirements.txt`-ből HIÁNYZOTT. A helyi futás jónak látszott, a
prod regexet futtatott. Néma minőségromlás, ami hónapokig elélhet.

Amit ezek a tesztek őriznek:
  1. a kivonatoló DEKLARÁLVA van (a requirements.txt-et is mérjük, nem csak a
     futó környezetet — ez a fenti csapda egyetlen valódi őre),
  2. a boilerplate (nav/footer) tényleg eltűnik, a cikk marad,
  3. a HTML-entitások dekódolva vannak,
  4. egy gyenge kivonat NEM nyer a jobb ellen (a trafilatura 40 karaktert is
     visszaadhat egy oldalra, amit nem ért),
  5. a lánc SOSE dob — egy fetch-fok nem ölheti meg a kört, amit kiszolgál,
  6. a tényleges fok LÁTHATÓ (`article_extractor_status`), hogy a regexre esés
     ne bootlogból derüljön ki.
"""

import os
import re

import pytest

import server


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ARTICLE_HTML = """<html><head><title>Teszt</title></head><body>
<nav>Főoldal Rovatok Belépés Előfizetés Hírlevél Kapcsolat</nav>
<header>Reklám Reklám Reklám</header>
<article>
  <h1>A monetáris tanács döntése</h1>
  <p>Az első bekezdés a lead, és elég hosszú ahhoz, hogy a kivonatoló komolyan
  vegye: legalább kétszáz karakternyi valódi mondat a témáról, nem menüpont és
  nem jogi lábjegyzet, hanem összefüggő szöveg a döntés hátteréről.</p>
  <p>A második bekezdés szintén valódi tartalom, további mondatokkal, hogy a
  kivonat biztosan túllépje a küszöböt és értékelhető legyen.</p>
</article>
<footer>Impresszum Adatvédelem Cookie-k Minden jog fenntartva</footer>
</body></html>"""


# ── 1. A DEKLARÁCIÓ ─────────────────────────────────────────────────────

def test_extractors_are_declared_in_requirements():
    """A futó környezet MÉRÉSE nem elég: a fejlesztői gépen a ~/.local-ból is
    feloldódhat, miközben a Railway sosem kapja meg. Ez a teszt a
    requirements.txt-et nézi — ez az egyetlen, ami a prodra is állítás."""
    with open(os.path.join(REPO_ROOT, "requirements.txt"), encoding="utf-8") as fh:
        req = fh.read()
    declared = {
        line.split("#")[0].strip().split(">")[0].split("=")[0].strip().lower()
        for line in req.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert "trafilatura" in declared, (
        "a trafilatura nincs a requirements.txt-ben — a prod regexre esik, "
        "miközben a fejlesztői gépen minden jónak látszik"
    )
    assert "readability-lxml" in declared


def test_status_names_the_effective_tier():
    st = server.article_extractor_status()
    assert set(st) == {"trafilatura", "readability", "effective_tier"}
    assert st["effective_tier"] in {"trafilatura", "readability", "regex"}


# ── 2. A BOILERPLATE ELTŰNIK, A CIKK MARAD ──────────────────────────────

def test_navigation_and_footer_are_dropped():
    text, extractor = server.extract_article_text(ARTICLE_HTML, "https://x.example/c")
    assert "lead" in text, "a cikk leadje kiesett a kivonatból"
    assert "Előfizetés" not in text, "a navigáció bennmaradt"
    assert "Impresszum" not in text, "a lábléc bennmaradt"
    assert extractor in {"trafilatura", "readability"}


def test_extraction_is_much_smaller_than_the_raw_strip():
    """A nyereség MÉRHETŐ: a kivonat érdemben rövidebb, mint a nyers strip,
    és mégis tartalmazza a leadet. Ez a 68%-os boilerplate-arány őre."""
    text, _ = server.extract_article_text(ARTICLE_HTML)
    raw = server._regex_strip(ARTICLE_HTML)
    assert len(text) < len(raw)
    assert "lead" in text


# ── 3. HTML-ENTITÁSOK ───────────────────────────────────────────────────

def test_regex_fallback_decodes_entities():
    """A BBC leadje azért nem volt megtalálható, mert a szövegben `isn&#x27;t`
    állt. Egy kereső-hívó, aki a lead egy mondatára illeszt, ilyenkor nem
    talál semmit — pedig a szöveg ott van."""
    out = server._regex_strip("<div><p>it isn&#x27;t &amp; won&#39;t</p></div>")
    assert "isn't" in out
    assert "&" in out and "&amp;" not in out


def test_whitespace_is_collapsed():
    out = server._regex_strip("<p>a</p>\n\n\n   <p>b</p>")
    assert "  " not in out


# ── 4. GYENGE KIVONAT NEM NYER ──────────────────────────────────────────

def test_a_thin_extraction_does_not_win(monkeypatch):
    """A trafilatura egy nem értett oldalra 40 karaktert is visszaadhat.
    Ha ezt elfogadnánk, a "jobb" fok rosszabb lenne a regexnél, amit lecserélt —
    és pont a hosszú, tartalmas oldalakon."""
    class _Thin:
        @staticmethod
        def extract(html, **kw):
            return "pár szó"

    monkeypatch.setattr(server, "_trafilatura", _Thin)
    monkeypatch.setattr(server, "_HAVE_TRAFILATURA", True)
    text, extractor = server.extract_article_text(ARTICLE_HTML)
    assert extractor != "trafilatura", "egy 7 karakteres kivonat nem nyerhet"
    assert "lead" in text, "a degradált út is a cikket adja"


def test_empty_input_is_not_an_error():
    assert server.extract_article_text("") == ("", "none")
    assert server.extract_article_text("   ") == ("", "none")


# ── 5. A LÁNC SOSE DOB ──────────────────────────────────────────────────

def test_exceptions_degrade_instead_of_escaping(monkeypatch):
    class _Boom:
        @staticmethod
        def extract(html, **kw):
            raise RuntimeError("belső hiba")

    class _AlsoBoom:
        def __init__(self, html):
            raise RuntimeError("readability is elszállt")

    monkeypatch.setattr(server, "_trafilatura", _Boom)
    monkeypatch.setattr(server, "_HAVE_TRAFILATURA", True)
    monkeypatch.setattr(server, "_ReadabilityDocument", _AlsoBoom)
    monkeypatch.setattr(server, "_HAVE_READABILITY", True)

    text, extractor = server.extract_article_text(ARTICLE_HTML)
    assert extractor == "regex", "mindkét fok elszállt — a regexnek kell kifognia"
    assert "lead" in text


def test_all_extractors_absent_still_returns_text(monkeypatch):
    monkeypatch.setattr(server, "_HAVE_TRAFILATURA", False)
    monkeypatch.setattr(server, "_HAVE_READABILITY", False)
    text, extractor = server.extract_article_text(ARTICLE_HTML)
    assert extractor == "regex"
    assert "lead" in text


# ── 6. A FETCH-UTAK TÉNYLEG EZT HASZNÁLJÁK ──────────────────────────────

def test_fetch_paths_do_not_carry_their_own_regex_strip():
    """A repóban NÉGY külön HTML->szöveg implementáció élt. A duplikátumok
    azok, amik szétcsúsznak: az egyiket javítod, a másik három marad."""
    with open(os.path.join(REPO_ROOT, "server.py"), encoding="utf-8") as fh:
        src = fh.read()
    # Az OLDAL-szintű stripper ismertetőjele a <script>/<style> blokk kivágása.
    # (A puszta `<[^>]+>` csere ennél tompább mérce: a DDG találat-címeinek és
    # -snippetjeinek tisztítása is így néz ki, az viszont töredék-tisztítás, nem
    # oldal-kivonatolás, és jogosan marad a helyén.)
    hits = re.findall(r"<script\[\^>\]\*>\.\*\?</script>", src)
    assert len(hits) <= 1, (
        f"{len(hits)} külön OLDAL-szintű HTML->szöveg implementáció a "
        f"server.py-ban — a fetch-utaknak a közös extract_article_text-et kell "
        f"hívniuk. A duplikátumok azok, amik szétcsúsznak."
    )


def test_feldwebel_uses_the_shared_ladder():
    with open(os.path.join(REPO_ROOT, "feldwebel", "responder.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "extract_article_text" in src, (
        "a Feldwebel saját regex-implementációja a NEGYEDIK volt a repóban"
    )


def test_search_result_titles_decode_entities():
    """A találat-címek és -snippetek töredék-tisztítása jogosan külön út — de
    entitást AZ IS kap (`AT&amp;T`, `isn&#x27;t`). Dekódolatlanul a cím
    olvashatatlan, és a rá illesztő hívó nem talál."""
    assert server._clean_result_fragment("AT&amp;T <b>hír</b>") == "AT&T hír"
    assert server._clean_result_fragment("it isn&#x27;t") == "it isn't"
