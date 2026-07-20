# OPERATION PYTHIA — G0c: ORSZÁG-ALKALMASSÁGI MÁTRIX (2026-07-20)

Adat-audit, LLM-hívás nélkül. Négy kritérium, **mind kell** az IGAZOLHATÓ státuszhoz:
(1) KORPUSZ, (2) GROUND TRUTH, (3) MÉDIA-LEAN-KONFIG, (4) DEMOGRÁFIA.

Mérési források: Echolot lokál DB (`/home/tamas1/Hirmagnetmcp/echolot.db`, read-only)
+ **éles prod DB** (railway ssh, `file:/data/echolot.db?mode=ro`, glistening-luck);
Eurostat/OECD a statdata-rétegen át (Makron MCP, tényleges lekérdezésekkel);
`~/Claus/claus-bridge-mcp/plugins/delphoi.py` + `persona_sampler.py` + `ssr.py` kód-audit.

---

## VERDIKT-TÁBLA

| Ország | 1. KORPUSZ (score) | 2. GROUND TRUTH | 3. LEAN-KONFIG | 4. DEMOGRÁFIA | **VERDIKT** |
|---|---|---|---|---|---|
| **HU** | ✅ 0.80 (1903 cikk/nap, 85 forrás; ⚠ 56% unknown-lean) | ✅ CSMCI −1.0 + PT-NY 10.8 (2026-06) + választás 2026-04 MEGVOLT | ✅ KÉSZ (legrészletesebb, KSH/NMHH) | ✅ KSH mun0005/6 + cenzus + NMHH | **IGAZOLHATÓ** |
| **FR** | ✅ 0.89 (1985/nap, 170 forrás, balansz 0.67) | ✅ −19.3 + 28.2; elnökválasztás 2027-04 (jövőbeli GT) | ✅ KÉSZ (nowcast-grade) | ✅ Eurostat-marginálisok | **IGAZOLHATÓ** |
| **IT** | ✅ 0.90 (3166/nap, 187 forrás, balansz 0.83) | ✅ −22.2 + 25.1; parlamenti legkésőbb 2027-10 | ✅ KÉSZ (nowcast-grade) | ✅ Eurostat-marginálisok | **IGAZOLHATÓ** |
| **PL** | ⚠ 0.72 (944/nap, de 40 forrás < 60-küszöb; bal-oldal vékony L1154/R3914) | ✅ −13.3 + 20.2; parlamenti 2027 ősz | ✅ KÉSZ (nowcast-grade) | ✅ Eurostat-marginálisok | **IGAZOLHATÓ** (forrás-bővítés ajánlott) |
| **DE** | ✅ 0.87 (2931/nap, 111 forrás, balansz 0.77; unknown 24%) | ⚠ Eurostat ✅ (−14.6 + 37.4), DE választási GT nincs közel (Bundestag 2025-02 az ablak ELŐTT, következő 2029) | ❌ HIÁNYZIK (terv kész: lean_konfig_tervek.md) | ✅ VERIFIKÁLT (demo_pjan 2025: 83,58M; edat_lfse_03: 35,4%; degurba-bontás edat_lfs_9913) | **RÉSZBEN** — konfig+anchorok pótlásával Eurostat-GT-re igazolható |
| **ES** | ✅ 0.82 (4058/nap, 157 forrás; ⚠ jobbra húzó eloszlás CR 30,9k vs CL 3,4k) | ⚠ Eurostat ✅ (−5.3 + 27.8), választás legkésőbb 2027-08 | ❌ HIÁNYZIK (terv kész) | ✅ VERIFIKÁLT (edat_lfse_03: 42,4%) | **RÉSZBEN** — konfig+anchorok+korpusz-balansz-súlyozás után igazolható |
| **CZ** | ❌ 0.48 (399/nap, 17 forrás; NINCS bal-lean forrás, L=0) | ✅ −4.3 + 25.4; választás 2025-10 MEGVOLT (+ cz_nowcast backtest-fájlok élnek) | ✅ KÉSZ (FG-grade; priming-frissítés kell) | ✅ VERIFIKÁLT (edat_lfse_03: 28,7%) | **RÉSZBEN** — a rés pontosan a korpusz; út: scrape-bővítés (bal-oldal!) VAGY a bizonyított fan-out (corpus_cz_*.json) |
| **PT** | ❌ 0.43 (926/nap, de 5 forrás, MIND center — lean-monokultúra) | ✅ −21.9 + 47.1 (⚠ legmagasabb inflációs várakozás!); elnökválasztás 2026-01 MEGVOLT | ✅ KÉSZ (FG-grade) | ✅ VERIFIKÁLT (edat_lfse_03: 32,7%) | **RÉSZBEN** — út: forrás-onboarding VAGY marad a bizonyított PT-minta fan-out (corpus_pt_*.json, 3 ablak) |
| **EN (UK)** | ✅ 0.95 — a LEGJOBB (uk_ szegmens: 1068/nap, 51 forrás, L/R balansz 0.98; de nyelvi szűrés helyett source-prefix kell) | ❌ **NEM ELÉRHETŐ GÉPI ÚTON**: Eurostat nem szolgál UK-t; OECD MEI GBR.CSCICP02 2024-01-gyel MEGSZAKADT (kivezetett dataset); GfK/NIQ csak press-release, nincs API a rétegen | ❌ HIÁNYZIK (terv fiókba kész) | ❌ Eurostat nem fed le friss UK-t; ONS nincs a statdata-rétegen | **NEM IGAZOLHATÓ** — a korpusz hiába a legjobb, a GT- és demográfia-réteg géppel nem táplálható |
| **RO** | ❌ 0.26 (182/nap, 4 forrás, 53% unknown) | ⚠ Eurostat ✅ (−34.6 + 27.8), DE a választási GT terhelt (2024-12 annullált elnökválasztás), következő országos 2028 | ❌ HIÁNYZIK (terv kész) | ✅ VERIFIKÁLT (edat_lfse_03: 19,5%) | **NEM IGAZOLHATÓ** jelenleg — korpusz kritikusan hiányos + konfig nincs; Eurostat-GT él, így scrape-bővítéssel felépíthető |

Echolot-lefedettségben még jelen, de NEM jelölt: ru/uk(ukrán)/ar/zh (GT-rezsim ill.
szabad-sajtó feltétel nem teljesül), ja/nl/sk/fa/be (korpusz és/vagy GT-panel nincs).

---

## 1. KORPUSZ — mérés és képlet

**coverage_score = 0.4·density + 0.3·diversity + 0.3·lean_balance**, ahol
- density = min(1, cikk/nap ÷ 300) — 300/nap fölött a 7-napos nowcast-ablak biztosan telített;
- diversity = min(1, aktív forrás (30 nap) ÷ 60) — a nyelvenkénti 60-forrás küszöb (memória-doktrína);
- lean_balance = known_share × (1 − |L−R|/(L+R)); L = left+center_left, R = right+center_right(+right_independent) cikk-súllyal; known_share = nem-unknown cikkek aránya; L+R=0 → 0.

### PROD DB (éles, 30 nap: 2026-06-20 → 07-20) — ez a mérvadó

| lang | cikk/30d | cikk/nap | aktív forrás | reg. forrás | known | L | R | balansz | **score** |
|---|---|---|---|---|---|---|---|---|---|
| en | 360 839 | 12 028 | 904 | 1032 | .79 | 35 949 | 47 570 | .86 | (globál — nem ország) |
| es | 121 728 | 4 058 | 157 | 171 | .89 | 14 051 | 49 405 | .44 | **0.82** |
| it | 94 965 | 3 166 | 187 | 220 | .79 | 12 325 | 17 247 | .83 | **0.90** |
| de | 87 914 | 2 931 | 111 | 116 | .76 | 11 182 | 6 959 | .77 | **0.87** |
| fr | 59 534 | 1 985 | 170 | 209 | .95 | 9 453 | 4 812 | .67 | **0.89** |
| hu | 57 091 | 1 903 | 85 | 101 | .44 | 12 375 | 7 649 | .76 | **0.80** |
| en/uk_* | 32 043 | 1 068 | 51 | 53 | 1.00 | 8 370 | 7 970 | .98 | **0.95** |
| pl | 28 315 | 944 | 40 | 43 | .89 | 1 154 | 3 914 | .46 | **0.72** |
| pt | 27 794 | 926 | 5 | 7 | 1.00 | 0 | 0 | .00 | **0.43** |
| cs | 11 974 | 399 | 17 | 20 | 1.00 | 0 | 2 590 | .00 | **0.48** |
| ro | 5 474 | 182 | 4 | 5 | .47 | 0 | 0 | .00 | **0.26** |

(Lokál dev-DB összevetésül: nagyságrenddel kisebb — pl. en 526/nap vs prod 12 028/nap;
a QUELLENSCHLEUSE-bővítés (2114 forrás, 07-15) csak a prod-on él. A verdiktek a prod-ra épülnek.)

**Hiány-utak:**
- *Scrape-bővítés* (Echolot forrás-onboarding): CZ (bal-oldal: Právo/Novinky, A2larm, Deník Referendum), PT (Público, Observador, CM, Expresso, SIC, RTP), RO (G4Media, HotNews, Digi24, Libertatea, România TV), PL (bal: OKO.press, Krytyka Polityczna), UK jobb-bulvár (Mail, Sun, Express, GB News).
- *Fan-out korpusz a PT-minta szerint*: `corpus_pt_*.json` / `corpus_cz_*.json` a Bridge-gyökérben — időablakonként ~20 kurált, egysoros hír-állítás (JSON string-lista). CZ-re és PT-re 3-3 ablakon bizonyított; kis munkaigényű híd, amíg a scrape-bővítés beér.

## 2. GROUND TRUTH — ténylegesen lekérdezve (statdata / Makron MCP)

Eurostat `ei_bsco_m`, 2026-06, unit=BAL: **BS-CSMCI** (SA) / **BS-PT-NY** (SA):
HU −1.0 / 10.8 · CZ −4.3 / 25.4 · PT −21.9 / 47.1 · PL −13.3 / 20.2 · FR −19.3 / 28.2 ·
IT −22.2 / 25.1 · ES −5.3 / 27.8 · DE −14.6 / 37.4 · RO −34.6 / 27.8. Mind a 9 EU-jelöltre
mindkét mutató él és friss. **UK: a lekérdezés a 10 kért geo-ból 9-et adott vissza — UK-sor
nincs.** Pótlék-vizsgálat: OECD MEI `GBR.CSCICP02.STSA.M` (a GfK-alapú nemzeti CCI) létezik
DBnomics-on, de **utolsó megfigyelés 2024-01** (a MEI-t az OECD kivezette); a GfK/NIQ UK CCI
publikálódik, de csak sajtóközleményként, gépi API nincs a rétegen → **UK-ra nincs megbízható
gépi GT — kimondva.**

Választási GT (post-2025-Q4, országos, szabad/hiteles):
- **MEGVOLT**: CZ parlamenti 2025-10 · HU parlamenti 2026-04-12 (Tisza-kormány májustól) · PT elnökválasztás 2026-01.
- **JÖN (nowcast-horizonton)**: FR elnök 2027-04/05 · PL parlamenti 2027 ősz · IT legkésőbb 2027-10 · ES legkésőbb 2027-08.
- **NINCS KÖZEL**: DE (következő Bundestag 2029; a 2026-os tartományi választások csak regionálisak) · UK (legkésőbb 2029; 2026-05 devolvált/helyi) · RO (2028; plusz a 2024-12-es annullálás miatt a választási GT-hitelesség terhelt — becsületesen: RO választási GT-re nem építünk).

## 3. MÉDIA-LEAN-KONFIG — kód-audit (plugins/delphoi.py, NEM módosult)

- **KÉSZ**: HU (teljes: KSH-marginálisok + 5-elemű media-mix + priming), PL, FR, IT
  (nowcast-grade), **CZ, PT** (FG-grade, durvább marginálisok) — a parancs várakozásánál
  (HU/PL/FR/IT) TÖBB van kész.
- `FG_COUNTRIES` default: HU,CZ,PT,PL. `_SEED_ENTITIES`: HU×3 + Tusk/Macron/Meloni enabled;
  UK (Starmer, Farage) és US (Trump) seedelve **enabled=0**.
- Anchor-készletek: REGARD hu/pl/fr/it · PRICE HU/CZ/PT/PL (ssr.py) + fr/it (delphoi
  PRICE_EXTRA) · **es/de/en/ro sehol** — új országhoz konfig + 2×5 natív anchor-mondat kell.
- **ÚJ konfig-tervek** (ES/DE/UK/RO) + CZ/PT-kiegészítés: `lean_konfig_tervek.md`
  (ugyanebben a mappában) — a delphoi.py-ba szándékosan NEM került be.

## 4. DEMOGRÁFIA — persona_sampler + Eurostat-verifikáció

- `persona_sampler.py` = generikus KL-kvóta-balanszolt mintavevő (dims-agnosztikus;
  korreláció kor×iskola×település közt NEM modellezett — LOOT #9). A kvóta-CÉLOK a
  `COUNTRY_PANEL_CONFIG.dims`-ben élnek: HU = KSH mun0005/mun0006 + 2022 cenzus + NMHH
  2026-05; PL/FR/IT/CZ/PT = "Eurostat-közeli durvább marginálisok" (kód-komment szerint).
- Eurostat-lekérdezésekkel VERIFIKÁLVA (statdata-rétegen át): `demo_pjan` (DE 2025:
  83 577 140) · `edat_lfse_03` (ED5-8, 25–64, 2025: CZ 28,7 / DE 35,4 / ES 42,4 / PT 32,7 /
  RO 19,5%) · `edat_lfs_9913` (végzettség×**degurba** együtt! DE 2024 tertiary: Cities 39,1 /
  Towns 30,9 / Rural 28,0) → kor×végzettség×településtípus MIND lekérdezhető minden
  EU-jelöltre. Konfig-séma: a meglévő `dims` formátum (age 5 sáv / settlement=degurba 3-4
  kategória / edu ISCED 3 sáv) — vázlatok országonként a lean_konfig_tervek.md-ben.
- **UK-kivétel**: Eurostat nem szolgál friss UK-demográfiát; ONS nincs a rétegen → a
  4. kritérium UK-ra is elbukik.

---

## VÁRHATÓ MAG vs VALÓSÁG

A parancs várakozása (mag): **FR / ES / DE / IT / PL / CZ / EN**. A mérés szerint:

- **Bejött**: FR, IT — mindkettő minden kritériumon zöld. DE és ES adat-oldalon erős
  (0.87/0.82 korpusz + GT + demográfia verifikált), csak a lean-konfig+anchor hiányzik —
  ez a leggyorsabban pótolható rés-típus.
- **Részben jött be**: PL (átmegy, de 40 forrás < 60-küszöb és vékony bal-oldal);
  CZ (konfig+GT+demográfia kész, de a korpusz 0.48 — a fan-out út tartja életben).
- **NEM jött be**: **EN(UK) a nagy fordulat** — korpusz-oldalon a LEGJOBB jelölt (0.95),
  mégis kiesik, mert a GT-réteg (Eurostat✗, OECD 2024-01-ben megszakadt, GfK API-mentes)
  és az Eurostat-demográfia géppel nem táplálható.
- **A parancs nem várta, de a mátrixban van**: HU (etalon, minden zöld); PT (konfig+GT jó,
  korpusz-monokultúra — fan-out-tal él); RO (leggyengébb: 0.26 korpusz + konfig-hiány +
  terhelt választási GT → nem igazolható, de Eurostat-GT-je él, felépíthető).

**Javasolt élesítési sorrend**: HU/FR/IT (kész) → DE/ES (konfig+anchor pótlás) →
PL (forrás-bővítés mellett) → CZ/PT (fan-out híd + scrape-bővítés) → RO (előbb korpusz) →
UK (csak ha lesz gépi GT-forrás).
