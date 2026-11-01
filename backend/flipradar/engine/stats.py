"""Robust price statistics over observed comparables.  OWNER: engine agent.

Everything here is pure and deterministic:

* prices are integer centavos; intermediate maths uses :class:`~decimal.Decimal` and every reported
  value is rounded half-up to a whole centavo;
* quantiles use the "type 7" linear interpolation (the default of R / NumPy), so results can be
  reproduced with any spreadsheet;
* nothing is estimated when there is no data - empty inputs produce ``None`` statistics.

A *comparable* is another observed offer for the same canonical product, in the same condition class,
seen recently, from an independent seller, that is not an accessory/bundle and not flagged as critical
risk.  Every listing that is rejected is counted under a stable reason code so the UI (and the
explanation) can show exactly why the evidence set looks the way it does.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from .types import ListingView, PriceStats

#: Consistency constant that makes the MAD comparable to a standard deviation for normal data.
MAD_SCALE = Decimal("1.4826")
#: |robust z| above this value marks a price as an outlier (Iglewicz & Hoaglin recommendation).
OUTLIER_Z = Decimal("3.5")
#: Outlier trimming only runs when at least this many comparables exist.
MIN_N_FOR_OUTLIERS = 5

#: Condition -> condition class.  ``unknown`` is treated as ``used`` (the conservative assumption);
#: ``for_parts`` maps to ``None`` and is never comparable with anything.
CONDITION_CLASSES: dict[str, str | None] = {
    "new": "new",
    "refurbished": "refurbished",
    "used": "used",
    "unknown": "used",
    "for_parts": None,
}

#: Risk codes that are always critical (see :mod:`flipradar.engine.risk`).
CRITICAL_RISK_CODES = frozenset({"replica_or_counterfeit", "box_or_parts_only", "locked_or_reported"})

#: Order in which exclusion reasons are tested (each listing is counted under its FIRST reason).
EXCLUSION_REASONS = (
    "self",
    "different_product",
    "not_resale_market",
    "condition",
    "accessory",
    "bundle",
    "demo",
    "ended",
    "no_price",
    "stale",
    "critical_risk",
    "same_seller",
    "duplicate_seller",
    "outlier",
)

_FOUR_DP = Decimal("0.0001")


# ------------------------------------------------------------------------------------------------
# Basic helpers
# ------------------------------------------------------------------------------------------------


def _round_int(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def _as_aware(dt: datetime) -> datetime:
    """Naive datetimes are interpreted as UTC so naive/aware values can be compared safely."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def age(now: datetime, then: datetime) -> timedelta:
    """``now - then`` tolerating a mix of naive (assumed UTC) and aware datetimes."""
    return _as_aware(now) - _as_aware(then)


def condition_class(condition: str | None) -> str | None:
    """Map a listing condition to its comparison class (``new`` | ``refurbished`` | ``used``).

    ``unknown`` (and any unrecognised value) is treated as ``used``; ``for_parts`` returns ``None``
    because a for-parts unit is never comparable with a working one.
    """
    if condition is None:
        return "used"
    return CONDITION_CLASSES.get(condition.strip().lower(), "used")


def resale_price_cents(listing: ListingView) -> int | None:
    """Price a reseller actually receives from a comparable sale: the item price in MXN.

    Buyer-paid shipping is not revenue for the seller, and seller-paid shipping is modelled by the fee
    profile (free-shipping threshold / per-unit shipping tiers), so resale statistics use the item price.
    """
    return None if listing.price_mxn_cents is None else int(listing.price_mxn_cents)


def total_price_cents(listing: ListingView) -> int | None:
    """Total price paid by a buyer: price + shipping when shipping is known, else the price alone."""
    if listing.price_mxn_cents is None:
        return None
    if listing.shipping_mxn_cents is None:
        return int(listing.price_mxn_cents)
    return int(listing.price_mxn_cents) + int(listing.shipping_mxn_cents)


def _decimal_quantile(sorted_values: list[int], q: Decimal) -> Decimal:
    n = len(sorted_values)
    h = Decimal(n - 1) * q
    lo = int(h.to_integral_value(rounding=ROUND_FLOOR))
    hi = min(lo + 1, n - 1)
    frac = h - Decimal(lo)
    low_value = Decimal(sorted_values[lo])
    return low_value + frac * (Decimal(sorted_values[hi]) - low_value)


def _to_q(q: float | Decimal | str) -> Decimal:
    dq = q if isinstance(q, Decimal) else Decimal(str(q))
    if not Decimal(0) <= dq <= Decimal(1):
        raise ValueError(f"quantile q must be within [0, 1], got {q}")
    return dq


def quantile(sorted_values: list[int], q: float) -> int:
    """Deterministic linear-interpolation quantile (type 7), rounded half-up to an integer centavo.

    ``sorted_values`` must be sorted ascending.  ``q`` is converted through ``str`` to a Decimal so
    e.g. ``0.25`` is exactly one quarter.  Raises ``ValueError`` on an empty list or ``q`` outside
    ``[0, 1]``.
    """
    if not sorted_values:
        raise ValueError("quantile of an empty list is undefined")
    return _round_int(_decimal_quantile(sorted_values, _to_q(q)))


def _median_decimal(sorted_values: list[int] | list[Decimal]) -> Decimal:
    n = len(sorted_values)
    mid = n // 2
    if n % 2:
        return Decimal(sorted_values[mid])
    return (Decimal(sorted_values[mid - 1]) + Decimal(sorted_values[mid])) / 2


def _mad_decimal(sorted_values: list[int], median: Decimal) -> Decimal:
    deviations = sorted(abs(Decimal(v) - median) for v in sorted_values)
    return _median_decimal(deviations)


def _dispersion(iqr_cents: int, median_cents: int) -> float | None:
    if median_cents <= 0:
        return None
    ratio = (Decimal(iqr_cents) / Decimal(median_cents)).quantize(_FOUR_DP, rounding=ROUND_HALF_UP)
    return float(ratio)


def robust_stats(values_cents: list[int]) -> PriceStats:
    """Median/quartiles/MAD/IQR for a list of prices (no source info).

    Empty input returns a :class:`PriceStats` with ``n == 0`` and every statistic ``None``.
    ``iqr = p75 - p25`` and ``dispersion = iqr / median`` (4 decimals) are computed from the rounded
    centavo values that are reported, so anyone can recompute them from the output.
    """
    values = sorted(int(v) for v in values_cents)
    if not values:
        return PriceStats(
            n=0,
            median_cents=None,
            p25_cents=None,
            p75_cents=None,
            min_cents=None,
            max_cents=None,
            mad_cents=None,
            iqr_cents=None,
            dispersion=None,
            source_count=0,
            seller_count=0,
        )
    median_dec = _median_decimal(values)
    median = _round_int(median_dec)
    p25 = quantile(values, 0.25)
    p75 = quantile(values, 0.75)
    iqr = p75 - p25
    return PriceStats(
        n=len(values),
        median_cents=median,
        p25_cents=p25,
        p75_cents=p75,
        min_cents=values[0],
        max_cents=values[-1],
        mad_cents=_round_int(_mad_decimal(values, median_dec)),
        iqr_cents=iqr,
        dispersion=_dispersion(iqr, median),
        source_count=0,
        seller_count=0,
    )


def _robust_z_decimal(value_cents: int, median_cents: int | None, mad_cents: int | None) -> Decimal | None:
    if median_cents is None or not mad_cents:
        return None
    return (Decimal(value_cents) - Decimal(median_cents)) / (MAD_SCALE * Decimal(mad_cents))


def robust_z(value_cents: int, stats: PriceStats) -> float | None:
    """(value - median) / (1.4826 * MAD); None if MAD is 0/None.  Rounded to 4 decimals."""
    z = _robust_z_decimal(value_cents, stats.median_cents, stats.mad_cents)
    if z is None:
        return None
    return float(z.quantize(_FOUR_DP, rounding=ROUND_HALF_UP))


# ------------------------------------------------------------------------------------------------
# Comparable selection
# ------------------------------------------------------------------------------------------------


def _risk_entries(listing: ListingView) -> Iterable[object]:
    attrs = listing.attributes or {}
    codes = attrs.get("risk_codes")
    if isinstance(codes, str):
        return [codes]
    if isinstance(codes, (list, tuple, set, frozenset)):
        return sorted(codes, key=str) if isinstance(codes, (set, frozenset)) else codes
    return []


def has_critical_risk(listing: ListingView) -> bool:
    """True when ``listing.attributes['risk_codes']`` contains a critical risk.

    Entries may be plain codes (``"replica_or_counterfeit"``; only the always-critical codes count) or
    flag dicts (``{"code": ..., "severity": "critical"}``).
    """
    for entry in _risk_entries(listing):
        if isinstance(entry, str) and entry in CRITICAL_RISK_CODES:
            return True
        if isinstance(entry, dict):
            severity = entry.get("severity")
            if severity == "critical" or (severity is None and entry.get("code") in CRITICAL_RISK_CODES):
                return True
    return False


def _exclusion_reason(
    target: ListingView,
    candidate: ListingView,
    *,
    target_class: str | None,
    now: datetime,
    max_age: timedelta,
    include_demo: bool,
) -> str | None:
    if candidate.id == target.id:
        return "self"
    if target.product_key is None or candidate.product_key != target.product_key:
        return "different_product"
    if not candidate.resale_reference:
        return "not_resale_market"
    cand_class = condition_class(candidate.condition)
    if target_class is None or cand_class is None or cand_class != target_class:
        return "condition"
    if candidate.is_accessory:
        return "accessory"
    if candidate.is_bundle:
        return "bundle"
    if candidate.is_demo and not include_demo:
        return "demo"
    if (candidate.status or "").lower() == "ended":
        return "ended"
    if candidate.price_mxn_cents is None:
        return "no_price"
    if age(now, candidate.observed_at) > max_age:
        return "stale"
    if has_critical_risk(candidate):
        return "critical_risk"
    if (
        target.seller_id is not None
        and candidate.seller_id is not None
        and candidate.source_key == target.source_key
        and candidate.seller_id == target.seller_id
    ):
        return "same_seller"
    return None


def _sorted_counts(counts: dict[str, int]) -> dict[str, int]:
    return {key: counts[key] for key in sorted(counts) if counts[key] > 0}


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
    excluded_reason_counts).

    Rules (each rejected listing is counted once, under the first matching reason):

    ``self`` the target itself; ``different_product`` other/missing ``product_key`` (a target without
    a product key has no comparables); ``condition`` other condition class (``unknown`` counts as
    ``used``; ``for_parts`` never matches); ``accessory`` / ``bundle``; ``demo`` unless
    ``include_demo``; ``ended`` status ended (outcome unknown - ``sold`` listings ARE kept because
    they are transacted-price evidence); ``no_price`` no MXN price; ``stale`` observed more than
    ``max_age_days`` before ``now``; ``critical_risk`` critical code in ``attributes['risk_codes']``;
    ``same_seller`` same source and seller as the target; ``duplicate_seller`` extra listings of one
    (source, seller) - only the lowest total price is kept (ties: lowest id).  Listings without a
    ``seller_id`` cannot be deduplicated.

    The returned comparables are sorted by (total price, id); the reason dict is sorted by key and
    omits zero counts.
    """
    max_age = timedelta(days=max_age_days)
    target_class = condition_class(target.condition)
    counts: dict[str, int] = {}
    kept: list[ListingView] = []
    for candidate in sorted(pool, key=lambda item: item.id):
        reason = _exclusion_reason(
            target,
            candidate,
            target_class=target_class,
            now=now,
            max_age=max_age,
            include_demo=include_demo,
        )
        if reason is not None:
            counts[reason] = counts.get(reason, 0) + 1
            continue
        kept.append(candidate)

    best_by_seller: dict[tuple[str, str], ListingView] = {}
    anonymous: list[ListingView] = []
    for candidate in kept:
        if candidate.seller_id is None:
            anonymous.append(candidate)
            continue
        key = (candidate.source_key, candidate.seller_id)
        current = best_by_seller.get(key)
        if current is None:
            best_by_seller[key] = candidate
            continue
        counts["duplicate_seller"] = counts.get("duplicate_seller", 0) + 1
        if (resale_price_cents(candidate), candidate.id) < (resale_price_cents(current), current.id):
            best_by_seller[key] = candidate

    comparables = sorted(
        [*best_by_seller.values(), *anonymous],
        key=lambda item: (resale_price_cents(item) or 0, item.id),
    )
    return comparables, _sorted_counts(counts)


def seller_count(listings: Iterable[ListingView]) -> int:
    """Distinct (source, seller) pairs; listings without a seller id count as one seller each."""
    known: set[tuple[str, str]] = set()
    anonymous = 0
    for listing in listings:
        if listing.seller_id is None:
            anonymous += 1
        else:
            known.add((listing.source_key, listing.seller_id))
    return len(known) + anonymous


def comparable_stats(comparables: list[ListingView], excluded: dict[str, int], window_days: int) -> PriceStats:
    """PriceStats over comparables' resale price (item price in MXN), with source/seller counts,
    outlier trimming (robust z > 3.5 removed and counted in ``excluded['outlier']``).

    Outliers are detected against the median/MAD of the *initial* set and only when at least
    :data:`MIN_N_FOR_OUTLIERS` comparables exist and the MAD is non-zero.  ``excluded`` is copied, never
    mutated.  Listings without a price are ignored (counted as ``no_price``).
    """
    counts = dict(excluded)
    priced: list[tuple[int, ListingView]] = []
    for listing in comparables:
        total = resale_price_cents(listing)
        if total is None:
            counts["no_price"] = counts.get("no_price", 0) + 1
            continue
        priced.append((total, listing))
    priced.sort(key=lambda pair: (pair[0], pair[1].id))

    if len(priced) >= MIN_N_FOR_OUTLIERS:
        initial = robust_stats([total for total, _ in priced])
        retained: list[tuple[int, ListingView]] = []
        for total, listing in priced:
            z = _robust_z_decimal(total, initial.median_cents, initial.mad_cents)
            if z is not None and abs(z) > OUTLIER_Z:
                counts["outlier"] = counts.get("outlier", 0) + 1
            else:
                retained.append((total, listing))
        priced = retained

    stats = robust_stats([total for total, _ in priced])
    listings = [listing for _, listing in priced]
    stats.source_count = len({listing.source_key for listing in listings})
    stats.seller_count = seller_count(listings)
    stats.excluded = _sorted_counts(counts)
    stats.comparable_ids = sorted(listing.id for listing in listings)
    stats.window_days = window_days
    return stats
