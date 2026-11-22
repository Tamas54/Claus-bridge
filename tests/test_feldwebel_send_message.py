"""A FELDWEBEL send_message ESZKÖZE (Faber #6, 2026-11-22).

A hiba: a Feldwebel `send_message` eszköze minden hívásra azt adta vissza, hogy
„send_message() got an unexpected keyword argument 'caller'”, mert a responder `caller="feldwebel"`-lel hívta a
server.send_message-et, annak pedig nincs `caller` paramétere (a jogosultság-kapu ott a `sender`-t nézi).
Érintett címzett: cli-claus (a „cli-Claus, …” posta, lásd tests/test_cimzett.py).

Két próba:
1. végig: a Feldwebel-eszköz → a valódi server.send_message → a sor a messages táblában, cli-claus címzettel;
2. őr az egész hibafajtára: a Feldwebel minden `capture_state["_…_func"]`-hívásának kulcsszavas paraméterét
   összeveti a server.py-ban hozzákötött függvény valódi szignatúrájával (ugyanez a hiba máshol se csússzon be).
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from pathlib import Path

GYOKER = Path(__file__).resolve().parent.parent


def test_a_feldwebel_uzenete_a_cli_claus_postajaba_er(get_db, monkeypatch):
    import server
    from feldwebel import responder
    monkeypatch.setattr(server, "get_db", get_db)

    class Ctx:
        capture_state = {"_send_message_func": server.send_message}
    Ctx.get_db = staticmethod(get_db)

    v = json.loads(asyncio.run(responder._execute_tool(
        "send_message", {"recipient": "cli-claus", "subject": "Próba", "message": "Szia, a Feldwebel vagyok."}, Ctx())))
    assert "error" not in v, v
    assert v["status"] == "sent"
    conn = get_db()
    sor = conn.execute("SELECT sender, recipient, subject, message, thread_id, id FROM messages").fetchone()
    conn.close()
    assert tuple(sor)[:4] == ("feldwebel", "cli-claus", "Próba", "Szia, a Feldwebel vagyok.")
    assert sor["thread_id"] == sor["id"] == v["message_id"]


def test_ures_targy_alapertelmezest_kap_es_hianyzo_cimzett_kimondott_hiba(get_db, monkeypatch):
    import server
    from feldwebel import responder
    monkeypatch.setattr(server, "get_db", get_db)

    class Ctx:
        capture_state = {"_send_message_func": server.send_message}
    Ctx.get_db = staticmethod(get_db)

    v = json.loads(asyncio.run(responder._execute_tool(
        "send_message", {"recipient": "cli-claus", "message": "tárgy nélkül"}, Ctx())))
    assert v["status"] == "sent"
    conn = get_db()
    assert conn.execute("SELECT subject FROM messages").fetchone()[0] == "Feldwebel üzenet"
    conn.close()
    v = json.loads(asyncio.run(responder._execute_tool("send_message", {"message": "kinek?"}, Ctx())))
    assert v["error"] == "recipient és message szükséges"


# ── őr: a Feldwebel → Bridge-függvény hívások paraméterei egyezzenek a valódi szignatúrával ──

def _kotesek() -> dict[str, str]:
    """A server.py `_capture_state["_x_func"] = fuggveny` sorai: kulcs → a függvény neve."""
    fa = ast.parse((GYOKER / "server.py").read_text(encoding="utf-8"))
    ki = {}
    for n in ast.walk(fa):
        if (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Subscript)
                and isinstance(n.value, ast.Name)):
            cel = n.targets[0]
            if (isinstance(cel.value, ast.Name) and cel.value.id == "_capture_state"
                    and isinstance(cel.slice, ast.Constant) and str(cel.slice.value).endswith("_func")):
                ki[cel.slice.value] = n.value.id
    return ki


def _kulcs(kif) -> str | None:
    """`ctx.capture_state.get("_x_func")` vagy `ctx.capture_state["_x_func"]` → "_x_func"."""
    if (isinstance(kif, ast.Call) and isinstance(kif.func, ast.Attribute) and kif.func.attr == "get"
            and isinstance(kif.func.value, ast.Attribute) and kif.func.value.attr == "capture_state"
            and kif.args and isinstance(kif.args[0], ast.Constant)):
        return kif.args[0].value
    if (isinstance(kif, ast.Subscript) and isinstance(kif.value, ast.Attribute) and kif.value.attr == "capture_state"
            and isinstance(kif.slice, ast.Constant)):
        return kif.slice.value
    return None


def _hivasok() -> list[tuple[str, set[str], int]]:
    """A responder.py minden Bridge-függvényhívása: (kulcs, a kulcsszavas paraméterek, sor)."""
    fa = ast.parse((GYOKER / "feldwebel" / "responder.py").read_text(encoding="utf-8"))
    ki = []
    for fv in ast.walk(fa):
        if not isinstance(fv, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        valtozo = {}        # a függvényen belül: változónév → kulcs
        for n in ast.walk(fv):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                k = _kulcs(n.value)
                if k:
                    valtozo[n.targets[0].id] = k
        for n in ast.walk(fv):
            if not isinstance(n, ast.Call):
                continue
            k = valtozo.get(n.func.id) if isinstance(n.func, ast.Name) else _kulcs(n.func)
            if k and k.endswith("_func"):
                ki.append((k, {kw.arg for kw in n.keywords if kw.arg}, n.lineno))
    return ki


def test_a_feldwebel_bridge_hivasai_a_valodi_szignaturaval_egyeznek():
    import server
    kotesek = _kotesek()
    hivasok = _hivasok()
    assert "_send_message_func" in kotesek and any(k == "_send_message_func" for k, _, _ in hivasok)
    hibak = []
    for kulcs, kulcsszavak, sor in hivasok:
        assert kulcs in kotesek, f"responder.py:{sor}: a(z) {kulcs} nincs bekötve a server.py-ban"
        fv = getattr(server, kotesek[kulcs])
        param = inspect.signature(fv).parameters
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in param.values()):
            continue
        for kw in sorted(kulcsszavak - set(param)):
            hibak.append(f"responder.py:{sor}: {kotesek[kulcs]}() nem fogad {kw!r} paramétert")
    assert not hibak, "\n".join(hibak)
