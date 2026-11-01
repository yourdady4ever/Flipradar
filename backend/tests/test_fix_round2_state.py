"""Regression tests for the second-round review findings of the r2_state area.

state#0           recompute after a collection failed with 'database is locked' when another connection committed
state#1 / em#5    stale-buy expiry followed by re-sighting reset seen/watching opportunities to NEW
state#2           a cost lowered after a sale (allowed by the previous release) gave negative cost bases
state#3 / c#2     reused notification ids made the since_id cursor skip notifications
state#4           collection runs could stay 'running' forever (cancelled task / failed abort)
em#6              import duties of a listing without an international shipping quote used too low a bracket

The locking tests run on the on-disk temp SQLite file of the ``settings`` fixture with a real second writer
thread, because the defect only exists when several connections compete for SQLite's single write lock.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError

from flipradar.collectors import registry
from flipradar.collectors.base import Collector, CollectorInfo
from flipradar.db import SessionLocal, session_scope
from flipradar.models import (
    AlertRule,
    CollectionRun,
    InventoryItem,
    Listing,
    Notification,
    Opportunity,
    Source,
    TrackedSearch,
)
from flipradar.schemas import InventoryIn, InventoryUpdate, SaleIn
from flipradar.services import analysis, analytics, bootstrap, collection, scheduler
from flipradar.services import inventory as inventory_service
from flipradar.services.ingestion import ingest_raw_listings
from flipradar.services.timeutil import utcnow
from tests.conftest import raw
from tests.test_api_alerts import fabricate_opportunity

T0 = datetime(2026, 4, 15, 12, tzinfo=UTC)
IPHONE_15 = "Apple iPhone 15 Pro 256GB usado"
IPHONE_13 = "Apple iPhone 13 128GB usado"
PRICES = [9000, 9500, 10000, 10500, 11000, 11500]


def _listings(title: str, at: datetime, prefix: str, *, buy: bool = True) -> list:
    rows = [raw(f"{prefix}C{i}", title, p, brand=None, model=None, observed_at=at) for i, p in enumerate(PRICES)]
    if buy:
        rows.append(raw(f"{prefix}B", title, 6000, brand=None, model=None, observed_at=at))
    return rows


def _ingest(db, settings, rows, at):
    stats = ingest_raw_listings(db, "manual", rows, now=at, settings=settings)
    db.commit()
    return stats


def _opp(db, external_id: str) -> Opportunity:
    return db.scalars(select(Opportunity).join(Listing, Opportunity.buy_listing_id == Listing.id)
                      .where(Listing.external_id == external_id)).one()


# ------------------------------------------------------------------------------------------------
# state#0 - recompute must wait for the write lock instead of failing when another connection commits
# ------------------------------------------------------------------------------------------------


def _start_writer(path: str, outcome: list[str], done: threading.Event) -> threading.Thread:
    """A second connection (another request / collection) committing one small write."""

    def write() -> None:
        con = sqlite3.connect(path, timeout=10)
        try:
            con.execute("INSERT INTO probe (n) VALUES (1)")
            con.commit()
            outcome.append("ok")
        except Exception as exc:  # pragma: no cover - reported by the assertion
            outcome.append(repr(exc))
        finally:
            con.close()
            done.set()

    thread = threading.Thread(target=write, daemon=True)
    thread.start()
    return thread


def _create_probe_table(settings) -> str:
    path = str(settings.sqlite_path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS probe (n INTEGER)")
    con.commit()
    con.close()
    return path


def test_state0_recompute_survives_a_concurrent_commit_during_analysis(db, settings, monkeypatch, caplog):
    path = _create_probe_table(settings)
    _ingest(db, settings, [*_listings(IPHONE_15, T0, "a"), *_listings(IPHONE_13, T0, "b")], T0)

    real_observations = analysis._observations
    outcome: list[str] = []
    done = threading.Event()
    threads: list[threading.Thread] = []

    def observations_with_concurrent_commit(session, listing_ids, now):
        if not threads:  # first product: another connection commits while this product is analysed
            threads.append(_start_writer(path, outcome, done))
            done.wait(0.5)  # it is blocked while recompute holds the write lock (expected), else it commits now
        return real_observations(session, listing_ids, now)

    monkeypatch.setattr(analysis, "_observations", observations_with_concurrent_commit)
    with caplog.at_level(logging.ERROR, logger=analysis.log.name):
        result = analysis.recompute(db, now=T0, settings=settings, dispatch=False)
    threads[0].join(10)
    assert outcome == ["ok"], "the concurrent writer must succeed (it waits for the lock, never fails)"
    assert "analysis failed" not in caplog.text
    assert result.opportunities_created == 2, result
    assert {_opp(db, "aB").status, _opp(db, "bB").status} == {"new"}


def test_state0_recompute_with_a_free_running_concurrent_writer(db, settings):
    """No monkeypatching: a real second writer commits as fast as it can during the whole recompute."""
    path = _create_probe_table(settings)
    _ingest(db, settings, [*_listings(IPHONE_15, T0, "a"), *_listings(IPHONE_13, T0, "b")], T0)
    stop = threading.Event()
    errors: list[str] = []
    commits = [0]

    def hammer() -> None:
        con = sqlite3.connect(path, timeout=10)
        try:
            while not stop.is_set():
                con.execute("INSERT INTO probe (n) VALUES (1)")
                con.commit()
                commits[0] += 1
                time.sleep(0.001)
        except Exception as exc:  # pragma: no cover - reported by the assertion
            errors.append(repr(exc))
        finally:
            con.close()

    thread = threading.Thread(target=hammer, daemon=True)
    thread.start()
    try:
        while commits[0] < 3:  # the writer is running
            time.sleep(0.005)
        result = analysis.recompute(db, now=T0, settings=settings, dispatch=False)
    finally:
        stop.set()
        thread.join(10)
    assert errors == []
    assert result.opportunities_created == 2, result


def test_state0_a_failing_stale_sweep_never_fails_the_recompute(db, settings, monkeypatch, caplog):
    _ingest(db, settings, _listings(IPHONE_15, T0, "a"), T0)

    def locked(*_args, **_kwargs):
        raise OperationalError("UPDATE opportunities SET status=?", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(analysis, "_expire_stale_buys", locked)
    with caplog.at_level(logging.ERROR, logger=analysis.log.name):
        result = analysis.recompute(db, now=T0, settings=settings, dispatch=False)  # must not raise
    assert result.opportunities_created == 1
    assert "stale" in caplog.text
    assert _opp(db, "aB").status == "new"  # the product's results were committed


# ------------------------------------------------------------------------------------------------
# state#1 / engine_matching#5 - a stale-expired seen/watching opportunity gets its status back when re-sighted
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("user_status", ["seen", "watching"])
def test_state1_status_survives_expiry_when_its_own_product_is_recomputed(db, settings, user_status):
    _ingest(db, settings, _listings(IPHONE_15, T0, "a"), T0)
    analysis.recompute(db, now=T0, settings=settings, dispatch=False)
    opp = _opp(db, "aB")
    opp.status = user_status
    db.commit()

    later = T0 + timedelta(days=4)  # comparables seen again, the buy listing not: it went stale
    _ingest(db, settings, _listings(IPHONE_15, later, "a", buy=False), later)
    analysis.recompute(db, now=later, settings=settings, dispatch=False)
    db.refresh(opp)
    assert opp.status == "expired"

    back = T0 + timedelta(days=5)  # the collector sees the buy listing again
    _ingest(db, settings, _listings(IPHONE_15, back, "a"), back)
    result = analysis.recompute(db, now=back, settings=settings, dispatch=False)
    db.refresh(opp)
    assert opp.status == user_status, "the user's decision was lost"
    assert result.opportunities_created == 0


@pytest.mark.parametrize("user_status", ["seen", "watching"])
def test_state1_status_survives_the_global_stale_sweep(db, settings, user_status):
    _ingest(db, settings, _listings(IPHONE_15, T0, "a"), T0)
    analysis.recompute(db, now=T0, settings=settings, dispatch=False)
    opp = _opp(db, "aB")
    opp.status = user_status
    db.commit()

    later = T0 + timedelta(days=4)  # only another product is collected and recomputed
    stats = _ingest(db, settings, _listings(IPHONE_13, later, "b", buy=False), later)
    assert opp.product_id not in stats.product_ids_touched
    analysis.recompute(db, sorted(stats.product_ids_touched), now=later, settings=settings, dispatch=False)
    db.refresh(opp)
    assert opp.status == "expired"  # stale like any other open opportunity (consistent with the own-product path)

    back = T0 + timedelta(days=5)
    _ingest(db, settings, _listings(IPHONE_15, back, "a"), back)
    analysis.recompute(db, now=back, settings=settings, dispatch=False)
    db.refresh(opp)
    assert opp.status == user_status


def test_state1_new_opportunity_comes_back_as_new(db, settings):
    _ingest(db, settings, _listings(IPHONE_15, T0, "a"), T0)
    analysis.recompute(db, now=T0, settings=settings, dispatch=False)
    later = T0 + timedelta(days=4)
    _ingest(db, settings, _listings(IPHONE_15, later, "a", buy=False), later)
    analysis.recompute(db, now=later, settings=settings, dispatch=False)
    assert _opp(db, "aB").status == "expired"
    back = T0 + timedelta(days=5)
    _ingest(db, settings, _listings(IPHONE_15, back, "a"), back)
    analysis.recompute(db, now=back, settings=settings, dispatch=False)
    opp = _opp(db, "aB")
    db.refresh(opp)
    assert opp.status == "new"


def test_state1_status_before_expiry_column_is_added_to_existing_databases(api, db, settings):
    db.close()
    con = sqlite3.connect(str(settings.sqlite_path))  # a database created before the column existed
    con.execute("ALTER TABLE opportunities DROP COLUMN status_before_expiry")
    con.commit()
    con.close()
    with session_scope() as s:
        assert bootstrap.ensure_added_columns(s) == ["opportunities.status_before_expiry"]
    with session_scope() as s:
        assert bootstrap.ensure_added_columns(s) == []


# ------------------------------------------------------------------------------------------------
# state#2 - sales of items whose cost was lowered after a sale (previous release) never get a negative basis
# ------------------------------------------------------------------------------------------------


def _sale(**overrides) -> SaleIn:
    return SaleIn(**{"quantity": 1, "unit_price_cents": 150_000, "fees_cents": 0, "taxes_withheld_cents": 0,
                     **overrides})


def test_state2_cost_lowered_after_a_sale_in_a_legacy_database(db):
    item = inventory_service.create_item(db, InventoryIn(title="Lot", quantity=3, unit_cost_cents=1_000_000,
                                                         status="in_stock"))
    first = inventory_service.record_sale(db, item, _sale())
    assert first.cost_basis_cents == 1_000_000
    db.commit()
    # The previous release had no cost lock: its PATCH could correct the cost after a sale (a $10,000 typo).
    db.execute(update(InventoryItem).where(InventoryItem.id == item.id).values(unit_cost_cents=100_000))
    db.commit()
    db.refresh(item)
    db.expire(item, ["sales"])

    assert inventory_service.remaining_cost_cents(item) == 200_000  # the two unsold units at the corrected cost
    assert analytics.summary(db, 30).inventory_cost_cents == 200_000

    second = inventory_service.record_sale(db, item, _sale())
    assert second.cost_basis_cents == 100_000 and second.profit_cents == 50_000
    assert analytics.summary(db, 30).inventory_cost_cents == 100_000

    inventory_service.update_item(db, item, InventoryUpdate(status="written_off"))
    db.expire(item, ["sales"])
    assert inventory_service.written_off_cost_cents(item) == 100_000  # a loss, never a gain


def test_state2_rounding_dust_after_deleted_sales_never_gives_a_negative_basis(db):
    item = inventory_service.create_item(db, InventoryIn(title="Dust", quantity=5, unit_cost_cents=0,
                                                         extra_costs_cents=2, status="in_stock"))
    sales = [inventory_service.record_sale(db, item, _sale(unit_price_cents=10)) for _ in range(4)]
    assert [s.cost_basis_cents for s in sales] == [0, 1, 0, 1]
    for sale in sales:
        if sale.cost_basis_cents == 0:
            inventory_service.delete_sale(db, sale)
    db.expire(item, ["sales"])
    assert inventory_service.remaining_cost_cents(item) >= 0
    for _ in range(3):
        sale = inventory_service.record_sale(db, item, _sale(unit_price_cents=10))
        assert sale.cost_basis_cents >= 0
        db.expire(item, ["sales"])
        assert inventory_service.remaining_cost_cents(item) >= 0
    assert sum(s.cost_basis_cents for s in item.sales) == 2  # the books still reconcile


# ------------------------------------------------------------------------------------------------
# state#3 / contracts#2 - notification ids are never reused, so the since_id cursor never skips
# ------------------------------------------------------------------------------------------------

LEGACY_NOTIFICATIONS_DDL = """
CREATE TABLE notifications (
    id INTEGER NOT NULL,
    rule_id INTEGER,
    opportunity_id INTEGER,
    kind VARCHAR(32) NOT NULL,
    title VARCHAR(200) NOT NULL,
    body TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    read_at DATETIME,
    delivery_json JSON NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_notification_rule_opp UNIQUE (rule_id, opportunity_id),
    FOREIGN KEY(rule_id) REFERENCES alert_rules (id) ON DELETE SET NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities (id) ON DELETE CASCADE
)"""


def _add_notifications(db, count: int) -> list[int]:
    notes = [Notification(kind="system", title=f"n{i}", body="b", delivery_json={}) for i in range(count)]
    db.add_all(notes)
    db.commit()
    return [n.id for n in notes]


def test_state3_deleted_notification_ids_are_not_reused(api, db):
    ids = _add_notifications(db, 5)
    cursor = max(ids)  # the poller has seen every notification
    db.execute(delete(Notification).where(Notification.id > ids[1]))  # e.g. clear-demo deletes the newest
    db.commit()
    new_ids = _add_notifications(db, 3)
    assert min(new_ids) > cursor
    page = api.get(f"/api/v1/notifications?unread_only=true&since_id={cursor}&limit=50").json()
    assert sorted(n["id"] for n in page) == sorted(new_ids), "the poller never sees the new notifications"


def test_state3_existing_notifications_table_is_rebuilt_with_monotonic_ids(api, db, settings):
    ids = _add_notifications(db, 5)
    rule = AlertRule(name="all", exclude_suspicious=False)
    db.add(rule)
    opp = fabricate_opportunity(db)
    linked = Notification(rule_id=rule.id, opportunity_id=opp.id, kind="opportunity", title="linked", body="b",
                          delivery_json={"in_app": "ok"})
    db.add(linked)
    db.commit()
    ids_all, linked_id, opp_id = [*ids, linked.id], linked.id, opp.id
    db.close()
    con = sqlite3.connect(str(settings.sqlite_path))  # the table as the previous release created it
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("ALTER TABLE notifications RENAME TO notifications_current")
    con.execute("DROP INDEX ix_notifications_created_at")
    con.execute("DROP INDEX ix_notifications_opportunity_id")
    con.execute(LEGACY_NOTIFICATIONS_DDL)
    con.execute("CREATE INDEX ix_notifications_created_at ON notifications (created_at)")
    con.execute("CREATE INDEX ix_notifications_opportunity_id ON notifications (opportunity_id)")
    con.execute("INSERT INTO notifications SELECT * FROM notifications_current")
    con.execute("DROP TABLE notifications_current")
    con.commit()
    con.close()

    with session_scope() as s:
        bootstrap.bootstrap(s, settings)  # every start-up
    with session_scope() as s:
        assert bootstrap.ensure_monotonic_notification_ids(s) is False  # already migrated: idempotent
        rows = s.scalars(select(Notification).order_by(Notification.id)).all()
        assert [(n.id, n.title) for n in rows] == [*[(i, f"n{k}") for k, i in enumerate(ids)], (linked_id, "linked")]
        kept = s.get(Notification, linked_id)
        assert (kept.opportunity_id, kept.delivery_json) == (opp_id, {"in_app": "ok"})
        s.delete(s.get(Opportunity, opp_id))  # ON DELETE CASCADE survived the rebuild
    with session_scope() as s:
        assert s.get(Notification, linked_id) is None
    con = sqlite3.connect(str(settings.sqlite_path))
    ddl = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='notifications'").fetchone()[0]
    indexes = {row[1] for row in con.execute("PRAGMA index_list(notifications)")}
    con.close()
    assert "AUTOINCREMENT" in ddl.upper()
    assert {"ix_notifications_created_at", "ix_notifications_opportunity_id"} <= indexes

    db2 = SessionLocal()
    try:
        db2.execute(delete(Notification).where(Notification.id > ids[1]))
        db2.commit()
        new_ids = _add_notifications(db2, 2)
    finally:
        db2.close()
    assert min(new_ids) > max(ids_all)


# ------------------------------------------------------------------------------------------------
# state#4 - collection runs never stay 'running' forever
# ------------------------------------------------------------------------------------------------


class HangingCollector(Collector):
    """Test double: awaits the network forever (until its task is cancelled)."""

    info = CollectorInfo(
        key="hangsource", name="Hanging marketplace", kind="marketplace", country="MX", currency="MXN",
        default_fee_profile_key="ml_clasica", requires_credentials=False, docs_url=None, capabilities=["search"],
    )
    started: asyncio.Event | None = None

    def is_configured(self) -> bool:
        return True

    async def search(self, query: str, *, limit: int = 50, category_id: str | None = None):
        assert type(self).started is not None
        type(self).started.set()
        await asyncio.sleep(3600)
        return []  # pragma: no cover


@pytest.fixture
def hanging_collector(client):
    registry.register(HangingCollector)
    with session_scope() as session:
        bootstrap.seed_sources(session)
    yield HangingCollector
    registry.unregister(HangingCollector.info.key)


def test_state4_scheduler_stop_during_a_network_call_finishes_the_run(hanging_collector, settings):
    with session_scope() as s:
        s.add(TrackedSearch(query="ps5", source_keys=["hangsource"], interval_minutes=60, max_results=5))

    async def main():
        HangingCollector.started = asyncio.Event()
        task = scheduler.start(settings)
        await asyncio.wait_for(HangingCollector.started.wait(), 10)
        await scheduler.stop(task)  # server shutdown / restart while the collector awaits the API

    asyncio.run(main())
    with session_scope() as s:
        run = s.scalars(select(CollectionRun).order_by(CollectionRun.id.desc())).first()
        assert run.status == "error" and run.finished_at is not None, run.status
        assert "interrupted" in (run.error or "")
        source = s.scalars(select(Source).where(Source.key == "hangsource")).one()
        assert source.last_status == "error" and source.last_run_at is not None


def test_state4_cancelled_run_collection_finishes_the_run(hanging_collector, settings):
    async def main():
        HangingCollector.started = asyncio.Event()
        session = SessionLocal()
        try:
            task = asyncio.create_task(collection.run_collection(session, "q", ["hangsource"], 5, analyze=False))
            await asyncio.wait_for(HangingCollector.started.wait(), 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            session.close()

    asyncio.run(main())
    with session_scope() as s:
        run = s.scalars(select(CollectionRun).order_by(CollectionRun.id.desc())).first()
        assert run.status == "error" and run.finished_at is not None


def test_state4_stale_running_runs_are_closed_by_the_scheduler_and_at_startup(client, db, settings):
    source = db.scalars(select(Source).where(Source.key == "manual")).one()
    now = utcnow()
    old = CollectionRun(source_id=source.id, query="old", started_at=now - timedelta(hours=2), status="running")
    recent = CollectionRun(source_id=source.id, query="recent", started_at=now - timedelta(minutes=1),
                           status="running")
    db.add_all([old, recent])
    db.commit()

    asyncio.run(scheduler.tick(settings))
    db.expire_all()
    assert old.status == "error" and old.finished_at is not None and "interrupted" in old.error
    assert recent.status == "running"  # may still be running (another process)
    assert source.last_status == "error"

    crashed = CollectionRun(query="crashed", started_at=now - timedelta(hours=3), status="running")
    db.add(crashed)
    db.commit()
    with session_scope() as s:
        bootstrap.bootstrap(s, settings)
    db.expire_all()
    assert crashed.status == "error" and recent.status == "running"


# ------------------------------------------------------------------------------------------------
# engine_matching#6 - unknown international shipping never puts an import into a cheaper duty bracket
# ------------------------------------------------------------------------------------------------

RATE = 18.2142


def _ebay(price_cents: int, shipping_cents: int | None, location: str | None = None) -> Listing:
    source = Source(key="ebay", name="eBay", kind="marketplace", country="US", currency="USD")
    attributes = {"source_attributes": {"item_location_country": location}} if location else {}
    return Listing(
        id=1, source=source, external_id="E1", title="Apple iPhone 13 128GB used", currency="USD",
        price_cents=price_cents, price_mxn_cents=int(Decimal(price_cents) * Decimal(repr(RATE))), fx_rate=RATE,
        fx_source="manual", shipping_cents=shipping_cents, condition="used", status="active",
        attributes_json=attributes, risk_flags_json=[], seller_json={}, last_seen_at=T0, first_seen_at=T0,
    )


def _duties(usd_cents: int, rate_bp: int) -> int:
    value = Decimal(usd_cents) * Decimal(repr(RATE)) * Decimal(rate_bp) / Decimal(10_000)
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def test_em6_unknown_shipping_uses_the_highest_courier_rate_the_item_can_reach():
    # Known shipping: USD 45 + 5 = 50 is in the exempt bracket (unchanged).
    required, duties, note = analysis.import_estimate(_ebay(4_500, 500))
    assert required is True and duties == 0

    # No shipping quote: the real customs value (item + shipping) is unknown, so the exempt bracket of the item
    # price alone must not be assumed (USD 45 + ~25 shipping pays 17 %).  The highest courier rate is used.
    required, duties, note = analysis.import_estimate(_ebay(4_500, None))
    assert required is True and duties == _duties(4_500, 1900) == 15_573
    assert "not reported" in note and "19.00%" in note

    # Above USD 117 the bracket is already the highest one: same as before.
    assert analysis.import_estimate(_ebay(20_000, None))[1] == _duties(20_000, 1900)
    # Non-FTA origins have a single courier rate.
    assert analysis.import_estimate(_ebay(4_500, None, location="CN"))[1] == _duties(4_500, 3350)
    # Above the courier limit a formal import is still required (no estimate).
    assert analysis.import_estimate(_ebay(260_000, None))[1] is None
