#!/usr/bin/env python3
"""SHARED_MEMORY — EGY írási út, és az is működjön.

    python3 -m pytest tests/test_shared_memory_egy_gazda.py

⛔ A MÉRT ÁLLAPOT (2026-09-07). Négy hívóhely írta ezt a táblát, HÁROM
mintával, és a különbségek nem szándékosak voltak, hanem hibák:

  * `_sf_budget_remember` és a Feldwebel `/email` piszkozata `ON
    CONFLICT(key)`-t használt — de a `key` oszlopon NEM VOLT egyedi index,
    tehát élesben ez jött: „ON CONFLICT clause does not match any PRIMARY KEY
    or UNIQUE constraint". Mindkettő ráadásul kihagyta a `created_at` NOT NULL
    oszlopot. KÉT hiba egy utasításban — a második csak az első javítása után
    látszott (élesben lépésenként mérve).
    KÖVETKEZMÉNY: az `sf_chat` megtanult token-kerete SOHA nem íródott ki, és
    a `/email` piszkozata SOHA nem tárolódott. Mindkettő WARNING-ként ment el,
    a hívó sikernek látta.
  * `resolve_discussion`: VAK INSERT — ugyanannak a beszélgetésnek a kétszeri
    lezárása néma duplikátumot hagyott.
  * `write_memory` / `api_memory_list`: SELECT + UPDATE/INSERT — működött, de
    két lekérdezés, versenyhelyzettel a résben.

Kommandant, 2026-09-07: „ne legyenek duplikalt kodutak. Legyen jó."
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

SEMA = """
CREATE TABLE shared_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    tags TEXT DEFAULT '',
    updated_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_shared_memory_key ON shared_memory(key);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SEMA)
    yield c
    c.close()


def _ir():
    from server import memoria_ir
    return memoria_ir


def test_ketszeri_iras_EGY_sort_hagy(conn):
    ir = _ir()
    ir(conn, "kulcs", "elso", category="system", updated_by="teszt")
    ir(conn, "kulcs", "masodik", category="system", updated_by="teszt")
    conn.commit()
    sorok = conn.execute("SELECT * FROM shared_memory WHERE key='kulcs'").fetchall()
    assert len(sorok) == 1, "az upsert nem duplikálhat"
    assert sorok[0]["value"] == "masodik", "a friss érték nyer"


def test_a_created_at_utkozeskor_NEM_irodik_felul(conn):
    """A `created_at` a sor KELETKEZÉSE — ugyanaz a szerep, mint a
    `first_seen`-é az epizód-tárban."""
    ir = _ir()
    ir(conn, "k", "egy", updated_by="teszt")
    conn.commit()
    elso = conn.execute("SELECT created_at, updated_at FROM shared_memory "
                        "WHERE key='k'").fetchone()
    ir(conn, "k", "ketto", updated_by="teszt")
    conn.commit()
    masodik = conn.execute("SELECT created_at, updated_at FROM shared_memory "
                           "WHERE key='k'").fetchone()
    assert masodik["created_at"] == elso["created_at"], \
        "a keletkezés ideje nem változhat"


def test_a_kotelezo_oszlopok_kitoltodnek(conn):
    """A `created_at` és az `updated_by` NOT NULL — a régi hívóhelyek egyike
    kihagyta, és az INSERT emiatt akkor is elhasalt volna, ha az ON CONFLICT
    működik."""
    ir = _ir()
    ir(conn, "k2", "ertek")
    conn.commit()
    r = conn.execute("SELECT * FROM shared_memory WHERE key='k2'").fetchone()
    for mezo in ("created_at", "updated_at", "updated_by", "category"):
        assert r[mezo], f"üresen maradt: {mezo}"


def test_a_semaban_VAN_egyedi_index_a_kulcsra():
    """Enélkül minden `ON CONFLICT(key)` némán elhasal — pontosan ez történt."""
    src = Path("server.py").read_text(encoding="utf-8")
    assert re.search(r"CREATE UNIQUE INDEX IF NOT EXISTS\s+idx_shared_memory_key\s*\n?\s*ON shared_memory\(key\)", src), \
        "hiányzik az egyedi index a shared_memory(key)-re"


def test_EGYETLEN_produkcios_irasi_ut_van():
    """⛔ A KÓDÚT-ŐR. Ha valaki új `INSERT INTO shared_memory`-t ír, az megint
    külön minta lesz — és a mérés szerint pont ebből lett három hibás."""
    talalat = []
    for f in list(Path(".").glob("*.py")) + list(Path("plugins").glob("*.py")) \
            + list(Path("feldwebel").glob("*.py")) + list(Path("vertical_plugins").glob("*.py")):
        szoveg = f.read_text(encoding="utf-8", errors="replace")
        for sor in re.findall(r"INSERT INTO shared_memory", szoveg):
            talalat.append(str(f))
    # Pontosan EGY: maga a közös helper a server.py-ban.
    assert talalat == ["server.py"], (
        f"a shared_memory írásának EGY gazdája van (`server.memoria_ir`) — "
        f"ezek megkerülik: {sorted(set(talalat))}")


def test_a_feldwebel_a_kozos_uton_ir():
    """A Feldwebel a BridgeContext-en át kapja az írást — nem saját SQL-lel."""
    src = Path("feldwebel/commands.py").read_text(encoding="utf-8")
    assert "ctx.memoria_ir(" in src, "a Feldwebel nem a közös utat hívja"
    assert "INSERT INTO shared_memory" not in src, "maradt saját SQL-je"
    from feldwebel import BridgeContext
    assert hasattr(BridgeContext, "__dataclass_fields__") and \
        "memoria_ir" in BridgeContext.__dataclass_fields__, \
        "a BridgeContext nem hordozza a közös írási utat"


def test_a_szerver_INJEKTALJA_a_feldwebelnek():
    """A mező megléte kevés: be is kell kötni — különben a Feldwebel
    `None`-t kap, és az e-mail-piszkozat megint némán elveszne."""
    src = Path("server.py").read_text(encoding="utf-8")
    assert "memoria_ir=memoria_ir," in src, \
        "a BridgeContext() hívásból hiányzik a memoria_ir injektálása"
