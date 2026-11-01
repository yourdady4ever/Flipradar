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
:class:`RateLimiter` is an in-memory sliding window (per client IP, per process).  It is meant to blunt
brute force and runaway clients on a self-hosted single-process server; it is not shared between
workers.  The client address is taken from the ASGI connection (``X-Forwarded-For`` is NOT trusted; put
the limiter of your reverse proxy in front if you run one).
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

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


def client_key(scope: Scope) -> str:
    client = scope.get("client")
    if client and client[0]:
        return str(client[0])
    return "unknown"


class RateLimitMiddleware:
    """Pure ASGI middleware returning ``429`` + ``Retry-After`` when a client exceeds the limit."""

    def __init__(self, app: ASGIApp, limiter: RateLimiter, *, exempt_prefixes: tuple[str, ...] = ("/static/",)) -> None:
        self.app = app
        self.limiter = limiter
        self.exempt_prefixes = exempt_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.limiter.enabled:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(path.startswith(p) for p in self.exempt_prefixes):
            await self.app(scope, receive, send)
            return
        decision = self.limiter.hit(client_key(scope))
        if decision.allowed:
            await self.app(scope, receive, send)
            return
        body = b'{"detail":"Too many requests; slow down."}'
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"retry-after", str(decision.retry_after).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


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
