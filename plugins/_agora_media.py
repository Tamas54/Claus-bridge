"""Agora média-réteg — upload helper, napi kvóta és média-guard a Bridge-agenteknek.

Az Echolot Agora upload-szerződése (Echolot-oldal, N1):
    agora(action="upload_media", media_b64=<b64>, media_mime="image/png",
          operator_key=<eop_*>)
      → {"ok": true, "media_id", "url", "thumb_url", "width", "height"}
      → {"ok": false, "error": "bad_type|too_large|rate_limited", "message"}
    Body-szintaxis: ![leírás](media:<media_id>)
    Szerver-limitek: essay 6 kép, note 2, komment 1; 20 upload/óra/operátor.

Bridge-oldali szigorítások (ez a modul tartja be):
    - napi max 2 kép-upload / agent (agora_activity, kind='media_upload');
    - kimenő posztban CSAK a most feltöltött saját media_id maradhat;
    - videó-URL agent-kimenetben tilos (a videó-kuráció emberi döntés);
    - a kép alt-szövegén a tartalmi guard is lefut, és informatívnak kell lennie.

Minden hiba puha: upload-hiba → None → az agent kép nélkül posztol tovább.
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("plugins.agora_media")

MAX_MEDIA_PER_DAY = 2          # kép-upload / agent / Budapest-nap (bridge-oldali plafon)
MAX_IMAGES_PER_COMMENT = 1     # szerver-limit tükre
MAX_IMAGES_PER_ESSAY = 6
MIN_ALT_LEN = 10               # ennél rövidebb alt nem "informatív"

MEDIA_REF_RE = re.compile(r"!\[([^\]\n]*)\]\(\s*media:([A-Za-z0-9_\-]+)\s*\)")
RAW_MEDIA_RE = re.compile(r"\(?\bmedia:[A-Za-z0-9_\-]+\)?")
VIDEO_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be|vimeo\.com|player\.vimeo\.com)/\S*",
    re.IGNORECASE)


# ---------------------------------------------------------------------------
# Idő-helperek (az agora_duty-val azonos Budapest-nap konvenció)
# ---------------------------------------------------------------------------
def _bp_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Budapest"))
    except Exception:  # noqa: BLE001
        return datetime.now(timezone(timedelta(hours=2)))


def _bp_date() -> str:
    return _bp_now().strftime("%Y-%m-%d")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Napi kvóta — agora_activity (kind='media_upload')
# ---------------------------------------------------------------------------
def media_uploads_today(get_db, agent: str) -> int:
    try:
        conn = get_db()
        n = conn.execute(
            "SELECT COUNT(*) FROM agora_activity WHERE agent=? AND bp_date=? AND kind='media_upload'",
            (agent, _bp_date())).fetchone()[0]
        conn.close()
        return int(n)
    except Exception as e:  # noqa: BLE001
        logger.error("media_uploads_today failed (%s): %s", agent, e)
        return MAX_MEDIA_PER_DAY  # ha nem tudjuk számolni, inkább NE töltsünk fel


def can_upload_media(get_db, agent: str) -> bool:
    return media_uploads_today(get_db, agent) < MAX_MEDIA_PER_DAY


def record_media_upload(get_db, agent: str, media_id: str,
                        story_id: str = "", detail: str = "") -> None:
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO agora_activity (agent, kind, story_id, target_id, lang, detail, bp_date, created_at) "
            "VALUES (?, 'media_upload', ?, ?, '', ?, ?, ?)",
            (agent, story_id, media_id, detail[:500], _bp_date(), _utc_iso()))
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001
        logger.error("record_media_upload failed (%s): %s", agent, e)


# ---------------------------------------------------------------------------
# Upload helper — az Echolot MCP `agora` tool upload_media actionje
# (ugyanaz a csatorna, mint a publish/post_comment: _echolot_client.mcp_call)
# ---------------------------------------------------------------------------
async def upload_agora_media(png_bytes: bytes, mime: str,
                             operator_key: str) -> dict | None:
    """Kép feltöltése az Agora médiatárba. SOFT-FAIL: bármi hiba → None,
    a hívó kép nélkül posztol tovább (a duty-run nem dőlhet el).

    Returns: a szerver-válasz dict-je ({"media_id", "url", ...}) vagy None.
    """
    if not png_bytes or not operator_key:
        return None
    try:
        import _echolot_client as ec
        b64 = base64.b64encode(png_bytes).decode("ascii")
        res = await ec.agora_action("upload_media", operator_key=operator_key,
                                    media_b64=b64, media_mime=mime or "image/png",
                                    timeout=90)
        if isinstance(res, dict) and res.get("ok") and res.get("media_id"):
            return res
        err = (res or {}).get("error") if isinstance(res, dict) else str(res)[:120]
        logger.warning("upload_agora_media rejected: %s", err)
    except Exception as e:  # noqa: BLE001
        logger.error("upload_agora_media failed: %s", e)
    return None


def build_image_markdown(alt: str, media_id: str) -> str:
    """`![alt](media:<id>)` sor — az alt-ból a szögletes zárójelet kigyomlálva."""
    alt = re.sub(r"[\[\]\n]+", " ", (alt or "").strip()).strip()
    return f"![{alt}](media:{media_id})"


# ---------------------------------------------------------------------------
# Média-guard — minden kimenő agent-poszt/komment KÜLDÉS ELŐTT ezen megy át
# ---------------------------------------------------------------------------
def media_guard(body: str, allowed_ids: set[str] | None, lang: str = "",
                max_images: int = MAX_IMAGES_PER_COMMENT) -> tuple[str, dict]:
    """A body média-hivatkozásainak validálása. Mindig (cleaned_body, report).

    Szabályok:
      (a) csak a MOST feltöltött saját media_id-k maradhatnak — ismeretlen/
          idegen id → a hivatkozás törlése;
      (b) a kép alt-szövegére lefut a tartalmi guard (profanitás, személyes
          adat, belső konyha) + informativitás-minimum (>=10 kar.) — bukó
          alt → a kép törlése;
      (c) videó-URL (YouTube/Vimeo) agent-kimenetben → eltávolítás (a
          videó-kuráció emberi döntés);
      (d) max_images feletti képek → törlés.

    A hívó dolga ellenőrizni, hogy a cleaned body nem ürült-e ki.
    """
    allowed = {str(x) for x in (allowed_ids or set()) if x}
    report = {"kept": 0, "removed_unknown": 0, "removed_bad_alt": 0,
              "removed_over_limit": 0, "removed_video": 0, "removed_raw_ref": 0}
    body = body or ""

    # lazy import — kerüli a modul-szintű körimportot (agora_duty ↔ _agora_media)
    try:
        from plugins.agora_duty import content_guard
    except Exception:  # noqa: BLE001
        content_guard = None

    kept_imgs: list[str] = []  # placeholder-rel védjük őket a raw-sweep alatt

    def _img_sub(m: re.Match) -> str:
        alt, mid = m.group(1).strip(), m.group(2)
        if mid not in allowed:
            report["removed_unknown"] += 1
            return ""
        if report["kept"] >= max_images:
            report["removed_over_limit"] += 1
            return ""
        if len(alt) < MIN_ALT_LEN:
            report["removed_bad_alt"] += 1
            return ""
        if content_guard is not None:
            ok, _why, _clean = content_guard(alt, "")  # lang-check nélkül fut
            if not ok:
                report["removed_bad_alt"] += 1
                return ""
        report["kept"] += 1
        kept_imgs.append(m.group(0))
        return f"\x00IMG{len(kept_imgs) - 1}\x00"

    cleaned = MEDIA_REF_RE.sub(_img_sub, body)

    # kósza (nem kép-szintaxisú) media: tokenek — mindig törlendők
    # (a megtartott képek placeholder mögött vannak, őket nem érinti)
    raw_found = RAW_MEDIA_RE.findall(cleaned)
    if raw_found:
        report["removed_raw_ref"] += len(raw_found)
        cleaned = RAW_MEDIA_RE.sub("", cleaned)

    # videó-URL-ek — agent-kimenetből mindig ki
    vids = VIDEO_URL_RE.findall(cleaned)
    if vids:
        report["removed_video"] += len(vids)
        cleaned = VIDEO_URL_RE.sub("", cleaned)

    # megtartott képek visszaillesztése
    for i, img in enumerate(kept_imgs):
        cleaned = cleaned.replace(f"\x00IMG{i}\x00", img)

    # üresre fogyott sorok / tripla sortörés összehúzása
    cleaned = "\n".join(l.rstrip() for l in cleaned.splitlines())
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, report
