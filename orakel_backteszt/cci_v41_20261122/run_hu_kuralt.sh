#!/usr/bin/env bash
# HU kurált próba: A (priming) 07→10, majd B (kontroll) 07→10; SF-zár alatt; 402-re (exit 3) vagy ha az
# eddigi költés (A+B) >= 0,55 $, megáll (PREREG_HU_KURALT.json keret).
cd "$(dirname "$0")" || exit 1
PY=~/Claus/claus-bridge-mcp/.venv/bin/python
for v in A B; do
  for m in 2026-07 2026-08 2026-09 2026-10; do
    [ -f "cellak_hu_kuralt_$v/v41_hu_$m.json" ] && { echo "[kész] $v $m"; continue; }
    osszes=$($PY -c "import json,glob; print(round(sum(json.load(open(p))['cost_usd_this_run'] for p in glob.glob('cellak_hu_kuralt_*/*.json')),4))")
    if ! $PY -c "import sys; sys.exit(0 if $osszes < 0.55 else 1)"; then
      echo "[KERET] eddigi költés \$$osszes >= 0,55 \$ — MEGÁLLOK"; exit 4; fi
    flock ~/.cache/claus-sf.lock $PY run_hu_kuralt.py "$v" "$m"
    rc=$?
    if [ $rc -eq 3 ]; then echo "[402] SF-egyenleg elfogyott — MEGÁLLOK"; exit 3; fi
    [ $rc -ne 0 ] && echo "[HIBA] $v $m rc=$rc"
  done
done
echo "[VÉGE] $(date +%T)"
