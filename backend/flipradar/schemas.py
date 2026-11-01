"""Pydantic API schemas - this module IS the HTTP contract shared by the web dashboard and Android app.

Conventions: money is integer centavos (``*_cents``), percentages integer basis points (``*_bp``),
datetimes ISO-8601 UTC.  Changing a field here is a breaking API change: bump ``API_VERSION`` and update
API_DOCUMENTATION.md plus ``android/app/src/main/java/com/flipradar/app/data/ApiModels.kt``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

API_VERSION = "1"

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class Message(BaseModel):
    ok: bool = True
    message: str = ""


# ------------------------------------------------------------------------------------------------
# Meta / health / auth
# ------------------------------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    api_version: str = API_VERSION
    database: str
    time: datetime


class AuthCheckOut(BaseModel):
    ok: bool
    token_name: str | None = None
    auth_required: bool


# ------------------------------------------------------------------------------------------------
# Sources, searches, runs
# ------------------------------------------------------------------------------------------------


class SourceOut(ORMModel):
    key: str
    name: str
    kind: str
    country: str
    currency: str
    enabled: bool
    default_fee_profile_key: str | None
    configured: bool = False
    requires_credentials: bool = False
    capabilities: list[str] = []
    docs_url: str | None = None
    notes: str = ""
    last_run_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None


class SourceUpdate(BaseModel):
    enabled: bool | None = None
    default_fee_profile_key: str | None = None


class SourceTestOut(BaseModel):
    key: str
    configured: bool
    ok: bool | None
    message: str
    details: dict[str, Any] = {}


class TrackedSearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    category_hint: str | None = Field(default=None, max_length=64)
    source_keys: list[str] = []
    max_results: int = Field(default=50, ge=1, le=200)
    interval_minutes: int = Field(default=180, ge=15, le=10080)
    enabled: bool = True


class TrackedSearchUpdate(BaseModel):
    query: str | None = Field(default=None, min_length=2, max_length=200)
    category_hint: str | None = None
    source_keys: list[str] | None = None
    max_results: int | None = Field(default=None, ge=1, le=200)
    interval_minutes: int | None = Field(default=None, ge=15, le=10080)
    enabled: bool | None = None


class TrackedSearchOut(ORMModel):
    id: int
    query: str
    category_hint: str | None
    source_keys: list[str]
    max_results: int
    interval_minutes: int
    enabled: bool
    last_run_at: datetime | None
    created_at: datetime


class CollectRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    source_keys: list[str] = []  # empty = all enabled + configured sources with search capability
    limit: int = Field(default=50, ge=1, le=200)
    analyze: bool = True


class CollectionRunOut(ORMModel):
    id: int
    source_key: str | None = None
    query: str | None
    started_at: datetime
    finished_at: datetime | None
    status: str
    items_fetched: int
    items_new: int
    items_updated: int
    error: str | None


class CollectResult(BaseModel):
    runs: list[CollectionRunOut]
    opportunities_created: int = 0
    opportunities_updated: int = 0
    notifications_created: int = 0


# ------------------------------------------------------------------------------------------------
# Listings & products
# ------------------------------------------------------------------------------------------------


class RiskFlagOut(BaseModel):
    code: str
    severity: str
    message: str
    evidence: dict[str, Any] = {}


class ListingOut(ORMModel):
    id: int
    source_key: str
    external_id: str
    url: str | None
    title: str
    currency: str
    price_cents: int
    price_mxn_cents: int | None
    shipping_cents: int | None
    free_shipping: bool | None
    condition: str
    brand: str | None
    model: str | None
    category: str | None
    gtin: str | None
    seller_name: str | None
    location: str | None
    image_url: str | None
    available_quantity: int | None
    sold_quantity: int | None
    status: str
    product_id: int | None
    match_confidence: float | None
    match_method: str | None
    is_accessory: bool
    is_bundle: bool
    is_suspicious: bool
    risk_flags: list[RiskFlagOut] = []
    is_demo: bool
    first_seen_at: datetime
    last_seen_at: datetime


class PriceObservationOut(ORMModel):
    observed_at: datetime
    price_cents: int
    currency: str
    price_mxn_cents: int | None
    available_quantity: int | None
    sold_quantity: int | None
    status: str


class ListingDetailOut(ListingOut):
    description: str | None = None
    attributes: dict[str, Any] = {}
    seller: dict[str, Any] = {}
    observations: list[PriceObservationOut] = []


class PriceStatsOut(BaseModel):
    n: int
    median_cents: int | None
    p25_cents: int | None
    p75_cents: int | None
    min_cents: int | None
    max_cents: int | None
    mad_cents: int | None
    iqr_cents: int | None
    dispersion: float | None
    source_count: int
    seller_count: int
    excluded: dict[str, int] = {}
    comparable_ids: list[int] = []
    window_days: int | None = None


class ProductOut(ORMModel):
    id: int
    canonical_key: str
    title: str
    brand: str | None
    model: str | None
    category: str | None
    gtin: str | None
    attributes: dict[str, Any] = {}
    listing_count: int = 0
    min_price_mxn_cents: int | None = None
    median_price_mxn_cents: int | None = None
    updated_at: datetime


class ProductDetailOut(ProductOut):
    stats_by_condition: dict[str, PriceStatsOut] = {}
    listings: list[ListingOut] = []


# ------------------------------------------------------------------------------------------------
# Opportunities
# ------------------------------------------------------------------------------------------------


class CostLineOut(BaseModel):
    key: str
    label: str
    amount_cents: int
    formula: str


class ProfitResultOut(BaseModel):
    sale_price_cents: int
    acquisition_lines: list[CostLineOut]
    selling_lines: list[CostLineOut]
    total_acquisition_cents: int
    total_selling_costs_cents: int
    net_proceeds_cents: int
    net_profit_cents: int
    roi_bp: int | None
    margin_bp: int | None
    fee_profile_key: str
    assumptions: list[str] = []


class ScoreComponentOut(BaseModel):
    key: str
    label: str
    raw_value: float | None
    points: float
    max_points: float
    explanation: str


class DemandOut(BaseModel):
    level: str
    confidence: str | None
    signals: list[dict[str, Any]] = []
    note: str = ""


class OpportunityOut(BaseModel):
    id: int
    product_id: int
    product_title: str
    product_key: str
    category: str | None
    buy_listing_id: int
    buy_title: str
    buy_url: str | None
    buy_source_key: str
    buy_condition: str
    image_url: str | None
    fee_profile_key: str
    buy_total_cents: int
    expected_sale_cents: int
    net_profit_cents: int
    roi_bp: int
    margin_bp: int
    score: float
    confidence: str
    comparable_count: int
    risk_level: Literal["none", "info", "warning", "critical"]
    demand_level: str
    status: str
    is_demo: bool
    created_at: datetime
    updated_at: datetime


class OpportunityDetailOut(OpportunityOut):
    price_stats: PriceStatsOut
    cost_breakdown: ProfitResultOut
    score_components: list[ScoreComponentOut]
    risk_flags: list[RiskFlagOut]
    demand: DemandOut
    explanation: str
    facts: dict[str, Any]
    ai_summary: str | None
    comparables: list[ListingOut] = []


class OpportunityUpdate(BaseModel):
    status: Literal["new", "seen", "watching", "dismissed", "bought", "expired"]


class OpportunityBuyIn(BaseModel):
    quantity: int = Field(default=1, ge=1, le=1000)
    unit_cost_cents: int | None = Field(default=None, ge=0)  # default: listing price
    extra_costs_cents: int | None = Field(default=None, ge=0)  # default: listing shipping (if known)
    notes: str | None = Field(default=None, max_length=2000)


class AiSummaryOut(BaseModel):
    opportunity_id: int
    provider: str
    summary: str | None
    validated: bool  # True only if every number in the summary appears in the deterministic facts
    message: str = ""


class RecomputeResult(BaseModel):
    products_analyzed: int
    opportunities_created: int
    opportunities_updated: int
    opportunities_expired: int
    notifications_created: int


# ------------------------------------------------------------------------------------------------
# Calculator & fee profiles
# ------------------------------------------------------------------------------------------------


class FixedFeeTierIO(BaseModel):
    max_price_cents: int | None = Field(default=None, ge=0)
    fee_cents: int = Field(ge=0)


class FeeProfileIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]{2,64}$")
    name: str = Field(min_length=1, max_length=128)
    channel: str = Field(default="custom", max_length=64)
    commission_bp: int = Field(default=0, ge=0, le=10000)
    fixed_fee_tiers: list[FixedFeeTierIO] = []
    payment_fee_bp: int = Field(default=0, ge=0, le=10000)
    payment_fixed_cents: int = Field(default=0, ge=0)
    iva_on_fees: bool = False
    iva_rate_bp: int = Field(default=1600, ge=0, le=10000)
    seller_shipping_cents: int = Field(default=0, ge=0)
    free_shipping_threshold_cents: int | None = Field(default=None, ge=0)
    isr_withholding_bp: int = Field(default=0, ge=0, le=10000)
    iva_withholding_bp: int = Field(default=0, ge=0, le=10000)
    source_url: str | None = Field(default=None, max_length=1000)
    as_of: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=4000)


class FeeProfileOut(FeeProfileIn):
    is_builtin: bool = False
    updated_at: datetime | None = None


class CalculatorIn(BaseModel):
    buy_price_cents: int = Field(ge=0)
    sale_price_cents: int = Field(ge=0)
    fee_profile_key: str | None = None
    fee_profile: FeeProfileIn | None = None  # inline profile (overrides fee_profile_key)
    buy_shipping_cents: int = Field(default=0, ge=0)
    import_costs_cents: int = Field(default=0, ge=0)
    other_buy_costs_cents: int = Field(default=0, ge=0)
    other_sale_costs_cents: int = Field(default=0, ge=0)
    seller_shipping_override_cents: int | None = Field(default=None, ge=0)
    sale_price_includes_iva: bool = True
    treat_withholdings_as_cost: bool = True
    target_roi_bp: int | None = Field(default=None, ge=0, le=100000)


class CalculatorOut(BaseModel):
    result: ProfitResultOut
    break_even_sale_cents: int | None
    max_buy_for_target_cents: int | None
    target_roi_bp: int | None


class ImportCostIn(BaseModel):
    item_value_usd_cents: int = Field(ge=0)
    shipping_usd_cents: int = Field(default=0, ge=0)
    rule_key: str = "courier_usmca"
    usd_mxn_rate: str | None = None  # decimal string; default = latest stored FX rate


class ImportCostOut(BaseModel):
    rule_key: str
    rule_name: str
    customs_value_usd_cents: int
    usd_mxn_rate: str
    fx_source: str
    duty_rate_bp: int
    duties_mxn_cents: int
    customs_value_mxn_cents: int
    notes: list[str]
    source_url: str | None
    as_of: str | None


# ------------------------------------------------------------------------------------------------
# Inventory & sales
# ------------------------------------------------------------------------------------------------


class InventoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    sku: str | None = Field(default=None, max_length=64)
    product_id: int | None = None
    opportunity_id: int | None = None
    condition: Literal["new", "used", "refurbished", "for_parts", "unknown"] = "unknown"
    quantity: int = Field(default=1, ge=1, le=100000)
    unit_cost_cents: int = Field(ge=0)
    extra_costs_cents: int = Field(default=0, ge=0)
    purchase_source: str | None = Field(default=None, max_length=120)
    purchase_url: str | None = Field(default=None, max_length=1000)
    purchased_at: datetime | None = None
    status: Literal["purchased", "in_transit", "in_stock", "listed"] = "purchased"
    target_sale_cents: int | None = Field(default=None, ge=0)
    listed_channel: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)


class InventoryUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    sku: str | None = Field(default=None, max_length=64)
    condition: Literal["new", "used", "refurbished", "for_parts", "unknown"] | None = None
    unit_cost_cents: int | None = Field(default=None, ge=0)
    extra_costs_cents: int | None = Field(default=None, ge=0)
    status: Literal["purchased", "in_transit", "in_stock", "listed", "returned", "written_off"] | None = None
    target_sale_cents: int | None = Field(default=None, ge=0)
    listed_channel: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)


class SaleIn(BaseModel):
    quantity: int = Field(default=1, ge=1)
    unit_price_cents: int = Field(ge=0)
    channel: str | None = Field(default=None, max_length=64)
    fee_profile_key: str | None = None  # compute fees with the calculator
    fees_cents: int | None = Field(default=None, ge=0)  # override: total fees actually charged
    shipping_cents: int = Field(default=0, ge=0)  # total shipping paid by seller
    taxes_withheld_cents: int | None = Field(default=None, ge=0)  # override
    other_costs_cents: int = Field(default=0, ge=0)
    sold_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class SaleOut(ORMModel):
    id: int
    inventory_item_id: int
    quantity: int
    unit_price_cents: int
    channel: str | None
    fee_profile_key: str | None
    fees_cents: int
    shipping_cents: int
    taxes_withheld_cents: int
    other_costs_cents: int
    cost_basis_cents: int
    net_proceeds_cents: int
    profit_cents: int
    sold_at: datetime
    notes: str | None


class InventoryOut(ORMModel):
    id: int
    title: str
    sku: str | None
    product_id: int | None
    opportunity_id: int | None
    condition: str
    quantity: int
    quantity_available: int
    unit_cost_cents: int
    extra_costs_cents: int
    total_cost_cents: int = 0
    purchase_source: str | None
    purchase_url: str | None
    purchased_at: datetime
    status: str
    target_sale_cents: int | None
    listed_channel: str | None
    location: str | None
    notes: str | None
    days_held: int = 0
    realized_profit_cents: int = 0
    sales: list[SaleOut] = []
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------------------------------------
# Analytics
# ------------------------------------------------------------------------------------------------


class AnalyticsSummaryOut(BaseModel):
    period_days: int
    revenue_cents: int
    realized_profit_cents: int
    units_sold: int
    sales_count: int
    avg_roi_bp: int | None  # profit / cost basis over the period's sales
    avg_days_to_sell: float | None
    inventory_units: int
    inventory_cost_cents: int  # capital tied up (cost basis of unsold units)
    inventory_target_value_cents: int | None
    aging: dict[str, int]  # "0-7","8-30","31-90","90+" -> units
    open_opportunities: int
    avg_open_opportunity_score: float | None
    potential_profit_open_cents: int
    opportunities_by_source: dict[str, int]
    top_products_by_profit: list[dict[str, Any]]
    listings_tracked: int
    products_tracked: int
    last_collection_at: datetime | None


class TimeseriesPoint(BaseModel):
    period: str  # YYYY-MM-DD (start of bucket)
    revenue_cents: int
    profit_cents: int
    units: int
    purchases_cents: int


class TimeseriesOut(BaseModel):
    bucket: Literal["day", "week", "month"]
    points: list[TimeseriesPoint]


# ------------------------------------------------------------------------------------------------
# Alerts & notifications
# ------------------------------------------------------------------------------------------------


class AlertRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    keywords: str | None = Field(default=None, max_length=200)
    brand: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)
    min_profit_cents: int | None = Field(default=None, ge=0)
    min_roi_bp: int | None = Field(default=None, ge=0)
    min_score: float | None = Field(default=None, ge=0, le=100)
    max_buy_cents: int | None = Field(default=None, ge=0)
    min_confidence: Literal["low", "medium", "high"] | None = None
    exclude_suspicious: bool = True
    channels: list[Literal["in_app", "ntfy", "telegram", "webhook"]] = ["in_app"]
    enabled: bool = True


class AlertRuleOut(AlertRuleIn, ORMModel):
    id: int
    created_at: datetime


class NotificationOut(ORMModel):
    id: int
    rule_id: int | None
    opportunity_id: int | None
    kind: str
    title: str
    body: str
    created_at: datetime
    read_at: datetime | None


# ------------------------------------------------------------------------------------------------
# Import / URL check / settings / FX
# ------------------------------------------------------------------------------------------------


class ImportResult(BaseModel):
    rows_total: int
    rows_imported: int
    rows_updated: int
    rows_rejected: int
    errors: list[str] = []


class UrlCheckIn(BaseModel):
    url: HttpUrl
    save: bool = True


class UrlCheckOut(BaseModel):
    url: str
    allowed_by_robots: bool
    found: bool
    listing: ListingOut | None = None
    extracted: dict[str, Any] = {}
    message: str = ""


class SettingsOut(BaseModel):
    min_comparables: int
    comparable_max_age_days: int
    default_fee_profile_key: str
    target_roi_bp: int
    min_profit_cents: int
    fx_usd_mxn_manual: str | None
    include_demo_data: bool
    ai_provider: str
    scheduler_enabled: bool


class SettingsUpdate(BaseModel):
    min_comparables: int | None = Field(default=None, ge=2, le=100)
    comparable_max_age_days: int | None = Field(default=None, ge=1, le=365)
    default_fee_profile_key: str | None = None
    target_roi_bp: int | None = Field(default=None, ge=0, le=100000)
    min_profit_cents: int | None = Field(default=None, ge=0)
    fx_usd_mxn_manual: str | None = None
    include_demo_data: bool | None = None


class FxOut(BaseModel):
    base: str
    quote: str
    rate: str
    rate_date: str
    source: str
