"""KÖZLÉS-FIGYELŐ (2026-11-23) — „Igazunk van, tudjuk is, és előbb tudjuk.”

A hivatalos statisztikai és jegybanki közlések előre ismert időpontban jelennek meg (KSH, MNB, Eurostat, Destatis,
BLS, Fed). A figyelő a megjelenés pillanatában kiolvassa a számot, összeveti a saját előrejelzésünkkel (Makronóm
`forecast_path`, 80%-os sáv), a várakozással (ha a naptár adja) és az előző értékkel, azonnal szól a Kommandantnak
(Telegram), és X-TERVEZETET ír angolul és magyarul (`twitter_poszt.tervezet`: a kabin jóváhagyási útja, magától nem
posztol). Szöveg sablonból, LLM NÉLKÜL: a költség 0.

KÖZÖS FELÜLET (a B-képességnek — „eltérés a piactól”, `feat/piac-elteres`)
-------------------------------------------------------------------------
1. A LEZÁRT KÖZLÉSEK TÉNYLEGES ÉRTÉKE
   * `tenyleges_ertek(orszag, mutato, idoszak, *, naplo=None) -> dict | None`
       Az `elso_kozles` naplóból (Postgres, `ELSO_KOZLES_PG_DSN`) az ELSŐ közölt érték (`first_print`):
       {"ertek", "elso_lattuk" (a mi észlelésünk, UTC), "forras_url", "sorozat"}; None, ha a napló nem látta.
       A sorozat neve a naplóban: `kozles:<ORSZÁG>:<mutató>` (pl. `kozles:HU:cpi`, `kozles:US:nfp`,
       `kozles:HU:policy_rate`); az időszak 'ÉÉÉÉ-HH' / 'ÉÉÉÉ-Qn', kamatdöntésnél a döntés napja ('ÉÉÉÉ-HH-NN').
   * `lezart_kozlesek(*, sorozat=None, tol=None, ig=None, tar=None) -> list[dict]`
       Az észlelt közlések a `kozles_esemeny` táblából (ugyanaz a Postgres): {"kulcs", "sorozat", "orszag",
       "mutato", "idoszak", "ertek", "mertek", "hivatalos_ido", "hivatalos_ido_forras", "eszleles", "keses_mp",
       "forras_url", "elorejelzes" {pont, also, felso, mertek, idoszak}, "konszenzus", "elozo" {ertek, idoszak}}.
       `sorozat` a konfig kulcsa (pl. "HU_CPI"); `tol`/`ig` a tervezett közlési idő (datetime vagy ISO).
2. A KÖZELGŐ KÖZLÉSEK
   * `async kozelgo_kozlesek(*, orak=168, most=None, hiv=None, cfg=None) -> list[dict]`
       A következő `orak` óra figyelt közlései, mentés nélkül: {"kulcs", "sorozat", "orszag", "mutato", "nev_hu",
       "idoszak", "tervezett" (UTC ISO), "idopont_forras", "elorejelzes" {pont, also, felso, mertek} | None,
       "konszenzus", "olvashato" (bool)}. `hiv(eszkoz, parameterek) -> dict` a Makronóm-hívó (alap: `makronom_hiv`).
   * a tárolt napi terv: `tar_kapcsolat().esemenyek(tol, ig)` — ugyanazok a sorok, állapottal.
   A mértékre figyelj: az `elorejelzes.mertek` és a közlés `mertek`-e eltérhet (pl. KSH nemzeti CPI ↔ HICP-pálya);
   eltérő mértéket a figyelő sem vet össze.

HOGYAN MŰKÖDIK
--------------
* NAPI TERV (`tervez`): reggel és délben (`KOZLES_FIGYELO_TERV`, alap „07:00,12:00” Budapest; és induláskor) a
  következő 24 óra közlései a figyelt körből (`config/kozles_figyelo.json`). Az időpont HIVATALOS: a konfig listájából
  (forrással), és a StatData-naptár hivatalos tételeiből (`naptar_hivatalos`: a KSH gyorstájékoztató-naptára, a BLS
  hivatalos naptára — 11-24 óta a StatData adja, a konfig ezekből magától bővül; ha a naptár más időpontot mond, mint a
  konfig, a naptárat követjük, kimondva). A StatData többi naptári tétele (`naptar_minta`: FRED, Eurostat) BECSLÉS
  (2026-11-23: a US CPI-t 12-14-re tette, a BLS szerint 12-10; az euróövezeti gyors HICP-t 12-17-re, az Eurostat
  szerint 12-01) — csak ott, ahol a hivatalos lista nem fedi az ablakot, és csak a hivatalosan még nem ismert
  időszakra. A tervben minden közléshez: a referencia-időszak,
  az előrejelzésünk pillanatképe (a közlés ELŐTT rögzítve), az előző érték (alapvonal), a konszenzus, ha a naptár adja.
* ABLAK (`figyel`): T−1 perctől T+20 percig 25 másodpercenként egy Makronóm-hívás; az amerikai soroknál T+180 percig,
  percenként (a StatData a BLS-t közvetlenül olvassa, 2 perces gyorsítótárral és napi kerettel; ha a keret elfogy, a
  lánc a FRED-re esik, ami a BLS után 40–130 perccel frissít). Új közlés = az olvasott időszak eléri a
  referencia-időszakot és újabb az alapvonalnál (második becslésnél: más az érték/forrás); kamatnál a kamat vagy a
  döntés napja változik.
* ÉSZLELÉS (`eszlelt`): rögzítés a `kozles_esemeny`-be és az `elso_kozles`-be (`kozles:<ORSZÁG>:<mutató>`,
  `elso_lattuk` = a mi észlelésünk), egy Telegram-üzenet, két X-tervezet (EN, HU). EGY KÖZLÉS = EGY JELZÉS: a jelzést
  egy feltételes UPDATE foglalja (`jelezve IS NULL`), és ha nincs Postgres, a meglévő `action_drafts`-ban a
  `kozles:<kulcs>:en` tervezet léte jelzi, hogy már szóltunk (újraindulás után sem ismétel).
* KÉSÉS-MÉRÉS: `keses_mp` = észlelés − hivatalos idő (a forrás közlési ideje, ha adja — Eurostat `pub_date`;
  különben a naptári időpont). Heti összesítő: `heti_osszesito()`, a kabinban `GET /api/kozles`. Az első angol cikk
  ideje (Echolot) a `kozles_esemeny.elso_angol_cikk` oszlopba kerül majd.
* PÓTLÁS: ha az ablakban nem jelent meg (rossz naptár, újraindulás), a következő tervkör egyszer újraolvassa; ha
  közben megjelent, KIMONDOTT késéssel szól („késve láttuk”).

KAPCSOLÓ ÉS TÁR
---------------
`KOZLES_FIGYELO` alapból BE (a Kommandant kérte); 0 / ki / off → semmi nem fut. A Telegram csak a Kommandantnak megy
(a Bridge `_telegram_push`-a), az X csak tervezet. A tár Postgres (`ELSO_KOZLES_PG_DSN`, az `elso_kozles` mellett,
ugyanazzal a mintával: a séma csak akkor fut, ha a tábla még nincs); DSN nélkül memóriában fut, kimondva.
SQLite-tábla nincs (házi szabály).

ÉLŐ PRÓBA (csak olvasás): `python kozles_figyelo.py --elo-proba 7`
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import statistics
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

import elso_kozles

logger = logging.getLogger("kozles_figyelo")

KONFIG_UT = Path(__file__).resolve().parent / "config" / "kozles_figyelo.json"
FELADO = "kozles-figyelo"
BP = "Europe/Budapest"
TERV_ORAK_ALAP = "07:00,12:00"
TERV_HORIZONT_ORA = 24
POTLAS_ORA = 72
ABLAK_ALAP = {"elotte_perc": 1, "utana_perc": 20, "lekerdezes_s": 25}

ALLAPOTOK = ("tervezett", "figyel", "eszlelve", "nem_jelent_meg", "korabban_megjelent", "nincs_olvaso",
             "valtozatlan_nem_igazolt")
NAPTAR_TZ = {"ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York",
             "CET": "Europe/Brussels", "CEST": "Europe/Brussels", "UTC": "UTC", "GMT": "UTC"}
HONAP_HU = ("jan.", "febr.", "márc.", "ápr.", "máj.", "jún.", "júl.", "aug.", "szept.", "okt.", "nov.", "dec.")
HONAP_HU_HOSSZU = ("január", "február", "március", "április", "május", "június", "július", "augusztus",
                   "szeptember", "október", "november", "december")
HONAP_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
HONAP_EN_HOSSZU = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
                   "October", "November", "December")
NEGYEDEV_RO = ("I.", "II.", "III.", "IV.")


class KozlesHiba(RuntimeError):
    """A Makronóm-hívás nem adott használható választ."""


# ══ 1. KAPCSOLÓ, KONFIG, IDŐ ═════════════════════════════════════════════════

def bekapcsolva() -> bool:
    """`KOZLES_FIGYELO`: alapból BE; 0 / ki / off / false / nem → semmi."""
    return os.environ.get("KOZLES_FIGYELO", "1").strip().lower() not in ("0", "ki", "off", "false", "no", "nem")


_KONFIG: dict[str, dict] = {}


def konfig(ut: str | Path | None = None) -> dict:
    ut = str(ut or KONFIG_UT)
    if ut not in _KONFIG:
        with open(ut, encoding="utf-8") as f:
            _KONFIG[ut] = json.load(f)
    return _KONFIG[ut]


def sorozat_cfg(cfg: dict, kulcs: str) -> dict:
    for s in cfg["sorozatok"]:
        if s["kulcs"] == kulcs:
            return s
    raise KeyError(f"nincs ilyen figyelt sorozat: {kulcs}")


def _zona(nev: str):
    from zoneinfo import ZoneInfo
    return ZoneInfo(nev)


def _most() -> datetime:
    return datetime.now(timezone.utc)


def _utc(x) -> datetime | None:
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        d = x
    else:
        d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(x) -> str | None:
    d = _utc(x)
    return d.isoformat() if d else None


def helyi_ido(datum: str, ido: str, tz: str) -> datetime:
    """Egy helyi idejű időpont UTC-ben (a nyári időszámítással együtt)."""
    h, m = (int(x) for x in ido.split(":")[:2])
    d = date.fromisoformat(datum)
    return datetime(d.year, d.month, d.day, h, m, tzinfo=_zona(tz)).astimezone(timezone.utc)


def bp(d) -> datetime:
    return _utc(d).astimezone(_zona(BP))


def ablak(cfg: dict, s: dict) -> dict:
    nev = ((s.get("olvaso") or {}).get("ablak")) or "alap"
    return {**ABLAK_ALAP, **(cfg.get("ablak") or {}).get("alap", {}), **(cfg.get("ablak") or {}).get(nev, {})}


def ablak_hatarok(cfg: dict, e: dict) -> tuple[datetime, datetime, dict]:
    a = ablak(cfg, sorozat_cfg(cfg, e["sorozat"]))
    t = _utc(e["tervezett"])
    return t - timedelta(minutes=a["elotte_perc"]), t + timedelta(minutes=a["utana_perc"]), a


# ══ 2. IDŐSZAK ═══════════════════════════════════════════════════════════════

def idoszak_kulcs(s: str) -> tuple:
    s = str(s)
    if re.fullmatch(r"\d{4}-Q[1-4]", s):
        return (int(s[:4]), int(s[-1]) * 3, 0)
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return (int(s[:4]), int(s[5:7]), 0)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return (int(s[:4]), int(s[5:7]), int(s[8:10]))
    raise ValueError(f"ismeretlen időszak: {s!r}")


def ref_idoszak(gyakorisag: str, kozles_nap: date, eltolas: int) -> str:
    """A közlés napjából a referencia-időszak (havi: a közlés hónapja − eltolás; negyedéves: a negyedéve − eltolás)."""
    if gyakorisag == "Q":
        n = kozles_nap.year * 4 + (kozles_nap.month - 1) // 3 - eltolas
        return f"{n // 4}-Q{n % 4 + 1}"
    n = kozles_nap.year * 12 + kozles_nap.month - 1 - eltolas
    return f"{n // 12}-{n % 12 + 1:02d}"


def idoszak_hu(s: str | None, hosszu: bool = False) -> str:
    if not s:
        return ""
    if "-Q" in s:
        return f"{s[:4]}. {NEGYEDEV_RO[int(s[-1]) - 1]} negyedév" if hosszu else f"{NEGYEDEV_RO[int(s[-1]) - 1]} n.év"
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return f"{s[:4]}. {HONAP_HU_HOSSZU[int(s[5:7]) - 1]}" if hosszu else HONAP_HU[int(s[5:7]) - 1]
    return s


def idoszak_en(s: str | None) -> str:
    if not s:
        return ""
    if "-Q" in s:
        return f"Q{s[-1]} {s[:4]}"
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return f"{HONAP_EN[int(s[5:7]) - 1]} {s[:4]}"
    return s


# ══ 3. OLVASÓK: a Makronóm válaszából {ertek, idoszak, forras_url, hivatalos_ido, ...} ═══════════════════

def _szam(x) -> float | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)) and math.isfinite(x):
        return float(x)
    return None


_KSH_URL = re.compile(r"/gyorstajekoztatok/[a-z]+/[a-z]+(\d{2})(\d{2})\.html")


def _ksh_idoszak(url: str | None, gyakorisag: str) -> str | None:
    m = _KSH_URL.search(url or "")
    if not m:
        return None
    ev, ho = 2000 + int(m[1]), int(m[2])
    return f"{ev}-Q{(ho - 1) // 3 + 1}" if gyakorisag == "Q" else f"{ev}-{ho:02d}"


def _kinyer_macro(s: dict, v: dict) -> dict | None:
    """`get_macro_indicator`: csak hivatalos érték; az időszak a KSH-gyorstájékoztató címéből (far2611 = 2026-11), ha
    onnan jön, különben a `period_returned`. A lekérés napját időszaknak nem fogadjuk el (kivéve a kamatot)."""
    if v.get("confidence") not in (None, "official") or v.get("status") in ("missing", "rate_limited"):
        return None
    x = _szam(v.get("value"))
    if x is None:
        return None
    kamat = s.get("tipus") == "kamat"
    per = _ksh_idoszak(v.get("source_url"), s.get("gyakorisag", "M"))
    if per is None and not v.get("period_is_fetch_date"):
        per = v.get("period_returned") or v.get("period")
    if per is None and not kamat:
        return None
    ki = {"ertek": x, "idoszak": str(per) if per else None, "forras": v.get("source_used"),
          "forras_url": v.get("source_url")}
    if kamat:
        ki["dontes_nap"] = v.get("decision_date") or v.get("effective_from")
    return ki


def _kinyer_fred(s: dict, v: dict) -> dict | None:
    """`get_fred_data`: a legújabb nem üres megfigyelés; az időszak a megfigyelés hónapja. A `last_updated` a FRED
    frissítésének ideje (nem a BLS közlési ideje)."""
    sid = str((v.get("series") or {}).get("id") or "")
    for o in sorted([o for o in v.get("data") or [] if isinstance(o, dict)], key=lambda o: str(o.get("date")),
                    reverse=True):
        x = _szam(o.get("value"))
        if x is not None and o.get("date"):
            d = str(o["date"])
            per = f"{d[:4]}-Q{(int(d[5:7]) - 1) // 3 + 1}" if s.get("gyakorisag") == "Q" else d[:7]
            return {"ertek": x, "idoszak": per, "forras": f"FRED {sid}",
                    "forras_url": f"https://fred.stlouisfed.org/series/{sid}" if sid else None,
                    "forras_frissult": (v.get("series") or {}).get("last_updated")}
    return None


_EU_HICP = re.compile(r"annual inflation is expected to be (-?\d+(?:\.\d+)?)% in (" + "|".join(HONAP_EN_HOSSZU)
                      + r") (\d{4})", re.IGNORECASE)
_EU_GDP = re.compile(r"In the (first|second|third|fourth) quarter of (\d{4}), seasonally adjusted GDP "
                     r"(increased|decreased|fell|rose|remained stable|was stable)(?: by (\d+(?:\.\d+)?)%)? "
                     r"in the euro area", re.IGNORECASE)
_NEGYEDEV_EN = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def _eu_tetelek(v: dict) -> list[dict]:
    return sorted([t for t in v.get("items") or [] if isinstance(t, dict)], key=lambda t: str(t.get("pub_date")),
                  reverse=True)


def _kinyer_eu_hicp(s: dict, v: dict) -> dict | None:
    """Eurostat Atom: a legújabb GYORSBECSLÉS („is expected to be X% in <hónap> <év>”); a `pub_date` a közlési idő."""
    for t in _eu_tetelek(v):
        m = _EU_HICP.search(str(t.get("description") or ""))
        if m and "flash" in str(t.get("description") or "").lower():
            return {"ertek": float(m[1]), "idoszak": f"{m[3]}-{HONAP_EN_HOSSZU.index(m[2].capitalize()) + 1:02d}",
                    "forras": "Eurostat (gyorsbecslés)", "forras_url": t.get("link"),
                    "hivatalos_ido": _iso(t.get("pub_date"))}
    return None


def _kinyer_eu_gdp(s: dict, v: dict) -> dict | None:
    for t in _eu_tetelek(v):
        d = str(t.get("description") or "")
        m = _EU_GDP.search(d)
        if not m or "flash estimate" not in d.lower():
            continue
        if m[4] is None and "stable" not in m[3].lower():
            continue
        x = 0.0 if m[4] is None else float(m[4]) * (-1 if m[3].lower() in ("decreased", "fell") else 1)
        return {"ertek": x, "idoszak": f"{m[2]}-Q{_NEGYEDEV_EN[m[1].lower()]}", "forras": "Eurostat (gyorsbecslés)",
                "forras_url": t.get("link"), "hivatalos_ido": _iso(t.get("pub_date"))}
    return None


KINYEROK = {"macro_indicator": _kinyer_macro, "fred": _kinyer_fred, "eurostat_flash_hicp": _kinyer_eu_hicp,
            "eurostat_flash_gdp": _kinyer_eu_gdp}


def olvasat(s: dict, valasz) -> dict | None:
    """A sorozat olvasójának válaszából egy olvasat, vagy None (nem használható)."""
    o = s.get("olvaso")
    if not o or not isinstance(valasz, dict) or valasz.get("hiba") or valasz.get("error"):
        return None
    try:
        return KINYEROK[o["kinyero"]](s, valasz)
    except (KeyError, TypeError, ValueError, IndexError) as e:
        logger.warning("kozles_figyelo: %s olvasata nem értelmezhető: %s", s["kulcs"], e)
        return None


def ujjlenyomat(o: dict | None) -> tuple | None:
    if not o:
        return None
    return (o.get("idoszak"), elso_kozles.ertek_kanon(o["ertek"]), o.get("forras_url"))


def uj_kozles(e: dict, olv: dict | None, alap: dict | None) -> bool:
    """Megjelent-e az esemény közlése: az olvasat a referencia-időszakot hozza és újabb az alapvonalnál (második
    becslésnél: más az ujjlenyomata); kamatnál a kamat vagy a döntés napja változott."""
    if not olv:
        return False
    if e.get("tipus") == "kamat":
        if not alap:
            return False
        if elso_kozles.ertek_kanon(olv["ertek"]) != elso_kozles.ertek_kanon(alap["ertek"]):
            return True
        d = olv.get("dontes_nap")
        return bool(d and d != alap.get("dontes_nap") and str(d) >= str(e.get("idoszak") or ""))
    if not olv.get("idoszak"):
        return False
    ref = e.get("idoszak")
    if ref and idoszak_kulcs(olv["idoszak"]) < idoszak_kulcs(ref):
        return False
    if not alap:
        return True
    if e.get("ugyanaz_az_idoszak"):
        return ujjlenyomat(olv) != ujjlenyomat(alap)
    return not alap.get("idoszak") or idoszak_kulcs(olv["idoszak"]) > idoszak_kulcs(alap["idoszak"])


def korabban_megjelent(e: dict, alap: dict | None) -> bool:
    """A terv idején az alapvonal már a referencia-időszakot mutatja: a közlés a figyelés előtt megjelent."""
    if e.get("tipus") == "kamat" or e.get("ugyanaz_az_idoszak") or not alap or not alap.get("idoszak"):
        return False
    return bool(e.get("idoszak")) and idoszak_kulcs(alap["idoszak"]) >= idoszak_kulcs(e["idoszak"])


# ══ 4. A MAKRONÓM-HÍVÓ ═══════════════════════════════════════════════════════

Hivo = Callable[[str, dict], Awaitable[dict]]


async def makronom_hiv(eszkoz: str, parameterek: dict) -> dict:
    """A Bridge meglévő útja a Makronómhoz (`kulso_mcp.hiv("makronom", …)`): a válasz JSON-ja, vagy KozlesHiba."""
    import kulso_mcp
    s = await kulso_mcp.hiv("makronom", eszkoz, parameterek, max_kar=5_000_000)
    try:
        v = json.loads(s)
    except (TypeError, ValueError):
        raise KozlesHiba(f"{eszkoz}: nem JSON válasz ({str(s)[:120]})")
    if isinstance(v, dict) and (v.get("hiba") or v.get("error")):
        raise KozlesHiba(f"{eszkoz}: {str(v.get('hiba') or v.get('error'))[:200]}")
    return v


async def _biztos(hiv: Hivo, eszkoz: str, par: dict) -> dict | None:
    try:
        v = await hiv(eszkoz, dict(par))
        return v if isinstance(v, dict) else None
    except Exception as e:  # noqa: BLE001 — egy hívás bukása nem állítja meg a tervet / az ablakot
        logger.warning("kozles_figyelo: %s hívás nem sikerült: %s", eszkoz, str(e)[:200])
        return None


async def olvas(s: dict, hiv: Hivo) -> dict | None:
    if not s.get("olvaso"):
        return None
    return olvasat(s, await _biztos(hiv, s["olvaso"]["eszkoz"], s["olvaso"]["parameterek"]))


# ══ 5. A TÁR: kozles_esemeny (Postgres; tesztben és DSN nélkül memória — SQLite soha) ══════════════════

OSZLOPOK = ("sorozat", "orszag", "mutato", "idoszak", "tervezett", "idopont_forras", "kiadas", "allapot", "ertek",
            "hivatalos_ido", "eszleles", "keses_mp", "forras_url", "jelezve", "elso_angol_cikk")
IDO_OSZLOPOK = ("tervezett", "hivatalos_ido", "eszleles", "jelezve", "elso_angol_cikk")

SEMA = """
CREATE TABLE IF NOT EXISTS kozles_esemeny (
    kulcs           text PRIMARY KEY,          -- 'HU_CPI|2026-11|2026-12-08'
    sorozat         text NOT NULL,             -- a konfig kulcsa ('HU_CPI')
    orszag          text,
    mutato          text,
    idoszak         text,                      -- referencia-időszak; kamatdöntésnél a döntés napja
    tervezett       timestamptz NOT NULL,      -- a hivatalos (naptári) közlési idő
    idopont_forras  text,
    kiadas          text,                      -- 'első becslés' / 'második becslés' / NULL
    allapot         text NOT NULL,             -- tervezett | figyel | eszlelve | nem_jelent_meg | korabban_megjelent | …
    ertek           double precision,          -- a közölt érték (észleléskor)
    hivatalos_ido   timestamptz,               -- a forrás közlési ideje, ha adja; különben a naptári idő
    eszleles        timestamptz,               -- a mi észlelésünk
    keses_mp        double precision,          -- eszleles − hivatalos_ido
    forras_url      text,
    jelezve         timestamptz,               -- a jelzés foglalása (egy közlés = egy jelzés)
    elso_angol_cikk timestamptz,               -- később az Echolotból
    adat            jsonb NOT NULL DEFAULT '{}'::jsonb,   -- előrejelzés, konszenzus, előző érték, tervezetek, …
    letrehozva      timestamptz NOT NULL DEFAULT now(),
    frissitve       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kozles_esemeny_tervezett_idx ON kozles_esemeny (tervezett);
CREATE INDEX IF NOT EXISTS kozles_esemeny_sorozat_idx ON kozles_esemeny (sorozat, idoszak);
"""


def _normal(e: dict) -> dict:
    ki = dict(e)
    for k in IDO_OSZLOPOK:
        if ki.get(k) is not None:
            ki[k] = _iso(ki[k])
    return ki


class MemoriaTar:
    """A PgTar felülete memóriában (teszt; élesben DSN nélkül — újraindításkor elvész, ezt a terv kimondja)."""
    tartos = False

    def __init__(self):
        self._e: dict[str, dict] = {}
        self._zar = threading.Lock()

    def beszur(self, e: dict) -> bool:
        with self._zar:
            if e["kulcs"] in self._e:
                return False
            self._e[e["kulcs"]] = _normal(e)
            return True

    def frissit(self, kulcs: str, mezok: dict) -> None:
        with self._zar:
            if kulcs in self._e:
                self._e[kulcs].update(_normal(mezok))

    def jelzes_foglal(self, kulcs: str) -> bool:
        with self._zar:
            e = self._e.get(kulcs)
            if e is None or e.get("jelezve"):
                return False
            e["jelezve"] = _iso(_most())
            return True

    def esemeny(self, kulcs: str) -> dict | None:
        with self._zar:
            return dict(self._e[kulcs]) if kulcs in self._e else None

    def esemenyek(self, tol=None, ig=None, *, allapotok=None, sorozat=None) -> list[dict]:
        tol, ig = _utc(tol), _utc(ig)
        with self._zar:
            ki = [dict(e) for e in self._e.values()
                  if (tol is None or _utc(e["tervezett"]) >= tol) and (ig is None or _utc(e["tervezett"]) <= ig)
                  and (allapotok is None or e["allapot"] in allapotok) and (sorozat is None or e["sorozat"] == sorozat)]
        return sorted(ki, key=lambda e: (e["tervezett"], e["kulcs"]))


class PgTar:
    """A `kozles_esemeny` tábla Postgresben (az `elso_kozles` mellett, ugyanazzal a DSN-nel). A séma csak akkor fut,
    ha a tábla még nincs (élő táblán nincs DDL)."""
    tartos = True

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._sema_kesz = False

    def _k(self):
        import psycopg
        k = psycopg.connect(self.dsn, connect_timeout=5, autocommit=True)
        if not self._sema_kesz:
            if k.execute("SELECT to_regclass('kozles_esemeny')").fetchone()[0] is None:
                k.execute(SEMA)
            self._sema_kesz = True
        return k

    @staticmethod
    def _szet(mezok: dict) -> tuple[dict, dict]:
        osz = {k: v for k, v in mezok.items() if k in OSZLOPOK}
        adat = {k: v for k, v in mezok.items() if k not in OSZLOPOK and k != "kulcs"}
        return osz, adat

    def beszur(self, e: dict) -> bool:
        osz, adat = self._szet(_normal(e))
        nevek = ["kulcs", *osz, "adat"]
        with self._k() as k:
            c = k.execute(f"INSERT INTO kozles_esemeny ({', '.join(nevek)}) VALUES "
                          f"({', '.join(['%s'] * len(nevek))}) ON CONFLICT (kulcs) DO NOTHING",
                          [e["kulcs"], *osz.values(), json.dumps(adat, ensure_ascii=False, default=str)])
            return c.rowcount == 1

    def frissit(self, kulcs: str, mezok: dict) -> None:
        osz, adat = self._szet(_normal(mezok))
        sets = [f"{k} = %s" for k in osz] + ["adat = adat || %s::jsonb", "frissitve = now()"]
        with self._k() as k:
            k.execute(f"UPDATE kozles_esemeny SET {', '.join(sets)} WHERE kulcs = %s",
                      [*osz.values(), json.dumps(adat, ensure_ascii=False, default=str), kulcs])

    def jelzes_foglal(self, kulcs: str) -> bool:
        with self._k() as k:
            return k.execute("UPDATE kozles_esemeny SET jelezve = now(), frissitve = now() WHERE kulcs = %s AND "
                             "jelezve IS NULL RETURNING kulcs", [kulcs]).fetchone() is not None

    def _sor(self, r: dict) -> dict:
        ki = {**(r.pop("adat") or {}), **{k: v for k, v in r.items() if k not in ("letrehozva", "frissitve")}}
        return _normal(ki)

    def esemeny(self, kulcs: str) -> dict | None:
        from psycopg.rows import dict_row
        with self._k() as k:
            k.row_factory = dict_row
            r = k.execute("SELECT * FROM kozles_esemeny WHERE kulcs = %s", [kulcs]).fetchone()
        return self._sor(dict(r)) if r else None

    def esemenyek(self, tol=None, ig=None, *, allapotok=None, sorozat=None) -> list[dict]:
        from psycopg.rows import dict_row
        felt, par = [], []
        for f, v in (("tervezett >= %s", _utc(tol)), ("tervezett <= %s", _utc(ig)), ("sorozat = %s", sorozat)):
            if v is not None:
                felt.append(f)
                par.append(v)
        if allapotok:
            felt.append("allapot = ANY(%s)")
            par.append(list(allapotok))
        q = "SELECT * FROM kozles_esemeny" + (" WHERE " + " AND ".join(felt) if felt else "") + \
            " ORDER BY tervezett, kulcs"
        with self._k() as k:
            k.row_factory = dict_row
            return [self._sor(dict(r)) for r in k.execute(q, par).fetchall()]


_TAR: dict[str, object] = {}


def _dsn() -> str:
    return os.environ.get("ELSO_KOZLES_PG_DSN", "").strip()


def tar_kapcsolat():
    """A folyamat tára: Postgres, ha `ELSO_KOZLES_PG_DSN` van és a psycopg telepítve; különben memória (kimondva)."""
    dsn = _dsn()
    kulcs = dsn or "memoria"
    if kulcs not in _TAR:
        if dsn:
            try:
                import psycopg  # noqa: F401
                _TAR[kulcs] = PgTar(dsn)
            except ImportError:
                logger.warning("kozles_figyelo: a psycopg nincs telepítve — a tár memóriában fut")
                _TAR[kulcs] = MemoriaTar()
        else:
            logger.warning("kozles_figyelo: nincs ELSO_KOZLES_PG_DSN — a tár memóriában fut (újraindításkor elvész)")
            _TAR[kulcs] = MemoriaTar()
    return _TAR[kulcs]


_NAPLO_PG: dict[str, elso_kozles.PgNaplo] = {}


def _naplo(naplo=None):
    """Az `elso_kozles` napló: a megadott, a folyamaté (ha a hook kapcsolója BE), vagy közvetlenül a DSN-ről."""
    if naplo is not None:
        return naplo
    n = elso_kozles.naplo()
    if n is not None:
        return n
    dsn = _dsn()
    if not dsn:
        return None
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return None
    return _NAPLO_PG.setdefault(dsn, elso_kozles.PgNaplo(dsn))


def naplo_sorozat(e: dict) -> str:
    return f"kozles:{e['orszag']}:{e['mutato']}"


# ══ 6. A NAPI TERV ═══════════════════════════════════════════════════════════

def esemeny_kulcs(sorozat: str, idoszak: str | None, tervezett: datetime, tz: str) -> str:
    return f"{sorozat}|{idoszak or ''}|{tervezett.astimezone(_zona(tz)).date().isoformat()}"


def _alap_esemeny(s: dict, tervezett: datetime, tz: str, idoszak: str | None, forras: str, **tobb) -> dict:
    if s.get("tipus") == "kamat":
        idoszak = tervezett.astimezone(_zona(tz)).date().isoformat()
    elif not idoszak:
        idoszak = ref_idoszak(s.get("gyakorisag", "M"), tervezett.astimezone(_zona(tz)).date(),
                              int(s.get("ref_eltolas", 1)))
    return {"kulcs": esemeny_kulcs(s["kulcs"], idoszak, tervezett, tz), "sorozat": s["kulcs"], "orszag": s["orszag"],
            "mutato": s["mutato"], "tipus": s.get("tipus", "szint"), "idoszak": idoszak, "tervezett": _iso(tervezett),
            "idopont_forras": forras, "kiadas": tobb.get("kiadas"),
            "ugyanaz_az_idoszak": bool(tobb.get("ugyanaz_az_idoszak")), "nev_hu": s["nev_hu"],
            "mertek": (s.get("olvaso") or {}).get("mertek"), "konszenzus": tobb.get("konszenzus"),
            "allapot": "tervezett"}


def hivatalos_esemenyek(s: dict, tol: datetime | None = None, ig: datetime | None = None) -> list[dict]:
    """A konfig hivatalos listájának eseményei (`tol`/`ig` nélkül mind)."""
    ki = []
    for i in s.get("idopontok") or []:
        t = helyi_ido(i["datum"], i["ido"], i["tz"])
        if (tol is None or tol <= t) and (ig is None or t <= ig):
            ki.append(_alap_esemeny(s, t, i["tz"], i.get("idoszak"), f"hivatalos: {i.get('forras', '?')}",
                                    kiadas=i.get("kiadas"), ugyanaz_az_idoszak=i.get("ugyanaz_az_idoszak")))
    return ki


def hivatalos_fedi(s: dict, ig: datetime, naptarbol: list[dict] | None = None,
                   horizont: datetime | None = None) -> bool:
    """A hivatalos lista (a konfigé és a StatData-naptár hivatalos tételei) a figyelt ablak végéig (vagy azon túl) tart,
    vagy a hivatal saját naptára (`horizont`) ellát az ablak végéig: a StatData-becslés erre a sorozatra nem kell."""
    if horizont is not None and horizont >= ig:
        return True
    ido = [helyi_ido(i["datum"], i["ido"], i["tz"]) for i in s.get("idopontok") or []]
    ido += [_utc(e["tervezett"]) for e in naptarbol or []]
    return bool(ido) and max(ido) >= ig


def hivatalos_vege(s: dict, naptarbol: list[dict] | None = None) -> str | None:
    """Meddig tart a hivatalos lista (konfig + StatData-naptár hivatalos tételei), 'ÉÉÉÉ-HH-NN'."""
    napok = [i["datum"] for i in s.get("idopontok") or []]
    napok += [bp(e["tervezett"]).date().isoformat() for e in naptarbol or []]
    return max(napok) if napok else None


def naptar_ido(e: dict) -> datetime | None:
    """A StatData-naptár „08:30 ET” / „11:00 CET” idejéből UTC (a helyi zóna nyári idejével)."""
    m = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*([A-Z]{2,4})?\s*", str(e.get("time") or ""))
    if not m or not e.get("date"):
        return None
    return helyi_ido(str(e["date"])[:10], m[1], NAPTAR_TZ.get(m[2] or "UTC", "UTC"))


def naptar_zona(ne: dict) -> str:
    """A StatData-naptár tételének időzónája („08:30 ET” → America/New_York, „08:30 CET” → Europe/Brussels)."""
    m = re.fullmatch(r"\s*\d{1,2}:\d{2}\s*([A-Z]{2,4})?\s*", str(ne.get("time") or ""))
    return NAPTAR_TZ.get((m[1] if m else None) or "UTC", "UTC")


def naptar_illik(s: dict, ne: dict, kulcs: str = "naptar_minta") -> bool:
    """Illik-e a StatData-naptár tétele a sorozat mintáihoz (`naptar_minta`: becslés; `naptar_hivatalos`: a hivatal
    saját naptára — KSH-katalógus, BLS-naptár)."""
    for minta in s.get(kulcs) or []:
        jo = True
        for k, v in minta.items():
            if k == "indicator_re":
                jo = jo and bool(re.search(v, str(ne.get("indicator") or "")))
            else:
                jo = jo and str(ne.get(k) or "") == str(v)
        if jo:
            return True
    return False


def naptar_horizont(s: dict, naptar: list[dict]) -> datetime | None:
    """Meddig lát előre a sorozat hivatalos naptára: a `naptar_hivatalos` minták FORRÁSÁNAK (pl. a KSH teljes
    gyorstájékoztató-naptára, a BLS éves naptára) legkésőbbi tétele, mutatótól függetlenül. A hivatal naptára teljes:
    ami a horizonton belül nincs benne, azt nem közlik."""
    forrasok = [{k: v for k, v in m.items() if k in ("source", "official")} for m in s.get("naptar_hivatalos") or []]
    ido = [t for ne in naptar if any(f and all(str(ne.get(k) or "") == str(v) for k, v in f.items()) for f in forrasok)
           and (t := naptar_ido(ne)) is not None]
    return max(ido) if ido else None


def _kiadas_ugyanaz(kiadas: str | None) -> bool:
    """A második (harmadik …) becslés ugyanannak az időszaknak az újabb közlése; az első becslés új időszak."""
    k = str(kiadas or "").strip().lower()
    return bool(k) and not k.startswith("első") and "előzetes" not in k


def naptar_hivatalos_esemenyek(s: dict, naptar: list[dict]) -> list[dict]:
    """A StatData-naptár HIVATALOS tételei erre a sorozatra (a konfig `naptar_hivatalos` mintái szerint: a KSH
    gyorstájékoztató-naptára, a BLS hivatalos naptára) — ablak nélkül, eseményként. A referencia-időszak és a kiadás
    (első / második becslés) a naptárból jön, ha adja."""
    ki = []
    for ne in naptar:
        t = naptar_ido(ne)
        if t is None or not naptar_illik(s, ne, "naptar_hivatalos"):
            continue
        kiadas = ne.get("estimate") or None
        e = _alap_esemeny(s, t, naptar_zona(ne), ne.get("reference_period"),
                          f"hivatalos: {ne.get('source') or '?'}-naptár (StatData)", kiadas=kiadas,
                          ugyanaz_az_idoszak=_kiadas_ugyanaz(kiadas), konszenzus=_konszenzus(ne))
        if ne.get("url"):
            e["kozles_url"] = ne["url"]
        ki.append(e)
    return ki


def hivatalos_osszefesul(s: dict, naptarbol: list[dict], tol: datetime, ig: datetime) -> tuple[list[dict], list[str]]:
    """A konfig hivatalos listája + a StatData-naptár hivatalos tételei, az ablakra szűrve. Ugyanaz a közlés (időszak +
    kiadás) egyszer kerül be; ha a naptár MÁS időpontot mond, mint a konfig, a naptár az újabb (élő) forrás — azt
    követjük, kimondva. → (események, figyelmeztetések)"""
    figy: list[str] = []
    ki = {(e["idoszak"], e.get("kiadas") or ""): e for e in hivatalos_esemenyek(s)}
    for c in naptarbol:
        k = (c["idoszak"], c.get("kiadas") or "")
        regi = ki.get(k)
        if regi is not None:
            if abs((_utc(regi["tervezett"]) - _utc(c["tervezett"])).total_seconds()) < 3600:
                continue                                    # ugyanaz: a konfig forrásmegjelölése marad
            figy.append(f"{s['kulcs']} {c['idoszak']}: a hivatalos naptár (StatData) {bp(c['tervezett']):%m-%d %H:%M}"
                        f"-t mond, a konfig {bp(regi['tervezett']):%m-%d %H:%M}-t — a naptárat követjük")
        ki[k] = c
    return [e for e in ki.values() if tol <= _utc(e["tervezett"]) <= ig], figy


def _konszenzus(ne: dict) -> float | None:
    for k in ("consensus", "expected", "forecast", "konszenzus"):
        x = _szam(ne.get(k))
        if x is not None:
            return x
    return None


def elorejelzes_pont(palya: dict | None, idoszak: str | None, mertek: str | None) -> dict | None:
    """A Makronóm forecast_path pontja a referencia-időszakra, 80%-os sávval — ha az időszak még előrejelzés (nem
    tény)."""
    if not isinstance(palya, dict) or palya.get("status") not in (None, "ok") or not idoszak:
        return None
    for p in palya.get("path") or []:
        if isinstance(p, dict) and str(p.get("period")) == idoszak and _szam(p.get("value")) is not None:
            return {"pont": _szam(p["value"]), "also": _szam(p.get("low")), "felso": _szam(p.get("high")),
                    "idoszak": idoszak, "mertek": mertek, "forras": "Makronóm forecast_path",
                    "keszult": palya.get("generated_at")}
    return None


#: A StatData-naptárból legalább ennyi napot kérünk: a hivatal naptárának horizontja (`naptar_horizont`) egy hétvégén
#: át is az ablakon túl érjen (24 órás ablakra 2 nap kevés: péntek délben a következő KSH-tétel hétfőn van).
NAPTAR_NAPOK_MIN = 14


async def _naptar(hiv: Hivo, orak: int) -> tuple[list[dict], str | None]:
    v = await _biztos(hiv, "get_economic_calendar", {"days_ahead": min(90, max(NAPTAR_NAPOK_MIN,
                                                                               math.ceil(orak / 24) + 1)),
                                                     "region": "all"})
    if v is None:
        return [], "a StatData-naptár nem érhető el"
    return [e for e in v.get("events") or [] if isinstance(e, dict)], None


async def tervez(*, most: datetime | None = None, hiv: Hivo | None = None, cfg: dict | None = None,
                 orak: int = TERV_HORIZONT_ORA, alapot_olvas: bool = True, elorejelzest: bool = True) -> dict:
    """A következő `orak` óra figyelt közlései: {"esemenyek": [...], "figyelmeztetesek": [...]}. Mellékhatás nélkül
    (a mentés a hívóé). Az alapvonal-olvasás az előző értéket hozza, és kiszűri a már megjelent közlést."""
    most = _utc(most) or _most()
    hiv = hiv or makronom_hiv
    cfg = cfg or konfig()
    ig = most + timedelta(hours=orak)
    naptar, hiba = await _naptar(hiv, orak)
    figy = [hiba] if hiba else []
    esemenyek: list[dict] = []
    for s in cfg["sorozatok"]:
        naptarbol = naptar_hivatalos_esemenyek(s, naptar)
        sajat, osszefesules = hivatalos_osszefesul(s, naptarbol, most, ig)
        figy.extend(osszefesules)
        fedi = hivatalos_fedi(s, ig, naptarbol, naptar_horizont(s, naptar))
        if not fedi:
            napok = {_utc(e["tervezett"]).date() for e in sajat}
            idoszakok = {e["idoszak"] for e in sajat}
            for ne in naptar:
                t = naptar_ido(ne)
                if t is None or not (most <= t <= ig) or not naptar_illik(s, ne) or t.date() in napok:
                    continue
                b = _alap_esemeny(s, t, BP, ne.get("reference_period"),
                                  f"StatData-naptár ({ne.get('source')}, becslés)", konszenzus=_konszenzus(ne))
                if b["idoszak"] not in idoszakok:           # a hivatalosan már ismert időszakra nem kell becslés
                    sajat.append(b)
        else:
            for ne in naptar:                         # a hivatalos és a StatData eltérése: a StatData javításához
                t = naptar_ido(ne)
                if t and naptar_illik(s, ne) and most <= t <= ig and not any(
                        abs((_utc(e["tervezett"]) - t).total_seconds()) < 3600 for e in sajat):
                    figy.append(f"{s['kulcs']}: a StatData-naptár {bp(t):%m-%d %H:%M}-t mond, a hivatalos lista "
                                f"nem (a becslés eltér)")
        if not s.get("idopontok") and not naptarbol:
            figy.append(f"{s['kulcs']}: nincs hivatalos időpont a konfigban" + (
                " — csak a StatData becslése" if s.get("naptar_minta") else " — a konfigba kell"))
        elif not fedi:
            figy.append(f"{s['kulcs']}: a hivatalos lista {hivatalos_vege(s, naptarbol)}-ig tart — bővíteni kell"
                        + (" (addig a StatData becslése)" if s.get("naptar_minta") else ""))
        for e in sajat:
            if not s.get("olvaso"):
                e["allapot"] = "nincs_olvaso"
                e["megjegyzes"] = s.get("olvaso_hiany") or "nincs olvasó"
            elif (s.get("olvaso") or {}).get("figyelmeztetes"):
                e["megjegyzes"] = s["olvaso"]["figyelmeztetes"]
        esemenyek.extend(sajat)

    palyak: dict[tuple, dict | None] = {}
    if elorejelzest:
        kell = {(f["country"], f["indicator"], f.get("freq", "")) for e in esemenyek
                if (f := sorozat_cfg(cfg, e["sorozat"]).get("elorejelzes"))}
        valaszok = await asyncio.gather(*(_biztos(hiv, "forecast_path", {"country": c, "indicator": i, "freq": q})
                                          for c, i, q in kell))
        palyak = dict(zip(kell, valaszok))
    alapok: dict[str, dict | None] = {}
    if alapot_olvas:
        kell_s = sorted({e["sorozat"] for e in esemenyek if e["allapot"] == "tervezett"})
        olvasatok = await asyncio.gather(*(olvas(sorozat_cfg(cfg, k), hiv) for k in kell_s))
        alapok = dict(zip(kell_s, olvasatok))
    for e in esemenyek:
        s = sorozat_cfg(cfg, e["sorozat"])
        f = s.get("elorejelzes")
        if f:
            e["elorejelzes"] = elorejelzes_pont(palyak.get((f["country"], f["indicator"], f.get("freq", ""))),
                                                e["idoszak"], f.get("mertek"))
        if alapot_olvas and e["allapot"] == "tervezett":
            a = alapok.get(e["sorozat"])
            e["elozo"] = a
            if korabban_megjelent(e, a):
                e["allapot"] = "korabban_megjelent"
                e["megjegyzes"] = f"a terv idején már a {a['idoszak']}-i érték látszott"
    esemenyek.sort(key=lambda e: (e["tervezett"], e["kulcs"]))
    return {"tol": _iso(most), "ig": _iso(ig), "esemenyek": esemenyek, "figyelmeztetesek": figy}


# ══ 7. ÖSSZEVETÉS ÉS SABLONOK (LLM nélkül) ═══════════════════════════════════

def osszevetes(e: dict, ertek: float) -> dict:
    """Előrejelzés, konszenzus, előző érték; meglepetés = eltérés a várakozástól (ha nincs: a mi előrejelzésünktől),
    a sávhoz mérve. Eltérő mértéket (pl. nemzeti CPI ↔ HICP) nem vetünk össze."""
    ki: dict = {}
    f = e.get("elorejelzes")
    osszevetheto = bool(f) and (f.get("mertek") or "") == (e.get("mertek") or "")
    ki["osszevetheto"] = osszevetheto
    elozo = e.get("elozo")
    if elozo and _szam(elozo.get("ertek")) is not None:
        ki["valtozas"] = round(ertek - elozo["ertek"], 4)
    k = _szam(e.get("konszenzus"))
    ref, vs = (k, "várakozás") if k is not None else ((f["pont"], "előrejelzés") if osszevetheto else (None, None))
    if ref is not None:
        m = {"vs": vs, "elteres": round(ertek - ref, 4)}
        if osszevetheto and f.get("also") is not None and f.get("felso") is not None:
            m["hely"] = "a sáv alatt" if ertek < f["also"] else "a sáv fölött" if ertek > f["felso"] else "a sávon belül"
            fel = (f["felso"] - f["also"]) / 2
            if fel > 0:
                m["sav_arany"] = round((ertek - ref) / fel, 2)      # az eltérés a fél sáv hányszorosa
        ki["meglepetes"] = m
    return ki


def szam_hu(x: float | None, tizedes: int = 1, elojel: bool = False) -> str:
    if x is None:
        return "—"
    s = f"{x:+.{tizedes}f}" if elojel else f"{x:.{tizedes}f}"
    return s.replace(".", ",").replace("-", "−")


def szam_en(x: float | None, tizedes: int = 1, elojel: bool = False) -> str:
    if x is None:
        return "—"
    return (f"{x:+.{tizedes}f}" if elojel else f"{x:.{tizedes}f}").replace("-", "−")


def ertek_hu(s: dict, x: float | None) -> str:
    t = int(s.get("tizedes", 1))
    egys = s.get("egyseg", "%")
    return szam_hu(x, t, bool(s.get("elojel"))) + (egys if egys == "%" else f" {egys}")


def ertek_en(s: dict, x: float | None) -> str:
    t = int(s.get("tizedes", 1))
    egys = s.get("egyseg_en") or s.get("egyseg", "%")
    return szam_en(x, t, bool(s.get("elojel"))) + egys


def elteres_hu(s: dict, x: float) -> str:
    egys = s.get("egyseg", "%")
    return szam_hu(x, int(s.get("tizedes", 1)), True) + (" százalékpont" if egys == "%" else f" {egys}")


def keses_szoveg(mp: float | None) -> str:
    if mp is None:
        return ""
    jel = "+" if mp >= 0 else "−"
    a = int(round(abs(mp)))
    if a < 60:
        return f"{jel}{a} mp"
    if a < 3600:
        return f"{jel}{a // 60} p {a % 60:02d} mp"
    return f"{jel}{a // 3600} ó {a % 3600 // 60:02d} p"


def _sav_hu(s: dict, f: dict) -> str:
    t = int(s.get("tizedes", 1))
    if f.get("also") is None or f.get("felso") is None:
        return ""
    return f" (sáv {szam_hu(f['also'], t)}–{szam_hu(f['felso'], t)})"


def _cim_hu(s: dict, e: dict) -> str:
    if e.get("tipus") == "kamat":
        return f"{s['zaszlo']} {s['nev_hu']}"
    kiadas = f", {e['kiadas']}" if e.get("kiadas") else ""
    return f"{s['zaszlo']} {s['nev_hu']} ({idoszak_hu(e['idoszak'])}{kiadas})"


def telegram_szoveg(s: dict, e: dict, *, kesve: bool = False, tervezetek: list | None = None) -> str:
    """„🇭🇺 KSH infláció (okt.): 4,1% — várakozás 4,3%, a mi előrejelzésünk 4,2% (sáv 3,9–4,5) · 10:00:08-kor jelent
    meg, 10:00:31-kor láttuk (+23 mp).”"""
    x = e["ertek"]
    o = osszevetes(e, x)
    t = int(s.get("tizedes", 1))
    if e.get("tipus") == "kamat":
        fej = f"{_cim_hu(s, e)}: {ertek_hu(s, x)}"
        if "valtozas" in o:
            bp_ = round(o["valtozas"] * 100)
            fej += f" ({'változatlan' if bp_ == 0 else f'{bp_:+d} bp'.replace('-', '−')}; előző {ertek_hu(s, e['elozo']['ertek'])})"
        reszek = [fej]
    else:
        fej = f"{_cim_hu(s, e)}: {ertek_hu(s, x)}"
        hozza = []
        if _szam(e.get("konszenzus")) is not None:
            hozza.append(f"várakozás {ertek_hu(s, e['konszenzus'])}")
        f = e.get("elorejelzes")
        if f:
            mert = "" if o["osszevetheto"] else f" ({f.get('mertek')})"
            hozza.append(f"a mi előrejelzésünk{mert} {ertek_hu(s, f['pont'])}{_sav_hu(s, f)}")
        if hozza:
            fej += " — " + ", ".join(hozza)
        reszek = [fej]
        el = e.get("elozo")
        if el and _szam(el.get("ertek")) is not None:
            reszek.append(f"előző {ertek_hu(s, el['ertek'])}" + (f" ({idoszak_hu(el.get('idoszak'))})"
                                                                  if el.get("idoszak") else ""))
        m = o.get("meglepetes")
        if m:
            reszek.append(f"meglepetés: {elteres_hu(s, m['elteres'])} a "
                          f"{'várakozáshoz' if m['vs'] == 'várakozás' else 'mi előrejelzésünkhöz'} képest"
                          + (f", {m['hely'].replace('a sáv', 'a sávunk')}" if m.get("hely") else ""))
    hiv = bp(e["hivatalos_ido"])
    forrasbol = e.get("hivatalos_ido_forras") == "forrás"
    reszek.append(f"{hiv:%H:%M:%S}-kor jelent meg" if forrasbol else f"{hiv:%H:%M}-kor jelent meg (naptár)")
    reszek[-1] += f", {bp(e['eszleles']):%H:%M:%S}-kor láttuk ({keses_szoveg(e.get('keses_mp'))})."
    sorok = [" · ".join(reszek)]
    if kesve:
        sorok.append("⚠ Késve: a figyelt ablakban nem jelent meg, a pótló olvasás találta.")
    if e.get("forras_url"):
        sorok.append(str(e["forras_url"]))
    if tervezetek:
        sorok.append("X-tervezet: " + ", ".join(f"#{d['draft_id']} ({d['nyelv']})" for d in tervezetek)
                     + " — jóváhagyás a kabinban.")
    return "\n".join(sorok)


def x_szovegek(s: dict, e: dict) -> dict[str, str]:
    """A két X-tervezet szövege (link nélkül; a linket a tervezet külön sorba teszi)."""
    x = e["ertek"]
    o = osszevetes(e, x)
    t = int(s.get("tizedes", 1))
    el = e.get("elozo") or {}
    f = e.get("elorejelzes")
    forras = s.get("forras_hu") or ""
    if e.get("tipus") == "kamat":
        en = f"{s['zaszlo']} {s['nev_en']}: {ertek_en(s, x)}"
        hu = f"{s['zaszlo']} {s['nev_hu']}: {ertek_hu(s, x)}"
        if "valtozas" in o:
            bp_ = round(o["valtozas"] * 100)
            en += " (unchanged)" if bp_ == 0 else f" ({bp_:+d} bp, from {ertek_en(s, el['ertek'])})".replace("-", "−")
            hu += " (változatlan)" if bp_ == 0 else \
                f" ({bp_:+d} bp, előtte {ertek_hu(s, el['ertek'])})".replace("-", "−")
        return {"en": f"{en}. Source: {forras}", "hu": f"{hu}. Forrás: {forras}"}
    kiad_en = {"első becslés": " (first estimate)", "második becslés": " (second estimate)",
               "előzetes gyorsbecslés": " (preliminary flash)", "gyorsbecslés": " (flash)"}.get(e.get("kiadas") or "", "")
    kiad_hu = f" ({e['kiadas']})" if e.get("kiadas") else ""
    en = f"{s['zaszlo']} {s['nev_en']}, {idoszak_en(e['idoszak'])}{kiad_en}: {ertek_en(s, x)}"
    hu = f"{s['zaszlo']} {s['nev_hu']}, {idoszak_hu(e['idoszak'], True)}{kiad_hu}: {ertek_hu(s, x)}"
    if _szam(el.get("ertek")) is not None and el.get("idoszak") and el.get("idoszak") != e.get("idoszak"):
        en += f" ({idoszak_en(el['idoszak'])}: {ertek_en(s, el['ertek'])})"
        hu += f" ({idoszak_hu(el['idoszak'], True)}: {ertek_hu(s, el['ertek'])})"
    en += "."
    hu += "."
    if _szam(e.get("konszenzus")) is not None:
        en += f" Consensus: {ertek_en(s, e['konszenzus'])}."
        hu += f" Várakozás: {ertek_hu(s, e['konszenzus'])}."
    if f:
        mert = "" if o["osszevetheto"] else f" ({f.get('mertek')})"
        sav_en = (f", 80% band {szam_en(f['also'], t)}–{szam_en(f['felso'], t)}"
                  if f.get("also") is not None and f.get("felso") is not None else "")
        sav_hu = (f", 80%-os sáv {szam_hu(f['also'], t)}–{szam_hu(f['felso'], t)}"
                  if f.get("also") is not None and f.get("felso") is not None else "")
        en += f" Our forecast{mert}: {ertek_en(s, f['pont'])}{sav_en}."
        hu += f" A mi előrejelzésünk{mert}: {ertek_hu(s, f['pont'])}{sav_hu}."
    return {"en": f"{en} Source: {forras}", "hu": f"{hu} Forrás: {forras}"}


def terv_szoveg(cfg: dict, esemenyek: list[dict]) -> str:
    """A napi terv rövid Telegram-üzenete."""
    sorok = ["📅 Közlés-figyelő — a következő 24 óra:"]
    for e in esemenyek:
        s = sorozat_cfg(cfg, e["sorozat"])
        sor = f"• {bp(e['tervezett']):%m-%d %H:%M} {_cim_hu(s, e)}"
        f = e.get("elorejelzes")
        if f:
            mert = "" if (f.get("mertek") or "") == (e.get("mertek") or "") else f" ({f.get('mertek')})"
            sor += f" — előrejelzésünk{mert} {ertek_hu(s, f['pont'])}{_sav_hu(s, f)}"
        if e.get("allapot") == "nincs_olvaso":
            sor += f" — ⚠ nem olvasható: {e.get('megjegyzes')}"
        elif e.get("megjegyzes"):
            sor += f" — ⚠ {e['megjegyzes']}"
        if "becslés" in str(e.get("idopont_forras")):
            sor += " (időpont: StatData-becslés)"
        sorok.append(sor)
    return "\n".join(sorok)


def valtozatlan_szoveg(s: dict, e: dict, utana_perc: int) -> str:
    el = e.get("elozo") or {}
    return (f"{s['zaszlo']} {s['nev_hu']} ({bp(e['tervezett']):%m-%d %H:%M}): a forrás T+{utana_perc} percig nem "
            f"mutatott változást ({ertek_hu(s, el.get('ertek'))}); a döntés szövegét nem olvastuk, ezért nincs "
            f"X-tervezet.")


# ══ 8. AZ ÉSZLELÉS ═══════════════════════════════════════════════════════════

def _mar_tervezve(get_db, kulcs: str) -> bool:
    """Van-e már X-tervezet erre a közlésre (`source_message_id = kozles:<kulcs>:en`) — a meglévő action_drafts-ban.
    Postgres nélkül ez őrzi, hogy újraindulás után se szóljunk kétszer."""
    if get_db is None:
        return False
    try:
        conn = get_db()
        try:
            return conn.execute("SELECT 1 FROM action_drafts WHERE source_message_id = ? LIMIT 1",
                                (f"kozles:{kulcs}:en",)).fetchone() is not None
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — a tábla hiánya = nincs tervezet
        logger.warning("kozles_figyelo: az action_drafts nem olvasható: %s", e)
        return False


def x_tervezetek(get_db, s: dict, e: dict) -> list[dict]:
    """Két X-TERVEZET (EN, HU) a jóváhagyási úton; nem posztol. Ha a szöveg a linkkel túl hosszú, link nélkül."""
    if get_db is None:
        return []
    import twitter_poszt
    ki = []
    for nyelv, szoveg in x_szovegek(s, e).items():
        r = {}
        for link in ([e.get("forras_url")] if e.get("forras_url") else []) + [""]:
            r = twitter_poszt.tervezet(get_db, szoveg, link or "", forras=FELADO,
                                       source_message_id=f"kozles:{e['kulcs']}:{nyelv}")
            if not r.get("error"):
                break
        if r.get("draft_id"):
            ki.append({"nyelv": nyelv, "draft_id": r["draft_id"]})
        else:
            logger.warning("kozles_figyelo: az X-tervezet nem készült el (%s, %s): %s", e["kulcs"], nyelv, r.get("error"))
    return ki


async def eszlelt(e: dict, olv: dict, eszleles: datetime, *, cfg: dict, tar, telegram=None, get_db=None,
                  naplo=None, kesve: bool = False) -> bool:
    """Az új érték megjelent: rögzítés (kozles_esemeny + elso_kozles), és — közlésenként EGYSZER — Telegram + két
    X-tervezet. → True, ha most szóltunk."""
    s = sorozat_cfg(cfg, e["sorozat"])
    if (mar := tar.esemeny(e["kulcs"])) is not None and mar.get("allapot") == "eszlelve":
        logger.info("kozles_figyelo: %s már észlelve — az első észlelés marad", e["kulcs"])
        return False
    hivatalos = _utc(olv.get("hivatalos_ido")) or _utc(e["tervezett"])
    mezok = {"allapot": "eszlelve", "ertek": olv["ertek"], "eszleles": eszleles, "hivatalos_ido": hivatalos,
             "hivatalos_ido_forras": "forrás" if olv.get("hivatalos_ido") else "naptár",
             "keses_mp": round((eszleles - hivatalos).total_seconds(), 1), "forras_url": olv.get("forras_url"),
             "olvasat": {k: v for k, v in olv.items() if k != "ertek"}, "kesve": kesve}
    if e.get("tipus") != "kamat" and olv.get("idoszak"):
        mezok["idoszak_olvasott"] = olv["idoszak"]
    tar.frissit(e["kulcs"], mezok)
    e.update(_normal(mezok))
    n = _naplo(naplo)
    if n is not None:
        try:
            per = e["idoszak"] if e.get("tipus") == "kamat" else (olv.get("idoszak") or e["idoszak"])
            n.rogzit([elso_kozles.sor(naplo_sorozat(e), per, olv["ertek"], eszleles, olv.get("forras_url"))])
        except Exception as ex:  # noqa: BLE001 — a napló hibája nem viheti el a jelzést
            logger.warning("kozles_figyelo: az elso_kozles-be írás nem sikerült: %s", str(ex)[:200])
    if _mar_tervezve(get_db, e["kulcs"]) or not tar.jelzes_foglal(e["kulcs"]):
        logger.info("kozles_figyelo: %s már jelezve — nincs második jelzés", e["kulcs"])
        return False
    tervezetek = x_tervezetek(get_db, s, e)
    if tervezetek:
        tar.frissit(e["kulcs"], {"tervezetek": tervezetek})
    if telegram is not None:
        try:
            await telegram(telegram_szoveg(s, e, kesve=kesve, tervezetek=tervezetek))
        except Exception as ex:  # noqa: BLE001
            logger.warning("kozles_figyelo: a Telegram-jelzés nem ment ki: %s", ex)
    logger.info("kozles_figyelo: %s észlelve (%s, késés %s)", e["kulcs"], olv["ertek"], keses_szoveg(e.get("keses_mp")))
    return True


async def figyel(e: dict, *, cfg: dict, hiv: Hivo, tar, telegram=None, get_db=None, naplo=None,
                 most_fn: Callable[[], datetime] = _most, alvas: Callable = asyncio.sleep) -> str:
    """Az ablak: T−1 perctől T+20 percig (FRED-sornál T+180-ig) kíméletes lekérdezés, amíg az új érték meg nem jelenik.
    → a záró állapot."""
    s = sorozat_cfg(cfg, e["sorozat"])
    kezdet, vege, a = ablak_hatarok(cfg, e)
    while (d := (kezdet - most_fn()).total_seconds()) > 0:     # a tervkör indítja: addig alszik
        await alvas(min(d, 60.0))
    tar.frissit(e["kulcs"], {"allapot": "figyel"})
    alap = e.get("elozo")
    while True:
        olv = await olvas(s, hiv)
        t = most_fn()
        if e.get("tipus") == "kamat" and alap is None and olv is not None:
            alap = e["elozo"] = olv          # a terv idején nem volt alapvonal: az ablak első olvasata lesz az
            tar.frissit(e["kulcs"], {"elozo": olv})
        elif uj_kozles(e, olv, alap):
            await eszlelt(e, olv, t, cfg=cfg, tar=tar, telegram=telegram, get_db=get_db, naplo=naplo)
            return "eszlelve"
        if t >= vege:
            break
        await alvas(float(a["lekerdezes_s"]))
    if e.get("tipus") == "kamat":
        tar.frissit(e["kulcs"], {"allapot": "valtozatlan_nem_igazolt"})
        if telegram is not None and alap is not None and tar.jelzes_foglal(e["kulcs"]):
            try:
                await telegram(valtozatlan_szoveg(s, e, a["utana_perc"]))
            except Exception as ex:  # noqa: BLE001
                logger.warning("kozles_figyelo: a Telegram-jelzés nem ment ki: %s", ex)
        return "valtozatlan_nem_igazolt"
    tar.frissit(e["kulcs"], {"allapot": "nem_jelent_meg"})
    logger.info("kozles_figyelo: %s — az ablakban nem jelent meg", e["kulcs"])
    return "nem_jelent_meg"


async def potlas(*, most: datetime, cfg: dict, hiv: Hivo, tar, telegram=None, get_db=None, naplo=None) -> list[str]:
    """Az elmulasztott ablakok (rossz naptár, újraindulás): egy olvasás; ha közben megjelent, KIMONDOTT késéssel szól.
    → az így észlelt kulcsok."""
    ki = []
    for e in tar.esemenyek(most - timedelta(hours=POTLAS_ORA), most,
                           allapotok=("tervezett", "figyel", "nem_jelent_meg")):
        if e.get("tipus") == "kamat" or e["kulcs"] in _FUTO_KULCS:
            continue
        _, vege, _ = ablak_hatarok(cfg, e)
        if vege > most:
            continue
        olv = await olvas(sorozat_cfg(cfg, e["sorozat"]), hiv)
        if uj_kozles(e, olv, e.get("elozo")):
            await eszlelt(e, olv, most, cfg=cfg, tar=tar, telegram=telegram, get_db=get_db, naplo=naplo, kesve=True)
            ki.append(e["kulcs"])
        elif e["allapot"] != "nem_jelent_meg":
            tar.frissit(e["kulcs"], {"allapot": "nem_jelent_meg"})
    return ki


# ══ 9. HETI ÖSSZESÍTŐ ÉS A B-KÉPESSÉG FELÜLETE ═══════════════════════════════

def heti_osszesito(tar=None, most: datetime | None = None, napok: int = 7) -> dict:
    """A „tudjuk, hogy előbb” bizonyítéka: közlésenként a hivatalos idő, a mi észlelésünk és a késés."""
    tar = tar or tar_kapcsolat()
    most = _utc(most) or _most()
    tol = most - timedelta(days=napok)
    evs = tar.esemenyek(tol, most)
    eszlelt_ = [e for e in evs if e["allapot"] == "eszlelve"]
    kesesek = [e["keses_mp"] for e in eszlelt_ if _szam(e.get("keses_mp")) is not None]
    return {
        "tol": _iso(tol), "ig": _iso(most), "tartos_tar": getattr(tar, "tartos", False),
        "osszesen": len(evs), "eszlelt": len(eszlelt_),
        "keses_median_mp": round(statistics.median(kesesek), 1) if kesesek else None,
        "keses_max_mp": max(kesesek) if kesesek else None,
        "kesve_lattuk": sum(1 for e in eszlelt_ if e.get("kesve")),
        "kozlesek": [{k: e.get(k) for k in ("kulcs", "sorozat", "nev_hu", "idoszak", "allapot", "ertek", "tervezett",
                                            "hivatalos_ido", "hivatalos_ido_forras", "eszleles", "keses_mp",
                                            "forras_url", "elso_angol_cikk", "kesve")} for e in evs],
    }


def lezart_kozlesek(*, sorozat: str | None = None, tol=None, ig=None, tar=None) -> list[dict]:
    """B-felület: az észlelt (lezárt) közlések, tényleges értékkel, előrejelzéssel és késéssel (lásd a modul elejét)."""
    tar = tar or tar_kapcsolat()
    mezok = ("kulcs", "sorozat", "orszag", "mutato", "idoszak", "ertek", "mertek", "hivatalos_ido",
             "hivatalos_ido_forras", "eszleles", "keses_mp", "forras_url", "elorejelzes", "konszenzus", "elozo")
    return [{k: e.get(k) for k in mezok} for e in tar.esemenyek(tol, ig, allapotok=("eszlelve",), sorozat=sorozat)]


def tenyleges_ertek(orszag: str, mutato: str, idoszak: str, *, naplo=None) -> dict | None:
    """B-felület: a közlés ELSŐ értéke az `elso_kozles` naplóból (`first_print`), vagy None."""
    n = _naplo(naplo)
    if n is None:
        return None
    nev = f"kozles:{orszag}:{mutato}"
    sorok = n.sorok(nev, idoszak)
    if not sorok:
        return None
    s = min(sorok, key=lambda r: _utc(r["elso_lattuk"]))
    return {"sorozat": nev, "idoszak": idoszak, "ertek": s["ertek"], "elso_lattuk": _iso(s["elso_lattuk"]),
            "forras_url": s.get("forras_url")}


async def kozelgo_kozlesek(*, orak: int = 168, most: datetime | None = None, hiv: Hivo | None = None,
                           cfg: dict | None = None) -> list[dict]:
    """B-felület: a következő `orak` óra figyelt közlései az előrejelzésünk sávjával (mentés és alapvonal-olvasás
    nélkül)."""
    cfg = cfg or konfig()
    t = await tervez(most=most, hiv=hiv, cfg=cfg, orak=orak, alapot_olvas=False)
    mezok = ("kulcs", "sorozat", "orszag", "mutato", "nev_hu", "idoszak", "tervezett", "idopont_forras",
             "elorejelzes", "konszenzus", "mertek")
    return [{**{k: e.get(k) for k in mezok}, "olvashato": e["allapot"] != "nincs_olvaso"} for e in t["esemenyek"]]


# ══ 10. AZ ÜTEMEZŐ (a Bridge cron-szála percenként hívja) ═════════════════════

_DEPS: dict = {}
_TERV: dict[str, dict] = {}              # a folyamat ismert eseményei (kulcs → esemény)
_KESZ_SLOT: set[str] = set()
_BEJELENTVE: set[str] = set()
_FUTO_KULCS: set[str] = set()
_FUTO: set = set()


def _alaphelyzet() -> None:
    """Teszthez: a folyamat ütemező-állapota tisztán."""
    for x in (_DEPS, _TERV, _KESZ_SLOT, _BEJELENTVE, _FUTO_KULCS, _TAR, _NAPLO_PG, _KONFIG):
        x.clear()


def beallit(*, get_db: Callable | None, telegram: Callable | None, hiv: Hivo | None = None,
            naplo=None, tar=None, cfg: dict | None = None) -> None:
    """A Bridge bekötése (server.py): get_db (az action_drafts a tweet-tervezethez), telegram (a Kommandantnak)."""
    _DEPS.clear()
    _DEPS.update(get_db=get_db, telegram=telegram, hiv=hiv, naplo=naplo, tar=tar, cfg=cfg)


def _dep(nev: str):
    if nev == "tar":
        return _DEPS.get("tar") or tar_kapcsolat()
    if nev == "cfg":
        return _DEPS.get("cfg") or konfig()
    if nev == "hiv":
        return _DEPS.get("hiv") or makronom_hiv
    return _DEPS.get(nev)


def terv_idopontok() -> list[str]:
    ki = []
    for x in os.environ.get("KOZLES_FIGYELO_TERV", TERV_ORAK_ALAP).split(","):
        x = x.strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", x):
            ki.append(f"{int(x.split(':')[0]):02d}:{x.split(':')[1]}")
    return sorted(ki)


def esedekes_terv(most_bp: datetime) -> str | None:
    """A mai, már elmúlt tervkör, ha még nem futott (induláskor egyszer azonnal: „indulás”)."""
    nap = most_bp.date().isoformat()
    elmult = [f"{nap} {o}" for o in terv_idopontok() if most_bp.strftime("%H:%M") >= o]
    if not _KESZ_SLOT:
        _KESZ_SLOT.update(elmult)
        _KESZ_SLOT.add(f"{nap} indulás")
        return f"{nap} indulás"
    uj = [x for x in elmult if x not in _KESZ_SLOT]
    if not uj:
        return None
    _KESZ_SLOT.update(uj)
    return uj[-1]


async def terv_kor(most: datetime | None = None) -> dict:
    """Egy tervkör: a következő 24 óra terve (mentve), pótlás, a terv rövid Telegram-üzenete (csak az új tételek)."""
    most = _utc(most) or _most()
    cfg, tar, hiv = _dep("cfg"), _dep("tar"), _dep("hiv")
    t = await tervez(most=most, hiv=hiv, cfg=cfg)
    for e in t["esemenyek"]:
        if not tar.beszur(e):
            regi = tar.esemeny(e["kulcs"]) or {}
            potol = {k: e[k] for k in ("elozo", "elorejelzes") if e.get(k) and not regi.get(k)}
            if potol and regi.get("allapot") == "tervezett":
                tar.frissit(e["kulcs"], potol)
        _TERV[e["kulcs"]] = tar.esemeny(e["kulcs"]) or e
    for x in t["figyelmeztetesek"]:
        logger.info("kozles_figyelo terv: %s", x)
    await potlas(most=most, cfg=cfg, hiv=hiv, tar=tar, telegram=_dep("telegram"), get_db=_dep("get_db"),
                 naplo=_dep("naplo"))
    _figyelesek_inditasa(most)
    ujak = [e for e in t["esemenyek"] if e["kulcs"] not in _BEJELENTVE
            and e["allapot"] in ("tervezett", "nincs_olvaso")]
    if ujak and _dep("telegram") is not None and os.environ.get("KOZLES_FIGYELO_TERV_TELEGRAM", "1").strip() != "0":
        _BEJELENTVE.update(e["kulcs"] for e in ujak)
        try:
            await _dep("telegram")(terv_szoveg(cfg, ujak))
        except Exception as ex:  # noqa: BLE001
            logger.warning("kozles_figyelo: a terv Telegram-üzenete nem ment ki: %s", ex)
    return t


def _indit(coro) -> None:
    f = asyncio.get_running_loop().create_task(coro)
    _FUTO.add(f)
    f.add_done_callback(_FUTO.discard)


async def _figyel_biztos(e: dict) -> None:
    try:
        await figyel(e, cfg=_dep("cfg"), hiv=_dep("hiv"), tar=_dep("tar"), telegram=_dep("telegram"),
                     get_db=_dep("get_db"), naplo=_dep("naplo"))
    except Exception as ex:  # noqa: BLE001 — a cron-szál menjen tovább
        logger.error("kozles_figyelo: a figyelés elszállt (%s): %s", e.get("kulcs"), ex, exc_info=True)
    finally:
        _FUTO_KULCS.discard(e["kulcs"])
        if (friss := _dep("tar").esemeny(e["kulcs"])) is not None:
            _TERV[e["kulcs"]] = friss


async def _terv_biztos() -> None:
    try:
        await terv_kor()
    except Exception as ex:  # noqa: BLE001
        logger.error("kozles_figyelo: a tervkör elszállt: %s", ex, exc_info=True)


def _figyelesek_inditasa(most: datetime) -> int:
    """Minden ismert, még le nem zárt közlésre egy figyelő feladat (az ablak elejéig alszik). A cron-szál receptjei
    percekig is futhatnak, ezért az ablak kezdete nem függhet a percenkénti ütemtől."""
    n = 0
    cfg = _dep("cfg")
    for kulcs, e in list(_TERV.items()):
        if e.get("allapot") not in ("tervezett", "figyel") or kulcs in _FUTO_KULCS:
            continue
        if most <= ablak_hatarok(cfg, e)[1]:
            _FUTO_KULCS.add(kulcs)
            _indit(_figyel_biztos(e))
            n += 1
    return n


def cron_lepes(most_bp: datetime) -> int:
    """A cron-szál percenkénti hívása. KI állásban (vagy bekötés nélkül) semmi. → az elindított feladatok száma."""
    if not bekapcsolva() or not _DEPS:
        return 0
    n = 0
    try:
        if esedekes_terv(most_bp):
            _indit(_terv_biztos())
            n += 1
        n += _figyelesek_inditasa(_utc(most_bp))      # egy elszállt figyelő újraindul, amíg az ablak tart
    except Exception as ex:  # noqa: BLE001 — az ütemező hibája ne állítsa meg a cront
        logger.error("kozles_figyelo: ütemezési hiba: %s", ex)
    return n


# ══ 11. ÉLŐ PRÓBA (csak olvasás) ═════════════════════════════════════════════

async def _elo_proba(napok: int) -> None:
    t = await tervez(orak=24 * napok)
    cfg = konfig()
    print(f"Figyelt közlések {bp(t['tol']):%Y-%m-%d %H:%M} → {bp(t['ig']):%Y-%m-%d %H:%M} (Budapest):")
    for e in t["esemenyek"]:
        s = sorozat_cfg(cfg, e["sorozat"])
        kezdet, vege, a = ablak_hatarok(cfg, e)
        f = e.get("elorejelzes")
        el = e.get("elozo") or {}
        print(f"- {bp(e['tervezett']):%m-%d %H:%M}  {_cim_hu(s, e)}  [{e['allapot']}]"
              f"\n    ablak {bp(kezdet):%H:%M}–{bp(vege):%H:%M}, {a['lekerdezes_s']} mp-enként · időpont: "
              f"{e['idopont_forras']}"
              + (f"\n    előrejelzés: {ertek_hu(s, f['pont'])}{_sav_hu(s, f)} [{f.get('mertek')}]" if f else "")
              + (f"\n    előző: {ertek_hu(s, el.get('ertek'))} ({el.get('idoszak')}, {el.get('forras')})" if el else "")
              + (f"\n    ⚠ {e['megjegyzes']}" if e.get("megjegyzes") else ""))
    for x in t["figyelmeztetesek"]:
        print(f"! {x}")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.WARNING)
    if len(sys.argv) >= 2 and sys.argv[1] == "--elo-proba":
        asyncio.run(_elo_proba(int(sys.argv[2]) if len(sys.argv) > 2 else 7))
    else:
        print(__doc__)
