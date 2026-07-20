#!/usr/bin/env python3
"""
TÉMA-HARVESTER — Flash-alone (szöveg, csak cím) generalizációs teszt N csatornán.

A cél (Kommandant döntése: A): az ORÁKULUM = "melyik téma/cím fog menni" jós. A Flash-alone
mindhárom eddigi csatornán verte a drága multimodális ensemble-t (Spearman +0.58…+0.77). Most
azt teszteljük, GENERALIZÁL-e ismeretlen csatornákon (kicsi/közepes/nagy), kézi hangolás NÉLKÜL.

- Flash-only (DeepSeek-V4-Flash, base.call), slate-of-4, 50/35/15 GENERIKUS persona (egy niche-
  leíró megy a sablonba — nincs csatornánkénti kézi tuning → őszinte generalizációs teszt).
- Mérce: Spearman(panel klikk-ráta, valódi /hó) + hit@3 (top-tipp a valódi top-3-ban?).
- Adat: channels_data.json — lista [{key,label,niche,titles:[{t,views,age_mo}]}].

HASZNÁLAT: SSR_DEMO_N=40 N_SEEDS=3 ORAKEL_CACHE=1 python3 -u run_harvester.py
"""
import os, sys, json, random
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_title_test as base   # call (Flash+cache), parse, pick, GENDER, AGE, REGION
from scipy.stats import spearmanr

N = int(os.environ.get("SSR_DEMO_N", "40"))
N_SEEDS = int(os.environ.get("N_SEEDS", "3"))
WORKERS = int(os.environ.get("WORKERS", "6"))
DATA = os.path.join(os.path.dirname(__file__), os.environ.get("CHANNELS_JSON", "channels_data.json"))

_TAIL = ("\n\nWhich ONE of these would you be most likely to click right now? "
         "Answer EXACTLY:\nCHOICE: <number 1-4>\nWHY: <one short sentence>")


def interest_blend(niche):
    return [
        (f"just a casual YouTube viewer here to be entertained, not particularly into {niche}", 0.50),
        (f"genuinely interested in {niche}, but NOT a follower of this channel", 0.35),
        (f"a dedicated fan of {niche} content", 0.15),
    ]


def sys_prompt(niche, g, a, rg, it):
    return (f"You are a regular YouTube user: {g}, {a}, from {rg}, {it}. The algorithm just surfaced "
            f"these {niche} video titles in your feed — you did NOT seek them out. You click on whatever "
            "instantly grabs you right now. Judge ONLY by the titles — no thumbnails, no view counts.")


def user_text(titles, slate):
    block = "\n".join(f"{p+1}. {titles[idx]['t']}" for p, idx in enumerate(slate))
    return "Here are 4 videos from this channel:\n\n" + block + _TAIL


def make_slates(nt, seed):
    rng = random.Random(seed * 97 + 7)
    return [rng.sample(range(nt), 4) for _ in range(N)]


def run_channel(ch):
    titles = ch["titles"]; nt = len(titles); niche = ch["niche"]
    acc_p = [0]*nt; acc_a = [0]*nt
    for seed in range(1, N_SEEDS + 1):
        rng = random.Random(seed)
        ib = interest_blend(niche)
        personas = [(base.pick(rng, base.GENDER), base.pick(rng, base.AGE),
                     base.pick(rng, base.REGION), base.pick(rng, ib)) for _ in range(N)]
        slates = make_slates(nt, seed)
        base_iter = seed * 100_000

        def ask(i):
            g, a, rg, it = personas[i]
            try:
                return base.parse(base.call(sys_prompt(niche, g, a, rg, it), user_text(titles, slates[i]),
                                            iteration=base_iter + i))
            except Exception:
                return (None, "")
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            rows = list(ex.map(ask, range(N)))
        for (choice, _w), slate in zip(rows, slates):
            for idx in slate:
                acc_a[idx] += 1
            if choice and 1 <= choice <= 4:
                acc_p[slate[choice - 1]] += 1

    rate = [acc_p[i]/acc_a[i] if acc_a[i] else 0.0 for i in range(nt)]
    permo = [t["views"]/t["age_mo"] for t in titles]
    order = sorted(range(nt), key=lambda i: -permo[i])
    rho = spearmanr(rate, permo)[0]
    real_top3 = set(order[:3])
    panel_w = max(range(nt), key=lambda i: rate[i])
    panel_top3 = sorted(range(nt), key=lambda i: -rate[i])[:3]
    hit3 = panel_w in real_top3
    overlap = len(set(panel_top3) & real_top3)
    return {"label": ch["label"], "subs": ch.get("subs", "?"), "n_titles": nt,
            "rho": rho, "hit3": hit3, "overlap3": overlap,
            "panel_winner": titles[panel_w]["t"][:50], "real_winner": titles[order[0]]["t"][:50]}


def main():
    if not os.path.exists(DATA):
        print(f"HIÁNYZIK: {DATA}\nFormátum: [{{\"key\":..,\"label\":..,\"subs\":..,\"niche\":..,"
              "\"titles\":[{{\"t\":..,\"views\":..,\"age_mo\":..}}]}}]")
        return
    channels = json.load(open(DATA, encoding="utf-8"))
    print(f"TÉMA-HARVESTER (Flash-alone) — N={N}, {N_SEEDS} seed, {len(channels)} csatorna\n")
    res = []
    for ch in channels:
        r = run_channel(ch)
        res.append(r)
        print(f"  {r['label']:24s} ({r['subs']:>6} sub, {r['n_titles']}db)  "
              f"Spearman={r['rho']:+.2f}  hit@3={'✓' if r['hit3'] else '✗'} (átfedés {r['overlap3']}/3)")
        print(f"      valódi nyerő: {r['real_winner']}\n      panel tippje: {r['panel_winner']}")

    rhos = [r["rho"] for r in res if r["rho"] == r["rho"]]
    hits = sum(1 for r in res if r["hit3"])
    import statistics
    print(f"\n{'='*70}\nÖSSZEGZÉS — {len(res)} csatorna")
    print(f"  átlag Spearman = {statistics.mean(rhos):+.2f} (szórás {statistics.pstdev(rhos):.2f})")
    print(f"  hit@3 = {hits}/{len(res)} csatorna")
    print('='*70)
    json.dump(res, open(os.path.join(os.path.dirname(__file__), "harvester_results.json"), "w",
              encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
