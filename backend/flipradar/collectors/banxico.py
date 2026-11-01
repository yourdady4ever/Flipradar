"""Banco de Mexico SIE API - official FIX USD/MXN exchange rate.  OWNER: collectors agent.

Endpoint: ``GET {banxico_api_base}/series/SF43718/datos/oportuno`` with header ``Bmx-Token`` (free
64-character token from https://www.banxico.org.mx/SieAPIRest/service/v1/token).
Documented limits: 80 queries per minute and 40,000 per day per token - FLIP RADAR makes one call per
FX refresh, so it stays far below them.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..config import Settings
from . import netguard
from .base import CollectorError, CollectorNotConfigured

FIX_SERIES_ID = "SF43718"
ATTRIBUTION = "Fuente: Banco de México, SIE (serie SF43718, tipo de cambio FIX)"
# Sanity bounds: a USD/MXN value outside this range is treated as a parse/data error, never stored.
PLAUSIBLE_USD_MXN = (Decimal("5"), Decimal("100"))


def _error_detail(response: httpx.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        for key in ("mensaje", "detalle", "message"):
            if isinstance(error, dict) and error.get(key):
                return str(error[key])[:200]
    return str(body)[:200]


def parse_fix_response(payload: Any) -> tuple[str, Decimal]:
    """Parse the SIE JSON body -> ('YYYY-MM-DD', rate).  Raises CollectorError on missing/'N/E' data."""
    try:
        series_list = payload["bmx"]["series"]
    except (KeyError, TypeError) as exc:
        raise CollectorError("Banxico response has no bmx.series") from exc
    if not isinstance(series_list, list) or not series_list:
        raise CollectorError("Banxico response contains no series")
    series = next(
        (s for s in series_list if isinstance(s, dict) and s.get("idSerie") == FIX_SERIES_ID),
        series_list[0],
    )
    data = series.get("datos") if isinstance(series, dict) else None
    if not isinstance(data, list) or not data:
        raise CollectorError(f"Banxico series {FIX_SERIES_ID} has no data points")
    point = data[-1]
    if not isinstance(point, dict):
        raise CollectorError("Banxico data point is malformed")
    raw_value = str(point.get("dato") or "").strip()
    raw_date = str(point.get("fecha") or "").strip()
    if not raw_value or raw_value.upper() in ("N/E", "NE"):
        raise CollectorError(
            f"Banxico returned no value (dato={raw_value or 'empty'!r}) for {raw_date or 'the latest date'}"
        )
    try:
        rate = Decimal(raw_value.replace(",", ""))
    except InvalidOperation as exc:
        raise CollectorError(f"Banxico value {raw_value!r} is not a number") from exc
    if not rate.is_finite() or not PLAUSIBLE_USD_MXN[0] <= rate <= PLAUSIBLE_USD_MXN[1]:
        raise CollectorError(f"Banxico value {raw_value!r} is outside the plausible USD/MXN range")
    try:
        rate_date = datetime.strptime(raw_date, "%d/%m/%Y").date().isoformat()
    except ValueError as exc:
        raise CollectorError(f"Banxico date {raw_date!r} is not dd/mm/yyyy") from exc
    return rate_date, rate


async def fetch_usd_mxn_fix(
    settings: Settings, client: httpx.AsyncClient | None = None
) -> tuple[str, Decimal]:
    """Return (rate_date 'YYYY-MM-DD', rate) for the latest published FIX rate.
    Raises CollectorNotConfigured without a token, CollectorError on failures."""
    token = (settings.banxico_token or "").strip()
    if not token:
        raise CollectorNotConfigured(
            "Banxico SIE token not configured (set FLIPRADAR_BANXICO_TOKEN; the token is free)"
        )
    url = f"{settings.banxico_api_base.rstrip('/')}/series/{FIX_SERIES_ID}/datos/oportuno"
    headers = {"Bmx-Token": token, "Accept": "application/json", "User-Agent": settings.user_agent}
    async with netguard.http_client(settings, client) as http:
        try:
            response = await http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise CollectorError(f"Banxico SIE request failed: {exc.__class__.__name__}: {exc}") from exc
    if response.status_code in (401, 403):
        raise CollectorError(
            f"Banxico rejected the token (HTTP {response.status_code}): {_error_detail(response)}"
        )
    if response.status_code == 429:
        raise CollectorError("Banxico rate limit reached (80 queries/min, 40,000/day); try again later")
    if response.status_code != 200:
        raise CollectorError(f"Banxico SIE HTTP {response.status_code}: {_error_detail(response)}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise CollectorError("Banxico response is not valid JSON") from exc
    return parse_fix_response(payload)
