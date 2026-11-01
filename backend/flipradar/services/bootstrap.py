"""First-run / every-start seeding: built-in fee profiles and source rows.

Idempotent: existing rows keep their user-editable fields (``enabled``, ``default_fee_profile_key``).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..collectors import registry
from ..collectors.base import CollectorInfo
from ..config import Settings
from ..models import Source, SourceKind
from .fees import seed_fee_profiles

log = logging.getLogger(__name__)

# Sources that are not remote collectors.  ``capabilities`` / ``notes`` are exposed by GET /sources.
FIXED_SOURCES: list[dict[str, Any]] = [
    {
        "key": "manual",
        "name": "Manual entry",
        "kind": SourceKind.MANUAL.value,
        "capabilities": ["import"],
        "notes": "Prices you typed or imported yourself (default source of CSV rows).",
    },
    {
        "key": "csv_import",
        "name": "CSV import",
        "kind": SourceKind.MANUAL.value,
        "capabilities": ["import"],
        "notes": "Rows imported from a CSV file with source=csv_import.",
    },
    {
        "key": "web_jsonld",
        "name": "Web page (schema.org Product JSON-LD)",
        "kind": SourceKind.RETAILER.value,
        "capabilities": ["url_check"],
        "notes": "Single pages you asked FLIP RADAR to check; robots.txt is respected.",
    },
    {
        "key": "demo",
        "name": "Demo data (SYNTHETIC)",
        "kind": SourceKind.DEMO.value,
        "capabilities": [],
        "notes": "Synthetic, clearly labelled sample data. Never mixed into real analysis.",
    },
]
FIXED_SOURCE_KEYS = frozenset(s["key"] for s in FIXED_SOURCES)


def collector_infos() -> dict[str, CollectorInfo]:
    """Registered collector infos; a broken optional collector module never breaks start-up."""
    try:
        return {key: cls.info for key, cls in registry.collector_classes().items()}
    except Exception:  # pragma: no cover - defensive
        log.exception("could not load collector registry")
        return {}


def seed_sources(session: Session) -> int:
    existing = {s.key: s for s in session.scalars(select(Source))}
    inserted = 0
    for key, info in collector_infos().items():
        row = existing.get(key)
        if row is None:
            session.add(
                Source(
                    key=key,
                    name=info.name,
                    kind=info.kind,
                    country=info.country,
                    currency=info.currency,
                    enabled=True,
                    default_fee_profile_key=info.default_fee_profile_key,
                    config_json={},
                )
            )
            inserted += 1
        else:
            # Descriptive metadata follows the code; user choices (enabled / fee profile) are kept.
            row.name = info.name
            row.kind = info.kind
            row.country = info.country
            row.currency = info.currency
    for spec in FIXED_SOURCES:
        if spec["key"] in existing:
            continue
        session.add(
            Source(
                key=spec["key"],
                name=spec["name"],
                kind=spec["kind"],
                country="MX",
                currency="MXN",
                enabled=True,
                default_fee_profile_key=None,
                config_json={},
            )
        )
        inserted += 1
    session.flush()
    return inserted


def ensure_source(session: Session, key: str, *, name: str | None = None, kind: str = SourceKind.MANUAL.value) -> Source:
    """Return the Source row for ``key``, creating a MANUAL source when it does not exist."""
    row = session.scalars(select(Source).where(Source.key == key)).first()
    if row is None:
        row = Source(key=key, name=name or key, kind=kind, country="MX", currency="MXN", enabled=True, config_json={})
        session.add(row)
        session.flush()
    return row


def bootstrap(session: Session, settings: Settings | None = None) -> dict[str, int]:
    """Seed fee profiles and sources (flushed; caller commits)."""
    return {"fee_profiles": seed_fee_profiles(session), "sources": seed_sources(session)}
