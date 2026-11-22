#!/usr/bin/env bash
# CCI-backteszt V4.1-Flash — cellák sorban (a keretbe beszámítva: próba 0,0017 + füsthívás 0,0004 + az
# elvetett N=60-as HU-futás ~0,03 $), SF-zár alatt; 402-re (exit 3) vagy költségkeret-túllépésre megáll.
#   bash run_cellak.sh "HU 2026-07" "HU 2026-08" ...
cd "$(dirname "$0")" || exit 1
PY=~/Claus/claus-bridge-mcp/.venv/bin/python
KERET=${CCI_KERET:-0.45}
for cella in "$@"; do
  set -- $cella
  f="cellak/v41_$(echo "$1" | tr A-Z a-z)_$2.json"
  [ -f "$f" ] && { echo "[kész] $cella"; continue; }
  osszes=$($PY -c "import json,glob; print(round(sum(json.load(open(p))['cost_usd_this_run'] for p in glob.glob('cellak/*.json'))+0.0017+0.0004+0.03,4))")
  if $PY -c "import sys; sys.exit(0 if $osszes < $KERET else 1)"; then :; else
    echo "[KERET] eddigi költés \$$osszes >= \$$KERET — MEGÁLLOK"; exit 4; fi
  flock ~/.cache/claus-sf.lock $PY run_panel_v41.py "$1" "$2"
  rc=$?
  if [ $rc -eq 3 ]; then echo "[402] SF-egyenleg elfogyott — MEGÁLLOK"; exit 3; fi
  [ $rc -ne 0 ] && echo "[HIBA] $cella rc=$rc"
done
echo "[VÉGE] $(date +%T)"
