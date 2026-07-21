#!/usr/bin/env python3
"""run_first_nowcast_uk_us.py — US/UK KIRAKAT-BŐVÍTÉS egyszeri első futás.

Kommandant-parancs 2026-07-21 (B pont): a delphoi_entity_nowcast regiszterben
enabled=1-re flippeli az új kirakat-entitásokat, majd EGYSZERI első nowcast-
futást csinál CSAK rájuk. A meglévő 8 entitás lánc-sorait NEM érinti (per-entity
szűréssel fut); új entitásnál nincs korábbi ledger-sor, tehát regime-kérdés
sincs. A hétfői heti cron (delphoi_nowcast_weekly, 07:30) ettől kezdve viszi
őket automatikusan.

ENTITÁSOK:
  Q22686        US  Donald Trump
  keir-starmer  UK  Keir Starmer
  nigel-farage  UK  Nigel Farage

FUTÁS (migrate_pythia_* minta):
  python3 run_first_nowcast_uk_us.py                 # --dry-run a DEFAULT:
                                                     #   preflight, SEMMI írás/LLM
  python3 run_first_nowcast_uk_us.py --apply         # flip + első futás + ledger-sor
  python3 run_first_nowcast_uk_us.py --db /data/bridge.db --apply

Garanciák:
  - IDEMPOTENS: ha egy entitásnak már van ledger-sora, azt az entitást
    hangosan KIHAGYJA (a láncát a heti cron viszi tovább — ez a szkript
    kizárólag az első sort teheti le).
  - A flip a delphoi_entity_nowcast regisztert (mutable config) UPDATE-eli;
    az append-only delphoi_nowcast_ledger-hez CSAK INSERT-tel nyúl (a
    triggerek más utat fizikailag sem engednek).
  - Preflight (dry-runban is): COUNTRY_PANEL_CONFIG + EN kérdés/horgony-készlet
    + korpusz-elérhetőség (lang='en', uk_/us_ source-prefix) ellenőrzése.
    ÜRES korpusz esetén az --apply az adott entitást el sem indítja.

Env: SILICONFLOW_API_KEY (Hy3 panel), OPENAI_API_KEY (embedding, opcionális —
nélküle SF bge-m3), DELPHOI_NOWCAST_N / DELPHOI_NOWCAST_SAMPLES a szokott módon.
Emlékeztető élesre: DELPHOI_CORPUS_LANGS tartalmazza a 'de,en'-t (0eb3dd9 óta
default, de a Railway env-felülírást ellenőrizni kell), különben nincs napi
'en' press_snapshots-feed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
import sys

# Repo-gyökér a sys.path-ra (plugins.delphoi importhoz)
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except Exception:  # noqa: BLE001 — .env nélkül env-ből megy
    pass

from plugins import delphoi  # noqa: E402

# (entity_key, country) — CSAK ez a három; minden más sor érintetlen.
NEW_ENTITIES = [
    ("Q22686", "US"),
    ("keir-starmer", "UK"),
    ("nigel-farage", "UK"),
    ("andy-burnham", "UK"),   # Kommandant 07-21: az új brit miniszterelnök
]


def _get_db_factory(db_path: str):
    def get_db():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    return get_db


def flip_enabled(conn) -> list[str]:
    """enabled=1 a NEW_ENTITIES sorain (regiszter = mutable config; a seed
    INSERT OR IGNORE-ja a meglévő DB-sorokat nem éri el — ez itt a flip útja).
    Visszaadja a ténylegesen átbillentett kulcsokat (idempotens)."""
    flipped = []
    seed = {k: row for row in delphoi._SEED_ENTITIES for k in [row[0]]}
    for key, country in NEW_ENTITIES:
        row = conn.execute(
            "SELECT 1 FROM delphoi_entity_nowcast WHERE entity_key=? AND country=?",
            (key, country)).fetchone()
        if row is None and key in seed:
            # Élő DB-ben még nem létező, utólag seedelt entitás (pl. andy-burnham
            # 07-21): itt kap sort, egyenesen enabled=1-gyel.
            _, c, etype, label, _en = seed[key]
            delphoi.validate_display_label(label)
            conn.execute(
                "INSERT INTO delphoi_entity_nowcast (entity_key, country, "
                "entity_type, display_label, enabled, created_at) "
                "VALUES (?,?,?,?,1,?)",
                (key, c, etype, label,
                 datetime.now(timezone.utc).isoformat()))
            flipped.append(key)
            continue
        cur = conn.execute(
            "UPDATE delphoi_entity_nowcast SET enabled=1 "
            "WHERE entity_key=? AND country=? AND enabled=0", (key, country))
        if cur.rowcount:
            flipped.append(key)
    conn.commit()
    return flipped


def ledger_count(conn, key: str, country: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM delphoi_nowcast_ledger WHERE entity_key=? AND country=?",
        (key, country)).fetchone()[0]


def preflight(get_db) -> dict:
    """Read-only állapotkép: regiszter, ledger, EN-instrumentumok, korpusz."""
    out: dict = {"entities": {}, "en_layer_ok": True}
    for kind in ("regard", "price", "growth"):
        if ("en", kind) not in delphoi.NOWCAST_QUESTIONS:
            out["en_layer_ok"] = False
    if "en" not in delphoi.REFERENCE_SETS_REGARD:
        out["en_layer_ok"] = False
    conn = get_db()
    try:
        for key, country in NEW_ENTITIES:
            row = conn.execute(
                "SELECT enabled, display_label FROM delphoi_entity_nowcast "
                "WHERE entity_key=? AND country=?", (key, country)).fetchone()
            cfg = delphoi.COUNTRY_PANEL_CONFIG.get(country)
            corpus = delphoi.build_country_corpus(get_db, country)
            out["entities"][key] = {
                "country": country,
                "registered": row is not None,
                "enabled": bool(row["enabled"]) if row else False,
                "ledger_rows": ledger_count(conn, key, country),
                "panel_config": cfg is not None,
                "lang": (cfg or {}).get("lang"),
                "source_prefixes": list((cfg or {}).get("source_prefixes") or ()),
                # corpus_ok a döntő: a days a lang-réteg sorait számolja, a
                # context viszont már a uk_/us_ prefix-szűrés UTÁNI tartalom —
                # üres context = a nowcast az entitást el sem indítja.
                "corpus_ok": bool(corpus["context"]),
                "corpus_days": corpus["days"],
                "corpus_hash": corpus["corpus_hash"][:16],
            }
    finally:
        conn.close()
    return out


async def first_runs(deps: dict) -> list[dict]:
    """Egyszeri első nowcast CSAK a NEW_ENTITIES-re, entitásonként külön hívva
    (egy hibás entitás nem viszi el a többit). Már-láncolt entitás: SKIP."""
    get_db = deps["get_db"]
    results = []
    for key, country in NEW_ENTITIES:
        conn = get_db()
        try:
            n_rows = ledger_count(conn, key, country)
        finally:
            conn.close()
        if n_rows:
            print(f"  SKIP {key} ({country}): már van {n_rows} ledger-sora — "
                  "az első futás nem ismételhető, a heti cron viszi.")
            continue
        print(f"  RUN  {key} ({country}) …")
        rep = await delphoi.run_entity_nowcast(deps, entity_key=key, country=country)
        for r in rep.get("results", []):
            results.append(r)
            if r.get("ok"):
                print(f"       ok: direction={r['direction']:+.4f} "
                      f"n={r['n']} corpus_days={r['corpus_days']} "
                      f"hash={r['content_hash'][:16]}…")
            else:
                print(f"       HIBA: {r.get('error')}")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="US/UK kirakat: flip + egyszeri első nowcast")
    ap.add_argument("--db", default=os.environ.get("BRIDGE_DB_PATH", "bridge.db"))
    ap.add_argument("--apply", action="store_true",
                    help="flip + éles első futás (nélküle: dry-run preflight)")
    args = ap.parse_args()

    get_db = _get_db_factory(args.db)
    conn = get_db()
    try:
        delphoi.ensure_tables(conn)
        delphoi.seed_registry(conn)   # friss DB-n a sorok már enabled=1-gyel jönnek
    finally:
        conn.close()

    pf = preflight(get_db)
    print("PREFLIGHT:", json.dumps(pf, ensure_ascii=False, indent=1))
    if not pf["en_layer_ok"]:
        print("HIBA: hiányzik az EN NOWCAST_QUESTIONS/REFERENCE_SETS_REGARD réteg.")
        return 2

    if not args.apply:
        print("\nDRY-RUN (default): nincs flip, nincs LLM-hívás, nincs ledger-írás. "
              "Éleshez: --apply")
        return 0

    conn = get_db()
    try:
        flipped = flip_enabled(conn)
    finally:
        conn.close()
    print(f"FLIP: enabled=1 → {flipped or '(már mind enabled volt)'}")

    deps = {
        "get_db": get_db,
        "siliconflow_api_key": os.environ.get("SILICONFLOW_API_KEY", ""),
        "siliconflow_base_url": os.environ.get("SILICONFLOW_BASE_URL",
                                               "https://api.siliconflow.com/v1"),
        "siliconflow_timeout": int(os.environ.get("SILICONFLOW_TIMEOUT", "120")),
    }
    results = asyncio.run(first_runs(deps))
    ok = sum(1 for r in results if r.get("ok"))
    print(f"\nKÉSZ: {ok}/{len(results)} entitás kapott első ledger-sort.")
    chain = delphoi.verify_ledger_chain(get_db)
    print(f"LÁNC-ELLENŐRZÉS: ok={chain.get('ok')} checked={chain.get('checked')}")
    return 0 if (ok == len(results) and chain.get("ok")) else 1


if __name__ == "__main__":
    sys.exit(main())
