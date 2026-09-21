"""CSV/TSV/XLSX → táblázat + `statisztika` + `python_futtatas` (2026-09-21).

    .venv/bin/python -m pytest test_youngereka_statisztika.py -q
"""
import json
import pathlib
import sqlite3

import youngereka_docs as docs
import youngereka_statisztika as yst

CSV = ("csoport;magassag;tomeg;szin\n"
       "kontroll;12,5;3,1;piros\ncontroll_typo;;;;\nkontroll;13,1;3,4;piros\nkontroll;11,9;2,9;kek\nkontroll;12,8;3,0;piros\n"
       "kezelt;15,2;4,1;kek\nkezelt;14,8;3,9;kek\nkezelt;15,9;4,4;piros\nkezelt;15,1;4,0;kek\nkezelt;14,6;3,8;kek\n")


def _tabla():
    return yst.tabla_szovegbol(CSV.encode("utf-8"), "novenyek.csv")


def test_csv_tizedesvesszo_es_pontosvesszo():
    t = _tabla()
    assert t["oszlopok"] == ["csoport", "magassag", "tomeg", "szin"]
    assert t["tipus"]["magassag"] == "szam" and t["tipus"]["szin"] == "kategoria"
    assert yst.szam("12,5") == 12.5 and yst.szam("1.234,56") == 1234.56 and yst.szam("1,234.56") == 1234.56
    s = yst.osszefoglalo(t)
    assert "TÁBLÁZAT: 10 adatsor × 4 oszlop" in s and "átlag=" in s and "kezelt (5)" in s


def test_utf16_es_tsv_es_process_upload():
    raw16 = CSV.replace(";", "\t").encode("utf-16")          # Excel „Unicode text"
    doc = docs.process_upload(raw16, "adat.tsv")
    assert doc["kind"] == "tabla" and doc["tabla"]["n"] == 10 and "Táblázat · 10 sor × 4 oszlop" == doc["label"]
    doc2 = docs.process_upload(CSV.encode("cp1250"), "adat.csv")
    assert doc2["kind"] == "tabla"


def test_process_upload_rossz_csv_beszedes():
    import pytest
    with pytest.raises(ValueError, match="nem olvasható"):
        docs.process_upload(b"csak egy sor", "x.csv")


def test_xlsx_tablazat(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Adat"
    ws.append(["csoport", "ertek"]); [ws.append(r) for r in (["a", 1.0], ["a", 1.2], ["b", 2.1], ["b", 2.3])]
    p = tmp_path / "t.xlsx"; wb.save(p)
    doc = docs.process_upload(p.read_bytes(), "t.xlsx")
    assert doc["kind"] == "tabla" and doc["tabla"]["n"] == 4 and doc["tabla"]["tipus"]["ertek"] == "szam"


def test_probak_ismert_eredmennyel():
    t = _tabla()
    r = yst.szamol(t, {"muvelet": "t_proba", "oszlop": "magassag", "csoport": "csoport"})
    assert r["p"] < 0.001 and abs(r["cohen_d"]) > 2 and "t-próba" in r["magyarazat"]
    r2 = yst.szamol(t, {"muvelet": "mann_whitney", "oszlop": "magassag", "csoport": "csoport"})
    assert r2["p"] < 0.05 and r2["U"] in (0.0, 25.0)
    r3 = yst.szamol(t, {"muvelet": "korrelacio", "oszlop": "magassag", "oszlop2": "tomeg"})
    assert r3["pearson_r"] > 0.95
    r4 = yst.szamol(t, {"muvelet": "regresszio", "oszlop": "magassag", "oszlop2": "tomeg"})
    assert r4["r2"] > 0.9 and r4["meredekseg"] > 0
    r5 = yst.szamol(t, {"muvelet": "khi_negyzet", "oszlop": "csoport", "oszlop2": "szin"})
    assert r5["kontingencia"] and r5["fisher"] is not None
    r6 = yst.szamol(t, {"muvelet": "anova", "oszlop": "magassag", "csoport": "csoport"})
    assert r6["p"] < 0.001 and r6["tukey_paros"]
    r7 = yst.szamol(t, {"muvelet": "leiras"})
    assert "TÁBLÁZAT" in r7["osszefoglalo"]
    hiba = yst.szamol(t, {"muvelet": "t_proba", "oszlop": "nincs", "csoport": "csoport"})
    assert "Nincs „nincs” nevű oszlop" in hiba["hiba"] and "magassag" in hiba["hiba"]


def test_futtat_csak_sajat_fajl(tmp_path):
    con = sqlite3.connect(str(tmp_path / "c.db")); con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE yr_chat_files (id TEXT PRIMARY KEY, instance TEXT, filename TEXT, kind TEXT, label TEXT, text TEXT, image_paths TEXT, created_at TEXT, tabla_json TEXT)")
    con.execute("INSERT INTO yr_chat_files VALUES ('f1','YoungeReka','n.csv','tabla','l','', '[]','x',?)", (json.dumps(_tabla()),))
    con.commit()
    ok = yst.futtat(con, "YoungeReka", {"file_id": "f1", "muvelet": "leiras", "oszlop": "magassag"})
    assert ok["leiro"]["n"] == 9 and ok["file"] == "n.csv"   # a typo-sor üres
    assert "hiba" in yst.futtat(con, "AnnaKatheder", {"file_id": "f1", "muvelet": "leiras"})


def test_python_futtatas_homokozo(tmp_path):
    con = sqlite3.connect(str(tmp_path / "c.db")); con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE yr_chat_files (id TEXT PRIMARY KEY, instance TEXT, filename TEXT, kind TEXT, label TEXT, text TEXT, image_paths TEXT, created_at TEXT, tabla_json TEXT)")
    con.execute("INSERT INTO yr_chat_files VALUES ('f1','YoungeReka','n.csv','tabla','l','', '[]','x',?)", (json.dumps(_tabla()),))
    con.commit()
    img = tmp_path / "img"; img.mkdir()
    kod = ("m = oszlop('magassag'); print('n=', len(m), 'atlag=', round(float(np.mean(m)), 3))\n"
           "k = [r['magassag'] for r in adat if r['csoport']=='kezelt' and r['magassag'] is not None]\n"
           "print('kezelt n=', len(k)); plt.hist(m); plt.title('magassag')\n")
    r = yst.python_futtat(con, "YoungeReka", {"kod": kod, "file_id": "f1"}, img)
    assert "n= 9" in r["stdout"] and "kezelt n= 5" in r["stdout"] and len(r["abrak"]) == 1
    assert (img / r["abrak"][0]).exists() and r["abrak"][0].startswith("YoungeReka_")
    # hálózat tiltva, hiba adatként
    r2 = yst.python_futtat(con, "YoungeReka", {"kod": "import socket; socket.socket()"}, img)
    assert "HIBA" in r2["stdout"] and "nincs hálózat" in r2["stdout"]
    # más instance fájlja → hiba
    assert "hiba" in yst.python_futtat(con, "AnnaKatheder", {"kod": "print(1)", "file_id": "f1"}, img)
    # időkorlát
    yst.PY_IDOKORLAT_S = 3
    r3 = yst.python_futtat(con, "YoungeReka", {"kod": "while True: pass"}, img)
    assert "másodperc" in r3["hiba"]
