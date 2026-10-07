"""Memory lifecycle service.

CREATE -> VALIDATE -> EMBED -> STORE (PostgreSQL + Qdrant) -> RETRIEVE -> UPDATE
-> VERSION -> ARCHIVE

PostgreSQL is the system of record; Qdrant holds one vector per memory
(point id == memory id) with the payload needed for permission-aware
filtering. Writes go: DB flush -> vector upsert -> DB commit, with a
compensating vector delete / re-sync if the commit fails, so the two stores
cannot silently diverge.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditAction, record
from app.core.config import get_settings
from app.core.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationFailed,
)
from app.models.audit import AuditLog
from app.models.enums import (
    AgentStatus,
    AuditStatus,
    ConflictStatus,
    MemoryStatus,
    Sensitivity,
    VerificationStatus,
    Visibility,
)
from app.models.identity import Agent, Team
from app.models.memory import (
    Memory,
    MemoryAccess,
    MemoryConflict,
    MemoryFeedback,
    MemoryVersion,
    Project,
    Tag,
)
from app.schemas.common import Page
from app.schemas.memory import (
    AccessEvent,
    BulkCreateRequest,
    BulkCreateResponse,
    BulkItemResult,
    ConflictCandidate,
    DuplicateCandidate,
    FeedbackCreate,
    MemoryCreate,
    MemoryListItem,
    MemoryUpdate,
    MemoryWarnings,
    ProvenanceOut,
    ShareRequest,
    VerifyRequest,
)
from app.security import permissions as P
from app.security.access import (
    AccessDecision,
    build_read_filter,
    can_modify,
    can_read,
    can_view_sensitive_fields,
    memory_payload,
    sql_read_filter,
)
from app.security.principal import Principal
from app.services.conflict_rules import detect_contradiction
from app.services.serializers import memory_list_item, version_author, version_out
from app.vector import get_vector_store
from app.vector.base import FieldMatch, VectorFilter, VectorStore, VectorStoreError
from app.vector.embeddings import EmbeddingProvider, embedding_text, get_embedder

logger = logging.getLogger(__name__)

RESOURCE = "memory"


@dataclass
class DetectionResult:
    duplicates: list[DuplicateCandidate]
    conflicts: list[tuple[Memory, float, str, str]]  # (existing, similarity, reason, method)


def content_hash(title: str, content: str) -> str:
    normalised = " ".join(f"{title}\n{content}".lower().split())
    return hashlib.sha256(normalised.encode()).hexdigest()


class MemoryService:
    def __init__(self, db: Session, embedder: EmbeddingProvider | None = None, store: VectorStore | None = None):
        self.db = db
        self.settings = get_settings()
        self._embedder = embedder
        self._store = store

    # ------------------------------------------------------------------ infra
    @property
    def embedder(self) -> EmbeddingProvider:
        if self._embedder is None:
            try:
                self._embedder = get_embedder()
            except Exception as exc:
                logger.exception("embedding model unavailable")
                raise ServiceUnavailableError("Embedding model is unavailable") from exc
        return self._embedder

    @property
    def store(self) -> VectorStore:
        return self._store or get_vector_store()

    def embed(self, text: str) -> list[float]:
        try:
            return self.embedder.embed([text])[0]
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            logger.exception("embedding failed")
            raise ServiceUnavailableError("Embedding generation failed") from exc

    # ------------------------------------------------------------------ helpers
    def _deny(self, principal: Principal, action: str, memory_id: uuid.UUID | None, decision: AccessDecision, details: dict | None = None) -> None:
        record(action, AuditStatus.DENIED, principal, resource_type=RESOURCE, resource_id=memory_id, reason=decision.reason, details=details)
        raise ForbiddenError(f"Access denied: {decision.reason}")

    def load(self, memory_id: uuid.UUID, for_update: bool = False) -> Memory:
        stmt = select(Memory).where(Memory.id == memory_id)
        if for_update:
            stmt = stmt.with_for_update(of=Memory)
        memory = self.db.scalar(stmt)
        if memory is None:
            raise NotFoundError("Memory not found")
        return memory

    def _tags(self, names: list[str]) -> list[Tag]:
        if not names:
            return []
        existing = {t.name: t for t in self.db.scalars(select(Tag).where(Tag.name.in_(names)))}
        tags: list[Tag] = []
        for name in names:
            tag = existing.get(name)
            if tag is None:
                try:
                    with self.db.begin_nested():
                        tag = Tag(name=name)
                        self.db.add(tag)
                except IntegrityError:  # created concurrently
                    tag = self.db.scalar(select(Tag).where(Tag.name == name))
            tags.append(tag)
        return tags

    def _resolve_team(self, principal: Principal, requested: uuid.UUID | None) -> Team:
        if principal.is_agent:
            team_id = principal.team_id
            if requested is not None and requested != team_id and not principal.is_admin:
                record(AuditAction.MEMORY_CREATE, AuditStatus.DENIED, principal, resource_type=RESOURCE, reason="Agent attempted to create a memory for another team", details={"team_id": requested})
                raise ForbiddenError("Agents can only create memories for their own team")
        else:
            team_id = requested or principal.team_id
            if team_id is None:
                raise ValidationFailed("team_id is required for users that do not belong to a team")
            if team_id != principal.team_id and not principal.is_admin:
                record(AuditAction.MEMORY_CREATE, AuditStatus.DENIED, principal, resource_type=RESOURCE, reason="Attempted to create a memory for another team", details={"team_id": team_id})
                raise ForbiddenError("You can only create memories for your own team")
        team = self.db.get(Team, team_id) if team_id else None
        if team is None or not team.is_active:
            raise NotFoundError("Team not found or inactive")
        return team

    def _validate_grants(
        self, principal: Principal, team: Team, visibility: Visibility, sensitivity: Sensitivity,
        team_ids: list[uuid.UUID], agent_ids: list[uuid.UUID],
    ) -> tuple[list[Team], list[Agent]]:
        if not team_ids and not agent_ids:
            return [], []
        if visibility == Visibility.PRIVATE:
            raise ConflictError("Private memories cannot be shared. Change the visibility first.")
        if not principal.has(P.MEMORY_SHARE):
            raise ForbiddenError(f"Missing permission: {P.MEMORY_SHARE}")
        if sensitivity == Sensitivity.RESTRICTED and not principal.manages_team(team.id):
            raise ForbiddenError("Restricted memories can only be shared by a team manager or administrator")
        teams = list(self.db.scalars(select(Team).where(Team.id.in_(team_ids)))) if team_ids else []
        if len(teams) != len(set(team_ids)):
            raise NotFoundError("One or more target teams do not exist")
        agents = list(self.db.scalars(select(Agent).where(Agent.id.in_(agent_ids), Agent.status != AgentStatus.DELETED))) if agent_ids else []
        if len(agents) != len(set(agent_ids)):
            raise NotFoundError("One or more target agents do not exist")
        external = [t for t in teams if t.id != team.id] + [a for a in agents if a.team_id != team.id]
        if external and not team.allow_cross_team_sharing and not principal.is_admin:
            raise ForbiddenError(f"Team {team.name} does not allow cross-team sharing")
        return teams, agents

    def _snapshot(self, memory: Memory) -> dict:
        return {
            "sensitivity": memory.sensitivity.value,
            "project_id": str(memory.project_id) if memory.project_id else None,
            "expires_at": memory.expires_at.isoformat() if memory.expires_at else None,
            "source": memory.source,
            "source_ref": memory.source_ref,
            "metadata": memory.extra,
            "verification_status": memory.verification_status.value,
        }

    def _add_version(self, memory: Memory, principal: Principal, description: str, previous: MemoryVersion | None) -> MemoryVersion:
        version = MemoryVersion(
            memory_id=memory.id,
            version_number=memory.current_version,
            previous_version_id=previous.id if previous else None,
            title=memory.title,
            content=memory.content,
            memory_type=memory.memory_type.value,
            visibility=memory.visibility.value,
            tags=memory.tag_names,
            importance=memory.importance,
            confidence=memory.confidence,
            snapshot=self._snapshot(memory),
            change_description=description,
            author_agent_id=principal.agent_id,
            author_user_id=principal.user_id,
        )
        self.db.add(version)
        return version

    def _system_filter(self) -> VectorFilter:
        vf = VectorFilter()
        vf.must.append(FieldMatch("status", MemoryStatus.ACTIVE.value))
        vf.range_gt["expires_at_ts"] = datetime.now(timezone.utc).timestamp()
        return vf

    # ------------------------------------------------------------------ duplicate / conflict detection
    def detect(
        self, principal: Principal, vector: list[float], text: str, chash: str, exclude_id: uuid.UUID | None = None
    ) -> DetectionResult:
        s = self.settings
        duplicates: dict[uuid.UUID, DuplicateCandidate] = {}

        exact_stmt = select(Memory).where(Memory.content_hash == chash, sql_read_filter(principal)).limit(5)
        if exclude_id:
            exact_stmt = exact_stmt.where(Memory.id != exclude_id)
        for m in self.db.scalars(exact_stmt):
            duplicates[m.id] = DuplicateCandidate(memory_id=m.id, title=m.title, similarity=1.0, team=m.team.name, exact_match=True)

        try:
            # Duplicates: only among memories the caller may read (no information leak).
            dup_hits = self.store.search(vector, build_read_filter(principal), limit=6, score_threshold=s.duplicate_similarity_threshold)
            # Conflicts: organisation-wide, so contradictions with memories the caller
            # cannot see are still flagged for administrator review.
            conflict_hits = self.store.search(vector, self._system_filter(), limit=10, score_threshold=s.conflict_similarity_threshold)
        except VectorStoreError as exc:
            raise ServiceUnavailableError("Vector database unavailable") from exc

        dup_ids = [uuid.UUID(h.id) for h in dup_hits if uuid.UUID(h.id) != exclude_id and uuid.UUID(h.id) not in duplicates]
        if dup_ids:
            rows = {m.id: m for m in self.db.scalars(select(Memory).where(Memory.id.in_(dup_ids)))}
            for hit in dup_hits:
                m = rows.get(uuid.UUID(hit.id))
                if m is not None and can_read(principal, m):
                    duplicates[m.id] = DuplicateCandidate(memory_id=m.id, title=m.title, similarity=round(hit.score, 4), team=m.team.name)

        conflicts: list[tuple[Memory, float, str, str]] = []
        cand_ids = [uuid.UUID(h.id) for h in conflict_hits if uuid.UUID(h.id) != exclude_id]
        if cand_ids:
            rows = {m.id: m for m in self.db.scalars(select(Memory).where(Memory.id.in_(cand_ids)))}
            for hit in conflict_hits:
                m = rows.get(uuid.UUID(hit.id))
                if m is None or m.content_hash == chash:
                    continue
                contradiction = detect_contradiction(text, f"{m.title}\n{m.content}")
                if contradiction:
                    conflicts.append((m, round(hit.score, 4), contradiction.reason, contradiction.method))
        return DetectionResult(sorted(duplicates.values(), key=lambda d: -d.similarity), conflicts)

    def _record_conflicts(self, principal: Principal, memory: Memory, detection: DetectionResult) -> list[ConflictCandidate]:
        visible: list[ConflictCandidate] = []
        for existing, similarity, reason, method in detection.conflicts:
            record_ = MemoryConflict(
                memory_id=memory.id,
                conflicting_memory_id=existing.id,
                similarity=similarity,
                reason=reason,
                detection_method=method,
            )
            self.db.add(record_)
            self.db.flush()
            if can_read(principal, existing):
                visible.append(
                    ConflictCandidate(
                        memory_id=existing.id, title=existing.title, similarity=similarity,
                        reason=reason, detection_method=method, conflict_id=record_.id,
                    )
                )
        if detection.conflicts:
            memory.conflict_status = ConflictStatus.POTENTIAL_CONFLICT
        return visible

    def _warnings(self, detection: DetectionResult, visible_conflicts: list[ConflictCandidate]) -> MemoryWarnings:
        messages = []
        if detection.duplicates:
            messages.append("Potential duplicate memories found.")
        if detection.conflicts:
            hidden = len(detection.conflicts) - len(visible_conflicts)
            msg = "Potential conflict with existing memories detected; flagged for review."
            if hidden:
                msg += f" ({hidden} involve memories you cannot access and were sent to an administrator.)"
            messages.append(msg)
        return MemoryWarnings(message=" ".join(messages) or None, duplicates=detection.duplicates, conflicts=visible_conflicts)

    def check(self, principal: Principal, title: str, content: str, tags: list[str]) -> MemoryWarnings:
        text = embedding_text(title, content, tags)
        detection = self.detect(principal, self.embed(text), text, content_hash(title, content))
        visible = [
            ConflictCandidate(memory_id=m.id, title=m.title, similarity=sim, reason=reason, detection_method=method)
            for m, sim, reason, method in detection.conflicts
            if can_read(principal, m)
        ]
        return self._warnings(detection, visible)

    # ------------------------------------------------------------------ create
    def create(self, principal: Principal, data: MemoryCreate, detect: bool = True) -> tuple[Memory, MemoryWarnings]:
        s = self.settings
        if len(data.content) > s.max_memory_content_length:
            raise ValidationFailed(f"content exceeds the maximum length of {s.max_memory_content_length} characters")
        if data.agent_id is not None and data.agent_id != principal.agent_id:
            record(AuditAction.MEMORY_CREATE, AuditStatus.DENIED, principal, resource_type=RESOURCE, reason="agent_id does not match the authenticated agent", details={"agent_id": data.agent_id})
            raise ForbiddenError("agent_id does not match the authenticated agent")

        team = self._resolve_team(principal, data.team_id)
        visibility = data.visibility or team.default_visibility
        if visibility == Visibility.ORGANIZATION and data.sensitivity in (Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED):
            raise ValidationFailed("confidential/restricted memories cannot be visible to the whole organization")
        if data.project_id is not None and self.db.get(Project, data.project_id) is None:
            raise NotFoundError("Project not found")
        if data.parent_memory_id is not None:
            parent = self.db.get(Memory, data.parent_memory_id)
            if parent is None or not can_read(principal, parent, include_expired=True):
                raise NotFoundError("Parent memory not found")
        grant_teams, grant_agents = self._validate_grants(
            principal, team, visibility, data.sensitivity, data.allowed_team_ids, data.allowed_agent_ids
        )

        text = embedding_text(data.title, data.content, data.tags)
        vector = self.embed(text)
        chash = content_hash(data.title, data.content)
        detection = self.detect(principal, vector, text, chash) if detect else DetectionResult([], [])

        if data.reject_on_duplicate and detection.duplicates:
            record(
                AuditAction.DUPLICATE_DETECTED, AuditStatus.DENIED, principal, resource_type=RESOURCE,
                reason="Creation rejected: potential duplicate", details={"duplicates": [d.memory_id for d in detection.duplicates]},
            )
            raise ConflictError(
                "Potential duplicate memories found.",
                details={"duplicates": [d.model_dump(mode="json") for d in detection.duplicates]},
            )

        source = data.source or ("playground" if principal.on_behalf_of else principal.kind.value)
        memory = Memory(
            id=uuid.uuid4(),
            title=data.title,
            content=data.content,
            content_hash=chash,
            memory_type=data.memory_type,
            creator_agent_id=principal.agent_id,
            creator_user_id=principal.user_id,
            team_id=team.id,
            project_id=data.project_id,
            visibility=visibility,
            sensitivity=data.sensitivity,
            importance=data.importance,
            confidence=data.confidence,
            source=source,
            source_ref=data.source_ref,
            extra=data.metadata or {},
            expires_at=data.expires_at,
            parent_memory_id=data.parent_memory_id,
            current_version=1,
            embedding_model=self.embedder.model_name,
        )
        memory.embedding_id = str(memory.id)
        memory.tags = self._tags(data.tags)
        for t in grant_teams:
            memory.access_grants.append(MemoryAccess(team_id=t.id, granted_by_agent_id=principal.agent_id, granted_by_user_id=principal.user_id))
        for a in grant_agents:
            memory.access_grants.append(MemoryAccess(agent_id=a.id, granted_by_agent_id=principal.agent_id, granted_by_user_id=principal.user_id))
        self.db.add(memory)
        self.db.flush()
        self._add_version(memory, principal, "Initial version", None)
        visible_conflicts = self._record_conflicts(principal, memory, detection)
        self.db.flush()

        try:
            self.store.upsert(str(memory.id), vector, memory_payload(memory))
        except VectorStoreError as exc:
            self.db.rollback()
            record(AuditAction.MEMORY_CREATE, AuditStatus.FAILURE, principal, resource_type=RESOURCE, reason="Vector database unavailable")
            raise ServiceUnavailableError("Vector database unavailable; memory was not stored") from exc
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            self._safe_vector_delete(str(memory.id))
            raise

        warnings = self._warnings(detection, visible_conflicts)
        record(
            AuditAction.MEMORY_CREATE, AuditStatus.SUCCESS, principal, resource_type=RESOURCE, resource_id=memory.id,
            details={"title": memory.title, "team": team.name, "visibility": visibility.value, "memory_type": memory.memory_type.value, "grants": {"teams": [t.name for t in grant_teams], "agents": [a.name for a in grant_agents]}},
        )
        if detection.duplicates:
            record(AuditAction.DUPLICATE_DETECTED, AuditStatus.SUCCESS, principal, resource_type=RESOURCE, resource_id=memory.id, reason="Potential duplicate memories found.", details={"duplicates": [d.memory_id for d in detection.duplicates]})
        if detection.conflicts:
            record(AuditAction.CONFLICT_DETECTED, AuditStatus.SUCCESS, principal, resource_type=RESOURCE, resource_id=memory.id, reason="Potential conflict detected", details={"conflicting_memory_ids": [c[0].id for c in detection.conflicts]})
        return memory, warnings

    def bulk_create(self, principal: Principal, req: BulkCreateRequest) -> BulkCreateResponse:
        results: list[BulkItemResult] = []
        for index, raw in enumerate(req.items):
            title = raw.get("title") if isinstance(raw, dict) else None
            try:
                item = MemoryCreate.model_validate({**raw, "reject_on_duplicate": req.skip_duplicates or raw.get("reject_on_duplicate", False)})
            except PydanticValidationError as exc:
                errors = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
                results.append(BulkItemResult(index=index, status="error", title=title, error={"code": "validation_error", "message": "Invalid item", "details": errors}))
                if req.stop_on_error:
                    break
                continue
            try:
                memory, warnings = self.create(principal, item, detect=req.detect_duplicates)
                results.append(BulkItemResult(index=index, status="created", title=memory.title, memory_id=memory.id, warnings=warnings if warnings.message else None))
            except ConflictError as exc:
                self.db.rollback()
                if req.skip_duplicates and exc.message.startswith("Potential duplicate"):
                    results.append(BulkItemResult(index=index, status="skipped_duplicate", title=item.title, error={"code": exc.code, "message": exc.message, "details": exc.details}))
                    continue
                results.append(BulkItemResult(index=index, status="error", title=item.title, error={"code": exc.code, "message": exc.message}))
                if req.stop_on_error:
                    break
            except AppError as exc:
                self.db.rollback()
                results.append(BulkItemResult(index=index, status="error", title=item.title, error={"code": exc.code, "message": exc.message}))
                if req.stop_on_error:
                    break
        response = BulkCreateResponse(
            created=sum(r.status == "created" for r in results),
            skipped=sum(r.status == "skipped_duplicate" for r in results),
            failed=sum(r.status == "error" for r in results),
            results=results,
        )
        record(
            AuditAction.MEMORY_BULK_IMPORT, AuditStatus.SUCCESS if not response.failed else AuditStatus.FAILURE, principal,
            resource_type=RESOURCE, details={"submitted": len(req.items), "created": response.created, "skipped": response.skipped, "failed": response.failed, "detect_duplicates": req.detect_duplicates},
        )
        return response

    def _safe_vector_delete(self, point_id: str) -> None:
        try:
            self.store.delete(point_id)
        except VectorStoreError:
            logger.error("could not remove orphan vector %s; run POST /api/admin/reindex", point_id)

    # ------------------------------------------------------------------ read
    def get(self, principal: Principal, memory_id: uuid.UUID, include_expired: bool = False, audit: bool = True) -> Memory:
        memory = self.db.get(Memory, memory_id)
        if memory is None:
            raise NotFoundError("Memory not found")
        decision = can_read(principal, memory, include_expired=True)
        if not decision:
            self._deny(principal, AuditAction.MEMORY_READ, memory_id, decision, {"memory_team": memory.team.name, "visibility": memory.visibility.value})
        if memory.expires_at is not None and memory.expires_at <= datetime.now(timezone.utc) and not include_expired:
            raise NotFoundError("Memory has expired; pass include_expired=true to view it")
        if audit:
            self.register_access([memory], principal)
            self.db.commit()
            record(AuditAction.MEMORY_READ, AuditStatus.ALLOWED, principal, resource_type=RESOURCE, resource_id=memory.id, details={"memory_team": memory.team.name, "cross_team": memory.team_id != principal.team_id})
        return memory

    def register_access(self, memories: list[Memory], principal: Principal) -> None:
        now = datetime.now(timezone.utc)
        for memory in memories:
            memory.access_count += 1
            memory.last_accessed_at = now
            if principal.team_id != memory.team_id:
                memory.reuse_count += 1

    def list(
        self,
        principal: Principal,
        *,
        q: str | None = None,
        memory_type: str | None = None,
        team_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        visibility: str | None = None,
        status: str | None = None,
        verification_status: str | None = None,
        conflict_status: str | None = None,
        tag: str | None = None,
        include_expired: bool = False,
        page: int = 1,
        page_size: int = 25,
    ) -> Page[MemoryListItem]:
        include_archived = principal.is_admin and status == MemoryStatus.ARCHIVED.value
        where = [sql_read_filter(principal, include_archived=include_archived or (principal.is_admin and status is None), include_expired=include_expired)]
        if q:
            pattern = f"%{q.strip()[:200]}%"
            where.append(or_(Memory.title.ilike(pattern), Memory.content.ilike(pattern), Memory.tags.any(Tag.name.ilike(pattern))))
        if memory_type:
            where.append(Memory.memory_type == memory_type)
        if team_id:
            where.append(Memory.team_id == team_id)
        if agent_id:
            where.append(Memory.creator_agent_id == agent_id)
        if project_id:
            where.append(Memory.project_id == project_id)
        if visibility:
            where.append(Memory.visibility == visibility)
        if status:
            where.append(Memory.status == status)
        if verification_status:
            where.append(Memory.verification_status == verification_status)
        if conflict_status:
            where.append(Memory.conflict_status == conflict_status)
        if tag:
            where.append(Memory.tags.any(Tag.name == tag.lower()))
        total = self.db.scalar(select(func.count()).select_from(Memory).where(*where)) or 0
        rows = self.db.scalars(
            select(Memory).where(*where).order_by(Memory.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return Page[MemoryListItem](items=[memory_list_item(m) for m in rows], total=total, page=page, page_size=page_size)

    # ------------------------------------------------------------------ update / versioning
    def update(self, principal: Principal, memory_id: uuid.UUID, data: MemoryUpdate) -> tuple[Memory, MemoryWarnings]:
        memory = self.load(memory_id, for_update=True)
        decision = can_modify(principal, memory, P.MEMORY_UPDATE)
        if not decision:
            self.db.rollback()
            self._deny(principal, AuditAction.MEMORY_UPDATE, memory_id, decision)
        if data.expected_version is not None and data.expected_version != memory.current_version:
            raise ConflictError(
                f"Version mismatch: expected {data.expected_version}, current is {memory.current_version}",
                details={"current_version": memory.current_version},
            )

        fields = data.model_dump(exclude_unset=True, exclude={"change_description", "expected_version", "clear_expiration"})
        if not fields and not data.clear_expiration:
            raise ValidationFailed("No changes supplied")
        if "content" in fields and len(fields["content"]) > self.settings.max_memory_content_length:
            raise ValidationFailed(f"content exceeds the maximum length of {self.settings.max_memory_content_length} characters")

        changed: list[str] = []
        old_text = embedding_text(memory.title, memory.content, memory.tag_names)
        for name in ("title", "content", "memory_type", "visibility", "sensitivity", "importance", "confidence", "source_ref"):
            if name in fields and fields[name] is not None and getattr(memory, name) != fields[name]:
                setattr(memory, name, fields[name])
                changed.append(name)
        if "metadata" in fields and fields["metadata"] is not None and fields["metadata"] != memory.extra:
            memory.extra = fields["metadata"]
            changed.append("metadata")
        if "tags" in fields and fields["tags"] is not None and fields["tags"] != memory.tag_names:
            memory.tags = self._tags(fields["tags"])
            changed.append("tags")
        if "project_id" in fields and fields["project_id"] != memory.project_id:
            if fields["project_id"] is not None and self.db.get(Project, fields["project_id"]) is None:
                raise NotFoundError("Project not found")
            memory.project_id = fields["project_id"]
            changed.append("project_id")
        if data.clear_expiration and memory.expires_at is not None:
            memory.expires_at = None
            changed.append("expires_at")
        elif "expires_at" in fields and fields["expires_at"] is not None and fields["expires_at"] != memory.expires_at:
            memory.expires_at = fields["expires_at"]
            changed.append("expires_at")
        if memory.visibility == Visibility.ORGANIZATION and memory.sensitivity in (Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED):
            raise ValidationFailed("confidential/restricted memories cannot be visible to the whole organization")
        if not changed:
            raise ValidationFailed("No changes supplied (values are identical to the current version)")

        new_text = embedding_text(memory.title, memory.content, memory.tag_names)
        text_changed = new_text != old_text
        vector: list[float] | None = None
        detection = DetectionResult([], [])
        if text_changed:
            vector = self.embed(new_text)
            memory.content_hash = content_hash(memory.title, memory.content)
            memory.embedding_model = self.embedder.model_name
            detection = self.detect(principal, vector, new_text, memory.content_hash, exclude_id=memory.id)
            # A memory that has been re-worded no longer carries its old verification.
            if memory.verification_status == VerificationStatus.VERIFIED:
                memory.verification_status = VerificationStatus.UNVERIFIED
                memory.verified_at = None
                memory.verified_by_user_id = None

        previous = self.db.scalar(
            select(MemoryVersion).where(MemoryVersion.memory_id == memory.id).order_by(MemoryVersion.version_number.desc()).limit(1)
        )
        memory.current_version += 1
        memory.updated_at = datetime.now(timezone.utc)
        self._add_version(memory, principal, data.change_description, previous)
        visible_conflicts = self._record_conflicts(principal, memory, detection) if detection.conflicts else []
        self.db.flush()

        try:
            if vector is not None:
                self.store.upsert(str(memory.id), vector, memory_payload(memory))
            else:
                self.store.set_payload(str(memory.id), memory_payload(memory))
        except VectorStoreError as exc:
            self.db.rollback()
            raise ServiceUnavailableError("Vector database unavailable; memory was not updated") from exc
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            self.resync_vector(memory.id)
            raise

        record(
            AuditAction.MEMORY_UPDATE, AuditStatus.SUCCESS, principal, resource_type=RESOURCE, resource_id=memory.id,
            details={"version": memory.current_version, "changed_fields": changed, "change_description": data.change_description},
        )
        if detection.conflicts:
            record(AuditAction.CONFLICT_DETECTED, AuditStatus.SUCCESS, principal, resource_type=RESOURCE, resource_id=memory.id, reason="Potential conflict detected", details={"conflicting_memory_ids": [c[0].id for c in detection.conflicts]})
        return memory, self._warnings(detection, visible_conflicts)

    def resync_vector(self, memory_id: uuid.UUID) -> None:
        """Re-embed the committed DB state of a memory into the vector store."""
        memory = self.db.get(Memory, memory_id)
        try:
            if memory is None:
                self.store.delete(str(memory_id))
                return
            vector = self.embed(embedding_text(memory.title, memory.content, memory.tag_names))
            self.store.upsert(str(memory.id), vector, memory_payload(memory))
        except Exception:
            logger.exception("vector re-sync failed for %s", memory_id)

    def versions(self, principal: Principal, memory_id: uuid.UUID) -> tuple[Memory, list[MemoryVersion]]:
        memory = self.db.get(Memory, memory_id)
        if memory is None:
            raise NotFoundError("Memory not found")
        decision = can_read(principal, memory, include_expired=True)
        if not decision:
            self._deny(principal, AuditAction.MEMORY_VERSIONS_READ, memory_id, decision)
        rows = list(
            self.db.scalars(select(MemoryVersion).where(MemoryVersion.memory_id == memory_id).order_by(MemoryVersion.version_number.desc()))
        )
        return memory, rows

    def version(self, principal: Principal, memory_id: uuid.UUID, number: int) -> tuple[Memory, MemoryVersion]:
        memory, rows = self.versions(principal, memory_id)
        for row in rows:
            if row.version_number == number:
                return memory, row
        raise NotFoundError(f"Version {number} not found")

    # ------------------------------------------------------------------ archive / delete
    def archive(self, principal: Principal, memory_id: uuid.UUID) -> Memory:
        memory = self.load(memory_id, for_update=True)
        decision = can_modify(principal, memory, P.MEMORY_DELETE)
        if not decision:
            self.db.rollback()
            self._deny(principal, AuditAction.MEMORY_ARCHIVE, memory_id, decision)
        if memory.status == MemoryStatus.ARCHIVED:
            raise ConflictError("Memory is already archived")
        memory.status = MemoryStatus.ARCHIVED
        memory.archived_at = datetime.now(timezone.utc)
        self._commit_payload(memory)
        record(AuditAction.MEMORY_ARCHIVE, AuditStatus.SUCCESS, principal, resource_type=RESOURCE, resource_id=memory.id, details={"title": memory.title})
        return memory

    def restore(self, principal: Principal, memory_id: uuid.UUID) -> Memory:
        if not principal.is_admin:
            raise ForbiddenError("Administrator role required")
        memory = self.load(memory_id, for_update=True)
        if memory.status != MemoryStatus.ARCHIVED:
            raise ConflictError("Memory is not archived")
        memory.status = MemoryStatus.ACTIVE
        memory.archived_at = None
        self._commit_payload(memory)
        record(AuditAction.MEMORY_RESTORE, AuditStatus.SUCCESS, principal, resource_type=RESOURCE, resource_id=memory.id)
        return memory

    def delete_permanently(self, principal: Principal, memory_id: uuid.UUID) -> None:
        if not principal.is_admin:
            record(AuditAction.MEMORY_DELETE, AuditStatus.DENIED, principal, resource_type=RESOURCE, resource_id=memory_id, reason="Permanent deletion requires administrator role")
            raise ForbiddenError("Permanent deletion requires administrator role")
        memory = self.load(memory_id, for_update=True)
        title = memory.title
        self.db.delete(memory)
        self.db.flush()
        try:
            self.store.delete(str(memory_id))
        except VectorStoreError as exc:
            self.db.rollback()
            raise ServiceUnavailableError("Vector database unavailable; memory was not deleted") from exc
        self.db.commit()
        record(AuditAction.MEMORY_DELETE, AuditStatus.SUCCESS, principal, resource_type=RESOURCE, resource_id=memory_id, details={"title": title, "permanent": True})

    def _commit_payload(self, memory: Memory) -> None:
        self.db.flush()
        try:
            self.store.set_payload(str(memory.id), memory_payload(memory))
        except VectorStoreError as exc:
            self.db.rollback()
            raise ServiceUnavailableError("Vector database unavailable") from exc
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            self.resync_vector(memory.id)
            raise

    # ------------------------------------------------------------------ sharing
    def share(self, principal: Principal, memory_id: uuid.UUID, data: ShareRequest) -> Memory:
        memory = self.load(memory_id, for_update=True)
        decision = can_modify(principal, memory, P.MEMORY_SHARE)
        if not decision:
            self.db.rollback()
            self._deny(principal, AuditAction.MEMORY_SHARE, memory_id, decision, {"team_ids": data.team_ids, "agent_ids": data.agent_ids})
        teams, agents = self._validate_grants(principal, memory.team, memory.visibility, memory.sensitivity, data.team_ids, data.agent_ids)
        existing_teams = set(memory.allowed_team_ids)
        existing_agents = set(memory.allowed_agent_ids)
        added = {"teams": [], "agents": []}
        for t in teams:
            if t.id not in existing_teams:
                memory.access_grants.append(MemoryAccess(team_id=t.id, granted_by_agent_id=principal.agent_id, granted_by_user_id=principal.user_id))
                added["teams"].append(t.name)
        for a in agents:
            if a.id not in existing_agents:
                memory.access_grants.append(MemoryAccess(agent_id=a.id, granted_by_agent_id=principal.agent_id, granted_by_user_id=principal.user_id))
                added["agents"].append(a.name)
        self._commit_payload(memory)
        record(AuditAction.MEMORY_SHARE, AuditStatus.SUCCESS, principal, resource_type=RESOURCE, resource_id=memory.id, details={"granted": added})
        return memory

    def unshare(self, principal: Principal, memory_id: uuid.UUID, target: str) -> Memory:
        memory = self.load(memory_id, for_update=True)
        decision = can_modify(principal, memory, P.MEMORY_SHARE)
        if not decision:
            self.db.rollback()
            self._deny(principal, AuditAction.MEMORY_UNSHARE, memory_id, decision, {"target": target})
        kind, _, raw = target.partition(":")
        if not raw:
            kind, raw = "any", kind
        try:
            target_id = uuid.UUID(raw)
        except ValueError as exc:
            raise ValidationFailed("target must be 'team:<uuid>', 'agent:<uuid>' or a uuid") from exc
        grant = next(
            (
                g for g in memory.access_grants
                if (kind in ("team", "any") and g.team_id == target_id) or (kind in ("agent", "any") and g.agent_id == target_id)
            ),
            None,
        )
        if grant is None:
            raise NotFoundError("Sharing grant not found")
        memory.access_grants.remove(grant)
        self._commit_payload(memory)
        record(AuditAction.MEMORY_UNSHARE, AuditStatus.SUCCESS, principal, resource_type=RESOURCE, resource_id=memory.id, details={"revoked": target})
        return memory

    # ------------------------------------------------------------------ quality
    def feedback(self, principal: Principal, memory_id: uuid.UUID, data: FeedbackCreate) -> tuple[MemoryFeedback, float]:
        memory = self.db.get(Memory, memory_id)
        if memory is None:
            raise NotFoundError("Memory not found")
        decision = can_read(principal, memory)
        if not decision:
            self._deny(principal, AuditAction.MEMORY_FEEDBACK, memory_id, decision)
        fb = MemoryFeedback(memory_id=memory.id, agent_id=principal.agent_id, user_id=principal.user_id, rating=data.rating, helpful=data.helpful, comment=data.comment)
        self.db.add(fb)
        self.db.flush()
        count, rating_sum = self.db.execute(
            select(func.count(MemoryFeedback.id), func.coalesce(func.sum(MemoryFeedback.rating), 0)).where(MemoryFeedback.memory_id == memory.id)
        ).one()
        # Bayesian average with a neutral prior of two 3-star ratings, mapped to [0, 1]
        memory.reliability = round(((rating_sum + 6) / (count + 2) - 1) / 4, 4)
        self.db.commit()
        record(AuditAction.MEMORY_FEEDBACK, AuditStatus.SUCCESS, principal, resource_type=RESOURCE, resource_id=memory.id, details={"rating": data.rating, "helpful": data.helpful})
        return fb, memory.reliability

    def verify(self, principal: Principal, memory_id: uuid.UUID, data: VerifyRequest) -> Memory:
        memory = self.load(memory_id, for_update=True)
        if principal.user is None:
            self.db.rollback()
            self._deny(principal, AuditAction.MEMORY_VERIFY, memory_id, AccessDecision(False, "Only human reviewers can verify memories"))
        if not principal.has(P.MEMORY_VERIFY) or not principal.manages_team(memory.team_id):
            self.db.rollback()
            self._deny(principal, AuditAction.MEMORY_VERIFY, memory_id, AccessDecision(False, "Only administrators or managers of the owning team can verify"))
        memory.verification_status = data.status
        memory.verified_by_user_id = principal.user_id if data.status != VerificationStatus.UNVERIFIED else None
        memory.verified_at = datetime.now(timezone.utc) if data.status != VerificationStatus.UNVERIFIED else None
        self._commit_payload(memory)
        record(AuditAction.MEMORY_VERIFY, AuditStatus.SUCCESS, principal, resource_type=RESOURCE, resource_id=memory.id, details={"status": data.status.value, "note": data.note})
        return memory

    # ------------------------------------------------------------------ provenance
    def provenance(self, principal: Principal, memory_id: uuid.UUID) -> ProvenanceOut:
        memory, versions = self.versions(principal, memory_id)
        mid = str(memory.id)
        read_filter = and_(AuditLog.resource_type == RESOURCE, AuditLog.resource_id == mid, AuditLog.action == AuditAction.MEMORY_READ)
        search_filter = and_(AuditLog.action == AuditAction.MEMORY_SEARCH, AuditLog.details.contains({"result_ids": [mid]}))
        reads = self.db.scalar(select(func.count()).select_from(AuditLog).where(read_filter, AuditLog.status == AuditStatus.ALLOWED)) or 0
        hits = self.db.scalar(select(func.count()).select_from(AuditLog).where(search_filter)) or 0
        denied = self.db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.resource_id == mid, AuditLog.status == AuditStatus.DENIED)) or 0
        distinct = self.db.scalar(
            select(func.count(func.distinct(AuditLog.actor_id))).where(or_(and_(read_filter, AuditLog.status == AuditStatus.ALLOWED), search_filter))
        ) or 0

        history = None
        if can_view_sensitive_fields(principal, memory):
            rows = self.db.scalars(
                select(AuditLog).where(or_(AuditLog.resource_id == mid, search_filter)).order_by(AuditLog.timestamp.desc()).limit(50)
            )
            history = [
                AccessEvent(timestamp=r.timestamp, actor=r.actor_name, actor_type=r.actor_type.value, action=r.action, status=r.status.value, reason=r.reason)
                for r in rows
            ]
        creator = version_author(versions[-1]) if versions else None
        modified_by = sorted({version_author(v).name for v in versions if v.version_number > 1})
        return ProvenanceOut(
            memory_id=memory.id,
            title=memory.title,
            created_by=creator,
            team={"id": memory.team.id, "name": memory.team.name},
            created_at=memory.created_at,
            source=memory.source,
            source_ref=memory.source_ref if can_view_sensitive_fields(principal, memory) else None,
            parent_memory_id=memory.parent_memory_id,
            current_version=memory.current_version,
            versions=[version_out(v, memory.current_version) for v in versions],
            modified_by=modified_by,
            access_summary={"direct_reads": reads, "search_hits": hits, "denied_attempts": denied, "distinct_accessors": distinct, "cross_team_reuse": memory.reuse_count},
            access_history=history,
        )
