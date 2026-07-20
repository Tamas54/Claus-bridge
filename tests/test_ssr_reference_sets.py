"""Tests for the G0d (PYTHIA) multilingual anchor sets in plugins/ssr.py.

Szerkezet-teljesség nyelvkészletenként: minden kanonikus kategória megvan,
min. 5 mondat, nincs duplikátum, nincs üres — plusz zéró-regressziós őrök a
backtest-szkriptek által hivatkozott kulcsokra. Fut pytesttel ÉS scriptként:
    python3 tests/test_ssr_reference_sets.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import plugins.ssr as ssr

MIN_SENTENCES = 5
TARGET_LANGS = ("fr", "es", "de", "pl", "it", "cs", "en")  # a G0d célnyelvei
ALL_LANGS = tuple(ssr.REFERENCE_SETS_BY_LANG)


def _check_anchor_list(anchors, where: str):
    assert isinstance(anchors, list), f"{where}: nem lista"
    assert len(anchors) >= MIN_SENTENCES, f"{where}: {len(anchors)} < {MIN_SENTENCES} mondat"
    for s in anchors:
        assert isinstance(s, str) and s.strip(), f"{where}: üres/nem-string mondat"
    assert len(set(anchors)) == len(anchors), f"{where}: duplikált mondat"


def test_registry_covers_expected_langs():
    for lang in ("hu", "pt") + TARGET_LANGS:
        assert lang in ssr.REFERENCE_SETS_BY_LANG, f"hiányzó nyelv a regiszterben: {lang}"


def test_every_language_set_is_structurally_complete():
    for lang, sets in ssr.REFERENCE_SETS_BY_LANG.items():
        assert isinstance(sets, dict) and sets, f"{lang}: üres készlet"
        for cat, anchors in sets.items():
            _check_anchor_list(anchors, f"{lang}/{cat}")


def test_no_cross_category_duplicates_within_language():
    for lang, sets in ssr.REFERENCE_SETS_BY_LANG.items():
        seen = {}
        for cat, anchors in sets.items():
            for s in anchors:
                assert s not in seen, f"{lang}: '{s}' duplán ({seen.get(s)} és {cat})"
                seen[s] = cat


def test_target_langs_have_both_canonical_categories():
    for lang in TARGET_LANGS + ("hu",):
        for category in ("agreement", "financial_outlook"):
            anchors = ssr.get_anchor_set(lang, category)
            _check_anchor_list(anchors, f"{lang}/{category}")
    # PT: a minőség-referencia, financial_outlook kötelező (agreement nem célja)
    _check_anchor_list(ssr.get_anchor_set("pt", "financial_outlook"), "pt/financial_outlook")


def test_anchor_category_keys_resolve():
    for category, by_lang in ssr.ANCHOR_CATEGORY_KEYS.items():
        for lang, native_key in by_lang.items():
            assert lang in ssr.REFERENCE_SETS_BY_LANG, f"{category}: ismeretlen nyelv {lang}"
            assert native_key in ssr.REFERENCE_SETS_BY_LANG[lang], \
                f"{category}/{lang}: hiányzó natív kulcs '{native_key}'"


def test_price_sets_cover_g1g2_countries():
    # FR/IT ár-horgony a delphoi.REFERENCE_SETS_PRICE_EXTRA-ban él (szándékosan).
    for country in ("HU", "CZ", "PT", "PL", "ES", "DE", "EN"):
        assert country in ssr.REFERENCE_SETS_PRICE, f"PRICE hiányzó ország: {country}"
        _check_anchor_list(ssr.REFERENCE_SETS_PRICE[country], f"PRICE/{country}")


def test_business_sets_structurally_complete():
    for country, anchors in ssr.REFERENCE_SETS_BUSINESS.items():
        _check_anchor_list(anchors, f"BUSINESS/{country}")


def test_zero_regression_backtest_keys_and_snapshots():
    # A backtest-szkriptek által hivatkozott kulcsok + első mondat-pillanatképek:
    # ha ez törik, egy meglévő horgonyt írt át valaki (az TILOS — új kulcs kell).
    assert ssr.REFERENCE_SETS_HU["anyagi_varakozas"][0] == \
        "A háztartásom anyagi helyzete sokkal rosszabb lesz."
    assert ssr.REFERENCE_SETS_HU["egyetertes"][0] == "Egyáltalán nem értek ezzel egyet."
    assert ssr.REFERENCE_SETS_CZ["financni_vyhled"][0] == \
        "Finanční situace mé domácnosti se hodně zhorší."
    assert ssr.REFERENCE_SETS_PT["perspetiva_financeira"][0] == \
        "A situação financeira do meu agregado familiar vai piorar muito."
    assert ssr.REFERENCE_SETS_PL["perspektywa_finansowa"][0] == \
        "Sytuacja finansowa mojego gospodarstwa domowego znacznie się pogorszy."
    assert ssr.REFERENCE_SETS_EN["purchase_intent"][0] == "I would definitely not buy this"
    assert ssr.REFERENCE_SETS_PRICE["HU"][0].startswith("A bolti árak")
    assert ssr.REFERENCE_SETS_BUSINESS["HU"][0].startswith("A cégem üzleti helyzete")


def test_get_anchor_set_unknown_raises():
    try:
        ssr.get_anchor_set("xx", "agreement")
        assert False, "ismeretlen nyelvre KeyError kell"
    except KeyError:
        pass
    try:
        ssr.get_anchor_set("hu", "nonexistent_category")
        assert False, "ismeretlen kategóriára KeyError kell"
    except KeyError:
        pass


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ✓ {fn.__name__}")
    print(f"\nssr_reference_sets: {len(fns)}/{len(fns)} teszt OK")


if __name__ == "__main__":
    _run_all()
