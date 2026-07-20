#!/usr/bin/env python3
"""
HU CCI NOWCAST — vak Flash + havi hír-korpusz + SSR → headline CCI (BS-CSMCI) nowcast.
A vak-backteszt egy hónapja. A korpuszt és a ground truth-ot env-ből kapja → bármely hónapra futtatható.

HASZNÁLAT:
  CORPUS_JSON=corpus_hu_2025_06.json CCI_GT=-29.7 MONTH_LABEL="2025-06" SSR_DEMO_N=60 ORAKEL_CACHE=1 \
    python3 orakel_hu_nowcast.py
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
SF_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
OAI_KEY = os.environ.get("OPENAI_API_KEY", "")
_CACHE = LLMCache() if os.environ.get("ORAKEL_CACHE") == "1" else None
CORPUS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     os.environ.get("CORPUS_JSON", "corpus_hu_2026_05.json")), encoding="utf-8"))
CCI_GT = float(os.environ.get("CCI_GT", "0"))
LABEL = os.environ.get("MONTH_LABEL", "?")

KOR = [("18-29", 0.16), ("30-44", 0.26), ("45-59", 0.26), ("60+", 0.32)]
ISKOLA = [("alapfokú", 0.30), ("középfokú", 0.50), ("felsőfokú", 0.20)]
TELEPULES = [("Budapest", 0.18), ("megyeszékhely / nagyváros", 0.30), ("kisváros", 0.28), ("község / falu", 0.24)]


def pick(rng, dist):
    r = rng.random(); acc = 0
    for lab, w in dist:
        acc += w
        if r <= acc: return lab
    return dist[-1][0]


def prompt(rng):
    hirek = rng.sample(CORPUS, min(18, len(CORPUS)))
    media = ("AKTUÁLIS HÍRKÖRNYEZET (Magyarország) — néhány hír, amit mostanában láttál:\n"
             + "\n".join(f"- {h}" for h in hirek))
    system = ("Egy magyar fogyasztót (háztartási döntéshozót) szimulálsz. A demográfiai jellemződ és az "
              "alább látott aktuális hírkörnyezet alapján értékeled a saját anyagi helyzetedet — ahogyan "
              "ezek a hírek a te életedet és pénztárcádat érintik. NE használj külső tudást konkrét "
              "indexértékekről vagy jövőbeli gazdasági adatokról — a saját élethelyzetedből és a hírekből reagálj, őszintén.")
    user = (f"A TE PROFILOD:\n- Életkor: {pick(rng,KOR)}\n- Iskolai végzettség: {pick(rng,ISKOLA)}\n"
            f"- Lakóhely: {pick(rng,TELEPULES)}\n\n{media}\n\n"
            "KÉRDÉS: Egy-két mondatban, a SAJÁT szavaiddal és őszintén, hogyan látod a háztartásod anyagi "
            "kilátásait a következő 12 hónapra? Csak a mondato(ka)t írd, címke nélkül.")
    return system, user


def call(system, user, i):
    key = None
    if _CACHE is not None:
        key = cache_key(MODEL, {"t": 0.8, "mt": 160}, system, user, i)
        hit = _CACHE.get(key)
        if hit is not None: return hit
    body = {"model": MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": 160, "temperature": 0.8, "thinking": {"type": "disabled"}}
    req = urllib.request.Request("https://api.siliconflow.com/v1/chat/completions",
                                 data=json.dumps(body).encode(), headers={"Authorization": f"Bearer {SF_KEY}", "Content-Type": "application/json"})
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
    print(f"HU CCI NOWCAST — {LABEL} | vak {MODEL} + korpusz ({os.environ.get('CORPUS_JSON')}) + SSR, N={N}")
    rng = random.Random(1)
    prompts = [prompt(rng) for _ in range(N)]

    def ask(i_su):
        i, (s, u) = i_su
        try: return call(s, u, i)
        except Exception: return ""
    with ThreadPoolExecutor(max_workers=6) as ex:
        sents = [s for s in ex.map(ask, enumerate(prompts)) if s]
    print(f"  {len(sents)}/{N} mondat. Példák:")
    for s in sents[:3]: print(f"    • {s[:88]}")

    res = ssr.rate(sents, ssr.REFERENCE_SETS_HU["anyagi_varakozas"], method="linear", embed_fn=oai_embed)
    bal = (res["survey_score"] - 3) / 2 * 100
    print(f"\n  >> {LABEL} NOWCAST szaldó: {bal:+.1f}   |  valós headline CCI: {CCI_GT:+.1f}   |  hiba: {bal-CCI_GT:+.1f}")
    json.dump({"label": LABEL, "nowcast": bal, "cci_gt": CCI_GT, "n": len(sents)},
              open(os.path.join(os.path.dirname(__file__), f"hu_nowcast_{LABEL}.json"), "w"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
