# -*- coding: utf-8 -*-
"""Claus-Bridge → ECHOLOT ENGINE kliens (a Firecrawl-kompatibilis /v2 felület).

MIÉRT (2026-09-22): a motor külön Railway-service-ként él (saját kulccsal), és a
Bridge eddig CSAK a kulcsot tartotta a .env-ben — kód nem használta. Clausnak ezek
a képességek eddig hiányoztak: strukturált kinyerés sémával, kutató ügynök,
oldal-interakció (kattintás/űrlap), dokumentum-értelmezés, oldal-figyelés.

FEGYELEM
  * EGYETLEN MCP-tool (`echolot_engine`) `action` paraméterrel — a tool-szám a
    Bridge-en kritikus (feedback_mcp_tool_count_discipline).
  * A kulcs SOSEM kerül a válaszba/naplóba (a hibaüzenetet maszkoljuk).
  * A motor maga őrzi az SSRF-et, a kvótát és a napi LLM-keretet; itt csak
    időkorlát + méret-plafon van, hogy egy nagy lap ne fojtsa meg a chatet.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

ENGINE_URL = (os.environ.get("ECHOLOT_ENGINE_URL") or "").strip().rstrip("/")
ENGINE_KEY = (os.environ.get("ECHOLOT_ENGINE_KEY") or "").strip()
#: az /mcp-végpontot is elfogadjuk a .env-ből — a REST a gyökéren van
if ENGINE_URL.endswith("/mcp"):
    ENGINE_URL = ENGINE_URL[:-4]

TIMEOUT_S = float(os.environ.get("ECHOLOT_ENGINE_TIMEOUT_S", "120"))
MAX_CHARS = int(os.environ.get("ECHOLOT_ENGINE_MAX_CHARS", "20000"))

#: action → (HTTP-metódus, útvonal-sablon, kötelező mezők)
ACTIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "scrape":   ("POST", "/v2/scrape", ("url",)),
    "search":   ("POST", "/v2/search", ("query",)),
    "map":      ("POST", "/v2/map", ("url",)),
    "crawl":    ("POST", "/v2/crawl", ("url",)),
    "extract":  ("POST", "/v2/extract", ()),
    "agent":    ("POST", "/v2/agent", ("prompt",)),
    "parse":    ("POST", "/v2/parse", ()),
    "monitor":  ("POST", "/v2/monitor", ("targets",)),
    "interact": ("POST", "/v2/interact", ()),
    "status":   ("GET", "/v2/{kind}/{id}", ()),
}


def enabled() -> bool:
    return bool(ENGINE_URL and ENGINE_KEY)


def _maszk(s: str) -> str:
    return s.replace(ENGINE_KEY, "***") if ENGINE_KEY else s


def _vag(obj: Any, keret: int) -> Any:
    """A hosszú szövegmezőket vágjuk, hogy a tool-válasz ne fojtsa meg a modellt."""
    if isinstance(obj, str):
        return obj if len(obj) <= keret else obj[:keret] + f"\n…[+{len(obj) - keret} jel]"
    if isinstance(obj, list):
        return [_vag(x, keret) for x in obj[:50]]
    if isinstance(obj, dict):
        return {k: _vag(v, keret) for k, v in obj.items()}
    return obj


async def hivas(action: str, payload: dict | None = None, *, kind: str = "", job_id: str = "",
                max_chars: int | None = None) -> dict:
    """Egy motor-hívás. Visszatérés: a motor JSON-ja (vágva), vagy {"error": ...}."""
    if not enabled():
        return {"error": "engine_not_configured",
                "detail": "ECHOLOT_ENGINE_URL / ECHOLOT_ENGINE_KEY hiányzik a .env-ből"}
    if action not in ACTIONS:
        return {"error": "unknown_action", "detail": f"ismert: {', '.join(sorted(ACTIONS))}"}
    metodus, ut, kotelezo = ACTIONS[action]
    payload = dict(payload or {})
    hiany = [m for m in kotelezo if not payload.get(m)]
    if hiany:
        return {"error": "missing_field", "detail": f"a(z) {action} kéri: {', '.join(hiany)}"}
    if action == "status":
        if not (kind and job_id):
            return {"error": "missing_field", "detail": "a status kéri: kind + job_id"}
        ut = ut.format(kind=kind, id=job_id)
    fejlec = {"Authorization": f"Bearer {ENGINE_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False) as c:
            r = await c.request(metodus, ENGINE_URL + ut, headers=fejlec,
                                json=payload if metodus == "POST" else None)
        try:
            adat = r.json()
        except ValueError:
            return {"error": f"http_{r.status_code}", "detail": _maszk(r.text[:300])}
        if r.status_code >= 400 and isinstance(adat, dict):
            adat.setdefault("error", f"http_{r.status_code}")
        return _vag(adat, max_chars or MAX_CHARS)
    except httpx.TimeoutException:
        return {"error": "timeout", "detail": f"{TIMEOUT_S:.0f} s alatt nem válaszolt"}
    except Exception as e:  # noqa: BLE001 — a hívó chat NE haljon meg egy motorhibán
        return {"error": type(e).__name__, "detail": _maszk(str(e))[:300]}


_VAGAS_RE = re.compile(r"\n…\[\+(\d+) jel\]$")


def osszefoglal(action: str, adat: dict) -> str:
    """Rövid, ember- és modellbarát összegzés a nyers JSON elé (a teljes JSON is megy)."""
    if adat.get("error"):
        return f"❌ {action}: {adat.get('error')} — {str(adat.get('detail') or '')[:200]}"
    d = adat.get("data") if isinstance(adat.get("data"), dict) else {}
    if action == "scrape":
        m = d.get("metadata") or {}
        # 2026-09-23: a számláló csak a markdownt nézte — json/html/links kérésnél „0 jel"
        # állt a fejlécben hiánytalan adat mellett. Most formátumonként számol.
        reszek = []
        for k in ("markdown", "html", "rawHtml", "summary", "json", "links", "images",
                  "screenshot", "branding", "answer", "highlights"):
            v = d.get(k)
            if v in (None, "", [], {}):
                continue
            if isinstance(v, list):
                reszek.append(f"{k} {len(v)} db")
            elif isinstance(v, str):
                # a _vag utáni szöveg: a levágott rész a „…[+N jel]" jelben él — a VALÓDI hossz kell
                vagott = _VAGAS_RE.search(v)
                n = (len(v) - len(vagott.group(0)) + int(vagott.group(1))) if vagott else len(v)
                reszek.append(f"{k} {n} jel")
            else:
                reszek.append(f"{k} {len(json.dumps(v, ensure_ascii=False))} jel")
        blokk = m.get("blockReason")
        jel = "⛔" if blokk else "✅"
        return (f"{jel} scrape {m.get('sourceURL') or ''} · {m.get('statusCode')} · "
                f"{', '.join(reszek) or 'üres válasz'} · {m.get('proxyUsed') or 'basic'}"
                + (f" · BLOKK: {blokk} — ez NEM a kért tartalom" if blokk else ""))
    if action == "search":
        web = (adat.get("data") or {}).get("web") if isinstance(adat.get("data"), dict) else None
        return f"✅ search · {len(web or [])} találat"
    if action == "map":
        return f"✅ map · {len(adat.get('links') or [])} URL"
    if action in ("crawl", "extract", "agent") and adat.get("id"):
        return f"✅ {action} elindult · id={adat['id']} (status: action=\"status\", kind=\"{action}\")"
    return f"✅ {action}"
