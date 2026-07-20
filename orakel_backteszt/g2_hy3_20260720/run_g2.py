#!/usr/bin/env python3
"""OPERATION PYTHIA — G2 futtató: FR + IT vak backteszt (CCI headline + inflexp).

Protokoll = a G1 nyerő receptjeinek klónja, csak ország/nyelv cserél:
  cci    : persona-only + korpusz-grounding, MONDAT+KATEGORIA kettős formátum
           (HU demo_prompt / PT persona-only mintája), N=60/seed, SSR linear +
           text-embedding-3-small; anchor: ssr.REFERENCE_SETS_FR['perspective_financiere']
           ill. ssr.REFERENCE_SETS_IT['prospettiva_finanziaria'] (G0d-ben élesítve).
  inflexp: run_inflexp_backtest.py 2-soros protokollja (PRIX/PREZZI + FINANCES/FINANZE),
           N=40/seed; ár-anchor: delphoi.REFERENCE_SETS_PRICE_EXTRA['fr'/'it'].
Personák: plugins.persona_sampler.sample_country_personas (COUNTRY_QUOTAS FR/IT —
G2-ben feltöltve a delphoi nowcast-grade Eurostat-marginálisaiból, media-dimenzióval).
Modell: KIZÁRÓLAG tencent/Hy3 (SF, thinking:disabled, response_format nélkül), temp=0.8.
GT: gt_LOCKED_fr_it.json (pre-regisztrált, commit 74c5090). Flag: minden cella CLEAN.

HASZNÁLAT (cellánként, előtérben):
  G2_COUNTRY=FR G2_MODE=cci G2_WINDOW=2026-04 ORAKEL_CACHE=1 python3 -u run_g2.py
"""
import json
import os
import random
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

G2DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(G2DIR, "..", "g1_hy3_20260720"))
import g1_lib  # noqa: E402  (Pacer + pace_for + load_env + sha256 — G1 változatlan)

g1_lib.load_env()
sys.path.insert(0, g1_lib.REPO)
import numpy as np  # noqa: E402
from plugins import delphoi, persona_sampler, ssr  # noqa: E402
from plugins.llm_cache import LLMCache, cache_key  # noqa: E402

COUNTRY = os.environ.get("G2_COUNTRY", "FR").upper()
MODE = os.environ.get("G2_MODE", "cci")
WINDOW = os.environ.get("G2_WINDOW", "2026-04")
SEEDS = [int(s) for s in os.environ.get("G2_SEEDS", "1,2,3").split(",")]
MODEL = g1_lib.MODEL
SF_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
OAI_KEY = os.environ.get("OPENAI_API_KEY", "")
_CACHE = LLMCache() if os.environ.get("ORAKEL_CACHE") == "1" else None

GT = json.load(open(os.path.join(G2DIR, "gt_LOCKED_fr_it.json"), encoding="utf-8"))
N = 60 if MODE == "cci" else 40
LANG = {"FR": "fr", "IT": "it"}[COUNTRY]
CORPUS_FILE = os.path.join(G2DIR, f"corpus_{LANG}_{WINDOW}.json")
CORPUS = json.load(open(CORPUS_FILE, encoding="utf-8"))
NEWS = [it["text"] for it in CORPUS["items"]]

MONTH_HU = {"2026-04": {"fr": "avril 2026", "it": "aprile 2026"},
            "2026-05": {"fr": "mai 2026", "it": "maggio 2026"},
            "2026-06": {"fr": "juin 2026", "it": "giugno 2026"}}

PROFILE_HDR = {"fr": ("TON PROFIL:", "Âge", "Lieu de vie", "Études", "Médias"),
               "it": ("IL TUO PROFILO:", "Età", "Dove vivi", "Istruzione", "Media")}
NEWS_HDR = {
    "fr": "TON ENVIRONNEMENT MÉDIATIQUE ACTUEL (France, {m}) — quelques titres que tu as vus récemment :",
    "it": "IL TUO AMBIENTE MEDIATICO ATTUALE (Italia, {m}) — alcuni titoli che hai visto di recente:",
}

CCI_SYS = {
    "fr": ("Tu simules un consommateur français, décideur de ton foyer, en {m}. Tu réagis "
           "uniquement à partir de ton profil démographique et de l'environnement médiatique "
           "ci-dessous — la façon dont ces nouvelles touchent ta propre vie et ton portefeuille. "
           "N'utilise AUCUNE connaissance externe sur des indices de confiance, des sondages ou "
           "des données statistiques — réponds honnêtement depuis ta situation de vie."),
    "it": ("Simuli un consumatore italiano, che decide per la propria famiglia, in {m}. Reagisci "
           "solo in base al tuo profilo demografico e all'ambiente mediatico qui sotto — a come "
           "queste notizie toccano la tua vita e il tuo portafoglio. NON usare conoscenze esterne "
           "su indici di fiducia, sondaggi o dati statistici — rispondi onestamente dalla tua "
           "situazione di vita."),
}
CCI_ASK = {
    "fr": ("Comment vois-tu la situation financière de ton foyer pour les 12 prochains mois ?\n"
           "Réponds EXACTEMENT sous cette forme, en 2 lignes :\n"
           "PHRASE: <1-2 phrases avec tes propres mots, honnêtement>\n"
           "CATEGORIE: <meilleure|pareille|pire>"),
    "it": ("Come vedi la situazione economica della tua famiglia nei prossimi 12 mesi?\n"
           "Rispondi ESATTAMENTE in questo formato, in 2 righe:\n"
           "FRASE: <1-2 frasi con parole tue, onestamente>\n"
           "CATEGORIA: <migliore|uguale|peggiore>"),
}
CCI_CAT = {"fr": {"meilleure": 1, "pareille": 0, "pire": -1},
           "it": {"migliore": 1, "uguale": 0, "peggiore": -1}}
CCI_MARK = {"fr": ("PHRASE", "CATEGORIE"), "it": ("FRASE", "CATEGORIA")}

INF_SYS = {
    "fr": ("Tu simules un consommateur français en {m}. Tu réagis selon ton profil et "
           "l'actualité ci-dessous, telle qu'elle t'affecte. N'utilise pas de données "
           "d'indices externes."),
    "it": ("Simuli un consumatore italiano nel periodo {m}. Reagisci in base al tuo profilo "
           "e alle notizie qui sotto, per come ti toccano. Non usare dati di indici esterni."),
}
INF_ASK = {
    "fr": ("\n\nQUESTION — réponds EXACTEMENT en 2 lignes, avec tes propres mots :\n"
           "PRIX: <1-2 phrases : comment T'ATTENDS-TU à l'évolution des prix en magasin, de "
           "l'énergie et du carburant dans les 12 prochains mois ?>\n"
           "FINANCES: <1-2 phrases : comment vois-tu la situation financière de ton foyer dans "
           "les 12 prochains mois ?>"),
    "it": ("\n\nDOMANDA — rispondi ESATTAMENTE in 2 righe, con parole tue:\n"
           "PREZZI: <1-2 frasi: come TI ASPETTI che evolvano i prezzi nei negozi, dell'energia "
           "e dei carburanti nei prossimi 12 mesi?>\n"
           "FINANZE: <1-2 frasi: come vedi la situazione economica della tua famiglia nei "
           "prossimi 12 mesi?>"),
}
INF_MARK = {"fr": ("PRIX", "FINANCES"), "it": ("PREZZI", "FINANZE")}
NEWS_HDR_INF = {"fr": "ACTUALITÉS RÉCENTES :", "it": "NOTIZIE RECENTI:"}

FIN_ANCHOR = {"FR": ssr.REFERENCE_SETS_FR["perspective_financiere"],
              "IT": ssr.REFERENCE_SETS_IT["prospettiva_finanziaria"]}
PRICE_ANCHOR = {"FR": delphoi.REFERENCE_SETS_PRICE_EXTRA["fr"],
                "IT": delphoi.REFERENCE_SETS_PRICE_EXTRA["it"]}


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
    m = MONTH_HU[WINDOW][LANG]
    news = "\n".join("- " + x for x in rng.sample(NEWS, min(16, len(NEWS))))
    if mode == "cci":
        system = CCI_SYS[LANG].format(m=m)
        user = (f"{profile_block(p)}\n\n{NEWS_HDR[LANG].format(m=m)}\n{news}\n\n"
                f"{CCI_ASK[LANG]}")
    else:
        system = INF_SYS[LANG].format(m=m)
        user = f"{profile_block(p)}\n\n{NEWS_HDR_INF[LANG]}\n{news}{INF_ASK[LANG]}"
    return system, user


def parse_cci(txt):
    m1, m2 = CCI_MARK[LANG]
    ms = re.search(rf"{m1}\s*:?\s*(.+?)(?:\n\s*{m2}\s*:|$)", txt, re.S | re.I)
    mc = re.search(rf"{m2}\s*:?\s*([A-Za-zàéèêùìòóüçÀÉ']+)", txt, re.I)
    sent = (ms.group(1).strip() if ms else "").replace("\n", " ")
    cat = None
    if mc:
        w = mc.group(1).strip().lower()
        cat = CCI_CAT[LANG].get(w)
    return sent, cat


def parse_inf(txt):
    m = INF_MARK[LANG]
    p = re.search(rf"{m[0]}\s*:?\s*(.+?)(?:\n|{m[1]}|$)", txt, re.I | re.S)
    f = re.search(rf"{m[1]}\s*:?\s*(.+)$", txt, re.I | re.S)
    return ((p.group(1).strip().replace("\n", " ") if p else ""),
            (f.group(1).strip().replace("\n", " ") if f else ""))


def main():
    gt_key = "cci_BS-CSMCI" if MODE == "cci" else "inflexp_BS-PT-NY"
    gt = GT["gt_locked"][COUNTRY][gt_key][WINDOW]
    print(f"G2 {COUNTRY} {MODE} {WINDOW} — modell={MODEL}, N={N}/seed, seeds={SEEDS}, "
          f"GT({gt_key})={gt:+.1f}, korpusz n={CORPUS['n']}")

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
            res = ssr.rate(sents, FIN_ANCHOR[COUNTRY], method="linear", embed_fn=oai_embed)
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
            pres = ssr.rate(pr, PRICE_ANCHOR[COUNTRY], method="linear", embed_fn=oai_embed)
            fres = ssr.rate(fn, FIN_ANCHOR[COUNTRY], method="linear", embed_fn=oai_embed)
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
        "run": f"G2 {COUNTRY} {MODE} {WINDOW}",
        "model": MODEL, "provider": "siliconflow", "date": "2026-07-20",
        "flag": "CLEAN", "flag_note": f"target-ablak {WINDOW} >= 2025-08",
        "protocol": ("cci: persona-only + korpusz-grounding, MONDAT+KATEGORIA, N=60, SSR linear + "
                     "text-embedding-3-small" if MODE == "cci" else
                     "inflexp: run_inflexp_backtest 2-soros protokoll, N=40, SSR linear + OpenAI-small"),
        "anchors": ("ssr.REFERENCE_SETS_" + COUNTRY + " (G0d)" if MODE == "cci"
                    else "delphoi.REFERENCE_SETS_PRICE_EXTRA['" + LANG + "'] + ssr.REFERENCE_SETS_" + COUNTRY),
        "personas": f"persona_sampler.sample_country_personas('{COUNTRY}', n={N}) — COUNTRY_QUOTAS G2-fill",
        "corpus_files": {os.path.basename(CORPUS_FILE): g1_lib.sha256_file(CORPUS_FILE)},
        "corpus_n": CORPUS["n"], "corpus_lean": CORPUS["lean_dist"], "corpus_cats": CORPUS["cat_dist"],
        "gt": gt, "gt_indicator": gt_key,
        "per_seed": per_seed, "seed_mean": mean_v, "seed_sd": sd,
        "seeds_run": SEEDS, "cost": pacer.stats(),
    }
    sub = "cci" if MODE == "cci" else "inflexp"
    out = os.path.join(G2DIR, sub, f"hy3_{LANG}_{sub}_{WINDOW}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[g2] artefakt: {out}")


if __name__ == "__main__":
    main()
