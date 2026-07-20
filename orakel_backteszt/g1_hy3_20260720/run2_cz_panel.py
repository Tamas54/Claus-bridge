#!/usr/bin/env python3
"""G1/2. futam — CZ választás panel Hy3-replikáció (base + multiparty variáns).

Protokoll = orakel_backteszt_cz/run_panel.py változatlanul (N=80, temp=0.8,
max_tokens=30, 320-headline média-kontextus), modell = tencent/Hy3, seeds 1-3.
Variáns: G1_VARIANT=base | multiparty (ORAKEL_MULTIPARTY_HINT import ELŐTT áll be,
pontosan úgy, ahogy a történeti Flash-futásokban).
Flag: CLEAN (target 2025-10 >= 2025-08).
"""
import json
import os
import random
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g1_lib

VARIANT = os.environ.get("G1_VARIANT", "base")
g1_lib.load_env()
os.environ["ORAKEL_PROVIDER"] = "siliconflow"
os.environ["ORAKEL_PANEL_MODEL"] = g1_lib.MODEL
os.environ["ORAKEL_MULTIPARTY_HINT"] = "1" if VARIANT == "multiparty" else "0"

HARNESS_DIR = os.path.join(g1_lib.REPO, "orakel_backteszt_cz")
sys.path.insert(0, HARNESS_DIR)
import run_panel as rp  # noqa: E402  (a CSEH harness változatlan)
import config as cfg    # noqa: E402

N = int(os.environ.get("G1_N", str(rp.PANEL_N)))
SEEDS = [int(s) for s in os.environ.get("G1_SEEDS", "1,2,3").split(",")]
OUT = os.environ.get("G1_OUT", f"cz_election/hy3_panel_g1_{VARIANT}.json")
CORPUS_PATH = os.path.join(HARNESS_DIR, "corpus.jsonl")


def main():
    corpus = [json.loads(l) for l in open(CORPUS_PATH, encoding="utf-8") if l.strip()]
    media_env = rp.build_media_context(corpus)
    s0, u0 = rp.make_persona_prompt(random.Random(0), media_env)
    cpm = g1_lib.pace_for(g1_lib.est_tokens(s0, u0, max_tokens=30))
    pacer = g1_lib.Pacer(cpm)
    call = pacer.wrap(rp.call_model)
    print(f"G1/2 CZ panel [{VARIANT}] — modell={rp.PANEL_MODEL}, N={N}, seeds={SEEDS}, "
          f"multiparty_hint={os.environ['ORAKEL_MULTIPARTY_HINT']}, pacing={cpm:.0f}/perc")

    seed_results = []
    for seed in SEEDS:
        rng = random.Random(seed)
        prompts = [rp.make_persona_prompt(rng, media_env) for _ in range(N)]

        def ask(i):
            try:
                return rp.parse_vote(call(*prompts[i]))
            except Exception as e:  # noqa: BLE001
                print(f"  [!] seed {seed} persona {i}: {e}")
                return "nerozhodnut"

        tally = {}
        with ThreadPoolExecutor(max_workers=g1_lib.CONCURRENCY) as ex:
            for v in ex.map(ask, range(N)):
                tally[v] = tally.get(v, 0) + 1
        shares = rp.decided_share(tally)
        seed_results.append({"seed": seed, "tally": tally, "decided_shares": shares})
        top = sorted(shares.items(), key=lambda x: -x[1])[:4]
        print(f"  seed {seed}: " + " | ".join(f"{p} {v:.0f}%" for p, v in top))

    agg = rp.aggregate_shares(seed_results)
    ev = rp.evaluate(agg)
    print(f"  ÖSSZ rangsor: {' > '.join(ev['panel_rank'])} | ANO-SPOLU rés {ev['ano_spolu_gap']:+.1f} "
          f"(GT {cfg.GROUND_TRUTH_ANO_SPOLU_GAP:+.2f}) | ANO1 {ev['ano_first']} SPOLU2 {ev['spolu_second']} "
          f"Stacilo-ki {ev['stacilo_out']}")

    g1_lib.write_artifact(OUT, {
        "run": f"G1/2 CZ election panel ({VARIANT})",
        "model": g1_lib.MODEL, "provider": "siliconflow", "date": "2026-07-20",
        "protocol": f"orakel_backteszt_cz/run_panel.py változatlanul, ORAKEL_MULTIPARTY_HINT={os.environ['ORAKEL_MULTIPARTY_HINT']}",
        "flag": "CLEAN",
        "flag_note": "target-ablak 2025-10 >= 2025-08 (G0a flag-szabály); a G0a esemény-szonda a cseh választásra: NEM TUDOM",
        "corpus_files": {"orakel_backteszt_cz/corpus.jsonl": g1_lib.sha256_file(CORPUS_PATH)},
        "ground_truth": cfg.GROUND_TRUTH,
        "ground_truth_ano_spolu_gap": cfg.GROUND_TRUTH_ANO_SPOLU_GAP,
        "aggregate_shares": agg,
        "evaluation": ev,
        "seeds": seed_results,
        "flash_reference": {"file": f"orakel_backteszt_cz/panel_results_deepseek-ai_DeepSeek-V4-Flash{'_multiparty' if VARIANT=='multiparty' else ''}.json"},
        "cost": pacer.stats(),
        "raw_stream": pacer.raw_stream,
    })


if __name__ == "__main__":
    main()
