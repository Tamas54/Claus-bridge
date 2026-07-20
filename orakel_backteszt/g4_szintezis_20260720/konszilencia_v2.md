# OPERATION PYTHIA — G4: KONSZILENCIA-RIPORT v2 (2026-07-20)

**Jelleg:** elemzés + szintézis a kész G0a/G0c/G1/G2/G3 artefaktokból — **LLM-hívás: 0 db** (sem Hy3, sem Flash).
**Bemenetek:** `g1_hy3_20260720/g1_matrix.{md,json}` · `g2_hy3_20260720/g2_fr_it.{md,json}` · `g3_hy3_20260720/g3_falszifikacio.{md,json}` · `cutoff_szonda_20260720/cutoff_jegyzokonyv.md` · `orszag_matrix_20260720/matrix.md` · `hy3_20260720/agora_ab_results.json`.
**Gépi artefakt:** `konszilencia_v2.json` (ugyanez a tábla géppel fogyasztható formában). **Kalibrációs registry:** `data/delphoi_calibration.json`.

---

## 1. AZ EGYETLEN TÁBLA (domén × ország)

Oszlop-szemantika: **Irány** = előjel/tendencia-találat · **Szint** = pontszám-közelség (|e|, MAE) · **Diszkr.** = rangsor/ablak-megkülönböztetés (Spearman, hit@3) · **Flag** = kontamináció (G0a zárolt szabály: target ≤2025-04 CONT · 2025-05..07 GRAY · ≥2025-08 CLEAN) · **Seed** = seed-stabilitás · **Verdikt** = Hy3-vs-Flash összevetés.

| # | Domén | Ország | Irány | Szint | Diszkr. | Flag | Seed | **Hy3-vs-Flash verdikt** |
|---|---|---|---|---|---|---|---|---|
| 1 | Választás (rés) | HU | ✓ seed-átlagban (de 1/3 seed fordít) | RÉSZBEN — \|e\|≈6–7 mindkét modellnél (Hy3 +7,5 / Flash +20,9 vs GT +14,6) | n/a (egy rés) | **CLEAN — cutoff-szondával direkt igazolva** | **Hy3 INSTABIL** (σ=8,9) vs Flash stabil (σ=2,0) | Hasonló \|e\|; a 07-20-i 0,4 pontos seed=1 SZERENCSE volt, nem idézhető |
| 2a | Választás (ordinális) | CZ | ✓ (ANO 1., SPOLU 2. mindkét modell) | ✗ — rés-túllövés 26–36 pont | ✓ share-ρ 0,78–0,81 | CLEAN | Hy3 σ≈9 (base) | Ekvivalens ordinálisan; rés-szintet EGYIK sem hozza |
| 2b | Választás (multiparty-hint) | CZ | ✓ TELJES rangsor (Stačilo-ki is) | ✗ — rés-túllövés 32–40 pont | ✓ share-ρ 0,93–0,96 | CLEAN | **Hy3 seed-stabil** (σ≈1,5) | Ekvivalens — a HU-variancia nem általános Hy3-tulajdonság |
| 3a | CCI headline | HU | ✓ | ✓ Hy3 \|e\|=3,9 / Flash 0,2 | — | CLEAN | jó (σ=3,4) | Flash kicsit jobb |
| 3b | CCI headline | CZ | **Hy3 ✗ / Flash ✓** | ✗ Hy3 \|e\|=19,6 (Flash 5,0) | — | **GRAY** (2025-07 farok) | seed-stabil, de rossz irányban | **ROSSZABB — szláv szabad-mondat regiszter-hiba** (Hy3 +12 vs GT −7,5, miközben SAJÁT kategorikus válasza −77) |
| 3c | CCI headline | PT | ✓ | **✓ Hy3 \|e\|=0,1–2,6** (Flash 14,3) | — | CLEAN | jó (σ=1,1) | **Hy3 JOBB — a Flash-túllövést kijavítja** |
| 4a | CCI nowcast-ív | HU | ✓ ív-ρ=1,0 (emelkedő) | RÉSZBEN — MAE 6,2 mindkettő | ✓ időbeli | GRAY/CLEAN/CLEAN | jó | Ekvivalens |
| 4b | CCI nowcast-ív | CZ | ✓ ív-ρ=1,0 (csúcs-alak) | RÉSZBEN — MAE 3,7 vs 3,1 | ✓ időbeli | GRAY/CLEAN/CLEAN | julsep-cellán nagy szórás | Ekvivalens (Flash MAE minimálisan jobb) |
| 5a | Inflexp ár-rangsor | HU/CZ/PT/PL | ✓ Δ-irány 3/4 (a hibázó MÁS: Hy3→CZ, Flash→HU) | skála-offset (a 3-as anchor-középpont NEM a GT-nulla) | RÉSZBEN — Spearman +0,6→+0,2 hónapról hónapra romlik | CLEAN | seed-stabil | Ekvivalens |
| 5b | Inflexp CCI-oszlop (out-of-sample) | PL | **✗ Hy3** (+16,6..+26,9 vs GT −11..−13) | ✗ | ✗ | CLEAN | — | **Hy3 IRÁNY-HIBA — ugyanaz a szláv regiszter-minta, mint 3b** |
| 6 | YT cím-appeal | EN | zaj-közeli | — | ✗ hit@3 2/7 (Hy3) vs 1/7 (Flash), ρ̄ +0,14 vs +0,05 | 5 CLEAN + 2 CONT-PARTIAL | — | Mindkettő gyenge — a domén NEM igazolható |
| 7 | ESI szektor (between-sector) | HU/CZ/PL | — | — | **✗ FALSZIFIKÁLT: Hy3 ρ̄=−0,311 vs Flash −0,378; modellek közti pred.-korreláció +0,740 (p=2,6·10⁻⁷)** | CLEAN (36/36) | verdikt nem seed-érzékeny (−0,44/−0,20/−0,27) | **MODELL-INVARIÁNS bukás — a korlát a BEMENET tulajdonsága, scope-on kívül** |
| 8a | CCI (új ország) | FR | ✓ 3/3 (SSR + kategorikus egybehangzó) | ✗ túllövés 26–32 pont — **korpusz-kikötés** (pre-QUELLENSCHLEUSE ritka zóna, Le Point-right túlsúly) | ✗ ablak-Spearman −0,5 | CLEAN (12/12) | seed-stabil (σ 1,2–3,1) | Csak Hy3 mérve — **RÉSZBEN MEGERŐSÍTVE** |
| 8b | CCI (új ország) | IT | ✓ 3/3 | ✗ túllövés 12–27 pont — a korpusz-összetétellel skálázik (katasztrófa-hírű ablak −50, gazdagabb −34) | RÉSZBEN — ablak-Spearman +0,5 (a G2 legjobbja) | CLEAN | seed-stabil (σ 1,1–3,6) | Csak Hy3 — **RÉSZBEN MEGERŐSÍTVE** |
| 8c | Ár (új ország) | FR/IT | ✓ kereszt-ország rangsor 3/3 (FR>IT) | skála-offset (pre-reg sign-szabály 6/6 miss — szisztematikus, G1-referenciával egyező) | ✗ ablakon belül (FR −1,0 / IT −0,5) | CLEAN | nagyon stabil (σ 0,02–0,08) | Csak Hy3 — a RANG-réteg igazolt, a szint nem |

**Újlatin ellenpróba (G2 §4):** a szláv pozitivitás-torzítás FR/IT-n NEM jelent meg, sőt fordított (pesszimizmus-túllövés, a kategorikus réteg 25–40 ponttal mélyebb negatív) — a 3b/5b hiba nyelvspecifikus, nem általános Hy3-tulajdonság.

## 2. MI IGAZOLHATÓ, MI NEM — A NEM-EK UGYANOLYAN HANGOSAN

**IGAZOLHATÓ (modellfüggetlenül vagy Hy3-on megerősítve):**
- **Irány- és sorrend-réteg:** CCI-előjel (HU/PT/FR/IT), nowcast-ív alakja (ív-ρ=1,0 mindkét modellen), választási ordinális rangsor (CZ teljes, HU győztes), inflexp ár-KERESZT-rangsor (4-ország + FR>IT 3/3), between-country ESI-kontroll (Hy3 +0,375, Flash +0,292 — pozitív!).
- **HU-kontamináció LEZÁRVA:** a cutoff-szonda (25 válasz, 3 elicitálási mód) szerint a Hy3 NEM ismeri a Tisza-győzelmet, a választást jövőbeliként kezeli → a HU pre-election cella érvényes vak becslés.
- **PT-minta:** kurált, kiegyensúlyozott egysoros korpusszal a szint is hozható (|e|=0,1–2,6) — ez a szint-kalibráció bizonyított útja.

**NEM IGAZOLHATÓ — és miért:**
1. **CZ-CCI szláv regiszter (3b):** a Hy3 cseh szabad-mondatai „bizakodó" regiszterben íródnak, az SSR-embedding pozitívra pontozza → irány-vesztés, miközben a kategorikus réteg irány-helyes. A hiba a SZÖVEG-rétegben van, nem a véleményben → kalibráció helyett kategorikus elicitálás kell (registry: `use_categorical_layer`).
2. **PL out-of-sample (5b):** ugyanez a minta lengyelül — a PL CCI-oszlop irány-hibás; PL-cellát szabad-mondatos SSR-rel Hy3 alatt TILOS élesíteni.
3. **Szektorális scope-határ (7):** a between-sector diszkrimináció bukása REPLIKÁLÓDOTT (Hy3 −0,311 ≈ Flash −0,378, mindhárom seed negatív), és a két architektúra +0,74-gyel UGYANAZT a téves szektor-képet olvassa ki → a sajtó nem hordoz kihúzható szektor-differenciális jelet. Végleg scope-on kívül; módosítás csak Kommandant-szóra.
4. **HU seed-variancia (1):** a 0,4 pontos seed=1 eredmény NEM robusztus — 3 seeden +15,0/+12,5/−5,0, átlag +7,5 (GT +14,6), egy seed irányt fordít. Pontbecslés egyetlen seedből TILOS; seed-átlag (≥3) kötelező.
5. **FR/IT korpusz-kikötés (8a/8b):** a szint-túllövés a pre-QUELLENSCHLEUSE (07-15 előtti) ritka/torz történeti korpusz terméke — a G0c 0,89/0,90 score a MAI sűrű zónára áll. Első tiszta szint-teszt: a 2026-07-es target (Eurostat ~07-29) a mai korpusszal.
6. **Szint-réteg általában:** kalibráció nélkül sehol nem idézhető (választási rés-túllövés CZ-n 26–40 pont; FR/IT CCI 12–32 pont; inflexp skála-offset) — a `data/delphoi_calibration.json` affin horgonyai ezt kezelik, ahol van elég pont.
7. **YT cím-appeal (6):** mindkét modellen zaj-közeli — nem prediktív domén, nem építünk rá.

## 3. VEZETŐI ÖSSZEFOGLALÓ

A PYTHIA-kampány (G0–G3, ~10,7k Hy3-hívás, $0 LLM-költség) fő eredménye, hogy a szintetikus panel-módszer **irány- és sorrend-rétege modellfüggetlenül igazolt**: a CCI-előjelet, a nowcast-ívek alakját, a választási rangsort és az országok közti ár/CCI-rangsort a Hy3 ugyanúgy hozza, mint a Flash — a HU-cella pedig a cutoff-szondával bizonyítottan kontamináció-mentes vak becslés. A **szint-réteg viszont sehol nem áll meg kalibráció nélkül**: a rés- és CCI-túllövések szisztematikusak, ezért készült el a `data/delphoi_calibration.json` affin horgony-registry (4 kalibrálható + 1 alacsony-konfidenciás cella; a többi insufficient vagy kategorikus-ajánlás). A legfontosabb NEM-ek: a 07-20-i 0,4 pontos HU-találat seed-szerencse volt (seed-átlag +7,5 vs GT +14,6 — seed-átlagolás kötelező); a Hy3 szláv szabad-mondatos cellái (CZ-CCI, PL) regiszter-hiba miatt irány-vesztők, ott a kategorikus réteg az út; az FR/IT szint-rés a történeti korpusz ritkaságából jön, nem a módszerből — az első sűrű-zónás target (2026-07) dönt. A G3 pedig lezárta a scope-vitát: a szektorális mélység bukása modell-invariáns (Hy3 −0,311 ≈ Flash −0,378, modellek közti +0,74 korreláció), a korlát a bemenet tulajdonsága — a sajtószövegből semmilyen modell nem húz ki szektor-differenciális jelet. **Doktrína: irány és sorrend igazolt; szint csak kalibrációval; szektor-mélység out of scope — modellfüggetlenül.** Üzemi következmény: a Hy3 $0-s árszinten ekvivalens vagy jobb (PT!) a Flash-nél az igazolt rétegeken, a kirakat-entitások közül az fr/it növekedési-hangulat flip-érett, a PL csak kategorikus réteggel, az UK/US marad kikapcsolva.

## 4. ENTITÁS-JAVASLAT (flip NÉLKÜL — a delphoi.py `_SEED_ENTITIES` nem módosult)

| Entitás | Ország | Most | Javaslat | Indoklás |
|---|---|---|---|---|
| `fr-novekedesi-hangulat` | FR | 0 | **FLIP-JELÖLT enabled=1-re** | G2: CCI irány 3/3, SSR+kategorikus egybehangzó, seed-stabil (σ≤3,1); a szint-rést a registry FR-cellája kezeli (offset-only horgony) ÉS az éles futás már a QUELLENSCHLEUSE utáni sűrű korpuszon megy — a G2 korpusz-kikötése élesben nem áll fenn. Első validáció: 2026-07 Eurostat (~07-29). |
| `it-novekedesi-hangulat` | IT | 0 | **FLIP-JELÖLT enabled=1-re** | G2: irány 3/3 + a legjobb ablak-diszkrimináció (+0,5); a túllövés bizonyítottan korpusz-összetétellel skálázik, az éles korpusz gazdagabb; kalibráció a registry IT-cellájából (OLS, n=3, r²=0,77). |
| `pl-inflacios-varakozas` | PL | 0 | **FELTÉTELES — csak a kategorikus réteg implementálása UTÁN** | Az ár-rang réteg G1-ben PL-lel együtt működik, DE a szláv szabad-mondat regiszter-hiba (5b) miatt szabad-mondatos SSR-rel irány-vesztő; registry: `use_categorical_layer`. Plusz: PL-korpusz 40 forrás < 60-küszöb (G0c) — forrás-bővítés ajánlott. |
| `keir-starmer`, `nigel-farage` | UK | 0 | **MARAD 0** | G0c: nincs gépi GT (Eurostat ✗, OECD MEI 2024-01-gyel megszakadt, GfK API-mentes) és a demográfia-réteg sem táplálható. |
| `Q22686` (Trump) | US | 0 | **MARAD 0** | Nem volt G-teszt tárgya; GT- és konfig-réteg nincs. |
| Macron / Meloni / Tusk + HU-entitások | FR/IT/PL/HU | 1 | változatlan | Már enabled; a G2 az FR/IT infrastruktúrát (anchorok, kvóták, GT-lánc) hibamentesen igazolta. |

## 5. KAPCSOLÓDÓ ARTEFAKTOK

- Kalibrációs registry: `data/delphoi_calibration.json` (séma a fájl `_schema` mezőjében) — 13 bejegyzés: 4 calibrated + 1 calibrated_low_confidence + 5 insufficient_data + 2 use_categorical_layer + 1 anchor_pair (Agora Flash 28,4 ↔ Hy3 43,6, panel_version `orakel-agora-hu-v2-hy3`).
- Bridge-memória: `delphoi_gesamtbefehl_backtest_szintezis_2026_07` (category: learning, instance: cli-claus).
- Gépi tábla: `konszilencia_v2.json` (ebben a mappában).
