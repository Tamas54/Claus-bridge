"""
YoungeReka — napi keret, EGY számláló mindkét úthoz.
=====================================================

A munkaparancs úgy fogalmazott, hogy „ugyanaz a számláló, mint az
MCP-úton — ne csinálj másodikat". A felmérés szerint elsőt sem csinált
még senki: a `youngereka_profile.py`-ban nem volt `daily_budget_usd`,
és a repóban sehol nincs költség-könyvelés. Ez tehát AZ egy számláló;
az MCP-út és a chat-út egyaránt ide könyvel.

MÉRŐÓRA, NEM SOROMPÓ
--------------------
A keret elérése nem hibaüzenet és nem tiltás: csendes átváltás a
deepseekre. Réka ebből semmit nem vesz észre azon kívül, hogy a
válasz stílusa kicsit más. Egy „elfogyott a kereted" felirat annak
szólna, aki a számlát fizeti — az meg nem ő.

A napi határ env-ből felülírható (`YR_DAILY_BUDGET_USD`), hogy a
Kommandant redeploy nélkül emelhesse.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("bridge.yr_budget")

DEFAULT_DAILY_BUDGET_USD = 5.0

#: USD / 1M token (input, output). Csak a MÉRŐÓRÁHOZ kell — a számlát a
#: SiliconFlow állítja ki, ez a becslés. A kimi3 ára a drágaság oka,
#: ezért csak a „Gondolkodj rajta alaposan" gomb hívja.
_PRICE = {
    "moonshotai/Kimi-K2.7-Code":  (0.60, 2.50),
    "moonshotai/Kimi-K3":         (2.00, 15.00),
    "deepseek-ai/DeepSeek-V4-Pro": (0.30, 1.20),
    "zai-org/GLM-5.2":            (0.40, 1.60),
    "tencent/Hy3":                (0.00, 0.00),
}
_PRICE_FALLBACK = (0.60, 2.50)


def daily_budget_usd() -> float:
    try:
        return float(os.environ.get("YR_DAILY_BUDGET_USD") or DEFAULT_DAILY_BUDGET_USD)
    except ValueError:
        return DEFAULT_DAILY_BUDGET_USD


def estimate_cost(model_id: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = _PRICE.get(model_id, _PRICE_FALLBACK)
    return (tokens_in * pin + tokens_out * pout) / 1_000_000.0


def ensure_schema(conn: sqlite3.Connection) -> None:
    """TEXT uuid kulcs, nincs AUTOINCREMENT — a Postgres-migráció miatt."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS yr_spend (
          id         TEXT PRIMARY KEY,
          instance   TEXT NOT NULL,
          day        TEXT NOT NULL,
          usd        REAL NOT NULL,
          model      TEXT,
          source     TEXT,
          created_at TIMESTAMP NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_yr_spend_day "
                 "ON yr_spend(instance, day)")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record(conn: sqlite3.Connection, instance: str, usd: float,
           model: str = "", source: str = "chat") -> None:
    """Költés könyvelése. Sose dob — a könyvelés hibája nem viheti el a választ."""
    try:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO yr_spend (id, instance, day, usd, model, source, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), instance, _today(), float(usd), model, source,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("yr_spend könyvelés bukott (a válasz megy tovább): %s", e)


def spent_today(conn: sqlite3.Connection, instance: str) -> float:
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(usd), 0) FROM yr_spend WHERE instance=? AND day=?",
            (instance, _today())).fetchone()
        return float(row[0]) if row else 0.0
    except Exception as e:  # noqa: BLE001
        logger.warning("yr_spend olvasás bukott (0-nak vesszük): %s", e)
        return 0.0


def budget_state(conn: sqlite3.Connection, instance: str) -> dict:
    """A frontend keret-jelzőjének adata. `ratio` 0..1+."""
    limit = daily_budget_usd()
    spent = spent_today(conn, instance)
    return {
        "spent_usd": round(spent, 4),
        "limit_usd": limit,
        "ratio": round(spent / limit, 4) if limit > 0 else 0.0,
        "exhausted": limit > 0 and spent >= limit,
    }


def pick_model(conn: sqlite3.Connection, instance: str, requested: str) -> tuple[str, bool]:
    """(tényleges_modell, volt_e_fallback).

    Keret felett csendben deepseekre váltunk. A deepseek maga is
    fallback-cél, tehát azt sose írjuk felül — különben körbeérnénk.
    """
    if requested == "deepseek":
        return requested, False
    if budget_state(conn, instance)["exhausted"]:
        logger.info("YR napi keret kimerült → %s helyett deepseek", requested)
        return "deepseek", True
    return requested, False
