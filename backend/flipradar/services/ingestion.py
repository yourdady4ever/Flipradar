"""Ingestion: RawListing (collector/CSV output) -> normalized Listing rows, product clusters and price history.

Pipeline per raw listing::

    validate -> normalize_listing() -> ProductMatcher.match() -> get/create Product
             -> upsert Listing on (source_id, external_id) -> FX conversion -> PriceObservation

Rules
-----
* Nothing is invented: fields the source did not report stay ``None``.
* Non-MXN prices are converted only with a stored/manual FX rate; otherwise ``price_mxn_cents`` is None
  and the listing is excluded from comparisons.
* A PriceObservation is appended when price/status/quantities changed or the previous observation is
  older than 6 h (so history has a regular cadence without duplicating identical polls).  Older
  observations (e.g. a CSV with history) are inserted as history without overwriting newer current values.
* Demo data lives in its own product namespace (``demo|`` canonical keys) and is never matched against
  real products, and vice versa.
* Only ``http(s)`` URLs are stored (a ``javascript:`` URL from a CSV must never reach the dashboard).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..engine.types import MatchResult, NormalizedAttributes, RawListing
from ..matching import matcher as matcher_mod
from ..models import Condition, Listing, ListingStatus, PriceObservation, Product, Source
from ..normalize import extract as normalize_mod
from . import fx as fx_service
from .jsonutil import jsonable, truncate_json
from .timeutil import as_utc, utcnow

log = logging.getLogger(__name__)

DEMO_KEY_PREFIX = "demo|"
DEMO_SOURCE_KEY = "demo"
OBSERVATION_REFRESH = timedelta(hours=6)
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_CONDITIONS = {c.value for c in Condition}
_STATUSES = {s.value for s in ListingStatus}
_PRODUCT_ATTR_EXCLUDE = {"normalized_title", "condition", "is_accessory", "is_bundle", "color"}
_CHUNK = 500


@dataclass
class IngestStats:
    new: int = 0
    updated: int = 0
    skipped: int = 0
    product_ids_touched: set[int] = field(default_factory=set)
    listing_ids: list[int] = field(default_factory=list)  # in input order (accepted rows only)
    errors: list[str] = field(default_factory=list)


def is_demo_product_key(key: str | None) -> bool:
    return bool(key) and str(key).startswith(DEMO_KEY_PREFIX)


def safe_url(value: str | None, max_len: int = 1000) -> str | None:
    """Keep only absolute http(s) URLs."""
    if not value:
        return None
    text = str(value).strip()
    if len(text) > max_len or not re.match(r"^https?://[^\s]+$", text, flags=re.IGNORECASE):
        return None
    return text


def clean_title(title: str, max_len: int = 300) -> str:
    return re.sub(r"\s+", " ", title or "").strip()[:max_len]


def _product_candidate(product: Product) -> matcher_mod.ProductCandidate:
    return matcher_mod.ProductCandidate(
        key=product.canonical_key,
        title=product.title,
        brand=product.brand,
        model=product.model,
        gtin=product.gtin,
        attributes=dict(product.attributes_json or {}),
    )


def build_matcher(session: Session, *, demo: bool = False) -> matcher_mod.ProductMatcher:
    """Matcher seeded with existing products of the same namespace (demo vs real)."""
    products = session.scalars(select(Product).order_by(Product.id)).all()
    candidates = [_product_candidate(p) for p in products if is_demo_product_key(p.canonical_key) == demo]
    return matcher_mod.ProductMatcher(candidates)


def _product_attributes(attrs: NormalizedAttributes) -> dict[str, Any]:
    return jsonable({k: v for k, v in attrs.to_dict().items() if k not in _PRODUCT_ATTR_EXCLUDE})


def _listing_attributes(raw: RawListing, attrs: NormalizedAttributes, match: MatchResult) -> dict[str, Any]:
    normalized = {
        "storage_gb": attrs.storage_gb,
        "ram_gb": attrs.ram_gb,
        "color": attrs.color,
        "variant_tokens": list(attrs.variant_tokens or []),
        "canonical_key": attrs.canonical_key,
    }
    data: dict[str, Any] = {
        "normalized": normalized,
        "match": {"method": match.method, "confidence": match.confidence, "reasons": list(match.reasons or [])[:20]},
    }
    if raw.category_name:
        data["category_name"] = str(raw.category_name)[:200]
    if raw.attributes:
        data["source_attributes"] = truncate_json(dict(raw.attributes), max_bytes=8_000, max_field_bytes=1_000)
    return jsonable(data)


def _validate(raw: RawListing) -> str | None:
    if not raw.external_id or not str(raw.external_id).strip():
        return "missing external_id"
    if len(str(raw.external_id)) > 128:
        return "external_id longer than 128 characters"
    if not raw.title or not str(raw.title).strip():
        return "missing title"
    if isinstance(raw.price_cents, bool) or not isinstance(raw.price_cents, int):
        return "price must be an integer amount of centavos"
    if raw.price_cents <= 0:
        return "price must be greater than zero"
    if not _CURRENCY_RE.match((raw.currency or "").upper()):
        return f"invalid currency {raw.currency!r}"
    if raw.shipping_cents is not None and (not isinstance(raw.shipping_cents, int) or raw.shipping_cents < 0):
        return "shipping must be a non-negative integer amount of centavos"
    return None


def _non_negative(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


class _ObsKey:
    """Comparable snapshot of the observable state of a listing."""

    __slots__ = ("observed_at", "price_cents", "currency", "available_quantity", "sold_quantity", "status")

    def __init__(self, observed_at: datetime, price_cents: int, currency: str, available: int | None,
                 sold: int | None, status: str) -> None:
        self.observed_at = observed_at
        self.price_cents = price_cents
        self.currency = currency
        self.available_quantity = available
        self.sold_quantity = sold
        self.status = status

    def same_values(self, other: _ObsKey) -> bool:
        return (
            self.price_cents == other.price_cents
            and self.currency == other.currency
            and self.available_quantity == other.available_quantity
            and self.sold_quantity == other.sold_quantity
            and self.status == other.status
        )


def _chunks(items: list[Any], size: int = _CHUNK) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _preload(session: Session, source_id: int, external_ids: list[str]) -> tuple[dict[str, Listing], dict[int, _ObsKey]]:
    listings: dict[str, Listing] = {}
    for chunk in _chunks(external_ids):
        for row in session.scalars(
            select(Listing).where(Listing.source_id == source_id, Listing.external_id.in_(chunk))
        ):
            listings[row.external_id] = row
    last_obs: dict[int, _ObsKey] = {}
    ids = [row.id for row in listings.values()]
    for chunk in _chunks(ids):
        sub = (
            select(PriceObservation.listing_id, func.max(PriceObservation.observed_at).label("latest"))
            .where(PriceObservation.listing_id.in_(chunk))
            .group_by(PriceObservation.listing_id)
            .subquery()
        )
        rows = session.scalars(
            select(PriceObservation)
            .join(sub, and_(PriceObservation.listing_id == sub.c.listing_id, PriceObservation.observed_at == sub.c.latest))
            .order_by(PriceObservation.id)
        )
        for obs in rows:
            last_obs[obs.listing_id] = _ObsKey(
                as_utc(obs.observed_at), obs.price_cents, obs.currency, obs.available_quantity, obs.sold_quantity, obs.status
            )
    return listings, last_obs


def ingest_raw_listings(
    session: Session,
    source_key: str,
    raws: list[RawListing],
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> IngestStats:
    """Upsert ``raws`` for ``source_key``.  Flushes but does not commit.

    Raises LookupError when the source does not exist.  ``NotImplementedError`` from an unfinished
    normalizer/matcher propagates (it is a deployment problem, not a data problem).
    """
    now = as_utc(now or utcnow())
    source = session.scalars(select(Source).where(Source.key == source_key)).first()
    if source is None:
        raise LookupError(f"unknown source '{source_key}'")
    stats = IngestStats()
    if not raws:
        return stats

    is_demo = source_key == DEMO_SOURCE_KEY
    matcher = build_matcher(session, demo=is_demo)
    products: dict[str, Product] = {}
    usd_rate = fx_service.latest_usd_mxn(session, settings)
    external_ids = sorted({str(r.external_id).strip() for r in raws if r.external_id and str(r.external_id).strip()})
    listings, last_obs = _preload(session, source.id, external_ids)

    for index, raw in enumerate(raws):
        problem = _validate(raw)
        if problem is not None:
            stats.skipped += 1
            stats.errors.append(f"item {index + 1} ({str(raw.external_id)[:40]}): {problem}")
            continue
        try:
            attrs = normalize_mod.normalize_listing(raw)
            match = matcher.match(attrs, raw.title)
        except NotImplementedError:
            raise
        except Exception as exc:  # one malformed listing must not abort a whole batch
            log.warning("normalization failed for %s/%s: %s", source_key, raw.external_id, exc)
            stats.skipped += 1
            stats.errors.append(f"item {index + 1} ({str(raw.external_id)[:40]}): normalization failed")
            continue

        product = _resolve_product(session, matcher, products, match, attrs, raw, is_demo)
        external_id = str(raw.external_id).strip()
        observed_at = as_utc(raw.observed_at) if raw.observed_at else now
        currency = raw.currency.upper()
        rate, fx_source = fx_service.rate_for_currency(currency, usd_rate)
        price_mxn = fx_service.convert_to_mxn_cents(raw.price_cents, currency, rate)
        status = raw.status if raw.status in _STATUSES else ListingStatus.UNKNOWN.value
        available = _non_negative(raw.available_quantity)
        sold = _non_negative(raw.sold_quantity)

        listing = listings.get(external_id)
        is_new = listing is None
        if is_new:
            listing = Listing(source_id=source.id, external_id=external_id, first_seen_at=observed_at,
                              last_seen_at=observed_at, is_demo=is_demo)
            session.add(listing)
            listings[external_id] = listing
            stats.new += 1
            apply_current = True
        else:
            stats.updated += 1
            previous_seen = as_utc(listing.last_seen_at) if listing.last_seen_at else None
            apply_current = previous_seen is None or observed_at >= previous_seen
            if listing.product_id is not None:
                stats.product_ids_touched.add(listing.product_id)
            first_seen = as_utc(listing.first_seen_at) if listing.first_seen_at else observed_at
            if observed_at < first_seen:
                listing.first_seen_at = observed_at

        if apply_current:
            listing.url = safe_url(raw.url)
            listing.title = clean_title(raw.title, 500)
            listing.normalized_title = (attrs.normalized_title or "")[:500]
            listing.currency = currency
            listing.price_cents = int(raw.price_cents)
            listing.price_mxn_cents = price_mxn
            listing.fx_rate = float(rate) if (rate is not None and currency != "MXN") else None
            listing.fx_source = fx_source if currency != "MXN" else None
            listing.shipping_cents = raw.shipping_cents
            listing.free_shipping = raw.free_shipping
            listing.condition = attrs.condition if attrs.condition in _CONDITIONS else Condition.UNKNOWN.value
            listing.brand = (attrs.brand or raw.brand or None)
            listing.model = (attrs.model or raw.model or None)
            listing.category = attrs.category
            listing.source_category_id = (str(raw.category_id)[:64] if raw.category_id else None)
            listing.gtin = (attrs.gtin or None)
            listing.attributes_json = _listing_attributes(raw, attrs, match)
            listing.seller_id = (str(raw.seller_id)[:64] if raw.seller_id else None)
            listing.seller_name = (str(raw.seller_name)[:200] if raw.seller_name else None)
            listing.seller_json = truncate_json(dict(raw.seller or {}), max_bytes=4_000, max_field_bytes=1_000)
            listing.location = (str(raw.location)[:200] if raw.location else None)
            listing.image_url = safe_url(raw.image_url)
            listing.description = (str(raw.description)[:5000] if raw.description else None)
            listing.available_quantity = available
            listing.sold_quantity = sold
            listing.listing_type = (str(raw.listing_type)[:64] if raw.listing_type else None)
            listing.is_accessory = bool(attrs.is_accessory)
            listing.is_bundle = bool(attrs.is_bundle)
            listing.status = status
            listing.product_id = product.id if product is not None else None
            listing.match_confidence = float(match.confidence) if product is not None else None
            listing.match_method = (match.method or "none")[:32]
            listing.last_seen_at = observed_at
            listing.raw_json = truncate_json(dict(raw.raw or {}))
            if is_demo:
                listing.is_demo = True

        session.flush()  # assigns listing.id
        stats.listing_ids.append(listing.id)
        if listing.product_id is not None:
            stats.product_ids_touched.add(listing.product_id)

        snapshot = _ObsKey(observed_at, int(raw.price_cents), currency, available, sold, status)
        previous = last_obs.get(listing.id)
        if _should_observe(session, listing.id, previous, snapshot):
            session.add(
                PriceObservation(
                    listing_id=listing.id,
                    observed_at=observed_at,
                    price_cents=int(raw.price_cents),
                    currency=currency,
                    price_mxn_cents=price_mxn,
                    available_quantity=available,
                    sold_quantity=sold,
                    status=status,
                )
            )
            if previous is None or observed_at >= previous.observed_at:
                last_obs[listing.id] = snapshot
    session.flush()
    return stats


def _should_observe(session: Session, listing_id: int, previous: _ObsKey | None, snapshot: _ObsKey) -> bool:
    if previous is None:
        return True
    if snapshot.observed_at < previous.observed_at:
        # Historical backfill: insert unless an observation at that exact instant already exists.
        exists = session.scalar(
            select(func.count(PriceObservation.id)).where(
                PriceObservation.listing_id == listing_id, PriceObservation.observed_at == snapshot.observed_at
            )
        )
        return not exists
    if not snapshot.same_values(previous):
        return True
    return snapshot.observed_at - previous.observed_at >= OBSERVATION_REFRESH


def _resolve_product(
    session: Session,
    matcher: matcher_mod.ProductMatcher,
    cache: dict[str, Product],
    match: MatchResult,
    attrs: NormalizedAttributes,
    raw: RawListing,
    is_demo: bool,
) -> Product | None:
    key = match.product_key
    if not key:
        return None
    key = str(key)
    if is_demo and not is_demo_product_key(key):
        key = DEMO_KEY_PREFIX + key
    if not is_demo and is_demo_product_key(key):  # never attach real listings to demo clusters
        return None
    key = key[:255]
    product = cache.get(key)
    if product is None:
        product = session.scalars(select(Product).where(Product.canonical_key == key)).first()
    if product is None:
        title = clean_title(raw.title, 300)
        if is_demo and not title.startswith("[DEMO]"):
            title = ("[DEMO] " + title)[:300]
        product = Product(
            canonical_key=key,
            title=title,
            brand=(attrs.brand or raw.brand or None),
            model=(attrs.model or raw.model or None),
            category=attrs.category,
            gtin=attrs.gtin,
            attributes_json=_product_attributes(attrs),
        )
        session.add(product)
        session.flush()
        try:
            matcher.add(_product_candidate(product))
        except NotImplementedError:
            raise
        except Exception:  # pragma: no cover - defensive; the matcher can still match by key next time
            log.warning("matcher.add failed for %s", key, exc_info=True)
    else:
        # Fill gaps on an existing cluster without overwriting what is already known.
        if product.brand is None and attrs.brand:
            product.brand = attrs.brand
        if product.model is None and attrs.model:
            product.model = attrs.model
        if product.category is None and attrs.category:
            product.category = attrs.category
        if product.gtin is None and attrs.gtin:
            product.gtin = attrs.gtin
    cache[key] = product
    return product


def to_decimal_rate(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(repr(value))
