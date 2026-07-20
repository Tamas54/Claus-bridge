#!/usr/bin/env python3
"""Hy3 vs Flash A/B proba az Echolot Agora ORAKEL panel-promptjan.
Azonos personak (seed=42), azonos hirkontextus, azonos poszt.
Merjuk: formatum-validitas (PONTSZAM/INDOK parse), pontszam-eloszlas,
tabor-bontas, szintezis prompt-JSON validitas, latencia, token-hasznalat.
"""
import asyncio, json, os, re, sqlite3, statistics, sys, time

sys.path.insert(0, "/home/tamas1/Hirmagnetmcp")
from echolot_orakel import (sample_personas, _persona_prompt, _parse_persona,
                            _synthesis_prompt, _parse_motifs, _lean_bucket,
                            _persona_label)

SF_URL = "https://api.siliconflow.com/v1/chat/completions"
KEY = os.environ["SILICONFLOW_API_KEY"]
N = 16
MODELS = ["deepseek-ai/DeepSeek-V4-Flash", "tencent/Hy3"]

# --- fixtura: valodi HU Agora-poszt a lokalis DB-bol ---
conn = sqlite3.connect("/home/tamas1/Hirmagnetmcp/echolot.db")
conn.row_factory = sqlite3.Row
post = dict(conn.execute(
    "SELECT title, body FROM agora_post WHERE id='24236fa40de4'").fetchone())
conn.close()

# --- fix hirkontextus a backtest-korpuszbol (2026 marciusi HU hirek) ---
rows = [json.loads(l) for l in open(
    "/home/tamas1/Claus/claus-bridge-mcp/orakel_backteszt/corpus.jsonl")
    if l.strip()]
mar = [r for r in rows if str(r.get("date", "")).startswith("2026-03")][:6] or rows[:6]
CTX = "FRISS HÍRKONTEXTUS (Echolot, elmúlt 14 nap):\n" + "\n".join(
    f"- [{r['source']}] {r['title']}: {(r['lead'] or '')[:140]}" for r in mar)

personas = sample_personas(N, seed=42)
excerpt = post["body"][:1800]


async def chat(client, model, prompt, max_tokens=300, temperature=0.8, retries=3):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature, "max_tokens": max_tokens,
            "thinking": {"type": "disabled"}}
    last = None
    for a in range(retries):
        t0 = time.monotonic()
        try:
            r = await client.post(SF_URL, json=body)
            r.raise_for_status()
            d = r.json()
            content = (d["choices"][0]["message"].get("content") or "").strip()
            u = d.get("usage") or {}
            if content:
                return content, time.monotonic() - t0, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
            last = "empty"
        except Exception as e:
            last = repr(e)[:200]
        await asyncio.sleep(2 ** a)
    raise RuntimeError(f"chat failed: {last}")


async def run_model(model):
    import httpx
    sem = asyncio.Semaphore(8)
    out = {"model": model, "records": [], "errors": 0, "parse_fail": 0,
           "latencies": [], "tin": 0, "tout": 0}

    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {KEY}"},
                                 timeout=120) as client:
        async def one(p):
            async with sem:
                try:
                    text, lat, tin, tout = await chat(
                        client, model, _persona_prompt(p, CTX, post["title"], excerpt))
                    out["latencies"].append(lat)
                    out["tin"] += tin; out["tout"] += tout
                    score, reason = _parse_persona(text)
                    if score is None:
                        out["parse_fail"] += 1
                        return {"p": p, "raw": text[:300], "score": None, "reason": None}
                    return {"p": p, "label": _persona_label(p), "score": score,
                            "reason": reason, "raw": text[:300]}
                except Exception as e:
                    out["errors"] += 1
                    return {"p": p, "score": None, "err": repr(e)[:150]}

        out["records"] = list(await asyncio.gather(*[one(p) for p in personas]))

        ok = [r for r in out["records"] if r.get("score") is not None]
        # szintezis (prompt-JSON, response_format NELKUL)
        if ok:
            try:
                text, lat, tin, tout = await chat(
                    client, model, _synthesis_prompt(
                        [{"label": r["label"], "score": r["score"], "reason": r["reason"]}
                         for r in ok]), max_tokens=400, temperature=0.3)
                out["latencies"].append(lat)
                out["tin"] += tin; out["tout"] += tout
                out["synthesis_raw"] = text[:600]
                out["motifs"] = _parse_motifs(text)
            except Exception as e:
                out["synthesis_err"] = repr(e)[:200]
    return out


async def main():
    results = []
    for m in MODELS:
        t0 = time.monotonic()
        r = await run_model(m)
        r["wall_s"] = round(time.monotonic() - t0, 1)
        results.append(r)

    for r in results:
        ok = [x for x in r["records"] if x.get("score") is not None]
        scores = [x["score"] for x in ok]
        camps = {}
        for x in ok:
            camps.setdefault(_lean_bucket(x["p"]["media"]), []).append(x["score"])
        print("=" * 70)
        print("MODEL:", r["model"], f"wall={r['wall_s']}s")
        print(f"  valid: {len(ok)}/{N}  parse_fail={r['parse_fail']}  errors={r['errors']}")
        if scores:
            print(f"  score: mean={statistics.mean(scores):.1f}  median={statistics.median(scores)}"
                  f"  stdev={statistics.pstdev(scores):.1f}  min={min(scores)} max={max(scores)}")
        for c, v in sorted(camps.items()):
            print(f"    camp {c}: n={len(v)} mean={statistics.mean(v):.1f}")
        lat = r["latencies"]
        if lat:
            lat_s = sorted(lat)
            print(f"  latency: p50={lat_s[len(lat)//2]:.1f}s  p95={lat_s[int(len(lat)*0.95)-1]:.1f}s")
        print(f"  tokens: {r['tin']} in / {r['tout']} out")
        print("  synthesis JSON parsed:", bool(r.get("motifs")), "->", json.dumps(r.get("motifs"), ensure_ascii=False))
        print("  persona-hangok (elso 5 ervenyes):")
        for x in ok[:5]:
            print(f"    [{x['label']}] {x['score']}/100: {x['reason']}")
        bad = [x for x in r["records"] if x.get("score") is None]
        for x in bad[:3]:
            print("    PARSE-FAIL raw:", (x.get("raw") or x.get("err", ""))[:200])

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "agora_ab_results.json"), "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)

asyncio.run(main())
