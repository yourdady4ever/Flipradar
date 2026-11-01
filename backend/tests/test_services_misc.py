"""FX, import costs, scheduler and push notification channels."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import select

from flipradar.finance.import_costs import estimate_import_costs, select_rule
from flipradar.models import FxRate, Notification, TrackedSearch
from flipradar.services import fx, notify, scheduler
from tests.conftest import make_settings

# ------------------------------------------------------------------------------------------------ FX


def test_latest_rate_prefers_newest_stored(db, settings):
    assert fx.latest_usd_mxn(db, settings) is None
    fx.store_rate(db, "USD", "MXN", "2026-10-29", Decimal("17.90"), "banxico")
    fx.store_rate(db, "USD", "MXN", "2026-10-30", Decimal("18.05"), "banxico")
    fx.store_rate(db, "USD", "MXN", "2026-10-30", Decimal("18.06"), "banxico")  # upsert same day
    assert db.query(FxRate).count() == 2
    assert fx.latest_usd_mxn(db, settings) == (Decimal("18.06"), "banxico", "2026-10-30")
    with pytest.raises(ValueError):
        fx.store_rate(db, "USD", "MXN", "2026-10-30", Decimal("0"), "x")


def test_manual_rate_from_environment(tmp_path, db):
    s = make_settings(tmp_path, fx_usd_mxn_manual=17.5)
    rate, source, _ = fx.latest_usd_mxn(db, s)
    assert rate == Decimal("17.5") and source == "manual"


def test_refresh_from_banxico(db, settings, monkeypatch):
    assert asyncio.run(fx.refresh_from_banxico(db, settings)) is None  # no token -> skipped
    from flipradar.collectors import banxico

    async def fake_fetch(s, client=None):
        return "2026-10-31", Decimal("18.2345")

    monkeypatch.setattr(banxico, "fetch_usd_mxn_fix", fake_fetch)
    configured = settings.model_copy(update={"banxico_token": "t"})
    row = asyncio.run(fx.refresh_from_banxico(db, configured))
    assert (row.rate, row.rate_date, row.source) == ("18.2345", "2026-10-31", "banxico")


# ------------------------------------------------------------------------------------------------ import costs


def test_import_cost_brackets():
    rate = Decimal("18")
    low = estimate_import_costs(4_000, 1_000, "courier_usmca", rate)
    assert low["duty_rate_bp"] == 0 and low["duties_mxn_cents"] == 0 and low["customs_value_mxn_cents"] == 90_000
    mid = estimate_import_costs(10_000, 1_700, "courier_usmca", rate)
    assert mid["duty_rate_bp"] == 1700 and mid["duties_mxn_cents"] == 35_802  # 117 USD*18*17% = 358.02
    high = estimate_import_costs(20_000, 0, "courier_usmca", rate)
    assert high["duty_rate_bp"] == 0 and any(n.startswith("WARNING") for n in high["notes"])
    china = estimate_import_costs(10_000, 0, "courier_non_fta", rate)
    assert china["duty_rate_bp"] == 3350 and china["duties_mxn_cents"] == 60_300
    assert select_rule("courier_non_fta", 300_000).formal_import_required
    for result in (low, mid, high, china):
        assert any("PROVISIONAL" in n for n in result["notes"]) and result["as_of"] and result["source_url"]
    with pytest.raises(KeyError):
        estimate_import_costs(1, 0, "nope", rate)
    with pytest.raises(ValueError):
        estimate_import_costs(1, 0, "courier_usmca", Decimal("0"))


# ------------------------------------------------------------------------------------------------ scheduler


@pytest.mark.usefixtures("fake_pipeline", "fake_collectors")
def test_scheduler_tick_runs_due_searches_only(db, settings):
    now = datetime.now(UTC)
    due = TrackedSearch(query="iphone 15 pro", source_keys=["fakesource"], interval_minutes=60, last_run_at=None)
    recent = TrackedSearch(query="ps5", source_keys=["fakesource"], interval_minutes=60,
                           last_run_at=now - timedelta(minutes=10))
    disabled = TrackedSearch(query="switch", source_keys=["fakesource"], enabled=False)
    db.add_all([due, recent, disabled])
    db.commit()
    counters = asyncio.run(scheduler.tick(settings, now=now))
    assert counters == {"searches_run": 1, "fx_refreshed": 0}
    db.expire_all()
    assert db.get(TrackedSearch, due.id).last_run_at is not None
    assert asyncio.run(scheduler.tick(settings, now=now))["searches_run"] == 0  # not due anymore
    assert asyncio.run(scheduler.tick(settings, now=now + timedelta(minutes=61)))["searches_run"] == 2


def test_scheduler_refreshes_fx_at_most_every_12h(db, settings, monkeypatch):
    from flipradar.collectors import banxico

    calls = []

    async def fake_fetch(s, client=None):
        calls.append(1)
        return "2026-10-31", Decimal("18.00")

    monkeypatch.setattr(banxico, "fetch_usd_mxn_fix", fake_fetch)
    configured = settings.model_copy(update={"banxico_token": "t"})
    now = datetime.now(UTC)
    assert asyncio.run(scheduler.tick(configured, now=now))["fx_refreshed"] == 1
    assert asyncio.run(scheduler.tick(configured, now=now + timedelta(hours=1)))["fx_refreshed"] == 0
    assert asyncio.run(scheduler.tick(configured, now=now + timedelta(hours=13)))["fx_refreshed"] == 1
    assert len(calls) == 2


def test_scheduler_survives_errors_and_cancels(settings, client, monkeypatch):
    from flipradar.services import collection

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(collection, "run_due_searches", boom)
    assert asyncio.run(scheduler.tick(settings)) == {"searches_run": 0, "fx_refreshed": 0}

    async def run_and_cancel():
        task = scheduler.start(settings.model_copy(update={"scheduler_tick_seconds": 5}))
        await asyncio.sleep(0.05)
        await scheduler.stop(task)
        return task.cancelled() or task.done()

    assert asyncio.run(run_and_cancel())


# ------------------------------------------------------------------------------------------------ notify


def _note() -> Notification:
    return Notification(id=7, rule_id=1, opportunity_id=3, kind="opportunity", title="Cámara: +$1,000.00 (ROI 20.0%)",
                        body="Source: manual | Buy: $5,000.00", created_at=datetime(2026, 10, 1, tzinfo=UTC))


def test_unconfigured_channels(settings):
    result = notify.dispatch(_note(), ["in_app", "ntfy", "telegram", "webhook", "sms", "ntfy"], settings)
    assert result == {"in_app": "stored", "ntfy": "not_configured", "telegram": "not_configured",
                      "webhook": "not_configured", "sms": "unknown_channel"}


@respx.mock
def test_ntfy_telegram_webhook_delivery(settings):
    s = settings.model_copy(update={
        "ntfy_url": "https://ntfy.example.com/", "ntfy_topic": "flips", "ntfy_token": "tk_secret",
        "telegram_bot_token": "123:SECRET", "telegram_chat_id": "42", "webhook_url": "https://hooks.example.com/fr",
    })
    ntfy = respx.post("https://ntfy.example.com/flips").mock(return_value=httpx.Response(200))
    tg = respx.post("https://api.telegram.org/bot123:SECRET/sendMessage").mock(return_value=httpx.Response(200))
    hook = respx.post("https://hooks.example.com/fr").mock(return_value=httpx.Response(204))
    result = notify.dispatch(_note(), ["ntfy", "telegram", "webhook"], s, click_url="https://example.com/item")
    assert result == {"ntfy": "sent", "telegram": "sent", "webhook": "sent"}
    req = ntfy.calls.last.request
    assert req.headers["Authorization"] == "Bearer tk_secret"
    assert req.headers["Title"].startswith("=?utf-8?")  # RFC 2047 for the accented title
    assert req.headers["Priority"] == "high" and req.headers["Click"] == "https://example.com/item"
    assert req.content.decode() == "Source: manual | Buy: $5,000.00"
    import json

    tg_body = json.loads(tg.calls.last.request.content)
    assert tg_body["chat_id"] == "42" and "https://example.com/item" in tg_body["text"]
    hook_body = json.loads(hook.calls.last.request.content)
    assert hook_body["notification_id"] == 7 and hook_body["opportunity_id"] == 3


@respx.mock
def test_delivery_errors_never_leak_secrets(settings):
    s = settings.model_copy(update={"telegram_bot_token": "123:SECRET", "telegram_chat_id": "42",
                                    "ntfy_url": "https://ntfy.example.com", "ntfy_topic": "t",
                                    "webhook_url": "https://hooks.example.com/x"})
    respx.post("https://api.telegram.org/bot123:SECRET/sendMessage").mock(side_effect=httpx.ConnectError("bot123:SECRET"))
    respx.post("https://ntfy.example.com/t").mock(return_value=httpx.Response(403))
    respx.post("https://hooks.example.com/x").mock(side_effect=httpx.ReadTimeout("timeout"))
    result = notify.dispatch(_note(), ["telegram", "ntfy", "webhook"], s)
    assert result == {"telegram": "error: ConnectError", "ntfy": "error: HTTP 403", "webhook": "error: ReadTimeout"}
    assert "SECRET" not in str(result)


@respx.mock
def test_alert_delivery_records_status(api, db, settings):
    from flipradar.services import alerts
    from tests.test_api_alerts import fabricate_opportunity

    s = settings.model_copy(update={"webhook_url": "https://hooks.example.com/x"})
    respx.post("https://hooks.example.com/x").mock(return_value=httpx.Response(200))
    api.post("/api/v1/alerts", json={"name": "all", "channels": ["in_app", "webhook", "telegram"]})
    opp = fabricate_opportunity(db)
    created = alerts.evaluate(db, [opp], s)
    db.commit()
    note = db.scalars(select(Notification)).one()
    assert created[0].id == note.id
    assert note.delivery_json == {"in_app": "stored", "webhook": "sent", "telegram": "not_configured"}
