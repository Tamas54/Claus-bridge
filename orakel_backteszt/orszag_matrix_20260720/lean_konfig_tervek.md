# OPERATION PYTHIA — G0c: lean-konfig TERVEK az új országokra (2026-07-20)

**Státusz: TERV — a `plugins/delphoi.py` NEM módosult.** A formátum a meglévő
`COUNTRY_PANEL_CONFIG` media-elemét követi: `(label, súly, lean_bucket)` — a bucket
explicit. Elv (memória-doktrína): **perzisztens alignment** + a közmédia
**"mindenkori kormány"** deriváltként jelölve (kormányközeli címke TILOS, elavul);
kivétel, ahol a közmédia szerkezetileg NEM kormány-derivált (DE, UK — lásd ott).

TÉNY-állapot a delphoi.py-ban (audit, 2026-07-20): **HU, PL, FR, IT** (nowcast-grade)
**+ CZ, PT** (FG-grade) konfig LÉTEZIK — a parancs "HU/PL/FR/IT részben kész"
várakozásánál több van kész. Újként kell: **ES, DE, EN(UK), RO**; CZ/PT-hez
kiegészítés kell (korpusz-rés + priming-frissítés).

A politikai tény-állítások (kormány 2026) a 2026-01-es tudás-horizontig verifikáltak;
**élesítés előtt a priming-mondatokat friss forrásból újra kell ellenőrizni.**

---

## ES — ÚJ konfig (terv)

Kormány 2026: Sánchez (PSOE–Sumar koalíció). Közmédia: RTVE — mindenkori-kormány derivált.

```python
"ES": {
    "lang": "es",
    "priming": ("SITUACIÓN (2026): gobierna Pedro Sánchez (coalición PSOE–Sumar); "
                "oposición PP y Vox. RTVE tiende a apoyar al gobierno de turno."),
    "dims": {  # Eurostat: demo_pjan + edat_lfse_03 (ED5-8 25-64: 42,4% — 2025, verifikált) + edat_lfs_9913 degurba
        "age": [("18-29", 0.14), ("30-39", 0.15), ("40-49", 0.19), ("50-59", 0.18), ("60+", 0.34)],
        "settlement": [("Madrid/Barcelona/gran ciudad", 0.28), ("ciudad media", 0.27), ("ciudad pequeña", 0.22), ("rural", 0.23)],
        "edu": [("básica", 0.32), ("secundaria/FP", 0.26), ("superior", 0.42)],
    },
    "media": [
        ("sigue medios progresistas (El País, elDiario.es, Cadena SER, laSexta)", 0.26, "baloldali"),
        ("sigue medios conservadores (El Mundo, ABC, La Razón, OKDiario, COPE)", 0.26, "jobboldali"),
        ("sigue RTVE (apoya al gobierno de turno, ahora la coalición PSOE)", 0.16, "közmédia"),
        ("consume contenido político en redes sociales", 0.20, "közösségi"),
        ("apenas sigue las noticias políticas", 0.12, "alig"),
    ],
},
```

Echolot-korpusz jel (prod, 30 nap): az es cikk-lean **jobbra húz** (center_right 30 867
vs center_left 3 381 cikk) — a korpusz-építésnél (build_country_corpus) balansz-súlyozás
vagy forrás-kvóta kell, különben a nowcast jobb-frame-túlsúlyú inputot kap.

---

## DE — ÚJ konfig (terv)

Kormány 2026: Merz (CDU/CSU–SPD). Közmédia: ARD/ZDF/DLF — **tanácsi kormányzású,
szerkezetileg NEM kormány-derivált** → a bucket "közmédia", de a priming
"intézményi"-ként írja le (mint FR-nél a France Télévisions), NEM "mindenkori kormány".

```python
"DE": {
    "lang": "de",
    "priming": ("LAGE (2026): Bundeskanzler Friedrich Merz (CDU/CSU–SPD-Koalition) "
                "seit Mai 2025; stärkste Oppositionskraft die AfD. Der öffentlich-"
                "rechtliche Rundfunk (ARD/ZDF) ist institutionell, gremiengesteuert."),
    "dims": {  # Eurostat verifikált: demo_pjan DE 2025 = 83 577 140; edat_lfse_03 ED5-8 25-64: 35,4% (2025); degurba-bontás edat_lfs_9913 (2024: Cities 39,1 / Towns 30,9 / Rural 28,0)
        "age": [("18-29", 0.14), ("30-39", 0.16), ("40-49", 0.15), ("50-59", 0.17), ("60+", 0.38)],
        "settlement": [("Großstadt", 0.32), ("Mittelstadt/Vorort", 0.42), ("ländlich", 0.26)],
        "edu": [("ohne Abitur/Lehre", 0.30), ("Abitur/Fachschule", 0.35), ("Hochschule", 0.35)],
    },
    "media": [
        ("folgt linksliberalen Medien (SZ, Spiegel, Zeit, taz)", 0.26, "baloldali"),
        ("folgt konservativen/rechten Medien (FAZ, Welt, Bild, NIUS)", 0.24, "jobboldali"),
        ("folgt dem öffentlich-rechtlichen Rundfunk (ARD, ZDF, DLF)", 0.20, "közmédia"),
        ("konsumiert politische Inhalte in sozialen Medien", 0.18, "közösségi"),
        ("verfolgt politische Nachrichten kaum", 0.12, "alig"),
    ],
},
```

Echolot-korpusz jel: de lean-eloszlás egészséges (L 11 182 / R 6 959 / center 48 422),
de az unknown 24% — a de források lean-címkézése (40→111 forrás a prod-on) hiányos.

---

## EN (UK) — ÚJ konfig (terv)

Kormány 2026: Starmer (Labour, 2024-07 óta). Közmédia: BBC — intézményi (Royal
Charter, nem kormány-derivált). FIGYELEM: a G0c-verdikt szerint **UK jelenleg NEM
IGAZOLHATÓ** (GT-rés — lásd matrix.md), ez a terv a fiókba készül arra az esetre,
ha lesz gépi GfK/ONS-út.

```python
"UK": {
    "lang": "en",   # korpusz-szűrés KELL: source_id LIKE 'uk_%' (a nyelvi 'en' korpusz globális!)
    "priming": ("SITUATION (2026): Keir Starmer's Labour government (since July 2024); "
                "main opposition Conservatives, with Reform UK rising. The BBC is "
                "institutional, governed by Royal Charter."),
    "dims": {  # Eurostat NEM szolgál friss UK-t → ONS kellene (nincs a statdata-rétegen); az alábbi ONS-közeli durva becslés
        "age": [("18-29", 0.18), ("30-39", 0.17), ("40-49", 0.16), ("50-59", 0.16), ("60+", 0.33)],
        "settlement": [("London/major city", 0.30), ("town/suburban", 0.45), ("rural", 0.25)],
        "edu": [("no degree", 0.55), ("degree or higher", 0.45)],
    },
    "media": [
        ("follows left-liberal media (Guardian, Mirror, Independent)", 0.24, "baloldali"),
        ("follows right-leaning media (Telegraph, Times, Daily Mail, Sun, GB News)", 0.28, "jobboldali"),
        ("follows the BBC", 0.22, "közmédia"),
        ("consumes political content on social media", 0.16, "közösségi"),
        ("hardly follows political news", 0.10, "alig"),
    ],
},
```

Echolot-korpusz jel: a `uk_` forrás-szegmens cikk-szinten meglepően kiegyensúlyozott
(L 8 370 vs R 7 970 cikk/30 nap, 51 aktív forrás), de a jobb-bulvár forrás-számban
alul van (2 right-forrás regisztrálva) → Mail/Sun/Express/GB News onboarding ajánlott.

---

## RO — ÚJ konfig (terv)

Kormány 2026: Bolojan-koalíció (PNL–PSD–USR), elnök Nicușor Dan (2025-05-től).
Közmédia: TVR — intézményi, gyenge súlyú. FIGYELEM: az RO lean-tengely nem tisztán
bal/jobb — a domináns törés **mainstream-EU-párti vs szuverenista-populista**; a
bucket-címkék ezt kódolják, a L/C/R-re vetítés (no-gov-bucket doktrína: gov→R) csak
másodlagos leképezés.

```python
"RO": {
    "lang": "ro",
    "priming": ("SITUAȚIA (2026): guvernează coaliția Bolojan (PNL–PSD–USR); președinte "
                "Nicușor Dan. Opoziția principală AUR (suveranistă). TVR este instituțional."),
    "dims": {  # Eurostat verifikált: edat_lfse_03 ED5-8 25-64: 19,5% (2025) — a legalacsonyabb a jelöltek közt
        "age": [("18-29", 0.15), ("30-39", 0.17), ("40-49", 0.19), ("50-59", 0.17), ("60+", 0.32)],
        "settlement": [("București/oraș mare", 0.26), ("oraș mediu/mic", 0.28), ("rural", 0.46)],
        "edu": [("gimnazial/profesional", 0.45), ("liceu", 0.35), ("superior", 0.20)],
    },
    "media": [
        ("urmărește presa liberală/pro-europeană (G4Media, HotNews, Libertatea, Digi24)", 0.24, "baloldali"),
        ("urmărește presa suveranistă/populistă (România TV, Realitatea Plus, Antena 3 CNN)", 0.26, "jobboldali"),
        ("urmărește TVR", 0.14, "közmédia"),
        ("consumă conținut politic pe rețele sociale (Facebook/TikTok)", 0.24, "közösségi"),
        ("aproape nu urmărește știrile politice", 0.12, "alig"),
    ],
},
```

Echolot-korpusz jel: **kritikus rés** — 4 ro forrás, 182 cikk/nap, 53% unknown-lean.
Konfig önmagában nem elég: előbb scrape-bővítés (fenti forrás-jelöltek onboardingja).

---

## CZ — KIEGÉSZÍTÉS (konfig LÉTEZIK, FG-grade)

- **Priming-frissítés**: a jelenlegi "po volbách 2025" placeholder → konkrét: Babiš/ANO-
  kormány a 2025-10 választás után (élesítés előtt friss forrásból ellenőrizni).
- **Korpusz-rés**: az Echolot cs korpuszban NINCS bal-lean forrás (L=0 cikk/30 nap).
  Jelöltek: Právo/Novinky.cz (center_left), A2larm, Deník Referendum (left), plusz
  Echo24, Lidovky a jobb-közép mélyítésére. 17 aktív forrás → cél 60.
- Fan-out pálya BIZONYÍTOTT: `corpus_cz_*.json` + `cz_nowcast_*.json` (3 időablak) él.

## PT — KIEGÉSZÍTÉS (konfig LÉTEZIK, FG-grade)

- **Priming-frissítés**: elnökváltás 2026-01 (Marcelo mandátuma lejárt; az új elnök
  nevét élesítés előtt ellenőrizni) + Montenegro AD-kormány.
- **Korpusz-rés**: 5 forrás, MIND center — lean-monokultúra. Jelöltek: Público
  (center_left), Observador (center_right), Correio da Manhã (népi jobb), Expresso,
  SIC Notícias, RTP. Addig a fan-out korpusz (`corpus_pt_*.json` minta: ~20 kurált
  egysoros hír-állítás / időablak) marad az út — 3 ablakon bizonyított.

---

## Hiányzó anchor-készletek (SSR) — a konfigokon túl

REGARD: hu/pl/fr/it van → **es/de/en/ro hiányzik**. PRICE: HU/CZ/PT/PL (ssr.py) +
fr/it (delphoi PRICE_EXTRA) → **es/de/en/ro hiányzik**. Az új országok élesítéséhez
országonként 2×5 anchor-mondat kell (natív, a meglévő minták fordítás-adaptációja —
statikus szöveg, a static-copy-no-MT elv szerint kézzel írva).
