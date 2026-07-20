"""plugins/delphoi_calibration.py — PYTHIA P2: kalibrációs réteg a DELPHOI fölé.

A data/delphoi_calibration.json registry-t (G4-ben feltöltve, séma a _schema
mezőben) olvassa és (country, domain, panel_version, model) kulcson affin
(a, b) kalibrációt ad a nyers panel-becslésre: calibrated = a + b·raw.

Státusz-szemantika (a registry _schema.status_values szerint):
  - "calibrated":              a + b·raw alkalmazva.
  - "calibrated_low_confidence": az OLS nem megbízható → az
        ols_fallback_offset_only ág (b=1, a = mean(gt)−mean(raw)) alkalmazva
        + WARNING a metában.
  - "anchor_pair":             egyetlen skála-horgonypár → csak offset (b=1)
        alkalmazva + warning (szórás-korrekció nélkül).
  - "use_categorical_layer":   kalibráció TILOS (szláv szabad-mondatos SSR
        regiszter-hiba, G1 #3) → raw megy tovább + explicit meta.
  - "insufficient_data":       n < 3 pár → raw megy tovább + explicit meta.
  - nincs bejegyzés:           raw megy tovább, status="no_entry".

A hívó felelőssége a helyes skála: a registry konvenciója szerint a raw
CCI-doménben panel-szaldó (−100..+100), agora/appeal-doménben 0–100 átlag.
A kimeneti payloadban MINDIG nyers ÉS kalibrált érték megy ki, verzió-címkével
(registry_version + panel_version) — a kalibráció sosem néma.
"""
from __future__ import annotations

import json
import logging
import os
import threading

logger = logging.getLogger("plugins.delphoi_calibration")

__plugin_meta__ = {
    "name": "delphoi_calibration",
    "version": "1.0.0",
    "description": "DELPHOI kalibracios reteg (P2) — registry-alapu affin kalibracio, explicit statuszokkal",
    "library": True,  # nincs sajat MCP-tool — a delphoi.py hasznalja
}

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REGISTRY_PATH = os.path.join(_REPO_ROOT, "data", "delphoi_calibration.json")

_lock = threading.Lock()
_cache: dict = {"path": None, "mtime": None, "registry": None}


def registry_path() -> str:
    return os.environ.get("DELPHOI_CALIBRATION_PATH", DEFAULT_REGISTRY_PATH)


def load_registry(path: str | None = None) -> dict:
    """A registry betöltése mtime-cache-sel. Hiányzó/hibás fájl → üres registry
    (a kalibráció ilyenkor mindenhol raw-passthrough, explicit metával)."""
    p = path or registry_path()
    with _lock:
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            return {"entries": [], "version": None, "error": f"registry nem olvasható: {p}"}
        if _cache["path"] == p and _cache["mtime"] == mtime and _cache["registry"] is not None:
            return _cache["registry"]
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:  # noqa: BLE001 — sérült registry: hangos log, üres registry
            logger.error("delphoi_calibration: registry-parse hiba (%s): %s", p, e)
            return {"entries": [], "version": None, "error": f"registry-parse hiba: {e}"}
        reg = {
            "entries": [e for e in (data.get("entries") or []) if isinstance(e, dict)],
            "version": (data.get("_meta") or {}).get("version"),
        }
        _cache.update({"path": p, "mtime": mtime, "registry": reg})
        return reg


def normalize_model(model_id) -> str:
    """A ledger/job model_id kompozit string ('tencent/Hy3|non-think|…') →
    a registry-ben használt modellnév (az első '|' előtti rész)."""
    return str(model_id or "").split("|", 1)[0].strip()


def find_entry(country: str, domain: str, panel_version: str, model_id) -> dict | None:
    model = normalize_model(model_id)
    for e in load_registry()["entries"]:
        if (str(e.get("country", "")).upper() == str(country or "").upper()
                and e.get("domain") == domain
                and e.get("panel_version") == panel_version
                and e.get("model") == model):
            return e
    return None


def _offset_only_fallback(entry: dict) -> tuple[float, float] | None:
    """Low-confidence ág: az előre számolt ols_fallback_offset_only, vagy ha
    hiányzik, a párokból számolt offset (b=1, a = mean(gt) − mean(raw))."""
    fb = entry.get("ols_fallback_offset_only")
    if isinstance(fb, dict) and fb.get("a") is not None:
        return float(fb["a"]), float(fb.get("b", 1.0))
    pairs = [p for p in (entry.get("pairs") or []) if isinstance(p, (list, tuple)) and len(p) == 2]
    if not pairs:
        return None
    mean_raw = sum(float(p[0]) for p in pairs) / len(pairs)
    mean_gt = sum(float(p[1]) for p in pairs) / len(pairs)
    return mean_gt - mean_raw, 1.0


def calibrate(raw, country: str, domain: str, panel_version: str, model_id) -> tuple[float | None, dict]:
    """(calibrated, meta). A calibrated a registry-státusz szerint áll elő; ahol
    kalibráció nem végezhető, calibrated == raw és a meta EXPLICIT mondja meg,
    miért (applied=False + status). A meta hordozza a verzió-címkéket."""
    reg = load_registry()
    meta: dict = {
        "country": str(country or "").upper(),
        "domain": domain,
        "panel_version": panel_version,
        "model": normalize_model(model_id),
        "registry_version": reg.get("version"),
        "status": "no_entry",
        "applied": False,
        "warning": None,
    }
    if raw is None:
        meta["status"] = "no_raw"
        return None, meta
    raw = float(raw)
    entry = find_entry(country, domain, panel_version, model_id)
    if entry is None:
        if reg.get("error"):
            meta["status"] = "registry_unavailable"
            meta["warning"] = reg["error"]
        return round(raw, 3), meta

    status = entry.get("status") or "no_entry"
    meta["status"] = status
    meta["n"] = entry.get("n")
    if entry.get("source"):
        meta["source"] = entry["source"]

    if status == "calibrated":
        a, b = float(entry["a"]), float(entry["b"])
        meta.update({"applied": True, "a": a, "b": b,
                     "r2": entry.get("r2"), "residual_sd": entry.get("residual_sd"),
                     "formula": f"calibrated = {a:+.3f} {b:+.3f}·raw"})
        return round(a + b * raw, 3), meta

    if status == "calibrated_low_confidence":
        fb = _offset_only_fallback(entry)
        if fb is None:
            meta["warning"] = "low-confidence cella fallback-adat nélkül — raw megy tovább"
            return round(raw, 3), meta
        a, b = fb
        meta.update({"applied": True, "a": round(a, 3), "b": b,
                     "formula": f"calibrated = {a:+.3f} + raw (offset-only)",
                     "warning": ("OLS nem megbízható (meredekség-előjel/n) — "
                                 "offset-only fallback alkalmazva (b=1, szint-horgony)")})
        return round(a + b * raw, 3), meta

    if status == "anchor_pair":
        a, b = float(entry["a"]), float(entry.get("b") or 1.0)
        meta.update({"applied": True, "a": a, "b": b,
                     "formula": f"calibrated = {a:+.3f} {b:+.3f}·raw",
                     "warning": ("egyetlen skála-horgonypár — csak szint-eltolás (b=1), "
                                 "szórás-korrekció NINCS alkalmazva")})
        return round(a + b * raw, 3), meta

    if status == "use_categorical_layer":
        meta["warning"] = ("kalibráció TILOS ezen a cellán: szláv szabad-mondatos "
                           "SSR regiszter-hiba (G1 #3) — a kategorikus elicitálási "
                           "réteg használandó; a nyers érték csak irány-jelzés")
        meta["recommendation"] = entry.get("recommendation") or "use_categorical_layer"
        return round(raw, 3), meta

    if status == "insufficient_data":
        meta["warning"] = (f"n={entry.get('n')} pár (<3) — kalibráció nem végezhető, "
                           "a nyers érték megy tovább")
        return round(raw, 3), meta

    # ismeretlen státusz — defenzív raw-passthrough, hangos log
    logger.warning("delphoi_calibration: ismeretlen registry-státusz: %r", status)
    meta["warning"] = f"ismeretlen registry-státusz: {status} — raw megy tovább"
    return round(raw, 3), meta
