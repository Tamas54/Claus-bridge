"""Faber-továbbítás (2026-11-22): a Bridge az EGYETLEN Telegram-csatorna gazdája; a Faber (a Kommandant gépén fut)
innen veszi fel a neki szóló parancsokat és gombnyomásokat.

Miért így: a Faber és a Bridge ugyanazt a botot (claus_kommandant_bot) használta, a két getUpdates-lekérdező 409-cel
ütközött, a gombnyomás elveszett. A Kommandant: „nem akarok új telegrammot … Működjön.”

Biztonság: a /api/messages hitelesítés nélküli, bárki írhat a 'faber' címzettnek. Ezért minden továbbított tétel
HMAC-aláírást kap a bot tokenjéből képzett kulccsal (sha256("faber:" + token)) — ezt csak a Bridge és a Faber ismeri,
új titok vagy Railway-változó nem kell. A Faber csak aláírt tételt fogad el, Bridge-azonosítónként egyszer; a jóváhagyó
gomb ráadásul egyszer használatos kódot hordoz (mehet:<szám>:<kód>), amit a Faber a saját táblájában őriz.

SZÁNDÉK (2026-11-22): a Kommandant: „én szóban akarom elmondani neki, hogy mi a parancs - nem akarok ilyen / parancsokat.”
A `szandek(text)` a perjel NÉLKÜLI üzenetből ismeri fel, hogy a Fabernek szól-e (kívánság, jóváhagyás, elvetés,
visszavonás, állapot) — az olcsó szinten (Haiku 5.5, sémára kötött kimenet), kulcs nélkül vagy hibánál magyar
kulcsszavas heurisztikával. Kétes esetben `egyeb` → None: az üzenet a Bridge rendes beszélgetése marad.
A perjeles parancs és a gomb változatlanul működik.
"""
import hashlib
import hmac
import json
import logging
import re
from typing import Literal, Optional

from pydantic import BaseModel

logger = logging.getLogger("faber_relay")

PARANCSOK = ("/tanulj", "/faber", "/vissza")
GOMB = re.compile(r"^(mehet|megse):\d+:[A-Za-z0-9]{6,32}$")


def kulcs(token: str) -> bytes | None:
    return hashlib.sha256(("faber:" + token).encode()).digest() if token else None


def parancs_e(text: str) -> bool:
    return (text or "").strip().lower().startswith(PARANCSOK)


def gomb_e(adat: str) -> bool:
    return bool(GOMB.match(adat or ""))


def alair(k: bytes, tipus: str, adat: str, ts: str) -> str:
    """A 'faber' címzettű Bridge-üzenet törzse: {"torzs": <json>, "sig": <hmac hex>}."""
    torzs = json.dumps({"tipus": tipus, "adat": adat, "ts": ts}, ensure_ascii=False)
    return json.dumps({"torzs": torzs, "sig": hmac.new(k, torzs.encode(), hashlib.sha256).hexdigest()}, ensure_ascii=False)


def ellenoriz(k: bytes, uzenet: str) -> dict | None:
    """A Faber oldala (ugyanez a logika él a ~/faber/faber.py-ban): érvényes aláírásnál a törzs, különben None."""
    try:
        d = json.loads(uzenet)
        if not hmac.compare_digest(hmac.new(k, d["torzs"].encode(), hashlib.sha256).hexdigest(), d["sig"]):
            return None
        return json.loads(d["torzs"])
    except (ValueError, KeyError, TypeError):
        return None


def tovabbit(conn, token: str, tipus: str, adat: str, ts: str) -> int | None:
    """Aláírt üzenet a 'faber' címzettnek a Bridge messages táblájába; a Bridge-azonosító, vagy None, ha nincs token."""
    k = kulcs(token)
    if not k:
        return None
    cur = conn.execute(
        "INSERT INTO messages (timestamp, sender, recipient, subject, message, priority) "
        "VALUES (?, 'kommandant-telegram', 'faber', ?, ?, 'normal')",
        (ts, f"faber:{tipus}", alair(k, tipus, adat, ts)))
    mid = cur.lastrowid
    conn.execute("UPDATE messages SET thread_id = ? WHERE id = ?", (mid, mid))
    conn.commit()
    return mid


# ══ SZÁNDÉKFELISMERÉS (perjel nélkül) ═══════════════════════════════════

SZANDEKOK = ("kivansag", "jovahagy", "elvet", "vissza", "allapot", "egyeb")
REPOK = ("memoria-rag", "echolot", "lexonomics", "studio", "bridge")


class SzandekValasz(BaseModel):
    szandek: Literal["kivansag", "jovahagy", "elvet", "vissza", "allapot", "egyeb"]
    repo: Optional[Literal["memoria-rag", "echolot", "lexonomics", "studio", "bridge"]] = None
    kerelem: Optional[int] = None


#: Rövid prompt: egy szándékfelismerés pár száz bemeneti token, pár tucat kimeneti.
SZANDEK_PROMPT = (
    "A Kommandant egy Telegram-üzenetét osztályozod. A Faber a gépén futó ügynök: a saját alkalmazásainkat "
    "(memoria-rag, echolot, lexonomics, studio, bridge) tanítja — új képességet épít vagy hibát javít, kipróbálja, "
    "és jóváhagyásra vár. Szándékok:\n"
    "- kivansag: új képességet vagy javítást kér valamelyik alkalmazásba (nem egyszeri kérdés, adat vagy feladat)\n"
    "- jovahagy: Faber-kérelmet élesít („élesítsd”, „mehet”, „igen, csináld”)\n"
    "- elvet: Faber-kérelmet elvet („mégse”, „ne”)\n"
    "- vissza: élesített Faber-változást visszacsináltat („csináld vissza”)\n"
    "- allapot: a Faberről kérdez („mi van a faberrel?”, „mit tanultál?”)\n"
    "- egyeb: minden más (kérdés, beszélgetés, egyszeri feladat a Bridge-nek). Kétes esetben egyeb.\n"
    "repo: csak ha az üzenetből kiderül. kerelem: csak ha számot mond (pl. „a 3-ast élesítsd” → 3)."
)

_REPO_SZAVAK = (
    ("memoria-rag", ("memoria-rag", "memória-rag", "tudásbázis", "tudasbazis", "memória", "memoria")),
    ("echolot", ("echolot",)),
    ("lexonomics", ("lexonomics", "lexo")),
    ("studio", ("studio", "stúdió")),
    ("bridge", ("bridge",)),
)
_JOVAHAGY = ("élesítsd", "élesítheted", "élesíts", "mehet", "igen, csináld", "igen csináld", "jóváhagyom",
             "csináld meg")
_ELVET = ("mégse", "mégsem", "ne csináld", "elvetem", "vesd el", "nem kell")
_VISSZA = re.compile(r"csináld vissza|vond vissza|állítsd vissza|visszacsinál|visszavon|visszaállít")
_KIVANSAG_IGE = re.compile(r"tanuld|tanulj|javítsd|javíts ki|építs|csinálj|legyen|szeretném|kellene|tudjon")


def _kerelem_szam(t: str) -> int | None:
    """A kimondott kérelemszám: „#3”, „a 3-ast”, „3. kérelem”, „kérelem 3”."""
    for minta in (r"#\s*(\d{1,6})\b",                                               # „#3”
                  r"kérelem\s*#?\s*(\d{1,6})\b",                                    # „kérelem 3”
                  r"\b(\d{1,6})\s*[.-]?\s*(?:as|es|ös|os|ast|est|öst|ost|t)?\s*kérelm",  # „3. kérelmet”, „3-as kérelem”
                  r"\b(\d{1,6})\s*-\s*(?:as|es|ös|os|ast|est|öst|ost|t)\b"):        # „a 3-ast”, „a 12-t”
        m = re.search(minta, t.lower())
        if m:
            return int(m.group(1))
    return None


def _repo_szo(t: str) -> str | None:
    low = t.lower()
    for repo, szavak in _REPO_SZAVAK:
        if any(s in low for s in szavak):
            return repo
    return None


def _faber_jel(t: str) -> bool:
    """Kimondottan a Fabernek szól-e (a név, az „élesít” ige vagy egy kérelemszám)."""
    low = t.lower()
    return "faber" in low or "élesít" in low or _kerelem_szam(t) is not None


def heurisztika(t: str) -> str:
    """Kulcsszavas tartalék az LLM nélkül. Óvatos: kétes esetben `egyeb`."""
    low = t.lower().strip()
    szavak = re.findall(r"\w+", low)
    rovid = len(szavak) <= 6
    if _VISSZA.search(low):
        return "vissza"
    if ("faber" in low and re.search(r"mi van|hol tart|állapot|mit tanult|mit csinál|mi újság", low)) \
            or "mit tanultál" in low:
        return "allapot"
    if rovid and (any(s in low for s in _ELVET) or re.fullmatch(r"ne[.!]*", low)):
        return "elvet"
    if rovid and any(s in low for s in _JOVAHAGY):
        return "jovahagy"
    if (("faber" in low and _KIVANSAG_IGE.search(low))
            or re.match(r"(faber,?\s*)?(tanuld|tanulj) meg", low)):
        return "kivansag"
    return "egyeb"


async def szandek(text: str, *, llm=None, feldwebel_kerdezett: bool = False) -> dict | None:
    """A perjel nélküli Telegram-üzenet Faber-szándéka: `{"szandek", "szoveg", "repo", "kerelem"}`, vagy None,
    ha az üzenet a Bridge rendes beszélgetése (`egyeb`).

    `llm`: a strukturált hívó (alapból `llm_kapu.strukturalt`; a tesztek hamisat adnak). Ha nincs kulcs vagy a hívás
    hibázik, a `heurisztika` dönt.

    `feldwebel_kerdezett`: a Feldwebel az elmúlt percekben kérdezett valamit (pl. „posztoljam a kommentet?”). Ilyenkor
    egy puszta „mehet” / „mégse” NEKI felel, nem a Fabernek — csak a kimondott Faber-jel (név, „élesítsd”,
    kérelemszám) viszi át. Enélkül a Feldwebel jóváhagyásra váró lépései elakadnának.
    """
    t = (text or "").strip()
    if not t or t.startswith(("/", "@")):
        return None
    sz, repo, kerelem = None, None, None
    if llm is None:
        import llm_kapu
        llm = llm_kapu.strukturalt if llm_kapu.elerheto() else None
    if llm is not None:
        try:
            obj, err = await llm(SZANDEK_PROMPT, t, SzandekValasz, szint="olcso", max_tokens=200,
                                 purpose="szandek/telegram")
            if obj is not None:
                sz, repo, kerelem = obj.szandek, obj.repo, obj.kerelem
            else:
                logger.warning("szandek: az LLM nem adott választ (%s) — heurisztika", (err or {}).get("code"))
        except Exception as e:  # noqa: BLE001 — a felismerés hibája sosem állíthatja meg a Telegram-utat
            logger.warning("szandek: LLM-hiba (%s: %s) — heurisztika", type(e).__name__, e)
    if sz is None:
        sz = heurisztika(t)
    # Determinisztikus ellenőrzés: a kérelemszám csak akkor él, ha az üzenetben tényleg ott van.
    szam = _kerelem_szam(t)
    if kerelem is not None and str(kerelem) not in t:
        kerelem = None
    kerelem = kerelem if kerelem is not None else szam
    if sz not in ("jovahagy", "elvet", "vissza"):
        kerelem = None      # a kívánságban a szám tartalom („a 403-as hibát javítsd”), nem kérelemszám
    repo = repo or _repo_szo(t)
    if sz in ("jovahagy", "elvet") and feldwebel_kerdezett and not _faber_jel(t):
        return None
    if sz not in SZANDEKOK or sz == "egyeb":
        return None
    return {"szandek": sz, "szoveg": t, "repo": repo, "kerelem": kerelem}


NYUGTA = {
    "kivansag": "Rendben, szólok a Fabernek, hogy tanulja meg.",
    "jovahagy": "Rendben, szólok a Fabernek, hogy élesítse.",
    "elvet": "Rendben, szólok a Fabernek, hogy vesse el.",
    "vissza": "Rendben, szólok a Fabernek, hogy csinálja vissza.",
    "allapot": "Megkérdezem a Fabert, pár másodpercen belül jelentkezik.",
}


def szandek_adat(d: dict) -> str:
    """A `tipus="szandek"` tétel `adat`-ja: JSON-szöveg (a Faber `szandek(json.loads(adat))`-ként olvassa)."""
    return json.dumps({k: d.get(k) for k in ("szandek", "szoveg", "repo", "kerelem")}, ensure_ascii=False)
