"""Model Context Protocol (MCP) endpoint.

Exposes the shared memory as MCP tools over Streamable HTTP at ``/mcp`` so any
MCP client (Claude Code, Claude Desktop via mcp-remote, Cursor, VS Code, custom
agents) can use it with nothing but a URL and an agent API key:

    claude mcp add --transport http shared-memory http://localhost:8000/mcp \
        --header "X-API-Key: sml_..."

Every tool call is authenticated (``X-API-Key`` or ``Authorization: Bearer``),
authorised with the calling agent's own RBAC permissions and memory visibility,
and audited exactly like a REST call — MCP is just another front door.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from sqlalchemy import select
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.database import session_factory
from app.core.errors import AppError
from app.core.rate_limit import client_ip, enforce
from app.models.enums import MemoryType, Visibility
from app.models.identity import Agent, Team
from app.schemas.memory import MemoryCreate, MemoryUpdate, SearchRequest, ShareRequest
from app.security import permissions as P
from app.security.dependencies import authenticate_api_key, load_principal
from app.security.principal import Principal
from app.security.tokens import TokenError, decode_access_token
from app.services.memory_service import MemoryService
from app.services.search_service import SearchService
from app.services.serializers import version_out

logger = logging.getLogger(__name__)

INSTRUCTIONS = """Shared organisational memory for AI agents across teams.
- Before starting a task, call search_memory to reuse what other agents and teams already know
  (decisions, incidents, solutions, learnings). Cite the memory_id you relied on.
- After finishing, call remember with ONE concise, reusable learning or decision (not a transcript).
- If search_memory returns nothing relevant, say so instead of guessing.
- Only memories you are authorised to read are ever returned; access denials are audited."""

MEMORY_TYPES = [t.value for t in MemoryType]
VISIBILITIES = [v.value for v in Visibility]


# ------------------------------------------------------------------ authentication (ASGI wrapper)
class MCPAuth:
    """Authenticates every MCP HTTP request before it reaches the MCP transport."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        identity = await anyio.to_thread.run_sync(_authenticate, headers)
        if identity is None:
            body = json.dumps({"error": {"code": "unauthorized", "message": "Provide an agent API key (X-API-Key) or a Bearer token"}}).encode()
            await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": body})
            return
        scope.setdefault("state", {})["sml_identity"] = identity
        await self.app(scope, receive, send)


def _authenticate(headers: dict[str, str]) -> tuple[str, uuid.UUID] | None:
    db = session_factory()()
    try:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                claims = decode_access_token(auth[7:].strip())
            except TokenError:
                return None
            principal = load_principal(db, claims.subject_type, claims.subject_id)
            return (claims.subject_type, claims.subject_id) if principal else None
        key = headers.get("x-api-key")
        if key:
            agent = authenticate_api_key(db, key)
            return ("agent", agent.id) if agent is not None and agent.status == "active" else None
        return None
    finally:
        db.close()


# ------------------------------------------------------------------ helpers
def _identity(ctx: Context) -> tuple[str, uuid.UUID]:
    request = ctx.request_context.request
    identity = getattr(request, "scope", {}).get("state", {}).get("sml_identity") if request is not None else None
    if identity is None:
        raise ToolError("Not authenticated")
    return identity


def _call(ctx: Context, fn, bucket: str | None = None):
    """Run a synchronous service call for the authenticated principal in a worker thread."""
    kind, subject_id = _identity(ctx)
    request = ctx.request_context.request

    def work():
        db = session_factory()()
        try:
            principal = load_principal(db, kind, subject_id)
            if principal is None:
                raise ToolError("Account is disabled")
            principal.ip_address = client_ip(request) if request is not None else None
            principal.user_agent = "mcp"
            if bucket and request is not None:
                enforce(request, bucket, identity=f"{principal.kind.value}:{principal.id}")
            return fn(db, principal)
        except AppError as exc:
            # ToolError text is shown to the calling model; anything else is hidden by the SDK.
            raise ToolError(exc.message) from exc
        except ValueError as exc:
            raise ToolError(f"Invalid argument: {exc}") from exc
        finally:
            db.close()

    return anyio.to_thread.run_sync(work)


def _resolve_teams(db, names_or_ids: list[str]) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for value in names_or_ids:
        try:
            ids.append(uuid.UUID(value))
            continue
        except ValueError:
            pass
        team = db.scalar(select(Team).where(Team.name.ilike(value.strip()), Team.is_active.is_(True)))
        if team is None:
            raise ToolError(f"Unknown team '{value}'. Call list_teams to see valid names.")
        ids.append(team.id)
    return ids


def _resolve_agents(db, names_or_ids: list[str]) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for value in names_or_ids:
        try:
            ids.append(uuid.UUID(value))
            continue
        except ValueError:
            pass
        agent = db.scalar(select(Agent).where(Agent.name.ilike(value.strip())))
        if agent is None:
            raise ToolError(f"Unknown agent '{value}'.")
        ids.append(agent.id)
    return ids


def _memory_view(m) -> dict[str, Any]:
    return {
        "memory_id": str(m.id), "title": m.title, "content": m.content, "memory_type": m.memory_type.value,
        "team": m.team.name, "creator": (m.creator_agent.name if m.creator_agent else m.creator_user.username if m.creator_user else None),
        "tags": m.tag_names, "visibility": m.visibility.value, "sensitivity": m.sensitivity.value,
        "verification_status": m.verification_status.value, "confidence": m.confidence, "version": m.current_version,
        "updated_at": m.updated_at.isoformat(),
    }


# ------------------------------------------------------------------ server
def build_mcp_server() -> MCPServer:
    server = MCPServer(name="shared-memory", title="Shared Memory Layer", instructions=INSTRUCTIONS, version="1.0.0")
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    writes = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

    @server.tool(annotations=read_only)
    async def search_memory(
        query: str,
        ctx: Context,
        top_k: int = 5,
        memory_types: list[str] | None = None,
        tags: list[str] | None = None,
        mode: str = "hybrid",
    ) -> dict[str, Any]:
        """Search the organisation's shared memory by meaning (and exact keywords such as error codes).

        Returns only memories you are allowed to read, best first. memory_types can include:
        knowledge, decision, learning, experience, context, task_result, incident, documentation, solution.
        mode: hybrid (default), semantic or keyword."""

        def run(db, principal: Principal):
            if not principal.has(P.MEMORY_READ):
                raise ToolError(f"Missing permission {P.MEMORY_READ}")
            req = SearchRequest(query=query, top_k=max(1, min(top_k, 20)), memory_types=memory_types, tags=tags, mode=mode)
            res = SearchService(db).search(principal, req)
            return {
                "query": res.query,
                "total": res.total,
                "results": [
                    {"memory_id": str(r.memory_id), "title": r.title, "content": r.content, "similarity": r.similarity,
                     "team": r.team, "memory_type": r.memory_type.value, "version": r.version, "confidence": r.confidence,
                     "verified": r.verification_status.value == "verified", "cross_team": r.cross_team, "match": r.match}
                    for r in res.results
                ],
            }

        return await _call(ctx, run, bucket="search")

    @server.tool(annotations=writes)
    async def remember(
        title: str,
        content: str,
        ctx: Context,
        memory_type: str = "learning",
        tags: list[str] | None = None,
        visibility: str | None = None,
        share_with_teams: list[str] | None = None,
        importance: float = 0.5,
        confidence: float = 0.8,
        source_ref: str | None = None,
    ) -> dict[str, Any]:
        """Store one reusable piece of knowledge (a decision, learning, incident, solution...) in shared memory.

        visibility: private, team (default for most teams), selected_teams, selected_agents or organization.
        share_with_teams: team names (see list_teams) that should also be able to read it.
        The response lists potential duplicates or conflicts with existing memories."""

        def run(db, principal: Principal):
            if not principal.has(P.MEMORY_CREATE):
                raise ToolError(f"Missing permission {P.MEMORY_CREATE}")
            data = MemoryCreate(
                title=title, content=content, memory_type=memory_type, tags=tags or [], visibility=visibility,
                importance=importance, confidence=confidence, source="mcp", source_ref=source_ref,
                allowed_team_ids=_resolve_teams(db, share_with_teams or []),
            )
            memory, warnings = MemoryService(db).create(principal, data)
            return {"memory_id": str(memory.id), "version": memory.current_version, "team": memory.team.name,
                    "visibility": memory.visibility.value, "warnings": warnings.model_dump(mode="json")}

        return await _call(ctx, run, bucket="write")

    @server.tool(annotations=read_only)
    async def get_memory(memory_id: str, ctx: Context) -> dict[str, Any]:
        """Read one memory by id (fails with 'Access denied' if you are not authorised)."""

        def run(db, principal: Principal):
            return _memory_view(MemoryService(db).get(principal, uuid.UUID(memory_id)))

        return await _call(ctx, run)

    @server.tool(annotations=writes)
    async def update_memory(
        memory_id: str,
        ctx: Context,
        content: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
        change_description: str = "Updated via MCP",
    ) -> dict[str, Any]:
        """Update a memory you own (creates a new immutable version; old versions stay readable)."""

        def run(db, principal: Principal):
            fields = {k: v for k, v in {"content": content, "title": title, "tags": tags}.items() if v is not None}
            memory, warnings = MemoryService(db).update(principal, uuid.UUID(memory_id), MemoryUpdate(change_description=change_description, **fields))
            return {"memory_id": str(memory.id), "version": memory.current_version, "warnings": warnings.model_dump(mode="json")}

        return await _call(ctx, run, bucket="write")

    @server.tool(annotations=writes)
    async def share_memory(memory_id: str, ctx: Context, teams: list[str] | None = None, agents: list[str] | None = None) -> dict[str, Any]:
        """Grant other teams or agents (names or ids) read access to a memory you own."""

        def run(db, principal: Principal):
            req = ShareRequest(team_ids=_resolve_teams(db, teams or []), agent_ids=_resolve_agents(db, agents or []))
            memory = MemoryService(db).share(principal, uuid.UUID(memory_id), req)
            return {"memory_id": str(memory.id), "allowed_teams": [g.team.name for g in memory.access_grants if g.team],
                    "allowed_agents": [g.agent.name for g in memory.access_grants if g.agent]}

        return await _call(ctx, run)

    @server.tool(annotations=read_only)
    async def memory_history(memory_id: str, ctx: Context) -> dict[str, Any]:
        """Version history of a memory: who changed what, when and why."""

        def run(db, principal: Principal):
            memory, versions = MemoryService(db).versions(principal, uuid.UUID(memory_id))
            return {"memory_id": memory_id, "current_version": memory.current_version,
                    "versions": [version_out(v, memory.current_version).model_dump(mode="json") for v in versions]}

        return await _call(ctx, run)

    @server.tool(annotations=read_only)
    async def list_teams(ctx: Context) -> dict[str, Any]:
        """Teams in the organisation (use their names with share_with_teams / share_memory)."""

        def run(db, principal: Principal):
            teams = db.scalars(select(Team).where(Team.is_active.is_(True)).order_by(Team.name))
            return {"teams": [{"id": str(t.id), "name": t.name, "description": t.description} for t in teams]}

        return await _call(ctx, run)

    @server.tool(annotations=read_only)
    async def whoami(ctx: Context) -> dict[str, Any]:
        """Your identity, team and effective permissions in the shared memory."""

        def run(db, principal: Principal):
            team = db.get(Team, principal.team_id) if principal.team_id else None
            return {"id": str(principal.id), "kind": principal.kind.value, "name": principal.name,
                    "team": team.name if team else None, "roles": sorted(principal.roles), "permissions": sorted(principal.permissions)}

        return await _call(ctx, run)

    return server


def mount_mcp(app) -> MCPServer:
    """Attach the MCP endpoint at exactly ``/mcp`` (no redirects, authenticated)."""
    server = build_mcp_server()
    starlette_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        # Every request must carry an agent credential, so browser-driven DNS
        # rebinding cannot act on anyone's behalf; host checks are left to the proxy.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    route = next(r for r in starlette_app.routes if getattr(r, "path", None) == "/mcp")
    app.router.routes.append(Route("/mcp", endpoint=MCPAuth(route.app), methods=["GET", "POST", "DELETE"], include_in_schema=False))
    return server
