"""Inventory (things you bought) and sales (things you sold), with deterministic profit accounting.

Cost basis uses the item's *average* unit cost, ``total_cost / quantity`` with
``total_cost = unit_cost * quantity + extra_costs``, allocated **cumulatively**: after ``n`` units are sold
the sales together carry ``round_half_up(total_cost * n / quantity)``, so each sale's basis is that target
minus what earlier sales already carry and the sale that empties the item takes the remainder.  Invariant:
``sum(sale.cost_basis_cents) + remaining_cost_cents(item) == total_cost_cents(item)``, always.

* Once an item has sales its unit cost and extra costs are locked (409): the sold units were costed with
  them.  Delete the sales to change the cost, or record a late cost (e.g. an import-duty invoice) on the
  next sale as ``other_costs_cents``.
* Writing an item off (status ``written_off``) books its remaining cost as a realized loss dated
  ``written_off_at`` (see services/analytics.py).  ``returned`` means the purchase went back to the seller
  for a refund: the cost leaves inventory without a loss.
* Selling fees can be computed with the fee calculator (per unit, then multiplied by the units sold) or
  supplied as overrides with the amounts actually charged.  Shipping: Mercado Libre profiles model the
  per-unit shipping charged to the seller as their fixed-fee tier; a sale's ``shipping_cents`` > 0 (what
  you actually paid) REPLACES that tier instead of being added to it.  Leave it 0 to use the estimate.
* Stock is reserved with a conditional UPDATE, so two simultaneous sales can never oversell (409).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import case, func, select, update
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
#: Fee-profile channels whose fixed-fee tiers are the per-unit shipping charged to the seller (Mercado Libre's
#: "costo de los envios", see services/fees.py).  A sale's actual shipping replaces that tier.
SHIPPING_TIER_CHANNELS = frozenset({"mercadolibre"})
COST_FIELDS = ("unit_cost_cents", "extra_costs_cents")


def _half_up(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def total_cost_cents(item: InventoryItem) -> int:
    return int(item.unit_cost_cents) * int(item.quantity) + int(item.extra_costs_cents or 0)


def average_unit_cost(item: InventoryItem) -> Decimal:
    quantity = max(1, int(item.quantity))
    return Decimal(total_cost_cents(item)) / Decimal(quantity)


def cost_basis_for(item: InventoryItem, units: int) -> int:
    """Average cost of ``units`` units in isolation (display only; sales use :func:`cumulative_cost_cents`)."""
    return _half_up(average_unit_cost(item) * units)


def cumulative_cost_cents(item: InventoryItem, units_sold: int) -> int:
    """Cost carried by the first ``units_sold`` units: ``total_cost * units_sold / quantity``, half-up."""
    quantity = max(1, int(item.quantity))
    if units_sold >= quantity:
        return total_cost_cents(item)
    return _half_up(Decimal(total_cost_cents(item)) * max(0, units_sold) / Decimal(quantity))


def allocated_cost_cents(item: InventoryItem) -> int:
    """Purchase cost already expensed by the item's sales."""
    return sum(int(s.cost_basis_cents) for s in item.sales)


def remaining_cost_cents(item: InventoryItem) -> int:
    """Cost of the units not sold: total cost minus what the sales carry (reconciles to the centavo)."""
    return total_cost_cents(item) - allocated_cost_cents(item)


def written_off_cost_cents(item: InventoryItem) -> int:
    """Realized loss of a written-off item (its remaining cost); 0 for any other status."""
    return remaining_cost_cents(item) if item.status == InventoryStatus.WRITTEN_OFF.value else 0


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
    for name in ("title", "condition", *COST_FIELDS):
        if name in changes and changes[name] is None:
            raise Invalid(f"{name} cannot be null")
    if any(name in changes and changes[name] != getattr(item, name) for name in COST_FIELDS) and item.sales:
        raise Conflict(
            "this item already has sales, so its cost is locked (the sold units were costed with it); delete "
            "its sales to change it, or record the extra cost on the next sale as other_costs_cents"
        )
    new_status = changes.get("status")
    if new_status is not None:
        if item.quantity_available == 0 and new_status in OPEN_STATUSES:
            raise Conflict("all units are sold; delete a sale first to put units back in stock")
        if new_status != item.status:
            if new_status == InventoryStatus.WRITTEN_OFF.value:
                item.written_off_at = utcnow()
            elif item.status == InventoryStatus.WRITTEN_OFF.value:
                item.written_off_at = None
    for name, value in changes.items():
        if name == "status" and value is None:
            continue
        setattr(item, name, value.strip() if name == "title" else value)
    session.flush()
    return item


def delete_item(session: Session, item: InventoryItem) -> None:
    session.delete(item)
    session.flush()


def _reserve_units(session: Session, item: InventoryItem, units: int) -> None:
    """Take ``units`` out of stock atomically, or raise Conflict.

    A conditional UPDATE (checked through its rowcount) instead of read-check-write: two simultaneous sales
    of the last unit cannot both pass, because the second UPDATE sees the first one's result.  On SQLite the
    UPDATE also takes the write lock, so the item is stable until the caller commits.
    """
    result = session.execute(
        update(InventoryItem)
        .where(InventoryItem.id == item.id, InventoryItem.quantity_available >= units,
               InventoryItem.status.not_in(sorted(UNSELLABLE)))
        .values(quantity_available=InventoryItem.quantity_available - units)
        .execution_options(synchronize_session=False)
    )
    session.refresh(item)
    if result.rowcount != 1:
        if item.status in UNSELLABLE:
            raise Conflict(f"item is {item.status}; it cannot be sold")
        raise Conflict(f"only {item.quantity_available} unit(s) available")


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
    shipping_replaces_tier = False
    if data.fee_profile_key:
        profile = get_profile(session, data.fee_profile_key)
        if profile is None:
            raise Invalid(f"unknown fee profile '{data.fee_profile_key}'")
        fee_model = FeeModel.from_profile(profile)
        shipping_replaces_tier = (data.shipping_cents > 0 and profile.channel in SHIPPING_TIER_CHANNELS
                                  and bool(fee_model.fixed_fee_tiers))
    if fee_model is not None and (data.fees_cents is None or data.taxes_withheld_cents is None):
        calc_model = fee_model
        if shipping_replaces_tier:
            # The tier is this channel's estimate of the shipping charged to the seller; the shipping
            # actually paid (shipping_cents) replaces it so the same charge is not subtracted twice.
            calc_model = dataclasses.replace(fee_model, fixed_fee_tiers=())
            breakdown["shipping_source"] = "actual (replaces the profile's per-unit shipping tier)"
            breakdown["replaced_shipping_estimate_cents"] = fee_model.fixed_fee_for(data.unit_price_cents) * units
        per_unit = calculate_profit(
            ProfitInputs(
                buy_price_cents=_half_up(average_unit_cost(item)),
                sale_price_cents=data.unit_price_cents,
                fee=calc_model,
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

    _reserve_units(session, item, units)  # fresh item state from here on (write lock held on SQLite)
    units_sold_before, allocated_before = (int(v) for v in session.execute(
        select(func.coalesce(func.sum(Sale.quantity), 0), func.coalesce(func.sum(Sale.cost_basis_cents), 0))
        .where(Sale.inventory_item_id == item.id)
    ).one())
    cumulative = cumulative_cost_cents(item, units_sold_before + units)
    cost_basis = cumulative - allocated_before
    revenue = data.unit_price_cents * units
    net = revenue - fees - data.shipping_cents - taxes - data.other_costs_cents
    profit = net - cost_basis
    breakdown.update(
        revenue_cents=revenue, fees_cents=fees, shipping_cents=data.shipping_cents, taxes_withheld_cents=taxes,
        other_costs_cents=data.other_costs_cents, cost_basis_cents=cost_basis, net_proceeds_cents=net,
        profit_cents=profit,
        cost_allocation={"units_sold_after": units_sold_before + units, "cumulative_cost_cents": cumulative,
                         "allocated_by_earlier_sales_cents": allocated_before},
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
    if item.quantity_available == 0:
        item.status = InventoryStatus.SOLD.value
    session.flush()
    session.expire(item, ["sales"])
    return sale


def get_sale(session: Session, sale_id: int) -> Sale:
    sale = session.get(Sale, sale_id)
    if sale is None:
        raise NotFound(f"sale {sale_id} not found")
    return sale


def delete_sale(session: Session, sale: Sale) -> InventoryItem:
    item = sale.item
    restored = InventoryItem.quantity_available + int(sale.quantity)
    session.delete(sale)
    session.flush()
    # Relative, clamped update in SQL (never a stale absolute value written back).
    session.execute(
        update(InventoryItem)
        .where(InventoryItem.id == item.id)
        .values(quantity_available=case((restored > InventoryItem.quantity, InventoryItem.quantity), else_=restored))
        .execution_options(synchronize_session=False)
    )
    session.refresh(item)
    if item.status == InventoryStatus.SOLD.value and item.quantity_available > 0:
        item.status = InventoryStatus.IN_STOCK.value
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
        # A write-off is a realized loss of the units' remaining cost.
        realized_profit_cents=sum(int(s.profit_cents) for s in sales) - written_off_cost_cents(item),
        sales=[sale_out(s) for s in sales],
        created_at=as_utc(item.created_at),
        updated_at=as_utc(item.updated_at),
    )
