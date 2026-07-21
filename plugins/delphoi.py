"""plugins/delphoi.py — DELPHOI: nyilvános entitás-nowcast (KIRAKAT) + fizetős
szintetikus fókuszcsoport (FOGÁS) — egy motor, két vég.

A DOKTRÍNA (öt doménen validált, memory: orakel_ii_KONSZILIENCIA_5_domen_szintezis):
  - Motor: vak/stale-priorú Flash (Non-Think) + datált korpusz-grounding +
    demográfia-only persona-panel + SSR-linear (NEM softmax).
  - Scope-törvény: kollektív NARRATÍVA-vezérelt kimenet IGEN, strukturális/mechanikus
    kimenet NEM (ESI-falszifikáció, Spearman −0,38 — scope-határ).
  - Abszolút-szám tabu: a kimenet RELATÍV/ordinális jel (irány, rangsor, delta),
    sosem abszolút százalék.
  - Terminológia-vasszabály: "szintetikus fókuszcsoport" / "narratíva-hatás
    szimuláció", SOHA nem "közvélemény-kutatás".

AZ IDŐBÉLYEG-INVARIÁNS (v2, N1.5) — a track record bizonyíték-gerince:
  1. A delphoi_nowcast_ledger APPEND-ONLY, DB-szinten kikényszerítve (két TRIGGER
     ABORT-tal öl minden UPDATE/DELETE-et).
  2. predicted_at = SZERVER-idő a beszúrás pillanatában, sosem paraméter.
  3. IGAZI hash-lánc: minden sor content_hash-ébe bekerül az ELŐZŐ sor
     content_hash-e (prev_hash). GLOBÁLIS lánc (nem entitásonkénti) — a v2-parancs
     ajánlása szerint az erősebb változat: BÁRMELY sor módosítása az összes
     későbbi sor hash-ét érvényteleníti. Genesis-sor: prev_hash='GENESIS'.
  4. corpus_hash DETERMINISTA recepttel rögzíti, mit látott a panel:
     SHA256( "|".join(sorted(snapshot_id_str)) + "|" + ablak_kezdet + "|" +
             ablak_veg + "|" + country ).
  5. Külső horgony: anchor_hash() hook a cron végén — env-kapu mögött ALSZIK
     (DELPHOI_ANCHOR_CHANNEL=off|git|agora), a csatorna a Kommandant szava.

A nowcast-korpusz KIEGYENSÚLYOZOTT: az ország teljes datált hír-ablaka
(brief+trending), NEM entitás-szűrt — a "szavazó agentek" általános
információs környezetet kapnak, abban ítélik meg az entitást.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("plugins.delphoi")

__plugin_meta__ = {
    "name": "delphoi",
    "version": "1.0.0",
    "description": "DELPHOI — nyilvanos entitas-nowcast (hash-lancolt ledger) + fizetos szintetikus fokuszcsoport",
}

GENESIS = "GENESIS"

# Motor-konfig (PYTHIA P1) — a pollster-rel közös doktrína (Non-Think kötelező,
# feltétel nélkül). A nowcast- és a FOGÁS-ág KÜLÖN env-kapcsolót kap, mindkettő
# default-ja a tencent/Hy3 (NULLTARIF; Kommandant 07-20: a FOGÁS is mehet Hy3-ra,
# Hy3-tiltó őr NINCS). Flash SEHOL nem default és nem silent fallback —
# motorhiba = hangos hiba (a pollster._chat exception-nel + loggal hasal el).
NOWCAST_MODEL = os.environ.get("DELPHOI_NOWCAST_MODEL", "tencent/Hy3")
FG_MODEL = os.environ.get("DELPHOI_FG_MODEL", "tencent/Hy3")
CONCURRENCY = int(os.environ.get("DELPHOI_CONCURRENCY", os.environ.get("ORAKEL_CONCURRENCY", "8")))

# A FOGÁS-ág panel-verziója (P1 bump — Hy3-regime; az Echolot echolot_orakel.py
# PANEL_VERSION-jével azonos string, a modellregime-váltás korszak-jelölője).
FG_PANEL_VERSION = "orakel-agora-hu-v2-hy3"

# Modellregime-korszakhatár megjegyzés (N5-sáv / irányfal-kimenet, P1).
REGIME_NOTE = "bázisreset — modellváltás, nem hasonlítható"
EMBED_MODEL_SF = "Qwen/Qwen3-Embedding-8B"  # többnyelvű (hu/pl/fr/it). A BAAI/bge-m3 kivezetve az SF-ről (code 20012, lásd mem #16568) — 2026-07-09 live-probe: ez él
EMBED_MODEL_OPENAI = "text-embedding-3-small"  # a backtesztek nyertese — env-kapu (OPENAI_API_KEY)


# ---------------------------------------------------------------------------
# P2 — VARIANCIA-ŐR + EMBED-SAPKA + KALIBRÁCIÓS KULCS (env-vezérelt, hívás-
# időben olvasva a tesztelhetőségért). A G1-lelet: seed/minta-átlagolás
# KÖTELEZŐ Hy3 alatt — pontbecslés egyetlen mintából TILOS.
# ---------------------------------------------------------------------------
def nowcast_samples() -> int:
    """DELPHOI_NOWCAST_SAMPLES — minta/persona a nowcast-ágon (G1: default 3).
    KÜLÖN env a FOGÁS-étól: a heti nowcast hívásszáma is k-szorozódik."""
    try:
        return max(1, int(os.environ.get("DELPHOI_NOWCAST_SAMPLES", "3")))
    except ValueError:
        return 3


def fg_samples() -> int:
    """DELPHOI_SAMPLES_PER_PERSONA — minta/persona a FOGÁS-ágon (default 3)."""
    try:
        return max(1, int(os.environ.get("DELPHOI_SAMPLES_PER_PERSONA", "3")))
    except ValueError:
        return 3


def panel_temperature() -> float:
    """DELPHOI_PANEL_TEMP — panel-hőmérséklet env-kapu, a [0.7, 1.0] sávra
    vágva (0 felé a k-mintás variancia-becslés kollabálna, 1 fölött zaj)."""
    try:
        t = float(os.environ.get("DELPHOI_PANEL_TEMP", "0.8"))
    except ValueError:
        t = 0.8
    return min(1.0, max(0.7, t))


def embed_budget() -> int:
    """DELPHOI_EMBED_BUDGET — napi embedding-token-sapka (G0d-ajánlás: 2M
    token ≈ $0.04/nap plafon; a runaway-loop ellen véd, nem a tervezett
    terhelés ellen). Túllépésnél HANGOS hiba, nem néma vágás."""
    try:
        return max(1, int(os.environ.get("DELPHOI_EMBED_BUDGET", "2000000")))
    except ValueError:
        return 2_000_000


# A nowcast szentiment-entitásainak kalibrációs kulcsa (G4-registry, cci-domén
# cellák). A regard-domén cellái még nincsenek a registry-ben — ott a
# calibrate() explicit no_entry-metával raw-t ad (a plumbing így is látszik).
NOWCAST_CAL_PANEL_VERSION = "pythia-cci-ssr-v1"

_DEPS: dict | None = None  # register_tools tölti (cron_entry fallback)


# ---------------------------------------------------------------------------
# ORSZÁG-KONFIG (Akna 2: lean-tengely országonként — perzisztens alignment +
# mindenkori-kormány derivált; a "kormányközeli" címke TILOS, elavul).
# A demográfiai marginálisok forrása HU-nál: KSH mun0005/mun0006 + 2022 cenzus
# + NMHH 2026-05 (a pollster.py-ban validált készlet). PL/FR/IT: Eurostat-közeli
# durvább marginálisok — az első kör szűk pont ezért (gondos lean-konfig/ország).
# media-elem: (label, súly, lean_bucket) — a bucket EXPLICIT, nem string-szimat.
# ---------------------------------------------------------------------------
COUNTRY_PANEL_CONFIG: dict = {
    "HU": {
        "lang": "hu",
        "priming": (
            "AKTUÁLIS HELYZET (2026): a 2026. áprilisi választást a TISZA Párt "
            "(Magyar Péter) nyerte, jelenleg ők kormányoznak; a Fidesz-KDNP "
            "(Orbán Viktor) ellenzékben. A közmédia a mindenkori kormányt — "
            "most a Tiszát — támogatja."
        ),
        "dims": {
            "age": [("18-29", 0.157), ("30-39", 0.160), ("40-49", 0.190), ("50-59", 0.184), ("60+", 0.309)],
            "settlement": [("Budapest", 0.18), ("megyeszékhely", 0.19), ("város", 0.31), ("község", 0.32)],
            "edu": [("max 8 általános", 0.187), ("szakmunkás", 0.161), ("érettségi", 0.350), ("diploma", 0.302)],
        },
        "media": [
            ("baloldali/liberális médiát követ (Telex, 24.hu, HVG, 444, RTL)", 0.26, "baloldali"),
            ("jobboldali médiát követ (Origo, Mandiner, Magyar Nemzet)", 0.22, "jobboldali"),
            ("közmédiát követ (köztévé — a mindenkori kormányt, most a Tiszát támogatja)", 0.18, "közmédia"),
            ("közösségi médiában fogyaszt politikai tartalmat (Facebook/TikTok/YouTube)", 0.22, "közösségi"),
            ("alig követi a politikai híreket", 0.12, "alig"),
        ],
    },
    "PL": {
        "lang": "pl",
        "priming": (
            "SYTUACJA (2026): rządzi koalicja Donalda Tuska (KO); prezydentem jest "
            "Karol Nawrocki (obóz PiS) — kohabitacja. Media publiczne wspierają "
            "aktualny rząd (teraz koalicję Tuska)."
        ),
        "dims": {
            "age": [("18-29", 0.15), ("30-39", 0.18), ("40-49", 0.19), ("50-59", 0.16), ("60+", 0.32)],
            "settlement": [("Warszawa/duże miasto", 0.24), ("miasto średnie", 0.25), ("małe miasto", 0.19), ("wieś", 0.32)],
            "edu": [("podstawowe/zawodowe", 0.36), ("średnie", 0.36), ("wyższe", 0.28)],
        },
        "media": [
            ("śledzi media liberalne (Gazeta Wyborcza, Onet, TVN24)", 0.30, "liberális"),
            ("śledzi media prawicowe (TV Republika, wPolityce, Do Rzeczy)", 0.24, "jobboldali"),
            ("śledzi media publiczne (TVP — wspiera aktualny rząd, teraz koalicję Tuska)", 0.16, "közmédia"),
            ("konsumuje treści polityczne w mediach społecznościowych", 0.18, "közösségi"),
            ("prawie nie śledzi wiadomości politycznych", 0.12, "alig"),
        ],
    },
    "FR": {
        "lang": "fr",
        "priming": (
            "SITUATION (2026) : Emmanuel Macron est président (mandat jusqu'en 2027), "
            "dans un paysage politique fragmenté (gauche NFP, centre, RN à droite). "
            "L'audiovisuel public (France Télévisions, Radio France) est institutionnel."
        ),
        "dims": {
            "age": [("18-29", 0.17), ("30-39", 0.15), ("40-49", 0.16), ("50-59", 0.16), ("60+", 0.36)],
            "settlement": [("Paris/grande métropole", 0.22), ("ville moyenne", 0.28), ("petite ville", 0.20), ("rural", 0.30)],
            "edu": [("sans bac", 0.35), ("bac", 0.25), ("supérieur", 0.40)],
        },
        "media": [
            ("suit des médias de gauche (Libération, Mediapart, France Inter)", 0.22, "baloldali"),
            ("suit des médias de droite (Le Figaro, CNews, Valeurs actuelles)", 0.24, "jobboldali"),
            ("suit l'audiovisuel public (France Télévisions, Radio France)", 0.20, "közmédia"),
            ("consomme l'actualité politique sur les réseaux sociaux", 0.20, "közösségi"),
            ("ne suit presque pas l'actualité politique", 0.14, "alig"),
        ],
    },
    "IT": {
        "lang": "it",
        "priming": (
            "SITUAZIONE (2026): governa Giorgia Meloni (Fratelli d'Italia, destra) "
            "dal 2022; opposizione PD e M5S. La RAI tende a sostenere il governo "
            "in carica (ora il governo Meloni)."
        ),
        "dims": {
            "age": [("18-29", 0.14), ("30-39", 0.14), ("40-49", 0.17), ("50-59", 0.17), ("60+", 0.38)],
            "settlement": [("grande città", 0.23), ("città media", 0.27), ("piccola città", 0.24), ("paese/rurale", 0.26)],
            "edu": [("licenza media", 0.38), ("diploma", 0.40), ("laurea", 0.22)],
        },
        "media": [
            ("segue media di sinistra (La Repubblica, La Stampa, Fatto Quotidiano)", 0.24, "baloldali"),
            ("segue media di destra (Il Giornale, Libero, Rete 4)", 0.22, "jobboldali"),
            ("segue la RAI (che tende a sostenere il governo in carica, ora Meloni)", 0.20, "közmédia"),
            ("consuma contenuti politici sui social media", 0.20, "közösségi"),
            ("quasi non segue le notizie politiche", 0.14, "alig"),
        ],
    },
}

# A FOGÁS (fizetős fókuszcsoport) ország-köre — Kommandant-parancs 2026-07-21:
# a nemzetközi user a saját piacát méri → HU/CZ/PT/PL + FR/DE/UK/US. FIGYELEM:
# a fókuszcsoport-panel elérhetősége NEM azonos a backteszt-validációval —
# választási/CCI backteszt HU/CZ/PT/FR/IT-n van; DE/UK/US "panel available,
# backtest validation in progress" (az aipolling methodology-oldal jelöli).
FG_COUNTRIES = tuple(
    c.strip().upper() for c in os.environ.get(
        "DELPHOI_FG_COUNTRIES", "HU,CZ,PT,PL,FR,DE,UK,US").split(",") if c.strip())

# CZ/PT panel-konfig — a fókuszcsoport-ághoz (a nowcast-seed nem hivatkozik
# rájuk). Marginálisok: Eurostat-közeli durvább készlet; média: perzisztens
# lean + intézményi közmédia (ČT/RTP), a "kormányközeli" címke itt is tilos.
COUNTRY_PANEL_CONFIG["CZ"] = {
    "lang": "cs",
    "priming": "SITUACE (2026): česká politická scéna po volbách 2025; veřejnoprávní ČT je institucionální.",
    "dims": {
        "age": [("18-29", 0.15), ("30-39", 0.17), ("40-49", 0.19), ("50-59", 0.16), ("60+", 0.33)],
        "settlement": [("Praha/velké město", 0.25), ("střední město", 0.27), ("malé město", 0.22), ("venkov", 0.26)],
        "edu": [("základní/vyučen", 0.42), ("maturita", 0.35), ("vysokoškolské", 0.23)],
    },
    "media": [
        ("sleduje liberální média (Seznam Zprávy, Deník N, Respekt)", 0.26, "baloldali"),
        ("sleduje pravicová/bulvární média (Blesk, Parlamentní listy)", 0.22, "jobboldali"),
        ("sleduje veřejnoprávní média (ČT, ČRo)", 0.22, "közmédia"),
        ("konzumuje politický obsah na sociálních sítích", 0.18, "közösségi"),
        ("téměř nesleduje politické zprávy", 0.12, "alig"),
    ],
}
COUNTRY_PANEL_CONFIG["PT"] = {
    "lang": "pt",
    "priming": "SITUAÇÃO (2026): cena política portuguesa; a RTP é institucional.",
    "dims": {
        "age": [("18-29", 0.14), ("30-39", 0.15), ("40-49", 0.18), ("50-59", 0.17), ("60+", 0.36)],
        "settlement": [("Lisboa/Porto", 0.28), ("cidade média", 0.27), ("vila", 0.20), ("rural", 0.25)],
        "edu": [("básico", 0.40), ("secundário", 0.33), ("superior", 0.27)],
    },
    "media": [
        ("segue media de esquerda/liberais (Público, Expresso)", 0.24, "baloldali"),
        ("segue media de direita/popular (CM, Observador)", 0.24, "jobboldali"),
        ("segue os media públicos (RTP)", 0.20, "közmédia"),
        ("consome conteúdo político nas redes sociais", 0.20, "közösségi"),
        ("quase não segue notícias políticas", 0.12, "alig"),
    ],
}

# ── G3 (ORSZÁG-BŐVÍTÉS, 2026-07-21): DE/UK/US panel-konfig a FOGÁS-ághoz ──
# DE: a lean_konfig_tervek.md terve kódban; marginálisok Eurostat-lekérdezésből
#   (statdata MCP, 2026-07-21): demo_pjangroup 2025 (18+ bázis 69,58M; a 18-19
#   sáv a 15-19 csoport 2/5-e), edat_lfs_9913 2024 (ED0-2 16,3 / ED3-4 50,1 /
#   ED5-8 33,6%), ilc_lvho01 2024 degurba (Cities 38,9 / Towns 42,4 / Rural 18,6).
#   Az ARD/ZDF gremiengesteuert → intézményi, NEM mindenkori-kormány derivált.
# UK: Eurostat nem szolgál friss UK-t (G0c) → STATIKUS, ONS-alapú kvóták:
#   kor = ONS mid-2023 population estimates (18+ durva sávok); iskola = Census
#   2021 (England&Wales, Level 4+) + OECD EAG (25-64 tertiary ~45-50%) →
#   degree-or-higher 0.45; település = ONS/DEFRA rural-urban classification
#   közeli becslés. A BBC Royal Charter-intézményi.
# US: STATIKUS, Census/ACS-alapú kvóták: kor = Census Bureau 2023 national
#   population estimates (18+); iskola = ACS 2023 educational attainment (25+:
#   HS-or-less ~36 / some college-associate ~28 / BA+ ~36); település = Census
#   2020 urban-rural (80/20) + metró-bontás. Média-tengelyek a UK-minta szerint:
#   Fox/CNN/hálózati/közösségi (Pew news-platform mérések szelleme, nincs
#   közmédia-tengely — az USA-ban nincs releváns elérésű public broadcaster).
# UK/US lang="en" → a nyelvi korpusz GLOBÁLIS; a source_prefixes kulcs mondja
# meg a build_country_corpus-nak, mely Echolot forrás-szegmens az országé.
COUNTRY_PANEL_CONFIG["DE"] = {
    "lang": "de",
    "priming": ("LAGE (2026): Bundeskanzler Friedrich Merz (CDU/CSU–SPD-Koalition) "
                "seit Mai 2025; stärkste Oppositionskraft die AfD. Der öffentlich-"
                "rechtliche Rundfunk (ARD/ZDF) ist institutionell, gremiengesteuert."),
    "dims": {
        "age": [("18-29", 0.157), ("30-39", 0.157), ("40-49", 0.150), ("50-59", 0.170), ("60+", 0.366)],
        "settlement": [("Großstadt", 0.39), ("Mittelstadt/Vorort", 0.42), ("ländlich", 0.19)],
        "edu": [("ohne Berufs-/Studienabschluss", 0.16), ("Lehre/Abitur/Fachschule", 0.50), ("Hochschulabschluss", 0.34)],
    },
    "media": [
        ("folgt linksliberalen Medien (SZ, Spiegel, Zeit, taz)", 0.26, "baloldali"),
        ("folgt konservativen/rechten Medien (FAZ, Welt, Bild, NIUS)", 0.24, "jobboldali"),
        ("folgt dem öffentlich-rechtlichen Rundfunk (ARD, ZDF, DLF)", 0.20, "közmédia"),
        ("konsumiert politische Inhalte in sozialen Medien", 0.18, "közösségi"),
        ("verfolgt politische Nachrichten kaum", 0.12, "alig"),
    ],
}
COUNTRY_PANEL_CONFIG["UK"] = {
    "lang": "en",
    "source_prefixes": ("uk_",),   # Echolot: 53 uk_* forrás; a nyelvi 'en' korpusz globális!
    "priming": ("SITUATION (2026): Keir Starmer's Labour government (since July 2024); "
                "main opposition Conservatives, with Reform UK rising. The BBC is "
                "institutional, governed by Royal Charter."),
    "dims": {
        "age": [("18-29", 0.18), ("30-39", 0.17), ("40-49", 0.16), ("50-59", 0.16), ("60+", 0.33)],
        "settlement": [("London/major city", 0.30), ("town/suburban", 0.45), ("rural", 0.25)],
        "edu": [("no degree", 0.55), ("degree or higher", 0.45)],
    },
    "media": [
        ("follows left-liberal media (Guardian, Mirror, Independent)", 0.24, "baloldali"),
        ("follows right-leaning media (Telegraph, Times, Daily Mail, Sun, GB News)", 0.28, "jobboldali"),
        ("follows the BBC", 0.22, "közmédia"),
        ("consumes political content on social media", 0.16, "közösségi"),
        ("hardly follows political news", 0.10, "alig"),
    ],
}
COUNTRY_PANEL_CONFIG["US"] = {
    "lang": "en",
    "source_prefixes": ("us_",),   # Echolot: 39 us_* forrás
    "priming": ("SITUATION (2026): President Donald Trump (Republican) is in his second "
                "term since January 2025; Democrats are the opposition, with midterm "
                "elections due in November 2026. Cable news is sharply polarized; there "
                "is no public broadcaster with significant reach."),
    "dims": {
        "age": [("18-29", 0.20), ("30-39", 0.17), ("40-49", 0.16), ("50-59", 0.16), ("60+", 0.31)],
        "settlement": [("big city/urban core", 0.31), ("suburban/small metro", 0.49), ("rural", 0.20)],
        "edu": [("high school or less", 0.36), ("some college/associate", 0.28), ("bachelor's or higher", 0.36)],
    },
    "media": [
        ("follows liberal-leaning media (CNN, MSNBC, NYT, Washington Post)", 0.24, "baloldali"),
        ("follows right-leaning media (Fox News, NY Post, Newsmax, talk radio)", 0.24, "jobboldali"),
        ("follows network/local TV news (ABC, CBS, NBC, local stations)", 0.20, "hálózati"),
        ("gets political content mainly from social media (Facebook/YouTube/TikTok/X)", 0.22, "közösségi"),
        ("hardly follows political news", 0.10, "alig"),
    ],
}

# ---------------------------------------------------------------------------
# SSR anchor-halmazok — 5 pontos, 1=erősen negatív mozdulás … 5=erősen pozitív.
# A REGARD az entitás-megítélés irányát méri (politician/party); a MOOD_* a
# szentiment-várakozásokat (Akna 1: "hangulat/közérzet", SOHA nem "GDP-jóslat").
# ---------------------------------------------------------------------------
REFERENCE_SETS_REGARD = {
    "hu": [
        "A mostani hírek alapján sokkal rosszabb lett róla a véleményem.",
        "A mostani hírek alapján kicsit rosszabb lett róla a véleményem.",
        "A mostani hírek nem változtattak a véleményemen.",
        "A mostani hírek alapján kicsit jobb lett róla a véleményem.",
        "A mostani hírek alapján sokkal jobb lett róla a véleményem.",
    ],
    "pl": [
        "Po ostatnich wiadomościach moja opinia o nim znacznie się pogorszyła.",
        "Po ostatnich wiadomościach moja opinia o nim nieco się pogorszyła.",
        "Ostatnie wiadomości nie zmieniły mojej opinii.",
        "Po ostatnich wiadomościach moja opinia o nim nieco się poprawiła.",
        "Po ostatnich wiadomościach moja opinia o nim znacznie się poprawiła.",
    ],
    "fr": [
        "Après les nouvelles récentes, mon opinion s'est fortement dégradée.",
        "Après les nouvelles récentes, mon opinion s'est un peu dégradée.",
        "Les nouvelles récentes n'ont pas changé mon opinion.",
        "Après les nouvelles récentes, mon opinion s'est un peu améliorée.",
        "Après les nouvelles récentes, mon opinion s'est fortement améliorée.",
    ],
    "it": [
        "Dopo le notizie recenti la mia opinione è molto peggiorata.",
        "Dopo le notizie recenti la mia opinione è un po' peggiorata.",
        "Le notizie recenti non hanno cambiato la mia opinione.",
        "Dopo le notizie recenti la mia opinione è un po' migliorata.",
        "Dopo le notizie recenti la mia opinione è molto migliorata.",
    ],
}

# Ár/inflációs várakozás (1=csökkenő árak … 5=sokkal gyorsabb drágulás) — a
# ssr.REFERENCE_SETS_PRICE HU/PL készletét használjuk, FR/IT itt pótolva.
REFERENCE_SETS_PRICE_EXTRA = {
    "fr": [
        "Les prix en magasin, l'énergie et le carburant vont plutôt baisser dans les 12 prochains mois.",
        "Les prix vont rester à peu près les mêmes.",
        "Les prix vont augmenter, mais plus lentement que l'année passée.",
        "Les prix vont continuer à augmenter à peu près au même rythme.",
        "Les prix vont augmenter beaucoup plus vite qu'avant.",
    ],
    "it": [
        "I prezzi nei negozi, l'energia e i carburanti tenderanno a scendere nei prossimi 12 mesi.",
        "I prezzi resteranno più o meno gli stessi.",
        "I prezzi saliranno, ma più lentamente dell'anno scorso.",
        "I prezzi continueranno a salire più o meno allo stesso ritmo.",
        "I prezzi saliranno molto più velocemente di prima.",
    ],
}

# Növekedési hangulat / gazdasági közérzet (1=sokkal romlik … 5=sokkal javul).
REFERENCE_SETS_GROWTH = {
    "hu": [
        "Az ország gazdasági helyzete sokkal rosszabb lesz a következő évben.",
        "Az ország gazdasági helyzete kissé romlik a következő évben.",
        "Az ország gazdasági helyzete nagyjából ugyanolyan marad.",
        "Az ország gazdasági helyzete kissé javul a következő évben.",
        "Az ország gazdasági helyzete sokkal jobb lesz a következő évben.",
    ],
    "pl": [
        "Sytuacja gospodarcza kraju znacznie się pogorszy w przyszłym roku.",
        "Sytuacja gospodarcza kraju nieco się pogorszy w przyszłym roku.",
        "Sytuacja gospodarcza kraju pozostanie mniej więcej taka sama.",
        "Sytuacja gospodarcza kraju nieco się poprawi w przyszłym roku.",
        "Sytuacja gospodarcza kraju znacznie się poprawi w przyszłym roku.",
    ],
    "fr": [
        "La situation économique du pays va fortement se dégrader l'an prochain.",
        "La situation économique du pays va un peu se dégrader l'an prochain.",
        "La situation économique du pays va rester à peu près la même.",
        "La situation économique du pays va un peu s'améliorer l'an prochain.",
        "La situation économique du pays va fortement s'améliorer l'an prochain.",
    ],
    "it": [
        "La situazione economica del paese peggiorerà molto l'anno prossimo.",
        "La situazione economica del paese peggiorerà un po' l'anno prossimo.",
        "La situazione economica del paese resterà più o meno la stessa.",
        "La situazione economica del paese migliorerà un po' l'anno prossimo.",
        "La situazione economica del paese migliorerà molto l'anno prossimo.",
    ],
}

# A nowcast-kérdés sablonjai (lang, kind) szerint — a persona EGY őszinte,
# szabad mondatot ír (az SSR bemenete), nem számot (variancia-összeomlás ellen).
NOWCAST_QUESTIONS = {
    ("hu", "regard"): (
        "A fenti hírhelyzet ÖSSZESSÉGÉBEN merre mozdítja a véleményedet erről: {entity}? "
        "Válaszolj EGYETLEN őszinte mondattal, a saját szemszögedből."
    ),
    ("pl", "regard"): (
        "Biorąc pod uwagę powyższe wiadomości, w którą stronę zmienia się Twoja opinia o: {entity}? "
        "Odpowiedz JEDNYM szczerym zdaniem, z własnej perspektywy."
    ),
    ("fr", "regard"): (
        "Compte tenu de l'actualité ci-dessus, dans quel sens évolue votre opinion sur : {entity} ? "
        "Répondez par UNE seule phrase honnête, de votre point de vue."
    ),
    ("it", "regard"): (
        "Alla luce delle notizie sopra, in che direzione cambia la tua opinione su: {entity}? "
        "Rispondi con UNA sola frase onesta, dal tuo punto di vista."
    ),
    ("hu", "price"): (
        "A fenti hírhelyzet fényében mit vársz: hogyan alakulnak a bolti árak, a rezsi és az "
        "üzemanyag a következő 12 hónapban? Válaszolj EGYETLEN őszinte mondattal."
    ),
    ("pl", "price"): (
        "W świetle powyższych wiadomości: jak Twoim zdaniem zmienią się ceny w sklepach, rachunki "
        "i paliwo w ciągu najbliższych 12 miesięcy? Odpowiedz JEDNYM szczerym zdaniem."
    ),
    ("fr", "price"): (
        "À la lumière de l'actualité ci-dessus : comment vont évoluer les prix, l'énergie et le "
        "carburant dans les 12 prochains mois selon vous ? Répondez par UNE seule phrase honnête."
    ),
    ("it", "price"): (
        "Alla luce delle notizie sopra: come cambieranno secondo te i prezzi, le bollette e i "
        "carburanti nei prossimi 12 mesi? Rispondi con UNA sola frase onesta."
    ),
    ("hu", "growth"): (
        "A fenti hírhelyzet fényében milyennek látod az ország gazdasági kilátásait a következő "
        "évre — a saját közérzeted szerint? Válaszolj EGYETLEN őszinte mondattal."
    ),
    ("pl", "growth"): (
        "W świetle powyższych wiadomości: jak widzisz perspektywy gospodarcze kraju na przyszły "
        "rok — według własnego odczucia? Odpowiedz JEDNYM szczerym zdaniem."
    ),
    ("fr", "growth"): (
        "À la lumière de l'actualité ci-dessus : comment voyez-vous les perspectives économiques "
        "du pays pour l'année à venir — selon votre ressenti ? Répondez par UNE seule phrase honnête."
    ),
    ("it", "growth"): (
        "Alla luce delle notizie sopra: come vedi le prospettive economiche del paese per il "
        "prossimo anno — secondo il tuo sentire? Rispondi con UNA sola frase onesta."
    ),
}

# Akna 1 — STRUKTURÁLIS CSAPDA: a display_label nem sugallhat mechanikus
# kimenetet (ESI-falszifikáció, Spearman −0,38). A seed és minden új regiszter-
# sor átmegy ezen a szűrőn.
_FORBIDDEN_LABEL_PATTERNS = (
    "gdp", "előrejelz", "elorejelz", "forecast", "prognóz", "prognoz",
    "predikció", "prediction", "jóslat", "joslat", "árfolyam", "arfolyam",
)


def validate_display_label(label: str) -> None:
    low = (label or "").lower()
    for pat in _FORBIDDEN_LABEL_PATTERNS:
        if pat in low:
            raise ValueError(
                f"display_label strukturális kimenetet sugall ({pat!r} a címkében): {label!r} — "
                "a DELPHOI hangulatot/megítélést mér, nem mechanikus kimenetet (Akna 1)."
            )


# ---------------------------------------------------------------------------
# SÉMA — N-ág: regiszter (mutable config) + predikció-napló (APPEND-ONLY).
# A két TRIGGER a v2-parancs N1.5/1 pontja: az append-only nem kódfegyelem,
# hanem DB-garancia — UPDATE/DELETE fizikailag ABORT-tal hasal el.
# ---------------------------------------------------------------------------
_INIT_SQL = """
CREATE TABLE IF NOT EXISTS delphoi_entity_nowcast (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_key    TEXT NOT NULL,
    country       TEXT NOT NULL,
    entity_type   TEXT NOT NULL,
    display_label TEXT NOT NULL,
    enabled       BOOLEAN DEFAULT 1,
    created_at    TEXT NOT NULL,
    UNIQUE(entity_key, country)
);

CREATE TABLE IF NOT EXISTS delphoi_nowcast_ledger (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_key     TEXT NOT NULL,
    country        TEXT NOT NULL,
    predicted_at   TEXT NOT NULL,
    target_window  TEXT NOT NULL,
    direction      REAL NOT NULL,
    direction_prev REAL,
    corpus_hash    TEXT NOT NULL,
    model_id       TEXT NOT NULL,
    segment_json   TEXT,
    prev_hash      TEXT NOT NULL,
    content_hash   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_entity ON delphoi_nowcast_ledger(entity_key, predicted_at);

CREATE TRIGGER IF NOT EXISTS delphoi_ledger_no_update
BEFORE UPDATE ON delphoi_nowcast_ledger
BEGIN SELECT RAISE(ABORT, 'delphoi_nowcast_ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS delphoi_ledger_no_delete
BEFORE DELETE ON delphoi_nowcast_ledger
BEGIN SELECT RAISE(ABORT, 'delphoi_nowcast_ledger is append-only'); END;

-- P2: napi SSR-embedding token-számláló (DELPHOI_EMBED_BUDGET sapka őre)
CREATE TABLE IF NOT EXISTS delphoi_embed_usage (
    day    TEXT PRIMARY KEY,
    tokens INTEGER NOT NULL DEFAULT 0
);
"""

# ELSŐ MENET entitás-kör (N2, Kommandant-jóváhagyott). A 2. kör (UK/US) seedelve,
# de enabled=0 — env/config-flip, ha az első bevált.
#
# P5 / G4-verdikt (orakel_backteszt/orszag_matrix_20260720/matrix.md, 2026-07-20):
#   - fr-novekedesi-hangulat + it-novekedesi-hangulat: REGISZTRÁLVA enabled=0 —
#     FR/IT a mátrixban IGAZOLHATÓ (korpusz 0.89/0.90, lean-konfig nowcast-grade,
#     Eurostat-GT él); a flip Kommandant-szóra megy, itt NEM történik.
#   - pl-inflacios-varakozas: FELTÉTELES-jelölt — a PL korpusz átmegy (0.72, de
#     40 forrás < 60-küszöb, vékony bal-oldal), ám az inflációs-várakozás
#     kalibrációjához KATEGORIKUS réteg kellene (delphoi_calibration: a
#     kategorikus kalibráció TILOS-státuszú) → marad enabled=0, amíg a
#     kategorikus réteg nincs meg. Flip itt SEM történik.
_SEED_ENTITIES = [
    # (entity_key, country, entity_type, display_label, enabled)
    # entity_key = Wikidata QID, ahol van (az Echolot entitás-rétegének kulcsa);
    # szentiment-entitásoknál és QID-hiánynál slug (a REST label-fallback köti).
    ("Q124488292", "HU", "politician", "Magyar Péter megítélése", 1),
    ("tisza-part", "HU", "party", "TISZA Párt megítélése", 1),
    ("Q387006", "HU", "party", "Fidesz megítélése", 1),
    ("Q948", "PL", "politician", "Donald Tusk megítélése", 1),
    ("Q3052772", "FR", "politician", "Emmanuel Macron megítélése", 1),
    ("Q451791", "IT", "politician", "Giorgia Meloni megítélése", 1),
    ("hu-inflacios-varakozas", "HU", "sentiment_expectation", "Inflációs várakozás — hangulat (HU)", 1),
    ("hu-novekedesi-hangulat", "HU", "sentiment_expectation", "Növekedési hangulat / gazdasági közérzet (HU)", 1),
    ("pl-inflacios-varakozas", "PL", "sentiment_expectation", "Inflációs várakozás — hangulat (PL)", 0),
    ("fr-novekedesi-hangulat", "FR", "sentiment_expectation", "Növekedési hangulat / gazdasági közérzet (FR)", 0),
    ("it-novekedesi-hangulat", "IT", "sentiment_expectation", "Növekedési hangulat / gazdasági közérzet (IT)", 0),
    # 2. KÖR — flag mögött (durvább lean vállalva, ha élesítjük)
    ("keir-starmer", "UK", "politician", "Keir Starmer megítélése", 0),
    ("nigel-farage", "UK", "politician", "Nigel Farage megítélése", 0),
    ("Q22686", "US", "politician", "Donald Trump megítélése", 0),
]


def ensure_tables(conn) -> None:
    """Idempotens séma-létrehozás — register_tools ÉS a tesztek hívják."""
    conn.executescript(_INIT_SQL)
    conn.commit()


def seed_registry(conn) -> int:
    """Idempotens entitás-seed (UNIQUE(entity_key, country) véd). A display_label
    átmegy az Akna-1 szűrőn — strukturális címke be sem kerülhet."""
    n = 0
    ts = datetime.now(timezone.utc).isoformat()
    for key, country, etype, label, enabled in _SEED_ENTITIES:
        validate_display_label(label)
        cur = conn.execute(
            "INSERT OR IGNORE INTO delphoi_entity_nowcast "
            "(entity_key, country, entity_type, display_label, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, country, etype, label, enabled, ts),
        )
        n += cur.rowcount or 0
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# HASH-LÁNC (N1.5) — tiszta függvények, teszt alattuk.
# ---------------------------------------------------------------------------
def compute_corpus_hash(snapshot_ids, window_start: str, window_end: str, country: str) -> str:
    """DETERMINISTA korpusz-lenyomat (N1.5/4): az ID-k RENDEZVE, az ablakhatárok
    ISO-stringként — ugyanaz az ablak MINDIG ugyanazt a hash-t adja."""
    ids_sorted = sorted(str(i) for i in snapshot_ids)
    payload = "|".join(ids_sorted) + "|" + str(window_start) + "|" + str(window_end) + "|" + str(country)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_content_hash(entity_key: str, predicted_at: str, target_window: str,
                         direction: float, corpus_hash: str, prev_hash: str) -> str:
    """Sor-lenyomat, benne az ELŐZŐ sor hash-ével (prev_hash) — ez teszi lánccá.
    A direction fix 6 tizedesre formázva (a REAL float-reprezentáció ne lebegjen)."""
    payload = "|".join([
        str(entity_key), str(predicted_at), str(target_window),
        f"{float(direction):.6f}", str(corpus_hash), str(prev_hash),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _last_chain_hash(conn) -> str:
    """A GLOBÁLIS lánc utolsó sorának content_hash-e (nincs sor → GENESIS)."""
    row = conn.execute(
        "SELECT content_hash FROM delphoi_nowcast_ledger ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["content_hash"] if row else GENESIS


def append_ledger_row(get_db, entity_key: str, country: str, target_window: str,
                      direction: float, corpus_hash: str, model_id: str,
                      segment_json: str | None = None) -> dict:
    """EGYETLEN út a ledgerbe: INSERT. A predicted_at ITT, a szerver órájából
    születik (N1.5/2) — nem paraméter, nem visszadátumozható. A prev_hash a
    lánc utolsó sora; a direction_prev az entitás előző jele (a nyílhoz)."""
    conn = get_db()
    try:
        predicted_at = datetime.now(timezone.utc).isoformat()
        prev = conn.execute(
            "SELECT direction FROM delphoi_nowcast_ledger WHERE entity_key=? AND country=? "
            "ORDER BY id DESC LIMIT 1", (entity_key, country)).fetchone()
        direction_prev = float(prev["direction"]) if prev else None
        prev_hash = _last_chain_hash(conn)
        content_hash = compute_content_hash(
            entity_key, predicted_at, target_window, direction, corpus_hash, prev_hash)
        conn.execute(
            "INSERT INTO delphoi_nowcast_ledger "
            "(entity_key, country, predicted_at, target_window, direction, direction_prev, "
            " corpus_hash, model_id, segment_json, prev_hash, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entity_key, country, predicted_at, target_window, float(direction), direction_prev,
             corpus_hash, model_id, segment_json, prev_hash, content_hash),
        )
        conn.commit()
        return {"entity_key": entity_key, "country": country, "predicted_at": predicted_at,
                "target_window": target_window, "direction": float(direction),
                "direction_prev": direction_prev, "corpus_hash": corpus_hash,
                "prev_hash": prev_hash, "content_hash": content_hash}
    finally:
        conn.close()


def verify_ledger_chain(get_db) -> dict:
    """Audit-eszköz (a nowcaster-feed nagy vevőnek is): végigmegy a GLOBÁLIS
    láncon, újraszámolja a content_hash-eket és ellenőrzi a prev_hash-fűzést.
    Bármely korábbi sor módosítása az összes későbbi sort érvényteleníti."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, entity_key, predicted_at, target_window, direction, corpus_hash, "
            "prev_hash, content_hash FROM delphoi_nowcast_ledger ORDER BY id ASC").fetchall()
    finally:
        conn.close()
    expected_prev = GENESIS
    for r in rows:
        if r["prev_hash"] != expected_prev:
            return {"ok": False, "checked": len(rows), "first_bad_id": r["id"],
                    "reason": f"prev_hash mismatch (várt: {expected_prev[:12]}…, kapott: {str(r['prev_hash'])[:12]}…)"}
        recomputed = compute_content_hash(
            r["entity_key"], r["predicted_at"], r["target_window"],
            r["direction"], r["corpus_hash"], r["prev_hash"])
        if recomputed != r["content_hash"]:
            return {"ok": False, "checked": len(rows), "first_bad_id": r["id"],
                    "reason": "content_hash mismatch (a sor tartalma módosult)"}
        expected_prev = r["content_hash"]
    return {"ok": True, "checked": len(rows), "head": expected_prev if rows else GENESIS}


# ---------------------------------------------------------------------------
# MODELLREGIME-VÁLTÁS (P1) — az első Hy3-sor korszakhatár: ahol a direction_prev
# egy Flash-korszakú sorra mutat, a kimenet "model_regime_boundary": true jelzést
# + a delta mellé REGIME_NOTE megjegyzést kap. Régi sorok model_id=NULL =
# Flash-korszak (migráció előtti sorok, sor-érintés tilos).
# ---------------------------------------------------------------------------
def _model_era(model_id) -> str:
    """Ledger-sor modell-korszaka. NULL/üres = Flash-korszak (P1 előtti sor);
    'Hy3' a model_id-ban = hy3; 'Flash' = flash; egyéb: a modellnév-prefix."""
    if not model_id:
        return "flash"
    low = str(model_id).lower()
    if "hy3" in low:
        return "hy3"
    if "flash" in low:
        return "flash"
    return low.split("|", 1)[0]


def _annotate_regime(conn, rows: list[dict]) -> list[dict]:
    """Ledger-sor dict-ek in-place korszakhatár-jelölése. Minden sorhoz az
    entitás ELŐZŐ ledger-sorának (a direction_prev forrásának) korszakát nézi:
    eltérő korszak → model_regime_boundary=True + regime_note. A sorokhoz
    'id', 'entity_key', 'country' és 'model_id' mező kell."""
    for r in rows:
        prev = conn.execute(
            "SELECT model_id FROM delphoi_nowcast_ledger "
            "WHERE entity_key=? AND country=? AND id<? ORDER BY id DESC LIMIT 1",
            (r["entity_key"], r["country"], r["id"])).fetchone()
        if prev is None:
            continue  # nincs előző jel — nincs delta, nincs korszakhatár
        if _model_era(r.get("model_id")) != _model_era(prev["model_id"]):
            r["model_regime_boundary"] = True
            r["regime_note"] = REGIME_NOTE
    return rows


def public_nowcast_feed(get_db, entity_key: str = "", label: str = "",
                        history: int = 12) -> dict:
    """A NYILVÁNOS kirakat-feed (Echolot N5-sáv adatforrása) — a server.py
    /api/delphoi/nowcast végpontja deploykor erre delegál. A history-sorok
    korszakhatár-jelölést kapnak (model_regime_boundary + regime_note)."""
    history = min(52, max(1, int(history)))
    conn = get_db()
    try:
        sql = ("SELECT entity_key, country, entity_type, display_label "
               "FROM delphoi_entity_nowcast WHERE enabled=1")
        params: list = []
        if entity_key:
            sql += " AND entity_key = ?"; params.append(entity_key)
        elif label:
            sql += " AND display_label LIKE ?"; params.append(f"%{label}%")
        try:
            ents = conn.execute(sql, params).fetchall()
        except Exception:  # noqa: BLE001 — table may not exist yet
            ents = []
        out = []
        for e in ents:
            try:
                rows = conn.execute(
                    "SELECT id, entity_key, country, predicted_at, target_window, "
                    "direction, direction_prev, corpus_hash, content_hash, model_id "
                    "FROM delphoi_nowcast_ledger WHERE entity_key=? AND country=? "
                    "ORDER BY id DESC LIMIT ?",
                    (e["entity_key"], e["country"], history)).fetchall()
                hist = _annotate_regime(conn, [dict(r) for r in rows])
            except Exception:  # noqa: BLE001 — migráció előtti DB: legacy oszlopkészlet
                rows = conn.execute(
                    "SELECT predicted_at, target_window, direction, direction_prev, "
                    "corpus_hash, content_hash FROM delphoi_nowcast_ledger "
                    "WHERE entity_key=? AND country=? ORDER BY id DESC LIMIT ?",
                    (e["entity_key"], e["country"], history)).fetchall()
                hist = [dict(r) for r in rows]
            # P2 — kalibráció a feed "latest" sorára: NYERS és KALIBRÁLT érték
            # verzió-címkével (a ledger-sor maga érintetlen, a réteg on-the-fly).
            latest = hist[0] if hist else None
            if latest is not None and latest.get("direction") is not None:
                try:
                    from plugins import delphoi_calibration as _calib
                    domain = "cci" if e["entity_type"] == "sentiment_expectation" else "regard"
                    raw_balance = round(float(latest["direction"]) * 100.0, 3)  # panel-szaldó skála
                    calibrated, cal_meta = _calib.calibrate(
                        raw_balance, e["country"], domain, NOWCAST_CAL_PANEL_VERSION,
                        latest.get("model_id") or NOWCAST_MODEL)
                    latest["calibration"] = {"raw": raw_balance, "calibrated": calibrated, **cal_meta}
                except Exception:  # noqa: BLE001
                    logger.debug("delphoi feed calibration skipped", exc_info=True)
            out.append({"entity_key": e["entity_key"], "country": e["country"],
                        "entity_type": e["entity_type"], "display_label": e["display_label"],
                        "latest": latest, "history": hist})
        # P5: a lánc-fej a feedben — a fogyasztó (irányfal verify-badge) enélkül
        # is ellenőrizhet a /api/delphoi/verify-on, de így egy kérésből megvan.
        try:
            chain_head = _last_chain_hash(conn)
        except Exception:  # noqa: BLE001 — migráció előtti DB: nincs ledger-tábla
            chain_head = None
    finally:
        conn.close()
    # MOD2/A6 — a payload BRAND-SEMLEGES: a disclaimer a delphoi_brands közös,
    # semleges szövege (arc-specifikus szöveg a publikus adat-API-ban TILOS).
    from plugins.delphoi_brands import public_disclaimer
    return {"count": len(out), "entities": out, "chain_head": chain_head,
            "disclaimer": public_disclaimer()}


def _agora_anchor_post_text(head: str, checked: int, stamp_iso: str) -> tuple[str, str]:
    """A heti lánc-pecsét Agora-poszt (cím, törzs). A törzs ≥200 karakter (az
    Echolot publish-minimum) és brand-URL nélkül is értelmes: a verify-út a
    brand public_base_url-jéről jön (delphoi_brands — az Echolot /api/delphoi/
    verify pass-through-ja a Bridge-re mutat)."""
    from plugins.delphoi_brands import get_brand
    try:
        verify_url = get_brand()["public_base_url"].rstrip("/") + "/api/delphoi/verify"
    except Exception:  # noqa: BLE001 — brand-config hiba ne törje a pecsétet
        verify_url = "/api/delphoi/verify"
    title = f"DELPHOI lánc-pecsét — {stamp_iso[:10]}"
    body = (
        "Heti kriptográfiai pecsét a DELPHOI predikció-naplóról. A napló "
        "append-only, hash-láncolt: minden sor lenyomata tartalmazza az előző "
        "sorét, így bármely korábbi jel utólagos módosítása az összes későbbi "
        "sor hash-ét érvényteleníti. Ez a poszt a lánc mai fejét rögzíti "
        "nyilvánosan — a track record így kívülről is auditálható.\n\n"
        f"Lánc-fej (SHA-256): {head}\n"
        f"Ellenőrzött sorok: {checked}\n"
        f"Pecsét ideje: {stamp_iso} UTC\n"
        f"Független ellenőrzés: {verify_url}\n\n"
        "Szintetikus panel relatív irányjelzése — nem közvélemény-kutatás és "
        "nem abszolút mérés."
    )
    return title, body


async def _agora_anchor_publish(title: str, body: str) -> dict:
    """A tényleges Agora-publish a nowcaster-agent operátor-kulcsával
    (DELPHOI_ANCHOR_AGORA_KEY env; agent_label opcionális felülírás)."""
    import _echolot_client as ec
    key = os.environ.get("DELPHOI_ANCHOR_AGORA_KEY", "")
    res = await ec.agora_action(
        "publish", operator_key=key, title=title, body=body, lang="hu",
        agent_label=os.environ.get("DELPHOI_ANCHOR_AGORA_LABEL", ""))
    return res if isinstance(res, dict) else {"ok": False, "error": str(res)}


def anchor_hash(get_db, repo_root: str | None = None, publish_fn=None) -> dict:
    """N1.5/5 — külső horgony hook. A csatorna env-kapu mögött ALSZIK
    (DELPHOI_ANCHOR_CHANNEL=off|git|agora, default off) — a flip a Kommandant
    szava. Mindkét mód KÉSZ (P5):
      'git':   a lánc-fejet a repo ledger_anchors.txt-jébe fűzi (commit kézi/CI);
      'agora': heti lánc-pecsét Agora-poszt a nowcaster-agenttől
               (DELPHOI_ANCHOR_AGORA_KEY operátor-kulccsal; futó event-loopban
               háttér-taskként megy el, hogy a cron-utat ne blokkolja).
    publish_fn: tesztekhez injektálható async publisher (default: Echolot-kliens)."""
    channel = os.environ.get("DELPHOI_ANCHOR_CHANNEL", "off").lower()
    if channel == "off":
        return {"channel": "off", "anchored": False}
    conn = get_db()
    try:
        head = _last_chain_hash(conn)
        try:
            checked = conn.execute(
                "SELECT COUNT(*) AS n FROM delphoi_nowcast_ledger").fetchone()["n"]
        except Exception:  # noqa: BLE001
            checked = 0
    finally:
        conn.close()
    if head == GENESIS:
        return {"channel": channel, "anchored": False, "reason": "üres lánc"}
    now_iso = datetime.now(timezone.utc).isoformat()
    stamp = f"{now_iso} {head}\n"
    if channel == "git":
        root = repo_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "ledger_anchors.txt")
        with open(path, "a", encoding="utf-8") as f:
            f.write(stamp)
        return {"channel": "git", "anchored": True, "head": head, "path": path}
    if channel == "agora":
        if publish_fn is None and not os.environ.get("DELPHOI_ANCHOR_AGORA_KEY", ""):
            return {"channel": "agora", "anchored": False,
                    "reason": "nincs operátor-kulcs (DELPHOI_ANCHOR_AGORA_KEY)"}
        title, body = _agora_anchor_post_text(head, checked, now_iso)
        pub = publish_fn or _agora_anchor_publish
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # Cron-kontextus (futó loop): fire-and-forget háttér-task — a
            # nowcast-futást a hálózati út nem blokkolhatja; hiba → hangos log.
            async def _bg():
                try:
                    res = await pub(title, body)
                    if not (isinstance(res, dict) and res.get("ok")):
                        logger.error("delphoi agora-anchor publish FAILED: %s", res)
                    else:
                        logger.info("delphoi agora-anchor posztolva: head=%s post=%s",
                                    head[:12], res.get("post_id"))
                except Exception:  # noqa: BLE001
                    logger.exception("delphoi agora-anchor publish crashed")
            loop.create_task(_bg())
            return {"channel": "agora", "anchored": True, "head": head,
                    "mode": "scheduled"}
        # Loop nélkül (script/teszt): szinkron várjuk meg az eredményt.
        try:
            res = asyncio.run(pub(title, body))
        except Exception as e:  # noqa: BLE001
            return {"channel": "agora", "anchored": False,
                    "reason": f"publish-hiba: {type(e).__name__}: {e}"}
        if isinstance(res, dict) and res.get("ok"):
            return {"channel": "agora", "anchored": True, "head": head,
                    "mode": "posted", "post_id": res.get("post_id")}
        return {"channel": "agora", "anchored": False,
                "reason": f"publish-hiba: {res.get('error') if isinstance(res, dict) else res}"}
    return {"channel": channel, "anchored": False, "reason": "ismeretlen csatorna"}


# ---------------------------------------------------------------------------
# P2 — SSR-EMBEDDING NAPI TOKEN-SAPKA (G0d). A k-mintázás az embedding-
# hívásszámot k-szorozza: számoljuk, logoljuk, és a napi sapka felett
# HANGOSAN elhasalunk (RuntimeError) — néma vágás nincs. A hívó ága viszi a
# hibát: nowcastnál az entitás-futás, FOGÁS-nál failed + auto-refund.
# ---------------------------------------------------------------------------
def estimate_embed_tokens(texts) -> int:
    """Durva token-becslés: karakter/3.2 (a G1 költség-log konvenciója)."""
    return sum(max(1, int(len(str(t)) / 3.2)) for t in texts)


def charge_embed_budget(get_db, n_tokens: int, label: str = "") -> dict:
    """Atomi napi számláló-terhelés. Sapka felett a terhelés NEM íródik és
    RuntimeError repül (hangos hiba, nem néma vágás)."""
    day = datetime.now(timezone.utc).date().isoformat()
    budget = embed_budget()
    conn = get_db()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS delphoi_embed_usage "
            "(day TEXT PRIMARY KEY, tokens INTEGER NOT NULL DEFAULT 0)")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT tokens FROM delphoi_embed_usage WHERE day=?", (day,)).fetchone()
        used = int(row["tokens"]) if row else 0
        if used + int(n_tokens) > budget:
            conn.rollback()
            raise RuntimeError(
                f"DELPHOI_EMBED_BUDGET túllépés: ma {used} + kért {int(n_tokens)} > "
                f"sapka {budget} token ({label or 'embed'}) — a futás megáll, nem vágunk némán.")
        conn.execute(
            "INSERT INTO delphoi_embed_usage (day, tokens) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET tokens = tokens + ?",
            (day, int(n_tokens), int(n_tokens)))
        conn.commit()
    finally:
        conn.close()
    total = used + int(n_tokens)
    logger.info("delphoi embed-budget: +%d token (%s) → ma %d / %d",
                int(n_tokens), label or "embed", total, budget)
    return {"day": day, "charged": int(n_tokens), "today_total": total, "budget": budget}


# ---------------------------------------------------------------------------
# KORPUSZ-ÉPÍTÉS — kiegyensúlyozott, datált ország-ablak a press_snapshots-ból.
# ---------------------------------------------------------------------------
def build_country_corpus(get_db, country: str, window_days: int = 7,
                         max_topics: int = 10) -> dict:
    """Az ország nyelvének utolsó `window_days` napi brief+trending+news sorai →
    kompakt, ÁLTALÁNOS hír-kontextus (nem entitás-szűrt!) + determinista
    corpus_hash. A `news` (Echolot hírfolyam) a legszélesebb merítés — sok
    forrásból, per-nyelv szűrve. Visszaad: {context, corpus_hash, window_start,
    window_end, snapshot_ids, days}.

    G3 ORSZÁG-TUDATOSSÁG (UK/US): az 'en' nyelvi korpusz GLOBÁLIS — a konfig
    `source_prefixes` kulcsa (pl. ('uk_',)) esetén CSAK a 'news' signal megy be,
    cikkei az Echolot forrás-prefixre szűrve (uk_*/us_*); a brief/trending
    globál-EN jel, ország-attribúció nélkül KIMARAD. Vállalt kompromisszum: a
    napi en-snapshot 40 globális cikkéből a uk_/us_ részhalmaz kicsi — a
    lefedettség vékonyabb, a coverage-őr ezt jelzi."""
    cfg = COUNTRY_PANEL_CONFIG.get(country)
    lang = cfg["lang"] if cfg else country.lower()
    prefixes = tuple((cfg or {}).get("source_prefixes") or ())
    signals = "('news')" if prefixes else "('brief','trending','news')"
    conn = get_db()
    try:
        # *3: naponta 3 releváns signal (brief/trending/news) fér az ablakba.
        rows = conn.execute(
            "SELECT id, date_iso, signal_type, content FROM press_snapshots "
            f"WHERE lang=? AND signal_type IN {signals} "
            "ORDER BY date_iso DESC LIMIT ?", (lang, window_days * 3)).fetchall()
    finally:
        conn.close()
    if not rows:
        return {"context": "", "corpus_hash": "", "snapshot_ids": [], "days": 0,
                "window_start": "", "window_end": ""}
    dates = sorted({r["date_iso"] for r in rows})
    window_start, window_end = dates[0], dates[-1]
    parts, news_heads, seen_titles = [], [], set()
    for r in sorted(rows, key=lambda x: (x["date_iso"], x["signal_type"]), reverse=True):
        try:
            content = json.loads(r["content"])
        except Exception:  # noqa: BLE001
            continue
        if r["signal_type"] == "brief":
            if content.get("lead") and len(parts) < 3:
                parts.append("VILÁG: " + content["lead"])
            for t in (content.get("topics") or [])[:max_topics // 2]:
                title = (t.get("title") or "").strip()
                if title and title.lower()[:40] not in seen_titles:
                    seen_titles.add(title.lower()[:40])
                    parts.append(f"- {title}: {(t.get('summary') or '')[:140]}")
            for t in (content.get("local_topics") or [])[:max_topics // 2]:
                title = (t.get("title") or "").strip()
                if title and title.lower()[:40] not in seen_titles:
                    seen_titles.add(title.lower()[:40])
                    parts.append(f"- {title}: {(t.get('summary') or '')[:140]}")
        elif r["signal_type"] == "trending":
            kws = [t.get("keyword") for t in (content.get("trending") or [])[:8] if t.get("keyword")]
            if kws and not any(p.startswith("Felkapott") for p in parts):
                parts.append("Felkapott témák: " + ", ".join(kws))
        elif r["signal_type"] == "news":
            # A hírfolyam fejléc-szintű, de a legszélesebb merítés (sok forrás).
            for a in (content.get("articles") or []):
                if prefixes:
                    sid = str(a.get("source_id") or a.get("source") or "")
                    if not sid.startswith(prefixes):
                        continue
                title = (a.get("title") or "").strip()
                k = title.lower()[:48]
                if title and k not in seen_titles and len(news_heads) < 18:
                    seen_titles.add(k)
                    news_heads.append(title)
    base = parts[: max_topics + 4]
    if news_heads:
        base.append("Friss hírfolyam-címek: " + " · ".join(news_heads))
    snapshot_ids = [r["id"] for r in rows]
    return {
        "context": "\n".join(base),
        "corpus_hash": compute_corpus_hash(snapshot_ids, window_start, window_end, country),
        "snapshot_ids": snapshot_ids, "days": len(dates),
        "window_start": window_start, "window_end": window_end,
    }


# ---------------------------------------------------------------------------
# NOWCAST-FUTÁS (N3) — ugyanaz a motor (persona_sampler + Flash fan-out + SSR),
# entitás-bemenettel. chat_fn/embed_fn injektálható (teszt: fake-LLM).
# ---------------------------------------------------------------------------
def _anchor_kind(entity_key: str, entity_type: str) -> str:
    if entity_type == "sentiment_expectation":
        if "inflaci" in entity_key:
            return "price"
        return "growth"
    return "regard"


def _anchor_set(kind: str, lang: str):
    from plugins import ssr
    if kind == "regard":
        return REFERENCE_SETS_REGARD.get(lang) or REFERENCE_SETS_REGARD["hu"]
    if kind == "price":
        by_country = {c.lower(): v for c, v in ssr.REFERENCE_SETS_PRICE.items()}
        return by_country.get(lang) or REFERENCE_SETS_PRICE_EXTRA.get(lang) or by_country["hu"]
    return REFERENCE_SETS_GROWTH.get(lang) or REFERENCE_SETS_GROWTH["hu"]


def _stimulus_name(display_label: str) -> str:
    """A UI-címke (magyar, pl. 'Donald Tusk megítélése') → nyelvfüggetlen
    stimulus-név a persona-prompthoz ('Donald Tusk'). A magyar címke-toldalék
    nem kerülhet lengyel/francia/olasz kérdésbe."""
    name = display_label
    for suffix in (" megítélése", " megitelese"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()


def _nowcast_prompt(persona: dict, cfg: dict, entity_label: str, kind: str, ctx: str) -> str:
    lang = cfg["lang"]
    question = NOWCAST_QUESTIONS.get((lang, kind)) or NOWCAST_QUESTIONS[("hu", kind)]
    profile = ", ".join(f"{k}: {v}" for k, v in persona.items() if k not in ("id",))
    return (
        f"[{profile}]\n\n{cfg.get('priming', '')}\n\n"
        f"A friss hírkörnyezet:\n{ctx or '(nincs friss hír)'}\n\n"
        f"{question.format(entity=_stimulus_name(entity_label))}"
    )


def _build_dims(cfg: dict) -> dict:
    dims = dict(cfg["dims"])
    dims["media"] = [(label, w) for label, w, _bucket in cfg["media"]]
    return dims


def _media_bucket_map(cfg: dict) -> dict:
    return {label: bucket for label, _w, bucket in cfg["media"]}


async def _default_embed_fn(texts, deps):
    """Embedding API — SF bge-m3 (többnyelvű, egy fiók); OPENAI_API_KEY esetén a
    backtesztek nyertese (text-embedding-3-small) — env-kapu, nem kód-döntés."""
    import httpx
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        url, key, model = "https://api.openai.com/v1/embeddings", openai_key, EMBED_MODEL_OPENAI
    else:
        base = deps.get("siliconflow_base_url", "https://api.siliconflow.com/v1")
        url, key, model = f"{base}/embeddings", deps.get("siliconflow_api_key", ""), EMBED_MODEL_SF
    async with httpx.AsyncClient(timeout=deps.get("siliconflow_timeout", 60)) as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {key}"},
                                 json={"model": model, "input": list(texts)})
        resp.raise_for_status()
        data = resp.json()["data"]
    return [d["embedding"] for d in sorted(data, key=lambda x: x.get("index", 0))]


def _iso_target_window(predicted: datetime | None = None) -> str:
    """A jel a KÖVETKEZŐ ISO-hétre szól (pl. '2026-W29')."""
    d = (predicted or datetime.now(timezone.utc)) + timedelta(days=7)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


async def run_entity_nowcast(deps: dict, entity_key: str = "", country: str = "",
                             n: int = 60, seed: int = 42, window_days: int = 7,
                             dry_run: bool = False, chat_fn=None, embed_fn=None,
                             samples: int | None = None) -> dict:
    """A heti nowcast: enabled regiszter-sorok (szűrhető) → ország-korpusz →
    kvótás panel → Hy3 fan-out (personánként k minta, P2 variancia-őr) →
    SSR-linear → direction ∈ [-1,1] → ÚJ ledger-sor (INSERT — más út a
    triggerek miatt nincs is). samples=None → DELPHOI_NOWCAST_SAMPLES env."""
    from plugins import persona_sampler, pollster, ssr

    get_db = deps["get_db"]
    conn = get_db()
    try:
        sql = "SELECT * FROM delphoi_entity_nowcast WHERE enabled=1"
        params: list = []
        if entity_key:
            sql += " AND entity_key=?"; params.append(entity_key)
        if country:
            sql += " AND country=?"; params.append(country)
        entities = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    if not entities:
        return {"ok": False, "error": "nincs engedélyezett entitás a szűrésre", "results": []}

    results = []
    for ent in entities:
        cfg = COUNTRY_PANEL_CONFIG.get(ent["country"])
        if not cfg:
            results.append({"entity_key": ent["entity_key"], "ok": False,
                            "error": f"nincs COUNTRY_PANEL_CONFIG: {ent['country']} (Akna 2 — lean-konfig kötelező)"})
            continue
        corpus = build_country_corpus(get_db, ent["country"], window_days=window_days)
        if not corpus["context"]:
            results.append({"entity_key": ent["entity_key"], "ok": False,
                            "error": f"üres korpusz (lang={cfg['lang']}) — a nowcast korpusz nélkül nem fut"})
            continue

        personas, kl = persona_sampler.sample_personas(_build_dims(cfg), n=n, seed=seed)
        kind = _anchor_kind(ent["entity_key"], ent["entity_type"])
        bucket_of = _media_bucket_map(cfg)

        # Hy3 fan-out (P2 variancia-őr): personánként k minta (G1: minta-
        # átlagolás kötelező), panel-hőmérséklet env-kapun; megosztott httpx
        # client + semaphore + exp. backoff; chat_fn injektálható (teszt).
        k = max(1, int(samples if samples is not None else nowcast_samples()))
        temp = panel_temperature()
        by_persona: dict = {p["id"]: (p, []) for p in personas}
        if chat_fn is not None:
            for p in personas:
                for _j in range(k):
                    by_persona[p["id"]][1].append(
                        await chat_fn(_nowcast_prompt(p, cfg, ent["display_label"], kind, corpus["context"])))
        else:
            import httpx
            sem = asyncio.Semaphore(CONCURRENCY)
            async with httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {pollster._provider()[1]}"}, timeout=90) as client:
                async def _one(p, j):
                    async with sem:
                        try:
                            return (p["id"], await pollster._chat(
                                client, _nowcast_prompt(p, cfg, ent["display_label"], kind, corpus["context"]),
                                temperature=temp, model=NOWCAST_MODEL))
                        except Exception as e:  # noqa: BLE001
                            logger.warning("delphoi nowcast persona %s/minta %d failed: %s", p["id"], j, e)
                            return None
                got = await asyncio.gather(*[_one(p, j) for p in personas for j in range(k)])
                for g in got:
                    if g:
                        by_persona[g[0]][1].append(g[1])
        # persona akkor él, ha legalább 1 mintája van — az élők minta-ÁTLAGGAL
        reactions = [(p, sample_texts) for p, sample_texts in by_persona.values() if sample_texts]

        if not reactions:
            results.append({"entity_key": ent["entity_key"], "ok": False, "error": "üres panel (minden hívás elhalt)"})
            continue

        # SSR — MINDEN minta beágyazva; a hívásszám k-szorozódik → számoljuk,
        # logoljuk és a napi sapka őrzi (G0d; túllépés = hangos hiba).
        texts, slices = [], []
        for _p, sample_texts in reactions:
            slices.append((len(texts), len(texts) + len(sample_texts)))
            texts.extend(sample_texts)
        anchors = _anchor_set(kind, cfg["lang"])
        embed_info = charge_embed_budget(
            get_db, estimate_embed_tokens(texts + list(anchors)),
            label=f"nowcast:{ent['entity_key']}")
        _embed = embed_fn or (lambda ts: _default_embed_fn(ts, deps))
        emb_resp = await _embed(texts)
        emb_anch = await _embed(list(anchors))
        import numpy as np
        pmf = ssr.compute_pmf(np.asarray(emb_resp, dtype=float),
                              np.asarray(emb_anch, dtype=float), method="linear")
        scores = ssr.score_pmf(pmf)
        persona_scores = np.array([float(scores[a:b].mean()) for a, b in slices])
        survey_score = float(persona_scores.mean())
        direction = round((survey_score - 3.0) / 2.0, 4)   # [-1, +1] — RELATÍV jel

        # válasz-szórás arány (P2 riport-metrika): personán BELÜLI (minta-zaj)
        # vs personák KÖZÖTTI szórás — a k-átlagolás értelmét ez mutatja meg.
        within = [float(scores[a:b].std()) for a, b in slices if b - a >= 2]
        within_sd = round(float(np.mean(within)), 4) if within else None
        between_sd = round(float(persona_scores.std()), 4) if len(persona_scores) >= 2 else None
        dispersion_ratio = (round(within_sd / between_sd, 3)
                            if within_sd is not None and between_sd else None)

        seg: dict = {}
        for (p, _t), s in zip(reactions, persona_scores):
            b = bucket_of.get(p.get("media", ""), "egyéb")
            seg.setdefault(b, []).append(float(s))
        segment_json = json.dumps(
            {b: {"n": len(v), "mean_score": round(sum(v) / len(v), 3),
                 "direction": round((sum(v) / len(v) - 3.0) / 2.0, 4)} for b, v in seg.items()},
            ensure_ascii=False)

        emb_model = EMBED_MODEL_OPENAI if os.environ.get("OPENAI_API_KEY") else EMBED_MODEL_SF
        model_id = f"{NOWCAST_MODEL}|non-think|temp={temp}|k={k}|ssr=linear|emb={emb_model}"
        entry = {
            "entity_key": ent["entity_key"], "country": ent["country"], "ok": True,
            "display_label": ent["display_label"], "n": len(reactions),
            "direction": direction, "survey_score": round(survey_score, 3),
            "kl": {kk: round(v, 4) for kk, v in kl.items()},
            "corpus_hash": corpus["corpus_hash"], "corpus_days": corpus["days"],
            "sampling": {"k": k, "temperature": temp, "n_calls": len(texts),
                         "within_persona_sd": within_sd,
                         "between_persona_sd": between_sd,
                         "dispersion_ratio": dispersion_ratio},
            "embed_budget": embed_info,
        }

        # P2 — coverage + konfidencia + kalibráció (NYERS és KALIBRÁLT érték,
        # verzió-címkével). Best-effort: hibájuk SOSEM töri a nowcastot.
        try:
            from plugins import delphoi_scopegate as _sg
            cov = _sg.coverage_score(get_db, ent["country"], window_days=window_days, corpus=corpus)
            entry["coverage"] = cov
            entry["confidence"] = _sg.confidence(None, cov)
        except Exception:  # noqa: BLE001
            logger.exception("delphoi nowcast coverage failed (non-fatal)")
        try:
            from plugins import delphoi_calibration as _calib
            domain = "cci" if ent["entity_type"] == "sentiment_expectation" else "regard"
            raw_balance = round((survey_score - 3.0) / 2.0 * 100.0, 3)  # panel-szaldó skála
            calibrated, cal_meta = _calib.calibrate(
                raw_balance, ent["country"], domain, NOWCAST_CAL_PANEL_VERSION, NOWCAST_MODEL)
            entry["calibration"] = {"raw": raw_balance, "calibrated": calibrated, **cal_meta}
        except Exception:  # noqa: BLE001
            logger.exception("delphoi nowcast calibration failed (non-fatal)")

        # HALLUCINÁCIÓ-ŐR (P1) — olcsó heurisztika az éles kimeneten, LLM-hívás
        # nélkül; best-effort: az őr hibája SOSEM töri a nowcastot.
        try:
            from plugins import delphoi_halluguard as _hg
            grounding_extra = "\n".join(
                [cfg.get("priming", ""), ent["display_label"], _stimulus_name(ent["display_label"])])
            hg_report = _hg.scan_reactions(texts, corpus["context"], grounding_extra)
            _hg.log_flags(get_db, "nowcast", ent["entity_key"], ent["country"],
                          corpus["corpus_hash"], hg_report)
            entry["halluguard"] = {"n_texts": hg_report["n_texts"],
                                   "n_suspect_texts": hg_report["n_suspect_texts"],
                                   "n_suspects": len(hg_report["suspects"])}
        except Exception:  # noqa: BLE001
            logger.exception("delphoi halluguard failed (non-fatal)")
        if not dry_run:
            row = append_ledger_row(
                get_db, ent["entity_key"], ent["country"], _iso_target_window(),
                direction, corpus["corpus_hash"], model_id, segment_json)
            entry.update({"predicted_at": row["predicted_at"],
                          "direction_prev": row["direction_prev"],
                          "content_hash": row["content_hash"]})
        results.append(entry)

    out = {"ok": True, "ran": len(results), "dry_run": dry_run, "results": results}
    if not dry_run:
        out["anchor"] = anchor_hash(get_db)
    return out


def nowcast_status(get_db, entity_key: str = "", limit: int = 12) -> dict:
    """A regiszter + a legfrissebb napló-sorok (entitásonként), a láncfej.
    A ledger-sorok korszakhatár-jelölést kapnak (model_regime_boundary, P1)."""
    conn = get_db()
    try:
        reg = [dict(r) for r in conn.execute(
            "SELECT entity_key, country, entity_type, display_label, enabled "
            "FROM delphoi_entity_nowcast ORDER BY country, entity_key").fetchall()]
        sql = ("SELECT id, entity_key, country, predicted_at, target_window, direction, "
               "direction_prev, content_hash, model_id FROM delphoi_nowcast_ledger ")
        params: list = []
        if entity_key:
            sql += "WHERE entity_key=? "; params.append(entity_key)
        sql += "ORDER BY id DESC LIMIT ?"; params.append(limit)
        try:
            ledger = _annotate_regime(conn, [dict(r) for r in conn.execute(sql, params).fetchall()])
        except Exception:  # noqa: BLE001 — migráció előtti DB: legacy oszlopkészlet
            legacy_sql = sql.replace(", model_id", "").replace("SELECT id, ", "SELECT ")
            ledger = [dict(r) for r in conn.execute(legacy_sql, params).fetchall()]
        head = _last_chain_hash(conn)
    finally:
        conn.close()
    return {"registry": reg, "latest_ledger": ledger, "chain_head": head}


# ---------------------------------------------------------------------------
# Cron belépési pont — a server _cron_loop special-case hívja (heti).
# ---------------------------------------------------------------------------
async def cron_entry(recipe_name: str, deps: dict | None = None) -> None:
    d = deps or _DEPS
    if not d:
        logger.error("delphoi cron_entry: nincs deps — skip")
        return
    try:
        if recipe_name == "delphoi_watchdog":
            n = watchdog_sweep(d["get_db"])
            if n:
                logger.warning("delphoi watchdog: %d ragadt job → failed+refund", n)
            return
        rep = await run_entity_nowcast(d)
        oks = sum(1 for r in rep.get("results", []) if r.get("ok"))
        logger.info("delphoi nowcast cron kész: %d/%d entitás, anchor=%s",
                    oks, rep.get("ran", 0), rep.get("anchor", {}).get("channel"))
    except Exception:  # noqa: BLE001
        logger.exception("delphoi cron_entry (%s) failed", recipe_name)


# ---------------------------------------------------------------------------
# Plugin-regisztráció
# ---------------------------------------------------------------------------
def register_tools(app, deps):
    global _DEPS
    _DEPS = deps
    get_db = deps["get_db"]

    # A discover_and_register nem teszi sys.modules-ba — a server-oldali
    # `from plugins.delphoi import cron_entry` így ugyanezt a modult kapja.
    mod = sys.modules.get(__name__)
    if mod is not None:
        sys.modules.setdefault("plugins.delphoi", mod)

    conn = get_db()
    try:
        ensure_tables(conn)
        ensure_fg_tables(conn)
        seed_registry(conn)
        # Cron-receptek (idempotens seed) — a nowcast HETI (hétfő 07:30, nem
        # napi: token-költség + lean-konfig gondosság, N3/6); a watchdog
        # óránkénti háló a ragadt fókuszcsoport-jobokra (refund-vasszabály).
        ts = datetime.now(timezone.utc).isoformat()
        for name, desc, cron in (
            ("delphoi_nowcast_weekly",
             "DELPHOI heti entitás-nowcast — hash-láncolt ledger-sor entitásonként", "30 7 * * 1"),
            ("delphoi_watchdog",
             "DELPHOI watchdog — ragadt fókuszcsoport-jobok failed+refund", "5 * * * *"),
        ):
            exists = conn.execute("SELECT 1 FROM pyramid_recipes WHERE name=?", (name,)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO pyramid_recipes (name, description, required_tools, prompt_template, "
                    "created_by, created_at, updated_at, cron_schedule, cron_model, cron_enabled, cron_delivery) "
                    "VALUES (?, ?, '[]', ?, 'system', ?, ?, ?, 'deepseek', 1, 'none')",
                    (name, desc, "(special-cased — runtime: plugins.delphoi.cron_entry)", ts, ts, cron),
                )
                logger.info("delphoi recipe seed: %s (cron=%s)", name, cron)
        conn.commit()
    finally:
        conn.close()

    @app.tool()
    async def delphoi_entity_nowcast_run(entity_key: str = "", country: str = "",
                                         n: int = 60, dry_run: bool = False) -> str:
        """DELPHOI entitás-nowcast futtatása (KIRAKAT). A szintetikus panel az ország
        datált hír-korpuszán ítéli meg az entitás irányát (RELATÍV jel, nem abszolút %).
        Üres szűrők = minden engedélyezett entitás. dry_run=True: számol, de nem ír ledgerbe."""
        rep = await run_entity_nowcast(deps, entity_key=entity_key, country=country,
                                       n=n, dry_run=dry_run)
        return json.dumps(rep, ensure_ascii=False, indent=1)

    @app.tool()
    async def delphoi_nowcast_status(entity_key: str = "", limit: int = 12) -> str:
        """DELPHOI nowcast-státusz: entitás-regiszter + legfrissebb ledger-sorok + lánc-fej."""
        return json.dumps(nowcast_status(get_db, entity_key=entity_key, limit=limit),
                          ensure_ascii=False, indent=1)

    @app.tool()
    async def delphoi_verify_ledger() -> str:
        """A DELPHOI predikció-napló hash-láncának integritás-ellenőrzése (audit-eszköz).
        Bármely sor utólagos módosítása az összes későbbi sor hash-ét érvényteleníti."""
        return json.dumps(verify_ledger_chain(get_db), ensure_ascii=False)

    @app.tool()
    async def delphoi_run_focus_group(user_id: str, input_kind: str, input_text: str = "",
                                      input_variants: str = "", country: str = "HU",
                                      n_per_cell: int = 30, n_seeds: int = 1) -> str:
        """DELPHOI szintetikus fókuszcsoport (FIZETŐS, privát siló): job-felvétel +
        azonnali feldolgozás. input_kind: product_desc|pitch|yt_title|ab_test|concept.
        input_variants: JSON-lista (ab_test/yt_title). Kredit-levonás atomi;
        hiba esetén automatikus refund. RELATÍV jelet ad, nem abszolút %-ot."""
        variants = json.loads(input_variants) if input_variants else None
        spec = {"country": country, "n_per_cell": n_per_cell, "n_seeds": n_seeds}
        created = create_job(get_db, user_id, input_kind, input_text, spec, variants)
        if not created.get("ok"):
            return json.dumps(created, ensure_ascii=False)
        rep = await process_job(deps, created["job_id"])
        return json.dumps({**created, "processing": rep.get("ok"),
                           "status": "done" if rep.get("ok") else "failed"},
                          ensure_ascii=False)

    @app.tool()
    async def delphoi_get_credits(user_id: str) -> str:
        """DELPHOI kredit-egyenleg + ledger-kivonat (signup-grant idempotens)."""
        ensure_welcome(get_db, user_id)
        return json.dumps(get_credits(get_db, user_id), ensure_ascii=False)

    @app.tool()
    async def delphoi_job_status(job_id: str, user_id: str) -> str:
        """DELPHOI job-állapot (csak a tulajdonosnak). done → aggregált eredmény;
        nyers persona-mondat SOHA nem megy ki."""
        return json.dumps(get_job(get_db, job_id, user_id), ensure_ascii=False)

    logger.info("delphoi plugin regisztrálva (nowcast: %d seed-entitás, fg-országok: %s)",
                len(_SEED_ENTITIES), ",".join(FG_COUNTRIES))


# ═══════════════════════════════════════════════════════════════════════════
# A FOGÁS (§3, D1–D3) — fizetős szintetikus fókuszcsoport, PRIVÁT SILÓ.
#
# HIGIÉNIA-VASSZABÁLY: a delphoi_jobs / delphoi_panel_responses SOHA nem
# indexelődik FTS-be, SOHA nem jelenik meg feed/Agora/korpusz-lekérdezésben.
# A user inputja CSAK a saját jobja panel-promptjába kerül futásidőben; a
# panel groundingja a NYILVÁNOS hír-korpuszból (press_snapshots) jön.
# A user felé CSAK az aggregátum megy — nyers persona-mondat sosem.
# ═══════════════════════════════════════════════════════════════════════════

_FG_INIT_SQL = """
CREATE TABLE IF NOT EXISTS delphoi_jobs (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'queued',
    input_kind     TEXT NOT NULL,
    input_text     TEXT NOT NULL,
    input_variants TEXT,
    vision_ref     TEXT,
    panel_spec     TEXT NOT NULL,
    credits_cost   INTEGER NOT NULL,
    result_json    TEXT,
    error          TEXT,
    created_at     TEXT NOT NULL,
    started_at     TEXT,
    completed_at   TEXT,
    deleted_at     TEXT,
    model_id       TEXT,
    panel_version  TEXT,
    scope_verdict  TEXT,
    coverage_score REAL
);
CREATE INDEX IF NOT EXISTS idx_delphoi_jobs_user ON delphoi_jobs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_delphoi_jobs_status ON delphoi_jobs(status);

CREATE TABLE IF NOT EXISTS delphoi_panel_responses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL REFERENCES delphoi_jobs(id),
    persona_idx  INTEGER NOT NULL,
    segment      TEXT NOT NULL,
    raw_reaction TEXT NOT NULL,
    ssr_score    REAL,
    variant_id   TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delphoi_resp_job ON delphoi_panel_responses(job_id);

CREATE TABLE IF NOT EXISTS delphoi_credits (
    user_id    TEXT PRIMARY KEY,
    balance    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
-- UNIQUE(user_id, reason): a refund/signup-grant idempotenciája DB-szinten —
-- egy jobhoz LEGFELJEBB egy refund-sor, egy userhez egy signup_grant.
CREATE TABLE IF NOT EXISTS delphoi_credit_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    delta      INTEGER NOT NULL,
    reason     TEXT NOT NULL,
    job_id     TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, reason)
);
CREATE INDEX IF NOT EXISTS idx_delphoi_ledger_user ON delphoi_credit_ledger(user_id);
"""

WELCOME_CREDITS = int(os.environ.get("DELPHOI_WELCOME_CREDITS", "2"))
CALLS_PER_CREDIT = int(os.environ.get("DELPHOI_CALLS_PER_CREDIT", "30"))
FG_RETRIES = 3               # retry-küszöb a refund ELŐTT (v2 refund-vasszabály)
FG_MIN_COMPLETION = 0.9      # részeredmény nem termék: e alatt failed+refund
WATCHDOG_MINUTES = 30        # ragadt 'running' job → failed + refund

VALID_INPUT_KINDS = ("product_desc", "pitch", "yt_title", "ab_test", "concept")


def ensure_fg_tables(conn) -> None:
    conn.executescript(_FG_INIT_SQL)
    conn.commit()


# ── Kredit-könyvelés (atomi, ledger-alapú — az orakel_credits bevált mintája) ──

def ensure_welcome(get_db, user_id: str) -> None:
    """Idempotens signup-grant — a ledger UNIQUE(user_id,'signup_grant') a garancia."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT OR IGNORE INTO delphoi_credit_ledger (user_id, delta, reason, created_at) "
            "VALUES (?, ?, 'signup_grant', ?)",
            (str(user_id), WELCOME_CREDITS, datetime.now(timezone.utc).isoformat()))
        if cur.rowcount:
            conn.execute(
                "INSERT INTO delphoi_credits (user_id, balance, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?, updated_at = ?",
                (str(user_id), WELCOME_CREDITS, datetime.now(timezone.utc).isoformat(),
                 WELCOME_CREDITS, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()


def get_credits(get_db, user_id: str) -> dict:
    conn = get_db()
    try:
        row = conn.execute("SELECT balance FROM delphoi_credits WHERE user_id=?",
                           (str(user_id),)).fetchone()
        ledger = [dict(r) for r in conn.execute(
            "SELECT delta, reason, job_id, created_at FROM delphoi_credit_ledger "
            "WHERE user_id=? ORDER BY id DESC LIMIT 20", (str(user_id),)).fetchall()]
    finally:
        conn.close()
    return {"user_id": str(user_id), "balance": row["balance"] if row else 0, "ledger": ledger}


def charge(get_db, user_id: str, job_id: str, cost: int) -> bool:
    """Atomi levonás: ha nincs fedezet → False, SEMMI nem íródik."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE delphoi_credits SET balance = balance - ?, updated_at = ? "
            "WHERE user_id = ? AND balance >= ?",
            (int(cost), datetime.now(timezone.utc).isoformat(), str(user_id), int(cost)))
        if not cur.rowcount:
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO delphoi_credit_ledger (user_id, delta, reason, job_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(user_id), -int(cost), f"job:{job_id}", job_id,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return True
    finally:
        conn.close()


def refund(get_db, user_id: str, job_id: str, cost: int) -> bool:
    """REFUND-VASSZABÁLY (v2): automatikus, atomi, IDEMPOTENS — a
    UNIQUE(user_id, reason='refund:<job>') kizárja a dupla-refundot."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT OR IGNORE INTO delphoi_credit_ledger (user_id, delta, reason, job_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(user_id), int(cost), f"refund:{job_id}", job_id,
             datetime.now(timezone.utc).isoformat()))
        if not cur.rowcount:
            conn.rollback()
            return False
        conn.execute(
            "UPDATE delphoi_credits SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
            (int(cost), datetime.now(timezone.utc).isoformat(), str(user_id)))
        conn.commit()
        return True
    finally:
        conn.close()


def add_credits(get_db, user_id: str, amount: int, note: str) -> dict:
    """Top-up / admin-jóváírás. A note a dedup-kulcs része (reason egyedi)."""
    import uuid
    reason = f"topup:{note or uuid.uuid4().hex[:10]}"
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT OR IGNORE INTO delphoi_credit_ledger (user_id, delta, reason, created_at) "
            "VALUES (?, ?, ?, ?)",
            (str(user_id), int(amount), reason, datetime.now(timezone.utc).isoformat()))
        if not cur.rowcount:
            conn.rollback()
            return {"ok": False, "error": "duplicate_topup"}
        conn.execute(
            "INSERT INTO delphoi_credits (user_id, balance, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?, updated_at = ?",
            (str(user_id), int(amount), datetime.now(timezone.utc).isoformat(),
             int(amount), datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return {"ok": True, "reason": reason}
    finally:
        conn.close()


def job_cost(panel_spec: dict, input_variants=None) -> int:
    """ÉLŐ ár-visszacsatolás alapja (D5.1): hívásszám → kredit.
    calls = n_per_cell × szegmensszám × n_seeds × variánsszám."""
    import math
    n = max(30, int(panel_spec.get("n_per_cell", 30)))   # KEMÉNY ALSÓ KORLÁT
    segs = panel_spec.get("segments") or []
    n_segments = max(1, len(segs)) if isinstance(segs, list) else 1
    n_seeds = 3 if int(panel_spec.get("n_seeds", 1)) >= 3 else 1
    n_variants = max(1, len(input_variants or []))
    calls = n * n_segments * n_seeds * n_variants
    return max(1, math.ceil(calls / CALLS_PER_CREDIT))


# ── D3: instrumentum-készlet — anchorok + kérdés-sablonok ──────────────────
# Vonzerő-skála (1=egyáltalán nem vonzó … 5=nagyon vonzó) a validált
# purchase-intent vonal lokalizált megfelelője (Colgate-terep).
REFERENCE_SETS_APPEAL = {
    "hu": [
        "Ez egyáltalán nem érdekel, biztosan nem választanám.",
        "Nem igazán vonzó számomra.",
        "Semleges vagyok, lehet is, nem is.",
        "Eléggé vonzó, valószínűleg kipróbálnám.",
        "Nagyon vonzó, ezt biztosan választanám.",
    ],
    "cs": [
        "To mě vůbec nezajímá, určitě bych to nezvolil.",
        "Není to pro mě moc lákavé.",
        "Jsem neutrální, možná ano, možná ne.",
        "Je to docela lákavé, nejspíš bych to vyzkoušel.",
        "Je to velmi lákavé, určitě bych to zvolil.",
    ],
    "pt": [
        "Isto não me interessa nada, de certeza que não escolheria.",
        "Não é muito atraente para mim.",
        "Estou neutro, talvez sim, talvez não.",
        "É bastante atraente, provavelmente experimentaria.",
        "É muito atraente, de certeza que escolheria.",
    ],
    "pl": [
        "To mnie w ogóle nie interesuje, na pewno bym tego nie wybrał.",
        "Nie jest to dla mnie zbyt atrakcyjne.",
        "Jestem neutralny, może tak, może nie.",
        "Jest to dość atrakcyjne, pewnie bym spróbował.",
        "Jest to bardzo atrakcyjne, na pewno bym to wybrał.",
    ],
    # G3 (2026-07-21): fr/de/en — natív survey-regiszter, kézzel írt (static-
    # copy-no-MT elv). Az en szövege a delphoi_brief DIMENSION_BANK appeal/en
    # készletével AZONOS (ott futásidőben innen oldódik fel — EGY forrás).
    "fr": [
        "Cela ne m'intéresse pas du tout, je ne le choisirais certainement pas.",
        "Ce n'est pas vraiment attirant pour moi.",
        "Je suis partagé, peut-être oui, peut-être non.",
        "C'est assez attirant, j'essaierais probablement.",
        "C'est très attirant, je le choisirais certainement.",
    ],
    "de": [
        "Das interessiert mich überhaupt nicht, ich würde es sicher nicht wählen.",
        "Es ist für mich nicht besonders reizvoll.",
        "Ich bin unentschieden, vielleicht ja, vielleicht nein.",
        "Es ist ziemlich reizvoll, ich würde es wahrscheinlich ausprobieren.",
        "Es ist sehr reizvoll, ich würde es auf jeden Fall wählen.",
    ],
    "en": [
        "This does not appeal to me at all, I would definitely not choose it.",
        "It is not really attractive to me.",
        "I am neutral about it, maybe yes, maybe no.",
        "It is quite attractive, I would probably give it a try.",
        "It is very attractive, I would definitely choose it.",
    ],
}

# Kérdés-sablonok (lang, input_kind). A persona EGY szabad mondatot ír
# (SSR-input), plusz strukturált sorokat, ahol a metrika kéri.
FG_QUESTIONS = {
    ("hu", "product_desc"): (
        "Az alábbi termékleírást látod:\n„{stimulus}”\n\n"
        "Őszintén, a saját szemszögedből: mennyire vonzó ez neked? "
        "Válaszolj EGY őszinte mondattal."),
    ("hu", "concept"): (
        "Az alábbi koncepciót látod:\n„{stimulus}”\n\n"
        "Őszintén: mennyire tetszik ez neked? Válaszolj EGY őszinte mondattal."),
    ("hu", "pitch"): (
        "Az alábbi bemutatkozó szöveget (pitch) hallod:\n„{stimulus}”\n\n"
        "Válaszolj PONTOSAN így:\nREAKCIÓ: <egy őszinte mondat arról, mennyire győzött meg>\n"
        "HOMÁLYOS: <egy szó/kifejezés, ami nem volt világos, vagy '-'>"),
    ("hu", "ab_test"): (
        "Az alábbi szöveget látod:\n„{stimulus}”\n\n"
        "Válaszolj PONTOSAN így:\nREAKCIÓ: <egy őszinte mondat>\n"
        "VÁLASZTÁS: <igen, ha rákattintanál/választanád; nem, ha nem>"),
    ("hu", "yt_title"): (
        "Az alábbi videócímek közül EGYETLEN videót nézhetsz meg:\n{stimulus}\n\n"
        "Válaszolj PONTOSAN így:\nVÁLASZTÁS: <a választott cím sorszáma>\n"
        "INDOK: <egy rövid mondat>"),
    ("cs", "product_desc"): (
        "Vidíš tento popis produktu:\n„{stimulus}”\n\n"
        "Upřímně, z tvého pohledu: jak je to pro tebe lákavé? Odpověz JEDNOU upřímnou větou."),
    ("cs", "concept"): (
        "Vidíš tento koncept:\n„{stimulus}”\n\nUpřímně: jak se ti líbí? Odpověz JEDNOU větou."),
    ("cs", "pitch"): (
        "Slyšíš tento pitch:\n„{stimulus}”\n\nOdpověz PŘESNĚ takto:\n"
        "REAKCE: <jedna upřímná věta>\nNEJASNÉ: <slovo, které nebylo jasné, nebo '-'>"),
    ("cs", "ab_test"): (
        "Vidíš tento text:\n„{stimulus}”\n\nOdpověz PŘESNĚ takto:\n"
        "REAKCE: <jedna upřímná věta>\nVOLBA: <ano/ne>"),
    ("cs", "yt_title"): (
        "Z těchto názvů videí si můžeš pustit JEDINÉ video:\n{stimulus}\n\n"
        "Odpověz PŘESNĚ takto:\nVOLBA: <číslo vybraného názvu>\nDŮVOD: <krátká věta>"),
    ("pt", "product_desc"): (
        "Vês esta descrição de produto:\n„{stimulus}”\n\n"
        "Honestamente, do teu ponto de vista: quão atraente é para ti? Responde com UMA frase honesta."),
    ("pt", "concept"): (
        "Vês este conceito:\n„{stimulus}”\n\nHonestamente: quanto gostas? Responde com UMA frase."),
    ("pt", "pitch"): (
        "Ouves este pitch:\n„{stimulus}”\n\nResponde EXATAMENTE assim:\n"
        "REAÇÃO: <uma frase honesta>\nCONFUSO: <uma palavra que não ficou clara, ou '-'>"),
    ("pt", "ab_test"): (
        "Vês este texto:\n„{stimulus}”\n\nResponde EXATAMENTE assim:\n"
        "REAÇÃO: <uma frase honesta>\nESCOLHA: <sim/não>"),
    ("pt", "yt_title"): (
        "Destes títulos de vídeo podes ver UM ÚNICO vídeo:\n{stimulus}\n\n"
        "Responde EXATAMENTE assim:\nESCOLHA: <número do título>\nMOTIVO: <frase curta>"),
    ("pl", "product_desc"): (
        "Widzisz ten opis produktu:\n„{stimulus}”\n\n"
        "Szczerze, z twojej perspektywy: jak bardzo cię to pociąga? Odpowiedz JEDNYM szczerym zdaniem."),
    ("pl", "concept"): (
        "Widzisz ten koncept:\n„{stimulus}”\n\nSzczerze: jak bardzo ci się podoba? Odpowiedz JEDNYM zdaniem."),
    ("pl", "pitch"): (
        "Słyszysz ten pitch:\n„{stimulus}”\n\nOdpowiedz DOKŁADNIE tak:\n"
        "REAKCJA: <jedno szczere zdanie>\nNIEJASNE: <słowo, które nie było jasne, albo '-'>"),
    ("pl", "ab_test"): (
        "Widzisz ten tekst:\n„{stimulus}”\n\nOdpowiedz DOKŁADNIE tak:\n"
        "REAKCJA: <jedno szczere zdanie>\nWYBÓR: <tak/nie>"),
    ("pl", "yt_title"): (
        "Z tych tytułów wideo możesz obejrzeć TYLKO JEDNO wideo:\n{stimulus}\n\n"
        "Odpowiedz DOKŁADNIE tak:\nWYBÓR: <numer wybranego tytułu>\nPOWÓD: <krótkie zdanie>"),
    # G3 (2026-07-21): fr/de/en készletek — FR/DE/UK/US FG-regisztrációhoz.
    ("fr", "product_desc"): (
        "Tu vois cette description de produit :\n« {stimulus} »\n\n"
        "Honnêtement, de ton point de vue : à quel point cela t'attire-t-il ? "
        "Réponds par UNE seule phrase honnête."),
    ("fr", "concept"): (
        "Tu vois ce concept :\n« {stimulus} »\n\n"
        "Honnêtement : à quel point cela te plaît-il ? Réponds par UNE seule phrase."),
    ("fr", "pitch"): (
        "Tu entends ce pitch :\n« {stimulus} »\n\nRéponds EXACTEMENT ainsi :\n"
        "RÉACTION : <une phrase honnête>\nFLOU : <un mot resté flou, ou '-'>"),
    ("fr", "ab_test"): (
        "Tu vois ce texte :\n« {stimulus} »\n\nRéponds EXACTEMENT ainsi :\n"
        "RÉACTION : <une phrase honnête>\nCHOIX : <oui/non>"),
    ("fr", "yt_title"): (
        "Parmi ces titres de vidéos, tu ne peux regarder qu'UNE SEULE vidéo :\n{stimulus}\n\n"
        "Réponds EXACTEMENT ainsi :\nCHOIX : <numéro du titre choisi>\nRAISON : <une phrase courte>"),
    ("de", "product_desc"): (
        "Du siehst diese Produktbeschreibung:\n„{stimulus}“\n\n"
        "Ehrlich, aus deiner Sicht: wie ansprechend ist das für dich? "
        "Antworte mit EINEM ehrlichen Satz."),
    ("de", "concept"): (
        "Du siehst dieses Konzept:\n„{stimulus}“\n\n"
        "Ehrlich: wie gut gefällt es dir? Antworte mit EINEM Satz."),
    ("de", "pitch"): (
        "Du hörst diesen Pitch:\n„{stimulus}“\n\nAntworte GENAU so:\n"
        "REAKTION: <ein ehrlicher Satz>\nUNKLAR: <ein Wort, das unklar blieb, oder '-'>"),
    ("de", "ab_test"): (
        "Du siehst diesen Text:\n„{stimulus}“\n\nAntworte GENAU so:\n"
        "REAKTION: <ein ehrlicher Satz>\nWAHL: <ja/nein>"),
    ("de", "yt_title"): (
        "Von diesen Videotiteln kannst du dir nur EIN EINZIGES Video ansehen:\n{stimulus}\n\n"
        "Antworte GENAU so:\nWAHL: <Nummer des gewählten Titels>\nGRUND: <ein kurzer Satz>"),
    ("en", "product_desc"): (
        "You see this product description:\n“{stimulus}”\n\n"
        "Honestly, from your own point of view: how appealing is this to you? "
        "Answer with ONE honest sentence."),
    ("en", "concept"): (
        "You see this concept:\n“{stimulus}”\n\n"
        "Honestly: how much do you like it? Answer with ONE honest sentence."),
    ("en", "pitch"): (
        "You hear this pitch:\n“{stimulus}”\n\nAnswer EXACTLY like this:\n"
        "REACTION: <one honest sentence>\nUNCLEAR: <a word or phrase that was not clear, or '-'>"),
    ("en", "ab_test"): (
        "You see this text:\n“{stimulus}”\n\nAnswer EXACTLY like this:\n"
        "REACTION: <one honest sentence>\nCHOICE: <yes if you would click/choose it; no if not>"),
    ("en", "yt_title"): (
        "Out of these video titles you can watch ONLY ONE video:\n{stimulus}\n\n"
        "Answer EXACTLY like this:\nCHOICE: <number of the chosen title>\nREASON: <one short sentence>"),
}

# yt_title niche-illesztett néző-mix (memory: 50% casual / 35% téma / 15% rajongó)
YT_VIEWER_MIX = [("alkalmi néző", 0.50), ("a témát követő néző", 0.35), ("elkötelezett rajongó", 0.15)]

_CHOICE_YES = ("igen", "ano", "sim", "tak", "yes", "oui", "ja")


def _parse_structured(text: str, key_variants: tuple) -> str | None:
    import re
    for k in key_variants:
        m = re.search(rf"{k}\s*:\s*([^\n]+)", text or "", re.I)
        if m:
            return m.group(1).strip()
    return None


def _fg_prompt(persona: dict, cfg: dict, kind: str, stimulus: str) -> str:
    lang = cfg["lang"]
    template = FG_QUESTIONS.get((lang, kind)) or FG_QUESTIONS[("hu", kind)]
    profile = ", ".join(f"{k}: {v}" for k, v in persona.items() if k != "id")
    return f"[{profile}]\n\n{template.format(stimulus=stimulus)}"


def _fg_iteration(seed_idx: int, sample_idx: int, persona_id: int) -> int:
    """Cache-kulcs iteráció (P2): a (seed, minta, persona) hármas ütközés-
    mentesen — a k minta KÜLÖN cache-cellát kap (különben a cache a
    variancia-őrt ölné meg: k-szor ugyanaz a válasz jönne vissza)."""
    return (seed_idx * 100 + sample_idx) * 100000 + persona_id


def create_job(get_db, user_id: str, input_kind: str, input_text: str,
               panel_spec: dict, input_variants=None) -> dict:
    """Job-felvétel: validálás → ensure_welcome → ATOMI kredit-levonás →
    queued sor. Elégtelen kredit → 402-jellegű hiba, SEMMI nem íródik.
    # A2: user_id+kulcs a kanonikus authz; az Echolot-session csak a proxy
    # fordítási rétege — a user_id ma az Echolot-proxy 'user:<id>' stringje,
    # az átkötés célpontja a delphoi_users (origin+external_id) feloldás."""
    import uuid
    if input_kind not in VALID_INPUT_KINDS:
        return {"ok": False, "error": f"ismeretlen input_kind: {input_kind}"}
    if not (input_text or "").strip() and not input_variants:
        return {"ok": False, "error": "üres input"}
    country = str(panel_spec.get("country", "HU")).upper()
    if country not in FG_COUNTRIES:
        return {"ok": False, "error": f"nem validált ország: {country} (elérhető: {','.join(FG_COUNTRIES)})"}
    if country not in COUNTRY_PANEL_CONFIG:
        return {"ok": False, "error": f"nincs panel-konfig: {country}"}
    if int(panel_spec.get("n_per_cell", 30)) < 30:
        # KEMÉNY ALSÓ KORLÁT (D5.1): kis minta zajt adna el mérésként.
        panel_spec = dict(panel_spec, n_per_cell=30)
    if input_kind in ("ab_test", "yt_title") and (not input_variants or len(input_variants) < 2):
        return {"ok": False, "error": f"{input_kind}: legalább 2 variáns kell"}
    cost = job_cost(panel_spec, input_variants)
    job_id = "dlph-" + uuid.uuid4().hex[:10]
    ensure_welcome(get_db, user_id)
    if not charge(get_db, user_id, job_id, cost):
        bal = get_credits(get_db, user_id)["balance"]
        return {"ok": False, "error": "insufficient_credits", "cost": cost, "balance": bal}
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO delphoi_jobs (id, user_id, status, input_kind, input_text, "
            "input_variants, panel_spec, credits_cost, created_at) "
            "VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)",
            (job_id, str(user_id), input_kind, input_text,
             json.dumps(input_variants, ensure_ascii=False) if input_variants else None,
             json.dumps(panel_spec, ensure_ascii=False), cost,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "job_id": job_id, "cost": cost,
            "balance": get_credits(get_db, user_id)["balance"]}


def _fail_job(get_db, job_id: str, error: str) -> None:
    """failed + AUTOMATIKUS refund (v2 vasszabály) — kézi beavatkozás nélkül."""
    conn = get_db()
    try:
        row = conn.execute("SELECT user_id, credits_cost, status FROM delphoi_jobs WHERE id=?",
                           (job_id,)).fetchone()
        if not row or row["status"] in ("done", "failed"):
            return
        conn.execute(
            "UPDATE delphoi_jobs SET status='failed', error=?, completed_at=? WHERE id=?",
            (error[:500], datetime.now(timezone.utc).isoformat(), job_id))
        conn.commit()
    finally:
        conn.close()
    refund(get_db, row["user_id"], job_id, row["credits_cost"])
    logger.warning("delphoi job %s failed (%s) — kredit visszaírva", job_id, error[:120])


def watchdog_sweep(get_db) -> int:
    """Ragadt 'running' jobok (worker-halál) → failed + refund. Minden
    API-hívás és a cron is futtatja — olcsó, idempotens."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=WATCHDOG_MINUTES)).isoformat()
    conn = get_db()
    try:
        stuck = [r["id"] for r in conn.execute(
            "SELECT id FROM delphoi_jobs WHERE status='running' AND started_at < ?",
            (cutoff,)).fetchall()]
    finally:
        conn.close()
    for jid in stuck:
        _fail_job(get_db, jid, f"watchdog: {WATCHDOG_MINUTES} perce ragadt running-ban")
    return len(stuck)


def _aggregate(kind: str, rows: list, variants=None) -> dict:
    """RELATÍV/ordinális aggregátum + KÖTELEZŐ baseline (doktrína #3, #6).
    rows: [{segment, ssr_score, variant_id, choice, unclear}]."""
    from collections import Counter, defaultdict
    out: dict = {"kind": kind, "n": len(rows)}
    if kind in ("product_desc", "concept", "pitch"):
        seg = defaultdict(list)
        for r in rows:
            if r.get("ssr_score") is not None:
                seg[r["segment"]].append(r["ssr_score"])
        all_scores = [s for v in seg.values() for s in v]
        out["overall_score"] = round(sum(all_scores) / len(all_scores), 3) if all_scores else None
        out["baseline"] = {"type": "skála-középpont", "value": 3.0,
                           "note": "a jel a 3.0 semleges ponthoz mérve értelmezendő"}
        out["segments"] = sorted(
            [{"segment": k, "n": len(v), "score": round(sum(v) / len(v), 3),
              "vs_baseline": round(sum(v) / len(v) - 3.0, 3)} for k, v in seg.items()],
            key=lambda x: x["score"], reverse=True)
        if kind == "pitch":
            unclear = Counter(r["unclear"].lower() for r in rows
                              if r.get("unclear") and r["unclear"] != "-")
            out["unclear_top"] = [{"kifejezes": k, "n": n} for k, n in unclear.most_common(5)]
            out["clear_ratio"] = round(
                sum(1 for r in rows if not r.get("unclear") or r["unclear"] == "-") / max(1, len(rows)), 3)
    elif kind == "ab_test":
        # versengő döntés → PLURALITY az elsődleges (memory: YT-címteszt lecke),
        # SSR másodlagos. Between-subject: minden persona EGY variánst látott.
        per_v = defaultdict(lambda: {"n": 0, "yes": 0, "scores": []})
        for r in rows:
            v = per_v[r.get("variant_id") or "?"]
            v["n"] += 1
            v["yes"] += 1 if r.get("choice") else 0
            if r.get("ssr_score") is not None:
                v["scores"].append(r["ssr_score"])
        k = max(1, len(per_v))
        out["baseline"] = {"type": "véletlen választás", "value": round(1 / 2, 3),
                           "note": "az igen-arány az 50% zajszinthez mérve értelmezendő"}
        out["variants"] = sorted(
            [{"variant": vid, "n": d["n"],
              "choice_rate": round(d["yes"] / max(1, d["n"]), 3),
              "ssr_mean": round(sum(d["scores"]) / len(d["scores"]), 3) if d["scores"] else None}
             for vid, d in per_v.items()],
            key=lambda x: x["choice_rate"], reverse=True)
    elif kind == "yt_title":
        # egy-a-sokból kattintás → PLURALITY; a persona az EGÉSZ listát látta.
        picks = Counter(r.get("choice") for r in rows if r.get("choice"))
        total = sum(picks.values()) or 1
        k = max(1, len(variants or []))
        out["baseline"] = {"type": "véletlen választás", "value": round(1 / k, 3),
                           "note": f"{k} cím közül a véletlen szint {round(100 / k, 1)}%"}
        out["ranking"] = [
            {"variant": v, "share": round(n / total, 3), "n": n,
             "vs_baseline": round(n / total - 1 / k, 3)}
            for v, n in picks.most_common()]
    return out


async def process_job(deps: dict, job_id: str, chat_fn=None, embed_fn=None,
                      scope_chat_fn=None) -> dict:
    """A fókuszcsoport-futás (D2): SCOPE-GATE + coverage (P2) → grounding →
    kvótás panel → Hy3 fan-out (personánként k minta, retry-vel, cache BE) →
    SSR/plurality → aggregátum → done | failed+refund.
    RÉSZEREDMÉNY NEM TERMÉK: a kitöltési arány FG_MIN_COMPLETION alatt refund.
    scope_chat_fn: az ítész-hívás injektálható (teszt); ha chat_fn injektált
    (teszt-mód) és scope_chat_fn nincs, az ítész kimarad — a heurisztika dönt."""
    from plugins import persona_sampler, pollster, ssr

    get_db = deps["get_db"]
    conn = get_db()
    try:
        job = conn.execute("SELECT * FROM delphoi_jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["status"] not in ("queued", "running"):
            return {"ok": False, "error": "nincs ilyen queued job"}
        conn.execute("UPDATE delphoi_jobs SET status='running', started_at=? WHERE id=?",
                     (datetime.now(timezone.utc).isoformat(), job_id))
        conn.commit()
    finally:
        conn.close()

    try:
        spec = json.loads(job["panel_spec"])
        kind = job["input_kind"]
        country = str(spec.get("country", "HU")).upper()
        cfg = COUNTRY_PANEL_CONFIG[country]
        variants = json.loads(job["input_variants"]) if job["input_variants"] else None
        n = max(30, int(spec.get("n_per_cell", 30)))
        seed = int(spec.get("seed", 42))
        n_seeds = 3 if int(spec.get("n_seeds", 1)) >= 3 else 1

        # Grounding: NYILVÁNOS datált korpusz (a privát input SOSEM kerül más
        # jobok kontextusába — csak ennek a jobnak a promptjába, futásidőben).
        corpus = build_country_corpus(get_db, country)
        ctx_line = f"\n\nA friss hírkörnyezet (háttér): {corpus['context'][:600]}" if corpus["context"] else ""

        # ── P2 SCOPE-GATE + COVERAGE a job-felvételkor ──────────────────────
        # PIROS NEM TILT: kötelező figyelmeztetés + konfidencia-levonás. Az
        # oszlopok már a futás ELEJÉN íródnak (bukott job is hordozza a
        # verdiktet); a kapu hibája SOSEM töri a jobot.
        scope = None
        cov = None
        try:
            from plugins import delphoi_scopegate as _sg
            stimulus = (job["input_text"] or "").strip() or " | ".join(str(v) for v in (variants or []))
            use_judge = (chat_fn is None) or (scope_chat_fn is not None)
            scope = await _sg.scope_verdict(stimulus, chat_fn=scope_chat_fn, use_judge=use_judge)
            cov = _sg.coverage_score(get_db, country, corpus=corpus)
            conn = get_db()
            try:
                conn.execute(
                    "UPDATE delphoi_jobs SET scope_verdict=?, coverage_score=? WHERE id=?",
                    (scope["verdict"], cov["score"], job_id))
                conn.commit()
            finally:
                conn.close()
            if scope.get("warning"):
                logger.warning("delphoi job %s scope=%s: %s", job_id, scope["verdict"], scope["warning"])
            if cov.get("warning"):
                logger.warning("delphoi job %s coverage=%.3f: %s", job_id, cov["score"], cov["warning"])
        except Exception:  # noqa: BLE001
            logger.exception("delphoi scope-gate failed (non-fatal)")

        dims = _build_dims(cfg)
        if kind == "yt_title":
            dims = dict(dims, nezotipus=[(l, w) for l, w in YT_VIEWER_MIX])
        bucket_of = _media_bucket_map(cfg)

        # feladat-lista: (persona, variant_id, stimulus, iteration)
        tasks = []
        for s_i in range(n_seeds):
            personas, _kl = persona_sampler.sample_personas(dims, n=n, seed=seed + s_i * 1000)
            if kind == "ab_test" and variants:
                for i, p in enumerate(personas):   # between-subject: fele-fele
                    vid = f"V{(i % len(variants)) + 1}"
                    tasks.append((p, vid, variants[i % len(variants)], s_i))
            elif kind == "yt_title" and variants:
                listing = "\n".join(f"{i+1}. {v}" for i, v in enumerate(variants))
                for p in personas:
                    tasks.append((p, None, listing, s_i))
            else:
                for p in personas:
                    tasks.append((p, None, job["input_text"], s_i))

        # P2 variancia-őr: personánként k minta, panel-hőmérséklet env-kapun.
        k = fg_samples()
        temp = panel_temperature()

        cache = None
        if os.environ.get("DELPHOI_FG_CACHE", "1") == "1":
            from plugins.llm_cache import LLMCache, cache_key
            cache = LLMCache()

        async def _ask(client, p, stimulus, iteration):
            prompt = _fg_prompt(p, cfg, kind, stimulus) + ctx_line
            if cache is not None:
                from plugins.llm_cache import cache_key as _ck
                key = _ck(FG_MODEL, {"temperature": temp}, "", prompt, iteration=iteration)
                hit = cache.get(key)
                if hit is not None:
                    return hit
            if chat_fn is not None:
                text = await chat_fn(prompt)
            else:
                text = await pollster._chat(client, prompt, temperature=temp, model=FG_MODEL)
            if cache is not None and text:
                cache.set(key, text, model=FG_MODEL)
            return text

        results = []   # (persona, variant_id, [minta-szöveg × k])
        if chat_fn is not None:
            for p, vid, stim, s_i in tasks:
                sample_texts = []
                for j in range(k):
                    try:
                        sample_texts.append(await _ask(None, p, stim, _fg_iteration(s_i, j, p["id"])))
                    except Exception:  # noqa: BLE001
                        sample_texts.append(None)
                results.append((p, vid, sample_texts))
        else:
            import httpx
            sem = asyncio.Semaphore(CONCURRENCY)
            async with httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {pollster._provider()[1]}"}, timeout=90) as client:
                async def _one(t_idx, p, stim, s_i, j):
                    async with sem:
                        try:
                            # a pollster._chat már FG_RETRIES-nél többet (4) retry-zik
                            # exponenciális backoffal — a retry-küszöb ott érvényesül
                            return (t_idx, await _ask(client, p, stim, _fg_iteration(s_i, j, p["id"])))
                        except Exception as e:  # noqa: BLE001
                            logger.warning("delphoi fg persona %s/minta %d halott: %s", p["id"], j, e)
                            return (t_idx, None)
                flat = await asyncio.gather(*[
                    _one(i, p, stim, s_i, j)
                    for i, (p, _vid, stim, s_i) in enumerate(tasks) for j in range(k)])
                buf: dict = {i: [] for i in range(len(tasks))}
                for t_idx, text in flat:
                    buf[t_idx].append(text)
                results = [(tasks[i][0], tasks[i][1], buf[i]) for i in range(len(tasks))]

        # RÉSZEREDMÉNY NEM TERMÉK — personára is: egy persona mérése akkor
        # teljes, ha MIND a k mintája él (fél-mintás persona nem átlagolható
        # torzítatlanul); a kitöltés a teljes personák aránya.
        ok_results = [(p, v, ts_) for p, v, ts_ in results
                      if len(ts_) == k and all(t for t in ts_)]
        if len(ok_results) < FG_MIN_COMPLETION * len(tasks):
            _fail_job(get_db, job_id,
                      f"fan-out kitöltés {len(ok_results)}/{len(tasks)} teljes persona "
                      f"< {FG_MIN_COMPLETION:.0%} — részeredmény nem termék")
            return {"ok": False, "error": "incomplete_panel", "refunded": True}

        # kiértékelés instrumentumonként — task-szintű sorok (persona-aggregátum,
        # az _aggregate bemenete) + minta-szintű nyers sorok a privát silóba.
        from collections import Counter as _Counter
        rows = []          # task-szint: ssr_score = k minta átlaga; choice = többség
        sample_rows = []   # minta-szint: nyers reakciók (delphoi_panel_responses)
        ssr_texts, ssr_refs = [], []   # ssr_refs: (task_idx, sample_row_idx)
        _CHOICE_KEYS = ("VÁLASZTÁS", "VOLBA", "ESCOLHA", "WYBÓR", "WYBOR", "CHOICE",
                        "CHOIX", "WAHL")
        _REACT_KEYS = ("REAKCIÓ", "REAKCE", "REAÇÃO", "REACAO", "REAKCJA",
                       "RÉACTION", "REACTION", "REAKTION")
        for i, (p, vid, sample_texts) in enumerate(ok_results):
            seg_label = bucket_of.get(p.get("media", ""), "egyéb")
            row = {"persona_idx": i, "segment": seg_label, "variant_id": vid,
                   "ssr_score": None, "choice": None, "unclear": None}
            choices, unclears = [], []
            for text in sample_texts:
                sample_rows.append({"persona_idx": i, "segment": seg_label,
                                    "raw": text, "variant_id": vid, "ssr_score": None})
                if kind == "yt_title":
                    pick = _parse_structured(text, _CHOICE_KEYS)
                    if pick:
                        import re as _re
                        m = _re.search(r"\d+", pick)
                        if m and variants and 1 <= int(m.group(0)) <= len(variants):
                            choices.append(variants[int(m.group(0)) - 1])
                elif kind == "ab_test":
                    ch = _parse_structured(text, _CHOICE_KEYS)
                    choices.append(bool(ch and ch.lower().split()[0] in _CHOICE_YES))
                    ssr_texts.append(_parse_structured(text, _REACT_KEYS) or text)
                    ssr_refs.append((i, len(sample_rows) - 1))
                elif kind == "pitch":
                    unclears.append(_parse_structured(text, ("HOMÁLYOS", "NEJASNÉ", "NEJASNE", "CONFUSO",
                                                             "NIEJASNE", "FLOU", "UNKLAR", "UNCLEAR")))
                    ssr_texts.append(_parse_structured(text, _REACT_KEYS) or text)
                    ssr_refs.append((i, len(sample_rows) - 1))
                else:
                    ssr_texts.append(text)
                    ssr_refs.append((i, len(sample_rows) - 1))
            # minta-aggregálás personánként (P2): választás → plurality/többség,
            # homályos-kifejezés → leggyakoribb.
            if kind == "yt_title" and choices:
                row["choice"] = _Counter(choices).most_common(1)[0][0]
            elif kind == "ab_test" and choices:
                row["choice"] = sum(1 for c in choices if c) * 2 >= len(choices)
            if kind == "pitch":
                vals = [u for u in unclears if u is not None]
                if vals:
                    row["unclear"] = _Counter(vals).most_common(1)[0][0]
            rows.append(row)

        within_sd = between_sd = dispersion_ratio = None
        embed_info = None
        if ssr_texts:
            anchors = REFERENCE_SETS_APPEAL.get(cfg["lang"]) or REFERENCE_SETS_APPEAL["hu"]
            # embed-hívásszám k-szorozódik → napi sapka (G0d; túllépés = hangos
            # hiba → except-ág → failed + auto-refund, nem néma vágás)
            embed_info = charge_embed_budget(
                get_db, estimate_embed_tokens(ssr_texts + list(anchors)), label=f"fg:{job_id}")
            _embed = embed_fn or (lambda ts: _default_embed_fn(ts, deps))
            import numpy as np
            emb_resp = await _embed(ssr_texts)
            emb_anch = await _embed(list(anchors))
            pmf = ssr.compute_pmf(np.asarray(emb_resp, dtype=float),
                                  np.asarray(emb_anch, dtype=float), method="linear")
            scores = ssr.score_pmf(pmf)
            per_task: dict = {}
            for (t_idx, s_idx), sc in zip(ssr_refs, scores):
                per_task.setdefault(t_idx, []).append(float(sc))
                sample_rows[s_idx]["ssr_score"] = float(sc)
            for t_idx, vals in per_task.items():
                rows[t_idx]["ssr_score"] = float(np.mean(vals))
            # válasz-szórás arány (P2 riport-metrika): minta-zaj vs panel-jel
            within = [float(np.std(v)) for v in per_task.values() if len(v) >= 2]
            task_means = [float(np.mean(v)) for v in per_task.values()]
            within_sd = round(float(np.mean(within)), 4) if within else None
            between_sd = round(float(np.std(task_means)), 4) if len(task_means) >= 2 else None
            dispersion_ratio = (round(within_sd / between_sd, 3)
                                if within_sd is not None and between_sd else None)

        # nyers reakciók a PRIVÁT silóba (aggregálás előtti réteg) — MINDEN minta
        conn = get_db()
        try:
            ts = datetime.now(timezone.utc).isoformat()
            conn.executemany(
                "INSERT INTO delphoi_panel_responses (job_id, persona_idx, segment, "
                "raw_reaction, ssr_score, variant_id, created_at) VALUES (?,?,?,?,?,?,?)",
                [(job_id, r["persona_idx"], r["segment"], r["raw"], r["ssr_score"],
                  r["variant_id"], ts) for r in sample_rows])
            agg = _aggregate(kind, rows, variants)
            fg_model_id = f"{FG_MODEL}|non-think|temp={temp}|k={k}|ssr=linear"
            completion = round(len(ok_results) / max(1, len(tasks)), 4)
            agg["panel"] = {"country": country, "n_requested": len(tasks),
                            "n_completed": len(ok_results), "n_seeds": n_seeds,
                            "completion_ratio": completion,
                            "sampling": {"k": k, "temperature": temp,
                                         "n_calls": len(tasks) * k,
                                         "within_persona_sd": within_sd,
                                         "between_persona_sd": between_sd,
                                         "dispersion_ratio": dispersion_ratio},
                            "corpus_hash": corpus.get("corpus_hash", ""),
                            "model_id": fg_model_id,
                            "panel_version": FG_PANEL_VERSION}
            if embed_info:
                agg["panel"]["embed_budget"] = embed_info
            # P2 — scope-verdikt + coverage + konfidencia + kalibráció a riport-
            # payloadban is (a verdikt/coverage a job-oszlopokban már él).
            if scope is not None:
                agg["scope"] = scope
            if cov is not None:
                agg["coverage"] = cov
            try:
                from plugins import delphoi_scopegate as _sg
                agg["confidence"] = _sg.confidence(scope, cov)
            except Exception:  # noqa: BLE001
                logger.exception("delphoi confidence failed (non-fatal)")
            if agg.get("overall_score") is not None:
                try:
                    from plugins import delphoi_calibration as _calib
                    appeal_raw = round((float(agg["overall_score"]) - 1.0) / 4.0 * 100.0, 3)
                    calibrated, cal_meta = _calib.calibrate(
                        appeal_raw, country, "agora_appeal", FG_PANEL_VERSION, FG_MODEL)
                    agg["calibration"] = {"raw": appeal_raw, "scale": "0-100 appeal",
                                          "calibrated": calibrated, **cal_meta}
                except Exception:  # noqa: BLE001
                    logger.exception("delphoi fg calibration failed (non-fatal)")
            agg["disclaimer"] = ("Szintetikus panel relatív jelzése, nem abszolút mérés "
                                 "és nem közvélemény-kutatás.")
            conn.execute(
                "UPDATE delphoi_jobs SET status='done', result_json=?, completed_at=?, "
                "model_id=?, panel_version=? WHERE id=?",
                (json.dumps(agg, ensure_ascii=False), ts, fg_model_id,
                 FG_PANEL_VERSION, job_id))
            conn.commit()
        finally:
            conn.close()
        logger.info("delphoi job %s done (%d/%d válasz)", job_id, len(ok_results), len(tasks))
        return {"ok": True, "job_id": job_id, "result": agg}
    except Exception as e:  # noqa: BLE001 — BÁRMELY hiba: failed + auto-refund
        logger.exception("delphoi job %s crashed", job_id)
        _fail_job(get_db, job_id, f"{type(e).__name__}: {e}")
        return {"ok": False, "error": str(e), "refunded": True}


def get_job(get_db, job_id: str, user_id: str) -> dict:
    """CSAK a tulajdonosnak. done → aggregált eredmény; a nyers persona-sorok
    SOHA nem mennek ki (az aggregátum a termék)."""
    watchdog_sweep(get_db)
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM delphoi_jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    # A2: user_id+kulcs a kanonikus authz; az Echolot-session csak a proxy
    # fordítási rétege (a tulaj-egyezés ma nyers string-match a proxy-kulcson).
    if not row or row["user_id"] != str(user_id):
        return {"ok": False, "error": "not_found"}
    out = {"ok": True, "job_id": row["id"], "status": row["status"],
           "input_kind": row["input_kind"], "credits_cost": row["credits_cost"],
           "created_at": row["created_at"], "completed_at": row["completed_at"],
           "deleted_at": row["deleted_at"]}
    # P2: a scope-verdikt + coverage az API-payload ELSŐ szintjén is (nem csak
    # a result_json-ban) — bukott/futó job is hordozza. Legacy DB-n (oszlop
    # nélkül) a mező egyszerűen kimarad.
    for col in ("scope_verdict", "coverage_score"):
        if col in row.keys():
            out[col] = row[col]
    if row["status"] == "done" and row["result_json"]:
        out["result"] = json.loads(row["result_json"])
    if row["status"] == "failed":
        out["error_detail"] = row["error"]
        out["refunded"] = True   # a refund-vasszabály garantálja
    return out


def delete_job(get_db, job_id: str, user_id: str) -> dict:
    """GDPR-út (D4): a nyers input + nyers reakciók FIZIKAI felülírása,
    deleted_at kitöltve. Az aggregátum és a kredit-ledger MARAD (könyvelési
    integritás); a nowcast-ledger tábláit a törlés NEM ÉRINTI. Nem refund —
    a törlés adatvédelmi jog, nem visszatérítés."""
    conn = get_db()
    try:
        row = conn.execute("SELECT user_id, deleted_at FROM delphoi_jobs WHERE id=?",
                           (job_id,)).fetchone()
        # A2: user_id+kulcs a kanonikus authz; az Echolot-session csak a proxy
        # fordítási rétege.
        if not row or row["user_id"] != str(user_id):
            return {"ok": False, "error": "not_found"}
        if row["deleted_at"]:
            return {"ok": True, "already_deleted": True}
        ts = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE delphoi_jobs SET input_text='[deleted]', input_variants=NULL, "
            "vision_ref=NULL, deleted_at=? WHERE id=?", (ts, job_id))
        conn.execute(
            "UPDATE delphoi_panel_responses SET raw_reaction='[deleted]' WHERE job_id=?",
            (job_id,))
        conn.commit()
        return {"ok": True, "deleted_at": ts}
    finally:
        conn.close()


def list_jobs(get_db, user_id: str, limit: int = 20) -> list:
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, status, input_kind, credits_cost, created_at, completed_at, deleted_at "
            "FROM delphoi_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (str(user_id), limit)).fetchall()]
    finally:
        conn.close()
