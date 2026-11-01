"""Network safety (SSRF guard) and small shared HTTP helpers.  OWNER: collectors agent.

Used for every request whose URL comes from a *user* (the single-URL JSON-LD check):

* only ``http``/``https``; no ``user:pass@`` credentials; port 80/443 unless explicitly allowed;
* the hostname is resolved and EVERY address must be public (no private, loopback, link-local,
  multicast, reserved, unspecified, CGNAT, site-local, or IPv4-embedded-in-IPv6 private addresses);
* redirects are followed manually (max 3) and each hop is re-validated;
* bodies are streamed and capped (2 MB by default) and the content type can be restricted;
* :class:`GuardedTransport` repeats the address check at *connect time* and connects to the vetted IP,
  which defeats DNS-rebinding between the pre-check and the real connection.

Operator-configured URLs (API bases, RSS feeds) are trusted configuration and only use the
size-capping / Retry-After helpers.
"""

from __future__ import annotations

import asyncio
import contextlib
import email.utils
import ipaddress
import logging
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpcore
import httpx

from ..config import Settings
from .base import CollectorError

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
DEFAULT_ALLOWED_PORTS = frozenset({80, 443})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_RETRY_AFTER_SECONDS = 60.0

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_NAT64_NETWORKS = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")


class UnsafeUrlError(CollectorError):
    """The URL (or a redirect target) is not allowed: bad scheme/port/credentials or non-public address."""


class ResponseTooLarge(CollectorError):
    pass


class UnsupportedContentType(CollectorError):
    pass


# ------------------------------------------------------------------------------------------------
# small helpers shared by all collectors
# ------------------------------------------------------------------------------------------------


async def sleep(seconds: float) -> None:
    """Indirection so tests can skip real waiting (monkeypatch ``netguard.sleep``)."""
    await asyncio.sleep(seconds)


def parse_retry_after(
    value: str | None, *, default: float = 2.0, maximum: float = MAX_RETRY_AFTER_SECONDS
) -> float:
    """Seconds to wait from a ``Retry-After`` header (delta-seconds or HTTP-date), clamped to [0, maximum]."""
    seconds = default
    if value:
        text = value.strip()
        if text.isdigit():
            seconds = float(text)
        else:
            try:
                when = email.utils.parsedate_to_datetime(text)
            except (TypeError, ValueError, IndexError):
                when = None
            if when is not None:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                seconds = (when - datetime.now(UTC)).total_seconds()
    return max(0.0, min(seconds, maximum))


@contextlib.asynccontextmanager
async def http_client(
    settings: Settings, client: httpx.AsyncClient | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield ``client`` or a short-lived JSON API client (closed afterwards)."""
    if client is not None:
        yield client
        return
    own = httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
        follow_redirects=True,
    )
    try:
        yield own
    finally:
        await own.aclose()


async def read_capped(response: httpx.Response, max_bytes: int = MAX_BODY_BYTES) -> bytes:
    """Read a *streamed* response body, aborting as soon as it exceeds ``max_bytes`` (decoded size)."""
    declared = response.headers.get("content-length")
    if declared and declared.strip().isdigit() and int(declared) > max_bytes:
        raise ResponseTooLarge(f"response body is {int(declared)} bytes; the limit is {max_bytes} bytes")
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise ResponseTooLarge(f"response body exceeds the {max_bytes} byte limit")
    return bytes(buffer)


def media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


# ------------------------------------------------------------------------------------------------
# address / URL validation
# ------------------------------------------------------------------------------------------------


def _translated_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """IPv4 address actually reached through an IPv4-mapped (::ffff:a.b.c.d) or NAT64 (64:ff9b::/96,
    64:ff9b:1::/48) IPv6 address.  Such addresses are judged ONLY by that IPv4 address (Python flags the
    whole of 64:ff9b::/96 as reserved, which would break every site on DNS64/NAT64 networks)."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if any(ip in net for net in _NAT64_NETWORKS):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """IPv4 addresses embedded by tunnelling schemes (6to4, Teredo, deprecated IPv4-compatible)."""
    found: list[ipaddress.IPv4Address] = []
    if ip.sixtofour is not None:
        found.append(ip.sixtofour)
    if ip.teredo is not None:
        found.extend(ip.teredo)
    if ip in _IPV4_COMPATIBLE:
        found.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return found


def is_public_ip(address: str | IPAddress) -> bool:
    """True only for globally routable unicast addresses."""
    try:
        ip = (
            address
            if not isinstance(address, str)
            else ipaddress.ip_address(address.split("%", 1)[0].strip("[]"))
        )
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        translated = _translated_ipv4(ip)
        if translated is not None:
            return is_public_ip(translated)
        if ip.is_site_local:
            return False
        if any(not is_public_ip(v4) for v4 in _embedded_ipv4(ip)):
            return False
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    ):
        return False
    return True


def _host_of(url: httpx.URL) -> str:
    raw = url.raw_host.decode("ascii", "ignore") if url.raw_host else ""
    return raw.strip("[]").rstrip(".").lower()


def _port_of(url: httpx.URL) -> int:
    if url.port is not None:
        return url.port
    return 443 if url.scheme == "https" else 80


def parse_and_validate_url(url: str | httpx.URL, *, allowed_ports: Iterable[int] | None = None) -> httpx.URL:
    """Syntax-level checks (no DNS).  Raises :class:`UnsafeUrlError`."""
    try:
        parsed = url if isinstance(url, httpx.URL) else httpx.URL(str(url).strip())
    except (httpx.InvalidURL, TypeError, ValueError) as exc:
        raise UnsafeUrlError(f"invalid URL: {exc}") from exc
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError("only http:// and https:// URLs are allowed")
    if parsed.userinfo:
        raise UnsafeUrlError("URLs containing credentials (user:password@) are not allowed")
    host = _host_of(parsed)
    if not host:
        raise UnsafeUrlError("URL has no host name")
    ports = set(DEFAULT_ALLOWED_PORTS) | set(allowed_ports or ())
    port = _port_of(parsed)
    if port not in ports:
        raise UnsafeUrlError(
            f"port {port} is not allowed (allowed: {', '.join(str(p) for p in sorted(ports))})"
        )
    return parsed


async def resolve_host(host: str, port: int) -> list[str]:
    """Resolve ``host`` in a worker thread; returns de-duplicated address strings."""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise CollectorError(f"could not resolve host '{host}': {exc}") from exc
    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4]
        address = str(sockaddr[0]).split("%", 1)[0]
        if address not in addresses:
            addresses.append(address)
    return addresses


async def ensure_public_host(host: str, port: int) -> list[str]:
    """Return the vetted addresses of ``host``; raise :class:`UnsafeUrlError` if ANY is non-public."""
    host = host.strip("[]").rstrip(".").lower()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not is_public_ip(literal):
            raise UnsafeUrlError(f"address {literal} is not a public internet address; refusing to connect")
        return [str(literal)]
    addresses = await resolve_host(host, port)
    if not addresses:
        raise CollectorError(f"host '{host}' did not resolve to any address")
    blocked = [a for a in addresses if not is_public_ip(a)]
    if blocked:
        raise UnsafeUrlError(
            f"host '{host}' resolves to a non-public address ({blocked[0]}); refusing to connect"
        )
    return addresses


HostPolicy = Callable[[str], None]


async def validate_url(
    url: str | httpx.URL,
    *,
    allowed_ports: Iterable[int] | None = None,
    host_policy: HostPolicy | None = None,
) -> httpx.URL:
    """Full validation: syntax, optional host policy (may raise), DNS resolution to public addresses."""
    parsed = parse_and_validate_url(url, allowed_ports=allowed_ports)
    host = _host_of(parsed)
    if host_policy is not None:
        host_policy(host)
    await ensure_public_host(host, _port_of(parsed))
    return parsed


# ------------------------------------------------------------------------------------------------
# connect-time guard (DNS-rebinding protection)
# ------------------------------------------------------------------------------------------------


class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    """httpcore network backend that resolves + vets the host itself and connects to the vetted IP.

    TLS still uses the original host name for SNI and certificate verification (httpcore passes the
    origin host to ``start_tls``), so pinning the IP does not weaken HTTPS."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[object] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await ensure_public_host(host, port)
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,  # type: ignore[arg-type]
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[object] | None = None
    ) -> httpcore.AsyncNetworkStream:
        raise UnsafeUrlError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    """``httpx`` transport whose connections go through :class:`GuardedNetworkBackend` (no proxies)."""

    def __init__(
        self, *, verify: bool = True, network_backend: httpcore.AsyncNetworkBackend | None = None
    ) -> None:
        super().__init__(verify=verify, retries=0)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=verify),
            max_connections=10,
            max_keepalive_connections=5,
            keepalive_expiry=5.0,
            http1=True,
            http2=False,
            retries=0,
            network_backend=network_backend or GuardedNetworkBackend(),
        )


def build_guarded_client(
    settings: Settings, *, network_backend: httpcore.AsyncNetworkBackend | None = None
) -> httpx.AsyncClient:
    """Client for user-supplied URLs.  Environment proxies are ignored on purpose (they would bypass the
    connect-time address check)."""
    return httpx.AsyncClient(
        transport=GuardedTransport(network_backend=network_backend),
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": settings.user_agent},
        follow_redirects=False,
        trust_env=False,
    )


# ------------------------------------------------------------------------------------------------
# guarded GET with manual redirects
# ------------------------------------------------------------------------------------------------


@dataclass
class FetchedResponse:
    url: str  # final URL (after redirects)
    status_code: int
    headers: httpx.Headers
    content: bytes
    redirects: list[str] = field(default_factory=list)

    @property
    def content_type(self) -> str:
        return media_type(self.headers.get("content-type"))

    @property
    def charset(self) -> str | None:
        for part in (self.headers.get("content-type") or "").split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.strip().lower() == "charset" and value.strip():
                return value.strip().strip('"').strip("'")
        return None

    def text(self) -> str:
        encoding = self.charset or "utf-8"
        try:
            return self.content.decode(encoding, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


RedirectHook = Callable[[httpx.URL], Awaitable[None]]


async def guarded_get(
    client: httpx.AsyncClient,
    url: str | httpx.URL,
    *,
    headers: dict[str, str] | None = None,
    max_bytes: int = MAX_BODY_BYTES,
    max_redirects: int = MAX_REDIRECTS,
    allowed_ports: Iterable[int] | None = None,
    host_policy: HostPolicy | None = None,
    allowed_content_types: Iterable[str] | None = None,
    before_redirect: RedirectHook | None = None,
) -> FetchedResponse:
    """GET a user-supplied URL safely.  Every hop is validated; redirects are followed manually.

    ``allowed_content_types`` is enforced only for 2xx responses.  ``before_redirect`` is awaited with
    each validated redirect target (e.g. to re-check robots.txt) and may raise to stop."""
    ports = tuple(allowed_ports or ())
    current = await validate_url(url, allowed_ports=ports, host_policy=host_policy)
    allowed_types = {t.lower() for t in allowed_content_types} if allowed_content_types is not None else None
    redirects: list[str] = []
    while True:
        next_url: httpx.URL | None = None
        async with client.stream("GET", current, headers=headers, follow_redirects=False) as response:
            if response.status_code in REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise CollectorError(f"HTTP {response.status_code} redirect without a Location header")
                next_url = response.url.join(location.strip())
            else:
                if allowed_types is not None and 200 <= response.status_code < 300:
                    ctype = media_type(response.headers.get("content-type"))
                    if ctype not in allowed_types:
                        raise UnsupportedContentType(
                            f"content type '{ctype or 'missing'}' is not supported (expected {', '.join(sorted(allowed_types))})"
                        )
                body = await read_capped(response, max_bytes)
                return FetchedResponse(
                    url=str(response.url),
                    status_code=response.status_code,
                    headers=response.headers,
                    content=body,
                    redirects=redirects,
                )
        if len(redirects) >= max_redirects:
            raise UnsafeUrlError(f"too many redirects (limit {max_redirects})")
        current = await validate_url(next_url, allowed_ports=ports, host_policy=host_policy)
        redirects.append(str(current))
        if before_redirect is not None:
            await before_redirect(current)
