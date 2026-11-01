"""Business analytics computed from YOUR recorded purchases/sales and stored opportunities.

Everything here is a deterministic aggregation of rows in the database; nothing is estimated.  Buckets
are calendar periods in UTC.  Demo opportunities/listings are excluded unless ``include_demo_data``;
opportunities with a critical risk flag (likely scam/replica) are never counted as open/potential profit.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..models import CollectionRun, InventoryItem, Listing, Opportunity, OpportunityStatus, Sale, Source
from ..schemas import AnalyticsSummaryOut, TimeseriesOut, TimeseriesPoint
from . import settings_store
from .inventory import OPEN_STATUSES, total_cost_cents
from .timeutil import as_utc, utcnow

OPEN_OPPORTUNITY_STATUSES = (OpportunityStatus.NEW.value, OpportunityStatus.SEEN.value, OpportunityStatus.WATCHING.value)
AGING_BUCKETS = ("0-7", "8-30", "31-90", "90+")


def _half_up(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def aging_bucket(days: int) -> str:
    if days <= 7:
        return "0-7"
    if days <= 30:
        return "8-30"
    if days <= 90:
        return "31-90"
    return "90+"


def summary(session: Session, days: int = 30, now: datetime | None = None, settings: Settings | None = None) -> AnalyticsSummaryOut:
    now = as_utc(now or utcnow())
    start = now - timedelta(days=days)
    include_demo = bool(settings_store.get_value(session, "include_demo_data", settings))

    sales = session.scalars(
        select(Sale).options(selectinload(Sale.item)).where(Sale.sold_at >= start, Sale.sold_at <= now).order_by(Sale.id)
    ).all()
    revenue = sum(s.unit_price_cents * s.quantity for s in sales)
    profit = sum(s.profit_cents for s in sales)
    units = sum(s.quantity for s in sales)
    basis = sum(s.cost_basis_cents for s in sales)
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
    top = sorted(by_product.values(), key=lambda e: (-e["profit_cents"], e["title"]))[:5]

    items = session.scalars(
        select(InventoryItem).where(InventoryItem.status.in_(OPEN_STATUSES), InventoryItem.quantity_available > 0)
    ).all()
    aging = dict.fromkeys(AGING_BUCKETS, 0)
    inventory_units = inventory_cost = 0
    target_value: int | None = None
    for item in items:
        qa = int(item.quantity_available)
        inventory_units += qa
        inventory_cost += _half_up(Decimal(total_cost_cents(item)) * qa / Decimal(max(1, item.quantity)))
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


def timeseries(session: Session, days: int = 90, bucket: Bucket = "week", now: datetime | None = None) -> TimeseriesOut:
    now = as_utc(now or utcnow())
    start = now - timedelta(days=days)
    first = bucket_start(start.date(), bucket)
    last = bucket_start(now.date(), bucket)
    points: dict[date, dict[str, int]] = {}
    cursor = first
    while cursor <= last:
        points[cursor] = {"revenue_cents": 0, "profit_cents": 0, "units": 0, "purchases_cents": 0}
        cursor = _next(cursor, bucket)

    for sale in session.scalars(select(Sale).where(Sale.sold_at >= start, Sale.sold_at <= now)):
        key = bucket_start(as_utc(sale.sold_at).date(), bucket)
        if key in points:
            points[key]["revenue_cents"] += sale.unit_price_cents * sale.quantity
            points[key]["profit_cents"] += sale.profit_cents
            points[key]["units"] += sale.quantity
    for item in session.scalars(
        select(InventoryItem).where(InventoryItem.purchased_at >= start, InventoryItem.purchased_at <= now)
    ):
        key = bucket_start(as_utc(item.purchased_at).date(), bucket)
        if key in points:
            points[key]["purchases_cents"] += total_cost_cents(item)

    return TimeseriesOut(
        bucket=bucket,
        points=[TimeseriesPoint(period=k.isoformat(), **v) for k, v in sorted(points.items())],
    )
