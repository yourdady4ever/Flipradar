"""GET /api/v1/auth/check - lets clients verify their token."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...config import Settings
from ...models import ApiToken
from ...schemas import AuthCheckOut
from ..deps import get_settings, require_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/check", response_model=AuthCheckOut)
def check(token: ApiToken | None = Depends(require_token), settings: Settings = Depends(get_settings)) -> AuthCheckOut:
    return AuthCheckOut(ok=True, token_name=token.name if token else None, auth_required=settings.require_auth)
