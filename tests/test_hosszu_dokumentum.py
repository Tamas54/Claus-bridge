"""HOSSZÚ DOKUMENTUM (2026-11-23 este): a Bridge-ügynök a teljes dokumentumot lássa, a kontextus elárasztása nélkül.

Az élesben mért hiba: a Feldwebel (Sonnet) a robot-tanulmány (183 ezer karakter, 66 címsor) diasorát az olvasó-lépés
kivonatára építette, és az olvasó csak az első `OLVASO_MAX_KAR` (60 ezer) karaktert látta — „a 3.1 fejezet közepéig”.
A többi 46 db 4000 karakteres részben lett volna lapozható, fejezetre ugrás nélkül.

Itt hálózat nélkül, kitalált dokumentummal: a TÉRKÉP minden címsort tartalmaz (hely, hossz, rész, első mondat, számok),
hosszú dokumentumnál részleges olvasó nélkül; a lapozó fejezetre ugrik (sorszám, címrészlet, @hely), az egészet
fejezethatáron vágott részekben adja, és a kerete arányos a dokumentummal. A végén a valódi tanulmány (ha a gépen van):
`FABER_DIR` (alapból ~/faber) fajl_olvaso.olvas-ával, csak olvasva — mind a 66 címsor, és a 4.4 egy hívással."""
import asyncio
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kivonat  # noqa: E402


def _run(c):
    return asyncio.run(c)


async def _nincs_olvaso(payload, *, purpose=""):
    raise AssertionError("hosszú, tagolt dokumentumnál nem kell részleges olvasó")


FEJ = "[A Kommandant gépéről: ~/Downloads/kitalalt.docx — Word (.docx), 1,0 MB, módosítva 2026-11-23 20:00]"


def _dok(fejezetek: int = 10, alpont: int = 3, bekezdes: int = 4) -> str:
    """Kitalált tanulmány Markdown-címsorokkal (ahogy a futár a Word címsor-stílusait adja), ~130 ezer karakter."""
    s = [FEJ, "", "KITALÁLT ALAPÍTVÁNY", "Gépek és bérek", "# Vezetői összefoglaló",
         "Ez a tanulmány kitalált. A számai is kitaláltak, 1999 és 2030 között."]
    for i in range(1, fejezetek + 1):
        s.append(f"# {i}. A(z) {i}. fejezet témája")
        s.append(f"A(z) {i}. fejezet bevezető mondata. A második mondata. A harmadik már nem kell.")
        for j in range(1, alpont + 1):
            cim = "Ár és bér: mikor éri meg Magyarországon?" if (i, j) == (9, 2) else f"Részlet {i}.{j}"
            s.append(f"## {i}.{j} {cim}")
            if (i, j) == (9, 2):
                s.append("A humanoid ára 17,5 százalékkal esett 2026-ban, a bér 3 200 forint óránként.")
            for k in range(bekezdes):
                s.append(f"Bekezdés {i}.{j}.{k}: a gép ára {i}{j},{k} százalék, {1990 + i} óta {i * j} ezer forint. "
                         + "Töltelék mondat a hosszhoz. " * 34)
    return "\n".join(s)


DOK = _dok()
CIMEK = re.findall(r"(?m)^#{1,6} (.+)$", DOK)


# ── 1. a térkép: minden címsor, olvasó nélkül ────────────────────────────

def test_hosszu_dokumentum_terkepe_minden_cimsorral_olvaso_nelkul():
    assert len(DOK) > kivonat.OLVASO_MAX_KAR * 2 and len(CIMEK) == 41
    tar = kivonat.EredmenyTar(olvaso=_nincs_olvaso)
    ki = _run(tar.feldolgoz("helyi_fajl", {"ut": "~/Downloads/kitalalt.docx"}, DOK, feladat="diasor"))
    assert ki.startswith("[DOKUMENTUM-TÉRKÉP — a(z) `helyi_fajl` eredménye") and "41 címsorral" in ki
    assert tar.olvasasok == 0                                              # nincs részleges (félrevezető) olvasat
    for c in CIMEK:
        assert c in ki, c                                                  # a TELJES dokumentum minden címe
    sor = next(x for x in ki.splitlines() if "9.2 Ár és bér" in x)
    hely = DOK.index("## 9.2 Ár és bér")
    assert f"[@{hely} · " in sor and " · r" in sor                         # karakter-hely és rész
    i = ki.splitlines().index(sor)
    assert "17,5 százalékkal" in ki.splitlines()[i + 1] and "2026" in ki.splitlines()[i + 1]   # a fejezet számai
    assert "A(z) 10. fejezet bevezető mondata. A második mondata." in ki   # az első 1–2 mondat
    assert FEJ in ki                                                       # a futár fejléce (vágás, forrás) szó szerint
    assert len(ki) <= kivonat.TERKEP_MAX_KAR + 2_000                       # a kontextus nem árad el
    assert len(ki) < len(DOK) / 5
    assert 'fejezet="1.1"' in ki and f'resz=1…{len(tar._lapok["e1"])}' in ki
    assert "mondd ki pontosan, meddig olvastad" in ki


def test_terkep_keretben_marad_sok_cimsornal_is():
    sok = _dok(fejezetek=40, alpont=6, bekezdes=1)                         # 281 címsor
    tar = kivonat.EredmenyTar(olvaso=_nincs_olvaso)
    ki = _run(tar.feldolgoz("helyi_fajl", {}, sok))
    assert len(ki) <= kivonat.TERKEP_MAX_KAR + 2_000
    assert all(c in ki for c in re.findall(r"(?m)^#{1,6} (.+)$", sok))     # mind a 281 (a részletesség igazodik)
    # ha a puszta címlista sem férne el: a mélyebb szint marad ki, KIMONDVA, és a teljes lista lekérhető
    szuk = kivonat.terkep(sok, tar._fejek["e1"], tar._lapok["e1"], keret=3_000)
    assert len(szuk) <= 3_300 and "240 mélyebb alcím kihagyva" in szuk and 'fejezet="?"' in szuk
    assert "40. A(z) 40. fejezet témája" in szuk and "40.6 Részlet" not in szuk
    teljes = tar.lapoz({"azon": "e1", "fejezet": "?"})
    assert teljes.startswith("[e1 — a teljes címlista, 281 címsor]") and "40.6 Részlet 40.6" in teljes


# ── 2. a lapozó: fejezetre ugrás, egész fejezetenként, @hely ──────────────

def test_fejezetre_ugras_sorszammal_cimmel_es_hellyel():
    tar = kivonat.EredmenyTar(olvaso=_nincs_olvaso)
    _run(tar.feldolgoz("helyi_fajl", {}, DOK))
    a = DOK.index("## 9.2 Ár és bér")
    b = DOK.index("## 9.3 ")
    egy = tar.lapoz({"azon": "e1", "fejezet": "9.2"})
    fej, test = egy.split("\n", 1)
    assert fej.startswith("[e1 — fejezet: 9.2 Ár és bér: mikor éri meg Magyarországon?") and test == DOK[a:b]
    assert tar.lapoz({"azon": "e1", "fejezet": "ar es ber"}) == egy        # címrészlet, ékezet nélkül is
    assert tar.lapoz({"azon": "e1", "fejezet": "Mikor éri meg"}).split("\n", 1)[1] == DOK[a:b]
    egesz9 = tar.lapoz({"azon": "e1", "fejezet": "9."}).split("\n", 1)[1]  # a fejezet az alfejezeteivel
    assert egesz9.startswith("# 9. ") and "## 9.3 " in egesz9 and "# 10. " not in egesz9
    assert tar.lapoz({"azon": "e1", "fejezet": "1"}).split("\n", 2)[1].startswith("# 1. ")   # „1” ≠ „1.1”, ≠ „10.”
    hely = tar.lapoz({"azon": "e1", "fejezet": f"@{a + 5}"})                # a térkép @-értékéről (sor elejére igazít)
    assert hely.split("\n", 1)[1].startswith("## 9.2 Ár és bér") and 'Folytatás: fejezet="@' in hely.split("\n")[0]
    rossz = json.loads(tar.lapoz({"azon": "e1", "fejezet": "99.9"}))
    assert "nincs ilyen fejezet" in rossz["error"] and "9.2 Ár és bér: mikor éri meg Magyarországon?" in \
        rossz["fejezetek"]


def test_az_egesz_fejezethataron_vagva_egy_korben():
    tar = kivonat.EredmenyTar(olvaso=_nincs_olvaso)
    _run(tar.feldolgoz("helyi_fajl", {}, DOK))
    lapok = tar._lapok["e1"]
    assert len(lapok) <= -(-len(DOK) // kivonat.RESZ_KAR) + 2              # ~ a dokumentum / a rész mérete
    reszek = [tar.lapoz({"azon": "e1", "resz": r}) for r in range(1, len(lapok) + 1)]
    assert "".join(r.split("\n", 1)[1] for r in reszek) == DOK             # semmi nem vész el, nincs átfedés
    for r in reszek:
        test = r.split("\n", 1)[1]
        assert len(test) <= kivonat.RESZ_KAR
        assert test.startswith(("#", "[A Kommandant")), test[:60]          # fejezethatáron kezdődik
    assert reszek[0].startswith(f"[e1 — 1. rész / {len(lapok)}] 0–") and "(a szöveg vége)" in reszek[-1].split("\n")[0]


def test_lapozas_kerete_aranyos_a_dokumentummal():
    tar = kivonat.EredmenyTar(olvaso=_nincs_olvaso)
    _run(tar.feldolgoz("helyi_fajl", {}, DOK))
    n = len(tar._lapok["e1"])
    for _ in range(kivonat.LAPOZ_SZORZO):                                  # kétszer végig: rendben
        for r in range(1, n + 1):
            assert not tar.lapoz({"azon": "e1", "resz": r}).startswith('{"error"')
    tobb = [tar.lapoz({"azon": "e1", "resz": r}) for r in range(1, n + 1)]
    assert any("már 2-szer kiolvastad" in x for x in tobb)                 # a harmadik kör már nem


# ── 3. más alakok: számozott címsor (PDF), közepes dokumentum, tagolatlan szöveg, JSON ──

def test_szamozott_cimsorok_tartalomjegyzek_es_abrafelirat_nelkul():
    sz = ["Tartalom", "1. Bevezetés\t3", "1.1 Háttér\t4", "2. Eredmények ........ 9", ""]
    for i in range(1, 6):
        sz.append(f"{i}. Fejezet {i}")
        sz.append(f"{i}. ábra. Forrás: KSH ({2000 + i}).")
        sz.append("A szöveg. " * 600)
        sz.append(f"{i}.1 Alpont {i}")
        sz.append("Az alpont szövege, 12% és 5 millió forint. " * 30)
    t = "\n".join(sz)
    f = kivonat.cimsorok(t)
    assert [x["cim"] for x in f] == [c for i in range(1, 6) for c in (f"{i}. Fejezet {i}", f"{i}.1 Alpont {i}")]
    assert [x["szint"] for x in f[:2]] == [1, 2]
    assert kivonat.cimsorok("1. Egy\nszöveg\n2. Kettő\n") == []           # kevés cím: nincs térkép


def test_kozepes_dokumentum_olvasoval_es_terkeppel():
    kert = []

    async def olvaso(payload, *, purpose=""):
        kert.append(payload)
        return {"model": "claude-haiku-5-5", "choices": [{"message": {"content": "Kitalált tanulmány, 2026."}}]}, \
            None, []
    kozepes = _dok(fejezetek=3, alpont=2, bekezdes=4)
    assert kivonat.TERKEP_KUSZOB < len(kozepes) <= kivonat.OLVASO_MAX_KAR
    tar = kivonat.EredmenyTar(olvaso=olvaso)
    ki = _run(tar.feldolgoz("helyi_fajl", {}, kozepes))
    assert ki.startswith("[KIVONAT") and "CSAK az első" not in ki and len(kert) == 1   # az olvasó mindent látott
    assert "\n\n[TÉRKÉP — 10 címsor" in ki and "3.2 Részlet 3.2" in ki


def test_tagolatlan_hosszu_szoveg_olvaso_kimondva_es_szakasz_terkep():
    async def olvaso(payload, *, purpose=""):
        return {"model": "claude-haiku-5-5", "choices": [{"message": {"content": "Az eleje."}}]}, None, []
    t = "".join(f"A(z) {i}. mondat, {i % 97} százalék. " for i in range(5_000))
    assert len(t) > kivonat.OLVASO_MAX_KAR
    tar = kivonat.EredmenyTar(olvaso=olvaso)
    ki = _run(tar.feldolgoz("web_fetch", {"url": "https://pelda.hu/x"}, t))
    fej = ki.split("\n", 1)[0]
    assert f"CSAK az első {kivonat._ezres(kivonat.OLVASO_MAX_KAR)} karaktert látta" in fej
    assert "mondd ki, meddig jutottál" in fej
    terkep = ki.split("[SZAKASZ-TÉRKÉP", 1)[1]
    n = len(tar._lapok["e1"])
    assert terkep.count("\nr") == n and f"r{n} [@" in terkep and "százalék" in terkep


def test_json_eredmeny_szovegmezoje_kibontva():
    """web_fetch PDF: {"url", "content", …} — a térkép a `content` valódi sortöréseiből készül, a lapozó szöveget ad."""
    eredmeny = json.dumps({"url": "https://pelda.hu/t.pdf", "content": DOK, "length": len(DOK), "via": "http/pdf"},
                          ensure_ascii=False)
    tar = kivonat.EredmenyTar(olvaso=_nincs_olvaso)
    ki = _run(tar.feldolgoz("web_fetch", {"url": "https://pelda.hu/t.pdf"}, eredmeny))
    assert ki.startswith("[DOKUMENTUM-TÉRKÉP") and "Forrás: https://pelda.hu/t.pdf." in ki and "41 címsorral" in ki
    elso = tar.lapoz({"azon": "e1", "resz": 1}).split("\n", 1)[1]
    assert elso.startswith('[{"url": "https://pelda.hu/t.pdf", "length": ') and "\n# Vezetői összefoglaló\n" in elso
    assert tar.lapoz({"azon": "e1", "fejezet": "9.2"}).split("\n", 1)[1].startswith("## 9.2 Ár és bér")


# ── 4. a hurokban: a lapozót a hívásplafon nem fogja, az egész egy körben jöhet ──

def test_hurokban_terkep_fejezet_es_egesz_egy_korben_a_plafon_nem_fogja():
    import nachtschicht as N
    letoltes = []

    async def vegrehajto(nev, args, modell=""):
        letoltes.append(nev)
        if letoltes.count(nev) > 1:                                        # eszközönként EGY hívás (szigorú plafon)
            return json.dumps({"error": f"a(z) `{nev}` hívási kerete (1) elfogyott"})
        return DOK
    tar = kivonat.EredmenyTar(olvaso=_nincs_olvaso)
    n = len(kivonat.lap_hatarok(DOK, kivonat.cimsorok(DOK)))
    korok = [[("helyi_fajl", {"ut": "~/Downloads/kitalalt.docx"})],
             [(kivonat.LAPOZ_NEV, {"azon": "e1", "fejezet": "9.2"})],
             [(kivonat.LAPOZ_NEV, {"azon": "e1", "resz": r}) for r in range(1, n + 1)]]
    valaszok = [{"model": "claude-sonnet-5-5", "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"t{k}_{i}", "type": "function", "function": {"name": nev, "arguments": json.dumps(a)}}
        for i, (nev, a) in enumerate(h)]}}]} for k, h in enumerate(korok)]
    valaszok.append({"model": "claude-sonnet-5-5", "choices": [{"message": {"role": "assistant", "content": "KÉSZ"}}]})
    kuldott = []

    async def chat(payload, purpose=""):
        kuldott.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        return valaszok.pop(0), None, []
    eszkozok = [{"type": "function", "function": {"name": "helyi_fajl", "parameters": {"type": "object"}}}]
    szoveg, _ = _run(N._hurok([{"role": "system", "content": "FELADAT: diasor a tanulmányból"},
                               {"role": "user", "content": "x"}], eszkozok, purpose="ugynok/1/futas",
                              szamla=N.Szamla(keret=1.0), chat=chat, vegrehajto=vegrehajto, max_kor=6, tar=tar))
    assert szoveg == "KÉSZ" and letoltes == ["helyi_fajl"]
    eredm = [m["content"] for m in kuldott[-1]["messages"] if m.get("role") == "tool"]
    assert eredm[0].startswith("[DOKUMENTUM-TÉRKÉP") and all(c in eredm[0] for c in CIMEK)
    assert eredm[1].split("\n", 1)[1].startswith("## 9.2 Ár és bér")       # EGY hívással a fejezetre
    assert "".join(e.split("\n", 1)[1] for e in eredm[2:]) == DOK         # az egész, egy körben
    assert len(kuldott) == 4                                               # 3 eszközkör + a válasz


# ── 5. a VALÓDI tanulmány (csak olvasva; ha a gépen van) ──────────────────

TANULMANY = os.path.expanduser("~/Downloads/Mire_a_robot_megerkezik_HNA_v7.docx")


def _fajl_olvaso():
    d = os.path.expanduser(os.environ.get("FABER_DIR", "~/faber"))
    if not os.path.exists(TANULMANY) or not os.path.exists(os.path.join(d, "fajl_olvaso.py")):
        pytest.skip("a valódi tanulmány vagy a futár (FABER_DIR) nincs ezen a gépen")
    import importlib.util
    spec = importlib.util.spec_from_file_location("fajl_olvaso_valodi", os.path.join(d, "fajl_olvaso.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    if not hasattr(m, "_stilus_szintek"):
        pytest.skip("a FABER_DIR futárja még nem adja a Word címsorait (a faber fix/docx-cimsorok ága)")
    return m


def test_valodi_tanulmany_mind_a_66_fejezetcim_es_a_4_4_egy_hivassal():
    fo = _fajl_olvaso()
    elotte = os.stat(TANULMANY).st_mtime
    valodi, kij = fo.hely("~/Downloads/" + os.path.basename(TANULMANY))
    e = {"ut": kij, **fo.olvas(valodi)}
    szoveg = fo.fejlec(e) + e["szoveg"]                                    # ahogy a futár küldi (futar.py)
    assert os.stat(TANULMANY).st_mtime == elotte                           # csak olvas (a Downloads az nCore-seed)
    assert "</w:" not in szoveg and not e["vagva"]
    cimek = re.findall(r"(?m)^#{1,6} (.+)$", szoveg)
    assert len(cimek) == 66
    tar = kivonat.EredmenyTar(olvaso=_nincs_olvaso)
    ki = _run(tar.feldolgoz("helyi_fajl", {"ut": kij}, szoveg, feladat="diasor a tanulmányból"))
    assert ki.startswith("[DOKUMENTUM-TÉRKÉP") and "66 címsorral" in ki
    assert all(c in ki for c in cimek)                                     # mind a 66
    assert len(ki) <= kivonat.TERKEP_MAX_KAR + 2_000
    egy = tar.lapoz({"azon": "e1", "fejezet": "4.4"})                       # EGY hívással
    fej, test = egy.split("\n", 1)
    assert fej.startswith("[e1 — fejezet: 4.4 Ár és bér: mikor éri meg Magyarországon?")
    assert test.startswith("## 4.4 Ár és bér: mikor éri meg Magyarországon?") and "## 4.5" not in test
    assert len(test) > 1_500
