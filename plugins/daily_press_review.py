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
import json
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


async def _fetch_brief_html(lang: str, date: str | None = None) -> str:
    url = f"{BRIEF_URL}?date={date}&lang={lang}" if date else f"{BRIEF_URL}?lang={lang}"
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


# ── press_snapshots: dated "information-environment" archive (separate store) ──
# One row per (date_iso, lang, signal_type) with a JSON blob. This is the forward
# press DB the planned Flash pollster reads for news-grounded personas; it lives
# OUTSIDE the agent RAG. brief = date-accurate (works for backfill); spheres/news
# are "now"-only signals → captured for the current day only.

def _ensure_snapshots_table() -> None:
    from pyramid.memory_rag import _get_db
    conn = _get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS press_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date_iso TEXT NOT NULL,
                lang TEXT NOT NULL DEFAULT 'hu',
                signal_type TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(date_iso, lang, signal_type)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_date ON press_snapshots(date_iso, lang)")
        conn.commit()
    finally:
        conn.close()


_ensure_snapshots_table()


def _snapshot_stored(day_iso: str, lang: str, signal_type: str) -> bool:
    from pyramid.memory_rag import _get_db
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM press_snapshots WHERE date_iso=? AND lang=? AND signal_type=? LIMIT 1",
            (day_iso, lang, signal_type),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _store_snapshot(day_iso: str, lang: str, signal_type: str, content_obj) -> int:
    """Idempotent upsert (INSERT OR IGNORE on the UNIQUE key). Returns 1 if new, else 0."""
    from pyramid.memory_rag import _get_db
    conn = _get_db()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO press_snapshots (date_iso, lang, signal_type, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (day_iso, lang, signal_type,
             json.dumps(content_obj, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


async def _capture_snapshot(day_iso: str, brief_html: str, lang: str = "hu", live: bool = False) -> int:
    """Store the daily info-environment snapshot. Returns # of new signal rows.

    brief: always (date-accurate). spheres + news: only when live=True (the
    Echolot JSON API has no date param → those reflect 'now', so capturing them
    for a past date would be wrong). trending/velocity/entities come in (I-b)
    once the Echolot REST wrappers exist.
    """
    n = 0
    try:
        d = extract_brief(brief_html)
        n += _store_snapshot(day_iso, lang, "brief", {
            "headline": d.get("headline", ""), "lead": d.get("lead", ""),
            "topics": d.get("topics", []), "local_title": d.get("local_title", ""),
            "local_lead": d.get("local_lead", ""), "local_topics": d.get("local_topics", []),
            "outlook": d.get("outlook", ""),
        })
    except Exception as e:  # noqa: BLE001
        logger.warning("snapshot brief %s failed: %s", day_iso, e)

    if live:
        import _echolot_client as ec

        async def _grab(sig, coro):
            nonlocal n
            try:
                n += _store_snapshot(day_iso, lang, sig, await coro)
            except Exception as e:  # noqa: BLE001
                logger.warning("snapshot %s %s failed: %s", sig, day_iso, e)

        await _grab("spheres", ec.get_spheres())
        await _grab("news", ec.fetch_news(days=1, limit=40, language=lang))
        await _grab("velocity", ec.get_velocity(window_hours=24, limit=30))
        await _grab("entities", ec.get_top_entities(days=3, limit=30))

        # trending — capture, then derive narrative divergence on the top keywords
        trending = None
        try:
            trending = await ec.get_trending(days=1, limit=15)
            n += _store_snapshot(day_iso, lang, "trending", trending)
        except Exception as e:  # noqa: BLE001
            logger.warning("snapshot trending %s failed: %s", day_iso, e)
        try:
            kws = [t.get("keyword") for t in (trending or {}).get("trending", [])[:3] if t.get("keyword")]
            nar = {}
            for kw in kws:
                raw = await ec.narrative_divergence(kw, days=2, per_sphere_limit=3)
                # trim to essentials — which spheres cover it, with headlines+lean
                # (the raw response carries full article objects → ~100KB/keyword)
                nar[kw] = {
                    sph: [{"title": (a.get("title") or "")[:160],
                           "source": a.get("source") or a.get("source_name") or "",
                           "lean": a.get("lean") or ""} for a in arts]
                    for sph, arts in (raw.get("by_sphere") or {}).items()
                }
            if nar:
                n += _store_snapshot(day_iso, lang, "narrative", nar)
        except Exception as e:  # noqa: BLE001
            logger.warning("snapshot narrative %s failed: %s", day_iso, e)
    return n


_DATE_RE = re.compile(r"[?&]date=(\d{4}-\d{2}-\d{2})")


def _available_past_dates(html: str) -> list[str]:
    """ISO dates the brief date-nav still exposes (older days Echolot serves)."""
    seen: list[str] = []
    for m in _DATE_RE.finditer(html):
        d = m.group(1)
        if d not in seen:
            seen.append(d)
    return seen


def _store_brief(day_iso: str, html: str) -> int:
    """Parse one brief HTML and store world + domestic under day_iso. Returns count."""
    from pyramid.memory_rag import add_to_agent_rag
    d = extract_brief(html)
    world = _world_markdown(d)
    local = _domestic_markdown(d)
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
    return stored


async def fetch_and_store_press_review(lang: str = "hu", backfill: bool = True) -> dict:
    """Fetch the Echolot daily brief(s) and store world + domestic into the RAG.

    By default also backfills every older date the brief date-nav still exposes
    (~3 days), so the corpus seeds immediately and not only from the next cron
    run. Idempotent: each date dedups, so re-runs and the daily cron only add
    what is missing. Returns {ok, stored, days, skipped}.
    """
    today_iso = datetime.now(timezone.utc).date().isoformat()
    result: dict = {"ok": True, "stored": 0, "snapshots": 0, "days": [], "skipped": []}
    try:
        current_html = await _fetch_brief_html(lang)

        dates = [today_iso]
        if backfill:
            dates += [d for d in _available_past_dates(current_html) if d != today_iso]

        for d in dates:
            is_today = (d == today_iso)
            html = current_html if is_today else None
            try:
                # RAG brief (news corpus) — date-deduped
                if _already_stored(d):
                    result["skipped"].append(d)
                else:
                    if html is None:
                        html = await _fetch_brief_html(lang, date=d)
                    n = _store_brief(d, html)
                    result["stored"] += n
                    result["days"].append({"date": d, "stored": n})
                # press_snapshots (dated archive) — independently deduped.
                # Gate on the LAST signal we'd add (today: trending — the rich
                # live set; past: brief). _capture_snapshot uses INSERT OR IGNORE,
                # so a partially-captured day gets its missing signals filled.
                gate_signal = "trending" if is_today else "brief"
                if not _snapshot_stored(d, lang, gate_signal):
                    if html is None:
                        html = await _fetch_brief_html(lang, date=d)
                    result["snapshots"] += await _capture_snapshot(d, html, lang, live=is_today)
            except Exception as e:  # noqa: BLE001
                logger.warning("press_review %s failed: %s", d, e)

        logger.info("press_review: stored=%d snapshots=%d days=%s skipped=%s",
                    result["stored"], result["snapshots"],
                    [x["date"] for x in result["days"]], result["skipped"])
        return result
    except Exception as e:  # noqa: BLE001
        logger.error("press_review failed: %s", e)
        return {"ok": False, "error": str(e)}
