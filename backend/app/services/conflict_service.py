"""Human/admin resolution of detected memory conflicts."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from app.audit import AuditAction, record
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.models.enums import AuditStatus, ConflictRecordStatus, ConflictResolution, ConflictStatus, MemoryStatus
from app.models.memory import Memory, MemoryConflict
from app.schemas.common import Ref
from app.schemas.memory import ConflictOut, ConflictResolveRequest
from app.security import permissions as P
from app.security.principal import Principal
from app.services.memory_service import MemoryService


def conflict_out(c: MemoryConflict) -> ConflictOut:
    return ConflictOut(
        id=c.id,
        memory=Ref(id=c.memory.id, name=c.memory.title),
        conflicting_memory=Ref(id=c.conflicting_memory.id, name=c.conflicting_memory.title),
        memory_content=c.memory.content,
        conflicting_content=c.conflicting_memory.content,
        memory_team=c.memory.team.name,
        conflicting_team=c.conflicting_memory.team.name,
        similarity=c.similarity,
        reason=c.reason,
        detection_method=c.detection_method,
        status=c.status,
        resolution=c.resolution,
        resolution_note=c.resolution_note,
        resolved_at=c.resolved_at,
        created_at=c.created_at,
    )


class ConflictService:
    def __init__(self, db: Session):
        self.db = db
        self.memories = MemoryService(db)

    def list(self, principal: Principal, status: ConflictRecordStatus | None = None) -> list[ConflictOut]:
        if not principal.has(P.CONFLICT_RESOLVE):
            raise ForbiddenError(f"Missing permission: {P.CONFLICT_RESOLVE}")
        new, old = aliased(Memory), aliased(Memory)
        stmt = (
            select(MemoryConflict)
            .join(new, MemoryConflict.memory_id == new.id)
            .join(old, MemoryConflict.conflicting_memory_id == old.id)
            .order_by(MemoryConflict.created_at.desc())
        )
        if status:
            stmt = stmt.where(MemoryConflict.status == status)
        if not principal.is_admin:
            stmt = stmt.where(or_(new.team_id == principal.team_id, old.team_id == principal.team_id))
        return [conflict_out(c) for c in self.db.scalars(stmt.limit(200))]

    def resolve(self, principal: Principal, conflict_id: uuid.UUID, req: ConflictResolveRequest) -> ConflictOut:
        conflict = self.db.get(MemoryConflict, conflict_id)
        if conflict is None:
            raise NotFoundError("Conflict not found")
        if conflict.status != ConflictRecordStatus.OPEN:
            raise ConflictError("Conflict is already closed")
        to_archive: Memory | None = None
        if req.resolution == ConflictResolution.KEEP_NEW:
            to_archive = conflict.conflicting_memory
        elif req.resolution == ConflictResolution.KEEP_EXISTING:
            to_archive = conflict.memory
        allowed = principal.has(P.CONFLICT_RESOLVE) and (
            principal.is_admin
            or (to_archive is not None and principal.manages_team(to_archive.team_id))
            or (to_archive is None and (principal.manages_team(conflict.memory.team_id) or principal.manages_team(conflict.conflicting_memory.team_id)))
        )
        if not allowed:
            record(AuditAction.CONFLICT_RESOLVED, AuditStatus.DENIED, principal, resource_type="conflict", resource_id=conflict.id, reason="Not allowed to resolve this conflict")
            raise ForbiddenError("You are not allowed to resolve this conflict")

        now = datetime.now(timezone.utc)
        conflict.status = ConflictRecordStatus.DISMISSED if req.resolution == ConflictResolution.DISMISS else ConflictRecordStatus.RESOLVED
        conflict.resolution = req.resolution
        conflict.resolution_note = req.note
        conflict.resolved_by_user_id = principal.user_id
        conflict.resolved_at = now
        self.db.flush()
        for memory in (conflict.memory, conflict.conflicting_memory):
            open_left = self.db.scalar(
                select(MemoryConflict.id).where(
                    MemoryConflict.status == ConflictRecordStatus.OPEN,
                    or_(MemoryConflict.memory_id == memory.id, MemoryConflict.conflicting_memory_id == memory.id),
                ).limit(1)
            )
            if open_left is None and memory.conflict_status == ConflictStatus.POTENTIAL_CONFLICT:
                memory.conflict_status = ConflictStatus.RESOLVED
        if to_archive is not None and to_archive.status == MemoryStatus.ACTIVE:
            to_archive.status = MemoryStatus.ARCHIVED
            to_archive.archived_at = now
            self.memories._commit_payload(to_archive)
        else:
            self.db.commit()
        record(
            AuditAction.CONFLICT_RESOLVED, AuditStatus.SUCCESS, principal, resource_type="conflict", resource_id=conflict.id,
            details={"resolution": req.resolution.value, "archived_memory_id": to_archive.id if to_archive else None, "note": req.note},
        )
        return conflict_out(conflict)
