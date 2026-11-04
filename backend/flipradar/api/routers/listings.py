"""Listings (offers observed on sources) and the "needs review" queue of unclustered listings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ...config import Settings
from ...models import Listing, Source
from ...schemas import MAX_ID, ListingAssignIn, ListingDetailOut, ListingOut, Page
from ...services import analysis, bootstrap, ingestion
from ...services.serialize import listing_detail_out, listing_out
from ..deps import MAX_PAGE_SIZE, get_db, get_settings
from ._common import count, include_demo, like_pattern

router = APIRouter(prefix="/listings", tags=["listings"])


@router.get("", response_model=Page[ListingOut])
def list_listings(
    q: str | None = Query(None, max_length=200),
    source: str | None = Query(None, max_length=64),
    product_id: int | None = Query(None, ge=1, le=MAX_ID),
    suspicious: bool | None = None,
    status: str | None = Query(None, max_length=16),
    unclustered: bool | None = Query(
        None, description="true: the review queue - listings without a product that are not accessories; "
                          "false: only listings that belong to a product"),
    include_demo_data: bool | None = Query(None, alias="include_demo"),
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0, le=MAX_ID),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Page[ListingOut]:
    """Listings, newest first (``last_seen_at``).  ``unclustered=true`` is the "needs review" queue: listings the
    matcher left without a product (and that are not accessories), to assign with ``POST /listings/{id}/assign``;
    ``GET /listings/{id}`` explains why in ``attributes.normalization_notes``."""
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
    if unclustered is True:
        stmt = stmt.where(Listing.product_id.is_(None), Listing.is_accessory.is_(False))
    elif unclustered is False:
        stmt = stmt.where(Listing.product_id.is_not(None))
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
    """One listing with its price history.  ``attributes.normalization_notes`` (when available) explains every
    inference of the title normalizer and every reason the listing was not clustered."""
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(404, f"listing {listing_id} not found")
    return listing_detail_out(listing)


@router.post("/{listing_id}/assign", response_model=ListingDetailOut)
def assign_listing(
    body: ListingAssignIn,
    listing_id: int = Path(ge=1, le=MAX_ID),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ListingDetailOut:
    """Assign a listing to a product by hand: exactly one of ``product_id`` (an existing product),
    ``new_product`` (creates a product keyed ``manual|<slug>|<id>``) or ``unassign: true`` (no product).

    The listing gets ``match_method="manual"`` and ``match_confidence=1.0``; automatic matching never changes
    a manual assignment.  The previous and the new product are recomputed (opportunities of the listing move
    with it, or expire when it no longer belongs to any product).  422 for an invalid request (none or several
    choices, unknown product, demo/real mix), 404 for an unknown listing."""
    bootstrap.begin_write(db)  # this transaction reads before it writes: take SQLite's write lock first
    listing = db.get(Listing, listing_id)
    if listing is None:
        db.rollback()
        raise HTTPException(404, f"listing {listing_id} not found")
    try:
        affected = ingestion.assign_listing(db, listing, body)
    except Exception:
        db.rollback()  # release the write lock right away (e.g. Invalid -> 422)
        raise
    db.commit()
    if affected:
        analysis.recompute(db, sorted(affected), settings=settings)
    db.refresh(listing)
    return listing_detail_out(listing)
