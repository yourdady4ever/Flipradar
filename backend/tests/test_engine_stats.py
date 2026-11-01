from datetime import UTC, datetime, timedelta

import pytest

from flipradar.engine.stats import (
    comparable_stats,
    condition_class,
    has_critical_risk,
    quantile,
    robust_stats,
    robust_z,
    select_comparables,
    total_price_cents,
)
from flipradar.engine.types import ListingView

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
KEY = "apple-iphone-13-128"


def make_listing(listing_id: int, price: int = 1_000_000, **overrides) -> ListingView:
    data = dict(
        id=listing_id,
        source_key="ml",
        title=f"iPhone 13 128GB #{listing_id}",
        price_mxn_cents=price,
        shipping_mxn_cents=0,
        condition="used",
        product_key=KEY,
        match_confidence=0.95,
        observed_at=NOW - timedelta(days=1),
        seller_id=f"seller-{listing_id}",
    )
    data.update(overrides)
    return ListingView(**data)


# ------------------------------------------------------------------------------------------------
# quantile / robust_stats / robust_z
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("q", "expected"),
    [(0, 100), (0.25, 175), (0.5, 250), (0.75, 325), (1, 400)],
)
def test_quantile_type7_linear_interpolation(q, expected):
    assert quantile([100, 200, 300, 400], q) == expected


def test_quantile_rounds_half_up_not_bankers():
    assert quantile([10, 11], 0.5) == 11  # 10.5 -> 11 (banker's rounding would give 10)
    assert quantile([12, 13], 0.5) == 13  # 12.5 -> 13 (banker's rounding would give 12)
    assert quantile([10, 11], 0.25) == 10  # 10.25 -> 10


def test_quantile_single_value_and_errors():
    assert quantile([777], 0.25) == 777
    with pytest.raises(ValueError):
        quantile([], 0.5)
    with pytest.raises(ValueError):
        quantile([1, 2], 1.5)
    with pytest.raises(ValueError):
        quantile([1, 2], -0.1)


def test_robust_stats_known_values():
    s = robust_stats([1000, 100, 300, 400, 200])  # unsorted on purpose
    assert s.n == 5
    assert (s.median_cents, s.p25_cents, s.p75_cents) == (300, 200, 400)
    assert (s.min_cents, s.max_cents) == (100, 1000)
    assert s.iqr_cents == 200
    # |x - 300| = 200,100,0,100,700 -> median 100
    assert s.mad_cents == 100
    assert s.dispersion == pytest.approx(0.6667)


def test_robust_stats_even_count_median_is_average():
    s = robust_stats([100, 200, 301, 400])
    assert s.median_cents == 251  # 250.5 -> 251 half-up


def test_robust_stats_empty():
    s = robust_stats([])
    assert s.n == 0
    assert s.median_cents is None and s.mad_cents is None and s.dispersion is None


def test_robust_z():
    s = robust_stats([100, 200, 300, 400, 1000])
    # (0 - 300) / (1.4826 * 100) = -2.02347...
    assert robust_z(0, s) == -2.0235
    assert robust_z(300, s) == 0.0
    flat = robust_stats([500, 500, 500])
    assert flat.mad_cents == 0
    assert robust_z(100, flat) is None
    assert robust_z(100, robust_stats([])) is None


def test_condition_classes():
    assert condition_class("new") == "new"
    assert condition_class("refurbished") == "refurbished"
    assert condition_class("used") == "used"
    assert condition_class("unknown") == "used"
    assert condition_class("for_parts") is None


def test_total_price_includes_known_shipping_only():
    assert total_price_cents(make_listing(1, 10000, shipping_mxn_cents=500)) == 10500
    assert total_price_cents(make_listing(1, 10000, shipping_mxn_cents=None)) == 10000


# ------------------------------------------------------------------------------------------------
# select_comparables
# ------------------------------------------------------------------------------------------------


def test_every_exclusion_reason_is_counted():
    target = make_listing(1, seller_id="me", source_key="ml")
    pool = [
        target,
        make_listing(2),  # kept
        make_listing(3, status="sold"),  # kept: transacted-price evidence
        make_listing(4, product_key="other"),
        make_listing(5, product_key=None),
        make_listing(6, condition="new"),
        make_listing(7, condition="for_parts"),
        make_listing(8, is_accessory=True),
        make_listing(9, is_bundle=True),
        make_listing(10, is_demo=True),
        make_listing(11, status="ended"),
        make_listing(12, observed_at=NOW - timedelta(days=31)),
        make_listing(13, attributes={"risk_codes": ["replica_or_counterfeit"]}),
        make_listing(14, seller_id="me", source_key="ml"),
        make_listing(15, seller_id="me", source_key="facebook"),  # same id, other source: independent
        make_listing(16, condition="unknown"),  # unknown == used class: kept
        make_listing(17, price_mxn_cents=None),
    ]
    comps, excluded = select_comparables(target, pool, now=NOW, max_age_days=30)
    assert sorted(c.id for c in comps) == [2, 3, 15, 16]
    assert excluded == {
        "accessory": 1,
        "bundle": 1,
        "condition": 2,
        "critical_risk": 1,
        "demo": 1,
        "different_product": 2,
        "ended": 1,
        "no_price": 1,
        "same_seller": 1,
        "self": 1,
        "stale": 1,
    }


def test_include_demo_keeps_demo_listings():
    target = make_listing(1)
    pool = [target, make_listing(2, is_demo=True)]
    comps, excluded = select_comparables(target, pool, now=NOW, max_age_days=30, include_demo=True)
    assert [c.id for c in comps] == [2]
    assert "demo" not in excluded


def test_for_parts_target_never_matches():
    target = make_listing(1, condition="for_parts")
    pool = [target, make_listing(2, condition="for_parts"), make_listing(3)]
    comps, excluded = select_comparables(target, pool, now=NOW, max_age_days=30)
    assert comps == []
    assert excluded == {"condition": 2, "self": 1}


def test_unknown_target_compares_with_used_but_not_new_or_refurbished():
    target = make_listing(1, condition="unknown")
    pool = [target, make_listing(2, condition="used"), make_listing(3, condition="new"),
            make_listing(4, condition="refurbished")]
    comps, excluded = select_comparables(target, pool, now=NOW, max_age_days=30)
    assert [c.id for c in comps] == [2]
    assert excluded["condition"] == 2


def test_target_without_product_key_has_no_comparables():
    target = make_listing(1, product_key=None)
    pool = [target, make_listing(2, product_key=None), make_listing(3)]
    comps, excluded = select_comparables(target, pool, now=NOW, max_age_days=30)
    assert comps == []
    assert excluded == {"different_product": 2, "self": 1}


def test_stale_boundary_is_inclusive():
    target = make_listing(1)
    exactly = make_listing(2, observed_at=NOW - timedelta(days=30))
    older = make_listing(3, observed_at=NOW - timedelta(days=30, seconds=1))
    comps, excluded = select_comparables(target, [target, exactly, older], now=NOW, max_age_days=30)
    assert [c.id for c in comps] == [2]
    assert excluded["stale"] == 1


def test_naive_and_aware_datetimes_can_be_mixed():
    target = make_listing(1)
    naive = make_listing(2, observed_at=datetime(2026, 9, 30, 12, 0))  # naive = UTC
    comps, _ = select_comparables(target, [target, naive], now=NOW, max_age_days=30)
    assert [c.id for c in comps] == [2]


def test_per_seller_dedupe_keeps_lowest_item_price():
    target = make_listing(1)
    pool = [
        target,
        make_listing(2, 900_000, seller_id="A", shipping_mxn_cents=50_000),  # item 900,000 -> kept
        make_listing(3, 920_000, seller_id="A", shipping_mxn_cents=0),  # item 920,000
        make_listing(4, 930_000, seller_id="A"),
        make_listing(5, 800_000, seller_id="A", source_key="amazon"),  # other source: separate seller
        make_listing(6, 700_000, seller_id=None),
        make_listing(7, 700_000, seller_id=None),  # anonymous listings are never deduplicated
    ]
    comps, excluded = select_comparables(target, pool, now=NOW, max_age_days=30)
    assert sorted(c.id for c in comps) == [2, 5, 6, 7]
    assert excluded["duplicate_seller"] == 2


def test_buy_side_only_sources_are_not_resale_comparables():
    target = make_listing(1)
    pool = [
        target,
        make_listing(2, 900_000, seller_id="A"),
        make_listing(3, 500_000, seller_id="B", source_key="ebay", resale_reference=False),
    ]
    comps, excluded = select_comparables(target, pool, now=NOW, max_age_days=30)
    assert [c.id for c in comps] == [2]
    assert excluded["not_resale_market"] == 1


def test_dedupe_tie_keeps_lowest_id():
    target = make_listing(1)
    pool = [target, make_listing(9, 500_000, seller_id="A"), make_listing(4, 500_000, seller_id="A")]
    comps, excluded = select_comparables(target, pool, now=NOW, max_age_days=30)
    assert [c.id for c in comps] == [4]
    assert excluded == {"duplicate_seller": 1, "self": 1}


@pytest.mark.parametrize(
    ("risk_codes", "critical"),
    [
        (["replica_or_counterfeit"], True),
        (["box_or_parts_only", "shipping_unknown"], True),
        ("locked_or_reported", True),
        ([{"code": "price_far_below_market", "severity": "critical"}], True),
        ([{"code": "price_far_below_market", "severity": "warning"}], False),
        (["price_far_below_market"], False),  # severity unknown for a bare code that can be a warning
        (["shipping_unknown", "seller_unknown"], False),
        (None, False),
    ],
)
def test_critical_risk_codes(risk_codes, critical):
    listing = make_listing(2, attributes={"risk_codes": risk_codes} if risk_codes is not None else {})
    assert has_critical_risk(listing) is critical


def test_comparables_sorted_by_total_then_id():
    target = make_listing(1)
    pool = [target, make_listing(5, 300), make_listing(3, 100), make_listing(4, 100)]
    comps, _ = select_comparables(target, pool, now=NOW, max_age_days=30)
    assert [c.id for c in comps] == [3, 4, 5]


# ------------------------------------------------------------------------------------------------
# comparable_stats
# ------------------------------------------------------------------------------------------------


def test_comparable_stats_uses_item_price_and_counts():
    comps = [
        make_listing(2, 1_000_000, shipping_mxn_cents=20_000, source_key="ml", seller_id="a"),
        make_listing(3, 1_000_000, shipping_mxn_cents=None, source_key="ml", seller_id="b"),
        make_listing(4, 1_100_000, shipping_mxn_cents=0, source_key="amazon", seller_id="a"),
        make_listing(5, 900_000, shipping_mxn_cents=0, source_key="ml", seller_id=None),
    ]
    excluded = {"self": 1}
    s = comparable_stats(comps, excluded, 30)
    assert s.n == 4
    assert (s.min_cents, s.max_cents) == (900_000, 1_100_000)
    assert s.median_cents == 1_000_000  # item prices 900k, 1.0M, 1.0M, 1.1M (shipping ignored)
    assert s.source_count == 2
    assert s.seller_count == 4  # (ml,a), (ml,b), (amazon,a), anonymous
    assert s.comparable_ids == [2, 3, 4, 5]
    assert s.window_days == 30
    assert s.excluded == {"self": 1}
    assert excluded == {"self": 1}  # input not mutated


def test_outlier_trimming_removes_extreme_prices():
    prices = [950_000, 980_000, 1_000_000, 1_020_000, 1_050_000, 5_000_000, 100_000]
    comps = [make_listing(i + 2, p) for i, p in enumerate(prices)]
    s = comparable_stats(comps, {}, 30)
    assert s.excluded == {"outlier": 2}
    assert s.n == 5
    assert (s.min_cents, s.max_cents) == (950_000, 1_050_000)
    assert 7 not in s.comparable_ids and 8 not in s.comparable_ids


def test_no_outlier_trimming_below_five_comparables():
    comps = [make_listing(i + 2, p) for i, p in enumerate([1_000_000, 1_010_000, 1_020_000, 9_000_000])]
    s = comparable_stats(comps, {}, 30)
    assert s.n == 4
    assert "outlier" not in s.excluded
    assert s.max_cents == 9_000_000


def test_no_outlier_trimming_when_mad_is_zero():
    comps = [make_listing(i + 2, p) for i, p in enumerate([500_000] * 4 + [9_000_000])]
    s = comparable_stats(comps, {}, 30)
    assert s.mad_cents == 0
    assert s.n == 5
    assert "outlier" not in s.excluded


def test_moderate_spread_is_not_trimmed():
    prices = [800_000, 900_000, 1_000_000, 1_100_000, 1_200_000, 1_300_000]
    s = comparable_stats([make_listing(i + 2, p) for i, p in enumerate(prices)], {}, 30)
    assert s.n == 6
    assert "outlier" not in s.excluded


def test_empty_comparables():
    s = comparable_stats([], {"self": 1}, 30)
    assert s.n == 0 and s.median_cents is None
    assert s.source_count == 0 and s.seller_count == 0
    assert s.excluded == {"self": 1}


def test_stats_are_deterministic():
    target = make_listing(1)
    pool = [target] + [make_listing(i, 900_000 + i * 10_000, seller_id=f"s{i % 4}") for i in range(2, 15)]
    first = select_comparables(target, pool, now=NOW, max_age_days=30)
    second = select_comparables(target, list(reversed(pool)), now=NOW, max_age_days=30)
    assert [c.id for c in first[0]] == [c.id for c in second[0]]
    assert first[1] == second[1]
    assert comparable_stats(*first, 30).to_dict() == comparable_stats(*second, 30).to_dict()
