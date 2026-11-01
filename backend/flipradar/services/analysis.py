"""Recompute risk flags and opportunities for products (DB <-> pure engine glue).

For every product:

1. **Risk pass** - for each listing, pick its comparables (engine ``select_comparables``), compute
   comparable stats when at least ``min_comparables`` exist and call ``engine.risk.assess_listing``.
   Flags are persisted on the listing; ``warning``/``critical`` => ``Listing.is_suspicious``.
2. **Opportunity pass** - ``engine.opportunities.analyze_product`` with the default fee profile, fresh
   risk codes and the last 30 days of price observations.
3. **Persist** - upsert Opportunity rows (unique per buy listing + fee profile).  User decisions are
   preserved (``seen``/``watching``/``dismissed``/``bought`` keep their status); opportunities that are no
   longer returned become ``expired`` unless the user bought or dismissed them.
4. **Alerts** - created/changed opportunities are evaluated against alert rules (committed first,
   delivered afterwards so slow push channels never hold the SQLite write lock).

Demo products (``demo|`` keys) are always analysed with ``include_demo=True`` so the synthetic dataset
works, but they never share a cluster with real listings.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings, get_settings
from ..engine import opportunities as opp_engine
from ..engine import risk as risk_engine
from ..engine import stats as stats_engine
from ..engine.opportunities import AnalysisSettings
from ..engine.types import ListingView, OpportunityCandidate, RiskFlag
from ..finance.calculator import FeeModel
from ..models import (
    FeeProfile,
    Listing,
    ListingStatus,
    Opportunity,
    OpportunityStatus,
    PriceObservation,
    Product,
)
from ..schemas import RecomputeResult
from . import alerts as alerts_service
from . import settings_store
from .errors import Invalid
from .ingestion import is_demo_product_key
from .jsonutil import jsonable
from .timeutil import as_utc, utcnow

log = logging.getLogger(__name__)

OBSERVATION_WINDOW = timedelta(days=30)
SUSPICIOUS_SEVERITIES = frozenset({"warning", "critical"})
NEVER_EXPIRE = frozenset({OpportunityStatus.BOUGHT.value, OpportunityStatus.DISMISSED.value,
                          OpportunityStatus.EXPIRED.value})
_COMPARE_FIELDS = ("buy_total_cents", "expected_sale_cents", "net_profit_cents", "roi_bp", "margin_bp", "score",
                   "confidence", "comparable_count", "risk_flags_json", "fee_profile_key")


def default_fee_model(session: Session, settings: Settings | None = None) -> FeeModel:
    key = settings_store.get_value(session, "default_fee_profile_key", settings)
    row = session.scalars(select(FeeProfile).where(FeeProfile.key == key)).first()
    if row is None:
        row = session.scalars(select(FeeProfile).where(FeeProfile.key == "ml_clasica")).first()
    if row is None:
        row = session.scalars(select(FeeProfile).order_by(FeeProfile.id)).first()
    if row is None:
        raise Invalid("no fee profiles configured; run 'flipradar init'")
    return FeeModel.from_profile(row)


def shipping_mxn_cents(listing: Listing) -> int | None:
    if listing.shipping_cents is None:
        return None
    if (listing.currency or "MXN").upper() == "MXN":
        return int(listing.shipping_cents)
    if listing.fx_rate is None:
        return None
    rate = Decimal(repr(listing.fx_rate))
    return int((Decimal(listing.shipping_cents) * rate).to_integral_value(rounding=ROUND_HALF_UP))


ViewPair = tuple[Listing, ListingView]


def _risk_codes(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"code": f.get("code"), "severity": f.get("severity")} for f in flags if isinstance(f, dict)]


def listing_view(listing: Listing, product_key: str | None, now: datetime) -> ListingView | None:
    """ListingView for the engine, or None when the listing has no MXN price (not comparable)."""
    if listing.price_mxn_cents is None:
        return None
    last_seen = as_utc(listing.last_seen_at) if listing.last_seen_at else now
    first_seen = as_utc(listing.first_seen_at) if listing.first_seen_at else None
    flags = list(listing.risk_flags_json or [])
    attributes = dict(listing.attributes_json or {})
    attributes["risk_codes"] = _risk_codes(flags)
    attributes["risk_flags"] = flags
    attributes["hours_since_seen"] = max(0, int((now - last_seen).total_seconds() // 3600))
    return ListingView(
        id=listing.id,
        source_key=listing.source.key if listing.source is not None else "",
        title=listing.title,
        price_mxn_cents=int(listing.price_mxn_cents),
        shipping_mxn_cents=shipping_mxn_cents(listing),
        condition=listing.condition,
        product_key=product_key,
        match_confidence=listing.match_confidence,
        observed_at=last_seen,
        first_seen_at=first_seen,
        url=listing.url,
        status=listing.status,
        seller_id=listing.seller_id,
        seller=dict(listing.seller_json or {}),
        description=listing.description,
        is_accessory=bool(listing.is_accessory),
        is_bundle=bool(listing.is_bundle),
        available_quantity=listing.available_quantity,
        sold_quantity=listing.sold_quantity,
        is_demo=bool(listing.is_demo),
        attributes=attributes,
    )


def _assess_risk(views: list[ViewPair], a: AnalysisSettings, now: datetime) -> None:
    pool = [v for _, v in views]
    for listing, view in views:
        comparables, excluded = stats_engine.select_comparables(
            view, pool, now=now, max_age_days=a.comparable_max_age_days, include_demo=a.include_demo
        )
        stats = None
        if len(comparables) >= a.min_comparables:
            stats = stats_engine.comparable_stats(comparables, excluded, a.comparable_max_age_days)
        flags: list[RiskFlag] = risk_engine.assess_listing(view, stats)
        flag_dicts = [jsonable(f.to_dict()) for f in flags]
        listing.risk_flags_json = flag_dicts
        listing.is_suspicious = any(f.severity in SUSPICIOUS_SEVERITIES for f in flags)
    # Second pass sees the fresh flags.
    for listing, view in views:
        view.attributes["risk_codes"] = _risk_codes(list(listing.risk_flags_json or []))
        view.attributes["risk_flags"] = list(listing.risk_flags_json or [])


def _observations(session: Session, listing_ids: list[int], now: datetime) -> dict[int, list[tuple[datetime, int | None, int | None, str]]]:
    result: dict[int, list[tuple[datetime, int | None, int | None, str]]] = {lid: [] for lid in listing_ids}
    if not listing_ids:
        return result
    since = now - OBSERVATION_WINDOW
    for i in range(0, len(listing_ids), 500):
        chunk = listing_ids[i:i + 500]
        rows = session.scalars(
            select(PriceObservation)
            .where(PriceObservation.listing_id.in_(chunk), PriceObservation.observed_at >= since)
            .order_by(PriceObservation.listing_id, PriceObservation.observed_at, PriceObservation.id)
        )
        for obs in rows:
            result[obs.listing_id].append((as_utc(obs.observed_at), obs.price_mxn_cents, obs.sold_quantity, obs.status))
    return result


def _candidate_values(cand: OpportunityCandidate) -> dict[str, Any]:
    return {
        "buy_total_cents": int(cand.buy_total_cents),
        "expected_sale_cents": int(cand.expected_sale_cents),
        "net_profit_cents": int(cand.net_profit_cents),
        "roi_bp": int(cand.roi_bp),
        "margin_bp": int(cand.margin_bp),
        "score": float(cand.score),
        "confidence": str(cand.confidence),
        "comparable_count": int(cand.stats.n),
        "comparable_listing_ids": [int(i) for i in (cand.stats.comparable_ids or [])],
        "price_stats_json": jsonable(cand.stats.to_dict()),
        "cost_breakdown_json": jsonable(cand.profit),
        "score_components_json": [jsonable(c.to_dict()) for c in cand.components],
        "risk_flags_json": [jsonable(f.to_dict()) for f in cand.risk_flags],
        "demand_json": jsonable(cand.demand.to_dict()),
        "explanation": cand.explanation or "",
        "explanation_facts_json": jsonable(cand.facts or {}),
        "is_demo": bool(cand.buy.is_demo),
    }


def _products_to_analyze(session: Session, product_ids: Iterable[int] | None) -> list[Product]:
    stmt = select(Product).order_by(Product.id)
    if product_ids is not None:
        ids = sorted({int(i) for i in product_ids})
        if not ids:
            return []
        products: list[Product] = []
        for i in range(0, len(ids), 500):
            products.extend(session.scalars(stmt.where(Product.id.in_(ids[i:i + 500]))))
        return products
    with_listings = select(Listing.product_id).where(Listing.product_id.is_not(None)).distinct()
    return list(session.scalars(stmt.where(Product.id.in_(with_listings))))


def analyze_one_product(
    session: Session,
    product: Product,
    fee_model: FeeModel,
    base_settings: AnalysisSettings,
    now: datetime,
) -> tuple[list[Opportunity], int, int, int]:
    """Analyse one product.  Returns (created_or_changed_opps, created, updated, expired).  Flushes."""
    demo_product = is_demo_product_key(product.canonical_key)
    a = dataclasses.replace(base_settings, include_demo=True) if demo_product else base_settings
    listings = session.scalars(
        select(Listing).options(selectinload(Listing.source)).where(Listing.product_id == product.id).order_by(Listing.id)
    ).all()
    pairs: list[ViewPair] = []
    for listing in listings:
        view = listing_view(listing, product.canonical_key, now)
        if view is not None:
            pairs.append((listing, view))
        else:
            listing.risk_flags_json = []
            listing.is_suspicious = False
    if pairs:
        _assess_risk(pairs, a, now)
    views = [v for _, v in pairs]
    listing_by_id = {listing.id: listing for listing, _ in pairs}
    candidates: list[OpportunityCandidate] = []
    if views:
        observations = _observations(session, [v.id for v in views], now)
        candidates = opp_engine.analyze_product(product.canonical_key, views, fee_model, a, observations, now=now)
    candidates = [
        c for c in candidates
        if c.buy.id in listing_by_id and listing_by_id[c.buy.id].status == ListingStatus.ACTIVE.value
    ]

    listing_ids = [listing.id for listing in listings]
    own_listing_ids = set(listing_ids)
    existing: dict[tuple[int, str], Opportunity] = {}
    for opp in session.scalars(select(Opportunity).where(Opportunity.product_id == product.id)):
        existing[(opp.buy_listing_id, opp.fee_profile_key)] = opp
    for i in range(0, len(listing_ids), 500):
        for opp in session.scalars(select(Opportunity).where(Opportunity.buy_listing_id.in_(listing_ids[i:i + 500]))):
            existing.setdefault((opp.buy_listing_id, opp.fee_profile_key), opp)

    touched: list[Opportunity] = []
    created = updated = expired = 0
    returned: set[tuple[int, str]] = set()
    for cand in candidates:
        key = (cand.buy.id, cand.fee_profile_key)
        if key in returned:
            continue
        returned.add(key)
        values = _candidate_values(cand)
        opp = existing.get(key)
        if opp is None:
            opp = Opportunity(
                product_id=product.id,
                buy_listing_id=cand.buy.id,
                fee_profile_key=cand.fee_profile_key,
                status=OpportunityStatus.NEW.value,
                **values,
            )
            session.add(opp)
            created += 1
            touched.append(opp)
            continue
        changed = any(getattr(opp, f) != values[f] for f in _COMPARE_FIELDS if f in values)
        if opp.explanation_facts_json != values["explanation_facts_json"]:
            changed = True
            opp.ai_summary = None  # a summary of old facts must not survive a recompute
        if opp.product_id != product.id:
            opp.product_id = product.id
            changed = True
        for name, value in values.items():
            setattr(opp, name, value)
        if opp.status == OpportunityStatus.EXPIRED.value:
            opp.status = OpportunityStatus.NEW.value  # it is back on the market
            changed = True
        if changed:
            updated += 1
            touched.append(opp)
    for key, opp in existing.items():
        if key in returned or opp.status in NEVER_EXPIRE:
            continue
        if opp.product_id != product.id and opp.buy_listing_id not in own_listing_ids:
            continue  # belongs to another product analysed separately
        opp.status = OpportunityStatus.EXPIRED.value
        expired += 1
    session.flush()
    return touched, created, updated, expired


def recompute(
    session: Session,
    product_ids: Iterable[int] | None = None,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    dispatch: bool = True,
) -> RecomputeResult:
    """Recompute risk + opportunities for ``product_ids`` (None = every product with listings).

    Commits.  Alert notifications are created for new/changed opportunities and then delivered.
    """
    settings = settings or get_settings()
    now = as_utc(now or utcnow())
    base = settings_store.analysis_settings(session, settings)
    fee_model = default_fee_model(session, settings)
    products = _products_to_analyze(session, product_ids)

    touched: list[Opportunity] = []
    created = updated = expired = 0
    for product in products:
        opps, c, u, e = analyze_one_product(session, product, fee_model, base, now)
        touched.extend(opps)
        created += c
        updated += u
        expired += e
    session.commit()

    notifications = alerts_service.evaluate(session, touched, settings, dispatch=False)
    session.commit()
    if dispatch and notifications:
        alerts_service.deliver(session, notifications, settings)
        session.commit()
    return RecomputeResult(
        products_analyzed=len(products),
        opportunities_created=created,
        opportunities_updated=updated,
        opportunities_expired=expired,
        notifications_created=len(notifications),
    )
