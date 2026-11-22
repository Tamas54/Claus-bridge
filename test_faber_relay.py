"""Faber-továbbítás: aláírt 'faber' üzenet a messages táblába; hamisítás és rossz gomb elutasítva (2026-11-22).

    .venv/bin/python -m pytest test_faber_relay.py -q
"""
import json
import sqlite3

import faber_relay as f


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, sender TEXT, recipient TEXT, "
                "subject TEXT, message TEXT, priority TEXT, thread_id INTEGER, reply_to INTEGER, status TEXT)")
    return con


def test_parancs_es_gomb_felismeres():
    assert f.parancs_e("/tanulj olvasd be a ChatGPT-exportot") and f.parancs_e("  /FABER") and f.parancs_e("/vissza 3")
    assert not f.parancs_e("Élesítsd") and not f.parancs_e("/brief")
    assert f.gomb_e("mehet:12:Ab3dE9xY") and f.gomb_e("megse:1:abcdef")
    assert not f.gomb_e("mehet:12") and not f.gomb_e("torol:1:abcdef") and not f.gomb_e("mehet:1:ab")


def test_tovabbitas_alairva_es_a_faber_elfogadja():
    con = _con()
    mid = f.tovabbit(con, "123:TOKEN", "gomb", "mehet:1:Ab3dE9xY", "2026-11-22T11:30:00")
    sor = con.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    assert sor["recipient"] == "faber" and sor["thread_id"] == mid
    torzs = f.ellenoriz(f.kulcs("123:TOKEN"), sor["message"])
    assert torzs == {"tipus": "gomb", "adat": "mehet:1:Ab3dE9xY", "ts": "2026-11-22T11:30:00"}


def test_hamisitas_elutasitva():
    con = _con()
    mid = f.tovabbit(con, "123:TOKEN", "parancs", "/tanulj valami", "2026-11-22T11:31:00")
    uz = con.execute("SELECT message FROM messages WHERE id=?", (mid,)).fetchone()["message"]
    assert f.ellenoriz(f.kulcs("MAS:TOKEN"), uz) is None                        # más kulccsal nem jó
    d = json.loads(uz)
    d["torzs"] = d["torzs"].replace("valami", "rm -rf")                          # átírt törzs, régi aláírás
    assert f.ellenoriz(f.kulcs("123:TOKEN"), json.dumps(d)) is None
    assert f.ellenoriz(f.kulcs("123:TOKEN"), '{"torzs": "x"}') is None            # aláírás nélkül
    assert f.ellenoriz(f.kulcs("123:TOKEN"), "nem json") is None


def test_token_nelkul_nincs_tovabbitas():
    assert f.tovabbit(_con(), "", "parancs", "/faber", "2026-11-22T11:32:00") is None
