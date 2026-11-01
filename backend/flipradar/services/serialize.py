"""ORM row -> API schema conversion (tolerant of partially filled JSON columns)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import ValidationError

from ..collectors.base import CollectorInfo
from ..models import FeeProfile, Listing, Opportunity, PriceObservation, Product, Source
from ..schemas import (
    DemandOut,
    FeeProfileOut,
    FixedFeeTierIO,
    ListingDetailOut,
    ListingOut,
    OpportunityDetailOut,
    OpportunityOut,
    PriceObservationOut,
    PriceStatsOut,
    ProductDetailOut,
    ProductOut,
    ProfitResultOut,
    RiskFlagOut,
    ScoreComponentOut,
    SourceOut,
)
from .timeutil import as_utc, as_utc_opt

SEVERITY_RANK = {"none": 0, "info": 1, "warning": 2, "critical": 3}


def risk_level(flags: list[dict[str, Any]] | None) -> str:
    level = "none"
    for flag in flags or []:
        severity = flag.get("severity") if isinstance(flag, dict) else None
        if severity in SEVERITY_RANK and SEVERITY_RANK[severity] > SEVERITY_RANK[level]:
            level = severity
    return level


def risk_flags_out(flags: list[dict[str, Any]] | None) -> list[RiskFlagOut]:
    out: list[RiskFlagOut] = []
    for flag in flags or []:
        if not isinstance(flag, dict):
            continue
        out.append(
            RiskFlagOut(
                code=str(flag.get("code", "unknown")),
                severity=str(flag.get("severity", "info")),
                message=str(flag.get("message", "")),
                evidence=flag.get("evidence") if isinstance(flag.get("evidence"), dict) else {},
            )
        )
    return out


def median_cents(values: list[int]) -> int | None:
    """Median of observed values, half-up to the centavo (None for an empty list)."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return int((Decimal(ordered[mid - 1] + ordered[mid]) / 2).to_integral_value(rounding=ROUND_HALF_UP))


def _listing_fields(listing: Listing) -> dict[str, Any]:
    return {
        "id": listing.id,
        "source_key": listing.source.key if listing.source is not None else "",
        "external_id": listing.external_id,
        "url": listing.url,
        "title": listing.title,
        "currency": listing.currency,
        "price_cents": listing.price_cents,
        "price_mxn_cents": listing.price_mxn_cents,
        "shipping_cents": listing.shipping_cents,
        "free_shipping": listing.free_shipping,
        "condition": listing.condition,
        "brand": listing.brand,
        "model": listing.model,
        "category": listing.category,
        "gtin": listing.gtin,
        "seller_name": listing.seller_name,
        "location": listing.location,
        "image_url": listing.image_url,
        "available_quantity": listing.available_quantity,
        "sold_quantity": listing.sold_quantity,
        "status": listing.status,
        "product_id": listing.product_id,
        "match_confidence": listing.match_confidence,
        "match_method": listing.match_method,
        "is_accessory": bool(listing.is_accessory),
        "is_bundle": bool(listing.is_bundle),
        "is_suspicious": bool(listing.is_suspicious),
        "risk_flags": risk_flags_out(listing.risk_flags_json),
        "is_demo": bool(listing.is_demo),
        "first_seen_at": as_utc(listing.first_seen_at),
        "last_seen_at": as_utc(listing.last_seen_at),
    }


def listing_out(listing: Listing) -> ListingOut:
    return ListingOut(**_listing_fields(listing))


def observation_out(obs: PriceObservation) -> PriceObservationOut:
    return PriceObservationOut(
        observed_at=as_utc(obs.observed_at),
        price_cents=obs.price_cents,
        currency=obs.currency,
        price_mxn_cents=obs.price_mxn_cents,
        available_quantity=obs.available_quantity,
        sold_quantity=obs.sold_quantity,
        status=obs.status,
    )


def listing_detail_out(listing: Listing) -> ListingDetailOut:
    return ListingDetailOut(
        **_listing_fields(listing),
        description=listing.description,
        attributes=dict(listing.attributes_json or {}),
        seller=dict(listing.seller_json or {}),
        observations=[observation_out(o) for o in listing.observations],
    )


def product_out(product: Product, listings: list[Listing]) -> ProductOut:
    active = [lst.price_mxn_cents for lst in listings if lst.status == "active" and lst.price_mxn_cents is not None]
    return ProductOut(
        id=product.id,
        canonical_key=product.canonical_key,
        title=product.title,
        brand=product.brand,
        model=product.model,
        category=product.category,
        gtin=product.gtin,
        attributes=dict(product.attributes_json or {}),
        listing_count=len(listings),
        min_price_mxn_cents=min(active) if active else None,
        median_price_mxn_cents=median_cents([int(v) for v in active]),
        updated_at=as_utc(product.updated_at),
    )


def product_detail_out(product: Product, listings: list[Listing], stats: dict[str, PriceStatsOut]) -> ProductDetailOut:
    base = product_out(product, listings)
    return ProductDetailOut(**base.model_dump(), stats_by_condition=stats, listings=[listing_out(x) for x in listings])


_EMPTY_STATS: dict[str, Any] = {
    "n": 0, "median_cents": None, "p25_cents": None, "p75_cents": None, "min_cents": None, "max_cents": None,
    "mad_cents": None, "iqr_cents": None, "dispersion": None, "source_count": 0, "seller_count": 0,
}


def price_stats_out(data: dict[str, Any] | None, fallback_n: int = 0) -> PriceStatsOut:
    merged = {**_EMPTY_STATS, "n": fallback_n, **(data or {})}
    try:
        return PriceStatsOut.model_validate(merged)
    except ValidationError:
        return PriceStatsOut.model_validate({**_EMPTY_STATS, "n": fallback_n})


def profit_out(data: dict[str, Any] | None, opp: Opportunity) -> ProfitResultOut:
    fallback = {
        "sale_price_cents": opp.expected_sale_cents,
        "acquisition_lines": [],
        "selling_lines": [],
        "total_acquisition_cents": opp.buy_total_cents,
        "total_selling_costs_cents": 0,
        "net_proceeds_cents": opp.net_profit_cents + opp.buy_total_cents,
        "net_profit_cents": opp.net_profit_cents,
        "roi_bp": opp.roi_bp,
        "margin_bp": opp.margin_bp,
        "fee_profile_key": opp.fee_profile_key,
        "assumptions": ["Detailed breakdown unavailable for this opportunity."],
    }
    try:
        return ProfitResultOut.model_validate({**fallback, **(data or {})})
    except ValidationError:
        return ProfitResultOut.model_validate(fallback)


def _components_out(items: list[dict[str, Any]] | None) -> list[ScoreComponentOut]:
    out: list[ScoreComponentOut] = []
    for item in items or []:
        try:
            out.append(ScoreComponentOut.model_validate(item))
        except ValidationError:
            continue
    return out


def demand_out(data: dict[str, Any] | None) -> DemandOut:
    try:
        return DemandOut.model_validate({"level": "unknown", "confidence": None, **(data or {})})
    except ValidationError:
        return DemandOut(level="unknown", confidence=None)


def opportunity_out(opp: Opportunity) -> OpportunityOut:
    listing = opp.buy_listing
    product = opp.product
    return OpportunityOut(
        id=opp.id,
        product_id=opp.product_id,
        product_title=product.title if product else "",
        product_key=product.canonical_key if product else "",
        category=product.category if product else None,
        buy_listing_id=opp.buy_listing_id,
        buy_title=listing.title if listing else "",
        buy_url=listing.url if listing else None,
        buy_source_key=listing.source.key if listing is not None and listing.source is not None else "",
        buy_condition=listing.condition if listing else "unknown",
        image_url=listing.image_url if listing else None,
        fee_profile_key=opp.fee_profile_key,
        buy_total_cents=opp.buy_total_cents,
        expected_sale_cents=opp.expected_sale_cents,
        net_profit_cents=opp.net_profit_cents,
        roi_bp=opp.roi_bp,
        margin_bp=opp.margin_bp,
        score=float(opp.score),
        confidence=opp.confidence,
        comparable_count=opp.comparable_count,
        risk_level=risk_level(opp.risk_flags_json),  # type: ignore[arg-type]
        demand_level=str((opp.demand_json or {}).get("level") or "unknown"),
        status=opp.status,
        is_demo=bool(opp.is_demo),
        created_at=as_utc(opp.created_at),
        updated_at=as_utc(opp.updated_at),
    )


def opportunity_detail_out(opp: Opportunity, comparables: list[Listing]) -> OpportunityDetailOut:
    base = opportunity_out(opp)
    return OpportunityDetailOut(
        **base.model_dump(),
        price_stats=price_stats_out(opp.price_stats_json, opp.comparable_count),
        cost_breakdown=profit_out(opp.cost_breakdown_json, opp),
        score_components=_components_out(opp.score_components_json),
        risk_flags=risk_flags_out(opp.risk_flags_json),
        demand=demand_out(opp.demand_json),
        explanation=opp.explanation or "",
        facts=dict(opp.explanation_facts_json or {}),
        ai_summary=opp.ai_summary,
        comparables=[listing_out(c) for c in comparables],
    )


def fee_profile_out(row: FeeProfile) -> FeeProfileOut:
    tiers = [
        FixedFeeTierIO(max_price_cents=t.get("max_price_cents"), fee_cents=int(t.get("fee_cents", 0)))
        for t in (row.fixed_fee_tiers_json or [])
        if isinstance(t, dict)
    ]
    return FeeProfileOut(
        key=row.key,
        name=row.name,
        channel=row.channel,
        commission_bp=row.commission_bp,
        fixed_fee_tiers=tiers,
        payment_fee_bp=row.payment_fee_bp,
        payment_fixed_cents=row.payment_fixed_cents,
        iva_on_fees=bool(row.iva_on_fees),
        iva_rate_bp=row.iva_rate_bp,
        seller_shipping_cents=row.seller_shipping_cents,
        free_shipping_threshold_cents=row.free_shipping_threshold_cents,
        isr_withholding_bp=row.isr_withholding_bp,
        iva_withholding_bp=row.iva_withholding_bp,
        source_url=row.source_url,
        as_of=row.as_of,
        notes=row.notes,
        is_builtin=bool(row.is_builtin),
        updated_at=as_utc_opt(row.updated_at),
    )


def source_out(row: Source, info: CollectorInfo | None, configured: bool, fixed: dict[str, Any] | None = None) -> SourceOut:
    fixed = fixed or {}
    return SourceOut(
        key=row.key,
        name=row.name,
        kind=row.kind,
        country=row.country,
        currency=row.currency,
        enabled=bool(row.enabled),
        default_fee_profile_key=row.default_fee_profile_key,
        configured=configured,
        requires_credentials=bool(info.requires_credentials) if info else False,
        capabilities=list(info.capabilities) if info else list(fixed.get("capabilities", [])),
        docs_url=info.docs_url if info else None,
        notes=(info.notes if info else fixed.get("notes", "")) or "",
        last_run_at=as_utc_opt(row.last_run_at),
        last_status=row.last_status,
        last_error=row.last_error,
    )
