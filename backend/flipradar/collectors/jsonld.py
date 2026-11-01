"""User-initiated single-URL price check via schema.org Product/Offer JSON-LD.  OWNER: collectors agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import Settings
from ..engine.types import RawListing


@dataclass
class UrlCheckResult:
    url: str
    allowed_by_robots: bool
    found: bool
    raw: RawListing | None = None
    extracted: dict[str, Any] = field(default_factory=dict)
    message: str = ""


async def check_url(url: str, settings: Settings, client: httpx.AsyncClient | None = None) -> UrlCheckResult:
    """Fetch ONE page the user asked for (robots.txt respected, SSRF-guarded, size-capped) and extract a
    Product/Offer.  Returned RawListing uses source_key 'web_jsonld'."""
    raise NotImplementedError
