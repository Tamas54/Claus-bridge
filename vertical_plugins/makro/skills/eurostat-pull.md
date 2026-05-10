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
YoY-számításhoz: `(2026-Q1_érték / 2025-Q1_érték - 1) × 100`.

## Földrajzi entitások (`geo`)

- `HU` = Magyarország
- `EA` = eurózóna (változó tagsággal, lásd Eurostat dokumentáció)
- `EA20` = eurózóna 20 tagú (2023-tól)
- `EU27_2020` = EU 27 tagú (2020-tól)

A `prc_hicp_manr` viszonylag egyszerű — `EA` érvényes. A `namq_10_gdp` a **20-tagú** eurózónát
preferálja `EA20`-ként.

## Publikálási késleltetés — KÖTELEZŐ flag a briefben

| Adat | Tipikus késleltetés | 2026. május 10-i állapot |
|---|---|---|
| HICP (`prc_hicp_manr`) | 2-4 hét hónap után | **utolsó publikált 2025-12** (2026 január még NINCS) |
| GDP (`namq_10_gdp`) | 30-45 nap negyedév után | **2026-Q1 publikálva** |
| Munkanélküliség (`une_rt_m`) | 4-6 hét hónap után | **utolsó publikált 2026-03** (2026 április még NINCS) |
| Money market rate (`irt_st_m`) | 2-3 hét hónap után | **utolsó publikált 2026-03** |

A brief-ben **explicit ki kell mondani**, ha az Eurostat-adat utolsó publikált hónapja
nem a friss adatközlés napjához tartozik. Példa: *"Az eurózóna HICP utolsó publikált
hónapja 2025. december (2,0%); a 2026 első négy hónapjának eurózónás összevetése csak
2026. május végén lesz teljes."*

A magyar oldalon a **KSH `ara0039`** lényegesen gyorsabban frissül (2-3 hét után már
közli a fogyasztói árindexet), ezért a HU-CPI és HU-PPI tekintetében a hazai kép
naprakészebb mint az Eurostat-i HICP.

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
