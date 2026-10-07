"""LLM agent runtime, agent playground and agent/team management."""
from __future__ import annotations

from tests.conftest import unique


def test_agent_runtime_uses_shared_memory_and_stores_result(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    project = client.post("/api/projects", headers=factory.admin, json={"name": unique("rt"), "team_id": org["teams"]["dev"]["id"]}).json()
    source = factory.memory(dev, title="JWT authentication fails when refresh tokens expire",
                            content="Refresh token expiry caused HTTP 500 on /auth/refresh; return 401 token_expired instead.",
                            visibility="organization", project_id=project["id"], memory_type="learning")["memory"]
    r = client.post("/api/agent-runtime/tasks", headers=qa["headers"], json={
        "task": "Write test cases for authentication failures caused by expired tokens", "store_result": True, "project_id": project["id"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "extractive"
    assert body["memories_used"][0]["memory_id"] == source["id"]
    assert body["reused_cross_team"] >= 1
    assert "[M1]" in body["answer"] and body["learning"]
    assert [s["name"] for s in body["steps"]] == ["memory_search", "reasoning", "store_result"]
    stored = client.get(f"/api/memories/{body['stored_memory_id']}", headers=qa["headers"]).json()
    assert stored["memory_type"] == "task_result" and stored["source"] == "llm-agent"
    assert stored["parent_memory_id"] == source["id"]
    assert factory.audit(action="AGENT_TASK_RUN", actor=qa["name"])


def test_runtime_isolated_mode_only_uses_own_memories(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    project = client.post("/api/projects", headers=factory.admin, json={"name": unique("iso"), "team_id": org["teams"]["dev"]["id"]}).json()
    factory.memory(dev, title="Blue-green deployment rollback", content="Roll back by switching the load balancer to the blue pool.",
                   visibility="organization", project_id=project["id"])
    task = {"task": "how do we roll back a blue-green deployment", "project_id": project["id"]}
    shared = client.post("/api/agent-runtime/tasks", headers=qa["headers"], json=task).json()
    isolated = client.post("/api/agent-runtime/tasks", headers=qa["headers"], json={**task, "only_own": True}).json()
    assert shared["memories_used"] and shared["reused_cross_team"] == 1
    assert isolated["memories_used"] == [] and "No relevant shared memory" in isolated["answer"]


def test_runtime_requires_agent_identity(client, factory):
    user = factory.user("TEAM_MANAGER", factory.team())
    r = client.post("/api/agent-runtime/tasks", headers=user["headers"], json={"task": "hello world"})
    assert r.status_code == 403


def test_playground_simulation_uses_agent_permissions(client, factory, org):
    sec, support = org["agents"]["sec"], org["agents"]["support"]
    secret = factory.memory(sec, title=unique("confidential finding"), content="Confidential security finding", visibility="team", sensitivity="confidential")["memory"]
    # Admin simulates Support-Agent: the agent's (not the admin's) permissions apply
    r = client.post("/api/playground/access-check", headers=factory.admin, json={"agent_id": support["id"], "memory_id": secret["id"]})
    assert r.status_code == 200
    assert r.json()["decision"] == "DENIED" and r.json()["reason"] == "Unauthorized team"
    allowed = client.post("/api/playground/access-check", headers=factory.admin, json={"agent_id": sec["id"], "memory_id": secret["id"]})
    assert allowed.json()["decision"] == "ALLOWED"
    denied_event = factory.audit(action="MEMORY_READ", status="denied", resource_id=secret["id"])[0]
    assert denied_event["actor_name"] == support["name"] and denied_event["on_behalf_of"] == factory_admin_name()

    search = client.post("/api/playground/search", headers=factory.admin, json={"agent_id": support["id"], "search": {"query": secret["title"], "similarity_threshold": 0.0, "top_k": 50}})
    assert secret["id"] not in {x["memory_id"] for x in search.json()["results"]}


def factory_admin_name() -> str:
    from app.core.config import get_settings

    return get_settings().admin_username


def test_playground_limited_to_own_team_for_managers(client, factory, org):
    manager = factory.user("TEAM_MANAGER", org["teams"]["qa"])
    r = client.post("/api/playground/search", headers=manager["headers"], json={"agent_id": org["agents"]["dev"]["id"], "search": {"query": "x"}})
    assert r.status_code == 403
    ok = client.post("/api/playground/search", headers=manager["headers"], json={"agent_id": org["agents"]["qa"]["id"], "search": {"query": "x"}})
    assert ok.status_code == 200


def test_playground_create_memory_as_agent(client, factory, org):
    dev = org["agents"]["dev"]
    r = client.post("/api/playground/memories", headers=factory.admin, json={
        "agent_id": dev["id"], "memory": {"title": unique("playground memory"), "content": "Created through the playground", "visibility": "team"},
    })
    assert r.status_code == 201
    memory = r.json()["memory"]
    assert memory["creator_agent"]["id"] == dev["id"] and memory["source"] == "playground"


def test_agent_management_crud(client, factory):
    team = factory.team()
    agent = factory.agent(team, name=unique("crud-agent"))
    assert agent["api_key"].startswith("sml_")
    listed = client.get("/api/agents", headers=factory.admin, params={"team_id": team["id"]}).json()
    assert [a["id"] for a in listed] == [agent["id"]]
    upd = client.patch(f"/api/agents/{agent['id']}", headers=factory.admin, json={"description": "updated", "capabilities": ["testing"]})
    assert upd.status_code == 200 and upd.json()["capabilities"] == ["testing"]
    key = client.post(f"/api/agents/{agent['id']}/api-keys", headers=factory.admin, json={"name": "rotation"})
    assert key.status_code == 201 and key.json()["api_key"].startswith("sml_")
    assert client.delete(f"/api/agents/{agent['id']}", headers=factory.admin).status_code == 204
    assert client.get(f"/api/agents/{agent['id']}", headers=factory.admin).status_code == 404
    assert client.get("/api/auth/me", headers={"X-API-Key": key.json()["api_key"]}).status_code == 401
    assert factory.audit(action="AGENT_DELETE", resource_id=agent["id"])


def test_team_crud(client, factory):
    name = unique("Team")
    team = factory.team(name, description="demo")
    assert team["slug"] == name.lower()
    assert client.post("/api/teams", headers=factory.admin, json={"name": name}).status_code == 409
    upd = client.patch(f"/api/teams/{team['id']}", headers=factory.admin, json={"allow_cross_team_sharing": False, "default_visibility": "private"})
    assert upd.status_code == 200 and upd.json()["default_visibility"] == "private"
    assert client.get(f"/api/teams/{team['id']}", headers=factory.admin).json()["allow_cross_team_sharing"] is False
    assert factory.audit(action="TEAM_CREATE", resource_id=team["id"])
