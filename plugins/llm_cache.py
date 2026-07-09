"""plugins/llm_cache.py — determinista LLM-hívás cache (EDSL-ihletésű).

A failure mode, amit kezel (recon/LOOT.md #2): reprodukálhatóság-hiány + token-pazarlás.
A panel-stack most minden futáskor friss API-hívást tesz; a perszóna-GENERÁLÁS seedelt,
de a válaszok (temp>0) nem cache-eltek → nem reprodukálhatók és drágák.

A kulcs-minta az EDSL `CacheEntry.gen_key()`-éből reimplementálva (MIT): MD5 a
(model | params | system | user | iteration) fölött, a params `sort_keys=True`-jal
szerializálva → a paraméter-sorrend nem befolyásolja a kulcsot. Az `iteration` mező
engedi a cellánkénti több FÜGGETLEN mintát is cache-elve (LOOT #3).

Backend: SQLite (alapért. ~/.cache/orakel/llm_cache.db), szál-biztos egy lock-kal.
Használat:
    cache = LLMCache()
    key = cache_key(model, {"temperature": 0.8}, system, user, iteration=0)
    hit = cache.get(key)
    if hit is None:
        hit = call_api(...)
        cache.set(key, hit, model=model)
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time

_SEP = "\x1f"   # unit separator — nem fordulhat elő prompt-szövegben


def cache_key(model: str, params: dict | None, system: str | None,
              user: str | None, iteration: int = 0) -> str:
    """Determinista MD5-kulcs. A params sort_keys=True-val → sorrend-független."""
    payload = _SEP.join([
        str(model or ""),
        json.dumps(params or {}, sort_keys=True, ensure_ascii=False),
        system or "",
        user or "",
        str(iteration),
    ])
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


class LLMCache:
    """Szál-biztos, SQLite-alapú kulcs→válasz cache."""

    def __init__(self, path: str | None = None):
        if path is None:
            path = os.path.join(os.path.expanduser("~"), ".cache", "orakel", "llm_cache.db")
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_cache "
            "(key TEXT PRIMARY KEY, value TEXT, model TEXT, ts REAL)"
        )
        self._conn.commit()

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM llm_cache WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str, model: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO llm_cache (key, value, model, ts) VALUES (?,?,?,?)",
                (key, value, model, time.time()))
            self._conn.commit()

    def get_or_set(self, key: str, producer, model: str | None = None):
        """(value, was_cached) — producer() csak miss esetén fut."""
        v = self.get(key)
        if v is not None:
            return v, True
        v = producer()
        self.set(key, v, model=model)
        return v, False

    def stats(self) -> dict:
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
        return {"entries": n, "path": self.path}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
