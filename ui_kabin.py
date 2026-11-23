"""Bridge UI v2 — „kabinüveg” (2026-11-22): chat-központú munkafelület a Kommandantnak a `/app` úton.

A Kommandant (11-22): „ezt kéne az openbot és grokbot szerű felhasználási felületre frissíteni.” Korábban: „én szóban
akarom elmondani neki, hogy mi a parancs - nem akarok ilyen / parancsokat”, és „nem akarok új telegrammot … Működjön.”
Ez a felület a későbbi OpenBot-testig (Operation Umbau, F6 SIA skin) a SIA kabinüvege: a régi `/dashboard` utódja.

Mi van itt (a statikus kliens a `static/app/` alatt, build-lépés nélkül):
  1. BELÉPÉS jelszó nélkül, Telegrammal. A belépőlapon a „Belépőlink a Telegramodra” gomb a Kommandant chatjére
     (TELEGRAM_CHAT_ID) küld egy egyszer használatos, 10 percig érvényes linket ÉS egy 6 jegyű kódot. A link
     állapotmentes aláírt token (lejárat + véletlen + HMAC), a felhasználtságot memóriában tartjuk (újraindulás után
     a még le nem járt link egyszer újra élhet — 10 perces ablak, vállalt). A kód az iPhone kezdőképernyős
     alkalmazásának kell: ott a PWA-nak SAJÁT sütitára van, a Telegramból megnyitott link nem abba lép be.
     Kulcs: sha256("ui:" + bot token), mint a `faber_relay.kulcs`, más előtaggal — új titok nem kell, és a
     link- meg a süti-aláírás külön tartományban van („link|…”, „suti|…”), egyik nem használható a másik helyett.
  2. MUNKAMENET: aláírt, httpOnly, Secure, SameSite=Strict süti 30 napra. Minden `/api/ui/*` csak vele megy (401).
     A SameSite=Strict a CSRF ellen is véd: idegen oldalról indított kérés nem viszi a sütit.
  3. BESZÉLGETÉS ugyanazzal az aggyal, mint a Telegram (`deps["asszisztens"]` = `server._asszisztens_kor`): a kör
     háttérfeladatként fut, a kliens a kör állapotát kérdezi (gépelésjelző, élő eszközhívás-kártyák). Közös előzmény
     a Telegrammal: ugyanaz a `telegram_history` (chat_id) és ugyanaz a `messages` tábla.
  4. JÓVÁHAGYÁS: a Faber kérelmei (`faber:kapu` / `faber:esemeny`, lásd `faber_kartyak`) és a Bridge
     `action_drafts`-ai; a döntés hitelesített POST, a Faber felé `faber_relay.tovabbit(..., "gomb", ...)`. A
     tweet-tervezet (2026-11-23) a teljes szövegével és az X szerinti karakterszámával jön; a Jóváhagyom ott posztol
     (`draft_dont` → `twitter_poszt.dont`), az Elvetem nem. A 🐗 HOG-kártya (2026-11-23, hog.py) az oldal címével,
     a kártya-főcímmel, az elemzéssel és a tényekkel (összecsukva), a tweet-szöveggel és a móddal; a Jóváhagyom előbb az
     ÁLLANDÓ oldalt teszi ki (`hog_publish`), aztán a tweetet (`hog.dont`).
  5. MUNKAPAD: friss események (feladatok, viták, briefek) és az LLM-költés (`llm_kapu.allapot()`).
  6. FELADAT-ÜGYNÖKÖK (2026-11-23, feladat_ugynok.py): a bal sávban a fix ügynökök alatt, dinamikusan; mindegyiknek
     saját ablaka (`ugynok:<id>`: a futások naplója a messages táblából — a ténylegesen futott modell, költés, viták,
     utasítások), „+ Új ügynök” és „Szerkeszt” (modell, extra prompt, vitapartnerek, utasíthat), „Futtasd most”,
     „Leállít”, „Újraindít”, „Megszüntet” — hitelesített POST-ok, ugyanazon a belépőn, mint a Feldwebel `ugynok` eszköze.
     11-23: „Új utasítás az ügynöknek…” mező — egy azonnali futás a Kommandant utasításával (a lap tetején), az állandó
     feladat érintetlen; az átiratban az ő buborékja, utána a futás eredménye (`api_ugynok_utasitas`).
  7. KÖZÖS FELADATOK (2026-11-23, lásd 9/0): a munkapadon az `ai_task` feladatai (Kimi + DeepSeek + GLM + szintézis)
     beszédes állapottal („2/3 ügynök kész”, „összefoglaló készül”, „kész”, „hibás”); kattintásra a leírás, a
     válaszok a modellel, a szintézis, a DOCX — és hozzászólás, amiből folytató feladat indul ugyanazon az úton.

A link címe SOSEM a kérés Host-fejlécéből jön (azzal bárki a saját domainjére irányíthatná a Kommandantnak küldött
tokent): `BRIDGE_PUBLIC_URL`, különben `https://$RAILWAY_PUBLIC_DOMAIN`; Host-ból csak helyi gépen (localhost).

Tárolás: HÁZI SZABÁLY — új tábla nincs. Minden a meglévő `messages`, `tasks`, `discussions`, `action_drafts`,
`briefs`, `heartbeats` táblákból jön, a közös `get_db()`-n át; az `ai_tasks` / `ai_task_results` táblát csak olvassuk
(a hozzászólás új feladatát az `ai_task` eszköz írja, nem a kabin).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import os
import pathlib
import re
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

import faber_relay
import feladat_ugynok
import kimenet as kimenet_mod
import hog
import twitter_poszt

logger = logging.getLogger("ui_kabin")

ELOTAG = "ui:"
LINK_ERVENY = 10 * 60
SUTI_ERVENY = 30 * 24 * 3600
SUTI_NEV = "bridge_kabin"
KOD_PROBA = 5
STATIC_DIR = pathlib.Path(__file__).parent / "static" / "app"

#: A kliensnek kiszolgált fájlok — FEHÉRLISTA (útvonal-bejárás ellen), típussal.
FAJLOK = {
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "diktalas.js": "text/javascript; charset=utf-8",
    "belepes.js": "text/javascript; charset=utf-8",
    "sw.js": "text/javascript; charset=utf-8",
    "manifest.webmanifest": "application/manifest+json",
    "ikon.svg": "image/svg+xml",
    "ikon-192.png": "image/png",
    "ikon-512.png": "image/png",
    "ikon-maskable-512.png": "image/png",
}

#: A felület ügynökei: azonosító → (név, szerep). A `feldwebel` a fő asszisztens (a Telegram-agy).
UGYNOKOK = {
    "feldwebel": ("Claus · Feldwebel", "fő asszisztens — Telegrammal közös"),
    "kimi": ("Kimi", "kutató, elemző · 256k"),
    "deepseek": ("DeepSeek", "gyors gondolkodás, kritikus"),
    "glm5": ("GLM-5.3", "kódoló, eszközhasználó"),
    "faber": ("Faber", "tanít: új képesség a saját alkalmazásainkba"),
}
LLM_UGYNOKOK = ("kimi", "deepseek", "glm5")
#: A Faber-kérelem állapotai, amelyekben még döntésre vár (a Faber-oldal ezek egyikét írja, vagy semmit).
VARO_ALLAPOTOK = {"var", "vár", "varakozik", "jovahagyasra_var", "jóváhagyásra vár", "kapu", "pending", "uj", "új"}

_DEPS: dict = {}
_ZAR = threading.Lock()
_FELHASZNALT: dict[str, float] = {}           # link-nonce → lejárat
_KODOK: dict[str, dict] = {}                  # kérés-azonosító → {"kod", "lejar", "proba"}
_LINK_KERESEK: deque = deque(maxlen=64)       # a linkkérések ideje (fojtás)
_KOROK: dict[str, dict] = {}                  # kör-azonosító → kör (webes beszélgetési kör)


def _most() -> float:
    return time.time()


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ══ 1. ALÁÍRÁS: link és süti ═════════════════════════════════════════════

def kulcs(token: str) -> bytes | None:
    return hashlib.sha256((ELOTAG + token).encode()).digest() if token else None


def _alair(k: bytes, tartomany: str, torzs: str) -> str:
    return hmac.new(k, f"{tartomany}|{torzs}".encode(), hashlib.sha256).hexdigest()


def _token(k: bytes, tartomany: str, ervenyes_s: int, most: float | None = None) -> str:
    lejar = int((most if most is not None else _most()) + ervenyes_s)
    torzs = f"{lejar}.{secrets.token_urlsafe(12)}"
    return f"{torzs}.{_alair(k, tartomany, torzs)}"


def _ellenoriz(k: bytes | None, tartomany: str, token: str, most: float | None = None) -> tuple[int, str] | None:
    """Érvényes aláírásnál és le nem járt tokennél (lejárat, nonce), különben None."""
    if not k or not token or len(token) > 300:
        return None
    try:
        lejar_s, nonce, sig = token.split(".")
        lejar = int(lejar_s)
    except ValueError:
        return None
    if not hmac.compare_digest(_alair(k, tartomany, f"{lejar_s}.{nonce}"), sig):
        return None
    if lejar < (most if most is not None else _most()):
        return None
    return lejar, nonce


def belepo_token(k: bytes, most: float | None = None) -> str:
    return _token(k, "link", LINK_ERVENY, most)


def belepo_ervenyes(k: bytes | None, token: str, most: float | None = None) -> bool:
    """Érvényes-e, és még nem használták fel (NEM használja fel — a megerősítő lap mutatásához)."""
    e = _ellenoriz(k, "link", token, most)
    if e is None:
        return False
    with _ZAR:
        return e[1] not in _FELHASZNALT


def belepo_felhasznal(k: bytes | None, token: str, most: float | None = None) -> bool:
    """Egyszer használatos: az első érvényes felhasználás True, minden további False."""
    e = _ellenoriz(k, "link", token, most)
    if e is None:
        return False
    lejar, nonce = e
    t = most if most is not None else _most()
    with _ZAR:
        for n, lj in list(_FELHASZNALT.items()):
            if lj < t:
                del _FELHASZNALT[n]
        if nonce in _FELHASZNALT:
            return False
        _FELHASZNALT[nonce] = lejar
    return True


def suti_ertek(k: bytes, most: float | None = None) -> str:
    return _token(k, "suti", SUTI_ERVENY, most)


def suti_ervenyes(k: bytes | None, ertek: str, most: float | None = None) -> bool:
    return _ellenoriz(k, "suti", ertek, most) is not None


def _k() -> bytes | None:
    return kulcs(_DEPS["bot_token"]()) if _DEPS.get("bot_token") else None


def hitelesitett(request: Request) -> bool:
    return suti_ervenyes(_k(), request.cookies.get(SUTI_NEV, ""))


def _vedett(fn):
    """Minden `/api/ui/*` kezelő ezen át: érvényes munkamenet-süti nélkül 401, a kezelő le sem fut."""
    async def kezelo(request: Request):
        if not hitelesitett(request):
            return JSONResponse({"hiba": "belépés kell", "kod": "nincs_munkamenet"}, status_code=401)
        try:
            return await fn(request)
        except Exception as e:  # noqa: BLE001 — a felület kapjon olvasható hibát, ne 500-as HTML-t
            logger.exception("ui_kabin %s hiba", request.url.path)
            return JSONResponse({"hiba": f"{type(e).__name__}: {str(e)[:300]}"}, status_code=500)
    kezelo.__name__ = fn.__name__
    kezelo.vedett = True
    return kezelo


def _suti_beallit(resp: Response) -> Response:
    resp.set_cookie(SUTI_NEV, suti_ertek(_k()), max_age=SUTI_ERVENY, httponly=True, secure=True,
                    samesite="lax", path="/")
    return resp


# ══ 2. BELÉPÉS ═══════════════════════════════════════════════════════════

def _nyilvanos_cim(request: Request) -> str | None:
    """A belépőlink alapja. Host-fejlécből CSAK helyi gépen — élesben a Host hamisítható (lásd a modul fejlécét)."""
    kozvetlen = os.environ.get("BRIDGE_PUBLIC_URL", "").strip().rstrip("/")
    if kozvetlen:
        return kozvetlen
    railway = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip().strip("/")
    if railway:
        return f"https://{railway}"
    host = (request.url.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return str(request.base_url).rstrip("/")
    return None


def _fojtas_rendben(most: float) -> bool:
    """Legfeljebb 1 linkkérés 30 mp-enként és 8 óránként — a gomb nyilvános, a Kommandant Telegramját ne lehessen
    teleszemetelni."""
    with _ZAR:
        if _LINK_KERESEK and most - _LINK_KERESEK[-1] < 30:
            return False
        if sum(1 for t in _LINK_KERESEK if most - t < 3600) >= 8:
            return False
        _LINK_KERESEK.append(most)
        return True


async def belepolink_ker(request: Request):
    """POST /app/belepolink — link + kód a Kommandant Telegramjára. Válasz: {ok, keres} (a kódos belépéshez)."""
    k = _k()
    chat = _DEPS["chat_id"]() if _DEPS.get("chat_id") else ""
    if not k or not chat:
        return JSONResponse({"ok": False, "hiba": "A Bridge-en nincs Telegram-bot vagy TELEGRAM_CHAT_ID beállítva."},
                            status_code=503)
    alap = _nyilvanos_cim(request)
    if not alap:
        return JSONResponse({"ok": False, "hiba": "Nincs nyilvános cím (BRIDGE_PUBLIC_URL / RAILWAY_PUBLIC_DOMAIN)."},
                            status_code=503)
    most = _most()
    if not _fojtas_rendben(most):
        return JSONResponse({"ok": False, "hiba": "Most küldtem egyet — nézd meg a Telegramot, vagy kérj újat fél perc "
                                                  "múlva."}, status_code=429)
    token = belepo_token(k, most)
    keres = secrets.token_urlsafe(18)
    kod = f"{secrets.randbelow(1_000_000):06d}"
    with _ZAR:
        for kk, v in list(_KODOK.items()):
            if v["lejar"] < most:
                del _KODOK[kk]
        _KODOK[keres] = {"kod": kod, "lejar": most + LINK_ERVENY, "proba": 0}
    szoveg = (f"🔐 Belépés a Bridge-felületre (10 percig érvényes, egyszer használható):\n{alap}/app/belepes?t={token}"
              f"\n\nVagy írd be a kódot: <b>{kod}</b>\n\nHa nem te kérted, ne kattints, és ne add tovább a kódot.")
    try:
        await _DEPS["telegram_kuld"](szoveg)
    except Exception as e:  # noqa: BLE001
        with _ZAR:
            _KODOK.pop(keres, None)
        logger.warning("ui_kabin: a belépőlink nem ment ki: %s", e)
        return JSONResponse({"ok": False, "hiba": f"A Telegram nem vette át: {str(e)[:160]}"}, status_code=502)
    logger.info("ui_kabin: belépőlink elküldve a Kommandant Telegramjára")
    return JSONResponse({"ok": True, "keres": keres, "ervenyes_mp": LINK_ERVENY})


async def belepes_kod(request: Request):
    """POST /app/belepes/kod {keres, kod} — a kódos belépés (iPhone kezdőképernyő). Kérésenként 5 próba."""
    try:
        body = await request.json()
    except ValueError:
        body = {}
    keres, kod = str(body.get("keres", "")), re.sub(r"\D", "", str(body.get("kod", "")))
    most = _most()
    with _ZAR:
        v = _KODOK.get(keres)
        if not v or v["lejar"] < most or v["proba"] >= KOD_PROBA:
            _KODOK.pop(keres, None)
            return JSONResponse({"ok": False, "hiba": "A kód lejárt vagy elfogytak a próbák — kérj újat."},
                                status_code=403)
        v["proba"] += 1
        jo = hmac.compare_digest(v["kod"], kod)
        if jo:
            del _KODOK[keres]
    if not jo:
        return JSONResponse({"ok": False, "hiba": "Nem ez a kód."}, status_code=403)
    return _suti_beallit(JSONResponse({"ok": True}))


_LAP_FEJ = ("<!doctype html><html lang='hu'><head><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'>"
            "<title>Belépés · Claus Bridge</title><link rel='stylesheet' href='/app/app.css'></head>"
            "<body class='belepes-lap'><main class='belepes-doboz'><div class='marka'><img src='/app/ikon.svg' alt='' "
            "width='40' height='40'><div><b>CLAUS BRIDGE</b><small>kabin</small></div></div>")


def _lap(belso: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_LAP_FEJ + belso + "</main></body></html>", status_code=status, headers=_BIZTONSAGI_FEJLECEK)


async def belepes_lap(request: Request):
    """GET /app/belepes?t=… — a link megerősítő lapja. NEM használja fel a tokent: a link-előnézők és vírusirtók
    GET-tel kérik le a linket, és elhasználnák. A belépés a lap POST-ja (a `belepes.js` azonnal elküldi)."""
    t = request.query_params.get("t", "")
    if not belepo_ervenyes(_k(), t):
        return _lap("<h1>A link lejárt vagy már felhasználták</h1><p>Kérj újat a belépőlapon.</p>"
                    "<p><a class='gomb' href='/app/'>Vissza a belépéshez</a></p>", 403)
    return _lap("<h1>Belépés</h1><p>Egy pillanat…</p>"
                "<form method='post' action='/app/belepes' id='belepes-urlap'>"
                f"<input type='hidden' name='t' value='{html.escape(t, quote=True)}'>"
                "<button class='gomb gomb-fo' type='submit'>Belépek</button></form>"
                "<script src='/app/belepes.js'></script>")


async def belepes_post(request: Request):
    """POST /app/belepes (t=…) — egyszeri felhasználás, süti, 303 → /app/."""
    t = parse_qs((await request.body()).decode("utf-8", "replace")).get("t", [""])[0]
    if not belepo_felhasznal(_k(), t):
        return _lap("<h1>A link lejárt vagy már felhasználták</h1><p>Kérj újat a belépőlapon.</p>"
                    "<p><a class='gomb' href='/app/'>Vissza a belépéshez</a></p>", 403)
    logger.info("ui_kabin: belépés a Telegram-linkkel")
    return _suti_beallit(RedirectResponse("/app/", status_code=303))


async def kilepes(request: Request):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SUTI_NEV, path="/", secure=True, httponly=True, samesite="lax")
    return resp


# ══ 3. STATIKUS KLIENS ═══════════════════════════════════════════════════

_BIZTONSAGI_FEJLECEK = {
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                "form-action 'self'; manifest-src 'self'; worker-src 'self'"),
}


async def app_gyoker(request: Request):
    return RedirectResponse("/app/", status_code=307)


async def app_fajl(request: Request):
    """GET /app/ és /app/{fajl}: a fehérlistás fájl, minden más út az alkalmazás héja (index.html)."""
    nev = request.path_params.get("utvonal", "") or ""
    if nev in FAJLOK:
        fejlec = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}
        if nev == "sw.js":
            fejlec["Service-Worker-Allowed"] = "/app/"
        return FileResponse(STATIC_DIR / nev, media_type=FAJLOK[nev], headers=fejlec)
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8",
                        headers={**_BIZTONSAGI_FEJLECEK, "Cache-Control": "no-cache"})


# ══ 4. FABER-KÁRTYÁK (a szerződés) ═══════════════════════════════════════
# A Faber (a Kommandant gépén) a Bridge `messages` táblájába ír:
#   sender='faber', recipient='kommandant', subject='faber:kapu',    message={"id","repo","kivansag","jelentes","diffstat","kod"}
#   sender='faber', recipient='kommandant', subject='faber:esemeny', message={"id","allapot","szoveg"}
# A kártya a kérelem LEGFRISSEBB állapota. Döntésre vár, ha az utolsó kapu óta nincs esemény (vagy az esemény
# állapota várakozó), és még nem ment el rá döntés (a Bridge aláírt `faber:gomb` tétele ugyanarra a kódra).
# A `kod` a kliensnek NEM megy ki: a döntésnél a szerver keresi ki.

def _json(s: str) -> dict:
    try:
        d = json.loads(s or "")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _relay_tetelek(conn, k_faber: bytes | None, limit: int = 600) -> list[dict]:
    """A Bridge aláírt Faber-továbbításai (recipient='faber'), ellenőrizve — aláírás nélkül semmi (a /api/messages
    nyilvános, bárki írhat a 'faber' címzettnek)."""
    if not k_faber:
        return []
    out = []
    for r in conn.execute("SELECT id, timestamp, message FROM messages WHERE recipient = 'faber' "
                          "ORDER BY id DESC LIMIT ?", (limit,)).fetchall():
        t = faber_relay.ellenoriz(k_faber, r["message"])
        if t:
            out.append({"id": r["id"], "ido": r["timestamp"], "tipus": t.get("tipus"), "adat": t.get("adat", "")})
    return out[::-1]


def faber_kartyak(conn, k_faber: bytes | None, limit: int = 400) -> list[dict]:
    sorok = conn.execute("SELECT id, timestamp, subject, message FROM messages WHERE sender = 'faber' "
                         "AND recipient = 'kommandant' AND subject IN ('faber:kapu', 'faber:esemeny') "
                         "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()[::-1]
    gombok = [g for g in _relay_tetelek(conn, k_faber) if g["tipus"] == "gomb"]
    kartyak: dict = {}
    for r in sorok:
        d = _json(r["message"])
        try:
            kid = int(d.get("id"))
        except (TypeError, ValueError):
            continue
        if r["subject"] == "faber:kapu":
            kartyak[kid] = {
                "id": kid, "repo": str(d.get("repo") or ""), "kivansag": str(d.get("kivansag") or ""),
                "jelentes": str(d.get("jelentes") or ""), "diffstat": str(d.get("diffstat") or ""),
                "_kod": str(d.get("kod") or ""), "kapu_sor": r["id"], "ido": r["timestamp"],
                "allapot": "var", "szoveg": "", "frissitve": r["timestamp"], "dontes": None,
            }
        elif kid in kartyak:
            c = kartyak[kid]
            c["allapot"] = str(d.get("allapot") or c["allapot"])
            c["szoveg"] = str(d.get("szoveg") or "")
            c["frissitve"] = r["timestamp"]
    for g in gombok:
        m = re.match(r"^(mehet|megse):(\d+):(.+)$", g["adat"] or "")
        if not m:
            continue
        c = kartyak.get(int(m.group(2)))
        if c and m.group(3) == c["_kod"] and g["id"] > c["kapu_sor"]:
            c["dontes"] = {"dontes": m.group(1), "ido": g["ido"]}
    out = []
    for c in kartyak.values():
        c["var_dontesre"] = c["allapot"].strip().lower() in VARO_ALLAPOTOK and c["dontes"] is None
        out.append(c)
    out.sort(key=lambda c: c["frissitve"], reverse=True)
    return out


def _kartya_kifele(c: dict) -> dict:
    return {k: v for k, v in c.items() if not k.startswith("_")}


# ══ 5. ELŐZMÉNY (közös a Telegrammal) ═══════════════════════════════════

_SUBJ_MODELL = re.compile(r"modell=([^\s·]+)")
_SUBJ_ESZKOZ = re.compile(r"eszközök=([^\s·]+)")
_SZOVEG_EMLITES = re.compile(r"@(kimi|deepseek|glm5)\b", re.I)


def _subject_nyom(subject: str) -> tuple[str | None, list[str]]:
    m, e = _SUBJ_MODELL.search(subject or ""), _SUBJ_ESZKOZ.search(subject or "")
    return (m.group(1) if m else None), (e.group(1).split(",") if e else [])


def ugynok_ablak(ablak: str) -> bool:
    """Feladat-ügynök ablaka-e (`ugynok:<recept-id>`)."""
    return bool(re.fullmatch(r"ugynok:\d{1,9}", ablak or ""))


def _futas_szoveg(d: dict) -> str:
    """Egy feladat-ügynök futása az átiratba: összegzés, eredmény, és a gép gyűjtötte linkek (dokumentum, tervezet)."""
    osszeg, ered = str(d.get("osszegzes") or "").strip(), str(d.get("eredmeny") or "").strip()
    reszek = []
    if d.get("allapot") != "kesz":
        reszek.append(f"⚠ {d.get('hiba') or d.get('allapot')}")
    if osszeg and not ered.startswith(osszeg[:60]):
        reszek.append(f"**{osszeg}**")
    if ered:
        reszek.append(ered)
    for x in d.get("dokumentumok") or []:
        reszek.append(f"📄 [{x.get('name') or 'dokumentum'}]({x['edit_url']})" if x.get("edit_url")
                      else f"📄 {x.get('name') or 'dokumentum'}")
    for t in d.get("tervezetek") or []:
        felirat = f"levéltervezet: {t.get('subject') or ''} → {t.get('to') or ''} (a Piszkozatokban, nem ment el)"
        reszek.append(f"✉️ [{felirat}]({t['link']})" if t.get("link") else f"✉️ {felirat}")
    for j in d.get("javaslatok") or []:
        reszek.append(f"📅 {j.get('javaslat')} — javaslat, a munkapadon hagyhatod jóvá")
    for v in d.get("vitak") or []:
        reszek.append(f"💬 **Megvitatta — {v.get('nev')}** ({v.get('modell') or '?'})\n> {' '.join(str(v.get('kerdes') or '').split())[:600]}"
                      f"\n\n{str(v.get('valasz') or '').strip()[:3000]}")
    for x in d.get("utasitasok") or []:
        reszek.append(f"➡ **{x.get('nev')}**: " + ("kívánság a Fabernek (te hagyod jóvá)" if x.get("cel") == "faber"
                                                    else "elindítva ezzel a bemenettel")
                      + f"\n> {' '.join(str(x.get('uzenet') or '').split())[:600]}")
    if d.get("bemenet") and not d.get("utasitas"):      # az utasítás a Kommandant saját buborékjában látszik
        reszek.insert(0, f"> bemenet ({d.get('kuldo') or 'a Kommandant'}): {' '.join(str(d['bemenet']).split())[:600]}")
    for n in d.get("notes") or []:
        reszek.append(f"[{n}]")
    return "\n\n".join(reszek)


def _osztalyoz(r, relay: dict) -> list[tuple[str, dict]]:
    """Egy `messages`-sor → (ablak, tétel) párok. Ablakok: az ügynökök, a feladat-ügynökök, 'postalada'."""
    s, rc, subj, msg = r["sender"], r["recipient"], r["subject"] or "", r["message"] or ""
    alap = {"id": r["id"], "ido": r["timestamp"]}
    if s == rc and ugynok_ablak(s):
        d = _json(msg)
        if subj == feladat_ugynok.TARGY_FUTAS:
            elonezet = str(d.get("osszegzes") or d.get("hiba") or d.get("allapot") or "")
            return [(s, {**alap, "ki": s, "tipus": "uzenet", "szoveg": _futas_szoveg(d), "elonezet": elonezet,
                         "modell": d.get("modell") or None,
                         "hiba": d.get("allapot") != "kesz", "eszkozok": list(dict.fromkeys(d.get("eszkozok") or [])),
                         "futas": {k: d.get(k) for k in ("allapot", "jelzes", "koltes_usd", "keret_usd", "trigger",
                                                         "mp", "hivasok", "kert_modell", "koltes_becsult", "kuldo")}})]
        if subj == feladat_ugynok.TARGY_UTASITAS:
            # a Kommandant új utasítása a kabinból: az ő buborékja; utána a futás eredménye (TARGY_FUTAS)
            return [(s, {**alap, "ki": "kommandant", "tipus": "uzenet", "szoveg": str(d.get("szoveg") or ""),
                         "csatorna": "web"})]
        if subj == feladat_ugynok.TARGY_DOKUMENTUM:
            if d.get("hiba"):
                szoveg = f"⚠ A Word-dokumentum nem készült el: {d['hiba']}"
            else:
                szoveg = "\n\n".join([f"📄 [{x.get('name') or 'dokumentum'}]({x['edit_url']})" if x.get("edit_url")
                                       else f"📄 {x.get('name') or 'dokumentum'}" for x in d.get("dokumentumok") or []]
                                      + [f"a {str(d.get('futas_ido') or '')[:16]} futás jelentése"]
                                      + ([f"[{d['megjegyzes']}]"] if d.get("megjegyzes") else []))
            return [(s, {**alap, "ki": s, "tipus": "uzenet", "szoveg": szoveg, "hiba": bool(d.get("hiba")),
                         "elonezet": "Word-dokumentum"})]
        return [(s, {**alap, "ki": "rendszer", "tipus": "rendszer", "szoveg": str(d.get("szoveg") or subj)})]
    if s == "kommandant" and rc == "bridge" and subj.startswith(("Telegram:", "Web:")):
        csat = "web" if subj.startswith("Web:") else "telegram"
        emlitett = sorted({m.lower() for m in _SZOVEG_EMLITES.findall(msg)})
        tetel = {**alap, "ki": "kommandant", "tipus": "uzenet", "csatorna": csat}
        if emlitett:
            return [(u, {**tetel, "szoveg": _SZOVEG_EMLITES.sub("", msg).strip() or msg}) for u in emlitett]
        return [("feldwebel", {**tetel, "szoveg": msg})]
    if rc == "kommandant" and s.startswith("feldwebel-"):
        modell, eszk = _subject_nyom(subj)
        return [("feldwebel", {**alap, "ki": "feldwebel", "tipus": "uzenet", "szoveg": msg, "modell": modell,
                               "eszkozok": eszk})]
    if rc == "kommandant" and s in LLM_UGYNOKOK and subj.startswith("Re: @"):
        modell, _ = _subject_nyom(subj)
        return [(s, {**alap, "ki": s, "tipus": "uzenet", "szoveg": msg, "modell": modell})]
    if rc == "kommandant" and s == "faber":
        d = _json(msg)
        if subj == "faber:kapu":
            return [("faber", {**alap, "ki": "faber", "tipus": "kartya", "kerelem": d.get("id"),
                               "szoveg": str(d.get("kivansag") or "")})]
        if subj == "faber:esemeny":
            return [("faber", {**alap, "ki": "faber", "tipus": "esemeny", "kerelem": d.get("id"),
                               "allapot": str(d.get("allapot") or ""), "szoveg": str(d.get("szoveg") or "")})]
        return [("faber", {**alap, "ki": "faber", "tipus": "uzenet", "szoveg": msg})]
    if rc == "faber":
        t = relay.get(r["id"])
        if not t:
            return []                     # aláíratlan: nem a Kommandant szava
        if t["tipus"] == "szandek":
            d = _json(t["adat"])
            if d.get("forras") == "hopsz":       # az automatikus HOPSZ-kérelem (hopsz.py) nem a Kommandant szava
                return [("faber", {**alap, "ki": "rendszer", "tipus": "rendszer",
                                   "szoveg": f"🔧 HOPSZ → Faber: {str(d.get('szoveg') or '')[:600]}"})]
            tetel = {**alap, "ki": "kommandant", "tipus": "uzenet", "szoveg": str(d.get("szoveg") or ""),
                     "faber": {"szandek": d.get("szandek"), "repo": d.get("repo"), "kerelem": d.get("kerelem")}}
            return [("feldwebel", tetel), ("faber", tetel)]
        if t["tipus"] == "parancs":
            tetel = {**alap, "ki": "kommandant", "tipus": "uzenet", "szoveg": t["adat"], "faber": {"szandek": "parancs"}}
            return [("feldwebel", tetel), ("faber", tetel)]
        if t["tipus"] == "gomb":
            m = re.match(r"^(mehet|megse):(\d+):", t["adat"] or "")
            if m:
                return [("faber", {**alap, "ki": "rendszer", "tipus": "rendszer",
                                   "szoveg": f"{'Mehet' if m.group(1) == 'mehet' else 'Mégse'} — a #{m.group(2)} "
                                             f"kérelemre (továbbítva a Fabernek)"})]
        return []
    if rc == "kommandant" and s not in ("capture-daemon",):
        return [("postalada", {**alap, "ki": s, "tipus": "uzenet", "targy": subj, "szoveg": msg})]
    return []


_TORTENET_SQL = ("SELECT id, timestamp, sender, recipient, subject, message FROM messages WHERE "
                 "((sender = 'kommandant' AND recipient = 'bridge') OR recipient IN ('kommandant', 'faber') "
                 "OR sender LIKE 'ugynok:%')")


def tortenet(conn, ablak: str, k_faber: bytes | None, *, elotte: int | None = None, limit: int = 50,
             adag: int = 600, plafon: int = 6000) -> dict:
    """Egy ablak (ügynök vagy postaláda) üzenetei időrendben, a `elotte` azonosító előttről, `adag` soronként
    visszafelé lapozva, amíg `limit` tétel össze nem jön (legfeljebb `plafon` sort nézünk át egy kérésre).
    `tovabb`: van-e még régebbi átnézetlen sor."""
    out, relay, hatar, atnezett, kimerult = [], None, int(elotte) if elotte else None, 0, False
    while len(out) < limit and atnezett < plafon:
        sql, p = _TORTENET_SQL, []
        if hatar:
            sql += " AND id < ?"
            p.append(hatar)
        sorok = conn.execute(sql + " ORDER BY id DESC LIMIT ?", p + [adag]).fetchall()
        if relay is None and any(r["recipient"] == "faber" for r in sorok):
            relay = {t["id"]: t for t in _relay_tetelek(conn, k_faber, limit=5000)}
        feldolgozott = 0
        for r in sorok:
            hatar = r["id"]
            atnezett += 1
            feldolgozott += 1
            for ab, tetel in _osztalyoz(r, relay or {}):
                if ab == ablak:
                    out.append(tetel)
            if len(out) >= limit:
                break
        if len(sorok) < adag and feldolgozott == len(sorok):
            kimerult = True                       # a tábla elejéig értünk
            break
    # a lapozó kurzor az utolsó ÁTNÉZETT sor: a következő oldal ez előttről indul (nincs kétszer átnézett sor)
    return {"ablak": ablak, "tetelek": out[::-1], "tovabb": not kimerult, "legkisebb_id": hatar}


def _utolsok(conn, k_faber: bytes | None) -> tuple[dict, int]:
    """Ablakonként az utolsó tétel (előnézet, idő, azonosító), és a legnagyobb releváns üzenet-azonosító."""
    sorok = conn.execute(_TORTENET_SQL + " ORDER BY id DESC LIMIT 300").fetchall()
    relay = {t["id"]: t for t in _relay_tetelek(conn, k_faber, limit=100)}
    utolso: dict = {}
    for r in sorok:
        for ab, tetel in _osztalyoz(r, relay):
            utolso.setdefault(ab, {"id": tetel["id"], "ido": tetel["ido"], "ki": tetel["ki"],
                                   "szoveg": (tetel.get("elonezet") or tetel.get("szoveg") or "")[:140]})
    return utolso, (sorok[0]["id"] if sorok else 0)


# ══ 6. TELEGRAM-HTML → FELÜLETI SZÖVEG ═══════════════════════════════════

#: A beszélő-fej: opcionális ikon (cimzett.fejlec, 11-22), <b>NÉV</b>, opcionális „· modell” a sor végéig, opcionális „—”.
_FEJ = re.compile(r"^\s*(?:[^\s<]{1,4}\s+)?<b>([^<]{1,40})</b>[ \t]*(?:·[^\n—]*)?\s*(—\s*)?")
_BESZELOK = {"FELDWEBEL": "feldwebel", "FELDWEBEL DEBUG": "feldwebel", "KIMI": "kimi", "DEEPSEEK": "deepseek",
             "GLM5": "glm5", "GLM": "glm5", "BRIDGE": "feldwebel"}


def tg_szoveg(tg_html: str, alap_ki: str) -> dict:
    """A push szövege (Telegram-HTML) → {"ki", "szoveg", "hiba"}. A beszélő-fej („<b>FELDWEBEL</b>”) leválik; a
    formázás a kliens markdown-szerű megjelenítőjének megy (**félkövér**, `kód`, ```blokk```)."""
    s = tg_html or ""
    ki, hiba = alap_ki, False
    m = _FEJ.match(s)
    if m and m.group(1).strip().upper() in _BESZELOK:
        ki = _BESZELOK[m.group(1).strip().upper()]
        hiba = bool(m.group(2)) or m.group(1).strip().upper().endswith("DEBUG")
        s = s[m.end():]
    s = re.sub(r"<pre>(.*?)</pre>", lambda x: "\n```\n" + x.group(1) + "\n```\n", s, flags=re.S)
    s = re.sub(r"<code>(.*?)</code>", r"`\1`", s, flags=re.S)
    s = re.sub(r"<b>(.*?)</b>", r"**\1**", s, flags=re.S)
    s = re.sub(r'<a href="([^"]+)">(.*?)</a>', r"[\2](\1)", s, flags=re.S)
    s = re.sub(r"</?(i|u|s|em|strong|tg-spoiler)>", "", s)
    s = re.sub(r"<[^>]{1,60}>", "", s)
    return {"ki": ki, "szoveg": html.unescape(s).strip(), "hiba": hiba}


# ══ 7. WEBES BESZÉLGETÉSI KÖR ════════════════════════════════════════════

async def _faber_kor(szoveg: str, k) -> None:
    """A Faber-ablak: amit a Kommandant ide ír, az a Fabernek szól. A szándékfelismerő dönt (élesít, elvet,
    visszavon, állapot); ha nem ismer fel semmit, kívánság. A perjeles parancs változatlanul megy."""
    token = _DEPS["bot_token"]()
    k.nyom["ugynok"] = "faber"
    if faber_relay.parancs_e(szoveg):
        tipus, adat, nyugta = "parancs", szoveg.strip(), "🔧 Továbbítva a Fabernek."
        k.nyom["faber"] = {"szandek": "parancs"}
    else:
        sz = await faber_relay.szandek("Faber, " + szoveg)
        if sz is None:
            sz = {"szandek": "kivansag", "szoveg": szoveg, "repo": faber_relay._repo_szo(szoveg), "kerelem": None}
        sz["szoveg"] = szoveg
        tipus, adat = "szandek", faber_relay.szandek_adat(sz)
        nyugta = faber_relay.NYUGTA.get(sz["szandek"], "Rendben, szólok a Fabernek.")
        k.nyom["faber"] = {k2: sz.get(k2) for k2 in ("szandek", "repo", "kerelem")}
    conn = _DEPS["get_db"]()
    try:
        mid = faber_relay.tovabbit(conn, token, tipus, adat, _iso())
    finally:
        conn.close()
    k.tetelek.append({"ido": _iso(), "html": nyugta if mid else
                      "A Faber most nem érhető el innen: nincs Telegram-token a Bridge-en."})


async def _kor_fut(kor: dict) -> None:
    k = kor["kimenet"]
    try:
        if kor["ugynok"] == "faber":
            await _faber_kor(kor["szoveg"], k)
        else:
            szoveg = kor["szoveg"]
            if kor["ugynok"] in LLM_UGYNOKOK and f"@{kor['ugynok']}" not in szoveg.lower():
                szoveg = f"@{kor['ugynok']} {szoveg}"
            await _DEPS["asszisztens"](szoveg, k, "web")
        kor["allapot"] = "kesz"
    except Exception as e:  # noqa: BLE001
        logger.exception("ui_kabin: a webes kör hibával állt meg")
        kor["allapot"], kor["hiba"] = "hiba", f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        k.nyitva = False
        kor["vege"] = _most()


def _kor_kifele(kor: dict) -> dict:
    k = kor["kimenet"]
    alap_ki = kor["ugynok"]
    valaszok = []
    for t in k.tetelek:
        v = tg_szoveg(t["html"], alap_ki)
        v["ido"] = t["ido"]
        valaszok.append(v)
    eszk = [{kk: vv for kk, vv in e.items() if not kk.startswith("_")} for e in k.nyom.get("eszkozok", [])]
    return {"kor_id": kor["id"], "ugynok": kor["ugynok"], "allapot": kor["allapot"], "hiba": kor.get("hiba"),
            "valaszok": valaszok, "eszkozok": eszk, "modell": k.nyom.get("modell"),
            "szolgaltato": k.nyom.get("szolgaltato"), "faber": k.nyom.get("faber"), "indult": kor["indult_iso"]}


@_vedett
async def api_chat(request: Request):
    """POST /api/ui/chat {szoveg, ugynok} → {kor_id}. A kör a háttérben fut; állapota: GET /api/ui/chat/{kor_id}."""
    body = await request.json()
    szoveg = str(body.get("szoveg") or "").strip()
    ugynok = str(body.get("ugynok") or "feldwebel")
    idk = body.get("mellekletek") or []
    if not szoveg and not idk:
        return JSONResponse({"hiba": "üres üzenet"}, status_code=400)
    if len(szoveg) > 8000:
        return JSONResponse({"hiba": "túl hosszú üzenet (legfeljebb 8000 jel)"}, status_code=400)
    if ugynok not in UGYNOKOK:
        return JSONResponse({"hiba": f"ismeretlen ügynök: {ugynok}"}, status_code=400)
    if idk:
        # a feltöltött mellékletek szövege a kérés alá (melleklet.py) — a Feldwebel így olvassa, és emlékszik rá
        import melleklet
        mm = melleklet.betolt(_DEPS["get_db"], idk if isinstance(idk, list) else [idk])
        if not mm:
            return JSONResponse({"hiba": "a melléklet nem található — töltsd fel újra"}, status_code=400)
        szoveg = melleklet.uzenet(szoveg, mm)
    most = _most()
    with _ZAR:
        for kid, kk in list(_KOROK.items()):
            if kk.get("vege") and most - kk["vege"] > 1800:
                del _KOROK[kid]
    kor = {"id": uuid.uuid4().hex[:16], "ugynok": ugynok, "szoveg": szoveg, "allapot": "fut",
           "kimenet": kimenet_mod.Kimenet("web"), "indult": most, "indult_iso": _iso()}
    kor["task"] = asyncio.get_running_loop().create_task(_kor_fut(kor))
    with _ZAR:
        _KOROK[kor["id"]] = kor
    return JSONResponse({"kor_id": kor["id"], "allapot": "fut"})


@_vedett
async def api_chat_kor(request: Request):
    kor = _KOROK.get(request.path_params["kor_id"])
    if not kor:
        return JSONResponse({"hiba": "nincs ilyen kör (újraindult a Bridge?)", "kod": "nincs_kor"}, status_code=404)
    return JSONResponse(_kor_kifele(kor))


@_vedett
async def api_beszelgetes(request: Request):
    """GET /api/ui/beszelgetes?ablak=feldwebel&elotte=123 — közös előzmény a Telegrammal."""
    ablak = request.query_params.get("ablak", "feldwebel")
    if ablak not in UGYNOKOK and ablak != "postalada" and not ugynok_ablak(ablak):
        return JSONResponse({"hiba": f"ismeretlen ablak: {ablak}"}, status_code=400)
    elotte = request.query_params.get("elotte")
    conn = _DEPS["get_db"]()
    try:
        adat = tortenet(conn, ablak, faber_relay.kulcs(_DEPS["bot_token"]()),
                        elotte=int(elotte) if elotte and elotte.isdigit() else None)
        if ugynok_ablak(ablak):
            sor = feladat_ugynok._sor(conn, feladat_ugynok.rid_azonositobol(ablak))
            adat["ugynok"] = feladat_ugynok.nezet(sor) if sor is not None else None
        if ablak == "faber":
            adat["kartyak"] = {c["id"]: _kartya_kifele(c) for c in faber_kartyak(conn, faber_relay.kulcs(
                _DEPS["bot_token"]()))}
    finally:
        conn.close()
    futo = [_kor_kifele(k) for k in _KOROK.values() if k["ugynok"] == ablak and k["allapot"] == "fut"]
    adat["futo_korok"] = futo
    return JSONResponse(adat)


# ══ 8. JÓVÁHAGYÁS ════════════════════════════════════════════════════════

@_vedett
async def api_faber_dont(request: Request):
    """POST /api/ui/faber/dont {id, dontes: mehet|megse} → aláírt `gomb` a Fabernek: `{dontes}:{id}:{kod}`."""
    body = await request.json()
    dontes = str(body.get("dontes") or "")
    try:
        kid = int(body.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"hiba": "hiányzó vagy rossz kérelem-azonosító"}, status_code=400)
    if dontes not in ("mehet", "megse"):
        return JSONResponse({"hiba": "a döntés 'mehet' vagy 'megse'"}, status_code=400)
    token = _DEPS["bot_token"]()
    conn = _DEPS["get_db"]()
    try:
        kartya = next((c for c in faber_kartyak(conn, faber_relay.kulcs(token)) if c["id"] == kid), None)
        if kartya is None:
            return JSONResponse({"hiba": f"nincs #{kid} Faber-kérelem"}, status_code=404)
        if not kartya["var_dontesre"]:
            return JSONResponse({"hiba": "erről a kérelemről már döntöttél, vagy nem vár döntésre",
                                 "kartya": _kartya_kifele(kartya)}, status_code=409)
        adat = f"{dontes}:{kid}:{kartya['_kod']}"
        if not faber_relay.gomb_e(adat):
            return JSONResponse({"hiba": "a kérelem kódja nem érvényes — a Faber-kártya hibás"}, status_code=422)
        mid = faber_relay.tovabbit(conn, token, "gomb", adat, _iso())
    finally:
        conn.close()
    if not mid:
        return JSONResponse({"hiba": "a Faber-továbbítás nem megy: nincs Telegram-token a Bridge-en"}, status_code=503)
    logger.info("ui_kabin: Faber-döntés a felületről: %s #%d (Bridge #%d)", dontes, kid, mid)
    return JSONResponse({"ok": True, "bridge_id": mid, "dontes": dontes, "id": kid})


def _draftok(conn) -> list[dict]:
    try:
        sorok = conn.execute("SELECT id, kind, title, due_at, payload_json, created_at FROM action_drafts "
                             "WHERE status = 'pending' ORDER BY created_at DESC, id DESC LIMIT 20").fetchall()
    except Exception:  # noqa: BLE001 — régi DB / teszt: a tábla hiánya nem hiba
        return []
    out = []
    for r in sorok:
        p = _json(r["payload_json"])
        d = {"id": r["id"], "fajta": r["kind"], "cim": r["title"], "mikor": r["due_at"],
             "reszlet": str(p.get("details") or "")[:400], "ido": r["created_at"],
             "forras": str(p.get("forras") or "") or None}
        if r["kind"] == twitter_poszt.KIND:
            # TWEET (2026-11-23): a TELJES szöveg és az X szerinti karakterszám — erre mondja az igent; az előző
            # próba hibája (ha volt) is látszik
            szoveg = str(p.get("szoveg") or r["title"])
            d.update(szoveg=szoveg, karakter=twitter_poszt.hossz(szoveg), max=twitter_poszt.MAX_HOSSZ,
                     hiba=str(p.get("utolso_hiba") or "") or None, reszlet="")
        elif r["kind"] == hog.KIND:
            # OPERATION HOG (2026-11-23): az oldal címe, a kártya-főcím, az elemzés és a tényvonalak (összecsukva), a
            # tweet-szöveg karakterszámmal és a mód (válasz / idézet / saját poszt); ha az oldal már kint van, az is
            d.update(hog.kartya(p))
        out.append(d)
    return out


@_vedett
async def api_draft_dont(request: Request):
    """POST /api/ui/draft/dont {id, dontes: approve|reject} — a Bridge `action_draft_decide` eszközén át (az hozza
    létre jóváhagyáskor a naptáreseményt; a felület nem ír mellé második utat)."""
    body = await request.json()
    dontes = str(body.get("dontes") or "")
    if dontes not in ("approve", "reject"):
        return JSONResponse({"hiba": "a döntés 'approve' vagy 'reject'"}, status_code=400)
    try:
        did = int(body.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"hiba": "hiányzó vagy rossz azonosító"}, status_code=400)
    eredmeny = await _DEPS["draft_dont"](did, dontes)
    if isinstance(eredmeny, dict) and eredmeny.get("error"):
        return JSONResponse({"hiba": eredmeny["error"], "eredmeny": eredmeny}, status_code=409)
    return JSONResponse({"ok": True, "eredmeny": eredmeny})


# ══ 9. ÁLLAPOT (bal sáv + munkapad) ══════════════════════════════════════

def _esemenyek(conn) -> list[dict]:
    out = []
    try:
        for r in conn.execute("SELECT id, title, description, status, assigned_to, assigned_by, updated_at "
                              "FROM tasks ORDER BY updated_at DESC LIMIT 8").fetchall():
            out.append({"tipus": "feladat", "id": r["id"], "cim": r["title"], "allapot": r["status"],
                        "ki": f"{r['assigned_by']} → {r['assigned_to']}", "ido": r["updated_at"],
                        "reszlet": (r["description"] or "")[:600]})
    except Exception:  # noqa: BLE001
        pass
    try:
        for r in conn.execute("SELECT d.id, d.topic, d.status, d.created_by, d.created_at, "
                              "MAX(e.timestamp) AS utolso, COUNT(e.id) AS db FROM discussions d "
                              "LEFT JOIN discussion_entries e ON e.discussion_id = d.id "
                              "GROUP BY d.id ORDER BY COALESCE(MAX(e.timestamp), d.created_at) DESC LIMIT 6").fetchall():
            out.append({"tipus": "vita", "id": r["id"], "cim": r["topic"], "allapot": r["status"],
                        "ki": r["created_by"], "ido": r["utolso"] or r["created_at"], "db": r["db"]})
    except Exception:  # noqa: BLE001
        pass
    try:
        for r in conn.execute("SELECT id, kind, session, asof, created_at FROM briefs WHERE lang = 'hu' "
                              "ORDER BY id DESC LIMIT 4").fetchall():
            nev = {"economic": "Gazdasági brief", "area": "Térségi brief"}.get(r["kind"], f"Brief ({r['kind']})")
            out.append({"tipus": "brief", "id": r["id"], "cim": f"{nev} · {r['session']}", "allapot": "kész",
                        "ki": "bridge", "ido": r["created_at"]})
    except Exception:  # noqa: BLE001
        pass
    out.sort(key=lambda e: e.get("ido") or "", reverse=True)
    return out[:14]


def _vitak(conn) -> list[dict]:
    try:
        return [{"id": r["id"], "cim": r["topic"], "ki": r["created_by"], "ido": r["created_at"]}
                for r in conn.execute("SELECT id, topic, created_by, created_at FROM discussions WHERE status = 'open' "
                                      "ORDER BY created_at DESC LIMIT 12").fetchall()]
    except Exception:  # noqa: BLE001
        return []


# ══ 9/0. KÖZÖS FELADATOK (ai_tasks — Kimi, DeepSeek, GLM közös munkája) ═══════
#
# A Kommandant (11-23): „nem látom a boardon … azt se látom a dashboardon, h meg lettek kérdezve”. Az `ai_task`
# MCP-eszköz (server.py) feladatai: a három ügynök dolgozik rajta, a végén a szintézis. A lista a munkapadon jön az
# `allapot`-tal (12 mp-enként), tartalom nélkül; a részletek (leírás, ügynökönként a teljes válasz a modellel, a
# szintézis, a DOCX-export) kattintásra a `/api/ui/feladat/{id}`-ről. Csak olvas, a közös `get_db()`-n át.
#
# A „pending” önmagában semmit nem mond (a dispatch-mód a futás alatt végig „pending”, a broadcast „running”), ezért
# az állapot az `ai_task_results` soraiból jön:
#   * broadcast: a három ügynök egyenként ír → „n/3 ügynök kész”;
#   * dispatch: az ügynökök sorai EGYSZERRE jönnek, a darabszámuk addig nincs a DB-ben → „ügynökök dolgoznak”;
#   * minden ügynök kész, szintézis még nincs → „összefoglaló készül”; `completed` → „kész”;
#   * `failed` vagy `no_usable_agent_result` → „hibás” a kóddal; 3 óra csend befejezés nélkül → „megakadt”.
#
# HOZZÁSZÓLÁS (11-23, a Kommandant: „abba is bele kéne tudnom szólni”): `POST /api/ui/feladat/{id}/hozzaszolas`
# egy FOLYTATÓ közös feladatot indít a meglévő `ai_task` eszközön át (deps["ai_task_indit"]). Címe
# „↳ #<eredeti>: <cím>” — új oszlop nincs, a kapcsolat a címelőtagból jön; a hozzászólás a kontextus elején áll,
# onnan olvassuk vissza. Az ügynökök csak a Kommandant szövegét és a feladat anyagát kapják (cím, leírás, a korábbi
# válaszok rövidítve) — tudásbázist, memóriát, levelet, naptárt nem; az őrök az `ai_task` úton változatlanok.

KOZOS_FELADAT_MAX = 10
SZINTEZIS = "szintézis"
DISPATCH_SZEREP = "Pyramid dispatch"
FAJL_SZEREP = "📎 Generált fájl"
MEGAKADT_S = 3 * 3600
AI_TASK_NEV = {"kimi": "Kimi", "deepseek": "DeepSeek", "glm5": "GLM", SZINTEZIS: "Összefoglaló"}
_HIBA_ELEJE = ("ERROR:", "TIMEOUT", "(no response)")
_FAJL_RE = re.compile(r"file_id=(\d+), formátum=(\w+)")
_SZINTEZIS_MODELL_RE = re.compile(r"\b(kimi|deepseek|glm5|none)\b")
#: a folytató feladat címe és a hozzászólás keretezése a kontextusban (ebből olvassuk vissza)
FOLYTATAS_RE = re.compile(r"^↳ #(\d+): ")
HOZZASZOLAS_FEJ = "A KOMMANDANT HOZZÁSZÓLÁSA: "
HOZZASZOLAS_LAB = "\n— erre reagálj elsőként, ehhez igazítsd a választ."
_HOZZASZOLAS_RE = re.compile(re.escape(HOZZASZOLAS_FEJ) + r"(.*?)" + re.escape(HOZZASZOLAS_LAB), re.S)
HOZZASZOLAS_MAX = 4000
#: az `ai_task` a kontextust 10 000 karakterre vágja — ebbe kell beférnie a hozzászólásnak és a korábbi válaszoknak
KONTEXTUS_KERET = 9800
VALASZ_RESZLET_MAX = 3000
LEIRAS_RESZLET_MAX = 1500


def _ts(iso) -> float | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def _sor_hibas(sor: dict) -> bool:
    return bool(sor.get("error_code")) or str(sor.get("eleje") or "").lstrip().startswith(_HIBA_ELEJE)


def _fajl_sor(sor: dict) -> bool:
    return str(sor.get("role") or "").startswith(FAJL_SZEREP)


def _ugynok_sor(sor: dict) -> bool:
    return sor.get("agent") != SZINTEZIS and not _fajl_sor(sor)


def feladat_allapot(task: dict, sorok: list[dict], most: float | None = None) -> dict:
    """Beszédes állapot egy `ai_tasks` sorhoz. `task`: status, error_code, created_at; `sorok`: az `ai_task_results`
    sorai id szerint (agent, role, error_code, eleje = a tartalom eleje, timestamp)."""
    most = _most() if most is None else most
    status = str(task.get("status") or "pending").lower()
    kod = str(task.get("error_code") or "")
    ugynok_sorok = [s for s in sorok if _ugynok_sor(s)]
    szint = [s for s in sorok if s.get("agent") == SZINTEZIS]
    dispatch = any(s.get("role") == DISPATCH_SZEREP for s in ugynok_sorok)
    kesz = list(dict.fromkeys(s["agent"] for s in ugynok_sorok))
    hibas = list(dict.fromkeys(s["agent"] for s in ugynok_sorok if _sor_hibas(s)))
    if dispatch:
        vart = list(kesz)                                  # a dispatch egyszerre írja az összes ügynökét
    elif status == "pending" and not kesz:
        vart = None                                        # dispatch fut (vagy épp indul): a létszám nincs a DB-ben
    else:
        vart = list(LLM_UGYNOKOK) + [a for a in kesz if a not in LLM_UGYNOKOK]
    szint_kod = next((s["error_code"] for s in szint if s.get("error_code")), "")
    utolso = max([task.get("created_at")] + [s.get("timestamp") for s in sorok], key=lambda x: _ts(x) or 0)
    reszlet = []
    if status == "failed" or kod == "no_usable_agent_result" or szint_kod:
        szoveg, fajta = "hibás", "hiba"
        kod = kod or szint_kod or next((s["error_code"] for s in ugynok_sorok if s.get("error_code")), "")
    elif status == "completed":
        szoveg, fajta = "kész", "kesz"
        if len(kesz) > 1 and not szint:
            reszlet.append("összefoglaló nélkül")
    elif most - (_ts(utolso) or most) > MEGAKADT_S:
        szoveg, fajta = "megakadt", "hiba"
        reszlet.append(f"{int((most - _ts(utolso)) // 3600)} órája nincs új eredmény, és nem fejeződött be")
    elif vart is None:
        szoveg, fajta = "ügynökök dolgoznak", "fut"
    elif len(kesz) < len(vart):
        szoveg, fajta = f"{len(kesz)}/{len(vart)} ügynök kész", "fut"
    elif not szint and len(vart) > 1:
        szoveg, fajta = "összefoglaló készül", "fut"
    else:
        szoveg, fajta = "lezárás…", "fut"
    if hibas and fajta != "hiba":
        reszlet.append(f"hibás: {', '.join(AI_TASK_NEV.get(a, a) for a in hibas)}")
    ugynokok = [{"id": a, "nev": AI_TASK_NEV.get(a, a),
                 "allapot": "hiba" if a in hibas else "kesz" if a in kesz else "var"}
                for a in (vart if vart is not None else kesz)]
    return {"allapot": status, "allapot_szoveg": szoveg, "fajta": fajta, "hibakod": kod or None,
            "valaszolt": len(kesz), "vart": len(vart) if vart is not None else None, "ugynokok": ugynokok,
            "szintezis": bool(szint), "mod": "dispatch" if dispatch else "broadcast" if vart is not None else None,
            "utolso": utolso, "reszlet": " · ".join(reszlet) or None}


def _feladat_sorok(conn, where: str, params: tuple, *, oszlopok: str = "id, title, assigned_by, status, created_at, "
                   "completed_at") -> list[dict]:
    """`ai_tasks` sorok dictként; a régi sémán (nincs `error_code` oszlop) is megy."""
    try:
        sorok = conn.execute(f"SELECT {oszlopok}, COALESCE(error_code, '') AS error_code FROM ai_tasks {where}",
                             params).fetchall()
    except Exception:  # noqa: BLE001 — migrálatlan DB: a hibakód nélkül
        sorok = conn.execute(f"SELECT {oszlopok}, '' AS error_code FROM ai_tasks {where}", params).fetchall()
    return [dict(r) for r in sorok]


def _eredmeny_sorok(conn, task_ids: list[int], *, teljes: bool = False) -> dict[int, list[dict]]:
    """task_id → az `ai_task_results` sorai id szerint. `teljes=False`: csak a tartalom eleje (a listához)."""
    if not task_ids:
        return {}
    q = ",".join("?" * len(task_ids))
    tartalom = "content" if teljes else "''"
    alap = (f"SELECT id, task_id, agent, role, {{kod}} AS error_code, substr(content, 1, 40) AS eleje, "
            f"{tartalom} AS content, timestamp FROM ai_task_results WHERE task_id IN ({q}) ORDER BY id")
    try:
        sorok = conn.execute(alap.format(kod="COALESCE(error_code, '')"), task_ids).fetchall()
    except Exception:  # noqa: BLE001 — migrálatlan DB
        sorok = conn.execute(alap.format(kod="''"), task_ids).fetchall()
    out: dict[int, list[dict]] = {}
    for r in sorok:
        out.setdefault(r["task_id"], []).append(dict(r))
    return out


def _elozmeny_id(cim: str) -> int | None:
    m = FOLYTATAS_RE.match(cim or "")
    return int(m.group(1)) if m else None


def _hozzaszolas(kontextus: str) -> str | None:
    m = _HOZZASZOLAS_RE.search(kontextus or "")
    return m.group(1).strip() if m else None


def _lista_elem(t: dict, sorok: list[dict], most: float | None = None) -> dict:
    return {"id": t["id"], "cim": t["title"], "ki": t["assigned_by"], "ido": t["created_at"],
            "kesz_ido": t.get("completed_at"), "elozmeny": _elozmeny_id(t["title"]),
            **feladat_allapot(t, sorok, most)}


def kozos_feladatok(conn, limit: int = KOZOS_FELADAT_MAX, most: float | None = None) -> list[dict]:
    """A munkapad listája: a legutóbbi `limit` közös feladat, tartalom nélkül, beszédes állapottal."""
    try:
        tasks = _feladat_sorok(conn, "ORDER BY id DESC LIMIT ?", (limit,))
    except Exception:  # noqa: BLE001 — régi DB / teszt: a tábla hiánya nem hiba
        return []
    sorok = _eredmeny_sorok(conn, [t["id"] for t in tasks])
    return [_lista_elem(t, sorok.get(t["id"], []), most) for t in tasks]


def _szintezis_modell(role: str) -> str | None:
    m = _SZINTEZIS_MODELL_RE.search(role or "")
    nev = m.group(1) if m else ("kimi" if (role or "").startswith("Koordinátori összefoglaló") else None)
    return None if nev in (None, "none") else nev


def _modell(ugynok: str | None, mod: str) -> str | None:
    f = _DEPS.get("ai_task_modell")
    return f(ugynok, mod) if (f and ugynok) else None


def feladat_reszletek(conn, fid: int) -> dict | None:
    tasks = _feladat_sorok(conn, "WHERE id = ?", (fid,), oszlopok="id, title, description, context, assigned_by, "
                           "status, created_at, completed_at")
    if not tasks:
        return None
    t = tasks[0]
    sorok = _eredmeny_sorok(conn, [fid], teljes=True).get(fid, [])
    alap = _lista_elem(t, sorok)
    mod = "dispatch" if alap["mod"] == "dispatch" else "broadcast"
    valaszok, fajlok, szintezis = [], [], None
    for s in sorok:
        if _fajl_sor(s):
            m = _FAJL_RE.search(s["content"] or "")
            if m:
                fajlok.append({"ugynok": s["agent"], "nev": AI_TASK_NEV.get(s["agent"], s["agent"]),
                               "formatum": m.group(2), "url": f"/api/uploads/{m.group(1)}/download"})
            continue
        if s["agent"] == SZINTEZIS:
            ki = _szintezis_modell(s["role"])
            szintezis = {"szoveg": s["content"], "szerep": s["role"], "ido": s["timestamp"],
                         "hibakod": s["error_code"] or None, "ugynok": ki, "modell": _modell(ki, "szintezis")}
            continue
        valaszok.append({"ugynok": s["agent"], "nev": AI_TASK_NEV.get(s["agent"], s["agent"]),
                         "modell": _modell(s["agent"], mod), "szerep": s["role"], "szoveg": s["content"],
                         "ido": s["timestamp"], "hibakod": s["error_code"] or None, "hibas": _sor_hibas(s)})
    # FOLYTATÁS: vissza az eredetire, és előre a hozzászólásokból indult folytatásokra (a címelőtagból)
    elozmeny = None
    if alap["elozmeny"]:
        e = _feladat_sorok(conn, "WHERE id = ?", (alap["elozmeny"],))
        elozmeny = {"id": e[0]["id"], "cim": e[0]["title"]} if e else None
    foly = _feladat_sorok(conn, "WHERE title LIKE ? ORDER BY id", (f"↳ #{fid}: %",),
                          oszlopok="id, title, context, assigned_by, status, created_at, completed_at")
    foly_sorok = _eredmeny_sorok(conn, [f["id"] for f in foly])
    folytatasok = [{**_lista_elem(f, foly_sorok.get(f["id"], [])), "hozzaszolas": _hozzaszolas(f["context"])}
                   for f in foly]
    return {**alap, "leiras": t["description"], "kontextus": t["context"] or "",
            "hozzaszolas": _hozzaszolas(t["context"]) if alap["elozmeny"] else None,
            "valaszok": valaszok, "szintezis": szintezis, "fajlok": fajlok,
            "elozmeny_feladat": elozmeny, "folytatasok": folytatasok,
            "export": f"/api/ai_tasks/{fid}/export" if alap["allapot"] == "completed" and sorok else None}


def folytatas_argumentumok(r: dict, szoveg: str) -> dict:
    """A hozzászólásból indított folytató feladat `ai_task`-argumentumai. A kontextus legelején a Kommandant szövege,
    alatta az eredeti cím és leírás, aztán ügynökönként a korábbi válasz rövidítve (a 10 000 karakteres keretben)."""
    eredeti_cim = FOLYTATAS_RE.sub("", r["cim"]) if r.get("elozmeny") else r["cim"]
    fej = (f"{HOZZASZOLAS_FEJ}{szoveg}{HOZZASZOLAS_LAB}\n\n"
           f"=== AZ EREDETI KÖZÖS FELADAT (#{r['id']}) ===\nCÍM: {eredeti_cim}\n"
           f"LEÍRÁS: {(r.get('leiras') or '')[:LEIRAS_RESZLET_MAX]}\n")
    valaszok = [v for v in r["valaszok"] if not v["hibas"]] or r["valaszok"]
    darab = max(1, len(valaszok))
    jut = max(400, min(VALASZ_RESZLET_MAX, (KONTEXTUS_KERET - len(fej)) // darab - 80))
    blokkok = []
    for v in valaszok:
        t = v["szoveg"] or ""
        blokkok.append(f"\n=== {v['nev'].upper()} KORÁBBI VÁLASZA{' (rövidítve)' if len(t) > jut else ''} ===\n"
                       f"{t[:jut]}")
    kontextus = (fej + "".join(blokkok))[:KONTEXTUS_KERET]
    args = {"title": f"↳ #{r['id']}: {eredeti_cim}"[:300], "assigned_by": "kommandant", "context": kontextus,
            "description": (f"A Kommandant hozzászólt a #{r['id']}-es közös feladathoz. Reagálj a hozzászólására "
                            "elsőként, aztán ahhoz igazítva add meg a javított, teljes válaszodat. A hozzászólás, az "
                            "eredeti feladat és a korábbi válaszok a kontextusban vannak.")}
    if r.get("mod") == "dispatch" and valaszok:
        # dispatch-ban a kontextus nem jut el az ügynökökhöz: a promptjukba kerül, ügynökönként a saját szerepével
        args["agent_tasks"] = json.dumps({v["ugynok"]: {
            "prompt": (f"{args['description']}\n\n{kontextus}\n\nTE: {v['nev']} vagy — a fenti „{v['nev'].upper()} "
                       "KORÁBBI VÁLASZA” a TE részfeladatod volt; azt folytasd a Kommandant hozzászólása szerint."),
            "max_tokens": 8000} for v in valaszok}, ensure_ascii=False)
    return args


@_vedett
async def api_feladat(request: Request):
    """GET /api/ui/feladat/{id}: egy közös feladat részletei — leírás, ügynökönként a teljes válasz a modellel, a
    szintézis, a fájlok, a DOCX-export (ha kész), és a folytatások (hozzászólások) mindkét irányban."""
    fid = int(request.path_params["feladat_id"])
    conn = _DEPS["get_db"]()
    try:
        r = feladat_reszletek(conn, fid)
    finally:
        conn.close()
    if r is None:
        return JSONResponse({"hiba": "nincs ilyen közös feladat"}, status_code=404)
    return JSONResponse(r)


@_vedett
async def api_feladat_hozzaszolas(request: Request):
    """POST /api/ui/feladat/{id}/hozzaszolas {szoveg} → új folytató közös feladat az `ai_task` eszközön át."""
    fid = int(request.path_params["feladat_id"])
    body = await request.json()
    szoveg = str((body or {}).get("szoveg") or "").strip() if isinstance(body, dict) else ""
    if not szoveg:
        return JSONResponse({"hiba": "üres hozzászólás"}, status_code=400)
    if len(szoveg) > HOZZASZOLAS_MAX:
        return JSONResponse({"hiba": f"túl hosszú (legfeljebb {HOZZASZOLAS_MAX} karakter)"}, status_code=400)
    if not _DEPS.get("ai_task_indit"):
        return JSONResponse({"hiba": "a közös feladat indítása nincs bekötve"}, status_code=503)
    conn = _DEPS["get_db"]()
    try:
        r = feladat_reszletek(conn, fid)
    finally:
        conn.close()
    if r is None:
        return JSONResponse({"hiba": "nincs ilyen közös feladat"}, status_code=404)
    args = folytatas_argumentumok(r, szoveg)
    eredmeny = await _DEPS["ai_task_indit"](args)
    uj = (eredmeny or {}).get("task_id") if isinstance(eredmeny, dict) else None
    if not uj:
        hiba = (eredmeny or {}).get("error") if isinstance(eredmeny, dict) else None
        return JSONResponse({"hiba": hiba or "az ai_task nem indult el", "eredmeny": eredmeny}, status_code=502)
    logger.info("ui_kabin: hozzászólás a #%d közös feladathoz → folytatás #%s (%s)", fid, uj,
                "dispatch" if "agent_tasks" in args else "broadcast")
    return JSONResponse({"ok": True, "id": uj, "elozmeny": fid, "cim": args["title"],
                         "mod": "dispatch" if "agent_tasks" in args else "broadcast"})


@_vedett
async def api_allapot(request: Request):
    token = _DEPS["bot_token"]()
    k_faber = faber_relay.kulcs(token)
    conn = _DEPS["get_db"]()
    try:
        utolsok, utolso_id = _utolsok(conn, k_faber)
        kartyak = faber_kartyak(conn, k_faber)
        draftok = _draftok(conn)
        esemenyek = _esemenyek(conn)
        vitak = _vitak(conn)
        kozos = kozos_feladatok(conn)
        try:
            feladat_ugynokok = feladat_ugynok.lista(conn)
        except Exception as e:  # noqa: BLE001 — régi DB / teszt: a recept-tábla hiánya nem hiba
            logger.warning("ui_kabin: a feladat-ügynökök nem olvashatók: %s", e)
            feladat_ugynokok = []
        try:
            hb = conn.execute("SELECT * FROM heartbeats").fetchall()
        except Exception:  # noqa: BLE001
            hb = []
    finally:
        conn.close()
    jelenlet = _DEPS["jelenlet"](hb) if _DEPS.get("jelenlet") else {}
    elerhetoseg = _DEPS["ugynokok"]() if _DEPS.get("ugynokok") else {}
    ugynokok = []
    for uid, (nev, szerep) in UGYNOKOK.items():
        e = elerhetoseg.get(uid, {})
        u = utolsok.get(uid) or {}
        utoljara = u.get("ido")
        if uid == "faber":
            hb_faber = (jelenlet.get("faber") or {}).get("last_seen")
            utoljara = max(filter(None, [utoljara, hb_faber]), default=None)
        ugynokok.append({"id": uid, "nev": nev, "szerep": szerep, "elerheto": bool(e.get("elerheto", uid == "faber")),
                         "modell": e.get("modell"), "megjegyzes": e.get("megjegyzes"), "utoljara": utoljara,
                         "utolso": u or None,
                         "fut": any(k["ugynok"] == uid and k["allapot"] == "fut" for k in _KOROK.values())})
    for f in feladat_ugynokok:
        u = utolsok.get(f["id"]) or {}
        megj = f["allapot_szoveg"] + (f" · köv.: {f['kovetkezo_szoveg']}" if f.get("kovetkezo_szoveg") else "")
        ugynokok.append({"id": f["id"], "nev": f["nev"], "szerep": f["utemezes_szoveg"],
                         "elerheto": f["allapot"] == "aktiv", "modell": None, "megjegyzes": megj,
                         "utoljara": u.get("ido") or (f.get("utolso_futas") or {}).get("ido"), "utolso": u or None,
                         "fut": f["fut"], "feladat_ugynok": f})
    postalada = utolsok.get("postalada")
    return JSONResponse({
        "most": _iso(),
        "ugynokok": ugynokok,
        "postalada": postalada,
        "vitak": vitak,
        "peldanyok": {k: v for k, v in jelenlet.items() if k in ("cli-claus", "web-claus")},
        "jovahagyas": {"faber": [_kartya_kifele(c) for c in kartyak if c["var_dontesre"]],
                       "faber_friss": [_kartya_kifele(c) for c in kartyak if not c["var_dontesre"]][:4],
                       "draftok": draftok},
        "esemenyek": esemenyek,
        "kozos_feladatok": kozos,
        "llm_kapu": _DEPS["llm_allapot"]() if _DEPS.get("llm_allapot") else None,
        "feladat_ugynok_modellek": feladat_ugynok.MODELL_VALASZTEK,
        "utolso_id": utolso_id,
    })


@_vedett
async def api_vita(request: Request):
    """GET /api/ui/vita/{id}: a vita bejegyzései; POST {szoveg}: hozzászólás a Kommandant nevében."""
    vid = int(request.path_params["vita_id"])
    conn = _DEPS["get_db"]()
    try:
        if request.method == "POST":
            body = await request.json()
            szoveg = str(body.get("szoveg") or "").strip()
            if not szoveg:
                return JSONResponse({"hiba": "üres hozzászólás"}, status_code=400)
            if not conn.execute("SELECT 1 FROM discussions WHERE id = ?", (vid,)).fetchone():
                return JSONResponse({"hiba": "nincs ilyen vita"}, status_code=404)
            conn.execute("INSERT INTO discussion_entries (discussion_id, instance, content, timestamp) "
                         "VALUES (?, 'kommandant', ?, ?)", (vid, szoveg[:8000], _iso()))
            conn.commit()
        d = conn.execute("SELECT * FROM discussions WHERE id = ?", (vid,)).fetchone()
        if not d:
            return JSONResponse({"hiba": "nincs ilyen vita"}, status_code=404)
        bej = conn.execute("SELECT id, instance, content, timestamp FROM discussion_entries WHERE discussion_id = ? "
                           "ORDER BY timestamp ASC", (vid,)).fetchall()
    finally:
        conn.close()
    return JSONResponse({"id": d["id"], "cim": d["topic"], "kontextus": d["context"], "allapot": d["status"],
                         "dontes": d["resolution"], "ki": d["created_by"], "ido": d["created_at"],
                         "bejegyzesek": [{"id": b["id"], "ki": b["instance"], "szoveg": b["content"],
                                          "ido": b["timestamp"]} for b in bej]})


@_vedett
async def api_brief(request: Request):
    bid = int(request.path_params["brief_id"])
    conn = _DEPS["get_db"]()
    try:
        r = conn.execute("SELECT id, kind, session, asof, payload, created_at FROM briefs WHERE id = ?",
                         (bid,)).fetchone()
    finally:
        conn.close()
    if not r:
        return JSONResponse({"hiba": "nincs ilyen brief"}, status_code=404)
    p = _json(r["payload"])
    szoveg = p.get("telegram_digest") or p.get("macro_review") or p.get("osszefoglalo") or ""
    if not isinstance(szoveg, str) or not szoveg.strip():
        szoveg = "```\n" + json.dumps(p, ensure_ascii=False, indent=1)[:6000] + "\n```"   # nincs emberi kivonat
    return JSONResponse({"id": r["id"], "fajta": r["kind"], "ido": r["created_at"],
                         "szoveg": tg_szoveg(szoveg, "feldwebel")["szoveg"]})


# ══ 9/A. FELADAT-ÜGYNÖKÖK (feladat_ugynok.py) ═════════════════════════════

UGYNOK_MEZOK = ("nev", "feladat", "utemezes", "egyszeri", "ertesites", "keret_usd", "havi_keret_usd", "modell",
               "extra_prompt", "vitapartnerek", "utasithat", "vita_max", "dokumentum", "kaszkad")
#: 11-23: „dokumentum” = a legutóbbi jelentés Word-dokumentumként (a Kommandant: „kéne ez nekem dokumentumban”)
UGYNOK_KABIN_MUVELETEK = ("futtat", "leallit", "ujraindit", "megszuntet", "dokumentum")


def _ugynok_valasz(out: dict) -> JSONResponse:
    if out.get("error"):
        kod = 404 if str(out["error"]).startswith("nincs ilyen") else 400
        return JSONResponse({**out, "hiba": out["error"]}, status_code=kod)
    return JSONResponse(out)


@_vedett
async def api_ugynok(request: Request):
    """POST /api/ui/ugynok {nev, feladat, utemezes, egyszeri, ertesites, keret_usd, havi_keret_usd} — „+ Új ügynök”.
    Ugyanaz a belépő, mint a Feldwebel `ugynok` eszközéé (feladat_ugynok.vegrehajt); ütemezés nélkül most fut."""
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"hiba": "rossz kérés"}, status_code=400)
    return _ugynok_valasz(feladat_ugynok.vegrehajt("indit", {k: body.get(k) for k in UGYNOK_MEZOK},
                                                   get_db=_DEPS["get_db"], ki="kabin"))


@_vedett
async def api_ugynok_modosit(request: Request):
    """POST /api/ui/ugynok/modosit {id: "ugynok:12", …a változó mezők} — a „Szerkeszt” űrlap. Csak a megadott mezők
    változnak (`feladat_ugynok.MODOSITHATO`); a jogosultságot (vitapartnerek, utasíthat) itt a Kommandant adja."""
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"hiba": "rossz kérés"}, status_code=400)
    uid = str(body.get("id") or "")
    if not ugynok_ablak(uid):
        return JSONResponse({"hiba": "hiányzó vagy rossz ügynök-azonosító (ugynok:<szám>)"}, status_code=400)
    args = {k: body[k] for k in feladat_ugynok.MODOSITHATO if k in body}
    args["id"] = uid
    return _ugynok_valasz(feladat_ugynok.vegrehajt("modosit", args, get_db=_DEPS["get_db"], ki="kabin"))


@_vedett
async def api_ugynok_muvelet(request: Request):
    """POST /api/ui/ugynok/muvelet {id: "ugynok:12", muvelet: futtat|leallit|ujraindit|megszuntet}."""
    body = await request.json()
    uid, muvelet = str((body or {}).get("id") or ""), str((body or {}).get("muvelet") or "")
    if not ugynok_ablak(uid):
        return JSONResponse({"hiba": "hiányzó vagy rossz ügynök-azonosító (ugynok:<szám>)"}, status_code=400)
    if muvelet not in UGYNOK_KABIN_MUVELETEK:
        return JSONResponse({"hiba": f"a művelet: {', '.join(UGYNOK_KABIN_MUVELETEK)}"}, status_code=400)
    return _ugynok_valasz(feladat_ugynok.vegrehajt(muvelet, {"id": uid}, get_db=_DEPS["get_db"], ki="kabin"))


@_vedett
async def api_ugynok_utasitas(request: Request):
    """POST /api/ui/ugynok/utasitas {id: "ugynok:12", szoveg} — a Kommandant ÚJ UTASÍTÁSA a feladat-ügynöknek (11-23:
    „itt egyébként nem tudok az agentnek új utasítást adni”). Egy azonnali, egyszeri futás ugyanazzal az ügynökkel, a
    „Futtasd most” útján (`feladat_ugynok.vegrehajt('futtat', utasitas=True)`); az állandó feladat NEM változik."""
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"hiba": "rossz kérés"}, status_code=400)
    uid, szoveg = str(body.get("id") or ""), str(body.get("szoveg") or "").strip()
    if not ugynok_ablak(uid):
        return JSONResponse({"hiba": "hiányzó vagy rossz ügynök-azonosító (ugynok:<szám>)"}, status_code=400)
    if not szoveg:
        return JSONResponse({"hiba": "üres utasítás"}, status_code=400)
    if len(szoveg) > feladat_ugynok.BEMENET_MAX:
        return JSONResponse({"hiba": f"túl hosszú (legfeljebb {feladat_ugynok.BEMENET_MAX} karakter)"}, status_code=400)
    out = feladat_ugynok.vegrehajt("futtat", {"id": uid, "bemenet": szoveg}, get_db=_DEPS["get_db"], ki="kabin",
                                   utasitas=True)
    futas = out.get("futas") or {}
    if not out.get("error") and not futas.get("indult"):
        return JSONResponse({**out, "hiba": futas.get("hiba") or "a futás nem indult el"}, status_code=409)
    return _ugynok_valasz(out)


# ══ 9/B. MELLÉKLET-FELTÖLTÉS (fénykép, PDF, Word… → Studio + szöveg) ═════════════

MELLEKLET_MAX = 50 * 1024 * 1024


@_vedett
async def api_melleklet(request: Request):
    """POST /api/ui/melleklet (multipart, mező: `fajl`) → {id, nev, cimke, karakter, studio}. A kliens az `id`-t a
    következő `POST /api/ui/chat` `mellekletek` listájában küldi; a szöveg és a Studio-fájl a szerveren marad."""
    import melleklet
    if int(request.headers.get("content-length") or 0) > MELLEKLET_MAX + 1024 * 1024:
        return JSONResponse({"hiba": "a fájl túl nagy (legfeljebb 50 MB)"}, status_code=413)
    urlap = await request.form()
    f = urlap.get("fajl")
    if f is None or not hasattr(f, "read"):
        return JSONResponse({"hiba": "nincs fájl a kérésben"}, status_code=400)
    raw = await f.read()
    if not raw:
        return JSONResponse({"hiba": "üres fájl"}, status_code=400)
    try:
        m = await melleklet.feldolgoz(raw, f.filename or "melleklet", f.content_type or "",
                                      get_db=_DEPS["get_db"], most=_iso, kitol="kommandant-web")
    except ValueError as e:
        return JSONResponse({"hiba": str(e)}, status_code=422)
    return JSONResponse({"id": m["id"], "nev": m["nev"], "cimke": m["cimke"], "karakter": len(m["szoveg"]),
                         "megjegyzesek": m["megjegyzesek"], "studio": m["studio"]})


# ══ 10. REGISZTRÁCIÓ ═════════════════════════════════════════════════════

#: Az összes `/api/ui/*` út — a teszt ezen megy végig, hogy süti nélkül mind 401.
API_UTAK = [
    ("/api/ui/allapot", ["GET"], api_allapot),
    ("/api/ui/beszelgetes", ["GET"], api_beszelgetes),
    ("/api/ui/chat", ["POST"], api_chat),
    ("/api/ui/melleklet", ["POST"], api_melleklet),
    ("/api/ui/chat/{kor_id}", ["GET"], api_chat_kor),
    ("/api/ui/faber/dont", ["POST"], api_faber_dont),
    ("/api/ui/draft/dont", ["POST"], api_draft_dont),
    ("/api/ui/vita/{vita_id:int}", ["GET", "POST"], api_vita),
    ("/api/ui/brief/{brief_id:int}", ["GET"], api_brief),
    ("/api/ui/feladat/{feladat_id:int}", ["GET"], api_feladat),
    ("/api/ui/feladat/{feladat_id:int}/hozzaszolas", ["POST"], api_feladat_hozzaszolas),
    ("/api/ui/ugynok", ["POST"], api_ugynok),
    ("/api/ui/ugynok/muvelet", ["POST"], api_ugynok_muvelet),
    ("/api/ui/ugynok/modosit", ["POST"], api_ugynok_modosit),
    ("/api/ui/ugynok/utasitas", ["POST"], api_ugynok_utasitas),
]


def regisztral(app, deps: dict) -> None:
    """A FastMCP-app custom_route-jaira köt. `deps`:
      get_db, bot_token(), chat_id(), telegram_kuld(szoveg) [async, bukásnál dob], asszisztens(szoveg, kimenet,
      csatorna) [async], ugynokok() → {id: {elerheto, modell}}, llm_allapot(), jelenlet(heartbeat-sorok),
      draft_dont(id, dontes) [async], ai_task_modell(ugynok, mod) → modell-azonosító, ai_task_indit(args) [async]
      → az `ai_task` eszköz JSON-válasza (`task_id`)."""
    global _DEPS
    _DEPS = deps
    r = app.custom_route
    r("/app", methods=["GET"])(app_gyoker)
    r("/app/belepes", methods=["GET"])(belepes_lap)
    r("/app/belepes", methods=["POST"])(belepes_post)
    r("/app/belepes/kod", methods=["POST"])(belepes_kod)
    r("/app/belepolink", methods=["POST"])(belepolink_ker)
    r("/app/kilepes", methods=["POST"])(kilepes)
    r("/app/", methods=["GET"])(app_fajl)
    r("/app/{utvonal:path}", methods=["GET"])(app_fajl)
    for ut, modok, fn in API_UTAK:
        r(ut, methods=modok)(fn)
    logger.info("ui_kabin (Bridge UI v2) bekötve: /app + %d /api/ui út", len(API_UTAK))
