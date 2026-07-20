"""test_migrate_pythia_p4.py — P4 pénz-út migráció: dry-run default, backup +
apply MÁSOLATON, idempotencia, origin-oszlop + placeholder-seed, régi ledger-sor
érintetlen (a migrate_pythia_p1/b3 teszt-mintája)."""
import glob
import shutil
import sqlite3

import migrate_pythia_p4 as mig
from plugins.delphoi_stripe import SEED_PACKS


def _mk_db(tmp_path, with_ledger=True) -> str:
    path = str(tmp_path / "bridge_test.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS press_snapshots (id INTEGER PRIMARY KEY)")
    if with_ledger:
        # A MAI (origin-oszlop nélküli) credit_ledger + egy meglévő sor —
        # a migrációnak a régi sort NEM szabad érintenie.
        conn.execute(
            "CREATE TABLE delphoi_credit_ledger ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,"
            " delta INTEGER NOT NULL, reason TEXT NOT NULL, job_id TEXT,"
            " created_at TEXT NOT NULL, UNIQUE(user_id, reason))")
        conn.execute(
            "INSERT INTO delphoi_credit_ledger (user_id, delta, reason, created_at) "
            "VALUES ('user:1', 2, 'signup_grant', '2026-07-01T00:00:00+00:00')")
    conn.commit()
    conn.close()
    return path


def _tables(path: str) -> set:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _cols(path: str, table: str) -> list:
    conn = sqlite3.connect(path)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def test_dry_run_default_writes_nothing(tmp_path, capsys):
    db = _mk_db(tmp_path)
    rc = mig.main(["--db", db])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" in out and "PLACEHOLDER" in out
    assert not _tables(db) & set(mig.PLANNED_TABLES)
    assert "origin" not in _cols(db, "delphoi_credit_ledger")
    assert not glob.glob(db + ".pre_pythia_p4.*")     # backup sem készül


def test_apply_on_copy_creates_schema_and_seed(tmp_path, capsys):
    """Migráció MÁSOLAT-teszten: az eredeti DB érintetlen, a másolaton
    táblák + origin-oszlop + placeholder-seed."""
    original = _mk_db(tmp_path)
    work = str(tmp_path / "bridge_copy.db")
    shutil.copy2(original, work)

    rc = mig.main(["--db", work, "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Backup kész" in out and "verify" in out

    # a MÁSOLATON: séma + seed kész
    assert set(mig.PLANNED_TABLES) <= _tables(work)
    assert "origin" in _cols(work, "delphoi_credit_ledger")
    conn = sqlite3.connect(work)
    conn.row_factory = sqlite3.Row
    try:
        n = conn.execute("SELECT COUNT(*) FROM delphoi_price_config").fetchone()[0]
        old = conn.execute(
            "SELECT * FROM delphoi_credit_ledger WHERE reason='signup_grant'").fetchone()
        cur = conn.execute(
            "SELECT origin, pack_key, currency FROM delphoi_price_config "
            "WHERE origin='echolot'").fetchall()
    finally:
        conn.close()
    assert n == len(SEED_PACKS)
    # RÉGI SOR NEM ÉRINTHETŐ: delta/reason változatlan, origin=NULL (korszakjel)
    assert old["delta"] == 2 and old["origin"] is None
    # currency-fegyelem a seedben: echolot → HUF/EUR
    assert {r["currency"] for r in cur} == {"HUF", "EUR"}

    # az EREDETI DB-hez a migráció nem nyúlt
    assert not _tables(original) & set(mig.PLANNED_TABLES)
    assert "origin" not in _cols(original, "delphoi_credit_ledger")

    backups = glob.glob(work + ".pre_pythia_p4.*")
    assert len(backups) == 1


def test_apply_idempotent(tmp_path, capsys):
    db = _mk_db(tmp_path)
    assert mig.main(["--db", db, "--apply"]) == 0
    capsys.readouterr()
    assert mig.main(["--db", db, "--apply"]) == 0
    out = capsys.readouterr().out
    # második futás: minden lépés SKIP + 0 új seed-sor
    assert out.count("SKIP") == len(mig.PLANNED_TABLES) + 1
    assert "0 hiányzó csomag" in out
    conn = sqlite3.connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM delphoi_price_config").fetchone()[0]
    finally:
        conn.close()
    assert n == len(SEED_PACKS)


def test_missing_ledger_table_is_skip_note(tmp_path, capsys):
    db = _mk_db(tmp_path, with_ledger=False)
    rc = mig.main(["--db", db, "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "tábla nem létezik" in out
    assert set(mig.PLANNED_TABLES) <= _tables(db)


def test_missing_db_errors(tmp_path):
    assert mig.main(["--db", str(tmp_path / "nincs.db")]) == 1
