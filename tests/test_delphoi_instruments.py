"""test_delphoi_instruments.py — a 4 instrumentum aggregálása (D3):
plurality vs SSR a HELYES helyen (versengő→plurality, attitűd→SSR),
kötelező baseline minden riportban, pitch kvalitatív blokk aggregáltan."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from plugins import delphoi


@pytest.fixture
def fg_db(get_db, monkeypatch):
    monkeypatch.setenv("DELPHOI_FG_CACHE", "0")
    conn = get_db()
    delphoi.ensure_fg_tables(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS press_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_iso TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'hu',
            signal_type TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(date_iso, lang, signal_type))""")
    conn.execute(
        "INSERT INTO press_snapshots (date_iso, lang, signal_type, content, created_at) VALUES (?,?,?,?,?)",
        ("2026-07-08", "hu", "brief", json.dumps({"lead": "Hír.", "topics": []}),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return get_db


_ANCH = delphoi.REFERENCE_SETS_APPEAL["hu"]


async def _embed(texts):
    return [[1.0 if i == (_ANCH.index(t) if t in _ANCH else 2) else 0.0
             for i in range(5)] for t in texts]


def _run(fg_db, user, kind, text, variants=None, chat_fn=None):
    delphoi.add_credits(fg_db, user, 20, f"seed-{user}")
    created = delphoi.create_job(fg_db, user, kind, text,
                                 {"country": "HU", "n_per_cell": 30}, variants)
    assert created["ok"], created
    rep = asyncio.run(delphoi.process_job({"get_db": fg_db}, created["job_id"],
                                          chat_fn=chat_fn, embed_fn=_embed))
    assert rep["ok"], rep
    return rep["result"]


def test_product_desc_ssr_with_baseline_and_segments(fg_db):
    async def chat(prompt):
        return _ANCH[4]  # mindenki: "nagyon vonzó"
    res = _run(fg_db, "user:t1", "product_desc", "Új fogkrém", chat_fn=chat)
    assert res["overall_score"] == pytest.approx(5.0, abs=1e-6)
    assert res["baseline"]["value"] == 3.0                       # kötelező null-modell
    assert res["segments"] and all("vs_baseline" in s for s in res["segments"])
    assert all(s["vs_baseline"] == pytest.approx(2.0, abs=1e-6) for s in res["segments"])


def test_ab_test_plurality_primary_ssr_secondary(fg_db):
    """Versengő döntés → a plurality (választás-arány) az elsődleges metrika."""
    async def chat(prompt):
        if "A-szöveg" in prompt:
            return f"REAKCIÓ: {_ANCH[3]}\nVÁLASZTÁS: igen"
        return f"REAKCIÓ: {_ANCH[1]}\nVÁLASZTÁS: nem"
    res = _run(fg_db, "user:t2", "ab_test", "", ["A-szöveg ajánlat", "B-szöveg ajánlat"],
               chat_fn=chat)
    assert res["baseline"]["value"] == 0.5                       # véletlen szint
    v = {x["variant"]: x for x in res["variants"]}
    assert v["V1"]["choice_rate"] == 1.0 and v["V2"]["choice_rate"] == 0.0
    assert res["variants"][0]["variant"] == "V1"                 # plurality-rangsor
    assert v["V1"]["ssr_mean"] > v["V2"]["ssr_mean"]             # SSR másodlagos jel


def test_yt_title_plurality_with_random_baseline(fg_db):
    """Egy-a-sokból kattintás → plurality; baseline = 1/k."""
    async def chat(prompt):
        return "VÁLASZTÁS: 2\nINDOK: ez a legérdekesebb"
    titles = ["Unalmas cím", "Ütős cím", "Harmadik cím"]
    res = _run(fg_db, "user:t3", "yt_title", "", titles, chat_fn=chat)
    assert res["baseline"]["value"] == pytest.approx(1 / 3, abs=1e-3)
    assert res["ranking"][0]["variant"] == "Ütős cím"
    assert res["ranking"][0]["share"] == 1.0
    assert res["ranking"][0]["vs_baseline"] == pytest.approx(2 / 3, abs=1e-3)


def test_yt_title_viewer_mix_in_panel(fg_db):
    """A yt_title panel niche-illesztett néző-mixet kap (50/35/15)."""
    seen = []
    async def chat(prompt):
        seen.append(prompt)
        return "VÁLASZTÁS: 1\nINDOK: ok"
    _run(fg_db, "user:t4", "yt_title", "", ["Cím A", "Cím B"], chat_fn=chat)
    joined = "\n".join(seen)
    for viewer_type, _w in delphoi.YT_VIEWER_MIX:
        assert viewer_type in joined, f"hiányzó nézőtípus a panelből: {viewer_type}"


def test_pitch_resonance_plus_qualitative_block(fg_db):
    """Pitch: rezonancia (SSR) + aggregált 'mi maradt homályos' blokk —
    nyers mondat nélkül."""
    state = {"i": 0}
    async def chat(prompt):
        state["i"] += 1
        if state["i"] % 2 == 0:
            return f"REAKCIÓ: {_ANCH[3]}\nHOMÁLYOS: árazás"
        return f"REAKCIÓ: {_ANCH[3]}\nHOMÁLYOS: -"
    res = _run(fg_db, "user:t5", "pitch", "Befektetői pitch szövege", chat_fn=chat)
    assert res["overall_score"] == pytest.approx(4.0, abs=1e-6)
    assert res["clear_ratio"] == pytest.approx(0.5, abs=0.05)
    assert res["unclear_top"] and res["unclear_top"][0]["kifejezes"] == "árazás"
    # az aggregátumban nincs nyers persona-mondat
    assert _ANCH[3] not in json.dumps(res, ensure_ascii=False)
