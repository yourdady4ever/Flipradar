"""Regression tests for verified collector defects (review findings collectors#0-6, security#1, security#6).

No network: respx mocks + fake DNS, like the other collector tests.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import socket
import tracemalloc
import zlib
from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from flipradar.collectors import fx_sources, jsonld, netguard, rss
from flipradar.collectors.base import CollectorError
from flipradar.config import Settings

SHOP = "https://shop.example.com"
PUBLIC_IP = "93.184.215.14"
FEED_A = "https://a.example/feed"
FEED_B = "https://b.example/feed"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
ER_API_URL = "https://open.er-api.com/v6/latest/USD"
BANXICO_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF43718/datos/oportuno"
TODAY = date(2026, 11, 1)


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------------------------------------
# collectors#0 - a model number before the price must not be merged into a space-grouped amount
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("Galaxy A54 150 pesos", 15000),  # "A54" is a model code: only "150 pesos" is an amount
        ("Funda Galaxy A54 150 MXN", 15000),
        ("Control Xbox 360 en 500 pesos", 50000),
        ("Kindle Paperwhite: 1 999 pesos", 199900),  # a delimiter anchors the space-grouped amount
        ("Switch OLED (12 999 pesos)", 1299900),
        ("12 999 pesos Switch OLED", 1299900),  # at the start of the text
        ("1 299 999 pesos", 129999900),
    ],
)
def test_space_grouped_amount_positive(text, cents):
    assert rss.extract_price(text).cents == cents


@pytest.mark.parametrize(
    "text",
    [
        "Control Xbox 360 500 pesos",  # 360,500 or Xbox 360 at 500 -> ambiguous
        "Xbox 360 500 MXN",
        "Funda iPhone 13 100 pesos",  # 13,100 or iPhone 13 at 100
        "Nintendo Switch 2 999 pesos",  # 2,999 or Switch 2 at 999
        "Switch OLED 12 999 pesos",  # same shape: cannot be told apart without guessing
        "Kindle Paperwhite 1 999 pesos",
    ],
)
def test_space_grouped_amount_after_a_word_is_ambiguous(text):
    extraction = rss.extract_price(text)
    assert extraction.cents is None
    assert "ambiguous" in extraction.reason


def test_space_grouped_model_number_end_to_end_listing_is_skipped():
    item = rss.FeedItem(
        title="Control Xbox 360 500 pesos",
        link="https://deals.example.com/1",
        guid="x360",
        published=None,
        description="",
        categories=[],
        merchant=None,
        explicit_price=None,
        condition_text=None,
    )
    listing, reason = rss.item_to_listing(item, FEED_A)
    assert listing is None
    assert "ambiguous" in reason


# ------------------------------------------------------------------------------------------------
# collectors#1 - "$12 999 pesos" / "MXN 12 999" / "$ 3 499" must not be read as 12 or 3 pesos
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("Pantalla Samsung 55 pulgadas $12 999 pesos", 1299900),
        ("Pantalla Samsung 55 pulgadas $12 999 pesos", 1299900),  # NBSP grouping
        ("$1 299 pesos audífonos", 129900),
        ("Monitor 27 $ 3 499 MXN", 349900),
        ("MXN 12 999 pesos", 1299900),
        ("Switch OLED $12 999 pesos", 1299900),
        # unchanged neighbours
        ("iPhone $12,999 128GB", 1299900),
        ("$ 899", 89900),
        ("Pack 999 MXN", 99900),
        ("Xbox Series S MXN 12999", 1299900),
        ("iPhone 15 128 GB a $13,499.50 MXN", 1349950),
    ],
)
def test_prefixed_space_grouped_amount_positive(text, cents):
    assert rss.extract_price(text).cents == cents


@pytest.mark.parametrize(
    "text",
    [
        "MXN 12 999 iPhone 15",  # "12 999" without a pesos/MXN suffix: 12 or 12,999?
        "Monitor 27 $ 3 499",
        "Pantalla $12 999",
        "Memoria USB $999 128GB",  # 999 or 999,128?
    ],
)
def test_prefixed_amount_followed_by_digit_group_is_ambiguous(text):
    extraction = rss.extract_price(text)
    assert extraction.cents is None
    assert "ambiguous" in extraction.reason


def test_prefixed_space_grouped_amount_does_not_let_another_amount_win():
    # "$12 999" is ambiguous, so the item must not silently resolve to the other amount
    assert rss.extract_price("Pantalla $12 999 o $11,999 con tarjeta").cents is None


# ------------------------------------------------------------------------------------------------
# collectors#3 - installment / coupon / savings / free-shipping-threshold amounts are not the price
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "iPhone 15 a 12 MSI de $1,249",
        "iPhone 15 a 12 meses sin intereses de $1,249",
        "PS5 en 18 pagos fijos de $599",
        "Laptop $749 mensuales",
        "Laptop $749/mes",
        "PS5 con cupón de $1,500",
        "Ahorra $2,000 en PS5",
        "PS5: ahorra hasta $2,000",
        "iPhone 15 con $3,000 de descuento",
        "iPhone 15 con descuento de $3,000",
        "iPhone 15 con 3,000 pesos de descuento",
        "Bonificación de $500 en Xbox",
        "Envío gratis en compras mayores a $299: Audífonos JBL",
        "Envío gratis en compras desde $299",
    ],
)
def test_non_price_amounts_are_not_taken_as_the_price(text):
    assert rss.extract_price(text).cents is None


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("Laptop $8,999 a 12 MSI de $749", 899900),  # installment amount dropped, price kept
        ("Laptop $8,999 o $749 mensuales", 899900),
        ("Audífonos JBL $899, envío gratis en compras mayores a $299", 89900),
        ("MX$1,299 con cupón", 129900),  # price obtained with a coupon (no coupon amount)
        ("Bocina 1,299 pesos y 5% de descuento", 129900),
        ("Garantía 12 meses $4,999", 499900),
    ],
)
def test_price_kept_next_to_non_price_amounts(text, cents):
    assert rss.extract_price(text).cents == cents


@pytest.mark.parametrize(
    "text",
    [
        "PS5 $9,999 con cupón de $1,500",  # before or after the coupon?
        "iPhone 15 $15,999 con $3,000 de descuento",
    ],
)
def test_price_next_to_a_discount_amount_is_ambiguous(text):
    extraction = rss.extract_price(text)
    assert extraction.cents is None
    assert "discount" in extraction.reason


def test_installment_only_title_falls_back_to_description():
    extraction = rss.extract_price("iPhone 15 a 12 MSI de $1,249", "Precio de contado $14,988")
    assert extraction.cents == 1498800
    assert extraction.reason == "single price in description"


def test_coupon_title_skips_the_item_end_to_end():
    item = rss.FeedItem(
        title="Apple iPhone 15 128GB nuevo con cupón de $6,500",
        link="https://deals.example.com/2",
        guid="cupon",
        published=None,
        description="",
        categories=[],
        merchant=None,
        explicit_price=None,
        condition_text=None,
    )
    listing, reason = rss.item_to_listing(item, FEED_A)
    assert listing is None and "discount" in reason


# ------------------------------------------------------------------------------------------------
# collectors#4 - search must filter by the query before truncating
# ------------------------------------------------------------------------------------------------


def _feed(titles: list[str], prefix: str) -> bytes:
    items = "".join(
        f"<item><title>{title}</title><guid>{prefix}-{i}</guid><link>https://x.example/{prefix}/{i}</link></item>"
        for i, title in enumerate(titles)
    )
    return f'<rss version="2.0"><channel><title>t</title>{items}</channel></rss>'.encode()


def test_search_finds_matches_after_500_items_of_earlier_feeds():
    settings = Settings(_env_file=None, rss_feeds=f"{FEED_A},{FEED_B}")
    feed_a = _feed([f"Audifonos lote {i} por $1,299" for i in range(rss.MAX_ITEMS_PER_FEED)], "a")
    feed_b = _feed([f"Nintendo Switch lote {i} por $5,999" for i in range(50)], "b")
    with respx.mock(assert_all_called=True) as router:
        router.get(FEED_A).mock(return_value=httpx.Response(200, content=feed_a))
        router.get(FEED_B).mock(return_value=httpx.Response(200, content=feed_b))
        collector = rss.RssDealsCollector(settings)
        results = run(collector.search("nintendo switch", limit=50))
        assert len(results) == 50
        assert all("Nintendo Switch" in listing.title for listing in results)
        assert len(run(collector.search("nintendo switch", limit=10))) == 10
        # fetch_latest still honours its own limit
        assert len(run(collector.fetch_latest(limit=500))) == 500


# ------------------------------------------------------------------------------------------------
# collectors#2 - a stale FX quote must not stop the fallback chain
# ------------------------------------------------------------------------------------------------


@pytest.fixture
def _fx_cache():
    fx_sources.reset_cache()
    yield
    fx_sources.reset_cache()


def _er_payload(rate: float, day: str) -> dict:
    return {
        "result": "success",
        "base_code": "USD",
        "time_last_update_utc": f"{day}, 01 Nov 2026 00:00:01 +0000",
        "rates": {"MXN": rate},
    }


def test_stale_frankfurter_quote_falls_through_to_exchangerate_api(_fx_cache):
    settings = Settings(_env_file=None)
    with respx.mock(assert_all_called=False) as router:
        frank = router.get(FRANKFURTER_URL).mock(
            return_value=httpx.Response(
                200, json={"base": "USD", "date": "2026-05-15", "rates": {"MXN": 17.2}}
            )
        )
        er = router.get(ER_API_URL).mock(return_value=httpx.Response(200, json=_er_payload(19.4, "Sun")))
        quote = run(fx_sources.get_usd_mxn(settings, today=TODAY))
        assert frank.call_count == 1 and er.call_count == 1
    assert quote.source == "exchangerate_api_open"
    assert quote.rate == Decimal("19.4") and quote.rate_date == "2026-11-01"


def test_stale_banxico_quote_falls_through_to_public_sources(_fx_cache):
    settings = Settings(_env_file=None, banxico_token="a" * 64)
    stale_fix = {
        "bmx": {"series": [{"idSerie": "SF43718", "datos": [{"fecha": "02/10/2026", "dato": "18.1234"}]}]}
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(BANXICO_URL).mock(return_value=httpx.Response(200, json=stale_fix))
        router.get(FRANKFURTER_URL).mock(
            return_value=httpx.Response(
                200, json={"base": "USD", "date": "2026-10-30", "rates": {"MXN": 18.1}}
            )
        )
        quote = run(fx_sources.get_usd_mxn(settings, today=TODAY))
    assert quote.source == "frankfurter_ecb" and quote.rate_date == "2026-10-30"


def test_quote_exactly_max_age_is_still_fresh(_fx_cache):
    settings = Settings(_env_file=None)
    with respx.mock(assert_all_called=False) as router:
        router.get(FRANKFURTER_URL).mock(
            return_value=httpx.Response(
                200, json={"base": "USD", "date": "2026-10-25", "rates": {"MXN": 18.1}}
            )
        )
        er = router.get(ER_API_URL)
        quote = run(fx_sources.get_usd_mxn(settings, today=TODAY))
        assert er.call_count == 0
    assert quote.source == "frankfurter_ecb"
    assert fx_sources.MAX_QUOTE_AGE_DAYS == 7


def test_all_sources_stale_reports_every_reason(_fx_cache):
    settings = Settings(_env_file=None)
    with respx.mock(assert_all_called=False) as router:
        router.get(FRANKFURTER_URL).mock(
            return_value=httpx.Response(
                200, json={"base": "USD", "date": "2026-05-15", "rates": {"MXN": 17.2}}
            )
        )
        router.get(ER_API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": "success",
                    "base_code": "USD",
                    "time_last_update_utc": "Fri, 01 May 2026 00:00:01 +0000",
                    "rates": {"MXN": 17.5},
                },
            )
        )
        with pytest.raises(CollectorError) as err:
            run(fx_sources.get_usd_mxn(settings, today=TODAY))
    message = str(err.value)
    assert message.startswith("no USD/MXN exchange rate available: ")
    assert "banxico: not configured" in message
    assert "frankfurter: stale USD/MXN rate dated 2026-05-15" in message
    assert "exchangerate-api: stale USD/MXN rate dated 2026-05-01" in message
    assert message.index("frankfurter: stale") < message.index("exchangerate-api: stale")


def test_service_refresh_stores_the_fresh_fallback(db, settings, _fx_cache, monkeypatch):
    """End to end through services.fx.refresh_usd_mxn (real chain, respx, temp DB)."""
    from flipradar.services import fx

    class _FixedNow(fx_sources.datetime):
        @classmethod
        def now(cls, tz=None):
            return fx_sources.datetime(2026, 11, 1, 12, 0, tzinfo=tz)

    monkeypatch.setattr(fx_sources, "datetime", _FixedNow)  # refresh_usd_mxn does not pass `today` down
    public = settings.model_copy(update={"fx_public_sources_enabled": True})
    with respx.mock(assert_all_called=False) as router:
        router.get(FRANKFURTER_URL).mock(
            return_value=httpx.Response(
                200, json={"base": "USD", "date": "2026-05-15", "rates": {"MXN": 17.2}}
            )
        )
        router.get(ER_API_URL).mock(return_value=httpx.Response(200, json=_er_payload(19.4, "Sun")))
        row = run(fx.refresh_usd_mxn(db, public, today=TODAY))
    assert (row.rate, row.source, row.rate_date) == ("19.4", "exchangerate_api_open", "2026-11-01")
    assert fx.latest_usd_mxn(db, public, today=TODAY)[:2] == (Decimal("19.4"), "exchangerate_api_open")


# ------------------------------------------------------------------------------------------------
# collectors#5 / security#6 - hostile JSON-LD must not crash check_url
# collectors#6 - "12.999" is the ambiguous dot-thousands form
# ------------------------------------------------------------------------------------------------


class FakeDNS:
    def __init__(self, mapping):
        self.mapping = mapping

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        value = self.mapping.get(host)
        if value is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in value]


@pytest.fixture
def dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", FakeDNS({"shop.example.com": [PUBLIC_IP]}))


VALID_BLOCK = {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "Consola PS5 Slim",
    "offers": {"@type": "Offer", "price": "8499.00", "priceCurrency": "MXN"},
}


def _page(*blocks: str) -> httpx.Response:
    scripts = "".join(f'<script type="application/ld+json">{b}</script>' for b in blocks)
    return httpx.Response(
        200, text=f"<html><head>{scripts}</head><body>x</body></html>", headers={"content-type": "text/html"}
    )


async def _check(url, settings):
    async with httpx.AsyncClient() as client:
        return await jsonld.check_url(url, settings, client)


def test_deeply_nested_jsonld_block_is_skipped_and_other_blocks_survive():
    deep = "[" * 20000 + "]" * 20000
    unterminated = "[" * 100000
    documents = jsonld.extract_jsonld_blocks(
        f'<script type="application/ld+json">{deep}</script>'
        f'<script type="application/ld+json">{unterminated}</script>'
        f'<script type="application/ld+json">{json.dumps(VALID_BLOCK)}</script>'
    )
    assert documents == [VALID_BLOCK]


def test_check_url_with_deeply_nested_jsonld_finds_the_valid_offer(dns):
    settings = Settings(_env_file=None)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
        router.get(f"{SHOP}/p/1").mock(return_value=_page("[" * 100000, json.dumps(VALID_BLOCK)))
        result = run(_check(f"{SHOP}/p/1", settings))
    assert result.found is True and result.raw.price_cents == 849900


@pytest.mark.parametrize(
    "price",
    ["99999999999999999999999999999", "9" * 27, "1000000000000000000", 1e300, "1000000000.01"],
)
def test_absurd_prices_are_rejected(price):
    assert jsonld.parse_price_value(price) is None


def test_price_cap_keeps_large_but_sane_prices():
    assert jsonld.parse_price_value("1000000000") == Decimal("1000000000")
    assert jsonld.parse_price_value(250000.5) == Decimal("250000.5")


@pytest.mark.parametrize("price", ["99999999999999999999999999999", 1e300])
def test_check_url_with_absurd_price_reports_not_found(dns, price):
    settings = Settings(_env_file=None)
    block = {**VALID_BLOCK, "offers": {"@type": "Offer", "price": price, "priceCurrency": "MXN"}}
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
        router.get(f"{SHOP}/p/2").mock(return_value=_page(json.dumps(block)))
        result = run(_check(f"{SHOP}/p/2", settings))
    assert result.found is False and result.raw is None


def test_check_url_unexpected_parse_error_becomes_not_found(dns, monkeypatch):
    settings = Settings(_env_file=None)

    def boom(*args, **kwargs):
        raise ArithmeticError("synthetic parser failure")

    monkeypatch.setattr(jsonld, "extract_offer", boom)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
        router.get(f"{SHOP}/p/3").mock(return_value=_page(json.dumps(VALID_BLOCK)))
        result = run(_check(f"{SHOP}/p/3", settings))
    assert result.found is False and result.allowed_by_robots is True
    assert "could not be parsed" in result.message


def test_check_url_endpoint_returns_found_false_for_hostile_jsonld(tmp_path, monkeypatch):
    """POST /api/v1/check-url must answer 200/found=false (not 500) for both hostile payloads."""
    from fastapi.testclient import TestClient

    from flipradar.db import session_scope
    from flipradar.main import create_app
    from flipradar.security import create_token
    from tests.conftest import make_settings

    monkeypatch.setattr(socket, "getaddrinfo", FakeDNS({"shop.example.com": [PUBLIC_IP]}))
    monkeypatch.setattr(netguard, "build_guarded_client", lambda settings, **kw: httpx.AsyncClient())
    app = create_app(make_settings(tmp_path))
    huge = {**VALID_BLOCK, "offers": {"@type": "Offer", "price": "9" * 29, "priceCurrency": "MXN"}}
    with TestClient(app, raise_server_exceptions=False) as client:
        with session_scope() as session:
            _, plain = create_token(session, "pytest")
        headers = {"Authorization": f"Bearer {plain}"}
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
            router.get(f"{SHOP}/deep").mock(return_value=_page("[" * 200000 + "]" * 200000))
            router.get(f"{SHOP}/huge").mock(return_value=_page(json.dumps(huge)))
            for path in ("/deep", "/huge"):
                response = client.post("/api/v1/check-url", json={"url": f"{SHOP}{path}"}, headers=headers)
                assert response.status_code == 200, (path, response.text)
                assert response.json()["found"] is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12.999", None),  # dot-thousands (es-MX/es-ES) or 12.999 pesos -> ambiguous
        ("1.299", None),
        ("$ 12.999", None),
        ("12.99", Decimal("12.99")),
        ("12.9", Decimal("12.9")),
        ("8499.000", Decimal("8499.000")),  # 4 integer digits cannot be a thousands group
        ("8499.000000", Decimal("8499.000000")),
        (12.999, Decimal("12.999")),  # JSON numbers are unambiguous ('.' is the decimal mark)
    ],
)
def test_parse_price_value_dot_thousands(value, expected):
    assert jsonld.parse_price_value(value) == expected


def test_dot_thousands_offer_is_not_stored_as_13_pesos():
    documents = [
        {
            "@type": "Product",
            "name": "Laptop X",
            "offers": {"@type": "Offer", "price": "12.999", "priceCurrency": "MXN"},
        }
    ]
    assert jsonld.extract_offer(documents, f"{SHOP}/p") is None


# ------------------------------------------------------------------------------------------------
# security#1 - decompression bombs must not bypass the response cap
# ------------------------------------------------------------------------------------------------

ZEROS_MIB = 32


def _streamed(body: bytes, encoding: str | None):
    """respx side effect: a STREAMED response, like a real connection.  (``httpx.Response(content=bytes)``
    would be read and decoded eagerly by httpx itself, which is not the production code path.)"""

    def respond(request):
        async def chunks():
            for i in range(0, len(body), 65536):
                yield body[i : i + 65536]

        headers = {"content-encoding": encoding} if encoding else {}
        return httpx.Response(200, content=chunks(), headers={**headers, "content-type": "text/plain"})

    return respond


@pytest.fixture(scope="module")
def gzip_bomb() -> bytes:
    return gzip.compress(b"\0" * (ZEROS_MIB * 1024 * 1024), compresslevel=9)


@pytest.fixture(scope="module")
def double_gzip_bomb(gzip_bomb) -> bytes:
    return gzip.compress(gzip_bomb, compresslevel=9)


def _peak_while(coro_factory):
    tracemalloc.start()
    try:
        error = None
        try:
            run(coro_factory())
        except Exception as exc:  # noqa: BLE001 - returned to the test
            error = exc
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return error, peak


async def _guarded(path, max_bytes):
    async with httpx.AsyncClient() as client:
        return await netguard.guarded_get(client, f"{SHOP}{path}", max_bytes=max_bytes)


def test_single_gzip_bomb_is_capped_with_bounded_memory(dns, gzip_bomb):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/bomb").mock(side_effect=_streamed(gzip_bomb, "gzip"))
        error, peak = _peak_while(lambda: _guarded("/bomb", 512 * 1024))
    assert isinstance(error, netguard.ResponseTooLarge)
    assert peak < 8 * 1024 * 1024, peak  # decoded size is 32 MiB


def test_stacked_gzip_bomb_is_rejected_with_bounded_memory(dns, double_gzip_bomb):
    assert len(double_gzip_bomb) < 64 * 1024
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(side_effect=_streamed(double_gzip_bomb, "gzip, gzip"))
        error, peak = _peak_while(lambda: _guarded("/robots.txt", jsonld.MAX_ROBOTS_BYTES))
    assert isinstance(error, CollectorError)
    assert peak < 8 * 1024 * 1024, peak


def test_check_url_robots_bomb_is_conservative(dns, double_gzip_bomb):
    settings = Settings(_env_file=None)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(side_effect=_streamed(double_gzip_bomb, "gzip, gzip"))
        page = router.get(f"{SHOP}/p/1").mock(return_value=_page(json.dumps(VALID_BLOCK)))
        error, peak = _peak_while(lambda: _check(f"{SHOP}/p/1", settings))
        assert page.call_count == 0
    assert error is None
    assert peak < 8 * 1024 * 1024, peak


def test_unknown_content_encoding_is_rejected(dns):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/br").mock(side_effect=_streamed(b"\x1b\x00\x00", "br"))
        with pytest.raises(CollectorError, match="content encoding"):
            run(_guarded("/br", 4096))


@pytest.mark.parametrize(
    ("encoding", "encode"),
    [
        ("gzip", gzip.compress),
        ("x-gzip", gzip.compress),
        ("deflate", zlib.compress),  # zlib-wrapped (RFC 9110)
        ("deflate", lambda data: zlib.compress(data)[2:-4]),  # raw deflate sent by some servers
        ("identity", lambda data: data),
    ],
)
def test_legitimate_encoded_bodies_are_decoded(dns, encoding, encode):
    body = b"User-agent: *\nDisallow: /private\n" * 50
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{SHOP}/robots.txt").mock(side_effect=_streamed(encode(body), encoding))
        fetched = run(_guarded("/robots.txt", 4096))
        sent = route.calls[0].request.headers["accept-encoding"]
    assert fetched.content == body
    assert sent == netguard.ACCEPT_ENCODING == "gzip, deflate"


def test_encoded_body_cap_is_exact(dns):
    body = b"a" * 5000
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/x").mock(side_effect=_streamed(gzip.compress(body), "gzip"))
        assert run(_guarded("/x", 5000)).content == body
        with pytest.raises(netguard.ResponseTooLarge):
            run(_guarded("/x", 4999))


def test_corrupt_gzip_body_is_a_collector_error(dns):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/x").mock(side_effect=_streamed(b"not gzip at all", "gzip"))
        with pytest.raises(CollectorError, match="decode"):
            run(_guarded("/x", 4096))


def test_rss_feed_gzip_bomb_is_capped(gzip_bomb, double_gzip_bomb):
    settings = Settings(_env_file=None, rss_feeds=f"{FEED_A},{FEED_B}")
    with respx.mock() as router:
        a = router.get(FEED_A).mock(side_effect=_streamed(gzip_bomb, "gzip"))
        router.get(FEED_B).mock(side_effect=_streamed(double_gzip_bomb, "gzip, gzip"))
        collector = rss.RssDealsCollector(settings)
        error, peak = _peak_while(collector.fetch_latest)
        assert a.calls[0].request.headers["accept-encoding"] == "gzip, deflate"
    assert isinstance(error, CollectorError)
    assert "limit" in collector.last_errors[FEED_A]
    assert "content encoding" in collector.last_errors[FEED_B]
    assert peak < 8 * 1024 * 1024, peak


def test_rss_gzip_feed_still_parses():
    settings = Settings(_env_file=None, rss_feeds=FEED_A)
    feed = _feed(["PS5 Slim $7,999"], "g")
    with respx.mock() as router:
        router.get(FEED_A).mock(side_effect=_streamed(gzip.compress(feed), "gzip"))
        listings = run(rss.RssDealsCollector(settings).fetch_latest())
    assert [listing.price_cents for listing in listings] == [799900]
