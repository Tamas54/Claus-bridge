"""Sérült .docx (be nem jegyzett kép) szövege is olvasható (2026-11-23).

MIÉRT: a Kommandant robot-tanulmányát (v7, Gmail-melléklet) a Bridge nem tudta elolvasni: a python-docx elutasította,
mert egy kép nincs a [Content_Types].xml-ben. A Word megnyitja. A szöveg a zipből kiolvasható.

Futtatás: ../../../.venv/bin/python -m pytest -q tests/test_docx_tartalek.py"""
import pathlib
import zipfile

import server

DOC = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
       'wordprocessingml/2006/main"><w:body>'
       '<w:p><w:r><w:t>Mire a robot megérkezik</w:t></w:r></w:p>'
       '<w:p><w:r><w:t xml:space="preserve">Első </w:t></w:r><w:r><w:t>bekezdés &amp; szám: 550 $</w:t></w:r></w:p>'
       '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cella</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
       '<w:p><w:r><w:t>név</w:t></w:r><w:r><w:tab/><w:t>érték</w:t></w:r><w:r><w:br/><w:t>új sor</w:t></w:r></w:p>'
       '</w:body></w:document>')
TIPUSOK = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
           'content-types"><Default Extension="xml" ContentType="application/xml"/>'
           '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
           'wordprocessingml.document.main+xml"/></Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>')


def _serult_docx(ut: pathlib.Path) -> pathlib.Path:
    with zipfile.ZipFile(ut, "w") as z:
        z.writestr("[Content_Types].xml", TIPUSOK)        # a .jpg nincs bejegyezve
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", DOC)
        z.writestr("word/media/photo_baines.jpg", b"\xff\xd8\xff")
    return ut


def test_a_serult_docx_szovege_a_zipbol_jon(tmp_path):
    szoveg = server._parse_file_to_text(_serult_docx(tmp_path / "v7.docx"), "application/octet-stream")
    assert "Mire a robot megérkezik" in szoveg
    assert "Első bekezdés & szám: 550 $" in szoveg
    assert "cella" in szoveg
    assert "Parse error" not in szoveg


def test_a_zip_tartalek_kozvetlenul(tmp_path):
    sorok = server._docx_szoveg_zipbol(_serult_docx(tmp_path / "v.docx")).splitlines()
    assert sorok[0] == "Mire a robot megérkezik"


def test_a_tabulator_es_a_sortores_megmarad(tmp_path):
    """11-23: a `<w:tab/>` és a `<w:br/>` a `<w:t>`-n kívül áll — eddig a szöveggyűjtés elhagyta."""
    szoveg = server._docx_szoveg_zipbol(_serult_docx(tmp_path / "v.docx"))
    assert "név\térték\núj sor" in szoveg
