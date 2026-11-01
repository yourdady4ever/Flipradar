"""Regression tests for the second-round review findings in the security area (r2_security).

One section per finding id.  No network: TestClient / raw ASGI calls, respx mocks and a fake DNS.
"""

from __future__ import annotations

import asyncio
import logging
import socket

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from flipradar.ai import narrative
from flipradar.collectors import jsonld, netguard, rss
from flipradar.collectors.base import CollectorError
from flipradar.config import Settings
from tests.conftest import make_settings

SHOP = "https://shop.example.com"
PUBLIC_IP = "93.184.215.14"
FEED = "https://a.example/feed"
ORIGIN = "https://dash.example.com"
UNKNOWN_TOKEN = "fr_" + "A" * 43  # well-formed, but no such token


def run(coro):
    return asyncio.run(coro)


def _app(tmp_path, **overrides):
    from flipradar.main import create_app

    return create_app(make_settings(tmp_path, **overrides))


def _new_token() -> str:
    from flipradar.db import session_scope
    from flipradar.security import create_token

    with session_scope() as session:
        return create_token(session, "owner")[1]


def _asgi(app, method: str, path: str, headers: dict[str, str], chunks: tuple[bytes, ...] = (b"",),
          client: tuple[str, int] = ("203.0.113.7", 1)):
    """Drive the ASGI app with exactly these headers (TestClient would add its own).  Returns
    (status, response headers, body, request bytes the app consumed)."""
    consumed = 0
    messages = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]
    sent: list[dict] = []

    async def receive():
        nonlocal consumed
        if messages:
            message = messages.pop(0)
            consumed += len(message["body"])
            return message
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": client, "server": ("127.0.0.1", 8000),
    }
    asyncio.run(asyncio.wait_for(app(scope, receive, send), 10))
    start = next(m for m in sent if m["type"] == "http.response.start")
    response_headers = {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], response_headers, body, consumed


# ------------------------------------------------------------------------------------------------
# security#0 - a fake "Authorization: Bearer" header must not switch off the per-IP rate limit
# ------------------------------------------------------------------------------------------------

MALFORMED_JSON = b'{"a":' + b" " * 200_000 + b"["


@pytest.mark.parametrize("bearer", ["junk", UNKNOWN_TOKEN])
@pytest.mark.parametrize(
    ("method", "path", "body", "expected"),
    [
        ("POST", "/api/v1/calculator/profit", MALFORMED_JSON, 422),  # body parsed before require_token runs
        ("GET", "/api/v1/does-not-exist", None, 404),  # no route: require_token never runs
        ("DELETE", "/api/v1/calculator/profit", None, 405),
    ],
)
def test_fake_bearer_header_does_not_bypass_the_ip_limit(tmp_path, bearer, method, path, body, expected):
    app = _app(tmp_path, rate_limit_per_minute=5)
    headers = {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}
    with TestClient(app, client=("203.0.113.7", 1)) as c:
        codes = [c.request(method, path, content=body, headers=headers).status_code for _ in range(8)]
    assert codes == [expected] * 5 + [429] * 3


def test_invalid_tokens_are_charged_to_the_ip_before_the_body_is_read(tmp_path):
    app = _app(tmp_path, rate_limit_per_minute=2)
    with TestClient(app):
        headers = {"authorization": f"Bearer {UNKNOWN_TOKEN}", "content-type": "application/json",
                   "content-length": str(len(MALFORMED_JSON))}
        results = [_asgi(app, "POST", "/api/v1/calculator/profit", headers, (MALFORMED_JSON,)) for _ in range(3)]
    assert [status for status, *_ in results] == [422, 422, 429]
    assert results[-1][3] == 0  # refused without reading a byte of the body


def test_valid_token_keeps_its_own_bucket_for_requests_that_fail_before_auth(tmp_path):
    app = _app(tmp_path, rate_limit_per_minute=5)
    with TestClient(app, client=("203.0.113.7", 1)) as c:
        owner = {"Authorization": f"Bearer {_new_token()}", "Content-Type": "application/json"}
        junk = {"Authorization": "Bearer junk", "Content-Type": "application/json"}
        flood = [c.post("/api/v1/calculator/profit", content=b"{", headers=junk).status_code for _ in range(7)]
        assert flood.count(429) == 2
        # The address is over its limit, but a valid token is limited per token (anonymous and invalid
        # requests from a shared proxy address must not lock its owner out).
        assert c.get("/api/v1/auth/check", headers=owner).status_code == 200
        own = [c.post("/api/v1/calculator/profit", content=b"{", headers=owner).status_code for _ in range(5)]
        assert own == [422, 422, 422, 422, 429]  # malformed bodies count against the token's own budget
        assert c.get("/api/v1/does-not-exist", headers=owner).status_code == 429


def test_large_upload_with_invalid_token_is_counted_once(tmp_path):
    app = _app(tmp_path, rate_limit_per_minute=3)
    with TestClient(app):
        headers = {"authorization": f"Bearer {UNKNOWN_TOKEN}", "content-type": "multipart/form-data; boundary=x",
                   "content-length": str(5 * 1024 * 1024)}
        statuses = [_asgi(app, "POST", "/api/v1/import/csv", headers, (b"-" * 65536,))[0] for _ in range(4)]
    assert statuses == [401, 401, 401, 429]


# ------------------------------------------------------------------------------------------------
# contracts#3 - token checks for an address that is over its limit.  By design a VALID token is still
# accepted then (its own bucket; otherwise anyone behind a shared proxy address could lock its owner out),
# so the remaining cost of a flood of made-up tokens is kept small instead.
# ------------------------------------------------------------------------------------------------


def test_tokens_of_an_impossible_shape_are_refused_without_a_database_query():
    from flipradar.security import verify_token

    for token in ("junk", "fr_short", "fr_" + "A" * 42, "fr_" + "A" * 44, "fr_" + "A" * 42 + "!", "x" + "A" * 45):
        assert verify_token(None, token) is None, token  # a None session would fail if it were queried


def test_over_limit_address_gets_429_for_made_up_tokens_before_any_route_runs(tmp_path):
    app = _app(tmp_path, rate_limit_per_minute=3)
    with TestClient(app, client=("203.0.113.9", 1)) as c:
        owner = {"Authorization": f"Bearer {_new_token()}"}
        codes = [c.get("/api/v1/auth/check", headers={"Authorization": f"Bearer fr_{i:043d}"}).status_code
                 for i in range(6)]
        assert codes == [401, 401, 401, 429, 429, 429]
        assert c.get("/api/v1/auth/check", headers=owner).status_code == 200  # intended: per-token bucket


# ------------------------------------------------------------------------------------------------
# security#1 - CORS preflight must never need a token or be blocked by the body/rate guards
# ------------------------------------------------------------------------------------------------

PREFLIGHT = {"origin": ORIGIN, "access-control-request-method": "POST",
             "access-control-request-headers": "authorization"}


@pytest.mark.parametrize("path", ["/api/v1/import/csv", "/api/v1/calculator/profit"])
def test_cors_preflight_is_answered_for_every_api_path(tmp_path, path):
    app = _app(tmp_path, cors_origins=ORIGIN)
    with TestClient(app):
        status, headers, body, _ = _asgi(app, "OPTIONS", path, PREFLIGHT)
    assert status == 200, body
    assert headers["access-control-allow-origin"] == ORIGIN
    assert "authorization" in headers["access-control-allow-headers"].lower()


def test_bodyless_requests_to_the_upload_path_do_not_need_upload_preauthorization(tmp_path):
    app = _app(tmp_path)
    with TestClient(app):
        for method in ("OPTIONS", "GET", "HEAD"):
            status, _, body, _ = _asgi(app, method, "/api/v1/import/csv", {})
            assert b"required for this upload" not in body, method
            assert status in (401, 405), (method, status)
        # A body-less method still cannot send a large body there.
        status, *_ = _asgi(app, "GET", "/api/v1/import/csv", {"content-length": str(1024 * 1024)})
        assert status == 413


def test_preflight_is_not_rate_limited_and_rejections_carry_cors_headers(tmp_path):
    app = _app(tmp_path, cors_origins=ORIGIN, rate_limit_per_minute=2)
    with TestClient(app):
        hdr = {"origin": ORIGIN}
        for _ in range(3):
            _asgi(app, "GET", "/health", hdr)
        status, headers, _, _ = _asgi(app, "GET", "/api/v1/sources", hdr)
        assert status == 429 and headers["access-control-allow-origin"] == ORIGIN  # readable by the dashboard
        status, headers, _, _ = _asgi(app, "OPTIONS", "/api/v1/import/csv", PREFLIGHT)
        assert status == 200 and headers["access-control-allow-origin"] == ORIGIN
    app = _app(tmp_path, cors_origins=ORIGIN)
    with TestClient(app):
        upload = {"origin": ORIGIN, "content-type": "multipart/form-data; boundary=x",
                  "content-length": str(5 * 1024 * 1024)}
        status, headers, _, consumed = _asgi(app, "POST", "/api/v1/import/csv", upload, (b"-" * 65536,))
        assert status == 401 and consumed == 0 and headers["access-control-allow-origin"] == ORIGIN
        too_big = {"origin": ORIGIN, "content-type": "application/json", "content-length": str(400 * 1024)}
        status, headers, _, _ = _asgi(app, "POST", "/api/v1/collect", too_big, (b" ",))
        assert status == 413 and headers["access-control-allow-origin"] == ORIGIN


# ------------------------------------------------------------------------------------------------
# security#2 - a short price followed by a unit-suffixed spec number is not a thousands group
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("Cable USB-C $99 100W", 9900),
        ("Audífonos $499 100% originales", 49900),
        ("Router TP-Link $499 300Mbps", 49900),
        ("Memoria microSD Samsung $199 256GB", 19900),
        ("Monitor $999 144Hz", 99900),
        ("Memoria USB $999 128GB", 99900),
        ("Memoria microSD Samsung $199 256 GB", 19900),
        ("Power bank $499 10000mAh", 49900),
        # a currency right after the group still makes it one space-grouped amount
        ("Pantalla $12 999MXN", 1299900),
        ("Pantalla $12 999pesos", 1299900),
        ("Pantalla $12 999 MXN", 1299900),
        ("Pantalla $12 999 M.N.", 1299900),
    ],
)
def test_price_followed_by_a_spec_with_a_unit_is_kept(text, cents):
    assert rss.extract_price(text).cents == cents


@pytest.mark.parametrize(
    "text",
    [
        "Pantalla $12 999",  # 12 or 12,999?
        "Pantalla $12 999.00",
        "Pantalla $12 999,",
        "Pantalla $12 999 en oferta",
        "Teclado mecánico $799 104 teclas",  # a bare number: same shape as "$12 999 en oferta"
        "Pantalla $12 999 Walmart",
        "Pantalla $12 999Envío gratis",  # words run together in a feed are not a unit
        "Pantalla $12 999W0w",
    ],
)
def test_price_followed_by_a_bare_digit_group_stays_ambiguous(text):
    extraction = rss.extract_price(text)
    assert extraction.cents is None and "ambiguous" in extraction.reason


def test_unit_suffixed_title_no_longer_blocks_the_description():
    assert rss.extract_price("Memoria microSD Samsung $199 256GB", "Precio: $199 en Amazon").cents == 19900


# ------------------------------------------------------------------------------------------------
# security#3 - the brand MSI is not "meses sin intereses"
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("Laptop Gamer MSI por $15,999", 1599900),
        ("Monitor MSI de $3,499 en Amazon", 349900),
        ("Tarjeta de video MSI x $8,999", 899900),
        ("Laptop MSI x $15,999 - Amazon", 1599900),
        ("Laptop MSI $15,999 o 12 MSI de $1,333", 1599900),  # the installment is still dropped
    ],
)
def test_msi_brand_before_the_price_is_not_an_installment(text, cents):
    assert rss.extract_price(text).cents == cents


@pytest.mark.parametrize(
    "text",
    [
        "PC Gamer MSI por $25,999 + audifonos de regalo valor $499",  # two prices: never pick one
        "Laptop MSI de $18,999 incluye mochila $799",
        "Monitor MSI de $3,499 (antes $4,999)",
    ],
)
def test_msi_brand_price_is_not_dropped_in_favour_of_another_amount(text):
    extraction = rss.extract_price(text)
    assert extraction.cents is None and "different prices" in extraction.reason


@pytest.mark.parametrize(
    "text",
    [
        "Laptop MSI a 12 MSI de $1,249",
        "iPhone 15 a 12MSI de $1,249",
        "iPhone 15 en 18 meses de $899",
        "iPhone 15 en meses sin intereses de $1,249",
        "PS5 en mensualidades de $599",
    ],
)
def test_counted_installments_are_still_ignored(text):
    assert rss.extract_price(text).cents is None


def test_msi_brand_title_does_not_fall_through_to_the_description():
    extraction = rss.extract_price("Laptop Gamer MSI por $15,999", "Precio anterior: $19,999")
    assert extraction.cents == 1599900 and extraction.reason == "single price in title"


# ------------------------------------------------------------------------------------------------
# security#4 - harmless Content-Encoding values are identity, real unsupported compressions fail
# ------------------------------------------------------------------------------------------------


class _FakeDNS:
    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        if host != "shop.example.com":
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]


@pytest.fixture
def dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _FakeDNS())


def _streamed(body: bytes, encoding: str | None, content_type: str = "text/plain"):
    """respx side effect returning a streamed response (the production read path)."""

    def respond(request):
        async def chunks():
            for i in range(0, len(body), 65536):
                yield body[i : i + 65536]

        headers = {"content-type": content_type}
        if encoding is not None:
            headers["content-encoding"] = encoding
        return httpx.Response(200, content=chunks(), headers=headers)

    return respond


async def _guarded(path: str, max_bytes: int = 4096):
    async with httpx.AsyncClient() as client:
        return await netguard.guarded_get(client, f"{SHOP}{path}", max_bytes=max_bytes)


@pytest.mark.parametrize("encoding", ["none", "None", "UTF-8", "binary", "", "identity", "identity, none"])
def test_harmless_content_encodings_are_read_as_identity(dns, encoding):
    body = b"User-agent: *\nDisallow: /private\n" * 20
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/robots.txt").mock(side_effect=_streamed(body, encoding))
        assert run(_guarded("/robots.txt")).content == body


def test_identity_like_encoding_is_still_capped_on_wire_bytes(dns):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/big").mock(side_effect=_streamed(b"a" * 10_000, "none"))
        with pytest.raises(netguard.ResponseTooLarge):
            run(_guarded("/big", 4096))


@pytest.mark.parametrize("encoding", ["br", "zstd", "compress", "x-compress", "gzip, gzip", "gzip, none, deflate"])
def test_unsupported_or_stacked_compression_is_still_refused(dns, encoding):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SHOP}/x").mock(side_effect=_streamed(b"\x1b\x00\x00", encoding))
        with pytest.raises(CollectorError, match="content encoding"):
            run(_guarded("/x"))


def test_rss_feed_with_content_encoding_none_parses():
    settings = Settings(_env_file=None, rss_feeds=FEED)
    feed = (b'<rss version="2.0"><channel><title>t</title><item><title>PS5 Slim $7,999</title>'
            b"<guid>g1</guid><link>https://x.example/1</link></item></channel></rss>")
    with respx.mock() as router:
        router.get(FEED).mock(side_effect=_streamed(feed, "none", "application/rss+xml"))
        collector = rss.RssDealsCollector(settings)
        listings = run(collector.fetch_latest())
    assert [listing.price_cents for listing in listings] == [799900]
    assert not collector.last_errors


# ------------------------------------------------------------------------------------------------
# security#5 - the AI validator must not reject accurate summaries (but stays strict on numbers)
# ------------------------------------------------------------------------------------------------

INFO_FACTS = {
    "risk_level": "info",
    "risk_flags": [{"code": "shipping_unknown", "severity": "info", "message": "Shipping cost unknown."}],
    "buy_price_cents": 1_000_000,
    "expected_sale_cents": 1_299_900,
    "net_profit_cents": 150_000,
    "net_profit": "$1,500.00",
    "roi_bp": 1500,
    "roi_pct": "15.00%",
    "break_even_sale_cents": 1_182_471,
    "max_buy_price_cents": 1_179_829,
}


@pytest.mark.parametrize(
    "summary",
    [
        "No hay riesgos críticos; solo falta el costo de envío. Ganancia neta $1,500.00 (ROI 15.00%).",
        "Sin riesgos graves: solo un aviso informativo. Ganancia neta de $1,500.00.",
        "No presenta riesgos importantes y deja $1,500.00 de ganancia.",
        "Comprar a $10,000.00 y vender en $12,999.00 deja $1,500.00 de ganancia; para no perder dinero no "
        "vendas por menos de $10,000.00.",
        "Para no perder dinero, el precio de venta mínimo es $11,824.71.",
        "Ganancia neta de $1,500.00 sin pérdidas; ROI de 15.00%.",
        "Comprar a $10,000.00; para evitar pérdidas no pagues más de $11,798.29.",
        "Existe riesgo de pérdida si el envío es caro. El ROI es de 15.00%.",
    ],
)
def test_accurate_summaries_are_accepted(summary):
    assert narrative.validate_summary(summary, INFO_FACTS) == (True, [])


def test_validator_stays_strict_on_risk_denials():
    assert narrative.validate_summary("No hay riesgos.", INFO_FACTS)[1] == ["no hay riesgos"]
    assert narrative.validate_summary("Sin riesgos de ningún tipo.", INFO_FACTS)[0] is False
    warning = {**INFO_FACTS, "risk_level": "warning"}
    assert narrative.validate_summary("No hay riesgos críticos.", warning)[0]  # only warnings: accurate
    assert not narrative.validate_summary("No hay riesgos graves.", warning)[0]
    critical = {**INFO_FACTS, "risk_level": "critical"}
    for summary in ("No hay riesgos críticos.", "Sin riesgos graves.", "Ningún riesgo importante."):
        assert not narrative.validate_summary(summary, critical)[0], summary


def test_validator_stays_strict_on_numbers_and_signs():
    assert narrative.validate_summary("Ganancia neta de $1,600.00.", INFO_FACTS)[1] == ["1,600.00"]
    assert not narrative.validate_summary("Pérdida de $1,500.00.", INFO_FACTS)[0]
    assert not narrative.validate_summary("Tendrás una pérdida neta de $1,500.00.", INFO_FACTS)[0]
    assert not narrative.validate_summary("No vendas: la pérdida es de $1,500.00.", INFO_FACTS)[0]
    loss = {"net_profit_cents": -150_000, "roi_bp": -1500, "risk_level": "none"}
    assert narrative.validate_summary("Una pérdida neta de $1,500.00 y un ROI negativo de 15.00%.", loss)[0]
    assert not narrative.validate_summary("Sin pérdidas: deja $1,500.00 (ROI de 15.00%).", loss)[0]


# ------------------------------------------------------------------------------------------------
# security#6 - credentials in a refused check-url URL never reach the log
# ------------------------------------------------------------------------------------------------


def test_check_url_refusal_log_has_no_credentials(api, caplog):
    caplog.set_level(logging.DEBUG)
    r = api.post("/api/v1/check-url", json={"url": "https://admin:S3cret@shop.example/p/1", "save": False})
    assert r.status_code == 400
    assert "refused" in caplog.text and "credentials" in caplog.text  # the reason is still logged
    assert "S3cret" not in caplog.text and "admin" not in caplog.text
    assert "shop.example/p/1" in caplog.text


def test_redact_userinfo_handles_odd_urls():
    from flipradar.api.routers.collect import redact_userinfo

    assert redact_userinfo("https://a:b@c@host.example/x?y=1") == "https://host.example/x?y=1"
    assert redact_userinfo("http://user@host.example") == "http://host.example"
    assert redact_userinfo("redirect to https://u:p@h.example/ refused") == "redirect to https://h.example/ refused"
    assert redact_userinfo("https://host.example/a@b") == "https://host.example/a@b"  # '@' in the path


# ------------------------------------------------------------------------------------------------
# contracts#4 - "too many redirects" is not reported as a non-public address
# ------------------------------------------------------------------------------------------------


def _redirect_chain(router, hops: int) -> None:
    router.get(f"{SHOP}/robots.txt").mock(return_value=httpx.Response(404))
    for i in range(hops):
        router.get(f"{SHOP}/r{i}").mock(return_value=httpx.Response(302, headers={"location": f"/r{i + 1}"}))
    router.get(f"{SHOP}/r{hops}").mock(return_value=httpx.Response(200, text="<html></html>",
                                                                  headers={"content-type": "text/html"}))


async def _check(url: str):
    async with httpx.AsyncClient() as client:
        return await jsonld.check_url(url, Settings(_env_file=None), client)


def test_redirect_limit_is_reported_as_such_by_check_url(dns):
    with respx.mock(assert_all_called=False) as router:
        _redirect_chain(router, 5)
        result = run(_check(f"{SHOP}/r0"))
    assert result.found is False and result.allowed_by_robots is True
    assert "too many redirects" in result.message and "non-public" not in result.message


def test_redirect_limit_on_robots_txt_is_a_robots_failure(dns):
    with respx.mock(assert_all_called=False) as router:
        for i in range(5):
            target = "/robots.txt" if i == 0 else f"/rr{i}"
            router.get(f"{SHOP}{target}").mock(
                return_value=httpx.Response(302, headers={"location": f"/rr{i + 1}"}))
        page = router.get(f"{SHOP}/p/1").mock(return_value=httpx.Response(200, text="x"))
        result = run(_check(f"{SHOP}/p/1"))
        assert page.call_count == 0
    assert result.found is False and result.allowed_by_robots is False
    assert "too many redirects" in result.message


def test_check_url_api_distinguishes_redirect_limit_from_unsafe_address(api, monkeypatch):
    async def too_many(url, settings, client=None):
        raise netguard.TooManyRedirects("too many redirects (limit 3)")

    monkeypatch.setattr(jsonld, "check_url", too_many)
    r = api.post("/api/v1/check-url", json={"url": "https://bit.example/x", "save": False})
    assert r.status_code == 200, r.text
    assert "too many redirects" in r.json()["message"] and "non-public" not in r.json()["message"]
    # the redirect-limit error is still an UnsafeUrlError for callers that only check that
    assert issubclass(netguard.TooManyRedirects, netguard.UnsafeUrlError)
