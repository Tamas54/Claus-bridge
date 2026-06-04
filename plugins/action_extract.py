"""
Action Extract Plugin — emailszövegből dátum/teendő kinyerése Calendar/reminder
DRAFT-ként, KIZÁRÓLAG jóváhagyásra.

Biztonsági alapelv: a plugin SOHA nem hoz létre valódi naptáreseményt magától.
Minden kinyert akció status='pending' draftként kerül az action_drafts táblába.
Valódi Google Calendar esemény CSAK akkor jön létre, ha a Kommandant (vagy a
nevében eljáró agent) explicit action_draft_decide(id, 'approve') hívással
jóváhagyja — és csak akkor, ha a calendar_service él.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("plugins.action_extract")

__plugin_meta__ = {
    "name": "action_extract",
    "version": "1.0.0",
    "description": "Emailbol datum/teendo kinyerese -> Calendar/reminder DRAFT (csak jovahagyasra, SOHA nem auto-create)",
}

# Europe/Budapest — UTC+1 (tél) / UTC+2 (nyár). Egyszerű, könyvtár nélküli
# közelítés: a nyári időszámítás kb. március végétől október végéig tart.
_BUDAPEST_SUMMER_MONTHS = {4, 5, 6, 7, 8, 9}  # ezekben a hónapokban biztosan UTC+2


def _budapest_now() -> datetime:
    """Mostani idő Europe/Budapest értelmezésben (közelítő DST-vel)."""
    utc_now = datetime.now(timezone.utc)
    offset_hours = 2 if utc_now.month in _BUDAPEST_SUMMER_MONTHS else 1
    return utc_now + timedelta(hours=offset_hours)


# --- Magyar relatív dátum minták a regex-fallbackhez ---
_WEEKDAYS_HU = {
    "hétfő": 0, "hetfo": 0, "kedd": 1, "szerda": 2, "csütörtök": 3, "csutortok": 3,
    "péntek": 4, "pentek": 4, "szombat": 5, "vasárnap": 6, "vasarnap": 6,
}


def _regex_fallback(text: str) -> list:
    """
    Egyszerű regex-alapú dátumkinyerés, ha nincs SiliconFlow kulcs.
    Felismert minták: ISO dátum (YYYY-MM-DD), "holnap", "ma", hét napjai +
    "-ig/-én/-kor" toldalékos hivatkozások. Minden találat reminder draftként.
    method='regex_fallback' jelöléssel tér vissza.
    """
    now_local = _budapest_now()
    found = []
    low = text.lower()

    # 1) ISO dátumok: 2026-06-12 (opcionálisan idővel)
    for m in re.finditer(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}):(\d{2}))?", text):
        date_part = m.group(1)
        if m.group(2):
            due = f"{date_part}T{int(m.group(2)):02d}:{m.group(3)}:00"
        else:
            due = date_part
        found.append({
            "kind": "reminder",
            "title": f"Teendo: {text.strip()[:60]}",
            "due_at": due,
            "details": "Regex-fallback: ISO datum talalat.",
        })

    # 2) "holnap" / "ma"
    if "holnap" in low:
        due = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
        found.append({
            "kind": "reminder",
            "title": f"Teendo (holnap): {text.strip()[:50]}",
            "due_at": due,
            "details": "Regex-fallback: 'holnap' kulcsszo.",
        })
    elif re.search(r"\bma\b", low):
        due = now_local.strftime("%Y-%m-%d")
        found.append({
            "kind": "reminder",
            "title": f"Teendo (ma): {text.strip()[:50]}",
            "due_at": due,
            "details": "Regex-fallback: 'ma' kulcsszo.",
        })

    # 3) Hét napjai ("péntekig", "kedden", "hétfőn") — a legközelebbi jövőbeli ilyen nap
    for day_word, day_idx in _WEEKDAYS_HU.items():
        # toldalékkal: pénteken, péntekig, péntekre, péntekkor...
        if re.search(r"\b" + re.escape(day_word) + r"(ig|en|on|ön|re|ra|kor|i)?\b", low):
            days_ahead = (day_idx - now_local.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7  # "péntek" mindig a következő ilyen nap, ha ma az van
            target = now_local + timedelta(days=days_ahead)
            found.append({
                "kind": "reminder",
                "title": f"Teendo ({day_word}): {text.strip()[:50]}",
                "due_at": target.strftime("%Y-%m-%d"),
                "details": f"Regex-fallback: '{day_word}' nap-hivatkozas.",
            })
            break  # csak az első napra illesztünk

    return found


async def _read_thread_text(svc, thread_id: str) -> str:
    """Gmail thread összefűzött szövege (snippet-ekből). svc lehet None — ekkor üres."""
    if not svc or not thread_id:
        return ""

    def _fetch():
        thread = svc.users().threads().get(
            userId="me", id=thread_id, format="full"
        ).execute()
        parts = []
        for msg in thread.get("messages", []):
            payload = msg.get("payload", {})
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
            subj = headers.get("Subject", "")
            snippet = msg.get("snippet", "")
            if subj:
                parts.append(f"Targy: {subj}")
            if snippet:
                parts.append(snippet)
        return "\n".join(parts)

    # blokkoló Google-hívás külön szálon — nem fagyasztja az event loopot
    return await asyncio.to_thread(_fetch)


def register_tools(app, deps):
    """Az action_extract MCP toolok regisztrálása a Bridge szerveren."""
    import httpx

    get_db = deps.get("get_db")
    sf_key = deps.get("siliconflow_api_key", "")
    sf_base = deps.get("siliconflow_base_url", "https://api.siliconflow.com/v1")
    sf_timeout = deps.get("siliconflow_timeout", 60)
    sf_models = deps.get("siliconflow_models", {})
    capture_state = deps.get("capture_state", {})

    def _now_utc_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def _extract_with_llm(text: str) -> list:
        """SiliconFlow (deepseek) hívás — JSON-lista akciókkal. Hiba esetén None."""
        today_local = _budapest_now()
        today_str = today_local.strftime("%Y-%m-%d (%A) %H:%M")

        prompt = (
            f"A mai datum es ido (Europe/Budapest): {today_str}.\n"
            f"Az alabbi emailszovegbol nyerd ki az osszes naptarba kivankozo esemenyt es "
            f"teendot/emlekeztetot. A relativ datumokat ('holnap', 'jovo kedd', 'pentekig', "
            f"'10-kor') a fenti mai datumhoz kepest, Europe/Budapest idoben oldd fel.\n\n"
            f"EMAIL SZOVEG:\n{text[:4000]}\n\n"
            f"Valaszolj KIZAROLAG egy valid JSON-listaval (semmi mas, magyarazat nelkul). "
            f"Ha nincs egyetlen esemeny/teendo sem, ures listat adj: []\n"
            f"Minden elem formatuma:\n"
            f'{{"kind": "calendar|reminder", "title": "rovid cim magyarul", '
            f'"due_at": "ISO 8601 datum vagy datum+ido, vagy ures string ha nincs", '
            f'"details": "rovid leiras magyarul"}}\n'
            f"A 'calendar' konkret idoponthoz kotott esemeny, a 'reminder' hatarido/teendo."
        )

        model_id = sf_models.get("deepseek", "deepseek-ai/DeepSeek-V4-Pro")
        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": "Te egy precíz adatkinyerő vagy. KIZÁRÓLAG valid JSON-listát válaszolsz, semmi mást."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 800,
        }
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

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            # néha a modell ```json fejlécet ad
            if content.lower().startswith("json"):
                content = content[4:].strip()

            parsed = json.loads(content)
            if isinstance(parsed, dict):
                # ha objektumot adott "actions" kulccsal, csomagoljuk ki
                parsed = parsed.get("actions") or parsed.get("items") or [parsed]
            if not isinstance(parsed, list):
                return None

            # normalizálás + validáció
            clean = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                kind = item.get("kind", "reminder")
                if kind not in ("calendar", "reminder"):
                    kind = "reminder"
                title = (item.get("title") or "").strip()
                if not title:
                    continue
                clean.append({
                    "kind": kind,
                    "title": title[:200],
                    "due_at": (item.get("due_at") or "").strip(),
                    "details": (item.get("details") or "").strip(),
                })
            return clean
        except Exception as e:
            logger.warning("action_extract LLM hiba: %s", e)
            return None

    @app.tool()
    async def action_extract(text: str = "", thread_id: str = "",
                             source_message_id: str = "", caller: str = "") -> str:
        """Emailszövegből naptári esemény / teendő DRAFT-okat nyer ki — KIZÁRÓLAG jóváhagyásra.

        FONTOS BIZTONSÁGI SZABÁLY: ez a tool SOHA nem hoz létre valódi naptáreseményt.
        Csak 'pending' (függőben lévő) draftokat ír az adatbázisba. A valódi Google
        Calendar esemény CSAK az action_draft_decide(draft_id, 'approve') hívás után jön
        létre, ha a Kommandant jóváhagyja.

        MIKOR HASZNÁLD: ha egy email szövegéből ki kell nyerni a dátumokat/határidőket
        (pl. "jövő péntekig", "holnap 10-kor", "2026-06-12"), és emlékeztetőt vagy
        naptári javaslatot akarsz készíteni.

        LÉPÉSENKÉNT:
          1. Adj meg VAGY egy `text` paramétert (a nyers email szövege — Gmail nélkül is
             működik!), VAGY egy `thread_id`-t (ekkor a Bridge beolvassa a Gmail threadet,
             ha él a kapcsolat).
          2. A tool kinyeri az akciókat és mindegyiket 'pending' draftként elmenti.
          3. A visszakapott draft_ids alapján később az action_drafts_list és
             action_draft_decide toolokkal kezeled őket.

        Példa: action_extract(text='Kedves Tamás, a szerződést kérjük jövő péntekig aláírni...')
          → 1 reminder draft jön létre due_at=jövő péntek dátummal, status='pending'.

        Args:
            text: Az email nyers szövege. Ha üres, a thread_id-t használja.
            thread_id: Gmail thread azonosító (csak ha él a Gmail kapcsolat).
            source_message_id: Opcionális — honnan származik (naplózáshoz, draftba mentve).
            caller: Hívó instance azonosító (naplózáshoz).

        Returns:
            JSON: {"extracted": [...kinyert akciók...], "draft_ids": [...új draft id-k...]}
            Hiba esetén: {"error": "..."}.
        """
        # 1) Forrásszöveg meghatározása
        src_text = text or ""
        if not src_text and thread_id:
            svc = capture_state.get("gmail_service")
            if svc:
                try:
                    src_text = await _read_thread_text(svc, thread_id)
                except Exception as e:
                    logger.warning("action_extract Gmail thread olvasas hiba: %s", e)
                    return json.dumps(
                        {"error": f"Gmail thread olvasas sikertelen: {e}"},
                        ensure_ascii=False,
                    )
            else:
                # graceful degradation: nincs Gmail, és nincs text sem
                return json.dumps(
                    {"error": "Nincs Gmail kapcsolat es nincs 'text' parameter sem. "
                              "Adj meg nyers emailszoveget a 'text' parameterben."},
                    ensure_ascii=False,
                )

        if not src_text.strip():
            return json.dumps(
                {"error": "Ures bemenet: adj meg 'text'-et vagy egy ervenyes 'thread_id'-t."},
                ensure_ascii=False,
            )

        # 2) Kinyerés: LLM, vagy ha nincs kulcs / hiba → regex-fallback
        method = "llm"
        extracted = None
        if sf_key:
            extracted = await _extract_with_llm(src_text)
        if extracted is None:
            method = "regex_fallback"
            extracted = _regex_fallback(src_text)

        # 3) Draftok mentése (status='pending' — SOHA nem auto-create)
        draft_ids = []
        try:
            conn = get_db()
            ts = _now_utc_iso()
            for action in extracted:
                payload_json = json.dumps(
                    {**action, "method": method, "source_message_id": source_message_id},
                    ensure_ascii=False,
                )
                cur = conn.execute(
                    "INSERT INTO action_drafts "
                    "(source_message_id, kind, title, due_at, payload_json, status, created_at, decided_at) "
                    "VALUES (?, ?, ?, ?, ?, 'pending', ?, '')",
                    (source_message_id, action["kind"], action["title"],
                     action.get("due_at", ""), payload_json, ts),
                )
                draft_ids.append(cur.lastrowid)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("action_extract DB INSERT hiba: %s", e)
            return json.dumps({"error": f"Adatbazis hiba: {e}"}, ensure_ascii=False)

        logger.info("action_extract: %d draft (method=%s, caller=%s)",
                    len(draft_ids), method, caller or "?")
        return json.dumps(
            {"extracted": extracted, "draft_ids": draft_ids, "method": method},
            ensure_ascii=False,
        )

    @app.tool()
    async def action_drafts_list(status: str = "pending", limit: int = 20) -> str:
        """Kilistázza az action_drafts táblában lévő (még jóváhagyásra váró) draftokat.

        MIKOR HASZNÁLD: miután action_extract draftokat hozott létre, ezzel nézed meg,
        mi vár jóváhagyásra, mielőtt action_draft_decide-dal döntesz róluk.

        Példa: action_drafts_list(status='pending') → a függőben lévő draftok listája
          id, kind, title, due_at mezőkkel.

        Args:
            status: Szűrő — 'pending' (alap), 'approved', 'rejected', 'created' vagy 'all'.
            limit: Hány draftot adjon vissza (alap: 20).

        Returns:
            JSON: {"count": N, "drafts": [{id, kind, title, due_at, status, created_at, ...}, ...]}
        """
        try:
            conn = get_db()
            if status and status.lower() != "all":
                rows = conn.execute(
                    "SELECT id, source_message_id, kind, title, due_at, payload_json, "
                    "status, created_at, decided_at FROM action_drafts "
                    "WHERE status = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, source_message_id, kind, title, due_at, payload_json, "
                    "status, created_at, decided_at FROM action_drafts "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            conn.close()
        except Exception as e:
            logger.error("action_drafts_list DB hiba: %s", e)
            return json.dumps({"error": f"Adatbazis hiba: {e}"}, ensure_ascii=False)

        drafts = []
        for r in rows:
            drafts.append({
                "id": r["id"],
                "source_message_id": r["source_message_id"],
                "kind": r["kind"],
                "title": r["title"],
                "due_at": r["due_at"],
                "status": r["status"],
                "created_at": r["created_at"],
                "decided_at": r["decided_at"],
            })
        return json.dumps({"count": len(drafts), "drafts": drafts}, ensure_ascii=False)

    @app.tool()
    async def action_draft_decide(draft_id: int, decision: str, caller: str = "") -> str:
        """Egy draftról dönt: jóváhagyás ('approve') vagy elutasítás ('reject').

        FONTOS BIZTONSÁGI SZABÁLY: SOHA nem hoz létre valódi naptáreseményt jóváhagyás
        nélkül. Valódi Google Calendar esemény KIZÁRÓLAG akkor jön létre, ha:
          - decision='approve', ÉS
          - a draft kind='calendar', ÉS
          - él a Google Calendar kapcsolat.
        Ha a Calendar kapcsolat nem él, a draft 'approved' státuszban marad (a Bridge
        create_calendar_event toolja később létrehozhatja) — ezt a válasz jelzi.

        LÉPÉSENKÉNT:
          1. Nézd meg a draftokat action_drafts_list-tel, jegyezd meg a draft id-t.
          2. Ha jó, hívd: action_draft_decide(draft_id=ID, decision='approve').
          3. Ha nem kell, hívd: action_draft_decide(draft_id=ID, decision='reject').

        Példa: action_draft_decide(draft_id=5, decision='reject')
          → az 5-ös draft status='rejected' lesz, semmi nem jön létre.

        Args:
            draft_id: A draft azonosítója (action_drafts_list adja).
            decision: 'approve' (jóváhagyás) vagy 'reject' (elutasítás).
            caller: Hívó instance azonosító (naplózáshoz).

        Returns:
            JSON az eredménnyel: új status, és ha naptáresemény jött létre, az event_id/link.
            Hiba esetén: {"error": "..."}.
        """
        dec = (decision or "").strip().lower()
        if dec not in ("approve", "reject"):
            return json.dumps(
                {"error": "Ervenytelen decision. Hasznald: 'approve' vagy 'reject'."},
                ensure_ascii=False,
            )

        try:
            conn = get_db()
            row = conn.execute(
                "SELECT id, source_message_id, kind, title, due_at, payload_json, status "
                "FROM action_drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
        except Exception as e:
            logger.error("action_draft_decide DB hiba: %s", e)
            return json.dumps({"error": f"Adatbazis hiba: {e}"}, ensure_ascii=False)

        if not row:
            conn.close()
            return json.dumps({"error": f"Nincs ilyen draft: id={draft_id}"}, ensure_ascii=False)

        if row["status"] not in ("pending", "approved"):
            conn.close()
            return json.dumps(
                {"error": f"A draft mar eldontott allapotban van: status={row['status']}",
                 "draft_id": draft_id, "status": row["status"]},
                ensure_ascii=False,
            )

        decided_at = _now_utc_iso()

        # --- ELUTASÍTÁS ---
        if dec == "reject":
            conn.execute(
                "UPDATE action_drafts SET status='rejected', decided_at=? WHERE id=?",
                (decided_at, draft_id),
            )
            conn.commit()
            conn.close()
            logger.info("action_draft_decide: draft #%d REJECTED (caller=%s)", draft_id, caller or "?")
            return json.dumps(
                {"draft_id": draft_id, "status": "rejected", "calendar_created": False},
                ensure_ascii=False,
            )

        # --- JÓVÁHAGYÁS ---
        svc = capture_state.get("calendar_service")

        # Csak calendar típusú draftnál, élő service mellett hozunk létre valódi eseményt.
        if row["kind"] == "calendar" and svc:
            try:
                due = (row["due_at"] or "").strip()
                summary = row["title"]
                # leírás a payloadból
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except Exception:
                    payload = {}
                description = payload.get("details", "")

                if not due:
                    # nincs időpont → nem tudunk Calendar eseményt csinálni; marad approved
                    conn.execute(
                        "UPDATE action_drafts SET status='approved', decided_at=? WHERE id=?",
                        (decided_at, draft_id),
                    )
                    conn.commit()
                    conn.close()
                    return json.dumps(
                        {"draft_id": draft_id, "status": "approved", "calendar_created": False,
                         "note": "Nincs due_at idopont a drafton, nem jott letre naptaresemeny. "
                                 "Allitsd be a due_at-ot, vagy hasznald a Bridge create_calendar_event toolt."},
                        ensure_ascii=False,
                    )

                is_all_day = "T" not in due
                if is_all_day:
                    end_date = (datetime.fromisoformat(due) + timedelta(days=1)).strftime("%Y-%m-%d")
                    event_body = {
                        "summary": summary,
                        "start": {"date": due},
                        "end": {"date": end_date},
                    }
                else:
                    start_dt = datetime.fromisoformat(due)
                    end_dt = (start_dt + timedelta(hours=1)).isoformat()
                    event_body = {
                        "summary": summary,
                        "start": {"dateTime": due},
                        "end": {"dateTime": end_dt},
                    }
                if description:
                    event_body["description"] = description

                # blokkoló Google-hívás külön szálon
                def _insert():
                    return svc.events().insert(calendarId="primary", body=event_body).execute()

                result = await asyncio.to_thread(_insert)

                conn.execute(
                    "UPDATE action_drafts SET status='created', decided_at=? WHERE id=?",
                    (decided_at, draft_id),
                )
                conn.commit()
                conn.close()
                logger.info("action_draft_decide: draft #%d CREATED calendar event (caller=%s)",
                            draft_id, caller or "?")
                return json.dumps(
                    {"draft_id": draft_id, "status": "created", "calendar_created": True,
                     "event_id": result.get("id"), "link": result.get("htmlLink"),
                     "summary": summary, "start": due},
                    ensure_ascii=False,
                )
            except Exception as e:
                logger.error("action_draft_decide naptarletrehozas hiba: %s", e)
                # hiba esetén ne vesszen el a jóváhagyás: marad approved
                conn.execute(
                    "UPDATE action_drafts SET status='approved', decided_at=? WHERE id=?",
                    (decided_at, draft_id),
                )
                conn.commit()
                conn.close()
                return json.dumps(
                    {"draft_id": draft_id, "status": "approved", "calendar_created": False,
                     "error": f"Naptaresemeny letrehozasa sikertelen: {e}. A draft 'approved' maradt."},
                    ensure_ascii=False,
                )

        # Reminder, VAGY nincs élő Calendar kapcsolat → csak 'approved' lesz.
        conn.execute(
            "UPDATE action_drafts SET status='approved', decided_at=? WHERE id=?",
            (decided_at, draft_id),
        )
        conn.commit()
        conn.close()

        if row["kind"] == "calendar" and not svc:
            note = ("Nincs elo Google Calendar kapcsolat, ezert a draft 'approved' allapotban "
                    "maradt. A Bridge create_calendar_event toolja kesobb letrehozhatja.")
        else:
            note = "Reminder draft jovahagyva ('approved'). Nem keszult naptaresemeny."

        logger.info("action_draft_decide: draft #%d APPROVED (no event, kind=%s, caller=%s)",
                    draft_id, row["kind"], caller or "?")
        return json.dumps(
            {"draft_id": draft_id, "status": "approved", "calendar_created": False, "note": note},
            ensure_ascii=False,
        )
