"""FLIP RADAR opportunity engine: comparables, statistics, risk, demand, scoring and explanations.

Every function in this package is pure and deterministic (time is passed in, money is integer
centavos, percentages are basis points).  The main entry point is :func:`analyze_product`.
"""

from .demand import Observations, estimate_demand
from .explain import AnalysisContext, build_facts, fmt_money, fmt_pct_bp, render_explanation
from .opportunities import AnalysisSettings, analyze_product
from .risk import assess_listing, normalize_text, risk_level
from .scoring import score_opportunity
from .stats import (
    comparable_stats,
    condition_class,
    quantile,
    robust_stats,
    robust_z,
    select_comparables,
    total_price_cents,
)

__all__ = [
    "AnalysisContext",
    "AnalysisSettings",
    "Observations",
    "analyze_product",
    "assess_listing",
    "build_facts",
    "comparable_stats",
    "condition_class",
    "estimate_demand",
    "fmt_money",
    "fmt_pct_bp",
    "normalize_text",
    "quantile",
    "render_explanation",
    "risk_level",
    "robust_stats",
    "robust_z",
    "score_opportunity",
    "select_comparables",
    "total_price_cents",
]
