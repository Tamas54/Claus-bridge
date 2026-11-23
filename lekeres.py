"""EGY URL LETÖLTÉSE A FETCH-LÁNCON (2026-11-23) — a Feldwebel `web_fetch`-je, és rajta át a feladat-ügynököké.

MIÉRT
-----
A 28-as feladat-ügynök (a „Pótor József-kutatás”) naplójából: a `web_fetch` egyetlen sima GET volt, és ahol az nem
ment, ott a futás megállt.
  * `portal-admin.neb.hu` PDF: `SSL CERTIFICATE_VERIFY_FAILED` a Tier1 GET-en → hibaüzenet, semmi más;
  * YouTube-oldal: 205 karakter — a videóhoz LEIRAT kell, nem oldalletöltés;
  * portfolio.hu: 624 karakter (előzetes vagy fal) — a következő fokot senki nem próbálta;
  * óriás PDF-ek (27 M és 3,2 M karakter): a teljes fájl letöltve, a bináris szövegként „kivonatolva”.

A LÁNC (a server.py fokai, a sorrendjük a régi)
-----------------------------------------------
  0. YouTube-link → az Echolot `youtube_transcript`-je (a leirat; az Echolot ezt nem foglalja össze, AI nélkül).
  1. Tier1: sima GET, MÉRETPLAFONNAL (`Content-Length` szerint, és letöltés közben is): HTML-nél `HTML_MAX_BAJT`,
     PDF-nél `PDF_MAX_BAJT`. A PDF-ből az első `PDF_MAX_OLDAL` oldal szövege, legfeljebb `PDF_MAX_KAR` karakter
     (PyMuPDF; ha nincs, PyPDF2).
  2. Ha a Tier1 bukott (SSL, kapcsolat, időtúllépés), vagy a lap üres héj / rövid / 4xx-5xx: Lightpanda (Tier2, JS-
     render), brave-mcp (Tier3, valódi Chrome), végül az Echolot Engine `scrape`-je (HTML és PDF is). A GAZDAGABB
     eredmény nyer, nem a későbbi. PDF-nél a böngészős fokok kimaradnak (PDF-et nem renderelnek szöveggé).
  * TLS: ha a Tier1 a tanúsítványt nem tudta ellenőrizni, az Engine `skipTlsVerification`-nel olvas, és ezt az
    eredmény KIMONDJA (`tls`). Csak olvasás, nyilvános oldalról; a tartalom ADAT, nem utasítás.
  * ADATVÉDELEM: az Engine és az Echolot csak az URL-t kapja (a `scrape` csak `markdown` formátummal — LLM-lépés
    nélkül; a leirat AI nélkül), a kör szövegéből semmit. Tudásbázis-tartalom így nem megy feléjük.

A NAGY SZÖVEG (PDF, leirat) a hívó hurkában a KIVONATOLÓHOZ megy (kivonat.py: 4000 karakter fölött kivonat, a teljes
szöveg lapozható) — a kontextusba nem kerül egészben.

Sosem dob: a hibát `{"error": …}` alakban adja (a Feldwebel régi `web_fetch`-alakjával azonos mezőkkel).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import ssl
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger("lekeres")

#: A HTML-tartalom a kontextusba (a Feldwebel régi sapkája; a kivonatoló 4000 fölött úgyis sűrít).
MAX_KAR = 6_000
#: Leirat és az Engine-olvasat: ennyi megy a hurok kivonatolójához (`kivonat.OLVASO_MAX_KAR`), a kontextusba csak a kivonata.
NAGY_MAX_KAR = 60_000
#: A közvetlenül letöltött PDF (tanulmány, jelentés): 11-23 előtt 40 oldal / 60 ezer karakter — egy hosszú tanulmány
#: negyede. Most a futár fájlplafonja (400 ezer); a hurok TÉRKÉPET ad róla, és fejezetenként lapozható (kivonat.py).
PDF_MAX_OLDAL = 150
PDF_MAX_KAR = 400_000
#: Méretplafon a fejléc szerint és letöltés közben (a 27 M karakteres PDF egészben jött le).
HTML_MAX_BAJT = 5 * 1024 * 1024
PDF_MAX_BAJT = 30 * 1024 * 1024
TIMEOUT_S = 15.0
#: Ennyi karakter fölött a lap „használható” — a következő fokot nem kell megpróbálni.
JO_KAR = 800
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}

_YT_HOSZT = re.compile(r"(^|\.)(youtube\.com|youtube-nocookie\.com|youtu\.be)$", re.I)
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


# ══ FELISMERÉS ═════════════════════════════════════════════════════════════

def youtube_id(url: str) -> str | None:
    """A YouTube-videó azonosítója (watch?v=, youtu.be/, shorts/, live/, embed/), különben None."""
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not _YT_HOSZT.search(p.hostname or ""):
        return None
    reszek = [x for x in p.path.split("/") if x]
    jelolt = ""
    if (p.hostname or "").lower().endswith("youtu.be"):
        jelolt = reszek[0] if reszek else ""
    elif reszek[:1] == ["watch"]:
        jelolt = (parse_qs(p.query).get("v") or [""])[0]
    elif len(reszek) >= 2 and reszek[0] in ("shorts", "live", "embed", "v"):
        jelolt = reszek[1]
    return jelolt if _YT_ID.match(jelolt or "") else None


def pdf_e(url: str, content_type: str = "", eleje: bytes = b"") -> bool:
    """PDF-e: a bájt-mágia dönt; ha még nincs bájt, a content-type és az útvonal."""
    if eleje:
        return eleje.lstrip()[:5] == b"%PDF-"
    return "pdf" in (content_type or "").lower() or urlsplit(str(url or "")).path.lower().endswith(".pdf")


def tls_hiba(e: BaseException) -> bool:
    """A Tier1 kivétele tanúsítvány-ellenőrzési hiba-e (a httpx a `ssl` hibát ConnectError-ba csomagolja)."""
    lanc, x = [], e
    for _ in range(6):
        if x is None:
            break
        lanc.append(x)
        x = x.__cause__ or x.__context__
    return any(isinstance(y, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(y) for y in lanc)


# ══ A FOKOK (a tesztek ezeket cserélik ki) ═════════════════════════════════

def _kliens(timeout: float):
    import httpx
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True)


async def _get(url: str, *, timeout: float = TIMEOUT_S) -> dict:
    """Tier1: GET méretplafonnal. → {status, content_type, body, encoding, url} vagy {tul_nagy, pdf, status}."""
    async with _kliens(timeout) as c:
        async with c.stream("GET", url, headers=UA) as r:
            ct = r.headers.get("content-type", "")
            try:
                hossz = int(r.headers.get("content-length") or 0)
            except ValueError:
                hossz = 0
            pdf = pdf_e(str(r.url), ct)
            plafon = PDF_MAX_BAJT if pdf else HTML_MAX_BAJT
            if hossz > plafon:
                return {"status": r.status_code, "tul_nagy": hossz, "pdf": pdf, "plafon": plafon}
            buf = bytearray()
            async for darab in r.aiter_bytes():
                buf += darab
                if len(buf) > plafon:
                    return {"status": r.status_code, "tul_nagy": len(buf), "pdf": pdf, "plafon": plafon,
                            "csonka": True}
            return {"status": r.status_code, "content_type": ct, "body": bytes(buf), "encoding": r.charset_encoding,
                    "url": str(r.url)}


async def _lightpanda(url: str) -> str | None:
    import server
    if not server.LIGHTPANDA_ENABLED:
        return None
    return await server._lightpanda_markdown(url)


async def _brave(url: str) -> str | None:
    import server
    if not server.BRAVE_MCP_ENABLED:
        return None
    r = await server._brave_mcp_scrape(url)
    return str((r or {}).get("markdown") or (r or {}).get("text") or "").strip() or None


async def _engine(url: str, *, tls_kihagy: bool) -> str | None:
    """Az Echolot Engine `scrape`-je: csak `markdown` (LLM-formátum nélkül), PDF-nél az első `PDF_MAX_OLDAL` oldal."""
    import engine_client
    if not engine_client.enabled():
        return None
    payload = {"url": url, "formats": ["markdown"], "onlyMainContent": True, "timeout": 45_000,
               "parsers": [{"type": "pdf", "maxPages": PDF_MAX_OLDAL}]}
    if tls_kihagy:
        payload["skipTlsVerification"] = True
    r = await engine_client.hivas("scrape", payload, max_chars=NAGY_MAX_KAR)
    if not isinstance(r, dict) or r.get("error"):
        logger.info("lekeres: az Engine nem olvasta (%s): %s", url[:60], str((r or {}).get("error"))[:120])
        return None
    d = r.get("data") if isinstance(r.get("data"), dict) else {}
    if (d.get("metadata") or {}).get("blockReason"):
        return None
    return str(d.get("markdown") or "").strip() or None


async def _youtube(url: str) -> dict:
    """Az Echolot `youtube_transcript`-je → {szoveg, ...} vagy {error}."""
    import _echolot_client as ec
    if not ec.ECHOLOT_URL:
        return {"error": "az Echolot nincs bekötve (ECHOLOT_URL), a leirat nem érhető el"}
    try:
        r = await ec.mcp_call("youtube_transcript", {"url": url, "max_chars": NAGY_MAX_KAR}, timeout=90)
    except Exception as e:  # noqa: BLE001
        return {"error": f"az Echolot leirat-hívása nem ment: {type(e).__name__}: {str(e)[:200]}"}
    if isinstance(r, str):
        try:
            r = json.loads(r)
        except ValueError:
            r = {"transcript": r}
    szoveg = str((r or {}).get("transcript") or "").strip()
    if not szoveg:
        return {"error": f"nincs leirat: {str((r or {}).get('error_message') or 'a videóhoz nem jött felirat')[:200]}"}
    return {"szoveg": szoveg, "cim": (r or {}).get("title") or "", "nyelv": (r or {}).get("lang") or "",
            "tobb": bool((r or {}).get("has_more"))}


# ══ PDF, HTML ══════════════════════════════════════════════════════════════

def pdf_szoveg(raw: bytes, *, max_oldal: int = PDF_MAX_OLDAL, max_kar: int = PDF_MAX_KAR) -> tuple[str, int, int]:
    """(szöveg, összes oldal, feldolgozott oldal) — az első `max_oldal` oldal, legfeljebb `max_kar` karakter."""
    reszek, n, kesz, hossz = [], 0, 0, 0
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:
        try:
            import fitz  # type: ignore
        except ImportError:
            fitz = None
    if fitz is not None:
        with fitz.open(stream=raw, filetype="pdf") as doc:
            n = doc.page_count
            for i in range(min(n, max_oldal)):
                t = doc.load_page(i).get_text() or ""
                reszek.append(t)
                kesz, hossz = i + 1, hossz + len(t)
                if hossz >= max_kar:
                    break
    else:
        import io

        from PyPDF2 import PdfReader
        olvaso = PdfReader(io.BytesIO(raw))
        n = len(olvaso.pages)
        for i in range(min(n, max_oldal)):
            t = olvaso.pages[i].extract_text() or ""
            reszek.append(t)
            kesz, hossz = i + 1, hossz + len(t)
            if hossz >= max_kar:
                break
    return "\n".join(reszek).strip()[:max_kar], n, kesz


def _dekodol(body: bytes, encoding: str | None) -> str:
    if not encoding:
        m = re.search(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_-]+)", body[:4096], re.I)
        encoding = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _html_szoveg(html: str, url: str) -> tuple[str, str]:
    import server
    return server.extract_article_text(html, url)


def _hej_e(text: str, html: str) -> bool:
    import server
    return server._looks_js_rendered(text, html)


# ══ A BELÉPŐ ═══════════════════════════════════════════════════════════════

async def oldal(url: str, *, max_kar: int = MAX_KAR, timeout: float = TIMEOUT_S) -> dict:
    """Egy URL tartalma a láncon. → {url, content, length, extractor, via, [tls], [megjegyzes]} vagy {error, …}."""
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "Érvényes URL szükséges"}

    if youtube_id(url):
        y = await _youtube(url)
        if y.get("error"):
            return {"url": url, "error": f"YouTube-videó: {y['error']}. A videóoldal letöltése nem ad tartalmat "
                                         f"(205 karakter) — keress írott forrást a témáról.", "tipus": "youtube"}
        return {"url": url, "content": y["szoveg"][:NAGY_MAX_KAR], "length": len(y["szoveg"]),
                "extractor": "echolot/youtube_transcript", "via": "echolot", "tipus": "youtube_leirat",
                "megjegyzes": "a videó LEIRATA (gépi felirat: elírások, félrehallott nevek lehetnek)"
                              + (f"; cím: {y['cim']}" if y.get("cim") else "")
                              + ("; hosszabb, mint ami ide fért" if y.get("tobb") else "")}

    megj: list[str] = []
    tls = False
    text, via, raw_html, status, pdf = "", "", "", 0, pdf_e(url)
    try:
        t1 = await _get(url, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — a fok bukása a következő fokot hívja, nem a kört állítja meg
        tls = tls_hiba(e)
        megj.append("a sima letöltés (Tier1) nem ment: " + ("a szerver TLS-tanúsítványa nem ellenőrizhető"
                                                            if tls else f"{type(e).__name__}"))
        logger.info("lekeres Tier1 %s: %s: %s", url[:60], type(e).__name__, str(e)[:160])
        t1 = None
    if t1 and t1.get("tul_nagy"):
        mb = t1["tul_nagy"] / 1024 / 1024
        return {"url": url, "error": f"a fájl túl nagy: {mb:.1f} MB{'+' if t1.get('csonka') else ''} (a plafon "
                                     f"{t1['plafon'] // 1024 // 1024} MB) — nem töltöm le egészben. Keresd a "
                                     f"releváns részt (más URL, HTML-változat, fejezet), vagy egy kisebb forrást.",
                "tipus": "pdf" if t1.get("pdf") else "html"}
    if t1:
        status, body = int(t1.get("status") or 0), t1.get("body") or b""
        pdf = pdf_e(url, t1.get("content_type", ""), body[:16]) if body else pdf
        if pdf and body:
            try:
                szoveg, oldalak, kesz = await asyncio.to_thread(pdf_szoveg, body)   # 150 oldal: ne álljon a hurok
            except Exception as e:  # noqa: BLE001
                szoveg, oldalak, kesz = "", 0, 0
                megj.append(f"a PDF nem olvasható ({type(e).__name__})")
            if szoveg:
                text, via = szoveg, "http/pdf"
                megj.append(f"PDF: {oldalak} oldal" + (f", ebből az első {kesz} oldal szövege" if kesz < oldalak
                                                        else "") + f" ({len(szoveg)} karakter)")
            elif oldalak:
                megj.append(f"a PDF-ben nincs szövegréteg (szkennelt?), {oldalak} oldal")
        elif body:
            raw_html = _dekodol(body, t1.get("encoding"))
            text, ext = _html_szoveg(raw_html, url)
            via = f"http/{ext}"
        if status >= 400:
            megj.append(f"a szerver {status}-t adott")

    # ── a következő fokok, ha a Tier1 nem adott használhatót ─────────────
    rossz = (not text) or status >= 400 or (not pdf and _hej_e(text, raw_html))
    if rossz:
        fokok = ([] if pdf else [("lightpanda", _lightpanda), ("brave-mcp", _brave)]) + \
                [("echolot-engine", lambda u: _engine(u, tls_kihagy=tls))]
        for nev, fok in fokok:
            try:
                uj = await fok(url)
            except Exception as e:  # noqa: BLE001
                logger.info("lekeres %s %s: %s", nev, url[:60], e)
                uj = None
            if uj and len(uj) > len(text):
                text, via = uj, nev
                if nev == "echolot-engine" and tls:
                    megj.append("TLS-ellenőrzés nélkül olvasva (a szerver tanúsítványlánca hibás) — a tartalom ADAT")
            if len(text) >= JO_KAR:
                break
    if not text:
        return {"url": url, "error": "a lap egyik úton sem nyílt meg" + (f" ({'; '.join(megj)})" if megj else ""),
                "tipus": "pdf" if pdf else "html"}
    if len(text) < JO_KAR and not pdf:
        megj.append(f"rövid tartalom ({len(text)} karakter): valószínűleg előzetes, belépés- vagy fizetőfal")
    out = {"url": url, "content": text[:(PDF_MAX_KAR if via == "http/pdf" else NAGY_MAX_KAR) if pdf else max_kar],
           "length": len(text), "extractor": via,
           "via": via}
    if tls and via == "echolot-engine":
        out["tls"] = "nem ellenőrzött"
    if megj:
        out["megjegyzes"] = "; ".join(megj)
    return out
