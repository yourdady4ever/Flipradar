"""Exchange rates (USD -> MXN)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...collectors.base import CollectorError
from ...config import Settings
from ...schemas import FxOut
from ...services import fx as service
from ..deps import get_db, get_settings

router = APIRouter(prefix="/fx", tags=["fx"])


@router.get("/latest", response_model=FxOut)
def latest(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> FxOut:
    found = service.latest_usd_mxn(db, settings)
    if found is None:
        raise HTTPException(404, "no USD/MXN rate available (configure Banxico or set a manual rate)")
    rate, source, rate_date = found
    return FxOut(base="USD", quote="MXN", rate=str(rate), rate_date=rate_date, source=source,
                 attribution=service.ATTRIBUTIONS.get(source))


@router.post("/refresh", response_model=FxOut)
async def refresh(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> FxOut:
    """Fetch the latest USD/MXN rate now: Banxico FIX (with FLIPRADAR_BANXICO_TOKEN), else key-less public
    sources labelled as estimates."""
    if not service.fx_refresh_enabled(settings):
        raise HTTPException(409, "No FX source enabled (set FLIPRADAR_BANXICO_TOKEN or enable public sources)")
    try:
        row = await service.refresh_usd_mxn(db, settings)
    except CollectorError as exc:
        raise HTTPException(502, f"FX request failed: {str(exc)[:300]}") from exc
    db.commit()
    return FxOut(base=row.base, quote=row.quote, rate=row.rate, rate_date=row.rate_date, source=row.source,
                 attribution=service.ATTRIBUTIONS.get(row.source))
