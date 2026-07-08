"""Offline unit tesztek az Agora-sorszolgálathoz (nincs hálózat, nincs LLM)."""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from plugins._agora_personas import AGORA_AGENTS, get_agora_service_block  # noqa: E402
from plugins.agora_duty import (  # noqa: E402
    _INIT_SQL, MAX_COMMENTS_PER_DAY, content_guard, detect_lang, story_lang,
    _counts, _log_act, _commented_story, _kill_switch_on, _extract_json,
)


def _mkdb():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    def get_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    conn = get_db()
    conn.executescript(_INIT_SQL)
    conn.execute("CREATE TABLE IF NOT EXISTS shared_memory (key TEXT, value TEXT)")
    conn.commit()
    conn.close()
    return get_db, path


# ── nyelvi kapu ──
def test_detect_lang_hu():
    assert detect_lang("A kormány szerint az infláció már nem probléma, de a piac mást mond.") == "hu"


def test_detect_lang_en():
    assert detect_lang("The central bank is expected to hold rates for the rest of the year.") == "en"


def test_detect_lang_ru_is_other():
    assert detect_lang("Российская экономика столкнулась с новыми санкциями") == "other"


def test_detect_lang_de_is_other():
    assert detect_lang("Die Bundesregierung hat ein neues Gesetz für die Wirtschaft beschlossen und die Steuern nicht erhöht") == "other"


def test_story_lang_gate():
    assert story_lang({"languages": ["de"], "title": "Die Zeitung"}) == "other"
    assert story_lang({"languages": ["hu"], "title": "Valami magyar cím"}) == "hu"
    assert story_lang({"languages": ["en", "hu"], "title": "The inflation is higher than expected and the forint is weak"}) == "en"
    # kevert nyelvű cluster, nem detektálható cím → hu preferencia
    assert story_lang({"languages": ["en", "hu"], "title": "Fizz: Bux 42000"}) == "hu"


# ── tartalmi guard ──
def test_guard_length_trim():
    ok, why, body = content_guard(
        "Az infláció még mindig magas, és a piac már nem hisz a jegybanknak. " * 40, "hu")
    assert ok and len(body) <= 1200


def test_guard_profanity():
    ok, why, _ = content_guard("Ez a kurva cikk téved.", "hu")
    assert not ok and why == "profanity"


def test_guard_email_blocked():
    ok, why, _ = content_guard("Írj nekem: valaki@example.com címre.", "hu")
    assert not ok and "personal_data" in why


def test_guard_lang_mismatch():
    ok, why, _ = content_guard("This is entirely written in English for sure.", "hu")
    assert not ok and why.startswith("lang_mismatch")


# ── kvóták, dedup, kill switch ──
def test_quota_counting_and_dedup():
    get_db, _ = _mkdb()
    for i in range(MAX_COMMENTS_PER_DAY):
        _log_act(get_db, "von_takt", "comment", f"story{i}", f"c{i}", "hu")
    c = _counts(get_db, "von_takt")
    assert c["comments_today"] == MAX_COMMENTS_PER_DAY
    assert _commented_story(get_db, "von_takt", "story0")
    assert not _commented_story(get_db, "von_takt", "storyX")


def test_kill_switch_default_on_and_off():
    get_db, _ = _mkdb()
    assert _kill_switch_on(get_db) is True  # hiányzó kulcs = engedélyezve
    conn = get_db()
    conn.execute("INSERT INTO shared_memory (key, value) VALUES ('agora_duty_enabled', 'false')")
    conn.commit()
    conn.close()
    assert _kill_switch_on(get_db) is False


# ── personák ──
def test_personas_complete():
    assert set(AGORA_AGENTS) == {"von_takt", "der_kartograph", "frau_lupe"}
    for a in AGORA_AGENTS.values():
        assert a["model_badge"] and a["env_key"] and a["memory_key"]
    blk = get_agora_service_block("kimi")
    assert "Von Takt" in blk and "AGORA-SZOLGÁLAT" in blk
    assert get_agora_service_block("qwen3_coder") == ""


def test_extract_json_fenced():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('szöveg előtte {"b": [1,2]} utána') == {"b": [1, 2]}


# ── mondathatár-truncation (2026-07-08: author_note "...kezdett el " fix) ──
def test_truncate_sentence_short_unchanged():
    from plugins.agora_duty import truncate_sentence
    assert truncate_sentence("Rövid mondat.", 400) == "Rövid mondat."


def test_truncate_sentence_cuts_at_sentence_boundary():
    from plugins.agora_duty import truncate_sentence
    text = ("Az első mondat itt véget ér. A második mondat hosszabban folytatódik, "
            "és a vágásnak az előző mondathatárnál kell történnie, nem fél szónál.")
    out = truncate_sentence(text, 60)
    assert out == "Az első mondat itt véget ér."


def test_truncate_sentence_no_half_word():
    from plugins.agora_duty import truncate_sentence
    text = "Egyetlenhosszú mondatvégtelen szavakkal amit nem lehet mondathatáron vágni mert nincs benne pont"
    out = truncate_sentence(text, 50)
    assert len(out) <= 51
    # nem maradhat fél szó: a vágott szöveg szóhatáron ér véget (… lezárással)
    assert out.endswith("…")
    assert not out[:-1].endswith(" ")
    for w in out[:-1].split():
        assert w in text  # minden megmaradt szó teljes


def test_parse_essay_output_author_note_sentence_safe():
    from plugins.agora_duty import parse_essay_output
    long_note = ("Ezt az esszét azért írtam meg, mert a közmédia elsötétülése ritka "
                 "pillanat. " * 6) + "A végén pedig valami hosszú gondolatba kezdett el"
    raw = f"CÍM: Teszt cím\nJEGYZET: {long_note}\n---\nAz esszé törzse."
    title, note, body = parse_essay_output(raw)
    assert title == "Teszt cím"
    assert body == "Az esszé törzse."
    assert len(note) <= 400
    assert not note.endswith("kezdett el")          # a fél mondat nem maradhat
    assert note.endswith(".") or note.endswith("…")  # mondat- vagy szóhatár-lezárás


def test_parse_essay_output_fallback_title():
    from plugins.agora_duty import parse_essay_output
    title, note, body = parse_essay_output("csak nyers szöveg formátum nélkül",
                                           fallback_title="Beat-fókusz")
    assert title == "Beat-fókusz"
    assert body == "csak nyers szöveg formátum nélkül"


# ── EMBERI NYELV guard (persona-szabály 15) ──
def test_guard_internal_sphere_code_blocked():
    ok, why, _ = content_guard(
        "A hu_economy szférában a keretezés ma egészen máshogy néz ki, mint tegnap.", "hu")
    assert not ok and why.startswith("internal_id")


def test_guard_backtick_internal_blocked():
    ok, why, _ = content_guard(
        "A `conflict` keret dominál a mai címlapokon, és ez sokat elmond.", "hu")
    assert not ok and why.startswith("backtick_internal")


def test_guard_ttier_blocked():
    ok, why, _ = content_guard(
        "A T1 források már reggel hozták a hírt, a többi csak délután.", "hu")
    assert not ok and why.startswith("internal_tier")


def test_guard_human_translation_passes():
    ok, why, _ = content_guard(
        "A gazdasági hírrégió ma a konfliktus-keretezés felé tolódott el, "
        "és ez nem véletlen: az erkölcsi keretezés csak a címlapokon él.", "hu")
    assert ok, why


def test_guard_url_underscore_not_flagged():
    ok, why, _ = content_guard(
        "A teljes elemzés itt olvasható: https://example.com/riport_2026_junius "
        "— de a lényeg már a címből is látszik, mert a számok nem hazudnak.", "hu")
    assert ok, why


# ── beat-match jelölt-normalizálás (dedup-fallback alapja) ──
def test_normalize_matches_new_and_old_shape():
    from plugins.agora_duty import _normalize_matches
    new = _normalize_matches({"von_takt": {"candidates": [
        {"story_id": "b", "score": 4}, {"story_id": "a", "score": 9}]}})
    assert [c["story_id"] for c in new["von_takt"]] == ["a", "b"]  # score-rendezés
    old = _normalize_matches({"der_kartograph": {"story_id": "x", "score": 7}})
    assert old["der_kartograph"][0]["story_id"] == "x"
    assert old["von_takt"] == []
    assert _normalize_matches("nem dict") == {}


# ── nyelvközi lefedettség blokk (defenzív no-op) ──
def test_intl_coverage_block_defensive():
    from plugins.agora_duty import _intl_coverage_block
    assert _intl_coverage_block({"story_id": "x"}) == ""            # mező hiányzik → no-op
    assert _intl_coverage_block({"international_coverage": []}) == ""
    blk = _intl_coverage_block({"international_coverage": [
        {"title": "Blackout in Hungary", "lang": "en", "source": "Reuters"},
        "Stromausfall in Ungarn (de)"]})
    assert "Blackout in Hungary" in blk and "Reuters" in blk
    assert "Stromausfall" in blk and "NYELVKÖZI" in blk


# ── note-műfaj segédek ──
def test_note_quota_and_spacing():
    from plugins.agora_duty import _last_note_within_days
    get_db, _ = _mkdb()
    assert _last_note_within_days(get_db, "frau_lupe") is False
    _log_act(get_db, "frau_lupe", "note", "s1", "p1", "hu", "cross-story jegyzet")
    assert _last_note_within_days(get_db, "frau_lupe") is True
    c = _counts(get_db, "frau_lupe")
    assert c["notes_today"] == 1 and c["publishes_today"] == 1
