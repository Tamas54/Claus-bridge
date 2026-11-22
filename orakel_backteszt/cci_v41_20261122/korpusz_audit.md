# KORPUSZ-AUDIT — CCI-backteszt V4.1-Flash (2026-11-23)

Forrás: Echolot MCP `get_news` (nyilvános, csak olvas), a cikk `published_at` napja szerint (as_of), `place=<ország>` + nyelv. Ablak: EU [M−1 hó 15., M hó 14.], US [M−1 hó 8., M hó 7.], minden 2. nap.
A napi pool felső korlátja 500 (a get_news határa): az 500 sűrű napot jelent.

| Ország | Cél-hónap | Ablak | Napok | Napi pool (min–átlag–max) | Nyers sor | Forrás | Szivárgás-szűrő | Korpusz n | Lean (n=60) | Fő kategóriák (nyers) |
|---|---|---|---|---|---|---|---|---|---|---|
| HU | 2026-07 | 06-15→07-14 | 15 | 8–402–500 | 528 | 37 | −0 | 60 | unknown 25, left 18, right 8, analytical 7, center 2 | general 28%, economy 27%, politics 21% |
| HU | 2026-08 | 07-15→08-14 | 16 | 500–500–500 | 658 | 38 | −1 | 60 | unknown 26, left 15, analytical 8, right 7, center 3, right_independent 1 | politics 28%, general 20%, economy 20% |
| HU | 2026-09 | 08-15→09-14 | 16 | 500–500–500 | 677 | 39 | −1 | 60 | unknown 29, left 13, right 6, analytical 6, center 6 | economy 20%, general 19%, politics 16% |
| HU | 2026-10 | 09-15→10-14 | 15 | 500–500–500 | 795 | 47 | −1 | 60 | unknown 29, left 14, right 7, center 6, analytical 4 | general 35%, economy 21%, tech 16% |
| HU | 2026-11 | 10-15→11-14 | 16 | 500–500–500 | 1008 | 47 | −0 | 60 | unknown 25, left 17, right 10, center 5, analytical 3 | general 48%, economy 20%, tech 13% |
| FR | 2026-07 | 06-15→07-14 | 15 | 11–384–500 | 821 | 110 | −0 | 60 | center 34, left 17, right 5, center_left 1, unknown 1, center_right 1, analytical 1 | foreign 43%, general 36%, tech 9% |
| FR | 2026-08 | 07-15→08-14 | 16 | 500–500–500 | 1200 | 95 | −0 | 60 | center 37, unknown 9, center_left 5, center_right 4, right 3, left 2 | foreign 29%, general 18%, sport 16% |
| FR | 2026-09 | 08-15→09-14 | 16 | 500–500–500 | 1172 | 98 | −0 | 60 | center 35, unknown 9, center_left 5, center_right 4, left 4, right 3 | sport 22%, foreign 19%, general 12% |
| FR | 2026-10 | 09-15→10-14 | 15 | 500–500–500 | 1129 | 112 | −0 | 60 | center 35, unknown 9, center_left 5, center_right 3, right 3, left 3, analytical 2 | foreign 42%, general 14%, sport 13% |
| FR | 2026-11 | 10-15→11-14 | 16 | 500–500–500 | 1279 | 102 | −0 | 60 | center 37, unknown 8, right 5, center_left 4, center_right 3, left 3 | foreign 62%, general 11%, sport 9% |
| IT | 2026-07 | 06-15→07-14 | 15 | 15–404–500 | 625 | 72 | −0 | 60 | center 36, left 14, unknown 5, right 5 | general 58%, foreign 24%, tech 9% |
| IT | 2026-08 | 07-15→08-14 | 16 | 500–500–500 | 984 | 117 | −0 | 60 | unknown 30, center 24, left 4, right 1, center_right 1 | sport 36%, general 17%, politics 11% |
| IT | 2026-09 | 08-15→09-14 | 16 | 500–500–500 | 989 | 118 | −0 | 60 | unknown 30, center 26, left 3, right 1 | sport 45%, general 18%, politics 9% |
| IT | 2026-10 | 09-15→10-14 | 15 | 500–500–500 | 944 | 114 | −0 | 60 | center 25, unknown 25, left 8, right 1, center_left 1 | sport 34%, general 19%, foreign 19% |
| IT | 2026-11 | 10-15→11-14 | 16 | 500–500–500 | 1063 | 105 | −0 | 60 | unknown 30, center 22, left 6, right 1, center_right 1 | sport 33%, foreign 23%, general 18% |
| DE | 2026-07 | 06-15→07-14 | 15 | 21–405–500 | 830 | 76 | −0 | 60 | center 36, left 10, unknown 8, right 6 | general 49%, sport 12%, foreign 11% |
| DE | 2026-08 | 07-15→08-14 | 16 | 500–500–500 | 1260 | 91 | −0 | 60 | unknown 27, center 26, left 5, right 2 | sport 26%, general 18%, politics 14% |
| DE | 2026-09 | 08-15→09-14 | 16 | 500–500–500 | 1301 | 97 | −0 | 60 | center 27, unknown 24, left 7, right 2 | sport 31%, general 17%, entertainment 11% |
| DE | 2026-10 | 09-15→10-14 | 15 | 500–500–500 | 1200 | 92 | −1 | 60 | center 27, unknown 24, left 7, right 2 | sport 23%, general 21%, global 14% |
| DE | 2026-11 | 10-15→11-14 | 16 | 500–500–500 | 1302 | 100 | −0 | 60 | unknown 27, center 26, left 6, right 1 | global 24%, general 23%, sport 19% |
| US | 2026-07 | 06-08→07-07 | 15 | 13–275–500 | 627 | 106 | −0 | 60 | center 26, right 12, unknown 11, left 8, center_left 2, gov 1 | global 29%, sport 22%, foreign 15% |
| US | 2026-08 | 07-08→08-07 | 16 | 500–500–500 | 1564 | 180 | −1 | 60 | center 21, unknown 19, right 9, left 6, center_left 3, gov 1, center_right 1 | global 30%, foreign 18%, sport 14% |
| US | 2026-09 | 08-08→09-07 | 16 | 500–500–500 | 1600 | 168 | −0 | 60 | unknown 20, center 20, right 8, left 7, center_left 2, center_right 1, analytical 1, gov 1 | global 23%, sport 18%, general 15% |
| US | 2026-10 | 09-08→10-07 | 15 | 500–500–500 | 1500 | 169 | −0 | 60 | center 20, unknown 20, right 8, left 7, center_left 2, center_right 1, gov 1, analytical 1 | global 25%, sport 22%, general 15% |
| US | 2026-11 | 10-08→11-07 | 16 | 500–500–500 | 1562 | 212 | −0 | 60 | unknown 31, center 12, left 7, right 7, center_right 1, center_left 1, gov 1 | global 61%, sport 10%, general 10% |
