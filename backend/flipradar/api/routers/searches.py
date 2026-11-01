"""Tracked searches: queries the scheduler runs periodically."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import Settings
from ...models import Source, TrackedSearch
from ...schemas import CollectResult, Message, TrackedSearchIn, TrackedSearchOut, TrackedSearchUpdate
from ...services import collection
from ...services.timeutil import as_utc, as_utc_opt
from ..deps import get_db, get_settings

router = APIRouter(prefix="/searches", tags=["searches"])


def _out(row: TrackedSearch) -> TrackedSearchOut:
    out = TrackedSearchOut.model_validate(row)
    return out.model_copy(update={"last_run_at": as_utc_opt(row.last_run_at), "created_at": as_utc(row.created_at)})


def _check_sources(db: Session, keys: list[str]) -> list[str]:
    keys = list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))
    if keys:
        known = set(db.scalars(select(Source.key).where(Source.key.in_(keys))))
        unknown = [k for k in keys if k not in known]
        if unknown:
            raise HTTPException(422, f"unknown source(s): {', '.join(unknown)}")
    return keys


def _get(db: Session, search_id: int) -> TrackedSearch:
    row = db.get(TrackedSearch, search_id)
    if row is None:
        raise HTTPException(404, f"tracked search {search_id} not found")
    return row


@router.get("", response_model=list[TrackedSearchOut])
def list_searches(db: Session = Depends(get_db)) -> list[TrackedSearchOut]:
    return [_out(r) for r in db.scalars(select(TrackedSearch).order_by(TrackedSearch.id))]


@router.post("", response_model=TrackedSearchOut, status_code=status.HTTP_201_CREATED)
def create_search(body: TrackedSearchIn, db: Session = Depends(get_db)) -> TrackedSearchOut:
    row = TrackedSearch(
        query=body.query.strip(),
        category_hint=body.category_hint,
        source_keys=_check_sources(db, body.source_keys),
        max_results=body.max_results,
        interval_minutes=body.interval_minutes,
        enabled=body.enabled,
    )
    db.add(row)
    db.commit()
    return _out(row)


@router.patch("/{search_id}", response_model=TrackedSearchOut)
def update_search(search_id: int, body: TrackedSearchUpdate, db: Session = Depends(get_db)) -> TrackedSearchOut:
    row = _get(db, search_id)
    changes = body.model_dump(exclude_unset=True)
    for name, value in changes.items():
        if value is None and name != "category_hint":
            continue
        if name == "source_keys":
            value = _check_sources(db, value)
        elif name == "query":
            value = value.strip()
        setattr(row, name, value)
    db.commit()
    return _out(row)


@router.delete("/{search_id}", response_model=Message)
def delete_search(search_id: int, db: Session = Depends(get_db)) -> Message:
    row = _get(db, search_id)
    db.delete(row)
    db.commit()
    return Message(message=f"tracked search {search_id} deleted")


@router.post("/{search_id}/run", response_model=CollectResult)
async def run_search(search_id: int, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CollectResult:
    _get(db, search_id)
    return await collection.run_tracked_search(db, search_id, settings=settings)
