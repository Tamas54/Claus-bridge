"""ORAKEL-II Flash pollster — news-grounded synthetic party-preference panel.

Forecast mode (B): personas are defined by DEMOGRAPHICS ONLY (age, settlement,
education, media diet); party preference EMERGES from each persona's reaction to
the current news state (read from press_snapshots). Quota-sampled proportionally
to the HU adult population, so the raw share is implicitly post-stratified.

Cheap by design: DeepSeek-V4-Flash with Non-Think ({"thinking":{"type":"disabled"}},
see [[siliconflow_flash_nonthink]]) + short prompts + concurrency. Predictions are
written to poll_results (kind='prediction') — OUTSIDE the unified agent RAG.

This is the relative/ordinal instrument from the ORAKEL design; absolute calibration
comes from the backtest (IV) against partpreferencia.hu ground truth.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from collections import Counter
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SF_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
SF_URL = "https://api.siliconflow.com/v1/chat/completions"
MODEL = os.environ.get("ORAKEL_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
CONCURRENCY = int(os.environ.get("ORAKEL_CONCURRENCY", "8"))

# HU adult-population marginals, KSH-grounded (sampled independently as a v0
# simplification — joint correlations not yet modelled).
#   AGE: KSH mun0005 (15–74 népesség, 2025; 18+ buckets, 75+ estimated).
#   EDU: KSH mun0006 (legmagasabb iskolai végzettség, 2025).
#   SETTLEMENT: 2022 census settlement-type shares.
#   MEDIA: NMHH 2026-05 online audience (Telex/24.hu/HVG independent > Index/Origo
#          pro-gov) + TV adjustment (public TV skews pro-gov/older, not in online data).
AGE = [("18-29", 0.157), ("30-39", 0.160), ("40-49", 0.190), ("50-59", 0.184), ("60+", 0.309)]
SETTLEMENT = [("Budapest", 0.18), ("megyeszékhely", 0.19), ("város", 0.31), ("község", 0.32)]
EDU = [("max 8 általános", 0.187), ("szakmunkás", 0.161), ("érettségi", 0.350), ("diploma", 0.302)]
MEDIA = [("kormányközeli médiát követ (köztévé, Origo, Index)", 0.32),
         ("független/ellenzéki online médiát követ (Telex, 24.hu, HVG, 444, RTL)", 0.26),
         ("Facebook-vegyes hírfogyasztó", 0.22),
         ("alig követi a hírt", 0.15),
         ("változatos forrásokból tájékozódik", 0.05)]

_OUT_KEYS = ["fidesz", "tisza", "dk", "mihazank", "mkkp", "egyeb", "bizonytalan"]


def _pick(rng: random.Random, dist):
    r = rng.random()
    acc = 0.0
    for label, w in dist:
        acc += w
        if r <= acc:
            return label
    return dist[-1][0]


def generate_personas(n: int = 60, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    return [{"id": i, "age": _pick(rng, AGE), "settlement": _pick(rng, SETTLEMENT),
             "edu": _pick(rng, EDU), "media": _pick(rng, MEDIA)} for i in range(n)]


def latest_context(max_topics: int = 6) -> tuple[str, str | None]:
    """Build a compact news-context block from the freshest press_snapshots."""
    from pyramid.memory_rag import _get_db
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT date_iso FROM press_snapshots WHERE signal_type='brief' ORDER BY date_iso DESC LIMIT 1"
        ).fetchone()
        if not row:
            return "", None
        d = row["date_iso"]
        briefr = conn.execute(
            "SELECT content FROM press_snapshots WHERE date_iso=? AND signal_type='brief' LIMIT 1", (d,)).fetchone()
        trr = conn.execute(
            "SELECT content FROM press_snapshots WHERE date_iso=? AND signal_type='trending' LIMIT 1", (d,)).fetchone()
    finally:
        conn.close()

    parts = []
    if briefr:
        b = json.loads(briefr["content"])
        if b.get("lead"):
            parts.append("VILÁG: " + b["lead"])
        for t in (b.get("topics") or [])[:max_topics]:
            parts.append(f"- {t.get('title','')}: {(t.get('summary') or '')[:140]}")
        if b.get("local_title"):
            parts.append("ITTHON: " + b["local_title"])
        for t in (b.get("local_topics") or [])[:max_topics]:
            parts.append(f"- {t.get('title','')}: {(t.get('summary') or '')[:140]}")
    if trr:
        tr = json.loads(trr["content"])
        kws = [t.get("keyword") for t in (tr.get("trending") or [])[:8] if t.get("keyword")]
        if kws:
            parts.append("Felkapott témák: " + ", ".join(kws))
    return "\n".join(parts), d


PRIMING_2026 = (
    "AKTUÁLIS POLITIKAI HELYZET (2026): A 2026. áprilisi országgyűlési választást "
    "a TISZA Párt (Magyar Péter) nyerte, jelenleg ők kormányoznak. A Fidesz-KDNP "
    "(Orbán Viktor) ellenzékbe került 16 év kormányzás után. A parlamentbe bejutott "
    "a Mi Hazánk és a DK is. A két legnagyobb erő jelenleg a TISZA és a Fidesz."
)


def _persona_prompt(p: dict, ctx: str, priming: str = "") -> str:
    pri = (priming + "\n\n") if priming else ""
    return (
        f"Te egy magyar választópolgár vagy. A profilod: {p['age']} éves, lakóhely: "
        f"{p['settlement']}, iskolázottság: {p['edu']}, {p['media']}.\n\n"
        f"{pri}"
        f"A mostani hírhelyzet Magyarországon és a világban:\n{ctx or '(nincs friss hír)'}\n\n"
        "Ha most vasárnap országgyűlési választás lenne, melyik pártra szavaznál? "
        "VÁLASSZ PONTOSAN EGYET: Fidesz, Tisza, DK, Mi Hazánk, MKKP, egyéb, bizonytalan.\n\n"
        "Válaszolj KIZÁRÓLAG ebben a formátumban:\nPÁRT: <egy a felsoroltakból>\nINDOK: <egy rövid mondat>"
    )


def _parse_party(text: str) -> str:
    import re
    m = re.search(r"P[ÁA]RT:\s*([^\n]+)", text or "", re.I)
    raw = (m.group(1) if m else (text or "")).lower()
    # order matters: check multi-word / specific before generic
    for needle, key in [("mi hazánk", "mihazank"), ("mihazánk", "mihazank"), ("mi hazank", "mihazank"),
                        ("fidesz", "fidesz"), ("tisza", "tisza"), ("mkkp", "mkkp"),
                        ("dk", "dk"), ("bizonytal", "bizonytalan"), ("egyéb", "egyeb"), ("egyeb", "egyeb")]:
        if needle in raw:
            return key
    return "bizonytalan"


async def _chat(client, prompt: str, max_tokens: int = 260, temperature: float = 0.8, retries: int = 4) -> str:
    """One Flash call (Non-Think) over a SHARED client, with exponential backoff —
    the proven teszterek/ pattern. Retries on exception AND empty content (flash
    occasionally returns empty under rate-limit). See [[siliconflow_flash_nonthink]]."""
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},  # Non-Think — V4 form
    }
    for attempt in range(retries):
        try:
            r = await client.post(SF_URL, json=body)
            r.raise_for_status()
            content = (r.json()["choices"][0]["message"].get("content") or "").strip()
            if content:
                return content
        except Exception:  # noqa: BLE001 — transient; back off and retry
            pass
        if attempt < retries - 1:
            await asyncio.sleep(2 ** attempt + random.random())
    raise RuntimeError("flash failed after retries (empty/transient)")


async def run_poll(n: int = 60, seed: int = 42, store: bool = True, priming: str = "") -> dict:
    """Run the synthetic panel against the latest news context. Returns shares + meta."""
    import httpx
    ctx, date = latest_context()
    personas = generate_personas(n, seed)
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {SF_KEY}"}, timeout=90) as client:

        async def _one(p):
            async with sem:
                try:
                    return _parse_party(await _chat(client, _persona_prompt(p, ctx, priming)))
                except Exception as e:  # noqa: BLE001
                    logger.warning("orakel persona %s failed: %s", p["id"], e)
                    return None

        results = [r for r in await asyncio.gather(*[_one(p) for p in personas]) if r]
    total = len(results) or 1
    counts = Counter(results)
    shares = {k: round(counts.get(k, 0) / total * 100, 1) for k in _OUT_KEYS}

    period_date = date or datetime.now(timezone.utc).date().isoformat()
    out = {"date": period_date, "n": len(results), "requested": n, "seed": seed, "shares": shares}

    if store and len(results):
        from plugins.poll_results import insert_poll, aggregate
        insert_poll(
            "prediction", f"orakel-{period_date}", "ORAKEL-Flash", period_date,
            {k: shares[k] for k in ("fidesz", "tisza", "dk", "mihazank", "bizonytalan")},
            base="teljes_nepesseg", source=f"orakel-flash n={len(results)} seed={seed}",
        )
        # nearest ground-truth quarter for a quick sanity delta (optional)
        gt_q = "2025-Q3"
        gt = aggregate(gt_q)
        if gt.get("mean"):
            out["ground_truth_ref"] = {"period": gt_q, "mean": gt["mean"]}
    logger.info("orakel poll %s: n=%d shares=%s", period_date, len(results), shares)
    return out
