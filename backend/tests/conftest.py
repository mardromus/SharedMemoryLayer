"""Test fixtures.

The suite runs against **real** infrastructure: a dedicated PostgreSQL database
(``TEST_DATABASE_URL``, created by the postgres init script) migrated with
Alembic, a dedicated Qdrant collection and the real local embedding model.
Nothing is mocked.

Run inside the backend container:
    docker compose exec backend pytest
"""
from __future__ import annotations

import os
import secrets
import uuid

# ---- environment must be configured before the application is imported -------
_test_db = os.environ.get("TEST_DATABASE_URL")
if not _test_db:
    raise RuntimeError("TEST_DATABASE_URL must be set to run the test-suite")
os.environ["DATABASE_URL"] = _test_db
os.environ["QDRANT_COLLECTION"] = "test_memories"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "test"
os.environ["ADMIN_USERNAME"] = "test-admin"
os.environ["ADMIN_PASSWORD"] = "Adm1n-" + secrets.token_urlsafe(12)
os.environ["LLM_PROVIDER"] = "none"
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import reset_engine, session_scope  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.bootstrap import ensure_admin, ensure_rbac, ensure_vector_collection  # noqa: E402
from app.vector import get_vector_store  # noqa: E402
from app.vector.embeddings import get_embedder  # noqa: E402

STRONG_PASSWORD = "Str0ng-Passw0rd!"


def _migrate_fresh() -> None:
    engine = create_engine(_test_db)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    cfg.attributes["database_url"] = _test_db
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def infrastructure():
    reset_engine()
    _migrate_fresh()
    get_vector_store().reset(get_embedder().dimension)
    with session_scope() as db:
        ensure_rbac(db)
        ensure_admin(db)
    assert ensure_vector_collection(retries=3)
    yield


@pytest.fixture(scope="session")
def app(infrastructure):
    return create_app(run_startup=False)


@pytest.fixture(scope="session")
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    limiter.reset()
    yield
    limiter.reset()


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def api_key_headers(key: str) -> dict:
    return {"X-API-Key": key}


class Factory:
    """Creates isolated teams / agents / users through the public API."""

    def __init__(self, client: TestClient):
        self.client = client
        settings = get_settings()
        r = client.post("/api/auth/login", json={"username": settings.admin_username, "password": settings.admin_password})
        assert r.status_code == 200, r.text
        self.admin_token = r.json()["access_token"]
        self.admin = bearer(self.admin_token)

    def team(self, name: str | None = None, **extra) -> dict:
        r = self.client.post("/api/teams", headers=self.admin, json={"name": name or unique("team"), **extra})
        assert r.status_code == 201, r.text
        return r.json()

    def agent(self, team: dict, roles: list[str] | None = None, name: str | None = None) -> dict:
        body = {"name": name or unique("agent"), "team_id": team["id"], "roles": roles or ["AGENT"]}
        r = self.client.post("/api/agents", headers=self.admin, json=body)
        assert r.status_code == 201, r.text
        data = r.json()
        data["headers"] = api_key_headers(data["api_key"])
        return data

    def user(self, role: str = "READ_ONLY", team: dict | None = None) -> dict:
        username = unique("user")
        body = {"username": username, "password": STRONG_PASSWORD, "role": role, "team_id": team["id"] if team else None}
        r = self.client.post("/api/auth/register", headers=self.admin, json=body)
        assert r.status_code == 201, r.text
        login = self.client.post("/api/auth/login", json={"username": username, "password": STRONG_PASSWORD})
        assert login.status_code == 200, login.text
        data = r.json()
        data["password"] = STRONG_PASSWORD
        data["tokens"] = login.json()
        data["headers"] = bearer(login.json()["access_token"])
        return data

    def memory(self, agent: dict, **fields) -> dict:
        body = {"title": unique("memory"), "content": "Generic content " + uuid.uuid4().hex, "memory_type": "knowledge", **fields}
        r = self.client.post("/api/memories", headers=agent["headers"], json=body)
        assert r.status_code == 201, r.text
        return r.json()

    def search(self, principal: dict, query: str, **fields) -> dict:
        r = self.client.post("/api/memories/search", headers=principal["headers"], json={"query": query, **fields})
        assert r.status_code == 200, r.text
        return r.json()

    def audit(self, **params) -> list[dict]:
        r = self.client.get("/api/audit-logs", headers=self.admin, params={"page_size": 200, **params})
        assert r.status_code == 200, r.text
        return r.json()["items"]


@pytest.fixture(scope="session")
def factory(client) -> Factory:
    return Factory(client)


@pytest.fixture
def org(factory):
    """A fresh mini-organisation: Development, QA, Security and Support teams, one agent each."""
    teams = {name: factory.team(unique(name)) for name in ("dev", "qa", "sec", "support")}
    agents = {name: factory.agent(team) for name, team in teams.items()}
    return {"teams": teams, "agents": agents}
