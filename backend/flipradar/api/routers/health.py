"""GET /health - liveness/readiness without authentication (reveals no data)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ... import __version__
from ...schemas import HealthOut
from ...services.timeutil import utcnow
from ..deps import get_db

router = APIRouter(tags=["meta"])


@router.api_route("/health", methods=["GET", "HEAD"], response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    dialect = db.get_bind().dialect.name
    try:
        db.execute(text("SELECT 1"))
        status, database = "ok", f"{dialect}: ok"
    except Exception:
        status, database = "degraded", f"{dialect}: unavailable"
    return HealthOut(status=status, version=__version__, database=database, time=utcnow())
