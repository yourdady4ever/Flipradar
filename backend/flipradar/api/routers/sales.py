"""Sales history (deleting a sale puts the units back in stock)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Sale
from ...schemas import Message, Page, SaleOut
from ...services import inventory as service
from ..deps import MAX_PAGE_SIZE, get_db
from ._common import count

router = APIRouter(prefix="/sales", tags=["sales"])


@router.get("", response_model=Page[SaleOut])
def list_sales(
    inventory_item_id: int | None = None,
    limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Page[SaleOut]:
    stmt = select(Sale)
    if inventory_item_id is not None:
        stmt = stmt.where(Sale.inventory_item_id == inventory_item_id)
    total = count(db, stmt)
    rows = db.scalars(stmt.order_by(Sale.sold_at.desc(), Sale.id.desc()).limit(limit).offset(offset)).all()
    return Page[SaleOut](items=[service.sale_out(s) for s in rows], total=total, limit=limit, offset=offset)


@router.delete("/{sale_id}", response_model=Message)
def delete_sale(sale_id: int, db: Session = Depends(get_db)) -> Message:
    """Delete a sale and put its units back in stock (a later sale is costed so the item still reconciles)."""
    sale = service.get_sale(db, sale_id)
    item = service.delete_sale(db, sale)
    db.commit()
    return Message(message=f"sale {sale_id} deleted; {item.quantity_available} unit(s) available again")
