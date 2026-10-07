"""FastAPI application factory for the Shared Memory Layer."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import agents, audit, auth, memories, operations, rbac, teams
from app.core.config import get_settings
from app.core.database import session_scope
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.mcp_server import mount_mcp

logger = logging.getLogger(__name__)

DESCRIPTION = """
Secure shared memory infrastructure for autonomous AI agents belonging to different teams.

* **Agents** authenticate with an API key (`X-API-Key`) or a JWT obtained from `/api/auth/agent-token`.
* **Humans** authenticate with `/api/auth/login` (JWT access token + rotating refresh token).
* Every memory operation is authorised with RBAC permissions **and** per-memory visibility rules,
  enforced in the API/database layer and pushed down into the vector search.
* Every security-relevant operation is written to the audit log.
"""

TAGS = [
    {"name": "Authentication", "description": "Login, token refresh/rotation, logout, agent tokens."},
    {"name": "Memories", "description": "Create, retrieve, semantic search, update/version, share, archive."},
    {"name": "Agents", "description": "Agent registry and API keys."},
    {"name": "Teams & Projects"},
    {"name": "Users & Access Control", "description": "Users, roles and configurable permissions."},
    {"name": "Audit"},
    {"name": "Conflicts", "description": "Review queue for contradicting memories."},
    {"name": "Agent Runtime", "description": "LLM agent that uses shared memory as context."},
    {"name": "Agent Playground", "description": "Humans simulating agents (always with the agent's permissions)."},
    {"name": "Dashboard"},
    {"name": "Administration"},
    {"name": "Health"},
]


def startup_tasks() -> None:
    from app.services.bootstrap import ensure_admin, ensure_rbac, ensure_vector_collection
    from app.services.demo_seed import seed_demo

    settings = get_settings()
    with session_scope() as db:
        ensure_rbac(db)
        ensure_admin(db)
    if not ensure_vector_collection():
        logger.error("vector store unavailable at start-up; semantic search will degrade to keyword search")
        return
    if settings.seed_demo_data:
        with session_scope() as db:
            try:
                seed_demo(db)
            except Exception:  # the API must still come up if seeding fails
                logger.exception("demo seed failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(get_settings().log_level)
    if app.state.run_startup:
        startup_tasks()
    # The MCP Streamable HTTP transport needs its session manager running.
    async with app.state.mcp.session_manager.run():
        yield


def create_app(run_startup: bool = True) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.run_startup = run_startup
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    register_exception_handlers(app)
    for module in (auth, memories, agents, teams, rbac, audit, operations):
        app.include_router(module.router)
    app.state.mcp = mount_mcp(app)
    return app


app = create_app()
