"""UTC helpers.

SQLite has no native timezone-aware timestamp type: SQLAlchemy stores ``DateTime(timezone=True)`` values
as naive ISO strings and returns *naive* datetimes.  FLIP RADAR always writes UTC, so a naive value read
back from the database is UTC.  This module

* normalises any datetime to an aware UTC datetime (:func:`as_utc`), and
* installs an ORM ``load``/``refresh`` listener that re-attaches ``tzinfo=UTC`` to naive values of
  ``DateTime`` columns, so API responses always carry an explicit ``+00:00`` offset and comparisons with
  aware datetimes never raise ``TypeError``.

(Recommended long-term: a ``UTCDateTime`` TypeDecorator in ``flipradar.models``; this listener is a
drop-in until then and becomes a no-op once values are already aware.)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, event
from sqlalchemy.orm import Mapper
from sqlalchemy.orm.attributes import set_committed_value

from ..db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Aware UTC datetime (naive values are assumed to already be UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def as_utc_opt(value: datetime | None) -> datetime | None:
    return None if value is None else as_utc(value)


_DATETIME_ATTRS: dict[type, tuple[str, ...]] = {}


def _datetime_attrs(mapper: Mapper[Any]) -> tuple[str, ...]:
    cls = mapper.class_
    cached = _DATETIME_ATTRS.get(cls)
    if cached is None:
        cached = tuple(
            prop.key
            for prop in mapper.column_attrs
            if any(isinstance(col.type, DateTime) for col in prop.columns)
        )
        _DATETIME_ATTRS[cls] = cached
    return cached


def _attach_utc(target: Any, attrs: tuple[str, ...] | None = None) -> None:
    state_dict = target.__dict__
    mapper = target.__mapper__
    for key in _datetime_attrs(mapper):
        if attrs is not None and key not in attrs:
            continue
        value = state_dict.get(key)
        if isinstance(value, datetime) and value.tzinfo is None:
            set_committed_value(target, key, value.replace(tzinfo=UTC))


def _on_load(target: Any, _context: Any) -> None:
    _attach_utc(target)


def _on_refresh(target: Any, _context: Any, attrs: Any) -> None:
    _attach_utc(target, tuple(attrs) if attrs else None)


def install_utc_listeners() -> None:
    """Idempotently register the listeners on the declarative Base (all mapped classes)."""
    if not event.contains(Base, "load", _on_load):
        event.listen(Base, "load", _on_load, propagate=True)
    if not event.contains(Base, "refresh", _on_refresh):
        event.listen(Base, "refresh", _on_refresh, propagate=True)


install_utc_listeners()
