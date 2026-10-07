"""Memory access-control policy.

This module is the single source of truth for "who may see / change which
memory". The same rules are expressed twice:

* ``can_read`` / ``can_modify`` evaluate a concrete Memory row (authoritative).
* ``build_read_filter`` expresses the read rule as a vector-store filter so
  that semantic search never even *retrieves* candidates the caller cannot
  see. Search results are re-checked with ``can_read`` afterwards
  (defence in depth).

Read rule (non-admin):
    creator agent                                         -> allowed
    visibility == ORGANIZATION                            -> allowed
    visibility != PRIVATE and one of:
        visibility in {TEAM, SELECTED_TEAMS} and caller's team owns it
        caller's team has an explicit sharing grant
        caller (agent) has an explicit sharing grant
    otherwise                                             -> denied
Archived memories are visible to administrators only.
Expired memories are hidden unless explicitly requested.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import ColumnElement, and_, exists, or_, true

from app.models.enums import MemoryStatus, Visibility
from app.models.memory import Memory, MemoryAccess
from app.security import permissions as P
from app.security.principal import Principal
from app.vector.base import FieldMatch, VectorFilter


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


ALLOW = AccessDecision(True, "authorised")


def _is_expired(memory: Memory, now: datetime | None = None) -> bool:
    if memory.expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return memory.expires_at <= now


def can_read(principal: Principal, memory: Memory, include_expired: bool = False) -> AccessDecision:
    if not principal.has(P.MEMORY_READ):
        return AccessDecision(False, f"Missing permission {P.MEMORY_READ}")
    if principal.is_admin:
        return ALLOW
    if memory.status == MemoryStatus.ARCHIVED:
        return AccessDecision(False, "Memory is archived (administrators only)")
    if _is_expired(memory) and not include_expired:
        return AccessDecision(False, "Memory has expired")
    if principal.agent_id is not None and memory.creator_agent_id == principal.agent_id:
        return ALLOW
    if principal.user_id is not None and memory.creator_user_id == principal.user_id:
        return ALLOW

    visibility = memory.visibility
    if visibility == Visibility.ORGANIZATION:
        return ALLOW
    if visibility == Visibility.PRIVATE:
        return AccessDecision(False, "Memory is private to its creator")

    same_team = principal.team_id is not None and principal.team_id == memory.team_id
    if same_team and visibility in (Visibility.TEAM, Visibility.SELECTED_TEAMS):
        return ALLOW
    if principal.team_id is not None and principal.team_id in memory.allowed_team_ids:
        return ALLOW
    if principal.agent_id is not None and principal.agent_id in memory.allowed_agent_ids:
        return ALLOW

    if visibility == Visibility.SELECTED_AGENTS:
        return AccessDecision(False, "Agent is not in the memory's allowed agents list")
    if visibility == Visibility.SELECTED_TEAMS:
        return AccessDecision(False, "Team is not in the memory's allowed teams list")
    return AccessDecision(False, "Unauthorized team")


def can_modify(principal: Principal, memory: Memory, permission: str) -> AccessDecision:
    """Update / delete / share rule: owner (creator) or managers of the owning team."""
    if not principal.has(permission):
        return AccessDecision(False, f"Missing permission {permission}")
    if principal.is_admin:
        return ALLOW
    if memory.status == MemoryStatus.ARCHIVED:
        return AccessDecision(False, "Memory is archived (administrators only)")
    if principal.manages_team(memory.team_id):
        return ALLOW
    if principal.agent_id is not None and memory.creator_agent_id == principal.agent_id:
        return ALLOW
    if principal.user_id is not None and memory.creator_user_id == principal.user_id:
        return ALLOW
    if principal.team_id is not None and principal.team_id != memory.team_id:
        return AccessDecision(False, "Unauthorized team")
    return AccessDecision(False, "Only the creator or a manager of the owning team may modify this memory")


def can_view_sensitive_fields(principal: Principal, memory: Memory) -> bool:
    """Owner-side fields (sharing lists, metadata, source refs) are hidden from cross-team readers."""
    if principal.is_admin or principal.manages_team(memory.team_id):
        return True
    if principal.agent_id is not None and memory.creator_agent_id == principal.agent_id:
        return True
    if principal.user_id is not None and memory.creator_user_id == principal.user_id:
        return True
    return principal.team_id is not None and principal.team_id == memory.team_id


def build_read_filter(
    principal: Principal,
    include_archived: bool = False,
    include_expired: bool = False,
    now: datetime | None = None,
) -> VectorFilter:
    """Translate ``can_read`` into a vector-store filter (see module docstring)."""
    now = now or datetime.now(timezone.utc)
    vf = VectorFilter()

    if not (principal.is_admin and include_archived):
        vf.must.append(FieldMatch("status", MemoryStatus.ACTIVE.value))
    if not include_expired:
        vf.range_gt["expires_at_ts"] = now.timestamp()

    if principal.is_admin:
        return vf

    clauses: list[list[FieldMatch]] = [[FieldMatch("visibility", Visibility.ORGANIZATION.value)]]
    if principal.agent_id is not None:
        clauses.append([FieldMatch("creator_agent_id", str(principal.agent_id))])
    if principal.user_id is not None:
        clauses.append([FieldMatch("creator_user_id", str(principal.user_id))])
    if principal.team_id is not None:
        team = str(principal.team_id)
        clauses.append(
            [
                FieldMatch("team_id", team),
                FieldMatch("visibility", [Visibility.TEAM.value, Visibility.SELECTED_TEAMS.value]),
            ]
        )
        clauses.append([FieldMatch("allowed_team_ids", team), FieldMatch("is_private", False)])
    if principal.agent_id is not None:
        clauses.append([FieldMatch("allowed_agent_ids", str(principal.agent_id)), FieldMatch("is_private", False)])
    vf.any_of = clauses
    return vf


def sql_read_filter(
    principal: Principal,
    include_archived: bool = False,
    include_expired: bool = False,
    now: datetime | None = None,
) -> ColumnElement[bool]:
    """Translate ``can_read`` into a SQL WHERE clause (keyword search / listings)."""
    now = now or datetime.now(timezone.utc)
    conditions: list[ColumnElement[bool]] = []
    if not (principal.is_admin and include_archived):
        conditions.append(Memory.status == MemoryStatus.ACTIVE)
    if not include_expired:
        conditions.append(or_(Memory.expires_at.is_(None), Memory.expires_at > now))
    if principal.is_admin:
        return and_(true(), *conditions)

    access: list[ColumnElement[bool]] = [Memory.visibility == Visibility.ORGANIZATION]
    not_private = Memory.visibility != Visibility.PRIVATE
    if principal.agent_id is not None:
        access.append(Memory.creator_agent_id == principal.agent_id)
        access.append(
            and_(
                not_private,
                exists().where(MemoryAccess.memory_id == Memory.id, MemoryAccess.agent_id == principal.agent_id),
            )
        )
    if principal.user_id is not None:
        access.append(Memory.creator_user_id == principal.user_id)
    if principal.team_id is not None:
        access.append(
            and_(
                Memory.team_id == principal.team_id,
                Memory.visibility.in_([Visibility.TEAM, Visibility.SELECTED_TEAMS]),
            )
        )
        access.append(
            and_(
                not_private,
                exists().where(MemoryAccess.memory_id == Memory.id, MemoryAccess.team_id == principal.team_id),
            )
        )
    conditions.append(or_(*access))
    return and_(*conditions)


def memory_payload(memory: Memory) -> dict:
    """Payload stored next to the vector; mirrors the fields ``build_read_filter`` needs."""
    far_future = 32503680000.0  # 3000-01-01, stands in for "never expires"
    return {
        "memory_id": str(memory.id),
        "team_id": str(memory.team_id),
        "creator_agent_id": str(memory.creator_agent_id) if memory.creator_agent_id else None,
        "creator_user_id": str(memory.creator_user_id) if memory.creator_user_id else None,
        "visibility": memory.visibility.value,
        "is_private": memory.visibility == Visibility.PRIVATE,
        "allowed_team_ids": [str(t) for t in memory.allowed_team_ids],
        "allowed_agent_ids": [str(a) for a in memory.allowed_agent_ids],
        "status": memory.status.value,
        "memory_type": memory.memory_type.value,
        "project_id": str(memory.project_id) if memory.project_id else None,
        "tags": memory.tag_names,
        "verification_status": memory.verification_status.value,
        "created_at_ts": memory.created_at.timestamp(),
        "expires_at_ts": memory.expires_at.timestamp() if memory.expires_at else far_future,
        "confidence": memory.confidence,
        "importance": memory.importance,
        "version": memory.current_version,
    }


def ids_to_str(ids: list[uuid.UUID] | None) -> list[str] | None:
    return [str(i) for i in ids] if ids else None
