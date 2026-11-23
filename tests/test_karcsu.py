"""KARCSÚ FELDWEBEL (2026-11-23): költségkorlát-őr, képesség-őr, cache-töréspont-őr, kivonat — hálózat nélkül.

A Kommandant: „Túl sokba kerül a futás… Valami tényleg nem oké.” Mérve (count_tokens, Haiku 5.5, éles DB-mentéssel):
a Feldwebel üzenetenként ~50 ezer tokent küldött (45 eszköz ≈ 17,2k, rendszerszöveg ≈ 32k). Utána: ~7,7k előtöltés
nélkül, ~9,5–9,8k előtöltéssel, és a ~6,6k-s stabil előtag (eszközök + mag) 1 órás cache-ből.

  1. KÖLTSÉGKORLÁT-ŐR: a tipikus első kör becsült tokenszáma a plafon alatt; egy új mag-eszköz vagy promptbővítés,
     amely túllépi, itt bukik. A becslés karakterszámból, a mért arányoknál óvatosabban (többet becsül).
  2. KÉPESSÉG-ŐR: minden korábbi eszköz elérhető — vagy a magban, vagy pontosan egy kategóriában, a betöltőn át.
  3. CACHE-ŐR: a töréspont a stabil részen (eszközök + mag), a változó rész (dátum, RAG, feladat) mögötte.
  4. KIVONAT: a nagy eszközeredményből kimondott kivonat, a teljes szöveg lapozható.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3

import pytest

import kivonat
import llm_kapu
from feldwebel import eszkoz_ut


def _run(c):
    return asyncio.run(c)


# ── a becslő: karakter → token, a mért aránynál óvatosabban ────────────────
#
# Mérve 2026-11-23 (count_tokens, claude-haiku-5-5): a magyar rendszerszöveg 1,87 karakter/token, az eszközök JSON-ja
# 2,05 karakter/token; az eszközhasználat rendszerpromptja 286, a halasztott eszközök jelenléte +93 token. A becslő
# 1,85 és 2,0 osztóval számol (kicsit többet becsül, mint a valóság).
SZOVEG_OSZTO = 1.85
ESZKOZ_OSZTO = 2.0
ESZKOZ_ALAP = 286 + 93
UZENET_ALAP = 8

#: A plafonok (becsült token). A stabil előtag a cache-elt rész; a tipikus kör előtöltés nélkül; előtöltéssel.
STABIL_PLAFON = 7_000
TIPIKUS_PLAFON = 8_000
ELOTOLTOTT_PLAFON = 10_000


def becsles(kw: dict) -> dict:
    """A kapu Anthropic-alakú kérésének becsült tokenszáma, részekre bontva (a halasztott eszköz nem számít, csak
    ha egy `tool_reference` betölti)."""
    sysz = kw.get("system") or ""
    sysz = sysz[0]["text"] if isinstance(sysz, list) else sysz
    tools = kw.get("tools") or []
    szerint = {t["name"]: t for t in tools}
    mag = [t for t in tools if not t.get("defer_loading")]
    eszkoz = (len(json.dumps(mag, ensure_ascii=False)) / ESZKOZ_OSZTO + ESZKOZ_ALAP) if tools else 0
    uzenet = 0.0
    for m in kw["messages"]:
        uzenet += UZENET_ALAP
        for b in m["content"]:
            if b.get("type") == "text":
                uzenet += len(b["text"]) / SZOVEG_OSZTO
            elif b.get("type") == "tool_result":
                c = b.get("content")
                if isinstance(c, list):
                    for r in c:
                        if r.get("type") == "tool_reference" and r["tool_name"] in szerint:
                            uzenet += len(json.dumps(szerint[r["tool_name"]], ensure_ascii=False)) / ESZKOZ_OSZTO
                else:
                    uzenet += len(str(c)) / SZOVEG_OSZTO
            elif b.get("type") == "tool_use":
                uzenet += len(json.dumps(b.get("input"), ensure_ascii=False)) / SZOVEG_OSZTO + 10
    rendszer = len(sysz) / SZOVEG_OSZTO
    return {"rendszer": round(rendszer), "eszkozok": round(eszkoz), "uzenetek": round(uzenet),
            "stabil": round(rendszer + eszkoz), "teljes": round(rendszer + eszkoz + uzenet)}


# ── a Feldwebel köre hamis kapuval (a payload, amit a respond() ELSŐNEK küld) ─────

def _db(tmp_path):
    ut = tmp_path / "f.db"
    con = sqlite3.connect(ut)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, sender TEXT, "
                "recipient TEXT, subject TEXT, message TEXT, priority TEXT, thread_id INTEGER, reply_to INTEGER, "
                "status TEXT)")
    con.execute("CREATE TABLE telegram_history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, "
                "role TEXT NOT NULL, agent_id TEXT DEFAULT 'deepseek', content TEXT NOT NULL, timestamp TEXT NOT NULL)")
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, status TEXT, priority TEXT, "
                "assigned_to TEXT, assigned_by TEXT, created_at TEXT)")
    # tipikus előzmény (az éles mintája, 11-22): 4 kör, a válaszok ~300 karakteresek; 9 nyitott feladat ~90 karakteres
    # címmel. A pillanatkép legrosszabb esetét (3 hosszú RAG-tétel, 10 hosszú feladat) külön teszt nézi.
    for i in range(4):
        con.execute("INSERT INTO telegram_history (chat_id, role, content, timestamp) VALUES ('42','user',?,?)",
                    (f"kérdés {i}: mi a helyzet az üggyel?", f"2026-11-22T10:0{i}:00"))
        con.execute("INSERT INTO telegram_history (chat_id, role, content, timestamp) VALUES ('42','assistant',?,?)",
                    ("Válasz: " + "a dolog áll, ennyit tudunk róla. " * 9, f"2026-11-22T10:0{i}:30"))
    for i in range(9):
        con.execute("INSERT INTO tasks (title, status, priority, created_at) VALUES (?, 'pending', 'normal', ?)",
                    (f"Feladat {i}: " + "egy nyitott ügy rövid leírása, " * 3, f"2026-11-2{i % 10}T00:00:00"))
    con.commit()
    con.close()

    def g():
        k = sqlite3.connect(ut)
        k.row_factory = sqlite3.Row
        return k
    return g


def _kulso_defs():
    """A hat külső MCP-szerver eszköze valós méretű leírással (halasztva mennek — a becslésbe nem számítanak)."""
    import kulso_mcp

    class T:
        def __init__(self, n):
            self.name, self.description = n, "Egy eszköz leírása, ami elég hosszú. " * 4
            self.inputSchema = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    return [kulso_mcp.eszkoz_definicio(n, [T(f"{n}_{i}") for i in range(20)]) for n in kulso_mcp.SZERVEREK]


@pytest.fixture
def kor(tmp_path, monkeypatch):
    import feldwebel as fw
    import futar_kliens
    from feldwebel import BridgeContext, responder
    get_db = _db(tmp_path)
    elkapott = []

    async def kapu(payload, purpose=""):
        elkapott.append({"payload": json.loads(json.dumps(payload, ensure_ascii=False)), "purpose": purpose})
        return {"model": "claude-haiku-5-5", "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Rendben."}}]}, None, []

    async def push(_):
        return None

    async def kulso():
        return _kulso_defs()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt-TITOK")
    monkeypatch.setattr(fw, "_ctx", BridgeContext(telegram_push=push, get_db=get_db, llm_chat=kapu))
    monkeypatch.setattr(responder, "_kulso_eszkozok", kulso)
    monkeypatch.setattr(responder, "_rag_top", lambda q, n=3: [])     # a tipikus üzenethez az élesben sincs RAG-találat
    monkeypatch.setattr(futar_kliens, "bekotve", lambda: True)

    def futtat(szoveg):
        elkapott.clear()
        _run(responder.respond(szoveg, "42"))
        p = elkapott[0]["payload"]
        kw, _ = llm_kapu.fordit_keres(p, modell="claude-haiku-5-5", effort="low", fallbacks=False)
        return p, kw
    return futtat, responder


TIPIKUS = {"forint": "mi a helyzet a forinttal?",
           "level": "írj levelet Lacinak, hogy a csütörtöki megbeszélés átkerül péntek 10-re",
           "kimi": "Kimi, mit gondolsz a forint mai gyengüléséről?"}


# ── 1. KÖLTSÉGKORLÁT-ŐR ───────────────────────────────────────────────────

def test_a_becslo_ovatosabb_a_mert_ertekeknel():
    # a mért 2026-11-23-i adatokon (karakter, token): a becslés sosem kevesebb a valóságnál
    for kar, tok, oszto in ((5984, 3207, SZOVEG_OSZTO), (6223, 3035, ESZKOZ_OSZTO), (1973, 1036 - 80, SZOVEG_OSZTO)):
        assert kar / oszto >= tok, (kar, tok)


def test_a_tipikus_uzenet_a_plafon_alatt(kor):
    futtat, _ = kor
    b = {nev: becsles(futtat(sz)[1]) for nev, sz in TIPIKUS.items()}
    for nev, e in b.items():
        assert e["stabil"] <= STABIL_PLAFON, (nev, e)
    assert b["forint"]["teljes"] <= TIPIKUS_PLAFON, b["forint"]            # előtöltés nélkül
    assert b["level"]["teljes"] <= ELOTOLTOTT_PLAFON, b["level"]           # levelezés (+ naptár) előtöltve
    assert b["kimi"]["teljes"] <= ELOTOLTOTT_PLAFON, b["kimi"]             # AI-ügynökök előtöltve
    # a stabil előtag mindhárom üzenetben UGYANAZ (cache)
    assert len({e["stabil"] for e in b.values()}) == 1


def test_uj_mag_eszkoz_vagy_promptbovites_buktatja(kor, monkeypatch):
    futtat, responder = kor
    alap = becsles(futtat(TIPIKUS["forint"])[1])
    # egy nagy új eszköz a MAGBAN: a stabil előtag túllépi a plafont → bukás
    nagy = {"type": "function", "function": {"name": "nagy_uj_eszkoz", "description": "Hosszú leírás. " * 600,
                                             "parameters": {"type": "object", "properties": {}}}}
    monkeypatch.setattr(responder, "TOOLS", responder.TOOLS + [nagy])
    monkeypatch.setattr(eszkoz_ut, "MAG", eszkoz_ut.MAG + ("nagy_uj_eszkoz",))
    assert becsles(futtat(TIPIKUS["forint"])[1])["stabil"] > STABIL_PLAFON
    # ugyanez kategória nélkül (nem a magban): halasztva jön, a becslés nem nő
    monkeypatch.setattr(eszkoz_ut, "MAG", tuple(n for n in eszkoz_ut.MAG if n != "nagy_uj_eszkoz"))
    assert becsles(futtat(TIPIKUS["forint"])[1])["stabil"] == alap["stabil"]
    # promptbővítés: +6000 karakter a magban → túl a plafonon
    monkeypatch.setattr(responder, "SYSTEM_PROMPT_TEMPLATE", responder.SYSTEM_PROMPT_TEMPLATE + "\nÚj szabály. " * 500)
    assert becsles(futtat(TIPIKUS["forint"])[1])["stabil"] > STABIL_PLAFON


def test_a_pillanatkep_felso_korlatos(kor, monkeypatch):
    """A legrosszabb eset: 3 hosszú RAG-tétel és 10 hosszú című feladat — legfeljebb ~1,5k token (a feladat plafonja)."""
    futtat, responder = kor
    monkeypatch.setattr(responder, "_rag_top", lambda q, n=3: [
        {"forras": "kimi", "cim": "Korábbi elemzés " * 20, "szoveg": "Hosszú korábbi munka szövege. " * 40,
         "ido": "2026-11-01"}] * n)

    class Ctx:
        @staticmethod
        def get_db():
            k = sqlite3.connect(":memory:")
            k.row_factory = sqlite3.Row
            k.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT, status TEXT, priority TEXT, "
                      "created_at TEXT)")
            k.executemany("INSERT INTO tasks (title, status, priority, created_at) VALUES (?, 'pending', 'high', '')",
                          [("Nagyon hosszú feladatcím " * 20,)] * 15)
            return k
    pk = responder._pillanatkep(Ctx, "bármi")
    assert pk.startswith("PILLANATKÉP") and pk.count("\n- #") == responder.FELADAT_SOR
    rag = pk.split("# NYITOTT FELADATOK")[0]
    assert len(rag) / SZOVEG_OSZTO <= 1_000, len(rag)          # a RAG-rész (≤3 tétel) legfeljebb ~1k token
    assert len(pk) / SZOVEG_OSZTO <= 1_700, len(pk)            # az egész pillanatkép legfeljebb ~1,7k token
    p, _ = futtat(TIPIKUS["forint"])
    assert len([m for m in p["messages"] if str(m.get("content")).startswith("PILLANATKÉP")]) == 1


# ── 2. KÉPESSÉG-ŐR ────────────────────────────────────────────────────────

def _regi_eszkozok() -> set[str]:
    """A 11-23 előtti Feldwebel teljes eszközlistájának forrásai (statikus lista, külső appok, futár, szimulátor,
    Faber, feladat-ügynök)."""
    import dontes_szimulator
    import futar_kliens
    import kulso_mcp
    from feldwebel import responder
    return ({t["function"]["name"] for t in responder.TOOLS} | set(kulso_mcp.SZERVEREK) | set(futar_kliens.MUVELETEK)
            | {dontes_szimulator.ESZKOZ_NEV, "faber", "ugynok"})


def test_minden_korabbi_eszkoz_a_magban_vagy_pontosan_egy_kategoriaban():
    for nev in _regi_eszkozok():
        hol = [k for k, v in eszkoz_ut.KATEGORIAK.items() if nev in v["eszkozok"]]
        assert (nev in eszkoz_ut.MAG) != bool(hol), f"{nev}: mag={nev in eszkoz_ut.MAG}, kategóriák={hol}"
        assert len(hol) <= 1, (nev, hol)
    # a kategóriák nem hivatkoznak nem létező eszközre
    for k, v in eszkoz_ut.KATEGORIAK.items():
        assert set(v["eszkozok"]) <= _regi_eszkozok(), k


def test_a_betolto_minden_kategoriat_betolt_es_a_leirasa_sorolja():
    d = eszkoz_ut.betolto_def()["function"]
    assert set(d["parameters"]["properties"]["kategoriak"]["items"]["enum"]) == set(eszkoz_ut.KATEGORIAK)
    for k, v in eszkoz_ut.KATEGORIAK.items():
        assert f"- {k}: " in d["description"]
        szoveg, nevek, kat = eszkoz_ut.betolt([k], _regi_eszkozok())
        assert kat == [k] and nevek == list(v["eszkozok"])
        if v["utmutato"]:
            assert v["utmutato"].split("\n")[0].replace("{profil}", "") in szoveg
    szoveg, nevek, _ = eszkoz_ut.betolt(["nincs_ilyen"], _regi_eszkozok())
    assert nevek == [] and "Ismeretlen kategória: nincs_ilyen" in szoveg


def test_a_payloadban_minden_eszkoz_ott_van_a_mag_elol_a_tobbi_halasztva(kor):
    futtat, _ = kor
    p, kw = futtat(TIPIKUS["forint"])
    nevek = [t["function"]["name"] for t in p["tools"]]
    assert set(_regi_eszkozok()) <= set(nevek)
    assert nevek[:len(eszkoz_ut.MAG)] == list(eszkoz_ut.MAG)
    assert nevek[len(eszkoz_ut.MAG):len(eszkoz_ut.MAG) + 2] == [eszkoz_ut.BETOLTO_NEV, kivonat.LAPOZ_NEV]
    halasztott = {t["name"] for t in kw["tools"] if t.get("defer_loading")}
    assert halasztott == set(nevek) - set(eszkoz_ut.MAG) - {eszkoz_ut.BETOLTO_NEV, kivonat.LAPOZ_NEV}


@pytest.mark.parametrize("szoveg, kell", [
    ("mi a helyzet a forinttal?", set()),
    ("írj levelet Lacinak", {"levelezes"}),
    ("Kimi, mit gondolsz?", {"ai_ugynokok"}),
    ("kérdezd meg a deepseeket", {"ai_ugynokok"}),
    ("nézzük a Task 223-at", {"ai_ugynokok"}),
    ("indíts egy ügynököt, ami figyeli az MNB-t", {"feladat_ugynokok"}),
    ("futtasd le a napi briefet", {"receptek"}),
    ("csináld meg a hirszemlémet", {"receptek"}),
    ("mi van az Agorán?", {"hirek"}),
    ("mennyi az alapkamat?", {"makro"}),
    ("mit mond erről a törvény?", {"jog"}),
    ("fáj a fejem, mit vegyek?", {"egeszseg", "tanacs"}),
    ("küldd el a fájlt", {"levelezes", "dokumentum"}),
    ("elemezd a #2-es képet", {"dokumentum"}),
    ("emlékszel, mit döntöttünk?", {"memoria"}),
    ("szólj a fejlesztonek", {"kommunikacio"}),
    ("nézd meg ezt: https://pelda.hu/cikk", {"web_melyebb"}),
    ("mi lenne, ha bevezetnénk a négynapos munkahetet?", {"szimulator"}),
    ("írd be a naptárba a határidőt", {"naptar_feladat"}),
])
def test_a_szandek_utvalaszto(szoveg, kell):
    assert kell <= set(eszkoz_ut.szandek(szoveg)), (szoveg, eszkoz_ut.szandek(szoveg))
    if not kell:
        assert eszkoz_ut.szandek(szoveg) == []


def test_fajl_nem_fajdalom_es_ekezet_nelkul_is():
    assert "egeszseg" not in eszkoz_ut.szandek("küldd el a fájlt")
    assert "levelezes" in eszkoz_ut.szandek("irj levelet Bencenek")
    assert "receptek" in eszkoz_ut.szandek("Csinald meg a hirszemlemet")


def test_rovid_jovahagyas_az_elozo_fordulo_szandekat_kapja():
    elozo = "írj levelet Lacinak Rendben, itt a tervezet. Elküldjem?"
    assert "levelezes" in eszkoz_ut.szandek("mehet", elozo)
    assert eszkoz_ut.szandek("mehet") == []


def test_mellekletnel_tanacs_dokumentum_es_naptar():
    k = set(eszkoz_ut.szandek("[Melléklet #7: hatarozat.pdf — 2 oldal · Studio file_id=f1] mit kell tennem?"))
    assert {"tanacs", "dokumentum", "naptar_feladat"} <= k


def test_elotoltes_a_kor_elejen_es_a_modell_maga_is_betolthet(kor, monkeypatch):
    futtat, responder = kor
    p, kw = futtat(TIPIKUS["kimi"])
    hivas = [m for m in p["messages"] if m.get("tool_calls")]
    assert hivas and hivas[0]["tool_calls"][0]["function"]["name"] == eszkoz_ut.BETOLTO_NEV
    eredmeny = [m for m in p["messages"] if m.get("tool_call_id") == eszkoz_ut.ELOTOLTES_ID][0]
    assert eredmeny["tool_reference"] == ["ai_query", "ai_task", "read_task_results"]
    assert "AI-ÜGYNÖKÖK" in eredmeny["content"]
    # az Anthropic-alakban: tool_result CSAK hivatkozással, az útmutató külön szövegblokk utána
    tr = [b for m in kw["messages"] for b in m["content"] if b.get("type") == "tool_result"][0]
    assert [r["type"] for r in tr["content"]] == ["tool_reference"] * 3
    # a modell maga tölt be egy kategóriát (jog) → a következő körben a hivatkozás ott van
    import feldwebel as fw
    hivasok, sor = [], [
        {"model": "claude-haiku-5-5", "choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": "", "tool_calls": [{"id": "t9", "type": "function", "function": {
                "name": eszkoz_ut.BETOLTO_NEV, "arguments": json.dumps({"kategoriak": ["jog"]})}}]}}]},
        {"model": "claude-haiku-5-5", "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Kész."}}]}]

    async def kapu(payload, purpose=""):
        hivasok.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        return sor.pop(0), None, []
    monkeypatch.setattr(fw._ctx, "llm_chat", kapu)
    _run(responder.respond("szia", "42"))
    m = [x for x in hivasok[1]["messages"] if x.get("tool_call_id") == "t9"][0]
    assert m["tool_reference"] == ["lexonomics"] and "JOGI IRAT" in m["content"]


def test_a_sf_tartalek_minden_eszkozt_lat_kiegeszites_nelkul(kor):
    futtat, _ = kor
    p, _ = futtat(TIPIKUS["kimi"])
    sf = llm_kapu.sf_tisztit(p)
    assert "cache_rendszer" not in sf and "cache_rendszer" in p
    assert all("defer_loading" not in t and "defer_loading" not in t["function"] for t in sf["tools"])
    assert all("tool_reference" not in m for m in sf["messages"])
    assert [t["function"]["name"] for t in sf["tools"]] == [t["function"]["name"] for t in p["tools"]]
    assert llm_kapu.sf_tisztit({"messages": [], "model": "x"}) == {"messages": [], "model": "x"}


# ── 3. CACHE-ŐR ───────────────────────────────────────────────────────────

def test_a_toresepont_a_stabil_reszen_a_valtozo_mogotte(kor):
    futtat, responder = kor
    kws = [futtat(sz)[1] for sz in TIPIKUS.values()]
    for kw in kws:
        assert kw["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert kw["cache_control"] == {"type": "ephemeral"}       # a farok automatikus (5 perc): a hosszabb TTL elöl
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ma = datetime.now(ZoneInfo("Europe/Budapest"))
        assert f"{ma:%Y-%m-%d}" not in kw["system"][0]["text"]                     # a mai dátum nem a magban
        assert kw["system"][0]["text"] == responder.SYSTEM_PROMPT_TEMPLATE            # a mag statikus szöveg
        assert "PILLANATKÉP — most:" not in kw["system"][0]["text"]
        assert any("PILLANATKÉP — most:" in b.get("text", "") for m in kw["messages"] for b in m["content"])
    # a stabil előtag (eszközök + rendszerszöveg) bájtra ugyanaz a három különböző üzenetnél
    assert len({json.dumps([kw["tools"], kw["system"]], ensure_ascii=False, sort_keys=True) for kw in kws}) == 1


def test_a_kapu_csak_kerésre_tesz_rendszer_torespontot():
    p = {"messages": [{"role": "system", "content": "mag"}, {"role": "user", "content": "x"}]}
    kw, _ = llm_kapu.fordit_keres(p, modell="claude-haiku-5-5", effort="low", fallbacks=False)
    assert kw["system"] == "mag"                                    # a régi hívók változatlanok
    kw, _ = llm_kapu.fordit_keres({**p, "cache_rendszer": "5m"}, modell="claude-haiku-5-5", effort="low",
                                  fallbacks=False)
    assert kw["system"] == [{"type": "text", "text": "mag", "cache_control": {"type": "ephemeral"}}]


def test_csupa_halasztott_eszkoznel_a_halasztas_lekerul():
    p = {"messages": [{"role": "user", "content": "x"}],
         "tools": [{"type": "function", "function": {"name": "a", "parameters": {"type": "object"}},
                    "defer_loading": True}]}
    kw, _ = llm_kapu.fordit_keres(p, modell="claude-haiku-5-5", effort="low", fallbacks=False)
    assert "defer_loading" not in kw["tools"][0]


# ── 4. KIVONAT ────────────────────────────────────────────────────────────

def test_kicsi_eredmeny_valtozatlan_nagybol_kimondott_kivonat_es_lapozhato():
    kert = []

    async def olvaso(payload, *, purpose=""):
        kert.append((purpose, payload))
        return {"model": "claude-haiku-5-5", "koltes_usd": 0.0004, "choices": [{"message": {
            "content": "EUR/HUF 384,12 (2026-11-21, MNB) — id: m1"}}]}, None, []
    tar = kivonat.EredmenyTar(olvaso=olvaso)
    assert _run(tar.feldolgoz("web_fetch", {}, "rövid")) == "rövid"
    nagy = "Az árfolyam 384,12 forint volt. " * 400
    ki = _run(tar.feldolgoz("web_fetch", {"url": "https://mnb.hu"}, nagy, feladat="mi a forint?"))
    assert ki.startswith("[KIVONAT — a(z) `web_fetch` eredménye") and f"{len(nagy)} karakter" in ki
    assert "384,12" in ki and 'eredmeny_lapoz(azon="e1"' in ki
    assert kert[0][0] == kivonat.OLVASO_CEL and "mi a forint?" in kert[0][1]["messages"][0]["content"]
    assert tar.olvasasok == 1 and tar.olvaso_usd == pytest.approx(0.0004)
    reszek = -(-len(nagy) // kivonat.RESZ_KAR)
    elso = tar.lapoz({"azon": "e1", "resz": 1})
    assert elso.startswith(f"[e1 — 1. rész / {reszek}]") and nagy[:100] in elso
    assert nagy[-50:] in tar.lapoz({"azon": "e1", "resz": reszek})
    assert "nincs ilyen kivonat" in tar.lapoz({"azon": "e9"})


def test_olvaso_nelkul_determinisztikus_vagas_kimondva():
    async def rossz(payload, *, purpose=""):
        return None, {"code": "transport"}, []
    tar = kivonat.EredmenyTar(olvaso=rossz)
    nagy = "x" * 10_000
    ki = _run(tar.feldolgoz("lexonomics", {}, nagy))
    assert ki.startswith("[LEVÁGVA — a(z) `lexonomics` eredménye 10000 karakter") and len(ki) < 3_800
    # kivétel: a betöltő / a nyugták / a hibaüzenet nem kivonatolódik
    assert _run(tar.feldolgoz("ugynok", {}, nagy)) == nagy
    hiba = json.dumps({"error": "y" * 5000})
    assert _run(tar.feldolgoz("web_fetch", {}, hiba)) == hiba


def test_a_feldwebel_kore_a_nagy_eredmenyt_kivonatolja(kor, monkeypatch):
    futtat, responder = kor
    import feldwebel as fw
    hivasok, sor = [], [
        {"model": "claude-haiku-5-5", "choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function", "function": {
                "name": "web_fetch", "arguments": json.dumps({"url": "https://pelda.hu"})}}]}}]},
        {"model": "claude-haiku-5-5", "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Kész."}}]}]

    async def kapu(payload, purpose=""):
        hivasok.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        return sor.pop(0), None, []

    async def eszkoz(nev, args, ctx, kor=None, *, modell="", vedett=False):
        return "Hosszú oldal. " * 1000

    async def olvaso(payload, *, purpose=""):
        return {"model": "claude-haiku-5-5", "choices": [{"message": {"content": "LÉNYEG"}}]}, None, []
    monkeypatch.setattr(fw._ctx, "llm_chat", kapu)
    monkeypatch.setattr(responder, "_execute_tool", eszkoz)
    monkeypatch.setattr(llm_kapu, "chat", olvaso)
    _run(responder.respond("szia", "42"))
    m = [x for x in hivasok[1]["messages"] if x.get("tool_call_id") == "t1"][0]
    assert m["content"].startswith("[KIVONAT") and m["content"].endswith("LÉNYEG")


def test_az_ugynok_hurka_is_kivonatol_es_a_szamlabol_fizet(monkeypatch):
    """A feladat-ügynök és a Nachtschicht közös hurka (nachtschicht._hurok): a nagy eredményből kivonat, az olvasó
    a futás keretéből fizet, a lapozó a lista végén van (az eszközlista a hurok alatt nem változik)."""
    import nachtschicht as N
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt-TITOK")
    kuldott = []
    valaszok = [
        {"model": "claude-sonnet-5-5", "koltes_usd": 0.01, "choices": [{"message": {"role": "assistant", "content": "",
         "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "web_fetch",
                                                                       "arguments": "{\"url\": \"https://x.hu\"}"}}]}}]},
        {"model": "claude-sonnet-5-5", "koltes_usd": 0.01, "choices": [{"message": {"role": "assistant",
                                                                                     "content": "{\"jelzes\": false}"}}]}]

    async def chat(payload, purpose=""):
        kuldott.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        return valaszok.pop(0), None, []

    async def olvaso(payload, *, purpose="", modell=None):
        assert purpose == kivonat.OLVASO_CEL
        return {"model": "claude-haiku-5-5", "koltes_usd": 0.0005, "choices": [{"message": {"content": "KIVONAT-SZÖVEG"}}]}, \
            None, []

    async def vegrehajto(nev, args, modell=""):
        return "Nagyon hosszú oldal. " * 500
    monkeypatch.setattr(llm_kapu, "chat", olvaso)
    szamla = N.Szamla(keret=1.0)
    eszkozok = [{"type": "function", "function": {"name": "web_fetch", "parameters": {"type": "object"}}}]
    szoveg, _ = _run(N._hurok([{"role": "system", "content": "FELADAT: x"}, {"role": "user", "content": "rajta"}],
                              eszkozok, purpose="ugynok/1/futas", szamla=szamla, chat=chat, vegrehajto=vegrehajto,
                              max_kor=4))
    assert szoveg == "{\"jelzes\": false}"
    assert [t["function"]["name"] for t in kuldott[0]["tools"]] == ["web_fetch", kivonat.LAPOZ_NEV]
    assert kuldott[0]["tools"] == kuldott[1]["tools"]
    eredmeny = [m for m in kuldott[1]["messages"] if m.get("role") == "tool"][0]["content"]
    assert eredmeny.startswith("[KIVONAT") and eredmeny.endswith("KIVONAT-SZÖVEG")
    assert szamla.usd == pytest.approx(0.0205) and szamla.hivasok == 3       # két modellhívás + az olvasó
