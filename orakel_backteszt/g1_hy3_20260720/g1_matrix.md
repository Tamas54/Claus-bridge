# OPERATION PYTHIA — G1 EREDMÉNY-MÁTRIX (2026-07-20)

**Modell (új):** `tencent/Hy3` (SiliconFlow, thinking:disabled, response_format nélkül) · **Flash-oszlop:** deepseek-ai/DeepSeek-V4-Flash **történeti artefaktokból, futtatás nélkül** · **Seed-szabály:** minden cella ≥ 3 seed (HU pre-election: seed 1 = a 07-20-i füst-futás + 2 új seed) · **Pacing:** G0a/B szabály (konk. 24, min(100, 300k/token) hívás/perc, exp backoff a harnessekben) · **Elicitálás:** a harnessek VÁLTOZATLAN promptjai/paraméterei (temp=0,8), egyetlen mozgó alkatrész a modell + a seed. Gépi artefakt: `g1_matrix.json`.

**Kontamináció-flag** (G0a jegyzőkönyv): target ≤2025-04 CONTAMINATED · 2025-05..07 GRAY · ≥2025-08 CLEAN; Irán-háborús target sehol nincs → TOPIC-GRAY(iran-war) nem aktiválódott.

## 1. A MÁTRIX (domén × ország × modell × seed)

| # | Domén | Ország | Flag | Metrika | **Hy3 (seedek)** | **Hy3 agg.** | **Flash (tört.)** | Verdikt |
|---|---|---|---|---|---|---|---|---|
| 1 | Választás (rés) | HU | **CLEAN/cutoff-igazolt** | Tisza−Fidesz rés vs +14,57 | +15,0 / +12,5 / **−5,0** | **+7,5** (σ=8,9), \|e\|=7,1, irány ✓ (de 1 seed fordul) | +20,9 (σ=2,0), \|e\|=6,3, irány ✓ | **hasonló \|e\|, de Hy3 INSTABIL** |
| 2a | Választás (ordinális) | CZ | CLEAN | rangsor + ANO−SPOLU rés vs +11,16 | rés: +55,7 / +37,2 / +47,4 | ANO 1. ✓ SPOLU 2. ✓ Stačilo-ki ✗; rés +46,8 (\|e\|=35,6); share-ρ 0,81 | ANO 1. ✓ SPOLU 2. ✓ Stačilo-ki ✗; rés +37,3 (\|e\|=26,1); share-ρ 0,78 | **hasonló (ordinális ✓), Hy3 rés-túllövés nagyobb** |
| 2b | Választás (multiparty-hint) | CZ | CLEAN | ugyanaz | rés: +51,2 / +48,8 / +52,5 | **teljes ordinális találat** (Stačilo-ki ✓); rés +50,8 (\|e\|=39,7); share-ρ 0,93 | teljes ordinális találat; rés +42,8 (\|e\|=31,6); share-ρ 0,96 | **hasonló, Hy3 seed-stabil (σ≈1,5)** |
| 3a | CCI headline | HU | CLEAN | szaldó vs −18,4 | −26,1 / −19,6 / −21,0 | **−22,3**, \|e\|=3,9, irány ✓ | −18,6 (offline újramérve; kanonikus −18,7), \|e\|=0,2 | **kicsit rosszabb** |
| 3b | CCI headline | CZ | **GRAY** (2025-07 farok) | szaldó vs −7,5 | +12,1 / +11,2 / +13,0 | **+12,1**, \|e\|=19,6, **irány ✗** (kategorikus közben −77!) | −2,5, \|e\|=5,0, irány ✓ | **ROSSZABB — irány-vesztés** |
| 3c | CCI headline | PT | CLEAN | szaldó vs −14,0 (ablak) / −11,5 (2026-05) | −12,4 / −11,5 / −10,2 | **−11,4**, \|e\|=2,6 ill. 0,1, irány ✓ | −28,3, \|e\|=14,3, irány ✓ | **JOBB — a Flash-túllövést kijavítja** |
| 4a | CCI nowcast-ív | HU | GRAY/CLEAN/CLEAN | 3 hónap vs (−29,7/−19,4/−2,1) | 06: −21,8/−24,8/−26,3 · 12: −9,5/−7,4/−9,7 · 05: +4,3/−1,1/−2,0 | **−24,3 → −8,9 → +0,4**; ív-ρ 1,0; MAE 6,2 | −35,4 → −15,1 → −10,6; ív-ρ 1,0; MAE 6,2 | **hasonló (emelkedő ív ✓)** |
| 4b | CCI nowcast-ív | CZ | GRAY/CLEAN/CLEAN | 3 ablak vs (−7,5/+1,1/−7,6) | js: −4,8/−15,9/+0,3 · 01: +6,9/+6,1/+5,7 · 05: −12,6/−11,7/−14,1 | **−6,8 → +6,2 → −12,8**; ív-ρ 1,0; MAE 3,7 | −7,1 → +2,8 → −14,8; ív-ρ 1,0; MAE 3,1 | **hasonló (csúcs-alak ✓; julsep seed-szórás nagy)** |
| 5 | Inflációs várakozás | HU/CZ/PT/PL | CLEAN | 4-ország Spearman/hó (ár) | s1: +0,8/+0,2/+0,2 · s2: +0,6/+0,4/+0,2 · s3: +0,6/+0,4/+0,2 | seed-átlag **+0,6/+0,4/+0,2**; CCI-oszlop +0,8/+0,8/**−0,4**; Δ-irány 3/4 (CZ ✗) | ár +0,6/+0,4/+0,4; CCI +0,8/+1,0/0,0; Δ-irány 3/4 (HU ✗) | **hasonló (ár), CCI-oszlop 2026-01-ben elfordul; PL out-of-sample CCI IRÁNY-HIBA (+16,6/+26,9/+9,0 vs −11/−13)** |
| 6 | YT cím-appeal | EN | 5 CLEAN + 2 CONT-PARTIAL csatorna | hit@3 + átlag ρ | (csatornánként, N_SEEDS=3 belső) | **hit@3 2/7, ρ̄=+0,14** (DOAC +0,83 ✓, Pharos +0,67 ✓) | hit@3 1/7, ρ̄=+0,05 | **kicsit jobb, de mindkettő gyenge** |

## 2. FŐ LELETEK

1. **A 07-20-i HU seed=1 (+15,0; 0,4 pont hiba) NEM robusztus.** 3 seeden: +15,0/+12,5/−5,0 → átlag +7,5, szórás 8,9, egy seed IRÁNYT fordít. A Hy3 „jobb kalibráció" mintáját a HU-cellán a seed-sweep MEGCÁFOLJA: a pontszerű találat szerencse volt; a Flash 2-seedes +20,9-e (σ=2,0) rosszabb középértékű, de sokkal stabilabb.
2. **CZ-választáson viszont a Hy3 seed-stabil** (multiparty σ≈1,5) és a teljes ordinális kritériumot hozza — a HU-variancia nem általános modell-tulajdonság, hanem cella-függő (a HU-korpusz kétpárti élessége + max_tokens=30-as kényszerválasztás mellett a panel billenékeny).
3. **Szláv nyelvű szabad-mondatos SSR-en a Hy3 pozitivitás-torzítású:** CZ CCI headline irány-vesztés (+12 vs −7,5, miközben a saját kategorikus válasza −77!) és PL inflexp-CCI irány-hiba (+17..+27 vs −11..−13). A hiba NEM a vélemény-, hanem a szöveg-rétegben van: a Hy3 cseh/lengyel mondatai „bizakodó" regiszterben íródnak, amit az SSR-embedding pozitívra pontoz. HU/PT-n ez nincs.
4. **PT CCI-n a Hy3 érdemben jobb a Flash-nél** (−11,4 vs −28,3; GT −14,0/−11,5), HU CCI-n kicsit rosszabb (−22,3 vs −18,6), a nowcast-ÍVEK alakját (emelkedő HU, csúcsos CZ) mindkét modell ρ=1,0-val hozza, MAE-ben a Flash minimálisan jobb (CZ 3,1 vs 3,7).
5. **Inflexp ár-rangsor:** Hy3 ≈ Flash (a 2026-01-es hónapban gyengébb: +0,2 vs +0,4); a keresztidő-delta irányt 3/4 országon hozza (a hibázó ország más: Hy3→CZ, Flash→HU).
6. **YT:** mindkét modell zaj közeli; Hy3 2/7 hit@3 (+0,14) vs Flash 1/7 (+0,05).

## 3. AJÁNLÁS A G4-SZINTÉZISNEK

- **Seed-átlagolás KÖTELEZŐ** minden Hy3-cellára (≥3 seed); pontbecslést egyetlen seedből TILOS idézni (1. lelet).
- **Szláv SSR-cellákra kalibráció kell** a Hy3 alá: vagy (a) nyelv-specifikus horgony-újrakalibráció/regiszter-korrekció, vagy (b) a kategorikus réteg használata a szabad-mondatos helyett (a CZ kategorikus irányban helyes volt), vagy (c) ezeken a cellákon Flash marad.
- A Hy3 ott ad hozzá, ahol a Flash TÚLLŐ (PT CCI, HU nowcast szintek); ordinális/alak-feladatokon (CZ rangsor, ívek) a kettő ekvivalens — a Hy3 $0 ára miatt ezekre költséghatékony helyettesítő.
- GRAY-cellák (CZ julsep, HU 2025-06) eredményei irányadóak, de a cutoff-farok miatt a G4-ben külön súllyal kezelendők.

## 4. KÖLTSÉG-LOG

| Artefakt | Logikai hívás | API-hívás (záró szelet) | Cache-hit | ~prompt tok | ~compl. tok | Idő |
|---|---|---|---|---|---|---|
| hu_pre_election/hy3_panel_g1 | 160 | 160 | 0 | 2,96M | 0,9k | 597 s |
| cz_election base + multiparty | 480 | 480 | 0 | 7,23M | 1,5k | 1459 s |
| cci_headline hu + cz + pt | 540 | 321 | 219 | 2,47M | 25,8k | 887 s |
| cci_nowcast 6 cella | 1080 | 1080 | 0 | 1,04M | 93,6k | 755 s |
| inflexp | 1440 | 145* | 1295 | 0,07M* | 13,5k | 623 s |
| yt_title | 840 | 38* | 802 | 0,01M* | 1,5k | 302 s |
| **Összesen** | **4540** | **2224 + ~2,3k korábbi szeletben** | — | **~13,8M (+~1,3M a felülírt szeletekben) ≈ 15M** | **~0,14M** | ~1,5 h |

\* A timeout-szeletelt futamok (inflexp, yt, cz-cci) korábbi szeleteinek API-hívásai a cache-ben élnek; a záró szelet költsége már cache-hitként látja őket. Valós API-összeg a kampányra: **≈4,5–4,8k hívás, ~15M prompt- + ~0,14M completion-token** (kar/3,2 becslés). Hy3-ár: **$0** (NULLTARIF). Embedding: OpenAI text-embedding-3-small, ~90 SSR-értékelés + 1 Flash-referencia újramérés (filléres tétel).

## 5. INTEGRITÁS

- GT-k és korpuszok futás ELŐTT git-tiszták (P0 `a31af1f` + G1 hiánypótló evidence-commit `7068da7`: nowcast/inflexp harnessek, inflation_ground_truth_LOCKED, pt_corpus, YT címkézett készlet — az inflation-LOCKED bitre egyezik a committed inflexp_result.json GT-blokkjaival).
- corpus_hash (sha256) minden futam-artefaktban; HU panel- és HU CCI-korpusz bitre azonos (d06594e1…), a többi hash az artefaktokban.
- Harness-fájlok és plugins/ ÉRINTETLENEK; a wrapperek (run1–run6) csak modellt/seedet/pacinget vezérelnek. Flash-hívás a G1-ben: **0 db**.
