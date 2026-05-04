"""Echolot HTTP client + news_context helpers for the Bridge.

Echolot runs as a separate Railway instance exposing REST endpoints:
    GET /api/news?spheres=a,b,c&days=N&limit=N&language=&source_type=
    GET /api/search?query=&days=N&sphere=&category=&language=&limit=N
    GET /api/narrative_divergence?query=&days=N&per_sphere_limit=N
    GET /api/spheres

Caching policy:
    Echolot owns its cache (separate instance). The Bridge does NOT add a TTL
    cache — only short-window request coalescing so that concurrent identical
    calls share one upstream request.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("echolot_client")

ECHOLOT_URL = os.getenv("ECHOLOT_URL", "").rstrip("/")
TIMEOUT = float(os.getenv("ECHOLOT_TIMEOUT", "10"))
COALESCE_WINDOW = 5.0  # seconds — concurrent identical calls share one upstream

# In-flight request coalescing: key -> (started_ts, future)
_inflight: dict[str, tuple[float, asyncio.Future]] = {}
_inflight_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Presets (for ai_query/ai_task news_context="<preset>")
# ---------------------------------------------------------------------------
NEWS_PRESETS: dict[str, dict[str, Any]] = {
    "general": {
        "spheres": ["global_anchor", "regional_us", "regional_uk", "hu_press"],
        "days": 1,
        "limit": 25,
    },
    "economy": {
        "spheres": ["global_economy", "global_anchor", "hu_economy", "regional_us"],
        "days": 2,
        "limit": 30,
    },
    "tech": {
        "spheres": ["global_tech", "global_ai", "hu_tech"],
        "days": 2,
        "limit": 20,
    },
    "geopolitics": {
        "spheres": [
            "global_anchor", "global_analysis", "global_conflict",
            "regional_us", "regional_chinese", "regional_russian",
            "iran_regime", "israel_press_center", "ua_front_osint",
        ],
        "days": 3,
        "limit": 40,
    },
}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------
class EcholotError(Exception):
    pass


async def _get(path: str, params: dict[str, Any]) -> dict:
    """GET ECHOLOT_URL+path with params, with request-coalescing.

    Strips empty/None params. Retries once on transport error. Raises EcholotError
    on non-2xx or unparseable JSON.
    """
    if not ECHOLOT_URL:
        raise EcholotError("ECHOLOT_URL env var not set")

    clean = {k: v for k, v in params.items() if v not in ("", None, [])}
    # Lists become comma-joined for our REST surface.
    for k, v in list(clean.items()):
        if isinstance(v, list):
            clean[k] = ",".join(str(x) for x in v)

    key = f"{path}?" + "&".join(f"{k}={clean[k]}" for k in sorted(clean))
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
        url = f"{ECHOLOT_URL}{path}"
        last_err: Exception | None = None
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    resp = await client.get(url, params=clean)
                if resp.status_code >= 400:
                    raise EcholotError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                future.set_result(data)
                return data
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_err = e
                if attempt == 2:
                    break
                await asyncio.sleep(0.5)
        err = EcholotError(f"transport error: {last_err}")
        future.set_exception(err)
        raise err
    except EcholotError as e:
        if not future.done():
            future.set_exception(e)
        raise
    finally:
        async with _inflight_lock:
            cur = _inflight.get(key)
            if cur and cur[1] is future:
                _inflight.pop(key, None)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def fetch_news(
    spheres: list[str] | None = None,
    days: int = 7,
    limit: int = 30,
    language: str = "",
    source_type: str = "",
) -> dict:
    """GET /api/news (sphere-OR-filter, recency-ordered)."""
    return await _get("/api/news", {
        "spheres": spheres or [],
        "days": days,
        "limit": limit,
        "language": language,
        "source_type": source_type,
    })


async def search_news(
    query: str,
    days: int = 3,
    sphere: str = "",
    category: str = "",
    language: str = "",
    limit: int = 20,
) -> dict:
    """GET /api/search (FTS5 keyword search)."""
    return await _get("/api/search", {
        "query": query,
        "days": days,
        "sphere": sphere,
        "category": category,
        "language": language,
        "limit": limit,
    })


async def narrative_divergence(query: str, days: int = 3, per_sphere_limit: int = 5) -> dict:
    """GET /api/narrative_divergence (cross-sphere coverage of a topic)."""
    return await _get("/api/narrative_divergence", {
        "query": query,
        "days": days,
        "per_sphere_limit": per_sphere_limit,
    })


async def get_spheres() -> dict:
    """GET /api/spheres (sphere stats from last 7 days)."""
    return await _get("/api/spheres", {})


# ---------------------------------------------------------------------------
# news_context resolver — what ai_query/ai_task call before injecting prompts
# ---------------------------------------------------------------------------
async def resolve_news_context(spec: Any) -> tuple[list[dict], str]:
    """Resolve a news_context spec into (articles, label).

    spec accepts:
        - str preset name: "general" | "economy" | "tech" | "geopolitics"
        - dict with one of:
            {"preset": "<name>", **overrides}
            {"spheres": [...], "days": N, "limit": N, "language": .., "source_type": ..}
            {"query": "...", "days": N, "limit": N}                    # FTS search
            {"narrative": "<query>", "days": N, "per_sphere_limit": N} # divergence
            {"polymarket": {...}}                                       # Phase 2 placeholder

    Returns (articles, label) where label is a short tag for the prompt block.
    """
    if spec is None:
        return [], ""

    if isinstance(spec, str):
        spec = {"preset": spec}

    if not isinstance(spec, dict):
        raise EcholotError(f"news_context must be str or dict, got {type(spec).__name__}")

    if "polymarket" in spec:
        # Phase 2 — currently a placeholder. Allow caller to pass the key without breaking.
        logger.info("news_context.polymarket key set — Phase 2 not yet implemented, ignoring")

    # narrative_divergence path
    if "narrative" in spec:
        q = spec["narrative"]
        days = int(spec.get("days", 3))
        per_sphere = int(spec.get("per_sphere_limit", 5))
        data = await narrative_divergence(q, days=days, per_sphere_limit=per_sphere)
        articles: list[dict] = []
        for sphere, items in (data.get("by_sphere") or {}).items():
            for it in items:
                it = dict(it)
                it["sphere"] = sphere
                articles.append(it)
        return articles, f'narrative-divergence:"{q}"'

    # FTS search path
    if spec.get("query"):
        q = spec["query"]
        days = int(spec.get("days", 3))
        limit = int(spec.get("limit", 20))
        sphere = spec.get("sphere", "")
        data = await search_news(q, days=days, sphere=sphere, limit=limit)
        return list(data.get("articles") or []), f'search:"{q}"'

    # preset / spheres path
    if "preset" in spec:
        preset_name = spec["preset"]
        if preset_name not in NEWS_PRESETS:
            raise EcholotError(f"Unknown preset: {preset_name!r}. Valid: {list(NEWS_PRESETS)}")
        merged = dict(NEWS_PRESETS[preset_name])
        merged.update({k: v for k, v in spec.items() if k != "preset" and v not in ("", None)})
        spheres = merged.get("spheres") or []
        days = int(merged.get("days", 1))
        limit = int(merged.get("limit", 25))
        language = merged.get("language", "")
        source_type = merged.get("source_type", "")
        data = await fetch_news(spheres=spheres, days=days, limit=limit,
                                language=language, source_type=source_type)
        return list(data.get("articles") or []), f"preset:{preset_name}"

    spheres = spec.get("spheres") or []
    days = int(spec.get("days", 1))
    limit = int(spec.get("limit", 25))
    language = spec.get("language", "")
    source_type = spec.get("source_type", "")
    data = await fetch_news(spheres=spheres, days=days, limit=limit,
                            language=language, source_type=source_type)
    label = "spheres:" + ",".join(spheres) if spheres else "latest"
    return list(data.get("articles") or []), label


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------
MULTILANG_DIRECTIVE = (
    "\n\n--- FRISS HÍRKONTEXTUS HASZNÁLATI UTASÍTÁS ---\n"
    "Az alábbi friss híranyag többnyelvű (címek és lead-ek 8 nyelven, eredeti "
    "nyelvükön). Magyarul válaszolj, kivéve ha a felhasználó kifejezetten más "
    "nyelven kéri. Ha konkrét állításra hivatkozol a kontextusból, idézd a "
    "forrást [forrás_neve, ÉÉÉÉ-HH-NN] formátumban. Ha a kontextus nem fedi a "
    "kérdést, NE találj ki tényt — mondd ki hogy nincs róla friss adat.\n"
)


def format_news_block(articles: list[dict], label: str = "", group_by_sphere: bool = True) -> str:
    """Return a Markdown news block ready to append to a system_prompt."""
    if not articles:
        return f"\n\n--- FRISS HÍRKONTEXTUS ({label or 'üres'}) ---\n(Nincs friss cikk a kérésre.)\n"

    lines = [f"\n\n--- FRISS HÍRKONTEXTUS ({label or 'mixed'}) ---"]

    if group_by_sphere:
        groups: dict[str, list[dict]] = {}
        for a in articles:
            sph = a.get("sphere")
            if not sph:
                spheres_field = a.get("spheres_json") or a.get("spheres")
                if isinstance(spheres_field, str):
                    try:
                        import json as _json
                        spheres_field = _json.loads(spheres_field)
                    except Exception:
                        spheres_field = []
                sph = (spheres_field or ["?"])[0]
            groups.setdefault(sph, []).append(a)

        for sph, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"\n### sphere: {sph} ({len(items)} cikk)")
            for a in items:
                lines.append(_format_one(a))
    else:
        for a in articles:
            lines.append(_format_one(a))

    return "\n".join(lines)


def _format_one(a: dict) -> str:
    when = (a.get("published_at") or "")[:16].replace("T", " ")
    src = a.get("source_name") or a.get("source") or "?"
    title = (a.get("title") or "").strip()
    lead = (a.get("lead") or "").strip().replace("\n", " ")
    if len(lead) > 280:
        lead = lead[:277] + "..."
    url = a.get("url") or ""
    bullet = f"- [{when}] **{src}**: {title}"
    if lead:
        bullet += f" — {lead}"
    if url:
        bullet += f" [{url}]"
    return bullet
