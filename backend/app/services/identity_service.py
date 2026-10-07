"""Teams, agents, API keys, users, roles and projects."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import AuditAction, record
from app.core.config import get_settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailed
from app.models.enums import AgentStatus, AuditStatus, MemoryStatus, RoleName
from app.models.identity import Agent, ApiKey, Permission, Role, Team, User
from app.models.memory import Memory, Project
from app.schemas.identity import (
    AgentCreate,
    AgentOut,
    AgentUpdate,
    ApiKeyCreate,
    ApiKeyOut,
    ProjectCreate,
    ProjectOut,
    RoleOut,
    TeamCreate,
    TeamOut,
    TeamUpdate,
    UserOut,
    UserUpdate,
)
from app.security import permissions as P
from app.security.passwords import hash_password, validate_password_policy
from app.security.principal import Principal
from app.security.tokens import new_api_key


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:100] or uuid.uuid4().hex[:8]


class IdentityService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ teams
    def team_out(self, team: Team) -> TeamOut:
        agents = self.db.scalar(select(func.count(Agent.id)).where(Agent.team_id == team.id, Agent.status != AgentStatus.DELETED)) or 0
        members = self.db.scalar(select(func.count(User.id)).where(User.team_id == team.id)) or 0
        memories = self.db.scalar(select(func.count(Memory.id)).where(Memory.team_id == team.id, Memory.status == MemoryStatus.ACTIVE)) or 0
        out = TeamOut.model_validate(team)
        out.agent_count, out.member_count, out.memory_count = agents, members, memories
        return out

    def list_teams(self, include_inactive: bool = False) -> list[TeamOut]:
        stmt = select(Team).order_by(Team.name)
        if not include_inactive:
            stmt = stmt.where(Team.is_active.is_(True))
        return [self.team_out(t) for t in self.db.scalars(stmt)]

    def get_team(self, team_id: uuid.UUID) -> Team:
        team = self.db.get(Team, team_id)
        if team is None:
            raise NotFoundError("Team not found")
        return team

    def create_team(self, principal: Principal, data: TeamCreate) -> Team:
        if self.db.scalar(select(Team.id).where(func.lower(Team.name) == data.name.lower())):
            raise ConflictError("A team with this name already exists")
        team = Team(
            name=data.name,
            slug=slugify(data.name),
            description=data.description,
            default_visibility=data.default_visibility,
            allow_cross_team_sharing=data.allow_cross_team_sharing,
        )
        self.db.add(team)
        self.db.commit()
        record(AuditAction.TEAM_CREATE, AuditStatus.SUCCESS, principal, resource_type="team", resource_id=team.id, details={"name": team.name})
        return team

    def update_team(self, principal: Principal, team_id: uuid.UUID, data: TeamUpdate) -> Team:
        team = self.get_team(team_id)
        if not principal.manages_team(team.id):
            record(AuditAction.TEAM_UPDATE, AuditStatus.DENIED, principal, resource_type="team", resource_id=team.id, reason="Only administrators or the team's manager can update a team")
            raise ForbiddenError("Only administrators or the team's manager can update this team")
        changes = data.model_dump(exclude_unset=True)
        if "name" in changes and changes["name"] and changes["name"] != team.name:
            if self.db.scalar(select(Team.id).where(func.lower(Team.name) == changes["name"].lower(), Team.id != team.id)):
                raise ConflictError("A team with this name already exists")
            team.name = changes["name"]
            team.slug = slugify(changes["name"])
        for field in ("description", "default_visibility", "allow_cross_team_sharing", "is_active"):
            if field in changes and changes[field] is not None:
                setattr(team, field, changes[field])
        self.db.commit()
        record(AuditAction.TEAM_UPDATE, AuditStatus.SUCCESS, principal, resource_type="team", resource_id=team.id, details={"changes": changes})
        return team

    # ------------------------------------------------------------------ roles
    def role_by_name(self, name: str) -> Role:
        role = self.db.scalar(select(Role).where(Role.name == name))
        if role is None:
            raise NotFoundError(f"Role {name} not found")
        return role

    def _check_assignable(self, principal: Principal, role_names: list[str], target: str) -> list[Role]:
        roles = [self.role_by_name(n) for n in dict.fromkeys(role_names)]
        if not principal.is_admin:
            forbidden = [r.name for r in roles if r.name not in P.TEAM_MANAGER_ASSIGNABLE_ROLES]
            if forbidden:
                record(AuditAction.ROLE_CHANGE, AuditStatus.DENIED, principal, resource_type="agent", reason=f"Privilege escalation attempt: cannot assign {', '.join(forbidden)}", details={"target": target})
                raise ForbiddenError(f"You cannot assign role(s): {', '.join(forbidden)}")
        return roles

    def list_roles(self) -> list[RoleOut]:
        return [
            RoleOut(id=r.id, name=r.name, description=r.description, is_system=r.is_system, permissions=sorted(r.permission_codes))
            for r in self.db.scalars(select(Role).order_by(Role.name))
        ]

    def list_permissions(self) -> list[Permission]:
        return list(self.db.scalars(select(Permission).order_by(Permission.code)))

    def set_role_permissions(self, principal: Principal, role_id: uuid.UUID, codes: list[str]) -> Role:
        role = self.db.get(Role, role_id)
        if role is None:
            raise NotFoundError("Role not found")
        perms = list(self.db.scalars(select(Permission).where(Permission.code.in_(codes))))
        unknown = set(codes) - {p.code for p in perms}
        if unknown:
            raise ValidationFailed(f"Unknown permissions: {', '.join(sorted(unknown))}")
        if role.name == RoleName.ADMIN and {P.ROLE_MANAGE, P.SYSTEM_ADMIN} - set(codes):
            raise ValidationFailed("ADMIN must keep role:manage and system:admin (prevents lock-out)")
        before = sorted(role.permission_codes)
        role.permissions = perms
        self.db.commit()
        record(AuditAction.ROLE_PERMISSIONS_UPDATE, AuditStatus.SUCCESS, principal, resource_type="role", resource_id=role.id, details={"role": role.name, "before": before, "after": sorted(codes)})
        return role

    # ------------------------------------------------------------------ agents
    def agent_out(self, agent: Agent, include_keys: bool = False) -> AgentOut:
        perms: set[str] = set()
        for r in agent.roles:
            perms |= r.permission_codes
        created = self.db.scalar(select(func.count(Memory.id)).where(Memory.creator_agent_id == agent.id)) or 0
        keys = []
        if include_keys:
            keys = [ApiKeyOut.model_validate(k) for k in self.db.scalars(select(ApiKey).where(ApiKey.agent_id == agent.id).order_by(ApiKey.created_at.desc()))]
        return AgentOut(
            id=agent.id, name=agent.name, description=agent.description, agent_type=agent.agent_type,
            team_id=agent.team_id, team_name=agent.team.name, status=agent.status, capabilities=list(agent.capabilities or []),
            persona=agent.persona, roles=sorted(r.name for r in agent.roles), permissions=sorted(perms),
            last_active_at=agent.last_active_at, created_at=agent.created_at, memories_created=created, api_keys=keys,
        )

    def list_agents(self, team_id: uuid.UUID | None = None, include_deleted: bool = False) -> list[Agent]:
        stmt = select(Agent).order_by(Agent.name)
        if team_id:
            stmt = stmt.where(Agent.team_id == team_id)
        if not include_deleted:
            stmt = stmt.where(Agent.status != AgentStatus.DELETED)
        return list(self.db.scalars(stmt))

    def get_agent(self, agent_id: uuid.UUID) -> Agent:
        agent = self.db.get(Agent, agent_id)
        if agent is None or agent.status == AgentStatus.DELETED:
            raise NotFoundError("Agent not found")
        return agent

    def _require_team_scope(self, principal: Principal, team_id: uuid.UUID, action: str, resource_id=None) -> None:
        if not principal.manages_team(team_id):
            record(action, AuditStatus.DENIED, principal, resource_type="agent", resource_id=resource_id, reason="Can only manage agents of your own team", details={"team_id": team_id})
            raise ForbiddenError("You can only manage agents of your own team")

    def create_agent(self, principal: Principal, data: AgentCreate) -> tuple[Agent, str | None]:
        self.get_team(data.team_id)
        self._require_team_scope(principal, data.team_id, AuditAction.AGENT_CREATE)
        if self.db.scalar(select(Agent.id).where(func.lower(Agent.name) == data.name.lower())):
            raise ConflictError("An agent with this name already exists")
        roles = self._check_assignable(principal, data.roles or [RoleName.AGENT], data.name)
        agent = Agent(
            name=data.name, description=data.description, agent_type=data.agent_type, team_id=data.team_id,
            capabilities=data.capabilities, persona=data.persona, created_by_user_id=principal.user_id,
        )
        agent.roles = roles
        self.db.add(agent)
        self.db.flush()
        plaintext = None
        if data.issue_api_key:
            plaintext, _ = self._new_key(agent, principal, ApiKeyCreate(name="default"))
        self.db.commit()
        record(AuditAction.AGENT_CREATE, AuditStatus.SUCCESS, principal, resource_type="agent", resource_id=agent.id, details={"name": agent.name, "team_id": agent.team_id, "roles": [r.name for r in roles]})
        return agent, plaintext

    def update_agent(self, principal: Principal, agent_id: uuid.UUID, data: AgentUpdate) -> Agent:
        agent = self.get_agent(agent_id)
        self._require_team_scope(principal, agent.team_id, AuditAction.AGENT_UPDATE, agent.id)
        changes = data.model_dump(exclude_unset=True)
        if "team_id" in changes and changes["team_id"] and changes["team_id"] != agent.team_id:
            self.get_team(changes["team_id"])
            self._require_team_scope(principal, changes["team_id"], AuditAction.AGENT_UPDATE, agent.id)
            agent.team_id = changes["team_id"]
        if "name" in changes and changes["name"] and changes["name"] != agent.name:
            if self.db.scalar(select(Agent.id).where(func.lower(Agent.name) == changes["name"].lower(), Agent.id != agent.id)):
                raise ConflictError("An agent with this name already exists")
            agent.name = changes["name"]
        for field in ("description", "agent_type", "capabilities", "persona", "status"):
            if field in changes and changes[field] is not None:
                setattr(agent, field, changes[field])
        if data.roles is not None:
            roles = self._check_assignable(principal, data.roles, agent.name)
            before = sorted(r.name for r in agent.roles)
            agent.roles = roles
            record(AuditAction.ROLE_CHANGE, AuditStatus.SUCCESS, principal, resource_type="agent", resource_id=agent.id, details={"before": before, "after": sorted(r.name for r in roles)})
        self.db.commit()
        self.db.refresh(agent)
        record(AuditAction.AGENT_UPDATE, AuditStatus.SUCCESS, principal, resource_type="agent", resource_id=agent.id, details={"changes": {k: v for k, v in changes.items() if k != "persona"}})
        return agent

    def delete_agent(self, principal: Principal, agent_id: uuid.UUID) -> None:
        agent = self.get_agent(agent_id)
        self._require_team_scope(principal, agent.team_id, AuditAction.AGENT_DELETE, agent.id)
        # Soft delete keeps provenance of memories the agent created.
        agent.status = AgentStatus.DELETED
        now = datetime.now(timezone.utc)
        for key in self.db.scalars(select(ApiKey).where(ApiKey.agent_id == agent.id, ApiKey.revoked_at.is_(None))):
            key.revoked_at = now
        self.db.commit()
        record(AuditAction.AGENT_DELETE, AuditStatus.SUCCESS, principal, resource_type="agent", resource_id=agent.id, details={"name": agent.name})

    # ------------------------------------------------------------------ API keys
    def _new_key(self, agent: Agent, principal: Principal, data: ApiKeyCreate) -> tuple[str, ApiKey]:
        plaintext, prefix, digest = new_api_key()
        key = ApiKey(agent_id=agent.id, name=data.name, prefix=prefix, key_hash=digest, expires_at=data.expires_at, created_by_user_id=principal.user_id)
        self.db.add(key)
        self.db.flush()
        return plaintext, key

    def create_api_key(self, principal: Principal, agent_id: uuid.UUID, data: ApiKeyCreate) -> tuple[str, ApiKey]:
        agent = self.get_agent(agent_id)
        self._require_team_scope(principal, agent.team_id, AuditAction.API_KEY_CREATE, agent.id)
        plaintext, key = self._new_key(agent, principal, data)
        self.db.commit()
        record(AuditAction.API_KEY_CREATE, AuditStatus.SUCCESS, principal, resource_type="agent", resource_id=agent.id, details={"key_prefix": key.prefix, "name": key.name})
        return plaintext, key

    def revoke_api_key(self, principal: Principal, agent_id: uuid.UUID, key_id: uuid.UUID) -> None:
        agent = self.get_agent(agent_id)
        self._require_team_scope(principal, agent.team_id, AuditAction.API_KEY_REVOKE, agent.id)
        key = self.db.get(ApiKey, key_id)
        if key is None or key.agent_id != agent.id:
            raise NotFoundError("API key not found")
        key.revoked_at = key.revoked_at or datetime.now(timezone.utc)
        self.db.commit()
        record(AuditAction.API_KEY_REVOKE, AuditStatus.SUCCESS, principal, resource_type="agent", resource_id=agent.id, details={"key_prefix": key.prefix})

    # ------------------------------------------------------------------ users
    def user_out(self, user: User) -> UserOut:
        return UserOut(
            id=user.id, username=user.username, email=user.email, full_name=user.full_name, role=user.role.name,
            team_id=user.team_id, team_name=user.team.name if user.team else None, is_active=user.is_active,
            last_login_at=user.last_login_at, created_at=user.created_at,
        )

    def list_users(self) -> list[UserOut]:
        return [self.user_out(u) for u in self.db.scalars(select(User).order_by(User.username))]

    def update_user(self, principal: Principal, user_id: uuid.UUID, data: UserUpdate) -> User:
        user = self.db.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found")
        changes = data.model_dump(exclude_unset=True, exclude={"password"})
        if data.role is not None and data.role != user.role.name:
            if user.id == principal.user_id:
                raise ForbiddenError("You cannot change your own role")
            role = self.role_by_name(data.role)
            before = user.role.name
            user.role_id = role.id
            self.db.flush()
            self.db.refresh(user)
            record(AuditAction.ROLE_CHANGE, AuditStatus.SUCCESS, principal, resource_type="user", resource_id=user.id, details={"before": before, "after": role.name})
        if data.clear_team:
            user.team_id = None
        elif data.team_id is not None:
            self.get_team(data.team_id)
            user.team_id = data.team_id
        if data.email is not None:
            user.email = data.email
        if data.full_name is not None:
            user.full_name = data.full_name
        if data.is_active is not None:
            if user.id == principal.user_id and not data.is_active:
                raise ForbiddenError("You cannot deactivate yourself")
            user.is_active = data.is_active
        if data.password:
            problems = validate_password_policy(data.password, user.username, get_settings().password_min_length)
            if problems:
                raise ValidationFailed("Password does not meet the policy", details={"password": problems})
            user.password_hash = hash_password(data.password)
        self.db.commit()
        self.db.refresh(user)
        record(AuditAction.USER_UPDATE, AuditStatus.SUCCESS, principal, resource_type="user", resource_id=user.id, details={"changes": {k: v for k, v in changes.items()}, "password_changed": bool(data.password)})
        return user

    # ------------------------------------------------------------------ projects
    def list_projects(self) -> list[ProjectOut]:
        out = []
        for p in self.db.scalars(select(Project).order_by(Project.name)):
            item = ProjectOut.model_validate(p)
            item.memory_count = self.db.scalar(select(func.count(Memory.id)).where(Memory.project_id == p.id, Memory.status == MemoryStatus.ACTIVE)) or 0
            out.append(item)
        return out

    def create_project(self, principal: Principal, data: ProjectCreate) -> Project:
        team_id = data.team_id or principal.team_id
        if team_id is not None and not principal.manages_team(team_id):
            raise ForbiddenError("You can only create projects for your own team")
        if self.db.scalar(select(Project.id).where(func.lower(Project.name) == data.name.lower())):
            raise ConflictError("A project with this name already exists")
        project = Project(name=data.name, description=data.description, team_id=team_id)
        self.db.add(project)
        self.db.commit()
        record(AuditAction.PROJECT_CREATE, AuditStatus.SUCCESS, principal, resource_type="project", resource_id=project.id, details={"name": project.name})
        return project
