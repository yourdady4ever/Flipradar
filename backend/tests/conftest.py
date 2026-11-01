"""Shared fixtures: an isolated app per test (temp-file SQLite), an auth token, and *fake* pipeline
components (normalizer, matcher, engine) so API/service tests pass regardless of the state of the
modules other engineers are implementing.  Tests marked ``integration`` use the real components."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flipradar.collectors import registry
from flipradar.collectors.base import Collector, CollectorError, CollectorInfo
from flipradar.config import Settings
from flipradar.engine.types import (
    DemandEstimate,
    ListingView,
    MatchResult,
    NormalizedAttributes,
    OpportunityCandidate,
    PriceStats,
    RawListing,
    RiskFlag,
    ScoreComponent,
)
from flipradar.finance.calculator import ProfitInputs, calculate_profit


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "integration: uses the real normalizer/matcher/engine/collectors (may fail until implemented)"
    )


# ------------------------------------------------------------------------------------------------
# App / client / auth
# ------------------------------------------------------------------------------------------------


def make_settings(tmp_path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": f"sqlite:///{tmp_path / 'flipradar-test.db'}",
        "scheduler_enabled": False,
        "require_auth": True,
        "rate_limit_per_minute": 100000,
        "llm_provider": "none",
        "fx_usd_mxn_manual": None,
        "banxico_token": None,
        "fx_public_sources_enabled": False,  # tests never touch the network
        "ntfy_url": None,
        "ntfy_topic": None,
        "telegram_bot_token": None,
        "telegram_chat_id": None,
        "webhook_url": None,
        "cors_origins": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def app(settings: Settings):
    from flipradar.main import create_app

    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(client):
    from flipradar.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def token(client) -> str:
    from flipradar.db import session_scope
    from flipradar.security import create_token

    with session_scope() as session:
        _, plain = create_token(session, "pytest")
    return plain


class AuthedClient:
    """TestClient wrapper that adds the bearer token to every request."""

    def __init__(self, client: TestClient, token: str) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}

    def request(self, method: str, url: str, **kwargs: Any):
        headers = {**self.headers, **(kwargs.pop("headers", None) or {})}
        return self.client.request(method, url, headers=headers, **kwargs)

    def get(self, url: str, **kwargs: Any):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any):
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any):
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any):
        return self.request("DELETE", url, **kwargs)


@pytest.fixture
def api(client: TestClient, token: str) -> AuthedClient:
    return AuthedClient(client, token)


# ------------------------------------------------------------------------------------------------
# Fake pipeline (normalizer, matcher, engine)
# ------------------------------------------------------------------------------------------------


def fake_normalize(raw: RawListing) -> NormalizedAttributes:
    title = " ".join(raw.title.lower().split())
    brand = raw.brand.lower() if raw.brand else None
    model = raw.model.lower() if raw.model else None
    cond_raw = (raw.condition_raw or "").lower()
    condition = "used" if ("usado" in title or cond_raw in ("used", "usado")) else ("new" if cond_raw in ("new", "nuevo") else "unknown")
    key = f"{brand}|{model}" if brand and model else None
    return NormalizedAttributes(
        normalized_title=title, brand=brand, model=model, category="phone" if brand else None,
        condition=condition, storage_gb=None, gtin=raw.gtin, canonical_key=key,
    )


class FakeMatcher:
    def __init__(self, candidates=None, fuzzy_threshold: float = 0.92) -> None:
        self.keys = {c.key for c in (candidates or [])}

    def add(self, candidate) -> None:
        self.keys.add(candidate.key)

    def match(self, attrs: NormalizedAttributes, title: str) -> MatchResult:
        if not attrs.canonical_key:
            return MatchResult(None, 0.0, "none", ["no key"])
        method = "canonical_key"
        return MatchResult(attrs.canonical_key, 0.95, method, ["fake matcher"])


def _total(view: ListingView) -> int:
    return view.price_mxn_cents + (view.shipping_mxn_cents or 0)


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return int((Decimal(ordered[mid - 1] + ordered[mid]) / 2).to_integral_value(rounding=ROUND_HALF_UP))


def fake_select_comparables(target, pool, *, now, max_age_days, include_demo=False):
    comps = [
        v for v in pool
        if v.id != target.id and v.condition == target.condition and v.status == "active"
        and (include_demo or not v.is_demo)
        and not any(c.get("severity") == "critical" for c in v.attributes.get("risk_codes", []))
    ]
    return comps, {}


def fake_comparable_stats(comparables, excluded, window_days) -> PriceStats:
    totals = [_total(v) for v in comparables]
    med = _median(totals) if totals else None
    return PriceStats(
        n=len(totals), median_cents=med, p25_cents=min(totals) if totals else None,
        p75_cents=max(totals) if totals else None, min_cents=min(totals) if totals else None,
        max_cents=max(totals) if totals else None, mad_cents=0, iqr_cents=0, dispersion=0.0,
        source_count=len({v.source_key for v in comparables}), seller_count=len(comparables),
        excluded=dict(excluded), comparable_ids=[v.id for v in comparables], window_days=window_days,
    )


def fake_assess_listing(view: ListingView, stats: PriceStats | None) -> list[RiskFlag]:
    flags: list[RiskFlag] = []
    if "replica" in view.title.lower():
        flags.append(RiskFlag("replica_or_counterfeit", "critical", "Title mentions a replica.", {}))
    if stats is not None and stats.median_cents and _total(view) * 100 < stats.median_cents * 55:
        flags.append(RiskFlag("price_far_below_market", "warning", "Far below market.", {"median": stats.median_cents}))
    return flags


def fake_analyze_product(product_key, listings, fee_model, settings, observations, *, now,
                         fee_models_by_condition=None) -> list[OpportunityCandidate]:
    out: list[OpportunityCandidate] = []
    for buy in listings:
        if buy.status != "active" or (buy.is_demo and not settings.include_demo):
            continue
        comps, excluded = fake_select_comparables(buy, listings, now=now, max_age_days=settings.comparable_max_age_days,
                                                  include_demo=settings.include_demo)
        if len(comps) < settings.min_comparables:
            continue
        stats = fake_comparable_stats(comps, excluded, settings.comparable_max_age_days)
        expected = stats.median_cents or 0
        profit = calculate_profit(ProfitInputs(buy_price_cents=buy.price_mxn_cents, sale_price_cents=expected,
                                               fee=fee_model, buy_shipping_cents=buy.shipping_mxn_cents or 0))
        roi = profit.roi_bp or 0
        if profit.net_profit_cents < settings.min_profit_cents or roi < settings.min_roi_bp:
            continue
        flags = [RiskFlag(f["code"], f["severity"], f.get("message", ""), f.get("evidence", {}))
                 for f in buy.attributes.get("risk_flags", [])]
        score = round(min(100.0, roi / 100), 1)
        facts = {"buy_total_cents": profit.total_acquisition_cents, "expected_sale_cents": expected,
                 "net_profit_cents": profit.net_profit_cents, "roi_bp": roi, "comparable_count": stats.n,
                 "product_title": buy.title}
        out.append(OpportunityCandidate(
            buy=buy, product_key=product_key, fee_profile_key=fee_model.key, stats=stats,
            expected_sale_cents=expected, buy_total_cents=profit.total_acquisition_cents, profit=profit.to_dict(),
            net_profit_cents=profit.net_profit_cents, roi_bp=roi, margin_bp=profit.margin_bp or 0,
            risk_flags=flags, demand=DemandEstimate("unknown", None, [], "fake"), score=score, confidence="medium",
            components=[ScoreComponent("roi", "ROI", roi / 10000, score, 100.0, "fake")],
            explanation=f"Fake explanation: profit {profit.net_profit_cents}", facts=facts,
        ))
    return sorted(out, key=lambda c: -c.score)


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Replace normalizer/matcher/engine with simple deterministic fakes."""
    from flipradar.engine import opportunities, risk, stats
    from flipradar.matching import matcher
    from flipradar.normalize import extract

    monkeypatch.setattr(extract, "normalize_listing", fake_normalize)
    monkeypatch.setattr(matcher, "ProductMatcher", FakeMatcher)
    monkeypatch.setattr(stats, "select_comparables", fake_select_comparables)
    monkeypatch.setattr(stats, "comparable_stats", fake_comparable_stats)
    monkeypatch.setattr(risk, "assess_listing", fake_assess_listing)
    monkeypatch.setattr(opportunities, "analyze_product", fake_analyze_product)
    return True


# ------------------------------------------------------------------------------------------------
# Fake collectors
# ------------------------------------------------------------------------------------------------


def raw(external_id: str, title: str, pesos: int, *, brand: str = "apple", model: str = "iphone 15 pro",
        shipping: int | None = 0, condition: str = "used", seller: str | None = None, **extra: Any) -> RawListing:
    return RawListing(
        source_key="fakesource", external_id=external_id, title=title, price_cents=pesos * 100, currency="MXN",
        url=f"https://example.com/item/{external_id}", condition_raw=condition,
        shipping_cents=None if shipping is None else shipping * 100, brand=brand, model=model,
        seller_id=seller or f"seller-{external_id}", seller_name=seller or f"Seller {external_id}",
        observed_at=extra.pop("observed_at", None), **extra,
    )


FAKE_RESULTS: list[RawListing] = [
    raw("A1", "iPhone 15 Pro 256GB usado", 15000),
    raw("A2", "iPhone 15 Pro 256GB usado excelente", 15500),
    raw("A3", "iPhone 15 Pro 256GB usado 90% bateria", 16000),
    raw("A4", "iPhone 15 Pro 256GB usado con caja", 15200),
    raw("A5", "iPhone 15 Pro 256GB usado oferta", 9500),
    raw("A6", "iPhone 15 Pro replica usado", 4000),
]


class FakeCollector(Collector):
    info = CollectorInfo(
        key="fakesource", name="Fake marketplace", kind="marketplace", country="MX", currency="MXN",
        default_fee_profile_key="ml_clasica", requires_credentials=False, docs_url=None,
        notes="test double", capabilities=["search"],
    )
    results: list[RawListing] = FAKE_RESULTS

    def is_configured(self) -> bool:
        return True

    async def search(self, query: str, *, limit: int = 50, category_id: str | None = None) -> list[RawListing]:
        return [r for r in self.results][:limit]


class UnconfiguredCollector(Collector):
    info = CollectorInfo(
        key="needscreds", name="Needs credentials", kind="marketplace", country="MX", currency="MXN",
        default_fee_profile_key=None, requires_credentials=True, docs_url=None, capabilities=["search"],
    )

    def is_configured(self) -> bool:
        return False


class BrokenCollector(Collector):
    info = CollectorInfo(
        key="brokensource", name="Broken source", kind="marketplace", country="MX", currency="MXN",
        default_fee_profile_key=None, requires_credentials=False, docs_url=None, capabilities=["search"],
    )

    def is_configured(self) -> bool:
        return True

    async def search(self, query: str, *, limit: int = 50, category_id: str | None = None) -> list[RawListing]:
        raise CollectorError("HTTP 503 from source")


@pytest.fixture
def fake_collectors(client):
    """Register the fake collectors and create their Source rows."""
    from flipradar.db import session_scope
    from flipradar.services.bootstrap import seed_sources

    for cls in (FakeCollector, UnconfiguredCollector, BrokenCollector):
        registry.register(cls)
    with session_scope() as session:
        seed_sources(session)
    yield
    for cls in (FakeCollector, UnconfiguredCollector, BrokenCollector):
        registry.unregister(cls.info.key)


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------------------------------------
# Hermetic tests: no outbound network (only loopback is allowed, for local test servers).
# ------------------------------------------------------------------------------------------------

import socket as _socket  # noqa: E402

import pytest as _pytest  # noqa: E402

_REAL_CONNECT = _socket.socket.connect
_REAL_GETADDRINFO = _socket.getaddrinfo
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "")


def _guarded_getaddrinfo(host, *args, **kwargs):  # pragma: no cover - only triggers on a test bug
    name = host.decode() if isinstance(host, bytes) else (host or "")
    if name in _LOCAL_HOSTS or name.startswith("127."):
        return _REAL_GETADDRINFO(host, *args, **kwargs)
    raise RuntimeError(f"test tried to resolve {name!r}; mock the network call instead")


def _guarded_connect(self, address):  # pragma: no cover - only triggers on a test bug
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and (host.startswith("127.") or host in ("localhost", "::1") or self.family == _socket.AF_UNIX):
        return _REAL_CONNECT(self, address)
    raise RuntimeError(f"test tried to open a network connection to {address!r}; mock it instead")


@_pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # Without a proxy every outbound connection goes through the guard (a loopback proxy would hide it).
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(_socket, "getaddrinfo", _guarded_getaddrinfo)
