"""FastAPI dependencies: database session, settings, bearer-token authentication and per-token rate
limiting (see :class:`flipradar.security.RateLimitMiddleware`)."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import db as db_module
from ..config import Settings
from ..config import get_settings as _env_settings
from ..models import ApiToken
from ..security import DEFERRED_CLIENT_IP_KEY, TOO_MANY_REQUESTS, verify_token

MAX_PAGE_SIZE = 200


def get_db() -> Iterator[Session]:
    """Request-scoped session (delegates to :func:`flipradar.db.get_db`)."""
    yield from db_module.get_db()


def get_settings(request: Request) -> Settings:
    """The Settings instance the app was created with (falls back to the environment)."""
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else _env_settings()


def _unauthorized(detail: str, error: str | None = None) -> HTTPException:
    challenge = 'Bearer realm="flipradar"'
    if error:
        challenge += f', error="{error}"'
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": challenge})


def _rate_limit(request: Request, token: ApiToken | None) -> None:
    """Count a request whose rate limiting the middleware deferred (it carries a bearer token): per
    verified token, or per client address when the token is invalid.  Raises 429 over the limit."""
    client_ip = request.scope.get(DEFERRED_CLIENT_IP_KEY)
    limiter = getattr(request.app.state, "rate_limiter", None)
    if client_ip is None or limiter is None:
        return  # already counted by the middleware (no bearer token) or limiting is not installed
    decision = limiter.hit(f"token:{token.id}" if token is not None else f"ip:{client_ip}")
    if not decision.allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=TOO_MANY_REQUESTS,
                            headers={"Retry-After": str(decision.retry_after)})


def token_is_active(token: str) -> bool:
    """True when ``token`` is a valid, unrevoked API token (own short session; for ASGI middleware)."""
    session = db_module.SessionLocal()
    try:
        row = verify_token(session, token)
        session.commit()
        return row is not None
    finally:
        session.close()


def require_token(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiToken | None:
    """Validate ``Authorization: Bearer <token>`` unless ``require_auth`` is disabled."""
    if not settings.require_auth:
        return None
    header = request.headers.get("authorization", "")
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        raise _unauthorized("Missing bearer token")
    token = verify_token(db, credentials.strip())
    if token is None:
        db.rollback()
        _rate_limit(request, None)
        raise _unauthorized("Invalid or revoked token", "invalid_token")
    db.commit()  # persists the (throttled) last_used_at update; cheap when nothing changed
    _rate_limit(request, token)
    request.state.token_name = token.name
    return token
