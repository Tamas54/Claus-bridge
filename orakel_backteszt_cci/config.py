"""
ORAKEL-II BACKTESZT — KONFIGURÁCIÓ
===================================
A 2026-os OGY választás (ápr. 12.) ELŐTTI médiakörnyezet rögzítése.

FONTOS: a lean-címkék itt a KAMPÁNY-IDŐSZAKI állapotot tükrözik (jan–ápr. 1.),
NEM a mostani (választás utáni) Echolot-címkéket. A közmédia áprilisig Fidesz-oldali.
"""

# ── A 9 MEGHATÁROZÓ ONLINE LAP ──────────────────────────────────────────
# domain      : a lap web-domainje (a forrás-vezérelt kereséshez)
# lean        : KAMPÁNY-IDŐSZAKI politikai irány (gov | opposition | center)
# reach_weight: média-elérési súly (relatív; a mintavételi kvótát ez vezérli)
#               — durva becslés a 2026 eleji online közönség-arányokból,
#                 amit te (Kommandant) felülírhatsz pontosabb NMHH-adattal.
SOURCES = [
    # ── KORMÁNYOLDAL / JOBB ──
    {"name": "Origo",          "domain": "origo.hu",          "lean": "gov",        "reach_weight": 12},
    {"name": "Magyar Nemzet",  "domain": "magyarnemzet.hu",   "lean": "gov",        "reach_weight": 8},
    {"name": "Index",          "domain": "index.hu",          "lean": "gov",        "reach_weight": 14},
    {"name": "Mandiner",       "domain": "mandiner.hu",       "lean": "gov",        "reach_weight": 5},

    # ── ELLENZÉK / BAL / FÜGGETLEN ──
    {"name": "Telex",          "domain": "telex.hu",          "lean": "opposition", "reach_weight": 14},
    {"name": "444",            "domain": "444.hu",            "lean": "opposition", "reach_weight": 9},
    {"name": "HVG",            "domain": "hvg.hu",            "lean": "opposition", "reach_weight": 10},
    {"name": "24.hu",          "domain": "24.hu",             "lean": "opposition", "reach_weight": 8},
    {"name": "Népszava",       "domain": "nepszava.hu",       "lean": "opposition", "reach_weight": 4},
]

# ── KÖZMÉDIA / SOCIAL — STRUKTURÁLIS GROUNDING (nem cikk-korpusz) ────────
# Ezt a panel-prompt környezet-leírásába injektáljuk, NEM cikként.
STRUCTURAL_MEDIA = {
    "kozmedia_lean": "gov",       # KAMPÁNY ALATT: a közmédia (M1, Kossuth, hírado.hu) Fidesz-oldali volt
    "kozmedia_reach": 22,          # a közmédia nagy, de a választás ELŐTT kormányoldali
    "social_dominance": {
        "platform": "Facebook/YouTube/TikTok",
        "lean": "opposition",      # a Tisza/Magyar Péter erős social-fölénye
        "note": "Magyar Péter és a Tisza a kampány alatt jelentős organikus social-eléréssel bírt; "
                "a kormányoldal inkább a fizetett/közmédia-csatornákon volt erős."
    }
}

# ── IDŐABLAK ─────────────────────────────────────────────────────────────
WINDOW_START = "2026-01-01"
WINDOW_END   = "2026-04-01"   # SZIGORÚAN a választás (ápr. 12.) ELŐTT, kampányhajrá nélkül
# A hónapok progresszív, egyenletes lefedéshez:
MONTHS = ["2026-01", "2026-02", "2026-03"]

# ── KORPUSZ-MÉRET ────────────────────────────────────────────────────────
TARGET_CORPUS_SIZE = 500       # ~500 cím + lead

# ── POLL-SZŰRŐ (a tisztaság védőhálója) ──────────────────────────────────
# Bármely cikk, ami konkrét pártszámot / előrejelzést tartalmaz, KIZÁRVA.
# A panel nem láthatja a kész választ.
POLL_BLOCKLIST = [
    "közvélemény-kutatás", "kozvelemeny-kutatas", "közvéleménykutatás",
    "felmérés", "felmeres", "pártpreferencia", "partpreferencia",
    "Závecz", "Zavecz", "Medián", "Median", "Nézőpont", "Nezopont",
    "Republikon", "IDEA", "Publicus", "Závecz Research", "21 Kutatóközpont",
    "21 Kutatokozpont", "XXI. Század Intézet", "McLaughlin", "Real-PR",
    "Iránytű", "Iranytu", "Társadalomkutató", "Tarsadalomkutato",
    "százalékon áll", "szazalekon all", "vezet a", "ponttal vezet",
    "ponttal előnyben", "ponttal elonyben", "mandátumot szerezne",
    "mandatumot szerezne", "képviselői helyet", "kepviseloi helyet",
    "ha most lenne a választás", "ha most lenne a valasztas",
    "exit poll", "exitpoll",
]

# ── PANEL MOTOR — PROVIDER-VÁLTÓ (Kommandant döntése) ─────────────────────
# A pre-election backteszthez KÉT modell (lásd thoughts20260614 cutoff-vita):
#   - "siliconflow" / DeepSeek-V4-Flash: cutoff ~2025-01 → GENUINELY VAK a 2026-os
#     kampányra. EZ a tudományosan tiszta vak-jósló (elsődleges). Non-Think kötelező.
#   - "openai" / gpt-5.4-mini: cutoff ~2026-03 (kampány UTÁN) → kontaminált, de
#     stabil; ÖSSZEHASONLÍTÓ. Nem a vakság bizonyítéka.
# A providert env vezérli: ORAKEL_PROVIDER + ORAKEL_PANEL_MODEL felülír.
import os as _os

_PROVIDER = _os.environ.get("ORAKEL_PROVIDER", "siliconflow").lower()
_DEFAULT_MODEL = {
    "siliconflow": "deepseek-ai/DeepSeek-V4-Flash",
    "openai":      "gpt-5.4-mini",
    "anthropic":   "claude-sonnet-4-6",
}
PANEL_PROVIDER = _PROVIDER
PANEL_MODEL = _os.environ.get("ORAKEL_PANEL_MODEL", _DEFAULT_MODEL.get(_PROVIDER, "claude-sonnet-4-6"))
PANEL_N = int(_os.environ.get("ORAKEL_PANEL_N", "80"))          # personák száma (a #3 receptje)
PANEL_SEEDS = int(_os.environ.get("ORAKEL_PANEL_SEEDS", "2"))   # robusztusság-ellenőrzés
PANEL_CONCURRENCY = int(_os.environ.get("ORAKEL_CONCURRENCY", "6"))
