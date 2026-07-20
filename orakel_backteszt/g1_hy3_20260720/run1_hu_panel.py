#!/usr/bin/env python3
"""G1/1. futam — HU pre-election panel Hy3-replikáció.

Protokoll = orakel_backteszt/run_panel.py változatlanul (N=80, temp=0.8,
max_tokens=30, 400-headline média-kontextus), modell = tencent/Hy3.
A 07-20-i seed=1 Hy3-futás (hy3_20260720/panel_results_tencent_Hy3.json) megvan →
itt a +2 seed (2, 3) fut, majd a három seed együtt aggregálódik.
Flag: CLEAN/cutoff-igazolt (G0a jegyzőkönyv 4. pont).
"""
import json
import os
import random
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g1_lib

g1_lib.load_env()
os.environ["ORAKEL_PROVIDER"] = "siliconflow"
os.environ["ORAKEL_PANEL_MODEL"] = g1_lib.MODEL

HARNESS_DIR = os.path.join(g1_lib.REPO, "orakel_backteszt")
sys.path.insert(0, HARNESS_DIR)
import run_panel as rp  # noqa: E402  (a harness változatlan)

N = int(os.environ.get("G1_N", str(rp.PANEL_N)))
SEEDS = [int(s) for s in os.environ.get("G1_SEEDS", "2,3").split(",")]
OUT = os.environ.get("G1_OUT", "hu_pre_election/hy3_panel_g1.json")

CORPUS_PATH = os.path.join(HARNESS_DIR, "corpus.jsonl")
SEED1_PATH = os.path.join(HARNESS_DIR, "hy3_20260720", "panel_results_tencent_Hy3.json")


def main():
    corpus = [json.loads(l) for l in open(CORPUS_PATH, encoding="utf-8") if l.strip()]
    media_env = rp.build_media_context(corpus)
    s0, u0 = rp.make_persona_prompt(random.Random(0), media_env)
    cpm = g1_lib.pace_for(g1_lib.est_tokens(s0, u0, max_tokens=30))
    pacer = g1_lib.Pacer(cpm)
    call = pacer.wrap(rp.call_model)
    print(f"G1/1 HU panel — modell={rp.PANEL_MODEL}, N={N}, seeds={SEEDS}, pacing={cpm:.0f}/perc")

    per_seed = []
    for seed in SEEDS:
        rng = random.Random(seed)
        prompts = [rp.make_persona_prompt(rng, media_env) for _ in range(N)]

        def ask(i):
            try:
                return rp.parse_vote(call(*prompts[i]))
            except Exception as e:  # noqa: BLE001
                print(f"  [!] seed {seed} persona {i}: {e}")
                return "bizonytalan"

        tally = {}
        with ThreadPoolExecutor(max_workers=g1_lib.CONCURRENCY) as ex:
            for v in ex.map(ask, range(N)):
                tally[v] = tally.get(v, 0) + 1
        shares = rp.decided_share(tally)
        gap = shares.get("Tisza", 0) - shares.get("Fidesz-KDNP", 0)
        per_seed.append({"seed": seed, "tally": tally, "decided_shares": shares,
                         "tisza_fidesz_gap": gap})
        print(f"  seed {seed}: Tisza {shares.get('Tisza', 0):.1f} | Fidesz {shares.get('Fidesz-KDNP', 0):.1f} | rés {gap:+.2f}")

    # a 07-20-i seed=1 futás beolvasása és egyesítés
    seed1 = json.load(open(SEED1_PATH, encoding="utf-8"))["seeds"][0]
    seed1["source"] = "hy3_20260720/panel_results_tencent_Hy3.json (2026-07-20 futás)"
    all_seeds = [seed1] + per_seed
    gaps = [s["tisza_fidesz_gap"] for s in all_seeds]
    mean_gap = statistics.mean(gaps)
    stdev = statistics.pstdev(gaps)
    gt = 14.57
    print(f"  ÖSSZ (3 seed): mean_gap {mean_gap:+.2f} (szórás {stdev:.2f}) | GT {gt:+.2f} | |hiba| {abs(mean_gap-gt):.2f}")

    g1_lib.write_artifact(OUT, {
        "run": "G1/1 HU pre-election panel",
        "model": g1_lib.MODEL, "provider": "siliconflow", "date": "2026-07-20",
        "protocol": "orakel_backteszt/run_panel.py változatlanul (N=80, temp=0.8, max_tokens=30)",
        "flag": "CLEAN/cutoff-igazolt",
        "flag_note": "G0a cutoff-szonda 4. pont: a Hy3 a 2026-04-i választást jövőként kezeli, 25/25 válaszban nincs Tisza-győzelem",
        "corpus_files": {"orakel_backteszt/corpus.jsonl": g1_lib.sha256_file(CORPUS_PATH)},
        "ground_truth_gap": gt,
        "seeds": all_seeds,
        "mean_gap": mean_gap, "stdev": stdev, "abs_err": abs(mean_gap - gt),
        "flash_reference": {"file": "orakel_backteszt/panel_results_deepseek-ai_DeepSeek-V4-Flash.json",
                            "mean_gap": 20.888, "seeds": 2, "abs_err": 6.32},
        "cost": pacer.stats(),
        "raw_stream": pacer.raw_stream,
    })


if __name__ == "__main__":
    main()
