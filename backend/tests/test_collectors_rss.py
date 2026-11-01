"""RSS/Atom deal-feed collector tests (fixtures + respx; no network)."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from flipradar.collectors import netguard, rss
from flipradar.collectors.base import CollectorError, CollectorNotConfigured
from flipradar.collectors.registry import get_collector
from flipradar.config import Settings

FIX = Path(__file__).parent / "fixtures" / "collectors"
FEED1 = "https://deals.example.com/feed.xml"
FEED2 = "https://atom.example.org/atom.xml"


def run(coro):
    return asyncio.run(coro)


def fixture_bytes(name):
    return (FIX / name).read_bytes()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(netguard, "sleep", fake_sleep)
    return sleeps


# ------------------------------------------------------------------------------------------------
# price extraction
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("iPhone 15 128GB $12,999", 1299900),
        ("PS5 Slim $12,999.00 en Liverpool", 1299900),
        ("Xbox Series S MXN 12999", 1299900),
        ("Switch OLED 12 999 pesos", 1299900),
        ("MX$1,299 con cupón", 129900),
        ("Bocina 1,299 pesos y 5% de descuento", 129900),
        ("iPhone 15 128 GB a $13,499.50 MXN", 1349950),
        ("Kindle $1,499 (aprox $80 USD)", 149900),
        ("Pack 999 MXN", 99900),
        ("$ 899", 89900),
    ],
)
def test_extract_price_positive(text, cents):
    assert rss.extract_price(text).cents == cents


@pytest.mark.parametrize(
    "text",
    [
        "Antes $15,999 ahora $12,999",  # two prices -> ambiguous
        "Laptop desde $14,999",  # 'from' qualifier
        "A partir de $999 audífonos",
        "Consola $499 USD",  # dollars
        "TV US$300",
        "Monitor U$S 150",
        "Laptop $12.999",  # European thousands separator -> ambiguous
        "Precio $12,99",  # comma decimals -> ambiguous
        "Galaxy S24 128 GB 5G",  # no price at all
        "Descuento de 20%",
        "$0 envío",
    ],
)
def test_extract_price_negative(text):
    assert rss.extract_price(text).cents is None


def test_extract_price_falls_back_to_description_only_when_title_has_none():
    assert rss.extract_price("Cámara Sony a6400", "Ahora en MXN 15,499.00").cents == 1549900
    assert rss.extract_price("Cámara $15,000", "otro precio $14,000").cents == 1500000


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PS5 nueva y sellada", True),
        ("Condición: nuevo", True),
        ("iPhone (Nuevo)", True),
        ("Brand new AirPods", True),
        ("Nuevo precio mínimo PS5", False),
        ("nuevo mínimo histórico", False),
        ("iPhone usado", False),
    ],
)
def test_says_new(text, expected):
    assert rss.says_new(text) is expected


def test_matches_query_accent_insensitive():
    assert rss.matches_query("Cámara Sony Alpha a6400", "camara sony")
    assert rss.matches_query("Audífonos SONY WH-1000XM5", "audifonos wh 1000xm5")
    assert not rss.matches_query("Cámara Canon", "camara sony")
    assert not rss.matches_query("Cámara Sony", "  ")


# ------------------------------------------------------------------------------------------------
# parsing
# ------------------------------------------------------------------------------------------------


def test_parse_rss2_items():
    items = rss.parse_feed(fixture_bytes("feed_rss2.xml"))
    assert len(items) == 6
    first = items[0]
    assert first.title == "Consola PS5 Slim Digital $7,999 en Liverpool"
    assert first.link == "https://deals.example.com/oferta/1"
    assert first.guid == "deal-1"
    assert first.published == datetime(2026, 10, 31, 0, 30, tzinfo=UTC)
    assert first.merchant == "Liverpool"  # <category domain="merchant">
    assert first.categories == ["Liverpool", "Videojuegos"]
    assert "alert" not in first.description and "Nueva y sellada" in first.description
    assert items[2].merchant == "Tienda Foto"  # <merchant> element
    assert items[2].description == "Ahora en MXN 15,499.00 con envío gratis"
    assert items[3].published == datetime(2026, 10, 29, 10, 0, tzinfo=UTC)  # dc:date


def test_rss2_items_to_listings_skip_ambiguous():
    items = rss.parse_feed(fixture_bytes("feed_rss2.xml"))
    now = datetime(2026, 11, 1, tzinfo=UTC)
    results = [rss.item_to_listing(item, FEED1, now=now) for item in items]
    listings = [listing for listing, _ in results if listing is not None]
    reasons = [reason for listing, reason in results if listing is None]
    assert [listing.price_cents for listing in listings] == [799900, 1549900, 199900]
    assert len(reasons) == 3
    assert any("2 different prices" in r for r in reasons)
    assert any("'from'" in r for r in reasons)
    assert any("no MXN price" in r for r in reasons)

    ps5, camera, kindle = listings
    assert ps5.source_key == "rss_deals" and ps5.currency == "MXN"
    assert ps5.condition_raw == "new"  # "Nueva y sellada" in description
    assert ps5.seller_name == "Liverpool"
    assert ps5.url == "https://deals.example.com/oferta/1"
    assert ps5.observed_at == datetime(2026, 10, 31, 0, 30, tzinfo=UTC)
    assert ps5.attributes["published_at"] == "2026-10-31T00:30:00+00:00"
    assert len(ps5.external_id) == 32
    # external id is stable and feed-scoped
    assert rss.item_to_listing(items[0], FEED1, now=now)[0].external_id == ps5.external_id
    assert rss.item_to_listing(items[0], FEED2, now=now)[0].external_id != ps5.external_id
    assert camera.condition_raw is None and camera.seller_name == "Tienda Foto"
    assert camera.attributes["price_extraction"] == "single price in description"
    assert camera.observed_at == now  # no date in the item
    assert kindle.condition_raw is None  # "Nuevo precio mínimo" is not a condition


def test_parse_atom_entries():
    items = rss.parse_feed(fixture_bytes("feed_atom.xml"))
    assert len(items) == 2
    entry = items[0]
    assert entry.title == "iPhone 15 128 GB (Nuevo) a $13,499.50 MXN"
    assert entry.link == "https://atom.example.org/deals/iphone-15"
    assert entry.guid == "tag:atom.example.org,2026:deal-100"
    assert entry.published == datetime(2026, 10, 31, 8, 0, tzinfo=UTC)
    assert entry.categories == ["Celulares"]
    listing, _ = rss.item_to_listing(entry, FEED2)
    assert listing.price_cents == 1349950 and listing.condition_raw == "new"
    assert listing.category_name == "Celulares"
    assert rss.item_to_listing(items[1], FEED2)[0] is None


def test_explicit_price_element_needs_currency_marker():
    xml = b"""<rss version="2.0"><channel>
      <item><title>Producto A $1,000 o $900</title><price>$950</price><guid>a</guid></item>
      <item><title>Producto B</title><price>950</price><guid>b</guid></item>
    </channel></rss>"""
    first, second = rss.parse_feed(xml)
    listing, reason = rss.item_to_listing(first, FEED1)
    assert listing.price_cents == 95000 and reason == "price element"
    assert rss.item_to_listing(second, FEED1)[0] is None


def test_parse_rejects_entity_expansion_and_garbage():
    bomb = b"""<?xml version="1.0"?>
    <!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>
    <rss><channel><item><title>&lol2; $100</title></item></channel></rss>"""
    with pytest.raises(CollectorError, match="not valid/safe XML"):
        rss.parse_feed(bomb)
    with pytest.raises(CollectorError):
        rss.parse_feed(b"<html><body>not a feed")


# ------------------------------------------------------------------------------------------------
# collector (HTTP)
# ------------------------------------------------------------------------------------------------


def test_info_and_configuration():
    collector = get_collector("rss_deals", Settings(_env_file=None))
    assert collector.info.kind == "feed"
    assert collector.info.requires_credentials is False
    assert collector.info.capabilities == ["search", "feed"]
    assert not collector.is_configured()
    with pytest.raises(CollectorNotConfigured):
        run(collector.fetch_latest())
    assert run(collector.health()).configured is False
    assert rss.RssDealsCollector(Settings(_env_file=None, rss_feeds=f"{FEED1}, {FEED2}")).is_configured()


def test_search_across_feeds_filters_by_tokens():
    settings = Settings(_env_file=None, rss_feeds=f"{FEED1},{FEED2}")
    with respx.mock(assert_all_called=True) as router:
        router.get(FEED1).mock(return_value=httpx.Response(200, content=fixture_bytes("feed_rss2.xml")))
        router.get(FEED2).mock(return_value=httpx.Response(200, content=fixture_bytes("feed_atom.xml")))
        collector = rss.RssDealsCollector(settings)
        results = run(collector.search("camara SONY"))
        assert [listing.title for listing in results] == ["Cámara Sony Alpha a6400 kit"]
        assert collector.last_skipped == {FEED1: 3, FEED2: 1}
        everything = run(collector.fetch_latest())
    assert len(everything) == 4


def test_429_retry_after_then_success(_no_sleep):
    settings = Settings(_env_file=None, rss_feeds=FEED1)
    with respx.mock() as router:
        route = router.get(FEED1).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "120"}),
                httpx.Response(200, content=fixture_bytes("feed_rss2.xml")),
            ]
        )
        listings = run(rss.RssDealsCollector(settings).fetch_latest())
        assert route.call_count == 2
    assert len(listings) == 3
    assert _no_sleep == [60.0]  # capped


def test_429_twice_and_partial_failure():
    settings = Settings(_env_file=None, rss_feeds=f"{FEED1},{FEED2}")
    with respx.mock() as router:
        router.get(FEED1).mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}))
        router.get(FEED2).mock(return_value=httpx.Response(200, content=fixture_bytes("feed_atom.xml")))
        collector = rss.RssDealsCollector(settings)
        listings = run(collector.fetch_latest())
    assert len(listings) == 1
    assert "429" in collector.last_errors[FEED1]


def test_all_feeds_failing_raises_and_size_cap():
    settings = Settings(_env_file=None, rss_feeds=f"{FEED1},{FEED2}")
    huge = b"<rss>" + b" " * (rss.MAX_FEED_BYTES + 10) + b"</rss>"
    with respx.mock() as router:
        router.get(FEED1).mock(return_value=httpx.Response(200, content=huge))
        router.get(FEED2).mock(return_value=httpx.Response(500))
        collector = rss.RssDealsCollector(settings)
        with pytest.raises(CollectorError, match="all RSS feeds failed"):
            run(collector.fetch_latest())
    assert "limit" in collector.last_errors[FEED1]
    assert "HTTP 500" in collector.last_errors[FEED2]


def test_streamed_body_without_content_length_is_capped():
    settings = Settings(_env_file=None, rss_feeds=FEED1)

    async def chunks():
        for _ in range(9):
            yield b"x" * (256 * 1024)

    with respx.mock() as router:
        router.get(FEED1).mock(return_value=httpx.Response(200, content=chunks()))
        collector = rss.RssDealsCollector(settings)
        with pytest.raises(CollectorError):
            run(collector.fetch_latest())
    assert "exceeds" in collector.last_errors[FEED1]


def test_health_per_feed_details():
    settings = Settings(_env_file=None, rss_feeds=f"{FEED1},{FEED2}")
    with respx.mock() as router:
        router.get(FEED1).mock(return_value=httpx.Response(200, content=fixture_bytes("feed_rss2.xml")))
        router.get(FEED2).mock(return_value=httpx.Response(404))
        health = run(rss.RssDealsCollector(settings).health())
    assert health.ok is False
    assert health.details[FEED1] == {"ok": True, "priced_items": 3, "skipped_items": 3}
    assert health.details[FEED2]["ok"] is False


def test_non_http_feed_url_rejected():
    settings = Settings(_env_file=None, rss_feeds="file:///etc/passwd")
    collector = rss.RssDealsCollector(settings)
    with pytest.raises(CollectorError):
        run(collector.fetch_latest())
    assert "unsupported feed URL scheme" in collector.last_errors["file:///etc/passwd"]
