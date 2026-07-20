#!/usr/bin/env python3
"""ESI SZEKTORÁLIS BIZALOM — ground truth ZÁROLÁS (a panel-futás ELŐTT).
Forrás: Eurostat ei_bssi_m_r2, s_adj=SA, lehúzva 2026-06-17.
Indikátorok: Industrial / Services / Retail / Construction confidence indicator.
Rács: 5 ország {HU,CZ,PL,PT,DE} × 6 hónap (3 cél + 3 M-1 a persistence baseline-hoz) × 4 szektor.
A vonalzót a futás után NEM igazítjuk. Kimenet: esi_sector_ground_truth_LOCKED.json
"""
import json, datetime

SECTORS = ["industry", "services", "retail", "construction"]
MONTHS_ALL = ["2025-08", "2025-09", "2025-10", "2025-11", "2025-12", "2026-01"]
TARGET_MONTHS = ["2025-09", "2025-11", "2026-01"]      # célhónapok (panel jósol)
PREV_MONTH = {"2025-09": "2025-08", "2025-11": "2025-10", "2026-01": "2025-12"}  # persistence

# (industry, services, retail, construction) az ei_bssi_m_r2 SA értékei
GT = {
 "HU": {
  "2025-08": (-11.4, -18.0, -20.9, -26.7), "2025-09": (-9.0, -11.3, -21.4, -26.2),
  "2025-10": (-8.9, -11.4, -17.6, -24.7),  "2025-11": (-11.4, -9.0, -17.1, -24.6),
  "2025-12": (-7.6, -8.7, -16.2, -27.5),   "2026-01": (-11.1, -13.1, -18.0, -25.4)},
 "CZ": {
  "2025-08": (-5.4, 39.6, 12.6, 0.9),  "2025-09": (-6.0, 38.2, 14.9, 2.3),
  "2025-10": (-2.5, 37.8, 12.4, -0.6), "2025-11": (-7.2, 37.2, 14.0, -4.0),
  "2025-12": (-7.9, 34.8, 10.2, -2.7), "2026-01": (-7.7, 36.3, 9.4, -5.0)},
 "PL": {
  "2025-08": (-16.5, -3.0, -2.4, -18.3), "2025-09": (-15.3, -3.4, -2.3, -17.4),
  "2025-10": (-15.6, -3.3, -1.6, -17.4), "2025-11": (-14.9, -2.5, -2.4, -17.1),
  "2025-12": (-15.5, -2.0, -1.7, -16.5), "2026-01": (-14.3, -1.4, -0.6, -16.5)},
 "PT": {
  "2025-08": (-3.1, 11.9, 3.4, 2.7), "2025-09": (-3.4, 8.5, 3.6, 2.5),
  "2025-10": (-5.3, 7.7, 4.6, 3.3),  "2025-11": (-0.2, 6.0, 6.6, 3.9),
  "2025-12": (-0.9, 8.2, 6.3, 2.2),  "2026-01": (-3.6, 4.1, 4.5, 3.0)},
 "DE": {
  "2025-08": (-19.3, 4.6, -22.0, -12.6), "2025-09": (-20.1, 1.6, -22.9, -14.9),
  "2025-10": (-18.1, 3.4, -21.3, -13.6), "2025-11": (-18.8, 4.3, -23.7, -12.4),
  "2025-12": (-20.7, 2.8, -25.3, -12.0), "2026-01": (-17.4, 5.3, -25.2, -11.2)},
}

def main():
    locked = {
        "_meta": {
            "locked_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "source": "Eurostat ei_bssi_m_r2, s_adj=SA",
            "sectors": SECTORS,
            "target_months": TARGET_MONTHS,
            "prev_month_for_persistence": PREV_MONTH,
            "note": "ZÁROLT vonalzó. A futás után NEM igazítjuk. Brief: brief_esi_szektor_backteszt.md",
        },
        "gt": {c: {m: dict(zip(SECTORS, vals)) for m, vals in months.items()} for c, months in GT.items()},
    }
    with open("esi_sector_ground_truth_LOCKED.json", "w", encoding="utf-8") as f:
        json.dump(locked, f, ensure_ascii=False, indent=2)
    print(f"ZÁROLVA: {locked['_meta']['locked_at']}  ({len(GT)} ország × {len(MONTHS_ALL)} hónap × 4 szektor)")
    # gyors épségellenőrzés: between-sector sorrend célhónaponként
    for c in GT:
        for m in TARGET_MONTHS:
            order = sorted(SECTORS, key=lambda s: -dict(zip(SECTORS, GT[c][m]))[s])
            print(f"  {c} {m}: {' > '.join(order)}")

if __name__ == "__main__":
    main()
