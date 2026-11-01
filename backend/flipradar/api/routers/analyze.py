"""POST /api/v1/analyze/recompute - re-run risk + opportunity analysis for every product."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...config import Settings
from ...schemas import RecomputeResult
from ...services import analysis
from ..deps import get_db, get_settings

router = APIRouter(prefix="/analyze", tags=["analysis"])


@router.post("/recompute", response_model=RecomputeResult)
async def recompute(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> RecomputeResult:
    return await asyncio.to_thread(analysis.recompute, db, None, settings=settings)
