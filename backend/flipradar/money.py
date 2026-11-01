"""Deterministic money helpers.

Rules used across the code base:

* Amounts are stored as integer **centavos** (``*_cents`` columns) - never floats.
* Percentages are stored as integer **basis points** (1 bp = 0.01 %, so 16 % = 1600 bp).
* Calculations use :class:`decimal.Decimal` and round half-up to whole centavos only when a value is
  persisted or displayed.  This keeps every financial figure reproducible bit-for-bit.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
BP_DIVISOR = Decimal(10000)
# Upper bound for any amount accepted from a client or a file (10,000,000,000.00 MXN).  Keeps every
# stored value - and products such as quantity x unit price - far inside SQLite's signed 64-bit INTEGER.
MAX_CENTS = 10**12

_CURRENCY_MARKS = re.compile(r"\$|mxn", re.IGNORECASE)
_AMOUNT_CHARS = re.compile(r"[0-9.,]+")


class AmbiguousAmountError(ValueError):
    """The text could mean two different amounts (e.g. ``1,234`` in a decimal-comma file)."""


def _grouped(text: str, sep: str) -> bool:
    """``1,234,567`` style thousands grouping (first group 1-3 digits, then groups of exactly 3)."""
    groups = text.split(sep)
    return (len(groups) >= 2 and 1 <= len(groups[0]) <= 3 and groups[0].isdigit()
            and all(len(g) == 3 and g.isdigit() for g in groups[1:]))


def parse_amount(text: str, *, decimal_comma: bool = False) -> Decimal:
    """Parse a human-written amount with an explicit grammar; never strips separators blindly.

    ``decimal_comma=False`` (Mexican/English convention, the documented CSV format): ``12999.00``,
    ``$12,999``, ``1,234.50``.  A ``,`` is only accepted as a thousands separator; a value such as
    ``12999,00`` or ``1.234,50`` is rejected as ambiguous.

    ``decimal_comma=True`` (``;``-delimited spreadsheets written by decimal-comma locales): ``1.234,50``,
    ``12999,00`` and ``1,5`` are read with a decimal comma, and unambiguous ``.``-decimal values such as
    ``12999.00`` or ``1,234.50`` still work.  ``1,234`` and ``1.234`` could mean either 1234 or 1.234, so
    they are rejected.

    ``$``/``MXN`` marks and a leading sign are allowed.  Raises :class:`ValueError` (or its subclass
    :class:`AmbiguousAmountError`).
    """
    raw = _CURRENCY_MARKS.sub("", text).strip()
    sign = ""
    if raw[:1] in ("-", "+"):
        sign, raw = raw[0], raw[1:].strip()
    if not raw or not _AMOUNT_CHARS.fullmatch(raw) or not any(ch.isdigit() for ch in raw):
        raise ValueError(f"not a number: {text[:30]!r}")
    ambiguous = AmbiguousAmountError(
        f"ambiguous decimal separator in {text[:30]!r}; write amounts like 1234.50"
        + ("" if not decimal_comma else " or 1234,50")
    )
    has_comma, has_dot = "," in raw, "." in raw
    if has_comma and has_dot:
        decimal_sep = "," if raw.rfind(",") > raw.rfind(".") else "."
        group_sep = "." if decimal_sep == "," else ","
        int_part, _, frac = raw.rpartition(decimal_sep)
        if decimal_sep == "," and not decimal_comma:
            raise ambiguous
        if not frac.isdigit() or decimal_sep in int_part or not _grouped(int_part, group_sep):
            raise ValueError(f"not a valid amount: {text[:30]!r}")
        number = int_part.replace(group_sep, "") + "." + frac
    elif has_comma or has_dot:
        sep = "," if has_comma else "."
        parts = raw.split(sep)
        single = len(parts) == 2 and all(p.isdigit() for p in parts)
        # A single separator followed by exactly three digits after a 1-3 digit head reads as grouping too.
        could_be_grouping = _grouped(raw, sep)
        if sep == "." and not decimal_comma:
            if not single:
                raise ValueError(f"not a valid amount: {text[:30]!r}")
            number = raw  # '.' is the decimal separator in the documented format
        elif sep == "," and not decimal_comma:
            if could_be_grouping:
                number = raw.replace(",", "")
            elif single:
                raise ambiguous  # "12999,00" / "1,5": a decimal comma in a comma-delimited file
            else:
                raise ValueError(f"not a valid amount: {text[:30]!r}")
        else:  # decimal-comma file, a single kind of separator
            if could_be_grouping and len(parts) == 2:
                raise ambiguous  # "1,234" / "1.234"
            if could_be_grouping:
                number = raw.replace(sep, "")  # "1.234.567" (a decimal separator cannot repeat)
            elif single:
                number = raw.replace(",", ".")
            else:
                raise ValueError(f"not a valid amount: {text[:30]!r}")
    else:
        number = raw
    try:
        return Decimal(sign + number)
    except InvalidOperation as exc:  # pragma: no cover - the grammar above only lets digits through
        raise ValueError(f"not a number: {text[:30]!r}") from exc


def to_decimal(value: object) -> Decimal:
    """Convert int/str/Decimal (or float, via ``str``) into a Decimal.  Raises ValueError on junk.

    Strings are parsed with :func:`parse_amount` (``,`` only as a thousands separator)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int subclass - refuse it explicitly
        raise ValueError("boolean is not a monetary value")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, str):
        return parse_amount(value)
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
