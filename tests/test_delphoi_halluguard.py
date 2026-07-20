"""test_delphoi_halluguard.py — a hallucináció-őr heurisztika (PYTHIA P1).

Fixture-alapú: grounding-korpusz + persona-mondatok; a forráson TÚLI konkrétum
(szám/név/dátum) gyanúként jelenik meg, a korpuszban szereplő nem.
LLM-hívás nincs — tiszta függvények + log + heti összesítő.
"""
from plugins import delphoi_halluguard as hg

CORPUS = (
    "VILÁG: Feszült hét a nagypolitikában.\n"
    "- Uniós csúcs: vita a költségvetésről, a tét 120 milliárd euró.\n"
    "ITTHON: Magyar Péter beszédet mondott a parlamentben 2026-07-15-én.\n"
    "Felkapott témák: költségvetés, uniós csúcs"
)
PRIMING = "AKTUÁLIS HELYZET (2026): a TISZA Párt kormányoz."


def test_extract_claims_types():
    c = hg.extract_claims("Szerintem Orbán Viktor 45%-ot mondott 2027-ben Kovács Bélának.")
    assert "45%" in c["numbers"]
    assert "2027" in c["dates"]
    assert "Orbán Viktor" in c["names"] or "Viktor" in c["names"]
    assert any("Kovács" in n for n in c["names"])


def test_sentence_initial_capital_is_not_a_name():
    c = hg.extract_claims("Szerintem ez jó. Fontos a béke.")
    assert not c["names"]


def test_grounded_content_is_not_flagged():
    texts = [
        "A költségvetés vitája miatt kicsit romlott a véleményem.",
        "Magyar Péter beszéde meggyőző volt, a 120 milliárd euró sok.",
    ]
    rep = hg.scan_reactions(texts, CORPUS, PRIMING)
    assert rep["n_texts"] == 2
    assert rep["n_suspect_texts"] == 0
    assert rep["suspects"] == []


def test_beyond_source_concretes_are_flagged():
    texts = [
        # 87% és 2031 nincs a korpuszban; Nagy Elemér sem
        "Úgy tudom, 87% támogatja, és 2031-re ígérték, Nagy Elemér mondta.",
        "A költségvetés vitája érdekel.",   # grounded
    ]
    rep = hg.scan_reactions(texts, CORPUS, PRIMING)
    assert rep["n_suspect_texts"] == 1
    claims = {s["claim"] for s in rep["suspects"]}
    assert "87%" in claims
    assert "2031" in claims
    assert any("Elemér" in c or "Nagy" in c for c in claims)


def test_inflected_grounded_name_not_flagged():
    """Ragozott alak (Péterrel) a nyers stem-illesztéssel groundoltnak számít."""
    rep = hg.scan_reactions(["Egyetértek Magyar Péterrel."], CORPUS, PRIMING)
    assert rep["suspects"] == []


def test_repeated_suspect_counts_aggregate():
    texts = ["Állítólag 87% támogatja.", "Igen, 87% — ezt hallottam én is."]
    rep = hg.scan_reactions(texts, CORPUS, PRIMING)
    top = rep["suspects"][0]
    assert top["claim"] == "87%"
    assert top["count"] == 2
    assert rep["n_suspect_texts"] == 2


def test_log_flags_and_weekly_summary(get_db):
    rep = hg.scan_reactions(["Állítólag 87% támogatja Kiss Ödönt."], CORPUS, PRIMING)
    hg.log_flags(get_db, "nowcast", "Q124488292", "HU", "chash", rep)
    hg.log_flags(get_db, "nowcast", "Q387006", "HU", "chash2",
                 {"n_texts": 5, "n_suspect_texts": 0, "suspects": []})
    summary = hg.weekly_summary(get_db, days=7)
    assert summary["runs"] == 2
    assert summary["per_entity"]["Q124488292/HU"]["suspect_texts"] == 1
    assert summary["per_entity"]["Q387006/HU"]["suspect_texts"] == 0
    assert any(s["claim"] == "87%" for s in summary["top_suspects"])


def test_weekly_summary_empty_db(get_db):
    summary = hg.weekly_summary(get_db)
    assert summary["runs"] == 0
    assert summary["top_suspects"] == []
