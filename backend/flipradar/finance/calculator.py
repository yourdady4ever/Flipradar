"""Deterministic resale profit calculator.

Everything here is pure (no I/O, no randomness, no AI).  All amounts are integer centavos, all rates
are integer basis points, intermediate maths uses ``Decimal`` and every *line item* is rounded half-up
to whole centavos - the same way a marketplace invoice rounds each charge.  The same inputs therefore
always produce the same outputs, which is what makes recommendations auditable.

Model (per unit sold)::

    acquisition      = buy_price + buy_shipping + import_costs + other_buy_costs
    commission       = sale_price * commission_rate
    fixed_fee        = tier(sale_price)
    payment_fee      = sale_price * payment_rate + payment_fixed
    iva_on_fees      = (commission + fixed_fee + payment_fee) * IVA      (only if the channel adds IVA)
    seller_shipping  = shipping the seller pays (mandatory free shipping above threshold, or override)
    tax_base         = sale_price / (1 + IVA)   if the sale price includes IVA, else sale_price
    isr_withheld     = tax_base * isr_withholding_rate                   (only if treat_withholdings_as_cost)
    iva_withheld     = tax_base * iva_withholding_rate                   (only if treat_withholdings_as_cost)
    selling_costs    = sum of the above selling lines + other_sale_costs
    net_proceeds     = sale_price - selling_costs
    net_profit       = net_proceeds - acquisition
    roi              = net_profit / acquisition
    margin           = net_profit / sale_price

This is an estimate tool, not tax advice: whether platform withholdings are a final cost depends on the
seller's tax regime (see SECURITY.md / docs/DATA_SOURCES.md for the sources of every default value).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

BP = Decimal(10000)


def _line(amount: Decimal) -> int:
    """Round a Decimal amount of centavos to an integer centavo, half-up."""
    return int(amount.to_integral_value(rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class FixedFeeTier:
    """Applies when sale price <= max_price_cents (None = no upper bound)."""

    max_price_cents: int | None
    fee_cents: int


@dataclass(frozen=True)
class FeeModel:
    key: str
    name: str
    commission_bp: int = 0
    fixed_fee_tiers: tuple[FixedFeeTier, ...] = ()
    payment_fee_bp: int = 0
    payment_fixed_cents: int = 0
    iva_on_fees: bool = False
    iva_rate_bp: int = 1600
    seller_shipping_cents: int = 0
    free_shipping_threshold_cents: int | None = None
    isr_withholding_bp: int = 0
    iva_withholding_bp: int = 0
    source_url: str | None = None
    as_of: str | None = None

    def __post_init__(self) -> None:
        for name in ("commission_bp", "payment_fee_bp", "iva_rate_bp", "isr_withholding_bp", "iva_withholding_bp"):
            value = getattr(self, name)
            if not 0 <= value <= 10000:
                raise ValueError(f"{name} must be within 0..10000 basis points, got {value}")
        for name in ("payment_fixed_cents", "seller_shipping_cents"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        tiers = list(self.fixed_fee_tiers)
        bounded = [t.max_price_cents for t in tiers if t.max_price_cents is not None]
        if bounded != sorted(bounded):
            raise ValueError("fixed_fee_tiers must be sorted by max_price_cents ascending")
        if any(t.fee_cents < 0 for t in tiers):
            raise ValueError("fixed fee cannot be negative")

    def fixed_fee_for(self, sale_price_cents: int) -> int:
        for tier in self.fixed_fee_tiers:
            if tier.max_price_cents is None or sale_price_cents <= tier.max_price_cents:
                return tier.fee_cents
        return 0

    @classmethod
    def from_profile(cls, profile: Any) -> FeeModel:
        """Build from a :class:`flipradar.models.FeeProfile` row (or any object with the same attrs)."""
        tiers = tuple(
            FixedFeeTier(
                max_price_cents=(int(t["max_price_cents"]) if t.get("max_price_cents") is not None else None),
                fee_cents=int(t["fee_cents"]),
            )
            for t in (profile.fixed_fee_tiers_json or [])
        )
        return cls(
            key=profile.key,
            name=profile.name,
            commission_bp=int(profile.commission_bp or 0),
            fixed_fee_tiers=tiers,
            payment_fee_bp=int(profile.payment_fee_bp or 0),
            payment_fixed_cents=int(profile.payment_fixed_cents or 0),
            iva_on_fees=bool(profile.iva_on_fees),
            iva_rate_bp=int(profile.iva_rate_bp if profile.iva_rate_bp is not None else 1600),
            seller_shipping_cents=int(profile.seller_shipping_cents or 0),
            free_shipping_threshold_cents=profile.free_shipping_threshold_cents,
            isr_withholding_bp=int(profile.isr_withholding_bp or 0),
            iva_withholding_bp=int(profile.iva_withholding_bp or 0),
            source_url=profile.source_url,
            as_of=profile.as_of,
        )


@dataclass(frozen=True)
class ProfitInputs:
    buy_price_cents: int
    sale_price_cents: int
    fee: FeeModel
    buy_shipping_cents: int = 0
    import_costs_cents: int = 0
    other_buy_costs_cents: int = 0
    other_sale_costs_cents: int = 0
    # None -> use the fee model rule (free-shipping threshold); an int forces the seller shipping cost.
    seller_shipping_override_cents: int | None = None
    sale_price_includes_iva: bool = True
    treat_withholdings_as_cost: bool = True

    def __post_init__(self) -> None:
        for name in (
            "buy_price_cents",
            "sale_price_cents",
            "buy_shipping_cents",
            "import_costs_cents",
            "other_buy_costs_cents",
            "other_sale_costs_cents",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.seller_shipping_override_cents is not None and self.seller_shipping_override_cents < 0:
            raise ValueError("seller_shipping_override_cents cannot be negative")


@dataclass
class CostLine:
    key: str
    label: str
    amount_cents: int
    formula: str


@dataclass
class ProfitResult:
    sale_price_cents: int
    acquisition_lines: list[CostLine]
    selling_lines: list[CostLine]
    total_acquisition_cents: int
    total_selling_costs_cents: int
    net_proceeds_cents: int
    net_profit_cents: int
    roi_bp: int | None  # None when acquisition cost is zero
    margin_bp: int | None  # None when sale price is zero
    fee_profile_key: str
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pct(bp: int) -> str:
    return f"{Decimal(bp) / 100:.2f}%"


def _money(cents: int) -> str:
    return f"${Decimal(cents) / 100:,.2f}"


def calculate_profit(inp: ProfitInputs) -> ProfitResult:
    fee = inp.fee
    sale = Decimal(inp.sale_price_cents)
    assumptions: list[str] = []

    acquisition = [
        CostLine("buy_price", "Purchase price", inp.buy_price_cents, "listing price"),
        CostLine("buy_shipping", "Shipping to you", inp.buy_shipping_cents, "as provided"),
        CostLine("import_costs", "Import duties / customs", inp.import_costs_cents, "as provided"),
        CostLine("other_buy_costs", "Other acquisition costs", inp.other_buy_costs_cents, "as provided"),
    ]
    total_acq = sum(line.amount_cents for line in acquisition)

    commission = _line(sale * fee.commission_bp / BP)
    fixed_fee = fee.fixed_fee_for(inp.sale_price_cents)
    payment_fee = _line(sale * fee.payment_fee_bp / BP) + (fee.payment_fixed_cents if inp.sale_price_cents > 0 else 0)
    iva_fees = 0
    if fee.iva_on_fees:
        iva_fees = _line(Decimal(commission + fixed_fee + payment_fee) * fee.iva_rate_bp / BP)

    if inp.seller_shipping_override_cents is not None:
        shipping = inp.seller_shipping_override_cents
        shipping_formula = "override"
    elif fee.free_shipping_threshold_cents is not None and inp.sale_price_cents >= fee.free_shipping_threshold_cents:
        shipping = fee.seller_shipping_cents
        shipping_formula = f"free shipping mandatory at >= {_money(fee.free_shipping_threshold_cents)}"
    elif fee.free_shipping_threshold_cents is None:
        shipping = fee.seller_shipping_cents
        shipping_formula = "profile default"
    else:
        shipping = 0
        shipping_formula = f"buyer pays shipping below {_money(fee.free_shipping_threshold_cents)}"

    if inp.sale_price_includes_iva:
        tax_base = sale * BP / (BP + fee.iva_rate_bp)
        base_formula = f"sale / (1 + {_pct(fee.iva_rate_bp)})"
    else:
        tax_base = sale
        base_formula = "sale"
    isr = iva_w = 0
    if inp.treat_withholdings_as_cost:
        isr = _line(tax_base * fee.isr_withholding_bp / BP)
        iva_w = _line(tax_base * fee.iva_withholding_bp / BP)
    elif fee.isr_withholding_bp or fee.iva_withholding_bp:
        assumptions.append("Platform tax withholdings excluded (treated as creditable, not as cost).")

    selling = [
        CostLine("commission", "Marketplace commission", commission, f"sale x {_pct(fee.commission_bp)}"),
        CostLine("fixed_fee", "Fixed fee per unit", fixed_fee, "tier by sale price"),
        CostLine(
            "payment_fee",
            "Payment processing",
            payment_fee,
            f"sale x {_pct(fee.payment_fee_bp)} + {_money(fee.payment_fixed_cents)}",
        ),
        CostLine("iva_on_fees", "IVA on fees", iva_fees, f"fees x {_pct(fee.iva_rate_bp)}" if fee.iva_on_fees else "n/a"),
        CostLine("seller_shipping", "Shipping paid by seller", shipping, shipping_formula),
        CostLine("other_sale_costs", "Packaging / other selling costs", inp.other_sale_costs_cents, "as provided"),
        CostLine("isr_withheld", "ISR withheld by platform", isr, f"{base_formula} x {_pct(fee.isr_withholding_bp)}"),
        CostLine("iva_withheld", "IVA withheld by platform", iva_w, f"{base_formula} x {_pct(fee.iva_withholding_bp)}"),
    ]
    total_selling = sum(line.amount_cents for line in selling)
    net_proceeds = inp.sale_price_cents - total_selling
    profit = net_proceeds - total_acq
    roi_bp = _line(Decimal(profit) * BP / Decimal(total_acq)) if total_acq > 0 else None
    margin_bp = _line(Decimal(profit) * BP / sale) if inp.sale_price_cents > 0 else None
    if fee.as_of:
        assumptions.append(f"Fee profile '{fee.name}' values as of {fee.as_of}; verify current rates.")

    return ProfitResult(
        sale_price_cents=inp.sale_price_cents,
        acquisition_lines=acquisition,
        selling_lines=selling,
        total_acquisition_cents=total_acq,
        total_selling_costs_cents=total_selling,
        net_proceeds_cents=net_proceeds,
        net_profit_cents=profit,
        roi_bp=roi_bp,
        margin_bp=margin_bp,
        fee_profile_key=fee.key,
        assumptions=assumptions,
    )


def _with_sale(inp: ProfitInputs, sale_cents: int) -> ProfitInputs:
    return ProfitInputs(**{**inp.__dict__, "sale_price_cents": sale_cents})


def break_even_sale_price(inp: ProfitInputs, *, upper_bound_cents: int = 10_000_000_000) -> int | None:
    """Smallest sale price (centavos) whose net profit is >= 0, or None if none below ``upper_bound``.

    Net proceeds are piecewise-linear in the sale price (fee tiers and the free-shipping threshold create
    breakpoints), so we solve each segment analytically and verify the candidate with the real calculator.
    """
    fee = inp.fee
    breakpoints = sorted(
        {0}
        | {t.max_price_cents + 1 for t in fee.fixed_fee_tiers if t.max_price_cents is not None}
        | ({fee.free_shipping_threshold_cents} if fee.free_shipping_threshold_cents is not None else set())
    )
    segments = [(lo, (breakpoints[i + 1] - 1) if i + 1 < len(breakpoints) else upper_bound_cents)
                for i, lo in enumerate(breakpoints)]
    best: int | None = None
    for lo, hi in segments:
        if lo > upper_bound_cents:
            break
        # Evaluate profit at two points to get the slope/intercept of this linear segment.
        p_lo = calculate_profit(_with_sale(inp, lo)).net_profit_cents
        if p_lo >= 0:
            candidate = lo
        else:
            probe = min(hi, lo + 1_000_000)
            if probe == lo:
                continue
            p_probe = calculate_profit(_with_sale(inp, probe)).net_profit_cents
            slope = Decimal(p_probe - p_lo) / Decimal(probe - lo)
            if slope <= 0:
                continue
            candidate = lo + int((Decimal(-p_lo) / slope).to_integral_value(rounding=ROUND_CEILING))
            if candidate > hi:
                continue
            # Line-item rounding can shift the root by a few centavos; walk to the exact boundary.
            while candidate > lo and calculate_profit(_with_sale(inp, candidate - 1)).net_profit_cents >= 0:
                candidate -= 1
            steps = 0
            while calculate_profit(_with_sale(inp, candidate)).net_profit_cents < 0 and steps < 1000:
                candidate += 1
                steps += 1
            if candidate > hi or calculate_profit(_with_sale(inp, candidate)).net_profit_cents < 0:
                continue
        if best is None or candidate < best:
            best = candidate
        break  # segments are ascending: the first segment with a root has the smallest root
    return best


def max_buy_price_for_roi(inp: ProfitInputs, target_roi_bp: int) -> int | None:
    """Highest purchase price (centavos) that still yields ``target_roi_bp`` at the given sale price.

    Selling costs do not depend on the purchase price, so::

        profit / acquisition >= r  <=>  acquisition <= net_proceeds / (1 + r)
    """
    if target_roi_bp < 0:
        raise ValueError("target ROI must be >= 0")
    result = calculate_profit(inp)
    max_acq = Decimal(result.net_proceeds_cents) * BP / (BP + target_roi_bp)
    other = inp.buy_shipping_cents + inp.import_costs_cents + inp.other_buy_costs_cents
    value = int(max_acq.to_integral_value(rounding=ROUND_FLOOR)) - other
    return value if value > 0 else None
