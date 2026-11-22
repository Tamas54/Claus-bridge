# CCI-BACKTESZT V4.1-FLASH — JEGYZŐKÖNYV (2026-11-22/23)

## Röviden, egyszerűen

**Mit mértünk:** jól olvassa-e a DELPHOI szintetikus panelje a gazdasági hangulatot.

**Ki kivel:**
- **A panel** országonként és hónaponként 60 kitalált ember (3 × 20). Őket a `DeepSeek-V4.1-Flash` modell játssza el.
- Mindegyik 16 valódi újságcímet olvas az adott ország adott havi sajtójából (Echolot). Utána megmondja, hogyan látja a családja anyagi helyzetét a következő 12 hónapban.
- **A mérce** a valódi felmérés: az Európai Bizottság havi fogyasztói bizalmi indexe (Magyarországon ezt a GKI kérdezi le), az USA-ban a Michigan-index.
- **Öt ország:** Magyarország, Franciaország, Olaszország, Németország, USA. **Négy hónap:** 2026 július–október.

**Tisztaság:** a modell nem tudhatta a választ. Mind a 2026-os hónapra rákérdeztünk közvetlenül, magyarul és angolul. Mind a 30 kérdésre „nem tudom” volt a válasz. Ugyanezzel a kérdéssel a 2024-es Michigan-értéket pontosan megmondta, vagyis amit tud, azt ki is mondja.

**Mi jött ki:**
- **Irány (javult vagy romlott a hangulat):** 10 hónapból 6-szor találta el. Ez nem jobb a pénzfeldobásnál. A panel kategóriás válaszaival 7/10, ez sem bizonyító erejű.
  - Eltalálta a nagy francia októberi esést és az amerikai augusztus–szeptemberi esést.
  - A nagy magyar augusztusi romlást (−4,4) nem találta el.
  - A 15 havi panelmozgásból csak 2 nagyobb a panel saját zajánál.
- **Szint:** a panel átlagosan 13 ponttal borúlátóbb a valóságnál.
  - Országonként állandó eltolással a hiba 4,0 pontra csökken.
  - A „ugyanannyi, mint múlt hónapban” tipp hibája viszont csak 1,4 pont.
- **Sorrend:** az országok sorrendjét fordítva adja (ρ = −0,37). A valóságban a magyar a legderűlátóbb (−3…+1), a panelben a legborúlátóbb (−25…−34).

**Ítélet:** a panel ma nem olvassa jól a havi gazdasági hangulatot. A hónapról hónapra történő mozgást nem jelzi előre megbízhatóan, és a szintje országonként 1–27 ponttal eltér. A mérés gyenge pontja a kis minta (20 fő/seed a júliusi 60 helyett, a költségkeret miatt): a valódi havi változások (0–4 pont) nagyrészt a panel zaján belül vannak.

---

## 1. Előregisztráció és integritás

A commitok sorrendje (`feat/cci-backteszt`):

| Commit | Tartalom | Mikor |
|---|---|---|
| `40707bf` | GT-zár (`gt_LOCKED_cci.json`), cellák, 80 próba-kérdés, tűrés, kiértékelési szabályok (`PREREG.json`), minden futtató | minden modellhívás előtt (kivéve 1 tokenmérő füsthívást és 1 embedding-próbát, egyik sem eredmény) |
| `78a8fc7` | célzott cutoff-próba: nyers válaszok + pontozás | a panel előtt |
| `878168c`, `c248ba8`, `f3e4f2b` | korpuszok országonként, sha256-tal (`korpusz_LOCKED.json`) + korpusz-audit | az adott ország panelje előtt |
| `d492124` | a 20 backteszt-cella + kiértékelés | — |
| `b2d13be` | novemberi előrejelzés (HU/FR/IT/DE), a GT megjelenése előtt; kiértékelő-javítás | — |

**GT:**
- EU: Eurostat `ei_bsco_m`, BS-CSMCI, SA, szaldó (a DG ECFIN harmonizált felmérése).
- US: FRED `UMCSENT`.
- Mindkettő a Makronóm MCP-ből jön, 2026-11-23-i közlés szerint.
- A júliusi zárolás óta kis revíziók vannak (pl. IT 2026-05: −21,3 → −21,2). Mindig a mai érték számít.

## 2. Célzott cutoff-próba (a döntő lépés)

- **Mit kérdeztünk:** minden ország × hónap cellára közvetlenül az indexértéket és a havi irányt (pl. „Mennyi volt a magyar fogyasztói bizalmi index 2026 szeptemberében, és javult vagy romlott augusztushoz képest?”), magyarul és angolul.
- **Hogyan:** lágy B-prompt, temp=0. Fájlok: `proba_valaszok.json`, `proba_pontozas.json`.
- **Tűrés:** EU ±1,0 pont, Michigan ±1,5 pont.
- **Mikor esik ki egy cella („tudta”):** ha az érték a tűrésen belül van, ÉS az irány is egyezik.

| Csoport | Cellák | Eredmény |
|---|---|---|
| 2026-06…10 (backteszt + bázis) | 25 × 2 nyelv | **0/50 tudta** — mind „NEM TUDOM” / „I DON'T KNOW” |
| 2026-11 (előrejelzés) | 5 × 2 | 0/10 — mind „nem tudom” |
| Kontroll 2024-12, 2025-06 (tudás-határon belül) | 10 × 2 | angolul 0/10 (mind „I DON'T KNOW”); magyarul 3/10 tudta (Michigan 74,0 és 60,7 pontosan; IT 2025-06 −16,2 a −17,1 mellett), 4 téves szám (pl. DE 2024-12-re −23,3: ez a GfK Konsumklima, nem a bizottsági index), 3 „nem tudom” |

**Tiszta cellák listája: mind a 20 backteszt-cella** (HU/FR/IT/DE/US × 2026-07…10), és mind a 4 novemberi EU-cella. Kiesett: egy sem.

A mért cutoff-tábla (`cutoff_szonda_20261122`: tiszta 2026-01-től) cellánként megerősítve.

## 3. Cella-tábla

- **Panel:** a 3 seed SSR-szaldójának átlaga, ± a standard hiba.
- **Irány-találat:** a panel havi változásának előjele egyezik-e a valóséval. A „lapos hónap” azt jelenti, hogy |valós változás| < 0,5, ezért kimarad.
- **Eltolás:** az ország korábbi hónapjaiból becsült átlagos különbség.

| Ország | Hónap | Tudta a modell? | Valós index | Valós változás | Panel (SSR-szaldó ± SE) | Panel-változás | Irány-találat | Szinthiba | Szinthiba eltolással |
|---|---|---|---|---|---|---|---|---|---|
| Magyarország | 2026-07 | nem | +1,2 | — | −34,0 ± 4,5 | — | bázis | −35,2 | — |
| Magyarország | 2026-08 | nem | −3,2 | −4,4 | −32,5 ± 2,5 | +1,6 | nem | −29,3 | +6,0 |
| Magyarország | 2026-09 | nem | −3,4 | −0,2 | −25,0 ± 1,1 | +7,5 | lapos hónap | −21,6 | +10,7 |
| Magyarország | 2026-10 | nem | −4,9 | −1,5 | −25,8 ± 1,7 | −0,8 | igen | −20,9 | +7,8 |
| Franciaország | 2026-07 | nem | −17,0 | — | −16,5 ± 1,2 | — | bázis | +0,5 | — |
| Franciaország | 2026-08 | nem | −17,8 | −0,8 | −14,7 ± 2,2 | +1,8 | nem | +3,1 | +2,6 |
| Franciaország | 2026-09 | nem | −18,5 | −0,7 | −22,1 ± 3,4 | −7,4 | igen | −3,6 | −5,4 |
| Franciaország | 2026-10 | nem | −21,9 | −3,4 | −25,9 ± 1,5 | −3,7 | igen | −4,0 | −4,0 |
| Olaszország | 2026-07 | nem | −20,2 | — | −25,6 ± 0,4 | — | bázis | −5,4 | — |
| Olaszország | 2026-08 | nem | −20,2 | 0,0 | −26,3 ± 0,6 | −0,6 | lapos hónap | −6,1 | −0,6 |
| Olaszország | 2026-09 | nem | −23,2 | −3,0 | −26,1 ± 0,8 | +0,2 | nem | −2,9 | +2,9 |
| Olaszország | 2026-10 | nem | −23,3 | −0,1 | −25,2 ± 0,6 | +0,8 | lapos hónap | −1,9 | +2,9 |
| Németország | 2026-07 | nem | −13,6 | — | −36,2 ± 1,6 | — | bázis | −22,6 | — |
| Németország | 2026-08 | nem | −12,5 | +1,1 | −33,7 ± 3,1 | +2,5 | igen | −21,2 | +1,4 |
| Németország | 2026-09 | nem | −14,3 | −1,8 | −32,9 ± 2,5 | +0,8 | nem | −18,6 | +3,3 |
| Németország | 2026-10 | nem | −14,5 | −0,2 | −34,8 ± 1,3 | −1,9 | lapos hónap | −20,3 | +0,5 |
| USA (Michigan) | 2026-07 | nem | 55,2 | — | −17,0 ± 1,1 | — | bázis | más skála | — |
| USA (Michigan) | 2026-08 | nem | 51,7 | −3,5 | −22,7 ± 1,4 | −5,8 | igen | más skála | −2,2 (tájékoztató) |
| USA (Michigan) | 2026-09 | nem | 48,1 | −3,6 | −23,8 ± 0,8 | −1,1 | igen | más skála | +1,4 (tájékoztató) |
| USA (Michigan) | 2026-10 | nem | 48,0 | −0,1 | −23,6 ± 1,6 | +0,2 | lapos hónap | más skála | +1,3 (tájékoztató) |

## 4. Összesítés az alapvonalak mellett (`eredmeny.json`)

**(a) Irány** — 10 nem-lapos hónap (HU 08, 10 · FR 08, 09, 10 · IT 09 · DE 08, 09 · US 08, 09):

| Jósló | Találat | p (egyoldali, 50% ellen) |
|---|---|---|
| Panel, SSR-szaldó (elsődleges) | **6/10** | 0,38 |
| Panel, kategorikus szaldó | 7/10 | 0,17 |
| Alapvonal: „az előző hónap iránya folytatódik” | 4/10 | 0,83 |
| Alapvonal: „nincs változás” | 0/10 (definíció szerint) | — |
| Csak EU, SSR | 4/8 | 0,64 |

A pre-regisztrált „jobb a véletlennél” küszöb (≥ 8/10, p ≤ 0,055) nem teljesült.

A 15 havi panelmozgásból 2 nagyobb a saját zaja kétszeresénél. Ez a kettő a HU 2026-09 (+7,5, a valóságban lapos hónap) és a US 2026-08 (−5,8, találat).

**(b) Szint** (EU, szaldópont):

| Mérőszám | Érték |
|---|---|
| Nyers átlagos abszolút hiba, 16 cella | **13,6** (átlagos eltolás −13,1: a panel borúlátóbb) |
| Országonkénti eltolás (valós − panel) | HU +26,8 · DE +20,7 · IT +4,1 · FR +1,0 |
| 08–10, nyers hiba | 12,8 |
| 08–10, eltolással (a korábbi hónapokból) | **4,0** |
| 08–10, „nincs változás” | **1,4** |
| US (tájékoztató, más skála) | eltolással 1,7 · „nincs változás” 2,4 |

Az állandó eltolás javít (12,8 → 4,0), de a naiv „mint múlt hónapban” tipp ennél háromszor pontosabb.

**(c) Sorrend** (Spearman ρ):

| Mérőszám | ρ |
|---|---|
| c1: a 16 EU-cella szintje | **−0,37** |
| c2: havonta a 4 EU-ország sorrendje (átlag) | −0,40 (07: −0,6 · 08: −0,6 · 09: 0,0 · 10: −0,4) |
| c3: havi változások, EU (12) | +0,23 |
| c3: havi változások, mind (15) | +0,33 |
| Országon belül, 4 hónap | FR +0,8 · US +0,8 · DE 0,0 · IT −0,63 · HU −0,8 |

## 5. Mit jelent

1. **A tudás-határ után a panel nem „jósol be”.** A cutoff tiszta, így ez valódi teszt, és az eredmény a véletlen szintje. Az irány-találat 6/10, a mozgások többsége zaj.
2. **A szint-eltérés országfüggő és nagy.**
   - A magyar és a német panel 20–27 ponttal borúlátóbb, a francia és az olasz közel van.
   - A magyar eltérés egybeesik a 2026-os fordulattal: a valós index 2026 áprilisa és májusa között −15,6-ról −2,1-re ugrott, a választás után.
   - A júliusi recept persona-only, priming nélkül. A panel a kormányváltásról csak a címekből tud, és a magyar personák szövege („rezsi, élelmiszer, nem követi a fizetés”) a régi hangulatot hozza. Ez magyarázat, nem bizonyított ok.
3. **Ahol nagy volt a valós mozgás, ott vegyes a kép.**
   - Találat: FR október (−3,4 ↔ −3,7), US augusztus–szeptember (−3,5/−3,6 ↔ −5,8/−1,1).
   - Tévedés: HU augusztus (−4,4 ↔ +1,6), IT szeptember (−3,0 ↔ +0,2).
4. **A mérés ereje kicsi.** A seed-szórás cellánként 0,6–6,4 pont, a havi változás standard hibája 0,7–5,1 pont, a valós havi változás 0–4,4 pont. Egy hibátlan irányú panel is gyakran zajba fulladna 20 fő/seed mellett.
5. **A júliusi Hy3-számokkal nem vethető össze 1:1** (más modell, más embedding, más korpusz-merítés). Csak tájékoztatásul: júliusban az FR-szint −47…−51 volt (−19…−22-es GT mellett), most −15…−26 (−17…−22 mellett).

## 6. Eltérések a júliusi recepttől és a PREREG-től (kimondva)

| Tétel | Július | Most | Ok |
|---|---|---|---|
| Modell | `tencent/Hy3` | `deepseek-ai/DeepSeek-V4.1-Flash` | a feladat |
| Embedding | `text-embedding-3-small` (OpenAI) | `Qwen/Qwen3-Embedding-8B` (SF, `delphoi.EMBED_MODEL_SF`) | a Bridge OpenAI-kulcsa 401 → **a júliusi számok nem 1:1 összevethetők** |
| N/seed | 60 | **20** (3 seed, 60 válasz/cella) | mért ár: HU-hívás 1891+125 token → N=60-nal 20 cella ≈ 1,3 $, a 0,50 $-os keret 2,6-szorosa (PREREG-ben előre kimondva) |
| Korpusz-merítés | prod-DB közvetlen olvasása (SQLite, mode=ro), véletlen 1200 | Echolot MCP `get_news`, minden 2. nap, `place=ország`, napi ≤100 cikk, forrásonként ≤3 | az éles DB azóta Postgres a Hostmanen; a nyilvános MCP csak olvas |
| Ország-szűrés | FR/IT csak nyelv, DE `de_` prefix, US/UK prefix | mind `place=ország` | a júliusi DE-lelet (osztrák/svájci forrás) általánosítva |
| Szivárgás-szűrő | nincs | minden fogyasztói-bizalmi/hangulati index-cím kiesik (5 cím összesen) | a panel ne lásson indexszámot |
| HU-szöveg | (G2-ben nem volt HU) | a G2-sablon magyar változata (MONDAT/KATEGÓRIA) | — |
| Cache | `LLMCache` (SQLite) | nincs; nyers válaszok JSON-ban (`raw/`) | házirend: nincs SQLite |

**Hibák és javítások, nyíltan:**
- A futtató első indítása a régi N=60-as alapértékkel ment (HU 2026-07, seed 1, 60 hívás). Leállítottam és elvetettem. A fájl: `raw/ELVETETT_N60_hu_2026-07_seed1.json`, nem része az eredménynek. Ára ~0,021 $, plusz a leállításkor folyamatban lévő hívások (≤ 0,006 $). Javítás: az N most a `PREREG.json`-ból jön.
  - Ez a futtató-javítás a futások előtt megtörtént, de csak utánuk került commitba (`d492124`).
- A próba-pontozó a magyar főnévi alakokat (javulás, romlás) eredetileg nem ismerte fel.
  - A javítás csak a kontroll-cellákat érinti: a 2026-os cellákon nem volt szám.
- A kiértékelőben a pontos nulla változás negatív iránynak számított.
  - Javítás után a kategorikus találat 8/10-ről 7/10-re változott, az SSR-é (6/10) nem.

## 7. Korpusz-lefedettség 2026-07…11 (`korpusz_audit.md`)

- **Lefedettség:** mind az 5 ország mind az 5 ablaka lefedett.
  - Napi pool 500 (a felső korlát) minden napon 2026-07-15-től.
  - A júliusi ablakok eleje (06-08/15…06-20) ritka: napi 8–21 cikk. Ez a júliusi audit „sűrű zóna 06-21-től” lelete.
- **HU-réteg:** 37–47 forrás, minden ablakban 500/nap. Témák: általános, gazdaság, politika.
- **Forrásszám:** FR 95–112, IT 72–118, DE 76–100, US 106–212.
- **Téma-torzulás:** a sport aránya magas (IT 33–45%, DE 19–31%, US 10–22%). Témaszűrés nincs, ahogy júliusban sem: a reprezentativitás elve.
  - A novemberi ablakban a külföldi hírek aránya ugrik (FR 62%, US „global” 61%).
- **UK:** kimarad, a GfK-index nincs a mérőeszközeinken.

## 8. Novemberi előrejelzés (`ELOREJELZES_2026-11.json`)

- **Mikor rögzítettük:** 2026-11-23 00:4x CET, a GT előtt (DG ECFIN, ~11-27…30).
- **Feltétel:** a PREREG szerint opcionális volt, és csak akkor futhatott, ha a költés < 0,40 $. Ez teljesült.

| Ország | Okt. valós | Panel okt. → nov. | Jósolt irány (SSR / kategorikus) | Zajnál nagyobb? | Jósolt szint (eltolással) |
|---|---|---|---|---|---|
| HU | −4,9 | −25,8 → −37,7 | romlik / romlik | igen | −10,9 |
| FR | −21,9 | −25,9 → −13,1 | javul / javul | igen | −12,0 |
| IT | −23,3 | −25,2 → −29,6 | romlik / romlik | igen | −25,5 |
| DE | −14,5 | −34,8 → −35,4 | romlik / javul | nem | −14,7 |

A novemberi panelmozgások szokatlanul nagyok (HU −11,9, FR +12,8), miközben a korpusz témaösszetétele is erősen elmozdult. A kiértékelés a PREREG szabályaival megy, amint a GT kint van.

## 9. Költség

| Tétel | SF (token × listaár, 0,15/0,60 $/M) |
|---|---|
| Célzott próba (80 hívás) | 0,0017 $ |
| Tokenmérő füsthívás (1) | 0,0004 $ |
| Elvetett N=60-as indítás | ~0,021 $ (+ ≤ 0,006 $) |
| 20 backteszt-cella (1200 hívás; 1,67 M prompt + 0,11 M kimeneti token) | 0,319 $ |
| 4 novemberi cella (240 hívás) | 0,069 $ |
| Embedding (Qwen3-Embedding-8B, 134 ezer token) | < 0,02 $ (0,15 $/M-es árnál is) |
| **Összesen** | **≈ 0,41–0,44 $** (keret 0,50 $); 402 nem volt; Anthropic 0 $ |

## 10. Nyitott pontok

1. **Novemberi kiértékelés:** a GT ~11-27…30-án jön. A `gt_LOCKED_cci.json` bővítése után az `evaluate.py` szabályai szerint kell kiértékelni.
2. **Erő:** a döntő irány-teszthez a júliusi N=60/seed kell. Ez 20 cellára ~1,3 $, vagy kevesebb cím personánként (más recept).
3. **Magyar szint-eltérés:** érdemes külön megmérni, hogy a priming (kormányváltás) behúzza-e a magyar panelt. Ez már nem a júliusi recept, ezért panel-verzió kell hozzá.
4. **US 2026-11:** a Michigan novemberi értéke megjelent (11-20), de a FRED-en még nincs fent. Nem teszteltük.
5. **UK:** GfK-forrás kell hozzá.

## Fájlok

| Fájl | Mi van benne |
|---|---|
| `PREREG.json`, `gt_LOCKED_cci.json`, `korpusz_LOCKED.json`, `make_prereg.py` | előregisztráció |
| `run_cutoff_cella_proba.py`, `grade_proba.py`, `proba_valaszok.json`, `proba_pontozas.json` | célzott cutoff-próba |
| `build_corpora.py`, `korpusz/`, `korpusz_audit.py`, `korpusz_audit.md` | korpusz |
| `run_panel_v41.py`, `run_cellak.sh`, `cellak/`, `raw/`, `panel_*.log` | panel |
| `evaluate.py`, `eredmeny.json`, `tabla.py`, `ELOREJELZES_2026-11.json` | kiértékelés |
