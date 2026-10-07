"""Schemas for teams, agents, users, roles, projects and API keys."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import AgentStatus, Visibility
from app.schemas.common import CleanText, EntityName, ORMModel, PlainLine


# ---------------------------------------------------------------- teams
class TeamCreate(BaseModel):
    name: EntityName
    description: CleanText = Field("", max_length=2000)
    default_visibility: Visibility = Visibility.TEAM
    allow_cross_team_sharing: bool = True


class TeamUpdate(BaseModel):
    name: EntityName | None = None
    description: CleanText | None = Field(None, max_length=2000)
    default_visibility: Visibility | None = None
    allow_cross_team_sharing: bool | None = None
    is_active: bool | None = None


class TeamOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str
    default_visibility: Visibility
    allow_cross_team_sharing: bool
    is_active: bool
    created_at: datetime
    agent_count: int = 0
    member_count: int = 0
    memory_count: int = 0


# ---------------------------------------------------------------- agents
class AgentCreate(BaseModel):
    name: EntityName
    team_id: uuid.UUID
    description: CleanText = Field("", max_length=2000)
    agent_type: PlainLine = Field("custom", max_length=50)
    capabilities: list[PlainLine] = Field(default_factory=list, max_length=30)
    persona: CleanText = Field("", max_length=4000)
    roles: list[str] = Field(default_factory=lambda: ["AGENT"], max_length=5)
    issue_api_key: bool = True


class AgentUpdate(BaseModel):
    name: EntityName | None = None
    team_id: uuid.UUID | None = None
    description: CleanText | None = Field(None, max_length=2000)
    agent_type: PlainLine | None = Field(None, max_length=50)
    capabilities: list[PlainLine] | None = Field(None, max_length=30)
    persona: CleanText | None = Field(None, max_length=4000)
    status: AgentStatus | None = None
    roles: list[str] | None = Field(None, max_length=5)


class ApiKeyOut(ORMModel):
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreate(BaseModel):
    name: PlainLine = Field("default", min_length=1, max_length=100)
    expires_at: datetime | None = None


class ApiKeyCreated(ApiKeyOut):
    api_key: str = Field(..., description="Plaintext key - shown only once")


class AgentOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str
    agent_type: str
    team_id: uuid.UUID
    team_name: str
    status: AgentStatus
    capabilities: list[str]
    persona: str
    roles: list[str]
    permissions: list[str]
    last_active_at: datetime | None
    created_at: datetime
    memories_created: int = 0
    api_keys: list[ApiKeyOut] = []


class AgentCreated(AgentOut):
    api_key: str | None = Field(None, description="Plaintext key - shown only once")


# ---------------------------------------------------------------- users
class UserOut(ORMModel):
    id: uuid.UUID
    username: str
    email: str | None
    full_name: str
    role: str
    team_id: uuid.UUID | None
    team_name: str | None
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: PlainLine | None = Field(None, max_length=128)
    role: str | None = None
    team_id: uuid.UUID | None = None
    clear_team: bool = False
    is_active: bool | None = None
    password: str | None = Field(None, max_length=128)


# ---------------------------------------------------------------- roles
class PermissionOut(ORMModel):
    id: uuid.UUID
    code: str
    description: str


class RoleOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str
    is_system: bool
    permissions: list[str]


class RolePermissionsUpdate(BaseModel):
    permissions: list[str] = Field(..., max_length=64)


class AgentRolesUpdate(BaseModel):
    roles: list[str] = Field(..., min_length=1, max_length=5)


# ---------------------------------------------------------------- projects
class ProjectCreate(BaseModel):
    name: EntityName
    description: CleanText = Field("", max_length=2000)
    team_id: uuid.UUID | None = None


class ProjectOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str
    team_id: uuid.UUID | None
    created_at: datetime
    memory_count: int = 0

    @field_validator("description", mode="before")
    @classmethod
    def _desc(cls, v: str | None) -> str:
        return v or ""
