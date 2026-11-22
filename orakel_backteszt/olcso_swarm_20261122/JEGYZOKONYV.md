# OLCSÓ SWARM — a döntés-szimulátor panelmodellje (2026-11-22)

**Kérdés (Kommandant):** „A delphoi panel túl drága AI-t használ (Hy3). Olcsóbb modell is elég a swarm-előrejelzéshez.”
Kiegészítés: a költség az elsődleges szempont, a minőségkapu áll; **közmédia-kapu**: a közmédia ma a Tisza-kormányt
támogatja, a modell ezt kövesse, ne a régi térképet.

**Pre-regisztráció:** `91a35c9`, `2b569d3` (a mérőgépek és a közmédia-kapu szabálya), `01c7ccf` (az Echolot-bank) —
mind a futások ELŐTT. A HU-backteszt a `../run_panel.py` (06-15) változatlan promptjával és pontozásával fut.

## 1. Ár és egy szimulátor-panel valós költsége

Egy szimulátor-panel: n=60 persona × k=3 minta = 180 hívás (concept-lépés). A költség a mért tokenszámból és a
SiliconFlow-listaárból (2026-11-22) jön. A két mérés: az FG-skála (5 lépés × 30 persona) és a közmédia-kapu
(3 hosszabb lépés × 90 persona), mindkettő 180 hívásra vetítve.

| Modell | Ár be / ki ($/M) | Panel (180 hívás) | A Hy3-hoz képest |
|---|---|---|---|
| tencent/Hy3 | 0,132 / 0,528 | **1,22–1,24 ¢** | — |
| deepseek-ai/DeepSeek-V4.1-Flash | 0,15 / 0,60 | **0,98–1,03 ¢** | −16…−21% (rövidebb válasz, tömörebb tokenizáló) |
| Qwen/Qwen3-30B-A3B-Instruct-2507 | 0,09 / 0,30 | 0,59–0,62 ¢ | −50% |
| Qwen/Qwen3-14B | 0,07 / 0,28 | 0,55–0,57 ¢ | −55% |
| Qwen/Qwen3.5-9B | 0,10 / 0,15 | 0,42–0,45 ¢ | −65% |
| Qwen/Qwen3-8B | 0,06 / 0,06 | 0,25–0,28 ¢ | −79% |
| openai/gpt-oss-120b | 0,05 / 0,45 | 2,06 ¢ | +66% (a gondolkodás nem kapcsolható ki) |
| openai/gpt-oss-20b | 0,04 / 0,18 | 1,49 ¢ | +20% (ugyanaz) |

`tencent/Hy4-preview` létezik az SF-en, de nincs az árlistán; a szimulátor élesben a Hy3-at hívja
(`DELPHOI_FG_MODEL=tencent/Hy3`, Railway).

## 2. Minőség: FG-skála (élő DELPHOI-prompt + SSR, 5 lépés × 30 persona, k=1)

| Modell | Kitöltés | Lépések szórása (max−min) | Átlag-eltolás a Hy3-hoz | Spearman (lépés / szegmens) |
|---|---|---|---|---|
| Hy3 | 100% | 1,52 | — | — |
| V4.1-Flash | 100% | 1,52 | +0,30 (+7,5 pont a 0–100-as skálán) | 0,9 / 0,9 |
| Qwen3-30B-A3B | 100% | 1,14 | +0,15 (+3,9) | 1,0 / 0,9 |
| Qwen3-14B | 100% | **0,29 — lapos** | −0,21 (−5,3) | 0,6 / −0,1 |
| Qwen3.5-9B | 100% | **0,31 — lapos** | +0,11 (+2,8) | 0,7 / 0,3 |
| Qwen3-8B | 100% | **0,39 — lapos** | +0,02 (+0,4) | 0,5 / 0,2 |
| gpt-oss-120b | 100% | 1,39 | +0,61 (+15,4) | 0,9 / 0,3 |

A lapos modellek mindenre ugyanazt a langyos választ adják: a panel nem különbözteti meg a lépéseket.

## 3. Közmédia-kapu (3 Tisza-kormányzati lépés × 90 persona; hely = (K−J)/(B−J), < 0,5 = régi térkép = kizáró)

| Modell | Baloldali | Jobboldali | Közmédia | Hely (összevont) | Verdikt |
|---|---|---|---|---|---|
| Hy3 | 3,19 | 2,29 | 2,78 | 0,54 (az euró-lépésen **0,26**) | határeset: az eurónál a régi térkép |
| **V4.1-Flash** | 3,19 | 2,45 | **3,21** | **1,04** | **új térkép** (mindhárom lépésen ≥ 0,83) |
| Qwen3-30B-A3B | 3,17 | 2,98 | 2,63 | −1,79 | **KIZÁRÓ**: az eurónál a jobboldalit teszi a legpozitívabbra |
| Qwen3.5-9B | 2,62 | 2,27 | 2,35 | 0,21 | **KIZÁRÓ**: régi térkép |
| Qwen3-14B | 2,06 | 2,14 | 2,11 | — | nem választja szét a táborokat |
| Qwen3-8B | 2,26 | 2,27 | 2,27 | — | nem választja szét a táborokat |

A Hy3 euró-lépésen a Kommandant próbafutását ismétli: átlag 2,63, a közmédia-szegmens a jobboldali mellett.

## 4. HU választás előtti vak backteszt (N=80/seed, a 06-15-i gép)

Viszonyítás: hivatalos rés **+14,6**; a panel által modellezett részhalmaz (belföldi, két-párti, digitális) **+20,3…+22,7**.
A cutoff-szonda szerint a 2026-04-i választás minden mért modellnél CLEAN (`../cutoff_szonda_20261122/CUTOFF_JEGYZOKONYV.md`).

| Modell | Seed 1 | Seed 2 | Seed 3 | Átlag ± szórás | Hiba a +14,6-hoz | Hiba a +20,3…+22,7-hez | Parse (szigorú) | API-hiba | Költség |
|---|---|---|---|---|---|---|---|---|---|
| V4.1-Flash | +16,5 | +17,5 | +12,5 | **+15,5 ± 2,2** | +0,9 | −4,8…−7,2 | 100% | 0 | 0,81 $ |
| Hy3 (ma) | −7,5 | −5,0 | −17,5 | **−10,0 ± 5,4** | −24,6 | −30,3…−32,7 | 96–100% | 0 | 0,80 $ |
| Hy3 (07-20) | +15,0 | — | — | — | +0,4 | −5,3…−7,7 | — | — | — |
| V4-Flash (06-15) | +22,9 | +18,9 | — | +20,9 | +6,3 | −1,8…+0,6 | — | — | — |
| Qwen3-30B-A3B | −66,7* | — | — | — | −81,3 | −87…−89 | 60% | 40% | 0,11 $ |

\* 32/80 hívás 4 próbálkozás után is elhalt; a 48 válaszolóból 40 Fidesz. A Hy3 07-20-i +15,0-ja ugyanazzal a
seeddel ma −7,5: a Hy3 viselkedése azóta megváltozott (a mai 3 seed szórása 5,4, a különbség 25 pont).

A többi Qwen és a gpt-oss nem kapott HU-futást: az FG-skála vagy a közmédia-kapu már előtte kizárta őket.

**G2-cella (IT-CCI 2026-06): nem futott.** A 07-20-i protokoll OpenAI-embeddinget használ
(`text-embedding-3-small`), és a Bridge `.env`-jében lévő OpenAI-kulcs 401-et ad. SF-embeddinggel futtatható, de
akkor a Hy3-cellát is újra kell futtatni, mert a 07-20-i számok mással nem vethetők össze.

## 5. Döntés

- **Érdemben olcsóbb modell nem ment át a kapun.** A Qwen-család 50–79%-kal olcsóbb, de vagy lapos, vagy a
  közmédia-kapun bukik (régi térkép, illetve összekevert táborok).
- **Nyertes: `deepseek-ai/DeepSeek-V4.1-Flash`** — 16–21%-kal olcsóbb panelenként, a közmédia-kaput egyedül
  teljesíti tisztán, a HU-backteszten stabil (+15,5 ± 2,2), és ugyanúgy szétválasztja a lépéseket, mint a Hy3.
  Skála-eltolás a Hy3-hoz: +0,2…+0,3 SSR-pont (+5…+7,5 a 0–100-as skálán), ezért külön panel-verzió kell
  (`delphoi.fg_panel_version`).
- Bekötve: `SZIMULATOR_PANEL_MODEL` (alap: V4.1-Flash), `delphoi.process_job(model=…)`. A nyilvános DELPHOI-termék
  (`DELPHOI_FG_MODEL`) és a ledger nem változik.
- **Javaslat (nincs átállítva):** a `DELPHOI_FG_MODEL` is váltson V4.1-Flashre, `FG_PANEL_VERSION`-emeléssel és
  ledger-korszakhatárral, mert a Hy3 ma a HU-backteszten a régi Fidesz-priort hozza (−10,0, a 07-20-i +15,0 nem ismételhető), és az euró-lépésen a közmédiát
  a jobboldalhoz teszi.
- **Javaslat a HU-konfighoz (nincs átírva):** a FOGÁS-prompt (`_fg_prompt`) a `priming` szöveget NEM kapja meg (csak a
  nowcast-prompt), így a persona a kormányváltásról csak a média-címkéből tud. Érdemes a primingot az FG-promptba is
  betenni (panel-verzió-emeléssel), és a közmédia-címkét konkretizálni, pl. „közmédiát követ (M1, Kossuth — a Tisza-kormány
  megalakulása óta a kormány vonalát közvetíti)”.
- A backteszt-módnak: a V4.1-Flash 2026-01-től tiszta, a Hy3 2025-08-tól. Korábbi eseményre a Hy3 tisztább,
  de a fenti prior-hiba miatt kérdéses.

## Költés

SiliconFlow 1,89 $ (token × listaár; ebből a HU-backteszt 1,72 $), Anthropic 0,51 $ (a cutoff-szonda, Batches).

## Fájlok

`run_fg_skala.py`, `run_kozmedia.py`, `run_hu_panel.py`, `run_g2_model.py`; eredmények: `fg_skala_*.json`,
`kozmedia_*.json`, `hu_panel_*_s*.json` (nyers válaszokkal), `g2/`.
