"""Plain data types shared by collectors, normalization, matching and the opportunity engine.

These are deliberately framework-free dataclasses so every engine stage can be unit-tested without a
database.  The ingestion service converts between these and ORM rows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

Severity = Literal["info", "warning", "critical"]
ConfidenceLevel = Literal["high", "medium", "low"]
DemandLevel = Literal["high", "medium", "low", "unknown"]


@dataclass
class RawListing:
    """What a collector returns: one offer exactly as the source reported it (lightly typed)."""

    source_key: str
    external_id: str
    title: str
    price_cents: int  # in `currency`
    currency: str = "MXN"
    url: str | None = None
    condition_raw: str | None = None  # source vocabulary ("new", "used", "Nuevo", "seminuevo"...)
    shipping_cents: int | None = None
    free_shipping: bool | None = None
    brand: str | None = None
    model: str | None = None
    gtin: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    seller_id: str | None = None
    seller_name: str | None = None
    seller: dict[str, Any] = field(default_factory=dict)  # reputation/transactions/age as reported
    location: str | None = None
    image_url: str | None = None
    description: str | None = None
    available_quantity: int | None = None
    sold_quantity: int | None = None
    listing_type: str | None = None
    status: str = "active"  # active|ended|sold|unknown
    observed_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedAttributes:
    """Output of :func:`flipradar.normalize.extract.normalize_listing`."""

    normalized_title: str
    brand: str | None
    model: str | None  # canonical model family, e.g. "iphone 15 pro max", "ps5 slim"
    category: str | None  # coarse FLIP RADAR category: phone|console|laptop|tablet|audio|watch|camera|...
    condition: str  # flipradar.models.Condition value
    storage_gb: int | None = None
    ram_gb: int | None = None
    color: str | None = None
    gtin: str | None = None
    is_accessory: bool = False
    is_bundle: bool = False
    variant_tokens: list[str] = field(default_factory=list)  # e.g. ["digital", "oled", "5g"]
    canonical_key: str | None = None  # None when not enough information to cluster safely

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MatchResult:
    product_key: str | None
    confidence: float  # 0..1
    method: str  # gtin|canonical_key|fuzzy|none
    reasons: list[str] = field(default_factory=list)


@dataclass
class ListingView:
    """A listing as seen by the analysis engine (already normalized, prices in MXN centavos)."""

    id: int
    source_key: str
    title: str
    price_mxn_cents: int
    shipping_mxn_cents: int | None
    condition: str
    product_key: str | None
    match_confidence: float | None
    observed_at: datetime  # last_seen_at
    first_seen_at: datetime | None = None
    url: str | None = None
    status: str = "active"
    seller_id: str | None = None
    seller: dict[str, Any] = field(default_factory=dict)
    description: str | None = None
    is_accessory: bool = False
    is_bundle: bool = False
    available_quantity: int | None = None
    sold_quantity: int | None = None
    is_demo: bool = False
    attributes: dict[str, Any] = field(default_factory=dict)
    # False for listings that are only BUY-side evidence (e.g. eBay US, retail deal feeds, single retailer
    # pages): they can be purchase candidates but never define the Mexican resale price.
    resale_reference: bool = True


@dataclass
class PriceStats:
    """Robust statistics over *observed* comparable prices (never invented)."""

    n: int
    median_cents: int | None
    p25_cents: int | None
    p75_cents: int | None
    min_cents: int | None
    max_cents: int | None
    mad_cents: int | None  # median absolute deviation
    iqr_cents: int | None
    dispersion: float | None  # iqr / median (relative spread)
    source_count: int  # distinct sources among comparables
    seller_count: int  # distinct sellers among comparables
    excluded: dict[str, int] = field(default_factory=dict)  # reason -> count of listings excluded
    comparable_ids: list[int] = field(default_factory=list)
    window_days: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RiskFlag:
    code: str  # stable machine code, e.g. "price_far_below_market"
    severity: Severity
    message: str  # human readable, deterministic
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DemandEstimate:
    level: DemandLevel
    confidence: ConfidenceLevel | None
    signals: list[dict[str, Any]] = field(default_factory=list)  # each: {name, value, unit, source, note}
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScoreComponent:
    key: str
    label: str
    raw_value: float | None  # the measured input (e.g. ROI fraction)
    points: float  # contribution to the 0..100 score (may be negative for penalties)
    max_points: float
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OpportunityCandidate:
    """Fully analysed buy candidate, ready to be persisted as an Opportunity row."""

    buy: ListingView
    product_key: str
    fee_profile_key: str
    stats: PriceStats
    expected_sale_cents: int
    buy_total_cents: int
    profit: dict[str, Any]  # flipradar.finance.calculator.ProfitResult.to_dict()
    net_profit_cents: int
    roi_bp: int
    margin_bp: int
    risk_flags: list[RiskFlag]
    demand: DemandEstimate
    score: float
    confidence: ConfidenceLevel
    components: list[ScoreComponent]
    explanation: str
    facts: dict[str, Any]  # every number the explanation (or an AI summary) may cite
