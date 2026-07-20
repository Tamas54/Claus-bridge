"""plugins/delphoi_scopegate.py — PYTHIA P2: SCOPE-GATE + COVERAGE + KONFIDENCIA.

A DOKTRÍNA SCOPE-TÖRVÉNYE kódba öntve: a szintetikus panel NARRATÍVA-vezérelt
kimenetet mér; strukturális/szektorális kimenetre NEM validált — a G3-lelet
szerint a between-sector diszkrimináció MODELL-INVARIÁNSAN bukik (Hy3 átlag
Spearman −0,311, Flash −0,378, közös narratív torzítás ρ=+0,74).

Két őr:
  1. scope_verdict(brief_text): heurisztika (kulcsszó-osztályok) + olcsó
     Hy3-ítész (1 hívás, prompt-JSON, lenient parse az echolot_orakel
     _parse_motifs mintájára). Verdikt: zöld=narratíva-vezérelt / sárga=kevert /
     piros=strukturális. PIROS NEM TILT: kötelező figyelmeztetés + konfidencia-
     levonás — a felhasználó dönt, de tájékozottan.
  2. coverage_score(country, window): 0..1 — cikkszám + forrás-diverzitás +
     lean-balansz a press_snapshots 'news' rétegéből; ha a Bridge-DB-ben nincs
     sajtó-adat (lokál/dev), a nowcast-korpusz paramétereiből (napok +
     snapshot-szám) számolódik. Env-küszöb: DELPHOI_COVERAGE_MIN (default 0.5)
     — alatta warning + konfidencia-levonás, szintén NEM tilt.

A konfidencia: 1.0 − scope-büntetés − coverage-hiány, alsó korlát 0.1.
"""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger("plugins.delphoi_scopegate")

__plugin_meta__ = {
    "name": "delphoi_scopegate",
    "version": "1.0.0",
    "description": "DELPHOI scope-gate (P2) — narrativ/strukturalis verdikt + coverage + konfidencia",
    "library": True,  # nincs sajat MCP-tool — a delphoi.py hasznalja
}

VERDICT_GREEN = "zöld"
VERDICT_YELLOW = "sárga"
VERDICT_RED = "piros"
_SEVERITY = {VERDICT_GREEN: 0, VERDICT_YELLOW: 1, VERDICT_RED: 2}

# Konfidencia-levonás verdiktenként (piros nem tilt, de drága).
CONFIDENCE_PENALTY = {VERDICT_GREEN: 0.0, VERDICT_YELLOW: 0.15, VERDICT_RED: 0.4}

RED_WARNING = (
    "SCOPE-FIGYELMEZTETÉS (piros): a stimulus strukturális/szektorális kimenetre "
    "kérdez — a szintetikus panel erre NEM validált (between-sector Spearman "
    "−0,31/−0,38, MODELL-INVARIÁNS bukás, G3-falszifikáció). Az eredmény "
    "legfeljebb narratíva-jelként értelmezhető; szektorális/strukturális "
    "döntésre NE használd. A konfidencia levonással jelenik meg."
)
YELLOW_WARNING = (
    "SCOPE-JELZÉS (sárga): a stimulus kevert — narratív ÉS strukturális elemet "
    "is tartalmaz. A panel a narratív komponenst méri; a strukturális részre "
    "az eredmény nem bizonyíték."
)


def judge_model() -> str:
    """Az ítész-motor — KIZÁRÓLAG Hy3 (Flash tilos, silent fallback nincs)."""
    return os.environ.get("DELPHOI_SCOPE_JUDGE_MODEL", "tencent/Hy3")


def coverage_min() -> float:
    try:
        return float(os.environ.get("DELPHOI_COVERAGE_MIN", "0.5"))
    except ValueError:
        return 0.5


# ---------------------------------------------------------------------------
# 1a. HEURISZTIKA — kulcsszó-osztályok. A KEMÉNY szektorális jelzők önmagukban
# piros-gyanút adnak (az ESI-falszifikáció terepe pont ez volt); a PUHA
# strukturális jelzők egyenként sárgát, kettő fölött pirosat.
# ---------------------------------------------------------------------------
STRUCTURAL_HARD = (
    "ágazat", "agazat", "ágazati", "szektor", "sector", "iparág", "iparag",
    "iparági", "industry", "b2b", "értéklánc", "erteklanc", "beszállító",
    "beszallito", "supply chain", "vertikum",
)
STRUCTURAL_SOFT = (
    "gdp", "kibocsátás", "kibocsatas", "termelési index", "termelesi index",
    "árbevétel", "arbevetel", "üzleti bizalom", "uzleti bizalom",
    "megrendelés-állomány", "megrendeles-allomany", "kapacitáskihasznált",
    "kapacitaskihasznalt", "árfolyam", "arfolyam", "hozamgörbe", "hozamgorbe",
    "forecast", "előrejelzés", "elorejelzes",
)
NARRATIVE_MARKERS = (
    "megítélés", "megiteles", "vélemény", "velemeny", "hangulat", "közérzet",
    "kozerzet", "narratíva", "narrativa", "kampány", "kampany", "üzenet",
    "uzenet", "imázs", "imazs", "reakció", "reakcio", "vonzó", "vonzo",
    "tetszik", "megítélése", "megitelese",
)


def heuristic_scope(text: str) -> dict:
    """Olcsó, determinista első réteg. A kimenet csak a TALÁLT kulcsszavakat
    hordozza, a nyers stimulust SOHA (privát input nem szivároghat riportba)."""
    low = " ".join(str(text or "").lower().split())
    hard = sorted({p for p in STRUCTURAL_HARD if p in low})
    soft = sorted({p for p in STRUCTURAL_SOFT if p in low})
    narrative = sorted({p for p in NARRATIVE_MARKERS if p in low})
    if hard or len(soft) >= 2:
        verdict = VERDICT_RED
    elif soft:
        verdict = VERDICT_YELLOW
    else:
        verdict = VERDICT_GREEN
    return {"verdict": verdict, "structural_hard": hard,
            "structural_soft": soft, "narrative": narrative}


# ---------------------------------------------------------------------------
# 1b. Hy3-ÍTÉSZ — 1 hívás, prompt-JSON, lenient parse (_parse_motifs-minta).
# Az ítész hibája SOSEM töri a futást: None → a heurisztika dönt egyedül.
# ---------------------------------------------------------------------------
_JUDGE_PROMPT = (
    "Egy szintetikus fókuszcsoport-panel scope-őre vagy. A panel NARRATÍVA-"
    "vezérelt kimenetet mér (megítélés, hangulat, vonzerő, üzenet-hatás); "
    "STRUKTURÁLIS/szektorális/mechanikus gazdasági kimenetre (ágazati bontás, "
    "B2B/iparági mélység, GDP-szerű mennyiség, árfolyam) NEM validált.\n\n"
    "Sorold be az alábbi mérési stimulust:\n"
    "  \"zöld\"  = narratíva-vezérelt (a panel scope-jában),\n"
    "  \"sárga\" = kevert (narratív és strukturális elem is),\n"
    "  \"piros\" = strukturális/szektorális (scope-on kívül).\n\n"
    "STIMULUS:\n„{stimulus}”\n\n"
    "Válaszolj KIZÁRÓLAG ezzel a JSON-nal:\n"
    "{{\"verdict\": \"zöld|sárga|piros\", \"indok\": \"egy rövid mondat\"}}"
)

_VERDICT_ALIASES = {
    "zöld": VERDICT_GREEN, "zold": VERDICT_GREEN, "green": VERDICT_GREEN,
    "sárga": VERDICT_YELLOW, "sarga": VERDICT_YELLOW, "yellow": VERDICT_YELLOW,
    "piros": VERDICT_RED, "vörös": VERDICT_RED, "voros": VERDICT_RED, "red": VERDICT_RED,
}


def _parse_judge(text: str) -> dict | None:
    """Lenient JSON-parse az echolot_orakel._parse_motifs mintájára:
    code-fence lehántás → első {...} blokk → json.loads → verdikt-normalizálás."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    verdict = _VERDICT_ALIASES.get(str(data.get("verdict", "")).strip().lower())
    if verdict is None:
        return None
    return {"verdict": verdict, "indok": str(data.get("indok") or "")[:200]}


async def judge_scope(text: str, chat_fn=None) -> dict | None:
    """Egyetlen olcsó Hy3-hívás. chat_fn injektálható (teszt: mockolt ítész);
    élesben a pollster._chat útja (thinking:disabled, exp backoff)."""
    prompt = _JUDGE_PROMPT.format(stimulus=str(text or "")[:800])
    try:
        if chat_fn is not None:
            raw = await chat_fn(prompt)
        else:
            import httpx

            from plugins import pollster
            async with httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {pollster._provider()[1]}"},
                    timeout=60) as client:
                raw = await pollster._chat(client, prompt, max_tokens=160,
                                           temperature=0.0, model=judge_model())
    except Exception as e:  # noqa: BLE001 — az ítész-hiba nem törhet futást
        logger.warning("delphoi scope-ítész hívás elhalt (heurisztika dönt): %s", e)
        return None
    parsed = _parse_judge(raw)
    if parsed is None:
        logger.warning("delphoi scope-ítész válasz nem parse-olható (heurisztika dönt)")
    return parsed


async def scope_verdict(text: str, chat_fn=None, use_judge: bool = True) -> dict:
    """A kombinált verdikt: heurisztika + (opcionális) Hy3-ítész, a SZIGORÚBB
    nyer (konzervatív — piros úgysem tilt, csak figyelmeztet + levon)."""
    heur = heuristic_scope(text)
    judge = await judge_scope(text, chat_fn=chat_fn) if use_judge else None
    verdict = heur["verdict"]
    if judge and _SEVERITY.get(judge["verdict"], -1) > _SEVERITY[verdict]:
        verdict = judge["verdict"]
    warning = {VERDICT_RED: RED_WARNING, VERDICT_YELLOW: YELLOW_WARNING}.get(verdict)
    return {"verdict": verdict, "heuristic": heur, "judge": judge,
            "warning": warning, "confidence_penalty": CONFIDENCE_PENALTY[verdict]}


# ---------------------------------------------------------------------------
# 2. COVERAGE — 0..1 pontszám a korpusz-mélységről.
# Elsődleges forrás: press_snapshots 'news' rétege (cikkszám + forrás-
# diverzitás + lean-balansz); fallback: a nowcast-korpusz paraméterei
# (build_country_corpus kimenete: days + snapshot_ids).
# ---------------------------------------------------------------------------
# Heurisztikus célértékek: ~8 cikk/nap egészséges hírfolyam; 10 külön forrás
# a diverzitás-plafon (vö. a nyelvenkénti 60-forrás rendszerszintű küszöbbel —
# egy 7 napos ablakban 10 AKTÍV forrás már jó merítés).
ARTICLES_PER_DAY_TARGET = 8
SOURCE_DIVERSITY_TARGET = 10

_LEAN_MAP = (
    (("l", "bal", "left", "liber"), "L"),
    (("r", "jobb", "right", "kons"), "R"),
    (("c", "koz", "köz", "cent", "center"), "C"),
)


def _norm_lean(lean) -> str | None:
    low = str(lean or "").strip().lower()
    if not low:
        return None
    for prefixes, out in _LEAN_MAP:
        if any(low.startswith(p) for p in prefixes):
            return out
    return None


def _lean_balance(leans: list[str]) -> float | None:
    """Normalizált entrópia az L/C/R eloszláson (1.0 = tökéletes balansz).
    5 címkézett cikk alatt nincs értelmes becslés → None (semleges 0.5-tel
    számol a kompozit)."""
    import math
    if len(leans) < 5:
        return None
    from collections import Counter
    counts = Counter(leans)
    total = sum(counts.values())
    h = -sum((c / total) * math.log(c / total) for c in counts.values() if c)
    return round(h / math.log(3), 3)


def coverage_score(get_db, country: str, window_days: int = 7, corpus: dict | None = None) -> dict:
    """0..1 coverage az ország hír-ablakára. Komponensek és az adat-bázis
    ('basis') explicit a kimenetben — a szám sosem magyarázat nélküli."""
    try:
        from plugins.delphoi import COUNTRY_PANEL_CONFIG
        cfg = COUNTRY_PANEL_CONFIG.get(str(country or "").upper())
        lang = cfg["lang"] if cfg else str(country or "").lower()
    except Exception:  # noqa: BLE001
        lang = str(country or "").lower()

    articles: list[dict] = []
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT content FROM press_snapshots WHERE lang=? AND signal_type='news' "
                "ORDER BY date_iso DESC LIMIT ?", (lang, int(window_days))).fetchall()
        finally:
            conn.close()
        seen: set = set()
        for r in rows:
            try:
                content = json.loads(r["content"])
            except Exception:  # noqa: BLE001
                continue
            for a in (content.get("articles") or []):
                title = (a.get("title") or "").strip()
                key = title.lower()[:48]
                if not title or key in seen:
                    continue
                seen.add(key)
                articles.append({
                    "source": (a.get("source") or a.get("source_name") or "").strip().lower(),
                    "lean": _norm_lean(a.get("lean")),
                })
    except Exception:  # noqa: BLE001 — press_snapshots hiánya nem hiba, csak fallback
        articles = []

    min_th = coverage_min()
    if articles:
        n = len(articles)
        volume = min(1.0, n / float(ARTICLES_PER_DAY_TARGET * max(1, window_days)))
        sources = {a["source"] for a in articles if a["source"]}
        diversity = min(1.0, len(sources) / float(SOURCE_DIVERSITY_TARGET))
        balance = _lean_balance([a["lean"] for a in articles if a["lean"]])
        score = 0.4 * volume + 0.3 * diversity + 0.3 * (balance if balance is not None else 0.5)
        components = {"n_articles": n, "volume": round(volume, 3),
                      "n_sources": len(sources), "diversity": round(diversity, 3),
                      "lean_balance": balance}
        basis = "press_snapshots"
    elif corpus and corpus.get("snapshot_ids"):
        days = int(corpus.get("days") or 0)
        day_cov = min(1.0, days / float(max(1, window_days)))
        volume = min(1.0, len(corpus["snapshot_ids"]) / float(3 * max(1, window_days)))
        score = 0.6 * day_cov + 0.4 * volume
        components = {"corpus_days": days, "day_coverage": round(day_cov, 3),
                      "n_snapshots": len(corpus["snapshot_ids"]), "volume": round(volume, 3)}
        basis = "corpus_params"
    else:
        score, components, basis = 0.0, {}, "none"

    score = round(max(0.0, min(1.0, score)), 3)
    below = score < min_th
    return {
        "score": score, "basis": basis, "components": components,
        "window_days": int(window_days), "min": min_th, "below_min": below,
        "warning": (f"ALACSONY COVERAGE ({score} < {min_th}): a(z) {str(country).upper()} "
                    "hír-ablak vékony (cikkszám/forrás-diverzitás/lean-balansz) — "
                    "a jel zajosabb, a konfidencia levonással jelenik meg.") if below else None,
    }


# ---------------------------------------------------------------------------
# 3. KONFIDENCIA — a scope-büntetés és a coverage-hiány EGY számban.
# ---------------------------------------------------------------------------
def confidence(scope: dict | None, coverage: dict | None) -> float:
    """1.0 − scope-büntetés − max(0, min_küszöb − coverage). Alsó korlát 0.1 —
    a konfidencia sosem nulla (a jel megy ki, csak címkézve)."""
    c = 1.0
    if scope:
        c -= float(scope.get("confidence_penalty") or 0.0)
    if coverage:
        c -= max(0.0, float(coverage.get("min") or coverage_min())
                 - float(coverage.get("score") or 0.0))
    return round(max(0.1, c), 3)
