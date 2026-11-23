"""OPERATION HOG A BRIDGE-BŐL (2026-11-23) — egy nagy elérésű X-posztra (Elon Musk, egy hírfiók) adatalapú válasz:
ÁLLANDÓ, nyilvános Echolot-elemzőoldal + az X algoritmusára tervezett komment, a Kommandant EGY jóváhagyásával.

A Kommandant (11-23, egy képernyőképpel): „ugye az echolotban van a HOG twitter tervezet — na ilyet kellene tudnia
csinálni.” Pontosítás: „Tehát Elon tweetjére egy algoritmikusan tervezett twittet — linkkel.”

AZ ÚT
-----
1. BEMENET (Feldwebel, `hog_keszit`): tweet-link, tweet-szöveg vagy KÉPERNYŐKÉP (`[Melléklet #ID]`). A hívás csak
   elindítja a háttérfutást (`inditas`) — a Feldwebel (Haiku) nem ír HOG-ot.
2. A FUTÁS (`futtat`, háttérben, ~2–3 perc), előbb adat, aztán determinisztikus, a végén AI:
   a) képernyőkép → a meglévő képelemzés (`server._analyze_image`) JSON-ban kiolvassa a szöveget, a szerzőt, a @handle-t;
   b) a poszt helye: a linkből a tweet-ID; ha nincs link, az Echolot `search_social` (csak az a találat számít, amelyik
      URL-jében a szerző handle-je áll — a továbbposztolók nem); ha így sincs meg, SAJÁT POSZT mód, és a Feldwebel egy
      mondatban elkéri a linket (`hog_keszit(hog_id=…, poszt_url=…)` utólag válasszá teszi);
   c) a saját fiók követőszáma (`twitter_poszt.en(metrika=True)`, `public_metrics`) → a `hog_prepare`
      `own_follower_count`-ja (cold-start, X: ≤ 1000);
   d) TERVEZÉS (Sonnet, kicsi hívás): a `hog_prepare` kezelései — `keywords` (replace), `protagonist_hint`, `anchors`,
      `lang`. Élesben mérve (11-23, Elon/NVIDIA): kezelés nélkül a kulcsszó „corrected, newsroom, quoted” lett;
   e) `hog_prepare` (Echolot, szerkesztői kulcs) → befagyasztott csomag (`draft_id`, források, tények, szabályok);
   f) ÍRÁS (Sonnet 5.5, kötött modell): a csomag `writing_rules` / `comment_rules` / `playbook` szerint a cím, a kártya-
      főcím, az elemzés (3–5 bekezdés, minden állítás `[n]`), ≥ 12 tényvonal, a komment (X-válasz: szám · kontraszt ·
      [kártyakép] · kérdés, LINK NÉLKÜL) és az idézet-szöveg. A `[n]`-eket és az URL-eket a KÓD ellenőrzi
      (`ellenoriz`, a `hog_publish` `invalid_references` szabályának másolata) — hibánál a modell javít (≤ 2 kör);
   g) a tervezet a meglévő `action_drafts`-ba (`kind='hog'`, pending; a kind-CHECK a tweet mintájára egyszer bővül) →
      kabin-kártya „🐗 HOG” + Telegram-értesítés.
3. EGY JÓVÁHAGYÁS, KÉT NYILVÁNOS LÉPÉS (`dont`, csak a kabin gombja vagy a „posztold ki a tervezetet: <3+ szó>”):
   a) `hog_publish` → állandó oldal + kártya, élő ellenőrzéssel (ha nem él, tweet NEM megy: az X egyszer olvassa a kártyát);
   b) a tweet a doktrína szerint:
      * VÁLASZ (van tweet-ID): a komment + a kártyakép (`media_feltolt`) `reply.in_reply_to_tweet_id`-val; a LINK
        (`reply_text`) 2–3 perc múlva a saját válasz alá (`HOG_LINK_KESES_S`, alap 150 s). Ha az X a választ 403-mal
        elutasítja (önkiszolgáló fióknál API-ból csak az válaszolhat, akit a szerző megemlített — docs.x.com, „summoned
        replies”, 2026-02-23), KIMONDVA idézetre esik vissza (`quote_tweet_id`, az idézet-szöveg + link);
      * IDÉZET: `quote_tweet_id`, az idézet-szöveg + link (a link a kártya);
      * SAJÁT POSZT: az idézet-szöveg + link, önállóan (X-en a saját poszt a komment-mód idézet-változata).
   c) MINDIG ott a „Válasz a telefonról” link (`x.com/intent/post?in_reply_to=…&text=…`; idézetnél `&url=…`): egy
      koppintás, és a Kommandant a saját fiókjából posztol — az API-korlátozástól független.
   Az „Elvetem”-re semmi nem megy ki. Az eredmény (oldal-URL, tweet-URL vagy hiba) a Feldwebel-szálba és a Telegramra.
   Ha az oldal kint van, de a tweet bukott: a kártya függő marad (oldal-URL-lel, a hibával, a telefon-linkkel) — a
   gomb újrapróbálja (a `hog_publish` idempotens), az elvetés lezárja.

KULCS: `ECHOLOT_EDITOR_KEY` (a Bridge env-je; a Railway-változót a Kommandant állítja). Csak a hívás argumentumába kerül;
logba, válaszba, hibaszövegbe, payloadba SOHA (`_tisztit`).

KÖLTÉS (mérve, `llm_kapu` valós könyvelése): a tervezés + az írás (+ javítás) Sonnet 5.5-ön; a kártya és az értesítés
kimondja. Az X ~0,01 $/tweet (a válasz + a link = 2 tweet).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import twitter_poszt

logger = logging.getLogger("hog")

KIND = "hog"
MODELL = "claude-sonnet-5-5"
NYELVEK = ("hu", "en", "de", "es", "zh", "fr", "pl", "ru", "uk", "it", "el", "tr")   # az Echolot 12 kiadása
MIN_TENY = 12
JAVITO_KOR = 2
PREPARE_IDO = 130.0              # a hog_prepare saját kerete 85 s + hálózat
PUBLISH_IDO = 120.0              # a hog_publish saját kerete 80 s + élő ellenőrzés
KERESO_IDO = 60.0
LINK_KESES_ALAP = 150            # a doktrína: „a link a saját válasz alá, 2–3 perc múlva”
FORRAS_LEAD = 150                # az író csomagjában a lead hossza
MAX_PARHUZAMOS = 2

TEENDO_INDULT = ("A HOG-futás ELINDULT a háttérben (Sonnet, kb. 2–3 perc). Mondd el a Kommandantnak röviden: a kész "
                 "tervezet a kabin munkapadjára kerül (🐗 HOG), és a Telegramon szólok. Semmi nem ment ki: a nyilvános "
                 "oldal és a tweet csak a jóváhagyása után (kabin-gomb, vagy „posztold ki a tervezetet: <első 3 szó>”). "
                 "A kommentet NE te írd meg, és ne mondd, hogy kiment.")


class HogHiba(RuntimeError):
    """Kimondott hiba a futásban (az üzenete a Kommandantnak szól)."""


# ══ KULCS ÉS TISZTÍTÁS ═══════════════════════════════════════════════════════

def kulcs() -> str:
    return (os.environ.get("ECHOLOT_EDITOR_KEY") or "").strip()


def _tisztit(szoveg) -> str:
    """Minden kifelé menő szöveg: a szerkesztői kulcs és az X-kulcsok nem szivároghatnak."""
    s = str(szoveg or "")
    k = kulcs()
    if len(k) >= 4:
        s = s.replace(k, "***")
    return twitter_poszt._tisztit(s)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ══ AZ X-POSZT AZONOSÍTÁSA ═════════════════════════════════════════════════

_STATUS = re.compile(r"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d{5,25})",
                     re.IGNORECASE)
_HANDLE = re.compile(r"@([A-Za-z0-9_]{1,15})\b")


def poszt_azonosito(url: str) -> tuple[str, str] | None:
    """(handle, tweet_id) egy X-linkből; None, ha nem X-poszt linkje."""
    m = _STATUS.search(str(url or ""))
    return (m.group(1), m.group(2)) if m else None


def handle_ki(szoveg: str) -> str:
    m = _HANDLE.search(str(szoveg or ""))
    return m.group(1) if m else ""


def szerzo_jelzes(szerzo: str, handle: str) -> str:
    """A `hog_prepare` `author_hint`-je: név + @handle, a handle egyszer."""
    szerzo, handle = " ".join(str(szerzo or "").split()), str(handle or "").lstrip("@")
    if handle and f"@{handle}".lower() not in szerzo.lower():
        szerzo = f"{szerzo} @{handle}".strip()
    return szerzo


def tweet_url(handle: str, tid: str) -> str:
    return f"https://x.com/{handle or 'i'}/status/{tid}"


def intent_valasz(tid: str, szoveg: str) -> str:
    """„Válasz a telefonról”: az X-app a kitöltött válasszal nyílik, a Kommandant nyomja meg a Post gombot."""
    return f"https://x.com/intent/post?in_reply_to={quote(str(tid))}&text={quote(szoveg or '', safe='')}"


def intent_idezet(cel_url: str, szoveg: str) -> str:
    return f"https://x.com/intent/post?text={quote(szoveg or '', safe='')}&url={quote(cel_url or '', safe='')}"


def intent_sajat(szoveg: str) -> str:
    return f"https://x.com/intent/post?text={quote(szoveg or '', safe='')}"


def _ekezet_nelkul(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _szavak(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", _ekezet_nelkul(s)) if len(w) > 2]


_HOG_SZO = re.compile(r"(?<![a-z0-9])hog(?:-?(?:ot|ra|hoz|ja|nak|ot|os|gal))?(?![a-z0-9])")


def szandek(szoveg: str) -> bool:
    """HOG-kérés-e (LLM nélkül): a „HOG” szó (ragozva is: HOG-ot, HOG-ra…), de a „hogy”/„hogyan” nem. A Telegram-
    úton ez a Feldwebelhez viszi az üzenetet a Faber-szándékfelismerő előtt („csináljunk erre HOG-ot” nem kódkérés)."""
    return bool(_HOG_SZO.search(_ekezet_nelkul(szoveg)))


def nyelv_tipp(szoveg: str) -> str:
    """Tartalék nyelvbecslés (ha sem a Feldwebel, sem a tervező nem mondta): magyar betű/szó → hu, különben en."""
    s = str(szoveg or "")
    if re.search(r"[őűŐŰ]", s) or re.search(r"\b(és|hogy|nem|az|egy|van|volt)\b", s.lower()):
        return "hu"
    return "en"


# ══ AZ ECHOLOT (MCP) ═══════════════════════════════════════════════════════

async def _echolot_alap(tool: str, args: dict, timeout: float):
    import _echolot_client
    return await _echolot_client.mcp_call(tool, args, timeout=timeout)


#: A tesztek ezt cserélik hamisra (hálózat nélkül).
ECHOLOT_HIV = _echolot_alap


async def _echolot(tool: str, args: dict, timeout: float):
    try:
        r = await ECHOLOT_HIV(tool, args, timeout)
    except Exception as e:  # noqa: BLE001 — kimondott hiba, a kulcs nélkül
        # ELŐBB tisztít, UTÁNA vág: a vágás a kulcs közepén nem hagyhat meg egy darabot belőle
        raise HogHiba(f"az Echolot `{tool}` nem válaszolt ({type(e).__name__}: {_tisztit(e)[:300]})") from None
    if isinstance(r, str):
        try:
            r = json.loads(r)
        except ValueError:
            raise HogHiba(f"az Echolot `{tool}` nem JSON-t adott: {_tisztit(r)[:200]}") from None
    return r


# ══ A KÉPERNYŐKÉP ═════════════════════════════════════════════════════════

KEP_PROMPT = (
    "This image is a screenshot of a social-media post (usually X/Twitter). Read it and answer with ONE JSON object, "
    "nothing else: {\"platform\": \"x|other\", \"szerzo\": \"display name of the post's author\", \"handle\": \"@handle "
    "of the author exactly as shown\", \"szoveg\": \"the post's own text, VERBATIM, in its original language\", "
    "\"idezett\": {\"szerzo\": \"…\", \"handle\": \"@…\", \"szoveg\": \"verbatim text of the quoted post\"} or null, "
    "\"datum\": \"date and time as shown, or empty\", \"url\": \"a status link if visible, else empty\", "
    "\"nyelv\": \"ISO 639-1 code of the post's language\"}. Do not translate, do not summarise; replies and comments "
    "under the post are NOT part of it. Unreadable → empty string.")


def _json_ki(szoveg: str) -> dict | None:
    """Az első JSON-objektum egy modellválaszból (kódkerítéssel vagy anélkül)."""
    s = str(szoveg or "")
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.DOTALL)
    jelolt = m.group(1) if m else s[s.find("{"): s.rfind("}") + 1] if "{" in s and "}" in s else ""
    try:
        d = json.loads(jelolt)
        return d if isinstance(d, dict) else None
    except ValueError:
        return None


async def kep_olvas(get_db, melleklet_id, kepelemzo=None) -> dict:
    """A `[Melléklet #ID]` képe → {szerzo, handle, szoveg, idezett, datum, url, nyelv} (üres dict, ha nincs meg).
    A meglévő képelemzés (`server._analyze_image`) olvas; ha nincs (vagy bukik), a feltöltéskor készült átirat a tartalék."""
    try:
        mid = int(melleklet_id)
    except (TypeError, ValueError):
        return {}
    conn = get_db()
    try:
        sor = conn.execute("SELECT mime_type, content_text, content_base64 FROM uploads WHERE id = ?",
                           (mid,)).fetchone()
    except Exception:  # noqa: BLE001
        sor = None
    finally:
        conn.close()
    if not sor:
        return {}
    if kepelemzo and sor["content_base64"] and str(sor["mime_type"] or "").startswith("image/"):
        try:
            d = _json_ki(await kepelemzo(sor["content_base64"], sor["mime_type"], KEP_PROMPT))
        except Exception as e:  # noqa: BLE001
            logger.warning("hog: a képelemzés bukott (#%s): %s", mid, str(e)[:200])
            d = None
        if d and (d.get("szoveg") or d.get("handle")):
            d["forras"] = "kepelemzes"
            return d
    atirat = str(sor["content_text"] or "").strip()
    return {"szoveg": atirat[:4000], "handle": handle_ki(atirat), "forras": "atirat"} if atirat else {}


# ══ A POSZT MEGKERESÉSE (ha nincs link) ═════════════════════════════════════

async def poszt_keres(handle: str, szoveg: str) -> dict | None:
    """Az Echolot `search_social`-ja (korpusz + web): csak az a találat számít, amelyik URL-jében a SZERZŐ handle-je
    áll, és a szövege a poszt elejével egyezik (vagy a szerzőtől ez az egyetlen találat). → {url, tweet_id, handle}."""
    handle = (handle or "").lstrip("@")
    szavak = _szavak(szoveg)[:8]
    if not handle or not szavak:
        return None
    kerdesek = [f"{handle} {' '.join(szavak)}", " ".join(szavak)]
    for q in dict.fromkeys(kerdesek):
        try:
            r = await _echolot("search_social", {"query": q, "platforms": ["x"], "time_range": "week", "count": 10,
                                                 "brave_fallback": False}, KERESO_IDO)
        except HogHiba as e:
            logger.info("hog: a posztkeresés bukott: %s", e)
            continue
        talalt = []
        for p in (r.get("posts") or []) if isinstance(r, dict) else []:
            az = poszt_azonosito(p.get("url") or "")
            if not az or az[0].lower() != handle.lower():
                continue
            szoveg_p = set(_szavak(f"{p.get('text') or ''} {p.get('title') or ''}"))
            talalt.append((sum(w in szoveg_p for w in szavak[:5]), az))
        if talalt:
            talalt.sort(key=lambda t: -t[0])
            pont, (h, tid) = talalt[0]
            if pont >= min(3, len(szavak)) or len(talalt) == 1:
                return {"url": tweet_url(h, tid), "tweet_id": tid, "handle": h}
    return None


# ══ A SONNET (tervezés, írás, javítás) ═════════════════════════════════════

async def _sonnet(system: str, user: str, *, purpose: str, max_tokens: int = 8000, chat=None) -> tuple[str, float, list]:
    """Egy Sonnet-hívás a kapun át (KÖTÖTT modell, tartalék nélkül — a minőségből nem adunk lejjebb). → (szöveg, $, notes)"""
    import llm_kapu
    chat = chat or llm_kapu.chat
    payload = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "max_tokens": max_tokens}
    data, err, notes = await chat(payload, purpose=purpose, modell=MODELL)
    if err is not None or not data:
        raise HogHiba(f"a Sonnet nem válaszolt ({(err or {}).get('code')}: {str((err or {}).get('message'))[:200]})")
    szoveg = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return szoveg, float(data.get("koltes_usd") or (data.get("usage") or {}).get("koltes_usd") or 0.0), list(notes or [])


TERVEZO = """You prepare the call of Echolot's `hog_prepare` (OPERATION HOG) for one social-media post. The tool builds a
frozen press corpus around the post's EVENT; its automatic keywords come from the post's words, and a short post
("The mistake has been corrected") gives generic ones. Give the event's own terms. Answer with ONE JSON object only:
{"lang": "<the post's language, one of: hu en de es zh fr pl ru uk it el tr>",
 "keywords": ["<3-8 content keywords of the EVENT, in the post's language (English for English posts): names of
              companies/people/places, the key verb or noun — e.g. Nvidia, Jensen Huang, reinstates, CEO, board>"],
 "protagonist_hint": "<the person/organisation the event is about, if the post does not name it plainly; else empty>",
 "anchors": ["<0-4 most concrete names a direct report must contain (a town, a company, a person); [] if none>"],
 "post_text": "<the text to analyse: the post itself, and if it quotes another post, a blank line and that post's text
               — no labels, no brackets, no metadata>"}"""


def tervezo_uzenet(b: dict) -> str:
    sorok = [f"Author: {b.get('szerzo') or '?'} (@{(b.get('handle') or '').lstrip('@')})",
             f"Post text: {b.get('szoveg') or ''}"]
    if b.get("idezett"):
        i = b["idezett"]
        sorok.append(f"It quotes: {i.get('szerzo') or ''} (@{(i.get('handle') or '').lstrip('@')}): {i.get('szoveg') or ''}")
    if b.get("utasitas"):
        sorok.append(f"The editor's note (Hungarian): {b['utasitas']}")
    return "\n".join(sorok)


IRO = """You are the writer of OPERATION HOG for Echolot (echolotnews.com). From ONE frozen Echolot package you write:
(1) a PERMANENT public analysis page — title, card headline, analysis, fact sheet — and (2) the comment for X, designed
for X's ranking algorithm per the package playbook. The package is your ONLY source; you write every sentence.

HARD RULES (code checks them; a violation is sent back to you):
- Follow the package `writing_rules` (all of them), `comment_rules` and `playbook` (variants, forbidden, angle_template).
- Every factual sentence of analysis_text and every fact line carries [n] — n = a `sources[].n` listed in the package
  (or a web_sources n). Never an n that is not listed. No URL anywhere unless it is a listed source's url (prefer none).
- Language: everything in `lang` (except komment_hu / megjegyzes_hu: Hungarian).
- analysis_text: 3–5 paragraphs separated by a blank line; the first sentence carries the surprising number with its n
  and [n]; neutral register; describe how the press covered the event; never refute or defend the post's author.
- facts: at least 12 lines (12–25), each "kind: text [n]" — kind one of what, who, where_when, quote, numbers,
  positions, timeline, background, reaction, next, coverage — in that group order. Quotes VERBATIM from
  fact_material.quotes or a source's title/lead; reactions state WHAT was said, not only that someone reacted.
- NAMESAKES: check fact_material.actors descriptions. A description that does not fit the story (e.g. a footballer for a
  company's board chair, "printed circuit board" for a company board) is a wrong link: never repeat it; describe the
  person from the sources, or leave them out. Off-topic reports (other event, namesake) → exclude_refs, never cited.
- Numbers: coverage counts (reports, regions, languages, first report time) are Echolot measurements. A contrast is a
  measurement only if min_n_ok is true; groups with n < 5 are illustrations — call them that. If frames are not
  classified (corpus.classified = 0), do not claim any framing share: use coverage/timing numbers instead.
- CONTENT, NOT WORDING: the reader wants the information. A difference in WORDING (synonyms such as "CEO" vs "chief
  executive", one outlet's word choice) is not information — never build the angle, the card headline or the comment
  on it, even if it is a measured headline-term contrast. Choose a CONTENT number: what the coverage is about (how many
  reports name an actor or a claim — fact lines "named in N of M reports"), which storyline each region led with, who
  reported first and how much later the others followed, how many outlets ran one agency text. A bare count of
  articles is not a headline either: say what they reported.
- card_headline: ~60 characters, subject first, the number must be a package measurement — or give card_headline_ref
  (the [n] of the outlet whose number it is).
- comment_text = the X REPLY (variant "reply"): the template lines framing_number · contrast · reader_question (the card
  image is attached automatically — do not write it). One line each, separated by newlines. The first line is one
  surprising number from the data; the second a contrast between two regions/spheres; the third a question to the
  READERS (never to a Trump/Musk-calibre author). NO link, NO [n], NO @mention, at most one hashtag (prefer none), no
  engagement bait, no irony, no judgement adjectives. At most 260 characters.
- quote_text = your own text for the X QUOTE variant (framing_number · [contrast] · reader_question): the link with the
  card is appended automatically. Different wording from comment_text (duplicate text is penalised). NO link, NO [n].
  At most 240 characters.
- title: the page title in `lang`, subject first, the content not a label.
Answer with ONE JSON object only:
{"title": "…", "card_headline": "…", "card_headline_ref": null, "analysis_text": "…", "facts": ["kind: text [n]", …],
 "comment_text": "…", "quote_text": "…", "exclude_refs": [{"ref": n, "reason": "…"}], "slug": "",
 "komment_hu": "<Hungarian translation of comment_text>",
 "megjegyzes_hu": "<1–2 Hungarian sentences for the editor: the angle you chose and any caveat in the data>"}"""


def _rovid(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def irasi_csomag(pkg: dict, poszt: dict) -> dict:
    """A `hog_prepare` csomagja (élesben ~440 ezer karakter) → az írónak kellő rész (~25–30 ezer token): a szabályok és
    a tényanyag egészben, a források tömörítve (n, orgánum, ország, nyelv, idő, cím, lead), a régiók összesítve."""
    forrasok = []
    for s in pkg.get("sources") or []:
        d = {"n": s.get("n"), "src": s.get("source"), "cc": s.get("country"), "lang": s.get("lang"),
             "t": str(s.get("published_at") or "")[:16], "title": _rovid(s.get("title"), 200),
             "reg": s.get("region_label")}
        lead = _rovid(s.get("lead"), FORRAS_LEAD)
        if lead and lead != d["title"]:
            d["lead"] = lead
        for k in ("role", "kind", "frame", "syndication_group", "lean"):
            if s.get(k):
                d[k] = s[k]
        forrasok.append(d)
    web = [{"n": w.get("n"), "src": w.get("source") or w.get("outlet"), "title": _rovid(w.get("title"), 200),
            "lead": _rovid(w.get("lead") or w.get("description"), FORRAS_LEAD), "t": str(w.get("published_at") or "")[:16]}
           for w in pkg.get("web_sources") or []]
    regiok = []
    br = pkg.get("by_region") if isinstance(pkg.get("by_region"), dict) else {}
    for kulcs_, r in br.items():
        if not isinstance(r, dict):
            continue
        regiok.append({"region": kulcs_, "articles": r.get("articles"), "classified": r.get("articles_classified"),
                       "dominant_frame": r.get("dominant_frame"), "frames": r.get("frame_distribution") or {},
                       "headlines": [_rovid(h.get("title"), 120) for h in (r.get("headlines") or [])[:3]]})
    hs = dict(pkg.get("home_silence") or {}) if isinstance(pkg.get("home_silence"), dict) else pkg.get("home_silence")
    if isinstance(hs, dict) and isinstance(hs.get("measured_via"), dict):
        hs["measured_via"] = {k: v for k, v in hs["measured_via"].items() if k != "fts_query"}
    kontraszt = []
    for c in pkg.get("contrast_candidates") or []:
        c = dict(c)
        c["groups"] = [g for g in (c.get("groups") or []) if (g.get("n") or 0) >= 1][:10]
        kontraszt.append(c)
    pb = pkg.get("playbook") if isinstance(pkg.get("playbook"), dict) else {}
    cr = pkg.get("comment_rules") if isinstance(pkg.get("comment_rules"), dict) else {}
    return {
        "post": poszt,
        "lang": pkg.get("lang"), "status": pkg.get("status"), "coverage": pkg.get("coverage"),
        "note": pkg.get("note"), "notes": pkg.get("notes"), "gaps": (pkg.get("gaps") or [])[:10],
        "author": (pkg.get("author") or {}).get("label") if isinstance(pkg.get("author"), dict) else pkg.get("author"),
        "author_kind": pkg.get("author_kind"), "protagonist": pkg.get("protagonist"),
        "card_subject": pkg.get("card_subject"), "corpus": pkg.get("corpus"),
        "angle_suggestion": pkg.get("angle_suggestion"), "frames_total": pkg.get("frames_total"),
        "emotions_total": pkg.get("emotions_total"), "writing_rules": pkg.get("writing_rules"),
        "comment_rules": {k: cr.get(k) for k in ("template", "variants", "forbidden", "selected_angle", "note")},
        "playbook": {k: pb.get(k) for k in ("comment_allowed", "angle", "angle_template", "variants", "reach_note",
                                            "timing", "forbidden", "platform_objective")},
        "fact_material": pkg.get("fact_material"), "facts": pkg.get("facts"),
        "contrast_candidates": kontraszt, "timeline": (pkg.get("timeline") or [])[:25],
        "syndication": pkg.get("syndication"), "home_silence": hs, "primary_source": pkg.get("primary_source"),
        "claim_origin": pkg.get("claim_origin"), "attention_gap": pkg.get("attention_gap"),
        "by_region": regiok, "sources": forrasok, "web_sources": web,
    }


# ══ A HELYI ELLENŐRZÉS (a hog_publish szabályainak másolata) ══════════════

_REF = re.compile(r"\[(\d{1,4})\]")
#: Az URL-felismerés az Echolot `echolot.hog.publish._URL_RE` másolata (ugyanaz döntsön itt, mint ott).
_URL_KAR = r"[^\s<>\"'«»“”‘’\[\]]"
_URL_RE = re.compile(
    r"(?<![\w.+\-])[a-z][a-z0-9+.\-]*://" + _URL_KAR + r"+"
    r"|(?<![\w:/])//[a-z0-9\-]+(?:\.[a-z0-9\-]+)+" + _URL_KAR + r"*"
    r"|(?<![\w./\-])www\." + _URL_KAR + r"+"
    r"|(?P<csupasz>(?<![\w./\-])(?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+[a-z]{2,63}"
    r"(?::\d{1,5})?/" + _URL_KAR + r"*)", re.I)
_URL_VEG = ".,;:!?)»”’'\"…"
_UT_JEL_RE = re.compile(r"[\d\-_%~=&+]")
_UT_KITERJESZTES_RE = re.compile(r"\.(?:s?html?|php\d?|aspx?|jsp|cms|cgi|pdf|xml|rss|json|txt|csv|jpe?g|png|gif|webp|"
                                 r"svg|mp[34]|docx?|xlsx?|pptx?|zip)$", re.I)
TENY_FAJTAK = ("what", "who", "where_when", "quote", "numbers", "positions", "timeline", "background", "reaction",
               "next", "coverage")


def _utas_url(szoveg: str) -> bool:
    ut = str(szoveg or "").split("/", 1)[1] if "/" in str(szoveg or "") else ""
    if "?" in ut or "#" in ut:
        return True
    szegmensek = [s for s in ut.split("/") if s]
    if len(szegmensek) >= 2:
        return True
    return bool(szegmensek) and bool(_UT_JEL_RE.search(szegmensek[0]) or _UT_KITERJESZTES_RE.search(szegmensek[0]))


def _url_kulcs(url: str) -> str:
    u = str(url or "").strip().rstrip(_URL_VEG)
    if u.startswith("//"):
        u = "https:" + u
    try:
        p = urlsplit(u if "://" in u else f"https://{u}")
        host = (p.hostname or "").lower()
    except ValueError:
        return u.lower()
    host = host[4:] if host.startswith("www.") else host
    return f"{host}{p.path.rstrip('/')}" + (f"?{p.query}" if p.query else "")


def urlek(szoveg: str) -> list[str]:
    ki = []
    for m in _URL_RE.finditer(str(szoveg or "")):
        u = m.group(0).rstrip(_URL_VEG)
        if m.group("csupasz") and not _utas_url(u):
            continue
        ki.append(u)
    return ki


def ervenyes_szamok(pkg: dict) -> set[int]:
    return ({int(s["n"]) for s in pkg.get("sources") or [] if isinstance(s.get("n"), int)}
            | {int(w["n"]) for w in pkg.get("web_sources") or [] if isinstance(w.get("n"), int)})


def _kizartak(iras: dict) -> set[int]:
    ki = set()
    for e in iras.get("exclude_refs") or []:
        v = e.get("ref") if isinstance(e, dict) else e
        try:
            ki.add(int(v))
        except (TypeError, ValueError):
            continue
    return ki


def _hashtagek(s: str) -> int:
    return len(re.findall(r"(?<!\w)#\w+", s or ""))


def ellenoriz(pkg: dict, iras: dict) -> list[str]:
    """A megírt csomag hibái (angolul — a modellnek megy vissza); üres lista = mehet a kártyára."""
    h: list[str] = []
    ervenyes = ervenyes_szamok(pkg)
    kizart = _kizartak(iras)
    forras_url = {_url_kulcs(s.get("url")) for s in (pkg.get("sources") or []) + (pkg.get("web_sources") or [])
                  if s.get("url")}
    for k in ("title", "analysis_text", "comment_text", "quote_text"):
        if not str(iras.get(k) or "").strip():
            h.append(f"`{k}` is empty")
    tenyek = iras.get("facts") if isinstance(iras.get("facts"), list) else []
    if len(tenyek) < MIN_TENY:
        h.append(f"`facts` has {len(tenyek)} lines — at least {MIN_TENY} are required")
    bad = sorted(kizart - ervenyes)
    if bad:
        h.append(f"exclude_refs {bad} are not source numbers")
    helyek = [("title", iras.get("title"), False), ("card_headline", iras.get("card_headline"), False),
              ("analysis_text", iras.get("analysis_text"), True)] + [
             (f"facts[{i}]", t, True) for i, t in enumerate(tenyek)]
    for hol, szoveg, ref_kell in helyek:
        szoveg = str(szoveg or "")
        if ref_kell:
            for n in sorted({int(x) for x in _REF.findall(szoveg)}):
                if n in kizart:
                    h.append(f"{hol} cites [{n}], which you excluded (exclude_refs)")
                elif n not in ervenyes:
                    h.append(f"{hol} cites [{n}] — no such source (valid: {min(ervenyes, default=0)}–"
                             f"{max(ervenyes, default=0)})")
        for u in urlek(szoveg):
            if _url_kulcs(u) not in forras_url:
                h.append(f"{hol} contains the URL {u}, which is not a source URL — remove it")
    for i, t in enumerate(tenyek):
        t = str(t or "")
        if not _REF.search(t):
            h.append(f"facts[{i}] has no [n]")
        fajta = t.split(":", 1)[0].strip().lower() if ":" in t else "what"
        if ":" in t and fajta not in TENY_FAJTAK and " " not in fajta:
            h.append(f"facts[{i}] kind '{fajta}' is unknown (use: {', '.join(TENY_FAJTAK)})")
    elemzes = str(iras.get("analysis_text") or "")
    bekezdes = [b for b in re.split(r"\n\s*\n", elemzes) if b.strip()]
    if elemzes and not 3 <= len(bekezdes) <= 5:
        h.append(f"analysis_text has {len(bekezdes)} paragraphs — 3 to 5 are required (blank line between them)")
    if bekezdes and not _REF.search(bekezdes[0]):
        h.append("the first paragraph has no [n]")
    ref = iras.get("card_headline_ref")
    if ref not in (None, "", 0):
        try:
            if int(str(ref).strip("[] ")) not in ervenyes:
                h.append(f"card_headline_ref {ref} is not a source number")
        except ValueError:
            h.append(f"card_headline_ref {ref!r} is not a number")
    komment, idezet = str(iras.get("comment_text") or ""), str(iras.get("quote_text") or "")
    for nev, szoveg, plafon in (("comment_text", komment, twitter_poszt.MAX_HOSSZ),
                                ("quote_text", idezet, twitter_poszt.MAX_HOSSZ - 1 - twitter_poszt.LINK_HOSSZ)):
        if not szoveg:
            continue
        n = twitter_poszt.hossz(szoveg)
        if n > plafon:
            h.append(f"{nev} is {n} characters on X — at most {plafon} (shorten by {n - plafon})")
        if urlek(szoveg) or re.search(r"https?://", szoveg):
            h.append(f"{nev} contains a link — the link is added automatically, remove it")
        if _REF.search(szoveg):
            h.append(f"{nev} contains [n] — no source numbers in the X text")
        if _hashtagek(szoveg) > 2:
            h.append(f"{nev} has {_hashtagek(szoveg)} hashtags — at most 2 (forbidden: hashtags_over_two)")
        if re.search(r"(?<!\w)@[A-Za-z0-9_]{1,15}", szoveg):
            h.append(f"{nev} contains an @mention — remove it")
    if komment and idezet and " ".join(komment.split()) == " ".join(idezet.split()):
        h.append("comment_text and quote_text are identical (forbidden: duplicate_text) — reword quote_text")
    return h


async def ir(pkg: dict, poszt: dict, *, chat=None) -> tuple[dict, float, list]:
    """Az írás + legfeljebb `JAVITO_KOR` javítókör. → (írás, költés $, megjegyzések). Hibánál HogHiba (a listával)."""
    csomag = json.dumps(irasi_csomag(pkg, poszt), ensure_ascii=False, default=str)
    user = f"THE PACKAGE (JSON):\n{csomag}\n\nWrite the HOG now. One JSON object."
    szoveg, koltes, notes = await _sonnet(IRO, user, purpose="hog/iras", max_tokens=16000, chat=chat)
    iras = _json_ki(szoveg)
    hibak = ["the answer was not one JSON object"] if iras is None else ellenoriz(pkg, iras)
    kor = 0
    while hibak and kor < JAVITO_KOR:
        kor += 1
        javit = (f"{user}\n\nYOUR PREVIOUS ANSWER:\n{json.dumps(iras, ensure_ascii=False) if iras else szoveg[:20000]}"
                 f"\n\nTHE CODE CHECK FOUND THESE ERRORS — fix all of them, change nothing else, and answer with the "
                 f"complete corrected JSON object:\n- " + "\n- ".join(hibak))
        szoveg, k2, n2 = await _sonnet(IRO, javit, purpose="hog/javitas", max_tokens=16000, chat=chat)
        koltes += k2
        notes += n2
        uj = _json_ki(szoveg)
        if uj is not None:
            iras = uj
        hibak = ["the answer was not one JSON object"] if iras is None else ellenoriz(pkg, iras)
    if hibak:
        raise HogHiba(f"a megírt HOG {kor} javítókör után is hibás ({len(hibak)}): " + "; ".join(hibak[:6])
                      + f" — költés eddig ${koltes:.3f}")
    iras["_javito_kor"] = kor
    return iras, koltes, notes


# ══ A TÁR: action_drafts (kind='hog') ═════════════════════════════════════

def _payload(sor) -> dict:
    try:
        p = json.loads(sor["payload_json"] or "{}")
        return p if isinstance(p, dict) else {}
    except (TypeError, ValueError):
        return {}


def _frissit(conn, did: int, statusz: str, decided_at: str, ujdonsag: dict) -> None:
    sor = conn.execute("SELECT payload_json FROM action_drafts WHERE id = ?", (did,)).fetchone()
    p = {**(_payload(sor) if sor else {}), **ujdonsag}
    conn.execute("UPDATE action_drafts SET status = ?, decided_at = ?, payload_json = ? WHERE id = ?",
                 (statusz, decided_at, json.dumps(p, ensure_ascii=False), did))


def _tweet_hossz(p: dict) -> dict:
    """A kiposztolandó szövegek X-hossza: a válasz a komment (link nélkül, a kártyaképpel), az idézet/saját poszt az
    idézet-szöveg + szóköz + link (23)."""
    komment = str(p.get("comment_text") or "")
    idezet = str(p.get("quote_text") or "")
    return {"komment": twitter_poszt.hossz(komment),
            "idezet": twitter_poszt.hossz(idezet) + 1 + twitter_poszt.LINK_HOSSZ if idezet else 0,
            "max": twitter_poszt.MAX_HOSSZ}


def ment(get_db, *, iras: dict, pkg: dict, bemenet: dict, cel: dict | None, mod: str, koltes: float, chat_id: str,
         notes: list | None = None) -> int:
    """A kész HOG-tervezet a meglévő `action_drafts`-ba (pending). Semmi nem ment ki. → draft id."""
    tenyek = [str(t) for t in iras.get("facts") or []]
    payload = {
        "mod": mod, "cel": cel or {}, "echolot_draft_id": pkg.get("draft_id"), "lang": pkg.get("lang"),
        "title": str(iras.get("title") or ""), "card_headline": str(iras.get("card_headline") or ""),
        "card_headline_ref": iras.get("card_headline_ref"), "analysis_text": str(iras.get("analysis_text") or ""),
        "facts": tenyek, "comment_text": str(iras.get("comment_text") or ""),
        "quote_text": str(iras.get("quote_text") or ""), "exclude_refs": iras.get("exclude_refs") or [],
        "slug": str(iras.get("slug") or ""), "komment_hu": str(iras.get("komment_hu") or ""),
        "megjegyzes_hu": str(iras.get("megjegyzes_hu") or ""), "javito_kor": iras.get("_javito_kor", 0),
        "koltes_usd": round(koltes, 4), "modell": MODELL,
        "poszt": {k: bemenet.get(k) for k in ("szoveg", "szerzo", "handle", "url") if bemenet.get(k)},
        "csomag": {"status": pkg.get("status"), "coverage": pkg.get("coverage"),
                   "angle": pkg.get("angle_suggestion"), "sources": len(pkg.get("sources") or []),
                   "web_sources": len(pkg.get("web_sources") or []),
                   "classified": (pkg.get("corpus") or {}).get("classified"),
                   "articles": (pkg.get("corpus") or {}).get("article_count"),
                   "regions": (pkg.get("corpus") or {}).get("regions"),
                   "languages": len((pkg.get("corpus") or {}).get("languages") or [])},
        "jegyzetek": [str(n)[:300] for n in (notes or [])][:8],
        "chat_id": str(chat_id or ""), "forras": "feldwebel", "details": str(iras.get("comment_text") or ""),
    }
    payload["szoveg"] = payload["comment_text"]          # a „posztold ki a tervezetet: …” ezzel illeszt
    cim = "🐗 HOG · " + twitter_poszt.cim(payload["title"] or payload["comment_text"])
    conn = get_db()
    try:
        twitter_poszt.schema_biztosit(conn, KIND)
        cur = conn.execute(
            "INSERT INTO action_drafts (source_message_id, kind, title, due_at, payload_json, status, created_at, "
            "decided_at) VALUES (?, ?, ?, '', ?, 'pending', ?, '')",
            (f"hog:{chat_id}", KIND, cim, json.dumps(payload, ensure_ascii=False), _iso()))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def kartya(p: dict) -> dict:
    """A kabin munkapadjának „🐗 HOG” kártyája (ui_kabin._draftok)."""
    cel = p.get("cel") or {}
    return {"hog": {
        "mod": p.get("mod"), "cel_url": cel.get("url") or "", "cel_handle": cel.get("handle") or "",
        "cim": p.get("title") or "", "fejlec": p.get("card_headline") or "", "elemzes": p.get("analysis_text") or "",
        "tenyek": list(p.get("facts") or []), "komment": p.get("comment_text") or "",
        "idezet": p.get("quote_text") or "", "komment_hu": p.get("komment_hu") or "",
        "megjegyzes": p.get("megjegyzes_hu") or "", "hossz": _tweet_hossz(p),
        "koltes_usd": p.get("koltes_usd"), "lang": p.get("lang"), "csomag": p.get("csomag") or {},
        "oldal_url": p.get("oldal_url") or "", "intent": p.get("intent") or {}, "jegyzetek": p.get("jegyzetek") or [],
    }, "szoveg": p.get("comment_text") or "", "hiba": str(p.get("utolso_hiba") or "") or None, "reszlet": ""}


def cel_beallit(get_db, hog_id, url: str, mod: str = "") -> dict:
    """Utólag kapott link egy FÜGGŐ HOG-hoz: a tweet-ID-vel válasz (vagy kért idézet) lesz belőle. Nem posztol."""
    az = poszt_azonosito(url)
    if not az:
        return {"error": "ez nem X-poszt linkje (x.com/<handle>/status/<szám>)"}
    conn = get_db()
    try:
        sor = conn.execute("SELECT kind, status, payload_json FROM action_drafts WHERE id = ?", (int(hog_id),)).fetchone()
        if not sor or sor["kind"] != KIND:
            return {"error": f"nincs ilyen HOG-tervezet: #{hog_id}"}
        if sor["status"] != "pending":
            return {"error": f"a #{hog_id} HOG már nem vár döntésre (állapot: {sor['status']})"}
        uj_mod = mod if mod in ("valasz", "idezet") else "valasz"
        cel = {"url": tweet_url(*az), "tweet_id": az[1], "handle": az[0], "forras": "link"}
        _frissit(conn, int(hog_id), "pending", "", {"cel": cel, "mod": uj_mod, "intent": {}})
        conn.commit()
    finally:
        conn.close()
    return {"status": "jovahagyasra_var", "hog_id": int(hog_id), "mod": uj_mod, "cel": cel["url"],
            "teendo": f"A #{hog_id} HOG mostantól {'VÁLASZ' if uj_mod == 'valasz' else 'IDÉZET'} erre a posztra. "
                      "Semmi nem ment ki; a kabinban hagyja jóvá."}


# ══ A FUTÁS ═══════════════════════════════════════════════════════════════

_FUTASOK: set = set()


def ertesito(ctx):
    """Az eredmény a Feldwebel-szálba (előzmény + kabin-ablak) és a Telegramra — a tweet-értesítés mintájára."""
    async def _ertesit(chat_id: str, szoveg: str) -> None:
        from html import escape
        from cimzett import fejlec
        twitter_poszt.szalba(ctx.get_db, chat_id or getattr(ctx, "telegram_chat_id", "") or "", szoveg)
        if getattr(ctx, "telegram_push", None):
            await ctx.telegram_push(f"{fejlec('feldwebel')}\n\n{escape(szoveg)}")
    return _ertesit


async def kovetok_szama() -> int | None:
    if not twitter_poszt.beallitva():
        return None
    try:
        r = await twitter_poszt.en(metrika=True)
    except Exception:  # noqa: BLE001
        return None
    n = r.get("followers_count") if isinstance(r, dict) else None
    return int(n) if isinstance(n, int) else None


async def futtat(get_db, args: dict, *, chat_id: str = "", ertesit=None, kepelemzo=None, chat=None) -> dict:
    """A teljes előkészítés (publikálás és posztolás NÉLKÜL). → {"ok", "hog_id", …} vagy {"error"}; az eredményt az
    `ertesit` is elviszi a Feldwebel-szálba és a Telegramra."""
    koltes = 0.0
    notes: list[str] = []
    try:
        b = {"szoveg": str(args.get("poszt_szoveg") or "").strip(), "szerzo": str(args.get("szerzo") or "").strip(),
             "url": str(args.get("poszt_url") or "").strip(), "utasitas": str(args.get("utasitas") or "").strip(),
             "nyelv": str(args.get("nyelv") or "").strip().lower()}
        b["handle"] = handle_ki(b["szerzo"])
        if args.get("melleklet_id") not in (None, "", 0):
            kep = await kep_olvas(get_db, args.get("melleklet_id"), kepelemzo)
            if kep:
                notes.append(f"képernyőkép: {'képelemzés' if kep.get('forras') == 'kepelemzes' else 'feltöltési átirat'}")
                b["szoveg"] = str(kep.get("szoveg") or "").strip() or b["szoveg"]
                b["handle"] = (str(kep.get("handle") or "").lstrip("@") or b["handle"])
                b["szerzo"] = b["szerzo"] or str(kep.get("szerzo") or "")
                b["url"] = b["url"] or str(kep.get("url") or "")
                b["nyelv"] = b["nyelv"] or str(kep.get("nyelv") or "").lower()
                if isinstance(kep.get("idezett"), dict) and kep["idezett"].get("szoveg"):
                    b["idezett"] = kep["idezett"]
        cel = None
        az = poszt_azonosito(b["url"])
        if az:
            cel = {"url": tweet_url(*az), "tweet_id": az[1], "handle": az[0], "forras": "link"}
            b["handle"] = b["handle"] or az[0]
            if not b["szoveg"]:
                r = await _echolot("scrape_url", {"url": cel["url"]}, KERESO_IDO)
                b["szoveg"] = str((r or {}).get("text") or "").strip() if isinstance(r, dict) else ""
        if not b["szoveg"]:
            raise HogHiba("nincs mit elemezni: nem kaptam a poszt szövegét (se szöveg, se olvasható kép, se link)")
        if not cel and b["handle"]:
            cel = await poszt_keres(b["handle"], b["szoveg"])
            if cel:
                cel["forras"] = "kereses"
        kert = str(args.get("mod") or "").strip().lower()
        mod = ("idezet" if kert == "idezet" and cel else "sajat" if kert == "sajat" or not cel else "valasz")
        # TERVEZÉS: a hog_prepare kezelései (a kulcsszó-kezelés nélkül élesben általános szavak lettek)
        terv_sz, k, n = await _sonnet(TERVEZO, tervezo_uzenet(b), purpose="hog/terv", max_tokens=2000, chat=chat)
        koltes += k
        notes += n
        terv = _json_ki(terv_sz) or {}
        lang = str(terv.get("lang") or b["nyelv"] or "").lower()
        lang = lang if lang in NYELVEK else (b["nyelv"] if b["nyelv"] in NYELVEK else nyelv_tipp(b["szoveg"]))
        kovetok = await kovetok_szama()
        if not kulcs():
            raise HogHiba("nincs ECHOLOT_EDITOR_KEY a Bridge-en — a HOG szerkesztői eszköz, kulcs nélkül nem megy")
        prep = {"post_text": str(terv.get("post_text") or "").strip() or b["szoveg"], "post_url": (cel or {}).get("url", ""),
                "lang": lang, "author_hint": szerzo_jelzes(b["szerzo"], b["handle"]),
                "platform": "x", "mode": "comment", "own_follower_count": kovetok, "editor_key": kulcs()}
        kw = [str(x) for x in (terv.get("keywords") or []) if str(x).strip()][:12]
        if kw:
            prep.update(keywords=kw, keywords_mode="replace")
        if str(terv.get("protagonist_hint") or "").strip():
            prep["protagonist_hint"] = str(terv["protagonist_hint"]).strip()
        anc = [str(x) for x in (terv.get("anchors") or []) if str(x).strip()][:8]
        if anc:
            prep["anchors"] = anc
        pkg = await _echolot("hog_prepare", prep, PREPARE_IDO)
        if not isinstance(pkg, dict) or pkg.get("ok") is False or pkg.get("error"):
            raise HogHiba(f"a hog_prepare hibát adott: {_tisztit((pkg or {}).get('error'))}: "
                          f"{_tisztit((pkg or {}).get('message'))[:300]}")
        if not pkg.get("draft_id"):
            raise HogHiba(_tisztit("a hog_prepare nem készített tervezetet: "
                                   + str((pkg.get("playbook") or {}).get("why") or pkg.get("note") or "nincs anyag")))
        poszt = {"text": prep["post_text"], "author": prep["author_hint"], "url": prep["post_url"]}
        iras, k, n = await ir(pkg, poszt, chat=chat)
        koltes += k
        notes += n
        if pkg.get("status") == "partial" or not (pkg.get("corpus") or {}).get("classified"):
            notes.append(f"az Echolot-csomag részleges: {str(pkg.get('note') or '')[:200]}")
        hid = ment(get_db, iras=iras, pkg=pkg, bemenet=b, cel=cel, mod=mod, koltes=koltes, chat_id=chat_id,
                   notes=notes)
        uzenet = kesz_uzenet(hid, iras, mod, cel, koltes, pkg)
        out = {"ok": True, "hog_id": hid, "mod": mod, "koltes_usd": round(koltes, 4), "uzenet": uzenet}
    except HogHiba as e:
        uzenet = f"❌ A HOG nem készült el: {_tisztit(e)}" + (f" (Sonnet-költés: ${koltes:.3f})" if koltes else "")
        out = {"error": _tisztit(e), "koltes_usd": round(koltes, 4), "uzenet": uzenet}
    except Exception as e:  # noqa: BLE001 — a háttérfutás sosem némán hal meg
        logger.exception("hog: a futás váratlanul bukott")
        uzenet = f"❌ A HOG nem készült el (váratlan hiba): {_tisztit(f'{type(e).__name__}: {e}')[:300]}"
        out = {"error": uzenet, "koltes_usd": round(koltes, 4), "uzenet": uzenet}
    if ertesit:
        try:
            await ertesit(chat_id, uzenet)
        except Exception as e:  # noqa: BLE001
            logger.warning("hog: az értesítés nem ment ki: %s", _tisztit(e))
    return out


def _elso_szavak(s: str, n: int = 3) -> str:
    return " ".join(str(s or "").split()[:n])


def kesz_uzenet(hid: int, iras: dict, mod: str, cel: dict | None, koltes: float, pkg: dict) -> str:
    h = _tweet_hossz(iras)
    mod_sz = {"valasz": f"VÁLASZ erre: {(cel or {}).get('url')}",
              "idezet": f"IDÉZET erre: {(cel or {}).get('url')}",
              "sajat": "SAJÁT POSZT — nem találtam az eredeti poszt linkjét. Elküldöd? Akkor válasz lesz belőle."}[mod]
    korpusz = pkg.get("corpus") or {}
    komment = (f"Komment ({h['komment']}/{h['max']}, link nélkül, kártyaképpel; a link 2–3 perc múlva a válaszod "
               f"alá):\n«{iras.get('comment_text')}»\n"
               + (f"Magyarul: «{iras.get('komment_hu')}»\n" if iras.get("komment_hu") else ""))
    idezet = f"«{iras.get('quote_text')}» + a link ({h['idezet']}/{h['max']})\n"
    return (f"🐗 HOG-tervezet kész (#{hid}) — még SEMMI nem ment ki.\n"
            f"Oldal: «{iras.get('title')}»\nKártya: «{iras.get('card_headline') or '—'}»\n"
            f"Mód: {mod_sz}\n"
            + (komment + "Ha az X a választ elutasítja, idézet megy: " + idezet if mod == "valasz"
               else "A tweet: " + idezet + "Ha válasz lesz belőle: " + komment)
            + (f"Megjegyzés: {iras.get('megjegyzes_hu')}\n" if iras.get("megjegyzes_hu") else "")
            + f"Anyag: {korpusz.get('article_count')} tudósítás, {korpusz.get('regions')} régió, "
              f"{len(korpusz.get('languages') or [])} nyelv · {len(iras.get('facts') or [])} tényvonal · "
              f"Sonnet-költés: ${koltes:.3f}\n"
            f"Jóváhagyás: a kabin munkapadján (🐗 HOG → Jóváhagyom), vagy: „posztold ki a tervezetet: "
            f"{_elso_szavak(iras.get('comment_text'))}”. A jóváhagyás az ÁLLANDÓ oldalt ÉS a tweetet is kiteszi.")


async def inditas(ctx, args: dict, chat_id: str) -> dict:
    """A Feldwebel `hog_keszit` eszköze: a háttérfutás indítása (vagy egy függő HOG linkjének beállítása). Nem publikál."""
    if args.get("hog_id") not in (None, "", 0) and args.get("poszt_url"):
        return cel_beallit(ctx.get_db, args.get("hog_id"), str(args.get("poszt_url")), str(args.get("mod") or ""))
    if not any(str(args.get(k) or "").strip() for k in ("poszt_szoveg", "poszt_url", "melleklet_id")):
        return {"error": "add meg a poszt szövegét, a linkjét, vagy a képernyőkép mellékletének számát (melleklet_id)"}
    if not kulcs():
        return {"error": "nincs ECHOLOT_EDITOR_KEY a Bridge-en — a HOG az Echolot szerkesztői eszköze, kulcs nélkül nem "
                         "indítható (a Railway-változót a Kommandant állítja)"}
    if len(_FUTASOK) >= MAX_PARHUZAMOS:
        return {"error": f"már {len(_FUTASOK)} HOG-futás megy — várd meg, amíg elkészül"}
    kepelemzo = (getattr(ctx, "capture_state", None) or {}).get("_analyze_image")
    feladat = asyncio.ensure_future(futtat(ctx.get_db, dict(args), chat_id=str(chat_id or ""), ertesit=ertesito(ctx),
                                           kepelemzo=kepelemzo))
    _FUTASOK.add(feladat)
    feladat.add_done_callback(_FUTASOK.discard)
    logger.info("hog: futás indult (chat %s)", chat_id)
    return {"status": "elindult", "publikalva": False, "posztolva": False, "teendo": TEENDO_INDULT}


# ══ A JÓVÁHAGYÁS: hog_publish → tweet ═════════════════════════════════════

async def _letolt_alap(url: str) -> bytes | None:
    import httpx
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
        r = await c.get(url)
    return r.content if r.status_code == 200 and r.content else None


#: A tesztek cserélik (a kártyakép letöltése).
LETOLT = _letolt_alap


def _link(szoveg: str) -> str:
    talalt = re.findall(r"https?://\S+", str(szoveg or ""))
    return talalt[-1].rstrip(_URL_VEG) if talalt else ""


def link_keses() -> float:
    try:
        return max(0.0, float(os.environ.get("HOG_LINK_KESES_S", LINK_KESES_ALAP)))
    except (TypeError, ValueError):
        return float(LINK_KESES_ALAP)


async def _publikal(p: dict) -> dict:
    args = {"draft_id": p.get("echolot_draft_id"), "analysis_text": p.get("analysis_text"),
            "comment_text": p.get("comment_text"), "title": p.get("title"), "lang": p.get("lang") or "",
            "quote_text": p.get("quote_text") or "", "facts": list(p.get("facts") or []),
            "card_headline": p.get("card_headline") or "", "editor_key": kulcs()}
    if p.get("card_headline_ref") not in (None, "", 0):
        args["card_headline_ref"] = p["card_headline_ref"]
    if p.get("exclude_refs"):
        args["exclude_refs"] = p["exclude_refs"]
    if p.get("slug"):
        args["slug"] = p["slug"]
    return await _echolot("hog_publish", args, PUBLISH_IDO)


async def _tweetel(p: dict, pub: dict, kliens=None) -> dict:
    """A doktrína szerinti tweet. → {"ok", "mod_valos", "url", "id", "megjegyzes": […], "hiba"?, "link_kell"}"""
    megj: list[str] = []
    if not twitter_poszt.beallitva():
        return {"ok": False, "hiba": "nincs X-kulcs a Bridge-en (TWITTER_* env) — az API-poszt nem megy", "megjegyzes": megj}
    cel = p.get("cel") or {}
    tid = cel.get("tweet_id")
    mod = p.get("mod") if tid else "sajat"
    if mod == "valasz":
        media = []
        og = pub.get("og_image_url")
        if og:
            try:
                kep = await LETOLT(og)
            except Exception as e:  # noqa: BLE001
                kep = None
                megj.append(f"a kártyakép nem tölthető le ({type(e).__name__})")
            if kep:
                up = await twitter_poszt.media_feltolt(kep, kliens=kliens)
                if up.get("ok"):
                    media = [up["media_id"]]
                else:
                    megj.append(f"a kártyakép feltöltése nem sikerült ({up.get('error')}) — kép nélkül ment")
        r = await twitter_poszt.posztol(p.get("comment_text") or "", kliens=kliens, valasz_id=tid, media_ids=media)
        if r.get("ok"):
            return {"ok": True, "mod_valos": "valasz", "url": r["url"], "id": r["id"], "megjegyzes": megj,
                    "link_kell": True, "kep": bool(media)}
        if r.get("status") != 403:
            return {"ok": False, "hiba": r.get("error"), "status": r.get("status"), "megjegyzes": megj}
        megj.append(f"Az X a VÁLASZT elutasította ({r.get('error')}) — ezért IDÉZETKÉNT posztoltam.")
        mod = "idezet"
    szoveg = pub.get("quote_text") or twitter_poszt.osszerak(p.get("quote_text") or "", pub.get("quote_url") or "")
    r = await twitter_poszt.posztol(szoveg, kliens=kliens, idezet_id=tid if mod == "idezet" else None)
    if r.get("ok"):
        return {"ok": True, "mod_valos": mod, "url": r["url"], "id": r["id"], "megjegyzes": megj, "link_kell": False}
    return {"ok": False, "hiba": r.get("error"), "status": r.get("status"), "megjegyzes": megj}


def intentek(p: dict, pub: dict) -> dict:
    """A „Válasz a telefonról” linkek (a kész oldal linkjével) — az API-korlátozástól függetlenül működnek."""
    cel = p.get("cel") or {}
    reply_link = _link(pub.get("reply_text")) or pub.get("url") or ""
    idezet_szoveg = pub.get("quote_text") or ""
    ki = {}
    if cel.get("tweet_id"):
        ki["valasz"] = intent_valasz(cel["tweet_id"], f"{p.get('comment_text') or ''}\n\n{reply_link}".strip())
        ki["idezet"] = intent_idezet(cel.get("url") or tweet_url(cel.get("handle"), cel["tweet_id"]),
                                     idezet_szoveg)
    else:
        ki["sajat"] = intent_sajat(idezet_szoveg or f"{p.get('comment_text') or ''}\n\n{reply_link}")
    return ki


async def _link_valasz(get_db, did: int, sajat_id: str, szoveg: str, chat_id: str, *, keses: float, ertesit=None,
                       kliens=None) -> dict:
    """A link a saját válasz alá (a doktrína: 2–3 perc múlva)."""
    if keses > 0:
        await asyncio.sleep(keses)
    r = await twitter_poszt.posztol(szoveg, kliens=kliens, valasz_id=sajat_id)
    conn = get_db()
    try:
        if r.get("ok"):
            _frissit_payload(conn, did, {"link_valasz": r["url"]})
            uzenet = f"🔗 A HOG linkje kiment a válaszod alá: {r['url']}"
        else:
            telefon = intent_valasz(sajat_id, szoveg)
            _frissit_payload(conn, did, {"link_valasz": None, "link_hiba": r.get("error"),
                                         "intent_link": telefon})
            uzenet = (f"❌ A HOG linkje NEM ment ki a válaszod alá: {r.get('error')}\n"
                      f"Telefonról egy koppintás: {telefon}")
        conn.commit()
    finally:
        conn.close()
    if ertesit:
        try:
            await ertesit(chat_id, uzenet)
        except Exception as e:  # noqa: BLE001
            logger.warning("hog: a link-értesítés nem ment ki: %s", _tisztit(e))
    return r


def _frissit_payload(conn, did: int, ujdonsag: dict) -> None:
    sor = conn.execute("SELECT payload_json FROM action_drafts WHERE id = ?", (did,)).fetchone()
    p = {**(_payload(sor) if sor else {}), **ujdonsag}
    conn.execute("UPDATE action_drafts SET payload_json = ? WHERE id = ?", (json.dumps(p, ensure_ascii=False), did))


_HATTER: set = set()


async def dont(get_db, draft_id: int, dontes: str, *, ertesit=None, utolag=None, ki: str = "kabin",
               kliens=None) -> dict:
    """A Kommandant döntése egy HOG-ról: 'approve' → `hog_publish`, utána a tweet; 'reject' → semmi nem megy ki.
    `ertesit(chat_id, szoveg)` [async]: az azonnali eredmény; `utolag`: a 2–3 perc múlva kimenő link üzenete
    (alapból az `ertesit`)."""
    if dontes not in ("approve", "reject"):
        return {"error": "a döntés 'approve' vagy 'reject'"}
    utolag = utolag or ertesit
    conn = get_db()
    try:
        sor = conn.execute("SELECT id, kind, status, payload_json FROM action_drafts WHERE id = ?",
                           (int(draft_id),)).fetchone()
        if not sor or sor["kind"] != KIND:
            return {"error": f"nincs ilyen HOG-tervezet: #{draft_id}"}
        p = _payload(sor)
        if sor["status"] != "pending":
            return {"error": f"a #{draft_id} HOG már nem vár döntésre (állapot: {sor['status']})",
                    "draft_id": int(draft_id), "status": sor["status"]}
        most = _iso()
        if dontes == "reject":
            _frissit(conn, int(draft_id), "rejected", most, {"elvetette": ki})
            conn.commit()
            el = p.get("oldal_url")
            return {"draft_id": int(draft_id), "status": "rejected", "posztolva": False,
                    "uzenet": (f"Lezárva: több tweet nem megy ki. Az oldal már él: {el}" if el
                               else "Elvetve — semmi nem ment ki.")}
        if not kulcs():
            return {"error": "nincs ECHOLOT_EDITOR_KEY a Bridge-en — a HOG nem publikálható", "draft_id": int(draft_id),
                    "status": "pending", "posztolva": False}
        plafon = twitter_poszt.napi_max()
        cur = conn.execute(
            "UPDATE action_drafts SET status = 'approved', decided_at = ? WHERE id = ? AND status = 'pending' AND "
            f"(SELECT COUNT(*) FROM action_drafts WHERE kind IN {twitter_poszt.PLAFON_KINDS!r} AND status IN "
            "('approved', 'created') AND decided_at >= ?) < ?", (most, int(draft_id), twitter_poszt._nap_kezdet(), plafon))
        conn.commit()
        if cur.rowcount != 1:
            allapot = conn.execute("SELECT status FROM action_drafts WHERE id = ?", (int(draft_id),)).fetchone()[0]
            if allapot == "pending":
                return {"error": f"elérted a napi plafont (TWITTER_NAPI_MAX={plafon}) — a HOG függő marad",
                        "draft_id": int(draft_id), "status": "pending", "posztolva": False}
            return {"error": f"a #{draft_id} HOG már nem vár döntésre (állapot: {allapot})",
                    "draft_id": int(draft_id), "status": allapot}
    finally:
        conn.close()

    chat_id = str(p.get("chat_id") or "")
    # 1) AZ OLDAL
    try:
        pub = await _publikal(p)
        if not isinstance(pub, dict):
            raise HogHiba("a hog_publish nem JSON-t adott")
        if pub.get("ok") is False and pub.get("error"):
            reszlet = ""
            if pub.get("error") == "invalid_references":
                reszlet = f" — rossz hivatkozások: {json.dumps(pub.get('bad_refs'), ensure_ascii=False)[:300]}"
            raise HogHiba(f"a hog_publish hibát adott: {_tisztit(pub.get('error'))}: "
                          f"{_tisztit(pub.get('message'))[:300]}{reszlet}")
        if not pub.get("url"):
            raise HogHiba("a hog_publish nem adott oldal-URL-t")
    except HogHiba as e:
        hiba = _tisztit(e)
        conn = get_db()
        try:
            _frissit(conn, int(draft_id), "pending", "", {"utolso_hiba": hiba, "utolso_proba": _iso()})
            conn.commit()
        finally:
            conn.close()
        uzenet = f"❌ A HOG (#{draft_id}) nem publikálódott: {hiba}\nSemmi nem ment ki; a kártya a kabinban maradt."
        await _ertesit(ertesit, chat_id, uzenet)
        return {"draft_id": int(draft_id), "status": "pending", "posztolva": False, "publikalva": False,
                "error": hiba, "uzenet": uzenet}

    oldal = pub["url"]
    intent = intentek(p, pub)
    alap = {"oldal_url": oldal, "og_image_url": pub.get("og_image_url"), "reply_text": pub.get("reply_text"),
            "quote_text_kesz": pub.get("quote_text"), "quote_url": pub.get("quote_url"), "intent": intent,
            "x_card_ready": pub.get("x_card_ready"), "publikalva": pub.get("published_at") or _iso()}
    telefon = intent.get(p.get("mod")) or intent.get("valasz") or intent.get("sajat") or intent.get("idezet")
    # 2) A TWEET — csak ha az oldal ÉS a kártya élőben elérhető (az X egyszer olvassa a kártyát, és cache-eli)
    if not pub.get("ok"):
        tw = {"ok": False, "hiba": "az oldal kint van, de az élő ellenőrzés nem zöld (a lap vagy a kártya nem érhető "
                                   "el) — a tweet NEM ment ki, mert az X egyszer olvassa a kártyát; pár perc múlva "
                                   "próbáld újra", "megjegyzes": []}
    else:
        tw = await _tweetel(p, pub, kliens=kliens)
    conn = get_db()
    try:
        if tw.get("ok"):
            _frissit(conn, int(draft_id), "created", _iso(), {
                **alap, "tweet_url": tw["url"], "tweet_id": tw["id"], "mod_valos": tw["mod_valos"],
                "jovahagyta": ki, "megjegyzes_x": tw.get("megjegyzes"), "utolso_hiba": None,
                "link_valasz": "fuggoben" if tw.get("link_kell") else None})
            statusz = "created"
        else:
            _frissit(conn, int(draft_id), "pending", "", {
                **alap, "utolso_hiba": tw.get("hiba"), "utolso_proba": _iso(), "megjegyzes_x": tw.get("megjegyzes")})
            statusz = "pending"
        conn.commit()
    finally:
        conn.close()
    megj = "".join(f"\n⚠️ {m}" for m in tw.get("megjegyzes") or [])
    kartya_figy = ("" if pub.get("x_card_ready") else
                   f"\n⚠️ Az X-kártya még nem kész ({pub.get('x_card_reason')}) — a link működik, a kép később jön.")
    if tw.get("ok"):
        mod_sz = {"valasz": "VÁLASZ", "idezet": "IDÉZET", "sajat": "SAJÁT POSZT"}[tw["mod_valos"]]
        keses = link_keses()
        uzenet = (f"🐗 A HOG kiment.\nOldal (állandó): {oldal}\nTweet ({mod_sz}): {tw['url']}{megj}{kartya_figy}"
                  + (f"\nA link {int(keses // 60)}–{int(keses // 60) + 1} perc múlva a válaszod alá megy."
                     if tw.get("link_kell") and keses >= 60 else "")
                  + (f"\nVálasz a telefonról (ha mégis ott akarod): {intent['valasz']}"
                     if tw["mod_valos"] != "valasz" and intent.get("valasz") else ""))
    else:
        uzenet = (f"⚠️ Az oldal ÉL (állandó): {oldal}\nDe a tweet NEM ment ki: {tw.get('hiba')}{megj}{kartya_figy}\n"
                  f"Telefonról egy koppintás (kitöltött {'válasz' if intent.get('valasz') else 'poszt'}): {telefon}\n"
                  f"A kártya a kabinban maradt: a Jóváhagyom újrapróbálja, a Lezárom befejezi.")
    await _ertesit(ertesit, chat_id, uzenet)
    if tw.get("ok") and tw.get("link_kell") and pub.get("reply_text"):
        keses = link_keses()
        if keses <= 0:
            await _link_valasz(get_db, int(draft_id), tw["id"], pub["reply_text"], chat_id, keses=0,
                               ertesit=utolag, kliens=kliens)
        else:
            f = asyncio.ensure_future(_link_valasz(get_db, int(draft_id), tw["id"], pub["reply_text"], chat_id,
                                                   keses=keses, ertesit=utolag, kliens=kliens))
            _HATTER.add(f)
            f.add_done_callback(_HATTER.discard)
    logger.info("hog: #%s %s (%s)", draft_id, "KIMENT" if statusz == "created" else "oldal kint, tweet bukott", ki)
    out = {"draft_id": int(draft_id), "status": statusz, "publikalva": True, "posztolva": statusz == "created",
           "oldal_url": oldal, "url": tw.get("url") or oldal, "uzenet": uzenet, "intent": intent}
    if tw.get("ok"):
        out.update(tweet_url=tw["url"], tweet_id=tw["id"], mod=tw["mod_valos"])
    else:
        out.update(error=tw.get("hiba"), x_status=tw.get("status"))
    return out


async def _ertesit(ertesit, chat_id: str, uzenet: str) -> None:
    if not ertesit:
        return
    try:
        await ertesit(chat_id, uzenet)
    except Exception as e:  # noqa: BLE001 — az értesítés hibája a publikálást nem csinálja vissza
        logger.warning("hog: az értesítés nem ment ki: %s", _tisztit(e))
