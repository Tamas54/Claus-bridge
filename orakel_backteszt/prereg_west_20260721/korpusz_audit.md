# OPERATION G2-WEST — KORPUSZ-AUDIT (2026-07-21)

**Forrás:** ÉLES prod Echolot DB (railway ssh glistening-luck, `file:/data/echolot.db?mode=ro`),
mérés 2026-07-21 14:2x CET. Nyers számok: `korpusz_audit_raw.json`.
Dátum-kulcs: `COALESCE(published_at, fetched_at)`.

## 1. SZEGMENS × HÓNAP CIKKSZÁM (2026)

| Szegmens | 04 | 05 | 06 | 07 (07-21-ig) | Sűrű zóna kezdete | Sűrű-zóna volumen |
|---|---|---|---|---|---|---|
| **de** (lang='de') | 68 | 106 | 23 397 | 70 498 | **2026-06-21** | 1,7–2,8k/nap |
| **uk_** (en + source_id LIKE 'uk\_%') | 5 | 4 | 2 606 | 32 018 | **2026-06-21** | 140–340/nap; 07-13-tól ~2,7k/nap |
| **us_** (en + source_id LIKE 'us\_%') | 1 | 1 | 1 003 | 27 493 | **2026-06-22** | 110–155/nap; 07-13-tól ~2,5k/nap |
| en globál | 564 | 769 | 90 911 | 300 701 | 2026-06-17/21 | 6–10k/nap; 07-13-tól 14–25k/nap |

A töréspontok megfelelnek a snapshot-rétegzés élesítésének (EN nowcast-réteg + uk_/us_
prefix-kvóta, ill. a DELPHOI_CORPUS_LANGS de/en bővítés — commitok `5fdec79`, `0eb3dd9`)
és a QUELLENSCHLEUSE-nek (07-15). **2026-06-21 előtt a de/uk_/us_ réteg gyakorlatilag üres.**

## 2. ORSZÁG × GT-ABLAK FEDHETŐSÉG (fieldwork-igazított ablakokkal, G2-elv)

| GT-cella | Fieldwork (kb.) | Kellő korpusz-ablak | Fedhető? |
|---|---|---|---|
| US Conference Board 2026-06 (publ. 06-30, GT 91,2) | 06-01→06-18 | 05-15→06-18 | **NEM** (us_ ~0-5/nap) |
| US Michigan 2026-06 final (publ. 06-26, GT 49,5) | 05-26→06-22 | 05-15→06-22 | **NEM** (csak a farok 06-21/22 él) |
| **US Michigan 2026-07 prelim (publ. 07-17, GT 54,4)** | 06-24→07-14 | 06-15→07-14 | **IGEN** — az egyetlen fedhető, már-publikált nyugati GT → EZ A BACKTESZT |
| UK GfK 2026-06 (publ. 06-19, GT −23) | 06-01→06-13 | 05-15→06-13 | NEM |
| **UK GfK 2026-07 (publ. 07-24)** | ~07-01→07-15 | 06-21→07-15 | **IGEN — PREREG** (GT még nincs kint) |
| DE Eurostat BS-CSMCI/BS-PT-NY 2026-06 (GT −14,6/+37,4) | 06-01→06-20 | 05-15→06-14 | NEM |
| **DE Eurostat 2026-07 (publ. ~07-30)** | 07-01→~07-20 | 06-21→07-14 | **IGEN — PREREG** |
| **DE NIM/GfK Konsumklima 2026-08 (publ. 07-24)** | ~07-02→07-16 | 06-21→07-14 | **IGEN — PREREG** |
| **US CB 2026-07 (publ. 07-28) + Michigan 2026-07 final (publ. 07-31)** | júl. közepe–vége | 07-01→07-21 (mai) | **IGEN — PREREG** |

**Becsületes kimondás:** a 2026-06-os és korábbi nyugati GT-kre backteszt NEM fedhető —
a us_/uk_/de réteg a fieldwork-ablakokban üres volt. Backteszt-hamisítás helyett:
1 valódi backteszt (Michigan júl. prelim, pre-outcome ablakkal) + 5 előregisztrált jóslás.

## 3. MoM-IRÁNY PROXY-CELLA A BACKTESZTHEZ

A Michigan jún-final→júl-prelim IRÁNY teszteléséhez kell egy június-panel. Ablak:
**[2026-06-21 → 06-25]** (us_ sűrű kezdet → a jún-final publikáció, 06-26 ELŐTTI utolsó nap,
GT-echo-szivárgás kizárva). **Korpusz-kikötés:** ez a jún. fieldwork (05-26→06-22) FARKÁT
fedi csak — PROXY-cella, a jegyzőkönyvben így címkézve.

## 4. DE-RÉTEG CAVEAT

A `COUNTRY_PANEL_CONFIG['DE']`-nek nincs source_prefixes kulcsa → a de nyelvi korpusz
osztrák/svájci forrásokat is tartalmazhat. A korpusz-fájlban forrás-lista dokumentálva;
a prod panel-út ugyanígy működik, tehát a prereg a valós üzemi utat teszteli.
