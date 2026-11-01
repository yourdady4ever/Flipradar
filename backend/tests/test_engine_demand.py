from datetime import UTC, datetime, timedelta

from flipradar.engine.demand import estimate_demand
from flipradar.engine.types import ListingView

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def make_listing(listing_id: int, **overrides) -> ListingView:
    data = dict(
        id=listing_id,
        source_key="ml",
        title=f"PS5 Slim #{listing_id}",
        price_mxn_cents=900_000,
        shipping_mxn_cents=0,
        condition="new",
        product_key="sony-ps5-slim",
        match_confidence=0.95,
        observed_at=NOW - timedelta(hours=2),
        seller_id=f"seller-{listing_id}",
    )
    data.update(overrides)
    return ListingView(**data)


def readings(*points: tuple[float, int | None], status: str = "active"):
    """points: (days_ago, sold_quantity)."""
    return [(NOW - timedelta(days=days), 900_000, sold, status) for days, sold in points]


def signal(estimate, name):
    matches = [s for s in estimate.signals if s["name"] == name]
    return matches[0] if matches else None


def test_unknown_without_evidence_even_with_lots_of_supply():
    listings = [make_listing(i) for i in range(1, 51)]
    est = estimate_demand(listings, {}, now=NOW)
    assert est.level == "unknown"
    assert est.confidence is None
    assert "unknown, not zero" in est.note
    assert signal(est, "active_listings")["value"] == 50
    assert signal(est, "distinct_sellers")["value"] == 50
    assert signal(est, "sales_rate") is None


def test_every_signal_cites_its_source():
    listings = [make_listing(1), make_listing(2, status="sold")]
    obs = {1: readings((10, 3), (0, 7))}
    est = estimate_demand(listings, obs, now=NOW)
    assert est.signals
    for s in est.signals:
        assert set(s) == {"name", "value", "unit", "source", "note"}
        assert s["source"] and s["note"]
    assert "only visible for some sources" in signal(est, "sold_quantity_delta")["note"]
    assert "not demand" in signal(est, "active_listings")["note"]


def test_units_per_day_from_sold_quantity_deltas():
    est = estimate_demand([make_listing(1)], {1: readings((10, 10), (5, 12), (0, 14))}, now=NOW)
    assert signal(est, "sold_quantity_delta")["value"] == 4
    assert signal(est, "sales_rate")["value"] == 0.4  # 4 units / 10 days
    assert signal(est, "sales_rate")["unit"] == "units/day"
    assert est.level == "medium"
    assert est.confidence == "medium"  # one listing, but span >= 7 days


def test_rate_uses_union_span_across_listings():
    obs = {1: readings((20, 0), (10, 2)), 2: readings((10, 5), (1, 8))}
    est = estimate_demand([make_listing(1), make_listing(2)], obs, now=NOW)
    assert signal(est, "sold_quantity_delta")["value"] == 5
    assert signal(est, "sales_rate")["value"] == round(5 / 19, 4)
    assert est.level == "medium"
    assert est.confidence == "medium"


def test_high_demand_high_confidence():
    listings = [make_listing(i) for i in range(1, 6)]
    obs = {i: readings((14, 100), (0, 106)) for i in range(1, 6)}  # 30 units over 14 days
    est = estimate_demand(listings, obs, now=NOW)
    assert signal(est, "sales_rate")["value"] == round(30 / 14, 4)
    assert est.level == "high"
    assert est.confidence == "high"


def test_low_confidence_with_one_listing_and_short_span():
    est = estimate_demand([make_listing(1)], {1: readings((3, 0), (0, 1))}, now=NOW)
    assert est.level == "medium"  # 1 unit / 3 days = 0.3333
    assert est.confidence == "low"


def test_zero_delta_is_evidence_of_low_demand():
    est = estimate_demand([make_listing(1)], {1: readings((10, 50), (0, 50))}, now=NOW)
    assert est.level == "low"
    assert signal(est, "sales_rate")["value"] == 0.0


def test_span_shorter_than_two_days_is_not_enough():
    est = estimate_demand([make_listing(1)], {1: readings((1.5, 10), (0, 20))}, now=NOW)
    assert est.level == "unknown"
    assert signal(est, "sold_quantity_delta")["value"] == 10
    assert signal(est, "sales_rate") is None
    assert "cover only" in est.note


def test_readings_outside_window_or_null_are_ignored():
    obs = {1: readings((40, 0), (35, 5), (0, 20)) + readings((5, None))}
    est = estimate_demand([make_listing(1)], obs, now=NOW)
    assert est.level == "unknown"  # only one non-null reading inside the 30-day window
    assert signal(est, "sold_quantity_delta") is None


def test_future_readings_are_ignored():
    obs = {1: readings((5, 0), (-3, 30))}  # -3 days = in the future relative to now
    est = estimate_demand([make_listing(1)], obs, now=NOW)
    assert est.level == "unknown"


def test_decreasing_counter_is_ignored_and_reported():
    obs = {1: readings((10, 50), (0, 40)), 2: readings((10, 0), (0, 3))}
    est = estimate_demand([make_listing(1), make_listing(2)], obs, now=NOW)
    assert signal(est, "sold_quantity_delta")["value"] == 3
    assert "decreasing" in est.note


def test_sold_listings_count_as_evidence():
    listings = [make_listing(i, status="sold", observed_at=NOW - timedelta(days=i)) for i in range(1, 4)]
    listings.append(make_listing(9, status="sold", observed_at=NOW - timedelta(days=45)))  # too old
    listings.append(make_listing(10, status="ended"))  # outcome unknown: not a sale
    est = estimate_demand(listings, {}, now=NOW)
    assert signal(est, "sold_listings")["value"] == 3
    assert est.level == "low"  # 3 / 30 days = 0.1 per day
    assert est.confidence == "medium"


def test_sold_status_in_observations_counts():
    listings = [make_listing(i) for i in range(1, 7)]
    obs = {i: readings((2, None), status="sold") for i in range(1, 7)}
    est = estimate_demand(listings, obs, now=NOW)
    assert signal(est, "sold_listings")["value"] == 6
    assert est.level == "medium"  # 6 / 30 = 0.2


def test_single_sold_listing_is_low_confidence():
    est = estimate_demand([make_listing(1, status="sold")], {}, now=NOW)
    assert est.level == "low"
    assert est.confidence == "low"


def test_larger_rate_wins_without_double_counting():
    listings = [make_listing(1), make_listing(2, status="sold")]
    obs = {1: readings((10, 0), (0, 20))}  # 2 units/day
    est = estimate_demand(listings, obs, now=NOW)
    assert est.level == "high"
    assert "sold_quantity changes" in est.note


def test_distinct_sellers_counts_source_and_seller():
    listings = [
        make_listing(1, seller_id="a"),
        make_listing(2, seller_id="a"),
        make_listing(3, seller_id="a", source_key="amazon"),
        make_listing(4, seller_id=None),
        make_listing(5, seller_id="b", observed_at=NOW - timedelta(days=60)),  # stale: not current supply
    ]
    est = estimate_demand(listings, {}, now=NOW)
    assert signal(est, "active_listings")["value"] == 4
    assert signal(est, "distinct_sellers")["value"] == 3


def test_deterministic_and_order_independent():
    listings = [make_listing(i) for i in range(1, 6)] + [make_listing(7, status="sold")]
    obs = {i: readings((12, i), (6, i + 1), (0, i * 2)) for i in range(1, 6)}
    first = estimate_demand(listings, obs, now=NOW).to_dict()
    second = estimate_demand(list(reversed(listings)), {k: list(reversed(v)) for k, v in obs.items()}, now=NOW)
    assert first == second.to_dict()
