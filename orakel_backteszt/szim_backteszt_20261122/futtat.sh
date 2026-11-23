#!/usr/bin/env bash
# A backteszt futásai (2026-11-22, a v2-szimulátoron): eseményenként, a nehéz zár alatt.
#
# ELSŐ MENET = GAZDASÁGI (a Kommandant: „a közgazdasági közvetlen előrejelzés fontosabb”):
#   * a claude-út (Sonnet 5.5 minden lépésre; a v2-ben nincs Opus-lépés), SiliconFlow NÉLKÜL;
#   * a DELPHOI-panel KI (a panelmodellt a másik ügynök most választja ki méréssel) — a társadalmi jel hiányzik, kimondva;
#   * a gazdasági mag mindig fut; a sajtó csak ott, ahol a bank kérdése a sajtóról/társadalomról szól, a jog sehol
#     (a bankban nincs jog-kérdés);
#   * a horizont legfeljebb 90 nap és legkésőbb 2026-11-20 (a gazdasági célidőszakok adata így ma már megjelent).
# A panel-menet később: PANEL_MODELL=<nyertes modell> KI_DIR=futasok_panel — az SF-zár is kell (claus-sf.lock, ELŐTTE).
# Előtte (zár nélkül, SF nem kell): `szim_backteszt elotolt` — a tervezőtől független Echolot-adat felvétele.
set -euo pipefail
cd "$(dirname "$0")/../.."
D=orakel_backteszt/szim_backteszt_20261122
PY=../../../.venv/bin/python
mkdir -p ~/.cache
ESEMENYEK=${ESEMENYEK:-$($PY -c "import json;print(' '.join(e['id'] for e in json.load(open('$D/esemenybank_LOCKED.json'))['esemenyek']))")}
$PY -m szim_backteszt elotolt --bank $D/esemenybank_LOCKED.json --felvetel $D/felvetel_futas.json.gz
ZAR="flock $HOME/.cache/claus-heavy.lock"
PANEL=()
if [ -n "${PANEL_MODELL:-}" ]; then ZAR="flock $HOME/.cache/claus-sf.lock $ZAR"; PANEL=(--panel-modell "$PANEL_MODELL"); fi
for e in $ESEMENYEK; do
  $ZAR $PY -m szim_backteszt futtat --bank $D/esemenybank_LOCKED.json --felvetel $D/felvetel_futas.json.gz \
    --ki-dir $D/${KI_DIR:-futasok} --ut ${UTAK:-claude} --esemeny "$e" --max-usd ${MAX_USD:-0.8} "${PANEL[@]}"
done
$PY -m szim_backteszt ertekel --bank $D/esemenybank_LOCKED.json --gt $D/gt_LOCKED.json --ki-dir $D/${KI_DIR:-futasok} \
  --felvetel $D/felvetel_gazd_gt.json.gz --ki $D/${KI_DIR:-futasok}/ertekeles.json
# ÚJRAPONTOZÁS ELSŐ KÖZLÉSEN (11-23, elso_kozles): a feloldási szabály (first_print) szerint; az amerikai sorok első
# közlése az ALFRED-ből (a felvételben), a többi sor kimondva „revideált érték, első közlés hiányzik”. Hálózat nélkül:
#   $PY -m szim_backteszt ertekel --bank $D/esemenybank_LOCKED.json --gt $D/gt_LOCKED.json --ki-dir $D/futasok \
#     --felvetel $D/felvetel_gazd_gt_elso_kozles.json.gz --skala-felvetel $D/felvetel_futas.json.gz --ma 2026-11-22 \
#     --csak-felvetel --ki $D/futasok/ertekeles_elso_kozles.json
