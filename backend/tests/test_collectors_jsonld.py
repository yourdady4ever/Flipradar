"""JSON-LD single-URL check + SSRF guard tests (fixtures + respx + fake DNS; no network)."""

import asyncio
import hashlib
import socket
from decimal import Decimal
from pathlib import Path

import httpcore
import httpx
import pytest
import respx

from flipradar.collectors import jsonld, netguard
from flipradar.config import Settings

FIX = Path(__file__).parent / "fixtures" / "collectors"
SHOP = "https://shop.example.com"
PUBLIC_IP = "93.184.215.14"


def run(coro):
    return asyncio.run(coro)


def html(name):
    return (FIX / name).read_text(encoding="utf-8")


def html_response(name, **kw):
    return httpx.Response(200, text=html(name), headers={"content-type": "text/html; charset=utf-8"}, **kw)


class FakeDNS:
    """Replacement for socket.getaddrinfo: host -> list of addresses (or a callable for rebinding)."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        self.calls.append(host)
        value = self.mapping.get(host)
        if callable(value):
            value = value()
        if value is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        out = []
        for address in value:
            fam = socket.AF_INET6 if ":" in address else socket.AF_INET
            sockaddr = (address, port, 0, 0) if fam == socket.AF_INET6 else (address, port)
            out.append((fam, socket.SOCK_STREAM, 6, "", sockaddr))
        return out


@pytest.fixture
def dns(monkeypatch):
    fake = FakeDNS(
        {
            "shop.example.com": [PUBLIC_IP],
            "www.shop.example.com": [PUBLIC_IP],
            "store.example.mx": ["2606:2800:21f:cb07:6820:80da:af6b:8b2c"],
            "internal.example.com": ["10.0.0.5"],
            "mixed.example.com": [PUBLIC_IP, "192.168.1.10"],
            "metadata.example.com": ["169.254.169.254"],
            "v6local.example.com": ["::1"],
        }
    )
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return fake


@pytest.fixture
def settings():
    return Settings(_env_file=None)


# ------------------------------------------------------------------------------------------------
# netguard: addresses and URLs
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.0.0.1",
        "172.16.5.4",
        "192.168.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "240.0.0.1",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fec0::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "64:ff9b::7f00:1",
        "2002:7f00:1::1",
        "fe80::1%eth0",
        "not-an-ip",
    ],
)
def test_non_public_addresses(address):
    assert netguard.is_public_ip(address) is False


@pytest.mark.parametrize("address", ["8.8.8.8", "93.184.215.14", "2606:4700::1111", "64:ff9b::808:808"])
def test_public_addresses(address):
    assert netguard.is_public_ip(address) is True


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "gopher://example.com/",
        "javascript:alert(1)",
        "http://user:pass@example.com/",
        "https://token@example.com/",
        "http://example.com:8080/",
        "https://example.com:22/",
        "http:///nohost",
        "not a url at all",
    ],
)
def test_parse_and_validate_url_rejects(url):
    with pytest.raises(netguard.UnsafeUrlError):
        netguard.parse_and_validate_url(url)


def test_parse_and_validate_url_accepts_and_extra_ports():
    assert netguard.parse_and_validate_url("https://example.com/a?b=1").host == "example.com"
    assert netguard.parse_and_validate_url("http://example.com:443/").port == 443
    assert netguard.parse_and_validate_url("http://example.com:8080/", allowed_ports=[8080]).port == 8080


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.1.2.3/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://internal.example.com/",
        "http://mixed.example.com/",
        "http://metadata.example.com/",
        "http://v6local.example.com/",
    ],
)
def test_validate_url_rejects_private_targets(dns, url):
    with pytest.raises(netguard.UnsafeUrlError):
        run(netguard.validate_url(url))


def test_validate_url_accepts_public_and_reports_dns_failure(dns):
    assert run(netguard.validate_url("https://shop.example.com/p/1")).host == "shop.example.com"
    assert run(netguard.validate_url("https://store.example.mx/")).host == "store.example.mx"
    with pytest.raises(netguard.CollectorError, match="could not resolve"):
        run(netguard.validate_url("https://does-not-exist.example.net/"))


def test_decimal_and_hex_ip_literals_resolve_and_are_rejected(monkeypatch):
    # getaddrinfo("2130706433") -> 127.0.0.1 on Linux; the resolved address is what gets checked
    monkeypatch.setattr(
        socket, "getaddrinfo", FakeDNS({"2130706433": ["127.0.0.1"], "0x7f.1": ["127.0.0.1"]})
    )
    for url in ("http://2130706433/", "http://0x7f.1/"):
        with pytest.raises(netguard.UnsafeUrlError):
            run(netguard.validate_url(url))


def test_parse_retry_after():
    assert netguard.parse_retry_after("5") == 5.0
    assert netguard.parse_retry_after("999") == 60.0
    assert netguard.parse_retry_after(None) == 2.0
    assert netguard.parse_retry_after("garbage") == 2.0
    assert netguard.parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0  # in the past


# ------------------------------------------------------------------------------------------------
# netguard: guarded GET (redirects, size cap, content type)
# ------------------------------------------------------------------------------------------------


def test_redirect_to_private_ip_rejected(dns):
    async def scenario():
        async with httpx.AsyncClient() as client:
            return await netguard.guarded_get(client, f"{SHOP}/go")

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/go").mock(
            return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
        )
        internal = router.get("http://127.0.0.1/admin")
        with pytest.raises(netguard.UnsafeUrlError, match="127.0.0.1"):
            run(scenario())
        assert internal.call_count == 0


def test_redirect_to_hostname_resolving_private_rejected(dns):
    async def scenario():
        async with httpx.AsyncClient() as client:
            return await netguard.guarded_get(client, f"{SHOP}/go")

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/go").mock(
            return_value=httpx.Response(301, headers={"location": "http://internal.example.com/secret"})
        )
        with pytest.raises(netguard.UnsafeUrlError, match="non-public"):
            run(scenario())


def test_redirect_limit_and_relative_redirects(dns):
    async def scenario(path):
        async with httpx.AsyncClient() as client:
            return await netguard.guarded_get(client, f"{SHOP}{path}")

    with respx.mock(assert_all_called=False) as router:
        for i in range(5):
            router.get(f"{SHOP}/r{i}").mock(
                return_value=httpx.Response(302, headers={"location": f"/r{i + 1}"})
            )
        router.get(f"{SHOP}/r5").mock(return_value=httpx.Response(200, text="ok"))
        result = run(scenario("/r2"))
        assert result.url == f"{SHOP}/r5" and result.redirects == [f"{SHOP}/r3", f"{SHOP}/r4", f"{SHOP}/r5"]
        with pytest.raises(netguard.UnsafeUrlError, match="too many redirects"):
            run(scenario("/r0"))


def test_body_size_cap_declared_and_streamed(dns):
    async def scenario(path, max_bytes):
        async with httpx.AsyncClient() as client:
            return await netguard.guarded_get(client, f"{SHOP}{path}", max_bytes=max_bytes)

    async def chunks():
        for _ in range(10):
            yield b"a" * 1000

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/big").mock(return_value=httpx.Response(200, content=b"a" * 5000))
        router.get(f"{SHOP}/stream").mock(return_value=httpx.Response(200, content=chunks()))
        with pytest.raises(netguard.ResponseTooLarge, match="5000 bytes"):
            run(scenario("/big", 4000))
        with pytest.raises(netguard.ResponseTooLarge, match="exceeds"):
            run(scenario("/stream", 4000))
        assert len(run(scenario("/big", 5000)).content) == 5000


def test_content_type_restriction(dns):
    async def scenario():
        async with httpx.AsyncClient() as client:
            return await netguard.guarded_get(client, f"{SHOP}/x", allowed_content_types=("text/html",))

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/x").mock(return_value=httpx.Response(200, json={"a": 1}))
        with pytest.raises(netguard.UnsupportedContentType, match="application/json"):
            run(scenario())


def test_guarded_transport_blocks_rebinding_at_connect_time(monkeypatch, settings):
    """First lookup (pre-check) is public, the second (connect time) is private -> blocked."""
    answers = iter([[PUBLIC_IP], ["10.0.0.7"]])
    monkeypatch.setattr(socket, "getaddrinfo", FakeDNS({"rebind.example.com": lambda: next(answers)}))
    inner = httpcore.AsyncMockBackend(
        [b"HTTP/1.1 200 OK\r\n", b"Content-Type: text/html\r\n", b"\r\n", b"hi"]
    )

    async def scenario():
        client = netguard.build_guarded_client(
            settings, network_backend=netguard.GuardedNetworkBackend(inner)
        )
        async with client:
            return await netguard.guarded_get(client, "http://rebind.example.com/")

    with pytest.raises(netguard.UnsafeUrlError, match="10.0.0.7"):
        run(scenario())


def test_guarded_transport_connects_to_vetted_public_ip(monkeypatch, settings):
    monkeypatch.setattr(socket, "getaddrinfo", FakeDNS({"ok.example.com": [PUBLIC_IP]}))
    seen = []

    class RecordingBackend(httpcore.AsyncMockBackend):
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            seen.append((host, port))
            return await super().connect_tcp(host, port, timeout, local_address, socket_options)

    inner = RecordingBackend(
        [b"HTTP/1.1 200 OK\r\n", b"Content-Type: text/html\r\n", b"Content-Length: 2\r\n", b"\r\n", b"hi"]
    )

    async def scenario():
        client = netguard.build_guarded_client(
            settings, network_backend=netguard.GuardedNetworkBackend(inner)
        )
        async with client:
            direct_literal = None
            try:
                await client.get("http://127.0.0.1/")
            except netguard.UnsafeUrlError as exc:
                direct_literal = exc
            response = await netguard.guarded_get(client, "http://ok.example.com/")
            return direct_literal, response

    blocked, response = run(scenario())
    assert isinstance(blocked, netguard.UnsafeUrlError)  # even without guarded_get, the transport refuses
    assert response.status_code == 200 and response.content == b"hi"
    assert seen == [(PUBLIC_IP, 80)]


# ------------------------------------------------------------------------------------------------
# domain policy and URL helpers
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "instagram.com",
        "www.instagram.com",
        "amazon.com",
        "www.amazon.com.mx",
        "amazon.co.uk",
        "amzn.to",
        "mercadolibre.com.mx",
        "articulo.mercadolibre.com.mx",
        "www.mercadolivre.com.br",
        "meli.la",
        "FB.ME",
    ],
)
def test_blocked_domains(host):
    assert jsonld.is_blocked_domain(host) is True
    assert jsonld.blocked_domain_reason(host)


@pytest.mark.parametrize(
    "host", ["example.com", "s3.amazonaws.com", "myamazonstore.mx", "notfacebook.com", "liverpool.com.mx", ""]
)
def test_allowed_domains(host):
    assert jsonld.is_blocked_domain(host) is False


def test_blocked_domain_messages_explain_reason():
    assert "manually" in jsonld.blocked_domain_reason("www.amazon.com.mx")
    assert "official API" in jsonld.blocked_domain_reason("articulo.mercadolibre.com.mx")
    assert "Meta" in jsonld.blocked_domain_reason("instagram.com")


def test_canonicalize_url_and_external_id():
    url = "HTTPS://Shop.Example.com:443/p/ps5?utm_source=x&color=black&gclid=abc#reviews"
    assert jsonld.canonicalize_url(url) == "https://shop.example.com/p/ps5?color=black"
    expected = hashlib.sha256(b"https://shop.example.com/p/ps5?color=black").hexdigest()[:24]
    assert jsonld.external_id_for(url) == expected
    assert jsonld.canonicalize_url("http://shop.example.com:8080") == "http://shop.example.com:8080/"


# ------------------------------------------------------------------------------------------------
# JSON-LD extraction
# ------------------------------------------------------------------------------------------------


def test_extract_blocks_skips_malformed():
    documents = jsonld.extract_jsonld_blocks(html("page_product_offer.html"))
    assert len(documents) == 2  # BreadcrumbList + Product; the broken block is skipped


def test_extract_product_with_offer():
    documents = jsonld.extract_jsonld_blocks(html("page_product_offer.html"))
    offer = jsonld.extract_offer(documents, f"{SHOP}/p/ps5-slim")
    assert offer["name"] == "Consola PlayStation 5 Slim Digital 1TB"
    assert offer["brand"] == "Sony"
    assert offer["gtin"] == "0711719542292"
    assert offer["sku"] == "PS5-SLIM-DIG" and offer["mpn"] == "CFI-2015"
    assert offer["price"] == "8499.00" and offer["price_currency"] == "MXN"
    assert offer["price_kind"] == "price" and offer["offer_count"] == 1
    assert offer["availability"] == "InStock" and offer["item_condition"] == "NewCondition"
    assert offer["image"] == f"{SHOP}/img/ps5-slim.jpg"  # relative URL resolved
    assert offer["offer_seller"] == "Tienda Ejemplo"


def test_extract_aggregate_offer_uses_low_price():
    offer = jsonld.extract_offer(jsonld.extract_jsonld_blocks(html("page_aggregate_offer.html")))
    assert offer["price"] == "5999" and offer["price_kind"] == "lowPrice"
    assert offer["brand"] == "Sony"
    assert offer["item_condition"] == "RefurbishedCondition"


def test_extract_graph_with_multiple_offers():
    offer = jsonld.extract_offer(jsonld.extract_jsonld_blocks(html("page_graph.html")))
    assert offer["name"] == "Nintendo Switch OLED Blanco"
    assert (
        offer["price"] == "6899.00"
    )  # lowest AVAILABLE offer ("6,899.00" normalised); sold-out 6500 ignored
    assert offer["price_kind"] == "lowest_offer" and offer["offer_count"] == 3
    assert offer["item_condition"] == "UsedCondition"
    assert offer["gtin"] == "045496883386"
    assert offer["image"] == "https://store.example.mx/img/switch.jpg"


def test_extract_product_group_variants_and_price_specification():
    documents = [
        {
            "@type": "ProductGroup",
            "name": "Playera",
            "hasVariant": [
                {
                    "@type": "Product",
                    "name": "Playera M",
                    "offers": {
                        "@type": "Offer",
                        "priceSpecification": {
                            "@type": "UnitPriceSpecification",
                            "price": 299,
                            "priceCurrency": "MXN",
                        },
                    },
                }
            ],
        }
    ]
    offer = jsonld.extract_offer(documents)
    assert offer["name"] == "Playera M" and offer["price"] == "299" and offer["product_count"] == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (8499, Decimal("8499")),
        (8499.5, Decimal("8499.5")),
        ("8499.00", Decimal("8499.00")),
        ("12,999.00", Decimal("12999.00")),
        ("$ 1,299", Decimal("1299")),
        ("12,99", None),
        ("12.999,00", None),
        ("0", None),
        ("-5", None),
        ("abc", None),
        (True, None),
        (None, None),
    ],
)
def test_parse_price_value(value, expected):
    assert jsonld.parse_price_value(value) == expected


def test_build_raw_listing_fields():
    documents = jsonld.extract_jsonld_blocks(html("page_product_offer.html"))
    url = f"{SHOP}/p/ps5-slim?utm_campaign=x"
    raw = jsonld.build_raw_listing(jsonld.extract_offer(documents, url), url)
    assert raw.source_key == "web_jsonld"
    assert raw.external_id == hashlib.sha256(f"{SHOP}/p/ps5-slim".encode()).hexdigest()[:24]
    assert raw.url == f"{SHOP}/p/ps5-slim"
    assert raw.title == "Consola PlayStation 5 Slim Digital 1TB"
    assert raw.price_cents == 849900 and raw.currency == "MXN"
    assert raw.condition_raw == "new"
    assert raw.brand == "Sony" and raw.gtin == "0711719542292"
    assert raw.seller_name == "shop.example.com"
    assert raw.status == "active"
    assert raw.attributes["sku"] == "PS5-SLIM-DIG"


# ------------------------------------------------------------------------------------------------
# check_url end-to-end (respx + fake DNS)
# ------------------------------------------------------------------------------------------------


async def _check(url, settings):
    async with httpx.AsyncClient() as client:
        return await jsonld.check_url(url, settings, client)


def test_check_url_found(dns, settings):
    with respx.mock(assert_all_called=False) as router:
        robots = router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
        page = router.get(f"{SHOP}/p/ps5-slim").mock(return_value=html_response("page_product_offer.html"))
        result = run(_check(f"{SHOP}/p/ps5-slim?utm_source=newsletter", settings))
        assert robots.call_count == 1 and page.call_count == 1
        assert page.calls[0].request.headers["User-Agent"] == settings.user_agent
    assert result.found is True and result.allowed_by_robots is True
    assert result.raw.price_cents == 849900
    assert result.raw.url == f"{SHOP}/p/ps5-slim"
    assert result.extracted["robots"] == "no robots.txt (HTTP 404)"
    assert "MXN 8499.00" in result.message


def test_check_url_robots_disallow_skips_page(dns, settings):
    robots_txt = "User-agent: *\nDisallow: /p/\n"
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(200, text=robots_txt))
        page = router.get(f"{SHOP}/p/ps5-slim")
        result = run(_check(f"{SHOP}/p/ps5-slim", settings))
        assert page.call_count == 0
    assert result.allowed_by_robots is False and result.found is False
    assert "disallows" in result.message


def test_check_url_robots_specific_agent_group(dns, settings):
    robots_txt = "User-agent: FlipRadar\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(200, text=robots_txt))
        page = router.get(f"{SHOP}/p/1")
        result = run(_check(f"{SHOP}/p/1", settings))
        assert page.call_count == 0
    assert result.allowed_by_robots is False


def test_check_url_robots_allow(dns, settings):
    robots_txt = "User-agent: *\nDisallow: /cart\n"
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(200, text=robots_txt))
        router.get(f"{SHOP}/p/1").mock(return_value=html_response("page_graph.html"))
        result = run(_check(f"{SHOP}/p/1", settings))
    assert result.allowed_by_robots is True and result.found is True
    assert result.raw.price_cents == 689900
    assert result.raw.condition_raw == "used"
    assert "3 offers" in result.message


def test_check_url_robots_server_error_is_conservative(dns, settings):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(503))
        page = router.get(f"{SHOP}/p/1")
        result = run(_check(f"{SHOP}/p/1", settings))
        assert page.call_count == 0
    assert result.allowed_by_robots is False and "503" in result.message


def test_check_url_redirect_rechecks_robots_of_new_host(dns, settings):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
        router.get(f"{SHOP}/p/1").mock(
            return_value=httpx.Response(301, headers={"location": "https://www.shop.example.com/p/1"})
        )
        router.get("https://www.shop.example.com/robots.txt").mock(
            return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        )
        final = router.get("https://www.shop.example.com/p/1")
        result = run(_check(f"{SHOP}/p/1", settings))
        assert final.call_count == 0
    assert result.allowed_by_robots is False and "redirect target" in result.message


def test_check_url_redirect_to_private_ip_raises(dns, settings):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
        router.get(f"{SHOP}/p/1").mock(
            return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        )
        with pytest.raises(netguard.UnsafeUrlError):
            run(_check(f"{SHOP}/p/1", settings))


def test_check_url_redirect_to_blocked_domain(dns, settings):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
        router.get(f"{SHOP}/go").mock(
            return_value=httpx.Response(302, headers={"location": "https://www.amazon.com.mx/dp/B0CHX1W1XY"})
        )
        result = run(_check(f"{SHOP}/go", settings))
    assert result.found is False and "Amazon" in result.message


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/x",
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://internal.example.com/",
    ],
)
def test_check_url_rejects_private_targets_without_requests(dns, settings, url):
    with respx.mock(assert_all_called=False) as router:
        with pytest.raises(netguard.UnsafeUrlError):
            run(_check(url, settings))
        assert router.calls.call_count == 0


def test_check_url_blocked_domain_no_network(dns, settings):
    with respx.mock(assert_all_called=False) as router:
        result = run(_check("https://www.facebook.com/marketplace/item/123", settings))
        assert router.calls.call_count == 0
    assert result.found is False and result.allowed_by_robots is False
    assert result.extracted == {"blocked_domain": "www.facebook.com"}


def test_check_url_body_too_large(dns, settings):
    big = "<html>" + "x" * (netguard.MAX_BODY_BYTES + 1) + "</html>"
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
        router.get(f"{SHOP}/p/1").mock(
            return_value=httpx.Response(200, text=big, headers={"content-type": "text/html"})
        )
        result = run(_check(f"{SHOP}/p/1", settings))
    assert result.found is False and "limit" in result.message


def test_check_url_non_html_and_http_error(dns, settings):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
        router.get(f"{SHOP}/file.pdf").mock(
            return_value=httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        )
        router.get(f"{SHOP}/gone").mock(
            return_value=httpx.Response(404, text="nope", headers={"content-type": "text/html"})
        )
        pdf = run(_check(f"{SHOP}/file.pdf", settings))
        gone = run(_check(f"{SHOP}/gone", settings))
    assert pdf.found is False and "application/pdf" in pdf.message
    assert gone.found is False and "HTTP 404" in gone.message


def test_check_url_no_product_and_no_currency(dns, settings):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
        router.get(f"{SHOP}/plain").mock(
            return_value=httpx.Response(
                200, text="<html><body>hola</body></html>", headers={"content-type": "text/html"}
            )
        )
        router.get(f"{SHOP}/nocur").mock(return_value=html_response("page_no_currency.html"))
        plain = run(_check(f"{SHOP}/plain", settings))
        nocur = run(_check(f"{SHOP}/nocur", settings))
    assert plain.found is False and "no schema.org Product" in plain.message
    assert nocur.found is False and "priceCurrency" in nocur.message
    assert nocur.extracted["name"] == "Producto sin moneda"
    assert nocur.raw is None


def test_check_url_default_client_is_guarded(dns, settings, monkeypatch):
    created = []
    original = netguard.build_guarded_client

    def spy(*args, **kwargs):
        client = original(*args, **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(netguard, "build_guarded_client", spy)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
        page = router.get(f"{SHOP}/p/ps5-slim").mock(return_value=html_response("page_product_offer.html"))
        result = run(jsonld.check_url(f"{SHOP}/p/ps5-slim", settings))
        assert page.calls[0].request.headers["User-Agent"] == settings.user_agent
    assert result.found is True
    assert len(created) == 1 and isinstance(created[0]._transport, netguard.GuardedTransport)
    assert created[0].is_closed
