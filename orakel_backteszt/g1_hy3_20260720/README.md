# OPERATION PYTHIA — G1: REPLIKÁCIÓS KAMPÁNY (2026-07-20)

Minden eddigi ORAKEL-backteszt Hy3-replikációja. Modell KIZÁRÓLAG `tencent/Hy3`
(SiliconFlow, `thinking:{"type":"disabled"}`, response_format nélkül). A
DeepSeek-V4-Flash oszlop a TÖRTÉNETI artefaktokból töltődik — Flash-hívás a G1-ben
nem történt.

## Szerkezet
- `g1_lib.py` — pacing (G0a/B szabály: konk. 24 + min(100, 300k/token) hívás/perc)
  + hívás-számláló + nyersválasz-napló. A harness-fájlok érintetlenek: a wrapperek
  importálják őket, és csak a seedet/modellt/pacinget vezérlik.
- `run1_hu_panel.py` … `run6_yt.py` — a hat futam wrapperei (sorosan futtatva).
- `hu_pre_election/ cz_election/ cci_headline/ cci_nowcast/ inflexp/ yt_title/`
  — futamonkénti JSON-artefaktok (nyers válaszok + aggregátum + seed + corpus_hash + flag).
- `build_matrix.py` → `g1_matrix.json`; jegyzőkönyv: `g1_matrix.md`.

## Kontamináció-flag (G0a cutoff-jegyzőkönyv szabálya)
target-ablak <= 2025-04 → CONTAMINATED · 2025-05..07 → GRAY · >= 2025-08 → CLEAN;
Irán-háborús target CLEAN-ben is TOPIC-GRAY(iran-war) — a hat futam egyikének
targetje sem Irán-háborús. A HU pre-election cella: CLEAN/cutoff-igazolt.

## Elicitálás
A G0a tanulsága szerint lágy elicitálás — a harnessek saját promptjai és
paraméterei (temp=0.8 stb.) VÁLTOZATLANOK; a replikáció egyetlen mozgó alkatrésze
a modell (+ a seed-sweep: minden cella >= 3 seed; a HU pre-election seed=1 a
07-20-i füst-futásból származik).
