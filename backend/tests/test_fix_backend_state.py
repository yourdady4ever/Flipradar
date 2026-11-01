"""Regression tests for the backend_state review findings (SQLite locking, inventory accounting, analytics).

Every test reproduces one verified defect; the finding id is in the test name / docstring.  The locking tests
run against the on-disk temp SQLite file of the ``settings`` fixture (never StaticPool / in-memory), because
the defects only exist when several connections compete for SQLite's single write lock.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from flipradar.collectors import mercadolibre, registry
from flipradar.collectors.base import Collector, CollectorInfo
from flipradar.db import SessionLocal, session_scope
from flipradar.models import AppSetting, CollectionRun, InventoryItem, Notification, TrackedSearch
from flipradar.schemas import InventoryIn, SaleIn
from flipradar.services import alerts, analytics, bootstrap, collection, notify
from flipradar.services import inventory as inventory_service
from flipradar.services.errors import Conflict
from tests.conftest import raw
from tests.test_api_alerts import fabricate_opportunity

# ------------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------------


def _db_path(settings) -> str:
    return str(settings.sqlite_path)


def can_take_write_lock(path: str, timeout: float = 0.2) -> bool:
    """True when another connection could start a write transaction right now (BEGIN IMMEDIATE)."""
    con = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


class SlowCollector(Collector):
    """Test double: awaits (like a network call) and records whether the DB write lock was free meanwhile."""

    info = CollectorInfo(
        key="slowsource", name="Slow marketplace", kind="marketplace", country="MX", currency="MXN",
        default_fee_profile_key="ml_clasica", requires_credentials=False, docs_url=None, capabilities=["search"],
    )
    db_path: str = ""
    probes: list[bool] = []
    delay: float = 0.2

    def is_configured(self) -> bool:
        return True

    async def search(self, query: str, *, limit: int = 50, category_id: str | None = None):
        await asyncio.sleep(self.delay / 2)
        type(self).probes.append(can_take_write_lock(type(self).db_path))
        await asyncio.sleep(self.delay / 2)
        return [raw(f"S{query}{i}", f"iPhone 15 Pro 256GB usado {query} {i}", 15000 + i) for i in range(3)]


@pytest.fixture
def slow_collector(client, settings):
    SlowCollector.db_path = _db_path(settings)
    SlowCollector.probes = []
    registry.register(SlowCollector)
    with session_scope() as session:
        bootstrap.seed_sources(session)
    yield SlowCollector
    registry.unregister(SlowCollector.info.key)


@pytest.fixture
def ml_token_cleanup():
    yield
    mercadolibre._REFRESH_LISTENERS.clear()
    mercadolibre.reset_token_cache()
    bootstrap._reset_ml_token_state()


# ------------------------------------------------------------------------------------------------
# services#1 - the rotated Mercado Libre refresh token is persisted during a real collection
# ------------------------------------------------------------------------------------------------


def test_services1_rotated_ml_refresh_token_is_persisted_during_run_collection(db, settings, ml_token_cleanup):
    seeded = settings.model_copy(update={"ml_client_id": "cid", "ml_client_secret": "secret",
                                         "ml_refresh_token": "TG-seed"})
    mercadolibre.reset_token_cache()
    bootstrap.install_ml_token_persistence(db, seeded)
    token_forms: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/token"):
            token_forms.append(request.content.decode())
            return httpx.Response(200, json={"access_token": "APP_USR-1", "expires_in": 21600,
                                             "refresh_token": "TG-rotated-NEW", "user_id": 1})
        return httpx.Response(200, json={"results": [], "paging": {"total": 0}})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            session = SessionLocal()
            try:
                return await collection.run_collection(session, "iphone 13", ["mercadolibre"], 10, analyze=False,
                                                       settings=seeded, client=http)
            finally:
                session.close()

    started = time.monotonic()
    result = asyncio.run(main())
    elapsed = time.monotonic() - started
    assert token_forms and "refresh_token=TG-seed" in token_forms[0]
    assert [r.status for r in result.runs] == ["ok"], result.runs
    assert elapsed < 4.0, f"collection stalled for {elapsed:.1f}s (SQLite busy_timeout)"
    with session_scope() as s:
        row = s.get(AppSetting, bootstrap.ML_TOKEN_KEY)
        assert row is not None and row.value_json["token"] == "TG-rotated-NEW"
    # Restart with the same (now invalidated) seed configured: the persisted rotated token is used.
    restarted = settings.model_copy(update={"ml_client_id": "cid", "ml_client_secret": "secret",
                                            "ml_refresh_token": "TG-seed"})
    bootstrap.install_ml_token_persistence(db, restarted)
    assert restarted.ml_refresh_token == "TG-rotated-NEW"


def test_services1_listener_never_writes_on_the_event_loop(db, settings, ml_token_cleanup):
    """Called from a running loop the listener returns at once, even while another connection holds the
    write lock; the token is written as soon as the lock is free."""
    seeded = settings.model_copy(update={"ml_refresh_token": "TG-seed"})
    bootstrap.install_ml_token_persistence(db, seeded)
    blocker = sqlite3.connect(_db_path(settings), timeout=0.1, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")

    async def main():
        started = time.monotonic()
        for listener in list(mercadolibre._REFRESH_LISTENERS):
            listener("TG-rotated-2")
        returned_after = time.monotonic() - started
        await asyncio.sleep(0.2)
        blocker.execute("ROLLBACK")  # lock released: the background write can now finish
        assert await asyncio.to_thread(bootstrap.persist_rotated_ml_token) is True
        return returned_after

    try:
        returned_after = asyncio.run(main())
    finally:
        blocker.close()
    assert returned_after < 0.5
    with session_scope() as s:
        assert s.get(AppSetting, bootstrap.ML_TOKEN_KEY).value_json["token"] == "TG-rotated-2"


# ------------------------------------------------------------------------------------------------
# services#2 - no write transaction is held across collector network awaits
# ------------------------------------------------------------------------------------------------


def test_services2_run_collection_holds_no_write_lock_while_collector_awaits(slow_collector, fake_pipeline):
    async def main():
        session = SessionLocal()
        try:
            return await collection.run_collection(session, "a", ["slowsource"], 10, analyze=False)
        finally:
            session.close()

    result = asyncio.run(main())
    assert [r.status for r in result.runs] == ["ok"]
    assert result.runs[0].items_new == 3
    assert slow_collector.probes == [True], "another writer would have failed with 'database is locked'"
    with session_scope() as s:  # the run row was committed as 'running' and then finished
        run = s.scalars(select(CollectionRun).order_by(CollectionRun.id.desc())).first()
        assert run.status == "ok" and run.finished_at is not None


def test_services2_tracked_search_holds_no_write_lock_while_collector_awaits(slow_collector, fake_pipeline):
    with session_scope() as s:
        search = TrackedSearch(query="b", source_keys=["slowsource"], interval_minutes=60, max_results=5)
        s.add(search)
        s.flush()
        search_id = search.id

    async def main():
        session = SessionLocal()
        try:
            return await collection.run_tracked_search(session, search_id)
        finally:
            session.close()

    result = asyncio.run(main())
    assert [r.status for r in result.runs] == ["ok"]
    assert slow_collector.probes == [True]
    with session_scope() as s:
        assert s.get(TrackedSearch, search_id).last_run_at is not None


def test_services2_overlapping_collections_neither_fail_nor_freeze_the_loop(slow_collector, fake_pipeline):
    """A scheduled run and a manual 'Run now' overlapping on the same event loop both succeed quickly."""
    slow_collector.delay = 0.3

    async def run(query: str):
        session = SessionLocal()
        try:
            return await collection.run_collection(session, query, ["slowsource"], 10, analyze=False)
        finally:
            session.close()

    async def main():
        stalls: list[float] = []
        stop = asyncio.Event()

        async def heartbeat():
            last = time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.02)
                now = time.monotonic()
                stalls.append(now - last)
                last = now

        beat = asyncio.create_task(heartbeat())
        started = time.monotonic()
        try:
            results = await asyncio.gather(run("x"), run("y"))
        finally:
            stop.set()
            await beat
        return results, time.monotonic() - started, max(stalls or [0.0])

    try:
        results, elapsed, worst_stall = asyncio.run(main())
    finally:
        slow_collector.delay = 0.2
    assert [r.status for res in results for r in res.runs] == ["ok", "ok"]
    assert elapsed < 3.0, f"overlapping runs took {elapsed:.1f}s"
    assert worst_stall < 1.0, f"event loop blocked for {worst_stall:.1f}s"


# ------------------------------------------------------------------------------------------------
# services#5 - notification delivery holds no write lock during the (slow) push HTTP calls
# ------------------------------------------------------------------------------------------------


def _three_notifications(api, db, settings):
    api.post("/api/v1/alerts", json={"name": "all", "channels": ["webhook"], "exclude_suspicious": False})
    opps = [fabricate_opportunity(db) for _ in range(3)]
    return opps


def test_services5_deliver_dispatches_without_holding_the_write_lock(api, db, settings, monkeypatch):
    opps = _three_notifications(api, db, settings)
    notes = alerts.evaluate(db, opps, settings, dispatch=False)
    db.commit()
    assert len(notes) == 3
    probes: list[bool] = []

    def slow_dispatch(note, channels, s, *, click_url=None, client=None):
        probes.append(can_take_write_lock(_db_path(settings)))
        return {"webhook": "sent"}

    monkeypatch.setattr(notify, "dispatch", slow_dispatch)
    alerts.deliver(db, notes, settings)
    db.commit()
    assert probes == [True, True, True], "a concurrent API write would have failed with 'database is locked'"
    with session_scope() as s:
        stored = s.scalars(select(Notification).order_by(Notification.id)).all()
        assert [n.delivery_json for n in stored] == [{"webhook": "sent"}] * 3


def test_services5_evaluate_with_dispatch_holds_no_write_lock(api, db, settings, monkeypatch):
    opps = _three_notifications(api, db, settings)
    probes: list[bool] = []

    def slow_dispatch(note, channels, s, *, click_url=None, client=None):
        probes.append(can_take_write_lock(_db_path(settings)))
        return {"webhook": "sent"}

    monkeypatch.setattr(notify, "dispatch", slow_dispatch)
    created = alerts.evaluate(db, opps, settings)
    db.commit()
    assert len(created) == 3 and probes == [True, True, True]
    assert all(n.delivery_json == {"webhook": "sent"} for n in created)


# ------------------------------------------------------------------------------------------------
# services#6 - the profile used as default_fee_profile_key_used cannot be deleted
# ------------------------------------------------------------------------------------------------


def test_services6_default_used_fee_profile_cannot_be_deleted(api):
    r = api.post("/api/v1/fee-profiles", json={"key": "my_used", "name": "My used", "channel": "mercadolibre",
                                               "commission_bp": 100})
    assert r.status_code == 201, r.text
    assert api.put("/api/v1/settings", json={"default_fee_profile_key_used": "my_used"}).status_code == 200
    r = api.delete("/api/v1/fee-profiles/my_used")
    assert r.status_code == 409, r.text
    assert "used" in r.json()["detail"]
    assert any(p["key"] == "my_used" for p in api.get("/api/v1/fee-profiles").json())
    # After pointing the setting elsewhere it can be deleted.
    assert api.put("/api/v1/settings", json={"default_fee_profile_key_used": "ml_clasica_used"}).status_code == 200
    assert api.delete("/api/v1/fee-profiles/my_used").status_code == 200


# ------------------------------------------------------------------------------------------------
# services#7 - PATCH {"condition": null} is a 422, not a 500
# ------------------------------------------------------------------------------------------------


def test_services7_null_condition_is_rejected_with_422(api):
    item = api.post("/api/v1/inventory", json={"title": "Switch", "unit_cost_cents": 100_000}).json()
    r = api.patch(f"/api/v1/inventory/{item['id']}", json={"condition": None})
    assert r.status_code == 422 and "condition" in r.json()["detail"]
    assert api.get(f"/api/v1/inventory/{item['id']}").json()["condition"] == "unknown"


# ------------------------------------------------------------------------------------------------
# services#8 - since_id pages forward, so a poller never skips notifications
# ------------------------------------------------------------------------------------------------


def test_services8_since_id_pages_forward_without_skipping(api, db):
    db.add_all([Notification(kind="opportunity", title=f"n{i}", body="b", delivery_json={}) for i in range(121)])
    db.commit()
    ids = sorted(db.scalars(select(Notification.id)))
    last_seen = ids[0]  # the poller already saw the first one
    seen: list[int] = []
    for _ in range(10):  # NotificationPollJob: since_id=lastSeen, limit=50, lastSeen = max(id)
        page = api.get(f"/api/v1/notifications?unread_only=true&since_id={last_seen}&limit=50").json()
        if not page:
            break
        page_ids = [n["id"] for n in page]
        assert page_ids == sorted(page_ids, reverse=True)  # still newest first within a response
        seen.extend(page_ids)
        last_seen = max(page_ids)
    assert sorted(seen) == ids[1:], "notifications between polls were skipped"
    # Without a cursor the inbox still shows the newest page.
    newest = api.get("/api/v1/notifications?limit=5").json()
    assert [n["id"] for n in newest] == ids[-5:][::-1]


# ------------------------------------------------------------------------------------------------
# finance#1 - cost edits after a sale would break sum(cost basis) + remaining cost == total cost
# ------------------------------------------------------------------------------------------------


def _item(api, **overrides):
    body = {"title": "PS5", "quantity": 2, "unit_cost_cents": 10_000, "status": "in_stock", **overrides}
    r = api.post("/api/v1/inventory", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _sell(api, item_id, price=20_000, **extra):
    r = api.post(f"/api/v1/inventory/{item_id}/sales", json={"quantity": 1, "unit_price_cents": price,
                                                              "fees_cents": 0, "taxes_withheld_cents": 0, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_finance1_cost_cannot_change_once_the_item_has_sales(api):
    item = _item(api)
    # Before any sale the cost is freely editable (e.g. a late import-duty invoice).
    r = api.patch(f"/api/v1/inventory/{item['id']}", json={"extra_costs_cents": 1_000})
    assert r.status_code == 200 and r.json()["total_cost_cents"] == 21_000
    _sell(api, item["id"])
    for change in ({"unit_cost_cents": 15_000}, {"extra_costs_cents": 4_000}):
        r = api.patch(f"/api/v1/inventory/{item['id']}", json=change)
        assert r.status_code == 409, r.text
        assert "other_costs_cents" in r.json()["detail"]
    # Re-sending the unchanged values (clients that PATCH every field) and other edits still work.
    r = api.patch(f"/api/v1/inventory/{item['id']}", json={"unit_cost_cents": 10_000, "extra_costs_cents": 1_000,
                                                           "notes": "box damaged"})
    assert r.status_code == 200 and r.json()["notes"] == "box damaged"
    _sell(api, item["id"])
    final = api.get(f"/api/v1/inventory/{item['id']}").json()
    assert sum(s["cost_basis_cents"] for s in final["sales"]) == final["total_cost_cents"] == 21_000
    assert final["realized_profit_cents"] == 40_000 - 21_000


# ------------------------------------------------------------------------------------------------
# finance#3 - Mercado Libre shipping is not counted twice
# ------------------------------------------------------------------------------------------------


def test_finance3_ml_shipping_paid_replaces_the_profile_shipping_tier(api):
    def sale(shipping, profile="ml_clasica"):
        item = _item(api, quantity=1, unit_cost_cents=800_000)
        r = api.post(f"/api/v1/inventory/{item['id']}/sales", json={
            "quantity": 1, "unit_price_cents": 1_100_000, "fee_profile_key": profile, "shipping_cents": shipping})
        assert r.status_code == 201, r.text
        return r.json()

    estimated = sale(0)  # shipping left blank: the profile's per-unit shipping tier (6,550) is the estimate
    assert estimated["fees_cents"] == 165_000 + 6_550 and estimated["shipping_cents"] == 0
    actual = sale(6_550)  # the user entered what Mercado Libre actually charged
    assert actual["fees_cents"] == 165_000 and actual["shipping_cents"] == 6_550
    assert actual["profit_cents"] == estimated["profit_cents"]
    higher = sale(9_000)  # a heavier parcel: the real charge wins
    assert higher["profit_cents"] == estimated["profit_cents"] - (9_000 - 6_550)
    # Profiles whose fixed fee is not shipping are unchanged: shipping is an extra cost there.
    amazon = sale(6_550, profile="amazon_mx_standard")
    amazon_blank = sale(0, profile="amazon_mx_standard")
    assert amazon["fees_cents"] == amazon_blank["fees_cents"]
    assert amazon["profit_cents"] == amazon_blank["profit_cents"] - 6_550


# ------------------------------------------------------------------------------------------------
# finance#4 - concurrent sales cannot oversell the last unit
# ------------------------------------------------------------------------------------------------


def test_finance4_interleaved_sales_cannot_oversell(client):
    with session_scope() as s:
        item_id = inventory_service.create_item(s, InventoryIn(title="Switch", quantity=1, unit_cost_cents=1_000_000,
                                                              status="in_stock")).id
    a, b = SessionLocal(), SessionLocal()
    try:
        item_a = inventory_service.get_item(a, item_id)
        item_b = inventory_service.get_item(b, item_id)
        assert item_a.quantity_available == item_b.quantity_available == 1  # both read the stale value
        inventory_service.record_sale(a, item_a, SaleIn(quantity=1, unit_price_cents=1_500_000))
        a.commit()
        with pytest.raises(Conflict):
            inventory_service.record_sale(b, item_b, SaleIn(quantity=1, unit_price_cents=1_500_000))
        b.rollback()
    finally:
        a.close()
        b.close()
    with session_scope() as s:
        item = s.get(InventoryItem, item_id)
        assert item.quantity_available == 0 and item.status == "sold"
        assert len(item.sales) == 1 and sum(x.cost_basis_cents for x in item.sales) == 1_000_000


# ------------------------------------------------------------------------------------------------
# finance#5 - the first timeseries bucket covers its whole period
# ------------------------------------------------------------------------------------------------


def test_finance5_first_bucket_is_not_partial(db):
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    item = inventory_service.create_item(db, InventoryIn(title="Phone", quantity=3, unit_cost_cents=1_000,
                                                         status="in_stock",
                                                         purchased_at=datetime(2026, 7, 30, tzinfo=UTC)))
    for when, price in ((datetime(2026, 8, 2, 10, tzinfo=UTC), 5_000), (datetime(2026, 8, 20, 10, tzinfo=UTC), 7_000),
                        (datetime(2026, 10, 2, 8, tzinfo=UTC), 999)):
        inventory_service.record_sale(db, item, SaleIn(quantity=1, unit_price_cents=price, sold_at=when))
    db.commit()
    monthly = {p.period: p for p in analytics.timeseries(db, 90, "month", now=now).points}
    assert monthly["2026-08-01"].revenue_cents == 12_000 and monthly["2026-08-01"].units == 2
    daily = analytics.timeseries(db, 30, "day", now=now).points
    assert daily[0].period == "2026-10-02" and daily[0].revenue_cents == 999


# ------------------------------------------------------------------------------------------------
# finance#6 - per-sale cost bases add up to the item's total cost
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("quantity", "unit", "extra"), [(3, 333, 1), (2, 500, 1), (7, 1_001, 3), (1000, 100, 500)])
def test_finance6_cost_bases_reconcile_with_total_cost(db, quantity, unit, extra):
    item = inventory_service.create_item(db, InventoryIn(title="Lot", quantity=quantity, unit_cost_cents=unit,
                                                         extra_costs_cents=extra, status="in_stock"))
    total = unit * quantity + extra
    for n in range(1, quantity + 1):
        inventory_service.record_sale(db, item, SaleIn(quantity=1, unit_price_cents=unit * 2))
        if quantity <= 10 or n % 250 == 1:
            sold = sum(s.cost_basis_cents for s in item.sales)
            remaining = analytics.summary(db, 30).inventory_cost_cents
            assert sold + remaining == total
    assert sum(s.cost_basis_cents for s in item.sales) == total


def test_finance6_deleting_a_middle_sale_keeps_the_books_reconciled(db):
    item = inventory_service.create_item(db, InventoryIn(title="Lot", quantity=3, unit_cost_cents=333,
                                                         extra_costs_cents=1, status="in_stock"))
    sales = [inventory_service.record_sale(db, item, SaleIn(quantity=1, unit_price_cents=900)) for _ in range(2)]
    inventory_service.delete_sale(db, sales[0])
    db.expire(item, ["sales"])
    for _ in range(2):
        inventory_service.record_sale(db, item, SaleIn(quantity=1, unit_price_cents=900))
    db.expire(item, ["sales"])
    assert item.quantity_available == 0
    assert sum(s.cost_basis_cents for s in item.sales) == 1_000


# ------------------------------------------------------------------------------------------------
# finance#7 - writing stock off books the loss
# ------------------------------------------------------------------------------------------------


def test_finance7_write_off_is_booked_as_a_realized_loss(api):
    a = _item(api, quantity=1, unit_cost_cents=100_000)
    _sell(api, a["id"], price=150_000)
    b = _item(api, quantity=1, unit_cost_cents=500_000)
    before = api.get("/api/v1/analytics/summary?days=30").json()
    assert before["realized_profit_cents"] == 50_000 and before["inventory_cost_cents"] == 500_000

    assert api.patch(f"/api/v1/inventory/{b['id']}", json={"status": "written_off"}).status_code == 200
    after = api.get("/api/v1/analytics/summary?days=30").json()
    assert after["inventory_cost_cents"] == 0
    assert after["realized_profit_cents"] == 50_000 - 500_000
    assert after["avg_roi_bp"] == round(-450_000 * 10_000 / 600_000)
    assert api.get(f"/api/v1/inventory/{b['id']}").json()["realized_profit_cents"] == -500_000
    ts = api.get("/api/v1/analytics/timeseries?days=30&bucket=day").json()
    assert sum(p["profit_cents"] for p in ts["points"]) == 50_000 - 500_000

    # Undoing the write-off puts the cost back into inventory and removes the loss.
    assert api.patch(f"/api/v1/inventory/{b['id']}", json={"status": "in_stock"}).status_code == 200
    undone = api.get("/api/v1/analytics/summary?days=30").json()
    assert undone["realized_profit_cents"] == 50_000 and undone["inventory_cost_cents"] == 500_000


def test_finance7_partial_write_off_and_returns(api):
    c = _item(api, quantity=3, unit_cost_cents=200_000)
    _sell(api, c["id"], price=250_000)
    api.patch(f"/api/v1/inventory/{c['id']}", json={"status": "written_off"})
    assert api.get(f"/api/v1/inventory/{c['id']}").json()["realized_profit_cents"] == 50_000 - 400_000
    # A returned purchase is refunded by the seller: no loss is booked, the cost simply leaves inventory.
    d = _item(api, quantity=1, unit_cost_cents=300_000)
    api.patch(f"/api/v1/inventory/{d['id']}", json={"status": "returned"})
    summary = api.get("/api/v1/analytics/summary?days=30").json()
    assert summary["realized_profit_cents"] == 50_000 - 400_000 and summary["inventory_cost_cents"] == 0


def test_finance7_written_off_at_column_is_added_to_existing_databases(api, db, settings):
    legacy = _item(api, quantity=1, unit_cost_cents=70_000)
    api.patch(f"/api/v1/inventory/{legacy['id']}", json={"status": "written_off"})
    _item(api, quantity=1, unit_cost_cents=1_000)
    db.close()
    con = sqlite3.connect(_db_path(settings))  # a database created before the column existed
    con.execute("ALTER TABLE inventory_items DROP COLUMN written_off_at")
    con.commit()
    con.close()
    with session_scope() as s:
        assert bootstrap.ensure_added_columns(s) == ["inventory_items.written_off_at"]
    with session_scope() as s:
        assert bootstrap.ensure_added_columns(s) == []
        rows = {i.status: i.written_off_at for i in s.scalars(select(InventoryItem))}
        assert rows["written_off"] is not None and rows["in_stock"] is None  # legacy write-off backfilled
    assert api.get("/api/v1/analytics/summary?days=30").json()["realized_profit_cents"] == -70_000
