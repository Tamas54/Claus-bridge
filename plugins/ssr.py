"""plugins/ssr.py — Semantic Similarity Rating (SSR).

A szintetikus panel KVANTITATÍV megbízhatóságát javító, behúzható réteg. A failure
mode-ok, amiket kezel (lásd recon/LOOT.md #1, #2):
  - #1 VARIANCIA-ÖSSZEOMLÁS: ha egy LLM-et közvetlenül 1–5 Likert-SZÁMRA kérünk, a
    válaszok szűk sávba omlanak (eltűnik a szórás). Az SSR a perszóna SZABAD SZÖVEGES
    válaszát képezi le egy teljes valószínűségi eloszlássá (PMF) → a szórás megmarad.
  - #2 HIPER-POLARIZÁCIÓ: a kényszerített egy-választás kemény szélekre ugrik; a PMF
    az ambivalens választ szétosztja a Likert-pontok közt.

Reimplementáció (clean-room, NEM 1:1 másolat):
  - LINEÁRIS út: bayramannakov/synthetic-market-research, test_run.py::compute_ssr_pmf
    (MIT) — L2-normált embedding → (1+cos)/2 → soronkénti min-kivonás → L1-normálás.
  - SOFTMAX út: PyMC Labs SSR (arXiv 2510.08338), temperature + epsilon paraméterrel.
    A Kommandant döntése: MINDKÉT változat egy modulban, `method=` kapcsolóval, hogy a
    backteszten összevethető legyen, melyik kalibrál jobban a magyar adatra.

Az embedding-lépés BEHÚZHATÓ (a stackünk az embeddinget API-n csinálja, nincs lokális
sentence-transformers): adj `embed_fn(list[str]) -> np.ndarray`-t, VAGY add a kész
embeddingeket, VAGY ha telepítve van a sentence-transformers, az `all-MiniLM-L6-v2`
lokálisan fut. A PMF-matek tiszta numpy → embedding nélkül is tesztelhető.
"""
from __future__ import annotations

import numpy as np

# ── REFERENCIA-MONDATOK (anchor sets) ────────────────────────────────────
# Likert 1→K, halmazonként K mondat. Több halmaz átlaga csökkenti az egyetlen
# megfogalmazásra való érzékenységet (a paper 4–6 halmazt ajánl).

# Angol vásárlási-szándék halmazok — a forrás-repo (MIT) anchorjainak reimplementációja,
# market-research kompatibilitáshoz. A G0d (PYTHIA) kör az agreement + financial_outlook
# kategóriákkal egészítette ki (a HU-séma ekvivalense).
REFERENCE_SETS_EN = {
    "purchase_intent": [
        "I would definitely not buy this",
        "I probably would not buy this",
        "I might or might not buy this",
        "I would probably buy this",
        "I would definitely buy this",
    ],
    "interest": [
        "Not interested in purchasing at all",
        "Slightly interested in purchasing",
        "Moderately interested in purchasing",
        "Very interested in purchasing",
        "Extremely interested in purchasing",
    ],
    "agreement": [
        "I completely disagree with this.",
        "I mostly disagree.",
        "I'm torn — I really can't decide.",
        "I mostly agree.",
        "I completely agree with this.",
    ],
    "financial_outlook": [
        "My household's financial situation is going to get much worse.",
        "My household's financial situation is going to get a bit worse.",
        "My household's financial situation is going to stay about the same.",
        "My household's financial situation is going to get a bit better.",
        "My household's financial situation is going to get much better.",
    ],
}

# Cseh attitűd-halmazok — a cseh CCI-panelhez (fogyasztói bizalom).
# G0d: + "souhlas" (egyetértés-Likert, a HU "egyetertes" ekvivalense).
REFERENCE_SETS_CZ = {
    "financni_vyhled": [
        "Finanční situace mé domácnosti se hodně zhorší.",
        "Finanční situace mé domácnosti se mírně zhorší.",
        "Finanční situace mé domácnosti zůstane zhruba stejná.",
        "Finanční situace mé domácnosti se mírně zlepší.",
        "Finanční situace mé domácnosti se hodně zlepší.",
    ],
    "souhlas": [
        "Rozhodně s tím nesouhlasím.",
        "Spíše nesouhlasím.",
        "Tak napůl, nedokážu se rozhodnout.",
        "Spíše souhlasím.",
        "Naprosto s tím souhlasím.",
    ],
}

# Portugál attitűd-halmaz — a portugál CCI-panelhez (fogyasztói bizalom, BS-FS-NY).
REFERENCE_SETS_PT = {
    "perspetiva_financeira": [
        "A situação financeira do meu agregado familiar vai piorar muito.",
        "A situação financeira do meu agregado familiar vai piorar um pouco.",
        "A situação financeira do meu agregado familiar vai ficar mais ou menos igual.",
        "A situação financeira do meu agregado familiar vai melhorar um pouco.",
        "A situação financeira do meu agregado familiar vai melhorar muito.",
    ],
}

# Magyar attitűd-halmazok — a MI doménünk (a CCI-panel és általános egyetértés).
REFERENCE_SETS_HU = {
    "egyetertes": [
        "Egyáltalán nem értek ezzel egyet.",
        "Inkább nem értek egyet.",
        "Is-is, nem tudok dönteni.",
        "Inkább egyetértek.",
        "Teljes mértékben egyetértek.",
    ],
    "anyagi_varakozas": [
        "A háztartásom anyagi helyzete sokkal rosszabb lesz.",
        "A háztartásom anyagi helyzete kissé rosszabb lesz.",
        "A háztartásom anyagi helyzete nagyjából ugyanolyan marad.",
        "A háztartásom anyagi helyzete kissé jobb lesz.",
        "A háztartásom anyagi helyzete sokkal jobb lesz.",
    ],
}

# Lengyel pénzügyi-kilátás anchor — a PL CCI-bónuszhoz.
# G0d: + "zgoda" (egyetértés-Likert, CBOS-regiszter: "trudno powiedzieć" közép).
REFERENCE_SETS_PL = {
    "perspektywa_finansowa": [
        "Sytuacja finansowa mojego gospodarstwa domowego znacznie się pogorszy.",
        "Sytuacja finansowa mojego gospodarstwa domowego nieco się pogorszy.",
        "Sytuacja finansowa mojego gospodarstwa domowego pozostanie mniej więcej taka sama.",
        "Sytuacja finansowa mojego gospodarstwa domowego nieco się poprawi.",
        "Sytuacja finansowa mojego gospodarstwa domowego znacznie się poprawi.",
    ],
    "zgoda": [
        "Zdecydowanie się z tym nie zgadzam.",
        "Raczej się nie zgadzam.",
        "Trudno powiedzieć, nie potrafię się zdecydować.",
        "Raczej się zgadzam.",
        "Całkowicie się z tym zgadzam.",
    ],
}

# ── G0d (OPERATION PYTHIA): új nyelvi horgony-készletek ──────────────────
# Szerkezet = a HU-séma (egyetértés-Likert + háztartási anyagi kilátás), a
# megfogalmazás NATÍV survey-regiszter (nem tükörfordítás; minőség-minta: PT).
# 1 → 5: erősen negatív/elutasító → erősen pozitív/támogató.

# Francia — "foyer" (háztartás), a francia kérdőív-regiszter szerint.
REFERENCE_SETS_FR = {
    "accord": [
        "Je ne suis pas du tout d'accord avec cela.",
        "Je suis plutôt en désaccord.",
        "C'est mitigé, je n'arrive pas à me décider.",
        "Je suis plutôt d'accord.",
        "Je suis entièrement d'accord avec cela.",
    ],
    "perspective_financiere": [
        "La situation financière de mon foyer va fortement se dégrader.",
        "La situation financière de mon foyer va légèrement se dégrader.",
        "La situation financière de mon foyer va rester à peu près la même.",
        "La situation financière de mon foyer va légèrement s'améliorer.",
        "La situation financière de mon foyer va nettement s'améliorer.",
    ],
}

# Spanyol — "hogar" (a CIS-kérdőívek háztartás-terminusa).
REFERENCE_SETS_ES = {
    "acuerdo": [
        "No estoy nada de acuerdo con esto.",
        "Más bien no estoy de acuerdo.",
        "Ni lo uno ni lo otro, no sabría decidirme.",
        "Estoy bastante de acuerdo.",
        "Estoy totalmente de acuerdo con esto.",
    ],
    "perspectiva_financiera": [
        "La situación económica de mi hogar va a empeorar mucho.",
        "La situación económica de mi hogar va a empeorar un poco.",
        "La situación económica de mi hogar va a seguir más o menos igual.",
        "La situación económica de mi hogar va a mejorar un poco.",
        "La situación económica de mi hogar va a mejorar mucho.",
    ],
}

# Német — "Teils, teils" a klasszikus német survey-közép (a HU "is-is" natív párja).
REFERENCE_SETS_DE = {
    "zustimmung": [
        "Dem stimme ich überhaupt nicht zu.",
        "Ich stimme eher nicht zu.",
        "Teils, teils — ich kann mich nicht entscheiden.",
        "Ich stimme eher zu.",
        "Dem stimme ich voll und ganz zu.",
    ],
    "finanzielle_aussicht": [
        "Die finanzielle Lage meines Haushalts wird sich deutlich verschlechtern.",
        "Die finanzielle Lage meines Haushalts wird sich etwas verschlechtern.",
        "Die finanzielle Lage meines Haushalts wird in etwa gleich bleiben.",
        "Die finanzielle Lage meines Haushalts wird sich etwas verbessern.",
        "Die finanzielle Lage meines Haushalts wird sich deutlich verbessern.",
    ],
}

# Olasz — "famiglia" (az ISTAT fogyasztói felmérés háztartás-terminusa).
REFERENCE_SETS_IT = {
    "accordo": [
        "Non sono affatto d'accordo con questo.",
        "Tendenzialmente non sono d'accordo.",
        "Un po' sì e un po' no, non riesco a decidermi.",
        "Tutto sommato sono d'accordo.",
        "Sono completamente d'accordo con questo.",
    ],
    "prospettiva_finanziaria": [
        "La situazione economica della mia famiglia peggiorerà di molto.",
        "La situazione economica della mia famiglia peggiorerà un po'.",
        "La situazione economica della mia famiglia resterà più o meno la stessa.",
        "La situazione economica della mia famiglia migliorerà un po'.",
        "La situazione economica della mia famiglia migliorerà di molto.",
    ],
}

# ÁR-VÁRAKOZÁS anchor-halmazok (BS-PT-NY) — 5-pontos, TAPASZTALATI (bolti ár/rezsi/üzemanyag),
# 1 = inkább csökken/stabil … 5 = sokkal gyorsabban emelkedik. Magasabb score = magasabb árvárakozás.
REFERENCE_SETS_PRICE = {
    "HU": [
        "A bolti árak, a rezsi és az üzemanyag a következő 12 hónapban inkább csökkenni fognak.",
        "Az árak nagyjából változatlanok maradnak a következő évben.",
        "Az árak emelkednek, de lassabban, mint az elmúlt évben.",
        "Az árak körülbelül ugyanolyan gyorsan emelkednek tovább, mint eddig.",
        "Az árak sokkal gyorsabban fognak emelkedni, mint eddig.",
    ],
    "CZ": [
        "Ceny v obchodech, energie a pohonné hmoty budou v příštích 12 měsících spíše klesat.",
        "Ceny zůstanou zhruba stejné jako dosud.",
        "Ceny porostou, ale pomaleji než v uplynulém roce.",
        "Ceny porostou zhruba stejně rychle jako dosud.",
        "Ceny porostou mnohem rychleji než dosud.",
    ],
    "PT": [
        "Os preços nas lojas, a energia e os combustíveis vão sobretudo descer nos próximos 12 meses.",
        "Os preços vão manter-se mais ou menos iguais.",
        "Os preços vão subir, mas mais devagar do que no último ano.",
        "Os preços vão continuar a subir mais ou menos ao mesmo ritmo de até agora.",
        "Os preços vão subir muito mais depressa do que até agora.",
    ],
    "PL": [
        "Ceny w sklepach, rachunki i paliwo będą w ciągu najbliższych 12 miesięcy raczej spadać.",
        "Ceny pozostaną mniej więcej takie same jak dotąd.",
        "Ceny będą rosły, ale wolniej niż w minionym roku.",
        "Ceny będą rosły mniej więcej w takim samym tempie jak dotąd.",
        "Ceny będą rosły dużo szybciej niż dotąd.",
    ],
    # G0d-kiegészítés (ES/DE/EN — a G1–G2 új országokhoz). FR/IT ár-horgony
    # SZÁNDÉKOSAN nincs itt: a delphoi.REFERENCE_SETS_PRICE_EXTRA-ban él, és a
    # delphoi._anchor_set ELŐBB ezt a dictet nézi — ide másolva némán átvenné.
    "ES": [
        "Los precios en las tiendas, la energía y el combustible más bien van a bajar en los próximos 12 meses.",
        "Los precios se van a quedar más o menos como están.",
        "Los precios van a subir, pero más despacio que el año pasado.",
        "Los precios van a seguir subiendo más o menos al mismo ritmo que hasta ahora.",
        "Los precios van a subir mucho más deprisa que hasta ahora.",
    ],
    "DE": [
        "Die Preise in den Geschäften, für Energie und Kraftstoff werden in den nächsten 12 Monaten eher sinken.",
        "Die Preise werden ungefähr so bleiben wie bisher.",
        "Die Preise werden steigen, aber langsamer als im vergangenen Jahr.",
        "Die Preise werden ungefähr im gleichen Tempo weiter steigen wie bisher.",
        "Die Preise werden viel schneller steigen als bisher.",
    ],
    "EN": [
        "Prices in the shops, energy and fuel are more likely to fall over the next 12 months.",
        "Prices are going to stay roughly where they are.",
        "Prices will go up, but more slowly than in the past year.",
        "Prices will keep rising at about the same pace as now.",
        "Prices will rise much faster than they have so far.",
    ],
}

# ÜZLETI-BIZALOM anchor-halmazok (ESI szektorális bizalom panel) — 5-pontos cég-üzletmenet
# (sokkal rosszabb ↔ sokkal jobb a köv. 12 hónapban). Szektor-agnosztikus: a szektor-jelet a
# persona+prompt húzza ki (rendelésállomány/kereslet/foglalkoztatás), nem a horgony.
REFERENCE_SETS_BUSINESS = {
    "HU": [
        "A cégem üzleti helyzete sokkal rosszabb lesz a következő 12 hónapban.",
        "A cégem üzleti helyzete kissé rosszabb lesz a következő 12 hónapban.",
        "A cégem üzleti helyzete nagyjából ugyanolyan marad a következő 12 hónapban.",
        "A cégem üzleti helyzete kissé jobb lesz a következő 12 hónapban.",
        "A cégem üzleti helyzete sokkal jobb lesz a következő 12 hónapban.",
    ],
    "CZ": [
        "Obchodní situace mé firmy se v příštích 12 měsících hodně zhorší.",
        "Obchodní situace mé firmy se v příštích 12 měsících mírně zhorší.",
        "Obchodní situace mé firmy zůstane v příštích 12 měsících zhruba stejná.",
        "Obchodní situace mé firmy se v příštích 12 měsících mírně zlepší.",
        "Obchodní situace mé firmy se v příštích 12 měsících hodně zlepší.",
    ],
    "PL": [
        "Sytuacja biznesowa mojej firmy w ciągu najbliższych 12 miesięcy znacznie się pogorszy.",
        "Sytuacja biznesowa mojej firmy w ciągu najbliższych 12 miesięcy nieco się pogorszy.",
        "Sytuacja biznesowa mojej firmy w ciągu najbliższych 12 miesięcy pozostanie mniej więcej taka sama.",
        "Sytuacja biznesowa mojej firmy w ciągu najbliższych 12 miesięcy nieco się poprawi.",
        "Sytuacja biznesowa mojej firmy w ciągu najbliższych 12 miesięcy znacznie się poprawi.",
    ],
    "PT": [
        "A situação de negócio da minha empresa vai piorar muito nos próximos 12 meses.",
        "A situação de negócio da minha empresa vai piorar um pouco nos próximos 12 meses.",
        "A situação de negócio da minha empresa vai ficar mais ou menos igual nos próximos 12 meses.",
        "A situação de negócio da minha empresa vai melhorar um pouco nos próximos 12 meses.",
        "A situação de negócio da minha empresa vai melhorar muito nos próximos 12 meses.",
    ],
    "DE": [
        "Die Geschäftslage meines Unternehmens wird sich in den nächsten 12 Monaten stark verschlechtern.",
        "Die Geschäftslage meines Unternehmens wird sich in den nächsten 12 Monaten etwas verschlechtern.",
        "Die Geschäftslage meines Unternehmens wird in den nächsten 12 Monaten ungefähr gleich bleiben.",
        "Die Geschäftslage meines Unternehmens wird sich in den nächsten 12 Monaten etwas verbessern.",
        "Die Geschäftslage meines Unternehmens wird sich in den nächsten 12 Monaten stark verbessern.",
    ],
}

# ── G0d: nyelv-regiszter + kanonikus kategória-térkép ────────────────────
# A per-nyelv dictek kulcsnevei NATÍVAK (meglévő konvenció); ez a térkép adja
# a szemantikai ekvivalenciát (tesztek + többnyelvű hívók számára).
# Nyelvkód = ISO 639-1 (a delphoi COUNTRY_PANEL_CONFIG "lang" mezőjével azonos: cs, nem cz).
REFERENCE_SETS_BY_LANG = {
    "hu": REFERENCE_SETS_HU,
    "en": REFERENCE_SETS_EN,
    "cs": REFERENCE_SETS_CZ,
    "pt": REFERENCE_SETS_PT,
    "pl": REFERENCE_SETS_PL,
    "fr": REFERENCE_SETS_FR,
    "es": REFERENCE_SETS_ES,
    "de": REFERENCE_SETS_DE,
    "it": REFERENCE_SETS_IT,
}

# Kanonikus kategória → nyelvenkénti natív kulcsnév. A PT-nek nincs egyetértés-
# halmaza (a PT a minőség-referencia, nem célnyelv a G0d-körben).
ANCHOR_CATEGORY_KEYS = {
    "agreement": {
        "hu": "egyetertes", "en": "agreement", "cs": "souhlas", "pl": "zgoda",
        "fr": "accord", "es": "acuerdo", "de": "zustimmung", "it": "accordo",
    },
    "financial_outlook": {
        "hu": "anyagi_varakozas", "en": "financial_outlook", "cs": "financni_vyhled",
        "pt": "perspetiva_financeira", "pl": "perspektywa_finansowa",
        "fr": "perspective_financiere", "es": "perspectiva_financiera",
        "de": "finanzielle_aussicht", "it": "prospettiva_finanziaria",
    },
}


def get_anchor_set(lang: str, category: str) -> list[str]:
    """Kanonikus kategórianévvel ('agreement' | 'financial_outlook') adja vissza a
    natív horgony-halmazt. KeyError, ha a nyelv/kategória nincs lefedve."""
    key = ANCHOR_CATEGORY_KEYS[category][lang]
    return REFERENCE_SETS_BY_LANG[lang][key]


def _l2_normalize(x: np.ndarray) -> np.ndarray:
    """Soronkénti L2-normálás (nulla-vektor védve)."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[None, :]
    norm = np.linalg.norm(x, axis=1, keepdims=True)
    norm = np.where(norm == 0, 1.0, norm)
    return x / norm


def _softmax(logits: np.ndarray) -> np.ndarray:
    """Numerikusan stabil soronkénti softmax."""
    z = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def compute_pmf(response_embs: np.ndarray, ref_embs: np.ndarray,
                method: str = "linear", temperature: float = 1.0,
                epsilon: float = 0.0) -> np.ndarray:
    """Válasz-embeddingek (n×d) → Likert-PMF (n×K) egy anchor-halmaz (K×d) ellen.

    method="linear": (1+cos)/2 → min-kivonás (+epsilon a minimum-pozícióhoz) → L1-norm.
    method="softmax": softmax((1+cos)/2 / temperature), majd epsilon-floor + renormálás.
    """
    resp = _l2_normalize(response_embs)
    ref = _l2_normalize(ref_embs)
    sim = resp @ ref.T                      # koszinusz ∈ [-1, 1]
    cos01 = (1.0 + sim) / 2.0               # [0, 1]-re skálázva

    if method == "linear":
        cmin = cos01.min(axis=1, keepdims=True)
        num = cos01 - cmin
        if epsilon > 0:
            # tömeg a minimum-pozícióhoz (regularizáció a nulla-tömeg ellen)
            idx = np.argmin(cos01, axis=1)
            num[np.arange(num.shape[0]), idx] += epsilon
        denom = num.sum(axis=1, keepdims=True)
        denom = np.where(denom == 0, 1e-10, denom)
        return num / denom
    if method == "softmax":
        if temperature <= 0:
            raise ValueError("temperature must be > 0 for softmax")
        pmf = _softmax(cos01 / temperature)
        if epsilon > 0:
            pmf = pmf + epsilon
            pmf = pmf / pmf.sum(axis=1, keepdims=True)
        return pmf
    raise ValueError(f"unknown method: {method!r} (use 'linear' or 'softmax')")


def score_pmf(pmf: np.ndarray, scale: np.ndarray | None = None) -> np.ndarray:
    """PMF (n×K) → pont-becslés VÁRHATÓ ÉRTÉKKÉNT (nem argmax). Default skála 1..K."""
    pmf = np.asarray(pmf, dtype=np.float64)
    if pmf.ndim == 1:
        pmf = pmf[None, :]
    k = pmf.shape[1]
    if scale is None:
        scale = np.arange(1, k + 1, dtype=np.float64)
    return pmf @ np.asarray(scale, dtype=np.float64)


def _as_set_list(reference_sets):
    """Elfogad: dict{name->[K str]}, egyetlen [K str] lista, vagy listák listája."""
    if reference_sets is None:
        reference_sets = REFERENCE_SETS_HU
    if isinstance(reference_sets, dict):
        return list(reference_sets.values())
    if isinstance(reference_sets, (list, tuple)) and reference_sets and isinstance(reference_sets[0], str):
        return [list(reference_sets)]
    return [list(s) for s in reference_sets]


def _default_embed(texts, model_name: str = "all-MiniLM-L6-v2"):
    """Lokális sentence-transformers embedding (ha telepítve). Lusta import."""
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "Nincs lokális embedding: telepítsd a `sentence-transformers`-t, VAGY adj "
            "`embed_fn`-t (pl. a /embeddings API-ra), VAGY add a kész embeddingeket."
        ) from e
    if not hasattr(_default_embed, "_cache"):
        _default_embed._cache = {}
    model = _default_embed._cache.get(model_name)
    if model is None:
        model = SentenceTransformer(model_name)
        _default_embed._cache[model_name] = model
    return np.asarray(model.encode(list(texts)), dtype=np.float64)


def rate(responses, reference_sets=None, method: str = "linear",
         temperature: float = 1.0, epsilon: float = 0.0,
         embed_fn=None, model_name: str = "all-MiniLM-L6-v2", scale=None) -> dict:
    """Szabad szöveges válaszok → SSR-eredmény.

    Visszaad: {
      per_response: [{pmf:[K], score:float}],   # perszónánként
      survey_pmf:  [K],                          # a perszónák PMF-átlaga
      survey_score: float,                       # a survey-szintű várható érték
      n, k, method, n_reference_sets
    }
    """
    responses = list(responses)
    if not responses:
        return {"per_response": [], "survey_pmf": [], "survey_score": None,
                "n": 0, "k": 0, "method": method, "n_reference_sets": 0}
    sets = _as_set_list(reference_sets)
    embed = embed_fn or (lambda t: _default_embed(t, model_name))

    resp_embs = np.asarray(embed(responses), dtype=np.float64)
    # halmazonként PMF, majd a halmazok ELEMENKÉNTI átlaga (reference_set_id="mean")
    pmfs = []
    for anchors in sets:
        ref_embs = np.asarray(embed(anchors), dtype=np.float64)
        pmfs.append(compute_pmf(resp_embs, ref_embs, method=method,
                                temperature=temperature, epsilon=epsilon))
    mean_pmf = np.mean(np.stack(pmfs, axis=0), axis=0)   # (n × K)
    scores = score_pmf(mean_pmf, scale=scale)
    survey_pmf = mean_pmf.mean(axis=0)                    # (K,)
    survey_score = float(score_pmf(survey_pmf[None, :], scale=scale)[0])

    return {
        "per_response": [{"pmf": mean_pmf[i].tolist(), "score": float(scores[i])}
                         for i in range(len(responses))],
        "survey_pmf": survey_pmf.tolist(),
        "survey_score": survey_score,
        "n": len(responses), "k": mean_pmf.shape[1],
        "method": method, "n_reference_sets": len(sets),
    }
