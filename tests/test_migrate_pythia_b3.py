"""test_migrate_pythia_b3.py — B3 migráció: dry-run default, backup + apply,
idempotencia, verify (a migrate_pythia_p1/b1 teszt-mintája)."""
import glob
import sqlite3

import migrate_pythia_b3 as mig


def _mk_db(tmp_path) -> str:
    path = str(tmp_path / "bridge_test.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS press_snapshots (id INTEGER PRIMARY KEY)")
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


def test_dry_run_default_writes_nothing(tmp_path, capsys):
    db = _mk_db(tmp_path)
    rc = mig.main(["--db", db])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" in out
    assert not _tables(db) & set(mig.PLANNED_TABLES)
    assert not glob.glob(db + ".pre_pythia_b3.*")   # backup sem készül


def test_apply_creates_tables_with_backup(tmp_path, capsys):
    db = _mk_db(tmp_path)
    rc = mig.main(["--db", db, "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert set(mig.PLANNED_TABLES) <= _tables(db)
    backups = glob.glob(db + ".pre_pythia_b3.*")
    assert len(backups) == 1
    assert "Backup kész" in out and "verify" in out


def test_apply_idempotent(tmp_path, capsys):
    db = _mk_db(tmp_path)
    assert mig.main(["--db", db, "--apply"]) == 0
    capsys.readouterr()
    assert mig.main(["--db", db, "--apply"]) == 0
    out = capsys.readouterr().out
    assert out.count("SKIP") == len(mig.PLANNED_TABLES)


def test_p1_users_table_respected(tmp_path, capsys):
    """Ha a p1-migráció már létrehozta a delphoi_users-t, a b3 SKIP-eli és
    csak a kulcs-táblát teszi mellé (nincs séma-ütközés)."""
    db = _mk_db(tmp_path)
    import migrate_pythia_p1 as p1
    conn = sqlite3.connect(db)
    conn.executescript(p1._USERS_SQL)
    conn.commit()
    conn.close()
    rc = mig.main(["--db", db, "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "delphoi_users: SKIP" in out
    assert "delphoi_api_keys" in _tables(db)


def test_apply_verifies_ledger_chain_when_present(tmp_path, capsys):
    """Ha van nowcast-ledger a DB-n, a verify_ledger_chain KÖTELEZŐ zöld."""
    db = _mk_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    from plugins import delphoi
    delphoi.ensure_tables(conn)
    conn.close()

    def _get_db():
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        return c

    delphoi.append_ledger_row(_get_db, "teszt-entitas", "HU", "2026-W30",
                              0.25, "hash-x", "tencent/Hy3|teszt")
    rc = mig.main(["--db", db, "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verify_ledger_chain ZÖLD" in out


def test_missing_db_fails_loud(tmp_path, capsys):
    rc = mig.main(["--db", str(tmp_path / "nincs.db")])
    assert rc == 1
    assert "HIBA" in capsys.readouterr().err


def test_migration_ddl_matches_plugin_source_of_truth(tmp_path):
    """A migráció ugyanabból a DDL-ből dolgozik, mint az app-indulás
    (delphoi_public_mcp.ensure_key_tables) — nincs séma-kettősség."""
    db = _mk_db(tmp_path)
    assert mig.main(["--db", db, "--apply"]) == 0
    conn = sqlite3.connect(db)
    kcols = {r[1] for r in conn.execute("PRAGMA table_info(delphoi_api_keys)")}
    ucols = {r[1] for r in conn.execute("PRAGMA table_info(delphoi_users)")}
    conn.close()
    assert kcols == {"id", "key_hash", "key_prefix", "user_id", "label",
                     "created_at", "revoked_at"}
    assert ucols == {"user_id", "origin", "external_id", "email", "created_at"}
