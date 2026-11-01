"""Inventory items and recording sales against them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ...models import InventoryItem, InventoryStatus
from ...schemas import InventoryIn, InventoryOut, InventoryUpdate, Message, Page, SaleIn, SaleOut
from ...services import inventory as service
from ..deps import MAX_PAGE_SIZE, get_db
from ._common import count, like_pattern, split_csv_param

router = APIRouter(prefix="/inventory", tags=["inventory"])
ALL_STATUSES = {s.value for s in InventoryStatus}


@router.get("", response_model=Page[InventoryOut])
def list_inventory(
    status_filter: str | None = Query(None, alias="status", max_length=200),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Page[InventoryOut]:
    stmt = select(InventoryItem)
    statuses = split_csv_param(status_filter)
    if statuses:
        invalid = [s for s in statuses if s not in ALL_STATUSES]
        if invalid:
            raise HTTPException(422, f"invalid status: {', '.join(invalid)}")
        stmt = stmt.where(InventoryItem.status.in_(statuses))
    if q and q.strip():
        pattern = like_pattern(q)
        stmt = stmt.where(or_(func.lower(InventoryItem.title).like(pattern, escape="\\"),
                              func.lower(InventoryItem.sku).like(pattern, escape="\\")))
    total = count(db, stmt)
    rows = db.scalars(
        stmt.options(selectinload(InventoryItem.sales))
        .order_by(InventoryItem.purchased_at.desc(), InventoryItem.id.desc())
        .limit(limit).offset(offset)
    ).all()
    return Page[InventoryOut](items=[service.inventory_out(r) for r in rows], total=total, limit=limit, offset=offset)


@router.post("", response_model=InventoryOut, status_code=status.HTTP_201_CREATED)
def create_item(body: InventoryIn, db: Session = Depends(get_db)) -> InventoryOut:
    item = service.create_item(db, body)
    db.commit()
    return service.inventory_out(item)


@router.get("/{item_id}", response_model=InventoryOut)
def get_item(item_id: int, db: Session = Depends(get_db)) -> InventoryOut:
    return service.inventory_out(service.get_item(db, item_id))


@router.patch("/{item_id}", response_model=InventoryOut)
def update_item(item_id: int, body: InventoryUpdate, db: Session = Depends(get_db)) -> InventoryOut:
    item = service.update_item(db, service.get_item(db, item_id), body)
    db.commit()
    return service.inventory_out(item)


@router.delete("/{item_id}", response_model=Message)
def delete_item(item_id: int, db: Session = Depends(get_db)) -> Message:
    service.delete_item(db, service.get_item(db, item_id))
    db.commit()
    return Message(message=f"inventory item {item_id} deleted")


@router.post("/{item_id}/sales", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
def record_sale(item_id: int, body: SaleIn, db: Session = Depends(get_db)) -> SaleOut:
    sale = service.record_sale(db, service.get_item(db, item_id), body)
    db.commit()
    return service.sale_out(sale)
