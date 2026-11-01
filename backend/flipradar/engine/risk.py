"""Suspicious listing detection (deterministic rules).  OWNER: engine agent."""

from __future__ import annotations

from .types import ListingView, PriceStats, RiskFlag


def assess_listing(listing: ListingView, stats: PriceStats | None) -> list[RiskFlag]:
    """Return risk flags for a listing.  ``stats`` are the comparable stats for its product/condition
    (None when unknown).  Never raises on missing data - missing data is itself a (low) signal."""
    raise NotImplementedError


def risk_level(flags: list[RiskFlag]) -> str:
    """'none' | 'info' | 'warning' | 'critical' (max severity)."""
    raise NotImplementedError
