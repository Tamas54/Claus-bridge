"""A döntés-szimulátor backteszt ESEMÉNY-BANKJA (2026-11-22) — a modellfutás ÉS a GT-lekérés ELŐTT commitolva.

KIVÁLASZTÁSI SZABÁLY (a kimenetel ismerete nélkül rögzítve):
  * a három jegybank (MNB, EKB, Fed) MINDEN kamatváltoztatása 2026-07-01 és 2026-10-31 között (MNB 07-21, 08-25;
    EKB 09-10; Fed 09-16), plus az első MNB-tartás a vágási sorozat után (09-22, inflációscél-csökkentéssel) és az
    utolsó Fed-döntés az ablakban (10-28, tartás);
  * két magyar kormányzati döntés a Telex napi címeiből (2026-09-20 autópálya-koncesszió feljelentés, 2026-09-30 MNM
    Közgyűjteményi Központ megszüntetése) — a döntés napjának címe alapján, a következmények megnézése nélkül.
  * as_of ≥ 2026-06-24: az Echolot HU-rétege (és a de/uk_/us_ is) 2026-06-21 előtt üres (get_news-mérés 11-22:
    Telex 06-20: 0 cikk, 06-21: 67) — korábbi esemény sajtó-adat nélkül futna.

KÉRDÉS-SABLONOK (eseménytípusonként egységesek, a küszöbök NEM a kimenetelhez igazítva):
  p1  árfolyam-irány as_of+14 napon (utolsó záróár ≤ cél) vs az as_of előtti utolsó záróár;
      MNB: EUR/HUF le (forint erősödik), EKB: EUR/USD fel, Fed: EUR/USD le (dollár erősödik); koncesszió: OPUS.BD le
  p2  ugyanaz az árfolyam a döntés előtti záróár ±1,0%-os SÁVJÁBAN marad (intervallumos esemény)
  p3  a következő döntés(ek) után a kamat tovább mozdul ugyanabba az irányba (MNB tartásnál: vágás folytatódik);
      MNB as_of+45, EKB as_of+60 (a hatálybalépés a döntés utáni szerda), Fed as_of+45 nap; a referencia as_of+7
      (a döntés hatályba lépett); fed-2610-nél kimarad (a cél 2026-12-12 > a mai nap)
  s1  a Telex legalább N cikket közöl a témában [as_of, as_of+2] (cím+lead regex); N: MNB 3, EKB/Fed 2, kormány 3
  s2  ugyanez a Magyar Nemzetre
  s3  a Magyar Nemzet TÖBB témába vágó cikket közöl, mint a Telex ugyanabban az ablakban
  t1  (MNM) a Telex [as_of, as_of+13] tiltakozásról / nyílt levélről / petícióról / tüntetésről ír a témában

KIMONDOTT KORLÁT: a kamat-kérdések (p3) kimenetelét a bank szerzője a döntési dátumok ellenőrzésekor (Makronóm
policy_rate idősor) már látta; a sablon ezért minden jegybanki eseményre ugyanaz, válogatás nélkül. A jog-területre
nincs előre rögzíthető, strukturált kimenet-forrás a bankban → jog-kérdés 0 (hiány, kimondva).
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

ITT = Path(__file__).resolve().parent

MNB_RX = r"(alapkamat|kamatdönt|kamatvág|kamatcsökk|Monetáris Tanács|inflációs cél)"
EKB_RX = r"(EKB|Európai Központi Bank|Lagarde)"
FED_RX = r"(\bFed\b|amerikai jegybank|Federal Reserve|Powell)"

ESEMENYEK = [
    {"id": "mnb-2607", "as_of": "2026-07-21", "tipus": "jegybank_hu", "orszag": "HU",
     "lepes": "A Magyar Nemzeti Bank Monetáris Tanácsa 25 bázisponttal, 6,00-ról 5,75 százalékra csökkenti az "
              "alapkamatot a júliusi kamatdöntő ülésén.",
     "arfolyam": ("EURHUF=X", "le", "az EUR/HUF", "a forint erősödik"), "kamat": ("HU", "alacsonyabb", 45),
     "regex": MNB_RX, "n": 3},
    {"id": "mnb-2608", "as_of": "2026-08-25", "tipus": "jegybank_hu", "orszag": "HU",
     "lepes": "A Magyar Nemzeti Bank Monetáris Tanácsa 25 bázisponttal, 5,75-ről 5,50 százalékra csökkenti az "
              "alapkamatot az augusztusi kamatdöntő ülésén.",
     "arfolyam": ("EURHUF=X", "le", "az EUR/HUF", "a forint erősödik"), "kamat": ("HU", "alacsonyabb", 45),
     "regex": MNB_RX, "n": 3},
    {"id": "ekb-2609", "as_of": "2026-09-10", "tipus": "jegybank_kulfold", "orszag": "EA",
     "lepes": "Az Európai Központi Bank 25 bázisponttal, 2,25-ről 2,50 százalékra emeli a betéti kamatot, a "
              "közel-keleti konfliktus miatti inflációs kockázatokra hivatkozva.",
     "arfolyam": ("EURUSD=X", "fel", "az EUR/USD", "az euró erősödik a dollárral szemben"),
     "kamat": ("EA", "magasabb", 60), "regex": EKB_RX, "n": 2},
    {"id": "fed-2609", "as_of": "2026-09-16", "tipus": "jegybank_kulfold", "orszag": "US",
     "lepes": "Az amerikai jegybank (Fed) 25 bázisponttal emeli az irányadó kamatot: a célsáv felső széle 3,75-ről "
              "4,00 százalékra nő.",
     "arfolyam": ("EURUSD=X", "le", "az EUR/USD", "a dollár erősödik az euróval szemben"),
     "kamat": ("US", "magasabb", 45), "regex": FED_RX, "n": 2},
    {"id": "mnb-2609", "as_of": "2026-09-22", "tipus": "jegybank_hu", "orszag": "HU",
     "lepes": "A Magyar Nemzeti Bank Monetáris Tanácsa 5,50 százalékon hagyja az alapkamatot, és bejelenti: 2028. "
              "január 1-jétől az inflációs célt 3 százalékról 2,5 százalékra csökkenti (a ±1 százalékpontos "
              "toleranciasáv marad).",
     "arfolyam": ("EURHUF=X", "le", "az EUR/HUF", "a forint erősödik"), "kamat": ("HU", "alacsonyabb", 45),
     "regex": MNB_RX, "n": 3},
    {"id": "fed-2610", "as_of": "2026-10-28", "tipus": "jegybank_kulfold", "orszag": "US",
     "lepes": "Az amerikai jegybank (Fed) változatlanul hagyja az irányadó kamatot: a célsáv felső széle 4,00 "
              "százalék marad.",
     "arfolyam": ("EURUSD=X", "le", "az EUR/USD", "a dollár erősödik az euróval szemben"),
     "kamat": None, "regex": FED_RX, "n": 2},
    {"id": "konc-2609", "as_of": "2026-09-20", "tipus": "kormany_hu", "orszag": "HU",
     "lepes": "A magyar kormány — Vitézy Dávid közlekedési és beruházási miniszter bejelentése szerint — "
              "büntetőfeljelentést tesz hűtlen kezelés gyanújával a Mészáros Lőrinc és Szíjj László érdekeltségébe "
              "tartozó autópálya-koncessziós szerződés miatt, amely a miniszter szerint 2057-ig legalább 23 ezer "
              "milliárd forintba kerülne az államnak.",
     "arfolyam": ("OPUS.BD", "le", "az Opus Global (OPUS.BD) részvény", "a Mészáros-érdekeltségű Opus esik"),
     "kamat": None, "regex": r"(koncesszi|autópály)", "n": 3},
    {"id": "mnm-2609", "as_of": "2026-09-30", "tipus": "kormany_hu", "orszag": "HU",
     "lepes": "Tarr Zoltán miniszter bejelenti: megszüntetik a Magyar Nemzeti Múzeum Közgyűjteményi Központot; 2027. "
              "január 1-jével az érintett intézmények (vidéki múzeumok, gyűjtemények) visszakapják jogi és szakmai "
              "önállóságukat.",
     "arfolyam": None, "kamat": None, "regex": r"(Közgyűjteményi|Nemzeti Múzeum|MNM)", "n": 3,
     "tiltakozas": r"(tiltakoz|nyílt lev|petíci|aláírásgyűjt|tüntet|demonstr)"},
]


def _nap(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def kerdesek(e: dict) -> list[dict]:
    a = e["as_of"]
    q = []
    if e.get("arfolyam"):
        sym, irany, nev, jelentes = e["arfolyam"]
        cel = _nap(a, 14)
        q.append({"id": f"{e['id']}-p1", "terulet": "piac", "hatarido": cel,
                  "kerdes": f"{nev} záróára {cel}-én (vagy az előtte utolsó kereskedési napon) "
                            f"{'ALACSONYABB' if irany == 'le' else 'MAGASABB'} lesz, mint a döntés előtti utolsó "
                            f"záróár ({jelentes}).",
                  "szabaly": {"modszer": "arfolyam_irany", "symbol": sym, "cel_datum": cel, "irany": irany}})
        q.append({"id": f"{e['id']}-p2", "terulet": "piac", "hatarido": cel, "tipus": "intervallum",
                  "kerdes": f"{nev} záróára {cel}-én (vagy az előtte utolsó kereskedési napon) a döntés előtti utolsó "
                            f"záróár ±1,0%-os sávjában lesz.",
                  "szabaly": {"modszer": "arfolyam_sav", "symbol": sym, "cel_datum": cel, "sav_szazalek": 1.0}})
    if e.get("kamat"):
        orszag, irany, napok = e["kamat"]
        cel = _nap(a, napok)
        q.append({"id": f"{e['id']}-p3", "terulet": "piac", "hatarido": cel,
                  "kerdes": f"A jegybank irányadó kamata {cel}-én {'ALACSONYABB' if irany == 'alacsonyabb' else 'MAGASABB'}"
                            f" lesz, mint közvetlenül e döntés után (azaz a következő ülés(ek)en "
                            f"{'tovább csökkentik' if irany == 'alacsonyabb' else 'tovább emelik'}).",
                  "szabaly": {"modszer": "kamat_osszevetes", "orszag": orszag, "irany": irany,
                              "referencia_datum": _nap(a, 7), "cel_datum": cel}})
    napok3 = [_nap(a, i) for i in range(3)]
    for lap, nev, sid in (("telex", "a Telex", "s1"), ("magyarnemzet", "a Magyar Nemzet", "s2")):
        q.append({"id": f"{e['id']}-{sid}", "terulet": "sajto", "hatarido": napok3[-1],
                  "kerdes": f"{nev} a döntés napján és az azt követő két napban ({napok3[0]}–{napok3[-1]}) legalább "
                            f"{e['n']} cikket közöl a témáról (az Echolot-korpuszban, cím és lead alapján).",
                  "szabaly": {"modszer": "sajto_cikkszam", "lapok": [lap], "napok": napok3, "regex": e["regex"],
                              "legalabb": e["n"]}})
    q.append({"id": f"{e['id']}-s3", "terulet": "sajto", "hatarido": napok3[-1],
              "kerdes": f"A Magyar Nemzet TÖBB cikket közöl a témáról {napok3[0]}–{napok3[-1]} között, mint a Telex.",
              "szabaly": {"modszer": "sajto_osszevetes", "lapok": ["magyarnemzet", "telex"], "napok": napok3,
                          "regex": e["regex"]}})
    if e.get("tiltakozas"):
        napok14 = [_nap(a, i) for i in range(14)]
        q.append({"id": f"{e['id']}-t1", "terulet": "tarsadalom", "hatarido": napok14[-1],
                  "kerdes": f"{napok14[0]}–{napok14[-1]} között a Telex beszámol a döntés elleni tiltakozásról "
                            f"(nyílt levél, petíció, aláírásgyűjtés vagy tüntetés).",
                  "szabaly": {"modszer": "sajto_cikkszam", "lapok": ["telex"], "napok": napok14, "regex": e["regex"],
                              "regex2": e["tiltakozas"], "legalabb": 1}})
    return q


def main() -> None:
    bank = {"verzio": "szim-backteszt-bank-v1", "keszult": "2026-11-22", "szimulator": "dontes-szimulator-v1",
            "kivalasztas": __doc__.split("KÉRDÉS-SABLONOK")[0].split("KIVÁLASZTÁSI SZABÁLY")[1].strip(),
            "kiertekeles": ("Brier kérdésenként ((p − o)², o ∈ {0,1}); alapvonal: 50% (0,25) és leave-one-out "
                            "bázisarány; hiányzó válasz kimondva + „hiány = 0,5” sor; a nem CLEAN futás külön sorban, "
                            "a tisztaság a modell_cutoff kapuja szerint, a döntés (as_of) hónapjára, a futás MINDEN "
                            "generatív modelljére. GT: a szabaly mezők, szim_backteszt.gt_felold."),
            "modell_utak": {"claude": "Sonnet 5.5 minden szöveglépésre + Opus 5.5 kockázati térkép + Hy3 panel",
                            "hy3": "tencent/Hy3 MINDEN lépésre (terv, motorok, összegzés, kockázat, kérdések, panel)"},
            "esemenyek": []}
    for e in ESEMENYEK:
        bank["esemenyek"].append({"id": e["id"], "as_of": e["as_of"], "tipus": e["tipus"], "orszag": e["orszag"],
                                  "lepes": e["lepes"], "horizont": "3 hónap", "kerdesek": kerdesek(e)})
    (ITT / "esemenybank_LOCKED.json").write_text(json.dumps(bank, ensure_ascii=False, indent=1), encoding="utf-8")
    print(sum(len(e["kerdesek"]) for e in bank["esemenyek"]), "kérdés,", len(bank["esemenyek"]), "esemény")


if __name__ == "__main__":
    main()
