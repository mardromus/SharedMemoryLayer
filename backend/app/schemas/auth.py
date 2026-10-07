from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import USERNAME_RE, PlainLine


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class AgentTokenRequest(BaseModel):
    api_key: str = Field(..., min_length=10, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str | None = Field(None, max_length=200)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    email: EmailStr | None = None
    full_name: PlainLine = Field("", max_length=128)
    # Only honoured when an administrator performs the registration
    role: str | None = None
    team_id: uuid.UUID | None = None

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        if not USERNAME_RE.match(v):
            raise ValueError("username may contain letters, digits, '.', '_' and '-' (3-64 chars)")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str | None = None
    refresh_expires_at: datetime | None = None


class MeResponse(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    team_id: uuid.UUID | None
    team_name: str | None
    roles: list[str]
    permissions: list[str]
    email: str | None = None
    full_name: str | None = None
