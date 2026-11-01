"""Opportunity scoring (0..100) with explainable components.  OWNER: engine agent."""

from __future__ import annotations

from .types import ConfidenceLevel, DemandEstimate, PriceStats, RiskFlag, ScoreComponent


def score_opportunity(
    *,
    net_profit_cents: int,
    roi_bp: int,
    stats: PriceStats,
    match_confidence: float | None,
    demand: DemandEstimate,
    risk_flags: list[RiskFlag],
    min_comparables: int,
) -> tuple[float, ConfidenceLevel, list[ScoreComponent]]:
    """Deterministic weighted score; returns (score rounded to 1 decimal, confidence, components)."""
    raise NotImplementedError
