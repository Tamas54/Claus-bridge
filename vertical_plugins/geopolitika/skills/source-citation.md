# Forrás-citation szabályok geopolitikai briefekhez

A geopolitikai brief **csak akkor használható**, ha minden állítás visszavezethető
egy konkrét cikkre, **emberi-olvasható formában**. A citation-formátum **ember-szempontú**:
az olvasó tudjon utánamenni a forrásnak.

## Citation-formátum (KÖTELEZŐ)

**Sablon**: *"<cím>"*, <orgánum>, <megjelenés dátuma>, <URL>

Példák:
- *"Russia launches missile barrage on Kharkiv energy grid"*, AP, 2026. május 9., https://apnews.com/article/2026-05-09-kharkiv-missile
- *"Why the Western Balkans enlargement push is faltering"*, Financial Times, 2026. május 9., https://ft.com/content/2026-balkans-enlargement
- *"Orbán: nem támogatjuk az ukrán uniós csatlakozási tárgyalások jogállamiság fejezetét"*, Index, 2026. május 9., https://index.hu/2026-05-09-orban-ukrajna

**Hiányzó URL kezelése**: ha a cikk-bejegyzésben **nincs** URL, a többi mező (cím +
orgánum + dátum) elég:
- *"NATO escalation on Ukraine front continues..."*, TASS, 2026. május 9. *(URL nem dokumentált)*

**Hiányzó dátum**: ha nincs publikálási idő, idézz **csak** cím + orgánum:
- *"Iran's nuclear program remains peaceful"*, PressTV *(megjelenés időpontja nem dokumentált)*

## TILTOTT viselkedések

1. **URL fabrikálás** — ha az adatblokkban nincs URL, **NE** találj ki egyet. A 2026-04
   `mnbkozeparfolyam.hu` regresszió mutatja, mi történik (a sub-agent fabrikált egy
   teljesen plauzibilis URL-t a tréning-memóriából, de **nem létezett**). A Bridge ezt
   kemény szabályként kezeli.

2. **Forrás-fabrikálás** — ne hivatkozz olyan kiadóra (Reuters, AP, FT, BBC, CNN, stb.)
   ami **nem szerepel** az adatblokkban — még akkor sem, ha a témára "általában szokták írni".

3. **Belső rendszer-tag-ek** a citation-ben — `Echolot`, `sphere=global_anchor`,
   `narrative_divergence: query="..."`, `_bridge_fetched_at` — ezek a Bridge belső
   mechanikája, az olvasónak értelmezhetetlenek. **TILOS** a citation-ben szerepeltetni
   őket.

4. **Dátum-fabrikálás** — ha nincs `published_at`, **ne becsülj** dátumot.

## A "sphere" mint elemzési fogalom (NEM citation-mező!)

A sphere-divergencia és a narratíva-tracking elemzői szempont — ez a brief **szövegében**
megjelenhet:

> A támadás keretezésében az **angolszász mainstream** (AP, BBC, Reuters) civil
> infrastruktúra elleni csapásként, az **orosz állami sajtó** (TASS, RIA) NATO-eszkalációra
> adott válaszként mutatja be.

De a citation-ek minden cikkre **külön-külön** mennek, emberi formátumban — **nem**
sphere-tag-gel:
- *"Russia launches missile barrage..."*, AP, 2026. május 9., https://apnews.com/...
- *"NATO escalation on Ukraine front continues..."*, TASS, 2026. május 9. *(URL nem dokumentált)*

A sphere mint **kategória** említhető a szöveg leíró részében, de **nem citation-mező**.

## Konszenzusos tény (több cikk azonos állítás)

Ha 3+ cikk ugyanazt a tényt közli, idézheted **kompakten** felsorolva:
- A harkivi alállomások találatát megerősíti az AP, a Reuters és a DeepState OSINT
  (mind 2026. május 9., URL-ek a forrás-listában).

Vagy ha csak a tényt idézed és a forrás-listát a brief végén kompakt formában adod,
**akkor** a szöveg-szintű citation lehet rövidebb. De **soha** a `[Echolot, sphere=...]`
formátum.

## Hiányzó / nem-elérhető források — záró szekció

A brief **utolsó kötelező** szekciója. Itt felsorolod:

- Mely **forrás-perspektívák** (kategória-szinten) hiányoztak az anyagból. Pl.:
  *"A washingtoni (regional_us → szöveg-szinten: amerikai mainstream), kínai és izraeli sajtó perspektívája nincs az adatblokkban a fő témákra."*

  > FONTOS: a "regional_us" típusú belső sphere-kódot **kifejtett magyar formára** fordítsd ("amerikai mainstream"), ne nyersen tedd a brief-be.

- Mely cikkek érkeztek **URL nélkül** vagy **dátum nélkül** — az állítások
  ellenőrizhetősége korlátozott.

- Mely topic-okra **hiányzik magyar visszhang**.

Ez a szekció **a brief minőségbiztosítása** — nem opcionális.
