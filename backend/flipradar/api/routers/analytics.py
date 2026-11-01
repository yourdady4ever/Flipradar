"""Business analytics from recorded purchases, sales and stored opportunities."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...config import Settings
from ...schemas import AnalyticsSummaryOut, TimeseriesOut
from ...services import analytics as service
from ..deps import get_db, get_settings

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsSummaryOut)
def summary(days: int = Query(30, ge=1, le=3650), db: Session = Depends(get_db),
            settings: Settings = Depends(get_settings)) -> AnalyticsSummaryOut:
    return service.summary(db, days, settings=settings)


@router.get("/timeseries", response_model=TimeseriesOut)
def timeseries(days: int = Query(90, ge=1, le=3650), bucket: Literal["day", "week", "month"] = "week",
               db: Session = Depends(get_db)) -> TimeseriesOut:
    return service.timeseries(db, days, bucket)
