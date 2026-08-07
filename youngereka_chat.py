"""
OPERATION LESESAAL — Réka chat-felülete.
=========================================

Egy link, amit megnyit és chatel. Nincs connector-konfiguráció, nincs
regisztráció, nincs jelszó.

IDENTITÁS
---------
A `/chat/yr-{token}` link egyszer villan fel, onnantól aláírt HttpOnly
cookie viszi a munkamenetet. A token így nem marad benne a böngésző
history-jában megoszthatóan. A cookie ALÁÍRT (HMAC a tokennel): egy
kitalált `yr_session=YoungeReka` érték nem enged be senkit.

MODELL-ÚT
---------
  alap        kimi (K2.7-Code)  — szöveg ÉS kép, egy kódút
  eszkaláció  kimi3 (K3)        — csak a „Gondolkodj rajta alaposan" gomb
  fallback    keret felett      — lásd `_fallback_for()`

A fallback KÉP-TUDATOS, és ez eltérés a munkaparancstól. Élő mérés
(2026-08-07) szerint a `deepseek-ai/DeepSeek-V4-Pro` képre HTTP 400-at
dob: `code 20041 — The model is not a VLM`. Ha a keret kimerülésekor
Réka egy gélképet tölt fel, a spec szerinti néma deepseek-fallback nem
csendes átváltás lenne, hanem hibaoldal — pont azon a funkción, amiért
a felület készül. Képes üzenetnél ezért a fallback is látó modell.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import pathlib
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone

import httpx
from starlette.requests import Request
from starlette.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                                 Response, StreamingResponse)

import youngereka_budget as budget
import youngereka_docs as docs
import youngereka_guest as guest
import youngereka_hq as hq
import youngereka_memory as memory
from youngereka_access import chat_profile, resolve_instance_from_path

logger = logging.getLogger("bridge.yr_chat")

COOKIE_NAME = "yr_session"
COOKIE_MAX_AGE = 90 * 24 * 3600  # 90 nap — a családi felületeken

#: A Hauptquartier ugyanazon az URL-en HÁROM ember magánéletét nyitja,
#: ezért egy felejtett élő munkamenet ára itt sokkal nagyobb.
HQ_COOKIE_MAX_AGE = 7 * 24 * 3600


def _cookie_max_age(instance: str) -> int:
    return HQ_COOKIE_MAX_AGE if instance == "kommandant" else COOKIE_MAX_AGE

#: Az utolsó ennyi üzenet megy fel kontextusként. Efölött összefoglalás —
#: az a fázis 2.
CONTEXT_MESSAGES = 20

_HTML_PATH = pathlib.Path(__file__).resolve().parent / "youngereka_chat.html"

# Ezeket az `install()` tölti fel a server.py-ból.
_CFG: dict = {}


# ============================================================
# SÉMA
# ============================================================

def ensure_schema(conn: sqlite3.Connection) -> None:
    """TEXT uuid kulcs, nincs AUTOINCREMENT, TIMESTAMP nem DATETIME —
    a Postgres-migráció a nyakunkon van."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS yr_chat_sessions (
          id          TEXT PRIMARY KEY,
          instance    TEXT NOT NULL,
          title       TEXT,
          created_at  TIMESTAMP NOT NULL,
          updated_at  TIMESTAMP NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS yr_chat_messages (
          id          TEXT PRIMARY KEY,
          session_id  TEXT NOT NULL REFERENCES yr_chat_sessions(id) ON DELETE CASCADE,
          role        TEXT NOT NULL,
          content     TEXT NOT NULL,
          model       TEXT,
          attachments TEXT,
          tokens_in   INTEGER DEFAULT 0,
          tokens_out  INTEGER DEFAULT 0,
          cost_usd    REAL    DEFAULT 0,
          created_at  TIMESTAMP NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_yr_msg_session "
                 "ON yr_chat_messages(session_id, created_at)")
    # A feldolgozott feltöltések. A kinyert szöveg és a normalizált képek
    # útja itt él, hogy egy restart ne veszítse el a mellékletet.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS yr_chat_files (
          id          TEXT PRIMARY KEY,
          instance    TEXT NOT NULL,
          filename    TEXT NOT NULL,
          kind        TEXT,
          label       TEXT,
          text        TEXT,
          image_paths TEXT,
          created_at  TIMESTAMP NOT NULL
        )""")
    budget.ensure_schema(conn)
    memory.ensure_schema(conn)
    guest.ensure_schema(conn)
    hq.ensure_schema(conn)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# COOKIE — aláírt, nem kitalálható
# ============================================================

def _secret(instance: str) -> bytes:
    """A cookie aláírókulcsa az adott személy SAJÁT tokenjéből származik.

    Korábban közös volt (az összes token együtt), és annak két baja volt:

    (a) Réka tokenjének forgatása kiléptette Annát és Bellát is —
        collateral, amit csak dokumentáltam, javítani kellett volna.
    (b) Új személy felvétele MINDENKIT kiléptetett. Bella hozzáadása
        emiatt „mellékhatásként" tolta volna ki a lányokat.

    Személyenkénti kulccsal mindkettő megszűnik, ÉS a lényegi védelem
    megmarad: ha a Kommandant Réka tokenjét cseréli, Réka minden korábbi
    cookie-ja meghal — beleértve azt is, amit valaki más kapott, amikor
    tévedésből az ő linkjét küldtük ki. Pontosan ez a forgatás célja.

    Token nélküli instance (nincs env, vagy vendég) → a folyamat
    egyedi kulcsa, tehát a cookie-ja nem hamisítható, de restartkor
    érvénytelen. Vendégnél ez rendben van: a `gs-` linkjét újranyithatja.
    """
    from youngereka_access import token_for
    sajat = token_for(instance)
    if not sajat:
        sajat = "no-token|" + _VENDEG_KULCS
    return hashlib.sha256(("yr-chat-v2|" + instance + "|" + sajat).encode()).digest()


#: Vendég-cookie kulcsa. Folyamat-indításkor generált — a vendég a
#: `gs-` linkjével bármikor újranyit, tehát a restart nem probléma.
_VENDEG_KULCS = __import__("secrets").token_hex(16)


def _sign(instance: str, expiry: int) -> str:
    msg = f"{instance}|{expiry}".encode()
    sig = hmac.new(_secret(instance), msg, hashlib.sha256).hexdigest()[:32]
    return f"{instance}|{expiry}|{sig}"


def _verify(cookie: str) -> str | None:
    if not cookie or cookie.count("|") != 2:
        return None
    instance, exp_s, _ = cookie.split("|")
    try:
        expiry = int(exp_s)
    except ValueError:
        return None
    if expiry < time.time():
        return None
    if not hmac.compare_digest(cookie, _sign(instance, expiry)):
        return None
    return instance


def _who(request: Request) -> str | None:
    return _verify(request.cookies.get(COOKIE_NAME, ""))


def _forbidden() -> Response:
    """403 — de emberi. Aki ide téved, ne egy nyers hibakódot lásson."""
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Nincs hozzáférés</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#FBFAF7;"
        "color:#2A2352;display:grid;place-items:center;height:100vh;margin:0;"
        "text-align:center;padding:24px}p{max-width:34ch;line-height:1.6}"
        "code{font-family:ui-monospace,monospace;font-size:.9em}</style>"
        "<div><h1>Nincs hozzáférés</h1>"
        "<p>Ehhez a felülethez személyes link kell. Ha van linked, nyisd meg "
        "újra azt — a belépés utána 90 napig megmarad ezen az eszközön.</p></div>",
        status_code=403)


# ============================================================
# MODELLVÁLASZTÁS
# ============================================================

def _TOKEN_MAP_VALUES() -> set:
    """Az ÉLŐ családi instance-ok. A kaszkád-halálhoz kell: ha a meghívó
    tokenje eltűnt (forgatás, törlés), a vendége sem léphet be."""
    from youngereka_access import _token_map
    return set(_token_map().values())


def _fallback_for(has_images: bool) -> str:
    """Keret felett ide váltunk. Képnél látó modell kell — a V4-Pro
    képre HTTP 400-at ad (mérés 2026-08-07)."""
    return "gemma" if has_images else "deepseek"


def _budget_limit_for(instance: str) -> float:
    """A vendégé 2.0 USD, a családé 5.0 (env-ből felülírható)."""
    if (instance or "").startswith("guest-"):
        return guest.NAPI_KERET_USD
    return budget.daily_budget_usd()


def _resolve_model(conn, instance: str, requested: str, has_images: bool) -> tuple[str, bool]:
    if budget.spent_today(conn, instance) >= _budget_limit_for(instance):
        return _fallback_for(has_images), True
    return requested, False


def _model_id(alias: str) -> str:
    extra = {"gemma": "google/gemma-4-12B-it",
             "qwen-vl": "Qwen/Qwen3.6-35B-A3B"}
    if alias in extra:
        return extra[alias]
    return _CFG["models"].get(alias, alias)


def _agent_extra(alias: str) -> dict:
    """A thinking-clamp. A K2.7 SF-en default-thinkinggel timeoutol; a
    K3-nál a thinking KÖTELEZŐ és nem kapcsolható ki."""
    if alias in ("kimi", "hy3", "qwen-vl"):
        return {"thinking": {"type": "disabled"}}
    if alias == "deepseek":
        return {"reasoning_effort": "medium"}
    return {}


def _timeout_for(alias: str) -> float:
    # A K3 mandatory-thinking: mérésen 180s alatt nem végzett.
    return 600.0 if alias == "kimi3" else 240.0


# ============================================================
# ELŐZMÉNY
# ============================================================

def _history(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT role, content FROM yr_chat_messages WHERE session_id=? "
        "ORDER BY created_at, rowid", (session_id,)).fetchall()
    rows = rows[-CONTEXT_MESSAGES:]
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def _is_first_message(conn, session_id: str) -> bool:
    row = conn.execute("SELECT COUNT(*) c FROM yr_chat_messages WHERE session_id=?",
                       (session_id,)).fetchone()
    return (row["c"] if row else 0) == 0


def _load_files(conn, file_ids: list[str], instance: str) -> list[dict]:
    """Csak a SAJÁT feltöltéseit adjuk vissza — a file_id nem lehet
    átjáró más instance anyagához."""
    out = []
    for fid in file_ids[:8]:
        row = conn.execute(
            "SELECT * FROM yr_chat_files WHERE id=? AND instance=?",
            (fid, instance)).fetchone()
        if row:
            out.append(dict(row))
    return out


# ============================================================
# TELEPÍTÉS — a server.py ezt hívja
# ============================================================

def install(mcp, *, get_db, api_key: str, base_url: str, models: dict,
            upload_dir: pathlib.Path, ai_task_fn=None, ertesit=None,
            hq_ertesit=None) -> None:
    _CFG.update(get_db=get_db, api_key=api_key, base_url=base_url,
                models=models, upload_dir=upload_dir,
                ai_task_fn=getattr(ai_task_fn, "fn", ai_task_fn),
                ertesit=ertesit or (lambda *a: None),
                hq_ertesit=hq_ertesit or (lambda *a: None))

    img_dir = upload_dir / "yr_chat"
    try:
        img_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("yr_chat képmappa nem hozható létre: %s", e)
    _CFG["img_dir"] = img_dir

    try:
        conn = get_db()
        ensure_schema(conn)
        conn.close()
    except Exception as e:  # noqa: BLE001
        logger.error("yr_chat séma létrehozása bukott: %s", e)

    # ---------- belépés ----------

    @mcp.custom_route("/chat/{prefix}-{token}", methods=["GET"])
    async def yr_chat_enter(request: Request):
        # `/chat/yr-{token}` és `/chat/an-{token}` — a prefix csak
        # emberi címke, az identitást KIZÁRÓLAG a token dönti el.
        token = request.path_params.get("token", "")
        prefix = request.path_params.get("prefix", "")
        if prefix == "gs":
            # Vendég: DB-ből, négy kapun át (visszavonás / abszolút lejárat /
            # tétlenség / KASZKÁD a meghívóra). A kaszkád belépéskor dől el,
            # nem takarító-feladatként — így redeploy nélkül azonnal hat.
            conn = get_db()
            try:
                instance = guest.resolve(
                    conn, token,
                    sponsor_el=lambda sp: sp in _TOKEN_MAP_VALUES())
            finally:
                conn.close()
        else:
            instance = resolve_instance_from_path(token)
        if not instance:
            logger.warning("yr_chat: érvénytelen token a belépésnél")
            return _forbidden()
        elettartam = _cookie_max_age(instance)
        expiry = int(time.time()) + elettartam
        resp = RedirectResponse("/chat", status_code=302)
        resp.set_cookie(
            COOKIE_NAME, _sign(instance, expiry),
            max_age=elettartam, httponly=True, samesite="lax",
            secure=True, path="/chat")
        logger.info("yr_chat: %s belépett", instance)
        try:
            conn = get_db()
            try:
                event(conn, instance, "login",
                      (request.headers.get("user-agent") or "")[:120])
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            pass
        return resp

    @mcp.custom_route("/chat", methods=["GET"])
    async def yr_chat_page(request: Request):
        if not _who(request):
            return _forbidden()
        try:
            html = _HTML_PATH.read_text(encoding="utf-8")
            prof = chat_profile(_who(request))
            # A HTML EGY fájl; a személyre szóló rész itt kerül bele.
            # `json.dumps` escapel, tehát a profil szövege nem törhet ki
            # a script-blokkból.
            beallitas = json.dumps({
                "cim": prof["cim"], "alcim": prof["alcim"],
                "koszones": prof["koszones"], "motto": prof["mottó"],
                "ures": prof["ures"],
                "melyseg": prof["melyseg_gomb"],
                "kutatas": prof["kutatas_gomb"],
            }, ensure_ascii=False)
            return HTMLResponse(html.replace("/*__PROFIL__*/null", beallitas))
        except FileNotFoundError:
            logger.error("youngereka_chat.html hiányzik: %s", _HTML_PATH)
            return HTMLResponse("A felület fájlja hiányzik a szerverről.",
                                status_code=500)

    # ---------- beszélgetések ----------

    @mcp.custom_route("/chat/api/sessions", methods=["GET"])
    async def yr_sessions(request: Request):
        instance = _who(request)
        if not instance:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        conn = get_db()
        try:
            ensure_schema(conn)
            rows = conn.execute(
                "SELECT id, title, updated_at FROM yr_chat_sessions "
                "WHERE instance=? ORDER BY updated_at DESC LIMIT 100",
                (instance,)).fetchall()
            try:
                import youngereka_search as yrs
                kereses = yrs.keret_allapot(conn, instance)
            except Exception:  # noqa: BLE001
                kereses = {"elfogyott": False}
            b = budget.budget_state(conn, instance)
            hatar = _budget_limit_for(instance)
            if hatar != b["limit_usd"]:      # vendég: 2.0, nem 5.0
                b = {"spent_usd": b["spent_usd"], "limit_usd": hatar,
                     "ratio": round(b["spent_usd"] / hatar, 4) if hatar else 0.0,
                     "exhausted": b["spent_usd"] >= hatar}
            return JSONResponse({
                "sessions": [dict(r) for r in rows],
                "budget": b,
                "search": kereses,
            })
        finally:
            conn.close()

    @mcp.custom_route("/chat/api/session/new", methods=["POST"])
    async def yr_session_new(request: Request):
        instance = _who(request)
        if not instance:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        sid = str(uuid.uuid4())
        conn = get_db()
        try:
            ensure_schema(conn)
            conn.execute(
                "INSERT INTO yr_chat_sessions (id, instance, title, created_at, updated_at) "
                "VALUES (?,?,?,?,?)", (sid, instance, None, _now(), _now()))
            conn.commit()
        finally:
            conn.close()
        return JSONResponse({"id": sid})

    @mcp.custom_route("/chat/api/session/{sid}", methods=["GET"])
    async def yr_session_get(request: Request):
        instance = _who(request)
        if not instance:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        sid = request.path_params["sid"]
        conn = get_db()
        try:
            ensure_schema(conn)
            own = conn.execute(
                "SELECT id FROM yr_chat_sessions WHERE id=? AND instance=?",
                (sid, instance)).fetchone()
            if not own:
                return JSONResponse({"error": "not found"}, status_code=404)
            rows = conn.execute(
                "SELECT id, role, content, model, attachments, created_at "
                "FROM yr_chat_messages WHERE session_id=? ORDER BY created_at, rowid",
                (sid,)).fetchall()
            msgs = []
            for r in rows:
                d = dict(r)
                try:
                    d["attachments"] = json.loads(d["attachments"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    d["attachments"] = []
                msgs.append(d)
            return JSONResponse({"messages": msgs})
        finally:
            conn.close()

    # ---------- vendégszoba ----------
    #
    # SZIMMETRIA: ezek a végpontok a meghívónak KIZÁRÓLAG azt mondják meg,
    # hogy a meghívás ÉL-E. Se beszélgetés, se jelenlét, se költés, se
    # utolsó belépés. Ha ide valaha bekerül egy „mit csinál a vendégem"
    # mező, azzal ez a felület megfigyelő-állássá válik.

    @mcp.custom_route("/chat/api/guest", methods=["GET"])
    async def yr_guest_list(request: Request):
        instance = _who(request)
        if not instance:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if instance.startswith("guest-"):
            # Vendég nem hívhat vendéget — a gráf zárt marad.
            return JSONResponse({"guests": [], "allowed": False})
        conn = get_db()
        try:
            return JSONResponse({"guests": guest.aktiv_vendegek(conn, instance),
                                 "allowed": True,
                                 "max": guest.MAX_AKTIV_VENDEG})
        finally:
            conn.close()

    @mcp.custom_route("/chat/api/guest/invite", methods=["POST"])
    async def yr_guest_invite(request: Request):
        instance = _who(request)
        if not instance or instance.startswith("guest-"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        conn = get_db()
        try:
            r = guest.invite(conn, instance, body.get("name") or "")
        finally:
            conn.close()
        if "error" in r:
            return JSONResponse(r, status_code=400)

        # A Kommandant ÉRTESÜL a meghívásról (v2.2 §B.1) — de csak arról,
        # HOGY történt, nem arról, kivel beszél a vendég.
        try:
            _CFG["ertesit"](instance, r["display_name"], r["id"])
        except Exception as e:  # noqa: BLE001
            logger.warning("vendég-értesítés bukott: %s", e)

        # A NYERS token EGYSZER, itt. Utána már csak a hash van meg.
        return JSONResponse({
            "id": r["id"], "display_name": r["display_name"],
            "url": f"/chat/{r['token']}", "expires_at": r["expires_at"]})

    @mcp.custom_route("/chat/api/guest/revoke", methods=["POST"])
    async def yr_guest_revoke(request: Request):
        instance = _who(request)
        if not instance or instance.startswith("guest-"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        conn = get_db()
        try:
            # `revoke` mindig sponsor-ra szűr: más vendégét a guest_id
            # ismerete sem vonja vissza.
            return JSONResponse({"revoked": guest.revoke(
                conn, instance, body.get("id") or "")})
        finally:
            conn.close()

    # ---------- emlékezet ----------
    #
    # Amit a rendszer megjegyzett, azt LÁTNIA kell és törölnie kell tudnia.
    # Egy emlékezet, amibe nem lehet belenézni, kellemetlen; egy emlékezet,
    # amit nem lehet törölni, csapda.

    @mcp.custom_route("/chat/api/notes", methods=["GET"])
    async def yr_notes(request: Request):
        instance = _who(request)
        if not instance:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        conn = get_db()
        try:
            return JSONResponse({"notes": memory.list_notes(conn, instance)})
        finally:
            conn.close()

    @mcp.custom_route("/chat/api/notes/delete", methods=["POST"])
    async def yr_notes_delete(request: Request):
        instance = _who(request)
        if not instance:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        conn = get_db()
        try:
            # A `forget` mindig instance-re szűr — más jegyzetét a note_id
            # ismerete sem törli.
            n = memory.forget(conn, instance,
                              note_id=(body.get("id") or ""),
                              mind=bool(body.get("all")))
            return JSONResponse({"deleted": n})
        finally:
            conn.close()

    # ---------- feltöltés ----------

    @mcp.custom_route("/chat/api/upload", methods=["POST"])
    async def yr_upload(request: Request):
        instance = _who(request)
        if not instance:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            form = await request.form()
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": f"A feltöltés nem olvasható: {e}"},
                                status_code=400)
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return JSONResponse({"error": "Nem érkezett fájl."}, status_code=400)
        filename = getattr(upload, "filename", "") or "melleklet"
        raw = await upload.read()
        if not raw:
            return JSONResponse({"error": "A fájl üres."}, status_code=400)

        loop = asyncio.get_running_loop()
        try:
            # A feldolgozás CPU-kötött (PDF-raszterizálás, kép-átméretezés)
            # — szálba tesszük, hogy ne fagyassza az event loopot.
            # Anna kurzus-PDF-et tölt fel, nem szakcikket: nála az
            # ábra-kiemelés csak zajt adna. Réka szakcikkében viszont az
            # információ fele a Figure-ökben van.
            kepkorlat = (docs.VISION_MAX_IMAGES
                         if chat_profile(instance)["abra_kiemeles"] else 4)
            doc = await loop.run_in_executor(
                None, docs.process_upload, raw, filename, kepkorlat)
        except Exception as e:  # noqa: BLE001
            logger.warning("yr_chat feldolgozás bukott (%s): %s", filename, e)
            return JSONResponse(
                {"error": f"Ezt a fájlt nem sikerült feldolgozni: {e}"},
                status_code=400)

        fid = str(uuid.uuid4())
        paths = []
        for i, jpeg in enumerate(doc["images"]):
            p = _CFG["img_dir"] / f"{fid}_{i}.jpg"
            try:
                p.write_bytes(jpeg)
                paths.append(str(p))
            except Exception as e:  # noqa: BLE001
                logger.warning("kép mentése bukott: %s", e)

        conn = get_db()
        try:
            ensure_schema(conn)
            conn.execute(
                "INSERT INTO yr_chat_files (id, instance, filename, kind, label, "
                "text, image_paths, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (fid, instance, filename, doc["kind"], doc["label"],
                 doc.get("text", ""), json.dumps(paths), _now()))
            conn.commit()
        finally:
            conn.close()

        return JSONResponse({
            "file_id": fid, "filename": filename, "kind": doc["kind"],
            "label": doc["label"], "notes": doc.get("notes", []),
            "images": len(paths),
        })

    # ---------- küldés (SSE) ----------

    @mcp.custom_route("/chat/api/send", methods=["POST"])
    async def yr_send(request: Request):
        instance = _who(request)
        if not instance:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "hibás kérés"}, status_code=400)

        sid = (body.get("session_id") or "").strip()
        text = (body.get("text") or "").strip()
        file_ids = body.get("file_ids") or []
        deep = bool(body.get("deep"))
        search = bool(body.get("search"))
        if not text and not file_ids:
            return JSONResponse({"error": "üres üzenet"}, status_code=400)

        return StreamingResponse(
            _stream_answer(instance, sid, text, file_ids, deep, search),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform",
                     "X-Accel-Buffering": "no",
                     "Connection": "keep-alive"})

    # ---------- alapos utánajárás ----------

    @mcp.custom_route("/chat/api/deep", methods=["POST"])
    async def yr_deep(request: Request):
        instance = _who(request)
        if not instance:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not chat_profile(instance)["kutatas_gomb"]:
            # KISKAPU-ZÁRÁS: a gomb nincs kitéve nála, de a végpontot is
            # zárni kell — a felület elrejtése nem hozzáférés-védelem.
            return JSONResponse(
                {"error": "Ez a gomb ezen a felületen nincs bekötve. "
                          "Kérdezz rá simán — végigmegyünk rajta."},
                status_code=403)
        fn = _CFG.get("ai_task_fn")
        if fn is None:
            return JSONResponse(
                {"error": "Az alapos utánajárás most nem elérhető."},
                status_code=503)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "hibás kérés"}, status_code=400)
        question = (body.get("text") or "").strip()
        if not question:
            return JSONResponse({"error": "üres kérdés"}, status_code=400)
        sid = (body.get("session_id") or "").strip()

        conn = get_db()
        try:
            ensure_schema(conn)
            ctx_parts = []
            for f in _load_files(conn, body.get("file_ids") or [], instance):
                if f.get("text"):
                    ctx_parts.append(f"[{f['filename']}]\n{f['text'][:20000]}")
        finally:
            conn.close()

        async def _run():
            try:
                await fn(title=docs.title_from(question, 80),
                         description=question + "\n\n" + REKA_CHAT_PROMPT,
                         context="\n\n".join(ctx_parts),
                         assigned_by=instance, deep_research=True)
            except Exception as e:  # noqa: BLE001
                logger.error("yr deep task bukott: %s", e)

        task = asyncio.create_task(_run())
        _DEEP_TASKS[str(id(task))] = task
        return JSONResponse({"task_id": str(id(task)), "status": "running"})

    @mcp.custom_route("/chat/api/deep/{tid}", methods=["GET"])
    async def yr_deep_poll(request: Request):
        if not _who(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        tid = request.path_params["tid"]
        task = _DEEP_TASKS.get(tid)
        if task is None:
            return JSONResponse({"status": "unknown"}, status_code=404)
        if not task.done():
            return JSONResponse({"status": "running"})
        _DEEP_TASKS.pop(tid, None)
        try:
            raw = task.result()
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"status": "failed", "error": str(e)})
        return JSONResponse({"status": "done", "result": raw})

    logger.info("YoungeReka chat-felület bekötve (/chat)")


_DEEP_TASKS: dict[str, asyncio.Task] = {}


# ============================================================
# RÁLÁTÁS — csak core instance-nak
# ============================================================

def oversight(conn, instance: str = "", session_id: str = "",
              limit: int = 20, full: bool = False) -> dict:
    """Réka és Anna beszélgetései, olvasásra.

    A hívó jogosultságát NEM itt ellenőrizzük — a `server.py` tool-burka
    teszi, MIELŐTT idejut. Ez a függvény feltételezi, hogy a hívó már
    igazolt core instance.
    """
    ensure_schema(conn)
    import youngereka_search as yrs

    if session_id:
        sor = conn.execute("SELECT * FROM yr_chat_sessions WHERE id=?",
                           (session_id,)).fetchone()
        if not sor:
            return {"error": "nincs ilyen beszélgetés"}
        uzenetek = conn.execute(
            "SELECT role, content, model, attachments, tokens_in, tokens_out, "
            "cost_usd, created_at FROM yr_chat_messages WHERE session_id=? "
            "ORDER BY created_at, rowid", (session_id,)).fetchall()
        return {"session": dict(sor),
                "messages": [dict(u) for u in uzenetek]}

    hol, param = "", []
    if instance:
        hol, param = "WHERE s.instance=?", [instance]

    sorok = conn.execute(f"""
        SELECT s.id, s.instance, s.title, s.created_at, s.updated_at,
               COUNT(m.id) AS uzenet,
               COALESCE(SUM(m.cost_usd), 0) AS koltseg
        FROM yr_chat_sessions s
        LEFT JOIN yr_chat_messages m ON m.session_id = s.id
        {hol}
        GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?""",
        param + [max(1, min(limit, 200))]).fetchall()

    ki = {"sessions": [dict(r) for r in sorok], "instances": {}}

    for inst in (["YoungeReka", "AnnaKatheder"] if not instance else [instance]):
        b = budget.budget_state(conn, inst)
        n = conn.execute("SELECT COUNT(*) c FROM yr_chat_sessions WHERE instance=?",
                         (inst,)).fetchone()["c"]
        m = conn.execute(
            "SELECT COUNT(*) c FROM yr_chat_messages m JOIN yr_chat_sessions s "
            "ON s.id=m.session_id WHERE s.instance=?", (inst,)).fetchone()["c"]
        utolso = conn.execute(
            "SELECT MAX(updated_at) u FROM yr_chat_sessions WHERE instance=?",
            (inst,)).fetchone()["u"]
        ki["instances"][inst] = {
            "beszelgetes": n, "uzenet": m, "utolso_aktivitas": utolso,
            "koltes_ma_usd": b["spent_usd"], "napi_keret_usd": b["limit_usd"],
            "kereses": yrs.keret_allapot(conn, inst),
        }

    if full:
        for s in ki["sessions"]:
            s["messages"] = [dict(u) for u in conn.execute(
                "SELECT role, content, model, created_at FROM yr_chat_messages "
                "WHERE session_id=? ORDER BY created_at, rowid", (s["id"],))]
    return ki


# ============================================================
# A VÁLASZ-FOLYAM
# ============================================================

def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def _tomorito(hosszu: str) -> str:
    """Hosszú üzenet → keresőkérdés, egy olcsó hívással."""
    darabok = []
    async for kind, p in _call_model("deepseek", _model_id("deepseek"), [
            {"role": "system", "content":
             "Alakítsd át a felhasználó üzenetét EGYETLEN tömör keresőkérdéssé. "
             "Csak a keresőkérdést add vissza, semmi mást, idézőjel nélkül."},
            {"role": "user", "content": hosszu[:2000]}]):
        if kind == "delta":
            darabok.append(p)
    return "".join(darabok)


async def _jegyzetelo(system: str, user: str) -> str:
    """Olcsó, nem-gondolkodó hívás a jegyzet-kiemeléshez."""
    darabok = []
    async for kind, p in _call_model("deepseek", _model_id("deepseek"), [
            {"role": "system", "content": system},
            {"role": "user", "content": user}]):
        if kind == "delta":
            darabok.append(p)
    return "".join(darabok)


async def _stream_answer(instance: str, session_id: str, text: str,
                         file_ids: list[str], deep: bool, search: bool = False):
    """SSE-folyam. Minden hiba a folyamon belül, emberi mondatként megy ki
    — a felület sose kapjon néma 500-ast."""
    get_db = _CFG["get_db"]
    conn = get_db()
    try:
        ensure_schema(conn)

        # --- munkamenet ---
        if session_id:
            own = conn.execute(
                "SELECT id FROM yr_chat_sessions WHERE id=? AND instance=?",
                (session_id, instance)).fetchone()
            if not own:
                session_id = ""
        if not session_id:
            session_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO yr_chat_sessions (id, instance, title, created_at, updated_at) "
                "VALUES (?,?,?,?,?)",
                (session_id, instance, docs.title_from(text), _now(), _now()))
            conn.commit()
        yield _sse("session", {"id": session_id})

        first = _is_first_message(conn, session_id)

        # A PIN a LEHETŐ LEGKORÁBBAN esik ki: se a modellhez, se a
        # naplóba, se az üzenet-táblába nem kerülhet be.
        pin_ok = False
        if instance == "kommandant":
            text, pin_ok = hq.strip_pin(text)
            if pin_ok:
                hq.unlock(conn, instance)
                yield _sse("status", {"message":
                                      f"PIN elfogadva — {hq.UNLOCK_PERC} percig nyitva."})
            if not text.strip() and not file_ids:
                yield _sse("done", {"budget": budget.budget_state(conn, instance)})
                return

        # --- mellékletek ---
        files = _load_files(conn, file_ids, instance)
        images: list[bytes] = []
        attach_desc = []
        for f in files:
            attach_desc.append({"filename": f["filename"], "label": f["label"]})
            try:
                for p in json.loads(f["image_paths"] or "[]"):
                    try:
                        images.append(pathlib.Path(p).read_bytes())
                    except OSError:
                        pass
            except (json.JSONDecodeError, TypeError):
                pass

        if len(images) > docs.VISION_MAX_IMAGES:
            images = images[:docs.VISION_MAX_IMAGES]

        # --- a felhasználói üzenet ---
        user_text = text
        doc_blocks = [docs.summarize_for_prompt(
            {"label": f["label"], "text": f["text"], "images": []}, f["filename"])
            for f in files]
        if doc_blocks:
            user_text = (text + "\n\n" if text else "") + "\n\n".join(doc_blocks)

        conn.execute(
            "INSERT INTO yr_chat_messages (id, session_id, role, content, "
            "attachments, created_at) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), session_id, "user", text or "(melléklet)",
             json.dumps(attach_desc, ensure_ascii=False), _now()))
        conn.commit()

        # --- keresés (opcionális, determinisztikus lánc) ---
        kereses_blokk = ""
        if search:
            yield _sse("status", {"message": "Keresek…"})
            try:
                import _echolot_client as _ec
                import youngereka_search as yrs
                talalat = await yrs.keres(conn, instance, text,
                                          mcp_call=_ec.mcp_call,
                                          tomorito=_tomorito)
                kereses_blokk = talalat["blokk"]
                yield _sse("search", {"sources": talalat["forrasok"],
                                      "note": talalat["megjegyzes"],
                                      "ran": talalat["futott"]})
            except Exception as e:  # noqa: BLE001
                logger.warning("keresés bukott (a válasz megy tovább): %s", e)
                yield _sse("search", {"sources": [], "ran": False,
                                      "note": "A keresés nem futott le — "
                                              "a válasz kereső nélkül készült."})

        if kereses_blokk:
            user_text += kereses_blokk

        # --- modell ---
        requested = "kimi3" if deep else "kimi"
        alias, fell_back = _resolve_model(conn, instance, requested, bool(images))
        model_id = _model_id(alias)
        yield _sse("model", {"alias": alias, "model": model_id,
                             "fallback": fell_back, "deep": deep})

        system = chat_profile(instance)["prompt"]

        # MAI DÁTUM. Enélkül a modell a tanítási adataiból tippel, és
        # MAGABIZTOSAN téved: Anna első kérdésére („milyen nap van ma?")
        # 2025. július 14-et mondott 2026. augusztus 7-én. Pont az a
        # hibafajta, amit a promptja tilt — és egy elsőéves nem tudja
        # megkülönböztetni a magabiztos tévedést a tudástól.
        # A Bridge az ai_query-útra régóta injektál temporál-direktívát;
        # a chat-út lemaradt róla.
        _ma = datetime.now(timezone.utc)
        _napok = ("hétfő", "kedd", "szerda", "csütörtök", "péntek",
                  "szombat", "vasárnap")
        system += (
            f"\n\nMAI DÁTUM: {_ma:%Y. %m. %d}. ({_napok[_ma.weekday()]}), "
            f"{_ma:%H:%M} UTC.\n"
            "Ez a futásidejű dátum, és ez az igaz — a saját belső "
            "időérzékelésed elavult, azt NE használd. Ha időhöz kötött "
            "kérdés jön (mai nap, aktuális esemény, határidő), ebből "
            "számolj. Ha valami a tudásod lezárása utánról való, mondd "
            "meg, hogy arról nincs friss információd.")

        # A becenév-elhagyás DETERMINISZTIKUS: nem modell dönti el, hogy
        # kérte-e. A prompt „azonnal és véglegesen"-t ígér — a véglegeshez
        # ez a szabály és a jegyzet kell, különben a következő
        # beszélgetésben visszajön.
        memory.check_becenev(conn, instance, text)
        if memory.becenev_tiltva(conn, instance):
            system += ("\n\nFONTOS: korábban kérte, hogy hagyd el a becenevet. "
                       "NE használd, sehol, az első üzenetben sem. "
                       "Köszönj a nevén vagy simán.")
        system += memory.recall_block(conn, instance)
        # A KÉPESSÉGLISTA KÓDBÓL. A prompt így soha nem állíthat többet,
        # mint ami be van kötve — ez a „szükség esetén megnézhetem"
        # hibaosztály gyökere volt.
        system += hq.capability_block(instance, conn)

        if not first:
            # A „kis hercegnő" CSAK az első üzenetben. Enélkül a modell
            # minden válaszba beszúrná, amit a spec kifejezetten tilt.
            system += ("\n\nEZ NEM az első üzenet ebben a beszélgetésben — "
                       "a köszönést hagyd el, folytasd a munkát.")

        history = _history(conn, session_id)[:-1]
        messages = [{"role": "system", "content": system}]
        messages += history
        messages.append({"role": "user",
                         "content": docs.vision_content(user_text, images)})

        # --- ESZKÖZ-KÖR (csak ott, ahol tényleg van eszköz) ---
        #
        # Nem streamelő elő-kör: ha a modell eszközt hív, végrehajtjuk, az
        # eredményt visszatesszük a beszélgetésbe, és UTÁNA streamelünk.
        # Így a tool-hívás nem töri meg a folyamot, és a hiba is kezelhető.
        if instance in hq.TOOLS_FOR:
            for _kor in range(3):
                hivasok, kozbenso = await _tool_kor(alias, model_id, messages)
                if not hivasok:
                    break
                messages.append(kozbenso)
                for h in hivasok:
                    try:
                        argok = json.loads(h["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        argok = {}
                    yield _sse("tool", {"name": h["function"]["name"]})
                    ered = await hq.dispatch(conn, instance,
                                             h["function"]["name"], argok,
                                             ertesit=_CFG.get("hq_ertesit"))
                    messages.append({"role": "tool", "tool_call_id": h["id"],
                                     "content": json.dumps(ered, ensure_ascii=False,
                                                           default=str)[:12000]})

        # --- hívás ---
        full, usage, err = "", {}, None
        async for kind, payload in _call_model(alias, model_id, messages):
            if kind == "delta":
                full += payload
                yield _sse("delta", {"t": payload})
            elif kind == "usage":
                usage = payload
            elif kind == "error":
                err = payload

        # Kép + nem-látó modell: egy ritka, de zavarba ejtő eset. Inkább
        # váltunk, mint hogy Réka hibát lásson.
        if err and images and alias not in ("kimi", "gemma", "qwen-vl"):
            alias = _fallback_for(True)
            model_id = _model_id(alias)
            yield _sse("model", {"alias": alias, "model": model_id,
                                 "fallback": True, "deep": deep})
            full, usage, err = "", {}, None
            async for kind, payload in _call_model(alias, model_id, messages):
                if kind == "delta":
                    full += payload
                    yield _sse("delta", {"t": payload})
                elif kind == "usage":
                    usage = payload
                elif kind == "error":
                    err = payload

        if err and not full:
            event(conn, instance, "error", err[:150])
            yield _sse("error", {"message": err})
            return

        # --- könyvelés ---
        tin = int(usage.get("prompt_tokens") or 0)
        tout = int(usage.get("completion_tokens") or 0)
        cost = budget.estimate_cost(model_id, tin, tout)
        conn.execute(
            "INSERT INTO yr_chat_messages (id, session_id, role, content, model, "
            "tokens_in, tokens_out, cost_usd, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), session_id, "assistant", full, alias,
             tin, tout, cost, _now()))
        conn.execute("UPDATE yr_chat_sessions SET updated_at=?, "
                     "title=COALESCE(title, ?) WHERE id=?",
                     (_now(), docs.title_from(text), session_id))
        conn.commit()
        budget.record(conn, instance, cost, model_id, "chat")

        yield _sse("done", {"budget": budget.budget_state(conn, instance)})

        # Jegyzet-kiemelés a válasz UTÁN: nem lassítja a beszélgetést, és
        # ha bukik, a folyam már lezárult — Réka semmit nem vesz észre.
        try:
            uj = await memory.extract(conn, instance, text, full, _jegyzetelo)
            if uj:
                yield _sse("notes", {"added": uj})
        except Exception as e:  # noqa: BLE001
            logger.warning("jegyzetelés bukott: %s", e)

    except Exception as e:  # noqa: BLE001
        logger.exception("yr_chat folyam hiba")
        yield _sse("error", {"message":
                             f"Elakadtam a válasz közben ({type(e).__name__}). "
                             f"Küldd el újra, és ha megint elakad, szólj Tamásnak."})
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


async def _tool_kor(alias: str, model_id: str, messages: list):
    """Egy nem-streamelő kör eszközökkel. (tool_calls, asszisztens-üzenet).

    Ha a modell nem hív eszközt, üres listát ad — a hívó ilyenkor
    továbbmegy a normál streamelésre.
    """
    api_key = _CFG.get("api_key") or ""
    if not api_key:
        return [], None
    payload = {"model": model_id, "messages": messages, "max_tokens": 1200,
               "temperature": 0.3, "tools": hq.openai_tools(),
               **_agent_extra(alias)}
    try:
        async with httpx.AsyncClient(timeout=_timeout_for(alias)) as client:
            r = await client.post(f"{_CFG['base_url']}/chat/completions",
                                  headers={"Authorization": f"Bearer {api_key}",
                                           "Content-Type": "application/json"},
                                  json=payload)
            if r.status_code != 200:
                logger.warning("tool-kör HTTP %d: %s", r.status_code, r.text[:200])
                return [], None
            uz = (r.json().get("choices") or [{}])[0].get("message") or {}
            hivasok = uz.get("tool_calls") or []
            if not hivasok:
                return [], None
            return hivasok, {"role": "assistant",
                             "content": uz.get("content") or "",
                             "tool_calls": hivasok}
    except Exception as e:  # noqa: BLE001
        logger.warning("tool-kör bukott (a válasz eszköz nélkül megy): %s", e)
        return [], None


async def _call_model(alias: str, model_id: str, messages: list):
    """SiliconFlow streaming. ('delta', szöveg) / ('usage', dict) / ('error', mondat)."""
    api_key = _CFG.get("api_key") or ""
    if not api_key:
        yield "error", ("A modell-hozzáférés nincs beállítva a szerveren "
                        "(SILICONFLOW_API_KEY hiányzik). Ezt Tamás tudja javítani.")
        return

    payload = {
        "model": model_id,
        "messages": messages,
        "stream": True,
        "max_tokens": 4096,
        "temperature": 0.6,
        "stream_options": {"include_usage": True},
        **_agent_extra(alias),
    }
    try:
        async with httpx.AsyncClient(timeout=_timeout_for(alias)) as client:
            async with client.stream(
                "POST", f"{_CFG['base_url']}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=payload,
            ) as resp:
                if resp.status_code != 200:
                    raw = (await resp.aread()).decode("utf-8", "replace")
                    logger.warning("SF %s HTTP %d: %s", model_id,
                                   resp.status_code, raw[:300])
                    yield "error", _human_error(resp.status_code, raw)
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        d = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if d.get("usage"):
                        yield "usage", d["usage"]
                    for ch in d.get("choices") or []:
                        piece = (ch.get("delta") or {}).get("content")
                        if piece:
                            yield "delta", piece
    except httpx.TimeoutException:
        yield "error", ("A modell most nagyon lassú, és megszakadt a válasz. "
                        "Próbáld újra — ha alapos gondolkodást kértél, "
                        "a sima küldés gyorsabb.")
    except Exception as e:  # noqa: BLE001
        logger.exception("SF hívás hiba")
        yield "error", (f"Nem értem el a modellt ({type(e).__name__}). "
                        f"Próbáld újra egy perc múlva.")


def _human_error(status: int, raw: str) -> str:
    """Hibaüzenet Réka oldaláról: mi történt, és mit lehet tenni.
    Se bocsánatkérés, se köd."""
    code = ""
    try:
        code = str((json.loads(raw) or {}).get("code") or "")
    except Exception:  # noqa: BLE001
        pass
    if code == "20041":
        return ("Ez a modell nem lát képet. Küldd el újra a képet — "
                "a rendszer átvált látó modellre.")
    if code == "20015" or "image" in raw.lower() and status == 400:
        return ("Túl sok kép egyszerre. Küldd kevesebbel — "
                "négy kép egy üzenetben biztosan átmegy.")
    if status == 429:
        return ("Most sok a kérés a modell felé. Várj fél percet, "
                "és küldd újra.")
    if status in (401, 403):
        return ("A modell-hozzáférés elutasított minket. "
                "Ezt Tamás tudja javítani.")
    if status >= 500:
        return ("A modell szolgáltatója most hibázik. "
                "Ez nem a te fájlodon múlik — próbáld újra pár perc múlva.")
    return f"A modell HTTP {status}-t adott vissza. Próbáld újra."


# ============================================================
# JELENLÉT, ABLAK, NAPLÓ  (v2.3)
# ============================================================
#
# A rutin-út a JELENLÉT: ki használja, milyen ritmusban, mennyibe kerül.
# Tartalom nélkül. A nyers szöveg kivétel, és a kivétel a Kommandant
# kezéből nyílik — nem az asszisztenséből.

def ensure_oversight_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_events (
          id         TEXT PRIMARY KEY,
          instance   TEXT NOT NULL,
          kind       TEXT NOT NULL,        -- login | message | error
          detail     TEXT,
          created_at TIMESTAMP NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_events "
                 "ON chat_events(instance, created_at)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oversight_audit (
          id         TEXT PRIMARY KEY,
          caller     TEXT NOT NULL,
          instance   TEXT NOT NULL,
          session_id TEXT,
          reason     TEXT NOT NULL,
          created_at TIMESTAMP NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oversight_windows (
          id         TEXT PRIMARY KEY,
          instance   TEXT NOT NULL,
          reason     TEXT NOT NULL,
          opened_by  TEXT NOT NULL,
          opened_at  TIMESTAMP NOT NULL,
          expires_at TIMESTAMP NOT NULL
        )""")
    conn.commit()


def event(conn, instance: str, kind: str, detail: str = "") -> None:
    """Bázisvonal-esemény. Sose dob — a napló hibája nem viheti el a választ."""
    try:
        ensure_oversight_schema(conn)
        conn.execute("INSERT INTO chat_events (id, instance, kind, detail, "
                     "created_at) VALUES (?,?,?,?,?)",
                     (str(uuid.uuid4()), instance, kind, detail[:200], _now()))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("chat_events írás bukott: %s", e)


def audit(conn, caller: str, instance: str, session_id: str, reason: str) -> None:
    """Nyers olvasás naplósora. KÖTELEZŐ minden tartalom-olvasásnál."""
    try:
        ensure_oversight_schema(conn)
        conn.execute("INSERT INTO oversight_audit (id, caller, instance, "
                     "session_id, reason, created_at) VALUES (?,?,?,?,?,?)",
                     (str(uuid.uuid4()), caller, instance, session_id or "",
                      reason, _now()))
        conn.commit()
        logger.info("oversight_audit: %s olvasott %s (%s)", caller, instance, reason)
    except Exception as e:  # noqa: BLE001
        logger.error("oversight_audit írás BUKOTT — %s", e)


def window_open(conn, instance: str, reason: str, minutes: int) -> dict:
    ensure_oversight_schema(conn)
    from datetime import timedelta
    most = datetime.now(timezone.utc)
    vege = most + timedelta(minutes=minutes)
    wid = str(uuid.uuid4())
    conn.execute("INSERT INTO oversight_windows (id, instance, reason, "
                 "opened_by, opened_at, expires_at) VALUES (?,?,?,?,?,?)",
                 (wid, instance or "*", reason, "kommandant",
                  most.isoformat(), vege.isoformat()))
    conn.commit()
    logger.info("oversight-ablak nyitva: %s, %d perc (%s)", instance, minutes, reason)
    return {"id": wid, "instance": instance or "*", "reason": reason,
            "expires_at": vege.isoformat(), "minutes": minutes}


def window_is_open(conn, instance: str, session_id: str = "") -> bool:
    """Van-e ÉLŐ ablak. Ha `instance` üres, a session tulajdonosát nézzük."""
    try:
        ensure_oversight_schema(conn)
        if not instance and session_id:
            sor = conn.execute("SELECT instance FROM yr_chat_sessions WHERE id=?",
                               (session_id,)).fetchone()
            instance = sor["instance"] if sor else ""
        most = datetime.now(timezone.utc).isoformat()
        sor = conn.execute(
            "SELECT 1 FROM oversight_windows WHERE expires_at > ? "
            "AND (instance = ? OR instance = '*') LIMIT 1",
            (most, instance or "*")).fetchone()
        return sor is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("ablak-ellenőrzés bukott (zárva vesszük): %s", e)
        return False


def presence(conn, instance: str = "") -> dict:
    """Jelenlét és ritmus — TARTALOM NÉLKÜL.

    Szándékosan nem ad vissza se címet, se üzenetet. Az érték a
    VÁLTOZÁS: „nyolc napja nem lépett be, előtte napi kétszer".
    """
    ensure_schema(conn)
    ensure_oversight_schema(conn)
    import youngereka_search as yrs
    from datetime import timedelta

    most = datetime.now(timezone.utc)
    ki: dict = {"most": most.isoformat(), "instances": {}}
    nevek = [instance] if instance else ["YoungeReka", "AnnaKatheder"]

    for inst in nevek:
        sor = conn.execute(
            "SELECT COUNT(*) db, MAX(m.created_at) utolso, MIN(m.created_at) elso "
            "FROM yr_chat_messages m JOIN yr_chat_sessions s ON s.id=m.session_id "
            "WHERE s.instance=? AND m.role='user'", (inst,)).fetchone()
        utolso = sor["utolso"]
        napok_ota = None
        if utolso:
            try:
                d = datetime.fromisoformat(utolso)
                napok_ota = round((most - (d if d.tzinfo else
                                   d.replace(tzinfo=timezone.utc))).total_seconds()
                                  / 86400, 2)
            except (ValueError, TypeError):
                pass

        # Ritmus: napi átlag az utolsó 14 napban, és az azt megelőző 14-ben.
        # A KETTŐ KÜLÖNBSÉGE a jel, nem a szám maga.
        def _db(tol, ig):
            return conn.execute(
                "SELECT COUNT(*) c FROM yr_chat_messages m "
                "JOIN yr_chat_sessions s ON s.id=m.session_id "
                "WHERE s.instance=? AND m.role='user' AND m.created_at>=? "
                "AND m.created_at<?", (inst, tol, ig)).fetchone()["c"]

        t0 = (most - timedelta(days=14)).isoformat()
        t1 = (most - timedelta(days=28)).isoformat()
        friss, korabbi = _db(t0, most.isoformat()), _db(t1, t0)

        # Napszak: hány üzenet ment 00:00–05:00 között (helyi UTC)
        hajnali = conn.execute(
            "SELECT COUNT(*) c FROM yr_chat_messages m "
            "JOIN yr_chat_sessions s ON s.id=m.session_id "
            "WHERE s.instance=? AND m.role='user' AND m.created_at>=? "
            "AND CAST(substr(m.created_at,12,2) AS INTEGER) < 5",
            (inst, t0)).fetchone()["c"]

        b = budget.budget_state(conn, inst)
        ki["instances"][inst] = {
            "uzenet_osszesen": sor["db"],
            "elso_uzenet": sor["elso"],
            "utolso_uzenet": utolso,
            "napja_nem_irt": napok_ota,
            "uzenet_utolso_14_nap": friss,
            "uzenet_elozo_14_nap": korabbi,
            "valtozas": (None if not korabbi else
                         round((friss - korabbi) / korabbi, 2)),
            "hajnali_uzenet_14_nap": hajnali,
            "koltes_ma_usd": b["spent_usd"],
            "napi_keret_usd": b["limit_usd"],
            "kereses": yrs.keret_allapot(conn, inst),
            "belepes_uj_eszkozrol": conn.execute(
                "SELECT COUNT(*) c FROM chat_events WHERE instance=? "
                "AND kind='login' AND created_at>=?", (inst, t0)).fetchone()["c"],
            "hiba_14_nap": conn.execute(
                "SELECT COUNT(*) c FROM chat_events WHERE instance=? "
                "AND kind='error' AND created_at>=?", (inst, t0)).fetchone()["c"],
        }

    ki["nyitott_ablakok"] = [dict(r) for r in conn.execute(
        "SELECT instance, reason, opened_by, expires_at FROM oversight_windows "
        "WHERE expires_at > ?", (most.isoformat(),))]
    ki["utolso_olvasasok"] = [dict(r) for r in conn.execute(
        "SELECT caller, instance, reason, created_at FROM oversight_audit "
        "ORDER BY created_at DESC LIMIT 10")]
    ki["megjegyzes"] = ("Tartalom NINCS ebben a nézetben. A jel a VÁLTOZÁS, "
                        "nem a szám. Ez nem őrszem: csak akkor néz oda, ha "
                        "kérdezed.")
    return ki
