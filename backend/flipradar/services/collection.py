"""Run collectors (search a query on one or more sources), ingest the results and recompute.

Each source gets a :class:`CollectionRun` row with status ``ok`` / ``skipped`` (disabled or not
configured) / ``error`` (CollectorError, message stored on the run and on ``Source.last_error``).
Sources are queried sequentially so per-source rate limits are respected.

SQLite has ONE write lock, so no write transaction is ever open while a collector awaits the network: the
run row is committed (status ``running``) before the collector is called, the collector itself touches no
database state, and the results are ingested and the run finished in one short transaction afterwards.
Every database step runs in a worker thread (``asyncio.to_thread``), so a step that has to wait for the
lock never blocks the event loop that other requests and the scheduler share.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..collectors import registry
from ..collectors.base import Collector, CollectorError, CollectorNotConfigured
from ..config import Settings, get_settings
from ..engine.types import RawListing
from ..models import CollectionRun, Source, TrackedSearch
from ..schemas import CollectionRunOut, CollectResult
from . import analysis, bootstrap
from .errors import NotFound
from .ingestion import IngestStats, ingest_raw_listings
from .timeutil import as_utc, utcnow

log = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 1000
NO_SOURCE_MESSAGE = (
    "no enabled source with search support is configured; add credentials for a collector (see "
    "docs/DATA_SOURCES.md) or import data with CSV"
)


def _ingest_atomic(session: Session, key: str, raws: list[RawListing], settings: Settings) -> IngestStats:
    """Ingest one source's batch inside a SAVEPOINT so a failure leaves no partial rows behind."""
    with session.begin_nested():
        return ingest_raw_listings(session, key, raws, settings=settings)


def run_out(run: CollectionRun, source_key: str | None) -> CollectionRunOut:
    return CollectionRunOut(
        id=run.id,
        source_key=source_key,
        query=run.query,
        started_at=as_utc(run.started_at),
        finished_at=as_utc(run.finished_at) if run.finished_at else None,
        status=run.status,
        items_fetched=run.items_fetched,
        items_new=run.items_new,
        items_updated=run.items_updated,
        error=run.error,
    )


def searchable_source_keys(session: Session, settings: Settings) -> list[str]:
    """Enabled sources backed by a configured collector that supports search."""
    classes = registry.collector_classes()
    keys: list[str] = []
    for source in session.scalars(select(Source).where(Source.enabled.is_(True)).order_by(Source.key)):
        cls = classes.get(source.key)
        if cls is None or "search" not in (cls.info.capabilities or []):
            continue
        try:
            collector = cls(settings, None)
            if collector.is_configured():
                keys.append(source.key)
        except Exception:
            log.warning("collector %s could not be instantiated", source.key, exc_info=True)
    return keys


def _finish(run: CollectionRun, source: Source | None, status: str, message: str | None, now: datetime) -> None:
    run.status = status
    run.error = message[:MAX_ERROR_LENGTH] if message else None
    run.finished_at = now
    if source is not None:
        source.last_run_at = now
        source.last_status = status
        source.last_error = run.error if status == "error" else None


def _open_run(session: Session, key: str, query: str, tracked_search_id: int | None) -> tuple[CollectionRun, Source | None]:
    """Create and COMMIT the run row before any network I/O (unknown/disabled sources are finished at once)."""
    source = session.scalars(select(Source).where(Source.key == key)).first()
    run = CollectionRun(
        source_id=source.id if source else None,
        tracked_search_id=tracked_search_id,
        query=query[:200],
        started_at=utcnow(),
        status="running",
    )
    session.add(run)
    if source is None:
        _finish(run, None, "error", f"unknown source '{key}'", utcnow())
    elif not source.enabled:
        _finish(run, source, "skipped", "source is disabled", utcnow())
    session.commit()
    return run, source


async def _fetch(
    key: str, query: str, limit: int, settings: Settings, client: httpx.AsyncClient | None
) -> tuple[str, Any]:
    """Network phase of one source - no database access.  Returns ``("ok", raws)`` or ``(status, message)``."""
    collector: Collector | None = None
    try:
        collector = registry.get_collector(key, settings, client)
        if not collector.is_configured():
            return "skipped", "not configured: add the credentials described in the docs"
        raws = await collector.search(query, limit=limit)
        return "ok", list(raws)[: max(1, limit)]
    except CollectorNotConfigured as exc:
        return "skipped", f"not configured: {exc}"
    except CollectorError as exc:
        return "error", str(exc) or type(exc).__name__
    except NotImplementedError:
        return "error", "collector or pipeline component not implemented yet"
    except Exception as exc:  # never let one source break the others
        log.exception("collection from %s failed", key)
        return "error", f"unexpected error: {type(exc).__name__}"
    finally:
        if collector is not None:
            try:
                await collector.aclose()
            except Exception:  # pragma: no cover - defensive
                log.warning("closing collector %s failed", key, exc_info=True)


def _close_run(
    session: Session, run: CollectionRun, source: Source | None, key: str, status: str, payload: Any,
    settings: Settings,
) -> set[int]:
    """Database phase of one source (worker thread): ingest the fetched listings and finish the run, then commit.

    Returns the product ids touched.  A refresh token rotated during the network phase is stored first,
    in its own short transaction (see ``bootstrap.persist_rotated_ml_token``).
    """
    bootstrap.persist_rotated_ml_token()
    touched: set[int] = set()
    if status != "ok":
        _finish(run, source, status, payload, utcnow())
    else:
        raws: list[RawListing] = payload
        # Make a WRITE the first statement of this transaction: it waits for the write lock (busy_timeout)
        # before ingestion reads anything.  A transaction that reads first cannot upgrade to a writer once
        # another connection has committed (SQLite WAL answers SQLITE_BUSY at once, without waiting).
        run.items_fetched = len(raws)
        session.flush()
        try:
            stats = _ingest_atomic(session, key, raws, settings)
        except NotImplementedError:
            _finish(run, source, "error", "collector or pipeline component not implemented yet", utcnow())
        except Exception as exc:  # never let one source break the others
            log.exception("ingesting results from %s failed", key)
            _finish(run, source, "error", f"unexpected error: {type(exc).__name__}", utcnow())
        else:
            run.items_new = stats.new
            run.items_updated = stats.updated
            touched = set(stats.product_ids_touched)
            message = None
            if stats.skipped:
                message = f"{stats.skipped} item(s) skipped: " + "; ".join(stats.errors[:5])
            _finish(run, source, "ok", message, utcnow())
    session.commit()
    return touched


def _abort_run(session: Session, run: CollectionRun, source: Source | None, exc: Exception) -> None:
    session.rollback()
    _finish(run, source, "error", f"could not store the results: {type(exc).__name__}", utcnow())
    try:
        session.commit()
    except Exception:
        session.rollback()
        log.warning("could not mark collection run %s as failed", run.id, exc_info=True)


def _record_no_source(session: Session, query: str, tracked_search_id: int | None) -> CollectionRun:
    run = CollectionRun(tracked_search_id=tracked_search_id, query=query[:200], started_at=utcnow(), status="running")
    session.add(run)
    _finish(run, None, "skipped", NO_SOURCE_MESSAGE, utcnow())
    session.commit()
    return run


async def run_collection(
    session: Session,
    query: str,
    source_keys: list[str] | None,
    limit: int,
    *,
    analyze: bool = True,
    settings: Settings | None = None,
    tracked_search_id: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> CollectResult:
    """Search ``query`` on ``source_keys`` (empty/None = every searchable configured source).  Commits.

    No write transaction is open while a collector awaits the network (see the module docstring).
    """
    settings = settings or get_settings()
    query = (query or "").strip()
    keys = list(dict.fromkeys(source_keys or [])) or searchable_source_keys(session, settings)
    if not keys:
        # Make "nothing happened" visible in the run history instead of silently doing nothing.
        run = await asyncio.to_thread(_record_no_source, session, query, tracked_search_id)
        return CollectResult(runs=[run_out(run, None)])

    runs: list[tuple[CollectionRun, str | None]] = []
    touched: set[int] = set()
    for key in keys:
        run, source = await asyncio.to_thread(_open_run, session, key, query, tracked_search_id)
        runs.append((run, key))
        if run.status != "running":  # unknown or disabled source: already finished and committed
            continue
        status, payload = await _fetch(key, query, limit, settings, client)
        try:
            touched |= await asyncio.to_thread(_close_run, session, run, source, key, status, payload, settings)
        except Exception as exc:  # e.g. the database stayed locked: record it, keep going with the others
            log.exception("storing the results from %s failed", key)
            await asyncio.to_thread(_abort_run, session, run, source, exc)

    result = CollectResult(runs=[run_out(r, k) for r, k in runs])
    if analyze and touched:
        recomputed = await asyncio.to_thread(analysis.recompute, session, sorted(touched), settings=settings)
        result.opportunities_created = recomputed.opportunities_created
        result.opportunities_updated = recomputed.opportunities_updated
        result.notifications_created = recomputed.notifications_created
    return result


def _mark_started(session: Session, search: TrackedSearch) -> None:
    search.last_run_at = utcnow()
    session.commit()


async def run_tracked_search(
    session: Session, search_id: int, *, settings: Settings | None = None, client: httpx.AsyncClient | None = None
) -> CollectResult:
    search = session.get(TrackedSearch, search_id)
    if search is None:
        raise NotFound(f"tracked search {search_id} not found")
    # Committed right away: never left open (holding the write lock) while the collectors run.
    await asyncio.to_thread(_mark_started, session, search)
    return await run_collection(
        session,
        search.query,
        list(search.source_keys or []),
        search.max_results,
        settings=settings,
        tracked_search_id=search.id,
        client=client,
    )


def due_searches(session: Session, now: datetime | None = None) -> list[TrackedSearch]:
    now = as_utc(now or utcnow())
    due: list[TrackedSearch] = []
    for search in session.scalars(select(TrackedSearch).where(TrackedSearch.enabled.is_(True)).order_by(TrackedSearch.id)):
        if search.last_run_at is None or as_utc(search.last_run_at) + timedelta(minutes=search.interval_minutes) <= now:
            due.append(search)
    return due


async def run_due_searches(session: Session, *, settings: Settings | None = None, now: datetime | None = None) -> list[CollectResult]:
    results: list[CollectResult] = []
    for search in due_searches(session, now):
        try:
            results.append(await run_tracked_search(session, search.id, settings=settings))
        except Exception:
            log.exception("tracked search %s failed", search.id)
            session.rollback()
    return results
