"""PYRAMID-VITA (2026-11-23) — vita → parancs → végrehajtás, látható a kabinban.

A Kommandant (11-23 este): „KÉRDÉS MEGVITATÁSA!!! MEGVITATJÁK! ÁTGONDOLJÁK, ötleteket szereznek. … és AZTÁN hajtják
végre: ez a pyramid koncepcióm alapelve.” — „AKAROK vitázni, megbeszélni, ÁLLÁSPONTOKAT látni.” — „Van egy pont a
vitában, amikor jóváhagyok, és utasítok.”

Ami előtte volt (a hiba): a `start_discussion` / `add_to_discussion` EGY kört indított (`server._ai_auto_discuss`):
mind a nyolc SiliconFlow-modell párhuzamosan, egymást nem olvasva, 3–4 mondatban, eszköz nélkül szólt. A 18-as vitában
nyolc egyforma vélemény. Végrehajtás nem volt, a kabin hozzászólása senkit nem indított el.

A HUROK
-------
1. ÁLLÁSPONTOK (az R1 előtt): a moderátor a témából 2–4 valódi, egymással ÜTKÖZŐ álláspontot fogalmaz meg; minden
   résztvevő egyet kap és KÉPVISEL; az utolsó (a legellentmondóbb) gazdája az ÖRDÖGÜGYVÉD is.
2. VITA, több körben, EGYMÁS UTÁN: a résztvevők (alapból Kimi, DeepSeek, GLM) sorban szólnak, mindenki látja a teljes
   eddigi szálat, a sorrend körönként forog. Körök (alapból 3): R1 ÖTLETEK → R2 ÜTKÖZTETÉS → R3 ÖSSZEFÉSÜLÉS (top 3,
   első lépés, szavazás). Szólónként a meglévő korlátos eszközhurok (`server._rizs_eszkozhurok`: a közös
   SUBAGENT-eszközlista, a tiltólista és a kínai-őr érintetlen), legfeljebb `ESZKOZ_MAX` hívás.
3. ÁLLÁSPONT-TÉRKÉP minden kör után: résztvevőnként a jelenlegi álláspont, mi változott, a fő érv (a szóló maga adja a
   záró JSON-ban — determinisztikus), a Kommandant sora, és a moderátor összegzése: hol az egyetértés, hol a valódi
   vita; alul a Kommandanthoz intézett kérdések.
4. A KOMMANDANT VITAPARTNER: a hozzászólása a következő szólók promptjának TETEJÉRE kerül („A KOMMANDANT SZÓLT…”), és
   név szerint neki válaszolnak elsőként (vitatkozhatnak is vele). „Várj rám a körök között” (alapból BE): minden kör
   után megáll, és csak az ő hozzászólására vagy a Tovább gombra megy tovább.
5. HATÁROZATI JAVASLAT az utolsó kör után (a moderátor — a meglévő szintézis-lánc): feladatlista + nyitott kérdések.
   Ez NYERSANYAG: magától semmi nem fut.
6. PARANCS: a végrehajtás CSAK a Kommandant parancsára indul — bármikor, a vita közepén is. A parancs szövegéből (az ő
   szavai + a javaslat nyersanyagként; az ő utasítása mindig felülír) feladatlista lesz, „PARANCS #n” blokként azonnal
   a szálba kerül, és azonnal indul (házi szabály: jóváhagyás után nincs újabb ellenőrző kör). Végrehajtók — mind a
   MEGLÉVŐ úton: kutatás/elemzés/írás → `ai_task` (dispatch a kijelölt ügynöknek); ismétlődő → `feladat_ugynok`;
   kódolás/eszköz → Faber-kívánság (aláírt relay; a merge a Kommandanté); kifelé (poszt) → `action_drafts`-tervezet a
   saját jóváhagyó gombjával. Minden eredmény visszaíródik a szálba (✅ / ❌), a hurok zárul — a háttérben futó vagy
   jóváhagyásra váró tételé is (`figyel`): az ai_task vége, a Faber-kérelem sorsa (jóváhagyásra vár → élesítve /
   elvetve / bukott; a Bridge a Faber `faber:esemeny` / `faber:kapu` üzeneteiből tudja, a kérelmet a kívánság elejére
   tett jel köti a tételhez) és a tweet-tervezet utólagos döntése (kiposztolva / elvetve, az `action_drafts`-ból).
7. A parancs után a vita „végrehajtás alatt”; ha a Kommandant újra hozzászól („folytassátok”, új kérdés), új kör indul
   ugyanabban a szálban.

TÁROLÁS — HÁZI SZABÁLY: új tábla, új oszlop NINCS. A vita a meglévő `discussions` / `discussion_entries` táblában él;
a Pyramid-állapot (kör, sorrend, álláspontok, térkép, javaslat, parancsok, bejegyzés-metaadat) JSON-ként a
`discussions.resolution` mezőben (`{"pyramid": 1, …}`) — a régi viták `resolution`-je szöveg marad. Minden módosítás
egy rövid `BEGIN IMMEDIATE` tranzakció (olvas–módosít–ír), így a motor, a kabin gombjai és a végrehajtás-figyelők nem
írják felül egymást.

KÖLTSÉG ÉS BIZTONSÁG: `PYRAMID_VITA=0` → semmi nem indul (a régi egykörös vita marad). Plafonok: körszám
(`PYRAMID_VITA_KOROK`, legfeljebb 6), szólónként `MAX_TOKENS`, a kimeneti keret plafonja `PLAFON` (16k — a csonkolás
miatti duplázó létra eddig 131k-ig ment; ha a 16k-n is csonkol, KIMONDVA csonka marad), eszközhívás-plafon,
vitánként legfeljebb `MAX_SZOLAS` szólás, és a „Leállítom” gomb. A modellhívás a meglévő `sf_chat` / `llm_kapu` úton.

INDULÁSKOR (`ujraeleszt_mind`, a cron-szál egyszer hívja): a „fut” vita motorja és minden végrehajtás-figyelő
magától folytatódik — a kapcsoló és a plafonok tiszteletben tartásával (lásd ott).

A modul függőségeit a server.py köti be (`beallit`): get_db, szolo, moderator, vegrehajt, ai_task_allapot,
faber_allapot, draft_allapot, ertesit — tesztben hamisak.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone

logger = logging.getLogger("pyramid.vita")

# ══ BEÁLLÍTÁSOK ═══════════════════════════════════════════════════════════

NEVEK = {"kimi": "Kimi", "deepseek": "DeepSeek", "glm5": "GLM", "kommandant": "Kommandant",
         "moderator": "Moderátor", "pyramid": "Pyramid"}
#: a fő rizsrakéták (a Pyramid alapító dokumentuma: Kimi, DeepSeek, GLM) — a vita alap-résztvevői
ALAP_RESZTVEVOK = ("kimi", "deepseek", "glm5")
MAX_TOKENS = 6000          # szólónként kért kimeneti keret
PLAFON = 16000             # a csonkolás miatti duplázás teteje (sf_chat / llm_kapu)
ESZKOZ_MAX = 4             # szólónként legfeljebb ennyi eszközhívás
HOSSZ = 2500               # a hozzászólás kért terjedelme (karakter)
MAX_SZOLAS = 40            # vitánként összesen (költségvédelem)
MAX_FELADAT = 8            # parancsonként
SZAL_MAX = 60000           # a szóló promptjába kerülő szál felső határa (karakter)
FIGYELO_S = 20.0           # az ai_task-figyelő lekérdezési köze
FIGYELO_MAX_S = 3600.0     # utána a figyelő feladja (a feladat tovább futhat)
FIGYELO_LASSU_S = 60.0     # a Faber-kérelem és a tweet-tervezet figyelője (órákig / napokig is tarthat: a Kommandant dönt)
FIGYELO_HOSSZU_MAX_S = 14 * 24 * 3600.0
INDULASKOR_MAX = 3         # induláskor legfeljebb ennyi futó vita motorja indul magától (`PYRAMID_VITA_INDULASKOR_MAX`)
FOLYTAT_ORA = 72           # az ennél régebben frissült „fut” vita induláskor nem indul magától (`PYRAMID_VITA_FOLYTAT_ORA`)
UJ_KOR_KOROK = 1           # a parancs / lezárás utáni hozzászólás ennyi új kört indít

KOR_TIPUSOK = {
    "otletek": ("ÖTLETEK",
                "Hozz ÚJ ötleteket a saját álláspontodból — amit más már mondott, azt ne ismételd. Ha kell, nézz "
                "utána eszközzel, és add meg a forrást."),
    "utkoztetes": ("ÜTKÖZTETÉS",
                   "Nevezd meg NÉV SZERINT a többiek legerősebb és leggyengébb ötletét. Támadd a gyengét (miért nem "
                   "működik), javítsd és kombináld az erőset. Tartsd vagy váltsd az álláspontod — indokolva."),
    "osszefesules": ("ÖSSZEFÉSÜLÉS",
                     "Add meg a TOP 3 javaslatot; mindegyikhez az ELSŐ konkrét lépést: ki vagy melyik eszköz, "
                     "mikorra, mennyiért, mi a mérce. Aztán SZAVAZZ (melyikre és miért) — a záró JSON `szavazat` "
                     "mezőjében is."),
    "folytatas": ("FOLYTATÁS",
                  "Ha a Kommandant új hozzászólást vagy kérdést tett, arra reagálj ELSŐKÉNT (vitatkozhatsz is vele); "
                  "ha nem, a legerősebb nyitott ellenvetésre. Aztán frissítsd a TOP 3 javaslatot az első lépéssel, és "
                  "szavazz."),
}

VEGREHAJTOK = {
    "kutatas": "kutatás / elemzés / írás (ai_task a kijelölt ügynöknek)",
    "ismetlodo": "ismétlődő feladat (ütemezett feladat-ügynök)",
    "kodolas": "kódolás / eszközépítés (Faber-kívánság; a merge a Kommandanté)",
    "kifele": "kifelé menő (poszt-TERVEZET jóváhagyásra a munkapadon)",
}

ALLAPOT_NEV = {"fut": "fut", "var_ra": "várlak", "parancsra_var": "döntésre vár", "vegrehajtas": "végrehajtás",
               "kesz": "végrehajtva", "leallitva": "leállítva"}

#: A végrehajtás-figyelők: a tétel `ref`-jének kulcsa → a függőség, amely az állapotát adja. Az ai_task a feladat
#: azonosítójával, a Faber-kérelem a teljes ref-fel (a relay-azonosító és a tétel jele — a kérelem számát a figyelő
#: találja meg és jegyzi be), a tweet-tervezet az `action_drafts` azonosítójával.
FIGYELT = {"ai_task": "ai_task_allapot", "faber": "faber_allapot", "draft": "draft_allapot"}
VEGALLAPOTOK = ("kesz", "hibas", "elutasitva")

_DEPS: dict = {}
#: vita-id → a futó motor (asyncio.Task); feladat-kulcs → a futó ai_task-figyelő
_MOTOR: dict[int, asyncio.Task] = {}
_FIGYELO: dict[str, asyncio.Task] = {}
_PARANCS_FUT: dict[int, asyncio.Task] = {}


def beallit(**deps) -> None:
    """A Bridge bekötése (server.py):
      get_db()                                   → sqlite3-kapcsolat (Row)
      szolo(ki, system, user)            [async] → {szoveg, csonka, modell, eszkozok, jegyzetek, hiba}
      moderator(system, user, max_tokens)[async] → (szoveg, ki_irta | None)
      vegrehajt(feladat, kontextus)      [async] → {allapot: kesz|fut|hibas|jovahagyasra_var|kezi, szoveg, link?,
                                                    nezet?, ref?}
      ai_task_allapot(task_id)           [async] → {allapot: fut|kesz|hibas, szoveg, link?}
      faber_allapot(ref)                 [async] → {allapot: fut|jovahagyasra_var|kesz|hibas|elutasitva, szoveg,
                                                    ref?: {kerelem}}            (nem kötelező)
      draft_allapot(draft_id)            [async] → {allapot: jovahagyasra_var|kesz|elutasitva|hibas, szoveg, link?}
                                                                                  (nem kötelező)
      ertesit(szoveg)                    [async] → Telegram-jelzés a Kommandantnak (nem kötelező)"""
    _DEPS.clear()
    _DEPS.update(deps)


def _env_int(nev: str, alap: int, also: int, felso: int) -> int:
    try:
        return max(also, min(felso, int(os.environ.get(nev, "") or alap)))
    except ValueError:
        return alap


def bekapcsolva() -> bool:
    """`PYRAMID_VITA` (alapból BE). KI: semmi nem indul — se vita, se parancs."""
    return os.environ.get("PYRAMID_VITA", "1").strip().lower() not in ("0", "false", "off", "ki", "nem", "no")


def resztvevok_alap() -> list[str]:
    """`PYRAMID_VITA_RESZTVEVOK` (vesszővel), alapból a három fő rizsrakéta. A régi egykörös vita is ezt használja."""
    nyers = os.environ.get("PYRAMID_VITA_RESZTVEVOK", "") or ",".join(ALAP_RESZTVEVOK)
    out = []
    for r in nyers.split(","):
        r = r.strip().lower()
        if r and r not in out:
            out.append(r)
    return out or list(ALAP_RESZTVEVOK)


def korok_alap() -> int:
    return _env_int("PYRAMID_VITA_KOROK", 3, 1, 6)


def nev(ki: str) -> str:
    return NEVEK.get(ki, ki)


def _most() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db():
    return _DEPS["get_db"]()


# ══ TÁROLÁS (discussions.resolution = a Pyramid-állapot JSON-ja) ══════════

def allapot_ertelmez(resolution) -> dict | None:
    """A `discussions.resolution` → Pyramid-állapot, vagy None (régi vita: ott a resolution szöveg)."""
    if not resolution or not str(resolution).lstrip().startswith("{"):
        return None
    try:
        st = json.loads(resolution)
    except (TypeError, ValueError):
        return None
    return st if isinstance(st, dict) and st.get("pyramid") else None


def _vita_sor(conn, vid: int):
    return conn.execute("SELECT * FROM discussions WHERE id = ?", (int(vid),)).fetchone()


def betolt(vid: int) -> tuple[dict | None, dict | None]:
    """(állapot, a vita sora dict-ként) — nincs ilyen vita: (None, None)."""
    conn = _db()
    try:
        d = _vita_sor(conn, vid)
    finally:
        conn.close()
    if not d:
        return None, None
    d = dict(d)
    return allapot_ertelmez(d.get("resolution")), d


def bejegyzesek(vid: int) -> list[dict]:
    conn = _db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, instance, content, timestamp FROM discussion_entries WHERE discussion_id = ? "
            "ORDER BY timestamp, id", (int(vid),)).fetchall()]
    finally:
        conn.close()


def _tranzakcio(fn):
    """Rövid írási tranzakció (olvas–módosít–ír egyben): a motor, a kabin és a figyelők nem írják felül egymást."""
    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        r = fn(conn)
        conn.commit()
        return r
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ir(conn, vid: int, st: dict, **oszlop) -> None:
    st["frissitve"] = _most()
    sets = ["resolution = ?"] + [f"{k} = ?" for k in oszlop]
    conn.execute(f"UPDATE discussions SET {', '.join(sets)} WHERE id = ?",
                 (json.dumps(st, ensure_ascii=False), *oszlop.values(), int(vid)))


def modosit(vid: int, fn) -> dict | None:
    """`fn(st, conn)` módosítja az állapotot (helyben); → az új állapot (None: nem Pyramid-vita)."""
    def _t(conn):
        d = _vita_sor(conn, vid)
        st = allapot_ertelmez(d["resolution"]) if d else None
        if st is None:
            return None
        oszlop = fn(st, conn) or {}
        _ir(conn, vid, st, **oszlop)
        return st
    return _tranzakcio(_t)


def _bejegyez(conn, vid: int, st: dict, ki: str, szoveg: str, meta: dict) -> int:
    """Egy bejegyzés a szálba + a metaadata az állapotba (a hívó tranzakciójában)."""
    cur = conn.execute("INSERT INTO discussion_entries (discussion_id, instance, content, timestamp) "
                       "VALUES (?, ?, ?, ?)", (int(vid), ki, szoveg, _most()))
    eid = int(cur.lastrowid)
    st.setdefault("bej", {})[str(eid)] = meta
    return eid


def _uj_allapot(resztvevok=None, korok=None, varj_ram: bool = True) -> dict:
    rv = [r for r in (resztvevok or resztvevok_alap()) if r]
    k = max(1, min(6, int(korok or korok_alap())))
    return {"pyramid": 1, "allapot": "fut", "resztvevok": rv, "korok": k, "kor": 1, "szakasz": 1,
            "szakasz_kezdo": 1, "sorrend": sorrend(rv, 1), "kovetkezo": 0, "varj_ram": bool(varj_ram),
            "leallit": False, "allaspontok": [], "kiosztas": {}, "terkep": [], "kommandant_uj": [], "bej": {},
            "javaslat": None, "parancsok": [], "szolasok": 0, "hiba": None, "indult": _most()}


# ══ TISZTA LOGIKA (tesztelhető, hívás nélkül) ═════════════════════════════

def sorrend(resztvevok: list[str], kor: int) -> list[str]:
    """A kör szólási sorrendje: körönként egyet forog (R1: A B C, R2: B C A, R3: C A B)."""
    if not resztvevok:
        return []
    s = (int(kor) - 1) % len(resztvevok)
    return list(resztvevok[s:]) + list(resztvevok[:s])


def kor_tipus(st: dict, kor: int | None = None) -> str:
    """A kör fajtája: az első szakaszban ÖTLETEK → ÜTKÖZTETÉS… → ÖSSZEFÉSÜLÉS; a parancs / lezárás utáni
    szakaszban FOLYTATÁS."""
    kor = int(kor if kor is not None else st["kor"])
    if int(st.get("szakasz", 1)) > 1:
        return "folytatas"
    i = kor - int(st.get("szakasz_kezdo", 1))
    k = int(st["korok"])
    if i >= k - 1:
        return "osszefesules"
    return "otletek" if i == 0 else "utkoztetes"


def kor_cimke(st: dict, kor: int) -> str:
    return f"R{kor} · {KOR_TIPUSOK[kor_tipus(st, kor)][0]}"


def allaspont_szam(resztvevok: list[str]) -> int:
    """Hány álláspontot kérünk a moderátortól: résztvevőnként egyet, 2 és 4 között."""
    return max(2, min(4, len(resztvevok)))


def kiosztas(resztvevok: list[str], allaspontok: list[dict], vid: int = 0) -> dict:
    """Ki melyik álláspontot képviseli. Az utolsó (a legellentmondóbb) álláspont gazdája az ÖRDÖGÜGYVÉD is — vitánként
    más résztvevő (a vita száma szerint forog). A többiek sorban kapják a többit."""
    n, k = len(resztvevok), len(allaspontok)
    if not n or not k:
        return {}
    ordog = resztvevok[int(vid) % n] if n >= 2 else None
    tobbi = [r for r in resztvevok if r != ordog]
    out = {}
    elso = max(1, k - 1) if ordog else k
    for i, r in enumerate(tobbi):
        out[r] = {"allaspont": i % elso, "ordogugyved": False}
    if ordog:
        out[ordog] = {"allaspont": k - 1, "ordogugyved": True}
    return out


def betu(i: int) -> str:
    return "ABCDEFGH"[i] if 0 <= i < 8 else str(i + 1)


_JSON_BLOKK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def json_kinyer(szoveg: str) -> tuple[dict | None, str]:
    """(az utolsó JSON-objektum, a szöveg nélküle). Előbb a ```json blokk, aztán a szöveg végi {...}."""
    s = str(szoveg or "")
    blokkok = list(_JSON_BLOKK.finditer(s))
    for m in reversed(blokkok):
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj, (s[:m.start()] + s[m.end():]).strip()
    i, j = s.find("{"), s.rfind("}")
    while i != -1 and j > i:
        try:
            obj = json.loads(s[i:j + 1])
            if isinstance(obj, dict):
                return obj, (s[:i] + s[j + 1:]).strip()
        except ValueError:
            pass
        i = s.find("{", i + 1)
    return None, s.strip()


def _ekezet_nelkul(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


_VALTOZAS = {"tart": "tart", "marad": "tart", "erosit": "erősít", "enged": "enged", "valt": "vált", "uj": "új"}


def valtozas_norm(v) -> str:
    s = _ekezet_nelkul(v)
    for k, nv in _VALTOZAS.items():
        if k in s:
            return nv
    return "tart" if not s else str(v)[:20]


def szolo_allaspont(obj: dict | None) -> dict:
    """A szóló záró JSON-ja → a térkép sora (hiányzó mezőkkel is)."""
    o = obj if isinstance(obj, dict) else {}
    return {"allaspont": str(o.get("allaspont") or "").strip()[:400],
            "valtozas": valtozas_norm(o.get("valtozas")),
            "fo_erv": str(o.get("fo_erv") or o.get("fo_erve") or "").strip()[:300],
            "kerdes": str(o.get("kerdes") or "").strip()[:300],
            "szavazat": str(o.get("szavazat") or "").strip()[:200]}


_VEGREHAJTO_SZINONIMA = (
    ("kifele", ("kifele", "poszt", "tweet", "twitter", "level", "email", "e-mail", "kozzetet", "hirlevel")),
    ("ismetlodo", ("ismetlod", "utemez", "heti", "napi", "havi", "rendszeres", "hetfon", "cron", "ugynok")),
    ("kodolas", ("kodol", "kod", "faber", "eszkoz", "fejleszt", "program", "implement")),
    ("kutatas", ("kutat", "elemez", "elemzes", "iras", "ir", "ai_task", "osszefoglal")),
)


def vegrehajto_norm(v) -> str:
    s = _ekezet_nelkul(v)
    if s in VEGREHAJTOK:
        return s
    for kulcs, minta in _VEGREHAJTO_SZINONIMA:
        if any(m in s for m in minta):
            return kulcs
    return "kutatas"


def ki_norm(v, resztvevok: list[str]) -> str:
    s = _ekezet_nelkul(v).replace(" ", "")
    for r in list(resztvevok) + list(ALAP_RESZTVEVOK):
        if r and (r in s or (r == "glm5" and "glm" in s)):
            return r
    for m in ("sonnet", "haiku", "opus", "claude"):
        if m in s:
            return "sonnet" if m == "claude" else m
    return ""


def feladat_norm(f, resztvevok: list[str]) -> dict | None:
    """Egy feladat a moderátor / parser JSON-jából → a végrehajtható alak (vagy None, ha nincs mit)."""
    if not isinstance(f, dict):
        return None
    mit = " ".join(str(f.get("mit") or f.get("feladat") or "").split())[:600]
    if not mit:
        return None
    v = vegrehajto_norm(f.get("vegrehajto") or f.get("tipus") or mit)
    ki = ki_norm(f.get("ki") or "", resztvevok)
    if v == "kutatas" and ki not in ALAP_RESZTVEVOK and ki not in resztvevok:
        ki = (resztvevok or list(ALAP_RESZTVEVOK))[0]
    out = {"mit": mit, "vegrehajto": v, "ki": ki,
           "hatarido": str(f.get("hatarido") or "").strip()[:80],
           "merce": str(f.get("merce") or "").strip()[:300],
           "utemezes": " ".join(str(f.get("utemezes") or "").split())[:60],
           "tervezet": str(f.get("tervezet") or "").strip()[:1000],
           "nev": " ".join(str(f.get("nev") or "").split())[:60],
           "allapot": "var", "eredmeny": "", "link": "", "nezet": None, "ref": None}
    return out


#: A puszta jóváhagyás szavai: ha a parancsban ezeken kívül nincs semmi, a javaslat feladatai mennek (LLM nélkül).
_JOVAHAGYO_SZAVAK = frozenset((
    "mehet mehetnek igen ok oke okes rendben jo jol csinaljatok csinald hajra jovahagyom jovahagyva elfogadom legyen "
    "indulhat start kerem a az javaslat javaslatot javaslatnak szerint minden mind mindet mindegyik pontja pontjat "
    "pontjait pont pontok egeszet egesz igy ugy ahogy van ezt ezeket azt azokat es is hat persze").split())


def csak_jovahagyas(szoveg: str) -> bool:
    """A parancs puszta jóváhagyás-e („mehet”, „mehet a javaslat szerint, minden pontja”) — akkor a javaslat feladatai
    mennek, LLM nélkül (előbb determinisztikus). Ha bármi érdemi szó van benne („csináljátok így: X, Y”), az utasítás."""
    szavak = re.findall(r"[a-z0-9]+", _ekezet_nelkul(szoveg))
    return bool(szavak) and all(w in _JOVAHAGYO_SZAVAK for w in szavak)


def _rovid(s: str, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ══ A SZÓLÓ PROMPTJA ═══════════════════════════════════════════════════════

def _szal_szoveg(st: dict, entries: list[dict], kihagy=("terkep", "allaspontok", "rendszer")) -> str:
    """A szál a szólónak: címkézve (R-kör, név), a térkép- és álláspont-bejegyzések nélkül (azok külön mennek)."""
    sorok = []
    for e in entries:
        m = (st.get("bej") or {}).get(str(e["id"])) or {}
        if m.get("tipus") in kihagy:
            continue
        ki = e["instance"]
        cimke = f"R{m['kor']} · {nev(ki)}" if m.get("kor") and m.get("tipus") == "szolo" else nev(ki)
        sorok.append(f"[{cimke}]: {e['content']}")
    szoveg = "\n\n".join(sorok)
    if len(szoveg) > SZAL_MAX:
        eleje = sorok[0] if sorok else ""
        maradek = SZAL_MAX - len(eleje) - 200
        szoveg = eleje + "\n\n[… a szál korábbi része rövidítve …]\n\n" + szoveg[-maradek:]
    return szoveg


def allaspontok_szoveg(st: dict) -> str:
    gazdak: dict[int, list[str]] = {}
    for r, k in (st.get("kiosztas") or {}).items():
        gazdak.setdefault(int(k["allaspont"]), []).append(nev(r) + (" (ördögügyvéd)" if k.get("ordogugyved") else ""))
    return "\n".join(f"{betu(i)}: {a.get('cim', '')} — {a.get('allitas', '')} (képviseli: "
                     f"{', '.join(gazdak.get(i, [])) or 'senki'})" for i, a in enumerate(st.get("allaspontok") or []))


def kommandant_fuggo(st: dict, entries: list[dict]) -> list[dict]:
    """A Kommandant még meg nem válaszolt hozzászólásai (a kör végéig mindenki elsőként neki felel)."""
    ids = {int(k["id"]) for k in st.get("kommandant_uj") or []}
    return [e for e in entries if int(e["id"]) in ids]


def szolo_prompt(st: dict, d: dict, ki: str, entries: list[dict], modell: str = "") -> tuple[str, str]:
    """(system, user) egy szólónak."""
    k = (st.get("kiosztas") or {}).get(ki) or {}
    aa = st.get("allaspontok") or []
    a_i = int(k.get("allaspont", 0)) if aa else None
    szerep = ""
    if a_i is not None and a_i < len(aa):
        szerep = (f"A SZEREPED: a(z) „{betu(a_i)}: {aa[a_i].get('cim', '')}” álláspontot KÉPVISELED: "
                  f"{aa[a_i].get('allitas', '')}\n")
    if k.get("ordogugyved"):
        szerep += ("Te vagy az ÖRDÖGÜGYVÉD is: minden körben a legerősebb, épp kialakuló egyetértést támadd — keresd a "
                   "gyenge pontját, a rejtett költséget, a kockázatot, és a harmadik utat.\n")
    tipus = kor_tipus(st)
    kor_nev, kor_utasitas = KOR_TIPUSOK[tipus]
    system = (
        f"Te {nev(ki)} vagy{f' ({modell})' if modell else ''}, a Pyramid egyik kutatója: a Kommandant szervezetében "
        f"egy több körös VITÁBAN veszel részt ({', '.join(nev(r) for r in st['resztvevok'] if r != ki)} a többiek). "
        "A vita célja, hogy a Kommandant valódi, eltérő ÁLLÁSPONTOKAT lásson, ütköztetve — nem konszenzus-kórust.\n\n"
        + szerep +
        "\nSZABÁLYOK:\n"
        "- A saját álláspontodból érvelj. „Egyetértek” csak ellenérvvel vagy új elemmel együtt állhat.\n"
        "- Ha meggyőztek, kimondhatod, hogy álláspontot váltasz — de indokold: ki és mivel győzött meg.\n"
        "- Amikor másokra reagálsz, név szerint szólítsd meg őket.\n"
        "- Ha a Kommandant szólt, ELSŐKÉNT neki válaszolj, név szerint („Kommandant, …”). Vitatkozhatsz is vele: ha "
        "nem értesz egyet, mondd meg és indokold — ne bólogass.\n"
        "- Ha a Kommandanttól döntés vagy adat kell, kérdezd meg (a záró JSON `kerdes` mezőjében, egy mondatban).\n"
        f"- Tényt forrással. Ha friss adat kell, használd az eszközeidet (legfeljebb {ESZKOZ_MAX} hívás); számot "
        "ne találj ki.\n"
        f"- Terjedelem: legfeljebb kb. {HOSSZ} karakter. Magyarul.\n"
        "- A válasz VÉGÉN egy ```json blokk, pontosan ezekkel a mezőkkel:\n"
        '  {"allaspont": "a jelenlegi álláspontod 1–2 mondatban", "valtozas": "tart | erősít | enged | vált", '
        '"fo_erv": "a fő érved egy mondatban", "kerdes": "kérdés a Kommandanthoz, vagy üres", '
        '"szavazat": "melyik javaslatra szavazol (az összefésülő körben), vagy üres"}\n')
    reszek = []
    fuggo = kommandant_fuggo(st, entries)
    if fuggo:
        reszek.append("⚠️ A KOMMANDANT SZÓLT:\n" + "\n".join(f"„{e['content']}”" for e in fuggo)
                      + "\n— erre reagálj ELSŐKÉNT, név szerint (vitatkozhatsz is vele).\n")
    reszek.append(f"VITA #{d['id']}: {d['topic']}")
    if d.get("context"):
        reszek.append(f"KONTEXTUS:\n{_rovid(d['context'], 6000)}")
    if aa:
        reszek.append("AZ ÁLLÁSPONTOK:\n" + allaspontok_szoveg(st))
    terkep = (st.get("terkep") or [])
    if terkep:
        t = terkep[-1]
        reszek.append(f"AZ ELŐZŐ KÖR (R{t['kor']}) TÉRKÉPE — egyetértés: {'; '.join(t.get('egyetertes') or []) or '–'}"
                      f" · valódi vita: {'; '.join(t.get('vita') or []) or '–'}")
    reszek.append("EDDIGI SZÁL:\n" + (_szal_szoveg(st, entries) or "(még üres)"))
    reszek.append(f"MOST: R{st['kor']} · {kor_nev} — te következel. {kor_utasitas}")
    return system, "\n\n".join(reszek)


# ══ A KOMMANDANT MŰVELETEI ════════════════════════════════════════════════

def _hiba(szoveg: str) -> dict:
    return {"ok": False, "hiba": szoveg}


def letrehoz(tema: str, kontextus: str = "", kezdo: str = "", ki: str = "kommandant", *, resztvevok=None,
             korok=None, varj_ram: bool = True, indit: bool = True) -> dict:
    """Új Pyramid-vita. → {ok, vita_id}. A motor (álláspontok, R1) azonnal indul, ha `indit`."""
    if not bekapcsolva():
        return _hiba("a Pyramid-vita ki van kapcsolva (PYRAMID_VITA=0)")
    tema = " ".join(str(tema or "").split())[:300]
    if not tema:
        return _hiba("a vitának téma kell")
    st = _uj_allapot(resztvevok, korok, varj_ram)

    def _t(conn):
        cur = conn.execute("INSERT INTO discussions (topic, context, status, created_by, created_at) "
                           "VALUES (?, ?, 'open', ?, ?)", (tema, str(kontextus or "")[:20000], ki, _most()))
        vid = int(cur.lastrowid)
        if str(kezdo or "").strip():
            _bejegyez(conn, vid, st, ki, str(kezdo).strip()[:8000], {"tipus": "nyito"})
        _ir(conn, vid, st)
        return vid
    vid = _tranzakcio(_t)
    if indit:
        inditas(vid)
    return {"ok": True, "vita_id": vid, "allapot": st["allapot"]}


def atalakit(vid: int, *, korok=None, varj_ram: bool = True, indit: bool = True) -> dict:
    """Meglévő (régi, egykörös) vita → Pyramid-vita: a bejegyzések maradnak (a szál része), az R1 indul."""
    if not bekapcsolva():
        return _hiba("a Pyramid-vita ki van kapcsolva (PYRAMID_VITA=0)")

    def _t(conn):
        d = _vita_sor(conn, vid)
        if not d:
            return _hiba(f"nincs #{vid} vita")
        if allapot_ertelmez(d["resolution"]):
            return {"ok": True, "vita_id": int(vid), "mar_pyramid": True}
        st = _uj_allapot(None, korok, varj_ram)
        if d["resolution"]:
            st["regi_dontes"] = str(d["resolution"])[:4000]
        _ir(conn, vid, st, status="open", resolved_at=None)
        return {"ok": True, "vita_id": int(vid)}
    out = _tranzakcio(_t)
    if out.get("ok") and indit and not out.get("mar_pyramid"):
        inditas(int(vid))
    return out


def hozzaszol(vid: int, szoveg: str, ki: str = "kommandant") -> dict:
    """Hozzászólás egy Pyramid-vitához. A Kommandanté a következő szólók promptjának tetejére kerül; ha a motor a körök
    között vár rá, továbbmegy; ha a vita lezárult (döntésre vár / végrehajtás / kész), ÚJ kör indul ugyanabban a
    szálban. Más (cli-claus, web-claus) hozzászólása csak a szál része."""
    szoveg = str(szoveg or "").strip()
    if not szoveg:
        return _hiba("üres hozzászólás")
    kommandant = ki == "kommandant"
    teendo = {"indit": False}

    def fn(st, conn):
        eid = _bejegyez(conn, vid, st, ki, szoveg[:8000],
                        {"tipus": "kommandant" if kommandant else "kulso", "kor": st.get("kor")})
        teendo["eid"] = eid
        if not kommandant or not bekapcsolva():
            return {}
        st.setdefault("kommandant_uj", []).append({"id": eid, "lattak": 0})
        a = st["allapot"]
        if a == "var_ra":
            st["allapot"] = "fut"
            teendo["indit"] = True
        elif a in ("parancsra_var", "vegrehajtas", "kesz"):
            uj_szakasz(st)
            teendo["indit"] = True
            return {"status": "open", "resolved_at": None}
        elif a == "fut":
            teendo["indit"] = True          # ha a motor (újraindulás után) nem él, most elindul
        return {}
    st = modosit(vid, fn)
    if st is None:
        return _hiba("ez nem Pyramid-vita")
    if teendo["indit"]:
        inditas(vid)
    return {"ok": True, "vita_id": int(vid), "bejegyzes": teendo.get("eid"), "allapot": st["allapot"]}


def uj_szakasz(st: dict) -> None:
    """Új kör(ök) ugyanabban a szálban (a parancs / lezárás után, a Kommandant hozzászólására vagy a Tovább-ra)."""
    st["szakasz"] = int(st.get("szakasz", 1)) + 1
    st["kor"] = int(st["kor"]) + (1 if st.get("kovetkezo", 0) or st.get("kor_lezarva") else 0)
    st["szakasz_kezdo"] = st["kor"]
    st["korok"] = UJ_KOR_KOROK
    st["sorrend"] = sorrend(st["resztvevok"], st["kor"])
    st["kovetkezo"] = 0
    st["kor_lezarva"] = False
    st["allapot"] = "fut"
    st["leallit"] = False
    st["hiba"] = None


def tovabb(vid: int) -> dict:
    """„Tovább”: a körök között várakozó vagy leállított vita folytatása; lezárt vitán egy új kör."""
    if not bekapcsolva():
        return _hiba("a Pyramid-vita ki van kapcsolva (PYRAMID_VITA=0)")

    def fn(st, conn):
        a = st["allapot"]
        if a in ("var_ra", "leallitva", "fut"):
            st["allapot"] = "fut"
            st["leallit"] = False
            st["hiba"] = None
        elif a in ("parancsra_var", "vegrehajtas", "kesz"):
            uj_szakasz(st)
            return {"status": "open", "resolved_at": None}
        return {}
    st = modosit(vid, fn)
    if st is None:
        return _hiba("ez nem Pyramid-vita")
    inditas(vid)
    return {"ok": True, "vita_id": int(vid), "allapot": st["allapot"]}


def leallit(vid: int) -> dict:
    """„Leállítom” (költségvédelem): a motor megáll (a futó szóló hívása megszakad), az állapot megmarad — a Tovább
    onnan folytatja."""
    def fn(st, conn):
        if st["allapot"] in ("fut", "var_ra"):
            st["allapot"] = "leallitva"
        st["leallit"] = True
        _bejegyez(conn, vid, st, "pyramid", "⏹ A vitát a Kommandant leállította. (A Tovább gomb onnan folytatja.)",
                  {"tipus": "rendszer"})
        return {}
    st = modosit(vid, fn)
    if st is None:
        return _hiba("ez nem Pyramid-vita")
    megallit(vid)
    return {"ok": True, "vita_id": int(vid), "allapot": st["allapot"]}


def varj_ram(vid: int, be: bool) -> dict:
    def fn(st, conn):
        st["varj_ram"] = bool(be)
        return {}
    st = modosit(vid, fn)
    if st is None:
        return _hiba("ez nem Pyramid-vita")
    return {"ok": True, "vita_id": int(vid), "varj_ram": st["varj_ram"]}


def parancs(vid: int, szoveg: str) -> dict:
    """A KOMMANDANT PARANCSA: jóváhagy és utasít. A vita (ha fut) megáll, a parancs szövegéből (+ a javaslat
    nyersanyagként) feladatlista lesz — „PARANCS #n” a szálban —, és a végrehajtás azonnal indul."""
    if not bekapcsolva():
        return _hiba("a Pyramid-vita ki van kapcsolva (PYRAMID_VITA=0) — parancsból nem indul végrehajtás")
    szoveg = str(szoveg or "").strip()
    if not szoveg:
        return _hiba("üres parancs")
    teendo = {}

    def fn(st, conn):
        n = len(st.get("parancsok") or []) + 1
        eid = _bejegyez(conn, vid, st, "kommandant", f"⚡ PARANCS: {szoveg[:6000]}", {"tipus": "parancs_szoveg", "n": n})
        st.setdefault("parancsok", []).append({"n": n, "szoveg": szoveg[:6000], "ido": _most(), "bejegyzes": eid,
                                               "allapot": "ertelmez", "feladatok": [], "blokk": None})
        st["allapot"] = "vegrehajtas"
        st["leallit"] = True                  # a motor a következő lépésnél kilép (és meg is szakítjuk)
        # a parancs válasz is: ami a Kommandanttól függőben volt, azt a parancs lezárja
        st["kommandant_uj"] = []
        teendo["n"] = n
        return {}
    st = modosit(vid, fn)
    if st is None:
        return _hiba("ez nem Pyramid-vita")
    megallit(vid)
    n = teendo["n"]
    _hatter(_PARANCS_FUT, vid, parancs_feldolgoz(vid, n))
    return {"ok": True, "vita_id": int(vid), "parancs": n, "allapot": st["allapot"]}


# ══ A MOTOR ═══════════════════════════════════════════════════════════════

def _hatter(tar: dict, kulcs, coro) -> asyncio.Task | None:
    """Háttérfeladat ÚJ kontextusban (egy webes kör kimenet-elkapója ne nyelje el a Telegram-jelzéseit)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        logger.warning("pyramid-vita: nincs futó eseményhurok — a háttérfeladat (%s) nem indult", kulcs)
        return None
    task = loop.create_task(coro, context=contextvars.Context())
    tar[kulcs] = task

    def _vege(t, kulcs=kulcs):
        if tar.get(kulcs) is t:
            tar.pop(kulcs, None)
        if not t.cancelled() and t.exception() is not None:
            logger.error("pyramid-vita háttérfeladat (%s) hibával állt meg: %s", kulcs, t.exception())
    task.add_done_callback(_vege)
    return task


def fut_e(vid: int) -> bool:
    t = _MOTOR.get(int(vid))
    return bool(t and not t.done())


def inditas(vid: int) -> bool:
    """A motor indítása (idempotens: ha már fut, nem indul másodszor)."""
    if not bekapcsolva() or fut_e(vid) or not (_DEPS.get("szolo") and _DEPS.get("moderator")):
        return False
    return _hatter(_MOTOR, int(vid), motor(int(vid))) is not None


def megallit(vid: int) -> bool:
    t = _MOTOR.get(int(vid))
    if t and not t.done():
        try:
            if t is asyncio.current_task():
                return False
        except RuntimeError:
            pass
        try:
            fo = asyncio.get_running_loop()
        except RuntimeError:
            fo = None
        if fo is t.get_loop():
            t.cancel()
        else:       # más szál eseményhurkán fut (pl. az induláskor folytatott vita a cron-szálon): szálbiztosan
            t.get_loop().call_soon_threadsafe(t.cancel)
        return True
    return False


async def _ertesit(szoveg: str) -> None:
    f = _DEPS.get("ertesit")
    if not f:
        return
    try:
        await f(szoveg)
    except Exception as e:  # noqa: BLE001 — a jelzés hibája a vitát nem állítja meg
        logger.warning("pyramid-vita: a Telegram-jelzés nem ment ki: %s", e)


async def motor(vid: int) -> None:
    """A vita egy szakaszát viszi: álláspontok → körök (szólók sorban) → térkép → (várj rám) → javaslat."""
    hibak = 0
    while True:
        if not bekapcsolva():
            return
        st, d = betolt(vid)
        if not st or st.get("leallit") or st["allapot"] != "fut":
            return
        try:
            if not st.get("allaspontok"):
                await allaspontok_keszit(vid, st, d)
                continue
            if st.get("kor_lezarva") or int(st["kovetkezo"]) >= len(st["sorrend"]):
                if not await kor_vege(vid):
                    return
                continue
            if int(st.get("szolasok", 0)) >= MAX_SZOLAS:
                _rendszer(vid, f"⏹ A vita elérte a szólás-plafont ({MAX_SZOLAS}) — leállt. (Költségvédelem; a Tovább "
                               f"gomb nem lép túl rajta, adj parancsot.)", allapot="parancsra_var")
                return
            ok = await szol(vid, st, d)
            hibak = 0 if ok else hibak + 1
            if hibak >= max(2, len(st["resztvevok"])):
                _rendszer(vid, "⚠️ A vita megállt: egymás után több szóló sem tudott hozzászólni (szolgáltató-hiba). "
                               "A Tovább gomb újrapróbálja.", allapot="leallitva", hiba="szolok_hibaja")
                await _ertesit(f"⚠️ A #{vid} vita megállt: a szólók nem érhetők el. A kabinban a Tovább újrapróbálja.")
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — egy váratlan hiba ne hagyja „fut” állapotban
            logger.exception("pyramid-vita #%d: a motor hibával megállt", vid)
            _rendszer(vid, f"⚠️ A vita megállt: {type(e).__name__}: {str(e)[:300]}. A Tovább gomb újrapróbálja.",
                      allapot="leallitva", hiba=str(e)[:300])
            return


def _rendszer(vid: int, szoveg: str, *, allapot: str | None = None, hiba: str | None = None) -> None:
    def fn(st, conn):
        _bejegyez(conn, vid, st, "pyramid", szoveg, {"tipus": "rendszer"})
        if allapot:
            st["allapot"] = allapot
        if hiba is not None:
            st["hiba"] = hiba
        return {}
    modosit(vid, fn)


ALLASPONT_SYSTEM = (
    "Te a Pyramid-vita MODERÁTORA vagy. Feladatod: a témából {k} VALÓDI, egymással ÜTKÖZŐ álláspontot fogalmazni — "
    "nem árnyalatokat, hanem olyanokat, amelyek mást javasolnak TENNI (pl. „1-gyel kezdjünk” / „2-vel kezdjünk” / "
    "„egyik sem: előbb X”). Az UTOLSÓ legyen a legellentmondóbb, a többség ellen menő. Magyarul. "
    'Csak JSON-t adj vissza: {{"allaspontok": [{{"cim": "legfeljebb 8 szó", "allitas": "1–2 mondat: mit tegyünk és '
    'miért"}}]}}')


async def allaspontok_keszit(vid: int, st: dict, d: dict) -> None:
    """Az R1 előtt: 2–4 ütköző álláspont a moderátortól, kiosztás (ördögügyvéddel), és egy bejegyzés a szálba."""
    k = allaspont_szam(st["resztvevok"])
    entries = bejegyzesek(vid)
    user = (f"A VITA TÉMÁJA: {d['topic']}\n\nKONTEXTUS:\n{_rovid(d.get('context') or '–', 6000)}\n\n"
            f"AZ EDDIGI SZÁL:\n{_rovid(_szal_szoveg(st, entries), 12000) or '(üres)'}")
    lista = []
    try:
        szoveg, _ki = await _DEPS["moderator"](ALLASPONT_SYSTEM.format(k=k), user, 2500)
        obj, _ = json_kinyer(szoveg)
        for a in (obj or {}).get("allaspontok") or []:
            if isinstance(a, dict) and str(a.get("allitas") or a.get("cim") or "").strip():
                lista.append({"cim": _rovid(a.get("cim") or a.get("allitas"), 80),
                              "allitas": _rovid(a.get("allitas") or a.get("cim"), 400)})
    except Exception as e:  # noqa: BLE001
        logger.warning("pyramid-vita #%d: az álláspontok moderátora hibázott: %s", vid, e)
    tartalek = not lista
    if tartalek:   # determinisztikus tartalék: a vita akkor is ütköző szerepekkel indul
        lista = [{"cim": "Mellette — a legerősebb változat", "allitas": "A felvetett irány mellett érvel, és a "
                  "legjobb formáját keresi."},
                 {"cim": "Másik út", "allitas": "Egy konkrét alternatív irányt képvisel a felvetett helyett."},
                 {"cim": "Egyik sem: előbb mérjünk", "allitas": "Szerinte döntés előtt adat kell: előbb egy olcsó "
                  "próba vagy mérés, és csak utána választás."}]
    lista = lista[:4] if len(lista) >= 2 else lista + [{"cim": "Egyik sem", "allitas": "Előbb mérni kell."}]

    def fn(st2, conn):
        if st2.get("allaspontok"):
            return {}
        st2["allaspontok"] = lista
        st2["kiosztas"] = kiosztas(st2["resztvevok"], lista, vid)
        szoveg = ("🎯 **AZ ÁLLÁSPONTOK** (a moderátor osztotta ki — mindenki a sajátját képviseli)"
                  + (" — tartalék kiosztás, a moderátor most nem válaszolt" if tartalek else "") + "\n\n"
                  + "\n".join(f"- **{betu(i)}: {a['cim']}** — {a['allitas']}" for i, a in enumerate(lista)) + "\n\n"
                  + "\n".join(f"- {nev(r)}: {betu(k['allaspont'])}" + (" · ördögügyvéd" if k.get("ordogugyved") else "")
                              for r, k in st2["kiosztas"].items()))
        _bejegyez(conn, vid, st2, "moderator", szoveg, {"tipus": "allaspontok"})
        return {}
    modosit(vid, fn)


def _csonka(r: dict) -> bool:
    return bool(r.get("csonka")) or any("csonkolt" in str(n) for n in r.get("jegyzetek") or [])


async def szol(vid: int, st: dict, d: dict) -> bool:
    """A soron következő szóló: prompt → hívás → bejegyzés + térkép-sor → léptetés. → sikerült-e."""
    idx, kor = int(st["kovetkezo"]), int(st["kor"])
    ki = st["sorrend"][idx]
    entries = bejegyzesek(vid)
    latott = [int(e["id"]) for e in kommandant_fuggo(st, entries)]
    system, user = szolo_prompt(st, d, ki, entries)
    r = await _DEPS["szolo"](ki, system, user)
    r = r if isinstance(r, dict) else {}
    hiba = r.get("hiba")
    szoveg = str(r.get("szoveg") or "").strip()
    obj, latszo = json_kinyer(szoveg) if szoveg else (None, "")
    sor = szolo_allaspont(obj)
    csonka = _csonka(r)
    cimke = kor_cimke(st, kor)

    def fn(st2, conn):
        if int(st2["kor"]) != kor or int(st2["kovetkezo"]) != idx:
            return {}                        # közben más léptette (pl. Tovább kétszer) — nem írunk kétszer
        if hiba is not None or not latszo:
            ok = (hiba or {}).get("code") if isinstance(hiba, dict) else hiba
            _bejegyez(conn, vid, st2, "pyramid", f"⚠️ {cimke} · {nev(ki)} most nem szólt ({ok or 'üres válasz'}).",
                      {"tipus": "rendszer", "kor": kor})
        else:
            fej = f"**{cimke}**" + (" · ⚠️ CSONKA (a válasz a kimeneti plafonon is csonkolt)" if csonka else "")
            lab = []
            if r.get("eszkozok"):
                lab.append("eszközök: " + ", ".join(r["eszkozok"]))
            for n in r.get("jegyzetek") or []:
                if any(k in str(n) for k in ("KIESÉS", "MODELLVÁLTÁS", "csonkolt")):
                    lab.append(str(n)[:200])
            tartalom = fej + "\n\n" + latszo[:HOSSZ * 3] + (("\n\n_" + " · ".join(lab) + "_") if lab else "")
            eid = _bejegyez(conn, vid, st2, ki, tartalom,
                            {"tipus": "szolo", "kor": kor, "cimke": cimke, "csonka": csonka,
                             "modell": r.get("modell") or "", "eszkozok": r.get("eszkozok") or []})
            st2.setdefault("allasok", {}).setdefault(str(kor), {})[ki] = {**sor, "bejegyzes": eid}
            st2["szolasok"] = int(st2.get("szolasok", 0)) + 1
        for k in st2.get("kommandant_uj") or []:
            if int(k["id"]) in latott:
                k["lattak"] = int(k.get("lattak", 0)) + 1
        st2["kovetkezo"] = idx + 1
        return {}
    modosit(vid, fn)
    return hiba is None and bool(latszo)


KOR_SYSTEM = (
    "Te a Pyramid-vita MODERÁTORA vagy. A kör után összegzed az ÁLLÁSPONT-TÉRKÉPET: hol az EGYETÉRTÉS, és hol a "
    "VALÓDI VITA (konkrétan: kik között, miben). Ha a Kommandant szólt, az ő álláspontját is 1–2 mondatban. Ne simítsd "
    "el a különbségeket. Magyarul. Csak JSON-t adj vissza: "
    '{"egyetertes": ["…"], "vita": ["… (ki vs. ki)"], "kommandant": "1–2 mondat, vagy üres"}')


async def kor_vege(vid: int) -> bool:
    """A kör lezárása: térkép (szólók sorai + Kommandant + moderátor-összegzés), aztán következő kör / várakozás /
    javaslat. → folytatódjon-e a motor."""
    st, d = betolt(vid)
    if not st or st["allapot"] != "fut":
        return False
    kor = int(st["kor"])
    if not st.get("kor_lezarva"):
        entries = bejegyzesek(vid)
        bej = st.get("bej") or {}
        e_kor = [e for e in entries if (bej.get(str(e["id"])) or {}).get("kor") == kor]
        kom = [e for e in e_kor if e["instance"] == "kommandant"]
        osszeg = {}
        try:
            szoveg, _ = await _DEPS["moderator"](KOR_SYSTEM, (
                f"VITA: {d['topic']}\nAZ ÁLLÁSPONTOK:\n{allaspontok_szoveg(st)}\n\nA(Z) R{kor} KÖR:\n"
                + _rovid(_szal_szoveg(st, e_kor), 30000)), 1500)
            osszeg, _ = json_kinyer(szoveg)
        except Exception as e:  # noqa: BLE001
            logger.warning("pyramid-vita #%d: a kör-összegzés elmaradt: %s", vid, e)
        osszeg = osszeg or {}
        allasok = (st.get("allasok") or {}).get(str(kor)) or {}
        sorok = {ki: allasok[ki] for ki in st["resztvevok"] if ki in allasok}
        if kom:
            sorok["kommandant"] = {"allaspont": _rovid(osszeg.get("kommandant") or kom[-1]["content"], 300),
                                   "valtozas": "új", "fo_erv": "", "kerdes": "", "szavazat": ""}
        else:   # a Kommandant álláspontja a térképen marad, amíg nem szól újra
            elozo = next((t["sorok"]["kommandant"] for t in reversed(st.get("terkep") or [])
                          if "kommandant" in (t.get("sorok") or {})), None)
            if elozo:
                sorok["kommandant"] = {**elozo, "valtozas": "tart"}
        kerdesek = [{"ki": ki, "kerdes": s["kerdes"]} for ki, s in sorok.items() if s.get("kerdes")]
        terkep = {"kor": kor, "cimke": kor_cimke(st, kor), "sorok": sorok,
                  "egyetertes": [_rovid(x, 300) for x in (osszeg.get("egyetertes") or []) if str(x).strip()][:5],
                  "vita": [_rovid(x, 300) for x in (osszeg.get("vita") or []) if str(x).strip()][:5],
                  "kerdesek": kerdesek}

        def fn(st2, conn):
            if int(st2["kor"]) != kor or st2.get("kor_lezarva"):
                return {}
            st2.setdefault("terkep", []).append(terkep)
            st2["kommandant_uj"] = [k for k in st2.get("kommandant_uj") or [] if not int(k.get("lattak", 0))]
            st2["kor_lezarva"] = True
            _bejegyez(conn, vid, st2, "moderator", terkep_markdown(terkep), {"tipus": "terkep", "kor": kor})
            return {}
        modosit(vid, fn)
    st, d = betolt(vid)
    if not st or st["allapot"] != "fut":
        return False
    utolso = int(st["kor"]) - int(st["szakasz_kezdo"]) + 1 >= int(st["korok"])
    if utolso:
        await javaslat_keszit(vid)
        return False
    def lep(st2, conn):
        if int(st2["kor"]) != kor or st2["allapot"] != "fut":
            return {}
        st2["kor"] = kor + 1
        st2["sorrend"] = sorrend(st2["resztvevok"], kor + 1)
        st2["kovetkezo"] = 0
        st2["kor_lezarva"] = False
        # ha a Kommandant a kör vége óta (még senki által nem látott) hozzászólt, az maga a „tovább”
        if st2.get("varj_ram") and not st2.get("kommandant_uj"):
            st2["allapot"] = "var_ra"
        return {}
    st = modosit(vid, lep)
    if st and st["allapot"] == "var_ra":
        await _ertesit(f"🗣 A #{vid} vita ({_rovid(d['topic'], 80)}) R{kor} köre kész — várlak a kabinban: szólj "
                       f"hozzá, vagy nyomd meg a Tovább gombot. (A parancsod bármikor indítja a végrehajtást.)")
        return False
    return True


def terkep_markdown(t: dict) -> str:
    sorok = [f"🗺 **ÁLLÁSPONT-TÉRKÉP — {t.get('cimke') or 'R' + str(t['kor'])} után**", ""]
    for ki, s in (t.get("sorok") or {}).items():
        sorok.append(f"- **{nev(ki)}** ({s.get('valtozas') or 'tart'}): {s.get('allaspont') or '–'}"
                     + (f" — fő érv: {s['fo_erv']}" if s.get("fo_erv") else "")
                     + (f" — szavazat: {s['szavazat']}" if s.get("szavazat") else ""))
    if t.get("egyetertes"):
        sorok.append("\n**Egyetértés:** " + "; ".join(t["egyetertes"]))
    if t.get("vita"):
        sorok.append("**Valódi vita:** " + "; ".join(t["vita"]))
    for k in t.get("kerdesek") or []:
        sorok.append(f"❓ **Kérdés a Kommandanthoz** ({nev(k['ki'])}): {k['kerdes']}")
    return "\n".join(sorok)


JAVASLAT_SYSTEM = (
    "Te a Pyramid-vita MODERÁTORA vagy. HATÁROZATI JAVASLATOT írsz a Kommandantnak a vita alapján — ez NYERSANYAG a "
    "parancsához: ő dönt, és amíg nem ad parancsot, semmi nem fut. Légy konkrét, ne simítsd el a vitát: ha nincs "
    "egyetértés, mondd ki, és tedd a nyitott kérdések közé.\n"
    "Végrehajtók: kutatas (kutatás / elemzés / írás egy ügynöknek: kimi | deepseek | glm5), ismetlodo (ütemezett "
    "feladat-ügynök; `utemezes` cron-kifejezés Budapest-idő szerint, pl. „0 8 * * 1” = hétfőnként 8:00), kodolas (a "
    "Faber: kód, eszköz — a merge a Kommandanté), kifele (poszt TERVEZET jóváhagyásra; a `tervezet` mezőben a kész, "
    "legfeljebb 280 karakteres szöveg).\n"
    "Formátum: előbb legfeljebb 5 mondatos összefoglaló (mire jutottunk, hol maradt vita), utána egy ```json blokk:\n"
    '{"feladatok": [{"mit": "…", "vegrehajto": "kutatas|ismetlodo|kodolas|kifele", "ki": "kimi|deepseek|glm5|", '
    '"hatarido": "…", "merce": "miből látszik, hogy kész / sikeres", "utemezes": "", "tervezet": ""}], '
    '"kerdesek": ["nyitott kérdés a Kommandanthoz"]}\n'
    "Legfeljebb 6 feladat. Magyarul.")


async def javaslat_keszit(vid: int) -> None:
    """Az utolsó kör után: határozati javaslat (feladatlista + nyitott kérdések) → „döntésre vár”."""
    st, d = betolt(vid)
    if not st:
        return
    entries = bejegyzesek(vid)
    user = (f"VITA #{vid}: {d['topic']}\nKONTEXTUS: {_rovid(d.get('context') or '–', 4000)}\n\nAZ ÁLLÁSPONTOK:\n"
            f"{allaspontok_szoveg(st)}\n\nA TELJES SZÁL:\n{_rovid(_szal_szoveg(st, entries, kihagy=('allaspontok',)), 50000)}")
    osszefoglalo, feladatok, kerdesek, ki_irta = "", [], [], None
    try:
        szoveg, ki_irta = await _DEPS["moderator"](JAVASLAT_SYSTEM, user, 4000)
        obj, osszefoglalo = json_kinyer(szoveg)
        obj = obj or {}
        feladatok = [f for f in (feladat_norm(x, st["resztvevok"]) for x in obj.get("feladatok") or []) if f][:6]
        kerdesek = [_rovid(k, 300) for k in obj.get("kerdesek") or [] if str(k).strip()][:6]
    except Exception as e:  # noqa: BLE001
        logger.warning("pyramid-vita #%d: a javaslat elmaradt: %s", vid, e)
    javaslat = {"osszefoglalo": _rovid(osszefoglalo, 2500), "feladatok": feladatok, "kerdesek": kerdesek,
                "kor": st["kor"], "ki_irta": ki_irta, "ido": _most()}

    def fn(st2, conn):
        st2["javaslat"] = javaslat
        if st2["allapot"] == "fut":
            st2["allapot"] = "parancsra_var"
        if not (feladatok or osszefoglalo):
            _bejegyez(conn, vid, st2, "moderator", "⚠️ A moderátor most nem tudott határozati javaslatot írni — adj "
                      "parancsot a vita alapján (a szavaidból lesz a feladatlista).", {"tipus": "javaslat"})
        else:
            _bejegyez(conn, vid, st2, "moderator", javaslat_markdown(javaslat), {"tipus": "javaslat"})
        return {}
    modosit(vid, fn)
    await _ertesit(f"📜 A #{vid} vita ({_rovid(d['topic'], 80)}) a végére ért — a határozati javaslat a kabinban. "
                   f"Döntésre vár: a parancsod indítja a végrehajtást.")


def _feladat_sor(i: int, f: dict) -> str:
    v = {"kutatas": "kutatás", "ismetlodo": "ismétlődő", "kodolas": "kódolás · Faber", "kifele": "kifelé · tervezet"}
    ki = f" · {nev(f['ki'])}" if f.get("ki") else ""
    return (f"{i}. [{v.get(f['vegrehajto'], f['vegrehajto'])}{ki}] {f['mit']}"
            + (f" — határidő: {f['hatarido']}" if f.get("hatarido") else "")
            + (f" — mérce: {f['merce']}" if f.get("merce") else "")
            + (f" — ütemezés: `{f['utemezes']}`" if f.get("utemezes") else ""))


def javaslat_markdown(j: dict) -> str:
    s = ["📜 **HATÁROZATI JAVASLAT** (nyersanyag a parancsodhoz — magától semmi nem fut)", ""]
    if j.get("osszefoglalo"):
        s += [j["osszefoglalo"], ""]
    if j.get("feladatok"):
        s += ["**Feladatok:**"] + [_feladat_sor(i, f) for i, f in enumerate(j["feladatok"], 1)]
    if j.get("kerdesek"):
        s += ["", "**Nyitott kérdések a Kommandanthoz:**"] + [f"- {k}" for k in j["kerdesek"]]
    return "\n".join(s)


# ══ PARANCS → FELADATLISTA → VÉGREHAJTÁS ══════════════════════════════════

PARANCS_SYSTEM = (
    "A KOMMANDANT PARANCSÁT bontod végrehajtható feladatlistává. A PARANCS A MÉRVADÓ: amit ő mond, az számít; a "
    "moderátor javaslata csak nyersanyag — ha ő mást mond, az övé. Csak azt vedd fel, amit elrendelt; ha azt mondja, "
    "„mehet a javaslat” / „így csináljátok”, a javaslat feladatait. Amit ő megnevez (ki, mit, mikorra), azt szó "
    "szerint vedd át; ahol nem mond semmit, a javaslatból vagy józan észből töltsd ki.\n"
    "Végrehajtók: kutatas (kutatás / elemzés / írás egy ügynöknek: kimi | deepseek | glm5), ismetlodo (ütemezett "
    "feladat-ügynök; `utemezes` cron Budapest-idő szerint, pl. „0 8 * * 1” = hétfőnként 8:00; `nev` rövid név; `ki` "
    "lehet kimi | deepseek | glm5 | sonnet | haiku | opus), kodolas (a Faber: kód, eszköz), kifele (poszt / levél "
    "TERVEZETE jóváhagyásra; a `tervezet` mezőben a kész szöveg, tweetnél legfeljebb 280 karakter).\n"
    "Minden tételhez: mit, végrehajtó, határidő, mérce. Legfeljebb 8 tétel. Magyarul. Csak JSON-t adj vissza:\n"
    '{"feladatok": [{"mit": "…", "vegrehajto": "kutatas|ismetlodo|kodolas|kifele", "ki": "", "hatarido": "", '
    '"merce": "", "utemezes": "", "tervezet": "", "nev": ""}], "megjegyzes": ""}')


async def parancs_feldolgoz(vid: int, n: int) -> None:
    """A parancs → feladatlista (az ő szavai felülírnak; puszta „mehet” → a javaslat, LLM nélkül) → PARANCS #n blokk a
    szálban → azonnali végrehajtás, tételenként állapottal és visszajelzéssel."""
    st, d = betolt(vid)
    if not st:
        return
    p = next((x for x in st.get("parancsok") or [] if int(x["n"]) == int(n)), None)
    if not p:
        return
    javaslat = st.get("javaslat") or {}
    feladatok: list[dict] = []
    forras = "parancs"
    if csak_jovahagyas(p["szoveg"]) and javaslat.get("feladatok"):
        feladatok = [dict(f) for f in javaslat["feladatok"]]
        forras = "javaslat"
    else:
        entries = bejegyzesek(vid)
        terkep = (st.get("terkep") or [])[-1:] or []
        user = (f"A KOMMANDANT PARANCSA (mérvadó):\n„{p['szoveg']}”\n\n"
                f"VITA #{vid}: {d['topic']}\n"
                + (f"\nA MODERÁTOR JAVASLATA (nyersanyag):\n{javaslat_markdown(javaslat)}\n" if javaslat else
                   f"\n(Határozati javaslat még nincs — a vita eddigi része nyersanyagként:)\n"
                   f"{_rovid(_szal_szoveg(st, entries), 20000)}\n")
                + (f"\nAZ UTOLSÓ TÉRKÉP:\n{terkep_markdown(terkep[0])}" if terkep else ""))
        try:
            szoveg, _ = await _DEPS["moderator"](PARANCS_SYSTEM, user, 3000)
            obj, _ = json_kinyer(szoveg)
            feladatok = [f for f in (feladat_norm(x, st["resztvevok"]) for x in (obj or {}).get("feladatok") or [])
                         if f][:MAX_FELADAT]
        except Exception as e:  # noqa: BLE001
            logger.warning("pyramid-vita #%d: a parancs értelmezése hibázott: %s", vid, e)
    for f in feladatok:
        f.update({"allapot": "fut", "eredmeny": "", "link": "", "nezet": None, "ref": None})

    def fn(st2, conn):
        p2 = next(x for x in st2["parancsok"] if int(x["n"]) == int(n))
        if not feladatok:
            p2["allapot"] = "hibas"
            _bejegyez(conn, vid, st2, "pyramid", f"❌ **PARANCS #{n}**: nem tudtam feladatokká bontani — mondd újra "
                      f"pontosabban (ki, mit, mikorra).", {"tipus": "parancs", "n": n})
            if not any(x.get("allapot") in ("fut", "ertelmez") for x in st2["parancsok"] if x is not p2):
                st2["allapot"] = "parancsra_var"
            return {}
        p2["feladatok"] = feladatok
        p2["allapot"] = "fut"
        p2["forras"] = forras
        szoveg = (f"⚡ **PARANCS #{n}** — a Kommandant: „{_rovid(p2['szoveg'], 400)}”"
                  + (" (a javaslat szerint)" if forras == "javaslat" else "") + "\n\n"
                  + "\n".join(_feladat_sor(i, f) for i, f in enumerate(feladatok, 1))
                  + "\n\n_A végrehajtás azonnal indul; a tételek állapota itt frissül._")
        p2["blokk"] = _bejegyez(conn, vid, st2, "pyramid", szoveg, {"tipus": "parancs", "n": n})
        return {}
    modosit(vid, fn)
    if not feladatok:
        await _ertesit(f"❌ A #{vid} vita PARANCS #{n}: nem tudtam feladatokká bontani — mondd újra pontosabban.")
        return
    kontextus = (f"Pyramid-vita #{vid}: {d['topic']}\nA Kommandant parancsa: {p['szoveg']}\n"
                 + (f"A határozati javaslat összefoglalója: {javaslat.get('osszefoglalo')}\n"
                    if javaslat.get("osszefoglalo") else ""))
    for i, f in enumerate(feladatok, 1):
        await vegrehajt_tetel(vid, n, i, f, kontextus)
    _vegrehajtas_vege(vid)


async def vegrehajt_tetel(vid: int, n: int, i: int, f: dict, kontextus: str) -> None:
    try:
        r = await _DEPS["vegrehajt"]({**f, "vita_id": vid, "parancs": n, "tetel": i}, kontextus)
    except Exception as e:  # noqa: BLE001
        logger.exception("pyramid-vita #%d P%d/%d végrehajtás", vid, n, i)
        r = {"allapot": "hibas", "szoveg": f"{type(e).__name__}: {str(e)[:300]}"}
    r = r if isinstance(r, dict) else {"allapot": "hibas", "szoveg": "a végrehajtó nem adott választ"}
    visszajelzes(vid, n, i, r)
    ref = r.get("ref") or {}
    if r.get("allapot") in ("fut", "jovahagyasra_var") and figyelt_fajta(ref):
        _hatter(_FIGYELO, f"{vid}:{n}:{i}", figyel(vid, n, i, ref))


def faber_jel(vid: int, n: int, i: int) -> str:
    """A Faber-kívánság jele (a kívánság szövegének ELEJÉN, a végrehajtó teszi bele): a Faber „megkaptam” üzenete
    (a kívánság első 300 karakterével) és kapu-kártyája (a teljes kívánsággal) ebből köthető vissza a vita tételéhez —
    a Faber a Bridge relay-azonosítóját nem adja vissza."""
    return f"#{int(vid)} PYRAMID-VITA PARANCS #{int(n)}-ÁBÓL ({int(i)}. tétel"


def figyelt_fajta(ref) -> str | None:
    """A tétel `ref`-jéből a figyelő fajtája (ai_task / faber / draft), vagy None (nincs mit figyelni)."""
    if not isinstance(ref, dict):
        return None
    return next((k for k in FIGYELT if ref.get(k)), None)


JEL = {"kesz": "✅", "hibas": "❌", "jovahagyasra_var": "🕓", "kezi": "✋", "fut": "⏳", "elutasitva": "✖"}
ALLAPOT_SZO = {"kesz": "kész", "hibas": "hibás", "jovahagyasra_var": "jóváhagyásra vár a munkapadon",
               "kezi": "kézi teendő", "fut": "fut", "elutasitva": "elutasítva"}


def visszajelzes(vid: int, n: int, i: int, r: dict) -> None:
    """Egy tétel eredménye: az állapot a PARANCS-blokkban, és (ha nem csak „fut”) egy bejegyzés a szálban."""
    allapot = r.get("allapot") if r.get("allapot") in JEL else "hibas"

    def fn(st, conn):
        p = next((x for x in st.get("parancsok") or [] if int(x["n"]) == int(n)), None)
        if not p or i - 1 >= len(p["feladatok"]):
            return {}
        f = p["feladatok"][i - 1]
        f["allapot"] = allapot
        f["eredmeny"] = _rovid(r.get("szoveg") or "", 1200)
        for k in ("link", "nezet", "ref"):
            if r.get(k):
                f[k] = r[k]
        if allapot != "fut" or r.get("bejegyez"):
            # csak abszolút link megy a szálba (a kabin markdownja a relatívat nem linkeli; a PARANCS-kártyán ott a gomb)
            link = (f" [{r.get('link_szoveg') or 'eredmény'}]({r['link']})"
                    if str(r.get("link") or "").startswith("http") else "")
            _bejegyez(conn, vid, st, "pyramid",
                      f"{JEL[allapot]} PARANCS #{n} · {i}. feladat {ALLAPOT_SZO[allapot]}: {_rovid(f['mit'], 160)}"
                      + (f" — {_rovid(r.get('szoveg'), 900)}" if r.get("szoveg") else "") + link,
                      {"tipus": "visszajelzes", "n": n, "tetel": i})
        return {}
    modosit(vid, fn)


def _vegrehajtas_vege(vid: int) -> None:
    """Ha egy parancs minden tétele lezárult (nincs „fut”), a parancs kész; ha egyik sem fut, a vita „végrehajtva”."""
    teendo = {}

    def fn(st, conn):
        for p in st.get("parancsok") or []:
            if p.get("allapot") == "fut" and p["feladatok"] and all(f["allapot"] != "fut" for f in p["feladatok"]):
                p["allapot"] = "kesz"
                teendo.setdefault("kesz", []).append(p)
        if st["allapot"] == "vegrehajtas" and not any(p.get("allapot") in ("fut", "ertelmez")
                                                       for p in st.get("parancsok") or []):
            st["allapot"] = "kesz"
            return {"status": "resolved", "resolved_at": _most()}
        return {}
    modosit(vid, fn)
    for p in teendo.get("kesz") or []:
        db = {a: sum(1 for f in p["feladatok"] if f["allapot"] == a) for a in JEL}
        osszeg = ", ".join(f"{db[a]} {ALLAPOT_SZO[a]}" for a in ("kesz", "jovahagyasra_var", "kezi", "elutasitva",
                                                                  "hibas") if db[a])
        asyncio.ensure_future(_ertesit(f"⚡ A #{vid} vita PARANCS #{p['n']} lefutott: {osszeg}. Részletek a kabinban."))


def _tetel(st: dict | None, n: int, i: int) -> dict | None:
    p = next((x for x in (st or {}).get("parancsok") or [] if int(x["n"]) == int(n)), None)
    return p["feladatok"][i - 1] if p and 0 < i <= len(p.get("feladatok") or []) else None


async def figyel(vid: int, n: int, i: int, ref) -> None:
    """Egy háttérben futó / jóváhagyásra váró tétel állapotát figyeli, és MINDEN VÁLTOZÁST visszaír a szálba:
      * ai_task: a befejezését (kész / hibás) — 20 mp-enként, legfeljebb egy óráig;
      * Faber-kérelem: dolgozik → jóváhagyásra vár (🕓, a Kommandant gombja) → élesítve (✅) / elvetve (✖) / bukott (❌);
      * tweet-tervezet: jóváhagyásra vár → kiposztolva (✅) / elvetve (✖).
    A két utóbbi percenként, legfeljebb két hétig (a döntés a Kommandanté, napokig is várhat). `ref`: a tétel ref-je
    (régi hívó: az ai_task azonosítója)."""
    ref = dict(ref) if isinstance(ref, dict) else {"ai_task": int(ref)}
    fajta = figyelt_fajta(ref)
    fv = _DEPS.get(FIGYELT[fajta]) if fajta else None
    if fv is None:
        return
    hosszu = fajta != "ai_task"
    koz, plafon = (FIGYELO_LASSU_S, FIGYELO_HOSSZU_MAX_S) if hosszu else (FIGYELO_S, FIGYELO_MAX_S)
    st, _ = betolt(vid)
    utolso = (_tetel(st, n, i) or {}).get("allapot") or "fut"
    t = 0.0
    while t <= plafon:
        await asyncio.sleep(koz)
        t += max(koz, 0.001)
        try:
            r = await fv(ref if fajta == "faber" else int(ref[fajta]))
        except Exception as e:  # noqa: BLE001
            logger.warning("pyramid-vita figyelő %s %s: %s", fajta, ref.get(fajta), e)
            continue
        r = r if isinstance(r, dict) else {}
        a = r.get("allapot") or "fut"
        uj_ref = {**ref, **(r.get("ref") or {})}
        if a == utolso and uj_ref == ref:
            continue
        ref = uj_ref
        visszajelzes(vid, n, i, {**r, "allapot": a, "ref": ref})   # „fut”: csendben (a PARANCS-kártya frissül)
        utolso = a
        if a in VEGALLAPOTOK:
            _vegrehajtas_vege(vid)
            return
    if fajta == "ai_task":
        task_id = int(ref["ai_task"])
        visszajelzes(vid, n, i, {"allapot": "hibas", "szoveg": f"a figyelő {int(FIGYELO_MAX_S // 60)} perc után "
                                                               f"feladta — az ai_task #{task_id} tovább futhat",
                                 "link": f"/api/ai_tasks/{task_id}/export"})
        _vegrehajtas_vege(vid)
    elif utolso == "fut":
        visszajelzes(vid, n, i, {"allapot": "hibas", "ref": ref,
                                 "szoveg": f"a figyelő {int(FIGYELO_HOSSZU_MAX_S // 86400)} nap után feladta — a "
                                           f"{'Faber-kérelem' if fajta == 'faber' else 'tervezet'} tovább élhet"})
        _vegrehajtas_vege(vid)
    else:
        logger.info("pyramid-vita #%d P%d/%d: a %s figyelője %d nap után leállt (%s)", vid, n, i, fajta,
                    int(FIGYELO_HOSSZU_MAX_S // 86400), utolso)


_TWEET_TERVEZET = re.compile(r"tweet-tervezet #(\d+)")


def _ref_potol(f: dict) -> dict:
    """A figyelő bevezetése (11-24) előtti, jóváhagyásra váró tweet-tétel ref-je az eredmény szövegéből."""
    ref = dict(f.get("ref") or {})
    if not figyelt_fajta(ref) and f.get("vegrehajto") == "kifele" and f.get("allapot") == "jovahagyasra_var":
        m = _TWEET_TERVEZET.search(str(f.get("eredmeny") or ""))
        if m:
            ref["draft"] = int(m.group(1))
    return ref


def ujraeleszt(vid: int, st: dict | None = None, *, motor: bool = True) -> dict:
    """A Bridge újraindulása után: a „fut” állapotú vita motorja (ha `motor`), a félbemaradt parancs-értelmezés és a
    futó / jóváhagyásra váró tételek figyelői (ai_task, Faber-kérelem, tweet-tervezet) újra indulnak. Hívja az
    induláskori folytatás (`ujraeleszt_mind`) és a kabin nézete is. → {motor: bool, figyelo: n, parancs: n}"""
    ki = {"motor": False, "figyelo": 0, "parancs": 0}
    if not bekapcsolva():
        return ki
    if st is None:
        st, _ = betolt(vid)
    if not st:
        return ki
    if motor and st["allapot"] == "fut" and not st.get("leallit") and not fut_e(vid):
        ki["motor"] = inditas(vid)
    for p in st.get("parancsok") or []:
        if p.get("allapot") == "ertelmez" and int(vid) not in _PARANCS_FUT:
            if _hatter(_PARANCS_FUT, int(vid), parancs_feldolgoz(int(vid), int(p["n"]))) is not None:
                ki["parancs"] += 1
        for i, f in enumerate(p.get("feladatok") or [], 1):
            k = f"{vid}:{p['n']}:{i}"
            ref = _ref_potol(f)
            if f.get("allapot") in ("fut", "jovahagyasra_var") and figyelt_fajta(ref) and k not in _FIGYELO:
                if _hatter(_FIGYELO, k, figyel(int(vid), int(p["n"]), i, ref)) is not None:
                    ki["figyelo"] += 1
    return ki


def _frissult_ora(st: dict) -> float | None:
    try:
        d = datetime.fromisoformat(str(st.get("frissitve") or st.get("indult") or ""))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - (d if d.tzinfo else d.replace(tzinfo=timezone.utc))).total_seconds() / 3600


def ujraeleszt_mind(limit: int = 40) -> dict:
    """INDULÁSKOR (a Bridge cron-szála hívja, egyszer): a futó viták és a végrehajtás-figyelők maguktól folytatódnak —
    nem csak akkor, amikor a Kommandant megnyitja a vitát a kabinban. A kapcsoló és a plafonok érvényesek:
    `PYRAMID_VITA=0` → semmi; a leállított vita nem indul; a motor maga tartja a körszámot és a szólás-plafont
    (`MAX_SZOLAS`); legfeljebb `PYRAMID_VITA_INDULASKOR_MAX` (alap 3) motor indul magától, a legfrissebbek, és csak a
    `PYRAMID_VITA_FOLYTAT_ORA` (alap 72) óránál frissebb vita — a többit a kabin megnyitása folytatja. A figyelők
    (adatbázis-lekérdezés, modellhívás nélkül) mind indulnak. → összesítő"""
    ki = {"motor": [], "figyelo": 0, "parancs": 0, "kihagyva": []}
    if not bekapcsolva() or not _DEPS.get("get_db"):
        return ki
    max_motor = _env_int("PYRAMID_VITA_INDULASKOR_MAX", INDULASKOR_MAX, 0, 20)
    max_ora = _env_int("PYRAMID_VITA_FOLYTAT_ORA", FOLYTAT_ORA, 1, 24 * 30)
    conn = _db()
    try:
        sorok = [(int(r["id"]), r["resolution"]) for r in conn.execute(
            "SELECT id, resolution FROM discussions ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]
    finally:
        conn.close()
    for vid, res in sorok:
        st = allapot_ertelmez(res)
        if not st:
            continue
        motor_kell = st["allapot"] == "fut" and not st.get("leallit") and not fut_e(vid)
        motor = False
        if motor_kell:
            ora = _frissult_ora(st)
            if ora is not None and ora > max_ora:
                ki["kihagyva"].append({"vita": vid, "ok": f"{int(ora)} órája nem frissült"})
            elif len(ki["motor"]) >= max_motor:
                ki["kihagyva"].append({"vita": vid, "ok": f"induláskor legfeljebb {max_motor} vita indul"})
            else:
                motor = True
        r = ujraeleszt(vid, st, motor=motor)
        if r["motor"]:
            ki["motor"].append(vid)
        ki["figyelo"] += r["figyelo"]
        ki["parancs"] += r["parancs"]
    if ki["motor"] or ki["figyelo"] or ki["parancs"] or ki["kihagyva"]:
        logger.info("pyramid-vita induláskor: motor %s, figyelő %d, parancs %d, kihagyva %s", ki["motor"],
                    ki["figyelo"], ki["parancs"], ki["kihagyva"])
    return ki


# ══ NÉZET (a kabinnak) ════════════════════════════════════════════════════

def allapot_szoveg(st: dict) -> str:
    a = st["allapot"]
    i = int(st["kor"]) - int(st.get("szakasz_kezdo", 1)) + 1
    k = int(st["korok"])
    if a == "fut":
        if not st.get("allaspontok"):
            return "az álláspontok kiosztása…"
        if st.get("kor_lezarva") or int(st["kovetkezo"]) >= len(st["sorrend"]):
            return f"R{st['kor']} kész · a térkép frissül…"
        return f"{i}/{k}. kör · következik: {nev(st['sorrend'][int(st['kovetkezo'])])}"
    if a == "var_ra":
        return f"R{int(st['kor']) - 1} kész · várlak (szólj hozzá, vagy Tovább)"
    if a == "parancsra_var":
        return "döntésre vár · a parancsod indítja a végrehajtást"
    if a == "vegrehajtas":
        ff = [f for p in st.get("parancsok") or [] for f in p.get("feladatok") or []]
        if not ff:
            return "végrehajtás · a parancs feldolgozása…"
        return f"végrehajtás {sum(1 for f in ff if f['allapot'] != 'fut')}/{len(ff)}"
    if a == "kesz":
        return "végrehajtva"
    if a == "leallitva":
        return "leállítva" + (f" · {st['hiba']}" if st.get("hiba") else "")
    return a


def nezet(st: dict, vid: int | None = None) -> dict:
    """A kabin vita-nézetének Pyramid-része: állapotsor, résztvevők, álláspontok, térkép(ek), javaslat, parancsok."""
    kiosztas_ = st.get("kiosztas") or {}
    aa = st.get("allaspontok") or []
    resztvevok = [{"id": r, "nev": nev(r),
                   "allaspont": betu(int(kiosztas_[r]["allaspont"])) if r in kiosztas_ else None,
                   "ordogugyved": bool((kiosztas_.get(r) or {}).get("ordogugyved"))} for r in st["resztvevok"]]
    return {
        "allapot": st["allapot"], "allapot_nev": ALLAPOT_NEV.get(st["allapot"], st["allapot"]),
        "allapot_szoveg": allapot_szoveg(st), "kor": st["kor"], "korok": st["korok"], "szakasz": st.get("szakasz", 1),
        "kovetkezo": (nev(st["sorrend"][int(st["kovetkezo"])])
                      if st["allapot"] == "fut" and int(st["kovetkezo"]) < len(st["sorrend"]) else None),
        "motor_fut": fut_e(vid) if vid is not None else None,
        "varj_ram": bool(st.get("varj_ram")), "resztvevok": resztvevok,
        "allaspontok": [{"betu": betu(i), **a} for i, a in enumerate(aa)],
        "terkep": st.get("terkep") or [], "javaslat": st.get("javaslat"), "parancsok": st.get("parancsok") or [],
        "kommandant_fuggo": len(st.get("kommandant_uj") or []), "hiba": st.get("hiba"),
        "bej": st.get("bej") or {},
    }


# ══ A FELDWEBEL ESZKÖZE (Telegram: „vitassák meg…”, „a 18-as vitához…”, „parancs: …”) ════════════

ESZKOZ_DEF = {"type": "function", "function": {
    "name": "vita",
    "description": (
        "PYRAMID-VITA: Kimi, DeepSeek, GLM több körben, egymásra reagálva vitatja meg a kérdést, ütköző álláspontokkal; "
        "a végrehajtás CSAK a Kommandant parancsára indul. muvelet: indit (tema, kontextus; vagy vita_id: meglévő vita "
        "újra), hozzaszol (a Kommandant szavai — neki válaszolnak), parancs (jóváhagy és utasít: a szavaiból "
        "feladatlista, azonnal fut), tovabb, leallit, allapot. `szoveg`: a Kommandant szavai szó szerint."),
    "parameters": {"type": "object", "properties": {
        "muvelet": {"type": "string", "enum": ["indit", "hozzaszol", "parancs", "tovabb", "leallit", "allapot"]},
        "vita_id": {"type": "integer", "description": "nincs megadva: a legutóbbi Pyramid-vita"},
        "tema": {"type": "string"},
        "kontextus": {"type": "string"},
        "szoveg": {"type": "string"},
        "korok": {"type": "integer", "description": "alap 3, legfeljebb 6"},
    }, "required": ["muvelet"]}}}


def legutobbi_pyramid() -> int | None:
    conn = _db()
    try:
        for r in conn.execute("SELECT id, resolution FROM discussions ORDER BY id DESC LIMIT 40").fetchall():
            if allapot_ertelmez(r["resolution"]):
                return int(r["id"])
    finally:
        conn.close()
    return None


def rovid_allapot(vid: int) -> dict:
    st, d = betolt(vid)
    if not d:
        return _hiba(f"nincs #{vid} vita")
    if not st:
        return {"ok": True, "vita_id": vid, "pyramid": False, "tema": d["topic"], "status": d["status"]}
    t = (st.get("terkep") or [None])[-1]
    return {"ok": True, "vita_id": vid, "pyramid": True, "tema": d["topic"], "allapot": allapot_szoveg(st),
            "terkep": terkep_markdown(t) if t else None,
            "javaslat": javaslat_markdown(st["javaslat"]) if st.get("javaslat") else None,
            "parancsok": [{"n": p["n"], "allapot": p.get("allapot"),
                           "feladatok": [{"mit": f["mit"], "allapot": ALLAPOT_SZO.get(f["allapot"], f["allapot"]),
                                          "eredmeny": f.get("eredmeny")} for f in p.get("feladatok") or []]}
                          for p in st.get("parancsok") or []]}


async def feldwebel_hiv(args: dict, ctx=None) -> str:
    """A Feldwebel `vita` eszköze (Telegram / a kabin Claus-a). A kabinban ugyanez gombokkal megy."""
    a = args if isinstance(args, dict) else {}
    m = str(a.get("muvelet") or "allapot")
    try:
        vid = int(a["vita_id"]) if a.get("vita_id") not in (None, "", 0) else None
    except (TypeError, ValueError):
        vid = None
    if m == "indit":
        if vid:
            out = atalakit(vid, korok=a.get("korok"))
            if out.get("ok") and out.get("mar_pyramid"):
                out = tovabb(vid)
        else:
            out = letrehoz(str(a.get("tema") or ""), str(a.get("kontextus") or ""), ki="kommandant",
                           korok=a.get("korok"))
        if out.get("ok"):
            out["uzenet"] = (f"A #{out['vita_id']} vita elindult: álláspontok, aztán Kimi, DeepSeek és GLM egymás után. "
                             f"Minden kör után megáll és rád vár (a kabinban látod a térképet); a végrehajtás csak a "
                             f"parancsodra indul.")
        return json.dumps(out, ensure_ascii=False)
    vid = vid or legutobbi_pyramid()
    if not vid:
        return json.dumps(_hiba("nincs Pyramid-vita — indíts egyet (muvelet=indit)"), ensure_ascii=False)
    st, d = betolt(vid)
    if d is None:
        return json.dumps(_hiba(f"nincs #{vid} vita"), ensure_ascii=False)
    if m in ("hozzaszol", "parancs") and st is None:
        if not bekapcsolva():
            return json.dumps(_hiba("a Pyramid-vita ki van kapcsolva"), ensure_ascii=False)
        atalakit(vid, indit=(m == "hozzaszol"))
    if m == "hozzaszol":
        out = hozzaszol(vid, str(a.get("szoveg") or ""), "kommandant")
    elif m == "parancs":
        out = parancs(vid, str(a.get("szoveg") or ""))
        if out.get("ok"):
            out["uzenet"] = (f"PARANCS #{out['parancs']} a #{vid} vitában: a szavaidból feladatlista lesz, és azonnal "
                             f"indul. Az állapotot a vita szálában látod (✅/❌).")
    elif m == "tovabb":
        out = tovabb(vid)
    elif m == "leallit":
        out = leallit(vid)
    else:
        out = rovid_allapot(vid)
    return json.dumps(out, ensure_ascii=False)
