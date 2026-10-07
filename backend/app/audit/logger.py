"""Audit trail writer.

Audit events are written in their **own** short transaction so that events
describing a *failed* or *denied* operation are persisted even though the
request's main transaction is rolled back.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi.encoders import jsonable_encoder

from app.core.database import session_factory
from app.core.logging import log_extra, request_id_var
from app.models.audit import AuditLog
from app.models.enums import ActorType, AuditStatus
from app.security.principal import Principal

logger = logging.getLogger("app.audit")


class AuditAction:
    AUTH_LOGIN = "AUTH_LOGIN"
    AUTH_LOGIN_FAILED = "AUTH_LOGIN_FAILED"
    AUTH_LOGOUT = "AUTH_LOGOUT"
    AUTH_REFRESH = "AUTH_REFRESH"
    AUTH_REGISTER = "AUTH_REGISTER"
    AUTH_AGENT_TOKEN = "AUTH_AGENT_TOKEN"
    AUTH_INVALID_TOKEN = "AUTH_INVALID_TOKEN"
    MEMORY_CREATE = "MEMORY_CREATE"
    MEMORY_READ = "MEMORY_READ"
    MEMORY_SEARCH = "MEMORY_SEARCH"
    MEMORY_UPDATE = "MEMORY_UPDATE"
    MEMORY_DELETE = "MEMORY_DELETE"
    MEMORY_ARCHIVE = "MEMORY_ARCHIVE"
    MEMORY_RESTORE = "MEMORY_RESTORE"
    MEMORY_SHARE = "MEMORY_SHARE"
    MEMORY_UNSHARE = "MEMORY_UNSHARE"
    MEMORY_VERIFY = "MEMORY_VERIFY"
    MEMORY_FEEDBACK = "MEMORY_FEEDBACK"
    MEMORY_VERSIONS_READ = "MEMORY_VERSIONS_READ"
    MEMORY_BULK_IMPORT = "MEMORY_BULK_IMPORT"
    DUPLICATE_DETECTED = "DUPLICATE_DETECTED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    CONFLICT_RESOLVED = "CONFLICT_RESOLVED"
    AGENT_CREATE = "AGENT_CREATE"
    AGENT_UPDATE = "AGENT_UPDATE"
    AGENT_DELETE = "AGENT_DELETE"
    API_KEY_CREATE = "API_KEY_CREATE"
    API_KEY_REVOKE = "API_KEY_REVOKE"
    TEAM_CREATE = "TEAM_CREATE"
    TEAM_UPDATE = "TEAM_UPDATE"
    USER_CREATE = "USER_CREATE"
    USER_UPDATE = "USER_UPDATE"
    ROLE_CHANGE = "ROLE_CHANGE"
    ROLE_PERMISSIONS_UPDATE = "ROLE_PERMISSIONS_UPDATE"
    PROJECT_CREATE = "PROJECT_CREATE"
    AGENT_TASK_RUN = "AGENT_TASK_RUN"
    ACCESS_DENIED = "ACCESS_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    SYSTEM_REINDEX = "SYSTEM_REINDEX"


def record(
    action: str,
    status: AuditStatus,
    principal: Principal | None = None,
    *,
    resource_type: str | None = None,
    resource_id: uuid.UUID | str | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    actor_name: str | None = None,
    actor_type: ActorType | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    entry = AuditLog(
        actor_type=actor_type or (principal.kind if principal else ActorType.ANONYMOUS),
        actor_id=principal.id if principal else None,
        actor_name=(actor_name or (principal.name if principal else "anonymous"))[:100],
        actor_team_id=principal.team_id if principal else None,
        on_behalf_of=principal.on_behalf_of if principal else None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        status=status,
        reason=reason[:500] if reason else None,
        ip_address=ip_address or (principal.ip_address if principal else None),
        user_agent=(user_agent or (principal.user_agent if principal else None) or "")[:255] or None,
        request_id=request_id_var.get(),
        details=jsonable_encoder(details or {}),
    )
    db = session_factory()()
    try:
        db.add(entry)
        db.commit()
    except Exception:  # auditing must never break the request, but must be visible
        db.rollback()
        logger.exception("failed to persist audit event %s", action)
    finally:
        db.close()
    logger.info(
        "audit %s %s",
        action,
        status.value,
        extra=log_extra(
            actor=entry.actor_name,
            resource=f"{resource_type}:{resource_id}" if resource_type else None,
            reason=reason,
        ),
    )
