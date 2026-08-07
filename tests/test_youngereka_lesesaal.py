#!/usr/bin/env python3
"""OPERATION LESESAAL — a §9 tesztkör.

    python tests/test_youngereka_lesesaal.py

Önálló szkriptként fut (nincs pytest-függés), és a hálózatot nem
piszkálja: a modell-hívás ki van cserélve. Amit ellenőriz, az a
token-réteg, a jogosultság-réteg és a dokumentum-pipeline — vagyis
minden, ami Réka nélkül is eldönthető.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import pathlib
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# A token-térkép env-ből olvas — a teszt előtt kell beállítani.
os.environ["YR_TOKEN"] = "TESZT_TOKEN_1234567890abcdef"
BAD = "NEM_JO_TOKEN_0000000000000000"

import youngereka_budget as budget          # noqa: E402
import youngereka_chat as chat              # noqa: E402
import youngereka_docs as docs              # noqa: E402
from youngereka_access import (             # noqa: E402
    REKA_CHAT_PROMPT, YRScopeMiddleware, force_caller,
    resolve_instance_from_path)

hibak: list[str] = []


def ok(felt, cim):
    print(("  ✓ " if felt else "  ✗ ") + cim)
    if not felt:
        hibak.append(cim)


def szakasz(cim):
    print(f"\n── {cim} " + "─" * max(0, 56 - len(cim)))


# ============================================================
szakasz("1–3. Token és identitás")

ok(resolve_instance_from_path("TESZT_TOKEN_1234567890abcdef") == "YoungeReka",
   "jó token → YoungeReka")
ok(resolve_instance_from_path(BAD) is None, "rossz token → None (403 lesz)")
ok(resolve_instance_from_path("") is None, "üres token → None")

# A legfontosabb: a hamisított caller felülírása.
hamis = {"method": "tools/call",
         "params": {"name": "read_memory",
                    "arguments": {"caller": "kommandant", "key": "x"}}}
javitott = force_caller(hamis, "YoungeReka")
ok(javitott["params"]["arguments"]["caller"] == "YoungeReka",
   "hamisított caller='kommandant' felülírva YoungeReka-ra")
ok(hamis["params"]["arguments"]["caller"] == "kommandant",
   "az eredeti payload érintetlen (nincs mellékhatás)")

# Caller nélkül érkező hívás is kap identitást — különben a szerver
# „nincs caller" ágon csendben átengedné.
nincs = {"method": "tools/call",
         "params": {"name": "capture_gmail_poll", "arguments": {}}}
ok(force_caller(nincs, "YoungeReka")["params"]["arguments"]["caller"] == "YoungeReka",
   "hiányzó caller BEÍRVA (nem marad üres → nem csúszik át)")

# Üres env → a token-térkép ne engedjen be senkit
_ment = os.environ.pop("YR_TOKEN")
ok(resolve_instance_from_path("") is None and resolve_instance_from_path("akarmi") is None,
   "YR_TOKEN nélkül SENKI nem lép be (az üres string nem lesz érvényes token)")
os.environ["YR_TOKEN"] = _ment


# ============================================================
szakasz("10. DENY-teszt — a lényegi")

from permissions import (Access, PermissionDeniedError,  # noqa: E402
                         check_permission)
from youngereka_profile import register_youngereka        # noqa: E402

register_youngereka()

for tool in ("capture_gmail_poll", "capture_send_email", "capture_calendar_poll",
             "create_calendar_event", "capture_inbox", "read_gmail_attachment",
             "capture_status"):
    try:
        check_permission("YoungeReka", tool)
        ok(False, f"{tool} → DENY")
    except PermissionDeniedError:
        ok(True, f"{tool} → DENY")

for tool in ("read_memory", "ai_query", "ai_task", "upload_file"):
    try:
        ok(check_permission("YoungeReka", tool) in (Access.ALLOW, Access.FILTERED),
           f"{tool} → ALLOW")
    except PermissionDeniedError:
        ok(False, f"{tool} → ALLOW")

# 12. A böngésző-vezérlés tiltása. A Brave MCP nem kereső, hanem böngésző
# perzisztens login-session-ökkel: egy brave_navigate megnyithatná a
# mail.google.com-ot, és a fenti Gmail-tiltás díszletté válna.
for tool in ("brave_login", "brave_session_action", "brave_navigate",
             "brave_mouse_control", "brave_visual_captcha", "brave_list_sessions",
             "brave_crawl", "brave_scrape", "brave_marked_snapshot",
             "brave_visual_inspect", "brave_clear_sessions"):
    try:
        check_permission("YoungeReka", tool)
        ok(False, f"{tool} → DENY")
    except PermissionDeniedError:
        ok(True, f"{tool} → DENY")

# …de a szűk kereső-metszet nyitva
for tool in ("search_web", "scrape_url"):
    try:
        ok(check_permission("YoungeReka", tool) == Access.ALLOW, f"{tool} → ALLOW")
    except PermissionDeniedError:
        ok(False, f"{tool} → ALLOW")

# Nem regisztrált instance semmit nem érhet el ("was nicht erlaubt ist…")
try:
    check_permission("idegen", "read_memory")
    ok(False, "ismeretlen instance → DENY")
except PermissionDeniedError:
    ok(True, "ismeretlen instance → DENY")


# ============================================================
szakasz("Cookie — aláírás nélkül nincs belépés")

sign = chat._sign("YoungeReka", 2 ** 31)
ok(chat._verify(sign) == "YoungeReka", "saját aláírású cookie elfogadva")
ok(chat._verify("YoungeReka|2147483648|hamisitott") is None,
   "hamisított aláírás elutasítva")
ok(chat._verify("YoungeReka") is None, "aláírás nélküli cookie elutasítva")
ok(chat._verify(chat._sign("YoungeReka", 1)) is None, "lejárt cookie elutasítva")


# ============================================================
szakasz("5. Kép — HEIC/forgatás/EXIF-strip")

from PIL import Image  # noqa: E402


def _exif_kep(orientation: int, w=400, h=260) -> bytes:
    """Fekvő kép, EXIF orientation=6 → a néző álló képet vár."""
    im = Image.new("RGB", (w, h), (200, 120, 140))
    exif = im.getexif()
    exif[274] = orientation           # Orientation
    exif[34853] = {1: "N", 2: (47.0, 30.0, 0.0)}  # GPS — ennek MENNIE kell
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


forgatott = _exif_kep(6)
norm = docs.normalize_image(forgatott)
im = Image.open(io.BytesIO(norm))
ok(im.size == (260, 400), f"EXIF orientation alkalmazva: 400×260 → {im.size[0]}×{im.size[1]} (álló)")
ok(not im.getexif(), "EXIF (és vele a GPS) teljesen eltávolítva")
ok(im.mode == "RGB", "RGB-re konvertálva")

nagy = Image.new("RGB", (4000, 3000), "white")
b = io.BytesIO(); nagy.save(b, format="PNG")
ok(max(Image.open(io.BytesIO(docs.normalize_image(b.getvalue()))).size) == 2048,
   "4000px → 2048px hosszabb él")

d = docs.process_upload(forgatott, "IMG_2231.jpg")
ok(d["kind"] == "image" and len(d["images"]) == 1, "kép → 1 vision-kép")
ok(d["label"].startswith("Kép · 260×400"), f"preparátum-címke: {d['label']!r}")


# ============================================================
szakasz("6–8. PDF — szöveg, szkennelt, ábrás")

import fitz  # noqa: E402

# (6) szöveges PDF
doc = fitz.open()
for i in range(3):
    p = doc.new_page()
    p.insert_text((72, 100), f"{i+1}. oldal. " + "GFAP expression in astrocytes. " * 12)
szoveges = doc.tobytes(); doc.close()

r = docs.process_upload(szoveges, "cikk.pdf")
ok(r["kind"] == "pdf" and "GFAP" in r["text"], "szövegréteg kijön")
ok(r["images"] == [], "szöveges PDF-nél nincs fölösleges raszterizálás")
ok("3 oldal" in r["label"], f"címke: {r['label']!r}")

# (7) szkennelt PDF — nincs szövegréteg
doc = fitz.open()
kep = Image.new("RGB", (900, 1200), "white")
bb = io.BytesIO(); kep.save(bb, format="PNG")
for _ in range(2):
    p = doc.new_page(width=595, height=842)
    p.insert_image(fitz.Rect(0, 0, 595, 842), stream=bb.getvalue())
szkennelt = doc.tobytes(); doc.close()

r = docs.process_upload(szkennelt, "szkennelt.pdf")
ok(r["kind"] == "pdf" and len(r["images"]) == 2,
   f"szkennelt PDF raszterizálva: {len(r['images'])} oldalkép")
ok(any("nincs szövegréteg" in n for n in r["notes"]),
   "Réka megkapja az indoklást, miért képként olvassuk")

# (8) ábrás PDF — a Figure-öket külön kell kiemelni
doc = fitz.open()
p = doc.new_page(width=595, height=842)
p.insert_text((72, 80), "Results. " + "Astrocyte activation was measured. " * 20)
abra = Image.new("RGB", (600, 420), (240, 240, 240))
ab = io.BytesIO(); abra.save(ab, format="PNG")
p.insert_image(fitz.Rect(72, 300, 472, 580), stream=ab.getvalue())
ikon = Image.new("RGB", (40, 40), "black")
ib = io.BytesIO(); ikon.save(ib, format="PNG")
p.insert_image(fitz.Rect(500, 60, 540, 100), stream=ib.getvalue())
abras = doc.tobytes(); doc.close()

r = docs.process_upload(abras, "figure.pdf")
ok(len(r["images"]) == 1, f"az ÁBRA kiemelve, az ikon nem ({len(r['images'])} kép)")
ok("Astrocyte" in r["text"], "a szöveg is megvan az ábra mellett")
ok("ábra kinyerve" in r["label"], f"címke: {r['label']!r}")

# 8 kép fölött vágás + jelzés
doc = fitz.open()
for i in range(11):
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 60), "Figure page. " + "text " * 60)
    egyedi = Image.new("RGB", (300 + i, 300), (i * 20 % 255, 90, 140))
    eb = io.BytesIO(); egyedi.save(eb, format="PNG")
    p.insert_image(fitz.Rect(72, 200, 472, 600), stream=eb.getvalue())
sok = doc.tobytes(); doc.close()
r = docs.process_upload(sok, "sok_abra.pdf")
ok(len(r["images"]) <= docs.VISION_MAX_IMAGES,
   f"kép-korlát tartva ({len(r['images'])} ≤ {docs.VISION_MAX_IMAGES})")
ok(any("ment fel" in n for n in r["notes"]),
   "Réka megtudja, hány ábra maradt ki és hogyan kérheti")


# ============================================================
szakasz("Egyéb formátumok")

r = docs.process_upload("A;B;C\n1;2;3\n".encode(), "adat.csv")
ok(r["kind"] == "text" and "A;B;C" in r["text"], "CSV közvetlenül")

r = docs.process_upload("mérés\tn=3\n".encode("utf-8"), "m.txt")
ok(r["kind"] == "text", "TXT közvetlenül")

try:
    docs.process_upload(b"\x00\x01\x02\xff\xfe binaris", "valami.bin")
    ok(False, "olvashatatlan fájl → emberi hibaüzenet")
except ValueError as e:
    ok("PDF-ként vagy képként" in str(e), "olvashatatlan fájl → útmutató hiba")

# MIME-sniff tartalom szerint, nem kiterjesztés szerint
ok(docs._sniff(szoveges, "akarmi.txt") == "pdf",
   "rosszul nevezett PDF felismerve (.txt kiterjesztéssel is)")
ok(docs._sniff(forgatott, "kep_kiterjesztes_nelkul") == "image",
   "kiterjesztés nélküli JPEG felismerve")


# ============================================================
szakasz("9. Keret — csendes fallback, nem hiba")

tmp = tempfile.mkdtemp()
conn = sqlite3.connect(pathlib.Path(tmp) / "t.db")
conn.row_factory = sqlite3.Row
budget.ensure_schema(conn)

ok(budget.spent_today(conn, "YoungeReka") == 0.0, "induláskor 0 USD")
ok(budget.pick_model(conn, "YoungeReka", "kimi") == ("kimi", False),
   "kereten belül a kért modell megy")
ok(not budget.budget_state(conn, "YoungeReka")["exhausted"], "keret nincs kimerítve")

budget.record(conn, "YoungeReka", 6.0, "moonshotai/Kimi-K3", "teszt")
allapot = budget.budget_state(conn, "YoungeReka")
ok(allapot["exhausted"] and allapot["ratio"] > 1.0, "6 USD > 5 USD keret → kimerült")
ok(budget.pick_model(conn, "YoungeReka", "kimi") == ("deepseek", True),
   "kimerült keret → csendes deepseek (nem hiba, nem tiltás)")
ok(budget.pick_model(conn, "YoungeReka", "deepseek") == ("deepseek", False),
   "a deepseek nem esik vissza önmagára (nincs körbeérés)")

# A KÉP-TUDATOS fallback — a V4-Pro nem VLM (élő mérés: HTTP 400/20041).
ok(chat._fallback_for(False) == "deepseek", "szöveges üzenet → deepseek fallback")
ok(chat._fallback_for(True) != "deepseek",
   f"KÉPES üzenet → látó fallback ({chat._fallback_for(True)}), nem a nem-VLM deepseek")

ok(budget.estimate_cost("moonshotai/Kimi-K3", 1_000_000, 1_000_000) == 17.0,
   "költségbecslés: K3 1M+1M = 2.00 + 15.00 USD")
conn.close()


# ============================================================
szakasz("Rendszerprompt")

ok("kis hercegnő" in REKA_CHAT_PROMPT, "a megszólítás benne van")
ok("CSAK a" in REKA_CHAT_PROMPT and "köszönés" in REKA_CHAT_PROMPT,
   "…de kizárólag a köszönésben")
ok("KORLÁTOK" in REKA_CHAT_PROMPT, "a korlátok szekció kötelező")
ok("TILOS kitalált hivatkozás" in REKA_CHAT_PROMPT, "kitalált DOI tiltva")
ok("szerb" in REKA_CHAT_PROMPT, "nem szűkít magyar forrásra")
for tilos in ("unokahúg", "nagynén", "szőke", "szép"):
    ok(tilos not in REKA_CHAT_PROMPT.lower().replace("a nagybátyja", ""),
       f"nincs benne magánéleti/külső jellemző: {tilos!r}")


# ============================================================
szakasz("Séma — Postgres-kész")

conn = sqlite3.connect(pathlib.Path(tmp) / "s.db")
conn.row_factory = sqlite3.Row
chat.ensure_schema(conn)
for tabla in ("yr_chat_sessions", "yr_chat_messages", "yr_chat_files", "yr_spend"):
    sor = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (tabla,)).fetchone()
    ok(sor is not None, f"{tabla} létrejött")
    if sor:
        ok("AUTOINCREMENT" not in sor["sql"].upper(),
           f"{tabla}: nincs AUTOINCREMENT (TEXT uuid kulcs)")
ok(conn.execute("SELECT sql FROM sqlite_master WHERE name='idx_yr_msg_session'"
                ).fetchone() is not None, "üzenet-index létrejött")
chat.ensure_schema(conn)  # idempotens?
ok(True, "a séma kétszer futtatva sem dob")
conn.close()


# ============================================================
szakasz("ASGI — a scoped MCP-út")


def _asgi_hivas(path: str, torzs: bytes):
    """Végigfuttat egy kérést a middleware-en, és visszaadja, mit kapott
    a belső app (vagy a 403-at)."""
    fogott = {}

    async def belso_app(scope, receive, send):
        fogott["path"] = scope["path"]
        darabok = []
        while True:
            m = await receive()
            darabok.append(m.get("body", b""))
            if not m.get("more_body"):
                break
        fogott["body"] = b"".join(darabok)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = YRScopeMiddleware(belso_app)
    kimenet = []

    async def receive():
        return {"type": "http.request", "body": torzs, "more_body": False}

    async def send(msg):
        kimenet.append(msg)

    scope = {"type": "http", "path": path, "headers": [(b"content-length", b"1")],
             "method": "POST"}
    asyncio.run(mw(scope, receive, send))
    return fogott, kimenet


torzs = json.dumps({"method": "tools/call", "params": {
    "name": "read_memory", "arguments": {"caller": "kommandant"}}}).encode()

fogott, ki = _asgi_hivas("/mcp/yr-TESZT_TOKEN_1234567890abcdef", torzs)
ok(fogott.get("path") == "/mcp", "az út /mcp-re írva")
uj = json.loads(fogott["body"])
ok(uj["params"]["arguments"]["caller"] == "YoungeReka",
   "az ASGI-rétegen át is felülíródik a caller")

fogott, ki = _asgi_hivas("/mcp/yr-" + BAD, torzs)
ok(fogott == {}, "rossz token: a belső app MEG SEM hívódik")
ok(ki[0]["status"] == 403, "rossz token → 403")

fogott, ki = _asgi_hivas("/mcp", torzs)
ok(fogott.get("path") == "/mcp" and json.loads(fogott["body"])[
    "params"]["arguments"]["caller"] == "kommandant",
   "a sima /mcp út ÉRINTETLEN (a core instance-ok nem sérülnek)")


# ============================================================
print("\n" + "═" * 62)
if hibak:
    print(f"PIROS — {len(hibak)} bukás:")
    for h in hibak:
        print("   ·", h)
    sys.exit(1)
print("MIND ZÖLD — a tesztkör átment.")
