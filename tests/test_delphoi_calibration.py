"""test_delphoi_calibration.py — PYTHIA P2 kalibrációs réteg.

A VALÓDI data/delphoi_calibration.json registry-n (G4) bizonyítja a
státusz-szemantikát: calibrated → a+b·raw; low_confidence → offset-only
fallback + warning; use_categorical_layer / insufficient_data → raw + explicit
meta; hiányzó cella → no_entry; anchor_pair → offset. Plusz: kompozit model_id
normalizálás, hiányzó registry-fájl viselkedés, verzió-címkék a metában.
"""
import json

import pytest

from plugins import delphoi_calibration as cal


@pytest.fixture(autouse=True)
def _real_registry(monkeypatch):
    """Minden teszt a repo VALÓDI registry-jét olvassa (env-override törölve)."""
    monkeypatch.delenv("DELPHOI_CALIBRATION_PATH", raising=False)
    yield


def test_registry_loads_with_version():
    reg = cal.load_registry()
    assert reg["version"] == "1.0"
    assert len(reg["entries"]) >= 10


def test_calibrated_affine_applied_hu_cci_hy3():
    # HU/cci/Hy3: a=-5.763, b=0.846 (G1-mátrix)
    calibrated, meta = cal.calibrate(-10.0, "HU", "cci", "pythia-cci-ssr-v1", "tencent/Hy3")
    assert meta["status"] == "calibrated" and meta["applied"] is True
    assert calibrated == pytest.approx(-5.763 + 0.846 * -10.0, abs=1e-3)
    assert meta["registry_version"] == "1.0"          # verzió-címke kötelező
    assert meta["panel_version"] == "pythia-cci-ssr-v1"
    assert meta["warning"] is None


def test_composite_model_id_normalized():
    """A ledger model_id kompozit ('tencent/Hy3|non-think|…') — a lookup a
    modellnév-prefixen történik."""
    composite = "tencent/Hy3|non-think|temp=0.8|ssr=linear|emb=x"
    calibrated, meta = cal.calibrate(0.0, "HU", "cci", "pythia-cci-ssr-v1", composite)
    assert meta["status"] == "calibrated" and meta["model"] == "tencent/Hy3"
    assert calibrated == pytest.approx(-5.763, abs=1e-3)


def test_low_confidence_uses_offset_only_fallback_fr():
    # FR/cci/Hy3: NEGATÍV meredekségű OLS → az ols_fallback_offset_only ág
    # (a=28.384, b=1) alkalmazandó, warning-metával.
    calibrated, meta = cal.calibrate(-49.0, "FR", "cci", "pythia-cci-ssr-v1", "tencent/Hy3")
    assert meta["status"] == "calibrated_low_confidence" and meta["applied"] is True
    assert meta["b"] == 1.0
    assert calibrated == pytest.approx(28.384 - 49.0, abs=1e-3)
    assert meta["warning"] and "offset" in meta["warning"]


def test_use_categorical_layer_returns_raw_with_explicit_meta():
    # CZ/cci/Hy3: szláv regiszter-hiba — kalibráció TILOS, raw megy tovább.
    calibrated, meta = cal.calibrate(12.1, "CZ", "cci", "pythia-cci-ssr-v1", "tencent/Hy3")
    assert meta["status"] == "use_categorical_layer" and meta["applied"] is False
    assert calibrated == pytest.approx(12.1, abs=1e-6)
    assert meta["warning"] and "kategorikus" in meta["warning"]
    assert meta["recommendation"] == "use_categorical_layer"


def test_insufficient_data_returns_raw_with_explicit_meta():
    # PT/cci/Hy3: n=1 pár — kalibráció nem végezhető.
    calibrated, meta = cal.calibrate(-11.4, "PT", "cci", "pythia-cci-ssr-v1", "tencent/Hy3")
    assert meta["status"] == "insufficient_data" and meta["applied"] is False
    assert calibrated == pytest.approx(-11.4, abs=1e-6)
    assert meta["warning"] and "kalibráció nem végezhető" in meta["warning"]


def test_anchor_pair_offset_applied_hu_agora():
    # HU/agora_appeal/Hy3: egyetlen horgonypár (Flash 28.4 <-> Hy3 43.6) →
    # a=-15.125, b=1 offset + warning a szórás-korrekció hiányáról.
    calibrated, meta = cal.calibrate(43.5625, "HU", "agora_appeal",
                                     "orakel-agora-hu-v2-hy3", "tencent/Hy3")
    assert meta["status"] == "anchor_pair" and meta["applied"] is True
    assert calibrated == pytest.approx(43.5625 - 15.125, abs=1e-3)
    assert meta["warning"] and "horgonypár" in meta["warning"]


def test_no_entry_passthrough():
    calibrated, meta = cal.calibrate(5.0, "HU", "regard", "pythia-cci-ssr-v1", "tencent/Hy3")
    assert meta["status"] == "no_entry" and meta["applied"] is False
    assert calibrated == pytest.approx(5.0, abs=1e-6)


def test_missing_registry_file_is_loud_but_nonfatal(monkeypatch, tmp_path):
    monkeypatch.setenv("DELPHOI_CALIBRATION_PATH", str(tmp_path / "nincs.json"))
    calibrated, meta = cal.calibrate(1.0, "HU", "cci", "pythia-cci-ssr-v1", "tencent/Hy3")
    assert meta["status"] == "registry_unavailable"
    assert calibrated == pytest.approx(1.0, abs=1e-6)
    assert meta["warning"]


def test_low_confidence_fallback_computed_from_pairs(monkeypatch, tmp_path):
    """Ha az ols_fallback_offset_only hiányzik, a párokból számolt offset
    (mean(gt)−mean(raw)) az út."""
    reg = {"_meta": {"version": "t"}, "entries": [{
        "country": "XX", "domain": "cci", "panel_version": "v", "model": "m",
        "status": "calibrated_low_confidence", "a": -1.0, "b": -0.5, "n": 3,
        "pairs": [[-10.0, -4.0], [-20.0, -14.0], [-30.0, -24.0]],
    }]}
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(reg), encoding="utf-8")
    monkeypatch.setenv("DELPHOI_CALIBRATION_PATH", str(p))
    calibrated, meta = cal.calibrate(-20.0, "XX", "cci", "v", "m")
    # mean(gt)-mean(raw) = (-14) - (-20) = +6 → calibrated = raw + 6
    assert meta["applied"] is True and meta["b"] == 1.0
    assert calibrated == pytest.approx(-14.0, abs=1e-6)
