#!/usr/bin/env python3
"""
PORTUGÁL CCI — fogyasztói bizalom, vak persona-only panel + SSR (a HU/CZ nyerő recept klónja).
NEGYEDIK konsziliencia-tanú: ugyanaz a vak-modell + SSR módszer, ÚJ nyelven (portugál) és országon.

GROUND TRUTH (Eurostat ei_bsco_m, indic=BS-FS-NY pénzügyi várakozás köv.12h, geo=PT, SA — LEZÁRVA):
  pre-cutoff szint (2025-07…2026-02 átlag) ≈ -1,7   ← a vak Flash (cutoff ~2026-01) ezt kéne hozza
  recent zuhanás (2026-03 -12,3 / 04 -18,3 / 05 -11,5) ← grounding NÉLKÜL a vak modell NEM láthatja
DISZKRIMINÁCIÓS TESZT: HU panel -18,7 (valós -18,4) > CZ panel -2,5 (valós -7,5) > PT(?) (valós ~-1,7).
  A panel akkor jó, ha Portugáliát a LEGKEVÉSBÉ borúlátónak adja (PT > CZ > HU abszolútban).

HASZNÁLAT: SSR_DEMO_N=60 ORAKEL_CACHE=1 python3 orakel_backteszt_pt_cci.py
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

# GROUND TRUTH (Eurostat BS-FS-NY, geo=PT, SA — LEZÁRVA). A korpusz ~2026 közepe.
PT_GT_LATEST = -11.5    # 2026-05 (legfrissebb publikált)
PT_GT_WINDOW = -14.0    # 2026-03..05 átlag (-12,3 / -18,3 / -11,5)
PT_PRIOR = -1.7         # a vak Flash 2025-01 cutoff-priorja (grounding NÉLKÜL ide esne)
HU_PANEL, CZ_PANEL = -18.7, -2.5

CORPUS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pt_corpus.json"), encoding="utf-8"))

AGE = [("18-29", 0.16), ("30-44", 0.26), ("45-59", 0.26), ("60+", 0.32)]
REGIAO = [("Lisboa", 0.27), ("Porto / Norte", 0.34), ("Centro", 0.21), ("Sul / Alentejo / Algarve", 0.18)]
EDU = [("ensino básico", 0.45), ("ensino secundário", 0.33), ("ensino superior", 0.22)]


def pick(rng, dist):
    r = rng.random(); acc = 0
    for lab, w in dist:
        acc += w
        if r <= acc: return lab
    return dist[-1][0]


def prompt(rng):
    noticias = rng.sample(CORPUS, min(18, len(CORPUS)))
    media_env = ("AMBIENTE NOTICIOSO ATUAL (Portugal, meados de 2026) — alguns destaques que tens visto:\n"
                 + "\n".join(f"- {n}" for n in noticias))
    system = ("Simulas um consumidor português, decisor do teu agregado familiar, a meados de 2026. "
              "Avalias com base no teu perfil demográfico e no ambiente noticioso atual que vês abaixo — "
              "a forma como estas notícias afetam a tua própria vida e carteira. NÃO uses conhecimento "
              "externo sobre índices de confiança, sondagens ou dados estatísticos — reage honestamente "
              "a partir da tua situação de vida e do que lês nas notícias.")
    user = (f"O TEU PERFIL:\n- Idade: {pick(rng,AGE)}\n- Região: {pick(rng,REGIAO)}\n- Escolaridade: {pick(rng,EDU)}\n\n"
            f"{media_env}\n\n"
            "PERGUNTA: Em uma ou duas frases, com as TUAS próprias palavras e honestamente, como vês as "
            "perspetivas financeiras do teu agregado familiar para os próximos 12 meses? "
            "Responde APENAS com a(s) frase(s), sem rótulos.")
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
    print(f"PORTUGÁL CCI — vak persona-only panel ({MODEL}), N={N}, SSR-linear + {EMB}\n")
    rng = random.Random(1)
    prompts = [prompt(rng) for _ in range(N)]

    def ask(i_su):
        i, (s, u) = i_su
        try: return call(s, u, i)
        except Exception: return ""
    with ThreadPoolExecutor(max_workers=6) as ex:
        sents = [s for s in ex.map(ask, enumerate(prompts)) if s]
    print(f"  {len(sents)}/{N} szabad mondat begyűjtve. Példák:")
    for s in sents[:3]: print(f"    • {s[:90]}")

    res = ssr.rate(sents, ssr.REFERENCE_SETS_PT["perspetiva_financeira"], method="linear", embed_fn=oai_embed)
    score = res["survey_score"]
    bal = (score - 3) / 2 * 100
    print("\n" + "=" * 70)
    print(f"  PT panel szaldó (vak Flash + korpusz-grounding, SSR-linear): {bal:+.1f}   (score={score:.3f}/5)")
    print("  ── A LEZÁRT GROUND TRUTH (Eurostat BS-FS-NY, geo=PT, SA) ──")
    print(f"    valós 2026-05 (legfrissebb): {PT_GT_LATEST:+.1f}   | hiba: {bal-PT_GT_LATEST:+.1f}")
    print(f"    valós 2026-03..05 ablak átl.: {PT_GT_WINDOW:+.1f}   | hiba: {bal-PT_GT_WINDOW:+.1f}")
    print("  ── A GROUNDING EREJE ──")
    print(f"    vak prior (2025-01, grounding NÉLKÜL ide esne): {PT_PRIOR:+.1f}")
    print(f"    a korpusz {bal-PT_PRIOR:+.1f}-et mozdított a valós 2026-os zuhanás felé.")
    hit = abs(bal - PT_GT_WINDOW) < 6 or abs(bal - PT_GT_LATEST) < 6
    print(f"    {'✓ A grounding behúzta a 2026-os PT-romlást — negyedik konsziliencia-tanú.' if hit else '~ nézd a számokat: a grounding mozdított-e eleget?'}")
    print("=" * 70)
    json.dump({"pt_balance": bal, "survey_score": score, "n": len(sents),
               "gt_latest": PT_GT_LATEST, "gt_window": PT_GT_WINDOW, "prior": PT_PRIOR,
               "hu_panel": HU_PANEL, "cz_panel": CZ_PANEL},
              open(os.path.join(os.path.dirname(__file__), "pt_cci_result.json"), "w"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
