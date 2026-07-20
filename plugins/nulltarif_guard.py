"""plugins/nulltarif_guard.py — NULLTARIF-ŐR (PYTHIA P1).

Napi ellenőrzés: a tencent/Hy3 (a P1 motor-default) LÉTEZIK-e még a
SiliconFlow-n és ára $0-e. A NULLTARIF-üzemmód gazdasági alapfeltevése a
$0-s Hy3 — ha az ár > 0 lesz vagy a modell eltűnik, AZONNALI riasztás kell:
  - Telegram-riasztás a Feldwebel-úton (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
    env, sendMessage — a server.py _telegram_push mintája; injektálható
    send_fn a tesztekhez),
  - hangos log.
FONTOS: az őr CSAK riaszt — modell-csere/fallback NEM történik (P1-doktrína:
motorhiba = hangos hiba, nem silent modell-váltás).

A cron-regisztráció ELŐKÉSZÍTVE (seed_cron + cron_entry) — a tényleges
cron-seed a P1-deploy lépése, itt nem fut le automatikusan.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("plugins.nulltarif_guard")

__plugin_meta__ = {
    "name": "nulltarif_guard",
    "version": "1.0.0",
    "description": "NULLTARIF-or — napi Hy3 letezes+ar ellenorzes, Telegram-riasztas a Feldwebel-uton",
}

_DEPS: dict | None = None

GUARD_MODEL = os.environ.get("NULLTARIF_GUARD_MODEL", "tencent/Hy3")
SF_MODELS_URL = os.environ.get(
    "NULLTARIF_MODELS_URL", "https://api.siliconflow.com/v1/models")

# Cron-előkészítés (a seed a deploykor fut, ld. seed_cron):
CRON_RECIPE_NAME = "nulltarif_guard_daily"
CRON_SCHEDULE = "20 6 * * *"   # napi 06:20 UTC — a heti nowcast (hétfő 07:30) előtt
CRON_DESC = "NULLTARIF-őr — napi Hy3 létezés+ár ellenőrzés, riasztás ha ár>0 vagy hiány"

# Ár-mezők, amelyeket a /models entry-ben defenzíven keresünk (a SiliconFlow
# válasz-séma nem garantál ár-mezőt — ha nincs, price_known=False).
_PRICE_KEYS = ("price", "input_price", "output_price", "prompt_price",
               "completion_price", "unit_price")


def _api_key(deps: dict | None = None) -> str:
    d = deps or _DEPS or {}
    return d.get("siliconflow_api_key") or os.environ.get("SILICONFLOW_API_KEY", "")


async def _default_fetch_models(deps: dict | None = None) -> list:
    """GET /v1/models — a teljes modell-lista (OpenAI-kompatibilis {data:[...]})."""
    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(SF_MODELS_URL,
                             headers={"Authorization": f"Bearer {_api_key(deps)}"})
        r.raise_for_status()
        payload = r.json()
    data = payload.get("data") if isinstance(payload, dict) else payload
    return list(data or [])


def _to_price(val) -> float | None:
    try:
        return float(str(val).lstrip("$").replace(",", "."))
    except (TypeError, ValueError):
        return None


def model_price(entry: dict) -> tuple[float | None, bool]:
    """(ár, price_known) egy /models entry-ből — defenzív mező-szimat.
    Több ár-mező esetén a LEGMAGASABB számít (bármelyik > 0 már riasztás).
    Beágyazott 'pricing' dict-et is átnézzük."""
    prices = []
    sources = [entry] + ([entry["pricing"]] if isinstance(entry.get("pricing"), dict) else [])
    for src in sources:
        for k in _PRICE_KEYS:
            if k in src:
                p = _to_price(src.get(k))
                if p is not None:
                    prices.append(p)
    if not prices:
        return None, False
    return max(prices), True


def evaluate(models: list, guard_model: str = "") -> dict:
    """Tiszta kiértékelés (teszt alatta): {exists, price, price_known, alert, reason}."""
    target = guard_model or GUARD_MODEL
    entry = next((m for m in models
                  if isinstance(m, dict) and m.get("id") == target), None)
    if entry is None:
        return {"model": target, "exists": False, "price": None,
                "price_known": False, "alert": True,
                "reason": f"a(z) {target} ELTŰNT a SiliconFlow /models listáról"}
    price, known = model_price(entry)
    if known and price > 0:
        return {"model": target, "exists": True, "price": price,
                "price_known": True, "alert": True,
                "reason": f"a(z) {target} ára már NEM $0 (talált ár: {price})"}
    return {"model": target, "exists": True, "price": price,
            "price_known": known, "alert": False,
            "reason": "OK — a modell él" + ("" if known else " (ár-mező nincs a listában — létezés-őrzés)")}


async def _default_send_telegram(text: str) -> bool:
    """A Feldwebel-út: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env, sendMessage
    (a server.py _telegram_push mintája)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.error("nulltarif riasztás: nincs TELEGRAM_BOT_TOKEN/CHAT_ID — a riasztás CSAK logban!")
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:4000],
                      "parse_mode": "HTML", "disable_web_page_preview": True})
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("nulltarif Telegram push failed: %s", e)
        return False


async def daily_check(deps: dict | None = None, send_fn=None, fetch_fn=None) -> dict:
    """A napi őr-futás: /models lekérés → evaluate → riasztás ha kell.
    fetch_fn/send_fn injektálható (teszt: mock). A lekérés hibája is HANGOS
    (alert=True, fetch_error) — a néma őr rosszabb, mint a téves riasztás."""
    ts = datetime.now(timezone.utc).isoformat()
    try:
        models = await (fetch_fn() if fetch_fn else _default_fetch_models(deps))
        report = evaluate(models)
    except Exception as e:  # noqa: BLE001
        report = {"model": GUARD_MODEL, "exists": None, "price": None,
                  "price_known": False, "alert": True,
                  "reason": f"/models lekérés hiba: {type(e).__name__}: {e}"}
    report["checked_at"] = ts
    if report["alert"]:
        msg = ("🚨 <b>NULLTARIF-ŐR RIASZTÁS</b>\n"
               f"{report['reason']}\n"
               f"Modell: <code>{report['model']}</code> · {ts}\n"
               "A P1-motor NEM vált automatikusan — döntés kell (env: "
               "DELPHOI_NOWCAST_MODEL / DELPHOI_FG_MODEL / ORAKEL_MODEL).")
        logger.warning("NULLTARIF-ŐR: %s", report["reason"])
        sent = await (send_fn(msg) if send_fn else _default_send_telegram(msg))
        report["alert_sent"] = bool(sent)
    else:
        logger.info("nulltarif-őr OK: %s", report["reason"])
    return report


# ── Cron-előkészítés (a seed a P1-deploy lépése — itt NEM fut automatikusan) ──

def seed_cron(conn) -> bool:
    """Idempotens recept-seed a pyramid_recipes-be (a delphoi cron-mintája).
    DEPLOYKOR hívandó — a register_tools szándékosan nem hívja."""
    exists = conn.execute("SELECT 1 FROM pyramid_recipes WHERE name=?",
                          (CRON_RECIPE_NAME,)).fetchone()
    if exists:
        return False
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, "
        "created_by, created_at, updated_at, cron_schedule, cron_model, cron_enabled, cron_delivery) "
        "VALUES (?, ?, '[]', ?, 'system', ?, ?, ?, 'deepseek', 1, 'none')",
        (CRON_RECIPE_NAME, CRON_DESC,
         "(special-cased — runtime: plugins.nulltarif_guard.cron_entry)", ts, ts, CRON_SCHEDULE))
    conn.commit()
    logger.info("nulltarif_guard recipe seed: %s (cron=%s)", CRON_RECIPE_NAME, CRON_SCHEDULE)
    return True


async def cron_entry(recipe_name: str, deps: dict | None = None) -> None:
    """A server _cron_loop special-case belépési pontja (bekötés deploykor)."""
    try:
        report = await daily_check(deps or _DEPS)
        logger.info("nulltarif cron kész: %s", json.dumps(report, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.exception("nulltarif cron_entry (%s) failed", recipe_name)


def register_tools(app, deps):
    """Nem regisztrál MCP-toolt (tool-count fegyelem) — csak a deps-t tárolja.
    A cron-seed (seed_cron) és a _cron_loop-bekötés a P1-deploy lépése."""
    global _DEPS
    _DEPS = deps
    logger.info("nulltarif_guard betöltve (cron-seed deploykor: %s)", CRON_RECIPE_NAME)
