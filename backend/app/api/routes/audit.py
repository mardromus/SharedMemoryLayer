from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.models.audit import AuditLog
from app.models.enums import ActorType, AuditStatus
from app.schemas.common import Page
from app.security import permissions as P
from app.security.principal import Principal

router = APIRouter(prefix="/api/audit-logs", tags=["Audit"])


class AuditLogOut(BaseModel):
    id: uuid.UUID
    timestamp: datetime
    actor_type: ActorType
    actor_id: uuid.UUID | None
    actor_name: str
    actor_team_id: uuid.UUID | None
    on_behalf_of: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    status: AuditStatus
    reason: str | None
    ip_address: str | None
    request_id: str | None
    metadata: dict


@router.get("", response_model=Page[AuditLogOut])
def list_audit_logs(
    actor: str | None = Query(None, max_length=100, description="Actor name contains"),
    actor_type: ActorType | None = None,
    action: str | None = Query(None, max_length=48),
    status: AuditStatus | None = None,
    resource_type: str | None = Query(None, max_length=32),
    resource_id: str | None = Query(None, max_length=64),
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(P.AUDIT_READ)),
):
    """Administrators see every event; team managers see events performed by their team's actors."""
    where = []
    if not principal.is_admin:
        where.append(AuditLog.actor_team_id == principal.team_id)
    if actor:
        where.append(AuditLog.actor_name.ilike(f"%{actor}%"))
    if actor_type:
        where.append(AuditLog.actor_type == actor_type)
    if action:
        where.append(AuditLog.action == action.upper())
    if status:
        where.append(AuditLog.status == status)
    if resource_type:
        where.append(AuditLog.resource_type == resource_type)
    if resource_id:
        where.append(AuditLog.resource_id == resource_id)
    if since:
        where.append(AuditLog.timestamp >= since)
    if until:
        where.append(AuditLog.timestamp <= until)
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*where)) or 0
    rows = db.scalars(select(AuditLog).where(*where).order_by(AuditLog.timestamp.desc()).offset((page - 1) * page_size).limit(page_size))
    items = [
        AuditLogOut(
            id=r.id, timestamp=r.timestamp, actor_type=r.actor_type, actor_id=r.actor_id, actor_name=r.actor_name,
            actor_team_id=r.actor_team_id, on_behalf_of=r.on_behalf_of, action=r.action, resource_type=r.resource_type,
            resource_id=r.resource_id, status=r.status, reason=r.reason, ip_address=r.ip_address, request_id=r.request_id,
            metadata=r.details or {},
        )
        for r in rows
    ]
    return Page[AuditLogOut](items=items, total=total, page=page, page_size=page_size)


@router.get("/actions", response_model=list[str])
def list_actions(db: Session = Depends(get_db), _: Principal = Depends(require_permission(P.AUDIT_READ))):
    return list(db.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action)))
