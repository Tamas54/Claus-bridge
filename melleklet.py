"""MELLÉKLET (2026-11-22) — fénykép, PDF, Word és a többi a Feldwebelnek, Telegramról és a webes felületről.

A Kommandant: „bekopizok egy dokumentumot - mondjuk befényképezek egy jogi dokumentumot - és azt kérem, írjon rá
választ, adjon rá tanácsot. VAGY: van egy tanulmányom … adjon rá javaslatot - és kérdeznie is kell ilyenkor.”
És: „ott van a Studio, ami 50 féle dokumentumot tud feldolgozni - a cél, hogyha már megkapta, akkor használja”.

AZ ÚT
-----
1. **A fájl a Studióba megy** (`upload_file`, nagyobbnál egyszeri feltöltőlink + PUT) → `file_id` + szerkesztő-link.
   A további munka (válaszlevél Wordben, javaslat követett változtatásként, űrlap, átalakítás, szerkeszthető Word
   egy fotóból `digitize`-zal) ezzel a `file_id`-vel a Studio eszközein megy.
2. **A szöveg a beszélgetésbe kerül**, hogy a Feldwebel olvassa, és a következő kérdésnél is emlékezzen rá:
   * szöveges formátum (PDF szövegréteggel, DOCX, PPTX, HTML, MD, TXT…) → a Studio `extract`-ja (determinisztikus,
     ingyenes); amit az nem olvas (.doc, .odt, .rtf…) → a Studio `convert`-tel DOCX-szé alakítja, aztán `extract`;
   * fénykép és szkennelt oldal → a Sonnet SZÓ SZERINT átírja (a Studio útmutatója szerint a képet a kliens nézze
     meg — a Studio saját OCR-je kreditbe kerül, a `digitize` pedig a bélyegzőt és a kézírást kihagyja, egy jogi
     iratnál pedig épp a kézbesítési bélyegző dátuma adja a határidőt);
   * ha a Studio nem érhető el: a helyi feldolgozó (`youngereka_docs`, a LESESAAL-é — ugyanaz a kód, nem új).
3. A fájl a Bridge `uploads` táblájába is bekerül (továbbküldhető: „küldd el emailben a #12-t”).

A melléklet szövege a Feldwebel üzenetében `[Melléklet #ID: …]` fejléccel jön — erről ismeri fel a Faber-
szándékfelismerő (átugorja), az LLM-kapu (legalább Sonnet) és a felület (összecsukva mutatja).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time

import kulso_mcp
import youngereka_docs as yd

logger = logging.getLogger("melleklet")

JEL = "[Melléklet #"
KOZVETLEN_MAX = 4 * 1024 * 1024          # eddig base64-ben az MCP-hívásban; fölötte egyszeri feltöltőlink
MAX_FAJL = 50 * 1024 * 1024              # a Studio határa
DB_B64_MAX = 10 * 1024 * 1024            # ennél nagyobbat a Bridge-adatbázisba csak szövegként teszünk
MAX_SZOVEG_KAR = 40_000                  # ~10 ezer token: a beszélgetés előzményében is ennyi utazik
ATIRAS_MAX_OLDAL = 8
SZKENNELT_KUSZOB = 200                   # ennél kevesebb kinyert karakter → képként kezeljük (szkennelt PDF)
FRISS_TTL_S = 3 * 3600

ALAP_KERES = ("Küldtem egy mellékletet. Mondd meg röviden, mi ez (és ha van benne határidő, az elsőként), "
              "aztán kérdezd meg, mit kérek vele.")

ATIRO_PROMPT = (
    "Írd át a képe(ke)n látható dokumentum TELJES szövegét SZÓ SZERINT, az eredeti nyelven ({nev}). "
    "Tartsd meg a tagolást (címek, pontok, bekezdések, táblázat Markdown-táblaként). Ne foglald össze, ne értelmezd, "
    "ne javíts. A bélyegzőket, kézzel írt részeket, aláírást, iktatószámot és dátumokat is írd át, jelölve: "
    "[bélyegző: …], [kézírás: …], [aláírás]. Olvashatatlan rész: [olvashatatlan]; bizonytalan szó után: [?]. "
    "Több oldalnál az oldalak elé: --- {{n}}. oldal ---. Ha a kép nem dokumentum (tárgy, gyógyszerdoboz, tünet, "
    "helyszín), írd le pontosan, mi látható rajta, a rajta lévő feliratokkal együtt."
)

_FRISS: dict[int, tuple[float, dict]] = {}


# ── Studio ────────────────────────────────────────────────────────────────

def _json(szoveg: str) -> dict:
    try:
        v = json.loads(szoveg)
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


async def studioba(raw: bytes, nev: str) -> dict | None:
    """A fájl a Kommandant Studio-munkaterületére → {file_id, edit_url, …}, vagy None (nincs bekötve / hiba)."""
    if not kulso_mcp.bekotott("studio"):
        return None
    try:
        if len(raw) <= KOZVETLEN_MAX:
            v = _json(await kulso_mcp.hiv("studio", "upload_file",
                                          {"filename": nev, "content_base64": base64.b64encode(raw).decode("ascii")}))
        else:
            link = _json(await kulso_mcp.hiv("studio", "files", {"action": "upload_link", "title": nev}))
            url = link.get("url") or link.get("upload_url") or link.get("link")
            if not url:
                logger.warning("melleklet: a Studio nem adott feltöltőlinket: %s", str(link)[:200])
                return None
            import httpx
            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.put(url, content=raw)
            v = r.json() if r.status_code < 300 else {}
        if v.get("file_id"):
            return v
        logger.warning("melleklet: Studio-feltöltés sikertelen (%s): %s", nev, str(v)[:200])
    except Exception as e:  # noqa: BLE001 — a Studio kiesése ne vigye el a mellékletet
        logger.warning("melleklet: Studio-feltöltés hiba (%s): %s", nev, str(e)[:200])
    return None


async def _studio_szoveg(file_id: str) -> str:
    """A Studio `extract`-ja; amit nem olvas, azt előbb DOCX-szé alakítja (`convert`)."""
    v = _json(await kulso_mcp.hiv("studio", "extract", {"file_id": file_id, "mode": "text"}))
    if isinstance(v.get("text"), str):
        return v["text"]
    conv = _json(await kulso_mcp.hiv("studio", "convert", {"target_format": "docx", "file_id": file_id}))
    uj = conv.get("file_id")
    if not uj and isinstance(conv.get("result"), dict):
        uj = conv["result"].get("file_id")
    if not uj:
        return ""
    v = _json(await kulso_mcp.hiv("studio", "extract", {"file_id": uj, "mode": "text"}))
    return v.get("text") if isinstance(v.get("text"), str) else ""


# ── Átírás (fénykép, szkennelt oldal) ───────────────────────────────────────

async def atir(kepek: list[bytes], nev: str) -> str:
    """A képek szó szerinti átirata a Sonnettel (a kapu közép szintje; tartalék: a Kimi K2.7 vision)."""
    import llm_kapu
    payload = {"model": "moonshotai/Kimi-K2.7-Code", "max_tokens": 8000,
               "messages": [{"role": "user",
                             "content": yd.vision_content(ATIRO_PROMPT.format(nev=nev), kepek[:ATIRAS_MAX_OLDAL])}]}
    data, err, _ = await llm_kapu.chat(payload, purpose="melleklet/atiras")
    if err is not None or not data:
        return f"(Az átírás nem sikerült: {(err or {}).get('message', 'ismeretlen hiba')[:200]})"
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


# ── A teljes út ──────────────────────────────────────────────────────────

def _tarol(get_db, nev: str, mime: str, raw: bytes, szoveg: str, kitol: str, most: str) -> int:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO uploads (filename, mime_type, content_text, content_base64, uploaded_by, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (nev, mime or "application/octet-stream", szoveg,
             base64.b64encode(raw).decode("ascii") if len(raw) <= DB_B64_MAX else "", kitol, most))
        uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return int(uid)
    finally:
        conn.close()


async def feldolgoz(raw: bytes, nev: str, mime: str = "", *, get_db, most, kitol: str) -> dict:
    """Egy beérkezett fájl → {id, nev, cimke, szoveg, studio}. Olvashatatlan és a Studióba sem jutott fájlnál
    ValueError (olvasható üzenettel)."""
    if len(raw) > MAX_FAJL:
        raise ValueError(f"A fájl túl nagy ({len(raw) // (1024 * 1024)} MB, legfeljebb 50 MB).")
    fajta = yd._sniff(raw, nev)
    studio_feladat = asyncio.ensure_future(studioba(raw, nev))
    szoveg, cimke, megj = "", "", []
    if fajta == "image":
        jpeg = await asyncio.to_thread(yd.normalize_image, raw)     # forgatás, HEIC, EXIF/GPS törlése
        szoveg, cimke = await atir([jpeg], nev), "fénykép · átírva"
        studio = await studio_feladat
    else:
        studio = await studio_feladat
        if studio:
            try:
                szoveg = await _studio_szoveg(studio["file_id"])
                cimke = "Studio-kinyerés"
            except Exception as e:  # noqa: BLE001
                logger.warning("melleklet: Studio-kinyerés hiba (%s): %s", nev, str(e)[:200])
        if len((szoveg or "").strip()) < SZKENNELT_KUSZOB:
            try:
                doc = await asyncio.to_thread(yd.process_upload, raw, nev)
            except ValueError:
                if not studio:
                    raise
                doc = None
            if doc:
                szoveg, cimke, megj = doc.get("text") or "", doc["label"], list(doc.get("notes") or [])
                if doc.get("images") and len(szoveg.strip()) < SZKENNELT_KUSZOB:
                    szoveg, cimke = await atir(doc["images"], nev), f"{doc['label']} · átírva"
    szoveg = (szoveg or "").strip()
    if len(szoveg) > MAX_SZOVEG_KAR:
        megj.append(f"Hosszú — az első {MAX_SZOVEG_KAR} karakter van itt, a teljes fájl a Studióban.")
        szoveg = szoveg[:MAX_SZOVEG_KAR]
    if not szoveg:
        megj.append("A szövegét nem tudtam kinyerni — a Studio eszközeivel dolgozható fel.")
    uid = _tarol(get_db, nev, mime, raw, szoveg, kitol, most())
    m = {"id": uid, "nev": nev, "cimke": cimke or fajta, "szoveg": szoveg, "megjegyzesek": megj,
         "studio": {k: studio.get(k) for k in ("file_id", "edit_url")} if studio else None}
    _FRISS[uid] = (time.time(), m)
    for k, (t, _) in list(_FRISS.items()):
        if time.time() - t > FRISS_TTL_S:
            del _FRISS[k]
    logger.info("melleklet #%d %s: %s, %d karakter, Studio=%s", uid, nev, m["cimke"], len(szoveg),
                (studio or {}).get("file_id"))
    return m


def fejlec(m: dict) -> str:
    reszek = [m["cimke"]]
    if m.get("studio"):
        reszek.append(f"Studio file_id={m['studio']['file_id']}")
        if m["studio"].get("edit_url"):
            reszek.append(f"szerkesztő: {m['studio']['edit_url']}")
    return f"{JEL}{m['id']}: {m['nev']} — {' · '.join(reszek)}]"


def uzenet(keres: str, mellekletek: list[dict]) -> str:
    """A Feldwebelnek menő üzenet: a Kommandant kérése (vagy az alapkérés), alatta a mellékletek."""
    blokkok = []
    for m in mellekletek:
        b = fejlec(m)
        if m.get("megjegyzesek"):
            b += "\n(" + " ".join(m["megjegyzesek"]) + ")"
        if m.get("szoveg"):
            b += "\n" + m["szoveg"]
        blokkok.append(b)
    return ((keres or "").strip() or ALAP_KERES) + "\n\n" + "\n\n".join(blokkok)


def van_melleklet(szoveg: str) -> bool:
    return JEL in (szoveg or "")


def betolt(get_db, idk: list) -> list[dict]:
    """A webes felület feltöltött mellékletei azonosító alapján (előbb a friss tár, aztán az adatbázis)."""
    out = []
    for i in idk[:10]:
        try:
            i = int(i)
        except (TypeError, ValueError):
            continue
        if i in _FRISS:
            out.append(_FRISS[i][1])
            continue
        conn = get_db()
        try:
            r = conn.execute("SELECT id, filename, content_text FROM uploads WHERE id = ?", (i,)).fetchone()
        except Exception as e:  # noqa: BLE001 — olvasási hiba = nincs meg (a kliens újra feltölti)
            logger.warning("melleklet: #%s betöltése nem sikerült: %s", i, str(e)[:200])
            r = None
        finally:
            conn.close()
        if r:
            out.append({"id": r[0], "nev": r[1], "cimke": "feltöltve", "szoveg": r[2] or "", "megjegyzesek": [],
                        "studio": None})
    return out
