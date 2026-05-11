"""StatData HTTP client + data_context helpers for the Bridge.

StatData runs as a separate Railway instance. It exposes a generic REST
dispatch endpoint that mirrors its 14 @mcp.tool() functions:

    POST /api/call
    Body: {"tool": "<name>", "args": {...}}
    →    {"ok": true, "result": <str|json>}    on success
         {"ok": false, "error": "<msg>"}       on failure (4xx/5xx)

Caching policy:
    StatData owns its caches (recipe_book, KSH index, etc). The Bridge does
    NOT add a TTL cache — only short-window request coalescing so that
    concurrent identical calls share one upstream request.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("statdata_client")

STATDATA_URL = os.getenv("STATDATA_URL", "").strip().rstrip("/")
TIMEOUT = float(os.getenv("STATDATA_TIMEOUT", "60").strip() or "60")  # 2026-05-11: 30→60s
                                                                       # — statdata router ksh_flash brave-mcp scrape
                                                                       # 4-hónap visszamenős próbálkozás 15-25s közt
                                                                       # mozoghat; párhuzamos prefetch-nél a 30s
                                                                       # limit átléphető volt (#186 transport errors).
COALESCE_WINDOW = 5.0  # seconds — concurrent identical calls share one upstream

_inflight: dict[str, tuple[float, asyncio.Future]] = {}
_inflight_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Presets (for ai_query/ai_task data_context="<preset>")
# ---------------------------------------------------------------------------
DATA_PRESETS: dict[str, list[dict[str, Any]]] = {
    "hu_macro": [
        # ── ELSŐDLEGES: magas-szintű router (garantáltan friss, 2026-05-11 óta) ──
        # Egy hívás → egy szám + dátum + forrás (status: fresh|stale|missing).
        # A sub-agentnek NEM kell N alacsony-szintű hívást ottlépcsőznie.
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "cpi"}},
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "hicp"}},
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "core_cpi"}},
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "services_cpi"}},
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "policy_rate"}},
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "unemployment"}},
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "gdp"}},
        # HU GDP komponensek (negyedéves, Eurostat namq_10_gdp más na_item-kóddal):
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "gdp_consumption"}},
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "gdp_investment"}},
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "gdp_exports"}},
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "gdp_imports"}},
        # HU pénzpiac:
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "bond_yield_10y"}},
        {"tool": "get_macro_indicator", "args": {"country": "HU", "indicator": "ppi"}},
        # Eurozóna kontextus a HU-vetésért:
        {"tool": "get_macro_indicator", "args": {"country": "EA", "indicator": "cpi"}},
        {"tool": "get_macro_indicator", "args": {"country": "EA", "indicator": "core_cpi"}},
        {"tool": "get_macro_indicator", "args": {"country": "EA", "indicator": "services_cpi"}},
        {"tool": "get_macro_indicator", "args": {"country": "EA", "indicator": "energy_cpi"}},
        {"tool": "get_macro_indicator", "args": {"country": "EA", "indicator": "food_cpi"}},
        {"tool": "get_macro_indicator", "args": {"country": "EA", "indicator": "policy_rate"}},  # ECB DFR friss
        {"tool": "get_macro_indicator", "args": {"country": "EA", "indicator": "unemployment"}},
        # ── KÖZEGÉSZ: alacsony-szintű idősorok trend-elemzéshez ──
        # Ezek továbbra is kellenek, mert a router csak az utolsó értéket adja —
        # 12–24 havi idősor a trend és bázishatás-elemzéshez szükséges.
        {"tool": "get_ksh_stadat", "args": {"table_code": "ara0039", "max_rows": 36, "format": "long"}},
        {"tool": "get_ksh_stadat", "args": {"table_code": "ara0066", "max_rows": 24, "format": "long"}},
        {"tool": "get_ksh_stadat", "args": {"table_code": "ara0045", "max_rows": 36, "format": "long"}},
        {"tool": "get_ksh_stadat", "args": {"table_code": "mun0143", "max_rows": 24, "format": "long"}},
        # Eurostat HU+EA összevethető (Kommandant feedback 2026-05-10):
        # Megj.: az Eurostat HICP / une_rt_m 3-6 hét publikálási késedelemmel jön —
        # heti brief napján a folyó hónapra csak KSH-adat van.
        {"tool": "get_eurostat_data", "args": {"dataset_code": "prc_hicp_manr", "geo": "HU,EA", "sinceTimePeriod": "2025-01"}},  # HICP havi HU + eurózóna
        {"tool": "get_eurostat_data", "args": {"dataset_code": "namq_10_gdp", "geo": "HU,EA20",
                                                  "filters": "na_item=B1GQ&unit=CLV15_MEUR&s_adj=SCA",
                                                  "sinceTimePeriod": "2024-Q1"}},  # GDP negyedéves HU + EA20
        {"tool": "get_eurostat_data", "args": {"dataset_code": "une_rt_m", "geo": "HU,EU27_2020",
                                                  "filters": "sex=T&age=TOTAL&unit=PC_ACT&s_adj=SA",
                                                  "sinceTimePeriod": "2025-01"}},  # munkanélküli ráta HU + EU27 (EA20 ehhez a táblához nem érvényes geo-kód, lásd 2026-05-10 audit)
        {"tool": "get_eurostat_data", "args": {"dataset_code": "irt_st_m", "geo": "HU", "sinceTimePeriod": "2025-01"}},  # money market rate (~MNB rate proxy)
        # KSH ÉVES bontások (kontextusként):
        {"tool": "get_ksh_stadat", "args": {"table_code": "ara0002"}},  # CPI éves bontás kategóriánként (1.1.1.2)
        {"tool": "get_ksh_stadat", "args": {"table_code": "gdp0004"}},  # GDP éves nominál HUF/EUR/USD/PPP (1995-2024)
        # Árfolyam — napi MNB referencia + heti idősor (yfinance):
        {"tool": "mnb_rates", "args": {"mode": "current", "currencies": "EUR,USD"}},
        {"tool": "yfinance", "args": {"symbol": "EURHUF=X", "action": "history", "period": "3mo", "interval": "1wk"}},  # EUR/HUF heti idősor 3 hó
        # Jegybanki kamatok és pénzpiaci hozamok:
        # - Eurostat ei_mfir_m geo=EA: havi friss money market rate + 1/5/10y yields
        # - BIS HU+XM — gyakran stale, kontextusként; XM-en a get_policy_rates már
        #   automatikus ECB DFR overlay-jel jön, lásd statdata server.py 2026-05-11.
        {"tool": "get_eurostat_data", "args": {"dataset_code": "ei_mfir_m", "geo": "EA",
                                                  "sinceTimePeriod": "2025-09"}},  # eurozóna 3hó / 1y / 5y / 10y / Maastricht-hozam (havi)
        {"tool": "get_policy_rates", "args": {"countries": "HU,XM"}},  # HU BIS + XM (XM-re ECB DFR direct overlay)
        # ECB Data Portal direkt SDMX (2026-05-11): a havi szolgáltatás-infláció,
        # core HICP és HU HICP 2026-os adat amit a KSH STADAT és az Eurostat
        # csonkolt feed-jei nem adnak ki. ECB ICP STS_INSTITUTION=4 (Eurostat-
        # sourced) sorozatok, REF_AREA=HU.
        {"tool": "get_ecb_data", "args": {"dataset": "ICP", "key": "M.HU.N.000000.4.ANR", "last_n": 12}},  # HU HICP overall havi YoY% (frissebb mint az Eurostat csonkolt)
        {"tool": "get_ecb_data", "args": {"dataset": "ICP", "key": "M.HU.N.SERV00.4.ANR", "last_n": 12}},  # HU HICP services havi YoY% — egyetlen havi forrás
        {"tool": "get_ecb_data", "args": {"dataset": "ICP", "key": "M.HU.N.XEF000.4.ANR", "last_n": 12}},  # HU core HICP (excl. energy & food) havi YoY%
        {"tool": "get_ecb_data", "args": {"dataset": "ICP", "key": "M.U2.N.000000.4.ANR", "last_n": 6}},   # Euro area HICP overall havi (flash + final)
        {"tool": "get_ecb_data", "args": {"dataset": "ICP", "key": "M.U2.N.XEF000.4.ANR", "last_n": 6}},   # Euro area core HICP havi
        {"tool": "get_ecb_data", "args": {"dataset": "FM",  "key": "D.U2.EUR.4F.KR.DFR.LEV", "last_n": 5}}, # ECB Deposit Facility Rate napi (kanonikus EA policy rate)
        {"tool": "get_ecb_data", "args": {"dataset": "IRS", "key": "M.HU.L.L40.CI.0000.HUF.N.Z", "last_n": 12}},  # HU 10Y állampapír hozam (Maastricht), havi
        # Flash releases — a legfrissebb publikált HU/EA szám amíg a strukturált
        # idősorok még nem frissültek (KSH RSS retain ~5–20 item, Eurostat Atom 60):
        {"tool": "get_flash_releases", "args": {"query": "fogyasztói árak", "source": "ksh", "limit": 5}},     # HU CPI flash
        {"tool": "get_flash_releases", "args": {"query": "munkanélküliség",  "source": "ksh", "limit": 5}},   # HU munkanélküliség flash
        {"tool": "get_flash_releases", "args": {"query": "GDP",              "source": "ksh", "limit": 5}},   # HU GDP flash
        {"tool": "get_flash_releases", "args": {"query": "HICP inflation",   "source": "eurostat", "limit": 5}},  # EA HICP flash
        {"tool": "get_flash_releases", "args": {"query": "unemployment",     "source": "eurostat", "limit": 3}},  # EA unemployment flash
    ],
    "us_macro": [
        {"tool": "get_fred_data", "args": {"series_id": "GDP", "limit": 8}},
        {"tool": "get_fred_data", "args": {"series_id": "CPIAUCSL", "limit": 12}},
        {"tool": "get_fred_data", "args": {"series_id": "DGS10", "limit": 12}},
        {"tool": "get_fred_data", "args": {"series_id": "UNRATE", "limit": 12}},
        {"tool": "get_policy_rates", "args": {"countries": "US"}},
    ],
    "eu_macro": [
        {"tool": "get_eurostat_data", "args": {"dataset_code": "prc_hicp_manr", "geo": "EA"}},
        {"tool": "get_policy_rates", "args": {"countries": "XM"}},           # Eurozone
        {"tool": "yfinance", "args": {"symbol": "EURUSD=X", "action": "quote"}},
    ],
    "markets": [
        {"tool": "yfinance", "args": {"symbol": "^GSPC", "action": "quote"}},      # S&P 500
        {"tool": "yfinance", "args": {"symbol": "OTP.BD", "action": "quote"}},     # OTP Bank — HU piaci proxy (^BUX delistelt YHD-n)
        {"tool": "yfinance", "args": {"symbol": "EURHUF=X", "action": "quote"}},
        {"tool": "yfinance", "args": {"symbol": "CL=F", "action": "quote"}},       # WTI crude
        {"tool": "yfinance", "args": {"symbol": "^TNX", "action": "quote"}},       # 10Y treasury yield
        {"tool": "yfinance", "args": {"symbol": "GC=F", "action": "quote"}},       # Gold
    ],
    "tech_stocks": [
        {"tool": "yfinance", "args": {"symbol": s, "action": "quote"}}
        for s in ("MSFT", "NVDA", "GOOGL", "AAPL", "META", "AMZN", "^IXIC")
    ],
    "commodities": [
        {"tool": "yfinance", "args": {"symbol": s, "action": "quote"}}
        for s in ("CL=F", "BZ=F", "GC=F", "NG=F", "ZW=F", "HG=F")
    ],
    "fx_majors": [
        {"tool": "yfinance", "args": {"symbol": s, "action": "quote"}}
        for s in ("EURUSD=X", "USDJPY=X", "GBPUSD=X", "USDCHF=X", "EURHUF=X")
    ],
    "bonds": [
        {"tool": "yfinance", "args": {"symbol": "^TNX", "action": "quote"}},                  # US 10Y
        {"tool": "yfinance", "args": {"symbol": "^TYX", "action": "quote"}},                  # US 30Y
        {"tool": "get_fred_data", "args": {"series_id": "DGS2", "limit": 12}},                # US 2Y
        {"tool": "get_fred_data", "args": {"series_id": "IRLTLT01DEM156N", "limit": 12}},     # DE 10Y
    ],
    "inflation_focus": [
        {"tool": "get_fred_data", "args": {"series_id": "CPIAUCSL", "limit": 24}},
        {"tool": "get_eurostat_data", "args": {"dataset_code": "prc_hicp_manr", "geo": "EA", "sinceTimePeriod": "2025-01"}},
        {"tool": "get_eurostat_data", "args": {"dataset_code": "prc_hicp_manr", "geo": "HU", "sinceTimePeriod": "2025-01"}},  # HU HICP Eurostat-ról (KSH ara0002 mellé)
        {"tool": "get_ksh_stadat", "args": {"table_code": "ara0002"}},                         # KSH CPI (volt qse001)
        {"tool": "get_fred_data", "args": {"series_id": "T10YIE", "limit": 12}},               # US 10Y breakeven
    ],
    "emerging_markets": [
        {"tool": "yfinance", "args": {"symbol": s, "action": "quote"}}
        for s in ("EEM", "USDBRL=X", "USDINR=X", "USDCNY=X", "USDZAR=X", "USDTRY=X")
    ],
    "hu_markets": [
        # Magyar tőzsdei proxy — a Yahoo Finance-en a ^BUX index delistelt, és
        # RICHTER.BD/MTELEKOM.BD/MASTERPLAST.BD szintén halott mutual fund proxyk.
        # Ezek az ÉLŐ Budapest tickerek 2026-05-05 audit alapján:
        {"tool": "yfinance", "args": {"symbol": "OTP.BD", "action": "quote"}},     # OTP Bank — BUX legnagyobb komponens
        {"tool": "yfinance", "args": {"symbol": "MOL.BD", "action": "quote"}},     # MOL Magyar Olaj
        {"tool": "yfinance", "args": {"symbol": "MTEL.BD", "action": "quote"}},    # Magyar Telekom (NEM MTELEKOM.BD!)
        {"tool": "yfinance", "args": {"symbol": "4IG.BD", "action": "quote"}},     # 4iG
        {"tool": "yfinance", "args": {"symbol": "OPUS.BD", "action": "quote"}},    # Opus Global
        {"tool": "yfinance", "args": {"symbol": "EURHUF=X", "action": "quote"}},   # EUR/HUF FX
    ],
}

# Forecast add-ons appended when data_context = {"presets": [...], "forecast": True}.
# Use cautiously — forecast tool is slow (~10-30s per call, runs ML pipeline).
FORECAST_ADDONS: dict[str, list[dict[str, Any]]] = {
    "hu_macro": [
        {"tool": "forecast", "args": {"country": "HU", "indicator": "inflation", "year": 2027}},
        {"tool": "forecast", "args": {"country": "HU", "indicator": "gdp", "year": 2027}},
    ],
    "us_macro": [
        {"tool": "forecast", "args": {"country": "US", "indicator": "inflation", "year": 2027}},
        {"tool": "forecast", "args": {"country": "US", "indicator": "oecd_cli"}},
    ],
    "eu_macro": [
        {"tool": "forecast", "args": {"country": "DE", "indicator": "inflation", "year": 2027}},
    ],
    "inflation_focus": [
        {"tool": "forecast", "args": {"country": "HU", "indicator": "inflation", "year": 2027}},
        {"tool": "forecast", "args": {"country": "US", "indicator": "inflation", "year": 2027}},
    ],
}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------
class StatDataError(Exception):
    pass


async def _call(tool: str, args: dict[str, Any]) -> Any:
    """POST STATDATA_URL/api/call with {"tool", "args"}, with request-coalescing.

    Retries once on transport error. Raises StatDataError on non-ok response.
    """
    if not STATDATA_URL:
        raise StatDataError("STATDATA_URL env var not set")

    clean_args = {k: v for k, v in (args or {}).items() if v not in ("", None)}
    key = f"{tool}::" + _json.dumps(clean_args, sort_keys=True, default=str)
    now = time.monotonic()

    async with _inflight_lock:
        existing = _inflight.get(key)
        if existing and (now - existing[0]) < COALESCE_WINDOW:
            future = existing[1]
            owner = False
        else:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            _inflight[key] = (now, future)
            owner = True

    if not owner:
        return await future

    try:
        url = f"{STATDATA_URL}/api/call"
        payload = {"tool": tool, "args": clean_args}
        last_err: Exception | None = None
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    resp = await client.post(url, json=payload)
                if resp.status_code >= 400:
                    body = resp.text[:300]
                    raise StatDataError(f"HTTP {resp.status_code}: {body}")
                data = resp.json()
                if not data.get("ok"):
                    raise StatDataError(f"upstream error: {data.get('error', '<unknown>')}")
                future.set_result(data["result"])
                return data["result"]
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_err = e
                if attempt == 2:
                    break
                await asyncio.sleep(0.5)
        err = StatDataError(f"transport error: {last_err}")
        future.set_exception(err)
        raise err
    except StatDataError as e:
        if not future.done():
            future.set_exception(e)
        raise
    finally:
        async with _inflight_lock:
            cur = _inflight.get(key)
            if cur and cur[1] is future:
                _inflight.pop(key, None)


# ---------------------------------------------------------------------------
# Public API — one helper per StatData tool (kwargs forwarded as-is)
# ---------------------------------------------------------------------------
async def search_datasets(**kwargs) -> Any:
    return await _call("search_datasets", kwargs)


async def get_eurostat_data(**kwargs) -> Any:
    return await _call("get_eurostat_data", kwargs)


async def get_ksh_hvd(**kwargs) -> Any:
    return await _call("get_ksh_hvd", kwargs)


async def dbnomics_search(**kwargs) -> Any:
    return await _call("dbnomics_search", kwargs)


async def dbnomics_series(**kwargs) -> Any:
    return await _call("dbnomics_series", kwargs)


async def get_ksh_stadat(**kwargs) -> Any:
    return await _call("get_ksh_stadat", kwargs)


async def yfinance(**kwargs) -> Any:
    return await _call("yfinance", kwargs)


async def calculate(**kwargs) -> Any:
    return await _call("calculate", kwargs)


async def mnb_rates(**kwargs) -> Any:
    return await _call("mnb_rates", kwargs)


async def recipe_book(**kwargs) -> Any:
    return await _call("recipe_book", kwargs)


async def get_fred_data(**kwargs) -> Any:
    return await _call("get_fred_data", kwargs)


async def forecast(**kwargs) -> Any:
    return await _call("forecast", kwargs)


async def get_economic_calendar(**kwargs) -> Any:
    return await _call("get_economic_calendar", kwargs)


async def get_policy_rates(**kwargs) -> Any:
    return await _call("get_policy_rates", kwargs)


# ---------------------------------------------------------------------------
# data_context resolver — what ai_query/ai_task call before injecting prompts
# ---------------------------------------------------------------------------
async def resolve_data_context(spec: Any) -> tuple[list[dict], str]:
    """Resolve a data_context spec into (entries, label).

    spec accepts:
        - str preset name: "hu_macro" | "us_macro" | "eu_macro" | "markets" |
                           "tech_stocks" | "commodities" | "fx_majors" |
                           "bonds" | "inflation_focus" | "emerging_markets" |
                           "hu_markets"
        - dict:
            {"presets": ["hu_macro", "markets"]}
            {"series": [{"tool": "get_fred_data", "args": {...}}, ...]}
            {"presets": [...], "series": [...], "forecast": True}

    Returns (entries, label) where each entry is
        {"tool": str, "args": dict, "result": <result>}  on success
        {"tool": str, "args": dict, "error": str}        on failure
    """
    if spec is None:
        return [], ""

    if isinstance(spec, str):
        spec = {"presets": [spec]}

    if not isinstance(spec, dict):
        raise StatDataError(f"data_context must be str or dict, got {type(spec).__name__}")

    presets: list[str] = list(spec.get("presets") or [])
    if "preset" in spec and spec["preset"]:                  # tolerate singular
        presets.insert(0, spec["preset"])
    series: list[dict] = list(spec.get("series") or [])
    want_forecast = bool(spec.get("forecast", False))

    calls: list[dict] = []
    label_parts: list[str] = []

    for p in presets:
        if p not in DATA_PRESETS:
            raise StatDataError(f"Unknown preset: {p!r}. Valid: {list(DATA_PRESETS)}")
        for c in DATA_PRESETS[p]:
            calls.append({"tool": c["tool"], "args": dict(c.get("args") or {})})
        addons = FORECAST_ADDONS.get(p, []) if want_forecast else []
        for c in addons:
            calls.append({"tool": c["tool"], "args": dict(c.get("args") or {})})
        label_parts.append(p + ("+forecast" if addons else ""))

    for c in series:
        if not isinstance(c, dict) or "tool" not in c:
            raise StatDataError(f"series entry must be {{'tool': ..., 'args': ...}}, got {c!r}")
        calls.append({"tool": c["tool"], "args": dict(c.get("args") or {})})

    if series:
        label_parts.append(f"custom({len(series)})")

    # Execute all calls in parallel; coalescing handles dedup across concurrent calls.
    results = await asyncio.gather(
        *(_call(c["tool"], c["args"]) for c in calls),
        return_exceptions=True,
    )

    out: list[dict] = []
    for c, r in zip(calls, results):
        if isinstance(r, Exception):
            out.append({"tool": c["tool"], "args": c["args"], "error": str(r)})
        else:
            out.append({"tool": c["tool"], "args": c["args"], "result": r})

    label = "+".join(label_parts) if label_parts else "empty"
    return out, label


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------
DATA_DIRECTIVE = (
    "\n\n--- FRISS GAZDASÁGI ADATKONTEXTUS HASZNÁLATI UTASÍTÁS ---\n"
    "Az alábbi friss adatok a StatData MCP-ből származnak (Eurostat, KSH, "
    "DBnomics, MNB, FRED, BIS, Yahoo Finance). Magyarul válaszolj, kivéve ha "
    "a felhasználó kifejezetten más nyelven kéri.\n"
    "\n"
    "FORRÁS-PRIORITÁS — KÖTELEZŐ SORREND:\n"
    "1. **ELSŐSORBAN** a fenti 'FRISS GAZDASÁGI ADATKONTEXTUS' szekcióban "
    "kapott számokat használd. Hivatkozz rájuk `[StatData/<dataset>, "
    "ÉÉÉÉ-HH-NN]` formátumban — pl. `[StatData/Eurostat prc_hicp_manr, "
    "2025-06]` vagy `[StatData/MNB current, 2026-05-05]`. EZ A FORRÁSCÍMKE "
    "azonosítja, hogy a megadott kontextusból van — a felhasználó tudni "
    "fogja, hogy autoritatív, friss adat.\n"
    "2. Mielőtt `web_search` vagy más külső tool-t hívnál, **OLVASD VÉGIG "
    "A FENTI KONTEXTUST** és győződj meg róla, hogy a kért adat tényleg "
    "nincs benne. Ha benne van, NE hívj web-keresést — használd a "
    "kontextus-számot.\n"
    "3. CSAK akkor hívj `web_search`-öt vagy `web_fetch`-et, ha a kontextus "
    "tényleg NEM tartalmazza az adatot. Ekkor a forrást `[web/<oldal>, "
    "<dátum>]` formátumban jelöld — ELTÉRŐEN a `[StatData/...]`-tól. "
    "A kétféle hivatkozás **különböző megbízhatósági szint** — a "
    "felhasználó számára átláthatónak kell lennie.\n"
    "4. Ha a kontextusban van adat (pl. Eurostat HU HICP), DE a webről más "
    "szám jön (pl. TradingEconomics), MINDKETTŐT IDÉZD külön és magyarázd "
    "az eltérést — NE válaszd ki spontán az egyiket.\n"
    "\n"
    "STALE ADATOK:\n"
    "5. Ha az adat `[STALE]` flag-es a kontextusban (pl. BIS policy_rate "
    "2025-06, ~11 hónap stale), ELŐBB nézd meg a JSON-ban az "
    "`eurostat_proxy` mezőt — ha ott friss érték van (pl. Day-to-day "
    "money market rate), akkor azt használd 'a friss proxy' jelöléssel. "
    "Ha proxy sincs, jelöld az adatot `[STALE]`-ként és kommentáld, hogy "
    "a friss érték ismeretlen ezen a forráson.\n"
    "\n"
    "EGYÉB SZABÁLYOK:\n"
    "6. Ha az adatkontextus nem fedi a kérdést, NE találj ki tényt — "
    "mondd ki, hogy 'erre nincs friss adat a rendelkezésre álló "
    "forrásokban', vagy hívd a megfelelő `statdata_*` tool-t a hiányzó "
    "idősorra.\n"
    "7. A `forecast` tool-tól kapott számok ML-modellbecslések, NEM tények "
    "— jelöld őket explicit `[forecast]` címkével és 'becslés'/'előrejelzés' "
    "szavakkal.\n"
    "8. Ha a kontextusban hiba-üzenet van egy adott tool-ra (pl. "
    "`'error': 'HTTP 404'`), NE idézd, mintha az adat lenne — ismerd el, "
    "hogy a lekérés nem sikerült.\n"
)


def format_data_block(entries: list[dict], label: str = "") -> str:
    """Return a Markdown data block ready to append to a system_prompt."""
    if not entries:
        return f"\n\n--- FRISS GAZDASÁGI ADATKONTEXTUS ({label or 'üres'}) ---\n(Nincs friss adat a kérésre.)\n"

    lines = [f"\n\n--- FRISS GAZDASÁGI ADATKONTEXTUS ({label or 'mixed'}) ---"]

    groups: dict[str, list[dict]] = {}
    for e in entries:
        groups.setdefault(e["tool"], []).append(e)

    for tool, items in groups.items():
        lines.append(f"\n### {tool} ({len(items)} hívás)")
        for it in items:
            args_str = ", ".join(f"{k}={v}" for k, v in (it.get("args") or {}).items())
            if "error" in it:
                lines.append(f"- `{tool}({args_str})` HIBA: {it['error']}")
                continue
            result = it.get("result", "")
            if not isinstance(result, str):
                result = _json.dumps(result, ensure_ascii=False, default=str)
            # 2000→8000: the prior 2KB limit cut Eurostat HICP at month 6 of
            # 18, hiding 2026 frontier data from sub-agents. 8KB now fits a
            # full 18-month series with all dimensions. (2026-05-05 fix.)
            if len(result) > 8000:
                result = result[:8000] + f"\n…[csonkolva, összesen {len(result)} char]"
            lines.append(f"- `{tool}({args_str})`:\n{result}")

    return "\n".join(lines)
