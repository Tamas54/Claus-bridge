"""
Thread Digest Plugin — Operation Zahnrad
Email-thread összefoglaló + napi reggeli digest a Kommandantnak.

Két fő képesség:
  1) thread_summary — hosszú email-thread → 3 mondat magyar összefoglaló + akció-sor.
     Olvashat Gmailből (ha él a service), VAGY nyers szövegből (Gmail nélkül is megy).
  2) email_digest — napi batch: "N email jött, M igényel választ, K-ra van draft",
     a digest_state táblába upsertelve (watermark a duplikáció ellen).

Graceful degradation: ha nincs Gmail service ÉS nincs SiliconFlow kulcs, akkor a
messages tábla capture-daemon soraiból és priority-alapú becslésből dolgozik.

A plugin regisztráláskor beseedeli az 'email_digest' recipe-t a pyramid_recipes-be
(ha még nincs), hogy az orchestrátor-agentek is el tudják indítani a digestet.
"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("plugins.thread_digest")

__plugin_meta__ = {
    "name": "thread_digest",
    "version": "1.0.0",
    "description": "Email-thread összefoglaló + napi digest -- thread_summary, email_digest, digest_history",
}


def _now() -> str:
    """ISO timestamp (UTC)."""
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    """Mai dátum YYYY-MM-DD (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# Priority-értékek, amelyek "választ igénylő" emailt jeleznek (LLM nélküli becslés).
_NEEDS_REPLY_PRIORITIES = {"urgent", "important", "high"}


# ──────────────────────────────────────────────────────────────────────────
# SiliconFlow LLM hívás — közös, az email_triage.py mintáját követi.
# Üres api_key esetén SOHA nem hív (a hívók előbb ellenőrzik a kulcsot).
# ──────────────────────────────────────────────────────────────────────────
async def _sf_chat(deps: dict, system: str, user: str, max_tokens: int = 500) -> str:
    """SiliconFlow chat-completion hívás, a felhasznált szövegtartalmat adja vissza.

    Hiba esetén üres stringet ad vissza — a hívó fallbackeljen.
    """
    import httpx

    sf_key = deps.get("siliconflow_api_key", "")
    sf_base = deps.get("siliconflow_base_url", "https://api.siliconflow.com/v1")
    sf_models = deps.get("siliconflow_models", {})
    sf_timeout = deps.get("siliconflow_timeout", 220)

    if not sf_key:
        return ""

    model_id = sf_models.get("deepseek", "deepseek-ai/DeepSeek-V4-Pro")
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    # Modell-specifikus kapcsolók (mint az email_triage-ban).
    if "Kimi" in model_id:
        payload["thinking"] = {"type": "disabled"}
    elif "DeepSeek" in model_id:
        payload["reasoning_effort"] = "medium"

    try:
        async with httpx.AsyncClient(timeout=sf_timeout) as client:
            resp = await client.post(
                f"{sf_base}/chat/completions",
                headers={"Authorization": f"Bearer {sf_key}", "Content-Type": "application/json"},
                json=payload,
            )
            data = json.loads(resp.text)
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content.strip()
    except Exception as e:
        logger.warning("SiliconFlow chat hiba: %s", e)
        return ""


def _strip_code_fence(text: str) -> str:
    """Ha ```json ... ``` blokk, kibontja."""
    text = text.strip()
    if text.startswith("```"):
        # első sor (```json) levágva, záró ``` levágva
        parts = text.split("\n", 1)
        if len(parts) == 2:
            text = parts[1]
        text = text.rsplit("```", 1)[0]
    return text.strip()


# ──────────────────────────────────────────────────────────────────────────
# Gmail thread → nyers szöveg (csak ha él a service). asyncio.to_thread-del,
# mert a Google API kliens szinkron (blokkoló).
# ──────────────────────────────────────────────────────────────────────────
def _decode_part(part: dict) -> str:
    """Egy MIME-part text/plain tartalmának dekódolása (base64url)."""
    import base64
    body = part.get("body", {})
    data = body.get("data")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_plain_text(payload: dict) -> str:
    """Rekurzívan kiszedi a text/plain tartalmat egy Gmail payloadból."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        return _decode_part(payload)
    # Multipart → bejárjuk a részeket, a text/plain-t preferáljuk
    texts = []
    for part in payload.get("parts", []) or []:
        sub = _extract_plain_text(part)
        if sub:
            texts.append(sub)
    return "\n".join(texts)


def _read_gmail_thread_sync(svc, thread_id: str) -> str:
    """Szinkron Gmail thread olvasás → összefűzött nyers szöveg.

    Minden üzenetből: Felado/Datum/Targy fejléc + text/plain törzs.
    """
    from email.utils import parseaddr

    thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
    blocks = []
    for msg in thread.get("messages", []):
        payload = msg.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        sender_name, sender_email = parseaddr(headers.get("From", ""))
        body = _extract_plain_text(payload)
        if not body:
            body = msg.get("snippet", "")
        blocks.append(
            f"--- Üzenet ---\n"
            f"Feladó: {sender_name or sender_email}\n"
            f"Dátum: {headers.get('Date', '')}\n"
            f"Tárgy: {headers.get('Subject', '')}\n\n"
            f"{body.strip()}"
        )
    return "\n\n".join(blocks)


# ──────────────────────────────────────────────────────────────────────────
# Recipe-seed — az 'email_digest' recipe beírása a pyramid_recipes-be.
# IF NOT EXISTS guard: előbb SELECT 1, csak ha nincs, akkor INSERT. DDL-t NEM
# futtatunk (a recipes.py kezeli a táblát).
# ──────────────────────────────────────────────────────────────────────────
_DIGEST_RECIPE_PROMPT = (
    "FELADAT: Készíts napi email-digestet a Kommandantnak.\n\n"
    "LÉPÉSEK (pontosan ebben a sorrendben):\n"
    "1) Hívd meg az `email_digest` tool-t paraméter nélkül (a dátum alapból a mai nap).\n"
    "   Példa hívás: email_digest()\n"
    "2) A tool egy JSON-t ad vissza ezekkel a mezőkkel: emails_seen (hány email jött), "
    "needs_reply (hány igényel választ), drafted (hányra van már draft), summary "
    "(magyar, emberi olvasásra szánt szöveg), source (gmail vagy messages_fallback).\n"
    "3) Írd ki a Kommandantnak MAGYARUL, tömören, NE találj ki semmit, CSAK a tool "
    "által visszaadott számokat és a summary szöveget használd. Formátum:\n"
    "   'Reggeli digest (DÁTUM): N email jött, M igényel választ, K-ra van már draft. "
    "<summary>'\n"
    "4) Ha a tool 'error' mezőt ad vissza (pl. nincs adat a mai napra), írd ki: "
    "'Ma még nincs feldolgozható email.' — és NE találj ki adatot.\n\n"
    "FONTOS: Te csak az email_digest tool kimenetét tálalod. Tilos fejből számot "
    "vagy email-tartalmat kitalálni."
)


def _seed_digest_recipe(get_db):
    """Beseedeli az 'email_digest' recipe-t, ha még nincs. Idempotens."""
    conn = get_db()
    try:
        exists = conn.execute(
            "SELECT 1 FROM pyramid_recipes WHERE name = ?", ("email_digest",)
        ).fetchone()
        if exists:
            return
        ts = _now()
        conn.execute(
            "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, "
            "created_by, created_at, updated_at, cron_model, cron_enabled, cron_delivery) "
            "VALUES (?, ?, ?, ?, 'system', ?, ?, 'glm5', 0, 'both')",
            (
                "email_digest",
                "Napi email-digest: N email jött, M igényel választ, K-ra van draft",
                '["email_digest"]',
                _DIGEST_RECIPE_PROMPT,
                ts, ts,
            ),
        )
        conn.commit()
        logger.info("Recipe-seed: 'email_digest' beillesztve a pyramid_recipes-be")
    except Exception as e:
        # A pyramid_recipes táblát a recipes plugin hozza létre. Ha valamiért még
        # nem létezik (plugin betöltési sorrend), itt csak logolunk, nem dőlünk el.
        logger.warning("Recipe-seed kihagyva (pyramid_recipes nem elérhető?): %s", e)
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────
# Digest-építés a messages tábla capture-daemon sorai alapján (Gmail-fallback).
# ──────────────────────────────────────────────────────────────────────────
def _fetch_capture_rows(get_db, date: str) -> list:
    """A messages tábla capture-daemon sorai az adott napra (timestamp prefix-match)."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, subject, message, timestamp, priority FROM messages "
            "WHERE sender = 'capture-daemon' AND substr(timestamp, 1, 10) = ? "
            "ORDER BY timestamp",
            (date,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _count_drafts(get_db, date: str) -> int:
    """draft_log COUNT az adott napra (created_at prefix-match). Üres tábla → 0."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM draft_log WHERE substr(created_at, 1, 10) = ?",
            (date,),
        ).fetchone()
        return int(row["c"]) if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


def _upsert_digest(get_db, date: str, emails_seen: int, needs_reply: int,
                   drafted: int, summary: str, watermark: str):
    """INSERT OR REPLACE a digest_state-be digest_date alapján."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO digest_state "
            "(digest_date, emails_seen, needs_reply, drafted, summary, watermark, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (date, emails_seen, needs_reply, drafted, summary, watermark, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def register_tools(app, deps):
    """Regisztrálja a thread_digest MCP tool-okat és beseedeli az email_digest recipe-t."""
    get_db = deps["get_db"]
    capture_state = deps.get("capture_state", {}) or {}

    # Recipe-seed regisztráláskor (idempotens, IF NOT EXISTS guarddal).
    _seed_digest_recipe(get_db)

    # ──────────────────────────────────────────────────────────────────
    # TOOL 1: thread_summary
    # ──────────────────────────────────────────────────────────────────
    @app.tool()
    async def thread_summary(thread_id: str = "", text: str = "", caller: str = "") -> str:
        """Egy hosszú email-thread tömör magyar összefoglalója + a Kommandant teendője.

        MIKOR HASZNÁLD: ha a Kommandant azt kéri, foglald össze egy email-beszélgetést,
        vagy ha tudni akarja "mi a teendőm" egy hosszú levélváltásban.

        KÉT MÓD (az egyiket kötelező megadni):
          - Add meg a `thread_id`-t (Gmail thread azonosító) → a tool kiolvassa a Gmailből.
            Ez CSAK akkor megy, ha él a Gmail-kapcsolat. Példa: thread_summary(thread_id="18f...")
          - VAGY add meg a `text`-et (a nyers thread-szöveg, akár bemásolva) → ez Gmail
            nélkül is működik. Példa: thread_summary(text="Feladó: ... Tárgy: ... <levél>")

        MIT AD VISSZA (JSON):
          {"summary": "pontosan 3 magyar mondat a thread lényegéről",
           "akcio": "1 mondat: mi a Kommandant teendője, vagy 'nincs teendő'",
           "source": "gmail vagy text"}

        Ha sem thread_id, sem text nincs megadva → 'error' mezőt kapsz vissza.

        Args:
            thread_id: Gmail thread azonosító (opcionális). Csak akkor használd, ha van.
            text: A thread nyers szövege (opcionális). Gmail nélkül is működik.
            caller: A hívó agent/instance azonosítója (naplózáshoz).
        """
        thread_id = (thread_id or "").strip()
        text = (text or "").strip()

        raw = ""
        source = ""

        # 1) Forrás meghatározása: thread_id (Gmail) VAGY text.
        if text:
            raw = text
            source = "text"
        elif thread_id:
            svc = capture_state.get("gmail_service")
            if not svc:
                return json.dumps({
                    "error": "Gmail nem elérhető (nincs service). Add meg a thread "
                             "szövegét a 'text' paraméterben.",
                }, ensure_ascii=False)
            try:
                import asyncio
                raw = await asyncio.to_thread(_read_gmail_thread_sync, svc, thread_id)
                source = "gmail"
            except Exception as e:
                logger.warning("Gmail thread olvasás hiba (%s): %s", thread_id, e)
                return json.dumps({
                    "error": f"Gmail thread olvasás sikertelen: {e}",
                }, ensure_ascii=False)
        else:
            return json.dumps({
                "error": "Adj meg VAGY egy thread_id-t (Gmail), VAGY a thread szövegét (text).",
            }, ensure_ascii=False)

        if not raw.strip():
            return json.dumps({
                "error": "Üres thread-tartalom — nincs mit összefoglalni.",
                "source": source,
            }, ensure_ascii=False)

        # 2) LLM összefoglaló. Ha nincs SF-kulcs → egyszerű fallback (nem hív LLM-et).
        sf_key = deps.get("siliconflow_api_key", "")
        if not sf_key:
            # Graceful degradation: első ~3 mondat kivonata, nincs valódi akció-becslés.
            snippet = raw.strip().replace("\n", " ")
            short = snippet[:400]
            return json.dumps({
                "summary": (f"(LLM nem elérhető — nyers kivonat) {short}"
                            + ("..." if len(snippet) > 400 else "")),
                "akcio": "nincs teendő (LLM nélkül nem becsülhető)",
                "source": source,
            }, ensure_ascii=False)

        system = (
            "Te egy magyar email-asszisztens vagy. KIZÁRÓLAG valid JSON-t válaszolj, "
            "semmi mást. Tömör, lényegre törő, magyar."
        )
        user = (
            "Foglald össze az alábbi email-thread-et. Válaszolj CSAK JSON-ban:\n"
            '{"summary": "PONTOSAN 3 magyar mondat a thread lényegéről",\n'
            '  "akcio": "1 mondat: mi a Kommandant teendője, vagy ha nincs, akkor: nincs teendő"}\n\n'
            "=== THREAD ===\n"
            f"{raw[:8000]}\n"
            "=== THREAD VÉGE ==="
        )
        content = await _sf_chat(deps, system, user, max_tokens=600)
        content = _strip_code_fence(content)

        result = {"summary": "", "akcio": "nincs teendő", "source": source}
        try:
            parsed = json.loads(content)
            result["summary"] = str(parsed.get("summary", "")).strip()
            result["akcio"] = str(parsed.get("akcio", "nincs teendő")).strip() or "nincs teendő"
        except Exception:
            # Ha az LLM nem adott valid JSON-t, a nyers választ summarynak tesszük.
            result["summary"] = content[:600] if content else "(nem sikerült összefoglalni)"

        logger.info("thread_summary kész (source=%s, caller=%s)", source, caller or "?")
        return json.dumps(result, ensure_ascii=False)

    # ──────────────────────────────────────────────────────────────────
    # TOOL 2: email_digest
    # ──────────────────────────────────────────────────────────────────
    @app.tool()
    async def email_digest(date: str = "", caller: str = "") -> str:
        """Napi email-digest: hány email jött, mennyi igényel választ, mennyire van draft.

        MIKOR HASZNÁLD: reggeli batch-összesítéshez, vagy ha a Kommandant azt kéri,
        "mi van a postaládámmal ma". HÍVD MEG paraméter nélkül a mai naphoz: email_digest()

        MIT CSINÁL (lépésről lépésre):
          1) Összegyűjti az aznapi emaileket. Ha él a Gmail → a 'newer_than:1d' szűrővel.
             Ha NEM él a Gmail → a Bridge messages táblájának capture-daemon sorait használja.
          2) Megbecsüli, hány email igényel választ (urgent/important priority, vagy kevés
             email esetén LLM-besorolással).
          3) Megszámolja, hányra van már draft (draft_log tábla).
          4) Elmenti az eredményt a digest_state táblába (egy sor naponta, watermark-kal,
             hogy ne duplikáljon).

        MIT AD VISSZA (JSON):
          {"date": "YYYY-MM-DD", "emails_seen": N, "needs_reply": M, "drafted": K,
           "summary": "magyar, emberi olvasásra szánt összefoglaló",
           "source": "gmail vagy messages_fallback", "watermark": "utolsó feldolgozott id/ts"}

        Ha se Gmail, se capture-daemon email nincs az adott napra → 'error' mezőt kapsz.

        Args:
            date: Dátum YYYY-MM-DD formátumban. Üresen hagyva a mai nap (UTC).
            caller: A hívó agent/instance azonosítója (naplózáshoz).
        """
        date = (date or "").strip() or _today()

        svc = capture_state.get("gmail_service")
        sf_key = deps.get("siliconflow_api_key", "")

        emails = []   # normalizált: {"id", "subject", "priority", "from"}
        source = ""

        # ── (a) Gmail-forrás, ha él a service ──
        if svc:
            try:
                import asyncio
                from email.utils import parseaddr

                def _list_gmail():
                    res = svc.users().messages().list(
                        userId="me", q="newer_than:1d", maxResults=100
                    ).execute()
                    out = []
                    for stub in res.get("messages", []):
                        m = svc.users().messages().get(
                            userId="me", id=stub["id"], format="metadata",
                            metadataHeaders=["From", "Subject"],
                        ).execute()
                        hdrs = {h["name"]: h["value"]
                                for h in m.get("payload", {}).get("headers", [])}
                        _, sender_email = parseaddr(hdrs.get("From", ""))
                        out.append({
                            "id": stub["id"],
                            "subject": hdrs.get("Subject", ""),
                            "from": sender_email,
                            "priority": "",  # Gmailből nincs priority → LLM/heurisztika dönt
                        })
                    return out

                emails = await asyncio.to_thread(_list_gmail)
                source = "gmail"
            except Exception as e:
                logger.warning("Gmail digest list hiba, fallbackelek messages-re: %s", e)
                svc = None  # essünk át a fallback ágra

        # ── (b) messages-fallback (capture-daemon sorok), ha nincs Gmail ──
        if source != "gmail":
            rows = _fetch_capture_rows(get_db, date)
            emails = [{
                "id": str(r["id"]),
                "subject": r.get("subject", ""),
                "from": "capture-daemon",
                "priority": (r.get("priority") or "normal").lower(),
            } for r in rows]
            source = "messages_fallback"

        # ── (c) ha egyik forrásban sincs adat → error ──
        if not emails:
            return json.dumps({
                "error": f"Nincs feldolgozható email a(z) {date} napra "
                         f"(forrás: {source}).",
                "date": date,
                "source": source,
            }, ensure_ascii=False)

        emails_seen = len(emails)

        # ── needs_reply becslés ──
        # Először a priority-alapú heurisztika (urgent/important/high).
        needs_reply = sum(1 for e in emails if e["priority"] in _NEEDS_REPLY_PRIORITIES)

        # Ha kevés az email (<20) ÉS van SF-kulcs ÉS a priority nem volt informatív
        # (pl. Gmail-forrás, ahol nincs priority), LLM-mel finomítunk.
        priority_informative = any(e["priority"] for e in emails)
        if emails_seen < 20 and sf_key and not priority_informative:
            subjects = "\n".join(
                f"{i + 1}. {e['from']} — {e['subject']}" for i, e in enumerate(emails)
            )
            system = (
                "Te egy email-triage rendszer vagy. KIZÁRÓLAG valid JSON-t válaszolj. "
                "Döntsd el, mely emailek igényelnek választ a Kommandanttól."
            )
            user = (
                "Az alábbi emailek közül hány igényel VÁLASZT (személyes/munka, nem hírlevél, "
                "nem automatikus értesítés)? Válaszolj CSAK így:\n"
                '{"needs_reply_count": <egész szám>}\n\n'
                f"{subjects}"
            )
            content = _strip_code_fence(await _sf_chat(deps, system, user, max_tokens=120))
            try:
                parsed = json.loads(content)
                n = int(parsed.get("needs_reply_count", needs_reply))
                needs_reply = max(0, min(n, emails_seen))
            except Exception:
                pass  # marad a heurisztikus érték (itt 0)

        # ── drafted: draft_log COUNT az adott napra ──
        drafted = _count_drafts(get_db, date)

        # ── watermark: utolsó feldolgozott üzenet azonosítója (duplikáció ellen) ──
        watermark = emails[-1]["id"] if emails else ""

        # ── summary: emberi olvasásra szánt magyar szöveg ──
        summary = (
            f"{date}: {emails_seen} email érkezett, ebből {needs_reply} igényel választ, "
            f"{drafted}-ra van már draft. Forrás: "
            f"{'Gmail' if source == 'gmail' else 'capture-daemon (Gmail nélkül)'}."
        )

        # ── upsert digest_state ──
        _upsert_digest(get_db, date, emails_seen, needs_reply, drafted, summary, watermark)

        logger.info("email_digest kész: %s (%d email, %d reply, %d draft, source=%s)",
                    date, emails_seen, needs_reply, drafted, source)

        return json.dumps({
            "date": date,
            "emails_seen": emails_seen,
            "needs_reply": needs_reply,
            "drafted": drafted,
            "summary": summary,
            "source": source,
            "watermark": watermark,
        }, ensure_ascii=False)

    # ──────────────────────────────────────────────────────────────────
    # TOOL 3: digest_history
    # ──────────────────────────────────────────────────────────────────
    @app.tool()
    async def digest_history(limit: int = 7) -> str:
        """A legutóbbi N napi digest a digest_state táblából (legfrissebb elöl).

        MIKOR HASZNÁLD: ha a Kommandant az elmúlt napok email-digestjeit kéri vissza,
        vagy trendet akar látni (pl. "hány email jött a héten").

        Példa hívás: digest_history(limit=7)

        MIT AD VISSZA (JSON):
          {"count": N, "history": [{"date", "emails_seen", "needs_reply",
                                    "drafted", "summary", "watermark"}, ...]}

        Args:
            limit: Hány napot kérj vissza (alap: 7). 1 és 90 között.
        """
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = 7
        limit = max(1, min(limit, 90))

        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT digest_date, emails_seen, needs_reply, drafted, summary, "
                "watermark, created_at FROM digest_state "
                "ORDER BY digest_date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()

        history = [{
            "date": r["digest_date"],
            "emails_seen": r["emails_seen"],
            "needs_reply": r["needs_reply"],
            "drafted": r["drafted"],
            "summary": r["summary"],
            "watermark": r["watermark"],
            "created_at": r["created_at"],
        } for r in rows]

        return json.dumps({"count": len(history), "history": history}, ensure_ascii=False)
