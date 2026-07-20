#!/usr/bin/env python3
"""migrate_pythia_p1.py — PYTHIA P1 ledger-migráció (Bridge).

Cél (GESAMTBEFEHL P1/2 + MODIFIKATION 2/A2):
  - delphoi_nowcast_ledger + model_id oszlop (ha hiányzik);
  - delphoi_jobs + model_id, panel_version, scope_verdict, coverage_score;
  - A2 IDENTITÁS-RÉTEG: delphoi_users tábla (user_id INTEGER PK, origin
    CHECK('echolot'|'saas'), external_id, email, created_at) + backfill —
    a meglévő Echolot-eredetű user-hivatkozások (delphoi_credits.user_id ∪
    delphoi_jobs.user_id; a mező TEXT, az Echolot-proxy 'user:<id>' formát
    küld) origin='echolot' sorokként, IDEMPOTENSEN (UNIQUE(origin,
    external_id) index a DB-szintű garancia).

Módszer: ALTER TABLE ... ADD COLUMN — az append-only triggerekkel nem ütközik
(az ALTER nem UPDATE/DELETE, a triggerek nem tüzelnek). RÉGI SOR NEM ÉRINTHETŐ:
a hozzáadott oszlop minden meglévő sorban NULL marad — model_id=NULL =
Flash-korszak (a korszakhatár-jelölés így olvassa).

Futás:
  python migrate_pythia_p1.py                  # --dry-run a DEFAULT: csak terv
  python migrate_pythia_p1.py --apply          # backup + ALTER + verify (KÖTELEZŐ zöld)
  python migrate_pythia_p1.py --db /path/x.db  # explicit DB (default: BRIDGE_DB_PATH | bridge.db)

--apply garanciák:
  1. időbélyeges FÁJL-MÁSOLAT backup az ALTER előtt;
  2. utána verify_ledger_chain() — ha NEM zöld: hangos hiba (exit 2) + a
     backup-útvonal kiírása. A hash-lánc nem tartalmazza a model_id-t, ezért
     az oszlop-hozzáadás a láncot nem érintheti — ha mégis, azonnal kiderül.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

# Repo-gyökér a sys.path-ra (plugins.delphoi importhoz)
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# (tábla, oszlop, típus) — CSAK ADD COLUMN, sor-érintés nincs.
PLANNED_COLUMNS = [
    ("delphoi_nowcast_ledger", "model_id", "TEXT"),
    ("delphoi_jobs", "model_id", "TEXT"),
    ("delphoi_jobs", "panel_version", "TEXT"),
    ("delphoi_jobs", "scope_verdict", "TEXT"),
    ("delphoi_jobs", "coverage_score", "REAL"),
]

# A2 — identitás-réteg (brand-agnosztikus Core: 'echolot' és 'saas' eredetű
# userek egy táblában; az external_id az eredet-oldali azonosító, Echolotnál
# a mai 'user:<id>' TEXT-kulcs). Az UNIQUE index az idempotens backfill őre.
_USERS_SQL = """
CREATE TABLE IF NOT EXISTS delphoi_users (
    user_id     INTEGER PRIMARY KEY,
    origin      TEXT CHECK(origin IN ('echolot','saas')),
    external_id TEXT,
    email       TEXT,
    created_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_delphoi_users_origin_ext
    ON delphoi_users(origin, external_id);
"""

# A backfill forrásai: a MAI user-hivatkozás mezők (mindkettő TEXT).
_USER_REF_SOURCES = [("delphoi_credits", "user_id"), ("delphoi_jobs", "user_id")]


def _pending_user_refs(conn: sqlite3.Connection) -> list[str]:
    """Distinct user-hivatkozások a forrás-táblákból, amelyek MÉG nincsenek a
    delphoi_users-ben origin='echolot'-tal. Hiányzó forrás-tábla = üres ág."""
    refs: set = set()
    for table, col in _USER_REF_SOURCES:
        if not _table_exists(conn, table):
            continue
        for r in conn.execute(f"SELECT DISTINCT {col} FROM {table} "
                              f"WHERE {col} IS NOT NULL AND {col} != ''"):
            refs.add(str(r[0]))
    if not refs:
        return []
    existing: set = set()
    if _table_exists(conn, "delphoi_users"):
        existing = {str(r[0]) for r in conn.execute(
            "SELECT external_id FROM delphoi_users WHERE origin='echolot'")}
    return sorted(refs - existing)


def apply_users(conn: sqlite3.Connection) -> int:
    """Tábla + index (IF NOT EXISTS) + idempotens origin='echolot' backfill.
    Visszaadja az ÚJ user-sorok számát."""
    conn.executescript(_USERS_SQL)
    pending = _pending_user_refs(conn)
    ts = datetime.now(timezone.utc).isoformat()
    n = 0
    for ext in pending:
        cur = conn.execute(
            "INSERT OR IGNORE INTO delphoi_users (origin, external_id, created_at) "
            "VALUES ('echolot', ?, ?)", (ext, ts))
        n += cur.rowcount or 0
    conn.commit()
    return n


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def build_plan(conn: sqlite3.Connection) -> list[dict]:
    """A terv: mely oszlopok hiányoznak. Hiányzó tábla → skip-jegyzet (a táblát
    az app-indulás ensure_tables/ensure_fg_tables-e hozza létre, MÁR az új
    sémával — ott nincs mit migrálni)."""
    plan = []
    for table, col, typ in PLANNED_COLUMNS:
        if not _table_exists(conn, table):
            plan.append({"table": table, "column": col, "type": typ,
                         "action": "skip", "why": "tábla nem létezik (app-induláskor új sémával jön létre)"})
            continue
        if col in _columns(conn, table):
            plan.append({"table": table, "column": col, "type": typ,
                         "action": "skip", "why": "oszlop már létezik"})
        else:
            plan.append({"table": table, "column": col, "type": typ, "action": "add"})
    return plan


def apply_plan(conn: sqlite3.Connection, plan: list[dict]) -> int:
    n = 0
    for step in plan:
        if step["action"] != "add":
            continue
        conn.execute(
            f'ALTER TABLE {step["table"]} ADD COLUMN {step["column"]} {step["type"]}')
        n += 1
    conn.commit()
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PYTHIA P1 ledger-migráció (append-only-barát ALTER-ek)")
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
        users_exists = _table_exists(conn, "delphoi_users")
        pending_users = _pending_user_refs(conn)
    finally:
        conn.close()

    print(f"DB: {os.path.abspath(args.db)}")
    print(f"Mód: {'APPLY' if args.apply else 'DRY-RUN (terv — futtasd --apply-jal a végrehajtáshoz)'}")
    for step in plan:
        if step["action"] == "add":
            print(f"  + ALTER TABLE {step['table']} ADD COLUMN {step['column']} {step['type']}")
        else:
            print(f"  = {step['table']}.{step['column']}: SKIP ({step['why']})")
    print(f"  {'=' if users_exists else '+'} delphoi_users tábla (A2): "
          f"{'már létezik' if users_exists else 'CREATE + UNIQUE(origin, external_id) index'}")
    print(f"  + backfill origin='echolot': {len(pending_users)} új user-hivatkozás "
          f"(delphoi_credits ∪ delphoi_jobs, idempotens)")
    to_add = [s for s in plan if s["action"] == "add"]
    if not args.apply:
        print(f"Terv: {len(to_add)} oszlop-hozzáadás, {len(plan) - len(to_add)} skip, "
              f"{len(pending_users)} user-backfill. (dry-run — semmi nem íródott)")
        return 0

    # ── APPLY: 1) backup ────────────────────────────────────────────────────
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = f"{args.db}.pre_pythia_p1.{stamp}.bak"
    shutil.copy2(args.db, backup_path)
    print(f"Backup kész: {backup_path}")

    # ── 2) ALTER-ek ─────────────────────────────────────────────────────────
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        n = apply_plan(conn, plan)
        n_users = apply_users(conn)
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
    print(f"Végrehajtva: {n} oszlop hozzáadva; delphoi_users kész, "
          f"{n_users} új origin='echolot' user-sor backfillelve.")

    # ── 3) verify_ledger_chain — KÖTELEZŐ zöld ─────────────────────────────
    from plugins.delphoi import verify_ledger_chain

    def _get_db():
        c = sqlite3.connect(args.db)
        c.row_factory = sqlite3.Row
        return c

    conn = sqlite3.connect(args.db)
    has_ledger = _table_exists(conn, "delphoi_nowcast_ledger")
    conn.close()
    if not has_ledger:
        print("verify: a delphoi_nowcast_ledger tábla nem létezik ezen a DB-n — nincs lánc, nincs mit ellenőrizni.")
        return 0
    rep = verify_ledger_chain(_get_db)
    if not rep.get("ok"):
        print(f"HIBA: verify_ledger_chain NEM zöld: {rep}", file=sys.stderr)
        print(f"A DB visszaállítható a backupból: {backup_path}", file=sys.stderr)
        return 2
    print(f"verify_ledger_chain ZÖLD: {rep['checked']} sor, head={str(rep.get('head'))[:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
