from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal, require_permission
from app.core.database import get_db
from app.schemas.identity import (
    AgentCreate,
    AgentCreated,
    AgentOut,
    AgentRolesUpdate,
    AgentUpdate,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
)
from app.security import permissions as P
from app.security.principal import Principal
from app.services.identity_service import IdentityService

router = APIRouter(prefix="/api/agents", tags=["Agents"])


@router.post("", response_model=AgentCreated, status_code=201)
def create_agent(data: AgentCreate, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.AGENT_CREATE))):
    """Register an agent. The plaintext API key is returned **once**."""
    svc = IdentityService(db)
    agent, key = svc.create_agent(principal, data)
    return AgentCreated(**svc.agent_out(agent, include_keys=True).model_dump(), api_key=key)


@router.get("", response_model=list[AgentOut])
def list_agents(team_id: uuid.UUID | None = Query(None), db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    svc = IdentityService(db)
    return [svc.agent_out(a, include_keys=principal.manages_team(a.team_id)) for a in svc.list_agents(team_id)]


@router.get("/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: uuid.UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)):
    svc = IdentityService(db)
    agent = svc.get_agent(agent_id)
    return svc.agent_out(agent, include_keys=principal.manages_team(agent.team_id))


@router.patch("/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: uuid.UUID, data: AgentUpdate, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.AGENT_UPDATE))):
    svc = IdentityService(db)
    return svc.agent_out(svc.update_agent(principal, agent_id, data), include_keys=True)


@router.put("/{agent_id}/roles", response_model=AgentOut)
def set_agent_roles(agent_id: uuid.UUID, data: AgentRolesUpdate, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.AGENT_UPDATE))):
    svc = IdentityService(db)
    return svc.agent_out(svc.update_agent(principal, agent_id, AgentUpdate(roles=data.roles)), include_keys=True)


@router.delete("/{agent_id}", status_code=204, response_class=Response)
def delete_agent(agent_id: uuid.UUID, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.AGENT_DELETE))):
    IdentityService(db).delete_agent(principal, agent_id)
    return Response(status_code=204)


@router.post("/{agent_id}/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_api_key(agent_id: uuid.UUID, data: ApiKeyCreate, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.AGENT_UPDATE))):
    plaintext, key = IdentityService(db).create_api_key(principal, agent_id, data)
    return ApiKeyCreated(**ApiKeyOut.model_validate(key).model_dump(), api_key=plaintext)


@router.delete("/{agent_id}/api-keys/{key_id}", status_code=204, response_class=Response)
def revoke_api_key(agent_id: uuid.UUID, key_id: uuid.UUID, db: Session = Depends(get_db), principal: Principal = Depends(require_permission(P.AGENT_UPDATE))):
    IdentityService(db).revoke_api_key(principal, agent_id, key_id)
    return Response(status_code=204)
