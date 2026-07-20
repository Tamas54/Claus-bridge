#!/usr/bin/env python3
"""G1/3. futam — HU/CZ/PT CCI headline (BS-CSMCI ill. BS-FS-NY) Hy3-replikáció.

Három cella, mind a történeti nyerő recepttel (szabad mondat -> SSR linear +
text-embedding-3-small), seeds 1-3:
  G1_CELL=hu : orakel_backteszt_cci/run_ssr_demo.py protokoll (N=40, demo_prompt,
               base.call_model) — a történeti Flash -18,7 a compare_embeddings
               OpenAI-small/linear újramérése a tracked ssr_demo_sentences.json-on
               (itt ugyanazt a Flash-referenciát offline újraszámoljuk, hívás nélkül).
  G1_CELL=cz : orakel_backteszt_cz/run_cci_ssr.py protokoll (N=80, cci_prompt, helyi call).
  G1_CELL=pt : orakel_backteszt_pt_cci.py protokoll (N=60, persona-only prompt).
Flagek: HU 2026-01..03 CLEAN | CZ 2025-07..09 GRAY(2025-07 a cutoff-farokban) |
        PT 2026-03..05 CLEAN.
"""
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g1_lib

CELL = os.environ.get("G1_CELL", "hu")
g1_lib.load_env()
os.environ["ORAKEL_PROVIDER"] = "siliconflow"
os.environ["ORAKEL_PANEL_MODEL"] = g1_lib.MODEL
os.environ.setdefault("SSR_EMB_MODEL", "text-embedding-3-small")

SEEDS = [int(s) for s in os.environ.get("G1_SEEDS", "1,2,3").split(",")]


def run_hu():
    os.environ.setdefault("SSR_DEMO_N", "40")
    hd = os.path.join(g1_lib.REPO, "orakel_backteszt_cci")
    sys.path.insert(0, hd)
    sys.path.insert(0, g1_lib.REPO)
    import run_panel_cci as base    # noqa: E402
    import run_ssr_demo as demo     # noqa: E402
    from plugins import ssr         # noqa: E402

    n = int(os.environ["SSR_DEMO_N"])
    corpus_path = os.path.join(hd, "corpus.jsonl")
    corpus = [json.loads(l) for l in open(corpus_path, encoding="utf-8") if l.strip()]
    media_env = base.build_media_context(corpus)
    s0, u0 = demo.demo_prompt(random.Random(0), media_env)
    pacer = g1_lib.Pacer(g1_lib.pace_for(g1_lib.est_tokens(s0, u0, 120)))
    call = pacer.wrap(base.call_model)
    anchors = ssr.REFERENCE_SETS_HU["anyagi_varakozas"]
    gt = base.CCI_WINDOW_MEAN   # -18.4 (zárolt, tracked)

    per_seed = []
    for seed in SEEDS:
        rng = random.Random(seed)
        prompts = [demo.demo_prompt(rng, media_env) for _ in range(n)]

        def ask(i):
            try:
                return demo.parse(call(*prompts[i]))
            except Exception:  # noqa: BLE001
                return ("", None)

        with ThreadPoolExecutor(max_workers=g1_lib.CONCURRENCY) as ex:
            rows = list(ex.map(ask, range(n)))
        sents = [s for s, c in rows if s]
        cats = [c for s, c in rows if c is not None]
        cat_balance = 100 * sum(cats) / len(cats) if cats else 0.0
        res = ssr.rate(sents, anchors, method="linear", embed_fn=base._oai_embed)
        bal = (res["survey_score"] - 3) / 2 * 100
        per_seed.append({"seed": seed, "n_sent": len(sents), "cat_balance": cat_balance,
                         "ssr_score": res["survey_score"], "ssr_balance": bal,
                         "survey_pmf": res["survey_pmf"], "sentences": sents})
        print(f"  seed {seed}: SSR szaldó {bal:+.1f} | kategorikus {cat_balance:+.1f} (n={len(sents)})")

    # Flash-referencia OFFLINE újramérése a tracked mondatokon (nincs Flash-hívás)
    flash_sents = json.load(open(os.path.join(hd, "ssr_demo_sentences.json"), encoding="utf-8"))
    fres = ssr.rate(flash_sents, anchors, method="linear", embed_fn=base._oai_embed)
    fbal = (fres["survey_score"] - 3) / 2 * 100
    print(f"  Flash-ref (tracked mondatok, OpenAI-small/linear, offline): {fbal:+.1f}")

    mean_bal = sum(s["ssr_balance"] for s in per_seed) / len(per_seed)
    return {
        "run": "G1/3 HU CCI headline (SSR-demó protokoll)",
        "flag": "CLEAN", "flag_note": "target-ablak 2026-01..03 >= 2025-08",
        "protocol": "orakel_backteszt_cci/run_ssr_demo.py demo_prompt + run_panel_cci.call_model, SSR linear + text-embedding-3-small",
        "corpus_files": {"orakel_backteszt_cci/corpus.jsonl": g1_lib.sha256_file(corpus_path)},
        "gt": gt, "per_seed": per_seed, "mean_ssr_balance": mean_bal,
        "abs_err": abs(mean_bal - gt),
        "flash_reference": {"canonical": -18.7,
                            "recomputed_offline_on_tracked_sentences": fbal,
                            "source": "orakel_backteszt_cci/ssr_demo_sentences.json (Flash-mondatok, tracked)"},
        "cost": pacer.stats(), "raw_stream": pacer.raw_stream,
    }


def run_cz():
    os.environ.setdefault("SSR_DEMO_N", "80")
    hd = os.path.join(g1_lib.REPO, "orakel_backteszt_cz")
    sys.path.insert(0, hd)
    sys.path.insert(0, g1_lib.REPO)
    import run_cci_ssr as m         # noqa: E402  (importálja a cz run_panel-t)
    from plugins import ssr         # noqa: E402

    n = int(os.environ["SSR_DEMO_N"])
    corpus_path = os.path.join(hd, "corpus.jsonl")
    corpus = [json.loads(l) for l in open(corpus_path, encoding="utf-8") if l.strip()]
    media_env = m.cz.build_media_context(corpus)
    s0, u0 = m.cci_prompt(random.Random(0), media_env)
    pacer = g1_lib.Pacer(g1_lib.pace_for(g1_lib.est_tokens(s0, u0, 160)))
    call = pacer.wrap(m.call)
    anchors = ssr.REFERENCE_SETS_CZ["financni_vyhled"]
    gt = m.CZ_CCI_MEAN   # -7.53 (zárolt, tracked)

    per_seed = []
    for seed in SEEDS:
        rng = random.Random(seed)
        prompts = [m.cci_prompt(rng, media_env) for _ in range(n)]

        def ask(i):
            try:
                return m.parse(call(prompts[i][0], prompts[i][1], iteration=seed * 100000 + i))
            except Exception:  # noqa: BLE001
                return ("", None)

        with ThreadPoolExecutor(max_workers=g1_lib.CONCURRENCY) as ex:
            rows = list(ex.map(ask, range(n)))
        sents = [s for s, c in rows if s]
        cats = [c for s, c in rows if c is not None]
        cat_balance = 100 * sum(cats) / len(cats) if cats else 0.0
        res = ssr.rate(sents, anchors, method="linear", embed_fn=m.oai_embed)
        bal = (res["survey_score"] - 3) / 2 * 100
        per_seed.append({"seed": seed, "n_sent": len(sents), "cat_balance": cat_balance,
                         "ssr_score": res["survey_score"], "ssr_balance": bal,
                         "survey_pmf": res["survey_pmf"], "sentences": sents})
        print(f"  seed {seed}: SSR szaldó {bal:+.1f} | kategorikus {cat_balance:+.1f} (n={len(sents)})")

    mean_bal = sum(s["ssr_balance"] for s in per_seed) / len(per_seed)
    return {
        "run": "G1/3 CZ CCI headline (SSR n77 protokoll)",
        "flag": "GRAY", "flag_note": "target-ablak 2025-07..09; a 2025-07 a cutoff-farok GRAY sávjában",
        "protocol": "orakel_backteszt_cz/run_cci_ssr.py változatlanul (N=80, cci_prompt, SSR linear + OpenAI-small)",
        "corpus_files": {"orakel_backteszt_cz/corpus.jsonl": g1_lib.sha256_file(corpus_path)},
        "gt": gt, "per_seed": per_seed, "mean_ssr_balance": mean_bal,
        "abs_err": abs(mean_bal - gt),
        "flash_reference": {"file": "orakel_backteszt_cz/cci_ssr_deepseek_ai_DeepSeek_V4_Flash_n77.json",
                            "ssr_balance": -2.5},
        "cost": pacer.stats(), "raw_stream": pacer.raw_stream,
    }


def run_pt():
    os.environ.setdefault("SSR_DEMO_N", "60")
    sys.path.insert(0, g1_lib.REPO)
    import orakel_backteszt_pt_cci as m   # noqa: E402
    from plugins import ssr               # noqa: E402

    n = int(os.environ["SSR_DEMO_N"])
    corpus_path = os.path.join(g1_lib.REPO, "pt_corpus.json")
    s0, u0 = m.prompt(random.Random(0))
    pacer = g1_lib.Pacer(g1_lib.pace_for(g1_lib.est_tokens(s0, u0, 160)))
    call = pacer.wrap(m.call)
    anchors = ssr.REFERENCE_SETS_PT["perspetiva_financeira"]

    per_seed = []
    for seed in SEEDS:
        rng = random.Random(seed)
        prompts = [m.prompt(rng) for _ in range(n)]

        def ask(i):
            try:
                return call(prompts[i][0], prompts[i][1], seed * 100000 + i)
            except Exception:  # noqa: BLE001
                return ""

        with ThreadPoolExecutor(max_workers=g1_lib.CONCURRENCY) as ex:
            sents = [s for s in ex.map(ask, range(n)) if s]
        res = ssr.rate(sents, anchors, method="linear", embed_fn=m.oai_embed)
        bal = (res["survey_score"] - 3) / 2 * 100
        per_seed.append({"seed": seed, "n_sent": len(sents),
                         "ssr_score": res["survey_score"], "ssr_balance": bal,
                         "survey_pmf": res["survey_pmf"], "sentences": sents})
        print(f"  seed {seed}: SSR szaldó {bal:+.1f} (n={len(sents)})")

    mean_bal = sum(s["ssr_balance"] for s in per_seed) / len(per_seed)
    return {
        "run": "G1/3 PT CCI headline (persona-only + SSR)",
        "flag": "CLEAN", "flag_note": "target-ablak 2026-03..05 >= 2025-08",
        "protocol": "orakel_backteszt_pt_cci.py változatlanul (N=60, SSR linear + OpenAI-small)",
        "corpus_files": {"pt_corpus.json": g1_lib.sha256_file(corpus_path)},
        "gt": {"gt_latest": m.PT_GT_LATEST, "gt_window": m.PT_GT_WINDOW, "prior": m.PT_PRIOR},
        "per_seed": per_seed, "mean_ssr_balance": mean_bal,
        "abs_err_window": abs(mean_bal - m.PT_GT_WINDOW),
        "flash_reference": {"file": "pt_cci_result.json", "pt_balance": -28.32},
        "cost": pacer.stats(), "raw_stream": pacer.raw_stream,
    }


def main():
    fn = {"hu": run_hu, "cz": run_cz, "pt": run_pt}[CELL]
    print(f"G1/3 CCI headline — cella={CELL}, modell={g1_lib.MODEL}, seeds={SEEDS}")
    payload = fn()
    payload.update({"model": g1_lib.MODEL, "provider": "siliconflow", "date": "2026-07-20",
                    "seeds_run": SEEDS})
    g1_lib.write_artifact(f"cci_headline/hy3_{CELL}_cci.json", payload)


if __name__ == "__main__":
    main()
