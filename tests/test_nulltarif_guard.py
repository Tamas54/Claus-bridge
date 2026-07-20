"""test_nulltarif_guard.py — a NULLTARIF-őr (PYTHIA P1).

Szimulált riasztás: a SiliconFlow /models lekérés ÉS a Telegram-küldés mockolva.
Riasztás-feltétel: a tencent/Hy3 hiányzik VAGY az ára > 0. Zöld út: létezik és
$0 (vagy nincs ár-mező → létezés-őrzés). Az őr SOSEM vált modellt.
"""
import asyncio

from plugins import nulltarif_guard as ng


def _models(*entries):
    return list(entries)


def test_evaluate_green_free_model():
    rep = ng.evaluate(_models({"id": "tencent/Hy3", "price": "0"}))
    assert rep["exists"] is True
    assert rep["alert"] is False
    assert rep["price"] == 0.0
    assert rep["price_known"] is True


def test_evaluate_green_no_price_field():
    rep = ng.evaluate(_models({"id": "tencent/Hy3", "object": "model"}))
    assert rep["exists"] is True
    assert rep["alert"] is False
    assert rep["price_known"] is False


def test_evaluate_alert_on_price():
    rep = ng.evaluate(_models({"id": "tencent/Hy3", "pricing": {"input_price": "0.35"}}))
    assert rep["alert"] is True
    assert rep["price"] == 0.35
    assert "NEM $0" in rep["reason"]


def test_evaluate_alert_on_missing_model():
    rep = ng.evaluate(_models({"id": "deepseek-ai/DeepSeek-V4-Flash", "price": 0.1}))
    assert rep["exists"] is False
    assert rep["alert"] is True
    assert "ELTŰNT" in rep["reason"]


def _run_daily(models=None, raise_fetch=False):
    sent = []

    async def fake_fetch():
        if raise_fetch:
            raise RuntimeError("boom")
        return models

    async def fake_send(text):
        sent.append(text)
        return True

    report = asyncio.run(ng.daily_check(send_fn=fake_send, fetch_fn=fake_fetch))
    return report, sent


def test_daily_check_green_sends_nothing():
    report, sent = _run_daily([{"id": "tencent/Hy3", "price": 0}])
    assert report["alert"] is False
    assert sent == []
    assert "checked_at" in report


def test_daily_check_alert_sends_telegram():
    report, sent = _run_daily([{"id": "tencent/Hy3", "price": "0.5"}])
    assert report["alert"] is True
    assert report["alert_sent"] is True
    assert len(sent) == 1
    assert "NULLTARIF" in sent[0]
    assert "tencent/Hy3" in sent[0]
    # az őr NEM vált modellt — a döntést emberre bízza
    assert "NEM vált automatikusan" in sent[0]


def test_daily_check_fetch_error_is_loud_alert():
    report, sent = _run_daily(raise_fetch=True)
    assert report["alert"] is True
    assert "lekérés hiba" in report["reason"]
    assert len(sent) == 1


def test_seed_cron_idempotent(get_db):
    conn = get_db()
    try:
        assert ng.seed_cron(conn) is True
        assert ng.seed_cron(conn) is False   # második futás no-op
        row = conn.execute("SELECT cron_schedule, cron_enabled FROM pyramid_recipes WHERE name=?",
                           (ng.CRON_RECIPE_NAME,)).fetchone()
        assert row is not None
        assert row["cron_schedule"] == ng.CRON_SCHEDULE
    finally:
        conn.close()


def test_register_tools_adds_no_tools(get_db):
    class FakeApp:
        def __init__(self):
            self.tools = []
        def tool(self):
            def deco(fn):
                self.tools.append(fn.__name__)
                return fn
            return deco
    app = FakeApp()
    ng.register_tools(app, {"get_db": get_db})
    assert app.tools == []   # tool-count fegyelem: az őr nem MCP-tool
