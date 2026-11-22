# CUTOFF-SZONDA 2026-11-22 — JEGYZŐKÖNYV

**Cél:** a `modell_cutoff.CUTOFF` tábla kitöltése mért értékekkel, a döntés-szimulátor és a backteszt-mód minden
lehetséges modelljére. **Módszer:** a 07-20-i protokoll (`../cutoff_szonda_20260720/`): lágy „B” system-prompt,
temp=0 (SF, Non-Think; gpt-oss: reasoning_effort=low), 1 hívás/kérdés. Claude: Message Batches, effort=low, max 1024 token.

**Pre-regisztráció (a futtatás ELŐTT commitolva):** `91a35c9` (2023–2024-es létra, 38 kérdés + a 07-20-i bankok gépi
pontozási szabálya), `01c7ccf` (Echolot-bank, 56 esemény 2025-07…2026-11, GT kizárólag Echolot-napi kiadásból és
datált Echolot-címekből; 11 kizárt, kitalálható tétel). A 07-20-i bankok: `decb49d`, `2aac2d3`, `e7e8711`.

**Pontozás:** determinisztikus (`grade.py`, AI nélkül): kulcsszó-találat, a NEM TUDOM-mal kezdődő válasz nem találat.
Ez utóbbi utólagos javítás (az Opus két hamis pozitívja miatt: „NEM TUDOM … az előző idényt a Slavia nyerte”), minden
modellre egyformán alkalmazva. A pontozó a 07-20-i Hy3-válaszokon visszaadja a kézi verdiktet (cutoff 2025-04, a
2025-06-os Irán-sziget, HU-kártya 0/5).
**Definíció (a modulé):** `cutoff` = az utolsó hónap ≥60%-os havi találattal; `tiszta_tol` = az utolsó találat
utáni hónap. A kettő között GRAY.

## Eredmény

| Modell | Kérdés | Talált | cutoff | tiszta_tol | Sziget / megjegyzés | HU-kártya (2026-04) |
|---|---|---|---|---|---|---|
| claude-opus-5-5 | 154 | 89/138 | **2026-01** (6/8) | **2026-03** | 2026-02: csak az Anthropic–Pentagon-vita (saját téma) | 0/5 — nem tudja |
| claude-sonnet-5-5 | 154 | 71/138 | **2025-08** | **2026-02** | foltos, 40–60% 2025-09…2026-01 (Maduro, Saks) | 0/5 |
| claude-haiku-5-5 | 154 | 70/138 | **2025-12** | **2026-03** | 2026-02: Anthropic–Pentagon-vita | 0/5 |
| deepseek-ai/DeepSeek-V4.1-Flash | 154 | 51/138 | **2025-03** | **2026-01** | farok 2025-07-ig; sziget 2025-12 (vb-sorsolás, tajvani fegyvercsomag) | 0/5 |
| Qwen/Qwen3-30B-A3B-Instruct-2507 | 98 | 21/88 | 2024-07 | 2025-02 | alacsony felidézés | 0/5 |
| Qwen/Qwen3-14B | 98 | 6/88 | 2023-09 | 2024-10 | alacsony felidézés | 0/5 |
| Qwen/Qwen3.5-9B | 98 | 12/88 | 2023-09 | 2024-10 | alacsony felidézés, magabiztos tévedések | 0/5 |
| Qwen/Qwen3-8B | 98 | 3/88 | 2023-03 | 2023-12 | nagyon alacsony felidézés: a „cutoff” itt a tudás hiánya | 0/5 |
| openai/gpt-oss-120b | 98 | 20/88 | 2024-05 | 2024-06 | éles határ | 0/5 |
| openai/gpt-oss-20b | 98 | 14/88 | 2024-04 | 2024-06 | éles határ | 0/5 |
| tencent/Hy3 (07-20) | — | — | 2025-04 | 2025-08 | 2025-06 Irán-háború | 0/5 |

A „Kérdés” oszlop a kiküldött kérdések száma (a HU-kártya és a kizárt tételek nélkül pontozva). A régi tudású SF-modellek
az Echolot-bankot nem kapták meg: a 07-20-i bank 2025-07…2026-06-os 31 eseményére nulla találatot adtak.

## Mit jelent a backtesztnek

- **A 2026-04-i HU-választás minden mért modellnél CLEAN:** egyik modell sem tudja a Tisza győzelmét. A Claude-ok a
  választást jövőbeli eseményként kezelik („Legutóbbi ismereteim szerint Orbán Viktor a miniszterelnök”).
- **A szimulátor teljes lánca** (Sonnet 5.5 sajtó/összegzés + Opus 5.5 + Haiku + a panelmodell) 2026-03-tól tiszta.
  Ennél korábbi eseményre a `futas_tisztasaga()` GRAY-t vagy CONTAMINATED-et ad.
- Az Opus 5.5 rendszerszintű határa (2026 júniusa) helyett a szonda 2026-01-et mér; a backteszt a mért értéket használja.
- Nem mért: az SSR-embedding (`Qwen/Qwen3-Embedding-8B`). Ez nem generál szöveget, csak a válaszokat helyezi el a
  horgonyok között.

## Fájlok

`resp_<modell>*.json` (nyers válaszok), `grade_<modell>*.json` (pontozás soronként), `run_probe.py`, `grade.py`,
`kw_override.json`, `cutoff_events_ladder_2023_2024.json`, `cutoff_events_echolot.json`.
