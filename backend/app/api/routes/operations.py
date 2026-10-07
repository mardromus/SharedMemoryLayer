"""Dashboard, conflicts, agent runtime, playground, admin maintenance and health."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime, AgentTaskRequest, AgentTaskResponse
from app.api.deps import get_current_principal, rate_limited, require_admin, require_permission, require_user
from app.audit import AuditAction, record
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import AppError, ForbiddenError
from app.models.enums import AuditStatus, ConflictRecordStatus
from app.models.memory import Memory
from app.schemas.memory import (
    ConflictOut,
    ConflictResolveRequest,
    MemoryCreate,
    MemoryCreateResponse,
    MemoryOut,
    SearchRequest,
    SearchResponse,
)
from app.security import permissions as P
from app.security.access import memory_payload
from app.security.principal import Principal
from app.services.conflict_service import ConflictService
from app.services.dashboard_service import dashboard_stats, system_health
from app.services.identity_service import IdentityService
from app.services.memory_service import MemoryService
from app.services.search_service import SearchService
from app.services.serializers import memory_out
from app.vector.embeddings import embedding_text, get_embedder

router = APIRouter()


# ---------------------------------------------------------------- dashboard
@router.get("/api/dashboard/stats", tags=["Dashboard"])
def get_dashboard_stats(db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    return dashboard_stats(db, principal)


# ---------------------------------------------------------------- conflicts
@router.get("/api/conflicts", response_model=list[ConflictOut], tags=["Conflicts"])
def list_conflicts(status: ConflictRecordStatus | None = None, db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    return ConflictService(db).list(principal, status)


@router.post("/api/conflicts/{conflict_id}/resolve", response_model=ConflictOut, tags=["Conflicts"])
def resolve_conflict(conflict_id: uuid.UUID, req: ConflictResolveRequest, db: Session = Depends(get_db),
                     principal: Principal = Depends(require_permission(P.CONFLICT_RESOLVE))):
    return ConflictService(db).resolve(principal, conflict_id, req)


# ---------------------------------------------------------------- agent runtime
@router.post("/api/agent-runtime/tasks", response_model=AgentTaskResponse, tags=["Agent Runtime"],
             dependencies=[Depends(rate_limited("search"))])
def run_agent_task(req: AgentTaskRequest, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.MEMORY_READ))):
    """Receive a task -> search shared memory -> use memories as LLM context -> answer
    -> optionally store the result as a new memory."""
    return AgentRuntime(db).run(principal, req)


# ---------------------------------------------------------------- playground (humans simulating agents)
class PlaygroundBase(BaseModel):
    agent_id: uuid.UUID


class PlaygroundSearch(PlaygroundBase):
    search: SearchRequest


class PlaygroundTask(PlaygroundBase):
    task: AgentTaskRequest


class PlaygroundCreate(PlaygroundBase):
    memory: MemoryCreate


class PlaygroundAccess(PlaygroundBase):
    memory_id: uuid.UUID


class AccessCheckResult(BaseModel):
    agent: str
    agent_team: str
    memory_id: uuid.UUID
    decision: str = Field(..., description="ALLOWED or DENIED")
    reason: str
    memory: MemoryOut | None
    audit_logged: bool = True


def _simulate(db: Session, user: Principal, agent_id: uuid.UUID) -> Principal:
    """Build the agent's principal. The action is authorised with the *agent's*
    permissions (never the human's) and audited as the agent, on behalf of the user."""
    agent = IdentityService(db).get_agent(agent_id)
    if not user.manages_team(agent.team_id):
        record(AuditAction.ACCESS_DENIED, AuditStatus.DENIED, user, resource_type="agent", resource_id=agent.id, reason="Cannot simulate an agent outside your team")
        raise ForbiddenError("You can only simulate agents of your own team")
    principal = Principal.from_agent(agent)
    principal.on_behalf_of = user.name
    principal.ip_address, principal.user_agent = user.ip_address, user.user_agent
    return principal


@router.post("/api/playground/search", response_model=SearchResponse, tags=["Agent Playground"], dependencies=[Depends(rate_limited("search"))])
def playground_search(req: PlaygroundSearch, db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    agent = _simulate(db, user, req.agent_id)
    if not agent.has(P.MEMORY_READ):
        raise ForbiddenError(f"Agent lacks permission {P.MEMORY_READ}")
    return SearchService(db).search(agent, req.search)


@router.post("/api/playground/tasks", response_model=AgentTaskResponse, tags=["Agent Playground"], dependencies=[Depends(rate_limited("search"))])
def playground_task(req: PlaygroundTask, db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    agent = _simulate(db, user, req.agent_id)
    if not agent.has(P.MEMORY_READ):
        raise ForbiddenError(f"Agent lacks permission {P.MEMORY_READ}")
    return AgentRuntime(db).run(agent, req.task)


@router.post("/api/playground/memories", response_model=MemoryCreateResponse, status_code=201, tags=["Agent Playground"], dependencies=[Depends(rate_limited("write"))])
def playground_create(req: PlaygroundCreate, db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    agent = _simulate(db, user, req.agent_id)
    if not agent.has(P.MEMORY_CREATE):
        record(AuditAction.MEMORY_CREATE, AuditStatus.DENIED, agent, resource_type="memory", reason=f"Missing permission {P.MEMORY_CREATE}")
        raise ForbiddenError(f"Agent lacks permission {P.MEMORY_CREATE}")
    memory, warnings = MemoryService(db).create(agent, req.memory)
    return MemoryCreateResponse(memory=memory_out(memory, agent), warnings=warnings)


@router.post("/api/playground/access-check", response_model=AccessCheckResult, tags=["Agent Playground"])
def playground_access(req: PlaygroundAccess, db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    """Have the simulated agent try to open a memory; the decision is enforced and audited."""
    agent = _simulate(db, user, req.agent_id)
    try:
        memory = MemoryService(db).get(agent, req.memory_id, include_expired=True)
        return AccessCheckResult(agent=agent.name, agent_team=agent.agent.team.name, memory_id=req.memory_id,
                                 decision="ALLOWED", reason="authorised", memory=memory_out(memory, agent))
    except ForbiddenError as exc:
        return AccessCheckResult(agent=agent.name, agent_team=agent.agent.team.name, memory_id=req.memory_id,
                                 decision="DENIED", reason=exc.message.removeprefix("Access denied: "), memory=None)


# ---------------------------------------------------------------- admin maintenance
@router.post("/api/admin/reindex", tags=["Administration"])
def reindex(recreate: bool = Query(False, description="Drop and recreate the collection first"), db: Session = Depends(get_db),
            principal: Principal = Depends(require_admin)):
    """Rebuild the vector index from PostgreSQL (recovery after vector-store data loss or an embedding-model change)."""
    started = time.perf_counter()
    embedder = get_embedder()
    svc = MemoryService(db)
    if recreate:
        svc.store.reset(embedder.dimension)
    memories = list(db.scalars(select(Memory)))
    batch = 32
    for i in range(0, len(memories), batch):
        chunk = memories[i : i + batch]
        vectors = embedder.embed([embedding_text(m.title, m.content, m.tag_names) for m in chunk])
        for memory, vector in zip(chunk, vectors):
            svc.store.upsert(str(memory.id), vector, memory_payload(memory))
            memory.embedding_id, memory.embedding_model = str(memory.id), embedder.model_name
    db.commit()
    took = round(time.perf_counter() - started, 2)
    record(AuditAction.SYSTEM_REINDEX, AuditStatus.SUCCESS, principal, details={"memories": len(memories), "recreate": recreate, "seconds": took})
    return {"reindexed": len(memories), "seconds": took}


@router.get("/api/admin/consistency", tags=["Administration"])
def consistency(db: Session = Depends(get_db), _: Principal = Depends(require_admin)):
    """Compare PostgreSQL (system of record) with the vector index."""
    store = MemoryService(db).store
    ids = [str(i) for i in db.scalars(select(Memory.id))]
    missing = [i for i in ids if not store.exists(i)]
    return {"postgres_memories": len(ids), "vector_points": store.count(), "missing_vectors": missing}


@router.get("/api/admin/config", tags=["Administration"])
def config(_: Principal = Depends(require_admin)):
    """Non-secret runtime configuration."""
    s = get_settings()
    return {
        "environment": s.environment,
        "embedding": {"provider": s.embedding_provider, "model": s.embedding_model},
        "llm": {"provider": s.llm_provider, "model": s.effective_llm_model},
        "search": {"default_top_k": s.search_default_top_k, "similarity_threshold": s.search_similarity_threshold,
                   "duplicate_threshold": s.duplicate_similarity_threshold, "conflict_threshold": s.conflict_similarity_threshold},
        "rate_limits_per_minute": {"enabled": s.rate_limit_enabled, "auth": s.rate_limit_auth_per_minute,
                                   "search": s.rate_limit_search_per_minute, "write": s.rate_limit_write_per_minute},
        "auth": {"access_token_minutes": s.jwt_expiration, "refresh_token_days": s.refresh_token_expiration_days,
                 "public_registration": s.allow_public_registration, "max_failed_logins": s.max_failed_logins},
        "limits": {"max_content_length": s.max_memory_content_length},
    }


# ---------------------------------------------------------------- health & metrics
@router.get("/health", tags=["Health"])
def health(response: Response):
    status = system_health()
    healthy = status["database"] == "up" and status["vector_db"] == "up"
    response.status_code = 200 if healthy else 503
    return {"status": "ok" if healthy else "degraded", "version": get_settings().app_version, "components": status}


@router.get("/health/live", tags=["Health"])
def live():
    return {"status": "alive"}


@router.get("/metrics", tags=["Health"], include_in_schema=False)
def metrics(request: Request):
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
