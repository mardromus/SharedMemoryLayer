"""Aggregated statistics for the dashboard (all computed from live data)."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import Float, Integer, cast, func, select
from sqlalchemy.orm import Session

from app.audit import AuditAction
from app.core.config import get_settings
from app.core.database import check_database
from app.models.audit import AuditLog
from app.models.enums import AgentStatus, AuditStatus, ConflictRecordStatus, MemoryStatus, VerificationStatus
from app.models.identity import Agent, Team, User
from app.models.memory import Memory, MemoryConflict, Project
from app.security.access import sql_read_filter
from app.security.principal import Principal
from app.vector import get_vector_store


def _count(db: Session, stmt) -> int:
    return int(db.scalar(stmt) or 0)


def system_health() -> dict:
    from app.llm import get_llm
    from app.vector import embeddings

    settings = get_settings()
    store = get_vector_store()
    vector_ok = store.healthy()
    llm = get_llm()
    return {
        "database": "up" if check_database() else "down",
        "vector_db": "up" if vector_ok else "down",
        "embedding_model": {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "loaded": embeddings._provider is not None,
        },
        "llm": {"provider": llm.name, "model": llm.model, "reachable": llm.healthy()},
    }


def dashboard_stats(db: Session, principal: Principal) -> dict:
    now = datetime.now(timezone.utc)
    today = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    day_ago = now - timedelta(hours=24)
    week_start = today - timedelta(days=6)

    searches_total = _count(db, select(func.count()).select_from(AuditLog).where(AuditLog.action == AuditAction.MEMORY_SEARCH))
    searches_today = _count(db, select(func.count()).select_from(AuditLog).where(AuditLog.action == AuditAction.MEMORY_SEARCH, AuditLog.timestamp >= today))
    result_count = cast(AuditLog.details["result_count"].astext, Integer)
    successful = _count(db, select(func.count()).select_from(AuditLog).where(AuditLog.action == AuditAction.MEMORY_SEARCH, result_count > 0))
    cross_team_searches = _count(
        db, select(func.count()).select_from(AuditLog).where(AuditLog.action == AuditAction.MEMORY_SEARCH, cast(AuditLog.details["cross_team_results"].astext, Integer) > 0)
    )
    avg_latency = db.scalar(
        select(func.avg(cast(AuditLog.details["took_ms"].astext, Float))).where(AuditLog.action == AuditAction.MEMORY_SEARCH, AuditLog.timestamp >= day_ago)
    )

    counts = {
        "agents": _count(db, select(func.count(Agent.id)).where(Agent.status != AgentStatus.DELETED)),
        "active_agents": _count(db, select(func.count(Agent.id)).where(Agent.status == AgentStatus.ACTIVE, Agent.last_active_at >= day_ago)),
        "teams": _count(db, select(func.count(Team.id)).where(Team.is_active.is_(True))),
        "users": _count(db, select(func.count(User.id))),
        "projects": _count(db, select(func.count(Project.id))),
        "memories": _count(db, select(func.count(Memory.id)).where(Memory.status == MemoryStatus.ACTIVE)),
        "archived_memories": _count(db, select(func.count(Memory.id)).where(Memory.status == MemoryStatus.ARCHIVED)),
        "expired_memories": _count(db, select(func.count(Memory.id)).where(Memory.expires_at <= now)),
        "verified_memories": _count(db, select(func.count(Memory.id)).where(Memory.verification_status == VerificationStatus.VERIFIED)),
        "memories_today": _count(db, select(func.count(Memory.id)).where(Memory.created_at >= today)),
        "searches_today": searches_today,
        "searches_total": searches_total,
        "access_denials_today": _count(db, select(func.count()).select_from(AuditLog).where(AuditLog.status == AuditStatus.DENIED, AuditLog.timestamp >= today)),
        "access_denials_total": _count(db, select(func.count()).select_from(AuditLog).where(AuditLog.status == AuditStatus.DENIED)),
        "memory_reuse": _count(db, select(func.coalesce(func.sum(Memory.reuse_count), 0))),
        "memory_accesses": _count(db, select(func.coalesce(func.sum(Memory.access_count), 0))),
        "open_conflicts": _count(db, select(func.count(MemoryConflict.id)).where(MemoryConflict.status == ConflictRecordStatus.OPEN)),
        "duplicates_flagged": _count(db, select(func.count()).select_from(AuditLog).where(AuditLog.action == AuditAction.DUPLICATE_DETECTED)),
    }

    by_type = [{"memory_type": t.value if hasattr(t, "value") else t, "count": c} for t, c in db.execute(
        select(Memory.memory_type, func.count()).where(Memory.status == MemoryStatus.ACTIVE).group_by(Memory.memory_type).order_by(func.count().desc())
    )]
    by_team = [{"team": n, "count": c} for n, c in db.execute(
        select(Team.name, func.count(Memory.id)).join(Memory, Memory.team_id == Team.id).where(Memory.status == MemoryStatus.ACTIVE).group_by(Team.name).order_by(Team.name)
    )]
    by_visibility = [{"visibility": v.value if hasattr(v, "value") else v, "count": c} for v, c in db.execute(
        select(Memory.visibility, func.count()).where(Memory.status == MemoryStatus.ACTIVE).group_by(Memory.visibility)
    )]

    day = func.date_trunc("day", AuditLog.timestamp)
    activity: dict[str, dict] = {}
    for i in range(7):
        d = (week_start + timedelta(days=i)).date().isoformat()
        activity[d] = {"date": d, "memories_created": 0, "searches": 0, "denials": 0}
    mday = func.date_trunc("day", Memory.created_at)
    for d, c in db.execute(select(mday, func.count()).where(Memory.created_at >= week_start).group_by(mday)):
        activity.setdefault(d.date().isoformat(), {"date": d.date().isoformat(), "memories_created": 0, "searches": 0, "denials": 0})["memories_created"] = c
    for d, c in db.execute(select(day, func.count()).where(AuditLog.action == AuditAction.MEMORY_SEARCH, AuditLog.timestamp >= week_start).group_by(day)):
        activity.setdefault(d.date().isoformat(), {"date": d.date().isoformat(), "memories_created": 0, "searches": 0, "denials": 0})["searches"] = c
    for d, c in db.execute(select(day, func.count()).where(AuditLog.status == AuditStatus.DENIED, AuditLog.timestamp >= week_start).group_by(day)):
        activity.setdefault(d.date().isoformat(), {"date": d.date().isoformat(), "memories_created": 0, "searches": 0, "denials": 0})["denials"] = c

    top_reused = [
        {"id": m.id, "title": m.title, "team": m.team.name, "reuse_count": m.reuse_count, "access_count": m.access_count}
        for m in db.scalars(
            select(Memory).where(sql_read_filter(principal), Memory.access_count > 0).order_by(Memory.reuse_count.desc(), Memory.access_count.desc()).limit(5)
        )
    ]
    recent_filter = [] if principal.is_admin else [AuditLog.actor_team_id == principal.team_id]
    recent = [
        {"timestamp": r.timestamp, "actor": r.actor_name, "actor_type": r.actor_type.value, "action": r.action, "resource_type": r.resource_type, "resource_id": r.resource_id, "status": r.status.value, "reason": r.reason}
        for r in db.scalars(select(AuditLog).where(*recent_filter).order_by(AuditLog.timestamp.desc()).limit(12))
    ]

    return {
        "generated_at": now,
        "counts": counts,
        "retrieval_success_rate": round(successful / searches_total, 4) if searches_total else None,
        "cross_team_search_rate": round(cross_team_searches / searches_total, 4) if searches_total else None,
        "avg_search_latency_ms_24h": round(float(avg_latency), 2) if avg_latency is not None else None,
        "memories_by_type": by_type,
        "memories_by_team": by_team,
        "memories_by_visibility": by_visibility,
        "activity_7d": sorted(activity.values(), key=lambda a: a["date"])[-7:],
        "top_reused_memories": top_reused,
        "recent_activity": recent,
        "health": system_health(),
    }
