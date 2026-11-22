"""FUTÁR-KLIENS (2026-11-22): a Bridge keze a Kommandant gépén. Ami CSAK ott van — a teljes saját tudásbázis, a
ChatGPT-előfizetés (Codex) és a Gemini-fiók —, azt a gépen futó futár (~/faber/futar.py, systemd: futar.service)
hozza el. A Kommandant jóváhagyta, és kifejezetten engedte, hogy a Bridge a futáron át a Codexet is indítsa (ez
felülírja a 10-31-i „a Codexet csak a Kommandant indítja” szabályt).

HOGYAN (a Faber mintája, faber_relay.py)
- A Bridge aláírt kérést tesz a messages táblába: recipient='futar', subject='futar:<művelet>', a törzs
  {"kerdes": <egyszer használatos azonosító>, "hatarido": <unix mp>, "param": {...}}. A HMAC-kulcs
  sha256("futar:" + bot-token): saját tartomány (a Faberé `faber:`, a munkáskulcs `munkas:`), új titok nem kell.
- A futár a munkáskulccsal kérdezi le (GET /api/messages?recipient=futar), lefuttatja a három fix művelet egyikét, és
  aláírt tételt küld vissza (POST /api/messages, sender='futar', recipient='futar-valasz', reply_to=<a kérés sora>):
  előbb nyugtát ('atvett'), aztán a választ ('valasz'). Az aláíratlan, hamis vagy más kérésre szóló választ eldobjuk.
- Ha a nyugta ATVETEL_S másodpercen belül nem jön: „a géped most nem elérhető (ki van kapcsolva?)” — egy kikapcsolt
  gépre nem várunk percekig.

A TUDÁSBÁZIS CSAK CLAUDE-MODELLNEK (a Kommandant, 11-22: „a kínai modellek különálló memóriája maradjon meg”;
„a Bridge persze megkaphatja a TELJES tudásbázist - de az anthropic modellek láthatják”; „az anthropic ügynökök is
láthatják”):
- A Feldwebel csak akkor kapja meg a `tudasbazis` eszközt, ha az Anthropic az elsődleges és elérhető (`claude_elerheto`).
- Végrehajtáskor is: az eszközt hívó kör modellje `claude-*` kell legyen, különben kimondott hiba (`CSAK_CLAUDE`).
- A SiliconFlow-ügynökök eszközkészletébe (server.SUBAGENT_TOOL_DEFS) sosem kerül; a Bridge MCP-n a `tudasbazis`
  eszköz csak a Kommandant saját példányainak (Claude) szól.
- A futár sorai (kérés, nyugta, válasz) a SiliconFlow-ügynökök kontextusába nem kerülnek (`SF_KIZARAS_SQL`).
- A Feldwebel köre, amelyben tudásbázis-tartalom van, csak Claude-dal megy (SiliconFlow-tartalék nélkül), és nem hív
  SiliconFlow-ügynököt vagy általuk olvasott tárat (`sf_fele`) — lásd feldwebel/responder.py.
A ChatGPT és a Gemini nem kap automatikusan tudásbázis-kivonatot: csak azt, amit a hívó a promptba tesz.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone

import faber_relay

logger = logging.getLogger("futar_kliens")

MUVELETEK = ("tudasbazis", "chatgpt", "gemini")
CIMZETT, VALASZ_CIMZETT, FELADO = "futar", "futar-valasz", "futar"
#: A futár sorai a messages táblában — és a Nachtschicht naplója (nachtschicht.py, 11-22: a tételei és a reggeli
#: jelentés tudásbázis-tartalmat hordozhatnak). A SiliconFlow-ügynökök kontextusépítője ezeket SOHA nem veszi fel.
SF_KIZARAS_SQL = ("(sender NOT IN ('futar', 'nachtschicht', 'faber') "
                  "AND recipient NOT IN ('futar', 'futar-valasz', 'nachtschicht', 'faber'))")
#: A tudas.py -f választéka (a gépen a kozos.FORRASOK; a futár az ottani lista szerint ellenőriz).
FORRASOK = ("jegyzet", "atirat", "doksi", "commit", "cm_kerdes", "cm_osszegzes", "cm_megfigyeles", "bridge_memoria",
            "bridge_uzenet", "bridge_vita", "bridge_feladat", "kod", "web_atirat", "web_projekt", "web_memoria",
            "google_sajat", "google_profil", "google_havi")
ATVETEL_S = 25                  # a futár 3 mp-enként kérdez (régi Bridge-en 10); ennyi alatt a nyugtának meg kell jönnie
VARAKOZAS_S = {"tudasbazis": 120, "chatgpt": 270, "gemini": 210}    # a futár időkorlátja (90/240/180) + ráhagyás
LEPES_S = 1.0
KERDES_PLAFON, PROMPT_PLAFON, MAX_EREDMENY_KAR = 1_000, 20_000, 12_000

NEM_ELERHETO = "a géped most nem elérhető (ki van kapcsolva?)"
CSAK_CLAUDE = "a tudásbázis csak Claude-nak megy ki"
#: A Bridge MCP `tudasbazis` eszközének hívója: a Kommandant saját Claude-példánya (claude.ai-csatlakozó, CLI), a zárt
#: MCP-ajtó (api_kapu) és a példány-ellenőrzés (server.tudasbazis) után.
MCP_HIVO = "claude-mcp-peldany"
#: A telegram_history agent_id-jének jele: a válasz tudásbázis-tartalmat hordoz, csak Claude-modell láthatja.
CLAUDE_CSAK_JEL = ":claude-csak"
REJTETT_ELOZMENY = ("(Korábbi válasz a Kommandant tudásbázisából. Adatvédelmi okból csak Claude-modell láthatja, "
                    "ezért itt nem szerepel.)")
#: Eszközök, amelyek a kör tartalmát SiliconFlow-ügynökhöz (Kimi, DeepSeek, GLM), általuk olvasott tárba (feladat,
#: recept, Bridge-üzenet) vagy nyilvánosan vinnék tovább. Tudásbázis-tartalmú körben nem futnak.
SF_FELE = frozenset({"ai_query", "ai_task", "create_task", "execute_recipe", "create_recipe", "schedule_recipe",
                     "send_message",
                     # 11-22: azok a saját MCP-k sem kapnak tudásbázis-tartalmat, amelyek a kapott szöveget a SAJÁT
                     # AI-jukkal dolgozzák fel (az Echolot és az Engine SiliconFlow-, azaz kínai modellekkel, a Studio a
                     # saját AI-lépéseivel). A ChatGPT és a Gemini KAPHAT (a Kommandant: „chatgpt és gemini kaphat”); a
                     # tiszta adatkeresők (Lexonomics, Medical, Makronóm) is mehetnek.
                     "echolot", "engine", "studio"})
SF_FELE_IRASKOR = {"agora": lambda a: str(a.get("action") or "").lower() == "comment",
                   "story_comments": lambda a: bool(a.get("body"))}


# ══ KULCS, ALÁÍRÁS, ÜZENETEK ═════════════════════════════════════════════

def kulcs(token: str) -> bytes | None:
    return hashlib.sha256(("futar:" + token).encode()).digest() if token else None


def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def bekotve() -> bool:
    """Van-e futár-csatorna (a kulcs a bot-tokenből jön). Nélküle a három eszköz nem jelenik meg."""
    return bool(_token())


def kuld(conn, token: str, muvelet: str, param: dict, *, hatarido: float, ts: str) -> tuple[int, str] | None:
    """Aláírt kérés a futárnak a messages táblába: (Bridge-azonosító, kérésazonosító), vagy None, ha nincs token."""
    k = kulcs(token)
    if not k:
        return None
    kerdes = secrets.token_hex(12)
    adat = json.dumps({"kerdes": kerdes, "hatarido": hatarido, "param": param}, ensure_ascii=False)
    cur = conn.execute(
        "INSERT INTO messages (timestamp, sender, recipient, subject, message, priority) "
        "VALUES (?, 'feldwebel', ?, ?, ?, 'normal')",
        (ts, CIMZETT, f"futar:{muvelet}", faber_relay.alair(k, muvelet, adat, ts)))
    mid = cur.lastrowid
    conn.execute("UPDATE messages SET thread_id = ? WHERE id = ?", (mid, mid))
    conn.commit()
    return mid, kerdes


def valasz(conn, k: bytes, mid: int, kerdes: str) -> tuple[bool, dict | None]:
    """(átvette-e már a futár, a válasz vagy None). Csak az érvényesen aláírt, erre a kérésre szóló tétel számít."""
    atvett, vege = False, None
    for r in conn.execute("SELECT message FROM messages WHERE recipient = ? AND sender = ? AND reply_to = ? "
                          "ORDER BY id", (VALASZ_CIMZETT, FELADO, mid)).fetchall():
        t = faber_relay.ellenoriz(k, r["message"])
        if not t or t.get("tipus") not in ("atvett", "valasz"):
            continue
        try:
            adat = json.loads(t["adat"])
        except (ValueError, TypeError):
            continue
        if not isinstance(adat, dict) or adat.get("kerdes") != kerdes:
            continue
        atvett = True
        if t["tipus"] == "valasz":
            vege = adat
    return atvett, vege


# ══ A TUDÁSBÁZIS-SZABÁLY ═════════════════════════════════════════════════

def claude_elerheto() -> bool:
    """Az Anthropic az elsődleges és van kulcs — csak ekkor kaphatja meg a Feldwebel a `tudasbazis` eszközt."""
    import llm_kapu
    return llm_kapu.elsodleges() == "anthropic" and llm_kapu.elerheto()


def claude_modell(modell: str | None) -> bool:
    return str(modell or "").startswith("claude-")


def tudasbazis_tartalom(nev: str, eredmeny: str) -> bool:
    """A `tudasbazis` hívás valódi találatot hozott-e (nem hibát) — ettől a kör tudásbázis-tartalmú."""
    if nev != "tudasbazis":
        return False
    try:
        return "error" not in json.loads(eredmeny)
    except (ValueError, TypeError):
        return False


def vedett_elozmeny(agent_id: str | None) -> bool:
    return str(agent_id or "").endswith(CLAUDE_CSAK_JEL)


def sf_fele(nev: str, args: dict | None) -> bool:
    """Az eszköz a kör tartalmát SiliconFlow-ügynökhöz, általuk olvasott tárba vagy nyilvánosan vinné-e."""
    if nev in SF_FELE:
        return True
    f = SF_FELE_IRASKOR.get(nev)
    return bool(f and f(args or {}))


SF_FELE_HIBA = ("ebben a beszélgetésben a Kommandant tudásbázisából van tartalom, ami csak Claude-modellnek mehet ki — "
                "SiliconFlow-ügynöknek (Kimi, DeepSeek, GLM), saját AI-t használó szolgáltatásnak "
                "(Echolot, Engine, Studio), feladatba, receptbe, Bridge-üzenetbe vagy nyilvános kommentbe nem adom tovább")


# ══ ESZKÖZÖK ═════════════════════════════════════════════════════════════

_TANACS = ("AI-TANÁCS (a Kommandant Claude + GPT + Gemini véleményét kéri): a `chatgpt`-t és a `gemini`-t EGY körben, "
           "egyszerre hívd, ugyanazzal az önálló prompttal; a saját (Claude) véleményed mellé tedd őket, és mondd meg, "
           "miben egyeznek és miben térnek el.")

TUDASBAZIS_DEF = {"type": "function", "function": {
    "name": "tudasbazis",
    "description": ("A Kommandant TELJES saját tudásbázisa a gépén: memóriajegyzetek, session-átiratok, saját commitok "
                    "és kód, repó-dokumentumok, a régi claude-mem, a claude.ai- és a Google-export, a Bridge régi "
                    "anyaga. Korábbi döntéseink, előzmények, „ezt már csináltuk?”, mit mondott a Kommandant valamiről "
                    "→ ezt használd (a search_memory csak a Bridge közös memóriája). A gépnek bekapcsolva kell lennie. "
                    "A találat CSAK ebben a válaszban használható: ne add tovább más ügynöknek (Kimi, DeepSeek, GLM), "
                    "ne tedd feladatba, receptbe, közös memóriába."),
    "parameters": {"type": "object", "properties": {
        "kerdes": {"type": "string", "description": "A kérdés természetes nyelven, pl. „mikor döntöttünk a Postgres mellett?”"},
        "szurok": {"type": "array", "items": {"type": "string", "enum": list(FORRASOK)},
                   "description": "Csak ezekből a forrásokból (elhagyható). Döntés: jegyzet, atirat; kód: kod, commit."},
        "tol": {"type": "string", "description": "Csak ettől a naptól (ÉÉÉÉ-HH-NN, elhagyható)."},
    }, "required": ["kerdes"]}}}


def _ai_def(nev: str, ki: str) -> dict:
    return {"type": "function", "function": {
        "name": nev,
        "description": (f"Második vélemény {ki} (a Kommandant előfizetéséből, a gépén futó parancssori változattal, "
                        "csak olvasó módban, fájl- és parancshozzáférés nélkül). Keresztellenőrzéshez, vitás vagy "
                        "fontos kérdéshez, vagy ha a Kommandant kéri. A prompt legyen ÖNÁLLÓ: nem látja a beszélgetést, "
                        f"csak azt kapja, amit beleírsz. Fél–három perc; a gépnek bekapcsolva kell lennie. {_TANACS}"),
        "parameters": {"type": "object", "properties": {
            "prompt": {"type": "string", "description": "Az önálló kérdés vagy feladat, a szükséges háttérrel."},
        }, "required": ["prompt"]}}}


CHATGPT_DEF = _ai_def("chatgpt", "a ChatGPT-től (OpenAI, Codex)")
GEMINI_DEF = _ai_def("gemini", "a Geminitől (Google)")


def eszkoz_definiciok() -> list:
    """A Feldwebel futár-eszközei: futár-csatorna nélkül egy sem; a `tudasbazis` csak Claude-elsődlegességnél."""
    if not bekotve():
        return []
    return ([TUDASBAZIS_DEF] if claude_elerheto() else []) + [CHATGPT_DEF, GEMINI_DEF]


def sajat_e(nev: str) -> bool:
    return nev in MUVELETEK


def _param(muvelet: str, args: dict) -> tuple[dict | None, str | None]:
    """Az eszköz argumentumai → a futár paraméterei, vagy (None, hiba). A futár maga is ellenőriz, szigorúbban."""
    args = args if isinstance(args, dict) else {}
    if muvelet == "tudasbazis":
        kerdes = str(args.get("kerdes") or args.get("query") or "").strip()
        if not kerdes:
            return None, "hiányzik a kérdés"
        if len(kerdes) > KERDES_PLAFON:
            return None, f"túl hosszú kérdés (legfeljebb {KERDES_PLAFON} karakter)"
        szurok = args.get("szurok") or []
        szurok = [szurok] if isinstance(szurok, str) else szurok
        if not isinstance(szurok, list) or any(s not in FORRASOK for s in szurok):
            return None, f"ismeretlen szűrő; megengedett: {', '.join(FORRASOK)}"
        tol = str(args.get("tol") or "").strip() or None
        if tol and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tol):
            return None, "a `tol` formája ÉÉÉÉ-HH-NN"
        return {"kerdes": kerdes, "szurok": szurok, "tol": tol}, None
    prompt = str(args.get("prompt") or args.get("kerdes") or "").strip()
    if not prompt:
        return None, "hiányzik a prompt"
    if len(prompt) > PROMPT_PLAFON:
        return None, f"túl hosszú prompt (legfeljebb {PROMPT_PLAFON} karakter)"
    return {"prompt": prompt}, None


def _hiba(szoveg: str) -> str:
    return json.dumps({"error": szoveg}, ensure_ascii=False)


_FORRAS_NEV = {"tudasbazis": "a Kommandant tudásbázisa (a gépén)", "chatgpt": "ChatGPT (Codex, a Kommandant gépén)",
               "gemini": "Gemini (a Kommandant gépén)"}


async def hiv(muvelet: str, args: dict, *, get_db, modell: str = "", token: str | None = None,
              atvetel_s: float = ATVETEL_S, varakozas_s: float | None = None, lepes_s: float = LEPES_S) -> str:
    """Egy futár-művelet a gépen; az eredmény JSON-szöveg (`{"forras", "valasz", "mp"}` vagy `{"error"}`). Sosem dob.

    `modell`: az eszközt hívó kör VALÓBAN kiszolgáló modellje (a válasz `model` mezője). A `tudasbazis` csak
    `claude-*` modellnek fut; a Bridge MCP-eszköz `MCP_HIVO`-t ad (a hívó a Kommandant saját Claude-példánya)."""
    if muvelet not in MUVELETEK:
        return _hiba(f"ismeretlen futár-művelet: {muvelet}")
    if muvelet == "tudasbazis" and not claude_modell(modell):
        logger.warning("futar: tudásbázis-hívás elutasítva (a kört nem Claude adta: %s)", modell or "?")
        return _hiba(f"{CSAK_CLAUDE} (ezt a kört a(z) {modell or 'ismeretlen modell'} adta)")
    param, hiba = _param(muvelet, args)
    if hiba:
        return _hiba(hiba)
    token = _token() if token is None else token
    k = kulcs(token)
    if not k:
        return _hiba("nincs Telegram-token a Bridge-en, a futár-csatorna nem megy")
    varakozas_s = VARAKOZAS_S[muvelet] if varakozas_s is None else varakozas_s
    t0 = time.monotonic()
    conn = get_db()
    try:
        mid, kerdes = kuld(conn, token, muvelet, param, hatarido=time.time() + varakozas_s,
                           ts=datetime.now(timezone.utc).isoformat())
    finally:
        conn.close()
    logger.info("futar: #%d %s elküldve", mid, muvelet)
    atvett = False
    while True:
        await asyncio.sleep(lepes_s)
        conn = get_db()
        try:
            atvett, vege = valasz(conn, k, mid, kerdes)
        finally:
            conn.close()
        eltelt = time.monotonic() - t0
        if vege is not None:
            logger.info("futar: #%d %s kész (%s, %.0f mp)", mid, muvelet, "rendben" if vege.get("ok") else "hiba", eltelt)
            if not vege.get("ok"):
                return _hiba(str(vege.get("hiba") or "a futár hibát jelzett"))
            return json.dumps({"forras": _FORRAS_NEV[muvelet], "valasz": str(vege.get("szoveg") or "")[:MAX_EREDMENY_KAR],
                               "mp": vege.get("mp")}, ensure_ascii=False)
        if not atvett and eltelt >= atvetel_s:
            logger.warning("futar: #%d %s — nincs nyugta %.0f mp alatt", mid, muvelet, eltelt)
            return _hiba(NEM_ELERHETO)
        if eltelt >= varakozas_s:
            logger.warning("futar: #%d %s — a futár átvette, de nem válaszolt %.0f mp alatt", mid, muvelet, eltelt)
            return _hiba(f"a géped átvette a kérést, de {int(varakozas_s)} mp alatt nem jött válasz ({muvelet})")
