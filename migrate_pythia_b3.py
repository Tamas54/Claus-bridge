#!/usr/bin/env python3
"""migrate_pythia_b3.py — PYTHIA B3 API-kulcs réteg migráció (Bridge).

Cél (GESAMTBEFEHL B3): a publikus SaaS MCP-kapu auth-táblái éles DB-n, a
migrate_pythia_p1/b1 bevált mintájával (dry-run DEFAULT + időbélyeges backup +
idempotens + verify):
  - delphoi_users    (A2 — ha a p1-migráció még nem hozta létre; IF NOT EXISTS)
  - delphoi_api_keys (key_hash SHA256, user_id → delphoi_users.user_id, label,
                      created_at, revoked_at + key_prefix a kirakat-listához)

A DDL az EGYETLEN igazságforrásból jön: plugins.delphoi_public_mcp
(ensure_key_tables — delphoi_users a migrate_pythia_p1._USERS_SQL-ből,
delphoi_api_keys az API_KEYS_INIT_SQL-ből; mind IF NOT EXISTS — az
újrafuttatás no-op). Meglévő táblához NEM nyúlunk; az append-only
nowcast-ledger triggereit a CREATE nem érinti.

Futás:
  python migrate_pythia_b3.py                  # --dry-run a DEFAULT: csak terv
  python migrate_pythia_b3.py --apply          # backup + CREATE + verify
  python migrate_pythia_b3.py --db /path/x.db  # explicit DB (default: BRIDGE_DB_PATH | bridge.db)

--apply garanciák:
  1. időbélyeges FÁJL-MÁSOLAT backup a CREATE előtt;
  2. utána verify: mindkét tábla létezik + (ha van nowcast-ledger)
     verify_ledger_chain() KÖTELEZŐ zöld — ha nem: hangos hiba (exit 2)
     + a backup-útvonal kiírása.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

# Repo-gyökér a sys.path-ra (plugins.* importhoz)
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PLANNED_TABLES = ("delphoi_users", "delphoi_api_keys")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def build_plan(conn: sqlite3.Connection) -> list[dict]:
    plan = []
    for table in PLANNED_TABLES:
        if _table_exists(conn, table):
            plan.append({"table": table, "action": "skip", "why": "tábla már létezik"})
        else:
            plan.append({"table": table, "action": "create"})
    return plan


def apply_plan(conn: sqlite3.Connection) -> None:
    """Az EGYETLEN igazságforrás DDL-je (idempotens IF NOT EXISTS)."""
    from plugins.delphoi_public_mcp import ensure_key_tables
    ensure_key_tables(conn)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="PYTHIA B3 API-kulcs réteg migráció (idempotens CREATE-ek)")
    ap.add_argument("--db", default=os.environ.get("BRIDGE_DB_PATH", "bridge.db"),
                    help="DB-útvonal (default: BRIDGE_DB_PATH env vagy bridge.db)")
    ap.add_argument("--apply", action="store_true",
                    help="Végrehajtás backup+verify-jal (nélküle: dry-run, csak terv)")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"HIBA: nincs ilyen DB: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        plan = build_plan(conn)
    finally:
        conn.close()

    print(f"DB: {os.path.abspath(args.db)}")
    print(f"Mód: {'APPLY' if args.apply else 'DRY-RUN (terv — futtasd --apply-jal a végrehajtáshoz)'}")
    for step in plan:
        if step["action"] == "create":
            print(f"  + CREATE TABLE {step['table']} (+ indexek)")
        else:
            print(f"  = {step['table']}: SKIP ({step['why']})")
    to_create = [s for s in plan if s["action"] == "create"]
    if not args.apply:
        print(f"Terv: {len(to_create)} tábla-létrehozás, {len(plan) - len(to_create)} skip. "
              "(dry-run — semmi nem íródott)")
        return 0

    # ── APPLY: 1) backup ────────────────────────────────────────────────────
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = f"{args.db}.pre_pythia_b3.{stamp}.bak"
    shutil.copy2(args.db, backup_path)
    print(f"Backup kész: {backup_path}")

    # ── 2) CREATE-ek (idempotens) ───────────────────────────────────────────
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        apply_plan(conn)
    except Exception as e:  # noqa: BLE001 — hangos hiba + backup-útvonal
        print(f"HIBA a migráció közben: {type(e).__name__}: {e}", file=sys.stderr)
        print(f"A DB visszaállítható a backupból: {backup_path}", file=sys.stderr)
        conn.close()
        return 2
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    print(f"Végrehajtva: {len(to_create)} új tábla (a többi már létezett).")

    # ── 3) verify: táblák + (ha van ledger) hash-lánc KÖTELEZŐ zöld ────────
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        missing = [t for t in PLANNED_TABLES if not _table_exists(conn, t)]
        has_ledger = _table_exists(conn, "delphoi_nowcast_ledger")
    finally:
        conn.close()
    if missing:
        print(f"HIBA: verify — hiányzó táblák a migráció után: {missing}", file=sys.stderr)
        print(f"A DB visszaállítható a backupból: {backup_path}", file=sys.stderr)
        return 2
    print(f"verify: mind a(z) {len(PLANNED_TABLES)} tábla létezik.")
    if has_ledger:
        from plugins.delphoi import verify_ledger_chain

        def _get_db():
            c = sqlite3.connect(args.db)
            c.row_factory = sqlite3.Row
            return c

        rep = verify_ledger_chain(_get_db)
        if not rep.get("ok"):
            print(f"HIBA: verify_ledger_chain NEM zöld: {rep}", file=sys.stderr)
            print(f"A DB visszaállítható a backupból: {backup_path}", file=sys.stderr)
            return 2
        print(f"verify_ledger_chain ZÖLD: {rep['checked']} sor, "
              f"head={str(rep.get('head'))[:16]}…")
    else:
        print("verify: nincs delphoi_nowcast_ledger ezen a DB-n — lánc-ellenőrzés kihagyva.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
