#!/usr/bin/env python3
"""OLCSÓ SWARM — a DELPHOI fókuszcsoport-panel skála-eltolása modellenként (2026-11-22).

UGYANAZ a gép, mint élesben: `plugins.delphoi` HU-konfig + `persona_sampler` (seed 42) + `_fg_prompt` (concept) +
`pollster._chat` (Non-Think, temp 0.8, 260 token) + SSR (Qwen3-Embedding-8B, `ssr.compute_pmf` linear). Csak a modell
változik. A korpusz-háttér (ctx_line) KIMARAD: a modellek összevetése a stimulusra adott reakción múlik, nem a napi
hírkörnyezeten (az élő DB nem kell hozzá).

Mérjük: kitöltés (nem üres, nem hibás válasz), SSR-átlag stimulusonként (1–5), eltolás a Hy3-hoz képest, a stimulusok
és a média-szegmensek SORRENDJE a Hy3-éhoz képest (Spearman), token-használat → valós költség (listaár).

  python3 run_fg_skala.py <model> [<model> ...]     → fg_skala_<model>.json
"""
import asyncio, json, os, pathlib, sys, time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
import httpx  # noqa: E402
import numpy as np  # noqa: E402
from plugins import delphoi, persona_sampler, ssr  # noqa: E402

KEY = os.environ["SILICONFLOW_API_KEY"]
SF = "https://api.siliconflow.com/v1"
N = int(os.environ.get("FG_N", "30"))
SEED = 42
TEMP = 0.8
CONC = int(os.environ.get("FG_CONC", "8"))

#: előre rögzített, vegyes előjelű stimulusok (a szimulátor tipikus lépései: törvény, ár, nyilatkozat)
STIMULI = [
    ("S1_utdij", "A kormány 2027-től havi 5000 forintos útdíjat vezet be minden személyautóra."),
    ("S2_szja", "A kormány 2027-től a 25 év alattiak után a 30 év alattiakra is kiterjeszti az szja-mentességet."),
    ("S3_nyugdij", "A nyugdíjkorhatárt 2030-ig fokozatosan 67 évre emelik."),
    ("S4_vasut", "A MÁV 2027-től országos havi 9000 forintos bérletet vezet be minden vonatra és helyközi buszra."),
    ("S5_atom", "A kormány felfüggeszti a Paks II. atomerőmű építését, és a pénzt napelemparkokra fordítja."),
]

PRICES = {  # USD / 1M token (be, ki) — SiliconFlow árlista, 2026-11-22
    "tencent/Hy3": (0.132, 0.528), "deepseek-ai/DeepSeek-V4.1-Flash": (0.15, 0.6),
    "Qwen/Qwen3-8B": (0.06, 0.06), "Qwen/Qwen3.5-9B": (0.10, 0.15),
    "openai/gpt-oss-120b": (0.05, 0.45), "openai/gpt-oss-20b": (0.04, 0.18),
    "zai-org/GLM-5.3-Flash": (0.15, 0.5), "Qwen/Qwen3-14B": (0.07, 0.28),
    "Qwen/Qwen3-30B-A3B-Instruct-2507": (0.09, 0.30), "Qwen/Qwen2.5-7B-Instruct": (0.05, 0.05),
}
EMBED_PRICE = 0.04  # USD/1M — Qwen3-Embedding-8B (becslés; a költségben külön sor)


def body_for(model, prompt):
    b = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 260, "temperature": TEMP}
    if model.startswith("openai/gpt-oss"):
        b["reasoning_effort"] = "low"
        b["max_tokens"] = 1200   # a gondolkodás is ebből él
    else:
        b["thinking"] = {"type": "disabled"}
    return b


async def ask(client, sem, model, prompt):
    last = None
    for a in range(4):
        async with sem:
            try:
                r = await client.post(f"{SF}/chat/completions", json=body_for(model, prompt))
                if r.status_code == 429:
                    last = "429"
                else:
                    r.raise_for_status()
                    d = r.json()
                    txt = (d["choices"][0]["message"].get("content") or "").strip()
                    u = d.get("usage") or {}
                    if txt:
                        return {"text": txt, "pt": u.get("prompt_tokens", 0), "ct": u.get("completion_tokens", 0)}
                    last = "empty"
            except Exception as e:  # noqa: BLE001
                last = repr(e)[:160]
        await asyncio.sleep(2 ** a + 0.5)
    return {"text": None, "err": last, "pt": 0, "ct": 0}


async def embed(client, texts):
    out = []
    for i in range(0, len(texts), 32):
        r = await client.post(f"{SF}/embeddings", json={"model": delphoi.EMBED_MODEL_SF, "input": texts[i:i + 32]})
        r.raise_for_status()
        out += [d["embedding"] for d in sorted(r.json()["data"], key=lambda x: x.get("index", 0))]
    return out


def spearman(a, b):
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


async def run(model):
    cfg = delphoi.COUNTRY_PANEL_CONFIG["HU"]
    personas, _ = persona_sampler.sample_personas(delphoi._build_dims(cfg), n=N, seed=SEED)
    bucket = delphoi._media_bucket_map(cfg)
    jobs = [(sid, p, delphoi._fg_prompt(p, cfg, "concept", stim)) for sid, stim in STIMULI for p in personas]
    sem = asyncio.Semaphore(CONC)
    t0 = time.time()
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {KEY}"}, timeout=120) as client:
        res = await asyncio.gather(*[ask(client, sem, model, pr) for _, _, pr in jobs])
        ok = [i for i, r in enumerate(res) if r["text"]]
        texts = [res[i]["text"] for i in ok]
        anchors = delphoi.REFERENCE_SETS_APPEAL["hu"]
        e_r = await embed(client, texts)
        e_a = await embed(client, list(anchors))
    scores = ssr.score_pmf(ssr.compute_pmf(np.asarray(e_r, float), np.asarray(e_a, float), method="linear"))
    rows = []
    for j, i in enumerate(ok):
        sid, p, _ = jobs[i]
        rows.append({"stim": sid, "persona": p["id"], "segment": bucket.get(p.get("media", ""), "egyéb"),
                     "score": float(scores[j]), "text": res[i]["text"]})
    pt = sum(r["pt"] for r in res); ct = sum(r["ct"] for r in res)
    pin, pout = PRICES.get(model, (None, None))
    cost = (pt * pin + ct * pout) / 1e6 if pin is not None else None
    per_stim = {sid: round(float(np.mean([r["score"] for r in rows if r["stim"] == sid])), 4) for sid, _ in STIMULI}
    segs = sorted({r["segment"] for r in rows})
    per_seg = {s: round(float(np.mean([r["score"] for r in rows if r["segment"] == s])), 4) for s in segs}
    out = {"model": model, "n_personas": N, "n_calls": len(jobs), "n_ok": len(ok),
           "kitoltes": round(len(ok) / len(jobs), 4),
           "hibak": sorted({r.get("err") for r in res if not r["text"]} - {None}),
           "overall": round(float(np.mean([r["score"] for r in rows])), 4), "per_stim": per_stim, "per_segment": per_seg,
           "tokens": {"prompt": pt, "completion": ct, "per_call_prompt": round(pt / max(1, len(ok)), 1),
                      "per_call_completion": round(ct / max(1, len(ok)), 1)},
           "koltseg_usd": round(cost, 5) if cost is not None else None,
           "panel180_usd": round(cost / len(jobs) * 180, 5) if cost is not None else None,
           "fal_s": round(time.time() - t0, 1), "run_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows}
    ref = HERE / "fg_skala_tencent_Hy3.json"
    if model != "tencent/Hy3" and ref.exists():
        h = json.loads(ref.read_text())
        common = [s for s in per_stim if s in h["per_stim"]]
        cseg = [s for s in per_seg if s in h["per_segment"]]
        out["vs_hy3"] = {
            "eltolas_overall": round(out["overall"] - h["overall"], 4),
            "eltolas_appeal_0_100": round((out["overall"] - h["overall"]) / 4 * 100, 2),
            "eltolas_stimulusonkent": {s: round(per_stim[s] - h["per_stim"][s], 4) for s in common},
            "spearman_stimulus": round(spearman([per_stim[s] for s in common], [h["per_stim"][s] for s in common]), 3),
            "spearman_szegmens": round(spearman([per_seg[s] for s in cseg], [h["per_segment"][s] for s in cseg]), 3)
            if len(cseg) >= 3 else None}
    safe = model.replace("/", "_")
    (HERE / f"fg_skala_{safe}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, ensure_ascii=False))


async def main():
    for m in sys.argv[1:]:
        await run(m)

if __name__ == "__main__":
    asyncio.run(main())
