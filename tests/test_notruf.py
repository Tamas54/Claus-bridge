#!/usr/bin/env python3
"""OPERATION NOTRUF — vészjelzés. A §9 tesztkör.

    python tests/test_notruf.py

Az 5-ös a kapuőr: a beszélgetésből SEMMI nem szivároghat a kimenő
jelzésbe. Ha szivárogna, a lábléc-ígéret csendben hazugsággá válna —
pont a legrosszabb pillanatban.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sqlite3
import sys
import tempfile
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for k, v in (("YR_TOKEN", "REKA_TOKEN_1234567890abcdefgh"),
             ("AN_TOKEN", "ANNA_TOKEN_zyxwvu0987654321ab"),
             ("BL_TOKEN", "BELLA_TOKEN_qwerty0987654321"),
             ("HQ_TOKEN", "HQ_TOKEN_abcdefgh1234567890"),
             ("HQ_PIN", "483927")):
    os.environ[k] = v

import youngereka_chat as chat      # noqa: E402
import youngereka_hq as hq          # noqa: E402
import youngereka_notruf as notruf  # noqa: E402

hibak: list[str] = []


def ok(f, c):
    print(("  ✓ " if f else "  ✗ ") + c)
    if not f:
        hibak.append(c)


def szakasz(c):
    print(f"\n── {c} " + "─" * max(0, 54 - len(c)))


tmp = pathlib.Path(tempfile.mkdtemp())
conn = sqlite3.connect(tmp / "n.db")
conn.row_factory = sqlite3.Row
chat.ensure_schema(conn)

kuldott: list[str] = []


async def _push_ok(t):
    kuldott.append(t)


async def _push_bukik(t):
    raise RuntimeError("Telegram halott")


# ============================================================
szakasz("1, 4. A jelzés elmegy")

r = asyncio.run(notruf.send(conn, "YoungeReka", "Réka", "tamas", "",
                            telegram_push=_push_ok, event=chat.event))
ok(r["sikeres"], "1: kísérő üzenet NÉLKÜL is elmegy")
ok(len(kuldott) == 1 and "VÉSZJELZÉS" in kuldott[0], "megérkezett a push")
ok("Réka" in kuldott[0], "a jelzésben ott a neve")
ok("Nem írt mellé semmit" in kuldott[0], "…és hogy nem írt semmit")
print("     →", kuldott[0].replace("\n", " | ")[:150])

r = asyncio.run(notruf.send(conn, "AnnaKatheder", "Anna", "tamas",
                            "Nem bírom tovább, kérlek szólj neki.",
                            telegram_push=_push_ok, event=chat.event))
ok("Nem bírom tovább" in kuldott[-1], "4: a KÍSÉRŐ üzenet átmegy")
ok("Anna" in kuldott[-1], "a küldő neve helyes")


# ============================================================
szakasz("5. A BESZÉLGETÉSBŐL SEMMI — a kapuőr")

# Ültetett kanári a beszélgetésbe
sid = str(uuid.uuid4())
conn.execute("INSERT INTO yr_chat_sessions (id,instance,title,created_at,"
             "updated_at) VALUES (?,?,?,?,?)",
             (sid, "YoungeReka", "MOKUSFA_7781 a címben", chat._now(), chat._now()))
for szoveg in ("A jelszavam MOKUSFA_7781 és ez titkos.",
               "Még egyszer: MOKUSFA_7781."):
    conn.execute("INSERT INTO yr_chat_messages (id,session_id,role,content,"
                 "created_at) VALUES (?,?,?,?,?)",
                 (str(uuid.uuid4()), sid, "user", szoveg, chat._now()))
conn.commit()

kuldott.clear()
asyncio.run(notruf.send(conn, "YoungeReka", "Réka", "tamas", "",
                        telegram_push=_push_ok, event=chat.event))
ok("MOKUSFA_7781" not in kuldott[-1],
   "5: a beszélgetésbe ültetett kanári NEM szerepel a jelzésben")
ok("titkos" not in kuldott[-1], "…és a beszélgetés egyetlen szava sem")
ok("NEM olvasta a beszélgetését" in kuldott[-1],
   "a jelzés maga kimondja, hogy nem olvasott bele")

# a tool-leírás is tiltja a kiemelést
vesz = [t for t in hq.TOOLS if t["name"] == "veszjelzes"][0]
ok("SOHA ne írj ide olyat, amit te" in vesz["params"]["properties"]["uzenet"]["description"],
   "a tool-paraméter EXPLICITEN tiltja a kiemelést")


# ============================================================
szakasz("6. Ha a küldés BUKIK — beszédes hiba, nem néma")

r = asyncio.run(notruf.send(conn, "YoungeReka", "Réka", "tamas", "baj van",
                            telegram_push=_push_bukik, event=chat.event))
ok(r["sikeres"] is False, "6: a bukás jelzett")
ok("Nem tudtam elérni" in r["uzenet"], "6: kimondja, hogy nem sikerült")
ok("Hívd fel" in r["uzenet"], "6: telefonálásra irányít")
ok("112" in r["uzenet"] and "116 123" in r["uzenet"],
   "6: a krízis-számok is ott vannak")
ok("hiba" not in r["uzenet"].lower() or "Nem tudtam" in r["uzenet"],
   "6: nem nyers hibaüzenet")
print("     →", r["uzenet"].replace("\n", " ")[:180])

# siker esetén is kapja a számokat
r = asyncio.run(notruf.send(conn, "YoungeReka", "Réka", "tamas", "",
                            telegram_push=_push_ok, event=chat.event))
ok("Szóltam Tamásnak" in r["uzenet"] and ":" in r["uzenet"],
   "sikernél: „Szóltam Tamásnak, HH:MM-kor”")
ok("112" in r["uzenet"], "…és a krízis-számok akkor is")
ok("10 percen belül" in r["uzenet"], "…és hogy mit tegyen, ha nem jelentkezik")


# ============================================================
szakasz("7. Napló — a tény igen, a szöveg NEM")

sorok = conn.execute("SELECT * FROM chat_events WHERE kind='notruf_sent'").fetchall()
ok(len(sorok) >= 4, f"7: notruf_sent bekerült a chat_events-be ({len(sorok)})")
egesz = " ".join(str(dict(s)) for s in sorok)
ok("Nem bírom tovább" not in egesz, "7: a kísérő ÜZENET NEM került a naplóba")
ok("baj van" not in egesz, "7: …egyik sem")
ok("MOKUSFA_7781" not in egesz, "7: …és a beszélgetés sem")
ok("cimzett=tamas" in egesz and "sikeres=" in egesz,
   "7: a címzett és a siker viszont naplózva")

audit = conn.execute("SELECT COUNT(*) c FROM oversight_audit").fetchone()["c"]
ok(audit == 0, "oversight_audit sor NEM keletkezik — ez nem betekintés")


# ============================================================
szakasz("8. Vendég — nincs vészjelzés, de vannak számok")

r = asyncio.run(notruf.send(conn, "guest-abc123", "Vendég", "tamas", "baj",
                            telegram_push=_push_ok, event=chat.event))
ok(r.get("jogosulatlan") is True, "8: vendégnek NINCS vészjelzése")
ok("guest-abc123" not in " ".join(kuldott), "8: …és nem is ment ki semmi")
ok(not hq.tools_for("guest-abc123"), "8: a vendégnek nincs eszköze")
ok("112" in notruf.krizis_blokk() and "116 123" in notruf.krizis_blokk(),
   "8: a krízis-számok viszont megvannak neki is")


# ============================================================
szakasz("2, 3. A modell oldala — eszköz minden családi felületen")

for prof in ("YoungeReka", "AnnaKatheder", "Bella", "kommandant"):
    nevek = {t["name"] for t in hq.tools_for(prof)}
    ok("veszjelzes" in nevek, f"{prof}: megvan a veszjelzes eszköz")
    b = hq.capability_block(prof, conn)
    ok("veszjelzes" in b, f"{prof}: …és a promptban is szerepel")

leiras = vesz["leiras"]
ok("AZONNAL" in leiras and "ne kérdezz vissza" in leiras,
   "2: egyértelmű kérésre AZONNAL küld")
ok("bizonytalan" in leiras and "kérdezd meg" in leiras,
   "3: bizonytalan jelre rákérdez")
ok("fogadd el" in leiras, "3: …és a nemet elfogadja")
ok(not vesz["pin"], "a vészjelzéshez NEM kell PIN")


szakasz("TESZT-jelölés — csak a Telegramon")

kuldott.clear()
os.environ["NOTRUF_TESZT"] = "1"
r = asyncio.run(notruf.send(conn, "YoungeReka", "Réka", "tamas", "próba",
                            telegram_push=_push_ok, event=chat.event))
ok("[TESZT]" in kuldott[-1], "a Telegram-üzenet MEG VAN jelölve")
ok("EZ PRÓBA" in kuldott[-1], "…és megmondja, hogyan kapcsolható ki")
ok("[TESZT]" not in r["uzenet"],
   "a FELHASZNÁLÓ felé menő szöveg VÁLTOZATLAN (ott nem segítene)")
sor = conn.execute("SELECT detail FROM chat_events WHERE kind='notruf_sent' "
                   "ORDER BY created_at DESC LIMIT 1").fetchone()
ok("TESZT" not in (sor["detail"] or ""), "…és a napló is változatlan")

del os.environ["NOTRUF_TESZT"]
r = asyncio.run(notruf.send(conn, "YoungeReka", "Réka", "tamas", "éles",
                            telegram_push=_push_ok, event=chat.event))
ok("[TESZT]" not in kuldott[-1], "kikapcsolva NINCS jelölés")
ok("🚨" in kuldott[-1], "…és visszajön az éles jel")

for ertek in ("0", "", "nem", "false"):
    os.environ["NOTRUF_TESZT"] = ertek
    asyncio.run(notruf.send(conn, "YoungeReka", "Réka", "tamas", "",
                            telegram_push=_push_ok, event=chat.event))
    ok("[TESZT]" not in kuldott[-1], f"NOTRUF_TESZT={ertek!r} → ÉLES (fail-safe)")
os.environ.pop("NOTRUF_TESZT", None)


szakasz("Telefonszám — koppintható")

os.environ["NOTRUF_TAMAS_SZAM"] = "+36 30 391 1579"
r = asyncio.run(notruf.send(conn, "YoungeReka", "Réka", "tamas", "",
                            telegram_push=_push_ok, event=chat.event))
ok("tel:+36303911579" in r["uzenet"],
   "sikernél KOPPINTHATÓ tel: link (szóköz nélkül a sémában)")
ok("+36 30 391 1579" in r["uzenet"], "…olvashatóan is kiírva")
r = asyncio.run(notruf.send(conn, "YoungeReka", "Réka", "tamas", "",
                            telegram_push=_push_bukik, event=chat.event))
ok("tel:+36303911579" in r["uzenet"], "bukásnál is koppintható")
ok("Hívd fel most" in r["uzenet"], "…és az utasítás egyértelmű")
ok("tel:112" in notruf.krizis_blokk() and "tel:116123" in notruf.krizis_blokk(),
   "a krízis-számok is koppinthatók")
del os.environ["NOTRUF_TAMAS_SZAM"]
r = asyncio.run(notruf.send(conn, "YoungeReka", "Réka", "tamas", "",
                            telegram_push=_push_bukik, event=chat.event))
ok("tel:" not in r["uzenet"].split("Ha most kell")[0],
   "szám nélkül NEM hazudik számot")
ok("telefonon" in r["uzenet"], "…hanem szám nélkül irányít")


conn.close()

szakasz("MARKER-SZIVÁRGÁS — amit a felhasználó SOHA nem láthat")

# Élesben megtörtént: a Kimi a tool-hívást SZÖVEGKÉNT írta ki
# („<|tool_call_begin|>functions.jelenlet:0…"), és az a felhasználó
# buborékjában landolt. Kétféle védelem kell, és mindkettő önállóan is:
#   1. a tool-körben KINYERJÜK a szövegből (a Bridge meglévő parserével)
#   2. a streamből KIVÁGJUK, hogy sose látszódjon
SZIVARGAS = ("Ellenőrzöm a jelenlétüket.<|tool_calls_section_begin|>"
             "<|tool_call_begin|>functions.jelenlet:0<|tool_call_argument_begin|>"
             "{}<|tool_call_end|><|tool_calls_section_end|>")

kiad, puf, vagva = chat._marker_vago(SZIVARGAS, "")
ok(vagva, "a vágó felismeri a marker kezdetét")
ok(kiad == "Ellenőrzöm a jelenlétüket.", f"csak a tiszta rész marad: {kiad!r}")
ok("tool_call" not in kiad, "MARKER NEM megy ki a felhasználóhoz")

# darabokra törve is (a stream így érkezik)
kiad_ossz, puf, vagva = "", "", False
for i in range(0, len(SZIVARGAS), 7):
    if vagva:
        break
    k, puf, vagva = chat._marker_vago(SZIVARGAS[i:i + 7], puf)
    kiad_ossz += k
ok("tool_call" not in kiad_ossz and "DSML" not in kiad_ossz,
   f"DARABOLVA is tiszta: {kiad_ossz!r}")

for minta in ("<｜｜DSML｜｜invoke", "<function_calls>", "tool_calls_section"):
    k, _, v = chat._marker_vago("szöveg " + minta + " maradék", "")
    ok(v and minta not in k, f"felismeri: {minta[:18]!r}")

k, _, v = chat._marker_vago("Ez egy sima mondat < ötnél kisebb.", "")
ok(not v, "ártatlan < jel NEM vágja el a választ")


szakasz("A folyam SZABAD VÁLTOZÓI — a néma üzemzavar ellen")

# 2026-08-07: a `_tool_kor` `instance`-t használt, de nem kapta meg
# paraméterként. NameError → MINDEN chat-üzenet hibára futott, MINDEN
# profilon, élesben. A szintaxis-ellenőrzés ezt NEM fogja meg.
import symtable   # noqa: E402

src = (ROOT / "youngereka_chat.py").read_text(encoding="utf-8")
st = symtable.symtable(src, "youngereka_chat.py", "exec")
MODUL = {sym.get_name() for sym in st.get_symbols()}
# `builtins` modul, NEM `__builtins__`: főmodulként az utóbbi a modul (és
# `dir()` a beépített neveket adja), IMPORTÁLVA viszont egy dict — ott
# `dir()` a dict metódusait adja vissza, tehát az `int`, a `set` és a
# `ValueError` "ismeretlen globálisnak" látszott. A vizsgálat így
# szkriptként zöld volt, pytest alatt piros — ugyanarra a kódra.
import builtins  # noqa: E402
BEEPITETT = set(dir(builtins)) | {"__import__", "self"}


def _fak(t):
    yield t
    for c in t.get_children():
        yield from _fak(c)


gyanus = []
for f in _fak(st):
    if f.get_type() != "function":
        continue
    for sym in f.get_symbols():
        n = sym.get_name()
        if sym.is_global() and n not in MODUL and n not in BEEPITETT:
            gyanus.append(f"{f.get_name()}(): {n}")
ok(not gyanus, f"nincs ismeretlen globális név egyetlen függvényben sem "
               f"({gyanus[:4] if gyanus else 'tiszta'})")

import inspect   # noqa: E402
sig = inspect.signature(chat._tool_kor)
ok("instance" in sig.parameters, "a _tool_kor MEGKAPJA az instance-t")


# A fájl szkriptként született: import közben lefuttat mindent, és a végén
# `sys.exit(1)`-gyel jelez. Pytest alatt ez INTERNALERROR volt — nem egy
# bukott teszt, hanem a TELJES SVIT GYŰJTÉSÉNEK megállítása, tehát egyetlen
# fájl elrejtette az összes többit. Innentől mindkét módon működik:
# `python tests/test_notruf.py` továbbra is kilépési kóddal jelez, a pytest
# pedig egy rendes tesztet lát.

def test_notruf_ellenorzesek():
    """A fenti ellenőrzések import közben lefutottak; itt csak az ítélet."""
    assert hibak == [], "NOTRUF-bukások: " + "; ".join(hibak)


def _osszegzes() -> int:
    print("\n" + "═" * 60)
    if hibak:
        print(f"PIROS — {len(hibak)} bukás:")
        for h in hibak:
            print("   ·", h)
        return 1
    print("MIND ZÖLD — a vészjelző szól, és nem visz magával semmit.")
    return 0


if __name__ == "__main__":
    sys.exit(_osszegzes())
