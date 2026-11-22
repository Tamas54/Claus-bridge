#!/usr/bin/env python3
"""OLCSÓ SWARM — KÖZMÉDIA-KAPU (2026-11-22, a Kommandant: „a médiatérkép elavult: a közmédia mára Tisza-párti”).

A kérdés: a modell követi-e a HU-konfig leírását („közmédiát követ (köztévé — a mindenkori kormányt, most a Tiszát
támogatja)”), vagy a régi priorja szerint a közmédia-nézőt Fidesz-oldalinak „tudja”? Ugyanaz a gép, mint a
`run_fg_skala.py` (élő DELPHOI-prompt, persona-sampler, SSR), Tisza-kormányzati lépésekre.

Mérőszám: a közmédia-szegmens HELYE a jobboldali (Fidesz-média) és a baloldali/liberális szegmens között:
  hely = (K − J) / (B − J)   — 0 = a jobboldalival egyezik (RÉGI TÉRKÉP), 1 = a baloldalival (az ellenzékből jött
  kormánypárti tábor) egyezik. Csak ott értelmes, ahol a modell egyáltalán szétválasztja a tábort (|B − J| ≥ 0,15).
Előre rögzített szabály: hely < 0,5 (a lépések átlagában) = a modell a régi térképet követi → KIZÁRÓ ok.

  python3 run_kozmedia.py <model> [...]   → kozmedia_<model>.json
"""
import asyncio, json, pathlib, sys, time

import httpx
import numpy as np

import run_fg_skala as fg
from plugins import delphoi, persona_sampler, ssr

HERE = pathlib.Path(__file__).resolve().parent
N = 90
STIMULI = [
    ("K1_euro", "A Magyar Péter vezette Tisza-kormány bejelentette: Magyarország 2030. január 1-jén bevezeti az eurót, "
                "és már jövőre belép az ERM-II árfolyam-mechanizmusba."),
    ("K2_szuver", "A Tisza-kormány törvényjavaslata megszünteti a Szuverenitásvédelmi Hivatalt, és a vagyonát a "
                  "korrupcióellenes ügyészség kapja."),
    ("K3_ukrajna", "A Tisza-kormány feloldja Magyarország vétóját, és támogatja Ukrajna uniós csatlakozási "
                   "tárgyalásainak következő szakaszát."),
]


async def run(model):
    cfg = delphoi.COUNTRY_PANEL_CONFIG["HU"]
    personas, _ = persona_sampler.sample_personas(delphoi._build_dims(cfg), n=N, seed=7)
    bucket = delphoi._media_bucket_map(cfg)
    jobs = [(sid, p, delphoi._fg_prompt(p, cfg, "concept", stim)) for sid, stim in STIMULI for p in personas]
    sem = asyncio.Semaphore(fg.CONC)
    t0 = time.time()
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {fg.KEY}"}, timeout=120) as client:
        res = await asyncio.gather(*[fg.ask(client, sem, model, pr) for _, _, pr in jobs])
        ok = [i for i, r in enumerate(res) if r["text"]]
        e_r = await fg.embed(client, [res[i]["text"] for i in ok])
        e_a = await fg.embed(client, list(delphoi.REFERENCE_SETS_APPEAL["hu"]))
    sc = ssr.score_pmf(ssr.compute_pmf(np.asarray(e_r, float), np.asarray(e_a, float), method="linear"))
    rows = [{"stim": jobs[i][0], "segment": bucket.get(jobs[i][1].get("media", ""), "egyéb"), "score": float(sc[j]),
             "text": res[i]["text"]} for j, i in enumerate(ok)]
    per = {}
    for sid, _ in STIMULI:
        seg = {}
        for s in ("baloldali", "jobboldali", "közmédia", "közösségi", "alig"):
            v = [r["score"] for r in rows if r["stim"] == sid and r["segment"] == s]
            seg[s] = {"n": len(v), "atlag": round(float(np.mean(v)), 4) if v else None}
        B, J, K = seg["baloldali"]["atlag"], seg["jobboldali"]["atlag"], seg["közmédia"]["atlag"]
        szet = (B - J) if None not in (B, J) else None
        hely = round((K - J) / szet, 3) if szet and abs(szet) >= 0.15 and K is not None else None
        per[sid] = {"szegmensek": seg, "B_minus_J": round(szet, 4) if szet is not None else None, "kozmedia_hely": hely}
    helyek = [v["kozmedia_hely"] for v in per.values() if v["kozmedia_hely"] is not None]
    allB = np.mean([r["score"] for r in rows if r["segment"] == "baloldali"])
    allJ = np.mean([r["score"] for r in rows if r["segment"] == "jobboldali"])
    allK = np.mean([r["score"] for r in rows if r["segment"] == "közmédia"])
    pooled = round(float((allK - allJ) / (allB - allJ)), 3) if abs(allB - allJ) >= 0.15 else None
    verdikt = ("NEM VÁLASZTJA SZÉT A TÁBOROKAT (B≈J) — a kapu nem dönthető" if pooled is None else
               "RÉGI TÉRKÉP (a közmédia-néző a jobboldalihoz áll közelebb) — KIZÁRÓ" if pooled < 0.5 else
               "ÚJ TÉRKÉP (a közmédia-néző a kormánypárti tábor oldalán)")
    pt, ct = sum(r["pt"] for r in res), sum(r["ct"] for r in res)
    pin, pout = fg.PRICES.get(model, (0, 0))
    out = {"model": model, "n_personas": N, "n_calls": len(jobs), "kitoltes": round(len(ok) / len(jobs), 4),
           "lepesenkent": per, "kozmedia_hely_atlag": round(float(np.mean(helyek)), 3) if helyek else None,
           "kozmedia_hely_osszevont": pooled, "osszevont": {"B": round(float(allB), 4), "J": round(float(allJ), 4),
                                                            "K": round(float(allK), 4)},
           "verdikt": verdikt, "koltseg_usd": round((pt * pin + ct * pout) / 1e6, 5),
           "fal_s": round(time.time() - t0, 1), "run_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows}
    (HERE / f"kozmedia_{model.replace('/', '_')}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k not in ("rows",)}, ensure_ascii=False), flush=True)


async def main():
    for m in sys.argv[1:]:
        await run(m)

if __name__ == "__main__":
    asyncio.run(main())
