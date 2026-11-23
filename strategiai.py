"""STRATÉGIAI MÓD (2026-11-24): a Bridge vezérkari beszélgetőtárs — álláspont, kezdeményezés, visszamérés.

A Kommandant (11-23 éjjel): „de legalább »beszélget velem«?” Addig a Feldwebel válaszolt, de nem kezdeményezett, és
nem volt saját álláspontja; hétköznapi beszélgetésre Haiku futott. Az alapelv: „A STRATÉGIAI LÁTÁSMÓD” a mi előnyünk
(helyzetkép a 12 nyelvű képből → értékelés → változatok → álláspont → kérdés → visszamérés).

MIT AD
------
1. A FELDWEBEL STRATÉGIAI MÓDJA (`feldwebel/responder.py`): ha az üzenet stratégiai (a szándékfelismerő,
   `eszkoz_ut.strategiai_szandek`, LLM nélkül; „gondolkodj”, „értékeld”, „mi a véleményed” MINDIG), vagy egy stratégiai
   beszélgetés folytatása (`FOLYTATAS_ORA`), a rendszerszöveg a Kommandanttal együtt fogalmazott prompttal bővül
   (`prompts/strategiai.md`), a modell Opus 5.5 (`llm_kapu` „strategiai” sora), és az Echolot-, makró- és
   tudásbázis-eszközök előtöltve jönnek. Egyszerű kérdésnél SEMMI nem változik: a karcsú alapprompt a régi.
2. REGGELI NYITÓ (`cron_lepes`, alapból 07:20 Budapest): 3 helyzet (gazdaság, geopolitika, belpolitika) abból, amit
   aznap az Echolot 12 nyelvű képe a legfontosabbnak mutat (lapszám hu + en, a nyelvterületeken átívelő témák,
   `regional_framing`, `frame_divergence`, `attention_gap`). Helyzetenként: helyzetkép 2 mondatban → álláspont
   1 mondatban → egy kérdés. A Kommandant válasza ugyanabba a beszélgetésbe megy, stratégiai módban (az előzmény
   jelölt: `STRATEGIAI_JEL`).
3. NAPLÓ: minden stratégiai értékelésből egy ellenőrizhető állítás dátummal → Postgres, csak hozzáfűzés, hash-lánc
   (a `piac_elteres` mintája és függvényei). A Telegram-nyitó csak a SIKERES naplózás után megy (ha van napló).
   HAVI KÖR (`havi_kor`, minden hónap 1-jén 08:00 után): a lejárt állításokat az Echolot korpuszából vett bizonyítékkal
   egy Opus-bíró pontozza (igaz / hamis / eldöntetlen), Brier-pontszámmal. Az ítélet csak javaslat: a Kommandant
   „#31 igaz” üzenete helyesbíti (`helyesbites`), és az az érvényes.
4. KERET: a stratégiai mód napi kerete (`STRATEGIAI_NAPI_KERET_USD`, alap 2 USD) a kapu könyveléséből
   (`llm_kapu.strategiai_allapot`); fölötte a Feldwebel rendes módban felel, és ezt kimondja. A reggeli nyitó és a havi
   kör saját kerettel fut (`nachtschicht.Szamla`). A kabin LLM-költés blokkja mutatja.

KAPCSOLÓK (Railway-változók)
----------------------------
  STRATEGIAI_MOD=0          a Feldwebel stratégiai módja KI (minden a régi úton)
  STRATEGIAI_REGGELI=0      a reggeli nyitó KI;  STRATEGIAI_REGGELI_IDO=HH:MM (alap 07:20)
  STRATEGIAI_PG_DSN         a napló DSN-je (tartalék: ELSO_KOZLES_PG_DSN). SQLite nincs (házi szabály); DSN nélkül
                            nincs napló, és ezt a válasz kimondja.

HATÁROK: kifelé (levél, poszt, fizetés) semmi nem megy a Kommandant igene nélkül — ez a modul csak a Kommandantnak ír
(Telegram, kabin) és a saját naplójába.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger("strategiai")

BP = ZoneInfo("Europe/Budapest")
PROMPT_UT = Path(__file__).resolve().parent / "prompts" / "strategiai.md"
#: a telegram_history `agent_id`-jában: ez a forduló stratégiai volt (a válasz erre a folytatás)
STRATEGIAI_JEL = ":strategiai"
#: ennyi órán belül a Kommandant válasza egy stratégiai fordulóra (a reggeli nyitóra is) stratégiai folytatás
FOLYTATAS_ORA = 12
#: a stratégiai módban előtöltött eszköz-kategóriák (a 12 nyelvű kép, a számok, a Kommandant korábbi gondolatai)
ELOTOLTES = ("hirek", "makro", "memoria")
CEL_FELDWEBEL = "feldwebel/strategiai/telegram"
CEL_NYITO_TERV = "strategiai/reggeli/terv"
CEL_NYITO_IRAS = "strategiai/reggeli/iras"
CEL_PONTOZAS = "strategiai/pontozas"
#: a SiliconFlow-tartalék modellje (ha az Anthropic-kapu nem él); az Anthropic-szintet a `purpose` választja
SF_MODELL = "deepseek-ai/DeepSeek-V4-Pro"
REGGELI_IDO = (7, 20)
REGGELI_ABLAK_VEGE = (10, 0)         # ennél később nem indul (egy késő újraindulás ne küldjön délben „reggelit”)
REGGELI_KERET_USD = 0.80
HAVI_KOR_ORA = 8
HAVI_KOR_KERET_USD = 5.0
HAVI_KOR_CSOMAG = 8                  # ennyi állítás egy bírói hívásban
ALLITAS_MAX_EV = 3                   # ennél távolabbi határidő nem ellenőrizhető belátható időn belül
TERULETEK = {"gazdasag": "Gazdaság", "geopolitika": "Geopolitika", "belpolitika": "Belpolitika"}


def _env_be(nev: str) -> bool:
    return os.environ.get(nev, "1").strip().lower() not in ("0", "false", "ki", "off", "no")


def bekapcsolva() -> bool:
    return _env_be("STRATEGIAI_MOD")


def reggeli_bekapcsolva() -> bool:
    return _env_be("STRATEGIAI_REGGELI")


def reggeli_ido() -> tuple:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", os.environ.get("STRATEGIAI_REGGELI_IDO", "").strip())
    if m and int(m[1]) < 24 and int(m[2]) < 60:
        return int(m[1]), int(m[2])
    return REGGELI_IDO


# ══ 1. A PROMPT ═══════════════════════════════════════════════════════════════

#: A gépi feldolgozás formai szabálya (nem a Kommandant szövege, csak a naplóhoz): az állítás egy jól elkülönülő sor.
FORMAI_SZABALY = """FORMAI SZABÁLY (a naplóhoz, gépi feldolgozás): az ellenőrizhető állítás a válasz VÉGÉN, külön sorban, \
pontosan így álljon:
Állítás: ÉÉÉÉ-HH-NN-ig <mi történik, egy mondatban, kívülről ellenőrizhetően> (<valószínűség>%)
Ha a válasz nem értékelés (csak pontosítasz vagy visszakérdezel), ez a sor elmarad."""


@functools.lru_cache(maxsize=1)
def prompt() -> str:
    """A Kommandanttal együtt fogalmazott szöveg (`prompts/strategiai.md`)."""
    return PROMPT_UT.read_text(encoding="utf-8").strip()


def rendszer_toldat() -> str:
    """A Feldwebel rendszerszövegének stratégiai toldata — CSAK stratégiai módban kerül be (a karcsú alapprompt marad)."""
    return "\n\n══ STRATÉGIAI MÓD ══\n" + prompt() + "\n\n" + FORMAI_SZABALY


# ══ 2. A MÓD ══════════════════════════════════════════════════════════════════

@dataclass
class Mod:
    aktiv: bool = False
    ok: str | None = None          # miért stratégiai (a szándékfelismerő indoka)
    jegyzet: str | None = None     # kimondandó (pl. a napi keret elfogyott)


def _ido(s) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def elozo_strategiai(history: list, most: datetime | None = None) -> bool:
    """A Kommandant mostani üzenete előtti utolsó asszisztens-forduló stratégiai volt-e (`STRATEGIAI_JEL`), és
    `FOLYTATAS_ORA`-n belül. A `history` a `feldwebel.history.get_history` alakja (a mostani üzenettel a végén)."""
    most = most or datetime.now(timezone.utc)
    for m in reversed(history[:-1] if history and history[-1].get("role") == "user" else history):
        if m.get("role") != "assistant":
            continue
        if STRATEGIAI_JEL not in str(m.get("agent_id") or ""):
            return False
        t = _ido(m.get("timestamp"))
        return t is None or most - t <= timedelta(hours=FOLYTATAS_ORA)
    return False


def mod(szoveg: str, history: list, most: datetime | None = None) -> Mod:
    """A Feldwebel mostani köre stratégiai-e. A szándékot a meglévő determinisztikus felismerő adja; a keretet a kapu."""
    if not bekapcsolva():
        return Mod()
    from feldwebel import eszkoz_ut
    import llm_kapu
    ok = eszkoz_ut.strategiai_szandek(szoveg, elozo_strategiai=elozo_strategiai(history, most))
    if not ok:
        return Mod()
    if llm_kapu.elerheto() and llm_kapu.strategiai_kimerult():
        a = llm_kapu.strategiai_allapot()
        return Mod(False, ok, f"a stratégiai mód mai kerete elfogyott (${a['koltes_usd']:.2f} / ${a['keret_usd']:.2f}), "
                              f"ezért rendes módban felelek — a STRATEGIAI_NAPI_KERET_USD emeli")
    return Mod(True, ok)


# ══ 3. AZ ÁLLÍTÁS (determinisztikus kinyerés) ═════════════════════════════════

_ALLITAS_SOR = re.compile(r"^[\s>*_\-•📌]*(?:ellen[őo]rizhet[őo]\s+)?[áa]ll[íi]t[áa]s[\s*_]*:[\s*_]*(.+)$",
                          re.IGNORECASE | re.MULTILINE)
_DATUM = re.compile(r"(\d{4})[-.](\d{2})[-.](\d{2})\.?(?:\s*-?\s*(?:ig|áig|éig|ig\b))?", re.IGNORECASE)
#: a valószínűség CSAK zárójelben („(70%)”) — a mondat végi „4%” az állítás része, nem a valószínűsége
_VALOSZINUSEG = re.compile(r"[\(\[]\s*(?:p\s*=\s*|valószínűség\w*\s*:?\s*)?(\d{1,3})(?:[.,]\d+)?\s*%\s*[\)\]][\s*_.]*$",
                           re.IGNORECASE)


def allitas_kivesz(szoveg: str, ma: date | None = None) -> dict | None:
    """Az `Állítás: ÉÉÉÉ-HH-NN-ig … (70%)` sor → {hatarido, allitas, valoszinuseg}. None, ha nincs, vagy nem ellenőrizhető
    (a határidő nem a jövőben van, vagy `ALLITAS_MAX_EV` évnél távolabb)."""
    ma = ma or datetime.now(BP).date()
    talalatok = _ALLITAS_SOR.findall(szoveg or "")
    for sor in reversed(talalatok):                      # a válasz végén álló az érvényes
        sor = sor.strip().strip("*_ ")
        m = _DATUM.search(sor)
        if not m or m.start() > 12:
            continue
        try:
            hatarido = date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            continue
        if hatarido <= ma or hatarido > ma.replace(year=ma.year + ALLITAS_MAX_EV):
            continue
        maradek = sor[m.end():].strip(" :—–-,")
        p = None
        mp = _VALOSZINUSEG.search(maradek)
        if mp:
            p = int(mp[1])
            maradek = maradek[:mp.start()].strip(" ,;—–-")
            if not 1 <= p <= 99:
                p = None
        maradek = maradek.strip().strip("*_ ").rstrip(" ,;")
        if len(maradek) < 10:
            continue
        return {"hatarido": hatarido.isoformat(), "allitas": maradek[:500], "valoszinuseg": p}
    return None


def allitas_kulcs(a: dict) -> str:
    """Ugyanaz az állítás (határidő + normalizált szöveg) egyszer kerül a naplóba."""
    norm = " ".join(_ekezet_nelkul(a["allitas"]).split())
    return f"{a['hatarido']}:{hashlib.sha256(norm.encode()).hexdigest()[:16]}"


def _ekezet_nelkul(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


# ══ 4. A NAPLÓ (Postgres, csak hozzáfűzés, hash-lánc — a piac_elteres mintája; teszt alatt memória) ═════════════

SEMA = """
CREATE TABLE IF NOT EXISTS strategiai_naplo (
    id          bigserial PRIMARY KEY,
    tipus       text NOT NULL,            -- 'allitas' | 'nyito' | 'pontozas' | 'helyesbites' | 'havi_kor'
    kulcs       text NOT NULL,            -- allitas: határidő:hash; nyito: nap; pontozas: az állítás id-je; havi_kor: hónap
    rogzitve    timestamptz NOT NULL DEFAULT now(),
    payload     jsonb NOT NULL,
    elozo_hash  text NOT NULL,            -- az előző sor hash-e (az első sornál 64 nulla)
    sor_hash    text NOT NULL,            -- sha256(elozo_hash | tipus | kulcs | kanonikus payload)
    UNIQUE (tipus, kulcs)
);
CREATE INDEX IF NOT EXISTS strategiai_naplo_tipus_idx ON strategiai_naplo (tipus, rogzitve);
CREATE OR REPLACE FUNCTION strategiai_naplo_csak_hozzafuzes() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'strategiai_naplo: csak hozzáfűzés (a rögzített állítás nem írható felül)';
END $$;
CREATE TRIGGER strategiai_naplo_csak_hozzafuzes BEFORE UPDATE OR DELETE ON strategiai_naplo
    FOR EACH ROW EXECUTE FUNCTION strategiai_naplo_csak_hozzafuzes();
"""


def _lanc():
    """A hash-lánc függvényei a piac_elteres-ből (egy megvalósítás)."""
    import piac_elteres as pe
    return pe


class MemoriaNaplo:
    """A PgNaplo felülete memóriában (teszt). Ugyanaz a szemantika: hozzáfűzés, hash-lánc, (tipus, kulcs) egyedi."""

    def __init__(self):
        self._sorok: list[dict] = []
        self._zar = threading.Lock()

    def hozzafuz(self, tipus: str, kulcs: str, payload: dict) -> dict | None:
        pe = _lanc()
        with self._zar:
            if any(s["tipus"] == tipus and s["kulcs"] == kulcs for s in self._sorok):
                return None
            elozo = self._sorok[-1]["sor_hash"] if self._sorok else pe.NULLA_HASH
            payload = json.loads(pe.kanon(payload))
            s = {"id": len(self._sorok) + 1, "tipus": tipus, "kulcs": kulcs, "rogzitve": datetime.now(timezone.utc),
                 "payload": payload, "elozo_hash": elozo, "sor_hash": pe.sor_hash(elozo, tipus, kulcs, payload)}
            self._sorok.append(s)
            return dict(s)

    def sorok(self, tipus: str | None = None) -> list[dict]:
        return [dict(s) for s in self._sorok if tipus is None or s["tipus"] == tipus]


class PgNaplo:
    """A `strategiai_naplo` tábla. A séma első használatkor jön létre (idempotens migráció, `SEMA`); a hash-lánc egy
    tranzakcióban, advisory lockkal (két író — pl. deploy-átfedés — sem ágaztatja el)."""

    ZAR = 7_720_261_124

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._sema_kesz = False

    def _kapcsolat(self):
        import psycopg
        return psycopg.connect(self.dsn, connect_timeout=5)

    def _sema(self, k) -> None:
        if not self._sema_kesz:
            if k.execute("SELECT to_regclass('strategiai_naplo')").fetchone()[0] is None:
                k.execute(SEMA)
            self._sema_kesz = True

    def hozzafuz(self, tipus: str, kulcs: str, payload: dict) -> dict | None:
        from psycopg.types.json import Jsonb
        pe = _lanc()
        payload = json.loads(pe.kanon(payload))
        with self._kapcsolat() as k:
            self._sema(k)
            k.execute("SELECT pg_advisory_xact_lock(%s)", [self.ZAR])
            if k.execute("SELECT 1 FROM strategiai_naplo WHERE tipus = %s AND kulcs = %s", [tipus, kulcs]).fetchone():
                return None
            r = k.execute("SELECT sor_hash FROM strategiai_naplo ORDER BY id DESC LIMIT 1").fetchone()
            elozo = r[0] if r else pe.NULLA_HASH
            h = pe.sor_hash(elozo, tipus, kulcs, payload)
            row = k.execute("INSERT INTO strategiai_naplo (tipus, kulcs, payload, elozo_hash, sor_hash) "
                            "VALUES (%s, %s, %s, %s, %s) RETURNING id, rogzitve",
                            [tipus, kulcs, Jsonb(payload), elozo, h]).fetchone()
            k.commit()
        return {"id": row[0], "tipus": tipus, "kulcs": kulcs, "rogzitve": row[1], "payload": payload,
                "elozo_hash": elozo, "sor_hash": h}

    def sorok(self, tipus: str | None = None) -> list[dict]:
        from psycopg.rows import dict_row
        with self._kapcsolat() as k:
            self._sema(k)
            k.row_factory = dict_row
            q = ("SELECT id, tipus, kulcs, rogzitve, payload, elozo_hash, sor_hash FROM strategiai_naplo"
                 + (" WHERE tipus = %s" if tipus else "") + " ORDER BY id")
            return [dict(r) for r in k.execute(q, [tipus] if tipus else []).fetchall()]


_NAPLO: dict = {}


def naplo():
    """A folyamat naplója, ha van DSN (`STRATEGIAI_PG_DSN`, tartalék `ELSO_KOZLES_PG_DSN`); különben None."""
    dsn = (os.environ.get("STRATEGIAI_PG_DSN") or os.environ.get("ELSO_KOZLES_PG_DSN") or "").strip()
    if not dsn:
        return None
    try:
        import psycopg  # noqa: F401
    except ImportError:
        logger.warning("strategiai: van DSN, de a psycopg nincs telepítve — nincs napló")
        return None
    return _NAPLO.setdefault(dsn, PgNaplo(dsn))


def lanc_ellenoriz(n) -> dict:
    return _lanc().lanc_ellenoriz(n.sorok())


def rogzit(a: dict, *, forras: str, n="alap", **reszletek) -> dict:
    """Egy állítás a naplóba. → {"ok": True, "id", "uj"} | {"ok": False, "ok_szoveg"}. Sosem dob."""
    n = naplo() if n == "alap" else n
    if n is None:
        return {"ok": False, "ok_szoveg": "nincs napló (STRATEGIAI_PG_DSN)"}
    kulcs = allitas_kulcs(a)
    payload = {**a, "forras": forras, "rogzitve_bp": datetime.now(BP).isoformat(timespec="seconds"),
               **{k: v for k, v in reszletek.items() if v is not None}}
    try:
        r = n.hozzafuz("allitas", kulcs, payload)
        if r is None:                                     # ugyanez az állítás már bent van
            regi = next((s for s in n.sorok("allitas") if s["kulcs"] == kulcs), None)
            return {"ok": True, "id": regi["id"] if regi else None, "uj": False}
        return {"ok": True, "id": r["id"], "uj": True}
    except Exception as e:  # noqa: BLE001 — a napló hibája ne vigye el a választ
        logger.warning("strategiai: az állítás nem rögzíthető: %s", e)
        return {"ok": False, "ok_szoveg": f"a napló nem írható ({type(e).__name__})"}


def rogzites_jegyzet(a: dict | None, r: dict | None) -> str | None:
    """A válasz alá: mi került a naplóba (vagy miért nem)."""
    if a is None:
        return None
    p = f" ({a['valoszinuseg']}%)" if a.get("valoszinuseg") else ""
    fej = f"📌 #{r['id']} naplóban" if r and r.get("ok") and r.get("id") else \
        f"📌 nincs naplózva: {(r or {}).get('ok_szoveg', '?')}"
    return f"{fej} — {a['hatarido']}-ig: {a['allitas'][:160]}{p}"


# ══ 5. A REGGELI NYITÓ ════════════════════════════════════════════════════════

NYITO_TERV_PROMPT = """Te Claus vagy, a Kommandant vezérkari főnöke. Reggel te kezdesz: ehhez most kiválasztod a nap \
HÁROM helyzetét az Echolot 12 nyelvű képéből — egy gazdaságit, egy geopolitikait és egy magyar belpolitikait.

A legfontosabb az, amit sok forrás és több nyelvterület visz, és aminek következménye van a Kommandantra (magyar \
olvasó, közgazdász, jogász, a gazdaság és a hatalom szerkezete érdekli). Bulvár, baleset, időjárás nem helyzet.

Csak a kapott adatból dolgozz. KIZÁRÓLAG JSON-t adj vissza, semmi mást:
{"helyzetek": [{"terulet": "gazdasag|geopolitika|belpolitika", "cim": "rövid magyar cím", \
"kereses_en": "2-4 angol kulcsszó (Echolot-kereséshez)", "kereses_hu": "2-4 magyar kulcsszó", \
"story_ids": ["..."], "miert": "egy mondat: miért ez a nap helyzete"}]}"""

NYITO_IRAS_FELADAT = """══ REGGELI NYITÓ ══
Most te kezdesz: a nap három helyzete, a kapott Echolot-adatból (lapszám, nyelvterületeken átívelő témák, regionális \
és szféra-keretezés, figyelemrés). Helyzetenként:
- helyzetkep: PONTOSAN 2 mondat — mi történt, és ki hogyan tálalja (melyik régió / szféra, ki hallgat);
- allaspont: PONTOSAN 1 mondat — a saját álláspontod indokkal, kertelés nélkül;
- kerdes: EGY kérdés a Kommandanthoz — az a döntés vagy információ, ami nélküle nem megy;
- allitas: egy ellenőrizhető állítás dátummal (a jövőben, legfeljebb egy éven belül) és valószínűséggel.
Számot csak a kapott adatból mondasz. Ha egy területhez nincs elég adat, azt a helyzetképben kimondod.
KIZÁRÓLAG JSON-t adj vissza, semmi mást:
{"helyzetek": [{"terulet": "gazdasag|geopolitika|belpolitika", "cim": "...", "helyzetkep": "...", "allaspont": "...", \
"kerdes": "...?", "allitas": {"hatarido": "ÉÉÉÉ-HH-NN", "szoveg": "mi történik", "valoszinuseg": 65}}]}"""


def _rovid(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def lapszam_tomor(v: dict | None) -> dict:
    """A `get_daily_edition` válaszából a modellnek kellő rész (top sztorik, átívelő témák, kilátás)."""
    if not isinstance(v, dict) or not isinstance(v.get("payload"), dict):
        return {}
    p = v["payload"]
    b = p.get("brief") if isinstance(p.get("brief"), dict) else {}
    return {
        "datum": v.get("date"), "nyelv": v.get("lang"),
        "top": [{"id": t.get("story_id"), "cim": _rovid(t.get("title"), 160), "forras": t.get("source_count"),
                 "szfera": t.get("sphere")} for t in (p.get("top_stories") or [])[:14] if isinstance(t, dict)],
        "fo_hir": _rovid(b.get("headline"), 200),
        "atfogo_temak": [{"id": t.get("story_id"), "cim": _rovid(t.get("title"), 160), "osszegzes":
                          _rovid(t.get("summary"), 220), "trend": t.get("trend")}
                         for t in (b.get("topics") or [])[:10] if isinstance(t, dict)],
        "kilatas": _rovid(b.get("outlook"), 500),
    }


def _hiba(v, hiba) -> str | None:
    if hiba:
        return str(hiba)[:200]
    if isinstance(v, dict) and v.get("error"):
        return str(v["error"])[:200]
    return None


async def _lapszam(hivo, nap: str, nyelv: str, hianyok: list) -> dict:
    v, hiba = await hivo("echolot", "get_daily_edition", {"date": nap, "lang": nyelv})
    if isinstance(v, dict) and v.get("error") == "no_snapshot" and v.get("latest_available"):
        v, hiba = await hivo("echolot", "get_daily_edition", {"date": v["latest_available"], "lang": nyelv})
    h = _hiba(v, hiba)
    if h:
        hianyok.append(f"lapszám ({nyelv}): {h}")
        return {}
    return lapszam_tomor(v)


async def nyito_alapanyag(hivo, nap: str) -> dict:
    """A 12 nyelvű kép a választáshoz: a lapszám (hu, en) és a magyar figyelemrés."""
    hianyok: list = []
    async with hivo.echolot_munkamenet():
        hu = await _lapszam(hivo, nap, "hu", hianyok)
        en = await _lapszam(hivo, nap, "en", hianyok)
        gap, hiba = await hivo("echolot", "attention_gap", {"geo": "hu", "lang": "hu", "limit": 8})
    h = _hiba(gap, hiba)
    if h:
        hianyok.append(f"attention_gap: {h}")
        gap = None
    from dontes_szimulator import tomorit
    return {"lapszam_hu": hu, "lapszam_en": en, "figyelemres_hu": tomorit(gap, lista_max=8, szoveg_max=200),
            "hianyok": hianyok}


async def nyito_keretezes(hivo, helyzetek: list) -> tuple[list, list]:
    """Helyzetenként a régiók (`regional_framing`) és a szférák (`frame_divergence`) keretezése, 3 napra."""
    from dontes_szimulator import tomorit
    out, hianyok = [], []
    async with hivo.echolot_munkamenet():
        for h in helyzetek:
            q_en = _rovid(h.get("kereses_en") or h.get("cim"), 80)
            q_hu = _rovid(h.get("kereses_hu") or q_en, 80)
            reg, hiba1 = await hivo("echolot", "regional_framing", {"query": q_en, "days": 3})
            szf, hiba2 = await hivo("echolot", "frame_divergence", {"query": q_hu, "days": 3})
            for nev, v, hb in (("regional_framing", reg, hiba1), ("frame_divergence", szf, hiba2)):
                if _hiba(v, hb):
                    hianyok.append(f"{nev} ({h.get('terulet')}): {_hiba(v, hb)}")
            out.append({"terulet": h.get("terulet"), "cim": h.get("cim"),
                        "regionalis": None if _hiba(reg, hiba1) else tomorit(reg, lista_max=8, szoveg_max=240),
                        "szferak": None if _hiba(szf, hiba2) else tomorit(szf, lista_max=10, szoveg_max=200)})
    return out, hianyok


_MONDAT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÁÉÍÓÖŐÚÜŰ„\"(0-9])")


def mondatok(szoveg: str, n: int) -> str:
    """Az első `n` mondat (a modell néha többet ír, mint kérték — a szerkezetet a gép tartja)."""
    s = " ".join(str(szoveg or "").split())
    return " ".join(_MONDAT.split(s)[:n]).strip()


def nyito_ellenoriz(adat: dict | None, ma: date) -> tuple[list, list]:
    """A modell JSON-ja → a szerkezet, amit a Kommandant lát: 2–3 helyzet, mindegyikben helyzetkép (≤2 mondat),
    álláspont (1 mondat), kérdés; az állítás csak ha ellenőrizhető. → (helyzetek, figyelmeztetések)"""
    figy: list = []
    nyers = (adat or {}).get("helyzetek") if isinstance(adat, dict) else None
    if not isinstance(nyers, list):
        return [], ["a nyitó nem volt értelmezhető JSON"]
    out = []
    for h in nyers[:3]:
        if not isinstance(h, dict):
            continue
        terulet = str(h.get("terulet") or "").strip().lower()
        kep, all_, kerd = mondatok(h.get("helyzetkep"), 2), mondatok(h.get("allaspont"), 1), _rovid(h.get("kerdes"), 300)
        if terulet not in TERULETEK or not kep or not all_ or not kerd:
            figy.append(f"hiányos helyzet kimaradt ({terulet or '?'})")
            continue
        if not kerd.endswith("?"):
            kerd = kerd.rstrip(".!") + "?"
        allitas = None
        a = h.get("allitas") if isinstance(h.get("allitas"), dict) else {}
        p = a.get("valoszinuseg")
        sor = f"Állítás: {a.get('hatarido')}-ig {_rovid(a.get('szoveg'), 400)}" + (f" ({int(p)}%)" if isinstance(
            p, (int, float)) else "")
        allitas = allitas_kivesz(sor, ma) if a else None
        if a and allitas is None:
            figy.append(f"{TERULETEK[terulet]}: az állítás nem ellenőrizhető (határidő: {a.get('hatarido')})")
        out.append({"terulet": terulet, "cim": _rovid(h.get("cim"), 120), "helyzetkep": kep, "allaspont": all_,
                    "kerdes": kerd, "allitas": allitas})
    if len(out) < 3:
        figy.append(f"csak {len(out)} helyzet állt össze a háromból")
    return out, figy


def nyito_szoveg(helyzetek: list, *, most: datetime, modell: str = "", html: bool = True,
                 megjegyzesek: list | None = None) -> str:
    """A Telegram (HTML) és az előzmény (sima szöveg) alakja ugyanabból."""
    from html import escape as e
    if not html:
        def e(x):  # noqa: F811 — sima szöveg
            return str(x)
    b = (lambda x: f"<b>{x}</b>") if html else (lambda x: x)
    i = (lambda x: f"<i>{x}</i>") if html else (lambda x: x)
    napok = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]
    bp = most.astimezone(BP)
    from cimzett import modell_rovid
    fej = (f"🎖 {b('REGGELI HELYZET')} · {bp:%Y-%m-%d}, {napok[bp.weekday()]}"
           + (f" · {e(modell_rovid(modell))}" if modell else ""))
    reszek = [fej]
    for n, h in enumerate(helyzetek, 1):
        sorok = [b(f"{n}. {TERULETEK[h['terulet']]} — {e(h['cim'])}"), e(h["helyzetkep"]),
                 f"{b('Álláspont:')} {e(h['allaspont'])}", f"{b('Kérdés:')} {e(h['kerdes'])}"]
        a = h.get("allitas")
        if a:
            p = f" ({a['valoszinuseg']}%)" if a.get("valoszinuseg") else ""
            azon = f"#{h['allitas_id']} " if h.get("allitas_id") else ""
            sorok.append(i(f"📌 {azon}{a['hatarido']}-ig: {e(a['allitas'])}{p}"))
        reszek.append("\n".join(sorok))
    lab = "Válaszolj ide — stratégiai módban folytatjuk. Forrás: Echolot-lapszám (hu, en), regionális és " \
          "szféra-keretezés (3 nap), figyelemrés."
    if megjegyzesek:
        lab += " " + "; ".join(dict.fromkeys(megjegyzesek)) + "."
    reszek.append(i(e(lab)))
    return "\n\n".join(reszek)


async def _llm_json(szamla, chat, system: str, user: str, purpose: str, max_tokens: int) -> tuple[dict | None, dict]:
    from nachtschicht import json_kivesz
    payload = {"model": SF_MODELL, "messages": [{"role": "system", "content": system},
                                                 {"role": "user", "content": user}], "max_tokens": max_tokens}
    data = await szamla.hiv(chat, payload, purpose)
    import llm_kapu
    return json_kivesz(llm_kapu.valasz_szoveg(data)), data


async def reggeli_nyito(*, telegram: Callable | None, chat: Callable | None = None, hivo=None,
                        most: datetime | None = None, n="alap", elozmeny: Callable | None = None,
                        tarol: Callable | None = None) -> dict:
    """Egy reggeli nyitó. `telegram(html)`: a küldés; `elozmeny(szoveg)`: a Feldwebel-beszélgetésbe (stratégiai
    jellel); `tarol(szoveg, modell)`: a kabin előzményébe. Sorrend: adat → választás (Opus) → keretezés → írás (Opus)
    → GÉPI szerkezet-ellenőrzés → napló (nyitó + állítások) → Telegram. Napló esetén csak sikeres naplózás után küld."""
    import llm_kapu
    import nachtschicht as ns
    from dontes_szimulator import Hivo
    most = most or datetime.now(timezone.utc)
    nap = most.astimezone(BP).date()
    n = naplo() if n == "alap" else n
    r: dict = {"nap": nap.isoformat(), "allapot": "indul", "hianyok": [], "figyelmeztetesek": []}
    if n is not None:
        try:
            if any(s["kulcs"] == nap.isoformat() for s in await asyncio.to_thread(n.sorok, "nyito")):
                r["allapot"] = "mar_ment"
                return r
        except Exception as e:  # noqa: BLE001
            r["figyelmeztetesek"].append(f"a napló nem olvasható ({type(e).__name__})")
    hivo = hivo or Hivo(echolot_koz_s=12.0, munkamenet_koz_s=7.0, echolot_max_s=300.0)
    chat = chat or llm_kapu.chat
    szamla = ns.Szamla(keret=REGGELI_KERET_USD)
    try:
        alap = await nyito_alapanyag(hivo, nap.isoformat())
        r["hianyok"] += alap.pop("hianyok")
        if not alap["lapszam_hu"] and not alap["lapszam_en"]:
            r["allapot"] = "nincs_adat"
            if telegram is not None:
                await telegram(f"🎖 <b>REGGELI HELYZET</b> — ma elmarad: az Echolot nem adott lapszámot "
                               f"({'; '.join(r['hianyok'])[:300]}). Adat nélkül nem értékelek.")
            return r
        terv, _ = await _llm_json(szamla, chat, NYITO_TERV_PROMPT, json.dumps(alap, ensure_ascii=False),
                                  CEL_NYITO_TERV, 8_000)
        valasztas = [h for h in ((terv or {}).get("helyzetek") or []) if isinstance(h, dict)][:3]
        if not valasztas:
            r["allapot"] = "hiba"
            r["figyelmeztetesek"].append("a választás nem volt értelmezhető")
            return r
        keretezes, hiany2 = await nyito_keretezes(hivo, valasztas)
        r["hianyok"] += hiany2
        anyag = {**alap, "valasztott_helyzetek": valasztas, "keretezes": keretezes, "hianyok": r["hianyok"],
                 "ma": nap.isoformat()}
        iras, data = await _llm_json(szamla, chat, prompt() + "\n\n" + NYITO_IRAS_FELADAT,
                                     json.dumps(anyag, ensure_ascii=False), CEL_NYITO_IRAS, 12_000)
    except ns.KeretElfogyott as e:
        r.update(allapot="keret", figyelmeztetesek=r["figyelmeztetesek"] + [str(e)])
        logger.warning("strategiai: a reggeli nyitó kerete elfogyott: %s", e)
        return r
    except ns.KapuHiba as e:
        r.update(allapot="hiba", figyelmeztetesek=r["figyelmeztetesek"] + [f"LLM-kapu: {e}"])
        logger.error("strategiai: reggeli nyitó, kapuhiba: %s", e)
        return r
    helyzetek, figy = nyito_ellenoriz(iras, nap)
    r["figyelmeztetesek"] += figy
    r["koltes_usd"] = round(szamla.usd, 4)
    modell = str((data or {}).get("model") or "")
    if not helyzetek:
        r["allapot"] = "hiba"
        return r
    nyito_payload = {"datum": nap.isoformat(), "modell": modell, "helyzetek": helyzetek, "hianyok": r["hianyok"],
                     "koltes_usd": r["koltes_usd"], "valasztas": valasztas}
    megj = []
    if n is not None:
        try:
            sor = await asyncio.to_thread(n.hozzafuz, "nyito", nap.isoformat(), nyito_payload)
        except Exception as e:  # noqa: BLE001
            sor = False
            logger.warning("strategiai: a nyitó nem naplózható: %s", e)
        if sor is None:                         # egy másik példány közben elküldte (deploy-átfedés)
            r["allapot"] = "mar_ment"
            return r
        if sor is False:
            megj.append("a napló most nem írható, az állítások nincsenek rögzítve")
        else:
            r["nyito_id"] = sor["id"]
            for h in helyzetek:
                if h.get("allitas"):
                    rr = await asyncio.to_thread(rogzit, h["allitas"], forras="reggeli", n=n, nyito=sor["id"],
                                                 terulet=h["terulet"], cim=h["cim"], modell=modell,
                                                 kereses=next((v.get("kereses_hu") for v in valasztas
                                                               if v.get("terulet") == h["terulet"]), None))
                    h["allitas_id"] = rr.get("id")
    else:
        megj.append("napló nélkül fut (nincs STRATEGIAI_PG_DSN), az állítások nincsenek rögzítve")
    if r["hianyok"]:
        megj.append(f"adathiány: {len(r['hianyok'])} lekérés nem jött meg")
    html = nyito_szoveg(helyzetek, most=most, modell=modell, megjegyzesek=megj)
    sima = nyito_szoveg(helyzetek, most=most, modell=modell, html=False, megjegyzesek=megj)
    if telegram is not None:
        await telegram(html)
    for fn, arg in ((elozmeny, (sima,)), (tarol, (sima, modell))):
        if fn is not None:
            try:
                fn(*arg)
            except Exception as e:  # noqa: BLE001 — az előzmény hibája ne vigye el a nyitót
                logger.warning("strategiai: a nyitó nem került az előzménybe: %s", e)
    r.update(allapot="kesz", helyzetek=helyzetek, szoveg=sima)
    logger.info("strategiai: reggeli nyitó kész — %d helyzet, $%.3f, hiány: %d", len(helyzetek), szamla.usd,
                len(r["hianyok"]))
    return r


# ══ 6. A HAVI PONTOZÁS ════════════════════════════════════════════════════════

BIRO_PROMPT = """Te a stratégiai napló bírája vagy. Minden állításról eldöntöd, beigazolódott-e a határidejéig, \
KIZÁRÓLAG a kapott bizonyítékból (az Echolot hírkorpuszának találatai, dátummal, forrással).
- igaz: a bizonyíték szerint a határidőig megtörtént;
- hamis: a bizonyíték szerint nem történt meg (vagy az ellenkezője történt);
- eldontetlen: a bizonyíték nem elég — ezt mondd, ne találgass.
KIZÁRÓLAG JSON-t adj vissza: {"iteletek": [{"id": 12, "itelet": "igaz|hamis|eldontetlen", "indok": "egy mondat", \
"forras": "a döntő találat címe és forrása, ha van"}]}"""

ITELETEK = ("igaz", "hamis", "eldontetlen")
_STOP = {"hogy", "mint", "vagy", "akkor", "lesz", "legalabb", "kozott", "szerint", "utan", "elott", "eleri", "marad",
         "tobb", "kevesebb", "fogja", "megtortenik", "bejelenti", "napig", "honap"}


def kereso_kifejezes(a: dict) -> str:
    """Az állítás keresőszavai (determinisztikus): a megadott `kereses`, különben a nagybetűs nevek és a hosszabb szavak."""
    if a.get("kereses"):
        return _rovid(a["kereses"], 80)
    szavak = re.findall(r"[A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9][\wÁÉÍÓÖŐÚÜŰáéíóöőúüű%-]+", a.get("allitas") or "")
    nagy = [s for s in szavak if s[0].isupper() or s.isupper()]
    tobbi = sorted((s for s in szavak if s not in nagy and len(s) >= 6 and _ekezet_nelkul(s) not in _STOP),
                   key=len, reverse=True)
    return " ".join(list(dict.fromkeys(nagy + tobbi))[:6])


def ervenyes_iteletek(sorok: list) -> dict:
    """Állítás-id → az érvényes ítélet: a Kommandant helyesbítése (a legutolsó) felülírja a bíróét."""
    out: dict = {}
    for s in sorted(sorok, key=lambda s: s["id"]):
        p = s["payload"]
        if s["tipus"] in ("pontozas", "helyesbites") and p.get("allitas_id") is not None:
            out[int(p["allitas_id"])] = {**p, "tipus": s["tipus"], "sor_id": s["id"]}
    return out


def lejart(n, ma: date) -> list:
    """A lejárt, még pontozatlan állítások (a határidő elmúlt, nincs ítéletük)."""
    sorok = n.sorok()
    van = ervenyes_iteletek(sorok)
    return [{"id": s["id"], **s["payload"]} for s in sorok
            if s["tipus"] == "allitas" and s["payload"].get("hatarido", "9999") < ma.isoformat() and s["id"] not in van]


def brier(p: int | None, itelet: str) -> float | None:
    if p is None or itelet not in ("igaz", "hamis"):
        return None
    return round((p / 100 - (1.0 if itelet == "igaz" else 0.0)) ** 2, 4)


def osszesito(n) -> dict:
    """Az összes érvényes ítélet: darabszám, Brier (ahol volt valószínűség), irány-találat, a legnagyobb tévedések."""
    sorok = n.sorok()
    allitasok = {s["id"]: s["payload"] for s in sorok if s["tipus"] == "allitas"}
    db = {k: 0 for k in ITELETEK}
    bs, talalat, rossz = [], 0, []
    for aid, i in ervenyes_iteletek(sorok).items():
        a = allitasok.get(aid)
        if a is None or i.get("itelet") not in ITELETEK:
            continue
        db[i["itelet"]] += 1
        b = brier(a.get("valoszinuseg"), i["itelet"])
        if b is not None:
            bs.append(b)
            talalat += (a["valoszinuseg"] >= 50) == (i["itelet"] == "igaz")
            if b >= 0.36:
                rossz.append((b, aid, a["valoszinuseg"], i["itelet"]))
    rossz.sort(reverse=True)
    return {"db": db, "n_brier": len(bs), "brier": round(sum(bs) / len(bs), 4) if bs else None,
            "talalat": talalat, "legrosszabb": [{"id": aid, "p": p, "itelet": it} for _, aid, p, it in rossz[:3]]}


def havi_szoveg(honap: str, o: dict, *, pontozott: int, kimaradt: int = 0) -> str:
    db = o["db"]
    sorok = [f"📊 <b>HAVI SZÁMADÁS</b> · {honap}",
             f"Most pontozott lejárt állítás: {pontozott} — összesen igaz {db.get('igaz', 0)}, hamis "
             f"{db.get('hamis', 0)}, eldöntetlen {db.get('eldontetlen', 0)}."]
    if o["brier"] is not None:
        sorok.append(f"Brier: {o['brier']:.3f}".replace(".", ",") + f" (n={o['n_brier']}; a pénzfeldobás 0,250) · "
                     f"irány-találat: {o['talalat']}/{o['n_brier']}.")
    if o["legrosszabb"]:
        sorok.append("Legnagyobb tévedés: " + ", ".join(f"#{x['id']} ({x['p']}% → {x['itelet']})"
                                                        for x in o["legrosszabb"]) + ".")
    if kimaradt:
        sorok.append(f"{kimaradt} állítás a havi keret miatt a következő körre maradt.")
    sorok.append("<i>Az ítélet a bíró javaslata. Ha rossz: „#31 igaz” (vagy hamis / eldöntetlen) — a te szavad "
                 "az érvényes.</i>")
    return "\n".join(sorok)


async def bizonyitek(hivo, a: dict, ma: date) -> dict:
    """Egy állítás bizonyítéka az Echolot korpuszából: a rögzítéstől a határidő utánig."""
    from dontes_szimulator import tomorit
    rog = _ido(a.get("rogzitve_bp")) or datetime.now(timezone.utc) - timedelta(days=30)
    napok = max(3, min(90, (ma - rog.astimezone(BP).date()).days + 1))
    v, hiba = await hivo("echolot", "search_news", {"query": kereso_kifejezes(a), "days": napok, "limit": 12})
    return {"id": a["id"], "allitas": a.get("allitas"), "hatarido": a.get("hatarido"),
            "valoszinuseg": a.get("valoszinuseg"),
            "talalatok": None if _hiba(v, hiba) else tomorit(v, lista_max=12, szoveg_max=260),
            "hiany": _hiba(v, hiba)}


async def havi_kor(*, telegram: Callable | None, chat: Callable | None = None, hivo=None, n="alap",
                   most: datetime | None = None) -> dict:
    """A lejárt állítások pontozása (csomagokban, saját kerettel), utána a havi összesítő a Telegramon."""
    import llm_kapu
    import nachtschicht as ns
    from dontes_szimulator import Hivo
    most = most or datetime.now(timezone.utc)
    ma = most.astimezone(BP).date()
    honap = f"{ma:%Y-%m}"
    n = naplo() if n == "alap" else n
    if n is None:
        return {"allapot": "nincs_naplo"}
    if any(s["kulcs"] == honap for s in await asyncio.to_thread(n.sorok, "havi_kor")):
        return {"allapot": "mar_ment"}
    hivo = hivo or Hivo(echolot_koz_s=12.0, munkamenet_koz_s=7.0, echolot_max_s=900.0)
    chat = chat or llm_kapu.chat
    szamla = ns.Szamla(keret=HAVI_KOR_KERET_USD)
    jeloltek = await asyncio.to_thread(lejart, n, ma)
    pontozott, kimaradt = 0, 0
    for i in range(0, len(jeloltek), HAVI_KOR_CSOMAG):
        csomag = jeloltek[i:i + HAVI_KOR_CSOMAG]
        async with hivo.echolot_munkamenet():
            bizonyitekok = [await bizonyitek(hivo, a, ma) for a in csomag]
        try:
            ad, _ = await _llm_json(szamla, chat, BIRO_PROMPT, json.dumps({"ma": ma.isoformat(), "allitasok":
                                    bizonyitekok}, ensure_ascii=False), CEL_PONTOZAS, 8_000)
        except (ns.KeretElfogyott, ns.KapuHiba) as e:
            kimaradt = len(jeloltek) - i
            logger.warning("strategiai: a havi kör megállt: %s", e)
            break
        ervenyes = {a["id"] for a in csomag}
        for it in (ad or {}).get("iteletek") or []:
            if not isinstance(it, dict):
                continue
            try:
                aid = int(it.get("id"))
            except (TypeError, ValueError):
                continue
            itelet = str(it.get("itelet") or "").strip().lower()
            if aid not in ervenyes or itelet not in ITELETEK:
                continue
            if await asyncio.to_thread(n.hozzafuz, "pontozas", str(aid), {
                    "allitas_id": aid, "itelet": itelet, "indok": _rovid(it.get("indok"), 400),
                    "forras": _rovid(it.get("forras"), 300), "honap": honap, "biro": "opus"}):
                pontozott += 1
    o = await asyncio.to_thread(osszesito, n)
    await asyncio.to_thread(n.hozzafuz, "havi_kor", honap, {"honap": honap, "pontozott": pontozott,
                                                            "kimaradt": kimaradt, "osszesito": o,
                                                            "koltes_usd": round(szamla.usd, 4)})
    if telegram is not None and (pontozott or kimaradt):
        await telegram(havi_szoveg(honap, o, pontozott=pontozott, kimaradt=kimaradt))
    return {"allapot": "kesz", "pontozott": pontozott, "kimaradt": kimaradt, "osszesito": o}


_HELYESBITES = re.compile(r"^\W*(?:a\s+)?#\s?(\d+)(?:\s*-?\s*(?:es|as|os|ös|ás|és))?\s*(?:allitas\s*)?(?:az?\s+)?"
                          r"(igaz|hamis|eldontetlen)\b")


def helyesbites(szoveg: str, n="alap") -> str | None:
    """„#31 igaz” — a Kommandant ítélete egy állításról (LLM nélkül). None, ha az üzenet nem ilyen."""
    m = _HELYESBITES.match(_ekezet_nelkul(szoveg or "").strip())
    if not m:
        return None
    n = naplo() if n == "alap" else n
    if n is None:
        return "A stratégiai napló nem él (nincs STRATEGIAI_PG_DSN), ezért az ítéletet nem tudom rögzíteni."
    aid, itelet = int(m[1]), m[2]
    try:
        sorok = n.sorok()
        a = next((s for s in sorok if s["tipus"] == "allitas" and s["id"] == aid), None)
        if a is None:
            return f"Nincs #{aid} állítás a stratégiai naplóban."
        elozo = ervenyes_iteletek(sorok).get(aid)
        n.hozzafuz("helyesbites", f"{aid}:{datetime.now(timezone.utc).isoformat()}",
                   {"allitas_id": aid, "itelet": itelet, "ki": "kommandant"})
    except Exception as e:  # noqa: BLE001
        return f"A napló most nem írható ({type(e).__name__}), az ítéletet nem rögzítettem."
    volt = f" (eddig: {elozo['itelet']})" if elozo else ""
    return f"Rögzítve: #{aid} → {itelet}{volt}. „{a['payload'].get('allitas', '')[:140]}”"


# ══ 7. ÜTEMEZÉS ═══════════════════════════════════════════════════════════════

_UTOLSO: dict = {}
_FUTO: set = set()


def _hatter(coro) -> None:
    t = asyncio.get_running_loop().create_task(coro)
    _FUTO.add(t)
    t.add_done_callback(_FUTO.discard)


def cron_lepes(most_bp: datetime, *, telegram: Callable | None, chat: Callable | None = None,
               elozmeny: Callable | None = None, tarol: Callable | None = None) -> list:
    """A cron-szál percenkénti hívása. Reggel (`reggeli_ido()`–10:00, Budapest) egyszer a nyitó (a `chat` a Bridge
    kapuja: Anthropic, kiesésnél kimondott SiliconFlow-tartalék — a nyitó nyilvános Echolot-adatból dolgozik); minden
    hónap 1-jén 08:00 után egyszer a havi pontozás (csak Claude). Mindkettő háttérfeladat; a napi egyszeriséget a napló őrzi (újraindulás után is),
    a folyamaton belül a `_UTOLSO`. Sosem dob."""
    indult = []
    try:
        nap = most_bp.date().isoformat()
        hm = (most_bp.hour, most_bp.minute)
        if (bekapcsolva() and reggeli_bekapcsolva() and reggeli_ido() <= hm < REGGELI_ABLAK_VEGE
                and _UTOLSO.get("nyito") != nap):
            _UTOLSO["nyito"] = nap
            _hatter(reggeli_nyito(telegram=telegram, chat=chat, elozmeny=elozmeny, tarol=tarol))
            indult.append("nyito")
        honap = most_bp.strftime("%Y-%m")
        if bekapcsolva() and most_bp.day == 1 and most_bp.hour >= HAVI_KOR_ORA and _UTOLSO.get("havi") != honap:
            _UTOLSO["havi"] = honap
            _hatter(havi_kor(telegram=telegram))      # a bíró CSAK Claude (az állítás tudásbázisból is jöhet)
            indult.append("havi")
    except Exception as e:  # noqa: BLE001 — az ütemező hibája ne állítsa meg a cront
        logger.error("strategiai: ütemezési hiba: %s", e)
    return indult
