"""Shared query helpers for routers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from ...config import Settings
from ...services import settings_store


def like_pattern(text: str) -> str:
    """Escape LIKE wildcards in user input and wrap with %...% (use with escape='\\\\')."""
    escaped = text.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def count(db: Session, stmt: Select[Any]) -> int:
    return int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)


def include_demo(db: Session, settings: Settings, requested: bool | None) -> bool:
    if requested is not None:
        return requested
    return bool(settings_store.get_value(db, "include_demo_data", settings))


def split_csv_param(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]
