"""PYRAMID-VITA (2026-11-23): vita → parancs → végrehajtás — hálózat nélkül, hamis modellhívással.

A Kommandant: „KÉRDÉS MEGVITATÁSA!!! … és AZTÁN hajtják végre” — „AKAROK vitázni, megbeszélni, ÁLLÁSPONTOKAT látni.”
— „Van egy pont a vitában, amikor jóváhagyok, és utasítok.”

Amit ez a fájl őriz:
  * a sorrend és a forgás; minden szóló látja az előzőt; a körcímkék (R1 ÖTLETEK / R2 ÜTKÖZTETÉS / R3 ÖSSZEFÉSÜLÉS);
  * eltérő álláspont-kiosztás (mindenki mást képvisel, egy ördögügyvéd); a térkép szerkezete körönként (sorok,
    egyetértés, vita, kérdések a Kommandanthoz);
  * a Kommandant hozzászólása a következő szólók promptjának TETEJÉRE kerül, név szerint neki felelnek; lezárt vitánál
    új kört indít; a „várj rám” megáll, a Tovább és a hozzászólás továbbviszi;
  * a határozati javaslat szerkezete; PARANCS NÉLKÜL SEMMI NEM FUT; a parancs a vita közepén is működik; az utasítás
    felülírja a javaslatot (puszta „mehet” → a javaslat, LLM nélkül); a PARANCS-blokk és a tételállapotok; az
    eredmény visszaíródik a szálba;
  * a végrehajtók útja (ai_task / feladat_ugynok / Faber / action_drafts — a kifelé menő tétel valódi action_drafts
    sorba kerül); a leállítás és a plafonok (szólás-plafon, körszám, a 16k-s kimeneti plafon az sf_chat létráján);
    a kapcsoló KI állása; a kínai-őr: tiltott eszköz nincs a szóló eszközei között.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import llm_kapu
from pyramid import vita as pv

DDL = """
CREATE TABLE IF NOT EXISTS discussions (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, context TEXT DEFAULT '',
  status TEXT DEFAULT 'open', resolution TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS discussion_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, discussion_id INTEGER NOT NULL,
  instance TEXT NOT NULL, content TEXT NOT NULL, timestamp TEXT NOT NULL);
"""

R = ["kimi", "deepseek", "glm5"]


class Hamis:
    """A modellhívások helyett: a szóló a promptot naplózza és egyedi jelet ír; a moderátor a rendszerszöveg alapján
    a megfelelő JSON-t adja."""

    def __init__(self):
        self.szolok: list[tuple[str, str, str]] = []      # (ki, system, user)
        self.moderator_hivasok: list[tuple[str, str]] = []
        self.vegrehajtott: list[dict] = []
        self.ertesitesek: list[str] = []
        self.kerdes_tole: dict[str, str] = {}
        self.csonka = False
        self.parancs_feladatok = [{"mit": "Heti radar-brief megírása", "vegrehajto": "kutatas", "ki": "glm5",
                                   "hatarido": "hétfő", "merce": "1 oldal"}]
        self.vegrehajt_valasz = None
        self.faber_sor: list[dict] = []          # a Faber-figyelő egymás utáni válaszai (az utolsó ismétlődik)
        self.draft_sor: list[dict] = []
        self.faber_kerdesek: list[dict] = []

    async def faber_allapot(self, ref):
        self.faber_kerdesek.append(dict(ref))
        return self.faber_sor.pop(0) if len(self.faber_sor) > 1 else (self.faber_sor or [{"allapot": "fut"}])[0]

    async def draft_allapot(self, draft_id):
        return self.draft_sor.pop(0) if len(self.draft_sor) > 1 else (self.draft_sor or [{"allapot":
                                                                                           "jovahagyasra_var"}])[0]

    async def szolo(self, ki, system, user):
        n = len(self.szolok) + 1
        self.szolok.append((ki, system, user))
        allas = {"allaspont": f"{ki} álláspontja #{n}", "valtozas": "erősít", "fo_erv": f"{ki} érve",
                 "kerdes": self.kerdes_tole.get(ki, ""), "szavazat": ""}
        return {"szoveg": f"JEL-{n}-{ki}: ez a hozzászólásom.\n\n```json\n{json.dumps(allas, ensure_ascii=False)}\n```",
                "hiba": None, "jegyzetek": [], "eszkozok": [], "csonka": self.csonka, "modell": f"hamis/{ki}"}

    async def moderator(self, system, user, max_tokens=4000):
        self.moderator_hivasok.append((system, user))
        if "ÜTKÖZŐ álláspontot" in system:
            return json.dumps({"allaspontok": [{"cim": "Az 1-gyel kezdjünk", "allitas": "Előbb a radar."},
                                               {"cim": "A 2-vel kezdjünk", "allitas": "Előbb a csatornák."},
                                               {"cim": "Egyik sem: előbb mérjünk", "allitas": "Előbb adat."}]}), "kimi"
        if "ÁLLÁSPONT-TÉRKÉPET" in system:
            return json.dumps({"egyetertes": ["kell mérce"], "vita": ["radar vs. csatornák (Kimi vs. DeepSeek)"],
                               "kommandant": "a csatornákkal kezdene"}), "kimi"
        if "HATÁROZATI JAVASLATOT" in system:
            return ("Összefoglaló: a radar mellett döntöttünk.\n\n```json\n" + json.dumps({
                "feladatok": [{"mit": "Radar-prototípus X-re", "vegrehajto": "kutatas", "ki": "kimi",
                               "hatarido": "péntek", "merce": "10 jelölt"}],
                "kerdesek": ["Mennyi a keret?"]}, ensure_ascii=False) + "\n```"), "deepseek"
        if "PARANCSÁT" in system:
            return json.dumps({"feladatok": self.parancs_feladatok}, ensure_ascii=False), "kimi"
        return "", None

    async def vegrehajt(self, f, kontextus):
        self.vegrehajtott.append(f)
        if self.vegrehajt_valasz:
            return self.vegrehajt_valasz(f)
        return {"allapot": "kesz", "szoveg": f"kész: {f['mit']}", "link": "/x"}

    async def ai_task_allapot(self, task_id):
        return {"allapot": "kesz", "szoveg": f"ai_task #{task_id}: az eredmény", "link": f"/api/ai_tasks/{task_id}/export"}

    async def ertesit(self, szoveg):
        self.ertesitesek.append(szoveg)


@pytest.fixture
def h(get_db, monkeypatch):
    c = get_db()
    c.executescript(DDL)
    c.commit()
    c.close()
    hamis = Hamis()
    regi = dict(pv._DEPS)
    pv.beallit(get_db=get_db, szolo=hamis.szolo, moderator=hamis.moderator, vegrehajt=hamis.vegrehajt,
               ai_task_allapot=hamis.ai_task_allapot, faber_allapot=hamis.faber_allapot,
               draft_allapot=hamis.draft_allapot, ertesit=hamis.ertesit)
    monkeypatch.setenv("PYRAMID_VITA", "1")
    monkeypatch.delenv("PYRAMID_VITA_RESZTVEVOK", raising=False)
    monkeypatch.delenv("PYRAMID_VITA_KOROK", raising=False)
    monkeypatch.delenv("PYRAMID_VITA_INDULASKOR_MAX", raising=False)
    monkeypatch.delenv("PYRAMID_VITA_FOLYTAT_ORA", raising=False)
    monkeypatch.setattr(pv, "FIGYELO_S", 0.0)
    monkeypatch.setattr(pv, "FIGYELO_LASSU_S", 0.0)
    for tar in (pv._MOTOR, pv._FIGYELO, pv._PARANCS_FUT):
        tar.clear()
    hamis.get_db = get_db
    yield hamis
    pv._DEPS.clear()
    pv._DEPS.update(regi)


async def vegig(vid: int | None = None):
    """Megvárja a háttérfeladatokat (motor, parancs-feldolgozás, figyelők), amíg van."""
    for _ in range(200):
        tasks = [t for tar in (pv._MOTOR, pv._PARANCS_FUT, pv._FIGYELO) for t in list(tar.values()) if not t.done()]
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)


def run(coro):
    return asyncio.run(coro)


def allapot(vid):
    st, _ = pv.betolt(vid)
    return st


def szal(vid):
    return pv.bejegyzesek(vid)


# ── tiszta logika ─────────────────────────────────────────────────────────

def test_sorrend_korönkent_forog():
    assert pv.sorrend(R, 1) == ["kimi", "deepseek", "glm5"]
    assert pv.sorrend(R, 2) == ["deepseek", "glm5", "kimi"]
    assert pv.sorrend(R, 3) == ["glm5", "kimi", "deepseek"]
    assert pv.sorrend(R, 4) == pv.sorrend(R, 1)


def test_alap_resztvevok_a_harom_fo_rizsraketa(monkeypatch):
    monkeypatch.delenv("PYRAMID_VITA_RESZTVEVOK", raising=False)
    assert pv.resztvevok_alap() == ["kimi", "deepseek", "glm5"]
    monkeypatch.setenv("PYRAMID_VITA_RESZTVEVOK", "glm5, kimi")
    assert pv.resztvevok_alap() == ["glm5", "kimi"]


def test_eltero_allaspont_kiosztas_egy_ordogugyveddel():
    aa = [{"cim": "A"}, {"cim": "B"}, {"cim": "C"}]
    for vid in range(6):
        k = pv.kiosztas(R, aa, vid)
        assert sorted(x["allaspont"] for x in k.values()) == [0, 1, 2]          # mindenki MÁST képvisel
        ordog = [r for r, x in k.items() if x["ordogugyved"]]
        assert len(ordog) == 1 and k[ordog[0]]["allaspont"] == 2                 # az ördögügyvéd a legellentmondóbbat
    assert {pv.kiosztas(R, aa, v)[R[v % 3]]["ordogugyved"] for v in range(3)} == {True}   # vitánként forog
    assert pv.allaspont_szam(R) == 3 and pv.allaspont_szam(["kimi"]) == 2


def test_kor_tipusok_es_cimkek():
    st = pv._uj_allapot(R, 3)
    assert [pv.kor_cimke(st, k) for k in (1, 2, 3)] == ["R1 · ÖTLETEK", "R2 · ÜTKÖZTETÉS", "R3 · ÖSSZEFÉSÜLÉS"]
    st1 = pv._uj_allapot(R, 1)
    assert pv.kor_tipus(st1, 1) == "osszefesules"
    assert pv._uj_allapot(R, 99)["korok"] == 6                                    # körszám-plafon


def test_json_kinyeres_es_szoveg():
    obj, s = pv.json_kinyer('Szöveg itt.\n\n```json\n{"allaspont": "x", "valtozas": "váltok"}\n```')
    assert obj["allaspont"] == "x" and s == "Szöveg itt."
    assert pv.szolo_allaspont(obj)["valtozas"] == "vált"
    assert pv.json_kinyer("nincs json")[0] is None


def test_feladat_normalizalas():
    f = pv.feladat_norm({"mit": "Heti brief", "vegrehajto": "ütemezett", "ki": "GLM", "utemezes": "0 8 * * 1"}, R)
    assert (f["vegrehajto"], f["ki"], f["utemezes"]) == ("ismetlodo", "glm5", "0 8 * * 1")
    assert pv.feladat_norm({"mit": "Poszt az X-re", "vegrehajto": "tweet"}, R)["vegrehajto"] == "kifele"
    assert pv.feladat_norm({"mit": "Faber építsen eszközt", "vegrehajto": "kód"}, R)["vegrehajto"] == "kodolas"
    assert pv.feladat_norm({"mit": "Nézz utána", "vegrehajto": "??", "ki": "valaki"}, R)["ki"] == "kimi"
    assert pv.feladat_norm({"mit": ""}, R) is None
    assert pv.csak_jovahagyas("Mehet a javaslat szerint.") and not pv.csak_jovahagyas(
        "Jó, de a GLM írja meg a heti briefet hétfőre, a DeepSeek keressen 3 PR-ügynökséget")


# ── a vita ────────────────────────────────────────────────────────────────

def _uj(h, varj=False, korok=3):
    async def _f():
        out = pv.letrehoz("Gondolatmag-radar: mit csináljunk vele?", "Háttér.", "Nyitó álláspont.", "kommandant",
                          varj_ram=varj, korok=korok)
        await vegig()
        return out["vita_id"]
    return _f


def test_harom_kor_egymas_utan_mindenki_latja_az_elozot_es_parancs_nelkul_semmi_nem_fut(h):
    vid = run(_uj(h, varj=False)())
    st = allapot(vid)
    assert st["allapot"] == "parancsra_var"
    assert [k for k, _, _ in h.szolok] == ["kimi", "deepseek", "glm5", "deepseek", "glm5", "kimi",
                                           "glm5", "kimi", "deepseek"]                      # sorban, körönként forog
    for i, (_, _, user) in enumerate(h.szolok):                                             # mindenki látja az előzőt
        for j in range(1, i + 1):
            assert f"JEL-{j}-" in user
        assert f"JEL-{i + 1}-" not in user
    assert "R2 · ÜTKÖZTETÉS" in h.szolok[3][2] and "R3 · ÖSSZEFÉSÜLÉS" in h.szolok[6][2]
    # körcímkék a bejegyzéseken
    cimkek = [m.get("cimke") for m in st["bej"].values() if m.get("tipus") == "szolo"]
    assert cimkek == ["R1 · ÖTLETEK"] * 3 + ["R2 · ÜTKÖZTETÉS"] * 3 + ["R3 · ÖSSZEFÉSÜLÉS"] * 3
    assert any(e["content"].startswith("**R1 · ÖTLETEK**") for e in szal(vid))
    # a záró JSON nem kerül a buborékba, hanem a térképre
    assert not any("```json" in e["content"] for e in szal(vid))
    # parancs nélkül semmi nem fut
    assert h.vegrehajtott == []
    assert any("döntésre vár" in m.lower() or "Döntésre vár" in m for m in h.ertesitesek)


def test_eltero_allaspontok_a_promptban(h):
    vid = run(_uj(h, varj=True)())
    st = allapot(vid)
    assert len(st["allaspontok"]) == 3
    kepviselt = {}
    for ki, system, _ in h.szolok[:3]:
        assert "KÉPVISELED" in system
        kepviselt[ki] = system.split("álláspontot KÉPVISELED")[0].rsplit("„", 1)[1]
        assert ("ÖRDÖGÜGYVÉD" in system) == st["kiosztas"][ki]["ordogugyved"]
        assert "„Egyetértek” csak ellenérvvel vagy új elemmel" in system
        assert "álláspontot váltasz — de indokold" in system
    assert len(set(kepviselt.values())) == 3                                            # három különböző álláspont
    assert any(e["instance"] == "moderator" and "AZ ÁLLÁSPONTOK" in e["content"] for e in szal(vid))


def test_terkep_szerkezete_korönkent(h):
    h.kerdes_tole = {"deepseek": "Mennyi a havi keret?"}
    vid = run(_uj(h, varj=False)())
    st = allapot(vid)
    assert [t["kor"] for t in st["terkep"]] == [1, 2, 3]
    for t in st["terkep"]:
        assert set(t["sorok"]) == set(R)
        for s in t["sorok"].values():
            assert s["allaspont"] and s["valtozas"] == "erősít" and s["fo_erv"]
        assert t["egyetertes"] == ["kell mérce"] and t["vita"]
        assert {"ki": "deepseek", "kerdes": "Mennyi a havi keret?"} in t["kerdesek"]
    n = pv.nezet(st, vid)
    assert n["terkep"][-1]["cimke"] == "R3 · ÖSSZEFÉSÜLÉS" and n["allapot_szoveg"].startswith("döntésre vár")
    assert any("Kérdés a Kommandanthoz" in e["content"] for e in szal(vid))


def test_varj_ram_megall_a_kor_utan_es_a_tovabb_viszi(h):
    vid = run(_uj(h, varj=True)())
    st = allapot(vid)
    assert st["allapot"] == "var_ra" and len(h.szolok) == 3 and st["kor"] == 2
    assert "várlak" in pv.allapot_szoveg(st)
    assert any("R1 köre kész" in m for m in h.ertesitesek)

    async def _tovabb():
        assert pv.tovabb(vid)["ok"]
        await vegig()
    run(_tovabb())
    assert len(h.szolok) == 6 and allapot(vid)["allapot"] == "var_ra"


def test_kommandant_hozzaszolasa_a_kovetkezo_promptok_tetejen_es_tovabbviszi(h):
    vid = run(_uj(h, varj=True)())

    async def _szol():
        out = pv.hozzaszol(vid, "Szerintem a csatornákkal kezdjünk, a radar ráér.", "kommandant")
        assert out["ok"] and out["allapot"] == "fut"
        await vegig()
    run(_szol())
    r2 = h.szolok[3:6]
    assert len(r2) == 3
    for _, _, user in r2:                         # az R2 minden szólója elsőként neki felel
        assert user.startswith("⚠️ A KOMMANDANT SZÓLT:")
        assert "a csatornákkal kezdjünk" in user.split("VITA #")[0]
    st = allapot(vid)
    assert st["terkep"][1]["sorok"]["kommandant"]["allaspont"] == "a csatornákkal kezdene"
    assert "kommandant" not in st["terkep"][0]["sorok"]
    assert st["kommandant_uj"] == []              # a kör végén lezárul — az R3 már nem kapja kiemelve

    async def _tovabb():
        pv.tovabb(vid)
        await vegig()
    run(_tovabb())
    assert not h.szolok[6][2].startswith("⚠️ A KOMMANDANT SZÓLT")
    r3 = allapot(vid)["terkep"][2]["sorok"]["kommandant"]   # az álláspontja a térképen marad
    assert r3["allaspont"] == "a csatornákkal kezdene" and r3["valtozas"] == "tart"


def test_lezart_vitan_a_hozzaszolas_uj_kort_indit(h):
    vid = run(_uj(h, varj=False)())
    assert allapot(vid)["allapot"] == "parancsra_var"

    async def _szol():
        pv.hozzaszol(vid, "És ha előbb a jogi oldalt nézzük?", "kommandant")
        await vegig()
    run(_szol())
    st = allapot(vid)
    assert len(h.szolok) == 12 and st["szakasz"] == 2 and st["allapot"] == "parancsra_var"
    assert all("R4 · FOLYTATÁS" in u for _, _, u in h.szolok[9:12])
    assert h.szolok[9][2].startswith("⚠️ A KOMMANDANT SZÓLT")
    assert h.vegrehajtott == []


def test_mas_hozzaszolasa_nem_inditja_ujra(h):
    vid = run(_uj(h, varj=False)())

    async def _szol():
        pv.hozzaszol(vid, "web-claus megjegyzése", "web-claus")
        await vegig()
    run(_szol())
    assert len(h.szolok) == 9 and allapot(vid)["allapot"] == "parancsra_var"


def test_hatarozati_javaslat_szerkezete(h):
    vid = run(_uj(h, varj=False)())
    j = allapot(vid)["javaslat"]
    assert j["osszefoglalo"].startswith("Összefoglaló")
    assert j["feladatok"][0] | {} and {k for k in ("mit", "vegrehajto", "ki", "hatarido", "merce")} <= set(j["feladatok"][0])
    assert j["feladatok"][0]["vegrehajto"] == "kutatas" and j["kerdesek"] == ["Mennyi a keret?"]
    e = [x for x in szal(vid) if "HATÁROZATI JAVASLAT" in x["content"]]
    assert e and "magától semmi nem fut" in e[0]["content"] and "Nyitott kérdések" in e[0]["content"]


# ── parancs → végrehajtás ────────────────────────────────────────────────

def test_parancs_a_vita_kozepen_megallitja_a_vitat_es_vegrehajt(h):
    vid = run(_uj(h, varj=True)())                       # az R1 után vár — a vita még nem ért véget
    n_elotte = len(h.szolok)

    async def _p():
        out = pv.parancs(vid, "Elég, a GLM írja meg a heti radar-briefet hétfőre.")
        assert out["ok"] and out["parancs"] == 1
        await vegig()
    run(_p())
    st = allapot(vid)
    assert len(h.szolok) == n_elotte                      # nem szólt több vitázó
    assert [f["mit"] for f in h.vegrehajtott] == ["Heti radar-brief megírása"]
    assert st["allapot"] == "kesz" and st["parancsok"][0]["allapot"] == "kesz"
    sor = h.get_db().execute("SELECT status FROM discussions WHERE id = ?", (vid,)).fetchone()
    assert sor["status"] == "resolved"
    # a parancs prompt: az ő szavai mérvadók; javaslat még nincs, a szál a nyersanyag
    parancs_user = next(u for s, u in h.moderator_hivasok if "PARANCSÁT" in s)
    assert "A KOMMANDANT PARANCSA (mérvadó)" in parancs_user and "Határozati javaslat még nincs" in parancs_user


def test_parancs_futo_vitaban_is_mukodik(h):
    async def _f():
        vid = pv.letrehoz("Téma", "", "", "kommandant", varj_ram=False)["vita_id"]
        out = pv.parancs(vid, "Mehet, csináljátok: a GLM írja meg a briefet.")   # a motor még el sem indult
        assert out["ok"]
        await vegig()
        return vid
    vid = run(_f())
    assert allapot(vid)["allapot"] == "kesz" and len(h.vegrehajtott) == 1
    assert len(h.szolok) == 0


def test_az_utasitas_felulirja_a_javaslatot(h):
    vid = run(_uj(h, varj=False)())
    assert allapot(vid)["javaslat"]["feladatok"][0]["mit"] == "Radar-prototípus X-re"
    h.parancs_feladatok = [{"mit": "PR-ügynökségek: 3 jelölt", "vegrehajto": "kutatas", "ki": "deepseek"}]

    async def _p():
        pv.parancs(vid, "Nem a radar: a DeepSeek keressen 3 PR-ügynökséget.")
        await vegig()
    run(_p())
    assert [f["mit"] for f in h.vegrehajtott] == ["PR-ügynökségek: 3 jelölt"]          # NEM a javaslat tétele
    u = next(u for s, u in h.moderator_hivasok if "PARANCSÁT" in s)
    assert u.index("A KOMMANDANT PARANCSA (mérvadó)") < u.index("A MODERÁTOR JAVASLATA (nyersanyag)")
    sys_ = next(s for s, _ in h.moderator_hivasok if "PARANCSÁT" in s)
    assert "ha ő mást mond, az övé" in sys_


def test_puszta_mehet_a_javaslatot_hajtja_vegre_llm_nelkul(h):
    vid = run(_uj(h, varj=False)())
    elotte = len(h.moderator_hivasok)

    async def _p():
        pv.parancs(vid, "Mehet a javaslat szerint.")
        await vegig()
    run(_p())
    assert len(h.moderator_hivasok) == elotte                                          # nincs parser-hívás
    assert [f["mit"] for f in h.vegrehajtott] == ["Radar-prototípus X-re"]
    assert allapot(vid)["parancsok"][0]["forras"] == "javaslat"


def test_parancs_blokk_es_tetelallapotok_visszairas(h):
    vid = run(_uj(h, varj=False)())
    h.parancs_feladatok = [
        {"mit": "Elemzés a csatornákról", "vegrehajto": "kutatas", "ki": "kimi"},
        {"mit": "Heti radar-brief", "vegrehajto": "ismetlodo", "utemezes": "0 8 * * 1"},
        {"mit": "Radar-eszköz", "vegrehajto": "kodolas"},
        {"mit": "Poszt az eredményről", "vegrehajto": "kifele", "tervezet": "Elindult a radar."},
    ]

    def valasz(f):
        return {"kutatas": {"allapot": "fut", "ref": {"ai_task": 77}, "szoveg": "ai_task #77"},
                "ismetlodo": {"allapot": "kesz", "szoveg": "feladat-ügynök", "nezet": {"tipus": "ugynok", "id": "ugynok:5"}},
                "kodolas": {"allapot": "hibas", "szoveg": "a Faber-relay nem elérhető"},
                "kifele": {"allapot": "jovahagyasra_var", "szoveg": "tweet-tervezet #3"}}[f["vegrehajto"]]
    h.vegrehajt_valasz = valasz

    async def _p():
        pv.parancs(vid, "Csináljátok így: elemzés, heti brief, eszköz, poszt.")
        await vegig()
    run(_p())
    st = allapot(vid)
    p = st["parancsok"][0]
    assert [f["allapot"] for f in p["feladatok"]] == ["kesz", "kesz", "hibas", "jovahagyasra_var"]
    assert p["feladatok"][0]["link"] == "/api/ai_tasks/77/export"                     # a figyelő visszaírta
    assert p["feladatok"][1]["nezet"] == {"tipus": "ugynok", "id": "ugynok:5"}
    tartalmak = [e["content"] for e in szal(vid)]
    blokk = next(c for c in tartalmak if c.startswith("⚡ **PARANCS #1**"))
    assert "1. [kutatás · Kimi] Elemzés a csatornákról" in blokk
    assert any(c.startswith("✅ PARANCS #1 · 1. feladat kész") and "ai_task #77: az eredmény" in c for c in tartalmak)
    assert any(c.startswith("❌ PARANCS #1 · 3. feladat hibás") and "Faber-relay" in c for c in tartalmak)
    assert any(c.startswith("🕓 PARANCS #1 · 4. feladat jóváhagyásra vár") for c in tartalmak)
    assert st["allapot"] == "kesz"
    assert any("PARANCS #1 lefutott" in m for m in h.ertesitesek)


def _parancs_egy_tetellel(h, vegrehajto, valasz, szoveg="Csináld meg."):
    vid = run(_uj(h, varj=False)())
    h.parancs_feladatok = [{"mit": "Radar-eszköz" if vegrehajto == "kodolas" else "Poszt a radarról",
                            "vegrehajto": vegrehajto, "tervezet": "Elindult a radar."}]
    h.vegrehajt_valasz = lambda f: valasz(vid, f)

    async def _p():
        pv.parancs(vid, szoveg)
        await vegig()
    run(_p())
    return vid


def test_a_faber_eredmenye_visszairodik_a_szalba(h):
    """Eddig csak az került a szálba, hogy a kívánság továbbment. Most a kérelem sorsa is: a kérelem száma bekerül a
    tételbe, a jóváhagyásra várás 🕓, az élesítés ✅ — és amíg a Faber dolgozik, a tétel „fut” (a parancs nem kész)."""
    h.faber_sor = [{"allapot": "fut", "szoveg": "a Faber még nem vette fel"},
                   {"allapot": "fut", "ref": {"kerelem": 12}, "szoveg": "Faber #12: dolgozik"},
                   {"allapot": "fut", "ref": {"kerelem": 12}, "szoveg": "Faber #12: dolgozik"},
                   {"allapot": "jovahagyasra_var", "ref": {"kerelem": 12},
                    "szoveg": "Faber #12: kipróbálta, a tesztek zöldek — jóváhagyásra vár"},
                   {"allapot": "jovahagyasra_var", "ref": {"kerelem": 12}, "szoveg": "Faber #12: jóváhagyásra vár"},
                   {"allapot": "kesz", "ref": {"kerelem": 12}, "szoveg": "Faber #12: élesítve ✅ (Claus Bridge)"}]

    def valasz(vid, f):
        return {"allapot": "fut", "bejegyez": True, "szoveg": "kívánság a Fabernek (relay #55)",
                "ref": {"faber": 55, "vita": vid, "parancs": 1, "tetel": 1}}
    vid = _parancs_egy_tetellel(h, "kodolas", valasz)
    st = allapot(vid)
    f = st["parancsok"][0]["feladatok"][0]
    assert f["allapot"] == "kesz" and f["ref"] == {"faber": 55, "vita": vid, "parancs": 1, "tetel": 1, "kerelem": 12}
    assert h.faber_kerdesek[0] == {"faber": 55, "vita": vid, "parancs": 1, "tetel": 1}
    assert h.faber_kerdesek[-1]["kerelem"] == 12                    # a megtalált kérelemszámmal kérdez tovább
    tartalmak = [e["content"] for e in szal(vid)]
    sorok = [c for c in tartalmak if c.startswith(("⏳", "🕓", "✅", "❌", "✖")) and "PARANCS #1 · 1." in c]
    assert [c[0] for c in sorok] == ["⏳", "🕓", "✅"], sorok        # továbbment → jóváhagyásra vár → élesítve
    assert "Faber #12: kipróbálta" in sorok[1] and "Faber #12: élesítve" in sorok[2]
    assert st["allapot"] == "kesz"


def test_a_faber_elutasitas_es_bukas_is_visszairodik(h):
    h.faber_sor = [{"allapot": "elutasitva", "ref": {"kerelem": 3}, "szoveg": "Faber #3: elvetetted"}]

    def valasz(vid, f):
        return {"allapot": "fut", "bejegyez": True, "szoveg": "relay #9", "ref": {"faber": 9, "vita": vid,
                                                                                  "parancs": 1, "tetel": 1}}
    vid = _parancs_egy_tetellel(h, "kodolas", valasz)
    f = allapot(vid)["parancsok"][0]["feladatok"][0]
    assert f["allapot"] == "elutasitva"
    assert any(c.startswith("✖ PARANCS #1 · 1. feladat elutasítva") and "Faber #3: elvetetted" in c
               for c in (e["content"] for e in szal(vid)))
    assert any("1 elutasítva" in m for m in h.ertesitesek)
    h.faber_sor = [{"allapot": "hibas", "ref": {"kerelem": 4}, "szoveg": "Faber #4: nem sikerült — a tesztek pirosak"}]
    vid = _parancs_egy_tetellel(h, "kodolas", valasz)
    assert any(c.startswith("❌ PARANCS #1 · 1. feladat hibás") and "tesztek pirosak" in c
               for c in (e["content"] for e in szal(vid)))


def test_a_tweet_tervezet_utolagos_jovahagyasa_visszairodik(h):
    h.draft_sor = [{"allapot": "jovahagyasra_var", "szoveg": "tweet-tervezet #3 — jóváhagyásra vár"},
                   {"allapot": "jovahagyasra_var", "szoveg": "tweet-tervezet #3 — jóváhagyásra vár"},
                   {"allapot": "kesz", "szoveg": "tweet-tervezet #3 kiposztolva: https://x.com/i/status/1",
                    "link": "https://x.com/i/status/1", "link_szoveg": "a poszt"}]

    def valasz(vid, f):
        return {"allapot": "jovahagyasra_var", "ref": {"draft": 3}, "szoveg": "tweet-tervezet #3 (17 karakter)"}
    vid = _parancs_egy_tetellel(h, "kifele", valasz)
    st = allapot(vid)
    f = st["parancsok"][0]["feladatok"][0]
    assert f["allapot"] == "kesz" and f["link"] == "https://x.com/i/status/1"
    sorok = [e["content"] for e in szal(vid) if "PARANCS #1 · 1. feladat" in e["content"]]
    assert [c[0] for c in sorok] == ["🕓", "✅"]                      # a 🕓 egyszer, aztán a döntés
    assert "kiposztolva" in sorok[1] and "[a poszt](https://x.com/i/status/1)" in sorok[1]
    # elvetés
    h.draft_sor = [{"allapot": "elutasitva", "szoveg": "tweet-tervezet #3: elvetetted, nem ment ki"}]
    vid = _parancs_egy_tetellel(h, "kifele", valasz)
    assert allapot(vid)["parancsok"][0]["feladatok"][0]["allapot"] == "elutasitva"


def test_induleskor_a_futo_vita_es_a_figyelok_maguktol_folytatodnak(h, monkeypatch):
    """Újraindulás után eddig csak a vita kabinbeli megnyitása folytatta. Most induláskor (`ujraeleszt_mind`): a
    „fut” vita motorja, a futó / jóváhagyásra váró tételek figyelői — a kapcsoló és a plafonok szerint."""
    # három „fut” vita (mintha a Bridge a futásuk közben állt volna le) + egy leállított + egy végrehajtás alatti
    async def _letrehoz():
        ids = [pv.letrehoz(f"Téma {k}", "", "", "kommandant", varj_ram=False, indit=False)["vita_id"] for k in range(4)]
        return ids
    ids = run(_letrehoz())
    pv.leallit(ids[0])
    regi = ids[1]                    # régóta nem frissült (a `modosit` a frissítés idejét átírná — közvetlenül állítjuk)
    conn = h.get_db()
    try:
        r = conn.execute("SELECT resolution FROM discussions WHERE id = ?", (regi,)).fetchone()["resolution"]
        st = json.loads(r)
        st["frissitve"] = "2026-11-19T00:00:00+00:00"
        conn.execute("UPDATE discussions SET resolution = ? WHERE id = ?", (json.dumps(st), regi))
        conn.commit()
    finally:
        conn.close()
    # egy végrehajtás alatti vita: Faber-tétel „fut”, tweet-tétel jóváhagyásra vár (régi, ref nélküli alak is)
    vh = pv.letrehoz("Végrehajtás", "", "", "kommandant", varj_ram=False, indit=False)["vita_id"]

    def _vegrehajtas(st, conn):
        st["allapot"] = "vegrehajtas"
        st["parancsok"] = [{"n": 1, "szoveg": "x", "allapot": "fut", "feladatok": [
            {"mit": "eszköz", "vegrehajto": "kodolas", "allapot": "fut", "eredmeny": "relay #55",
             "ref": {"faber": 55, "vita": vh, "parancs": 1, "tetel": 1}},
            {"mit": "poszt", "vegrehajto": "kifele", "allapot": "jovahagyasra_var",
             "eredmeny": "tweet-tervezet #7 (40 karakter) — a munkapadon hagyod jóvá", "ref": None},
            {"mit": "elemzés", "vegrehajto": "kutatas", "allapot": "kesz", "ref": {"ai_task": 3}}]}]
    pv.modosit(vh, _vegrehajtas)
    h.faber_sor = [{"allapot": "kesz", "ref": {"kerelem": 8}, "szoveg": "Faber #8: élesítve"}]
    h.draft_sor = [{"allapot": "elutasitva", "szoveg": "tweet-tervezet #7: elvetetted"}]
    indult = []
    eredeti = pv.inditas
    monkeypatch.setattr(pv, "inditas", lambda vid: indult.append(vid) or eredeti(vid))

    async def _indul():
        ki = pv.ujraeleszt_mind()
        await vegig()
        return ki
    monkeypatch.setenv("PYRAMID_VITA_INDULASKOR_MAX", "1")
    ki = run(_indul())
    assert ki["motor"] == [ids[3]], ki                               # a legfrissebb, egy (plafon)
    assert indult == [ids[3]]
    okok = {k["vita"]: k["ok"] for k in ki["kihagyva"]}
    assert "legfeljebb 1" in okok[ids[2]] and "órája nem frissült" in okok[regi] and ids[0] not in okok
    assert ki["figyelo"] == 2                                        # a Faber-tétel és a (régi alakú) tweet-tétel
    st = allapot(vh)
    assert [f["allapot"] for f in st["parancsok"][0]["feladatok"]] == ["kesz", "elutasitva", "kesz"]
    assert st["parancsok"][0]["feladatok"][1]["ref"] == {"draft": 7}
    assert len(h.szolok) == 9 and allapot(ids[3])["allapot"] == "parancsra_var"   # a folytatott vita végigment
    # KI állásban semmi
    monkeypatch.setenv("PYRAMID_VITA", "0")
    assert pv.ujraeleszt_mind() == {"motor": [], "figyelo": 0, "parancs": 0, "kihagyva": []}


def test_megallit_mas_szal_hurkan_futo_motort_szalbiztosan(h):
    """Az induláskor folytatott vita a cron-szál hurkán fut, a „Leállítom” a webes hurokból jön."""
    import threading
    kesz = threading.Event()
    allj = {}

    def masik():
        loop = asyncio.new_event_loop()

        async def orok():
            await asyncio.sleep(3600)
        t = loop.create_task(orok())
        pv._MOTOR[999] = t
        allj["t"] = t
        kesz.set()
        try:
            loop.run_until_complete(t)
        except asyncio.CancelledError:
            allj["megszakadt"] = True
        finally:
            loop.close()
    th = threading.Thread(target=masik)
    th.start()
    kesz.wait(5)

    async def _fo():
        return pv.megallit(999)
    assert run(_fo()) is True
    th.join(5)
    assert allj.get("megszakadt") is True and not th.is_alive()
    pv._MOTOR.pop(999, None)


def test_ertelmezhetetlen_parancs_kimondva_es_visszaall(h):
    vid = run(_uj(h, varj=False)())
    h.parancs_feladatok = []

    async def _p():
        pv.parancs(vid, "Hát izé.")
        await vegig()
    run(_p())
    st = allapot(vid)
    assert st["allapot"] == "parancsra_var" and h.vegrehajtott == []
    assert any("nem tudtam feladatokká bontani" in e["content"] for e in szal(vid))


# ── leállítás, plafonok, kapcsoló ─────────────────────────────────────────

def test_leallitas_es_folytatas(h):
    async def _f():
        vid = pv.letrehoz("Téma", "", "", "kommandant", varj_ram=False)["vita_id"]
        assert pv.leallit(vid)["allapot"] == "leallitva"           # a motor még az első lépés előtt áll
        await vegig()
        return vid
    vid = run(_f())
    assert h.szolok == [] and allapot(vid)["allapot"] == "leallitva"

    async def _t():
        pv.tovabb(vid)
        await vegig()
    run(_t())
    assert len(h.szolok) == 9 and allapot(vid)["allapot"] == "parancsra_var"


def test_szolas_plafon(h, monkeypatch):
    monkeypatch.setattr(pv, "MAX_SZOLAS", 2)
    vid = run(_uj(h, varj=False)())
    assert len(h.szolok) == 2 and allapot(vid)["allapot"] == "parancsra_var"
    assert any("szólás-plafont" in e["content"] for e in szal(vid))


def test_csonka_valasz_kimondva(h):
    h.csonka = True
    vid = run(_uj(h, varj=True)())
    e = next(x for x in szal(vid) if x["instance"] == "kimi" and x["content"].startswith("**R1"))
    assert "CSONKA" in e["content"]
    assert allapot(vid)["bej"][str(e["id"])]["csonka"] is True


def test_kapcsolo_ki_semmi_nem_indul(h, monkeypatch):
    vid = run(_uj(h, varj=True)())
    n = len(h.szolok)
    monkeypatch.setenv("PYRAMID_VITA", "0")
    assert not pv.letrehoz("Új", "")["ok"]
    assert not pv.parancs(vid, "Mehet.")["ok"]

    async def _f():
        pv.hozzaszol(vid, "Szólok.", "kommandant")
        assert not pv.inditas(vid)
        await vegig()
    run(_f())
    assert len(h.szolok) == n and h.vegrehajtott == []


def test_szolo_hiba_utan_megall_kimondva(h):
    async def rossz(ki, system, user):
        h.szolok.append((ki, system, user))
        return {"szoveg": "", "hiba": {"code": "payment"}, "jegyzetek": [], "eszkozok": []}
    pv._DEPS["szolo"] = rossz
    vid = run(_uj(h, varj=False)())
    st = allapot(vid)
    assert st["allapot"] == "leallitva" and st["hiba"] == "szolok_hibaja"
    assert any("most nem szólt (payment)" in e["content"] for e in szal(vid))


# ── a Feldwebel eszköze (Telegram) és a kínai-őr ─────────────────────────

def test_feldwebel_eszkoz_parancs_es_hozzaszolas(h):
    async def _f():
        out = json.loads(await pv.feldwebel_hiv({"muvelet": "indit", "tema": "Radar", "kontextus": "x"}))
        assert out["ok"]
        await vegig()
        vid = out["vita_id"]
        out = json.loads(await pv.feldwebel_hiv({"muvelet": "parancs", "szoveg": "Jó, a GLM írja meg."}))
        assert out["ok"] and out["vita_id"] == vid                          # vita_id nélkül a legutóbbi
        await vegig()
        return vid
    vid = run(_f())
    assert allapot(vid)["allapot"] == "kesz" and len(h.vegrehajtott) == 1


def test_sf_or_a_vita_eszkozre():
    import futar_kliens
    assert futar_kliens.sf_fele("vita", {"muvelet": "parancs"})
    assert futar_kliens.sf_fele("vita", {"muvelet": "indit"})
    assert not futar_kliens.sf_fele("vita", {"muvelet": "allapot"})


def test_feldwebel_szandek_a_vita_kategoria():
    from feldwebel import eszkoz_ut
    # a vita a feladat-ügynökök kategóriájában (külön kategória a betöltő leírásával minden kör stabil előtagját
    # növelné; az ai_ugynokok előtöltése minden „Kimi, …” üzenetet drágítana)
    assert "feladat_ugynokok" in eszkoz_ut.szandek("vitassák meg a radart")
    assert "feladat_ugynokok" in eszkoz_ut.szandek("parancs: a GLM írja meg")
    assert "feladat_ugynokok" not in eszkoz_ut.szandek("melyik vitamint szedjem")
    assert eszkoz_ut.kategoria("vita") == "feladat_ugynokok"
    assert "PYRAMID-VITA" in eszkoz_ut.KATEGORIAK["feladat_ugynokok"]["utmutato"]


# ── a server.py bekötése: szóló, plafon, végrehajtók ─────────────────────

@pytest.fixture
def srv(monkeypatch):
    import server

    async def nincs_kulso():
        return []
    monkeypatch.setattr(server, "_sf_kulso_eszkozok", nincs_kulso)
    return server


def test_szolo_eszkozei_kinai_or_plafon_es_eszkozkeret(srv, monkeypatch):
    hivasok = []
    dispatch = []

    async def hamis_sf(payload, *, purpose="", **kw):
        hivasok.append({"purpose": purpose, "max_tokens": payload.get("max_tokens"),
                        "tools": [t["function"]["name"] for t in payload.get("tools") or []],
                        "plafon": llm_kapu.KERET_PLAFON.get()})
        if payload.get("tools"):           # mindig 3 eszközt kér, amíg engedik
            return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"t{len(hivasok)}{i}", "type": "function",
                 "function": {"name": "web_search", "arguments": json.dumps({"query": f"q{i}"})}} for i in range(3)]},
                "finish_reason": "tool_calls"}], "model": "hamis"}, None, []
        return {"choices": [{"message": {"content": "Kész."}, "finish_reason": "stop"}], "model": "hamis"}, None, []

    async def hamis_dispatch(nev, args):
        dispatch.append(nev)
        return "eredmény"
    monkeypatch.setattr(srv, "sf_chat", hamis_sf)
    monkeypatch.setattr(srv, "_dispatch_subagent_tool", hamis_dispatch)
    r = run(srv._pyramid_szolo("kimi", "rendszer", "kérdés"))
    assert r["szoveg"] == "Kész." and r["hiba"] is None
    assert len(dispatch) <= pv.ESZKOZ_MAX                                    # eszközhívás-plafon
    for h_ in hivasok:
        assert h_["purpose"] == "vita/kimi" and h_["plafon"] == pv.PLAFON == 16000
        assert h_["max_tokens"] == pv.MAX_TOKENS
        assert not set(h_["tools"]) & srv.SUBAGENT_TILTOTT                   # a kínai-őr: tiltott eszköz nincs
        for tiltott in ("tudasbazis", "search_memory", "read_memory", "gmail_search", "naptar", "send_email",
                        "ai_task", "faber"):
            assert tiltott not in h_["tools"]
    assert llm_kapu.KERET_PLAFON.get() is None                              # a plafon nem szivárog ki


def test_sf_chat_letraja_16k_folott_nem_duplaz(srv, monkeypatch):
    kert = []

    async def csonka(client, body, key=""):
        kert.append(body["max_tokens"])
        return {"choices": [{"message": {"content": "félbe"}, "finish_reason": "length"}], "model": body["model"]}, None
    monkeypatch.setattr(srv, "_sf_post_once", csonka)
    monkeypatch.setattr(srv, "SILICONFLOW_API_KEY", "k")
    monkeypatch.setattr(srv, "_SF_LEARNED_BUDGET", {"zai-org/GLM-5.3": 131000})
    monkeypatch.setattr(srv, "_sf_budget_remember", lambda *a, **k: None)

    async def _f():
        with llm_kapu.keret_plafon(16000):
            return await srv.sf_chat({"model": "zai-org/GLM-5.3", "messages": [{"role": "user", "content": "x"}],
                                      "max_tokens": 6000}, purpose="vita/glm5", failover=False)
    data, err, notes = run(_f())
    assert max(kert) == 16000 and err is None
    assert any("PLAFONJÁN (16000) is csonkolt" in n for n in notes)
    # plafon nélkül a megtanult keret is csak a bővítés tetejéig (32k) emel — a régi 131k-s indulás a #594 létrája volt
    kert.clear()
    run(srv.sf_chat({"model": "zai-org/GLM-5.3", "messages": [{"role": "user", "content": "x"}], "max_tokens": 6000},
                    purpose="x", failover=False))
    assert kert[0] == llm_kapu.BOVITES_PLAFON and max(kert) == llm_kapu.BOVITES_PLAFON


def test_llm_kapu_plafon_a_forditasban():
    payload = {"model": "x", "messages": [{"role": "user", "content": "x"}], "max_tokens": 60000}
    with llm_kapu.keret_plafon(16000):
        kw, _ = llm_kapu.fordit_keres(payload, modell="claude-haiku-5-5", effort="low", fallbacks=False)
    assert kw["max_tokens"] == 16000
    kw, _ = llm_kapu.fordit_keres(payload, modell="claude-haiku-5-5", effort="low", fallbacks=False)
    assert kw["max_tokens"] == 60000


def test_vegrehajtok_utvonala(srv, monkeypatch):
    import faber_relay
    import feladat_ugynok
    hivas = {}

    async def hamis_ai_task(**kw):
        hivas["ai_task"] = kw
        return json.dumps({"status": "task_dispatched", "task_id": 91})

    def hamis_fu(muvelet, args, **kw):
        hivas["feladat_ugynok"] = (muvelet, args, kw.get("ki"))
        return {"ok": True, "ugynok": {"id": "ugynok:4", "nev": args["nev"], "utemezes_szoveg": "hétfőnként 8:00"}}

    def hamis_relay(conn, token, tipus, adat, ts):
        hivas["faber"] = (tipus, json.loads(adat))
        return 55
    monkeypatch.setattr(srv, "ai_task", hamis_ai_task)
    monkeypatch.setattr(feladat_ugynok, "vegrehajt", hamis_fu)
    monkeypatch.setattr(faber_relay, "tovabbit", hamis_relay)
    alap = {"vita_id": 5, "parancs": 1, "hatarido": "hétfő", "merce": "1 oldal", "tervezet": "", "nev": "", "utemezes": ""}

    r = run(srv._pyramid_vegrehajt({**alap, "tetel": 1, "mit": "Elemzés", "vegrehajto": "kutatas", "ki": "glm5"}, "K"))
    assert r["allapot"] == "fut" and r["ref"] == {"ai_task": 91}
    assert json.loads(hivas["ai_task"]["agent_tasks"]) == {"glm5": {"prompt": hivas["ai_task"]["description"]}}
    assert "Mérce" in hivas["ai_task"]["description"]

    r = run(srv._pyramid_vegrehajt({**alap, "tetel": 2, "mit": "Heti radar-brief", "vegrehajto": "ismetlodo",
                                    "ki": "", "utemezes": "0 8 * * 1"}, "K"))
    assert r["allapot"] == "kesz" and hivas["feladat_ugynok"][0] == "indit"
    assert hivas["feladat_ugynok"][1]["utemezes"] == "0 8 * * 1" and hivas["feladat_ugynok"][2] == "kommandant"

    monkeypatch.setattr(srv, "TELEGRAM_BOT_TOKEN", "1:x")
    r = run(srv._pyramid_vegrehajt({**alap, "tetel": 3, "mit": "Radar-eszköz", "vegrehajto": "kodolas", "ki": ""}, "K"))
    # a tétel „fut”, amíg a Faber dolgozik: a figyelő a kérelem sorsát visszaírja (a ref köti a tételhez)
    assert r["allapot"] == "fut" and hivas["faber"][0] == "szandek"
    assert r["ref"] == {"faber": 55, "vita": 5, "parancs": 1, "tetel": 3} and pv.figyelt_fajta(r["ref"]) == "faber"
    assert hivas["faber"][1]["szandek"] == "kivansag" and "PYRAMID-VITA" in hivas["faber"][1]["szoveg"]
    assert hivas["faber"][1]["szoveg"].startswith("KÍVÁNSÁG A " + pv.faber_jel(5, 1, 3))

    r = run(srv._pyramid_vegrehajt({**alap, "tetel": 4, "mit": "Levél a partnernek", "vegrehajto": "kifele", "ki": "",
                                    "tervezet": "Kedves…"}, "K"))
    assert r["allapot"] == "kezi"


def test_kifele_tetel_az_action_drafts_ba_kerul_es_nem_posztol(srv):
    r = run(srv._pyramid_vegrehajt({"vita_id": 5, "parancs": 1, "tetel": 1, "mit": "Poszt az X-re",
                                    "vegrehajto": "kifele", "ki": "", "hatarido": "", "merce": "", "nev": "",
                                    "utemezes": "", "tervezet": "Elindult a gondolatmag-radar."}, ""))
    assert r["allapot"] == "jovahagyasra_var" and pv.figyelt_fajta(r["ref"]) == "draft"
    conn = srv.get_db()
    try:
        sor = conn.execute("SELECT id, kind, status, source_message_id FROM action_drafts WHERE source_message_id = "
                           "'vita:5' ORDER BY id DESC").fetchone()
    finally:
        conn.close()
    assert (sor["kind"], sor["status"]) == ("tweet", "pending") and r["ref"] == {"draft": sor["id"]}
    # a figyelő ebből olvassa az utólagos döntést
    did = sor["id"]
    assert run(srv._pyramid_draft_allapot(did))["allapot"] == "jovahagyasra_var"
    conn = srv.get_db()
    try:
        conn.execute("UPDATE action_drafts SET status = 'created', payload_json = ? WHERE id = ?",
                     (json.dumps({"url": "https://x.com/i/status/9"}), did))
        conn.commit()
    finally:
        conn.close()
    a = run(srv._pyramid_draft_allapot(did))
    assert a["allapot"] == "kesz" and a["link"] == "https://x.com/i/status/9" and "kiposztolva" in a["szoveg"]
    conn = srv.get_db()
    try:
        conn.execute("UPDATE action_drafts SET status = 'rejected' WHERE id = ?", (did,))
        conn.commit()
    finally:
        conn.close()
    assert run(srv._pyramid_draft_allapot(did))["allapot"] == "elutasitva"


def test_faber_allapot_a_bridge_uzeneteibol(srv):
    """A Faber a messages táblába ír (sender='faber'): „megkaptam” (a kívánság első 300 jelével), faber:esemeny
    minden állapotváltáskor, faber:kapu jóváhagyásnál. A kérelem számát a kívánság elején álló jel köti a tételhez."""
    from datetime import datetime, timedelta, timezone

    def ir(subject, message, mikor=None):
        conn = srv.get_db()
        try:
            cur = conn.execute("INSERT INTO messages (timestamp, sender, recipient, subject, message) VALUES (?, ?, ?, ?, ?)",
                               ((mikor or datetime.now(timezone.utc)).isoformat(), "faber", "kommandant", subject,
                                message if isinstance(message, str) else json.dumps(message, ensure_ascii=False)))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
    conn = srv.get_db()
    try:
        mid = conn.execute("INSERT INTO messages (timestamp, sender, recipient, subject, message) VALUES "
                           "('x', 'kommandant-telegram', 'faber', 'faber:szandek', '{}')").lastrowid
        conn.commit()
    finally:
        conn.close()
    ref = {"faber": mid, "vita": 41, "parancs": 2, "tetel": 3}
    assert run(srv._pyramid_faber_allapot(ref))["allapot"] == "fut"              # még nem vette fel
    ir("faber:uzenet", "🔧 Faber #77: megkaptam. Tanulom (bridge).\n„KÍVÁNSÁG A #41 PYRAMID-VITA PARANCS #2-ÁBÓL "
                       "(1. tétel; a Kommandant parancsa): más tétel”")
    ir("faber:uzenet", "🔧 Faber #78: megkaptam. Tanulom (bridge).\n„KÍVÁNSÁG A " + srv.pyramid_vita.faber_jel(41, 2, 3)
       + "; a Kommandant parancsa): radar-eszköz”")
    ir("faber:esemeny", {"id": 78, "allapot": "dolgozik", "szoveg": ""})
    r = run(srv._pyramid_faber_allapot(ref))
    assert r["allapot"] == "fut" and r["ref"] == {"kerelem": 78}
    ir("faber:esemeny", {"id": 77, "allapot": "elesitve", "szoveg": ""})          # a másik tétel kérelme: nem ez
    ir("faber:esemeny", {"id": 78, "allapot": "jovahagyasra_var", "szoveg": ""})
    ir("faber:kapu", {"id": 78, "repo": "bridge", "kivansag": "…", "jelentes": "Megírtam a radart, 3 teszt.", "kod": "k"})
    r = run(srv._pyramid_faber_allapot({**ref, "kerelem": 78}))
    assert r["allapot"] == "jovahagyasra_var" and "Megírtam a radart" in r["szoveg"] and r["szoveg"].startswith("Faber #78")
    # élesítve: az esemény után a Faber saját szava
    ir("faber:esemeny", {"id": 78, "allapot": "elesitve", "szoveg": ""})
    r = run(srv._pyramid_faber_allapot({**ref, "kerelem": 78}))
    assert r["allapot"] == "fut"                                   # a Faber üzenete még úton (friss esemény)
    ir("faber:uzenet", "🔧 Faber #78: élesítve ✅ (Claus Bridge, feltöltve, a deploy indul).")
    r = run(srv._pyramid_faber_allapot({**ref, "kerelem": 78}))
    assert r["allapot"] == "kesz" and r["szoveg"].startswith("Faber #78: élesítve ✅ (Claus Bridge")
    # elvetés, üzenet nélkül, régi eseménnyel: a sablonszöveg
    regen = datetime.now(timezone.utc) - timedelta(minutes=10)
    ir("faber:esemeny", {"id": 78, "allapot": "elvetve", "szoveg": ""}, regen)
    r = run(srv._pyramid_faber_allapot({**ref, "kerelem": 78}))
    assert (r["allapot"], r["szoveg"]) == ("elutasitva", "Faber #78: elvetetted")
    # a kapu-kártyából is megtalálja (ha a „megkaptam” elmaradt)
    ref2 = {"faber": mid, "vita": 41, "parancs": 2, "tetel": 4}
    ir("faber:kapu", {"id": 90, "kivansag": "KÍVÁNSÁG A " + srv.pyramid_vita.faber_jel(41, 2, 4) + "; …", "jelentes": "j"})
    assert run(srv._pyramid_faber_allapot(ref2))["ref"] == {"kerelem": 90}


def test_ai_task_allapot_figyelonek(srv):
    conn = srv.get_db()
    try:
        cur = conn.execute("INSERT INTO ai_tasks (title, description, assigned_by, status, created_at) "
                           "VALUES ('t', 'd', 'pyramid-vita#1', 'running', 'x')")
        tid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    assert run(srv._pyramid_ai_task_allapot(tid))["allapot"] == "fut"
    conn = srv.get_db()
    try:
        conn.execute("INSERT INTO ai_task_results (task_id, agent, role, content, timestamp) VALUES (?, 'szintézis', "
                     "'K', 'A végeredmény szövege.', 'x')", (tid,))
        conn.execute("UPDATE ai_tasks SET status = 'completed' WHERE id = ?", (tid,))
        conn.commit()
    finally:
        conn.close()
    r = run(srv._pyramid_ai_task_allapot(tid))
    assert r["allapot"] == "kesz" and "A végeredmény" in r["szoveg"] and r["link"].endswith(f"/{tid}/export")


def test_regi_egykoros_vita_csak_a_harom_fo_modellel(srv, monkeypatch):
    modellek = []

    async def hamis_sf(payload, *, purpose="", **kw):
        modellek.append(payload["model"])
        return {"choices": [{"message": {"content": "Nincs hozzáfűznivalóm."}}]}, None, []
    monkeypatch.setattr(srv, "sf_chat", hamis_sf)
    monkeypatch.setattr(srv, "SILICONFLOW_API_KEY", "k")
    run(srv._ai_auto_discuss(1, "téma", "[x]: y"))
    assert sorted(modellek) == sorted(srv.SILICONFLOW_MODELS[k] for k in ("kimi", "deepseek", "glm5"))


def test_start_discussion_mod_pyramid_es_add_to_discussion(srv, h, monkeypatch):
    monkeypatch.setattr(srv, "get_db", h.get_db)          # a vita a teszt-DB-ben

    async def _f():
        out = json.loads(await srv.start_discussion("Radar", "Nyitó", "Kontextus", instance="cli-claus", mod="pyramid"))
        assert out["mode"] == "pyramid"
        vid = out["discussion_id"]
        await vegig()
        out = json.loads(await srv.add_to_discussion(vid, "A csatornákkal kezdjünk.", instance="kommandant"))
        assert out["mode"] == "pyramid" and out["ok"]
        await vegig()
        return vid
    vid = run(_f())
    st = allapot(vid)
    assert st and len(h.szolok) == 6 and st["allapot"] == "var_ra"     # alapból „várj rám”: R1, aztán R2 a szólásra
    assert h.szolok[3][2].startswith("⚠️ A KOMMANDANT SZÓLT")


def test_regi_vita_atalakitasa_pyramidda(srv, h, monkeypatch):
    c = h.get_db()
    c.execute("INSERT INTO discussions (topic, status, created_by, created_at) VALUES ('Gondolatmag-radar', 'open', "
              "'web-claus', 'x')")
    c.execute("INSERT INTO discussion_entries (discussion_id, instance, content, timestamp) VALUES (1, 'kimi', "
              "'régi vélemény', 'x')")
    c.commit()
    c.close()
    monkeypatch.setattr(srv, "get_db", h.get_db)

    async def _f():
        out = json.loads(await srv.start_discussion("", "", instance="cli-claus", mod="pyramid", discussion_id=1))
        assert out["discussion_id"] == 1
        await vegig()
    run(_f())
    assert allapot(1)["allapot"] == "var_ra" and "régi vélemény" in h.szolok[0][2]


# ── a kabin ───────────────────────────────────────────────────────────────

def _kabin_app(get_db):
    from fastmcp import FastMCP
    import ui_kabin as ui

    async def semmi(*a, **k):
        return None
    deps = {"get_db": get_db, "bot_token": lambda: "1:T", "chat_id": lambda: "42", "telegram_kuld": semmi,
            "asszisztens": semmi, "ugynokok": lambda: {}, "llm_allapot": lambda: {}, "jelenlet": lambda rows: {},
            "draft_dont": semmi}
    mcp = FastMCP("ui-teszt")
    ui.regisztral(mcp, deps)
    return mcp.http_app(stateless_http=True), ui


async def _keres(app, ui, method, path, **kw):
    import httpx
    fej = {"cookie": f"{ui.SUTI_NEV}={ui.suti_ertek(ui.kulcs('1:T'))}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, path, headers=fej, **kw)


def test_kabin_uj_vita_hozzaszolas_parancs_es_gombok(h):
    app, ui = _kabin_app(h.get_db)

    async def _f():
        r = await _keres(app, ui, "POST", "/api/ui/vita/uj", json={"tema": "Radar", "kontextus": "x", "korok": 2})
        vid = r.json()["vita_id"]
        await vegig()
        v = (await _keres(app, ui, "GET", f"/api/ui/vita/{vid}")).json()
        assert v["pyramid"]["allapot"] == "var_ra" and v["pyramid"]["varj_ram"] is True
        assert any((b["meta"] or {}).get("cimke") == "R1 · ÖTLETEK" for b in v["bejegyzesek"])
        assert v["pyramid"]["terkep"][0]["sorok"]
        r = await _keres(app, ui, "POST", f"/api/ui/vita/{vid}/muvelet", json={"muvelet": "varj_ram", "ertek": False})
        assert r.json()["varj_ram"] is False
        r = await _keres(app, ui, "POST", f"/api/ui/vita/{vid}", json={"szoveg": "A csatornákkal kezdjünk."})
        assert r.status_code == 200
        await vegig()
        v = (await _keres(app, ui, "GET", f"/api/ui/vita/{vid}")).json()
        assert v["pyramid"]["allapot"] == "parancsra_var" and v["pyramid"]["javaslat"]
        r = await _keres(app, ui, "POST", f"/api/ui/vita/{vid}", json={"szoveg": "Mehet.", "parancs": True})
        assert r.status_code == 200
        await vegig()
        v = (await _keres(app, ui, "GET", f"/api/ui/vita/{vid}")).json()
        assert v["pyramid"]["parancsok"][0]["feladatok"][0]["allapot"] == "kesz"
        assert (await _keres(app, ui, "POST", f"/api/ui/vita/{vid}/muvelet", json={"muvelet": "x"})).status_code == 400
        r = await _keres(app, ui, "POST", f"/api/ui/vita/{vid}/muvelet", json={"muvelet": "leallit"})
        assert r.status_code == 200
        return vid
    run(_f())


def test_kabin_regi_vitan_a_hozzaszolas_pyramidda_alakit(h):
    c = h.get_db()
    c.execute("INSERT INTO discussions (topic, status, created_by, created_at) VALUES ('Umbau', 'open', 'web-claus', 'x')")
    c.commit()
    c.close()
    app, ui = _kabin_app(h.get_db)

    async def _f():
        r = await _keres(app, ui, "POST", "/api/ui/vita/1", json={"szoveg": "Vitassátok meg."})
        assert r.status_code == 200 and r.json()["pyramid"] is not None
        assert r.json()["bejegyzesek"][-1]["szoveg"] == "Vitassátok meg."
        await vegig()
    run(_f())
    assert len(h.szolok) == 3 and h.szolok[0][2].startswith("⚠️ A KOMMANDANT SZÓLT")


def test_kabin_parancs_kapcsolo_ki(h, monkeypatch):
    c = h.get_db()
    c.execute("INSERT INTO discussions (topic, status, created_by, created_at) VALUES ('Umbau', 'open', 'web-claus', 'x')")
    c.commit()
    c.close()
    monkeypatch.setenv("PYRAMID_VITA", "0")
    app, ui = _kabin_app(h.get_db)

    async def _f():
        r = await _keres(app, ui, "POST", "/api/ui/vita/1", json={"szoveg": "Mehet.", "parancs": True})
        assert r.status_code == 400
        r = await _keres(app, ui, "POST", "/api/ui/vita/1", json={"szoveg": "Csak hozzászólok."})
        assert r.status_code == 200 and r.json()["pyramid"] is None
    run(_f())
    assert h.szolok == [] and h.vegrehajtott == []


def test_puszta_jovahagyas_felismerese():
    for s in ("Mehet.", "Mehet a javaslat szerint, minden pontja.", "Igen, jóváhagyom.", "OK, így legyen."):
        assert pv.csak_jovahagyas(s), s
    for s in ("Csináljátok így: elemzés, heti brief, eszköz, poszt.", "Jó, a GLM írja meg.", "Mehet, de a radar nélkül.",
              ""):
        assert not pv.csak_jovahagyas(s), s


def test_read_discussion_olvashato_pyramid_osszefoglalo(srv, h, monkeypatch):
    monkeypatch.setattr(srv, "get_db", h.get_db)
    vid = run(_uj(h, varj=False)())
    out = json.loads(run(srv.read_discussion(vid, caller="cli-claus")))
    assert out["pyramid"]["pyramid"] is True and "HATÁROZATI JAVASLAT" in out["pyramid"]["javaslat"]
    assert out["resolution"] is None                                   # a gépi JSON nem megy ki nyersen


def test_resolve_discussion_nem_irja_felul_az_allapotot(srv, h, monkeypatch):
    monkeypatch.setattr(srv, "get_db", h.get_db)
    monkeypatch.setattr(srv, "memoria_ir", lambda *a, **k: None)
    vid = run(_uj(h, varj=True)())
    run(srv.resolve_discussion(vid, "Kézzel lezárva: a radarral megyünk.", instance="cli-claus"))
    st = allapot(vid)
    assert st and st["allapot"] == "kesz" and st["lezaras"]["szoveg"].startswith("Kézzel lezárva")
