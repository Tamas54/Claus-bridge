# Makró-idősor értelmezési szabályok

A nyers számok önmagukban félrevezetők. Ezek a szabályok kötelezőek minden
makró-szintézisben, amit a Bridge ad ki.

## ELSŐDLEGES SZABÁLY: statdata_macro a friss adatra

A 2026-05-11-i bővítés óta a StatData backend tartalmazza a magas-szintű
`statdata_macro(country, indicator)` tool-t, amely **garantáltan friss**
adatot ad ~50 országra × ~9 indikátorra. Belül 3-lépcsős fallback:
strukturált API (ECB/Eurostat/FRED/BIS) → hivatalos hivatal scrape (KSH,
MNB, Destatis, INSEE, ECB, BLS, Fed, BoE stb.) → brave_search.

**Az adatblokk FACTUAL CONTEXT-jébe a `hu_macro` preset bekér 8 magas-
szintű `statdata_macro` választ** (cpi/core/services/policy_rate/
unemployment/gdp HU-ra + cpi/policy_rate EA-ra). Mindegyik egy konkrét
számot + period + source_used + status (fresh/stale/missing) ad.

**Sorrend a friss adatok használatára:**
1. **Először** a FACTUAL CONTEXT-ben keresd a `statdata_macro` kimenetét.
   Ha `status: fresh`, használd a számot, idézd a `source_used`-et emberi
   formában (pl. "ECB Data Portal" / "MNB" / "BLS").
2. **Ha hiányzó/stale** indikátor van: hívd meg a `statdata_macro`-t
   közvetlenül (`statdata_macro(country='DE', indicator='cpi')`) — más
   country-paraméterre is működik (DE/FR/IT/ES/AT/PL/CZ/RO/EA/US/GB/JP/
   CN/KR/IN/BR/MX/TR és további ~30 ország).
3. **Csak végső esetben** (ha a router `missing`-et ad VISSZA), próbálj
   web_search/web_scrape-pel — de jelezd hogy a strukturált fallback is
   üres.

**Tilos** a FACTUAL CONTEXT régi alacsony-szintű idősorát (pl. ECB ICP
2025-12 stale) frissként idézni, ha a `statdata_macro` ugyanahhoz az
indikátorhoz `status: fresh` választ ad ugyanabban a contextben.

## YoY vs MoM

- **YoY** (year-over-year): a megfelelő hónap egy évvel korábbi értékéhez képest.
  Trendet mutat, kiszűri a szezonalitást. Inflációnál (CPI, HICP) a **standard**.
- **MoM** (month-over-month): az előző hónaphoz képest. Fordulópontokat mutat, de
  zajos. Annualizálva (×12) idézhető — de **mindig jelöld**, hogy annualizált.

Ha MoM-ot idézel inflációnál, **explicit** ki kell mondani.

## Inflációs mutatók hierarchiája

1. **Headline CPI / HICP** — mit fizet a háztartás (energia, élelmiszer benne).
2. **Maginfláció (core)** — energia + élelmiszer kihúzva, tisztább trend. Magyarországon
   az MNB **erre céloz** elsősorban.
3. **Szolgáltatás-infláció** — a bérinfláció ide gyűrűzik be leghamarabb. Ha az
   adatblokkban szerepel, idézz; ha nincs, ne találj ki.

A KSH `ara0045` (maginfláció) **bázis-indexet** ad, NEM közvetlen YoY %-ot. A 656,9
abszolút érték önmagában értelmetlen az olvasónak — YoY-ra konvertálni KÖTELEZŐ.

## GDP — mit lehet mondani belőle

- **Reál (chain-linked, CLV15)** = volumen, infláció kiszűrve. Növekedési ütem
  mérésére **ezt használd**.
- **Nominál** = pénzértékben. Adósság/GDP rátához OK, növekedés-elemzéshez nem.
- **Szezonálisan kiigazított (`SCA`)**: negyedéves elemzéshez kötelező.

Az Eurostat `namq_10_gdp` **abszolút** értékeket ad (millió EUR). YoY %-ot **te** számolsz:
`(idei_érték / előző_évi_érték - 1) × 100`.

## Munkanélküliség

- A `une_rt_m` ILO-definíció szerinti, EU-harmonizált. A magyar belső "regisztrált
  álláskereső" szám ettől eltér — ne keverd.
- Trend > pillanatnyi érték. Havi 0,1–0,2 pp ingadozás zaj.

## Árfolyam

- **MNB középárfolyam** (mnb_rates tool, `mode=current`) = **hivatalos napi referencia**.
  Egy adott napra fix érték (munkanapokon). Példa: 2026-05-08-ra EUR/HUF 355,14.
- **Élő piaci ár** (yfinance EURHUF=X) = intraday, a középárfolyamtól ±0,5% eltérhet.
- Az MNB középárfolyam **objektív** és **emberi-ellenőrizhető**: bárki megnézheti
  https://www.mnb.hu/arfolyamok-en (vagy ennek megfelelő hu-oldal).

Ha mindkettő van az adatblokkban, mindkettőt idézheted, jelölve melyik:
"MNB referencia 355,14 (2026-05-08), heti záró 353,66 (yfinance EURHUF=X)".

## Jegybanki kamatok

### ECB Deposit Facility Rate (DFR) — a presetben FRISS, web_scrape csak fallback

**2026-05-11-től** a `hu_macro` preset tartalmazza a `statdata_ecb` direkt tool-on
keresztül frissített ECB DFR-t (`FM/D.U2.EUR.4F.KR.DFR.LEV`), és a
`statdata_policy_rates(countries='XM,...')` is automatikusan overlay-eli a napi
ECB DFR-t — vagyis az adatblokkban **az aktuális napi értékkel** szerepel.

**Sorrend:**
1. Az adatblokkban keresd az `ECB Deposit Facility Rate` mezőt
   (`get_policy_rates → ecb_direct.deposit_facility_rate` overlay,
   vagy `get_ecb_data FM/D.U2.EUR.4F.KR.DFR.LEV` direkt válasz).
   A `period` a legutolsó publikálási nap.
2. Ha az adatblokk valamiért NEM tartalmazza (preset-hiba): `web_scrape` a
   `ecb.europa.eu/stats/policy_and_exchange_rates/key_ecb_interest_rates/html/`
   kanonikus URL-re.
3. A DBnomics ECB FM 2025-02-05-i 2,75%-os értéket **NE** idézd frissként —
   az stale.

Citation a brief-be:
> ECB Deposit Facility Rate, 2,00%, 2026. május 11. (forrás: ECB Data Portal)

A **piaci kamatok** (3-hónapos money market, 1y/5y/10y euro yield) ezzel
szemben az Eurostat `ei_mfir_m geo=EA` sorozatból érkeznek frissen — ezek
tisztán különbözőek a policy rate-tól (diskrét döntés vs folyamatos piac).

### MNB irányadó kamat — preset + flash + web_search

A `get_policy_rates` BIS-adata HU-ra **gyakran stale** (2-12 hónap késleltetéssel),
az adatblokk explicit `STALE!` flaggel jelzi. Az `irt_st_m` Day-to-day money market
rate Eurostat-ról **proxy** (~10 bps eltérés), nem hivatalos kamat.

**Sorrend HU jegybanki kamatra:**
1. Először az adatblokk KSH flash-találatait nézd
   (`statdata_flash(query='alapkamat'/'kamat', source='ksh')` — a 2026-05-11-i
   preset alapból bekér `query='fogyasztói árak'`, `query='munkanélküliség'`,
   `query='GDP'` flash-eket; az MNB-rátára kérj kiegészítő flash-t, ha kell).
2. Ezután a `statdata_policy_rates(countries='HU')` BIS-érték — ha `stale=false`,
   használd; ha `stale=true`, csak kontextusként idézd (pl. „BIS WS_CBPOL HU
   legutolsó adatpontja: 6,5% (2025-06)").
3. Ha az adatblokk még nem tartalmaz friss MNB-döntést:
   `web_search(query="mnb irányadó kamat <HÓNAP> <ÉV> monetáris tanács")`,
   majd `site:mnb.hu` szűrés, majd `web_scrape` a hivatalos közleményre.

Web-citation formátuma:
> MNB Monetáris Tanács, irányadó kamat 6,50%, 2026. április 29. (forrás: mnb.hu/monetaris-politika/...)

Ha sem flash, sem web_search nem hoz friss eredményt, "(MNB-honlap nem
elérhető a lekérdezés időpontjában)" + "Hiányzó / nem elérhető források"
szekció. **Tilos** fejből kitalálni az MNB-ratet.

## Szolgáltatás-infláció — a presetben FRISS ECB-sorozattal

A KSH STADAT a fogyasztói **szolgáltatás-inflációt** havi szinten **nem
publikálja külön táblában** (csak `ara0002` éves bontás). **2026-05-11-től**
a `hu_macro` preset tartalmazza a `statdata_ecb(dataset='ICP',
key='M.HU.N.SERV00.4.ANR')` havi sorozatot — ez az ECB ICP STS_INSTITUTION=4
(Eurostat-sourced) HU szolgáltatás HICP YoY%, havi bontásban, 2026-os
adatokkal együtt.

**Sorrend:**
1. Először az ECB ICP `M.HU.N.SERV00.4.ANR` sorozatból (adatblokkban közvetlenül).
2. Ha nincs vagy gyanúsan régi: `statdata_flash(query='szolgáltatás infláció', source='ksh')` ill. `query='fogyasztói árak'` — a KSH gyorstájékoztatóban néha bontásban szerepel.
3. Csak végső esetben: `web_search` MNB Inflációs Jelentésre.

Citation:
> ECB ICP, HU HICP szolgáltatások, 2025. december — +7,9% YoY (forrás: ECB Data Portal)

(NEM `get_ecb_data` tool-nevet vagy series-key-t mint citation-szöveget — az
intézmény és a tartalmi név emberi formában a fő szövegbe.)

## HU HICP / munkanélküliség 2026-os adatok — preset + flash

Az Eurostat API időnként csonkolja a `prc_hicp_manr` / `une_rt_m` HU-adatot
500 soros limit miatt — a feed 2025-12-nél megáll. **2026-05-11-től** a preset:

- `statdata_ecb(dataset='ICP', key='M.HU.N.000000.4.ANR')` — HU HICP overall
  havi YoY%, 2026-os adatokkal együtt (az ECB ICP nem érintett a csonkolásban).
- `statdata_flash(query='fogyasztói árak', source='ksh')` — a KSH által
  publikált legfrissebb havi CPI hír (1–3 nappal megelőzi az ECB-t is).
- `statdata_flash(query='munkanélküliség', source='ksh')` — HU munkanélküliség
  flash (havi `une_rt_m` Eurostat-csonkolás esetén ez a kerülőút).

**Tilos** az adatblokk csonkolt 2025-12-es Eurostat-értékét frissként idézni,
ha a fenti két forrás 2026-os értéket ad. A flash release headline-szám
mellé kötelező a publikálási dátum.

## Egyéb stale/hiányzó adat — KÖTELEZŐ 3-lépcsős keresési minta

Ha az adatblokkban valami **hiányzik** vagy `[STALE!]` flag-et kapott (BIS
WS_CBPOL, régi DBnomics-adatpont, publikálási késleltetés), **NE elégedj meg**
azzal hogy "nem elérhető". A flash press release-ek és a hivatalos közlemények
**léteznek HTML formában** — a `web_scrape` tool JS-rendered SPA-kat is le tud
húzni.

### KÖTELEZŐ 3-lépcsős keresési minta

A `<HÓNAP>` és `<ÉV>` mindig az AKTUÁLIS lekérdezési időpontból (temporal
directive), NE fix dátum.

**Lépcső 1 — általános keresés:**
- `web_search(query="<topic> <HÓNAP> <ÉV>")`
- Pl. `query="mnb irányadó kamat 2026 monetáris tanács"`,
  `query="ecb deposit facility rate 2026"`,
  `query="eurostat hicp flash april 2026"`

**Lépcső 2 — KÖTELEZŐ site-szűrés ha nem volt hivatalos találat:**
Ha az első 5 találatban nincs **hivatalos domain** (`mnb.hu`, `ec.europa.eu`,
`ecb.europa.eu`, `ksh.hu`, `bis.org`, `imf.org`, `oecd.org`),
**KÖTELEZŐ** ismételni site-szűréssel:
- `web_search(query="<topic> site:mnb.hu")`
- `web_search(query="<topic> site:ecb.europa.eu")`
- `web_search(query="<topic> site:ec.europa.eu")`

A másodlagos forrás (rankia.hu, economx.hu, index.hu, portfolio.hu) **NEM
elfogadható** addig, amíg a site-szűréses hivatalos keresést **NEM** próbáltad meg.

**Lépcső 3 — `web_scrape` a hivatalos URL-re:**
A találati listából a **hivatalos domain-en** lévő URL-t válaszd. `web_scrape`
azt az URL-t — a press release / közlemény markdown-szövegét fogja visszaadni.
Onnan idézd a konkrét számot a brief-ben.

### Mikor mehet "hiányzó / nem elérhető" a brief-be

**CSAK** akkor minősítsd hiányzónak, ha **MIND a 3 lépcső** sikertelen volt:
- általános `web_search` nem hozott releváns találatot
- ÉS site-szűréses keresés is üres
- ÉS a `web_scrape` a kanonikus hivatalos URL-en is sikertelen

A "Hiányzó / nem elérhető források" szekcióba **kifejezetten** ezt a 3-lépcsős
próbálkozást rögzítsd:

> ECB betéti kamat 2025-02-05 utáni: `web_search(deposit facility rate 2026)`
> nem hozott friss; `site:ecb.europa.eu` keresés ECB-press-release-listát adott;
> `web_scrape(ecb.europa.eu/press/pr/date/2026/...)` 404 — az ECB tavasszal
> még nem publikált új DFR-döntést, a 2,75% (2025-02-05) marad érvényben.

Ez **konkrét audit-trail**, nem általános "publikálási rend miatt nincs"
mentegetőzés.

### TILTOTT viselkedések

- Másodlagos forrást (rankia, economx, index, portfolio) idézni **anélkül**
  hogy a hivatalos `site:`-szűréses keresést megpróbáltad volna.
- "Webes kereséssel nem érhető el" jellegű általánosítás konkrét próbálkozás
  nélkül.
- "Strukturált API-ban nem szerepel" mint indoklás, ha a `web_scrape` még
  nem volt használva a hivatalos newsroom-ra.

## Anti-hallucination

1. Ha egy szám nincs az adatblokkban → "(adat nem elérhető)".
2. Ha a szám van, de a dátum hiányzik → idézd a számot, jelöld: "(időpont nem dokumentált)".
3. Ne keverj össze két forrást (KSH CPI ≠ Eurostat HICP) — külön mondatban idézd őket.
4. Ne használj "körülbelül 3,5%" formát, ha 3,3% van az adatblokkban.

## Citation — emberi formátumban

**Sablon:** `<intézmény>, <tartalmi név>, <adat dátuma>` (+ URL ha az adatblokkban van).

Példák:
- KSH, fogyasztói árindex, 2026. április — +2,1%
- Eurostat, HICP, eurózóna, 2025. december — +2,0%
- MNB középárfolyam EUR/HUF, 2026. május 8. — 355,14

**TILOS:**
- `get_ksh_stadat`, `_bridge_fetched_at`, `prc_hicp_manr`, `geo=HU` — belső tag-ek.
- A Bridge által lekérés időpontját adat-dátumként idézni.
- Olyan URL-t a citation-be tenni, ami nincs az adatblokkban.
