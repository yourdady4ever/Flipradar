"""POST /api/v1/import/csv - import listings/prices from a CSV file (multipart field ``file``)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ...config import Settings
from ...schemas import ImportResult
from ...services import imports as service
from ..deps import get_db, get_settings

router = APIRouter(prefix="/import", tags=["import"])


@router.post("/csv", response_model=ImportResult)
async def import_csv(
    file: UploadFile = File(...),
    source: str = Query("manual", pattern=r"^[a-z0-9_]{2,64}$", description="default source for rows without one"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ImportResult:
    data = await file.read(service.MAX_BYTES + 1)
    await file.close()
    if len(data) > service.MAX_BYTES:
        raise HTTPException(413, f"file is larger than {service.MAX_BYTES // (1024 * 1024)} MB")
    try:
        return await asyncio.to_thread(service.import_csv, db, data, default_source=source, settings=settings)
    except service.CsvTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except service.CsvImportError as exc:
        raise HTTPException(422, str(exc)) from exc
