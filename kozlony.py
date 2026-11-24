"""Magyar Közlöny: keresés a hivatalos lap saját keresőjében, a találat a lapszám PDF-jéből, jogforrással és oldallal.

Miért (11-24, a Kommandant a Pótor-kutatásról: „van róla rendes közlönyös forrás … szedje össze magát az agent!”):
a feladat-ügynök a kinevezést nem találta, mert a 2026-os közlönyöket a webes keresők alig indexelik. A Közlöny saját
keresője (https://magyarkozlony.hu/?content=…) viszont egy hívásra hozza: „Magyar Közlöny 2026. évi 80. szám …
Pótor Józsefet …” — és a lapszám PDF-jében ott a teljes határozat: „A miniszterelnök 63/2026. (VI. 26.) ME
határozata …”, 3157. oldal.

A kereső csapdája: SZAVANKÉNT VAGY-ol — a „Pótor József” keresés a „József” miatt bármilyen Józsefet hoz. Ezért
személynévnél a VEZETÉKNÉVVEL keresünk, és a lapszám szövegében a teljes névre szűrünk (a keresztnév tövével, mert a
közlönyben ragozva áll: „Pótor Józsefet”). Az azonos vezetéknevű MÁS személyek (pl. „Pótor László”) külön listába
kerülnek — hogy a jelentés ne keverje össze őket.

Csak olvas; a PDF-et a fetch-lánc GET-je hozza (lekeres._get, méretplafonnal), a szöveget a PyMuPDF (ha nincs, PyPDF2).
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import urlencode

KERESO = "https://magyarkozlony.hu/"
#: Ennyi lapszám PDF-jét töltjük le alapból (a kereső a legújabbat adja elöl); a többi csak a kereső részletével jön.
ALAP_LAPSZAM = 3
MAX_LAPSZAM = 6
#: Lapszámonként legfeljebb ennyi részlet, részletenként ennyi karakter (a Feldwebel ablaka szűk).
MAX_RESZLET = 3
RESZLET_KAR = 700
#: A PDF-ből legfeljebb ennyi oldalt nézünk (egy közlönyszám jellemzően < 400 oldal).
MAX_PDF_OLDAL = 800

#: Személynév előtti címek — a keresésből kimaradnak.
CIMEK = {"dr", "dr.", "prof", "prof.", "ifj", "ifj.", "id", "id.", "özv", "özv."}
NAGYBETU = "A-ZÁÉÍÓÖŐÚÜŰ"
KISBETU = "a-záéíóöőúüű"

#: Jogforrás-fejléc a közlönyben: önálló sor, „A”/„Az” kezdettel, a jogforrás fajtájával a végén — számozott („A
#: miniszterelnök 63/2026. (VI. 26.) ME határozata”, „A Kormány 1234/2026. (VI. 26.) Korm. határozata”) és számozatlan
#: is („A közigazgatási és igazságügyi miniszter közleménye” — a Hivatalos Értesítő közleményei jellemzően ilyenek;
#: e nélkül a találat egy korábbi, számozott fejléc alá került, 11-24).
FEJLEC = re.compile(
    r"^(Az?\s[^\n]{2,150}?\s(?:határozata|rendelete|utasítása|végzése|közleménye|hirdetménye|parancsa))[ \t]*$",
    re.M)
LAPFEJ = re.compile(r"(\d{4})\.\s*évi\s*(\d+)\.\s*szám")


# ══ A FOKOK (a tesztek ezeket cserélik ki) ═════════════════════════════════

async def _get(url: str) -> dict:
    import lekeres
    return await lekeres._get(url)


def _oldalak(raw: bytes) -> list[str]:
    """A PDF oldalankénti szövege (az oldalhatár kell: abból jön az oldalszám)."""
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:
        try:
            import fitz  # type: ignore
        except ImportError:
            fitz = None
    if fitz is not None:
        with fitz.open(stream=raw, filetype="pdf") as doc:
            return [doc.load_page(i).get_text() or "" for i in range(min(doc.page_count, MAX_PDF_OLDAL))]
    import io

    from PyPDF2 import PdfReader
    olvaso = PdfReader(io.BytesIO(raw))
    return [(p.extract_text() or "") for p in olvaso.pages[:MAX_PDF_OLDAL]]


# ══ TISZTA FÜGGVÉNYEK ═════════════════════════════════════════════════════

def _norm(s: str) -> str:
    return re.sub(r"[ \t\xa0]+", " ", (s or "").replace("\r", ""))


def nev_tagok(kifejezes: str) -> list[str] | None:
    """Személynév-e: 2–4 nagybetűs tag (a címek nélkül) → a tagok; különben None."""
    tagok = [t for t in re.split(r"\s+", (kifejezes or "").strip()) if t and t.lower() not in CIMEK]
    if 2 <= len(tagok) <= 4 and all(re.match(rf"[{NAGYBETU}][{KISBETU}\-]+$", t) for t in tagok):
        return tagok
    return None


def kereso_kifejezes(kifejezes: str) -> str:
    """A Közlöny keresőjének kifejezése: személynévnél a vezetéknév (a kereső VAGY-ol), egyébként a kifejezés."""
    tagok = nev_tagok(kifejezes)
    return tagok[0] if tagok else (kifejezes or "").strip()


def kereso_url(kifejezes: str) -> str:
    return KERESO + "?" + urlencode({"content": kereso_kifejezes(kifejezes)})


def _szoveg(h: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()


def talalatok_htmlbol(lap: str) -> list[dict]:
    """A kereső találatlistája: [{lap, datum, pdf, kereso_reszlet}], a kereső sorrendjében (a legújabb elöl)."""
    out = []
    for blokk in re.split(r'<div class="journal-row">', lap or "")[1:]:
        cim = re.search(r'<b itemprop="name">(.*?)</b>', blokk, re.S)
        datum = re.search(r'itemprop="datePublished" content="([0-9-]+)"', blokk)
        pdf = re.search(r'href="(https://magyarkozlony\.hu/[^"]*?/letoltes)"', blokk)
        reszlet = re.search(r'text-muted text-sm">\s*<div[^>]*>(.*?)</div>', blokk, re.S)
        if not (cim and pdf):
            continue
        out.append({"lap": _szoveg(cim.group(1)), "datum": datum.group(1) if datum else "",
                    "pdf": pdf.group(1), "kereso_reszlet": _szoveg(reszlet.group(1))[:300] if reszlet else ""})
    return out


def oldalszam(oldal_szoveg: str, pdf_index: int) -> str:
    """A közlöny saját oldalszáma a lapfejből („… 2026. évi 80. szám 3157”); ha nincs, a PDF-oldal."""
    fej = _norm(oldal_szoveg[:200])
    m = LAPFEJ.search(fej)
    if m:
        kizart = {m.group(1), m.group(2)}
        for n in re.findall(r"(?<![\d/.])(\d{1,5})(?![\d/.])", fej):
            if n not in kizart:
                return n
    return f"PDF {pdf_index + 1}. oldal"


def _fejlec_elotte(szoveg: str, poz: int) -> tuple[str, str]:
    """A találat előtti utolsó jogforrás-fejléc és tárgya (a fejlécet követő kisbetűs sorok)."""
    utolso = None
    for m in FEJLEC.finditer(szoveg, 0, poz):
        utolso = m
    if not utolso:
        return "", ""
    targy = []
    for sor in szoveg[utolso.end():].lstrip("\n").split("\n")[:4]:
        sor = sor.strip()
        if not sor or not re.match(rf"[{KISBETU}]", sor):
            break
        targy.append(sor)
    return re.sub(r"\s+", " ", utolso.group(1)).strip(), " ".join(targy).strip()


def reszletek_oldalakbol(oldalak: list[str], kifejezes: str) -> tuple[list[dict], list[str]]:
    """A lapszám oldalaiból a kifejezés előfordulásai: ([{oldal, jogforras, targy, szoveg}], [más személyek]).

    Személynévnél a vezetéknév után 60 karakteren belül a keresztnév töve kell (ragozva áll); ami nem az, az MÁS
    személy („Pótor László”) — azt csak megnevezzük."""
    tagok = nev_tagok(kifejezes)
    if tagok:
        vezetek, tovek = tagok[0], [t[:max(3, len(t) - 2)] for t in tagok[1:]]
        minta = re.compile(re.escape(vezetek) + rf"(?:\s+[{NAGYBETU}][{KISBETU}\-]+){{1,3}}")
    else:
        vezetek, tovek = (kifejezes or "").strip(), []
        minta = re.compile(re.escape(vezetek), re.I)
    reszletek, masok = [], []
    elozo = ""          # az előző oldal vége: a fejléc lehet az előző oldalon
    for i, nyers in enumerate(oldalak):
        szoveg = _norm(nyers)
        for m in minta.finditer(szoveg):
            talalt = m.group(0)
            if tovek and not all(t.lower() in talalt.lower() for t in tovek):
                nev = re.sub(r"\s+", " ", talalt).strip()
                if nev not in masok:
                    masok.append(nev)
                continue
            if len(reszletek) >= MAX_RESZLET:
                continue
            jf, targy = _fejlec_elotte(elozo + "\n" + szoveg, len(elozo) + 1 + m.start())
            ki = szoveg[max(0, m.start() - RESZLET_KAR // 2): m.end() + RESZLET_KAR // 2]
            reszletek.append({"oldal": oldalszam(nyers, i), "jogforras": jf, "targy": targy,
                              "szoveg": re.sub(r"\s+", " ", ki).strip()})
        elozo = szoveg[-3000:]
    return reszletek, masok


# ══ AZ ESZKÖZ ══════════════════════════════════════════════════════════════

async def kereses(kifejezes: str, max_lapszam: int | None = None) -> dict:
    """`magyar_kozlony` eszköz: a Közlöny keresője + a legújabb `max_lapszam` lapszám PDF-jéből a pontos részlet."""
    kifejezes = (kifejezes or "").strip()
    if len(kifejezes) < 3:
        return {"error": "adj meg legalább 3 karakteres kifejezést (személynévnél a teljes nevet)"}
    try:
        n = int(max_lapszam or ALAP_LAPSZAM)
    except (TypeError, ValueError):
        n = ALAP_LAPSZAM
    n = max(1, min(MAX_LAPSZAM, n))
    url = kereso_url(kifejezes)
    try:
        lap = await _get(url)
    except Exception as e:  # noqa: BLE001
        lap = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
    if lap.get("error") or not lap.get("body") or (lap.get("status") or 200) >= 400:
        return {"error": f"a Közlöny keresője nem válaszolt ({lap.get('status') or lap.get('error')})", "kereso": url}
    talalatok = talalatok_htmlbol(lap["body"].decode("utf-8", "replace"))
    out = {"kifejezes": kifejezes, "kereso": url, "kereso_kifejezes": kereso_kifejezes(kifejezes),
           "talalat": len(talalatok), "lapszamok": [], "tovabbi_talalatok": talalatok[n:],
           "megjegyzes": ("A Közlöny keresője szavanként VAGY-ol: személynévnél a vezetéknévvel keres, a lapszámban a "
                          "teljes névre szűr. Az azonos vezetéknevű más személyek a `mas_szemelyek` listában. "
                          "Idézéskor: jogforrás + Magyar Közlöny évfolyam, szám, oldal + a PDF linkje.")}
    if not talalatok:
        out["megjegyzes"] = ("A Közlöny keresője erre nem adott találatot (igazolt hiány a hivatalos lapban: "
                             f"{url}). Más alakkal (pl. csak vezetéknév, szótöredék `*`-gal) még próbálható.")
        return out
    for t in talalatok[:n]:
        elem = dict(t)
        try:
            pdf = await _get(t["pdf"])
            if pdf.get("tul_nagy"):
                elem["hiba"] = f"a PDF túl nagy ({pdf['tul_nagy']} bájt) — a kereső részlete és a link marad"
            elif not pdf.get("body"):
                elem["hiba"] = f"a PDF nem jött le ({pdf.get('status') or pdf.get('error')})"
            else:
                reszletek, masok = reszletek_oldalakbol(_oldalak(pdf["body"]), kifejezes)
                elem["reszletek"] = reszletek
                if masok:
                    elem["mas_szemelyek"] = masok
                if not reszletek and not masok:
                    elem["hiba"] = "a PDF szövegében a kifejezés nem található (szkennelt vagy elválasztott szöveg?)"
        except Exception as e:  # noqa: BLE001
            elem["hiba"] = f"{type(e).__name__}: {str(e)[:160]}"
        out["lapszamok"].append(elem)
    return out
