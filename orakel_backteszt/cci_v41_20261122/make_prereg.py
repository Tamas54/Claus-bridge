#!/usr/bin/env python3
"""CCI-backteszt V4.1-Flash — ELŐREGISZTRÁCIÓ (a futtatások ELŐTT commitolva).

Kimenet: gt_LOCKED_cci.json (a ma már megjelent GT-értékek) + PREREG.json (cellák, próba-kérdések, tűrés,
kiértékelési szabályok, korpusz-sha256). Hálózatot nem használ: a GT-értékek a Makronóm MCP-ből (2026-11-23)
lekért számok, itt szó szerint rögzítve.
"""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# ── GT (Makronóm MCP, 2026-11-23) ─────────────────────────────────────────────────────────────────────────────
# EU: Eurostat ei_bsco_m, indic=BS-CSMCI (Consumer confidence indicator), s_adj=SA, unit=BAL — a DG ECFIN
#     harmonizált fogyasztói felmérése (Magyarországon a terepmunkát a GKI végzi a Bizottság megbízásából).
# US: FRED UMCSENT (University of Michigan: Consumer Sentiment, végleges havi érték, 1966:Q1=100, NSA).
GT = {
    "HU": {"2024-11": -28.3, "2024-12": -26.5, "2025-05": -28.2, "2025-06": -29.8,
           "2026-05": -2.1, "2026-06": -1.1, "2026-07": 1.2, "2026-08": -3.2, "2026-09": -3.4, "2026-10": -4.9},
    "FR": {"2024-11": -15.2, "2024-12": -17.7, "2025-05": -17.5, "2025-06": -17.4,
           "2026-05": -21.1, "2026-06": -19.3, "2026-07": -17.0, "2026-08": -17.8, "2026-09": -18.5, "2026-10": -21.9},
    "IT": {"2024-11": -14.1, "2024-12": -15.8, "2025-05": -16.0, "2025-06": -17.1,
           "2026-05": -21.2, "2026-06": -22.1, "2026-07": -20.2, "2026-08": -20.2, "2026-09": -23.2, "2026-10": -23.3},
    "DE": {"2024-11": -10.7, "2024-12": -10.2, "2025-05": -9.1, "2025-06": -9.8,
           "2026-05": -15.9, "2026-06": -14.4, "2026-07": -13.6, "2026-08": -12.5, "2026-09": -14.3, "2026-10": -14.5},
    "US": {"2024-11": 71.8, "2024-12": 74.0, "2025-05": 52.2, "2025-06": 60.7,
           "2026-05": 44.8, "2026-06": 49.5, "2026-07": 55.2, "2026-08": 51.7, "2026-09": 48.1, "2026-10": 48.0},
}
BACKTEST = ["2026-07", "2026-08", "2026-09", "2026-10"]
CONTROL = ["2024-12", "2025-06"]
PROBE_MONTHS = CONTROL + ["2026-06"] + BACKTEST + ["2026-11"]
FLAT = 0.5          # |ΔGT| < 0.5 → „lényegében változatlan"
TOL = {"EU": 1.0, "US": 1.5}

HU_IN = {"06": "júniusában", "07": "júliusában", "08": "augusztusában", "09": "szeptemberében",
         "10": "októberében", "11": "novemberében", "12": "decemberében"}
HU_TO = {"05": "májushoz", "06": "júniushoz", "07": "júliushoz", "08": "augusztushoz", "09": "szeptemberhez",
         "10": "októberhez", "11": "novemberhez"}
EN_M = {"05": "May", "06": "June", "07": "July", "08": "August", "09": "September", "10": "October",
        "11": "November", "12": "December"}
HU_ORSZ = {"HU": "a magyar", "FR": "a francia", "IT": "az olasz", "DE": "a német"}
EN_ORSZ = {"HU": "Hungary", "FR": "France", "IT": "Italy", "DE": "Germany"}


def prev_month(m):
    y, mm = map(int, m.split("-"))
    return f"{y - 1}-12" if mm == 1 else f"{y}-{mm - 1:02d}"


def questions():
    qs = []
    for c in ["HU", "FR", "IT", "DE", "US"]:
        for m in PROBE_MONTHS:
            y, mm = m.split("-")
            pm = prev_month(m)
            _, pmm = pm.split("-")
            if c == "US":
                hu = (f"Mennyi volt az amerikai Michigan-i Egyetem fogyasztói hangulatindexe (University of Michigan "
                      f"consumer sentiment) {y} {HU_IN[mm]}, és javult vagy romlott {HU_TO[pmm]} képest?")
                en = (f"What was the University of Michigan consumer sentiment index for {EN_M[mm]} {y}, and did it "
                      f"rise or fall compared with {EN_M[pmm]} {pm[:4]}?")
            else:
                gki = ("a GKI méri az Európai Bizottság megbízásából" if c == "HU"
                       else "az Európai Bizottság fogyasztói bizalmi mutatója")
                gki_en = " (the survey is run by GKI)" if c == "HU" else ""
                hu = (f"Mennyi volt {HU_ORSZ[c]} fogyasztói bizalmi index ({gki}) {y} {HU_IN[mm]}, és javult "
                      f"vagy romlott {HU_TO[pmm]} képest?")
                en = (f"What was the European Commission (DG ECFIN) consumer confidence indicator for "
                      f"{EN_ORSZ[c]}{gki_en} in {EN_M[mm]} {y}, and did it improve or worsen compared with "
                      f"{EN_M[pmm]} {pm[:4]}?")
            kind = "kontroll" if m in CONTROL else ("elorejelzes" if m == "2026-11" else "cella")
            for nyelv, q in (("hu", hu), ("en", en)):
                qs.append({"id": f"{c}_{m}_{nyelv}", "orszag": c, "honap": m, "nyelv": nyelv, "tipus": kind,
                           "kerdes": q})
    return qs


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def main():
    gt_dir = {}
    for c, s in GT.items():
        for m in list(s):
            pm = prev_month(m)
            if pm in s:
                d = round(s[m] - s[pm], 2)
                gt_dir[f"{c}_{m}"] = {"delta": d, "irany": 0 if abs(d) < FLAT else (1 if d > 0 else -1)}
    gt = {
        "op": "CCI-backteszt V4.1-Flash — GROUND TRUTH (a futtatások ELŐTT zárolva)",
        "date_locked": "2026-11-23",
        "source": {"EU": "Eurostat ei_bsco_m BS-CSMCI SA BAL (DG ECFIN harmonizált felmérés; HU: GKI-terepmunka), "
                         "Makronóm MCP get_eurostat_data, 2026-11-23",
                   "US": "FRED UMCSENT (University of Michigan Consumer Sentiment, végleges havi érték), "
                         "Makronóm MCP get_fred_data, 2026-11-23 (FRED last_updated 2026-11-20)"},
        "values": GT, "direction": gt_dir, "flat_threshold": FLAT,
        "megjegyzes": ("A júliusi zárolás óta kis revíziók vannak (pl. IT 2026-05 −21,3 → −21,2): mindig a mai "
                       "közölt érték számít. 2026-11: az EU-országok novemberi értéke 2026-11-23-án még nem jelent "
                       "meg (DG ECFIN, ~11-27); a Michigan novemberi értéke a FRED-en még nincs fent → a 2026-11 "
                       "cellák csak előrejelzések (opcionálisak, lásd PREREG)."),
    }
    json.dump(gt, open(os.path.join(HERE, "gt_LOCKED_cci.json"), "w", encoding="utf-8"), ensure_ascii=False,
              indent=1)

    prereg = {
        "op": "CCI-backteszt V4.1-Flash — ELŐREGISZTRÁCIÓ",
        "date": "2026-11-23",
        "cel": ("Jól olvassa-e a DELPHOI szintetikus panel (deepseek-ai/DeepSeek-V4.1-Flash) a gazdasági hangulatot, "
                "a modell tudás-határa UTÁNI hónapokon. Mérce: a havi fogyasztói bizalmi index."),
        "cellak": {"orszagok": ["HU", "FR", "IT", "DE", "US"], "backteszt_honapok": BACKTEST,
                   "elorejelzes_honap": "2026-11 (csak HU/FR/IT/DE; OPCIONÁLIS: csak ha a backteszt után a költés "
                                        "< 0,40 $; a GT ~2026-11-27-től)",
                   "irany_teszt_honapok": BACKTEST[1:],
                   "megjegyzes": ("Az irányhoz két egymást követő panel kell, ezért 2026-07 csak bázis (szint + az "
                                  "eltolás becslése). UK: kimarad (a GfK-index nincs a mérőeszközeinken).")},
        "gt_file": "gt_LOCKED_cci.json",
        "proba": {
            "leiras": ("Célzott cutoff-próba: minden ország × hónap cellára közvetlen kérdés az indexértékre és a havi "
                       "irányra, magyarul és angolul, temp=0, 1 hívás/változat, Non-Think, max 220 token. "
                       "Lágy B-prompt (a cutoff_szonda_20261122 mintája)."),
            "system": {"hu": ("Segítőkész asszisztens vagy. Válaszolj tömören, legfeljebb 3 mondatban a betanított "
                              "tudásod alapján. Ha valóban nem ismered a választ, írd: NEM TUDOM."),
                       "en": ("You are a helpful assistant. Answer concisely, in at most 3 sentences, from your "
                              "trained knowledge. If you genuinely do not know the answer, write: I DON'T KNOW.")},
            "kerdesek": questions(),
            "turesek": {"EU_balance_pont": TOL["EU"], "US_michigan_indexpont": TOL["US"]},
            "pontozas": [
                "1. NEM TUDOM / I DON'T KNOW-val kezdődő vagy szám nélküli válasz: nem tudja.",
                "2. Érték: az első szám, amely nem évszám (1900–2100) és a hihető tartományban van (EU: −60…+30; "
                "US: 30…120); a magyar tizedesvessző és a mínusz-jel (−, mínusz) kezelve.",
                "3. Irány: kulcsszavak — javult/emelkedett/nőtt/improved/rose/increased/up = +1; romlott/csökkent/"
                "esett/visszaesett/worsened/fell/declined/decreased/dropped = −1; változatlan/stagnált/unchanged/"
                "flat/stable = 0; ha + és − is szerepel: nincs irány.",
                "4. TUDTA (a cella KIESIK): bármelyik nyelvi változatban az érték a tűrésen belül van ÉS az irány "
                "egyezik a GT irányával (|ΔGT| < 0,5 → csak a 0 egyezik).",
                "5. GYANÚS (jelölve, nem esik ki): csak az érték van a tűrésen belül, vagy irány nélkül.",
                "6. Az irány-egyezés egyedül (szám nélkül) nem bizonyíték (50% esély), csak feljegyezzük.",
                "7. Kontroll (2024-12, 2025-06): ugyanez a pontozás; ha a kontrollon sincs találat, a próba csak azt "
                "mutatja, hogy a modell az ilyen indexeket általában sem idézi fel — ezt kimondjuk.",
            ],
        },
        "korpusz": {"forras": "Echolot MCP get_news, as_of = published_at napja; build_corpora.py",
                    "zarolas": "korpusz_LOCKED.json (sha256 fájlonként) — külön commit, a panel-futás ELŐTT",
                    "ablak": "EU: [M−1 hó 15., M hó 14.]; US: [M−1 hó 8., M hó 7.]",
                    "szivargas_szuro": "LEAK_RE (build_corpora.py): minden fogyasztói-bizalmi/hangulati index-hír kiesik"},
        "panel": {
            "recept": ("A júliusi G2 1:1 klónja (run_g2.py FR/IT, run_west.py EN/DE szövegei szó szerint, HU a sablon "
                       "magyar változata): persona-only, korpusz-grounding 16 véletlen címmel personánként, "
                       "MONDAT+KATEGÓRIA, temp 0,8, max 160 token, thinking ki, SSR linear, háztartási anyagi "
                       "kilátás-horgony (ssr.REFERENCE_SETS_*), persona_sampler.sample_country_personas."),
            "modell": "deepseek-ai/DeepSeek-V4.1-Flash",
            "embedding": "Qwen/Qwen3-Embedding-8B (SF, delphoi.EMBED_MODEL_SF) — júliusban text-embedding-3-small "
                         "(OpenAI); a Bridge OpenAI-kulcsa ma 401 → a júliusi számok NEM 1:1 összevethetők",
            "seedek": [1, 2, 3],
            "N_per_seed": 20,
            "N_elteres": ("A júliusi recept N=60/seed. Mért ár (füsthívás, HU): 1891 prompt- + 125 kimeneti token "
                          "hívásonként ≈ 0,036 ¢ → N=60×3 cellánként ≈ 6,5 ¢, 20 cellára ≈ 1,3 $, ami a 0,50 $-os "
                          "keret 2,6-szorosa. Ezért N=20/seed (60 válasz/cella); minden más 1:1. A zaj nagyobb: a "
                          "seed-szórást és a becsült standard hibát cellánként közöljük."),
            "panel_ertek": "P = a 3 seed SSR-szaldójának átlaga, (score−3)/2×100; másodlagos: kategorikus szaldó",
            "leallas": "SF 402 → azonnali megállás és jelentés",
        },
        "kiertekeles": {
            "a_irany": ("ΔP_M = P_M − P_{M−1}, ΔG_M = G_M − G_{M−1}, M ∈ {08,09,10}. Találat: sign(ΔP) = sign(ΔG). "
                        "|ΔG| < 0,5 cellák (lényegében változatlan) kimaradnak a találati arányból, külön közöljük. "
                        "Elsődleges: SSR-szaldó; másodlagos: kategorikus szaldó. Szignifikancia: egyoldali binomiális "
                        "próba 50% ellen. Zaj-jelzés: |ΔP| > 2×SE(ΔP)."),
            "b_szint": ("EU (HU/FR/IT/DE): hiba e = P − G; nyers MAE (16 cella). Eltolás-korrekció: M hónapra "
                        "offset_M = átlag(G_k − P_k) az ország KORÁBBI backteszt-hónapjaira (k < M; 07-nek nincs), "
                        "korrigált hiba |P_M + offset_M − G_M| a 08–10 cellákon (12 cella). Javítható, ha a "
                        "korrigált MAE < nyers MAE ugyanezen cellákon; összevetve a 'nincs változás' MAE-jével. "
                        "US: a Michigan-index más skála (indexpont, nem szaldó) → ugyanez csak tájékoztató."),
            "c_sorrend": ("c1: Spearman ρ a 16 EU-cella szintjén (P vs G); c2: havonta az EU-országok szint-sorrendje "
                          "(4 ország), a 4 hónap ρ-átlaga; c3: Spearman ρ a változásokon (ΔP vs ΔG), 12 EU-cella "
                          "(másodlagos: US-sel 15)."),
            "alapvonalak": {"nincs_valtozas": "ΔĜ = 0, Ĝ_M = G_{M−1}: irány-találat a nem-lapos cellákon 0 "
                                              "(definíció szerint), szint-MAE = átlag |ΔG|",
                            "irany_folytatodik": "sign(ΔĜ_M) = sign(G_{M−1} − G_{M−2}); ha az előző |Δ| < 0,5: "
                                                 "'változatlan' jóslat (nem-lapos cellán hiba)"},
            "kizaras": "A próbán TUDTA-cellák kiesnek mindhárom mérésből; ha egy 07-es bázis esik ki, a 08-as irány is.",
        },
    }
    json.dump(prereg, open(os.path.join(HERE, "PREREG.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"PREREG.json: {len(prereg['proba']['kerdesek'])} próba-kérdés")


if __name__ == "__main__":
    main()
