#!/usr/bin/env python3
"""
EMBEDDING-ÖSSZEVETÉS — számít-e a mérőszalag mérete?
Flash EGYSZER fut (a szabad mondatokért), majd UGYANAZOKON a mondatokon végigmérünk
több embedding-modellt (0.6B / 4B / 8B / OpenAI), SSR linear+softmax → szaldó.
Ground truth: valós CCI ≈ -18,4.
"""
import os
import sys
import json
import random
import statistics
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import run_panel_cci as base
import run_ssr_demo as demo
from plugins import ssr

N = int(os.environ.get("SSR_DEMO_N", "40"))
SF_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
OAI_KEY = os.environ.get("OPENAI_API_KEY", "")


def make_embed(provider, model):
    url = ("https://api.siliconflow.com/v1/embeddings" if provider == "sf"
           else "https://api.openai.com/v1/embeddings")
    key = SF_KEY if provider == "sf" else OAI_KEY

    def _embed(texts):
        payload = {"model": model, "input": list(texts)}
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode("utf-8"))
        items = sorted(data["data"], key=lambda d: d.get("index", 0))
        return np.asarray([it["embedding"] for it in items], dtype=np.float64)
    return _embed


MODELS = [
    ("sf", "Qwen/Qwen3-Embedding-0.6B", "Qwen-0.6B"),
    ("sf", "Qwen/Qwen3-Embedding-4B",   "Qwen-4B"),
    ("sf", "Qwen/Qwen3-Embedding-8B",   "Qwen-8B"),
    ("openai", "text-embedding-3-small", "OpenAI-small"),
    ("openai", "text-embedding-3-large", "OpenAI-large"),
]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "corpus.jsonl"), encoding="utf-8") as f:
        corpus = [json.loads(l) for l in f if l.strip()]
    media_env = base.build_media_context(corpus)
    rng = random.Random(1)
    prompts = [demo.demo_prompt(rng, media_env) for _ in range(N)]
    print(f"Flash (vak) {N} perszóna → szabad mondatok...\n")

    def ask(su):
        try:
            return demo.parse(base.call_model(*su))
        except Exception:
            return ("", None)

    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(ask, prompts))
    sents = [s for s, c in rows if s]
    cats = [c for s, c in rows if c is not None]
    cat_balance = 100 * statistics.mean(cats) if cats else 0.0
    json.dump(sents, open(os.path.join(here, "ssr_demo_sentences.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Mondat: {len(sents)} | RÉGI kategorikus szaldó: {cat_balance:+.1f}  (valós CCI: {base.CCI_WINDOW_MEAN:+.1f})\n")

    anchors = ssr.REFERENCE_SETS_HU["anyagi_varakozas"]
    print(f"{'embedding':14s} {'dim':>5s}  {'linear':>16s}  {'softmax':>16s}")
    print("-" * 60)
    for provider, model, label in MODELS:
        try:
            emb = make_embed(provider, model)
            dim = emb(["teszt"]).shape[1]
            out = {}
            for method in ("linear", "softmax"):
                r = ssr.rate(sents, anchors, method=method, temperature=1.0, embed_fn=emb)
                bal = (r["survey_score"] - 3) / 2 * 100
                top = max(r["survey_pmf"])
                out[method] = (r["survey_score"], bal, top)
            ls, lb, lt = out["linear"]
            ss, sb, st = out["softmax"]
            print(f"{label:14s} {dim:5d}  {ls:4.2f}→{lb:+5.1f}({lt*100:2.0f}%)  {ss:4.2f}→{sb:+5.1f}({st*100:2.0f}%)")
        except Exception as e:  # noqa: BLE001
            print(f"{label:14s}  HIBA: {repr(e)[:50]}")
    print("-" * 60)
    print("Olvasat: a '→' előtt az 1-5 átlagpont, utána a -100..+100 szaldó, zárójelben a")
    print(f"PMF csúcsa (magasabb = élesebb/magabiztosabb mérés). Cél-szaldó ≈ {base.CCI_WINDOW_MEAN:+.1f}.")


if __name__ == "__main__":
    main()
