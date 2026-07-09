"""Tests for plugins/ssr.py — Semantic Similarity Rating.

A PMF-matek tiszta numpy → embedding (sentence-transformers) NÉLKÜL tesztelhető,
injektált/ortonormált embeddingekkel. Fut pytesttel ÉS sima scriptként:
    python3 tests/test_ssr.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import plugins.ssr as ssr


def _ortho_anchors(k=5):
    """K ortonormált anchor (a standard bázis K-dim) — tiszta, ellenőrizhető geometria."""
    return np.eye(k, dtype=np.float64)


def test_pmf_rows_sum_to_one_both_methods():
    ref = _ortho_anchors(5)
    resp = np.array([[0, 0, 0, 0, 1.0], [0.3, 0.3, 0, 0, 0.9]])
    for method in ("linear", "softmax"):
        pmf = ssr.compute_pmf(resp, ref, method=method)
        assert pmf.shape == (2, 5)
        assert np.allclose(pmf.sum(axis=1), 1.0), method
        assert (pmf >= 0).all(), method


def test_pmf_peaks_at_nearest_anchor():
    ref = _ortho_anchors(5)
    resp = np.array([[0, 0, 0, 0, 1.0]])     # pontosan az 5. anchor
    for method in ("linear", "softmax"):
        pmf = ssr.compute_pmf(resp, ref, method=method)
        assert int(np.argmax(pmf[0])) == 4, method


def test_linear_collapses_to_exact_anchor():
    # linear: az ortogonális, pontos egyezésnél a min-kivonás → 1.0 tömeg a csúcson
    ref = _ortho_anchors(5)
    resp = np.array([[0, 0, 0, 0, 1.0]])
    pmf = ssr.compute_pmf(resp, ref, method="linear")
    assert pmf[0, 4] > 0.99
    score = ssr.score_pmf(pmf)[0]
    assert abs(score - 5.0) < 1e-6


def test_score_is_monotonic():
    ref = _ortho_anchors(5)
    low = np.array([[1.0, 0, 0, 0, 0]])      # az 1. anchor (Likert 1)
    high = np.array([[0, 0, 0, 0, 1.0]])     # az 5. anchor (Likert 5)
    for method in ("linear", "softmax"):
        s_low = ssr.score_pmf(ssr.compute_pmf(low, ref, method=method))[0]
        s_high = ssr.score_pmf(ssr.compute_pmf(high, ref, method=method))[0]
        assert s_low < s_high, method
        assert 1.0 <= s_low <= 5.0 and 1.0 <= s_high <= 5.0, method


def test_softmax_temperature_sharpens():
    ref = _ortho_anchors(5)
    resp = np.array([[0, 0, 0, 0, 1.0]])
    hot = ssr.compute_pmf(resp, ref, method="softmax", temperature=1.0)
    cold = ssr.compute_pmf(resp, ref, method="softmax", temperature=0.1)
    # alacsonyabb hőmérséklet → élesebb eloszlás → magasabb csúcs
    assert cold[0].max() > hot[0].max()


def test_softmax_invalid_temperature_raises():
    ref = _ortho_anchors(5)
    resp = np.array([[0, 0, 0, 0, 1.0]])
    try:
        ssr.compute_pmf(resp, ref, method="softmax", temperature=0.0)
        assert False, "0 temperature-nek hibát kéne dobnia"
    except ValueError:
        pass


def test_epsilon_adds_floor_mass_linear():
    ref = _ortho_anchors(5)
    resp = np.array([[0, 0, 0, 0, 1.0]])
    pmf0 = ssr.compute_pmf(resp, ref, method="linear", epsilon=0.0)
    pmf1 = ssr.compute_pmf(resp, ref, method="linear", epsilon=0.2)
    # epsilon a minimum-pozícióhoz ad tömeget → a nulla-tömegű pontok már nem 0 mind
    assert (pmf0 == 0).sum() > (pmf1 == 0).sum()
    assert np.allclose(pmf1.sum(axis=1), 1.0)


def test_rate_with_injected_embed():
    # injektált embed_fn: minden szöveg → fix vektor (a PMF-úton a survey-szint becsül)
    vocab = {
        "nagyon rossz": [1.0, 0, 0, 0, 0],
        "nagyon jó": [0, 0, 0, 0, 1.0],
    }
    anchors = ["a", "b", "c", "d", "e"]
    ortho = {a: list(np.eye(5)[i]) for i, a in enumerate(anchors)}

    def embed_fn(texts):
        out = []
        for t in texts:
            if t in ortho:
                out.append(ortho[t])
            else:
                out.append(vocab.get(t, [0.2, 0.2, 0.2, 0.2, 0.2]))
        return np.asarray(out, dtype=np.float64)

    res = ssr.rate(["nagyon jó", "nagyon rossz"],
                   reference_sets={"s": anchors}, method="linear", embed_fn=embed_fn)
    assert res["n"] == 2 and res["k"] == 5
    assert 1.0 <= res["survey_score"] <= 5.0
    # "nagyon jó" magasabb pontot kap, mint "nagyon rossz"
    assert res["per_response"][0]["score"] > res["per_response"][1]["score"]
    assert abs(sum(res["survey_pmf"]) - 1.0) < 1e-9


def test_multiple_reference_sets_averaged():
    res = None
    anchors_a = ["a", "b", "c"]

    def embed_fn(texts):
        m = {"a": [1, 0, 0], "b": [0, 1, 0], "c": [0, 0, 1], "x": [0, 0, 1.0]}
        return np.asarray([m.get(t, [0.3, 0.3, 0.3]) for t in texts], dtype=np.float64)

    res = ssr.rate(["x"], reference_sets={"s1": anchors_a, "s2": anchors_a},
                   method="linear", embed_fn=embed_fn)
    assert res["n_reference_sets"] == 2
    assert res["k"] == 3
    assert int(np.argmax(res["per_response"][0]["pmf"])) == 2   # "x" ~ "c"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
        passed += 1
    print(f"\nSSR: {passed}/{len(fns)} teszt OK")


if __name__ == "__main__":
    _run_all()
