"""Calculator endpoints must return exactly what the deterministic finance module computes."""

from decimal import Decimal

from flipradar.finance.calculator import (
    FeeModel,
    ProfitInputs,
    break_even_sale_price,
    calculate_profit,
    max_buy_price_for_roi,
)
from flipradar.finance.import_costs import estimate_import_costs
from flipradar.services.fees import BUILTIN_FEE_PROFILES


class _Row:
    def __init__(self, data):
        self.__dict__.update(data)


def builtin_model(key: str) -> FeeModel:
    spec = next(p for p in BUILTIN_FEE_PROFILES if p["key"] == key)
    return FeeModel.from_profile(_Row(spec))


def test_profit_matches_finance_module(api):
    body = {"buy_price_cents": 1_000_000, "sale_price_cents": 1_450_000, "fee_profile_key": "ml_clasica",
            "buy_shipping_cents": 15_000, "other_sale_costs_cents": 5_000, "target_roi_bp": 2500}
    r = api.post("/api/v1/calculator/profit", json=body)
    assert r.status_code == 200, r.text
    inputs = ProfitInputs(buy_price_cents=1_000_000, sale_price_cents=1_450_000, fee=builtin_model("ml_clasica"),
                          buy_shipping_cents=15_000, other_sale_costs_cents=5_000)
    expected = calculate_profit(inputs)
    out = r.json()
    assert out["result"] == expected.to_dict()
    assert out["break_even_sale_cents"] == break_even_sale_price(inputs)
    assert out["max_buy_for_target_cents"] == max_buy_price_for_roi(inputs, 2500)
    assert out["target_roi_bp"] == 2500
    lines = {line["key"]: line["amount_cents"] for line in out["result"]["selling_lines"]}
    assert lines["commission"] == 217_500  # 15 % of $14,500.00
    assert lines["fixed_fee"] == 6_550


def test_profit_uses_default_profile_and_target(api):
    r = api.post("/api/v1/calculator/profit", json={"buy_price_cents": 100_000, "sale_price_cents": 150_000})
    assert r.status_code == 200
    out = r.json()
    assert out["result"]["fee_profile_key"] == "ml_clasica"
    assert out["target_roi_bp"] == 2000


def test_profit_with_inline_profile(api):
    profile = {"key": "my_local", "name": "Local", "commission_bp": 1000,
               "fixed_fee_tiers": [{"max_price_cents": 50_000, "fee_cents": 1_000}, {"max_price_cents": None, "fee_cents": 2_000}]}
    r = api.post("/api/v1/calculator/profit", json={"buy_price_cents": 50_000, "sale_price_cents": 100_000,
                                                    "fee_profile": profile, "fee_profile_key": "ml_clasica"})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["fee_profile_key"] == "my_local"
    assert result["total_selling_costs_cents"] == 10_000 + 2_000
    assert result["net_profit_cents"] == 100_000 - 12_000 - 50_000


def test_profit_errors(api):
    assert api.post("/api/v1/calculator/profit", json={"buy_price_cents": 1, "sale_price_cents": 2,
                                                       "fee_profile_key": "nope"}).status_code == 404
    assert api.post("/api/v1/calculator/profit", json={"buy_price_cents": -1, "sale_price_cents": 2}).status_code == 422
    bad_tiers = {"key": "bad", "name": "Bad", "fixed_fee_tiers": [{"max_price_cents": 500, "fee_cents": 1},
                                                                   {"max_price_cents": 100, "fee_cents": 1}]}
    assert api.post("/api/v1/calculator/profit", json={"buy_price_cents": 1, "sale_price_cents": 2,
                                                       "fee_profile": bad_tiers}).status_code == 422
    unbounded_first = {"key": "bad", "name": "Bad", "fixed_fee_tiers": [{"max_price_cents": None, "fee_cents": 1},
                                                                         {"max_price_cents": 100, "fee_cents": 1}]}
    assert api.post("/api/v1/calculator/profit", json={"buy_price_cents": 1, "sale_price_cents": 2,
                                                       "fee_profile": unbounded_first}).status_code == 422


def test_import_costs_endpoint(api):
    body = {"item_value_usd_cents": 9_000, "shipping_usd_cents": 1_500, "rule_key": "courier_usmca", "usd_mxn_rate": "18.50"}
    r = api.post("/api/v1/calculator/import-costs", json=body)
    assert r.status_code == 200, r.text
    expected = estimate_import_costs(9_000, 1_500, "courier_usmca", Decimal("18.50"), fx_source="provided")
    assert r.json() == expected
    assert r.json()["duty_rate_bp"] == 1700
    assert r.json()["duties_mxn_cents"] == 33_023  # 105 USD * 18.50 * 17 % = 330.225 -> 330.23
    assert any("regla 3.7.35" in n for n in r.json()["notes"])


def test_import_costs_rate_resolution_and_errors(api):
    body = {"item_value_usd_cents": 4_000, "rule_key": "courier_usmca"}
    assert api.post("/api/v1/calculator/import-costs", json=body).status_code == 422  # no rate anywhere
    api.put("/api/v1/settings", json={"fx_usd_mxn_manual": "17.00"})
    r = api.post("/api/v1/calculator/import-costs", json=body)
    assert r.status_code == 200
    assert r.json()["usd_mxn_rate"] == "17.00" and r.json()["fx_source"].startswith("manual")
    assert r.json()["duties_mxn_cents"] == 0 and r.json()["customs_value_mxn_cents"] == 68_000
    assert api.post("/api/v1/calculator/import-costs", json={**body, "rule_key": "nope"}).status_code == 422
    assert api.post("/api/v1/calculator/import-costs", json={**body, "usd_mxn_rate": "abc"}).status_code == 422


def test_import_costs_formal_import_is_flagged_not_guessed(api):
    """Above the courier-regime limit duties are not estimated: null, never a guessed 0."""
    for rule_key in ("courier_usmca", "courier_non_fta"):
        body = {"item_value_usd_cents": 240_000, "shipping_usd_cents": 20_000, "rule_key": rule_key,
                "usd_mxn_rate": "18.00"}
        r = api.post("/api/v1/calculator/import-costs", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["formal_import_required"] is True
        assert out["duty_rate_bp"] is None and out["duties_mxn_cents"] is None
        assert out["customs_value_usd_cents"] == 260_000 and out["customs_value_mxn_cents"] == 4_680_000
        assert any(n.startswith("WARNING") and "formal import" in n for n in out["notes"])
        assert out == estimate_import_costs(240_000, 20_000, rule_key, Decimal("18.00"), fx_source="provided")
    # At the limit itself (USD 2,500.00) the courier regime still applies and duties are estimated.
    at_limit = api.post("/api/v1/calculator/import-costs", json={
        "item_value_usd_cents": 250_000, "rule_key": "courier_usmca", "usd_mxn_rate": "18.00"}).json()
    assert at_limit["formal_import_required"] is False and at_limit["duty_rate_bp"] == 1900
    assert at_limit["duties_mxn_cents"] == 855_000  # 2,500 USD * 18 * 19 % = 8,550.00
