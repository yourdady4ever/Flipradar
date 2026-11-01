"""Banco de Mexico SIE API - official FIX USD/MXN exchange rate.  OWNER: collectors agent."""

from __future__ import annotations

from decimal import Decimal

import httpx

from ..config import Settings

FIX_SERIES_ID = "SF43718"


async def fetch_usd_mxn_fix(settings: Settings, client: httpx.AsyncClient | None = None) -> tuple[str, Decimal]:
    """Return (rate_date 'YYYY-MM-DD', rate) for the latest published FIX rate.
    Raises CollectorNotConfigured without a token, CollectorError on failures."""
    raise NotImplementedError
