#!/usr/bin/env python3
"""migrate_pythia_p4.py — PYTHIA P4 pénz-út migráció (Bridge, MOD2/A5).

Cél (GESAMTBEFEHL P4 + MOD2/A5), a migrate_pythia_p1/b3 bevált mintájával
(dry-run DEFAULT + időbélyeges backup + idempotens + verify):
  - delphoi_price_config   (kredit-árlista config-DB-tábla: origin/brand
    dimenzió, currency HUF/EUR az echolot-originen, USD/EUR a saas-on,
    Stripe product/price-ID-k brand-enként; PLACEHOLDER-seed — az árak
    Kommandant-döntésre várnak)
  - delphoi_stripe_events  (webhook esemény-napló: credited/duplicate/
    failed_logged/ignored — a sikertelen fizetés útvonalának naplója)
  - delphoi_credit_ledger + origin oszlop (ALTER ADD COLUMN — a credit_ledger
    sorai origin-t rögzítenek; RÉGI SOR NEM ÉRINTHETŐ: origin=NULL =
    Stripe-előtti korszak)

A DDL az EGYETLEN igazságforrásból jön: plugins.delphoi_stripe
(ensure_stripe_tables + seed_prices — mind IF NOT EXISTS / INSERT OR IGNORE,
az újrafuttatás no-op). Az append-only nowcast-ledger triggereit sem a
CREATE, sem az ALTER nem érinti (nem UPDATE/DELETE).

Futás:
  python migrate_pythia_p4.py                  # --dry-run a DEFAULT: csak terv
  python migrate_pythia_p4.py --apply          # backup + CREATE/ALTER/seed + verify
  python migrate_pythia_p4.py --db /path/x.db  # explicit DB (default: BRIDGE_DB_PATH | bridge.db)

--apply garanciák:
  1. időbélyeges FÁJL-MÁSOLAT backup a változtatás előtt;
  2. utána verify: táblák léteznek + origin oszlop a credit_ledgeren (ha a
     tábla létezik) + (ha van nowcast-ledger) verify_ledger_chain() KÖTELEZŐ
     zöld — ha nem: hangos hiba (exit 2) + a backup-útvonal kiírása.
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

PLANNED_TABLES = ("delphoi_price_config", "delphoi_stripe_events")
LEDGER_TABLE = "delphoi_credit_ledger"
LEDGER_NEW_COLUMN = "origin"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def build_plan(conn: sqlite3.Connection) -> list[dict]:
    plan = []
    for table in PLANNED_TABLES:
        if _table_exists(conn, table):
            plan.append({"what": table, "action": "skip", "why": "tábla már létezik"})
        else:
            plan.append({"what": table, "action": "create"})
    if not _table_exists(conn, LEDGER_TABLE):
        plan.append({"what": f"{LEDGER_TABLE}.{LEDGER_NEW_COLUMN}", "action": "skip",
                     "why": "tábla nem létezik (app-induláskor új sémával jön létre)"})
    elif LEDGER_NEW_COLUMN in _columns(conn, LEDGER_TABLE):
        plan.append({"what": f"{LEDGER_TABLE}.{LEDGER_NEW_COLUMN}", "action": "skip",
                     "why": "oszlop már létezik"})
    else:
        plan.append({"what": f"{LEDGER_TABLE}.{LEDGER_NEW_COLUMN}", "action": "alter"})
    return plan


def _pending_seed(conn: sqlite3.Connection) -> int:
    """Hány placeholder-csomag hiányzik még (idempotens seed-előnézet)."""
    from plugins.delphoi_stripe import SEED_PACKS
    if not _table_exists(conn, "delphoi_price_config"):
        return len(SEED_PACKS)
    n = 0
    for origin, pack_key, _credits, currency, _amount in SEED_PACKS:
        row = conn.execute(
            "SELECT 1 FROM delphoi_price_config WHERE origin=? AND pack_key=? AND currency=?",
            (origin, pack_key, currency)).fetchone()
        if not row:
            n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="PYTHIA P4 pénz-út migráció (árlista-config + stripe-napló + ledger-origin)")
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
        n_seed = _pending_seed(conn)
    finally:
        conn.close()

    print(f"DB: {os.path.abspath(args.db)}")
    print(f"Mód: {'APPLY' if args.apply else 'DRY-RUN (terv — futtasd --apply-jal a végrehajtáshoz)'}")
    for step in plan:
        if step["action"] == "create":
            print(f"  + CREATE TABLE {step['what']}")
        elif step["action"] == "alter":
            print(f"  + ALTER TABLE {LEDGER_TABLE} ADD COLUMN {LEDGER_NEW_COLUMN} TEXT")
        else:
            print(f"  = {step['what']}: SKIP ({step['why']})")
    print(f"  + placeholder-árlista seed: {n_seed} hiányzó csomag "
          f"(idempotens; ÁRAK = PLACEHOLDER, Kommandant-döntés)")
    to_do = [s for s in plan if s["action"] != "skip"]
    if not args.apply:
        print(f"Terv: {len(to_do)} séma-lépés, {len(plan) - len(to_do)} skip, "
              f"{n_seed} seed-sor. (dry-run — semmi nem íródott)")
        return 0

    # ── APPLY: 1) backup ────────────────────────────────────────────────────
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = f"{args.db}.pre_pythia_p4.{stamp}.bak"
    shutil.copy2(args.db, backup_path)
    print(f"Backup kész: {backup_path}")

    # ── 2) CREATE/ALTER/seed — az igazságforrás a plugins.delphoi_stripe ────
    from plugins.delphoi_stripe import ensure_stripe_tables, seed_prices
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        ensure_stripe_tables(conn)
        n_seeded = seed_prices(conn)
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
    print(f"Végrehajtva: táblák + origin-oszlop kész, {n_seeded} placeholder-csomag seedelve.")

    # ── 3) verify — táblák + oszlop + (ha van) hash-lánc KÖTELEZŐ zöld ─────
    conn = sqlite3.connect(args.db)
    try:
        missing = [t for t in PLANNED_TABLES if not _table_exists(conn, t)]
        col_ok = (not _table_exists(conn, LEDGER_TABLE)
                  or LEDGER_NEW_COLUMN in _columns(conn, LEDGER_TABLE))
        has_ledger = _table_exists(conn, "delphoi_nowcast_ledger")
    finally:
        conn.close()
    if missing or not col_ok:
        print(f"HIBA: verify FAILED — hiányzó táblák: {missing}, "
              f"origin-oszlop: {col_ok}", file=sys.stderr)
        print(f"A DB visszaállítható a backupból: {backup_path}", file=sys.stderr)
        return 2
    if not has_ledger:
        print("verify: táblák+oszlop OK; delphoi_nowcast_ledger nincs ezen a DB-n — "
              "nincs lánc, nincs mit ellenőrizni.")
        return 0
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
    print(f"verify_ledger_chain ZÖLD: {rep['checked']} sor, head={str(rep.get('head'))[:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
