"""USD -> MXN exchange rate with a free fallback chain.  OWNER: collectors agent.

Order (the first FRESH quote wins; every failure reason is kept):

1. **Banxico SIE FIX** (official, needs the free ``FLIPRADAR_BANXICO_TOKEN``) - ``is_official=True``.
2. **Frankfurter** (no key; European Central Bank reference rates crossed to USD/MXN) - an *estimate*.
3. **ExchangeRate-API open access** (no key; attribution required; we request it at most once per 24 h
   per process and reuse the cached quote) - an *estimate*.

A source that answers with a quote older than :data:`MAX_QUOTE_AGE_DAYS` (e.g. a frozen API version)
counts as failed and the next source is tried.  The manual ``FLIPRADAR_FX_USD_MXN_MANUAL`` rate is applied
by the service layer, not here.
Optional settings read with ``getattr``: ``frankfurter_api_base``, ``exchangerate_api_url`` and
``fx_public_sources_enabled`` (default True).
"""

from __future__ import annotations

import email.utils
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..config import Settings
from . import netguard
from .banxico import ATTRIBUTION as BANXICO_ATTRIBUTION
from .banxico import PLAUSIBLE_USD_MXN, fetch_usd_mxn_fix
from .base import CollectorError

FRANKFURTER_DEFAULT_BASE = "https://api.frankfurter.dev/v1"
EXCHANGERATE_API_DEFAULT_URL = "https://open.er-api.com/v6/latest/USD"
EXCHANGERATE_API_MIN_INTERVAL_SECONDS = 24 * 3600

#: Quotes older than this (calendar days) are stale - the same rule as ``services.fx.MAX_FX_AGE_DAYS``.
MAX_QUOTE_AGE_DAYS = 7

SOURCE_BANXICO = "banxico_fix"
SOURCE_FRANKFURTER = "frankfurter_ecb"
SOURCE_EXCHANGERATE_API = "exchangerate_api_open"

FRANKFURTER_ATTRIBUTION = (
    "Estimate: European Central Bank reference rates (cross rate USD/MXN) via Frankfurter (frankfurter.dev)"
)
EXCHANGERATE_API_ATTRIBUTION = "Rates By Exchange Rate API (https://www.exchangerate-api.com)"


@dataclass(frozen=True)
class FxQuote:
    rate: Decimal  # MXN per 1 USD
    rate_date: str  # YYYY-MM-DD
    source: str
    attribution: str | None
    is_official: bool
    base: str = "USD"
    quote: str = "MXN"


# (monotonic timestamp, quote) of the last successful ExchangeRate-API call in this process.
_ER_API_CACHE: dict[str, tuple[float, FxQuote]] = {}


def reset_cache() -> None:
    """Forget cached quotes (tests)."""
    _ER_API_CACHE.clear()


def _rate(value: Any, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise CollectorError(f"{label}: MXN rate missing")
    try:
        rate = Decimal(str(value))
    except InvalidOperation as exc:
        raise CollectorError(f"{label}: MXN rate {value!r} is not a number") from exc
    if not rate.is_finite() or not PLAUSIBLE_USD_MXN[0] <= rate <= PLAUSIBLE_USD_MXN[1]:
        raise CollectorError(f"{label}: MXN rate {value!r} is outside the plausible range")
    return rate


async def _get_json(
    http: httpx.AsyncClient, url: str, label: str, params: dict[str, str] | None = None
) -> Any:
    try:
        response = await http.get(url, params=params, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise CollectorError(f"{label}: request failed ({exc.__class__.__name__}: {exc})") from exc
    if response.status_code == 429:
        raise CollectorError(f"{label}: rate limited (HTTP 429)")
    if response.status_code != 200:
        raise CollectorError(f"{label}: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise CollectorError(f"{label}: response is not valid JSON") from exc


async def fetch_banxico(settings: Settings, client: httpx.AsyncClient | None = None) -> FxQuote:
    rate_date, rate = await fetch_usd_mxn_fix(settings, client)
    return FxQuote(rate, rate_date, SOURCE_BANXICO, BANXICO_ATTRIBUTION, True)


async def fetch_frankfurter(settings: Settings, client: httpx.AsyncClient | None = None) -> FxQuote:
    base = str(getattr(settings, "frankfurter_api_base", None) or FRANKFURTER_DEFAULT_BASE).rstrip("/")
    async with netguard.http_client(settings, client) as http:
        payload = await _get_json(http, f"{base}/latest", "frankfurter", {"base": "USD", "symbols": "MXN"})
    if not isinstance(payload, dict):
        raise CollectorError("frankfurter: unexpected response shape")
    if payload.get("base") not in (None, "USD"):
        raise CollectorError(f"frankfurter: unexpected base {payload.get('base')!r}")
    rates = payload.get("rates") if isinstance(payload.get("rates"), dict) else {}
    rate = _rate(rates.get("MXN"), "frankfurter")
    rate_date = str(payload.get("date") or "")
    try:
        rate_date = datetime.strptime(rate_date, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise CollectorError(f"frankfurter: invalid date {rate_date!r}") from exc
    return FxQuote(rate, rate_date, SOURCE_FRANKFURTER, FRANKFURTER_ATTRIBUTION, False)


def _er_api_date(payload: dict[str, Any]) -> str:
    text = payload.get("time_last_update_utc")
    if isinstance(text, str) and text.strip():
        try:
            return email.utils.parsedate_to_datetime(text).astimezone(UTC).date().isoformat()
        except (TypeError, ValueError, IndexError):
            pass
    unix = payload.get("time_last_update_unix")
    if isinstance(unix, (int, float)) and not isinstance(unix, bool) and unix > 0:
        return datetime.fromtimestamp(unix, UTC).date().isoformat()
    raise CollectorError("exchangerate-api: response has no last-update time")


async def fetch_exchangerate_api(settings: Settings, client: httpx.AsyncClient | None = None) -> FxQuote:
    url = str(getattr(settings, "exchangerate_api_url", None) or EXCHANGERATE_API_DEFAULT_URL)
    cached = _ER_API_CACHE.get(url)
    if cached is not None and time.monotonic() - cached[0] < EXCHANGERATE_API_MIN_INTERVAL_SECONDS:
        return cached[1]
    async with netguard.http_client(settings, client) as http:
        payload = await _get_json(http, url, "exchangerate-api")
    if not isinstance(payload, dict):
        raise CollectorError("exchangerate-api: unexpected response shape")
    if payload.get("result") != "success":
        raise CollectorError(
            f"exchangerate-api: result={payload.get('result')!r} error={payload.get('error-type')!r}"
        )
    if payload.get("base_code") not in (None, "USD"):
        raise CollectorError(f"exchangerate-api: unexpected base {payload.get('base_code')!r}")
    rates = payload.get("rates") if isinstance(payload.get("rates"), dict) else {}
    quote = FxQuote(
        _rate(rates.get("MXN"), "exchangerate-api"),
        _er_api_date(payload),
        SOURCE_EXCHANGERATE_API,
        EXCHANGERATE_API_ATTRIBUTION,
        False,
    )
    _ER_API_CACHE[url] = (time.monotonic(), quote)
    return quote


def quote_is_fresh(rate_date: str, today: date) -> bool:
    """True when ``rate_date`` (YYYY-MM-DD) is at most :data:`MAX_QUOTE_AGE_DAYS` before ``today``."""
    try:
        when = date.fromisoformat(rate_date[:10])
    except ValueError:
        return False
    return today - when <= timedelta(days=MAX_QUOTE_AGE_DAYS)


async def get_usd_mxn(
    settings: Settings, client: httpx.AsyncClient | None = None, *, today: date | None = None
) -> FxQuote:
    """Best available FRESH USD/MXN quote.  Raises CollectorError listing every source's failure reason.

    A quote older than :data:`MAX_QUOTE_AGE_DAYS` before ``today`` (default: the current UTC date) is that
    source's failure, so the chain moves on to the next source instead of stopping at a stale answer."""
    today = today or datetime.now(UTC).date()
    errors: list[str] = []
    sources: list[tuple[str, str, Any]] = []  # (label, error prefix, fetch function)
    if (settings.banxico_token or "").strip():
        sources.append(("banxico", "banxico: ", fetch_banxico))
    else:
        errors.append("banxico: not configured (FLIPRADAR_BANXICO_TOKEN)")
    if not getattr(settings, "fx_public_sources_enabled", True):
        errors.append("public fallback sources disabled")
    else:
        sources.append(("frankfurter", "", fetch_frankfurter))  # their errors already carry the label
        sources.append(("exchangerate-api", "", fetch_exchangerate_api))
    async with netguard.http_client(settings, client) as http:
        for label, prefix, fetch in sources:
            try:
                quote = await fetch(settings, http)
            except CollectorError as exc:  # includes CollectorNotConfigured
                errors.append(f"{prefix}{exc}")
                continue
            if quote_is_fresh(quote.rate_date, today):
                return quote
            errors.append(
                f"{label}: stale USD/MXN rate dated {quote.rate_date} (older than {MAX_QUOTE_AGE_DAYS} days)"
            )
    raise CollectorError("no USD/MXN exchange rate available: " + "; ".join(errors))
