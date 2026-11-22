"""API-KAPU (2026-11-22) — a Bridge REST-útjai és a régi műszerfal CSAK belépve vagy munkáskulccsal.

MIÉRT
-----
A régi `/api/*` utak hitelesítés nélkül voltak nyitva: bárki levelet küldhetett a Kommandant Gmailjéből
(`/api/send_email`), letölthette a feltöltött fájlokat (`/api/uploads`), olvashatta az összes üzenetet és
írhatott a közös memóriába (`/api/messages`, `/api/memory` — az ügynökök kontextusa így megmérgezhető volt).

HOGYAN — a Kommandantnak semmit nem kell beírnia
-----------------------------------------------
* **A Kommandant** egyszer belép a felületen (Telegram-link vagy 6 jegyű kód, `ui_kabin`). Ugyanaz a süti
  nyitja a régi `/dashboard`-ot és minden `/api/*` utat is. A süti HASZNÁLAT KÖZBEN MAGÁTÓL MEGÚJUL (csúszó
  30 nap), így aki naponta-hetente benéz, soha nem kap újra belépőlapot.
  Ha belépés nélkül nyit meg egy védett lapot (pl. egy Telegramban kapott linket), a belépés után oda kerül
  vissza (`/app/?vissza=…`).
* **A saját munkások** (Faber és a gépen futó segédek) a `X-Bridge-Key` vagy `Authorization: Bearer` fejlécben
  a munkáskulcsot küldik: `sha256("munkas:" + bot-token)` hexben. A bot tokenje nekik is megvan (a Faber már
  ebből számolja a továbbítás HMAC-kulcsát), tehát új titok és beállítás nem kell.
  Ezen felül a `DELPHOI_BRIDGE_KEY` (az Echolot már küldi) és a `BRIDGE_MUNKAS_KULCSOK` (vesszővel elválasztva,
  opcionális) is munkáskulcs.

MI MARAD SZÁNDÉKOSAN NYILVÁNOS
------------------------------
`/` (Railway-healthcheck), `/app/*` (a belépés maga), `/chat/*` (LESESAAL, saját belépés), `/saas/*` és
`/api/delphoi/*` (DELPHOI, saját kulcs + Stripe-webhook), `/api/brief/*` (saját X-Delphoi-Key), `/api/ui/*`
(az `ui_kabin` maga védi), `/mcp*` (saját hitelesítés), valamint a nyilvános adatok: `/api/news`,
`/api/press_snapshots`, `/api/poll_results`.
Minden más `/api/…` út és a `/dashboard` védett — egy új végpont így alapból zárt, nem lehet elfelejteni.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
from urllib.parse import quote

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, RedirectResponse, Response

import ui_kabin

logger = logging.getLogger("api_kapu")

NYILVANOS_PONTOS = frozenset({"/api/news", "/api/press_snapshots", "/api/poll_results"})
#: Saját hitelesítésük van (vagy szándékosan nyilvánosak) — a kapu átengedi őket.
NYILVANOS_ELOTAG = ("/api/delphoi/", "/api/brief/", "/api/ui/")
VEDETT_LAPOK = frozenset({"/dashboard", "/dashboard/"})
MUNKAS_ELOTAG = "munkas:"
#: A süti akkor újul meg, ha a kiállítása óta legalább ennyi idő telt el (naponta legfeljebb egyszer ír sütit).
MEGUJITAS_S = 24 * 3600


#: Az Anthropic stabil KIMENŐ címei (MCP-hívások külső szerverek felé) — platform.claude.com/docs/en/api/ip-addresses,
#: 2026-11-22: „These addresses will not change without notice.” A claude.ai-csatlakozó innen hív.
ANTHROPIC_KIMENO = tuple(ipaddress.ip_network(h) for h in ("160.79.104.0/21", "153.61.208.0/20"))


def mcp_mod() -> str:
    """BRIDGE_MCP_ZARVA: "1" = zár (a sima /mcp csak az Anthropic címeiről vagy munkáskulccsal); "0" = nyitva;
    üres = FIGYEL (nem zár, csak naplózza, honnan jön a hívás — az élesítés előtti mérés)."""
    v = os.environ.get("BRIDGE_MCP_ZARVA", "").strip()
    return "zar" if v == "1" else "nyitva" if v == "0" else "figyel"


def mcp_zarva() -> bool:
    return mcp_mod() == "zar"


def ip_jeloltek(conn: HTTPConnection) -> dict:
    xff = [x.strip() for x in conn.headers.get("x-forwarded-for", "").split(",") if x.strip()]
    return {"x-real-ip": conn.headers.get("x-real-ip", "").strip(),
            "xff-elso": xff[0] if xff else "", "xff-utolso": xff[-1] if xff else "",
            "kapcsolat": conn.client.host if conn.client else ""}


def kliens_ip(conn: HTTPConnection) -> str:
    """A hívó valódi címe. A Railway-él melyik fejlécét lehet hitelesnek venni, azt a figyelő mód MÉRI (hamisított
    fejléces próbával), és a BRIDGE_KLIENS_IP_FORRAS mondja meg; amíg nincs megmérve, nincs megbízható cím."""
    forras = os.environ.get("BRIDGE_KLIENS_IP_FORRAS", "").strip()
    return ip_jeloltek(conn).get(forras, "") if forras else ""


def anthropic_cim(ip: str) -> bool:
    try:
        cim = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(cim in halo for halo in ANTHROPIC_KIMENO)


def vedett_e(ut: str) -> bool:
    if ut in VEDETT_LAPOK:
        return True
    if not ut.startswith("/api/"):
        return False
    if ut in NYILVANOS_PONTOS or ut.rstrip("/") in NYILVANOS_PONTOS:
        return False
    return not ut.startswith(NYILVANOS_ELOTAG)


def munkas_kulcs(bot_token: str | None) -> str | None:
    """A munkások kulcsa: sha256("munkas:" + bot-token), hexben. Külön tartomány, mint a süti (`ui:`) és a
    Faber-továbbítás (`faber:`) kulcsa — egyikből sem következik a másik."""
    return hashlib.sha256((MUNKAS_ELOTAG + bot_token).encode()).hexdigest() if bot_token else None


def _bot_token() -> str | None:
    f = ui_kabin._DEPS.get("bot_token")
    return f() if f else None


def elfogadott_kulcsok() -> list[str]:
    kulcsok = [munkas_kulcs(_bot_token()), os.environ.get("DELPHOI_BRIDGE_KEY", "").strip()]
    kulcsok += [k.strip() for k in os.environ.get("BRIDGE_MUNKAS_KULCSOK", "").split(",")]
    return [k for k in kulcsok if k and len(k) >= 16]


def _kert_kulcs(conn: HTTPConnection) -> str:
    k = conn.headers.get("x-bridge-key", "").strip()
    if k:
        return k
    auth = conn.headers.get("authorization", "")
    return auth[7:].strip() if auth[:7].lower() == "bearer " else ""


def munkas_e(conn: HTTPConnection) -> bool:
    kert = _kert_kulcs(conn)
    if not kert:
        return False
    # minden kulcsot végignéz (nem áll meg az elsőnél), az összevetés időzítés-biztos
    return any([hmac.compare_digest(kert.encode(), k.encode()) for k in elfogadott_kulcsok()])


def suti_lejarat(conn: HTTPConnection) -> int | None:
    """Az érvényes munkamenet-süti lejárata (unix mp), vagy None."""
    e = ui_kabin._ellenoriz(ui_kabin._k(), "suti", conn.cookies.get(ui_kabin.SUTI_NEV, ""))
    return e[0] if e else None


def _bongeszo_lap(conn: HTTPConnection) -> bool:
    return conn.scope.get("method") == "GET" and "text/html" in conn.headers.get("accept", "")


def _vissza_cel(conn: HTTPConnection) -> str:
    ut = conn.scope.get("path", "/")
    qs = conn.scope.get("query_string", b"").decode("latin-1")
    return ut + (f"?{qs}" if qs else "")


def _megujito_fejlec() -> tuple[bytes, bytes]:
    r = Response()
    ui_kabin._suti_beallit(r)
    return next((k, v) for k, v in r.raw_headers if k == b"set-cookie")


class ApiKapuMiddleware:
    """ASGI-réteg a FastMCP `run(middleware=[…])` horgán — ugyanott, ahol a `YRScopeMiddleware`."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        conn = HTTPConnection(scope)
        ut = scope.get("path", "")
        lejar = suti_lejarat(conn)

        # MCP-AJTÓ (2026-11-22): a sima `/mcp` csak munkáskulccsal; a claude.ai-csatlakozó a titkos példány-úton jön
        # (`/mcp/wc-<token>`, a YRScopeMiddleware ellenőrzi). Kapcsoló: BRIDGE_MCP_ZARVA=1 — a csatlakozó átállítása
        # UTÁN kapcsoljuk be, hogy a Kommandant claude.ai-ja egy percre se essen ki.
        if ut in ("/mcp", "/mcp/"):
            mod = mcp_mod()
            if mod == "figyel":
                j = ip_jeloltek(conn)
                logger.info("api_kapu MCP-figyelés: %s %s · %s", scope.get("method"), ut,
                            " · ".join(f"{k}={v}{'(A)' if anthropic_cim(v) else ''}" for k, v in j.items()))
            elif mod == "zar" and not munkas_e(conn) and not anthropic_cim(kliens_ip(conn)):
                resp = JSONResponse({"hiba": "a Bridge MCP csak a saját csatlakozóról, a titkos példány-úton "
                                             "(/mcp/<példány>-<token>) vagy munkáskulccsal érhető el"}, status_code=401)
                logger.info("api_kapu: MCP elutasítva %s %s (%s)", scope.get("method"), ut, kliens_ip(conn) or "?")
                await resp(scope, receive, send)
                return

        if vedett_e(ut) and lejar is None and not munkas_e(conn):
            if _bongeszo_lap(conn):
                resp = RedirectResponse(f"/app/?vissza={quote(_vissza_cel(conn), safe='')}", status_code=303)
            else:
                resp = JSONResponse({"hiba": "belépés kell", "kod": "nincs_munkamenet",
                                     "belepes": "/app/"}, status_code=401)
            logger.info("api_kapu: elutasítva %s %s", scope.get("method"), ut)
            await resp(scope, receive, send)
            return

        # Csúszó munkamenet: használat közben (naponta legfeljebb egyszer) friss, 30 napos süti.
        # A kilépés maga törli a sütit — ott nem újítunk.
        if (lejar is not None and ut != "/app/kilepes"
                and lejar - ui_kabin._most() < ui_kabin.SUTI_ERVENY - MEGUJITAS_S):
            fejlec = _megujito_fejlec()

            async def kuld(uzenet):
                if uzenet["type"] == "http.response.start":
                    meglevo = list(uzenet.get("headers", []))
                    if not any(k.lower() == b"set-cookie" and ui_kabin.SUTI_NEV.encode() in v for k, v in meglevo):
                        uzenet = dict(uzenet, headers=meglevo + [fejlec])
                await send(uzenet)

            await self.app(scope, receive, kuld)
            return

        await self.app(scope, receive, send)
