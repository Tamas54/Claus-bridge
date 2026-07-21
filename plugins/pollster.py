"""ORAKEL-II Flash pollster — news-grounded synthetic party-preference panel.

Forecast mode (B): personas are defined by DEMOGRAPHICS ONLY (age, settlement,
education, media diet); party preference EMERGES from each persona's reaction to
the current news state (read from press_snapshots). Quota-sampled proportionally
to the HU adult population, so the raw share is implicitly post-stratified.

Cheap by design: SiliconFlow Non-Think motor ({"thinking":{"type":"disabled"}} —
feltétel nélkül) + short prompts + concurrency. PYTHIA P1 óta a default motor a
tencent/Hy3 (ORAKEL_MODEL env felülírhatja); Flash SEHOL nem default és nem
silent fallback — motorhiba = hangos hiba, nem modell-csere. Predictions are
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
import time
from collections import Counter, deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SF_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
SF_URL = "https://api.siliconflow.com/v1/chat/completions"
MODEL = os.environ.get("ORAKEL_MODEL", "tencent/Hy3")
CONCURRENCY = int(os.environ.get("ORAKEL_CONCURRENCY", "8"))

# Provider switch — default SiliconFlow (tencent/Hy3); set ORAKEL_PROVIDER=openai to
# backtest with a different model family (e.g. gpt-4o) for the prior diagnostic.
def _provider() -> tuple:
    """Returns (url, api_key, model, use_thinking_param)."""
    if os.environ.get("ORAKEL_PROVIDER", "siliconflow").lower() == "openai":
        return ("https://api.openai.com/v1/chat/completions",
                os.environ.get("OPENAI_API_KEY", ""),
                os.environ.get("ORAKEL_OPENAI_MODEL", "gpt-4o"),
                False)  # OpenAI has no "thinking" param
    return (SF_URL, SF_KEY, MODEL, True)


# ============================================================
# MOD1-C (KLARTEXT K1) — TPM-TUDATOS FAN-OUT
# ============================================================
# SiliconFlow L1-keret: 10 000 kérés/perc (RPM) és 400 000 token/perc (TPM).
# A pacer HÁROM féket kombinál:
#   1. PONTOS csúszóablakos kérés-büdzsé (RPM): bármely 60 mp-es ablakban a
#      kérésszám ≤ safety×RPM — nem token-bucket-közelítés, hanem esemény-napló;
#   2. ugyanilyen csúszóablakos token-büdzsé (TPM);
#   3. adaptív (AIMD) konkurencia: 429-re felezés, sikersorozatra +1.
# A garanciát a test_klartext_k1 szimulált órával bizonyítja (N=1000 × k=3).

def rate_limits() -> dict:
    """Env-vezérelt keret-konfig — a pacer ÉS a futásidő-becslő
    (delphoi.estimate_runtime_seconds) EGY forrásból olvas."""
    def _i(name: str, dflt: int) -> int:
        try:
            return max(1, int(os.environ.get(name, str(dflt))))
        except ValueError:
            return dflt
    try:
        safety = float(os.environ.get("DELPHOI_RATE_SAFETY", "0.8"))
    except ValueError:
        safety = 0.8
    return {
        "rpm": _i("DELPHOI_RPM_LIMIT", 10_000),
        "tpm": _i("DELPHOI_TPM_LIMIT", 400_000),
        "safety": min(1.0, max(0.1, safety)),
        "tokens_per_call": _i("DELPHOI_TOKENS_PER_CALL", 750),
        "latency_s": _i("DELPHOI_CALL_LATENCY_S", 8),
        "conc_start": _i("DELPHOI_CONCURRENCY", int(os.environ.get("ORAKEL_CONCURRENCY", "8") or 8)),
        "conc_min": _i("DELPHOI_CONCURRENCY_MIN", 2),
        "conc_max": _i("DELPHOI_CONCURRENCY_MAX", 32),
    }


class _SlidingBudget:
    """Pontos csúszóablakos büdzsé: az utolsó window_s mp-ben elköltött
    mennyiség sosem lépi túl a limitet (esemény-napló, nem közelítés)."""

    def __init__(self, limit: float, window_s: float = 60.0):
        self.limit = float(limit)
        self.window_s = float(window_s)
        self._events: deque = deque()      # (t, amount)
        self._sum = 0.0

    def _prune(self, now: float) -> None:
        while self._events and self._events[0][0] <= now - self.window_s:
            self._sum -= self._events.popleft()[1]

    def wait_time(self, now: float, amount: float) -> float:
        """0.0 = mehet most; különben ennyi mp múlva lesz elég hely."""
        self._prune(now)
        if self._sum + amount <= self.limit:
            return 0.0
        need = self._sum + amount - self.limit
        freed = 0.0
        for t, amt in self._events:
            freed += amt
            if freed >= need:
                return max(0.001, t + self.window_s - now)
        return self.window_s   # elméleti ág: amount önmagában > limit

    def add(self, now: float, amount: float) -> None:
        self._prune(now)
        self._events.append((now, amount))
        self._sum += amount


class RatePacer:
    """RPM+TPM csúszóablak + AIMD-konkurencia. Az óra és az altató
    injektálható (a teszt szimulált órával bizonyítja a keret-tartást)."""

    AIMD_SUCCESS_STEP = 20    # ennyi sikeres hívás után nő a konkurencia +1-gyel

    def __init__(self, rpm: float, tpm: float, conc_start: int = 8,
                 conc_min: int = 2, conc_max: int = 32, clock=None, sleep=None):
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._req = _SlidingBudget(rpm)
        self._tok = _SlidingBudget(tpm)
        self._conc_min = max(1, int(conc_min))
        self._conc_max = max(self._conc_min, int(conc_max))
        self.concurrency = min(self._conc_max, max(self._conc_min, int(conc_start)))
        self._active = 0
        self._successes = 0
        self._lock = asyncio.Lock()
        self.stats = {"acquired": 0, "throttled": 0, "rate_limited": 0}

    async def acquire(self, tokens: int) -> None:
        while True:
            async with self._lock:
                if self._active < self.concurrency:
                    now = self._clock()
                    wait = max(self._req.wait_time(now, 1),
                               self._tok.wait_time(now, float(tokens)))
                    if wait <= 0:
                        self._req.add(now, 1)
                        self._tok.add(now, float(tokens))
                        self._active += 1
                        self.stats["acquired"] += 1
                        return
                else:
                    wait = 0.05
            self.stats["throttled"] += 1
            await self._sleep(min(max(wait, 0.05), 2.0))

    def release(self, ok: bool = True) -> None:
        self._active = max(0, self._active - 1)
        if ok:
            self._successes += 1
            if self._successes >= self.AIMD_SUCCESS_STEP and self.concurrency < self._conc_max:
                self.concurrency += 1
                self._successes = 0

    def on_rate_limited(self) -> None:
        """429 a motortól → multiplikatív visszavágás (AIMD)."""
        self.stats["rate_limited"] += 1
        self._successes = 0
        self.concurrency = max(self._conc_min, self.concurrency // 2)

    def slot(self, tokens: int):
        """Async kontextus: acquire → hívás → release (kivételnél is)."""
        pacer = self

        class _Slot:
            async def __aenter__(self):
                await pacer.acquire(tokens)
                return pacer

            async def __aexit__(self, exc_type, exc, tb):
                pacer.release(ok=exc_type is None)
                return False

        return _Slot()


def make_pacer(clock=None, sleep=None) -> RatePacer:
    """Éles pacer az env-konfigból (safety-vel leszorított keretek)."""
    rl = rate_limits()
    return RatePacer(rpm=rl["rpm"] * rl["safety"], tpm=rl["tpm"] * rl["safety"],
                     conc_start=rl["conc_start"], conc_min=rl["conc_min"],
                     conc_max=rl["conc_max"], clock=clock, sleep=sleep)


# HU adult-population marginals, KSH-grounded (sampled independently as a v0
# simplification — joint correlations not yet modelled).
#   AGE: KSH mun0005 (15–74 népesség, 2025; 18+ buckets, 75+ estimated).
#   EDU: KSH mun0006 (legmagasabb iskolai végzettség, 2025).
#   SETTLEMENT: 2022 census settlement-type shares.
#   MEDIA: PERSISTENT ideological lean (NOT "kormányközeli" — that label goes
#          stale when the government changes; cf. the Echolot HU media-positioning
#          rule). Private outlets carry a stable left/right/neutral lean; public
#          media (közmédia) is always pro-government-of-the-day (lean follows
#          whoever governs — now TISZA). Weights: NMHH 2026-05 online audience
#          (liberal Telex/24.hu/HVG > right Origo/Mandiner) + TV reach adjustment.
AGE = [("18-29", 0.157), ("30-39", 0.160), ("40-49", 0.190), ("50-59", 0.184), ("60+", 0.309)]
SETTLEMENT = [("Budapest", 0.18), ("megyeszékhely", 0.19), ("város", 0.31), ("község", 0.32)]
EDU = [("max 8 általános", 0.187), ("szakmunkás", 0.161), ("érettségi", 0.350), ("diploma", 0.302)]
# Media-environment descriptions grounded in the CURRENT (2026) reality, not the
# model's stale ~2023 prior. The two channels that FLIPPED need explicit framing:
#   közmédia → now backs the governing Tisza (was Orbán-aligned for 15y);
#   közösségi → political reach is Tisza-dominated (Telex 2026-02 & 2026-06 data).
# baloldali/jobboldali keep their stable lean (model handles those correctly).
MEDIA = [("baloldali/liberális médiát követ (Telex, 24.hu, HVG, 444, RTL)", 0.26),
         ("jobboldali médiát követ (Origo, Mandiner, Magyar Nemzet)", 0.22),
         ("közmédiát követ (köztévé — amely jelenleg a kormányon lévő Tisza-kormányt támogatja)", 0.18),
         ("közösségi médiában fogyaszt politikai tartalmat (Facebook/TikTok/YouTube — itt jelenleg túlnyomórészt Tisza-közeli tartalom éri el a legtöbb embert)", 0.22),
         ("alig követi a politikai híreket", 0.12)]

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


async def _chat(client, prompt: str, max_tokens: int = 260, temperature: float = 0.8,
                retries: int = 4, model: str | None = None, pacer=None) -> str:
    """One Non-Think call over a SHARED client, with exponential backoff —
    the proven teszterek/ pattern. Retries on exception AND empty content (a motor
    rate-limit alatt néha üreset ad). A `model` paraméter felülírja a modellnevet;
    None esetén a _provider() default-ja az útvonal. NINCS silent fallback:
    kimerült retry = hangos RuntimeError (exception + log), NEM modell-csere.
    pacer: opcionális RatePacer — 429-nél on_rate_limited() (AIMD-visszavágás);
    a slot-kezelés (acquire/release) a HÍVÓ dolga, a _chat csak jelez."""
    url, _key, provider_model, use_think = _provider()
    model = model or provider_model
    body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if model.startswith("gpt-5") and "chat" not in model:
        # gpt-5 reasoning models: max_completion_tokens, default temperature only,
        # reasoning burns tokens → give headroom + minimal effort for a short answer.
        body["max_completion_tokens"] = max(max_tokens, 2000)
        body["reasoning_effort"] = "low"  # 'minimal' rejected by gpt-5.4-mini; low = cheap
    else:
        body["temperature"] = temperature
        body["max_tokens"] = max_tokens
        if use_think:
            # Non-Think — FELTÉTEL NÉLKÜL a SiliconFlow-úton (Hy3 default-thinking
            # modell, a thinking:disabled kötelező — NULLTARIF-recept).
            body["thinking"] = {"type": "disabled"}
    for attempt in range(retries):
        try:
            r = await client.post(url, json=body)
            # 429 → AIMD-jelzés a pacernek (getattr: a teszt-fake kliensek
            # válaszán nincs status_code — az nem hiba)
            if pacer is not None and getattr(r, "status_code", None) == 429:
                pacer.on_rate_limited()
            r.raise_for_status()
            content = (r.json()["choices"][0]["message"].get("content") or "").strip()
            if content:
                return content
        except Exception:  # noqa: BLE001 — transient; back off and retry
            pass
        if attempt < retries - 1:
            await asyncio.sleep(2 ** attempt + random.random())
    # HANGOS motorhiba — semmilyen modell-csere/fallback nem történik.
    logger.error("orakel motor (%s) failed after %d retries (empty/transient)", model, retries)
    raise RuntimeError(f"orakel motor ({model}) failed after retries (empty/transient)")


async def run_poll(n: int = 60, seed: int = 42, store: bool = True, priming: str = "") -> dict:
    """Run the synthetic panel against the latest news context. Returns shares + meta."""
    import httpx
    ctx, date = latest_context()
    personas = generate_personas(n, seed)
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {_provider()[1]}"}, timeout=90) as client:

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
        _model = _provider()[2]
        insert_poll(
            "prediction", f"orakel-{period_date}", f"ORAKEL-{_model.split('/')[-1]}", period_date,
            {k: shares[k] for k in ("fidesz", "tisza", "dk", "mihazank", "bizonytalan")},
            base="teljes_nepesseg", source=f"orakel model={_model} n={len(results)} seed={seed}",
        )
        # nearest ground-truth quarter for a quick sanity delta (optional)
        gt_q = "2025-Q3"
        gt = aggregate(gt_q)
        if gt.get("mean"):
            out["ground_truth_ref"] = {"period": gt_q, "mean": gt["mean"]}
    logger.info("orakel poll %s: n=%d shares=%s", period_date, len(results), shares)
    return out


# ============================================================
# FRAMING RADAR — the validated relative instrument
# ============================================================
# Each recent news story is a stimulus; personas react between-subject
# (interest 1-5 + would-share = viral-box). Output: which story resonates /
# would spread, and with which segments. Generalizes teszterek/ma_mi_nyerne.

def collect_stimuli(days: int = 3, max_items: int = 12) -> list[dict]:
    """Distinct recent news stories (title+summary) from press_snapshots briefs."""
    from pyramid.memory_rag import _get_db
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT date_iso, content FROM press_snapshots WHERE signal_type='brief' "
            "ORDER BY date_iso DESC LIMIT ?", (days,)).fetchall()
    finally:
        conn.close()
    seen, out = set(), []
    for r in rows:
        b = json.loads(r["content"])
        for t in (b.get("topics") or []) + (b.get("local_topics") or []):
            title = (t.get("title") or "").strip()
            key = title.lower()[:40]
            if title and key not in seen:
                seen.add(key)
                out.append({"title": title, "summary": (t.get("summary") or "")[:220], "date": r["date_iso"]})
    return out[:max_items]


def _radar_prompt(p: dict, s: dict, priming: str = "") -> str:
    pri = (priming + "\n\n") if priming else ""
    return (
        f"Te egy magyar állampolgár vagy: {p['age']}, lakóhely: {p['settlement']}, "
        f"iskolázottság: {p['edu']}, {p['media']}.\n\n{pri}"
        f"Megjelent ez a hír:\n„{s['title']}” — {s['summary']}\n\n"
        "Őszintén, a saját szemszögedből: mennyire érdekel ez a hír, és megosztanád "
        "vagy beszélnél-e róla másokkal?\n\n"
        "Válaszolj PONTOSAN így:\nÉRDEKLŐDÉS: <1-5>\nMEGOSZTÁS: <igen/nem>\nINDOK: <egy rövid mondat>"
    )


def _parse_radar(text: str):
    import re
    mi = re.search(r"[ÉE]RDEKL[ŐO]D[ÉE]S:\s*([1-5])", text or "", re.I)
    ms = re.search(r"MEGOSZT[ÁA]S:\s*(igen|nem)", text or "", re.I)
    interest = int(mi.group(1)) if mi else None
    share = (ms.group(1).lower() == "igen") if ms else False
    return interest, share


def _lean_bucket(media: str) -> str:
    m = media.lower()
    if "baloldali" in m or "liberális" in m:
        return "baloldali"
    if "jobboldali" in m:
        return "jobboldali"
    if "közméd" in m or "köztévé" in m:  # "közmédiát követ (köztévé …)"
        return "közmédia"
    if "közösségi" in m or "facebook" in m or "tiktok" in m:
        return "közösségi"
    return "alig/egyéb"


async def run_framing_radar(n_per_cell: int = 6, days: int = 3, seed: int = 42, priming: str = "") -> dict:
    """Between-subject framing radar over the recent news stories. Returns a
    viral-box-ranked list of stories with mean interest and per-lean breakdown."""
    import httpx
    from collections import defaultdict
    stimuli = collect_stimuli(days=days)
    if not stimuli:
        return {"error": "no stimuli (press_snapshots empty)"}
    personas = generate_personas(n_per_cell * len(stimuli), seed)
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {_provider()[1]}"}, timeout=90) as client:
        async def _one(i, p):
            s = stimuli[i % len(stimuli)]
            async with sem:
                try:
                    interest, share = _parse_radar(await _chat(client, _radar_prompt(p, s, priming)))
                    if interest is None:
                        return None
                    return {"story": s["title"], "lean": _lean_bucket(p["media"]),
                            "interest": interest, "share": share}
                except Exception as e:  # noqa: BLE001
                    logger.warning("radar persona %s failed: %s", p["id"], e)
                    return None
        recs = [r for r in await asyncio.gather(*[_one(i, p) for i, p in enumerate(personas)]) if r]

    agg = defaultdict(lambda: {"n": 0, "interest": [], "share": 0, "lean": defaultdict(lambda: [0, 0])})
    for r in recs:
        a = agg[r["story"]]
        a["n"] += 1
        a["interest"].append(r["interest"])
        a["share"] += 1 if r["share"] else 0
        lb = a["lean"][r["lean"]]
        lb[0] += 1
        lb[1] += 1 if r["share"] else 0

    ranking = []
    for story, a in agg.items():
        ranking.append({
            "story": story, "n": a["n"],
            "interest": round(sum(a["interest"]) / len(a["interest"]), 2),
            "viral_box": round(a["share"] / a["n"] * 100, 1),
            "by_lean": {k: round(v[1] / v[0] * 100) for k, v in a["lean"].items() if v[0]},
        })
    ranking.sort(key=lambda x: x["viral_box"], reverse=True)
    logger.info("framing radar: %d stories, %d responses", len(ranking), len(recs))
    return {"days": days, "stimuli": len(stimuli), "responses": len(recs), "ranking": ranking}


# ============================================================
# DIRECTION / DELTA RADAR — nowcasting the swing, not the level
# ============================================================
# Leverages what LLMs CAN do (directional reaction) vs what they can't (absolute
# landslide level). Each persona reads the current news and says which big party
# it currently favors them more — aggregated into a net swing direction + strength.

def _direction_prompt(p: dict, ctx: str) -> str:
    return (
        f"Te magyar választópolgár vagy: {p['age']}, lakóhely: {p['settlement']}, "
        f"iskolázottság: {p['edu']}, {p['media']}.\n\n"
        "A magyar politika két fő ereje jelenleg a TISZA (Magyar Péter) és a "
        "Fidesz-KDNP (Orbán Viktor).\n\n"
        f"A mostani hírhelyzet:\n{ctx or '(nincs friss hír)'}\n\n"
        "ÖSSZESSÉGÉBEN ezek a hírek melyik párt megítélésének kedveznek NÁLAD "
        "inkább — vagyis merre MOZDÍTANAK téged?\n\n"
        "Válaszolj PONTOSAN így:\nIRÁNY: <Tisza / Fidesz / egyik sem>\n"
        "ERŐSSÉG: <1-3>\nINDOK: <egy rövid mondat>"
    )


def _parse_direction(text: str):
    import re
    di = re.search(r"IR[ÁA]NY:\s*(tisza|fidesz|egyik)", text or "", re.I)
    st = re.search(r"ER[ŐO]SS[ÉE]G:\s*([1-3])", text or "", re.I)
    direction = di.group(1).lower() if di else None
    if direction == "egyik":
        direction = "egyik sem"
    strength = int(st.group(1)) if st else 1
    return direction, strength


async def run_direction_radar(n: int = 60, seed: int = 42) -> dict:
    """Nowcast the swing DIRECTION from current news. Returns net swing
    (Tisza-ward positive) + per-lean breakdown. NO priming — the news drives it."""
    import httpx
    from collections import defaultdict
    ctx, date = latest_context()
    personas = generate_personas(n, seed)
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {_provider()[1]}"}, timeout=90) as client:
        async def _one(p):
            async with sem:
                try:
                    d, s = _parse_direction(await _chat(client, _direction_prompt(p, ctx)))
                    if d is None:
                        return None
                    return {"dir": d, "strength": s, "lean": _lean_bucket(p["media"])}
                except Exception as e:  # noqa: BLE001
                    logger.warning("direction persona %s failed: %s", p["id"], e)
                    return None
        recs = [r for r in await asyncio.gather(*[_one(p) for p in personas]) if r]

    if not recs:
        return {"error": "no responses"}
    tisza = sum(r["strength"] for r in recs if r["dir"] == "tisza")
    fidesz = sum(r["strength"] for r in recs if r["dir"] == "fidesz")
    n_t = sum(1 for r in recs if r["dir"] == "tisza")
    n_f = sum(1 for r in recs if r["dir"] == "fidesz")
    n_x = sum(1 for r in recs if r["dir"] == "egyik sem")
    net = round((tisza - fidesz) / len(recs), 2)  # strength-weighted, Tisza-ward +
    lean = defaultdict(lambda: [0, 0, 0])  # [tisza, fidesz, n]
    for r in recs:
        lb = lean[r["lean"]]
        lb[2] += 1
        if r["dir"] == "tisza":
            lb[0] += 1
        elif r["dir"] == "fidesz":
            lb[1] += 1
    by_lean = {k: f"Tisza {v[0]}/{v[2]} · Fidesz {v[1]}/{v[2]}" for k, v in lean.items()}
    return {"date": date, "n": len(recs),
            "tisza_ward": n_t, "fidesz_ward": n_f, "neither": n_x,
            "net_strength": net, "by_lean": by_lean}
