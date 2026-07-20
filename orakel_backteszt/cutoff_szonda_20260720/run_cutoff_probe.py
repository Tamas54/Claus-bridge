#!/usr/bin/env python3
"""PYTHIA G0a — Hy3 cutoff-szonda futtató.
Minden kérdés: temp=0, 2 ismétlés, tencent/Hy3, thinking:disabled, sima szöveg.
Nyers válaszok: cutoff_responses.json
"""
import asyncio, json, os, time, pathlib

import httpx

HERE = pathlib.Path(__file__).parent
SF_URL = "https://api.siliconflow.com/v1/chat/completions"
KEY = os.environ["SILICONFLOW_API_KEY"]
MODEL = "tencent/Hy3"
REPS = 2
CONC = 8

SYSTEM = (
    "Tudás-szonda vagy. Válaszolj KIZÁRÓLAG a betanított (paraméteres) tudásod alapján, "
    "tömören, legfeljebb 3 mondatban. Ne találgass: ha az esemény a tudásod lezárása "
    "(cutoff) utánra esik, vagy nem ismered a tényt, a válaszod legyen pontosan: NEM TUDOM. "
    "A válasz végén új sorban add meg: BIZONYOSSÁG: [ismert tény | következtetés | nem tudom]"
)

bank = json.loads((HERE / "cutoff_events.json").read_text())
questions = []
for ev in bank["events"]:
    questions.append({"id": ev["id"], "bucket": ev["bucket"],
                      "event_month": ev["event_month"], "question": ev["question"]})
for v in bank["featured_hu_card"]["variants"]:
    questions.append({"id": v["id"], "bucket": "featured-HU", "event_month": "2026-04",
                      "question": v["question"]})

sem = asyncio.Semaphore(CONC)


async def one(client, q, rep):
    body = {"model": MODEL,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": q["question"]}],
            "temperature": 0, "max_tokens": 350,
            "thinking": {"type": "disabled"}}
    last = None
    for attempt in range(4):
        async with sem:
            t0 = time.monotonic()
            try:
                r = await client.post(SF_URL, json=body)
                if r.status_code == 429:
                    last = "429"
                    await asyncio.sleep(2 ** attempt * 2)
                    continue
                r.raise_for_status()
                d = r.json()
                content = (d["choices"][0]["message"].get("content") or "").strip()
                u = d.get("usage") or {}
                if content:
                    return {"id": q["id"], "rep": rep, "bucket": q["bucket"],
                            "event_month": q["event_month"], "question": q["question"],
                            "answer": content, "latency_s": round(time.monotonic() - t0, 2),
                            "prompt_tokens": u.get("prompt_tokens"),
                            "completion_tokens": u.get("completion_tokens")}
                last = "empty"
            except Exception as e:
                last = repr(e)[:200]
        await asyncio.sleep(2 ** attempt)
    return {"id": q["id"], "rep": rep, "bucket": q["bucket"],
            "event_month": q["event_month"], "question": q["question"],
            "answer": None, "error": last}


async def main():
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {KEY}"},
                                 timeout=180) as client:
        tasks = [one(client, q, rep) for q in questions for rep in range(1, REPS + 1)]
        results = await asyncio.gather(*tasks)
    results.sort(key=lambda r: (r["id"], r["rep"]))
    out = {"meta": {"model": MODEL, "reps": REPS, "temp": 0,
                    "run_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "n_questions": len(questions), "n_calls": len(results),
                    "n_errors": sum(1 for r in results if r.get("answer") is None)},
           "responses": results}
    (HERE / "cutoff_responses.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    print(f"kész: {out['meta']}")

asyncio.run(main())
