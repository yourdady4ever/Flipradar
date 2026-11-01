"""Import duty estimates for buying abroad (e.g. eBay US) and reselling in Mexico.

PROVISIONAL: the rules below are placeholders supplied by the project lead and MUST be verified against
current Mexican customs rules (courier/"mensajeria y paqueteria" regime) before relying on them.  The
estimate is deterministic: ``duties = customs_value_usd * usd_mxn_rate * duty_rate`` (Decimal, half-up to
the centavo).  Customs value = item value + international shipping, in USD centavos.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

BP = Decimal(10000)
_AS_OF = "2026-10"
_ANAM = "https://www.anam.gob.mx/"
_PROVISIONAL = "PROVISIONAL - verify against current customs rules before relying on this estimate."


@dataclass(frozen=True)
class ImportRule:
    """One value bracket of an import regime: applies when customs value <= ``max_value_usd_cents``."""

    key: str
    name: str
    max_value_usd_cents: int | None  # None = no upper bound
    duty_rate_bp: int
    notes: tuple[str, ...]
    source_url: str | None
    as_of: str | None
    formal_import_required: bool = False


IMPORT_RULES: dict[str, tuple[ImportRule, ...]] = {
    "courier_usmca": (
        ImportRule(
            "courier_usmca", "Courier shipment from USA/Canada (USMCA/T-MEC)", 5000, 0,
            ("Customs value up to 50 USD: no duties under the USMCA express-shipment threshold.",),
            _ANAM, _AS_OF,
        ),
        ImportRule(
            "courier_usmca", "Courier shipment from USA/Canada (USMCA/T-MEC)", 11700, 1700,
            ("Customs value above 50 and up to 117 USD: 17 % global rate assumed (covers duty + IVA).",),
            _ANAM, _AS_OF,
        ),
        ImportRule(
            "courier_usmca", "Courier shipment from USA/Canada (USMCA/T-MEC)", None, 0,
            (
                "WARNING: customs value above 117 USD - a formal import (customs broker) is likely required; "
                "duties are NOT estimated here.",
            ),
            _ANAM, _AS_OF, formal_import_required=True,
        ),
    ),
    "courier_non_fta": (
        ImportRule(
            "courier_non_fta", "Courier shipment from a country without trade agreement", 250000, 3350,
            ("33.5 % global rate assumed for courier shipments up to 2,500 USD from non-FTA countries.",),
            _ANAM, _AS_OF,
        ),
        ImportRule(
            "courier_non_fta", "Courier shipment from a country without trade agreement", None, 0,
            (
                "WARNING: customs value above 2,500 USD - a formal import (customs broker) is required; "
                "duties are NOT estimated here.",
            ),
            _ANAM, _AS_OF, formal_import_required=True,
        ),
    ),
}


def rule_keys() -> list[str]:
    return sorted(IMPORT_RULES)


def select_rule(rule_key: str, customs_value_usd_cents: int) -> ImportRule:
    brackets = IMPORT_RULES.get(rule_key)
    if not brackets:
        raise KeyError(rule_key)
    for rule in brackets:
        if rule.max_value_usd_cents is None or customs_value_usd_cents <= rule.max_value_usd_cents:
            return rule
    return brackets[-1]  # pragma: no cover - the last bracket is unbounded


def _cents(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def estimate_import_costs(
    item_value_usd_cents: int,
    shipping_usd_cents: int,
    rule_key: str,
    usd_mxn_rate: Decimal,
    *,
    fx_source: str = "provided",
) -> dict[str, Any]:
    """Deterministic duty estimate.  Returns a dict with exactly the fields of ``schemas.ImportCostOut``.

    Raises KeyError for an unknown ``rule_key`` and ValueError for negative amounts / non-positive rate.
    """
    if item_value_usd_cents < 0 or shipping_usd_cents < 0:
        raise ValueError("amounts cannot be negative")
    rate = Decimal(usd_mxn_rate)
    if not rate.is_finite() or rate <= 0:
        raise ValueError("usd_mxn_rate must be positive")
    customs_usd = int(item_value_usd_cents) + int(shipping_usd_cents)
    rule = select_rule(rule_key, customs_usd)
    customs_mxn = _cents(Decimal(customs_usd) * rate)
    duties = _cents(Decimal(customs_usd) * rate * Decimal(rule.duty_rate_bp) / BP)
    notes = [
        *rule.notes,
        _PROVISIONAL,
        f"Rule '{rule.key}' as of {rule.as_of or 'unknown'}; reference: {rule.source_url or 'not recorded'}.",
        "Customs value = item value + international shipping, converted at the given USD/MXN rate.",
    ]
    return {
        "rule_key": rule.key,
        "rule_name": rule.name,
        "customs_value_usd_cents": customs_usd,
        "usd_mxn_rate": str(rate),
        "fx_source": fx_source,
        "duty_rate_bp": rule.duty_rate_bp,
        "duties_mxn_cents": duties,
        "customs_value_mxn_cents": customs_mxn,
        "notes": notes,
        "source_url": rule.source_url,
        "as_of": rule.as_of,
    }
