#!/usr/bin/env python3
"""OLCSÓ SWARM — egy G2-cella (07-20, `../g2_hy3_20260720/run_g2.py`) VÁLTOZATLAN protokollal, más modellel.

A run_g2 modul-szintű MODEL-jét és kimeneti könyvtárát írjuk felül (a 07-20-i Hy3-artefaktok érintetlenek maradnak);
korpusz, GT-lock (gt_LOCKED_fr_it.json), persona-kvóta, prompt, SSR és a 3 seed ugyanaz.

  G2_COUNTRY=IT G2_MODE=cci G2_WINDOW=2026-06 python3 run_g2_model.py <model>
"""
import json, os, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
G2 = HERE.parent / "g2_hy3_20260720"
sys.path.insert(0, str(G2))
model = sys.argv[1]
import run_g2  # noqa: E402  (modul-szinten betölti a korpuszt és a GT-t, a main() még nem fut)

run_g2.MODEL = model
run_g2.G2DIR = str(HERE / "g2")
run_g2.main()
sub = "cci" if run_g2.MODE == "cci" else "inflexp"
src = HERE / "g2" / sub / f"hy3_{run_g2.LANG}_{sub}_{run_g2.WINDOW}.json"
dst = HERE / "g2" / f"{model.replace('/', '_')}_{run_g2.LANG}_{sub}_{run_g2.WINDOW}.json"
d = json.loads(src.read_text())
d["date"] = "2026-11-22"
d["olcso_swarm_megjegyzes"] = "a 07-20-i run_g2 protokollja, csak a modell más (run_g2_model.py)"
dst.write_text(json.dumps(d, ensure_ascii=False, indent=2))
src.unlink()
print(f"[olcso-g2] {dst.name}: seed_mean={d['seed_mean']:.2f} sd={d['seed_sd']:.2f} gt={d['gt']}")
