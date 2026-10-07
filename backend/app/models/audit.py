"""Append-only audit log."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, str_enum, uuid_pk
from app.models.enums import ActorType, AuditStatus


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_action_timestamp", "action", "timestamp"),
        Index("ix_audit_logs_status_timestamp", "status", "timestamp"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    timestamp: Mapped[datetime] = created_at_col(index=True)
    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType, "audit_actor_type"), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    actor_name: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    actor_team_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    on_behalf_of: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[AuditStatus] = mapped_column(str_enum(AuditStatus, "audit_status"), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


# Declared in the migration too; keeps autogenerate in sync.
Index("ix_audit_logs_metadata_gin", AuditLog.details, postgresql_using="gin")
