"""test_delphoi_en_nowcast.py — US/UK KIRAKAT-BŐVÍTÉS (Kommandant 2026-07-21).

A) EN nowcast-réteg: NOWCAST_QUESTIONS + REFERENCE_SETS_* 'en' készlet a hu
   minta szerkezeti párja (5 horgony, {entity} a regard-kérdésben).
B) Flip: keir-starmer / nigel-farage / Q22686 enabled=1 a seedben; a
   run_first_nowcast_uk_us szkript flip-je a meglévő (enabled=0) DB-sorokon is
   átbillent, idempotensen; már-láncolt entitást az első futás KIHAGY.
Korpusz-út: UK/US a lang='en' rétegből uk_/us_ source-prefixszel olvas —
kereszt-szivárgás nincs (a csak-uk_ hírkészlet a US-nowcastot blokkolja).
"""
import asyncio
import json
from datetime import datetime, timezone

import pytest

import run_first_nowcast_uk_us as first_run
from plugins import delphoi


@pytest.fixture
def en_db(get_db):
    """press_snapshots lang='en' NEWS-jellel (a UK/US konfig csak news-t olvas),
    CSAK uk_ forrás-prefixű cikkekkel — a US-ág üres-korpusz próbájához is."""
    conn = get_db()
    delphoi.ensure_tables(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS press_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_iso TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'hu',
            signal_type TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(date_iso, lang, signal_type))""")
    ts = datetime.now(timezone.utc).isoformat()
    news = {"articles": [
        {"title": "Starmer defends spending review in Commons clash", "source_id": "uk_bbc"},
        {"title": "Reform UK tops new poll as Tories slip further", "source_id": "uk_guardian"},
        {"title": "Bank of England holds rates amid sticky inflation", "source_id": "uk_ft"},
    ]}
    for d in ("2026-07-14", "2026-07-15"):
        conn.execute("INSERT INTO press_snapshots (date_iso, lang, signal_type, content, created_at) "
                     "VALUES (?,?,?,?,?)", (d, "en", "news", json.dumps(news), ts))
    delphoi.seed_registry(conn)
    conn.commit()
    conn.close()
    return get_db


def _fake_chat_capture(anchor_set, prompts):
    async def chat_fn(prompt):
        prompts.append(prompt)
        return anchor_set[3]  # "somewhat better" → survey_score=4.0 → +0.5
    return chat_fn


def _fake_embed(anchor_set):
    async def embed_fn(texts):
        out = []
        for t in texts:
            vec = [0.0] * 5
            vec[anchor_set.index(t) if t in anchor_set else 3] = 1.0
            out.append(vec)
        return out
    return embed_fn


# ── A) EN instrumentum-készlet ──────────────────────────────────────────────

def test_en_instruments_complete():
    assert len(delphoi.REFERENCE_SETS_REGARD["en"]) == 5
    assert len(delphoi.REFERENCE_SETS_GROWTH["en"]) == 5
    for kind in ("regard", "price", "growth"):
        assert ("en", kind) in delphoi.NOWCAST_QUESTIONS, f"nincs en/{kind} kérdés"
    assert "{entity}" in delphoi.NOWCAST_QUESTIONS[("en", "regard")]
    # az anchor-halmaz a hu szerkezeti párja: 5 fokozat, mind különböző mondat
    assert len(set(delphoi.REFERENCE_SETS_REGARD["en"])) == 5
    # ár-horgony: az ssr-készlet EN kulcsa él — az EXTRA nem duplikálja (némán
    # felülírná a _anchor_set elsőbbségi sorrendje miatt)
    assert "en" not in delphoi.REFERENCE_SETS_PRICE_EXTRA


def test_en_anchor_set_resolves_without_hu_fallback():
    from plugins import ssr
    assert delphoi._anchor_set("regard", "en") == delphoi.REFERENCE_SETS_REGARD["en"]
    assert delphoi._anchor_set("growth", "en") == delphoi.REFERENCE_SETS_GROWTH["en"]
    assert delphoi._anchor_set("price", "en") == ssr.REFERENCE_SETS_PRICE["EN"]


# A seed IGAZSÁGFORRÁSA a plugins/delphoi._SEED_ENTITIES. A tesztek korábban
# beégették a hármat (keir-starmer / nigel-farage / Q22686); amikor a
# Kommandant 2026-07-21-én felvette Andy Burnhamet, a seed nőtt, a tesztek
# nem — és mindhárom UK/US teszt elbukott, pedig a KÓD volt helyes.
# Ezért innentől a listát a forrásból SZÁRMAZTATJUK: egy ötödik entitás
# felvétele nem bukhat el itt.
def _seeded_en_keys() -> set[str]:
    return {
        row[0] for row in delphoi._SEED_ENTITIES
        if row[1] in ("UK", "US")
    }


# ── B) flip a seedben + UK életciklus + US prefix-izoláció ─────────────────

def test_uk_us_seed_enabled(en_db):
    conn = en_db()
    rows = {r["entity_key"]: r["enabled"] for r in conn.execute(
        "SELECT entity_key, enabled FROM delphoi_entity_nowcast "
        "WHERE country IN ('UK','US')")}
    conn.close()
    assert rows == {key: 1 for key in _seeded_en_keys()}


def test_uk_nowcast_lifecycle_uses_en_question(en_db):
    deps = {"get_db": en_db}
    anchors = delphoi.REFERENCE_SETS_REGARD["en"]
    prompts: list = []
    rep = asyncio.run(delphoi.run_entity_nowcast(
        deps, entity_key="keir-starmer", country="UK", n=8,
        chat_fn=_fake_chat_capture(anchors, prompts), embed_fn=_fake_embed(anchors)))
    assert rep["ok"] and rep["ran"] == 1
    r = rep["results"][0]
    assert r["ok"] and r["direction"] == pytest.approx(0.5, abs=1e-6)
    assert r["content_hash"] and r["corpus_hash"]
    # a persona az EN kérdést kapta, a stimulus a toldalék-mentes név
    assert prompts and "your opinion of: Keir Starmer" in prompts[0]
    assert "megítélése" not in prompts[0].split("A friss hírkörnyezet")[-1]
    # a korpuszba a uk_ hírcímek kerültek
    assert "Reform UK tops new poll" in prompts[0]


def test_us_corpus_prefix_isolation(en_db):
    """A DB-ben csak uk_ cikkek vannak → a US (us_ prefix) korpusza ÜRES, a
    Trump-nowcast nem fut, ledger-sor nem születik (nincs kereszt-szivárgás)."""
    deps = {"get_db": en_db}
    anchors = delphoi.REFERENCE_SETS_REGARD["en"]
    rep = asyncio.run(delphoi.run_entity_nowcast(
        deps, entity_key="Q22686", country="US", n=6,
        chat_fn=_fake_chat_capture(anchors, []), embed_fn=_fake_embed(anchors)))
    assert rep["results"][0]["ok"] is False
    assert "korpusz" in rep["results"][0]["error"]
    conn = en_db()
    n = conn.execute("SELECT COUNT(*) FROM delphoi_nowcast_ledger").fetchone()[0]
    conn.close()
    assert n == 0


# ── az egyszeri-futás szkript logikája ─────────────────────────────────────

def test_first_run_script_flip_idempotent(en_db):
    conn = en_db()
    # prod-szimuláció: a meglévő DB-sorok még enabled=0-val állnak
    conn.execute("UPDATE delphoi_entity_nowcast SET enabled=0 "
                 "WHERE entity_key IN ({})".format(
                     ",".join("'%s'" % k for k, _ in first_run.NEW_ENTITIES)))
    conn.commit()
    flipped = first_run.flip_enabled(conn)
    assert sorted(flipped) == sorted(k for k, _ in first_run.NEW_ENTITIES)
    assert first_run.flip_enabled(conn) == []          # idempotens
    enabled = {r["entity_key"] for r in conn.execute(
        "SELECT entity_key FROM delphoi_entity_nowcast WHERE enabled=1 "
        "AND country IN ('UK','US')")}
    conn.close()
    assert enabled == {k for k, _ in first_run.NEW_ENTITIES}


def test_first_run_script_skips_already_chained(en_db, capsys):
    """Ha az entitásnak már van ledger-sora, az első futás KIHAGYJA — a
    meglévő láncokat a szkript garantáltan nem érinti (LLM-hívás sincs)."""
    for key, country in first_run.NEW_ENTITIES:
        delphoi.append_ledger_row(en_db, key, country, "2026-W30", 0.1,
                                  "deadbeef", "test|model", "{}")
    results = asyncio.run(first_run.first_runs({"get_db": en_db}))
    assert results == []
    out = capsys.readouterr().out
    # Szintén a forrásból, nem beégetve: ahány entitást a szkript ismer,
    # annyi SKIP-nek kell lennie.
    assert out.count("SKIP") == len(first_run.NEW_ENTITIES)


def test_first_run_script_preflight_reports_corpus(en_db):
    pf = first_run.preflight(en_db)
    assert pf["en_layer_ok"] is True
    assert pf["entities"]["keir-starmer"]["corpus_ok"] is True     # uk_ hírek élnek
    assert pf["entities"]["Q22686"]["corpus_ok"] is False          # us_ forrás nincs
    assert pf["entities"]["nigel-farage"]["source_prefixes"] == ["uk_"]


def test_a_ket_entitas_lista_nem_csuszhat_szet():
    """`delphoi._SEED_ENTITIES` (UK/US része) és `first_run.NEW_ENTITIES` két
    külön lista, és egyeznie KELL: a seed viszi be a sorokat, a szkript
    flippeli őket enabled=1-re. Ha csak az egyikbe kerül be egy új entitás,
    az vagy sosem kap első futást, vagy egy nem létező sorra próbál flippelni
    — mindkettő némán.

    Ez a teszt egy VALÓDI leletből született: az `andy-burnham` felvétele
    (Kommandant, 2026-07-21) MINDKÉT listába bekerült, de a tesztek beégetve
    hármat vártak, és hetekig pirosan álltak. A robusztusság-próba során
    kiderült, hogy a két lista összekötése hiányzik."""
    assert _seeded_en_keys() == {key for key, _ in first_run.NEW_ENTITIES}
