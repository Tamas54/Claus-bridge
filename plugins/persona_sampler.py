"""plugins/persona_sampler.py — KL-kvóta-balanszolt perszóna-mintavétel (LOOT #4).

A failure mode, amit kezel (recon/LOOT.md #4): szelektív demográfiai érzékenység /
marginális drift kis N-nél. A naiv `weighted_pick` NEM korrigál → kis mintán a
generált eloszlás elsodródhat a céltól. Ez batch-kvóta-korrekciót ad: minden húzásnál
a már generált arányt kivonja a célból (`adjusted = max(target − generated, 0)`,
renormálva), így a marginális eloszlás KONVERGÁL a célhoz. Plusz KL-divergencia riport.

KORLÁT (őszinte): a dimenziókat továbbra is FÜGGETLENÜL mintázza — a korreláció
(kor×iskola×település együttes) NEM modellezett. Az a LOOT #9 (joint/IPF, nagy munka).
A PersonaVerse `sample_space.py` (CC0/MIT) logikájának reimplementációja.

Használat:
    dims = {"age": [("18-29",0.16),("30-39",0.17),...], "edu": [...], ...}
    personas, kl = sample_personas(dims, n=80, seed=42)
    # personas: [{"id":0,"age":"30-39","edu":"diploma",...}, ...]
    # kl: {"age": 0.002, "edu": 0.001, ...}  (kisebb = jobb illeszkedés)
"""
from __future__ import annotations

import random

import numpy as np
from scipy.stats import entropy


def _normalize(targets):
    t = np.asarray(targets, dtype=np.float64)
    s = t.sum()
    return t / s if s > 0 else t


def sample_personas(dims: dict, n: int = 80, seed: int = 42) -> tuple[list[dict], dict]:
    """dims: {dimenzió_név: [(label, target_prob), ...]}. Visszaad: (personas, kl_report)."""
    rng = random.Random(seed)
    labels = {d: [l for l, _ in opts] for d, opts in dims.items()}
    targets = {d: _normalize([t for _, t in opts]) for d, opts in dims.items()}
    counts = {d: np.zeros(len(opts), dtype=np.float64) for d, opts in dims.items()}

    personas = []
    for i in range(n):
        p = {"id": i}
        for d in dims:
            tot = counts[d].sum()
            genp = counts[d] / tot if tot > 0 else np.zeros_like(counts[d])
            adj = np.clip(targets[d] - genp, 0.0, None)   # kvóta-hiány
            if adj.sum() == 0:                            # cél elérve → a cél szerint húz
                adj = targets[d]
            adj = adj / adj.sum()
            # súlyozott választás
            r = rng.random()
            acc = 0.0
            idx = len(adj) - 1
            for j, w in enumerate(adj):
                acc += w
                if r <= acc:
                    idx = j
                    break
            p[d] = labels[d][idx]
            counts[d][idx] += 1
        personas.append(p)

    eps = 1e-9
    kl = {}
    for d in dims:
        gen = _normalize(counts[d])
        # KL(generált || cél)
        kl[d] = float(entropy(gen + eps, targets[d] + eps))
    return personas, kl


def kl_report(personas: list[dict], dims: dict) -> dict:
    """Egy meglévő perszóna-lista marginálisainak KL-divergenciája a célhoz (utólagos audit)."""
    eps = 1e-9
    out = {}
    for d, opts in dims.items():
        labels = [l for l, _ in opts]
        targets = _normalize([t for _, t in opts])
        counts = np.array([sum(1 for p in personas if p.get(d) == l) for l in labels], dtype=np.float64)
        gen = _normalize(counts) if counts.sum() > 0 else counts
        out[d] = float(entropy(gen + eps, targets + eps))
    return out


# ── G0d (OPERATION PYTHIA): ORSZÁG-KONFIG RÉTEG ──────────────────────────
# A sample_personas/kl_report VÁLTOZATLAN (zéró regresszió) — ez a réteg csak
# egy kvóta-regisztert + kényelmi belépőt ad fölé. A HU a pollster.py-ban
# validált KSH/NMHH készletből töltődik (lusta import → EGY forrás, nincs
# számsor-duplikáció). Az új országok kvótaszámait a G0c ország-mátrix agent
# hozza (Eurostat) — addig a helyük TODO, a get_country_dims beszédes hibával
# utasítja el őket.


def _hu_dims_from_pollster() -> dict:
    """HU marginálisok — kanonikus forrás: plugins/pollster.py (KSH mun0005/mun0006
    + 2022 cenzus + NMHH 2026-05). Lusta import: a sampler numpy/scipy-only marad."""
    from plugins import pollster
    return {
        "age": list(pollster.AGE),
        "settlement": list(pollster.SETTLEMENT),
        "edu": list(pollster.EDU),
        "media": [(label, w) for label, w in pollster.MEDIA],
    }


# Regiszter-sor: {"status": "live"|"todo", "source": <adatforrás>, "loader": callable|None}.
# A loader () -> dims formát ad ({dim: [(label, target_prob), ...]}) — közvetlenül
# a sample_personas bemenete. TODO-soroknál loader=None.
_EUROSTAT_TODO = ("TODO(G0c): Eurostat demo_pjan (kor) + edat_lfs_9903 (végzettség) "
                  "+ degurba/cenzus (településtípus) + helyi médiafogyasztás-mérés")


def _delphoi_dims(country: str):
    """FR/IT (G2) marginálisok — kanonikus forrás: plugins/delphoi.py
    COUNTRY_PANEL_CONFIG (nowcast-grade, Eurostat-verifikált a G0c mátrixban:
    demo_pjan + edat_lfse_03 + edat_lfs_9913 degurba). Lusta import, EGY forrás
    — a HU/pollster mintája. A media (label, w, bucket) hármasból (label, w) lesz."""
    def load():
        from plugins import delphoi
        cfg = delphoi.COUNTRY_PANEL_CONFIG[country]
        dims = {d: list(opts) for d, opts in cfg["dims"].items()}
        dims["media"] = [(label, w) for label, w, _bucket in cfg["media"]]
        return dims
    return load


COUNTRY_QUOTAS: dict = {
    "HU": {
        "status": "live",
        "source": "KSH mun0005/mun0006 + 2022 cenzus + NMHH 2026-05 (pollster.py a kanonikus tár)",
        "loader": _hu_dims_from_pollster,
    },
    # G2 (PYTHIA): FR/IT feltöltve a G0c-verifikált Eurostat-marginálisokból
    # (delphoi.COUNTRY_PANEL_CONFIG a kanonikus tár, nowcast-grade konfig).
    "FR": {
        "status": "live",
        "source": "Eurostat-marginálisok (G0c mátrix-verifikáció) — delphoi.COUNTRY_PANEL_CONFIG['FR'] a kanonikus tár",
        "loader": _delphoi_dims("FR"),
    },
    "IT": {
        "status": "live",
        "source": "Eurostat-marginálisok (G0c mátrix-verifikáció) — delphoi.COUNTRY_PANEL_CONFIG['IT'] a kanonikus tár",
        "loader": _delphoi_dims("IT"),
    },
    # G3 (ORSZÁG-BŐVÍTÉS, 2026-07-21): DE Eurostat-lekérdezésből, UK/US
    # dokumentált statikus kvóták — kanonikus tár mindhármnál a
    # delphoi.COUNTRY_PANEL_CONFIG (részletes forrás-kommentek ott).
    "DE": {
        "status": "live",
        "source": ("Eurostat-lekérdezés 2026-07-21 (statdata MCP): demo_pjangroup 2025 (kor, 18+ bázis 69,58M) "
                   "+ edat_lfs_9913 2024 (ISCED-sávok) + ilc_lvho01 2024 (degurba) — "
                   "delphoi.COUNTRY_PANEL_CONFIG['DE'] a kanonikus tár"),
        "loader": _delphoi_dims("DE"),
    },
    "UK": {
        "status": "live",
        "source": ("STATIKUS ONS-alapú kvóták (Eurostat nem szolgál friss UK-t, ONS nincs a statdata-rétegen): "
                   "ONS mid-2023 population estimates (kor) + Census 2021/OECD EAG (végzettség) + "
                   "ONS/DEFRA rural-urban classification (település) — delphoi.COUNTRY_PANEL_CONFIG['UK']"),
        "loader": _delphoi_dims("UK"),
    },
    "US": {
        "status": "live",
        "source": ("STATIKUS Census/ACS-alapú kvóták: Census Bureau 2023 population estimates (kor) + "
                   "ACS 2023 educational attainment 25+ (végzettség) + Census 2020 urban-rural (település) — "
                   "delphoi.COUNTRY_PANEL_CONFIG['US']"),
        "loader": _delphoi_dims("US"),
    },
    # Többi célország — a G0c ország-mátrix tölti fel (loader vagy literal dims):
    "CZ": {"status": "todo", "source": _EUROSTAT_TODO, "loader": None},
    "ES": {"status": "todo", "source": _EUROSTAT_TODO, "loader": None},
    "PL": {"status": "todo", "source": _EUROSTAT_TODO, "loader": None},
    "PT": {"status": "todo", "source": _EUROSTAT_TODO, "loader": None},
}


def get_country_dims(country: str) -> dict:
    """Ország-kód (pl. 'HU') → dims a sample_personas-hoz. Beszédes hibával áll
    meg, ha az ország ismeretlen vagy a kvótái még nincsenek feltöltve."""
    entry = COUNTRY_QUOTAS.get((country or "").upper())
    if entry is None:
        raise KeyError(f"ismeretlen ország a COUNTRY_QUOTAS-ban: {country!r} "
                       f"(elérhető: {', '.join(sorted(COUNTRY_QUOTAS))})")
    if entry.get("loader") is None:
        raise ValueError(f"{country.upper()}: a kvóták még nincsenek feltöltve — {entry['source']}")
    return entry["loader"]()


def sample_country_personas(country: str, n: int = 80, seed: int = 42) -> tuple[list[dict], dict]:
    """Kényelmi belépő: COUNTRY_QUOTAS[country] kvótáival hívja a sample_personas-t.
    Ugyanaz a motor, ugyanaz a (personas, kl_report) kimenet."""
    return sample_personas(get_country_dims(country), n=n, seed=seed)
