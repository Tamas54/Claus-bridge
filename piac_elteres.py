"""PIAC-ELTÉRÉS ÁRNYÉK-MÓDBAN (2026-11-23): eltérés a piactól, ott, ahol bizonyítottan jobbak vagyunk.

A Kommandant (11-23, „csináljuk meg A-B-t”): „nagy eséllyel tudjuk, és előbb tudjuk.” Ez a B. Az A (a közlés-figyelő,
`kozles_figyelo.py`, másik ág) rögzíti a hivatalos számot megjelenéskor; a B abból old fel.

ÁRNYÉK-MÓD: csak napló és pontozás. NINCS kereskedés, megbízás, tőzsdei fiók vagy kulcs — a modulban nincs ilyen
kódút. A Telegram-üzenet árnyék-jelzés, nem befektetési tanács: a Kommandant dönt. LLM-hívás nincs (költség: 0).

A TERV (előre rögzítve, a pontszámok ELŐTT commitolva; a minta: ThesisInstitute/thesis `docs/kalshi-paper-comparison.md`)
--------------------------------------------------------------------------------------------------------------
1. PIACOK: a Kalshi és a Polymarket NYITOTT gazdasági eseményei, amelyek a következő `horizont_nap` (30) napban zárnak
   (nyilvános, kulcs nélküli olvasó API). Egy esemény = egy statisztika egy időszakra (pl. KXCPIYOY-26NOV); ez a
   pontozás egysége.
2. PÁROSÍTÁS (`TERKEP`): csak ugyanazt a mérőszámot vetjük össze (pl. a BLS CPI-U NSA éves változása ↔ a Makronóm
   `forecast_path(US, inflation)` = FRED CPIAUCNS pc1). Ami más mérőszám (havi CPI, maginfláció, Q/Q SAAR GDP, nemzeti
   CPI a HICP helyett), azt kimondva NEM párosítjuk. A kamatpiacokhoz ma nincs FELTÉTEL NÉLKÜLI előrejelzésünk (a
   szimulátor kamat-sávja feltételes, LLM-es): ezek a térképen „p_mi nincs” jelöléssel szerepelnek.
3. A MI VALÓSZÍNŰSÉGÜNK (p_mi): a sorozat 80%-os sávjából (`forecast_path` az adott időszakra) a `szim_backteszt.sav_normal`
   normál eloszlása — UGYANAZ, amit a CRPS pontoz. KEREKÍTÉSI KORREKCIÓ: a hivatal a latens L értéket a g rácsra
   kerekítve közli (Y), ezért P(Y > s) = 1 − F(s + g/2), P(Y ≥ s) = 1 − F(s − g/2), P(Y = v) = F(v + g/2) − F(v − g/2).
   FRISSESSÉG: ha a hivatal már közölt egy újabb időszakot, mint a pálya utolsó ténye, az előrejelzés elavult → nincs
   p_mi (különben a piac többet tudna, mint mi, és az „él” hamis).
4. A PIAC VALÓSZÍNŰSÉGE (p_piac): a vételi és az eladási ár KÖZEPE a rögzítés pillanatában (üres bid = 0, üres ask = 1;
   NEM az utolsó kötés — az csak érzékenység). Polymarketen az IGEN-token a kimenet NEVE szerint (nem a sorrend), a
   mezők string-JSON-ként és tömbként is jöhetnek. A küszöbsor (Kalshi „above X” létra) nem nőhet a küszöbbel: a
   sértést súlyozatlan PAV (izotonikus regresszió) javítja, és megszámoljuk.
5. ÉL = p_mi − p_piac. SZŰRŐ (minden feltétel kell a JELZÉSHEZ):
   (a) |él| > `el_kuszob`;
   (b) KÉPESSÉG-MÉRLEG: az a sorozat × előrejelző (aki a p_mi-t adta), amelynek mért múltja jó: CRPS-különbség az
       alapvonaltól (vagy log-pontszám a piac ellen) < `max_kulonbseg`, legalább `min_n` pár, előjelcserés p ≤ `max_p`.
       Források: a szimulátor backtesztje (`orakel_backteszt/szim_backteszt_*/…/ertekeles_alap_sav.json`), a saját
       feloldott árnyék-naplónk (CRPS a naiv sáv ellen, log-pontszám a piac ellen), és tájékoztatásul a pálya saját
       MAE-backtesztje (ennek nincs p-je, egymagában nem jogosít);
   (c) LIKVIDITÁS (spread ≤ `max_spread`, mélység ≥ `min_melyseg_usd`) — CSAK ÉRZÉKENYSÉG: minden sor naplózza a
       likviditás nélküli döntést is, a fő pontozás minden összevetésen fut;
   (d) ELŐZETESSÉG: az előrejelzés ideje ≤ a piaci ár ideje < a piac zárása (a zárás a közlés előtt van).
6. ELŐRE RÖGZÍTÉS: MINDEN összevetés (jelzés vagy sem) a feloldás ELŐTT a naplóba kerül (Postgres, csak hozzáfűzés,
   hash-lánc). A Telegram csak a SIKERES naplózás után megy; napló nélkül nincs jelzés.
7. FELOLDÁS: az ELSŐ KÖZLÉS (`elso_kozles.feloldas`, `first_print`: a napló — ide ír az A közlés-figyelője —, amerikai
   sornál az ALFRED; a mai, revideált érték NEM pontoz). A közölt értéket a szerződés rácsára kerekítjük. Csak az a
   sor pontozható, amelyet a piac zárása előtt rögzítettünk (különben „nem előzetes”, kizárva).
8. PONTOZÁS: eseményenként több kategóriás log-pontszám (a sávok 0,001-es padlóval, arányos újraosztással) és Brier
   (0…2), nekünk és a piacnak, ugyanazokon a sávokon; piaconként bináris Brier és log. CRPS a naiv sáv ellen
   (perzisztencia + a múltbeli h-lépéses változások 10–90%-os sávja, a pálya ~36 pontos előtörténetéből).
   ÁRNYÉK-PnL a jelzésekre: ha az él irányába 1 szerződést vettünk volna (a) a közép-áron, (b) a spreaden át
   (ask / 1 − bid) — mindkettő díjjal: díj = `dij_*` · p · (1 − p) szerződésenként (konzervatív, konfigurálható).
9. HETI FITNESZ sorozatonként: n, átlagos Δlog (pozitív = mi jobbak), előjelcserés p, 95%-os bootstrap-CI, Brier-
   különbség, CRPS a naiv ellen, a jelzések PnL-je. Kevesebb mint 5 párnál a számok mellett kimondva: „kevés pár”.
   Egyik eredmény sem „szignifikáns” — az intervallumok úgy, ahogy vannak.

KAPCSOLÓ: `PIAC_ELTERES` (alapból BE, a Kommandant kérése); `0` → semmi nem fut. A napló DSN-je `PIAC_ELTERES_PG_DSN`
(tartalék: `ELSO_KOZLES_PG_DSN`). SQLite nincs (házi szabály): teszt alatt memória-napló.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import statistics
import threading
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable

import elso_kozles as ek
from szim_backteszt import (SAV_P, bootstrap_ci, crps_sav, elojelcseres_p, lepes_skala, paros_crps_kulonbseg,
                            sav_normal)

logger = logging.getLogger("piac_elteres")

VERZIO = "piac-elteres-v1"
_N01 = statistics.NormalDist()
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA_API = "https://gamma-api.polymarket.com"
UA = "claus-bridge-piac-elteres/1 (read-only research)"
P_PADLO = 0.001                    # a log-pontszám padlója (a terv szerint, mindkét előrejelzőre)
REPO = Path(__file__).resolve().parent
ALAP_MERLEG_FAJLOK = ("orakel_backteszt/szim_backteszt_20261122/futasok/ertekeles_alap_sav.json",
                      "orakel_backteszt/szim_backteszt_20261122/futasok_panel/ertekeles_alap_sav.json")


# ══ 0. KONFIG ═════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Konfig:
    el_kuszob: float = 0.10            # |p_mi − p_piac| e fölött
    min_n: int = 5                     # a képesség-mérleg legkisebb mintája (a terv: 5 pár alatt nincs következtetés)
    max_p: float = 0.20                # a képesség-mérleg előjelcserés p-je legfeljebb
    max_kulonbseg: float = 0.0         # CRPS- / log-különbség felső határa (negatív = jobbak vagyunk)
    max_spread: float = 0.10           # likviditás (érzékenység): legfeljebb 10 ¢ spread
    min_melyseg_usd: float = 100.0     # likviditás (érzékenység): a vett oldal legjobb ajánlatának mélysége
    horizont_nap: int = 30
    dij_kalshi: float = 0.07           # díj = együttható · p · (1 − p) szerződésenként
    dij_poly: float = 0.07
    mercek: tuple = ("crps", "log_piac")   # ezek a mértékek jogosítanak (a MAE csak tájékoztató)
    merleg_fajlok: tuple = ALAP_MERLEG_FAJLOK


def _env(nev: str, alap, tipus):
    v = os.environ.get(nev, "").strip()
    if not v:
        return alap
    try:
        return tuple(x.strip() for x in v.split(",") if x.strip()) if tipus is tuple else tipus(v)
    except ValueError:
        logger.warning("piac_elteres: hibás %s=%r — az alapérték marad (%r)", nev, v, alap)
        return alap


def konfig() -> Konfig:
    return Konfig(**{f.name: _env(f"PIAC_ELTERES_{f.name.upper()}", f.default, type(f.default))
                     for f in fields(Konfig)})


def bekapcsolva() -> bool:
    """A kapcsoló alapból BE (a Kommandant kérése, 11-23): csak a `PIAC_ELTERES=0|false|ki|off` állítja le."""
    return os.environ.get("PIAC_ELTERES", "1").strip().lower() not in ("0", "false", "nem", "ki", "off")


# ══ 1. ELOSZLÁS → VALÓSZÍNŰSÉG (kerekítési korrekcióval) ═══════════════════════════════════════════════════════

def eloszlas_savbol(also, felso, p: float = SAV_P, **meta) -> dict:
    mu, sigma = sav_normal(also, felso, p)
    return {"tipus": "normal", "mu": mu, "sigma": sigma, "also": float(also), "felso": float(felso), "p": p, **meta}


def cdf(eloszlas: dict, x: float) -> float:
    if eloszlas["tipus"] == "normal":
        if eloszlas["sigma"] <= 0:
            return 1.0 if x >= eloszlas["mu"] else 0.0
        return _N01.cdf((x - eloszlas["mu"]) / eloszlas["sigma"])
    if eloszlas["tipus"] == "diszkret":
        s = sum(w for _, w in eloszlas["atomok"])
        return sum(w for z, w in eloszlas["atomok"] if z <= x + 1e-12) / s
    raise ValueError(f"ismeretlen eloszlás: {eloszlas.get('tipus')}")


def _diszkret_p(eloszlas: dict, kerdes: dict) -> float:
    s = sum(w for _, w in eloszlas["atomok"])
    return sum(w for z, w in eloszlas["atomok"] if teljesul(kerdes, z)) / s


def p_kerdes(eloszlas: dict, kerdes: dict, racs: float) -> float:
    """P(a kérdés IGEN) a mi eloszlásunkból. Folytonos eloszlás a LATENS értéket írja le, a hivatal a `racs`-ra
    kerekítve közöl: a sávhatár fél rácsnyit tolódik (a terv 3. pontja). Diszkrét eloszlás (pl. kamat-atomok)
    pontosan, kerekítés nélkül. `racs` = 0: szó szerinti olvasat (érzékenység)."""
    if eloszlas["tipus"] == "diszkret":
        return _diszkret_p(eloszlas, kerdes)
    g = float(racs) / 2
    t = kerdes["tipus"]
    if t == "nagyobb":
        return 1.0 - cdf(eloszlas, kerdes["x"] + g)
    if t == "legalabb":
        return 1.0 - cdf(eloszlas, kerdes["x"] - g)
    if t == "kisebb":
        return cdf(eloszlas, kerdes["x"] - g)
    if t == "legfeljebb":
        return cdf(eloszlas, kerdes["x"] + g)
    if t == "pontosan":
        return max(0.0, cdf(eloszlas, kerdes["x"] + g) - cdf(eloszlas, kerdes["x"] - g))
    if t == "kozott":
        return max(0.0, cdf(eloszlas, kerdes["felso"] + g) - cdf(eloszlas, kerdes["also"] - g))
    raise ValueError(f"ismeretlen kérdéstípus: {t}")


def teljesul(kerdes: dict, y: float) -> bool:
    """A szerződés szabálya a KÖZÖLT (rácsra kerekített) értékre."""
    t, e = kerdes["tipus"], 1e-9
    if t == "nagyobb":
        return y > kerdes["x"] + e
    if t == "legalabb":
        return y >= kerdes["x"] - e
    if t == "kisebb":
        return y < kerdes["x"] - e
    if t == "legfeljebb":
        return y <= kerdes["x"] + e
    if t == "pontosan":
        return abs(y - kerdes["x"]) <= e
    if t == "kozott":
        return kerdes["also"] - e <= y <= kerdes["felso"] + e
    raise ValueError(f"ismeretlen kérdéstípus: {t}")


def racsra(x: float, racs: float) -> float:
    """A hivatal kerekítése (fél felfelé, mint a BLS egy tizedese)."""
    if not racs:
        return float(x)
    q = Decimal(str(racs))
    return float((Decimal(str(x)) / q).quantize(Decimal(1), rounding=ROUND_HALF_UP) * q)


def naiv_sav(history: list[dict], cel: str, p: float = SAV_P) -> dict | None:
    """Az alapvonal a CRPS-hez (a `szim_backteszt.asof_palya` mintája): az utolsó tény változatlanul, 80%-os sávval a
    múltbeli h-lépéses változások 10–90%-os kvantiliséből — CSAK a rögzítéskor ismert előtörténetből."""
    obs = [(str(o["period"]), float(o["value"])) for o in history or []
           if isinstance(o, dict) and isinstance(o.get("value"), (int, float))]
    if len(obs) < 13:
        return None
    utolso = obs[-1][0]
    h = _lepesek(utolso, cel)
    if h is None or h < 1:
        return None
    ertek = [v for _, v in obs]
    valt = [ertek[i + h] - ertek[i] for i in range(len(ertek) - h)]
    if len(valt) < 8:
        return None
    srt = sorted(valt)
    kv = lambda q: srt[min(len(srt) - 1, max(0, int(round(q * (len(srt) - 1)))))]  # noqa: E731
    lo, hi = (1 - p) / 2, (1 + p) / 2
    return {"kozep": ertek[-1], "also": round(ertek[-1] + kv(lo), 4), "felso": round(ertek[-1] + kv(hi), 4), "p": p,
            "h": h, "n": len(valt), "utolso": utolso,
            "skala": lepes_skala([b - a for a, b in zip(ertek, ertek[1:])], max(map(abs, ertek)))}


def _idx(per: str) -> tuple[int, str] | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})", per or "")
    if m:
        return int(m[1]) * 12 + int(m[2]) - 1, "M"
    m = re.fullmatch(r"(\d{4})-Q([1-4])", per or "")
    if m:
        return int(m[1]) * 4 + int(m[2]) - 1, "Q"
    return None


def _lepesek(tol: str, ig: str) -> int | None:
    a, b = _idx(tol), _idx(ig)
    if not a or not b or a[1] != b[1]:
        return None
    return b[0] - a[0]


# ══ 2. PIACI ÁR: közép, PAV, a Polymarket-csapdák ═══════════════════════════════════════════════════════════════

def pav_nem_novekvo(ertekek: list[float]) -> tuple[list[float], int]:
    """Súlyozatlan PAV: a legközelebbi nem növekvő sorozat (a küszöb nő → az „above X” ára nem nőhet). Kimenet: a
    javított sor és a sértések száma (szomszédos párok, ahol a következő nagyobb)."""
    sertes = sum(1 for a, b in zip(ertekek, ertekek[1:]) if b > a + 1e-12)
    blokk: list[list[float]] = []
    for v in ertekek:
        blokk.append([v, 1.0])
        while len(blokk) > 1 and blokk[-2][0] / blokk[-2][1] < blokk[-1][0] / blokk[-1][1]:
            s, n = blokk.pop()
            blokk[-1][0] += s
            blokk[-1][1] += n
    ki: list[float] = []
    for s, n in blokk:
        ki.extend([s / n] * int(n))
    return ki, sertes


def _szam(x) -> float | None:
    if x is None or isinstance(x, bool) or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def lista(x) -> list:
    """A Polymarket `outcomes` / `outcomePrices` / `clobTokenIds` mezője string-JSON („[\"Yes\",\"No\"]”) VAGY tömb."""
    if isinstance(x, list):
        return x
    if isinstance(x, str) and x.strip():
        try:
            v = json.loads(x)
            return v if isinstance(v, list) else []
        except json.JSONDecodeError:
            return [s.strip().strip('"') for s in x.strip("[]").split(",") if s.strip()]
    return []


def kozep_ar(bid, ask) -> dict:
    """A terv ár-szabálya: üres bid = 0, üres ask = 1; közép = (bid + ask) / 2."""
    b = _szam(bid)
    a = _szam(ask)
    b = 0.0 if b is None else b
    a = 1.0 if a is None or a <= 0 else a
    return {"bid": b, "ask": a, "kozep": (b + a) / 2, "spread": a - b}


_HONAP = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10,
          "NOV": 11, "DEC": 12}
_HONAPNEV = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
             "september": 9, "october": 10, "november": 11, "december": 12}


def kalshi_idoszak(esemeny_ticker: str) -> str | None:
    """KXCPIYOY-26NOV → 2026-11 (az esemény referencia-időszaka a ticker végén)."""
    m = re.search(r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})?$", esemeny_ticker or "")
    return f"20{m[1]}-{_HONAP[m[2]]:02d}" if m else None


def kalshi_kerdes(m: dict) -> dict | None:
    t = m.get("strike_type")
    lo, hi = _szam(m.get("floor_strike")), _szam(m.get("cap_strike"))
    if t == "greater" and lo is not None:
        return {"tipus": "nagyobb", "x": lo}
    if t == "greater_or_equal" and lo is not None:
        return {"tipus": "legalabb", "x": lo}
    if t == "less" and hi is not None:
        return {"tipus": "kisebb", "x": hi}
    if t == "less_or_equal" and hi is not None:
        return {"tipus": "legfeljebb", "x": hi}
    if t == "between" and lo is not None and hi is not None:
        return {"tipus": "kozott", "also": lo, "felso": hi}
    return None


def kalshi_piac(m: dict) -> dict:
    ar = kozep_ar(m.get("yes_bid_dollars", m.get("yes_bid")), m.get("yes_ask_dollars", m.get("yes_ask")))
    return {"piac_id": m.get("ticker"), "kerdes_szoveg": m.get("title") or m.get("yes_sub_title"),
            "kerdes": kalshi_kerdes(m), **ar, "utolso": _szam(m.get("last_price_dollars", m.get("last_price"))),
            "bid_meret": _szam(m.get("yes_bid_size_fp")) or 0.0, "ask_meret": _szam(m.get("yes_ask_size_fp")) or 0.0,
            "forgalom": _szam(m.get("volume_fp", m.get("volume"))), "nyitott": _szam(m.get("open_interest_fp")),
            "zaras": m.get("close_time"), "nyitas": m.get("open_time"), "allapot": m.get("status"),
            "eredmeny": m.get("result") or None, "szabaly": (m.get("rules_primary") or "")[:400]}


def kalshi_esemeny(e: dict) -> dict:
    """Egy Kalshi-esemény (beágyazott piacokkal) → a közös esemény-alak. Az „above X” létrán PAV-javítás."""
    piacok = [kalshi_piac(m) for m in e.get("markets") or [] if m.get("status") in (None, "active", "open")]
    letra = sorted([p for p in piacok if p["kerdes"] and p["kerdes"]["tipus"] in ("nagyobb", "legalabb")],
                   key=lambda p: p["kerdes"]["x"])
    sertes = 0
    if letra:
        javitott, sertes = pav_nem_novekvo([p["kozep"] for p in letra])
        for p, j in zip(letra, javitott):
            p["p_piac"] = j
    for p in piacok:
        p.setdefault("p_piac", p["kozep"])
    zarasok = sorted(p["zaras"] for p in piacok if p.get("zaras"))
    return {"platform": "kalshi", "sorozat_piac": e.get("series_ticker"), "esemeny_id": e.get("event_ticker"),
            "cim": f"{e.get('title') or ''} — {e.get('sub_title') or ''}".strip(" —"),
            "idoszak": kalshi_idoszak(e.get("event_ticker")), "zaras": zarasok[0] if zarasok else None,
            "piacok": piacok, "pav_sertes": sertes, "kizarolagos": bool(e.get("mutually_exclusive")),
            "ar_forras": "élő könyv (yes bid/ask közép) a rögzítés pillanatában"}


_PM_SZAM = r"(-?\d+(?:\.\d+)?)"


def pm_kerdes(szoveg: str) -> dict | None:
    """A Polymarket-sáv címe (`groupItemTitle`, ill. a kérdés) → kérdés-spec. „≤3.3%”, „3.4%”, „≥4.4%”, „3.3% or less”,
    „4.4% or more”, „3.0-3.2%”, „above 3.5%”."""
    s = (szoveg or "").replace(",", ".").replace("–", "-").strip()
    m = re.fullmatch(rf"(?:≤|<=)\s*{_PM_SZAM}\s*%?", s) or re.search(rf"{_PM_SZAM}\s*%?\s*or (?:less|lower|below)", s)
    if m:
        return {"tipus": "legfeljebb", "x": float(m[1])}
    m = re.fullmatch(rf"(?:≥|>=)\s*{_PM_SZAM}\s*%?", s) or re.search(rf"{_PM_SZAM}\s*%?\s*or (?:more|higher|above)", s)
    if m:
        return {"tipus": "legalabb", "x": float(m[1])}
    m = re.fullmatch(rf"(?:<)\s*{_PM_SZAM}\s*%?", s) or re.search(rf"(?:less than|under|below)\s*{_PM_SZAM}\s*%", s)
    if m:
        return {"tipus": "kisebb", "x": float(m[1])}
    m = re.fullmatch(rf"(?:>)\s*{_PM_SZAM}\s*%?", s) or re.search(rf"(?:more than|over|above)\s*{_PM_SZAM}\s*%", s)
    if m:
        return {"tipus": "nagyobb", "x": float(m[1])}
    m = re.fullmatch(rf"{_PM_SZAM}\s*%?\s*-\s*{_PM_SZAM}\s*%?", s)
    if m:
        return {"tipus": "kozott", "also": float(m[1]), "felso": float(m[2])}
    m = re.fullmatch(rf"{_PM_SZAM}\s*%", s)
    if m:
        return {"tipus": "pontosan", "x": float(m[1])}
    return None


def pm_piac(m: dict) -> dict:
    """Egy Polymarket-piac IGEN-ára. A csapdák (sportarbrust_learnings): az árak az `outcomePrices`-ban vannak, a
    kimenetek sorrendje NEM rögzített (az IGEN indexét a NEVE adja), a mezők string-JSON-ok vagy tömbök. A Gamma
    `bestBid`/`bestAsk` az ELSŐ kimenet tokenjére szól: ha az IGEN a második, a komplementer (1 − ask, 1 − bid)."""
    kim = [str(x).strip().lower() for x in lista(m.get("outcomes"))]
    arak = [_szam(x) for x in lista(m.get("outcomePrices"))]
    igen = kim.index("yes") if "yes" in kim else None
    bb, ba = _szam(m.get("bestBid")), _szam(m.get("bestAsk"))
    if igen is None:
        ar, forras = {"bid": None, "ask": None, "kozep": None, "spread": None}, "nincs IGEN-kimenet"
    elif bb is not None and ba is not None and ba > 0:
        ar = kozep_ar(bb, ba) if igen == 0 else kozep_ar(1 - ba, 1 - bb)
        forras = "Gamma bestBid/bestAsk közép" + ("" if igen == 0 else " (komplementer: az IGEN a 2. kimenet)")
    elif igen < len(arak) and arak[igen] is not None:
        ar = {"bid": None, "ask": None, "kozep": arak[igen], "spread": None}
        forras = "outcomePrices (nincs könyv: nem közép-ár, likviditás ismeretlen)"
    else:
        ar, forras = {"bid": None, "ask": None, "kozep": None, "spread": None}, "nincs ár"
    tokenek = lista(m.get("clobTokenIds"))
    sav = m.get("groupItemTitle") or m.get("question") or ""
    return {"piac_id": m.get("slug") or m.get("conditionId") or m.get("id"), "kerdes_szoveg": m.get("question"),
            "kerdes": pm_kerdes(sav) or pm_kerdes(m.get("question") or ""), **ar, "p_piac": ar["kozep"],
            "ar_forras": forras, "utolso": _szam(m.get("lastTradePrice")),
            "igen_token": tokenek[igen] if igen is not None and igen < len(tokenek) else None,
            "likviditas_usd": _szam(m.get("liquidityNum", m.get("liquidity"))),
            "forgalom": _szam(m.get("volumeNum", m.get("volume"))), "zaras": m.get("endDate"),
            "allapot": "active" if m.get("active") and not m.get("closed") else "closed",
            "dij_bazis": m.get("takerBaseFee"), "dij_be": m.get("feesEnabled")}


def pm_idoszak(cim: str, zaras: str | None) -> str | None:
    """„November Inflation US - Annual” + zárás 2026-12-10 → 2026-11 (a hónap az év a zárás előtt)."""
    m = re.search(r"\b(" + "|".join(_HONAPNEV) + r")\b", (cim or "").lower())
    if not m or not zaras:
        return None
    z = date.fromisoformat(str(zaras)[:10])
    h = _HONAPNEV[m[1]]
    return f"{z.year if h <= z.month else z.year - 1}-{h:02d}"


def pm_esemeny(e: dict) -> dict:
    piacok = [pm_piac(m) for m in e.get("markets") or []]
    piacok = [p for p in piacok if p["allapot"] == "active"]
    zaras = e.get("endDate")
    return {"platform": "polymarket", "sorozat_piac": e.get("seriesSlug") or e.get("slug"), "esemeny_id": e.get("slug"),
            "cim": e.get("title"), "idoszak": pm_idoszak(e.get("title") or "", zaras), "zaras": zaras,
            "piacok": piacok, "pav_sertes": 0, "kizarolagos": bool(e.get("negRisk")),
            "ar_osszeg": round(sum(p["kozep"] for p in piacok if p["kozep"] is not None), 4),
            "ar_forras": "Gamma bestBid/bestAsk közép (IGEN-token, név szerint) a rögzítés pillanatában"}


# ══ 3. A TÉRKÉP: melyik piaci sorozat melyik saját sorozatunk ══════════════════════════════════════════════════
#
# `mi`: (ország, forecast_path-mutató); `macro`: a get_macro_indicator mutatója (frissesség) és az első-közlés
# feloldásának `macro_history`-mutatója; `racs`: a közlés rácsa; `forras_kell`: a pálya `source` mezőjében kötelező
# részlet — ha a Makronóm forrást vált, a párosítás magától leáll („definíció eltér”), nem csendben mér mást.

TERKEP_KALSHI = {
    "KXCPIYOY": {"mi": ("US", "inflation"), "macro": "cpi", "racs": 0.1, "forras_kell": "CPIAUCNS",
                 "meres": "CPI-U, nem szezonálisan igazított, 12 havi változás (BLS, 1 tizedes)"},
    "KXU3": {"mi": ("US", "unemployment"), "macro": "unemployment", "racs": 0.1, "forras_kell": "UNRATE",
             "meres": "U-3 munkanélküliségi ráta, SA (BLS, 1 tizedes)"},
    "KXHICP": {"mi": ("EA", "inflation"), "macro": "hicp", "racs": 0.1, "forras_kell": "HICP",
               "meres": "euróövezeti HICP, éves (Eurostat)"},
    "KXUKCPIYOY": {"mi": ("GB", "inflation"), "macro": "cpi", "racs": 0.1, "forras_kell": "ONS",
                   "meres": "UK CPI, éves (ONS)"},
    "KXCHCPIYOY": {"mi": ("CN", "inflation"), "macro": "cpi", "racs": 0.1, "forras_kell": "",
                   "meres": "Kína CPI, éves (NBS)"},
    # kamat: van piac, de nincs FELTÉTEL NÉLKÜLI előrejelzésünk (a szimulátor kamat-sávja feltételes, LLM-es)
    "KXFED": {"mi": ("US", "policy_rate"), "kamat": True, "meres": "Fed-alapkamat felső határa a döntés után"},
    "KXFEDDECISION": {"mi": ("US", "policy_rate"), "kamat": True, "meres": "Fed-döntés (bp)"},
    "KXCBDHUNGARY": {"mi": ("HU", "policy_rate"), "kamat": True, "meres": "MNB alapkamat-döntés"},
    "KXCBDECISIONEU": {"mi": ("EA", "policy_rate"), "kamat": True, "meres": "EKB-döntés"},
    "KXEZDEPRATE": {"mi": ("EA", "policy_rate"), "kamat": True, "meres": "EKB betéti ráta"},
    "KXBOE": {"mi": ("GB", "policy_rate"), "kamat": True, "meres": "BoE alapkamat"},
    # van piac, de más mérőszám, mint a mi sorozatunk: kimondva NEM párosítjuk
    "KXCPI": {"nincs": "havi (MoM, SA) CPI — a mi pályánk éves (y/y) változás"},
    "KXCPICORE": {"nincs": "maginfláció — nincs ilyen pályánk"},
    "KXPAYROLLS": {"nincs": "foglalkoztatotti létszám-változás — nincs ilyen pályánk"},
    "KXGDP": {"nincs": "GDP Q/Q évesített (SAAR) — a mi pályánk éves (y/y) változás"},
    "KXDECPIPREL": {"nincs": "német NEMZETI CPI (előzetes) — a mi pályánk a HICP"},
    "KXFRCPIPREL": {"nincs": "francia NEMZETI CPI (előzetes) — a mi pályánk a HICP"},
    "KXRECSSNBER": {"nincs": "NBER-recesszió — nincs ilyen valószínűségi előrejelzésünk"},
}

#: Polymarket: az esemény slugja (a Gamma a havi eseményeknek új slugot ad, ezért mintával)
TERKEP_POLY = [
    (r"^[a-z]+-inflation-us-annual", {"mi": ("US", "inflation"), "macro": "cpi", "racs": 0.1, "forras_kell": "CPIAUCNS",
                                      "meres": "CPI-U NSA, 12 havi változás (BLS)"}),
    (r"^[a-z]+-inflation-canada-annual", {"mi": ("CA", "inflation"), "macro": "cpi", "racs": 0.1, "forras_kell": "",
                                          "meres": "kanadai CPI, éves (StatCan)"}),
    (r"^[a-z]+-inflation-china-annual", {"mi": ("CN", "inflation"), "macro": "cpi", "racs": 0.1, "forras_kell": "",
                                         "meres": "kínai CPI, éves (NBS)"}),
    (r"^fed-decision-in-", {"mi": ("US", "policy_rate"), "kamat": True, "meres": "Fed-döntés (bp)"}),
    (r"^ecb-interest-rates-", {"mi": ("EA", "policy_rate"), "kamat": True, "meres": "EKB-döntés (bp)"}),
    (r"(bank|reserve-bank|national-bank).*-decision-in-", {"kamat": True, "mi": None, "meres": "jegybanki döntés"}),
    (r"-inflation-[a-z]+-monthly|core-cpi|core-pce", {"nincs": "havi / mag-mutató — nincs ilyen pályánk"}),
]

KALSHI_SOROZATOK = tuple(TERKEP_KALSHI)
POLY_CIMKEK = ("inflation", "economic-policy")


def poly_terkep(slug: str) -> dict | None:
    for minta, d in TERKEP_POLY:
        if re.search(minta, slug or ""):
            return d
    return None


# ══ 4. A MI ELŐREJELZÉSÜNK (Makronóm forecast_path, determinisztikus, LLM nélkül) ═══════════════════════════════

Hivo = Callable[[str, str, dict], Awaitable[Any]]


def _json(v):
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


async def bridge_hiv(szerver: str, eszkoz: str, parameterek: dict) -> str:
    """A Bridge gépi hívója: a teljes JSON kell (a Feldwebel 15 ezres sapkája a pályát levágná)."""
    import kulso_mcp
    return await kulso_mcp.hiv(szerver, eszkoz, parameterek, max_kar=200_000)


async def palya(hiv: Hivo, orszag: str, mutato: str) -> dict:
    v = _json(await hiv("makronom", "forecast_path", {"country": orszag, "indicator": mutato}))
    return v if isinstance(v, dict) else {"status": "missing", "reason": "nem JSON válasz"}


def kozolt_idoszak(v) -> str | None:
    """A get_macro_indicator válaszából a hivatalosan közölt legutóbbi időszak — CSAK strukturált forrásból. A
    webes tartalék (brave_search, „unverified web figure”) időszaka a lekérés napja (11-23: US cpi → „2026-11-23”),
    ezért az nem számít közlésnek."""
    if not isinstance(v, dict) or v.get("value") is None:
        return None
    if re.search(r"brave|scrape|web", f"{v.get('source_used') or ''} {v.get('measure') or ''}", re.I):
        return None
    per = str(v.get("period_returned") or v.get("period") or "")
    m = re.match(r"(\d{4})-(\d{2})(?:-\d{2})?$", per) or None
    if m:
        return f"{m[1]}-{m[2]}"
    return per if re.fullmatch(r"\d{4}-Q[1-4]", per) else None


async def legfrissebb(hiv: Hivo, orszag: str, macro: str) -> str | None:
    return kozolt_idoszak(_json(await hiv("makronom", "get_macro_indicator", {"country": orszag, "indicator": macro})))


def sajat_eloszlas(p: dict, idoszak: str, d: dict, *, friss_idoszak: str | None) -> dict:
    """A pálya → az időszak eloszlása, VAGY {hiba} kimondott okkal (nincs pálya, más definíció, elavult)."""
    if not isinstance(p, dict) or p.get("status") != "ok":
        return {"hiba": f"nincs pálya ({(p or {}).get('reason') or (p or {}).get('status')})"}
    if d.get("forras_kell") and d["forras_kell"] not in str(p.get("source") or "") + str(p.get("method") or ""):
        return {"hiba": f"definíció eltér: a pálya forrása ({str(p.get('source'))[:80]}) nem {d['forras_kell']}"}
    utolso = str((p.get("last_actual") or {}).get("period") or "")
    if friss_idoszak and utolso and friss_idoszak > utolso:
        return {"hiba": f"elavult előrejelzés: a hivatal már közölte a(z) {friss_idoszak} időszakot, a pálya utolsó "
                        f"ténye {utolso}"}
    pont = next((x for x in p.get("path") or [] if str(x.get("period")) == idoszak), None)
    if not pont or _szam(pont.get("low")) is None or _szam(pont.get("high")) is None:
        tenyek = {str(x.get("period")) for x in p.get("history") or []}
        return {"hiba": "az időszak már közölt tény" if idoszak in tenyek else f"a pályán nincs {idoszak} sáv"}
    return eloszlas_savbol(pont["low"], pont["high"], SAV_P, pont=_szam(pont.get("value")), idoszak=idoszak,
                           forras=f"Makronóm forecast_path {d['mi'][0]} {d['mi'][1]} ({str(p.get('source'))[:120]})",
                           generalt=p.get("generated_at"), utolso_teny=p.get("last_actual"),
                           naiv=naiv_sav(p.get("history") or [], idoszak),
                           backteszt={k: (p.get("backtest") or {}).get(k) for k in ("mae", "naive_mae", "origins")})


# ══ 5. A KÉPESSÉG-MÉRLEG (sorozatonként; csak ott jelzünk, ahol jó) ═══════════════════════════════════════════

def _sorozat_kulcs(orszag: str, mutato: str) -> str:
    """A közös kulcs: a szimulátor `kulcs`-a (US:inflation, US:policy_rate, EURHUF=X)."""
    return f"{orszag}:{mutato}"


def merleg_szimulatorbol(ertekeles: dict, elorejelzo: str = "szimulator") -> list[dict]:
    """A szimulátor-backteszt (`ertekeles_alap_sav.json`) sorai sorozatonként: páros, normalizált CRPS-különbség az
    eloszlásos alapvonaltól (negatív = jobbak vagyunk), n, előjelcserés p, CI."""
    csop: dict[str, list] = {}
    for ut in (ertekeles.get("utak") or {}).values():
        for g in (ut.get("gazdasagi") or {}).get("sorok") or []:
            if g.get("crps_dontessel") is None or g.get("crps_alap") is None:
                continue
            if g.get("alap_eloszlas") not in ("sav80", "sav80_bolyongas", "kamat_lepcsos"):
                continue
            csop.setdefault(str(g.get("kulcs")), []).append(dict(g, id=f"{g.get('esemeny')}:{g.get('kulcs')}"))
    ki = []
    for k, sorok in sorted(csop.items()):
        r = paros_crps_kulonbseg(sorok)
        ki.append({"sorozat": k, "elorejelzo": elorejelzo, "mertek": "crps", "n": r.get("n_norm", 0),
                   "kulonbseg": r.get("kulonbseg_norm"), "p": r.get("p_esemenyenkent", r.get("p_celpontonkent")),
                   "ci95": r.get("ci95"), "jobb_db": r.get("jobb_db"), "rosszabb_db": r.get("rosszabb_db"),
                   "forras": "szimulátor-backteszt (feltételes előrejelzés a döntéssel; CRPS az eloszlásos alapvonal ellen)"})
    return ki


def merleg_fajlokbol(fajlok) -> list[dict]:
    ki = []
    for f in fajlok or ():
        p = REPO / f
        try:
            ert = json.loads(p.read_text())
        except (OSError, ValueError) as e:
            logger.info("piac_elteres: a mérleg-fájl nem olvasható (%s): %s", f, e)
            continue
        cimke = "szimulator_panel" if "panel" in f else "szimulator"
        ki += [dict(r, fajl=f) for r in merleg_szimulatorbol(ert, cimke)]
    return ki


def egy_esemenyre(feloldasok: list[dict]) -> list[dict]:
    """Naponta rögzítünk, de a pontozás egysége az ESEMÉNY (a terv): eseményenként (platform, esemény) a zárás előtti
    UTOLSÓ rögzítés számít (a legrövidebb horizont); a korábbiak a naplóban maradnak, de nem duplikálják a mintát."""
    legjobb: dict[tuple, dict] = {}
    for f in feloldasok:
        k = (f.get("platform"), f.get("esemeny_id"))
        if k not in legjobb or str(f.get("elore_rogzitve") or "") > str(legjobb[k].get("elore_rogzitve") or ""):
            legjobb[k] = f
    return list(legjobb.values())


def merleg_naplobol(feloldasok: list[dict]) -> list[dict]:
    """A saját árnyék-naplónk feloldott sorai sorozatonként (előrejelző: forecast_path): CRPS a naiv sáv ellen és
    log-pontszám a piac ellen (kulonbseg = log_piac − log_mi; negatív = jobbak vagyunk). Fürt: a közlés időszaka (az
    egy napon közölt piacok összefüggnek: Kalshi és Polymarket ugyanarra a CPI-re)."""
    csop: dict[str, list] = {}
    for f in egy_esemenyre([f for f in feloldasok if not f.get("ervenytelen") and f.get("sorozat")]):
        csop.setdefault(f["sorozat"], []).append(f)
    ki = []
    for s, sorok in sorted(csop.items()):
        cr = [x for x in sorok if x.get("crps_mi") is not None and x.get("crps_naiv") is not None]
        r = paros_crps_kulonbseg([{"id": x["idoszak"], "esemeny": x["idoszak"], "crps_dontessel": x["crps_mi"],
                                   "crps_alap": x["crps_naiv"], "skala": x.get("skala")} for x in cr])
        ki.append({"sorozat": s, "elorejelzo": "forecast_path", "mertek": "crps", "n": r.get("n_norm", 0),
                   "kulonbseg": r.get("kulonbseg_norm"), "p": r.get("p_celpontonkent"), "ci95": r.get("ci95"),
                   "forras": "árnyék-napló: CRPS a naiv sáv ellen (első közlésen)"})
        lg = [x for x in sorok if x.get("log_mi") is not None and x.get("log_piac") is not None]
        if lg:
            d = [x["log_piac"] - x["log_mi"] for x in lg]
            furt = [x["idoszak"] for x in lg]
            lo, hi = bootstrap_ci(d, furt)
            ki.append({"sorozat": s, "elorejelzo": "forecast_path", "mertek": "log_piac", "n": len(set(furt)),
                       "n_esemeny": len(d),
                       "kulonbseg": round(statistics.fmean(d), 4), "p": round(elojelcseres_p(d, furt), 4),
                       "ci95": [round(lo, 4), round(hi, 4)],
                       "forras": "árnyék-napló: több kategóriás log-pontszám a piac ellen (első közlésen)"})
    return ki


def merleg_palyabol(sorozat: str, backteszt: dict | None) -> dict | None:
    """A pálya saját gördülő backtesztje (MAE a naiv ellen) — TÁJÉKOZTATÓ: nincs p, nincs eloszlás-pontozás."""
    b = backteszt or {}
    mae, naiv = _szam(b.get("mae")), _szam(b.get("naive_mae"))
    if mae is None or not naiv:
        return None
    return {"sorozat": sorozat, "elorejelzo": "forecast_path", "mertek": "mae", "n": int(b.get("origins") or 0),
            "kulonbseg": round(mae / naiv - 1, 4), "p": None, "ci95": None,
            "forras": "a pálya saját backtesztje (MAE / naiv MAE − 1; csak tájékoztató)"}


def kepesseg_dontes(merleg: list[dict], sorozat: str, elorejelzo: str, k: Konfig) -> dict:
    """Jogosult-e a sorozat × előrejelző jelzésre: van-e olyan rekord egy jogosító mértékkel (`k.mercek`), amelyben a
    különbség < `max_kulonbseg`, n ≥ `min_n` és p ≤ `max_p`. A döntés mellé a kimondott ok és a rekordok."""
    sajat = [r for r in merleg if r["sorozat"] == sorozat and r["elorejelzo"] == elorejelzo]
    okok = []
    for r in sajat:
        if r["mertek"] not in k.mercek:
            okok.append(f"{r['mertek']}: csak tájékoztató")
            continue
        if r.get("kulonbseg") is None or r["kulonbseg"] >= k.max_kulonbseg:
            okok.append(f"{r['mertek']}: nem jobb az alapvonalnál ({r.get('kulonbseg')})")
        elif (r.get("n") or 0) < k.min_n:
            okok.append(f"{r['mertek']}: kevés pár (n={r.get('n')} < {k.min_n})")
        elif r.get("p") is None or r["p"] > k.max_p:
            okok.append(f"{r['mertek']}: nem elég biztos (p={r.get('p')} > {k.max_p})")
        else:
            return {"jogosult": True, "ok": f"{r['mertek']}: {r['kulonbseg']} (n={r['n']}, p={r['p']})", "rekord": r,
                    "rekordok": sajat}
    return {"jogosult": False, "ok": "; ".join(okok) or f"nincs mért múlt ({sorozat}, {elorejelzo})",
            "rekord": None, "rekordok": sajat}


# ══ 6. AZ ÖSSZEVETÉS ÉS A SZŰRŐ ════════════════════════════════════════════════════════════════════════════════

def dij(platform: str, ar: float, k: Konfig) -> float:
    c = k.dij_kalshi if platform == "kalshi" else k.dij_poly
    return c * ar * (1 - ar)


def likviditas(platform: str, piac: dict, irany: int, k: Konfig) -> dict:
    """A vett oldal legjobb ajánlata: IGEN-irány → az ask, NEM-irány → a bid (YES eladása ≡ NO vétele)."""
    sp = piac.get("spread")
    if platform == "kalshi":
        meret, ar = (piac.get("ask_meret"), piac.get("ask")) if irany > 0 else (piac.get("bid_meret"), 1 - (piac.get("bid") or 0))
        melyseg = (meret or 0) * (ar or 0)
    else:
        melyseg = piac.get("likviditas_usd")
    jo = sp is not None and sp <= k.max_spread and melyseg is not None and melyseg >= k.min_melyseg_usd
    return {"jo": bool(jo), "spread": sp, "melyseg_usd": None if melyseg is None else round(melyseg, 2)}


def osszevet(esemeny: dict, eloszlas: dict | None, d: dict, kepesseg: dict, k: Konfig, most: datetime) -> dict:
    """Egy esemény minden piacára p_mi, p_piac, él és a szűrő döntése. Semmi I/O."""
    sorok = []
    zaras = _ido(esemeny.get("zaras"))
    generalt = _ido((eloszlas or {}).get("generalt"))
    elozetes = zaras is not None and most < zaras and (generalt is None or generalt <= most)
    for p in esemeny["piacok"]:
        s = {k_: p.get(k_) for k_ in ("piac_id", "kerdes_szoveg", "kerdes", "bid", "ask", "kozep", "spread", "p_piac",
                                       "utolso", "ar_forras", "zaras")}
        if eloszlas is None or p.get("kerdes") is None or p.get("p_piac") is None:
            s["p_mi"] = None
            s["ok"] = ("nincs saját eloszlás" if eloszlas is None else
                       "a kérdés nem olvasható" if p.get("kerdes") is None else "nincs piaci ár")
            sorok.append(s)
            continue
        pm = p_kerdes(eloszlas, p["kerdes"], d.get("racs", 0))
        el = pm - p["p_piac"]
        irany = 1 if el > 0 else -1
        lik = likviditas(esemeny["platform"], p, irany, k)
        nagy = abs(el) > k.el_kuszob
        s.update(p_mi=round(pm, 4), p_mi_szo_szerint=round(p_kerdes(eloszlas, p["kerdes"], 0), 4), el=round(el, 4),
                 irany="IGEN" if irany > 0 else "NEM", likviditas=lik, nagy_el=nagy,
                 jelzes_likviditas_nelkul=bool(nagy and kepesseg["jogosult"] and elozetes),
                 jelzes=bool(nagy and kepesseg["jogosult"] and elozetes and lik["jo"]))
        sorok.append(s)
    return {"piacok": sorok, "elozetes": elozetes, "jelzesek": [s["piac_id"] for s in sorok if s.get("jelzes")]}


def _ido(x) -> datetime | None:
    if not x:
        return None
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ══ 7. A NAPLÓ (Postgres, csak hozzáfűzés, hash-lánc; teszt alatt memória — SQLite soha) ═══════════════════════

SEMA = """
CREATE TABLE IF NOT EXISTS piac_elteres_naplo (
    id          bigserial PRIMARY KEY,
    tipus       text NOT NULL,            -- 'elore' (összevetés a feloldás előtt) | 'feloldas'
    kulcs       text NOT NULL,            -- elore: platform:esemény:nap; feloldas: az elore-sor kulcsa
    rogzitve    timestamptz NOT NULL DEFAULT now(),
    payload     jsonb NOT NULL,
    elozo_hash  text NOT NULL,            -- az előző sor hash-e (az első sornál 64 nulla)
    sor_hash    text NOT NULL,            -- sha256(elozo_hash | tipus | kulcs | kanonikus payload)
    UNIQUE (tipus, kulcs)
);
CREATE INDEX IF NOT EXISTS piac_elteres_naplo_tipus_idx ON piac_elteres_naplo (tipus, rogzitve);
CREATE OR REPLACE FUNCTION piac_elteres_csak_hozzafuzes() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'piac_elteres_naplo: csak hozzáfűzés (az előre rögzített sor nem írható felül)';
END $$;
CREATE TRIGGER piac_elteres_csak_hozzafuzes BEFORE UPDATE OR DELETE ON piac_elteres_naplo
    FOR EACH ROW EXECUTE FUNCTION piac_elteres_csak_hozzafuzes();
"""
NULLA_HASH = "0" * 64


def kanon(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sor_hash(elozo: str, tipus: str, kulcs: str, payload: dict) -> str:
    return hashlib.sha256(f"{elozo}|{tipus}|{kulcs}|{kanon(payload)}".encode()).hexdigest()


def lanc_ellenoriz(sorok: list[dict]) -> dict:
    elozo = NULLA_HASH
    for i, s in enumerate(sorted(sorok, key=lambda s: s["id"])):
        if s["elozo_hash"] != elozo or s["sor_hash"] != sor_hash(elozo, s["tipus"], s["kulcs"], s["payload"]):
            return {"ep": False, "hibas_id": s["id"], "index": i}
        elozo = s["sor_hash"]
    return {"ep": True, "n": len(sorok), "utolso_hash": elozo}


class MemoriaNaplo:
    """A PgNaplo felülete memóriában (teszt, egyszeri helyi futás). Ugyanaz a szemantika: hozzáfűzés, hash-lánc,
    (tipus, kulcs) egyedi — a második rögzítés nem ír felül."""

    def __init__(self, ora: Callable[[], datetime] | None = None):
        self._sorok: list[dict] = []
        self._ora = ora or (lambda: datetime.now(timezone.utc))
        self._zar = threading.Lock()

    def hozzafuz(self, tipus: str, kulcs: str, payload: dict) -> dict | None:
        with self._zar:
            if any(s["tipus"] == tipus and s["kulcs"] == kulcs for s in self._sorok):
                return None
            elozo = self._sorok[-1]["sor_hash"] if self._sorok else NULLA_HASH
            payload = json.loads(kanon(payload))
            s = {"id": len(self._sorok) + 1, "tipus": tipus, "kulcs": kulcs, "rogzitve": self._ora(),
                 "payload": payload, "elozo_hash": elozo, "sor_hash": sor_hash(elozo, tipus, kulcs, payload)}
            self._sorok.append(s)
            return dict(s)

    def sorok(self, tipus: str | None = None) -> list[dict]:
        return [dict(s) for s in self._sorok if tipus is None or s["tipus"] == tipus]


class PgNaplo:
    """A `piac_elteres_naplo` tábla. A hash-lánc egy tranzakcióban, advisory lockkal (két író sem ágaztatja el)."""

    ZAR = 7_720_261_123

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._sema_kesz = False

    def _kapcsolat(self):
        import psycopg
        return psycopg.connect(self.dsn, connect_timeout=5)

    def _sema(self, k) -> None:
        if not self._sema_kesz:
            if k.execute("SELECT to_regclass('piac_elteres_naplo')").fetchone()[0] is None:
                k.execute(SEMA)
            self._sema_kesz = True

    def hozzafuz(self, tipus: str, kulcs: str, payload: dict) -> dict | None:
        from psycopg.types.json import Jsonb
        payload = json.loads(kanon(payload))
        with self._kapcsolat() as k:
            self._sema(k)
            k.execute("SELECT pg_advisory_xact_lock(%s)", [self.ZAR])
            if k.execute("SELECT 1 FROM piac_elteres_naplo WHERE tipus = %s AND kulcs = %s",
                         [tipus, kulcs]).fetchone():
                return None
            r = k.execute("SELECT sor_hash FROM piac_elteres_naplo ORDER BY id DESC LIMIT 1").fetchone()
            elozo = r[0] if r else NULLA_HASH
            h = sor_hash(elozo, tipus, kulcs, payload)
            row = k.execute("INSERT INTO piac_elteres_naplo (tipus, kulcs, payload, elozo_hash, sor_hash) "
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
            q = ("SELECT id, tipus, kulcs, rogzitve, payload, elozo_hash, sor_hash FROM piac_elteres_naplo"
                 + (" WHERE tipus = %s" if tipus else "") + " ORDER BY id")
            return [dict(r) for r in k.execute(q, [tipus] if tipus else []).fetchall()]


_NAPLO: dict[str, PgNaplo] = {}


def naplo():
    """A folyamat naplója, ha van DSN (`PIAC_ELTERES_PG_DSN`, tartalék `ELSO_KOZLES_PG_DSN`); különben None —
    ilyenkor nincs előre rögzítés, ezért JELZÉS SEM (a térkép és az összevetés fut, és ezt kimondja)."""
    dsn = (os.environ.get("PIAC_ELTERES_PG_DSN") or os.environ.get("ELSO_KOZLES_PG_DSN") or "").strip()
    if not dsn:
        return None
    try:
        import psycopg  # noqa: F401
    except ImportError:
        logger.warning("piac_elteres: van DSN, de a psycopg nincs telepítve — nincs napló")
        return None
    return _NAPLO.setdefault(dsn, PgNaplo(dsn))


# ══ 8. FELOLDÁS ÉS PONTOZÁS ═══════════════════════════════════════════════════════════════════════════════════

def padlo_normal(p: list[float], padlo: float = P_PADLO) -> list[float]:
    """Minden sáv legalább `padlo`, a többlet arányosan a padló fölötti sávokból (a terv 3. változtatása)."""
    k = len(p)
    if padlo * k >= 1:
        raise ValueError("a padló túl nagy a sávok számához")
    p = [max(0.0, x) for x in p]
    s = sum(p)
    p = [x / s for x in p] if s > 0 else [1 / k] * k
    alacsony = [x < padlo for x in p]
    while True:
        kell = padlo * sum(alacsony)
        szabad = sum(x for x, a in zip(p, alacsony) if not a)
        ki = [padlo if a else x * (1 - kell) / szabad for x, a in zip(p, alacsony)]
        uj = [not a and x < padlo for x, a in zip(ki, alacsony)]
        if not any(uj):
            return ki
        alacsony = [a or b for a, b in zip(alacsony, uj)]


def savok(sorok: list[dict], kizarolagos: bool) -> tuple[list[dict], list[float], list[float]] | None:
    """Az esemény sávjai és a két eloszlás rajtuk. Küszöblétra („nagyobb” / „legalább” X): n küszöb → n + 1 sáv, a
    javított létra különbségei. Kizárólagos sávos esemény (Polymarket negRisk): a listázott sávok + „egyéb”, a piac a
    közép-árakból (összeg > 1 → normálva; < 1 → a maradék az „egyéb”)."""
    jo = [s for s in sorok if s.get("p_mi") is not None and s.get("kerdes")]
    letra = sorted([s for s in jo if s["kerdes"]["tipus"] in ("nagyobb", "legalabb")], key=lambda s: s["kerdes"]["x"])
    if letra and len(letra) == len(jo) and not kizarolagos:
        pp = [min(1.0, max(0.0, s["p_piac"])) for s in letra]
        pm = [min(1.0, max(0.0, s["p_mi"])) for s in letra]
        sav_piac = [1 - pp[0]] + [a - b for a, b in zip(pp, pp[1:])] + [pp[-1]]
        sav_mi = [1 - pm[0]] + [a - b for a, b in zip(pm, pm[1:])] + [pm[-1]]
        hatar = [{"tipus": "letra", "kerdesek": [s["kerdes"] for s in letra]}]
        return hatar, [max(0.0, x) for x in sav_mi], [max(0.0, x) for x in sav_piac]
    if kizarolagos and jo:
        pm = [s["p_mi"] for s in jo]
        pp = [s["p_piac"] for s in jo]
        egyeb_mi = max(0.0, 1 - sum(pm))
        osz = sum(pp)
        pp = [x / osz for x in pp] + [0.0] if osz > 1 else pp + [1 - osz]
        return [{"tipus": "kizarolagos", "kerdesek": [s["kerdes"] for s in jo]}], pm + [egyeb_mi], pp
    return None


def realizalt_sav(hatar: dict, y: float) -> int:
    if hatar["tipus"] == "letra":
        return sum(1 for q in hatar["kerdesek"] if teljesul(q, y))
    for i, q in enumerate(hatar["kerdesek"]):
        if teljesul(q, y):
            return i
    return len(hatar["kerdesek"])


def brier_sav(p: list[float], i: int) -> float:
    return sum((x - (1.0 if j == i else 0.0)) ** 2 for j, x in enumerate(p))


def arnyek_pnl(platform: str, s: dict, o: int, k: Konfig) -> dict:
    """1 szerződés az él irányába: (a) a közép-áron, (b) a spreaden át (IGEN: ask; NEM: 1 − bid), díjjal."""
    igen = s["el"] > 0
    ki = {}
    for nev, ar in (("kozepen", s["p_piac"] if igen else 1 - s["p_piac"]),
                    ("spreaddel", s.get("ask") if igen else (None if s.get("bid") is None else 1 - s["bid"]))):
        if ar is None:
            ki[nev] = None
            continue
        nyer = (o if igen else 1 - o) - ar - dij(platform, ar, k)
        ki[nev] = round(nyer, 4)
    return ki


def pontoz(elore: dict, y_kozolt: float, k: Konfig) -> dict:
    """Egy előre rögzített esemény pontozása a közölt (rácsra kerekített) értékkel. Kimenet: piaconként bináris
    Brier/log, eseményenként több kategóriás log és Brier nekünk és a piacnak, CRPS a naiv sáv ellen, a jelzések
    árnyék-PnL-je."""
    pl = elore["platform"]
    piacok = []
    for s in elore["osszevetes"]["piacok"]:
        if s.get("p_mi") is None:
            continue
        o = int(teljesul(s["kerdes"], y_kozolt))
        pmi, ppi = (min(1 - P_PADLO, max(P_PADLO, x)) for x in (s["p_mi"], s["p_piac"]))
        r = {"piac_id": s["piac_id"], "o": o, "brier_mi": round((s["p_mi"] - o) ** 2, 5),
             "brier_piac": round((s["p_piac"] - o) ** 2, 5),
             "log_mi": round(math.log(pmi if o else 1 - pmi), 5), "log_piac": round(math.log(ppi if o else 1 - ppi), 5),
             "jelzes": s.get("jelzes"), "jelzes_likviditas_nelkul": s.get("jelzes_likviditas_nelkul")}
        if s.get("jelzes_likviditas_nelkul"):
            r["pnl"] = arnyek_pnl(pl, s, o, k)
        piacok.append(r)
    ki = {"y": y_kozolt, "piacok": piacok}
    sv = savok(elore["osszevetes"]["piacok"], elore.get("kizarolagos", False))
    if sv:
        (hatar,), mi, piac = sv
        i = realizalt_sav(hatar, y_kozolt)
        mi_f, piac_f = padlo_normal(mi), padlo_normal(piac)
        ki.update(sav=i, n_sav=len(mi), log_mi=round(math.log(mi_f[i]), 5), log_piac=round(math.log(piac_f[i]), 5),
                  brier_sav_mi=round(brier_sav(mi, i), 5), brier_sav_piac=round(brier_sav(piac, i), 5))
    el = elore.get("eloszlas") or {}
    naiv = el.get("naiv") or {}
    if el.get("tipus") == "normal":
        ki["crps_mi"] = round(crps_sav(el["also"], el["felso"], el.get("p", SAV_P), y_kozolt), 5)
        if naiv.get("also") is not None:
            ki["crps_naiv"] = round(crps_sav(naiv["also"], naiv["felso"], naiv.get("p", SAV_P), y_kozolt), 5)
            ki["skala"] = naiv.get("skala")
    return ki


#: Ahol az A közlés-figyelőjének mértéke AZONOS a szerződésével, ott a B az A értékét veszi át: ez a hivatalos közlés
#: pillanatában rögzített első szám (`kozles:<ORSZÁG>:<mutató>`). US CPI = CPI-U y/y NSA, ezen old fel a Kalshi KXCPIYOY
#: és a Polymarket. Máshol a mérték eltérhet (pl. KSH nemzeti CPI ≠ HICP), ott marad az `elso_kozles.feloldas`.
KOZLES_AZONOS_MERTEK = frozenset({("US", "cpi")})


def a_kozlesbol(orszag: str, macro: str, idoszak: str, ekn) -> dict | None:
    """Az A közlés-figyelőjének első száma `elso_kozles.feloldas`-alakban, vagy None (nincs ilyen sor, vagy más a mérték)."""
    if (orszag, macro) not in KOZLES_AZONOS_MERTEK or ekn is None:
        return None
    import kozles_figyelo                                          # későn: a két modul egymástól függetlenül is betölt
    a = kozles_figyelo.tenyleges_ertek(orszag, macro, idoszak, naplo=ekn)
    if not a or a.get("ertek") is None:
        return None
    return {"ertek": a["ertek"], "kozolve": a["elso_lattuk"],
            "feloldas": f"első közlés, napló ({a['sorozat']}, a közlés-figyelő rögzítette)", "url": a.get("forras_url")}


async def felold(nap, hiv: Hivo | None, k: Konfig, most: datetime, *, ek_naplo="alap") -> list[dict]:
    """Minden még fel nem oldott előzetes sor: az első közlés (`elso_kozles.feloldas`, `first_print`; ide ír az A
    közlés-figyelője) → pontozás → 'feloldas' sor. Csak a piac zárása előtt rögzített sor pontozható."""
    kesz = {s["kulcs"] for s in nap.sorok("feloldas")}
    uj = []
    ekn = ek.naplo() if ek_naplo == "alap" else ek_naplo
    for s in nap.sorok("elore"):
        if s["kulcs"] in kesz:
            continue
        e = s["payload"]
        zaras = _ido(e.get("zaras"))
        if not e.get("eloszlas") or zaras is None or zaras > most:
            continue
        orszag, mutato = e["mi"]
        f = a_kozlesbol(orszag, e["macro"], e["idoszak"], ekn)
        if f is None:
            f = await ek.feloldas(hiv, orszag, e["macro"], e["idoszak"], szabaly="first_print", mai=None, naplo=ekn,
                                  ma=most)
        if f.get("ertek") is None:
            continue
        rog = _ido(s["rogzitve"])
        kozolve = f.get("kozolve")
        elozetes = rog is not None and rog < zaras and (not kozolve or rog.date() <= date.fromisoformat(str(kozolve)[:10]))
        y = racsra(f["ertek"], e.get("racs") or 0)
        payload = {"elore_id": s["id"], "elore_hash": s["sor_hash"], "elore_rogzitve": str(rog), "sorozat": e["sorozat"],
                   "idoszak": e["idoszak"],
                   "platform": e["platform"], "esemeny_id": e["esemeny_id"], "elso_kozles": f.get("ertek"),
                   "feloldas": f.get("feloldas"), "kozolve": kozolve, "verzio": VERZIO}
        if not elozetes:
            payload["ervenytelen"] = (f"nem előzetes: rögzítve {rog}, a piac zárása {zaras}, közölve {kozolve} — "
                                      "kizárva a pontozásból")
        else:
            payload.update(pontoz(e, y, k))
        r = nap.hozzafuz("feloldas", s["kulcs"], payload)
        if r:
            uj.append(r)
    return uj


def heti_osszesito(nap, k: Konfig, most: datetime, *, napok: int = 7) -> dict:
    """A fitnesz sorozatonként: MINDEN feloldott eseményen (a fő szám), plusz a heti újak és a jelzések PnL-je.
    Δlog = log_mi − log_piac (POZITÍV = mi jobbak); kevesebb mint 5 párnál „kevés pár”."""
    fel = [s["payload"] | {"_rogzitve": s["rogzitve"]} for s in nap.sorok("feloldas")]
    ervenyes = [f for f in fel if not f.get("ervenytelen")]
    csop: dict[str, list] = {}
    for f in egy_esemenyre(ervenyes):
        csop.setdefault(f["sorozat"], []).append(f)
    jel_csop: dict[str, list] = {}
    for f in ervenyes:                       # PnL: minden napi jelzés egy külön (feltételezett) vétel a saját árán
        jel_csop.setdefault(f["sorozat"], []).append(f)
    ki = {"most": most.isoformat(), "sorozatok": {}, "ervenytelen": sum(1 for f in fel if f.get("ervenytelen"))}
    for sor, fs in sorted(csop.items()):
        d = [f["log_mi"] - f["log_piac"] for f in fs if f.get("log_mi") is not None]
        furt = [f["idoszak"] for f in fs if f.get("log_mi") is not None]
        b = [f["brier_sav_piac"] - f["brier_sav_mi"] for f in fs if f.get("brier_sav_mi") is not None]
        cr = [(f["crps_mi"] - f["crps_naiv"]) / f["skala"] for f in fs
              if f.get("crps_naiv") is not None and f.get("skala")]
        js = jel_csop.get(sor, [])
        pnl = [p["pnl"] for f in js for p in f.get("piacok") or [] if p.get("jelzes") and p.get("pnl")]
        pnl_lik = [p["pnl"] for f in js for p in f.get("piacok") or [] if p.get("jelzes_likviditas_nelkul") and p.get("pnl")]
        uj = sum(1 for f in fs if _ido(f["_rogzitve"]) and _ido(f["_rogzitve"]) > most - timedelta(days=napok))
        r = {"n": len(d), "n_idoszak": len(set(furt)), "uj_a_heten": uj, "kevés_par": len(set(furt)) < 5,
             "dlog_atlag": round(statistics.fmean(d), 4) if d else None,
             "dbrier_atlag": round(statistics.fmean(b), 4) if b else None,
             "crps_naiv_ellen_norm": round(statistics.fmean(cr), 4) if cr else None,
             "jelzesek": len(pnl), "pnl_kozepen": round(sum(x["kozepen"] or 0 for x in pnl), 4),
             "pnl_spreaddel": round(sum(x["spreaddel"] or 0 for x in pnl), 4),
             "pnl_likviditas_nelkul_spreaddel": round(sum(x["spreaddel"] or 0 for x in pnl_lik), 4)}
        if d:
            lo, hi = bootstrap_ci(d, furt)
            r.update(p=round(elojelcseres_p(d, furt), 4), ci95=[round(lo, 4), round(hi, 4)])
        ki["sorozatok"][sor] = r
    return ki


def heti_szoveg(o: dict) -> str:
    sorok = ["<b>Piac-eltérés — heti fitnesz (árnyék-mód, nem befektetési tanács)</b>"]
    if not o["sorozatok"]:
        sorok.append("Még nincs feloldott összevetés.")
    for s, r in o["sorozatok"].items():
        sorok.append(f"• {s}: n={r['n']}{' (kevés pár)' if r['kevés_par'] else ''}, Δlog={_f(r['dlog_atlag'])} "
                     f"(+ = mi jobbak), p={_f(r.get('p'))}, CI={r.get('ci95')}, CRPS a naiv ellen={_f(r['crps_naiv_ellen_norm'])}; "
                     f"jelzések: {r['jelzesek']}, árnyék-PnL közép/spread: {_f(r['pnl_kozepen'])}/{_f(r['pnl_spreaddel'])} $")
    if o.get("ervenytelen"):
        sorok.append(f"Kizárva (nem előzetes): {o['ervenytelen']}")
    return "\n".join(sorok)


def _f(x, nd: int = 3) -> str:
    return "—" if x is None else f"{x:.{nd}f}".replace(".", ",")


# ══ 9. A KÖR: piacok → összevetés → szűrő → ELŐRE rögzítés → (csak utána) Telegram ══════════════════════════════

Lekero = Callable[[str, dict], Awaitable[Any]]


async def httpx_lekero(url: str, params: dict) -> Any:
    import httpx
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA, "Accept": "application/json"}) as c:
        r = await c.get(url, params=params)          # a Gamma >50-es lapja csak gzip: a httpx kéri és kibontja
        r.raise_for_status()
        return r.json()


async def piacok_lekerese(lekero: Lekero, most: datetime, k: Konfig) -> tuple[list[dict], list[str]]:
    """A nyitott gazdasági események a horizonton belül zárással. Takarékosan: sorozatonként 1 Kalshi-kérés, címkénként
    1 Polymarket-kérés (zárási ablakkal)."""
    hatar = most + timedelta(days=k.horizont_nap)
    esemenyek, hibak = [], []
    for sz in KALSHI_SOROZATOK:
        try:
            v = await lekero(f"{KALSHI_API}/events", {"series_ticker": sz, "status": "open",
                                                       "with_nested_markets": "true", "limit": 50})
        except Exception as e:  # noqa: BLE001 — egy sorozat kiesése ne vigye a kört
            hibak.append(f"kalshi {sz}: {type(e).__name__}: {str(e)[:120]}")
            continue
        for e in (v or {}).get("events") or []:
            es = kalshi_esemeny(e)
            z = _ido(es["zaras"])
            if z and most < z <= hatar:
                esemenyek.append(es)
    lattuk = set()
    for cimke in POLY_CIMKEK:
        try:
            v = await lekero(f"{GAMMA_API}/events", {"tag_slug": cimke, "active": "true", "closed": "false",
                                                      "end_date_min": most.isoformat(), "end_date_max": hatar.isoformat(),
                                                      "limit": 100})
        except Exception as e:  # noqa: BLE001
            hibak.append(f"polymarket {cimke}: {type(e).__name__}: {str(e)[:120]}")
            continue
        for e in v if isinstance(v, list) else []:
            if e.get("slug") in lattuk:
                continue
            lattuk.add(e.get("slug"))
            es = pm_esemeny(e)
            z = _ido(es["zaras"])
            if z and most < z <= hatar:
                esemenyek.append(es)
    return esemenyek, hibak


def terkep_def(es: dict) -> dict | None:
    return TERKEP_KALSHI.get(es["sorozat_piac"]) if es["platform"] == "kalshi" else poly_terkep(es["esemeny_id"])


def jelzes_szoveg(e: dict, csak: list | None = None) -> str:
    """Árnyék-jelzés a Kommandantnak — kereskedési tanács NÉLKÜL."""
    sorok = [f"<b>Árnyék-jelzés — piac-eltérés</b> (csak napló és pontozás; nem befektetési tanács, nem kereskedünk)",
             f"{e['platform']} · {e['esemeny_id']} · {e['cim']} · zárás {str(e['zaras'])[:16]}",
             f"Saját sorozat: {e['sorozat']} ({e['eloszlas'].get('forras', '')[:80]}), 80%-os sáv "
             f"{_f(e['eloszlas']['also'], 2)}–{_f(e['eloszlas']['felso'], 2)}",
             f"Képesség-mérleg: {e['kepesseg']['ok']}"]
    for s in e["osszevetes"]["piacok"]:
        if s.get("jelzes") and (csak is None or s["piac_id"] in csak):
            sorok.append(f"• {s['kerdes_szoveg']}: mi {_f(s['p_mi'] * 100, 0)}%, piac {_f(s['p_piac'] * 100, 0)}% "
                         f"(közép, spread {_f((s.get('spread') or 0) * 100, 0)}¢) → él {_f(s['el'] * 100, 0)} pp")
    sorok.append(f"Előre rögzítve: #{e['_id']} · {e['_hash'][:12]}…")
    return "\n".join(sorok)


async def kor(hiv: Hivo, *, lekero: Lekero = httpx_lekero, nap="alap", telegram=None, most: datetime | None = None,
              k: Konfig | None = None, csak_terkep: bool = False) -> dict:
    """Egy kör. `csak_terkep`: semmi nem kerül a naplóba és Telegramra (élő feltérképezés, csak olvasás)."""
    if not bekapcsolva():
        return {"ki": True, "ok": "PIAC_ELTERES=0 — a modul nem fut"}
    k = k or konfig()
    most = most or datetime.now(timezone.utc)
    nap = (None if csak_terkep else naplo()) if nap == "alap" else nap
    esemenyek, hibak = await piacok_lekerese(lekero, most, k)
    merleg = merleg_fajlokbol(k.merleg_fajlok)
    if nap is not None:
        merleg += merleg_naplobol([s["payload"] for s in nap.sorok("feloldas")])
    korabbi: dict[tuple, set] = {}           # a már elküldött jelzések: a Telegram csak az ÚJ jelzésről szól
    if nap is not None:
        for s in nap.sorok("elore"):
            pl = s["payload"]
            korabbi.setdefault((pl.get("platform"), pl.get("esemeny_id")), set()).update(
                (pl.get("osszevetes") or {}).get("jelzesek") or [])
    palyak: dict[tuple, dict] = {}
    frissek: dict[tuple, str | None] = {}
    ki = {"most": most.isoformat(), "esemenyek": [], "hibak": hibak, "naplo": nap is not None, "jelzesek": 0,
          "merleg": merleg}
    for es in sorted(esemenyek, key=lambda x: (x["zaras"] or "", x["esemeny_id"] or "")):
        d = terkep_def(es)
        sor = {"platform": es["platform"], "esemeny_id": es["esemeny_id"], "cim": es["cim"], "idoszak": es["idoszak"],
               "zaras": es["zaras"], "n_piac": len(es["piacok"]), "pav_sertes": es.get("pav_sertes"),
               "kizarolagos": es.get("kizarolagos")}
        if not d or d.get("nincs"):
            sor["ok"] = (d or {}).get("nincs") or "nincs ilyen sorozatunk"
            ki["esemenyek"].append(sor)
            continue
        sor["sorozat"] = _sorozat_kulcs(*d["mi"]) if d.get("mi") else None
        sor["meres"] = d.get("meres")
        if d.get("kamat"):
            sor["ok"] = "kamatpiac: nincs feltétel nélküli kamat-előrejelzésünk (a szimulátoré feltételes, LLM-es)"
            sor["kepesseg"] = kepesseg_dontes(merleg, sor["sorozat"], "szimulator", k) if sor["sorozat"] else None
            ki["esemenyek"].append(sor)
            continue
        if d["mi"] not in palyak:
            palyak[d["mi"]] = await palya(hiv, *d["mi"])
            frissek[d["mi"]] = await legfrissebb(hiv, d["mi"][0], d["macro"])
        el = sajat_eloszlas(palyak[d["mi"]], es["idoszak"] or "", d, friss_idoszak=frissek[d["mi"]]) \
            if es["idoszak"] else {"hiba": "az esemény időszaka nem olvasható"}
        merleg_sajat = merleg + [r for r in [merleg_palyabol(sor["sorozat"], (palyak[d["mi"]] or {}).get("backtest"))] if r]
        kep = kepesseg_dontes(merleg_sajat, sor["sorozat"], "forecast_path", k)
        sor["kepesseg"] = kep
        if el.get("hiba"):
            sor["ok"] = el["hiba"]
            ki["esemenyek"].append(sor)
            continue
        ov = osszevet(es, el, d, kep, k, most)
        sor.update(eloszlas=el, osszevetes=ov)
        if not csak_terkep and nap is None:
            for p in ov["piacok"]:          # napló nélkül nincs előre rögzítés → jelzés sem
                p["jelzes"] = False
            ov["jelzesek"] = []
            sor["ok"] = "nincs napló (PIAC_ELTERES_PG_DSN): az összevetés megvan, de előre rögzítés és jelzés nincs"
        if nap is not None and ov["elozetes"]:
            payload = {"verzio": VERZIO, "platform": es["platform"], "esemeny_id": es["esemeny_id"], "cim": es["cim"],
                       "idoszak": es["idoszak"], "zaras": es["zaras"], "sorozat": sor["sorozat"], "mi": list(d["mi"]),
                       "macro": d["macro"], "racs": d.get("racs"), "meres": d.get("meres"),
                       "kizarolagos": es.get("kizarolagos"), "ar_forras": es.get("ar_forras"),
                       "pav_sertes": es.get("pav_sertes"), "ar_osszeg": es.get("ar_osszeg"),
                       "eloszlas": el, "kepesseg": {"jogosult": kep["jogosult"], "ok": kep["ok"], "rekord": kep["rekord"]},
                       "osszevetes": ov, "konfig": {f.name: getattr(k, f.name) for f in fields(Konfig)},
                       "piaci_ido": most.isoformat()}
            kulcs = f"{es['platform']}:{es['esemeny_id']}:{most.date().isoformat()}"
            try:
                r = nap.hozzafuz("elore", kulcs, payload)
            except Exception as e:  # noqa: BLE001 — a napló hibája: nincs rögzítés → nincs jelzés
                logger.error("piac_elteres: az előre rögzítés nem sikerült (%s): %s", kulcs, e)
                r, sor["ok"] = None, f"a napló hibája: {type(e).__name__} — jelzés nincs"
            sor["rogzitve"] = bool(r)
            uj_jel = [j for j in ov["jelzesek"] if j not in korabbi.get((es["platform"], es["esemeny_id"]), set())]
            if r and uj_jel and telegram is not None:
                try:
                    await telegram(jelzes_szoveg({**payload, "_id": r["id"], "_hash": r["sor_hash"]}, uj_jel))
                except Exception as e:  # noqa: BLE001
                    logger.error("piac_elteres: a Telegram nem ment ki: %s", e)
            if r:
                ki["jelzesek"] += len(ov["jelzesek"])
        ki["esemenyek"].append(sor)
    return ki


# ══ 10. ÜTEMEZÉS (a Bridge cron-szála percenként hívja) ═════════════════════════════════════════════════════════

KOR_ORA = 12              # Budapest: naponta egyszer, a 14:30-as amerikai közlések ELŐTT (a Kalshi 14:29-kor zár)
_UTOLSO: dict[str, str] = {}
_FUTO: set = set()


def cron_lepes(most_bp: datetime, *, hiv: Hivo | None = None, telegram=None) -> bool:
    """Naponta egyszer (12:00 után, Budapest): feloldás → kör; hétfőn a heti fitnesz is. A futás háttérfeladat; a
    kapcsoló KI vagy napló nélküli állapotban a kör csak összevet (a napló nélküli kör jelzést nem küld)."""
    try:
        if not bekapcsolva() or most_bp.hour < KOR_ORA:
            return False
        nap_ = most_bp.date().isoformat()
        if _UTOLSO.get("nap") == nap_:
            return False
        _UTOLSO["nap"] = nap_
        hiv = hiv or bridge_hiv

        async def _fut():
            k = konfig()
            n = naplo()
            most = datetime.now(timezone.utc)
            if n is not None:
                try:
                    await felold(n, hiv, k, most)
                except Exception as e:  # noqa: BLE001
                    logger.error("piac_elteres: feloldási hiba: %s", e)
            r = await kor(hiv, nap=n, telegram=telegram, most=most, k=k)
            logger.info("piac_elteres: kör kész — %d esemény, %d jelzés, napló: %s",
                        len(r.get("esemenyek") or []), r.get("jelzesek", 0), r.get("naplo"))
            if n is not None and most_bp.weekday() == 0 and telegram is not None:
                await telegram(heti_szoveg(heti_osszesito(n, k, most)))

        t = asyncio.get_running_loop().create_task(_fut())
        _FUTO.add(t)
        t.add_done_callback(_FUTO.discard)
        return True
    except Exception as e:  # noqa: BLE001 — az ütemező hibája ne állítsa meg a cront
        logger.error("piac_elteres: ütemezési hiba: %s", e)
        return False


# ══ 11. PARANCSSOR: élő feltérképezés (csak olvasás) ═══════════════════════════════════════════════════════════

def terkep_tabla(r: dict) -> str:
    ki = [f"Piac-eltérés — élő térkép ({r['most'][:16]} UTC), {len(r['esemenyek'])} gazdasági esemény a horizonton"]
    for e in r["esemenyek"]:
        fej = f"\n[{e['platform']}] {e['esemeny_id']} — {e['cim']} (időszak {e.get('idoszak')}, zárás {str(e.get('zaras'))[:16]})"
        ki.append(fej)
        if e.get("sorozat"):
            kep = e.get("kepesseg") or {}
            ki.append(f"   saját sorozat: {e['sorozat']} · képesség: {'JOGOSULT' if kep.get('jogosult') else 'nem jogosult'}"
                      f" — {kep.get('ok', '')}")
        if e.get("ok"):
            ki.append(f"   → {e['ok']}")
        el = e.get("eloszlas")
        if el and not el.get("hiba"):
            ki.append(f"   mi: 80%-os sáv {el['also']:.2f}–{el['felso']:.2f} (μ={el['mu']:.3f}, σ={el['sigma']:.3f}), "
                      f"utolsó tény {el.get('utolso_teny')}")
            for s in e["osszevetes"]["piacok"]:
                if s.get("p_mi") is None:
                    continue
                jel = "JELZÉS" if s.get("jelzes") else ("nagy él" if s.get("nagy_el") else "")
                ki.append(f"   {str(s['piac_id'])[:40]:40s} {str(s.get('kerdes'))[:44]:44s} p_mi={s['p_mi']:.3f} "
                          f"p_piac={s['p_piac']:.3f} él={s['el']:+.3f} spread={s.get('spread')} {jel}")
    if r.get("hibak"):
        ki.append("\nLekérési hibák: " + "; ".join(r["hibak"]))
    return "\n".join(ki)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Piac-eltérés (árnyék-mód): élő térkép, csak olvasás")
    ap.add_argument("parancs", choices=["terkep"])
    ap.add_argument("--json", help="a teljes eredmény ide (JSON)")
    a = ap.parse_args()

    eredmeny = asyncio.run(kor(bridge_hiv, csak_terkep=True))
    print(terkep_tabla(eredmeny))
    if a.json:
        Path(a.json).write_text(json.dumps(eredmeny, ensure_ascii=False, indent=1, default=str))
