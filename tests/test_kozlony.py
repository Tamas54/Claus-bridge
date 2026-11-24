"""magyar_kozlony (kozlony.py) — a Közlöny keresője, a lapszám PDF-je, jogforrás + oldal, az azonos nevűek külön.

A minták a 11-24-i élő Pótor-kutatásból valók (Magyar Közlöny 2026/80, 3157. o.; MK 2019/143; HÉ 2011/33)."""
import asyncio
import json

import kozlony as K

KERESO_HTML = """<html><body>
<div class="panel-body panel-striped">
 <div class="journal-row">
  <div class="row" itemscope itemtype="http://schema.org/Newspaper">
   <div class="col-xs-6">
    <a href="https://magyarkozlony.hu/dokumentumok/be27/megtekintes#;=Pótor" class="text-success hidden-xs">
     <b itemprop="name">Magyar Közlöny 2026. évi 80. szám</b></a>
    <a class="text-success hidden visible-xs" href="https://magyarkozlony.hu/hivatalos-lapok/qG03/dokumentumok/be27/letoltes">
     <b itemprop="name">Magyar Közlöny 2026. évi 80. szám</b></a>
   </div>
   <div class="col-xs-3 text-lowercase text-sm"><meta itemprop="datePublished" content="2026-06-26"> 2026. június 26.</div>
  </div>
  <div class="row text-justify text-muted text-sm">
   <div class="col-xs-9"> ... Popa Gergelyt,
<em>Pótor</em> Józsefet,
Rágyanszki Györgyöt ... </div>
  </div>
 </div>
 <div class="journal-row">
  <div class="row">
   <div class="col-xs-6"><b itemprop="name">Magyar Közlöny 2019. évi 143. szám</b>
    <a href="https://magyarkozlony.hu/hivatalos-lapok/abcC/dokumentumok/2051/letoltes">PDF</a></div>
   <div class="col-xs-3"><meta itemprop="datePublished" content="2019-08-22"></div>
  </div>
  <div class="row text-justify text-muted text-sm"><div class="col-xs-9"> ... lelkészi szolgálata elismeréseként <em>Pótor</em> ... </div></div>
 </div>
</div></body></html>"""

OLDAL_2026 = ("M A G YA R K Ö Z L Ö N Y • 2026. évi 80. szám\t\n3157\n"
              "A miniszterelnök 63/2026. (VI. 26.) ME határozata\n"
              "helyettes államtitkári kinevezések módosulásának megállapításáról, valamint helyettes államtitkárok \n"
              "felmentéséről és helyettes államtitkárok kinevezéséről\n"
              "A kormányzati igazgatásról szóló 2018.\xa0évi CXXV. törvény 234. § (7) bekezdésében foglalt jogkörömben\n"
              "egyidejűleg\nPopa Gergelyt,\nPótor Józsefet,\nRágyanszki Györgyöt\n"
              "a Társadalmi Kapcsolatokért és Kultúráért Felelős Minisztérium helyettes államtitkárává\n"
              "– 2026. június 28-ai hatállyal –\nkinevezem.\n")

OLDAL_2019 = ("M A G YA R K Ö Z L Ö N Y • 2019. évi 143. szám\n5610\n"
              "A köztársasági elnök 331/2019. (VIII. 22.) KE határozata\nkitüntetés adományozásáról\n"
              "szolgálata elismeréseként\nPótor László, a Nyírmeggyesi Református Egyházközség lelkipásztora részére,\n")

#: A HÉ 2011/33: a számozatlan közlemény-fejléc az oldal tetején, előtte (az előző oldalon) egy számozott SZTNH-fejléc.
OLDAL_2011_ELOZO = ("H I V A T A L O S   É R T E S Í T Õ • 2011. évi 33. szám\n6212\n"
                    "A Szellemi Tulajdon Nemzeti Hivatala elnökének 4/2011. (V. 27.) SZTNH közleménye\n"
                    "a 20. Ifjúsági Tudományos és Innovációs Tehetségkutató Verseny kiállításán …\n")
OLDAL_2011 = ("H I V A T A L O S   É R T E S Í T Õ • 2011. évi 33. szám\n6213\n"
              "A közigazgatási és igazságügyi miniszter közleménye \n"
              "hatályát vesztett igazságügyi szakértõi igazolványokról\n"
              "Ottlakánné Kulcsár Erzsébet 6140. számú, Pótor József\nSZ 271 625. számú, Pribil Vencel Róbert\n")


def test_szemelynev_a_vezeteknevvel_keres():
    # a kereső szavanként VAGY-ol: a „József” bármilyen Józsefet hozna
    assert K.kereso_kifejezes("Pótor József") == "Pótor"
    assert K.kereso_kifejezes("dr. Kollai István") == "Kollai"
    assert K.kereso_kifejezes("civilügyi helyettes államtitkár") == "civilügyi helyettes államtitkár"
    assert "content=P%C3%B3tor" in K.kereso_url("Pótor József")


def test_talalatlista_a_kereso_htmlbol():
    t = K.talalatok_htmlbol(KERESO_HTML)
    assert [x["lap"] for x in t] == ["Magyar Közlöny 2026. évi 80. szám", "Magyar Közlöny 2019. évi 143. szám"]
    assert t[0]["datum"] == "2026-06-26"
    assert t[0]["pdf"].endswith("/dokumentumok/be27/letoltes")
    assert "Pótor Józsefet" in t[0]["kereso_reszlet"]


def test_kinevezes_jogforrassal_es_oldallal():
    r, masok = K.reszletek_oldalakbol(["elso oldal", OLDAL_2026], "Pótor József")
    assert masok == []
    assert len(r) == 1
    assert r[0]["oldal"] == "3157"
    assert r[0]["jogforras"] == "A miniszterelnök 63/2026. (VI. 26.) ME határozata"
    assert r[0]["targy"].startswith("helyettes államtitkári kinevezések")
    assert "kinevezéséről" in r[0]["targy"] and "kormányzati" not in r[0]["targy"]
    assert "Pótor Józsefet" in r[0]["szoveg"]


def test_azonos_vezeteknev_mas_szemely_kulon():
    r, masok = K.reszletek_oldalakbol([OLDAL_2019], "Pótor József")
    assert r == []
    assert masok == ["Pótor László"]


def test_szamozatlan_kozlemeny_fejlece_nyer():
    # 11-24 élőben: a találat a korábbi, számozott SZTNH-fejléc alá került — a számozatlan közlemény a helyes
    r, _ = K.reszletek_oldalakbol([OLDAL_2011_ELOZO, OLDAL_2011], "Pótor József")
    assert r[0]["jogforras"] == "A közigazgatási és igazságügyi miniszter közleménye"
    assert r[0]["targy"] == "hatályát vesztett igazságügyi szakértõi igazolványokról"
    assert r[0]["oldal"] == "6213"


def test_fejlec_az_elozo_oldalon():
    oldal_1 = "M A G YA R K Ö Z L Ö N Y • 2026. évi 80. szám\n3156\nA miniszterelnök 62/2026. (VI. 26.) ME határozata\nvalamiről\n"
    oldal_2 = "M A G YA R K Ö Z L Ö N Y • 2026. évi 80. szám\n3157\nfolytatás\nPótor Józsefet\n"
    r, _ = K.reszletek_oldalakbol([oldal_1, oldal_2], "Pótor József")
    assert r[0]["jogforras"] == "A miniszterelnök 62/2026. (VI. 26.) ME határozata"
    assert r[0]["oldal"] == "3157"


def test_oldalszam_lapfej_nelkul_pdf_oldal():
    assert K.oldalszam("valami más fejléc\n", 4) == "PDF 5. oldal"


def test_kereses_vegig(monkeypatch):
    pdfek = {"https://magyarkozlony.hu/hivatalos-lapok/qG03/dokumentumok/be27/letoltes": [OLDAL_2026],
             "https://magyarkozlony.hu/hivatalos-lapok/abcC/dokumentumok/2051/letoltes": [OLDAL_2019]}
    kert = []

    async def get(url):
        kert.append(url)
        if url.startswith(K.KERESO + "?"):
            return {"status": 200, "body": KERESO_HTML.encode()}
        return {"status": 200, "body": url.encode()}

    monkeypatch.setattr(K, "_get", get)
    monkeypatch.setattr(K, "_oldalak", lambda raw: pdfek[raw.decode()])
    out = asyncio.run(K.kereses("Pótor József"))
    assert out["kereso_kifejezes"] == "Pótor" and out["talalat"] == 2
    elso, masodik = out["lapszamok"]
    assert elso["reszletek"][0]["jogforras"].startswith("A miniszterelnök 63/2026.")
    assert masodik["reszletek"] == [] and masodik["mas_szemelyek"] == ["Pótor László"]
    assert len(kert) == 3
    json.dumps(out, ensure_ascii=False)


def test_kereses_nincs_talalat_igazolt_hiany(monkeypatch):
    async def get(url):
        return {"status": 200, "body": "<html>Nincs megjeleníthető tartalom.</html>".encode()}

    monkeypatch.setattr(K, "_get", get)
    out = asyncio.run(K.kereses("Nemlétező Személy"))
    assert out["talalat"] == 0 and "igazolt hiány" in out["megjegyzes"]


def test_kereses_kereso_hiba(monkeypatch):
    async def get(url):
        raise TimeoutError("lassú")

    monkeypatch.setattr(K, "_get", get)
    out = asyncio.run(K.kereses("Pótor József"))
    assert "nem válaszolt" in out["error"]


def test_bekotes_feldwebel_es_feladat_ugynok():
    import feladat_ugynok as F
    from feldwebel import eszkoz_ut
    from feldwebel.responder import TOOLS
    assert "magyar_kozlony" in {t["function"]["name"] for t in TOOLS}
    assert "magyar_kozlony" in F.FELDWEBEL_ENGEDETT
    assert F.engedett("magyar_kozlony", {"kifejezes": "x"}, sf=True) is None      # nyilvános adat: SF-ügynöknek is
    assert "magyar_kozlony" in eszkoz_ut.KATEGORIAK["jog"]["eszkozok"]
    assert "magyar_kozlony" in F.SZABALYOK
