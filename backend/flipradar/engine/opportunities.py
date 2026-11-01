"""Opportunity detection: combine comparables, fees, risk, demand and scoring.  OWNER: engine agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..finance.calculator import FeeModel
from .demand import Observations
from .types import ListingView, OpportunityCandidate


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


def analyze_product(
    product_key: str,
    listings: list[ListingView],
    fee_model: FeeModel,
    settings: AnalysisSettings,
    observations: Observations,
    *,
    now: datetime,
) -> list[OpportunityCandidate]:
    """Evaluate every listing of a product as a BUY candidate against the others as RESALE comparables.

    Returns only candidates that pass the profit/ROI thresholds, sorted by score descending.
    Pure function: same inputs -> same outputs.
    """
    raise NotImplementedError
