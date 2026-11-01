"""Data sources: list, enable/disable, choose resale fee profile, test connectivity."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...collectors import registry
from ...collectors.base import Collector
from ...config import Settings
from ...models import FeeProfile, Source
from ...schemas import SourceOut, SourceTestOut, SourceUpdate
from ...services.bootstrap import FIXED_SOURCES
from ...services.serialize import source_out
from ..deps import get_db, get_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/sources", tags=["sources"])
_FIXED = {s["key"]: s for s in FIXED_SOURCES}


def _configured(cls: type[Collector] | None, settings: Settings) -> bool:
    if cls is None:
        return True  # manual / csv / url-check / demo need no credentials
    try:
        return bool(cls(settings, None).is_configured())
    except Exception:
        log.warning("collector %s is_configured() failed", cls, exc_info=True)
        return False


def _source_or_404(db: Session, key: str) -> Source:
    row = db.scalars(select(Source).where(Source.key == key)).first()
    if row is None:
        raise HTTPException(404, f"source '{key}' not found")
    return row


def _out(row: Source, settings: Settings) -> SourceOut:
    classes = registry.collector_classes()
    cls = classes.get(row.key)
    return source_out(row, cls.info if cls else None, _configured(cls, settings), _FIXED.get(row.key))


@router.get("", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> list[SourceOut]:
    return [_out(row, settings) for row in db.scalars(select(Source).order_by(Source.key))]


@router.patch("/{key}", response_model=SourceOut)
def update_source(key: str, body: SourceUpdate, db: Session = Depends(get_db),
                  settings: Settings = Depends(get_settings)) -> SourceOut:
    row = _source_or_404(db, key)
    changes = body.model_dump(exclude_unset=True)
    if "enabled" in changes and changes["enabled"] is not None:
        row.enabled = changes["enabled"]
    if "default_fee_profile_key" in changes:
        fee_key = changes["default_fee_profile_key"]
        if fee_key is not None and db.scalars(select(FeeProfile).where(FeeProfile.key == fee_key)).first() is None:
            raise HTTPException(422, f"unknown fee profile '{fee_key}'")
        row.default_fee_profile_key = fee_key
    db.commit()
    return _out(row, settings)


@router.post("/{key}/test", response_model=SourceTestOut)
async def test_source(key: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> SourceTestOut:
    _source_or_404(db, key)
    cls = registry.collector_classes().get(key)
    if cls is None:
        return SourceTestOut(key=key, configured=True, ok=None, message="local source: nothing to test")
    collector = cls(settings, None)
    try:
        status = await collector.health()
        return SourceTestOut(key=key, configured=status.configured, ok=status.ok, message=status.message,
                             details=status.details or {})
    except NotImplementedError:
        return SourceTestOut(key=key, configured=False, ok=False, message="collector not implemented yet")
    except Exception as exc:
        log.warning("health check for %s failed", key, exc_info=True)
        return SourceTestOut(key=key, configured=_configured(cls, settings), ok=False,
                             message=f"health check failed: {type(exc).__name__}")
    finally:
        await collector.aclose()
