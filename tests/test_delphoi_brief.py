"""test_delphoi_brief.py — PYTHIA B1: brief-séma + validátor + spec_hash +
dimenzió-bank. Cél-bizonyíték (1): mentett brief kétszer ugyanazt a
spec_hash-t adja (idempotens mentés, kulcs-sorrend-invariáns hash)."""
import json

import pytest

from plugins import delphoi_brief as db_mod


def _valid_spec(**over) -> dict:
    spec = {
        "goal": "Új termékleírás vonzerejének mérése",
        "instrument": "product_desc",
        "stimuli": ["Prémium kávéfőző, amely 30 másodperc alatt főz."],
        "country": "HU",
        "n": 30,
        "segments": {"age": ["18-29", "30-39"]},
        "dimensions": ["appeal", "clarity"],
        "custom_questions": ["Mi hiányzik a leírásból?"],
        "tracking": "none",
        "report": {"lang": "hu", "formats": ["pdf", "csv"],
                   "notify_email": "teszt@example.com"},
    }
    spec.update(over)
    return spec


@pytest.fixture
def brief_db(get_db):
    conn = get_db()
    db_mod.ensure_brief_tables(conn)
    conn.close()
    return get_db


# ── séma + validátor ────────────────────────────────────────────────────────

def test_valid_spec_passes():
    assert db_mod.validate_brief(_valid_spec()) == []


def test_missing_goal_rejected():
    errs = db_mod.validate_brief(_valid_spec(goal=""))
    assert any("goal" in e for e in errs)


def test_unknown_instrument_rejected():
    errs = db_mod.validate_brief(_valid_spec(instrument="poll"))
    assert any("instrument" in e for e in errs)


def test_ab_test_needs_two_stimuli():
    errs = db_mod.validate_brief(_valid_spec(instrument="ab_test",
                                             stimuli=["csak egy"]))
    assert any("2 stimulus" in e for e in errs)
    ok = db_mod.validate_brief(_valid_spec(instrument="ab_test",
                                           stimuli=["A", "B"]))
    assert ok == []


def test_forbidden_segment_axis_rejected():
    """Nem/etnikum tengely: HANGOS tiltás, nem csendes ejtés."""
    for axis in ("gender", "nem", "etnikum", "ethnicity", "race"):
        errs = db_mod.validate_brief(_valid_spec(segments={axis: ["x"]}))
        assert any("tiltott szegmens-tengely" in e for e in errs), axis


def test_unknown_segment_axis_rejected():
    errs = db_mod.validate_brief(_valid_spec(segments={"horoszkop": ["kos"]}))
    assert any("ismeretlen szegmens-tengely" in e for e in errs)


def test_income_axis_allowed():
    assert db_mod.validate_brief(
        _valid_spec(segments={"income": ["alsó harmad"]})) == []


def test_max_three_custom_questions():
    errs = db_mod.validate_brief(_valid_spec(
        custom_questions=["a?", "b?", "c?", "d?"]))
    assert any("custom_question" in e for e in errs)


def test_custom_questions_marked_unvalidated():
    c = db_mod.canonicalize_spec(_valid_spec())
    assert c["custom_questions"] == [
        {"text": "Mi hiányzik a leírásból?", "validated": False}]


def test_unknown_dimension_rejected():
    errs = db_mod.validate_brief(_valid_spec(dimensions=["appeal", "virality"]))
    assert any("dimenzió" in e for e in errs)


def test_invalid_country_rejected():
    errs = db_mod.validate_brief(_valid_spec(country="DE"))
    assert any("ország" in e for e in errs)


def test_n_bounds():
    assert any("n a megengedett" in e
               for e in db_mod.validate_brief(_valid_spec(n=10)))
    assert any("n a megengedett" in e
               for e in db_mod.validate_brief(_valid_spec(n=100000)))


def test_bad_email_rejected():
    errs = db_mod.validate_brief(_valid_spec(
        report={"lang": "hu", "formats": ["pdf"], "notify_email": "nem-email"}))
    assert any("notify_email" in e for e in errs)


def test_bad_tracking_rejected():
    errs = db_mod.validate_brief(_valid_spec(tracking="daily"))
    assert any("tracking" in e for e in errs)


# ── spec_hash determinizmus ─────────────────────────────────────────────────

def test_spec_hash_key_order_invariant():
    spec = _valid_spec()
    reordered = json.loads(json.dumps(spec, sort_keys=True))
    shuffled = dict(reversed(list(reordered.items())))
    assert db_mod.spec_hash(spec) == db_mod.spec_hash(shuffled)


def test_spec_hash_differs_on_content_change():
    assert db_mod.spec_hash(_valid_spec()) != db_mod.spec_hash(
        _valid_spec(goal="Más cél"))


def test_saved_brief_same_hash_twice(brief_db):
    """CÉL-BIZONYÍTÉK (1): ugyanaz a brief kétszer mentve ugyanazt a
    spec_hash-t adja — és nem duplikálódik (idempotens mentés)."""
    r1 = db_mod.save_brief(brief_db, "user:1", _valid_spec())
    r2 = db_mod.save_brief(brief_db, "user:1", _valid_spec())
    assert r1["ok"] and r2["ok"]
    assert r1["spec_hash"] == r2["spec_hash"]
    assert r1["brief_id"] == r2["brief_id"]
    assert r1["created"] is True and r2["created"] is False


def test_save_invalid_brief_refused(brief_db):
    r = db_mod.save_brief(brief_db, "user:1", _valid_spec(goal=""))
    assert not r["ok"] and r["error"] == "invalid_brief"


def test_get_brief_owner_only(brief_db):
    r = db_mod.save_brief(brief_db, "user:1", _valid_spec())
    assert db_mod.get_brief(brief_db, r["brief_id"], "user:1")["ok"]
    assert not db_mod.get_brief(brief_db, r["brief_id"], "user:2")["ok"]


def test_tracking_brief_creates_tracking_row(brief_db):
    r = db_mod.save_brief(brief_db, "user:1", _valid_spec(tracking="weekly"))
    got = db_mod.get_brief(brief_db, r["brief_id"], "user:1")
    assert got["tracking"]["cadence"] == "weekly"
    assert got["tracking"]["active"] == 1


def test_list_briefs(brief_db):
    db_mod.save_brief(brief_db, "user:1", _valid_spec())
    db_mod.save_brief(brief_db, "user:1", _valid_spec(goal="Második brief"))
    items = db_mod.list_briefs(brief_db, "user:1")
    assert len(items) == 2
    assert {i["goal"] for i in items} == {"Új termékleírás vonzerejének mérése",
                                          "Második brief"}


# ── dimenzió-bank ───────────────────────────────────────────────────────────

def test_dimension_bank_hu_en_complete():
    """MINDEN engedélyezett dimenzióhoz natív hu+en 5-pontos horgony-készlet."""
    for dim in db_mod.ALLOWED_DIMENSIONS:
        for lang in ("hu", "en"):
            anchors = db_mod.get_dimension_anchors(dim, lang)
            assert len(anchors) == 5, (dim, lang)
            assert len(set(anchors)) == 5, (dim, lang)
            assert all(isinstance(a, str) and len(a) > 10 for a in anchors)


def test_dimension_bank_hu_en_not_identical():
    """A hu és en készlet nem lehet azonos (nem tükör-másolat)."""
    for dim in db_mod.ALLOWED_DIMENSIONS:
        assert db_mod.get_dimension_anchors(dim, "hu") != \
            db_mod.get_dimension_anchors(dim, "en"), dim


def test_dimension_bank_appeal_reuses_delphoi_sets():
    from plugins import delphoi
    assert db_mod.get_dimension_anchors("appeal", "cs") == \
        delphoi.REFERENCE_SETS_APPEAL["cs"]


def test_dimension_bank_unknown_lang_falls_back():
    assert db_mod.get_dimension_anchors("clarity", "sw") == \
        db_mod.get_dimension_anchors("clarity", "en")


def test_dimension_bank_unknown_dimension_loud():
    with pytest.raises(ValueError):
        db_mod.get_dimension_anchors("virality", "hu")


# ── kredit-becslés (config-dict képlet) ─────────────────────────────────────

def test_estimate_credits_formula():
    cfg = {"calls_per_persona_base": 1.0, "calls_per_extra_dimension": 1.0,
           "calls_per_custom_question": 1.0, "calls_per_credit": 30}
    assert db_mod.estimate_credits(30, 1, 0, config=cfg) == 1
    assert db_mod.estimate_credits(30, 2, 1, config=cfg) == 3   # 30*3/30
    assert db_mod.estimate_credits(60, 1, 0, 2, config=cfg) == 4  # 60*2/30
    assert db_mod.estimate_credits(1, 1, 0, config=cfg) == 1     # min 1


# ── brief → job leképezés ──────────────────────────────────────────────────

def test_brief_to_job_args_single():
    kind, text, panel_spec, variants = db_mod.brief_to_job_args(
        _valid_spec(), "brf-x")
    assert kind == "product_desc"
    assert text.startswith("Prémium")
    assert variants is None
    assert panel_spec["brief_id"] == "brf-x"
    assert panel_spec["n_per_cell"] == 30
    assert panel_spec["dimensions"] == ["appeal", "clarity"]


def test_brief_to_job_args_variants():
    kind, text, _spec, variants = db_mod.brief_to_job_args(
        _valid_spec(instrument="ab_test", stimuli=["A", "B"]), "brf-y")
    assert kind == "ab_test" and text == "" and variants == ["A", "B"]


def test_custom_brief_maps_to_concept():
    kind, _t, spec, _v = db_mod.brief_to_job_args(
        _valid_spec(instrument="custom_brief"), "brf-z")
    assert kind == "concept"
    assert spec["instrument"] == "custom_brief"
