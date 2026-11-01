"""Demand estimation from observed evidence only.  OWNER: engine agent.

Demand is never guessed.  Two kinds of *evidence* can set a level:

(a) **sold_quantity deltas** - some sources (e.g. Mercado Libre for some items) expose a cumulative
    ``sold_quantity``.  When a listing has at least two non-null readings in the last 30 days, the
    difference between its last and first reading is the number of units sold in between.  Units are
    summed over listings and divided by the covered time span (earliest first reading to latest last
    reading, across listings - a conservative choice) to obtain units/day, but only when that span is at
    least 2 days.
(b) **sold listings** - listings whose source reports status ``sold`` and that were observed in the last
    30 days.  This is a lower bound (only what our searches saw) and is converted to a rate over the
    whole 30-day window.

Supply signals (active listings, distinct sellers) are reported for context but are *not* demand.
Without (a) or (b) the level is ``unknown`` - which is different from ``low``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .stats import age
from .types import ConfidenceLevel, DemandEstimate, ListingView

# observations[listing_id] = list of (observed_at, price_mxn_cents|None, sold_quantity|None, status)
Observations = dict[int, list[tuple[datetime, int | None, int | None, str]]]

WINDOW_DAYS = 30
MIN_SPAN_DAYS = Decimal(2)
HIGH_RATE = Decimal(1)  # units/day
MEDIUM_RATE = Decimal("0.2")
HIGH_CONFIDENCE_LISTINGS = 5
HIGH_CONFIDENCE_SPAN_DAYS = Decimal(14)
MEDIUM_CONFIDENCE_LISTINGS = 2
MEDIUM_CONFIDENCE_SPAN_DAYS = Decimal(7)

_FOUR_DP = Decimal("0.0001")
_TWO_DP = Decimal("0.01")
_CONFIDENCE_RANK: dict[str, int] = {"low": 1, "medium": 2, "high": 3}

NOTE_SOLD_QUANTITY = (
    "sold_quantity is only visible for some sources and listings and may be rounded or hidden by the "
    "source; units sold between the first and last reading in the window"
)
NOTE_SOLD_LISTINGS = (
    "listings reported as sold by their source and observed in the window; only sources that expose a "
    "sold status contribute, so this is a lower bound"
)
NOTE_SUPPLY = "supply indicator (competing offers), not demand"


def _days(delta: timedelta) -> Decimal:
    seconds = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(1_000_000)
    return seconds / Decimal(86400)


def _q4(value: Decimal) -> float:
    return float(value.quantize(_FOUR_DP, rounding=ROUND_HALF_UP))


def _in_window(now: datetime, when: datetime, window: timedelta) -> bool:
    elapsed = age(now, when)
    return timedelta(0) <= elapsed <= window


def _level(rate: Decimal) -> str:
    if rate >= HIGH_RATE:
        return "high"
    if rate >= MEDIUM_RATE:
        return "medium"
    return "low"


def _signal(name: str, value: int | float, unit: str, source: str, note: str) -> dict[str, object]:
    return {"name": name, "value": value, "unit": unit, "source": source, "note": note}


def estimate_demand(listings: list[ListingView], observations: Observations, *, now: datetime) -> DemandEstimate:
    """Return level 'unknown' unless there is enough real evidence (sold-quantity deltas over time,
    listings that ended/sold, competing listing counts).  Every signal must cite where it came from.

    Levels: units/day >= 1 -> high, >= 0.2 -> medium, else low (the larger of the two evidence rates is
    used so the same sales are never double counted).  Confidence for (a): high with >= 5 listings and
    a span >= 14 days, medium with >= 2 listings or a span >= 7 days, else low; (b) alone is at most
    medium (>= 2 sold listings).  Signals: ``{name, value, unit, source, note}``.
    """
    window = timedelta(days=WINDOW_DAYS)
    ordered = sorted(listings, key=lambda item: item.id)
    signals: list[dict[str, object]] = []

    # (a) sold_quantity deltas -----------------------------------------------------------------
    delta_listings = 0
    anomalies = 0
    units = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    delta_sources: set[str] = set()
    for listing in ordered:
        readings = sorted(
            (
                (obs[0], int(obs[2]))
                for obs in observations.get(listing.id, [])
                if obs[2] is not None and _in_window(now, obs[0], window)
            ),
            key=lambda reading: (age(reading[0], datetime(1970, 1, 1)), reading[1]),
        )
        if len(readings) < 2:
            continue
        delta = readings[-1][1] - readings[0][1]
        if delta < 0:
            anomalies += 1  # counter went backwards: unreliable, ignore this listing
            continue
        delta_listings += 1
        units += delta
        delta_sources.add(listing.source_key)
        start, end = readings[0][0], readings[-1][0]
        if first_seen is None or age(start, first_seen) < timedelta(0):
            first_seen = start
        if last_seen is None or age(end, last_seen) > timedelta(0):
            last_seen = end

    span_days = _days(age(last_seen, first_seen)) if first_seen and last_seen else Decimal(0)
    rate_a: Decimal | None = None
    confidence_a: ConfidenceLevel | None = None
    if delta_listings:
        source_text = "sold_quantity observations (" + ", ".join(sorted(delta_sources)) + ")"
        signals.append(_signal(
            "sold_quantity_delta",
            units,
            "units",
            source_text,
            f"{NOTE_SOLD_QUANTITY}; {delta_listings} listing(s) over {_q4(span_days)} days",
        ))
        if span_days >= MIN_SPAN_DAYS:
            rate_a = Decimal(units) / span_days
            signals.append(_signal(
                "sales_rate", _q4(rate_a), "units/day", source_text,
                f"units sold divided by the {_q4(span_days)}-day span covered by the readings",
            ))
            if delta_listings >= HIGH_CONFIDENCE_LISTINGS and span_days >= HIGH_CONFIDENCE_SPAN_DAYS:
                confidence_a = "high"
            elif delta_listings >= MEDIUM_CONFIDENCE_LISTINGS or span_days >= MEDIUM_CONFIDENCE_SPAN_DAYS:
                confidence_a = "medium"
            else:
                confidence_a = "low"

    # (b) sold listings ------------------------------------------------------------------------
    sold_ids: set[int] = set()
    sold_sources: set[str] = set()
    for listing in ordered:
        sold = (listing.status or "").lower() == "sold" and _in_window(now, listing.observed_at, window)
        if not sold:
            sold = any(
                (obs[3] or "").lower() == "sold" and _in_window(now, obs[0], window)
                for obs in observations.get(listing.id, [])
            )
        if sold:
            sold_ids.add(listing.id)
            sold_sources.add(listing.source_key)
    rate_b: Decimal | None = None
    confidence_b: ConfidenceLevel | None = None
    if sold_ids:
        rate_b = Decimal(len(sold_ids)) / Decimal(WINDOW_DAYS)
        signals.append(_signal(
            "sold_listings",
            len(sold_ids),
            "listings",
            "listing status (" + ", ".join(sorted(sold_sources)) + ")",
            f"{NOTE_SOLD_LISTINGS}; window {WINDOW_DAYS} days ({_q4(rate_b)} per day)",
        ))
        confidence_b = "medium" if len(sold_ids) >= MEDIUM_CONFIDENCE_LISTINGS else "low"

    # (c) supply (informational) ----------------------------------------------------------------
    active = [
        listing for listing in ordered
        if (listing.status or "").lower() == "active" and _in_window(now, listing.observed_at, window)
    ]
    sellers = {(item.source_key, item.seller_id) for item in active if item.seller_id is not None}
    anonymous = sum(1 for item in active if item.seller_id is None)
    active_sources = ", ".join(sorted({item.source_key for item in active})) or "none"
    signals.append(_signal("active_listings", len(active), "listings", f"listings ({active_sources})", NOTE_SUPPLY))
    signals.append(_signal(
        "distinct_sellers", len(sellers) + anonymous, "sellers", f"listings ({active_sources})",
        NOTE_SUPPLY + "; listings without a seller id count as one seller each",
    ))

    rates = [rate for rate in (rate_a, rate_b) if rate is not None]
    if not rates:
        reasons = []
        if delta_listings and span_days < MIN_SPAN_DAYS:
            reasons.append(f"sold_quantity readings cover only {_q4(span_days)} days (minimum {MIN_SPAN_DAYS})")
        if anomalies:
            reasons.append(f"{anomalies} listing(s) had a decreasing sold_quantity and were ignored")
        detail = ("; " + "; ".join(reasons)) if reasons else ""
        return DemandEstimate(
            level="unknown",
            confidence=None,
            signals=signals,
            note=(
                f"No sales evidence in the last {WINDOW_DAYS} days (no usable sold_quantity changes and no "
                f"listings reported as sold){detail}. Demand is unknown, not zero."
            ),
        )

    rate = max(rates)
    level = _level(rate)
    confidences = [c for c in (confidence_a, confidence_b) if c is not None]
    confidence: ConfidenceLevel = max(confidences, key=lambda c: _CONFIDENCE_RANK[c])
    basis = "sold_quantity changes" if rate_a is not None and rate == rate_a else "listings reported as sold"
    note = (
        f"Estimated {_q4(rate)} units/day from {basis} in the last {WINDOW_DAYS} days "
        f"(high at {HIGH_RATE} or more, medium at {MEDIUM_RATE} or more)."
    )
    if anomalies:
        note += f" {anomalies} listing(s) with a decreasing sold_quantity were ignored."
    return DemandEstimate(level=level, confidence=confidence, signals=signals, note=note)
