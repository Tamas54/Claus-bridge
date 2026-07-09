"""Tests for plugins/llm_cache.py — deterministic LLM-call cache.

Fut pytesttel ÉS sima scriptként:
    python3 tests/test_llm_cache.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from plugins.llm_cache import LLMCache, cache_key


def test_key_is_deterministic():
    k1 = cache_key("flash", {"temperature": 0.8}, "sys", "user", 0)
    k2 = cache_key("flash", {"temperature": 0.8}, "sys", "user", 0)
    assert k1 == k2 and len(k1) == 32


def test_key_param_order_independent():
    k1 = cache_key("m", {"a": 1, "b": 2}, "s", "u", 0)
    k2 = cache_key("m", {"b": 2, "a": 1}, "s", "u", 0)
    assert k1 == k2   # sort_keys=True → a sorrend nem számít


def test_key_iteration_differs():
    k0 = cache_key("m", {}, "s", "u", 0)
    k1 = cache_key("m", {}, "s", "u", 1)
    assert k0 != k1   # cellánkénti több független minta külön kulcs


def test_key_changes_on_prompt():
    assert cache_key("m", {}, "s", "u1", 0) != cache_key("m", {}, "s", "u2", 0)
    assert cache_key("m", {}, "s1", "u", 0) != cache_key("m", {}, "s2", "u", 0)
    assert cache_key("m1", {}, "s", "u", 0) != cache_key("m2", {}, "s", "u", 0)


def test_get_set_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        c = LLMCache(os.path.join(d, "c.db"))
        key = cache_key("m", {"t": 0.8}, "s", "u", 0)
        assert c.get(key) is None                 # miss
        c.set(key, "PÁRT: Tisza", model="m")
        assert c.get(key) == "PÁRT: Tisza"        # hit
        c.close()


def test_get_or_set_runs_producer_once():
    with tempfile.TemporaryDirectory() as d:
        c = LLMCache(os.path.join(d, "c.db"))
        key = cache_key("m", {}, "s", "u", 0)
        calls = {"n": 0}

        def producer():
            calls["n"] += 1
            return "válasz"

        v1, cached1 = c.get_or_set(key, producer, model="m")
        v2, cached2 = c.get_or_set(key, producer, model="m")
        assert v1 == v2 == "válasz"
        assert cached1 is False and cached2 is True
        assert calls["n"] == 1                    # a producer csak egyszer futott
        assert c.stats()["entries"] == 1
        c.close()


def test_persists_across_instances():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.db")
        key = cache_key("m", {}, "s", "u", 0)
        c1 = LLMCache(path)
        c1.set(key, "marad", model="m")
        c1.close()
        c2 = LLMCache(path)                        # új példány, ugyanaz a fájl
        assert c2.get(key) == "marad"
        c2.close()


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
        passed += 1
    print(f"\nLLMCache: {passed}/{len(fns)} teszt OK")


if __name__ == "__main__":
    _run_all()
