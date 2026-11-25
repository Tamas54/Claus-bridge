"""HOPSZ-RIASZTÓ (hopsz.py, 2026-11-23): az ügynök képesség-hiányából automatikus, aláírt Faber-kérelem.

1. A MAI NÉGY PÉLDA → kérelem: a sérült .docx („no content type for partname”), a túl széles Studio-tiltás
   tudásbázis-körben, a Feldwebel „Nem tudom, mi a HOG”-ja, a diasor-eszköz hiánya.
2. ZAJ → nincs kérelem: 402, időtúllépés, DNS, X-403, 429/5xx, a kínai-ügynök tiltás, a keret, kifelé ható művelet;
   a valódi visszakérdezés („Nem tudom, mit küldjek”) és a fizetős cikk sem.
3. DUPLIKÁCIÓ (7 nap) és NAPI PLAFON (HOPSZ_NAPI_MAX, alapból 3).
4. TITOK nem szivárog (bot-token, kulcsok, DSN-jelszó, a környezet titka), tudásbázis-körben az argumentum és a válasz sem.
5. KAPCSOLÓ: HOPSZ_AUTO=0 → semmi; alapból BE.
6. BEKÖTÉS: a feladat-ügynök futása és a Feldwebel köre a hopsz-on át megy.

Futtatás: ../../../.venv/bin/python -m pytest -q tests/test_hopsz.py"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import faber_relay
import feladat_ugynok as F
import futar_kliens
import hopsz as H
from server import memoria_ir

TOKEN = "123456789:AAHopszTesztBotTokenNagyonTitkos_xyz"
NAP = 24 * 3600
T0 = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc).timestamp()

SHARED_MEMORY = ("CREATE TABLE IF NOT EXISTS shared_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, "
                 "value TEXT NOT NULL, category TEXT DEFAULT 'general', tags TEXT DEFAULT '', updated_by TEXT NOT NULL, "
                 "created_at TEXT NOT NULL, updated_at TEXT NOT NULL); "
                 "CREATE UNIQUE INDEX IF NOT EXISTS idx_sm_key ON shared_memory(key);")

# a mai négy példa hibaszövege (a valódi alakban)
DOCX = json.dumps({"filename": "Mire_a_robot_megerkezik_HNA_v7.docx", "mime_type": "application/vnd.openxmlformats",
                   "size": 1234567, "content": "(File parse error: \"no content type for partname "
                                               "'/word/media/photo_baines.jpg' in [Content_Types].xml\")"})
#: a 11-23 délutáni, túl széles tiltás szövege (7e61ee3 előtt): a saját Studiónkat is tiltotta tudásbázis-körben
REGI_SF_FELE_HIBA = ("ebben a beszélgetésben a Kommandant tudásbázisából van tartalom, ami csak Claude-modellnek mehet "
                     "ki — SiliconFlow-ügynöknek (Kimi, DeepSeek, GLM), saját AI-t használó szolgáltatásnak (Echolot, "
                     "Engine, Studio), feladatba, receptbe, Bridge-üzenetbe vagy nyilvános kommentbe nem adom tovább")
HOG_VALASZ = "Nem tudom, mi a HOG. Elmondanád, mire gondolsz?"
DIASOR_VALASZ = ("Nincs diasor-eszközöm, ezért csak szöveges diatervet tudok adni:\n1. dia — A fő állítás\n"
                 "2. dia — A tét")


@pytest.fixture
def bridge(get_db, monkeypatch):
    c = get_db()
    c.executescript(SHARED_MEMORY)
    c.commit()
    c.close()
    monkeypatch.setenv("HOPSZ_AUTO", "1")
    monkeypatch.delenv("HOPSZ_NAPI_MAX", raising=False)
    elozo = dict(H._DEPS)
    H.beallit(get_db=get_db, memoria_ir=memoria_ir, bot_token=lambda: TOKEN)
    yield get_db
    H._DEPS.clear()
    H._DEPS.update(elozo)


def _kerelmek(get_db) -> list[dict]:
    c = get_db()
    try:
        sorok = c.execute("SELECT sender, subject, message FROM messages WHERE recipient = 'faber' ORDER BY id").fetchall()
    finally:
        c.close()
    out = []
    for r in sorok:
        t = faber_relay.ellenoriz(faber_relay.kulcs(TOKEN), r["message"])
        assert t is not None and t["tipus"] == "szandek" and r["subject"] == "faber:szandek"     # aláírt, a meglévő úton
        out.append({**json.loads(t["adat"]), "_nyers": r["message"]})
    return out


def _docx_kor(fajl="photo_baines.jpg"):
    k = H.Kor("a(z) „Bemutató” feladat-ügynök", "Ebből a tanulmányból készíts 12–15 diás bemutatót a szerdai "
                                                "Market-megbeszélésre.")
    k.eszkoz("gmail_search", {"query": "robot tanulmány"}, json.dumps({"talalat": 1}))
    k.eszkoz("read_gmail_attachment", {"message_id": "18c9f0a1b2", "attachment_index": 0},
             DOCX.replace("photo_baines.jpg", fajl))
    return k


# ══ 1. A MAI NÉGY PÉLDA ══════════════════════════════════════════════════════

def test_1_serult_docx_kerelem(bridge):
    mid = _docx_kor().vege("A mellékletet nem sikerült elolvasni.", most=T0)
    assert mid
    [k] = _kerelmek(bridge)
    assert k["szandek"] == "kivansag" and k["repo"] == "bridge" and k["forras"] == "hopsz" and k["kerelem"] is None
    assert k["eszkoz"] == "read_gmail_attachment" and len(k["alairas"]) == 16
    sz = k["szoveg"]
    assert sz.startswith("HOPSZ (automatikus): read_gmail_attachment nem ment: (File parse error: ")
    assert "no content type for partname" in sz
    assert "A feladat: Ebből a tanulmányból készíts 12–15 diás bemutatót" in sz
    assert "Amit az ügynök próbált: gmail_search(query=robot tanulmány); read_gmail_attachment(" in sz
    assert "→ hiba" in sz
    assert sz.endswith("Javítsd meg úgy, hogy legközelebb menjen; teszt a hibára.")


def test_2_tul_szeles_studio_tiltas_tudasbazis_korben_kerelem(bridge):
    k = H.Kor("a Feldwebel", "Csinálj diasort a robot-tanulmányból a Studióban.")
    k.eszkoz("tudasbazis", {"kerdes": "TITKOS-TUDAS-KERDES"}, json.dumps({"valasz": "TITKOS-TUDAS-TARTALOM"}))
    k.eszkoz("studio", {"eszkoz": "pptx_build", "parameterek": {"spec": "TITKOS-DIA"}}, json.dumps({"error": REGI_SF_FELE_HIBA}))
    assert k.vege("A Studio nem tudta feldolgozni: TITKOS-TUDAS-TARTALOM", vedett=True, most=T0)
    [kk] = _kerelmek(bridge)
    assert kk["eszkoz"] == "studio" and kk["szoveg"].startswith("HOPSZ (automatikus): studio nem ment: ")
    assert "csak a kínai ÜGYNÖKHÖZ adást tiltja" in kk["szoveg"]                  # miért nem a Kommandant szabálya
    assert "TITKOS" not in kk["_nyers"]                                          # tudásbázis-kör: se argumentum, se válasz
    assert "tudasbazis; studio → hiba" in kk["szoveg"]


def test_3_feldwebel_nem_tudja_mi_a_hog_kerelem(bridge):
    k = H.Kor("a Feldwebel", "Csináld meg a HOG-ot erre a posztra: https://x.com/valaki/status/1861")
    assert k.vege(HOG_VALASZ, most=T0)
    [kk] = _kerelmek(bridge)
    assert kk["eszkoz"] == "a Feldwebel („HOG”)"
    assert "„Nem tudom, mi a HOG.”" in kk["szoveg"] and "eszközt nem hívott" in kk["szoveg"]


def test_4_nincs_diasor_eszkoz_kerelem(bridge):
    k = H.Kor("a(z) „Bemutató” feladat-ügynök", "Készíts 12–15 diás bemutatót a szerdai megbeszélésre.")
    k.eszkoz("read_gmail_attachment", {"message_id": "m1"}, json.dumps({"content": "a tanulmány szövege"}))
    assert k.vege(DIASOR_VALASZ, most=T0)
    [kk] = _kerelmek(bridge)
    assert kk["eszkoz"] == "a(z) „Bemutató” feladat-ügynök („diasor”)"
    assert "Nincs diasor-eszközöm" in kk["szoveg"]


@pytest.mark.parametrize("valasz", [
    "Nem férek hozzá a Studióhoz, ezért csak a diaterv szövegét adom.",
    "A teljes Studio tiltott nekem, ezért csak szöveges diatervet tudok adni.",       # a túl széles tiltás is hiány
    "Ismeretlen tool: hog_prepare",
    "Nem tudom, mi a „HOG”.",                                                      # idézőjeles név
])
def test_a_kepesseg_hiany_tobb_alakja(valasz):
    k = H.Kor("a Feldwebel", "készíts bemutatót a HOG-ról")
    if valasz.startswith("Ismeretlen"):
        k.eszkoz("hog_prepare", {}, json.dumps({"error": valasz}))
        valasz = ""
    lel = [x for x in k.leletek(valasz) if x.tipus == "hiany"]
    assert lel, valasz


@pytest.mark.parametrize("hiba", ["KeyError: 'items'", "TypeError: 'NoneType' object is not subscriptable",
                                  "AttributeError: 'dict' object has no attribute 'json'",
                                  "ValueError: invalid literal for int() with base 10: 'x'",
                                  "[Parse error: Expecting value: line 1 column 1 (char 0)]"])
def test_python_kivetel_az_eszkozben_hiany(hiba):
    lel = H.eszkoz_itelet("web_fetch", H.hiba_szoveg(json.dumps({"error": hiba})))
    assert lel.tipus == "hiany", lel


# ══ 2. ZAJ ═══════════════════════════════════════════════════════════════════

ZAJ = [
    ("statdata", {"error": "SiliconFlow HTTP 402: Payment Required — insufficient balance"}, False),
    ("web_fetch", {"error": "ReadTimeout: The read operation timed out"}, False),
    ("web_fetch", {"error": "ConnectError: [Errno -2] Name or service not known"}, False),
    ("tweet", {"error": "X API 403 Forbidden: You are not permitted to perform this action."}, False),
    ("ai_query", {"error": "HTTP 429 Too Many Requests"}, False),
    ("echolot", {"error": "Echolot MCP: HTTP 503 Service Unavailable"}, False),
    ("ai_query", {"error": futar_kliens.SF_FELE_HIBA}, False),                     # a kínai-ügynök tiltás
    ("tudasbazis", {"error": F.SF_TUDASBAZIS_ESZKOZ}, True),
    ("gmail_search", {"error": F.SF_GMAIL_NAPTAR_ESZKOZ}, True),
    ("utasit", {"error": F.SF_PARTNER_HIBA}, True),
    ("send_email", {"error": F.engedett("send_email")}, True),                    # kifelé ható: jóváhagyás kell
    ("echolot", {"error": F.engedett("echolot", {"eszkoz": "hog_publish"})}, True),
    ("web_search", {"error": "a(z) `web_search` hívási kerete (6) ebben a futásban elfogyott — dolgozz a meglévő "
                             "eredményekből"}, True),                             # a keret
    ("feladat_ugynok.futas", {"error": "KeretElfogyott: a futás kerete elfogyott"}, False),
    ("faber", {"hiba": "a Faber-relay nem elérhető (nincs Telegram-token a Bridge-en)"}, False),
    ("tudasbazis", {"error": futar_kliens.NEM_ELERHETO}, False),                  # a gép ki van kapcsolva
]


@pytest.mark.parametrize("eszkoz,eredmeny,elutasitas", ZAJ)
def test_zaj_nem_megy(bridge, eszkoz, eredmeny, elutasitas):
    k = H.Kor("a Feldwebel", "valami feladat")
    k.eszkoz(eszkoz, {"eszkoz": "hog_publish"} if eszkoz == "echolot" else {}, json.dumps(eredmeny, ensure_ascii=False),
             elutasitas=elutasitas)
    assert k.vege("Rendben, itt az eredmény.", most=T0) is None
    assert _kerelmek(bridge) == []


@pytest.mark.parametrize("valasz,kerdes", [
    ("Nem tudom, mit küldjek. Még nincs szöveg, amit a cli-claus-nak írnék.", "küldj neki valamit"),   # visszakérdezés
    ("A cikket nem tudom elolvasni, mert fizetős (paywall).", "olvasd el ezt a cikket"),
    ("Nem tudom, mi a helyzet a piacon.", "mi a helyzet a piacon?"),
    ("Nem tudom, mi a Laci álláspontja.", "mit gondol Laci?"),
    ("Nem tudom, mi a HOG.", "mi újság?"),                                     # nem a kérés része → bizonytalan, csend
    ("A miniszter szerint „nincs eszközünk a forint gyengülése ellen”.", "figyeld a forintot"),   # idézet, nem az ügynök
    ("Nincs olyan eszköz, amely garantálná a hozamot.", "mibe fektessek?"),     # nem az ügynök saját hiánya
])
def test_a_rendes_valasz_nem_kepesseg_hiany(bridge, valasz, kerdes):
    assert H.Kor("a Feldwebel", kerdes).vege(valasz, most=T0) is None
    assert _kerelmek(bridge) == []


def test_bizonytalan_csak_naplo(bridge, caplog):
    k = H.Kor("a Feldwebel", "x")
    k.eszkoz("ugynok", {}, json.dumps({"error": "nincs ilyen ügynök: „Kamatfigyelő”"}))
    with caplog.at_level("INFO", logger="hopsz"):
        assert k.vege("", most=T0) is None
    assert _kerelmek(bridge) == [] and "HOPSZ bizonytalan" in caplog.text


# ══ 3. DUPLIKÁCIÓ ÉS NAPI PLAFON ═════════════════════════════════════════════

def test_ugyanaz_a_hiba_7_napig_egyszer(bridge):
    assert _docx_kor().vege(most=T0)
    assert _docx_kor("masik_kep.png").vege(most=T0 + 3600) is None        # más fájl, ugyanaz a hiba
    assert _docx_kor().vege(most=T0 + 6 * NAP) is None
    assert len(_kerelmek(bridge)) == 1
    assert _docx_kor().vege(most=T0 + 7 * NAP + 60)                       # 7 nap után újra szólhat
    assert len(_kerelmek(bridge)) == 2


def test_ugyanaz_a_kepesseg_hiany_mas_mondattal_is_egyszer(bridge):
    assert H.Kor("a Feldwebel", "csináld meg a HOG-ot").vege(HOG_VALASZ, most=T0)
    assert H.Kor("a Feldwebel", "HOG kell erre").vege("Sajnos nem tudom, mi az a HOG.", most=T0 + 60) is None
    assert len(_kerelmek(bridge)) == 1


def _hibas_kor(eszkoz, hiba):
    k = H.Kor("a Feldwebel", "feladat")
    k.eszkoz(eszkoz, {}, json.dumps({"error": hiba}))
    return k


HIBAK = [("web_fetch", "KeyError: 'items'"), ("statdata", "TypeError: unsupported operand"),
         ("studio", "AttributeError: 'NoneType' object has no attribute 'get'"),
         ("lexonomics", "ValueError: invalid literal for int()")]


def test_napi_plafon_alapbol_3(bridge):
    ki = [_hibas_kor(e, h).vege(most=T0 + i) for i, (e, h) in enumerate(HIBAK)]
    assert [bool(x) for x in ki] == [True, True, True, False]
    assert len(_kerelmek(bridge)) == 3
    assert _hibas_kor(*HIBAK[3]).vege(most=T0 + NAP)                     # másnap (Budapest) mehet
    assert len(_kerelmek(bridge)) == 4


def test_napi_plafon_envbol(bridge, monkeypatch):
    monkeypatch.setenv("HOPSZ_NAPI_MAX", "1")
    assert [bool(_hibas_kor(e, h).vege(most=T0 + i)) for i, (e, h) in enumerate(HIBAK)] == [True, False, False, False]
    monkeypatch.setenv("HOPSZ_NAPI_MAX", "0")
    assert _hibas_kor("x", "KeyError: 'y'").vege(most=T0 + 2 * NAP) is None


def test_az_allapot_a_kozos_retegen_at_egy_kulcsban(bridge):
    _docx_kor().vege(most=T0)
    c = bridge()
    try:
        sorok = c.execute("SELECT key, category, updated_by, value FROM shared_memory").fetchall()
    finally:
        c.close()
    assert [(r["key"], r["category"], r["updated_by"]) for r in sorok] == [("hopsz_allapot", "hopsz", "hopsz")]
    d = json.loads(sorok[0]["value"])
    assert d["db"] == 1 and len(d["alairasok"]) == 1 and "partname" not in sorok[0]["value"]   # csak aláírás, szöveg nem


# ══ 4. TITOK ═════════════════════════════════════════════════════════════════

def test_titok_nem_szivarog(bridge, monkeypatch):
    monkeypatch.setenv("VALAMI_API_KEY", "kornyezetiTitok-98765")
    hiba = (f"KeyError: 'x' — url https://api.telegram.org/bot{TOKEN}/sendMessage?token=qwertyQWERTY123 "
            "kulcs sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv dsn postgresql://claus:JelszoTitok42@db.host:5432/b "
            "Authorization: Bearer abcdefghijklmnopqrstuvwx eop_titkoskulcs123 kornyezetiTitok-98765 "
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 AIzaSyA1234567890abcdefghijklmnop")
    k = H.Kor("a Feldwebel", f"próbáld meg, a kulcsom sk-ant-api03-ZzZzZzZzZzZzZzZzZzZz")
    k.eszkoz("web_fetch", {"url": f"https://x.hu/?api_key=nagyonTITKOSkulcs99", "token": TOKEN}, json.dumps({"error": hiba}))
    assert k.vege(most=T0)
    [kk] = _kerelmek(bridge)
    for titok in (TOKEN, "AAHopszTeszt", "qwertyQWERTY123", "sk-ant-api03", "JelszoTitok42", "abcdefghijklmnopqrstuvwx",
                  "titkoskulcs123", "kornyezetiTitok", "ABCDEFGHIJKLMNOP", "AIzaSyA123", "nagyonTITKOS", "ZzZzZz"):
        assert titok not in kk["szoveg"], titok
    assert "KeyError" in kk["szoveg"] and len(kk["szoveg"]) < 1400


def test_a_hiba_legfeljebb_600_karakter(bridge):
    assert _hibas_kor("web_fetch", "KeyError: " + "x " * 2000).vege(most=T0)
    [kk] = _kerelmek(bridge)
    hiba = kk["szoveg"].split(" nem ment: ", 1)[1].split(". A feladat:", 1)[0]
    assert len(hiba) <= H.HIBA_MAX


# ══ 5. KAPCSOLÓ ══════════════════════════════════════════════════════════════

def test_kapcsolo_ki_semmi(bridge, monkeypatch):
    monkeypatch.setenv("HOPSZ_AUTO", "0")
    assert _docx_kor().vege(most=T0) is None
    assert H.Kor("a Feldwebel", "Csináld meg a HOG-ot").vege(HOG_VALASZ, most=T0) is None
    assert _kerelmek(bridge) == []
    c = bridge()
    try:
        assert c.execute("SELECT COUNT(*) FROM shared_memory").fetchone()[0] == 0
    finally:
        c.close()


def test_kapcsolo_alapbol_be(monkeypatch):
    monkeypatch.delenv("HOPSZ_AUTO", raising=False)
    assert H.aktiv() and H.napi_max() == 3


def test_bekotes_es_token_nelkul_csend(get_db, monkeypatch):
    monkeypatch.setenv("HOPSZ_AUTO", "1")
    elozo = dict(H._DEPS)
    try:
        H._DEPS.clear()
        assert _docx_kor().vege(most=T0) is None                          # nincs bekötve
        H.beallit(get_db=get_db, memoria_ir=memoria_ir, bot_token=lambda: "")
        assert _docx_kor().vege(most=T0) is None                          # nincs relay-kulcs
    finally:
        H._DEPS.clear()
        H._DEPS.update(elozo)


def test_a_riaszto_hibaja_nem_allitja_meg_az_ugynokot(get_db, monkeypatch):
    monkeypatch.setenv("HOPSZ_AUTO", "1")
    elozo = dict(H._DEPS)

    def rossz_db():
        raise RuntimeError("adatbázis-hiba")
    try:
        H.beallit(get_db=rossz_db, memoria_ir=memoria_ir, bot_token=lambda: TOKEN)
        assert _docx_kor().vege(most=T0) is None
    finally:
        H._DEPS.clear()
        H._DEPS.update(elozo)


# ══ 6. BEKÖTÉS ═══════════════════════════════════════════════════════════════

class _Forras:
    def tudasbazis_elerheto(self):
        return False

    async def kulso_definiciok(self):
        return []

    async def feldwebel_eszkoz(self, nev, args, modell):
        if nev == "read_gmail_attachment":
            return DOCX
        return json.dumps({"valasz": "ok"})


def _valasz(szoveg="", hivasok=None):
    msg = {"role": "assistant", "content": szoveg}
    if hivasok:
        msg["tool_calls"] = [{"id": f"t{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                             for i, (n, a) in enumerate(hivasok)]
    return {"model": "claude-sonnet-5-5", "koltes_usd": 0.01,
            "choices": [{"message": msg, "finish_reason": "tool_calls" if hivasok else "stop"}]}


def test_feladat_ugynok_futasa_kerelmet_tesz(bridge, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teszt-hopsz")
    import llm_kapu
    monkeypatch.setattr(llm_kapu, "_KOLTES", {"honap": "", "usd": 0.0, "hivasok": 0, "szintenkent": {}})
    c = bridge()
    try:
        out = F.letrehoz(c, nev="Bemutató", feladat="Ebből a tanulmányból készíts 12–15 diás bemutatót a szerdai "
                                                    "megbeszélésre.", utemezes="", egyszeri=True)
        c.commit()
    finally:
        c.close()
    rid = out["ugynok"]["rid"]
    sor = [_valasz(hivasok=[("read_gmail_attachment", {"message_id": "m1", "attachment_index": 0})]),
           _valasz(json.dumps({"jelzes": True, "osszegzes": "nem ment",
                               "eredmeny": "A mellékletet nem sikerült elolvasni."}, ensure_ascii=False))]

    async def kapu(payload, *, purpose="", **kw):
        return sor.pop(0), None, []

    async def tg(s):
        pass
    asyncio.run(F.futas(rid, trigger="kezi", get_db=bridge, forras=_Forras, chat=kapu, telegram=tg,
                        most=datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)))
    [kk] = _kerelmek(bridge)
    assert kk["eszkoz"] == "read_gmail_attachment" and "no content type for partname" in kk["szoveg"]
    assert "„Bemutató” feladat-ügynök" not in kk["eszkoz"] and "A feladat: Ebből a tanulmányból" in kk["szoveg"]


def test_a_feldwebel_kore_es_a_feladat_ugynok_a_hopsz_on_at_megy():
    gyoker = Path(__file__).resolve().parent.parent
    resp = (gyoker / "feldwebel" / "responder.py").read_text(encoding="utf-8")
    assert "hopsz_kor = _hopsz.Kor(" in resp and resp.count("hopsz_kor.eszkoz(fn_name, fn_args, result)") == 2
    assert resp.index("hopsz_kor.vege(final_content") < resp.index("if not final_content:\n        # Debug")
    fu = (gyoker / "feladat_ugynok.py").read_text(encoding="utf-8")
    assert "nyom.hopsz_kor.vege(" in fu and "nyom.hopsz_kor.eszkoz(eszkoz, args, ns._hiba(ok), elutasitas=True)" in fu
    srv = (gyoker / "server.py").read_text(encoding="utf-8")
    assert "hopsz.beallit(get_db=get_db, memoria_ir=memoria_ir" in srv


def test_a_kabin_a_hopsz_kerelmet_rendszer_tetelkent_mutatja(bridge):
    import ui_kabin
    _docx_kor().vege(most=T0)
    c = bridge()
    try:
        r = c.execute("SELECT id, timestamp, sender, recipient, subject, message FROM messages "
                      "WHERE recipient = 'faber'").fetchone()
        relay = {t["id"]: t for t in ui_kabin._relay_tetelek(c, faber_relay.kulcs(TOKEN))}
    finally:
        c.close()
    [(ablak, tetel)] = ui_kabin._osztalyoz(r, relay)
    assert ablak == "faber" and tetel["ki"] == "rendszer" and tetel["szoveg"].startswith("🔧 HOPSZ → Faber: HOPSZ")


def test_kulso_szolgaltatas_hibaja_a_sajat_repojaba_megy():
    """11-25, a Kommandant: a Bridge összetett rendszer — a Faber mindenhez férjen hozzá, ha probléma van."""
    import hopsz
    assert hopsz.repo_eszkozhoz("echolot") == "echolot" and hopsz.repo_eszkozhoz("engine") == "echolot"
    assert hopsz.repo_eszkozhoz("makronom") == "statdata" and hopsz.repo_eszkozhoz("studio") == "studio"
    for e in ("lexonomics", "foldgomb", "tudasbazis", "", None):        # profil nélkül → a Bridge
        assert hopsz.repo_eszkozhoz(e) == "bridge"
    import faber_relay
    assert set(hopsz.ESZKOZ_REPO.values()) <= set(faber_relay.REPOK)
