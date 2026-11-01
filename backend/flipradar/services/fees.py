"""Built-in fee profiles (selling channels) and their seeding.

IMPORTANT: the numbers below are PROVISIONAL placeholders supplied by the project lead and must be
verified against each platform's current published fee schedule before relying on them.  Each profile
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
_AS_OF = "2026-10"
_PROVISIONAL = "PROVISIONAL - verify against the platform's current published fees before relying on it."

# Fixed per-unit charges by sale price (centavos).  Provisional: per-unit cost of the shipping service
# for items <= 0.3 kg sold by a seller with green reputation.
_ML_TIERS: list[dict[str, Any]] = [
    {"max_price_cents": 9899, "fee_cents": 2500},
    {"max_price_cents": 19899, "fee_cents": 3200},
    {"max_price_cents": 29899, "fee_cents": 3500},
    {"max_price_cents": 49899, "fee_cents": 5240},
    {"max_price_cents": 99899, "fee_cents": 6550},
    {"max_price_cents": None, "fee_cents": 6550},
]

_WITHHOLDING_NOTE = (
    "Platform withholdings (ISR 2.5 % / IVA 8 % of the pre-IVA price) assume a persona fisica seller "
    "WITH RFC registered on the platform; sellers without RFC are withheld at much higher rates."
)

BUILTIN_FEE_PROFILES: list[dict[str, Any]] = [
    {
        "key": "ml_clasica",
        "name": "Mercado Libre - Clasica",
        "channel": "mercadolibre",
        "commission_bp": 1500,
        "fixed_fee_tiers_json": _ML_TIERS,
        "payment_fee_bp": 0,
        "payment_fixed_cents": 0,
        "iva_on_fees": False,
        "iva_rate_bp": 1600,
        "seller_shipping_cents": 0,
        "free_shipping_threshold_cents": None,
        "isr_withholding_bp": 250,
        "iva_withholding_bp": 800,
        "source_url": _ML_SOURCE,
        "as_of": _AS_OF,
        "notes": f"{_PROVISIONAL} Commission varies by category. {_WITHHOLDING_NOTE}",
    },
    {
        "key": "ml_premium",
        "name": "Mercado Libre - Premium",
        "channel": "mercadolibre",
        "commission_bp": 1950,
        "fixed_fee_tiers_json": _ML_TIERS,
        "payment_fee_bp": 0,
        "payment_fixed_cents": 0,
        "iva_on_fees": False,
        "iva_rate_bp": 1600,
        "seller_shipping_cents": 0,
        "free_shipping_threshold_cents": None,
        "isr_withholding_bp": 250,
        "iva_withholding_bp": 800,
        "source_url": _ML_SOURCE,
        "as_of": _AS_OF,
        "notes": f"{_PROVISIONAL} Premium listings include interest-free installments. {_WITHHOLDING_NOTE}",
    },
    {
        "key": "amazon_mx_standard",
        "name": "Amazon Mexico - standard referral fee",
        "channel": "amazon_mx",
        "commission_bp": 1500,
        "fixed_fee_tiers_json": [],
        "payment_fee_bp": 0,
        "payment_fixed_cents": 0,
        "iva_on_fees": False,
        "iva_rate_bp": 1600,
        "seller_shipping_cents": 0,
        "free_shipping_threshold_cents": None,
        "isr_withholding_bp": 250,
        "iva_withholding_bp": 800,
        "source_url": None,
        "as_of": _AS_OF,
        "notes": (
            f"{_PROVISIONAL} Source URL not yet recorded: check the referral-fee table in Amazon Seller "
            "Central Mexico (rates vary by category; FBA/subscription fees are not included). "
            f"{_WITHHOLDING_NOTE}"
        ),
    },
    {
        "key": "facebook_local",
        "name": "Facebook Marketplace - local pickup (cash)",
        "channel": "facebook",
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
        "as_of": _AS_OF,
        "notes": (
            f"{_PROVISIONAL} Assumes in-person pickup paid in cash: no platform fee, no shipping, no "
            "withholding. Your own tax obligations still apply."
        ),
    },
    {
        "key": "mercadopago_link",
        "name": "Mercado Pago - payment link",
        "channel": "mercadopago",
        "commission_bp": 0,
        "fixed_fee_tiers_json": [],
        "payment_fee_bp": 349,
        "payment_fixed_cents": 400,
        "iva_on_fees": True,
        "iva_rate_bp": 1600,
        "seller_shipping_cents": 0,
        "free_shipping_threshold_cents": None,
        "isr_withholding_bp": 0,
        "iva_withholding_bp": 0,
        "source_url": None,
        "as_of": _AS_OF,
        "notes": (
            f"{_PROVISIONAL} Source URL not yet recorded: check Mercado Pago's 'costos por recibir pagos' "
            "page (rate depends on when you want the money released)."
        ),
    },
    {
        "key": "local_cash",
        "name": "Local sale - cash, no fees",
        "channel": "local",
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
        "as_of": _AS_OF,
        "notes": f"{_PROVISIONAL} Direct cash sale with no platform involved.",
    },
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
