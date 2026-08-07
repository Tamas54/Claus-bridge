"""
Emlékezet — amit a rendszer megjegyez a lányokról.
===================================================

Kommandant, 2026-08-07: „jegyezze meg a csajoknak a dolgokat a rendszer,
mert nem hülyék, és egyébként se arról híres a család, h stabil lenne."

MIÉRT NEM KÉNYELMI FUNKCIÓ
---------------------------
A promptjukban ez áll: ha szólnak, hogy hagyd el a becenevet, hagyd el
„AZONNAL ÉS VÉGLEGESEN". Emlékezet nélkül ez az ígéret hazugság — a
véglegesből a következő beszélgetés első üzenetéig tart. Egy ígéret,
amit a rendszer szerkezetileg nem tud betartani, rosszabb, mint ha meg
sem ígérte volna.

Ezért a becenév-elutasítás az EGYETLEN dolog, amit NEM modell dönt el,
hanem determinisztikus szabály (`_becenev_elutasitas`). Egy LLM-ítélet
99%-ban jó — ez itt kevés, mert a maradék 1% pont az az eset, amikor
másodszor is meg kell kérnie rá.

MINDEN MÁS AUTOMATIKUS, DE LÁTHATÓ
-----------------------------------
A többi jegyzetet olcsó modellhívás emeli ki, a válasz UTÁN (nem lassítja
a beszélgetést). Amit megjegyzett, azt a felhasználó LÁTJA és bármikor
törölheti. Egy emlékezet, amibe nem lehet belenézni, kellemetlen; egy
emlékezet, amit nem lehet törölni, csapda.

Instance-onként külön. Réka jegyzetei és Annáé soha nem keverednek.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("bridge.yr_memory")

#: Ennél több jegyzetet nem tartunk. A legrégebbi, legritkábban
#: megerősített esik ki — különben a rendszerprompt hízna a végtelenségig.
MAX_JEGYZET = 40

#: Egy jegyzet ennél hosszabb nem lehet. Ami ennél több, az nem jegyzet,
#: hanem beszélgetés-részlet.
MAX_HOSSZ = 300


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS yr_notes (
          id          TEXT PRIMARY KEY,
          instance    TEXT NOT NULL,
          kind        TEXT NOT NULL,
          content     TEXT NOT NULL,
          source      TEXT,
          created_at  TIMESTAMP NOT NULL,
          updated_at  TIMESTAMP NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_yr_notes_inst "
                 "ON yr_notes(instance, updated_at)")
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# ÍRÁS
# ============================================================

def remember(conn, instance: str, kind: str, content: str,
             source: str = "auto") -> str | None:
    """Jegyzet felvétele. Duplikátumot nem hoz létre, csak frissít.

    Visszaad: a jegyzet id-je, vagy None, ha nem vettük fel.
    """
    content = (content or "").strip()
    if not content or len(content) > MAX_HOSSZ:
        return None
    try:
        ensure_schema(conn)
        # Egyszerű duplikátum-védelem: azonos kind + nagyon hasonló szöveg.
        # Nem kell fuzzy — a kiemelő modell úgyis a meglévőket látja, és
        # arra kérjük, hogy ne ismételjen.
        norm = _norm(content)
        for sor in conn.execute(
                "SELECT id, content FROM yr_notes WHERE instance=? AND kind=?",
                (instance, kind)):
            if _norm(sor["content"]) == norm:
                conn.execute("UPDATE yr_notes SET updated_at=? WHERE id=?",
                             (_now(), sor["id"]))
                conn.commit()
                return sor["id"]

        jid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO yr_notes (id, instance, kind, content, source, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (jid, instance, kind, content, source, _now(), _now()))
        # Kvóta: a legrégebben frissített esik ki.
        conn.execute("""
            DELETE FROM yr_notes WHERE instance=? AND id NOT IN (
                SELECT id FROM yr_notes WHERE instance=?
                ORDER BY updated_at DESC LIMIT ?)""",
            (instance, instance, MAX_JEGYZET))
        conn.commit()
        return jid
    except Exception as e:  # noqa: BLE001
        logger.warning("jegyzet felvétele bukott: %s", e)
        return None


def forget(conn, instance: str, note_id: str = "", mind: bool = False) -> int:
    """Törlés. Csak a SAJÁT jegyzeteit — az instance mindig szűr."""
    try:
        ensure_schema(conn)
        if mind:
            cur = conn.execute("DELETE FROM yr_notes WHERE instance=?", (instance,))
        else:
            cur = conn.execute("DELETE FROM yr_notes WHERE instance=? AND id=?",
                               (instance, note_id))
        conn.commit()
        return cur.rowcount
    except Exception as e:  # noqa: BLE001
        logger.warning("jegyzet törlése bukott: %s", e)
        return 0


def list_notes(conn, instance: str) -> list[dict]:
    try:
        ensure_schema(conn)
        return [dict(r) for r in conn.execute(
            "SELECT id, kind, content, source, created_at, updated_at "
            "FROM yr_notes WHERE instance=? ORDER BY kind, updated_at DESC",
            (instance,))]
    except Exception as e:  # noqa: BLE001
        logger.warning("jegyzetek olvasása bukott: %s", e)
        return []


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


# ============================================================
# A BECENÉV-ELUTASÍTÁS — determinisztikus, nem modell dönti
# ============================================================

#: „ne hívj így", „hagyd el a kis hercegnőt", „ne becézz" — a felszíni
#: alakok. Szándékosan BŐKEZŰ: a téves pozitív ára az, hogy nem becézi
#: (semmi baj); a téves negatívé az, hogy másodszor is kérnie kell.
#
# A ragozott alakoknál SZÓHATÁR kell. Enélkül a „Ne HÍVJUK ezt
# szignifikánsnak" beindítaná a szabályt, mert a `hívj` a `hívjuk`-ban is
# benne van — és a felhasználó némán elveszítené a becenevét egy szakmai
# mondat miatt. (A teszt fogta meg, 2026-08-07.)
_ELUTASITAS = re.compile(
    r"(ne\s+(h[ií]vj(ál|al)?|sz[óo]l[ií]ts(ál|al)?|nevezz(él|el)?"
    r"|bec[ée]zz?(él|el)?)\b"
    r"|hagyd\s+el\s+(a\s+)?(kis\s+)?(hercegn|csodakirályn|csodakiralyn|becen)"
    r"|(kis\s+hercegn\w*|csodakirályn\w*|csodakiralyn\w*)[^.!?]{0,40}"
    r"(ne|nem\s+kell|hagyd|elég|eleg|idegesít|idegesit|ciki|kínos|kinos)"
    r"|(ne|nem\s+kell|hagyd|elég|eleg)[^.!?]{0,40}"
    r"(kis\s+hercegn\w*|csodakirályn\w*|csodakiralyn\w*|becenev|becenév)"
    r"|nem\s+vagyok\s+(kis\s+)?(hercegn|csodakirályn|csodakiralyn))",
    re.IGNORECASE)

BECENEV_TILTAS = "NE használd a becenevet — kérte, hogy hagyd el."


def _becenev_elutasitas(szoveg: str) -> bool:
    return bool(_ELUTASITAS.search(szoveg or ""))


def check_becenev(conn, instance: str, uzenet: str) -> bool:
    """Ha az üzenet a becenév elhagyását kéri, RÖGZÍTI. Igazzal tér vissza,
    ha most rögzítette."""
    if not _becenev_elutasitas(uzenet):
        return False
    if any(n["content"] == BECENEV_TILTAS for n in list_notes(conn, instance)):
        return False
    remember(conn, instance, "preferencia", BECENEV_TILTAS, source="szabály")
    logger.info("%s: becenév-tiltás rögzítve", instance)
    return True


def becenev_tiltva(conn, instance: str) -> bool:
    return any(n["content"] == BECENEV_TILTAS for n in list_notes(conn, instance))


# ============================================================
# BEHÍVÁS A PROMPTBA
# ============================================================

_FEJ = {
    "preferencia": "Amit kért",
    "munka": "Amin dolgozik",
    "tény": "Amit tudok róla",
}


def recall_block(conn, instance: str) -> str:
    """A rendszerprompt végére fűzött emlékezet-blokk. Üres, ha nincs mit."""
    jegyzetek = list_notes(conn, instance)
    if not jegyzetek:
        return ""
    csoport: dict[str, list[str]] = {}
    for j in jegyzetek:
        csoport.setdefault(j["kind"], []).append(j["content"])

    sorok = ["\n\nAMIT KORÁBBRÓL TUDOK",
             "Ez korábbi beszélgetésekből maradt meg. Használd, ha releváns, "
             "de NE sorold fel neki, és ne kezdd ezzel a választ. Ha valami "
             "ellentmond annak, amit MOST mond, a mostani az igaz."]
    for kind in ("preferencia", "munka", "tény"):
        if kind in csoport:
            sorok.append(f"\n{_FEJ.get(kind, kind)}:")
            sorok += [f"- {c}" for c in csoport[kind]]
    for kind, ertekek in csoport.items():
        if kind not in ("preferencia", "munka", "tény"):
            sorok.append(f"\n{kind}:")
            sorok += [f"- {c}" for c in ertekek]
    return "\n".join(sorok)


# ============================================================
# KIEMELÉS — a válasz UTÁN, olcsó modellel
# ============================================================

_KIEMELO_PROMPT = """Egy kutatói asszisztens beszélgetését olvasod.
A feladatod: kiemelni azt a KEVÉS dolgot, amit érdemes hosszú távon
megjegyezni a felhasználóról, hogy a következő beszélgetésben ne kelljen
újra elmondania.

CSAK ezeket jegyezd meg:
- preferencia: hogyan szeretné, hogy beszélj vele, mit ne csinálj
- munka: min dolgozik éppen (téma, kísérlet, kurzus, határidő)
- tény: tartós körülmény (labor, tanszék, eszköz, nyelv, szak)

NE jegyezd meg:
- ami csak erre az egy kérdésre vonatkozik
- általános szakmai tudást (azt úgyis tudod)
- érzelmi állapotot, egészségügyi vagy magánéleti részletet
- bármit, amit ő maga csak kérdésként vetett fel

Amit MÁR TUDOK (ezeket ne ismételd meg):
{meglevo}

Válasz KIZÁRÓLAG JSON-tömb, legfeljebb 3 elem, magyarul, egyes szám
harmadik személyben, tömören:
[{{"kind":"preferencia|munka|tény","content":"..."}}]
Ha nincs semmi megjegyzendő: []"""


async def extract(conn, instance: str, user_msg: str, assistant_msg: str,
                  hivo) -> list[dict]:
    """Jegyzetek kiemelése a beszélgetésből. Sose dob.

    `hivo(system, user) -> str` — a modellhívás, kívülről adva, hogy ez a
    modul ne ismerje a SiliconFlow-t.
    """
    if not (user_msg or "").strip():
        return []
    try:
        meglevo = "\n".join(f"- [{n['kind']}] {n['content']}"
                            for n in list_notes(conn, instance)) or "(még semmi)"
        beszelgetes = (f"FELHASZNÁLÓ:\n{user_msg[:3000]}\n\n"
                       f"ASSZISZTENS:\n{(assistant_msg or '')[:1500]}")
        nyers = await hivo(_KIEMELO_PROMPT.format(meglevo=meglevo), beszelgetes)
        uj = _parse(nyers)
        felvett = []
        for j in uj[:3]:
            kind = j.get("kind", "tény")
            if kind not in ("preferencia", "munka", "tény"):
                kind = "tény"
            if remember(conn, instance, kind, j.get("content", "")):
                felvett.append({"kind": kind, "content": j.get("content", "")})
        if felvett:
            logger.info("%s: %d jegyzet felvéve", instance, len(felvett))
        return felvett
    except Exception as e:  # noqa: BLE001
        logger.warning("jegyzet-kiemelés bukott (a beszélgetés megy tovább): %s", e)
        return []


def _parse(nyers: str) -> list[dict]:
    """A modell néha kódblokkba teszi, néha elé beszél. Kiszedjük a tömböt."""
    s = (nyers or "").strip()
    if not s:
        return []
    m = re.search(r"\[.*\]", s, re.DOTALL)
    if not m:
        return []
    try:
        d = json.loads(m.group(0))
        return [x for x in d if isinstance(x, dict)] if isinstance(d, list) else []
    except json.JSONDecodeError:
        return []
