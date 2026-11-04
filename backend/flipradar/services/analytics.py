"""Business analytics computed from YOUR recorded purchases/sales and stored opportunities.

Everything here is a deterministic aggregation of rows in the database; nothing is estimated.  Timeseries
buckets are calendar periods (day / ISO week starting Monday / month) in the LOCAL time zone
``settings.timezone`` (default ``America/Mexico_City``): a sale recorded at 20:00 in Mexico City on the 3rd
(02:00 UTC on the 4th) belongs to the 3rd.  Timestamps stay UTC in the database; only the bucketing is
local.  The summary uses a rolling window (``now - days`` .. ``now``), which is the same instant in every time
zone.  Demo opportunities/listings are excluded unless ``include_demo_data``;
opportunities with a critical risk flag (likely scam/replica) are never counted as open/potential profit.

Realized profit = the sales' profit MINUS the cost of stock written off in the period (status
``written_off``, dated ``written_off_at``): a write-off is a realized loss of the units' remaining cost.
``avg_roi_bp`` divides by the cost of the units sold plus the cost written off.  A ``returned`` item is
assumed refunded, so its cost leaves inventory without a loss.  Inventory cost is each open item's
remaining cost (``total cost - cost already carried by its sales``), so sold + remaining == total cost.

Timeseries: the window is extended back to the start (local midnight) of the first bucket, so every point
covers its whole calendar period (the first one is never partial).  Totals can therefore include a few days
more than ``/analytics/summary`` for the same ``days``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings, get_settings
from ..models import (
    CollectionRun,
    InventoryItem,
    InventoryStatus,
    Listing,
    Opportunity,
    OpportunityStatus,
    Sale,
    Source,
)
from ..schemas import AnalyticsSummaryOut, TimeseriesOut, TimeseriesPoint
from . import settings_store
from .inventory import OPEN_STATUSES, remaining_cost_cents, total_cost_cents, written_off_cost_cents
from .timeutil import as_utc, utcnow

log = logging.getLogger(__name__)

OPEN_OPPORTUNITY_STATUSES = (OpportunityStatus.NEW.value, OpportunityStatus.SEEN.value, OpportunityStatus.WATCHING.value)
AGING_BUCKETS = ("0-7", "8-30", "31-90", "90+")


def _half_up(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def local_timezone(settings: Settings | None = None) -> tzinfo:
    """The IANA time zone analytics buckets use (``settings.timezone``).  An unknown name (or a system without
    a time zone database) falls back to UTC with a warning instead of failing every analytics request."""
    name = (settings or get_settings()).timezone
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown time zone %r (FLIPRADAR_TIMEZONE); analytics buckets fall back to UTC", name)
        return UTC


def local_date(moment: datetime, tz: tzinfo) -> date:
    """Calendar date of ``moment`` (aware, or naive UTC as stored by SQLite) in ``tz``."""
    return as_utc(moment).astimezone(tz).date()


def local_midnight(day: date, tz: tzinfo) -> datetime:
    """The UTC instant at which ``day`` starts in ``tz``."""
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def aging_bucket(days: int) -> str:
    if days <= 7:
        return "0-7"
    if days <= 30:
        return "8-30"
    if days <= 90:
        return "31-90"
    return "90+"


def _write_offs(session: Session, start: datetime, end: datetime) -> list[InventoryItem]:
    """Items written off within [start, end] (with their sales loaded, for the remaining cost)."""
    return list(session.scalars(
        select(InventoryItem)
        .options(selectinload(InventoryItem.sales))
        .where(InventoryItem.status == InventoryStatus.WRITTEN_OFF.value, InventoryItem.written_off_at >= start,
               InventoryItem.written_off_at <= end)
        .order_by(InventoryItem.id)
    ))


def summary(session: Session, days: int = 30, now: datetime | None = None, settings: Settings | None = None) -> AnalyticsSummaryOut:
    now = as_utc(now or utcnow())
    start = now - timedelta(days=days)
    include_demo = bool(settings_store.get_value(session, "include_demo_data", settings))

    sales = session.scalars(
        select(Sale).options(selectinload(Sale.item)).where(Sale.sold_at >= start, Sale.sold_at <= now).order_by(Sale.id)
    ).all()
    write_offs = [(item, written_off_cost_cents(item)) for item in _write_offs(session, start, now)]
    written_off = sum(cost for _, cost in write_offs)
    revenue = sum(s.unit_price_cents * s.quantity for s in sales)
    profit = sum(s.profit_cents for s in sales) - written_off
    units = sum(s.quantity for s in sales)
    basis = sum(s.cost_basis_cents for s in sales) + written_off
    avg_roi = _half_up(Decimal(profit) * 10000 / Decimal(basis)) if basis > 0 else None
    held = [(as_utc(s.sold_at) - as_utc(s.item.purchased_at)).total_seconds() / 86400 for s in sales if s.item]
    avg_days = round(sum(max(0.0, h) for h in held) / len(held), 1) if held else None

    by_product: dict[Any, dict[str, Any]] = {}
    for s in sales:
        item = s.item
        key = ("p", item.product_id) if item and item.product_id else ("t", item.title if item else "?")
        entry = by_product.setdefault(
            key, {"product_id": item.product_id if item else None, "title": item.title if item else "?",
                  "profit_cents": 0, "units": 0}
        )
        entry["profit_cents"] += s.profit_cents
        entry["units"] += s.quantity
    for item, cost in write_offs:
        if not cost:
            continue
        key = ("p", item.product_id) if item.product_id else ("t", item.title)
        entry = by_product.setdefault(
            key, {"product_id": item.product_id, "title": item.title, "profit_cents": 0, "units": 0}
        )
        entry["profit_cents"] -= cost
    top = sorted(by_product.values(), key=lambda e: (-e["profit_cents"], e["title"]))[:5]

    items = session.scalars(
        select(InventoryItem)
        .options(selectinload(InventoryItem.sales))
        .where(InventoryItem.status.in_(OPEN_STATUSES), InventoryItem.quantity_available > 0)
    ).all()
    aging = dict.fromkeys(AGING_BUCKETS, 0)
    inventory_units = inventory_cost = 0
    target_value: int | None = None
    for item in items:
        qa = int(item.quantity_available)
        inventory_units += qa
        inventory_cost += remaining_cost_cents(item)
        if item.target_sale_cents is not None:
            target_value = (target_value or 0) + int(item.target_sale_cents) * qa
        aging[aging_bucket(max(0, (now - as_utc(item.purchased_at)).days))] += qa

    opp_stmt = (
        select(Opportunity, Source.key)
        .join(Listing, Listing.id == Opportunity.buy_listing_id)
        .join(Source, Source.id == Listing.source_id)
        .where(Opportunity.status.in_(OPEN_OPPORTUNITY_STATUSES), Opportunity.is_suspicious.is_(False))
    )
    if not include_demo:
        opp_stmt = opp_stmt.where(Opportunity.is_demo.is_(False))
    open_opps = session.execute(opp_stmt).all()
    by_source: dict[str, int] = defaultdict(int)
    for _, source_key in open_opps:
        by_source[source_key] += 1
    scores = [o.score for o, _ in open_opps]

    listing_filter = [] if include_demo else [Listing.is_demo.is_(False)]
    listings_tracked = session.scalar(select(func.count(Listing.id)).where(*listing_filter)) or 0
    products_tracked = session.scalar(
        select(func.count(func.distinct(Listing.product_id))).where(Listing.product_id.is_not(None), *listing_filter)
    ) or 0
    last_run = session.scalar(select(func.max(CollectionRun.finished_at)).where(CollectionRun.status == "ok"))

    return AnalyticsSummaryOut(
        period_days=days,
        revenue_cents=revenue,
        realized_profit_cents=profit,
        units_sold=units,
        sales_count=len(sales),
        avg_roi_bp=avg_roi,
        avg_days_to_sell=avg_days,
        inventory_units=inventory_units,
        inventory_cost_cents=inventory_cost,
        inventory_target_value_cents=target_value,
        aging=aging,
        open_opportunities=len(open_opps),
        avg_open_opportunity_score=round(sum(scores) / len(scores), 1) if scores else None,
        potential_profit_open_cents=sum(int(o.net_profit_cents) for o, _ in open_opps),
        opportunities_by_source=dict(sorted(by_source.items())),
        top_products_by_profit=top,
        listings_tracked=int(listings_tracked),
        products_tracked=int(products_tracked),
        last_collection_at=as_utc(last_run) if last_run else None,
    )


Bucket = Literal["day", "week", "month"]


def bucket_start(day: date, bucket: Bucket) -> date:
    if bucket == "day":
        return day
    if bucket == "week":
        return day - timedelta(days=day.weekday())  # ISO week, Monday start
    return day.replace(day=1)


def _next(day: date, bucket: Bucket) -> date:
    if bucket == "day":
        return day + timedelta(days=1)
    if bucket == "week":
        return day + timedelta(days=7)
    return date(day.year + (day.month // 12), day.month % 12 + 1, 1)


def timeseries(
    session: Session,
    days: int = 90,
    bucket: Bucket = "week",
    now: datetime | None = None,
    settings: Settings | None = None,
) -> TimeseriesOut:
    """Per-period totals in local calendar periods (``settings.timezone``; see the module docstring)."""
    now = as_utc(now or utcnow())
    tz = local_timezone(settings)
    first = bucket_start(local_date(now - timedelta(days=days), tz), bucket)
    start = local_midnight(first, tz)  # whole first local period, never a partial bucket
    last = bucket_start(local_date(now, tz), bucket)
    points: dict[date, dict[str, int]] = {}
    cursor = first
    while cursor <= last:
        points[cursor] = {"revenue_cents": 0, "profit_cents": 0, "units": 0, "purchases_cents": 0}
        cursor = _next(cursor, bucket)

    for sale in session.scalars(select(Sale).where(Sale.sold_at >= start, Sale.sold_at <= now)):
        key = bucket_start(local_date(sale.sold_at, tz), bucket)
        if key in points:
            points[key]["revenue_cents"] += sale.unit_price_cents * sale.quantity
            points[key]["profit_cents"] += sale.profit_cents
            points[key]["units"] += sale.quantity
    for item in session.scalars(
        select(InventoryItem).where(InventoryItem.purchased_at >= start, InventoryItem.purchased_at <= now)
    ):
        key = bucket_start(local_date(item.purchased_at, tz), bucket)
        if key in points:
            points[key]["purchases_cents"] += total_cost_cents(item)
    for item in _write_offs(session, start, now):
        key = bucket_start(local_date(item.written_off_at, tz), bucket)
        if key in points:
            points[key]["profit_cents"] -= written_off_cost_cents(item)

    return TimeseriesOut(
        bucket=bucket,
        points=[TimeseriesPoint(period=k.isoformat(), **v) for k, v in sorted(points.items())],
    )
