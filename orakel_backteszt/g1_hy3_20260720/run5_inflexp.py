#!/usr/bin/env python3
"""G1/5. futam — inflációs (ár)várakozás vak backteszt Hy3-replikáció.

Protokoll = run_inflexp_backtest.py változatlanul (4 ország x 3 időpont, N=40,
16 mintavett hír, temp=0.8, max_tokens=160, SSR linear + OpenAI-small; PL
out-of-sample nyelv), modell = tencent/Hy3, seeds 1-3.
GT: a script-be zárolt PRICE_GT/CCI_GT (== inflation_ground_truth_LOCKED.json,
bit-egyezés ellenőrizve, evidence-commit 7068da7).
Flag: minden target-ablak (2025-09 / 2025-11 / 2026-01) >= 2025-08 -> CLEAN.
"""
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g1_lib

g1_lib.load_env()
os.environ["ORAKEL_PANEL_MODEL"] = g1_lib.MODEL
os.environ.setdefault("SSR_DEMO_N", "40")
os.environ.setdefault("ORAKEL_CACHE", "1")

sys.path.insert(0, g1_lib.REPO)
os.chdir(g1_lib.REPO)   # a harness a korpuszfájlokat relatív úton nyitja
import run_inflexp_backtest as m   # noqa: E402
from plugins import ssr            # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

SEEDS = [int(s) for s in os.environ.get("G1_SEEDS", "1,2,3").split(",")]
N = m.N
COUNTRIES = ["HU", "CZ", "PT", "PL"]


def run_cell(pacer_call, country, ti, seed):
    """m.run_cell tükörmásolata — egyetlen eltérés: a rng seedje paraméter."""
    corpus = json.load(open(m.CORP[(country, ti)], encoding="utf-8"))
    nt = len(corpus)
    age, reg, edu = m.DEMOG[country]
    rng = random.Random(seed)
    mark = m.PR_MARK[country]

    def build(i):
        news = "\n".join("- " + x for x in rng.sample(corpus, min(16, nt)))
        sysmsg = m.SYS[country].format(y=m.MONTHS[ti])
        user = f"{m.pickn(rng, age)}, {m.pickn(rng, reg)}, {m.pickn(rng, edu)}.\n\n{m.HDR[country]}\n{news}{m.ASK[country]}"
        return sysmsg, user

    prompts = [build(i) for i in range(N)]

    def ask(i):
        s, u = prompts[i]
        return m.parse2(pacer_call(s, u, seed * 100000 + i), mark)

    with ThreadPoolExecutor(max_workers=g1_lib.CONCURRENCY) as ex:
        rows = list(ex.map(ask, range(N)))
    pr = [p for p, f in rows if p]
    fn = [f for p, f in rows if f]
    pscore = ssr.rate(pr, ssr.REFERENCE_SETS_PRICE[country], method="linear", embed_fn=m.oai_embed)["survey_score"]
    fscore = ssr.rate(fn, m.FIN_ANCHOR[country], method="linear", embed_fn=m.oai_embed)["survey_score"]
    return pscore, (fscore - 3) / 2 * 100, len(pr), [r[0] for r in rows], [r[1] for r in rows]


def main():
    s0, u0 = None, None
    corpus0 = json.load(open(m.CORP[("HU", 0)], encoding="utf-8"))
    rng0 = random.Random(0)
    news = "\n".join("- " + x for x in rng0.sample(corpus0, min(16, len(corpus0))))
    s0 = m.SYS["HU"].format(y=m.MONTHS[0])
    u0 = f"x, y, z.\n\n{m.HDR['HU']}\n{news}{m.ASK['HU']}"
    pacer = g1_lib.Pacer(g1_lib.pace_for(g1_lib.est_tokens(s0, u0, 160)))
    call = pacer.wrap(m.sf)
    print(f"G1/5 inflexp — modell={g1_lib.MODEL}, N={N}, seeds={SEEDS}, 12 cella/seed")

    per_seed = []
    for seed in SEEDS:
        P = {}; CB = {}
        raws = {}
        for c in COUNTRIES:
            P[c] = []; CB[c] = []
            for ti in range(3):
                ps, cb, n, pr_raw, fn_raw = run_cell(call, c, ti, seed)
                P[c].append(ps); CB[c].append(cb)
                raws[f"{c}_{m.MONTHS[ti]}"] = {"price": pr_raw, "fin": fn_raw}
                print(f"  seed {seed} {c} {m.MONTHS[ti]}: ár-score={ps:.3f}/5 cci={cb:+.1f} (n={n}, valós ár {m.PRICE_GT[c][ti]:+.1f})")
        rho_price = [float(spearmanr([P[c][ti] for c in COUNTRIES], [m.PRICE_GT[c][ti] for c in COUNTRIES])[0]) for ti in range(3)]
        rho_cci = [float(spearmanr([CB[c][ti] for c in COUNTRIES], [m.CCI_GT[c][ti] for c in COUNTRIES])[0]) for ti in range(3)]
        delta_dir = {c: {"panel": P[c][1] - P[c][0], "gt": m.PRICE_GT[c][1] - m.PRICE_GT[c][0],
                         "ok": (P[c][1] - P[c][0] > 0) == (m.PRICE_GT[c][1] - m.PRICE_GT[c][0] > 0) or abs(m.PRICE_GT[c][1] - m.PRICE_GT[c][0]) < 2}
                     for c in COUNTRIES}
        per_seed.append({"seed": seed, "price_score": P, "cci_balance": CB,
                         "spearman_price": rho_price, "spearman_cci": rho_cci,
                         "cross_time_delta": delta_dir, "raw": raws})
        print(f"  seed {seed}: Spearman ár {['%+.2f' % r for r in rho_price]} | CCI {['%+.2f' % r for r in rho_cci]}")

    # seed-átlag aggregátum
    Pm = {c: [sum(s["price_score"][c][ti] for s in per_seed) / len(per_seed) for ti in range(3)] for c in COUNTRIES}
    CBm = {c: [sum(s["cci_balance"][c][ti] for s in per_seed) / len(per_seed) for ti in range(3)] for c in COUNTRIES}
    rho_price_m = [float(spearmanr([Pm[c][ti] for c in COUNTRIES], [m.PRICE_GT[c][ti] for c in COUNTRIES])[0]) for ti in range(3)]
    rho_cci_m = [float(spearmanr([CBm[c][ti] for c in COUNTRIES], [m.CCI_GT[c][ti] for c in COUNTRIES])[0]) for ti in range(3)]
    print(f"  SEED-ÁTLAG: Spearman ár {['%+.2f' % r for r in rho_price_m]} | CCI {['%+.2f' % r for r in rho_cci_m]}")

    g1_lib.write_artifact("inflexp/hy3_inflexp.json", {
        "run": "G1/5 inflációs várakozás 4 ország x 3 hó",
        "model": g1_lib.MODEL, "provider": "siliconflow", "date": "2026-07-20",
        "protocol": "run_inflexp_backtest.py változatlanul (N=40, SSR linear + OpenAI-small); rng-seed parametrizálva",
        "flag": "CLEAN", "flag_note": "target-ablakok 2025-09/2025-11/2026-01 >= 2025-08; PL out-of-sample nyelv",
        "corpus_files": {fn_: g1_lib.sha256_file(os.path.join(g1_lib.REPO, fn_)) for fn_ in sorted(set(m.CORP.values()))},
        "gt": {"price_gt": m.PRICE_GT, "cci_gt": m.CCI_GT, "months": m.MONTHS},
        "per_seed": [{k: v for k, v in s.items() if k != "raw"} for s in per_seed],
        "raw_by_seed": {str(s["seed"]): s["raw"] for s in per_seed},
        "seed_mean": {"price_score": Pm, "cci_balance": CBm,
                      "spearman_price": rho_price_m, "spearman_cci": rho_cci_m},
        "flash_reference": {"file": "inflexp_result.json"},
        "cost": pacer.stats(),
    })


if __name__ == "__main__":
    main()
