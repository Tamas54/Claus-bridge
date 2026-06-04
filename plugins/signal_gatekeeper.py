"""
Signal Gatekeeper Plugin — episztemikus jel→kimenet ítélő-tool.

A doktrína magja: bemenet egy JEL (X) és egy KIMENET (Y), kimenet egy
episztemikus verdikt arról, milyen viszonyban áll X az Y-nal:
  - "definial":      X definíció szerint / logikailag MAGA az Y (vagy részhalmaza)
  - "oksagi_hid":    X oksági mechanizmuson át valószínűsíti Y-t (a mechanizmus megnevezhető)
  - "csak_korrelal": X és Y együtt mozoghat, de nincs definíciós/oksági kapcsolat (zaj / közös ok)

Az LLM-et négy jogász-heurisztikán át vezetjük végig (cui bono, kategória-mozdulat,
rés szó és tett közt, amit nem mondanak). Nincs daemon — csak híváskor fut.

A signal_gatekeeper SQLite tábla MÁR LÉTEZIK (a server.py init_db hozza létre),
ez a plugin CSAK használja (nem CREATE-el).
"""

import datetime
import json
import logging

logger = logging.getLogger("plugins.signal_gatekeeper")

__plugin_meta__ = {
    "name": "signal_gatekeeper",
    "version": "1.0.0",
    "description": "Episztemikus jel→kimenet ítélő — definial / oksagi_hid / csak_korrelal verdikt jogász-heurisztikákkal",
}

# Érvényes verdiktek (a tábla CHECK constraint-jével egyezően).
VALID_VERDIKTEK = ("definial", "oksagi_hid", "csak_korrelal")

# A négy jogász-heurisztika kanonikus címkéi (a heurisztika_hasznalt mezőhöz).
HEURISZTIKA_CIMKEK = ("cui_bono", "kategoria_mozdulat", "res_szo_tett", "amit_nem_mondanak")

# A system prompt — explicit végigvezeti az LLM-et a négy heurisztikán és a verdikteken.
SYSTEM_PROMPT = """Te egy episztemikus ítélő ("Signal Gatekeeper") vagy. A feladatod, hogy
eldöntsd, milyen viszonyban áll egy JEL (X) egy KIMENETtel (Y). HÁROM verdikt lehetséges:

1) "definial" — X DEFINÍCIÓ SZERINT vagy logikailag MAGA az Y (vagy az Y részhalmaza).
   Ilyenkor X bekövetkezése önmagában jelenti Y bekövetkezését, nem kell külön mechanizmus.
   Példa: X="A Fed 25 bázisponttal emeli az irányadó kamatot", Y="kamatemelés lesz" → definial.

2) "oksagi_hid" — X egy MEGNEVEZHETŐ oksági mechanizmuson át valószínűsíti Y-t.
   X nem maga az Y, de van egy ok→okozat lánc, amit ki tudsz mondani.
   Példa: X="tartós aszály a búzaövezetben", Y="emelkedik a kenyér ára" → oksagi_hid
   (mechanizmus: kínálatszűkülés → áremelkedés).

3) "csak_korrelal" — X és Y együtt mozoghat, de NINCS sem definíciós, sem megnevezhető
   oksági kapcsolat. Lehet közös ok, lehet véletlen, lehet zaj.
   Példa: X="nő a fagylalteladás", Y="nő a vízbefúlások száma" → csak_korrelal (közös ok: nyár).

MIELŐTT döntesz, KÖTELEZŐEN gondold végig EZT A NÉGY JOGÁSZ-HEURISZTIKÁT, mindegyiket
explicit megvizsgálva:

a) CUI BONO — Kinek áll érdekében, hogy X-et Y jeleként olvassuk? Ha valakinek érdeke
   fűződik a kapcsolat sugalmazásához, az gyengíti a jelet (lehet, hogy csak korrelál).

b) KATEGÓRIA-MOZDULAT — X valódi világbeli VÁLTOZÁS-e, vagy csak ÁTKATEGORIZÁLÁS /
   újracímkézés / definíciós trükk? Ha X csak átnevez valamit, az gyakran "definial"
   (tautológia) vagy "csak_korrelal", nem valódi oksági híd.

c) RÉS SZÓ ÉS TETT KÖZT — X KIJELENTÉS (szándék, bejelentés, ígéret) vagy CSELEKVÉS
   (megtörtént tény)? A puszta kijelentés gyengébb jel, mint a tett; egy bejelentés
   ritkán "definial" magát a kimenetet.

d) AMIT NEM MONDANAK — Milyen elhallgatás, hiányzó adat vagy ki nem mondott feltétel
   erősíti vagy gyengíti az X→Y kapcsolatot? A hiány önmagában is jel lehet.

A "heurisztika_hasznalt" mezőbe CSAK azokat a heurisztikákat írd be (vesszővel elválasztva,
a következő kanonikus címkékkel: cui_bono, kategoria_mozdulat, res_szo_tett, amit_nem_mondanak),
amelyek TÉNYLEGESEN DÖNTŐEK voltak az ítéletben. Ha egyik sem volt érdemben döntő, hagyd üresen ("").

KIZÁRÓLAG valid JSON-t adj vissza, semmi mást (se magyarázó szöveget, se markdown-fence-t),
pontosan ebben a sémában:
{"verdikt": "definial|oksagi_hid|csak_korrelal",
 "indoklas": "max 4 mondat magyarul",
 "heurisztika_hasznalt": "vesszővel elválasztott címkék az érdemben döntő heurisztikákról, vagy üres string",
 "konfidencia": 0.0}
A "konfidencia" egy 0.0 és 1.0 közötti szám."""


def _extract_json(content: str) -> str:
    """
    Robusztusan kivágja a JSON-t az LLM válaszból.
    Kezeli a ```...``` (és ```json ...```) markdown-fence-t, illetve a fence
    nélküli, körülvevő szöveggel kevert választ is (első '{' .. utolsó '}').
    """
    content = (content or "").strip()

    # Markdown-fence levágása, ha van.
    if content.startswith("```"):
        # Az első sor a nyitó fence (esetleg "```json") — azt eldobjuk.
        parts = content.split("\n", 1)
        if len(parts) == 2:
            content = parts[1]
        # A záró fence-t (utolsó ```) levágjuk.
        content = content.rsplit("```", 1)[0].strip()

    # Ha még mindig van körülvevő szöveg, vágjuk ki az első {-től az utolsó }-ig.
    if not content.startswith("{"):
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            content = content[start:end + 1]

    return content.strip()


def _normalize_result(parsed: dict) -> dict:
    """
    Az LLM JSON-jét érvényes, mentésre kész dict-té normalizálja.
    Hibás/hiányzó verdikt esetén 'csak_korrelal'-ra esik vissza (legkonzervatívabb).
    """
    verdikt = str(parsed.get("verdikt", "")).strip().lower()
    if verdikt not in VALID_VERDIKTEK:
        # Legbiztonságosabb (legkonzervatívabb) visszaesés: nincs igazolt kapcsolat.
        verdikt = "csak_korrelal"

    indoklas = str(parsed.get("indoklas", "")).strip()
    heur = str(parsed.get("heurisztika_hasznalt", "")).strip()

    # Konfidencia 0.0–1.0 közé szorítva.
    try:
        konf = float(parsed.get("konfidencia", 0.0))
    except (TypeError, ValueError):
        konf = 0.0
    konf = max(0.0, min(1.0, konf))

    return {
        "verdikt": verdikt,
        "indoklas": indoklas,
        "heurisztika_hasznalt": heur,
        "konfidencia": konf,
    }


def register_tools(app, deps):
    """Regisztrálja a signal_gatekeeper MCP toolokat (judge + history)."""
    import httpx

    sf_key = deps.get("siliconflow_api_key", "")
    sf_base = deps.get("siliconflow_base_url", "https://api.siliconflow.com/v1")
    sf_models = deps.get("siliconflow_models", {})
    sf_timeout = deps.get("siliconflow_timeout", 60)
    get_db = deps.get("get_db")

    @app.tool()
    async def signal_gatekeeper_judge(
        signal_x: str,
        outcome_y: str,
        context: str = "",
        caller: str = "",
    ) -> str:
        """Eldönti, milyen episztemikus viszonyban áll egy JEL (X) egy KIMENETtel (Y), és naplózza az ítéletet.

        MIRE VALÓ:
            Adsz egy jelet (signal_x) és egy kimenetet (outcome_y), és a tool megmondja,
            hogy a jel (1) DEFINÍCIÓ SZERINT maga a kimenet, (2) OKSÁGI MECHANIZMUSON át
            valószínűsíti a kimenetet, vagy (3) CSAK EGYÜTT MOZOG vele valódi kapcsolat nélkül.

        LÉPÉSENKÉNTI HASZNÁLAT:
            1. Töltsd ki a 'signal_x'-et a megfigyelt JELLEL (egy esemény, bejelentés, tény).
            2. Töltsd ki az 'outcome_y'-t azzal a KIMENETTEL, amiről tudni akarod, hogy X jelzi-e.
            3. Opcionálisan adj 'context'-et (háttér, ami segít a döntésben).
            4. Opcionálisan add meg a 'caller'-t (ki/mi hívja a toolt — naplózáshoz).
            5. A tool egy LLM-mel négy jogász-heurisztikán (cui bono, kategória-mozdulat,
               rés szó és tett közt, amit nem mondanak) átvezetve ítél, majd a verdiktet
               elmenti a signal_gatekeeper táblába.

        Args:
            signal_x: A megfigyelt JEL (X) szöveges leírása. KÖTELEZŐ.
                Példa: signal_x='Az X cég bejelentette a részvény-visszavásárlási programot'
            outcome_y: A KIMENET (Y) szöveges leírása, amivel X viszonyát vizsgáljuk. KÖTELEZŐ.
                Példa: outcome_y='Az Y részvény esik'
            context: Opcionális háttérinformáció (alapból üres string). Pl. piaci kontextus, időpont.
            caller: Opcionális azonosító, ki hívta a toolt (alapból üres → 'signal_gatekeeper' lesz a naplóban).

        Teljes példa hívás:
            signal_gatekeeper_judge(
                signal_x='A Fed 25 bázisponttal emeli az irányadó kamatot',
                outcome_y='kamatemelés lesz',
                context='2026 júniusi FOMC ülés',
                caller='orchestrator')

        Visszatérési érték (JSON string):
            {"verdikt": "definial|oksagi_hid|csak_korrelal",
             "indoklas": "max 4 mondat magyarul",
             "heurisztika_hasznalt": "vesszővel elválasztott címkék, pl. cui_bono,res_szo_tett",
             "konfidencia": 0.85,
             "id": 42}   # az elmentett naplósor azonosítója (ha sikerült a mentés)
            Hiba esetén: {"error": "..."}. Pl. ha hiányzik az API kulcs:
            {"error": "SILICONFLOW_API_KEY hiányzik"}.
        """
        if not sf_key:
            return json.dumps({"error": "SILICONFLOW_API_KEY hiányzik"}, ensure_ascii=False)

        if not (signal_x or "").strip() or not (outcome_y or "").strip():
            return json.dumps(
                {"error": "signal_x és outcome_y is kötelező (nem lehet üres)"},
                ensure_ascii=False,
            )

        # A felhasználói üzenet összeállítása (jel, kimenet, opcionális kontextus).
        user_msg = (
            f"JEL (X): {signal_x.strip()}\n"
            f"KIMENET (Y): {outcome_y.strip()}\n"
        )
        if (context or "").strip():
            user_msg += f"KONTEXTUS: {context.strip()}\n"
        user_msg += (
            "\nVizsgáld végig a négy heurisztikát, majd add meg a verdiktet "
            "KIZÁRÓLAG a megadott JSON-sémában."
        )

        # Default modell: deepseek.
        model_id = sf_models.get("deepseek", "deepseek-ai/DeepSeek-V4-Pro")

        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.2,
            "max_tokens": 600,
        }
        # Modell-specifikus kapcsolók (az email_triage.py mintájára).
        if "Kimi" in model_id:
            payload["thinking"] = {"type": "disabled"}
        elif "DeepSeek" in model_id:
            payload["reasoning_effort"] = "medium"

        # LLM-hívás.
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
        except Exception as e:
            logger.warning("signal_gatekeeper LLM-hívás sikertelen: %s", e)
            return json.dumps(
                {"error": f"LLM-hívás sikertelen: {e}"}, ensure_ascii=False
            )

        # Válasz kicsomagolása és robusztus JSON-parse.
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        raw = _extract_json(content)
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("a válasz nem JSON-objektum")
        except Exception as e:
            logger.warning("signal_gatekeeper invalid JSON az LLM-től: %s | nyers: %r", e, content[:300])
            return json.dumps(
                {
                    "error": "Az LLM nem adott valid JSON-t.",
                    "nyers_valasz": content[:500],
                },
                ensure_ascii=False,
            )

        result = _normalize_result(parsed)

        # Mentés a signal_gatekeeper táblába (a tábla MÁR létezik, csak INSERT).
        idobelyeg = datetime.datetime.now(datetime.timezone.utc).isoformat()
        created_by = (caller or "").strip() or "signal_gatekeeper"
        row_id = None
        if get_db is not None:
            try:
                conn = get_db()
                try:
                    cur = conn.execute(
                        """
                        INSERT INTO signal_gatekeeper
                            (signal_x, outcome_y, verdikt, indoklas,
                             heurisztika_hasznalt, idobelyeg, created_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            signal_x.strip(),
                            outcome_y.strip(),
                            result["verdikt"],
                            result["indoklas"],
                            result["heurisztika_hasznalt"],
                            idobelyeg,
                            created_by,
                        ),
                    )
                    conn.commit()
                    row_id = cur.lastrowid
                finally:
                    conn.close()
            except Exception as e:
                # A naplózás hibája nem buktatja el az ítéletet — csak logoljuk.
                logger.error("signal_gatekeeper DB-mentés sikertelen: %s", e)

        if row_id is not None:
            result["id"] = row_id

        logger.info(
            "signal_gatekeeper ítélet: X=%r Y=%r -> %s (konf=%.2f, heur=%s)",
            signal_x[:40], outcome_y[:40], result["verdikt"],
            result["konfidencia"], result["heurisztika_hasznalt"] or "-",
        )
        return json.dumps(result, ensure_ascii=False)

    @app.tool()
    async def signal_gatekeeper_history(limit: int = 20, verdikt: str = "") -> str:
        """Visszaadja a signal_gatekeeper napló utolsó N ítéletét (opcionális verdikt-szűrővel).

        MIRE VALÓ:
            Lekérdezi a korábban meghozott jel→kimenet ítéleteket a naplóból, legfrissebb elöl.

        LÉPÉSENKÉNTI HASZNÁLAT:
            1. Add meg a 'limit'-et (hány ítéletet kérsz vissza; alapból 20).
            2. Opcionálisan add meg a 'verdikt'-et szűrőként ('definial', 'oksagi_hid'
               vagy 'csak_korrelal'). Üres string = nincs szűrés, minden verdikt jön.

        Args:
            limit: A visszaadott ítéletek maximális száma (alapból 20, 1 és 200 közé szorítva).
            verdikt: Opcionális szűrő. Pontosan az egyik: 'definial' | 'oksagi_hid' | 'csak_korrelal'.
                Üres string ('') esetén minden verdikt szerepel.

        Példa hívás:
            signal_gatekeeper_history(limit=10, verdikt='oksagi_hid')

        Visszatérési érték (JSON string):
            {"count": 2, "items": [
                {"id": 42, "signal_x": "...", "outcome_y": "...", "verdikt": "oksagi_hid",
                 "indoklas": "...", "heurisztika_hasznalt": "...",
                 "idobelyeg": "2026-06-04T...", "created_by": "..."}, ...]}
            Hiba esetén: {"error": "..."}.
        """
        if get_db is None:
            return json.dumps({"error": "Nincs DB-kapcsolat (get_db hiányzik)"}, ensure_ascii=False)

        # Limit korlátozása ésszerű tartományba.
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(200, limit))

        verdikt_filter = (verdikt or "").strip().lower()
        if verdikt_filter and verdikt_filter not in VALID_VERDIKTEK:
            return json.dumps(
                {
                    "error": (
                        "Érvénytelen verdikt-szűrő: "
                        f"'{verdikt_filter}'. Választható: "
                        + ", ".join(VALID_VERDIKTEK)
                        + ", vagy üres string."
                    )
                },
                ensure_ascii=False,
            )

        try:
            conn = get_db()
            try:
                if verdikt_filter:
                    cur = conn.execute(
                        """
                        SELECT id, signal_x, outcome_y, verdikt, indoklas,
                               heurisztika_hasznalt, idobelyeg, created_by
                        FROM signal_gatekeeper
                        WHERE verdikt = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (verdikt_filter, limit),
                    )
                else:
                    cur = conn.execute(
                        """
                        SELECT id, signal_x, outcome_y, verdikt, indoklas,
                               heurisztika_hasznalt, idobelyeg, created_by
                        FROM signal_gatekeeper
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    )
                rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as e:
            logger.error("signal_gatekeeper_history lekérdezés sikertelen: %s", e)
            return json.dumps({"error": f"DB-lekérdezés sikertelen: {e}"}, ensure_ascii=False)

        items = [dict(r) for r in rows]
        return json.dumps({"count": len(items), "items": items}, ensure_ascii=False)
