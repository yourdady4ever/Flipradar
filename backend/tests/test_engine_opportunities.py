import json
import re
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest

from flipradar.engine.explain import fmt_money, render_explanation
from flipradar.engine.opportunities import AnalysisSettings, analyze_product
from flipradar.engine.types import ListingView
from flipradar.finance.calculator import (
    FeeModel,
    FixedFeeTier,
    ProfitInputs,
    break_even_sale_price,
    calculate_profit,
    max_buy_price_for_roi,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
KEY = "apple-iphone-13-128"
LOCAL = FeeModel(key="local", name="Local cash sale")
ML_LIKE = FeeModel(
    key="ml_clasica_test",
    name="ML Clasica (test)",
    commission_bp=1500,
    fixed_fee_tiers=(FixedFeeTier(29899, 2500),),
    seller_shipping_cents=12000,
    free_shipping_threshold_cents=29900,
    isr_withholding_bp=250,
    iva_withholding_bp=800,
    as_of="2026-09-01",
)
GOOD_SELLER = {"transactions_total": 300, "reputation_level": "5_green", "registration_date": "2017-05-01T00:00:00Z"}


def make_listing(listing_id: int, price: int, **overrides) -> ListingView:
    data = dict(
        id=listing_id,
        source_key="ml",
        title=f"Apple iPhone 13 128GB usado {listing_id}",
        price_mxn_cents=price,
        shipping_mxn_cents=0,
        condition="used",
        product_key=KEY,
        match_confidence=0.95,
        observed_at=NOW - timedelta(days=1),
        url=f"https://example.com/item/MLM{listing_id}",
        seller_id=f"seller-{listing_id}",
        seller=dict(GOOD_SELLER),
    )
    data.update(overrides)
    return ListingView(**data)


def market(buy_price: int = 600_000, **buy_overrides) -> list[ListingView]:
    """Buy listing #1 plus six comparables at $9,000 .. $11,500 (step $500)."""
    comparables = [make_listing(i, 900_000 + (i - 2) * 50_000) for i in range(2, 8)]
    return [make_listing(1, buy_price, **buy_overrides), *comparables]


def run(listings, fee=LOCAL, settings=None, observations=None):
    return analyze_product(KEY, listings, fee, settings or AnalysisSettings(), observations or {}, now=NOW)


def by_id(candidates, listing_id):
    matches = [c for c in candidates if c.buy.id == listing_id]
    return matches[0] if matches else None


# ------------------------------------------------------------------------------------------------
# Number provenance helper: every number in an explanation must exist in the facts.
# ------------------------------------------------------------------------------------------------

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    return {match.group(0).replace(",", "") for match in NUMBER_RE.finditer(text)}


def fact_numbers(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= fact_numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= fact_numbers(item)
    elif isinstance(value, bool) or value is None:
        pass
    elif isinstance(value, (int, float)):
        found |= numbers_in(str(value))
    elif isinstance(value, str):
        found |= numbers_in(value)
    return found


def numbers_missing_from_facts(text: str, facts: dict) -> set[str]:
    return numbers_in(text) - fact_numbers(facts)


def test_number_helper_detects_invented_numbers():
    facts = {"net_profit": "$3,625.00", "n": 6, "nested": [{"pct": "60.42%"}]}
    assert numbers_missing_from_facts("Profit $3,625.00 from 6 comps (60.42%)", facts) == set()
    assert numbers_missing_from_facts("Profit $3,625.00 and demand of 42 units", facts) == {"42"}
    assert numbers_missing_from_facts("Profit $3,625.01", facts) == {"3625.01"}


# ------------------------------------------------------------------------------------------------
# Core analysis
# ------------------------------------------------------------------------------------------------


def test_basic_candidate_math_competitive_strategy():
    candidate = by_id(run(market()), 1)
    assert candidate is not None
    assert candidate.stats.n == 6
    assert candidate.stats.comparable_ids == [2, 3, 4, 5, 6, 7]
    # p25 of 900k..1,150k (step 50k), type-7: 950,000 + 0.25 * 50,000
    assert candidate.expected_sale_cents == 962_500
    assert candidate.buy_total_cents == 600_000
    assert candidate.net_profit_cents == 362_500
    assert candidate.roi_bp == 6042  # 362,500 / 600,000 = 60.4167 %
    assert candidate.margin_bp == 3766
    assert candidate.facts["estimate_method"] == "comparables_p25"
    assert candidate.facts["estimate_percentile"] == 25
    assert candidate.facts["comparables_median_cents"] == 1_025_000
    assert candidate.profit["net_profit_cents"] == candidate.net_profit_cents


def test_market_strategy_uses_median():
    settings = AnalysisSettings(pricing_strategy="market")
    candidate = by_id(run(market(), settings=settings), 1)
    assert candidate.expected_sale_cents == 1_025_000
    assert candidate.facts["estimate_method"] == "comparables_median"


def test_invalid_strategy_raises():
    with pytest.raises(ValueError):
        run(market(), settings=AnalysisSettings(pricing_strategy="moon"))


def test_profit_matches_calculator_with_real_fee_model():
    candidate = by_id(run(market(), fee=ML_LIKE), 1)
    inputs = ProfitInputs(buy_price_cents=600_000, buy_shipping_cents=0, sale_price_cents=962_500, fee=ML_LIKE)
    expected = calculate_profit(inputs)
    assert candidate.profit == expected.to_dict()
    assert candidate.net_profit_cents == expected.net_profit_cents
    assert candidate.facts["break_even_sale_cents"] == break_even_sale_price(inputs)
    assert candidate.facts["max_buy_price_cents"] == max_buy_price_for_roi(inputs, 1000)
    assert candidate.facts["target_roi_pct"] == "10.00%"
    assert candidate.facts["fee_profile_name"] == "ML Clasica (test)"
    assert candidate.facts["fee_profile_as_of"] == "2026-09-01"


def test_sold_comparables_are_preferred_when_enough():
    listings = [
        make_listing(1, 600_000),
        make_listing(2, 1_000_000, status="sold"),
        make_listing(3, 1_010_000, status="sold"),
        make_listing(4, 1_020_000, status="sold"),
        make_listing(5, 900_000),
        make_listing(6, 950_000),
        make_listing(7, 1_100_000),
    ]
    candidate = by_id(run(listings), 1)
    assert candidate.expected_sale_cents == 1_010_000  # median of the sold totals
    assert candidate.facts["estimate_method"] == "sold_median"
    assert candidate.facts["sold_comparables_n"] == 3
    assert candidate.facts["estimate_method_label"] == "median of 3 sold comparables"
    assert not any("asking prices" in caveat for caveat in candidate.facts["caveats"])
    # sold listings are evidence, never buy candidates
    assert {c.buy.id for c in run(listings)} <= {1, 5, 6, 7}

    # with a stricter minimum the 3 sold listings are not enough -> p25 of all comparables
    strict = by_id(run(listings, settings=AnalysisSettings(min_comparables=4)), 1)
    assert strict.facts["estimate_method"] == "comparables_p25"
    assert strict.expected_sale_cents == 962_500
    assert any("asking prices" in caveat for caveat in strict.facts["caveats"])


def test_insufficient_evidence_produces_no_candidate():
    two_comparables = [make_listing(1, 600_000), make_listing(2, 1_000_000), make_listing(3, 1_050_000)]
    assert run(two_comparables) == []
    # plenty of listings, but none comparable (stale / other condition / accessory / other product)
    noise = [
        make_listing(1, 600_000),
        make_listing(2, 1_000_000, observed_at=NOW - timedelta(days=60)),
        make_listing(3, 1_000_000, condition="new"),
        make_listing(4, 1_000_000, is_accessory=True),
        make_listing(5, 1_000_000, product_key="other"),
        make_listing(6, 1_000_000, status="ended"),
    ]
    assert run(noise) == []


def test_outlier_trimming_can_remove_the_evidence():
    listings = [make_listing(1, 600_000)] + [
        make_listing(i + 2, p) for i, p in enumerate([990_000, 1_000_000, 1_010_000, 1_020_000, 9_000_000])
    ]
    settings = AnalysisSettings(min_comparables=5)
    assert by_id(run(listings, settings=settings), 1) is None  # 5 comparables - 1 outlier = 4 < 5
    candidate = by_id(run(listings, settings=AnalysisSettings(min_comparables=4)), 1)
    assert candidate.stats.excluded["outlier"] == 1
    assert candidate.stats.max_cents == 1_020_000


def test_profit_and_roi_thresholds_filter_candidates():
    assert by_id(run(market(), settings=AnalysisSettings(min_roi_bp=7000)), 1) is None
    assert by_id(run(market(), settings=AnalysisSettings(min_profit_cents=400_000)), 1) is None
    assert by_id(run(market(), settings=AnalysisSettings(min_profit_cents=362_500, min_roi_bp=6042)), 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_accessory": True},
        {"is_bundle": True},
        {"condition": "for_parts"},
        {"status": "ended"},
        {"status": "sold"},
        {"status": "unknown"},
        {"is_demo": True},
        {"price_mxn_cents": None},
        {"product_key": "other-product"},
    ],
)
def test_non_candidates_are_skipped(overrides):
    assert by_id(run(market(**overrides)), 1) is None


def test_demo_buy_listing_allowed_when_requested():
    listings = market(is_demo=True)
    assert by_id(run(listings, settings=AnalysisSettings(include_demo=True)), 1) is not None


def test_unknown_shipping_uses_assumption_and_flags_it():
    candidate = by_id(run(market(shipping_mxn_cents=None),
                          settings=AnalysisSettings(unknown_shipping_assumption_cents=15_000)), 1)
    assert candidate.buy_total_cents == 615_000
    lines = {line["key"]: line["amount_cents"] for line in candidate.profit["acquisition_lines"]}
    assert lines["buy_shipping"] == 15_000
    flag = next(f for f in candidate.risk_flags if f.code == "shipping_unknown")
    assert flag.severity == "info"
    assert flag.evidence["assumed_shipping_cents"] == 15_000
    assert candidate.facts["buy_shipping_known"] is False
    assert any("$150.00 was assumed" in caveat for caveat in candidate.facts["caveats"])
    assert "(not reported, assumed)" in candidate.explanation

    default = by_id(run(market(shipping_mxn_cents=None)), 1)
    assert default.buy_total_cents == 600_000
    assert any(f.code == "shipping_unknown" for f in default.risk_flags)


def test_known_buy_shipping_is_an_acquisition_cost():
    candidate = by_id(run(market(shipping_mxn_cents=20_000)), 1)
    assert candidate.buy_total_cents == 620_000
    assert candidate.net_profit_cents == 962_500 - 620_000


def test_suspicious_listing_scores_lower_with_low_confidence():
    clean = market()
    replica = market(title="iPhone 13 128GB réplica AAA 1:1")
    clean_candidate = by_id(run(clean), 1)
    replica_candidate = by_id(run(replica), 1)
    assert replica_candidate.score < clean_candidate.score
    assert replica_candidate.confidence == "low"
    assert replica_candidate.facts["risk_level"] == "critical"
    assert "[critical] replica_or_counterfeit" in replica_candidate.explanation


def test_critical_risk_listings_are_not_used_as_comparables():
    listings = market() + [make_listing(8, 100_000, attributes={"risk_codes": ["replica_or_counterfeit"]})]
    candidate = by_id(run(listings), 1)
    assert 8 not in candidate.stats.comparable_ids
    assert candidate.stats.excluded["critical_risk"] == 1


def test_results_sorted_by_score_then_profit_then_id():
    listings = market() + [make_listing(8, 600_000), make_listing(9, 700_000), make_listing(10, 650_000)]
    results = run(listings, settings=AnalysisSettings(pricing_strategy="market"))
    assert len(results) >= 3
    keys = [(-c.score, -c.net_profit_cents, c.buy.id) for c in results]
    assert keys == sorted(keys)
    # listings 1 and 8 are identical offers -> identical score and profit -> lower id first
    ids = [c.buy.id for c in results]
    assert by_id(results, 1).score == by_id(results, 8).score
    assert ids.index(1) == ids.index(8) - 1


def test_demand_uses_product_evidence_but_not_accessories():
    listings = market() + [make_listing(20, 50_000, is_accessory=True)]
    observations = {
        3: [(NOW - timedelta(days=10), 1_000_000, 10, "active"), (NOW - timedelta(days=1), 1_000_000, 19, "active")],
        20: [(NOW - timedelta(days=10), 50_000, 0, "active"), (NOW - timedelta(days=1), 50_000, 500, "active")],
    }
    candidate = by_id(run(listings, observations=observations), 1)
    assert candidate.demand.level == "high"  # 9 units / 9 days = 1/day (accessory excluded)
    rate = next(s for s in candidate.demand.signals if s["name"] == "sales_rate")
    assert rate["value"] == 1.0
    assert candidate.facts["demand_level"] == "high"


def test_demand_unknown_without_evidence_is_explained():
    candidate = by_id(run(market()), 1)
    assert candidate.demand.level == "unknown"
    assert "Level: unknown" in candidate.explanation
    assert any("Demand is unknown" in caveat for caveat in candidate.facts["caveats"])


# ------------------------------------------------------------------------------------------------
# Facts & explanation
# ------------------------------------------------------------------------------------------------


def test_explanation_contains_formatted_profit_and_sections():
    candidate = by_id(run(market(), fee=ML_LIKE), 1)
    text = candidate.explanation
    assert fmt_money(candidate.net_profit_cents) in text
    assert candidate.facts["net_profit"] == fmt_money(candidate.net_profit_cents)
    for section in ("Buy", "Resale estimate", "Costs", "Profit", "Demand", "Risks", "Score", "Caveats"):
        assert f"\n{section}" in text
    assert "not guaranteed sale prices" in text
    assert "tax regime" in text
    assert candidate.facts["roi_pct"] in text
    assert candidate.facts["expected_sale"] in text


@pytest.mark.parametrize(
    "scenario",
    ["competitive_ml", "market_local", "sold", "unknown_shipping", "suspicious", "with_demand"],
)
def test_explanation_never_contains_numbers_absent_from_facts(scenario):
    settings = AnalysisSettings()
    fee = ML_LIKE
    observations = {}
    listings = market()
    if scenario == "market_local":
        settings, fee = AnalysisSettings(pricing_strategy="market"), LOCAL
    elif scenario == "sold":
        listings = [make_listing(1, 600_000)] + [
            make_listing(i, 1_000_000 + i * 1000, status="sold") for i in range(2, 6)
        ]
    elif scenario == "unknown_shipping":
        listings = market(shipping_mxn_cents=None)
        settings = AnalysisSettings(unknown_shipping_assumption_cents=12_345)
    elif scenario == "suspicious":
        listings = market(title="iPhone 13 réplica", description="Whatsapp 55 1234 5678",
                          seller={"transactions_total": 1}, match_confidence=0.7)
    elif scenario == "with_demand":
        observations = {i: [(NOW - timedelta(days=12), None, 5, "active"), (NOW, None, 8, "active")]
                        for i in range(2, 8)}
    results = run(listings, fee=fee, settings=settings, observations=observations)
    assert results
    for candidate in results:
        missing = numbers_missing_from_facts(candidate.explanation, candidate.facts)
        assert missing == set(), f"numbers not grounded in facts: {missing}\n{candidate.explanation}"


def test_facts_are_json_serialisable_and_self_sufficient():
    candidate = by_id(run(market(), fee=ML_LIKE), 1)
    roundtrip = json.loads(json.dumps(candidate.facts))
    assert roundtrip == candidate.facts
    assert render_explanation(roundtrip) == candidate.explanation


def test_explanation_uses_only_facts():
    candidate = by_id(run(market()), 1)
    tampered = dict(candidate.facts, net_profit="$1.23", buy_title="Something else")
    text = render_explanation(tampered)
    assert "$1.23" in text and "Something else" in text
    assert candidate.facts["net_profit"] not in text.split("Profit\n", 1)[1].split("\n")[0]


def test_facts_contain_money_as_cents_and_strings():
    facts = by_id(run(market(), fee=ML_LIKE), 1).facts
    for key in ("buy_price", "buy_shipping", "buy_total", "expected_sale", "net_proceeds", "net_profit",
                "break_even_sale", "max_buy_price", "comparables_median", "comparables_min", "comparables_max"):
        assert isinstance(facts[f"{key}_cents"], int), key
        assert facts[key] == fmt_money(facts[f"{key}_cents"]), key
    for line in facts["selling_lines"] + facts["acquisition_lines"]:
        assert line["amount"] == fmt_money(line["amount_cents"])
    assert facts["comparables_n"] == 6
    assert facts["comparables_window_days"] == 30
    assert facts["comparables_sources"] == ["ml"]
    assert facts["fee_profile_key"] == "ml_clasica_test"
    assert {c["key"] for c in facts["score_components"]} == {"roi", "profit", "price_evidence", "match",
                                                             "demand", "risk"}


# ------------------------------------------------------------------------------------------------
# Determinism
# ------------------------------------------------------------------------------------------------


def test_analysis_is_deterministic_and_order_independent():
    listings = market() + [make_listing(8, 640_000, shipping_mxn_cents=None, seller={}),
                           make_listing(9, 700_000, title="iPhone 13 libre de iCloud, whatsapp")]
    observations = {3: [(NOW - timedelta(days=8), None, 1, "active"), (NOW, None, 4, "active")]}
    first = run(listings, fee=ML_LIKE, observations=observations)
    second = run(listings, fee=ML_LIKE, observations=observations)
    shuffled = run(list(reversed(listings)), fee=ML_LIKE, observations=observations)
    assert [asdict(c) for c in first] == [asdict(c) for c in second] == [asdict(c) for c in shuffled]
    assert [c.facts for c in first] == [c.facts for c in second]


def test_input_listings_are_not_mutated():
    listings = market()
    snapshot = [replace(item, attributes=dict(item.attributes), seller=dict(item.seller)) for item in listings]
    run(listings, fee=ML_LIKE)
    assert listings == snapshot


def test_fee_model_selected_by_buy_condition():
    from flipradar.finance.calculator import FeeModel as _FM

    new_model = _FM(key="new_model", name="New", commission_bp=1000)
    used_model = _FM(key="used_model", name="Used", commission_bp=0)
    listings = [
        ListingView(
            id=i, source_key="ml", title="x", price_mxn_cents=p, shipping_mxn_cents=0, condition="used",
            product_key="k", match_confidence=0.95, observed_at=NOW, seller_id=f"s{i}",
        )
        for i, p in [(1, 500_000), (2, 1_000_000), (3, 1_000_000), (4, 1_000_000), (5, 1_000_000)]
    ]
    settings = AnalysisSettings(min_comparables=3, min_profit_cents=0, min_roi_bp=0)
    cands = analyze_product(
        "k", listings, new_model, settings, {}, now=NOW,
        fee_models_by_condition={"new": new_model, "used": used_model},
    )
    assert cands and all(c.fee_profile_key == "used_model" for c in cands)
    best = next(c for c in cands if c.buy.id == 1)
    assert best.net_profit_cents == 1_000_000 - 500_000
