"""Built-in fee profiles (selling channels) and their seeding.

The numbers below come from the 2026-11-01 research documented in docs/DATA_SOURCES.md.  Most
were read from secondary quotes of official pages, so each profile says how confident the value is.  Each profile
records ``source_url`` (when known) and ``as_of`` so the dashboard can show where a number came from.
Every value is user-editable through ``PUT /api/v1/fee-profiles/{key}``; seeding never overwrites a
profile that already exists, so user edits survive restarts and upgrades.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FeeProfile

_ML_SOURCE = "https://www.mercadolibre.com.mx/ayuda/costos-de-vender-un-producto_870"
_ML_AS_OF = "2026-10-28"
_VERIFY = (
    "Researched 2026-11-01 from secondary quotes of the official pages (the sandbox could not open them "
    "directly) - confirm in your seller account before relying on it; with a Mercado Libre token the "
    "exact per-category commission is available from /sites/MLM/listing_prices."
)
_ML_WITHHOLDING_NEW = (
    "Withholdings ISR 2.5 % (LIF 2026 Art. 25 fr. VI, fiscal year 2026 only; statutory 1 % otherwise) + "
    "IVA 8 % of the pre-IVA price apply to NEW items sold by a persona fisica WITH RFC. Without RFC: "
    "ISR 20 % + IVA 16 %."
)
_ML_WITHHOLDING_USED = (
    "Per Mercado Libre's seller-center retention page, USED items sold by sellers WITH RFC have 0 % "
    "withholding (without RFC: ISR 20 % + IVA 16 %). Your own tax obligations still apply."
)


def _tiers(values_pesos: list[float | None], bounds_cents: list[int | None]) -> list[dict[str, Any]]:
    return [
        {"max_price_cents": bound, "fee_cents": round(value * 100)}
        for value, bound in zip(values_pesos, bounds_cents, strict=True)
        if value is not None
    ]


# Mercado Libre price bands: < $99 | $99-198.99 | $199-298.99 | $299-498.99 | $499-998.99 | >= $999.
_ML_BANDS = [9899, 19899, 29899, 49899, 99899, None]
# Per-unit "costo de los envios" charged to the seller (replaced the legacy fixed fee in April 2026).
_ML_NEW_SMALL = _tiers([25, 32, 35, 52.40, 65.50, 65.50], _ML_BANDS)  # new, <= 0.3 kg, green rep (ayuda 40538)
_ML_NEW_1_2KG = _tiers([35, 40, 41, 67.60, 84.50, 84.50], _ML_BANDS)  # new, 1-2 kg, green rep (ayuda 40538)
# Used items: free shipping is optional; the >= $299 value assumes you DO offer free shipping (ayuda 49978).
_ML_USED_BANDS = [9899, 19899, 29899, None]
_ML_USED_SMALL = _tiers([50, 64, 70, 131], _ML_USED_BANDS)  # used, <= 0.3 kg
_ML_USED_1_2KG = _tiers([70, 80, 82, 169], _ML_USED_BANDS)  # used, 1-2 kg


def _ml(key: str, name: str, commission_bp: int, tiers: list[dict[str, Any]], *, used: bool, extra: str) -> dict:
    return {
        "key": key,
        "name": name,
        "channel": "mercadolibre",
        "commission_bp": commission_bp,
        "fixed_fee_tiers_json": tiers,
        "payment_fee_bp": 0,
        "payment_fixed_cents": 0,
        "iva_on_fees": False,
        "iva_rate_bp": 1600,
        "seller_shipping_cents": 0,
        "free_shipping_threshold_cents": None,
        "isr_withholding_bp": 0 if used else 250,
        "iva_withholding_bp": 0 if used else 800,
        "source_url": _ML_SOURCE,
        "as_of": _ML_AS_OF,
        "notes": (
            f"{extra} Commission is category dependent (Clasica ~8-17 %, Premium ~11-21.5 %); "
            "the fixed-fee tiers model the per-unit shipping cost charged to the seller (free shipping is "
            f"mandatory for new items >= $299). {_ML_WITHHOLDING_USED if used else _ML_WITHHOLDING_NEW} {_VERIFY}"
        ),
    }


def _flat(key: str, name: str, channel: str, notes: str, **values: Any) -> dict:
    base = {
        "key": key,
        "name": name,
        "channel": channel,
        "commission_bp": 0,
        "fixed_fee_tiers_json": [],
        "payment_fee_bp": 0,
        "payment_fixed_cents": 0,
        "iva_on_fees": False,
        "iva_rate_bp": 1600,
        "seller_shipping_cents": 0,
        "free_shipping_threshold_cents": None,
        "isr_withholding_bp": 0,
        "iva_withholding_bp": 0,
        "source_url": None,
        "as_of": None,
        "notes": notes,
    }
    base.update(values)
    return base


BUILTIN_FEE_PROFILES: list[dict[str, Any]] = [
    _ml("ml_clasica", "Mercado Libre Clasica - new, up to 0.3 kg", 1500, _ML_NEW_SMALL, used=False,
        extra="Default for new/refurbished units (phones, accessories, small electronics)."),
    _ml("ml_clasica_1_2kg", "Mercado Libre Clasica - new, 1-2 kg", 1500, _ML_NEW_1_2KG, used=False,
        extra="For heavier new items (handheld consoles, small appliances)."),
    _ml("ml_premium", "Mercado Libre Premium - new, up to 0.3 kg", 1950, _ML_NEW_SMALL, used=False,
        extra="Premium listings include interest-free installments (MSI)."),
    _ml("ml_clasica_used", "Mercado Libre Clasica - used, up to 0.3 kg", 1500, _ML_USED_SMALL, used=True,
        extra="Default for used / unknown-condition units."),
    _ml("ml_clasica_used_1_2kg", "Mercado Libre Clasica - used, 1-2 kg", 1500, _ML_USED_1_2KG, used=True,
        extra="For heavier used items."),
    _flat(
        "amazon_mx_standard", "Amazon Mexico - 15 % referral (video games, home, media)", "amazon_mx",
        "Referral fee is charged on the total sale price (item + shipping + gift wrap), IVA included, minimum "
        "$8 MXN per item (not modelled - irrelevant above ~$54). Electronics are 10 % (see amazon_mx_electronics); "
        "range 8-20 % by category. Professional plan $75-600 MXN/month is NOT included. Platform withholdings "
        "for persona fisica with RFC as for Mercado Libre new items. Values as published ~2026-02; verify at the "
        "source URL.",
        commission_bp=1500, isr_withholding_bp=250, iva_withholding_bp=800,
        source_url="https://vender.amazon.com.mx/precios", as_of="2026-02",
    ),
    _flat(
        "amazon_mx_electronics", "Amazon Mexico - 10 % referral (electronics)", "amazon_mx",
        "Electronics referral fee 10 % (see amazon_mx_standard for the caveats).",
        commission_bp=1000, isr_withholding_bp=250, iva_withholding_bp=800,
        source_url="https://vender.amazon.com.mx/precios", as_of="2026-02",
    ),
    _flat(
        "facebook_local", "Facebook Marketplace - local pickup (cash)", "facebook",
        "No platform fee for in-person pickup paid in cash (Marketplace checkout/shipping fees were US-only; "
        "UNVERIFIED for Mexico in 2026). No shipping, no withholding. Your own tax obligations still apply.",
    ),
    _flat(
        "mercadopago_link", "Mercado Pago - payment link (placeholder)", "mercadopago",
        "PLACEHOLDER: historical structure 3.49 % + $4 MXN + IVA; the exact 2026 rate depends on how fast you "
        "want the money released and could not be verified. Edit with the rate shown in your Mercado Pago "
        "account before using.",
        payment_fee_bp=349, payment_fixed_cents=400, iva_on_fees=True,
        source_url="https://www.mercadopago.com.mx/costs-section", as_of=None,
    ),
    _flat("local_cash", "Local sale - cash, no fees", "local", "Direct cash sale with no platform involved."),
]

BUILTIN_KEYS = frozenset(p["key"] for p in BUILTIN_FEE_PROFILES)


def seed_fee_profiles(session: Session) -> int:
    """Insert built-in profiles that do not exist yet.  Never touches existing rows (user edits win).

    Returns the number of profiles inserted (flushed, not committed).
    """
    existing = set(session.scalars(select(FeeProfile.key)))
    inserted = 0
    for spec in BUILTIN_FEE_PROFILES:
        if spec["key"] in existing:
            continue
        data = dict(spec)
        data["fixed_fee_tiers_json"] = [dict(t) for t in spec["fixed_fee_tiers_json"]]
        session.add(FeeProfile(**data, is_builtin=True))
        inserted += 1
    if inserted:
        session.flush()
    return inserted


def get_profile(session: Session, key: str) -> FeeProfile | None:
    return session.scalars(select(FeeProfile).where(FeeProfile.key == key)).first()
