"""Visibility levels, sharing and cross-team authorisation (enforced at API + vector level)."""
from __future__ import annotations

import pytest

from tests.conftest import unique


def _can_get(client, principal, memory_id) -> int:
    return client.get(f"/api/memories/{memory_id}", headers=principal["headers"]).status_code


def _found_by_search(factory, principal, memory) -> bool:
    results = factory.search(principal, memory["title"] + " " + memory["content"], similarity_threshold=0.0, top_k=50)["results"]
    return memory["id"] in {r["memory_id"] for r in results}


@pytest.mark.parametrize(
    "visibility, expected",
    [
        # who may read:            creator  teammate  other-team-agent
        ("private",                (True,    False,    False)),
        ("team",                   (True,    True,     False)),
        ("organization",           (True,    True,     True)),
    ],
)
def test_visibility_matrix(client, factory, org, visibility, expected):
    dev = org["agents"]["dev"]
    teammate = factory.agent(org["teams"]["dev"])
    outsider = org["agents"]["support"]
    m = factory.memory(dev, title=unique(f"{visibility} note"), content=f"Visibility test content for {visibility}", visibility=visibility)["memory"]
    for principal, allowed in zip((dev, teammate, outsider), expected):
        status = _can_get(client, principal, m["id"])
        assert status == (200 if allowed else 403), (visibility, principal["name"], status)
        assert _found_by_search(factory, principal, m) is allowed


def test_selected_teams_visibility(client, factory, org):
    dev, qa, sec = org["agents"]["dev"], org["agents"]["qa"], org["agents"]["sec"]
    m = factory.memory(dev, title=unique("selected teams"), content="Shared only with QA", visibility="selected_teams",
                       allowed_team_ids=[org["teams"]["qa"]["id"]])["memory"]
    assert _can_get(client, qa, m["id"]) == 200
    assert _can_get(client, sec, m["id"]) == 403
    assert _found_by_search(factory, qa, m) and not _found_by_search(factory, sec, m)


def test_selected_agents_visibility(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    qa_peer = factory.agent(org["teams"]["qa"])
    dev_peer = factory.agent(org["teams"]["dev"])
    m = factory.memory(dev, title=unique("selected agents"), content="Only for one QA agent", visibility="selected_agents",
                       allowed_agent_ids=[qa["id"]])["memory"]
    assert _can_get(client, qa, m["id"]) == 200
    assert _can_get(client, qa_peer, m["id"]) == 403
    assert _can_get(client, dev_peer, m["id"]) == 403  # not even the owner team
    denied = factory.audit(action="MEMORY_READ", status="denied", resource_id=m["id"])
    assert denied and "allowed agents" in denied[0]["reason"]


def test_explicit_share_and_revoke(client, factory, org):
    """QA agent -> Development memory only when explicitly permitted."""
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    m = factory.memory(dev, title=unique("jwt refresh"), content="JWT authentication fails when refresh tokens expire.",
                       visibility="team", tags=["authentication"])["memory"]
    assert _can_get(client, qa, m["id"]) == 403

    shared = client.post(f"/api/memories/{m['id']}/share", headers=dev["headers"], json={"team_ids": [org["teams"]["qa"]["id"]]})
    assert shared.status_code == 200
    assert [t["name"] for t in shared.json()["allowed_teams"]] == [org["teams"]["qa"]["name"]]
    assert _can_get(client, qa, m["id"]) == 200
    assert _found_by_search(factory, qa, m)
    assert factory.audit(action="MEMORY_SHARE", resource_id=m["id"])

    revoked = client.delete(f"/api/memories/{m['id']}/share/team:{org['teams']['qa']['id']}", headers=dev["headers"])
    assert revoked.status_code == 200 and revoked.json()["allowed_teams"] == []
    assert _can_get(client, qa, m["id"]) == 403
    assert not _found_by_search(factory, qa, m)


def test_only_owner_can_share(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    m = factory.memory(dev, visibility="organization")["memory"]
    r = client.post(f"/api/memories/{m['id']}/share", headers=qa["headers"], json={"team_ids": [org["teams"]["qa"]["id"]]})
    assert r.status_code == 403


def test_private_memory_cannot_be_shared(client, factory, org):
    dev = org["agents"]["dev"]
    m = factory.memory(dev, visibility="private")["memory"]
    r = client.post(f"/api/memories/{m['id']}/share", headers=dev["headers"], json={"team_ids": [org["teams"]["qa"]["id"]]})
    assert r.status_code == 409


def test_team_sharing_policy_enforced(client, factory):
    closed = factory.team(allow_cross_team_sharing=False)
    other = factory.team()
    agent = factory.agent(closed)
    m = factory.memory(agent, visibility="team")["memory"]
    r = client.post(f"/api/memories/{m['id']}/share", headers=agent["headers"], json={"team_ids": [other["id"]]})
    assert r.status_code == 403


def test_cross_team_confidential_access_denied_and_audited(client, factory, org):
    """Support agent -> confidential Security memory => ACCESS DENIED + audit record."""
    sec, support = org["agents"]["sec"], org["agents"]["support"]
    m = factory.memory(sec, title=unique("SSRF in PDF export"), content="Confidential: unpatched SSRF in the PDF export service.",
                       visibility="team", sensitivity="confidential", memory_type="incident")["memory"]
    r = client.get(f"/api/memories/{m['id']}", headers=support["headers"])
    assert r.status_code == 403
    assert r.json()["error"]["message"] == "Access denied: Unauthorized team"
    events = factory.audit(action="MEMORY_READ", status="denied", resource_id=m["id"])
    assert events and events[0]["actor_name"] == support["name"] and events[0]["reason"] == "Unauthorized team"
    # ... and semantic similarity never leaks it
    assert not _found_by_search(factory, support, m)


def test_denied_agent_cannot_update_share_or_version(client, factory, org):
    sec, support = org["agents"]["sec"], org["agents"]["support"]
    m = factory.memory(sec, visibility="team")["memory"]
    assert client.patch(f"/api/memories/{m['id']}", headers=support["headers"], json={"content": "x"}).status_code == 403
    assert client.get(f"/api/memories/{m['id']}/versions", headers=support["headers"]).status_code == 403
    assert client.get(f"/api/memories/{m['id']}/provenance", headers=support["headers"]).status_code == 403
    assert client.post(f"/api/memories/{m['id']}/feedback", headers=support["headers"], json={"rating": 1}).status_code == 403


def test_cross_team_readers_do_not_see_owner_fields(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    m = factory.memory(dev, visibility="organization", source_ref="internal-ticket-99", metadata={"internal": True},
                       allowed_team_ids=[org["teams"]["sec"]["id"]])["memory"]
    owner = client.get(f"/api/memories/{m['id']}", headers=dev["headers"]).json()
    reader = client.get(f"/api/memories/{m['id']}", headers=qa["headers"]).json()
    assert owner["source_ref"] == "internal-ticket-99" and owner["allowed_teams"]
    assert reader["source_ref"] is None and reader["metadata"] is None and reader["allowed_teams"] is None
    assert reader["can_edit"] is False


def test_keyword_listing_respects_permissions(client, factory, org):
    dev, support = org["agents"]["dev"], org["agents"]["support"]
    marker = unique("zebra-marker")
    hidden = factory.memory(dev, title=f"{marker} hidden", visibility="team")["memory"]
    public = factory.memory(dev, title=f"{marker} public", visibility="organization")["memory"]
    items = client.get("/api/memories", headers=support["headers"], params={"q": marker}).json()["items"]
    ids = {i["id"] for i in items}
    assert public["id"] in ids and hidden["id"] not in ids


def test_duplicate_warning_does_not_leak_unreadable_memories(client, factory, org):
    sec, support = org["agents"]["sec"], org["agents"]["support"]
    content = f"Secret incident {unique('x')}: the admin console is exposed on port 9443 without MFA."
    factory.memory(sec, title="Exposed admin console", content=content, visibility="team")
    r = client.post("/api/memories", headers=support["headers"], json={"title": "Exposed admin console", "content": content})
    assert r.status_code == 201
    assert r.json()["warnings"]["duplicates"] == []
