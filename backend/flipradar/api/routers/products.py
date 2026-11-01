"""Product clusters with observed price statistics."""

from __future__ import annotations

import dataclasses
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ...config import Settings
from ...engine import stats as stats_engine
from ...models import Listing, Product
from ...schemas import MAX_ID, Page, PriceStatsOut, ProductDetailOut, ProductOut
from ...services import settings_store
from ...services.analysis import shipping_mxn_cents
from ...services.ingestion import DEMO_KEY_PREFIX
from ...services.serialize import price_stats_out, product_detail_out, product_out
from ...services.timeutil import as_utc, utcnow
from ..deps import MAX_PAGE_SIZE, get_db, get_settings
from ._common import count, include_demo, like_pattern

log = logging.getLogger(__name__)
router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=Page[ProductOut])
def list_products(
    q: str | None = Query(None, max_length=200),
    category: str | None = Query(None, max_length=80),
    include_demo_data: bool | None = Query(None, alias="include_demo"),
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0, le=MAX_ID),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Page[ProductOut]:
    stmt = select(Product).where(Product.id.in_(select(Listing.product_id).where(Listing.product_id.is_not(None))))
    if q and q.strip():
        pattern = like_pattern(q)
        stmt = stmt.where(or_(func.lower(Product.title).like(pattern, escape="\\"),
                              Product.canonical_key.like(pattern, escape="\\")))
    if category:
        stmt = stmt.where(func.lower(Product.category) == category.strip().lower())
    if not include_demo(db, settings, include_demo_data):
        stmt = stmt.where(~Product.canonical_key.like(f"{DEMO_KEY_PREFIX}%"))
    total = count(db, stmt)
    products = db.scalars(stmt.order_by(Product.updated_at.desc(), Product.id.desc()).limit(limit).offset(offset)).all()
    by_product: dict[int, list[Listing]] = defaultdict(list)
    ids = [p.id for p in products]
    if ids:
        for listing in db.scalars(select(Listing).where(Listing.product_id.in_(ids))):
            by_product[listing.product_id].append(listing)  # type: ignore[index]
    items = [product_out(p, by_product.get(p.id, [])) for p in products]
    return Page[ProductOut](items=items, total=total, limit=limit, offset=offset)


def _stats_by_condition(listings: list[Listing], max_age_days: int) -> dict[str, PriceStatsOut]:
    """Robust statistics of observed, fresh, active, priced listings per condition (no estimation)."""
    now = utcnow()
    groups: dict[str, list[Listing]] = defaultdict(list)
    for listing in listings:
        if (listing.status != "active" or listing.price_mxn_cents is None or listing.is_accessory
                or listing.is_bundle or (now - as_utc(listing.last_seen_at)).days > max_age_days):
            continue
        groups[listing.condition or "unknown"].append(listing)
    out: dict[str, PriceStatsOut] = {}
    for condition, group in sorted(groups.items()):
        totals = [int(x.price_mxn_cents) + (shipping_mxn_cents(x) or 0) for x in group]  # type: ignore[arg-type]
        try:
            stats = stats_engine.robust_stats(totals)
        except NotImplementedError:
            log.debug("robust_stats not implemented yet")
            return {}
        stats = dataclasses.replace(
            stats,
            source_count=len({x.source_id for x in group}),
            seller_count=len({x.seller_id or f"listing:{x.id}" for x in group}),
            comparable_ids=[x.id for x in group],
            window_days=max_age_days,
        )
        out[condition] = price_stats_out(stats.to_dict(), len(group))
    return out


@router.get("/{product_id}", response_model=ProductDetailOut)
def get_product(product_id: int, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ProductDetailOut:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, f"product {product_id} not found")
    listings = list(
        db.scalars(
            select(Listing).options(selectinload(Listing.source)).where(Listing.product_id == product.id)
            .order_by(Listing.price_mxn_cents.is_(None), Listing.price_mxn_cents, Listing.id)
        )
    )
    max_age = int(settings_store.get_value(db, "comparable_max_age_days", settings))
    return product_detail_out(product, listings, _stats_by_condition(listings, max_age))
