#!/usr/bin/env python3
"""OLCSÓ SWARM — általános cutoff-szonda futtató (2026-11-22), a 07-20-i `run_cutoff_probe.py` B-elicitálásával.

A 07-20-i tanulság szerint: lágy „B” system-prompt (a szigorú „ne találgass” kollapszust okoz), temp=0 (ahol a modell
engedi), 1 hívás/kérdés (a 2 ismétlés temp=0-nál 100%-ban egyezett). A bankok a futtatás ELŐTT commitolva.

  python3 run_probe.py sf <model>          → resp_<model>.json   (SiliconFlow, Non-Think; gpt-oss: reasoning low)
  python3 run_probe.py anthropic <model>   → resp_<model>.json   (Message Batches, 50% ár; effort low)
"""
import asyncio, json, os, pathlib, sys, time

HERE = pathlib.Path(__file__).resolve().parent
OLD = HERE.parent / "cutoff_szonda_20260720"
SYSTEM_B = ("Segítőkész asszisztens vagy. Válaszolj tömören, legfeljebb 3 mondatban a betanított "
            "tudásod alapján. Ha valóban nem ismered a választ, írd: NEM TUDOM.")
BANKS = [OLD / "cutoff_events.json", OLD / "cutoff_events_boundary.json", OLD / "cutoff_events_boundary2.json",
         HERE / "cutoff_events_ladder_2023_2024.json", HERE / "cutoff_events_echolot.json"]


def load_questions(only=None):
    qs, seen = [], set()
    for p in BANKS:
        if only and p.name not in only:
            continue
        if not p.exists():
            print(f"[!] hiányzó bank: {p.name}", file=sys.stderr)
            continue
        b = json.loads(p.read_text())
        for ev in b.get("events", []):
            if ev["id"] in seen:
                continue
            seen.add(ev["id"])
            qs.append({"id": ev["id"], "bank": p.name, "bucket": ev.get("bucket"), "event_month": ev["event_month"],
                       "question": ev["question"]})
        for v in (b.get("featured_hu_card") or {}).get("variants", []):
            if v["id"] not in seen:
                seen.add(v["id"])
                qs.append({"id": v["id"], "bank": p.name, "bucket": "featured-HU", "event_month": "2026-04",
                           "question": v["question"]})
    return qs


async def run_sf(model, qs):
    import httpx
    key = os.environ["SILICONFLOW_API_KEY"]
    sem = asyncio.Semaphore(6)

    async def one(client, q):
        body = {"model": model, "messages": [{"role": "system", "content": SYSTEM_B},
                                             {"role": "user", "content": q["question"]}],
                "temperature": 0, "max_tokens": 350}
        if model.startswith("openai/gpt-oss"):
            body.update({"reasoning_effort": "low", "max_tokens": 1500})
        else:
            body["thinking"] = {"type": "disabled"}
        last = None
        for a in range(4):
            async with sem:
                try:
                    r = await client.post("https://api.siliconflow.com/v1/chat/completions", json=body)
                    if r.status_code != 429:
                        r.raise_for_status()
                        d = r.json()
                        txt = (d["choices"][0]["message"].get("content") or "").strip()
                        u = d.get("usage") or {}
                        if txt:
                            return {**q, "answer": txt, "prompt_tokens": u.get("prompt_tokens"),
                                    "completion_tokens": u.get("completion_tokens")}
                        last = "empty"
                    else:
                        last = "429"
                except Exception as e:  # noqa: BLE001
                    last = repr(e)[:200]
            await asyncio.sleep(2 ** a * 2)
        return {**q, "answer": None, "error": last}

    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=180) as client:
        return await asyncio.gather(*[one(client, q) for q in qs])


def run_anthropic(model, qs):
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    client = anthropic.Anthropic()
    reqs = [Request(custom_id=q["id"].replace(".", "_")[:64], params=MessageCreateParamsNonStreaming(
        model=model, max_tokens=1024, system=SYSTEM_B, output_config={"effort": "low"},
        messages=[{"role": "user", "content": q["question"]}])) for q in qs]
    batch = client.messages.batches.create(requests=reqs)
    print(f"batch {batch.id} ({len(reqs)} kérés)", flush=True)
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        time.sleep(30)
    by_id = {q["id"].replace(".", "_")[:64]: q for q in qs}
    out = []
    for res in client.messages.batches.results(batch.id):
        q = by_id[res.custom_id]
        if res.result.type == "succeeded":
            m = res.result.message
            txt = "".join(bl.text for bl in m.content if bl.type == "text").strip()
            u = m.usage
            out.append({**q, "answer": txt or None, "stop_reason": m.stop_reason,
                        "prompt_tokens": u.input_tokens, "completion_tokens": u.output_tokens})
        else:
            out.append({**q, "answer": None, "error": res.result.type})
    return out, batch.id


def main():
    prov, model = sys.argv[1], sys.argv[2]
    only = sys.argv[3:] or None            # opcionális: csak ezek a bankok (fájlnév)
    qs = load_questions(only)
    meta = {"model": model, "provider": prov, "system_prompt": SYSTEM_B, "banks": [p.name for p in BANKS if p.exists()],
            "n_questions": len(qs), "run_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if prov == "sf":
        res = asyncio.run(run_sf(model, qs))
        meta.update({"temp": 0, "thinking": "disabled" if not model.startswith("openai/gpt-oss") else "reasoning_effort=low"})
    else:
        res, bid = run_anthropic(model, qs)
        meta.update({"batch_id": bid, "effort": "low", "thinking": "adaptive (alap)", "batch_50pct": True})
    res = sorted(res, key=lambda r: (r["event_month"], r["id"]))
    meta["n_errors"] = sum(1 for r in res if r.get("answer") is None)
    meta["prompt_tokens"] = sum(r.get("prompt_tokens") or 0 for r in res)
    meta["completion_tokens"] = sum(r.get("completion_tokens") or 0 for r in res)
    suffix = "" if not only else "_" + "+".join(sorted(o.replace("cutoff_events_", "").replace(".json", "") for o in only))
    (HERE / f"resp_{model.replace('/', '_')}{suffix}.json").write_text(
        json.dumps({"meta": meta, "responses": res}, ensure_ascii=False, indent=1))
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
