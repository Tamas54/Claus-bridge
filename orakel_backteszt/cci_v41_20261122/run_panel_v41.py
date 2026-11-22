#!/usr/bin/env python3
"""CCI-backteszt V4.1-Flash (2026-11-22) — panel-futtató, a júliusi G2-recept 1:1 klónja.

Recept (g2_hy3_20260720/run_g2.py + prereg_west_20260721/run_west.py, változatlanul):
  persona-only (nincs priming) + korpusz-grounding (16 véletlen cím a cella 60-as korpuszából personánként),
  MONDAT+KATEGÓRIA kettős formátum, N=60/seed, 3 seed, temp=0.8, max_tokens=160, thinking kikapcsolva,
  SSR linear; horgony: ssr.REFERENCE_SETS_<nyelv> háztartási anyagi kilátás; personák:
  persona_sampler.sample_country_personas (COUNTRY_QUOTAS, a delphoi.COUNTRY_PANEL_CONFIG a kanonikus tár).
  FR/IT szövegek = run_g2.py, EN/DE = run_west.py szó szerint; HU = ugyanennek a sablonnak a magyar változata.

Két kényszerű eltérés (a jegyzőkönyvben kimondva):
  1. modell: deepseek-ai/DeepSeek-V4.1-Flash (júliusban tencent/Hy3);
  2. embedding: SF Qwen/Qwen3-Embedding-8B (delphoi.EMBED_MODEL_SF) a text-embedding-3-small helyett
     (a Bridge OpenAI-kulcsa 401) — a júliusi számok ezért nem 1:1 összevethetők.
Nincs SQLite-cache: a nyers válaszok cellánként JSON-ba mennek (raw/), újraindításkor onnan folytat.
402 (egyenleg) → azonnali megállás, 3-as kilépési kóddal.

  flock ~/.cache/claus-sf.lock python3 run_panel_v41.py HU 2026-08
  python3 run_panel_v41.py HU 2026-08 --smoke     # 1 hívás, token-mérés (nem eredmény)
"""
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, REPO)
import numpy as np  # noqa: E402
from plugins import delphoi, persona_sampler, ssr  # noqa: E402

MODEL = "deepseek-ai/DeepSeek-V4.1-Flash"
EMBED_MODEL = delphoi.EMBED_MODEL_SF  # Qwen/Qwen3-Embedding-8B
SF_BASE = "https://api.siliconflow.com/v1"
PRICE_IN, PRICE_OUT = 0.15, 0.60  # $/M token, SF-listaár 2026-11-22 (olcso_swarm jegyzőkönyv)
SEEDS = [int(s) for s in os.environ.get("CCI_SEEDS", "1,2,3").split(",")]
N = int(os.environ.get("CCI_N", "60"))
NEWS_K = 16
CONC = int(os.environ.get("CCI_CONC", "8"))


def load_key():
    for line in open(os.path.join(os.path.expanduser("~/Claus/claus-bridge-mcp"), ".env"), encoding="utf-8"):
        line = line.strip()
        if line.startswith("SILICONFLOW_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("nincs SILICONFLOW_API_KEY a Bridge .env-ben")


SF_KEY = load_key()


class Balance402(Exception):
    pass


LANG = {"HU": "hu", "FR": "fr", "IT": "it", "DE": "de", "US": "en"}
MONTH = {
    "hu": {"2026-07": "2026. július", "2026-08": "2026. augusztus", "2026-09": "2026. szeptember",
           "2026-10": "2026. október", "2026-11": "2026. november"},
    "fr": {"2026-07": "juillet 2026", "2026-08": "août 2026", "2026-09": "septembre 2026",
           "2026-10": "octobre 2026", "2026-11": "novembre 2026"},
    "it": {"2026-07": "luglio 2026", "2026-08": "agosto 2026", "2026-09": "settembre 2026",
           "2026-10": "ottobre 2026", "2026-11": "novembre 2026"},
    "de": {"2026-07": "Juli 2026", "2026-08": "August 2026", "2026-09": "September 2026",
           "2026-10": "Oktober 2026", "2026-11": "November 2026"},
    "en": {"2026-07": "July 2026", "2026-08": "August 2026", "2026-09": "September 2026",
           "2026-10": "October 2026", "2026-11": "November 2026"},
}
PROFILE_HDR = {"hu": ("A PROFILOD:", "Életkor", "Lakóhely", "Végzettség", "Média"),
               "fr": ("TON PROFIL:", "Âge", "Lieu de vie", "Études", "Médias"),
               "it": ("IL TUO PROFILO:", "Età", "Dove vivi", "Istruzione", "Media"),
               "en": ("YOUR PROFILE:", "Age", "Where you live", "Education", "Media"),
               "de": ("DEIN PROFIL:", "Alter", "Wohnort", "Bildung", "Medien")}
NEWS_HDR = {
    "hu": "A JELENLEGI MÉDIAKÖRNYEZETED (Magyarország, {m}) — néhány cím, amit mostanában láttál:",
    "fr": "TON ENVIRONNEMENT MÉDIATIQUE ACTUEL (France, {m}) — quelques titres que tu as vus récemment :",
    "it": "IL TUO AMBIENTE MEDIATICO ATTUALE (Italia, {m}) — alcuni titoli che hai visto di recente:",
    "en": "YOUR CURRENT MEDIA ENVIRONMENT (United States, {m}) — some headlines you have seen recently:",
    "de": "DEIN AKTUELLES MEDIENUMFELD (Deutschland, {m}) — einige Schlagzeilen, die du zuletzt gesehen hast:",
}
CCI_SYS = {
    "hu": ("Egy magyar fogyasztót szimulálsz, aki a háztartása pénzügyi döntéseit hozza; az időpont: {m}. "
           "Kizárólag a demográfiai profilod és az alábbi médiakörnyezet alapján reagálsz — úgy, ahogy ezek a "
           "hírek a saját életedet és a pénztárcádat érintik. NE használj semmilyen külső tudást bizalmi "
           "indexekről, közvélemény-kutatásokról vagy statisztikai adatokról — őszintén, a saját "
           "élethelyzetedből válaszolj."),
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
    "en": ("You are simulating a American consumer, the financial decision-maker of your household, "
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
    "hu": ("Hogyan látod a háztartásod anyagi helyzetét a következő 12 hónapban?\n"
           "Pontosan ebben a formában válaszolj, 2 sorban:\n"
           "MONDAT: <1-2 mondat a saját szavaiddal, őszintén>\n"
           "KATEGÓRIA: <jobb|ugyanolyan|rosszabb>"),
    "fr": ("Comment vois-tu la situation financière de ton foyer pour les 12 prochains mois ?\n"
           "Réponds EXACTEMENT sous cette forme, en 2 lignes :\n"
           "PHRASE: <1-2 phrases avec tes propres mots, honnêtement>\n"
           "CATEGORIE: <meilleure|pareille|pire>"),
    "it": ("Come vedi la situazione economica della tua famiglia nei prossimi 12 mesi?\n"
           "Rispondi ESATTAMENTE in questo formato, in 2 righe:\n"
           "FRASE: <1-2 frasi con parole tue, onestamente>\n"
           "CATEGORIA: <migliore|uguale|peggiore>"),
    "en": ("How do you see your household's financial situation over the next 12 months?\n"
           "Answer EXACTLY in this format, in 2 lines:\n"
           "SENTENCE: <1-2 sentences in your own words, honestly>\n"
           "CATEGORY: <better|same|worse>"),
    "de": ("Wie siehst du die finanzielle Lage deines Haushalts in den nächsten 12 Monaten?\n"
           "Antworte GENAU in diesem Format, in 2 Zeilen:\n"
           "SATZ: <1-2 Sätze in deinen eigenen Worten, ehrlich>\n"
           "KATEGORIE: <besser|gleich|schlechter>"),
}
CCI_CAT = {"hu": {"jobb": 1, "ugyanolyan": 0, "rosszabb": -1},
           "fr": {"meilleure": 1, "pareille": 0, "pire": -1},
           "it": {"migliore": 1, "uguale": 0, "peggiore": -1},
           "en": {"better": 1, "same": 0, "worse": -1},
           "de": {"besser": 1, "gleich": 0, "schlechter": -1}}
CCI_MARK = {"hu": ("MONDAT", "KATEG[ÓO]RIA"), "fr": ("PHRASE", "CATEGORIE"), "it": ("FRASE", "CATEGORIA"),
            "en": ("SENTENCE", "CATEGORY"), "de": ("SATZ", "KATEGORIE")}
FIN_ANCHOR = {"hu": ssr.REFERENCE_SETS_HU["anyagi_varakozas"],
              "fr": ssr.REFERENCE_SETS_FR["perspective_financiere"],
              "it": ssr.REFERENCE_SETS_IT["prospettiva_finanziaria"],
              "en": ssr.REFERENCE_SETS_EN["financial_outlook"],
              "de": ssr.REFERENCE_SETS_DE["finanzielle_aussicht"]}

USAGE = {"prompt": 0, "completion": 0, "calls": 0, "embed_tokens": 0}


def _post(path, body, timeout=120):
    req = urllib.request.Request(f"{SF_BASE}{path}", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {SF_KEY}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 402:
            raise Balance402("SF 402 — egyenleg elfogyott") from None
        raise


def call(system, user):
    body = {"model": MODEL, "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": user}],
            "max_tokens": 160, "temperature": 0.8, "thinking": {"type": "disabled"}}
    last = None
    for a in range(5):
        try:
            d = _post("/chat/completions", body)
            txt = (d["choices"][0]["message"].get("content") or "").strip()
            u = d.get("usage") or {}
            USAGE["prompt"] += u.get("prompt_tokens", 0)
            USAGE["completion"] += u.get("completion_tokens", 0)
            USAGE["calls"] += 1
            if txt:
                return txt
            last = "empty"
        except Balance402:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 ** a + random.random())
    raise RuntimeError(f"call failed: {last}")


def sf_embed(texts):
    out = []
    texts = list(texts)
    for i in range(0, len(texts), 32):
        chunk = texts[i:i + 32]
        for a in range(5):
            try:
                d = _post("/embeddings", {"model": EMBED_MODEL, "input": chunk})
                break
            except Balance402:
                raise
            except Exception:  # noqa: BLE001
                if a == 4:
                    raise
                time.sleep(2 ** a)
        USAGE["embed_tokens"] += (d.get("usage") or {}).get("total_tokens", 0)
        out.extend(it["embedding"] for it in sorted(d["data"], key=lambda x: x.get("index", 0)))
    return np.asarray(out, dtype=float)


def profile_block(lang, p):
    hdr, a, s, e, m = PROFILE_HDR[lang]
    return f"{hdr}\n- {a}: {p['age']}\n- {s}: {p['settlement']}\n- {e}: {p['edu']}\n- {m}: {p['media']}"


def build_prompt(lang, month, news, p, rng):
    m = MONTH[lang][month]
    heads = "\n".join("- " + x for x in rng.sample(news, min(NEWS_K, len(news))))
    system = CCI_SYS[lang].format(m=m)
    user = f"{profile_block(lang, p)}\n\n{NEWS_HDR[lang].format(m=m)}\n{heads}\n\n{CCI_ASK[lang]}"
    return system, user


def parse_cci(lang, txt):
    m1, m2 = CCI_MARK[lang]
    ms = re.search(rf"{m1}\s*:?\s*(.+?)(?:\n\s*\**\s*{m2}\s*\**\s*:|$)", txt, re.S | re.I)
    mc = re.search(rf"{m2}\s*\**\s*:?\s*\**\s*([^\W\d_]+)", txt, re.I)
    sent = (ms.group(1).strip() if ms else "").replace("\n", " ").strip("* ")
    cat = CCI_CAT[lang].get(mc.group(1).strip().lower()) if mc else None
    return sent, cat


def main():
    country, month = sys.argv[1].upper(), sys.argv[2]
    smoke = "--smoke" in sys.argv
    lang = LANG[country]
    cpath = os.path.join(HERE, "korpusz", f"corpus_{country.lower()}_{month}.json")
    corpus = json.load(open(cpath, encoding="utf-8"))
    news = [it["text"] for it in corpus["items"]]
    if smoke:
        personas, _ = persona_sampler.sample_country_personas(country, n=1, seed=999)
        s, u = build_prompt(lang, month, news, personas[0], random.Random(999))
        txt = call(s, u)
        print(json.dumps({"usage": USAGE, "chars": len(s) + len(u), "answer": txt,
                          "parsed": parse_cci(lang, txt)}, ensure_ascii=False))
        return
    rawdir = os.path.join(HERE, "raw")
    os.makedirs(rawdir, exist_ok=True)
    rawpath = os.path.join(rawdir, f"raw_{country.lower()}_{month}.json")
    raw = json.load(open(rawpath, encoding="utf-8")) if os.path.exists(rawpath) else {}
    t0 = time.time()
    per_seed = []
    try:
        for seed in SEEDS:
            personas, kl = persona_sampler.sample_country_personas(country, n=N, seed=seed)
            rng = random.Random(seed)
            prompts = [build_prompt(lang, month, news, p, rng) for p in personas]
            key = str(seed)
            have = raw.get(key) or [None] * N

            def ask(i):
                if have[i]:
                    return have[i]
                try:
                    return call(*prompts[i])
                except Balance402:
                    raise
                except Exception:  # noqa: BLE001
                    return ""

            with ThreadPoolExecutor(max_workers=CONC) as ex:
                answers = list(ex.map(ask, range(N)))
            raw[key] = answers
            json.dump(raw, open(rawpath, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
            rows = [parse_cci(lang, t) for t in answers if t]
            sents = [s for s, c in rows if s]
            cats = [c for s, c in rows if c is not None]
            res = ssr.rate(sents, FIN_ANCHOR[lang], method="linear", embed_fn=sf_embed)
            bal = (res["survey_score"] - 3) / 2 * 100
            catb = 100 * sum(cats) / len(cats) if cats else None
            per_seed.append({"seed": seed, "n_answers": sum(1 for t in answers if t), "n_sent": len(sents),
                             "n_cat": len(cats), "ssr_score": res["survey_score"], "ssr_balance": bal,
                             "cat_balance": catb, "survey_pmf": res["survey_pmf"], "kl": kl})
            print(f"  {country} {month} seed {seed}: SSR {bal:+.1f} | kat {catb if catb is None else round(catb,1)}"
                  f" (n={len(sents)}) | tokens {USAGE['prompt']}/{USAGE['completion']}", flush=True)
    except Balance402 as e:
        json.dump(raw, open(rawpath, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        print(f"[!!] {e} — MEGÁLLOK ({country} {month})", flush=True)
        sys.exit(3)
    vals = [s["ssr_balance"] for s in per_seed]
    mean_v = sum(vals) / len(vals)
    sd = (sum((v - mean_v) ** 2 for v in vals) / len(vals)) ** 0.5
    cats = [s["cat_balance"] for s in per_seed if s["cat_balance"] is not None]
    cost = (USAGE["prompt"] * PRICE_IN + USAGE["completion"] * PRICE_OUT) / 1e6
    out = {"cell": f"{country} {month}", "country": country, "month": month, "lang": lang,
           "model": MODEL, "embed_model": EMBED_MODEL, "date": time.strftime("%Y-%m-%d"),
           "protocol": "G2-recept 1:1 (persona-only + korpusz-grounding, MONDAT+KATEGÓRIA, N=60, 3 seed, SSR linear)",
           "corpus_file": os.path.basename(cpath), "corpus_n": corpus["n"],
           "corpus_window": corpus["corpus_window"], "per_seed": per_seed,
           "seed_mean": mean_v, "seed_sd": sd, "cat_mean": (sum(cats) / len(cats)) if cats else None,
           "usage_this_run": dict(USAGE), "cost_usd_this_run": round(cost, 5),
           "wall_s": round(time.time() - t0, 1)}
    os.makedirs(os.path.join(HERE, "cellak"), exist_ok=True)
    json.dump(out, open(os.path.join(HERE, "cellak", f"v41_{country.lower()}_{month}.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[cella] {country} {month}: SSR {mean_v:+.2f} ± {sd:.2f} | kat {out['cat_mean']} | "
          f"költség ${cost:.4f}", flush=True)


if __name__ == "__main__":
    main()
