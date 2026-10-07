"""Memory-augmented agent runtime.

Task -> Agent -> permission-aware memory search -> relevant memories -> LLM
-> response -> (optional) store the result as a new memory.
"""
from __future__ import annotations

import re
import time
import uuid

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import AuditAction, record
from app.core.errors import ForbiddenError
from app.llm.providers import SYSTEM_TEMPLATE, ContextMemory, complete_with_fallback
from app.models.enums import AuditStatus, MemoryType, VerificationStatus
from app.schemas.common import CleanText
from app.schemas.memory import MemoryCreate, MemoryWarnings, SearchRequest, SearchResult
from app.security import permissions as P
from app.security.principal import Principal
from app.services.memory_service import MemoryService
from app.services.search_service import SearchService


class AgentTaskRequest(BaseModel):
    task: CleanText = Field(..., min_length=3, max_length=4000)
    top_k: int = Field(5, ge=1, le=20)
    similarity_threshold: float | None = Field(None, ge=0, le=1)
    store_result: bool = False
    result_memory_type: MemoryType = MemoryType.TASK_RESULT
    project_id: uuid.UUID | None = None
    only_own: bool = Field(False, description="Isolated-memory mode: only the agent's own memories are searched")


class AgentStep(BaseModel):
    name: str
    detail: str
    took_ms: float


class AgentTaskResponse(BaseModel):
    task_id: str
    agent: str
    team: str | None
    task: str
    answer: str
    learning: str | None
    provider: str
    model: str | None
    fallback_reason: str | None
    memories_used: list[SearchResult]
    reused_cross_team: int
    stored_memory_id: uuid.UUID | None
    store_warnings: MemoryWarnings | None
    steps: list[AgentStep]
    took_ms: float


_LEARNING = re.compile(r"^\s*LEARNING:\s*(.+)$", re.I | re.M)


class AgentRuntime:
    def __init__(self, db: Session):
        self.db = db
        self.memories = MemoryService(db)
        self.search = SearchService(db, self.memories)

    def run(self, principal: Principal, req: AgentTaskRequest) -> AgentTaskResponse:
        if principal.agent is None:
            raise ForbiddenError("Only agents (or administrators simulating an agent) can run agent tasks")
        started = time.perf_counter()
        task_id = uuid.uuid4().hex[:12]
        steps: list[AgentStep] = []

        t0 = time.perf_counter()
        search = self.search.search(
            principal,
            SearchRequest(query=req.task, top_k=req.top_k, similarity_threshold=req.similarity_threshold,
                          project_ids=[req.project_id] if req.project_id else None, only_own=req.only_own),
        )
        steps.append(AgentStep(name="memory_search", detail=f"{search.total} authorised memories retrieved ({search.mode})", took_ms=round((time.perf_counter() - t0) * 1000, 2)))

        context = [
            ContextMemory(
                ref=f"M{i + 1}", title=r.title, content=r.content, team=r.team, memory_type=r.memory_type.value,
                similarity=r.similarity, confidence=r.confidence, verified=r.verification_status == VerificationStatus.VERIFIED,
            )
            for i, r in enumerate(search.results)
        ]
        agent = principal.agent
        system = SYSTEM_TEMPLATE.format(agent_name=agent.name, team=agent.team.name, persona=agent.persona or "")

        t0 = time.perf_counter()
        result = complete_with_fallback(system, req.task, context)
        steps.append(AgentStep(name="reasoning", detail=f"provider={result.provider} model={result.model or '-'}", took_ms=round((time.perf_counter() - t0) * 1000, 2)))

        match = _LEARNING.search(result.text)
        learning = match.group(1).strip() if match else None

        stored_id, store_warnings = None, None
        if req.store_result:
            if not principal.has(P.MEMORY_CREATE):
                raise ForbiddenError(f"Missing permission: {P.MEMORY_CREATE}")
            t0 = time.perf_counter()
            memory, store_warnings = self.memories.create(
                principal,
                MemoryCreate(
                    title=f"Task result: {req.task[:180]}".replace("\n", " "),
                    content=result.text[:20000],
                    memory_type=req.result_memory_type,
                    tags=["agent-task", result.provider],
                    source="llm-agent",
                    source_ref=f"agent-task:{task_id}",
                    confidence=0.6,
                    importance=0.5,
                    project_id=req.project_id,
                    parent_memory_id=search.results[0].memory_id if search.results else None,
                    metadata={"used_memory_ids": [str(r.memory_id) for r in search.results], "provider": result.provider, "model": result.model},
                ),
            )
            stored_id = memory.id
            steps.append(AgentStep(name="store_result", detail=f"stored memory {memory.id}", took_ms=round((time.perf_counter() - t0) * 1000, 2)))

        took_ms = round((time.perf_counter() - started) * 1000, 2)
        cross = sum(r.cross_team for r in search.results)
        record(
            AuditAction.AGENT_TASK_RUN, AuditStatus.SUCCESS, principal, resource_type="agent_task", resource_id=task_id,
            details={"task": req.task[:200], "memories_used": [str(r.memory_id) for r in search.results], "cross_team": cross, "provider": result.provider, "stored_memory_id": stored_id, "took_ms": took_ms},
        )
        return AgentTaskResponse(
            task_id=task_id, agent=agent.name, team=agent.team.name, task=req.task, answer=result.text, learning=learning,
            provider=result.provider, model=result.model, fallback_reason=result.fallback_reason, memories_used=search.results,
            reused_cross_team=cross, stored_memory_id=stored_id, store_warnings=store_warnings, steps=steps, took_ms=took_ms,
        )
