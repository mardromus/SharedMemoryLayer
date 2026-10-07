from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal, get_optional_principal, request_meta
from app.audit import AuditAction, record
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import UnauthorizedError
from app.core.rate_limit import rate_limit
from app.models.enums import AuditStatus
from app.schemas.auth import AgentTokenRequest, LoginRequest, MeResponse, RefreshRequest, RegisterRequest, TokenResponse
from app.schemas.identity import UserOut
from app.security.dependencies import load_principal
from app.security.principal import Principal
from app.services.auth_service import AuthService
from app.services.identity_service import IdentityService

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

REFRESH_COOKIE = "sml_refresh"


def _set_refresh_cookie(response: Response, tokens: TokenResponse) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token or "",
        max_age=settings.refresh_token_expiration_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api/auth",
    )


@router.post("/register", response_model=UserOut, status_code=201, dependencies=[Depends(rate_limit("auth"))])
def register(data: RegisterRequest, request: Request, db: Session = Depends(get_db), actor: Principal | None = Depends(get_optional_principal)):
    """Create a human user. Administrators may choose role/team; public self-registration
    (if enabled) always yields a READ_ONLY user without a team."""
    ip, _ = request_meta(request)
    user = AuthService(db).register(data, actor, ip)
    return IdentityService(db).user_out(user)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit("auth"))])
def login(data: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    ip, ua = request_meta(request)
    tokens, _ = AuthService(db).login(data.username, data.password, ip, ua)
    _set_refresh_cookie(response, tokens)
    return tokens


@router.post("/token", response_model=TokenResponse, include_in_schema=True, dependencies=[Depends(rate_limit("auth"))],
             summary="OAuth2 password flow (used by the Swagger 'Authorize' button)")
def token(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    ip, ua = request_meta(request)
    tokens, _ = AuthService(db).login(form.username, form.password, ip, ua)
    return tokens


@router.post("/agent-token", response_model=TokenResponse, dependencies=[Depends(rate_limit("auth"))])
def agent_token(data: AgentTokenRequest, request: Request, db: Session = Depends(get_db)):
    """Exchange an agent API key for a short-lived JWT access token (+ refresh token)."""
    ip, ua = request_meta(request)
    return AuthService(db).agent_token(data.api_key, ip, ua)


@router.post("/refresh", response_model=TokenResponse, dependencies=[Depends(rate_limit("auth"))])
def refresh(request: Request, response: Response, data: RefreshRequest | None = None, db: Session = Depends(get_db)):
    """Rotate a refresh token (body field or the HttpOnly cookie set at login)."""
    token = (data.refresh_token if data else None) or request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise UnauthorizedError("Refresh token missing")
    ip, ua = request_meta(request)
    tokens, subject_type, subject_id = AuthService(db).refresh(token, ip, ua)
    if request.cookies.get(REFRESH_COOKIE):
        _set_refresh_cookie(response, tokens)
    principal = load_principal(db, subject_type, subject_id)
    record(AuditAction.AUTH_REFRESH, AuditStatus.SUCCESS, principal, ip_address=ip, user_agent=ua)
    return tokens


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, data: RefreshRequest | None = None, db: Session = Depends(get_db),
           principal: Principal | None = Depends(get_optional_principal)):
    token = (data.refresh_token if data else None) or request.cookies.get(REFRESH_COOKIE)
    AuthService(db).logout(token)
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    ip, ua = request_meta(request)
    record(AuditAction.AUTH_LOGOUT, AuditStatus.SUCCESS, principal, ip_address=ip, user_agent=ua)
    response.status_code = 204
    return response


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_current_principal)):
    team_name = None
    if principal.user is not None and principal.user.team is not None:
        team_name = principal.user.team.name
    elif principal.agent is not None:
        team_name = principal.agent.team.name
    return MeResponse(
        id=principal.id,
        kind=principal.kind.value,
        name=principal.name,
        team_id=principal.team_id,
        team_name=team_name,
        roles=sorted(principal.roles),
        permissions=sorted(principal.permissions),
        email=principal.user.email if principal.user else None,
        full_name=principal.user.full_name if principal.user else None,
    )
