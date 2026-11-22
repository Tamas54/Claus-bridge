"""DÖNTÉS-SZIMULÁTOR (2026-11-22): „Próbáld ki a döntést a világon, mielőtt meghozod.”

A Kommandant jóváhagyta („igen, ez mindkettő jó!”). Ő leír egy lépést (törvényjavaslat, árazás, nyilatkozat,
befektetés, kampány); a Bridge egyszerre lefuttatja a négy SAJÁT motorján, és EGY oldalt ad arról, mi történne.

A NÉGY MOTOR (mind a sajátunk, új motort nem építünk)
----------------------------------------------------
1. TÁRSADALOM — a DELPHOI szintetikus fókuszcsoportja (`plugins/delphoi.py`: create_job → process_job): kvótás
   persona-panel (kor, település, iskola, média-étrend), a saját SiliconFlow-motorján (Hy3, NULLTARIF), SSR-skálán.
   Relatív jel, NEM közvélemény-kutatás. A panel a lépés szövegét kapja, semmi mást.
2. SAJTÓ, az „Elővisszhang” — az Echolot MCP (search_news, source_profile, get_news, frame_divergence,
   regional_framing): mit írna holnap a Telex, a 444, a Magyar Nemzet, az Index, a Spiegel, a BBC és a Global Times,
   a korpuszban tárolt VALÓDI korábbi cikkeik és forrásprofiljuk alapján. A címet és a leadet Sonnet 5.5 írja.
3. PIAC ÉS MAKRÓ — a Makronóm (StatData) MCP: get_macro_indicator, forecast (forgatókönyvekkel), forecast_path
   (80%-os sávval), yfinance. Szám csak forrással; ami nem modellezhető, azt kimondja.
4. JOG — a Lexonomics MCP: provision (hatályos szöveg, NJT-link, állapotdátum), search, döntések (scope=decisions).

A végén összegzés, kockázati térkép és módosítási javaslat (egy Sonnet 5.5-lépés; az Opus 11-22-én kimaradt), és mérhető
előrejelzések. Minden előrejelzés a DELPHOI hash-láncolt, append-only predikció-naplójába kerül
(`delphoi_nowcast_ledger`, `append_ledger_row`): így később lemérhető, mennyire talált — önkalibráló szimulátor.

A NAPLÓ ÉS A NYOM (új tábla NINCS — házi szabály)
------------------------------------------------
* Előrejelzésenként EGY sor a DELPHOI meglévő láncában: entity_key=`szim:<futás>:<nn>`, target_window = a határidő,
  direction = a valószínűség (0–1, Brier-pontozáshoz), corpus_hash = az előrejelzés kanonikus JSON-jának SHA-256-ja
  (commit), model_id = `dontes-szimulator-v1|<modell>`. A lépés szövege NEM kerül a láncba (a lánc feje nyilvános).
* A futás nyoma a meglévő `messages` táblában: indulás, teljes adat (az előrejelzések szövegével — ebből számolható
  újra a hash), és az egyoldalas eredmény a Kommandantnak. `naplo_ellenorzes()` összeveti a kettőt.

LLM: KIZÁRÓLAG az Anthropic API az LLM-kapun át (`llm_kapu.chat`) — „az apit használjuk: azt tudjuk ellenőrizni”.
Futásonkénti, kikényszerített költségkeret (`SZIMULATOR_MAX_USD`, alap 1,5 $) a kapu VALÓS költéséből: minden lépés
előtt a legrosszabb esetre foglalunk; ami nem fér bele, kimarad, és a kimenet ezt kimondja (részeredmény).

ŐSZINTESÉG: ez szimuláció, nem jóslat. Minden szám és jogszabályhely forrással; amit egy motor nem adott, azt nem
állítjuk. A szimulált sajtócímek NEM valódi cikkek, és valódi személynek nem adunk szájába idézetet.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import html
import json
import logging
import os
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone

import kulso_mcp
import llm_kapu

logger = logging.getLogger("dontes_szimulator")

VERZIO = "dontes-szimulator-v2"
FELADO = "dontes-szimulator"
ESZKOZ_NEV = "dontes_szimulator"
ALAP_HORIZONT = "12 hónap"
LEPES_MIN_KAR, LEPES_MAX_KAR = 15, 4_000
FUTAS_MINTA = re.compile(r"^sz-[0-9a-f]{8}$")

#: A lapok, amelyeknek „holnapi” cikkét szimuláljuk (csak „teljes” módban). `lean`: rögzített beállítottság, amely
#: felülírja az Echolot címkéjét. A Kommandant (11-22): „A médiatérkép elavult: a közmédia mára tisza párti lett.” Az
#: Echolot `gov` címkéje a 2026 előtti (Fidesz-kormány alatti) besorolás öröksége; a közmédia a mindenkori kormányt
#: támogatja. A közmédia nincs az Echolot-korpuszban (11-22: a „hirado” forrás üres), ezért stílusminta nélkül megy.
LAPOK = (
    {"kulcs": "telex", "nev": "Telex", "nyelv": "hu", "forras": "Telex"},
    {"kulcs": "444", "nev": "444", "nyelv": "hu", "forras": "444"},
    {"kulcs": "magyarnemzet", "nev": "Magyar Nemzet", "nyelv": "hu", "forras": "Magyar Nemzet"},
    {"kulcs": "index", "nev": "Index", "nyelv": "hu", "forras": "Index"},
    {"kulcs": "kozmedia", "nev": "Közmédia (M1, hirado.hu)", "nyelv": "hu", "forras": None,
     "lean": "közmédia: a mindenkori kormányt támogatja — 2026 tavasza óta a Tisza-kormányt"},
    {"kulcs": "spiegel", "nev": "Der Spiegel", "nyelv": "de", "forras": "Spiegel"},
    {"kulcs": "bbc", "nev": "BBC", "nyelv": "en", "forras": "BBC"},
    {"kulcs": "globaltimes", "nev": "Global Times", "nyelv": "en", "forras": "Global Times"},
)

#: A Makronóm `get_macro_indicator` mutatói, amelyeket a terv kérhet (a lista a szerver leírásából).
MAKRO_MUTATOK = ("cpi", "hicp", "core_cpi", "services_cpi", "energy_cpi", "food_cpi", "policy_rate",
                 "unemployment", "gdp_growth", "ppi")
ELOREJELZES_MUTATOK = ("inflation", "gdp", "unemployment")
ALAP_MAKRO = (("cpi",), ("policy_rate",), ("gdp_growth",), ("unemployment",))
ALAP_ELOREJELZES = ("inflation", "gdp", "unemployment")
ALAP_SZIMBOLUM = {"HU": "EURHUF=X"}
SZIMBOLUM_MINTA = re.compile(r"^[A-Z0-9^=.\-]{1,15}$")
ORSZAG_MINTA = re.compile(r"^[A-Z]{2}$")

MAX_MAKRO, MAX_ELOREJELZES_MUTATO, MAX_SZIMBOLUM = 6, 3, 4
MAX_HIVATKOZAS, MAX_KIFEJEZES, MAX_DONTES_KERESES = 5, 3, 2
MAX_ELOREJELZES_MOTORONKENT = 3
MCP_HIVAS_TIMEOUT_S = 150

#: Az egyes lépések kért kimeneti keretei (token). A kapu ennél többet is engedhet (`llm_kapu.KIMENET_ALSO`), ezért a
#: költségfoglalás a NAGYOBBIKKAL számol: a keret a legrosszabb esetre is tart.
KIMENET = {"terv": 2_000, "sajto": 5_000, "piac": 4_000, "jog": 4_000, "osszegzes": 5_000}
#: A lépések célja (`purpose`) — ez dönti el a kapuban a szintet: minden Sonnet 5.5 („közép”). A külön Opus-lépés
#: (kockázati térkép) 11-22-én kimaradt — a Kommandant: „túl drága”; a kockázati térképet az összegző írja.
CELOK = {"terv": "szimulator/terv", "sajto": "szimulator/sajto", "piac": "szimulator/piac", "jog": "szimulator/jog",
         "osszegzes": "szimulator/osszegzes"}

#: A gazdasági alapvonal mutatóinak neve és egysége.
GAZDASAGI_NEV = {"inflation": ("infláció (éves, %)", "%"), "gdp": ("GDP-növekedés (éves, %)", "%"),
                 "unemployment": ("munkanélküliségi ráta (%)", "%"), "policy_rate": ("jegybanki alapkamat (%)", "%"),
                 "EURHUF=X": ("EUR/HUF árfolyam", "Ft"), "USDHUF=X": ("USD/HUF árfolyam", "Ft")}
#: Közlési késés (nap az időszak vége után), konzervatívan: a horizont végéig ennél korábbi időszak adata jelenik meg
#: (KSH-infláció ~8–10 nap, munkanélküliség ~3–4 hét, GDP y/y SA ~45–60 nap).
KOZLESI_KESES_NAP = {"M": 45, "Q": 75}
#: Ellenőrzési forrás mutatónként (a határidő után ezzel mérjük, talált-e az előrejelzés).
GAZDASAGI_ELLENORZES = {"inflation": "KSH/Eurostat — Makronóm get_macro_indicator({c}, cpi, period={p})",
                        "gdp": "Eurostat namq_10_gdp (y/y, SA) — Makronóm forecast_path({c}, gdp) last_actual",
                        "unemployment": "KSH/Eurostat — Makronóm get_macro_indicator({c}, unemployment, period={p})",
                        "policy_rate": "MNB — Makronóm get_macro_indicator({c}, policy_rate, period={p})",
                        "fx": "Yahoo/MNB — Makronóm yfinance({s}, history) az adott napon"}


def max_usd() -> float:
    try:
        return max(0.05, float(os.environ.get("SZIMULATOR_MAX_USD", "") or 1.5))
    except ValueError:
        return 1.5


def _env_int(nev: str, alap: int) -> int:
    try:
        return int(os.environ.get(nev, "") or alap)
    except ValueError:
        return alap


def _env_float(nev: str, alap: float) -> float:
    try:
        return float(os.environ.get(nev, "") or alap)
    except ValueError:
        return alap


def _most() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ══ KÖLTSÉGKERET ══════════════════════════════════════════════════════════

class Keret:
    """Futásonkénti, kikényszerített költségkeret. Minden LLM-lépés előtt FOGLAL (a legrosszabb esetre), utána a kapu
    valós költésével KÖNYVEL. Ami nem fér bele, nem fut: kimarad, és a kimenet kimondja."""

    def __init__(self, max_usd_: float):
        self.max_usd = float(max_usd_)
        self.koltott = 0.0
        self.foglalt = 0.0
        self.lepesek: dict[str, float] = {}
        self.kimaradt: list[str] = []

    def foglal(self, becsles: float, lepes: str) -> bool:
        if self.koltott + self.foglalt + becsles > self.max_usd:
            self.kimaradt.append(lepes)
            return False
        self.foglalt += becsles
        return True

    def lezar(self, becsles: float, valos: float, lepes: str) -> None:
        self.foglalt = max(0.0, self.foglalt - becsles)
        self.koltott += float(valos or 0.0)
        self.lepesek[lepes] = round(self.lepesek.get(lepes, 0.0) + float(valos or 0.0), 6)

    def allapot(self) -> dict:
        return {"usd": round(self.koltott, 4), "max_usd": self.max_usd, "lepesek": dict(self.lepesek),
                "kimaradt": list(self.kimaradt)}


def becsles_usd(szint: str, bemenet_kar: int, max_ki: int) -> float:
    """Egy hívás legrosszabb ára: a bemenet (~3 karakter/token, a magyar szöveg tokenigényes) + a kapu tényleges
    kimeneti plafonja (a kért keret, de legalább `llm_kapu.KIMENET_ALSO`)."""
    modell = llm_kapu.SZINTEK[szint]["modell"]
    ar = llm_kapu.ARAK.get(modell, llm_kapu.ISMERETLEN_AR)
    ki = max(int(max_ki), llm_kapu.KIMENET_ALSO)
    return (bemenet_kar / 3.0 * ar[0] + ki * ar[1]) / 1_000_000


# ══ SEGÉDEK: JSON, TÖMÖRÍTÉS ═════════════════════════════════════════════

def json_ki(szoveg: str) -> dict | None:
    """A modell válaszából az EGY JSON-objektum (kódblokk, előtte-utána szöveg és záró vessző tűrve)."""
    s = (szoveg or "").strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    darab = s[i:j + 1]
    for jelolt in (darab, re.sub(r",\s*([}\]])", r"\1", darab)):
        try:
            v = json.loads(jelolt)
        except ValueError:
            continue
        return v if isinstance(v, dict) else None
    return None


def mcp_json(szoveg: str):
    try:
        return json.loads(szoveg)
    except (TypeError, ValueError):
        return {"szoveg": (szoveg or "")[:3000]}


#: Az MCP-válaszokból a modellnek felesleges (audit-, gépi és ismétlő) mezők.
ELDOBANDO = frozenset({"all_attempts", "agent_instruction", "imf_all_years", "fallback_chain", "_machine",
                       "attribution_note", "answer_lead", "citable", "attribution", "diversity", "history",
                       "indexed_sources", "frame_taxonomy", "weight_schedule", "anchors", "inputs", "clamped_params",
                       "notes", "also", "eli_url", "query"})


def tomorit(v, *, lista_max: int = 8, szoveg_max: int = 700, melyseg: int = 0):
    if melyseg > 6:
        return "…"
    if isinstance(v, dict):
        return {k: tomorit(x, lista_max=lista_max, szoveg_max=szoveg_max, melyseg=melyseg + 1)
                for k, x in v.items() if k not in ELDOBANDO}
    if isinstance(v, list):
        elemek = [tomorit(x, lista_max=lista_max, szoveg_max=szoveg_max, melyseg=melyseg + 1) for x in v[:lista_max]]
        if len(v) > lista_max:
            elemek.append(f"…(+{len(v) - lista_max})")
        return elemek
    if isinstance(v, str) and len(v) > szoveg_max:
        return v[:szoveg_max] + "…"
    return v


def _hiba_e(v) -> str | None:
    """Az MCP-hívás hibát adott-e (a kulso_mcp `{"hiba"}`-ja, vagy a szerver saját `error`-ja)."""
    if isinstance(v, dict):
        if v.get("hiba"):
            h = str(v["hiba"])
            try:                                  # a szerver saját JSON-hibája a kulso_mcp hibaszövegében
                belso = json.loads(h)
                h = str(belso.get("error") or h) if isinstance(belso, dict) else h
            except ValueError:
                pass
            return h.split("\n", 1)[0][:300]
        if v.get("error") and len(v) <= 3:
            return str(v["error"])[:300]
    return None


def _json_dump(v, max_kar: int) -> str:
    s = json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= max_kar else s[:max_kar] + "…(levágva)"


# ══ HORIZONT ══════════════════════════════════════════════════════════════

def horizont_napok(horizont: str, ma: date | None = None) -> int:
    """„12 hónap” → 365, „2 év” → 730, „6 hét” → 42, „2030” → az év végéig. Alap 365, legfeljebb 10 év."""
    ma = ma or datetime.now(timezone.utc).date()
    s = (horizont or "").lower()
    napok = None
    for minta, szorzo in ((r"(\d+)\s*(?:év|ev|year)", 365), (r"(\d+)\s*(?:hónap|honap|hó|month)", 30.44),
                          (r"(\d+)\s*(?:hét|het|week)", 7), (r"(\d+)\s*(?:nap|day)", 1)):
        m = re.search(minta, s)
        if m:
            napok = int(round(int(m.group(1)) * szorzo))
            break
    if napok is None:
        m = re.search(r"\b(20\d\d)\b", s)
        if m:
            napok = (date(int(m.group(1)), 12, 31) - ma).days
    if not napok or napok < 7:
        napok = 365
    return min(napok, 3653)


# ══ ELŐREJELZÉSEK: tisztítás, hash, napló ═════════════════════════════════

TERULETEK = ("tarsadalom", "sajto", "piac", "jog", "osszesitett")


def elorejelzesek_tisztit(nyers, terulet: str, *, ma: date, hatar_napok: int,
                          max_db: int = MAX_ELOREJELZES_MOTORONKENT) -> tuple[list[dict], list[str]]:
    """A modell előrejelzéseiből a MÉRHETŐEK: állítás, metrika, határidő (jövőbeli, a horizonton belül), valószínűség
    (0–1), ellenőrzési forrás. Ami nem ilyen, kimarad — a kimaradás oka visszajön (nincs néma szűrés)."""
    jo, kimaradt = [], []
    if not isinstance(nyers, list):
        return jo, kimaradt
    for e in nyers[:max_db]:
        if not isinstance(e, dict):
            continue
        allitas = " ".join(str(e.get("allitas") or "").split())
        hatarido = str(e.get("hatarido") or "").strip()[:10]
        ellenorzes = " ".join(str(e.get("ellenorzes") or "").split())
        try:
            p = float(e.get("valoszinuseg"))
        except (TypeError, ValueError):
            p = None
        ok = None
        if not allitas:
            ok = "nincs állítás"
        elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", hatarido):
            ok = "a határidő nem ÉÉÉÉ-HH-NN"
        elif p is None or not 0.0 <= p <= 1.0:
            ok = "a valószínűség nem 0 és 1 közötti szám"
        elif not ellenorzes:
            ok = "nincs megadva, hol ellenőrizhető"
        else:
            try:
                d = date.fromisoformat(hatarido)
            except ValueError:
                d = None
            if d is None:
                ok = "érvénytelen dátum"
            elif d <= ma:
                ok = "a határidő nem a jövőben van"
            elif (d - ma).days > hatar_napok + 31:
                ok = "a határidő a horizonton túl van"
        if ok:
            kimaradt.append(f"{terulet}: „{allitas[:80]}” — {ok}")
            continue
        feltetel = str(e.get("feltetel") or "").strip().lower()
        jo.append({"terulet": terulet, "allitas": allitas[:400],
                   "feltetel": feltetel if feltetel in ("lepes", "alap") else "lepes",
                   "metrika": " ".join(str(e.get("metrika") or "").split())[:200],
                   "kuszob": " ".join(str(e.get("kuszob") or "").split())[:120],
                   "hatarido": hatarido, "valoszinuseg": round(min(0.99, max(0.01, p)), 3),
                   "ellenorzes": ellenorzes[:300],
                   "alap": " ".join(str(e.get("alap") or "").split())[:300]})
    return jo, kimaradt


def elorejelzes_kanon(e: dict, futas: str, sorszam: int, lepes_hash: str) -> str:
    """Az előrejelzés kanonikus alakja — ennek a SHA-256-ja megy a láncba (corpus_hash). Bármely mező utólagos
    módosítása más hash-t ad, így a napló és a messages-beli szöveg összevethető."""
    return json.dumps({"futas": futas, "sorszam": sorszam, "terulet": e["terulet"], "allitas": e["allitas"],
                       "metrika": e.get("metrika", ""), "kuszob": e.get("kuszob", ""), "hatarido": e["hatarido"],
                       "valoszinuseg": e["valoszinuseg"], "feltetel": e.get("feltetel", "lepes"),
                       "ellenorzes": e["ellenorzes"], "lepes_hash": lepes_hash,
                       "verzio": VERZIO}, ensure_ascii=False, sort_keys=True)


def naploba(get_db, elorejelzesek: list[dict], *, futas: str, orszag: str, lepes_hash: str) -> list[dict]:
    """Az előrejelzések a DELPHOI hash-láncolt, append-only predikció-naplójába — a MEGLÉVŐ úton
    (`delphoi.append_ledger_row`: a predicted_at a szerver órája, a prev_hash a lánc feje)."""
    from plugins import delphoi
    conn = get_db()
    try:
        delphoi.ensure_tables(conn)          # idempotens: a DELPHOI saját sémája (élesben már megvan)
    finally:
        conn.close()
    for i, e in enumerate(elorejelzesek, 1):
        kanon = elorejelzes_kanon(e, futas, i, lepes_hash)
        e["sorszam"] = i
        e["hash"] = _sha(kanon)
        sor = delphoi.append_ledger_row(
            get_db, entity_key=f"szim:{futas}:{i:02d}", country=orszag, target_window=e["hatarido"],
            direction=e["valoszinuseg"], corpus_hash=e["hash"],
            model_id=f"{VERZIO}|{e.get('modell') or '?'}",
            segment_json=json.dumps({"tipus": "dontes_szimulator", "futas": futas, "sorszam": i,
                                     "terulet": e["terulet"]}, ensure_ascii=False))
        e["naplo"] = {"entity_key": sor["entity_key"], "predicted_at": sor["predicted_at"],
                      "content_hash": sor["content_hash"], "prev_hash": sor["prev_hash"]}
    return elorejelzesek


# ══ A FUTÁS NYOMA (a meglévő messages tábla) ══════════════════════════════

def _nyom(get_db, futas: str, allapot: str, uzenet: str, *, cimzett: str = FELADO, thread: int | None = None,
          status: str = "read") -> int:
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO messages (timestamp, sender, recipient, subject, message, priority, thread_id, reply_to, "
            "status) VALUES (?, ?, ?, ?, ?, 'normal', ?, ?, ?)",
            (_most(), FELADO, cimzett, f"szimulator:{futas} · {allapot}", uzenet, thread, thread, status))
        mid = cur.lastrowid
        if thread is None:
            conn.execute("UPDATE messages SET thread_id = ? WHERE id = ?", (mid, mid))
        conn.commit()
        return mid
    finally:
        conn.close()


def _sorok(get_db, futas: str) -> list[dict]:
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, timestamp, recipient, subject, message FROM messages WHERE sender = ? AND subject LIKE ? "
            "ORDER BY id", (FELADO, f"szimulator:{futas} ·%")).fetchall()]
    finally:
        conn.close()


def betolt(get_db, futas: str) -> dict | None:
    """A futás teljes (JSON) eredménye a messages táblából, vagy None."""
    if not FUTAS_MINTA.match(futas or ""):
        return None
    for r in reversed(_sorok(get_db, futas)):
        if r["subject"].endswith("· adat"):
            try:
                return json.loads(r["message"])
            except ValueError:
                return None
    return None


def naplo_ellenorzes(get_db, futas: str) -> dict:
    """Az utólagos ellenőrzés: (1) a DELPHOI-lánc ép-e; (2) a messages-ben tárolt előrejelzések kanonikus hash-e
    egyezik-e a lánc soraival. Bármelyik oldal utólagos átírása itt kiderül."""
    from plugins import delphoi
    adat = betolt(get_db, futas)
    if adat is None:
        return {"ok": False, "hiba": "nincs ilyen befejezett futás"}
    lanc = delphoi.verify_ledger_chain(get_db)
    conn = get_db()
    try:
        sorok = {r["entity_key"]: dict(r) for r in conn.execute(
            "SELECT entity_key, corpus_hash, direction, target_window FROM delphoi_nowcast_ledger "
            "WHERE entity_key LIKE ?", (f"szim:{futas}:%",)).fetchall()}
    finally:
        conn.close()
    egyezik, elter = 0, []
    for e in adat.get("elorejelzesek") or []:
        kulcs = (e.get("naplo") or {}).get("entity_key")
        sor = sorok.get(kulcs)
        h = _sha(elorejelzes_kanon(e, futas, e.get("sorszam", 0), adat.get("lepes_hash", "")))
        if sor and sor["corpus_hash"] == h and abs(float(sor["direction"]) - e["valoszinuseg"]) < 1e-9 \
                and sor["target_window"] == e["hatarido"]:
            egyezik += 1
        else:
            elter.append(kulcs or f"#{e.get('sorszam')}")
    return {"ok": bool(lanc.get("ok")) and not elter, "lanc": lanc, "egyezik": egyezik, "elter": elter}


# ══ MCP-HÍVÓ: napló, időkorlát, Echolot-ütemezés ══════════════════════════

class Hivo:
    """A motorok MCP-hívásai egy helyen: minden hívás a forrás-naplóba kerül (eszköz, paraméter, idő, hiba).

    Az Echolot névtelen MCP-kerete szűk (percenként ~30 HTTP-kérés IP-nként; egy önálló MCP-hívás a kézfogással és a
    lezárással 4-5 kérés — élesben mérve 12 mp-es szünettel is 429-et kaptunk). Ezért a sajtó-motor hívásai EGY
    munkamenetben mennek (`echolot_munkamenet`: egy kézfogás, utána hívásonként egy kérés), SORBAN, szünettel (alap
    7 mp — munkamenetben 4 mp-cel még 429 jött; SZIMULATOR_ECHOLOT_MUNKAMENET_KOZ_S),
    429-nél várakozva újrapróbálva (új munkamenettel), és egy időkereten belül — ami nem fér bele, kimarad, és a
    kimenet kimondja. A Makronóm és a Lexonomics párhuzamosan (szerverenként legfeljebb 3)."""

    def __init__(self, hiv=None, *, echolot_koz_s: float = 12.0, echolot_max_s: float = 330.0,
                 varakozas_429_s: float = 35.0, munkamenet_koz_s: float | None = None):
        self._hiv = hiv
        self.koz_s = echolot_koz_s
        self.munkamenet_koz_s = echolot_koz_s if munkamenet_koz_s is None else munkamenet_koz_s
        self._munkamenet_aktiv = False
        self._kliens = None
        self.varakozas_429_s = varakozas_429_s
        self.echolot_hatarido = time.monotonic() + echolot_max_s
        self._echolot_zar = asyncio.Lock()
        self._echolot_utolso = 0.0
        self._echolot_lista_kesz = False
        self._sema = {"makronom": asyncio.Semaphore(3), "lexonomics": asyncio.Semaphore(3)}
        self.naplo: list[dict] = []

    async def _nyers(self, szerver: str, eszkoz: str, par: dict) -> str:
        hiv = self._hiv or kulso_mcp.hiv
        try:
            return await asyncio.wait_for(hiv(szerver, eszkoz, par, max_kar=200_000), MCP_HIVAS_TIMEOUT_S)
        except asyncio.TimeoutError:
            return json.dumps({"hiba": f"időtúllépés ({MCP_HIVAS_TIMEOUT_S} mp)"}, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001 — egy motor hibája ne vigye el a futást
            return json.dumps({"hiba": f"{type(e).__name__}: {str(e)[:300]}"}, ensure_ascii=False)

    async def __call__(self, szerver: str, eszkoz: str, par: dict):
        t0 = time.monotonic()
        if szerver == "echolot":
            szoveg = await self._echolot(eszkoz, par)
        elif szerver in self._sema:
            async with self._sema[szerver]:
                szoveg = await self._nyers(szerver, eszkoz, par)
        else:
            szoveg = await self._nyers(szerver, eszkoz, par)
        v = mcp_json(szoveg) if szoveg is not None else {"hiba": "kimaradt (az Echolot időkerete elfogyott)"}
        hiba = _hiba_e(v)
        self.naplo.append({"motor": szerver, "eszkoz": eszkoz, "parameterek": par, "ok": hiba is None,
                           "hiba": hiba, "mp": round(time.monotonic() - t0, 1)})
        return v, hiba

    async def _echolot_lista(self) -> None:
        """Az első Echolot-hívás előtt az eszközlista (a kulso_mcp ezt cache-eli). Ha a lista-lekérés 429-be fut, a
        kulso_mcp 2 percig „nem érhető el”-t adna minden hívásra — ezért itt, várakozva, frissen újrapróbáljuk."""
        if self._hiv is not None or self._echolot_lista_kesz:
            return
        self._echolot_lista_kesz = True
        for probalkozas in range(3):
            if await kulso_mcp._lista("echolot", friss=probalkozas > 0):     # elsőre a cache is jó
                return
            if time.monotonic() + self.varakozas_429_s > self.echolot_hatarido:
                return
            await asyncio.sleep(self.varakozas_429_s * (probalkozas + 1))

    @contextlib.asynccontextmanager
    async def echolot_munkamenet(self):
        """Egy MCP-munkamenet a benne futó összes Echolot-híváshoz. A kliens ugyanabban a feladatban nyílik és zárul
        (az anyio cancel scope ezt kéri), ezért a sajtó-motor teljes Echolot-sora ezen belül fut."""
        self._munkamenet_aktiv = self._hiv is None
        try:
            yield self
        finally:
            self._munkamenet_aktiv = False
            await self._kliens_zar()

    async def _kliens_zar(self) -> None:
        k, self._kliens = self._kliens, None
        if k is not None:
            try:
                await k.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 — a megtört munkamenet zárása se vigye el a futást
                logger.debug("szimulator: az Echolot-munkamenet zárása hibát adott", exc_info=True)

    async def _munkamenet_hivas(self, eszkoz: str, par: dict) -> str:
        try:
            if self._kliens is None:
                k = kulso_mcp._kliens("echolot")
                await k.__aenter__()
                self._kliens = k
            eredmeny = await self._kliens.call_tool(eszkoz, par, raise_on_error=False, timeout=MCP_HIVAS_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 — 429, kapcsolati hiba: a munkamenet új kézfogással nyílik újra
            await self._kliens_zar()
            return json.dumps({"hiba": f"{type(e).__name__}: {str(e)[:300]}"}, ensure_ascii=False)
        szoveg = kulso_mcp._szovegge(eredmeny, 200_000)
        logger.info("szimulator: echolot.%s (munkamenet) %s, %d karakter", eszkoz,
                    "HIBA" if eredmeny.is_error else "ok", len(szoveg))
        if eredmeny.is_error:
            return json.dumps({"hiba": szoveg[:1000] or "az eszköz hibát adott"}, ensure_ascii=False)
        return szoveg or "{}"

    async def _echolot(self, eszkoz: str, par: dict) -> str | None:
        async with self._echolot_zar:
            munkamenet = self._munkamenet_aktiv
            if not munkamenet:
                await self._echolot_lista()
            koz = self.munkamenet_koz_s if munkamenet else self.koz_s
            for probalkozas in range(3):
                varj = self._echolot_utolso + koz - time.monotonic()
                if time.monotonic() + max(0.0, varj) > self.echolot_hatarido:
                    return None
                if varj > 0:
                    await asyncio.sleep(varj)
                szoveg = (await self._munkamenet_hivas(eszkoz, par) if munkamenet
                          else await self._nyers("echolot", eszkoz, par))
                self._echolot_utolso = time.monotonic()
                if "429" in szoveg[:400] and '"hiba"' in szoveg[:30]:
                    logger.info("szimulator: Echolot 429 (%s) — %.0f mp múlva újra", eszkoz,
                                self.varakozas_429_s * (probalkozas + 1))
                    self._echolot_utolso += self.varakozas_429_s * (probalkozas + 1)
                    continue
                return szoveg
            return szoveg


# ══ 1. TÁRSADALOM — DELPHOI ═══════════════════════════════════════════════

def delphoi_felhasznalo() -> str:
    return os.environ.get("SZIMULATOR_DELPHOI_USER", "").strip() or "bridge:dontes-szimulator"


def panel_n() -> int:
    return max(30, _env_int("SZIMULATOR_PANEL_N", 60))


#: A szimulátor SAJÁT panelmotorja (2026-11-22, a Kommandant: „túl drága … olcsóbb modell is elég a swarm
#: előrejelzéshez”). Mérés alapján választva — `orakel_backteszt/olcso_swarm_20261122/JEGYZOKONYV.md`: ár, a HU
#: választás előtti vak backteszt (rés, parse, seed-szórás), a közmédia-kapu, a skála-eltolás a Hy3-hoz képest, és a
#: tudás-határ (`modell_cutoff.CUTOFF`). KÜLÖN a nyilvános DELPHOI-termék motorjától (`DELPHOI_FG_MODEL`): a ledger és
#: a fizetős fókuszcsoport skálája így nem változik csendben; a job-sor a tényleges modellt és panel-verziót rögzíti.
PANEL_MODELL_ALAP = "deepseek-ai/DeepSeek-V4.1-Flash"


def panel_modell() -> str:
    """`SZIMULATOR_PANEL_MODEL` (SiliconFlow-modellnév), alapból a mért nyertes."""
    return os.environ.get("SZIMULATOR_PANEL_MODEL", "").strip() or PANEL_MODELL_ALAP


async def tarsadalom(lepes: str, orszag: str, *, get_db, deps: dict | None, futas: str,
                     max_s: float) -> dict:
    """A DELPHOI fókuszcsoport-motorja a lépés szövegével (input_kind=concept). A kredit a belső felhasználóé
    (`SZIMULATOR_DELPHOI_USER`); ha nincs fedezet, PONTOSAN a hiányt írjuk jóvá, kimondva (`feltoltes`). A panel
    saját motorja (SiliconFlow, Hy3 — NULLTARIF) fut; hiba vagy időtúllépés → failed + automatikus refund."""
    from plugins import delphoi
    if not deps or not (deps.get("siliconflow_api_key") or os.environ.get("SILICONFLOW_API_KEY")):
        return {"ok": False, "hiba": "nincs SiliconFlow-kulcs a DELPHOI-panel motorjához"}
    orszag = orszag if orszag in delphoi.FG_COUNTRIES and orszag in delphoi.COUNTRY_PANEL_CONFIG else "HU"
    spec = {"country": orszag, "n_per_cell": panel_n(), "n_seeds": 1}
    felh = delphoi_felhasznalo()
    koltseg = delphoi.job_cost(spec)
    delphoi.ensure_welcome(get_db, felh)
    egyenleg = delphoi.get_credits(get_db, felh)["balance"]
    feltoltes = 0
    if egyenleg < koltseg:
        feltoltes = koltseg - egyenleg
        delphoi.add_credits(get_db, felh, feltoltes, f"szimulator-{futas}")
    created = delphoi.create_job(get_db, felh, "concept", lepes, spec)
    if not created.get("ok"):
        return {"ok": False, "hiba": f"a DELPHOI nem vette fel a jobot: {created.get('error')}", "kredit": koltseg}
    job_id = created["job_id"]
    modell = panel_modell()
    alap = {"job_id": job_id, "kredit": koltseg, "feltoltes": feltoltes, "felhasznalo": felh, "orszag": orszag,
            "n": spec["n_per_cell"], "modell": modell}
    try:
        rep = await asyncio.wait_for(delphoi.process_job(deps, job_id, model=modell), max_s)
    except asyncio.TimeoutError:
        delphoi._fail_job(get_db, job_id, f"a döntés-szimulátor időkorlátja ({int(max_s)} mp)")
        return {**alap, "ok": False, "hiba": f"a panel {int(max_s)} mp alatt nem végzett (kredit visszaírva)"}
    if not rep.get("ok"):
        return {**alap, "ok": False, "hiba": f"a panel nem futott le: {rep.get('error')} (kredit visszaírva)"}
    return {**alap, "ok": True, "eredmeny": rep.get("result") or {}}


DIM_NEV = {"age": "kor", "settlement": "település", "edu": "iskolai végzettség"}


def panel_kivonat(t: dict) -> dict:
    """A panel aggregátumából a modellnek és az oldalnak kellő rész (determinisztikus, AI nélkül)."""
    if not t.get("ok"):
        return {"ok": False, "hiba": t.get("hiba")}
    agg = t.get("eredmeny") or {}
    ki = {"ok": True, "n": (agg.get("panel") or {}).get("n_completed") or agg.get("n"),
          "osszkep": agg.get("overall_score"), "semleges_pont": (agg.get("baseline") or {}).get("value", 3.0),
          "skala": "1 = egyáltalán nem tetszik … 5 = nagyon tetszik (SSR)",
          "media_etrend": [{"csoport": s["segment"], "n": s["n"], "pont": s["score"], "elteres": s["vs_baseline"]}
                           for s in agg.get("segments") or []],
          "bontasok": {DIM_NEV.get(k, k): [{"csoport": g["group"], "n": g["n"], "pont": g["score"],
                                            "elteres": g["vs_baseline"]} for g in v]
                       for k, v in (agg.get("breakdowns") or {}).items()},
          "konfidencia": agg.get("confidence"),
          "scope": (agg.get("scope") or {}).get("verdict"), "scope_figyelmeztetes": (agg.get("scope") or {}).get("warning"),
          "lefedettseg": (agg.get("coverage") or {}).get("score"),
          "modell": (agg.get("panel") or {}).get("model_id"),
          "megjegyzes": agg.get("disclaimer") or "Szintetikus panel relatív jelzése, nem közvélemény-kutatás."}
    return ki


# ══ 2–4. ADATGYŰJTÉS ══════════════════════════════════════════════════════

def lap_azonosit(forras_nev: str) -> str | None:
    n = (forras_nev or "").lower().strip()
    if n.startswith("telex"):
        return "telex"
    if n.startswith("444"):
        return "444"
    if "magyar nemzet" in n:
        return "magyarnemzet"
    if n.startswith("index"):
        return "index"
    if "spiegel" in n and "tagesspiegel" not in n:
        return "spiegel"
    if n.startswith("bbc"):
        return "bbc"
    if "global times" in n:
        return "globaltimes"
    if n.startswith(("hirado", "híradó", "m1 ")) or n == "m1":
        return "kozmedia"
    return None


def _cikkek(v) -> list[dict]:
    if not isinstance(v, dict):
        return []
    lista = v.get("articles") or v.get("items") or []
    return [a for a in lista if isinstance(a, dict)]


def _cikk(a: dict) -> dict:
    return {"cim": str(a.get("title") or "")[:220], "lead": str(a.get("lead") or "")[:380],
            "datum": str(a.get("published_at") or "")[:10], "url": a.get("url"), "forras": a.get("source_name")}


async def sajto_adat(terv: dict, hivo: Hivo) -> dict:
    """A hét lap valódi korábbi cikkei a témában (search_news hu/en/de), forrásprofiljuk (source_profile, 7 nap),
    stílusminta a friss címeikből ott, ahol a témában nincs cikkük (get_news), és a szférák/régiók keretezése."""
    async with hivo.echolot_munkamenet():
        return await _sajto_adat(terv, hivo)


async def _sajto_adat(terv: dict, hivo: Hivo) -> dict:
    q = terv["kereses"]
    temaban: dict[str, list] = {l["kulcs"]: [] for l in LAPOK}
    stilus: dict[str, list] = {l["kulcs"]: [] for l in LAPOK}
    profil: dict[str, dict] = {}
    hianyok: list[str] = []
    for nyelv in ("hu", "en", "de"):
        v, hiba = await hivo("echolot", "search_news", {"query": q[nyelv], "days": 21, "limit": 50,
                                                        "max_per_source": 4, "language": nyelv})
        if hiba:
            hianyok.append(f"search_news ({nyelv}): {hiba}")
        for a in _cikkek(v):
            lap = lap_azonosit(a.get("source_name"))
            if lap and len(temaban[lap]) < 4:
                temaban[lap].append(_cikk(a))
    for lap in LAPOK:
        if not lap.get("forras"):
            continue
        v, hiba = await hivo("echolot", "source_profile", {"source": lap["forras"], "days": 7, "limit": 3})
        if hiba:
            hianyok.append(f"source_profile ({lap['nev']}): {hiba}")
            continue
        profilok = (v or {}).get("profiles") or {}
        jelolt = [(k, p) for k, p in profilok.items() if lap_azonosit(k) == lap["kulcs"]]
        jelolt.sort(key=lambda kp: ("(x)" in kp[0].lower(), -int((kp[1] or {}).get("articles") or 0)))
        if jelolt:
            nev, p = jelolt[0]
            profil[lap["kulcs"]] = {"forrasnev": nev, **{k: p.get(k) for k in (
                "lean", "trust_tier", "spheres", "dominant_frame", "frame_distribution", "emotion_distribution",
                "avg_sentiment", "articles")}}
    keretezes, hiba = await hivo("echolot", "frame_divergence", {"query": q["hu"], "days": 21})
    if hiba:
        hianyok.append(f"frame_divergence: {hiba}")
    regionalis, hiba = await hivo("echolot", "regional_framing", {"query": q["en"], "days": 21})
    if hiba:
        hianyok.append(f"regional_framing: {hiba}")
    for lap in LAPOK:
        if lap.get("lean"):
            profil[lap["kulcs"]] = {**(profil.get(lap["kulcs"]) or {}), "lean": lap["lean"]}
        if not lap.get("forras"):
            hianyok.append(f"{lap['nev']}: nincs az Echolot-korpuszban — stílusminta nélkül, alacsony bizonyossággal")
    for lap in LAPOK:
        if temaban[lap["kulcs"]] or not lap.get("forras"):
            continue
        v, hiba = await hivo("echolot", "get_news", {"source": lap["forras"], "limit": 4})
        if hiba:
            hianyok.append(f"get_news ({lap['nev']}): {hiba}")
        stilus[lap["kulcs"]] = [_cikk(a) for a in _cikkek(v) if lap_azonosit(a.get("source_name")) == lap["kulcs"]][:4]
    lapok = []
    for lap in LAPOK:
        k = lap["kulcs"]
        lapok.append({"lap": lap["nev"], "nyelv": lap["nyelv"], "profil": profil.get(k),
                      "temaba_vago_cikkek": temaban[k], "friss_cimek_stilushoz": stilus[k]})
    return {"lapok": lapok, "keretezes_szferankent": tomorit(keretezes, lista_max=10),
            "regionalis_keretezes": tomorit(regionalis, lista_max=10), "hianyok": hianyok}


def _idoszak_vege(per: str) -> date | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})", per or "")
    if m:
        y, mo = int(m[1]), int(m[2])
        return date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)
    m = re.fullmatch(r"(\d{4})-Q([1-4])", per or "")
    if m:
        y, q = int(m[1]), int(m[2])
        return date(y + (q == 4), (q * 3) % 12 + 1, 1) - timedelta(days=1)
    return None


def cel_pont(path, hatar: date, ma: date) -> dict | None:
    """A pálya azon pontja, amelynek adata a horizont végéig MEGJELENIK (közlési késéssel) — ez lesz az ellenőrizhető
    célidőszak. Ha a horizont a pályán túl van, a pálya utolsó, a horizonton belül megjelenő pontja."""
    jo = []
    for pt in path or []:
        if not isinstance(pt, dict) or not isinstance(pt.get("value"), (int, float)):
            continue
        per = str(pt.get("period") or "")
        vege = _idoszak_vege(per)
        if not vege:
            continue
        megjelenik = vege + timedelta(days=KOZLESI_KESES_NAP["Q" if "Q" in per else "M"])
        if ma < megjelenik <= hatar:
            jo.append((megjelenik, pt))
    if not jo:
        return None
    megj, pt = max(jo, key=lambda x: x[0])
    return {**pt, "megjelenik": megj.isoformat()}


def _szam(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def alapvonal_sorok(nyers: list, hatar: date, ma: date) -> tuple[list[dict], list[str]]:
    """A gazdasági ALAPVONAL determinisztikusan, a Makronóm nyers válaszaiból (AI nélkül): a forecast_path pontja a
    horizont végéig megjelenő időszakra, 80%-os sávval; az alapkamat és az árfolyam naiv alapvonala (változatlan).
    `nyers`: [(eszköz, paraméterek, válasz)]."""
    sorok, hianyok = [], []
    for e, p, v in nyers:
        if not isinstance(v, dict):
            continue
        c = str(p.get("country") or "")
        if e == "forecast_path":
            ind = str(p.get("indicator") or "")
            pt = cel_pont(v.get("path"), hatar, ma)
            if not pt:
                hianyok.append(f"alapvonal {c} {ind}: a horizonton belül nem jelenik meg új adat a pályán")
                continue
            nev, egys = GAZDASAGI_NEV.get(ind, (ind, "%"))
            sorok.append({"kulcs": f"{c}:{ind}", "mutato": nev, "orszag": c, "egyseg": egys, "idoszak": pt["period"],
                          "ertek": round(pt["value"], 2), "also": _szam(pt.get("low")), "felso": _szam(pt.get("high")),
                          "naiv": False, "hatarido": pt["megjelenik"],
                          "utolso_teny": v.get("last_actual"), "forras": f"Makronóm forecast_path ({v.get('source')})",
                          "modszer": "SARIMAX + IMF/OECD-horgony, 80%-os sáv a backtesztből",
                          "ellenorzes": GAZDASAGI_ELLENORZES.get(ind, "Makronóm").format(c=c, p=pt["period"], s="")})
        elif e == "get_macro_indicator" and p.get("indicator") == "policy_rate" and _szam(v.get("value")) is not None:
            nev, egys = GAZDASAGI_NEV["policy_rate"]
            sorok.append({"kulcs": f"{c}:policy_rate", "mutato": nev, "orszag": c, "egyseg": egys,
                          "idoszak": hatar.isoformat(), "ertek": _szam(v["value"]), "also": None, "felso": None,
                          "naiv": True, "hatarido": hatar.isoformat(),
                          "forras": f"Makronóm get_macro_indicator ({v.get('source_used')}, {v.get('period')})",
                          "modszer": "naiv alapvonal: a mai kamat változatlan",
                          "ellenorzes": GAZDASAGI_ELLENORZES["policy_rate"].format(c=c, p=hatar.isoformat(), s="")})
        elif e == "yfinance" and str(p.get("symbol") or "") in GAZDASAGI_NEV and _szam(v.get("price")) is not None:
            sym = p["symbol"]
            nev, egys = GAZDASAGI_NEV[sym]
            sorok.append({"kulcs": sym, "mutato": nev, "orszag": "", "egyseg": egys, "idoszak": hatar.isoformat(),
                          "ertek": _szam(v["price"]), "also": None, "felso": None, "naiv": True,
                          "hatarido": hatar.isoformat(), "sav52": [v.get("52w_low"), v.get("52w_high")],
                          "forras": f"Makronóm yfinance ({v.get('last_trade_at')})",
                          "modszer": "naiv alapvonal: a mai árfolyam változatlan (52 hetes sáv: "
                                     f"{v.get('52w_low')}–{v.get('52w_high')})",
                          "ellenorzes": GAZDASAGI_ELLENORZES["fx"].format(c="", p="", s=sym)})
    return sorok, hianyok


async def piac_adat(terv: dict, hivo: Hivo, ev: int, *, hatar: date | None = None, ma: date | None = None) -> dict:
    """Makronóm: a friss mutatók (get_macro_indicator), az előrejelzések forgatókönyvvel (forecast) és 80%-os sávval
    (forecast_path), a piaci árak (yfinance), és belőlük a gazdasági ALAPVONAL (`alapvonal`, determinisztikusan)."""
    hivasok = []
    for m in terv["makro"]:
        hivasok.append(("makronom", "get_macro_indicator", {"country": m["country"], "indicator": m["indicator"]}))
    for f in terv["elorejelzes"]:
        hivasok.append(("makronom", "forecast", {"country": f["country"], "indicator": f["indicator"], "year": ev}))
        hivasok.append(("makronom", "forecast_path", {"country": f["country"], "indicator": f["indicator"]}))
    for s in terv["piaci"]:
        hivasok.append(("makronom", "yfinance", {"symbol": s}))
    eredmenyek = await asyncio.gather(*(hivo(sz, e, p) for sz, e, p in hivasok))
    ma = ma or datetime.now(timezone.utc).date()
    alap, alap_hiany = alapvonal_sorok([(e, p, v) for (_, e, p), (v, hiba) in zip(hivasok, eredmenyek) if not hiba],
                                       hatar or ma + timedelta(days=365), ma)
    adatok, hianyok = [], list(alap_hiany)
    for (_, e, p), (v, hiba) in zip(hivasok, eredmenyek):
        if hiba:
            hianyok.append(f"{e} {p}: {hiba}")
            continue
        if e == "forecast_path" and isinstance(v, dict):
            v = {k: v.get(k) for k in ("status", "last_actual", "path", "annual", "method", "backtest", "source",
                                       "generated_at", "reason") if k in v}
        adatok.append({"eszkoz": e, "parameterek": p, "eredmeny": tomorit(v, lista_max=10, szoveg_max=400)})
    return {"alapvonal": alap, "adatok": adatok, "hianyok": hianyok}


async def jog_adat(terv: dict, hivo: Hivo) -> dict:
    """Lexonomics: a megnevezett jogszabályhelyek hatályos szövege (provision), szavas keresés (search), és a
    releváns bírósági/hatósági döntések (search scope=decisions)."""
    if not kulso_mcp.bekotott("lexonomics"):
        return {"hivatkozasok": [], "talalatok": [], "dontesek": [],
                "hianyok": ["a Lexonomics nincs bekötve (LEXONOMICS_MCP_KEY)"]}
    j = terv["jog"]
    hivasok = ([("provision", {"path": h, "max_chars": 2500}) for h in j["hivatkozasok"]]
               + [("search", {"q": q, "limit": 5}) for q in j["kifejezesek"]]
               + [("search", {"q": q, "scope": "decisions", "limit": 4}) for q in j["dontesek"]])
    eredmenyek = list(await asyncio.gather(*(hivo("lexonomics", e, p) for e, p in hivasok)))
    # Ha egy hivatkozást a provision nem ismer fel (pl. szokatlan rövidítés), a search a hivatkozásra is keres:
    # a találat ugyanúgy forrással (source_url, állapotdátum) megy tovább, mint a többi.
    potlas = [{"q": p["path"], "limit": 3} for (e, p), (_, hiba) in zip(hivasok, eredmenyek)
              if e == "provision" and hiba]
    for p, v in zip(potlas, await asyncio.gather(*(hivo("lexonomics", "search", p) for p in potlas))):
        hivasok.append(("search", p))
        eredmenyek.append(v)
    hiv_ki, talalatok, dontesek, hianyok = [], [], [], []
    for (e, p), (v, hiba) in zip(hivasok, eredmenyek):
        if hiba:
            hianyok.append(f"{e} {p.get('path') or p.get('q')}: {hiba}")
            continue
        if e == "provision":
            hiv_ki.append({"kert": p["path"], "eredmeny": tomorit(v, lista_max=4, szoveg_max=2500)})
        elif not isinstance(v, dict):
            hianyok.append(f"{e} {p.get('q')}: váratlan válaszalak")
        elif p.get("scope") == "decisions":
            for d in (v.get("decision_hits") or [])[:4]:
                dontesek.append({"kereses": p["q"], **{k: d.get(k) for k in (
                    "source_name", "title", "number", "decided", "year", "url", "path", "pages")},
                    "reszlet": str(d.get("snippet") or "")[:500]})
        else:
            for t in (v.get("exact") or []) + (v.get("text_hits") or [])[:4]:
                allapot = t.get("state") or {}
                talalatok.append({"kereses": p["q"], **{k: t.get(k) for k in (
                    "path", "citation", "act_title", "label", "heading", "status")},
                    "reszlet": str(t.get("snippet") or "")[:450], "source_url": allapot.get("source_url"),
                    "allapot_datum": allapot.get("state_date")})
    return {"hivatkozasok": hiv_ki, "talalatok": talalatok[:12], "dontesek": dontesek[:6], "hianyok": hianyok}


# ══ LLM-LÉPÉSEK (Anthropic, az LLM-kapun át) ══════════════════════════════

ALAP_SZABALYOK = """Te a Claus-Bridge DÖNTÉS-SZIMULÁTORÁNAK egyik motorja vagy. A feladat SZIMULÁCIÓ, nem jóslat.
Szabályok:
1. Csak a kapott adatokra támaszkodj. Minden szám és jogszabályhely mellé írd oda a forrását (az eszköz neve és az
   adat saját forrása vagy URL-je, ahogy az adatban áll). Amit az adat nem ad, azt NE állítsd: tedd a hiányok közé.
2. A bizonytalanságot mondd ki (tartomány, forgatókönyv). Pontszámot csak ott adj, ahol az adat alátámasztja.
3. Valódi személy szájába ne adj idézetet. Kitalált adatot, cikket, döntést ne hozz.
4. Mai dátum: {ma}. A szimuláció horizontja: {horizont} (legkésőbb {hatar}).
5. A válaszod EGYETLEN JSON-objektum, magyarázó szöveg és kódblokk nélkül.
6. TÖMÖREN: az eredményből egy döntéshozónak szóló oldal lesz. Minden szövegmező legfeljebb 1-2 rövid mondat
   (kb. 30 szó), ha a mező leírása nem mond mást; a forrás a mező végén, zárójelben."""

ELOREJELZES_SEMA = """"elorejelzesek": legfeljebb 3 MÉRHETŐ előrejelzés a SAJÁT területedről ({terulet}), mindegyik:
  {{"allitas": "egy mondat, ami a határidőn egyértelműen igaz vagy hamis lesz",
   "metrika": "mit mérünk (pl. KSH éves fogyasztóiár-index)", "kuszob": "a feltétel számmal (pl. > 3,8%)",
   "hatarido": "ÉÉÉÉ-HH-NN (jövőbeli, a horizonton belül)", "valoszinuseg": 0.05–0.95,
   "feltetel": "lepes (csak akkor értékelhető, ha a lépés megtörténik) | alap (a lépéstől függetlenül értékelhető)",
   "ellenorzes": "hol lesz ellenőrizhető (intézmény, kiadvány, eszköz)", "alap": "melyik kapott adatból következik"}}"""
#: motoronként, mire vonatkozzon az előrejelzés — hogy a négy motor ne ugyanazt jósolja, és mind ellenőrizhető legyen
ELOREJELZES_TERULET = {
    "sajto": "sajtó: a lapok tényleges reakciója, az Echolot-korpuszban ellenőrizhetően (lap, időablak a bejelentéstől, "
             "cikkszám vagy keret)",
    "piac": "piac és makró: a Makronóm forrásaival ellenőrizhető szám (KSH, Eurostat, MNB, árfolyam)",
    "jog": "jog: jogszabály-módosítás, alkotmánybírósági vagy hatósági eljárás, a Magyar Közlönyben / NJT-n ellenőrizhetően",
    "tarsadalom": "társadalom: reprezentatív közvélemény-kutatás eredménye, tüntetés, népszavazási vagy aláírásgyűjtési "
                  "kezdeményezés",
}
TARSADALMI_SEMA = ELOREJELZES_SEMA.format(terulet=ELOREJELZES_TERULET["tarsadalom"])
SAJTO_SEMA = ELOREJELZES_SEMA.format(terulet=ELOREJELZES_TERULET["sajto"])
PIAC_SEMA = ELOREJELZES_SEMA.format(terulet=ELOREJELZES_TERULET["piac"])
JOG_SEMA = ELOREJELZES_SEMA.format(terulet=ELOREJELZES_TERULET["jog"])
MAKRO_LISTA = ", ".join(MAKRO_MUTATOK)
LAP_NEVEK = ", ".join(l["nev"] for l in LAPOK)


async def llm_lepes(nev: str, system: str, user: str, keret: Keret) -> tuple[dict | None, dict]:
    """Egy LLM-lépés: foglalás a keretből → `llm_kapu.chat` → könyvelés a kapu valós költéséből → JSON."""
    payload = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "max_tokens": KIMENET[nev]}
    cel = CELOK[nev]
    szint, _ = llm_kapu.szint_valaszt(payload, cel)
    becsles = becsles_usd(szint, len(system) + len(user), KIMENET[nev])
    if not keret.foglal(becsles, nev):
        return None, {"kihagyva": f"a költségkeret ({keret.max_usd:.2f} $) nem fedezte"}
    data, err, notes = await llm_kapu.chat(payload, purpose=cel)
    valos = float(((data or {}).get("usage") or {}).get("koltes_usd") or (err or {}).get("koltes_usd") or 0.0)
    keret.lezar(becsles, valos, nev)
    info = {"modell": (data or {}).get("model"), "koltes_usd": round(valos, 5)}
    if notes:
        info["megjegyzes"] = list(notes)
    if err is not None:
        info["hiba"] = f"{err.get('code')}: {str(err.get('message') or '')[:300]}"
        return None, info
    szoveg = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    obj = json_ki(szoveg)
    if obj is None:
        info["hiba"] = "a modell válasza nem volt értelmezhető JSON"
        info["nyers"] = szoveg[:3000]
    return obj, info


def _szabalyok(k: dict) -> str:
    alap = ALAP_SZABALYOK.format(ma=k["ma"], horizont=k["horizont"], hatar=k["hatar"])
    if k.get("helyzet"):
        alap += ("\n7. A JELENLEGI POLITIKAI HELYZET (a DELPHOI országkonfigjából): " + k["helyzet"] + "\n   Ha a "
                 "lépés mást nem mond, a jelenlegi kormány teszi. Politikus nevét csak akkor írd le, ha a lépésben "
                 "vagy a kapott adatban szerepel; egy lap beállítottságát (lean) ehhez a helyzethez mérd, ne "
                 "korábbi kormányokhoz.")
    return alap


def politikai_helyzet(orszag: str) -> str:
    """Az ország aktuális politikai helyzete a DELPHOI meglévő panel-konfigjából (`priming`) — ugyanaz a leírás,
    amit a szintetikus panel is kap. Élő próba (11-22): nélküle a sajtó-motor a 2026-os Tisza-kormány idején
    „Orbán ígér eurót” címet írt, és a Magyar Nemzetet kormánypárti lapként kezelte."""
    try:
        from plugins import delphoi
        return str((delphoi.COUNTRY_PANEL_CONFIG.get(orszag) or {}).get("priming") or "")
    except Exception:  # noqa: BLE001
        return ""


async def terv_lepes(lepes: str, k: dict, keret: Keret) -> tuple[dict, dict]:
    """A lépésből a motorok lekérdezései (keresőszavak, mutatók, jogszabályhelyek). A kimenetet a kód
    fehérlistán szűri, és kiegészíti az alap-készlettel — a modell nem hívhat tetszőleges eszközt."""
    system = _szabalyok(k) + "\nMost a TERVEZŐ vagy: a lépésből a lekérdezéseket állítod össze."
    user = f"""A LÉPÉS (a Kommandant szövege):
\"\"\"{lepes}\"\"\"

Add vissza ezt a JSON-t:
{{"rovid_cim": "legfeljebb 8 szó",
  "orszag": "a lépés fő országa ISO-2 kóddal (pl. HU)",
  "kereses": {{"hu": "2-3 magyar kulcsszó idézőjel nélkül", "en": "2-3 angol kulcsszó", "de": "2-3 német kulcsszó"}},
  "makro": [{{"country": "HU", "indicator": "a fenti listából"}}],
  "elorejelzes": [{{"country": "HU", "indicator": "inflation|gdp|unemployment"}}],
  "piaci": ["yfinance-szimbólumok, pl. EURHUF=X, ^BUX.BD, OTP.BD"],
  "jog": {{"hivatkozasok": ["csak a hivatkozás, magyarázat nélkül, pl. Alaptörvény K) cikk, Ptk. 6:1"],
          "kifejezesek": ["magyar jogi keresőkifejezés"], "dontesek": ["magyar keresőkifejezés bírósági/AB-döntésekhez"]}}}}
A `makro` mutatói közül választhatsz: {MAKRO_LISTA} (legfeljebb {MAX_MAKRO}).
Legfeljebb {MAX_HIVATKOZAS} hivatkozás, {MAX_KIFEJEZES} kifejezés, {MAX_DONTES_KERESES} döntés-keresés,
{MAX_SZIMBOLUM} szimbólum. A keresőszavak rövidek legyenek (a hírkereső teljes szöveges, ÉS-kapcsolattal keres)."""
    obj, info = await llm_lepes("terv", system, user, keret)
    return terv_tisztit(obj or {}, lepes), info


def terv_tisztit(t: dict, lepes: str) -> dict:
    """A terv fehérlistás szűrése + alap-készlet. A modell hibás vagy hiányzó terve is futtatható tervet ad."""
    orszag = str(t.get("orszag") or "HU").upper().strip()[:2]
    orszag = orszag if ORSZAG_MINTA.match(orszag) else "HU"
    ker = t.get("kereses") if isinstance(t.get("kereses"), dict) else {}
    tartalek = " ".join(re.findall(r"\w{5,}", lepes)[:3]) or lepes[:40]
    kereses = {ny: " ".join(str(ker.get(ny) or "").replace('"', " ").split())[:80] or tartalek
               for ny in ("hu", "en", "de")}
    makro, latott = [], set()
    for m in list(t.get("makro") or []) + [{"country": orszag, "indicator": a[0]} for a in ALAP_MAKRO]:
        if not isinstance(m, dict):
            continue
        c, i = str(m.get("country") or orszag).upper()[:2], str(m.get("indicator") or "").lower()
        if ORSZAG_MINTA.match(c) and i in MAKRO_MUTATOK and (c, i) not in latott and len(makro) < MAX_MAKRO:
            latott.add((c, i))
            makro.append({"country": c, "indicator": i})
    elore, latott = [], set()
    for f in list(t.get("elorejelzes") or []) + [{"country": orszag, "indicator": a} for a in ALAP_ELOREJELZES]:
        if not isinstance(f, dict):
            continue
        c, i = str(f.get("country") or orszag).upper()[:2], str(f.get("indicator") or "").lower()
        if ORSZAG_MINTA.match(c) and i in ELOREJELZES_MUTATOK and (c, i) not in latott \
                and len(elore) < MAX_ELOREJELZES_MUTATO:
            latott.add((c, i))
            elore.append({"country": c, "indicator": i})
    piaci = []
    for s in list(t.get("piaci") or []) + ([ALAP_SZIMBOLUM[orszag]] if orszag in ALAP_SZIMBOLUM else []):
        s = str(s or "").strip().upper()
        if SZIMBOLUM_MINTA.match(s) and s not in piaci and len(piaci) < MAX_SZIMBOLUM:
            piaci.append(s)
    j = t.get("jog") if isinstance(t.get("jog"), dict) else {}

    def _lista(x, n):
        return [" ".join(str(e).split())[:120] for e in (x or []) if isinstance(e, str) and e.strip()][:n]

    def _hivatkozas(h):
        # „Alaptörvény 38. cikk (forint, pénzrendszer)” → „Alaptörvény 38. cikk”: a Lexonomics a zárójeles
        # magyarázattal nem ismeri fel a hivatkozást (élő próba, 11-22)
        return re.sub(r"\s*\((?![a-z0-9]{1,3}\))[^)]*\)\s*$", "", h).strip()
    return {"rovid_cim": " ".join(str(t.get("rovid_cim") or "").split())[:80] or lepes[:60],
            "orszag": orszag, "kereses": kereses, "makro": makro, "elorejelzes": elore, "piaci": piaci,
            "jog": {"hivatkozasok": [_hivatkozas(h) for h in _lista(j.get("hivatkozasok"), MAX_HIVATKOZAS)],
                    "kifejezesek": _lista(j.get("kifejezesek"), MAX_KIFEJEZES) or [kereses["hu"]],
                    "dontesek": _lista(j.get("dontesek"), MAX_DONTES_KERESES)}}


async def sajto_lepes(lepes: str, adat: dict, k: dict, keret: Keret) -> tuple[dict | None, dict]:
    system = _szabalyok(k) + """
Most az ELŐVISSZHANG motorja vagy: megírod, mit írna holnap a lépésről """ + f"{len(LAPOK)}" + """ lap. Lapra szabva: a lap saját nyelvén,
a forrásprofilja (beállítottság, jellemző keret, érzelem, hangulat) és a VALÓDI korábbi cikkei, címei alapján.
Ahol a lapnak nincs témába vágó cikke, a friss címei adják a stílust; ahol semmi adat nincs, a bizonyosság alacsony,
és ezt mondd ki. A frame-osztályozás az Echolotban lehet, hogy még nem futott le: ezt ne pótold kitalált számmal.
BEÁLLÍTOTTSÁG: a profil `lean` mezője dönt. Az Echolot „gov” címkéje a 2026 előtti (Fidesz-kormány alatti) besorolás
öröksége: magánlapnál JOBBOLDALIT jelent, nem a mostani kormány sajtóját. A közmédia (M1, hirado.hu) a mindenkori
kormányt támogatja: 2026 tavasza óta a Tisza-kormányt."""
    user = f"""A LÉPÉS:
\"\"\"{lepes}\"\"\"

AZ ECHOLOT ADATAI (JSON):
{_json_dump(adat, 28_000)}

Add vissza:
{{"lapok": [{{"lap": "Telex", "cim": "a lap nyelvén", "cim_hu": "magyar fordítás (magyar lapnál ugyanaz)",
             "lead": "1-2 mondat a lap nyelvén", "lead_hu": "magyarul",
             "keretezes": "a keret neve és egy rövid indok (legfeljebb 12 szó)",
             "hangnem": "támogató|semleges|kritikus|ellenséges",
             "belekotes": "a lépés melyik eleme és miért (legfeljebb 20 szó)",
             "alap": ["a felhasznált valódi cikkek URL-je vagy címe"], "bizonyossag": "alacsony|közepes|magas"}}],
  "kozos_minta": "1-2 mondat: hol egyezik és hol válik szét a sajtó",
  "legsebezhetobb_mondat": "a lépés mondata, amibe a legtöbb lap belekötne (idézőjel nélkül)",
  {SAJTO_SEMA}}}
Mind a {len(LAPOK)} lap szerepeljen: {LAP_NEVEK}."""
    return await llm_lepes("sajto", system, user, keret)


async def piac_lepes(lepes: str, adat: dict, k: dict, keret: Keret) -> tuple[dict | None, dict]:
    """A GAZDASÁGI ELŐREJELZŐ — a szimuláció magja (a Kommandant, 11-22: „a közgazdasági közvetlen előrejelzés
    fontosabb”). Az alapvonal determinisztikus (Makronóm); a modell a lépés SZÁMSZERŰ hatását adja hozzá, sávval."""
    system = _szabalyok(k) + """
Most a GAZDASÁGI ELŐREJELZŐ vagy: ez a szimuláció MAGJA. A feladat a közvetlen, számszerű előrejelzés: az ALAPVONAL
minden mutatójára mondd meg, mennyivel térne el a lépés miatt a megadott időszakban, 80%-os sávval.
- Az alapvonal a Makronóm saját előrejelzése (forecast_path, 80%-os sáv a backtesztből), ill. az alapkamatnál és az
  árfolyamnál a mai érték (naiv). Ezt NE írd felül: te az ELTÉRÉST adod.
- `valtozas`: az alapvonalhoz képesti eltérés a mutató SAJÁT egységében (százalékpont; árfolyamnál forint).
  `also` ≤ `valtozas` ≤ `felso`.
  - Pályás mutatónál (infláció, GDP, munkanélküliség) `also`/`felso` a HATÁS 80%-os sávja; a kód hozzáadja az
    alapvonal saját sávjához.
  - Naiv alapvonalnál (alapkamat, árfolyam) `also`/`felso` a TELJES 80%-os sáv a mai értékhez képest, a szokásos piaci
    mozgással együtt (az 52 hetes sáv és a forgatókönyvek adják a nagyságrendet).
- Ahol nincs közvetlen elaszticitás az adatban, adj nagyságrendi becslést SZÉLES sávval, és nevezd meg a feltevést
  (átviteli csatorna, analógia). Ha a horizonton nincs érdemi hatás, `valtozas` = 0, szűk sávval. Csak akkor hagyj ki
  egy mutatót (a `nem_modellezheto` listába, indokkal), ha még az irány sem dönthető el.
- Az egységet ne találd ki: ha az adat nem mondja meg, írd oda, hogy ismeretlen."""
    alap = [{x: a.get(x) for x in ("kulcs", "mutato", "egyseg", "idoszak", "ertek", "also", "felso", "naiv", "modszer",
                                    "sav52", "utolso_teny")} for a in (adat or {}).get("alapvonal") or []]
    nyers = {x: v for x, v in (adat or {}).items() if x != "alapvonal"}
    user = f"""A LÉPÉS:
\"\"\"{lepes}\"\"\"

AZ ALAPVONAL (a lépés NÉLKÜL; a `kulcs` alapján hivatkozz rá):
{_json_dump(alap, 6_000)}

A MAKRONÓM TOVÁBBI ADATAI (JSON):
{_json_dump(nyers, 20_000)}

Add vissza:
{{"hatas_szamszeru": [{{"kulcs": "az alapvonal egyik kulcsa", "valtozas": 0.0, "also": 0.0, "felso": 0.0,
                       "csatorna": "az átviteli csatorna (legfeljebb 15 szó)",
                       "alap": "mire épül a szám: kapott adat, analógia vagy feltevés (legfeljebb 25 szó)",
                       "bizonyossag": "alacsony|közepes|magas"}}],
  "kiindulas": [{{"mutato": "...", "ertek": "szám + egység", "idoszak": "...", "forras": "eszköz + source_used/URL"}}],
  "hatasok": [{{"terulet": "infláció|árfolyam|kamat|növekedés|munkaerőpiac|költségvetés|tőkepiac",
               "irany": "fel|le|semleges|bizonytalan", "becsles": "számmal vagy tartománnyal",
               "bizonytalansag": "mi miatt, mekkora sáv", "forras": "melyik adatból", "modszer": "mire épül"}}],
  "nem_modellezheto": ["…"],
  {PIAC_SEMA}}}
A `hatas_szamszeru` az ALAPVONAL minden sorára adjon választ (vagy a `nem_modellezheto`-ben indokold)."""
    return await llm_lepes("piac", system, user, keret)


def gazdasagi_tabla(alap: list[dict], eredmeny: dict | None) -> list[dict]:
    """Az alapvonal + a modell számszerű hatása → „a lépéssel” érték és 80%-os sáv, determinisztikusan.

    Pályás mutatónál a sáv az alapvonal sávja + a hatás sávja (konzervatív, szélesebb); naiv alapvonalnál a modell sávja
    a teljes sáv a mai értékhez képest."""
    hatas = {str(h.get("kulcs")): h for h in ((eredmeny or {}).get("hatas_szamszeru") or []) if isinstance(h, dict)}
    ki = []
    for a in alap or []:
        sor = {**a, "hatas": None}
        h = hatas.get(a["kulcs"])
        v, lo, hi = (_szam((h or {}).get(x)) for x in ("valtozas", "also", "felso"))
        if None in (v, lo, hi) or _szam(a.get("ertek")) is None:
            ki.append(sor)
            continue
        lo, hi = min(lo, v, hi), max(lo, v, hi)
        nd = 1 if a.get("egyseg") == "Ft" else 2
        alap_lo = a["also"] if (not a.get("naiv") and a.get("also") is not None) else a["ertek"]
        alap_hi = a["felso"] if (not a.get("naiv") and a.get("felso") is not None) else a["ertek"]
        sor["hatas"] = {"valtozas": round(v, nd), "also": round(lo, nd), "felso": round(hi, nd),
                        **{x: " ".join(str((h or {}).get(x) or "").split())[:200]
                           for x in ("csatorna", "alap", "bizonyossag")}}
        sor["dontessel"] = round(a["ertek"] + v, nd)
        sor["dontessel_also"] = round(alap_lo + lo, nd)
        sor["dontessel_felso"] = round(alap_hi + hi, nd)
        ki.append(sor)
    return ki


def _fmt(x, nd: int = 2) -> str:
    return "—" if x is None else f"{x:.{nd}f}".replace(".", ",")


def gazdasagi_elorejelzesek(tabla: list[dict]) -> list[dict]:
    """A gazdasági tábla → mérhető, intervallumos előrejelzések (80%): „a mutató X és Y közé esik, ha a lépés megtörténik”.
    A határidő az a nap, amikorra a célidőszak adata megjelenik (a horizonton belül)."""
    ki = []
    for t in tabla or []:
        if not t.get("hatas"):
            continue
        nd = 1 if t.get("egyseg") == "Ft" else 2
        lo, hi = t["dontessel_also"], t["dontessel_felso"]
        hol = f"{t['orszag']} " if t.get("orszag") else ""
        ki.append({"allitas": f"{t['mutato']} ({hol}{t['idoszak']}): {_fmt(lo, nd)} és {_fmt(hi, nd)} {t['egyseg']} "
                              f"között lesz, ha a lépés megtörténik (alapvonal nélküle: {_fmt(t['ertek'], nd)})",
                   "valoszinuseg": 0.8, "hatarido": t["hatarido"], "feltetel": "lepes", "metrika": t["mutato"],
                   "kuszob": f"{_fmt(lo, nd)}–{_fmt(hi, nd)} {t['egyseg']}", "ellenorzes": t.get("ellenorzes") or "Makronóm",
                   "alap": f"{t.get('modszer')}; a lépés hatása: {t['hatas'].get('csatorna')}"})
    return ki


async def jog_lepes(lepes: str, adat: dict, k: dict, keret: Keret) -> tuple[dict | None, dict]:
    system = _szabalyok(k) + """
Most a JOG motorja vagy (jogász szemmel, tényállásból kiindulva). Jogszabályhelyet CSAK akkor nevezz meg, ha a
Lexonomics kapott adatában szerepel, a source_url-jével és állapotdátumával. Ha egy nyilvánvalóan releváns szabály
nincs az adatban, ne idézd emlékezetből: tedd a `nem_ellenorzott` listába „ellenőrizendő” jelzéssel. A Lexonomics
nem hiteles jogforrás (a hiteles a Magyar Közlöny / NJT, ill. az EUR-Lex): ezt add tovább."""
    user = f"""A LÉPÉS:
\"\"\"{lepes}\"\"\"

A LEXONOMICS ADATAI (JSON):
{_json_dump(adat, 26_000)}

Add vissza:
{{"utkozesek": [{{"szabaly": "jogszabályhely", "reszlet": "rövid idézet a kapott szövegből",
                 "mi_utkozik": "a lépés melyik eleme és miért", "mit_kell_modositani": "…",
                 "forras": "source_url", "allapot_datum": "…"}}],
  "dontesek": [{{"azonosito": "…", "relevancia": "…", "url": "…"}}],
  "eljaras": "milyen jogalkotási/eljárási lépés kell (pl. minősített többség), ha az adatból következik",
  "nem_ellenorzott": ["…"],
  {JOG_SEMA}}}"""
    return await llm_lepes("jog", system, user, keret)


def _meglevo_elorejelzesek(r: dict) -> list[dict]:
    return [{"terulet": "gazdaság", "allitas": e["allitas"], "valoszinuseg": e["valoszinuseg"], "hatarido": e["hatarido"]}
            for e in gazdasagi_elorejelzesek(r.get("gazdasag") or [])] + [
            {"terulet": nev, "allitas": e.get("allitas"), "valoszinuseg": e.get("valoszinuseg"),
             "hatarido": e.get("hatarido")}
            for nev in ("sajto", "piac", "jog")
            for e in ((r.get(nev) or {}).get("eredmeny") or {}).get("elorejelzesek") or [] if isinstance(e, dict)]


def _motor_kivonat(r: dict) -> dict:
    """A motorok kimenetének az összegzőnek szóló kivonata (nyers adat nélkül); elöl a gazdasági tábla."""
    return {"gazdasagi_elorejelzes": [{x: t.get(x) for x in ("mutato", "idoszak", "egyseg", "ertek", "also", "felso",
                                                               "dontessel", "dontessel_also", "dontessel_felso", "hatas")}
                                      for t in r.get("gazdasag") or []],
            "nem_futott": [n for n in ("sajto", "jog") if ((r.get(n) or {}).get("info") or {}).get("nem_kert")],
            "tarsadalom": r.get("tarsadalom", {}).get("kivonat"),
            "sajto": {k: v for k, v in (r.get("sajto", {}).get("eredmeny") or {}).items() if k != "elorejelzesek"},
            "piac": {k: v for k, v in (r.get("piac", {}).get("eredmeny") or {}).items() if k != "elorejelzesek"},
            "jog": {k: v for k, v in (r.get("jog", {}).get("eredmeny") or {}).items() if k != "elorejelzesek"},
            "adathianyok": r.get("adathianyok")}


async def osszegzes_lepes(lepes: str, r: dict, k: dict, keret: Keret) -> tuple[dict | None, dict]:
    """Összegzés + kockázati térkép + módosítási javaslat EGY Sonnet-lépésben (a külön Opus-lépés 11-22-én kimaradt:
    „túl drága”). Az első mondat a gazdasági közvetlen előrejelzés, számokkal."""
    system = _szabalyok(k) + """
Most az ÖSSZEGZŐ vagy: a motorok eredményéből egy oldal eleje, a kockázati térkép és a módosítási javaslat.
- A MAG a gazdasági előrejelzés: az összegzés ELSŐ mondata a legfontosabb számszerű hatás, a tábla számaival
  (alapvonal → a lépéssel, sávval).
- A társadalmi jel a DELPHOI szintetikus panelé: relatív jelzés a 3,0-s semleges ponthoz mérve, SOHA nem
  közvélemény-kutatás, abszolút százalékot ne mondj belőle.
- Amelyik motor nem futott (`nem_futott`: a sajtó és a jog csak teljes módban fut), arról ne írj hiányként.
- Minden kockázat egy motor jelére épüljön; a javaslat konkrét átfogalmazás vagy feltétel legyen, a trade-offjával.
- A tömör, egyszerű mondat az első; a szakember a részleteket a motorok szakaszaiban látja."""
    user = f"""A LÉPÉS:
\"\"\"{lepes}\"\"\"

A MOTOROK EREDMÉNYE (JSON):
{_json_dump(_motor_kivonat(r), 30_000)}

A MOTOROK MÁR MEGADOTT ELŐREJELZÉSEI (ezeket NE ismételd, és ne mondj ellent nekik; ha nincs új, mérhető társadalmi
előrejelzés, az "elorejelzesek" lista legyen üres):
{_json_dump(_meglevo_elorejelzesek(r), 6_000)}

Add vissza:
{{"osszegzes": "3-5 mondat: az első a gazdasági hatás számokkal; utána hol a legnagyobb ellenállás, mi bizonytalan",
  "fo_megallapitasok": ["3-5 rövid pont, mindegyik egy motorra hivatkozva"],
  "tarsadalom_ertelmezes": "2-3 mondat a panel relatív jeléről (melyik csoport fogadja jobban/rosszabbul)",
  "bizonytalansag": "1-2 mondat: mit nem tud a szimuláció",
  "kockazati_terkep": [{{"kockazat": "legfeljebb 20 szó", "terulet": "gazdaság|társadalom|sajtó|jog",
                        "valoszinuseg": "alacsony|közepes|magas", "sulyossag": "alacsony|közepes|magas",
                        "jel": "melyik motor melyik adata (legfeljebb 12 szó)"}}],
  "modositasok": [{{"javaslat": "konkrét szöveg vagy feltétel (legfeljebb 40 szó)",
                   "mit_csokkent": "melyik kockázatot (néhány szó)", "ara": "a trade-off (legfeljebb 15 szó)"}}],
  {TARSADALMI_SEMA}}}
Legfeljebb 5 kockázat és 3 javaslat."""
    return await llm_lepes("osszegzes", system, user, keret)


# ══ A FUTÁS ══════════════════════════════════════════════════════════════

def bemenet_hiba(lepes: str) -> str | None:
    s = (lepes or "").strip()
    if len(s) < LEPES_MIN_KAR:
        return f"a lépés leírása túl rövid (legalább {LEPES_MIN_KAR} karakter kell)"
    if len(s) > LEPES_MAX_KAR:
        return f"a lépés leírása túl hosszú (legfeljebb {LEPES_MAX_KAR} karakter)"
    return None


def uj_futas_id() -> str:
    return "sz-" + secrets.token_hex(4)


async def futtat(lepes: str, horizont: str = "", *, get_db, deps: dict | None = None, futas: str | None = None,
                 hiv=None, panel_fn=None, studio: bool = True, max_usd_: float | None = None,
                 echolot_koz_s: float | None = None, naplozas: bool = True, teljes: bool = False) -> dict:
    """A szimuláció. Alapból GAZDASÁGI mód: terv + DELPHOI-panel + gazdasági előrejelzés + összegzés; `teljes=True`
    mellett a sajtó (Echolot) és a jog (Lexonomics) is fut (a Kommandant, 11-22: „túl drága … a közgazdasági
    közvetlen előrejelzés fontosabb”). Sosem dob: a hibák a kimenet `figyelmeztetesek`-ébe kerülnek, a részeredmény megmarad.

    Tesztekhez injektálható: `hiv` (az MCP-hívó, alapból kulso_mcp.hiv), `panel_fn(lepes, orszag)` (a DELPHOI-panel
    helyett), `studio=False` (nincs Studio-dokumentum). Az LLM-hívás mindig `llm_kapu.chat`."""
    lepes = (lepes or "").strip()
    horizont = (horizont or "").strip() or ALAP_HORIZONT
    futas = futas or uj_futas_id()
    ma = datetime.now(timezone.utc).date()
    hatar_napok = horizont_napok(horizont, ma)
    k = {"ma": ma.isoformat(), "horizont": horizont, "hatar": (ma + timedelta(days=hatar_napok)).isoformat()}
    keret = Keret(max_usd_ if max_usd_ is not None else max_usd())
    r: dict = {"futas": futas, "verzio": VERZIO, "mod": "teljes" if teljes else "gazdasagi", "lepes": lepes, "lepes_hash": _sha(lepes), "horizont": horizont,
               "horizont_vege": k["hatar"], "indult": _most(), "figyelmeztetesek": [], "adathianyok": {}}
    if not llm_kapu.elerheto():
        r.update(allapot="hiba", kesz=_most(), koltseg=keret.allapot())
        r["figyelmeztetesek"].append("nincs ANTHROPIC_API_KEY — a szimulátor csak az Anthropic API-n fut")
        return r
    hivo = Hivo(hiv, echolot_koz_s=echolot_koz_s if echolot_koz_s is not None
                else _env_float("SZIMULATOR_ECHOLOT_KOZ_S", 12.0),
                munkamenet_koz_s=echolot_koz_s if echolot_koz_s is not None
                else _env_float("SZIMULATOR_ECHOLOT_MUNKAMENET_KOZ_S", 7.0),
                echolot_max_s=_env_float("SZIMULATOR_ECHOLOT_MAX_S", 330.0))

    # 0. terv
    terv, info = await terv_lepes(lepes, k, keret)
    r["terv"], r["terv_info"] = terv, info
    k["helyzet"] = politikai_helyzet(terv["orszag"])

    # 1. társadalom (DELPHOI) — a saját motorján, a többivel párhuzamosan
    panel_max_s = _env_float("SZIMULATOR_PANEL_MAX_S", 600.0)

    async def _panel():
        try:
            if panel_fn is not None:
                return await panel_fn(lepes, terv["orszag"])
            return await tarsadalom(lepes, terv["orszag"], get_db=get_db, deps=deps, futas=futas, max_s=panel_max_s)
        except Exception as e:  # noqa: BLE001
            logger.exception("szimulator %s: a DELPHOI-panel hibát dobott", futas)
            return {"ok": False, "hiba": f"{type(e).__name__}: {str(e)[:300]}"}
    panel_feladat = asyncio.ensure_future(_panel())

    # 2–4. gazdaság (mindig), sajtó és jog (csak teljes módban): adat → LLM, motoronként párhuzamosan
    ev = min(int(k["hatar"][:4]), ma.year + 2)

    async def _nem_kert():
        return {"adat": None, "eredmeny": None, "info": {"nem_kert": True}}

    async def _ag(nev, adat_coro, llm_fn):
        try:
            adat = await adat_coro
        except Exception as e:  # noqa: BLE001
            logger.exception("szimulator %s: %s-adatgyűjtés hiba", futas, nev)
            return {"adat": None, "eredmeny": None, "info": {"hiba": f"adatgyűjtés: {type(e).__name__}: {e}"}}
        obj, inf = await llm_fn(lepes, adat, k, keret)
        return {"adat": adat, "eredmeny": obj, "info": inf}
    sajto, piac, jog = await asyncio.gather(
        _ag("sajto", sajto_adat(terv, hivo), sajto_lepes) if teljes else _nem_kert(),
        _ag("piac", piac_adat(terv, hivo, ev, hatar=date.fromisoformat(k["hatar"]), ma=ma), piac_lepes),
        _ag("jog", jog_adat(terv, hivo), jog_lepes) if teljes else _nem_kert())
    r["sajto"], r["piac"], r["jog"] = sajto, piac, jog
    r["gazdasag"] = gazdasagi_tabla((piac.get("adat") or {}).get("alapvonal") or [], piac.get("eredmeny"))
    for nev, ag in (("sajto", sajto), ("piac", piac), ("jog", jog)):
        if (ag.get("adat") or {}).get("hianyok"):
            r["adathianyok"][nev] = ag["adat"]["hianyok"]

    tars = await panel_feladat
    r["tarsadalom"] = {"panel": {k_: v for k_, v in tars.items() if k_ != "eredmeny"}, "kivonat": panel_kivonat(tars)}
    if not tars.get("ok"):
        r["adathianyok"]["tarsadalom"] = [str(tars.get("hiba"))]

    # összegzés + kockázati térkép + módosítás: egy Sonnet-lépés
    ossz, ossz_info = await osszegzes_lepes(lepes, r, k, keret)
    r["osszegzes"], r["osszegzes_info"] = ossz, ossz_info
    r["kockazat"] = ({"kockazati_terkep": ossz.get("kockazati_terkep") or [], "modositasok": ossz.get("modositasok") or []}
                     if ossz else None)
    r["kockazat_info"] = ossz_info

    # előrejelzések → DELPHOI hash-lánc; elöl a gazdasági, intervallumos sorok (determinisztikusan a táblából)
    elore, kimaradt = elorejelzesek_tisztit(gazdasagi_elorejelzesek(r["gazdasag"]), "piac", ma=ma,
                                            hatar_napok=hatar_napok, max_db=8)
    for e in elore:
        e["modell"] = (piac.get("info") or {}).get("modell")
    for terulet, forras, info_ in (("sajto", (sajto.get("eredmeny") or {}), sajto.get("info") or {}),
                                   ("piac", (piac.get("eredmeny") or {}), piac.get("info") or {}),
                                   ("jog", (jog.get("eredmeny") or {}), jog.get("info") or {}),
                                   ("tarsadalom", ossz or {}, ossz_info or {})):
        jo, ki = elorejelzesek_tisztit(forras.get("elorejelzesek"), terulet, ma=ma, hatar_napok=hatar_napok)
        for e in jo:
            e["modell"] = info_.get("modell")
            kulcs = re.sub(r"\W+", " ", e["allitas"].lower()).strip()
            if any(re.sub(r"\W+", " ", x["allitas"].lower()).strip() == kulcs for x in elore):
                kimaradt.append(f"{terulet}: „{e['allitas'][:80]}” — ugyanez már szerepel")
                continue
            elore.append(e)
        kimaradt += ki
    r["elorejelzes_kimaradt"] = kimaradt
    if naplozas and elore:
        try:
            elore = naploba(get_db, elore, futas=futas, orszag=terv["orszag"], lepes_hash=r["lepes_hash"])
        except Exception as e:  # noqa: BLE001
            logger.exception("szimulator %s: a DELPHOI-naplóba írás hibát adott", futas)
            r["figyelmeztetesek"].append(f"az előrejelzések nem kerültek a DELPHOI-naplóba: {type(e).__name__}: {e}")
    r["elorejelzesek"] = elore

    for nev, info_ in (("terv", r["terv_info"]), ("sajtó", sajto.get("info")), ("gazdaság", piac.get("info")),
                       ("jog", jog.get("info")), ("összegzés", ossz_info)):
        info_ = info_ or {}
        if info_.get("nem_kert"):
            continue
        if info_.get("kihagyva"):
            r["figyelmeztetesek"].append(f"{nev}: kimaradt — {info_['kihagyva']}")
        elif info_.get("hiba"):
            r["figyelmeztetesek"].append(f"{nev}: {info_['hiba']}")
    r["koltseg"] = keret.allapot()
    r["forrasnaplo"] = hivo.naplo
    kesz = (piac.get("eredmeny") and ossz and tars.get("ok")
            and (not teljes or (sajto.get("eredmeny") and jog.get("eredmeny"))))
    r["allapot"] = "kesz" if kesz else "reszleges"

    if studio:
        try:
            r["studio"] = await studio_dokumentum(r, hiv=hiv)
        except Exception as e:  # noqa: BLE001
            logger.exception("szimulator %s: Studio-dokumentum hiba", futas)
            r["studio"] = {"hiba": f"{type(e).__name__}: {str(e)[:200]}"}
    r["kesz"] = _most()
    return r


# ══ AZ EGYOLDALAS ═════════════════════════════════════════════════════════

def _pont(x) -> str:
    try:
        return f"{float(x):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return "?"


def _elojeles(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "?"
    return ("+" if v > 0 else "−" if v < 0 else "±") + f"{abs(v):.2f}".replace(".", ",")


def _szazalek(p) -> str:
    try:
        return f"{round(float(p) * 100)}%"
    except (TypeError, ValueError):
        return "?"


FELTETEL_NEV = {"lepes": "ha a lépés megtörténik", "alap": "a lépéstől függetlenül"}
IDEZOJEL = " \"'„”“«»"


def _rovid(x, n: int = 220) -> str:
    s = " ".join(str(x or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def szakaszok(r: dict) -> list[dict]:
    """Az egyoldalas szerkezete: [{cim, bekezdesek, pontok, tablazat}] — ebből épül a szöveg ÉS a Studio-dokumentum
    (determinisztikusan, AI-lépés nélkül)."""
    terv = r.get("terv") or {}
    ki: list[dict] = []
    fej = [f"Lépés: {r.get('lepes')}",
           f"Horizont: {r.get('horizont')} (legkésőbb {r.get('horizont_vege')}) · Futás: {r.get('futas')} · "
           f"{str(r.get('indult') or '')[:10]}",
           ("Ez szimuláció, nem jóslat: a saját motorok (Makronóm, DELPHOI, Echolot, Lexonomics) adataiból készült. "
            "A szimulált sajtócímek nem valódi cikkek; a társadalmi jel szintetikus panel relatív jelzése, nem "
            "közvélemény-kutatás." if r.get("mod") == "teljes" else
            "Ez szimuláció, nem jóslat: gazdasági mód — a Makronóm-alapvonalra épülő közvetlen gazdasági előrejelzés és "
            "a DELPHOI szintetikus panel relatív jelzése (nem közvélemény-kutatás). Sajtó és jog: „teljes "
            "szimuláció” kérésre.")]
    if r.get("allapot") == "reszleges":
        fej.append("RÉSZEREDMÉNY: " + "; ".join(r.get("figyelmeztetesek") or ["egy vagy több motor nem adott eredményt"]))
    ki.append({"cim": None, "bekezdesek": fej})

    o = r.get("osszegzes") or {}
    ki.append({"cim": "Összegzés", "bekezdesek": [o.get("osszegzes") or "Az összegzés nem készült el."]
               + ([f"Bizonytalanság: {o['bizonytalansag']}"] if o.get("bizonytalansag") else []),
               "pontok": [str(x) for x in (o.get("fo_megallapitasok") or [])][:5]})

    # 1. társadalom
    tk = (r.get("tarsadalom") or {}).get("kivonat") or {}
    tp = (r.get("tarsadalom") or {}).get("panel") or {}
    if tk.get("ok"):
        bek = [f"Összkép: {_pont(tk.get('osszkep'))} / 5 (semleges pont {_pont(tk.get('semleges_pont'))}, eltérés "
               f"{_elojeles((tk.get('osszkep') or 0) - (tk.get('semleges_pont') or 3.0))}); N = {tk.get('n')}, "
               f"kredit: {tp.get('kredit')}."]
        if o.get("tarsadalom_ertelmezes"):
            bek.append(o["tarsadalom_ertelmezes"])
        if tk.get("scope_figyelmeztetes"):
            bek.append(f"A DELPHOI hatókör-kapuja: {tk['scope_figyelmeztetes']}")
        sorok = [["média-étrend", s["csoport"], str(s["n"]), _pont(s["pont"]), _elojeles(s["elteres"])]
                 for s in tk.get("media_etrend") or []]
        for dim, csop in (tk.get("bontasok") or {}).items():
            sorok += [[dim, g["csoport"], str(g["n"]), _pont(g["pont"]), _elojeles(g["elteres"])] for g in csop]
        s_tars = {"cim": "Társadalom — DELPHOI szintetikus panel", "bekezdesek": bek,
                  "tablazat": {"fejlec": ["Bontás", "Csoport", "N", "Pont (1–5)", "Eltérés"],
                               "aranyok": [0.24, 0.30, 0.10, 0.18, 0.18], "sorok": sorok} if sorok else None}
    else:
        s_tars = {"cim": "Társadalom — DELPHOI szintetikus panel",
                  "bekezdesek": [f"A panel nem adott eredményt: {tk.get('hiba') or tp.get('hiba') or 'ismeretlen ok'}."]}

    # 2. sajtó
    se = (r.get("sajto") or {}).get("eredmeny") or {}
    sorok, leadek = [], []
    for lap in se.get("lapok") or []:
        cim = lap.get("cim") or ""
        if lap.get("cim_hu") and lap.get("cim_hu") != cim:
            cim += f" ({lap['cim_hu']})"
        sorok.append([f"{lap.get('lap') or ''} (bizonyosság: {lap.get('bizonyossag') or '?'})", _rovid(cim),
                      _rovid(lap.get("keretezes")), _rovid(lap.get("belekotes"))])
        lead = lap.get("lead_hu") or lap.get("lead")
        if lead:
            leadek.append(f"{lap.get('lap')}: {_rovid(lead, 320)}")
    bek = []
    if se.get("legsebezhetobb_mondat"):
        bek.append(f"A legsebezhetőbb mondat: „{str(se['legsebezhetobb_mondat']).strip(IDEZOJEL)}”")
    if se.get("kozos_minta"):
        bek.append(se["kozos_minta"])
    if not sorok:
        bek.append("A sajtó-motor nem adott eredményt: " + str(((r.get("sajto") or {}).get("info") or {}).get(
            "hiba") or ((r.get("sajto") or {}).get("info") or {}).get("kihagyva") or "ismeretlen ok"))
    s_sajto = {"cim": "Sajtó — az Elővisszhang (szimulált címek, nem valódi cikkek)", "bekezdesek": bek,
               "tablazat": {"fejlec": ["Lap", "Cím", "Keretezés", "Belekötés"], "aranyok": [0.18, 0.30, 0.24, 0.28],
                            "sorok": sorok} if sorok else None,
               "pontok_utana": [f"Lead — {x}" for x in leadek]}

    # gazdaság — a mag
    pe = (r.get("piac") or {}).get("eredmeny") or {}
    sorok = []
    for t in r.get("gazdasag") or []:
        nd = 1 if t.get("egyseg") == "Ft" else 2
        alap = _fmt(t.get("ertek"), nd) + (f" ({_fmt(t['also'], nd)}–{_fmt(t['felso'], nd)})"
                                           if t.get("also") is not None and not t.get("naiv") else " (naiv)")
        h = t.get("hatas")
        sorok.append([t["mutato"], str(t.get("idoszak")), alap,
                      (f"{_fmt(t['dontessel'], nd)} ({_fmt(t['dontessel_also'], nd)}–{_fmt(t['dontessel_felso'], nd)})"
                       if h else "—"),
                      (("+" if h["valtozas"] > 0 else "") + _fmt(h["valtozas"], nd) if h else "nem becsült"),
                      _rovid(f"{h.get('csatorna')} — {h.get('alap')} (bizonyosság: {h.get('bizonyossag')})", 200)
                      if h else ""])
    bek = []
    if sorok:
        bek.append("Alapvonal: a Makronóm saját előrejelzése (80%-os sáv a backtesztből), ill. az alapkamatnál és az "
                   "árfolyamnál a mai érték. „A lépéssel”: az alapvonal + a szimulált hatás, 80%-os sávval. Minden "
                   "sor mérhető előrejelzésként a naplóba került.")
    if pe.get("kiindulas"):
        bek.append("Kiinduló adatok: " + "; ".join(f"{x.get('mutato')} {x.get('ertek')} ({x.get('idoszak')}, "
                                                    f"{x.get('forras')})" for x in pe["kiindulas"][:6]))
    if pe.get("nem_modellezheto"):
        bek.append("Nem becsülhető a kapott adatból: " + "; ".join(str(x) for x in pe["nem_modellezheto"]))
    if not pe:
        bek.append("A gazdasági motor nem adott eredményt: " + str(((r.get("piac") or {}).get("info") or {}).get(
            "hiba") or ((r.get("piac") or {}).get("info") or {}).get("kihagyva") or "ismeretlen ok"))
    s_gazd = {"cim": "Gazdasági előrejelzés — alapvonal és a lépés hatása (Makronóm)", "bekezdesek": bek,
              "tablazat": {"fejlec": ["Mutató", "Időszak", "Alapvonal (80%)", "A lépéssel (80%)", "Eltérés",
                                      "Csatorna és alap"],
                           "aranyok": [0.2, 0.11, 0.16, 0.17, 0.1, 0.26], "sorok": sorok} if sorok else None,
              "pontok": [f"{h.get('terulet')}: {h.get('irany')} — {h.get('becsles')} (bizonytalanság: "
                         f"{h.get('bizonytalansag')}; forrás: {h.get('forras')})" for h in pe.get("hatasok") or []]}

    # 4. jog
    je = (r.get("jog") or {}).get("eredmeny") or {}
    pontok = [f"{u.get('szabaly')}: {u.get('mi_utkozik')} → {u.get('mit_kell_modositani')} "
              f"(forrás: {u.get('forras')}, állapot: {u.get('allapot_datum')})" for u in je.get("utkozesek") or []]
    pontok += [f"Döntés: {d.get('azonosito')} — {d.get('relevancia')} ({d.get('url')})" for d in je.get("dontesek") or []]
    bek = [je["eljaras"]] if je.get("eljaras") else []
    if je.get("nem_ellenorzott"):
        bek.append("Ellenőrizendő (a Lexonomics nem adta): " + "; ".join(str(x) for x in je["nem_ellenorzott"]))
    bek.append("A Lexonomics nem hiteles jogforrás; a hiteles szöveg a Magyar Közlöny / NJT, ill. az EUR-Lex.")
    if not je:
        bek.insert(0, "A jog-motor nem adott eredményt.")
    s_jog = {"cim": "Jog — Lexonomics", "bekezdesek": bek, "pontok": pontok}

    # kockázat + módosítás
    ke = r.get("kockazat") or {}
    sorok = [[_rovid(x.get("kockazat")), str(x.get("terulet") or ""), str(x.get("valoszinuseg") or ""),
              str(x.get("sulyossag") or ""), _rovid(x.get("jel"), 160)] for x in ke.get("kockazati_terkep") or []]
    s_kock = {"cim": "Kockázati térkép", "bekezdesek": [] if sorok else ["A kockázati térkép nem készült el: " + str(
        (r.get("kockazat_info") or {}).get("kihagyva") or (r.get("kockazat_info") or {}).get("hiba") or "?")],
              "tablazat": {"fejlec": ["Kockázat", "Terület", "Esély", "Súly", "Jel"],
                           "aranyok": [0.33, 0.17, 0.14, 0.14, 0.22], "sorok": sorok} if sorok else None}
    s_mod = {"cim": "Mit érdemes a lépésen módosítani",
             "pontok": [f"{m.get('javaslat')} — csökkenti: {m.get('mit_csokkent')}; ára: {m.get('ara')}"
                        for m in ke.get("modositasok") or []]}

    # előrejelzések
    sorok = [[f"{e.get('allitas')} (ellenőrzés: {_rovid(e.get('ellenorzes'), 120)})", _szazalek(e.get("valoszinuseg")),
              str(e.get("hatarido")), FELTETEL_NEV.get(e.get("feltetel"), "ha a lépés megtörténik"),
              f"{(e.get('naplo') or {}).get('entity_key', '—')} · {str(e.get('hash') or '')[:12]}"]
             for e in r.get("elorejelzesek") or []]
    s_elore = {"cim": "Mérhető előrejelzések — DELPHOI hash-láncos napló",
               "bekezdesek": ["Minden sor a DELPHOI append-only, hash-láncolt predikció-naplójába került: a határidő "
                              "után lemérjük, mennyire talált (Brier-pontszám). A „ha a lépés megtörténik” sorok csak "
                              "akkor értékelhetők, ha a lépést meghozzák."] if sorok else
               ["Mérhető előrejelzés nem készült."],
               "tablazat": {"fejlec": ["Állítás", "Esély", "Határidő", "Feltétel", "Napló"],
                            "aranyok": [0.37, 0.12, 0.17, 0.15, 0.19], "sorok": sorok} if sorok else None}

    # költség + források
    ko = r.get("koltseg") or {}
    lab = [f"Anthropic-költség: {ko.get('usd', 0):.3f} $ (keret: {ko.get('max_usd', 0):.2f} $)"
           + (f"; kimaradt a keret miatt: {', '.join(ko['kimaradt'])}" if ko.get("kimaradt") else ""),
           f"DELPHOI-panel: {tp.get('kredit', '—')} kredit" + (f" (ebből {tp['feltoltes']} belső jóváírás)"
                                                                if tp.get("feltoltes") else "")]
    naplo = r.get("forrasnaplo") or []
    lab.append(f"Motorhívások: {sum(1 for x in naplo if x.get('ok'))}/{len(naplo)} sikeres")
    hianyok = [f"{m}: {str(h)[:160]}" for m, hs in (r.get("adathianyok") or {}).items() for h in hs]
    if hianyok:
        lab.append("Adathiány: " + "; ".join(hianyok[:8]) + (" …" if len(hianyok) > 8 else ""))
    # SORREND: elöl a döntéshez kellő oldal (összegzés, gazdasági előrejelzés, kockázat, módosítás, előrejelzések), utána
    # a panel; a sajtó és a jog csak akkor, ha futott (teljes mód)
    def futott(n):
        return not (((r.get(n) or {}).get("info") or {}).get("nem_kert"))
    ki += [s_gazd, s_kock, s_mod, s_elore, s_tars] + ([s_sajto] if futott("sajto") else []) \
        + ([s_jog] if futott("jog") else []) + [{"cim": "Költség és források", "bekezdesek": lab}]
    return ki


def egyoldalas(r: dict) -> str:
    """Az eredmény sima szövegként (a messages-be és a jelentésbe)."""
    terv = r.get("terv") or {}
    sorok = [f"DÖNTÉS-SZIMULÁTOR — {terv.get('rovid_cim') or r.get('lepes', '')[:60]}"]
    for sz in szakaszok(r):
        if sz.get("cim"):
            sorok += ["", sz["cim"].upper()]
        sorok += [str(b) for b in sz.get("bekezdesek") or []]
        sorok += [f"- {p}" for p in sz.get("pontok") or []]
        t = sz.get("tablazat")
        if t:
            sorok += [" | ".join(s) for s in t["sorok"]]
        sorok += [f"- {p}" for p in sz.get("pontok_utana") or []]
    if (r.get("studio") or {}).get("edit_url"):
        sorok += ["", f"Studio-dokumentum: {r['studio']['edit_url']}"]
    return "\n".join(sorok)


def telegram_html(r: dict) -> str:
    """Az eredmény Telegram-HTML-ben (a hosszút a `_telegram_push` darabolja)."""
    e = html.escape
    terv = r.get("terv") or {}
    sorok = [f"<b>DÖNTÉS-SZIMULÁTOR — {e(terv.get('rovid_cim') or r.get('lepes', '')[:60])}</b>"]
    for sz in szakaszok(r):
        if sz.get("cim"):
            sorok += ["", f"<b>{e(sz['cim'])}</b>"]
        sorok += [e(str(b)) for b in sz.get("bekezdesek") or []]
        sorok += [f"• {e(p)}" for p in sz.get("pontok") or []]
        t = sz.get("tablazat")
        if t:
            sorok += [f"• {e(' | '.join(s))}" for s in t["sorok"]]
        sorok += [f"• {e(p)}" for p in sz.get("pontok_utana") or []]
    if (r.get("studio") or {}).get("edit_url"):
        sorok += ["", f"<b>Studio:</b> {e(r['studio']['edit_url'])}"]
    return "\n".join(sorok)


# ══ STUDIO: formázott dokumentum, determinisztikus wordcraft ═══════════════

def _wc_szoveg(s) -> str:
    return " ".join(str(s or "").split())


#: A szedéstükör szélessége pontban (A4/Letter, a Studio alapmargóival mérve ~455 pt) — az oszlopszélességek alapja.
SZEDESTUKOR_PT = 450


def wordcraft_parancsok(r: dict) -> list[dict]:
    """A Studio WordCraft-parancsai az egyoldalashoz: címsorok, bekezdések, felsorolás, táblázatok. AI-lépés nincs:
    a szöveg a `szakaszok()`-ból jön. Invariáns: minden blokk előtt a kurzor egy üres bekezdésben áll."""
    c: list[dict] = []

    def cmd(nev, **p):
        c.append({"command": nev, "params": p})

    def bekezdes(szoveg, felkover=None):
        cmd("para.normal")
        if felkover:
            cmd("format.bold", value=True)
            cmd("text.insert", text=felkover)
            cmd("format.bold", value=False)
        cmd("text.insert", text=_wc_szoveg(szoveg))
        cmd("text.newParagraph")

    def felsorolas(pontok):
        if pontok:
            cmd("para.normal")
            cmd("para.bullets")
            for p in pontok:
                cmd("text.insert", text=_wc_szoveg(p))
                cmd("text.newParagraph")
            cmd("para.bullets", off=True)

    terv = r.get("terv") or {}
    cmd("para.heading1")
    cmd("text.insert", text=_wc_szoveg(f"Döntés-szimulátor: {terv.get('rovid_cim') or r.get('lepes', '')[:60]}"))
    cmd("text.newParagraph")
    for sz in szakaszok(r):
        if sz.get("cim"):
            cmd("para.heading2")
            cmd("text.insert", text=_wc_szoveg(sz["cim"]))
            cmd("text.newParagraph")
        for b in sz.get("bekezdesek") or []:
            b = str(b)
            m = re.match(r"^([^:]{3,40}): (.+)$", b)
            if m and not sz.get("cim"):
                bekezdes(m.group(2), felkover=m.group(1) + ": ")
            else:
                bekezdes(b)
        felsorolas(sz.get("pontok") or [])
        t = sz.get("tablazat")
        if t and t.get("sorok"):
            cmd("para.normal")
            cmd("insert.table", rows=len(t["sorok"]) + 1, cols=len(t["fejlec"]))
            # a validate_document élő ellenőrzése (11-22) szerint: kimondott oszlopszélesség (az egyenlő oszlopokban a
            # fejléc és a rövid érték — „közepes”, „Határidő” — Word-szedésben szó közepén tört), a fejléc minden
            # oldalon ismétlődjön, és egy sor ne törjön két oldalra
            cmd("table.properties", headerRow=True, allowBreak=False)
            oszlop = len(t["fejlec"])
            aranyok = t.get("aranyok") or [1.0 / oszlop] * oszlop
            cellak = [(h, True) for h in t["fejlec"]] + [(x, False) for s in t["sorok"] for x in s]
            for i, (x, fej) in enumerate(cellak):
                if fej:
                    cmd("table.columnWidth", width=round(SZEDESTUKOR_PT * aranyok[i]))
                elif i % oszlop == 0:
                    cmd("table.properties", allowBreak=False)
                if fej:
                    cmd("format.bold", value=True)
                cmd("text.insert", text=_wc_szoveg(x) or "—")
                if fej:
                    cmd("format.bold", value=False)
                if i < len(cellak) - 1:
                    cmd("text.tab")
            cmd("caret.docEnd")
            cmd("para.normal")
        felsorolas(sz.get("pontok_utana") or [])
    return c


async def studio_dokumentum(r: dict, *, hiv=None) -> dict:
    """Új, névvel ellátott Word-dokumentum a Kommandant Studiójában (files new), majd EGY wordcraft-hívás a
    parancsokkal. A válasz a szerkesztő-link (edit_url)."""
    if not kulso_mcp.bekotott("studio"):
        return {"hiba": "a Studio nincs bekötve (STUDIO_MCP_KEY)"}
    hiv = hiv or kulso_mcp.hiv
    terv = r.get("terv") or {}
    cim = f"Döntés-szimulátor — {terv.get('rovid_cim') or 'lépés'} — {str(r.get('indult') or '')[:10]}"
    uj = mcp_json(await hiv("studio", "files", {"action": "new", "kind": "document", "title": cim[:120]},
                            max_kar=50_000))
    file_id = uj.get("file_id") if isinstance(uj, dict) else None
    par = {"commands": wordcraft_parancsok(r)}
    if file_id:
        par["file_id"] = file_id
    else:
        par["template"] = "blank"
    # a válasz minden parancs lépését visszaadja (500 parancsnál >100 ezer karakter) — a keret ezért nagy, és ha a
    # JSON mégis csonka, a file_id és az edit_url a nyers szövegből is kiolvasható
    nyers = await hiv("studio", "wordcraft", par, max_kar=5_000_000)
    v = mcp_json(nyers)
    if isinstance(v, dict) and not v.get("file_id") and not _hiba_e(v):
        for mezo in ("file_id", "edit_url", "preview_url", "name"):
            m = re.search(rf'"{mezo}"\s*:\s*"([^"]+)"', nyers or "")
            if m:
                v[mezo] = m.group(1)
    if not isinstance(v, dict) or not v.get("file_id"):
        return {"hiba": str(_hiba_e(v) or "a wordcraft nem adott file_id-t")[:300]}
    bukott = [s for s in v.get("steps") or [] if isinstance(s, dict) and s.get("ok") is False]
    ki = {"file_id": v["file_id"], "edit_url": v.get("edit_url"), "preview_url": v.get("preview_url"),
          "nev": v.get("name") or cim, "ellenorzes": (v.get("check") or {}).get("status")}
    if bukott:
        ki["bukott_parancsok"] = len(bukott)
    return ki


# ══ INDÍTÁS A HÁTTÉRBEN, ÁLLAPOT ═════════════════════════════════════════

_FUTO: dict[str, asyncio.Task] = {}


def varhato_perc(teljes: bool = False) -> str:
    return "4–8 perc" if teljes else "2–5 perc"


async def _hatter(futas: str, lepes: str, horizont: str, *, get_db, deps, kesz_fn, start_id: int, **kw) -> dict:
    try:
        r = await futtat(lepes, horizont, get_db=get_db, deps=deps, futas=futas, **kw)
    except Exception as e:  # noqa: BLE001 — a futtat nem dob, de a háttér-feladat sose haljon meg némán
        logger.exception("szimulator %s: váratlan hiba", futas)
        r = {"futas": futas, "lepes": lepes, "horizont": horizont, "allapot": "hiba", "kesz": _most(),
             "figyelmeztetesek": [f"váratlan hiba: {type(e).__name__}: {str(e)[:300]}"]}
    try:
        _nyom(get_db, futas, "adat", json.dumps(r, ensure_ascii=False, default=str), thread=start_id)
        _nyom(get_db, futas, {"kesz": "kész", "reszleges": "részleges"}.get(r.get("allapot"), "hiba"),
              egyoldalas(r), cimzett="kommandant", thread=start_id, status="unread")
    except Exception:  # noqa: BLE001
        logger.exception("szimulator %s: a nyom írása hibát adott", futas)
    if kesz_fn is not None:
        try:
            await kesz_fn(r)
        except Exception:  # noqa: BLE001
            logger.exception("szimulator %s: a kész-értesítés hibát adott", futas)
    logger.info("szimulator %s: %s, %.3f $", futas, r.get("allapot"), (r.get("koltseg") or {}).get("usd", 0.0))
    return r


def inditas(lepes: str, horizont: str = "", *, get_db, deps: dict | None = None, kesz_fn=None, **kw) -> dict:
    """A szimuláció indítása a háttérben (perceket vesz igénybe). Azonnal visszatér: `{futas, varhato}` vagy
    `{hiba}`. Egyszerre egy futás (a gép és a motorok kímélése). A futás saját, üres contextvars-kontextusban
    indul: a webes kör nyoma nem keveredik bele."""
    hiba = bemenet_hiba(lepes)
    if hiba:
        return {"hiba": hiba}
    if not llm_kapu.elerheto():
        return {"hiba": "nincs ANTHROPIC_API_KEY — a döntés-szimulátor csak az Anthropic API-n fut"}
    if _FUTO:
        return {"hiba": "már fut egy szimuláció — várd meg az eredményét", "futas": next(iter(_FUTO))}
    futas = uj_futas_id()
    horizont = (horizont or "").strip() or ALAP_HORIZONT
    start_id = _nyom(get_db, futas, "fut", json.dumps({"futas": futas, "lepes": lepes.strip(), "horizont": horizont,
                                                         "indult": _most(), "verzio": VERZIO}, ensure_ascii=False))
    feladat = asyncio.get_running_loop().create_task(
        _hatter(futas, lepes.strip(), horizont, get_db=get_db, deps=deps, kesz_fn=kesz_fn, start_id=start_id, **kw),
        context=contextvars.Context())
    _FUTO[futas] = feladat
    feladat.add_done_callback(lambda _t, f=futas: _FUTO.pop(f, None))
    return {"futas": futas, "elindult": True, "varhato": varhato_perc(bool(kw.get("teljes"))), "horizont": horizont,
            "mod": "teljes" if kw.get("teljes") else "gazdasagi"}


def allapot(get_db, futas: str) -> dict:
    """Egy futás állapota a messages-nyomból: fut / kész / részleges / hiba, kész futásnál az egyoldalas szöveg,
    a Studio-link, a költség és a napló-ellenőrzés."""
    futas = (futas or "").strip()
    if not FUTAS_MINTA.match(futas):
        return {"hiba": "érvénytelen futás-azonosító (alak: sz-xxxxxxxx)"}
    sorok = _sorok(get_db, futas)
    if not sorok:
        return {"hiba": "nincs ilyen futás"}
    utolso = sorok[-1]["subject"].rsplit("· ", 1)[-1]
    if futas in _FUTO or utolso == "fut":
        return {"futas": futas, "allapot": "fut", "indult": sorok[0]["timestamp"], "varhato": varhato_perc()}
    adat = betolt(get_db, futas) or {}
    szoveg = next((r["message"] for r in reversed(sorok) if r["recipient"] == "kommandant"), "")
    ki = {"futas": futas, "allapot": adat.get("allapot") or utolso, "egyoldalas": szoveg,
          "studio": adat.get("studio"), "koltseg": adat.get("koltseg"),
          "delphoi_panel": ((adat.get("tarsadalom") or {}).get("panel") or {}),
          "elorejelzesek": [{k: e.get(k) for k in ("allitas", "valoszinuseg", "hatarido", "hash", "naplo")}
                            for e in adat.get("elorejelzesek") or []]}
    try:
        ki["naplo_ellenorzes"] = naplo_ellenorzes(get_db, futas)
    except Exception as e:  # noqa: BLE001
        ki["naplo_ellenorzes"] = {"ok": False, "hiba": f"{type(e).__name__}: {e}"}
    return ki


# ══ A FELDWEBEL ESZKÖZE ══════════════════════════════════════════════════

ESZKOZ_DEF = {"type": "function", "function": {
    "name": ESZKOZ_NEV,
    "description": (
        "DÖNTÉS-SZIMULÁTOR — „próbáld ki a döntést a világon, mielőtt meghozod”. Egy TERVEZETT lépés (törvényjavaslat, "
        "árazás, nyilatkozat, befektetés, kampány) hatása. ALAPBÓL gazdasági mód: KÖZVETLEN GAZDASÁGI ELŐREJELZÉS "
        "(Makronóm-alapvonal: infláció, GDP, munkanélküliség, alapkamat, árfolyam + a lépés számszerű hatása 80%-os "
        "sávval) és a DELPHOI szintetikus panel (kor, település, iskola, média-étrend szerint), összegzés, kockázati "
        "térkép, módosítási javaslat, mérhető előrejelzések a DELPHOI hash-láncos naplójába. `teljes`=true esetén a "
        "sajtó (Echolot: mit írna holnap a Telex, a 444, a Magyar Nemzet, az Index, a közmédia, a Spiegel, a BBC, a "
        "Global Times) és a jog (Lexonomics) is fut — csak ha a Kommandant teljes szimulációt, sajtót vagy jogot kér. "
        "2–5 perc (teljes: 4–8): az eszköz AZONNAL visszatér, az eredmény külön üzenetben jön (Telegram + "
        "Studio-dokumentum). Akkor hívd, ha a Kommandant egy lépés hatását akarja kipróbálni („szimuláld”, „mi "
        "lenne, ha…”, „próbáld ki”). Szimuláció, nem jóslat."),
    "parameters": {"type": "object", "properties": {
        "lepes": {"type": "string", "description": "A lépés leírása, ahogy a Kommandant megfogalmazta (csak ez)."},
        "horizont": {"type": "string", "description": "Az előrejelzések horizontja, pl. „12 hónap”, „2 év”, „2030” "
                                                      "(elhagyható; alap: 12 hónap)."},
        "teljes": {"type": "boolean", "description": "true: sajtó és jog is (drágább, lassabb). Alap: false."},
    }, "required": ["lepes"]}}}


def eszkoz_definiciok() -> list:
    """A Feldwebel szimulátor-eszköze — csak ha az Anthropic-kulcs megvan (a szimulátor csak azon fut)."""
    return [ESZKOZ_DEF] if llm_kapu.elerheto() else []


def sajat_e(nev: str) -> bool:
    return nev == ESZKOZ_NEV


async def feldwebel_hiv(args: dict, ctx) -> str:
    """A Feldwebel hívása: a futás a háttérben indul, a Feldwebel válasza a NYUGTA; az eredmény a kész-értesítővel
    jön (Telegram-HTML), és a messages-be is bekerül."""
    args = args if isinstance(args, dict) else {}
    deps = {"get_db": ctx.get_db, "siliconflow_api_key": getattr(ctx, "siliconflow_api_key", ""),
            "siliconflow_base_url": getattr(ctx, "siliconflow_base_url", "https://api.siliconflow.com/v1"),
            "siliconflow_timeout": getattr(ctx, "siliconflow_timeout", 60)}

    async def _kesz(r):
        await ctx.telegram_push(telegram_html(r))
    v = inditas(str(args.get("lepes") or ""), str(args.get("horizont") or ""), get_db=ctx.get_db, deps=deps,
                kesz_fn=_kesz, teljes=bool(args.get("teljes")))
    if v.get("hiba"):
        return json.dumps({"error": v["hiba"], **({"futas": v["futas"]} if v.get("futas") else {})},
                          ensure_ascii=False)
    v["teendo"] = ("Mondd meg röviden a Kommandantnak: a szimuláció elindult (azonosító, várható idő), az eredmény "
                   "külön üzenetben jön, a Studio-dokumentum linkjével. Az eredményt NE találgasd.")
    return json.dumps(v, ensure_ascii=False)
