"""API tokens, rate limiting and security headers.

Tokens
------
A token looks like ``fr_<43 url-safe chars>`` (``secrets.token_urlsafe(32)`` = 256 bits of entropy).  Only
its SHA-256 hex digest and an 8-character display prefix are stored; the plain token is shown once at
creation time.  Verification looks rows up by prefix and compares digests with
:func:`hmac.compare_digest` (constant time).  ``last_used_at`` writes are throttled to one per minute
per token so read-heavy clients do not turn every request into a database write.

Rate limiting
-------------
:class:`RateLimiter` is an in-memory sliding window (per process; not shared between workers).  It is
meant to blunt brute force and runaway clients on a self-hosted single-process server.  Requests that
carry a bearer token are counted per *verified token* (in :func:`flipradar.api.deps.require_token`), so
anonymous traffic can never use up a valid client's budget; everything else - including requests with
an invalid token - is counted per client address.  The address is the TCP peer, or, when the peer is
listed in ``FLIPRADAR_TRUSTED_PROXIES``, the right-most untrusted ``X-Forwarded-For`` hop.

Request guards
--------------
:class:`BodySizeLimitMiddleware` rejects oversized bodies (``Content-Length`` and streamed bytes) before
any route parses them, and refuses large uploads that do not carry a valid token before reading a byte.
:class:`LocalRequestGuardMiddleware` (only with ``FLIPRADAR_REQUIRE_AUTH=false``) accepts only local
``Host`` headers and refuses cross-site state-changing requests, which blocks CSRF and DNS rebinding
against the token-less mode.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import secrets
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ApiToken

TOKEN_PREFIX = "fr_"  # noqa: S105 - public token prefix, not a secret
DISPLAY_PREFIX_LEN = 8
LAST_USED_WRITE_INTERVAL = timedelta(seconds=60)
MAX_TOKEN_LENGTH = 256

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


# ------------------------------------------------------------------------------------------------
# Tokens
# ------------------------------------------------------------------------------------------------


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_display_prefix(token: str) -> str:
    return token[:DISPLAY_PREFIX_LEN]


def create_token(session: Session, name: str) -> tuple[ApiToken, str]:
    """Create and persist (flush) a token.  Returns (row, plain_token); the plain token is not stored."""
    name = (name or "").strip()
    if not name:
        raise ValueError("token name is required")
    if len(name) > 80:
        raise ValueError("token name must be at most 80 characters")
    token = generate_token()
    row = ApiToken(name=name, token_prefix=token_display_prefix(token), token_hash=hash_token(token))
    session.add(row)
    session.flush()
    return row, token


def list_tokens(session: Session, *, include_revoked: bool = True) -> list[ApiToken]:
    stmt = select(ApiToken).order_by(ApiToken.id)
    if not include_revoked:
        stmt = stmt.where(ApiToken.revoked.is_(False))
    return list(session.scalars(stmt))


def revoke_token(session: Session, prefix: str) -> int:
    """Revoke every active token whose display prefix equals ``prefix``.  Returns how many were revoked."""
    prefix = (prefix or "").strip()
    if not prefix:
        return 0
    rows = session.scalars(
        select(ApiToken).where(ApiToken.token_prefix == prefix, ApiToken.revoked.is_(False))
    ).all()
    for row in rows:
        row.revoked = True
    session.flush()
    return len(rows)


def verify_token(session: Session, token: str | None, *, now: datetime | None = None) -> ApiToken | None:
    """Return the active token row matching ``token`` or None.  May update ``last_used_at`` (flushed, not
    committed - the caller decides when to commit)."""
    if not token or len(token) > MAX_TOKEN_LENGTH or not token.startswith(TOKEN_PREFIX):
        return None
    digest = hash_token(token)
    candidates = session.scalars(
        select(ApiToken).where(
            ApiToken.token_prefix == token_display_prefix(token), ApiToken.revoked.is_(False)
        )
    ).all()
    match: ApiToken | None = None
    for row in candidates:
        # Evaluate every candidate (no early exit) and compare in constant time.
        if hmac.compare_digest(row.token_hash.encode("ascii"), digest.encode("ascii")):
            match = row
    if match is None:
        return None
    now = now or datetime.now(UTC)
    last = match.last_used_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if last is None or now - last >= LAST_USED_WRITE_INTERVAL:
        match.last_used_at = now
        session.flush()
    return match


# ------------------------------------------------------------------------------------------------
# Rate limiting
# ------------------------------------------------------------------------------------------------


@dataclass
class RateDecision:
    allowed: bool
    retry_after: int = 0
    remaining: int = 0


class RateLimiter:
    """Sliding-window limiter: at most ``limit`` requests per ``window`` seconds per key."""

    def __init__(self, limit: int, window_seconds: float = 60.0, *, max_keys: int = 50_000,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = int(limit)
        self.window = float(window_seconds)
        self.max_keys = max_keys
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = clock()

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def _sweep(self, now: float) -> None:
        cutoff = now - self.window
        for key in [k for k, q in self._hits.items() if not q or q[-1] <= cutoff]:
            del self._hits[key]
        self._last_sweep = now

    def hit(self, key: str) -> RateDecision:
        if not self.enabled:
            return RateDecision(True, 0, 0)
        now = self._clock()
        cutoff = now - self.window
        with self._lock:
            if now - self._last_sweep > self.window or len(self._hits) > self.max_keys:
                self._sweep(now)
            q = self._hits.get(key)
            if q is None:
                if len(self._hits) >= self.max_keys:
                    # Memory guard under an address-spraying flood: refuse new keys until the sweep.
                    return RateDecision(False, max(1, math.ceil(self.window)), 0)
                q = self._hits[key] = deque()
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.limit:
                retry = max(1, math.ceil(q[0] + self.window - now))
                return RateDecision(False, retry, 0)
            q.append(now)
            return RateDecision(True, 0, self.limit - len(q))

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
# Scope key set by RateLimitMiddleware when the bearer-token check (and its rate limit) is left to
# ``require_token``; the value is the client address used for invalid tokens.
DEFERRED_CLIENT_IP_KEY = "flipradar.rate_limit_client_ip"
TOO_MANY_REQUESTS = "Too many requests; slow down."


def _header(scope: Scope, name: bytes) -> str | None:
    """First value of a request header (names are lower-case in ASGI)."""
    for key, value in scope.get("headers") or ():
        if key == name:
            return value.decode("latin-1")
    return None


def _header_values(scope: Scope, name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in scope.get("headers") or () if key == name]


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = text.strip()
    if text.startswith("[") and "]" in text:
        text = text[1:text.index("]")]
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _trusted(address: ipaddress.IPv4Address | ipaddress.IPv6Address, networks: Sequence[IPNetwork]) -> bool:
    return any(address in net for net in networks)


def client_ip(scope: Scope, trusted_proxies: Sequence[IPNetwork] = ()) -> str:
    """The client's address: the TCP peer, or - when the peer is a trusted proxy - the right-most
    ``X-Forwarded-For`` hop that is not itself a trusted proxy (hops further left are client-supplied)."""
    client = scope.get("client")
    peer = str(client[0]) if client and client[0] else None
    if peer is None:
        return "unknown"
    peer_ip = _ip(peer)
    if not trusted_proxies or peer_ip is None or not _trusted(peer_ip, trusted_proxies):
        return peer
    hops = [h for v in _header_values(scope, b"x-forwarded-for") for h in v.split(",") if h.strip()]
    for hop in reversed(hops):
        address = _ip(hop)
        if address is None:
            return peer  # malformed header: fall back to the proxy's own address
        if not _trusted(address, trusted_proxies):
            return str(address)
    first = _ip(hops[0]) if hops else None
    return str(first) if first is not None else peer


def client_key(scope: Scope, trusted_proxies: Sequence[IPNetwork] = ()) -> str:
    return "ip:" + client_ip(scope, trusted_proxies)


def bearer_credentials(scope: Scope) -> str | None:
    """The token of an ``Authorization: Bearer <token>`` request header, or None."""
    scheme, _, credentials = (_header(scope, b"authorization") or "").partition(" ")
    credentials = credentials.strip()
    return credentials if scheme.lower() == "bearer" and credentials else None


async def _send_json(send: Send, status: int, payload: dict[str, Any],
                     headers: Iterable[tuple[bytes, bytes]] = ()) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii")),
                    *headers],
    })
    await send({"type": "http.response.body", "body": body})


class RateLimitMiddleware:
    """Pure ASGI middleware returning ``429`` + ``Retry-After`` when a client exceeds the limit.

    Requests are counted per client address (see :func:`client_ip`).  ``defer_prefix``: requests under
    that path which carry a bearer token are not counted here - ``require_token`` counts them per verified
    token, or per client address when the token is invalid - so anonymous floods (even through one shared
    reverse-proxy address) cannot lock out clients holding a valid token."""

    def __init__(self, app: ASGIApp, limiter: RateLimiter, *, exempt_prefixes: tuple[str, ...] = ("/static/",),
                 trusted_proxies: Sequence[IPNetwork] = (), defer_prefix: str | None = None,
                 defer_exclude: tuple[str, ...] = ()) -> None:
        self.app = app
        self.limiter = limiter
        self.exempt_prefixes = exempt_prefixes
        self.trusted_proxies = tuple(trusted_proxies)
        self.defer_prefix = defer_prefix
        self.defer_exclude = defer_exclude

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.limiter.enabled:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(path.startswith(p) for p in self.exempt_prefixes):
            await self.app(scope, receive, send)
            return
        ip = client_ip(scope, self.trusted_proxies)
        if (self.defer_prefix and path.startswith(self.defer_prefix) and path not in self.defer_exclude
                and bearer_credentials(scope)):
            scope[DEFERRED_CLIENT_IP_KEY] = ip
            await self.app(scope, receive, send)
            return
        decision = self.limiter.hit("ip:" + ip)
        if decision.allowed:
            await self.app(scope, receive, send)
            return
        await _send_json(send, 429, {"detail": TOO_MANY_REQUESTS},
                         [(b"retry-after", str(decision.retry_after).encode("ascii"))])


# ------------------------------------------------------------------------------------------------
# Request body size
# ------------------------------------------------------------------------------------------------

DEFAULT_MAX_BODY_BYTES = 256 * 1024


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Caps request bodies before anything parses them.  FastAPI reads and parses the whole body (JSON
    into memory, multipart files into temp files) *before* running dependencies such as the token check.

    * ``Content-Length`` above the path's limit: ``413`` without reading the body.
    * Chunked/streamed bodies: bytes are counted as the app reads them; past the limit the read fails
      and the response is replaced by ``413``.
    * Paths whose limit is above ``default_limit`` (the CSV upload): when the body may exceed the default,
      ``authorize(scope)`` must accept the request first, so an anonymous client cannot make the server
      buffer megabytes before being told ``401``.
    """

    def __init__(self, app: ASGIApp, *, default_limit: int = DEFAULT_MAX_BODY_BYTES,
                 path_limits: Mapping[str, int] | None = None,
                 authorize: Callable[[Scope], Awaitable[bool]] | None = None) -> None:
        self.app = app
        self.default_limit = default_limit
        self.path_limits = dict(path_limits or {})
        self.authorize = authorize

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.path_limits.get(scope.get("path", ""), self.default_limit)
        too_large = {"detail": f"Request body too large (limit {limit} bytes)"}
        declared_text = _header(scope, b"content-length")
        declared: int | None = None
        if declared_text is not None:
            if not declared_text.strip().isdigit():
                await _send_json(send, 400, {"detail": "Invalid Content-Length header"})
                return
            declared = int(declared_text)
            if declared > limit:
                await _send_json(send, 413, too_large)
                return
        if (self.authorize is not None and limit > self.default_limit
                and (declared is None or declared > self.default_limit) and not await self.authorize(scope)):
            await _send_json(send, 401, {"detail": "A valid bearer token is required for this upload"},
                             [(b"www-authenticate", b'Bearer realm="flipradar"')])
            return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if exceeded:
                return  # whatever the app answers after a failed read is replaced by the 413 below
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            pass
        if exceeded and not response_started:
            await _send_json(send, 413, too_large)


# ------------------------------------------------------------------------------------------------
# Token-less mode: Host allow-list and cross-site checks
# ------------------------------------------------------------------------------------------------

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _split_host(host_header: str) -> tuple[str, int | None] | None:
    """``"example.com:8000"`` -> ("example.com", 8000); ``"[::1]:80"`` -> ("::1", 80); None if malformed."""
    host_header = host_header.strip().lower()
    if not host_header:
        return None
    if host_header.startswith("["):
        end = host_header.find("]")
        if end < 0:
            return None
        host, rest = host_header[1:end], host_header[end + 1:]
        if rest and not rest.startswith(":"):
            return None
        port_text = rest[1:]
    else:
        host, _, port_text = host_header.partition(":")
    if port_text and not port_text.isdigit():
        return None
    return host.rstrip("."), (int(port_text) if port_text else None)


class LocalRequestGuardMiddleware:
    """Installed only with ``FLIPRADAR_REQUIRE_AUTH=false``.  Any web page the user visits can send
    requests to a loopback port, so without a token the server checks where a request comes from:

    * the ``Host`` header must be a local name (or one listed in ``FLIPRADAR_ALLOWED_HOSTS``), which
      defeats DNS rebinding (``evil.example`` re-pointed at 127.0.0.1);
    * state-changing methods are refused when ``Sec-Fetch-Site: cross-site`` or when an ``Origin`` header
      names another origin that is not a configured CORS origin, which defeats CSRF through forms and
      ``fetch(no-cors)``.  Requests without ``Origin`` (curl, the CLI, the Android app) are allowed.
    """

    def __init__(self, app: ASGIApp, *, allowed_hosts: Iterable[str] = (), allowed_origins: Iterable[str] = ()) -> None:
        self.app = app
        self.allowed_hosts = frozenset(LOCAL_HOSTS) | {h.lower().strip("[]").rstrip(".") for h in allowed_hosts}
        self.allowed_origins = frozenset(o.rstrip("/").lower() for o in allowed_origins)

    def _same_origin(self, origin: str, scheme: str, host: str, port: int | None) -> bool:
        if origin.rstrip("/").lower() in self.allowed_origins:
            return True
        try:
            parts = urlsplit(origin.strip())
            origin_port = parts.port
        except ValueError:
            return False
        origin_scheme = parts.scheme.lower()
        if origin_scheme not in _DEFAULT_PORTS or not parts.hostname:
            return False  # includes "null" (sandboxed frames, file://)
        return (origin_scheme == scheme and parts.hostname.rstrip(".") == host
                and (origin_port or _DEFAULT_PORTS[origin_scheme]) == (port or _DEFAULT_PORTS.get(scheme)))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        parsed = _split_host(_header(scope, b"host") or "")
        if parsed is None or parsed[0] not in self.allowed_hosts:
            await _send_json(send, 400, {"detail": "Invalid host header (see FLIPRADAR_ALLOWED_HOSTS)"})
            return
        if str(scope.get("method", "GET")).upper() not in SAFE_METHODS:
            fetch_site = (_header(scope, b"sec-fetch-site") or "").strip().lower()
            origin = _header(scope, b"origin")
            scheme = str(scope.get("scheme", "http")).lower()
            if fetch_site == "cross-site" or (
                origin is not None and not self._same_origin(origin, scheme, parsed[0], parsed[1])
            ):
                await _send_json(send, 403, {"detail": "Cross-site request refused"})
                return
        await self.app(scope, receive, send)


# ------------------------------------------------------------------------------------------------
# Security headers
# ------------------------------------------------------------------------------------------------

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'"
)
# The interactive API docs page (Swagger UI) loads its assets from a CDN and uses an inline bootstrap
# script, so it gets a dedicated, slightly wider policy.  It shows no user data.
DOCS_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' https: data:; style-src 'self' https://cdn.jsdelivr.net; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; connect-src 'self'; frame-ancestors 'none'"
)

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
)


class SecurityHeadersMiddleware:
    """Adds CSP, nosniff, no-referrer and DENY framing headers to every HTTP response."""

    def __init__(self, app: ASGIApp, *, docs_paths: tuple[str, ...] = ()) -> None:
        self.app = app
        self.docs_paths = docs_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        csp = DOCS_CONTENT_SECURITY_POLICY if scope.get("path") in self.docs_paths else CONTENT_SECURITY_POLICY

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                if b"content-security-policy" not in present:
                    headers.append((b"content-security-policy", csp.encode("ascii")))
                for name, value in SECURITY_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
