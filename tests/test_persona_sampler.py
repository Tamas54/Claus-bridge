"""Tests for plugins/persona_sampler.py — KL-kvóta perszóna-mintavétel.
Fut pytesttel ÉS scriptként: python3 tests/test_persona_sampler.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from plugins.persona_sampler import sample_personas, kl_report

DIMS = {
    "age": [("18-29", 0.16), ("30-39", 0.17), ("40-49", 0.18), ("50-64", 0.27), ("65+", 0.22)],
    "edu": [("alap", 0.22), ("szak", 0.27), ("érett", 0.30), ("diploma", 0.21)],
    "tel": [("Bp", 0.18), ("megyei", 0.20), ("város", 0.32), ("falu", 0.30)],
}


def test_returns_n_personas_with_all_dims():
    personas, kl = sample_personas(DIMS, n=80, seed=1)
    assert len(personas) == 80
    for p in personas:
        assert set(p.keys()) == {"id", "age", "edu", "tel"}
        assert p["age"] in [l for l, _ in DIMS["age"]]


def test_deterministic_with_seed():
    a, _ = sample_personas(DIMS, n=50, seed=7)
    b, _ = sample_personas(DIMS, n=50, seed=7)
    assert [p["age"] for p in a] == [p["age"] for p in b]
    assert [p["edu"] for p in a] == [p["edu"] for p in b]


def test_kl_small_for_large_n():
    _, kl = sample_personas(DIMS, n=400, seed=3)
    for d, v in kl.items():
        assert v < 0.02, f"{d} KL too high: {v}"


def test_quota_beats_naive_at_small_n():
    # kvóta-korrekciós KL <= naiv független mintavétel KL (átlagban, több seeden)
    def naive(n, seed):
        rng = random.Random(seed)
        out = []
        for i in range(n):
            p = {"id": i}
            for d, opts in DIMS.items():
                r = rng.random(); acc = 0.0; pick = opts[-1][0]
                for lab, w in opts:
                    acc += w
                    if r <= acc:
                        pick = lab; break
                p[d] = pick
            out.append(p)
        return out

    quota_wins = 0
    for seed in range(12):
        _, kl_q = sample_personas(DIMS, n=40, seed=seed)
        kl_n = kl_report(naive(40, seed), DIMS)
        if sum(kl_q.values()) <= sum(kl_n.values()):
            quota_wins += 1
    # a kvótának az esetek többségében jobbnak (kisebb KL) kell lennie kis N-en
    assert quota_wins >= 8, f"quota csak {quota_wins}/12-ben győzött"


def test_kl_report_matches():
    personas, kl = sample_personas(DIMS, n=200, seed=5)
    kl2 = kl_report(personas, DIMS)
    for d in DIMS:
        assert abs(kl[d] - kl2[d]) < 1e-9


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ✓ {fn.__name__}")
    print(f"\npersona_sampler: {len(fns)}/{len(fns)} teszt OK")


if __name__ == "__main__":
    _run_all()
