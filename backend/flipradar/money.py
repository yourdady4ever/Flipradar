"""Deterministic money helpers.

Rules used across the code base:

* Amounts are stored as integer **centavos** (``*_cents`` columns) - never floats.
* Percentages are stored as integer **basis points** (1 bp = 0.01 %, so 16 % = 1600 bp).
* Calculations use :class:`decimal.Decimal` and round half-up to whole centavos only when a value is
  persisted or displayed.  This keeps every financial figure reproducible bit-for-bit.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
BP_DIVISOR = Decimal(10000)


def to_decimal(value: object) -> Decimal:
    """Convert int/str/Decimal (or float, via ``str``) into a Decimal.  Raises ValueError on junk."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int subclass - refuse it explicitly
        raise ValueError("boolean is not a monetary value")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("$", "").replace("MXN", "").replace("mxn", "").strip()
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:  # pragma: no cover - message path
            raise ValueError(f"not a number: {value!r}") from exc
    raise ValueError(f"unsupported monetary type: {type(value).__name__}")


def quantize(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def pesos_to_cents(amount: object) -> int:
    return int((quantize(to_decimal(amount)) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def cents_to_pesos(cents: int | None) -> Decimal | None:
    if cents is None:
        return None
    return (Decimal(cents) / 100).quantize(CENT)


def cents_to_str(cents: int | None) -> str | None:
    value = cents_to_pesos(cents)
    return None if value is None else f"{value:.2f}"


def bp_to_rate(bp: int | Decimal) -> Decimal:
    """Basis points -> fraction (1600 -> Decimal('0.16'))."""
    return Decimal(bp) / BP_DIVISOR


def rate_to_bp(rate: Decimal) -> int:
    return int((rate * BP_DIVISOR).to_integral_value(rounding=ROUND_HALF_UP))


def format_mxn(cents: int | None) -> str:
    if cents is None:
        return "n/a"
    value = cents_to_pesos(cents)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f} MXN"


def format_pct_bp(bp: int | None) -> str:
    if bp is None:
        return "n/a"
    return f"{Decimal(bp) / 100:.2f}%"
