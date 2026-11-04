"""Deterministic profit calculator and import-duty estimator."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...config import Settings
from ...finance.calculator import (
    FeeModel,
    FixedFeeTier,
    ProfitInputs,
    break_even_sale_price,
    calculate_profit,
    max_buy_price_for_roi,
)
from ...finance.import_costs import estimate_import_costs, rule_keys
from ...schemas import CalculatorIn, CalculatorOut, FeeProfileIn, ImportCostIn, ImportCostOut, ProfitResultOut
from ...services import fx as fx_service
from ...services import settings_store
from ...services.fees import get_profile
from ...services.settings_store import parse_fx_rate
from ..deps import get_db, get_settings

router = APIRouter(prefix="/calculator", tags=["calculator"])


def fee_model_from_input(profile: FeeProfileIn) -> FeeModel:
    """FeeModel from an inline profile; raises 422 on inconsistent tiers."""
    tiers = [FixedFeeTier(max_price_cents=t.max_price_cents, fee_cents=t.fee_cents) for t in profile.fixed_fee_tiers]
    if any(t.max_price_cents is None for t in tiers[:-1]):
        raise HTTPException(422, "only the last fixed-fee tier may have no max_price_cents")
    try:
        return FeeModel(
            key=profile.key,
            name=profile.name,
            commission_bp=profile.commission_bp,
            fixed_fee_tiers=tuple(tiers),
            payment_fee_bp=profile.payment_fee_bp,
            payment_fixed_cents=profile.payment_fixed_cents,
            iva_on_fees=profile.iva_on_fees,
            iva_rate_bp=profile.iva_rate_bp,
            seller_shipping_cents=profile.seller_shipping_cents,
            free_shipping_threshold_cents=profile.free_shipping_threshold_cents,
            isr_withholding_bp=profile.isr_withholding_bp,
            iva_withholding_bp=profile.iva_withholding_bp,
            source_url=profile.source_url,
            as_of=profile.as_of,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def resolve_fee_model(db: Session, settings: Settings, key: str | None, inline: FeeProfileIn | None) -> FeeModel:
    if inline is not None:
        return fee_model_from_input(inline)
    key = key or str(settings_store.get_value(db, "default_fee_profile_key", settings))
    row = get_profile(db, key)
    if row is None:
        raise HTTPException(404, f"fee profile '{key}' not found")
    return FeeModel.from_profile(row)


@router.post("/profit", response_model=CalculatorOut)
def profit(body: CalculatorIn, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CalculatorOut:
    fee = resolve_fee_model(db, settings, body.fee_profile_key, body.fee_profile)
    inputs = ProfitInputs(
        buy_price_cents=body.buy_price_cents,
        sale_price_cents=body.sale_price_cents,
        fee=fee,
        buy_shipping_cents=body.buy_shipping_cents,
        import_costs_cents=body.import_costs_cents,
        other_buy_costs_cents=body.other_buy_costs_cents,
        other_sale_costs_cents=body.other_sale_costs_cents,
        seller_shipping_override_cents=body.seller_shipping_override_cents,
        sale_price_includes_iva=body.sale_price_includes_iva,
        treat_withholdings_as_cost=body.treat_withholdings_as_cost,
    )
    result = calculate_profit(inputs)
    target = body.target_roi_bp
    if target is None:
        target = int(settings_store.get_value(db, "target_roi_bp", settings))
    return CalculatorOut(
        result=ProfitResultOut.model_validate(result.to_dict()),
        break_even_sale_cents=break_even_sale_price(inputs),
        max_buy_for_target_cents=max_buy_price_for_roi(inputs, target),
        target_roi_bp=target,
    )


@router.post("/import-costs", response_model=ImportCostOut)
def import_costs(body: ImportCostIn, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ImportCostOut:
    """Courier-regime import duty estimate.  ``formal_import_required`` is true when the customs value is above
    the courier-regime limit: a formal import (customs broker, pedimento) is needed, and ``duty_rate_bp`` /
    ``duties_mxn_cents`` are null because the duties are not estimated (never a guessed 0)."""
    if body.rule_key not in rule_keys():
        raise HTTPException(422, f"unknown rule_key '{body.rule_key}' (available: {', '.join(rule_keys())})")
    if body.usd_mxn_rate:
        try:
            rate = parse_fx_rate(body.usd_mxn_rate)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        source = "provided"
    else:
        rate = None
        source = ""
    if rate is None:
        latest = fx_service.latest_usd_mxn(db, settings)
        if latest is None:
            raise HTTPException(
                422, "no USD/MXN rate available: pass usd_mxn_rate, configure Banxico or set a manual rate"
            )
        rate, source = latest[0], f"{latest[1]} ({latest[2]})"
    data = estimate_import_costs(body.item_value_usd_cents, body.shipping_usd_cents, body.rule_key, Decimal(rate),
                                 fx_source=source)
    return ImportCostOut(**data)
