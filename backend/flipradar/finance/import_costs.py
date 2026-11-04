"""Import duty estimates for buying abroad (e.g. eBay US) and reselling in Mexico.

Rules follow the Mexican courier/parcel simplified regime ("mensajeria y paqueteria", RGCE regla
3.7.35) as researched on 2026-11-01 (see docs/DATA_SOURCES.md section 3).  The estimate is deterministic: ``duties = customs_value_usd * usd_mxn_rate * duty_rate`` (Decimal, half-up to
the centavo).  Customs value = item value + international shipping, in USD centavos.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

BP = Decimal(10000)
_AS_OF = "2025-08-15"
_RGCE = "https://www.sat.gob.mx/minisitio/NormatividadRMFyRGCE/documentos2025/rgce/rgce/4taRMRGCE_2025.pdf"
_REGLA = "http://www.apta.com.mx/aptace/reglasce/regla.php?regla=3.7.35"
_ESTIMATE = (
    "Estimate under the courier/parcel simplified regime (RGCE regla 3.7.35, rates in force since "
    "2025-08-15, researched 2026-11-01). The global rate replaces IGI + IVA; a fixed customs processing "
    "fee (DTA) may still apply. Carriers may add brokerage fees. Not legal advice."
)
_COURIER_CAP = (
    "Upper limit of the courier regime: USD 2,500 per consignee per the FedEx/AMCP table (some sources cite "
    "USD 1,000 for consignees not registered in the Padron de Importadores) - low confidence."
)


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


_USMCA = "Courier shipment from USA/Canada (USMCA/T-MEC origin)"
_NON_FTA = "Courier shipment from a country without a trade agreement with Mexico (e.g. China)"

IMPORT_RULES: dict[str, tuple[ImportRule, ...]] = {
    "courier_usmca": (
        ImportRule(
            "courier_usmca", _USMCA, 5000, 0,
            ("Customs value up to USD 50: exempt from IGI and IVA (DTA may still apply).",),
            _RGCE, _AS_OF,
        ),
        ImportRule(
            "courier_usmca", _USMCA, 11700, 1700,
            ("Customs value above USD 50 and up to USD 117: 17 % global rate.",),
            _REGLA, _AS_OF,
        ),
        ImportRule(
            "courier_usmca", _USMCA, 250000, 1900,
            (
                "Customs value above USD 117: 19 % global rate (shipments with air waybill/BL, goods not "
                "subject to non-tariff restrictions).",
                _COURIER_CAP,
            ),
            _RGCE, _AS_OF,
        ),
        ImportRule(
            "courier_usmca", _USMCA, None, 0,
            (
                "WARNING: customs value above the courier-regime limit - a formal import (customs broker, "
                "pedimento) is required; duties are NOT estimated here.",
                _COURIER_CAP,
            ),
            _RGCE, _AS_OF, formal_import_required=True,
        ),
    ),
    "courier_non_fta": (
        ImportRule(
            "courier_non_fta", _NON_FTA, 250000, 3350,
            (
                "33.5 % global rate for courier shipments from countries without a trade agreement (was 19 % "
                "from 2025-01-01 to 2025-08-14). Origins with other trade agreements (EU, Japan...) may differ.",
                _COURIER_CAP,
            ),
            _RGCE, _AS_OF,
        ),
        ImportRule(
            "courier_non_fta", _NON_FTA, None, 0,
            (
                "WARNING: customs value above the courier-regime limit - a formal import (customs broker) is "
                "required; 2026 LIGIE tariffs of 5-50 % may apply to non-FTA origins; duties are NOT estimated.",
            ),
            _RGCE, _AS_OF, formal_import_required=True,
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

    When the customs value is above the courier-regime limit (``formal_import_required``) duties are NOT
    estimated: ``duty_rate_bp`` and ``duties_mxn_cents`` are None (never a guessed 0), and the notes say a
    formal import through a customs broker is required.

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
    formal = bool(rule.formal_import_required)
    duty_rate_bp: int | None = None if formal else rule.duty_rate_bp
    duties: int | None = None if formal else _cents(Decimal(customs_usd) * rate * Decimal(rule.duty_rate_bp) / BP)
    notes = [
        *rule.notes,
        _ESTIMATE,
        f"Rule '{rule.key}' as of {rule.as_of or 'unknown'}; reference: {rule.source_url or 'not recorded'}.",
        "Customs value = item value + international shipping, converted at the given USD/MXN rate.",
    ]
    return {
        "rule_key": rule.key,
        "rule_name": rule.name,
        "customs_value_usd_cents": customs_usd,
        "usd_mxn_rate": str(rate),
        "fx_source": fx_source,
        "duty_rate_bp": duty_rate_bp,
        "duties_mxn_cents": duties,
        "customs_value_mxn_cents": customs_mxn,
        "formal_import_required": formal,
        "notes": notes,
        "source_url": rule.source_url,
        "as_of": rule.as_of,
    }
