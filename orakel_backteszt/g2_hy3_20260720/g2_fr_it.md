# OPERATION PYTHIA — G2 ELSŐ KÖR: FR + IT VAK BACKTESZT (2026-07-20)

**Modell:** KIZÁRÓLAG `tencent/Hy3` (SiliconFlow, thinking:disabled, response_format nélkül, temp=0,8) · **Flash-hívás: 0 db** · **Seed-szabály:** minden cella 3 seed (G1-lelet #1) · **Protokoll:** a G1 nyerő receptjeinek klónja (CCI: persona-only + korpusz-grounding + MONDAT/KATEGORIA kettős formátum + SSR linear + text-embedding-3-small; inflexp: run_inflexp_backtest 2-soros protokoll), G0d-ben élesített `REFERENCE_SETS_FR/IT` + `delphoi.REFERENCE_SETS_PRICE_EXTRA` horgonyokkal, `persona_sampler.sample_country_personas` kvótákkal (COUNTRY_QUOTAS FR/IT a G2-ben feltöltve, HU-viselkedés változatlan, tesztek zöldek). Gépi artefakt: `g2_fr_it.json`.

**Pre-regisztráció:** GT-lock `74c5090` → korpuszok `712130b` → persona-kvóták `4f60df0` → harness `9cb8799` — MIND a futások előtt. GT: Eurostat `ei_bsco_m` BS-CSMCI + BS-PT-NY (SA/BAL, statdata MCP-rétegen). **Flag: mind a 12 cella CLEAN** (targetek 2026-04/05/06 ≥ 2025-08).

**Ablak-design (eltérés-jegyzet):** a parancs szerinti „target-hónapot megelőző ~3 hét" ablakokban a prod DB fr/it rétege a scrape későbbi indulása miatt kritikusan ritka volt (fr 2026-03: 10 cikk; a sűrű zóna 2026-06-21-től él, ami minden GT-fedett targethez késői). A „told közelebbre" klauzula szerint a 3 legkésőbbi GT-fedett targetet vettük (2026-04/05/06), fieldwork-igazított korpusz-ablakkal ([M−1 hó 15. → M hó 14.]; az EU-s fogyasztói felmérés terepmunkája a hónap első 2-3 hete, a GT a hónap végén publikálódik → nincs target-szivárgás). Részletes indoklás: `gt_LOCKED_fr_it.json`.

## 1. CELLA-TÁBLA (12 cella, seed-átlagok; zárójelben a 3 seed)

| Cella | Korpusz (n, lean-jegyzet) | GT | **Hy3 SSR** (seedek) | Kategorikus | Irány | \|e\| | σ |
|---|---|---|---|---|---|---|---|
| FR-CCI 2026-04 | 30, Le Point-right 28/30, foreign 28 | −21,8 | **−49,6** (−45,3/−51,0/−52,4) | −88,3 | ✓ | 27,8 | 3,1 |
| FR-CCI 2026-05 | 60, right 56/60 | −21,1 | **−47,0** (−44,9/−47,0/−49,2) | −88,9 | ✓ | 25,9 | 1,8 |
| FR-CCI 2026-06 | 60, right 39/60, center 17 | −19,3 | **−50,8** (−50,2/−52,4/−49,7) | −89,4 | ✓ | 31,5 | 1,2 |
| IT-CCI 2026-04 | 22, mind unknown-lean, lokál/tech | −23,8 | **−50,4** (−48,8/−51,5/−51,1) | −85,6 | ✓ | 26,6 | 1,2 |
| IT-CCI 2026-05 | 29, unknown 17 + center 12 | −21,3 | **−36,9** (−35,3/−41,9/−33,4) | −77,8 | ✓ | 15,6 | 3,6 |
| IT-CCI 2026-06 | 58, unknown 45 + center 13 | −22,2 | **−34,5** (−35,7/−34,7/−33,1) | −74,5 | ✓ | 12,3 | 1,1 |
| FR-ár 2026-04 | 30 | +51,0 | **2,270**/5 (2,245/2,373/2,192) | fin −22,9 | (✗)* | — | 0,08 |
| FR-ár 2026-05 | 60 | +42,6 | **2,290**/5 (2,224/2,343/2,303) | fin −22,3 | (✗)* | — | 0,05 |
| FR-ár 2026-06 | 60 | +28,2 | **2,325**/5 (2,225/2,395/2,356) | fin −24,5 | (✗)* | — | 0,07 |
| IT-ár 2026-04 | 22 | +40,9 | **2,072**/5 (2,038/2,079/2,098) | fin −31,1 | (✗)* | — | 0,03 |
| IT-ár 2026-05 | 29 | +26,3 | **2,167**/5 (2,170/2,147/2,184) | fin −23,5 | (✗)* | — | 0,02 |
| IT-ár 2026-06 | 58 | +25,1 | **2,145**/5 (2,115/2,148/2,171) | fin −15,9 | (✗)* | — | 0,02 |

\* A pre-regisztrált sign(score−3)-szabály szerint miss, DE ez szisztematikus skála-offset, nem G2-hiba: a G1-referencián HU GT +36,5 mellett 2,31, CZ +23,6 mellett 1,72 volt a score — a 3-as anchor-középpont („lassabban drágul") nem a GT-nulla. Az informatív ár-metrika a RANG (lásd 2.).

## 2. ORSZÁG-SZINTŰ METRIKÁK

| Metrika | FR | IT |
|---|---|---|
| CCI irány-találat | **3/3** | **3/3** |
| CCI 3-ablak Spearman (GT-sorrend) | **−0,5** | **+0,5** |
| CCI \|e\| (átlag) | 28,4 | 18,2 |
| Ár 3-ablak Spearman | **−1,0** | **−0,5** |
| Ár kereszt-ország rangsor (FR>IT, ablakonként) | **3/3 ✓** (2,27>2,07 · 2,29>2,17 · 2,33>2,15 vs GT 51>41 · 43>26 · 28>25) | |
| CCI kereszt-ország rangsor | 1/3 | |
| Seed-σ tartomány | CCI 1,2–3,1 · ár 0,05–0,08 | CCI 1,1–3,6 · ár 0,02–0,03 |

## 3. VERDIKT ORSZÁGONKÉNT (a G0c-mátrix „IGAZOLHATÓ" státuszához)

- **FR: RÉSZBEN MEGERŐSÍTVE, korpusz-kikötéssel.** Az irány-réteg él (CCI 3/3, SSR + kategorikus egybehangzó; ár kereszt-rangsor 3/3; seed-stabil), a szint-réteg nem: −26..−32 pontos CCI-túllövés és negatív ablak-Spearman. A rés lokalizálható: a backtest-ablakok a QUELLENSCHLEUSE (07-15) ELŐTTI ritka zónába esnek — 30/60/60 cikk, Le Point-right túlsúly, foreign-témadominancia (Afrika/háborús riportok) —, a G0c 0,89-es korpusz-score a MAI sűrű zónára áll, a történeti ablakokra nem.
- **IT: RÉSZBEN MEGERŐSÍTVE, korpusz-kikötéssel.** Irány 3/3, a legjobb G2-es ablak-diszkrimináció (CCI ρ=+0,5), és a korpusz-hatás közvetlenül kimérhető: a lokál-katasztrófa-hírű 2026-04-es ablak (22 cikk: árvíz/bűnügy/baleset) −50,4-et ad, a gazdagabb 05/06 (29/58 cikk) −36,9/−34,5-öt — a túllövés a korpusz-összetétellel, nem az országgal skálázik.
- **Közös konklúzió a G0c-nak:** a konfig/GT/demográfia/anchor-réteg (a mátrix 4 kritériumából 3) MŰKÖDÖTT és megerősítve; a korpusz-kritérium IDŐFÜGGŐ — a G0c-score a jelenre igaz, a GT-fedett múltra nem. A „nowcast-grade" szint-állítás első tiszta tesztje az első sűrű-zónás target lesz (2026-07, Eurostat-publikáció ~07-29) — G3/G4-ajánlás: akkor azonnali újramérés a mai korpusszal; szint-kalibrációhoz a PT-minta (kurált, kiegyensúlyozott egysoros korpusz — G1-ben |e|=0,1..2,6!) a bizonyított út.

## 4. POZITIVITÁS-TORZÍTÁS ÚJLATIN NYELVEKEN: **NEM JELENT MEG**

A szláv minta (G1: CZ +12 vs GT −7,5, PL ár-CCI irány-hiba — a Hy3 cseh/lengyel mondatai „bizakodó" regiszterben) FR/IT-n **nem áll fenn, sőt fordított**: minden cellában pesszimizmus-túllövés, és a beépített kategorikus réteg (a G1 CZ-minta szerinti kettős elicitálás minden CCI-hívásban futott) az SSR-nél 25–40 ponttal MÉLYEBB negatívot ad (FR kat. −85..−93; IT −70..−90). SSR és kategorikus előjele mind a 12 cellában egyezik → irány-vesztés pozitív irányba sehol, külön összevető futamra nem volt szükség. A Hy3 újlatin szöveg-regisztere tehát nem „szépít" — a G2-torzítás forrása a hír-korpusz doom-skew-ja, amit a kategorikus válasz (árnyalat nélküli „pire/peggiore") még fel is erősít, az SSR pedig tompít.

## 5. KÖLTSÉG-LOG

| Tétel | Érték |
|---|---|
| Logikai = API-hívás | **1800** (12 cella × 3 seed × N=60/40; 0 cache-hit — egyetlen futam sem szakadt meg, szeletelés nem kellett) |
| Tokenek (kar/3,2 becslés) | ~2,78M prompt + ~0,15M completion |
| Panel-idő | ~23,3 perc (G0a/B pacing: konk. 24, ≤100 hívás/perc; 0 db 429) |
| Hy3-ár | **$0** (NULLTARIF) |
| Embedding | OpenAI text-embedding-3-small, ~72 rate-hívás (filléres) |

## 6. INTEGRITÁS

- Pre-reg lánc: GT `74c5090` → korpusz `712130b` (sha256-ok a commit-üzenetben és cella-artefaktokban) → persona `4f60df0` → harness `9cb8799` → 12 cella-commit (`5a3c1dd`…`a57bf29`, cellánként azonnal).
- plugins/ változás KIZÁRÓLAG `persona_sampler.py` COUNTRY_QUOTAS (FR/IT feltöltés a delphoi kanonikus tárból; `tests/test_persona_sampler_country.py` 7/7 + `test_persona_sampler.py` 5/5 zöld, HU bitre változatlan).
- G1-artefaktok érintetlenek (g1_lib importtal újrahasznosítva); éles prod DB kizárólag `mode=ro`; DeepSeek-V4-Flash hívás: **0 db**.
