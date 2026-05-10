# Eurostat — EU-harmonizált makró-adatok

Az Eurostat EU-szinten harmonizált adatokat ad — országok között közvetlenül
összehasonlítható. A heti makró-briefben az inflációs (HICP) és GDP-összevetés,
valamint a munkanélküliségi ráta forrása.

## Dataset-ek és tartalmuk

| Dataset | Mit ad | Frekvencia |
|---|---|---|
| `prc_hicp_manr` | HICP — Harmonised Index of Consumer Prices, **éves változás %-ban** (Annual rate of change) | havi |
| `namq_10_gdp` | Quarterly GDP (chain-linked volume, 2015 = 100, milliárd euró) | negyedéves |
| `une_rt_m` | Munkanélküli ráta (ILO definíció), %-ban | havi |
| `irt_st_m` | Money market rate (3-hónapos pénzpiaci kamat) — MNB irányadó ráta proxy | havi |

## Filter-konvenciók

GDP standard filter: `na_item=B1GQ&unit=CLV15_MEUR&s_adj=SCA`
- `B1GQ` = bruttó hazai termék piaci áron
- `CLV15_MEUR` = chain-linked volume, 2015 reference, millió euró (volumen, infláció kiszűrve)
- `SCA` = seasonally and calendar adjusted

Növekedési ütem-számítás: a Eurostat **abszolút értékeket** ad (millió EUR).
YoY-számításhoz: `(<aktuális negyedév>_érték / <egy_évvel_korábbi_negyedév>_érték - 1) × 100`.

## Földrajzi entitások (`geo`)

- `HU` = Magyarország
- `EA` = eurózóna (változó tagsággal, lásd Eurostat dokumentáció)
- `EA20` = eurózóna 20 tagú (2023-tól)
- `EU27_2020` = EU 27 tagú (2020-tól)

A `prc_hicp_manr` viszonylag egyszerű — `EA` érvényes. A `namq_10_gdp` a **20-tagú** eurózónát
preferálja `EA20`-ként.

## Publikálási rend — strukturált API vs. flash press release

| Adat | Strukturált API (`get_eurostat_data`) | Flash press release (Eurostat newsroom) |
|---|---|---|
| HICP (`prc_hicp_manr`) | 2-4 hét hónap után | **havi flash a hónap végén** (~28-30-án), `web_scrape` tool-lal |
| GDP (`namq_10_gdp`) | 30-45 nap negyedév után | quarterly flash, ~30 nap után |
| Munkanélküliség (`une_rt_m`) | 4-6 hét hónap után | havi flash ~30 nap után |
| Money market rate (`irt_st_m`) | 2-3 hét hónap után | — |

**KÖTELEZŐ**: ha a strukturált API utolsó adatpontja nem a folyó hónap, a flash
press release-t **`web_scrape`-pel kötelező lehúzni**. A flash adat ezekben az
esetekben már létezik HTML formában (JS-rendered SPA-ban), a strukturált API
csak később indexeli.

### Workflow-minta — Eurostat flash HICP (évtől független)

Az Eurostat newsroom (`ec.europa.eu/eurostat/web/products-euro-indicators` és környéke)
minden hónap végén kiad egy euro indicator flash press release-t. A **konkrét URL-konvenció
változhat redesign-nal** — ne támaszkodj fix URL-mintára. A megbízható út:

1. `web_search(query="eurostat hicp <aktuális hónap> <aktuális év> flash")` — a
   hónap és év a brief lekérdezési időpontjából vegyed.
2. A találatok közül **válaszd a `ec.europa.eu` domain-t hordozót**. Ha az első
   5 találatban nincs hivatalos forrás, ismételd meg site-szűréssel:
   `web_search(query="hicp flash site:ec.europa.eu")`.
3. `web_scrape(url=hivatalos URL)` → a press release teljes markdown-szövege.
4. Onnan idézd: éves HICP, energia / élelmiszer / szolgáltatás-infláció bontása,
   előző hónapi összevetés.

### Workflow-minta — MNB irányadó kamat (évtől független)

1. `web_search(query="mnb irányadó kamat <aktuális év> monetáris tanács")`
2. **Hivatalos URL preferálása**: a `mnb.hu` domain-en lévő találat. Ha az első
   5-ben nincs, ismételd site-szűréssel: `web_search(query="irányadó kamat site:mnb.hu")`.
3. `web_scrape(url=mnb.hu URL)` → a közlemény markdown-szövege a döntés időpontjával.

**Kerüld a fix URL-mintára építést** — sem a `mnb.hu/...`, sem a `ec.europa.eu/.../w/<id>`
slug-konvenció nem garantáltan stabil évek között. A `web_search` mindig megtalálja
az aktuális struktúrát.

A magyar oldalon a **KSH `ara0039`** lényegesen gyorsabban frissül (2-3 hét után már
közli a fogyasztói árindexet), ezért a HU-CPI és HU-PPI tekintetében a hazai kép
naprakészebb mint az Eurostat-i HICP — **DE az eurozónás HICP-re a `web_scrape`-es
flash press release ugyanolyan friss**.

## Citation — emberi formátumban

**Sablon:** `Eurostat, <tartalmi név>, <geo>, <adat dátuma>` + URL ha van.

Példák:
- Eurostat, HICP (éves változás %), Magyarország, 2025. december — +3,3%
- Eurostat, HICP, eurózóna, 2025. december — +2,0%
- Eurostat, GDP (chain-linked volume), Magyarország, 2026 Q1 — +1,7% YoY

**TILOS** a citation-ben:
- `get_eurostat_data`, `prc_hicp_manr`, `geo=HU`, `_bridge_fetched_at` — belső tag-ek.
- API-szintű URL (`ec.europa.eu/eurostat/api/...`) idézése; az emberi-böngészéshez a
  `databrowser/view/<dataset>` URL kellene, de ezt **csak akkor** használd citation-ben,
  ha az adatblokk explicit megadta.

## KSH CPI vs Eurostat HICP — különbség

A magyar inflációt két forrás méri, **nem azonosak**:
- **KSH CPI** (`ara0039` → `Fogyasztóiár-index`) — magyar kosár-súlyok.
- **Eurostat HICP** (`prc_hicp_manr` geo=HU) — EU-harmonizált kosár.

A két szám **rendszeresen 1-2 százalékponttal eltérhet** (pl. 2025-12-re KSH és HICP
**különbözhet**, mert eltérő súlyokkal mérik). Ez **nem hiba**, csak metodikai különbség.

A briefben:
- Magyar belpolitikai elemzéshez (MNB-célzás, lakossági reálbér) **KSH CPI-t** idézz.
- EU-szintű összevetéshez (eurózóna inflációval, ECB policy-vel) **HICP-et**.
- **Soha** ne tedd egy mondatba a két számot úgy, mintha egyformák lennének.
