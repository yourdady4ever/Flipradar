"""Opportunity detection: combine comparables, fees, risk, demand and scoring.  OWNER: engine agent.

For every active listing of a product the engine asks: *if I bought this one and resold it through
``fee_model``, what would I make, according to the other observed offers?*

1. pick independent comparables (:func:`~flipradar.engine.stats.select_comparables`) and compute robust
   statistics with outlier trimming;
2. refuse to estimate anything with fewer than ``settings.min_comparables`` comparables;
3. expected sale price = median of sold comparables when there are enough of them, otherwise the
   25th percentile (``competitive``) or the median (``market``) of comparable prices;
4. exact profit/ROI with :func:`~flipradar.finance.calculator.calculate_profit`;
5. risk flags, demand evidence, score and a fact-grounded explanation.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime

from ..finance.calculator import (
    FeeModel,
    ProfitInputs,
    break_even_sale_price,
    calculate_profit,
    max_buy_price_for_roi,
)
from .demand import Observations, estimate_demand
from .explain import AnalysisContext, build_facts, render_explanation
from .risk import assess_listing
from .scoring import score_opportunity
from .stats import comparable_stats, condition_class, quantile, resale_price_cents, select_comparables
from .types import ListingView, OpportunityCandidate, PriceStats, RiskFlag

PRICING_STRATEGIES = {"competitive": "comparables_p25", "market": "comparables_median"}


@dataclass(frozen=True)
class AnalysisSettings:
    min_comparables: int = 3
    comparable_max_age_days: int = 30
    min_profit_cents: int = 20000  # $200 MXN
    min_roi_bp: int = 1000  # 10 %
    pricing_strategy: str = "competitive"  # competitive = p25 of comparables, market = median
    include_demo: bool = False
    # Shipping cost assumed for buy listings whose shipping is unknown (None = treat as 0 + flag).
    unknown_shipping_assumption_cents: int | None = None


def _skip_reason(listing: ListingView, product_key: str, settings: AnalysisSettings) -> str | None:
    """Why a listing cannot be a BUY candidate (None = it can)."""
    if listing.product_key != product_key:
        return "different_product"
    if listing.is_accessory:
        return "accessory"
    if listing.is_bundle:
        return "bundle"
    if (listing.condition or "").lower() == "for_parts":
        return "for_parts"
    if (listing.status or "").lower() != "active":
        return "not_active"
    if listing.is_demo and not settings.include_demo:
        return "demo"
    if listing.price_mxn_cents is None or listing.price_mxn_cents <= 0:
        return "no_price"
    return None


def _expected_sale(
    comparables: list[ListingView], stats: PriceStats, settings: AnalysisSettings
) -> tuple[int, str, int, tuple[str, ...]]:
    """Return (expected sale cents, method, sold comparable count, comparable source keys)."""
    retained_ids = set(stats.comparable_ids)
    retained = [item for item in comparables if item.id in retained_ids]
    sold_totals = sorted(
        total
        for item in retained
        if (item.status or "").lower() == "sold" and (total := resale_price_cents(item)) is not None
    )
    sources = tuple(sorted({item.source_key for item in retained}))
    if len(sold_totals) >= settings.min_comparables:
        return quantile(sold_totals, 0.5), "sold_median", len(sold_totals), sources
    method = PRICING_STRATEGIES[settings.pricing_strategy]
    expected = stats.p25_cents if method == "comparables_p25" else stats.median_cents
    if expected is None:  # pragma: no cover - guarded by the min_comparables check
        raise ValueError("comparable statistics are empty")
    return expected, method, len(sold_totals), sources


def _with_shipping_assumption(flags: list[RiskFlag], assumed_cents: int) -> list[RiskFlag]:
    result = []
    for flag in flags:
        if flag.code == "shipping_unknown":
            flag = RiskFlag(
                flag.code,
                flag.severity,
                flag.message,
                {**flag.evidence, "assumed_shipping_cents": assumed_cents},
            )
        result.append(flag)
    return result


def analyze_product(
    product_key: str,
    listings: list[ListingView],
    fee_model: FeeModel,
    settings: AnalysisSettings,
    observations: Observations,
    *,
    now: datetime,
    fee_models_by_condition: dict[str, FeeModel] | None = None,
) -> list[OpportunityCandidate]:
    """Evaluate every listing of a product as a BUY candidate against the others as RESALE comparables.

    ``fee_models_by_condition`` optionally maps a condition class (``new`` | ``refurbished`` | ``used``)
    to the fee model used to RESELL a unit bought in that condition (e.g. Mercado Libre charges different
    shipping tiers and applies tax withholdings only to new items).  Missing classes use ``fee_model``.

    Returns only candidates that pass the profit/ROI thresholds, sorted by score descending.
    Pure function: same inputs -> same outputs.

    Candidates are skipped (never estimated) when they are accessories, bundles, for-parts, not active,
    demo (unless ``settings.include_demo``), without a price, or have fewer than
    ``settings.min_comparables`` comparables after outlier trimming.  Ties in score are broken by net
    profit (descending) then listing id (ascending).
    """
    if settings.pricing_strategy not in PRICING_STRATEGIES:
        raise ValueError(
            f"unknown pricing_strategy {settings.pricing_strategy!r}; expected one of {sorted(PRICING_STRATEGIES)}"
        )
    if settings.min_comparables < 1:
        raise ValueError("min_comparables must be >= 1")

    pool = sorted(listings, key=lambda item: item.id)
    demand_pool = [
        item
        for item in pool
        if item.product_key == product_key
        and not item.is_accessory
        and not item.is_bundle
        and (settings.include_demo or not item.is_demo)
    ]
    demand = estimate_demand(demand_pool, observations, now=now)

    candidates: list[OpportunityCandidate] = []
    for buy in pool:
        if _skip_reason(buy, product_key, settings) is not None:
            continue
        comparables, excluded = select_comparables(
            buy,
            pool,
            now=now,
            max_age_days=settings.comparable_max_age_days,
            include_demo=settings.include_demo,
        )
        stats = comparable_stats(comparables, excluded, settings.comparable_max_age_days)
        if stats.n < settings.min_comparables:
            continue  # no estimate without evidence

        expected, method, sold_count, sources = _expected_sale(comparables, stats, settings)
        shipping_assumed = buy.shipping_mxn_cents is None
        if shipping_assumed:
            shipping = settings.unknown_shipping_assumption_cents or 0
        else:
            shipping = int(buy.shipping_mxn_cents or 0)
        price = int(buy.price_mxn_cents)
        model = (fee_models_by_condition or {}).get(condition_class(buy.condition) or "", fee_model)
        inputs = ProfitInputs(
            buy_price_cents=price,
            buy_shipping_cents=shipping,
            sale_price_cents=expected,
            fee=model,
        )
        profit = calculate_profit(inputs)
        if profit.roi_bp is None or profit.margin_bp is None:
            continue
        if profit.net_profit_cents < settings.min_profit_cents or profit.roi_bp < settings.min_roi_bp:
            continue

        flags = assess_listing(buy, stats)
        if shipping_assumed:
            flags = _with_shipping_assumption(flags, shipping)
        score, confidence, components = score_opportunity(
            net_profit_cents=profit.net_profit_cents,
            roi_bp=profit.roi_bp,
            stats=stats,
            match_confidence=buy.match_confidence,
            demand=demand,
            risk_flags=flags,
            min_comparables=settings.min_comparables,
        )
        target_roi = settings.min_roi_bp if settings.min_roi_bp >= 0 else None
        context = AnalysisContext(
            estimate_method=method,
            sold_comparables=sold_count,
            comparable_sources=sources,
            fee_profile_name=model.name,
            fee_profile_as_of=model.as_of,
            shipping_assumed=shipping_assumed,
            break_even_sale_cents=break_even_sale_price(inputs),
            max_buy_price_cents=max_buy_price_for_roi(inputs, target_roi) if target_roi is not None else None,
            target_roi_bp=target_roi,
        )
        candidate = OpportunityCandidate(
            buy=buy,
            product_key=product_key,
            fee_profile_key=model.key,
            stats=stats,
            expected_sale_cents=expected,
            buy_total_cents=price + shipping,
            profit=profit.to_dict(),
            net_profit_cents=profit.net_profit_cents,
            roi_bp=profit.roi_bp,
            margin_bp=profit.margin_bp,
            risk_flags=flags,
            demand=copy.deepcopy(demand),
            score=score,
            confidence=confidence,
            components=components,
            explanation="",
            facts={},
        )
        candidate.facts = build_facts(candidate, context=context)
        candidate.explanation = render_explanation(candidate.facts)
        candidates.append(candidate)

    candidates.sort(key=lambda c: (-c.score, -c.net_profit_cents, c.buy.id))
    return candidates
