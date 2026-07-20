#!/usr/bin/env python3
"""CZ CCI NOWCAST — vak Flash + havi cseh hír-korpusz + SSR → headline CCI (BS-CSMCI) nowcast.
HASZNÁLAT: CORPUS_JSON=corpus_cz_2026_01.json CCI_GT=1.1 MONTH_LABEL=2026-01 SSR_DEMO_N=60 ORAKEL_CACHE=1 python3 orakel_cz_nowcast.py
"""
import os, sys, json, time, random, urllib.request
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv; load_dotenv(".env")
except Exception: pass
import numpy as np
from plugins import ssr
from plugins.llm_cache import LLMCache, cache_key

N = int(os.environ.get("SSR_DEMO_N", "60"))
EMB = os.environ.get("SSR_EMB_MODEL", "text-embedding-3-small")
MODEL = os.environ.get("ORAKEL_PANEL_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
SF_KEY = os.environ.get("SILICONFLOW_API_KEY", ""); OAI_KEY = os.environ.get("OPENAI_API_KEY", "")
_CACHE = LLMCache() if os.environ.get("ORAKEL_CACHE") == "1" else None
CORPUS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.environ.get("CORPUS_JSON", "corpus_cz_2026_01.json")), encoding="utf-8"))
CCI_GT = float(os.environ.get("CCI_GT", "0")); LABEL = os.environ.get("MONTH_LABEL", "?")

VEK = [("18-29", 0.16), ("30-44", 0.26), ("45-59", 0.26), ("60+", 0.32)]
VZDELANI = [("základní", 0.20), ("středoškolské", 0.55), ("vysokoškolské", 0.25)]
KRAJ = [("Praha", 0.12), ("Čechy (mimo Prahu)", 0.46), ("Morava a Slezsko", 0.42)]


def pick(rng, dist):
    r = rng.random(); acc = 0
    for lab, w in dist:
        acc += w
        if r <= acc: return lab
    return dist[-1][0]


def prompt(rng):
    zpravy = rng.sample(CORPUS, min(18, len(CORPUS)))
    media = ("AKTUÁLNÍ ZPRAVODAJSKÉ PROSTŘEDÍ (Česko) — několik zpráv, které jsi v poslední době viděl/a:\n"
             + "\n".join(f"- {z}" for z in zpravy))
    system = ("Simuluješ českého spotřebitele, který rozhoduje o rozpočtu své domácnosti. Na základě svého "
              "demografického profilu a aktuálního zpravodajského prostředí níže hodnotíš finanční situaci své "
              "domácnosti — jak se tě tyto zprávy dotýkají. Nepoužívej externí znalosti o indexech nebo budoucích "
              "ekonomických datech — reaguj upřímně podle své vlastní životní situace a zpráv.")
    user = (f"TVŮJ PROFIL:\n- Věk: {pick(rng,VEK)}\n- Vzdělání: {pick(rng,VZDELANI)}\n- Region: {pick(rng,KRAJ)}\n\n"
            f"{media}\n\nOTÁZKA: V jedné nebo dvou větách, vlastními slovy a upřímně, jak vidíš finanční vyhlídky "
            "své domácnosti na příštích 12 měsíců? Napiš pouze větu(y), bez označení.")
    return system, user


def call(system, user, i):
    key = None
    if _CACHE is not None:
        key = cache_key(MODEL, {"t": 0.8, "mt": 160}, system, user, i)
        hit = _CACHE.get(key)
        if hit is not None: return hit
    body = {"model": MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": 160, "temperature": 0.8, "thinking": {"type": "disabled"}}
    req = urllib.request.Request("https://api.siliconflow.com/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {SF_KEY}", "Content-Type": "application/json"})
    last = None
    for a in range(5):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                txt = (json.loads(r.read().decode())["choices"][0]["message"].get("content") or "").strip()
            if txt:
                if _CACHE is not None: _CACHE.set(key, txt, model=MODEL)
                return txt
        except Exception as e: last = e
        if a < 4: time.sleep(2**a + random.random())
    raise RuntimeError(f"call failed: {last}")


def oai_embed(texts):
    payload = {"model": EMB, "input": list(texts)}
    req = urllib.request.Request("https://api.openai.com/v1/embeddings", data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {OAI_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    return np.asarray([it["embedding"] for it in sorted(data["data"], key=lambda d: d.get("index", 0))], dtype=float)


def main():
    print(f"CZ CCI NOWCAST — {LABEL} | vak {MODEL} + korpusz ({os.environ.get('CORPUS_JSON')}) + SSR, N={N}")
    rng = random.Random(1); prompts = [prompt(rng) for _ in range(N)]
    def ask(i_su):
        i, (s, u) = i_su
        try: return call(s, u, i)
        except Exception: return ""
    with ThreadPoolExecutor(max_workers=6) as ex:
        sents = [s for s in ex.map(ask, enumerate(prompts)) if s]
    print(f"  {len(sents)}/{N} vět. Příklady:")
    for s in sents[:3]: print(f"    • {s[:88]}")
    res = ssr.rate(sents, ssr.REFERENCE_SETS_CZ["financni_vyhled"], method="linear", embed_fn=oai_embed)
    bal = (res["survey_score"] - 3) / 2 * 100
    print(f"\n  >> {LABEL} NOWCAST szaldó: {bal:+.1f}   |  valós headline CCI: {CCI_GT:+.1f}   |  hiba: {bal-CCI_GT:+.1f}")
    json.dump({"label": LABEL, "nowcast": bal, "cci_gt": CCI_GT, "n": len(sents)},
              open(os.path.join(os.path.dirname(__file__), f"cz_nowcast_{LABEL}.json"), "w"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
