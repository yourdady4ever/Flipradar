"""Background scheduler (in-process asyncio task, no external queue needed).

Every ``scheduler_tick_seconds`` it:

* runs tracked searches that are due (enabled and never run or older than their interval);
* refreshes the Banxico FIX USD/MXN rate at most every 12 hours when a token is configured.

Each tick uses its own database session.  Exceptions are logged and never stop the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..config import Settings
from ..db import SessionLocal
from ..models import FxRate
from . import collection, fx
from .timeutil import as_utc, utcnow

log = logging.getLogger(__name__)

FX_REFRESH_INTERVAL = timedelta(hours=12)


async def tick(settings: Settings, *, now: datetime | None = None) -> dict[str, int]:
    """One scheduler iteration.  Returns counters (useful for tests and logs)."""
    now = as_utc(now or utcnow())
    counters = {"searches_run": 0, "fx_refreshed": 0}
    session = SessionLocal()
    try:
        if settings.banxico_token:
            try:
                last = session.scalar(select(func.max(FxRate.fetched_at)).where(FxRate.source == "banxico"))
                if last is None or as_utc(last) + FX_REFRESH_INTERVAL <= now:
                    if await fx.refresh_from_banxico(session, settings) is not None:
                        counters["fx_refreshed"] = 1
                    session.commit()
            except Exception:
                session.rollback()
                log.exception("Banxico FX refresh failed")
        try:
            results = await collection.run_due_searches(session, settings=settings, now=now)
            counters["searches_run"] = len(results)
        except Exception:
            session.rollback()
            log.exception("running tracked searches failed")
    finally:
        session.close()
    return counters


async def scheduler_loop(settings: Settings) -> None:
    interval = max(5, int(settings.scheduler_tick_seconds))
    log.info("scheduler started (tick every %s s)", interval)
    while True:
        try:
            await tick(settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - tick already catches
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)


def start(settings: Settings) -> asyncio.Task[None]:
    return asyncio.create_task(scheduler_loop(settings), name="flipradar-scheduler")


async def stop(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
