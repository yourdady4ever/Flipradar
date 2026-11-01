"""Inventory (things you bought) and sales (things you sold), with deterministic profit accounting.

Cost basis uses the item's *average* unit cost: ``(unit_cost * quantity + extra_costs) / quantity``
(Decimal, rounded half-up per sale).  Selling fees can be computed with the fee calculator (per unit,
then multiplied by the units sold) or supplied as overrides with the amounts actually charged.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.orm import Session

from ..finance.calculator import FeeModel, ProfitInputs, calculate_profit
from ..models import InventoryItem, InventoryStatus, Opportunity, OpportunityStatus, Product, Sale
from ..schemas import InventoryIn, InventoryOut, InventoryUpdate, OpportunityBuyIn, SaleIn, SaleOut
from .analysis import shipping_mxn_cents
from .errors import Conflict, Invalid, NotFound
from .fees import get_profile
from .ingestion import safe_url
from .timeutil import as_utc, utcnow

FEE_LINES = ("commission", "fixed_fee", "payment_fee", "iva_on_fees")
TAX_LINES = ("isr_withheld", "iva_withheld")
UNSELLABLE = frozenset({InventoryStatus.RETURNED.value, InventoryStatus.WRITTEN_OFF.value})
OPEN_STATUSES = frozenset({InventoryStatus.PURCHASED.value, InventoryStatus.IN_TRANSIT.value,
                           InventoryStatus.IN_STOCK.value, InventoryStatus.LISTED.value})


def _half_up(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def total_cost_cents(item: InventoryItem) -> int:
    return int(item.unit_cost_cents) * int(item.quantity) + int(item.extra_costs_cents or 0)


def average_unit_cost(item: InventoryItem) -> Decimal:
    quantity = max(1, int(item.quantity))
    return Decimal(total_cost_cents(item)) / Decimal(quantity)


def cost_basis_for(item: InventoryItem, units: int) -> int:
    return _half_up(average_unit_cost(item) * units)


def get_item(session: Session, item_id: int) -> InventoryItem:
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise NotFound(f"inventory item {item_id} not found")
    return item


def create_item(session: Session, data: InventoryIn) -> InventoryItem:
    if data.product_id is not None and session.get(Product, data.product_id) is None:
        raise Invalid(f"product {data.product_id} does not exist")
    if data.opportunity_id is not None and session.get(Opportunity, data.opportunity_id) is None:
        raise Invalid(f"opportunity {data.opportunity_id} does not exist")
    if data.purchase_url and safe_url(data.purchase_url) is None:
        raise Invalid("purchase_url must be an absolute http(s) URL")
    item = InventoryItem(
        title=data.title.strip(),
        sku=data.sku,
        product_id=data.product_id,
        opportunity_id=data.opportunity_id,
        condition=data.condition,
        quantity=data.quantity,
        quantity_available=data.quantity,
        unit_cost_cents=data.unit_cost_cents,
        extra_costs_cents=data.extra_costs_cents,
        purchase_source=data.purchase_source,
        purchase_url=data.purchase_url,
        purchased_at=as_utc(data.purchased_at) if data.purchased_at else utcnow(),
        status=data.status,
        target_sale_cents=data.target_sale_cents,
        listed_channel=data.listed_channel,
        location=data.location,
        notes=data.notes,
    )
    session.add(item)
    session.flush()
    return item


def create_from_opportunity(session: Session, opportunity_id: int, data: OpportunityBuyIn) -> InventoryItem:
    opp = session.get(Opportunity, opportunity_id)
    if opp is None:
        raise NotFound(f"opportunity {opportunity_id} not found")
    listing = opp.buy_listing
    unit_cost = data.unit_cost_cents
    if unit_cost is None:
        if listing is None or listing.price_mxn_cents is None:
            raise Invalid("the listing has no MXN price; provide unit_cost_cents")
        unit_cost = int(listing.price_mxn_cents)
    extra = data.extra_costs_cents
    if extra is None:
        extra = (shipping_mxn_cents(listing) or 0) if listing is not None else 0
    title = (listing.title if listing is not None else None) or (opp.product.title if opp.product else "Item")
    item = InventoryItem(
        title=title[:300],
        product_id=opp.product_id,
        opportunity_id=opp.id,
        condition=(listing.condition if listing is not None else "unknown"),
        quantity=data.quantity,
        quantity_available=data.quantity,
        unit_cost_cents=unit_cost,
        extra_costs_cents=extra,
        purchase_source=(listing.source.key if listing is not None and listing.source else None),
        purchase_url=(listing.url if listing is not None else None),
        purchased_at=utcnow(),
        status=InventoryStatus.PURCHASED.value,
        target_sale_cents=opp.expected_sale_cents,
        notes=data.notes,
    )
    session.add(item)
    opp.status = OpportunityStatus.BOUGHT.value
    session.flush()
    return item


def update_item(session: Session, item: InventoryItem, data: InventoryUpdate) -> InventoryItem:
    changes = data.model_dump(exclude_unset=True)
    for name in ("title", "unit_cost_cents", "extra_costs_cents"):
        if name in changes and changes[name] is None:
            raise Invalid(f"{name} cannot be null")
    if "status" in changes and changes["status"] is not None:
        if item.quantity_available == 0 and changes["status"] in OPEN_STATUSES:
            raise Conflict("all units are sold; delete a sale first to put units back in stock")
    for name, value in changes.items():
        if name == "status" and value is None:
            continue
        setattr(item, name, value.strip() if name == "title" else value)
    session.flush()
    return item


def delete_item(session: Session, item: InventoryItem) -> None:
    session.delete(item)
    session.flush()


def record_sale(session: Session, item: InventoryItem, data: SaleIn) -> Sale:
    units = int(data.quantity)
    if item.status in UNSELLABLE:
        raise Conflict(f"item is {item.status}; it cannot be sold")
    if units > item.quantity_available:
        raise Conflict(f"only {item.quantity_available} unit(s) available")
    breakdown: dict[str, Any] = {"quantity": units, "unit_price_cents": data.unit_price_cents,
                                 "average_unit_cost": str(average_unit_cost(item))}
    fees = taxes = 0
    fee_model: FeeModel | None = None
    if data.fee_profile_key:
        profile = get_profile(session, data.fee_profile_key)
        if profile is None:
            raise Invalid(f"unknown fee profile '{data.fee_profile_key}'")
        fee_model = FeeModel.from_profile(profile)
    if fee_model is not None and (data.fees_cents is None or data.taxes_withheld_cents is None):
        per_unit = calculate_profit(
            ProfitInputs(
                buy_price_cents=_half_up(average_unit_cost(item)),
                sale_price_cents=data.unit_price_cents,
                fee=fee_model,
                # Actual shipping is recorded separately (shipping_cents is the real total paid).
                seller_shipping_override_cents=0,
            )
        )
        lines = {line.key: line.amount_cents for line in per_unit.selling_lines}
        fees = sum(lines.get(k, 0) for k in FEE_LINES) * units
        taxes = sum(lines.get(k, 0) for k in TAX_LINES) * units
        breakdown["per_unit"] = per_unit.to_dict()
    if data.fees_cents is not None:
        fees = data.fees_cents
        breakdown["fees_source"] = "override"
    else:
        breakdown["fees_source"] = "calculator" if fee_model is not None else "none"
    if data.taxes_withheld_cents is not None:
        taxes = data.taxes_withheld_cents
        breakdown["taxes_source"] = "override"
    else:
        breakdown["taxes_source"] = "calculator" if fee_model is not None else "none"

    revenue = data.unit_price_cents * units
    cost_basis = cost_basis_for(item, units)
    net = revenue - fees - data.shipping_cents - taxes - data.other_costs_cents
    profit = net - cost_basis
    breakdown.update(
        revenue_cents=revenue, fees_cents=fees, shipping_cents=data.shipping_cents, taxes_withheld_cents=taxes,
        other_costs_cents=data.other_costs_cents, cost_basis_cents=cost_basis, net_proceeds_cents=net,
        profit_cents=profit,
    )
    sale = Sale(
        inventory_item_id=item.id,
        quantity=units,
        unit_price_cents=data.unit_price_cents,
        channel=data.channel,
        fee_profile_key=data.fee_profile_key,
        fees_cents=fees,
        shipping_cents=data.shipping_cents,
        taxes_withheld_cents=taxes,
        other_costs_cents=data.other_costs_cents,
        cost_basis_cents=cost_basis,
        net_proceeds_cents=net,
        profit_cents=profit,
        breakdown_json=breakdown,
        sold_at=as_utc(data.sold_at) if data.sold_at else utcnow(),
        notes=data.notes,
    )
    session.add(sale)
    item.quantity_available -= units
    if item.quantity_available == 0:
        item.status = InventoryStatus.SOLD.value
    session.flush()
    return sale


def get_sale(session: Session, sale_id: int) -> Sale:
    sale = session.get(Sale, sale_id)
    if sale is None:
        raise NotFound(f"sale {sale_id} not found")
    return sale


def delete_sale(session: Session, sale: Sale) -> InventoryItem:
    item = sale.item
    item.quantity_available = min(int(item.quantity), int(item.quantity_available) + int(sale.quantity))
    if item.status == InventoryStatus.SOLD.value and item.quantity_available > 0:
        item.status = InventoryStatus.IN_STOCK.value
    session.delete(sale)
    session.flush()
    session.expire(item, ["sales"])
    return item


def sale_out(sale: Sale) -> SaleOut:
    out = SaleOut.model_validate(sale)
    return out.model_copy(update={"sold_at": as_utc(sale.sold_at)})


def days_held(item: InventoryItem, now: datetime | None = None) -> int:
    now = as_utc(now or utcnow())
    end = now
    if item.quantity_available == 0 and item.sales:
        end = max(as_utc(s.sold_at) for s in item.sales)
    return max(0, (end - as_utc(item.purchased_at)).days)


def inventory_out(item: InventoryItem, now: datetime | None = None) -> InventoryOut:
    sales = sorted(item.sales, key=lambda s: (as_utc(s.sold_at), s.id))
    return InventoryOut(
        id=item.id,
        title=item.title,
        sku=item.sku,
        product_id=item.product_id,
        opportunity_id=item.opportunity_id,
        condition=item.condition,
        quantity=item.quantity,
        quantity_available=item.quantity_available,
        unit_cost_cents=item.unit_cost_cents,
        extra_costs_cents=item.extra_costs_cents,
        total_cost_cents=total_cost_cents(item),
        purchase_source=item.purchase_source,
        purchase_url=item.purchase_url,
        purchased_at=as_utc(item.purchased_at),
        status=item.status,
        target_sale_cents=item.target_sale_cents,
        listed_channel=item.listed_channel,
        location=item.location,
        notes=item.notes,
        days_held=days_held(item, now),
        realized_profit_cents=sum(int(s.profit_cents) for s in sales),
        sales=[sale_out(s) for s in sales],
        created_at=as_utc(item.created_at),
        updated_at=as_utc(item.updated_at),
    )
