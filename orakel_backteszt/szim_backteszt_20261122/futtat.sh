#!/usr/bin/env bash
# A backteszt futásai eseményenként, ZÁR ALATT (a gép és a közös SiliconFlow-keret kímélése):
#   ~/.cache/claus-heavy.lock — nehéz futás;  ~/.cache/claus-sf.lock — SiliconFlow-batch (a másik ügynök is használja).
# A zárak sorrendje: ELŐBB az SF-zár, utána a nehéz — így várakozás közben nem tartjuk a nehéz zárat.
# Előtte (zár nélkül, SF nem kell): `szim_backteszt elotolt` — a tervezőtől független Echolot-adat felvétele.
# Sorrend: előbb a hy3-út (a DELPHOI as_of-panel itt fut le eseményenként EGYSZER, a panel_tar.json-ba), utána a
# claude-út ugyanazzal a panellel és a felvett Echolot-adattal. A kulcsok a környezetből jönnek (soha nem fájlból).
set -euo pipefail
cd "$(dirname "$0")/../.."
D=orakel_backteszt/szim_backteszt_20261122
PY=../../../.venv/bin/python
mkdir -p ~/.cache
ESEMENYEK=${ESEMENYEK:-$($PY -c "import json;print(' '.join(e['id'] for e in json.load(open('$D/esemenybank_LOCKED.json'))['esemenyek']))")}
$PY -m szim_backteszt elotolt --bank $D/esemenybank_LOCKED.json --felvetel $D/felvetel_futas.json.gz
for e in $ESEMENYEK; do
  flock ~/.cache/claus-sf.lock flock ~/.cache/claus-heavy.lock \
    $PY -m szim_backteszt futtat --bank $D/esemenybank_LOCKED.json --felvetel $D/felvetel_futas.json.gz \
      --ki-dir $D/futasok --ut ${UTAK:-hy3 claude} --esemeny "$e" --max-usd ${MAX_USD:-0.6} --panel-n ${PANEL_N:-30}
done
$PY -m szim_backteszt ertekel --bank $D/esemenybank_LOCKED.json --gt $D/gt_LOCKED.json --ki-dir $D/futasok \
  --ki $D/ertekeles.json
