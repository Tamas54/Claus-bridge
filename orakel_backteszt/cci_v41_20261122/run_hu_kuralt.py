#!/usr/bin/env python3
"""HU-próba kurált korpusszal (PREREG_HU_KURALT.json) — két változat:
  A = priming + N=60/seed (a helyzetleírás a user-prompt ELEJÉN, ahogy a delphoi._fg_prompt)
  B = kontroll: ugyanez priming NÉLKÜL
Közös: 3 seed, 2026-07…10, a delphoi.COUNTRY_PANEL_CONFIG['HU'] dimenziói és médiacímkéi (a pontosított
közmédia-címkével), persona-szintű étrend (a saját szeletből 10 cím + a közös rétegből 6; „alig”: 16 a közösből),
a G2-recept többi része változatlan (run_panel_v41: system/kérdés/MONDAT+KATEGÓRIA/SSR/embedding).

  flock ~/.cache/claus-sf.lock python3 run_hu_kuralt.py A 2026-07
"""
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_panel_v41 as R  # noqa: E402  (call, sf_embed, parse_cci, szövegek, USAGE, Balance402)
from plugins import delphoi, persona_sampler, ssr  # noqa: E402

PR = json.load(open(os.path.join(HERE, "PREREG_HU_KURALT.json"), encoding="utf-8"))
N = int(PR["panel"]["N_per_seed"])
SEEDS = PR["panel"]["seedek"]
OWN_K, COMMON_K = PR["persona_etrend"]["sajat_szelet"], PR["persona_etrend"]["kozos_reteg"]
CFG = delphoi.COUNTRY_PANEL_CONFIG["HU"]
BUCKET2SLICE = {"baloldali": "bal", "jobboldali": "jobb", "közmédia": "kozmedia", "közösségi": "kozossegi",
                "alig": None}


def build_prompt(month, p, slices, rng, priming):
    m = R.MONTH["hu"][month]
    sl = BUCKET2SLICE[delphoi._media_bucket_map(CFG)[p["media"]]]
    if sl is None:
        heads = rng.sample(slices["kozos"], min(OWN_K + COMMON_K, len(slices["kozos"])))
    else:
        heads = (rng.sample(slices[sl], min(OWN_K, len(slices[sl])))
                 + rng.sample(slices["kozos"], min(COMMON_K, len(slices["kozos"]))))
    rng.shuffle(heads)
    system = R.CCI_SYS["hu"].format(m=m)
    user = (f"{R.profile_block('hu', p)}\n\n{R.NEWS_HDR['hu'].format(m=m)}\n"
            + "\n".join("- " + h for h in heads) + f"\n\n{R.CCI_ASK['hu']}")
    if priming:
        user = f"{priming}\n\n{user}"
    return system, user, sl or "alig"


def main():
    variant, month = sys.argv[1].upper(), sys.argv[2]
    priming = (CFG.get("priming") or "").strip() if variant == "A" else ""
    corpus = json.load(open(os.path.join(HERE, "korpusz_kuralt", f"corpus_hu_{month}.json"), encoding="utf-8"))
    slices = {}
    for it in corpus["items"]:
        slices.setdefault(it["slice"], []).append(it["text"])
    rawdir = os.path.join(HERE, f"raw_hu_kuralt_{variant}")
    os.makedirs(rawdir, exist_ok=True)
    rawpath = os.path.join(rawdir, f"raw_hu_{month}.json")
    raw = json.load(open(rawpath, encoding="utf-8")) if os.path.exists(rawpath) else {}
    t0, per_seed = time.time(), []
    try:
        for seed in SEEDS:
            personas, kl = persona_sampler.sample_personas(delphoi._build_dims(CFG), n=N, seed=seed)
            rng = random.Random(seed)
            prompts = [build_prompt(month, p, slices, rng, priming) for p in personas]
            have = raw.get(str(seed)) or [None] * N

            def ask(i):
                if have[i]:
                    return have[i]
                try:
                    return R.call(prompts[i][0], prompts[i][1])
                except R.Balance402:
                    raise
                except Exception:  # noqa: BLE001
                    return ""

            with ThreadPoolExecutor(max_workers=R.CONC) as ex:
                answers = list(ex.map(ask, range(N)))
            raw[str(seed)] = answers
            json.dump(raw, open(rawpath, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
            rows = [(R.parse_cci("hu", t), prompts[i][2]) for i, t in enumerate(answers) if t]
            sents = [s for (s, c), _ in rows if s]
            cats = [c for (s, c), _ in rows if c is not None]
            res = ssr.rate(sents, R.FIN_ANCHOR["hu"], method="linear", embed_fn=R.sf_embed)
            bal = (res["survey_score"] - 3) / 2 * 100
            catb = 100 * sum(cats) / len(cats) if cats else None
            by_sl = {}
            for (s, sl), pr in zip([(s, sl) for (s, c), sl in rows if s], res["per_response"]):
                by_sl.setdefault(sl, []).append((pr["score"] - 3) / 2 * 100)
            per_seed.append({"seed": seed, "n_answers": len(rows), "n_sent": len(sents), "n_cat": len(cats),
                             "ssr_balance": bal, "cat_balance": catb, "survey_pmf": res["survey_pmf"], "kl": kl,
                             "szelet_szaldo": {k: round(sum(v) / len(v), 2) for k, v in by_sl.items()},
                             "szelet_n": {k: len(v) for k, v in by_sl.items()}})
            print(f"  HU-{variant} {month} seed {seed}: SSR {bal:+.1f} | kat {catb if catb is None else round(catb, 1)}"
                  f" (n={len(sents)}) | tokens {R.USAGE['prompt']}/{R.USAGE['completion']}", flush=True)
    except R.Balance402 as e:
        json.dump(raw, open(rawpath, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        print(f"[!!] {e} — MEGÁLLOK (HU-{variant} {month})", flush=True)
        sys.exit(3)
    vals = [s["ssr_balance"] for s in per_seed]
    mean_v = sum(vals) / len(vals)
    cats = [s["cat_balance"] for s in per_seed if s["cat_balance"] is not None]
    cost = (R.USAGE["prompt"] * R.PRICE_IN + R.USAGE["completion"] * R.PRICE_OUT) / 1e6
    out = {"cell": f"HU {month}", "variant": variant, "priming": priming or None, "N_per_seed": N,
           "model": R.MODEL, "embed_model": R.EMBED_MODEL, "corpus": f"korpusz_kuralt/corpus_hu_{month}.json",
           "per_seed": per_seed, "seed_mean": mean_v,
           "seed_sd": (sum((v - mean_v) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5,
           "cat_mean": sum(cats) / len(cats) if cats else None, "usage_this_run": dict(R.USAGE),
           "cost_usd_this_run": round(cost, 5), "wall_s": round(time.time() - t0, 1)}
    d = os.path.join(HERE, f"cellak_hu_kuralt_{variant}")
    os.makedirs(d, exist_ok=True)
    json.dump(out, open(os.path.join(d, f"v41_hu_{month}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[cella] HU-{variant} {month}: SSR {mean_v:+.2f} (sd {out['seed_sd']:.2f}) | kat {out['cat_mean']} | "
          f"költség ${cost:.4f}", flush=True)


if __name__ == "__main__":
    main()
