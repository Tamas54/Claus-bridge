# OPERATION PYTHIA — G3: FALSZIFIKÁCIÓ-MEGERŐSÍTÉS JEGYZŐKÖNYV

**Dátum:** 2026-07-20 · **Modell:** `tencent/Hy3` (SiliconFlow, `thinking:{"type":"disabled"}`, response_format nélkül, temp=0.8)
**Kérdés (az EGYETLEN):** modellfüggetlen-e a narratív/strukturális scope-határ — a between-sector diszkrimináció bukása?
**Történeti referencia:** DeepSeek-V4-Flash, 9 cella between-sector Spearman átlag = **−0,378** → FALSZIFIKÁLVA (ESI szektorális mélység a módszer scope-ján kívül).
**TILOS-lista 8. pont betartva:** a futás nem "hátha bug volt" újrapróba — a protokoll bitre azonos, csak a modell + seed-szám mozgott.

---

## 1. VERDIKT

> **A scope-határ modell-invariáns: IGEN.**

A Hy3 between-sector átlag Spearman = **−0,311** (±0,605) — a Flash −0,378-ával azonos
zónában, mélyen a persistence baseline (+0,978) alatt, előjelre is negatív (anti-informatív).
Mindhárom seed negatív (−0,444 / −0,200 / −0,267); a Flash mind az 5 seedje szintén negatív volt.
A bukás **replikálódott** → **a doktrína erősödik**: a szektorális mélység a módszer scope-ján
KÍVÜL esik, modelltől függetlenül. Felülvizsgálati jegyzőkönyvre nincs szükség; a termék-scope
nem változik.

**A döntő többlet-bizonyíték — közös narratív torzítás:** a két modell cellán belüli
szektor-rangsorai egymással ρ = **+0,740** (p = 2,6·10⁻⁷) korrelálnak, miközben a valós
GT-sorrendtől mindkettő negatívan tér el. Vagyis nem két független zaj-bukásról van szó:
a két különböző architektúra UGYANAZT a (téves) szektor-képet olvassa ki ugyanabból a
kiegyensúlyozott hírkorpuszból + persona-promptból. A hiba a **narratív bemenetben és a
módszer szerkezetében** van (a sajtó nem hordoz szektor-differenciális jelet a promptban
kihúzható formában), nem a modell-implementációban.

## 2. PROTOKOLL (változatlan mag)

- Harness: `run_esi_sector_backtest.py` (pre-reg commit `d4de015`) — VÁLTOZATLANUL importálva
  (`run_cell`, promptok, korpusz-mapping, SSR-linear + text-embedding-3-small, N=40/seed, max_tokens=160, temp=0.8).
- Rács: HU/CZ/PL × {2025-09, 2025-11, 2026-01} × {industry, services, retail, construction} = 36 cella.
- Ami mozgott: modell = `tencent/Hy3` (Flash helyett), seeds = 3 (0,1,2 — a történeti 0..4 első három tagja, azonos rng-számozás).
- GT: `esi_sector_ground_truth_LOCKED.json` (Eurostat ei_bssi_m_r2, SA; zárolva 2026-06-17, futás után nem igazítva).
- Korpuszok: a 9 történeti `corpus_*.json` git-trackelt állapotban, sha256-uk a `g3_run_meta.json`-ban.
- Pacing: G0a-szabály (`g1_lib.Pacer`), 100 hívás/perc token-bucket; mért átlag 77,8 hívás/perc; konkurrencia a harness-beli 12.
- Futtató: `run_g3.py` (pre-reg `9ad6633`), 9 ország×hó szelet, szeletenkénti checkpoint + commit.

## 3. EREDMÉNYTÁBLA (between-sector Spearman cellánként)

| Cella | Flash ρ | Hy3 ρ |
|---|---|---|
| HU 2025-09 | −1,00 | −0,80 |
| HU 2025-11 | −0,20 | −0,20 |
| HU 2026-01 | −1,00 | −0,80 |
| CZ 2025-09 | +0,40 | +0,40 |
| CZ 2025-11 | +0,40 | 0,00 |
| CZ 2026-01 | −0,40 | −0,20 |
| PL 2025-09 | −0,80 | −1,00 |
| PL 2025-11 | −0,40 | −1,00 |
| PL 2026-01 | −0,40 | +0,80 |
| **átlag** | **−0,378** (±0,494) | **−0,311** (±0,605) |

| Metrika | Flash (történeti) | Hy3 (G3) |
|---|---|---|
| between-sector átlag ρ (A KULCSSZÁM) | −0,378 | **−0,311** |
| pooled ρ (cellán belül z-score, N=36) | −0,402 (p=0,015 — szignifikánsan NEGATÍV) | −0,211 (p=0,217) |
| persistence baseline (GT M−1) | +0,978 | +0,978 |
| between-country kontroll (átlag ρ) | +0,292 | +0,375 |
| seedenkénti between-sector ρ | −0,36/−0,33/−0,42/−0,33/−0,31 (5 seed) | −0,44/−0,20/−0,27 (3 seed) |
| irány-hit (2025-09→2026-01, tol. \|ΔGT\|<1,5) | 4/12 | 9/12 |

Megjegyzések:
- Az egyetlen kiugró cella a PL 2026-01 (Hy3 +0,80 vs Flash −0,40): a Hy3-panel ott gyakorlatilag
  lapos (−4,1…−0,9), a rangsor kis zajra billeg — egy cella, az összképet nem menti meg.
- A between-country kontroll mindkét modellnél pozitív (Hy3 +0,375 ≥ Flash +0,292) — a módszer
  ország-szinten továbbra is diszkriminál; a bukás specifikusan a szektorális mélység.
- A Hy3 irány-hitje jobb (9/12), de az within-sector idő-irány, nem a scope-kérdés tárgya;
  a between-sector rangsor (a tét) ettől még negatív.

## 4. SEED-STABILITÁS

Hy3 3 seed: −0,444 / −0,200 / −0,267 (szórás 0,10) — mindhárom negatív, egyik sem közelíti
sem a persistence baseline-t, sem a nullát-fölülről. A verdikt nem seed-érzékeny.

## 5. KONTAMINÁCIÓ-FLAG

A cutoff-jegyzőkönyv (`orakel_backteszt/cutoff_szonda_20260720/cutoff_jegyzokonyv.md`, 5. pont)
zárolt szabálya szerint: target-ablak ≥ 2025-08 → **CLEAN**. Mindhárom célhó (2025-09, 2025-11,
2026-01) mélyen a tiszta zónában van (Hy3 szisztematikus cutoff ~2025-04; a 2025-08 utáni sávban
31/31 esemény-szondára nulla találat). **Mind a 36 cella: CLEAN.** A G3-parancs elővigyázatos
GRAY/CONTAMINATED-vélelme a zárolt flag-szabály szerint nem áll fenn — becsületesen rögzítve:
a szektorális GT (Eurostat, 2025-09…2026-01) teljes egészében a modell cutoffja UTÁNI kiadás.

## 6. DOKTRÍNA-KÖVETKEZMÉNY

1. Az ESI szektorális (between-sector) mélység **végleg a módszer scope-ján kívül** — a bukás
   két független architektúrán (Flash MoE + Hy3), eltérő seed-készleten replikálódott, miközben
   a predikció-rangok modellek közt +0,74-gyel egyeznek: közös, narratív eredetű plafon.
2. A konszilencia-törvény erősödik: ami a korpuszban nincs benne (szektor-differenciális jel),
   azt semmilyen modell nem húzza ki belőle — a scope-határ a BEMENET tulajdonsága.
3. A termék-scope NEM bővül és NEM szűkül e futás alapján; bármilyen scope-módosítás kizárólag
   Kommandant-szóra történhet. Headline-szintű (CCI, ország-rang) használat változatlanul érvényes
   (between-country kontroll itt is pozitív).

## 7. KÖLTSÉG + ARTEFAKTOK

- 4320 Hy3-hívás (0 cache-hit), ~2,66M prompt + ~0,42M completion token (becslés, 3,2 char/token),
  9 szelet × ~5,5 perc = 55,5 perc össz-futás, mért 77,8 hívás/perc.
- LLM-költség: **$0** (Hy3 NULLTARIF-lap). Embedding (text-embedding-3-small, SSR): ~0,45M token ≈ **$0,01**.
- Fájlok: `run_g3.py` (futtató), `g3_hy3_result.json` (36 cella, seedenkénti balance-ok),
  `g3_run_meta.json` (szeletenkénti korpusz-sha256 + pacer-statisztika), `g3_analyze.py` (elemző),
  `g3_falszifikacio.json` (gépi eredmény), e jegyzőkönyv.
- Pre-reg lánc: `d4de015` (harness+GT git-be), `9ad6633` (futtató), majd 9 szelet-commit.
- Korlátok betartva: plugins/ változatlan, G1/G2-artefaktok érintetlenek, bridge.db érintetlen, deploy/push nincs.
