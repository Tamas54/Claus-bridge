"""Diasor a Studióban, a diák szövegét az ügynök írja (2026-11-23).

MIÉRT: a Kommandant a szerdai bemutatót a Bridge-re bízta; a feladat-ügynök csak szöveges diatervet tudott adni, mert
a teljes Studio tiltott neki (van benne saját AI-jú lépés). A `studio_diasor` a `pptx_build`-et CSAK `spec`-kel hívja:
a `from_document` / `instructions` út (ahol a Studio AI-ja írna) sosem megy ki.

Futtatás: ../../../.venv/bin/python -m pytest -q tests/test_studio_diasor.py"""
import asyncio
import json

import feladat_ugynok as fu
import nachtschicht as ns


def _fut(c):
    return asyncio.run(c)


class _Forras:
    def __init__(self, valasz=None):
        self.hivasok = []
        self.valasz = valasz or {"file_id": "f-1", "name": "Robot.pptx", "edit_url": "https://studio/e/f-1",
                                 "preview_url": "https://studio/p/f-1", "check": {"status": "ok", "findings": []}}

    async def studio(self, eszkoz, parameterek, modell):
        self.hivasok.append((eszkoz, parameterek))
        return json.dumps(self.valasz)


def _spec(n=5, **extra):
    diak = [{"layout": "cover", "title": "Mire a robot megérkezik"}] + \
        [{"layout": "bullets", "title": f"{i}. gondolat", "bullets": ["egy", "kettő"], "notes": "forrás: [1]"}
         for i in range(n - 2)] + [{"layout": "closing", "title": "Köszönöm"}]
    return {"title": "Robot", "slides": diak, **extra}


def test_spec_megy_ki_es_link_jon_vissza():
    f = _Forras()
    r = _fut(ns.studio_diasor(f, "Robot", _spec()))
    assert r["edit_url"] == "https://studio/e/f-1" and r["check"] == "ok" and r["diak"] == 5
    eszkoz, par = f.hivasok[0]
    assert eszkoz == "pptx_build" and par["spec"]["slides"][0]["layout"] == "cover"


def test_from_document_es_instructions_sosem_megy_ki():
    f = _Forras()
    _fut(ns.studio_diasor(f, "Robot", _spec(from_document="doc-9", instructions="írd meg te", template_file_id="t-1")))
    _, par = f.hivasok[0]
    assert "from_document" not in par and "instructions" not in par
    assert set(par["spec"]) <= {"title", "footer", "brand", "theme", "slides"}


def test_rossz_diaszam_es_ismeretlen_tipus_nem_hiv():
    f = _Forras()
    assert "error" in _fut(ns.studio_diasor(f, "x", _spec(n=3)))
    assert "error" in _fut(ns.studio_diasor(f, "x", {"slides": [{"layout": "cover"}] * 31}))
    assert "error" in _fut(ns.studio_diasor(f, "x", {"slides": [{"layout": "hologram"}] * 5}))
    assert "error" in _fut(ns.studio_diasor(f, "x", "nem objektum"))
    assert f.hivasok == []


def test_a_studio_hibaja_kimondva():
    f = _Forras(valasz={"error": "insufficient_credits"})
    r = _fut(ns.studio_diasor(f, "Robot", _spec()))
    assert "error" in r and "insufficient_credits" in r["error"]


def test_a_feladat_ugynok_hasznalhatja_de_a_teljes_studio_tiltott_marad():
    assert fu.engedett("studio_diasor") is None
    assert fu.engedett("studio") is not None
    assert any(d["function"]["name"] == "studio_diasor" for d in ns.SAJAT_DEFS)


def test_vegrehajto_a_termesbe_teszi():
    f = _Forras()
    t = ns.Termes()
    v = ns.vegrehajto(f, t)
    ki = json.loads(_fut(v("studio_diasor", {"cim": "Robot", "spec": _spec()})))
    assert ki["edit_url"] and t.dokumentumok and t.dokumentumok[0]["file_id"] == "f-1"


def test_a_nyers_pptx_build_is_csak_spec_kel_mehet():
    """A kínai modellek Studio-útja (server._sf_kulso_elutasitas) ugyanezt a kaput használja."""
    assert ns.studio_engedett("pptx_build", {"spec": {"slides": []}}) is None
    assert ns.studio_engedett("pptx_build", {"from_document": "doc-1"}) is not None
    assert ns.studio_engedett("pptx_build", {"spec": {"slides": []}, "instructions": "írd meg"}) is not None
    assert ns.studio_engedett("pptx_build", {}) is not None
