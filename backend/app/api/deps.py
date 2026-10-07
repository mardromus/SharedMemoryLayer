"""Shared API dependencies."""
from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.security.dependencies import (
    api_key_scheme,
    bearer_scheme,
    get_current_principal,
    rate_limited,
    require_admin,
    require_permission,
    require_user,
)
from app.security.principal import Principal

__all__ = [
    "get_current_principal",
    "get_optional_principal",
    "rate_limited",
    "require_admin",
    "require_permission",
    "require_user",
]


def get_optional_principal(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    api_key: str | None = Depends(api_key_scheme),
) -> Principal | None:
    if credentials is None and not api_key:
        return None
    return get_current_principal(request, db, credentials, api_key)


def request_meta(request: Request) -> tuple[str, str | None]:
    return client_ip(request), request.headers.get("user-agent")
