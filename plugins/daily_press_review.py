"""Daily press review — fetch the Echolot daily brief and store it in the RAG.

A pure fetch+store cron job (NO LLM): it pulls the Echolot daily brief HTML
(https://.../brief?lang=hu — VILÁG + ITTHON), extracts clean text, and writes
two entries (world + domestic) into the unified agent RAG under a dedicated
virtual agent ``echolot`` with category ``news``.

Why a virtual agent: the RAG read-path is unified (every agent reads all
agents' entries with provenance tags), so storing news under ``echolot`` makes
the whole fleet — and later the synthetic poll personas — continuously
"news-aware", while the [ECHOLOT] source tag keeps it distinct from analyst
output. The corpus grows by one world + one domestic entry per day.

Wired into server.py `_cron_loop` as a name special-case (`daily_press_review`),
seeded in plugins/recipes.py. Cron schedule interpreted in Europe/Budapest.
"""
from __future__ import annotations

import html as _htmlmod
import logging
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

logger = logging.getLogger(__name__)

BRIEF_URL = os.getenv(
    "ECHOLOT_BRIEF_URL", "https://web-production-02611.up.railway.app/brief"
).strip().rstrip("/")
BRIEF_TIMEOUT = float(os.getenv("ECHOLOT_BRIEF_TIMEOUT", "25").strip() or "25")

_WS_RE = re.compile(r"\s+")
_TREND_BY_CLASS = {
    "trend-new": "new",
    "trend-rising": "rising",
    "trend-steady": "steady",
    "trend-fading": "fading",
}


def _clean(text: str) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", _htmlmod.unescape(text)).strip()


class _BriefParser(HTMLParser):
    """stdlib-only parser for the Echolot brief page (no bs4 dependency)."""

    _KIND_BY_CLASS = {
        "brief-headline": "headline",
        "brief-lead": "lead",
        "brief-topic-title": "topic_title",
        "brief-topic-summary": "topic_summary",
        "brief-outlook": "outlook",
        "brief-local-title": "local_title",
        "brief-meta": "meta",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._cap: list[list] = []
        self._h1_depth = 0
        self._h1_buf: list[str] = []
        self._in_h1 = False
        self.date = ""
        self.headline = ""
        self._events: list[tuple[str, object]] = []
        self._pending_title = None
        self._cur_trend = None

    @staticmethod
    def _classes(attrs) -> list[str]:
        for name, value in attrs:
            if name == "class" and value:
                return value.split()
        return []

    def handle_starttag(self, tag, attrs):
        classes = self._classes(attrs)
        if tag == "h1":
            self._in_h1 = True
            self._h1_depth = 1
            self._h1_buf = []
            return
        if self._in_h1:
            self._h1_depth += 1
        if "brief-trend" in classes:
            for c in classes:
                if c in _TREND_BY_CLASS:
                    self._cur_trend = _TREND_BY_CLASS[c]
                    break
        if self._cap:
            self._cap[-1][1] += 1
        for c in classes:
            kind = self._KIND_BY_CLASS.get(c)
            if kind:
                self._cap.append([kind, 1, []])
                break

    def handle_endtag(self, tag):
        if self._in_h1:
            self._h1_depth -= 1
            if self._h1_depth <= 0:
                self._in_h1 = False
                self.date = _clean("".join(self._h1_buf))
        if self._cap:
            ctx = self._cap[-1]
            ctx[1] -= 1
            if ctx[1] <= 0:
                kind, _depth, buf = self._cap.pop()
                self._flush(kind, _clean("".join(buf)))

    def handle_data(self, data):
        if self._in_h1:
            self._h1_buf.append(data)
        if self._cap:
            self._cap[-1][2].append(data)

    def _flush(self, kind, value):
        if kind == "headline":
            if not self.headline:
                self.headline = value
        elif kind in ("local_title", "lead", "outlook", "meta"):
            self._events.append((kind, value))
        elif kind == "topic_title":
            self._pending_title = value
        elif kind == "topic_summary":
            title = self._pending_title or ""
            trend = self._cur_trend  # trend span sits between title and summary
            self._pending_title = None
            self._cur_trend = None
            self._events.append(("topic", {"title": title, "summary": value, "trend": trend}))

    def build(self) -> dict:
        local_title = lead = local_lead = outlook = meta = ""
        topics: list[dict] = []
        local_topics: list[dict] = []
        seen_local = False
        for ev, payload in self._events:
            if ev == "local_title":
                local_title = payload
                seen_local = True
            elif ev == "lead":
                if seen_local:
                    local_lead = local_lead or payload
                else:
                    lead = payload if not lead else lead + "\n" + payload
            elif ev == "outlook":
                outlook = payload
            elif ev == "meta":
                meta = payload
            elif ev == "topic":
                (local_topics if seen_local else topics).append(payload)
        return {
            "date": self.date, "headline": self.headline, "lead": lead,
            "topics": topics, "local_title": local_title, "local_lead": local_lead,
            "local_topics": local_topics, "outlook": outlook, "meta": meta,
        }


def extract_brief(html: str) -> dict:
    """Parse the brief HTML into a structured dict. Never raises."""
    p = _BriefParser()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 — malformed markup must not crash the cron loop
        pass
    return p.build()


def _topic_md(t: dict) -> str:
    head = f"## {t.get('title', '')}"
    if t.get("trend"):
        head += f" ({t['trend']})"
    return f"{head}\n{t.get('summary', '')}".rstrip()


def _world_markdown(d: dict) -> str:
    parts = []
    if d.get("headline"):
        parts.append(d["headline"])
    if d.get("lead"):
        parts.append(d["lead"])
    parts.extend(_topic_md(t) for t in d.get("topics", []))
    if d.get("outlook"):
        parts.append("Kitekintő: " + d["outlook"] if not d["outlook"].lower().startswith("kitekint") else d["outlook"])
    return "\n\n".join(p for p in parts if p).strip()


def _domestic_markdown(d: dict) -> str:
    parts = []
    if d.get("local_title"):
        parts.append(d["local_title"])
    if d.get("local_lead"):
        parts.append(d["local_lead"])
    parts.extend(_topic_md(t) for t in d.get("local_topics", []))
    return "\n\n".join(p for p in parts if p).strip()


async def _fetch_brief_html(lang: str) -> str:
    url = f"{BRIEF_URL}?lang={lang}"
    last_err = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=BRIEF_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
            last_err = e
            logger.warning("press_review fetch attempt %d failed: %s", attempt + 1, e)
    raise RuntimeError(f"brief fetch failed: {last_err}")


def _already_stored(day_iso: str) -> bool:
    """True if today's world entry is already in the RAG (idempotent re-runs)."""
    from pyramid.memory_rag import _get_db
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM pyramid_agent_rag WHERE agent_id='echolot' "
            "AND category='news' AND task_title LIKE ? LIMIT 1",
            (f"%VILÁG%{day_iso}%",),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


async def fetch_and_store_press_review(lang: str = "hu") -> dict:
    """Fetch the Echolot daily brief and store world + domestic into the RAG.

    Returns a status dict: {ok, stored, skipped, date, world_chars, local_chars}.
    Safe to call repeatedly — dedups on the day's date.
    """
    from pyramid.memory_rag import add_to_agent_rag

    day_iso = datetime.now(timezone.utc).date().isoformat()
    try:
        if _already_stored(day_iso):
            logger.info("press_review %s already stored — skip", day_iso)
            return {"ok": True, "stored": 0, "skipped": True, "date": day_iso}

        html = await _fetch_brief_html(lang)
        d = extract_brief(html)
        world = _world_markdown(d)
        local = _domestic_markdown(d)

        if not world and not local:
            logger.error("press_review %s: empty parse, nothing stored", day_iso)
            return {"ok": False, "error": "empty parse", "date": day_iso}

        stored = 0
        if world:
            add_to_agent_rag(
                agent_id="echolot",
                content=f"Napi sajtószemle · VILÁG · {day_iso}\n\n{world}",
                task_title=f"Napi sajtószemle · VILÁG · {day_iso}",
                category="news",
            )
            stored += 1
        if local:
            add_to_agent_rag(
                agent_id="echolot",
                content=f"Napi sajtószemle · ITTHON · {day_iso}\n\n{local}",
                task_title=f"Napi sajtószemle · ITTHON · {day_iso}",
                category="news",
            )
            stored += 1

        logger.info("press_review %s: stored=%d world=%dc local=%dc",
                    day_iso, stored, len(world), len(local))
        return {"ok": True, "stored": stored, "skipped": False, "date": day_iso,
                "world_chars": len(world), "local_chars": len(local)}
    except Exception as e:  # noqa: BLE001
        logger.error("press_review failed for %s: %s", day_iso, e)
        return {"ok": False, "error": str(e), "date": day_iso}
