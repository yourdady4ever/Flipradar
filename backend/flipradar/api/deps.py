"""FastAPI dependencies: database session, settings and bearer-token authentication."""

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
