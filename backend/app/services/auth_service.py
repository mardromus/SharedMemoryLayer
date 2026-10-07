"""Authentication flows: login, refresh-token rotation, logout, agent tokens, registration."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import AuditAction, record
from app.core.config import get_settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError, ValidationFailed
from app.models.enums import ActorType, AgentStatus, AuditStatus, RoleName
from app.models.identity import Agent, RefreshToken, Role, Team, User
from app.schemas.auth import RegisterRequest, TokenResponse
from app.security.dependencies import authenticate_api_key
from app.security.passwords import hash_password, needs_rehash, validate_password_policy, verify_password
from app.security.principal import Principal
from app.security.tokens import create_access_token, hash_refresh_token, new_refresh_token

GENERIC_LOGIN_ERROR = "Invalid username or password"


class AuthService:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    # ------------------------------------------------------------------ tokens
    def issue_tokens(
        self, subject_type: str, subject_id: uuid.UUID, *, family_id: uuid.UUID | None = None,
        ip: str | None = None, user_agent: str | None = None,
    ) -> tuple[TokenResponse, RefreshToken]:
        access, expires_in = create_access_token(subject_type, subject_id)
        plaintext, digest = new_refresh_token()
        expires_at = datetime.now(timezone.utc) + timedelta(days=self.settings.refresh_token_expiration_days)
        row = RefreshToken(
            user_id=subject_id if subject_type == "user" else None,
            agent_id=subject_id if subject_type == "agent" else None,
            token_hash=digest,
            family_id=family_id or uuid.uuid4(),
            expires_at=expires_at,
            ip_address=ip,
            user_agent=(user_agent or "")[:255] or None,
        )
        self.db.add(row)
        self.db.flush()
        return TokenResponse(access_token=access, expires_in=expires_in, refresh_token=plaintext, refresh_expires_at=expires_at), row

    # ------------------------------------------------------------------ login
    def login(self, username: str, password: str, ip: str | None, user_agent: str | None) -> tuple[TokenResponse, User]:
        user = self.db.scalar(select(User).where(User.username == username))
        now = datetime.now(timezone.utc)
        if user is not None and user.locked_until is not None and user.locked_until > now:
            verify_password(password, None)  # constant-ish time
            record(AuditAction.AUTH_LOGIN_FAILED, AuditStatus.DENIED, actor_name=username, actor_type=ActorType.USER, reason="Account temporarily locked", ip_address=ip, user_agent=user_agent)
            raise UnauthorizedError("Account temporarily locked due to repeated failed logins. Try again later.")

        if user is None or not verify_password(password, user.password_hash) or not user.is_active:
            reason = "Unknown username" if user is None else ("Account disabled" if not user.is_active else "Invalid password")
            if user is not None and reason == "Invalid password":
                user.failed_login_count += 1
                if user.failed_login_count >= self.settings.max_failed_logins:
                    user.locked_until = now + timedelta(minutes=self.settings.lockout_minutes)
                    user.failed_login_count = 0
                    reason += " - account locked"
                self.db.commit()
            record(AuditAction.AUTH_LOGIN_FAILED, AuditStatus.FAILURE, actor_name=username[:100], actor_type=ActorType.USER, reason=reason, ip_address=ip, user_agent=user_agent)
            raise UnauthorizedError(GENERIC_LOGIN_ERROR)

        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        tokens, _ = self.issue_tokens("user", user.id, ip=ip, user_agent=user_agent)
        self.db.commit()
        principal = Principal.from_user(user)
        record(AuditAction.AUTH_LOGIN, AuditStatus.SUCCESS, principal, resource_type="user", resource_id=user.id, ip_address=ip, user_agent=user_agent)
        return tokens, user

    def agent_token(self, api_key: str, ip: str | None, user_agent: str | None) -> TokenResponse:
        agent = authenticate_api_key(self.db, api_key)
        if agent is None or agent.status != AgentStatus.ACTIVE:
            record(AuditAction.AUTH_LOGIN_FAILED, AuditStatus.FAILURE, actor_type=ActorType.AGENT, actor_name="unknown-agent", reason="Invalid or revoked API key", ip_address=ip, user_agent=user_agent)
            raise UnauthorizedError("Invalid or revoked API key")
        tokens, _ = self.issue_tokens("agent", agent.id, ip=ip, user_agent=user_agent)
        self.db.commit()
        record(AuditAction.AUTH_AGENT_TOKEN, AuditStatus.SUCCESS, Principal.from_agent(agent), resource_type="agent", resource_id=agent.id, ip_address=ip, user_agent=user_agent)
        return tokens

    # ------------------------------------------------------------------ refresh / logout
    def refresh(self, token: str, ip: str | None, user_agent: str | None) -> tuple[TokenResponse, str, uuid.UUID]:
        row = self.db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token)).with_for_update())
        now = datetime.now(timezone.utc)
        if row is None:
            raise UnauthorizedError("Invalid refresh token")
        if row.revoked_at is not None:
            # Re-use of a rotated token: assume theft and kill the whole token family.
            self.db.execute(
                update(RefreshToken).where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now)
            )
            self.db.commit()
            record(AuditAction.AUTH_REFRESH, AuditStatus.DENIED, actor_type=ActorType.USER if row.user_id else ActorType.AGENT, actor_name=str(row.user_id or row.agent_id), reason="Refresh token reuse detected - session family revoked", ip_address=ip)
            raise UnauthorizedError("Refresh token has been revoked")
        if row.expires_at <= now:
            raise UnauthorizedError("Refresh token has expired")

        subject_type = "user" if row.user_id else "agent"
        subject_id = row.user_id or row.agent_id
        if subject_type == "user":
            subject = self.db.get(User, subject_id)
            active = subject is not None and subject.is_active
        else:
            subject = self.db.get(Agent, subject_id)
            active = subject is not None and subject.status == AgentStatus.ACTIVE
        if not active:
            raise UnauthorizedError("Account is disabled")

        tokens, new_row = self.issue_tokens(subject_type, subject_id, family_id=row.family_id, ip=ip, user_agent=user_agent)
        row.revoked_at = now
        row.replaced_by_id = new_row.id
        self.db.commit()
        return tokens, subject_type, subject_id

    def logout(self, token: str | None) -> None:
        if not token:
            return
        row = self.db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token)))
        if row is None:
            return
        now = datetime.now(timezone.utc)
        self.db.execute(
            update(RefreshToken).where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now)
        )
        self.db.commit()

    # ------------------------------------------------------------------ registration / users
    def register(self, data: RegisterRequest, actor: Principal | None, ip: str | None) -> User:
        is_admin = actor is not None and actor.is_admin
        if not is_admin:
            if actor is not None:
                raise ForbiddenError("Only administrators can register users")
            if not self.settings.allow_public_registration:
                record(AuditAction.AUTH_REGISTER, AuditStatus.DENIED, actor_name=data.username, reason="Public registration disabled", ip_address=ip)
                raise ForbiddenError("Public registration is disabled; ask an administrator for an account")
            if (data.role and data.role != RoleName.READ_ONLY) or data.team_id is not None:
                record(AuditAction.AUTH_REGISTER, AuditStatus.DENIED, actor_name=data.username, reason="Self-registration attempted privilege escalation", ip_address=ip, details={"requested_role": data.role})
                raise ForbiddenError("Self-registered accounts are always READ_ONLY without a team")

        problems = validate_password_policy(data.password, data.username, self.settings.password_min_length)
        if problems:
            raise ValidationFailed("Password does not meet the policy", details={"password": problems})
        if self.db.scalar(select(User.id).where(User.username == data.username)):
            raise ConflictError("Username already exists")
        if data.email and self.db.scalar(select(User.id).where(User.email == data.email)):
            raise ConflictError("Email already registered")

        role_name = (data.role or RoleName.READ_ONLY) if is_admin else RoleName.READ_ONLY
        role = self.db.scalar(select(Role).where(Role.name == role_name))
        if role is None:
            raise NotFoundError(f"Role {role_name} not found")
        if data.team_id is not None and self.db.get(Team, data.team_id) is None:
            raise NotFoundError("Team not found")

        user = User(
            username=data.username,
            email=data.email,
            full_name=data.full_name,
            password_hash=hash_password(data.password),
            role_id=role.id,
            team_id=data.team_id if is_admin else None,
        )
        self.db.add(user)
        self.db.commit()
        record(
            AuditAction.USER_CREATE if is_admin else AuditAction.AUTH_REGISTER,
            AuditStatus.SUCCESS,
            actor,
            actor_name=None if actor else data.username,
            resource_type="user",
            resource_id=user.id,
            ip_address=ip,
            details={"username": user.username, "role": role.name},
        )
        return user
