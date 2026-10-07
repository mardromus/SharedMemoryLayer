"""FastAPI dependencies for authentication and RBAC."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditAction, record
from app.core.database import get_db
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.rate_limit import client_ip, enforce
from app.models.enums import AgentStatus, AuditStatus
from app.models.identity import Agent, ApiKey, User
from app.security.principal import Principal
from app.security.tokens import TokenError, decode_access_token, parse_api_key, verify_api_key

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False, description="Agent API key")

_AUTH_HEADERS = {"WWW-Authenticate": "Bearer"}


def _touch_agent(db: Session, agent: Agent) -> None:
    now = datetime.now(timezone.utc)
    if agent.last_active_at is None or (now - agent.last_active_at).total_seconds() > 30:
        agent.last_active_at = now
        db.commit()


def authenticate_api_key(db: Session, raw_key: str) -> Agent | None:
    prefix = parse_api_key(raw_key)
    if prefix is None:
        return None
    key = db.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if key is None or not verify_api_key(raw_key, key.key_hash):
        return None
    now = datetime.now(timezone.utc)
    if key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= now):
        return None
    if key.last_used_at is None or (now - key.last_used_at).total_seconds() > 30:
        key.last_used_at = now
        db.commit()
    return key.agent


def load_principal(db: Session, subject_type: str, subject_id) -> Principal | None:
    if subject_type == "user":
        user = db.get(User, subject_id)
        if user is None or not user.is_active:
            return None
        return Principal.from_user(user)
    agent = db.get(Agent, subject_id)
    if agent is None or agent.status != AgentStatus.ACTIVE:
        return None
    _touch_agent(db, agent)
    return Principal.from_agent(agent)


def get_current_principal(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    api_key: str | None = Depends(api_key_scheme),
) -> Principal:
    principal: Principal | None = None
    ip = client_ip(request)
    if credentials is not None:
        try:
            claims = decode_access_token(credentials.credentials)
        except TokenError as exc:
            record(
                AuditAction.AUTH_INVALID_TOKEN,
                AuditStatus.DENIED,
                reason=str(exc),
                ip_address=ip,
                details={"path": request.url.path},
            )
            raise UnauthorizedError(str(exc), headers=_AUTH_HEADERS) from exc
        principal = load_principal(db, claims.subject_type, claims.subject_id)
        if principal is None:
            raise UnauthorizedError("Account is disabled or no longer exists", headers=_AUTH_HEADERS)
    elif api_key:
        agent = authenticate_api_key(db, api_key)
        if agent is None or agent.status != AgentStatus.ACTIVE:
            enforce(request, "auth")  # slow down API-key guessing
            record(
                AuditAction.AUTH_INVALID_TOKEN,
                AuditStatus.DENIED,
                reason="Invalid or revoked API key",
                ip_address=ip,
                details={"path": request.url.path},
            )
            raise UnauthorizedError("Invalid or revoked API key", headers=_AUTH_HEADERS)
        _touch_agent(db, agent)
        principal = Principal.from_agent(agent)
    else:
        raise UnauthorizedError("Authentication required", headers=_AUTH_HEADERS)

    principal.ip_address = ip
    principal.user_agent = request.headers.get("user-agent")
    return principal


def require_permission(*permissions: str):
    """Dependency factory: principal must hold *all* the listed permissions."""

    def _dependency(request: Request, principal: Principal = Depends(get_current_principal)) -> Principal:
        missing = [p for p in permissions if not principal.has(p)]
        if missing:
            record(
                AuditAction.ACCESS_DENIED,
                AuditStatus.DENIED,
                principal,
                reason=f"Missing permission {', '.join(missing)}",
                details={"path": request.url.path, "method": request.method},
            )
            raise ForbiddenError(f"Missing permission: {', '.join(missing)}")
        return principal

    return _dependency


def require_admin(request: Request, principal: Principal = Depends(get_current_principal)) -> Principal:
    if not principal.is_admin:
        record(
            AuditAction.ACCESS_DENIED,
            AuditStatus.DENIED,
            principal,
            reason="Administrator role required",
            details={"path": request.url.path, "method": request.method},
        )
        raise ForbiddenError("Administrator role required")
    return principal


def require_user(principal: Principal = Depends(get_current_principal)) -> Principal:
    if principal.user is None:
        raise ForbiddenError("This operation is only available to human users")
    return principal


def rate_limited(bucket: str):
    """Per-principal rate limit dependency for authenticated endpoints."""

    def _dependency(request: Request, principal: Principal = Depends(get_current_principal)) -> Principal:
        enforce(request, bucket, identity=f"{principal.kind.value}:{principal.id}")
        return principal

    return _dependency
