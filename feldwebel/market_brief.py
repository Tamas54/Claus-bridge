"""Market brief generator — Bridge side of PLAN_20260531.md (§3, §4, §7 steps 1-2).

Builds a concise Strategic Brief for the NOFX trading bot (US equities focus),
twice a day (morning before US open + early afternoon). The brief is synthesized
by DeepSeek V4-Pro from fresh news (Echolot) + macro/market data (StatData), then
validated against the §3 JSON schema and POSTed to the NOFX brief endpoint
(http://localhost:8080/api/brief by convention; configured via NOFX_BRIEF_URL).

Data flow (PLAN §1): "push-once-and-stays" — the Bridge pushes the brief, NOFX
stores it and renders it into every 3-minute cycle prompt until overwritten.

Design notes
------------
- Synthesis engine is LOCKED to DeepSeek V4-Pro (PLAN §3/§6).
- The FULL day's earnings/macro calendar is pre-loaded into avoid[] + exit_flags[]
  with instrument-specific deadlines — NOT just held positions (PLAN §3 point 1).
- Strict JSON is not guaranteed by SiliconFlow models → schema validation + up to
  2 re-prompt retries (PLAN §2B/§3).
- If NOFX_BRIEF_URL is unset, the brief is written to a local file instead of
  failing (so morning runs never crash the cron loop on a half-configured deploy).

This module is intentionally dependency-light: the heavy lifting (ai_query, the
StatData/Echolot context fetch) lives in server.py. To avoid an import cycle, the
ai_query callable is injected via `set_ai_query()` at server startup (same pattern
as `_capture_state["_ai_query_func"]`).
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional

logger = logging.getLogger("feldwebel.market_brief")

# Injected at server startup (avoids server.py <-> module import cycle).
_ai_query_func: Optional[Callable] = None

# Injected at server startup: the server's _telegram_push coroutine. When wired,
# every successful brief is also sent to the Kommandant's Telegram in a
# human-readable form (besides the NOFX push / local file).
_telegram_push_func: Optional[Callable] = None

# StatData client is a top-level module in the bridge dir; import is safe (no cycle).
try:  # pragma: no cover - import guard mirrors server.py convention
    import _statdata_client as statdata_client
except Exception:  # noqa: BLE001
    statdata_client = None  # type: ignore


def set_ai_query(fn: Callable) -> None:
    """Register the server's ai_query coroutine so this module can call it."""
    global _ai_query_func
    _ai_query_func = fn


def set_telegram_push(fn: Callable) -> None:
    """Register the server's _telegram_push coroutine for brief delivery."""
    global _telegram_push_func
    _telegram_push_func = fn


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# NOFX brief endpoint. The real NOFX route is http://localhost:8080/api/brief
# (port 8080, path /api/brief — NOT a bare /brief). When NOFX_BRIEF_URL is unset
# the brief is written to a local file instead of POSTed (local-file fallback).
NOFX_BRIEF_URL = os.environ.get("NOFX_BRIEF_URL", "").strip()

# Optional shared secret. When NOFX_BRIEF_TOKEN is set and non-empty, the push
# sends it as the `X-Brief-Token` header; when unset, no extra header is sent.
# (Read at call time in _push_to_nofx so it honours env set after import.)

# Where to dump the brief locally when NOFX_BRIEF_URL is unset (or push fails).
# Mirrors the bridge convention of a /data volume on Railway, local cwd otherwise.
_BRIEF_DIR = pathlib.Path(os.environ.get("MARKET_BRIEF_DIR", "data/market_brief"))

# DeepSeek V4-Pro — locked synthesis engine (PLAN §3/§6).
_BRIEF_MODEL = "deepseek"

_VALID_SESSIONS = ("morning", "afternoon")

# How long a brief stays "live" before NOFX should treat it as stale and go
# conservative (PLAN §3: valid_until = until the next brief).
_VALID_UNTIL_HOURS = {"morning": 6, "afternoon": 8}


# ---------------------------------------------------------------------------
# §3 JSON schema — manual validator (no jsonschema dependency; keeps the
# requirements.txt pin surface small, matching the codebase's minimalism).
# ---------------------------------------------------------------------------
def validate_brief(obj: Any) -> list[str]:
    """Return a list of human-readable schema violations (empty == valid).

    Validates against PLAN §3 point 1. Used both for the retry loop and tests.
    """
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["brief must be a JSON object"]

    def _is_int(v: Any) -> bool:
        # The Go consumer types these as int and rejects non-integer floats with
        # HTTP 400 (silent data loss), so a float from the LLM must fail here.
        # NB: isinstance(True, int) is True in Python — exclude bools explicitly.
        return isinstance(v, int) and not isinstance(v, bool)

    def _req(key: str, types: tuple) -> Any:
        if key not in obj:
            errs.append(f"missing required field '{key}'")
            return None
        if not isinstance(obj[key], types):
            tnames = "/".join(t.__name__ for t in types)
            errs.append(f"field '{key}' must be {tnames}, got {type(obj[key]).__name__}")
            return None
        return obj[key]

    _req("asof", (str,))
    session = _req("session", (str,))
    if session is not None and session not in _VALID_SESSIONS:
        errs.append(f"field 'session' must be one of {_VALID_SESSIONS}, got {session!r}")
    _req("valid_until", (str,))
    _req("regime", (str,))
    if "risk_budget_pct" not in obj:
        errs.append("missing required field 'risk_budget_pct'")
    elif not _is_int(obj["risk_budget_pct"]):
        errs.append(
            "risk_budget_pct must be an integer (no decimals), got "
            f"{type(obj['risk_budget_pct']).__name__}"
        )
    elif not (0 <= obj["risk_budget_pct"] <= 100):
        errs.append("field 'risk_budget_pct' must be between 0 and 100")
    _req("bias", (dict,))
    _req("tradeable", (list,))
    _req("avoid", (list,))
    _req("events", (list,))
    _req("crowd", (str,))

    caps = _req("exposure_caps", (list,))
    if isinstance(caps, list):
        for i, c in enumerate(caps):
            if not isinstance(c, dict):
                errs.append(f"exposure_caps[{i}] must be an object")
                continue
            if not isinstance(c.get("cluster"), str):
                errs.append(f"exposure_caps[{i}].cluster must be a string")
            if not isinstance(c.get("members"), list):
                errs.append(f"exposure_caps[{i}].members must be a list")
            if not _is_int(c.get("max_units")):
                errs.append(
                    f"exposure_caps[{i}].max_units must be an integer (no decimals)"
                )

    flags = _req("exit_flags", (list,))
    if isinstance(flags, list):
        for i, f in enumerate(flags):
            if not isinstance(f, dict):
                errs.append(f"exit_flags[{i}] must be an object")
                continue
            if not isinstance(f.get("symbol"), str):
                errs.append(f"exit_flags[{i}].symbol must be a string")
            if not isinstance(f.get("reason"), str):
                errs.append(f"exit_flags[{i}].reason must be a string")
            if not isinstance(f.get("deadline"), str):
                errs.append(f"exit_flags[{i}].deadline must be a string")

    _req("note", (str,))
    return errs


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------
def _build_data_context() -> str:
    """data_context spec for ai_query: macro + market snapshot + VIX + calendar.

    Per the implementation map: us_macro + markets presets, plus ^VIX quote and
    the US economic calendar for today. policy_rates is already inside us_macro.
    """
    spec = {
        "presets": ["us_macro", "markets"],
        "series": [
            {"tool": "yfinance", "args": {"symbol": "^VIX", "action": "quote"}},
            {"tool": "get_economic_calendar", "args": {"days_ahead": 1, "region": "US"}},
        ],
    }
    return json.dumps(spec)


# News preset suited for market-moving US news (implementation map §2).
_NEWS_CONTEXT = "economy"


async def _fetch_calendar_raw() -> str:
    """Directly pull the full day's US economic calendar from StatData.

    Used to PRE-LOAD the avoid[]/exit_flags[] blocks (PLAN §3): the synthesis
    model must see the complete known earnings/macro schedule for today, not
    only events affecting currently-held positions.
    """
    if statdata_client is None or not getattr(statdata_client, "STATDATA_URL", ""):
        return ""
    try:
        result = await statdata_client.get_economic_calendar(days_ahead=1, region="US")
        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False)[:4000]
        return str(result)[:4000]
    except Exception as e:  # noqa: BLE001
        logger.warning("market_brief: economic calendar fetch failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
def _build_prompt(session: str, asof_iso: str, valid_until_iso: str, calendar_raw: str) -> str:
    schema_example = json.dumps(
        {
            "asof": asof_iso,
            "session": session,
            "valid_until": valid_until_iso,
            "regime": "risk_on | risk_off | neutral",
            "risk_budget_pct": 55,
            "bias": {"equity": "long_small", "gold": "long"},
            "tradeable": ["NVDA", "AAPL", "GOLD"],
            "avoid": ["NFP 2026-06-04 08:30 ET -> no equity entries +/-1h"],
            "events": ["US Consumer Sentiment today"],
            "crowd": "complacent | fearful | neutral",
            "exposure_caps": [
                {"cluster": "semis", "members": ["NVDA", "MU", "INTC"], "max_units": 1}
            ],
            "exit_flags": [
                {
                    "symbol": "NVDA",
                    "reason": "earnings tonight 16:20 ET -> flatten before close",
                    "deadline": "2026-05-31T20:00Z",
                }
            ],
            "note": "one short sentence",
            "telegram_digest": (
                "4-6 mondatos MAGYAR nyelvű helyzetértékelés a Kommandantnak: mi "
                "mozgatja ma a piacot, miért ez a rezsim és kockázati keret, mire "
                "kell figyelni napközben."
            ),
        },
        indent=2,
    )

    cal_block = ""
    if calendar_raw:
        cal_block = (
            "\n\n=== TODAY'S US ECONOMIC / EARNINGS CALENDAR (raw) ===\n"
            f"{calendar_raw}\n"
            "=== END CALENDAR ===\n"
            "Pre-load EVERY known event from this calendar into `avoid` (windowed, "
            "instrument-aware) AND, where an event clearly maps to a specific ticker "
            "(e.g. an earnings release), into `exit_flags` with a concrete `deadline` "
            "(ISO-8601 UTC). Cover the WHOLE day, not only events affecting open "
            "positions — a position opened at noon must already see its own deadline."
        )

    return (
        f"You are the strategic market-brief generator for an automated US-equities "
        f"trading bot. Produce the {session.upper()} brief.\n\n"
        "Use ONLY the freshly-injected NEWS and DATA blocks above (VIX, S&P 500, "
        "10Y, gold, WTI, policy rates, CPI/GDP/unemployment, economic calendar) plus "
        "the calendar block below. Do NOT invent numbers, levels, or events.\n\n"
        "Decide: market REGIME (risk_on/risk_off/neutral), a risk_budget_pct (0-100), "
        "directional BIAS per asset class, a small TRADEABLE universe (advisory tickers "
        "for the bot to prefer), AVOID windows (macro/earnings, with times), today's key "
        "EVENTS, the CROWD sentiment extreme, EXPOSURE_CAPS for correlated clusters "
        "(1 risk unit per cluster), and EXIT_FLAGS for instruments that must be flat by "
        "a deadline today."
        f"{cal_block}\n\n"
        f"`asof` MUST be \"{asof_iso}\" and `valid_until` MUST be \"{valid_until_iso}\" "
        f"and `session` MUST be \"{session}\".\n"
        "Keep `note` to ONE short sentence. Keep every field except `telegram_digest` "
        "tight (~200 tokens total for the machine fields).\n"
        "`telegram_digest` is the ONE exception: 4-6 full sentences IN HUNGARIAN, "
        "written for a human reader — explain what is driving the market today, why "
        "you chose this regime and risk budget, and what to watch intraday. Use the "
        "actual numbers from the data blocks.\n\n"
        "OUTPUT: a SINGLE JSON object, NOTHING else (no markdown fences, no prose "
        "before or after). It MUST conform exactly to this schema/shape:\n\n"
        f"{schema_example}"
    )


# ---------------------------------------------------------------------------
# JSON extraction from a model response
# ---------------------------------------------------------------------------
def _extract_response_text(ai_query_result: str) -> str:
    """ai_query returns a JSON envelope {model, response, ...}; pull `response`."""
    try:
        env = json.loads(ai_query_result)
    except (json.JSONDecodeError, TypeError):
        return ai_query_result or ""
    if isinstance(env, dict):
        if "error" in env:
            raise RuntimeError(f"ai_query error: {env['error']}")
        return env.get("response", "") or ""
    return str(env)


def _extract_json_object(text: str) -> Optional[dict]:
    """Best-effort extraction of the first balanced top-level JSON object."""
    if not text:
        return None
    # Strip common markdown fences.
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)
        t = t[1] if len(t) > 1 else text
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    start = t.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = t[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
    return None


# ---------------------------------------------------------------------------
# NOFX push
# ---------------------------------------------------------------------------
async def _push_to_nofx(brief: dict) -> dict:
    """POST the brief to NOFX_BRIEF_URL; on unset/failure, write to a local file.

    Returns a dict describing what happened (for logging / the tool response).
    """
    payload = json.dumps(brief, ensure_ascii=False)

    # Read at call time so env vars set after import (tests/hot-reload) are
    # honoured; falls back to the module-level constant that tests patch.
    url = os.environ.get("NOFX_BRIEF_URL", "").strip() or NOFX_BRIEF_URL

    if not url:
        path = _write_local(brief)
        logger.info("market_brief: NOFX_BRIEF_URL unset -> wrote brief to %s", path)
        return {"pushed": False, "local_file": str(path), "reason": "NOFX_BRIEF_URL unset"}

    headers = {"Content-Type": "application/json"}
    # Optional shared secret — read at call time so env set after import is honoured.
    token = os.environ.get("NOFX_BRIEF_TOKEN", "").strip()
    if token:
        headers["X-Brief-Token"] = token

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                content=payload,
                headers=headers,
            )
        ok = 200 <= resp.status_code < 300
        if ok:
            logger.info("market_brief: pushed to NOFX %s -> HTTP %d", url, resp.status_code)
            return {"pushed": True, "status_code": resp.status_code, "url": url}
        logger.warning("market_brief: NOFX push HTTP %d -> writing local fallback", resp.status_code)
        path = _write_local(brief)
        return {
            "pushed": False,
            "status_code": resp.status_code,
            "local_file": str(path),
            "reason": f"HTTP {resp.status_code}",
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("market_brief: NOFX push failed (%s) -> writing local fallback", e)
        path = _write_local(brief)
        return {"pushed": False, "local_file": str(path), "reason": str(e)}


def _write_local(brief: dict) -> pathlib.Path:
    """Write the brief to data/market_brief/<session>.json (latest, overwritten)."""
    try:
        _BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    session = brief.get("session", "unknown")
    path = _BRIEF_DIR / f"{session}.json"
    path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _archive_brief(brief: dict) -> Optional[pathlib.Path]:
    """Append-style archive of EVERY successful brief (future reuse / audit).

    Written regardless of push outcome to data/market_brief/archive/
    <YYYY-MM-DD_HHMM>_<session>.json. Never raises (best-effort).
    """
    try:
        archive_dir = _BRIEF_DIR / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        # asof is contract-forced (%Y-%m-%dT%H:%MZ) — derive the stamp from it
        # so the filename matches the brief's own timestamp.
        stamp = (brief.get("asof") or "").replace(":", "").replace("T", "_").rstrip("Z") or "unknown"
        session = brief.get("session", "unknown")
        path = archive_dir / f"{stamp}_{session}.json"
        path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except Exception as e:  # noqa: BLE001
        logger.warning("market_brief: archive write failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Telegram rendering
# ---------------------------------------------------------------------------
_REGIME_EMOJI = {"risk_on": "🟢", "risk_off": "🔴", "neutral": "🟡"}


def format_brief_telegram(brief: dict, digest: str = "") -> str:
    """Render the §3 brief JSON as a compact, human-readable Telegram HTML message.

    `digest` is the model's Hungarian situation assessment (human-facing only,
    stripped from the NOFX wire payload). Stays well under the 4000-char cap
    _telegram_push enforces.
    """
    session = brief.get("session", "?")
    regime = brief.get("regime", "?")
    emoji = _REGIME_EMOJI.get(regime, "⚪")
    lines = [
        f"{emoji} <b>NOFX Market Brief — {session}</b> ({brief.get('asof', '?')})",
        f"Regime: <b>{regime}</b> | Risk budget: <b>{brief.get('risk_budget_pct', '?')}%</b>"
        f" | Crowd: {brief.get('crowd', '?')}",
    ]
    if digest:
        lines.append("")
        lines.append(digest.strip()[:1500])
        lines.append("")
    bias = brief.get("bias") or {}
    if bias:
        lines.append("Bias: " + ", ".join(f"{k}={v}" for k, v in bias.items()))
    tradeable = brief.get("tradeable") or []
    if tradeable:
        lines.append("Tradeable: <code>" + ", ".join(map(str, tradeable)) + "</code>")
    avoid = brief.get("avoid") or []
    if avoid:
        lines.append("⛔ <b>Avoid:</b>")
        lines.extend(f"  • {a}" for a in avoid[:6])
    events = brief.get("events") or []
    if events:
        lines.append("📅 <b>Events:</b>")
        lines.extend(f"  • {e}" for e in events[:6])
    caps = brief.get("exposure_caps") or []
    if caps:
        cap_strs = [
            f"{c.get('cluster', '?')}({','.join(map(str, c.get('members', [])))})={c.get('max_units', '?')}u"
            for c in caps[:4]
        ]
        lines.append("Exposure caps: " + "; ".join(cap_strs))
    flags = brief.get("exit_flags") or []
    if flags:
        lines.append("🚪 <b>Exit flags:</b>")
        lines.extend(
            f"  • <b>{f.get('symbol', '?')}</b> — {f.get('reason', '?')} (by {f.get('deadline', '?')})"
            for f in flags[:6]
        )
    note = brief.get("note", "")
    if note:
        lines.append(f"<i>{note}</i>")
    lines.append(f"Valid until: {brief.get('valid_until', '?')}")
    return "\n".join(lines)


async def _send_telegram(brief: dict, digest: str = "") -> bool:
    """Best-effort Telegram delivery of the brief; never raises."""
    if _telegram_push_func is None:
        return False
    try:
        await _telegram_push_func(format_brief_telegram(brief, digest))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("market_brief: telegram push failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def generate_market_brief(session: str) -> dict:
    """Generate, validate, and push the strategic market brief for one session.

    Args:
        session: "morning" | "afternoon".

    Returns a result dict: {ok, session, brief?, push?, error?, attempts, size_chars}.
    Never raises for an LLM/validation/push failure — returns ok=False instead, so
    the cron loop stays alive.
    """
    if session not in _VALID_SESSIONS:
        return {"ok": False, "error": f"session must be one of {_VALID_SESSIONS}, got {session!r}"}

    if _ai_query_func is None:
        return {"ok": False, "session": session, "error": "ai_query not wired (set_ai_query not called)"}

    now_dt = datetime.now(timezone.utc)
    asof_iso = now_dt.strftime("%Y-%m-%dT%H:%MZ")
    valid_until = now_dt + timedelta(hours=_VALID_UNTIL_HOURS.get(session, 6))
    valid_until_iso = valid_until.strftime("%Y-%m-%dT%H:%MZ")

    calendar_raw = await _fetch_calendar_raw()
    data_context = _build_data_context()
    base_prompt = _build_prompt(session, asof_iso, valid_until_iso, calendar_raw)

    system_prompt = (
        "You are a disciplined market strategist. You output ONLY strict JSON that "
        "conforms exactly to the requested schema. No commentary, no markdown."
    )

    last_errs: list[str] = []
    last_text = ""
    attempts = 0
    max_attempts = 3  # initial + up to 2 retries (PLAN §3)

    for attempt in range(max_attempts):
        attempts = attempt + 1
        prompt = base_prompt
        if last_errs:
            prompt = (
                base_prompt
                + "\n\n=== YOUR PREVIOUS OUTPUT WAS INVALID ===\n"
                + "Schema validation errors:\n- "
                + "\n- ".join(last_errs)
                + "\nReturn a corrected SINGLE JSON object that fixes ALL of these. "
                + "Output JSON only."
            )

        try:
            raw_result = await _ai_query_func(
                model=_BRIEF_MODEL,
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.2,
                # DeepSeek V4-Pro runs with reasoning_effort=medium and its
                # reasoning tokens COUNT AGAINST max_tokens — 2000 was fully
                # consumed by thinking, yielding content="" (live smoke,
                # 2026-06-05). The brief itself stays ~200 tokens; the headroom
                # is purely for the reasoning budget.
                max_tokens=8000,
                caller="feldwebel",
                # news/data context only needed on the first attempt; retries are
                # pure JSON-repair on already-fetched facts already inside the model
                # response. Re-injecting wastes tokens and slows the retry.
                news_context=_NEWS_CONTEXT if attempt == 0 else "",
                data_context=data_context if attempt == 0 else "",
            )
        except Exception as e:  # noqa: BLE001
            last_errs = [f"ai_query call raised: {e}"]
            logger.warning("market_brief[%s] attempt %d: ai_query raised: %s", session, attempts, e)
            continue

        try:
            text = _extract_response_text(raw_result)
        except RuntimeError as e:
            last_errs = [str(e)]
            logger.warning("market_brief[%s] attempt %d: %s", session, attempts, e)
            continue

        last_text = text
        brief = _extract_json_object(text)
        if brief is None:
            last_errs = ["response did not contain a parseable JSON object"]
            logger.warning("market_brief[%s] attempt %d: no JSON object in response", session, attempts)
            continue

        # Force the contract fields the bot relies on (defensive — the model
        # sometimes drifts the timestamps despite the instruction).
        brief.setdefault("session", session)
        brief["session"] = session
        brief.setdefault("asof", asof_iso)
        brief.setdefault("valid_until", valid_until_iso)

        errs = validate_brief(brief)
        if errs:
            last_errs = errs
            logger.warning(
                "market_brief[%s] attempt %d: %d schema error(s): %s",
                session, attempts, len(errs), "; ".join(errs[:5]),
            )
            continue

        # Valid. The telegram_digest is human-facing ONLY: strip it from the
        # §3 payload before the NOFX push (keeps the wire contract pure and the
        # bot's prompt token budget unaffected), but archive it and render it
        # into the Telegram message.
        digest = str(brief.pop("telegram_digest", "") or "")
        size_chars = len(json.dumps(brief, ensure_ascii=False))
        # ~4 chars/token rough heuristic for a token-ish size in the log.
        approx_tokens = size_chars // 4
        push = await _push_to_nofx(brief)
        archive_path = _archive_brief({**brief, "telegram_digest": digest} if digest else brief)
        telegram_sent = await _send_telegram(brief, digest)
        logger.info(
            "market_brief[%s] SUCCESS in %d attempt(s): %d chars (~%d tok), pushed=%s, telegram=%s",
            session, attempts, size_chars, approx_tokens, push.get("pushed"), telegram_sent,
        )
        return {
            "ok": True,
            "session": session,
            "attempts": attempts,
            "size_chars": size_chars,
            "approx_tokens": approx_tokens,
            "brief": brief,
            "push": push,
            "archive": str(archive_path) if archive_path else None,
            "telegram": telegram_sent,
        }

    # All attempts exhausted.
    logger.error(
        "market_brief[%s] FAILED after %d attempts. Last errors: %s",
        session, attempts, "; ".join(last_errs[:5]),
    )
    return {
        "ok": False,
        "session": session,
        "attempts": attempts,
        "error": "schema validation failed after retries",
        "validation_errors": last_errs,
        "last_response_preview": (last_text or "")[:500],
    }
