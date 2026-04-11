"""
Recipe pre-fetch helpers — lehuzza a valos adatokat MIELOTT a sub-agent futna.

A _execute_ai_task-ban futo sub-agentek (kimi/deepseek/glm5) CSAK web_search toolt
kapnak, tehat naptart, gmail-t, arfolyamokat nem tudnak hivni. Ehelyett itt,
Pythonban, elore lehuzzuk a valodi adatokat, es CONTEXT blokkent injektaljuk a
recipe promptba. Igy a sub-agent mar csak formaz — nem halucinal.

Ez egy HELPER modul, nem plugin. A filename underscore prefixe miatt a plugin
auto-discovery (__init__.py:41) kihagyja, tehat nem lesz duplan betoltve.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger("plugins.recipe_prefetch")


# ── ECB devizaarfolyamok ──────────────────────────────────────────────

async def _fetch_ecb_rates() -> dict:
    """ECB napi referenciaarfolyamok (EUR-bazisu) + HUF keresztarfolyamok."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
                headers={"User-Agent": "ClausBridge/1.0"},
            )
        root = ET.fromstring(resp.text)
        ns = {"ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
        date_str = ""
        for tc in root.findall(".//ecb:Cube[@time]", ns):
            date_str = tc.attrib["time"]

        rates = {}
        for cube in root.findall(".//ecb:Cube[@currency]", ns):
            rates[cube.attrib["currency"]] = float(cube.attrib["rate"])

        eur_huf = rates.get("HUF")
        out = {
            "source": "ECB daily reference rates",
            "date": date_str,
            "EUR/HUF": round(eur_huf, 2) if eur_huf else None,
        }
        if eur_huf:
            for ccy in ("USD", "CHF", "GBP", "CZK", "PLN"):
                if ccy in rates and rates[ccy]:
                    out[f"{ccy}/HUF"] = round(eur_huf / rates[ccy], 2)
        return out
    except Exception as e:
        logger.error("ECB fetch failed: %s: %s", type(e).__name__, e)
        return {"error": f"ECB: {type(e).__name__}: {e}"}


# ── Yahoo Finance kvotok ──────────────────────────────────────────────

async def _fetch_yahoo_quote(symbol: str, label: str = "") -> dict:
    """Egy Yahoo Finance kvot a chart API-bol."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(resp.text)
        result_data = data.get("chart", {}).get("result", [])
        if not result_data:
            err = data.get("chart", {}).get("error", {})
            return {"symbol": symbol, "label": label, "error": err.get("description", "no data")}
        meta = result_data[0].get("meta", {})
        price = meta.get("regularMarketPrice", 0)
        prev_close = meta.get("previousClose") or meta.get("chartPreviousClose", 0)
        change = round(price - prev_close, 4) if prev_close else 0
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0
        return {
            "symbol": symbol,
            "label": label or meta.get("shortName", symbol),
            "price": price,
            "change": change,
            "change_pct": change_pct,
            "currency": meta.get("currency", ""),
            "source": "Yahoo Finance",
        }
    except Exception as e:
        logger.error("Yahoo fetch failed for %s: %s: %s", symbol, type(e).__name__, e)
        return {"symbol": symbol, "label": label, "error": f"{type(e).__name__}: {e}"}


async def _fetch_market_basket() -> list:
    """Napi piaci kosar: arany, brent, wti, EURHUF, USDHUF, BUX, Bitcoin."""
    symbols = [
        ("GC=F",      "Gold spot (USD/oz)"),
        ("BZ=F",      "Brent crude (USD/bbl)"),
        ("CL=F",      "WTI crude (USD/bbl)"),
        ("EURHUF=X",  "EUR/HUF"),
        ("USDHUF=X",  "USD/HUF"),
        ("^BUX.BD",   "BUX index"),
        ("BTC-USD",   "Bitcoin (USD)"),
    ]
    fetched = await asyncio.gather(
        *[_fetch_yahoo_quote(sym, label) for sym, label in symbols],
        return_exceptions=True,
    )
    results = []
    for item in fetched:
        if isinstance(item, Exception):
            results.append({"error": f"{type(item).__name__}: {item}"})
        else:
            results.append(item)
    return results


# ── Google Calendar (szinkron, mert a google-api-python-client sync) ──

def _fetch_calendar_today(calendar_service) -> list:
    """Mai nap osszes esemenye budapesti napban."""
    if not calendar_service:
        return [{"error": "Calendar service not initialized"}]
    try:
        # UTC hatarok egy szelesebb ablakkal — biztosan lefedjuk a budapesti napot
        utc_now = datetime.now(timezone.utc)
        start = (utc_now - timedelta(hours=12)).replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=36)

        result = calendar_service.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=30
        ).execute()

        # Budapest-i napra szukites
        try:
            from zoneinfo import ZoneInfo
            bp_tz = ZoneInfo("Europe/Budapest")
        except ImportError:
            bp_tz = timezone(timedelta(hours=2))  # CEST fallback
        today_bp = datetime.now(bp_tz).date()

        events = []
        for item in result.get("items", []):
            start_info = item.get("start", {})
            dt_str = start_info.get("dateTime", start_info.get("date", ""))
            if not dt_str:
                continue
            # Szures a mai napra
            if "T" in dt_str:
                try:
                    ev_dt = datetime.fromisoformat(dt_str).astimezone(bp_tz)
                except Exception:
                    continue
                if ev_dt.date() != today_bp:
                    continue
                time_str = ev_dt.strftime("%H:%M")
            else:
                # all-day
                try:
                    if datetime.fromisoformat(dt_str).date() != today_bp:
                        continue
                except Exception:
                    continue
                time_str = "egesz nap"

            events.append({
                "time": time_str,
                "summary": item.get("summary", "(no title)"),
                "location": item.get("location", ""),
            })
        return events
    except Exception as e:
        logger.error("Calendar fetch failed: %s: %s", type(e).__name__, e)
        return [{"error": f"{type(e).__name__}: {e}"}]


# ── Gmail unread ──────────────────────────────────────────────────────

def _fetch_gmail_unread(gmail_service, limit: int = 10) -> list:
    """Utolso N olvasatlan email."""
    if not gmail_service:
        return [{"error": "Gmail service not initialized"}]
    try:
        result = gmail_service.users().messages().list(
            userId="me", q="is:unread in:inbox", maxResults=limit
        ).execute()
        msgs = []
        for m in result.get("messages", []):
            detail = gmail_service.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            msgs.append({
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
                "snippet": (detail.get("snippet", "") or "")[:140],
            })
        return msgs
    except Exception as e:
        logger.error("Gmail fetch failed: %s: %s", type(e).__name__, e)
        return [{"error": f"{type(e).__name__}: {e}"}]


# ── Bridge DB — nyitott feladatok ─────────────────────────────────────

def _fetch_open_tasks(get_db, limit: int = 10) -> list:
    """Nyitott taskok a Bridge DB-bol."""
    if not get_db:
        return []
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT id, title, status, priority FROM tasks "
            "WHERE status IN ('pending', 'in_progress') "
            "ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [{"id": r["id"], "title": r["title"], "status": r["status"],
                 "priority": r["priority"]} for r in rows]
    except Exception as e:
        logger.error("Tasks fetch failed: %s: %s", type(e).__name__, e)
        return [{"error": f"{type(e).__name__}: {e}"}]


# ── Hirmagnet friss hirek (opcionalis, csak szoveg, NEM adat) ─────────

async def _fetch_hirmagnet_trending(limit: int = 8) -> list:
    """Trending magyar hirek — ha elerheto a Hirmagnet API kulcs."""
    import os
    api_key = os.environ.get("HIRMAGNET_API_KEY", "")
    if not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://hirmagnet.hu/api/trending?limit={limit}",
                headers={"X-API-Key": api_key, "User-Agent": "ClausBridge/1.0"},
            )
        if resp.status_code != 200:
            return []
        data = json.loads(resp.text)
        items = data.get("articles", []) or data.get("items", []) or []
        return [{"title": a.get("title", ""), "source": a.get("source", ""),
                 "url": a.get("url", "")} for a in items[:limit]]
    except Exception as e:
        logger.debug("Hirmagnet fetch skipped: %s", e)
        return []


# ── Fo prefetch funkciok recipe-kent ──────────────────────────────────

async def prefetch_daily_briefing(deps: dict) -> str:
    """daily_briefing: naptar + gmail + taskok."""
    capture_state = deps.get("capture_state") or {}
    calendar_service = capture_state.get("calendar_service")
    gmail_service = capture_state.get("gmail_service")
    get_db = deps.get("get_db")

    # Sync hivasok executorban, hogy ne blokkolja az event loopot
    loop = asyncio.get_event_loop()
    calendar, gmail, tasks = await asyncio.gather(
        loop.run_in_executor(None, _fetch_calendar_today, calendar_service),
        loop.run_in_executor(None, _fetch_gmail_unread, gmail_service, 10),
        loop.run_in_executor(None, _fetch_open_tasks, get_db, 10),
    )

    return json.dumps({
        "calendar_today": calendar,
        "gmail_unread": gmail,
        "open_tasks": tasks,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=2)


async def prefetch_daily_news_brief(deps: dict) -> str:
    """daily_news_brief: ECB arfolyamok + Yahoo piaci kosar + Hirmagnet hirek."""
    ecb, market, news = await asyncio.gather(
        _fetch_ecb_rates(),
        _fetch_market_basket(),
        _fetch_hirmagnet_trending(limit=8),
    )
    return json.dumps({
        "fx_ecb": ecb,
        "market_yahoo": market,
        "hirmagnet_news": news,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=2)


# ── Recipe nev → prefetcher mapping ───────────────────────────────────

RECIPE_PREFETCHERS = {
    "daily_briefing": prefetch_daily_briefing,
    "daily_news_brief": prefetch_daily_news_brief,
}


async def run_prefetch(recipe_name: str, deps: dict) -> str | None:
    """Futtatja a recipe-hez tartozo prefetchert, ha van. None ha nincs."""
    fn = RECIPE_PREFETCHERS.get(recipe_name)
    if not fn:
        return None
    try:
        return await fn(deps)
    except Exception as e:
        logger.error("Recipe prefetch failed for %s: %s: %s",
                     recipe_name, type(e).__name__, e)
        return json.dumps({"error": f"prefetch failed: {type(e).__name__}: {e}"})
