"""ELSŐ-KÖZLÉS-NAPLÓ ÉS FELOLDÁSI SZABÁLY (2026-11-23).

A HIBA, AMIT JAVÍT
------------------
A backteszt (`szim_backteszt`: `macro_ertek`, `gazdasagi_tenyleges`, az `asof_palya` alapvonala) a `macro_history`
MAI, már REVIDEÁLT értékét olvasta. Két következménye volt: (1) a backteszt látott jövőbeli revíziót; (2) a pontozás
(CRPS) nem azzal az értékkel számolt, amit a piac a közlés napján látott. A minta a ThesisInstitute/thesis
feloldási szabálya (`first_print` / `fixed_vintage` / `final`), `observation_vintages` táblája és ALFRED-olvasója
(`scripts/resolve_pending.py`, `fred_vintage_series`).

A NAPLÓ (`elso_kozles` tábla, Postgres)
---------------------------------------
Minden makró-szám bekerül, amikor a Bridge ELŐSZÖR látja — a StatData/Makronóm hívások melléktermékeként, két
szállítási ponton, EGY közös kinyerővel (`kinyer`): `kulso_mcp.hiv("makronom", …)` (MCP: a szimulátor, a backteszt
élő felvétele, a Feldwebel) és `_statdata_client._call` (REST: a `statdata_*` eszközök, a data_context). Csak
hozzáfűzés: (sorozat, időszak, érték-hash) egyedi, `ON CONFLICT DO NOTHING`; a revízió ÚJ sor a saját
`elso_lattuk`-jával, az első érték megmarad (PG-trigger tiltja az UPDATE-et és a DELETE-et).

HOL ÉL: a Bridge adatbázisa SQLite (örökség), új SQLite-tábla nem lehet (házi szabály). Élesben (Railway) ma
egyetlen Postgres sem érhető el a Bridge-ből: a tudásbázis PG-je (memoria-rag) a fejlesztői gépen fut
(127.0.0.1), az Echolot-PG másik projekt, publikus proxy nélkül. Ezért a kapcsoló alapból KI
(`ELSO_KOZLES_NAPLO`), és a DSN külön változó (`ELSO_KOZLES_PG_DSN`): helyben a tudásbázis PG-jére mutathat (a
Faber is azt használja), élesben a Bridge saját Postgresére (a tervezett PG-költözéssel). KI állásban semmi nem
fut: a hook egy környezeti változót olvas, és visszatér.

A FELOLDÁSI SZABÁLY (minden előrejelzés-sor `feloldasi_szabaly` mezője; alap: `first_print`)
--------------------------------------------------------------------------------------------
- `first_print`: az időszak ELSŐ közölt értéke. Forrás sorrendben: (1) a napló, ha a Bridge az első közlés
  várható idején belül látta (`megjelenes_hatar`; később látott sor nem első közlés); (2) amerikai sornál az ALFRED
  (a St. Louis Fed vintage-archívuma, kulcs nélkül: az első vintage, amelyben az időszak megjelent); (3) különben a
  MAI érték, KIMONDVA: „revideált érték, első közlés hiányzik” (`JELOLES_HIANY`) — sosem csendben.
- `fixed_vintage`: a megadott napon (`vintage`) ismert érték (napló, ALFRED), különben ugyanígy kimondva.
- `final`: a mai (revideált) érték.
A StatData `get_fred_data`-ja (2026-11-23) nem ad vintage-et (nincs realtime_start/vintage_dates paramétere) —
ezért olvas a Bridge közvetlenül az ALFRED-ből; a hívás a `kulso_mcp.hiv("alfred", …)` álszerveren megy, így a
backteszt felvevője (`szim_backteszt.Felvevo`) felveszi, és a visszajátszás hálózat nélkül is ugyanazt adja.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger("elso_kozles")

SZABALYOK = ("first_print", "fixed_vintage", "final")
ALAP_SZABALY = "first_print"
JELOLES_HIANY = "revideált érték, első közlés hiányzik"
JELOLES_VINTAGE_HIANY = "revideált érték, a kért vintage hiányzik"

#: a napló sora akkor első közlés, ha a Bridge legkésőbb ennyivel az időszak vége után látta (a közlési késés + tűrés)
ABLAK_NAP = {"M": 75, "Q": 150, "A": 400, "D": 120}

ALFRED_URL = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
ALFRED_TIMEOUT_S = 60.0

#: a macro_history amerikai kanonikus sorai → az ALFRED-sorozat, amely UGYANAZT méri. Ellenőrizve 2026-11-23-án a
#: macro_history mai értékein: BIS WS_LONG_CPI M.US.771 = CPIAUCNS pc1 (2026-09: 3,737685 ↔ 3,73768); OECD KEI
#: USA UNEMP = UNRATE (2026-08…10: 4,1 / 4,2 / 4,1); OECD KEI USA GDP y/y = GDPC1 pc1 (2025-Q4: 2,046041 ↔ 2,04604).
ALFRED_SOROK = {
    ("US", "cpi"): {"series_id": "CPIAUCNS", "transformation": "pc1", "freq": "M"},
    ("US", "unemployment"): {"series_id": "UNRATE", "transformation": "", "freq": "M"},
    ("US", "gdp_growth"): {"series_id": "GDPC1", "transformation": "pc1", "freq": "Q"},
}

SEMA = """
CREATE TABLE IF NOT EXISTS elso_kozles (
    id           bigserial PRIMARY KEY,
    sorozat      text NOT NULL,              -- pl. 'macro_history:HU:cpi', 'fred:UNRATE', 'fred:CPIAUCNS:pc1'
    idoszak      text NOT NULL,              -- '2026-09', '2026-Q2', FRED-nél a megfigyelés napja ('2026-09-01')
    ertek        double precision NOT NULL,
    elso_lattuk  timestamptz NOT NULL,       -- amikor a Bridge először látta; ALFRED-sornál a közlés (vintage) napja
    forras_url   text,
    payload_hash text NOT NULL,              -- sha256(sorozat|időszak|érték): ugyanaz az érték kétszer nem kerül be
    rogzitve     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (sorozat, idoszak, payload_hash)
);
CREATE INDEX IF NOT EXISTS elso_kozles_sorozat_idx ON elso_kozles (sorozat, idoszak, elso_lattuk);
CREATE OR REPLACE FUNCTION elso_kozles_csak_hozzafuzes() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'elso_kozles: csak hozzáfűzés (a revízió új sor, nem felülírás)';
END $$;
CREATE TRIGGER elso_kozles_csak_hozzafuzes BEFORE UPDATE OR DELETE ON elso_kozles
    FOR EACH ROW EXECUTE FUNCTION elso_kozles_csak_hozzafuzes();
"""

BESZURAS = """
INSERT INTO elso_kozles (sorozat, idoszak, ertek, elso_lattuk, forras_url, payload_hash)
SELECT * FROM unnest(%s::text[], %s::text[], %s::float8[], %s::timestamptz[], %s::text[], %s::text[])
ON CONFLICT (sorozat, idoszak, payload_hash) DO NOTHING
"""


# ══ 1. A SOR ═════════════════════════════════════════════════════════════════

def _most() -> datetime:
    return datetime.now(timezone.utc)


def _nap(x) -> date:
    if isinstance(x, datetime):
        return x.astimezone(timezone.utc).date()
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def _szam(x) -> float | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    return None


def ertek_kanon(x: float) -> str:
    """Az érték kanonikus alakja a hash-hez: 10 értékes jegy (a forrás kerekítési zaja ne legyen „revízió”)."""
    return format(float(x), ".10g")


def payload_hash(sorozat: str, idoszak: str, ertek: float) -> str:
    return hashlib.sha256(f"{sorozat}|{idoszak}|{ertek_kanon(ertek)}".encode()).hexdigest()


def sor(sorozat: str, idoszak: str, ertek: float, elso_lattuk: datetime, forras_url: str | None) -> dict:
    return {"sorozat": sorozat, "idoszak": str(idoszak), "ertek": float(ertek), "elso_lattuk": elso_lattuk,
            "forras_url": forras_url, "payload_hash": payload_hash(sorozat, str(idoszak), float(ertek))}


# ══ 2. A KINYERŐ — egy hely, minden StatData-válaszra ═════════════════════════════════════════════════

def _json(valasz):
    if isinstance(valasz, (dict, list)):
        return valasz
    if isinstance(valasz, (bytes, str)):
        try:
            return json.loads(valasz)
        except (TypeError, ValueError):
            return None                    # levágott vagy nem JSON válasz: nincs mit naplózni
    return None


def kinyer(eszkoz: str, parameterek: dict | None, valasz, *, most: datetime | None = None) -> list[dict]:
    """Egy StatData/Makronóm-válasz számai napló-sorként. Mellékhatás nélküli. Ismert alakok: `macro_history`
    (kanonikus sor), `get_macro_indicator` (egy friss érték, forrásonként külön sorozat), `get_fred_data`
    (FRED-megfigyelések, a units/frequency-transzformáció a sorozat nevében). A piaci árfolyam nem revideálódik —
    nem kerül be."""
    v = _json(valasz)
    if not isinstance(v, dict) or v.get("hiba") or v.get("error"):
        return []
    par = parameterek if isinstance(parameterek, dict) else {}
    most = most or _most()
    ki: list[dict] = []
    if eszkoz == "macro_history":
        orszag = str(v.get("country") or par.get("country") or "").upper()
        ind = str(v.get("indicator") or par.get("indicator") or "")
        if not orszag or not ind or not isinstance(v.get("observations"), list):
            return []
        nev = f"macro_history:{orszag}:{ind}"
        if v.get("native_frequency") and v.get("native_frequency") != v.get("frequency"):
            nev += f":{v.get('frequency')}"          # aggregált kérés: külön sorozat, nem a natív „revíziója”
        url = v.get("source_url") or f"statdata:macro_history/{orszag}/{ind} ({v.get('source_series_id') or '?'})"
        for o in v["observations"]:
            x = _szam(o.get("value")) if isinstance(o, dict) else None
            if x is not None and o.get("period"):
                ki.append(sor(nev, str(o["period"]), x, most, url))
    elif eszkoz == "get_macro_indicator":
        orszag = str(v.get("country") or par.get("country") or "").upper()
        ind = str(v.get("indicator") or par.get("indicator") or "")
        x = _szam(v.get("value"))
        per = v.get("period_returned") or v.get("period")
        if orszag and ind and x is not None and per:
            nev = f"macro_indicator:{orszag}:{ind}:{v.get('source_used') or '?'}"
            ki.append(sor(nev, str(per), x, most, v.get("source_url") or f"statdata:get_macro_indicator/{orszag}/{ind}"))
    elif eszkoz == "get_fred_data":
        sid = str((v.get("series") or {}).get("id") or par.get("series_id") or "")
        if not sid or not isinstance(v.get("data"), list):
            return []
        nev = fred_sorozat(sid, str(par.get("units") or ""), str(par.get("frequency") or ""))
        url = f"https://fred.stlouisfed.org/series/{sid}"
        for o in v["data"]:
            x = _szam(o.get("value")) if isinstance(o, dict) else None
            if x is not None and o.get("date"):
                ki.append(sor(nev, str(o["date"]), x, most, url))
    return ki


def fred_sorozat(series_id: str, transformation: str = "", frequency: str = "") -> str:
    nev = f"fred:{series_id}"
    if transformation and transformation != "lin":
        nev += f":{transformation}"
    if frequency:
        nev += f":f{frequency.lower()}"
    return nev


# ══ 3. A NAPLÓ (Postgres; tesztben memória — SQLite soha) ══════════════════════════════════════════════

class MemoriaNaplo:
    """A PgNaplo felülete memóriában (teszt, egyszeri helyi futás). Ugyanaz a szemantika: az első érték marad."""

    def __init__(self):
        self._sorok: dict[tuple, dict] = {}

    def rogzit(self, sorok: list[dict]) -> int:
        uj = 0
        for s in sorok:
            k = (s["sorozat"], s["idoszak"], s["payload_hash"])
            if k not in self._sorok:
                self._sorok[k] = dict(s)
                uj += 1
        return uj

    def sorok(self, sorozat: str, idoszak: str | None = None) -> list[dict]:
        ki = [dict(s) for (sz, i, _), s in self._sorok.items() if sz == sorozat and (idoszak is None or i == idoszak)]
        return sorted(ki, key=lambda s: (s["idoszak"], s["elso_lattuk"]))


class PgNaplo:
    """Az `elso_kozles` tábla Postgresben. A séma csak akkor fut, ha a tábla még nincs (élő táblán nincs DDL)."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._sema_kesz = False
        self._irt: set[str] = set()            # a folyamat már beírt hash-ei: ismételt válasznál nincs körút
        self._zar = threading.Lock()

    def _kapcsolat(self):
        import psycopg
        return psycopg.connect(self.dsn, connect_timeout=5, autocommit=True)

    def _sema(self, k) -> None:
        if self._sema_kesz:
            return
        if k.execute("SELECT to_regclass('elso_kozles')").fetchone()[0] is None:
            k.execute(SEMA)
        self._sema_kesz = True

    def rogzit(self, sorok: list[dict]) -> int:
        with self._zar:
            uj = [s for s in {s["payload_hash"]: s for s in sorok}.values() if s["payload_hash"] not in self._irt]
        if not uj:
            return 0
        with self._kapcsolat() as k:
            self._sema(k)
            c = k.execute(BESZURAS, [[s["sorozat"] for s in uj], [s["idoszak"] for s in uj],
                                     [s["ertek"] for s in uj], [s["elso_lattuk"] for s in uj],
                                     [s.get("forras_url") for s in uj], [s["payload_hash"] for s in uj]])
            n = c.rowcount
        with self._zar:
            self._irt.update(s["payload_hash"] for s in uj)
        return n

    def sorok(self, sorozat: str, idoszak: str | None = None) -> list[dict]:
        from psycopg.rows import dict_row
        with self._kapcsolat() as k:
            self._sema(k)
            k.row_factory = dict_row
            q = ("SELECT sorozat, idoszak, ertek, elso_lattuk, forras_url, payload_hash FROM elso_kozles "
                 "WHERE sorozat = %s" + (" AND idoszak = %s" if idoszak is not None else "") +
                 " ORDER BY idoszak, elso_lattuk, id")
            return [dict(r) for r in k.execute(q, [sorozat] + ([idoszak] if idoszak is not None else [])).fetchall()]


def bekapcsolva() -> bool:
    return (os.environ.get("ELSO_KOZLES_NAPLO", "").strip().lower() in ("1", "true", "igen", "on")
            and bool(os.environ.get("ELSO_KOZLES_PG_DSN", "").strip()))


_NAPLO: dict[str, PgNaplo] = {}
_FIGYELMEZTETVE: set[str] = set()


def naplo():
    """A folyamat napló-példánya, ha a kapcsoló BE és a DSN megvan; különben None (a pontozás ilyenkor a
    naplót nem kérdezi, és ahol nincs más forrás, kimondja a hiányt)."""
    if not bekapcsolva():
        return None
    dsn = os.environ["ELSO_KOZLES_PG_DSN"].strip()
    if dsn not in _NAPLO:
        try:
            import psycopg  # noqa: F401
        except ImportError:
            if "psycopg" not in _FIGYELMEZTETVE:
                _FIGYELMEZTETVE.add("psycopg")
                logger.warning("elso_kozles: a kapcsoló BE, de a psycopg nincs telepítve — a napló nem fut")
            return None
        _NAPLO[dsn] = PgNaplo(dsn)
    return _NAPLO[dsn]


_FUTO: set = set()


def _biztos_rogzit(n, eszkoz: str, parameterek, valasz) -> int:
    try:
        sorok = kinyer(eszkoz, parameterek, valasz)
        return n.rogzit(sorok) if sorok else 0
    except Exception as e:  # noqa: BLE001 — a napló hibája sosem viheti el a hívót
        logger.warning("elso_kozles: a rögzítés nem sikerült (%s.%s): %s", eszkoz, type(e).__name__, str(e)[:200])
        return 0


async def befejez() -> None:
    """A háttérben futó rögzítések bevárása (teszt, leállás)."""
    if _FUTO:
        await asyncio.gather(*list(_FUTO), return_exceptions=True)


def megfigyel_hatterben(eszkoz: str, parameterek, valasz) -> None:
    """A HOOK (a két szállítási pont hívja): KI állásban azonnal visszatér; BE állásban háttérszálon kinyer és ír —
    a hívó válaszideje nem nő, és a napló hibája nem jut el hozzá."""
    if not bekapcsolva() or eszkoz not in ("macro_history", "get_macro_indicator", "get_fred_data"):
        return
    n = naplo()
    if n is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _biztos_rogzit(n, eszkoz, parameterek, valasz)
        return
    t = loop.create_task(asyncio.to_thread(_biztos_rogzit, n, eszkoz, parameterek, valasz))
    _FUTO.add(t)
    t.add_done_callback(_FUTO.discard)


# ══ 4. A FELOLDÁS (tiszta függvények a napló soraira) ═══════════════════════════════════════════════════

def gyakorisag(idoszak: str) -> str:
    s = str(idoszak)
    if len(s) == 7 and s[4] == "-" and s[5] == "Q":
        return "Q"
    if len(s) == 7 and s[4] == "-":
        return "M"
    if len(s) == 4:
        return "A"
    return "D"


def idoszak_vege(idoszak: str) -> date:
    s, f = str(idoszak), gyakorisag(idoszak)
    if f == "Q":
        ev, q = int(s[:4]), int(s[-1])
        return date(ev + (q == 4), (q * 3) % 12 + 1, 1) - timedelta(days=1)
    if f == "M":
        ev, h = int(s[:4]), int(s[5:7])
        return date(ev + (h == 12), h % 12 + 1, 1) - timedelta(days=1)
    if f == "A":
        return date(int(s), 12, 31)
    return date.fromisoformat(s[:10])        # FRED: a megfigyelés napja (havi/negyedéves sornál az időszak eleje)


def megjelenes_hatar(idoszak: str) -> date:
    """Eddig a napig látott érték számít első közlésnek; ami később került a naplóba, azt a Bridge már nem az első
    közléskor látta (a figyelés később indult) — az ilyen sor nem első közlés."""
    return idoszak_vege(idoszak) + timedelta(days=ABLAK_NAP[gyakorisag(idoszak)])


def valaszt(sorok: list[dict], szabaly: str, *, idoszak: str, vintage=None) -> dict:
    """A napló egy (sorozat, időszak) sorai közül a szabály szerinti érték. {ertek, elso_lattuk, forras_url} vagy
    {ok: miért nincs}. A `final` itt nem dönt (az a mai érték, a hívó adja)."""
    if szabaly not in SZABALYOK:
        raise ValueError(f"ismeretlen feloldási szabály: {szabaly!r} (ezek vannak: {', '.join(SZABALYOK)})")
    sorok = sorted([s for s in sorok if str(s.get("idoszak")) == str(idoszak)], key=lambda s: s["elso_lattuk"])
    if not sorok:
        return {"ok": "a naplóban nincs"}
    if szabaly == "first_print":
        s = sorok[0]
        hatar = megjelenes_hatar(idoszak)
        if _nap(s["elso_lattuk"]) > hatar:
            return {"ok": f"a napló először {_nap(s['elso_lattuk'])}-én látta — az első közlés várható ideje "
                          f"({hatar}) után, ez nem első közlés"}
        return {"ertek": s["ertek"], "elso_lattuk": s["elso_lattuk"], "forras_url": s.get("forras_url")}
    if szabaly == "fixed_vintage":
        if vintage is None:
            return {"ok": "a fixed_vintage szabályhoz vintage-dátum kell"}
        jo = [s for s in sorok if _nap(s["elso_lattuk"]) <= _nap(vintage)]
        if not jo:
            return {"ok": f"a napló {vintage} előtt nem látta"}
        return {"ertek": jo[-1]["ertek"], "elso_lattuk": jo[-1]["elso_lattuk"], "forras_url": jo[-1].get("forras_url")}
    return {"ok": "final: a mai érték"}


def ismert_ertekek(sorok: list[dict], datum) -> dict[str, float]:
    """Egy sorozat minden időszakának a `datum` ELŐTT (szigorúan) ismert értéke: időszakonként az utolsó, addig
    látott napló-sor. Amit a napló a dátum előtt nem látott, az nincs benne (nem pótoljuk a maival)."""
    d = _nap(datum)
    ki: dict[str, tuple] = {}
    for s in sorok:
        if _nap(s["elso_lattuk"]) < d:
            k = str(s["idoszak"])
            if k not in ki or s["elso_lattuk"] >= ki[k][0]:
                ki[k] = (s["elso_lattuk"], float(s["ertek"]))
    return {k: v for k, (_, v) in sorted(ki.items())}


# ══ 5. ALFRED (a St. Louis Fed vintage-archívuma; kulcs nélkül) ═══════════════════════════════════════════

def macro_to_fred_datum(idoszak: str) -> str:
    s = str(idoszak)
    if gyakorisag(s) == "Q":
        return f"{s[:4]}-{(int(s[-1]) - 1) * 3 + 1:02d}-01"
    if gyakorisag(s) == "M":
        return f"{s}-01"
    return s


def fred_datum_to_macro(datum: str, freq: str) -> str:
    ev, h = int(datum[:4]), int(datum[5:7])
    return f"{ev}-Q{(h - 1) // 3 + 1}" if freq == "Q" else f"{ev}-{h:02d}"


def alfred_url(par: dict) -> str:
    from urllib.parse import urlencode
    q = {"id": par["series_id"], "vintage_date": str(par["vintage_date"])[:10]}
    if par.get("transformation"):
        q["transformation"] = par["transformation"]
    if par.get("kezdet"):
        q["cosd"] = str(par["kezdet"])[:10]
    if par.get("veg"):
        q["coed"] = str(par["veg"])[:10]
    return f"{ALFRED_URL}?{urlencode(q)}"


def alfred_csv_ertelmez(szoveg: str) -> list[dict]:
    """Az alfredgraph.csv: `observation_date,<ID>[_<TRANSZF>]_<ÉÉÉÉHHNN>`; az üres/„.” érték hiány."""
    ki = []
    olv = csv.reader(io.StringIO(szoveg))
    fej = next(olv, None)
    if not fej or len(fej) < 2 or fej[0].lower() not in ("observation_date", "date"):
        raise ValueError(f"nem ALFRED-CSV: {str(fej)[:120]}")
    for r in olv:
        if len(r) >= 2 and r[0] and r[1] not in ("", "."):
            try:
                ki.append({"date": r[0], "value": float(r[1])})
            except ValueError:
                continue
    return ki


async def alfred_lekeres(par: dict) -> str:
    """A `kulso_mcp.hiv("alfred", "vintage", par)` álszerver: egy ALFRED-vintage JSON-ként. par: series_id,
    vintage_date, [transformation], [kezdet, veg]. HTTP/1.1 (a HTTP/2-folyamot az ALFRED 2026-11-23-án
    INTERNAL_ERROR-ral zárta)."""
    import httpx
    try:
        url = alfred_url(par)
    except KeyError as e:
        return json.dumps({"hiba": f"ALFRED: hiányzó paraméter ({e})"}, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=ALFRED_TIMEOUT_S, http2=False,
                                     headers={"User-Agent": "claus-bridge/1.0"}) as c:
            r = await c.get(url)
        if r.status_code >= 400:
            return json.dumps({"hiba": f"ALFRED HTTP {r.status_code}", "url": url}, ensure_ascii=False)
        obs = alfred_csv_ertelmez(r.text)
    except Exception as e:  # noqa: BLE001 — a hiány kimondva, nem pótolva
        return json.dumps({"hiba": f"ALFRED: {type(e).__name__}: {str(e)[:200]}", "url": url}, ensure_ascii=False)
    return json.dumps({"series_id": par["series_id"], "vintage_date": str(par["vintage_date"])[:10],
                       "transformation": par.get("transformation") or "", "url": url, "observations": obs},
                      ensure_ascii=False)


def _valasz(x):
    """A hívók két alakja: a nyers hívó szöveget ad (`hiv(szerver, eszköz, par)` → str), a szimulátor Hivo-ja
    (érték, hiba) párt."""
    if isinstance(x, tuple):
        return x[0] if not x[1] else {"hiba": x[1]}
    return _json(x)


class AlfredHiba(RuntimeError):
    """Az ALFRED nem válaszolt (vagy a visszajátszásban nincs felvéve) — ez NEM azonos azzal, hogy az időszak hiányzik."""


async def alfred_vintage(hiv, spec: dict, vintage, *, kezdet: str | None = None, veg: str | None = None) -> dict:
    """{fred-dátum: érték} a `vintage` napon (az ALFRED-ben akkor közölt állapot). Ha a lekérés bukik: AlfredHiba."""
    par = {"series_id": spec["series_id"], "vintage_date": _nap(vintage).isoformat(),
           "transformation": spec.get("transformation") or ""}
    if kezdet:
        par["kezdet"] = kezdet
    if veg:
        par["veg"] = veg
    v = _valasz(await hiv("alfred", "vintage", par))
    if not isinstance(v, dict) or v.get("hiba"):
        raise AlfredHiba(str((v or {}).get("hiba") if isinstance(v, dict) else "nincs válasz")[:200])
    return {o["date"]: float(o["value"]) for o in v.get("observations") or [] if isinstance(o, dict)
            and o.get("date") and _szam(o.get("value")) is not None}


async def alfred_elso_kozles(hiv, spec: dict, fred_datum: str, *, ma) -> dict:
    """Az időszak ELSŐ közlése az ALFRED-ből: felezéses kereséssel az első vintage-nap, amelyben az időszak megvan
    (az időszak vége és `ma` között, ~7 lekérés). {ertek, kozolve, url} vagy {ok}."""
    ma = _nap(ma)
    lo = idoszak_vege(fred_datum_to_macro(fred_datum, spec["freq"]))    # az időszak vége: még nem közölhették

    async def _megvan(nap: date):
        e = await alfred_vintage(hiv, spec, nap, kezdet=fred_datum, veg=fred_datum)
        return e.get(fred_datum)
    hi_ertek = await _megvan(ma)
    if hi_ertek is None:
        return {"ok": f"az ALFRED-ben {ma}-ig nincs {spec['series_id']} {fred_datum}"}
    hi = ma
    while (hi - lo).days > 1:
        mid = lo + timedelta(days=(hi - lo).days // 2)
        x = await _megvan(mid)
        if x is None:
            lo = mid
        else:
            hi, hi_ertek = mid, x
    return {"ertek": hi_ertek, "kozolve": hi,
            "url": alfred_url({**spec, "vintage_date": hi.isoformat(), "kezdet": fred_datum, "veg": fred_datum})}


# ══ 6. A PONTOZÓ FELOLDÁSA: egy makró-sor tényleges értéke a szabály szerint ═══════════════════════════

async def feloldas(hiv, orszag: str, mutato: str, idoszak: str, *, szabaly: str | None, mai: float | None,
                   naplo=None, vintage=None, ma=None) -> dict:
    """A `macro_history` (orszag, mutato) sorának `idoszak`-i értéke a feloldási szabály szerint.

    Kimenet: {ertek, feloldas (honnan, milyen jellegű), [jeloles], [kozolve], [ok]}. Sorrend: napló → (amerikai
    sornál) ALFRED → a mai érték KIMONDOTT jelöléssel. `mai`: a macro_history mai értéke (a `final` szabály és a
    kimondott tartalék)."""
    szabaly = szabaly or ALAP_SZABALY
    if szabaly not in SZABALYOK:
        raise ValueError(f"ismeretlen feloldási szabály: {szabaly!r}")
    if szabaly == "final":
        return {"ertek": mai, "feloldas": "final: a mai (revideált) érték"}
    okok = []
    spec = ALFRED_SOROK.get((orszag, mutato))
    fd = macro_to_fred_datum(idoszak) if spec else None
    fred_nev = fred_sorozat(spec["series_id"], spec["transformation"]) if spec else None
    if naplo is not None:
        helyek = [(f"macro_history:{orszag}:{mutato}", idoszak)] + ([(fred_nev, fd)] if spec else [])
        for nev, per in helyek:
            try:
                r = valaszt(naplo.sorok(nev, per), szabaly, idoszak=per, vintage=vintage)
            except Exception as e:  # noqa: BLE001 — a napló kiesése nem pontozási hiba: tovább a következő forrásra
                r = {"ok": f"a napló nem olvasható ({type(e).__name__})"}
            if r.get("ertek") is not None:
                return {"ertek": r["ertek"], "feloldas": f"{szabaly}: napló ({nev}, először {_nap(r['elso_lattuk'])})",
                        "kozolve": _nap(r["elso_lattuk"]).isoformat()}
            okok.append(f"{nev}: {r.get('ok')}")
    else:
        okok.append("a napló KI (ELSO_KOZLES_NAPLO)")
    if spec and hiv is not None:
        try:
            if szabaly == "first_print":
                a = await alfred_elso_kozles(hiv, spec, fd, ma=ma or _most())
            elif vintage is None:
                a = {"ok": "a fixed_vintage szabályhoz vintage-dátum kell"}
            else:
                x = (await alfred_vintage(hiv, spec, vintage, kezdet=fd, veg=fd)).get(fd)
                a = ({"ertek": x, "kozolve": _nap(vintage),
                      "url": alfred_url({**spec, "vintage_date": _nap(vintage).isoformat(), "kezdet": fd, "veg": fd})}
                     if x is not None else {"ok": f"az ALFRED-ben {vintage}-én nincs"})
        except AlfredHiba as e:
            a = {"ok": f"nem érhető el ({e})"}
        if a.get("ertek") is not None:
            if naplo is not None and szabaly == "first_print":     # a közlés napja ismert: valódi első közlés
                try:
                    k = a["kozolve"]
                    naplo.rogzit([sor(fred_nev, fd, a["ertek"], datetime(k.year, k.month, k.day, tzinfo=timezone.utc),
                                      a["url"])])
                except Exception as e:  # noqa: BLE001
                    logger.warning("elso_kozles: az ALFRED-sor naplózása nem sikerült: %s", str(e)[:200])
            return {"ertek": a["ertek"], "kozolve": a["kozolve"].isoformat(),
                    "feloldas": f"{szabaly}: ALFRED {spec['series_id']}"
                                f"{(' ' + spec['transformation']) if spec['transformation'] else ''} "
                                f"(vintage {a['kozolve']})", "url": a["url"]}
        okok.append(f"ALFRED: {a.get('ok')}")
    return {"ertek": mai, "feloldas": "a mai (revideált) érték",
            "jeloles": JELOLES_HIANY if szabaly == "first_print" else JELOLES_VINTAGE_HIANY, "ok": "; ".join(okok)}


async def ismert_elotortenet(hiv, orszag: str, mutato: str, datum, *, naplo=None, min_db: int = 24) -> dict | None:
    """Az as_of-alapvonalhoz: a sor időszakainak a `datum` ELŐTT ismert értéke. Napló (ha legalább `min_db` időszakot
    látott a dátum előtt), különben amerikai sornál az ALFRED vintage-e a dátum előtti napon. {ertekek, forras} vagy
    None (nincs vintage — a hívó a mai előtörténettel dolgozik, és ezt kimondja)."""
    if naplo is not None:
        try:
            e = ismert_ertekek(naplo.sorok(f"macro_history:{orszag}:{mutato}"), datum)
        except Exception as ex:  # noqa: BLE001
            logger.warning("elso_kozles: a napló nem olvasható: %s", str(ex)[:200])
            e = {}
        if len(e) >= min_db:
            return {"ertekek": e, "forras": f"elso_kozles-napló ({datum} előtt látott értékek)"}
    spec = ALFRED_SOROK.get((orszag, mutato))
    if spec and hiv is not None:
        elotte = _nap(datum) - timedelta(days=1)
        try:
            v = await alfred_vintage(hiv, spec, elotte, kezdet=f"{elotte.year - 13}-01-01")
        except AlfredHiba as e:
            logger.info("elso_kozles: ALFRED-vintage nincs (%s %s): %s", spec["series_id"], elotte, e)
            return None
        e = {fred_datum_to_macro(d, spec["freq"]): x for d, x in v.items()}
        if len(e) >= min_db:
            return {"ertekek": dict(sorted(e.items())),
                    "forras": f"ALFRED {spec['series_id']} vintage {elotte.isoformat()}"}
    return None
