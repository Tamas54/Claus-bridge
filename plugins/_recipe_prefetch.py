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

async def _fetch_echolot_press_review(limit: int = 10) -> dict:
    """HIRSZEMLE az Echolotbol — KLASZTEREZETT sztorik, forrasszammal.

    ⚠️ HAROM ROSSZ VALASZ, MIRE EZ MEGLETT (2026-08-30, Kommandant-lelet):
      1. `hirmagnet.hu/api/trending` egy NEM LETEZO API-kulccsal → mindig `[]`.
         Az Echolot adatai SOHA nem voltak benne a briefben.
      2. `get_trending` → KULCSSZAVAK ("peter" 16 forras, "trump" 14,
         "budapesten" 11). Sulyozott temalista, nem hirlista.
      3. `fetch_news(hu_press)` → valodi cimek, DE frissesseg szerint: Witcher 3
         gameplay-video es homorodalmasi utak keverve a belpolitikaval. Ez
         hirFOLYAM, nem hirSZEMLE.
    A szemle az, ami a napi LAPSZAMBAN van: klaszterezett sztorik aszerint
    rangsorolva, HANY FUGGETLEN FORRAS hozta. 13 sztori, source_count 6/6/6/4...

    ⚠️ ES A LAPSZAM ~21:55 UTC-kor FAGY. A 14:14-es cron tehat a TEGNAPIT kapja.
    Ezt NEM elhallgatni kell, hanem kimondani — az S-007 tanulsaga: a bot
    tegnapi lapszamot adott el "mai top sztorik" cimke alatt, iranyforditassal
    egyutt. Ezert ad ez a fuggveny KET blokkot: a lapszamot a SAJAT datumaval,
    es kulon a MAI friss teteleket a korpuszbol.
    """
    out = {"edition_date": None, "edition_is_today": False,
           "top_stories": [], "fresh_today": [], "_error": None}
    try:
        import _echolot_client as echolot_client
    except ImportError as e:
        out["_error"] = f"az Echolot-kliens nem importalhato: {e}"
        return out
    if not getattr(echolot_client, "ECHOLOT_URL", ""):
        out["_error"] = "ECHOLOT_URL nincs beallitva"
        return out

    from datetime import date as _date, timedelta as _td
    today = _date.today()
    for d_ in (today, today - _td(days=1)):
        try:
            raw = await echolot_client.mcp_call("get_daily_edition", {"date": d_.isoformat()})
        except Exception as e:  # noqa: BLE001
            logger.warning("Echolot lapszam (%s) hiba: %s", d_, e)
            continue
        try:
            env = json.loads(raw) if isinstance(raw, str) else (raw or {})
            payload = env.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            stories = (payload or {}).get("top_stories") or []
        except Exception as e:  # noqa: BLE001
            logger.warning("Echolot lapszam (%s) feldolgozasi hiba: %s", d_, e)
            continue
        if stories:
            out["edition_date"] = d_.isoformat()
            out["edition_is_today"] = (d_ == today)
            out["top_stories"] = [
                {"title": (st.get("title") or "").strip(),
                 "source_count": st.get("source_count"),
                 "story_id": st.get("story_id") or st.get("id") or "",
                 "url": st.get("url") or ""}
                for st in stories[:limit] if (st.get("title") or "").strip()
            ]
            break

    # A MAI friss tetelek KULON — a lapszam fagyott, a korpusz nem.
    try:
        data = await echolot_client.fetch_news(spheres=["hu_press"], days=1, limit=6)
        out["fresh_today"] = [
            {"title": (a.get("title") or "").strip(),
             "source": a.get("source") or "", "url": a.get("url") or ""}
            for a in (data.get("articles") or [])[:6] if (a.get("title") or "").strip()
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("Echolot friss lekeres hiba: %s", e)

    if not out["top_stories"] and not out["fresh_today"]:
        out["_error"] = "sem lapszam, sem friss cikk nem erkezett az Echolottol"
    return out


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
REQUIRED_SECTIONS = {
    "daily_news_brief": {
        "fx_ecb": "ECB napi devizaarfolyamok",
        "market_yahoo": "Yahoo Finance kvotok",
        "echolot_hirszemle": "Echolot hirszemle (klaszterezett sztorik)",
    },
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
            else:
                out[key] = (True, f"{label}: {len(val.get('top_stories') or [])} sztori "
                                  f"({val.get('edition_date')}) + "
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


async def prefetch_daily_news_brief(deps: dict) -> str:
    """daily_news_brief: ECB arfolyamok + Yahoo piaci kosar + Hirmagnet hirek."""
    ecb, market, news = await asyncio.gather(
        _fetch_ecb_rates(),
        _fetch_market_basket(),
        _fetch_echolot_press_review(limit=10),
    )
    return json.dumps({
        "fx_ecb": ecb,
        "market_yahoo": market,
        "echolot_hirszemle": news,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=2)


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
