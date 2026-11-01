"""eBay Browse API (US marketplace, items that ship to Mexico).  OWNER: collectors agent.

Free eBay Developers Program; the operator supplies their own *production* keyset
(``FLIPRADAR_EBAY_CLIENT_ID`` / ``FLIPRADAR_EBAY_CLIENT_SECRET``).

* Token: ``POST {ebay_api_base}/identity/v1/oauth2/token`` (client credentials, Basic auth,
  scope ``https://api.ebay.com/oauth/api_scope``) -> cached until ``expires_in - 300`` s (eBay allows
  1,000 application-token requests per day).
* Search: ``GET {ebay_api_base}/buy/browse/v1/item_summary/search`` with ``filter=deliveryCountry:MX`` and
  ``X-EBAY-C-ENDUSERCTX: contextualLocation=country%3DMX`` so shipping costs are quoted to Mexico.
* Quota: the default Browse allowance is 5,000 calls/day; a per-process daily counter refuses calls
  beyond ``ebay_daily_call_limit`` (default 4,500, read with getattr).

Limitations: active listings only (asking prices, no sold prices - Marketplace Insights is a limited
release API); Mexican import duties/IVA are NOT included (use the import-cost estimator); auction-only
listings are skipped because their current bid is not a purchasable price.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

import httpx

from ..config import Settings
from ..engine.types import RawListing
from ..money import pesos_to_cents
from . import netguard
from .base import Collector, CollectorError, CollectorInfo, CollectorNotConfigured, HealthStatus, RateLimiter
from .jsonld import gtin_is_valid
from .registry import register

log = logging.getLogger(__name__)

SOURCE_KEY = "ebay"
OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
ENDUSER_CONTEXT = "contextualLocation=country%3DMX"
DEFAULT_DAILY_CALL_LIMIT = 4500
MAX_PAGE_SIZE = 200


class EbayQuotaExceeded(CollectorError):
    pass


# ------------------------------------------------------------------------------------------------
# process-wide token cache and daily call counter
# ------------------------------------------------------------------------------------------------


@dataclass
class _Token:
    value: str
    expires_at: float


_TOKENS: dict[tuple[str, str], _Token] = {}
_QUOTA: dict[str, int] = {}  # {"YYYY-MM-DD" (UTC): browse calls made by this process}


def reset_state() -> None:
    """Forget cached tokens and the daily counter (tests)."""
    _TOKENS.clear()
    _QUOTA.clear()


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def quota_status(settings: Settings) -> dict[str, Any]:
    day = _today()
    return {
        "date_utc": day,
        "calls": _QUOTA.get(day, 0),
        "limit": int(getattr(settings, "ebay_daily_call_limit", DEFAULT_DAILY_CALL_LIMIT)),
    }


def _consume_quota(settings: Settings) -> None:
    day = _today()
    limit = int(getattr(settings, "ebay_daily_call_limit", DEFAULT_DAILY_CALL_LIMIT))
    used = _QUOTA.get(day, 0)
    if used >= limit:
        raise EbayQuotaExceeded(
            f"eBay daily safety limit reached ({used} Browse calls today, limit {limit}; eBay's default quota "
            "is 5,000/day). The counter resets at 00:00 UTC (per process)."
        )
    for stale in [d for d in _QUOTA if d != day]:
        del _QUOTA[stale]
    _QUOTA[day] = used + 1


# ------------------------------------------------------------------------------------------------
# pure mapping helpers
# ------------------------------------------------------------------------------------------------


def _amount(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or isinstance(value, (dict, list)):
        return None
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


def _str_or_none(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def map_item_summary(summary: dict[str, Any], *, observed_at: datetime | None = None) -> RawListing | None:
    """One ``itemSummaries[]`` entry -> RawListing (None when unusable or auction-only)."""
    item_id = _str_or_none(summary.get("itemId"))
    title = _str_or_none(summary.get("title"))
    price = summary.get("price") if isinstance(summary.get("price"), dict) else {}
    amount = _amount(price.get("value"))
    currency = _str_or_none(price.get("currency"))
    if not item_id or not title or amount is None or amount == 0 or not currency:
        return None
    buying_options = [str(o) for o in summary.get("buyingOptions") or [] if isinstance(o, str)]
    if buying_options and "AUCTION" in buying_options and "FIXED_PRICE" not in buying_options:
        return None
    shipping_cents: int | None = None
    shipping_type: str | None = None
    for option in summary.get("shippingOptions") or []:
        if not isinstance(option, dict):
            continue
        cost = option.get("shippingCost") if isinstance(option.get("shippingCost"), dict) else {}
        cost_amount = _amount(cost.get("value"))
        if cost_amount is not None and _str_or_none(cost.get("currency")) == currency:
            shipping_cents = pesos_to_cents(cost_amount)
            shipping_type = _str_or_none(option.get("shippingCostType"))
            break
    seller = summary.get("seller") if isinstance(summary.get("seller"), dict) else {}
    seller_info: dict[str, Any] = {}
    username = _str_or_none(seller.get("username"))
    if username:
        seller_info["username"] = username
    feedback_pct = _amount(seller.get("feedbackPercentage"))
    if feedback_pct is not None:
        seller_info["feedback_percentage"] = float(feedback_pct)
    feedback_score = seller.get("feedbackScore")
    if isinstance(feedback_score, int) and not isinstance(feedback_score, bool):
        seller_info["transactions_total"] = feedback_score
        seller_info["feedback_score"] = feedback_score
    location = summary.get("itemLocation") if isinstance(summary.get("itemLocation"), dict) else {}
    location_text = ", ".join(
        p for p in (_str_or_none(location.get("postalCode")), _str_or_none(location.get("country"))) if p
    )
    image = summary.get("image") if isinstance(summary.get("image"), dict) else {}
    gtin = _str_or_none(summary.get("gtin"))
    attributes = {
        "condition_id": _str_or_none(summary.get("conditionId")),
        "buying_options": buying_options or None,
        "epid": _str_or_none(summary.get("epid")),
        "shipping_cost_type": shipping_type,
        "item_location_country": _str_or_none(location.get("country")),
    }
    raw_keys = ("itemId", "title", "price", "condition", "conditionId", "itemWebUrl", "shippingOptions",
                "itemLocation", "buyingOptions", "epid", "seller")  # fmt: skip
    return RawListing(
        source_key=SOURCE_KEY,
        external_id=item_id,
        title=title[:500],
        price_cents=pesos_to_cents(amount),
        currency=currency.upper()[:3],
        url=_str_or_none(summary.get("itemWebUrl")),
        condition_raw=_str_or_none(summary.get("condition")),
        shipping_cents=shipping_cents,
        free_shipping=(shipping_cents == 0) if shipping_cents is not None else None,
        gtin=gtin if gtin and gtin_is_valid(gtin) else None,
        attributes={k: v for k, v in attributes.items() if v is not None},
        seller_id=username[:64] if username else None,
        seller_name=username,
        seller=seller_info,
        location=location_text or None,
        image_url=_str_or_none(image.get("imageUrl")),
        listing_type="FIXED_PRICE"
        if "FIXED_PRICE" in buying_options
        else (buying_options[0] if buying_options else None),
        status="active",
        observed_at=observed_at or datetime.now(UTC),
        raw={k: summary[k] for k in raw_keys if k in summary},
    )


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return (response.text or "")[:300]
    errors = body.get("errors") if isinstance(body, dict) else None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first = errors[0]
        return str(first.get("longMessage") or first.get("message") or first)[:300]
    if isinstance(body, dict) and body.get("error_description"):
        return str(body["error_description"])[:300]
    return str(body)[:300]


# ------------------------------------------------------------------------------------------------
# collector
# ------------------------------------------------------------------------------------------------


@register
class EbayCollector(Collector):
    info = CollectorInfo(
        key=SOURCE_KEY,
        name="eBay (US) Browse API - ships to MX",
        kind="marketplace",
        country="US",
        currency="USD",
        default_fee_profile_key=None,
        requires_credentials=True,
        docs_url="https://developer.ebay.com/api-docs/buy/browse/overview.html",
        notes=(
            "Official Browse API with your own free production keyset. Active listings only (asking prices); "
            "no sold-price data (Marketplace Insights is a limited-release API). Prices are in USD and Mexican "
            "import duties/IVA are NOT included - use the import-cost estimator. Shipping is quoted to Mexico "
            "when the seller ships there. Auction-only listings are skipped. Default quota 5,000 calls/day."
        ),
        capabilities=["search"],
    )
    requests_per_second: ClassVar[float] = 5.0

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(settings, client)
        self._limiter = RateLimiter(self.requests_per_second)

    @property
    def _base(self) -> str:
        return self.settings.ebay_api_base.rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.settings.ebay_client_id and self.settings.ebay_client_secret)

    async def _token(self, *, force_refresh: bool = False) -> str:
        if not self.is_configured():
            raise CollectorNotConfigured(
                "eBay not configured: set FLIPRADAR_EBAY_CLIENT_ID and FLIPRADAR_EBAY_CLIENT_SECRET (production keyset)"
            )
        key = (self._base, str(self.settings.ebay_client_id))
        cached = _TOKENS.get(key)
        if cached and not force_refresh and time.monotonic() < cached.expires_at:
            return cached.value
        credentials = f"{self.settings.ebay_client_id}:{self.settings.ebay_client_secret}".encode()
        await self._limiter.wait()
        try:
            response = await self.client.post(
                f"{self._base}/identity/v1/oauth2/token",
                data={"grant_type": "client_credentials", "scope": OAUTH_SCOPE},
                headers={
                    "Authorization": "Basic " + base64.b64encode(credentials).decode("ascii"),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise CollectorError(f"eBay token request failed ({exc.__class__.__name__}: {exc})") from exc
        if response.status_code != 200:
            raise CollectorError(
                f"eBay token request rejected (HTTP {response.status_code}): {_error_text(response)}. "
                "Check that the keyset is a PRODUCTION keyset."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CollectorError("eBay token response is not JSON") from exc
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise CollectorError("eBay token response has no access_token")
        expires_in = payload.get("expires_in")
        expires_in = expires_in if isinstance(expires_in, int) and not isinstance(expires_in, bool) else 600
        ttl = expires_in - 300 if expires_in > 600 else expires_in / 2
        _TOKENS[key] = _Token(token, time.monotonic() + ttl)
        return token

    async def _browse_get(self, path: str, params: dict[str, str]) -> Any:
        retried_401 = retried_429 = False
        force = False
        while True:
            token = await self._token(force_refresh=force)
            force = False
            _consume_quota(self.settings)
            await self._limiter.wait()
            try:
                response = await self.client.get(
                    f"{self._base}{path}",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-EBAY-C-MARKETPLACE-ID": self.settings.ebay_marketplace_id,
                        "X-EBAY-C-ENDUSERCTX": ENDUSER_CONTEXT,
                        "Accept": "application/json",
                    },
                )
            except httpx.HTTPError as exc:
                raise CollectorError(f"eBay request failed ({exc.__class__.__name__}: {exc})") from exc
            if response.status_code == 401 and not retried_401:
                retried_401 = force = True
                continue
            if response.status_code == 429 and not retried_429:
                retried_429 = True
                await netguard.sleep(netguard.parse_retry_after(response.headers.get("retry-after")))
                continue
            break
        if response.status_code == 401:
            raise CollectorError(f"eBay rejected the application token (HTTP 401): {_error_text(response)}")
        if response.status_code == 403:
            raise CollectorError(
                f"eBay denied access (HTTP 403): {_error_text(response)}. The keyset must be a production keyset "
                "with access to the Buy > Browse API."
            )
        if response.status_code == 429:
            raise CollectorError(
                "eBay rate limit (HTTP 429) persisted after waiting; the daily quota may be exhausted"
            )
        if response.status_code >= 400:
            raise CollectorError(
                f"eBay Browse API error HTTP {response.status_code}: {_error_text(response)}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise CollectorError("eBay returned a non-JSON response") from exc

    async def search(
        self, query: str, *, limit: int = 50, category_id: str | None = None
    ) -> list[RawListing]:
        query = (query or "").strip()
        if not self.is_configured():
            await self._token()  # raises CollectorNotConfigured with instructions
        if not query:
            raise CollectorError("empty search query")
        params = {
            "q": query[:350],
            "limit": str(max(1, min(int(limit), MAX_PAGE_SIZE))),
            "filter": "deliveryCountry:MX",
        }
        if category_id:
            params["category_ids"] = category_id
        data = await self._browse_get("/buy/browse/v1/item_summary/search", params)
        summaries = data.get("itemSummaries") if isinstance(data, dict) else None
        if summaries is None:
            return []  # eBay omits itemSummaries when nothing matches
        if not isinstance(summaries, list):
            raise CollectorError("eBay search returned an unexpected shape")
        observed_at = datetime.now(UTC)
        listings: list[RawListing] = []
        for summary in summaries:
            if isinstance(summary, dict):
                listing = map_item_summary(summary, observed_at=observed_at)
                if listing is not None:
                    listings.append(listing)
        return listings

    async def health(self) -> HealthStatus:
        if not self.is_configured():
            return HealthStatus(
                configured=False,
                ok=None,
                message="not configured: set FLIPRADAR_EBAY_CLIENT_ID and FLIPRADAR_EBAY_CLIENT_SECRET",
            )
        try:
            await self._token()
            data = await self._browse_get(
                "/buy/browse/v1/item_summary/search",
                {"q": "iphone", "limit": "1", "filter": "deliveryCountry:MX"},
            )
        except CollectorError as exc:
            return HealthStatus(
                configured=True, ok=False, message=str(exc), details=quota_status(self.settings)
            )
        details = quota_status(self.settings)
        if isinstance(data, dict) and isinstance(data.get("total"), int):
            details["sample_total"] = data["total"]
        return HealthStatus(configured=True, ok=True, message="token OK; Browse search OK", details=details)
