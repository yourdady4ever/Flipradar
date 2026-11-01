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
    return FxOut(base="USD", quote="MXN", rate=str(rate), rate_date=rate_date, source=source)


@router.post("/refresh", response_model=FxOut)
async def refresh(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> FxOut:
    """Fetch the latest Banxico FIX rate now (requires FLIPRADAR_BANXICO_TOKEN)."""
    if not settings.banxico_token:
        raise HTTPException(409, "Banxico is not configured (set FLIPRADAR_BANXICO_TOKEN)")
    try:
        row = await service.refresh_from_banxico(db, settings)
    except NotImplementedError as exc:
        raise HTTPException(503, "Banxico connector not implemented yet") from exc
    except CollectorError as exc:
        raise HTTPException(502, f"Banxico request failed: {str(exc)[:300]}") from exc
    db.commit()
    if row is None:  # pragma: no cover - token checked above
        raise HTTPException(409, "Banxico is not configured")
    return FxOut(base=row.base, quote=row.quote, rate=row.rate, rate_date=row.rate_date, source=row.source)
