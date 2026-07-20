#!/usr/bin/env python3
"""
YOUTUBE CÍM-TESZT — szintetikus tartalom-A/B a Maiorianus (római történelem) csatornára.

A panel (Flash perszónák, ANGOL történelem-YouTube közönség) 10 valódi címből választja ki,
melyiket kattintaná. A valós NÉZETTSÉG a ground truth (a panel elől ELREJTVE) → megnézzük,
a panel rangsora korrelál-e a tényleges teljesítménnyel (Spearman).

MÓDSZER M1 — kényszerített választás (plurality): minden persona 1 címet választ + 1 mondat indok.

HASZNÁLAT: SSR_DEMO_N=40 python3 run_title_test.py   (Flash; ORAKEL_CACHE=1 a reprodukcióhoz)
"""
import os
import re
import sys
import json
import random
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass
from plugins.llm_cache import LLMCache, cache_key

N = int(os.environ.get("SSR_DEMO_N", "40"))
MODEL = os.environ.get("ORAKEL_PANEL_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
SF_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
_CACHE = LLMCache() if os.environ.get("ORAKEL_CACHE") == "1" else None

# ── 10 VALÓDI CÍM + REJTETT GROUND TRUTH (nézettség, kor hónapban) ───────
# Forrás: youtube.com/@Maiorianus_Sebastian (lehúzva 2026-06-16). A 'views' a panel elől REJTVE.
TITLES = [
    {"t": "Walking through Cities of Europe in 700 AD: What would you have seen?",        "views": 953000, "age_mo": 24},
    {"t": "Top 10 incredibly advanced Roman technologies that will blow your mind.",       "views": 880000, "age_mo": 48},
    {"t": "A forgotten Remnant of the Roman Empire?",                                      "views": 409000, "age_mo": 36},
    {"t": "Walking Through Constantinople At Its Peak: What Would You Have Seen?",          "views": 339000, "age_mo": 3},
    {"t": "The Most Important Date Of Roman History That Almost Nobody Knows: 751 AD",      "views": 310000, "age_mo": 12},
    {"t": 'Visiting Rome After "The Fall" Of 476 AD: What Would You Have Seen?',            "views": 307000, "age_mo": 3},
    {"t": "What If The Eastern Roman Empire Never Fell?",                                   "views": 67000,  "age_mo": 12},
    {"t": 'How Constantine "The Great" Doomed The Roman Empire.',                          "views": 54000,  "age_mo": 8},
    {"t": "Meet The Worst Province Of The Roman Empire.",                                   "views": 28000,  "age_mo": 10},
    {"t": "Flavius Ricimer: The Most Hated Figure Of Late Roman History. But Rightfully So?", "views": 9500, "age_mo": 12},
]

# ── PERSZÓNA — ANGOL történelem-YouTube közönség (NEM magyar választó!) ──
AGE = [("18-24", 0.20), ("25-34", 0.30), ("35-44", 0.22), ("45-54", 0.14), ("55+", 0.14)]
GENDER = [("male", 0.78), ("female", 0.22)]
REGION = [("the USA", 0.40), ("the UK", 0.12), ("continental Europe", 0.30), ("another English-speaking country", 0.18)]

# A Kommandant korrekciója: a YouTube TÖMEGIPAR — a videót a casual, algoritmus-etette
# tömegnéző futtatja fel, nem a törirajongó értelmiségi. Env TITLE_AUDIENCE=mass (alap) | enthusiast.
_AUD = os.environ.get("TITLE_AUDIENCE", "mass")
INTEREST_MASS = [
    ("mostly here to be entertained, NOT a history buff", 0.55),
    ("someone who sometimes watches history or documentary videos for fun", 0.30),
    ("a genuine history enthusiast", 0.15),
]
INTEREST_ENTH = [("a casual history viewer", 0.45), ("a history enthusiast", 0.40), ("a hardcore history buff", 0.15)]
INTEREST = INTEREST_MASS if _AUD == "mass" else INTEREST_ENTH


def pick(rng, dist):
    r = rng.random(); acc = 0.0
    for lab, w in dist:
        acc += w
        if r <= acc:
            return lab
    return dist[-1][0]


def persona_prompt(rng, titles_block):
    age = pick(rng, AGE); gender = pick(rng, GENDER); region = pick(rng, REGION); interest = pick(rng, INTEREST)
    if _AUD == "mass":
        system = (
            f"You are a regular YouTube user: {gender}, {age}, from {region}, {interest}. "
            "You are scrolling YouTube for fun and the algorithm just surfaced these Roman-history "
            "video titles in your feed — you did NOT seek them out. You click on whatever instantly "
            "grabs you (curiosity, spectacle, a vivid mental image), NOT on what is most scholarly or "
            "obscure. Judge ONLY by the titles — no thumbnails, no view counts. React the way the broad "
            "mass audience genuinely does."
        )
    else:
        system = (
            "You are a real person who watches history YouTube channels (Roman and Byzantine history). "
            f"You are {gender}, {age}, from {region}, and {interest}. You are browsing this channel's video "
            "list. Judge ONLY by the titles — you cannot see thumbnails or view counts. React honestly, "
            "the way you genuinely would when deciding what to click."
        )
    user = (
        "Here are 10 video titles from a Roman history channel:\n\n" + titles_block +
        "\n\nWhich ONE video would you be most likely to click right now? "
        "Answer EXACTLY in this format, nothing else:\nCHOICE: <number 1-10>\nWHY: <one short sentence>"
    )
    return system, user


def call(system, user, iteration=0, retries=5):
    key = None
    if _CACHE is not None:
        key = cache_key(MODEL, {"t": 0.8, "mt": 120}, system, user, iteration)
        hit = _CACHE.get(key)
        if hit is not None:
            return hit
    body = {"model": MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": 120, "temperature": 0.8, "thinking": {"type": "disabled"}}
    req = urllib.request.Request("https://api.siliconflow.com/v1/chat/completions",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Authorization": f"Bearer {SF_KEY}", "Content-Type": "application/json"})
    import time
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.loads(r.read().decode("utf-8"))
            txt = (d["choices"][0]["message"].get("content") or "").strip()
            if txt:
                if _CACHE is not None and key is not None:
                    _CACHE.set(key, txt, model=MODEL)
                return txt
        except Exception as e:  # noqa: BLE001
            last = e
        if attempt < retries - 1:
            time.sleep(2 ** attempt + random.random())
    raise RuntimeError(f"call failed: {last}")


def parse(text):
    mc = re.search(r"CHOICE:\s*(\d+)", text, re.I)
    mw = re.search(r"WHY:\s*(.+)", text, re.I | re.S)
    choice = int(mc.group(1)) if mc else None
    if choice is not None and not (1 <= choice <= len(TITLES)):
        choice = None
    why = (mw.group(1).strip().replace("\n", " ") if mw else "")
    return choice, why


def spearman(a, b):
    """Spearman-rang korreláció (scipy nélkül is, de scipy van)."""
    from scipy.stats import spearmanr
    rho, p = spearmanr(a, b)
    return rho, p


def main():
    titles_block = "\n".join(f"{i+1}. {t['t']}" for i, t in enumerate(TITLES))
    rng = random.Random(1)
    prompts = [persona_prompt(rng, titles_block) for _ in range(N)]
    print(f"YT cím-teszt — {MODEL} (vak a nézettségre), N={N}, ANGOL történelem-közönség\n")

    def ask(i_su):
        i, (s, u) = i_su
        try:
            return parse(call(s, u, iteration=i))
        except Exception:
            return (None, "")

    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(ask, enumerate(prompts)))
    choices = [c for c, w in rows if c]
    votes = Counter(choices)
    n_valid = len(choices)

    # ── eredmény: panel-rangsor szavazat szerint ──
    ranked = sorted(range(1, len(TITLES) + 1), key=lambda k: -votes.get(k, 0))
    print("=" * 78)
    print(f"PANEL-RANGSOR (szavazat) vs VALÓS NÉZETTSÉG   (n={n_valid} érvényes választás)")
    print("=" * 78)
    print(f"{'#':>2} {'szav':>4} {'nézettség':>10} {'/hó':>7}  cím")
    for k in ranked:
        t = TITLES[k - 1]
        v = votes.get(k, 0)
        print(f"{k:>2} {v:>4} {t['views']:>10,} {t['views']//t['age_mo']:>7,}  {t['t'][:52]}")

    # ── korreláció: panel-szavazat vs nézettség (és /hó) ──
    idx = list(range(len(TITLES)))
    panel = [votes.get(i + 1, 0) for i in idx]
    views = [TITLES[i]["views"] for i in idx]
    permo = [TITLES[i]["views"] / TITLES[i]["age_mo"] for i in idx]
    rho_v, p_v = spearman(panel, views)
    rho_m, p_m = spearman(panel, permo)
    print("-" * 78)
    print(f"Spearman(panel-szavazat, nyers nézettség) = {rho_v:+.2f}  (p={p_v:.3f})")
    print(f"Spearman(panel-szavazat, nézettség/hó)    = {rho_m:+.2f}  (p={p_m:.3f})")
    print("  (+1 = a panel tökéletesen a valós sorrendet adja; 0 = nincs kapcsolat)")
    print("=" * 78)
    win = ranked[0]
    print(f"\nPANEL GYŐZTES: #{win} „{TITLES[win-1]['t']}”  ({votes.get(win,0)} szavazat)")
    print("Néhány indok:")
    shown = 0
    for c, w in rows:
        if c == win and w:
            print(f"  • {w[:96]}")
            shown += 1
            if shown >= 4:
                break

    json.dump({"n": n_valid, "votes": dict(votes), "ranked": ranked,
               "spearman_views": rho_v, "spearman_permonth": rho_m,
               "titles": TITLES, "reasons": [{"choice": c, "why": w} for c, w in rows]},
              open(os.path.join(os.path.dirname(__file__), "title_test_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("\nKimenet: title_test_result.json")


if __name__ == "__main__":
    main()
