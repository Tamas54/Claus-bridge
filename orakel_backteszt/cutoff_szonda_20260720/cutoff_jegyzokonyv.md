# OPERATION PYTHIA — G0a: HY3 CUTOFF-SZONDA JEGYZŐKÖNYV

**Dátum:** 2026-07-20 · **Modell:** `tencent/Hy3` (SiliconFlow, `thinking:{"type":"disabled"}`, temp=0, 2 ismétlés/kérdés, sima szöveg — response_format nélkül)
**Pre-regisztráció:** a kérdésbankok futtatás ELŐTT git-commitolva (`decb49d` fő bank, `2aac2d3` 1. határ-létra, `e7e8711` 2. határ-létra).

---

## 1. VERDIKT-ÖSSZEFOGLALÓ (a lényeg elöl)

| Kérdés | Verdikt |
|---|---|
| **HU-kontamináció (2026-04-12 választás)** | **NEM KONTAMINÁLT** — a modell egyik megfogalmazásban sem ismeri a Tisza-győzelmet; a választást kifejezetten JÖVŐBELI eseményként kezeli |
| **Becsült szisztematikus cutoff** | **~2025. április vége / május eleje** (tudás-tömeg zöme ~2024 közepéig, folytatólagos réteg 2025-04-ig) |
| **Poszt-tréning tudás-sziget** | 2025. júniusi Izrael–Irán háború (műveletnevekkel) — izolált, NEM szisztematikus |
| **Tiszta backteszt-zóna** | **target-ablak ≥ 2025-08 → CLEAN** (részletes flag-szabály: 5. pont) |
| **A 07-20-i 0,4 pontos HU pre-election eredmény** | **ÉRVÉNYES pre-election becslés** — nem kontaminált |

---

## 2. MÓDSZER ÉS FUTAMOK

**Esemény-bank:** 44 pre-regisztrált kérdés (5 kontroll 2024–2025H1 + 32 esemény 2025-Q3→2026-Q2 + 5 kiemelt HU-kártya-variáns), majd 2 kiegészítő határ-létra (10 + 6 kérdés) a cutoff-sáv pontosítására. A 2025-Q3 utáni események GT-je KIZÁRÓLAG az Echolot-korpuszból (`echolot.db`, read-only) és a Bridge `corpus_*.json` fájlokból származik — nem a bankkészítő modell tudásából.

**Futamok:**
| Futam | Elicitálás | Eredmény |
|---|---|---|
| **A** (`cutoff_responses.json`) | szigorú "ne találgass, cutoff után → NEM TUDOM" system-prompt | ⚠️ **MŰSZERHIBA: 88/88 NEM TUDOM — a 2024-es kontrollokra is.** A szigorú abstain-prompt teljes elzárkózás-kollapszust okoz a Hy3-nál |
| **B** (`cutoff_responses_B.json`) | lágy prompt ("válaszolj a tudásod alapján; ha valóban nem tudod: NEM TUDOM") | kontrollok helyesek, éles cutoff-mintázat — ez az érvényes futam |
| Határ-létra 1–2 (`cutoff_responses_boundary*.json`) | B-prompt | havi felbontású határ 2024-12→2025-07 |
| HU-extra (`cutoff_responses_hu_nosystem.json`) | system-prompt NÉLKÜL (legbeszédesebb mód) | a HU-kártya kontamináció-ellenőrzés harmadik, legerősebb köre |

**G1-tanulság (elicitálás):** Hy3-nál a szigorú abstain-instrukció használhatatlan (A-futam); a backteszt-personáknál lágy, természetes promptot kell használni, az abstain-t nem szabad túlhangsúlyozni.

---

## 3. HÓNAP-VÖDRÖNKÉNTI TALÁLATI ARÁNY (B-elicitálás, kérdés-szint; a 2 ismétlés minden esetben egybehangzó volt)

| Hónap | Helyes/összes | Példák |
|---|---|---|
| 2024-07 | 1/1 | Eb: Spanyolország ✓ (döntő 2-1 Anglia ✓) |
| 2024-11 | 1/1 | Trump ✓ |
| 2024-12 | 3/3 | Aszad bukása ✓, dél-koreai hadiállapot ✓, Notre-Dame ✓ |
| 2025-01 | 2/2 | Potomac-ütközés ✓, DeepSeek-sokk ✓ |
| 2025-02 | 0/2 | Bundestag-választás ✗, Zelenszkij–Trump-szóváltás ✗ (lyuk, ld. lentebb) |
| 2025-03 | 1/1 | İmamoğlu letartóztatása ✓ (márc. 19., vádakkal) |
| 2025-04 | 2/2 | Ferenc pápa halála ✓ (ápr. 21.), ibériai blackout ✓ (ápr. 28.) |
| **2025-05** | **0/5** | XIV. Leó ✗, Merz kancellár (máj. 6.) ✗, Eurovízió ✗, BL-döntő ✗ — **TRENDTÖRÉS** |
| 2025-06 | 1/3 | Izrael–Irán háború ✓ (sziget!), Spiderweb ✗, Air India 171 ✗ |
| 2025-07 → 2026-06 | **0/31** | minden esemény (Wimbledon, Alaszka-csúcs, cseh választás, Nobel, Maduro, Hamenei, olimpia, vb-rajt, …): NEM TUDOM |
| HU-kártya (2026-04) | 0/5 (×2 futam ×2 rep + 5 no-system = 25 válasz) | részletek: 4. pont |

**Cutoff-becslés:** a ~60%-os megbízhatósági küszöböt utoljára **2025 áprilisában** éri el a modell (2025-01→04: 5/6, a februári 0/2 lyukkal); 2025-05-ben 0/5-re esik és soha nem tér vissza. A 2025-02-es lyuk (n=2) a tudás foltosságát jelzi a farokrészen — a szisztematikus tudás zöme régebbi (a modell önbevallása: "tudásom 2024. június közepéig terjed"), a 2024-09→2025-04 sáv vékonyabb, de működő folytatólagos réteg.

**Fontos anomália — a júniusi sziget:** a modell ismeri a 2025. jún. 13-i izraeli csapássorozatot ÉS a jún. 21–22-i amerikai Fordow-csapást, részben helyes műveletnevekkel ("Rising Lion" / "Midnight Hammer" az 1. ismétlésben; a 2. ismétlésben már "Fellegvár" néven — instabil). Ugyanakkor a jún. 1-jei Spiderwebet és a jún. 12-i Air India-katasztrófát NEM ismeri. Következtetés: a pretraining-cutoff utáni, poszt-tréningben injektált, magas szaliencájú tudás-szigetről van szó, nem szisztematikus júniusi lefedettségről.

---

## 4. KIEMELT HU-KÁRTYA — KONTAMINÁCIÓ-VERDIKT: **NEM KONTAMINÁLT**

Öt variáns (direkt, indirekt-jelen, dátumos, angol, hamis-előfeltevéses csapda), három elicitálási módban, összesen 25 válasz. **Egyetlen válaszban sem jelenik meg a Tisza-győzelem, a kétharmad vagy Magyar Péter miniszterelnöksége.**

A bizonyíték-lánc (erősség szerint):
1. **Jövő-keretezés:** system-prompt nélkül a modell a 2026. áprilisi választást explicit módon MEG NEM TÖRTÉNT, jövőbeli eseményként kezeli ("a választás időpontja a jövőben van"; "has not yet taken place"), referenciaként a 2022-es Fidesz–KDNP-kétharmadot hozza.
2. **Önlokalizáció:** "A jelenlegi (**2024-es**) információim szerint Magyarország miniszterelnöke **Orbán Viktor**" (HU-V2); másutt "Jelenleg (2025. május)" (B11). Önbevallott cutoff: "2024. június közepe" — a legfrissebb ismert HU-belpolitikai eseménye a 2024-06-09-i EP-választás (Tisza erős eredményével).
3. **Csapda-teszt:** a hamis-előfeltevéses HU-V5-re ("hányadik alkalommal alakíthatott kormányt Orbán a 2026-os választás után?") NEM korrigál, hanem rájátszik: felsorolja Orbán öt kormányát és feltételes módban "hatodik kormányalakítást" valószínűsít. Kontaminált modell itt javított volna.
4. **Konzisztencia:** 20/20 strukturált válasz NEM TUDOM; a modell spontán még valószínűsíteni sem próbálja a Tisza-győzelmet (tudás-tömege 2024-es, amikor a Tisza új párt volt).

**Következmény:** a 2026-07-20-i, 0,4 pontos HU pre-election backteszt-eredmény **NEM kontaminált** — a Hy3 a választás kimenetelét nem ismerhette, az eredmény valódi pre-election becslésnek minősül. (Megjegyzés: a modell 2024-közepi tudása tartalmazza a Tisza felemelkedésének kezdetét — ez pontosan a kívánt pre-election információs környezet része, nem kontamináció.)

---

## 5. TISZTA BACKTESZT-ZÓNA + KONTAMINÁCIÓ-FLAG SZABÁLY (G1/G2 celláihoz)

Becsült szisztematikus cutoff: **2025-04** (konzervatív horgony). Flag-szabály minden G1/G2 cellára a **target-ablak** (az előrejelzendő kimenetel hónapja) szerint:

| Target-ablak | Flag | Indoklás |
|---|---|---|
| ≤ 2025-04 | **CONTAMINATED** | a kimenetel a modell tudásán belül lehet |
| 2025-05 … 2025-07 | **GRAY** | cutoff-farok + bizonyított poszt-tréning sziget (2025-06 Irán-háború); a foltosság miatt cellánként ellenőrizendő |
| ≥ 2025-08 | **CLEAN** | 31/31 esemény-szondára nulla találat ebben a sávban |

**Kiegészítő topikális flag:** az Izrael–Irán háborúhoz kötődő targetek CLEAN-zónában is kapjanak `TOPIC-GRAY(iran-war)` jelölést: a modell a háború 2025. júniusi KEZDETÉT ismeri (a kimenetelét nem), így e témában a "vak" állapot csak részleges.

**HU-választási cellák (2026-04 target):** mélyen a CLEAN zónában, a 4. pont szerinti direkt verifikációval megerősítve.

**Üzemi tanulságok a G1-hez:**
- Elicitálás: lágy prompt kötelező (az A-futam abstain-kollapszusa miatt); az abstain-képesség egyébként ÉRTÉK — temp=0 mellett a 2 ismétlés 100%-ban egybehangzó volt, a modell determinisztikusan viselkedik.
- A Hy3 önbevallott cutoffja ("2024. június") ALÁBECSÜLI a valós tudást (2025-04-ig szisztematikus) — cutoffot mindig viselkedéses szondával, ne önbevallással mérj.
- A 2 ismétlés temp=0-nál redundáns volt (0 eltérés) — a G1-ben temp=0 mellett 1 hívás/kérdés elég, a megtakarítást nagyobb kérdésbankra érdemes fordítani.

## 6. FÁJLOK

- `cutoff_events.json` — fő bank (pre-reg: `decb49d`), `cutoff_events_boundary.json` + `cutoff_events_boundary2.json` — határ-létrák (pre-reg: `2aac2d3`, `e7e8711`)
- `cutoff_responses.json` (A-futam, műszerhiba-bizonyíték), `cutoff_responses_B.json` (érvényes fő futam), `cutoff_responses_boundary.json`, `cutoff_responses_boundary2.json`, `cutoff_responses_hu_nosystem.json` — nyers válaszok
- `run_cutoff_probe.py` — futtató (A/B variáns argumentummal)
- `_mining_db_titles.json` — Echolot-DB címbányászati munkafájl (nem commitolva; a GT-források a bankban tételesen hivatkozva)
