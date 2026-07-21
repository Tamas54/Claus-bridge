# OPERATION G2-WEST — US/UK/DE VALIDÁCIÓS JELENTÉS (2026-07-21)

**Modell:** KIZÁRÓLAG `tencent/Hy3-preview` (a `tencent/Hy3` 2026-07-21-től 402/20015 —
futás előtt verifikálva; **Flash-hívás: 0 db**) · **Seed-szabály:** minden cella 3 seed ·
**Protokoll:** a G2 FR/IT nyerő receptjének 1:1 klónja EN/DE nyelvre (CCI: persona-only +
korpusz-grounding + SENTENCE/CATEGORY ill. SATZ/KATEGORIE kettős formátum + SSR linear +
text-embedding-3-small; inflexp: 2-soros PRICES/FINANCES ill. PREISE/FINANZEN), a G3
országbővítés élő infrastruktúráján (`COUNTRY_PANEL_CONFIG` US/UK/DE, `persona_sampler`
kvóták KL<0,05, `ssr.REFERENCE_SETS_EN/DE` + `REFERENCE_SETS_PRICE['EN'/'DE']` horgonyok).

**Pre-reg lánc:** audit+GT-lock `8ec6e31` → amendment_1+korpuszok `6ca49bc` → harness
`78806b8` → futások+jóslatok `a35dc6d` — a GT-értékek és MINDEN kiértékelési szabály a
modell-futások ELŐTT commitolva. Cutoff: `cutoff_preview_probe.json` — a preview a
2024-12 utáni eseményeket és a backteszt-GT-ket DIREKT kérdésre sem ismeri → **minden
cella CLEAN**.

---

## 1. A FŐ EREDMÉNY EGY MONDATBAN

**Nyugati (US/UK/DE) IGAZOLT backteszt a történeti korpusz-résekből fakadóan NEM
születhetett** (a us_/uk_/de réteg 2026-06-21 előtt üres, a us_ ráadásul 07-08-ig
Us Weekly-monokultúra — kimondva, nem hamisítottunk); helyette a PYTHIA-doktrína
legerősebb bizonyíték-formája készült el: **8 előregisztrált, számszerű, bárki által
ellenőrizhető jóslat** (P1–P8), amelyek GT-je **2026-07-24 és 07-31 között érkezik**.

## 2. KORPUSZ-AUDIT VERDIKT (részletek: korpusz_audit.md)

| Szegmens | Használható zóna | Június-targetek | Július-targetek |
|---|---|---|---|
| us_ | **2026-07-09-től** (39 forrás; előtte celeb-monokultúra) | NEM fedhető | részleges (fieldwork-farok) |
| uk_ | 2026-06-21-től (49 forrás, ír források kizárva) | NEM fedhető | fedhető (GfK fieldwork ~07-01..15) |
| de_ | 2026-06-21-től (30 forrás, de_ prefix-szűréssel) | NEM fedhető | fedhető (EU-felmérés fieldwork júl. eleje) |

## 3. CELLA-TÁBLA (7 futás, seed-átlagok; zárójelben a 3 seed)

| Cella | Ablak | Korpusz | **SSR-szaldó** (seedek) | Kategorikus | Ár-score | σ |
|---|---|---|---|---|---|---|
| US-A CCI | 07-09→07-14 | 58, 39 forrás | **−35,5** (−32,8/−38,1/−35,7) | −72,1 | — | 2,2 |
| US-A ár | 07-09→07-14 | ua. | — | — | **2,628** (2,682/2,576/2,625) | 0,043 |
| US-B CCI | 07-15→07-21 | 59 | **−41,1** (−41,3/−38,7/−43,1) | −83,9 | — | 1,8 |
| US-B ár | 07-15→07-21 | ua. | — | — | **2,720** (2,662/2,595/2,903) | 0,132 |
| UK CCI | 06-21→07-15 | 60 | **−38,3** (−41,2/−33,8/−40,0) | −80,5 | — | 3,3 |
| DE CCI | 06-21→07-14 | 60 | **−50,3** (−50,6/−50,2/−50,0) | −94,4 | — | 0,25 |
| DE ár | 06-21→07-14 | ua. | — | — | **2,073** (2,095/2,033/2,092) | 0,029 |

Seed-stabilitás mindenütt a G2-tartományban (σ ≤ 3,3); a DE-cella kiugróan stabil.

## 4. BACKTESZT (redukált hatókör — becsületes elszámolás)

Egyetlen pre-outcome-fedhető, már-publikált nyugati GT volt: **Michigan 2026-07 prelim
= 54,4** (publ. 07-17; a us_A ablak vége 07-14). A MoM-irány ág a monokultúra-lelet
miatt HALOTT (amendment_1, futások előtt kimondva). A megmaradt, előre rögzített
level-sign szabály eredménye:

| Cella | GT (szabály) | Panel | Irány | Verdikt |
|---|---|---|---|---|
| US-A CCI vs Michigan 07P | 54,4 > 12m-átlag 53,575 → **+** | −35,5 → **−** | ✗ | **MISS — PONTOSAN az előre commitolt várakozás szerint** (hír-doom-skew; G2 FR/IT-vel egybehangzó). Nem validációs bizonyíték, hanem a szint-réteg kalibráció-igényének újabb, előre bejelentett dokumentálása. |

## 5. ELŐREGISZTRÁLT JÓSLATOK (prereg_JOSLATOK.json, commit `a35dc6d`, 2026-07-21 ~15:4x CET)

| # | Mire | Jóslat | GT érkezik |
|---|---|---|---|
| P1 | Michigan 2026-07 **final** vs prelim 54,4 | **final < 54,4** (lefelé revízió; Δssr = −5,5, kategorikus Δ = −11,8 egybehangzó) | **07-31** |
| P2 | Michigan final **1y-infláció** vs 4,2% | **> 4,2%** (felfelé; Δprice = +0,092) | **07-31** |
| P3 | Conference Board 2026-07 vs 91,2 | **< 91,2** (MÁSODLAGOS/exploratory — nincs júniusi baseline-panel) | **07-28** |
| P4 | GfK UK 2026-07 (baseline −23) | nyers szaldó **−38,3**; sign −; \|e\| kalibrációs nyersanyag | **07-24** |
| P5 | Eurostat DE BS-CSMCI 2026-07 (baseline −14,6) | nyers szaldó **−50,3**; sign −; G2-formátumú sign+\|e\| — az első német "dense-zone target" teszt | **~07-30** |
| P6 | Eurostat DE BS-PT-NY 2026-07 (baseline +37,4) | price_score **2,073** → sign(score−3) = − (ismert szisztematikus offset-tudattal regisztrálva) | **~07-30** |
| P7 | NIM/GfK Konsumklima 2026-08 (baseline −29,2) | DE-panel −50,3; sign − | **07-24** |
| P8 | Kereszt-ország pesszimizmus-RANGSOR (z-normalizált) | **DE > UK > US** (−50,3 / −38,3 / −35,5) — a G1/G2-ben validált RANG-réteg tesztje | 07-24..31 |

A kiértékelési képletek (z-formula, idősorok, sign-szabályok) a `gt_LOCKED_west.json`-ban
a futások ELŐTT zárolva; a jóslatok corpus-sha256-szal és timestamppel a
`prereg_JOSLATOK.json`-ban.

## 6. KONSZILENCIA-TÁBLA-KOMPATIBILIS SOROK (a G4-tábla folytatása)

| # | Domén | Ország | Irány | Szint | Diszkr. | Flag | Seed | Verdikt |
|---|---|---|---|---|---|---|---|---|
| 9a | CCI (új ország) | US | backteszt-irány NEM mérhető (korpusz-rés, kimondva); level-sign MISS az előre bejelentett doom-skew szerint | ✗ (kalibrálatlan) | P1/P2/P3 prereg folyamatban | CLEAN (szondával direkt igazolva) | ✓ σ≤2,2 | **FÜGGŐ — prereg-kimenetig** |
| 9b | CCI (új ország) | UK | P4 prereg folyamatban | ✗ (nyers) | P8 rang-teszt folyamatban | CLEAN | ✓ σ=3,3 | **FÜGGŐ — 07-24-én dől el** |
| 9c | CCI (új ország) | DE | P5 prereg folyamatban | ✗ (nyers; várt túllövés) | P8 | CLEAN | ✓✓ σ=0,25 (kampány-rekord) | **FÜGGŐ — 07-30-án dől el** |
| 9d | Ár (új ország) | US/DE | P2/P6 prereg | skála-offset ismert | US>DE ár-rang exploratory | CLEAN | ✓ | **FÜGGŐ** |

## 7. METHODOLOGY-AJÁNLÁS (aipolling — copy-t NEM módosítottam)

**MOST még SEMELYIK nyugati ország nem léphet "validated"-re** — a "backtest validation
in progress" sor US/UK/DE-re tényszerűen pontos, és ez a művelet éppen a "in progress"
tartalmát tette kemény bizonyíték-formává. Javasolt lépcső:

1. **07-24 (GfK UK + Konsumklima):** ha P4 sign-hit ÉS P8 DE-UK párja talál → az UK
   sor mehet *"validation in progress (preregistered forecast pending: 2/4 resolved)"*
   típusú, dátumozott státuszra.
2. **07-28..31 (CB + Eurostat + Michigan final):** ha P1 (a legerősebb, valódi
   irány-jóslat) talál ÉS P5 sign-hit ÉS P8 legalább 2/3 pár → **DE javasolható
   "validated (direction & rank layer)"-re** (a szint-réteg explicit kizárásával,
   ahogy az FR/IT-nél); az **US** a P1+P2 találat esetén *"direction-validated
   (preregistered)"*-re. UK-nál a GT-lánc gépi táplálhatatlansága (G0c) miatt a
   "validated" címke mellé kézi-GT lábjegyzet kell.
3. **Bukás esetén** a sor marad "in progress", és a prereg-artefakt NYILVÁNOS
   kudarc-dokumentum — ez a doktrína szerint ugyanolyan értékű kimenet.

Szöveg-javaslat a methodology-oldalra (EN, beillesztésre kész):
> *"For the US, UK and Germany, validation is running as **preregistered live
> forecasting**: panel outputs, corpus hashes and evaluation rules were committed to
> git before the official releases (Michigan final & Conference Board July, GfK UK
> July, NIM Consumer Climate August, EU harmonised consumer confidence July). Each
> forecast is scored publicly when the figure is published — hits and misses alike."*

## 8. MELLÉK-LELETEK (javítási TODO-k, külön munkarendbe)

1. **SQLite LIKE-wildcard bug:** a `LIKE 'uk_%'` mintában az `_` joker — az
   `ukrainska_pravda_en` a uk_ szegmensbe matchel. A prod `build_country_corpus`
   source_prefixes-szűrése és a G0c-mátrix mérései érintettek lehetnek → `ESCAPE`
   kell mindenhová. (Itt: javítva `ESCAPE '!'`-lel.)
2. **Ír források a uk_ prefixben** (Irish Independent/Times, RTÉ, TheJournal —
   ~23% a uk_ poolból): a UK country-panel korpuszából ki kell zárni (itt: kizárva);
   prefix-átnevezés (ie_) javasolt.
3. **de nyelvi réteg ≠ DE ország** (31% osztrák/svájci/egyéb): `COUNTRY_PANEL_CONFIG['DE']`
   kapjon `source_prefixes=('de_',)`-t; plusz id-névtér-szemét: Tagesspiegel=`hu_tag`,
   NZZ=`hu_nzz`.
4. **us_ réteg mélysége:** multi-forrás rezsim csak 2026-07-09-től — a 60-forrás
   nyelvi küszöb-doktrína szellemében a us_ (39) és de_ (30) forrás-szám bővítendő.

## 9. KÖLTSÉG-LOG

| Tétel | Érték |
|---|---|
| Hy3-preview hívás | **1080** (7 cella; 0 megszakadt futam) + 16 szonda + 2 modell-próba |
| Tokenek (becslés) | ~1,4M prompt + ~0,12M completion |
| Panel-idő | ~12,3 perc (G0a/B pacing, 0 db 429) |
| Hy3-preview ár | **$0** (NULLTARIF) |
| Embedding | OpenAI text-embedding-3-small (filléres) |
| Flash / plugins-módosítás / deploy | **0 / 0 / 0** (éles prod DB kizárólag mode=ro) |
