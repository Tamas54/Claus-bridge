"""ANTHROPIC-KAPU (2026-11-22): a Bridge „agya” a Kommandant Anthropic-kulcsán, okos/olcsó útválasztással.

MIÉRT
-----
2026-11-20: mindkét SiliconFlow-kulcs 402-t adott (elfogyott az egyenleg), és a Bridge MINDEN LLM-útja megállt —
más szolgáltató nem volt bekötve. A Kommandant: „A bridge-be integrálhatjuk az anthropic-tól kapott api kulcson
keresztül a havi 200 dolcsis keretem, és okosan kellene megoldani, hogy ha kell okos modell, ha kell buta modell
válaszoljon.”

MIT AD
------
  * `chat(payload, purpose)` — a Bridge OpenAI-szerű payloadja BE (messages, tools, tool_calls), OpenAI-szerű
    válasz KI (`choices[0].message` + `usage`), ugyanazzal a `(data, err, notes)` alakkal, mint a `sf_chat`.
    A meglévő hívók és a tool-use hurkaik így VÁLTOZTATÁS NÉLKÜL működnek.
  * `szint_valaszt(payload, purpose)` — a szintválasztás EGY helyen, táblázatosan (`SZINT_TABLA`).
  * `strukturalt(...)` — pydantic-sémás kimenet (a Telegram-szándékfelismerőnek).
  * `allapot()` — a havi költés a `/api/status`-hoz.

A bekötés a `server.sf_chat`-ben van (a közös tölcsér, lásd `sf_onjavitas_huzalozas`): ott dől el, ki az
elsődleges, és ott történik a tartalékra váltás — KIMONDVA, a `notes`-ban (néma modellváltás nincs).

KÖRNYEZET (Railway-változók, titok soha nem kerül fájlba vagy naplóba)
---------------------------------------------------------------------
  ANTHROPIC_API_KEY         üres = a kapu nem elérhető, minden a SiliconFlow-on megy, pontosan mint eddig
  LLM_ELSODLEGES            anthropic | siliconflow — alapból anthropic, ha van kulcs
  ANTHROPIC_HAVI_KERET_USD  alap 190; e fölött a kapu átadja a munkát a SiliconFlow-nak

KÖLTÉS
------
Memóriabeli havi számláló a `usage` és az árak alapján — ÚJ TÁROLÁS NINCS (házi szabály: SQLite-alapú új megoldás
tilos). Újraindításkor nulláról indul, ezért ez csak puha fék; a KEMÉNY plafon az Anthropic Console költségkerete.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

logger = logging.getLogger("llm_kapu")

# ══ SZINTEK ═══════════════════════════════════════════════════════════════
#
# Opus 5.5: a gondolkodás NEM kapcsolható ki (thinking=disabled → 400 minden effortnál), csak az effort szabályoz;
# az alapértelmezése `medium`, ezért kimondva küldjük. Sonnet 5.5: az effort alapja `high`, a szintek újrakalibrálva.
# Haiku 5.5: az effort alapja `medium`; szerveroldali refusal-tartalék NINCS (a `fallbacks` paraméter ott 400 /
# hatástalan), ezért csak az Opusnál és a Sonnetnél kérjük. Mintavételi paraméter (temperature/top_p) egyiknél sem
# mehet: az Opus 5.5-nél megszűnt, a másik kettőnél a nem-alapértelmezett érték 400.
SZINTEK = {
    "okos": {"modell": "claude-opus-5-5", "effort": "high", "fallbacks": True},
    "kozep": {"modell": "claude-sonnet-5-5", "effort": "medium", "fallbacks": True},
    "olcso": {"modell": "claude-haiku-5-5", "effort": "low", "fallbacks": False},
}

#: Szerveroldali refusal-tartalék (Opus 5.5, Sonnet 5.5): a visszautasított kérést az API UGYANABBAN a hívásban
#: újrafuttatja az Anthropic által ajánlott modellen, az elutasítás kategóriája szerint.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# ══ ÁRAK — USD / millió token: (bemenet, kimenet, cache-olvasás, cache-írás 5 perc) ═══════════════════════
ARAK = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    # A szerveroldali refusal-tartalék modelljei — ha ők szolgálnak ki, az ő áruk számít.
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25),
    "claude-sonnet-5": (2.00, 10.00, 0.20, 2.50),
}
#: SILICONFLOW-ÁRAK (USD / millió token: bemenet, kimenet) — 11-23, a feladat-ügynök SF-modelljének keretéhez. Az IGAZSÁG
#: az SF-konzol „Usage & Charges” lapja (memória: echolot_llm_koltseg_valosag); itt a mért vagy a Kommandant által
#: mutatott árak állnak, és ami nincs mérve, az felülről becsült (`SF_ISMERETLEN_AR`): a keret inkább előbb álljon meg.
SF_ARAK = {
    # a Kommandant képernyőképe (10-03): napszakos ár, a drágább sáv (14–24 UTC) — 0,30 / 1,20
    "deepseek-ai/DeepSeek-V4.1-Flash": (0.30, 1.20),
    "zai-org/GLM-5.3-Flash": (0.15, 0.50),           # mérve 10-11 (OCR-modellmérés)
    "moonshotai/Kimi-K3": (2.70, 13.50),             # mérve 10-11; a K3.1 Pro árát nem mértük → ugyanez, felső becslés
    "moonshotai/Kimi-K3.1-Pro": (2.70, 13.50),
}
#: Nem mért SF-modell (DeepSeek V4-Pro-0813, GLM-5.3 …): a legdrágább ismert SF-ár — túlbecsül, nem alá.
SF_ISMERETLEN_AR = (2.70, 13.50)


def sf_ar(modell: str) -> tuple[float, float]:
    return SF_ARAK.get(modell or "", SF_ISMERETLEN_AR)


def sf_koltes_usd(data: dict | None) -> float:
    """Egy SiliconFlow-válasz ára a `usage`-ből (OpenAI-alak: prompt_tokens, completion_tokens — a gondolkodás a
    kimenetben van). Becslés (`SF_ARAK`); az Anthropic-válaszhoz a kapu maga írja a `koltes_usd`-t."""
    u = (data or {}).get("usage") or {}
    be, ki = sf_ar(str((data or {}).get("model") or ""))
    return (int(u.get("prompt_tokens") or 0) * be + int(u.get("completion_tokens") or 0) * ki) / 1_000_000


#: A Haiku 5.5 két árkártyája: 100K tokennél hosszabb promptra az ötszörös.
HAIKU_HOSSZU_PROMPT = 100_000
HAIKU_HOSSZU_AR = (0.50, 2.50, 0.05, 0.625)
#: Ismeretlen modellre a legdrágább ismert árral számolunk: a számláló inkább túlbecsüljön, mint alá.
ISMERETLEN_AR = (5.00, 25.00, 0.50, 6.25)

# ══ KIMENETI KERET ═══════════════════════════════════════════════════════
# A `max_tokens` plafon, nem ár: csak a ténylegesen generált tokenért fizetünk. A gondolkodás is ebből él, ezért a
# SiliconFlow-korabeli 1500-as kereteket megemeljük — különben a gondolkodás megeszi a választ (a „reasoning-only”
# csonkolás, amit a `_sf_is_truncated` már ismer). Streamelünk, így a nagy keret sem ütközik a 10 perces korlátba.
KIMENET_ALSO = 8_000
KIMENET_PLAFON = 128_000
CSONKOLAS_DUPLAZAS = 2

# ══ A SZINTVÁLASZTÁS TÁBLÁJA ════════════════════════════════════════════════
#
# Sorrend = elsőbbség: az ELSŐ illeszkedő sor dönt. Utána jön a méret-szabály (`MERET_SZABALYOK`).
SZINT_TABLA = (
    # (szabály,     mit néz,                                                                   szint)
    # 11-22, NACHTSCHICHT (nachtschicht.py): a rövid tervező lépés Opus, az éjszakai munka (eszközhurok, írás) Sonnet
    ("nachtschicht_terv", "a Nachtschicht tervező lépése (`nachtschicht/terv`): mit fog ma kérni a Kommandant", "okos"),
    ("nachtschicht", "a Nachtschicht munkája (`nachtschicht/…`): háttéranyag, levéltervezet, beadványvázlat", "kozep"),
    # 11-23, FELADAT-ÜGYNÖK (feladat_ugynok.py): a Kommandant által indított, névvel ellátott munkás egy futása
    ("feladat_ugynok", "a feladat-ügynök futása (`ugynok/<id>/futas`): ütemezett vagy egyszeri munka eszközökkel", "kozep"),
    ("cel_okos", "a `purpose` okos munka: synth, brief, elemzés, dispatch, essay, nowcast", "okos"),
    # 11-22, Kommandant: „8 cent nagyon sok egy válaszra” — a Feldwebel alapból olcsó, és csak a mélyebb kérésnél közép
    ("feldwebel_melyebb", "a Feldwebel, és a Kommandant üzenete elemzést/tervet/összevetést kér vagy hosszú", "kozep"),
    ("feldwebel", "a `purpose` a Feldwebel: a Kommandant Telegram-asszisztense, eszközhurokkal (hétköznapi kérés)", "olcso"),
    # 11-22, ANTHROPIC-TARTALÉK: a kapu mögé került hívóhelyek, ahol a minőség számít (az eredeti modell pro-szintű volt)
    ("cel_kozep", "a `purpose` közepes munka: levéltervezet a Kommandant hangján, jel- és határeset-ítélet (kauzális "
                  "híd), szál-összegzés, képleírás, Agora-perszóna (komment, reakció, témaválasztás), vita-tartalék, "
                  "a családi chat (youngereka) helyettesítő válasza",
     "kozep"),
    ("cel_olcso", "a `purpose` olcsó munka: triage, osztályozás, fordítás, rövid chat, szándék, formázás, kinyerés",
     "olcso"),
    ("sf_flash", "a kért SiliconFlow-modell FLASH-tier: dsflash, glm53f, hy3", "olcso"),
    ("sf_nehez", "a kért SiliconFlow-modell nehéz: kimi3, DeepSeek-V4-Pro, glm53", "okos"),
    ("sf_pro", "a kért SiliconFlow-modell pro: kimi (K3.1 Pro), glm5 (GLM-5.3)", "kozep"),
    ("alap", "minden más", "kozep"),
)
MERET_SZABALYOK = (
    ("nagy_bemenet", "150 000 karakternél nagyobb bemenet → legalább közép", "kozep"),
    ("apro_keres", "4 000 karakternél kisebb, eszköz nélküli kérés, ha a szint a kért modellből jött "
                   "→ legfeljebb közép", "kozep"),
)
NAGY_BEMENET_KAR = 150_000
APRO_BEMENET_KAR = 4_000

#: A Feldwebelnél ezek a szavak (vagy a hosszú üzenet) jelzik, hogy mélyebb gondolkodás kell → Sonnet; különben Haiku.
MELY_JELEK = ("elemez", "elemzés", "értékel", "gondold végig", "alaposan", "részletesen", "stratég", "terv",
              "összehasonl", "összevet", "javasol", "javaslat", "döntés", "dönts", "miért", "magyarázd", "érvel",
              "kockázat", "előrejelz", "prognózis")
MELY_HOSSZ_KAR = 400
#: Tanácskérés (jog, egészség, irat, tanulmány) és melléklet → Sonnet: itt a minőség a lényeg, nem a fél cent.
#: A Feldwebel ezekre az első körben sem kényszerít eszközt (vissza kell tudnia kérdezni).
TANACS_JELEK = ("[melléklet #", "tanács", "véleményez", "javíts", "mit tegyek", "mit vegyek", "mit szedjek",
                "tünet", "gyógyszer", "orvos", "beteg", "szerződés", "fellebb", "bíróság", "végzés",
                "határozat", "felmond", "kereset", "beadvány", "tanulmány", "kézirat")
#: „fáj a fejem”, „fájdalom” — de NEM „fájl”.
_FAJ = re.compile(r"\bfáj(?!l)")


def tanacs_keres(szoveg: str) -> bool:
    s = (szoveg or "").lower()
    return any(j in s for j in TANACS_JELEK) or bool(_FAJ.search(s))

OKOS_CELOK = ("synth", "brief", "elemz", "analy", "dispatch", "essay", "esszé", "nowcast")
OLCSO_CELOK = ("triage", "osztaly", "osztály", "classif", "fordit", "fordít", "translat", "chat",
               "szandek", "szándék", "intent", "formaz", "formáz", "kinyer", "besorol")
#: A közép szint céljai (a `cel_olcso` ELŐTT nézzük: az „email_triage/hatareset” határeset-ítélet, nem sima triage).
KOZEP_CELOK = ("levelterv", "jelkapu", "hatareset", "szal_osszegz", "kepleiras", "agora", "vita/", "youngereka")

#: A kért SiliconFlow-modell súlya. Alias ÉS szolgáltatói azonosító is, mert a hívók vegyesen adják át.
#: A `tests/test_llm_kapu.py` ellenőrzi, hogy a `server.SILICONFLOW_MODELS` MINDEN modellje szerepel itt —
#: egy új SF-modell így nem csúszhat be besorolás nélkül.
SF_SZINT = {
    "dsflash": "sf_flash", "deepseek-ai/DeepSeek-V4.1-Flash": "sf_flash", "deepseek-ai/DeepSeek-V4-Flash": "sf_flash",
    "glm53f": "sf_flash", "zai-org/GLM-5.3-Flash": "sf_flash",
    "hy3": "sf_flash", "tencent/Hy3": "sf_flash",
    "kimi3": "sf_pro", "moonshotai/Kimi-K3": "sf_nehez",   # 11-22: a kimi3 is a K3.1 Pro-ra mutat
    "deepseek": "sf_nehez", "deepseek-ai/DeepSeek-V4-Pro": "sf_nehez",
    "glm53": "sf_nehez", "zai-org/GLM-5.3": "sf_nehez",
    "kimi": "sf_pro", "moonshotai/Kimi-K2.7-Code": "sf_pro", "moonshotai/Kimi-K3.1-Pro": "sf_pro",
    "deepseek-ai/DeepSeek-V4-Pro-0813": "sf_nehez",
    "glm5": "sf_nehez", "zai-org/GLM-5.2": "sf_pro",   # 11-22: a glm5 a GLM-5.3-ra mutat
}

_TABLA_SZINT = {nev: szint for nev, _, szint in SZINT_TABLA}
_SORREND = ("olcso", "kozep", "okos")


# ══ KÖRNYEZET ═══════════════════════════════════════════════════════════
# Hívásonként olvassuk (nem import-időben): így a tesztek és a Railway-változó is egyértelműen hat.

def _kulcs() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def elerheto() -> bool:
    """Van-e Anthropic-kulcs. Nélküle a kapu nem létezik, és minden a SiliconFlow-on megy, mint eddig."""
    return bool(_kulcs())


#: A Bridge-összegző lépései: ezek akkor is a kapu szerint mennek (Claude), ha a cél egy ügynök nevét viseli.
NEVESITETT_KIVETEL = ("synth",)


def nevesitett_ugynok(purpose: str) -> str | None:
    """Ha a hívás egy NÉV SZERINT szólított ügynöké (Kimi, DeepSeek, GLM…: `telegram/chat/kimi`, `ai_query/kimi`,
    `dispatch/glm5`, `agent_with_tools/<modell-id>/…`), az ügynök kulcsa — különben None.

    A Kommandant 11-22: „Kimi még nem tudja ő kicsoda” — a kapu a Kimi-kérdést a Haikuhoz vitte, és a Haiku
    Kimi-jelmezben azt mondta, hogy SiliconFlow-n fut. Aki név szerint szól egy ügynökhöz, az ügynököt kapja; a
    Claude csak KIMONDOTT tartalék. A háromügynökös feladatkiosztás (Kimi + DeepSeek + GLM) értéke is a
    sokféleség — ha mind Claude, nincs mit összevetni."""
    p = purpose or ""
    if not p or any(k in p.lower() for k in NEVESITETT_KIVETEL):
        return None
    szeletek = set(p.split("/"))
    for kulcs in SF_SZINT:
        if ("/" in kulcs and kulcs in p) or kulcs in szeletek:
            return kulcs
    return None


def elsodleges() -> str:
    """`anthropic` vagy `siliconflow`. Az `LLM_ELSODLEGES` dönt; kulcs nélkül mindig `siliconflow`."""
    kert = os.environ.get("LLM_ELSODLEGES", "").strip().lower()
    if kert == "siliconflow":
        return "siliconflow"
    return "anthropic" if elerheto() else "siliconflow"


def havi_keret() -> float:
    try:
        return float(os.environ.get("ANTHROPIC_HAVI_KERET_USD", "") or 190.0)
    except ValueError:
        logger.warning("llm_kapu: az ANTHROPIC_HAVI_KERET_USD nem szám — az alap 190 USD él")
        return 190.0


# ══ SZINTVÁLASZTÁS ══════════════════════════════════════════════════════

def bemenet_meret(payload: dict) -> int:
    """A kérés szöveges bemenetének hossza karakterben (üzenetek + eszköz-eredmények)."""
    n = 0
    for m in payload.get("messages") or []:
        n += len(_szoveg(m.get("content")))
        for tc in m.get("tool_calls") or []:
            n += len(str((tc.get("function") or {}).get("arguments") or ""))
    return n


def _utolso_kommandant_szoveg(payload: dict) -> str:
    """Az utolsó `user` szerepű üzenet szövege (a tool-eredmény `tool` szerepű, az nem számít)."""
    for m in reversed(payload.get("messages") or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
            return str(c or "")
    return ""


def szint_valaszt(payload: dict, purpose: str = "") -> tuple[str, str]:
    """`(szint, indok)`. A `SZINT_TABLA` első illeszkedő sora, utána a `MERET_SZABALYOK`."""
    cel = (purpose or "").lower()
    if cel.startswith("nachtschicht"):
        szabaly = "nachtschicht_terv" if cel.startswith("nachtschicht/terv") else "nachtschicht"
    elif cel.startswith("ugynok/"):
        szabaly = "feladat_ugynok"
    elif any(c in cel for c in OKOS_CELOK):
        szabaly = "cel_okos"
    elif "feldwebel" in cel:
        utolso = _utolso_kommandant_szoveg(payload)
        mely = (len(utolso) > MELY_HOSSZ_KAR or any(j in utolso.lower() for j in MELY_JELEK)
                or tanacs_keres(utolso) or "melyebb" in cel)   # a Feldwebel jelzi: többlépéses, kombinált feladat
        szabaly = "feldwebel_melyebb" if mely else "feldwebel"
    elif any(c in cel for c in KOZEP_CELOK):
        szabaly = "cel_kozep"
    elif any(c in cel for c in OLCSO_CELOK):
        szabaly = "cel_olcso"
    else:
        szabaly = SF_SZINT.get(str(payload.get("model") or ""), "alap")
    szint = _TABLA_SZINT[szabaly]
    indok = szabaly

    meret = bemenet_meret(payload)
    if meret > NAGY_BEMENET_KAR and _SORREND.index(szint) < _SORREND.index("kozep"):
        szint, indok = "kozep", f"{szabaly}+nagy_bemenet({meret})"
    elif (meret < APRO_BEMENET_KAR and not payload.get("tools") and szabaly.startswith("sf_")
          and szint == "okos"):
        szint, indok = "kozep", f"{szabaly}+apro_keres({meret})"
    return szint, indok


# ══ FORDÍTÓRÉTEG: OpenAI-szerű kérés → Anthropic Messages ════════════════

_ID_TISZTITO = re.compile(r"[^a-zA-Z0-9_-]")


def _biztos_id(tid: str) -> str:
    """Az Anthropic a tool-azonosítót `^[a-zA-Z0-9_-]+$` alakban fogadja. A SiliconFlow-modellek ennél lazábbak
    (a Kimi pl. `functions.web_search:0`) — ha egy hurok SF-kör után folytatódik itt, a két oldalt ugyanúgy
    tisztítjuk, így a párosítás megmarad."""
    return _ID_TISZTITO.sub("_", str(tid or "")) or "toolu_ismeretlen"


def _szoveg(content) -> str:
    """Egy OpenAI-szerű `content` (szöveg vagy részek listája) szöveges tartalma."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                out.append(str(p.get("text") or ""))
            elif isinstance(p, str):
                out.append(p)
        return "\n".join(out)
    return str(content)


def _kep_blokk(url: str) -> dict:
    m = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", url or "", re.S)
    if m:
        return {"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}}
    return {"type": "image", "source": {"type": "url", "url": url}}


def _user_blokkok(content) -> list[dict]:
    if isinstance(content, list):
        out = []
        for p in content:
            if not isinstance(p, dict):
                if str(p).strip():
                    out.append({"type": "text", "text": str(p)})
            elif p.get("type") == "text":
                if str(p.get("text") or "").strip():
                    out.append({"type": "text", "text": str(p["text"])})
            elif p.get("type") == "image_url":
                iu = p.get("image_url")
                out.append(_kep_blokk(iu.get("url") if isinstance(iu, dict) else str(iu)))
            else:
                out.append({"type": "text", "text": json.dumps(p, ensure_ascii=False)})
        return out or [{"type": "text", "text": "(üres üzenet)"}]
    s = _szoveg(content)
    return [{"type": "text", "text": s if s.strip() else "(üres üzenet)"}]


def _argumentumok(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {"_nyers": str(raw)}
    return v if isinstance(v, dict) else {"_ertek": v}


def _eszkoz(t: dict) -> dict | None:
    f = t.get("function") if t.get("type") == "function" else t
    if not isinstance(f, dict) or not f.get("name"):
        return None
    schema = f.get("parameters") or {"type": "object", "properties": {}}
    if schema.get("type") != "object":
        schema = {"type": "object", "properties": {}}
    d = {"name": f["name"], "input_schema": schema}
    if f.get("description"):
        d["description"] = f["description"]
    if t.get("defer_loading") or f.get("defer_loading"):
        d["defer_loading"] = True
    return d


# ══ IGÉNY SZERINTI ESZKÖZÖK ÉS A CACHE-ELHETŐ MAG (2026-11-23) ══════════════
#
# A Kommandant: „Túl sokba kerül a futás… Valami tényleg nem oké.” A Feldwebel minden üzenetre ~50 ezer tokent küldött
# (45 eszköz ≈ 17k, rendszerszöveg ≈ 32k), és a változó RAG a rendszerszövegben ült, így üzenetek között semmi nem jött
# cache-ből. Három, a hívó által kért kiegészítés (az Anthropic dokumentációja szerint):
#   * `defer_loading` egy eszközön (OpenAI-alakban a `tools[i]` szintjén): az API az eszközt nem rendereli az
#     előtagba, csak ha egy `tool_reference` betölti — a `tools` lista így minden kérésben ugyanaz marad (cache).
#   * `tool_reference` egy `tool` üzeneten (névlista): az eredmény `tool_reference` blokkok (az „egyéni tool search”
#     alak); a szöveges tartalom KÜLÖN szövegblokként jön utána ugyanabban a user-fordulóban — a kettő egy
#     `tool_result`-ban nem keveredhet (az API 400-at ad).
#   * `cache_rendszer` ("5m" | "1h"): explicit cache-töréspont a vezető rendszerszöveg végén (eszközök + mag); az
#     automatikus (felső szintű) töréspont a farkat viszi. A hosszabb TTL-nek kell elöl állnia — így is van.
# A SiliconFlow ezeket nem ismeri: a `sf_tisztit` leveszi őket (ott minden eszköz látszik, mint eddig).
CACHE_TTL = {"5m": None, "1h": "1h"}


def _cache_jel(ttl: str | None) -> dict:
    return {"type": "ephemeral", **({"ttl": ttl} if ttl else {})}


def sf_tisztit(payload: dict) -> dict:
    """A payload SiliconFlow-alakja: az Anthropic-kiegészítések (`defer_loading`, `tool_reference`, `cache_rendszer`)
    nélkül. A SiliconFlow-modell így MINDEN eszközt lát (képesség nem vész el), a betöltő eredménye sima szöveg."""
    if not any(k in payload for k in ("cache_rendszer",)) and not any(
            isinstance(t, dict) and (t.get("defer_loading") or (t.get("function") or {}).get("defer_loading"))
            for t in payload.get("tools") or []) and not any(
            isinstance(m, dict) and "tool_reference" in m for m in payload.get("messages") or []):
        return payload
    p = {k: v for k, v in payload.items() if k != "cache_rendszer"}
    if p.get("tools"):
        uj = []
        for t in p["tools"]:
            if isinstance(t, dict):
                t = {k: v for k, v in t.items() if k != "defer_loading"}
                if isinstance(t.get("function"), dict) and "defer_loading" in t["function"]:
                    t["function"] = {k: v for k, v in t["function"].items() if k != "defer_loading"}
            uj.append(t)
        p["tools"] = uj
    if p.get("messages"):
        p["messages"] = [{k: v for k, v in m.items() if k != "tool_reference"} if isinstance(m, dict) else m
                         for m in p["messages"]]
    return p


def _eszkozvalasztas(payload: dict) -> dict:
    """A `tool_choice` fordítása. A kényszerített hívás (`required` / megnevezett függvény) az Opus 5.5-ön és a
    Sonnet 5.5-ön 400-at ad — ezért `auto` lesz belőle; a hívást a prompt és a megadott eszközök terelik."""
    tc = payload.get("tool_choice")
    d = {"type": "none"} if tc == "none" else {"type": "auto"}
    if d["type"] == "auto" and payload.get("parallel_tool_calls") is False:
        d["disable_parallel_tool_use"] = True
    return d


def _hozzaad(out: list, role: str, blokkok: list) -> None:
    """Egymást követő azonos szerepű üzenetek összevonása (az Anthropic váltakozó szerepet vár)."""
    if not blokkok:
        return
    if out and out[-1]["role"] == role and not out[-1].get("_nyers"):
        out[-1]["content"].extend(blokkok)
    else:
        out.append({"role": role, "content": list(blokkok)})


def _ujjlenyomat(kw: dict) -> str:
    """A rendszerprompt + az eszközkészlet + a modell lenyomata. Egy gondolat-blokk csak AZONOS előtag mellett
    küldhető vissza (a „preserved thinking” előtag-ellenőrzése) — ezt nézzük a visszajátszás előtt."""
    alap = json.dumps({"s": kw.get("system"), "t": kw.get("tools"), "m": kw.get("model")},
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(alap.encode()).hexdigest()[:24]


def fordit_keres(payload: dict, *, modell: str, effort: str, fallbacks: bool,
                 visszajatszas: bool = True) -> tuple[dict, bool]:
    """`(kwargs a beta.messages.stream-hez, történt-e gondolat-visszajátszás)`.

    Szerepek: system → a legelső(k) a felső `system` mezőbe, a későbbiek `[Rendszer-utasítás]` szövegként;
    assistant + `tool_calls` → `tool_use` blokkok; `tool` → `tool_result` blokkok egy user-üzenetben. Ha a kérésben
    NINCS eszközkészlet (pl. a hurok utolsó, kényszerített szöveges köre), az eszközhívásokat és -eredményeket
    szövegként adjuk át: eszközdefiníció nélküli `tool_use` blokkot nem küldünk.
    """
    eszkozok = [e for e in (_eszkoz(t) for t in (payload.get("tools") or []) if isinstance(t, dict)) if e]
    if eszkozok and all(e.get("defer_loading") for e in eszkozok):
        for e in eszkozok:                       # legalább egy eszköz nem lehet halasztott (az API 400-at ad)
            e.pop("defer_loading", None)
    halasztott = {e["name"] for e in eszkozok if e.get("defer_loading")}
    kw: dict = {"model": modell}
    system: list[str] = []
    out: list[dict] = []
    nevek: dict[str, str] = {}
    volt_nem_system = False
    for m in payload.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "system":
            s = _szoveg(m.get("content"))
            if not s.strip():
                continue
            if not volt_nem_system:
                system.append(s)
            else:
                _hozzaad(out, "user", [{"type": "text", "text": f"[Rendszer-utasítás]\n{s}"}])
            continue
        volt_nem_system = True
        if role == "tool":
            tid = _biztos_id(m.get("tool_call_id") or "")
            tartalom = _szoveg(m.get("content")) or "(üres eredmény)"
            hivatkozas = [n for n in (m.get("tool_reference") or []) if n in halasztott] if eszkozok else []
            if hivatkozas:
                # a betöltött eszközök `tool_reference`-ként; a kísérő szöveg (útmutató) külön blokk utána
                _hozzaad(out, "user", [{"type": "tool_result", "tool_use_id": tid,
                                        "content": [{"type": "tool_reference", "tool_name": n} for n in hivatkozas]}]
                         + ([{"type": "text", "text": tartalom}] if tartalom.strip() else []))
            elif eszkozok:
                _hozzaad(out, "user", [{"type": "tool_result", "tool_use_id": tid, "content": tartalom}])
            else:
                nev = nevek.get(tid, "eszköz")
                _hozzaad(out, "user", [{"type": "text", "text": f"[Eszköz-eredmény ({nev})]\n{tartalom}"}])
        elif role == "assistant":
            szoveg = _szoveg(m.get("content"))
            tcs = [tc for tc in (m.get("tool_calls") or []) if isinstance(tc, dict)]
            for tc in tcs:
                nevek[_biztos_id(tc.get("id") or "")] = (tc.get("function") or {}).get("name") or "eszköz"
            if eszkozok and tcs:
                blokkok = []
                if szoveg.strip():
                    blokkok.append({"type": "text", "text": szoveg})
                for i, tc in enumerate(tcs):
                    fn = tc.get("function") or {}
                    blokkok.append({"type": "tool_use", "id": _biztos_id(tc.get("id") or f"toolu_{len(out)}_{i}"),
                                    "name": fn.get("name") or "eszkoz",
                                    "input": _argumentumok(fn.get("arguments"))})
                out.append({"role": "assistant", "content": blokkok,
                            "_tool_ids": [b["id"] for b in blokkok if b["type"] == "tool_use"]})
            else:
                if tcs:
                    szoveg = (szoveg + "\n" if szoveg.strip() else "") + "\n".join(
                        f"[Eszközhívás: {(tc.get('function') or {}).get('name') or 'eszköz'}"
                        f"({(tc.get('function') or {}).get('arguments') or ''})]" for tc in tcs)
                if szoveg.strip():
                    _hozzaad(out, "assistant", [{"type": "text", "text": szoveg}])
        else:
            _hozzaad(out, "user", _user_blokkok(m.get("content")))

    if system:
        kw["system"] = "\n\n".join(system)
        ttl = payload.get("cache_rendszer")
        if ttl in CACHE_TTL:
            # explicit töréspont a STABIL előtag végén (eszközök + vezető rendszerszöveg): az üzenetek között is ez
            # jön cache-ből; a változó rész (dátum, RAG, feladatok) a hívó dolga, hogy a töréspont MÖGÉ kerüljön
            kw["system"] = [{"type": "text", "text": kw["system"], "cache_control": _cache_jel(CACHE_TTL[ttl])}]
    if eszkozok:
        kw["tools"] = eszkozok
        kw["tool_choice"] = _eszkozvalasztas(payload)
        # Eszközhurokban a következő kör ugyanazt az előtagot küldi újra: a cache-olvasás 0,05× ár.
        kw["cache_control"] = {"type": "ephemeral"}

    # GONDOLAT-VISSZAJÁTSZÁS: a tool-hurok következő körében az asszisztens-fordulót PONTOSAN úgy küldjük vissza,
    # ahogy az API adta (a gondolat-blokkokkal együtt) — ha a modell és az előtag ugyanaz. Különben a modell az
    # eszközeredmény fölött elveszítené, mit gondolt a hívás előtt.
    volt_visszajatszas = False
    if visszajatszas and eszkozok:
        ujj = _ujjlenyomat(kw)
        for u in out:
            ids = u.get("_tool_ids")
            if not ids:
                continue
            with _ZAR:
                tetel = _GONDOLAT_TAR.get(ids[0])
            if tetel and tetel["ujj"] == ujj and tetel["ids"] == ids:
                u["content"] = copy.deepcopy(tetel["tartalom"])
                u["_nyers"] = True
                volt_visszajatszas = True

    _parosit(out)
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(folytatás)"}]})
    if out[-1]["role"] == "assistant":
        # Az utolsó asszisztens-forduló „prefill” lenne: az 5.5-ös modelleken 400.
        out.append({"role": "user", "content": [{"type": "text", "text": "Folytasd."}]})
    for u in out:
        if u["role"] == "user":
            u["content"].sort(key=lambda b: 0 if b.get("type") == "tool_result" else 1)
    kw["messages"] = [{"role": u["role"], "content": u["content"]} for u in out]

    asked = int(payload.get("max_tokens") or 0)
    kw["max_tokens"] = min(max(asked, KIMENET_ALSO), KIMENET_PLAFON)
    stop = payload.get("stop")
    if stop:
        kw["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    kw["output_config"] = {"effort": effort}
    if fallbacks:
        kw["betas"] = [FALLBACK_BETA]
        kw["fallbacks"] = "default"
    return kw, volt_visszajatszas


def _parosit(out: list) -> None:
    """Minden `tool_use`-nak kell `tool_result` a KÖVETKEZŐ user-üzenetben, különben 400. A Bridge-hurkok néha
    kihagynak egy hívást (ismeretlen eszköznév, max. 3 hívás/kör) — ezeket hibás eredménnyel pótoljuk. Az árva
    `tool_result` (nincs előtte a hívása) szöveg lesz."""
    i = 0
    while i < len(out):
        u = out[i]
        if u["role"] == "assistant":
            ids = [b["id"] for b in u["content"] if b.get("type") == "tool_use"]
            if ids:
                if i + 1 >= len(out) or out[i + 1]["role"] != "user":
                    out.insert(i + 1, {"role": "user", "content": []})
                kov = out[i + 1]
                megvan = {b.get("tool_use_id") for b in kov["content"] if b.get("type") == "tool_result"}
                kov["content"][:0] = [{"type": "tool_result", "tool_use_id": t,
                                       "content": "(ez az eszközhívás nem futott le)", "is_error": True}
                                      for t in ids if t not in megvan]
        elif u["role"] == "user":
            elozo = out[i - 1] if i > 0 and out[i - 1]["role"] == "assistant" else None
            elozo_ids = {b["id"] for b in (elozo["content"] if elozo else []) if b.get("type") == "tool_use"}
            uj = []
            for b in u["content"]:
                if b.get("type") == "tool_result" and b.get("tool_use_id") not in elozo_ids:
                    uj.append({"type": "text", "text": f"[Eszköz-eredmény]\n{b.get('content')}"})
                else:
                    uj.append(b)
            u["content"] = uj or [{"type": "text", "text": "(üres üzenet)"}]
        i += 1


# ══ FORDÍTÓRÉTEG: Anthropic-válasz → OpenAI-szerű ════════════════════════

_FINISH = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "tool_use": "tool_calls",
           "refusal": "content_filter", "pause_turn": "stop"}


def fordit_valasz(msg) -> dict:
    """`{"choices": [{"message": {content, tool_calls}, "finish_reason"}], "usage", "model"}` — a `model` a
    VALÓBAN kiszolgáló modell (szerveroldali tartaléknál nem a kért)."""
    szoveg = "".join(getattr(b, "text", "") or "" for b in msg.content if getattr(b, "type", "") == "text")
    tool_calls = [{"id": b.id, "type": "function",
                   "function": {"name": b.name, "arguments": json.dumps(b.input, ensure_ascii=False)}}
                  for b in msg.content if getattr(b, "type", "") == "tool_use"]
    uzenet = {"role": "assistant", "content": szoveg}
    if tool_calls:
        uzenet["tool_calls"] = tool_calls
    u = msg.usage
    olv = int(getattr(u, "cache_read_input_tokens", 0) or 0)
    irt = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
    prompt = int(u.input_tokens or 0) + olv + irt
    return {
        "id": msg.id, "object": "chat.completion", "model": msg.model, "provider": "anthropic",
        "choices": [{"index": 0, "message": uzenet, "finish_reason": _FINISH.get(msg.stop_reason, "stop")}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": int(u.output_tokens or 0),
                  "total_tokens": prompt + int(u.output_tokens or 0),
                  "cache_read_tokens": olv, "cache_write_tokens": irt},
    }


# ── a gondolat-blokkok tára (memóriában, korlátos) ──────────────────────
_ZAR = threading.Lock()
_GONDOLAT_TAR: "OrderedDict[str, dict]" = OrderedDict()
_TAR_MERET = 256
_VISSZAJATSZHATO = {"thinking": ("type", "thinking", "signature"), "redacted_thinking": ("type", "data"),
                    "text": ("type", "text"), "tool_use": ("type", "id", "name", "input")}


def _tarol(msg, kw: dict) -> None:
    """Eszközhívó válasz nyers tartalma a következő körhöz. Csak ha minden blokk visszajátszható."""
    ids = [b.id for b in msg.content if getattr(b, "type", "") == "tool_use"]
    if not ids:
        return
    tartalom = []
    for b in msg.content:
        mezok = _VISSZAJATSZHATO.get(getattr(b, "type", ""))
        if mezok is None:
            return                      # pl. `fallback` blokk: nem játsszuk vissza, a fordított alak megy
        d = b.model_dump(mode="json", exclude_none=True)
        tartalom.append({k: d[k] for k in mezok if k in d})
    with _ZAR:
        _GONDOLAT_TAR[ids[0]] = {"tartalom": tartalom, "ids": ids, "ujj": _ujjlenyomat(kw)}
        while len(_GONDOLAT_TAR) > _TAR_MERET:
            _GONDOLAT_TAR.popitem(last=False)


# ══ KÖLTÉS ═══════════════════════════════════════════════════════════════

_KOLTES: dict = {"honap": "", "usd": 0.0, "hivasok": 0, "szintenkent": {}}


def _honap() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _ar(modell: str, bemenet: int, kimenet: int, olv: int, irt: int) -> float:
    ar = ARAK.get(modell, ISMERETLEN_AR)
    if modell == "claude-haiku-5-5" and bemenet + olv + irt > HAIKU_HOSSZU_PROMPT:
        ar = HAIKU_HOSSZU_AR
    return (bemenet * ar[0] + kimenet * ar[1] + olv * ar[2] + irt * ar[3]) / 1_000_000


def koltes_usd(usage, modell: str) -> float:
    """Egy hívás ára. Szerveroldali tartaléknál a `usage.iterations` modellenként bontja a tokeneket."""
    tetelek = [it for it in (getattr(usage, "iterations", None) or [])
               if getattr(it, "type", "") in ("message", "fallback_message")]
    if tetelek:
        return sum(_ar(getattr(it, "model", None) or modell, int(it.input_tokens or 0), int(it.output_tokens or 0),
                       int(it.cache_read_input_tokens or 0), int(it.cache_creation_input_tokens or 0))
                   for it in tetelek)
    return _ar(modell, int(usage.input_tokens or 0), int(usage.output_tokens or 0),
               int(getattr(usage, "cache_read_input_tokens", 0) or 0),
               int(getattr(usage, "cache_creation_input_tokens", 0) or 0))


def _konyvel(usage, modell: str, szint: str, purpose: str) -> float:
    usd = koltes_usd(usage, modell)
    with _ZAR:
        if _KOLTES["honap"] != _honap():
            _KOLTES.update({"honap": _honap(), "usd": 0.0, "hivasok": 0, "szintenkent": {}})
        _KOLTES["usd"] += usd
        _KOLTES["hivasok"] += 1
        sz = _KOLTES["szintenkent"].setdefault(szint, {"hivasok": 0, "usd": 0.0})
        sz["hivasok"] += 1
        sz["usd"] += usd
        osszes, n = _KOLTES["usd"], _KOLTES["hivasok"]
    # 11-23: a tokenek is a naplóba — a Kommandant: „Valami tényleg nem oké” a költséggel; mérni kell, mi megy ki
    be = int(getattr(usage, "input_tokens", 0) or 0)
    olv = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    irt = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    ki = int(getattr(usage, "output_tokens", 0) or 0)
    logger.info("llm_kapu[%s] %s: $%.5f — tokenek: be %d, cache-olvas %d, cache-ír %d, ki %d — havi költés $%.4f / "
                "$%.0f (%d hívás, %s)", purpose or "?", modell, usd, be, olv, irt, ki, osszes, havi_keret(), n,
                _KOLTES["honap"])
    return usd


def keret_kimerult() -> bool:
    with _ZAR:
        if _KOLTES["honap"] != _honap():
            return False
        return _KOLTES["usd"] >= havi_keret()


def allapot() -> dict:
    """A `/api/status` blokkja. Titok nincs benne."""
    with _ZAR:
        honap_ok = _KOLTES["honap"] == _honap()
        usd = _KOLTES["usd"] if honap_ok else 0.0
        hivasok = _KOLTES["hivasok"] if honap_ok else 0
        szintek = copy.deepcopy(_KOLTES["szintenkent"]) if honap_ok else {}
    for v in szintek.values():
        v["usd"] = round(v["usd"], 4)
    return {
        "elerheto": elerheto(), "elsodleges": elsodleges(), "honap": _honap(),
        "koltes_usd": round(usd, 4), "keret_usd": havi_keret(), "keret_kimerult": usd >= havi_keret(),
        "hivasok": hivasok, "szintenkent": szintek,
        "modellek": {k: v["modell"] for k, v in SZINTEK.items()},
        "siliconflow_megszakito": sf_megszakito.allapot(),
        "kapu_bekotve": kapu_bekotve(),
        "megjegyzes": "memóriabeli számláló, újraindításkor nulláról indul; a kemény plafon az Anthropic Console "
                      "havi költségkerete",
    }


# ══ HÍVÁS ════════════════════════════════════════════════════════════════

def _hiba(e: Exception) -> dict:
    """Tipizált hiba, a `sf_chat` hibakódjaival egy nyelven."""
    import anthropic
    uzenet = str(getattr(e, "message", "") or e)[:400]
    statusz = getattr(e, "status_code", None)
    low = uzenet.lower()
    if isinstance(e, anthropic.APIConnectionError):            # az APITimeoutError is ez
        kod = "transport"
    elif isinstance(e, anthropic.RateLimitError):
        kod = "rate_limit"
    elif isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        kod, uzenet = "auth", f"HTTP {statusz} hitelesítési hiba — ellenőrizd az ANTHROPIC_API_KEY-t"
    elif isinstance(e, anthropic.NotFoundError):
        kod = "model_missing"
    elif isinstance(e, anthropic.PaymentRequiredError) or any(
            s in low for s in ("credit balance", "usage limit", "spend limit", "billing")):
        kod = "anthropic_keret"
    elif isinstance(e, anthropic.BadRequestError):
        kod = "bad_request"
    elif isinstance(e, anthropic.APIStatusError):
        kod = "server_error" if (statusz or 0) >= 500 else "http_error"
    else:
        kod, uzenet = "kapu_hiba", f"{type(e).__name__}: {uzenet}"
    d = {"code": kod, "message": f"Anthropic {kod}: {uzenet}", "provider": "anthropic"}
    if statusz:
        d["status"] = statusz
    return d


def _ugyfel():
    import anthropic
    # Az SDK maga újrapróbálja a 408/409/429/5xx-et és a kapcsolati hibát (exponenciális várakozással).
    return anthropic.AsyncAnthropic(api_key=_kulcs(), max_retries=3)


async def _kuld(kw: dict):
    """EGY Anthropic-hívás, streamelve (a nagy `max_tokens` a nem-streamelt hívásnál 10 perces korlátba ütközne).
    A tesztek ezt cserélik le — hálózat nélkül."""
    async with _ugyfel() as client:
        async with client.beta.messages.stream(**kw) as stream:
            return await stream.get_final_message()


async def _parse(kw: dict):
    """Strukturált kimenet (pydantic-séma) — a tesztek ezt is lecserélik."""
    async with _ugyfel() as client:
        return await client.messages.parse(**kw)


def _tartalek_futott(msg) -> bool:
    """A szerveroldali tartalék dokumentált jele: `fallback_message` tétel a `usage.iterations`-ben (a `fallback`
    blokk csak akkor van, ha a kért modell már írt valamit, mielőtt elutasított)."""
    return (any(getattr(it, "type", "") == "fallback_message" for it in (getattr(msg.usage, "iterations", None) or []))
            or any(getattr(b, "type", "") == "fallback" for b in msg.content))


def rogzitett_cfg(modell: str, szint: str) -> dict:
    """KÖTÖTT MODELL (backteszt, 2026-11-22): a hívó PONTOSAN ezt a Claude-modellt kéri. Az effort a modell saját
    szintjéé (ha egyik szint sem ezt használja, a választott szinté); a szerveroldali refusal-tartalék KI — a tartalék
    más modellen (pl. `claude-opus-4-8`) válaszolna, és a futás tudás-határa már nem az lenne, amit a hívó rögzített."""
    sajat = next((s for s, c in SZINTEK.items() if c["modell"] == modell), szint)
    return {"modell": modell, "effort": SZINTEK[sajat]["effort"], "fallbacks": False}


async def chat(payload: dict, *, purpose: str = "", modell: str | None = None
               ) -> tuple[dict | None, dict | None, list[str]]:
    """Anthropic chat OpenAI-szerű payloadra. `(data, error, notes)`, mint a `sf_chat`.

    Hibánál (hálózat, 429, 5xx, költségkeret, refusal, rossz kérés) `error`-t ad — a tartalékra váltás a hívó
    (`sf_chat`) dolga, és kimondott. Csonkolt válasznál (stop_reason=max_tokens) duplázza a keretet és újrapróbál.

    `modell`: KÖTÖTT Claude-modell (a döntés-szimulátor backteszt-módja) — csak ismert árú modell (`ARAK`), szerveroldali
    tartalék nélkül; a válasz `model` mezője a VALÓBAN kiszolgáló modell.
    """
    notes: list[str] = []
    if not elerheto():
        return None, {"code": "nincs_kulcs", "message": "nincs ANTHROPIC_API_KEY", "provider": "anthropic"}, notes
    if keret_kimerult():
        return None, {"code": "havi_keret", "provider": "anthropic",
                      "message": f"az Anthropic havi kerete elérve (${_KOLTES['usd']:.2f} / ${havi_keret():.0f})"}, notes
    if modell is not None and modell not in ARAK:
        return None, {"code": "ismeretlen_modell", "provider": "anthropic",
                      "message": f"a kötött modell ({modell}) nem ismert Claude-modell (ARAK)"}, notes
    szint, indok = szint_valaszt(payload, purpose)
    cfg = SZINTEK[szint] if modell is None else rogzitett_cfg(modell, szint)
    if modell is not None:
        indok = f"kotott_modell({modell})"
    kw, visszajatszott = fordit_keres(payload, modell=cfg["modell"], effort=cfg["effort"],
                                      fallbacks=cfg["fallbacks"])
    logger.info("llm_kapu[%s] szint=%s (%s) → %s, effort=%s, kért SF-modell: %s",
                purpose or "?", szint, indok, cfg["modell"], cfg["effort"], payload.get("model"))
    duplazas = 0
    #: a hívás TELJES ára (a csonkolás miatti újrapróbálással együtt). Két helyen megy vissza: a válasz `koltes_usd`-jében
    #: (a Nachtschicht kerete) és a `usage.koltes_usd`-ben (a döntés-szimulátoré); mindkettő a kapu valós könyveléséből számol
    koltes = 0.0
    while True:
        try:
            msg = await _kuld(kw)
        except Exception as e:  # noqa: BLE001 — minden hiba tipizálva megy vissza, a tartalék a hívóé
            err = _hiba(e)
            if visszajatszott and err["code"] == "bad_request":
                # ÖNJAVÍTÁS: a visszajátszott gondolat-blokkot az API nem fogadta (megváltozott előtag) —
                # nélküle újra, a szöveg és az eszközhívások maradnak.
                notes.append(f"a `{cfg['modell']}` nem fogadta a visszajátszott gondolat-blokkot — nélküle újrapróbálva")
                logger.warning("llm_kapu[%s]: gondolat-visszajátszás elutasítva (%s), nélküle újra",
                               purpose or "?", err["message"][:200])
                kw, visszajatszott = fordit_keres(payload, modell=cfg["modell"], effort=cfg["effort"],
                                                  fallbacks=cfg["fallbacks"], visszajatszas=False)
                continue
            logger.warning("llm_kapu[%s] %s: %s", purpose or "?", cfg["modell"], err["message"][:300])
            if koltes:
                err["koltes_usd"] = round(koltes, 6)
            return None, err, notes
        koltes += _konyvel(msg.usage, cfg["modell"], szint, purpose)
        # A `stop_reason`-t a tartalom ELŐTT nézzük: refusalnál a tartalom üres vagy félbemaradt.
        if msg.stop_reason == "refusal":
            kat = getattr(getattr(msg, "stop_details", None), "category", None)
            return None, {"code": "refusal", "provider": "anthropic", "koltes_usd": round(koltes, 6),
                          "message": f"Anthropic refusal: a `{msg.model}` elutasította a kérést (kategória: {kat})"}, notes
        if msg.stop_reason == "max_tokens" and duplazas < CSONKOLAS_DUPLAZAS and kw["max_tokens"] < KIMENET_PLAFON:
            nxt = min(kw["max_tokens"] * 2, KIMENET_PLAFON)
            notes.append(f"a {kw['max_tokens']} tokenes keret KEVÉS volt a(z) `{cfg['modell']}`-nek "
                         f"(csonkolt válasz) — {nxt}-re emelve, újrapróbálva")
            kw["max_tokens"] = nxt
            duplazas += 1
            continue
        if msg.stop_reason == "max_tokens":
            notes.append(f"a válasz a(z) `{cfg['modell']}` {kw['max_tokens']} tokenes keretén is csonkolt")
        if _tartalek_futott(msg):
            notes.append(f"MODELLVÁLTÁS (Anthropic-tartalék): a `{cfg['modell']}` elutasította a kérést, "
                         f"a választ `{msg.model}` adta")
        _tarol(msg, kw)
        valasz = fordit_valasz(msg)
        valasz["koltes_usd"] = round(koltes, 6)
        valasz.setdefault("usage", {})["koltes_usd"] = round(koltes, 6)
        return valasz, None, notes


async def strukturalt(system: str, szoveg: str, sema, *, szint: str = "olcso", max_tokens: int = 300,
                      purpose: str = ""):
    """`(pydantic-objektum | None, error | None)`. Rövid osztályozó hívás sémára kötött kimenettel.

    Az olcsó szinten (Haiku 5.5, effort `low`) a gondolkodást kikapcsoljuk — ott ez engedett, és egy osztályozáshoz
    nem kell: így a válasz pár tucat token."""
    if not elerheto():
        return None, {"code": "nincs_kulcs", "message": "nincs ANTHROPIC_API_KEY"}
    if keret_kimerult():
        return None, {"code": "havi_keret", "message": "az Anthropic havi kerete elérve"}
    cfg = SZINTEK[szint]
    kw = {"model": cfg["modell"], "max_tokens": max_tokens, "system": system,
          "messages": [{"role": "user", "content": szoveg}],
          "output_config": {"effort": cfg["effort"]}, "output_format": sema}
    if szint == "olcso":
        kw["thinking"] = {"type": "disabled"}
    try:
        msg = await _parse(kw)
    except Exception as e:  # noqa: BLE001
        err = _hiba(e)
        logger.warning("llm_kapu[%s] strukturált hívás hiba: %s", purpose or "?", err["message"][:300])
        return None, err
    _konyvel(msg.usage, cfg["modell"], szint, purpose)
    if msg.stop_reason == "refusal":
        return None, {"code": "refusal", "message": "Anthropic refusal"}
    if msg.stop_reason == "max_tokens":
        return None, {"code": "csonkolt", "message": f"a {max_tokens} tokenes keret kevés volt"}
    obj = msg.parsed_output
    if obj is None:
        return None, {"code": "ures", "message": "a válasz nem illeszkedett a sémára"}
    return obj, None



# ══ SILICONFLOW-MEGSZAKÍTÓ (2026-11-22) ═══════════════════════════════════
#
# A Kommandant: „A bridge-nek az anthropic kulcsunkkal is működnie kell tudni, ha rizsrakéták nem mennek, kivéve a
# delphoi funkciót.” Ma este 0,04 $-ra fogyott a SiliconFlow-egyenleg — és minden hívás újra nekifutott a halott
# szolgáltatónak (két kulcs, újrapróbálás, várakozás), mielőtt az Anthropic átvette.
#
# A megszakító a SiliconFlow EGÉSZÉRE szól (nem egy modellre), MEMÓRIÁBAN él (új tárolás nincs — házi szabály; az
# Echolot NOTSTROM-jának mintájára), és KIMONDOTT: aki a kihagyás miatt kapja az Anthropic válaszát, a `notes`-ban
# olvassa (`jegyzet()`).
#   * 402 (elfogyott egyenleg, minden kulcson) → AZONNAL nyit: másik modell nem segít, ugyanaz a fiók fizetné.
#   * tartós kiesés (kapcsolati hiba / 5xx a teljes tartaléklánc végén) → `SF_MEGSZAKITO_KUSZOB` egymás utáni
#     bukás után nyit (egy botlás nem kiesés).
#   * nyitva: `SF_MEGSZAKITO_PERC` percig (alap 10) a kapu az SF-et ki sem próbálja.
#   * félig nyitva: az ablak lejárta után EGY hívást átenged (próba); a többi addig is kihagy. Sikeres SF-válasz →
#     zár; újabb kiesés → újra nyit egy teljes ablakra. A Bridge önvizsgálatának SF-próbája is zárhat/nyithat.
# A rossz kérés (400, hiányzó modell, auth) NEM a szolgáltató kiesése: semleges, nem számít bele.

#: Azonnal nyitó hibakód — a pénz a fiókon fogyott el, nem a modellen.
MEGSZAKITO_AZONNAL = ("payment",)
#: Tartós kiesésnek számító hibakódok (a teljes önjavító kör végén).
MEGSZAKITO_TARTOS = ("transport", "server_error")
#: A félig nyitott próba legfeljebb ennyi ideig foglalja a próba-helyet (a SiliconFlow-időkorlát fölött).
MEGSZAKITO_PROBA_S = 420.0


def _most() -> float:
    """Monoton óra — a tesztek ezt cserélik."""
    return time.monotonic()


def _env_szam(nev: str, alap: float) -> float:
    try:
        v = float(os.environ.get(nev, "") or alap)
        return v if v > 0 else alap
    except ValueError:
        logger.warning("llm_kapu: a(z) %s nem szám — az alap %s él", nev, alap)
        return alap


class SfMegszakito:
    """A SiliconFlow-kiesés emlékezete. Szálbiztos (a Bridge háttérszálakon is hív)."""

    def __init__(self) -> None:
        self._zar = threading.Lock()
        self.alaphelyzet()

    def alaphelyzet(self) -> None:
        with self._zar:
            self._ok: str | None = None          # mi nyitotta (`payment`, `transport`, …); None = zárt
            self._uzenet = ""
            self._eddig = 0.0                    # a nyitott ablak vége (monoton óra)
            self._egymas_utan = 0                # tartós kiesés-számláló (a küszöbig)
            self._proba_eddig = 0.0              # a folyamatban lévő próba határideje
            self._nyitasok = 0

    @staticmethod
    def ablak_s() -> float:
        return _env_szam("SF_MEGSZAKITO_PERC", 10.0) * 60.0

    @staticmethod
    def kuszob() -> int:
        return int(_env_szam("SF_MEGSZAKITO_KUSZOB", 3.0))

    def _nyit(self, ok: str, uzenet: str) -> None:
        # a zár alatt hívandó
        elso = self._ok is None
        self._ok, self._uzenet = ok, (uzenet or "")[:200]
        self._eddig = _most() + self.ablak_s()
        self._proba_eddig = 0.0
        self._nyitasok += 1
        logger.warning("llm_kapu: SILICONFLOW-MEGSZAKÍTÓ %s (%s) — %.0f percig a SiliconFlow kimarad, az Anthropic "
                       "válaszol", "NYIT" if elso else "ÚJRA NYIT", ok, self.ablak_s() / 60)

    def nyitva(self) -> bool:
        """Csak olvas: él-e a kihagyási ablak (a próba-helyet NEM foglalja — az embedding-visszalépés ezt nézi)."""
        with self._zar:
            return self._ok is not None and _most() < self._eddig

    def kihagy(self) -> bool:
        """True: most NE próbáld a SiliconFlow-t. False: mehet — zárt, vagy ez a félig nyitott próba (EGY hívás)."""
        with self._zar:
            if self._ok is None:
                return False
            most = _most()
            if most < self._eddig or most < self._proba_eddig:
                return True
            self._proba_eddig = most + MEGSZAKITO_PROBA_S
            logger.info("llm_kapu: a megszakító ablaka lejárt — egy próbahívás mehet a SiliconFlow-ra (%s)", self._ok)
            return False

    def siker(self) -> None:
        """A SiliconFlow kiszolgált: zár, a számláló nullázva."""
        with self._zar:
            if self._ok is not None:
                logger.warning("llm_kapu: SILICONFLOW-MEGSZAKÍTÓ ZÁR — a SiliconFlow újra kiszolgál (volt: %s)",
                               self._ok)
            self._ok, self._uzenet, self._eddig, self._proba_eddig, self._egymas_utan = None, "", 0.0, 0.0, 0

    def kieses(self, kod: str, uzenet: str = "") -> None:
        """A SiliconFlow nem szolgált ki (a teljes önjavító kör végén). A rossz kérés semleges."""
        with self._zar:
            if kod in MEGSZAKITO_AZONNAL:
                self._nyit(kod, uzenet)
            elif kod in MEGSZAKITO_TARTOS:
                self._egymas_utan += 1
                # félig nyitott próba bukása → újra nyit; zárt állapotban a küszöbig számol
                if self._ok is not None or self._egymas_utan >= self.kuszob():
                    self._nyit(kod, uzenet)
            else:
                self._proba_eddig = 0.0          # a szolgáltató válaszolt (csak a kérés rossz) — a próba-hely szabad

    def ok(self) -> str | None:
        with self._zar:
            return self._ok

    def jegyzet(self) -> str:
        """A kimondott váltás mondata a `notes`-ba."""
        with self._zar:
            ok = self._ok or "?"
            hatra = max(0.0, self._eddig - _most())
        return (f"SILICONFLOW-KIESÉS ({ok}) — a megszakító még {hatra / 60:.0f} percig kihagyja a SiliconFlow-t, "
                f"a választ az Anthropic adja")

    def allapot(self) -> dict:
        """A `/api/status` blokkja."""
        with self._zar:
            most = _most()
            return {"nyitva": self._ok is not None and most < self._eddig, "ok": self._ok,
                    "uzenet": self._uzenet, "hatra_mp": round(max(0.0, self._eddig - most)) if self._ok else 0,
                    "tartos_szamlalo": self._egymas_utan, "nyitasok": self._nyitasok,
                    "ablak_perc": self.ablak_s() / 60, "kuszob": self.kuszob()}


#: A Bridge EGY megszakítója (a `server.sf_chat`, a családi chat streamje és az embedding-visszalépés közösen nézi).
sf_megszakito = SfMegszakito()


def valaszolo_modell(payload: dict, purpose: str = "") -> str:
    """Melyik Claude-modell válaszolna erre a kérésre (a szintválasztás szerint) — a helyettesítő-jegyzethez."""
    return SZINTEK[szint_valaszt(payload, purpose)[0]]["modell"]


# ══ A KÖZÖS BELÉPŐ: `kapun()` (2026-11-22) ══════════════════════════════════
#
# A pluginok és a Feldwebel a szervert nem importálhatják (körkörös import), ezért 18 hívóhely saját, közvetlen
# SiliconFlow-POST-tal kerülte meg a kaput — ott nem volt se önjavítás, se Anthropic-tartalék. Mostantól mind ezen
# az egy belépőn megy: a `server` induláskor bejegyzi a közös kaput (`kapu_beallit(sf_chat)`), és a hívó ugyanazt a
# `(data, err, notes)` alakot kapja, mint a `sf_chat`-től. Bejegyzett kapu nélkül (önálló plugin-teszt, régi
# bekötés) a régi közvetlen út fut: EGY SiliconFlow-hívás, tipizált hibával. A `tests/test_huzalozas_or.py` őrzi,
# hogy új közvetlen `/chat/completions`-hívás ne keletkezhessen a kapu mellett.

_KAPU = None


def kapu_beallit(fn) -> None:
    """A közös kapu bejegyzése (`server.sf_chat`). None = a régi közvetlen út."""
    global _KAPU
    _KAPU = fn


def kapu_bekotve() -> bool:
    return _KAPU is not None


def sf_hibakod(status: int, szoveg: str) -> str:
    """Egyszerű hibakód a közvetlen úthoz (a `server._sf_classify` nyelvén)."""
    low = (szoveg or "").lower()
    if status == 402:
        return "payment"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "server_error"
    if status in (401, 403):
        return "auth"
    if status == 400 and ("20012" in low or "does not exist" in low):
        return "model_missing"
    if status == 400:
        return "bad_request"
    return "http_error"


def _valasz_json(resp):
    szoveg = getattr(resp, "text", "") or ""
    if szoveg:
        try:
            return json.loads(szoveg)
        except ValueError:
            pass
    return resp.json()


async def _kozvetlen_sf(payload: dict, *, sf_kulcs: str = "", sf_alap: str = "",
                        timeout: float | None = None) -> tuple[dict | None, dict | None, list[str]]:
    """A RÉGI közvetlen út (bejegyzett kapu nélkül): egy SiliconFlow-hívás, tipizált hibával, újrapróbálás nélkül."""
    import httpx
    kulcs = sf_kulcs or os.environ.get("SILICONFLOW_API_KEY", "")
    if not kulcs:
        return None, {"code": "nincs_kulcs", "message": "nincs SILICONFLOW_API_KEY (és nincs bejegyzett kapu)",
                      "provider": "siliconflow"}, []
    alap = (sf_alap or os.environ.get("SILICONFLOW_BASE_URL", "") or "https://api.siliconflow.com/v1").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout or 220) as client:
            resp = await client.post(f"{alap}/chat/completions",
                                     headers={"Authorization": f"Bearer {kulcs}",
                                              "Content-Type": "application/json"},
                                     json=sf_tisztit(payload))
    except Exception as e:  # noqa: BLE001 — tipizált hibaként megy vissza
        return None, {"code": "transport", "message": f"{type(e).__name__}: {e}"[:400], "provider": "siliconflow",
                      "idotullepes": isinstance(e, httpx.TimeoutException)}, []
    status = int(getattr(resp, "status_code", 200) or 200)
    if status != 200:
        szoveg = getattr(resp, "text", "") or ""
        return None, {"code": sf_hibakod(status, szoveg), "status": status, "provider": "siliconflow",
                      "message": f"HTTP {status} — {szoveg[:400]}"}, []
    try:
        data = _valasz_json(resp)
    except Exception as e:  # noqa: BLE001
        return None, {"code": "bad_json", "message": f"200-as válasz, de nem JSON ({e})", "provider": "siliconflow"}, []
    if not isinstance(data, dict) or not data.get("choices"):
        return None, {"code": "no_choices", "message": f"válasz `choices` nélkül: {str(data)[:300]}",
                      "provider": "siliconflow"}, []
    return data, None, []


async def kapun(payload: dict, *, purpose: str = "", sf_elso: bool = False, sf_kulcs: str = "",
                sf_alap: str = "", timeout: float | None = None) -> tuple[dict | None, dict | None, list[str]]:
    """A Bridge közös LLM-belépője. `(data, err, notes)`, OpenAI-alakú `data`, a `model` mező a VALÓBAN válaszoló
    modell (Anthropicnál `claude-…`, és `provider: anthropic`).

    `purpose`: beszédes cél (`email_triage/besorolas`, `levelterv/draft_reply`, `agora/komment/kimi3` …) — ebből dönt
    a kapu szintet (`SZINT_TABLA`), és ebből ismeri fel a név szerint szólított ügynököt (`nevesitett_ugynok`).
    `sf_elso`: a kért SiliconFlow-modell az elsődleges akkor is, ha az `LLM_ELSODLEGES` az Anthropic (a családi chat:
    a kiválasztott modell válaszol, a Claude csak kimondott tartalék). `sf_kulcs`/`sf_alap`/`timeout` csak a régi
    közvetlen útra hatnak (bejegyzett kapu nélkül)."""
    if _KAPU is not None:
        if sf_elso:
            return await _KAPU(payload, purpose=purpose, sf_elso=True)
        return await _KAPU(payload, purpose=purpose)
    return await _kozvetlen_sf(payload, sf_kulcs=sf_kulcs, sf_alap=sf_alap, timeout=timeout)


def van_llm(sf_kulcs: str = "") -> bool:
    """Van-e egyáltalán LLM a hívónak: SiliconFlow-kulcs, vagy Anthropic-kulcs a bejegyzett kapu mögött."""
    return bool(sf_kulcs) or (kapu_bekotve() and elerheto())


def valasz_szoveg(data: dict | None) -> str:
    """Az első választás szövege (üres, ha nincs)."""
    try:
        return ((data or {}).get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except (AttributeError, IndexError, TypeError):
        return ""


def helyettesitett(data: dict | None, kert_modell: str = "") -> str | None:
    """Ha NEM a kért modell válaszolt (Anthropic-tartalék vagy SF-modellváltás), a valódi modell — különben None."""
    valodi = str((data or {}).get("model") or "")
    if not valodi:
        return None
    if (data or {}).get("provider") == "anthropic" or valodi.startswith("claude-"):
        return valodi
    if kert_modell and valodi != kert_modell:
        return valodi
    return None
