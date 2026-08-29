"""
S-006 — A JELENLÉT A MUNKA MELLÉKHATÁSA, NEM KÜLÖN BEJELENTÉS.

A hiba: a `get_status` `cli-claus last_seen 2026-08-16`-ot mutatott, miközben
a cli-claus AZNAP 15 memória-bejegyzést írt. A `heartbeat` toolt senki nem
hívja — a „ki van online" nézet két hetet tévedett működés közben.

Amit ezek a tesztek őriznek:
  1. bármelyik tool-hívás jelenlétté válik (egy fogóhelyen, nem 89-en),
  2. a fojtás tényleg fojt (nem lesz írás minden hívásra),
  3. az explicit `heartbeat` session_info-ja NEM vész el,
  4. ismeretlen azonosító nem szennyezi a nézetet,
  5. a jelenlét SOSEM töri el a tool-hívást,
  6. nincs néma elcsúszás: új azonosító-paraméter-név esetén pirosra vált.
"""

import asyncio
import re
import sqlite3

import pytest

import presence


_HEARTBEATS_DDL = """
CREATE TABLE IF NOT EXISTS heartbeats (
    instance TEXT PRIMARY KEY,
    last_seen TEXT NOT NULL,
    session_info TEXT DEFAULT ''
);
"""


@pytest.fixture(autouse=True)
def _clean_throttle():
    presence.reset_throttle()
    yield
    presence.reset_throttle()


@pytest.fixture
def hb_db(tmp_path):
    """A PROD séma szerinti heartbeats tábla (még oszlop-migráció NÉLKÜL)."""
    path = str(tmp_path / "hb.db")
    conn = sqlite3.connect(path)
    conn.executescript(_HEARTBEATS_DDL)
    conn.commit()
    conn.close()

    def _get_db():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    return _get_db


def _row(get_db, instance):
    conn = get_db()
    try:
        return conn.execute(
            "SELECT * FROM heartbeats WHERE instance = ?", (instance,)
        ).fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. AZONOSÍTÓ KINYERÉSE
# ---------------------------------------------------------------------------

def test_az_azonosito_barmelyik_bevett_parameterbol_jon():
    assert presence.caller_from_arguments({"caller": "cli-claus"}) == "cli-claus"
    assert presence.caller_from_arguments({"instance": "cli-claus"}) == "cli-claus"
    assert presence.caller_from_arguments({"sender": "web-claus"}) == "web-claus"
    assert presence.caller_from_arguments({"assigned_by": "kommandant"}) == "kommandant"
    assert presence.caller_from_arguments({"uploaded_by": "kommandant"}) == "kommandant"


def test_a_nem_azonositok_nem_szamitanak_jelenletnek():
    """Sok tool alapértéke `""` vagy `"unknown"` — ezekből nem lehet jelenlét."""
    for args in ({}, {"caller": ""}, {"caller": "   "}, {"instance": "unknown"},
                 {"caller": "None"}, {"caller": 42}, None, "nem is dict"):
        assert presence.caller_from_arguments(args) == ""


def test_a_prioritas_a_caller_e():
    assert presence.caller_from_arguments(
        {"sender": "web-claus", "caller": "cli-claus"}) == "cli-claus"


# ---------------------------------------------------------------------------
# 2. KI KAPHAT JELENLÉTET
# ---------------------------------------------------------------------------

def test_a_core_instance_ismert(hb_db):
    assert presence.is_known_instance(hb_db, "cli-claus")
    assert presence.is_known_instance(hb_db, "web-claus")


def test_az_ismeretlen_azonosito_nem_szennyezi_a_nezetet(hb_db):
    assert presence.is_known_instance(hb_db, "asdf-random") is False
    assert presence.touch_from_tool_call(hb_db, "get_status", {"caller": "asdf-random"}) is False
    assert _row(hb_db, "asdf-random") is None


def test_aki_egyszer_mar_bejelentkezett_az_ismert(hb_db):
    """A `heartbeat` a regisztráció; onnantól a MUNKA tartja életben."""
    conn = hb_db()
    conn.execute("INSERT INTO heartbeats (instance, last_seen, session_info) "
                 "VALUES ('siabot', '2026-08-01T00:00:00+00:00', 'régi')")
    conn.commit()
    conn.close()
    assert presence.is_known_instance(hb_db, "siabot")
    assert presence.touch_from_tool_call(hb_db, "search_memory", {"caller": "siabot"}) is True


# ---------------------------------------------------------------------------
# 3. AZ ÍRÁS
# ---------------------------------------------------------------------------

def test_a_tool_hivas_jelenletet_ir(hb_db):
    """A LÉNYEG: nem kell külön bejelenteni, elég DOLGOZNI."""
    assert _row(hb_db, "cli-claus") is None
    assert presence.touch_from_tool_call(hb_db, "write_memory", {"instance": "cli-claus"}) is True

    row = _row(hb_db, "cli-claus")
    assert row is not None
    assert row["last_seen"].startswith("20")
    assert row["last_activity_source"] == "tool:write_memory"


def test_a_session_info_nem_vesz_el(hb_db):
    """A származtatott jelenlét CSAK a last_seen-t frissíti.

    Az explicit heartbeat `INSERT OR REPLACE`-szel ír session_info-t; ha a
    következő tool-hívás azt letörölné, a javítás adatot rombolna.
    """
    conn = hb_db()
    conn.execute("INSERT INTO heartbeats (instance, last_seen, session_info) "
                 "VALUES ('cli-claus', '2026-08-16T10:00:00+00:00', 'S-002 javítás')")
    conn.commit()
    conn.close()

    presence.touch(hb_db, "cli-claus", source="tool:write_memory")

    row = _row(hb_db, "cli-claus")
    assert row["session_info"] == "S-002 javítás"
    assert row["last_seen"] > "2026-08-16T10:00:00+00:00"


def test_a_fojtas_tenyleg_fojt(hb_db):
    """Írás-erősítés ellen: 20 hívás egy percen belül = 1 írás."""
    clock = [1000.0]
    written = sum(
        presence.touch(hb_db, "cli-claus", monotonic=lambda: clock[0])
        for _ in range(20)
    )
    assert written == 1


def test_a_fojtas_ablaka_lejar(hb_db, monkeypatch):
    monkeypatch.setenv(presence.THROTTLE_ENV, "60")
    clock = [1000.0]
    assert presence.touch(hb_db, "cli-claus", monotonic=lambda: clock[0]) is True
    clock[0] += 59
    assert presence.touch(hb_db, "cli-claus", monotonic=lambda: clock[0]) is False
    clock[0] += 2
    assert presence.touch(hb_db, "cli-claus", monotonic=lambda: clock[0]) is True


def test_a_fojtas_instance_onkent_kulon(hb_db):
    clock = [1000.0]
    assert presence.touch(hb_db, "cli-claus", monotonic=lambda: clock[0]) is True
    assert presence.touch(hb_db, "web-claus", monotonic=lambda: clock[0]) is True


def test_a_hibas_iras_nem_nyeli_el_a_kovetkezo_percet(hb_db):
    """Tranziens DB-hiba után a következő hívás ÚJRA próbálhat."""
    def _broken():
        raise sqlite3.OperationalError("disk I/O error")

    clock = [1000.0]
    assert presence.touch(_broken, "cli-claus", monotonic=lambda: clock[0]) is False
    # ugyanabban a fojtás-ablakban, de működő DB-vel: mégis ír
    assert presence.touch(hb_db, "cli-claus", monotonic=lambda: clock[0]) is True


def test_a_jelenlet_sosem_dob():
    def _broken():
        raise RuntimeError("nincs DB")

    assert presence.touch_from_tool_call(_broken, "get_status", {"caller": "cli-claus"}) is False


# ---------------------------------------------------------------------------
# 4. SÉMA (additív migráció)
# ---------------------------------------------------------------------------

def test_az_oszlop_migracio_idempotens_es_additiv(hb_db):
    conn = hb_db()
    cols_before = {r[1] for r in conn.execute("PRAGMA table_info(heartbeats)")}
    assert "last_activity_source" not in cols_before

    assert presence.ensure_schema(conn) is True
    assert presence.ensure_schema(conn) is True  # kétszer is szabad

    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(heartbeats)")}
    conn.close()
    # A régi oszlopok MEGMARADNAK — a meglévő olvasók szerződése változatlan.
    assert cols_before < cols_after
    assert cols_after - cols_before == {"last_activity_source"}


def test_a_migralatlan_db_n_is_ir(tmp_path):
    """Ha a migráció valamiért nem megy át, a last_seen akkor is frissül."""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(_HEARTBEATS_DDL)
    conn.commit()
    conn.close()

    def _get_db():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    # ensure_schema-t elrontjuk: "nincs oszlop, és nem is lehet"
    orig = presence.ensure_schema
    presence.ensure_schema = lambda conn: False
    try:
        assert presence.touch(_get_db, "cli-claus") is True
    finally:
        presence.ensure_schema = orig

    assert _row(_get_db, "cli-claus") is not None


# ---------------------------------------------------------------------------
# 5. A MIDDLEWARE
# ---------------------------------------------------------------------------

class _Msg:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _Ctx:
    def __init__(self, name, arguments):
        self.message = _Msg(name, arguments)


def test_a_middleware_minden_hivast_jelenletté_tesz(hb_db):
    mw = presence.build_middleware(hb_db)

    async def _call_next(ctx):
        return "eredeti eredmény"

    out = asyncio.run(mw.on_call_tool(_Ctx("search_memory", {"caller": "cli-claus"}), _call_next))

    assert out == "eredeti eredmény"          # a hívás eredménye érintetlen
    assert _row(hb_db, "cli-claus") is not None


def test_a_middleware_nem_torheti_el_a_hivast():
    """Egy elhasalt jelenlét-írás NEM ölheti meg a tool-hívást."""
    def _broken():
        raise RuntimeError("nincs DB")

    mw = presence.build_middleware(_broken)

    async def _call_next(ctx):
        return "megvan"

    assert asyncio.run(mw.on_call_tool(_Ctx("get_status", {"caller": "cli-claus"}), _call_next)) == "megvan"
    # rossz alakú context sem törhet el semmit
    assert asyncio.run(mw.on_call_tool(object(), _call_next)) == "megvan"


def test_a_middleware_atengedi_a_hivas_hibajat(hb_db):
    """A jelenlét nem nyelheti el a tool VALÓDI hibáját."""
    mw = presence.build_middleware(hb_db)

    async def _call_next(ctx):
        raise ValueError("a tool hibázott")

    with pytest.raises(ValueError):
        asyncio.run(mw.on_call_tool(_Ctx("x", {"caller": "cli-claus"}), _call_next))


# ---------------------------------------------------------------------------
# 6. ELCSÚSZÁS-ŐR — új azonosító-paraméter ne maradjon némán kimaradva
# ---------------------------------------------------------------------------

_TOOL_RE = re.compile(r"@mcp\.tool\(\)\s*\n(?:@[^\n]*\n)*async def (\w+)\(([^)]*)\)", re.S)

#: Azonosító-GYANÚS paraméternevek. SZÉLESEBB, mint az IDENTITY_ARGS —
#: különben a teszt körkörös lenne. Ha egy új tool ezek közül olyat használ,
#: ami nincs az IDENTITY_ARGS-ban, a jelenlét NÉMÁN kimaradna rá.
_IDENTITY_SUSPECTS = {
    "caller", "instance", "sender", "assigned_by", "created_by", "requested_by",
    "uploaded_by", "author", "user", "who", "actor", "instance_id", "agent",
    "from_instance", "owner", "by",
}


def _server_tool_signatures():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "server.py"), encoding="utf-8").read()
    out = {}
    for name, params in _TOOL_RE.findall(src):
        pnames = set()
        for part in params.split(","):
            part = part.strip()
            m = re.match(r"(\w+)\s*[:=]", part)
            if m:
                pnames.add(m.group(1))
            elif re.match(r"^\w+$", part):
                pnames.add(part)
        out[name] = pnames
    return out


def test_a_meroeszkoz_talal_toolokat():
    """A parse-oló maga is hitelesítve — üres lista mindent zölden hagyna."""
    sigs = _server_tool_signatures()
    assert len(sigs) > 50, f"csak {len(sigs)} toolt talált a parser — a regex romlott el"
    assert "get_status" in sigs and "caller" in sigs["get_status"]


def test_nincs_lefedetlen_azonosito_parameter():
    """Ha valaki `author=`-t vagy `who=`-t vezet be, ez a teszt pirosra vált."""
    covered = set(presence.IDENTITY_ARGS)
    offenders = {}
    for name, pnames in _server_tool_signatures().items():
        extra = (pnames & _IDENTITY_SUSPECTS) - covered
        if extra:
            offenders[name] = sorted(extra)
    assert not offenders, (
        "azonosító-gyanús paraméter az IDENTITY_ARGS-on kívül — a jelenlét "
        f"némán kimaradna rájuk: {offenders}"
    )


def test_minden_server_tool_attributalhato():
    """2026-08-29-én mind a 61 `@mcp.tool()` visel azonosító-paramétert.

    Ha egy új tool egyet sem visel, nem tudjuk kihez kötni a hívást — a
    jelenlét rá vak lesz. Ez nem tiltás, hanem figyelmeztetés: vagy kap
    `caller`-t (amit a permission-ellenőrzés úgyis kér), vagy tudatos kivétel.
    """
    covered = set(presence.IDENTITY_ARGS)
    blind = sorted(n for n, p in _server_tool_signatures().items() if not (p & covered))
    assert blind == [], f"azonosító-paraméter nélküli tool(ok): {blind}"
