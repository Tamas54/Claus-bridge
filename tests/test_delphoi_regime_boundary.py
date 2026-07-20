"""test_delphoi_regime_boundary.py — modellregime-korszakhatár (PYTHIA P1).

Az első Hy3-sor korszakhatár: ahol a direction_prev egy Flash-korszakú sorra
mutat (model_id=NULL VAGY Flash-string), a kimenet model_regime_boundary=True
jelzést + REGIME_NOTE megjegyzést kap. Régi sorok érintetlenek.
"""
import pytest

from plugins import delphoi


def test_model_era_classification():
    assert delphoi._model_era(None) == "flash"          # migráció előtti sor
    assert delphoi._model_era("") == "flash"
    assert delphoi._model_era("deepseek-ai/DeepSeek-V4-Flash|non-think") == "flash"
    assert delphoi._model_era("tencent/Hy3|non-think|temp=0.8") == "hy3"
    assert delphoi._model_era("valami/Egyeb-Model|x") == "valami/egyeb-model"


@pytest.fixture
def ledger_db(get_db):
    conn = get_db()
    delphoi.ensure_tables(conn)
    delphoi.seed_registry(conn)
    conn.close()
    return get_db


def _append(get_db, entity="Q124488292", country="HU", direction=0.1, model_id=None):
    return delphoi.append_ledger_row(
        get_db, entity, country, "2026-W30", direction, "ch", model_id or "x")


def test_boundary_marked_on_first_hy3_row(ledger_db):
    flash_id = "deepseek-ai/DeepSeek-V4-Flash|non-think|temp=0.8|ssr=linear|emb=e"
    hy3_id = "tencent/Hy3|non-think|temp=0.8|ssr=linear|emb=e"
    _append(ledger_db, direction=-0.2, model_id=flash_id)
    _append(ledger_db, direction=-0.1, model_id=flash_id)
    _append(ledger_db, direction=-0.3, model_id=hy3_id)     # ← korszakhatár
    _append(ledger_db, direction=-0.25, model_id=hy3_id)    # Hy3→Hy3: nem határ

    st = delphoi.nowcast_status(ledger_db, entity_key="Q124488292", limit=10)
    rows = list(reversed(st["latest_ledger"]))  # kronológiai sorrend
    assert "model_regime_boundary" not in rows[0]
    assert "model_regime_boundary" not in rows[1]
    assert rows[2]["model_regime_boundary"] is True
    assert rows[2]["regime_note"] == delphoi.REGIME_NOTE
    assert "bázisreset" in rows[2]["regime_note"]
    assert "model_regime_boundary" not in rows[3]


def test_null_model_id_counts_as_flash_era(ledger_db):
    """Migráció előtti (model_id=NULL) sor = Flash-korszak — az első Hy3-sor
    utána korszakhatár. A migrált-legacy állapotot hűen szimuláljuk: pre-P1
    tábla (model_id nélkül) + ALTER ADD COLUMN (nullable, ahogy a migráció)."""
    conn = ledger_db()
    conn.executescript("""
        DROP TABLE delphoi_nowcast_ledger;
        CREATE TABLE delphoi_nowcast_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_key TEXT NOT NULL, country TEXT NOT NULL,
            predicted_at TEXT NOT NULL, target_window TEXT NOT NULL,
            direction REAL NOT NULL, direction_prev REAL,
            corpus_hash TEXT NOT NULL, segment_json TEXT,
            prev_hash TEXT NOT NULL, content_hash TEXT NOT NULL);
        CREATE TRIGGER delphoi_ledger_no_update BEFORE UPDATE ON delphoi_nowcast_ledger
        BEGIN SELECT RAISE(ABORT, 'delphoi_nowcast_ledger is append-only'); END;
        CREATE TRIGGER delphoi_ledger_no_delete BEFORE DELETE ON delphoi_nowcast_ledger
        BEGIN SELECT RAISE(ABORT, 'delphoi_nowcast_ledger is append-only'); END;
    """)
    ch = delphoi.compute_content_hash("Q387006", "2026-07-01T00:00:00+00:00",
                                      "2026-W28", 0.1, "ch0", delphoi.GENESIS)
    conn.execute(
        "INSERT INTO delphoi_nowcast_ledger (entity_key, country, predicted_at, "
        "target_window, direction, corpus_hash, prev_hash, content_hash) "
        "VALUES ('Q387006','HU','2026-07-01T00:00:00+00:00','2026-W28',0.1,'ch0',?,?)",
        (delphoi.GENESIS, ch))
    conn.execute("ALTER TABLE delphoi_nowcast_ledger ADD COLUMN model_id TEXT")
    conn.commit()
    conn.close()
    _append(ledger_db, entity="Q387006", direction=0.2,
            model_id="tencent/Hy3|non-think")
    st = delphoi.nowcast_status(ledger_db, entity_key="Q387006", limit=10)
    newest = st["latest_ledger"][0]
    assert newest["model_regime_boundary"] is True
    oldest = st["latest_ledger"][-1]
    assert "model_regime_boundary" not in oldest


def test_first_row_of_entity_is_never_boundary(ledger_db):
    _append(ledger_db, entity="Q948", country="PL", model_id="tencent/Hy3|non-think")
    st = delphoi.nowcast_status(ledger_db, entity_key="Q948", limit=5)
    assert "model_regime_boundary" not in st["latest_ledger"][0]


def test_public_nowcast_feed_carries_boundary_fields(ledger_db):
    flash_id = "deepseek-ai/DeepSeek-V4-Flash|non-think"
    _append(ledger_db, direction=-0.2, model_id=flash_id)
    _append(ledger_db, direction=-0.1, model_id="tencent/Hy3|non-think")
    feed = delphoi.public_nowcast_feed(ledger_db, entity_key="Q124488292", history=10)
    assert feed["count"] == 1
    ent = feed["entities"][0]
    assert ent["latest"]["model_regime_boundary"] is True
    assert ent["latest"]["regime_note"] == delphoi.REGIME_NOTE
    # a history newest-first; a régi Flash-sor jelöletlen
    assert "model_regime_boundary" not in ent["history"][-1]
    # a feed hordozza a model_id-t is (az Echolot-oldali defenzív jelöléshez)
    assert "model_id" in ent["latest"]
