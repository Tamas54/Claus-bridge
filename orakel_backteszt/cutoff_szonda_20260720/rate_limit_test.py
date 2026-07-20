#!/usr/bin/env python3
"""PYTHIA G0a/B — Hy3 rate-limit stresszteszt (óvatos, felfelé lépcsőző).
L1 keret: 10k RPM / 400k TPM. Cél: méretezési szabály a G1 backtest-futamokhoz
(80-240 persona-hívás/futam), NEM a limit ostroma.
Lépcsők: 5 (szekvenciális alapvonal) -> 20 -> 60 -> 120 párhuzamos rövid hívás.
"""
import asyncio, json, os, statistics, time, pathlib

import httpx

HERE = pathlib.Path(__file__).parent
SF_URL = "https://api.siliconflow.com/v1/chat/completions"
KEY = os.environ["SILICONFLOW_API_KEY"]
MODEL = "tencent/Hy3"
PROMPT = ("Sorolj fel három európai fővárost, csak a neveket, vesszővel elválasztva.")

STAGES = [20, 60, 120]
STAGE_PAUSE = 20  # mp a lépcsők között — ne terheljük tartósan a keretet


async def call(client, i):
    t0 = time.monotonic()
    try:
        r = await client.post(SF_URL, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": PROMPT}],
            "temperature": 0, "max_tokens": 24,
            "thinking": {"type": "disabled"}})
        lat = time.monotonic() - t0
        u = (r.json().get("usage") or {}) if r.status_code == 200 else {}
        return {"i": i, "status": r.status_code, "latency_s": round(lat, 3),
                "prompt_tokens": u.get("prompt_tokens"),
                "completion_tokens": u.get("completion_tokens")}
    except Exception as e:
        return {"i": i, "status": "EXC", "latency_s": round(time.monotonic() - t0, 3),
                "error": repr(e)[:150]}


def stats(recs):
    ok = [r for r in recs if r["status"] == 200]
    lats = sorted(r["latency_s"] for r in ok)
    n429 = sum(1 for r in recs if r["status"] == 429)
    nerr = sum(1 for r in recs if r["status"] not in (200, 429))
    out = {"n": len(recs), "ok": len(ok), "n_429": n429, "n_other_err": nerr}
    if lats:
        out.update({
            "lat_p50": round(statistics.median(lats), 2),
            "lat_p90": round(lats[int(len(lats) * 0.9) - 1], 2),
            "lat_max": round(lats[-1], 2),
            "lat_mean": round(statistics.mean(lats), 2)})
    toks = [(r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in ok]
    if toks:
        out["avg_tokens_per_call"] = round(statistics.mean(toks), 1)
    return out


async def main():
    report = {"model": MODEL, "run_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "l1_frame": "10000 RPM / 400000 TPM", "stages": []}
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {KEY}"},
                                 timeout=120) as client:
        # 0. lépcső: szekvenciális alapvonal (5 hívás)
        base = []
        for i in range(5):
            base.append(await call(client, i))
        s = stats(base)
        s["stage"] = "baseline_seq_5"
        report["stages"].append(s)
        print("baseline:", s)
        await asyncio.sleep(5)

        for n in STAGES:
            t0 = time.monotonic()
            recs = await asyncio.gather(*[call(client, i) for i in range(n)])
            wall = time.monotonic() - t0
            s = stats(list(recs))
            s["stage"] = f"parallel_{n}"
            s["wall_s"] = round(wall, 2)
            s["throughput_rpm_equiv"] = round(n / wall * 60, 0)
            report["stages"].append(s)
            print(f"stage {n}:", s)
            (HERE / "rate_limit_raw.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=1))
            await asyncio.sleep(STAGE_PAUSE)

    (HERE / "rate_limit_raw.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1))
    print("kész -> rate_limit_raw.json")

asyncio.run(main())
