"""FastAPI dependencies: database session, settings and bearer-token authentication.

Rate limiting (per valid token, else per client address) happens earlier, in
:class:`flipradar.security.RateLimitMiddleware`, which checks the token with :func:`active_token_id`
before the request body is read or the route is resolved."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import db as db_module
from ..config import Settings
from ..config import get_settings as _env_settings
from ..models import ApiToken
from ..security import verify_token

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


def active_token_id(token: str) -> int | None:
    """Id of the valid, unrevoked API token ``token``, else None (own short session; for ASGI
    middleware, which runs before FastAPI dependencies)."""
    session = db_module.SessionLocal()
    try:
        row = verify_token(session, token)
        session.commit()
        return row.id if row is not None else None
    finally:
        session.close()


def token_is_active(token: str) -> bool:
    """True when ``token`` is a valid, unrevoked API token."""
    return active_token_id(token) is not None


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
        raise _unauthorized("Invalid or revoked token", "invalid_token")
    db.commit()  # persists the (throttled) last_used_at update; cheap when nothing changed
    request.state.token_name = token.name
    return token
