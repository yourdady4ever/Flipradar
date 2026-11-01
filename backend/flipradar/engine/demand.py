"""Demand estimation from observed evidence only.  OWNER: engine agent."""

from __future__ import annotations

from datetime import datetime

from .types import DemandEstimate, ListingView

# observations[listing_id] = list of (observed_at, price_mxn_cents|None, sold_quantity|None, status)
Observations = dict[int, list[tuple[datetime, int | None, int | None, str]]]


def estimate_demand(listings: list[ListingView], observations: Observations, *, now: datetime) -> DemandEstimate:
    """Return level 'unknown' unless there is enough real evidence (sold-quantity deltas over time,
    listings that ended/sold, competing listing counts).  Every signal must cite where it came from."""
    raise NotImplementedError
