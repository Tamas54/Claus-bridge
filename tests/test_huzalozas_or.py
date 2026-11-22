"""HUZALOZÁS-ŐR (2026-11-22): `/chat/completions`-re közvetlen hívás CSAK az engedélylistán lehet.

A HELYZET, AMI EZT KIVÁLTOTTA
-----------------------------
A Kommandant: „A bridge-nek az anthropic kulcsunkkal is működnie kell tudni, ha rizsrakéták nem mennek, kivéve a
delphoi funkciót.” Az Anthropic-tartalék a közös kapuban (`server.sf_chat`) él — de 18 hívóhely saját, közvetlen
SiliconFlow-POST-tal kerülte meg: SF-kiesésnél (402) ezek egyszerűen álltak. Mind a kapu mögé került (a pluginok és a
Feldwebel a `llm_kapu.kapun()`-on át). Ez a teszt őrzi, hogy új közvetlen hívás ne csússzon be észrevétlenül: a
legjobb tartalék is nulla, ha nincs rákötve arra a csőre, ami elromlik (a `test_sf_selfheal` 08-30-i tanulsága).

Hogyan mér: a repó minden saját `.py` fájljában a SZÖVEG-LITERÁLOKAT (f-string darabokat is) nézi — megjegyzést és
docstringet nem —, és minden `chat/completions`-találatot a legbelső befoglaló függvényhez köt.
"""
from __future__ import annotations

import ast
import os
import pathlib

import pytest

GYOKER = pathlib.Path(__file__).resolve().parent.parent
KIHAGYOTT_MAPPAK = {".claude", ".git", "tests", ".venv", "venv", "node_modules", "__pycache__"}

#: (fájl, a befoglaló függvények egyike) — „*” = az egész fájl. Mindegyiknek oka van; új sor csak indokkal.
ENGEDELYLISTA = {
    # a kapu belseje
    ("server.py", "_sf_post_once"): "a közös kapu (`sf_chat`) egyetlen SiliconFlow-hívása",
    ("llm_kapu.py", "_kozvetlen_sf"): "a régi közvetlen út, ha nincs bejegyzett kapu (önálló plugin-teszt)",
    # magát a SiliconFlow-t méri, Anthropicra váltani értelmetlen lenne
    ("server.py", "_probe_siliconflow_model"): "az önvizsgálat SiliconFlow-modellpróbája",
    ("plugins/nulltarif_guard.py", "*"): "a SiliconFlow-árőr (NULLTARIF)",
    # a kapu nem streamel — kiesésnél a kapura vált (`_kapun_helyett`)
    ("youngereka_chat.py", "_call_model"): "a családi chat streamje; kiesésnél a kapun át az Anthropic válaszol",
    # DELPHOI és motorja: a Kommandant kivétele — marad SiliconFlow-n, Anthropic-útja nincs
    ("plugins/pollster.py", "*"): "DELPHOI-motor (szintetikus panel)",
    ("plugins/persona_sampler.py", "*"): "DELPHOI-motor",
    ("plugins/ssr.py", "*"): "DELPHOI-motor",
    ("szim_backteszt.py", "*"): "a döntés-szimulátor backteszt panel-útja (DELPHOI-panel)",
}


def _engedett_fajl(rel: str) -> bool:
    """A backteszt- és próbaszkriptek, és a DELPHOI-modulok egészben."""
    p = pathlib.PurePosixPath(rel)
    if p.parts[0].startswith("orakel_"):
        return True                                          # orakel_* fájlok és mappák (backteszt)
    if len(p.parts) == 1 and p.name.startswith("run_"):
        return True                                          # run_*_backtest.py, run_first_nowcast_*.py
    if p.parts[0] == "yt_title_test":
        return True
    if p.parts[0] == "vertical_plugins" and p.name.startswith("test_"):
        return True
    if p.parts[0] == "plugins" and p.name.startswith("delphoi"):
        return True
    return (rel, "*") in ENGEDELYLISTA


def talalatok(forras: str) -> list[tuple[int, tuple[str, ...]]]:
    """`[(sor, befoglaló függvények kívülről befelé)]` minden `chat/completions`-t tartalmazó szöveg-literálra."""
    fa = ast.parse(forras)
    docstringek = set()
    for n in ast.walk(fa):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            elso = n.body[0]
            if isinstance(elso, ast.Expr) and isinstance(elso.value, ast.Constant):
                docstringek.add(id(elso.value))
    fuggvenyek = [n for n in ast.walk(fa) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    ki = []
    for n in ast.walk(fa):
        if (isinstance(n, ast.Constant) and isinstance(n.value, str) and "chat/completions" in n.value
                and id(n) not in docstringek):
            lanc = tuple(f.name for f in sorted((f for f in fuggvenyek if f.lineno <= n.lineno <= f.end_lineno),
                                                key=lambda f: f.lineno))
            ki.append((n.lineno, lanc))
    return ki


def _sajat_fajlok():
    for gyoker, mappak, fajlok in os.walk(GYOKER):
        mappak[:] = [m for m in mappak if m not in KIHAGYOTT_MAPPAK]
        for f in fajlok:
            if f.endswith(".py"):
                ut = pathlib.Path(gyoker) / f
                yield ut.relative_to(GYOKER).as_posix(), ut


def _sertesek() -> tuple[list[str], set]:
    sertes, hasznalt = [], set()
    for rel, ut in _sajat_fajlok():
        if _engedett_fajl(rel):
            continue
        try:
            forras = ut.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "chat/completions" not in forras:
            continue
        for sor, lanc in talalatok(forras):
            kulcs = next(((rel, f) for f in lanc if (rel, f) in ENGEDELYLISTA), None)
            if kulcs:
                hasznalt.add(kulcs)
            else:
                sertes.append(f"{rel}:{sor} ({'.'.join(lanc) or '(modul)'})")
    return sertes, hasznalt


def test_nincs_kozvetlen_hivas_a_kapu_mellett():
    sertes, _ = _sertesek()
    assert not sertes, (
        "közvetlen `/chat/completions`-hívás a kapu MELLETT — SiliconFlow-kiesésnél (402) ez a hely áll, nincs "
        "Anthropic-tartaléka. Hívd a közös kaput: a szerverben `sf_chat(payload, purpose=…)`, pluginban/Feldwebelben "
        "`await llm_kapu.kapun(payload, purpose=…)`. Ha tényleg SiliconFlow-specifikus (DELPHOI, árőr, próba), az "
        "ENGEDELYLISTA-ba indokkal. Találatok:\n  " + "\n  ".join(sertes))


def test_az_engedelylista_nem_rohad():
    """A függvényre szóló engedély sora ÉLŐ hívást takarjon — a halott sor egy jövőbeli kerülőnek adna fedezéket."""
    _, hasznalt = _sertesek()
    # az engedett fájlokat a `_sertesek` átugorja, ezért a függvény-szintű sorokat külön mérjük
    for (rel, fn), ok in ENGEDELYLISTA.items():
        if fn == "*":
            continue
        forras = (GYOKER / rel).read_text(encoding="utf-8")
        assert any(fn in lanc for _, lanc in talalatok(forras)), f"halott engedély: {rel}::{fn} ({ok})"


def test_az_or_tenyleg_megfogja_az_uj_kerulot():
    """Önpróba: egy új plugin közvetlen POST-ja (f-stringben is) a teszten elbukna; a megjegyzés és a docstring nem."""
    forras = (
        'async def uj_fuggveny(kliens, alap):\n'
        '    """Ez a docstring említheti a /chat/completions-t."""\n'
        '    # a megjegyzés is: /chat/completions\n'
        '    return await kliens.post(f"{alap}/chat/completions", json={})\n'
    )
    t = talalatok(forras)
    assert t == [(4, ("uj_fuggveny",))]
    assert not _engedett_fajl("plugins/uj_plugin.py")
    assert _engedett_fajl("plugins/delphoi_valami.py") and _engedett_fajl("orakel_backteszt/x/run.py")
    assert _engedett_fajl("run_esi_sector_backtest.py") and not _engedett_fajl("feldwebel/run_x.py")


@pytest.mark.parametrize("rel", [
    "plugins/draft_reply.py", "plugins/email_triage.py", "plugins/semantic_triage.py", "plugins/signal_gatekeeper.py",
    "plugins/agora_duty.py", "plugins/thread_digest.py", "plugins/action_extract.py",
    "feldwebel/briefing.py", "feldwebel/triage.py", "feldwebel/responder.py",
])
def test_a_kapu_mogotti_modulok_a_kozos_belepot_hivjak(rel):
    """A 11-22-én átkötött modulok a közös belépőt használják (nem csak a közvetlen hívás tűnt el belőlük)."""
    forras = (GYOKER / rel).read_text(encoding="utf-8")
    assert "chat/completions" not in forras.replace("`/chat/completions`", "")
    assert "llm_kapu.kapun(" in forras or "llm_hivas(" in forras


def test_a_szerver_bejegyzi_a_kaput():
    """MODUL-SZINTEN (az import = az indulás), nem a `__main__`-ben és nem függvényben: a `python server.py` és minden
    `import server` bekötött kaput lát."""
    fa = ast.parse((GYOKER / "server.py").read_text(encoding="utf-8"))
    felso = [ast.unparse(n) for n in fa.body if isinstance(n, ast.Expr)]
    assert "llm_kapu.kapu_beallit(sf_chat)" in felso
