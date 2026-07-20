"""
ORAKEL-II BACKTESZT — CSEH GENERALIZÁCIÓS VALIDÁCIÓ — KONFIGURÁCIÓ
==================================================================
A 2025.10.03–04-i cseh képviselőházi választás ELŐTTI médiakörnyezet rögzítése.

CÉL: a magyar vak backteszt sikerét (+20,9, irány+magnitúdó eltalálva) egy MÁSODIK
országon reprodukálni. Ha a recept (vak modell + korpusz-grounding + média-grounding)
a cseh választáson is áll, akkor ÁTÜLTETHETŐ MÓDSZER, nem magyar-specifikus szerencse.

FONTOS: a lean-címkék a KAMPÁNY-IDŐSZAKI (2025 júl–szept) állapotot tükrözik. Ekkor
Fiala/Spolu volt KORMÁNYON, Babiš/ANO a kihívó ellenzék. A 'gov' lean = kormánykoalíció-
közeli/Babiš-kritikus; a 'populist' = ANO/SPD-közeli; a 'center' = független mainstream.
"""

# ── A 10 MEGHATÁROZÓ CSEH ONLINE LAP ─────────────────────────────────────
# domain      : a lap web-domainje (a forrás-vezérelt kereséshez)
# lean        : KAMPÁNY-IDŐSZAKI politikai irány (gov | populist | center)
#               gov      = kormánykoalíció (Spolu/STAN)-közeli / Babiš-kritikus minőségi sajtó
#               populist = ANO/SPD-közeli (incl. MAFRA-csoport reziduális Babiš-tilt)
#               center   = független mainstream, nagy elérésű portál
# reach_weight: relatív média-elérési súly (cseh online közönség-arányok durva becslése)
SOURCES = [
    # ── FÜGGETLEN MAINSTREAM / NAGY ELÉRÉS (center) ──
    {"name": "Seznam Zpravy",   "domain": "seznamzpravy.cz",          "lean": "center",   "reach_weight": 16},
    {"name": "Novinky",         "domain": "novinky.cz",               "lean": "center",   "reach_weight": 15},
    {"name": "CT24",            "domain": "ct24.ceskatelevize.cz",    "lean": "center",   "reach_weight": 8},
    {"name": "Hospodarske noviny","domain": "hn.cz",                  "lean": "center",   "reach_weight": 6},

    # ── ANO/SPD-KÖZELI / POPULISTA + MAFRA reziduális Babiš-tilt (populist) ──
    {"name": "iDNES",           "domain": "idnes.cz",                 "lean": "populist", "reach_weight": 13},
    {"name": "Parlamentni listy","domain": "parlamentnilisty.cz",     "lean": "populist", "reach_weight": 9},

    # ── KORMÁNYKOALÍCIÓ-KÖZELI / BABIŠ-KRITIKUS MINŐSÉGI SAJTÓ (gov) ──
    {"name": "Aktualne",        "domain": "aktualne.cz",              "lean": "gov",      "reach_weight": 8},
    {"name": "Denik N",         "domain": "denikn.cz",                "lean": "gov",      "reach_weight": 6},
    {"name": "Forum24",         "domain": "forum24.cz",               "lean": "gov",      "reach_weight": 4},
    {"name": "Echo24",          "domain": "echo24.cz",                "lean": "gov",      "reach_weight": 4},
]
# Lean-balansz (reach-súly szerint): center ~51%, populist ~25%, gov ~25%.
# A cseh online mainstream genuinely Babiš-kritikus-tiltú; a populista hang a SOCIAL-on
# (Babiš erős organikus elérése) él — pont ezt rögzíti a STRUCTURAL_MEDIA alább.

# ── KÖZMÉDIA / SOCIAL — STRUKTURÁLIS GROUNDING (nem cikk-korpusz) ─────────
# A magyar esetben ez a "közmédia=Fidesz áprilisig" FLIP volt. Csehországban MÁS a kép:
#   - a ČT (közszolgálati) viszonylag KIEGYENSÚLYOZOTT → NINCS éles flip;
#   - a fő tengely a Babiš-médiabefolyás (MAFRA, formálisan trösztben, hatás megmaradt)
#     + a Babiš/ANO erős organikus SOCIAL-elérése (FB/TikTok/YouTube) vs. a független,
#     kormánykritikus minőségi sajtó.
# Ezt a VÁLASZTÁS ELŐTTI valós csatorna-irányra groundoljuk, NEM a modell priorjára.
STRUCTURAL_MEDIA = {
    "public_tv_lean": "center",   # ČT/ČT24 kiegyensúlyozott — NINCS magyar-szerű kormány-flip
    "public_tv_reach": 10,
    "mafra_note": ("Az iDNES/Lidovky (MAFRA-csoport) korábban Babiš tulajdonában volt; "
                   "formálisan trösztben, de a reziduális ANO-közeli framing-hatás megmaradt."),
    "social_dominance": {
        "platform": "Facebook/TikTok/YouTube",
        "lean": "populist",       # Babiš/ANO erős organikus social-fölénye a kampányban
        "note": ("Andrej Babiš és az ANO a kampány alatt domináns organikus social-eléréssel "
                 "bírt (rövid videók, közvetlen mozgósítás); a kormánykoalíció (Spolu) inkább "
                 "a minőségi sajtóban és fizetett csatornákon volt erős. Az idősebb, vidéki, "
                 "gazdasági nehézségeket (infláció, megélhetés) érzékelő szavazók erősen "
                 "fogékonyak voltak az ANO üzeneteire.")
    }
}

# ── IDŐABLAK ─────────────────────────────────────────────────────────────
WINDOW_START = "2025-07-01"
WINDOW_END   = "2025-10-03"   # SZIGORÚAN a választás (okt. 3-4.) ELŐTT
MONTHS = ["2025-07", "2025-08", "2025-09"]

# ── KORPUSZ-MÉRET ────────────────────────────────────────────────────────
TARGET_CORPUS_SIZE = 320       # ~320 cím + lead (a magyar 245 elég volt)

# ── POLL-SZŰRŐ (a tisztaság védőhálója) ──────────────────────────────────
# Bármely cikk, ami konkrét pártszámot / előrejelzést / intézetet tartalmaz, KIZÁRVA.
# A panel NEM láthatja a kész választ. Cseh + nemzetközi kulcsszavak.
POLL_BLOCKLIST = [
    # intézetek
    "STEM", "Median", "Medián", "Kantar", "NMS", "Ipsos", "CVVM", "STEM/MARK",
    "Data Collect", "PAQ Research", "SANEP", "Behavio",
    # poll-terminológia (cs)
    "průzkum", "pruzkum", "volební model", "volebni model", "preference",
    "preferencí", "preferenci", "by volilo", "by volila", "získal by", "ziskal by",
    "získala by", "procent hlasů", "procent hlasu", "vede s", "vede o",
    "náskok", "naskok", "mandátů by", "mandatu by", "sněmovní volby model",
    "podle průzkumu", "podle pruzkumu", "v průzkumu", "v pruzkumu",
    "predikce", "odhad zisku", "exit poll", "povolební",
    # poll-terminológia (en/hu fallback ha a lead fordított)
    "opinion poll", "would win", "would get", "percent of the vote", "leads with",
    "közvélemény-kutatás", "felmérés",
]

# ── PÁRT-KÉSZLET (a többpárti emergencia — KÖTELEZŐ, lásd briefing 5/A) ───
# A magyar bináris kollapszus (Tisza/Fidesz) ott feature volt; a cseh rendszer
# többpárti, arányos, 5%-os küszöbbel. A panelnek ENGEDNIE kell a többpárti választást.
PARTIES = ["ANO", "SPOLU", "STAN", "Pirati", "SPD", "Motoriste", "Stacilo"]

# ── GROUND TRUTH (a vonalzó — ELŐRE rögzítve volby.cz/hivatalos alapján) ──
# 2025.10.03–04, képviselőház (200 fős), hivatalos listás eredmény.
# FORRÁS: volby.cz / Wikipedia 2025 Czech parliamentary election (lehúzva 2026-06-15).
GROUND_TRUTH = {
    "ANO":       {"pct": 34.52, "seats": 80, "in": True},
    "SPOLU":     {"pct": 23.36, "seats": 52, "in": True},
    "STAN":      {"pct": 11.23, "seats": 22, "in": True},
    "Pirati":    {"pct": 8.97,  "seats": 18, "in": True},
    "SPD":       {"pct": 7.78,  "seats": 15, "in": True},
    "Motoriste": {"pct": 6.77,  "seats": 13, "in": True},
    "Stacilo":   {"pct": 4.31,  "seats": 0,  "in": False},  # KÜSZÖB ALATT (5%)
}
GROUND_TRUTH_RANK = ["ANO", "SPOLU", "STAN", "Pirati", "SPD", "Motoriste", "Stacilo"]
GROUND_TRUTH_ANO_SPOLU_GAP = 34.52 - 23.36   # = 11.16 pont (HIVATALOS; a briefing "~16" közelítés volt)
TURNOUT = 68.95
# Küszöb: 1 párt 5%, 2-es koalíció 8%, 3+ koalíció 11%.
THRESHOLD_SINGLE = 5.0

# ── SIKER-KRITÉRIUM (ELŐRE rögzítve — briefing 6.) ───────────────────────
# ELSŐDLEGES (ordinális): ANO 1., SPOLU 2., Stačilo kiesik (a belépők alatt).
# MÁSODLAGOS (magnitúdó): az ANO–SPOLU rés nagyságrendje (~11 pont) eltalálva-e.
# CAVEAT (ELŐRE rögzítve): a korpusz az ONLINE/digitális elektorátust modellezi, ami
#   Csehországban Babiš-ELLEN tilt (ANO szavazó idősebb/vidéki/kevésbé online; Piráti/
#   STAN/Spolu fiatalabb/városi/online). Ez ELLENTÉTES a magyar online-skew-val (ott a
#   kihívót erősítette). Itt a digitális skew KOMPRIMÁLHATJA vagy akár MEGFORDÍTHATJA az
#   ANO-előnyt → az ordinális ANO-1. a NEHÉZ teszt; ha a vak Flash mégis ANO-t hoz elsőnek
#   az anti-Babiš-tiltú korpusz ELLENÉRE, az ERŐS bizonyíték a grounding erejére.

# ── PANEL MOTOR — PROVIDER-VÁLTÓ (a magyar futás két-modell kontrasztja) ──
#   - "siliconflow" / DeepSeek-V4-Flash: cutoff ~2025-01 → GENUINELY VAK a 2025.10-i
#     választásra (9 hónappal a látóhatár mögött). EZ a tudományosan tiszta vak-jósló
#     (elsődleges). Non-Think kötelező.
#   - "openai" / gpt-5.4-mini: cutoff ~2026-03 (választás UTÁN) → kontaminált KONTROLL,
#     a prior-fal demonstrálására.
import os as _os

_PROVIDER = _os.environ.get("ORAKEL_PROVIDER", "siliconflow").lower()
_DEFAULT_MODEL = {
    "siliconflow": "deepseek-ai/DeepSeek-V4-Flash",
    "openai":      "gpt-5.4-mini",
    "anthropic":   "claude-sonnet-4-6",
}
PANEL_PROVIDER = _PROVIDER
PANEL_MODEL = _os.environ.get("ORAKEL_PANEL_MODEL", _DEFAULT_MODEL.get(_PROVIDER, "claude-sonnet-4-6"))
PANEL_N = int(_os.environ.get("ORAKEL_PANEL_N", "80"))          # personák száma
PANEL_SEEDS = int(_os.environ.get("ORAKEL_PANEL_SEEDS", "2"))   # robusztusság-ellenőrzés
PANEL_CONCURRENCY = int(_os.environ.get("ORAKEL_CONCURRENCY", "4"))  # alacsonyabb (rate-limit barát)
