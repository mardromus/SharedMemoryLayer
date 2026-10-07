"""Domain enumerations (stored as VARCHAR + CHECK constraints)."""
from __future__ import annotations

from enum import StrEnum


class MemoryType(StrEnum):
    KNOWLEDGE = "knowledge"
    DECISION = "decision"
    LEARNING = "learning"
    EXPERIENCE = "experience"
    CONTEXT = "context"
    TASK_RESULT = "task_result"
    INCIDENT = "incident"
    DOCUMENTATION = "documentation"
    SOLUTION = "solution"


class Visibility(StrEnum):
    PRIVATE = "private"
    TEAM = "team"
    SELECTED_TEAMS = "selected_teams"
    SELECTED_AGENTS = "selected_agents"
    ORGANIZATION = "organization"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


class ConflictStatus(StrEnum):
    NONE = "none"
    POTENTIAL_CONFLICT = "potential_conflict"
    RESOLVED = "resolved"


class ConflictRecordStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ConflictResolution(StrEnum):
    KEEP_NEW = "keep_new"
    KEEP_EXISTING = "keep_existing"
    KEEP_BOTH = "keep_both"
    DISMISS = "dismiss"


class AgentStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"


class AuditStatus(StrEnum):
    SUCCESS = "success"
    ALLOWED = "allowed"
    DENIED = "denied"
    FAILURE = "failure"


class RoleName(StrEnum):
    ADMIN = "ADMIN"
    TEAM_MANAGER = "TEAM_MANAGER"
    AGENT = "AGENT"
    READ_ONLY = "READ_ONLY"


def values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]
