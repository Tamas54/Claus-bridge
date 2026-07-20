# Embedding-nyelvteszt — G0d (2026-07-20)

Modell: `text-embedding-3-small` · 205 egyedi horgonymondat · 3689 token · 1 batch-hívás

Mérce: **separation = within − between** (kategórián belüli vs kategóriák közti átlagos koszinusz-hasonlóság). Küszöb (HU/PT-referenciából kalibrálva): **ALARM, ha separation < 0.1427** (= 0.5 × min(HU=0.3561, PT=0.2854)).

| nyelv | kat. | mondat | within | between | separation | min. scale-koherencia | státusz |
|-------|------|--------|--------|---------|------------|----------------------|---------|
| cs | 4 | 20 | 0.7604 | 0.4379 | **0.3226** | -0.2606 | OK |
| de | 4 | 20 | 0.7687 | 0.4089 | **0.3598** | -0.0826 | OK |
| en | 5 | 25 | 0.681 | 0.212 | **0.469** | -0.178 | OK |
| es | 3 | 15 | 0.6943 | 0.3413 | **0.3529** | -0.089 | OK |
| fr | 5 | 25 | 0.7459 | 0.3605 | **0.3854** | -0.1017 | OK |
| hu (ref) | 6 | 30 | 0.7611 | 0.405 | **0.3561** | -0.178 | OK |
| it | 5 | 25 | 0.7439 | 0.4198 | **0.3241** | -0.2606 | OK |
| pl | 6 | 30 | 0.8123 | 0.4765 | **0.3358** | -0.2161 | OK |
| pt (ref) | 3 | 15 | 0.8123 | 0.527 | **0.2854** | 0.3623 | OK |

Kategória-részletek a JSON-ban (`embedding_nyelvteszt_20260720.json`).

Egyik nyelven sem omlott össze a szeparáció.

## Megfigyelések

1. **Minden új nyelv a referencia-sávban vagy fölötte.** A sáv alja PT=0.2854 (validált,
   élő backteszt-nyelv); a leggyengébb új nyelv (it=0.3241, cs=0.3226) is fölötte van,
   messze a 0.1427-es ALARM-küszöbtől.
2. **A negatív min. scale-koherencia NEM nyelvi hiba, hanem kategória-típus-tulajdonság.**
   Kizárólag az egyetértés-típusú halmazokon jelentkezik (agreement/zgoda/souhlas/accord/
   acuerdo/zustimmung/accordo), és a validált HU-referencián UGYANÍGY (hu/egyetertes
   koherencia 0.089, hu delphoi/growth −0.178): a két szélső horgony („egyáltalán nem" ↔
   „teljesen egyetértek") lexikailag közelebb ül egymáshoz, mint a köztes fokozatokhoz.
   Az SSR-nek a PMF-hez a horgony-KÖZELSÉG kell, nem a lineáris gradiens — a HU-n ez a
   geometria bizonyítottan működik, az új nyelvek mintázata ezzel azonos.
3. **A pénzügyi-kilátás halmazok mindenhol erősek** (within 0.85–0.89, price-koherencia
   0.29–0.74) — a G1–G2 két fő mutatójának (CCI + árvárakozás) horgonyai a legjobb
   állapotúak minden nyelven.
4. Az `en` kiugró szeparációja (0.469) részben abból jön, hogy az EN-készletben a
   market-research örökség (purchase_intent/interest) tematikusan távolabbi kategória.
