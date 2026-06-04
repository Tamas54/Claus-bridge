"""
Semantic Triage Plugin — embedding-alapú email-besorolás SiliconFlow-val.

Ez a plugin a régi kulcsszavas/whitelist email_triage.py-t váltja ki SZEMANTIKUSAN:
nem szavakat keres, hanem MEGÉRTI a jelentést. A "holnapig kérem a választ"
sürgős akkor is, ha nincs benne a "deadline" / "sürgős" szó — mert a mondat
embeddingje közel esik a 'urgent' kategória prototípus-mondataihoz.

Működés dióhéjban:
  1. Minden kategóriához (urgent/important/normal/ignore) van 6-10 prototípus-mondat.
  2. Első híváskor a prototípusok embeddingjét lekérjük és cache-eljük (lazy!).
     Kategória-centroid = az adott kategória prototípus-vektorainak átlaga.
  3. Bejövő email (subject + body első 1000 karaktere) → 1 embedding.
  4. Cosine-hasonlóság a 4 centroidhoz → legjobb kategória + score.
  5. Határeset (borderline): top-2 score-különbség < 0.04 VAGY top score < 0.45.
     Opcionálisan (use_gatekeeper=True) egy DeepSeek chat-hívás dönt a határesetről.
  6. Minden besorolás bekerül a semantic_triage_log táblába.

API kulcs nélkül → kulcsszavas fallback (a régi email_triage.py logikája).
"""

import json
import logging
import math
import os
from datetime import datetime, timezone

logger = logging.getLogger("plugins.semantic_triage")

__plugin_meta__ = {
    "name": "semantic_triage",
    "version": "1.0.0",
    "description": "Szemantikus (embedding) email-besorolas -- a kulcsszavas triage utodja",
}

# Embedding modell (SiliconFlow): BAAI/bge-m3 -- jol kezeli a magyar+angol kevert szoveget.
EMBEDDING_MODEL = "BAAI/bge-m3"

# A border-eset kuszobok (lasd docstring).
BORDERLINE_MARGIN = 0.04   # top-2 score-kulonbseg ennel kisebb -> hatareset
BORDERLINE_MIN_TOP = 0.45  # top score ennel kisebb -> hatareset

# ---------------------------------------------------------------------------
# Kategoria prototipus-mondatok (magyar ES angol vegyesen, valodi email-szovegszeruen).
# Ezek embeddingjebol atlagolva keszul a kategoria-centroid.
# ---------------------------------------------------------------------------
CATEGORY_PROTOTYPES = {
    "urgent": [
        "holnapig kérem a választ erre a levélre",
        "please respond by EOD, this is time-sensitive",
        "a szerződés aláírásra vár, ma le kell zárnunk",
        "sürgős: a rendszer leállt, azonnali beavatkozás kell",
        "the deadline is tomorrow morning, we need your approval now",
        "azonnal hívjon vissza, kritikus probléma merült fel",
        "final reminder: payment is overdue and due today",
        "kérlek még ma intézd el, különben lecsúszunk a határidőről",
        "urgent action required to avoid account suspension",
        "a holnapi prezentációhoz ma estig kellenek az adatok",
    ],
    "important": [
        "csatolom a szerződéstervezetet, kérlek nézd át a hét folyamán",
        "please review the attached proposal before our meeting",
        "egyeztessünk időpontot a jövő heti megbeszélésre",
        "your invoice is attached, payment due in 14 days",
        "csatolom a számlát, fizetési határidő két hét",
        "a projekt következő fázisáról szeretnék veled beszélni",
        "could you share your feedback on the new plan?",
        "fontos frissítés a megrendeléseddel kapcsolatban",
        "the client asked for a follow-up on last week's call",
        "kérlek erősítsd meg, hogy megkaptad a dokumentumokat",
    ],
    "normal": [
        "köszönöm a tegnapi segítséget, nagyon hasznos volt",
        "thanks for the update, sounds good to me",
        "csak jelezni szerettem volna, hogy minden rendben halad",
        "here are the notes from today's standup meeting",
        "megosztom veled a hétvégi program részleteit",
        "just checking in, no rush on this one",
        "rendben, akkor a jövő héten beszélünk",
        "fyi, the document has been uploaded to the shared drive",
        "jó hétvégét kívánok, hétfőn folytatjuk",
        "received your message, will get back to you soon",
    ],
    "ignore": [
        "weekly newsletter: top stories you might have missed",
        "unsubscribe anytime by clicking the link below",
        "leiratkozás a hírlevélről egyetlen kattintással",
        "huge discount! 50% off everything this weekend only",
        "you have been selected to win a free prize, claim now",
        "no-reply automated notification, do not respond",
        "heti hírlevelünk: a legfrissebb ajánlatok és akciók",
        "promóciós ajánlat: csak ma, ne maradj le róla",
        "your subscription has been renewed automatically",
        "automatikus értesítés, erre az üzenetre ne válaszoljon",
    ],
}

# ---------------------------------------------------------------------------
# Module-szintu cache (lazy): a centroidok elso hivaskor szamolodnak ki.
# import-kor NEM hivunk halozatot!
# ---------------------------------------------------------------------------
_CENTROID_CACHE = {}  # {kategoria: [float, ...]}  -- normalt centroid-vektor


# ---------------------------------------------------------------------------
# Kulcsszavas fallback (a regi email_triage.py _fallback_categorize mintaja).
# Akkor fut, ha nincs SiliconFlow API kulcs.
# ---------------------------------------------------------------------------
IGNORE_SENDERS = [s.strip().lower() for s in os.environ.get(
    "IGNORE_SENDERS", "noreply,no-reply,mailer-daemon,newsletter").split(",") if s.strip()]
URGENT_SENDERS = [s.strip().lower() for s in os.environ.get(
    "URGENT_SENDERS", "").split(",") if s.strip()]
URGENT_KEYWORDS = [k.strip().lower() for k in os.environ.get(
    "URGENT_KEYWORDS", "urgent,surgos,sürgős,fontos,deadline,hatarido,határidő,holnapig").split(",") if k.strip()]
IGNORE_KEYWORDS = [k.strip().lower() for k in os.environ.get(
    "IGNORE_KEYWORDS", "newsletter,hirlevel,hírlevél,unsubscribe,leiratkoz,promo,akció,discount").split(",") if k.strip()]


def _fallback_categorize(sender: str, subject: str, body: str = "") -> dict:
    """Kulcsszavas fallback, ha az embedding API nem elerheto.

    A regi email_triage.py logikajat koveti, de a 4 uj kategoriara
    (urgent/important/normal/ignore) kepez le.
    """
    sender_lower = (sender or "").lower()
    text_lower = ((subject or "") + " " + (body or "")).lower()

    for pattern in IGNORE_SENDERS:
        if pattern and pattern in sender_lower:
            return {"category": "ignore", "score": 0.0}
    for kw in IGNORE_KEYWORDS:
        if kw and kw in text_lower:
            return {"category": "ignore", "score": 0.0}
    for pattern in URGENT_SENDERS:
        if pattern and pattern in sender_lower:
            return {"category": "urgent", "score": 0.0}
    for kw in URGENT_KEYWORDS:
        if kw and kw in text_lower:
            return {"category": "urgent", "score": 0.0}
    return {"category": "normal", "score": 0.0}


# ---------------------------------------------------------------------------
# Vektor-segedfuggvenyek (tiszta Python, NINCS numpy!).
# ---------------------------------------------------------------------------
def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _norm(a):
    return math.sqrt(sum(x * x for x in a))


def _cosine(a, b):
    """Cosine-hasonlosag ket vektor kozott. Ures/nulla vektorra 0.0."""
    na = _norm(a)
    nb = _norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return _dot(a, b) / (na * nb)


def _mean_vectors(vectors):
    """Vektorok elemenkenti atlaga (centroid). Felteszi, hogy mind azonos hosszu."""
    n = len(vectors)
    if n == 0:
        return []
    dim = len(vectors[0])
    out = [0.0] * dim
    for v in vectors:
        for i in range(dim):
            out[i] += v[i]
    return [x / n for x in out]


def register_tools(app, deps):
    """A szemantikus triage MCP toolok regisztralasa."""
    import httpx

    sf_key = deps.get("siliconflow_api_key", "")
    sf_base = deps.get("siliconflow_base_url", "https://api.siliconflow.com/v1")
    sf_timeout = deps.get("siliconflow_timeout", 30)
    sf_models = deps.get("siliconflow_models", {})
    get_db = deps.get("get_db")

    # -----------------------------------------------------------------------
    # Belso: embeddingek lekerese a SiliconFlow embeddings API-rol.
    # POST {base_url}/embeddings, model=BAAI/bge-m3, input=[szoveg, ...]
    # -----------------------------------------------------------------------
    async def _embed(texts):
        """Visszaad egy listat embedding-vektorokrol (input sorrendben)."""
        payload = {"model": EMBEDDING_MODEL, "input": texts}
        async with httpx.AsyncClient(timeout=sf_timeout) as client:
            resp = await client.post(
                f"{sf_base}/embeddings",
                headers={
                    "Authorization": f"Bearer {sf_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            data = json.loads(resp.text)
        # Az API "data" listat ad vissza, mindegyikben "embedding" + "index".
        items = sorted(data["data"], key=lambda d: d.get("index", 0))
        return [it["embedding"] for it in items]

    async def _ensure_centroids():
        """Lazy: elso hivaskor kiszamolja es cache-eli a kategoria-centroidokat.

        Ezert nem fut halozati hivas import-kor — csak az elso tool-hivasnal.
        """
        if _CENTROID_CACHE:
            return
        for category, prototypes in CATEGORY_PROTOTYPES.items():
            vecs = await _embed(prototypes)
            _CENTROID_CACHE[category] = _mean_vectors(vecs)
        logger.info("Semantic triage centroidok kiszamolva: %d kategoria", len(_CENTROID_CACHE))

    def _log_to_db(message_id, sender, subject, category, score, method, borderline, gatekeeper_called):
        """Egy besorolas naplozasa a semantic_triage_log tablaba.

        A message_id NOT NULL -> ures helyett placeholdert teszunk.
        Hiba eseten csak warningol, nem dob fel kivetelt a hivonak.
        """
        if not get_db:
            return
        try:
            conn = get_db()
            conn.execute(
                """INSERT INTO semantic_triage_log
                   (message_id, sender, subject, category, score, method,
                    borderline, gatekeeper_called, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id or "no-id",
                    sender or "",
                    (subject or "")[:500],
                    category,
                    float(score),
                    method,
                    1 if borderline else 0,
                    1 if gatekeeper_called else 0,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("semantic_triage_log INSERT failed: %s", e)

    async def _gatekeeper(sender, subject, body):
        """Hatareset-eldonto DeepSeek chat-hivas (kauzalis-hid keretrendszer).

        A jel (X) az email tartalma; a kimenet (Y) az, hogy 'a Kommandantnak
        sürgősen cselekednie kell'. A modelltol verdiktet kerunk
        (definial / oksagi_hid / csak_korrelal) + kategoria-javaslatot.
        """
        model_id = sf_models.get("deepseek", "deepseek-ai/DeepSeek-V4-Pro")
        prompt = (
            "Egy email-besorolo rendszer hataresetet kell eldontened.\n\n"
            "Keretrendszer: van egy JEL (X) es egy KIMENET (Y).\n"
            f"X (a jel) = ez az email:\n"
            f"  Felado: {sender}\n  Targy: {subject}\n  Tartalom: {body[:800]}\n\n"
            "Y (a kimenet) = 'a Kommandantnak SURGOSEN cselekednie kell emiatt'.\n\n"
            "Kerdes: fontos-e ez a jel? Vagyis az X jel mennyire kapcsolodik az Y kimenethez?\n"
            "Add meg a verdiktet az alabbi harom kategoria egyikeben:\n"
            "  - 'definial': X gyakorlatilag definialja Y-t (biztosan surgos cselekves kell)\n"
            "  - 'oksagi_hid': X oksagilag Y-hoz vezet (valoszinuleg cselekedni kell)\n"
            "  - 'csak_korrelal': X csak lazan kapcsolodik Y-hoz (nem igazi surgosseg)\n\n"
            "Valaszolj KIZAROLAG valid JSON-ben, semmi mas:\n"
            '{"verdict": "definial|oksagi_hid|csak_korrelal", '
            '"category": "urgent|important|normal|ignore", '
            '"reason_hu": "max 1 mondat indoklas magyarul"}'
        )
        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": "Te egy email-triage hatareset-eldonto vagy. KIZAROLAG valid JSON-t valaszolsz."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 300,
        }
        if "DeepSeek" in model_id:
            payload["reasoning_effort"] = "medium"
        elif "Kimi" in model_id:
            payload["thinking"] = {"type": "disabled"}
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
            return json.loads(content)
        except Exception as e:
            logger.warning("Gatekeeper hivas sikertelen: %s", e)
            return None

    async def _classify(sender, subject, body):
        """Belso besorolo: visszaad (category, score, ranked_list, method).

        ranked_list = [(kategoria, score), ...] csokkeno score szerint.
        """
        await _ensure_centroids()
        text = f"{subject}\n{body}"[:1000]
        vecs = await _embed([text])
        email_vec = vecs[0]
        scored = []
        for category, centroid in _CENTROID_CACHE.items():
            scored.append((category, _cosine(email_vec, centroid)))
        scored.sort(key=lambda kv: kv[1], reverse=True)
        top_cat, top_score = scored[0]
        return top_cat, top_score, scored, "embedding"

    # =======================================================================
    # MCP TOOL 1: semantic_triage
    # =======================================================================
    @app.tool()
    async def semantic_triage(
        sender: str,
        subject: str,
        body: str = "",
        message_id: str = "",
        use_gatekeeper: bool = False,
    ) -> str:
        """Egy emailt szemantikusan (jelentés alapján) besorol 4 kategória egyikébe.

        MIT CSINÁL EZ A TOOL (lépésről lépésre):
          1. Veszi az email tárgyát (subject) és törzsét (body), összefűzi őket.
          2. Egy embedding-modellel (BAAI/bge-m3) vektorrá alakítja a szöveget.
          3. Összehasonlítja 4 kategória "minta-jelentésével" (urgent / important /
             normal / ignore) cosine-hasonlósággal.
          4. A legjobban illeszkedő kategóriát adja vissza egy score-ral (0.0–1.0).
          5. Ha bizonytalan (határeset), borderline=true lesz; ekkor opcionálisan
             egy második AI-modell (gatekeeper) eldönti a vitát.
          6. A besorolást elmenti egy naplóba.

        FONTOS: NEM kulcsszavakat keres! A "holnapig kérem a választ" akkor is
        URGENT lesz, ha nincs benne a "sürgős" vagy "deadline" szó — mert a
        JELENTÉSE sürgős.

        PARAMÉTEREK:
          sender (kötelező):  a feladó email-címe vagy neve. Pl.: "fonok@ceg.hu"
          subject (kötelező): az email tárgya. Pl.: "Holnapi prezentáció adatai"
          body (opcionális):  az email szövege. Csak az első 1000 karaktert nézi.
                              Üresen hagyható, ekkor csak a tárgyból dolgozik.
          message_id (opcionális): az email egyedi azonosítója a naplóhoz. Pl.: "msg-4821"
          use_gatekeeper (opcionális): true/false. Ha true ÉS az eset határeset,
                              akkor egy második AI-modell (DeepSeek) is megnézi.
                              Alapból false (gyorsabb, olcsóbb).

        PÉLDA HÍVÁS:
          semantic_triage(
            sender="igazgato@partner.hu",
            subject="Szerződés aláírásra vár",
            body="Kedves Kommandant, a mellékelt szerződést kérem ma estig írja alá.",
            use_gatekeeper=true
          )

        PÉLDA VÁLASZ (JSON szöveg):
          {"category": "urgent", "score": 0.71, "borderline": false,
           "method": "embedding", "gatekeeper": null}

        VISSZATÉRÉSI ÉRTÉK (JSON szöveg, mezők):
          category:   "urgent" | "important" | "normal" | "ignore"
          score:      0.0–1.0 közötti szám, mennyire biztos a besorolás
          borderline: true ha bizonytalan/határeset volt, különben false
          method:     "embedding" (normál) vagy "keyword_fallback" (nincs API kulcs)
          gatekeeper: null, vagy {"verdict":..., "category":..., "reason_hu":...}
                      ha lefutott a második AI-modell
        """
        # --- Fallback ag: nincs API kulcs -> kulcsszavas logika ---
        if not sf_key:
            fb = _fallback_categorize(sender, subject, body)
            _log_to_db(message_id, sender, subject, fb["category"], fb["score"],
                       "keyword_fallback", borderline=False, gatekeeper_called=False)
            logger.info("Semantic triage (fallback): %s -> %s", (subject or "")[:40], fb["category"])
            return json.dumps({
                "category": fb["category"],
                "score": fb["score"],
                "borderline": False,
                "method": "keyword_fallback",
                "gatekeeper": None,
            }, ensure_ascii=False)

        # --- Normal embedding ag ---
        try:
            top_cat, top_score, ranked, method = await _classify(sender, subject, body)
        except Exception as e:
            # Ha az embedding API elszall, esunk vissza kulcsszavakra.
            logger.warning("Embedding besorolas sikertelen, kulcsszavas fallback: %s", e)
            fb = _fallback_categorize(sender, subject, body)
            _log_to_db(message_id, sender, subject, fb["category"], fb["score"],
                       "keyword_fallback", borderline=False, gatekeeper_called=False)
            return json.dumps({
                "category": fb["category"],
                "score": fb["score"],
                "borderline": False,
                "method": "keyword_fallback",
                "gatekeeper": None,
            }, ensure_ascii=False)

        # --- Hatareset-detektalas ---
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = top_score - second_score
        borderline = (margin < BORDERLINE_MARGIN) or (top_score < BORDERLINE_MIN_TOP)

        gatekeeper_result = None
        gatekeeper_called = False
        # Opcionalis gatekeeper csak ha hatareset ES a hivo kerte.
        if borderline and use_gatekeeper:
            gatekeeper_result = await _gatekeeper(sender, subject, body)
            gatekeeper_called = gatekeeper_result is not None
            # Ha a gatekeeper adott kategoria-javaslatot, azt fogadjuk el.
            if gatekeeper_result and gatekeeper_result.get("category") in CATEGORY_PROTOTYPES:
                top_cat = gatekeeper_result["category"]

        _log_to_db(message_id, sender, subject, top_cat, top_score,
                   method, borderline, gatekeeper_called)

        logger.info("Semantic triage: %s -> %s (score=%.3f, border=%s, gk=%s)",
                    (subject or "")[:40], top_cat, top_score, borderline, gatekeeper_called)

        return json.dumps({
            "category": top_cat,
            "score": round(top_score, 4),
            "borderline": borderline,
            "method": method,
            "gatekeeper": gatekeeper_result,
        }, ensure_ascii=False)

    # =======================================================================
    # MCP TOOL 2: semantic_triage_stats
    # =======================================================================
    @app.tool()
    async def semantic_triage_stats(days: int = 7) -> str:
        """Összesíti az elmúlt N nap email-besorolásait kategóriánként.

        MIT CSINÁL EZ A TOOL:
          Megnézi a triage-naplót, és kategóriánként (urgent/important/normal/
          ignore) megszámolja, hány emailt soroltunk be az elmúlt N napban.
          Megadja az átlag-score-t, a határesetek és a gatekeeper-hívások számát is.

        PARAMÉTEREK:
          days (opcionális): hány napra visszamenőleg összesítsen. Alapból 7.
                             Pl. days=1 = csak ma, days=30 = utolsó hónap.

        PÉLDA HÍVÁS:
          semantic_triage_stats(days=7)

        PÉLDA VÁLASZ (JSON szöveg):
          {"days": 7, "total": 42,
           "by_category": [
             {"category": "urgent", "count": 5, "avg_score": 0.68,
              "borderline_count": 1, "gatekeeper_count": 1},
             {"category": "normal", "count": 30, "avg_score": 0.55,
              "borderline_count": 3, "gatekeeper_count": 0}
           ]}

        VISSZATÉRÉSI ÉRTÉK (JSON szöveg):
          days:        a lekérdezett napok száma
          total:       összes besorolt email az időszakban
          by_category: lista, kategóriánként count / avg_score /
                       borderline_count / gatekeeper_count mezőkkel
        """
        if not get_db:
            return json.dumps({"error": "no_db", "days": days, "total": 0, "by_category": []},
                              ensure_ascii=False)
        try:
            conn = get_db()
            # SQLite datetime osszehasonlitas ISO stringen mukodik (UTC).
            cutoff = f"-{int(days)} days"
            rows = conn.execute(
                """SELECT category,
                          COUNT(*) AS cnt,
                          AVG(score) AS avg_score,
                          SUM(borderline) AS border_cnt,
                          SUM(gatekeeper_called) AS gk_cnt
                   FROM semantic_triage_log
                   WHERE created_at >= datetime('now', ?)
                   GROUP BY category
                   ORDER BY cnt DESC""",
                (cutoff,),
            ).fetchall()
            conn.close()
        except Exception as e:
            logger.warning("semantic_triage_stats lekerdezes sikertelen: %s", e)
            return json.dumps({"error": str(e), "days": days, "total": 0, "by_category": []},
                              ensure_ascii=False)

        by_category = []
        total = 0
        for r in rows:
            cnt = r["cnt"] or 0
            total += cnt
            by_category.append({
                "category": r["category"],
                "count": cnt,
                "avg_score": round(r["avg_score"] or 0.0, 4),
                "borderline_count": r["border_cnt"] or 0,
                "gatekeeper_count": r["gk_cnt"] or 0,
            })

        return json.dumps({
            "days": days,
            "total": total,
            "by_category": by_category,
        }, ensure_ascii=False)
