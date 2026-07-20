"""test_delphoi_brands.py — DELPHOI brand-registry (A1-váz, MODIFIKATION 2).

Vasszabály-tesztek: default 'echolot' a mai értékekkel; env-default felülírás;
ismeretlen brand = hangos hiba (nincs csendes fallback); a visszaadott config
másolat (a registry nem mutálható kívülről); minden kötelező mező jelen.
"""
import pytest

from plugins import delphoi_brands as db


def test_default_brand_is_echolot(monkeypatch):
    monkeypatch.delenv("DELPHOI_DEFAULT_BRAND", raising=False)
    b = db.get_brand()
    assert b["brand_key"] == "echolot"
    assert b["name"] == "Echolot"


def test_echolot_carries_todays_values():
    b = db.get_brand("echolot")
    assert "nem közvélemény-kutatás" in b["disclaimer_text"]  # terminológia-vasszabály
    assert "szintetikus fókuszcsoport" in b["footer_text"]
    assert b["public_base_url"].startswith("http")
    assert not b["public_base_url"].endswith("/")


def test_all_required_fields_present():
    b = db.get_brand("echolot")
    for f in db._REQUIRED_FIELDS + ("brand_key",):
        assert f in b


def test_env_default_override(monkeypatch):
    monkeypatch.setitem(db.BRANDS, "aipolling", {
        "name": "aipolling.io", "logo_path": "", "footer_text": "f",
        "disclaimer_text": "d", "public_base_url": "https://aipolling.io"})
    monkeypatch.setenv("DELPHOI_DEFAULT_BRAND", "aipolling")
    assert db.get_brand()["brand_key"] == "aipolling"


def test_unknown_brand_is_loud():
    with pytest.raises(ValueError, match="ismeretlen DELPHOI brand"):
        db.get_brand("nincs-ilyen")


def test_returned_config_is_a_copy():
    b = db.get_brand("echolot")
    b["name"] = "Elrontott"
    assert db.get_brand("echolot")["name"] == "Echolot"


def test_new_brand_is_config_not_code(monkeypatch):
    """Új brand felvétele = egy registry-sor — a get_brand azonnal kiszolgálja."""
    monkeypatch.setitem(db.BRANDS, "teszt-arc", {
        "name": "Teszt Arc", "logo_path": "/static/t.svg", "footer_text": "t",
        "disclaimer_text": "t", "public_base_url": "https://t.example"})
    assert db.get_brand("teszt-arc")["name"] == "Teszt Arc"


def test_register_tools_adds_no_tools():
    class FakeApp:
        def __init__(self):
            self.tools = []
        def tool(self):
            def deco(fn):
                self.tools.append(fn.__name__)
                return fn
            return deco
    app = FakeApp()
    db.register_tools(app, {})
    assert app.tools == []
