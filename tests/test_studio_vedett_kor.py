"""Tudásbázis-tartalmú körben a Studio GÉPI műveletei mennek, csak az AI-lépései tiltottak (2026-11-23).

MIÉRT: a Kommandant a robot-tanulmányából diasort kért; a kör korábban a tudásbázist olvasta, és a teljes Studio tiltva
volt — a saját dokumentuma nem jutott el a saját Studiójába. A szabály célja (tudásbázis-tartalom ne menjen saját AI-jú
szolgáltatáshoz) a gépi műveleteknél nem sérül: ott semmilyen modell nem dolgozza fel.

Futtatás: ../../../.venv/bin/python -m pytest -q tests/test_studio_vedett_kor.py"""
import futar_kliens as fk


def _s(eszkoz, **p):
    return {"eszkoz": eszkoz, "parameterek": p}


def test_gepi_muveletek_mennek():
    for args in (_s("convert", file_id="f", target_format="docx"), _s("files", action="upload_link"),
                 _s("wordcraft", file_id="f"), _s("pptx_build", spec={"slides": []}), _s("extract", file_id="f"),
                 _s("validate_document", file_id="f"), _s("sugo")):
        assert not fk.sf_fele("studio", args), args


def test_ai_lepesek_tiltottak():
    for args in (_s("pptx_build", from_document="f"), _s("pptx_build", spec={"slides": []}, instructions="írd"),
                 _s("describe_image", file_id="f"), _s("edit_with_instruction", file_id="f", instruction="x"),
                 _s("facts", action="check"), _s("ocr_image", file_id="f"), _s("video", action="from_article"),
                 _s("validate_document", file_id="f", content=True), _s("ismeretlen_uj_eszkoz"), {}):
        assert fk.sf_fele("studio", args), args


def test_az_echolot_es_az_engine_tovabbra_is_tiltott():
    assert fk.sf_fele("echolot", {}) and fk.sf_fele("engine", {})
    assert fk.sf_fele("ai_query", {}) and fk.sf_fele("dontes_szimulator", {})
