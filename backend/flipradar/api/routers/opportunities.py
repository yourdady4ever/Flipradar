"""Opportunities: ranked buy candidates with full, auditable breakdowns."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ...ai import narrative
from ...config import Settings
from ...models import Listing, Opportunity, OpportunityStatus, Product, Source
from ...money import MAX_CENTS
from ...schemas import (
    MAX_ID,
    AiSummaryOut,
    InventoryOut,
    OpportunityBuyIn,
    OpportunityDetailOut,
    OpportunityOut,
    OpportunityUpdate,
    Page,
)
from ...services import inventory as inventory_service
from ...services.serialize import opportunity_detail_out, opportunity_out
from ..deps import MAX_PAGE_SIZE, get_db, get_settings
from ._common import count, include_demo, like_pattern, split_csv_param

router = APIRouter(prefix="/opportunities", tags=["opportunities"])

ALL_STATUSES = {s.value for s in OpportunityStatus}
# Filters may be negative (losses) and must stay inside SQLite's 64-bit INTEGER (else: HTTP 500).
MAX_ROI_FILTER_BP = 10**9
DEFAULT_STATUSES = ("new", "seen", "watching", "bought")
SORTS = {
    "score": (Opportunity.score.desc(), Opportunity.net_profit_cents.desc()),
    "profit": (Opportunity.net_profit_cents.desc(), Opportunity.score.desc()),
    "roi": (Opportunity.roi_bp.desc(), Opportunity.score.desc()),
    "recent": (Opportunity.updated_at.desc(),),
}


def _get(db: Session, opportunity_id: int) -> Opportunity:
    opp = db.get(Opportunity, opportunity_id)
    if opp is None:
        raise HTTPException(404, f"opportunity {opportunity_id} not found")
    return opp


@router.get("", response_model=Page[OpportunityOut])
def list_opportunities(
    status_filter: str | None = Query(
        None, alias="status", max_length=100,
        description="Comma separated statuses; default new,seen,watching,bought; 'all' for every status",
    ),
    q: str | None = Query(None, max_length=200),
    min_score: float | None = Query(None, ge=0, le=100),
    min_profit_cents: int | None = Query(None, ge=-MAX_CENTS, le=MAX_CENTS),
    min_roi_bp: int | None = Query(None, ge=-MAX_ROI_FILTER_BP, le=MAX_ROI_FILTER_BP),
    include_suspicious: bool = Query(False, description="also return suspicious opportunities (critical flag or 2+ warnings)"),
    include_demo_data: bool | None = Query(None, alias="include_demo"),
    source: str | None = Query(None, max_length=64),
    sort: Literal["score", "profit", "roi", "recent"] = "score",
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0, le=MAX_ID),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Page[OpportunityOut]:
    statuses = split_csv_param(status_filter)
    if statuses == ["all"]:
        statuses = sorted(ALL_STATUSES)
    elif not statuses:
        statuses = list(DEFAULT_STATUSES)
    invalid = [s for s in statuses if s not in ALL_STATUSES]
    if invalid:
        raise HTTPException(422, f"invalid status: {', '.join(invalid)}")

    stmt = (
        select(Opportunity)
        .join(Listing, Listing.id == Opportunity.buy_listing_id)
        .join(Product, Product.id == Opportunity.product_id)
        .join(Source, Source.id == Listing.source_id)
        .where(Opportunity.status.in_(statuses))
    )
    if q and q.strip():
        pattern = like_pattern(q)
        stmt = stmt.where(or_(func.lower(Product.title).like(pattern, escape="\\"),
                              func.lower(Listing.title).like(pattern, escape="\\")))
    if min_score is not None:
        stmt = stmt.where(Opportunity.score >= min_score)
    if min_profit_cents is not None:
        stmt = stmt.where(Opportunity.net_profit_cents >= min_profit_cents)
    if min_roi_bp is not None:
        stmt = stmt.where(Opportunity.roi_bp >= min_roi_bp)
    if not include_suspicious:
        # Hide suspicious opportunities (critical flag or >= 2 warnings, see engine.risk.is_suspicious).
        # A single warning stays visible with risk_level="warning".
        stmt = stmt.where(Opportunity.is_suspicious.is_(False))
    if not include_demo(db, settings, include_demo_data):
        stmt = stmt.where(Opportunity.is_demo.is_(False))
    if source:
        stmt = stmt.where(Source.key == source)
    total = count(db, stmt)
    rows = db.scalars(
        stmt.options(selectinload(Opportunity.product), selectinload(Opportunity.buy_listing).selectinload(Listing.source))
        .order_by(*SORTS[sort], Opportunity.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return Page[OpportunityOut](items=[opportunity_out(o) for o in rows], total=total, limit=limit, offset=offset)


@router.get("/{opportunity_id}", response_model=OpportunityDetailOut)
def get_opportunity(opportunity_id: int, db: Session = Depends(get_db)) -> OpportunityDetailOut:
    opp = _get(db, opportunity_id)
    ids = [int(i) for i in (opp.comparable_listing_ids or [])][:500]
    comparables = []
    if ids:
        found = {
            row.id: row
            for row in db.scalars(select(Listing).options(selectinload(Listing.source)).where(Listing.id.in_(ids)))
        }
        comparables = [found[i] for i in ids if i in found]
    return opportunity_detail_out(opp, comparables)


@router.patch("/{opportunity_id}", response_model=OpportunityOut)
def update_opportunity(opportunity_id: int, body: OpportunityUpdate, db: Session = Depends(get_db)) -> OpportunityOut:
    opp = _get(db, opportunity_id)
    opp.status = body.status
    db.commit()
    return opportunity_out(opp)


@router.post("/{opportunity_id}/buy", response_model=InventoryOut, status_code=status.HTTP_201_CREATED)
def buy_opportunity(opportunity_id: int, body: OpportunityBuyIn, db: Session = Depends(get_db)) -> InventoryOut:
    item = inventory_service.create_from_opportunity(db, opportunity_id, body)
    db.commit()
    db.refresh(item)
    return inventory_service.inventory_out(item)


@router.post("/{opportunity_id}/ai-summary", response_model=AiSummaryOut)
async def ai_summary(opportunity_id: int, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> AiSummaryOut:
    opp = _get(db, opportunity_id)
    summary, validated, provider, message = await narrative.summarize(dict(opp.explanation_facts_json or {}), settings)
    if validated and summary:
        opp.ai_summary = summary
        db.commit()
    return AiSummaryOut(opportunity_id=opp.id, provider=provider, summary=summary, validated=validated, message=message)
