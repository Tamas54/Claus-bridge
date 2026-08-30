"""A KET BRIEF ES A NEMA FELULIRAS — 2026-08-30.

A KIVALTO ESET (Telegram-jegyzokonyv, 2026-04-10)
-------------------------------------------------
    13:53 Kommandant: "Utemezz egy reggeli (7 oras) es egy delutani hireket.
                       A delutani hirek 4 orakor jojjenek."
    13:54 Feldwebel:  "Kesz! Utemezve: 1. Reggeli daily_news_brief: minden
                       reggel 7:00-kor 2. Delutani: minden nap 16:00-kor"
    13:55 Feldwebel:  "A ket utemezes aktiv."

Tarolva: EGY sor, `14 16 * * *`.

Harom hiba egy percben:
  1. a `cron_schedule` EGYETLEN oszlop a recept soran, tehat a masodik
     utemezes NEM hozzaadott, hanem eltorolte az elsot — a reggeli brief soha
     nem letezett;
  2. a bot MINDKETTOT aktivnak jelentette (nema siker: a 200-as valasz nem
     bizonyitek);
  3. es a kert 16:00-bol 16:14 lett — a `0 16` helyett `14 16` keruilt be.

Negy es fel honapig senki nem vette eszre, mert a rendszer sehol nem mondta ki,
hogy CSERELT. Ezek a tesztek azt orzik, hogy kimondja.
"""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from plugins import recipes as recipes_plugin
from plugins import news_brief


def _run(coro):
    return json.loads(asyncio.run(coro))


@pytest.fixture
def tools(fake_app, get_db):
    """A produkcios deps-alak. A jogosultsagi kapu ITT nem a merendo dolog —
    nevesitett hivot es tokenes utat adunk, hogy a menetrend-logikat lassuk."""
    async def _fake_ai_task(**kwargs):
        return json.dumps({"status": "created"})
    recipes_plugin.register_tools(fake_app, {
        "get_db": get_db,
        "ai_task_func": _fake_ai_task,
        "enforce_func": lambda caller, verb: None,      # engedelyezve
        "authenticated_func": lambda ctx: True,          # tokenes uton jottunk
    })
    return fake_app.tools


def _seed(get_db, name, cron=None, cron_enabled=1):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO pyramid_recipes (name, description, required_tools, "
        "prompt_template, created_by, created_at, updated_at, enabled, "
        "cron_schedule, cron_enabled) VALUES (?, 'd', '[]', 'p', 'test', '', '', 1, ?, ?)",
        (name, cron, cron_enabled))
    conn.commit()
    conn.close()


def _schedule_of(get_db, name):
    conn = get_db()
    try:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT cron_schedule FROM pyramid_recipes WHERE name=?",
                         (name,)).fetchone()
        return r["cron_schedule"] if r else None
    finally:
        conn.close()


# ═══ 1. A CSERE KIMONDASA ══════════════════════════════════════════════════

def test_a_2026_04_10_eset_ma_mar_kimondja_a_cseret(tools, get_db):
    """A PONTOS forgatokonyv, ami a reggeli briefet elvitte.

    Ket utemezes ugyanarra a receptre. A masodik tovabbra is FELULIR — ez
    legitim muvelet —, de a valasz mostantol kimondja, hogy mit torolt.
    """
    _seed(get_db, "daily_news_brief", cron=None, cron_enabled=0)
    elso = _run(tools["update_recipe"](name="daily_news_brief",
                                       cron_schedule="0 7 * * *",
                                       cron_enabled=True, caller="test"))
    assert elso["cron_schedule"] == "0 7 * * *"
    assert "replaced_schedule" not in elso, \
        "az ELSO utemezes nem csere — nem szabad csereként jelenteni"

    masodik = _run(tools["update_recipe"](name="daily_news_brief",
                                          cron_schedule="0 16 * * *",
                                          cron_enabled=True, caller="test"))
    # A DB tovabbra is egy menetrendet tart — ez a valosag, nem hiba.
    assert _schedule_of(get_db, "daily_news_brief") == "0 16 * * *"
    # De a valasz NEM hallgathatja el, hogy a reggeli megszunt.
    assert masodik["replaced_schedule"] == {"from": "0 7 * * *", "to": "0 16 * * *"}
    assert "0 7 * * *" in masodik["message"], \
        "a valasz nem mondja meg, MELYIK menetrend szunt meg"
    assert "MASODIK RECEPT" in masodik["message"], \
        "a valasz nem mondja meg a helyes megoldast (ket recept)"


def test_ugyanaz_a_menetrend_ujra_nem_csere(tools, get_db):
    """Szabotazs: ha minden ismetelt irast cserenek jelentenenk, a jelzes
    elertektelenedne (a migracio minden bootkor ugyanazt irja be)."""
    _seed(get_db, "r", cron="0 7 * * *")
    out = _run(tools["update_recipe"](name="r", cron_schedule="0 7 * * *",
                                      cron_enabled=True, caller="test"))
    assert "replaced_schedule" not in out


def test_a_torles_is_kimondott(tools, get_db):
    _seed(get_db, "r", cron="0 7 * * *")
    out = _run(tools["update_recipe"](name="r", cron_schedule="none",
                                      cron_enabled=False, caller="test"))
    assert out["replaced_schedule"] == {"from": "0 7 * * *", "to": None}
    assert "TOROLVE" in out["message"]


def test_menetrend_nelkuli_recept_torlese_nem_hazudik_cseret(tools, get_db):
    """Szabotazs: ha nem volt menetrend, nincs mit torolni — ne jelentsunk
    olyat, ami nem tortent."""
    _seed(get_db, "r", cron=None, cron_enabled=0)
    out = _run(tools["update_recipe"](name="r", cron_schedule="none",
                                      cron_enabled=False, caller="test"))
    assert "replaced_schedule" not in out


# ═══ 2. KET BRIEF, EGY FORRAS ══════════════════════════════════════════════

def test_ket_hirszemle_letezik_kulon_recepten():
    """Egy recept egy idopontban fut. Ket brief = ket sor — nincs mas mod."""
    assert set(news_brief.BRIEFS) == {"daily_news_brief", "daily_news_brief_pm"}
    cronok = {n: v[2] for n, v in news_brief.BRIEFS.items()}
    assert cronok["daily_news_brief"] == "0 7 * * *"
    assert cronok["daily_news_brief_pm"] == "0 16 * * *"
    assert len(set(cronok.values())) == 2, "a ket brief ugyanakkor futna"


def test_a_ket_prompt_egy_forrasbol_jon_es_csak_a_keretezes_ter_el():
    """A DUPLIKATUMOK SZETCSUSZNAK. A ket prompt ugyanabbol a fuggvenybol jon;
    a kulonbseg a session-keretezes, nem ket kulon kezzel irt szoveg."""
    reggel = news_brief.prompt_for("daily_news_brief")
    delutan = news_brief.prompt_for("daily_news_brief_pm")
    assert reggel != delutan
    # A KEMENY SZABALYOK blokk szo szerint azonos — ott nincs helye elteresnek.
    kemeny = "== KEMENY SZABALYOK =="
    assert reggel.split(kemeny)[1] == delutan.split(kemeny)[1], \
        "a ket brief kemeny szabalyai szetcsusztak"
    assert "REGGELI" in reggel.split(kemeny)[0]
    assert "DELUTANI" in delutan.split(kemeny)[0]


def test_mindket_briefnek_van_prefetchere_es_kotelezo_szekcioja():
    """A delutani brief adat-utja ugyanaz, mint a reggelie. Ha csak az egyik
    kapna prefetchert, a masik CSENDBEN ures FACTUAL CONTEXT-tel menne ki."""
    from plugins import _recipe_prefetch as pf
    for name in news_brief.BRIEFS:
        assert name in pf.RECIPE_PREFETCHERS, f"{name}: nincs prefetcher"
        assert name in pf.REQUIRED_SECTIONS, f"{name}: nincs kotelezo szekcio"
    assert pf.REQUIRED_SECTIONS["daily_news_brief"] is \
        pf.REQUIRED_SECTIONS["daily_news_brief_pm"], \
        "ket kulon szekcio-lista ket kulon igazsagga valik"


def test_a_kezzel_szerkesztett_promptot_nem_irjuk_felul():
    """A migracio minden bootkor fut. Ha a Kommandant vagy a Feldwebel atirta a
    promptot, a sajat kanonom NEM torolheti csendben."""
    assert news_brief._is_ours(None)
    assert news_brief._is_ours(news_brief.prompt_for("daily_news_brief"))
    assert not news_brief._is_ours("Sajat kezzel irt prompt a Kommandanttol.")


def test_a_prompt_a_gitben_van_nem_csak_a_dbben():
    """A prompt 4,5 honapig KIZAROLAG a produkcios SQLite-ban elt: egy
    DB-visszaallitas szo nelkul elvitte volna."""
    p = news_brief.prompt_for("daily_news_brief")
    assert f"news_brief_prompt v{news_brief.PROMPT_VERSION}" in p
    # A tartalmi gerinc tenyleg benne van, nem csak egy pointer:
    for kell in ("top_stories", "world_stories", "fresh_today", "forras",
                 "KULPOLITIKA", "BELPOLITIKA"):
        assert kell in p, f"a kanonikus promptbol hianyzik: {kell}"


def test_a_cim_onmagaban_nem_azonosit_hirt_benne_van_a_promptban():
    """A `fresh_today` lead nelkul jon; a prompt ezt KIMONDJA, kulonben a
    modell ugyanazt a hibat koveti el, amit en (paksi vs dunai fenekkuszob)."""
    for name in news_brief.BRIEFS:
        p = news_brief.prompt_for(name)
        assert "title_only" in p and "fenekkuszob" in p


# ═══ 3. A TELEGRAM-AG UGYANAZT MONDJA ══════════════════════════════════════
#
# A javitas felkesz lenne a Feldwebel nelkul: a Kommandant TELEGRAMON utemez,
# es 2026-04-10-en is az az ag futott. Az `update_recipe` (MCP) es a
# `schedule_recipe` (Telegram) KET kulon implementacio volt; a masodik
# egyetlen `len(parts) != 5` ellenorzest vegzett es sosem jelentett cseret.

def test_a_feldwebel_utemezese_is_kimondja_a_cseret(get_db):
    """UGYANAZ A FORGATOKONYV, A MASIK FELULETEN."""
    from feldwebel.responder import _execute_tool

    _Ctx = SimpleNamespace(get_db=get_db)

    _seed(get_db, "daily_news_brief", cron=None, cron_enabled=0)
    elso = json.loads(asyncio.run(_execute_tool(
        "schedule_recipe",
        {"name": "daily_news_brief", "cron_schedule": "0 7 * * *"}, _Ctx)))
    assert elso["status"] == "scheduled"
    assert "replaced_schedule" not in elso

    masodik = json.loads(asyncio.run(_execute_tool(
        "schedule_recipe",
        {"name": "daily_news_brief", "cron_schedule": "0 16 * * *"}, _Ctx)))
    assert masodik["replaced_schedule"] == {"from": "0 7 * * *", "to": "0 16 * * *"}, \
        "a Telegram-ag megint elhallgatja, hogy felulirt egy menetrendet"
    assert "MASODIK RECEPT" in masodik["message"]


def test_a_feldwebel_utemezese_is_validal(get_db):
    """A regi ag CSAK az ot mezot szamolta meg. Egy `70 99 * * *` atment volna."""
    from feldwebel.responder import _execute_tool

    _Ctx = SimpleNamespace(get_db=get_db)

    _seed(get_db, "r", cron="0 7 * * *")
    out = json.loads(asyncio.run(_execute_tool(
        "schedule_recipe", {"name": "r", "cron_schedule": "70 99 * * *"}, _Ctx)))
    assert out.get("reason_code") == "invalid_cron_schedule", \
        "ervenytelen cron-mezok atmentek a Telegram-agon"
    assert _schedule_of(get_db, "r") == "0 7 * * *", \
        "a hibas menetrend felulirta a mukodot"


def test_a_ket_felulet_UGYANAZT_a_motort_hasznalja():
    """Szabotazs a duplikatum ellen: ha valaki visszair egy sajat
    UPDATE-et a Telegram-agba, ez a teszt megfogja."""
    import inspect
    from feldwebel import responder
    src = inspect.getsource(responder._execute_tool)
    blokk = src.split('if name == "schedule_recipe":', 1)[1].split("if name ==", 1)[0]
    assert "apply_schedule" in blokk, \
        "a Telegram-ag nem a kozos menetrend-motort hasznalja"
    assert "UPDATE pyramid_recipes" not in blokk, \
        "a Telegram-ag megint kozvetlenul ir a DB-be, a validacio megkerulesevel"


def test_a_2026_04_10_elgepeles_javul_de_mas_menetrendhez_nem_nyulunk(get_db):
    """A `14 16 * * *` egyszeri, celzott javitasa — es CSAK azé.

    Szabotazs: ha a seed minden menetrendet a kanonra allitana, akkor a
    Kommandant sajat valasztasat minden ujrainditas csendben visszairna.
    """
    conn = get_db()
    conn.execute(
        "INSERT INTO pyramid_recipes (name, description, required_tools, "
        "prompt_template, created_by, created_at, updated_at, enabled, "
        "cron_schedule, cron_enabled) VALUES "
        "('daily_news_brief','d','[]','p','t','','',1,'14 16 * * *',1)")
    # Egy sajat valasztas ugyanazon a recept-tipuson:
    conn.execute(
        "INSERT INTO pyramid_recipes (name, description, required_tools, "
        "prompt_template, created_by, created_at, updated_at, enabled, "
        "cron_schedule, cron_enabled) VALUES "
        "('daily_news_brief_pm','d','[]','p','t','','',1,'45 18 * * *',1)")
    conn.commit()

    news_brief.seed_briefs(conn, "2026-08-30T00:00:00Z")
    conn.commit()

    def _cron(n):
        return conn.execute("SELECT cron_schedule FROM pyramid_recipes WHERE name=?",
                            (n,)).fetchone()[0]
    assert _cron("daily_news_brief") == "0 7 * * *", "az elgepeles nem javult"
    assert _cron("daily_news_brief_pm") == "45 18 * * *", \
        "a seed felulirta a Kommandant sajat menetrendjet"
    conn.close()
