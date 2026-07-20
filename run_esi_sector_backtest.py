#!/usr/bin/env python3
"""ORAKEL-II — ESI SZEKTORÁLIS ÜZLETI BIZALOM vak backteszt.
Rács: 5 ország {HU,CZ,PL,PT,DE} × 3 célhó {2025-09,2025-11,2026-01} × 4 szektor = 60 cella.
Recept (változatlan mag): vak Flash (cutoff ~2025-01) Non-Think + célhó-előtti KIEGYENSÚLYOZOTT korpusz
+ üzleti-manager persona, SZEKTOR-specifikus prompt-horgony (rendelésállomány/kereslet/foglalkoztatás)
+ SSR-linear (text-embedding-3-small) az ÜZLETI-bizalom 5-pontos horgonyon. max_tokens=160.
A szektor-jelet a PROMPT húzza ki, NEM a korpusz (kiegyensúlyozott, nem szektor-skew-elt).
5 seed/cella → átlag±szórás. Inkrementális checkpoint esi_sector_result.json-ba.

HASZNÁLAT (teljes):  SSR_DEMO_N=40 ESI_SEEDS=5 ORAKEL_CACHE=1 python3 -u run_esi_sector_backtest.py
HASZNÁLAT (smoke):   ESI_SMOKE=1 SSR_DEMO_N=4 ESI_SEEDS=1 ORAKEL_CACHE=1 python3 -u run_esi_sector_backtest.py
PLACEBO:             ESI_PLACEBO=1 ...   (HU/CZ 2026-01 rossz-ablakú korpusszal, lásd PLACEBO_CORP)
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

N = int(os.environ.get("SSR_DEMO_N", "40"))
SEEDS = int(os.environ.get("ESI_SEEDS", "5"))
EMB = "text-embedding-3-small"
MODEL = os.environ.get("ORAKEL_PANEL_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
SF_KEY = os.environ.get("SILICONFLOW_API_KEY", ""); OAI_KEY = os.environ.get("OPENAI_API_KEY", "")
_CACHE = LLMCache() if os.environ.get("ORAKEL_CACHE") == "1" else None
SMOKE = os.environ.get("ESI_SMOKE") == "1"
PLACEBO = os.environ.get("ESI_PLACEBO") == "1"

COUNTRIES = ["HU", "CZ", "PL", "PT", "DE"]
MONTHS = ["2025-09", "2025-11", "2026-01"]
SECTORS = ["industry", "services", "retail", "construction"]

# (ország, hónap-idx) -> korpuszfájl. HU/CZ/PT/PL: az inflexp-backteszt bevált mappingje; DE: új.
CORP = {
 ("HU",0):"corpus_hu_pre_2025_09.json", ("HU",1):"corpus_hu_pre_2025_11.json", ("HU",2):"corpus_hu_2025_12.json",
 ("CZ",0):"corpus_cz_2025_julsep.json", ("CZ",1):"corpus_cz_pre_2025_11.json", ("CZ",2):"corpus_cz_2026_01.json",
 ("PT",0):"corpus_pt_pre_2025_09.json", ("PT",1):"corpus_pt_pre_2025_11.json", ("PT",2):"corpus_pt_pre_2026_01.json",
 ("PL",0):"corpus_pl_pre_2025_09.json", ("PL",1):"corpus_pl_pre_2025_11.json", ("PL",2):"corpus_pl_pre_2026_01.json",
 ("DE",0):"corpus_de_pre_2025_09.json", ("DE",1):"corpus_de_pre_2025_11.json", ("DE",2):"corpus_de_pre_2026_01.json",
}
# PLACEBO: rossz-ablakú, AZONOS NYELVŰ korpusz a 2026-01 célhoz (a "2024-es ablak egy 2026-os célhoz" elve)
PLACEBO_CORP = {("HU",2):"corpus_hu_2025_06.json", ("CZ",2):"corpus_cz_2025_julsep.json"}

# --- lokalizáció ---
REGION = {
 "HU":["Budapest","nagyváros","kisváros","vidék"], "CZ":["Praha","Čechy","Morava"],
 "PL":["Warszawa","duże miasto","małe miasto","region"], "PT":["Lisboa","Porto/Norte","Centro","Sul"],
 "DE":["Bayern","Nordrhein-Westfalen","Berlin","Ostdeutschland"],
}
FIRMSIZE = {
 "HU":["mikrovállalkozás","kisvállalat","középvállalat","nagyvállalat"],
 "CZ":["mikropodnik","malá firma","střední firma","velký podnik"],
 "PL":["mikrofirma","mała firma","średnia firma","duża firma"],
 "PT":["microempresa","pequena empresa","média empresa","grande empresa"],
 "DE":["Kleinstbetrieb","kleines Unternehmen","mittelständisches Unternehmen","Großunternehmen"],
}
# szektoronkénti cég-szerep
ROLE = {
 "HU":{"industry":"egy feldolgozóipari (gyártó) cég","services":"egy szolgáltató cég",
       "retail":"egy kiskereskedelmi cég","construction":"egy építőipari cég"},
 "CZ":{"industry":"výrobní (průmyslové) firmy","services":"firmy ve službách",
       "retail":"maloobchodní firmy","construction":"stavební firmy"},
 "PL":{"industry":"firmy produkcyjnej (przemysłowej)","services":"firmy usługowej",
       "retail":"firmy handlu detalicznego","construction":"firmy budowlanej"},
 "PT":{"industry":"uma empresa industrial (fabril)","services":"uma empresa de serviços",
       "retail":"uma empresa de comércio a retalho","construction":"uma empresa de construção"},
 "DE":{"industry":"eines Industrie-/Produktionsbetriebs","services":"eines Dienstleistungsunternehmens",
       "retail":"eines Einzelhandelsunternehmens","construction":"eines Bauunternehmens"},
}
# szektoronkénti fókusz (mire reflektál)
FOCUS = {
 "HU":{"industry":"a rendelésállományod, a termelési kilátásaid és a foglalkoztatás",
       "services":"a kereslet, az üzletmeneted és a foglalkoztatás",
       "retail":"a forgalmad, a vásárlói kereslet és a készletek",
       "construction":"a rendelésállományod és a foglalkoztatási kilátásaid"},
 "CZ":{"industry":"stav zakázek, výhled výroby a zaměstnanost","services":"poptávka, chod podniku a zaměstnanost",
       "retail":"tržby, poptávka zákazníků a zásoby","construction":"stav zakázek a výhled zaměstnanosti"},
 "PL":{"industry":"portfel zamówień, perspektywy produkcji i zatrudnienie","services":"popyt, działalność i zatrudnienie",
       "retail":"obroty, popyt klientów i zapasy","construction":"portfel zamówień i perspektywy zatrudnienia"},
 "PT":{"industry":"a carteira de encomendas, as perspetivas de produção e o emprego","services":"a procura, a atividade e o emprego",
       "retail":"as vendas, a procura dos clientes e os stocks","construction":"a carteira de encomendas e as perspetivas de emprego"},
 "DE":{"industry":"Auftragsbestand, Produktionsaussichten und Beschäftigung","services":"Nachfrage, Geschäftsgang und Beschäftigung",
       "retail":"Umsatz, Kundennachfrage und Lagerbestände","construction":"Auftragsbestand und Beschäftigungsaussichten"},
}
SYS = {
 "HU":"{role} vezetője/tulajdonosa vagy {y}-ben, {size}, {reg} régióban. A profilod és az alábbi hírkörnyezet alapján reagálsz arra, ahogyan ezek a hírek a CÉGEDET érintik: {focus}. NE használj külső index- vagy felmérési adatot.",
 "CZ":"Jsi majitel/ředitel {role} v období {y}, {size}, region {reg}. Reaguješ podle svého profilu a níže uvedeného zpravodajství, jak se dotýká TVÉ FIRMY: {focus}. Nepoužívej externí indexová ani průzkumová data.",
 "PL":"Jesteś właścicielem/dyrektorem {role} w okresie {y}, {size}, region {reg}. Reagujesz na podstawie profilu i poniższych wiadomości, jak dotyczą TWOJEJ FIRMY: {focus}. Nie używaj zewnętrznych danych indeksowych ani sondażowych.",
 "PT":"És dono/gestor de {role} em {y}, {size}, região {reg}. Reages com base no teu perfil e no ambiente noticioso abaixo, conforme afeta a TUA EMPRESA: {focus}. Não uses dados externos de índices ou inquéritos.",
 "DE":"Du bist Inhaber/Geschäftsführer {role} im Zeitraum {y}, {size}, Region {reg}. Du reagierst anhand deines Profils und der unten stehenden Nachrichten, wie sie DEIN UNTERNEHMEN betreffen: {focus}. Nutze keine externen Index- oder Umfragedaten.",
}
ASK = {
 "HU":"\n\nKÉRDÉS — válaszolj 1-2 mondatban, a SAJÁT szavaiddal, címkék nélkül: hogyan látod a céged üzleti helyzetét a következő 12 hónapban?",
 "CZ":"\n\nOTÁZKA — odpověz 1-2 větami, vlastními slovy, bez štítků: jak vidíš obchodní situaci své firmy v příštích 12 měsících?",
 "PL":"\n\nPYTANIE — odpowiedz w 1-2 zdaniach, własnymi słowami, bez etykiet: jak widzisz sytuację biznesową swojej firmy w ciągu najbliższych 12 miesięcy?",
 "PT":"\n\nPERGUNTA — responde em 1-2 frases, com as TUAS palavras, sem rótulos: como vês a situação de negócio da tua empresa nos próximos 12 meses?",
 "DE":"\n\nFRAGE — antworte in 1-2 Sätzen, mit DEINEN eigenen Worten, ohne Etiketten: Wie siehst du die Geschäftslage deines Unternehmens in den nächsten 12 Monaten?",
}
HDR = {"HU":"AKTUÁLIS HÍREK:","CZ":"AKTUÁLNÍ ZPRÁVY:","PL":"AKTUALNE WIADOMOŚCI:","PT":"NOTÍCIAS ATUAIS:","DE":"AKTUELLE NACHRICHTEN:"}


def sf(system, user, i):
    key = None
    if _CACHE is not None:
        key = cache_key(MODEL, {"t":0.8,"mt":160}, system, user, i); h = _CACHE.get(key)
        if h is not None: return h
    body = {"model":MODEL,"messages":[{"role":"system","content":system},{"role":"user","content":user}],
            "max_tokens":160,"temperature":0.8,"thinking":{"type":"disabled"}}
    req = urllib.request.Request("https://api.siliconflow.com/v1/chat/completions",
            data=json.dumps(body).encode(), headers={"Authorization":f"Bearer {SF_KEY}","Content-Type":"application/json"})
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
    return ""


def oai_embed(texts):
    req = urllib.request.Request("https://api.openai.com/v1/embeddings",
            data=json.dumps({"model":EMB,"input":list(texts)}).encode(),
            headers={"Authorization":f"Bearer {OAI_KEY}","Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=90) as r: d = json.loads(r.read().decode())
    return np.asarray([it["embedding"] for it in sorted(d["data"], key=lambda x:x.get("index",0))], dtype=float)


def run_cell(country, mi, sector, corpus):
    """Egy cella: SEEDS×N üzleti-manager persona EGY poolban (max párhuzam) → seedenkénti SSR balance."""
    nt = len(corpus); role = ROLE[country][sector]; focus = FOCUS[country][sector]
    jobs = []  # (seed, system, user, callkey)
    for seed in range(SEEDS):
        rng = random.Random(hash((country, mi, sector, seed)) & 0x7fffffff)
        for i in range(N):
            news = "\n".join("- "+x for x in rng.sample(corpus, min(16, nt)))
            sysmsg = SYS[country].format(role=role, y=MONTHS[mi], size=rng.choice(FIRMSIZE[country]),
                                         reg=rng.choice(REGION[country]), focus=focus)
            jobs.append((seed, sysmsg, f"{HDR[country]}\n{news}{ASK[country]}", seed*1000+i))
    with ThreadPoolExecutor(max_workers=12) as ex:
        texts = list(ex.map(lambda j: sf(j[1], j[2], j[3]), jobs))
    by_seed = {s: [] for s in range(SEEDS)}
    for (seed, *_), t in zip(jobs, texts):
        if t: by_seed[seed].append(t)
    bals = []
    for seed in range(SEEDS):
        resp = by_seed[seed]
        if len(resp) < 3: continue
        score = ssr.rate(resp, ssr.REFERENCE_SETS_BUSINESS[country], method="linear", embed_fn=oai_embed)["survey_score"]
        bals.append(round((score - 3) / 2 * 100, 2))
    return bals


def main():
    countries = COUNTRIES[:1] if SMOKE else COUNTRIES
    outfile = "esi_sector_placebo_result.json" if PLACEBO else ("esi_sector_smoke_result.json" if SMOKE else "esi_sector_result.json")
    cells = list(PLACEBO_CORP.keys()) if PLACEBO else None  # placebo: csak a megadott (ország, hó-idx) cellák
    print(f"ESI SZEKTOR BACKTESZT — {MODEL}, N={N}, SEEDS={SEEDS}, "
          f"{'PLACEBO' if PLACEBO else ('SMOKE' if SMOKE else 'TELJES')}  → {outfile}\n")
    results = {}
    t0 = time.time()
    for c in countries:
        results[c] = {}
        for mi, month in enumerate(MONTHS):
            if cells is not None and (c, mi) not in cells: continue
            corpus_file = PLACEBO_CORP[(c, mi)] if (PLACEBO and (c, mi) in PLACEBO_CORP) else CORP[(c, mi)]
            corpus = json.load(open(corpus_file, encoding="utf-8"))
            results[c][month] = {}
            for sector in SECTORS:
                bals = run_cell(c, mi, sector, corpus)
                mean = float(np.mean(bals)) if bals else None
                std = float(np.std(bals)) if len(bals) > 1 else 0.0
                results[c][month][sector] = {"balances": bals, "mean": mean, "std": std, "corpus": corpus_file}
                print(f"  {c} {month} {sector:12s}: mean={mean:+6.1f}  std={std:4.1f}  (seeds={bals})")
            json.dump(results, open(outfile, "w"), ensure_ascii=False, indent=2)  # checkpoint
    print(f"\nKész {time.time()-t0:.0f}s alatt. Kimenet: {outfile}")


if __name__ == "__main__":
    main()
