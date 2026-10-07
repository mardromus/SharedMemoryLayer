from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal, rate_limited, require_permission
from app.core.database import get_db
from app.models.enums import MemoryStatus, MemoryType, VerificationStatus, Visibility
from app.schemas.common import Page, Ref
from app.schemas.memory import (
    BulkCreateRequest,
    BulkCreateResponse,
    FeedbackCreate,
    FeedbackOut,
    MemoryCheckRequest,
    MemoryCreate,
    MemoryCreateResponse,
    MemoryListItem,
    MemoryOut,
    MemoryUpdate,
    MemoryWarnings,
    ProvenanceOut,
    SearchRequest,
    SearchResponse,
    ShareRequest,
    ShareResponse,
    VerifyRequest,
    VersionOut,
)
from app.security import permissions as P
from app.security.principal import Principal
from app.services.memory_service import MemoryService
from app.services.search_service import SearchService
from app.services.serializers import memory_out, version_out

router = APIRouter(prefix="/api/memories", tags=["Memories"])


def _share_response(memory) -> ShareResponse:
    return ShareResponse(
        memory_id=memory.id,
        visibility=memory.visibility,
        allowed_teams=[Ref(id=g.team.id, name=g.team.name) for g in memory.access_grants if g.team is not None],
        allowed_agents=[Ref(id=g.agent.id, name=g.agent.name) for g in memory.access_grants if g.agent is not None],
    )


@router.post("", response_model=MemoryCreateResponse, status_code=201,
             dependencies=[Depends(rate_limited("write"))])
def create_memory(data: MemoryCreate, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.MEMORY_CREATE))):
    """Validate -> authorise -> embed -> duplicate/conflict detection -> store (PostgreSQL + Qdrant)
    -> version 1 -> audit. Potential duplicates/conflicts are returned as warnings."""
    memory, warnings = MemoryService(db).create(principal, data)
    return MemoryCreateResponse(memory=memory_out(memory, principal), warnings=warnings)


@router.get("", response_model=Page[MemoryListItem])
def list_memories(
    q: str | None = Query(None, max_length=200, description="Keyword filter (title, content, tags)"),
    memory_type: MemoryType | None = None,
    team_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    visibility: Visibility | None = None,
    status: MemoryStatus | None = None,
    verification_status: VerificationStatus | None = None,
    conflict_status: str | None = Query(None, pattern="^(none|potential_conflict|resolved)$"),
    tag: str | None = Query(None, max_length=50),
    include_expired: bool = False,
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(P.MEMORY_READ)),
):
    """Paginated listing / keyword search. Only memories the caller may read are returned."""
    return MemoryService(db).list(
        principal, q=q, memory_type=memory_type, team_id=team_id, agent_id=agent_id, project_id=project_id,
        visibility=visibility, status=status, verification_status=verification_status, conflict_status=conflict_status,
        tag=tag, include_expired=include_expired, page=page, page_size=page_size,
    )


@router.post("/bulk", response_model=BulkCreateResponse, dependencies=[Depends(rate_limited("write"))])
def bulk_create(req: BulkCreateRequest, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.MEMORY_CREATE))):
    """Import up to 100 memories in one call (existing docs, runbooks, FAQs). Every item goes through
    the normal pipeline (validation, authorisation, embedding, duplicate/conflict detection, audit)."""
    return MemoryService(db).bulk_create(principal, req)


@router.post("/search", response_model=SearchResponse, dependencies=[Depends(rate_limited("search"))])
def search_memories(req: SearchRequest, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.MEMORY_READ))):
    """Semantic search: embedding -> Qdrant similarity search with permission + metadata
    filters -> authoritative re-check -> ranking -> top-k."""
    return SearchService(db).search(principal, req)


@router.post("/check", response_model=MemoryWarnings, dependencies=[Depends(rate_limited("search"))])
def check_memory(req: MemoryCheckRequest, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.MEMORY_READ))):
    """Pre-flight duplicate / conflict check without storing anything."""
    return MemoryService(db).check(principal, req.title, req.content, req.tags)


@router.get("/{memory_id}", response_model=MemoryOut)
def get_memory(memory_id: uuid.UUID, include_expired: bool = False, db: Session = Depends(get_db),
               principal: Principal = Depends(require_permission(P.MEMORY_READ))):
    return memory_out(MemoryService(db).get(principal, memory_id, include_expired=include_expired), principal)


@router.patch("/{memory_id}", response_model=MemoryCreateResponse, dependencies=[Depends(rate_limited("write"))])
def update_memory(memory_id: uuid.UUID, data: MemoryUpdate, db: Session = Depends(get_db),
                  principal: Principal = Depends(require_permission(P.MEMORY_UPDATE))):
    """Every update creates a new immutable version; the vector is re-embedded when text changes."""
    memory, warnings = MemoryService(db).update(principal, memory_id, data)
    return MemoryCreateResponse(memory=memory_out(memory, principal), warnings=warnings)


@router.delete("/{memory_id}", status_code=204, response_class=Response)
def delete_memory(memory_id: uuid.UUID, permanent: bool = False, db: Session = Depends(get_db),
                  principal: Principal = Depends(require_permission(P.MEMORY_DELETE))):
    """Archive a memory (default). ``permanent=true`` (administrators only) removes it from both stores."""
    svc = MemoryService(db)
    if permanent:
        svc.delete_permanently(principal, memory_id)
    else:
        svc.archive(principal, memory_id)
    return Response(status_code=204)


@router.post("/{memory_id}/restore", response_model=MemoryOut)
def restore_memory(memory_id: uuid.UUID, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.MEMORY_DELETE))):
    return memory_out(MemoryService(db).restore(principal, memory_id), principal)


@router.get("/{memory_id}/versions", response_model=list[VersionOut])
def list_versions(memory_id: uuid.UUID, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.MEMORY_READ))):
    memory, versions = MemoryService(db).versions(principal, memory_id)
    return [version_out(v, memory.current_version) for v in versions]


@router.get("/{memory_id}/versions/{version}", response_model=VersionOut)
def get_version(memory_id: uuid.UUID, version: int, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.MEMORY_READ))):
    memory, row = MemoryService(db).version(principal, memory_id, version)
    return version_out(row, memory.current_version)


@router.get("/{memory_id}/provenance", response_model=ProvenanceOut)
def get_provenance(memory_id: uuid.UUID, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.MEMORY_READ))):
    return MemoryService(db).provenance(principal, memory_id)


@router.post("/{memory_id}/share", response_model=ShareResponse)
def share_memory(memory_id: uuid.UUID, data: ShareRequest, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.MEMORY_SHARE))):
    return _share_response(MemoryService(db).share(principal, memory_id, data))


@router.delete("/{memory_id}/share/{target}", response_model=ShareResponse)
def unshare_memory(memory_id: uuid.UUID, target: str, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.MEMORY_SHARE))):
    """``target`` is ``team:<uuid>``, ``agent:<uuid>`` or a bare uuid."""
    return _share_response(MemoryService(db).unshare(principal, memory_id, target))


@router.post("/{memory_id}/feedback", response_model=FeedbackOut, status_code=201)
def memory_feedback(memory_id: uuid.UUID, data: FeedbackCreate, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.MEMORY_READ))):
    fb, reliability = MemoryService(db).feedback(principal, memory_id, data)
    return FeedbackOut(id=fb.id, memory_id=fb.memory_id, rating=fb.rating, helpful=fb.helpful, comment=fb.comment, created_at=fb.created_at, reliability=reliability)


@router.post("/{memory_id}/verify", response_model=MemoryOut)
def verify_memory(memory_id: uuid.UUID, data: VerifyRequest, db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    return memory_out(MemoryService(db).verify(principal, memory_id, data), principal)
