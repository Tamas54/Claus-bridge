"""
DeepSeek response engine with native tool use (function calling).
The Feldwebel — operational assistant for the Kommandant.

Architecture: DeepSeek decides when to call tools (gmail_search, gmail_poll,
calendar_poll, send_email, create_task). Results are fed back for final response.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from html import escape as html_escape

import os
import re as _re
import xml.etree.ElementTree as ET

import httpx

from feldwebel import get_ctx
from feldwebel.history import add_message, get_history, trim_history
from feldwebel import jovahagyas as _jovahagyas

# Bridge UI v2 (2026-11-22): a webes kör élő nyoma (melyik modell válaszolt, milyen eszköz fut). Telegram-körben a
# `kimenet.nyom()` None, és minden úgy megy, mint eddig. A modul hiánya (önálló Feldwebel) sem hiba.
try:
    import kimenet as _kimenet
except ImportError:  # pragma: no cover
    _kimenet = None

# ── Economic data API keys ──
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

logger = logging.getLogger("feldwebel.responder")

WEEKDAYS_HU = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]

# ── Tools definition for DeepSeek ──────────────────────────────────

TOOLS = [
    # ── Email ──
    {"type": "function", "function": {
        "name": "gmail_search", "description": "Keress emaileket a Gmail-ben. Személynév, tárgy, dátum alapján.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Gmail query: from:Darvas, subject:EdTech, newer_than:1d"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "gmail_poll", "description": "Gmail inbox frissítése — új emailek lekérése.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "send_email", "description": "Email küldés. MINDIG kérj megerősítést a Kommandanttól küldés előtt!",
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string", "description": "Címzett email cím"},
            "subject": {"type": "string", "description": "Email tárgya"},
            "body": {"type": "string", "description": "Email szövege. Aláírás: Üdvözlettel, Kende Tamás"},
        }, "required": ["to", "subject", "body"]}}},
    {"type": "function", "function": {
        "name": "read_gmail_attachment", "description": "Email csatolmány tartalmának kiolvasása (docx, pdf, txt). Először gmail_search-csel keresd meg az email message_id-ját.",
        "parameters": {"type": "object", "properties": {
            "message_id": {"type": "string", "description": "Gmail message ID (gmail_search eredményéből)"},
            "attachment_index": {"type": "integer", "description": "Hanyadik csatolmány (0 = első)", "default": 0},
        }, "required": ["message_id"]}}},
    # ── Naptár ──
    {"type": "function", "function": {
        "name": "calendar_poll", "description": "Naptár frissítése — mai események lekérése.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "calendar_create", "description": "Naptáresemény JAVASLATA: a hívás még NEM ír be semmit — a Kommandant egyszavas igenje („igen”, „mehet”) után kerül a naptárba. A válaszodban mondd el, mit írnál be, és kérdezd meg.",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "Esemény neve"},
            "start_time": {"type": "string", "description": "Kezdés ISO formátumban: 2026-04-10T15:00:00; egész napos (határidő): 2026-04-10"},
            "end_time": {"type": "string", "description": "Befejezés ISO formátumban: 2026-04-10T16:00:00 (egész naposnál üres)", "default": ""},
            "description": {"type": "string", "description": "Részletek (opcionális)", "default": ""},
            "reminder_minutes": {"type": "array", "items": {"type": "integer"}, "description": "Emlékeztetők, perccel a kezdés előtt: [1440, 60] = 1 nappal és 1 órával előtte. Egész napos eseménynél a nap 0:00-jától számít: [3780, 900] = 3 nappal és 1 nappal előtte reggel 9-kor"},
        }, "required": ["summary", "start_time"]}}},
    # ── Közzététel: X/Twitter (2026-11-23, twitter_poszt.py) ──
    {"type": "function", "function": {
        "name": "tweet_tervezet", "description": "X/Twitter-poszt TERVEZETE: a hívás NEM posztol — a tervezet a kabin munkapadjára kerül, és csak a Kommandant jóváhagyásával megy ki (a kabin Jóváhagyom gombja, vagy a kifejezett „posztold ki a tervezetet: <első szavak>”). Legfeljebb 280 karakter az X számítása szerint (a link 23-nak számít, az emoji 2-nek). A válaszodban mondd el a teljes szöveget és a karakterszámot.",
        "parameters": {"type": "object", "properties": {
            "szoveg": {"type": "string", "description": "A tweet szövege, ahogy kimenne (link nélkül, ha a `link` mezőt használod)"},
            "link": {"type": "string", "description": "Opcionális link (http/https) — új sorban a szöveg után, 23 karakternek számít", "default": ""},
        }, "required": ["szoveg"]}}},
    # ── OPERATION HOG (2026-11-23, hog.py) ──
    {"type": "function", "function": {
        "name": "hog_keszit", "description": "OPERATION HOG: egy (nagy elérésű) X-posztra — Elon Musk, egy hírfiók, bárki — adatalapú válasz az Echolotból: ÁLLANDÓ nyilvános elemzőoldal (echolotnews.com/{lang}/hog/…, saját kártyával) + az X algoritmusára tervezett komment, a link a saját válasz alá. Akkor hívd, ha a Kommandant egy posztra (link, szöveg vagy képernyőkép — az előző üzenetében is lehet) azt mondja: „csináljunk erre HOG-ot”, „HOG Twitter-bejegyzést kérek”, „kommentáljuk ezt”, „írj rá választ”. A hívás NEM publikál és NEM posztol: a háttérben a Sonnet megírja (~2–3 perc, ~0,1–0,2 $), a kész tervezet a kabin munkapadjára kerül (🐗 HOG), és csak a Kommandant jóváhagyásával megy ki. A kommentet NE te írd meg. Képernyőképnél add át a melléklet számát ([Melléklet #ID]) és amit az átiratból tudsz (szöveg, szerző, @handle). Ha a Kommandant utólag küldi a poszt linkjét egy kész HOG-hoz: hog_id + poszt_url.",
        "parameters": {"type": "object", "properties": {
            "poszt_szoveg": {"type": "string", "description": "A poszt szövege szó szerint, az eredeti nyelven (idézett posztnál az is, külön bekezdésben)", "default": ""},
            "szerzo": {"type": "string", "description": "A poszt szerzője: név és @handle (pl. „Elon Musk @elonmusk”)", "default": ""},
            "poszt_url": {"type": "string", "description": "A poszt linkje (x.com/<handle>/status/<szám>), ha van", "default": ""},
            "melleklet_id": {"type": "integer", "description": "A képernyőkép mellékletének száma ([Melléklet #ID])"},
            "mod": {"type": "string", "enum": ["", "valasz", "idezet", "sajat"], "description": "Üres = válasz, ha megvan az eredeti poszt; „idézd” → idezet; „saját posztban” → sajat", "default": ""},
            "nyelv": {"type": "string", "description": "A poszt nyelve (hu, en, de…), ha tudod", "default": ""},
            "utasitas": {"type": "string", "description": "A Kommandant külön kérése a szögre vagy a hangra, a saját szavaival", "default": ""},
            "hog_id": {"type": "integer", "description": "Csak a link utólagos megadásához: a függő HOG száma"},
        }, "required": []}}},
    # ── Feladatok ──
    {"type": "function", "function": {
        "name": "create_task", "description": "Új Bridge feladat létrehozása.",
        "parameters": {"type": "object", "properties": {"title": {"type": "string", "description": "Feladat leírása"}}, "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "list_tasks", "description": "Nyitott feladatok listázása.",
        "parameters": {"type": "object", "properties": {}}}},
    # ── AI agentek ──
    {"type": "function", "function": {
        "name": "ai_query", "description": "Feladat EGY agentnek: kimi (kutató), deepseek (elemző), glm5 (kódoló/MCP specialist); a válasz itt jön (≤120 s). Eszközökkel dolgozik: web, Echolot, böngésző, StatData, Lexonomics, Medical, Makronóm, Engine, Studio-dokumentum — tudásbázis, Gmail, naptár, levélküldés NINCS náluk.",
        "parameters": {"type": "object", "properties": {
            "model": {"type": "string", "description": "kimi / deepseek / glm5", "enum": ["kimi", "deepseek", "glm5"]},
            "prompt": {"type": "string", "description": "Feladat / kérdés"},
            "news_context": {"type": "string", "description": "Opcionális friss hírkontextus az Echolot-ról: 'general' / 'economy' / 'tech' / 'geopolitics' preset, vagy üres ha nem kell.", "default": "", "enum": ["", "general", "economy", "tech", "geopolitics"]},
        }, "required": ["model", "prompt"]}}},
    {"type": "function", "function": {
        "name": "ai_task", "description": "Feladat MIND A 3 agentnek párhuzamosan, a háttérben (eredmény: read_task_results). Komplex kutatáshoz, elemzéshez; ugyanazokkal az eszközökkel, mint az ai_query.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Rövid cím"},
            "description": {"type": "string", "description": "Részletes leírás"},
            "news_context": {"type": "string", "description": "Opcionális friss hírkontextus az Echolot-ról: 'general' / 'economy' / 'tech' / 'geopolitics' preset, vagy üres ha nem kell.", "default": "", "enum": ["", "general", "economy", "tech", "geopolitics"]},
        }, "required": ["title", "description"]}}},
    {"type": "function", "function": {
        "name": "read_task_results", "description": "AI feladat eredményeinek lekérése. 'Mi lett a feladattal?', 'kész van?'",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "integer", "description": "Task ID szám"}}, "required": ["task_id"]}}},
    # ── Web ──
    {"type": "function", "function": {
        "name": "web_fetch", "description": "Weboldal (6000 karakterig), PDF (első oldalak) vagy YouTube-videó (leirat) szövege.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "A letöltendő URL"}, "kerdes": {"type": "string", "description": "Már olvasott URL-nél: új szempont"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "web_search", "description": "Web-keresés (saját SearXNG → brave-mcp → DDG lánc). Akkor jó, ha friss adatra (Eurostat flash, MNB-közlemény, ECB döntés), részletes forrásokra, vagy nem-hír jellegű információra van szükség. Visszaad cím + URL + leírás találatokat. HA A KOMMANDANT MOTORT NEVEZ MEG ('ezt searxng-vel', 'Searrel', 'brave-mcp-vel') → add át az `engine` mezőben, és a válaszodban MONDD KI, melyik motor szolgált ki (a találat végén ott a _bridge_served_by sor).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Keresési kifejezés"},
            "engine": {"type": "string", "enum": ["auto", "searxng", "brave-mcp", "ddg", "brave-api"], "description": "Melyik keresőt használja. 'auto' = a lánc dönt. CSAK akkor állítsd be, ha a Kommandant kifejezetten motort nevezett meg — az ő kérése felülírja a lánc-sorrendet.", "default": "auto"},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "web_scrape", "description": "JS-rendered weboldal teljes markdown-tartalma (headless Brave-böngészőn, Puppeteer-rel). ERŐSEBB mint web_fetch — kezeli a JavaScript-rendelt SPA-kat (Eurostat newsroom, MNB-honlap, ECB press release-ek). Tipikus minta: web_search → talált URL → web_scrape.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "Teljes URL (http/https)"},
            "wait_time": {"type": "integer", "description": "JS-render várakozás ms-ban (default 3000)", "default": 3000},
        }, "required": ["url"]}}},
    # ── Echolot / Hírmagnet (hírek + Agora) ──
    {"type": "function", "function": {
        "name": "echolot_query", "description": "Friss hírek az Echolot hírportálról (315 forrás, 8 nyelv, 63 sphere). Kereséshez adj query-t, különben a legfrissebb cikkek. top_stories=true → a címlap top sztorijai STORY ID-vel (kommenteléshez).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "FTS keresőkifejezés (opcionális; üresen a legfrissebb cikkek jönnek)", "default": ""},
            "spheres": {"type": "string", "description": "Sphere-szűrés vesszővel: hu_politics, hu_economy, global_economy, regional_us... (opcionális)", "default": ""},
            "days": {"type": "integer", "description": "Lookback napokban (1-21)", "default": 3},
            "limit": {"type": "integer", "description": "Max találat (1-50)", "default": 15},
            "language": {"type": "string", "description": "Nyelv-szűrő ISO kód: hu, en, de, fr... (opcionális)", "default": ""},
            "top_stories": {"type": "boolean", "description": "true → a címlap top sztorijai story_id + URL listaként (ha sztorira kell kommentelni)", "default": False},
        }}}},
    {"type": "function", "function": {
        "name": "agora", "description": "Echolot AGORA esszé-tér. action: feed=friss esszék listája (post_id-kkal), read=egy esszé teljes szövege, comments=esszé kommentfala, comment=KOMMENT ÍRÁSA az esszé alá. Komment posztolás PUBLIKUS weboldalra megy!",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["feed", "read", "comments", "comment"], "description": "Művelet"},
            "post_id": {"type": "string", "description": "Agora poszt ID (read/comments/comment esetén kötelező; a feed adja)", "default": ""},
            "body": {"type": "string", "description": "A komment szövege (action=comment esetén kötelező)", "default": ""},
            "parent_id": {"type": "string", "description": "Ha válasz egy meglévő kommentre: a szülő komment ID-ja", "default": ""},
            "limit": {"type": "integer", "description": "Max elem (feed/comments)", "default": 20},
        }, "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "story_comments", "description": "Echolot sztori-kommentfal (hírek alatti vita, ember+agent közös fal). body NÉLKÜL: olvassa a kommentfalat. body-VAL: kommentet ír a sztorihoz (PUBLIKUS!). Story ID az echolot_query top_stories eredményéből vagy a /story/<id> URL-ből.",
        "parameters": {"type": "object", "properties": {
            "story_id": {"type": "string", "description": "Echolot story/cluster ID"},
            "body": {"type": "string", "description": "Komment szövege — ha üres, csak olvas", "default": ""},
            "parent_id": {"type": "string", "description": "Válasz esetén a szülő komment ID-ja", "default": ""},
            "limit": {"type": "integer", "description": "Max komment olvasásnál (1-100)", "default": 30},
        }, "required": ["story_id"]}}},
    # ── Bridge memória és kommunikáció ──
    {"type": "function", "function": {
        "name": "read_memory", "description": "Bridge shared memory olvasása. Korábbi döntések, tudás, kontextus.",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "Memória kulcs (opcionális)", "default": ""},
            "category": {"type": "string", "description": "Kategória szűrés (opcionális)", "default": ""},
        }}}},
    {"type": "function", "function": {
        "name": "search_memory", "description": "Keresés a Bridge shared memory-ban kulcsszó alapján.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Keresési kifejezés"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "send_message", "description": "Bridge üzenet küldés másik instance-nak (web-claus, cli-claus, stb.).",
        "parameters": {"type": "object", "properties": {
            "recipient": {"type": "string", "description": "Címzett: web-claus, cli-claus, kommandant"},
            "subject": {"type": "string", "description": "Tárgy"},
            "message": {"type": "string", "description": "Üzenet szövege"},
        }, "required": ["recipient", "subject", "message"]}}},
    {"type": "function", "function": {
        "name": "search_discussions", "description": "Keresés a Bridge diskussziókban. Korábbi viták, döntések.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Keresési kifejezés"}}, "required": ["query"]}}},
    # ── Recipe-k (Operation Zahnrad) ──
    {"type": "function", "function": {
        "name": "list_recipes", "description": "Elérhető recipe-k (workflow template-ek) listázása.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "execute_recipe", "description": "Recipe futtatása — workflow végrehajtás AI agenten keresztül.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Recipe neve (pl. daily_briefing, weekly_macro_report)"},
            "model": {"type": "string", "description": "Agent: kimi / deepseek / glm5", "default": "deepseek"},
        }, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "create_recipe", "description": "Új recipe létrehozása — újrahasználható workflow template.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Egyedi recipe név"},
            "description": {"type": "string", "description": "Mit csinál a recipe"},
            "prompt_template": {"type": "string", "description": "A workflow prompt szövege"},
        }, "required": ["name", "description", "prompt_template"]}}},
    # ── Cron ütemezés ──
    {"type": "function", "function": {
        "name": "schedule_recipe", "description": "Recipe ütemezése — automatikus futtatás cron időzítéssel. Eredmény Telegramra jön.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Recipe neve"},
            "cron_schedule": {"type": "string", "description": "Cron: '0 7 * * *' (7:00 minden nap), '0 8 * * 1' (hétfő 8:00), '30 17 * * 1-5' (hétköznap 17:30). 'off' = kikapcsol"},
            "model": {"type": "string", "description": "Agent: glm5 / deepseek / kimi / all", "default": "glm5"},
        }, "required": ["name", "cron_schedule"]}}},
    # ── Fájl export ──
    {"type": "function", "function": {
        "name": "export_task", "description": "AI feladat exportálása fájlba. Telegramra küld, és opcionálisan emailben is csatolmányként.",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "integer", "description": "AI task ID száma"},
            "format": {"type": "string", "description": "Formátum: docx / xlsx / pptx", "enum": ["docx", "xlsx", "pptx"]},
            "email_to": {"type": "string", "description": "Ha emailben is kell küldeni, ide írd a címet", "default": ""},
        }, "required": ["task_id", "format"]}}},
    # ── Fájl továbbítás ──
    {"type": "function", "function": {
        "name": "send_file_email", "description": "Feltöltött fájl (fotó, dokumentum) továbbküldése emailben. Upload #ID szükséges.",
        "parameters": {"type": "object", "properties": {
            "file_id": {"type": "integer", "description": "Upload fájl ID (pl. #2)"},
            "to": {"type": "string", "description": "Email cím"},
            "subject": {"type": "string", "description": "Email tárgy", "default": "Claus — csatolt fájl"},
        }, "required": ["file_id", "to"]}}},
    # ── Kép elemzés ──
    {"type": "function", "function": {
        "name": "analyze_upload", "description": "Feltöltött kép elemzése a Kimi (K3.1 Pro) vision modellel. Upload #ID szükséges.",
        "parameters": {"type": "object", "properties": {
            "file_id": {"type": "integer", "description": "Upload fájl ID (pl. #2)"},
            "prompt": {"type": "string", "description": "Kérdés a képről", "default": "Mit latsz a kepen? Ird le reszletesen, magyarul."},
        }, "required": ["file_id"]}}},
    # ── Közgazdasági adatok (Makronóm) ──
    {"type": "function", "function": {
        "name": "mnb_rates", "description": "ECB napi árfolyamok (EUR/HUF, USD/HUF, CHF/HUF stb.) + HUF keresztárfolyamok. Friss, mai adat.",
        "parameters": {"type": "object", "properties": {
            "currencies": {"type": "string", "description": "Szűrés: 'HUF,USD,CHF' (üres = fő devizák)", "default": ""},
        }}}},
    {"type": "function", "function": {
        "name": "market_quote", "description": "Piaci adat Yahoo Finance-ből: részvény, deviza, áru, index, kötvényhozam, kripto. FRISS árak.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "Ticker: EURHUF=X, OTP.BD, ^BUX.BD, ^GSPC, GC=F (arany), BZ=F (Brent), BTC-USD, ^TNX (US 10Y)"},
        }, "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "fred_data", "description": "FRED makro idősorok: US kamatok, infláció, GDP, munkanélküliség, hozamok, M2, VIX, stb.",
        "parameters": {"type": "object", "properties": {
            "series_id": {"type": "string", "description": "FRED ID: UNRATE, DGS10, DGS2, T10Y2Y, CPIAUCSL, GDP, DFF, MORTGAGE30US, UMCSENT, M2SL, VIXCLS"},
            "limit": {"type": "integer", "description": "Utolsó N adatpont", "default": 12},
        }, "required": ["series_id"]}}},
    {"type": "function", "function": {
        "name": "policy_rates", "description": "Jegybanki alapkamatok — MNB, ECB, Fed, CNB, NBP, BNR, BoE, stb. BIS/DBnomics forrás.",
        "parameters": {"type": "object", "properties": {
            "countries": {"type": "string", "description": "BIS kódok: HU (MNB), XM (ECB), US (Fed), CZ, PL, RO, GB, JP, CH, TR", "default": "HU,XM,US,CZ,PL"},
        }}}},
    {"type": "function", "function": {
        "name": "econ_calendar", "description": "Közelgő gazdasági adatközlések és jegybanki események (US + EU).",
        "parameters": {"type": "object", "properties": {
            "days_ahead": {"type": "integer", "description": "Hány napra előre (max 30)", "default": 7},
        }}}},
]

# Tools that need confirmation before execution
#: Egy válaszban legfeljebb ennyi modellkör (az utolsó eszköz nélkül, a szintézisé). A kombinált feladat (hír + szám
#: + jog + dokumentum) 4-6 kör; a régi 5-ös plafon (4 eszközkör) a kombinálást vágta el.
MAX_KOR = 12
CONFIRM_TOOLS = {"send_email", "calendar_create", "tweet_tervezet"}
#: A KÓD IS kapu (2026-11-22): ezeknél a modell hívása csak javaslat (action_drafts), a valódi írás a Kommandant
#: kifejezett igenje után történik (feldwebel/jovahagyas.py). A `send_email` egyelőre a leírás szerinti kérdésen át.
#: A tweet (2026-11-23, twitter_poszt.py) szigorúbb: egy szavas igen nem elég — a kabin gombja, vagy a kifejezett
#: „posztold ki a tervezetet: <első szavak>” (`twitter_poszt.kezel`, a kör elején, LLM nélkül).
KODKAPU_TOOLS = {"calendar_create", "tweet_tervezet"}


# ── System prompt ──────────────────────────────────────────────────
#
# KARCSÚ, CACHE-ELHETŐ MAG (2026-11-23). A Kommandant: „Túl sokba kerül a futás… Valami tényleg nem oké.” Mérve
# (count_tokens, Haiku 5.5, éles DB-mentés): a régi rendszerszöveg ~32 ezer token volt (a sablon 9,9k, a Pyramid-kontextus
# a DeepSeek identitásával, Agora-personájával, 20 közös memóriával és ~60 RAG-tétellel ~20k), és MINDEN üzenetnél más
# (dátum, RAG), így üzenetek között semmi nem jött cache-ből. Most:
#   * ez a szöveg ÁLLANDÓ (dátum, RAG, feladat nélkül) → a kapu explicit, 1 órás cache-töréspontot tesz a végére
#     (`cache_rendszer`); a mért üzenetközök (2026-04–11, 232 üzenet) 55%-a 5 percen, 78%-a 60 percen belüli → az 1 órás
#     TTL a stabil előtag várható árát 0,62×-ről 0,51×-re viszi (Haikun; Sonneten 0,59× → 0,47×);
#   * a változó rész (idő, a kérdéshez illő 3 RAG-tétel, nyitott feladatok) a PILLANATKÉP: egy rendszer-üzenet a
#     Kommandant üzenete ELŐTT, a töréspont MÖGÖTT (`_pillanatkep`);
#   * a kategória-útmutatók (AI-ügynökök, receptek, Agora, melléklet, jog, egészség, levél, naptár…) a kategória
#     eszközeivel együtt jönnek (`feldwebel/eszkoz_ut.py`) — szó szerint a régi szakaszok;
#   * az eszközöket prózában ismétlő ESZKÖZTÉRKÉP a betöltő (`eszkoztar`) leírása lett (cost-optimization § 2.2).

SYSTEM_PROMPT_TEMPLATE = """Te a Feldwebel vagy — a Kommandant személyes asszisztense (Telegram és a webes kabin) a Claus-Bridge rendszeren belül.
Időzóna: CET (Budapest). A mai dátumot és a pontos időt minden üzenetnél a PILLANATKÉP adja — az a valós dátum, a te \
tudásod ennél régebbi; mai vagy friss tényt csak eszközből állíts.

A Kommandant: Dr. Csizmadia Tamás, senior kutató (Makronóm Intézet), AI-eszközfejlesztő. Tömör, direkt, néha ironikus; \
utálja a bürokráciát és a felesleges bonyolítást. A részletes adatai (űrlaphoz, hivatalos levélhez) a levelezés és a \
melléklet útmutatójában jönnek.

Szereped:
- Gyors, lényegre törő válaszok magyarul
- Ha a Kommandant személyt vagy emailt keres, MINDIG használd a gmail_search tool-t
- Ha frissítést kér, használd a gmail_poll vagy calendar_poll tool-t

ESZKÖZÖK: a mag mindig nálad van; ami más kell, azt az `eszkoztar` tölti be kategóriánként (az útmutatójával együtt). \
A kör elején már betöltött kategóriát ne töltsd be újra.

SZERV-VÁLASZTÁS (kötelező): a megnevezett keresőt (`web_search` `engine`) a válaszban MONDD KI találatszámmal \
("SearXNG, 27 találat"); ha a `_bridge_engine_substituted` sor is ott van, a kért motor NEM felelt — ezt is mondd ki \
("a SearXNG nem válaszolt, ezért brave-mcp-vel kerestem"). Néma csere TILOS — a Kommandant szervet parancsolt, nem \
javaslatot tett.

GAZDASÁGI ADAT: árfolyamhoz, kamathoz, piaci és makróadathoz a dedikált adateszközeinket hívd AZONNAL (mag: mnb_rates, \
market_quote; a többi a `makro` kategóriában) — ne ai_task-ot, és ne emlékezetből.

⚠️ AMIT A TOOL NEM ADOTT VISSZA, AZ NEM KERÜL A TÁBLÁZATBA.
  * Ha a `policy_rates` három országot ad, a táblázat HÁROM soros. NE egészítsd
    ki emlékezetből egy negyedikkel — akkor sem, ha "tudod" az értéket.
  * A betanított tudásod ELAVULT: a jegybankok havonta döntenek. Egy fejből
    írt kamat úgy néz ki, mint a többi sor, de nem ellenőrizte senki.
  * Ha egy országra kell adat, HÍVD ÚJRA a toolt azzal az országgal —
    `policy_rates(countries="PL,RO,CZ")`. Egy plusz hívás olcsó; egy rossz
    szám a Kommandant táblázatában nem az.
  * Ha a tool nem adja meg, írd oda, hogy "nincs rá friss adatunk" — a hiány
    bevallása helyes válasz.
  MEGTÖRTÉNT (2026-08-31): a válasz öt jegybankot sorolt fel; a tool hármat
  adott, a lengyel és a román sort a modell írta hozzá — mindkettő rossz volt
  (NBP 3,5 a valós 3,75 helyett, BNR 3,3 a valós 6,5 helyett).

⚠️ AZ OKOT SEM TALÁLHATOD KI. Piaci mozgást ne köss névhez, beszédhez vagy
  eseményhez, ami nincs a kapott adatban. Ha nincs ott, akkor a válasz
  szempontjából nem történt meg.

FORRÁS ÉS DÁTUM: ha a keresés üres vagy hibás, forrást, URL-t, domaint vagy konkrét számot kitalálni TILOS — mondd ki, \
hogy nincs megbízható forrás. Modell-becslést ne adj el élő adatként. Egy adat dátuma csak a tool-eredményben szereplő \
dátum vagy a `_bridge_fetched_at` bélyegző lehet. A más ügynök által hozott friss adat nem „kitalált” csak azért, mert \
a tréningedben nincs.

ÁLTALÁNOS MUNKAMÓDSZER — minden kérésre (a Kommandant: „általános jelleggel kombinálni minden eszközünket”):
1. Mi a kért EREDMÉNY? Válasz, dokumentum, művelet (levél, naptár, feladat), vagy figyelés/ütemezés.
2. Bontsd részekre, és minden részhez a legjobb SAJÁT forrást válaszd (az `eszkoztar` kategóriái); webes keresést csak
   akkor, ha a saját nem fed. Ami egymástól független, azt EGY körben, egyszerre hívd (több eszközhívás egy válaszban).
3. KOMBINÁLD: a hírt kösd össze a számmal, a jogszabállyal, a céggel, a melléklettel. Minden szám és állítás mögött
   az eszköz forrása legyen — amit eszköz nem adott, azt ne állítsd, a hiányt mondd ki.
4. Ha egy DÖNTŐ tény hiányzik, kérdezz (legfeljebb 3 kérdés, számozva), de amit már tudsz, azt előbb mondd el.
5. Ha az eredmény dokumentum, táblázat, prezentáció, kép vagy videó: a `dokumentum` kategória `studio` eszközével
   készítsd (FORMÁZVA, ellenőrizve — az útmutató a kategóriával jön), és add át az edit_url-t.
6. Mellékhatással járó lépés (levél, meghívó, megosztás, közzététel, figyelő vagy ütemezés indítása, fizetés) csak a
   Kommandant kifejezett igenje után.

CÍMJEGYZÉK — így éred el a többieket (ne mondd, hogy „nincs csatornád” hozzájuk):
- FABER (a műhely a Kommandant gépén, Claude Code: kódot ír, hibát javít, teszteli, Telegramon jóváhagyást kér) →
  `faber` eszköz. A „Faber, …” kezdetű üzenet közvetlenül neki megy, nem neked.
- KIMI, DEEPSEEK, GLM (önálló AI-modellek a SiliconFlow-n, KÜLÖN memóriával — NEM email kontaktok, SOHA ne keress
  rájuk a Gmail-ben) → `ai_query` / `ai_task` (`ai_ugynokok`). Ha a Kommandant feladatot ad ki ("kérdezd meg",
  "nézzen utána", "elemezze") vagy név szerint említ egy agentet → AZONNAL add ki, NE válaszolj szövegben.
  Tudásbázis-tartalom NEM mehet nekik. A „Kimi, …” üzenet közvetlenül hozzájuk megy.
- CLI-CLAUS (Claude Code a Kommandant gépén): a Kommandant „cli-Claus, …” kezdetű üzenete KÖZVETLENÜL neki megy, és ha
  a gépen fut, a Telegramon ő maga válaszol. Ha a Kommandant azt kéri, hogy írj neki: `send_message` (recipient:
  cli-claus), a Kommandant SAJÁT szavaival az előző üzenetéből — ne kérdezz vissza a szövegre, és ne a Fabernek szólj.
- WEB-CLAUS (Claude a claude.ai-on) → `send_message` (recipient: web-claus).
- A FUTÁR (a Kommandant gépe: teljes tudásbázis, ChatGPT, Gemini) → `tudasbazis`, `chatgpt`, `gemini`.
- NACHTSCHICHT (az éjszakai műszak, 05:50) → nincs közvetlen címe; reggel jelent a Telegramon.
- FELADAT-ÜGYNÖKÖK (a Kommandant névvel ellátott munkásai) → `ugynok`; Telegramon szólnak, a kabinban saját ablakuk van.
- HOG = X-posztra adatalapú válasz + Echolot-oldal → `hog_keszit`.

Szabályok:
- Magyar nyelv, hacsak nem kérnek mást
- Tömör válaszok (max 3-5 mondat, kivéve ha elemzést, iratot vagy tanácsot kérnek)
- NE HAZUDJ — ha a tool nem ad eredményt, mondd meg
- Email küldés SOHA nem automatikus — mindig kérj megerősítést"""

#: A mag és az összes kategória-útmutató együtt — amit a Feldwebel összesen tudhat (a régi egyetlen rendszerszöveg
#: tartalma). Dokumentációs és teszt-célra: a modellhez a mag mindig, egy útmutató a kategóriájával kerül.
def teljes_utmutato() -> str:
    from feldwebel import eszkoz_ut
    return "\n\n".join([SYSTEM_PROMPT_TEMPLATE, eszkoz_ut.betolto_def()["function"]["description"]]
                       + [v["utmutato"] for v in eszkoz_ut.KATEGORIAK.values() if v["utmutato"]])


#: A pillanatkép felső korlátai: a RAG legfeljebb 3 tétel × 700 karakter (~0,8k token), a feladat legfeljebb 10 sor.
RAG_TETEL = 3
RAG_KAR = 400
#: egy kulcsszó csak akkor számít, ha a tételek legfeljebb ekkora hányadában fordul elő (a „forint” a piaci briefek
#: felében ott van: nem különböztet meg semmit)
RAG_RITKA = 0.02
FELADAT_SOR = 10
_STOP = {"hogy", "mint", "vagy", "akkor", "most", "nekem", "neked", "kérlek", "légyszi", "szia", "helló", "hello",
         "mi a", "milyen", "melyik", "mikor", "miért", "hogyan", "lesz", "volt", "van", "nincs", "csak", "még", "már",
         "is", "az", "egy", "ezt", "azt", "meg", "majd", "mert", "pedig", "fel", "le", "ki", "be", "és", "de"}


def _kulcsszavak(szoveg: str) -> list[str]:
    """A RAG-relevanciához: a legalább 4 betűs, nem töltelék szavak 5 karakteres tövei (ékezet nélkül)."""
    from feldwebel.eszkoz_ut import ekezet_nelkul
    szavak = _re.findall(r"[a-z0-9]{4,}", ekezet_nelkul(szoveg))
    return list(dict.fromkeys(s[:5] for s in szavak if s not in _STOP))[:12]


def _rag_top(query: str, n: int = RAG_TETEL) -> list[dict]:
    """A kérdéshez legjobban illő n tétel a flotta munkanaplójából és a közös memóriából (kulcsszó-tő találat, aztán
    frissesség). Csak valódi találat jön — a „mi a helyzet?” típusú üzenethez nincs RAG."""
    kulcs = _kulcsszavak(query)
    if not kulcs:
        return []
    from feldwebel.eszkoz_ut import ekezet_nelkul
    sorok = []
    try:
        from pyramid.memory_rag import load_all_rag
        sorok += [{"forras": r["agent_id"], "cim": r["task_title"], "szoveg": r["content"], "ido": r["timestamp"]}
                  for r in load_all_rag()]
    except Exception as e:  # noqa: BLE001
        logger.debug("RAG nem olvasható: %s", e)
    try:
        from pyramid.memory_shared import load_shared_memory
        sorok += [{"forras": r["added_by"], "cim": r["category"], "szoveg": r["content"], "ido": r["timestamp"]}
                  for r in load_shared_memory()]
    except Exception as e:  # noqa: BLE001
        logger.debug("közös memória nem olvasható: %s", e)
    import math
    szovegek = [ekezet_nelkul(f"{r['cim']} {str(r['szoveg'] or '')[:3000]}") for r in sorok]
    df = {k: sum(1 for t in szovegek if k in t) for k in kulcs}
    hatar = max(3, int(RAG_RITKA * len(szovegek)))
    suly = {k: math.log((len(szovegek) + 1) / (d + 1)) for k, d in df.items() if 0 < d <= hatar}
    if not suly:
        return []
    kell = min(2, len(suly))           # legalább két RITKA kulcsszó egyezzen (ha csak egy ritka van, az az egy)
    pont = []
    for r, t in zip(sorok, szovegek):
        p = sum(w for k, w in suly.items() if k in t)
        if p > 0 and sum(1 for k in suly if k in t) >= kell:
            pont.append((round(p, 3), str(r["ido"] or ""), r, t))
    pont.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out, latott = [], []
    for _, _, r, t in pont:
        szavak = set(t[:800].split())
        # ugyanaz a munka gyakran kétszer van meg (a futás naplója és a közös memória): szóhalmaz-átfedéssel szűrve
        if any(len(szavak & l) > 0.6 * max(1, min(len(szavak), len(l))) for l in latott):
            continue
        latott.append(szavak)
        out.append(r)
        if len(out) >= n:
            break
    return out


def _pillanatkep(ctx, query: str = "") -> str:
    """A VÁLTOZÓ rész (a cache-töréspont mögött): most, a kérdéshez illő RAG (≤3), a nyitott feladatok (≤10)."""
    from zoneinfo import ZoneInfo
    most = datetime.now(ZoneInfo("Europe/Budapest"))
    sorok = [f"PILLANATKÉP — most: {most:%Y-%m-%d} ({WEEKDAYS_HU[most.weekday()]}) {most:%H:%M}, Budapest. "
             f"Ez a valós dátum."]
    rag = _rag_top(query)
    if rag:
        sorok.append("A KORÁBBI MUNKÁBÓL a kérdéshez illő (ADAT; több: search_memory / tudasbazis):")
        for r in rag:
            szoveg = " ".join(str(r["szoveg"] or "").split())
            sorok.append(f"- [{str(r['forras']).upper()} · {str(r['ido'])[:10]}] {str(r['cim'])[:120]} — "
                         f"{szoveg[:RAG_KAR]}{'…' if len(szoveg) > RAG_KAR else ''}")
    feladatok = _fetch_open_tasks(ctx)
    if feladatok:
        sorok.append(feladatok)
    return "\n".join(sorok)


def _build_system_prompt(ctx, query: str = "") -> str:
    """A STABIL mag (cache-elhető): dátum, RAG és feladat NÉLKÜL — azok a `_pillanatkep`-ben, a töréspont mögött.
    A `query` paraméter a régi hívók miatt marad (a mag nem függ tőle)."""
    return SYSTEM_PROMPT_TEMPLATE

# ── Main respond function (agent loop) ────────────────────────────


async def respond(text: str, chat_id: str, agent_id: str = "deepseek") -> str:
    """
    Generate a response with native tool use.
    Agent loop: DeepSeek calls tools → results fed back → final response.
    First round: tool_choice=required (forces tool use, no hallucination).
    """
    ctx = get_ctx()

    # TWEET (2026-11-23): „posztold ki a tervezetet: <első szavak>” — a KÓD dönt, LLM nélkül (twitter_poszt.kezel):
    # posztol, ha pontosan egy függő tervezet kezdődik így; különben kimondja, miért nem. Egy szavas igen erre nem jó.
    import twitter_poszt as _twitter
    _tw = await _twitter.kezel(ctx, text, chat_id)
    if _tw is not None:
        _store_bridge_reply(ctx, text, _tw, agent_id, eszkozok=["tweet"])
        return _tw

    # MEGERŐSÍTÉS (2026-11-22): a legutóbbi naptárjavaslatra egy szó elég („igen”, „mehet” / „nem”) — LLM nélkül,
    # a kód dönt (feldwebel/jovahagyas.py). Ami nem ilyen, az a rendes kör.
    _jv = await _jovahagyas.kezel(ctx, text, chat_id)
    if _jv is not None:
        _store_bridge_reply(ctx, text, _jv, agent_id, eszkozok=["calendar_create"])
        return _jv

    # STRATÉGIAI NAPLÓ, HELYESBÍTÉS (2026-11-24): „#31 igaz” — a Kommandant ítélete egy állításról, LLM nélkül
    import strategiai as _strat
    _hb = _strat.helyesbites(text)
    if _hb is not None:
        await ctx.telegram_push(f"🎖 <b>STRATÉGIAI NAPLÓ</b>\n\n{html_escape(_hb)}")
        _store_bridge_reply(ctx, text, _hb, agent_id, eszkozok=["strategiai_naplo"])
        return _hb

    # Store user message in history
    add_message(chat_id, "user", text)

    # Build messages
    history = get_history(chat_id, limit=8)
    # STRATÉGIAI MÓD (2026-11-24, strategiai.py): a szándékfelismerő (LLM nélkül) dönt — „gondolkodj”, „értékeld”,
    # „mi a véleményed” mindig; helyzetértékelés, „mi következik”, „mit tegyünk”, döntés, vita, elemzés; és a stratégiai
    # forduló (a reggeli nyitó is) folytatása. Ilyenkor a rendszerszöveg a stratégiai prompttal bővül, a modell Opus
    # (`llm_kapu` „strategiai” sora), a 12 nyelvű kép eszközei előtöltve. Különben MINDEN a régi (karcsú) úton megy.
    strat = _strat.mod(text, history)
    system_prompt = _build_system_prompt(ctx, query=text) + (_strat.rendszer_toldat() if strat.aktiv else "")
    messages = [{"role": "system", "content": system_prompt}]
    # TUDÁSBÁZIS = CSAK CLAUDE (a Kommandant, 11-22): a kör, amelyben a Kommandant tudásbázisából van tartalom (most
    # hívott `tudasbazis`, vagy egy korábbi, így jelölt válasz az előzményben), csak Claude-dal megy — SiliconFlow-
    # tartalék nélkül —, és nem hív SiliconFlow-ügynököt vagy általuk olvasott tárat (futar_kliens.sf_fele). Ha az
    # Anthropic nem elsődleges vagy nem elérhető, a jelölt előzmény tartalma ki sem megy.
    import futar_kliens
    claude_kor = futar_kliens.claude_elerheto()
    vedett = False
    for msg in history:
        tartalom = msg["content"]
        if futar_kliens.vedett_elozmeny(msg.get("agent_id")):
            if claude_kor:
                vedett = True
            else:
                tartalom = futar_kliens.REJTETT_ELOZMENY
        messages.append({"role": msg["role"], "content": tartalom})
    #: ebben a körben a `tudasbazis` valódi találatot hozott → a válasz jelölve kerül az előzménybe
    tudasbazis_kor = False

    # PILLANATKÉP (2026-11-23): a változó rész a cache-töréspont MÖGÖTT — egy rendszer-üzenet közvetlenül a Kommandant
    # mostani üzenete ELŐTT (a kapu a user-fordulóba fűzi). A mag és az eszközlista így üzenetről üzenetre bájtra ugyanaz.
    pk = _pillanatkep(ctx, text)
    if pk:
        hely = len(messages) - 1 if len(messages) > 1 and messages[-1]["role"] == "user" else len(messages)
        messages.insert(hely, {"role": "system", "content": pk})

    # ESZKÖZÖK IGÉNY SZERINT (2026-11-23, feldwebel/eszkoz_ut.py): az eszközlista állandó (mag + betöltő + lapozó, a
    # többi halasztva); a szándék-útválasztó (LLM nélkül) a valószínű kategóriákat előre betölti — egy `eszkoztar`-hívás
    # és -eredmény a kör elején. Ami ezen kívül kell, azt a modell maga tölti be.
    from feldwebel import eszkoz_ut
    import kivonat as _kivonat
    eszkozkeszlet = await _eszkozkeszlet()
    elerheto = {t["function"]["name"] for t in eszkozkeszlet}
    elozo = " ".join(str(m["content"])[:600] for m in history[-3:-1]) if len(history) > 1 else ""
    kategoriak = eszkoz_ut.szandek(text, elozo)
    if strat.aktiv:      # a 12 nyelvű kép (Echolot), a számok (makró) és a Kommandant korábbi gondolatai (tudásbázis)
        kategoriak = [k for k in eszkoz_ut.KATEGORIAK if k in set(kategoriak) | set(_strat.ELOTOLTES)]
    elotoltes, betoltve = eszkoz_ut.elotoltes(kategoriak, elerheto)
    messages += elotoltes
    #: a hurok kivonat-tára: a nagy eszközeredmény teljes szövege ebben a körben lapozható (kivonat.py)
    tar = _kivonat.EredmenyTar()
    #: a valódi eszközeredmények száma (a betöltő és a lapozó nem számít) — a mélyebb szint jelzéséhez
    valodi_eredmeny = 0
    #: HOPSZ (11-23, hopsz.py): a kör eszközhibái és a válasz — képesség-hiánynál a végén EGY kérelem a Fabernek
    import hopsz as _hopsz
    hopsz_kor = _hopsz.Kor("a Feldwebel", text)

    model_id = ctx.siliconflow_models.get(agent_id, "deepseek-ai/DeepSeek-V4-Pro")

    # Agent loop — max 5 rounds of tool calls (last round forces text)
    # Round 0: force_tool=True → DeepSeek MUST call a tool (no hallucination)
    final_content = ""
    #: A kapu jegyzetei (modellváltás, tartalékra váltás): a válasz végére kerülnek — néma váltás nincs.
    kapu_notes: list = []
    #: KI VÁLASZOLT VALÓJÁBAN (`served_by`) és milyen eszközökkel — a tárgyba kerül (a webes felület előzménye
    #: onnan olvassa), webes körben élőben is látszik (`kimenet`).
    kiszolgalo = None
    eszkoz_nevek: list = []
    import llm_kapu as _lk
    from melleklet import van_melleklet
    kotetlen = van_melleklet(text) or _lk.tanacs_keres(text)
    for round_num in range(MAX_KOR):
        # Több eszközös, többlépéses feladatnál (már legalább 2 eszközeredmény van) a kapu a közép szintet adja:
        # a kombinálás és a szintézis a Sonneté, az egyszerű kérdés marad a Haikun.
        melyebb = valodi_eredmeny >= 2
        data = await _call_deepseek(
            ctx, model_id, messages,
            use_tools=(round_num < MAX_KOR - 1), tools=eszkozkeszlet,
            purpose=(_strat.CEL_FELDWEBEL if strat.aktiv
                     else "feldwebel/melyebb/telegram" if melyebb else "feldwebel/telegram"),
            # mellékletnél és tanácskérésnél NEM kényszerítünk eszközt az első körben: vissza kell tudnia kérdezni
            force_tool=(round_num == 0 and not kotetlen),
            notes=kapu_notes,
            csak_claude=vedett,
        )
        if not data:
            break
        kiszolgalo = data.get("model") or kiszolgalo
        kor_modell = str(data.get("model") or "")      # az eszközt hívó kör VALÓDI modellje (a tudásbázis-kapunak)
        if _kimenet is not None:
            _kimenet.modell(data, ugynok="feldwebel")

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "")

        content = msg.get("content", "") or ""
        # Safety net: if a thinking-mode SF response delivered the actual
        # answer in reasoning_content with empty content, fall back to it
        # rather than treating the whole round as empty.
        if not content.strip():
            reasoning = msg.get("reasoning_content", "") or ""
            if reasoning.strip():
                content = reasoning
        tool_calls = msg.get("tool_calls")

        # Case A: Proper JSON tool_calls
        if tool_calls and finish_reason == "tool_calls":
            messages.append(msg)
            # Az egy körben kért eszközök EGYSZERRE futnak (független lekérdezések: hír + szám + jog…);
            # az eredmények a kérés sorrendjében mennek vissza.
            hivasok = []
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                fn_args_raw = tc.get("function", {}).get("arguments", "{}")
                try:
                    fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                except json.JSONDecodeError:
                    fn_args = {}
                logger.info("Tool call round %d: %s(%s)", round_num, fn_name, fn_args)
                hivasok.append((tc.get("id", ""), fn_name, fn_args or {}))

            async def _egy(fn_name, fn_args, kor_modell=kor_modell, vedett=vedett):
                if fn_name == eszkoz_ut.BETOLTO_NEV:
                    szoveg, nevek, kat = eszkoz_ut.betolt((fn_args or {}).get("kategoriak"), elerheto, kesz=betoltve)
                    betoltve.update(kat)
                    return szoveg, nevek, szoveg
                if fn_name == _kivonat.LAPOZ_NEV:
                    lap = tar.lapoz(fn_args)
                    return lap, None, lap
                ism = await tar.ismetles(fn_name, fn_args, feladat=text)   # ugyanaz az URL: nem tölt le újra
                if ism is not None:
                    return ism, None, ism
                _et = _kimenet.eszkoz_indul(fn_name, fn_args) if _kimenet is not None else None
                try:
                    result = await _execute_tool(fn_name, fn_args, ctx, kor={"chat_id": chat_id, "text": text},
                                                 modell=kor_modell, vedett=vedett)
                except Exception as e:  # noqa: BLE001 — egy eszköz hibája ne vigye el a kört
                    result = json.dumps({"error": f"{type(e).__name__}: {str(e)[:300]}"}, ensure_ascii=False)
                hopsz_kor.eszkoz(fn_name, fn_args, result)
                if _kimenet is not None:
                    _kimenet.eszkoz_kesz(_et, result)
                # NAGY EREDMÉNY → KIVONAT (olvasó-lépés, a teljes szöveg lapozható), MIELŐTT a kontextusba kerül
                return await tar.feldolgoz(fn_name, fn_args, result, feladat=text), None, result

            eredmenyek = await asyncio.gather(*(_egy(n, a) for _, n, a in hivasok))
            for (tc_id, fn_name, _), (result, hivatkozas, teljes) in zip(hivasok, eredmenyek):
                tool_msg = {"role": "tool", "tool_call_id": tc_id, "content": result}
                if fn_name == eszkoz_ut.BETOLTO_NEV:
                    tool_msg["tool_reference"] = hivatkozas
                elif fn_name != _kivonat.LAPOZ_NEV:
                    eszkoz_nevek.append(fn_name)
                    valodi_eredmeny += 1
                messages.append(tool_msg)
                if futar_kliens.tudasbazis_tartalom(fn_name, teljes) or (
                        fn_name == _kivonat.LAPOZ_NEV and tudasbazis_kor):
                    vedett = tudasbazis_kor = True     # innentől a kör csak Claude-dal megy
            continue

        # Case B: Text-based tool calls (DeepSeek DSML or Kimi markers)
        import re
        text_markers = ["<｜DSML｜", "<|tool_call", "function_calls>", "tool_calls_section"]
        if not tool_calls and any(m in content for m in text_markers):
            # Parse tool name and parameters from text
            # DSML format: <｜DSML｜invoke name="tool_name"> <｜DSML｜parameter name="x" string="true">value</｜DSML｜parameter>
            # Kimi format: <|tool_call_begin|>functions.web_search:2<|tool_call_argument_begin|>{"query": "..."}
            parsed_tool = _parse_text_tool_call(content)
            if parsed_tool:
                fn_name, fn_args = parsed_tool
                logger.info("Text tool call round %d: %s(%s)", round_num, fn_name, fn_args)
                if fn_name == eszkoz_ut.BETOLTO_NEV:          # a SiliconFlow-modell minden eszközt lát: csak az útmutató
                    result, _, kat = eszkoz_ut.betolt((fn_args or {}).get("kategoriak"), elerheto, kesz=betoltve)
                    betoltve.update(kat)
                elif fn_name == _kivonat.LAPOZ_NEV:
                    result = tar.lapoz(fn_args)
                else:
                    _et = _kimenet.eszkoz_indul(fn_name, fn_args) if _kimenet is not None else None
                    result = await _execute_tool(fn_name, fn_args, ctx, kor={"chat_id": chat_id, "text": text},
                                                 modell=kor_modell, vedett=vedett)
                    hopsz_kor.eszkoz(fn_name, fn_args, result)
                    if _kimenet is not None:
                        _kimenet.eszkoz_kesz(_et, result)
                    eszkoz_nevek.append(fn_name)
                    valodi_eredmeny += 1
                    if futar_kliens.tudasbazis_tartalom(fn_name, result):
                        vedett = tudasbazis_kor = True
                    result = await tar.feldolgoz(fn_name, fn_args, result, feladat=text)
                messages.append({"role": "assistant", "content": f"(Tool végrehajtva: {fn_name})"})
                messages.append({"role": "user", "content": f"Tool eredmény ({fn_name}):\n{result}\n\nFoglald össze az eredményt a Kommandantnak."})
                continue

        # Text response — we're done
        final_content = content
        break

    # Last-resort synthesis: V4-Pro on SiliconFlow occasionally exits the loop
    # with empty content even after successful tool calls (no_tools final round
    # returns empty body / reasoning-only). If we have tool results in the
    # message history, force one more text-only call with an explicit synthesis
    # instruction. Same pattern as _run_agent_with_tools in server.py.
    if not final_content and any(m.get("role") == "tool" for m in messages):
        tool_count = sum(1 for m in messages if m.get("role") == "tool")
        logger.info(
            "Feldwebel: agent loop exited empty after %d tool result(s); forcing last-resort synthesis",
            tool_count,
        )
        synth_messages = list(messages) + [{
            "role": "user",
            "content": (
                "Foglald össze a fenti tool-eredményeket KONKRÉT SZÁMOKKAL, magyarul, "
                "a Kommandantnak. NE hívj újabb tool-t — text-only válasz kell. "
                "Ha valamit nem fed le az eddigi adatkör, mondd meg."
            ),
        }]
        try:
            data = await _call_deepseek(ctx, model_id, synth_messages, use_tools=False, force_tool=False,
                                        notes=kapu_notes, csak_claude=vedett, tools=eszkozkeszlet,
                                        purpose=_strat.CEL_FELDWEBEL if strat.aktiv else "feldwebel/telegram")
            if data:
                kiszolgalo = data.get("model") or kiszolgalo
                if _kimenet is not None:
                    _kimenet.modell(data, ugynok="feldwebel")
                choice = data.get("choices", [{}])[0]
                msg = choice.get("message", {})
                synth_content = (msg.get("content") or "").strip()
                if not synth_content:
                    synth_content = (msg.get("reasoning_content") or "").strip()
                if synth_content:
                    final_content = synth_content
                    logger.info(
                        "Feldwebel last-resort synthesis succeeded (%d chars)",
                        len(final_content),
                    )
                else:
                    logger.warning(
                        "Feldwebel last-resort synthesis also empty (finish_reason=%s)",
                        choice.get("finish_reason"),
                    )
        except Exception as e:
            logger.error("Feldwebel last-resort synthesis failed: %s: %s",
                         type(e).__name__, e)

    # HOPSZ: a kör eszközhibái és a válasz („nem tudom, mi a …”, „nincs … eszközöm”) — determinisztikus, sosem dob
    hopsz_kor.vege(final_content, vedett=vedett)

    if not final_content:
        # Debug: show what happened in each round
        debug_parts = [f"Agent loop ended with no content (model={model_id})"]
        # Show last few messages for debugging
        for m in messages[-4:]:
            role = m.get("role", "?")
            c = "(rejtve: tudásbázis-tartalom)" if vedett and role == "tool" else (m.get("content") or "")[:150]
            tc = m.get("tool_calls")
            if tc:
                debug_parts.append(f"  [{role}] tool_calls={json.dumps([t.get('function',{}).get('name','?') for t in tc])}")
            else:
                debug_parts.append(f"  [{role}] {c[:150]}")
        debug_msg = "\n".join(debug_parts)
        logger.error("Feldwebel empty response: %s", debug_msg)
        await ctx.telegram_push(f"<b>FELDWEBEL DEBUG</b>\n<pre>{html_escape(debug_msg[:1500])}</pre>")
        return ""

    # STRATÉGIAI NAPLÓ (2026-11-24): a stratégiai válasz ellenőrizhető állítása (a formai sor, determinisztikusan)
    # a Postgres-naplóba — a havi kör pontozza. A rögzítés (vagy a hiánya) a válasz alatt látszik.
    strat_jegyzet = strat.jegyzet
    if strat.aktiv:
        _all = _strat.allitas_kivesz(final_content)
        if _all is not None:
            _r = await asyncio.to_thread(_strat.rogzit, _all, forras="feldwebel", kerdes=text[:500],
                                         modell=kiszolgalo, szandek=strat.ok)
            strat_jegyzet = _strat.rogzites_jegyzet(_all, _r)

    # Store in history — a tudásbázis-tartalmú válasz jelölve: a következő körökben csak Claude láthatja; a stratégiai
    # forduló is jelölve (a Kommandant válasza erre stratégiai folytatás — strategiai.elozo_strategiai)
    add_message(chat_id, "assistant", final_content,
                agent_id + (_strat.STRATEGIAI_JEL if strat.aktiv else "")
                + (futar_kliens.CLAUDE_CSAK_JEL if tudasbazis_kor else ""))

    # Push to Telegram
    safe_content = html_escape(final_content[:3800])
    if strat_jegyzet:
        safe_content += f"\n\n<i>{html_escape(strat_jegyzet)}</i>"
    if kapu_notes:
        safe_content += "\n\n" + "\n".join(f"<i>[{html_escape(n)}]</i>" for n in dict.fromkeys(kapu_notes))
    from cimzett import fejlec as _fejlec
    await ctx.telegram_push(f"{_fejlec('feldwebel', kiszolgalo)}{' · <i>stratégiai mód</i>' if strat.aktiv else ''}"
                            f"\n\n{safe_content}")

    # Store in Bridge DB (a kabin előzménye — a stratégiai jegyzettel együtt, ahogy a Telegramon látszott)
    _store_bridge_reply(ctx, text, final_content + (f"\n\n_{strat_jegyzet}_" if strat_jegyzet else ""), agent_id,
                        modell=kiszolgalo, eszkozok=eszkoz_nevek)

    # Trim old history
    trim_history(chat_id, max_entries=30)

    logger.info("Feldwebel responded (%d chars, tool-use)", len(final_content))
    return final_content


# ── DeepSeek API call ──────────────────────────────────────────────


def modell_rovid(modell: str) -> str:
    """'claude-sonnet-5-5' → 'Sonnet 5.5' (a közös megvalósítás: cimzett.modell_rovid)."""
    from cimzett import modell_rovid as _mr
    return _mr(modell)


async def _call_deepseek(ctx, model_id: str, messages: list, use_tools: bool = True, force_tool: bool = False,
                         notes: list | None = None, purpose: str = "feldwebel/telegram",
                         csak_claude: bool = False, tools: list | None = None) -> dict:
    """Call the Bridge LLM gateway (Anthropic / SiliconFlow), optionally with tools.

    2026-11-22: ha a Bridge bekötötte a közös kaput (`ctx.llm_chat` = `server.sf_chat`), azon megy — ott dől el,
    Anthropic vagy SiliconFlow válaszol, és ott az önjavítás. A `notes` a váltások jegyzete a hívónak.

    `csak_claude` (a kör tudásbázis-tartalmat hordoz): közvetlenül az Anthropic-kapu (`llm_kapu.chat`) — SiliconFlow-
    tartalék NINCS, hibánál kimondott hiba. A Kommandant tudásbázisa csak Claude-modell promptjába kerülhet."""
    try:
        payload = {
            "model": model_id,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4000,   # irat, terv, kombinált válasz — 2000-nél elvágta
        }
        # K2.x + V4-Pro default thinking ON on SF — clamp HARD in the agent loop.
        # reasoning_effort=medium proved insufficient for V4-Pro: with
        # tool_choice="required" on round 0 the model reasoned but never committed
        # to content OR a tool call (5 empty rounds). The agent loop is purely
        # tool-driven, so thinking buys nothing here — disable it for both.
        if "Kimi" in model_id or "DeepSeek" in model_id:
            payload["thinking"] = {"type": "disabled"}
        if use_tools:
            # 11-23: az eszközlista ÁLLANDÓ (mag + betöltő + lapozó, a többi halasztva — `_eszkozkeszlet`), és a vezető
            # rendszerszöveg végén 1 órás explicit cache-töréspont: a mag és az eszközök üzenetek között is cache-ből jönnek
            payload["tools"] = tools if tools is not None else await _eszkozkeszlet()
            payload["cache_rendszer"] = "1h"
            # tool_choice="required" is REJECTED by deepseek-reasoner (V4-Pro)
            # on SiliconFlow with code 20015 "does not support this tool_choice".
            # Skip it for DeepSeek; the system prompt + provided tools steer the
            # model to call them anyway. Kimi K2.7 still supports it cleanly.
            if force_tool and "DeepSeek" not in model_id:
                payload["tool_choice"] = "required"

        if csak_claude:
            import llm_kapu
            data, err, kapu_notes = await llm_kapu.chat(payload, purpose=purpose)
            if notes is not None:
                notes.extend(kapu_notes or [])
            if err is not None:
                err_msg = f"{err.get('code')}: {err.get('message', '')}"
                logger.error("Feldwebel Claude-kör hiba: %s (tudásbázis-tartalom, nincs tartalék)", err_msg[:300])
                await ctx.telegram_push(f"<b>FELDWEBEL</b> — API hiba: {html_escape(err_msg[:500])} (a beszélgetésben "
                                        f"tudásbázis-tartalom van, ami csak Claude-nak mehet ki, ezért SiliconFlow-"
                                        f"tartalékra nem váltok)")
                return {}
            return data or {}

        # A Bridge kapuja (`ctx.llm_chat`), vagy bekötés nélkül a közös belépő (`llm_kapu.kapun`) — 2026-11-22-ig itt
        # egy közvetlen SiliconFlow-POST állt, tartalék nélkül. A SiliconFlow mindkét hibaalakját (`{"error": …}` és
        # `{"code": N, "message": …}`) a kapu tipizálja, így a hiba oka nem vész el („Agent loop ended with no content”).
        from feldwebel import llm_hivas
        data, err, kapu_notes = await llm_hivas(ctx, payload, purpose)
        if notes is not None:
            notes.extend(kapu_notes or [])
        if err is not None:
            err_msg = f"{err.get('code')}: {err.get('message', '')}"
            logger.error("Feldwebel LLM-kapu hiba: %s (model=%s, force_tool=%s)", err_msg[:300], model_id, force_tool)
            await ctx.telegram_push(f"<b>FELDWEBEL</b> — API hiba: {html_escape(err_msg[:500])}")
            return {}
        return data or {}

    except Exception as e:
        logger.error("SiliconFlow API call failed: %s", e)
        await ctx.telegram_push(f"<b>FELDWEBEL</b> — API hiba: {html_escape(str(e)[:500])}")
        return {}


# ── Tool execution ─────────────────────────────────────────────────


def _echolot_op_key(ctx) -> str:
    """Feldwebel Echolot operátor-kulcs a PUBLIKUS írásokhoz (komment).

    Feloldási sorrend (agora_duty._operator_key mintája): env ELŐSZÖR, aztán
    Bridge shared_memory. A kulcsot SOHA nem logoljuk és nem adjuk vissza
    tool-eredményben. Üres string = nincs kulcs (a hívó ad emberi hibaüzenetet).
    """
    key = (os.environ.get("AGORA_OP_KEY_FELDWEBEL")
           or os.environ.get("ECHOLOT_OPERATOR_KEY") or "").strip()
    if key:
        return key
    for mem_key in ("agora_op_key_feldwebel", "echolot_operator_key"):
        try:
            conn = ctx.get_db()
            row = conn.execute("SELECT value FROM shared_memory WHERE key = ?",
                               (mem_key,)).fetchone()
            conn.close()
            if row and str(row["value"]).strip():
                return str(row["value"]).strip()
        except Exception as e:
            logger.error("Echolot op-key memory read failed (%s): %s", mem_key, e)
    return ""


_NO_OP_KEY_MSG = ("Nincs Echolot operátor-kulcs a Feldwebelhez. Kell: "
                  "AGORA_OP_KEY_FELDWEBEL env var VAGY 'agora_op_key_feldwebel' / "
                  "'echolot_operator_key' kulcs a Bridge shared_memory-ban.")


# ══════════════════════════════════════════════════════════════════════════
# MINDEN GAZDASÁGI ADAT A STATDATA-N ÁT — EGY ÚT, NEM ÖT
# ══════════════════════════════════════════════════════════════════════════
#
# Kommandant, 2026-08-31: "SZÓLTAM, h minden agentic move kapja meg a
# megfelelő infót."
#
# A Feldwebelnek ÖT saját gazdasági implementációja volt, mind közvetlenül egy
# külső API-ra: policy_rates → dbnomics BIS-tükör, mnb_rates → ECB deviza-XML,
# market_quote → Yahoo, fred_data → FRED REST, econ_calendar → FRED releases.
# Egyik sem kapta meg azt, ami a StatData-ban MEGVAN:
#   * rate-limit kapu + visszatartásos újrapróbálás (a FRED 429/403-at ad
#     terhelés alatt, és a nyers hívás ilyenkor csak elesik),
#   * `unit` / `unit_kind` mező (index vs. ráta vs. szint),
#   * hihetőségi kapu (indexszint sose menjen át rátaként),
#   * friss forráslánc (a BIS-tükör ~14 hónapot késik),
#   * hétvége-javított kiadványnaptár.
#
# ÉLES KÁR ebből (2026-08-31, 00:22): "Alapkamatok (legutolsó ismert: 2025.
# június): MNB 6,5% · ECB 2,0% · Fed 4,375%" — mind a három rossz, miközben a
# StatData ugyanerre 5,5% / 2,25% / 3,75%-ot felelt. Nem adathiány volt: rossz
# ajtón kopogtunk.
#
# A tartalék-ág megmarad (a StatData is lehet elérhetetlen), de KIMONDJA
# magát: néma visszaesés nincs.
_ECON_STATDATA_MAP = {
    "policy_rates":  "get_policy_rates",
    "mnb_rates":     "mnb_rates",
    "market_quote":  "yfinance",
    "fred_data":     "get_fred_data",
    "econ_calendar": "get_economic_calendar",
}


async def _econ_via_statdata(name: str, args: dict):
    """A StatData megfelelő toolja, vagy None ha nem elérhető. Sose dob."""
    tool = _ECON_STATDATA_MAP.get(name)
    if not tool:
        return None
    try:
        import _statdata_client as _sd
        if not getattr(_sd, "STATDATA_URL", ""):
            return None
        return await _sd._call(tool, args)
    except Exception as e:  # noqa: BLE001
        logger.warning("econ tool %s: a StatData nem valaszolt (%s) — tartalek ut", name, e)
        return None


FABER_DEF = {"type": "function", "function": {
    "name": "faber",
    "description": ("Üzenet a FABERNEK, a Kommandant gépén futó műhelynek (Claude Code): kódot ír, hibát javít, teszteli, "
                    "és Telegramon jóváhagyást kér — élesíteni csak a Kommandant [Mehet]-je után tud. "
                    "tipus: kivansag (mit csináljon), allapot (mit csinál most), jovahagy / elvet (egy függő kérelemről)."),
    "parameters": {"type": "object", "properties": {
        "tipus": {"type": "string", "enum": ["kivansag", "allapot", "jovahagy", "elvet"]},
        "szoveg": {"type": "string", "description": "a kívánság vagy a megjegyzés, magyarul, önállóan érthetően"},
        "repo": {"type": "string", "enum": ["bridge", "memoria-rag"], "description": "kívánságnál: melyik műhely"},
        "kerelem": {"type": "integer", "description": "jóváhagyásnál/elvetésnél a kérelem száma, ha ismert"},
    }, "required": ["tipus"]}}}


def _faber_uzen(args: dict, ctx) -> str:
    """A Feldwebel → Faber út: aláírt szándék a Bridge-relayen (ugyanaz, mint a Kommandant szóbeli kérése)."""
    import faber_relay
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    adat = {"szandek": args.get("tipus") or "allapot", "szoveg": str(args.get("szoveg") or ""),
            "repo": args.get("repo"), "kerelem": args.get("kerelem")}
    conn = ctx.get_db()
    try:
        from datetime import datetime, timezone
        mid = faber_relay.tovabbit(conn, token, "szandek", faber_relay.szandek_adat(adat),
                                   datetime.now(timezone.utc).isoformat())
    finally:
        conn.close()
    if not mid:
        return json.dumps({"hiba": "a Faber-relay nem elérhető (nincs Telegram-token a Bridge-en)"}, ensure_ascii=False)
    return json.dumps({"ok": True, "uzenet": "Továbbítva a Fabernek; a gépen felveszi, és Telegramon ő maga válaszol."},
                      ensure_ascii=False)


async def _eszkozkeszlet() -> list:
    """A Feldwebel TELJES eszközlistája, állandó sorrendben: a mag elöl, a betöltő és a lapozó, a többi halasztva
    (`eszkoz_ut.keszlet`). A forrás-sorrend változatlan: TOOLS, a külső appok (Lexonomics, Medical, Studio… szerverenként
    egy eszköz — kulso_mcp.py), a futár, a döntés-szimulátor (dontes_szimulator.py), a Faber és a feladat-ügynökök
    kezelője (feladat_ugynok.py)."""
    from feldwebel import eszkoz_ut
    import kivonat as _kivonat
    osszes = (TOOLS + await _kulso_eszkozok() + _futar_eszkozok() + _szimulator_eszkozok() + [FABER_DEF]
              + _ugynok_eszkozok() + _vita_eszkozok())
    return eszkoz_ut.keszlet(osszes, extra=[_kivonat.LAPOZ_DEF])


def _futar_eszkozok() -> list:
    """A gépen futó futár eszközei (tudasbazis, helyi_fajl, chatgpt, gemini — futar_kliens.py); hibánál üres lista."""
    try:
        import futar_kliens
        return futar_kliens.eszkoz_definiciok()
    except Exception as e:  # noqa: BLE001
        logger.warning("Feldwebel: a futár-eszközök kimaradtak: %s", str(e)[:200])
        return []


def _szimulator_eszkozok() -> list:
    """A döntés-szimulátor eszköze (dontes_szimulator.py) — csak Anthropic-kulccsal; hibánál üres lista."""
    try:
        import dontes_szimulator
        return dontes_szimulator.eszkoz_definiciok()
    except Exception as e:  # noqa: BLE001
        logger.warning("Feldwebel: a döntés-szimulátor eszköze kimaradt: %s", str(e)[:200])
        return []


def _ugynok_eszkozok() -> list:
    """A feladat-ügynökök kezelője (feladat_ugynok.py): indít, listáz, leállít, újraindít, futtat, megszüntet."""
    try:
        import feladat_ugynok
        return [feladat_ugynok.ESZKOZ_DEF]
    except Exception as e:  # noqa: BLE001
        logger.warning("Feldwebel: a feladat-ügynök eszköz kimaradt: %s", str(e)[:200])
        return []


def _vita_eszkozok() -> list:
    """A PYRAMID-VITA (pyramid/vita.py): indít, hozzászól, PARANCS (a Kommandant jóváhagy és utasít), tovább, leállít."""
    try:
        from pyramid import vita as pyramid_vita
        return [pyramid_vita.ESZKOZ_DEF]
    except Exception as e:  # noqa: BLE001
        logger.warning("Feldwebel: a vita-eszköz kimaradt: %s", str(e)[:200])
        return []


async def _kulso_eszkozok() -> list:
    """A bekötött külső MCP-szerverek eszközei; hibánál üres lista (a Feldwebel enélkül is válaszol)."""
    try:
        import kulso_mcp
        return await kulso_mcp.eszkoz_definiciok()
    except Exception as e:  # noqa: BLE001
        logger.warning("Feldwebel: a külső MCP-eszközök kimaradtak: %s", str(e)[:200])
        return []


async def _execute_tool(name: str, args: dict, ctx, kor: dict | None = None, *, modell: str = "",
                        vedett: bool = False) -> str:
    """Execute a tool and return the result as string.

    `kor`: a beszélgetési kör adatai (chat_id, a Kommandant üzenete) — a naptár-jóváhagyás ehhez köti a javaslatot.
    `modell`: az eszközt hívó kör valódi modellje (a `tudasbazis` csak `claude-*`-nak fut). `vedett`: a kör
    tudásbázis-tartalmat hordoz — SiliconFlow-ügynökhöz, általuk olvasott tárba vagy nyilvánosan menő eszköz nem fut."""
    # IMPORTANT: import httpx here — több eszköz-ág közvetlenül használja (a képelemzés 11-22 óta a kapun megy).
    import httpx
    import futar_kliens
    import kulso_mcp

    if vedett and futar_kliens.sf_fele(name, args):
        return json.dumps({"error": futar_kliens.SF_FELE_HIBA}, ensure_ascii=False)
    if name == "faber":
        return _faber_uzen(args or {}, ctx)
    if name == "ugynok":
        # FELADAT-ÜGYNÖK (2026-11-23): „indíts egy ügynököt, ami…”, „állítsd le a kamatfigyelőt”. Az ügynök Claude-on
        # fut, a naplója SiliconFlow-ügynökhöz nem kerül (feladat_ugynok.py), ezért tudásbázis-tartalmú körben is mehet.
        import feladat_ugynok
        return await feladat_ugynok.feldwebel_hiv(args or {}, ctx, vedett=vedett, kor=kor)
    if name == "vita":
        # PYRAMID-VITA (2026-11-23): „vitassák meg…”, „a 18-as vitához: …”, „jó, csináljátok ezt…” (parancs). A
        # résztvevők SiliconFlow-modellek: tudásbázis-tartalmú körből a szöveg nem mehet (`futar_kliens.SF_FELE_IRASKOR`).
        from pyramid import vita as pyramid_vita
        return await pyramid_vita.feldwebel_hiv(args or {}, ctx)
    if futar_kliens.sajat_e(name):
        return await futar_kliens.hiv(name, args, get_db=ctx.get_db, modell=modell)

    if kulso_mcp.sajat_e(name):
        return await kulso_mcp.hiv(name, str(args.get("eszkoz") or ""), args.get("parameterek"))

    import dontes_szimulator
    if dontes_szimulator.sajat_e(name):
        # a futás a háttérben indul (perceket vesz igénybe): ez a válasz a NYUGTA, az eredmény külön üzenetben jön
        return await dontes_szimulator.feldwebel_hiv(args, ctx)

    if name == "gmail_search":
        query = args.get("query", "")
        if not query:
            return json.dumps({"error": "Nincs keresési query"})
        if not ctx.capture_state.get("_capture_inbox_func"):
            return json.dumps({"error": "Gmail nem elérhető"})
        try:
            result = await ctx.capture_state["_capture_inbox_func"](
                limit=10, query=query, caller="feldwebel"
            )
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "gmail_poll":
        if not ctx.capture_gmail_poll:
            return json.dumps({"error": "Gmail poll nem elérhető"})
        try:
            result = await ctx.capture_gmail_poll(caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "calendar_poll":
        if not ctx.capture_calendar_poll:
            return json.dumps({"error": "Calendar poll nem elérhető"})
        try:
            result = await ctx.capture_calendar_poll(caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "create_task":
        title = args.get("title", "")
        if not title:
            return json.dumps({"error": "Nincs feladat megadva"})
        try:
            conn = ctx.get_db()
            conn.execute(
                "INSERT INTO tasks (title, assigned_to, assigned_by, status, priority, created_at) "
                "VALUES (?, 'cli-claus', 'feldwebel', 'pending', 'normal', ?)",
                (title, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
            conn.close()
            return json.dumps({"success": True, "title": title})
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "list_tasks":
        try:
            conn = ctx.get_db()
            rows = conn.execute(
                "SELECT id, title, status, priority, assigned_to FROM tasks "
                "WHERE status IN ('pending', 'in_progress') "
                "ORDER BY created_at DESC LIMIT 15"
            ).fetchall()
            conn.close()
            tasks = [{"id": r["id"], "title": r["title"], "status": r["status"],
                      "priority": r["priority"], "assigned_to": r["assigned_to"]} for r in rows]
            return json.dumps({"tasks": tasks}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "ai_query":
        model = args.get("model", "deepseek")
        prompt = args.get("prompt", "")
        news_context = args.get("news_context", "") or ""
        if not prompt:
            return json.dumps({"error": "Nincs prompt megadva"})

        # Route through ai_task for web search capability
        ai_task_func = ctx.capture_state.get("_ai_task_func")
        if ai_task_func:
            try:
                now = datetime.now(timezone.utc)
                date_info = f"\n\n[Mai dátum: {now.strftime('%Y.%m.%d')} ({WEEKDAYS_HU[now.weekday()]})]"
                agent_tasks = json.dumps({model: {"prompt": prompt + date_info, "max_tokens": 3000}})
                result_json = await ai_task_func(
                    title=f"Feldwebel → {model}: {prompt[:60]}",
                    description=prompt + date_info,
                    assigned_by="feldwebel",
                    agent_tasks=agent_tasks,
                    news_context=news_context,
                )
                result = json.loads(result_json)
                task_id = result.get("task_id")

                if task_id:
                    # Poll for result (ai_task runs in background thread)
                    import asyncio
                    for _ in range(60):  # max 120s wait
                        await asyncio.sleep(2)
                        conn = ctx.get_db()
                        row = conn.execute(
                            "SELECT content FROM ai_task_results WHERE task_id = ? LIMIT 1",
                            (task_id,)
                        ).fetchone()
                        status = conn.execute(
                            "SELECT status FROM ai_tasks WHERE id = ?", (task_id,)
                        ).fetchone()
                        conn.close()
                        if row:
                            return json.dumps({
                                "model": model, "response": row["content"],
                                "task_id": task_id
                            }, ensure_ascii=False)
                        if status and status["status"] == "failed":
                            return json.dumps({"error": f"AI task #{task_id} failed"})
                    return json.dumps({"error": f"AI task #{task_id} timeout (120s)"})
                return result_json
            except Exception as e:
                logger.error("ai_query via ai_task failed: %s", e)

        # Fallback: direct ai_query (no web search)
        ai_query_func = ctx.capture_state.get("_ai_query_func")
        if ai_query_func:
            try:
                return await ai_query_func(model=model, prompt=prompt, caller="feldwebel",
                                           news_context=news_context)
            except Exception as e:
                return json.dumps({"error": str(e)})

        return json.dumps({"error": "AI query nem elérhető"})

    if name == "ai_task":
        title = args.get("title", "")
        description = args.get("description", "")
        news_context = args.get("news_context", "") or ""
        if not title or not description:
            return json.dumps({"error": "Cím és leírás szükséges"})
        if not ctx.capture_state.get("_ai_task_func"):
            return json.dumps({"error": "AI task nem elérhető"})
        try:
            now = datetime.now(timezone.utc)
            date_info = f"\n\n[Mai dátum: {now.strftime('%Y.%m.%d')} ({WEEKDAYS_HU[now.weekday()]}). Az adatoknak FRISSNEK kell lenniük!]"
            result_json = await ctx.capture_state["_ai_task_func"](
                title=title, description=description + date_info, assigned_by="feldwebel",
                news_context=news_context,
            )
            # Return immediately — user can ask for results later with read_task_results
            return result_json
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "read_task_results":
        task_id = args.get("task_id", 0)
        if not task_id:
            return json.dumps({"error": "task_id szükséges"})
        try:
            conn = ctx.get_db()
            task = conn.execute(
                "SELECT title, status FROM ai_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not task:
                conn.close()
                return json.dumps({"error": f"Task #{task_id} nem található"})
            if task["status"] in ("pending", "running"):
                conn.close()
                return json.dumps({"task_id": task_id, "status": task["status"],
                                   "message": f"Task #{task_id} még fut, az agentek dolgoznak."})
            rows = conn.execute(
                "SELECT agent, content FROM ai_task_results WHERE task_id = ? ORDER BY timestamp",
                (task_id,)
            ).fetchall()
            conn.close()
            if rows:
                parts = []
                for r in rows:
                    parts.append(f"**{r['agent'].upper()}**:\n{r['content'][:2000]}")
                return json.dumps({
                    "task_id": task_id, "title": task["title"], "status": task["status"],
                    "agent_results": "\n\n---\n\n".join(parts)
                }, ensure_ascii=False)
            return json.dumps({"task_id": task_id, "status": task["status"], "message": "Nincs eredmény"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "send_email":
        to = args.get("to", "")
        subject = args.get("subject", "")
        body = args.get("body", "")
        if not to or not subject or not body:
            return json.dumps({"error": "to, subject, body mind szükséges"})
        send_func = ctx.capture_state.get("_send_email_func")
        if not send_func:
            return json.dumps({"error": "Email küldés nem elérhető"})
        try:
            result = await send_func(to=to, subject=subject, body=body, caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "read_gmail_attachment":
        message_id = args.get("message_id", "")
        attachment_index = args.get("attachment_index", 0)
        if not message_id:
            return json.dumps({"error": "message_id szükséges (gmail_search-ből)"})
        read_att_func = ctx.capture_state.get("_read_gmail_attachment_func")
        if not read_att_func:
            return json.dumps({"error": "Csatolmány olvasás nem elérhető"})
        try:
            result = await read_att_func(message_id=message_id, attachment_index=attachment_index, caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "calendar_create":
        summary = args.get("summary", "")
        start = args.get("start_time", "") or args.get("start", "")
        end = args.get("end_time", "") or args.get("end", "")
        description = args.get("description", "")
        if not summary or not start:
            return json.dumps({"error": "summary és start_time szükséges"})
        adat = {"summary": summary, "start": start, "end": end, "description": description,
                "reminder_minutes": _jovahagyas.emlekezteto_percek(args.get("reminder_minutes"))}
        # KÓDKAPU (2026-11-22): csak a Kommandant kifejezett igenje után ír. Ha ez a kör maga az igen (a modell előbb
        # szövegben kérdezett a naptárról), most ír; különben a hívás javaslat, és a Kommandant „mehet”-je dönt.
        chat_id = (kor or {}).get("chat_id")
        if not (kor and _jovahagyas.azonnal_irhat(ctx.get_db, chat_id, kor.get("text") or "")):
            try:
                draft_id = _jovahagyas.javasol(ctx.get_db, chat_id, adat)
            except Exception as e:  # noqa: BLE001
                return json.dumps({"error": f"a javaslat nem menthető: {e}"}, ensure_ascii=False)
            return json.dumps({
                "status": "jovahagyasra_var", "draft_id": draft_id, "beirva": False,
                "javaslat": _jovahagyas.javaslat_szoveg(adat),
                "teendo": ("MÉG NINCS a naptárban. Mondd el a Kommandantnak pontosan ezt a javaslatot, és kérdezd "
                           "meg: „Beírjam a naptárba? (igen / mehet)”. Egy szava elég; addig ne írd, hogy beírtad."),
            }, ensure_ascii=False)
        res = await _jovahagyas.beir(ctx.capture_state.get("_create_calendar_func"), adat)
        return json.dumps(res, ensure_ascii=False)

    if name == "tweet_tervezet":
        # KÓDKAPU (2026-11-23): csak TERVEZET az action_drafts-ban — posztolni a Feldwebel nem tud; a Kommandant a kabin
        # gombjával vagy a kifejezett mondattal hagyja jóvá (twitter_poszt.py).
        import twitter_poszt
        chat_id = str((kor or {}).get("chat_id") or "")
        try:
            out = twitter_poszt.tervezet(ctx.get_db, str(args.get("szoveg") or args.get("text") or ""),
                                         str(args.get("link") or ""), forras="feldwebel",
                                         source_message_id=f"feldwebel:{chat_id}", chat_id=chat_id)
        except Exception as e:  # noqa: BLE001
            out = {"error": f"a tervezet nem menthető: {type(e).__name__}: {str(e)[:200]}"}
        return json.dumps(out, ensure_ascii=False)

    if name == "hog_keszit":
        # OPERATION HOG (2026-11-23, hog.py): csak a HÁTTÉRFUTÁS indul (Sonnet ír) — publikálni és posztolni a Feldwebel
        # nem tud; a kész tervezetet a Kommandant a kabin gombjával vagy a kifejezett mondattal hagyja jóvá.
        import hog
        chat_id = str((kor or {}).get("chat_id") or "")
        try:
            out = await hog.inditas(ctx, args, chat_id)
        except Exception as e:  # noqa: BLE001
            out = {"error": f"a HOG nem indítható: {type(e).__name__}: {hog._tisztit(e)[:200]}"}
        return json.dumps(out, ensure_ascii=False)

    if name == "web_fetch":
        # 11-23: a fetch-LÁNC (lekeres.py), nem egyetlen GET: YouTube-linkre a leirat, PDF-re méretplafon + az első
        # oldalak szövege, SSL-hibára / üres héjra / falra a következő fok (Lightpanda → brave-mcp → Echolot Engine).
        # A HTML 6000 karakterig megy (a Feldwebel ablaka); a nagy PDF-et és leiratot a hurok kivonatolója sűríti.
        import lekeres
        return json.dumps(await lekeres.oldal(str(args.get("url") or "")), ensure_ascii=False)

    if name == "web_search":
        # Bridge-szintű _web_search-t hívjuk (brave-mcp primary + DDG fallback,
        # 10-min cache, bot-resistant). Lazy import, hogy a Feldwebel-modul
        # függetlenül indítható maradjon.
        q = (args.get("query") or "").strip()
        if not q:
            return json.dumps({"error": "query required"})
        try:
            from server import _web_search
            # S-009: a Kommandant kifejezett motor-kérése ("ezt searxng-vel")
            # eddig itt veszett el — nem volt hova mennie.
            return await _web_search(q, engine=(args.get("engine") or "auto"))
        except Exception as e:
            return json.dumps({"error": f"web_search exception: {type(e).__name__}: {e}"})

    if name == "web_scrape":
        # JS-rendered Puppeteer-scrape brave-mcp-server-en át (Eurostat newsroom,
        # MNB-honlap, ECB press release, stb.). Akkor érdemes a web_fetch helyett,
        # ha az URL SPA / JS-rendered.
        url = (args.get("url") or "").strip()
        if not url or not url.startswith("http"):
            return json.dumps({"error": "Érvényes URL szükséges"})
        import lekeres
        if lekeres.youtube_id(url):          # 11-23: a videóoldal 205 karakter — a leirat kell
            return json.dumps(await lekeres.oldal(url), ensure_ascii=False)
        try:
            wait_time = max(0, min(15000, int(args.get("wait_time", 3000))))
        except (TypeError, ValueError):
            wait_time = 3000
        try:
            from server import _brave_mcp_scrape, BRAVE_MCP_ENABLED
            if not BRAVE_MCP_ENABLED:
                return json.dumps({"error": "web_scrape disabled (BRAVE_MCP_URL env missing)"})
            result = await _brave_mcp_scrape(url, wait_time=wait_time)
            if not result:
                return json.dumps({"error": "brave-mcp-server scrape failed"})
            md = (result.get("markdown") or result.get("text") or "").strip()
            if len(md) > 6000:
                md = md[:6000] + f"\n\n[...truncated, total {len(md)} chars]"
            return json.dumps({
                "url": result.get("url", url),
                "title": (result.get("title") or "").strip(),
                "markdown": md,
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"web_scrape exception: {type(e).__name__}: {e}"})

    if name == "read_memory":
        key = args.get("key", "")
        category = args.get("category", "")
        read_mem_func = ctx.capture_state.get("_read_memory_func")
        if not read_mem_func:
            return json.dumps({"error": "Memory olvasás nem elérhető"})
        try:
            result = await read_mem_func(key=key, category=category, caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "search_memory":
        query = args.get("query", "")
        if not query:
            return json.dumps({"error": "Keresési kifejezés szükséges"})
        search_mem_func = ctx.capture_state.get("_search_memory_func")
        if not search_mem_func:
            return json.dumps({"error": "Memory keresés nem elérhető"})
        try:
            result = await search_mem_func(query=query, caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "send_message":
        recipient = args.get("recipient", "")
        subject = args.get("subject", "")
        message = args.get("message", "")
        if not recipient or not message:
            return json.dumps({"error": "recipient és message szükséges"})
        send_msg_func = ctx.capture_state.get("_send_message_func")
        if not send_msg_func:
            return json.dumps({"error": "Bridge üzenet küldés nem elérhető"})
        try:
            # A server.send_message-ben a jogosultság-kapu a `sender`-t nézi, `caller` paramétere nincs: a régi
            # `caller="feldwebel"` minden hívást TypeError-ral buktatott („unexpected keyword argument 'caller'”,
            # Faber #6). Az őr: tests/test_feldwebel_send_message.py.
            result = await send_msg_func(sender="feldwebel", recipient=recipient, subject=subject or "Feldwebel üzenet", message=message)
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "search_discussions":
        query = args.get("query", "")
        if not query:
            return json.dumps({"error": "Keresési kifejezés szükséges"})
        search_disc_func = ctx.capture_state.get("_search_discussions_func")
        if not search_disc_func:
            return json.dumps({"error": "Diskusszió keresés nem elérhető"})
        try:
            result = await search_disc_func(query=query, caller="feldwebel")
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    # ── Echolot / Hírmagnet / Agora tools ──
    # Ugyanazon a _echolot_client kliensen mennek, mint a Bridge MCP-oldali
    # echolot_query / read_agora_comments / read_story_comments wrapperek és az
    # agora_duty plugin (lásd shared_memory: echolot_mcp_endpoint_referencia).
    if name in ("echolot_query", "agora", "story_comments"):
        try:
            import _echolot_client as ec
        except ImportError:
            return json.dumps({"error": "Echolot kliens (_echolot_client) nem elérhető"})
        if not ec.ECHOLOT_URL:
            return json.dumps({"error": "Echolot integráció nincs bekapcsolva (ECHOLOT_URL env hiányzik)"})

        try:
            if name == "echolot_query":
                if args.get("top_stories"):
                    links = await ec.get_top_story_links(
                        limit=max(1, min(30, int(args.get("limit", 15) or 15))))
                    return json.dumps({"top_stories": links}, ensure_ascii=False)[:8000]
                query = (args.get("query") or "").strip()
                sphere_list = [s.strip() for s in (args.get("spheres") or "").split(",") if s.strip()]
                days = max(1, min(21, int(args.get("days", 3) or 3)))
                limit = max(1, min(50, int(args.get("limit", 15) or 15)))
                language = (args.get("language") or "").strip()
                if query:
                    data = await ec.search_news(query=query, days=days,
                                                sphere=sphere_list[0] if sphere_list else "",
                                                language=language, limit=limit)
                    label = f'search:"{query}"'
                else:
                    data = await ec.fetch_news(spheres=sphere_list, days=days,
                                               limit=limit, language=language)
                    label = "spheres:" + ",".join(sphere_list) if sphere_list else f"latest-{days}d"
                articles = list(data.get("articles") or [])
                block = ec.format_news_block(articles, label=label, group_by_sphere=True)
                # Telegram-chat asszisztens görgetett historyval — ugyanaz a sapka,
                # mint a web_fetch/web_scrape toolokon.
                return block[:6000]

            if name == "agora":
                action = (args.get("action") or "").strip()
                post_id = (args.get("post_id") or "").strip()
                body = (args.get("body") or "").strip()
                parent_id = (args.get("parent_id") or "").strip()
                limit = max(1, min(50, int(args.get("limit", 20) or 20)))
                if action == "feed":
                    res = await ec.agora_action("feed", limit=limit)
                    return json.dumps(res, ensure_ascii=False)[:8000]
                if action == "read":
                    if not post_id:
                        return json.dumps({"error": "post_id szükséges (agora feed adja)"})
                    res = await ec.agora_action("read", post_id=post_id)
                    return json.dumps(res, ensure_ascii=False)[:8000]
                if action == "comments":
                    if not post_id:
                        return json.dumps({"error": "post_id szükséges"})
                    res = await ec.get_agora_comments(post_id, limit=limit)
                    return json.dumps(res, ensure_ascii=False)[:8000]
                if action == "comment":
                    if not post_id or not body:
                        return json.dumps({"error": "post_id és body szükséges a kommenthez"})
                    op_key = _echolot_op_key(ctx)
                    if not op_key:
                        return json.dumps({"error": _NO_OP_KEY_MSG})
                    # Az Agora-vita a sztori-kommentfal infráján fut: story_id='pub-<post_id>'
                    sid = post_id if post_id.startswith("pub-") else f"pub-{post_id}"
                    res = await ec.post_comment(story_id=sid, body=body[:2000],
                                                operator_key=op_key, parent_id=parent_id,
                                                agent_label="Feldwebel")
                    return json.dumps(res, ensure_ascii=False)[:4000]
                return json.dumps({"error": f"Ismeretlen agora action: {action}"})

            if name == "story_comments":
                story_id = (args.get("story_id") or "").strip()
                if not story_id:
                    return json.dumps({"error": "story_id szükséges"})
                body = (args.get("body") or "").strip()
                parent_id = (args.get("parent_id") or "").strip()
                limit = max(1, min(100, int(args.get("limit", 30) or 30)))
                if not body:
                    res = await ec.get_story_comments(story_id, limit=limit)
                    return json.dumps(res, ensure_ascii=False)[:8000]
                op_key = _echolot_op_key(ctx)
                if not op_key:
                    return json.dumps({"error": _NO_OP_KEY_MSG})
                res = await ec.post_comment(story_id=story_id, body=body[:2000],
                                            operator_key=op_key, parent_id=parent_id,
                                            agent_label="Feldwebel")
                return json.dumps(res, ensure_ascii=False)[:4000]
        except Exception as e:
            logger.error("Feldwebel echolot tool %s failed: %s: %s", name, type(e).__name__, e)
            return json.dumps({"error": f"Echolot hiba ({name}): {type(e).__name__}: {e}"})

    # ── Recipe tools (Operation Zahnrad) ──
    if name == "list_recipes":
        try:
            conn = ctx.get_db()
            rows = conn.execute(
                "SELECT name, description, required_tools FROM pyramid_recipes WHERE enabled = 1 ORDER BY name"
            ).fetchall()
            conn.close()
            recipes = [{"name": r["name"], "description": r["description"],
                        "tools": json.loads(r["required_tools"]) if r["required_tools"] else []} for r in rows]
            return json.dumps({"recipes": recipes}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "execute_recipe":
        recipe_name = args.get("name", "")
        model = args.get("model", "deepseek")
        if not recipe_name:
            return json.dumps({"error": "Recipe neve szükséges"})
        import feladat_ugynok
        if feladat_ugynok.execute_elutasitas(recipe_name):
            return json.dumps({"error": feladat_ugynok.execute_elutasitas(recipe_name)}, ensure_ascii=False)
        try:
            conn = ctx.get_db()
            row = conn.execute(
                "SELECT prompt_template, required_tools, enabled FROM pyramid_recipes WHERE name = ?",
                (recipe_name,)
            ).fetchone()
            conn.close()
            if not row:
                return json.dumps({"error": f"Recipe '{recipe_name}' nem található"})
            if not row["enabled"]:
                return json.dumps({"error": f"Recipe '{recipe_name}' le van tiltva"})
            # Execute via ai_query (dispatch mode for web search support)
            ai_task_func = ctx.capture_state.get("_ai_task_func")
            if not ai_task_func:
                return json.dumps({"error": "AI task nem elérhető"})
            prompt = row["prompt_template"]
            now = datetime.now(timezone.utc)
            date_info = f"\n\n[Mai dátum: {now.strftime('%Y.%m.%d')} ({WEEKDAYS_HU[now.weekday()]})]"
            agent_tasks = json.dumps({model: {"prompt": prompt + date_info, "max_tokens": 8000}})
            result_json = await ai_task_func(
                title=f"Recipe: {recipe_name}",
                description=prompt + date_info,
                assigned_by="feldwebel",
                agent_tasks=agent_tasks,
            )
            result = json.loads(result_json)
            task_id = result.get("task_id")
            if task_id:
                import asyncio
                for _ in range(60):
                    await asyncio.sleep(2)
                    conn = ctx.get_db()
                    row2 = conn.execute("SELECT content FROM ai_task_results WHERE task_id = ? LIMIT 1", (task_id,)).fetchone()
                    status = conn.execute("SELECT status FROM ai_tasks WHERE id = ?", (task_id,)).fetchone()
                    conn.close()
                    if row2:
                        return json.dumps({"recipe": recipe_name, "model": model, "result": row2["content"], "task_id": task_id}, ensure_ascii=False)
                    if status and status["status"] == "failed":
                        return json.dumps({"error": f"Recipe task #{task_id} failed"})
                return json.dumps({"error": f"Recipe task #{task_id} timeout"})
            return result_json
        except Exception as e:
            return json.dumps({"error": str(e)})

    if name == "create_recipe":
        rname = args.get("name", "")
        desc = args.get("description", "")
        prompt_tpl = args.get("prompt_template", "")
        if not rname or not desc or not prompt_tpl:
            return json.dumps({"error": "name, description, prompt_template mind szükséges"})
        import feladat_ugynok
        if feladat_ugynok.fenntartott_nev(rname):
            return json.dumps({"error": feladat_ugynok.fenntartott_nev(rname)}, ensure_ascii=False)
        try:
            conn = ctx.get_db()
            ts = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, created_by, created_at, updated_at) "
                "VALUES (?, ?, '[]', ?, 'feldwebel', ?, ?)",
                (rname, desc, prompt_tpl, ts, ts),
            )
            conn.commit()
            conn.close()
            return json.dumps({"status": "created", "name": rname, "message": f"Recipe '{rname}' létrehozva!"}, ensure_ascii=False)
        except Exception as e:
            if "UNIQUE constraint" in str(e):
                return json.dumps({"error": f"Recipe '{rname}' már létezik."})
            return json.dumps({"error": str(e)})

    # ── Cron scheduling ──
    if name == "schedule_recipe":
        recipe_name = args.get("name", "")
        cron_schedule = args.get("cron_schedule", "")
        model = args.get("model", "glm5")
        if not recipe_name or not cron_schedule:
            return json.dumps({"error": "name és cron_schedule szükséges"})
        # EGY MOTOR MINDEN FELULETNEK (2026-08-30). Korabban ez az ag
        # KOZVETLENUL irt a DB-be, egyetlen `len(parts) != 5` ellenorzessel, es
        # a valasz sosem mondta ki, ha egy korabbi menetrendet felulirt. A
        # Kommandant Telegramon utemez — vagyis eppen az orizetlen agat
        # hasznalta, es 2026-04-10-en igy veszett el a reggeli hirbrief.
        # Mostantol ugyanaz a `plugins.recipes.apply_schedule` fut itt is, mint
        # az MCP-oldali `update_recipe`-ban: kozos validacio, kozos csere-jelzes.
        try:
            from plugins.recipes import apply_schedule
            conn = ctx.get_db()
            try:
                out = apply_schedule(conn, recipe_name, cron_schedule,
                                     model=model, delivery="both")
                conn.commit()
            finally:
                conn.close()
            if out.get("status") == "scheduled" and "replaced_schedule" not in out:
                out["message"] = (f"'{recipe_name}' ütemezve: "
                                  f"{out['cron_schedule']} ({model}). "
                                  f"Eredmény Telegramra jön.")
            return json.dumps(out, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    # ── Send uploaded file via email ──
    if name == "send_file_email":
        fid = args.get("file_id", 0)
        to = args.get("to", "")
        subject = args.get("subject", "Claus — csatolt fajl")
        if not fid or not to:
            return json.dumps({"error": "file_id és to szükséges"})
        send_email_func = ctx.capture_state.get("_send_email_func")
        if not send_email_func:
            return json.dumps({"error": "Email küldés nem elérhető"})
        try:
            result = await send_email_func(
                to=to, subject=subject,
                body=f"Csatolva: fájl #{fid}\n\nKüldte: Claus Bridge",
                file_id=fid, caller="feldwebel",
            )
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    # ── Analyze uploaded image ──
    if name == "analyze_upload":
        fid = args.get("file_id", 0)
        prompt = args.get("prompt", "Mit latsz a kepen? Ird le reszletesen, magyarul.")
        if not fid:
            return json.dumps({"error": "file_id szükséges"})
        try:
            conn = ctx.get_db()
            row = conn.execute("SELECT filename, mime_type, content_base64 FROM uploads WHERE id = ?", (fid,)).fetchone()
            conn.close()
            if not row:
                return json.dumps({"error": f"Upload #{fid} nem található"})
            if not row["content_base64"]:
                return json.dumps({"error": f"Upload #{fid} nem tartalmaz képet"})
            if not row["mime_type"].startswith("image/"):
                return json.dumps({"error": f"Upload #{fid} nem kép ({row['mime_type']})"})
            # A KÖZÖS KAPUN (2026-11-22): SiliconFlow-kiesésnél az Anthropic néz rá a képre, kimondva (`modell`).
            from feldwebel import llm_hivas
            sf_models = ctx.siliconflow_models
            model_id = sf_models.get("kimi", "moonshotai/Kimi-K3.1-Pro")
            data_url = f"data:{row['mime_type']};base64,{row['content_base64']}"
            vision_payload = {
                "model": model_id,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ]}],
                "temperature": 0.5,
                "max_tokens": 2000,
            }
            if "Kimi" in model_id:
                vision_payload["thinking"] = {"type": "disabled"}
            data, err, kapu_notes = await llm_hivas(ctx, vision_payload, "kepleiras/feltoltes", timeout=60)
            if err is not None:
                return json.dumps({"error": f"képelemzés: {err.get('code')}: {str(err.get('message', ''))[:300]}",
                                   **({"kapu_jegyzet": kapu_notes} if kapu_notes else {})}, ensure_ascii=False)
            analysis = ((data or {}).get("choices") or [{}])[0].get("message", {}).get("content") or "(ures valasz)"
            return json.dumps({"status": "analyzed", "file_id": fid, "analysis": analysis,
                               "modell": (data or {}).get("model") or model_id,
                               **({"kapu_jegyzet": list(dict.fromkeys(kapu_notes))} if kapu_notes else {})},
                              ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    # ── File export + Telegram send ──
    if name == "export_task":
        task_id = args.get("task_id", 0)
        fmt = args.get("format", "docx")
        if not task_id:
            return json.dumps({"error": "task_id szükséges"})
        if fmt not in ("docx", "xlsx", "pptx"):
            return json.dumps({"error": "format: docx / xlsx / pptx"})
        try:
            conn = ctx.get_db()
            task = conn.execute("SELECT * FROM ai_tasks WHERE id = ?", (task_id,)).fetchone()
            if not task:
                conn.close()
                return json.dumps({"error": f"Task #{task_id} nem található"})
            results = conn.execute(
                "SELECT agent, role, content, timestamp FROM ai_task_results WHERE task_id = ? ORDER BY id",
                (task_id,)
            ).fetchall()
            conn.close()
            if not results:
                return json.dumps({"error": f"Task #{task_id} nincs eredménye"})

            # Generate file
            if fmt == "xlsx":
                gen_func = ctx.capture_state.get("_generate_xlsx")
                if not gen_func:
                    return json.dumps({"error": "xlsx generálás nem elérhető"})
                buf = gen_func(task, results)
                mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif fmt == "pptx":
                gen_func = ctx.capture_state.get("_generate_pptx")
                if not gen_func:
                    return json.dumps({"error": "pptx generálás nem elérhető"})
                buf = gen_func(task, results)
                mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            else:  # docx
                from docx import Document as DocxDocument
                from io import BytesIO
                doc = DocxDocument()
                doc.add_heading(task["title"], level=1)
                doc.add_paragraph(f'Kiadta: {task["assigned_by"]} | Dátum: {task["created_at"][:10]}')
                agent_names = {"kimi": "Kimi K3.1 Pro", "deepseek": "DeepSeek V4-Pro", "glm5": "GLM-5.3", "szintézis": "Szintézis"}
                for r in results:
                    doc.add_heading(agent_names.get(r["agent"], r["agent"]), level=2)
                    for line in (r["content"] or "").split("\n"):
                        if line.strip():
                            doc.add_paragraph(line.strip())
                buf = BytesIO()
                doc.save(buf)
                buf.seek(0)
                mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

            filename = f"claus_task_{task_id}.{fmt}"
            file_bytes = buf.getvalue()
            actions = []

            # Send to Telegram
            send_file = ctx.capture_state.get("_telegram_send_file")
            if send_file:
                caption = f"📎 <b>{task['title']}</b>\nTask #{task_id} | {fmt.upper()}"
                sent = await send_file(file_bytes, filename, mime, caption)
                if sent:
                    actions.append("Telegram")

            # Send as email attachment if requested
            email_to = args.get("email_to", "")
            if email_to and "@" in email_to:
                import base64 as b64mod
                send_email_func = ctx.capture_state.get("_send_email_func")
                if send_email_func:
                    b64_content = b64mod.b64encode(file_bytes).decode("ascii")
                    await send_email_func(
                        to=email_to,
                        subject=f"Claus AI Task #{task_id}: {task['title']}",
                        body=f"Csatolva: {filename}\n\nKészítette: Claus Multi-Agent Rendszer",
                        attachment_base64=b64_content,
                        attachment_filename=filename,
                        attachment_mime=mime,
                        caller="feldwebel",
                    )
                    actions.append(f"email ({email_to})")

            if actions:
                return json.dumps({"status": "sent", "filename": filename, "size": len(file_bytes),
                                   "message": f"{fmt.upper()} elküldve: {', '.join(actions)}"}, ensure_ascii=False)
            return json.dumps({"error": "Sem Telegram, sem email küldés nem sikerült"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    # ── Közgazdasági adat-toolok (Makronóm) ──────────────────────────
    if name in ("mnb_rates", "market_quote", "fred_data", "policy_rates", "econ_calendar"):
        logger.info("ECON TOOL CALLED: %s args=%s", name, args)
        # ELOSZOR A STATDATA. Lasd `_econ_via_statdata` — egy ut, nem ot.
        # Az argumentum-alak toolonkent elter, ezert itt kepezzuk le.
        _sd_args = dict(args)
        if name == "market_quote":
            _sd_args = {"symbol": args.get("symbol", ""), "action": "quote"}
        elif name == "econ_calendar":
            _sd_args = {"days_ahead": int(args.get("days_ahead", 7) or 7),
                        "region": (args.get("region") or "US").upper()}
        elif name == "fred_data":
            _sd_args = {k: v for k, v in args.items() if k in
                        ("series_id", "limit", "sort_order", "frequency", "units")}
        _sd_res = await _econ_via_statdata(name, _sd_args)
        if _sd_res is not None:
            if isinstance(_sd_res, str):
                return _sd_res
            return json.dumps(_sd_res, ensure_ascii=False, default=str)
        logger.warning("ECON TOOL %s: StatData nem elerheto — TARTALEK ut, az adat "
                       "elavult lehet", name)

    if name == "mnb_rates":
        currencies_filter = [c.strip().upper() for c in args.get("currencies", "").split(",") if c.strip()]
        try:
            # ECB daily reference rates (EUR-based, includes HUF + 30 currencies)
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
                    headers={"User-Agent": "ClausBridge/1.0"},
                )
            root = ET.fromstring(resp.text)
            ns = {"ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
            date_str = ""
            for tc in root.findall(".//ecb:Cube[@time]", ns):
                date_str = tc.attrib["time"]
            rates = []
            eur_huf = None
            for cube in root.findall(".//ecb:Cube[@currency]", ns):
                curr = cube.attrib["currency"]
                rate_val = float(cube.attrib["rate"])
                if curr == "HUF":
                    eur_huf = rate_val
                if currencies_filter and curr not in currencies_filter:
                    continue
                rates.append({"currency": curr, "rate": rate_val})
            # If no filter, just return key currencies
            if not currencies_filter:
                key_currencies = {"HUF", "USD", "CHF", "GBP", "CZK", "PLN", "RON", "JPY", "SEK", "NOK", "DKK", "CNY", "TRY"}
                rates = [r for r in rates if r["currency"] in key_currencies]
            # Add cross rates (USD/HUF, CHF/HUF etc.) if HUF is in data
            cross_rates = []
            if eur_huf:
                for r in rates:
                    if r["currency"] != "HUF":
                        cross = round(eur_huf / r["rate"], 2)
                        cross_rates.append({"pair": f"{r['currency']}/HUF", "rate": cross})
            return json.dumps({
                "source": "ECB daily reference rates",
                "date": date_str,
                "base": "EUR",
                "rates": rates,
                "huf_cross_rates": cross_rates,
            }, ensure_ascii=False)
        except Exception as e:
            logger.error("mnb_rates tool error: %s: %s", type(e).__name__, e)
            return json.dumps({"error": f"ECB API hiba: {type(e).__name__}: {e}"})

    if name == "market_quote":
        symbol = args.get("symbol", "").strip()
        if not symbol:
            return json.dumps({"error": "symbol szükséges (pl. EURHUF=X)"})
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            data = json.loads(resp.text)
            result_data = data.get("chart", {}).get("result", [])
            if not result_data:
                err = data.get("chart", {}).get("error", {})
                return json.dumps({"error": f"Yahoo Finance: {err.get('description', 'no data')}", "symbol": symbol})
            meta = result_data[0].get("meta", {})
            indicators = result_data[0].get("indicators", {}).get("quote", [{}])[0]
            closes = indicators.get("close", [])
            timestamps = result_data[0].get("timestamp", [])
            price = meta.get("regularMarketPrice", 0)
            prev_close = meta.get("previousClose") or meta.get("chartPreviousClose", 0)
            change = round(price - prev_close, 4) if prev_close else 0
            change_pct = round((change / prev_close) * 100, 2) if prev_close else 0
            # Last 5 closes
            recent = []
            for i in range(max(0, len(closes) - 5), len(closes)):
                if i < len(timestamps) and closes[i] is not None:
                    from datetime import datetime as _dt
                    d = _dt.utcfromtimestamp(timestamps[i]).strftime("%m-%d")
                    recent.append({"date": d, "close": round(closes[i], 4)})
            return json.dumps({
                "symbol": symbol, "name": meta.get("shortName", symbol),
                "price": price, "change": change, "change_pct": change_pct,
                "currency": meta.get("currency", ""),
                "recent_closes": recent,
            }, ensure_ascii=False)
        except Exception as e:
            logger.error("market_quote tool error: %s: %s", type(e).__name__, e)
            return json.dumps({"error": f"Yahoo Finance hiba: {type(e).__name__}: {e}", "symbol": symbol})

    if name == "fred_data":
        series_id = args.get("series_id", "").strip().upper()
        limit = min(args.get("limit", 12), 100)
        if not series_id:
            return json.dumps({"error": "series_id szükséges (pl. UNRATE, DGS10)"})
        if not FRED_API_KEY:
            return json.dumps({"error": "FRED_API_KEY nincs beállítva a Railway env-ben"})
        try:
            url = (
                f"https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
                f"&sort_order=desc&limit={limit}"
            )
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
            data = json.loads(resp.text)
            if "error_code" in data:
                return json.dumps({"error": data.get("error_message", "FRED error"), "series_id": series_id})
            obs = data.get("observations", [])
            points = [{"date": o["date"], "value": o["value"]} for o in obs if o.get("value") != "."]
            # Also get series info
            info_url = (
                f"https://api.stlouisfed.org/fred/series"
                f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
            )
            async with httpx.AsyncClient(timeout=10) as client:
                info_resp = await client.get(info_url)
            info_data = json.loads(info_resp.text)
            serieses = info_data.get("seriess", [{}])
            title = serieses[0].get("title", series_id) if serieses else series_id
            units = serieses[0].get("units", "") if serieses else ""
            freq = serieses[0].get("frequency", "") if serieses else ""
            return json.dumps({
                "series_id": series_id, "title": title, "units": units,
                "frequency": freq, "observations": points,
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"FRED API hiba: {e}", "series_id": series_id})

    if name == "policy_rates":
        # ══ A STATDATA-N AT, NEM A NYERS DBNOMICS-TUKRON ═════════════════
        # ÉLES KÁR (2026-08-31, 00:22): a Kommandant azt kapta, hogy
        # "Alapkamatok (legutolsó ismert: 2025. június): MNB 6,5% · ECB 2,0% ·
        # Fed 4,375%". Mind a három ROSSZ — a valóság MNB 5,5% (08-25-i
        # döntés), ECB 2,25%, Fed 3,50–3,75%.
        #
        # AZ OK: ez az ág KÖZVETLENÜL a dbnomics BIS-tükrét hívta, ami MÉRTEN
        # 14 HÓNAPOT KÉSIK. A StatData `get_policy_rates` ugyanerre a kérdésre
        # helyesen felel (MNB-scrape + ECB Data Portal + FRED célsáv) — csak
        # nem azt hívtuk.
        #
        # Ez ma este a HARMADIK eset ugyanebből az osztályból: a Feldwebelnek
        # saját implementációja van valamire, ami máshol már meg van oldva, és
        # a Kommandant épp az őrizetlen ágat használja. (Lásd a
        # `schedule_recipe`-et és a `web_search` szerv-választást.)
        # Ide CSAK akkor jutunk, ha a StatData nem volt elerheto (a fenti
        # kozos elagazas mar megprobalta). Tartalek: a regi, tukor-alapu ut —
        # ami KIMONDJA, hogy elavult lehet. Nema visszaeses nincs.
        countries = args.get("countries", "HU,XM,US,CZ,PL")
        cc_list = [c.strip().upper() for c in countries.split(",") if c.strip()]
        try:
            results = {}
            async with httpx.AsyncClient(timeout=20) as client:
                for cc in cc_list[:8]:
                    url = f"https://api.db.nomics.world/v22/series/BIS/WS_CBPOL/M.{cc}?observations=1&limit=1"
                    resp = await client.get(url, headers={"User-Agent": "ClausBridge/1.0"})
                    data = json.loads(resp.text)
                    series_list = data.get("series", {}).get("docs", [])
                    if series_list:
                        s0 = series_list[0]
                        periods = s0.get("period", [])
                        values = s0.get("value", [])
                        history = []
                        start_idx = max(0, len(periods) - 6)
                        for i2 in range(len(periods) - 1, start_idx - 1, -1):
                            if i2 < len(values) and values[i2] is not None:
                                history.append({"period": periods[i2], "rate": values[i2]})
                        if history:
                            results[cc] = {"current_rate": history[0]["rate"],
                                           "as_of": history[0]["period"], "history": history}
            names = {"HU": "MNB", "XM": "ECB", "US": "Fed", "CZ": "CNB", "PL": "NBP",
                     "RO": "BNR", "GB": "BoE", "JP": "BoJ", "CH": "SNB", "TR": "TCMB"}
            return json.dumps({
                "source": "BIS/DBnomics TUKOR — TARTALEK UT",
                "warning": ("A StatData nem volt elerheto, ezert a dbnomics BIS-tukrebol "
                            "olvastunk. EZ A TUKOR MERTEN ~14 HONAPOT KESIK. NE idezd "
                            "aktualis alapkamatkent; mondd ki, hogy a friss ertek most "
                            "nem volt lekerdezheto."),
                "summary": [f"{names.get(cc, cc)}: {r['current_rate']}% ({r['as_of']}) [KESHET]"
                            for cc, r in results.items()],
                "rates": results,
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"DBnomics API hiba: {e}"})

    if name == "econ_calendar":
        days = min(args.get("days_ahead", 7), 30)
        events = []
        try:
            if FRED_API_KEY:
                from datetime import timedelta
                now_dt = datetime.now(timezone.utc)
                start = now_dt.strftime("%Y-%m-%d")
                end = (now_dt + timedelta(days=days)).strftime("%Y-%m-%d")
                url = (
                    f"https://api.stlouisfed.org/fred/releases/dates"
                    f"?api_key={FRED_API_KEY}&file_type=json"
                    f"&realtime_start={start}&realtime_end={end}"
                    f"&include_release_dates_with_no_data=true&limit=50"
                )
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url)
                data = json.loads(resp.text)
                for rd in data.get("release_dates", []):
                    events.append({
                        "date": rd.get("date", ""),
                        "release": rd.get("release_name", ""),
                        "release_id": rd.get("release_id", ""),
                        "source": "FRED",
                    })
            return json.dumps({"days_ahead": days, "events": events[:30]}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"Economic calendar hiba: {e}"})

    return json.dumps({"error": f"Ismeretlen tool: {name}"})


# ── Text-based tool call parser ────────────────────────────────────


def _parse_text_tool_call(content: str):
    """
    Parse text-based tool calls from DeepSeek DSML or Kimi markers.
    Returns (tool_name, args_dict) or None.
    """
    import re

    # DeepSeek DSML format:
    # <｜DSML｜invoke name="calendar_create">
    # <｜DSML｜parameter name="summary" string="true">Value</｜DSML｜parameter>
    dsml_match = re.search(r'invoke name="([^"]+)"', content)
    if dsml_match:
        tool_name = dsml_match.group(1)
        params = {}
        for m in re.finditer(r'parameter name="([^"]+)"[^>]*>([^<]*)<', content):
            params[m.group(1)] = m.group(2)
        if params:
            return tool_name, params

    # Kimi format:
    # <|tool_call_begin|>functions.web_search:2<|tool_call_argument_begin|>{"query": "..."}
    kimi_match = re.search(r'functions\.(\w+).*?argument_begin\|>(\{[^}]+\})', content, re.DOTALL)
    if kimi_match:
        tool_name = kimi_match.group(1)
        try:
            params = json.loads(kimi_match.group(2))
            return tool_name, params
        except json.JSONDecodeError:
            pass

    # Generic: try to find any {"key": "value"} JSON in the text
    json_match = re.search(r'\{[^{}]*"query"[^{}]*\}', content)
    if json_match:
        try:
            params = json.loads(json_match.group())
            return "web_search", params
        except json.JSONDecodeError:
            pass

    return None


# ── Helper functions ───────────────────────────────────────────────


def _fetch_open_tasks(ctx) -> str:
    """Fetch open tasks from Bridge DB for system prompt context."""
    try:
        conn = ctx.get_db()
        rows = conn.execute(
            "SELECT id, title, status, priority FROM tasks "
            "WHERE status IN ('pending', 'in_progress') "
            "ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        conn.close()
        if not rows:
            return ""
        lines = ["# NYITOTT FELADATOK"]
        for r in rows:
            cim = " ".join(str(r["title"] or "").split())
            lines.append(f"- #{r['id']} {cim[:90]}{'…' if len(cim) > 90 else ''} ({r['status']}, {r['priority']})")
        return "\n".join(lines)
    except Exception:
        return ""


def _store_bridge_reply(ctx, user_text: str, reply: str, agent_id: str, modell: str | None = None,
                        eszkozok: list | None = None):
    """Store Feldwebel reply in Bridge messages DB.

    A tárgy a kiszolgáló modellt és a hívott eszközöket is hordozza („Re: Feldwebel válasz · modell=… · eszközök=…”):
    a webes felület előzménye innen mondja ki a válasz alatt, ki felelt — a Telegram-körökre is (2026-11-22)."""
    targy = "Re: Feldwebel válasz"
    if modell:
        targy += f" · modell={modell}"
    if eszkozok:
        targy += " · eszközök=" + ",".join(dict.fromkeys(eszkozok))
    try:
        conn = ctx.get_db()
        row = conn.execute(
            "SELECT id FROM messages WHERE sender = 'kommandant' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        msg_id = row["id"] if row else None
        conn.execute(
            "INSERT INTO messages (timestamp, sender, recipient, subject, message, priority, thread_id, reply_to) "
            "VALUES (?, ?, 'kommandant', ?, ?, 'normal', ?, ?)",
            (datetime.now(timezone.utc).isoformat(),
             f"feldwebel-{agent_id}",
             targy,
             reply,
             msg_id, msg_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed to store Bridge reply: %s", e)
