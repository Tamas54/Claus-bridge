#!/usr/bin/env python3
"""OLCSÓ SWARM — a HU 2026 választás előtti vak backteszt modellenként (2026-11-22).

A `../run_panel.py` (ORAKEL-II, 06-15) VÁLTOZATLAN promptjaival és pontozásával: `build_media_context`,
`make_persona_prompt` (seed → demográfia), `parse_vote`, `decided_share`; N=80/seed, temp 0.8, Non-Think, max 30 token.
Egyetlen eltérés: a gpt-oss család gondolkodó modell (a thinking nem kapcsolható ki), ott max 1500 token +
reasoning_effort=low, különben a gondolkodás megeszi a 30 tokent.

Pluszban mér: PARSE-arányt (szigorú: „SZAVAZAT: <ismert opció>” sor; laza: a parse_vote ismert kategóriát talál),
API-hibát (az eredeti ezt csendben „bizonytalan”-nak számolta — itt külön), tokent → valós költség (listaár).

  python3 run_hu_panel.py <model> <seed> [<seed> ...]   → hu_panel_<model>_s<seed>.json
"""
import asyncio, json, os, pathlib, random, re, sys, time

HERE = pathlib.Path(__file__).resolve().parent
BT = HERE.parent
sys.path.insert(0, str(BT))
os.chdir(BT)
import httpx  # noqa: E402
import run_panel as rp  # noqa: E402

KEY = os.environ["SILICONFLOW_API_KEY"]
N = 80
GT_OFFICIAL = 14.57           # NVI, listás: Tisza 53,18 − Fidesz 38,61
GT_SUBSET = (20.3, 22.7)      # a panel által modellezett részhalmaz (belföldi, két-párti, digitális) — 06-15
CALLS_PER_MIN = float(os.environ.get("HU_CALLS_PER_MIN", "10"))   # ~20k token/hívás → ~200k TPM, a keret fele
CONC = int(os.environ.get("HU_CONC", "6"))
PRICES = {"tencent/Hy3": (0.132, 0.528), "deepseek-ai/DeepSeek-V4.1-Flash": (0.15, 0.6),
          "Qwen/Qwen3-8B": (0.06, 0.06), "Qwen/Qwen3.5-9B": (0.10, 0.15),
          "openai/gpt-oss-120b": (0.05, 0.45), "openai/gpt-oss-20b": (0.04, 0.18)}
STRICT = re.compile(r"SZAVAZAT:\s*\**\s*(Fidesz|Tisza|Mi Hazánk|DK|egyéb|bizonytalan|nem szavazna)", re.I)
KNOWN = re.compile(r"fidesz|tisza|hazánk|hazank|\bdk\b|demokratikus|bizonytalan|nem szav|egyéb|egyeb", re.I)


class Pacer:
    def __init__(self, per_min):
        self.gap = 60.0 / per_min
        self.next = time.monotonic()
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            now = time.monotonic()
            if now < self.next:
                await asyncio.sleep(self.next - now)
            self.next = max(now, self.next) + self.gap


def body_for(model, system, user):
    b = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if model.startswith("openai/gpt-oss"):
        b.update({"max_tokens": 1500, "reasoning_effort": "low", "temperature": 0.8})
    else:
        b.update({"max_tokens": 30, "temperature": 0.8, "thinking": {"type": "disabled"}})
    return b


async def ask(client, sem, pacer, model, system, user):
    last = None
    for a in range(4):
        await pacer.wait()
        async with sem:
            try:
                r = await client.post("https://api.siliconflow.com/v1/chat/completions", json=body_for(model, system, user))
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
        await asyncio.sleep(2 ** a * 2 + random.random())
    return {"text": None, "err": last, "pt": 0, "ct": 0}


async def run_seed(model, seed, corpus):
    rng = random.Random(seed)
    env = rp.build_media_context(corpus)
    prompts = [rp.make_persona_prompt(rng, env) for _ in range(N)]
    sem, pacer = asyncio.Semaphore(CONC), Pacer(CALLS_PER_MIN)
    t0 = time.time()
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {KEY}"}, timeout=180) as client:
        res = await asyncio.gather(*[ask(client, sem, pacer, model, s, u) for s, u in prompts])
    tally, strict_ok, loose_ok, errors = {}, 0, 0, 0
    for r in res:
        if r["text"] is None:
            errors += 1
            vote = "bizonytalan"          # az eredeti gép szabálya (a hibát külön is számoljuk)
        else:
            vote = rp.parse_vote(r["text"])
            strict_ok += bool(STRICT.search(r["text"]))
            loose_ok += bool(KNOWN.search(r["text"]))
        tally[vote] = tally.get(vote, 0) + 1
    shares = rp.decided_share(tally)
    gap = shares.get("Tisza", 0) - shares.get("Fidesz-KDNP", 0)
    pt, ct = sum(r["pt"] for r in res), sum(r["ct"] for r in res)
    pin, pout = PRICES.get(model, (0, 0))
    out = {"model": model, "seed": seed, "n": N, "tally": tally, "decided_shares": shares, "tisza_fidesz_gap": gap,
           "parse_strict": round(strict_ok / N, 4), "parse_loose": round(loose_ok / N, 4), "api_errors": errors,
           "decided_n": sum(v for k, v in tally.items() if k in ("Fidesz-KDNP", "Tisza", "Mi Hazánk", "DK", "egyéb")),
           "tokens": {"prompt": pt, "completion": ct}, "cost_usd": round((pt * pin + ct * pout) / 1e6, 4),
           "wall_s": round(time.time() - t0, 1), "run_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "raw": [r["text"] for r in res]}
    (HERE / f"hu_panel_{model.replace('/', '_')}_s{seed}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "raw"}, ensure_ascii=False), flush=True)
    return out


async def main():
    model, seeds = sys.argv[1], [int(s) for s in sys.argv[2:]]
    corpus = [json.loads(l) for l in open(BT / "corpus.jsonl", encoding="utf-8") if l.strip()]
    for s in seeds:
        await run_seed(model, s, corpus)

asyncio.run(main())
