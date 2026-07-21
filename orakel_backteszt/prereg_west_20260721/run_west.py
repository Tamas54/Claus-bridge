#!/usr/bin/env python3
"""OPERATION G2-WEST — futtató: US/UK/DE panel (CCI + inflexp) Hy3-preview-n.

Protokoll = a G2 FR/IT nyerő receptjének klónja (run_g2.py), EN/DE nyelvre:
  cci    : persona-only + korpusz-grounding, SENTENCE+CATEGORY kettős formátum,
           N=60/seed, SSR linear + text-embedding-3-small;
           anchor: ssr.REFERENCE_SETS_EN['financial_outlook'] (US/UK),
                   ssr.REFERENCE_SETS_DE['finanzielle_aussicht'] (DE).
  inflexp: 2-soros PRICES/FINANCES ill. PREISE/FINANZEN protokoll, N=40/seed;
           ár-anchor: ssr.REFERENCE_SETS_PRICE['EN'] / ['DE'].
Personák: persona_sampler.sample_country_personas US/UK/DE (G3 országbővítés).
Modell: KIZÁRÓLAG tencent/Hy3-preview (a tencent/Hy3 2026-07-21-től 402;
Flash-hívás TILOS). GT + szabályok: gt_LOCKED_west.json (+amendment_1), commit
a futások ELŐTT.

HASZNÁLAT (cellánként, előtérben):
  WEST_CELL=us_A WEST_MODE=cci ORAKEL_CACHE=1 python3 -u run_west.py
"""
import datetime
import json
import os
import random
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

WDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(WDIR, "..", "g1_hy3_20260720"))
import g1_lib  # noqa: E402  (Pacer + pace_for + load_env + sha256 — változatlan)

g1_lib.load_env()
sys.path.insert(0, g1_lib.REPO)
import numpy as np  # noqa: E402
from plugins import persona_sampler, ssr  # noqa: E402
from plugins.llm_cache import LLMCache, cache_key  # noqa: E402

MODEL = "tencent/Hy3-preview"   # NEM g1_lib.MODEL (tencent/Hy3 = 402 2026-07-21-től)
CELL = os.environ.get("WEST_CELL", "us_A")
MODE = os.environ.get("WEST_MODE", "cci")
SEEDS = [int(s) for s in os.environ.get("WEST_SEEDS", "1,2,3").split(",")]
SF_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
OAI_KEY = os.environ.get("OPENAI_API_KEY", "")
_CACHE = LLMCache() if os.environ.get("ORAKEL_CACHE") == "1" else None

CELL_META = {
    "us_A":   {"country": "US", "lang": "en", "nat": "American", "cn": "United States"},
    "us_B":   {"country": "US", "lang": "en", "nat": "American", "cn": "United States"},
    "uk_jul": {"country": "UK", "lang": "en", "nat": "British", "cn": "United Kingdom"},
    "de_jul": {"country": "DE", "lang": "de", "nat": None, "cn": "Deutschland"},
}
META = CELL_META[CELL]
LANG, COUNTRY = META["lang"], META["country"]
N = 60 if MODE == "cci" else 40
MONTH = {"en": "July 2026", "de": "Juli 2026"}[LANG]

CORPUS_FILE = os.path.join(WDIR, f"corpus_{CELL}.json")
CORPUS = json.load(open(CORPUS_FILE, encoding="utf-8"))
NEWS = [it["text"] for it in CORPUS["items"]]

PROFILE_HDR = {"en": ("YOUR PROFILE:", "Age", "Where you live", "Education", "Media"),
               "de": ("DEIN PROFIL:", "Alter", "Wohnort", "Bildung", "Medien")}
NEWS_HDR = {
    "en": "YOUR CURRENT MEDIA ENVIRONMENT ({cn}, {m}) — some headlines you have seen recently:",
    "de": "DEIN AKTUELLES MEDIENUMFELD ({cn}, {m}) — einige Schlagzeilen, die du zuletzt gesehen hast:",
}

CCI_SYS = {
    "en": ("You are simulating a {nat} consumer, the financial decision-maker of your household, "
           "in {m}. You react solely on the basis of your demographic profile and the media "
           "environment below — the way these news items touch your own life and your wallet. "
           "Do NOT use any outside knowledge about confidence indices, polls or statistical "
           "data — answer honestly from your own life situation."),
    "de": ("Du simulierst einen deutschen Verbraucher, der die finanziellen Entscheidungen "
           "seines Haushalts trifft, im {m}. Du reagierst ausschließlich auf Grundlage deines "
           "demografischen Profils und des Medienumfelds unten — so, wie diese Nachrichten dein "
           "eigenes Leben und deinen Geldbeutel berühren. Nutze KEIN externes Wissen über "
           "Vertrauensindizes, Umfragen oder statistische Daten — antworte ehrlich aus deiner "
           "eigenen Lebenssituation."),
}
CCI_ASK = {
    "en": ("How do you see your household's financial situation over the next 12 months?\n"
           "Answer EXACTLY in this format, in 2 lines:\n"
           "SENTENCE: <1-2 sentences in your own words, honestly>\n"
           "CATEGORY: <better|same|worse>"),
    "de": ("Wie siehst du die finanzielle Lage deines Haushalts in den nächsten 12 Monaten?\n"
           "Antworte GENAU in diesem Format, in 2 Zeilen:\n"
           "SATZ: <1-2 Sätze in deinen eigenen Worten, ehrlich>\n"
           "KATEGORIE: <besser|gleich|schlechter>"),
}
CCI_CAT = {"en": {"better": 1, "same": 0, "worse": -1},
           "de": {"besser": 1, "gleich": 0, "schlechter": -1}}
CCI_MARK = {"en": ("SENTENCE", "CATEGORY"), "de": ("SATZ", "KATEGORIE")}

INF_SYS = {
    "en": ("You are simulating a {nat} consumer in {m}. You react according to your profile "
           "and the news below, as they affect you personally. Do not use external index data."),
    "de": ("Du simulierst einen deutschen Verbraucher im {m}. Du reagierst gemäß deinem Profil "
           "und den Nachrichten unten, so wie sie dich persönlich betreffen. Nutze keine "
           "externen Indexdaten."),
}
INF_ASK = {
    "en": ("\n\nQUESTION — answer EXACTLY in 2 lines, in your own words:\n"
           "PRICES: <1-2 sentences: how do YOU EXPECT prices in the shops, energy and fuel to "
           "develop over the next 12 months?>\n"
           "FINANCES: <1-2 sentences: how do you see your household's financial situation over "
           "the next 12 months?>"),
    "de": ("\n\nFRAGE — antworte GENAU in 2 Zeilen, in deinen eigenen Worten:\n"
           "PREISE: <1-2 Sätze: wie ERWARTEST DU, dass sich die Preise in den Geschäften, für "
           "Energie und Kraftstoff in den nächsten 12 Monaten entwickeln?>\n"
           "FINANZEN: <1-2 Sätze: wie siehst du die finanzielle Lage deines Haushalts in den "
           "nächsten 12 Monaten?>"),
}
INF_MARK = {"en": ("PRICES", "FINANCES"), "de": ("PREISE", "FINANZEN")}
NEWS_HDR_INF = {"en": "RECENT NEWS:", "de": "AKTUELLE NACHRICHTEN:"}

FIN_ANCHOR = {"en": ssr.REFERENCE_SETS_EN["financial_outlook"],
              "de": ssr.REFERENCE_SETS_DE["finanzielle_aussicht"]}
PRICE_ANCHOR = {"en": ssr.REFERENCE_SETS_PRICE["EN"], "de": ssr.REFERENCE_SETS_PRICE["DE"]}


def call(system, user, i):
    key = None
    if _CACHE is not None:
        key = cache_key(MODEL, {"t": 0.8, "mt": 160}, system, user, i)
        hit = _CACHE.get(key)
        if hit is not None:
            return hit
    body = {"model": MODEL, "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": user}],
            "max_tokens": 160, "temperature": 0.8, "thinking": {"type": "disabled"}}
    req = urllib.request.Request("https://api.siliconflow.com/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {SF_KEY}",
                                          "Content-Type": "application/json"})
    last = None
    for a in range(5):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                txt = (json.loads(r.read().decode())["choices"][0]["message"].get("content") or "").strip()
            if txt:
                if _CACHE is not None:
                    _CACHE.set(key, txt, model=MODEL)
                return txt
        except Exception as e:  # noqa: BLE001
            last = e
        if a < 4:
            time.sleep(2 ** a + random.random())
    raise RuntimeError(f"call failed: {last}")


def oai_embed(texts):
    req = urllib.request.Request("https://api.openai.com/v1/embeddings",
                                 data=json.dumps({"model": "text-embedding-3-small",
                                                  "input": list(texts)}).encode(),
                                 headers={"Authorization": f"Bearer {OAI_KEY}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode())
    return np.asarray([it["embedding"] for it in sorted(d["data"], key=lambda x: x.get("index", 0))],
                      dtype=float)


def profile_block(p):
    hdr, a, s, e, m = PROFILE_HDR[LANG]
    return f"{hdr}\n- {a}: {p['age']}\n- {s}: {p['settlement']}\n- {e}: {p['edu']}\n- {m}: {p['media']}"


def build_prompt(p, rng, mode):
    news = "\n".join("- " + x for x in rng.sample(NEWS, min(16, len(NEWS))))
    fmt = {"m": MONTH, "nat": META["nat"], "cn": META["cn"]}
    if mode == "cci":
        system = CCI_SYS[LANG].format(**fmt)
        user = (f"{profile_block(p)}\n\n{NEWS_HDR[LANG].format(**fmt)}\n{news}\n\n"
                f"{CCI_ASK[LANG]}")
    else:
        system = INF_SYS[LANG].format(**fmt)
        user = f"{profile_block(p)}\n\n{NEWS_HDR_INF[LANG]}\n{news}{INF_ASK[LANG]}"
    return system, user


def parse_cci(txt):
    m1, m2 = CCI_MARK[LANG]
    ms = re.search(rf"{m1}\s*:?\s*(.+?)(?:\n\s*{m2}\s*:|$)", txt, re.S | re.I)
    mc = re.search(rf"{m2}\s*:?\s*([A-Za-zäöüßÄÖÜ']+)", txt, re.I)
    sent = (ms.group(1).strip() if ms else "").replace("\n", " ")
    cat = None
    if mc:
        cat = CCI_CAT[LANG].get(mc.group(1).strip().lower())
    return sent, cat


def parse_inf(txt):
    m = INF_MARK[LANG]
    p = re.search(rf"{m[0]}\s*:?\s*(.+?)(?:\n|{m[1]}|$)", txt, re.I | re.S)
    f = re.search(rf"{m[1]}\s*:?\s*(.+)$", txt, re.I | re.S)
    return ((p.group(1).strip().replace("\n", " ") if p else ""),
            (f.group(1).strip().replace("\n", " ") if f else ""))


def main():
    print(f"G2-WEST {CELL} {MODE} — modell={MODEL}, N={N}/seed, seeds={SEEDS}, "
          f"korpusz n={CORPUS['n']} ablak={CORPUS['corpus_window']}")

    s0, u0 = build_prompt({"age": "x", "settlement": "y", "edu": "z", "media": "w"},
                          random.Random(0), MODE)
    pacer = g1_lib.Pacer(g1_lib.pace_for(g1_lib.est_tokens(s0, u0, 160)))
    pcall = pacer.wrap(call)

    per_seed = []
    for seed in SEEDS:
        personas, kl = persona_sampler.sample_country_personas(COUNTRY, n=N, seed=seed)
        rng = random.Random(seed)
        prompts = [build_prompt(p, rng, MODE) for p in personas]

        def ask(i):
            try:
                return pcall(prompts[i][0], prompts[i][1], seed * 100000 + i)
            except Exception:  # noqa: BLE001
                return ""

        with ThreadPoolExecutor(max_workers=g1_lib.CONCURRENCY) as ex:
            raw = list(ex.map(ask, range(N)))

        if MODE == "cci":
            rows = [parse_cci(t) for t in raw if t]
            sents = [s for s, c in rows if s]
            cats = [c for s, c in rows if c is not None]
            cat_balance = 100 * sum(cats) / len(cats) if cats else None
            res = ssr.rate(sents, FIN_ANCHOR[LANG], method="linear", embed_fn=oai_embed)
            bal = (res["survey_score"] - 3) / 2 * 100
            per_seed.append({"seed": seed, "n_sent": len(sents), "cat_balance": cat_balance,
                             "ssr_score": res["survey_score"], "ssr_balance": bal,
                             "survey_pmf": res["survey_pmf"], "kl": kl, "sentences": sents})
            print(f"  seed {seed}: SSR szaldó {bal:+.1f} | kategorikus "
                  f"{(cat_balance if cat_balance is not None else float('nan')):+.1f} (n={len(sents)})")
        else:
            rows = [parse_inf(t) for t in raw if t]
            pr = [p for p, f in rows if p]
            fn = [f for p, f in rows if f]
            pres = ssr.rate(pr, PRICE_ANCHOR[LANG], method="linear", embed_fn=oai_embed)
            fres = ssr.rate(fn, FIN_ANCHOR[LANG], method="linear", embed_fn=oai_embed)
            fbal = (fres["survey_score"] - 3) / 2 * 100
            per_seed.append({"seed": seed, "n_price": len(pr), "price_score": pres["survey_score"],
                             "price_pmf": pres["survey_pmf"], "fin_balance": fbal, "kl": kl,
                             "price_sentences": pr, "fin_sentences": fn})
            print(f"  seed {seed}: ár-score {pres['survey_score']:.3f}/5 | fin-szaldó {fbal:+.1f} "
                  f"(n={len(pr)})")

    if MODE == "cci":
        vals = [s["ssr_balance"] for s in per_seed]
    else:
        vals = [s["price_score"] for s in per_seed]
    mean_v = sum(vals) / len(vals)
    sd = (sum((v - mean_v) ** 2 for v in vals) / len(vals)) ** 0.5

    payload = {
        "run": f"G2-WEST {CELL} {MODE}",
        "model": MODEL, "provider": "siliconflow",
        "timestamp": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "flag": "CLEAN", "flag_note": "korpusz-ablak >= 2026-06, target 2026-07/08 >= 2025-08",
        "protocol": ("cci: G2-klon persona-only + korpusz-grounding, SENTENCE+CATEGORY, N=60, "
                     "SSR linear + text-embedding-3-small" if MODE == "cci" else
                     "inflexp: G2-klon 2-soros protokoll, N=40, SSR linear + OpenAI-small"),
        "anchors": ("ssr.REFERENCE_SETS_EN['financial_outlook']" if LANG == "en" and MODE == "cci"
                    else "ssr.REFERENCE_SETS_DE['finanzielle_aussicht']" if MODE == "cci"
                    else f"ssr.REFERENCE_SETS_PRICE['{LANG.upper()}'] + fin-anchor"),
        "personas": f"persona_sampler.sample_country_personas('{COUNTRY}', n={N}) — G3 orszagbovites",
        "corpus_files": {os.path.basename(CORPUS_FILE): g1_lib.sha256_file(CORPUS_FILE)},
        "corpus_n": CORPUS["n"], "corpus_window": CORPUS["corpus_window"],
        "corpus_lean": CORPUS["lean_dist"], "corpus_cats": CORPUS["cat_dist"],
        "gt": None, "gt_note": "gt_LOCKED_west.json — backteszt: Michigan 2026-07P 54.4 (us_A level-sign); prereg-targetek GT-je a kiadaskor toltendo",
        "per_seed": per_seed, "seed_mean": mean_v, "seed_sd": sd,
        "seeds_run": SEEDS, "cost": pacer.stats(),
    }
    out = os.path.join(WDIR, f"hy3p_{CELL}_{MODE}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[g2-west] artefakt: {out}")


if __name__ == "__main__":
    main()
