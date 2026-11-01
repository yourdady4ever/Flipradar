"""Run collectors (search a query on one or more sources), ingest the results and recompute.

Each source gets a :class:`CollectionRun` row with status ``ok`` / ``skipped`` (disabled or not
configured) / ``error`` (CollectorError, message stored on the run and on ``Source.last_error``).
Sources are queried sequentially so per-source rate limits are respected.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..collectors import registry
from ..collectors.base import Collector, CollectorError, CollectorNotConfigured
from ..config import Settings, get_settings
from ..models import CollectionRun, Source, TrackedSearch
from ..schemas import CollectionRunOut, CollectResult
from . import analysis
from .errors import NotFound
from .ingestion import ingest_raw_listings
from .timeutil import as_utc, utcnow

log = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 1000


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
    """Search ``query`` on ``source_keys`` (empty/None = every searchable configured source).  Commits."""
    settings = settings or get_settings()
    query = (query or "").strip()
    keys = list(dict.fromkeys(source_keys or [])) or searchable_source_keys(session, settings)
    runs: list[tuple[CollectionRun, str | None]] = []
    touched: set[int] = set()

    for key in keys:
        source = session.scalars(select(Source).where(Source.key == key)).first()
        run = CollectionRun(
            source_id=source.id if source else None,
            tracked_search_id=tracked_search_id,
            query=query[:200],
            started_at=utcnow(),
            status="running",
        )
        session.add(run)
        session.flush()
        runs.append((run, key))
        if source is None:
            _finish(run, None, "error", f"unknown source '{key}'", utcnow())
            continue
        if not source.enabled:
            _finish(run, source, "skipped", "source is disabled", utcnow())
            continue
        collector: Collector | None = None
        try:
            collector = registry.get_collector(key, settings, client)
            if not collector.is_configured():
                _finish(run, source, "skipped", "not configured: add the credentials described in the docs", utcnow())
                continue
            raws = await collector.search(query, limit=limit)
            raws = list(raws)[: max(1, limit)]
            stats = await asyncio.to_thread(ingest_raw_listings, session, key, raws, settings=settings)
            run.items_fetched = len(raws)
            run.items_new = stats.new
            run.items_updated = stats.updated
            touched |= stats.product_ids_touched
            message = None
            if stats.skipped:
                message = f"{stats.skipped} item(s) skipped: " + "; ".join(stats.errors[:5])
            _finish(run, source, "ok", message, utcnow())
        except CollectorNotConfigured as exc:
            _finish(run, source, "skipped", f"not configured: {exc}", utcnow())
        except CollectorError as exc:
            _finish(run, source, "error", str(exc) or type(exc).__name__, utcnow())
        except NotImplementedError:
            _finish(run, source, "error", "collector or pipeline component not implemented yet", utcnow())
        except Exception as exc:  # never let one source break the others
            log.exception("collection from %s failed", key)
            _finish(run, source, "error", f"unexpected error: {type(exc).__name__}", utcnow())
        finally:
            if collector is not None:
                try:
                    await collector.aclose()
                except Exception:  # pragma: no cover - defensive
                    log.warning("closing collector %s failed", key, exc_info=True)
        session.flush()
    session.commit()

    result = CollectResult(runs=[run_out(r, k) for r, k in runs])
    if analyze and touched:
        recomputed = await asyncio.to_thread(analysis.recompute, session, sorted(touched), settings=settings)
        result.opportunities_created = recomputed.opportunities_created
        result.opportunities_updated = recomputed.opportunities_updated
        result.notifications_created = recomputed.notifications_created
    return result


async def run_tracked_search(
    session: Session, search_id: int, *, settings: Settings | None = None, client: httpx.AsyncClient | None = None
) -> CollectResult:
    search = session.get(TrackedSearch, search_id)
    if search is None:
        raise NotFound(f"tracked search {search_id} not found")
    search.last_run_at = utcnow()
    session.flush()
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
