# G1 futtatási sorrend (sorosan, commit futamonként)
1. run1_hu_panel.py                      (seeds 2,3; seed1 = hy3_20260720)      [FUT]
2. G1_VARIANT=base run2_cz_panel.py ; G1_VARIANT=multiparty run2_cz_panel.py   (seeds 1-3)
3. G1_CELL=hu run3 ; G1_CELL=cz ORAKEL_CACHE=1 run3 ; G1_CELL=pt ORAKEL_CACHE=1 run3
4. run4_nowcast.py x6: hu/2025-06 hu/2025-12 hu/2026-05 cz/2025-julsep cz/2026-01 cz/2026-05
5. run5_inflexp.py
6. run6_yt.py
Majd: g1_matrix.md + g1_matrix.json + költség-log, zárócommit.
