"""Role-based access control and privilege-escalation protection."""
from __future__ import annotations


def test_read_only_user_cannot_create_memory(client, factory):
    team = factory.team()
    reader = factory.user("READ_ONLY", team)
    r = client.post("/api/memories", headers=reader["headers"], json={"title": "Not allowed", "content": "x"})
    assert r.status_code == 403
    assert "memory:create" in r.json()["error"]["message"]


def test_agent_cannot_create_team_or_agents(client, factory):
    team = factory.team()
    agent = factory.agent(team)
    assert client.post("/api/teams", headers=agent["headers"], json={"name": "Rogue Team"}).status_code == 403
    r = client.post("/api/agents", headers=agent["headers"], json={"name": "rogue-agent", "team_id": team["id"]})
    assert r.status_code == 403


def test_agent_cannot_read_audit_logs(client, factory):
    team = factory.team()
    agent = factory.agent(team)
    assert client.get("/api/audit-logs", headers=agent["headers"]).status_code == 403
    denials = factory.audit(action="ACCESS_DENIED", actor=agent["name"])
    assert denials and "audit:read" in denials[0]["reason"]


def test_team_manager_cannot_grant_admin_role(client, factory):
    team = factory.team()
    manager = factory.user("TEAM_MANAGER", team)
    r = client.post("/api/agents", headers=manager["headers"], json={"name": "sneaky", "team_id": team["id"], "roles": ["ADMIN"]})
    assert r.status_code == 403
    agent = factory.agent(team)
    r2 = client.put(f"/api/agents/{agent['id']}/roles", headers=manager["headers"], json={"roles": ["ADMIN"]})
    assert r2.status_code == 403
    assert factory.audit(action="ROLE_CHANGE", status="denied")


def test_team_manager_scoped_to_own_team(client, factory):
    team, other = factory.team(), factory.team()
    manager = factory.user("TEAM_MANAGER", team)
    ok = client.post("/api/agents", headers=manager["headers"], json={"name": f"tm-agent-{team['id'][:6]}", "team_id": team["id"]})
    assert ok.status_code == 201
    denied = client.post("/api/agents", headers=manager["headers"], json={"name": f"tm-agent-{other['id'][:6]}", "team_id": other["id"]})
    assert denied.status_code == 403


def test_non_admin_cannot_change_role_permissions(client, factory):
    team = factory.team()
    manager = factory.user("TEAM_MANAGER", team)
    roles = client.get("/api/roles", headers=manager["headers"]).json()
    agent_role = next(r for r in roles if r["name"] == "AGENT")
    r = client.put(f"/api/roles/{agent_role['id']}/permissions", headers=manager["headers"], json={"permissions": ["memory:read"]})
    assert r.status_code == 403


def test_permissions_are_configurable(client, factory):
    """Removing memory:create from READ_ONLY->AGENT style role takes effect immediately."""
    roles = {r["name"]: r for r in client.get("/api/roles", headers=factory.admin).json()}
    agent_role = roles["AGENT"]
    original = agent_role["permissions"]
    team = factory.team()
    agent = factory.agent(team)
    try:
        r = client.put(f"/api/roles/{agent_role['id']}/permissions", headers=factory.admin,
                       json={"permissions": [p for p in original if p != "memory:create"]})
        assert r.status_code == 200
        denied = client.post("/api/memories", headers=agent["headers"], json={"title": "blocked", "content": "blocked"})
        assert denied.status_code == 403
    finally:
        client.put(f"/api/roles/{agent_role['id']}/permissions", headers=factory.admin, json={"permissions": original})
    assert client.post("/api/memories", headers=agent["headers"], json={"title": "allowed again", "content": "ok"}).status_code == 201


def test_admin_role_cannot_be_locked_out(client, factory):
    roles = {r["name"]: r for r in client.get("/api/roles", headers=factory.admin).json()}
    r = client.put(f"/api/roles/{roles['ADMIN']['id']}/permissions", headers=factory.admin, json={"permissions": ["memory:read"]})
    assert r.status_code == 422


def test_admin_cannot_demote_self(client, factory):
    me = client.get("/api/auth/me", headers=factory.admin).json()
    r = client.patch(f"/api/users/{me['id']}", headers=factory.admin, json={"role": "READ_ONLY"})
    assert r.status_code == 403


def test_suspended_agent_cannot_authenticate(client, factory):
    team = factory.team()
    agent = factory.agent(team)
    assert client.patch(f"/api/agents/{agent['id']}", headers=factory.admin, json={"status": "suspended"}).status_code == 200
    assert client.get("/api/auth/me", headers=agent["headers"]).status_code == 401
