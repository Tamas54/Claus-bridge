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
    ("cel_okos", "a `purpose` okos munka: synth, brief, elemzés, dispatch, essay, nowcast", "okos"),
    # 11-22, Kommandant: „8 cent nagyon sok egy válaszra” — a Feldwebel alapból olcsó, és csak a mélyebb kérésnél közép
    ("feldwebel_melyebb", "a Feldwebel, és a Kommandant üzenete elemzést/tervet/összevetést kér vagy hosszú", "kozep"),
    ("feldwebel", "a `purpose` a Feldwebel: a Kommandant Telegram-asszisztense, eszközhurokkal (hétköznapi kérés)", "olcso"),
    ("cel_olcso", "a `purpose` olcsó munka: triage, osztályozás, fordítás, rövid chat, szándék", "olcso"),
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
               "szandek", "szándék", "intent")

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
    elif any(c in cel for c in OKOS_CELOK):
        szabaly = "cel_okos"
    elif "feldwebel" in cel:
        utolso = _utolso_kommandant_szoveg(payload)
        mely = (len(utolso) > MELY_HOSSZ_KAR or any(j in utolso.lower() for j in MELY_JELEK)
                or tanacs_keres(utolso) or "melyebb" in cel)   # a Feldwebel jelzi: többlépéses, kombinált feladat
        szabaly = "feldwebel_melyebb" if mely else "feldwebel"
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
    return d


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
            if eszkozok:
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
    logger.info("llm_kapu[%s] %s: $%.5f — havi költés $%.4f / $%.0f (%d hívás, %s)",
                purpose or "?", modell, usd, osszes, havi_keret(), n, _KOLTES["honap"])
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


async def chat(payload: dict, *, purpose: str = "") -> tuple[dict | None, dict | None, list[str]]:
    """Anthropic chat OpenAI-szerű payloadra. `(data, error, notes)`, mint a `sf_chat`.

    Hibánál (hálózat, 429, 5xx, költségkeret, refusal, rossz kérés) `error`-t ad — a tartalékra váltás a hívó
    (`sf_chat`) dolga, és kimondott. Csonkolt válasznál (stop_reason=max_tokens) duplázza a keretet és újrapróbál.
    """
    notes: list[str] = []
    if not elerheto():
        return None, {"code": "nincs_kulcs", "message": "nincs ANTHROPIC_API_KEY", "provider": "anthropic"}, notes
    if keret_kimerult():
        return None, {"code": "havi_keret", "provider": "anthropic",
                      "message": f"az Anthropic havi kerete elérve (${_KOLTES['usd']:.2f} / ${havi_keret():.0f})"}, notes
    szint, indok = szint_valaszt(payload, purpose)
    cfg = SZINTEK[szint]
    kw, visszajatszott = fordit_keres(payload, modell=cfg["modell"], effort=cfg["effort"],
                                      fallbacks=cfg["fallbacks"])
    logger.info("llm_kapu[%s] szint=%s (%s) → %s, effort=%s, kért SF-modell: %s",
                purpose or "?", szint, indok, cfg["modell"], cfg["effort"], payload.get("model"))
    duplazas = 0
    #: a hívás (újrapróbálásokkal együtt) könyvelt költsége — a válaszban `koltes_usd`, hogy a hívó (pl. a
    #: Nachtschicht éjszakai kerete) a kapu valódi könyveléséből számolhasson, ne a saját becsléséből
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
            return None, err, notes
        koltes += _konyvel(msg.usage, cfg["modell"], szint, purpose)
        # A `stop_reason`-t a tartalom ELŐTT nézzük: refusalnál a tartalom üres vagy félbemaradt.
        if msg.stop_reason == "refusal":
            kat = getattr(getattr(msg, "stop_details", None), "category", None)
            return None, {"code": "refusal", "provider": "anthropic", "koltes_usd": koltes,
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
        valasz["koltes_usd"] = koltes
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
