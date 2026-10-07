"""Permission catalogue and default role -> permission mapping.

The mapping below is only the *initial* seed. At runtime permissions are read
from the ``role_permissions`` table and can be changed by administrators via
``PUT /api/roles/{id}/permissions``.
"""
from __future__ import annotations

from app.models.enums import RoleName

MEMORY_CREATE = "memory:create"
MEMORY_READ = "memory:read"
MEMORY_UPDATE = "memory:update"
MEMORY_DELETE = "memory:delete"
MEMORY_SHARE = "memory:share"
MEMORY_VERIFY = "memory:verify"
AGENT_CREATE = "agent:create"
AGENT_UPDATE = "agent:update"
AGENT_DELETE = "agent:delete"
TEAM_CREATE = "team:create"
TEAM_UPDATE = "team:update"
AUDIT_READ = "audit:read"
USER_MANAGE = "user:manage"
ROLE_MANAGE = "role:manage"
CONFLICT_RESOLVE = "conflict:resolve"
SYSTEM_ADMIN = "system:admin"

PERMISSION_DESCRIPTIONS: dict[str, str] = {
    MEMORY_CREATE: "Create memories",
    MEMORY_READ: "Read / search authorised memories",
    MEMORY_UPDATE: "Update memories (creates a new version)",
    MEMORY_DELETE: "Archive or delete memories",
    MEMORY_SHARE: "Share memories with other teams or agents",
    MEMORY_VERIFY: "Verify or reject memories",
    AGENT_CREATE: "Register agents",
    AGENT_UPDATE: "Update agents, their roles and API keys",
    AGENT_DELETE: "Delete agents",
    TEAM_CREATE: "Create teams",
    TEAM_UPDATE: "Update teams and their access configuration",
    AUDIT_READ: "Read audit logs",
    USER_MANAGE: "Manage human users",
    ROLE_MANAGE: "Configure role permissions",
    CONFLICT_RESOLVE: "Resolve memory conflicts",
    SYSTEM_ADMIN: "System configuration and maintenance",
}

ALL_PERMISSIONS = list(PERMISSION_DESCRIPTIONS)

DEFAULT_ROLE_PERMISSIONS: dict[str, list[str]] = {
    RoleName.ADMIN: ALL_PERMISSIONS,
    RoleName.TEAM_MANAGER: [
        MEMORY_CREATE,
        MEMORY_READ,
        MEMORY_UPDATE,
        MEMORY_DELETE,
        MEMORY_SHARE,
        MEMORY_VERIFY,
        AGENT_CREATE,
        AGENT_UPDATE,
        AGENT_DELETE,
        TEAM_UPDATE,
        AUDIT_READ,
        CONFLICT_RESOLVE,
    ],
    RoleName.AGENT: [MEMORY_CREATE, MEMORY_READ, MEMORY_UPDATE, MEMORY_SHARE],
    RoleName.READ_ONLY: [MEMORY_READ],
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    RoleName.ADMIN: "Full access to the platform",
    RoleName.TEAM_MANAGER: "Manages memories and agents of their own team",
    RoleName.AGENT: "Creates and retrieves authorised memories",
    RoleName.READ_ONLY: "Can only retrieve authorised memories",
}

# Roles a team manager may hand out (prevents privilege escalation).
TEAM_MANAGER_ASSIGNABLE_ROLES = {RoleName.AGENT, RoleName.READ_ONLY}
