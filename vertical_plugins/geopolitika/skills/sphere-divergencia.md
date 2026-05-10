# Sphere-divergencia — Echolot szerkesztői perspektíva-clusterek

Az Echolot **sphere-eket** használ, nem országokat. Egy sphere = szerkesztői /
narratív perspektíva-cluster, amit hasonló kerettel dolgozó kiadók adnak.
Két orosz lap lehet eltérő sphere-ben (állami vs. ellenzéki), két különböző
ország lapja lehet ugyanazon sphere-ben (anchor-presztízsmédia).

## Sphere-tipológia (a Bridge `geopolitics` preset szerint)

| Sphere | Mit ad | Tipikus források |
|---|---|---|
| `global_anchor` | nemzetközi mainstream, hír-prezentáció | Reuters, AP, AFP, BBC News |
| `global_analysis` | mély-elemzés, vélemény-kontextus | FT, Economist, Foreign Affairs |
| `global_conflict` | haditudósítás, konfliktus-fókusz | ISW, Bellingcat, Janes |
| `regional_us` | amerikai belpolitikai keret | NYT, WaPo, WSJ |
| `regional_uk` | brit belpolitikai keret | Guardian, Telegraph, Times |
| `regional_chinese` | kínai állami / félállami narratíva | Xinhua, Global Times, CGTN |
| `regional_russian` | orosz állami / félállami narratíva | TASS, RIA, RT |
| `iran_regime` | iráni állami narratíva | IRNA, PressTV, Tasnim |
| `israel_press_center` | izraeli kormány-közeli sajtóközlemények | GPO, Ynet, JPost |
| `ua_front_osint` | ukrán front OSINT, harctéri jelentések | DeepState, ASTRA, OSINT-aggregátorok |
| `hu_press` / `hu_economy` / `hu_tech` | magyar sajtóterület | Index, Telex, hvg, Portfolio |

## Hogyan olvass egy sphere-csoportosított hírblokkot

A `format_news_block` függvény sphere-szerint csoportosít. Olvasási sorrend:

1. **Először `global_anchor`-t** — ez a "konszenzus-narratíva" (mit tudunk objektíven)
2. **Aztán `global_analysis`** — mit jelent ez, milyen olvasatok vannak
3. **Aztán a sphere-párokat egymás ellen**:
   - `regional_us` ↔ `regional_russian` (NATO-Oroszország témák)
   - `regional_chinese` ↔ `regional_us` (Tajvan, technológia)
   - `iran_regime` ↔ `israel_press_center` (Közel-Kelet)
   - `ua_front_osint` ↔ `regional_russian` (ukrán front)
4. **Végül `hu_press`** — magyar visszhang, belpolitikai keretezés

## Cross-sphere olvasási szabályok

- **Ha egy esemény csak egy sphere-ben van**: az állítást **kondicionálva**
  idézd ("X szerint" / "Y állítása szerint"), ne tényként.
- **Ha egy esemény több sphere-ben is, eltérő keretezéssel**: ez a
  divergencia maga a hír. Idézd a kereteket egymás mellé:
  *"A `global_anchor` X-ként írja le; a `regional_russian` Y-ként."*
- **Ha egy esemény minden sphere-ben azonosan**: konszenzusos tény,
  citation-elhetjük forráscsoportként.

## A sphere mint elemzési fogalom — NEM citation-mező

A sphere-perspektíva **a brief szövegében** mint elemzői szempont szerepel
("az angolszász mainstream szerint... az orosz állami sajtó szerint..."), de
**SOHA** nem kerül a citation-be `sphere=X` formában. Lásd: `source-citation`
skill — a citation kötelezően cím + orgánum + dátum + URL formátumú.

## Anti-hallucination

- **NEM** kanonizálsz "valamelyik kiadói tér általában X-et mond" típusú
  állítást a tréning-emlékezetedből — csak abból, ami a HÍRBLOKKBAN van.
- Ha egy perspektíva nem szerepel a blokkban (pl. nincs amerikai vagy kínai
  cikk a fő topic-okra), NE feltételezd hogy az adott perspektíva "biztos
  azt mondaná" — a "Hiányzó / nem-elérhető források" záró szekcióba flaggeld
  **kifejtett magyar formában** ("az amerikai mainstream perspektíva nincs
  az adatblokkban"), ne nyers `sphere=regional_us` kóddal.
