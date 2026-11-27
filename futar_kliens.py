"""FUTÁR-KLIENS (2026-11-22): a Bridge keze a Kommandant gépén. Ami CSAK ott van — a teljes saját tudásbázis, a
ChatGPT-előfizetés (Codex), a Gemini-fiók és (11-23) a gépén lévő fájlok —, azt a gépen futó futár (~/faber/futar.py,
systemd: futar.service) hozza el. A Kommandant jóváhagyta, és kifejezetten engedte, hogy a Bridge a futáron át a
Codexet is indítsa (ez felülírja a 10-31-i „a Codexet csak a Kommandant indítja” szabályt).

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
  SiliconFlow-ügynököt vagy általuk olvasott tárat (`sf_fele`) — lásd feldwebel/responder.py. A Kommandant saját
  Claude-példányainak (cli-Claus, web-Claus — `CLAUDE_POSTAK`) szóló Bridge-üzenet ilyen körből is megy (Faber #10).
A ChatGPT és a Gemini nem kap automatikusan tudásbázis-kivonatot: csak azt, amit a hívó a promptba tesz.

A GÉPEN LÉVŐ FÁJL (`helyi_fajl`, 11-23 — a Kommandant: „De a downloadsban ottan vagyon a dolog… fel kéne töltenem?”):
- Ne kelljen feltöltenie vagy Gmailen átküldenie: ha a gép be van kapcsolva, a futár (`fajl` művelet) olvassa —
  CSAK ~/Downloads, ~/Desktop, ~/adatexport; a ~/Documents NEM (titkok), rejtett és titkot sejtető név sem; kanonizált
  úttal (a szabályt a futár érvényesíti, ~/faber/fajl_olvaso.py). Csak olvas (a Downloads az nCore-seed).
- Szöveg (.docx — sérültnél a zipből —, .pdf az első 60 oldal, .pptx, .md, .txt, .csv, .json), legfeljebb 400 ezer
  karakter, a vágás kimondva; vagy fájllista (a legújabb 50: név, méret, dátum). A nagy szövegből a hurok kivonatot
  ad, a teljes szöveg `eredmeny_lapoz`-zal olvasható (kivonat.py). 11-23 este: a futár a Word címsorait `#`-jellel
  adja, így a hosszú dokumentumból a hurok a TELJES dokumentum térképét adja (minden fejezetcím a helyével, első
  mondata, számai), és a lapozó fejezetre ugrik (`fejezet="4.4"`) — a robot-tanulmány diasora eddig az első 60 ezer
  karakterre (a 3.1 közepéig) épült.
- CSAK CLAUDE-NAK, mint a tudásbázis (a Kommandant ALAPELVE, 11-23: „a rólam szóló, egyéni dolgok nem mennek a
  kínaiaknak” — a gépe fájljai ilyenek): ugyanaz a három öv (`CLAUDE_CSAK_MUVELETEK`), és a fájl szövegével a kör ugyanúgy
  védett (`tudasbazis_tartalom`). A kínai ügynökök eszközkészletébe nem kerül (server.SUBAGENT_TILTOTT).
- A fájl szövegét a Bridge nem őrzi meg: a futár válaszsorát kiolvasás után kiüríti (`kiurit`).

A GÉP ÁLLAPOTA (`gep_allapot`, 11-25 — a Kommandant a kabinban: „A gépemen meg tudja nézni Cli Claus, h a hdd be van
csatlakozva?”; a Feldwebel a cli-Claus postájába tette, ahol senki nem figyelt, a válasz egy órán át nem jött):
- A futár `gep` művelete: be van-e kapcsolva a gép (üzemidő), milyen KÜLSŐ meghajtó van rajta (felcsatolva, szabad
  hellyel — vagy csatlakoztatva, de nem felcsatolva), a rendszer- és a belső lemez. Paramétere nincs; a gépen egy
  rögzített `lsblk` + `os.statvfs` (~/faber/gep_allapot.py), csak olvas.
- Nem személyes anyag (nem a tudásbázis, nem fájlszöveg): nem csak Claude-nak megy, és a kört nem teszi védetté.
- Ha a futár nem nyugtáz: „a géped most nem elérhető (ki van kapcsolva?)” — ez maga is válasz a kérdésre.
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

#: Az eszközök nevei (a Feldwebel ezeken hívja őket); a futár műveletének neve `FUTAR_MUVELET` szerint (alapból ugyanaz).
MUVELETEK = ("tudasbazis", "chatgpt", "gemini", "helyi_fajl", "gep_allapot")
FUTAR_MUVELET = {"helyi_fajl": "fajl", "gep_allapot": "gep"}
#: A Kommandant személyes anyaga: csak Claude-modell futtathatja, és a találat a kört védetté teszi.
CLAUDE_CSAK_MUVELETEK = frozenset({"tudasbazis", "helyi_fajl"})
CIMZETT, VALASZ_CIMZETT, FELADO = "futar", "futar-valasz", "futar"
#: A Kommandant saját Claude-példányainak postája (Faber #10, 11-27): a cli-Claus (Claude Code a gépen) és a web-Claus
#: (Claude a claude.ai-on). Mindkettő Claude-modell, és a teljes tudásbázist maga is eléri (a gépen a tudas.py, a Bridge
#: MCP-n a `tudasbazis` eszköz — server.tudasbazis). A nekik szóló Bridge-üzenet ezért tudásbázis-tartalmú körből is
#: mehet; a postájuk sorai a SiliconFlow-ügynökök kontextusába nem kerülnek (`SF_KIZARAS_SQL`).
CLAUDE_POSTAK = frozenset({"cli-claus", "web-claus"})
#: A futár sorai a messages táblában — és a Nachtschicht naplója (nachtschicht.py, 11-22: a tételei és a reggeli
#: jelentés tudásbázis-tartalmat hordozhatnak), valamint a feladat-ügynökök naplója (feladat_ugynok.py, 11-23:
#: sender = recipient = 'ugynok:<id>'; az ügynök olvashat tudásbázist), és (11-27) a Claude-példányok postája
#: (`CLAUDE_POSTAK`: oda tudásbázis-tartalmú körből is megy üzenet, és a válaszuk is hordozhat ilyet). A
#: SiliconFlow-ügynökök kontextusépítője és a DeepSeek-formázású reggeli összefoglaló ezeket SOHA nem veszi fel.
SF_KIZARAS_SQL = ("(sender NOT IN ('futar', 'nachtschicht', 'faber', 'cli-claus', 'web-claus') "
                  "AND recipient NOT IN ('futar', 'futar-valasz', 'nachtschicht', 'faber', 'cli-claus', 'web-claus') "
                  "AND sender NOT LIKE 'ugynok:%' AND recipient NOT LIKE 'ugynok:%')")
#: A tudas.py -f választéka (a gépen a kozos.FORRASOK; a futár az ottani lista szerint ellenőriz).
FORRASOK = ("jegyzet", "atirat", "doksi", "commit", "cm_kerdes", "cm_osszegzes", "cm_megfigyeles", "bridge_memoria",
            "bridge_uzenet", "bridge_vita", "bridge_feladat", "kod", "web_atirat", "web_projekt", "web_memoria",
            "google_sajat", "google_profil", "google_havi")
ATVETEL_S = 25                  # a futár 3 mp-enként kérdez (régi Bridge-en 10); ennyi alatt a nyugtának meg kell jönnie
VARAKOZAS_S = {"tudasbazis": 120, "chatgpt": 270, "gemini": 210,     # a futár időkorlátja (90/240/180/60) + ráhagyás
               "helyi_fajl": 90, "gep_allapot": 40}
LEPES_S = 1.0
KERDES_PLAFON, PROMPT_PLAFON, MAX_EREDMENY_KAR = 1_000, 20_000, 12_000
#: A fájl szövege: a futár plafonja (fajl_olvaso.PLAFON = 400 ezer karakter) + a fejléc.
FAJL_PLAFON, FAJL_EREDMENY_KAR, UT_PLAFON, MINTA_PLAFON = 400_000, 402_000, 1_000, 200
#: A kiolvasott fájl-válasz helyén ez marad a messages táblában (a Bridge a gép fájljának szövegét nem őrzi meg).
KIURITVE = "(a futár válasza: a Kommandant gépéről olvasott fájl — kiolvasva, a Bridge nem őrzi meg)"

NEM_ELERHETO = "a géped most nem elérhető (ki van kapcsolva?)"
CSAK_CLAUDE = "a tudásbázis csak Claude-nak megy ki"
CSAK_CLAUDE_FAJL = "a Kommandant gépén lévő fájl csak Claude-nak megy ki"
#: A Bridge MCP `tudasbazis` eszközének hívója: a Kommandant saját Claude-példánya (claude.ai-csatlakozó, CLI), a zárt
#: MCP-ajtó (api_kapu) és a példány-ellenőrzés (server.tudasbazis) után.
MCP_HIVO = "claude-mcp-peldany"
#: A telegram_history agent_id-jének jele: a válasz tudásbázis-tartalmat hordoz, csak Claude-modell láthatja.
CLAUDE_CSAK_JEL = ":claude-csak"
REJTETT_ELOZMENY = ("(Korábbi válasz a Kommandant tudásbázisából. Adatvédelmi okból csak Claude-modell láthatja, "
                    "ezért itt nem szerepel.)")
#: Eszközök, amelyek a kör tartalmát SiliconFlow-ügynökhöz (Kimi, DeepSeek, GLM), általuk olvasott tárba (feladat,
#: recept) vagy nyilvánosan vinnék tovább. Tudásbázis-tartalmú körben nem futnak. A Bridge-üzenet (`send_message`) a
#: címzettől függ: lásd `SF_FELE_IRASKOR`.
SF_FELE = frozenset({"ai_query", "ai_task", "create_task", "execute_recipe", "create_recipe", "schedule_recipe"})
# ALAPELV (a Kommandant, 11-23): „az eszközeink … általánosan elérhetők minden agentünknek … Olyan eszközöket nem
# adunk a kínaiak kezébe, amelyek RÓLAM, kifejezetten egyéni dolgokat használnak (lásd a tudásbázisom) … a főszabály:
# azért építjük az eszközöket, hogy használjuk.” Ezért tudásbázis-tartalmú körben CSAK az tiltott, ami a tartalmat
# kínai ÜGYNÖKHÖZ (Kimi, DeepSeek, GLM) vagy általuk olvasott tárba (feladat, recept, Bridge-üzenet) vinné, és a
# nyilvános komment. Az eszközök (Echolot, Engine, Studio — az AI-lépéseivel együtt —, a döntés-szimulátor, a
# képelemzés) mennek; a feladathoz kapott információt (dokumentum, szöveg) bármelyik eszköz és ügynök megkaphatja.
# (Korábban — 11-22 és 11-23 délután — az egész kör „szennyezett” lett, és még a Kommandant saját dokumentuma sem
# jutott el a saját Studiójába: „MIT tiltasz a saját ügynöködnek?”)
#: A Studio GÉPI műveletei (AI-lépés nélkül): tudásbázis-tartalmú körben is mehetnek — a tartalom a Kommandant saját
#: Studio-munkaterületére kerül, és ott semmilyen modell nem dolgozza fel. (Kommandant, 11-23: a robot-tanulmány
#: diasorát a Bridge nem tudta elkészíteni, mert a kör korábban a tudásbázist olvasta, és a teljes Studio tiltva volt.)
STUDIO_GEPI = frozenset({"files", "convert", "wordcraft", "office", "pptx_build", "validate_document", "extract",
                         "merge", "find_replace", "insert_after", "replace_heading", "delete_pages", "delete_section",
                         "insert_image", "upload_file", "upload_chunk", "upload_from_url", "history", "transfer", "units"})


def studio_ai_lepes(args: dict) -> bool:
    """A Studio-hívás a Studio SAJÁT AI-ját használná-e (akkor tudásbázis-tartalmú körben nem mehet)."""
    eszkoz = str((args or {}).get("eszkoz") or "")
    p = (args or {}).get("parameterek") or {}
    p = p if isinstance(p, dict) else {}
    if eszkoz == "sugo":
        return False                                   # az eszközlista lekérése: nem visz tartalmat
    if not eszkoz:
        return True                                    # ami nem egyértelmű, az tiltott marad
    if eszkoz not in STUDIO_GEPI:
        return True                                    # describe_image, ocr, facts, edit_with_instruction, video, …
    if eszkoz == "pptx_build" and (p.get("from_document") or p.get("instructions")):
        return True                                    # ott a Studio AI-ja írná a diákat
    if eszkoz == "validate_document" and p.get("content"):
        return True                                    # tartalmi (AI) olvasás
    if eszkoz == "extract" and str(p.get("mode") or "text") not in ("text", "markdown", "structure"):
        return True
    return False


def claude_posta(cimzett) -> bool:
    """A Bridge-üzenet címzettje a Kommandant saját Claude-példánya-e (cli-Claus, web-Claus)."""
    return str(cimzett or "").strip().lower() in CLAUDE_POSTAK


SF_FELE_IRASKOR = {"agora": lambda a: str(a.get("action") or "").lower() == "comment",
                   "story_comments": lambda a: bool(a.get("body")),
                   # Faber #10 (11-27): a Kommandant kétszer kérte, hogy a cli-Claus nézze meg a gemini-api-cli-t; a
                   # beszélgetésben korábbi tudásbázis-válasz volt, és a kapu MINDEN Bridge-üzenetet tiltott. A
                   # Claude-példány (cli-Claus, web-Claus) címzett mehet; minden más címzett tiltott marad.
                   "send_message": lambda a: not claude_posta(a.get("recipient")),
                   # a Pyramid-vita résztvevői (Kimi, DeepSeek, GLM) látják a témát, a kontextust és a hozzászólást
                   "vita": lambda a: str(a.get("muvelet") or "") in ("indit", "hozzaszol", "parancs")}


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
    """A `tudasbazis` vagy a `helyi_fajl` hívás valódi találatot hozott-e (nem hibát) — ettől a kör
    tudásbázis-tartalmú (védett). A `helyi_fajl` találata sima szöveg (a fájl fejléccel), a hibája JSON."""
    if nev not in CLAUDE_CSAK_MUVELETEK:
        return False
    try:
        return "error" not in json.loads(eredmeny)
    except (ValueError, TypeError):
        return nev == "helyi_fajl" and isinstance(eredmeny, str) and bool(eredmeny.strip())


def vedett_elozmeny(agent_id: str | None) -> bool:
    return str(agent_id or "").endswith(CLAUDE_CSAK_JEL)


def sf_fele(nev: str, args: dict | None) -> bool:
    """Az eszköz a kör tartalmát SiliconFlow-ügynökhöz, általuk olvasott tárba vagy nyilvánosan vinné-e."""
    if nev in SF_FELE:
        return True
    f = SF_FELE_IRASKOR.get(nev)
    return bool(f and f(args or {}))


SF_FELE_HIBA = ("ebben a beszélgetésben a Kommandant tudásbázisából van tartalom, ami csak Claude-modellnek mehet ki — "
                "SiliconFlow-ügynöknek (Kimi, DeepSeek, GLM), feladatba, receptbe, Bridge-üzenetbe vagy nyilvános "
                "kommentbe nem adom tovább. Az eszközök (Echolot, Engine, Studio, szimulátor) mennek, és a Claude-"
                "példányoknak (cli-claus, web-claus) szóló Bridge-üzenet is.")


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


HELYI_FAJL_DEF = {"type": "function", "function": {
    "name": "helyi_fajl",
    "description": ("A Kommandant gépén lévő fájl (Downloads, Desktop); a gépnek bekapcsolva kell lennie. OLVASÁS: `ut` "
                    "(pl. „~/Downloads/tanulmany.docx”, vagy csak a fájlnév) → a fájl szövege (.docx, .pdf — az első 60 "
                    "oldal —, .pptx, .md, .txt, .csv, .json; legfeljebb 400 ezer karakter, a vágást kimondja). "
                    "FÁJLLISTA: `lista` (pl. „~/Downloads”) és `minta` (pl. „robot” vagy „*.pdf”) → a legújabb 50 név, "
                    "méret, dátum. Ha a Kommandant azt mondja, hogy a fájl a gépén van (Downloads, asztal), EZT "
                    "használd — ne kérd, hogy töltse fel vagy küldje el Gmailen; ha a pontos név nem ismert, előbb "
                    "listázz. Csak ~/Downloads, ~/Desktop és ~/adatexport olvasható (a ~/Documents nem). A szöveg CSAK "
                    "ebben a válaszban használható: ne add tovább más ügynöknek (Kimi, DeepSeek, GLM), ne tedd "
                    "feladatba, receptbe, közös memóriába."),
    "parameters": {"type": "object", "properties": {
        "ut": {"type": "string", "description": "Olvasáshoz a fájl: „~/Downloads/<név>”, „~/Desktop/<név>” vagy csak a név."},
        "kerdes": {"type": "string", "description": ("Nagy, tagolatlan szövegnél: mire figyeljen a kivonat (elhagyható). "
                                                     "Fejezetekre tagolt dokumentumnál a teljes térképet kapod, és "
                                                     "fejezetre ugorhatsz (`eredmeny_lapoz`).")},
        "lista": {"type": "string", "description": "Fájllistához a mappa, pl. „~/Downloads” (olvasáskor hagyd el)."},
        "minta": {"type": "string", "description": "Fájllistánál a névminta, pl. „robot” vagy „*.pdf” (elhagyható)."},
    }}}}


GEP_ALLAPOT_DEF = {"type": "function", "function": {
    "name": "gep_allapot",
    "description": ("A Kommandant GÉPÉNEK állapota, azonnal (a gépen mindig futó futár olvassa, pár másodperc): be van-e "
                    "kapcsolva, milyen KÜLSŐ MEGHAJTÓ (HDD, pendrive) van csatlakoztatva — felcsatolva, szabad "
                    "hellyel, vagy csatlakoztatva, de nem felcsatolva —, és mennyi a szabad hely. „Be van dugva a "
                    "HDD?”, „be van kapcsolva a gép?”, „mennyi hely van?” → EZT hívd, akkor is, ha a Kommandant a "
                    "cli-Claust nevezi meg: ő csak nyitott Claude Code-ablakkal él, a futár mindig. Ha a válasz az, "
                    "hogy a gép nem elérhető, mondd meg: valószínűleg ki van kapcsolva."),
    "parameters": {"type": "object", "properties": {}}}}


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
    """A Feldwebel futár-eszközei: futár-csatorna nélkül egy sem; a `tudasbazis` és a `helyi_fajl` csak
    Claude-elsődlegességnél; a `gep_allapot` (nem személyes anyag) mindig."""
    if not bekotve():
        return []
    return ([TUDASBAZIS_DEF, HELYI_FAJL_DEF] if claude_elerheto() else []) + [GEP_ALLAPOT_DEF, CHATGPT_DEF, GEMINI_DEF]


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
    if muvelet == "helyi_fajl":
        return _fajl_param(args)
    if muvelet == "gep_allapot":
        return {}, None                                # paramétere nincs: a futár egy rögzített lekérdezést futtat
    prompt = str(args.get("prompt") or args.get("kerdes") or "").strip()
    if not prompt:
        return None, "hiányzik a prompt"
    if len(prompt) > PROMPT_PLAFON:
        return None, f"túl hosszú prompt (legfeljebb {PROMPT_PLAFON} karakter)"
    return {"prompt": prompt}, None


def _fajl_param(args: dict) -> tuple[dict | None, str | None]:
    """A `helyi_fajl` argumentumai: olvasás (`ut`, `kerdes`) VAGY fájllista (`lista`, `minta`). Csak az alakot nézi — a
    hely szabályát (engedett gyökér, tiltott név, kanonizálás) a futár érvényesíti a gépen."""
    def s(k: str, plafon: int) -> str | None:
        v = args.get(k)
        if v is True and k == "lista":
            return "~/Downloads"
        if v is None or v is False:
            return None
        v = str(v).strip()
        if "\x00" in v or len(v) > plafon:
            raise ValueError(f"érvénytelen `{k}` (legfeljebb {plafon} karakter)")
        return v
    try:
        ut, kerdes = s("ut", UT_PLAFON), s("kerdes", KERDES_PLAFON)
        lista, minta = s("lista", UT_PLAFON), s("minta", MINTA_PLAFON)
    except ValueError as e:
        return None, str(e)
    if ut and (lista or minta):
        return None, "vagy `ut` (olvasás), vagy `lista` (fájllista) — egyszerre nem"
    if ut:
        return ({"ut": ut, "kerdes": kerdes} if kerdes else {"ut": ut}), None
    if lista is not None or minta:
        return {"lista": lista or "~/Downloads", "minta": minta or None}, None
    return None, "hiányzik az `ut` (a fájl, pl. „~/Downloads/x.docx”) vagy a `lista` (a mappa, pl. „~/Downloads”)"


def kiurit(conn, mid: int) -> None:
    """A futár válaszsorai (nyugta, válasz) erre a kérésre: a szöveg helyén `KIURITVE` — a gép fájljának szövege nem
    marad a Bridge adatbázisában (és a messages tábla sem hízik soronként százezrekkel)."""
    conn.execute("UPDATE messages SET message = ? WHERE recipient = ? AND sender = ? AND reply_to = ?",
                 (KIURITVE, VALASZ_CIMZETT, FELADO, mid))
    conn.commit()


def _hiba(szoveg: str) -> str:
    return json.dumps({"error": szoveg}, ensure_ascii=False)


_FORRAS_NEV = {"tudasbazis": "a Kommandant tudásbázisa (a gépén)", "chatgpt": "ChatGPT (Codex, a Kommandant gépén)",
               "gemini": "Gemini (a Kommandant gépén)", "helyi_fajl": "a Kommandant gépén lévő fájl",
               "gep_allapot": "a Kommandant gépe (a futár olvasta, élőben)"}


async def hiv(muvelet: str, args: dict, *, get_db, modell: str = "", token: str | None = None,
              atvetel_s: float = ATVETEL_S, varakozas_s: float | None = None, lepes_s: float = LEPES_S) -> str:
    """Egy futár-művelet a gépen; az eredmény JSON-szöveg (`{"forras", "valasz", "mp"}` vagy `{"error"}`). Sosem dob.

    `modell`: az eszközt hívó kör VALÓBAN kiszolgáló modellje (a válasz `model` mezője). A `tudasbazis` és a
    `helyi_fajl` csak `claude-*` modellnek fut; a Bridge MCP-eszköz `MCP_HIVO`-t ad (a hívó a Kommandant saját
    Claude-példánya). A `helyi_fajl` sikeres eredménye SIMA SZÖVEG (a futár fejléce + a fájl szövege, a nagyát a hurok
    kivonatolja és lapozhatóvá teszi), a hibája JSON, mint a többié."""
    if muvelet not in MUVELETEK:
        return _hiba(f"ismeretlen futár-művelet: {muvelet}")
    if muvelet in CLAUDE_CSAK_MUVELETEK and not claude_modell(modell):
        logger.warning("futar: %s-hívás elutasítva (a kört nem Claude adta: %s)", muvelet, modell or "?")
        if muvelet == "helyi_fajl":
            return _hiba(f"{CSAK_CLAUDE_FAJL} (ezt a kört a(z) {modell or 'ismeretlen modell'} adta)")
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
        mid, kerdes = kuld(conn, token, FUTAR_MUVELET.get(muvelet, muvelet), param, hatarido=time.time() + varakozas_s,
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
            if muvelet == "helyi_fajl":
                conn = get_db()
                try:
                    kiurit(conn, mid)
                except Exception as e:  # noqa: BLE001 — a kiürítés hibája a választ nem viheti el
                    logger.warning("futar: #%d a fájl-válasz kiürítése elmaradt: %s", mid, str(e)[:200])
                finally:
                    conn.close()
            if not vege.get("ok"):
                return _hiba(str(vege.get("hiba") or "a futár hibát jelzett"))
            if muvelet == "helyi_fajl":
                return str(vege.get("szoveg") or "")[:FAJL_EREDMENY_KAR]
            return json.dumps({"forras": _FORRAS_NEV[muvelet], "valasz": str(vege.get("szoveg") or "")[:MAX_EREDMENY_KAR],
                               "mp": vege.get("mp")}, ensure_ascii=False)
        if not atvett and eltelt >= atvetel_s:
            logger.warning("futar: #%d %s — nincs nyugta %.0f mp alatt", mid, muvelet, eltelt)
            return _hiba(NEM_ELERHETO)
        if eltelt >= varakozas_s:
            logger.warning("futar: #%d %s — a futár átvette, de nem válaszolt %.0f mp alatt", mid, muvelet, eltelt)
            return _hiba(f"a géped átvette a kérést, de {int(varakozas_s)} mp alatt nem jött válasz ({muvelet})")
