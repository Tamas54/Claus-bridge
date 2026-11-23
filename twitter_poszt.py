"""X/TWITTER-POSZT (2026-11-23) — a Bridge X-re posztol, de CSAK a Kommandant jóváhagyásával.

A Kommandant (11-23): „na nézzük a twitter dolgot” — „a twitterre posztolást egyszer már megcsináltuk … teszteltük
is” (2026-04-10: OAuth 1.0a-val kiment a „Teszt. #ClausBridge”, 201, https://x.com/i/status/2042672637025951835).
A tervezett `plugins/twitter.py` sosem készült el; ez a modul az.

AZ ÚT: TERVEZET → JÓVÁHAGYÁS → POSZT
  1. TERVEZET (`tervezet`): a Feldwebel `tweet_tervezet` eszköze és a feladat-ügynök ugyanígy nevű eszköze a meglévő
     `action_drafts` táblába ír (`kind='tweet'`, `status='pending'`). Új tábla nincs; a `kind` CHECK-je bővül a
     'tweet'-tel (`schema_biztosit`: a meglévő sorok megmaradnak). Közvetlenül posztoló eszköze SENKINEK nincs.
  2. JÓVÁHAGYÁS — két út, mindkettő a KÓDBAN, LLM nélkül:
     * a kabin munkapadja: a kártya a teljes szöveget és a karakterszámot mutatja; „Jóváhagyom” → `dont`;
     * KIFEJEZETT mondat a Telegramon vagy a kabin chatjében: „posztold ki a tervezetet: <első szavak>”
       (`kifejezett`, `kezel`) — legalább `MIN_SZO` szó a tervezet elejéről, és pontosan egy függő tervezetre illik.
     Az egyszavas „igen”/„mehet” a tweetre NEM elég, mert nyilvános és visszavonhatatlan. Ugyanaz a minta, mint a
     feladat-ügynök naptárjavaslatánál (`source_message_id = ugynok:<id>`, csak a kabin gombja): a puszta igen nem
     hagy jóvá véletlenül — itt a kabin gombja mellett a tervezet szavait IDÉZŐ mondat is elég, mert abból látszik,
     melyik szövegre mondta. A naptár egyszavas igenje (`feldwebel/jovahagyas.py`) csak `kind='calendar'`-t néz, az
     `action_draft_decide` MCP-eszköz a tweetet nem hagyja jóvá (elvetni tudja).
  3. POSZT (`posztol`): OAuth 1.0a (HMAC-SHA1; az `oauthlib` aláírója — a google-auth-oauthlib függősége, a
     requirements.txt kifejezetten is pineli) aláírt `POST https://api.twitter.com/2/tweets`; a válaszban az új tweet
     URL-je. A 401 / 402 / 403 / 429 kimondva (`_hiba_szoveg`). Az eredmény (URL vagy hiba) a Feldwebel-szálba és a
     Telegramra megy (a hívó `ertesit`-je; `szalba` írja a Feldwebel előzményébe és a kabin ablakába).
     Dupla kattintás ellen a jóváhagyás egyetlen feltételes UPDATE-tel „foglal” (pending → approved); bukásnál a
     tervezet visszakerül függőbe, a hibával — újra jóváhagyható.

KERET: napi plafon `TWITTER_NAPI_MAX` (alap 10, a budapesti naptári napra), túllépésnél kimondott hiba, a tervezet
függő marad. A plafont ugyanaz az UPDATE nézi, ami foglal (két egyidejű jóváhagyás sem lépi át). Ár: használatalapú,
~0,01 $/tweet.

KULCSOK: `TWITTER_API_KEY`, `TWITTER_API_SECRET`, `TWITTER_ACCESS_TOKEN`, `TWITTER_ACCESS_TOKEN_SECRET` env-ből
(`.env` helyben, a Railway `web` szolgáltatáson élesben). Logba és válaszba SOHA nem kerülnek: minden kifelé menő
hibaszöveg `_tisztit`-on megy át, a fejléc sehol nem naplózódik.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("twitter_poszt")
#: Az oauthlib DEBUG-szinten naplózza az aláírás alapszövegét: benne a consumer key és az access token (a tesztsor
#: kulcs-szivárgás tesztje így találta meg). A napló szintje ettől függetlenül sem nyithatja meg.
_OAUTHLIB_NAPLO = logging.getLogger("oauthlib")
_OAUTHLIB_NAPLO.setLevel(logging.WARNING)

TWEET_URL = "https://api.twitter.com/2/tweets"
EN_URL = "https://api.twitter.com/2/users/me"
STATUS_URL = "https://x.com/i/status/{id}"
KULCS_NEVEK = ("TWITTER_API_KEY", "TWITTER_API_SECRET", "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN_SECRET")
#: Ezek értéke sem kerülhet ki semmilyen szövegben (a posztoláshoz nem kellenek, de a .env-ben ott vannak).
TITKOS_NEVEK = KULCS_NEVEK + ("TWITTER_BEARER_TOKEN", "TWITTER_CLIENT_ID", "TWITTER_CLIENT_SECRET")
KIND = "tweet"
MAX_HOSSZ = 280
LINK_HOSSZ = 23                      # az X minden linket t.co-ra rövidít: 23 karakter, akármilyen hosszú
NAPI_MAX_ALAP = 10
MIN_SZO = 3
IDOKORLAT = 20.0

TEENDO = ("MÉG NEM ment ki. Mondd el a Kommandantnak a TELJES szöveget és a karakterszámot, és hogy a kabin "
          "munkapadján hagyja jóvá (Jóváhagyom), vagy mondja ki: „posztold ki a tervezetet: <az első pár szó>”. "
          "Egy szavas igen erre nem elég (nyilvános és visszavonhatatlan); addig ne írd, hogy kiment.")


# ══ KULCSOK ═════════════════════════════════════════════════════════════════

def kulcsok() -> dict | None:
    """A négy OAuth 1.0a kulcs env-ből, vagy None, ha bármelyik hiányzik."""
    k = {n: (os.environ.get(n) or "").strip() for n in KULCS_NEVEK}
    return k if all(k.values()) else None


def beallitva() -> bool:
    return kulcsok() is not None


def _titkok() -> list[str]:
    return sorted({v for n in TITKOS_NEVEK if len(v := (os.environ.get(n) or "").strip()) >= 6}, key=len, reverse=True)


_OAUTH_FEJ = re.compile(r"OAuth\s+oauth_[^\n]*", re.IGNORECASE)
_OAUTH_PARAM = re.compile(r"oauth_(signature|token|consumer_key)=\"?[^\s\",&]+", re.IGNORECASE)


def _tisztit(szoveg) -> str:
    """Minden kifelé menő szöveg ezen megy át: a kulcsok értéke és az aláírt fejléc nem szivároghat."""
    s = str(szoveg or "")
    for t in _titkok():
        s = s.replace(t, "***")
    s = _OAUTH_FEJ.sub("OAuth ***", s)
    return _OAUTH_PARAM.sub(lambda m: f"oauth_{m.group(1)}=***", s)


def napi_max() -> int:
    try:
        return max(0, int(os.environ.get("TWITTER_NAPI_MAX", NAPI_MAX_ALAP)))
    except (TypeError, ValueError):
        return NAPI_MAX_ALAP


# ══ HOSSZ (az X súlyozott számlálása, twitter-text v3) ═════════════════════

#: Ezek a kódpontok 1-nek számítanak, minden más (CJK, emoji…) 2-nek. A magyar ékezetes betűk (ő, ű is) 1.
_EGYES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
_TLD = "com|net|org|hu|eu|io|ai|news|de|at|uk|info|app|dev|co|me|ly|gov|edu"
_URL = re.compile(r"(?:https?://|www\.)[^\s]+|\b(?:[a-z0-9][a-z0-9-]*\.)+(?:" + _TLD + r")\b(?:/[^\s]*)?",
                  re.IGNORECASE)
_URL_VEGE = ".,;:!?)]}'\"»…"


def _kodpont_suly(s: str) -> int:
    n, elozo_zwj, elozo_ri = 0, False, False
    for ch in s:
        c = ord(ch)
        if elozo_zwj:                                  # ZWJ-sorozat: az egész egy emoji
            elozo_zwj = False
            continue
        if c == 0x200D:
            elozo_zwj = True
            continue
        if c in (0xFE0E, 0xFE0F) or 0x1F3FB <= c <= 0x1F3FF or 0xE0020 <= c <= 0xE007F:
            continue                                   # változatjelölő, bőrszín, zászló-címke: az emojihoz tartozik
        if 0x1F1E6 <= c <= 0x1F1FF:                    # zászló = két regionális betű, együtt egy emoji
            if elozo_ri:
                elozo_ri = False
                continue
            elozo_ri = True
        else:
            elozo_ri = False
        n += 1 if any(a <= c <= b for a, b in _EGYES) else 2
    return n


def hossz(szoveg: str) -> int:
    """Az X szerinti hossz: NFC, a link 23 karakter, CJK és emoji 2 (az összetett emoji is egy)."""
    s = unicodedata.normalize("NFC", szoveg or "")
    n, poz = 0, 0
    for m in _URL.finditer(s):
        url = m.group(0)
        mag = url.rstrip(_URL_VEGE)
        n += _kodpont_suly(s[poz:m.start()]) + LINK_HOSSZ + _kodpont_suly(url[len(mag):])
        poz = m.end()
    return n + _kodpont_suly(s[poz:])


def osszerak(szoveg: str, link: str = "") -> str:
    """A kiposztolandó szöveg: a tervezet + (ha van) a link egy új sorban. NFC, a széleken szóköz nélkül."""
    s = unicodedata.normalize("NFC", str(szoveg or "")).strip()
    link = str(link or "").strip()
    if link and link not in s:
        s = f"{s}\n{link}" if s else link
    return s


def ellenoriz(szoveg: str) -> str | None:
    """None, ha mehet; különben a kimondott ok."""
    if not (szoveg or "").strip():
        return "üres a tweet szövege"
    n = hossz(szoveg)
    if n > MAX_HOSSZ:
        return (f"túl hosszú: {n} karakter az X számítása szerint (legfeljebb {MAX_HOSSZ}; a link {LINK_HOSSZ}-nak "
                f"számít) — rövidíts {n - MAX_HOSSZ} karakterrel")
    return None


# ══ OAUTH 1.0a ÉS AZ X API ═════════════════════════════════════════════════

def fejlec(method: str, url: str, k: dict | None = None, *, nonce: str | None = None,
           timestamp: str | None = None) -> str:
    """Az OAuth 1.0a `Authorization` fejléc (HMAC-SHA1). A JSON-törzs nem része az aláírásnak (csak a form-törzs
    lenne), a lekérdezés-paraméterek igen — az `oauthlib` mindkettőt a szabvány szerint kezeli."""
    from oauthlib.oauth1 import Client
    k = k or kulcsok()
    if not k:
        raise RuntimeError("nincs X-kulcs")
    if _OAUTHLIB_NAPLO.level < logging.WARNING:
        _OAUTHLIB_NAPLO.setLevel(logging.WARNING)
    c = Client(k["TWITTER_API_KEY"], client_secret=k["TWITTER_API_SECRET"],
               resource_owner_key=k["TWITTER_ACCESS_TOKEN"], resource_owner_secret=k["TWITTER_ACCESS_TOKEN_SECRET"],
               signature_method="HMAC-SHA1", nonce=nonce, timestamp=timestamp)
    _, fej, _ = c.sign(url, http_method=method.upper())
    return fej["Authorization"]


def _visszaall(fej) -> str:
    """A 429 utáni nyitás ideje (Budapest), a válasz fejlécéből; üres, ha nincs."""
    for n in ("x-user-limit-24hour-reset", "x-app-limit-24hour-reset", "x-rate-limit-reset"):
        v = (fej or {}).get(n)
        if v:
            try:
                from zoneinfo import ZoneInfo
                t = datetime.fromtimestamp(int(v), tz=ZoneInfo("Europe/Budapest"))
                return t.strftime("%m-%d %H:%M")
            except (TypeError, ValueError, OSError):
                continue
    return ""


def _hiba_szoveg(status: int, adat: dict | None, nyers: str = "", fej=None) -> str:
    """Az X hibája a Kommandant nyelvén, mit kell tenni vele."""
    adat = adat if isinstance(adat, dict) else {}
    hibak = adat.get("errors") if isinstance(adat.get("errors"), list) else []
    reszek = [str(adat.get(k) or "") for k in ("title", "detail")] + [
        str(e.get("message") or "") for e in hibak if isinstance(e, dict)]
    x = "; ".join(dict.fromkeys(r for r in reszek if r)) or (nyers or "").strip()[:200]
    kis = (x + " " + str(adat.get("type") or "")).lower()
    if status == 401:
        ok = ("az X elutasította a hitelesítést (401): a kulcs vagy a token érvénytelen, lejárt vagy visszavonták — "
              "a developer.x.com-on újra kell generálni, és a Bridge-en (TWITTER_*) cserélni")
    elif status == 402 or "credit" in kis:
        ok = (f"elfogyott az X API-kredit ({status}) — a developer.x.com → Billing alatt tölthető fel "
              f"(használatalapú, ~0,01 $/tweet)")
    elif status == 403 and "duplicate" in kis:
        ok = "az X nem enged kétszer ugyanolyan szöveget (403, duplicate content) — változtass a szövegen"
    elif status == 403 and ("permission" in kis or "oauth1" in kis or "not permitted" in kis):
        ok = ("az alkalmazásnak nincs írásjoga (403) — developer.x.com → az app User authentication settings: "
              "Read and write, utána az Access Tokent újra kell generálni")
    elif status == 403:
        ok = "az X megtagadta a posztot (403)"
    elif status == 429:
        mikor = _visszaall(fej)
        ok = ("túl sok kérés (429) — az X korlátja" + (f" {mikor}-kor nyílik újra" if mikor else " később nyílik újra")
              + (" (a napi/havi API-keret fogyott el)" if "usagecap" in kis.replace(" ", "") else ""))
    elif status >= 500:
        ok = f"az X szervere hibázott ({status}) — később újra próbálható"
    else:
        ok = f"az X {status}-et adott"
    return _tisztit(f"{ok}" + (f" — X: {x[:240]}" if x else ""))


async def _keres(kliens, method: str, url: str, **kw):
    import httpx
    if kliens is not None:
        return await kliens.request(method, url, **kw)
    async with httpx.AsyncClient(timeout=IDOKORLAT) as c:
        return await c.request(method, url, **kw)


def _json(r) -> dict:
    try:
        d = r.json()
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


async def posztol(szoveg: str, *, kliens=None) -> dict:
    """A tényleges posztolás. → {"ok", "id", "url", "szoveg"} vagy {"error", "status"}. CSAK a `dont` hívja."""
    import httpx
    k = kulcsok()
    if not k:
        return {"error": "nincs X-kulcs a Bridge-en (" + ", ".join(KULCS_NEVEK) + " env) — a posztolás nem megy",
                "status": None}
    hiba = ellenoriz(szoveg)
    if hiba:
        return {"error": hiba, "status": None}
    try:
        r = await _keres(kliens, "POST", TWEET_URL, json={"text": szoveg},
                         headers={"Authorization": fejlec("POST", TWEET_URL, k), "Content-Type": "application/json"})
    except httpx.HTTPError as e:
        return {"error": _tisztit(f"az X nem érhető el ({type(e).__name__}) — nem biztos, hogy kiment: nézd meg a "
                                  f"fiókot, mielőtt újra jóváhagyod"), "status": None}
    if r.status_code in (200, 201):
        d = _json(r).get("data") or {}
        tid = str(d.get("id") or "")
        if tid:
            return {"ok": True, "id": tid, "url": STATUS_URL.format(id=tid), "szoveg": str(d.get("text") or szoveg)}
        return {"error": f"az X {r.status_code}-et adott, de tweet-azonosító nélkül", "status": r.status_code}
    return {"error": _hiba_szoveg(r.status_code, _json(r), r.text, r.headers), "status": r.status_code}


async def en(*, kliens=None) -> dict:
    """Csak olvasó hitelesítés-ellenőrzés: `GET /2/users/me` a felhasználói tokennel — melyik fiók van bekötve."""
    import httpx
    k = kulcsok()
    if not k:
        return {"error": "nincs X-kulcs a Bridge-en (" + ", ".join(KULCS_NEVEK) + " env)", "status": None}
    try:
        r = await _keres(kliens, "GET", EN_URL, headers={"Authorization": fejlec("GET", EN_URL, k)})
    except httpx.HTTPError as e:
        return {"error": _tisztit(f"az X nem érhető el ({type(e).__name__})"), "status": None}
    if r.status_code == 200:
        d = _json(r).get("data") or {}
        return {"ok": True, "id": str(d.get("id") or ""), "username": str(d.get("username") or ""),
                "name": str(d.get("name") or ""), "url": f"https://x.com/{d.get('username') or ''}"}
    return {"error": _hiba_szoveg(r.status_code, _json(r), r.text, r.headers), "status": r.status_code}


# ══ A TÁR: action_drafts (kind='tweet') ═════════════════════════════════════

_KIND_CHECK = re.compile(r"CHECK\s*\(\s*kind\s+IN\s*\(([^)]*)\)\s*\)", re.IGNORECASE)


def schema_biztosit(conn) -> bool:
    """A meglévő `action_drafts` `kind` CHECK-je engedje a 'tweet'-et. SQLite-ban CHECK nem módosítható, ezért a tábla
    EGYSZER átépül (új tábla a bővített sémával → a sorok átmásolva, azonos id-vel → a régi törölve → átnevezve), egy
    tranzakcióban; ha már engedi, semmi nem történik. → True, ha most épült át. Új tábla a végén nincs."""
    sor = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'action_drafts'").fetchone()
    if not sor or f"'{KIND}'" in (sor[0] or ""):
        return False
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'action_drafts'").fetchone()[0]
        if f"'{KIND}'" in sql:                       # közben egy másik folyamat már átépítette
            conn.rollback()
            return False
        if not _KIND_CHECK.search(sql):
            raise RuntimeError("az action_drafts sémájában nincs ismert kind-CHECK — kézi átnézés kell")
        uj = _KIND_CHECK.sub(lambda m: f"CHECK(kind IN ({m.group(1).rstrip()},'{KIND}'))", sql, count=1)
        uj = re.sub(r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?action_drafts[\"`\]]?",
                    "CREATE TABLE action_drafts__uj", uj, count=1, flags=re.IGNORECASE)
        oszlopok = ", ".join(f'"{r[1]}"' for r in conn.execute("PRAGMA table_info(action_drafts)"))
        conn.execute(uj)
        conn.execute(f"INSERT INTO action_drafts__uj ({oszlopok}) SELECT {oszlopok} FROM action_drafts")
        conn.execute("DROP TABLE action_drafts")
        conn.execute("ALTER TABLE action_drafts__uj RENAME TO action_drafts")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info("twitter_poszt: az action_drafts kind-CHECK-je bővült a 'tweet'-tel (a sorok megmaradtak)")
    return True


def _iso(d: datetime | None = None) -> str:
    return (d or datetime.now(timezone.utc)).isoformat()


def _nap_kezdet(most: datetime | None = None) -> str:
    """A budapesti naptári nap kezdete, UTC ISO-ban (a `decided_at` is UTC ISO, így szövegként összevethető)."""
    from zoneinfo import ZoneInfo
    bp = (most or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/Budapest"))
    return bp.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()


def _payload(sor) -> dict:
    try:
        p = json.loads(sor["payload_json"] or "{}")
        return p if isinstance(p, dict) else {}
    except (TypeError, ValueError):
        return {}


def cim(szoveg: str) -> str:
    s = " ".join(str(szoveg or "").split())
    return s if len(s) <= 90 else s[:89].rstrip() + "…"


def tervezet(get_db, szoveg: str, link: str = "", *, forras: str, source_message_id: str, chat_id: str = "") -> dict:
    """Tweet-TERVEZET a meglévő `action_drafts`-ba (pending). Nem posztol. → az eszköz válasza (dict)."""
    link = str(link or "").strip()
    if link and not re.fullmatch(r"https?://\S+", link):
        return {"error": "a link http:// vagy https:// címmel kezdődjön, szóköz nélkül"}
    s = osszerak(szoveg, link)
    hiba = ellenoriz(s)
    if hiba:
        return {"error": hiba, "karakter": hossz(s)}
    n = hossz(s)
    payload = {"szoveg": s, "link": link, "karakter": n, "details": s, "forras": forras, "chat_id": str(chat_id or "")}
    conn = get_db()
    try:
        schema_biztosit(conn)
        cur = conn.execute(
            "INSERT INTO action_drafts (source_message_id, kind, title, due_at, payload_json, status, created_at, "
            "decided_at) VALUES (?, ?, ?, '', ?, 'pending', ?, '')",
            (source_message_id, KIND, cim(s), json.dumps(payload, ensure_ascii=False), _iso()))
        conn.commit()
        did = int(cur.lastrowid)
    finally:
        conn.close()
    logger.info("twitter_poszt: tweet-tervezet #%d (%d karakter, %s)", did, n, forras)
    return {"status": "jovahagyasra_var", "draft_id": did, "posztolva": False, "szoveg": s,
            "karakter": f"{n}/{MAX_HOSSZ}", "teendo": TEENDO}


def fuggok(get_db) -> list[dict]:
    """A függő tweet-tervezetek, a legfrissebb elöl."""
    conn = get_db()
    try:
        sorok = conn.execute("SELECT id, title, payload_json, created_at FROM action_drafts WHERE kind = ? AND "
                             "status = 'pending' ORDER BY id DESC LIMIT 50", (KIND,)).fetchall()
    except Exception:  # noqa: BLE001 — a tábla hiánya = nincs függő tervezet
        return []
    finally:
        conn.close()
    return [{"id": r["id"], "szoveg": str(_payload(r).get("szoveg") or r["title"]), "ido": r["created_at"]}
            for r in sorok]


def napi_szam(conn, most: datetime | None = None) -> int:
    """Ma (Budapest-nap) kiment vagy épp kimenő tweetek száma."""
    return int(conn.execute("SELECT COUNT(*) FROM action_drafts WHERE kind = ? AND status IN ('approved', 'created') "
                            "AND decided_at >= ?", (KIND, _nap_kezdet(most))).fetchone()[0])


def _frissit(conn, did: int, statusz: str, decided_at: str, ujdonsag: dict) -> None:
    sor = conn.execute("SELECT payload_json FROM action_drafts WHERE id = ?", (did,)).fetchone()
    p = {**(_payload(sor) if sor else {}), **ujdonsag}
    conn.execute("UPDATE action_drafts SET status = ?, decided_at = ?, payload_json = ? WHERE id = ?",
                 (statusz, decided_at, json.dumps(p, ensure_ascii=False), did))


async def dont(get_db, draft_id: int, dontes: str, *, ertesit=None, ki: str = "kabin", kliens=None) -> dict:
    """A Kommandant döntése egy tweet-tervezetről: 'approve' → posztol, 'reject' → elveti (POST nélkül).
    `ertesit(chat_id, szoveg)` [async]: az eredmény a Feldwebel-szálba és a Telegramra (a hívó köti be)."""
    if dontes not in ("approve", "reject"):
        return {"error": "a döntés 'approve' vagy 'reject'"}
    conn = get_db()
    try:
        sor = conn.execute("SELECT id, kind, status, payload_json FROM action_drafts WHERE id = ?",
                           (int(draft_id),)).fetchone()
        if not sor or sor["kind"] != KIND:
            return {"error": f"nincs ilyen tweet-tervezet: #{draft_id}"}
        p = _payload(sor)
        szoveg = str(p.get("szoveg") or "")
        if sor["status"] != "pending":
            return {"error": f"a #{draft_id} tervezet már nem vár döntésre (állapot: {sor['status']})",
                    "draft_id": int(draft_id), "status": sor["status"]}
        most = _iso()
        if dontes == "reject":
            _frissit(conn, int(draft_id), "rejected", most, {"elvetette": ki})
            conn.commit()
            logger.info("twitter_poszt: #%s elvetve (%s)", draft_id, ki)
            return {"draft_id": int(draft_id), "status": "rejected", "posztolva": False}
        hiba = ellenoriz(szoveg) or (None if beallitva() else
                                     "nincs X-kulcs a Bridge-en (" + ", ".join(KULCS_NEVEK) + " env) — a tervezet függő "
                                     "marad")
        if hiba:
            return {"error": hiba, "draft_id": int(draft_id), "status": "pending", "posztolva": False}
        plafon = napi_max()
        # FOGLALÁS egy utasításban: csak függőből, és csak ha a mai (kiment + épp kimenő) szám a plafon alatt van
        cur = conn.execute(
            "UPDATE action_drafts SET status = 'approved', decided_at = ? WHERE id = ? AND status = 'pending' AND "
            "(SELECT COUNT(*) FROM action_drafts WHERE kind = ? AND status IN ('approved', 'created') AND "
            "decided_at >= ?) < ?", (most, int(draft_id), KIND, _nap_kezdet(), plafon))
        conn.commit()
        if cur.rowcount != 1:
            allapot = conn.execute("SELECT status FROM action_drafts WHERE id = ?", (int(draft_id),)).fetchone()[0]
            if allapot == "pending":
                return {"error": f"elérted a napi plafont: ma már {napi_szam(conn)} tweet ment ki (TWITTER_NAPI_MAX="
                                 f"{plafon}) — a tervezet függő marad, holnap jóváhagyható",
                        "draft_id": int(draft_id), "status": "pending", "posztolva": False}
            return {"error": f"a #{draft_id} tervezet már nem vár döntésre (állapot: {allapot})",
                    "draft_id": int(draft_id), "status": allapot}
    finally:
        conn.close()

    res = await posztol(szoveg, kliens=kliens)
    conn = get_db()
    try:
        if res.get("ok"):
            _frissit(conn, int(draft_id), "created", most,
                     {"tweet_id": res["id"], "url": res["url"], "posztolva": _iso(), "jovahagyta": ki})
            uzenet = f"🐦 Kiposztoltam az X-re: {res['url']}\n«{szoveg}»"
            statusz = "created"
        else:
            _frissit(conn, int(draft_id), "pending", "",
                     {"utolso_hiba": res.get("error"), "utolso_proba": _iso(), "utolso_status": res.get("status")})
            uzenet = (f"❌ Nem ment ki a tweet (#{draft_id}): {res.get('error')}\n"
                      f"A tervezet a kabin munkapadján maradt, újra jóváhagyható.")
            statusz = "pending"
        conn.commit()
    finally:
        conn.close()
    logger.info("twitter_poszt: #%s %s (%s)", draft_id, "KIMENT" if statusz == "created" else "bukott", ki)
    if ertesit:
        try:
            await ertesit(str(p.get("chat_id") or ""), uzenet)
        except Exception as e:  # noqa: BLE001 — az értesítés hibája a posztot nem csinálja vissza
            logger.warning("twitter_poszt: az értesítés nem ment ki: %s", _tisztit(e))
    out = {"draft_id": int(draft_id), "status": statusz, "posztolva": statusz == "created", "uzenet": uzenet}
    if res.get("ok"):
        out.update(url=res["url"], tweet_id=res["id"])
    else:
        out.update(error=res.get("error"), x_status=res.get("status"))
    return out


# ══ A FELDWEBEL-SZÁL ÉS A KIFEJEZETT MONDAT ════════════════════════════════

def szalba(get_db, chat_id: str, szoveg: str) -> None:
    """Az eredmény a Feldwebel-szálba: az előzményébe (`telegram_history`, ezt látja a következő körben) és a kabin
    Feldwebel-ablakába (`messages`, feldwebel-* küldő). Hibánál csak log — a poszt attól még kiment."""
    conn = get_db()
    try:
        most = _iso()
        if chat_id:
            try:
                conn.execute("INSERT INTO telegram_history (chat_id, role, agent_id, content, timestamp) "
                             "VALUES (?, 'assistant', 'feldwebel', ?, ?)", (str(chat_id), szoveg, most))
            except Exception as e:  # noqa: BLE001 — Feldwebel nélkül nincs előzménytábla
                logger.info("twitter_poszt: a Feldwebel-előzmény kimaradt: %s", e)
        conn.execute("INSERT INTO messages (timestamp, sender, recipient, subject, message, priority) "
                     "VALUES (?, 'feldwebel-x', 'kommandant', 'Re: Feldwebel válasz · eszközök=tweet', ?, 'normal')",
                     (most, szoveg))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("twitter_poszt: a Feldwebel-szálba írás bukott: %s", e)
    finally:
        conn.close()


def _ekezet_nelkul(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _szavak(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _ekezet_nelkul(s))


_KIFEJEZETT = re.compile(
    r"^\W*(?:posztold|tedd|kuldd)\s+ki\s+(?:a\s+|az\s+)?(?:tweet-?|twitter-?|x-?)?"
    r"(?:tervezetet|tweetet|twitet|posztot)\b\s*[:,.;\-–—]*\s*(?P<idezet>.*)$", re.DOTALL)


def kifejezett(szoveg: str) -> str | None:
    """„posztold ki a tervezetet: <első szavak>” → az idézett rész (üres is lehet); None, ha nem ilyen mondat. A tagadás
    („ne posztold ki…”) és a kérdés nem ilyen: a mondatnak ezzel kell kezdődnie."""
    m = _KIFEJEZETT.match(_ekezet_nelkul((szoveg or "").strip()))
    return m.group("idezet").strip() if m else None


def illeszt(idezet: str, tervezetek: list[dict]) -> tuple[list[dict], str | None]:
    """Mely függő tervezetek kezdődnek az idézett szavakkal. → (találatok, hiba)."""
    q = _szavak(idezet)
    if not q:
        return [], f"mondd meg, melyiket: „posztold ki a tervezetet: <az első {MIN_SZO} szó>”"
    talalt = []
    for t in tervezetek:
        d = _szavak(t["szoveg"])
        if len(q) >= MIN_SZO or len(q) == len(d):
            if d[:len(q)] == q:
                talalt.append(t)
    if not talalt and len(q) < MIN_SZO:
        return [], f"legalább {MIN_SZO} szót mondj a tervezet elejéről (most {len(q)} volt)"
    return talalt, None


def _lista_szoveg(tervezetek: list[dict], n: int = 3) -> str:
    return "; ".join(f"#{t['id']} «{cim(t['szoveg'])[:60]}»" for t in tervezetek[:n])


async def kezel(ctx, szoveg: str, chat_id) -> str | None:
    """A Feldwebel elején, LLM nélkül: ha az üzenet a KIFEJEZETT mondat, a kód dönt (posztol, vagy kimondja, miért
    nem). → a válasz szövege (elküldve és az előzménybe írva), vagy None, ha az üzenet nem ilyen."""
    from html import escape as html_escape
    idezet = kifejezett(szoveg)
    if idezet is None:
        return None
    from cimzett import fejlec as cim_fejlec
    from feldwebel.history import add_message
    add_message(chat_id, "user", szoveg)
    tervezetek = fuggok(ctx.get_db)
    if not tervezetek:
        uzenet = "Nincs függő tweet-tervezet, nincs mit kiposztolni."
    else:
        talalt, hiba = illeszt(idezet, tervezetek)
        if hiba:
            uzenet = f"Nem posztoltam: {hiba}. Függő tervezetek: {_lista_szoveg(tervezetek)}"
        elif not talalt:
            uzenet = (f"Nem posztoltam: egyik függő tervezet sem így kezdődik («{idezet[:80]}»). "
                      f"Függő tervezetek: {_lista_szoveg(tervezetek)}")
        elif len(talalt) > 1:
            uzenet = (f"Nem posztoltam: {len(talalt)} tervezet is így kezdődik ({_lista_szoveg(talalt, 5)}) — mondj "
                      f"még pár szót, vagy hagyd jóvá a kabin munkapadján.")
        else:
            res = await dont(ctx.get_db, talalt[0]["id"], "approve", ki="kifejezett mondat")
            uzenet = res.get("uzenet") or f"❌ Nem ment ki a tweet (#{talalt[0]['id']}): {res.get('error')}"
    await ctx.telegram_push(f"{cim_fejlec('feldwebel')}\n\n{html_escape(uzenet)}")
    add_message(chat_id, "assistant", uzenet, "feldwebel")
    return uzenet
