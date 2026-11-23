"""DÖNTÉS-SZIMULÁTOR BACKTESZT (2026-11-22): a szimulátor múltbeli döntéseken, a kimenetel ismeretében, pontozva.

A Kommandant: „az ötlet akkor működik, ha további backtestek is mennek olyan modellel, ahol a knowledge cutoff az
esemény előtt volt.”

A MÓDSZER
---------
1. ESEMÉNY-BANK: múltbeli döntések (`as_of` = a döntés napja), mindegyikhez ELŐRE rögzített, igen/nem kimenetelű
   kérdések (sajtó, piac, jog, társadalom) és a kiértékelési szabályuk. A bank és a GT (`gt_LOCKED.json`) a modellfutás
   ELŐTT commitolva (`orakel_backteszt/szim_backteszt_*`), a PYTHIA-előregisztráció mintájára.
2. IDŐPONT-HŰ ADAT (`asof_adatgyujtok`): a szimulátor `futtat(as_of=…)` hívása ide delegál. Minden motor csak az
   `as_of` ELŐTTI adatot látja; ami nem tehető időpont-hűvé, az a futás `szivargas` listájára kerül, kimondva.
3. KÖTÖTT MODELL (`kotott_chat`): minden LLM-lépés modellje rögzített (`futtat(modellek=…)`). Claude → az LLM-kapu
   szerveroldali tartalék nélkül; régi tudás-határú, nem-Claude modell (pl. `tencent/Hy3`) → SiliconFlow. A futás a
   VALÓBAN futott modelleket rögzíti, és a tisztaság-kapu (`modell_cutoff.futas_tisztasaga`) az összesre kérdez.
4. KÉRDÉS-LÉPÉS (`kerdes_lepes`): a szimulátor kimenetéből (négy motor + összegzés) egy LLM-lépés valószínűséget ad
   az előre rögzített kérdésekre — ugyanazon a kötött modellen, mint az összegzés.
5. PONTOZÁS (`pontoz`, `elo_pontozas`): Brier kérdésenként, összevetve az 50%-os és a bázisarány-alapvonallal; a
   hiányzó válasz kimondva (és egy „hiány = 0,5” sorban is). Ugyanez a pontozó méri az éles `szim:*` ledger-sorokat
   a határidő után — a bináris, a küszöbös és az intervallumos („X és Y közé esik, p=0,8”) előrejelzést is.
   A GAZDASÁGI SÁVOK fő száma a CRPS (`crps_sav`, `paros_crps_kulonbseg`, 11-23): a sávba esés Briere a szélesebb
   sávot jutalmazná; a CRPS a pontosságot és a sáv szűkségét együtt méri. A találati arány csak leírás.

A tisztaság a PONTOZÁSKOR számol a `modell_cutoff.CUTOFF` aktuális soraiból: ha a szonda később tölti ki egy modell
sorát, az ISMERETLEN futás újrafuttatás nélkül kap besorolást. A kimenetel hónapja itt a döntés (as_of) hónapja: a
kimenetel ott kezd kibontakozni, a későbbi hónapok tisztábbak — a legrosszabb eset számít.

FELOLDÁSI SZABÁLY (11-23, `elso_kozles`): a gazdasági sor tényleges értéke a sor `feloldasi_szabaly`-a szerint (alap:
`first_print` — az ELSŐ közölt érték, nem a mai, revideált): az első-közlés-naplóból, amerikai sornál az ALFRED-ből;
ha egyik sincs (régi futás, nem amerikai sor), a mai érték megy, KIMONDVA („revideált érték, első közlés hiányzik”),
és a CRPS külön is megjelenik csak az első közlésen mért sorokra. Az as_of-alapvonal előtörténete ugyanígy a cél-
dátumig ISMERT értékekből épül, ahol a napló vagy az ALFRED ezt lehetővé teszi.

TÁROLÁS: SQLite-tábla nincs (házi szabály). Az élő pontozó a meglévő `delphoi_nowcast_ledger`-t és `messages`-t
olvassa; az első-közlés-napló Postgres (`elso_kozles`, kapcsolóval, alapból KI); a backteszt bemenete-kimenete fájl
az `orakel_backteszt/` alatt (gitben, a bizonyítékbázis része).
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import logging
import math
import os
import random
import re
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import elso_kozles as ek
import llm_kapu
import modell_cutoff

logger = logging.getLogger("szim_backteszt")

VERZIO = "szim-backteszt-v1"


def _d(x) -> date:
    return x if isinstance(x, date) and not isinstance(x, datetime) else date.fromisoformat(str(x)[:10])


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _kanon(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ══ 1. KÖTÖTT MODELL ══════════════════════════════════════════════════════
#
# (b) út: nem-Claude, RÉGI tudás-határú modell a SiliconFlow-on. Ár USD / millió token (bemenet, kimenet): a
# SiliconFlow-konzol az igazság, ez a keret foglalásához és a jelentés becsléséhez kell (a Hy3 már nem nulltarifás).
SF_ARAK = {"tencent/Hy3": (0.35, 1.40)}
SF_ISMERETLEN_AR = (1.00, 4.00)
SF_URL = "https://api.siliconflow.com/v1/chat/completions"
SF_KIMENET_MAX = 16_000


def claude_modell(modell: str | None) -> bool:
    return bool(modell) and str(modell).startswith("claude-")


def becsles_usd(modell: str, bemenet_kar: int, max_ki: int) -> float:
    """Egy kötött modellű hívás legrosszabb ára (a szimulátor keretfoglalásához)."""
    if claude_modell(modell):
        ar = llm_kapu.ARAK.get(modell, llm_kapu.ISMERETLEN_AR)
        ki = max(int(max_ki), llm_kapu.KIMENET_ALSO)
    else:
        ar = SF_ARAK.get(modell, SF_ISMERETLEN_AR)
        ki = min(int(max_ki) * 2, SF_KIMENET_MAX)
    return (bemenet_kar / 3.0 * ar[0] + ki * ar[1]) / 1_000_000


async def sf_chat(payload: dict, modell: str) -> tuple[dict | None, dict | None, list[str]]:
    """Kötött nem-Claude modell a SiliconFlow-on: gondolkodás KI, a válasz `model` mezője a kiszolgáló modell, a
    `usage.koltes_usd` a tokenekből. Néma modellcsere nincs: a hiba hiba marad."""
    import httpx
    kulcs = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    if not kulcs:
        return None, {"code": "nincs_kulcs", "message": "nincs SILICONFLOW_API_KEY", "provider": "siliconflow"}, []
    body = {"model": modell, "messages": payload["messages"], "temperature": 0.3, "thinking": {"type": "disabled"},
            "max_tokens": min(int(payload.get("max_tokens") or 4000) * 2, SF_KIMENET_MAX)}
    hiba: dict | None = None
    for probalkozas in range(3):
        try:
            async with httpx.AsyncClient(timeout=float(os.environ.get("SZIMULATOR_SF_TIMEOUT_S", "") or 300)) as kl:
                v = await kl.post(os.environ.get("SZIMULATOR_SF_URL", "") or SF_URL, json=body,
                                  headers={"Authorization": f"Bearer {kulcs}"})
            if v.status_code == 200:
                d = v.json()
                u = d.get("usage") or {}
                ar = SF_ARAK.get(modell, SF_ISMERETLEN_AR)
                usd = (int(u.get("prompt_tokens") or 0) * ar[0] + int(u.get("completion_tokens") or 0) * ar[1]) / 1e6
                d.setdefault("usage", {})["koltes_usd"] = round(usd, 6)
                d["model"] = d.get("model") or modell
                return d, None, []
            hiba = {"code": f"http_{v.status_code}", "message": v.text[:300], "provider": "siliconflow"}
            if v.status_code in (400, 401, 402, 403, 404):
                break
        except Exception as e:  # noqa: BLE001
            hiba = {"code": "kapu_hiba", "message": f"{type(e).__name__}: {str(e)[:300]}", "provider": "siliconflow"}
        await asyncio.sleep(3 * (probalkozas + 1))
    return None, hiba, []


async def kotott_chat(payload: dict, *, purpose: str, modell: str) -> tuple[dict | None, dict | None, list[str]]:
    """A szimulátor kötött modellű lépése (`dontes_szimulator.KOTOTT_MODELLEK`)."""
    if claude_modell(modell):
        return await llm_kapu.chat(payload, purpose=purpose, modell=modell)
    return await sf_chat(payload, modell)


# ══ 2. IDŐPONT-HŰ ADATGYŰJTÉS ══════════════════════════════════════════════

#: as_of-módban a lap get_news-forrásneve (a „Spiegel” a Tagesspiegelre is illeszkedne, és a 100-as plafonba futna)
ASOF_FORRAS = {"spiegel": "Der Spiegel"}
SAJTO_NAPOK = 3

SZIV_SAJTO = [
    "search_news: csak a MAI naptól visszafelé (1–21 nap) keres, dátumszűrője nincs — as_of-módban helyette a lapok "
    "napi hírfolyama (get_news source+date) az as_of előtti napokra, a téma helyben, kulcsszóval szűrve",
    "source_profile, frame_divergence, regional_framing: a mai naptól visszafelé számolnak (days) — kihagyva; a lap "
    "beállítottsága (lean, trust_tier) a cikkek mai metaadatából jön (statikus forrás-címke, nem a kimenetel)",
]
SZIV_PIAC = [
    "get_macro_indicator period: a MAI adatközlési évjárat (vintage) értéke — az as_of utáni revízió szivároghat "
    "(a Makronóm nem ad as-of vintage-et); a közlési késést konzervatívan kezeljük (havi mutató: as_of hónapja − 2, "
    "negyedéves: a negyedév vége + 60 nap < as_of, kamat: az as_of előtti nap)",
    "forecast: a mai adatokból számol — backteszt-módban kihagyva",
    "forecast_path (a v2 gazdasági ALAPVONALA): as_of paramétere nincs — helyette as_of-hű NAIV pálya: a "
    "macro_history utolsó, as_of-kor már közölt értéke változatlanul, 80%-os sávval a saját, as_of előtti h-lépéses "
    "változásainak 10–90. percentiliséből (10 év). Ez gyengébb alapvonal, mint az élő SARIMAX+IMF/OECD-pálya",
    "forecast_path előtörténete: as_of előtti VINTAGE-ből, ahol van (az első-közlés-napló, amerikai sornál az "
    "ALFRED); különben a MAI, revideált előtörténet — soronként kimondva a pálya `vintage` mezőjében",
    "yfinance: a quote helyett az as_of előtti záróárak (history); az ár = az utolsó as_of előtti záróár, az 52 hetes "
    "sáv az as_of előtti 365 napból",
]
SZIV_JOG = [
    "Lexonomics provision/search: a date = as_of előtti nap szerinti hatályos szöveg; a `states` lista, a `record` és "
    "az aktusok `last_state` mezője (későbbi módosítások léte) kivágva; döntés csak as_of előtti kelttel",
]
SZIV_PANEL = [
    "DELPHOI-panel: helyben (plugins/delphoi.py függvényeivel), az as_of előtti napok lapcímeiből injektált korpusszal; "
    "a persona-priming a MAI országkonfig (a 2026. áprilisi választás utáni helyzet — 2026-05 után érvényes)",
    "politikai helyzet a motorok rendszerpromptjában: a mai DELPHOI-priming (2026-05 után érvényes)",
]


def napi_parameterek(lap: dict, nap: str) -> dict | None:
    """Egy lap egy napjának get_news-paraméterei — a sajtó-motor, a panel-korpusz és az előtöltés UGYANEZT kéri,
    így a felvétel kulcsa mindhármukra azonos. Saját forrásnév nélküli lapnál (v2: a közmédia, amelyet az élő motor
    csak a témakeresés találataiból ismer fel) None: as_of-módban nincs róla adat, kimondva."""
    forras = ASOF_FORRAS.get(lap["kulcs"], lap.get("forras"))
    if not forras:
        return None
    return {"source": forras, "date": nap, "limit": 100, "diversify_results": False}


def napok_elotte(as_of: date, n: int) -> list[str]:
    return [(as_of - timedelta(days=i)).isoformat() for i in range(1, n + 1)]


def cikk_datum(a: dict) -> str:
    return str(a.get("published_at") or "")[:10]


def _tokenek(kifejezes: str) -> list[str]:
    return [t.lower() for t in re.findall(r"\w+", kifejezes or "") if len(t) >= 3]


def tema_illeszkedik(szoveg: str, kifejezes: str) -> bool:
    """Helyi témaszűrő: a kifejezés szavainak legalább a fele (felfelé kerekítve) előfordul a szövegben, a magyar
    toldalékok miatt 5 betűs szótő-előtaggal."""
    tok = _tokenek(kifejezes)
    if not tok:
        return False
    s = (szoveg or "").lower()
    talalat = sum(1 for t in tok if t[:5] in s)
    return talalat >= math.ceil(len(tok) / 2)


def _modusz(ertekek):
    """A leggyakoribb érték (listára is: JSON-alakon számolva)."""
    alakok = [json.dumps(e, ensure_ascii=False, sort_keys=True) for e in ertekek if e not in (None, "", [])]
    if not alakok:
        return None
    return json.loads(max(sorted(set(alakok)), key=alakok.count))


async def sajto_adat(terv: dict, hivo, as_of: date) -> dict:
    """A hét lap as_of ELŐTTI napi hírfolyama (get_news source+date), a témába vágó cikkek helyben szűrve, a profil
    a cikkek metaadatából. Ugyanaz az alak, mint a `dontes_szimulator._sajto_adat`-é."""
    import dontes_szimulator as ds
    async with hivo.echolot_munkamenet():
        napok = napok_elotte(as_of, int(os.environ.get("SZIMULATOR_ASOF_SAJTO_NAPOK", "") or SAJTO_NAPOK))
        lapok, hianyok = [], []
        for lap in ds.LAPOK:
            cikkek = []
            if not napi_parameterek(lap, napok[0]):
                hianyok.append(f"{lap['nev']}: nincs saját forrásneve az Echolotban — as_of-módban nincs adat "
                               "(élőben csak a témakeresés találataiból ismerhető fel)")
                lapok.append({"lap": lap["nev"], "nyelv": lap["nyelv"], "profil": None, "temaba_vago_cikkek": [],
                              "friss_cimek_stilushoz": []})
                continue
            for nap in napok:
                v, hiba = await hivo("echolot", "get_news", napi_parameterek(lap, nap))
                if hiba:
                    hianyok.append(f"get_news ({lap['nev']}, {nap}): {hiba}")
                    continue
                cikkek += [a for a in ds._cikkek(v) if ds.lap_azonosit(a.get("source_name")) == lap["kulcs"]
                           and cikk_datum(a) < as_of.isoformat()]
            cikkek.sort(key=lambda a: str(a.get("published_at") or ""), reverse=True)
            kif = terv["kereses"].get(lap["nyelv"]) or terv["kereses"].get("en") or ""
            temaban = [ds._cikk(a) for a in cikkek
                       if tema_illeszkedik(f"{a.get('title')} {a.get('lead')}", kif)][:4]
            profil = None
            if cikkek:
                profil = {"forrasnev": _modusz([a.get("source_name") for a in cikkek]),
                          "lean": _modusz([a.get("lean") for a in cikkek]),
                          "trust_tier": _modusz([a.get("trust_tier") for a in cikkek]),
                          "spheres": _modusz([a.get("spheres") for a in cikkek]),
                          "cikk_naponta": round(len(cikkek) / max(1, len(napok)), 1),
                          "forras": "get_news cikk-metaadat (as_of előtti napok)"}
            lapok.append({"lap": lap["nev"], "nyelv": lap["nyelv"], "profil": profil, "temaba_vago_cikkek": temaban,
                          "friss_cimek_stilushoz": [] if temaban else [ds._cikk(a) for a in cikkek[:4]]})
            if not cikkek:
                hianyok.append(f"{lap['nev']}: nincs cikk az as_of előtti {len(napok)} napban")
    return {"lapok": lapok, "keretezes_szferankent": None, "regionalis_keretezes": None, "hianyok": hianyok,
            "szivargas": list(SZIV_SAJTO), "as_of": as_of.isoformat(), "napok": napok}


# ── Makronóm, Lexonomics: időpont-hű proxy a szimulátor SAJÁT piac/jog gyűjtője előtt ─────────────────────────────

HAVI_MUTATOK = frozenset({"cpi", "hicp", "core_cpi", "services_cpi", "energy_cpi", "food_cpi", "ppi", "unemployment",
                          "retail_trade", "industrial_production", "wages", "house_prices", "bond_yield_10y"})
MAKRO_MEZOK = ("country", "indicator", "value", "unit", "unit_kind", "period", "status", "source_used", "source_url",
               "measure", "as_of", "effective_from", "period_requested", "period_returned", "aggregation")


def _honap_minusz(d: date, n: int) -> str:
    h = d.year * 12 + (d.month - 1) - n
    return f"{h // 12:04d}-{h % 12 + 1:02d}"


def asof_period(indikator: str, as_of: date) -> str:
    """A konzervatív, közlési késést tisztelő időszak: amit as_of napján már biztosan közöltek."""
    i = (indikator or "").lower()
    if i == "policy_rate":
        return (as_of - timedelta(days=1)).isoformat()
    if i in ("gdp_growth", "gdp", "gov_debt", "current_account"):
        ev, q = as_of.year, (as_of.month - 1) // 3 + 1
        for _ in range(8):
            q -= 1
            if q == 0:
                ev, q = ev - 1, 4
            vege = date(ev, q * 3, 1) + timedelta(days=31)
            vege = vege.replace(day=1) - timedelta(days=1)
            if vege + timedelta(days=60) < as_of:
                return f"{ev}-Q{q}"
    return _honap_minusz(as_of, 2)


def makro_szur(v, as_of: date, kert_period: str):
    """A get_macro_indicator válaszából csak a kért időszak értéke: a `time_series`, `all_attempts`, `cross_check`,
    `valid_through` a MAI napig ér (szivárgás), kivágva; a `measures` csak a kért időszakig."""
    if not isinstance(v, dict) or v.get("hiba"):
        return v
    ki = {k: v.get(k) for k in MAKRO_MEZOK if k in v}
    if isinstance(v.get("measures"), dict):
        ki["measures"] = {k: m for k, m in v["measures"].items()
                          if isinstance(m, dict) and str(m.get("period") or "9999") <= kert_period}
    ef = str(ki.get("effective_from") or "")
    if ef and ef >= as_of.isoformat():
        return {"hiba": f"backteszt: a kapott érték ({ef}) nem as_of előtti — kivágva"}
    return ki


def yf_szur(v, as_of: date):
    """A yfinance-history as_of előtti soraiból a quote alakja (`price`, `52w_low/high`, `last_trade_at`) — ebből
    épül a v2 naiv árfolyam-alapvonala —, plusz az utolsó záróárak."""
    if not isinstance(v, dict) or v.get("hiba"):
        return v
    sorok = [s for s in (v.get("data") or []) if isinstance(s, dict) and s.get("close") is not None
             and str(s.get("date") or "") < as_of.isoformat()]
    if not sorok:
        return {"hiba": "backteszt: nincs as_of előtti árfolyamsor"}
    ev = [float(s["close"]) for s in sorok if s["date"] >= (as_of - timedelta(days=365)).isoformat()]
    skala = lepes_skala([b - a for a, b in zip(ev, ev[1:])], max(map(abs, ev)))
    utolso = sorok[-1]
    harminc = [s for s in sorok if s["date"] >= (as_of - timedelta(days=30)).isoformat()]
    return {"symbol": v.get("symbol"), "source": v.get("source") or "Yahoo Finance (yfinance history)",
            "price": float(utolso["close"]), "last_trade_at": utolso["date"],
            "52w_low": round(min(ev), 4), "52w_high": round(max(ev), 4),
            "valtozas_30_nap_szazalek": (round((float(utolso["close"]) / float(harminc[0]["close"]) - 1) * 100, 2)
                                         if harminc else None),
            "utolso_10_zaras": [{"date": s["date"], "close": s.get("close")} for s in sorok[-10:]],
            "skala_1lepes": skala, "megjegyzes": "csak as_of előtti záróárak (backteszt)"}


#: a v2 forecast_path-mutatói → a macro_history kanonikus sorai (ugyanez a GT forrása is)
PALYA_INDIKATOR = {"inflation": "cpi", "gdp": "gdp_growth", "unemployment": "unemployment"}


def _kovetkezo(per: str) -> str:
    if "Q" in per:
        ev, q = int(per[:4]), int(per[-1])
        return f"{ev + (q == 4)}-Q{q % 4 + 1}"
    ev, h = int(per[:4]), int(per[5:7])
    return f"{ev + (h == 12)}-{h % 12 + 1:02d}"


def _negyedev_vege(per: str) -> date:
    ev, q = int(per[:4]), int(per[-1])
    return date(ev + (q == 4), (q * 3) % 12 + 1, 1) - timedelta(days=1)


def _idoszak_vege(per: str) -> date:
    if "Q" in per:
        return _negyedev_vege(per)
    ev, h = int(per[:4]), int(per[5:7])
    return date(ev + (h == 12), h % 12 + 1, 1) - timedelta(days=1)


def _kvantilis(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))]


def asof_palya(v, mutato: str, as_of: date, hatar: date, *, ismert: dict | None = None) -> dict:
    """As_of-hű NAIV pálya a macro_history válaszából (a forecast_path helyett, annak alakjában): az utolsó, as_of-kor
    már közölt érték változatlanul, 80%-os sávval a sor as_of előtti h-lépéses változásainak 10–90. percentiliséből.

    `ismert` (`elso_kozles.ismert_elotortenet`): {ertekek: {időszak: érték}, forras} — az as_of ELŐTT ismert
    (vintage) értékek; ha megvan, az előtörténet ebből épül, nem a mai, revideált sorból. Nélküle a mai sor megy, és a
    kimenet `vintage` mezője kimondja."""
    if not isinstance(v, dict) or v.get("hiba"):
        return v if isinstance(v, dict) else {"hiba": "macro_history: nincs adat"}
    freq = str(v.get("frequency") or "M")
    utolso_kozolt = asof_period("gdp_growth" if freq == "Q" else "cpi", as_of)
    if ismert and ismert.get("ertekek"):
        forras_obs, vintage = [{"period": p, "value": x} for p, x in ismert["ertekek"].items()], ismert.get("forras")
    else:
        forras_obs, vintage = v.get("observations") or [], None
    obs = [o for o in forras_obs if isinstance(o, dict) and isinstance(o.get("value"), (int, float))
           and ("Q" in str(o.get("period"))) == (freq == "Q") and str(o["period"]) <= utolso_kozolt]
    if len(obs) < 24:
        return {"status": "missing", "reason": f"backteszt: kevés as_of előtti megfigyelés ({len(obs)})"}
    obs = obs[-(40 if freq == "Q" else 120):]
    ertekek = [float(o["value"]) for o in obs]
    utolso = obs[-1]
    valt1 = [ertekek[i + 1] - ertekek[i] for i in range(len(ertekek) - 1)]
    path, per = [], str(utolso["period"])
    for h in range(1, 30):
        per = _kovetkezo(per)
        if _idoszak_vege(per) > hatar:
            break
        valt = valt1 if h == 1 else [ertekek[i + h] - ertekek[i] for i in range(len(ertekek) - h)]
        path.append({"period": per, "value": round(float(utolso["value"]), 3),
                     "low": round(float(utolso["value"]) + _kvantilis(valt, 0.1), 3),
                     "high": round(float(utolso["value"]) + _kvantilis(valt, 0.9), 3), "model": "naiv-asof"})
    return {"status": "ok", "last_actual": {"period": utolso["period"], "value": utolso["value"]}, "path": path,
            "skala_1lepes": lepes_skala(valt1, max(map(abs, ertekek))),
            "method": "backteszt: naiv perzisztencia + empirikus 80%-os sáv (macro_history, as_of előtt közölt adat)",
            "vintage": vintage or "nincs — a mai (revideált) előtörténet",
            "source": f"macro_history {v.get('source') or ''} ({v.get('source_series_id') or mutato})".strip()}


def lex_szur(v, as_of: date):
    if isinstance(v, list):
        return [lex_szur(x, as_of) for x in v]
    if not isinstance(v, dict):
        return v
    ki = {}
    for k, x in v.items():
        if k in ("states", "record", "last_state"):
            continue
        if k == "valid_to" and x and str(x) >= as_of.isoformat():
            ki[k] = None
            continue
        ki[k] = lex_szur(x, as_of)
    return ki


class AsOfHivo:
    """A szimulátor Hivo-ja elé tett, időpont-hű proxy a Makronóm- és a Lexonomics-hívásokhoz: a paramétert as_of-ra
    köti, a választ kivágja, és a nem időpont-hű eszközt (forecast, forecast_path) nem hívja — kimondva."""

    def __init__(self, hivo, as_of: date, hatar: date | None = None, *, naplo="alap"):
        self.hivo = hivo
        self.as_of = as_of
        self.hatar = hatar or as_of + timedelta(days=90)
        self.elotte = (as_of - timedelta(days=1)).isoformat()
        self.naplo = ek.naplo() if naplo == "alap" else naplo      # az első-közlés-napló (KI állásban None)

    async def __call__(self, szerver: str, eszkoz: str, par: dict):
        par = dict(par)
        if szerver == "makronom":
            if eszkoz == "forecast":
                return None, "backteszt: kihagyva — a mai adatokból számol (szivárgás)"
            if eszkoz == "forecast_path":
                mutato = PALYA_INDIKATOR.get(str(par.get("indicator") or ""))
                if not mutato:
                    return None, f"backteszt: ismeretlen pálya-mutató ({par.get('indicator')})"
                v, hiba = await self.hivo(szerver, "macro_history", {"country": par.get("country"), "indicator": mutato,
                                                                     "start": str(self.as_of.year - 12)})
                if hiba:
                    return None, hiba
                ismert = await ek.ismert_elotortenet(self.hivo, str(par.get("country") or ""), mutato, self.as_of,
                                                     naplo=self.naplo)
                v = asof_palya(v, mutato, self.as_of, self.hatar, ismert=ismert)
                return v, (v.get("hiba") or (v.get("reason") if v.get("status") != "ok" else None))
            if eszkoz == "get_macro_indicator":
                par["period"] = asof_period(par.get("indicator"), self.as_of)
                v, hiba = await self.hivo(szerver, eszkoz, par)
                v = makro_szur(v, self.as_of, par["period"])
                return v, hiba or (v.get("hiba") if isinstance(v, dict) else None)
            if eszkoz == "yfinance":
                par = {"symbol": par.get("symbol"), "action": "history", "interval": "1d", "end": self.as_of.isoformat(),
                       "start": (self.as_of - timedelta(days=370)).isoformat()}
                v, hiba = await self.hivo(szerver, eszkoz, par)
                v = yf_szur(v, self.as_of)
                return v, hiba or (v.get("hiba") if isinstance(v, dict) else None)
        if szerver == "lexonomics":
            if par.get("scope") == "decisions":
                par["year_to"] = self.as_of.year
            else:
                par["date"] = self.elotte
            v, hiba = await self.hivo(szerver, eszkoz, par)
            if isinstance(v, dict) and isinstance(v.get("decision_hits"), list):
                v = dict(v, decision_hits=[d for d in v["decision_hits"] if isinstance(d, dict)
                                           and str(d.get("decided") or "9999") < self.as_of.isoformat()])
            return lex_szur(v, self.as_of), hiba
        return await self.hivo(szerver, eszkoz, par)


def asof_adatgyujtok(terv: dict, hivo, as_of: date, ev: int, *, hatar: date) -> dict:
    """A `dontes_szimulator.futtat(as_of=…)` adatgyűjtői: {sajto, piac, jog} → korutint adó hívható (a v2 csak a
    kért motort futtatja). A piac és a jog a szimulátor SAJÁT gyűjtője az időpont-hű proxyn át; a sajtó a lapok as_of
    előtti napi hírfolyamából."""
    import dontes_szimulator as ds
    proxy = AsOfHivo(hivo, as_of, hatar)

    async def _piac():
        d = await ds.piac_adat(terv, proxy, ev, hatar=hatar, ma=as_of)
        return {**d, "szivargas": list(SZIV_PIAC), "as_of": as_of.isoformat()}

    async def _jog():
        d = await ds.jog_adat(terv, proxy)
        return {**d, "szivargas": list(SZIV_JOG), "as_of": as_of.isoformat()}
    return {"sajto": lambda: sajto_adat(terv, hivo, as_of), "piac": _piac, "jog": _jog}


# ══ 3. FELVÉTEL ÉS VISSZAJÁTSZÁS — mindkét modell-út ugyanazt az adatot látja ═══════════════════════════════════

class Felvevo:
    """MCP-hívó (`hiv(szerver, eszkoz, par, max_kar)` → szöveg) fájl-gyorsítótárral. Az első futás élőben hív és
    felvesz; a második modell-út ugyanazokra a hívásokra a FELVETT választ kapja (azonos adat, azonos korpusz-hash).
    Az Echolot egy munkamenetben, szünettel és 429-várakozással megy (névtelen keret: ~30 kérés/perc/IP).
    `eles=False`: csak visszajátszás (teszt, ismételhetőség) — ami nincs felvéve, hiba."""

    def __init__(self, fajl: str | Path | None = None, *, eles: bool = True, koz_s: float = 7.0,
                 varakozas_429_s: float = 40.0):
        self.fajl = Path(fajl) if fajl else None
        self.eles = eles
        self.koz_s = koz_s
        self.varakozas_429_s = varakozas_429_s
        self.tar: dict[str, dict] = {}
        self.uj = 0
        self._kliens = None
        self._utolso = 0.0
        self._zar = asyncio.Lock()
        if self.fajl and self.fajl.exists():
            with gzip.open(self.fajl, "rt", encoding="utf-8") as f:
                self.tar = json.load(f)

    @staticmethod
    def kulcs(szerver: str, eszkoz: str, par: dict) -> str:
        return _sha(_kanon([szerver, eszkoz, par]))

    async def _echolot(self, eszkoz: str, par: dict) -> str:
        import kulso_mcp
        async with self._zar:
            for probalkozas in range(4):
                varj = self._utolso + self.koz_s - time.monotonic()
                if varj > 0:
                    await asyncio.sleep(varj)
                try:
                    if self._kliens is None:
                        k = kulso_mcp._kliens("echolot")
                        await k.__aenter__()
                        self._kliens = k
                    e = await self._kliens.call_tool(eszkoz, par, raise_on_error=False, timeout=150)
                    self._utolso = time.monotonic()
                    szoveg = kulso_mcp._szovegge(e, 2_000_000)
                    if e.is_error:
                        return json.dumps({"hiba": szoveg[:500] or "az eszköz hibát adott"}, ensure_ascii=False)
                    return szoveg
                except Exception as ex:  # noqa: BLE001 — 429 / kapcsolat: új munkamenet, várakozás
                    self._utolso = time.monotonic()
                    await self.zar()
                    logger.info("felvevo: echolot.%s hiba (%s) — %d. újrapróbálás", eszkoz, str(ex)[:80],
                                probalkozas + 1)
                    await asyncio.sleep(self.varakozas_429_s * (probalkozas + 1))
            return json.dumps({"hiba": "Echolot: négy próbálkozás után sem válaszolt"}, ensure_ascii=False)

    async def __call__(self, szerver: str, eszkoz: str, par: dict | None, *, max_kar: int | None = None) -> str:
        import kulso_mcp
        par = dict(par or {})
        k = self.kulcs(szerver, eszkoz, par)
        if k in self.tar:
            return self.tar[k]["valasz"]
        if not self.eles:
            return json.dumps({"hiba": f"nincs felvéve (visszajátszás): {szerver}.{eszkoz}"}, ensure_ascii=False)
        if szerver == "echolot":
            szoveg = await self._echolot(eszkoz, par)
        else:
            szoveg = await kulso_mcp.hiv(szerver, eszkoz, par, max_kar=max_kar or 200_000)
        hibas = szoveg.lstrip().startswith('{"hiba"')
        if not hibas:
            self.tar[k] = {"szerver": szerver, "eszkoz": eszkoz, "parameterek": par, "valasz": szoveg,
                           "felveve": datetime.now(timezone.utc).isoformat()}
            self.uj += 1
            if self.uj % 10 == 0:
                self.ment()
        return szoveg

    def ment(self) -> None:
        if self.fajl:
            self.fajl.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.fajl.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(self.tar, f, ensure_ascii=False, sort_keys=True)
            tmp.replace(self.fajl)

    async def zar(self) -> None:
        k, self._kliens = self._kliens, None
        if k is not None:
            try:
                await k.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass


async def napi_lap(hiv, lap_kulcs: str, nap: str) -> list[dict]:
    """Egy lap egy napjának cikkei (get_news source+date, a lap saját forrásneveire szűrve)."""
    import dontes_szimulator as ds
    lap = next(l for l in ds.LAPOK if l["kulcs"] == lap_kulcs)
    par = napi_parameterek(lap, nap)
    if not par:
        return []
    v = mcp_json(await hiv("echolot", "get_news", par))
    if isinstance(v, dict) and v.get("hiba"):
        raise RuntimeError(f"get_news {lap_kulcs} {nap}: {str(v['hiba'])[:200]}")
    return [a for a in ds._cikkek(v) if ds.lap_azonosit(a.get("source_name")) == lap_kulcs and cikk_datum(a) == nap]


def mcp_json(s):
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return {"hiba": f"nem JSON: {str(s)[:200]}"}


# ══ 4. AS-OF PANEL — a DELPHOI helyben, injektált korpusszal ═══════════════════════════════════════════════════

HU_LAPOK = ("telex", "444", "magyarnemzet", "index")


async def panel_korpusz(hiv, as_of: date, napok: int = SAJTO_NAPOK, max_cim: int = 18) -> dict:
    """A panel hírháttere: a négy magyar lap as_of előtti napjainak címei (a `build_country_corpus` „Friss
    hírfolyam-címek” sorának mintájára). Ugyanazok a get_news-hívások, mint a sajtó-motoré (felvételből)."""
    cimek, lattuk = [], set()
    for nap in napok_elotte(as_of, napok):
        for lap in HU_LAPOK:
            try:
                for a in await napi_lap(hiv, lap, nap):
                    t = (a.get("title") or "").strip()
                    if t and t.lower()[:48] not in lattuk:
                        lattuk.add(t.lower()[:48])
                        cimek.append((str(a.get("published_at") or ""), t))
            except RuntimeError:
                continue
    cimek.sort(reverse=True)
    # laponként egyenletes merítés: a legfrissebbek elöl, legfeljebb max_cim cím
    kivalasztott = [t for _, t in cimek[:max_cim]]
    ctx = ("Friss hírfolyam-címek: " + " · ".join(kivalasztott)) if kivalasztott else ""
    return {"context": ctx, "corpus_hash": _sha(ctx), "n_cim": len(kivalasztott), "as_of": as_of.isoformat()}


async def asof_panel(lepes: str, orszag: str, *, korpusz: dict, modell: str = "tencent/Hy3", n: int = 30,
                     k: int | None = None, seed: int = 42, chat_fn=None, embed_fn=None) -> dict:
    """A DELPHOI fókuszcsoport-ága (concept) helyben, a `plugins/delphoi.py` függvényeivel: kvótás persona-minta →
    Hy3-reakciók (k minta/persona) → SSR (embedding + horgonyok) → `_aggregate`. A korpusz INJEKTÁLT (as_of előtti),
    nem a `press_snapshots` mai sorai. Kimenet: a szimulátor `tarsadalom()`-jának alakja."""
    import numpy as np
    from plugins import delphoi, persona_sampler, pollster, ssr
    if orszag not in delphoi.COUNTRY_PANEL_CONFIG:
        orszag = "HU"
    cfg = delphoi.COUNTRY_PANEL_CONFIG[orszag]
    k = k or delphoi.fg_samples()
    temp = delphoi.panel_temperature()
    dims = delphoi._build_dims(cfg)
    bucket_of = delphoi._media_bucket_map(cfg)
    personas, _ = persona_sampler.sample_personas(dims, n=n, seed=seed)
    ctx_line = f"\n\nA friss hírkörnyezet (háttér): {korpusz['context'][:600]}" if korpusz.get("context") else ""
    sf_kulcs = os.environ.get("SILICONFLOW_API_KEY", "")

    async def _egy(client, p):
        prompt = delphoi._fg_prompt(p, cfg, "concept", lepes) + ctx_line
        out = []
        for _ in range(k):
            try:
                out.append(await chat_fn(prompt) if chat_fn else
                           await pollster._chat(client, prompt, temperature=temp, model=modell))
            except Exception:  # noqa: BLE001 — a persona mérése így nem teljes
                out.append(None)
        return out
    if chat_fn:
        valaszok = [await _egy(None, p) for p in personas]
    else:
        import httpx
        sem = asyncio.Semaphore(8)
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {sf_kulcs}"}, timeout=90) as client:
            async def _korlat(p):
                async with sem:
                    return await _egy(client, p)
            valaszok = await asyncio.gather(*(_korlat(p) for p in personas))
    teljes = [(p, ts) for p, ts in zip(personas, valaszok) if len(ts) == k and all(ts)]
    if len(teljes) < delphoi.FG_MIN_COMPLETION * len(personas):
        return {"ok": False, "hiba": f"a panel kitöltése {len(teljes)}/{len(personas)} — részeredmény nem termék",
                "n": n, "orszag": orszag}
    szovegek = [t for _, ts in teljes for t in ts]
    horgonyok = delphoi.REFERENCE_SETS_APPEAL.get(cfg["lang"]) or delphoi.REFERENCE_SETS_APPEAL["hu"]
    deps = {"siliconflow_api_key": sf_kulcs, "siliconflow_base_url": "https://api.siliconflow.com/v1",
            "siliconflow_timeout": 90}
    _embed = embed_fn or (lambda ts: delphoi._default_embed_fn(ts, deps))
    pmf = ssr.compute_pmf(np.asarray(await _embed(szovegek), dtype=float),
                          np.asarray(await _embed(list(horgonyok)), dtype=float), method="linear")
    pontok = [float(x) for x in ssr.score_pmf(pmf)]
    rows, i = [], 0
    for idx, (p, ts) in enumerate(teljes):
        sajat = pontok[i:i + len(ts)]
        i += len(ts)
        rows.append({"persona_idx": idx, "segment": bucket_of.get(p.get("media", ""), "egyéb"), "variant_id": None,
                     "ssr_score": float(np.mean(sajat)), "choice": None, "unclear": None,
                     "dims": {d: p.get(d) for d in delphoi.BONTAS_DIMENZIOK if p.get(d)}})
    agg = delphoi._aggregate("concept", rows)
    embed_modell = "text-embedding-3-small" if os.environ.get("OPENAI_API_KEY") else delphoi.EMBED_MODEL_SF
    agg["panel"] = {"country": orszag, "n_requested": len(personas), "n_completed": len(teljes),
                    "model_id": f"{modell}|non-think|temp={temp}|k={k}|ssr=linear", "embed_model": embed_modell,
                    "corpus_hash": korpusz.get("corpus_hash"), "as_of": korpusz.get("as_of"),
                    "panel_version": f"{delphoi.FG_PANEL_VERSION}|asof"}
    agg["disclaimer"] = "Szintetikus panel relatív jelzése, nem abszolút mérés és nem közvélemény-kutatás."
    return {"ok": True, "job_id": f"asof-{korpusz.get('corpus_hash', '')[:10]}", "kredit": 0, "feltoltes": 0,
            "felhasznalo": "helyi-backteszt", "orszag": orszag, "n": n, "eredmeny": agg,
            "szivargas": list(SZIV_PANEL)}


# ══ 5. KÉRDÉS-LÉPÉS ════════════════════════════════════════════════════════

KERDES_CEL = "szimulator/backteszt-kerdesek"
KERDES_KIMENET = 3_000


def _kerdes_user(r: dict, esemeny: dict, kerdesek: list[dict]) -> str:
    import dontes_szimulator as ds
    piac_adat = ((r.get("piac") or {}).get("adat") or {}).get("adatok") or []
    arak = [a for a in piac_adat if a.get("eszkoz") == "yfinance"]
    kivonat = ds._motor_kivonat(r)
    return f"""A DÖNTÉS (ma, {r.get('as_of')} jelentették be):
\"\"\"{r.get('lepes')}\"\"\"

A SZIMULÁCIÓ EREDMÉNYE (a négy motor kivonata, JSON):
{ds._json_dump(kivonat, 26_000)}

A PIACI ÁRAK A DÖNTÉS ELŐTT (yfinance, csak a mai nap előtti záróárak):
{ds._json_dump(arak, 5_000)}

A GAZDASÁGI TÁBLA (alapvonal + a lépés számszerű hatása, 80%-os sáv):
{ds._json_dump([{k: t.get(k) for k in ('mutato', 'orszag', 'idoszak', 'ertek', 'also', 'felso', 'dontessel', 'dontessel_also', 'dontessel_felso', 'egyseg')} for t in r.get('gazdasag') or []], 5_000)}

A SZIMULÁCIÓ SAJÁT, SZABAD ELŐREJELZÉSEI:
{ds._json_dump([{k: e.get(k) for k in ('terulet', 'allitas', 'valoszinuseg', 'hatarido')} for e in r.get('elorejelzesek') or []], 4_000)}

ELŐRE RÖGZÍTETT KÉRDÉSEK (mindegyikre adj valószínűséget, hogy IGAZ lesz):
{ds._json_dump([{k: q[k] for k in ('id', 'terulet', 'kerdes', 'hatarido')} for q in kerdesek], 8_000)}

Add vissza: {{"valaszok": [{{"id": "a kérdés azonosítója", "valoszinuseg": 0.01–0.99,
                           "indok": "legfeljebb 25 szó: melyik motor melyik jele"}}]}}
Minden kérdés szerepeljen, pontosan egyszer."""


async def kerdes_lepes(r: dict, esemeny: dict, kerdesek: list[dict], *, modell: str | None,
                       keret=None) -> tuple[dict, dict]:
    """Valószínűség az előre rögzített kérdésekre, a szimulátor kimenetéből. `(valaszok{id: {p, indok}}, info)`."""
    import dontes_szimulator as ds
    system = ("Te a Claus-Bridge döntés-szimulátorának előrejelzője vagy. A feladat: a szimuláció eredményéből "
              "valószínűséget adni előre rögzített, igen/nem kimenetelű kérdésekre. Csak a kapott adatokra és a "
              "szimulációra támaszkodj; a kimenetel a jövőben van. Ha az adat kevés, a valószínűség legyen 0,5 "
              f"közelében, és ezt mondd ki az indokban. Mai dátum: {r.get('as_of')}. "
              "A válaszod EGYETLEN JSON-objektum, magyarázó szöveg és kódblokk nélkül.")
    user = _kerdes_user(r, esemeny, kerdesek)
    payload = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "max_tokens": KERDES_KIMENET}
    becsles = becsles_usd(modell or "claude-sonnet-5-5", len(system) + len(user), KERDES_KIMENET)
    if keret is not None and not keret.foglal(becsles, "kerdesek"):
        return {}, {"kihagyva": "a költségkeret nem fedezte"}
    if modell:
        data, err, notes = await kotott_chat(payload, purpose=KERDES_CEL, modell=modell)
    else:
        data, err, notes = await llm_kapu.chat(payload, purpose=KERDES_CEL)
    valos = float(((data or {}).get("usage") or {}).get("koltes_usd") or (err or {}).get("koltes_usd") or 0.0)
    if keret is not None:
        keret.lezar(becsles, valos, "kerdesek")
    info = {"modell": (data or {}).get("model"), "kert_modell": modell, "koltes_usd": round(valos, 5),
            "szolgaltato": "anthropic" if (modell is None or claude_modell(modell)) else "siliconflow"}
    if notes:
        info["megjegyzes"] = list(notes)
    if err is not None:
        info["hiba"] = f"{err.get('code')}: {str(err.get('message') or '')[:300]}"
        return {}, info
    szoveg = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    obj = ds.json_ki(szoveg) or {}
    ervenyes = {q["id"] for q in kerdesek}
    ki: dict[str, dict] = {}
    for v in obj.get("valaszok") or []:
        if not isinstance(v, dict) or v.get("id") not in ervenyes or v["id"] in ki:
            continue
        try:
            p = float(v.get("valoszinuseg"))
        except (TypeError, ValueError):
            continue
        if 0.0 <= p <= 1.0:
            ki[v["id"]] = {"p": round(min(0.99, max(0.01, p)), 3), "indok": " ".join(str(v.get("indok") or "").split())[:300]}
    hiany = sorted(ervenyes - set(ki))
    if hiany:
        info["hianyzo_valasz"] = hiany
    if not obj:
        info["nyers"] = szoveg[:2000]
    return ki, info


# ══ 6. TISZTASÁG ═══════════════════════════════════════════════════════════

#: Az SSR-embedding (szöveg → horgony-hasonlóság) nem generál tartalmat; a kapuban nem szerepel, de a futás rögzíti.
NEM_GENERATIV = ("Qwen/Qwen3-Embedding-8B", "text-embedding-3-small")


def hasznalt_modellek(r: dict, kerdes_info: dict | None = None) -> list[str]:
    """A futás MINDEN generatív LLM-lépésének valóban futott modellje (a kért modell, ha a válasz nem nevezi meg)."""
    infok = [r.get("terv_info"), (r.get("sajto") or {}).get("info"), (r.get("piac") or {}).get("info"),
             (r.get("jog") or {}).get("info"), r.get("osszegzes_info"), r.get("kockazat_info"), kerdes_info]
    modellek = set()
    for i in infok:
        if isinstance(i, dict) and not i.get("kihagyva") and (i.get("modell") or i.get("kert_modell")):
            modellek.add(str(i.get("modell") or i.get("kert_modell")))
    panel = (((r.get("tarsadalom") or {}).get("kivonat") or {}).get("modell")) or ""
    if panel:
        modellek.add(panel.split("|", 1)[0])
    return sorted(modellek)


def kimenetel_honapja(esemeny: dict) -> str:
    return str(esemeny["as_of"])[:7]


def tisztasag(modellek: list[str], esemeny: dict) -> dict:
    honap = kimenetel_honapja(esemeny)
    return {"tisztasag": modell_cutoff.futas_tisztasaga(modellek, honap), "honap": honap,
            "lepesenkent": {m: modell_cutoff.tisztasag(m, honap) for m in modellek}}


# ══ 7. PONTOZÓ ══════════════════════════════════════════════════════════════

def brier(p: float, o: int) -> float:
    return (float(p) - float(o)) ** 2


_SZAM = r"(-?\d+(?:[.,]\d+)?)"


def _szam(s) -> float:
    return float(str(s).replace(",", "."))


def kuszob_ertelmez(szoveg: str) -> dict | None:
    """„> 3,8%”, „≤ 2.5”, „3,2–4,1”, „3,2 és 4,1 között” → {tipus, ...}; ha nem értelmezhető: None."""
    s = " ".join(str(szoveg or "").split())
    m = re.search(_SZAM + r"\s*(?:%|százalék)?\s*(?:–|-|—|és)\s*" + _SZAM, s)
    if m and ("között" in s or "–" in s or "—" in s or re.search(r"\d\s*-\s*\d", s)):
        a, b = sorted((_szam(m.group(1)), _szam(m.group(2))))
        return {"tipus": "intervallum", "also": a, "felso": b}
    m = re.search(r"(>=|<=|≥|≤|>|<)\s*" + _SZAM, s)
    if m:
        jel = {"≥": ">=", "≤": "<="}.get(m.group(1), m.group(1))
        return {"tipus": "kuszob", "irany": jel, "kuszob": _szam(m.group(2))}
    m = re.fullmatch(r"=\s*" + _SZAM + r"\s*(?:%|Ft|százalék)?", s)
    if m:                                   # „= 5,5%”: pontos érték (pl. változatlan kamat)
        x = _szam(m.group(1))
        return {"tipus": "intervallum", "also": x, "felso": x}
    return None


def kimenetel(spec: dict, ertek: float | None = None) -> int | None:
    """Egy előrejelzés teljesült-e (1/0), a mért érték alapján. `spec`:
    {"tipus": "binaris", "o": 0|1} · {"tipus": "intervallum", "also": x, "felso": y} (zárt; egyik vége lehet None) ·
    {"tipus": "kuszob", "irany": ">"|">="|"<"|"<=", "kuszob": x}. Érték nélkül (nem binárisnál): None."""
    t = spec.get("tipus")
    if t == "binaris":
        return None if spec.get("o") is None else int(bool(spec["o"]))
    if ertek is None:
        return None
    x = float(ertek)
    if t == "intervallum":
        a, b = spec.get("also"), spec.get("felso")
        return int((a is None or x >= float(a)) and (b is None or x <= float(b)))
    if t == "kuszob":
        k, irany = float(spec["kuszob"]), spec.get("irany", ">")
        return int({">": x > k, ">=": x >= k, "<": x < k, "<=": x <= k}[irany])
    raise ValueError(f"ismeretlen előrejelzés-típus: {t}")


# ══ 7b. CRPS — a gazdasági sávok helyes pontszáma ══════════════════════════════════════════════════════════════
#
# A sávba esés (1/0) Briere a szélesebb sávot jutalmazza: 97%-os találatnál 0,058, a pontosan kalibrált 80%-os sávnál
# 0,16. A CRPS (continuous ranked probability score) a sávból épített eloszlást pontozza: ∫(F(y) − 1{y ≥ x})² dy. A
# pontosság és a sáv szűkössége együtt számít, és proper: a várható pontszámot a valódi meggyőződés közlése
# minimalizálja. Kisebb = jobb; egysége a mutatóé. Minta: ThesisInstitute/thesis (MIT) — `scoreNumericCdfDistribution`,
# `pairedNormalizedCrpsDelta`, `scripts/kalshi_compare/scoring.py` (`sign_flip_p`, `bootstrap_ci`).

SAV_P = 0.8                       # a v2 gazdasági sávjainak névleges lefedése (`gazdasagi_tabla`)
SKALA_RELATIV_PADLO = 1e-12       # a minta `NORMALIZATION_SCALE_RELATIVE_FLOOR`-ja
PROBA_SEED = 20261019
PROBA_PONTOS_HATAR = 16           # ennyi elemig az előjelcsere pontos (2^n eset), fölötte Monte Carlo
PROBA_MC = 20_000
PROBA_BOOTSTRAP = 10_000
_N01 = statistics.NormalDist()


def crps_normal(mu: float, sigma: float, x: float) -> float:
    """Zárt képlet: CRPS(N(μ, σ²), x) = σ·[z(2Φ(z) − 1) + 2φ(z) − 1/√π], z = (x − μ)/σ. σ = 0: |x − μ| (pontbecslés)."""
    if sigma <= 0:
        return abs(x - mu)
    z = (x - mu) / sigma
    return sigma * (z * (2 * _N01.cdf(z) - 1) + 2 * _N01.pdf(z) - 1 / math.sqrt(math.pi))


def crps_sav(also, felso, p: float, tenyleges) -> float | None:
    """CRPS a központi `p`-intervallumból [also, felso] épített normál eloszlásra: közép = a sáv közepe,
    σ = szélesség / (2·z₍₁₊ₚ₎/₂). also = felso: pontbecslés (σ = 0) → |tény − érték|. Nyitott vagy hiányzó sávvég → None."""
    if also is None or felso is None or tenyleges is None:
        return None
    if not 0 < p < 1:
        raise ValueError(f"a sáv valószínűsége 0 és 1 közé esik, nem {p}")
    a, b = sorted((float(also), float(felso)))
    return crps_normal((a + b) / 2, (b - a) / (2 * _N01.inv_cdf((1 + p) / 2)), float(tenyleges))


def lepes_skala(valt: list[float], nagysag: float = 0.0) -> float | None:
    """A normalizálás skálája: a cél ELŐTTI időszakok egylépéses változásainak mintaszórása (n − 1). None, ha kevesebb
    mint 2 változás van, vagy a szórás a sor nagyságának 1e-12-ed része alatt (lapos sor: maradék, nem nevező). Az
    előrejelzés (a sáv) sosem bemenet: különben a szélesebb sáv a saját normalizált hibáját zsugorítaná."""
    if len(valt) < 2:
        return None
    s = statistics.stdev(valt)
    return s if math.isfinite(s) and s > abs(nagysag) * SKALA_RELATIV_PADLO else None


def paros_crps_kulonbseg(sorok: list[dict]) -> dict:
    """sorok: [{id (célpont), esemeny, crps_dontessel, crps_alap, skala}]. Célpontonként (CRPS_döntéssel − CRPS_alap)
    / skála (több futásnál előbb a célponton belül átlagolva), utána átlag. NEGATÍV = jobbak vagyunk az alapvonalnál. A
    skála nélküli célpont csak a (skálafüggetlen) jobb/rosszabb számlálásba megy. p: előjelcserés próba célpontonként
    és eseményenként (fürt: egy futás sorai összefüggnek); 95%-os CI: eseményfürtös bootstrap."""
    cel: dict[str, dict] = {}
    for s in sorok:
        if s.get("crps_dontessel") is None or s.get("crps_alap") is None:
            continue
        c = cel.setdefault(str(s["id"]), {"d": [], "a": [], "skala": s.get("skala"), "esemeny": s.get("esemeny")})
        c["d"].append(float(s["crps_dontessel"]))
        c["a"].append(float(s["crps_alap"]))
    ki: dict = {"n": len(cel), "jobb_db": 0, "rosszabb_db": 0, "dontetlen_db": 0, "skala_nelkul": []}
    delta, furt = [], []
    for k, c in sorted(cel.items()):
        d, a = statistics.fmean(c["d"]), statistics.fmean(c["a"])
        ki["jobb_db" if d < a - 1e-12 else "rosszabb_db" if d > a + 1e-12 else "dontetlen_db"] += 1
        if c["skala"] and c["skala"] > 0:
            delta.append((d - a) / float(c["skala"]))
            furt.append(str(c["esemeny"] or k))
        else:
            ki["skala_nelkul"].append(k)
    ki["n_norm"] = len(delta)
    if delta:
        lo, hi = bootstrap_ci(delta, furt)
        ki.update(kulonbseg_norm=round(statistics.fmean(delta), 4), ci95=[round(lo, 4), round(hi, 4)],
                  p_celpontonkent=round(elojelcseres_p(delta), 4), p_esemenyenkent=round(elojelcseres_p(delta, furt), 4),
                  n_esemeny=len(set(furt)))
    return ki


def elojelcseres_p(ertekek: list[float], furtok: list[str] | None = None, *, seed: int = PROBA_SEED) -> float:
    """Kétoldalú randomizációs p a 0 átlagra, előjel-szimmetria mellett (a minta `sign_flip_p`-je). Fürtökkel a fürt
    összege cserél előjelet együtt. ≤16 egységig pontos, fölötte Monte Carlo (Phipson–Smyth: a megfigyelt eset is számít)."""
    n = len(ertekek)
    if n == 0:
        return float("nan")
    if furtok is None:
        egysegek = list(ertekek)
    else:
        g: dict[str, float] = {}
        for f, v in zip(furtok, ertekek):
            g[f] = g.get(f, 0.0) + v
        egysegek = [g[f] for f in sorted(g)]
    megfigyelt = abs(sum(egysegek)) / n - 1e-12
    m = len(egysegek)
    if m <= PROBA_PONTOS_HATAR:
        talalat = 0
        for maszk in range(2 ** m):
            if abs(sum(-v if maszk >> i & 1 else v for i, v in enumerate(egysegek))) / n >= megfigyelt:
                talalat += 1
        return talalat / 2 ** m
    rng = random.Random(seed)
    talalat = 1
    for _ in range(PROBA_MC):
        if abs(sum(v if rng.random() < 0.5 else -v for v in egysegek)) / n >= megfigyelt:
            talalat += 1
    return talalat / (PROBA_MC + 1)


def bootstrap_ci(ertekek: list[float], furtok: list[str] | None = None, *, huzas: int = PROBA_BOOTSTRAP,
                 seed: int = PROBA_SEED, szint: float = 0.95) -> tuple[float, float]:
    """Percentilis-intervallum az átlagra; fürtökkel a fürtöket húzza visszatevéssel (a minta `bootstrap_ci`-je)."""
    if not ertekek:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    csop: dict[str, list[float]] = {}
    for f, v in zip(furtok or [str(i) for i in range(len(ertekek))], ertekek):
        csop.setdefault(f, []).append(v)
    kulcsok = sorted(csop)
    atlagok = []
    for _ in range(huzas):
        minta = [v for _ in kulcsok for v in csop[kulcsok[rng.randrange(len(kulcsok))]]]
        atlagok.append(sum(minta) / len(minta))
    atlagok.sort()
    return (atlagok[int(math.floor((1 - szint) / 2 * huzas))],
            atlagok[min(int(math.ceil((1 + szint) / 2 * huzas)) - 1, huzas - 1)])


def crps_mezok(t: dict, x: float) -> dict:
    """Egy gazdasági tábla-sor CRPS-mezői a mért értékre. A döntéssel: a 80%-os sáv. Az alapvonal: a saját 80%-os sávja
    (pályás mutató), sáv nélküli naiv alapvonalnál (alapkamat, árfolyam) a kimondott pontérték (σ = 0: CRPS = |hiba|)."""
    savos = not t.get("naiv") and t.get("also") is not None and t.get("felso") is not None
    d = crps_sav(t.get("dontessel_also"), t.get("dontessel_felso"), SAV_P, x) if t.get("hatas") else None
    a = (crps_sav(t["also"], t["felso"], SAV_P, x) if savos
         else crps_sav(t.get("ertek"), t.get("ertek"), SAV_P, x))
    sk = t.get("skala")
    ki = {"crps_dontessel": None if d is None else round(d, 5), "crps_alap": None if a is None else round(a, 5),
          "alap_eloszlas": "sav80" if savos else "pont (naiv, sáv nélkül)", "skala": sk, "crps_kulonbseg_norm": None}
    if d is not None and a is not None and sk:
        ki["crps_kulonbseg_norm"] = round((d - a) / float(sk), 4)
    return ki


def _osszesit(sorok: list[dict]) -> dict:
    """Brier a megválaszolt kérdéseken + két alapvonal (50% és leave-one-out bázisarány), skill, hiányok."""
    pontozott = [s for s in sorok if s.get("o") is not None]
    valaszolt = [s for s in pontozott if s.get("p") is not None]
    hiany = [s["id"] for s in pontozott if s.get("p") is None]
    ki = {"n_kerdes": len(pontozott), "n_valaszolt": len(valaszolt), "hianyzo": hiany,
          "n_gt_nelkul": len([s for s in sorok if s.get("o") is None])}
    if not valaszolt:
        return ki
    osszes_o = [s["o"] for s in pontozott]

    def _bazis(s):                     # a TÖBBI pontozott kérdés kimenetel-aránya (a sajátját nem látja)
        tobbi = len(osszes_o) - 1
        return 0.5 if tobbi <= 0 else (sum(osszes_o) - s["o"]) / tobbi
    b = sum(brier(s["p"], s["o"]) for s in valaszolt) / len(valaszolt)
    b50 = 0.25
    bbazis = sum(brier(_bazis(s), s["o"]) for s in valaszolt) / len(valaszolt)
    b_hiany05 = (sum(brier(s["p"], s["o"]) for s in valaszolt) + 0.25 * len(hiany)) / len(pontozott)
    ki.update(brier=round(b, 4), brier_alap_50=b50, brier_alap_bazis=round(bbazis, 4),
              skill_50=round(1 - b / b50, 3), skill_bazis=round(1 - b / bbazis, 3) if bbazis > 0 else None,
              brier_hiany_05=round(b_hiany05, 4), bazisarany=round(sum(osszes_o) / len(osszes_o), 3),
              talalati_arany=round(sum(1 for s in valaszolt if (s["p"] >= 0.5) == bool(s["o"])) / len(valaszolt), 3))
    return ki


def pontoz(sorok: list[dict]) -> dict:
    """sorok: [{id, esemeny, terulet, p (None = nincs válasz), o (None = nincs GT), tisztasag}].
    Kimenet: összesen, tisztaság szerint (CLEAN külön sor), terület szerint."""
    csoport: dict[str, list] = {}
    for s in sorok:
        csoport.setdefault(s.get("tisztasag") or "ISMERETLEN", []).append(s)
    terulet: dict[str, list] = {}
    for s in sorok:
        terulet.setdefault(s.get("terulet") or "?", []).append(s)
    return {"osszesen": _osszesit(sorok), "tisztasag_szerint": {k: _osszesit(v) for k, v in sorted(csoport.items())},
            "csak_clean": _osszesit(csoport.get("CLEAN", [])),
            "terulet_szerint": {k: _osszesit(v) for k, v in sorted(terulet.items())}}


def elorejelzes_spec(e: dict) -> dict | None:
    """Egy éles szimulátor-előrejelzés kiértékelési alakja: strukturált mezőkből (also/felso, kuszob+irany), vagy a
    `kuszob` szövegéből („> 3,8%”, „3,2–4,1”)."""
    if e.get("also") is not None or e.get("felso") is not None:
        return {"tipus": "intervallum", "also": e.get("also"), "felso": e.get("felso")}
    if isinstance(e.get("kuszob"), (int, float)):
        return {"tipus": "kuszob", "kuszob": e["kuszob"], "irany": e.get("irany", ">")}
    return kuszob_ertelmez(e.get("kuszob") or "")


def elo_pontozas(get_db, kimenetelek: dict, *, ma: date | None = None) -> dict:
    """Az ÉLES `szim:*` ledger-sorok pontozása a határidő után, UGYANAZZAL a pontozóval.

    `kimenetelek`: entity_key → {"o": 0|1} (bináris, kimondott kimenetel) vagy {"ertek": szám} (a mért érték; az
    előrejelzés küszöbe/intervalluma a messages-beli futásadatból), + "forras". A valószínűség a ledger `direction`-je.
    A lánc épségét és a messages-beli szöveg egyezését a szimulátor `naplo_ellenorzes`-e méri futásonként."""
    import dontes_szimulator as ds
    ma = ma or datetime.now(timezone.utc).date()
    conn = get_db()
    try:
        sorok = [dict(x) for x in conn.execute(
            "SELECT entity_key, target_window, direction, model_id, predicted_at FROM delphoi_nowcast_ledger "
            "WHERE entity_key LIKE 'szim:%' ORDER BY id").fetchall()]
    finally:
        conn.close()
    futasok: dict[str, dict] = {}
    pont, gazd, nyitott, nem_esedekes, ellenorzes = [], [], [], 0, {}
    for s in sorok:
        _, futas, nn = s["entity_key"].split(":", 2)
        if str(s["target_window"])[:10] > ma.isoformat():
            nem_esedekes += 1
            continue
        if futas not in futasok:
            futasok[futas] = ds.betolt(get_db, futas) or {}
            try:
                ellenorzes[futas] = ds.naplo_ellenorzes(get_db, futas).get("ok")
            except Exception:  # noqa: BLE001
                ellenorzes[futas] = None
        e = next((x for x in futasok[futas].get("elorejelzesek") or []
                  if (x.get("naplo") or {}).get("entity_key") == s["entity_key"]), {})
        km = kimenetelek.get(s["entity_key"])
        if not km:
            nyitott.append(s["entity_key"])
            continue
        if "o" in km:
            o = kimenetel({"tipus": "binaris", "o": km["o"]})
        else:
            spec = elorejelzes_spec(e)
            o = kimenetel(spec, km.get("ertek")) if spec else None
            if o is None:
                nyitott.append(f"{s['entity_key']} (a küszöb nem értelmezhető: „{e.get('kuszob')}”)")
                continue
            t = gazdasagi_tabla_sor(futasok[futas], e)
            if t and t.get("hatas"):        # a v2 gazdasági sávja: a fő szám a CRPS, nem a sávba esés Briere
                gazd.append({"id": s["entity_key"], "esemeny": futas, "kulcs": t.get("kulcs"),
                             **({"tenyleges_jeloles": km["jeloles"]} if km.get("jeloles") else {}),
                             **crps_mezok(t, float(km["ertek"]))})
        pont.append({"id": s["entity_key"], "esemeny": futas, "terulet": e.get("terulet"), "p": float(s["direction"]),
                     "o": o, "tisztasag": "ELES"})
    return {"pontszam": pontoz(pont), "gazdasagi_crps": crps_csoportok(gazd), "gazdasagi_sorok": gazd,
            "pontozott": len(pont), "nyitott_esedekes": nyitott, "nem_esedekes": nem_esedekes,
            "naplo_ellenorzes": ellenorzes, "ma": ma.isoformat()}


def gazdasagi_tabla_sor(adat: dict, e: dict) -> dict | None:
    """Az éles futás gazdasági táblájának az a sora, amelyből a v2 intervallumos előrejelzése készült (az állítás
    „<mutató> (<ország> <időszak>): …”-tal kezdődik, `dontes_szimulator.gazdasagi_elorejelzesek`)."""
    for t in adat.get("gazdasag") or []:
        hol = f"{t['orszag']} " if t.get("orszag") else ""
        if str(e.get("allitas") or "").startswith(f"{t.get('mutato')} ({hol}{t.get('idoszak')})"):
            return t
    return None


async def elo_gazdasagi_kimenetelek(get_db, hiv, *, ma: date | None = None) -> dict:
    """Az éles `szim:*` sorok közül a v2 GAZDASÁGI (intervallumos) előrejelzéseinek mért értéke, automatikusan: a
    messages-ben tárolt futás `gazdasag` táblájának sorához illesztve (a v2 állítása „<mutató> (<ország> <időszak>):
    …”-tal kezdődik), a határidő után. Kimenet az `elo_pontozas` `kimenetelek` alakjában: {entity_key: {ertek, forras}}."""
    import dontes_szimulator as ds
    ma = ma or datetime.now(timezone.utc).date()
    conn = get_db()
    try:
        sorok = [dict(x) for x in conn.execute(
            "SELECT entity_key, target_window FROM delphoi_nowcast_ledger WHERE entity_key LIKE 'szim:%'").fetchall()]
    finally:
        conn.close()
    futasok: dict[str, dict] = {}
    ki = {}
    for s in sorok:
        if str(s["target_window"])[:10] > ma.isoformat():
            continue
        futas = s["entity_key"].split(":")[1]
        adat = futasok.setdefault(futas, ds.betolt(get_db, futas) or {})
        e = next((x for x in adat.get("elorejelzesek") or []
                  if (x.get("naplo") or {}).get("entity_key") == s["entity_key"]), None)
        if not e:
            continue
        t = gazdasagi_tabla_sor(adat, e)
        if t and t.get("hatas"):
            m = await gazdasagi_tenyleges(hiv, dict(t, feloldasi_szabaly=e.get("feloldasi_szabaly")
                                                    or t.get("feloldasi_szabaly")), ma=ma)
            if m.get("ertek") is not None:
                ki[s["entity_key"]] = {"ertek": m["ertek"], "forras": m.get("forras"),
                                       "feloldasi_szabaly": m.get("feloldasi_szabaly"), "feloldas": m.get("feloldas"),
                                       **({"jeloles": m["jeloles"]} if m.get("jeloles") else {})}
    return ki


# ══ 8. GT-FELOLDÁS (a bank kérdéseinek mért kimenetele) ═══════════════════════════════════════════════════════

async def _zarasok(hiv, symbol: str, tol: date, ig: date) -> list[dict]:
    v = mcp_json(await hiv("makronom", "yfinance", {"symbol": symbol, "action": "history", "interval": "1d",
                                                   "start": tol.isoformat(), "end": (ig + timedelta(days=1)).isoformat()}))
    if not isinstance(v, dict) or v.get("hiba") or not v.get("data"):
        raise RuntimeError(f"yfinance {symbol}: {str((v or {}).get('hiba') or 'nincs adat')[:200]}")
    return [s for s in v["data"] if s.get("close") is not None]


async def zaras(hiv, symbol: str, legkesobb: date, *, szigoruan_elotte: bool = False) -> dict:
    """Az utolsó záróár legkésőbb a megadott napon (vagy szigorúan előtte)."""
    sorok = await _zarasok(hiv, symbol, legkesobb - timedelta(days=12), legkesobb)
    jo = [s for s in sorok if (s["date"] < legkesobb.isoformat() if szigoruan_elotte
                               else s["date"] <= legkesobb.isoformat())]
    if not jo:
        raise RuntimeError(f"{symbol}: nincs záróár {legkesobb} {'előtt' if szigoruan_elotte else 'ig'}")
    return {"date": jo[-1]["date"], "close": float(jo[-1]["close"])}


async def kamat(hiv, orszag: str, nap: date) -> dict:
    v = mcp_json(await hiv("makronom", "get_macro_indicator", {"country": orszag, "indicator": "policy_rate",
                                                               "period": nap.isoformat()}))
    if not isinstance(v, dict) or v.get("value") is None:
        raise RuntimeError(f"kamat {orszag} {nap}: {str(v)[:200]}")
    return {"value": float(v["value"]), "effective_from": v.get("effective_from"), "source": v.get("source_used")}


async def gt_felold(hiv, kerdes: dict, esemeny: dict) -> dict:
    """Egy kérdés kimenetele a rögzített szabály (`kerdes["szabaly"]`) szerint. Kimenet: {o, ertek, reszlet, forras}."""
    sz = kerdes["szabaly"]
    as_of = _d(esemeny["as_of"])
    m = sz["modszer"]
    if m in ("arfolyam_irany", "arfolyam_sav"):
        alap = await zaras(hiv, sz["symbol"], as_of, szigoruan_elotte=True)
        cel = await zaras(hiv, sz["symbol"], _d(sz["cel_datum"]))
        valt = (cel["close"] / alap["close"] - 1) * 100
        if m == "arfolyam_irany":
            o = int(valt > 0) if sz["irany"] == "fel" else int(valt < 0)
        else:
            o = kimenetel({"tipus": "intervallum", "also": -sz["sav_szazalek"], "felso": sz["sav_szazalek"]}, valt)
        return {"o": o, "ertek": round(valt, 4), "reszlet": {"alap": alap, "cel": cel},
                "forras": f"Makronóm yfinance history {sz['symbol']}"}
    if m == "kamat_osszevetes":
        utana = await kamat(hiv, sz["orszag"], _d(sz["referencia_datum"]))
        kesobb = await kamat(hiv, sz["orszag"], _d(sz["cel_datum"]))
        o = int({"alacsonyabb": kesobb["value"] < utana["value"], "magasabb": kesobb["value"] > utana["value"]}
                [sz["irany"]])
        return {"o": o, "ertek": kesobb["value"], "reszlet": {"referencia": utana, "cel": kesobb},
                "forras": f"Makronóm get_macro_indicator policy_rate {sz['orszag']}"}
    if m in ("sajto_cikkszam", "sajto_osszevetes"):
        rx = re.compile(sz["regex"], re.I)
        szamok, cimek = {}, {}
        for lap in sz["lapok"]:
            talalt = []
            for nap in sz["napok"]:
                talalt += [a for a in await napi_lap(hiv, lap, nap)
                           if rx.search(f"{a.get('title') or ''} {a.get('lead') or ''}")
                           and (not sz.get("regex2") or re.search(sz["regex2"], f"{a.get('title') or ''} "
                                                                                 f"{a.get('lead') or ''}", re.I))]
            szamok[lap] = len(talalt)
            cimek[lap] = [a.get("title") for a in talalt][:12]
        if m == "sajto_cikkszam":
            o = int(szamok[sz["lapok"][0]] >= sz["legalabb"])
            ertek = szamok[sz["lapok"][0]]
        else:
            a, b = sz["lapok"][:2]
            o, ertek = int(szamok[a] > szamok[b]), szamok[a] - szamok[b]
        return {"o": o, "ertek": ertek, "reszlet": {"cikkszam": szamok, "cimek": cimek},
                "forras": "Echolot get_news (source+date), cím+lead regex"}
    raise ValueError(f"ismeretlen GT-módszer: {m}")


# ══ 8b. A GAZDASÁGI ELŐREJELZÉSEK MÉRT KIMENETELE (a v2 magja) ══════════════════════════════════════════════════

async def macro_ertek(hiv, orszag: str, mutato: str, idoszak: str) -> float | None:
    """A macro_history kanonikus sorának értéke egy időszakra (ugyanaz a sor, amelyből az as_of-alapvonal épül)."""
    v = mcp_json(await hiv("makronom", "macro_history", {"country": orszag, "indicator": mutato,
                                                         "start": str(int(idoszak[:4]) - 1)}))
    for o in (v or {}).get("observations") or []:
        if isinstance(o, dict) and str(o.get("period")) == idoszak and isinstance(o.get("value"), (int, float)):
            return float(o["value"])
    return None


NEM_REVIDEALODIK = "nem revideálódik (záróár / jegybanki döntés): a szabály a mért napot jelöli"


async def gazdasagi_tenyleges(hiv, sor: dict, *, naplo="alap", ma: date | None = None) -> dict:
    """Egy gazdasági tábla-sor (v2: `gazdasagi_tabla`) célidőszakának mért értéke a sor FELOLDÁSI SZABÁLYA szerint
    (`feloldasi_szabaly`, alap: `first_print`; `elso_kozles.feloldas`). {ertek, forras, feloldasi_szabaly, feloldas,
    [jeloles], [mai_ertek]} vagy {hiba}. A `jeloles` kimondja, ha az első közlés hiányzik, és a mai érték megy."""
    kulcs = str(sor.get("kulcs") or "")
    szabaly = sor.get("feloldasi_szabaly") or ek.ALAP_SZABALY
    naplo = ek.naplo() if naplo == "alap" else naplo
    try:
        if "=" in kulcs:                                     # árfolyam (naiv alapvonal, a horizont végén)
            z = await zaras(hiv, kulcs, _d(sor["idoszak"]))
            return {"ertek": z["close"], "datum": z["date"], "forras": f"Makronóm yfinance history {kulcs}",
                    "feloldasi_szabaly": szabaly, "feloldas": NEM_REVIDEALODIK}
        orszag, ind = kulcs.split(":", 1)
        if ind == "policy_rate":
            k = await kamat(hiv, orszag, _d(sor["idoszak"]))
            return {"ertek": k["value"], "forras": f"Makronóm policy_rate {orszag} {sor['idoszak']}",
                    "feloldasi_szabaly": szabaly, "feloldas": NEM_REVIDEALODIK}
        mutato = PALYA_INDIKATOR.get(ind)
        if not mutato:
            return {"hiba": f"ismeretlen mutató: {ind}"}
        x = await macro_ertek(hiv, orszag, mutato, str(sor["idoszak"]))
        f = await ek.feloldas(hiv, orszag, mutato, str(sor["idoszak"]), szabaly=szabaly, mai=x, naplo=naplo,
                              vintage=sor.get("vintage"), ma=ma)
        if f.get("ertek") is None:
            return {"hiba": f"a {sor['idoszak']} időszak értéke még nincs a macro_history-ban"}
        ki = {"ertek": f["ertek"], "forras": f"Makronóm macro_history {orszag} {mutato} {sor['idoszak']}",
              "feloldasi_szabaly": szabaly, "feloldas": f["feloldas"], "mai_ertek": x}
        if f.get("jeloles"):
            ki.update(jeloles=f["jeloles"], jeloles_ok=f.get("ok"))
        if f.get("kozolve"):
            ki["kozolve"] = f["kozolve"]
        return ki
    except Exception as e:  # noqa: BLE001 — a hiány kimondva, nem pótolva
        return {"hiba": f"{type(e).__name__}: {str(e)[:200]}"}


async def gazdasagi_sorok(hiv, futas: dict, ma: date, *, naplo="alap") -> list[dict]:
    """A futás gazdasági táblájának minden sora a mért értékkel: a fő szám a CRPS (`crps_mezok`: döntéssel, alapvonal,
    a normalizált különbség), mellette leírásként a sávba esés (1/0) és a pontbecslések hibája. A tényleges érték a
    sor feloldási szabálya szerint (a régi, mező nélküli futásnál `first_print`); a hiányzó első közlés kimondva."""
    ki = []
    naplo = ek.naplo() if naplo == "alap" else naplo
    for t in futas.get("gazdasag") or []:
        sor = {k: t.get(k) for k in ("kulcs", "mutato", "orszag", "egyseg", "idoszak", "hatarido", "ertek", "also",
                                     "felso", "naiv", "dontessel", "dontessel_also", "dontessel_felso", "skala")}
        sor["feloldasi_szabaly"] = t.get("feloldasi_szabaly") or ek.ALAP_SZABALY
        sor["hatas"] = (t.get("hatas") or {}).get("valtozas") if t.get("hatas") else None
        if str(t.get("hatarido") or "9999")[:10] > ma.isoformat():
            sor["allapot"] = "nem esedékes"
            ki.append(sor)
            continue
        m = await gazdasagi_tenyleges(hiv, t, naplo=naplo, ma=ma)
        sor.update(tenyleges=m.get("ertek"), tenyleges_forras=m.get("forras") or m.get("hiba"),
                   tenyleges_feloldas=m.get("feloldas"), tenyleges_mai=m.get("mai_ertek"))
        if m.get("jeloles"):
            sor["tenyleges_jeloles"] = m["jeloles"]
        x = m.get("ertek")
        if x is not None:
            if t.get("hatas"):
                sor["o_dontessel"] = kimenetel({"tipus": "intervallum", "also": t["dontessel_also"],
                                                "felso": t["dontessel_felso"]}, x)
                sor["hiba_dontessel"] = round(abs(float(t["dontessel"]) - x), 4)
            if not t.get("naiv") and t.get("also") is not None and t.get("felso") is not None:
                sor["o_alap"] = kimenetel({"tipus": "intervallum", "also": t["also"], "felso": t["felso"]}, x)
            sor["hiba_alap"] = round(abs(float(t["ertek"]) - x), 4)
            sor.update(crps_mezok(t, float(x)))
        sor["allapot"] = "mérve" if x is not None else "nincs adat"
        ki.append(sor)
    return ki


async def skala_potlas(futas: dict, hiv) -> list[str]:
    """A CRPS előtti futás gazdasági sorainak skálája utólag, a FELVETT nyers adatból: ugyanaz az as_of-pálya
    (`asof_palya`) és árfolyamsor (`yf_szur`), amelyből a futás alapvonala épült — a sávot nem nézi. A sorokat helyben
    egészíti ki; kimenet: a skála nélkül maradt sorok, okkal (a naiv alapkamatnak nincs egylépéses előtörténete)."""
    import dontes_szimulator as ds
    as_of = _d(futas["as_of"])
    proxy = AsOfHivo(ds.Hivo(hiv, echolot_koz_s=0.0), as_of, _d(futas.get("horizont_vege") or as_of))
    hiany = []
    for t in futas.get("gazdasag") or []:
        kulcs = str(t.get("kulcs") or "")
        if t.get("skala"):
            continue
        if "=" in kulcs:
            eszkoz, par = "yfinance", {"symbol": kulcs}
        elif kulcs.split(":")[-1] in PALYA_INDIKATOR:
            eszkoz, par = "forecast_path", {"country": kulcs.split(":")[0], "indicator": kulcs.split(":")[-1]}
        else:
            hiany.append(f"{kulcs}: nincs egylépéses előtörténet a felvételben")
            continue
        v, hiba = await proxy("makronom", eszkoz, par)
        if isinstance(v, dict) and v.get("skala_1lepes"):
            t["skala"] = v["skala_1lepes"]
        else:
            hiany.append(f"{kulcs}: {hiba or 'nincs skála'}")
    return hiany


#: a modell SZABAD gazdasági előrejelzéseinek (piac-lépés `elorejelzesek`) feloldása: kulcsszó → mutató
SZABAD_MUTATO = ((r"infl|fogyasztói ?ár|cpi|hicp", "inflation"), (r"munkanélk", "unemployment"),
                 (r"gdp|növekedés", "gdp"), (r"alapkamat|irányadó|kamat|betéti|célsáv", "policy_rate"),
                 (r"eur.?huf|forint", "EURHUF=X"), (r"eur.?usd|dollár", "EURUSD=X"))


#: az állítás saját országa (a tervező országa csak tartalék: az EKB-futás „KSH-inflációja” magyar)
SZABAD_ORSZAG = ((r"\bksh\b|\bmnb\b|magyar|forint", "HU"), (r"\bfed\b|amerikai|\busa?\b", "US"),
                 (r"\bekb\b|euróövezet|eurózóna", "EA"), (r"német", "DE"))


async def szabad_feloldas(hiv, e: dict, orszag: str, ma: date) -> dict:
    """A modell szabad, küszöbös gazdasági előrejelzése (NEM előregisztrált): ha a mutató, a küszöb és a határidő
    egyértelmű, a mért érték szerint 1/0 — különben kimondva feloldhatatlan."""
    if str(e.get("hatarido") or "9999") > ma.isoformat():
        return {"allapot": "nem esedékes"}
    szoveg = f"{e.get('metrika') or ''} {e.get('allitas') or ''}".lower()
    mut = next((m for rx, m in SZABAD_MUTATO if re.search(rx, szoveg)), None)
    spec = elorejelzes_spec(e)
    if not mut or not spec:
        return {"allapot": "feloldhatatlan", "ok": "a mutató vagy a küszöb nem egyértelmű"}
    orszag = next((c for rx, c in SZABAD_ORSZAG if re.search(rx, szoveg)), orszag)
    hatarido = _d(e["hatarido"])
    if "=" in mut or mut == "policy_rate":
        sor = {"kulcs": mut if "=" in mut else f"{orszag}:policy_rate", "idoszak": hatarido.isoformat()}
    else:                                    # a határidőig MEGJELENT utolsó időszak (a v2 közlési késésével)
        import dontes_szimulator as ds
        q = mut == "gdp"
        per = (f"{hatarido.year}-Q{(hatarido.month - 1) // 3 + 1}" if q else hatarido.isoformat()[:7])
        while _idoszak_vege(per) + timedelta(days=ds.KOZLESI_KESES_NAP["Q" if q else "M"]) > hatarido:
            per = _elozo(per)
        sor = {"kulcs": f"{orszag}:{mut}", "idoszak": per}
    m = await gazdasagi_tenyleges(hiv, sor)
    if m.get("ertek") is None:
        return {"allapot": "nincs adat", "ok": m.get("hiba"), "sor": sor}
    return {"allapot": "mérve", "o": kimenetel(spec, m["ertek"]), "ertek": m["ertek"], "sor": sor, "spec": spec}


def _elozo(per: str) -> str:
    if "Q" in per:
        ev, q = int(per[:4]), int(per[-1])
        return f"{ev - (q == 1)}-Q{(q - 2) % 4 + 1}"
    ev, h = int(per[:4]), int(per[5:7])
    return f"{ev - (h == 1)}-{(h - 2) % 12 + 1:02d}"


# ══ 9. A FUTÁS: esemény × modell-út ════════════════════════════════════════

#: A két út (a kapu dönt, melyik CLEAN): (a) Claude-lépések (a szonda szerinti cutoff UTÁNI eseményekre), (b) régi
#: tudás-határú modell MINDEN lépésre. A v2-ben nincs külön kockázati lépés (az összegző írja). A panel modellje
#: külön paraméter (`panel`): None = a panel nem fut (kimondva).
UTAK = {
    "claude": {"terv": "claude-sonnet-5-5", "sajto": "claude-sonnet-5-5", "piac": "claude-sonnet-5-5",
               "jog": "claude-sonnet-5-5", "osszegzes": "claude-sonnet-5-5", "kerdesek": "claude-sonnet-5-5"},
    "hy3": {"terv": "tencent/Hy3", "sajto": "tencent/Hy3", "piac": "tencent/Hy3", "jog": "tencent/Hy3",
            "osszegzes": "tencent/Hy3", "kerdesek": "tencent/Hy3"},
}
#: A horizont: legfeljebb 90 nap, és a vége a mérhetőség napja előtt — így a gazdasági célidőszakok adata a
#: kiértékeléskor már megjelent.
HORIZONT_MAX_NAP = 90
MERES_NAPJA = date(2026, 11, 20)


def horizont_nap(as_of: date, meres: date = MERES_NAPJA) -> int:
    return max(14, min(HORIZONT_MAX_NAP, (meres - as_of).days))


def esemeny_motorjai(esemeny: dict) -> set:
    """A gazdasági mag mindig fut; a sajtó és a jog csak ott, ahol a bank kérdése kifejezetten arról szól."""
    return {"sajto" for q in esemeny["kerdesek"] if q["terulet"] in ("sajto", "tarsadalom")} | \
           {"jog" for q in esemeny["kerdesek"] if q["terulet"] == "jog"}


async def futtat_esemeny(esemeny: dict, ut: str, *, felvevo: Felvevo, get_db, max_usd: float = 1.5,
                         panel_modell: str | None = None, panel_tar: dict | None = None, panel_n: int = 30) -> dict:
    """Egy esemény egy modell-úton: as_of-futás (kötött modellek, felvett adat) → kérdés-lépés. A panel csak kért
    modellel fut (eseményenként EGYSZER, mindkét útnak ugyanaz); `panel_modell=None` → a panel kimarad, kimondva. A
    tisztaságot NEM itt számoljuk: a kiértékelés teszi a `modell_cutoff.CUTOFF` akkori soraiból."""
    import dontes_szimulator as ds
    modellek = UTAK[ut]
    as_of = _d(esemeny["as_of"])
    panel_tar = panel_tar if panel_tar is not None else {}
    panel_ki: dict = {}

    async def _panel(lepes, orszag):
        if not panel_modell:
            return {"ok": False, "hiba": "backteszt: a panel ebben a menetben KI (a panelmodell kiválasztása mérés "
                                         "alatt; SiliconFlow nem fut) — a társadalmi jel hiányzik"}
        orszag = "HU"           # a backteszt panelje mindig a magyar (a korpusza a négy magyar lap)
        kulcs = _sha(_kanon([lepes, orszag, esemeny["as_of"], panel_modell, panel_n]))
        if kulcs not in panel_tar or not panel_tar[kulcs].get("ok"):
            korp = await panel_korpusz(felvevo, as_of)
            panel_tar[kulcs] = await asof_panel(lepes, orszag, korpusz=korp, modell=panel_modell, n=panel_n)
        panel_ki.update(panel_tar[kulcs])
        return panel_tar[kulcs]
    motorok = esemeny_motorjai(esemeny)
    futas = "sz-" + _sha(f"{esemeny['id']}|{ut}|{VERZIO}")[:8]
    horizont = f"{horizont_nap(as_of)} nap"
    r = await ds.futtat(esemeny["lepes"], horizont, get_db=get_db, futas=futas, hiv=felvevo, panel_fn=_panel,
                        studio=False, max_usd_=max_usd, echolot_koz_s=0.0, naplozas=False, teljes=motorok,
                        as_of=as_of, modellek=modellek)
    keret = ds.Keret(max(0.05, max_usd - float((r.get("koltseg") or {}).get("usd") or 0.0)))
    valaszok, kinfo = await kerdes_lepes(r, esemeny, esemeny["kerdesek"], modell=modellek["kerdesek"], keret=keret)
    szivargas = sorted({s for nev in ("sajto", "piac", "jog") for s in ((r.get(nev) or {}).get("adat") or {})
                        .get("szivargas") or []} | (set(SZIV_PANEL) if panel_modell else set()))
    return {"esemeny": esemeny["id"], "ut": ut, "verzio": VERZIO, "szimulator_verzio": r.get("verzio"),
            "szimulator_mod": r.get("mod"), "motorok": sorted(motorok), "as_of": esemeny["as_of"],
            "horizont": horizont, "horizont_vege": r.get("horizont_vege"), "futas": futas,
            "modellek_kert": modellek, "modellek_futott": hasznalt_modellek(r, kinfo),
            "panel": {"modell": panel_modell, "kivonat": (r.get("tarsadalom") or {}).get("kivonat"),
                      "embed_model": ((panel_ki.get("eredmeny") or {}).get("panel") or {}).get("embed_model"),
                      "korpusz_hash": ((panel_ki.get("eredmeny") or {}).get("panel") or {}).get("corpus_hash")},
            "valaszok": valaszok, "kerdes_info": kinfo, "szivargas": szivargas, "allapot": r.get("allapot"),
            "figyelmeztetesek": r.get("figyelmeztetesek"), "adathianyok": r.get("adathianyok"),
            "koltseg": {"szimulator": r.get("koltseg"), "kerdesek_usd": kinfo.get("koltes_usd")},
            "gazdasag": r.get("gazdasag"), "alapvonal": ((r.get("piac") or {}).get("adat") or {}).get("alapvonal"),
            "piac_eredmeny": (r.get("piac") or {}).get("eredmeny"),
            "szimulator_eredmeny": {k: r.get(k) for k in ("terv", "osszegzes", "elorejelzesek", "elorejelzes_kimaradt")},
            "motor_info": {n: (r.get(n) or {}).get("info") for n in ("sajto", "piac", "jog")} | {
                "terv": r.get("terv_info"), "osszegzes": r.get("osszegzes_info")},
            "kesz": datetime.now(timezone.utc).isoformat()}


async def ertekeles(bank: dict, gt: dict, eredmenyek: list[dict], *, hiv=None, ma: date | None = None) -> dict:
    """A bank × GT × futások → pontszám-táblák. ELÖL a gazdasági mag (a v2 intervallumos előrejelzései a mért értékkel:
    CRPS a döntéssel és az alapvonallal, páros különbség), utána az előregisztrált kérdések, végül a modell szabad gazdasági
    előrejelzései (nem előregisztrált, külön sor). A tisztaság MOST számol a `modell_cutoff.CUTOFF` soraiból."""
    ma = ma or datetime.now(timezone.utc).date()
    esemenyek = {e["id"]: e for e in bank["esemenyek"]}
    kimenet = {"utak": {}, "esemenyenkent": [], "cutoff_sorok": sorted(modell_cutoff.CUTOFF), "ma": ma.isoformat(),
               "szamolva": datetime.now(timezone.utc).isoformat()}
    for ut in sorted({e["ut"] for e in eredmenyek}):
        kerdes_sorok, szabad_sorok, gazd_reszlet, szabad_reszlet = [], [], [], []
        for f in [e for e in eredmenyek if e["ut"] == ut]:
            es = esemenyek[f["esemeny"]]
            tz = tisztasag(f["modellek_futott"], es)
            esemeny_sorok = []
            for q in es["kerdesek"]:
                g = gt["kerdesek"].get(q["id"]) or {}
                s = {"id": q["id"], "esemeny": es["id"], "terulet": q["terulet"],
                     "p": (f["valaszok"].get(q["id"]) or {}).get("p"), "o": g.get("o"), "tisztasag": tz["tisztasag"]}
                kerdes_sorok.append(s)
                esemeny_sorok.append(s)
            gs = await gazdasagi_sorok(hiv, f, ma) if hiv is not None else []
            for g in gs:
                g.update(esemeny=es["id"], tisztasag=tz["tisztasag"])
                gazd_reszlet.append(g)
            orszag = (((f.get("szimulator_eredmeny") or {}).get("terv") or {}).get("orszag")) or es.get("orszag")
            for e in ((f.get("piac_eredmeny") or {}).get("elorejelzesek") or []):
                if not isinstance(e, dict) or hiv is None:
                    continue
                fe = await szabad_feloldas(hiv, e, orszag, ma)
                szabad_reszlet.append({"esemeny": es["id"], "allitas": e.get("allitas"), "kuszob": e.get("kuszob"),
                                       "hatarido": e.get("hatarido"), "p": e.get("valoszinuseg"), **fe})
                if fe.get("o") is not None and isinstance(e.get("valoszinuseg"), (int, float)):
                    szabad_sorok.append({"id": f"{es['id']}:{e.get('allitas', '')[:40]}", "esemeny": es["id"],
                                         "terulet": "piac", "p": float(e["valoszinuseg"]), "o": fe["o"],
                                         "tisztasag": tz["tisztasag"]})
            ossz = _osszesit(esemeny_sorok)
            gmert = [g for g in gs if g.get("o_dontessel") is not None]
            gnorm = [g["crps_kulonbseg_norm"] for g in gs if g.get("crps_kulonbseg_norm") is not None
                     and g.get("alap_eloszlas") == "sav80"]                  # mint a fő szám: sáv a sáv ellen
            kimenet["esemenyenkent"].append({
                "esemeny": es["id"], "ut": ut, "as_of": es["as_of"],
                "kimenetel_honap": f"{tz['honap']}…{max([q['hatarido'] for q in es['kerdesek']] + [str(g.get('hatarido') or '') for g in gs])[:7]}",
                "modellek": f["modellek_futott"], "tisztasag": tz["tisztasag"], "lepesenkent": tz["lepesenkent"],
                "gazdasagi_n": len(gmert), "gazdasagi_talalat": sum(g["o_dontessel"] for g in gmert),
                "gazdasagi_crps_norm": round(statistics.fmean(gnorm), 4) if gnorm else None,
                "gazdasagi_crps_n_norm": len(gnorm),
                "n_elorejelzes": ossz.get("n_valaszolt", 0), "n_kerdes": ossz.get("n_kerdes", 0),
                "brier": ossz.get("brier"), "brier_alap_50": ossz.get("brier_alap_50"),
                "brier_alap_bazis": ossz.get("brier_alap_bazis"), "hianyzo": ossz.get("hianyzo"),
                "szivargas_db": len(f.get("szivargas") or []), "allapot": f.get("allapot"),
                "motorok": f.get("motorok"), "panel": (f.get("panel") or {}).get("modell")})
        kimenet["utak"][ut] = {
            "gazdasagi": {**gazdasagi_osszegzes(gazd_reszlet), "sorok": gazd_reszlet},
            "kerdesek": pontoz(kerdes_sorok),
            "szabad_gazdasagi_nem_prereg": {"pontszam": _osszesit(szabad_sorok), "sorok": szabad_reszlet}}
    return kimenet


def crps_csoportok(sorok: list[dict]) -> dict:
    """A CRPS-sorok páros összegzése. FŐ SZÁM (`crps`): csak ahol az alapvonal is eloszlás (saját 80%-os sáv) — sáv a
    sáv ellen. A sáv nélküli, naiv (pont) alapvonal ellen bármely ésszerű sáv „nyer”, ha a sor mozog (a backtesztben
    az EUR/HUF 6/7) — ez a szélesebb-sáv torzítás másik alakja, ezért külön, csak tájékoztatásul."""
    crps = [dict(g, id=g.get("id") or f"{g.get('esemeny')}:{g['kulcs']}") for g in sorok
            if g.get("crps_dontessel") is not None and g.get("crps_alap") is not None]
    savos = [g for g in crps if g.get("alap_eloszlas") == "sav80"]
    jelolt = [g for g in savos if g.get("tenyleges_jeloles")]
    return {"crps": paros_crps_kulonbseg(savos),
            "crps_elso_kozlesen": paros_crps_kulonbseg([g for g in savos if not g.get("tenyleges_jeloles")]),
            "feloldas": {"n_savos": len(savos), "n_elso_kozles_hianyzik": len(jelolt),
                         "jeloles": ek.JELOLES_HIANY if jelolt else None,
                         "hianyzik": sorted({f"{g.get('kulcs')} {g.get('idoszak') or ''}".strip() for g in jelolt})},
            "crps_pont_alapvonal": paros_crps_kulonbseg([g for g in crps if g.get("alap_eloszlas") != "sav80"]),
            "crps_minden_sor": paros_crps_kulonbseg(crps),
            "crps_megjegyzes": "fő szám: sávos alapvonal ellen; negatív = jobbak vagyunk; a pont-alapvonalas sorok "
                               "csak tájékoztatók (sáv a pont ellen); `crps_elso_kozlesen`: csak az első közölt "
                               "értéken mért sorok (a többinél a mai, revideált érték ment, kimondva: `feloldas`)"}


def gazdasagi_osszegzes(sorok: list[dict]) -> dict:
    """A mért gazdasági sorok (`gazdasagi_sorok` + `esemeny`) összegzése. A FŐ SZÁM a páros, normalizált
    CRPS-különbség a sávos alapvonaltól (`crps_csoportok`; negatív = jobbak vagyunk). A sávba esés aránya és a
    pontbecslés hibája leírás."""
    od = [g["o_dontessel"] for g in sorok if g.get("o_dontessel") is not None]
    oa = [g["o_alap"] for g in sorok if g.get("o_alap") is not None]
    pont = [g for g in sorok if g.get("hiba_dontessel") is not None]
    return {**crps_csoportok(sorok),
            "leiras": {"nevleges_lefedes": SAV_P, "n_dontessel": len(od), "n_alap": len(oa),
                       "talalati_arany_dontessel": round(sum(od) / len(od), 3) if od else None,
                       "talalati_arany_alap": round(sum(oa) / len(oa), 3) if oa else None,
                       "megjegyzes": "a sávba esés aránya csak leírás (a szélesebb sáv mindig többet talál); a fő "
                                     "szám a CRPS-különbség"},
            "pont_hiba_atlag_dontessel": (round(sum(g["hiba_dontessel"] for g in pont) / len(pont), 4)
                                          if pont else None),
            "pont_hiba_atlag_alap": round(sum(g["hiba_alap"] for g in pont) / len(pont), 4) if pont else None,
            "pont_javitott_db": sum(1 for g in pont if g["hiba_dontessel"] < g["hiba_alap"]),
            "pont_rontott_db": sum(1 for g in pont if g["hiba_dontessel"] > g["hiba_alap"]), "pont_n": len(pont)}


# ══ CLI ═════════════════════════════════════════════════════════════════════

def _beolvas(p) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _get_db_helyi():
    """A backteszt NEM ír éles naplóba (naplozas=False); a szimulátornak csak a szignatúra miatt kell get_db."""
    def _nincs():
        raise RuntimeError("a backteszt nem nyúl adatbázishoz")
    return _nincs


async def _cli_gt(args) -> None:
    bank = _beolvas(args.bank)
    felvevo = Felvevo(args.felvetel, eles=True)
    esemenyek = {e["id"]: e for e in bank["esemenyek"]}
    ki = {"bank_sha256": _sha(_kanon(bank)), "kerdesek": {}, "hibak": {},
          "szamolva": datetime.now(timezone.utc).isoformat()}
    try:
        for e in bank["esemenyek"]:
            for q in e["kerdesek"]:
                try:
                    ki["kerdesek"][q["id"]] = await gt_felold(felvevo, q, esemenyek[e["id"]])
                except Exception as ex:  # noqa: BLE001 — a hiányzó GT kimondva, nem pótolva
                    ki["hibak"][q["id"]] = f"{type(ex).__name__}: {str(ex)[:300]}"
                print(q["id"], ki["kerdesek"].get(q["id"], {}).get("o"), ki["hibak"].get(q["id"], ""), flush=True)
    finally:
        felvevo.ment()
        await felvevo.zar()
    Path(args.ki).write_text(json.dumps(ki, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


async def _cli_elotolt(args) -> None:
    """A tervezőtől FÜGGETLEN Echolot-adat előre felvéve: a hét lap as_of előtti napjai — pontosan azok a get_news-
    hívások, amelyeket a sajtó-motor és a panel-korpusz kér. SiliconFlow nem kell hozzá (zár nélkül futhat)."""
    import dontes_szimulator as ds
    bank = _beolvas(args.bank)
    felvevo = Felvevo(args.felvetel, eles=True)
    try:
        for e in bank["esemenyek"]:
            hiba = 0
            for lap in ds.LAPOK:
                for nap in napok_elotte(_d(e["as_of"]), SAJTO_NAPOK):
                    if not napi_parameterek(lap, nap):
                        continue
                    s = await felvevo("echolot", "get_news", napi_parameterek(lap, nap))
                    hiba += s.lstrip().startswith('{"hiba"')
            felvevo.ment()
            print(e["id"], "kész,", hiba, "hibás lekérés", flush=True)
    finally:
        felvevo.ment()
        await felvevo.zar()


async def _cli_futtat(args) -> None:
    bank = _beolvas(args.bank)
    felvevo = Felvevo(args.felvetel, eles=not args.csak_felvetel)
    ki_dir = Path(args.ki_dir)
    ki_dir.mkdir(parents=True, exist_ok=True)
    panel_fajl = ki_dir / "panel_tar.json"
    panel_tar = json.loads(panel_fajl.read_text()) if panel_fajl.exists() else {}
    try:
        for e in bank["esemenyek"]:
            if args.esemeny and e["id"] not in args.esemeny:
                continue
            for ut in args.ut:
                cel = ki_dir / f"futas_{ut}_{e['id']}.json"
                if cel.exists() and not args.ujra:
                    print("megvan:", cel.name, flush=True)
                    continue
                t0 = time.monotonic()
                f = await futtat_esemeny(e, ut, felvevo=felvevo, get_db=_get_db_helyi(), max_usd=args.max_usd,
                                         panel_modell=args.panel_modell, panel_tar=panel_tar, panel_n=args.panel_n)
                cel.write_text(json.dumps(f, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
                if args.panel_modell:
                    panel_fajl.write_text(json.dumps(panel_tar, ensure_ascii=False, default=str), encoding="utf-8")
                felvevo.ment()
                print(f"{cel.name}: {f['allapot']}, {len(f['valaszok'])}/{len(e['kerdesek'])} válasz, "
                      f"{len(f.get('gazdasag') or [])} gazdasági sor, motorok={f['motorok']}, "
                      f"{round(time.monotonic() - t0)} mp, {(f['koltseg']['szimulator'] or {}).get('usd')} $ + "
                      f"{f['koltseg']['kerdesek_usd']} $", flush=True)
    finally:
        felvevo.ment()
        await felvevo.zar()


async def _cli_ertekel(args) -> None:
    bank, gt = _beolvas(args.bank), _beolvas(args.gt)
    if gt.get("bank_sha256") != _sha(_kanon(bank)):
        raise SystemExit("a GT nem ehhez a bankhoz készült (bank_sha256 eltér)")
    eredmenyek = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(args.ki_dir).glob("futas_*.json"))]
    if args.skala_felvetel:              # CRPS előtti futás: a skála a futás saját felvételéből, utólag
        fv = Felvevo(args.skala_felvetel, eles=False)
        for f in eredmenyek:
            for h in await skala_potlas(f, fv):
                print(f"skála nélkül ({f['esemeny']}): {h}", flush=True)
    felvevo = Felvevo(args.felvetel, eles=not args.csak_felvetel)
    try:
        ki = await ertekeles(bank, gt, eredmenyek, hiv=felvevo, ma=_d(args.ma) if args.ma else None)
    finally:
        if not args.csak_felvetel:      # visszajátszásban nincs új felvétel: a bizonyíték-fájl érintetlen marad
            felvevo.ment()
        await felvevo.zar()
    Path(args.ki).write_text(json.dumps(ki, ensure_ascii=False, indent=1), encoding="utf-8")
    print("esemény | út | as_of | kimenetel hónapja | modellek | tisztaság | gazdasági (CRPS-különbség norm., n; "
          "sávban/n) | kérdés (válasz/n) | Brier | alap 50% | alap bázis | szivárgás")
    for s in ki["esemenyenkent"]:
        print(f"{s['esemeny']} | {s['ut']} | {s['as_of']} | {s['kimenetel_honap']} | {', '.join(s['modellek'])} | "
              f"{s['tisztasag']} | {s['gazdasagi_crps_norm']}, {s['gazdasagi_crps_n_norm']}; "
              f"{s['gazdasagi_talalat']}/{s['gazdasagi_n']} | "
              f"{s['n_elorejelzes']}/{s['n_kerdes']} | {s['brier']} | {s['brier_alap_50']} | "
              f"{s['brier_alap_bazis']} | {s['szivargas_db']}")
    for u, v in ki["utak"].items():
        g = v["gazdasagi"]
        print(u, "GAZDASÁGI", json.dumps({k: x for k, x in g.items() if k != "sorok"}, ensure_ascii=False))
        print(u, "KÉRDÉSEK ÖSSZESEN", json.dumps(v["kerdesek"]["osszesen"], ensure_ascii=False))
        print(u, "TERÜLET", json.dumps({k: {x: y for x, y in t.items() if x in ("n_valaszolt", "brier",
                                                                                   "brier_alap_bazis")}
                                         for k, t in v["kerdesek"]["terulet_szerint"].items()}, ensure_ascii=False))
        print(u, "SZABAD (nem prereg)", json.dumps(v["szabad_gazdasagi_nem_prereg"]["pontszam"], ensure_ascii=False))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Döntés-szimulátor backteszt")
    al = ap.add_subparsers(dest="parancs", required=True)
    g = al.add_parser("gt", help="a bank kérdéseinek kimenetele (GT) a rögzített szabályokkal")
    g.add_argument("--bank", required=True)
    g.add_argument("--felvetel", required=True)
    g.add_argument("--ki", required=True)
    t = al.add_parser("elotolt", help="a tervezőtől független Echolot-adat előre felvéve (SF nélkül)")
    t.add_argument("--bank", required=True)
    t.add_argument("--felvetel", required=True)
    f = al.add_parser("futtat", help="esemény × modell-út futások")
    f.add_argument("--bank", required=True)
    f.add_argument("--felvetel", required=True)
    f.add_argument("--ki-dir", required=True)
    f.add_argument("--ut", nargs="+", default=["claude"], choices=sorted(UTAK))
    f.add_argument("--esemeny", nargs="*")
    f.add_argument("--max-usd", type=float, default=0.6)
    f.add_argument("--panel-modell", default=None, help="a panel modellje; üres = a panel nem fut (kimondva)")
    f.add_argument("--panel-n", type=int, default=30)
    f.add_argument("--ujra", action="store_true")
    f.add_argument("--csak-felvetel", action="store_true")
    e = al.add_parser("ertekel", help="Brier-táblák a futásokból (a gazdasági GT-t a felvevőn át méri)")
    e.add_argument("--bank", required=True)
    e.add_argument("--gt", required=True)
    e.add_argument("--ki-dir", required=True)
    e.add_argument("--felvetel", required=True, help="a gazdasági GT nyers válaszai (külön a futásokétól)")
    e.add_argument("--ki", required=True)
    e.add_argument("--ma", default="")
    e.add_argument("--skala-felvetel", default="", help="a futások felvétele: a CRPS-skála pótlása a régi futásokhoz")
    e.add_argument("--csak-felvetel", action="store_true", help="hálózat nélkül: csak a felvett GT-válaszok")
    args = ap.parse_args(argv)
    # a felvevő maga ütemezi az Echolotot (429-várakozással) — a szimulátor Echolot-időkerete ehhez legyen bő
    os.environ.setdefault("SZIMULATOR_ECHOLOT_MAX_S", "2400")
    # …és a szimulátor hívásonkénti időkorlátja se vágja el a felvevő 429-várakozását (különben a munkamenet félbe-
    # szakadna, és a hívás „időtúllépés” hiányként esne ki)
    import dontes_szimulator as ds
    ds.MCP_HIVAS_TIMEOUT_S = max(ds.MCP_HIVAS_TIMEOUT_S, 900)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.parancs == "gt":
        asyncio.run(_cli_gt(args))
    elif args.parancs == "elotolt":
        asyncio.run(_cli_elotolt(args))
    elif args.parancs == "futtat":
        asyncio.run(_cli_futtat(args))
    else:
        asyncio.run(_cli_ertekel(args))


if __name__ == "__main__":
    main()
