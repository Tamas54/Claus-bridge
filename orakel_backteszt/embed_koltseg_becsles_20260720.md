# SSR-embedding költség-becslés — G0d (2026-07-20)

Hatókör: az SSR-lépés **embedding-hívásai** (OpenAI `text-embedding-3-small`,
$0.02 / 1M token). A Flash chat-oldal (persona-válaszok generálása) NEM része
ennek a becslésnek.

## Bemenő paraméterek (G1–G2 terv)

| paraméter | érték |
|---|---|
| Replikációs futamok (G1) | 6 |
| Új futamok (G2) | ~5 ország × 2 mutató × 3 ablak = **30** |
| Összes futam | **36** |
| Persona-panel / futam | N = 80–240 |
| DELPHOI_SAMPLES_PER_PERSONA | 3 |
| Beágyazandó szöveg / futam | N × 3 válasz + ~10 horgonymondat |
| Token / horgonymondat (MÉRT, nyelvteszt 2026-07-20) | 205 mondat = 3 689 token → **~18 tok/mondat** |
| Token / persona-válasz (becslés: 1 mondat, horgonynál ~2× hosszabb + többnyelvű tokenizer-ráhagyás) | **~35 tok** |

## Számítás

Egy futam embedding-volumene:

| panel | szöveg/futam | token/futam |
|---|---|---|
| N=80 | 240 válasz + 10 horgony | 240×35 + 180 ≈ **8 600** |
| N=240 | 720 válasz + 10 horgony | 720×35 + 180 ≈ **25 400** |

36 futamra:

| forgatókönyv | token összesen | költség |
|---|---|---|
| alsó (minden futam N=80) | ~0,31 M | **$0,006** |
| felső (minden futam N=240) | ~0,92 M | **$0,018** |
| felső × 3 biztonsági szorzó (retry, debug-újrafutás, extra horgony-halmazok) | ~2,8 M | **$0,056** |

Megjegyzések:
- A linear vs softmax összevetés NEM duplázza a költséget — ugyanazokból az
  embeddingekből számol mindkét PMF-út.
- Hívásszám: batch-elve (≤2 048 input/hívás) 1–2 API-hívás/futam → ~40–80
  request a teljes G1–G2-re; rate-limit-kockázat nincs.
- A horgonymondatok fixek → egy futamsorozaton belül cache-elhetők (a
  megtakarítás a fenti nagyságrendben elhanyagolható).

## Ajánlott sapka (implementáció P2, NEM e kör dolga)

- **`DELPHOI_EMBED_BUDGET=2000000`** — napi token-sapka (~$0,04/nap, plafon
  ~$1,2/hó). A teljes G1–G2 worst-case volumen (~0,9 M token) bőven belefér
  EGY napba is; a sapka a runaway-loop / hibás fan-out ellen véd, nem a
  tervezett terhelés ellen.
- Másodlagos őr (opcionális): per-futam sanity-limit **100 000 token/futam**
  (= 4× a legnagyobb tervezett futam), túllépésnél hangos hiba, nem csendes vágás.

## Következtetés

Az SSR-embedding a G1–G2 terv költségszerkezetében **zajszintű tétel** (<$0,06
a 3× biztonsági szorzóval együtt). A tényleges költség-kockázat a chat-oldalon
(Flash fan-out) él, nem az embeddingnél — a sapka ott legyen szigorú.
