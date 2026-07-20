"""plugins/delphoi_report.py — PYTHIA B2: RIPORT-MŰ (a letölthető termék).

Egy kész (status='done') fókuszcsoport-jobból ügyfél-riportot épít:
  HTML (Jinja2, ha elérhető — különben NOTSTROM f-string sablon)
  → PDF (WeasyPrint, ha elérhető — különben a HTML a PDF-fallback)
  + JSON + CSV aggregátum. Artefakt-mentés + delphoi_reports tábla-sor.

FEJEZETEK (a parancs szerint): vezetői összefoglaló (Hy3-szintézis, 3 mondat,
a report.lang nyelvén) · eredmények dimenziónként baseline-vonallal ·
szegmens-bontás · idősor (tracking-briefnél) · szintetizált hangulatkép
(AGGREGÁLT — szó szerinti persona-idézet TILOS: a szintézis KIZÁRÓLAG az
aggregátumot látja, nyers reakció be sem kerül a promptba) · exploratív
fejezet (nem validált) · MÓDSZERTAN-LAP · "mit mértünk / mit nem" ·
disclaimer + verify-link.

MODIFIKATION 2:
  A1 — BRAND-AGNOSZTIKUS: minden megjelenítési elem (név, lábléc, disclaimer,
       bázis-URL) a plugins.delphoi_brands.get_brand()-ből. Echolot-arculat
       beégetése TILOS.
  A3 — teljes hu+en string-katalógus (STRINGS — kézzel írt mindkét nyelven,
       nem gépi tükörfordítás).
  A4 — minden link a brand public_base_url-jéből épül.

LLM: KIZÁRÓLAG tencent/Hy3 (DELPHOI_REPORT_MODEL env; Flash TILOS) —
prompt-JSON kimenet + lenient parse; LLM-hiba esetén determinista NOTSTROM
összefoglaló (a riport sosem hasal el a szintézisen).

Artefakt-tár: DELPHOI_REPORTS_DIR env; default a BRIDGE_DB_PATH melletti
reports/ könyvtár (Railway-n a /data volume alatt marad a DB mellett).
"""
from __future__ import annotations

import csv
import html as _html
import io
import json
import logging
import os
import re
from datetime import datetime, timezone

logger = logging.getLogger("plugins.delphoi_report")

__plugin_meta__ = {
    "name": "delphoi_report",
    "version": "1.0.0",
    "description": "DELPHOI riport-mu (B2) — HTML/PDF/JSON/CSV riport brand-agnosztikusan",
}

_DEPS: dict | None = None

SYNTH_MODEL = os.environ.get("DELPHOI_REPORT_MODEL", "tencent/Hy3")

REPORT_FORMATS = ("pdf", "json", "csv")
_MIME = {"html": "text/html; charset=utf-8", "pdf": "application/pdf",
         "json": "application/json; charset=utf-8", "csv": "text/csv; charset=utf-8"}


def reports_dir() -> str:
    """Env-vezérelt artefakt-könyvtár; default a DB melletti reports/."""
    d = os.environ.get("DELPHOI_REPORTS_DIR", "")
    if not d:
        db = os.environ.get("BRIDGE_DB_PATH", "bridge.db")
        d = os.path.join(os.path.dirname(os.path.abspath(db)), "reports")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# A3 — STRING-KATALÓGUS (hu+en, kézzel írt mindkét nyelven)
# ---------------------------------------------------------------------------
STRINGS: dict = {
    "hu": {
        "report_title": "Szintetikus fókuszcsoport — riport",
        "generated_at": "Készült",
        "job_label": "Futás-azonosító",
        "goal_label": "A megrendelés célja",
        "ch_exec": "Vezetői összefoglaló",
        "ch_dimensions": "Eredmények dimenziónként",
        "ch_segments": "Szegmens-bontás",
        "ch_timeseries": "Idősor — gördülő mérés",
        "ch_mood": "Szintetizált hangulatkép (aggregált)",
        "ch_exploratory": "Exploratív fejezet — nem validált",
        "ch_methodology": "Módszertan-lap",
        "ch_scope": "Mit mértünk — és mit nem",
        "ch_disclaimer": "Jognyilatkozat",
        "baseline_label": "viszonyítási alap",
        "score_label": "jel",
        "vs_baseline": "eltérés a bázistól",
        "n_label": "elemszám",
        "segment": "szegmens",
        "variant": "variáns",
        "choice_rate": "választás-arány",
        "share": "részesedés",
        "ranking": "rangsor",
        "unclear_top": "leggyakoribb homályos kifejezések (aggregált)",
        "clear_ratio": "a panel ekkora hányadának volt világos",
        "run_at": "futás ideje",
        "overall_score": "összesített jel",
        "dim_measured": "mért dimenzió (SSR-horgonyzott)",
        "dim_pending": ("ebben a futásban nem mért — a dimenzió natív "
                        "SSR-horgonykészlete készen áll, a motor-út a következő "
                        "körben élesedik"),
        "exploratory_badge": "exploratív — nem validált",
        "exploratory_note": ("Az alábbi saját kérdések SSR-horgony nélkül, "
                             "validálatlan exploratív kérdésként rögzültek. "
                             "Eredményük tájékoztató jellegű, nem mérés."),
        "exploratory_pending": ("A kérdések a briefben rögzítve; kiértékelésük "
                                "az exploratív motor-út élesítésekor fut le."),
        "mood_note": ("A hangulatkép a panel AGGREGÁLT reakcióiból készült "
                      "gépi szintézis — szó szerinti válasz nem szerepel benne."),
        "meth_n": "Panel-méret (kért / teljesült)",
        "meth_quota": "Kvóta-forrás",
        "meth_panel_version": "Panel-verzió",
        "meth_model": "Modell-azonosító",
        "meth_grounding": "Grounding-ablak",
        "meth_grounding_val": ("az ország datált hír-korpusza, motor-default "
                               "7 napos ablak; a pontos cikk-készletet a "
                               "korpusz-lenyomat rögzíti"),
        "meth_corpus_hash": "Korpusz-lenyomat (SHA256)",
        "meth_coverage": "Kitöltési arány (coverage)",
        "meth_scope": "Scope-verdikt",
        "meth_calibration": "Kalibrációs verzió",
        "meth_contamination": "Kontamináció-státusz",
        "meth_na": "n/a (e futásban nem rögzült)",
        "contamination_none": "nem vizsgált ebben a futásban",
        "scope_measured": ("MÉRTÜK: a szöveg/koncepció keltette RELATÍV reakciót "
                           "egy demográfiai kvótákkal mintavételezett szintetikus "
                           "panelen — irány, rangsor, szegmensek közti dőlés."),
        "scope_not_measured": ("NEM MÉRTÜK: valós populáció abszolút arányait "
                               "(ez nem közvélemény-kutatás); strukturális/"
                               "mechanikus kimeneteket (piaci részesedés, "
                               "forgalom, GDP — scope-törvény); és nem "
                               "helyettesíti a valós piackutatást."),
        "verify_label": "A nyilvános track record integritás-ellenőrzése",
        "product_link_label": "Új mérés indítása",
        "series_note": "A jel RELATÍV (1–5 skálán a 3,0 semleges ponthoz mérve).",
        "notstrom_summary": ("Az összesített jel {score} az 1–5 skálán "
                             "(bázis: {baseline}). A panel {n} szintetikus "
                             "résztvevővel futott le, {country} kvóták szerint. "
                             "A részletes bontást a riport fejezetei tartalmazzák."),
        "notstrom_mood": ("A panel aggregált reakciója a semleges ponthoz képest "
                          "{tilt} irányba dől; a legerősebb jel a(z) {top} "
                          "szegmensből érkezett."),
        "tilt_pos": "pozitív", "tilt_neg": "negatív", "tilt_neutral": "semleges",
    },
    "en": {
        "report_title": "Synthetic focus group — report",
        "generated_at": "Generated",
        "job_label": "Run ID",
        "goal_label": "Brief goal",
        "ch_exec": "Executive summary",
        "ch_dimensions": "Results by dimension",
        "ch_segments": "Segment breakdown",
        "ch_timeseries": "Time series — rolling measurement",
        "ch_mood": "Synthesized mood picture (aggregated)",
        "ch_exploratory": "Exploratory chapter — not validated",
        "ch_methodology": "Methodology sheet",
        "ch_scope": "What we measured — and what we did not",
        "ch_disclaimer": "Disclaimer",
        "baseline_label": "baseline",
        "score_label": "signal",
        "vs_baseline": "vs baseline",
        "n_label": "sample size",
        "segment": "segment",
        "variant": "variant",
        "choice_rate": "choice rate",
        "share": "share",
        "ranking": "ranking",
        "unclear_top": "most frequent unclear terms (aggregated)",
        "clear_ratio": "of the panel found it clear",
        "run_at": "run at",
        "overall_score": "overall signal",
        "dim_measured": "measured dimension (SSR-anchored)",
        "dim_pending": ("not measured in this run — the native SSR anchor set "
                        "for this dimension is ready; the engine path goes live "
                        "in the next iteration"),
        "exploratory_badge": "exploratory — not validated",
        "exploratory_note": ("The custom questions below were recorded as "
                             "unvalidated exploratory questions, without SSR "
                             "anchors. Their output is indicative, not a "
                             "measurement."),
        "exploratory_pending": ("The questions are recorded in the brief; they "
                                "will be evaluated once the exploratory engine "
                                "path goes live."),
        "mood_note": ("The mood picture is a machine synthesis of the panel's "
                      "AGGREGATED reactions — no verbatim response appears in it."),
        "meth_n": "Panel size (requested / completed)",
        "meth_quota": "Quota source",
        "meth_panel_version": "Panel version",
        "meth_model": "Model ID",
        "meth_grounding": "Grounding window",
        "meth_grounding_val": ("the country's dated news corpus, engine-default "
                               "7-day window; the exact article set is pinned by "
                               "the corpus fingerprint"),
        "meth_corpus_hash": "Corpus fingerprint (SHA256)",
        "meth_coverage": "Completion ratio (coverage)",
        "meth_scope": "Scope verdict",
        "meth_calibration": "Calibration version",
        "meth_contamination": "Contamination status",
        "meth_na": "n/a (not recorded for this run)",
        "contamination_none": "not examined in this run",
        "scope_measured": ("WE MEASURED: the RELATIVE reaction your text/concept "
                           "triggers on a quota-sampled synthetic panel — "
                           "direction, ranking, tilt between segments."),
        "scope_not_measured": ("WE DID NOT MEASURE: absolute proportions of a "
                               "real population (this is not an opinion poll); "
                               "structural/mechanical outcomes (market share, "
                               "revenue, GDP — scope law); and it is no "
                               "substitute for real market research."),
        "verify_label": "Integrity check of the public track record",
        "product_link_label": "Launch a new measurement",
        "series_note": "The signal is RELATIVE (on a 1–5 scale against the 3.0 neutral point).",
        "notstrom_summary": ("The overall signal is {score} on the 1–5 scale "
                             "(baseline: {baseline}). The panel ran with {n} "
                             "synthetic participants under {country} quotas. "
                             "See the report chapters for the detailed breakdown."),
        "notstrom_mood": ("The panel's aggregated reaction tilts {tilt} of the "
                          "neutral point; the strongest signal came from the "
                          "{top} segment."),
        "tilt_pos": "positive", "tilt_neg": "negative", "tilt_neutral": "neutral",
    },
}

# Kvóta-forrás megjegyzés országonként (módszertan-lap) — hu+en.
QUOTA_SOURCES = {
    "HU": {"hu": "KSH mun0005/mun0006 + 2022-es cenzus + NMHH 2026-05 (média-mix)",
           "en": "HSO (KSH) mun0005/mun0006 + 2022 census + NMHH 2026-05 (media mix)"},
    "CZ": {"hu": "Eurostat-közeli marginálisok (durvább készlet)",
           "en": "Eurostat-based marginals (coarser set)"},
    "PT": {"hu": "Eurostat-közeli marginálisok (durvább készlet)",
           "en": "Eurostat-based marginals (coarser set)"},
    "PL": {"hu": "Eurostat-közeli marginálisok (durvább készlet)",
           "en": "Eurostat-based marginals (coarser set)"},
}


def _t(lang: str, key: str) -> str:
    d = STRINGS.get(lang) or STRINGS["hu"]
    return d.get(key) or STRINGS["hu"].get(key, key)


def _e(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


# ---------------------------------------------------------------------------
# ADATGYŰJTÉS — csak AGGREGÁTUM (nyers persona-sor ide be sem kerülhet)
# ---------------------------------------------------------------------------
def collect_report_data(get_db, job_id: str, user_id: str) -> dict:
    """A riport minden bemenete egy dict-ben. CSAK a tulajdonosnak; csak done
    jobra. A delphoi_panel_responses táblát SZÁNDÉKOSAN nem olvassuk — a
    riport-mű a nyers reakciókat fizikailag nem látja."""
    conn = get_db()
    try:
        job = conn.execute("SELECT * FROM delphoi_jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["user_id"] != str(user_id):
            return {"ok": False, "error": "not_found"}
        if job["status"] != "done" or not job["result_json"]:
            return {"ok": False, "error": "not_ready", "status": job["status"]}
        agg = json.loads(job["result_json"])
        try:
            panel_spec = json.loads(job["panel_spec"] or "{}")
        except Exception:  # noqa: BLE001
            panel_spec = {}
        brief, runs = None, []
        brief_id = panel_spec.get("brief_id") or ""
        if brief_id:
            try:
                brow = conn.execute("SELECT * FROM delphoi_briefs WHERE brief_id=?",
                                    (brief_id,)).fetchone()
                if brow and brow["user_id"] == str(user_id):
                    brief = json.loads(brow["spec_json"])
                    runs = [dict(r) for r in conn.execute(
                        "SELECT job_id, run_at, overall_score FROM delphoi_brief_runs "
                        "WHERE brief_id=? ORDER BY run_at ASC", (brief_id,)).fetchall()]
            except Exception:  # noqa: BLE001 — brief-táblák nélkül (migráció előtt) a riport él
                logger.info("delphoi_report: brief-táblák nem elérhetők (%s)", brief_id)
        job_cols = {k: job[k] for k in job.keys()}
    finally:
        conn.close()
    return {"ok": True, "job_id": job_id, "job": job_cols, "aggregate": agg,
            "panel_spec": panel_spec, "brief_id": brief_id, "brief": brief,
            "runs": runs}


def _calibration_version() -> str:
    """Defenzív: a kalibrációs registry verziója, ha a modul létezik (a G4-kör
    plugins/delphoi_calibration.py-ja) — nélküle n/a."""
    try:
        from plugins import delphoi_calibration as _cal  # noqa: PLC0415
        for attr in ("CALIBRATION_VERSION", "REGISTRY_VERSION", "VERSION"):
            v = getattr(_cal, attr, None)
            if v:
                return str(v)
        meta = getattr(_cal, "__plugin_meta__", None) or {}
        return str(meta.get("version", "n/a"))
    except Exception:  # noqa: BLE001
        return "n/a"


# ---------------------------------------------------------------------------
# Hy3-SZINTÉZIS — prompt-JSON + lenient parse; determinista NOTSTROM fallback
# ---------------------------------------------------------------------------
def parse_llm_json(text: str) -> dict:
    """Lenient JSON-parse LLM-kimenetre: kódkerítés-nyesés → első '{' … utolsó
    '}' kivágás → json.loads. Bukásra üres dict (a hívó NOTSTROM-ol)."""
    if not text:
        return {}
    t = re.sub(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$", "", text.strip())
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        obj = json.loads(t[start:end + 1])
        return obj if isinstance(obj, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _synth_prompt(agg: dict, goal: str, lang: str) -> str:
    compact = {k: agg.get(k) for k in
               ("kind", "n", "overall_score", "baseline", "segments", "variants",
                "ranking", "unclear_top", "clear_ratio", "panel") if k in agg}
    payload = json.dumps(compact, ensure_ascii=False)
    if lang == "en":
        return (
            "You are writing the executive summary of a SYNTHETIC focus group "
            "report. Input: the aggregated panel result as JSON (no raw "
            "responses exist for you). Rules: the signal is RELATIVE (1–5 "
            "scale, 3.0 neutral); never claim absolute population percentages; "
            "never invent quotes; write in English.\n\n"
            f"Brief goal: {goal or '(not given)'}\n"
            f"Aggregated result JSON:\n{payload}\n\n"
            'Return ONLY a JSON object, nothing else: {"summary": "<exactly 3 '
            'sentences, executive tone>", "mood": "<2-3 sentences describing '
            'the aggregated mood of the panel, no quotes>"}'
        )
    return (
        "Egy SZINTETIKUS fókuszcsoport riportjának vezetői összefoglalóját "
        "írod. Bemenet: az aggregált panel-eredmény JSON-ban (nyers válasz "
        "nem létezik számodra). Szabályok: a jel RELATÍV (1–5 skála, 3,0 a "
        "semleges pont); soha ne állíts abszolút populációs százalékot; soha "
        "ne találj ki idézetet; magyarul írj.\n\n"
        f"A brief célja: {goal or '(nincs megadva)'}\n"
        f"Aggregált eredmény JSON:\n{payload}\n\n"
        'KIZÁRÓLAG egy JSON-objektumot adj vissza, semmi mást: '
        '{"summary": "<pontosan 3 mondat, vezetői hangnem>", '
        '"mood": "<2-3 mondat a panel aggregált hangulatáról, idézet nélkül>"}'
    )


def _notstrom_synthesis(agg: dict, lang: str) -> dict:
    """Determinista összefoglaló LLM nélkül — a riport sosem hasal el."""
    s = STRINGS.get(lang) or STRINGS["hu"]
    score = agg.get("overall_score")
    baseline = (agg.get("baseline") or {}).get("value", 3.0)
    panel = agg.get("panel") or {}
    segs = agg.get("segments") or []
    top = segs[0]["segment"] if segs else "—"
    if score is None:
        tilt = s["tilt_neutral"]
    else:
        tilt = (s["tilt_pos"] if score > baseline + 0.05
                else s["tilt_neg"] if score < baseline - 0.05 else s["tilt_neutral"])
    summary = s["notstrom_summary"].format(
        score=(f"{score:.2f}" if score is not None else "n/a"),
        baseline=baseline, n=panel.get("n_completed", agg.get("n", "?")),
        country=panel.get("country", "?"))
    mood = s["notstrom_mood"].format(tilt=tilt, top=top)
    return {"summary": summary, "mood": mood, "model": "notstrom", "fallback": True}


async def synthesize(agg: dict, goal: str, lang: str, deps: dict | None = None,
                     synth_fn=None) -> dict:
    """Hy3-szintézis (prompt-JSON + lenient parse). synth_fn injektálható
    (teszt); hibára determinista NOTSTROM."""
    prompt = _synth_prompt(agg, goal, lang)
    try:
        if synth_fn is not None:
            text = await synth_fn(prompt)
        else:
            import httpx
            from plugins import pollster
            async with httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {pollster._provider()[1]}"},
                    timeout=90) as client:
                text = await pollster._chat(client, prompt, max_tokens=600,
                                            temperature=0.4, model=SYNTH_MODEL)
        parsed = parse_llm_json(text)
        summary = str(parsed.get("summary") or "").strip()
        mood = str(parsed.get("mood") or "").strip()
        if not summary:
            raise ValueError("üres/parse-olhatatlan szintézis")
        return {"summary": summary, "mood": mood, "model": SYNTH_MODEL,
                "fallback": False}
    except Exception as e:  # noqa: BLE001 — NOTSTROM, hangos loggal
        logger.warning("delphoi_report szintézis NOTSTROM-ra esett: %s", e)
        return _notstrom_synthesis(agg, lang)


# ---------------------------------------------------------------------------
# HTML-ÉPÍTÉS — a fejezet-törzsek Pythonban épülnek (kézi escape), a lap-shell
# Jinja2 (ha elérhető) vagy NOTSTROM str.format. Nyers persona-szöveg be sem
# kerülhet: a bemenet KIZÁRÓLAG az aggregátum + szintézis + módszertan-mezők.
# ---------------------------------------------------------------------------
_CSS = """
body { font-family: Georgia, 'Times New Roman', serif; color: #1a1d21;
  margin: 0; padding: 2.2rem 2.6rem; line-height: 1.55; }
header { border-bottom: 3px solid #1a1d21; padding-bottom: 0.8rem; margin-bottom: 1.6rem; }
header .brand { font-size: 1.05rem; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; }
h1 { font-size: 1.55rem; margin: 0.4rem 0 0.2rem; }
h2 { font-size: 1.12rem; margin: 1.6rem 0 0.5rem; border-bottom: 1px solid #c9ccd1;
  padding-bottom: 0.25rem; }
.meta { color: #5a616b; font-size: 0.86rem; }
table { border-collapse: collapse; width: 100%; margin: 0.5rem 0 1rem; font-size: 0.92rem; }
th, td { text-align: left; padding: 0.35rem 0.5rem; border-bottom: 1px solid #e2e5e9; }
th { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; color: #5a616b; }
.scale { position: relative; height: 10px; background: #eceef1; border-radius: 5px;
  margin: 0.35rem 0 0.2rem; }
.scale .mark { position: absolute; top: -3px; width: 4px; height: 16px;
  background: #0f766e; border-radius: 2px; }
.scale .base { position: absolute; top: 0; width: 2px; height: 10px; background: #9aa1ab; }
.baseline-note { color: #5a616b; font-size: 0.84rem; font-style: italic; }
.badge { display: inline-block; border: 1px solid #b45309; color: #b45309;
  border-radius: 999px; padding: 0.1rem 0.6rem; font-size: 0.74rem;
  letter-spacing: 0.04em; text-transform: uppercase; }
.note { color: #5a616b; font-size: 0.86rem; }
footer { margin-top: 2.2rem; border-top: 1px solid #c9ccd1; padding-top: 0.7rem;
  color: #5a616b; font-size: 0.84rem; }
dl.meth { display: grid; grid-template-columns: 16rem 1fr; gap: 0.25rem 0.9rem;
  font-size: 0.9rem; }
dl.meth dt { color: #5a616b; }
dl.meth dd { margin: 0; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 0.82em; }
@media print { body { padding: 1.2cm 1.4cm; } }
"""

_SHELL = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<header>
  <div class="brand">{brand_name}</div>
  <h1>{title}</h1>
  <div class="meta">{meta_line}</div>
</header>
{sections}
<footer>
  <p>{footer_text}</p>
  <p><a href="{verify_url}">{verify_label}</a> · <a href="{product_url}">{product_label}</a></p>
</footer>
</body>
</html>
"""


def _render_shell(ctx: dict) -> str:
    """Lap-shell: Jinja2, ha elérhető (autoescape nélkül — a fejezet-törzsek már
    escape-elt HTML-darabok); különben NOTSTROM str.format ugyanarra a sablonra.
    A {name} format-placeholderek Jinja-úton {{name}}-re fordulnak (regex)."""
    try:
        from jinja2 import Template  # noqa: PLC0415
        jinja_src = re.sub(r"\{(\w+)\}", r"{{\1}}", _SHELL)
        return Template(jinja_src).render(**ctx)
    except Exception:  # noqa: BLE001 — NOTSTROM: tiszta str.format
        return _SHELL.format(**ctx)


def _scale_bar(score: float | None, baseline: float = 3.0) -> str:
    if score is None:
        return ""
    pos = max(0.0, min(1.0, (float(score) - 1.0) / 4.0)) * 100
    bpos = max(0.0, min(1.0, (float(baseline) - 1.0) / 4.0)) * 100
    return (f'<div class="scale"><span class="base" style="left:{bpos:.1f}%"></span>'
            f'<span class="mark" style="left:{pos:.1f}%"></span></div>')


def _sec(sec_id: str, title: str, body: str) -> str:
    return f'<section id="{sec_id}"><h2>{_e(title)}</h2>{body}</section>'


def _dimensions_body(data: dict, lang: str) -> str:
    agg = data["aggregate"]
    brief = data.get("brief") or {}
    dims = brief.get("dimensions") or ["appeal"]
    baseline = (agg.get("baseline") or {})
    bval = baseline.get("value", 3.0)
    bnote = baseline.get("note", "")
    parts = []
    for i, dim in enumerate(dims):
        rows = []
        if i == 0:
            score = agg.get("overall_score")
            if score is not None:
                rows.append(
                    f"<p><strong>{_e(_t(lang, 'overall_score'))}: {score:.2f} / 5</strong> "
                    f"{_scale_bar(score, float(bval) if isinstance(bval, (int, float)) else 3.0)}"
                    f'<span class="baseline-note">{_e(_t(lang, "baseline_label"))}: '
                    f"{_e(bval)} — {_e(bnote)}</span></p>")
            if agg.get("variants"):
                head = (f"<tr><th>{_e(_t(lang, 'variant'))}</th>"
                        f"<th>{_e(_t(lang, 'choice_rate'))}</th><th>SSR</th>"
                        f"<th>{_e(_t(lang, 'n_label'))}</th></tr>")
                body = "".join(
                    f"<tr><td>{_e(v.get('variant'))}</td>"
                    f"<td>{round((v.get('choice_rate') or 0) * 100)}%</td>"
                    f"<td>{v.get('ssr_mean') if v.get('ssr_mean') is not None else '—'}</td>"
                    f"<td>{v.get('n')}</td></tr>" for v in agg["variants"])
                rows.append(f"<table>{head}{body}</table>"
                            f'<p class="baseline-note">{_e(_t(lang, "baseline_label"))}: '
                            f"{_e(bval)} — {_e(bnote)}</p>")
            if agg.get("ranking"):
                head = (f"<tr><th>#</th><th>{_e(_t(lang, 'variant'))}</th>"
                        f"<th>{_e(_t(lang, 'share'))}</th></tr>")
                body = "".join(
                    f"<tr><td>{j + 1}.</td><td>{_e(v.get('variant'))}</td>"
                    f"<td>{round((v.get('share') or 0) * 100)}%</td></tr>"
                    for j, v in enumerate(agg["ranking"]))
                rows.append(f"<table>{head}{body}</table>"
                            f'<p class="baseline-note">{_e(_t(lang, "baseline_label"))}: '
                            f"{_e(bval)} — {_e(bnote)}</p>")
            if agg.get("unclear_top"):
                items = "".join(f"<li>{_e(u.get('kifejezes'))} ({u.get('n')})</li>"
                                for u in agg["unclear_top"])
                rows.append(f"<p>{_e(_t(lang, 'unclear_top'))}:</p><ul>{items}</ul>")
                if agg.get("clear_ratio") is not None:
                    rows.append(f'<p class="note">{round(agg["clear_ratio"] * 100)}% '
                                f"{_e(_t(lang, 'clear_ratio'))}</p>")
            status = _t(lang, "dim_measured")
        else:
            status = _t(lang, "dim_pending")
        rows_html = "".join(rows)
        parts.append(f"<h3>{_e(dim)} <span class=\"note\">({_e(status)})</span></h3>{rows_html}")
    return "".join(parts)


def _segments_body(agg: dict, lang: str) -> str:
    segs = agg.get("segments") or []
    if not segs:
        return f'<p class="note">{_e(_t(lang, "meth_na"))}</p>'
    head = (f"<tr><th>{_e(_t(lang, 'segment'))}</th><th>{_e(_t(lang, 'n_label'))}</th>"
            f"<th>{_e(_t(lang, 'score_label'))}</th><th>{_e(_t(lang, 'vs_baseline'))}</th></tr>")
    body = "".join(
        f"<tr><td>{_e(s.get('segment'))}</td><td>{s.get('n')}</td>"
        f"<td>{s.get('score')}</td>"
        f"<td>{'+' if (s.get('vs_baseline') or 0) >= 0 else ''}{s.get('vs_baseline')}</td></tr>"
        for s in segs)
    return (f"<table>{head}{body}</table>"
            f'<p class="baseline-note">{_e(_t(lang, "series_note"))}</p>')


def _timeseries_body(runs: list, lang: str) -> str:
    head = (f"<tr><th>{_e(_t(lang, 'run_at'))}</th><th>{_e(_t(lang, 'job_label'))}</th>"
            f"<th>{_e(_t(lang, 'overall_score'))}</th></tr>")
    body = "".join(
        f"<tr><td>{_e((r.get('run_at') or '')[:16].replace('T', ' '))}</td>"
        f"<td><code>{_e(r.get('job_id'))}</code></td>"
        f"<td>{r.get('overall_score') if r.get('overall_score') is not None else '—'}"
        f"{_scale_bar(r.get('overall_score'))}</td></tr>" for r in runs)
    return (f"<table>{head}{body}</table>"
            f'<p class="baseline-note">{_e(_t(lang, "series_note"))}</p>')


def _exploratory_body(brief: dict, agg: dict, lang: str) -> str:
    customs = (brief or {}).get("custom_questions") or []
    badge = f'<span class="badge">{_e(_t(lang, "exploratory_badge"))}</span>'
    items = "".join(f"<li>{_e(q.get('text'))} {badge}</li>" for q in customs)
    results = agg.get("custom_questions_results")
    res_html = ""
    if results:
        res_html = "".join(
            f"<h3>{_e(r.get('question'))}</h3><p>{_e(r.get('synthesis'))}</p>"
            for r in results if isinstance(r, dict))
    else:
        res_html = f'<p class="note">{_e(_t(lang, "exploratory_pending"))}</p>'
    return (f'<p class="note">{_e(_t(lang, "exploratory_note"))}</p>'
            f"<ul>{items}</ul>{res_html}")


def _methodology_body(data: dict, lang: str) -> str:
    agg = data["aggregate"]
    job = data["job"]
    panel = agg.get("panel") or {}
    country = panel.get("country") or (data.get("panel_spec") or {}).get("country", "?")
    quota = (QUOTA_SOURCES.get(country) or {}).get(lang) or _t(lang, "meth_na")
    halluguard = agg.get("halluguard")
    if halluguard:
        contamination = (f"{halluguard.get('n_suspect_texts', 0)}/"
                         f"{halluguard.get('n_texts', '?')} gyanús szöveg (heurisztika)"
                         if lang == "hu" else
                         f"{halluguard.get('n_suspect_texts', 0)}/"
                         f"{halluguard.get('n_texts', '?')} flagged texts (heuristic)")
    else:
        contamination = _t(lang, "contamination_none")
    rows = [
        (_t(lang, "meth_n"), f"{panel.get('n_requested', '?')} / {panel.get('n_completed', '?')}"),
        (_t(lang, "meth_quota"), quota),
        (_t(lang, "meth_panel_version"), panel.get("panel_version") or job.get("panel_version") or _t(lang, "meth_na")),
        (_t(lang, "meth_model"), panel.get("model_id") or job.get("model_id") or _t(lang, "meth_na")),
        (_t(lang, "meth_grounding"), _t(lang, "meth_grounding_val")),
        (_t(lang, "meth_corpus_hash"), panel.get("corpus_hash") or _t(lang, "meth_na")),
        (_t(lang, "meth_coverage"), job.get("coverage_score") if job.get("coverage_score") is not None else _t(lang, "meth_na")),
        (_t(lang, "meth_scope"), job.get("scope_verdict") or _t(lang, "meth_na")),
        (_t(lang, "meth_calibration"), _calibration_version()),
        (_t(lang, "meth_contamination"), contamination),
    ]
    dl = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in rows)
    return f'<dl class="meth">{dl}</dl>'


def render_html(data: dict, synth: dict, brand: dict, lang: str) -> str:
    """A teljes riport-HTML. Brand-agnosztikus (A1): név/lábléc/disclaimer/URL
    a brand-configból; minden link a public_base_url-ből (A4)."""
    agg = data["aggregate"]
    brief = data.get("brief") or {}
    base = str(brand["public_base_url"]).rstrip("/")
    sections = [
        _sec("exec-summary", _t(lang, "ch_exec"), f"<p>{_e(synth.get('summary'))}</p>"),
        _sec("dimensions", _t(lang, "ch_dimensions"), _dimensions_body(data, lang)),
        _sec("segments", _t(lang, "ch_segments"), _segments_body(agg, lang)),
    ]
    if data.get("runs"):
        sections.append(_sec("timeseries", _t(lang, "ch_timeseries"),
                             _timeseries_body(data["runs"], lang)))
    mood_html = (f"<p>{_e(synth.get('mood'))}</p>" if synth.get("mood") else "")
    sections.append(_sec("mood", _t(lang, "ch_mood"),
                         mood_html + f'<p class="note">{_e(_t(lang, "mood_note"))}</p>'))
    if brief.get("custom_questions"):
        sections.append(_sec("exploratory", _t(lang, "ch_exploratory"),
                             _exploratory_body(brief, agg, lang)))
    sections.append(_sec("methodology", _t(lang, "ch_methodology"),
                         _methodology_body(data, lang)))
    sections.append(_sec("scope", _t(lang, "ch_scope"),
                         f"<p>{_e(_t(lang, 'scope_measured'))}</p>"
                         f"<p>{_e(_t(lang, 'scope_not_measured'))}</p>"))
    sections.append(_sec("disclaimer", _t(lang, "ch_disclaimer"),
                         f"<p>{_e(brand['disclaimer_text'])}</p>"))
    goal = brief.get("goal") or ""
    meta_bits = [f"{_e(_t(lang, 'job_label'))}: <code>{_e(data['job_id'])}</code>",
                 f"{_e(_t(lang, 'generated_at'))}: "
                 f"{_e(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))}"]
    if goal:
        meta_bits.append(f"{_e(_t(lang, 'goal_label'))}: {_e(goal)}")
    ctx = {
        "lang": _e(lang),
        "title": _e(_t(lang, "report_title")),
        "css": _CSS,
        "brand_name": _e(brand["name"]),
        "meta_line": " · ".join(meta_bits),
        "sections": "".join(sections),
        "footer_text": _e(brand["footer_text"]),
        "verify_url": _e(f"{base}/api/delphoi/verify"),
        "verify_label": _e(_t(lang, "verify_label")),
        "product_url": _e(f"{base}/delphoi"),
        "product_label": _e(_t(lang, "product_link_label")),
    }
    return _render_shell(ctx)


# ---------------------------------------------------------------------------
# JSON + CSV aggregátum
# ---------------------------------------------------------------------------
def build_json_payload(data: dict, synth: dict, brand: dict, lang: str) -> dict:
    return {
        "job_id": data["job_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lang": lang,
        "brand": brand["name"],
        "brief": data.get("brief"),
        "aggregate": data["aggregate"],
        "synthesis": {"summary": synth.get("summary"), "mood": synth.get("mood"),
                      "model": synth.get("model"), "fallback": synth.get("fallback")},
        "timeseries": data.get("runs") or [],
        "disclaimer": brand["disclaimer_text"],
        "verify_url": f"{str(brand['public_base_url']).rstrip('/')}/api/delphoi/verify",
    }


def build_csv(data: dict, lang: str) -> str:
    """Lapos aggregátum-CSV: section,label,n,value,vs_baseline."""
    agg = data["aggregate"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "label", "n", "value", "vs_baseline"])
    if agg.get("overall_score") is not None:
        base = (agg.get("baseline") or {}).get("value", 3.0)
        w.writerow(["overall", _t(lang, "overall_score"), agg.get("n"),
                    agg["overall_score"], round(agg["overall_score"] - base, 3)])
    for s in agg.get("segments") or []:
        w.writerow(["segment", s.get("segment"), s.get("n"), s.get("score"),
                    s.get("vs_baseline")])
    for v in agg.get("variants") or []:
        w.writerow(["variant", v.get("variant"), v.get("n"), v.get("choice_rate"),
                    v.get("ssr_mean")])
    for r in agg.get("ranking") or []:
        w.writerow(["ranking", r.get("variant"), r.get("n"), r.get("share"),
                    r.get("vs_baseline")])
    for r in data.get("runs") or []:
        w.writerow(["timeseries", r.get("run_at"), "", r.get("overall_score"), ""])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# ARTEFAKT-ÍRÁS + delphoi_reports könyvelés + PDF (WeasyPrint | HTML-fallback)
# ---------------------------------------------------------------------------
def _record(get_db, job_id: str, fmt: str, path: str) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO delphoi_reports (job_id, format, path, created_at) VALUES (?,?,?,?)",
            (job_id, fmt, path, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()


def _write_pdf(html_str: str, pdf_path: str) -> bool:
    """WeasyPrint-render; ha nincs telepítve / rendszerfüggőség hiányzik →
    False (a hívó a HTML-t adja PDF-fallbackként)."""
    try:
        from weasyprint import HTML  # noqa: PLC0415
        HTML(string=html_str).write_pdf(pdf_path)
        return True
    except Exception as e:  # noqa: BLE001 — NOTSTROM: HTML a PDF-fallback
        logger.warning("delphoi_report PDF NOTSTROM (WeasyPrint): %s", e)
        return False


async def generate_report(deps: dict, job_id: str, user_id: str,
                          lang: str | None = None, formats: list | None = None,
                          brand_key: str | None = None, synth_fn=None,
                          out_dir: str | None = None) -> dict:
    """A riport-mű fő belépési pontja: adatgyűjtés → Hy3-szintézis → HTML →
    (pdf|json|csv) artefaktok + delphoi_reports sorok. A lang/formats default
    a brief report-blokkjából jön (annak hiányában hu + minden formátum)."""
    from plugins.delphoi_brands import get_brand

    get_db = deps["get_db"]
    data = collect_report_data(get_db, job_id, user_id)
    if not data.get("ok"):
        return data
    brief = data.get("brief") or {}
    rep_cfg = brief.get("report") or {}
    lang = (lang or rep_cfg.get("lang") or "hu").lower()
    if lang not in STRINGS:
        lang = "hu"
    if formats is None:   # explicit [] = csak HTML (a PDF/JSON/CSV kihagyva)
        formats = rep_cfg.get("formats") or list(REPORT_FORMATS)
    formats = [f for f in formats if f in REPORT_FORMATS]
    brand = get_brand(brand_key)

    synth = await synthesize(data["aggregate"], brief.get("goal") or "", lang,
                             deps, synth_fn=synth_fn)
    html_str = render_html(data, synth, brand, lang)

    d = out_dir or reports_dir()
    base = os.path.join(d, f"{job_id}.{lang}")
    artifacts: dict = {}
    html_path = f"{base}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_str)
    _record(get_db, job_id, "html", html_path)
    artifacts["html"] = html_path

    pdf_fallback = False
    if "pdf" in formats:
        pdf_path = f"{base}.pdf"
        if _write_pdf(html_str, pdf_path):
            _record(get_db, job_id, "pdf", pdf_path)
            artifacts["pdf"] = pdf_path
        else:
            # PDF-fallback: a HTML-artefakt szolgál ki pdf-igényt is
            _record(get_db, job_id, "pdf", html_path)
            artifacts["pdf"] = html_path
            pdf_fallback = True
    if "json" in formats:
        json_path = f"{base}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(build_json_payload(data, synth, brand, lang), f,
                      ensure_ascii=False, indent=1)
        _record(get_db, job_id, "json", json_path)
        artifacts["json"] = json_path
    if "csv" in formats:
        csv_path = f"{base}.csv"
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(build_csv(data, lang))
        _record(get_db, job_id, "csv", csv_path)
        artifacts["csv"] = csv_path

    logger.info("delphoi_report kész: %s (%s) — %s%s", job_id, lang,
                ",".join(artifacts), " [pdf-fallback=HTML]" if pdf_fallback else "")
    return {"ok": True, "job_id": job_id, "lang": lang, "artifacts": artifacts,
            "pdf_fallback": pdf_fallback,
            "synthesis_fallback": bool(synth.get("fallback"))}


def latest_artifact(get_db, job_id: str, user_id: str, fmt: str) -> dict:
    """A legfrissebb artefakt-út egy formátumhoz — CSAK a tulajdonosnak
    (a REST letöltés-végpont vékony rétege). fmt: html|pdf|json|csv."""
    if fmt not in _MIME:
        return {"ok": False, "error": "bad_format"}
    conn = get_db()
    try:
        job = conn.execute("SELECT user_id FROM delphoi_jobs WHERE id=?",
                           (job_id,)).fetchone()
        if not job or job["user_id"] != str(user_id):
            return {"ok": False, "error": "not_found"}
        row = conn.execute(
            "SELECT path, created_at FROM delphoi_reports WHERE job_id=? AND format=? "
            "ORDER BY id DESC LIMIT 1", (job_id, fmt)).fetchone()
    finally:
        conn.close()
    if not row or not os.path.exists(row["path"]):
        return {"ok": False, "error": "no_artifact"}
    mime = _MIME[fmt]
    if fmt == "pdf" and row["path"].endswith(".html"):
        mime = _MIME["html"]   # PDF-fallback: HTML-artefakt szolgál
    return {"ok": True, "path": row["path"], "mime": mime,
            "created_at": row["created_at"]}


def register_tools(app, deps):
    """Nem regisztrál MCP-toolt (tool-count fegyelem) — a riport-mű a REST-út
    (server.py-huzalozás deploykor) és a tesztek könyvtára."""
    global _DEPS
    _DEPS = deps
    logger.info("delphoi_report betöltve (model=%s, dir=%s)",
                SYNTH_MODEL, os.environ.get("DELPHOI_REPORTS_DIR", "<db>/reports"))
