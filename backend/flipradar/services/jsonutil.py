"""Make engine output JSON-safe before it goes into JSON columns (Decimal/datetime/dataclass/set)."""

from __future__ import annotations

import dataclasses
import enum
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def jsonable(value: Any) -> Any:
    """Recursively convert to JSON-serialisable builtins (Decimal -> str to stay exact)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, enum.Enum):
        return jsonable(value.value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        items = [jsonable(v) for v in value]
        return sorted(items, key=repr) if isinstance(value, set | frozenset) else items
    return str(value)


def truncate_json(data: dict[str, Any] | None, *, max_bytes: int = 20_000, max_field_bytes: int = 4_000) -> dict[str, Any]:
    """Keep a raw payload for auditability without letting it grow unbounded.

    Fields whose serialised size exceeds ``max_field_bytes`` are replaced by a marker; fields are kept in
    order until the whole object would exceed ``max_bytes``.  The result always serialises to
    ``<= max_bytes`` bytes of JSON.
    """
    if not data:
        return {}
    out: dict[str, Any] = {}
    budget = max_bytes - 64  # room for the truncation marker
    used = 2
    truncated = False
    for key, value in data.items():
        safe = jsonable(value)
        encoded = json.dumps({str(key): safe}, ensure_ascii=False, separators=(",", ":"))
        size = len(encoded.encode("utf-8"))
        if size > max_field_bytes:
            safe = f"<dropped: {size} bytes>"
            encoded = json.dumps({str(key)[:200]: safe}, ensure_ascii=False, separators=(",", ":"))
            size = len(encoded.encode("utf-8"))
            truncated = True
        if used + size > budget:
            truncated = True
            break
        out[str(key)[:200]] = safe
        used += size
    if truncated:
        out["_truncated"] = True
    return out
