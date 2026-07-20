"""G0d (OPERATION PYTHIA) — embedding-nyelvteszt a többnyelvű SSR-horgonyokra.

Kérdés: az OpenAI text-embedding-3-small nyelvi minősége elég-e az új nyelvekre?
Mérce: horgony-KATEGÓRIA-szeparáció nyelvenként —
  - within  = kategórián belüli átlagos páronkénti koszinusz-hasonlóság
  - between = különböző kategóriák mondatpárjai közti átlagos hasonlóság
  - separation = within − between  (nagyobb = jobb; a küszöböt a HU/PT
    referencia kalibrálja: ALARM, ha separation < 0.5 × min(HU, PT))
Másodlagos jel: scale_coherence — a Likert-sorrend geometriai koherenciája
kategóriánként (Spearman ρ a |i−j| rang-távolság és az 1−cos távolság közt;
1.0 = a horgonyok tökéletes gradiensen ülnek).

Futtatás:  .venv/bin/python orakel_backteszt/run_embedding_nyelvteszt.py
Kimenet:   orakel_backteszt/embedding_nyelvteszt_20260720.json + .md
"""
from __future__ import annotations

import itertools
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
from scipy.stats import spearmanr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import plugins.ssr as ssr  # noqa: E402

EMBED_MODEL = "text-embedding-3-small"
OUT_JSON = os.path.join(ROOT, "orakel_backteszt", "embedding_nyelvteszt_20260720.json")
OUT_MD = os.path.join(ROOT, "orakel_backteszt", "embedding_nyelvteszt_20260720.md")
REFERENCE_LANGS = ("hu", "pt")            # ezekből kalibráljuk a küszöböt
ALARM_FACTOR = 0.5                        # ALARM, ha sep < 0.5 × min(HU, PT)

LANG_TO_COUNTRY = {"hu": "HU", "cs": "CZ", "pt": "PT", "pl": "PL",
                   "es": "ES", "de": "DE", "en": "EN", "fr": "FR", "it": "IT"}


def _load_env_key(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    env_path = os.path.join(ROOT, ".env")
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"{name} nincs sem env-ben, sem {env_path}-ben")


def collect_categories() -> dict:
    """lang → {category_label: [mondatok]} — ssr.py készletek + PRICE/BUSINESS
    ország-sorok + (ha importálható) a delphoi REGARD/GROWTH/PRICE_EXTRA sorai."""
    cats: dict = {}
    for lang, sets in ssr.REFERENCE_SETS_BY_LANG.items():
        cats[lang] = {f"ssr/{k}": list(v) for k, v in sets.items()}
        country = LANG_TO_COUNTRY[lang]
        if country in ssr.REFERENCE_SETS_PRICE:
            cats[lang][f"ssr/price[{country}]"] = list(ssr.REFERENCE_SETS_PRICE[country])
        if country in ssr.REFERENCE_SETS_BUSINESS:
            cats[lang][f"ssr/business[{country}]"] = list(ssr.REFERENCE_SETS_BUSINESS[country])
    try:
        from plugins import delphoi
        for lang, v in delphoi.REFERENCE_SETS_REGARD.items():
            cats.setdefault(lang, {})[f"delphoi/regard"] = list(v)
        for lang, v in delphoi.REFERENCE_SETS_GROWTH.items():
            cats.setdefault(lang, {})[f"delphoi/growth"] = list(v)
        for lang, v in delphoi.REFERENCE_SETS_PRICE_EXTRA.items():
            cats.setdefault(lang, {})[f"delphoi/price_extra"] = list(v)
    except Exception as e:  # noqa: BLE001 — a delphoi-sorok bónusz, nem feltétel
        print(f"  (delphoi-készletek kihagyva: {e})")
    return cats


def embed_all(texts: list[str], api_key: str) -> dict:
    """Egyetlen batch-hívás → {mondat: L2-normált vektor}."""
    import httpx
    r = httpx.post("https://api.openai.com/v1/embeddings", timeout=120,
                   headers={"Authorization": f"Bearer {api_key}"},
                   json={"model": EMBED_MODEL, "input": texts})
    r.raise_for_status()
    payload = r.json()
    vecs = [d["embedding"] for d in sorted(payload["data"], key=lambda x: x["index"])]
    usage = payload.get("usage", {})
    out = {}
    for t, v in zip(texts, vecs):
        v = np.asarray(v, dtype=np.float64)
        out[t] = v / (np.linalg.norm(v) or 1.0)
    return {"vectors": out, "usage": usage}


def _pair_sims(vs: list[np.ndarray]) -> list[float]:
    return [float(a @ b) for a, b in itertools.combinations(vs, 2)]


def analyze_lang(cat_map: dict, vec: dict) -> dict:
    per_cat = {}
    within_all = []
    for cat, sents in cat_map.items():
        vs = [vec[s] for s in sents]
        sims = _pair_sims(vs)
        within = float(np.mean(sims))
        within_all.append(within)
        # Likert-gradiens koherencia: |i−j| vs (1 − cos) rang-korreláció
        idx_d, emb_d = [], []
        for (i, a), (j, b) in itertools.combinations(enumerate(vs), 2):
            idx_d.append(abs(i - j)); emb_d.append(1.0 - float(a @ b))
        rho = spearmanr(idx_d, emb_d).correlation
        per_cat[cat] = {"n": len(sents), "within_sim": round(within, 4),
                        "scale_coherence": round(float(rho), 4) if rho == rho else None}
    between = []
    for ca, cb in itertools.combinations(cat_map, 2):
        for sa in cat_map[ca]:
            for sb in cat_map[cb]:
                between.append(float(vec[sa] @ vec[sb]))
    within_mean = float(np.mean(within_all))
    between_mean = float(np.mean(between)) if between else None
    return {
        "n_categories": len(cat_map),
        "n_sentences": sum(len(s) for s in cat_map.values()),
        "within_mean": round(within_mean, 4),
        "between_mean": round(between_mean, 4) if between_mean is not None else None,
        "separation": round(within_mean - between_mean, 4) if between_mean is not None else None,
        "per_category": per_cat,
    }


def main():
    api_key = _load_env_key("OPENAI_API_KEY")
    cats = collect_categories()
    all_sentences = sorted({s for by_cat in cats.values() for sents in by_cat.values() for s in sents})
    print(f"Nyelvek: {', '.join(sorted(cats))} — {len(all_sentences)} egyedi mondat, 1 batch-hívás…")
    emb = embed_all(all_sentences, api_key)
    vec, usage = emb["vectors"], emb["usage"]

    results = {lang: analyze_lang(by_cat, vec) for lang, by_cat in sorted(cats.items())}

    ref_seps = [results[l]["separation"] for l in REFERENCE_LANGS if results.get(l, {}).get("separation")]
    threshold = round(ALARM_FACTOR * min(ref_seps), 4)
    for lang, r in results.items():
        r["alarm"] = bool(r["separation"] is not None and r["separation"] < threshold)

    report = {
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": EMBED_MODEL,
        "usage_tokens": usage,
        "metric": "separation = mean(within-category cos-sim) − mean(between-category cos-sim)",
        "threshold": {"formula": f"{ALARM_FACTOR} × min(separation[{', '.join(REFERENCE_LANGS)}])",
                      "reference_separations": {l: results[l]["separation"] for l in REFERENCE_LANGS},
                      "value": threshold},
        "results": results,
        "alarms": [l for l, r in results.items() if r["alarm"]],
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    lines = [
        "# Embedding-nyelvteszt — G0d (2026-07-20)",
        "",
        f"Modell: `{EMBED_MODEL}` · {len(all_sentences)} egyedi horgonymondat · "
        f"{usage.get('total_tokens', '?')} token · 1 batch-hívás",
        "",
        "Mérce: **separation = within − between** (kategórián belüli vs kategóriák "
        "közti átlagos koszinusz-hasonlóság). Küszöb (HU/PT-referenciából kalibrálva): "
        f"**ALARM, ha separation < {threshold}** "
        f"(= {ALARM_FACTOR} × min(HU={results['hu']['separation']}, PT={results['pt']['separation']})).",
        "",
        "| nyelv | kat. | mondat | within | between | separation | min. scale-koherencia | státusz |",
        "|-------|------|--------|--------|---------|------------|----------------------|---------|",
    ]
    for lang in sorted(results):
        r = results[lang]
        coh = [c["scale_coherence"] for c in r["per_category"].values() if c["scale_coherence"] is not None]
        ref = " (ref)" if lang in REFERENCE_LANGS else ""
        status = "**ALARM — ÖSSZEOMLOTT**" if r["alarm"] else "OK"
        lines.append(f"| {lang}{ref} | {r['n_categories']} | {r['n_sentences']} | "
                     f"{r['within_mean']} | {r['between_mean']} | **{r['separation']}** | "
                     f"{min(coh) if coh else '—'} | {status} |")
    lines += ["", "Kategória-részletek a JSON-ban (`embedding_nyelvteszt_20260720.json`)."]
    if report["alarms"]:
        lines += ["", f"## ⚠ ALARM: {', '.join(report['alarms'])} — a szeparáció a küszöb "
                      "alatt, az adott nyelv SSR-horgonyai így NEM megbízhatók."]
    else:
        lines += ["", "Egyik nyelven sem omlott össze a szeparáció."]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(json.dumps({l: {"sep": r["separation"], "alarm": r["alarm"]}
                      for l, r in results.items()}, indent=2))
    print(f"Küszöb: {threshold} — alarmok: {report['alarms'] or 'nincs'}")
    print(f"→ {OUT_JSON}\n→ {OUT_MD}")


if __name__ == "__main__":
    main()
