"""Mercado Libre collector tests (respx mocks + fixtures; no network)."""

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from flipradar.collectors import mercadolibre as ml
from flipradar.collectors import netguard
from flipradar.collectors.base import CollectorError, CollectorNotConfigured
from flipradar.collectors.registry import collector_classes, get_collector
from flipradar.config import Settings

FIX = Path(__file__).parent / "fixtures" / "collectors"
BASE = "https://api.mercadolibre.com"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def run(coro):
    return asyncio.run(coro)


def make_settings(**kw):
    defaults = {"ml_client_id": "APPID", "ml_client_secret": "SECRET", "ml_refresh_token": "TG-RT1"}
    defaults.update(kw)
    return Settings(_env_file=None, **defaults)


def token_payload(access="APP_USR-AT1", refresh="TG-RT2", expires=21600):
    payload = {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": expires,
        "scope": "offline_access read",
    }
    payload["user_id"] = 99
    if refresh:
        payload["refresh_token"] = refresh
    return payload


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    ml.reset_token_cache()
    monkeypatch.setattr(ml.MercadoLibreCollector, "requests_per_second", 0.0)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(netguard, "sleep", fake_sleep)
    yield sleeps
    ml.reset_token_cache()


def mock_catalog(router, *, users=True):
    router.get(f"{BASE}/products/search").mock(
        return_value=httpx.Response(200, json=load("ml_products_search.json"))
    )
    router.get(f"{BASE}/products/MLM27172623/items").mock(
        return_value=httpx.Response(200, json=load("ml_product_items_1.json"))
    )
    router.get(f"{BASE}/products/MLM99887766/items").mock(
        return_value=httpx.Response(200, json=load("ml_product_items_2.json"))
    )
    if users:
        router.get(f"{BASE}/users/111").mock(return_value=httpx.Response(200, json=load("ml_user_111.json")))
        router.get(f"{BASE}/users/222").mock(return_value=httpx.Response(404, json={"message": "not found"}))


# ------------------------------------------------------------------------------------------------
# registry / info
# ------------------------------------------------------------------------------------------------


def test_registered_with_expected_info():
    assert "mercadolibre" in collector_classes()
    collector = get_collector("mercadolibre", make_settings())
    info = collector.info
    assert info.name == "Mercado Libre México (catalog API)"
    assert info.kind == "marketplace" and info.country == "MX" and info.currency == "MXN"
    assert info.default_fee_profile_key == "ml_clasica"
    assert info.requires_credentials is True
    assert info.capabilities == ["search"]
    assert "PolicyAgent" in info.notes and "catalog" in info.notes


def test_is_configured_variants():
    assert ml.MercadoLibreCollector(Settings(_env_file=None, ml_access_token="X")).is_configured()
    assert ml.MercadoLibreCollector(make_settings(ml_refresh_token=None)).is_configured()
    assert not ml.MercadoLibreCollector(Settings(_env_file=None, ml_client_id="only-id")).is_configured()


def test_not_configured_search_and_health():
    collector = ml.MercadoLibreCollector(Settings(_env_file=None))
    with pytest.raises(CollectorNotConfigured):
        run(collector.search("iphone"))
    health = run(collector.health())
    assert health.configured is False and health.ok is None


# ------------------------------------------------------------------------------------------------
# pure helpers
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("item_id", "expected"),
    [
        ("MLM123456", "https://articulo.mercadolibre.com.mx/MLM-123456"),
        ("MLM-123456", "https://articulo.mercadolibre.com.mx/MLM-123456"),
        ("mlm42", "https://articulo.mercadolibre.com.mx/MLM-42"),
        ("MLA123456", None),
        ("MLM12a", None),
        ("", None),
    ],
)
def test_item_url(item_id, expected):
    assert ml.item_url(item_id) == expected


def test_parse_listing_fee_percentage_to_bp():
    fee = ml.parse_listing_fee(load("ml_listing_prices.json"), "gold_pro")
    assert fee["commission_bp"] == 1950
    assert fee["fixed_fee_cents"] == 2500
    assert fee["sale_fee_cents"] == 253500
    assert fee["gross_amount_cents"] == 253500
    assert fee["currency"] == "MXN"
    assert fee["listing_type_label"] == "Premium"
    classic = ml.parse_listing_fee(load("ml_listing_prices.json"), "gold_special")
    assert classic["commission_bp"] == 1550 and classic["fixed_fee_cents"] == 0
    single = ml.parse_listing_fee(
        {"sale_fee_amount": 10, "sale_fee_details": {"percentage_fee": 12.25}}, "gold_special"
    )
    assert single["commission_bp"] == 1225 and single["fixed_fee_cents"] is None
    with pytest.raises(CollectorError):
        ml.parse_listing_fee(load("ml_listing_prices.json"), "free")
    with pytest.raises(CollectorError):
        ml.parse_listing_fee([], "gold_pro")


def test_map_seller():
    seller = ml.map_seller(load("ml_user_111.json"))
    assert seller == {
        "nickname": "TIENDA_EJEMPLO",
        "reputation_level": "5_green",
        "power_seller_status": "platinum",
        "transactions_total": 15234,
        "transactions_completed": 14980,
        "transactions_canceled": 254,
        "ratings_negative_pct": 2.0,
        "registration_date": "2015-03-02T10:11:12.000-04:00",
    }
    assert ml.map_seller({"id": 5, "nickname": "X", "seller_reputation": {"level_id": None}}) == {
        "nickname": "X"
    }
    assert ml.map_seller({"seller_reputation": {"transactions": {"ratings": {"negative": 0.0125}}}}) == {
        "ratings_negative_pct": 1.3
    }


# ------------------------------------------------------------------------------------------------
# token flows
# ------------------------------------------------------------------------------------------------


def test_refresh_token_flow_rotation_caching_and_listener():
    rotated = []
    ml.add_refresh_token_listener(rotated.append)
    try:
        settings = make_settings()
        with respx.mock(assert_all_called=False) as router:
            token_route = router.post(f"{BASE}/oauth/token").mock(
                side_effect=[
                    httpx.Response(200, json=token_payload("APP_USR-AT1", "TG-RT2")),
                    httpx.Response(200, json=token_payload("APP_USR-AT2", "TG-RT3")),
                ]
            )
            search = router.get(f"{BASE}/products/search").mock(
                return_value=httpx.Response(200, json={"results": [], "paging": {"total": 0}})
            )
            first = ml.MercadoLibreCollector(settings)
            assert run(first.search("iphone 15")) == []
            # second instance in the same process reuses the cached token
            second = ml.MercadoLibreCollector(settings)
            assert run(second.search("iphone 15")) == []
            assert token_route.call_count == 1
            form = parse_qs(token_route.calls[0].request.content.decode())
            assert form == {
                "grant_type": ["refresh_token"],
                "client_id": ["APPID"],
                "client_secret": ["SECRET"],
                "refresh_token": ["TG-RT1"],
            }
            assert search.calls[0].request.headers["Authorization"] == "Bearer APP_USR-AT1"
            assert first.rotated_refresh_token == "TG-RT2"
            assert ml.get_rotated_refresh_token(settings) == "TG-RT2"
            assert rotated == ["TG-RT2"]

            # token expiry -> the NEWEST refresh token (single use) is used, not the configured one
            ml._state_for(settings).expires_at = 0
            run(ml.MercadoLibreCollector(settings).search("iphone 15"))
            assert token_route.call_count == 2
            form2 = parse_qs(token_route.calls[1].request.content.decode())
            assert form2["refresh_token"] == ["TG-RT2"]
            assert search.calls[-1].request.headers["Authorization"] == "Bearer APP_USR-AT2"
            assert rotated == ["TG-RT2", "TG-RT3"]
    finally:
        ml.remove_refresh_token_listener(rotated.append)


def test_client_credentials_when_no_refresh_token():
    settings = make_settings(ml_refresh_token=None)
    with respx.mock(assert_all_called=False) as router:
        token_route = router.post(f"{BASE}/oauth/token").mock(
            return_value=httpx.Response(200, json=token_payload("APP_USR-CC", refresh=None))
        )
        router.get(f"{BASE}/products/search").mock(return_value=httpx.Response(200, json={"results": []}))
        collector = ml.MercadoLibreCollector(settings)
        run(collector.search("ps5"))
        form = parse_qs(token_route.calls[0].request.content.decode())
        assert form["grant_type"] == ["client_credentials"]
        assert "refresh_token" not in form
        assert collector.rotated_refresh_token is None


def test_invalid_grant_falls_back_to_client_credentials():
    settings = make_settings()
    with respx.mock(assert_all_called=False) as router:
        token_route = router.post(f"{BASE}/oauth/token").mock(
            side_effect=[
                httpx.Response(
                    400, json={"error": "invalid_grant", "message": "invalid_grant", "status": 400}
                ),
                httpx.Response(200, json=token_payload("APP_USR-CC", refresh=None)),
            ]
        )
        search = router.get(f"{BASE}/products/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        run(ml.MercadoLibreCollector(settings).search("ps5"))
        assert token_route.call_count == 2
        assert parse_qs(token_route.calls[1].request.content.decode())["grant_type"] == ["client_credentials"]
        assert search.calls[0].request.headers["Authorization"] == "Bearer APP_USR-CC"


def test_all_grants_fail_error_message():
    settings = make_settings()
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{BASE}/oauth/token").mock(
            side_effect=[
                httpx.Response(400, json={"error": "invalid_grant"}),
                httpx.Response(401, json={"error": "invalid_client", "message": "invalid client_id"}),
            ]
        )
        with pytest.raises(CollectorError) as err:
            run(ml.MercadoLibreCollector(settings).search("ps5"))
    message = str(err.value)
    assert "single-use" in message and "client_id/client_secret rejected" in message
    assert "SECRET" not in message


def test_static_access_token_used_directly():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    with respx.mock(assert_all_called=False) as router:
        token_route = router.post(f"{BASE}/oauth/token")
        search = router.get(f"{BASE}/products/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        run(ml.MercadoLibreCollector(settings).search("ps5"))
        assert token_route.call_count == 0
        assert search.calls[0].request.headers["Authorization"] == "Bearer APP_USR-STATIC"


def test_401_refreshes_once_then_succeeds():
    settings = make_settings(ml_access_token="APP_USR-EXPIRED")
    with respx.mock(assert_all_called=False) as router:
        token_route = router.post(f"{BASE}/oauth/token").mock(
            return_value=httpx.Response(200, json=token_payload("APP_USR-NEW", "TG-RT2"))
        )
        search = router.get(f"{BASE}/products/search").mock(
            side_effect=[
                httpx.Response(401, json={"message": "invalid access token", "error": "unauthorized"}),
                httpx.Response(200, json={"results": []}),
            ]
        )
        run(ml.MercadoLibreCollector(settings).search("ps5"))
        assert token_route.call_count == 1
        assert search.calls[0].request.headers["Authorization"] == "Bearer APP_USR-EXPIRED"
        assert search.calls[1].request.headers["Authorization"] == "Bearer APP_USR-NEW"


def test_401_twice_raises():
    settings = make_settings()
    with respx.mock(assert_all_called=False) as router:
        token_route = router.post(f"{BASE}/oauth/token").mock(
            side_effect=[
                httpx.Response(200, json=token_payload("A1", "R2")),
                httpx.Response(200, json=token_payload("A2", "R3")),
            ]
        )
        search = router.get(f"{BASE}/products/search").mock(
            return_value=httpx.Response(401, json={"message": "invalid access token"})
        )
        with pytest.raises(CollectorError, match="401"):
            run(ml.MercadoLibreCollector(settings).search("ps5"))
        assert search.call_count == 2
        assert token_route.call_count == 2


def test_static_token_rejected_without_app_credentials():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-OLD")
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/products/search").mock(
            return_value=httpx.Response(401, json={"message": "expired"})
        )
        with pytest.raises(CollectorError, match="no client_id/client_secret"):
            run(ml.MercadoLibreCollector(settings).search("ps5"))


# ------------------------------------------------------------------------------------------------
# error handling
# ------------------------------------------------------------------------------------------------


def test_policy_agent_403_message():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/products/search").mock(
            return_value=httpx.Response(403, json=load("ml_policy_403.json"))
        )
        with pytest.raises(ml.MercadoLibrePolicyError) as err:
            run(ml.MercadoLibreCollector(settings).search("iphone"))
    message = str(err.value)
    assert message.startswith("Mercado Libre denied access by policy (PolicyAgent) for this app.")
    assert "DevCenter" in message and "manual/CSV import" in message and "/products/search" in message


def test_plain_403_is_generic_error():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/products/search").mock(
            return_value=httpx.Response(403, json={"message": "forbidden", "error": "forbidden"})
        )
        with pytest.raises(CollectorError) as err:
            run(ml.MercadoLibreCollector(settings).search("iphone"))
    assert not isinstance(err.value, ml.MercadoLibrePolicyError)
    assert "HTTP 403" in str(err.value)


def test_429_respects_retry_after_once(_isolate):
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/products/search").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "7"}),
                httpx.Response(200, json={"results": []}),
            ]
        )
        assert run(ml.MercadoLibreCollector(settings).search("iphone")) == []
    assert _isolate == [7.0]


def test_429_twice_raises_and_caps_wait(_isolate):
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/products/search").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "600"})
        )
        with pytest.raises(CollectorError, match="429"):
            run(ml.MercadoLibreCollector(settings).search("iphone"))
        assert route.call_count == 2
    assert _isolate == [60.0]


def test_network_error_wrapped():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/products/search").mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(CollectorError, match="ConnectError"):
            run(ml.MercadoLibreCollector(settings).search("iphone"))


# ------------------------------------------------------------------------------------------------
# catalog search mapping
# ------------------------------------------------------------------------------------------------


def test_catalog_search_mapping_and_enrichment():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    with respx.mock(assert_all_called=False) as router:
        mock_catalog(router)
        collector = ml.MercadoLibreCollector(settings)
        listings = run(collector.search("iphone 15", limit=15))
        search_request = router.calls[0].request
        params = parse_qs(search_request.url.query.decode())
        assert search_request.url.path == "/products/search"
        assert params == {"status": ["active"], "site_id": ["MLM"], "limit": ["2"], "q": ["iphone 15"]}

    assert [listing.external_id for listing in listings] == ["MLM123456789", "MLM987654321", "MLM444555666"]
    first, second, third = listings
    assert first.source_key == "mercadolibre"
    assert first.title == "Apple iPhone 15 (128 GB) - Negro"
    assert first.price_cents == 1299999
    assert first.currency == "MXN"
    assert first.url == "https://articulo.mercadolibre.com.mx/MLM-123456789"
    assert first.condition_raw == "new"
    assert first.free_shipping is True and first.shipping_cents == 0
    assert first.brand == "Apple" and first.model == "iPhone 15" and first.gtin == "0195949036187"
    assert first.category_id == "MLM1055"
    assert first.seller_id == "111"
    assert first.image_url == "https://http2.mlstatic.com/D_NQ_NP_123-F.jpg"
    assert first.listing_type == "gold_special"
    assert first.attributes["listing_type_label"] == "Clásica"
    assert first.attributes["INTERNAL_MEMORY"] == "128 GB"
    assert "COLOR" not in first.attributes  # null value_name is not invented
    assert first.attributes["catalog_product_id"] == "MLM27172623"
    assert first.attributes["original_price_cents"] == 1599900
    assert first.raw["item_id"] == "MLM123456789" and "tags" not in first.raw
    assert first.seller["reputation_level"] == "5_green"
    assert first.seller["ratings_negative_pct"] == 2.0
    assert first.seller_name == "TIENDA_EJEMPLO"

    assert second.price_cents == 1349900
    assert second.condition_raw == "used"
    assert second.free_shipping is False and second.shipping_cents is None  # unknown, never guessed
    assert second.listing_type == "gold_pro" and second.attributes["listing_type_label"] == "Premium"
    assert second.attributes["official_store_id"] == "3456"
    assert second.seller == {}  # seller lookup returned 404

    assert third.price_cents == 1549950
    assert third.gtin is None  # invalid GTIN "123" dropped
    assert third.image_url is None
    assert third.seller["nickname"] == "TIENDA_EJEMPLO"  # cached seller reused
    assert any("skipped" in w for w in collector.last_warnings)


def test_search_limit_and_product_count():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    with respx.mock(assert_all_called=False) as router:
        mock_catalog(router)
        listings = run(ml.MercadoLibreCollector(settings).search("iphone 15", limit=1))
        params = parse_qs(router.calls[0].request.url.query.decode())
        assert params["limit"] == ["1"]
        assert len(listings) == 1
        paths = [call.request.url.path for call in router.calls]
    assert listings[0].external_id == "MLM123456789"
    assert "/products/MLM99887766/items" not in paths  # stops once the limit is reached
    assert paths.count("/users/111") == 1


def test_search_category_filter_and_gtin_query():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    with respx.mock(assert_all_called=False) as router:
        mock_catalog(router, users=False)
        router.get(f"{BASE}/users/111").mock(return_value=httpx.Response(404))
        listings = run(ml.MercadoLibreCollector(settings).search("0195949036187", category_id="MLM9999"))
        params = parse_qs(router.calls[0].request.url.query.decode())
    assert params["product_identifier"] == ["0195949036187"] and "q" not in params
    assert listings == []


def test_product_without_items_404_is_skipped():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/products/search").mock(
            return_value=httpx.Response(200, json=load("ml_products_search.json"))
        )
        router.get(f"{BASE}/products/MLM27172623/items").mock(
            return_value=httpx.Response(404, json={"message": "No winners found"})
        )
        router.get(f"{BASE}/products/MLM99887766/items").mock(
            return_value=httpx.Response(200, json=load("ml_product_items_2.json"))
        )
        router.get(f"{BASE}/users/111").mock(return_value=httpx.Response(200, json=load("ml_user_111.json")))
        collector = ml.MercadoLibreCollector(settings)
        listings = run(collector.search("iphone 15"))
    assert [listing.external_id for listing in listings] == ["MLM444555666"]
    assert any("MLM27172623" in w for w in collector.last_warnings)


def test_items_policy_block_propagates():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/products/search").mock(
            return_value=httpx.Response(200, json=load("ml_products_search.json"))
        )
        router.get(f"{BASE}/products/MLM27172623/items").mock(
            return_value=httpx.Response(403, json=load("ml_policy_403.json"))
        )
        with pytest.raises(ml.MercadoLibrePolicyError):
            run(ml.MercadoLibreCollector(settings).search("iphone 15"))


def test_seller_enrichment_limited_to_20_distinct_sellers():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    listings = [
        ml.map_catalog_item(
            {"item_id": f"MLM{i}", "price": 100, "currency_id": "MXN", "seller_id": 1000 + i},
            {"id": "MLMP", "name": "Producto"},
        )
        for i in range(25)
    ]
    with respx.mock(assert_all_called=False) as router:
        users = router.get(url__regex=rf"{BASE}/users/\d+").mock(
            return_value=httpx.Response(200, json=load("ml_user_111.json"))
        )
        found = run(ml.MercadoLibreCollector(settings).enrich_sellers(listings))
        assert users.call_count == 20
    assert found == 20
    assert sum(1 for listing in listings if listing.seller) == 20


def test_seller_enrichment_policy_block_stops_quietly():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    listings = [
        ml.map_catalog_item(
            {"item_id": f"MLM{i}", "price": 100, "currency_id": "MXN", "seller_id": 1000 + i},
            {"id": "MLMP", "name": "Producto"},
        )
        for i in range(3)
    ]
    with respx.mock(assert_all_called=False) as router:
        users = router.get(url__regex=rf"{BASE}/users/\d+").mock(
            return_value=httpx.Response(403, json=load("ml_policy_403.json"))
        )
        collector = ml.MercadoLibreCollector(settings)
        assert run(collector.enrich_sellers(listings)) == 0
        assert users.call_count == 1
    assert any("PolicyAgent" in w for w in collector.last_warnings)


# ------------------------------------------------------------------------------------------------
# category predictor / fees / health
# ------------------------------------------------------------------------------------------------


def test_predict_category_works_without_token():
    collector = ml.MercadoLibreCollector(Settings(_env_file=None))
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/sites/MLM/domain_discovery/search").mock(
            return_value=httpx.Response(200, json=load("ml_domain_discovery.json"))
        )
        result = run(collector.predict_category("iPhone 15 128 GB negro"))
        request = route.calls[0].request
    assert "Authorization" not in request.headers
    assert parse_qs(request.url.query.decode()) == {"q": ["iPhone 15 128 GB negro"], "limit": ["3"]}
    assert result[0]["category_id"] == "MLM1055"
    assert result[0]["domain_name"] == "Celulares y Smartphones"
    assert len(result) == 2


def test_get_listing_fee_request_and_mapping():
    settings = Settings(_env_file=None, ml_access_token="APP_USR-STATIC")
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/sites/MLM/listing_prices").mock(
            return_value=httpx.Response(200, json=load("ml_listing_prices.json"))
        )
        fee = run(
            ml.MercadoLibreCollector(settings).get_listing_fee(
                1299900, "MLM1055", "gold_pro", logistic_type="drop_off", shipping_mode="me2"
            )
        )
        request = route.calls[0].request
    assert parse_qs(request.url.query.decode()) == {
        "price": ["12999.00"],
        "listing_type_id": ["gold_pro"],
        "category_id": ["MLM1055"],
        "logistic_type": ["drop_off"],
        "shipping_mode": ["me2"],
    }
    assert request.headers["Authorization"] == "Bearer APP_USR-STATIC"
    assert fee["commission_bp"] == 1950
    assert fee["fixed_fee_cents"] == 2500
    assert fee["sale_fee_cents"] == 253500
    assert fee["raw"]["listing_type_id"] == "gold_pro"


def test_get_listing_fee_rejects_bad_input():
    collector = ml.MercadoLibreCollector(Settings(_env_file=None))
    with pytest.raises(ValueError):
        run(collector.get_listing_fee(0, "MLM1055"))
    with pytest.raises(CollectorError):
        run(collector.get_listing_fee(100, "../../users/1"))


def test_health_ok_and_policy_denied():
    settings = make_settings()
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{BASE}/oauth/token").mock(return_value=httpx.Response(200, json=token_payload()))
        router.get(f"{BASE}/sites/MLM/domain_discovery/search").mock(
            return_value=httpx.Response(200, json=load("ml_domain_discovery.json")[:1])
        )
        catalog = router.get(f"{BASE}/products/search").mock(
            return_value=httpx.Response(200, json=load("ml_products_search.json"))
        )
        health = run(ml.MercadoLibreCollector(settings).health())
        assert health.configured and health.ok is True
        assert health.details["token_source"] == "refresh_token"
        assert health.details["catalog_search"] == "ok"
        assert health.details["refresh_token_rotated"] is True
        assert "access_token" not in json.dumps(health.details).replace("token_source", "")

        catalog.mock(return_value=httpx.Response(403, json=load("ml_policy_403.json")))
        denied = run(ml.MercadoLibreCollector(settings).health())
    assert denied.ok is False and denied.details["catalog_search"] == "denied_by_policy"
    assert "PolicyAgent" in denied.message


def test_health_token_failure():
    settings = make_settings(ml_refresh_token=None)
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{BASE}/oauth/token").mock(
            return_value=httpx.Response(401, json={"error": "invalid_client"})
        )
        health = run(ml.MercadoLibreCollector(settings).health())
    assert health.configured is True and health.ok is False
    assert "authentication failed" in health.message


def test_changed_static_access_token_replaces_cached_one():
    with respx.mock(assert_all_called=False) as router:
        search = router.get(f"{BASE}/products/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        run(ml.MercadoLibreCollector(Settings(_env_file=None, ml_access_token="APP_USR-OLD")).search("a"))
        run(ml.MercadoLibreCollector(Settings(_env_file=None, ml_access_token="APP_USR-NEW")).search("a"))
    assert [c.request.headers["Authorization"] for c in search.calls] == [
        "Bearer APP_USR-OLD",
        "Bearer APP_USR-NEW",
    ]
