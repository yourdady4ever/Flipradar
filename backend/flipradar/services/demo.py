"""SYNTHETIC demo dataset - for trying the app without any API credentials.

Everything created here is clearly labelled: source ``demo``, ``is_demo=True``, titles prefixed
``[DEMO] ``, product keys prefixed ``demo|``, no URLs.  The prices are NOT market data: they are derived
deterministically from the fixed tables below (no randomness) so the demo always looks the same.
Demo products never share a cluster with real listings, and demo data is hidden from analytics and
opportunity lists unless ``include_demo_data`` is on (``seed_demo`` turns it on, ``clear_demo`` off).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Listing, Notification, Opportunity, PriceObservation, Product, SourceKind
from . import analysis, settings_store
from .alerts import fold
from .bootstrap import ensure_source
from .ingestion import DEMO_KEY_PREFIX, DEMO_SOURCE_KEY
from .timeutil import as_utc, utcnow

DEMO_PREFIX = "[DEMO] "
OBSERVATION_DAYS = (20, 15, 10, 5, 0)  # days before "now"


@dataclass(frozen=True)
class DemoProduct:
    slug: str
    title: str
    brand: str
    model: str
    category: str
    base_pesos: int  # synthetic reference price (whole pesos)
    attributes: dict[str, Any]
    patterns: str  # which listing patterns (letters below) this product uses


@dataclass(frozen=True)
class Pattern:
    key: str
    price_bp: int  # final price relative to base (10000 = 100 %)
    drift_bp: int  # price change per 5-day step going back in time (+ = was more expensive before)
    shipping_pesos: int | None
    condition: str
    seller: str  # key into SELLERS
    sold_start: int
    sold_per_step: int
    available: int
    suffix: str = ""
    description: str = ""
    status: str = "active"


PRODUCTS: tuple[DemoProduct, ...] = (
    DemoProduct("iphone15pro256", "Apple iPhone 15 Pro 256GB", "apple", "iphone 15 pro", "phone", 15500,
                {"storage_gb": 256}, "ABCDEFGHIJ"),
    DemoProduct("galaxys24128", "Samsung Galaxy S24 128GB", "samsung", "galaxy s24", "phone", 9800,
                {"storage_gb": 128}, "ABCDEFGK"),
    DemoProduct("ps5slim1tb", "Sony PlayStation 5 Slim 1TB", "sony", "playstation 5 slim", "console", 8900,
                {"storage_gb": 1024}, "ABCDEFHJK"),
    DemoProduct("switcholed", "Nintendo Switch OLED", "nintendo", "switch oled", "console", 5600,
                {"storage_gb": 64}, "ABCDEFG"),
    DemoProduct("steamdeckoled512", "Valve Steam Deck OLED 512GB", "valve", "steam deck oled", "handheld", 11200,
                {"storage_gb": 512}, "ABCDEFGHIJK"),
    DemoProduct("rogallyz1e", "ASUS ROG Ally Z1 Extreme 512GB", "asus", "rog ally z1 extreme", "handheld", 9900,
                {"storage_gb": 512}, "ABCDEFH"),
)

# Deal patterns (G, H) sit ~35 % below the synthetic reference: with marketplace fees and platform
# withholdings (~24 % of the sale price on the default profile) smaller gaps are not profitable.
PATTERNS: dict[str, Pattern] = {
    p.key: p
    for p in (
        Pattern("A", 10000, 0, 0, "used", "s1", 14, 2, 3),
        Pattern("B", 10300, 50, 150, "used", "s2", 4, 0, 1),
        Pattern("C", 9700, 100, 0, "used", "s3", 9, 1, 2),
        Pattern("D", 10600, 0, 0, "used", "s4", 2, 0, 1),
        Pattern("E", 9900, -50, 99, "used", "s5", 6, 1, 1),
        Pattern("F", 10150, 0, 0, "used", "s6", 20, 1, 4),
        Pattern("G", 6400, 150, 0, "used", "s7", 1, 0, 1, suffix=" - precio de oportunidad"),
        Pattern("H", 6600, 0, 120, "used", "s8", 0, 0, 1, suffix=" seminuevo, urge vender"),
        Pattern("I", 3000, 0, 0, "used", "s9", 0, 0, 10, suffix=" replica 1:1 AAA",
                description="Envio rapido. Pago solo por deposito OXXO, mandame WhatsApp."),
        Pattern("J", 10400, 0, 0, "used", "s10", 3, 0, 1, suffix=" con caja"),
        Pattern("K", 9600, 0, 0, "used", "s11", 1, 0, 0, status="sold"),
    )
}

SELLERS: dict[str, dict[str, Any]] = {
    "s1": {"name": "DemoTienda Uno", "transactions_total": 1500, "reputation_level": "5_green", "registered_days": 2400},
    "s2": {"name": "DemoTienda Dos", "transactions_total": 320, "reputation_level": "5_green", "registered_days": 1500},
    "s3": {"name": "DemoTienda Tres", "transactions_total": 780, "reputation_level": "4_light_green", "registered_days": 1900},
    "s4": {"name": "DemoTienda Cuatro", "transactions_total": 95, "reputation_level": "5_green", "registered_days": 900},
    "s5": {"name": "DemoTienda Cinco", "transactions_total": 410, "reputation_level": "5_green", "registered_days": 1200},
    "s6": {"name": "DemoTienda Seis", "transactions_total": 2600, "reputation_level": "5_green", "registered_days": 3000},
    "s7": {"name": "DemoVendedor Siete", "transactions_total": 58, "reputation_level": "5_green", "registered_days": 700},
    "s8": {"name": "DemoVendedor Ocho", "transactions_total": 33, "reputation_level": "4_light_green", "registered_days": 500},
    "s9": {"name": "DemoVendedor Nueve", "transactions_total": 2, "reputation_level": "2_orange", "registered_days": 12},
    "s10": {"name": "DemoTienda Diez", "transactions_total": 140, "reputation_level": "5_green", "registered_days": 1100},
    "s11": {"name": "DemoTienda Once", "transactions_total": 260, "reputation_level": "5_green", "registered_days": 1300},
}
LOCATIONS = ("CDMX", "Guadalajara, Jal.", "Monterrey, N.L.", "Puebla, Pue.", "Queretaro, Qro.")


def _pesos_bp(base_pesos: int, bp: int) -> int:
    """Whole-peso price (as centavos) = base * bp / 10000, rounded half-up to the peso."""
    pesos = (Decimal(base_pesos) * Decimal(bp) / Decimal(10000)).to_integral_value(rounding=ROUND_HALF_UP)
    return int(pesos) * 100


def _seller_json(seller: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "transactions_total": seller["transactions_total"],
        "reputation_level": seller["reputation_level"],
        "registration_date": (now - timedelta(days=seller["registered_days"])).date().isoformat() + "T00:00:00+00:00",
        "synthetic": True,
    }


def clear_demo(session: Session) -> dict[str, int]:
    """Remove every demo row and turn ``include_demo_data`` off.  Commits."""
    demo_opp_ids = select(Opportunity.id).where(Opportunity.is_demo.is_(True))
    notes = session.execute(delete(Notification).where(Notification.opportunity_id.in_(demo_opp_ids))).rowcount
    opps = session.execute(delete(Opportunity).where(Opportunity.is_demo.is_(True))).rowcount
    demo_listing_ids = select(Listing.id).where(Listing.is_demo.is_(True))
    session.execute(delete(PriceObservation).where(PriceObservation.listing_id.in_(demo_listing_ids)))
    listings = session.execute(delete(Listing).where(Listing.is_demo.is_(True))).rowcount
    demo_products = select(Product.id).where(Product.canonical_key.like(f"{DEMO_KEY_PREFIX}%"))
    # Real opportunities can never point at demo products, but be defensive.
    session.execute(delete(Opportunity).where(Opportunity.product_id.in_(demo_products)))
    products = session.execute(delete(Product).where(Product.canonical_key.like(f"{DEMO_KEY_PREFIX}%"))).rowcount
    settings_store.set_value(session, "include_demo_data", False)
    session.commit()
    session.expire_all()
    return {"products": products or 0, "listings": listings or 0, "opportunities": opps or 0, "notifications": notes or 0}


def seed_demo(session: Session, settings: Settings | None = None, *, now: datetime | None = None) -> dict[str, int]:
    """(Re)create the synthetic dataset and analyse it.  Idempotent: existing demo rows are replaced."""
    now = as_utc(now or utcnow())
    clear_demo(session)
    source = ensure_source(session, DEMO_SOURCE_KEY, name="Demo data (SYNTHETIC)", kind=SourceKind.DEMO.value)
    product_ids: list[int] = []
    listing_count = observation_count = 0
    for p_index, spec in enumerate(PRODUCTS):
        product = Product(
            canonical_key=f"{DEMO_KEY_PREFIX}{spec.brand}|{spec.model}|{spec.slug}",
            title=DEMO_PREFIX + spec.title,
            brand=spec.brand,
            model=spec.model,
            category=spec.category,
            gtin=None,
            attributes_json={**spec.attributes, "synthetic": True},
        )
        session.add(product)
        session.flush()
        product_ids.append(product.id)
        for l_index, letter in enumerate(spec.patterns):
            pattern = PATTERNS[letter]
            seller = SELLERS[pattern.seller]
            title = f"{DEMO_PREFIX}{spec.title}{pattern.suffix}"
            final_price = _pesos_bp(spec.base_pesos, pattern.price_bp)
            steps = OBSERVATION_DAYS if pattern.status == "active" else OBSERVATION_DAYS[:-1]
            last_day = steps[-1]
            listing = Listing(
                source_id=source.id,
                external_id=f"demo-{spec.slug}-{letter}",
                url=None,
                title=title,
                normalized_title=fold(title),
                currency="MXN",
                price_cents=final_price,
                price_mxn_cents=final_price,
                shipping_cents=None if pattern.shipping_pesos is None else pattern.shipping_pesos * 100,
                free_shipping=pattern.shipping_pesos == 0,
                condition=pattern.condition,
                brand=spec.brand,
                model=spec.model,
                category=spec.category,
                attributes_json={"normalized": dict(spec.attributes), "synthetic": True},
                seller_id=f"demo-{pattern.seller}",
                seller_name=seller["name"],
                seller_json=_seller_json(seller, now),
                location=LOCATIONS[(p_index + l_index) % len(LOCATIONS)],
                description=pattern.description or None,
                available_quantity=pattern.available,
                sold_quantity=pattern.sold_start + pattern.sold_per_step * (len(steps) - 1),
                listing_type="demo",
                is_accessory=False,
                is_bundle=False,
                status=pattern.status,
                product_id=product.id,
                match_confidence=1.0,
                match_method="demo",
                is_demo=True,
                first_seen_at=now - timedelta(days=OBSERVATION_DAYS[0]),
                last_seen_at=now - timedelta(days=last_day),
                raw_json={"synthetic": True, "pattern": letter},
            )
            session.add(listing)
            session.flush()
            listing_count += 1
            for step, days_ago in enumerate(steps):
                remaining = len(OBSERVATION_DAYS) - 1 - step
                price = _pesos_bp(spec.base_pesos, pattern.price_bp + pattern.drift_bp * remaining)
                is_last = step == len(steps) - 1
                session.add(
                    PriceObservation(
                        listing_id=listing.id,
                        observed_at=now - timedelta(days=days_ago),
                        price_cents=price,
                        currency="MXN",
                        price_mxn_cents=price,
                        available_quantity=pattern.available,
                        sold_quantity=pattern.sold_start + pattern.sold_per_step * step,
                        status=pattern.status if is_last else "active",
                    )
                )
                observation_count += 1
    settings_store.set_value(session, "include_demo_data", True)
    session.commit()
    result = analysis.recompute(session, product_ids, now=now, settings=settings, dispatch=False)
    return {
        "products": len(product_ids),
        "listings": listing_count,
        "observations": observation_count,
        "opportunities_created": result.opportunities_created,
        "notifications_created": result.notifications_created,
    }
