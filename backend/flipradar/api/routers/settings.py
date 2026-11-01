"""User-editable analysis settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...config import Settings
from ...schemas import SettingsOut, SettingsUpdate
from ...services import settings_store
from ...services.fees import get_profile
from ..deps import get_db, get_settings

router = APIRouter(prefix="/settings", tags=["settings"])


def settings_out(db: Session, settings: Settings) -> SettingsOut:
    values = settings_store.all_values(db, settings)
    fx = values["fx_usd_mxn_manual"]
    return SettingsOut(
        min_comparables=int(values["min_comparables"]),
        comparable_max_age_days=int(values["comparable_max_age_days"]),
        default_fee_profile_key=str(values["default_fee_profile_key"]),
        target_roi_bp=int(values["target_roi_bp"]),
        min_profit_cents=int(values["min_profit_cents"]),
        fx_usd_mxn_manual=None if fx is None else str(fx),
        include_demo_data=bool(values["include_demo_data"]),
        ai_provider=settings.llm_provider,
        scheduler_enabled=settings.scheduler_enabled,
    )


@router.get("", response_model=SettingsOut)
def get_app_settings(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> SettingsOut:
    return settings_out(db, settings)


@router.put("", response_model=SettingsOut)
def update_app_settings(body: SettingsUpdate, db: Session = Depends(get_db),
                        settings: Settings = Depends(get_settings)) -> SettingsOut:
    """Only fields that are present and not null change.  Send ``fx_usd_mxn_manual: ""`` to clear it."""
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    key = changes.get("default_fee_profile_key")
    if key is not None and get_profile(db, key) is None:
        raise HTTPException(422, f"unknown fee profile '{key}'")
    settings_store.update(db, changes)  # raises Invalid -> 422
    db.commit()
    return settings_out(db, settings)
