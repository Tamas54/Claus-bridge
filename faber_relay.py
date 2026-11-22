"""Faber-továbbítás (2026-11-22): a Bridge az EGYETLEN Telegram-csatorna gazdája; a Faber (a Kommandant gépén fut)
innen veszi fel a neki szóló parancsokat és gombnyomásokat.

Miért így: a Faber és a Bridge ugyanazt a botot (claus_kommandant_bot) használta, a két getUpdates-lekérdező 409-cel
ütközött, a gombnyomás elveszett. A Kommandant: „nem akarok új telegrammot … Működjön.”

Biztonság: a /api/messages hitelesítés nélküli, bárki írhat a 'faber' címzettnek. Ezért minden továbbított tétel
HMAC-aláírást kap a bot tokenjéből képzett kulccsal (sha256("faber:" + token)) — ezt csak a Bridge és a Faber ismeri,
új titok vagy Railway-változó nem kell. A Faber csak aláírt tételt fogad el, Bridge-azonosítónként egyszer; a jóváhagyó
gomb ráadásul egyszer használatos kódot hordoz (mehet:<szám>:<kód>), amit a Faber a saját táblájában őriz.
"""
import hashlib
import hmac
import json
import re

PARANCSOK = ("/tanulj", "/faber", "/vissza")
GOMB = re.compile(r"^(mehet|megse):\d+:[A-Za-z0-9]{6,32}$")


def kulcs(token: str) -> bytes | None:
    return hashlib.sha256(("faber:" + token).encode()).digest() if token else None


def parancs_e(text: str) -> bool:
    return (text or "").strip().lower().startswith(PARANCSOK)


def gomb_e(adat: str) -> bool:
    return bool(GOMB.match(adat or ""))


def alair(k: bytes, tipus: str, adat: str, ts: str) -> str:
    """A 'faber' címzettű Bridge-üzenet törzse: {"torzs": <json>, "sig": <hmac hex>}."""
    torzs = json.dumps({"tipus": tipus, "adat": adat, "ts": ts}, ensure_ascii=False)
    return json.dumps({"torzs": torzs, "sig": hmac.new(k, torzs.encode(), hashlib.sha256).hexdigest()}, ensure_ascii=False)


def ellenoriz(k: bytes, uzenet: str) -> dict | None:
    """A Faber oldala (ugyanez a logika él a ~/faber/faber.py-ban): érvényes aláírásnál a törzs, különben None."""
    try:
        d = json.loads(uzenet)
        if not hmac.compare_digest(hmac.new(k, d["torzs"].encode(), hashlib.sha256).hexdigest(), d["sig"]):
            return None
        return json.loads(d["torzs"])
    except (ValueError, KeyError, TypeError):
        return None


def tovabbit(conn, token: str, tipus: str, adat: str, ts: str) -> int | None:
    """Aláírt üzenet a 'faber' címzettnek a Bridge messages táblájába; a Bridge-azonosító, vagy None, ha nincs token."""
    k = kulcs(token)
    if not k:
        return None
    cur = conn.execute(
        "INSERT INTO messages (timestamp, sender, recipient, subject, message, priority) "
        "VALUES (?, 'kommandant-telegram', 'faber', ?, ?, 'normal')",
        (ts, f"faber:{tipus}", alair(k, tipus, adat, ts)))
    mid = cur.lastrowid
    conn.execute("UPDATE messages SET thread_id = ? WHERE id = ?", (mid, mid))
    conn.commit()
    return mid
