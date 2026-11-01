"""User-editable application settings stored in the ``app_settings`` table (JSON values).

Environment settings (:class:`flipradar.config.Settings`) provide *initial defaults*; once a value is
saved through the API/CLI the stored value wins.  Every key has a typed default so callers never get a
missing value.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..engine.opportunities import AnalysisSettings
from ..models import AppSetting
from .errors import Invalid


def _fx_default(settings: Settings) -> str | None:
    value = settings.fx_usd_mxn_manual
    if value is None:
        return None
    return str(Decimal(repr(value)))


DEFAULTS: dict[str, Callable[[Settings], Any]] = {
    "min_comparables": lambda s: int(s.min_comparables),
    "comparable_max_age_days": lambda s: int(s.comparable_max_age_days),
    "default_fee_profile_key": lambda s: "ml_clasica",
    # Resale fee profile for units bought USED (or of unknown condition): Mercado Libre charges different
    # shipping tiers and no tax withholding on used items for sellers with RFC.
    "default_fee_profile_key_used": lambda s: "ml_clasica_used",
    "target_roi_bp": lambda s: 2000,
    "min_profit_cents": lambda s: 20000,
    "min_roi_bp": lambda s: 1000,
    "fx_usd_mxn_manual": _fx_default,
    "include_demo_data": lambda s: False,
    "pricing_strategy": lambda s: "competitive",
    "unknown_shipping_assumption_cents": lambda s: None,
    # A BUY listing not seen within this many days is never an opportunity (it may be gone).
    "buy_max_age_days": lambda s: 3,
}

PRICING_STRATEGIES = ("competitive", "market")


def _settings(settings: Settings | None) -> Settings:
    return settings or get_settings()


def get_value(session: Session, key: str, settings: Settings | None = None) -> Any:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = session.get(AppSetting, key)
    if row is not None:
        return row.value_json
    return DEFAULTS[key](_settings(settings))


def all_values(session: Session, settings: Settings | None = None) -> dict[str, Any]:
    settings = _settings(settings)
    stored = {row.key: row.value_json for row in session.scalars(select(AppSetting))}
    return {key: (stored[key] if key in stored else default(settings)) for key, default in DEFAULTS.items()}


def parse_fx_rate(value: Any) -> Decimal | None:
    """Validate a USD->MXN rate given as string/number.  Empty -> None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("none", "null"):
        return None
    try:
        rate = Decimal(text)
    except InvalidOperation as exc:
        raise Invalid(f"invalid exchange rate {value!r}") from exc
    if not rate.is_finite() or rate <= 0 or rate >= 1000:
        raise Invalid("exchange rate must be a positive number below 1000")
    return rate


def _validate(key: str, value: Any) -> Any:
    if key in ("min_comparables",):
        value = int(value)
        if not 2 <= value <= 100:
            raise Invalid("min_comparables must be within 2..100")
    elif key == "comparable_max_age_days":
        value = int(value)
        if not 1 <= value <= 365:
            raise Invalid("comparable_max_age_days must be within 1..365")
    elif key == "buy_max_age_days":
        value = int(value)
        if not 1 <= value <= 90:
            raise Invalid("buy_max_age_days must be within 1..90")
    elif key in ("target_roi_bp", "min_roi_bp"):
        value = int(value)
        if not 0 <= value <= 100000:
            raise Invalid(f"{key} must be within 0..100000")
    elif key == "min_profit_cents":
        value = int(value)
        if value < 0:
            raise Invalid("min_profit_cents cannot be negative")
    elif key == "fx_usd_mxn_manual":
        rate = parse_fx_rate(value)
        value = None if rate is None else str(rate)
    elif key == "include_demo_data":
        value = bool(value)
    elif key == "pricing_strategy":
        if value not in PRICING_STRATEGIES:
            raise Invalid(f"pricing_strategy must be one of {', '.join(PRICING_STRATEGIES)}")
    elif key == "unknown_shipping_assumption_cents":
        if value is not None:
            value = int(value)
            if value < 0:
                raise Invalid("unknown_shipping_assumption_cents cannot be negative")
    elif key in ("default_fee_profile_key", "default_fee_profile_key_used"):
        value = str(value)
    return value


def set_value(session: Session, key: str, value: Any) -> Any:
    """Validate and store one value (flushed, not committed).  Returns the stored value."""
    if key not in DEFAULTS:
        raise Invalid(f"unknown setting '{key}'")
    try:
        value = _validate(key, value)
    except Invalid:
        raise
    except (TypeError, ValueError) as exc:
        raise Invalid(f"invalid value for {key}") from exc
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value_json=value))
    else:
        row.value_json = value
    session.flush()
    return value


def update(session: Session, updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        set_value(session, key, value)


def analysis_settings(session: Session, settings: Settings | None = None) -> AnalysisSettings:
    values = all_values(session, settings)
    return AnalysisSettings(
        min_comparables=int(values["min_comparables"]),
        comparable_max_age_days=int(values["comparable_max_age_days"]),
        min_profit_cents=int(values["min_profit_cents"]),
        min_roi_bp=int(values["min_roi_bp"]),
        pricing_strategy=str(values["pricing_strategy"]),
        buy_max_age_days=int(values["buy_max_age_days"]),
        include_demo=bool(values["include_demo_data"]),
        unknown_shipping_assumption_cents=(
            None
            if values["unknown_shipping_assumption_cents"] is None
            else int(values["unknown_shipping_assumption_cents"])
        ),
    )
