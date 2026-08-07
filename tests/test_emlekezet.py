#!/usr/bin/env python3
"""Emlékezet — amit a rendszer megjegyez a lányokról.

    python tests/test_emlekezet.py

A tét: a promptjuk „azonnal és VÉGLEGESEN" ígér a becenév elhagyására.
Emlékezet nélkül ez az ígéret a következő beszélgetésig tart. Itt az dől
el, hogy betartható-e.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("YR_TOKEN", "REKA_TOKEN_1234567890abcdefgh")
os.environ.setdefault("AN_TOKEN", "ANNA_TOKEN_zyxwvu0987654321ab")

import youngereka_memory as memory   # noqa: E402

hibak: list[str] = []


def ok(f, c):
    print(("  ✓ " if f else "  ✗ ") + c)
    if not f:
        hibak.append(c)


def szakasz(c):
    print(f"\n── {c} " + "─" * max(0, 54 - len(c)))


tmp = pathlib.Path(tempfile.mkdtemp())
conn = sqlite3.connect(tmp / "m.db")
conn.row_factory = sqlite3.Row
memory.ensure_schema(conn)


# ============================================================
szakasz("A becenév-elutasítás — determinisztikus")

# Ezeknek MIND fogniuk kell. A téves pozitív ára: nem becézi (semmi baj).
# A téves negatívé: másodszor is kérnie kell.
FOGJA = [
    "ne hívj kis hercegnőnek",
    "Ne hívj így kérlek.",
    "hagyd el a kis hercegnőt",
    "Hagyd el a becenevet.",
    "a csodakirálynő elég volt, ne használd",
    "ne becézz",
    "Ne nevezz kis hercegnőnek légyszi",
    "nem vagyok kis hercegnő",
    "ne szólíts csodakirálynőnek",
    "a kis hercegnő kicsit ciki, hagyd",
]
for m in FOGJA:
    ok(memory._becenev_elutasitas(m), f"fogja: {m!r}")

# Ezekre NEM szabad fognia — különben egy ártatlan mondat kikapcsolja.
NEM = [
    "Mi a különbség a kontroll és a kezelt csoport között?",
    "Ne felejtsd el a mintaszámot megnézni.",
    "A hercegnő-effektus nevű jelenségről olvastam.",
    "Ne hívjuk ezt szignifikánsnak, mert nem az.",
]
for m in NEM:
    ok(not memory._becenev_elutasitas(m), f"NEM fog rá: {m!r}")


# ============================================================
szakasz("A tiltás túléli a beszélgetést")

ok(not memory.becenev_tiltva(conn, "YoungeReka"), "induláskor nincs tiltás")
ok(memory.check_becenev(conn, "YoungeReka", "ne hívj kis hercegnőnek"),
   "a kérés rögzítve")
ok(memory.becenev_tiltva(conn, "YoungeReka"),
   "…és ÚJ beszélgetésben is érvényes (ez a „véglegesen”)")
ok(not memory.check_becenev(conn, "YoungeReka", "ne hívj kis hercegnőnek"),
   "másodszor nem duplikál")

# A másik lányra NEM hat át
ok(not memory.becenev_tiltva(conn, "AnnaKatheder"),
   "Réka kérése NEM némítja el Anna becenevét")

ok(memory.BECENEV_TILTAS in memory.recall_block(conn, "YoungeReka"),
   "a tiltás bekerül a prompt-blokkba")


# ============================================================
szakasz("Jegyzetek — felvétel, dedup, kvóta")

memory.remember(conn, "YoungeReka", "munka", "Asztrocita-aktiváció LPS-modellen dolgozik.")
memory.remember(conn, "YoungeReka", "tény", "GFAP-immunfestést használ.")
n = len(memory.list_notes(conn, "YoungeReka"))
ok(n == 3, f"3 jegyzet (a tiltással együtt), van: {n}")

memory.remember(conn, "YoungeReka", "tény", "GFAP-immunfestést használ.")
ok(len(memory.list_notes(conn, "YoungeReka")) == 3, "duplikátum nem jön létre")

ok(memory.remember(conn, "YoungeReka", "tény", "x" * 400) is None,
   "túl hosszú jegyzet elutasítva")
ok(memory.remember(conn, "YoungeReka", "tény", "   ") is None,
   "üres jegyzet elutasítva")

for i in range(60):
    memory.remember(conn, "YoungeReka", "tény", f"tesztjegyzet {i}")
ok(len(memory.list_notes(conn, "YoungeReka")) <= memory.MAX_JEGYZET,
   f"kvóta tartva (max {memory.MAX_JEGYZET})")


# ============================================================
szakasz("Elkülönülés és törlés")

memory.remember(conn, "AnnaKatheder", "munka", "Reformáció-beadandón dolgozik.")
r = {x["content"] for x in memory.list_notes(conn, "YoungeReka")}
a = {x["content"] for x in memory.list_notes(conn, "AnnaKatheder")}
ok("Reformáció-beadandón dolgozik." not in r, "Réka NEM látja Anna jegyzetét")
ok(len(a) == 1, "Anna csak a sajátját látja")

anna_id = memory.list_notes(conn, "AnnaKatheder")[0]["id"]
ok(memory.forget(conn, "YoungeReka", note_id=anna_id) == 0,
   "Réka Anna jegyzet-ID-jével sem töröl (instance szűr)")
ok(len(memory.list_notes(conn, "AnnaKatheder")) == 1, "…Anna jegyzete megvan")
ok(memory.forget(conn, "AnnaKatheder", note_id=anna_id) == 1, "a sajátját törli")

memory.forget(conn, "YoungeReka", mind=True)
ok(memory.list_notes(conn, "YoungeReka") == [], "„mind törlése” kiürít")
ok(not memory.becenev_tiltva(conn, "YoungeReka"),
   "…a becenév-tiltást is (ha mindent töröl, azt is elfelejti)")


# ============================================================
szakasz("Behívás a promptba")

ok(memory.recall_block(conn, "YoungeReka") == "", "üres emlékezet → üres blokk")
memory.remember(conn, "YoungeReka", "munka", "Doktori dolgozatát írja.")
b = memory.recall_block(conn, "YoungeReka")
ok("Doktori dolgozatát írja." in b, "a jegyzet bekerül")
ok("NE sorold fel neki" in b, "…azzal az utasítással, hogy ne olvassa a fejére")
ok("a mostani az igaz" in b, "…és hogy a friss információ felülírja a régit")


# ============================================================
szakasz("Kiemelés — a modell válaszának feldolgozása")

ok(memory._parse('[{"kind":"munka","content":"X"}]')[0]["content"] == "X",
   "tiszta JSON")
ok(memory._parse('```json\n[{"kind":"tény","content":"Y"}]\n```')[0]["content"] == "Y",
   "kódblokkba csomagolt JSON")
ok(memory._parse("Íme:\n[{\"kind\":\"munka\",\"content\":\"Z\"}]")[0]["content"] == "Z",
   "elé beszél a modell")
ok(memory._parse("[]") == [], "üres tömb")
ok(memory._parse("nem tudom") == [], "értelmezhetetlen válasz → üres, nem hiba")
ok(memory._parse("") == [], "üres válasz → üres")


async def _proba():
    async def hivo(system, user):
        assert "Amit MÁR TUDOK" in system
        return '[{"kind":"munka","content":"Nyugati blotot optimalizál."}]'
    uj = await memory.extract(conn, "YoungeReka", "Kérdésem a blotról.",
                              "Válasz.", hivo)
    return uj

uj = asyncio.run(_proba())
ok(len(uj) == 1 and uj[0]["content"] == "Nyugati blotot optimalizál.",
   "kiemelés felveszi a jegyzetet")


async def _bukas():
    async def hivo(system, user):
        raise RuntimeError("a modell elszállt")
    return await memory.extract(conn, "YoungeReka", "x", "y", hivo)

ok(asyncio.run(_bukas()) == [],
   "ha a kiemelő modell bukik, a beszélgetés NEM sérül (üres lista, nincs dobás)")

conn.close()

print("\n" + "═" * 60)
if hibak:
    print(f"PIROS — {len(hibak)} bukás:")
    for h in hibak:
        print("   ·", h)
    sys.exit(1)
print("MIND ZÖLD — az emlékezet tartja a „véglegesen” ígéretet.")
