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
