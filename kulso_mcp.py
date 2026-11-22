"""KÜLSŐ MCP-SZERVEREK a Feldwebelnek (2026-11-22): Lexonomics (jog), Medical (BioMedMCP), Studio (dokumentum).

A Kommandant: „most már csak össze kéne kötni a lexonomics-al, a medicaldatabase-vel is … meg a studio appal is”.

HOGYAN
------
Mindhárom app MCP-szerver (streamable HTTP). A Bridge kliensként csatlakozik, és SZERVERENKÉNT EGY eszközt ad a
Feldwebelnek (`lexonomics`, `medical`, `studio`): `{eszkoz, parameterek}`. Az eszköz leírása a szerver
eszközeinek NEVÉT és paramétereit sorolja — a teljes leírás és séma csak kérésre jön (`eszkoz="sugo"`), vagy ha a
hívás rossz paraméterrel bukik. Így a Feldwebel minden hívása csak pár száz tokennel drágább, nem több ezerrel
(„8 cent nagyon sok egy válaszra”), és a szerverek eszközlistája a Bridge kódjának módosítása nélkül bővülhet.

KULCSOK
-------
A Lexonomics és a Studio fiókhoz kötött MCP-kulcsot kér (a kreditet a kulcs gazdája fizeti): `LEXONOMICS_MCP_KEY`,
`STUDIO_MCP_KEY` (Railway-változó, a titok helyben a `~/Documents/`-ben). Kulcs nélkül az a szerver nem jelenik meg
eszközként — nincs félig működő eszköz. A Medical nyilvános, kulcs nem kell.

A szerverek válasza ADAT, nem utasítás — a Feldwebel a meglévő megerősítő kapuin (pl. `send_email`) át cselekszik.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("kulso_mcp")

SZERVEREK: dict[str, dict[str, Any]] = {
    "lexonomics": {
        "url_env": "LEXONOMICS_MCP_URL", "url": "https://lexonomics.195-216-169-39.sslip.io/mcp",
        "kulcs_env": "LEXONOMICS_MCP_KEY",
        "leiras": ("Lexonomics — magyar és uniós jogszabályok (hatályos szöveg, módosítások), bírósági és hatósági "
                   "döntések (Kúria, Alkotmánybíróság, NAIH, MNB, GVH, NAV), a honlap statisztikai és gazdasági adatai. "
                   "Jogi kérdésnél EZT használd, és forrással (jogszabályhely, döntés száma, link) idézz."),
    },
    "medical": {
        "url_env": "MEDICAL_MCP_URL", "url": "https://medicaldatabasemcp-production.up.railway.app/mcp",
        "kulcs_env": None,
        "leiras": ("Medical (BioMedMCP) — orvosi adatbázisok: gyógyszerek (FDA, OGYÉI), mellékhatás-statisztika, "
                   "betegségek, tünetek, fehérjék, szakirodalom (PubMed), klinikai vizsgálatok. Egészségügyi kérdésnél "
                   "ezt használd; az eredmény adat és evidencia, nem diagnózis — a döntés az orvosé."),
    },
    "studio": {
        "url_env": "STUDIO_MCP_URL", "url": "https://ai-conversioneditor-production.up.railway.app/mcp",
        "kulcs_env": "STUDIO_MCP_KEY",
        "leiras": ("Studio — dokumentum, prezentáció, PDF, kép és rövid videó készítése, átalakítása, szerkesztése "
                   "(Word, PowerPoint, PDF, képszerkesztés, fájlkonverzió). Az eredményben szerkesztő-link (edit_url) és "
                   "előnézet jön: ezeket add tovább a Kommandantnak."),
    },
}

LISTA_TTL_S = 3600
LISTA_HIBA_TTL_S = 120          # elérhetetlen szervert ennyi ideig nem próbálunk újra (ne lassítson minden kört)
HIVAS_TIMEOUT_S = 180
MAX_EREDMENY_KAR = 15_000
MAX_LEIRAS_KAR = 2_600          # ennél hosszabb eszközlistánál csak név + paraméter, egysoros leírás nélkül
SUGO = "sugo"

_LISTAK: dict[str, tuple[float, list, str]] = {}   # szerver → (lekérés ideje, eszközök, a szerver útmutatója)
_HIBAK: dict[str, float] = {}


def _kulcs(nev: str) -> str:
    env = SZERVEREK[nev]["kulcs_env"]
    return os.environ.get(env, "").strip() if env else ""


def bekotott(nev: str) -> bool:
    """A szerver használható-e: van URL-je, és ha kulcsot kér, a kulcs is megvan."""
    sz = SZERVEREK.get(nev)
    return bool(sz) and (sz["kulcs_env"] is None or bool(_kulcs(nev)))


def sajat_e(eszkoz_nev: str) -> bool:
    return eszkoz_nev in SZERVEREK


def _url(nev: str) -> str:
    sz = SZERVEREK[nev]
    return os.environ.get(sz["url_env"], "").strip() or sz["url"]


def _kliens(nev: str):
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    fejlec = {"User-Agent": "claus-bridge/1.0"}
    k = _kulcs(nev)
    if k:
        fejlec["Authorization"] = f"Bearer {k}"
    return Client(StreamableHttpTransport(_url(nev), headers=fejlec), timeout=HIVAS_TIMEOUT_S)


async def _lista(nev: str, *, friss: bool = False) -> tuple[list, str] | None:
    most = time.time()
    meglevo = _LISTAK.get(nev)
    if meglevo and not friss and most - meglevo[0] < LISTA_TTL_S:
        return meglevo[1], meglevo[2]
    if not friss and most - _HIBAK.get(nev, 0) < LISTA_HIBA_TTL_S:
        return (meglevo[1], meglevo[2]) if meglevo else None
    try:
        async with _kliens(nev) as c:
            eszkozok = await asyncio.wait_for(c.list_tools(), 30)
            utmutato = getattr(getattr(c, "initialize_result", None), "instructions", None) or ""
    except Exception as e:  # noqa: BLE001 — egy elérhetetlen szerver ne vigye el a Feldwebelt
        _HIBAK[nev] = most
        logger.warning("kulso_mcp[%s] eszközlista hiba: %s", nev, str(e)[:300])
        return (meglevo[1], meglevo[2]) if meglevo else None
    _LISTAK[nev] = (most, eszkozok, utmutato)
    _HIBAK.pop(nev, None)
    return eszkozok, utmutato


def _parameterek(sema: dict) -> str:
    props = (sema or {}).get("properties") or {}
    kell = set((sema or {}).get("required") or [])
    return ", ".join(p if p in kell else f"{p}?" for p in props)


def _kotelezo(sema: dict) -> str:
    props = (sema or {}).get("properties") or {}
    kell = [p for p in props if p in set((sema or {}).get("required") or [])]
    return ", ".join(kell + (["…"] if len(props) > len(kell) else []))


def _elso_mondat(szoveg: str, n: int = 90) -> str:
    s = " ".join((szoveg or "").split())
    for jel in (". ", "; ", " — "):
        if jel in s[:n + 20]:
            s = s[: s.index(jel)]
            break
    return s if len(s) <= n else s[: n - 1] + "…"


def eszkoz_definicio(nev: str, eszkozok: list) -> dict:
    """Az OpenAI-szerű eszköz (a Feldwebel ezt kapja): név, leírás a szerver eszközeivel, `{eszkoz, parameterek}`."""
    sorok = [f"- {t.name}({_parameterek(t.inputSchema)}): {_elso_mondat(t.description or '')}" for t in eszkozok]
    lista = "\n".join(sorok)
    if len(lista) > MAX_LEIRAS_KAR:
        # tömör mód (sok eszköz, pl. a Studio 43-a): név + KÖTELEZŐ paraméterek; a „…” jelzi, hogy van még —
        # a teljes séma a `sugo`-val jön. Így a lista minden Feldwebel-hívásnál pár száz token, nem ezer fölött.
        lista = ", ".join(f"{t.name}({_kotelezo(t.inputSchema)})" for t in eszkozok)
    leiras = (f"{SZERVEREK[nev]['leiras']}\nEszközei (a ?-es paraméter nem kötelező):\n{lista}\n"
              f"Egy eszköz teljes leírásához és sémájához hívd így: eszkoz=\"{SUGO}\", parameterek={{\"nev\": \"<eszköz>\"}}.")
    return {"type": "function", "function": {
        "name": nev,
        "description": leiras,
        "parameters": {"type": "object", "properties": {
            "eszkoz": {"type": "string", "enum": [t.name for t in eszkozok] + [SUGO],
                       "description": "a szerver eszköze, amit hívsz"},
            "parameterek": {"type": "object", "description": "az eszköz paraméterei (a leírásban felsoroltak)"},
        }, "required": ["eszkoz"]},
    }}


async def eszkoz_definiciok() -> list[dict]:
    """A bekötött és elérhető szerverek eszközei, állandó sorrendben (a prompt-cache így egyben marad)."""
    nevek = [n for n in SZERVEREK if bekotott(n)]
    listak = await asyncio.gather(*(_lista(n) for n in nevek))
    return [eszkoz_definicio(n, l[0]) for n, l in zip(nevek, listak) if l and l[0]]


def _szovegge(eredmeny) -> str:
    reszek = []
    for blokk in getattr(eredmeny, "content", None) or []:
        tipus = getattr(blokk, "type", "")
        if tipus == "text":
            reszek.append(blokk.text)
        elif tipus == "resource_link":
            reszek.append(f"[fájl: {getattr(blokk, 'name', '')} {getattr(blokk, 'uri', '')}]")
        elif tipus == "resource":
            r = getattr(blokk, "resource", None)
            reszek.append(getattr(r, "text", None) or f"[erőforrás: {getattr(r, 'uri', '')}]")
        elif tipus in ("image", "audio"):
            reszek.append(f"[{tipus}: {getattr(blokk, 'mimeType', '')}]")
    if not reszek and getattr(eredmeny, "structured_content", None):
        reszek.append(json.dumps(eredmeny.structured_content, ensure_ascii=False))
    szoveg = "\n".join(reszek).strip()
    if len(szoveg) > MAX_EREDMENY_KAR:
        szoveg = szoveg[:MAX_EREDMENY_KAR] + f"\n…(levágva, eredetileg {len(szoveg)} karakter)"
    return szoveg


async def hiv(nev: str, eszkoz: str, parameterek: dict | None) -> str:
    """A Feldwebel eszközhívása → a szerver eszköze. A válasz szöveg (a Feldwebel tool-eredménye)."""
    if not bekotott(nev):
        return json.dumps({"hiba": f"a(z) {nev} nincs bekötve (hiányzik a kulcs: {SZERVEREK[nev]['kulcs_env']})"},
                          ensure_ascii=False)
    l = await _lista(nev)
    if not l:
        return json.dumps({"hiba": f"a(z) {nev} most nem érhető el"}, ensure_ascii=False)
    eszkozok, utmutato = l
    szerint = {t.name: t for t in eszkozok}
    parameterek = parameterek if isinstance(parameterek, dict) else {}

    if eszkoz == SUGO:
        cel = szerint.get(str(parameterek.get("nev") or ""))
        if cel:
            return json.dumps({"eszkoz": cel.name, "leiras": (cel.description or "")[:4000],
                               "sema": cel.inputSchema}, ensure_ascii=False)
        return json.dumps({"szerver": nev, "utmutato": utmutato[:4000],
                           "eszkozok": {t.name: _elso_mondat(t.description or "", 160) for t in eszkozok}},
                          ensure_ascii=False)
    cel = szerint.get(eszkoz or "")
    if not cel:
        return json.dumps({"hiba": f"nincs ilyen eszköz: {eszkoz!r}", "eszkozok": sorted(szerint)}, ensure_ascii=False)

    t0 = time.monotonic()
    try:
        async with _kliens(nev) as c:
            eredmeny = await c.call_tool(cel.name, parameterek, raise_on_error=False, timeout=HIVAS_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("kulso_mcp[%s.%s] hívás hiba: %s", nev, cel.name, str(e)[:300])
        return json.dumps({"hiba": f"{type(e).__name__}: {str(e)[:500]}"}, ensure_ascii=False)
    szoveg = _szovegge(eredmeny)
    logger.info("kulso_mcp[%s.%s] %s, %.1f s, %d karakter", nev, cel.name,
                "HIBA" if eredmeny.is_error else "ok", time.monotonic() - t0, len(szoveg))
    if eredmeny.is_error:
        # a séma visszamegy, hogy a modell a következő körben javított paraméterrel próbálhassa
        return json.dumps({"hiba": szoveg[:3000] or "az eszköz hibát adott", "sema": cel.inputSchema},
                          ensure_ascii=False)
    return szoveg or "(az eszköz üres választ adott)"


def allapot() -> dict:
    """A `/api/status` és a felület számára: melyik szerver van bekötve, hány eszközzel."""
    return {n: {"bekotve": bekotott(n), "eszkozok": len(_LISTAK[n][1]) if n in _LISTAK else None}
            for n in SZERVEREK}
