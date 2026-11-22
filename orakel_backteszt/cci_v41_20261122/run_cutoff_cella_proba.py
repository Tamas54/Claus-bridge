#!/usr/bin/env python3
"""CCI-backteszt V4.1-Flash — CÉLZOTT CUTOFF-PRÓBA cellánként (a döntő lépés).

Minta: prereg_west_20260721/cutoff_preview_probe.json (Michigan-próba) + cutoff_szonda_20261122/run_probe.py
lágy „B” system-promptja (nem „ne találgass”: a júliusi tanulság szerint a szigorú tiltás teljes elzárkózást okoz).
Minden ország × hónap cellára KÖZVETLENÜL rákérdez az indexértékre és a havi változás irányára, magyarul és angolul
(2 változat), temp=0, 1 hívás/változat, Non-Think. Kontroll-cellák (2024-12, 2025-06): a modell tudás-határán BELÜL
vannak — megmutatják, hogy egyáltalán fel tudja-e idézni az ilyen indexeket.

Pontozás (determinisztikus, AI nélkül): a PREREG.json `proba` szakasza; grade_proba.py.

  flock ~/.cache/claus-sf.lock python3 run_cutoff_cella_proba.py    → proba_valaszok.json
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from run_panel_v41 import SF_KEY, MODEL, PRICE_IN, PRICE_OUT  # noqa: E402

PREREG = json.load(open(os.path.join(HERE, "PREREG.json"), encoding="utf-8"))
P = PREREG["proba"]


def call(system, user):
    body = {"model": MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": 220, "temperature": 0, "thinking": {"type": "disabled"}}
    req = urllib.request.Request("https://api.siliconflow.com/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {SF_KEY}", "Content-Type": "application/json"})
    last = None
    for a in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode())
            u = d.get("usage") or {}
            return {"answer": (d["choices"][0]["message"].get("content") or "").strip(),
                    "pt": u.get("prompt_tokens", 0), "ct": u.get("completion_tokens", 0)}
        except urllib.error.HTTPError as e:
            if e.code == 402:
                print("[!!] SF 402 — egyenleg elfogyott, MEGÁLLOK", flush=True)
                sys.exit(3)
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 ** a)
    return {"answer": None, "error": repr(last)[:200], "pt": 0, "ct": 0}


def main():
    qs = P["kerdesek"]
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(lambda q: {**q, **call(P["system"][q["nyelv"]], q["kerdes"])}, qs))
    pt, ct = sum(r["pt"] for r in res), sum(r["ct"] for r in res)
    out = {"model": MODEL, "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "system": P["system"],
           "tokens": {"prompt": pt, "completion": ct}, "cost_usd": round((pt * PRICE_IN + ct * PRICE_OUT) / 1e6, 5),
           "valaszok": res}
    json.dump(out, open(os.path.join(HERE, "proba_valaszok.json"), "w", encoding="utf-8"), ensure_ascii=False,
              indent=1)
    print(f"[proba] {len(res)} válasz, költség ${out['cost_usd']}")


if __name__ == "__main__":
    main()
