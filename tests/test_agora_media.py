"""Offline unit tesztek az Agora média-réteghez (nincs hálózat, nincs LLM).

Lefedi: média-guard (idegen media-ref, alt-guard, videó-URL, kép-limit),
napi upload-kvóta, chart-render (matplotlib megléte esetén), chart-spec
validálás, upload helper soft-fail, és egy Von Takt-stílusú dry-run
komment-draft generált mini-charttal — feltöltés NÉLKÜL (None media_id
→ kép nélküli út).
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from plugins._agora_media import (  # noqa: E402
    MAX_MEDIA_PER_DAY, build_image_markdown, can_upload_media, media_guard,
    media_uploads_today, record_media_upload, upload_agora_media,
)
from plugins import _agora_charts as ch  # noqa: E402
from plugins.agora_duty import _INIT_SQL, content_guard  # noqa: E402


def _mkdb():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    def get_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    conn = get_db()
    conn.executescript(_INIT_SQL)
    conn.commit()
    conn.close()
    return get_db, path


# ── média-guard ──
def test_guard_keeps_own_media_ref():
    body = "![Ábra: HU infláció 2025-2026, forrás KSH](media:abc123)\n\nA lényeg: 3,9%."
    cleaned, rep = media_guard(body, {"abc123"}, "hu", max_images=1)
    assert "media:abc123" in cleaned
    assert rep["kept"] == 1 and rep["removed_unknown"] == 0


def test_guard_strips_unknown_media_ref():
    body = "![Ábra: valami idegen, forrás ismeretlen](media:EVIL99)\n\nSzöveg marad."
    cleaned, rep = media_guard(body, {"abc123"}, "hu")
    assert "media:" not in cleaned and "Szöveg marad." in cleaned
    assert rep["removed_unknown"] == 1 and rep["kept"] == 0


def test_guard_strips_all_when_no_allowed_set():
    body = "![Ábra: hallucinált kép, forrás semmi](media:xyz)\n\nMarad a szöveg."
    cleaned, rep = media_guard(body, set(), "hu")
    assert "media:" not in cleaned and "Marad a szöveg." in cleaned


def test_guard_short_alt_rejected():
    body = "![rövid](media:abc123)\n\nA szám: 42."
    cleaned, rep = media_guard(body, {"abc123"}, "hu")
    assert rep["removed_bad_alt"] == 1 and "media:" not in cleaned


def test_guard_alt_content_guard_runs():
    # belső konyha az alt-ban → a tartalmi guard elutasítja → kép törölve
    body = "![Ábra a prefetch tool-outputból, statdata hiba nélkül](media:abc123)\n\nMarad."
    cleaned, rep = media_guard(body, {"abc123"}, "hu")
    assert rep["removed_bad_alt"] == 1 and "media:" not in cleaned


def test_guard_video_url_removed():
    body = ("Elemzés a számokról.\n\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ\n\n"
            "És még egy: https://vimeo.com/123456 itt.")
    cleaned, rep = media_guard(body, set(), "hu")
    assert "youtube" not in cleaned and "vimeo.com" not in cleaned
    assert rep["removed_video"] == 2
    assert "Elemzés a számokról." in cleaned


def test_guard_image_limit():
    ok_alt = "Ábra: infláció alakulása, forrás KSH"
    body = (f"![{ok_alt}](media:id1)\n\n![{ok_alt}](media:id2)\n\nSzöveg.")
    cleaned, rep = media_guard(body, {"id1", "id2"}, "hu", max_images=1)
    assert rep["kept"] == 1 and rep["removed_over_limit"] == 1
    assert cleaned.count("media:") == 1


def test_guard_raw_media_token_removed():
    body = "Nézd meg: media:abc123 — érdekes. (media:def456)"
    cleaned, rep = media_guard(body, {"abc123"}, "hu")
    assert "media:" not in cleaned and rep["removed_raw_ref"] == 2


def test_build_image_markdown_sanitizes_alt():
    md = build_image_markdown("Ábra [teszt]\ntöbb sor", "id9")
    assert md.startswith("![") and "](media:id9)" in md
    assert "[teszt]" not in md and "\n" not in md


# ── napi upload-kvóta (bridge-oldali 2/nap) ──
def test_media_daily_quota():
    get_db, _ = _mkdb()
    assert can_upload_media(get_db, "von_takt")
    for i in range(MAX_MEDIA_PER_DAY):
        record_media_upload(get_db, "von_takt", f"m{i}", "story1", "teszt ábra")
    assert media_uploads_today(get_db, "von_takt") == MAX_MEDIA_PER_DAY
    assert not can_upload_media(get_db, "von_takt")
    # másik agent kvótája független
    assert can_upload_media(get_db, "der_kartograph")


# ── chart-spec validálás ──
def test_validate_chart_spec_good():
    spec = ch.validate_chart_spec({
        "kind": "line", "title": "HU infláció (éves, %)",
        "labels": ["2026-01", "2026-02", "2026-03"],
        "values": [4.2, "4.0", 3.9],
        "unit": "%", "source": "KSH, 2026", "key_number": "3,9% (2026-03)"})
    assert spec and spec["values"] == [4.2, 4.0, 3.9]


def test_validate_chart_spec_bad():
    assert ch.validate_chart_spec(None) is None
    assert ch.validate_chart_spec({"kind": "pie", "title": "x", "labels": [], "values": []}) is None
    assert ch.validate_chart_spec({"kind": "line", "title": "ok cím",
                                   "labels": ["a"], "values": [1]}) is None  # <2 pont
    assert ch.validate_chart_spec({"kind": "bar", "title": "ok cím",
                                   "labels": ["a", "b"], "values": [1, "nan"]}) is None
    assert ch.validate_chart_spec({"kind": "bar", "title": "ok cím",
                                   "labels": ["a", "b"], "values": [1]}) is None  # hossz-eltérés


# ── chart-render (csak ha van matplotlib) ──
def test_render_line_chart_png():
    if not ch.HAS_MPL:
        print("  (matplotlib hiányzik — render-teszt kihagyva)")
        return
    png = ch.render_line_chart("HU infláció (éves, %)",
                               [f"2025-{m:02d}" for m in range(1, 13)],
                               [5.5, 5.2, 5.0, 4.8, 4.7, 4.5, 4.4, 4.3, 4.2, 4.1, 4.0, 3.9],
                               source="Adat: KSH, 2025", unit="%")
    assert png and png[:4] == b"\x89PNG"


def test_kartograph_regional_chart():
    if not ch.HAS_MPL:
        print("  (matplotlib hiányzik — render-teszt kihagyva)")
        return
    regional = {
        "ru": {"label": "Orosz szféra", "avg_sentiment": -0.42, "articles": 12},
        "us": {"label": "Amerikai szféra", "avg_sentiment": 0.15, "articles": 30},
        "hu": {"label": "Magyar sajtó", "avg_sentiment": 0.02, "articles": 21},
    }
    png, alt = ch.kartograph_regional_chart(regional, "ukrajnai tűzszünet", "hu")
    assert png and png[:4] == b"\x89PNG"
    assert "regional_framing" in alt and len(alt) >= 10
    # az alt átmegy a tartalmi guardon (a media_guard is ezt futtatja)
    ok, why, _ = content_guard(alt, "")
    assert ok, f"alt guard-bukás: {why}"


def test_kartograph_chart_needs_two_regions():
    png, alt = ch.kartograph_regional_chart({"hu": {"label": "HU", "avg_sentiment": 0.1}}, "x")
    assert png is None and alt == ""


# ── upload helper: soft-fail ──
def test_upload_helper_success_and_softfail():
    fake_ok = {"ok": True, "media_id": "med42", "url": "/m/med42", "width": 792, "height": 445}

    async def _run(resp, exc=None):
        fake = mock.AsyncMock(return_value=resp, side_effect=exc)
        with mock.patch("_echolot_client.agora_action", new=fake):
            return await upload_agora_media(b"\x89PNGfake", "image/png", "eop_test")

    res = asyncio.run(_run(fake_ok))
    assert res and res["media_id"] == "med42"
    # szerver-hiba (rate limit) → None, nem exception
    res = asyncio.run(_run({"ok": False, "error": "rate_limited"}))
    assert res is None
    # transport-hiba → None, nem exception
    res = asyncio.run(_run(None, exc=RuntimeError("boom")))
    assert res is None
    # üres input → None (hálózat-érintés nélkül)
    assert asyncio.run(upload_agora_media(b"", "image/png", "eop_x")) is None
    assert asyncio.run(upload_agora_media(b"png", "image/png", "")) is None


# ── Von Takt-stílusú dry-run draft: mini-chart + media-guard, upload NÉLKÜL ──
def test_von_takt_style_draft_with_chart_and_without():
    draft = ("A friss KSH-adat szerint az éves infláció 3,9 százalékra lassult "
             "márciusban, ami 2021 óta a legalacsonyabb érték. A trend iránya "
             "fontosabb, mint az egyhavi szám: a bázishatás még két hónapig lefelé húz.")

    # 1) sikeres upload-út (mockolt media_id-val): Python illeszti a képet
    if ch.HAS_MPL:
        spec = ch.validate_chart_spec({
            "kind": "line", "title": "HU infláció (éves, %)",
            "labels": ["2026-01", "2026-02", "2026-03"], "values": [4.2, 4.0, 3.9],
            "unit": "%", "source": "KSH, 2026", "key_number": "3,9%"})
        png = ch.render_from_spec(spec)
        assert png and png[:4] == b"\x89PNG"
        alt = ch.spec_alt_text(spec, "hu")
        body = build_image_markdown(alt, "medMOCK") + "\n\n" + draft
        cleaned, rep = media_guard(body, {"medMOCK"}, "hu", max_images=1)
        assert rep["kept"] == 1 and "media:medMOCK" in cleaned
        ok, why, _ = content_guard(alt, "")
        assert ok, f"alt guard-bukás: {why}"

    # 2) upload-hiba / None media_id → kép nélküli út: a draft érintetlen marad
    media_id = None
    body = (build_image_markdown("x", media_id) + "\n\n" + draft) if media_id else draft
    cleaned, rep = media_guard(body, set(), "hu", max_images=1)
    assert cleaned == draft and rep["kept"] == 0
    ok, why, final = content_guard(cleaned, "hu")
    assert ok and final


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
