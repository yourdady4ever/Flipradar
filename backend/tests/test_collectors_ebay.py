"""eBay Browse collector tests (respx mocks + fixtures; no network)."""

import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from flipradar.collectors import ebay, netguard
from flipradar.collectors.base import CollectorError, CollectorNotConfigured
from flipradar.collectors.registry import get_collector
from flipradar.config import Settings

FIX = Path(__file__).parent / "fixtures" / "collectors"
BASE = "https://api.ebay.com"
TOKEN_URL = f"{BASE}/identity/v1/oauth2/token"
SEARCH_URL = f"{BASE}/buy/browse/v1/item_summary/search"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def run(coro):
    return asyncio.run(coro)


def make_settings(**kw):
    return Settings(_env_file=None, ebay_client_id="MyApp-PRD-123", ebay_client_secret="PRD-secret", **kw)


def token_response(token="v^1.1#TOKEN", expires=7200):
    return httpx.Response(
        200, json={"access_token": token, "expires_in": expires, "token_type": "Application Access Token"}
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    ebay.reset_state()
    monkeypatch.setattr(ebay.EbayCollector, "requests_per_second", 0.0)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(netguard, "sleep", fake_sleep)
    yield sleeps
    ebay.reset_state()


def test_info_and_configuration():
    collector = get_collector("ebay", make_settings())
    assert collector.info.name == "eBay (US) Browse API - ships to MX"
    assert collector.info.country == "US" and collector.info.currency == "USD"
    assert collector.info.default_fee_profile_key is None
    assert "import" in collector.info.notes and "sold" in collector.info.notes
    assert collector.is_configured()
    assert not ebay.EbayCollector(Settings(_env_file=None)).is_configured()
    with pytest.raises(CollectorNotConfigured):
        run(ebay.EbayCollector(Settings(_env_file=None)).search("iphone"))
    assert run(ebay.EbayCollector(Settings(_env_file=None)).health()).configured is False


def test_token_request_shape_and_caching():
    with respx.mock(assert_all_called=False) as router:
        token = router.post(TOKEN_URL).mock(return_value=token_response())
        search = router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"total": 0}))
        run(ebay.EbayCollector(make_settings()).search("iphone 15"))
        run(ebay.EbayCollector(make_settings()).search("ps5"))
        assert token.call_count == 1  # cached across instances (1,000 token calls/day limit)
        request = token.calls[0].request
        expected = base64.b64encode(b"MyApp-PRD-123:PRD-secret").decode()
        assert request.headers["Authorization"] == f"Basic {expected}"
        assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert parse_qs(request.content.decode()) == {
            "grant_type": ["client_credentials"],
            "scope": ["https://api.ebay.com/oauth/api_scope"],
        }
        assert search.call_count == 2


def test_search_request_headers_and_params():
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=token_response("TKN"))
        search = router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=load("ebay_search.json")))
        run(ebay.EbayCollector(make_settings()).search("iphone 15 128gb", limit=500, category_id="9355"))
        request = search.calls[0].request
    assert request.headers["Authorization"] == "Bearer TKN"
    assert request.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"
    assert request.headers["X-EBAY-C-ENDUSERCTX"] == "contextualLocation=country%3DMX"
    assert parse_qs(request.url.query.decode()) == {
        "q": ["iphone 15 128gb"],
        "limit": ["200"],
        "filter": ["deliveryCountry:MX"],
        "category_ids": ["9355"],
    }


def test_mapping_usd_shipping_condition_and_auction_skip():
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=token_response())
        router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=load("ebay_search.json")))
        listings = run(ebay.EbayCollector(make_settings()).search("iphone 15"))
    assert [listing.external_id for listing in listings] == [
        "v1|123456789012|0",
        "v1|222222222222|0",
        "v1|444444444444|0",
    ]
    used, new, other = listings
    assert used.source_key == "ebay"
    assert used.currency == "USD"
    assert used.price_cents == 52999
    assert used.shipping_cents == 3550 and used.free_shipping is False
    assert used.condition_raw == "Used"
    assert used.attributes["condition_id"] == "3000"
    assert used.attributes["buying_options"] == ["FIXED_PRICE", "BEST_OFFER"]
    assert used.attributes["epid"] == "28059384956"
    assert used.url == "https://www.ebay.com/itm/123456789012"
    assert used.image_url == "https://i.ebayimg.com/images/g/abc/s-l225.jpg"
    assert used.seller == {
        "username": "gadget_seller",
        "feedback_percentage": 99.6,
        "transactions_total": 4821,
        "feedback_score": 4821,
    }
    assert used.seller_id == "gadget_seller"
    assert used.location == "330**, US"
    assert used.gtin == "0195949036187"
    assert used.listing_type == "FIXED_PRICE"

    assert new.price_cents == 69900 and new.shipping_cents == 0 and new.free_shipping is True
    assert new.condition_raw == "New" and new.image_url is None

    assert other.price_cents == 61000
    assert other.shipping_cents is None and other.free_shipping is None  # EUR shipping not mixed with USD
    assert other.condition_raw == "Open box"


def test_map_item_summary_rejects_incomplete():
    assert ebay.map_item_summary({"itemId": "x", "title": "t", "price": {"value": "1.00"}}) is None
    assert (
        ebay.map_item_summary({"itemId": "x", "title": "t", "price": {"value": "abc", "currency": "USD"}})
        is None
    )
    assert (
        ebay.map_item_summary({"itemId": "x", "title": "t", "price": {"value": "0.00", "currency": "USD"}})
        is None
    )
    assert ebay.map_item_summary({"title": "t", "price": {"value": "5", "currency": "USD"}}) is None


def test_no_results_returns_empty_list():
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=token_response())
        router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"total": 0, "limit": 50}))
        assert run(ebay.EbayCollector(make_settings()).search("zzzz")) == []


def test_daily_quota_guard():
    settings = make_settings()
    settings.ebay_daily_call_limit = 2
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=token_response())
        search = router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"total": 0}))
        collector = ebay.EbayCollector(settings)
        run(collector.search("a"))
        run(collector.search("b"))
        with pytest.raises(ebay.EbayQuotaExceeded, match="daily safety limit"):
            run(collector.search("c"))
        assert search.call_count == 2
    status = ebay.quota_status(settings)
    assert status["calls"] == 2 and status["limit"] == 2


def test_401_refreshes_token_once():
    with respx.mock(assert_all_called=False) as router:
        token = router.post(TOKEN_URL).mock(side_effect=[token_response("OLD"), token_response("NEW")])
        search = router.get(SEARCH_URL).mock(
            side_effect=[
                httpx.Response(401, json={"errors": [{"message": "Invalid access token"}]}),
                httpx.Response(200, json={"total": 0}),
            ]
        )
        run(ebay.EbayCollector(make_settings()).search("iphone"))
        assert token.call_count == 2
        assert search.calls[1].request.headers["Authorization"] == "Bearer NEW"


def test_429_retry_after_then_error(_isolate):
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=token_response())
        router.get(SEARCH_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "3"}),
                httpx.Response(200, json={"total": 0}),
            ]
        )
        assert run(ebay.EbayCollector(make_settings()).search("iphone")) == []
    assert _isolate == [3.0]
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=token_response())
        router.get(SEARCH_URL).mock(return_value=httpx.Response(429))
        with pytest.raises(CollectorError, match="429"):
            run(ebay.EbayCollector(make_settings()).search("iphone"))


def test_403_and_token_errors_are_explained():
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=token_response())
        router.get(SEARCH_URL).mock(
            return_value=httpx.Response(403, json={"errors": [{"errorId": 1100, "message": "Access denied"}]})
        )
        with pytest.raises(CollectorError, match="Access denied.*production keyset"):
            run(ebay.EbayCollector(make_settings()).search("iphone"))
    ebay.reset_state()
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                401, json={"error": "invalid_client", "error_description": "client authentication failed"}
            )
        )
        with pytest.raises(CollectorError, match="client authentication failed"):
            run(ebay.EbayCollector(make_settings()).search("iphone"))


def test_health_reports_quota():
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=token_response())
        router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"total": 1234}))
        health = run(ebay.EbayCollector(make_settings()).health())
    assert health.ok is True
    assert health.details["sample_total"] == 1234
    assert health.details["calls"] == 1
