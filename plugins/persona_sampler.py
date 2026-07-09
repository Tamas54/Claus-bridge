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
