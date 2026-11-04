"""SQLAlchemy ORM models.

Conventions
-----------
* money: integer centavos in ``*_cents`` columns (MXN unless a ``currency`` column says otherwise)
* percentages: integer basis points in ``*_bp`` columns (1600 = 16 %)
* timestamps: timezone-aware UTC
* JSON columns hold structured, *derived* data (breakdowns, flags, evidence).  Raw source payloads are
  stored truncated in ``raw_json`` for auditability.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Condition(enum.StrEnum):
    NEW = "new"
    USED = "used"
    REFURBISHED = "refurbished"
    FOR_PARTS = "for_parts"
    UNKNOWN = "unknown"


class SourceKind(enum.StrEnum):
    MARKETPLACE = "marketplace"  # e.g. Mercado Libre, eBay
    RETAILER = "retailer"  # single-merchant store page (JSON-LD check)
    FEED = "feed"  # RSS deal feeds
    MANUAL = "manual"  # user typed / CSV import
    REFERENCE = "reference"  # reference prices (e.g. government datasets)
    DEMO = "demo"  # synthetic data, never mixed into real analysis unless explicitly enabled


class ListingStatus(enum.StrEnum):
    ACTIVE = "active"
    ENDED = "ended"  # no longer returned by the source / marked closed
    SOLD = "sold"  # source explicitly reports sold
    UNKNOWN = "unknown"


class OpportunityStatus(enum.StrEnum):
    NEW = "new"
    SEEN = "seen"
    WATCHING = "watching"
    DISMISSED = "dismissed"
    BOUGHT = "bought"
    EXPIRED = "expired"


class InventoryStatus(enum.StrEnum):
    PURCHASED = "purchased"
    IN_TRANSIT = "in_transit"
    IN_STOCK = "in_stock"
    LISTED = "listed"
    SOLD = "sold"  # fully sold (quantity_available == 0)
    RETURNED = "returned"
    WRITTEN_OFF = "written_off"


# ------------------------------------------------------------------------------------------------
# Sources & collection
# ------------------------------------------------------------------------------------------------


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32), default=SourceKind.MARKETPLACE.value)
    country: Mapped[str] = mapped_column(String(2), default="MX")
    currency: Mapped[str] = mapped_column(String(3), default="MXN")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Fee profile used when this source is the RESALE channel (e.g. "mercadolibre_clasica").
    default_fee_profile_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listings: Mapped[list[Listing]] = relationship(back_populates="source")


class TrackedSearch(Base):
    """A query that the scheduler runs periodically against one or more sources."""

    __tablename__ = "tracked_searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(String(200))
    category_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_keys: Mapped[list[str]] = mapped_column(JSON, default=list)  # empty = all enabled sources
    max_results: Mapped[int] = mapped_column(Integer, default=50)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=180)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CollectionRun(Base):
    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    tracked_search_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_searches.id", ondelete="SET NULL"), nullable=True
    )
    query: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|error|skipped
    items_fetched: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_updated: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


# ------------------------------------------------------------------------------------------------
# Products & listings
# ------------------------------------------------------------------------------------------------


class Product(Base):
    """Canonical product cluster: listings that refer to the *same sellable thing*."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    brand: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    gtin: Mapped[str | None] = mapped_column(String(14), nullable=True, index=True)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    listings: Mapped[list[Listing]] = relationship(back_populates="product")


class Listing(Base):
    """One offer observed on one source.  Price history lives in :class:`PriceObservation`."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_listing_source_external"),
        Index("ix_listing_product_condition", "product_id", "condition"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    normalized_title: Mapped[str] = mapped_column(String(500), default="")
    currency: Mapped[str] = mapped_column(String(3), default="MXN")
    price_cents: Mapped[int] = mapped_column(BigInteger)  # in `currency`
    # Converted price in MXN (None when no FX rate was available -> excluded from comparisons).
    price_mxn_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fx_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # informational only
    fx_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shipping_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # None = unknown
    free_shipping: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    condition: Mapped[str] = mapped_column(String(16), default=Condition.UNKNOWN.value)
    brand: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_category_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gtin: Mapped[str | None] = mapped_column(String(14), nullable=True, index=True)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    seller_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seller_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Free-form seller signals as reported by the source (reputation level, transactions, age...).
    seller_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sold_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)  # as reported by source
    listing_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_accessory: Mapped[bool] = mapped_column(Boolean, default=False)
    is_bundle: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default=ListingStatus.ACTIVE.value, index=True)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0..1
    match_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_suspicious: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_flags_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    source: Mapped[Source] = relationship(back_populates="listings")
    product: Mapped[Product | None] = relationship(back_populates="listings")
    observations: Mapped[list[PriceObservation]] = relationship(
        back_populates="listing", cascade="all, delete-orphan", order_by="PriceObservation.observed_at"
    )


class PriceObservation(Base):
    __tablename__ = "price_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    price_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="MXN")
    price_mxn_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    available_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sold_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=ListingStatus.ACTIVE.value)

    listing: Mapped[Listing] = relationship(back_populates="observations")


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("base", "quote", "rate_date", "source", name="uq_fx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base: Mapped[str] = mapped_column(String(3))
    quote: Mapped[str] = mapped_column(String(3))
    rate_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    rate: Mapped[str] = mapped_column(String(32))  # Decimal as string, exact
    source: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------------------------------------------
# Fees
# ------------------------------------------------------------------------------------------------


class FeeProfile(Base):
    """Cost model for selling on a channel.  Every number is user-editable and carries its source."""

    __tablename__ = "fee_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    channel: Mapped[str] = mapped_column(String(64))  # mercadolibre | amazon_mx | facebook | local | custom
    commission_bp: Mapped[int] = mapped_column(Integer, default=0)
    # Fixed per-unit fee tiers: [{"max_price_cents": 29900, "fee_cents": 2500}, ...] sorted ascending;
    # the first tier whose max_price_cents >= sale price applies; None max = no upper bound.
    fixed_fee_tiers_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    payment_fee_bp: Mapped[int] = mapped_column(Integer, default=0)
    payment_fixed_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    # True when the channel adds 16 % IVA on top of its commission/fees (common in Mexico).
    iva_on_fees: Mapped[bool] = mapped_column(Boolean, default=False)
    iva_rate_bp: Mapped[int] = mapped_column(Integer, default=1600)
    # Default shipping cost paid by the seller per unit (0 for local pickup).
    seller_shipping_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    # Price at/above which the seller must offer free shipping (None = never mandatory).
    free_shipping_threshold_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Tax withholdings applied by the platform to individual sellers (persona fisica).
    isr_withholding_bp: Mapped[int] = mapped_column(Integer, default=0)
    iva_withholding_bp: Mapped[int] = mapped_column(Integer, default=0)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    as_of: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ------------------------------------------------------------------------------------------------
# Opportunities
# ------------------------------------------------------------------------------------------------


class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (UniqueConstraint("buy_listing_id", "fee_profile_key", name="uq_opp_listing_profile"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    buy_listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    fee_profile_key: Mapped[str] = mapped_column(String(64))
    buy_total_cents: Mapped[int] = mapped_column(BigInteger)  # price + shipping (+ import costs)
    expected_sale_cents: Mapped[int] = mapped_column(BigInteger)
    net_profit_cents: Mapped[int] = mapped_column(BigInteger, index=True)
    roi_bp: Mapped[int] = mapped_column(Integer, index=True)
    margin_bp: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float, index=True)  # 0..100
    confidence: Mapped[str] = mapped_column(String(16))  # high|medium|low
    comparable_count: Mapped[int] = mapped_column(Integer)
    comparable_listing_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    price_stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cost_breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    score_components_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    risk_flags_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    risk_level: Mapped[str] = mapped_column(String(16), default="none", index=True)  # none|info|warning|critical
    # engine.risk.is_suspicious: any critical flag or >= 2 warnings (hidden from default lists/alerts).
    is_suspicious: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    demand_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    explanation: Mapped[str] = mapped_column(Text, default="")
    explanation_facts_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=OpportunityStatus.NEW.value, index=True)
    # The user's seen/watching status when the analysis expired the row; restored if the listing comes back.
    # Added after the first release: services/bootstrap.ensure_added_columns adds it to existing databases.
    status_before_expiry: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    product: Mapped[Product] = relationship()
    buy_listing: Mapped[Listing] = relationship()


# ------------------------------------------------------------------------------------------------
# Alerts & notifications
# ------------------------------------------------------------------------------------------------


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    keywords: Mapped[str | None] = mapped_column(String(200), nullable=True)  # all words must match title
    brand: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    min_profit_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    min_roi_bp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_buy_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    min_confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)  # low|medium|high
    exclude_suspicious: Mapped[bool] = mapped_column(Boolean, default=True)
    channels: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["in_app"])  # in_app|ntfy|telegram|webhook
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    # AUTOINCREMENT: ids are never reused after the newest rows are deleted (clear-demo, cascades), so a poller's
    # ``since_id`` cursor does not skip notifications.  services/bootstrap.ensure_monotonic_notification_ids
    # rebuilds tables created before this option existed.
    __table_args__ = (
        UniqueConstraint("rule_id", "opportunity_id", name="uq_notification_rule_opp"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True)
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="opportunity")  # opportunity|system
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # channel -> status


# ------------------------------------------------------------------------------------------------
# Inventory & sales
# ------------------------------------------------------------------------------------------------


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True
    )
    condition: Mapped[str] = mapped_column(String(16), default=Condition.UNKNOWN.value)
    quantity: Mapped[int] = mapped_column(Integer, default=1)  # units purchased
    quantity_available: Mapped[int] = mapped_column(Integer, default=1)  # units not yet sold/returned
    unit_cost_cents: Mapped[int] = mapped_column(BigInteger)  # purchase price per unit
    extra_costs_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # shipping/import/repairs (total)
    purchase_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    purchase_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default=InventoryStatus.PURCHASED.value, index=True)
    target_sale_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    listed_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    # When the status became written_off (the date the remaining cost is booked as a realized loss).  Added
    # after the first release: services/bootstrap.ensure_added_columns adds it to existing databases.
    written_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)

    sales: Mapped[list[Sale]] = relationship(back_populates="item", cascade="all, delete-orphan")


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger)
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fee_profile_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fees_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    shipping_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    taxes_withheld_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    other_costs_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_basis_cents: Mapped[int] = mapped_column(BigInteger)  # allocated purchase cost of units sold
    net_proceeds_cents: Mapped[int] = mapped_column(BigInteger)
    profit_cents: Mapped[int] = mapped_column(BigInteger)
    breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    item: Mapped[InventoryItem] = relationship(back_populates="sales")


# ------------------------------------------------------------------------------------------------
# Auth & settings
# ------------------------------------------------------------------------------------------------


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    token_prefix: Mapped[str] = mapped_column(String(12), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class CategoryFee(Base):
    """Exact marketplace commission per category, fetched from the marketplace API (e.g. Mercado Libre
    ``/sites/MLM/listing_prices``) and cached.  Used by analysis to replace a fee profile's generic
    commission with the real per-category rate.  Rows are refreshed when older than the cache TTL."""

    __tablename__ = "category_fees"
    __table_args__ = (UniqueConstraint("source_key", "category_id", "listing_type_id", name="uq_category_fee"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(64))  # e.g. "mercadolibre"
    category_id: Mapped[str] = mapped_column(String(64))  # e.g. "MLM1055"
    category_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    listing_type_id: Mapped[str] = mapped_column(String(32))  # gold_special (Clasica) | gold_pro (Premium)
    commission_bp: Mapped[int] = mapped_column(Integer)
    sample_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sample_fixed_fee_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
