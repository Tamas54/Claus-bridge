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

async def _fetch_echolot_press_review(limit: int = 10, session: str = "reggel") -> dict:
    """A napi hirszemle — a KANONIKUS forrasbol.

    ⚠️ EGY GAZDA: a szemlet a `plugins.daily_press_review.structured_press_review`
    allitja elo, ugyanaz, amit a RAG-be tolto cron is hasznal. Ket kulon
    implementacio ket kulon igazsagot adna ugyanarrol a napro, es a
    duplikatumok azok, amik szetcsusznak.

    A mezoneveket a prompt-szerzodes miatt tartjuk meg (`top_stories`,
    `edition_date`, `edition_is_today`); a kanonikus fuggveny `home_*` neveit
    ide kepezzuk le.
    """
    try:
        from plugins.daily_press_review import structured_press_review
    except ImportError as e:  # pragma: no cover
        logger.error("a kanonikus szemle nem importalhato: %s", e)
        return {"edition_date": None, "edition_is_today": False, "top_stories": [],
                "world_edition_date": None, "world_stories": [], "fresh_today": [],
                "_error": f"a kanonikus szemle nem importalhato: {e}"}
    r = await structured_press_review(limit=limit)
    return {
        "session": session,
        "edition_date": r["home_date"],
        "edition_is_today": r["home_is_today"],
        "top_stories": r["home_stories"],
        "world_edition_date": r["world_date"],
        "world_stories": r["world_stories"],
        "fresh_today": r["fresh_today"],
        "_error": r["_error"],
    }


# ══ ADAT-UT PROBA ═══════════════════════════════════════════════════════
#
# MIERT SZULETETT (2026-08-30): a `daily_news_brief` prefetchere a
# `hirmagnet.hu/api/trending`-et hivta egy kulccsal, ami a produkcioban NINCS
# beallitva — tehat mar az ELSO soran `[]`-vel tert vissza. MINDIG. Az Echolot
# adatai SOHA nem voltak benne a briefben, es semmi nem jelezte: egy ures lista
# nem hiba, csak ures. Az onjavitas sem csaphatott le ra, mert nem TORTENT
# hiba — csak nem tortent adat.
#
# A tanulsag altalanos: AZ ADAT HIANYA NEM HIBA, AMIG VALAKI KI NEM MONDJA,
# HOGY OTT KELLENE LENNIE. Ezert deklaraljuk, mely szekcioknak KELL tartalmat
# adniuk, es a proba EZT meri — nem azt, hogy a fuggveny lefutott-e.

#: recipe -> {szekcio: emberi leiras}. Ami itt szerepel, annak URESEN HIBA.
_NEWS_SECTIONS = {
    "fx_ecb": "ECB napi devizaarfolyamok",
    "market_yahoo": "Yahoo Finance kvotok",
    "echolot_hirszemle": "Echolot hirszemle (klaszterezett sztorik)",
}
REQUIRED_SECTIONS = {
    "daily_news_brief": _NEWS_SECTIONS,
    "daily_news_brief_pm": _NEWS_SECTIONS,
}


async def probe_sections(recipe: str) -> dict:
    """{szekcio: (ok, reszlet)} — a recept adat-utjanak elesben merese."""
    required = REQUIRED_SECTIONS.get(recipe)
    if not required:
        return {}
    fn = globals().get(f"prefetch_{recipe}")
    if fn is None:
        return {"_prefetcher": (False, f"nincs prefetch_{recipe} fuggveny")}
    try:
        raw = await fn({})
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception as e:  # noqa: BLE001
        return {"_prefetcher": (False, f"{type(e).__name__}: {e}")}
    out = {}
    for key, label in required.items():
        val = payload.get(key)
        # A hirszemle DICT (lapszam + friss), nem lista — a hiba a `_error`
        # mezoben all, es "van adat" = van legalabb EGY sztori vagy friss cikk.
        # ⚠️ CSAK a hirszemle-ALAKRA. Az elso valtozat MINDEN dictet
        # hirszemlenek nezett, es ettol az `fx_ecb` (ECB-arfolyamok, szinten
        # dict) PIROSRA valtott "se lapszam, se friss cikk" uzenettel — a
        # proba a sajat tulzott altalanositasa miatt hazudott hibat.
        _is_review = isinstance(val, dict) and (
            "top_stories" in val or "fresh_today" in val or "_error" in val)
        if _is_review:
            if val.get("_error"):
                out[key] = (False, f"{label}: {val['_error']}")
            elif not (val.get("top_stories") or val.get("fresh_today")):
                out[key] = (False, f"{label}: URES — se lapszam, se friss cikk")
            elif not val.get("world_stories"):
                # Egy URES KULPOLITIKAI ROVAT NEM TERMEK. A hianyat ugyanugy
                # hibanak vesszuk, mint a magyaret — nem "reszleges sikernek".
                out[key] = (False, f"{label}: nincs VILAG-szemle "
                                   f"({len(val.get('top_stories') or [])} magyar sztori megvan)")
            else:
                out[key] = (True, f"{label}: {len(val.get('top_stories') or [])} magyar "
                                  f"({val.get('edition_date')}) + "
                                  f"{len(val.get('world_stories') or [])} vilag "
                                  f"({val.get('world_edition_date')}) + "
                                  f"{len(val.get('fresh_today') or [])} friss")
        elif isinstance(val, list) and val and isinstance(val[0], dict) and val[0].get("_error"):
            out[key] = (False, f"{label}: {val[0]['_error']}")
        elif not val:
            out[key] = (False, f"{label}: URES — a brief e nelkul keszulne el")
        else:
            n = len(val) if isinstance(val, (list, dict)) else 1
            out[key] = (True, f"{label}: {n} tetel")
    return out


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


async def _prefetch_news_brief(session: str) -> str:
    """A hirszemle FACTUAL CONTEXT-je. EGY torzs a reggeli es a delutani briefnek.

    A ket brief ugyanazokat a szekciokat kapja; a `session` mezo mondja meg a
    modellnek, melyik keretezes ervenyes (a promptot a plugins/news_brief.py
    allitja elo ugyanabbol a forrasbol). Ket kulon prefetcher ket kulon
    igazsagot adna ugyanarrol a naprol.
    """
    ecb, market, news = await asyncio.gather(
        _fetch_ecb_rates(),
        _fetch_market_basket(),
        _fetch_echolot_press_review(limit=10, session=session),
    )
    return json.dumps({
        "session": session,
        "fx_ecb": ecb,
        "market_yahoo": market,
        "echolot_hirszemle": news,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=2)


async def prefetch_daily_news_brief(deps: dict) -> str:
    """Reggeli hirszemle (7:00): ECB + Yahoo + Echolot lapszam."""
    return await _prefetch_news_brief("reggel")


async def prefetch_daily_news_brief_pm(deps: dict) -> str:
    """Delutani hirszemle (16:00): ugyanaz a torzs, delutani keretezessel."""
    return await _prefetch_news_brief("delutan")


# ── Vertikum-prefetcherek (B integráció, 2026-05-10) ──────────────────

async def prefetch_weekly_macro_report(deps: dict) -> str:
    """weekly_macro_report (vertikum: makro): hu_macro preset a StatData-ról."""
    try:
        import _statdata_client as statdata
    except ImportError:
        logger.error("StatData client nem elerheto — hu_macro preset nem huzhatto")
        return ""
    if not statdata.STATDATA_URL:
        logger.warning("STATDATA_URL nincs beallitva — vertikum prefetch ures")
        return ""
    try:
        entries, label = await statdata.resolve_data_context({"presets": ["hu_macro"]})
        block = statdata.format_data_block(entries, label=label)
        return block
    except Exception as e:
        logger.error("hu_macro preset huzas hiba: %s: %s", type(e).__name__, e)
        return ""


async def prefetch_weekly_geopolitics_brief(deps: dict) -> str:
    """weekly_geopolitics_brief (vertikum: geopolitika): Echolot geopolitics preset."""
    try:
        import _echolot_client as echolot
    except ImportError:
        logger.error("Echolot client nem elerheto")
        return ""
    if not echolot.ECHOLOT_URL:
        logger.warning("ECHOLOT_URL nincs beallitva — vertikum prefetch ures")
        return ""
    try:
        articles, label = await echolot.resolve_news_context({"presets": ["geopolitics"]})
        block = echolot.format_news_block(articles, label=label, group_by_sphere=True)
        return block
    except Exception as e:
        logger.error("geopolitics preset huzas hiba: %s: %s", type(e).__name__, e)
        return ""


# ── Recipe nev → prefetcher mapping ───────────────────────────────────

RECIPE_PREFETCHERS = {
    "daily_briefing": prefetch_daily_briefing,
    "daily_news_brief": prefetch_daily_news_brief,
    "daily_news_brief_pm": prefetch_daily_news_brief_pm,
    "weekly_macro_report": prefetch_weekly_macro_report,
    "weekly_geopolitics_brief": prefetch_weekly_geopolitics_brief,
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
