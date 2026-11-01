"""Robust price statistics over observed comparables.  OWNER: engine agent."""

from __future__ import annotations

from datetime import datetime

from .types import ListingView, PriceStats


def quantile(sorted_values: list[int], q: float) -> int:
    """Deterministic linear-interpolation quantile (type 7), rounded half-up to an integer centavo."""
    raise NotImplementedError


def robust_stats(values_cents: list[int]) -> PriceStats:
    """Median/quartiles/MAD/IQR for a list of prices (no source info)."""
    raise NotImplementedError


def select_comparables(
    target: ListingView,
    pool: list[ListingView],
    *,
    now: datetime,
    max_age_days: int,
    include_demo: bool = False,
) -> tuple[list[ListingView], dict[str, int]]:
    """Pick listings comparable to ``target`` (same product + condition class, fresh, not itself,
    not accessory/bundle, not flagged critical, one listing per seller...).  Returns (comparables,
    excluded_reason_counts)."""
    raise NotImplementedError


def comparable_stats(comparables: list[ListingView], excluded: dict[str, int], window_days: int) -> PriceStats:
    """PriceStats over comparables' total price (price + known shipping), with source/seller counts,
    outlier trimming (robust z > 3.5 removed and counted in ``excluded['outlier']``)."""
    raise NotImplementedError


def robust_z(value_cents: int, stats: PriceStats) -> float | None:
    """(value - median) / (1.4826 * MAD); None if MAD is 0/None."""
    raise NotImplementedError
