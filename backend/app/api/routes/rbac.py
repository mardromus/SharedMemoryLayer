from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal, require_permission
from app.core.database import get_db
from app.schemas.identity import PermissionOut, RoleOut, RolePermissionsUpdate, UserOut, UserUpdate
from app.security import permissions as P
from app.security.principal import Principal
from app.services.identity_service import IdentityService

router = APIRouter(tags=["Users & Access Control"])


@router.get("/api/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: Principal = Depends(require_permission(P.USER_MANAGE))):
    return IdentityService(db).list_users()


@router.patch("/api/users/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, data: UserUpdate, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.USER_MANAGE))):
    svc = IdentityService(db)
    return svc.user_out(svc.update_user(principal, user_id, data))


@router.get("/api/roles", response_model=list[RoleOut])
def list_roles(db: Session = Depends(get_db), _: Principal = Depends(get_current_principal)):
    return IdentityService(db).list_roles()


@router.get("/api/permissions", response_model=list[PermissionOut])
def list_permissions(db: Session = Depends(get_db), _: Principal = Depends(get_current_principal)):
    return IdentityService(db).list_permissions()


@router.put("/api/roles/{role_id}/permissions", response_model=RoleOut)
def set_role_permissions(role_id: uuid.UUID, data: RolePermissionsUpdate, db: Session = Depends(get_db),
                         principal: Principal = Depends(require_permission(P.ROLE_MANAGE))):
    """Configure which permissions a role grants (takes effect on the next request)."""
    role = IdentityService(db).set_role_permissions(principal, role_id, data.permissions)
    return RoleOut(id=role.id, name=role.name, description=role.description, is_system=role.is_system, permissions=sorted(role.permission_codes))
