"""Collector (data source connector) interface.

A collector talks to exactly one external source and returns :class:`RawListing` objects.  It must:

* never fabricate data - only map fields that the source actually returned;
* identify itself with the configured User-Agent and respect documented rate limits;
* raise :class:`CollectorNotConfigured` when credentials are missing (the scheduler then skips it);
* raise :class:`CollectorError` (with a human-readable message) for HTTP/auth/parse failures.
"""

from __future__ import annotations

import abc
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from ..config import Settings, get_settings
from ..engine.types import RawListing


class CollectorError(RuntimeError):
    pass


class CollectorNotConfigured(CollectorError):
    pass


@dataclass
class CollectorInfo:
    key: str
    name: str
    kind: str  # flipradar.models.SourceKind value
    country: str
    currency: str
    default_fee_profile_key: str | None
    requires_credentials: bool
    docs_url: str | None
    notes: str = ""
    capabilities: list[str] = field(default_factory=list)  # search|item|feed|url_check|import|fx


@dataclass
class HealthStatus:
    configured: bool
    ok: bool | None  # None = not checked
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class RateLimiter:
    """Simple async token-spacing limiter (min interval between calls)."""

    def __init__(self, calls_per_second: float) -> None:
        self.min_interval = 1.0 / calls_per_second if calls_per_second > 0 else 0.0
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._last + self.min_interval - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


class Collector(abc.ABC):
    info: ClassVar[CollectorInfo]

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    # -- http helpers --------------------------------------------------------------------------
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.settings.http_timeout_seconds,
                headers={"User-Agent": self.settings.user_agent, "Accept": "application/json"},
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Collector:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- interface -----------------------------------------------------------------------------
    @abc.abstractmethod
    def is_configured(self) -> bool:
        """True when all required credentials/settings are present."""

    async def health(self) -> HealthStatus:
        if not self.is_configured():
            return HealthStatus(configured=False, ok=None, message="not configured")
        return HealthStatus(configured=True, ok=None, message="configured (not checked)")

    async def search(self, query: str, *, limit: int = 50, category_id: str | None = None) -> list[RawListing]:
        """Search the source.  Collectors without search support raise CollectorError."""
        raise CollectorError(f"{self.info.key} does not support search")

    async def fetch_items(self, external_ids: list[str]) -> list[RawListing]:
        """Refresh specific listings (used to track price changes / sold status)."""
        raise CollectorError(f"{self.info.key} does not support item refresh")
