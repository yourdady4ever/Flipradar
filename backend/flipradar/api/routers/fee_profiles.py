"""Fee profiles (selling channel cost models): list, create, edit, delete custom ones."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import Settings
from ...models import FeeProfile, Source
from ...schemas import FeeProfileIn, FeeProfileOut, Message
from ...services import settings_store
from ...services.serialize import fee_profile_out
from ..deps import get_db, get_settings
from .calculator import fee_model_from_input

router = APIRouter(prefix="/fee-profiles", tags=["fee profiles"])


def _apply(row: FeeProfile, body: FeeProfileIn) -> None:
    fee_model_from_input(body)  # validates tiers and ranges (422)
    row.name = body.name
    row.channel = body.channel
    row.commission_bp = body.commission_bp
    row.fixed_fee_tiers_json = [t.model_dump() for t in body.fixed_fee_tiers]
    row.payment_fee_bp = body.payment_fee_bp
    row.payment_fixed_cents = body.payment_fixed_cents
    row.iva_on_fees = body.iva_on_fees
    row.iva_rate_bp = body.iva_rate_bp
    row.seller_shipping_cents = body.seller_shipping_cents
    row.free_shipping_threshold_cents = body.free_shipping_threshold_cents
    row.isr_withholding_bp = body.isr_withholding_bp
    row.iva_withholding_bp = body.iva_withholding_bp
    row.source_url = body.source_url
    row.as_of = body.as_of
    row.notes = body.notes


def _get(db: Session, key: str) -> FeeProfile:
    row = db.scalars(select(FeeProfile).where(FeeProfile.key == key)).first()
    if row is None:
        raise HTTPException(404, f"fee profile '{key}' not found")
    return row


@router.get("", response_model=list[FeeProfileOut])
def list_profiles(db: Session = Depends(get_db)) -> list[FeeProfileOut]:
    rows = db.scalars(select(FeeProfile).order_by(FeeProfile.is_builtin.desc(), FeeProfile.key))
    return [fee_profile_out(r) for r in rows]


@router.post("", response_model=FeeProfileOut, status_code=status.HTTP_201_CREATED)
def create_profile(body: FeeProfileIn, db: Session = Depends(get_db)) -> FeeProfileOut:
    if db.scalars(select(FeeProfile).where(FeeProfile.key == body.key)).first() is not None:
        raise HTTPException(409, f"fee profile '{body.key}' already exists")
    row = FeeProfile(key=body.key, is_builtin=False)
    _apply(row, body)
    db.add(row)
    db.commit()
    return fee_profile_out(row)


@router.put("/{key}", response_model=FeeProfileOut)
def replace_profile(key: str, body: FeeProfileIn, db: Session = Depends(get_db)) -> FeeProfileOut:
    row = _get(db, key)
    if body.key != key:
        raise HTTPException(422, "the key in the body must match the key in the URL")
    _apply(row, body)
    db.commit()
    return fee_profile_out(row)


@router.delete("/{key}", response_model=Message)
def delete_profile(key: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Message:
    row = _get(db, key)
    if row.is_builtin:
        raise HTTPException(409, "built-in fee profiles cannot be deleted (edit them instead)")
    # Both resale defaults (new/refurbished and used/unknown units) must keep pointing at a real profile;
    # otherwise analysis would silently fall back to another profile with different fees and withholdings.
    if settings_store.get_value(db, "default_fee_profile_key", settings) == key:
        raise HTTPException(409, "this profile is the default fee profile; choose another default first")
    if settings_store.get_value(db, "default_fee_profile_key_used", settings) == key:
        raise HTTPException(409, "this profile is the default fee profile for used units "
                                 "(default_fee_profile_key_used); choose another default first")
    for source in db.scalars(select(Source).where(Source.default_fee_profile_key == key)):
        source.default_fee_profile_key = None
    db.delete(row)
    db.commit()
    return Message(message=f"fee profile '{key}' deleted")
