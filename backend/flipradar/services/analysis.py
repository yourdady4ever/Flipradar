"""Recompute risk flags and opportunities for products (DB <-> pure engine glue).

For every product:

1. **Risk pass** - for each listing, pick its comparables (engine ``select_comparables``), compute
   comparable stats when at least ``min_comparables`` exist and call ``engine.risk.assess_listing``.
   Flags are persisted on the listing; ``engine.risk.is_suspicious`` (any critical flag or >= 2
   warnings) => ``Listing.is_suspicious``.
2. **Opportunity pass** - ``engine.opportunities.analyze_product`` with the resale fee profile chosen by
   the buy listing's condition (``default_fee_profile_key`` for new/refurbished,
   ``default_fee_profile_key_used`` for used/unknown), fresh risk codes and the last 30 days of price
   observations.  Only listings from Mexican marketplace/manual sources are resale comparables
   (``ListingView.resale_reference``); eBay US, deal feeds and single retailer pages are buy-side only.
   Items that ship from abroad carry estimated Mexican import duties (``finance.import_costs``, courier
   regime: USMCA rate for US/CA origins, non-FTA rate otherwise, at the FX rate stored on the listing).
3. **Persist** - upsert Opportunity rows (unique per buy listing + fee profile).  User decisions are
   preserved (``seen``/``watching``/``dismissed``/``bought`` keep their status), also when the fee profile
   key changes (the existing row of the buy listing is re-keyed in place) or the listing moves to another
   product; opportunities that are no longer returned become ``expired`` unless the user bought or
   dismissed them.
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
from ..engine.explain import fmt_money, fmt_pct_bp
from ..engine.opportunities import AnalysisSettings
from ..engine.types import ListingView, OpportunityCandidate, RiskFlag
from ..finance.calculator import FeeModel
from ..finance.import_costs import estimate_import_costs, select_rule
from ..models import (
    FeeProfile,
    Listing,
    ListingStatus,
    Opportunity,
    OpportunityStatus,
    PriceObservation,
    Product,
    Source,
    SourceKind,
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
NEVER_EXPIRE = frozenset({OpportunityStatus.BOUGHT.value, OpportunityStatus.DISMISSED.value,
                          OpportunityStatus.EXPIRED.value})
_COMPARE_FIELDS = ("buy_total_cents", "expected_sale_cents", "net_profit_cents", "roi_bp", "margin_bp", "score",
                   "confidence", "comparable_count", "risk_flags_json", "fee_profile_key")
# Which row of a buy listing inherits a new fee profile key: the strongest user decision wins.
_STATUS_PRIORITY = {
    OpportunityStatus.BOUGHT.value: 0,
    OpportunityStatus.DISMISSED.value: 1,
    OpportunityStatus.WATCHING.value: 2,
    OpportunityStatus.SEEN.value: 3,
    OpportunityStatus.NEW.value: 4,
    OpportunityStatus.EXPIRED.value: 5,
}


RESALE_SOURCE_KINDS = frozenset({SourceKind.MARKETPLACE.value, SourceKind.MANUAL.value, SourceKind.DEMO.value})


def is_resale_reference(source: Source | None) -> bool:
    """True when listings of ``source`` represent the Mexican RESALE market (comparables).

    Mexican marketplaces and user-entered data (manual/CSV - e.g. Facebook Marketplace prices you
    recorded) qualify.  Foreign marketplaces (eBay US), deal feeds and single retailer pages are
    purchase-side evidence only.
    """
    if source is None:
        return True
    return source.kind in RESALE_SOURCE_KINDS and (source.country or "MX").upper() == "MX"


def default_fee_model(
    session: Session, settings: Settings | None = None, *, setting_key: str = "default_fee_profile_key"
) -> FeeModel:
    key = settings_store.get_value(session, setting_key, settings)
    row = session.scalars(select(FeeProfile).where(FeeProfile.key == key)).first()
    if row is None:
        fallback = settings_store.DEFAULTS[setting_key](settings or get_settings())
        log.warning("fee profile %r (%s) not found; falling back to %r", key, setting_key, fallback)
        row = session.scalars(select(FeeProfile).where(FeeProfile.key == fallback)).first()
    if row is None:
        row = session.scalars(select(FeeProfile).where(FeeProfile.key == "ml_clasica")).first()
    if row is None:
        row = session.scalars(select(FeeProfile).order_by(FeeProfile.id)).first()
    if row is None:
        raise Invalid("no fee profiles configured; run 'flipradar init'")
    return FeeModel.from_profile(row)


USMCA_ORIGINS = frozenset({"US", "CA"})


def origin_country(listing: Listing) -> str:
    """ISO country the item ships from: the source-reported item location (eBay ``item_location_country``)
    when present, else the source's country (MX when unknown)."""
    source_attributes = (listing.attributes_json or {}).get("source_attributes")
    if isinstance(source_attributes, dict):
        location = source_attributes.get("item_location_country")
        if isinstance(location, str) and len(location.strip()) == 2:
            return location.strip().upper()
    country = listing.source.country if listing.source is not None else None
    return (country or "MX").upper()


def import_estimate(listing: Listing) -> tuple[bool, int | None, str | None]:
    """(import required, estimated duties in MXN centavos or None, note) for buying ``listing``.

    Items shipping from outside Mexico pay the courier-regime global rate on the customs value (item
    price + international shipping, USD) at the USD->MXN rate stored on the listing.  Duties are None
    (never guessed) when they cannot be estimated: formal import above the courier limit, or no USD price.
    """
    origin = origin_country(listing)
    if origin == "MX":
        return False, None, None
    rule_key = "courier_usmca" if origin in USMCA_ORIGINS else "courier_non_fta"
    if (listing.currency or "").upper() != "USD" or listing.fx_rate is None:
        return True, None, (
            f"Ships from {origin}: import duties cannot be estimated without a USD price and USD/MXN rate, "
            "so no profit is estimated."
        )
    rate = Decimal(repr(listing.fx_rate))
    shipping_usd = int(listing.shipping_cents or 0)
    estimate = estimate_import_costs(int(listing.price_cents), shipping_usd, rule_key, rate,
                                     fx_source=listing.fx_source or "listing")
    customs = f"customs value USD {Decimal(estimate['customs_value_usd_cents']) / 100:,.2f}"
    shipping_text = "item + international shipping" if listing.shipping_cents is not None else (
        "item only; international shipping not reported")
    if select_rule(rule_key, estimate["customs_value_usd_cents"]).formal_import_required:
        return True, None, (
            f"Ships from {origin}: {customs} is above the courier-regime limit, so a formal import is required "
            "and duties cannot be estimated; no profit is estimated."
        )
    note = (
        f"Ships from {origin}: estimated import duties {fmt_money(estimate['duties_mxn_cents'])} = {customs} "
        f"({shipping_text}) x {estimate['usd_mxn_rate']} MXN/USD ({estimate['fx_source']}) x "
        f"{fmt_pct_bp(estimate['duty_rate_bp'])} global rate, rule '{estimate['rule_key']}' as of "
        f"{estimate['as_of'] or 'unknown'}. A customs processing fee (DTA) and carrier brokerage fees may "
        "still apply; not legal advice."
    )
    return True, int(estimate["duties_mxn_cents"]), note


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
    import_required, import_costs, import_note = import_estimate(listing)
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
        resale_reference=is_resale_reference(listing.source),
        import_required=import_required,
        import_costs_mxn_cents=import_costs,
        import_note=import_note,
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
        listing.is_suspicious = risk_engine.is_suspicious(flags)
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
        "risk_level": risk_engine.risk_level(cand.risk_flags),
        "is_suspicious": risk_engine.is_suspicious(cand.risk_flags),
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


def _row_to_rekey(
    existing: dict[tuple[int, str], Opportunity], returned: set[tuple[int, str]], buy_listing_id: int
) -> Opportunity | None:
    """An existing row of ``buy_listing_id`` under another fee profile key (strongest user status first)."""
    rows = [
        opp for key, opp in existing.items()
        if key[0] == buy_listing_id and key not in returned
    ]
    if not rows:
        return None
    return min(rows, key=lambda opp: (_STATUS_PRIORITY.get(opp.status, len(_STATUS_PRIORITY)), -(opp.id or 0)))


def analyze_one_product(
    session: Session,
    product: Product,
    fee_model: FeeModel,
    base_settings: AnalysisSettings,
    now: datetime,
    fee_models_by_condition: dict[str, FeeModel] | None = None,
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
        candidates = opp_engine.analyze_product(
            product.canonical_key, views, fee_model, a, observations, now=now,
            fee_models_by_condition=fee_models_by_condition,
        )
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
        rekeyed = False
        if opp is None:
            # The fee profile key changed (setting or condition class): keep the listing's row and its user
            # status instead of creating a fresh NEW row next to it.
            opp = _row_to_rekey(existing, returned, cand.buy.id)
            if opp is not None:
                del existing[(opp.buy_listing_id, opp.fee_profile_key)]
                opp.fee_profile_key = cand.fee_profile_key
                existing[key] = opp
                rekeyed = True
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
        changed = rekeyed or any(getattr(opp, f) != values[f] for f in _COMPARE_FIELDS if f in values)
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
        if opp.buy_listing_id not in own_listing_ids:
            owner = opp.buy_listing.product_id if opp.buy_listing is not None else None
            if owner is not None and owner != product.id:
                continue  # the listing moved to another product, whose analysis takes this row over
        opp.status = OpportunityStatus.EXPIRED.value
        expired += 1
    session.flush()
    return touched, created, updated, expired


def _expire_stale_buys(session: Session, a: AnalysisSettings, now: datetime) -> int:
    """Expire open opportunities whose buy listing was not seen within the buy window, also for products
    this recompute did not analyse (the engine never returns such a listing as a candidate)."""
    cutoff = now - timedelta(days=min(a.buy_max_age_days, a.comparable_max_age_days))
    stale = session.scalars(
        select(Opportunity)
        .join(Listing, Opportunity.buy_listing_id == Listing.id)
        # A WATCHING opportunity keeps the user's decision: it is not swept (re-sighting would reset it to NEW).
        .where(Opportunity.status.not_in(NEVER_EXPIRE | {OpportunityStatus.WATCHING.value}),
               Listing.last_seen_at < cutoff)
    ).all()
    for opp in stale:
        opp.status = OpportunityStatus.EXPIRED.value
    session.flush()
    return len(stale)


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
    used_model = default_fee_model(session, settings, setting_key="default_fee_profile_key_used")
    by_condition = {"new": fee_model, "refurbished": fee_model, "used": used_model}
    products = _products_to_analyze(session, product_ids)

    touched: list[Opportunity] = []
    created = updated = expired = 0
    failed = 0
    for product in products:
        try:
            with session.begin_nested():  # one broken product must not abort the whole recompute
                opps, c, u, e = analyze_one_product(session, product, fee_model, base, now, by_condition)
        except NotImplementedError:
            raise
        except Exception:
            failed += 1
            log.exception("analysis failed for product %s (%s)", product.id, product.canonical_key)
            continue
        touched.extend(opps)
        created += c
        updated += u
        expired += e
    expired += _expire_stale_buys(session, base, now)
    session.commit()
    if failed:
        log.warning("recompute: %d of %d product(s) failed; see the log", failed, len(products))

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
