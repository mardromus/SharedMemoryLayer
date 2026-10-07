"""End-to-end integration: Agent (Python SDK) -> REST API -> PostgreSQL -> Qdrant.

The SDK is driven through the FastAPI test transport, so every call exercises
the real routing, authentication, authorisation, database and vector store.
"""
from __future__ import annotations

import pytest
from shared_memory import ConflictError, MemoryClient, PermissionDeniedError

from app.vector import get_vector_store
from tests.conftest import unique


@pytest.fixture
def sdk(client, org):
    def make(agent_key: str) -> MemoryClient:
        return MemoryClient(base_url="http://testserver", api_key=org["agents"][agent_key]["api_key"], http_client=client)

    return make


def test_sdk_full_agent_workflow(client, factory, org, sdk):
    dev, qa, support = sdk("dev"), sdk("qa"), sdk("support")
    project = client.post("/api/projects", headers=factory.admin, json={"name": unique("sdk"), "team_id": org["teams"]["dev"]["id"]}).json()

    created = dev.remember(
        title="JWT authentication issue",
        content="Authentication failed because refresh tokens expired.",
        memory_type="learning",
        tags=["authentication", "jwt"],
        visibility="team",
        project_id=project["id"],
    )
    memory = created.memory
    assert memory.version == 1
    assert get_vector_store().get_payload(memory.id)["project_id"] == project["id"]

    # Not shared yet: QA cannot see it
    with pytest.raises(PermissionDeniedError):
        qa.get(memory.id)
    assert qa.search("authentication problems caused by expired tokens", project_ids=[project["id"]]) == []

    dev.share(memory.id, team_ids=[org["teams"]["qa"]["id"]])
    results = qa.search("authentication problems caused by expired tokens", project_ids=[project["id"]])
    assert results[0].memory_id == memory.id and results[0].cross_team is True
    assert results.mode == "hybrid"

    updated = dev.update(memory.id, content="Authentication failed because refresh tokens expired; fixed by returning 401.", change_description="add fix")
    assert updated.memory.version == 2
    results = qa.search("authentication problems caused by expired tokens", project_ids=[project["id"]])
    assert results[0].version == 2 and "401" in results[0].content
    assert [v.version_number for v in qa.versions(memory.id)] == [2, 1]

    with pytest.raises(PermissionDeniedError):
        support.get(memory.id)
    with pytest.raises(PermissionDeniedError):
        qa.update(memory.id, content="not my memory")

    fb = qa.feedback(memory.id, rating=5, comment="useful")
    assert fb.reliability > 0.5
    prov = dev.provenance(memory.id)
    assert prov.current_version == 2 and prov.created_by.name == org["agents"]["dev"]["name"]

    dev.unshare(memory.id, f"team:{org['teams']['qa']['id']}")
    with pytest.raises(PermissionDeniedError):
        qa.get(memory.id)


def test_sdk_duplicate_rejection_and_whoami(org, sdk):
    dev = sdk("dev")
    me = dev.whoami()
    assert me.kind == "agent" and me.name == org["agents"]["dev"]["name"]
    title = unique("dup-sdk")
    dev.remember(title, "Background jobs are retried three times with jitter.")
    with pytest.raises(ConflictError):
        dev.remember(title, "Background jobs are retried three times with jitter.", reject_on_duplicate=True)


def test_sdk_run_task(org, sdk):
    qa = sdk("qa")
    out = qa.run_task("What should I test for login problems?", top_k=3)
    assert out.provider == "extractive" and out.agent == org["agents"]["qa"]["name"]


def test_sdk_token_exchange(org, sdk):
    dev = sdk("dev")
    dev.exchange_api_key()
    assert dev.whoami().id == org["agents"]["dev"]["id"]
