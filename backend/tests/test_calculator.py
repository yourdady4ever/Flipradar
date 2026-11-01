from decimal import Decimal

import pytest

from flipradar.finance.calculator import (
    FeeModel,
    FixedFeeTier,
    ProfitInputs,
    break_even_sale_price,
    calculate_profit,
    max_buy_price_for_roi,
)

ML_LIKE = FeeModel(
    key="test_ml",
    name="Test ML-like",
    commission_bp=1500,
    fixed_fee_tiers=(FixedFeeTier(29899, 2500),),
    iva_on_fees=False,
    seller_shipping_cents=12000,
    free_shipping_threshold_cents=29900,
    isr_withholding_bp=250,
    iva_withholding_bp=800,
)
LOCAL = FeeModel(key="local", name="Local cash")


def test_local_sale_is_simple_difference():
    r = calculate_profit(ProfitInputs(buy_price_cents=100000, sale_price_cents=150000, fee=LOCAL))
    assert r.net_profit_cents == 50000
    assert r.roi_bp == 5000
    assert r.margin_bp == 3333
    assert r.total_selling_costs_cents == 0


def test_ml_like_breakdown_is_exact():
    r = calculate_profit(ProfitInputs(buy_price_cents=500000, sale_price_cents=800000, fee=ML_LIKE))
    lines = {line.key: line.amount_cents for line in r.selling_lines}
    assert lines["commission"] == 120000  # 15% of 8000.00
    assert lines["fixed_fee"] == 0  # above tier
    assert lines["seller_shipping"] == 12000  # free shipping mandatory above 299
    # tax base = 8000 / 1.16 = 6896.5517... -> ISR 2.5% = 172.41, IVA 8% = 551.72
    assert lines["isr_withheld"] == 17241
    assert lines["iva_withheld"] == 55172
    assert r.total_selling_costs_cents == 120000 + 12000 + 17241 + 55172
    assert r.net_profit_cents == 800000 - r.total_selling_costs_cents - 500000


def test_low_price_tier_and_buyer_paid_shipping():
    r = calculate_profit(ProfitInputs(buy_price_cents=5000, sale_price_cents=20000, fee=ML_LIKE))
    lines = {line.key: line.amount_cents for line in r.selling_lines}
    assert lines["fixed_fee"] == 2500
    assert lines["seller_shipping"] == 0


def test_iva_on_fees_and_payment_fixed():
    fee = FeeModel(key="mp", name="MP link", payment_fee_bp=349, payment_fixed_cents=400, iva_on_fees=True)
    r = calculate_profit(ProfitInputs(buy_price_cents=0, sale_price_cents=100000, fee=fee))
    lines = {line.key: line.amount_cents for line in r.selling_lines}
    assert lines["payment_fee"] == 3490 + 400
    assert lines["iva_on_fees"] == 622  # 38.90 * 16% = 6.224 -> 6.22
    assert r.roi_bp is None  # zero acquisition


def test_withholdings_can_be_excluded():
    a = calculate_profit(ProfitInputs(buy_price_cents=500000, sale_price_cents=800000, fee=ML_LIKE))
    b = calculate_profit(
        ProfitInputs(buy_price_cents=500000, sale_price_cents=800000, fee=ML_LIKE, treat_withholdings_as_cost=False)
    )
    assert b.net_profit_cents - a.net_profit_cents == 17241 + 55172
    assert b.assumptions


def test_break_even_is_minimal_and_exact():
    inp = ProfitInputs(buy_price_cents=500000, sale_price_cents=0, fee=ML_LIKE, buy_shipping_cents=15000)
    be = break_even_sale_price(inp)
    assert be is not None
    from flipradar.finance.calculator import _with_sale

    assert calculate_profit(_with_sale(inp, be)).net_profit_cents >= 0
    assert calculate_profit(_with_sale(inp, be - 1)).net_profit_cents < 0


def test_break_even_low_price_segment():
    inp = ProfitInputs(buy_price_cents=1000, sale_price_cents=0, fee=ML_LIKE)
    be = break_even_sale_price(inp)
    from flipradar.finance.calculator import _with_sale

    assert be is not None and be < 29900
    assert calculate_profit(_with_sale(inp, be)).net_profit_cents >= 0
    assert calculate_profit(_with_sale(inp, be - 1)).net_profit_cents < 0


def test_max_buy_price_hits_target_roi():
    inp = ProfitInputs(buy_price_cents=1, sale_price_cents=800000, fee=ML_LIKE, buy_shipping_cents=10000)
    mb = max_buy_price_for_roi(inp, 2500)
    assert mb is not None
    r = calculate_profit(ProfitInputs(buy_price_cents=mb, sale_price_cents=800000, fee=ML_LIKE, buy_shipping_cents=10000))
    assert r.roi_bp >= 2500
    r2 = calculate_profit(
        ProfitInputs(buy_price_cents=mb + 100, sale_price_cents=800000, fee=ML_LIKE, buy_shipping_cents=10000)
    )
    assert r2.roi_bp < 2500


def test_deterministic_repeatability():
    inp = ProfitInputs(buy_price_cents=123457, sale_price_cents=234567, fee=ML_LIKE)
    assert calculate_profit(inp).to_dict() == calculate_profit(inp).to_dict()


@pytest.mark.parametrize("bad", [-1])
def test_rejects_negative(bad):
    with pytest.raises(ValueError):
        ProfitInputs(buy_price_cents=bad, sale_price_cents=1, fee=LOCAL)


def test_rejects_bad_rates():
    with pytest.raises(ValueError):
        FeeModel(key="x", name="x", commission_bp=20000)
    with pytest.raises(ValueError):
        FeeModel(key="x", name="x", fixed_fee_tiers=(FixedFeeTier(500, 1), FixedFeeTier(100, 1)))


def test_money_helpers():
    from flipradar.money import format_mxn, pesos_to_cents

    assert pesos_to_cents("1,234.565") == 123457
    assert pesos_to_cents(Decimal("0.005")) == 1
    assert format_mxn(-123456) == "-$1,234.56 MXN"
