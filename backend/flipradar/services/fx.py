"""USD/MXN exchange rates: stored Banxico FIX rates first, manual fallback second, never invented.

When no rate is available, foreign-currency listings keep ``price_mxn_cents = None`` and are therefore
excluded from price comparisons (rather than converted with a guessed rate).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..collectors import banxico
from ..config import Settings
from ..models import FxRate
from . import settings_store

USD = "USD"
MXN = "MXN"


def _decimal(text: str) -> Decimal | None:
    try:
        value = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() and value > 0 else None


def latest_usd_mxn(session: Session, settings: Settings | None = None) -> tuple[Decimal, str, str] | None:
    """(rate, source, rate_date) of the newest stored USD->MXN rate, else the manual rate, else None."""
    rows = session.scalars(
        select(FxRate)
        .where(FxRate.base == USD, FxRate.quote == MXN)
        .order_by(FxRate.rate_date.desc(), FxRate.fetched_at.desc(), FxRate.id.desc())
        .limit(5)
    ).all()
    for row in rows:
        rate = _decimal(row.rate)
        if rate is not None:
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
