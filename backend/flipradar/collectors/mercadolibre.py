"""Mercado Libre México (site MLM) through the official API - CATALOG based.  OWNER: collectors agent.

Why catalog-only: since 2026 the keyword search ``GET /sites/MLM/search`` answers 403 (PolicyAgent,
``PA_UNAUTHORIZED_RESULT_FROM_POLICIES``) for third-party apps, and ``/items/{id}`` may too.  What an
app can use (depending on the functional permissions enabled for it in the ML DevCenter):

* ``GET /products/search?status=active&site_id=MLM&q=...`` (or ``product_identifier=<GTIN>``)
  -> catalog products (name, attributes BRAND/MODEL/GTIN..., pictures);
* ``GET /products/{PRODUCT_ID}/items`` -> the active listings competing for that catalog product;
* ``GET /users/{USER_ID}`` -> seller reputation (optional enrichment, max 20 sellers per search);
* ``GET /sites/MLM/domain_discovery/search`` -> category predictor (works without a token);
* ``GET /sites/MLM/listing_prices`` -> official selling fees for a price/category/listing type.

Consequences: only listings attached to a catalog product are visible (no free-form used listings).

Authentication (``POST /oauth/token``): a configured ``FLIPRADAR_ML_ACCESS_TOKEN`` is used as-is until it
is rejected; otherwise ``grant_type=refresh_token`` (refresh tokens are SINGLE-USE: the newest one is
kept in memory, exposed as :attr:`MercadoLibreCollector.rotated_refresh_token` and pushed to listeners
registered with :func:`add_refresh_token_listener` so the service layer can persist it) and finally
``grant_type=client_credentials`` (undocumented, community-verified in 2026).  Tokens are cached until
``expires_in - 300`` seconds.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, ClassVar, Literal

import httpx

from ..config import Settings
from ..engine.types import RawListing
from ..money import cents_to_str, pesos_to_cents
from . import netguard
from .base import Collector, CollectorError, CollectorInfo, CollectorNotConfigured, HealthStatus, RateLimiter
from .jsonld import gtin_is_valid
from .registry import register

log = logging.getLogger(__name__)

SOURCE_KEY = "mercadolibre"
ARTICLE_BASE_URL = "https://articulo.mercadolibre.com.mx/"
MAX_CATALOG_PRODUCTS = 10
MAX_SELLER_ENRICHMENT = 20
MAX_SEARCH_LIMIT = 500
LISTING_TYPE_LABELS = {"gold_special": "Clásica", "gold_pro": "Premium"}
POLICY_MESSAGE = (
    "Mercado Libre denied access by policy (PolicyAgent) for this app. Enable the required functional "
    "permissions for your app in the ML DevCenter or use manual/CSV import."
)
_POLICY_MARKERS = ("PolicyAgent", "PA_UNAUTHORIZED_RESULT_FROM_POLICIES")
_ITEM_ID = re.compile(r"^([A-Z]{3})-?(\d+)$")
_SAFE_PATH_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

AuthMode = Literal["required", "optional", "none"]


class MercadoLibrePolicyError(CollectorError):
    """403 from Mercado Libre's PolicyAgent: the app lacks the functional permission for the endpoint."""


# ------------------------------------------------------------------------------------------------
# process-wide token store (refresh tokens are single-use, so every collector instance must share it)
# ------------------------------------------------------------------------------------------------


@dataclass
class _TokenState:
    access_token: str | None = None
    expires_at: float = 0.0  # time.monotonic()
    source: str | None = None  # access_token | refresh_token | client_credentials
    refresh_token: str | None = None  # newest refresh token received from Mercado Libre
    rotated: bool = False  # refresh_token differs from the configured FLIPRADAR_ML_REFRESH_TOKEN
    static_rejected: bool = False  # the configured access token got a 401
    static_seen: str | None = None  # configured access token the flags above refer to
    user_id: str | None = None
    scope: str | None = None


_TOKENS: dict[tuple[str, str], _TokenState] = {}
_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple[str, str], asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)
_REFRESH_LISTENERS: list[Callable[[str], None]] = []


def _state_key(settings: Settings) -> tuple[str, str]:
    return (settings.ml_api_base.rstrip("/"), settings.ml_client_id or "<access-token-only>")


def _state_for(settings: Settings) -> _TokenState:
    return _TOKENS.setdefault(_state_key(settings), _TokenState())


def _lock_for(key: tuple[str, str]) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _LOCKS.setdefault(loop, {})
    return locks.setdefault(key, asyncio.Lock())


def reset_token_cache() -> None:
    """Forget every cached token (tests / after changing credentials)."""
    _TOKENS.clear()


def add_refresh_token_listener(callback: Callable[[str], None]) -> None:
    """``callback(new_refresh_token)`` is called whenever Mercado Libre rotates the refresh token."""
    if callback not in _REFRESH_LISTENERS:
        _REFRESH_LISTENERS.append(callback)


def remove_refresh_token_listener(callback: Callable[[str], None]) -> None:
    if callback in _REFRESH_LISTENERS:
        _REFRESH_LISTENERS.remove(callback)


def get_rotated_refresh_token(settings: Settings) -> str | None:
    state = _TOKENS.get(_state_key(settings))
    return state.refresh_token if state is not None and state.rotated else None


# ------------------------------------------------------------------------------------------------
# pure mapping helpers (unit-tested with fixtures)
# ------------------------------------------------------------------------------------------------


def _to_cents(value: Any, *, allow_zero: bool = False) -> int | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        return None
    return pesos_to_cents(amount)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _str_or_none(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def item_url(item_id: str) -> str | None:
    """'MLM123456' -> 'https://articulo.mercadolibre.com.mx/MLM-123456' (MLM ids only)."""
    match = _ITEM_ID.match(str(item_id or "").strip().upper())
    if not match or match.group(1) != "MLM":
        return None
    return f"{ARTICLE_BASE_URL}{match.group(1)}-{match.group(2)}"


def catalog_attributes(product: dict[str, Any]) -> dict[str, str]:
    """{attribute id: value_name} for a catalog product (only attributes that carry a value)."""
    out: dict[str, str] = {}
    for attr in product.get("attributes") or []:
        if not isinstance(attr, dict):
            continue
        attr_id = _str_or_none(attr.get("id"))
        value = _str_or_none(attr.get("value_name"))
        if attr_id and value and len(out) < 80:
            out[attr_id.upper()] = value[:200]
    return out


def _gtin_from(value: str | None) -> str | None:
    if not value:
        return None
    for part in re.split(r"[,;/\s]+", value):
        digits = part.strip()
        if gtin_is_valid(digits):
            return digits
    return None


def _picture_url(product: dict[str, Any]) -> str | None:
    pictures = product.get("pictures")
    if isinstance(pictures, list) and pictures and isinstance(pictures[0], dict):
        return _str_or_none(pictures[0].get("url"))
    return None


def map_catalog_item(
    item: dict[str, Any], product: dict[str, Any], *, observed_at: datetime | None = None
) -> RawListing | None:
    """One ``/products/{id}/items`` result + its catalog product -> RawListing (None when unusable)."""
    item_id = _str_or_none(item.get("item_id")) or _str_or_none(item.get("id"))
    price_cents = _to_cents(item.get("price"))
    currency = _str_or_none(item.get("currency_id"))
    title = _str_or_none(product.get("name"))
    if not item_id or price_cents is None or not currency or not title:
        return None
    attrs = catalog_attributes(product)
    shipping = item.get("shipping") if isinstance(item.get("shipping"), dict) else {}
    free_shipping = shipping.get("free_shipping") if isinstance(shipping.get("free_shipping"), bool) else None
    listing_type = _str_or_none(item.get("listing_type_id"))
    attributes: dict[str, Any] = dict(attrs)
    meta = {
        "catalog_product_id": _str_or_none(product.get("id")),
        "domain_id": _str_or_none(product.get("domain_id")),
        "official_store_id": _str_or_none(item.get("official_store_id")),
        "logistic_type": _str_or_none(shipping.get("logistic_type")),
        "listing_type_label": LISTING_TYPE_LABELS.get(listing_type or ""),
        "original_price_cents": _to_cents(item.get("original_price")),
        "warranty": _str_or_none(item.get("warranty")),
    }
    attributes.update({k: v for k, v in meta.items() if v is not None})
    address = item.get("address") if isinstance(item.get("address"), dict) else {}
    location = ", ".join(
        p for p in (_str_or_none(address.get("city_name")), _str_or_none(address.get("state_name"))) if p
    )
    raw_keys = (
        "item_id", "price", "original_price", "currency_id", "seller_id", "condition", "listing_type_id",
        "shipping", "category_id", "official_store_id", "available_quantity", "sold_quantity", "warranty",
    )  # fmt: skip
    raw = {k: item[k] for k in raw_keys if k in item}
    raw["catalog_product_id"] = product.get("id")
    seller_id = item.get("seller_id")
    return RawListing(
        source_key=SOURCE_KEY,
        external_id=item_id,
        title=title[:500],
        price_cents=price_cents,
        currency=currency.upper()[:3],
        url=item_url(item_id) or _str_or_none(item.get("permalink")),
        condition_raw=_str_or_none(item.get("condition")),
        shipping_cents=0 if free_shipping is True else None,
        free_shipping=free_shipping,
        brand=attrs.get("BRAND"),
        model=attrs.get("MODEL"),
        gtin=_gtin_from(attrs.get("GTIN")),
        category_id=_str_or_none(item.get("category_id")),
        attributes=attributes,
        seller_id=str(seller_id) if seller_id is not None and not isinstance(seller_id, bool) else None,
        location=location or None,
        image_url=_picture_url(product),
        available_quantity=_int_or_none(item.get("available_quantity")),
        sold_quantity=_int_or_none(item.get("sold_quantity")),
        listing_type=listing_type,
        status="active",
        observed_at=observed_at or datetime.now(UTC),
        raw=raw,
    )


def map_seller(user: dict[str, Any]) -> dict[str, Any]:
    """``/users/{id}`` -> RawListing.seller dict (only keys the API actually returned)."""
    reputation = user.get("seller_reputation") if isinstance(user.get("seller_reputation"), dict) else {}
    transactions = reputation.get("transactions") if isinstance(reputation.get("transactions"), dict) else {}
    ratings = transactions.get("ratings") if isinstance(transactions.get("ratings"), dict) else {}
    negative_pct: float | None = None
    negative = ratings.get("negative")
    if isinstance(negative, (int, float)) and not isinstance(negative, bool):
        negative_pct = float((Decimal(str(negative)) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    out = {
        "nickname": _str_or_none(user.get("nickname")),
        "reputation_level": _str_or_none(reputation.get("level_id")),
        "power_seller_status": _str_or_none(reputation.get("power_seller_status")),
        "transactions_total": _int_or_none(transactions.get("total")),
        "transactions_completed": _int_or_none(transactions.get("completed")),
        "transactions_canceled": _int_or_none(transactions.get("canceled")),
        "ratings_negative_pct": negative_pct,
        "registration_date": _str_or_none(user.get("registration_date")),
    }
    return {k: v for k, v in out.items() if v is not None}


def _bp_from_percent(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        pct = Decimal(str(value))
    except InvalidOperation:
        return None
    if not pct.is_finite() or pct < 0:
        return None
    return int((pct * 100).to_integral_value(rounding=ROUND_HALF_UP))


def parse_listing_fee(payload: Any, listing_type_id: str) -> dict[str, Any]:
    """``/sites/MLM/listing_prices`` (list or object) -> {commission_bp, fixed_fee_cents, sale_fee_cents...}."""
    entries = [e for e in (payload if isinstance(payload, list) else [payload]) if isinstance(e, dict)]
    if not entries:
        raise CollectorError("Mercado Libre listing_prices returned no data")
    entry = next((e for e in entries if e.get("listing_type_id") == listing_type_id), None)
    if entry is None:
        if len(entries) == 1 and entries[0].get("listing_type_id") is None:
            entry = entries[0]
        else:
            raise CollectorError(
                f"Mercado Libre listing_prices has no entry for listing type '{listing_type_id}'"
            )
    details = entry.get("sale_fee_details") if isinstance(entry.get("sale_fee_details"), dict) else {}
    return {
        "listing_type_id": entry.get("listing_type_id") or listing_type_id,
        "listing_type_label": LISTING_TYPE_LABELS.get(str(entry.get("listing_type_id") or listing_type_id)),
        "commission_bp": _bp_from_percent(details.get("percentage_fee")),
        "fixed_fee_cents": _to_cents(details.get("fixed_fee"), allow_zero=True),
        "sale_fee_cents": _to_cents(entry.get("sale_fee_amount"), allow_zero=True),
        "gross_amount_cents": _to_cents(details.get("gross_amount"), allow_zero=True),
        "currency": _str_or_none(entry.get("currency_id")),
        "raw": entry,
    }


def _looks_like_gtin(query: str) -> bool:
    return query.isdigit() and len(query) in (8, 12, 13, 14)


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _is_policy_block(body: Any, text: str) -> bool:
    if isinstance(body, dict):
        for key in ("blocked_by", "code", "error", "message"):
            value = body.get(key)
            if isinstance(value, str) and any(m in value for m in _POLICY_MARKERS):
                return True
    return any(m in (text or "") for m in _POLICY_MARKERS)


def _error_text(body: Any, response: httpx.Response) -> str:
    if isinstance(body, dict):
        parts = [str(body[k]) for k in ("error", "message") if body.get(k)]
        if parts:
            return " - ".join(parts)[:300]
    return (response.text or "")[:300]


# ------------------------------------------------------------------------------------------------
# collector
# ------------------------------------------------------------------------------------------------


@register
class MercadoLibreCollector(Collector):
    info = CollectorInfo(
        key=SOURCE_KEY,
        name="Mercado Libre México (catalog API)",
        kind="marketplace",
        country="MX",
        currency="MXN",
        default_fee_profile_key="ml_clasica",
        requires_credentials=True,
        docs_url="https://developers.mercadolibre.com.mx/",
        notes=(
            "Official API, catalog-based: searches catalog products (/products/search) and reads the active "
            "listings attached to each one (/products/{id}/items). Free-form listings that are not linked to a "
            "catalog product (most used items) are NOT visible. Since 2026 the keyword search /sites/MLM/search "
            "is blocked for third-party apps (403 PolicyAgent), so it is not used; endpoint access depends on the "
            "functional permissions enabled for your app in the ML DevCenter. Needs a free app "
            "(client_id/client_secret) or an access token."
        ),
        capabilities=["search"],
    )
    requests_per_second: ClassVar[float] = 5.0

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(settings, client)
        self._limiter = RateLimiter(self.requests_per_second)
        self._seller_cache: dict[str, dict[str, Any] | None] = {}
        self.last_warnings: list[str] = []

    # -- configuration -------------------------------------------------------------------------
    @property
    def _base(self) -> str:
        return self.settings.ml_api_base.rstrip("/")

    @property
    def _site(self) -> str:
        return (self.settings.ml_site_id or "MLM").upper()

    def _has_app_credentials(self) -> bool:
        return bool(self.settings.ml_client_id and self.settings.ml_client_secret)

    def is_configured(self) -> bool:
        return bool(self.settings.ml_access_token or self._has_app_credentials())

    @property
    def rotated_refresh_token(self) -> str | None:
        """The newest refresh token issued by Mercado Libre when it differs from the configured one.
        Persist it (e.g. as FLIPRADAR_ML_REFRESH_TOKEN) - the configured value is no longer valid."""
        return get_rotated_refresh_token(self.settings)

    # -- auth ----------------------------------------------------------------------------------
    async def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        await self._limiter.wait()
        try:
            response = await self.client.post(
                f"{self._base}/oauth/token", data=form, headers={"Accept": "application/json"}
            )
        except httpx.HTTPError as exc:
            raise CollectorError(f"token request failed ({exc.__class__.__name__}: {exc})") from exc
        body = _json_or_none(response)
        if response.status_code != 200:
            error = body.get("error") if isinstance(body, dict) else None
            if error == "invalid_grant":
                raise CollectorError(
                    "refresh token rejected (invalid_grant). Mercado Libre refresh tokens are single-use and "
                    "expire; re-authorize the app and store the newest refresh token"
                )
            if error in ("invalid_client", "unauthorized_client"):
                raise CollectorError(f"client_id/client_secret rejected ({error})")
            raise CollectorError(f"HTTP {response.status_code}: {_error_text(body, response)}")
        if not isinstance(body, dict):
            raise CollectorError("token response is not a JSON object")
        return body

    def _apply_token(
        self, state: _TokenState, payload: dict[str, Any], source: str, used_refresh: str | None
    ) -> str:
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise CollectorError("token response has no access_token")
        expires_in = _int_or_none(payload.get("expires_in")) or 600
        ttl = expires_in - 300 if expires_in > 600 else expires_in / 2
        state.access_token = token
        state.expires_at = time.monotonic() + ttl
        state.source = source
        state.user_id = _str_or_none(payload.get("user_id"))
        state.scope = _str_or_none(payload.get("scope"))
        new_refresh = payload.get("refresh_token")
        if isinstance(new_refresh, str) and new_refresh and new_refresh != used_refresh:
            state.refresh_token = new_refresh
            state.rotated = new_refresh != (self.settings.ml_refresh_token or "")
            if state.rotated:
                log.warning(
                    "Mercado Libre issued a NEW refresh token (refresh tokens are single-use). The operator must "
                    "store it (MercadoLibreCollector.rotated_refresh_token) as FLIPRADAR_ML_REFRESH_TOKEN; the "
                    "configured one is no longer valid after a restart."
                )
                for listener in list(_REFRESH_LISTENERS):
                    try:
                        listener(new_refresh)
                    except Exception:  # noqa: BLE001 - a broken listener must not break collection
                        log.exception("refresh token listener failed")
        return token

    async def _access_token(self, *, force_refresh: bool = False) -> str:
        if not self.is_configured():
            raise CollectorNotConfigured(
                "Mercado Libre not configured: set FLIPRADAR_ML_CLIENT_ID and FLIPRADAR_ML_CLIENT_SECRET "
                "(optionally FLIPRADAR_ML_REFRESH_TOKEN) or FLIPRADAR_ML_ACCESS_TOKEN"
            )
        key = _state_key(self.settings)
        state = _state_for(self.settings)
        static = (self.settings.ml_access_token or "").strip()
        if static != (state.static_seen or ""):
            # the operator configured a different access token: forget the old one (and its rejection)
            state.static_seen, state.static_rejected = static or None, False
            if state.source == "access_token":
                state.access_token, state.expires_at, state.source = None, 0.0, None
        if not force_refresh and state.access_token and time.monotonic() < state.expires_at:
            return state.access_token
        async with _lock_for(key):
            if not force_refresh and state.access_token and time.monotonic() < state.expires_at:
                return state.access_token
            if force_refresh and state.source == "access_token":
                state.static_rejected = True
            if static and not state.static_rejected and not force_refresh:
                state.access_token, state.expires_at, state.source = static, math.inf, "access_token"
                return static
            errors: list[str] = []
            if self._has_app_credentials():
                client_id = str(self.settings.ml_client_id)
                client_secret = str(self.settings.ml_client_secret)
                refresh = state.refresh_token or (self.settings.ml_refresh_token or "").strip()
                if refresh:
                    try:
                        payload = await self._token_request(
                            {
                                "grant_type": "refresh_token",
                                "client_id": client_id,
                                "client_secret": client_secret,
                                "refresh_token": refresh,
                            }
                        )
                        return self._apply_token(state, payload, "refresh_token", refresh)
                    except CollectorError as exc:
                        errors.append(f"refresh_token grant: {exc}")
                        log.warning("Mercado Libre refresh_token grant failed: %s", exc)
                try:
                    payload = await self._token_request(
                        {
                            "grant_type": "client_credentials",
                            "client_id": client_id,
                            "client_secret": client_secret,
                        }
                    )
                    return self._apply_token(state, payload, "client_credentials", None)
                except CollectorError as exc:
                    errors.append(f"client_credentials grant: {exc}")
            else:
                errors.append(
                    "the configured access token was rejected and no client_id/client_secret are configured to "
                    "obtain a new one (Mercado Libre access tokens expire after a few hours)"
                )
            state.access_token, state.expires_at = None, 0.0
            raise CollectorError("Mercado Libre authentication failed: " + "; ".join(errors))

    # -- HTTP ----------------------------------------------------------------------------------
    async def _get_json(
        self,
        path: str,
        params: dict[str, str] | None = None,
        *,
        auth: AuthMode = "required",
        allow_404: bool = False,
    ) -> Any:
        url = f"{self._base}{path}"
        retried_401 = retried_429 = False
        force = False
        while True:
            headers = {"Accept": "application/json"}
            use_token = auth == "required" or (auth == "optional" and self.is_configured())
            if use_token:
                try:
                    headers["Authorization"] = f"Bearer {await self._access_token(force_refresh=force)}"
                except CollectorError:
                    if auth == "required":
                        raise
                    use_token = False
            force = False
            await self._limiter.wait()
            try:
                response = await self.client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise CollectorError(
                    f"Mercado Libre request to {path} failed ({exc.__class__.__name__}: {exc})"
                ) from exc
            if response.status_code == 401 and use_token and not retried_401:
                retried_401, force = True, True
                continue
            if response.status_code == 429 and not retried_429:
                retried_429 = True
                await netguard.sleep(netguard.parse_retry_after(response.headers.get("retry-after")))
                continue
            break
        if response.status_code == 404 and allow_404:
            return None
        body = _json_or_none(response)
        if response.status_code == 403 and _is_policy_block(body, response.text):
            raise MercadoLibrePolicyError(f"{POLICY_MESSAGE} (endpoint: {path})")
        if response.status_code == 401:
            raise CollectorError(
                f"Mercado Libre rejected the access token (HTTP 401) on {path} even after renewing it: "
                f"{_error_text(body, response)}"
            )
        if response.status_code == 429:
            raise CollectorError(
                "Mercado Libre rate limit (HTTP 429) persisted after waiting; try again later"
            )
        if response.status_code >= 400:
            raise CollectorError(
                f"Mercado Libre API error HTTP {response.status_code} on {path}: {_error_text(body, response)}"
            )
        if body is None:
            raise CollectorError(f"Mercado Libre returned a non-JSON response on {path}")
        return body

    # -- API wrappers --------------------------------------------------------------------------
    async def search_catalog(self, query: str, *, limit: int = MAX_CATALOG_PRODUCTS) -> list[dict[str, Any]]:
        """Catalog products for a keyword (or a GTIN, sent as ``product_identifier``)."""
        query = query.strip()
        limit = max(1, min(int(limit), 50))
        params = {"status": "active", "site_id": self._site, "limit": str(limit)}
        if _looks_like_gtin(query):
            params["product_identifier"] = query
        else:
            params["q"] = query
        data = await self._get_json("/products/search", params)
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise CollectorError(
                "Mercado Libre /products/search returned an unexpected shape (no results list)"
            )
        return [r for r in results if isinstance(r, dict) and r.get("id")][:limit]

    async def get_product_items(self, product_id: str) -> list[dict[str, Any]] | None:
        """Active listings of a catalog product; None when the product has none (HTTP 404)."""
        if not _SAFE_PATH_ID.match(product_id):
            raise CollectorError(f"invalid catalog product id {product_id!r}")
        data = await self._get_json(f"/products/{product_id}/items", allow_404=True)
        if data is None:
            return None
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise CollectorError(f"Mercado Libre /products/{product_id}/items returned an unexpected shape")
        return [r for r in results if isinstance(r, dict)]

    async def get_seller(self, seller_id: str) -> dict[str, Any] | None:
        """Seller reputation (cached per collector instance); None when unavailable."""
        if seller_id in self._seller_cache:
            return self._seller_cache[seller_id]
        if not seller_id.isdigit():
            self._seller_cache[seller_id] = None
            return None
        data = await self._get_json(f"/users/{seller_id}", auth="optional", allow_404=True)
        info = map_seller(data) if isinstance(data, dict) else None
        self._seller_cache[seller_id] = info
        return info

    async def enrich_sellers(
        self, listings: list[RawListing], *, max_sellers: int = MAX_SELLER_ENRICHMENT
    ) -> int:
        """Attach reputation data to listings (max ``max_sellers`` distinct sellers).  Returns sellers found."""
        seller_ids: list[str] = []
        for listing in listings:
            if listing.seller_id and listing.seller_id not in seller_ids:
                seller_ids.append(listing.seller_id)
        found = 0
        for seller_id in seller_ids[:max_sellers]:
            try:
                info = await self.get_seller(seller_id)
            except MercadoLibrePolicyError:
                self.last_warnings.append("seller reputation lookup denied by policy (PolicyAgent); skipped")
                break
            except CollectorError as exc:
                self.last_warnings.append(f"seller {seller_id}: {exc}")
                self._seller_cache[seller_id] = None
                continue
            if info:
                found += 1
        for listing in listings:
            info = self._seller_cache.get(listing.seller_id or "")
            if info:
                listing.seller = dict(info)
                listing.seller_name = info.get("nickname") or listing.seller_name
        return found

    async def predict_category(self, title: str, *, limit: int = 3) -> list[dict[str, Any]]:
        """Category predictor (no token needed): [{domain_id, domain_name, category_id, category_name, attributes}]."""
        title = (title or "").strip()
        if not title:
            raise CollectorError("empty title")
        limit = max(1, min(int(limit), 10))
        data = await self._get_json(
            f"/sites/{self._site}/domain_discovery/search",
            {"q": title[:200], "limit": str(limit)},
            auth="none",
        )
        if not isinstance(data, list):
            raise CollectorError("Mercado Libre domain_discovery returned an unexpected shape")
        out = []
        for entry in data[:limit]:
            if isinstance(entry, dict):
                out.append(
                    {
                        "domain_id": _str_or_none(entry.get("domain_id")),
                        "domain_name": _str_or_none(entry.get("domain_name")),
                        "category_id": _str_or_none(entry.get("category_id")),
                        "category_name": _str_or_none(entry.get("category_name")),
                        "attributes": entry.get("attributes")
                        if isinstance(entry.get("attributes"), list)
                        else [],
                    }
                )
        return out

    async def get_listing_fee(
        self,
        price_cents: int,
        category_id: str,
        listing_type_id: str = "gold_special",
        *,
        logistic_type: str | None = None,
        shipping_mode: str | None = None,
    ) -> dict[str, Any]:
        """Official selling fee for a price/category/listing type (``gold_special`` = Clásica,
        ``gold_pro`` = Premium).  Returns commission_bp, fixed_fee_cents, sale_fee_cents, gross_amount_cents,
        currency and the raw entry.  Uses the token when one is configured."""
        if price_cents <= 0:
            raise ValueError("price_cents must be positive")
        if not _SAFE_PATH_ID.match(category_id or ""):
            raise CollectorError(f"invalid category id {category_id!r}")
        params = {
            "price": cents_to_str(price_cents) or "0",
            "listing_type_id": listing_type_id,
            "category_id": category_id,
        }
        if logistic_type:
            params["logistic_type"] = logistic_type
        if shipping_mode:
            params["shipping_mode"] = shipping_mode
        data = await self._get_json(f"/sites/{self._site}/listing_prices", params, auth="optional")
        return parse_listing_fee(data, listing_type_id)

    # -- Collector interface -------------------------------------------------------------------
    async def search(
        self, query: str, *, limit: int = 50, category_id: str | None = None
    ) -> list[RawListing]:
        """Catalog search -> listings of the first N catalog products (N = ceil(limit/10), max 10).
        ``category_id`` filters listings by their ML category."""
        if not self.is_configured():
            await self._access_token()  # raises CollectorNotConfigured with instructions
        query = (query or "").strip()
        if not query:
            raise CollectorError("empty search query")
        limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
        self.last_warnings = []
        n_products = min(MAX_CATALOG_PRODUCTS, max(1, math.ceil(limit / 10)))
        products = await self.search_catalog(query, limit=n_products)
        observed_at = datetime.now(UTC)
        listings: list[RawListing] = []
        seen: set[str] = set()
        skipped = 0
        for product in products:
            if len(listings) >= limit:
                break
            product_id = str(product["id"])
            items = await self.get_product_items(product_id)
            if items is None:
                self.last_warnings.append(f"catalog product {product_id} has no active listings (HTTP 404)")
                continue
            for item in items:
                listing = map_catalog_item(item, product, observed_at=observed_at)
                if listing is None:
                    skipped += 1
                    continue
                if category_id and listing.category_id != category_id:
                    continue
                if listing.external_id in seen:
                    continue
                seen.add(listing.external_id)
                listings.append(listing)
                if len(listings) >= limit:
                    break
        if skipped:
            self.last_warnings.append(f"{skipped} listing(s) skipped: missing id, price or currency")
        if listings and getattr(self.settings, "ml_enrich_sellers", True):
            await self.enrich_sellers(listings)
        return listings

    async def health(self) -> HealthStatus:
        if not self.is_configured():
            return HealthStatus(
                configured=False,
                ok=None,
                message=(
                    "not configured: set FLIPRADAR_ML_CLIENT_ID + FLIPRADAR_ML_CLIENT_SECRET (optionally "
                    "FLIPRADAR_ML_REFRESH_TOKEN) or FLIPRADAR_ML_ACCESS_TOKEN"
                ),
            )
        details: dict[str, Any] = {}
        try:
            await self._access_token()
            state = _state_for(self.settings)
            details.update(
                token_source=state.source,
                user_id=state.user_id,
                scope=state.scope,
                refresh_token_rotated=state.rotated,
            )
            await self._get_json(
                f"/sites/{self._site}/domain_discovery/search", {"q": "iphone", "limit": "1"}, auth="optional"
            )
            details["api_reachable"] = True
        except CollectorError as exc:
            return HealthStatus(configured=True, ok=False, message=str(exc), details=details)
        try:
            await self.search_catalog("iphone", limit=1)
            details["catalog_search"] = "ok"
        except MercadoLibrePolicyError as exc:
            details["catalog_search"] = "denied_by_policy"
            return HealthStatus(configured=True, ok=False, message=str(exc), details=details)
        except CollectorError as exc:
            details["catalog_search"] = "error"
            return HealthStatus(
                configured=True,
                ok=False,
                message=f"token OK but catalog search failed: {exc}",
                details=details,
            )
        return HealthStatus(configured=True, ok=True, message="token OK; catalog search OK", details=details)
