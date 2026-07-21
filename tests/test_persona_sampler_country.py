"""Tests for the G0d country-quota layer in plugins/persona_sampler.py.

HU zéró-regresszió: a regiszter HU-kvótái bitre egyeznek a pollster.py-ban
validált KSH/NMHH készlettel, és a sample_country_personas kimenete azonos a
kézzel átadott dims-ű sample_personas-éval. Fut pytesttel ÉS scriptként:
    python3 tests/test_persona_sampler_country.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from plugins import persona_sampler as ps
from plugins import pollster


def test_hu_dims_match_pollster_canonical():
    dims = ps.get_country_dims("HU")
    assert dims["age"] == list(pollster.AGE)
    assert dims["settlement"] == list(pollster.SETTLEMENT)
    assert dims["edu"] == list(pollster.EDU)
    assert dims["media"] == [(l, w) for l, w in pollster.MEDIA]


def test_hu_dims_weights_sum_to_one():
    for d, opts in ps.get_country_dims("HU").items():
        total = sum(w for _l, w in opts)
        assert abs(total - 1.0) < 0.02, f"{d}: súlyösszeg {total}"


def test_sample_country_personas_equals_manual_dims():
    # zéró regresszió: a réteg NEM változtat a mintavételen, csak a dims-forráson
    a, kl_a = ps.sample_country_personas("HU", n=80, seed=42)
    b, kl_b = ps.sample_personas(ps.get_country_dims("HU"), n=80, seed=42)
    assert a == b
    assert kl_a == kl_b
    assert len(a) == 80
    assert set(a[0].keys()) == {"id", "age", "settlement", "edu", "media"}


def test_sample_country_personas_deterministic_and_case_insensitive():
    a, _ = ps.sample_country_personas("hu", n=40, seed=7)
    b, _ = ps.sample_country_personas("HU", n=40, seed=7)
    assert a == b


def test_todo_countries_are_registered_but_reject_loudly():
    # G3: DE/UK/US kikerült a todo-ból (élesítve 2026-07-21)
    for c in ("CZ", "ES", "PL", "PT"):
        assert c in ps.COUNTRY_QUOTAS, f"hiányzó regiszter-sor: {c}"
        assert ps.COUNTRY_QUOTAS[c]["status"] == "todo"
        try:
            ps.get_country_dims(c)
            assert False, f"{c}: feltöltetlen kvótára ValueError kell"
        except ValueError as e:
            assert "G0c" in str(e) or "kvót" in str(e)


def test_fr_it_live_from_delphoi_canonical():
    # G2: FR/IT a delphoi.COUNTRY_PANEL_CONFIG-ból töltődik (EGY forrás, lusta
    # import); G3: DE (Eurostat-lekérdezés) + UK/US (dokumentált statikus
    # kvóták) ugyanezen az úton élesek.
    from plugins import delphoi
    for c in ("FR", "IT", "DE", "UK", "US"):
        assert ps.COUNTRY_QUOTAS[c]["status"] == "live"
        dims = ps.get_country_dims(c)
        cfg = delphoi.COUNTRY_PANEL_CONFIG[c]
        for d in ("age", "settlement", "edu"):
            assert dims[d] == list(cfg["dims"][d])
        assert dims["media"] == [(l, w) for l, w, _b in cfg["media"]]
        for d, opts in dims.items():
            total = sum(w for _l, w in opts)
            assert abs(total - 1.0) < 0.02, f"{c}/{d}: súlyösszeg {total}"
        personas, kl = ps.sample_country_personas(c, n=60, seed=1)
        assert len(personas) == 60
        assert set(personas[0].keys()) == {"id", "age", "settlement", "edu", "media"}
        assert all(v < 0.05 for v in kl.values()), f"{c}: KL-illeszkedés gyenge: {kl}"


def test_unknown_country_raises_keyerror():
    try:
        ps.get_country_dims("XX")
        assert False, "ismeretlen országra KeyError kell"
    except KeyError as e:
        assert "XX" in str(e)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ✓ {fn.__name__}")
    print(f"\npersona_sampler_country: {len(fns)}/{len(fns)} teszt OK")


if __name__ == "__main__":
    _run_all()
