# OPERATION PYTHIA — G0a/B: HY3 RATE-LIMIT STRESSZTESZT

**Dátum:** 2026-07-20 · **Modell:** `tencent/Hy3` (SiliconFlow, thinking:disabled) · **L1 keret:** 10 000 RPM / 400 000 TPM
**Módszer:** óvatos, felfelé lépcsőző burst-teszt (5 szekvenciális alapvonal → 20 → 60 → 120 párhuzamos rövid hívás, ~59 token/hívás, 20 mp szünet a lépcsők között). Nyers adatok: `rate_limit_raw.json`, futtató: `rate_limit_test.py`.

## 1. MÉRT ADATOK

| Lépcső | 429 | Egyéb hiba | p50 latencia | p90 | max | Burst-áteresztés (RPM-ekv.) |
|---|---|---|---|---|---|---|
| alapvonal (5 szekv.) | 0 | 0 | 2,15 s | 2,44 s | 3,31 s | — |
| 20 párhuzamos | 0 | 0 | 3,02 s | 3,08 s | 3,08 s | ~389 |
| 60 párhuzamos | 0 | 0 | 5,52 s | 5,60 s | 5,63 s | ~639 |
| 120 párhuzamos | 0 | 0 | 8,20 s | 12,24 s | 12,39 s | ~580 |

## 2. ÉRTELMEZÉS

- **Egyetlen 429 sem** a 120-as párhuzamosságig: a 10k RPM keretet meg sem közelítettük (max ~640 RPM-ekvivalens), a kvóta nem korlátozó ebben a tartományban.
- **A tényleges korlát a szerveroldali sorbanállás:** a latencia a konkurrenciával közel lineárisan nő (2,2 s → 3,0 s → 5,5 s → 8,2 s), és az áteresztés ~600 RPM-ekv. körül **telítődik** (60-nál 639, 120-nál már csak 580 + p90-szórás felfut). 120 fölé menni nem ad áteresztést, csak latenciát és szórást.
- **Édes pont: 20–40 közötti konkurrencia** — közel maximális áteresztés, stabil, alacsony latencia, nulla hibaarány.

## 3. MÉRETEZÉSI SZABÁLY A G1 BACKTEST-FUTAMOKHOZ (80–240 persona-hívás/futam)

A persona-hívás nagyobb, mint a smoke-hívás: **~2 500 prompt + 300 completion ≈ 2 800 token/hívás** feltevéssel (vertikum-recepttől függően ellenőrizd) a TPM a szűk keresztmetszet, nem az RPM:

- **TPM-plafon 75%-os biztonsági sávval:** 300 000 token/perc → **max hívás/perc = 300 000 / token_per_hívás** (2 800 tokennél ≈ **107 hívás/perc**).
- **Ajánlott üzemi paraméterek:**
  - **Konkurrencia (semaphore): 24** (rövid, extrakciós hívásoknál 32–40 is mehet);
  - **Ütemezés: legfeljebb 100 hívás/perc** token-bucket pacinggel — a puszta semaphore NEM elég, mert 24-es konkurrenciával és ~6 s/hívás latenciával ~240 hívás/perc jönne ki, ami 2,8k tokenes hívásokkal ~670k TPM-et jelentene (keretsértés);
  - **240 hívásos futam várható ideje: ~2,5 perc** (80 hívásos: ~1 perc);
  - **429-backoff (védőháló, bár nem mértünk 429-et):** exponenciális, 2 s · 2^kísérlet + ±25% jitter, max 4 kísérlet;
  - **Adaptív fék:** ha a gördülő p90-latencia > 15 s, a konkurrencia felezése (a szerveroldali sorbanállás jele, nem kvótáé);
  - **Párhuzamos futamok:** két egyidejű backtest-futam még belefér a 75%-os sávba rövidebb hívásokkal; 2,8k tokenes hívásokkal futamokat SOROSAN futtass.
- **Ne tartósan a keret közelében:** a fenti pacing a keret ~70–75%-át használja csúcsban; ez a méretezés szándékosan hagy tartalékot az Echolot/Bridge éles Hy3-forgalmának (NULLTARIF fordítóréteg!).

## 4. KAPCSOLÓDÓ G0a-TANULSÁG

A cutoff-szonda 88 hívásos futamai (konkurrencia 8) hibátlanul, 429 nélkül futottak; temp=0 mellett a Hy3 determinisztikus (2 ismétlés 100% egyezés) — a G1-ben az ismétlés-szám csökkenthető, a keret inkább szélesebb kérdés/persona-bankra fordítandó.
