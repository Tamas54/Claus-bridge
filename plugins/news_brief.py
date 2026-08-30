"""HIRSZEMLE — a napi hirbrief KANONIKUS promptja es ket menetrendje.

MIERT VAN EZ A FAJL (2026-08-30)
--------------------------------
Ket okbol.

1. KET BRIEF KELL, EGY RECEPTEN NEM FER EL.
   A `cron_schedule` EGYETLEN oszlop a recept soran. Aki ugyanarra a receptre
   ket idopontot ir, az nem hozzaad, hanem FELULIR. Pontosan ez tortent
   2026-04-10-en: a Kommandant reggeli (7:00) ES delutani (16:00) briefet kert,
   a Feldwebel mindkettot a `daily_news_brief` sorra irta, a masodik eltorolte
   az elsot, majd jelentette, hogy "a ket utemezes aktiv". A reggeli brief soha
   nem letezett. Ezert KET RECEPT van: `daily_news_brief` (reggel) es
   `daily_news_brief_pm` (delutan).

2. KET RECEPT KET PROMPTOT JELENTENE — ES A DUPLIKATUMOK SZETCSUSZNAK.
   A prompt eddig KIZAROLAG a produkcios SQLite-ban elt. Ez ketszeresen rossz:
   egy DB-visszaallitas szo nelkul elvinne (a mai egesz napi munkat is), es ket
   sorban ket kulon szoveg kezdene sajat eletet. Innentol a prompt a GIT-ben
   van, egy peldanyban, es a ket recept ugyanazt kapja — a kulonbseg egyetlen
   valtozo, a `session`.

A VERZIO-JELOLES SZEREPE
------------------------
A `PROMPT_VERSION` beleiratik a promptba. A migracio csak akkor ir felul egy
sort, ha az ott levo szoveg egy ISMERT korabbi kanonikus verzio (vagy nincs
jelolese, azaz a mai kezi szerkesztesu allapot). Ha valaki kezzel atirta —
Feldwebel, Kommandant —, azt NEM bantjuk, csak naplozzuk. Kulonben minden
ujrainditas csendben visszaallitana a sajat verziomat a felhasznaloe fole.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("bridge.news_brief")

PROMPT_VERSION = 2

#: recept -> (session, emberi cimke, cron, leiras)
BRIEFS = {
    "daily_news_brief": (
        "reggel", "REGGELI", "0 7 * * *",
        "Reggeli hirszemle: belpol + kulpol + deviza/nyersanyag (Echolot lapszam)",
    ),
    "daily_news_brief_pm": (
        "delutan", "DELUTANI", "0 16 * * *",
        "Delutani hirszemle: a nap friss anyaga + kulpol + piaci zaras (Echolot)",
    ),
}

# ── Session-fuggo keretezes ────────────────────────────────────────────────
# A KULONBSEG NEM KOZMETIKAI. Az Echolot lapszama ~21:55 UTC-kor FAGY, ezert:
#   - reggel  a "tegnapi" lapszam az ELOZO NAP TELJES, LEZART anyaga. Ez egy
#             reggeli sajtoszemle helyes tartalma, nem hianyossag.
#   - delutan ugyanaz a lapszam mar ~16 orat kesett; ott a NAP FRISS anyaga
#             (`fresh_today`) a vezeto, a lapszam a hatter.
_SESSION_FRAMING = {
    "reggel": (
        "Ez a REGGELI szemle (7:00). A `top_stories` az ELOZO NAP TELJES, LEZART\n"
        "lapszamabol van — reggel ez a HELYES tartalom, nem hianyossag: a tegnapi\n"
        "nap ossznapi termese. Ez a rovat a VEZETO. A `fresh_today` az ejszakai es\n"
        "hajnali teteleket hozza; azt masodik, rovidebb blokkban add."
    ),
    "delutan": (
        "Ez a DELUTANI szemle (16:00). A lapszam ilyenkor mar ~16 orat kesett,\n"
        "ezert a `fresh_today` — A NAP FRISS ANYAGA — a VEZETO rovat, es a\n"
        "`top_stories` lapszam-tetelei utana, hatterkent jonnek. Ha egy tetel mar\n"
        "szerepelt a reggeli szemleben, az nem baj, de a friss anyag menjen elore."
    ),
}


def prompt_for(recipe_name: str) -> str:
    """A recept kanonikus prompt_template-je. Ismeretlen nevre a reggelit adja."""
    session = BRIEFS.get(recipe_name, BRIEFS["daily_news_brief"])[0]
    framing = _SESSION_FRAMING[session]
    label = "REGGELI" if session == "reggel" else "DELUTANI"
    return f"""<!-- news_brief_prompt v{PROMPT_VERSION} ({session}) -->
Készits {label} HIRSZEMLET a Kommandantnak.

== EZ A SZEMLE ==
{framing}

== ADATFORRASOK (a prompt vegen: "=== FACTUAL CONTEXT ===") ==
A blokkot a Bridge Python kodja (plugins/_recipe_prefetch.py) hozta letre:
  - fx_ecb: ECB napi devizaarfolyamok (EUR/HUF, USD/HUF, CHF/HUF, GBP/HUF stb.)
  - market_yahoo: Yahoo Finance kvotok — GC=F (Gold), BZ=F (Brent), CL=F (WTI),
    EURHUF=X, USDHUF=X, ^BUX.BD (BUX), BTC-USD
  - echolot_hirszemle: AZ ECHOLOT SAJAT HIRSZEMLEJE. Mezoi:
        edition_date      a lapszam DATUMA
        edition_is_today  igaz-e, hogy a MAI lapszam
        top_stories       MAGYAR klaszterezett sztorik: title + lead + source_count
                          (HANY FUGGETLEN FORRAS hozta) + sphere
        world_stories     VILAG-szemle az angol lapszambol: ugyanaz a szerkezet.
                          MAR RENDEZVE: a komoly szferak (global_anchor/analysis/
                          press/economy/politics) elol, a tobbi utanuk.
        world_edition_date a vilag-lapszam datuma
        fresh_today       friss cikkek a korpuszbol. ⚠️ CSAK CIM ES FORRAS —
                          `lead` NINCS (`title_only: true`). A cim onmagaban NEM
                          azonosit egy hirt: a "paksi fenekkuszob" es a "dunai
                          fenekkuszob" cimbol ket kulon dolognak latszik, a
                          leadbol ugyanaz. Ezert ezeket a teteleket NE vond ossze
                          es NE allitsd rolik, hogy kulonboznek a lapszam-
                          sztoriktol — csak soroljd fel oket.
        _error            ha az adatut elromlott

== ⚠️ A LAPSZAM DATUMA — EZT KI KELL MONDANI ==
Az Echolot lapszama ~21:55 UTC-kor FAGY.
  - Ha `edition_is_today` HAMIS: a rovat cime legyen "LAPSZAM ({{edition_date}})",
    es a `fresh_today` teteleket KULON blokkban add "MA FRISSEN" cimmel.
  - Ha IGAZ: "MAI LAPSZAM ({{edition_date}})".
  - SOHA ne cimkezd "mai"-nak azt, ami tegnapi. Ez nem stilus kerdese: a bot
    egyszer mar eladott tegnapi lapszamot "mai top sztorik" cimke alatt.

== ESZKOZOK ==
  - `echolot_query` — a SAJAT korpuszunk (315 forras, 63 szfera, cim + lead).
    EZ AZ ELSODLEGES, ha a CONTEXT nem eleg. Peldak:
        echolot_query(spheres="hu_press", days=1)
        echolot_query(spheres="global_anchor,global_analysis", days=1)
    Tobbszor is hivhato egy korben, kulonbozo szfera-keszletekkel.
  - `web_search` — CSAK ha az Echolot sem ad eleg anyagot. Ilyenkor JELEZD a
    rovatnal, hogy web-keresesbol van.
MIERT EZ A SORREND: az Echolot a sajat, ellenorzott korpuszunk; a web-kereses
egy kulso szolgaltatas talalati listaja. Ha a sajat adatunk megvan, ne kerdezz
kivulrol.

== STRUKTURA ==
1) BELPOLITIKA / MAGYAR HIRSZEMLE — a `top_stories` tetelek, forrasszam szerinti
   sorrendben
   - MINDEN tetelnel ird ki a forrasszamot igy: "(N forras)"
   - a forrasszam a MERTEK: 6 forras erosebb hir, mint 2. Ne rendezd at.
   - a cimeket SZO SZERINT idezd. TILOS atfogalmazni: az alany-targy felcserelese
     ellenkezo allitast csinal (megtortent: "X nekiment Y-nak" -> "Y nekiment X-nek").
   - ha `edition_is_today` hamis, utana KULON: "MA FRISSEN" + a `fresh_today` cimek
2) KULPOLITIKA / GLOBALIS — a `world_stories` tetelek, A KAPOTT SORRENDBEN
   - MINDEN tetelnel: cim + "(N forras)" + a szfera zarojelben, pl. [global_anchor]
   - a lista MAR rendezve van (komoly szferak elol) — NE rendezd at
   - 4-6 tetel; a cimeket SZO SZERINT idezd, a `lead`-bol legfeljebb egy tomor
     magyar mondatot irj hozza
   - ha a `world_stories` URES: `echolot_query(spheres="global_anchor,global_analysis,global_press", days=1)`
   - a rovat cime: "VILAG — LAPSZAM ({{world_edition_date}})"
   ⚠️ EZ A ROVAT NEM MARADHAT URESEN. Ha sem a `world_stories`, sem az
   `echolot_query` nem ad anyagot, akkor `web_search`-csel hozz 3-4 vezeto
   kulpolitikai hirt, es JELEZD, hogy web-keresesbol van. Egy ures rovat nem
   termek — a hiany oszinte jelzese ONMAGABAN nem eleg a felhasznalonak.
3) NYERSANYAG- ES DEVIZAARAK — KIZAROLAG a CONTEXT fx_ecb + market_yahoo szekciokbol
   - EUR/HUF, USD/HUF (fx_ecb, ECB) · Gold (GC=F) · Brent (BZ=F) · WTI (CL=F) · BUX (^BUX.BD)
   - minden szamhoz: ertek + napi valtozas (change_pct) + forras cimke
   - MEGJEGYZES: a reszletes makro- es piaci elemzes KULON briefben megy
     (Economic Brief, 8:30 es 17:30). Itt csak a szamok, kommentar nelkul.

== KEMENY SZABALYOK ==
- MINDEN numerikus adatnak (ar, arfolyam, index, szazalek, barrel, oz) a FACTUAL
  CONTEXT-bol kell szarmaznia. Ha nincs ott: "[label: adat nem elerheto]".
- Ha az `echolot_hirszemle._error` ki van toltve, ird a brief ELEJERE:
  "FIGYELEM: az Echolot hirszemle nem elerheto (<ok>) — a magyar rovat web-keresesbol keszult."
  NE hallgasd el, es NE potold kitalalt hirekkel.
- TILOS arat/arfolyamot/indexet "becsles", "Bloomberg", "MNB kozeparfolyam" cimkevel
  kiirni, ha a CONTEXT nem mondja.
- TILOS betanitasi memoriabol szamot eloszedni (pl. "2350 USD", "395 HUF" — 2024-es halucinaciok).
- Hirek eseten: cim + forras. Szamot SOHA ne kolts a hirekhez.
- Ha a FACTUAL CONTEXT blokk teljesen hianyzik, ird ki eloszor NAGYBETUVEL:
  "HIBA: FACTUAL CONTEXT NINCS A PROMPTBAN — A PREFETCHER NEM FUTOTT LE."
- Ha a tool-valasz vegen `_bridge_served_by` vagy MODELLVALTAS jelzest latsz,
  MONDD KI egy zaro sorban, melyik szerv szolgalta ki.
- Magyar nyelv, markdown, max 600 szo.
- Zaro sor: "Forras: Echolot lapszam ({{edition_date}}) + ECB daily reference rates
  + Yahoo Finance live quotes (prefetch: a CONTEXT fetched_at mezoje)"
"""


def _is_ours(stored: str | None) -> bool:
    """Igaz, ha a tarolt prompt a MI kanonunk (barmely verzioja) — vagy ures.

    A kezzel irt szoveget nem bantjuk. A 2026-08-30 elotti allapotnak nincs
    jelolese, ezert azt is a mienknek vesszuk: az a szoveg is tolem szarmazik,
    ugyanezen a napon.
    """
    if not stored or not stored.strip():
        return True
    if "<!-- news_brief_prompt v" in stored:
        return True
    # jelöletlen, de felismerheto regi kanon
    return stored.lstrip().startswith("Készits napi HIRSZEMLET a Kommandantnak.")


def seed_briefs(conn, now_iso: str) -> list[str]:
    """Idempotens: a ket hirszemle-recept letezese, promptja es menetrendje.

    Sose dob; a hivo (plugins.recipes betoltes) egyetlen tranzakcioban futtatja.
    Visszaadja az elvegzett valtoztatasok emberi leirasat naplozashoz.
    """
    changed: list[str] = []
    for name, (_session, _label, cron, desc) in BRIEFS.items():
        row = conn.execute(
            "SELECT prompt_template, cron_schedule FROM pyramid_recipes WHERE name = ?",
            (name,)).fetchone()
        canon = prompt_for(name)
        if row is None:
            conn.execute(
                "INSERT INTO pyramid_recipes (name, description, required_tools, "
                "prompt_template, created_by, created_at, updated_at, cron_schedule, "
                "cron_model, cron_enabled, cron_delivery) "
                "VALUES (?, ?, ?, ?, 'system', ?, ?, ?, 'dsflash', 1, 'both')",
                (name, desc, '["web_search", "echolot_query"]', canon,
                 now_iso, now_iso, cron),
            )
            changed.append(f"{name}: LETREHOZVA (cron={cron})")
            continue

        # ── EGYSZERI, CELZOTT JAVITAS: a 2026-04-10-i elgepeles ──────────
        # A `14 16 * * *` NEM menetrend, hanem lenyomat: a Kommandant "4 orat"
        # kert, a Feldwebel `0 16` helyett `14 16`-ot irt (innen a 16:14). Ezt
        # az EGY erteket javitjuk, mert tudjuk, honnan van. Minden mas
        # menetrendhez NEM nyulunk — kulonben minden ujrainditas visszaallitana
        # a kanont a Kommandant sajat valasztasa fole.
        if row["cron_schedule"] == "14 16 * * *":
            conn.execute("UPDATE pyramid_recipes SET cron_schedule=?, updated_at=? "
                         "WHERE name=?", (cron, now_iso, name))
            changed.append(
                f"{name}: menetrend 14 16 * * * (=16:14, elgepeles 2026-04-10) -> {cron}")

        stored = row["prompt_template"]
        if stored != canon:
            if _is_ours(stored):
                conn.execute(
                    "UPDATE pyramid_recipes SET prompt_template = ?, description = ?, "
                    "updated_at = ? WHERE name = ?",
                    (canon, desc, now_iso, name))
                changed.append(f"{name}: prompt -> kanon v{PROMPT_VERSION}")
            else:
                # Kezi szerkesztes: NEM irjuk felul, de nem is hallgatjuk el.
                logger.warning(
                    "news_brief: %s prompt_template-je kezzel szerkesztett — a "
                    "kanon (v%d) NEM lett rairva. Ha a kanon kell, torold a kezi "
                    "szoveget.", name, PROMPT_VERSION)
    return changed
