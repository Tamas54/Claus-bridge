#!/usr/bin/env python3
"""Pyramid modul lokális teszt — profilok, kontextus, memory, RAG, governance."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyramid.agents import (
    AGENT_REGISTRY, load_profile, get_kommandant_profile,
    get_bridge_info, get_team_info, get_agent_personality
)
from pyramid.context_builder import build_agent_context
from pyramid.memory_shared import (
    add_to_shared_memory, load_shared_memory,
    get_shared_memory_summary, search_shared_memory, endorse_entry
)
from pyramid.memory_rag import (
    add_to_agent_rag, load_agent_rag,
    get_agent_rag_summary, search_agent_rag
)
from pyramid.governance import classify_result, store_result


def test_profiles():
    print("=" * 60)
    print("TESZT 1: Profilok betöltése")
    print("=" * 60)

    k = load_profile("kommandant")
    assert k["name"] == "Dr. Csizmadia Tamás", "Kommandant név hibás!"
    print(f"  Kommandant: {k['name']} ({k['nickname']})")

    b = load_profile("bridge_info")
    assert b["name"] == "Claus-Bridge", "Bridge név hibás!"
    print(f"  Bridge: {b['name']} — {b['hosting']}")

    t = load_profile("team")
    assert "kimi" in t, "Kimi hiányzik a csapatból!"
    assert "deepseek" in t, "DeepSeek hiányzik!"
    assert "glm5" in t, "GLM-5 hiányzik!"
    print(f"  Csapattagok: {', '.join(t.keys())}")
    print("  OK\n")


def test_agent_registry():
    print("=" * 60)
    print("TESZT 2: Agent Registry")
    print("=" * 60)

    for agent_id, config in AGENT_REGISTRY.items():
        print(f"  {agent_id}: {config['model_id']} (temp={config['default_temperature']})")
    assert len(AGENT_REGISTRY) == 4, f"Registry méret hibás: {len(AGENT_REGISTRY)}"
    print("  OK\n")


def test_personality():
    print("=" * 60)
    print("TESZT 3: Agent személyiségek")
    print("=" * 60)

    for agent_id in ["kimi", "deepseek", "glm5"]:
        persona = get_agent_personality(agent_id)
        assert agent_id in persona, f"{agent_id} személyisége nem tartalmazza a nevét!"
        print(f"  {agent_id}: {persona[:80]}...")
    print("  OK\n")


def test_context_builder():
    print("=" * 60)
    print("TESZT 4: Context Builder (Kimi)")
    print("=" * 60)

    ctx = build_agent_context("kimi", include_shared_memory=False, include_rag=False)
    assert "IDENTITÁSOD" in ctx, "Identitás szekció hiányzik!"
    assert "KOMMANDANT" in ctx, "Kommandant szekció hiányzik!"
    assert "CLAUS-BRIDGE" in ctx, "Bridge szekció hiányzik!"
    assert "VISELKEDÉSI SZABÁLYOK" in ctx, "Viselkedési szabályok hiányzik!"

    lines = ctx.split("\n")
    print(f"  Kontextus méret: {len(ctx)} karakter, {len(lines)} sor")
    print(f"  Első 3 sor:")
    for line in lines[:3]:
        print(f"    {line}")
    print("  OK\n")


def test_shared_memory():
    print("=" * 60)
    print("TESZT 5: Shared Memory")
    print("=" * 60)

    entry = add_to_shared_memory(
        content="A Pyramid projekt elindult, Fázis 1 kész.",
        category="projekt",
        added_by="cli-claus"
    )
    print(f"  Hozzáadva: {entry['id']} — {entry['content'][:50]}")

    entry2 = add_to_shared_memory(
        content="A GDP előrejelzés 2026-ra: 2.8% (Makronóm becslés).",
        category="eredmény",
        added_by="kimi"
    )
    print(f"  Hozzáadva: {entry2['id']} — {entry2['content'][:50]}")

    endorsed = endorse_entry(entry["id"], "deepseek")
    assert endorsed is not None, "Endorse sikertelen!"
    assert "deepseek" in endorsed["endorsed_by"], "Endorser nem került be!"
    print(f"  Endorsed: {endorsed['id']} by deepseek (score: {endorsed['relevance_score']})")

    results = search_shared_memory("GDP")
    assert len(results) > 0, "Keresés nem talált semmit!"
    print(f"  Keresés 'GDP': {len(results)} találat")

    summary = get_shared_memory_summary()
    assert "Közös tudásbázis" in summary, "Summary formátum hibás!"
    print(f"  Summary: {len(summary)} karakter")
    print("  OK\n")


def test_rag():
    print("=" * 60)
    print("TESZT 6: Per-agent RAG")
    print("=" * 60)

    add_to_agent_rag("kimi", "Kutattam a vámtarifák hatását az EU GDP-re.", "Vámtarifa kutatás")
    add_to_agent_rag("kimi", "Összefoglaltam 5 forrást a deglobalizációról.", "Deglobalizáció elemzés")
    add_to_agent_rag("deepseek", "Lefordítottam a Makronóm briefet angolra.", "Fordítás")

    kimi_rag = load_agent_rag("kimi")
    assert len(kimi_rag) == 2, f"Kimi RAG méret hibás: {len(kimi_rag)}"
    print(f"  Kimi RAG: {len(kimi_rag)} bejegyzés")

    ds_rag = load_agent_rag("deepseek")
    assert len(ds_rag) == 1, f"DeepSeek RAG méret hibás: {len(ds_rag)}"
    print(f"  DeepSeek RAG: {len(ds_rag)} bejegyzés")

    results = search_agent_rag("kimi", "vámtarifa GDP")
    assert len(results) > 0, "RAG keresés nem talált semmit!"
    print(f"  Keresés 'vámtarifa GDP' (kimi): {len(results)} találat")

    summary = get_agent_rag_summary("kimi")
    assert "Saját korábbi munkáid" in summary, "RAG summary formátum hibás!"
    print(f"  Kimi RAG summary: {len(summary)} karakter")
    print("  OK\n")


def test_governance():
    print("=" * 60)
    print("TESZT 7: Governance")
    print("=" * 60)

    c1 = classify_result("Ez egy GDP elemzés a Makronóm számára.", "kimi", "GDP teszt")
    assert c1 == "both", f"Várt: both, kapott: {c1}"
    print(f"  'GDP elemzés Makronóm' → {c1}")

    c2 = classify_result("Lefordítottam a szöveget angolra.", "deepseek", "Fordítás")
    assert c2 == "rag", f"Várt: rag, kapott: {c2}"
    print(f"  'Fordítás' → {c2}")

    classification = store_result(
        content="A Pyramid rendszer tesztelése sikeres volt.",
        agent_id="glm5",
        task_title="Pyramid teszt"
    )
    assert classification == "both", f"Pyramid kulcsszó nem triggerelte a shared-et: {classification}"
    print(f"  store_result(Pyramid teszt) → {classification}")
    print("  OK\n")


def test_full_context_with_memory():
    print("=" * 60)
    print("TESZT 8: Teljes kontextus shared memory-val és RAG-gal")
    print("=" * 60)

    ctx = build_agent_context("kimi")
    assert "Közös tudásbázis" in ctx, "Shared memory nem jelenik meg a kontextusban!"
    assert "Saját korábbi munkáid" in ctx, "RAG nem jelenik meg a kontextusban!"
    print(f"  Teljes kontextus: {len(ctx)} karakter")
    print("  OK\n")


def cleanup_test_data():
    """Teszt adatok törlése."""
    import pathlib
    shared_path = pathlib.Path(__file__).parent.parent / "data" / "pyramid_shared_memory.json"
    rag_dir = pathlib.Path(__file__).parent.parent / "data" / "pyramid_rag"

    if shared_path.exists():
        shared_path.unlink()
        print("  Shared memory törölve")

    for f in rag_dir.glob("*_rag.json"):
        f.unlink()
        print(f"  RAG törölve: {f.name}")


if __name__ == "__main__":
    print("\n🔺 PYRAMID TESZT INDÍTÁSA\n")

    try:
        test_profiles()
        test_agent_registry()
        test_personality()
        test_context_builder()
        test_shared_memory()
        test_rag()
        test_governance()
        test_full_context_with_memory()

        print("=" * 60)
        print("MINDEN TESZT SIKERES!")
        print("=" * 60)

    finally:
        print("\nTeszt adatok takarítása...")
        cleanup_test_data()
        print("Kész.\n")
