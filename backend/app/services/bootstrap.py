"""Idempotent start-up tasks: RBAC catalogue, bootstrap administrator, vector collection."""
from __future__ import annotations

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import RoleName
from app.models.identity import Permission, Role, User
from app.security import permissions as P
from app.security.passwords import hash_password, validate_password_policy
from app.vector import get_vector_store
from app.vector.embeddings import get_embedder

logger = logging.getLogger(__name__)


def ensure_rbac(db: Session) -> None:
    existing = {p.code: p for p in db.scalars(select(Permission))}
    for code, description in P.PERMISSION_DESCRIPTIONS.items():
        if code not in existing:
            existing[code] = Permission(code=code, description=description)
            db.add(existing[code])
    db.flush()
    for role_name, codes in P.DEFAULT_ROLE_PERMISSIONS.items():
        role = db.scalar(select(Role).where(Role.name == role_name))
        if role is None:
            # Defaults are applied only on first creation; afterwards the
            # role -> permission mapping is owned by administrators.
            role = Role(name=role_name, description=P.ROLE_DESCRIPTIONS[role_name], is_system=True)
            role.permissions = [existing[c] for c in codes]
            db.add(role)
        elif role_name == RoleName.ADMIN:
            role.permissions = list(existing.values())  # admin always holds every permission
    db.commit()


def ensure_admin(db: Session) -> None:
    settings = get_settings()
    if db.scalar(select(User.id).where(User.username == settings.admin_username)):
        return
    if not settings.admin_password:
        logger.warning("ADMIN_PASSWORD not set - no administrator account was created")
        return
    problems = validate_password_policy(settings.admin_password, settings.admin_username, settings.password_min_length)
    if problems:
        raise RuntimeError(f"ADMIN_PASSWORD does not meet the password policy: {'; '.join(problems)}")
    role = db.scalar(select(Role).where(Role.name == RoleName.ADMIN))
    db.add(User(
        username=settings.admin_username,
        email=settings.admin_email,
        full_name="Platform Administrator",
        password_hash=hash_password(settings.admin_password),
        role_id=role.id,
    ))
    db.commit()
    logger.info("created bootstrap administrator %s", settings.admin_username)


def ensure_vector_collection(retries: int = 20, delay: float = 1.5) -> bool:
    embedder = get_embedder()
    store = get_vector_store()
    for attempt in range(1, retries + 1):
        try:
            store.ensure_collection(embedder.dimension)
            return True
        except Exception as exc:  # noqa: BLE001 - infrastructure may still be starting
            logger.warning("vector store not ready (attempt %s/%s): %s", attempt, retries, exc)
            time.sleep(delay)
    return False
