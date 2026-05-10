# KSH STADAT — magyar hivatalos statisztika

A KSH STADAT a hivatalos magyar makró-adatforrás. A heti makró-briefekben szereplő
fogyasztói árindex, maginfláció, kereseti adatok, GDP innen származnak.

## Tábla-kódok és tartalmuk

| Tábla | Mit ad | Frekvencia |
|---|---|---|
| `ara0039` | A főbb ármutatók havonta — **fogyasztói árindex (CPI), ipari termelői árindex, építőalapanyag-ipari árindex** (előző év azonos időszaka = 100) | havi |
| `ara0045` | **Maginfláció** (eredeti és szezonálisan kiigazított) — **figyelem**: a tábla **bázis-indexet** ad (1990-es alap), NEM közvetlen YoY %-ot. YoY-számításhoz: `(idei_érték / előző_évi_érték - 1) × 100` | havi |
| `ara0002` | CPI **éves** bontás kiadási csoportonként (élelmiszer, szolgáltatás, stb.) | éves |
| `mun0143` | Havi kereseti adatok (bruttó/nettó átlagkereset) | havi |
| `gdp0001` | GDP érték és volumen-változás (negyedéves gyorsbecslés is itt) | negyedéves |
| `gdp0004` | GDP éves nominál HUF/EUR/USD/PPP (1995-től) | éves |

## Számformátum-konvenciók

- A `ara0039` `Fogyasztóiár-index` mező értéke **bázis = 100**: 102,1 → +2,1% YoY.
- Az `ara0045` (maginfláció) **bázis-index** (1990-es alap körül 600 körüli értékek). YoY %-ra
  külön számítani kell. Ne idézd a 656-os abszolút számot mint inflációt!
- Üres mező = még nem publikálva. A friss hónapok között szerepelhet üres,
  ne tévedj: csak a számmal kitöltött a publikus adat.

## Citation — emberi formátumban

**Sablon:** `KSH, <tartalmi név>, <adat dátuma>` + URL, ha az adatblokkban van.

Példák:
- KSH, fogyasztói árindex, 2026. április — +2,1% (https://www.ksh.hu/stadat_files/ara/hu/ara0039.csv)
- KSH, maginfláció (eredeti, YoY), 2026. április — +2,2%
- KSH, havi kereseti adat, 2026. február

**TILOS** a citation-ben:
- `get_ksh_stadat`, `_bridge_fetched_at`, `ara0039` — ezek belső rendszer-tag-ek, az olvasó nem tudja értelmezni.
- A Bridge által lekérés időpontját adat-dátumként idézni.
- Olyan URL-t kitalálni, ami az adatblokkban nem szerepel.

## Anti-hallucination

- Ha az adat **dátumát** (év + hónap) az adatblokkban nem találod, a citation-ben írj
  csak intézmény + tartalmi név (dátum nélkül), és a "Hiányzó / nem-elérhető források"
  záró szekcióba flaggeld.
