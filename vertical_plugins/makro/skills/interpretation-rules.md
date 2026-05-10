# Makró-idősor értelmezési szabályok

A nyers számok önmagukban félrevezetők. Ezek a szabályok kötelezőek minden
makró-szintézisben, amit a Bridge ad ki.

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

### ECB Deposit Facility Rate (DFR) — KÖTELEZŐ web_scrape ecb.europa.eu-ról

**Bridge-tool-on át NINCS friss ECB DFR.** A DBnomics ECB FM idősor
2025-02-05-én megáll (2,75%), holott az ECB 2025 közepén tovább csökkentett
2,00%-ra. A Bridge sub-agentnek **TILOS** a DBnomics-i 2,75%-ot mint "friss"
adatot idéznie — **kötelező** web_scrape-szel pótolni hivatalos ECB-forrásról:

**3-lépcsős keresés ECB-rate-re:**
1. `web_search(query="ecb deposit facility rate <ÉV>")` → találati lista
2. `web_search(query="deposit facility rate site:ecb.europa.eu")` — site-szűrt
3. `web_scrape(url=ecb.europa.eu/.../key_ecb_interest_rates URL)` → markdown

Az `ecb.europa.eu/stats/policy_and_exchange_rates/key_ecb_interest_rates/html/`
oldal **kanonikus**: tartalmazza a Deposit Facility Rate, MRO Rate, Marginal
Lending Facility Rate aktuális szintjét és az utolsó döntés dátumát.

Citation a brief-be:
> ECB Deposit Facility Rate, 2,00%, 2025. mid-óta változatlan (forrás: ecb.europa.eu, web_scrape, <kanonikus URL>)

A **piaci kamatok** (3-hónapos money market, 1y/5y/10y euro yield) ezzel
szemben a presetből érkeznek **frissen**:

- **Eurostat `ei_mfir_m geo=EA`** — havi friss eurozóna money market + yield-görbe

Ez tisztán **különböző** mint a policy rate — a policy rate (DFR/MRO/MLF)
diskrét döntésekkel változik, a piaci kamatok folyamatosan.

### MNB irányadó kamat — KÖTELEZŐ web_search pótlás

A `get_policy_rates` BIS-adata **gyakran stale** (2-12 hónap késleltetéssel) — az
adatblokk explicit `STALE!` flaggel jelzi. Az `irt_st_m` (Day-to-day money market
rate) Eurostat-ról **proxy** a magyar jegybanki kamatra (~10 bps eltérés), de **NEM**
azonos a hivatalos MNB irányadó kamattal. Közvetlen friss MNB-rate-tool **nincs**
a Bridge `hu_macro` presetjében (DBnomics-on sem érhető el direkten).

**Szabály**: ha a heti brief MNB irányadó kamatot említ ÉS az adatblokkban csak stale
BIS van VAGY csak `irt_st_m` proxy szerepel, **kötelező** a `web_search` tool-t
használni a friss MNB döntésre. A web-citation formátuma:

> MNB Monetáris Tanács, irányadó kamat 6,50%, 2026. április 29. (forrás: mnb.hu/monetaris-politika/...)

Ha a `web_search` nem hoz friss eredményt, a citation-be írj "(MNB-honlap nem
elérhető a lekérdezés időpontjában)" és a "Hiányzó / nem-elérhető források"
szekcióba flaggeld. **Tilos** fejből kitalálni az MNB-ratet.

## Szolgáltatás-infláció — KÖTELEZŐ web_search pótlás

A KSH STADAT a fogyasztói **szolgáltatás-inflációt** havi szinten **közvetlenül NEM
publikálja külön táblában** (csak `ara0002` éves bontás, illetve archív B2B/B-All
táblák). Az MNB Inflációs Jelentés viszont rendszeresen közli havi szolgáltatás-
infláció bontást.

**Szabály**: ha a brief-be szolgáltatás-infláció kell ÉS az adatblokkban nincs friss
havi érték, használd a `web_search`-öt MNB Inflációs Jelentés vagy KSH gyorsjelentés
forrással. Web-citation formátuma:

> MNB Inflációs Jelentés, szolgáltatás-infláció (havi, YoY), 2026. március – 7,3% (mnb.hu/...)

Ha a web_search nem talál friss adatot, "(forrás nem elérhető)" + "Hiányzó forrás"
flag.

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
