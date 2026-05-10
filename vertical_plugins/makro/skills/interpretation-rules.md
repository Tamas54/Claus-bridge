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

### ECB Deposit Facility Rate (DFR) és eurozóna-pénzpiac — preset-ben friss

Az ECB-adatok **két csatornán** érkeznek a `hu_macro` presetben:

1. **ECB DFR (Deposit Facility Rate)** — DBnomics ECB FM datasetből
   (`B.U2.EUR.4F.KR.DFR.LEV`), az ECB SDW proxyn át, az utolsó kamatváltozás
   napjáig. Az utolsó adatpont az **aktuális DFR-szint**.
2. **Eurostat `ei_mfir_m geo=EA`** — havi friss eurozóna 3-hónapos money market
   rate, 1y / 5y / 10y euro yield-görbe, Maastricht-hozam.

Mindkettő ECB-eredetű; az 1. a **policy rate**, a 2. a **piaci kamatokat** adja.
Az ECB-re **ne** használj `web_search`-öt — a két preset-csatorna lefedi.

Citation:
> ECB Deposit Facility Rate, 2,75%, 2025. február 5-i hatállyal (forrás: ECB / DBnomics ECB FM)
> Eurostat money market 3-hónapos kamat, eurozóna, 2026. április — 2,18% (forrás: Eurostat ei_mfir_m)

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

## Egyéb stale/hiányzó adat

Ha a `get_policy_rates` `STALE!` flag-et ad VAGY az Eurostat-adat publikálási
késedelemben (HICP, une_rt_m): a brief-be **explicit kimondod**:
"az Eurostat HICP utolsó publikált hónapja YYYY-MM, a frissebb hazai adat csak KSH
`ara0039`-en érhető el (CPI), HICP-frissítés várható N hét múlva".

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
