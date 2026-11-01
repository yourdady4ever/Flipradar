"""Listings (offers observed on sources)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ...config import Settings
from ...models import Listing, Source
from ...schemas import ListingDetailOut, ListingOut, Page
from ...services.serialize import listing_detail_out, listing_out
from ..deps import MAX_PAGE_SIZE, get_db, get_settings
from ._common import count, include_demo, like_pattern

router = APIRouter(prefix="/listings", tags=["listings"])


@router.get("", response_model=Page[ListingOut])
def list_listings(
    q: str | None = Query(None, max_length=200),
    source: str | None = Query(None, max_length=64),
    product_id: int | None = None,
    suspicious: bool | None = None,
    status: str | None = Query(None, max_length=16),
    include_demo_data: bool | None = Query(None, alias="include_demo"),
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Page[ListingOut]:
    stmt = select(Listing).join(Source, Source.id == Listing.source_id)
    if q and q.strip():
        pattern = like_pattern(q)
        stmt = stmt.where(or_(func.lower(Listing.title).like(pattern, escape="\\"),
                              Listing.normalized_title.like(pattern, escape="\\")))
    if source:
        stmt = stmt.where(Source.key == source)
    if product_id is not None:
        stmt = stmt.where(Listing.product_id == product_id)
    if suspicious is not None:
        stmt = stmt.where(Listing.is_suspicious.is_(suspicious))
    if status:
        stmt = stmt.where(Listing.status == status)
    if not include_demo(db, settings, include_demo_data):
        stmt = stmt.where(Listing.is_demo.is_(False))
    total = count(db, stmt)
    rows = db.scalars(
        stmt.options(selectinload(Listing.source)).order_by(Listing.last_seen_at.desc(), Listing.id.desc())
        .limit(limit).offset(offset)
    ).all()
    return Page[ListingOut](items=[listing_out(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{listing_id}", response_model=ListingDetailOut)
def get_listing(listing_id: int, db: Session = Depends(get_db)) -> ListingDetailOut:
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(404, f"listing {listing_id} not found")
    return listing_detail_out(listing)
