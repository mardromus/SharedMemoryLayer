"""The authenticated identity (human user or AI agent) making a request."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.models.enums import ActorType, RoleName
from app.models.identity import Agent, User


@dataclass
class Principal:
    kind: ActorType  # USER or AGENT
    id: uuid.UUID
    name: str
    team_id: uuid.UUID | None
    roles: set[str] = field(default_factory=set)
    permissions: set[str] = field(default_factory=set)
    user: User | None = None
    agent: Agent | None = None
    # Set when an administrator simulates an agent (Agent Playground)
    on_behalf_of: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None

    @property
    def is_admin(self) -> bool:
        return RoleName.ADMIN in self.roles

    @property
    def is_team_manager(self) -> bool:
        return RoleName.TEAM_MANAGER in self.roles

    @property
    def is_agent(self) -> bool:
        return self.kind == ActorType.AGENT

    @property
    def agent_id(self) -> uuid.UUID | None:
        return self.id if self.is_agent else None

    @property
    def user_id(self) -> uuid.UUID | None:
        return self.id if self.kind == ActorType.USER else None

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def manages_team(self, team_id: uuid.UUID | None) -> bool:
        return self.is_admin or (self.is_team_manager and team_id is not None and team_id == self.team_id)

    @classmethod
    def from_user(cls, user: User) -> "Principal":
        return cls(
            kind=ActorType.USER,
            id=user.id,
            name=user.username,
            team_id=user.team_id,
            roles={user.role.name},
            permissions=set(user.role.permission_codes),
            user=user,
        )

    @classmethod
    def from_agent(cls, agent: Agent) -> "Principal":
        perms: set[str] = set()
        for role in agent.roles:
            perms |= role.permission_codes
        return cls(
            kind=ActorType.AGENT,
            id=agent.id,
            name=agent.name,
            team_id=agent.team_id,
            roles={r.name for r in agent.roles},
            permissions=perms,
            agent=agent,
        )
