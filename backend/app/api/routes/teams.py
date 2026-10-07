from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal, require_permission
from app.core.database import get_db
from app.schemas.identity import ProjectCreate, ProjectOut, TeamCreate, TeamOut, TeamUpdate
from app.security import permissions as P
from app.security.principal import Principal
from app.services.identity_service import IdentityService

router = APIRouter(tags=["Teams & Projects"])


@router.post("/api/teams", response_model=TeamOut, status_code=201)
def create_team(data: TeamCreate, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.TEAM_CREATE))):
    svc = IdentityService(db)
    return svc.team_out(svc.create_team(principal, data))


@router.get("/api/teams", response_model=list[TeamOut])
def list_teams(include_inactive: bool = False, db: Session = Depends(get_db), _: Principal = Depends(get_current_principal)):
    return IdentityService(db).list_teams(include_inactive)


@router.get("/api/teams/{team_id}", response_model=TeamOut)
def get_team(team_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(get_current_principal)):
    svc = IdentityService(db)
    return svc.team_out(svc.get_team(team_id))


@router.patch("/api/teams/{team_id}", response_model=TeamOut)
def update_team(team_id: uuid.UUID, data: TeamUpdate, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.TEAM_UPDATE))):
    svc = IdentityService(db)
    return svc.team_out(svc.update_team(principal, team_id, data))


@router.get("/api/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), _: Principal = Depends(get_current_principal)):
    return IdentityService(db).list_projects()


@router.post("/api/projects", response_model=ProjectOut, status_code=201)
def create_project(data: ProjectCreate, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.TEAM_UPDATE))):
    return ProjectOut.model_validate(IdentityService(db).create_project(principal, data))
