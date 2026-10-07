"""Convert ORM rows to API schemas, applying field-level authorisation."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.memory import Memory, MemoryVersion
from app.schemas.common import Ref
from app.schemas.memory import MemoryListItem, MemoryOut, VersionAuthor, VersionOut
from app.security import permissions as P
from app.security.access import can_modify, can_view_sensitive_fields
from app.security.principal import Principal


def _ref(obj, attr: str = "name") -> Ref | None:
    return Ref(id=obj.id, name=getattr(obj, attr)) if obj is not None else None


def is_expired(memory: Memory) -> bool:
    return memory.expires_at is not None and memory.expires_at <= datetime.now(timezone.utc)


def creator_name(memory: Memory) -> str:
    if memory.creator_agent is not None:
        return memory.creator_agent.name
    if memory.creator_user is not None:
        return memory.creator_user.username
    return "unknown"


def memory_out(memory: Memory, principal: Principal) -> MemoryOut:
    full = can_view_sensitive_fields(principal, memory)
    out = MemoryOut(
        id=memory.id,
        title=memory.title,
        content=memory.content,
        memory_type=memory.memory_type,
        team=Ref(id=memory.team.id, name=memory.team.name),
        creator_agent=_ref(memory.creator_agent),
        creator_user=_ref(memory.creator_user, "username"),
        project=_ref(memory.project),
        visibility=memory.visibility,
        sensitivity=memory.sensitivity,
        status=memory.status,
        verification_status=memory.verification_status,
        conflict_status=memory.conflict_status,
        tags=memory.tag_names,
        importance=memory.importance,
        confidence=memory.confidence,
        reliability=memory.reliability,
        source=memory.source,
        expires_at=memory.expires_at,
        is_expired=is_expired(memory),
        parent_memory_id=memory.parent_memory_id,
        version=memory.current_version,
        access_count=memory.access_count,
        reuse_count=memory.reuse_count,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        archived_at=memory.archived_at,
        can_edit=bool(can_modify(principal, memory, P.MEMORY_UPDATE)),
        can_share=bool(can_modify(principal, memory, P.MEMORY_SHARE)),
    )
    if full:
        # Owner-side details: never exposed to cross-team readers.
        out.source_ref = memory.source_ref
        out.metadata = memory.extra
        out.embedding_id = memory.embedding_id
        out.embedding_model = memory.embedding_model
        out.allowed_teams = [Ref(id=g.team.id, name=g.team.name) for g in memory.access_grants if g.team is not None]
        out.allowed_agents = [Ref(id=g.agent.id, name=g.agent.name) for g in memory.access_grants if g.agent is not None]
    return out


def memory_list_item(memory: Memory) -> MemoryListItem:
    snippet = memory.content if len(memory.content) <= 240 else memory.content[:237].rstrip() + "..."
    return MemoryListItem(
        id=memory.id,
        title=memory.title,
        snippet=snippet,
        memory_type=memory.memory_type,
        team=Ref(id=memory.team.id, name=memory.team.name),
        creator=creator_name(memory),
        visibility=memory.visibility,
        sensitivity=memory.sensitivity,
        status=memory.status,
        verification_status=memory.verification_status,
        conflict_status=memory.conflict_status,
        tags=memory.tag_names,
        confidence=memory.confidence,
        importance=memory.importance,
        version=memory.current_version,
        expires_at=memory.expires_at,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


def version_author(version: MemoryVersion) -> VersionAuthor:
    if version.author_agent is not None:
        return VersionAuthor(type="agent", id=version.author_agent.id, name=version.author_agent.name)
    if version.author_user is not None:
        return VersionAuthor(type="user", id=version.author_user.id, name=version.author_user.username)
    return VersionAuthor(type="unknown", id=None, name="unknown")


def version_out(version: MemoryVersion, current_version: int) -> VersionOut:
    return VersionOut(
        id=version.id,
        memory_id=version.memory_id,
        version_number=version.version_number,
        previous_version_id=version.previous_version_id,
        title=version.title,
        content=version.content,
        memory_type=version.memory_type,
        visibility=version.visibility,
        tags=list(version.tags or []),
        importance=version.importance,
        confidence=version.confidence,
        change_description=version.change_description,
        author=version_author(version),
        created_at=version.created_at,
        is_current=version.version_number == current_version,
    )
