"""HOPSZ-RIASZTÓ (2026-11-23): ha egy ügynökünk élesben valamit NEM tudott megcsinálni, a Faber magától nekiáll.

A Kommandant (11-23), négy kézzel javított eset után: „na pont az ilyenekre kéne fabernek benyomulni és megcsinálni.”
  1. a feladat-ügynök a sérült .docx-et nem tudta elolvasni (`no content type for partname …`);
  2. egy túl széles tiltás miatt a saját dokumentumát nem adta a Studiónak;
  3. a Feldwebel: „Nem tudom, mi a HOG” — hiányzó képesség;
  4. a feladat-ügynöknek nem volt diasor-eszköze.
A SIA/Faber-elv: „ha nem tudja megcsinálni — HOPSZ — megcsinálja magának az eszközkét.”

FELISMERÉS — determinisztikus, LLM nélkül (`eszkoz_itelet`, `kimenet_itelet`)
  KÉPESSÉG-HIÁNY → kérelem a Fabernek:
    * Python-kivétel egy eszközben (KeyError, TypeError, AttributeError, ValueError, …, „Parse error”);
    * a modell nem létező eszközt hívott („Ismeretlen tool: …”);
    * eszköz-elutasítás, ami NEM a Kommandant kimondott szabálya — pl. a saját Studiónk tiltása tudásbázis-körben (a
      szabály csak a kínai ÜGYNÖKHÖZ adást tiltja, 061c713), vagy egy saját eszköz, amit az ügynök nem kapott meg;
    * az ügynök válasza: „nem tudom, mi a <X>” (X név, és a kérésben is szerepel), „nincs … eszközöm”,
      „nem tudok … készíteni”, „csak szöveges … tudok”.
  ZAJ → nem megy: hálózat, időtúllépés, DNS; HTTP 401–404, 402, 429, 5xx (az X API 403-a is); a kínai-ügynök
    adatvédelmi tiltás; kifelé ható művelet jóváhagyása; a keret. Ez a Kommandant szabálya vagy a külvilág.
  BIZONYTALAN → nem megy (a csend az alapállapot), csak a naplóba.

KAPU (`jelez`)
  * `HOPSZ_AUTO` — alapból BE (a Kommandant kérte); 0 = semmi;
  * ugyanaz a hiba-aláírás (eszköz + hibatípus + normalizált üzenet) 7 napig egyszer;
  * napi plafon: `HOPSZ_NAPI_MAX` (alapból 3), budapesti naptári nap;
  * az állapot EGY `shared_memory`-kulcsban (`hopsz_allapot`), a közös írási úton (`server.memoria_ir`) — új tábla
    nincs. A Faber a saját oldalán (Postgres, `faber_kerelem`) ugyanezt még egyszer ellenőrzi.
  * az út a meglévő aláírt relay (`faber_relay.tovabbit`; szándék: kivansag, repo: bridge, forras: hopsz). Élesíteni a
    Faber CSAK a Kommandant [Mehet]-jére tud.

TITOK, TUDÁSBÁZIS: minden szöveg a `kitakar`-on megy át (a ~/Hirmagnetmcp/core/naplo.py mintái, bővítve, és a környezet
titkai). Tudásbázis-tartalmú körben az eszköz-argumentumok és a válasz szövege nem kerül bele, a hosszú idézet sem.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger("hopsz")

HIBA_MAX = 600
FELADAT_MAX = 200
PROBALT_MAX = 300
DUPLA_TILTAS_S = 7 * 24 * 3600
NAPI_MAX_ALAP = 3
KULCS = "hopsz_allapot"
#: tudásbázis-tartalmú körben az ennél hosszabb idézet kitakarva megy (az azonosító, fájlnév, kulcsnév marad)
VEDETT_IDEZET_MAX = 60


# ══ TITOK-KITAKARÁS ══════════════════════════════════════════════════════
# A ~/Hirmagnetmcp/core/naplo.py `kitakar` mintái (Telegram-token, ?key=, Bearer, sk-/eck_), bővítve a Bridge
# kulcsaival (eop_, GitHub, Google, JWT, DSN-jelszó, „api_key: …”) és a környezet titkos változóinak értékével.
_MINTAK: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(bot\d{5,}):[A-Za-z0-9_-]{20,}"), r"\1:***"),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}"), "***"),
    (re.compile(r"(?i)([?&](?:key|api_?key|token|access_token|secret|password|sig|signature)=)[^&\s\"']+"), r"\1***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"), r"\1***"),
    (re.compile(r"\b(sk|rk|pk)_(live|test)_[A-Za-z0-9]{10,}"), r"\1_\2_***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "sk-***"),
    (re.compile(r"\beck_[A-Za-z0-9_-]{12,}"), "eck_***"),
    (re.compile(r"\beop_[A-Za-z0-9_-]{8,}"), "eop_***"),
    (re.compile(r"\b(gh[pousr]_|github_pat_)[A-Za-z0-9_]{16,}"), r"\1***"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), "xox-***"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "AIza***"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "***"),
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s]+@"), r"\1***@"),           # postgres://user:JELSZÓ@
    (re.compile(r"(?i)([\"']?(?:api[_-]?key|token|secret|password|jelsz[oó]|x-api-key|x-bridge-key|authorization)"
                r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{6,}"), r"\1***"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "***"),
)
#: hosszú, vegyes (kis- és nagybetű + szám) jelsor — kulcs alakú; az útvonal (perjellel tagolt) nem ilyen
_KULCS_ALAK = re.compile(r"[A-Za-z0-9_-]{32,}")
_TITOK_NEV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|DSN|CREDENTIAL|COOKIE", re.I)


def _kulcs_alak(m: re.Match) -> str:
    s = m.group(0)
    return "***" if re.search(r"\d", s) and re.search(r"[a-z]", s) and re.search(r"[A-Z]", s) else s


def kitakar(szoveg) -> str:
    """A szöveg titkainak kitakarása (tiszta függvény): a környezet titkos változóinak értéke és az ismert alakok."""
    s = str(szoveg or "")
    for nev, ertek in os.environ.items():
        if ertek and len(ertek) >= 8 and _TITOK_NEV.search(nev) and ertek in s:
            s = s.replace(ertek, "***")
    for minta, csere in _MINTAK:
        s = minta.sub(csere, s)
    return _KULCS_ALAK.sub(_kulcs_alak, s)


# ══ AZ ÍTÉLET ════════════════════════════════════════════════════════════

@dataclass
class Lelet:
    tipus: str              # "hiany" (→ Faber) | "zaj" | "bizonytalan"
    eszkoz: str             # címke: az eszköz neve, vagy „a Feldwebel („HOG”)”
    hibatipus: str          # KeyError / ParseError / elutasitas / ismeretlen_eszkoz / kepesseg_hiany / …
    hiba: str               # a hiba szövege, kitakarva, legfeljebb HIBA_MAX
    ok: str = ""            # miért ez az ítélet (napló)
    kulcs: str = ""         # az aláírás alapja, ha nem az eszköz + hibatípus + hiba (a válaszból: a hiányzó dolog)

    @property
    def alairas(self) -> str:
        alap = self.kulcs or f"{self.eszkoz.lower()}|{self.hibatipus}|{_norm(self.hiba)}"
        return hashlib.sha256(alap.encode()).hexdigest()[:16]


def _norm(s: str) -> str:
    """Az aláíráshoz: kisbetű, idézetek és számok nélkül (ugyanaz a hiba más fájlnévvel, sorszámmal is ugyanaz)."""
    s = str(s or "").lower()
    s = re.sub(r"[\w.~-]*(?:/[\w.~-]+)+", "/…", s)                              # útvonal, fájlnév
    s = re.sub(r"[\"'„”`«»][^\"'„”`«»\n]{0,200}[\"'„”`«»]", "…", s)
    s = re.sub(r"0x[0-9a-f]+|\d+", "#", s)
    return re.sub(r"\s+", " ", s).strip()[:160]


#: A Kommandant kimondott szabályai, amelyek egy eszközhívást JOGGAL utasítanak el — ebből nem lesz kérelem.
#: Kifelé ható művelet: küldés, posztolás, publikálás, komment, naptárírás, ütemezés (jóváhagyás kell).
KIFELE = frozenset({
    "send_email", "send_message", "send_file_email", "export_task", "create_calendar_event", "capture_send_email",
    "calendar_create", "agora", "story_comments", "draft_reply", "post_tweet", "tweet_posztol", "tweet_tervezet",
    "hog_publish", "creator_publish", "press_pub_new", "press_pub_set", "press_pub_delete", "post_comment",
    "agora_editor", "newsletter", "engine_monitor", "engine_run", "schedule_recipe"})
#: A kínai ügynökhöz (Kimi, DeepSeek, GLM) vagy általuk olvasott tárba vivő utak — az adatvédelmi tiltás (11-22) CSAK
#: ezekre áll (ALAPELV, 11-23: „azért építjük az eszközöket, hogy használjuk”; futar_kliens.SF_FELE).
KINAI_UGYNOKHOZ = frozenset({
    "ai_query", "ai_task", "create_task", "execute_recipe", "create_recipe", "schedule_recipe", "read_task_results",
    "send_message", "megvitat", "utasit", "agora", "story_comments"})
#: Jogbővítés és költség: a feladat-ügynök a saját jogait nem bővítheti (faber, ugynok), a fizetős gépi utak (chatgpt,
#: gemini) és a SiliconFlow-ügynököt közvetlenül hívók (szimulátor, képfeltöltés-elemzés) a Kommandant döntései.
JOGBOVITES = frozenset({"faber", "ugynok", "gmail_poll", "calendar_poll", "chatgpt", "gemini", "dontes_szimulator",
                        "analyze_upload"})
SZABALY_ESZKOZOK = KIFELE | KINAI_UGYNOKHOZ | JOGBOVITES

_ELUTASITAS = re.compile(r"nem engedett|nem hívhat|nem hívod|tiltott|nincs jogod|nem adom tovább|csak claude-modell"
                         r"|nem dolgozik|nem éri el|nem olvashatja|láncolás-őr|kifelé hat", re.I)
_ADATVEDELEM = re.compile(r"csak claude-modell|kínai|siliconflow", re.I)
_SF_UGYNOK = re.compile(r"siliconflow-modellű ügynök|siliconflow-partner", re.I)    # maga az ügynök kínai modell
_SZABALY_SZOVEG = re.compile(r"kifelé hat|jóváhagy|nincs jogod|láncolás-őr|legfeljebb|egy futásban egy|önmagadat"
                             r"|már nincs meg|leállított ügynököt", re.I)
_KERET = re.compile(r"\bkeret|kvóta|quota|budget|elfogyott|limit (?:exceeded|reached)|keretelfogyott", re.I)
_BEMENET = re.compile(r"meg kell adni|szükséges|hiányzik|üres|required|missing|ismeretlen (?:művelet|modell|diatípus)",
                      re.I)

_HALOZAT = re.compile(
    r"timed? ?out|timeout|időtúllépés|name or service not known|temporary failure in name resolution"
    r"|nodename nor servname|getaddrinfo|gaierror|connection (?:refused|reset|aborted|error)|connecterror"
    r"|remoteprotocolerror|network is unreachable|no route to host|server disconnected|\bssl|eof occurred"
    r"|cancellederror|nem érhető el|nem elérhető|unreachable|\bdns\b|kapcsolódási hiba", re.I)
_HTTP_KOD = re.compile(r"(?<![\d.,$])\b(?:40[1-4]|429|5\d\d)\b(?![\d.,%$])")
_HTTP_KORNYEZET = re.compile(r"http|api|status|státusz|error|hiba|kód|code|response|válasz", re.I)
_HTTP_SZOVEG = re.compile(
    r"payment required|too many requests|rate.?limit|overloaded|insufficient|\bbalance\b|egyenleg|bad gateway"
    r"|service unavailable|internal server error|gateway time-?out|forbidden|unauthorized|invalid_grant"
    r"|not permitted to perform|x api|twitter api", re.I)

_PY = re.compile(r"\b(KeyError|TypeError|AttributeError|ValueError|IndexError|NameError|UnboundLocalError|ImportError"
                 r"|ModuleNotFoundError|NotImplementedError|AssertionError|ZeroDivisionError|JSONDecodeError"
                 r"|UnicodeDecodeError|UnicodeEncodeError|RecursionError|BadZipFile|PackageNotFoundError"
                 r"|OverflowError|LookupError)\b")
_PY_JEL = re.compile(r"parse error|not subscriptable|has no attribute|unexpected keyword argument"
                     r"|required positional argument|is not iterable|not json serializable|object is not callable"
                     r"|cannot unpack|index out of range|no content type for partname|invalid literal for"
                     r"|traceback \(most recent call last\)|referenced before assignment|is not defined", re.I)
_ISMERETLEN = re.compile(r"(?:ismeretlen (?:tool|eszköz)|unknown tool|no such tool|tool not found)\s*:?\s*`?"
                         r"(?P<x>[\w.-]{2,60})?|nincs ilyen eszköz", re.I)
#: a saját fájl-olvasónk hibaszövege a (sikeres) eszközeredményben: „(File parse error: …)”, „[Parse error: …]”
_PARSE_EREDMENYBEN = re.compile(r"[(\[](?:file )?parse error: [^\n]{0,500}", re.I)


def zaj_e(szoveg: str) -> str | None:
    """A külvilág (hálózat, HTTP, egyenleg, X API) vagy a keret — None, ha egyik sem."""
    s = str(szoveg or "")
    if _HALOZAT.search(s):
        return "hálózat/időtúllépés"
    if _HTTP_SZOVEG.search(s) or (_HTTP_KOD.search(s) and _HTTP_KORNYEZET.search(s)):
        return "HTTP (402/429/5xx/4xx, X API)"
    if _KERET.search(s):
        return "keret"
    return None


def hiba_szoveg(eredmeny) -> str | None:
    """Egy eszközeredményből a hiba szövege, vagy None, ha rendes eredmény. `{"error": …}`, `{"hiba": …}`, a
    „Traceback”/„Error:” kezdetű szöveg, és a saját fájl-olvasónk „(File parse error: …)” jele a tartalomban."""
    if eredmeny is None:
        return None
    d = eredmeny
    if isinstance(eredmeny, (str, bytes)):
        s = eredmeny.decode("utf-8", "replace") if isinstance(eredmeny, bytes) else eredmeny
        try:
            d = json.loads(s)
        except (ValueError, TypeError):
            d = None
        if not isinstance(d, dict):
            st = s.lstrip()
            if st.startswith(("Traceback (most recent call last)", "Error:", "Hiba:", "ERROR:")):
                return st[:4000]
            m = _PARSE_EREDMENYBEN.search(s[:60000])
            return m.group(0) if m else None
    if isinstance(d, dict):
        for k in ("error", "hiba"):
            if d.get(k):
                v = d[k]
                return (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))[:4000]
        for v in d.values():
            m = _PARSE_EREDMENYBEN.search(v[:60000]) if isinstance(v, str) else None
            if m:
                return m.group(0)
    return None


def _vag(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def eszkoz_itelet(eszkoz: str, hiba: str, *, args: dict | None = None, elutasitas: bool = False) -> Lelet:
    """Egy eszköz hibájának ítélete (a hiba szövege már kinyerve: `hiba_szoveg`)."""
    eszkoz = str(eszkoz or "?")
    h = _vag(kitakar(hiba), HIBA_MAX)

    def lelet(tipus, hibatipus, ok, cimke=eszkoz):
        return Lelet(tipus, cimke, hibatipus, h, ok)

    al = str((args or {}).get("eszkoz") or "")          # külső MCP (studio, echolot, engine): az alművelet
    if elutasitas or _ELUTASITAS.search(hiba):
        if _ADATVEDELEM.search(hiba):
            if _SF_UGYNOK.search(hiba):
                return lelet("zaj", "elutasitas", "kínai modellű ügynök: a Kommandant adatvédelmi szabálya")
            if eszkoz in KINAI_UGYNOKHOZ:
                return lelet("zaj", "elutasitas", "kínai ügynökhöz adás: a Kommandant adatvédelmi szabálya")
            return lelet("hiany", "elutasitas", "túl széles tiltás: a szabály csak a kínai ÜGYNÖKHÖZ adást tiltja, "
                                                "ez a saját eszközünk")
        if eszkoz in SZABALY_ESZKOZOK or al in KIFELE:
            return lelet("zaj", "elutasitas", "a Kommandant szabálya (kifelé ható / kínai ügynök / jogbővítés)")
        if _KERET.search(hiba):
            return lelet("zaj", "elutasitas", "keret")
        if _SZABALY_SZOVEG.search(hiba):
            return lelet("zaj", "elutasitas", "a Kommandant engedélye / korlátja")
        if _BEMENET.search(hiba):
            return lelet("bizonytalan", "elutasitas", "a modell rossz argumentuma")
        if re.search(r"nem engedett", hiba, re.I):
            return lelet("hiany", "elutasitas", "saját eszköz, amit az ügynök nem kapott meg (nem a Kommandant szabálya)")
        return lelet("bizonytalan", "elutasitas", "ismeretlen elutasítás")
    z = zaj_e(hiba)
    if z:
        return lelet("zaj", "kulso", z)
    m = _ISMERETLEN.search(hiba)
    if m:
        x = m.group("x") or ""
        return lelet("hiany", "ismeretlen_eszkoz", "a modell olyan eszközt kért, ami nincs",
                     cimke=f"{x} (nincs ilyen eszköz)" if x else eszkoz)
    m = _PY.search(hiba)
    if m:
        return lelet("hiany", m.group(1), "Python-kivétel az eszközben")
    m = _PY_JEL.search(hiba)
    if m:
        return lelet("hiany", "ParseError" if "parse error" in m.group(0).lower() else "Python-hiba",
                     "Python-hiba az eszközben")
    return lelet("bizonytalan", "egyeb", "nem ismert hibaalak")


# ── az ügynök válasza: „nem tudom, mi a X”, „nincs … eszközöm” ─────────
_X = r"[„“\"]?(?P<x>[^\s.,!?;:…„”“\"'()]{2,40})[”\"]?"
_NEM_TUDOM_MI = (
    re.compile(r"nem tudom,?\s+(?:hogy\s+)?(?:mi\s+(?:is\s+)?(?:az\s+a\s+|az?\s+)|mit jelent\s+(?:az?\s+)?)" + _X
               + r"(?=\s*(?:[.,!?;:…)]|$|\s+(?:jelent|pontosan)))", re.I),
    re.compile(r"nem ismerem\s+(?:az?\s+)?" + _X + r"(?=\s*(?:[.,!?;:…)]|$))", re.I),
    re.compile(r"i (?:don'?t|do not) know what (?:an?\s+|the\s+)?" + _X + r" (?:is|means)", re.I),
)
#: csak az ügynök SAJÁT hiánya (első személy: „eszközöm”, „nekem”) — a jelentésben idézett mondat nem („nincs eszközünk
#: a forint gyengülése ellen” egy miniszter szájából); az idézőjeles részt a vizsgálat előtt kivesszük
_ESZKOZ_HIANY = (
    re.compile(r"nincs\b[^.!?\n]{0,40}?\b(?P<x>[\w-]{0,30}?)[- ]?eszköz(?:öm|ünk)\b", re.I),
    re.compile(r"nincs\s+(?:ilyen|erre|ehhez|megfelelő|olyan)\s+eszköz(?:e|t)?\b[^.!?\n]{0,60}?(?:nekem|számomra"
               r"|rendelkezésemre|eszközeim|listámon)", re.I),
    re.compile(r"nem (?:rendelkezem|áll rendelkezésemre)[^.!?\n]{0,40}?eszköz", re.I),
    re.compile(r"nincs\s+(?:ilyen|ehhez|erre|megfelelő)\s+képesség", re.I),
    re.compile(r"nem tud(?:ok|om|tam)\s+(?P<x>(?:\S+\s+){0,3}?)(?:készíteni|létrehozni|generálni|renderelni|megnyitni"
               r"|elolvasni|beolvasni|szerkeszteni|átalakítani|exportálni)", re.I),
    re.compile(r"csak szöveges\s+(?P<x>\S+)\s+(?:\S+\s+)?(?:tudok|tudtam|adhatok|adok|készíthetek|tudom)", re.I),
    re.compile(r"(?:nem férek hozzá|nincs hozzáférésem)\s+(?:az?\s+)?(?P<x>(?:studi|stúdi|echolot|engine|lexonomics"
               r"|word|powerpoint|pptx|docx|diasor|prezentáci|excel|táblázat)\w*)", re.I),
    re.compile(r"csak (?:a\s+)?(?P<x>\S+)\s+szövegét\s+(?:tudom|adom|adhatom|tudtam)", re.I),
    re.compile(r"\b(?:i )?(?:don'?t|do not) have (?:a |any |the )?(?:tool|capability)", re.I),
)
#: a mondatban ott a külvilág vagy a Kommandant szabálya → nem képesség-hiány
#: (a „tiltott” szó NEM zaj: a túl széles tiltás épp képesség-hiány — a kínai-ügynök szabálya és a jóváhagyás az)
_KIMENET_ZAJ = re.compile(r"fizetős|paywall|előfizet|bejelentkez|captcha|blokkol|adatvédelm|kínai|jóváhagy|keret"
                          r"|egyenleg|nem érhető el|nem elérhető|időtúllépés|timeout|\b40[1-4]\b|\b429\b|\b5\d\d\b",
                          re.I)
#: idézet (legalább három szó) vagy idézett sor — a rövid, idézőjeles név („HOG”) marad
_IDEZET = re.compile(r"„[^”\n]{0,600}”|»[^«\n]{0,600}«|\"[^\"\n]{0,600}\"|“[^”\n]{0,600}”|^\s*>.*$", re.M)


def _idezet_ki(m: re.Match) -> str:
    return " " if m.group(0).lstrip().startswith(">") or m.group(0).count(" ") >= 2 else m.group(0)


_X_TOLTELEK = {"ilyen", "erre", "ehhez", "megfelelő", "olyan", "semmilyen", "külön", "saját", "az", "a"}


def _mondat(szoveg: str, m: re.Match) -> str:
    eleje = max(szoveg.rfind(j, 0, m.start()) for j in ".!?\n") + 1
    vege = min([i for i in (szoveg.find(j, m.end()) for j in ".!?\n") if i >= 0] or [len(szoveg)])
    return szoveg[eleje:vege + 1].strip()


def _nev_szeru(x: str) -> bool:
    return any(c.isupper() for c in x) or any(c.isdigit() for c in x) or "_" in x


def kimenet_itelet(kimenet: str, kerdes: str, forras: str, *, vedett: bool = False) -> Lelet | None:
    """Az ügynök végső válasza szerint hiányzik-e egy KÉRT képesség. None: nincs ilyen jel (vagy zaj/bizonytalan)."""
    k = _IDEZET.sub(_idezet_ki, str(kimenet or "")[:20000])   # az idézett mondat nem az ügynök szava
    q = str(kerdes or "").lower()
    if not k.strip():
        return None
    for minta in _NEM_TUDOM_MI:
        for m in minta.finditer(k):
            x = m.group("x").strip("„”\"'")
            mag = x.split("-")[0]
            if len(mag) < 2 or mag.lower() in ("kommandant",) or not _nev_szeru(mag) or mag.lower() not in q:
                logger.info("HOPSZ bizonytalan (nem megy): %s „%s” — nem név, vagy nem a kérés része", forras, x[:40])
                continue
            hiba = (f"a válasza szerint nem ismeri ezt: „{mag}” (a kérésben szerepelt) — hiányzó képesség" if vedett
                    else f"a válasza: „{_vag(_mondat(k, m), 300)}” — hiányzó képesség (a kérésben: „{mag}”)")
            return Lelet("hiany", f"{forras} („{mag}”)", "kepesseg_hiany", _vag(kitakar(hiba), HIBA_MAX),
                         "az ügynök nem ismeri, amit a Kommandant kért", kulcs=f"kimenet|nem-ismeri|{mag.lower()}")
    for minta in _ESZKOZ_HIANY:
        for m in minta.finditer(k):
            mondat = _mondat(k, m)
            if _KIMENET_ZAJ.search(mondat):
                logger.info("HOPSZ zaj (nem megy): %s — %s", forras, _vag(kitakar(mondat), 160))
                continue
            x = (m.groupdict().get("x") or "").strip(" -")
            x = "" if x.lower() in _X_TOLTELEK else x
            cimke = f"{forras} („{x}”)" if x and not vedett else forras
            hiba = ("a válasza szerint nincs hozzá eszköze — hiányzó képesség" if vedett
                    else f"a válasza: „{_vag(mondat, 300)}” — hiányzó képesség")
            return Lelet("hiany", cimke, "kepesseg_hiany", _vag(kitakar(hiba), HIBA_MAX),
                         "az ügynök szerint nincs eszköze a kért dologra",
                         kulcs=f"kimenet|nincs-eszkoz|{(x or forras).lower()}")
    return None


# ══ A KÖR GYŰJTŐJE ═══════════════════════════════════════════════════════

def _vedett_hiba(h: str) -> str:
    """Tudásbázis-tartalmú körben a hosszú idézet (lehet benne tartalom) kitakarva megy; az azonosító marad."""
    return re.sub(r"([\"'„`])([^\"'„”`\n]{%d,})([\"'”`])" % (VEDETT_IDEZET_MAX + 1), r"\1…\3", h)


def _args_szoveg(args: dict) -> str:
    reszek = []
    for k, v in list((args or {}).items())[:4]:
        v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        reszek.append(f"{k}={_vag(v, 40)}")
    return ", ".join(reszek)


class Kor:
    """Egy ügynök-kör (egy Feldwebel-üzenet, egy feladat-ügynök-futás) HOPSZ-gyűjtője. Sosem dob, és sosem lassít:
    a hívások csak listába kerülnek, az ítélet a kör végén (`vege`) születik."""

    def __init__(self, forras: str, feladat: str):
        self.forras, self.feladat = forras, str(feladat or "")
        self.hivasok: list[tuple[str, dict, str | None, bool]] = []

    def eszkoz(self, nev: str, args, eredmeny, *, elutasitas: bool = False) -> None:
        try:
            self.hivasok.append((str(nev or "?"), args if isinstance(args, dict) else {}, hiba_szoveg(eredmeny),
                                 bool(elutasitas)))
        except Exception as e:  # noqa: BLE001
            logger.debug("hopsz: a hívás nem rögzíthető: %s", e)

    def leletek(self, kimenet: str = "", *, vedett: bool = False) -> list[Lelet]:
        ki = []
        for nev, args, hiba, elut in self.hivasok:
            if hiba is not None:
                ki.append(eszkoz_itelet(nev, hiba, args=args, elutasitas=elut))
        lk = kimenet_itelet(kimenet, self.feladat, self.forras, vedett=vedett)
        if lk is not None:
            ki.append(lk)
        return ki

    def probalt(self, *, vedett: bool = False) -> str:
        if not self.hivasok:
            return "eszközt nem hívott"
        reszek = []
        for nev, args, hiba, _ in self.hivasok[:6]:
            r = nev if vedett else f"{nev}({_args_szoveg(args)})"
            reszek.append(r + (" → hiba" if hiba is not None else ""))
        if len(self.hivasok) > 6:
            reszek.append(f"… (+{len(self.hivasok) - 6})")
        return _vag(kitakar("; ".join(reszek)), PROBALT_MAX)

    def vege(self, kimenet: str = "", *, vedett: bool = False, most: float | None = None) -> int | None:
        """A kör vége: ha van képesség-hiány, EGY kérelem a Fabernek (a kapun át). → a Bridge-üzenet azonosítója."""
        try:
            if not aktiv():
                return None
            leletek = self.leletek(kimenet, vedett=vedett)
            for lel in leletek:
                if lel.tipus != "hiany":
                    logger.info("HOPSZ %s (nem megy): %s · %s · %s — %s", lel.tipus, self.forras, lel.eszkoz,
                                lel.hibatipus, lel.ok)
            hiany = next((x for x in leletek if x.tipus == "hiany"), None)
            if hiany is None:
                return None
            if vedett:
                hiany.hiba = _vedett_hiba(hiany.hiba)
            return jelez(hiany, feladat=_vag(kitakar(self.feladat), FELADAT_MAX) or "—",
                         probalt=self.probalt(vedett=vedett), most=most)
        except Exception as e:  # noqa: BLE001 — a riasztó hibája sosem állíthatja meg az ügynököt
            logger.warning("hopsz: a kör vége hibával (%s: %s)", type(e).__name__, str(e)[:200])
            return None


# ══ A KAPU ÉS A KÜLDÉS ═══════════════════════════════════════════════════

_DEPS: dict = {}
_ZAR = threading.Lock()


def beallit(*, get_db: Callable, memoria_ir: Callable, bot_token: Callable[[], str]) -> None:
    """A Bridge bekötése (server.py): get_db, memoria_ir (a shared_memory közös írási útja), bot_token()."""
    _DEPS.clear()
    _DEPS.update(get_db=get_db, memoria_ir=memoria_ir, bot_token=bot_token)


def aktiv() -> bool:
    """`HOPSZ_AUTO`: alapból BE (a Kommandant kérte); 0 / ki / off / false → semmi."""
    return os.environ.get("HOPSZ_AUTO", "1").strip().lower() not in ("0", "ki", "off", "false", "no", "nem")


def napi_max() -> int:
    try:
        return max(0, int(os.environ.get("HOPSZ_NAPI_MAX", str(NAPI_MAX_ALAP)).strip()))
    except ValueError:
        return NAPI_MAX_ALAP


def _bp_nap(most: float) -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(most, ZoneInfo("Europe/Budapest")).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return datetime.fromtimestamp(most, timezone.utc).strftime("%Y-%m-%d")


def _allapot_olvas(get_db) -> dict:
    conn = get_db()
    try:
        r = conn.execute("SELECT value FROM shared_memory WHERE key = ?", (KULCS,)).fetchone()
    finally:
        conn.close()
    try:
        d = json.loads(r[0]) if r else {}
    except (ValueError, TypeError):
        d = {}
    d.setdefault("alairasok", {})
    return d


def _allapot_ir(get_db, memoria_ir, d: dict) -> None:
    conn = get_db()
    try:
        memoria_ir(conn, KULCS, json.dumps(d, ensure_ascii=False), category="hopsz", tags="hopsz,faber",
                   updated_by="hopsz")
        conn.commit()
    finally:
        conn.close()


def kivansag(lelet: Lelet, *, feladat: str, probalt: str) -> str:
    """A Faber kívánsága (a Kommandant mintája, 11-23). Elutasításnál az is benne van, miért nem a Kommandant szabálya."""
    hiba = lelet.hiba + (f" [a HOPSZ szerint: {lelet.ok}]" if lelet.hibatipus == "elutasitas" else "")
    return kitakar(f"HOPSZ (automatikus): {lelet.eszkoz} nem ment: {hiba}. A feladat: {feladat}. "
                   f"Amit az ügynök próbált: {probalt}. Javítsd meg úgy, hogy legközelebb menjen; teszt a hibára.")


def jelez(lelet: Lelet, *, feladat: str, probalt: str, most: float | None = None) -> int | None:
    """A kapu (kapcsoló, 7 napos duplikáció, napi plafon), aztán EGY aláírt kérelem a Fabernek (repo: bridge)."""
    if not aktiv() or lelet.tipus != "hiany":
        return None
    get_db, memoria_ir, token_f = _DEPS.get("get_db"), _DEPS.get("memoria_ir"), _DEPS.get("bot_token")
    if not (get_db and memoria_ir):
        logger.info("HOPSZ: nincs bekötve (server.py) — kimarad: %s", lelet.eszkoz)
        return None
    token = (token_f() if callable(token_f) else "") or ""
    if not token:
        logger.info("HOPSZ: nincs Telegram-token (a relay kulcsa) — kimarad: %s", lelet.eszkoz)
        return None
    most = time.time() if most is None else most
    sig = lelet.alairas
    import faber_relay
    with _ZAR:
        d = _allapot_olvas(get_db)
        d["alairasok"] = {k: v for k, v in d["alairasok"].items() if most - float(v) < DUPLA_TILTAS_S}
        if sig in d["alairasok"]:
            logger.info("HOPSZ: ugyanez a hiba 7 napon belül már ment (%s · %s) — kimarad", lelet.eszkoz, sig)
            return None
        nap = _bp_nap(most)
        db = int(d.get("db") or 0) if d.get("nap") == nap else 0
        if db >= napi_max():
            logger.warning("HOPSZ: a napi plafon (%d) betelt — kimarad: %s · %s", napi_max(), lelet.eszkoz,
                           lelet.hibatipus)
            return None
        szoveg = kivansag(lelet, feladat=feladat, probalt=probalt)
        adat = faber_relay.szandek_adat({"szandek": "kivansag", "szoveg": szoveg, "repo": "bridge", "kerelem": None,
                                         "forras": "hopsz", "eszkoz": lelet.eszkoz, "alairas": sig})
        conn = get_db()
        try:
            mid = faber_relay.tovabbit(conn, token, "szandek", adat, datetime.now(timezone.utc).isoformat())
        finally:
            conn.close()
        if not mid:
            return None
        d["alairasok"][sig] = most
        d["nap"], d["db"] = nap, db + 1
        _allapot_ir(get_db, memoria_ir, d)
    logger.warning("HOPSZ → Faber (Bridge #%s): %s · %s — %s", mid, lelet.eszkoz, lelet.hibatipus, lelet.ok)
    return mid
