"""
Draft Reply Plugin — Valasz-DRAFT generalas a Kommandant stilusaban.

SOHA NEM KULD EMAILT. Kizarolag Gmail draftot hoz letre (users().drafts().create).
Ez kobe vesett biztonsagi szabaly: a pluginban elo sem fordulhat send() hivas.

A few-shot stilusminta a Kommandant korabbi elkuldott leveleibol jon (from:me),
es a SiliconFlow kimi modell ez alapjan utanozza a hangnemet.
"""

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import parseaddr

logger = logging.getLogger("plugins.draft_reply")

__plugin_meta__ = {
    "name": "draft_reply",
    "version": "1.0.0",
    "description": "Valasz-DRAFT generalas a Kommandant stilusaban -- SOHA nem kuld, csak Gmail draftot hoz letre",
}

# Gmail service hianya eseten ezt adjuk vissza (Google re-auth szukseges).
GMAIL_UNAVAILABLE = {"error": "Gmail service nem elerheto — Google re-auth szukseges"}


def _decode_part_body(part: dict) -> str:
    """Egy MIME part body.data mezojet base64url dekodolja szovegge."""
    body = part.get("body", {})
    data = body.get("data", "")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_plain_text(payload: dict) -> str:
    """
    A message payload-bol kinyeri a text/plain reszt (rekurzivan a parts-bol).
    Ha nincs text/plain, fallbackkent text/html-t prubal (HTML tagek nelkul nem
    tisztitva, csak nyers szoveg).
    """
    mime = payload.get("mimeType", "")

    # Egyszerű (nem multipart) uzenet: a body kozvetlenul itt van.
    if mime == "text/plain":
        return _decode_part_body(payload)

    parts = payload.get("parts", [])

    # Elsodleges: text/plain a parts-ban (rekurzivan).
    for part in parts:
        if part.get("mimeType") == "text/plain":
            txt = _decode_part_body(part)
            if txt:
                return txt
        if part.get("parts"):
            txt = _extract_plain_text(part)
            if txt:
                return txt

    # Fallback: text/html, ha semmi text/plain nincs.
    for part in parts:
        if part.get("mimeType") == "text/html":
            html = _decode_part_body(part)
            if html:
                return html

    # Vegso fallback: a top-level body (pl. text/html egyszeru uzenet).
    return _decode_part_body(payload)


def _headers_to_dict(payload: dict) -> dict:
    """A payload headers listajat dict-te alakitja (a header-nevek megtartasaval)."""
    return {h.get("name", ""): h.get("value", "") for h in payload.get("headers", [])}


def register_tools(app, deps):
    """Regisztralja a draft_reply es draft_reply_log MCP toolokat."""
    import httpx

    get_db = deps.get("get_db")
    sf_key = deps.get("siliconflow_api_key", "")
    sf_base = deps.get("siliconflow_base_url", "https://api.siliconflow.com/v1")
    sf_timeout = deps.get("siliconflow_timeout", 60)
    sf_models = deps.get("siliconflow_models", {})
    capture_state = deps.get("capture_state", {})

    def _gmail_service():
        """A jelenlegi Gmail service objektum, vagy None ha nincs (re-auth kell)."""
        return (capture_state or {}).get("gmail_service")

    async def _fetch_thread(svc, thread_id: str) -> dict:
        """Blokkolo Google-hivas thread-re, kulon szalon (asyncio.to_thread)."""
        return await asyncio.to_thread(
            lambda: svc.users().threads().get(
                userId="me", id=thread_id, format="full"
            ).execute()
        )

    async def _fetch_few_shot(svc, recipient_email: str) -> list:
        """
        Few-shot stilusminta: a Kommandant utolso max 3 elkuldott levele a cimzettnek.
        Ha a cimzetthez nincs elozmeny, altalanos from:me mintat hoz.
        Visszaad: list[str] -- a levelek nyers szovege (max 1500 char/level).
        """
        async def _list_and_read(query: str) -> list:
            def _blocking():
                res = svc.users().messages().list(
                    userId="me", q=query, maxResults=3
                ).execute()
                texts = []
                for stub in res.get("messages", []):
                    msg = svc.users().messages().get(
                        userId="me", id=stub["id"], format="full"
                    ).execute()
                    body = _extract_plain_text(msg.get("payload", {}))
                    if body.strip():
                        texts.append(body.strip()[:1500])
                return texts
            return await asyncio.to_thread(_blocking)

        samples = []
        if recipient_email:
            try:
                samples = await _list_and_read(f"from:me to:{recipient_email}")
            except Exception as e:
                logger.warning("Few-shot (to:%s) lekerdezes hiba: %s", recipient_email, e)

        # Ha nincs cimzett-specifikus minta, altalanos elkuldott levelek.
        if not samples:
            try:
                samples = await _list_and_read("from:me")
            except Exception as e:
                logger.warning("Few-shot (altalanos from:me) lekerdezes hiba: %s", e)

        return samples[:3]

    async def _generate_body(last_message: str, samples: list, instruction: str) -> str:
        """
        SiliconFlow chat-hivas (kimi default) -- valasz-email torzs generalasa
        a Kommandant hangjan, a few-shot mintak alapjan.
        """
        if not sf_key:
            # AI nelkul nem tudunk ertelmes draftot generalni.
            return ""

        # Few-shot blokk osszeallitasa a promptba.
        if samples:
            few_shot_block = "\n\n".join(
                f"--- Korabbi level #{i + 1} ---\n{s}" for i, s in enumerate(samples)
            )
        else:
            few_shot_block = "(Nincs elerheto korabbi level — alapertelmezett, tomör, udvarias magyar hangnem.)"

        instruction_line = (
            f"\nEmberi iranymutatas a valaszhoz: {instruction.strip()}\n"
            if instruction and instruction.strip()
            else ""
        )

        system_prompt = (
            "Te a Kommandant (Tamas) szemelyi email-asszisztense vagy. A feladatod: "
            "megirni egy valasz-email TORZSET a Kommandant sajat hangjan. "
            "A Kommandant korabbi leveleit kapod mintaul — utanozd a stilusat: "
            "a megszolitast (tegezodes/magazodas), a tomorseget, az udvozlesi es "
            "elkoszonesi formulat, a hangnemet. A valasz erdemi legyen es a thread "
            "utolso uzenetere reagaljon. KIZAROLAG a valasz-email torzset add vissza — "
            "semmi meta, semmi 'Targy:', semmi magyarazat, semmi idezojel."
        )

        user_prompt = (
            f"A Kommandant korabbi levelei — ezt a stilust utanozd:\n\n{few_shot_block}\n\n"
            f"=== A level, amire valaszolni kell (a thread utolso uzenete) ===\n"
            f"{last_message[:4000]}\n"
            f"{instruction_line}\n"
            f"Most ird meg a valasz-email torzset a Kommandant hangjan:"
        )

        model_id = sf_models.get("kimi", "moonshotai/Kimi-K2.6")

        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.6,
            "max_tokens": 1200,
        }
        # Kimi-nel a thinking-et kikapcsoljuk (gyorsabb, sima szoveg-output).
        if "Kimi" in model_id:
            payload["thinking"] = {"type": "disabled"}
        elif "DeepSeek" in model_id:
            payload["reasoning_effort"] = "medium"

        try:
            async with httpx.AsyncClient(timeout=sf_timeout) as client:
                resp = await client.post(
                    f"{sf_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {sf_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                data = json.loads(resp.text)
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            content = content.strip()
            # Esetleges kodblokk-burok eltavolitasa.
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return content
        except Exception as e:
            logger.warning("SiliconFlow draft-generalas hiba: %s", e)
            return ""

    @app.tool()
    async def draft_reply(thread_id: str, instruction: str = "", caller: str = "") -> str:
        """Valasz-DRAFT keszitese egy fontos emailre a Kommandant (Tamas) stilusaban.

        FONTOS: Ez a tool SOHA nem kuld emailt — kizarolag egy DRAFTOT (vazlatot) hoz
        letre a Gmail Drafts (Vazlatok) mappaban. A Kommandant kesobb maga atnezi es
        elkuldi. A pluginban send() hivas NEM letezik.

        Mit csinal lepesenkent:
        1. Beolvassa a thread-et a thread_id alapjan (a thread utolso uzenetere fog valaszolni).
        2. Lekeri a Kommandant korabbi elkuldott leveleit a cimzettnek (max 3) — ezekbol
           tanulja meg a stilust (tegezodes/magazodas, tomorseg, udvozles).
        3. Megirja a valasz torzset a Kommandant hangjan (SiliconFlow kimi modell).
        4. Letrehozza a Gmail draftot (To = eredeti felado, Subject = "Re: ...").
        5. Naplozza a draft_log tablaba.

        Parameterek:
            thread_id: A Gmail thread azonositoja (a thread-bol jon, amire valaszolni kell).
            instruction: Opcionalis emberi iranymutatas, hogy mit tartalmazzon a valasz.
                Pl. "udvariasan utasitsd el", "kerj 1 het haladekot", "koszond meg".
                Ha ures, az AI maga dont a thread tartalma alapjan.
            caller: A hivó instance azonositoja (naplozashoz).

        Pelda 1: draft_reply(thread_id='18c2a4f9b7e0d123', instruction='koszond meg es igazold vissza a hataridot')
        Pelda 2: draft_reply(thread_id='18c2a4f9b7e0d123', instruction='udvariasan utasitsd el a meghivast')
        Pelda 3: draft_reply(thread_id='18c2a4f9b7e0d123')   # instruction nelkul, AI dont

        Visszaad (JSON):
            {"draft_id": ..., "thread_id": ..., "recipient": ..., "subject": ..., "body_preview": ...}
            vagy {"error": "..."} ha a Gmail service nem elerheto (Google re-auth kell)
            vagy ha a draft generalasa/letrehozasa nem sikerult.
        """
        svc = _gmail_service()
        if svc is None:
            # Graceful degradation: Google token lejart -> nincs Gmail service.
            return json.dumps(GMAIL_UNAVAILABLE, ensure_ascii=False)

        # --- 1. Thread beolvasasa (blokkolo Google-hivas kulon szalon) ---
        try:
            thread = await _fetch_thread(svc, thread_id)
        except Exception as e:
            logger.error("Thread beolvasas hiba (%s): %s", thread_id, e)
            return json.dumps({"error": f"Thread beolvasas sikertelen: {e}"}, ensure_ascii=False)

        messages = thread.get("messages", [])
        if not messages:
            return json.dumps({"error": "A thread ures vagy nem letezik"}, ensure_ascii=False)

        # A thread utolso uzenete az, amire valaszolunk.
        last_msg = messages[-1]
        last_payload = last_msg.get("payload", {})
        last_headers = _headers_to_dict(last_payload)

        from_raw = last_headers.get("From", "")
        _sender_name, sender_email = parseaddr(from_raw)
        recipient = sender_email or from_raw or "unknown"

        orig_subject = last_headers.get("Subject", "") or "(no subject)"
        if orig_subject.lower().startswith("re:"):
            reply_subject = orig_subject
        else:
            reply_subject = f"Re: {orig_subject}"

        # In-Reply-To / References headerök a thread-eleshez.
        msg_id_header = last_headers.get("Message-ID") or last_headers.get("Message-Id", "")
        references = last_headers.get("References", "")

        last_body = _extract_plain_text(last_payload) or last_msg.get("snippet", "")

        # --- 2. Few-shot stilusminta a Gmail historybol ---
        samples = await _fetch_few_shot(svc, sender_email)
        if samples:
            style_notes = f"{len(samples)} korabbi level a cimzettnek (vagy altalanos from:me) alapjan"
        else:
            style_notes = "nincs few-shot minta — alapertelmezett magyar hangnem"

        # --- 3. SiliconFlow chat-hivas: valasz torzs generalasa ---
        body_text = await _generate_body(last_body, samples, instruction)
        if not body_text:
            return json.dumps(
                {"error": "A valasz-draft generalasa nem sikerult (SiliconFlow nem elerheto vagy ures valasz)"},
                ensure_ascii=False,
            )

        # --- 4. MIME uzenet + Gmail draft letrehozasa (NEM send!) ---
        try:
            mime = MIMEText(body_text, "plain", "utf-8")
            mime["To"] = recipient
            mime["Subject"] = reply_subject
            if msg_id_header:
                mime["In-Reply-To"] = msg_id_header
                # A References-be fuzzuk az eddigieket + az aktualis Message-ID-t.
                refs = (references + " " + msg_id_header).strip() if references else msg_id_header
                mime["References"] = refs

            raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()

            draft = await asyncio.to_thread(
                lambda: svc.users().drafts().create(
                    userId="me",
                    body={"message": {"raw": raw, "threadId": thread_id}},
                ).execute()
            )
        except Exception as e:
            logger.error("Draft letrehozas hiba (%s): %s", thread_id, e)
            return json.dumps({"error": f"Draft letrehozas sikertelen: {e}"}, ensure_ascii=False)

        draft_id = draft.get("id", "")
        body_preview = body_text[:200]

        # --- 5. Naplozas a draft_log tablaba ---
        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO draft_log "
                "(thread_id, draft_id, recipient, subject, body_preview, style_notes, created_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    draft_id,
                    recipient,
                    reply_subject,
                    body_preview,
                    style_notes,
                    datetime.now(timezone.utc).isoformat(),
                    caller or "unknown",
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            # A draft mar elkeszult — a naplozas hibaja nem buktatja a toolt.
            logger.warning("draft_log INSERT hiba: %s", e)

        logger.info("Draft keszult: thread=%s recipient=%s draft_id=%s", thread_id, recipient, draft_id)

        return json.dumps(
            {
                "draft_id": draft_id,
                "thread_id": thread_id,
                "recipient": recipient,
                "subject": reply_subject,
                "body_preview": body_preview,
            },
            ensure_ascii=False,
        )

    @app.tool()
    async def draft_reply_log(limit: int = 20) -> str:
        """A legutobb keszitett valasz-draftok naploja (draft_log tabla).

        Megmutatja, milyen valasz-vazlatok keszultek korabban: kinek, milyen targgyal,
        milyen stilusminta alapjan. Ez NEM kuld es NEM hoz letre semmit — csak olvas.

        Parameterek:
            limit: Hany utolso bejegyzes jojjon vissza (alapertelmezett 20).

        Pelda: draft_reply_log(limit=10)

        Visszaad (JSON-lista), legujabb elol:
            [{"id":..., "thread_id":..., "draft_id":..., "recipient":..., "subject":...,
              "body_preview":..., "style_notes":..., "created_at":..., "created_by":...}, ...]
        """
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 20
        if n <= 0:
            n = 20

        try:
            conn = get_db()
            rows = conn.execute(
                "SELECT id, thread_id, draft_id, recipient, subject, body_preview, "
                "style_notes, created_at, created_by "
                "FROM draft_log ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
            conn.close()
        except Exception as e:
            logger.warning("draft_log lekerdezes hiba: %s", e)
            return json.dumps([], ensure_ascii=False)

        out = [dict(r) for r in rows]
        return json.dumps(out, ensure_ascii=False)
