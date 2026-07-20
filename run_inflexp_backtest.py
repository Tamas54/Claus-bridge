#!/usr/bin/env python3
"""INFLÁCIÓS (ÁR)VÁRAKOZÁS VAK BACKTESZT — 4 ország × 3 időpont. Protokoll: orakel_ii_inflacios_varakozas_PROTOKOLL_2026_06_16.
Vak Flash + célhó-előtti ÁLTALÁNOS korpusz + SSR-linear (OpenAI-small). Tapasztalati prompt (bolti ár/rezsi/üzemanyag/bér).
Bónusz: CCI ugyanabban a hívásban (pénzügyi kérdés). Metrika: irány + 4-ország Spearman/időpont + cross-time delta.
HASZNÁLAT: SSR_DEMO_N=40 ORAKEL_CACHE=1 python3 -u run_inflexp_backtest.py
"""
import os, sys, json, time, random, re, urllib.request
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv; load_dotenv(".env")
except Exception: pass
import numpy as np
from scipy.stats import spearmanr
from plugins import ssr
from plugins.llm_cache import LLMCache, cache_key

N = int(os.environ.get("SSR_DEMO_N", "40"))
EMB = "text-embedding-3-small"; MODEL = os.environ.get("ORAKEL_PANEL_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
SF_KEY = os.environ.get("SILICONFLOW_API_KEY",""); OAI_KEY = os.environ.get("OPENAI_API_KEY","")
_CACHE = LLMCache() if os.environ.get("ORAKEL_CACHE")=="1" else None

# ZÁROLT GROUND TRUTH (Eurostat, a protokollban rögzítve)
PRICE_GT = {"HU":[36.5,33.3,30.9],"CZ":[23.6,8.6,7.3],"PT":[41.8,33.8,38.0],"PL":[20.9,21.6,20.6]}
CCI_GT   = {"HU":[-24.8,-24.4,-19.4],"CZ":[-7.3,-2.6,1.1],"PT":[-17.4,-15.1,-13.7],"PL":[-11.4,-13.0,-13.2]}
MONTHS = ["2025-09","2025-11","2026-01"]
# cella -> korpuszfájl (3 reused: HU 2026-01, CZ 2025-09, CZ 2026-01)
CORP = {
 ("HU",0):"corpus_hu_pre_2025_09.json", ("HU",1):"corpus_hu_pre_2025_11.json", ("HU",2):"corpus_hu_2025_12.json",
 ("CZ",0):"corpus_cz_2025_julsep.json", ("CZ",1):"corpus_cz_pre_2025_11.json", ("CZ",2):"corpus_cz_2026_01.json",
 ("PT",0):"corpus_pt_pre_2025_09.json", ("PT",1):"corpus_pt_pre_2025_11.json", ("PT",2):"corpus_pt_pre_2026_01.json",
 ("PL",0):"corpus_pl_pre_2025_09.json", ("PL",1):"corpus_pl_pre_2025_11.json", ("PL",2):"corpus_pl_pre_2026_01.json",
}
FIN_ANCHOR = {"HU":ssr.REFERENCE_SETS_HU["anyagi_varakozas"],"CZ":ssr.REFERENCE_SETS_CZ["financni_vyhled"],
              "PT":ssr.REFERENCE_SETS_PT["perspetiva_financeira"],"PL":ssr.REFERENCE_SETS_PL["perspektywa_finansowa"]}
DEMOG = {  # (kor, régió, iskola) — egyszerű, ország-lokalizált
 "HU":(["18-29","30-44","45-59","60+"],["Budapest","nagyváros","kisváros","falu"],["alapfokú","középfokú","felsőfokú"]),
 "CZ":(["18-29","30-44","45-59","60+"],["Praha","Čechy","Morava"],["základní","středoškolské","vysokoškolské"]),
 "PT":(["18-29","30-44","45-59","60+"],["Lisboa","Porto/Norte","Centro","Sul"],["básico","secundário","superior"]),
 "PL":(["18-29","30-44","45-59","60+"],["Warszawa","duże miasto","małe miasto","wieś"],["podstawowe","średnie","wyższe"]),
}
# persona system + kérdés + parse-markerek nyelvenként
SYS = {
 "HU":"Egy magyar fogyasztót szimulálsz {y}-ben. A profilod és az alábbi hírkörnyezet alapján reagálsz, ahogy ezek a hírek a pénztárcádat érintik. NE használj külső index-adatot.",
 "CZ":"Simuluješ českého spotřebitele v období {y}. Reaguješ podle svého profilu a níže uvedeného zpravodajství, jak se tě dotýká. Nepoužívej externí indexová data.",
 "PT":"Simulas um consumidor português em {y}. Reages com base no teu perfil e no ambiente noticioso abaixo, conforme te afeta. Não uses dados de índices externos.",
 "PL":"Symulujesz polskiego konsumenta w okresie {y}. Reagujesz na podstawie swojego profilu i poniższych wiadomości, jak Cię to dotyczy. Nie używaj zewnętrznych danych indeksowych.",
}
ASK = {
 "HU":"\n\nKÉRDÉS — válaszolj PONTOSAN 2 sorban, a SAJÁT szavaiddal:\nARAK: <1-2 mondat: hogyan VÁRAKOZOL a bolti árak, a rezsi és az üzemanyag alakulására a következő 12 hónapban?>\nPENZUGY: <1-2 mondat: hogyan látod a háztartásod anyagi helyzetét a következő 12 hónapban?>",
 "CZ":"\n\nOTÁZKA — odpověz PŘESNĚ ve 2 řádcích, vlastními slovy:\nCENY: <1-2 věty: jak OČEKÁVÁŠ vývoj cen v obchodech, energií a pohonných hmot v příštích 12 měsících?>\nFINANCE: <1-2 věty: jak vidíš finanční situaci své domácnosti v příštích 12 měsících?>",
 "PT":"\n\nPERGUNTA — responde EXATAMENTE em 2 linhas, com as tuas palavras:\nPRECOS: <1-2 frases: como ESPERAS a evolução dos preços nas lojas, energia e combustíveis nos próximos 12 meses?>\nFINANCAS: <1-2 frases: como vês a situação financeira do teu agregado nos próximos 12 meses?>",
 "PL":"\n\nPYTANIE — odpowiedz DOKŁADNIE w 2 liniach, własnymi słowami:\nCENY: <1-2 zdania: jak SPODZIEWASZ się zmian cen w sklepach, energii i paliw w ciągu najbliższych 12 miesięcy?>\nFINANSE: <1-2 zdania: jak widzisz sytuację finansową swojego gospodarstwa w ciągu 12 miesięcy?>",
}
PR_MARK = {"HU":("ARAK","PENZUGY"),"CZ":("CENY","FINANCE"),"PT":("PRECOS","FINANCAS"),"PL":("CENY","FINANSE")}
HDR = {"HU":"AKTUÁLIS HÍREK:","CZ":"AKTUÁLNÍ ZPRÁVY:","PT":"NOTÍCIAS ATUAIS:","PL":"AKTUALNE WIADOMOŚCI:"}


def pickn(rng, lst): return lst[rng.randrange(len(lst))]
def sf(system, user, i):
    key=None
    if _CACHE is not None:
        key=cache_key(MODEL,{"t":0.8,"mt":160},system,user,i); h=_CACHE.get(key)
        if h is not None: return h
    body={"model":MODEL,"messages":[{"role":"system","content":system},{"role":"user","content":user}],"max_tokens":160,"temperature":0.8,"thinking":{"type":"disabled"}}
    req=urllib.request.Request("https://api.siliconflow.com/v1/chat/completions",data=json.dumps(body).encode(),headers={"Authorization":f"Bearer {SF_KEY}","Content-Type":"application/json"})
    last=None
    for a in range(5):
        try:
            with urllib.request.urlopen(req,timeout=90) as r: txt=(json.loads(r.read().decode())["choices"][0]["message"].get("content") or "").strip()
            if txt:
                if _CACHE is not None: _CACHE.set(key,txt,model=MODEL)
                return txt
        except Exception as e: last=e
        if a<4: time.sleep(2**a+random.random())
    return ""
def oai_embed(texts):
    req=urllib.request.Request("https://api.openai.com/v1/embeddings",data=json.dumps({"model":EMB,"input":list(texts)}).encode(),headers={"Authorization":f"Bearer {OAI_KEY}","Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=90) as r: d=json.loads(r.read().decode())
    return np.asarray([it["embedding"] for it in sorted(d["data"],key=lambda x:x.get("index",0))],dtype=float)
def parse2(txt, m):
    p=re.search(rf"{m[0]}\s*:?\s*(.+?)(?:\n|{m[1]}|$)",txt,re.I|re.S); f=re.search(rf"{m[1]}\s*:?\s*(.+)$",txt,re.I|re.S)
    return (p.group(1).strip().replace("\n"," ") if p else ""), (f.group(1).strip().replace("\n"," ") if f else "")

def run_cell(country, ti):
    corpus=json.load(open(CORP[(country,ti)],encoding="utf-8")); nt=len(corpus)
    age,reg,edu=DEMOG[country]; rng=random.Random(1); m=PR_MARK[country]
    def build(i):
        news="\n".join("- "+x for x in rng.sample(corpus,min(16,nt)))
        sysmsg=SYS[country].format(y=MONTHS[ti])
        user=f"{pickn(rng,age)}, {pickn(rng,reg)}, {pickn(rng,edu)}.\n\n{HDR[country]}\n{news}{ASK[country]}"
        return sysmsg,user
    prompts=[build(i) for i in range(N)]
    def ask(i): s,u=prompts[i]; return parse2(sf(s,u,i),m)
    with ThreadPoolExecutor(max_workers=8) as ex: rows=list(ex.map(ask,range(N)))
    pr=[p for p,f in rows if p]; fn=[f for p,f in rows if f]
    pscore=ssr.rate(pr, ssr.REFERENCE_SETS_PRICE[country], method="linear", embed_fn=oai_embed)["survey_score"]
    fscore=ssr.rate(fn, FIN_ANCHOR[country], method="linear", embed_fn=oai_embed)["survey_score"]
    return pscore, (fscore-3)/2*100, len(pr)

def main():
    print(f"INFLÁCIÓS VÁRAKOZÁS BACKTESZT — {MODEL}, N={N}, 4 ország × 3 időpont\n")
    P={}; CB={}
    for c in ["HU","CZ","PT","PL"]:
        P[c]=[]; CB[c]=[]
        for ti in range(3):
            ps,cb,n=run_cell(c,ti); P[c].append(ps); CB[c].append(cb)
            print(f"  {c} {MONTHS[ti]}: ár-score={ps:.3f}/5  cci={cb:+.1f}  (n={n}, valós ár-szaldó {PRICE_GT[c][ti]:+.1f})")
    print("\n"+"="*78)
    print("(b) 4-ORSZÁG SPEARMAN időpontonként (panel ár-score vs valós BS-PT-NY):")
    for ti in range(3):
        panel=[P[c][ti] for c in ["HU","CZ","PT","PL"]]; gt=[PRICE_GT[c][ti] for c in ["HU","CZ","PT","PL"]]
        rho=spearmanr(panel,gt)[0]
        order_p=sorted(["HU","CZ","PT","PL"],key=lambda c:-P[c][ti]); order_g=sorted(["HU","CZ","PT","PL"],key=lambda c:-PRICE_GT[c][ti])
        print(f"  {MONTHS[ti]}: Spearman={rho:+.2f} | panel-rangsor {order_p} vs valós {order_g}")
    print("\n(c) CROSS-TIME DELTA 2025-09→2025-11 (ár-score Δ vs valós Δ, irány):")
    for c in ["HU","CZ","PT","PL"]:
        dp=P[c][1]-P[c][0]; dg=PRICE_GT[c][1]-PRICE_GT[c][0]
        ok="✓" if (dp>0)==(dg>0) or abs(dg)<2 else "✗"
        print(f"  {c}: panel Δ={dp:+.3f}  valós Δ={dg:+.1f}  {ok}")
    print("\n(BÓNUSZ) CCI diszkrimináció időpontonként (Spearman panel-cci vs valós CCI):")
    for ti in range(3):
        panel=[CB[c][ti] for c in ["HU","CZ","PT","PL"]]; gt=[CCI_GT[c][ti] for c in ["HU","CZ","PT","PL"]]
        print(f"  {MONTHS[ti]}: Spearman={spearmanr(panel,gt)[0]:+.2f}")
    print("="*78)
    json.dump({"price_score":P,"cci_balance":CB,"price_gt":PRICE_GT,"cci_gt":CCI_GT,"months":MONTHS},
              open("inflexp_result.json","w"), ensure_ascii=False, indent=2)
    print("Kimenet: inflexp_result.json")

if __name__=="__main__": main()
