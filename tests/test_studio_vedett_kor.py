"""Tudásbázis-tartalmú körben a Studio (és minden saját eszköz) használható (2026-11-23).

ALAPELV (a Kommandant): „az eszközeink … általánosan elérhetők minden agentünknek … Olyan eszközöket nem adunk a
kínaiak kezébe, amelyek RÓLAM, kifejezetten egyéni dolgokat használnak (lásd a tudásbázisom) … azért építjük az
eszközöket, hogy használjuk.” Csak a kínai ÜGYNÖKHÖZ adás (ai_query, feladat, recept, Bridge-üzenet) tiltott.

Futtatás: ../../../.venv/bin/python -m pytest -q tests/test_studio_vedett_kor.py"""
import futar_kliens as fk


def _s(eszkoz, **p):
    return {"eszkoz": eszkoz, "parameterek": p}


def test_a_studio_minden_lepese_megy():
    for args in (_s("convert", file_id="f", target_format="docx"), _s("wordcraft", file_id="f"),
                 _s("pptx_build", spec={"slides": []}), _s("pptx_build", from_document="f"),
                 _s("edit_with_instruction", file_id="f", instruction="x"), _s("describe_image", file_id="f")):
        assert not fk.sf_fele("studio", args), args


def test_a_kinai_ugynokhoz_adas_tovabbra_is_tiltott():
    for nev in ("ai_query", "ai_task", "create_task", "execute_recipe", "create_recipe", "schedule_recipe",
                "send_message"):
        assert fk.sf_fele(nev, {}), nev
    assert fk.sf_fele("agora", {"action": "comment"}) and fk.sf_fele("story_comments", {"body": "x"})
