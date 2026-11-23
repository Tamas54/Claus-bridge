"""STRATÉGIAI MÓD (2026-11-24, strategiai.py) — hálózat nélkül.

  1. SZÁNDÉK → MÓD → MODELL: a szándékfelismerő (LLM nélkül), a folytatás, a kapu szintje (Opus), és a Feldwebel köre:
     stratégiai üzenetnél a prompt bővül és az eszközök előtöltve jönnek; egyszerű kérdésnél MINDEN a régi.
  2. A REGGELI NYITÓ szerkezete: 3 helyzet, helyzetkép ≤2 mondat, álláspont 1 mondat, kérdés; napló előbb, Telegram után;
     naponta egyszer; adat nélkül nincs értékelés.
  3. A NAPLÓ: az állítás kinyerése, a hash-lánc, a duplikátum, a havi pontozás és a Kommandant helyesbítése.
  4. A KERET: a kapu könyvelése a napi számlálóra, a kimerült keret rendes módot ad (kimondva), a kabin adata.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import llm_kapu
import strategiai as st
from feldwebel import eszkoz_ut

MA = date(2026, 11, 24)


def _run(c):
    return asyncio.run(c)


# ══ 1. SZÁNDÉK → MÓD → MODELL ═══════════════════════════════════════════════

@pytest.mark.parametrize("szoveg, elv", [
    ("gondolkodj el ezen: mi lesz a forinttal decemberben", "mindig"),
    ("értékeld a mai EKB-döntést", "mindig"),
    ("mi a véleményed a Matolcsy-ügyről?", "mindig"),
    ("Ertekeld a helyzetet", "mindig"),
    ("mit tegyünk, ha Brüsszel visszatartja a pénzt?", "minta"),
    ("mi következik abból, hogy a demokraták nyertek?", "minta"),
    ("készíts helyzetértékelést az orosz flottáról", "minta"),
    ("kinek az érdeke ez a szerb választás?", "minta"),
    ("nem értek egyet, a Fed nem fog vágni", "minta"),
    ("döntenem kell: eladjam a forintot?", "minta"),
    ("elemezd a magyar költségvetés helyzetét", "minta"),
])
def test_a_strategiai_uzenet_felismerve(szoveg, elv):
    ok = eszkoz_ut.strategiai_szandek(szoveg)
    assert ok and ok.startswith(elv), (szoveg, ok)


@pytest.mark.parametrize("szoveg", [
    "mi a helyzet a forinttal?",                                   # tényleges állapotkérdés: olcsó út
    "írj levelet Lacinak, hogy a csütörtöki megbeszélés átkerül",
    "Kimi, mit gondolsz a forint mai gyengüléséről?",              # név szerint a Kiminek szól
    "elemezd a #2-es képet",                                       # dokumentum, nem helyzet
    "emlékszel, mit döntöttünk a múlt héten?",                     # memória, nem döntés
    "mennyi az alapkamat?",
    "futtasd le a napi briefet",
    "",
])
def test_az_egyszeru_kerdes_nem_strategiai(szoveg):
    assert eszkoz_ut.strategiai_szandek(szoveg) is None, szoveg


def test_folytatas_a_strategiai_fordulo_utan():
    assert eszkoz_ut.strategiai_szandek("szerintem az MNB decemberben nem lép", elozo_strategiai=True) == "folytatás"
    assert eszkoz_ut.strategiai_szandek("nem", elozo_strategiai=True) == "folytatás"     # válasz a kérdésre
    assert eszkoz_ut.strategiai_szandek("köszi", elozo_strategiai=True) is None        # nyugtázás
    assert eszkoz_ut.strategiai_szandek("írj levelet Lacinak erről", elozo_strategiai=True) is None   # művelet
    assert eszkoz_ut.strategiai_szandek("szerintem az MNB decemberben nem lép") is None  # előzmény nélkül nem az


def _hist(agent_id: str, mikor: datetime) -> list:
    return [{"role": "assistant", "content": "x", "agent_id": agent_id, "timestamp": mikor.isoformat()},
            {"role": "user", "content": "y", "agent_id": "deepseek", "timestamp": mikor.isoformat()}]


def test_elozo_strategiai_jel_es_idoablak():
    most = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)
    assert st.elozo_strategiai(_hist("reggeli" + st.STRATEGIAI_JEL, most - timedelta(hours=2)), most)
    assert st.elozo_strategiai(_hist("deepseek:strategiai:claude-csak", most - timedelta(hours=2)), most)
    assert not st.elozo_strategiai(_hist("reggeli" + st.STRATEGIAI_JEL, most - timedelta(hours=13)), most)
    assert not st.elozo_strategiai(_hist("deepseek", most - timedelta(minutes=5)), most)
    assert not st.elozo_strategiai([], most)


def test_a_kapu_a_strategiai_celt_opusra_viszi():
    p = {"messages": [{"role": "user", "content": "mi a helyzet a forinttal?"}]}
    assert llm_kapu.szint_valaszt(p, st.CEL_FELDWEBEL) == ("okos", "strategiai")
    assert llm_kapu.SZINTEK["okos"]["modell"] == "claude-opus-5-5"
    for cel in (st.CEL_NYITO_TERV, st.CEL_NYITO_IRAS, st.CEL_PONTOZAS):
        assert llm_kapu.szint_valaszt(p, cel)[0] == "okos"
    # az egyszerű kérdés marad a Haikun, a „melyebb” a Sonneten
    assert llm_kapu.szint_valaszt(p, "feldwebel/telegram")[0] == "olcso"
    assert llm_kapu.szint_valaszt(p, "feldwebel/melyebb/telegram")[0] == "kozep"
    # a név szerint szólított ügynök útja érintetlen
    assert llm_kapu.nevesitett_ugynok(st.CEL_FELDWEBEL) is None


def test_a_mod_kapcsolo_es_keret(monkeypatch):
    h = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt")
    monkeypatch.setattr(llm_kapu, "_STRATEGIAI", {"nap": llm_kapu._bp_nap(), "usd": 0.0, "hivasok": 0})
    assert st.mod("értékeld a helyzetet", h).aktiv
    monkeypatch.setenv("STRATEGIAI_MOD", "0")
    assert not st.mod("értékeld a helyzetet", h).aktiv
    monkeypatch.delenv("STRATEGIAI_MOD")
    monkeypatch.setattr(llm_kapu, "_STRATEGIAI", {"nap": llm_kapu._bp_nap(), "usd": 2.5, "hivasok": 9})
    m = st.mod("értékeld a helyzetet", h)
    assert not m.aktiv and m.ok and "mai kerete elfogyott" in m.jegyzet and "$2.50 / $2.00" in m.jegyzet


# ── a Feldwebel köre hamis kapuval ─────────────────────────────────────────

def _db(tmp_path, elozmeny: list | None = None):
    ut = tmp_path / "f.db"
    con = sqlite3.connect(ut)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, sender TEXT, "
                "recipient TEXT, subject TEXT, message TEXT, priority TEXT, thread_id INTEGER, reply_to INTEGER, "
                "status TEXT)")
    con.execute("CREATE TABLE telegram_history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, "
                "role TEXT NOT NULL, agent_id TEXT DEFAULT 'deepseek', content TEXT NOT NULL, timestamp TEXT NOT NULL)")
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, status TEXT, priority TEXT, "
                "assigned_to TEXT, assigned_by TEXT, created_at TEXT)")
    for role, agent, szoveg, ido in elozmeny or []:
        con.execute("INSERT INTO telegram_history (chat_id, role, agent_id, content, timestamp) VALUES ('42',?,?,?,?)",
                    (role, agent, szoveg, ido))
    con.commit()
    con.close()

    def g():
        k = sqlite3.connect(ut)
        k.row_factory = sqlite3.Row
        return k
    return g


VALASZ = ("Helyzetkép: a forint gyengül. Értékelés: az MNB vár.\nÁlláspont: nem lép decemberben.\n"
          "Állítás: 2027-01-15-ig az MNB alapkamata nem változik (70%)")


@pytest.fixture
def kor(tmp_path, monkeypatch):
    import feldwebel as fw
    import futar_kliens
    from feldwebel import BridgeContext, responder
    elkapott, push = [], []
    allapot = {"get_db": _db(tmp_path)}

    async def kapu(payload, purpose=""):
        elkapott.append({"payload": json.loads(json.dumps(payload, ensure_ascii=False)), "purpose": purpose})
        return {"model": "claude-opus-5-5" if "strategiai" in purpose else "claude-haiku-5-5",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": VALASZ}}]}, None, []

    async def tg(szoveg):
        push.append(szoveg)

    async def kulso():
        return []
    napl = st.MemoriaNaplo()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt-TITOK")
    monkeypatch.delenv("STRATEGIAI_PG_DSN", raising=False)
    monkeypatch.delenv("ELSO_KOZLES_PG_DSN", raising=False)
    monkeypatch.setattr(st, "naplo", lambda: napl)
    monkeypatch.setattr(llm_kapu, "_STRATEGIAI", {"nap": llm_kapu._bp_nap(), "usd": 0.0, "hivasok": 0})
    monkeypatch.setattr(responder, "_kulso_eszkozok", kulso)
    monkeypatch.setattr(responder, "_rag_top", lambda q, n=3: [])
    monkeypatch.setattr(futar_kliens, "bekotve", lambda: True)

    def ctx_uj(get_db):
        monkeypatch.setattr(fw, "_ctx", BridgeContext(telegram_push=tg, get_db=get_db, llm_chat=kapu))
    ctx_uj(allapot["get_db"])

    def futtat(szoveg, elozmeny=None):
        elkapott.clear()
        push.clear()
        if elozmeny is not None:                         # friss adatbázis a megadott előzménnyel
            d = tmp_path / f"e{len(list(tmp_path.iterdir()))}"
            d.mkdir()
            allapot["get_db"] = _db(d, elozmeny)
            ctx_uj(allapot["get_db"])
        _run(responder.respond(szoveg, "42"))
        return elkapott, push
    return futtat, responder, napl, allapot


def _eszkoztar_kategoriak(payload) -> set:
    for m in payload["messages"]:
        for tc in m.get("tool_calls") or []:
            if tc["function"]["name"] == eszkoz_ut.BETOLTO_NEV:
                return set(json.loads(tc["function"]["arguments"])["kategoriak"])
    return set()


def test_egyszeru_kerdesnel_minden_a_regi(kor):
    futtat, responder, napl, _ = kor
    elk, push = futtat("mi a helyzet a forinttal?")
    p = elk[0]
    assert p["purpose"] == "feldwebel/telegram"
    assert p["payload"]["messages"][0]["content"] == responder.SYSTEM_PROMPT_TEMPLATE   # a karcsú mag, toldat nélkül
    assert "STRATÉGIAI MÓD" not in json.dumps(p["payload"], ensure_ascii=False)
    assert not _eszkoztar_kategoriak(p["payload"])
    assert "stratégiai mód" not in push[-1] and napl.sorok() == []      # az „Állítás:” sort sem naplózza


def test_strategiai_uzenetnel_bovul_a_prompt_opus_es_naplo(kor):
    futtat, responder, napl, allapot = kor
    elk, push = futtat("értékeld: mi következik a forint gyengüléséből?")
    p = elk[0]
    assert p["purpose"] == st.CEL_FELDWEBEL
    rendszer = p["payload"]["messages"][0]["content"]
    assert rendszer.startswith(responder.SYSTEM_PROMPT_TEMPLATE)
    assert "Te Claus vagy, a Kommandant vezérkari főnöke" in rendszer and "szász alapossággal" in rendszer
    assert st.FORMAI_SZABALY in rendszer
    assert set(st.ELOTOLTES) <= _eszkoztar_kategoriak(p["payload"])
    # a válasz állítása a naplóban, a jegyzet a Telegramon, a forduló jelölve az előzményben
    sorok = napl.sorok("allitas")
    assert len(sorok) == 1 and sorok[0]["payload"]["hatarido"] == "2027-01-15"
    assert sorok[0]["payload"]["valoszinuseg"] == 70 and sorok[0]["payload"]["forras"] == "feldwebel"
    assert "stratégiai mód" in push[-1].split("\n")[0] and "📌 #1 naplóban" in push[-1]
    k = allapot["get_db"]()
    agent = k.execute("SELECT agent_id FROM telegram_history WHERE role='assistant' ORDER BY id DESC").fetchone()[0]
    assert agent.endswith(st.STRATEGIAI_JEL)
    # a kabin-előzmény is hordozza a naplójegyzetet
    assert "📌 #1 naplóban" in k.execute("SELECT message FROM messages ORDER BY id DESC").fetchone()[0]
    # ugyanaz az állítás másodszor nem kerül be újra
    futtat("értékeld újra")
    assert len(napl.sorok("allitas")) == 1


def test_a_reggeli_nyitora_adott_valasz_strategiai_folytatas(kor):
    futtat, _, _, _ = kor
    most = datetime.now(timezone.utc)
    elozmeny = [("assistant", "reggeli" + st.STRATEGIAI_JEL, "🎖 REGGELI HELYZET … Kérdés: lép az MNB?",
                 (most - timedelta(hours=1)).isoformat())]
    elk, push = futtat("szerintem nem lép, mert a forint stabil", elozmeny=elozmeny)
    assert elk[0]["purpose"] == st.CEL_FELDWEBEL
    # a régi (13 órás) nyitóra adott válasz már nem folytatás
    elozmeny = [("assistant", "reggeli" + st.STRATEGIAI_JEL, "régi nyitó", (most - timedelta(hours=13)).isoformat())]
    elk, _ = futtat("szerintem nem lép, mert a forint stabil", elozmeny=elozmeny)
    assert elk[0]["purpose"] == "feldwebel/telegram"


def test_kimerult_keretnel_rendes_mod_kimondva(kor, monkeypatch):
    futtat, responder, napl, _ = kor
    monkeypatch.setattr(llm_kapu, "_STRATEGIAI", {"nap": llm_kapu._bp_nap(), "usd": 9.0, "hivasok": 40})
    elk, push = futtat("értékeld a helyzetet")
    assert elk[0]["purpose"] != st.CEL_FELDWEBEL
    assert elk[0]["payload"]["messages"][0]["content"] == responder.SYSTEM_PROMPT_TEMPLATE
    assert "mai kerete elfogyott" in push[-1] and napl.sorok() == []


def test_helyesbites_llm_nelkul(kor):
    futtat, _, napl, _ = kor
    futtat("értékeld a helyzetet")                       # #1 állítás
    elk, push = futtat("#1 hamis")
    assert elk == [] and "Rögzítve: #1 → hamis" in push[-1]
    assert st.ervenyes_iteletek(napl.sorok())[1]["itelet"] == "hamis"
    _, push = futtat("#99 igaz")
    assert "Nincs #99" in push[-1]


# ══ 2. A NAPLÓ ════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("sor, vart", [
    ("Állítás: 2027-01-15-ig az MNB alapkamata nem változik (70%)",
     {"hatarido": "2027-01-15", "allitas": "az MNB alapkamata nem változik", "valoszinuseg": 70}),
    ("**Állítás:** 2027-03-31-ig a Fed legalább egyszer csökkent (p=60%)",
     {"hatarido": "2027-03-31", "allitas": "a Fed legalább egyszer csökkent", "valoszinuseg": 60}),
    ("📌 Ellenőrizhető állítás: 2026.12.31-ig az infláció 4% alá csökken",
     {"hatarido": "2026-12-31", "allitas": "az infláció 4% alá csökken", "valoszinuseg": None}),
])
def test_allitas_kivesz(sor, vart):
    assert st.allitas_kivesz("Helyzetkép…\n\n" + sor, MA) == vart


@pytest.mark.parametrize("szoveg", [
    "nincs benne állítás",
    "Állítás: 2026-11-20-ig ez már elmúlt (50%)",               # a múltban
    "Állítás: 2035-01-01-ig túl messze van ahhoz (50%)",        # nem ellenőrizhető belátható időn belül
    "Állítás: 2027-01-15-ig x (50%)",                           # nincs érdemi tartalom
])
def test_allitas_kivesz_ervenytelen(szoveg):
    assert st.allitas_kivesz(szoveg, MA) is None


def test_a_naplo_hash_lanc_es_csak_hozzafuzes():
    n = st.MemoriaNaplo()
    a = st.allitas_kivesz("Állítás: 2027-01-15-ig az MNB alapkamata nem változik (70%)", MA)
    r1 = st.rogzit(a, forras="feldwebel", n=n, kerdes="mi lesz?")
    r2 = st.rogzit({**a, "allitas": "az  MNB alapkamata NEM változik"}, forras="feldwebel", n=n)   # ugyanaz
    assert r1 == {"ok": True, "id": 1, "uj": True} and r2 == {"ok": True, "id": 1, "uj": False}
    st.rogzit(st.allitas_kivesz("Állítás: 2027-02-01-ig a Fed vág (40%)", MA) or
              {"hatarido": "2027-02-01", "allitas": "a Fed kamatot vág", "valoszinuseg": 40}, forras="reggeli", n=n)
    assert st.lanc_ellenoriz(n)["ep"] and st.lanc_ellenoriz(n)["n"] == 2
    n._sorok[0]["payload"]["allitas"] = "utólag átírva"            # hamisítás → a lánc törik
    assert not st.lanc_ellenoriz(n)["ep"]
    assert st.rogzit(a, forras="x", n=None) == {"ok": False, "ok_szoveg": "nincs napló (STRATEGIAI_PG_DSN)"}


def test_a_pg_sema_csak_hozzafuzes_es_nincs_sqlite():
    assert "UNIQUE (tipus, kulcs)" in st.SEMA and "BEFORE UPDATE OR DELETE" in st.SEMA
    assert "RAISE EXCEPTION" in st.SEMA
    forras = Path(st.__file__).read_text(encoding="utf-8")
    assert "sqlite3" not in forras                                     # házi szabály: új tárolás csak Postgres


def test_rogzites_jegyzet():
    a = {"hatarido": "2027-01-15", "allitas": "az MNB nem lép", "valoszinuseg": 70}
    assert st.rogzites_jegyzet(a, {"ok": True, "id": 7}) == "📌 #7 naplóban — 2027-01-15-ig: az MNB nem lép (70%)"
    assert "nincs naplózva: nincs napló" in st.rogzites_jegyzet(a, {"ok": False, "ok_szoveg": "nincs napló"})
    assert st.rogzites_jegyzet(None, None) is None


# ── a havi pontozás ──────────────────────────────────────────────────────────

class HamisHivo:
    """A `dontes_szimulator.Hivo` felülete: (szerver, eszköz, paraméter) → (válasz, hiba); munkamenet no-op."""

    def __init__(self, valaszok: dict):
        self.valaszok, self.hivasok = valaszok, []

    def echolot_munkamenet(self):
        import contextlib

        @contextlib.asynccontextmanager
        async def _m():
            yield self
        return _m()

    async def __call__(self, szerver, eszkoz, par):
        self.hivasok.append((eszkoz, dict(par)))
        v = self.valaszok.get(eszkoz)
        v = v(par) if callable(v) else v
        if v is None:
            return {"hiba": "nincs"}, "nincs"
        return v, None


def _hamis_chat(*valaszok, modell="claude-opus-5-5", usd=0.05):
    sor = list(valaszok)
    hivasok = []

    async def chat(payload, *, purpose=""):
        hivasok.append({"purpose": purpose, "payload": payload})
        v = sor.pop(0)
        return {"model": modell, "koltes_usd": usd,
                "choices": [{"message": {"content": v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}}]
                }, None, []
    chat.hivasok = hivasok
    return chat


def test_havi_kor_pontoz_brier_es_helyesbites():
    n = st.MemoriaNaplo()
    lejart = [("2026-11-30", "az MNB alapkamata nem változik", 70), ("2026-11-15", "a Fed legalább egyszer vág", 80),
              ("2026-11-20", "a szerb választást kiírják", 60)]
    for h, sz, p in lejart:
        n.hozzafuz("allitas", f"{h}:{sz}", {"hatarido": h, "allitas": sz, "valoszinuseg": p,
                                            "rogzitve_bp": "2026-11-01T08:00:00+01:00"})
    n.hozzafuz("allitas", "2027-05-01:x", {"hatarido": "2027-05-01", "allitas": "még nem járt le", "valoszinuseg": 50})
    hivo = HamisHivo({"search_news": {"articles": [{"title": "Az MNB nem változtatott", "source_name": "Portfolio"}]}})
    chat = _hamis_chat({"iteletek": [{"id": 1, "itelet": "igaz", "indok": "nem változott"},
                                     {"id": 2, "itelet": "hamis", "indok": "nem vágott"},
                                     {"id": 3, "itelet": "eldontetlen"}, {"id": 4, "itelet": "igaz"},   # 4: nem járt le
                                     {"id": 99, "itelet": "igaz"}]})
    tg = []

    async def telegram(s):
        tg.append(s)
    r = _run(st.havi_kor(telegram=telegram, chat=chat, hivo=hivo, n=n,
                         most=datetime(2026, 12, 1, 8, 5, tzinfo=timezone.utc)))
    assert r["allapot"] == "kesz" and r["pontozott"] == 3
    assert chat.hivasok[0]["purpose"] == st.CEL_PONTOZAS
    assert [e for e, _ in hivo.hivasok] == ["search_news"] * 3
    o = r["osszesito"]
    assert o["db"] == {"igaz": 1, "hamis": 1, "eldontetlen": 1}
    assert o["brier"] == pytest.approx(((0.7 - 1) ** 2 + (0.8 - 0) ** 2) / 2, abs=1e-4)
    assert o["talalat"] == 1 and o["legrosszabb"][0]["id"] == 2
    assert "HAVI SZÁMADÁS" in tg[0] and "Brier: 0,365" in tg[0] and "#2 (80% → hamis)" in tg[0]
    # ugyanabban a hónapban másodszor nem fut
    assert _run(st.havi_kor(telegram=telegram, chat=chat, hivo=hivo, n=n,
                            most=datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc)))["allapot"] == "mar_ment"
    # a Kommandant helyesbítése felülírja a bíróét
    assert "eddig: hamis" in st.helyesbites("#2-es állítás igaz", n=n)
    assert st.osszesito(n)["db"] == {"igaz": 2, "hamis": 0, "eldontetlen": 1}
    assert st.lanc_ellenoriz(n)["ep"]


def test_kereso_kifejezes():
    assert st.kereso_kifejezes({"kereses": "MNB alapkamat"}) == "MNB alapkamat"
    k = st.kereso_kifejezes({"allitas": "az MNB alapkamata decemberben nem változik"})
    assert k.startswith("MNB") and "alapkamata" in k


# ══ 3. A REGGELI NYITÓ ════════════════════════════════════════════════════════

LAPSZAM = {"date": "2026-11-24", "lang": "hu", "payload": {
    "brief": {"headline": "Elsüllyedt az orosz hadihajó", "topics": [
        {"title": "Trump: new warning to Iran", "summary": "Strikes after New Year", "trend": "steady",
         "story_id": "aa7"}], "outlook": "Feszült hét"},
    "top_stories": [{"story_id": "a18", "title": "Meghosszabbították Matolcsy György letartóztatását",
                     "source_count": 13, "sphere": "hu_economy"},
                    {"story_id": "a0d", "title": "Jóváhagyta az EU Tanácsa a 2027-es költségvetést",
                     "source_count": 7, "sphere": "hu_economy"}]}}

VALASZTAS = {"helyzetek": [
    {"terulet": "gazdasag", "cim": "EU-költségvetés 2027", "kereses_en": "EU budget 2027", "kereses_hu": "uniós költségvetés"},
    {"terulet": "geopolitika", "cim": "Orosz hajó Líbiánál", "kereses_en": "Russian warship Libya",
     "kereses_hu": "orosz hadihajó"},
    {"terulet": "belpolitika", "cim": "Matolcsy-ügy", "kereses_en": "Matolcsy arrest", "kereses_hu": "Matolcsy"}]}

IRAS = {"helyzetek": [
    {"terulet": "gazdasag", "cim": "EU-költségvetés 2027",
     "helyzetkep": "Az EU Tanácsa jóváhagyta a 2027-es számokat. A német sajtó a fegyelmet, a magyar a kohéziós pénzt "
                   "nézi. Ez a harmadik mondat már túl sok. És a negyedik is.",
     "allaspont": "A magyar forrás feltételhez kötve marad, mert a jogállamisági eljárás nem zárult le. Második mondat.",
     "kerdes": "Számoljunk-e 2027-ben kohéziós pénz nélkül",
     "allitas": {"hatarido": "2027-03-31", "szoveg": "a Bizottság nem szabadít fel új kohéziós forrást Magyarországnak",
                 "valoszinuseg": 65}},
    {"terulet": "geopolitika", "cim": "Orosz hajó Líbiánál",
     "helyzetkep": "Ukrajna Líbiánál elsüllyesztett egy orosz hadihajót. Az orosz sajtó hallgat, az ukrán ünnepel.",
     "allaspont": "A háború déli frontja a Földközi-tengerre terjed.", "kerdes": "Figyeljük-e a líbiai kikötőket?",
     "allitas": {"hatarido": "2026-11-01", "szoveg": "már elmúlt dátum, ez nem ellenőrizhető", "valoszinuseg": 50}},
    {"terulet": "belpolitika", "cim": "Matolcsy-ügy",
     "helyzetkep": "Három hónappal meghosszabbították Matolcsy György letartóztatását. A kormánypárti sajtó a jogállamot, "
                   "az ellenzéki a vagyont emeli ki.",
     "allaspont": "Az ügy 2027 tavaszáig napirenden marad.", "kerdes": "Kell-e erről elemzés a Makronómnak?",
     "allitas": {"hatarido": "2027-02-28", "szoveg": "vádemelés történik Matolcsy György ügyében", "valoszinuseg": 55}}]}


def _nyito_hivo(lapszam=LAPSZAM):
    return HamisHivo({"get_daily_edition": lapszam,
                      "attention_gap": {"results": [{"keyword": "Kert Attila", "gap_score": 80}]},
                      "regional_framing": {"by_region": {"regional_us": {"dominant_frame": "conflict"}}},
                      "frame_divergence": {"by_sphere": {"hu_press": {"dominant_frame": "economic"}}}})


def test_reggeli_nyito_szerkezete_naplo_es_elozmeny():
    n, tg, elo, tar = st.MemoriaNaplo(), [], [], []

    async def telegram(s):
        tg.append(s)
    hivo, chat = _nyito_hivo(), _hamis_chat(VALASZTAS, IRAS)
    most = datetime(2026, 11, 24, 6, 20, tzinfo=timezone.utc)     # 07:20 Budapest
    r = _run(st.reggeli_nyito(telegram=telegram, chat=chat, hivo=hivo, n=n, most=most, elozmeny=elo.append,
                              tarol=lambda s, m: tar.append((s, m))))
    assert r["allapot"] == "kesz"
    # a 12 nyelvű kép: lapszám hu + en, figyelemrés, és helyzetenként régió + szféra
    eszk = [e for e, _ in hivo.hivasok]
    assert eszk[:3] == ["get_daily_edition", "get_daily_edition", "attention_gap"]
    assert eszk.count("regional_framing") == 3 and eszk.count("frame_divergence") == 3
    assert [c["purpose"] for c in chat.hivasok] == [st.CEL_NYITO_TERV, st.CEL_NYITO_IRAS]
    assert "vezérkari főnöke" in chat.hivasok[1]["payload"]["messages"][0]["content"]
    # a szerkezet: 3 helyzet; helyzetkép ≤ 2 mondat, álláspont 1 mondat, kérdés kérdőjellel
    h = r["helyzetek"]
    assert [x["terulet"] for x in h] == ["gazdasag", "geopolitika", "belpolitika"]
    assert h[0]["helyzetkep"].endswith("kohéziós pénzt nézi.") and "harmadik" not in h[0]["helyzetkep"]
    assert h[0]["allaspont"].endswith("nem zárult le.") and h[0]["kerdes"].endswith("nélkül?")
    assert h[1]["allitas"] is None and any("nem ellenőrizhető" in f for f in r["figyelmeztetesek"])
    # a Telegram-üzenet: fej, három szakasz, Álláspont + Kérdés mindenhol, az állítások naplóazonosítóval
    uzenet = tg[0]
    assert uzenet.startswith("🎖 <b>REGGELI HELYZET</b> · 2026-11-24, kedd · Opus 5.5")
    assert uzenet.count("<b>Álláspont:</b>") == 3 and uzenet.count("<b>Kérdés:</b>") == 3
    assert "<b>1. Gazdaság — EU-költségvetés 2027</b>" in uzenet and "<b>3. Belpolitika — Matolcsy-ügy</b>" in uzenet
    assert "📌 #2 2027-03-31-ig" in uzenet and "(65%)" in uzenet and "Válaszolj ide" in uzenet
    # napló: a nyitó (napi egyedi kulccsal) és a két ellenőrizhető állítás, ép lánc
    assert [s["tipus"] for s in n.sorok()] == ["nyito", "allitas", "allitas"]
    assert n.sorok("allitas")[0]["payload"]["forras"] == "reggeli"
    assert n.sorok("allitas")[0]["payload"]["kereses"] == "uniós költségvetés"
    assert st.lanc_ellenoriz(n)["ep"]
    # az előzménybe (sima szöveg) és a kabinba ugyanez megy
    assert elo and "<b>" not in elo[0] and "REGGELI HELYZET" in elo[0]
    assert tar and tar[0][1] == "claude-opus-5-5"
    # aznap másodszor nem küld
    tg.clear()
    r2 = _run(st.reggeli_nyito(telegram=telegram, chat=_hamis_chat(), hivo=hivo, n=n, most=most))
    assert r2["allapot"] == "mar_ment" and tg == []


def test_reggeli_nyito_adat_nelkul_nem_ertekel():
    tg = []

    async def telegram(s):
        tg.append(s)
    chat = _hamis_chat()
    r = _run(st.reggeli_nyito(telegram=telegram, chat=chat, hivo=HamisHivo({}), n=st.MemoriaNaplo(),
                              most=datetime(2026, 11, 24, 6, 20, tzinfo=timezone.utc)))
    assert r["allapot"] == "nincs_adat" and chat.hivasok == []
    assert "ma elmarad" in tg[0] and "Adat nélkül nem értékelek" in tg[0]


def test_reggeli_nyito_tegnapi_lapszamra_esik_vissza():
    hivok = []

    def lapszam(par):
        hivok.append(par["date"])
        if par["date"] == "2026-11-24":
            return {"error": "no_snapshot", "latest_available": "2026-11-23"}
        return LAPSZAM
    r = _run(st.nyito_alapanyag(_nyito_hivo(lapszam), "2026-11-24"))
    assert hivok == ["2026-11-24", "2026-11-23", "2026-11-24", "2026-11-23"]
    assert r["lapszam_hu"]["top"][0]["cim"].startswith("Meghosszabbították") and not r["hianyok"]


def test_reggeli_nyito_keret_az_meglevo_szamlaval():
    """A nyitó a Nachtschicht keret-számláját használja: ha a becslés túllépné, nem hív, és ezt kimondja."""
    import nachtschicht as ns
    with pytest.raises(ns.KeretElfogyott):
        _run(st._llm_json(ns.Szamla(keret=0.01), _hamis_chat(VALASZTAS), "s", "u", st.CEL_NYITO_TERV, 8000))


def test_cron_lepes_ablak_es_egyszeri(monkeypatch):
    from zoneinfo import ZoneInfo
    bp = ZoneInfo("Europe/Budapest")
    indult = []
    monkeypatch.setattr(st, "_hatter", lambda coro: (indult.append(coro.__name__), coro.close()))
    monkeypatch.setattr(st, "_UTOLSO", {})
    assert st.cron_lepes(datetime(2026, 11, 24, 7, 0, tzinfo=bp), telegram=None) == []       # túl korán
    assert st.cron_lepes(datetime(2026, 11, 24, 7, 20, tzinfo=bp), telegram=None) == ["nyito"]
    assert st.cron_lepes(datetime(2026, 11, 24, 7, 21, tzinfo=bp), telegram=None) == []      # egyszer naponta
    monkeypatch.setattr(st, "_UTOLSO", {})
    assert st.cron_lepes(datetime(2026, 11, 24, 11, 0, tzinfo=bp), telegram=None) == []      # túl késő
    assert st.cron_lepes(datetime(2026, 12, 1, 8, 0, tzinfo=bp), telegram=None) == ["nyito", "havi"]   # 1-jén mindkettő
    assert st.cron_lepes(datetime(2026, 12, 1, 9, 0, tzinfo=bp), telegram=None) == []
    monkeypatch.setenv("STRATEGIAI_REGGELI", "0")
    monkeypatch.setattr(st, "_UTOLSO", {})
    assert st.cron_lepes(datetime(2026, 11, 25, 7, 30, tzinfo=bp), telegram=None) == []
    monkeypatch.setenv("STRATEGIAI_REGGELI_IDO", "06:45")
    monkeypatch.delenv("STRATEGIAI_REGGELI")
    assert st.cron_lepes(datetime(2026, 11, 25, 6, 50, tzinfo=bp), telegram=None) == ["nyito"]
    assert indult == ["reggeli_nyito", "reggeli_nyito", "havi_kor", "reggeli_nyito"]


# ══ 4. A KERET ════════════════════════════════════════════════════════════════

class _U:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 10_000, 2_000, 0, 0


def test_a_kapu_konyvelese_a_napi_strategiai_szamlalora(monkeypatch):
    monkeypatch.setattr(llm_kapu, "_KOLTES", {"honap": "", "usd": 0.0, "hivasok": 0, "szintenkent": {}})
    monkeypatch.setattr(llm_kapu, "_STRATEGIAI", {"nap": "", "usd": 0.0, "hivasok": 0})
    usd = llm_kapu._konyvel(_U(), "claude-opus-5-5", "okos", st.CEL_FELDWEBEL)
    assert usd == pytest.approx((10_000 * 4 + 2_000 * 20) / 1e6)
    llm_kapu._konyvel(_U(), "claude-haiku-5-5", "olcso", "feldwebel/telegram")          # nem stratégiai
    a = llm_kapu.strategiai_allapot()
    assert a["hivasok"] == 1 and a["koltes_usd"] == pytest.approx(0.08) and a["keret_usd"] == 2.0
    assert a["modell"] == "claude-opus-5-5" and not a["kimerult"]
    assert llm_kapu.allapot()["strategiai"]["koltes_usd"] == pytest.approx(0.08)        # a kabin ebből rajzol
    monkeypatch.setenv("STRATEGIAI_NAPI_KERET_USD", "0.05")
    assert llm_kapu.strategiai_kimerult()
    monkeypatch.setenv("STRATEGIAI_NAPI_KERET_USD", "nem szám")
    assert llm_kapu.strategiai_napi_keret() == 2.0
    # új nap: nulláról
    monkeypatch.setattr(llm_kapu, "_STRATEGIAI", {"nap": "2020-01-01", "usd": 99.0, "hivasok": 9})
    assert llm_kapu.strategiai_allapot()["koltes_usd"] == 0.0 and not llm_kapu.strategiai_kimerult()


def test_a_kabin_mutatja_a_strategiai_keretet():
    js = (Path(st.__file__).parent / "static" / "app" / "app.js").read_text(encoding="utf-8")
    assert "strategiaiSor(k.strategiai)" in js and "Stratégiai mód ma" in js


def test_a_prompt_a_kommandant_szovege_a_hang_mondattal():
    p = st.prompt()
    assert p.startswith("Te Claus vagy, a Kommandant vezérkari főnöke, nem a titkára.")
    assert "magyar vezérkari főnök, szász alapossággal és sváb munkabírással" in p
    assert "Oberstleutnant" not in p
    assert "Reggelente te kezdesz" in p and "nem a manipuláció" in p


# ── a valódi Postgres (csak kérésre: STRATEGIAI_TESZT_DSN, eldobható adatbázis) ────────────────────

@pytest.mark.network
@pytest.mark.skipif(not __import__("os").environ.get("STRATEGIAI_TESZT_DSN"),
                    reason="STRATEGIAI_TESZT_DSN nincs (eldobható PG)")
def test_pg_naplo_hozzafuzes_lanc_es_trigger():
    import os
    psycopg = pytest.importorskip("psycopg")
    n = st.PgNaplo(os.environ["STRATEGIAI_TESZT_DSN"])
    a = st.rogzit({"hatarido": "2027-01-15", "allitas": "az MNB alapkamata nem változik", "valoszinuseg": 70},
                  forras="teszt", n=n)
    assert a["ok"] and a["uj"]
    b = n.hozzafuz("pontozas", str(a["id"]), {"allitas_id": a["id"], "itelet": "igaz"})
    assert b and n.hozzafuz("pontozas", str(a["id"]), {"allitas_id": a["id"], "itelet": "hamis"}) is None
    assert st.lanc_ellenoriz(n)["ep"]
    with psycopg.connect(n.dsn, autocommit=True) as k:
        with pytest.raises(psycopg.errors.RaiseException):
            k.execute("UPDATE strategiai_naplo SET payload = '{}'::jsonb")
        with pytest.raises(psycopg.errors.RaiseException):
            k.execute("DELETE FROM strategiai_naplo")
