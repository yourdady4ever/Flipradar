"""USD/MXN exchange rates: newest stored rate (Banxico FIX preferred, key-less public sources as labelled
estimates), manual fallback second, never invented.

Stored rates older than :data:`MAX_FX_AGE_DAYS` are ignored (a frozen or stale feed must not silently
convert prices).  When no rate is available, foreign-currency listings keep ``price_mxn_cents = None`` and
are therefore excluded from price comparisons (rather than converted with a guessed rate).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..collectors import banxico, fx_sources
from ..collectors.base import CollectorError
from ..config import Settings
from ..models import FxRate
from . import settings_store

USD = "USD"
MXN = "MXN"
#: Rates older than this (calendar days; covers weekends and bank holidays) are not used.
MAX_FX_AGE_DAYS = 7
#: Attribution that must be shown wherever a rate from that source is displayed.
ATTRIBUTIONS = {
    fx_sources.SOURCE_EXCHANGERATE_API: fx_sources.EXCHANGERATE_API_ATTRIBUTION,
    fx_sources.SOURCE_FRANKFURTER: fx_sources.FRANKFURTER_ATTRIBUTION,
}


def _is_fresh(rate_date: str, today: date) -> bool:
    try:
        when = date.fromisoformat(rate_date[:10])
    except ValueError:
        return False
    return today - when <= timedelta(days=MAX_FX_AGE_DAYS)


def _decimal(text: str) -> Decimal | None:
    try:
        value = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() and value > 0 else None


def latest_usd_mxn(
    session: Session, settings: Settings | None = None, *, today: date | None = None
) -> tuple[Decimal, str, str] | None:
    """(rate, source, rate_date) of the newest FRESH stored USD->MXN rate, else the manual rate, else None.

    On the same date an official rate (Banxico) wins over public estimates.
    """
    today = today or datetime.now(UTC).date()
    rows = session.scalars(
        select(FxRate)
        .where(FxRate.base == USD, FxRate.quote == MXN)
        .order_by(FxRate.rate_date.desc(), FxRate.fetched_at.desc(), FxRate.id.desc())
        .limit(20)
    ).all()
    rows = sorted(rows, key=lambda r: (r.rate_date, r.source.startswith("banxico")), reverse=True)
    for row in rows:
        rate = _decimal(row.rate)
        if rate is not None and _is_fresh(row.rate_date, today):
            return rate, row.source, row.rate_date
    manual = settings_store.get_value(session, "fx_usd_mxn_manual", settings)
    rate = _decimal(str(manual)) if manual is not None else None
    if rate is not None:
        return rate, "manual", datetime.now(UTC).date().isoformat()
    return None


def store_rate(session: Session, base: str, quote: str, rate_date: str, rate: Decimal, source: str) -> FxRate:
    """Insert or update the rate for (base, quote, rate_date, source).  Flushed, not committed."""
    if not rate.is_finite() or rate <= 0:
        raise ValueError("exchange rate must be positive")
    row = session.scalars(
        select(FxRate).where(
            FxRate.base == base, FxRate.quote == quote, FxRate.rate_date == rate_date, FxRate.source == source
        )
    ).first()
    if row is None:
        row = FxRate(base=base, quote=quote, rate_date=rate_date, rate=str(rate), source=source)
        session.add(row)
    else:
        row.rate = str(rate)
        row.fetched_at = datetime.now(UTC)
    session.flush()
    return row


async def refresh_from_banxico(
    session: Session, settings: Settings, client: httpx.AsyncClient | None = None
) -> FxRate | None:
    """Fetch the latest FIX rate from Banxico and store it.  Returns None when no token is configured.

    Raises :class:`flipradar.collectors.base.CollectorError` on HTTP/parse failures.
    """
    if not settings.banxico_token:
        return None
    rate_date, rate = await banxico.fetch_usd_mxn_fix(settings, client)
    return store_rate(session, USD, MXN, rate_date, Decimal(rate), "banxico")


async def refresh_usd_mxn(
    session: Session, settings: Settings, client: httpx.AsyncClient | None = None, *, today: date | None = None
) -> FxRate:
    """Fetch the best available USD/MXN rate (Banxico FIX -> Frankfurter -> ExchangeRate-API) and store it.

    Raises :class:`CollectorError` when every source fails or the returned rate is stale.
    """
    quote = await fx_sources.get_usd_mxn(settings, client)
    today = today or datetime.now(UTC).date()
    if not _is_fresh(quote.rate_date, today):
        raise CollectorError(
            f"{quote.source} returned a stale USD/MXN rate dated {quote.rate_date} (older than {MAX_FX_AGE_DAYS} days)"
        )
    return store_rate(session, USD, MXN, quote.rate_date, Decimal(quote.rate), quote.source)


def fx_refresh_enabled(settings: Settings) -> bool:
    """True when at least one FX source can be queried (Banxico token or public fallbacks enabled)."""
    return bool((settings.banxico_token or "").strip()) or bool(getattr(settings, "fx_public_sources_enabled", True))


def convert_to_mxn_cents(amount_cents: int | None, currency: str | None, rate: Decimal | None) -> int | None:
    """Convert centavos of ``currency`` to MXN centavos (half-up).  None when not convertible."""
    if amount_cents is None:
        return None
    cur = (currency or MXN).upper()
    if cur == MXN:
        return int(amount_cents)
    if rate is None:
        return None
    return int((Decimal(amount_cents) * rate).to_integral_value(rounding=ROUND_HALF_UP))


def rate_for_currency(currency: str | None, usd_rate: tuple[Decimal, str, str] | None) -> tuple[Decimal | None, str | None]:
    """(rate, source) for converting ``currency`` to MXN with the information available."""
    cur = (currency or MXN).upper()
    if cur == MXN:
        return Decimal(1), None
    if cur == USD and usd_rate is not None:
        return usd_rate[0], usd_rate[1]
    return None, None
