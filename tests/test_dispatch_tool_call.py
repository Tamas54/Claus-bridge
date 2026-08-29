"""
TOOL CALL MINDEN MÓDBAN — a dispatch-út eszköz-kapuja.

Kommandant-követelmény (2026-08-24): „azt mindenképp jegyezzük meg: tool call
minden módban kelleni fog." Kiváltó ok az ai_task #395: dispatch módban a
keresésre utasított agent NEM tudott keresni, és ezt NEM JELEZTE — a modell
kiírta a tool-hívást sima szövegként, a hívó azt hitte, dolgozott, a szintézis
pedig kipótolta a hiányt kitalált adattal és HAMIS forrásmegjelöléssel.

A követelmény két fele, és mindkettőre van itt teszt:
  1. ESZKÖZ MINDEN ÚTVONALON — ami broadcast módban megvan, legyen meg
     dispatchban is.
  2. HANGOS HIBA — ha nincs eszköz, az legyen LÁTHATÓ HIBA, ne néma szöveg.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyramid.task_dispatcher as td  # noqa: E402


def _run(coro):
    # asyncio.run(), nem get_event_loop(): a teljes svitben egy korábbi teszt
    # már lezárhatta a hurkot, és ezek a tesztek önmagukban zöldek, együtt
    # pirosak lettek. Saját hurok tesztenként — a teszt ne függjön attól,
    # ki futott előtte.
    return asyncio.run(coro)


def _no_side_effects(monkeypatch):
    """A teszt nem ír se memóriába, se RAG-ba, se governance-be."""
    monkeypatch.setattr(td, "build_agent_context", lambda **kw: "SYS")
    monkeypatch.setattr(td, "store_result", lambda **kw: None)


# ── 1. HANGOS HIBA ──────────────────────────────────────────────────────────

def test_kiirt_toolhivas_hibas_futas_eszkoztelen_uton(monkeypatch):
    _no_side_effects(monkeypatch)
    stored = []
    monkeypatch.setattr(td, "store_result", lambda **kw: stored.append(kw))

    async def tool_less(**kwargs):
        # pontosan az, ami a 395-ösben történt
        return {"response": '<|tool_call_begin|>functions.web_search{"q":"x"}', "tokens": {}}

    out = _run(td.dispatch_parallel_tasks({"kimi": {"prompt": "keress rá"}}, call_agent_func=tool_less))

    assert out["kimi"]["error"] == "unhandled_tool_call"
    assert "NINCS eszköz-dispatcher" in out["kimi"]["response"]
    # És ami a legfontosabb: a kitalált szöveg NEM került a közös memóriába.
    assert stored == []


def test_a_hibauzenet_megnevezi_a_markert(monkeypatch):
    _no_side_effects(monkeypatch)

    async def tool_less(**kwargs):
        return {"response": "bla <｜｜DSML｜｜invoke name=\"web_search\">", "tokens": {}}

    out = _run(td.dispatch_parallel_tasks({"deepseek": {"prompt": "x"}}, call_agent_func=tool_less))
    assert out["deepseek"]["marker"] == "<｜｜DSML｜｜"


def test_minden_ismert_marker_fogva_van():
    for marker in td.TEXT_MARKER_TOKENS:
        assert td.looks_like_unhandled_tool_call(f"szöveg {marker} szöveg") == marker


def test_a_rendes_valasz_atmegy(monkeypatch):
    # A kapu másik fele: egy őr, ami mindent megfog, használhatatlan.
    _no_side_effects(monkeypatch)
    stored = []
    monkeypatch.setattr(td, "store_result", lambda **kw: stored.append(kw))

    async def tool_less(**kwargs):
        return {"response": "A KSH szerint a júniusi infláció 3,1% volt.", "tokens": {}}

    out = _run(td.dispatch_parallel_tasks({"kimi": {"prompt": "x"}}, call_agent_func=tool_less))
    assert "error" not in out["kimi"]
    assert len(stored) == 1


def test_ures_es_hianyzo_valasz_nem_szall_el(monkeypatch):
    _no_side_effects(monkeypatch)

    async def weird(**kwargs):
        return {"tokens": {}}  # nincs "response" kulcs

    out = _run(td.dispatch_parallel_tasks({"kimi": {"prompt": "x"}}, call_agent_func=weird))
    assert "kimi" in out


# ── 2. ESZKÖZ MINDEN ÚTVONALON ──────────────────────────────────────────────

def test_dispatch_az_eszkozos_hurkot_hasznalja_ha_van(monkeypatch):
    _no_side_effects(monkeypatch)
    seen = {}

    async def with_tools(model_id, messages, max_rounds=4, max_tokens=3000):
        seen["model"] = model_id
        seen["messages"] = messages
        seen["max_rounds"] = max_rounds
        return "3,1% (forrás: KSH)"

    async def tool_less(**kwargs):
        raise AssertionError("az eszköztelen útra esett vissza, pedig volt eszközös hívó")

    out = _run(
        td.dispatch_parallel_tasks(
            {"kimi": {"prompt": "keress rá", "max_rounds": 6}},
            call_agent_func=tool_less,
            run_with_tools_func=with_tools,
        )
    )
    assert out["kimi"]["tools"] is True
    assert out["kimi"]["response"] == "3,1% (forrás: KSH)"
    assert seen["model"] == "kimi"
    assert seen["max_rounds"] == 6
    # a rendszer-prompt és a feladat is átment, a megfelelő szerepekben
    assert seen["messages"][0]["role"] == "system"
    assert seen["messages"][1]["content"] == "keress rá"


def test_az_eszkoz_feladatonkent_kikapcsolhato(monkeypatch):
    _no_side_effects(monkeypatch)

    async def with_tools(*a, **kw):
        raise AssertionError("use_tools=False mellett nem szabad az eszközös úton mennie")

    async def tool_less(**kwargs):
        return {"response": "rendben", "tokens": {}}

    out = _run(
        td.dispatch_parallel_tasks(
            {"kimi": {"prompt": "x", "use_tools": False}},
            call_agent_func=tool_less,
            run_with_tools_func=with_tools,
        )
    )
    assert out["kimi"]["tools"] is False


def test_az_eszkoz_alapertelmezesben_be_van_kapcsolva(monkeypatch):
    # A követelmény szíve: nem opt-in, hanem alapértelmezés.
    _no_side_effects(monkeypatch)

    async def with_tools(*a, **kw):
        return "ok"

    out = _run(
        td.dispatch_parallel_tasks(
            {"kimi": {"prompt": "x"}},  # nincs use_tools kulcs
            call_agent_func=None,
            run_with_tools_func=with_tools,
        )
    )
    assert out["kimi"]["tools"] is True


def test_az_eszkozos_uton_is_hibas_a_kiirt_hivas(monkeypatch):
    # Ott azt jelenti: a hurok elfogyott a körökből, és a modell utolsó szava
    # egy végre nem hajtott hívás. Ez sem sikeres futás.
    _no_side_effects(monkeypatch)

    async def with_tools(*a, **kw):
        return "<|tool_call_begin|>functions.web_search"

    out = _run(
        td.dispatch_parallel_tasks(
            {"kimi": {"prompt": "x"}},
            run_with_tools_func=with_tools,
        )
    )
    assert out["kimi"]["error"] == "unhandled_tool_call"
    assert "elfogyott a körökből" in out["kimi"]["response"]


# ── 3. A KÉT MARKER-LISTA NEM CSÚSZHAT SZÉT ─────────────────────────────────

def test_marker_lista_paritas_a_serverrel():
    """A server.py-ban és itt is él egy TEXT_MARKER_TOKENS. A duplikátum
    kényszer (a server importálja a pyramidot, visszafelé nem lehet), ezért
    tesztnek kell őriznie — különben egy új modell markere csak az egyik
    helyre kerül be, és a másik út némán újra hazudik."""
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "server.py"), encoding="utf-8") as handle:
        source = handle.read()
    # A kommenteket ELŐBB le kell vágni: a nem-mohó zárás a komment
    # ZÁRÓJELÉRE illeszkedne ("(full-width pipes, double)"), és a lista
    # felét csendben elveszítenénk — a mérőeszköz mondaná, hogy stimmel.
    start = source.index("TEXT_MARKER_TOKENS = (")
    block, depth = [], 0
    for line in source[start:].splitlines():
        code = line.split("#", 1)[0]
        block.append(code)
        depth += code.count("(") - code.count(")")
        if depth == 0 and block:
            break
    names = re.findall(r'"([^"]+)"', "\n".join(block))
    assert list(td.TEXT_MARKER_TOKENS) == names
