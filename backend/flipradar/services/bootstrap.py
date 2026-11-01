"""First-run / every-start seeding: added columns, built-in fee profiles and source rows.

Idempotent: existing rows keep their user-editable fields (``enabled``, ``default_fee_profile_key``).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from sqlalchemy import inspect, select, text
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


#: Columns added to existing tables after the first release: (table, column, one-off backfill SQL or None).
#: ``create_all()`` only creates missing TABLES, so :func:`ensure_added_columns` adds these (nullable, no
#: default) to databases created earlier.  Items written off before ``written_off_at`` existed get their
#: last-modified time, the best date available, so their loss is still booked.
ADDED_COLUMNS: tuple[tuple[str, str, str | None], ...] = (
    ("inventory_items", "written_off_at",
     "UPDATE inventory_items SET written_off_at = updated_at WHERE status = 'written_off'"),
)


def ensure_added_columns(session: Session) -> list[str]:
    """``ALTER TABLE ... ADD COLUMN`` for every :data:`ADDED_COLUMNS` entry the database lacks.

    Returns the added ``table.column`` names.  Names come from the constant above, never from input.
    """
    from ..db import Base

    connection = session.connection()
    inspector = inspect(connection)
    added: list[str] = []
    for table_name, column_name, backfill in ADDED_COLUMNS:
        if not inspector.has_table(table_name):
            continue  # create_all() creates it with every column
        if column_name in {c["name"] for c in inspector.get_columns(table_name)}:
            continue
        column = Base.metadata.tables[table_name].c[column_name]
        ddl_type = column.type.compile(dialect=connection.dialect)
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_type}"))
        if backfill:
            connection.execute(text(backfill))
        added.append(f"{table_name}.{column_name}")
        log.info("added column %s.%s to the existing database", table_name, column_name)
    return added


def bootstrap(session: Session, settings: Settings | None = None) -> dict[str, int]:
    """Add new columns, seed fee profiles and sources and restore rotated API tokens (flushed; caller commits)."""
    ensure_added_columns(session)
    counts = {"fee_profiles": seed_fee_profiles(session), "sources": seed_sources(session)}
    if settings is not None:
        install_ml_token_persistence(session, settings)
    return counts


# ------------------------------------------------------------------------------------------------
# Mercado Libre refresh-token rotation
# ------------------------------------------------------------------------------------------------

ML_TOKEN_KEY = "secret.ml_refresh_token"  # noqa: S105 - a settings key name, not a secret


def _fingerprint(token: str | None) -> str | None:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest() if token else None


# The listener runs synchronously on the event loop (inside the collector's token refresh), so it must never
# touch the database itself: a sync SQLite write there waits busy_timeout for a lock that a coroutine on the
# same (now blocked) loop may hold, and then fails.  It only records the token; the write happens in a short,
# independent transaction off the loop (a worker thread), and run_collection persists it again right after
# every collector returns, so a write that failed (busy database) is retried.
_ml_seed: str | None = None  # fingerprint of the configured FLIPRADAR_ML_REFRESH_TOKEN
_ml_pending: tuple[str | None, str] | None = None  # (seed fingerprint, newest rotated token) not yet stored
_ml_pending_lock = threading.Lock()  # guards the variables above; never held during I/O
_ml_write_lock = threading.Lock()  # serialises the database writes (newest token always written last)
_ml_background: set[asyncio.Future[bool]] = set()


def _record_rotated_ml_token(new_token: str) -> None:
    """Refresh-token listener: remember the token and persist it without blocking the caller's event loop."""
    global _ml_pending
    with _ml_pending_lock:
        _ml_pending = (_ml_seed, new_token)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no event loop to block (synchronous caller): write right away
        persist_rotated_ml_token()
        return
    future = loop.run_in_executor(None, persist_rotated_ml_token)
    _ml_background.add(future)
    future.add_done_callback(_ml_background.discard)


def persist_rotated_ml_token() -> bool:
    """Store the newest rotated Mercado Libre refresh token in its own short transaction.  Never raises.

    Call it from a worker thread when an event loop is running.  Returns True when nothing is left pending;
    on failure (e.g. the database stayed locked) the token stays pending and the next call retries.
    """
    global _ml_pending
    from ..db import session_scope
    from ..models import AppSetting

    with _ml_write_lock:
        with _ml_pending_lock:
            pending = _ml_pending
        if pending is None:
            return True
        seed, token = pending
        try:
            with session_scope() as s:
                existing = s.get(AppSetting, ML_TOKEN_KEY)
                value = {"seed": seed, "token": token}
                if existing is None:
                    s.add(AppSetting(key=ML_TOKEN_KEY, value_json=value))
                else:
                    existing.value_json = value
        except Exception:
            log.warning("could not store the rotated Mercado Libre refresh token yet; will retry", exc_info=True)
            return False
        with _ml_pending_lock:
            if _ml_pending == pending:
                _ml_pending = None
            return _ml_pending is None


def _reset_ml_token_state() -> None:
    """Forget the seed and any pending token (tests)."""
    global _ml_seed, _ml_pending
    with _ml_pending_lock:
        _ml_seed, _ml_pending = None, None


def install_ml_token_persistence(session: Session, settings: Settings) -> None:
    """Persist Mercado Libre's single-use refresh tokens across restarts.

    Mercado Libre issues a NEW refresh token on every refresh and invalidates the old one, so the value
    in ``FLIPRADAR_ML_REFRESH_TOKEN`` stops working after the first refresh.  The rotated token is stored
    in ``app_settings`` (never returned by any API) together with a fingerprint of the configured seed
    token: if the operator later configures a *different* token (re-authorisation), the new one wins.
    Installing again (e.g. a second app instance in the same process) replaces the seed; the listener is
    registered once.
    """
    global _ml_seed
    from ..collectors import mercadolibre
    from ..models import AppSetting

    seed = _fingerprint(settings.ml_refresh_token)
    row = session.get(AppSetting, ML_TOKEN_KEY)
    if seed and row is not None and isinstance(row.value_json, dict) and row.value_json.get("seed") == seed:
        stored = row.value_json.get("token")
        if stored:
            settings.ml_refresh_token = stored

    with _ml_pending_lock:
        _ml_seed = seed
    if seed:
        mercadolibre.add_refresh_token_listener(_record_rotated_ml_token)
    else:
        mercadolibre.remove_refresh_token_listener(_record_rotated_ml_token)
