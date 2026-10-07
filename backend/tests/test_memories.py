"""Memory lifecycle: creation, validation, storage in both stores, versioning, archive, expiry."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.core.database import session_scope
from app.models.memory import Memory, MemoryVersion
from app.vector import get_vector_store
from tests.conftest import unique


def test_create_memory_persists_to_postgres_and_qdrant(client, factory, org):
    dev = org["agents"]["dev"]
    body = {
        "title": "JWT authentication issue",
        "content": "Authentication failed because refresh tokens expired.",
        "memory_type": "learning",
        "visibility": "team",
        "tags": ["Authentication", "jwt", "security"],
        "team_id": org["teams"]["dev"]["id"],
        "agent_id": dev["id"],
        "importance": 0.8,
        "confidence": 0.9,
        "source_ref": "task #1287",
        "metadata": {"ticket": "AUTH-42"},
    }
    r = client.post("/api/memories", headers=dev["headers"], json=body)
    assert r.status_code == 201, r.text
    memory = r.json()["memory"]
    assert memory["version"] == 1
    assert memory["tags"] == ["authentication", "jwt", "security"]  # normalised
    assert memory["creator_agent"]["id"] == dev["id"]
    assert memory["team"]["id"] == org["teams"]["dev"]["id"]
    assert memory["embedding_id"] == memory["id"]
    assert memory["embedding_model"] == "BAAI/bge-small-en-v1.5"

    # Vector stored with the payload used for permission filtering
    payload = get_vector_store().get_payload(memory["id"])
    assert payload is not None
    assert payload["team_id"] == org["teams"]["dev"]["id"]
    assert payload["visibility"] == "team"
    assert payload["creator_agent_id"] == dev["id"]

    with session_scope() as db:
        row = db.get(Memory, uuid.UUID(memory["id"]))
        assert row is not None and row.extra == {"ticket": "AUTH-42"}
        versions = db.scalars(select(MemoryVersion).where(MemoryVersion.memory_id == row.id)).all()
        assert [v.version_number for v in versions] == [1]

    events = factory.audit(action="MEMORY_CREATE", resource_id=memory["id"])
    assert events and events[0]["status"] == "success" and events[0]["actor_name"] == dev["name"]


def test_agent_cannot_impersonate_other_agent_or_team(client, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    r = client.post("/api/memories", headers=dev["headers"], json={"title": "spoof", "content": "x", "agent_id": qa["id"]})
    assert r.status_code == 403
    r2 = client.post("/api/memories", headers=dev["headers"], json={"title": "spoof", "content": "x", "team_id": org["teams"]["qa"]["id"]})
    assert r2.status_code == 403


def test_validation_errors(client, org):
    h = org["agents"]["dev"]["headers"]
    cases = [
        {"title": "x", "content": "too short title"},
        {"title": "A" * 201, "content": "too long title"},
        {"title": "<script>alert(1)</script>", "content": "xss in title"},
        {"title": "bad type", "content": "x", "memory_type": "gossip"},
        {"title": "bad visibility", "content": "x", "visibility": "everyone"},
        {"title": "bad tag", "content": "x", "tags": ["no spaces allowed!"]},
        {"title": "too many tags", "content": "x", "tags": [f"t{i}" for i in range(25)]},
        {"title": "bad score", "content": "x", "confidence": 1.5},
        {"title": "past expiry", "content": "x", "expires_at": "2000-01-01T00:00:00Z"},
        {"title": "big metadata", "content": "x", "metadata": {"blob": "x" * 9000}},
        {"title": "bad uuid", "content": "x", "project_id": "not-a-uuid"},
        {"title": "confidential org", "content": "x", "visibility": "organization", "sensitivity": "confidential"},
    ]
    for body in cases:
        r = client.post("/api/memories", headers=h, json=body)
        assert r.status_code == 422, (body, r.text)
        assert r.json()["error"]["code"] == "validation_error"


def test_content_size_limit(client, org):
    r = client.post("/api/memories", headers=org["agents"]["dev"]["headers"], json={"title": "huge", "content": "x" * 20001})
    assert r.status_code == 422


def test_sql_injection_is_inert(client, factory, org):
    dev = org["agents"]["dev"]
    payload = "'; DROP TABLE memories; --"
    m = factory.memory(dev, title="Injection attempt memory", content=payload)
    assert m["memory"]["content"] == payload
    r = client.get("/api/memories", headers=dev["headers"], params={"q": "' OR 1=1 --"})
    assert r.status_code == 200
    with session_scope() as db:
        assert db.execute(text("SELECT count(*) FROM memories")).scalar() > 0


def test_update_creates_new_version_and_reembeds(client, factory, org):
    dev = org["agents"]["dev"]
    created = factory.memory(dev, title="Database choice", content="MongoDB was selected.", memory_type="decision")["memory"]
    mid = created["id"]
    r = client.patch(f"/api/memories/{mid}", headers=dev["headers"], json={"content": "PostgreSQL was selected.", "change_description": "switch to PostgreSQL"})
    assert r.status_code == 200, r.text
    assert r.json()["memory"]["version"] == 2
    r = client.patch(f"/api/memories/{mid}", headers=dev["headers"], json={"content": "PostgreSQL + Redis was selected.", "change_description": "add Redis", "expected_version": 2})
    assert r.status_code == 200
    assert r.json()["memory"]["version"] == 3

    versions = client.get(f"/api/memories/{mid}/versions", headers=dev["headers"]).json()
    assert [v["version_number"] for v in versions] == [3, 2, 1]
    assert [v["content"] for v in versions] == ["PostgreSQL + Redis was selected.", "PostgreSQL was selected.", "MongoDB was selected."]
    assert versions[0]["is_current"] and not versions[1]["is_current"]
    assert versions[1]["previous_version_id"] == versions[2]["id"]
    assert versions[0]["change_description"] == "add Redis"
    assert versions[0]["author"]["name"] == dev["name"]

    v1 = client.get(f"/api/memories/{mid}/versions/1", headers=dev["headers"])
    assert v1.status_code == 200 and v1.json()["content"] == "MongoDB was selected."
    assert client.get(f"/api/memories/{mid}/versions/9", headers=dev["headers"]).status_code == 404

    assert get_vector_store().get_payload(mid)["version"] == 3


def test_optimistic_concurrency_conflict(client, factory, org):
    dev = org["agents"]["dev"]
    mid = factory.memory(dev)["memory"]["id"]
    r = client.patch(f"/api/memories/{mid}", headers=dev["headers"], json={"importance": 0.9, "expected_version": 5})
    assert r.status_code == 409
    assert r.json()["error"]["details"]["current_version"] == 1


def test_update_requires_changes(client, factory, org):
    dev = org["agents"]["dev"]
    m = factory.memory(dev)["memory"]
    r = client.patch(f"/api/memories/{m['id']}", headers=dev["headers"], json={"title": m["title"]})
    assert r.status_code == 422


def test_only_owner_or_manager_can_update(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    m = factory.memory(dev, visibility="organization")["memory"]
    r = client.patch(f"/api/memories/{m['id']}", headers=qa["headers"], json={"content": "hijacked"})
    assert r.status_code == 403
    manager = factory.user("TEAM_MANAGER", org["teams"]["dev"])
    ok = client.patch(f"/api/memories/{m['id']}", headers=manager["headers"], json={"content": "manager edit"})
    assert ok.status_code == 200
    assert ok.json()["memory"]["version"] == 2


def test_archive_hides_memory_but_admin_keeps_access(client, factory, org):
    dev = org["agents"]["dev"]
    manager = factory.user("TEAM_MANAGER", org["teams"]["dev"])
    m = factory.memory(dev, visibility="organization", title=unique("archivable"))["memory"]
    # Agents lack memory:delete by default
    assert client.delete(f"/api/memories/{m['id']}", headers=dev["headers"]).status_code == 403
    assert client.delete(f"/api/memories/{m['id']}", headers=manager["headers"]).status_code == 204
    assert client.get(f"/api/memories/{m['id']}", headers=dev["headers"]).status_code == 403
    admin_view = client.get(f"/api/memories/{m['id']}", headers=factory.admin)
    assert admin_view.status_code == 200 and admin_view.json()["status"] == "archived"
    assert get_vector_store().get_payload(m["id"])["status"] == "archived"
    results = factory.search(dev, m["title"], similarity_threshold=0.0, top_k=50)["results"]
    assert m["id"] not in {x["memory_id"] for x in results}
    restored = client.post(f"/api/memories/{m['id']}/restore", headers=factory.admin)
    assert restored.status_code == 200 and restored.json()["status"] == "active"


def test_permanent_delete_admin_only(client, factory, org):
    dev = org["agents"]["dev"]
    manager = factory.user("TEAM_MANAGER", org["teams"]["dev"])
    m = factory.memory(dev)["memory"]
    assert client.delete(f"/api/memories/{m['id']}", headers=manager["headers"], params={"permanent": True}).status_code == 403
    assert client.delete(f"/api/memories/{m['id']}", headers=factory.admin, params={"permanent": True}).status_code == 204
    assert client.get(f"/api/memories/{m['id']}", headers=factory.admin).status_code == 404
    assert get_vector_store().get_payload(m["id"]) is None


def test_expired_memories_hidden_unless_requested(client, factory, org):
    dev = org["agents"]["dev"]
    title = unique("expiring workaround")
    m = factory.memory(dev, title=title, content="Temporary workaround for the invoice export outage.", visibility="organization",
                       expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())["memory"]
    # Force expiry in both stores (as time passing would)
    with session_scope() as db:
        row = db.get(Memory, uuid.UUID(m["id"]))
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        from app.security.access import memory_payload

        get_vector_store().set_payload(m["id"], memory_payload(row))

    qa = org["agents"]["qa"]
    default = factory.search(qa, "invoice export outage workaround", similarity_threshold=0.0, top_k=50)
    assert m["id"] not in {r["memory_id"] for r in default["results"]}
    explicit = factory.search(qa, "invoice export outage workaround", similarity_threshold=0.0, top_k=50, include_expired=True)
    assert m["id"] in {r["memory_id"] for r in explicit["results"]}
    assert client.get(f"/api/memories/{m['id']}", headers=qa["headers"]).status_code == 404
    assert client.get(f"/api/memories/{m['id']}", headers=qa["headers"], params={"include_expired": True}).status_code == 200


def test_feedback_updates_reliability(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    m = factory.memory(dev, visibility="organization")["memory"]
    r = client.post(f"/api/memories/{m['id']}/feedback", headers=qa["headers"], json={"rating": 5, "helpful": True})
    assert r.status_code == 201
    assert r.json()["reliability"] > 0.5


def test_verification_by_manager_only(client, factory, org):
    dev = org["agents"]["dev"]
    m = factory.memory(dev, visibility="organization")["memory"]
    other_manager = factory.user("TEAM_MANAGER", org["teams"]["qa"])
    assert client.post(f"/api/memories/{m['id']}/verify", headers=dev["headers"], json={"status": "verified"}).status_code == 403
    assert client.post(f"/api/memories/{m['id']}/verify", headers=other_manager["headers"], json={"status": "verified"}).status_code == 403
    manager = factory.user("TEAM_MANAGER", org["teams"]["dev"])
    r = client.post(f"/api/memories/{m['id']}/verify", headers=manager["headers"], json={"status": "verified", "note": "checked"})
    assert r.status_code == 200 and r.json()["verification_status"] == "verified"
    rejected = client.post(f"/api/memories/{m['id']}/verify", headers=manager["headers"], json={"status": "rejected"})
    assert rejected.json()["verification_status"] == "rejected"
    # rejected memories are excluded from default search
    results = factory.search(dev, m["title"], similarity_threshold=0.0, top_k=50)["results"]
    assert m["id"] not in {x["memory_id"] for x in results}


def test_provenance(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    m = factory.memory(dev, visibility="organization", source_ref="Agent task #1287")["memory"]
    client.patch(f"/api/memories/{m['id']}", headers=dev["headers"], json={"importance": 0.9, "change_description": "raise importance"})
    client.get(f"/api/memories/{m['id']}", headers=qa["headers"])
    owner_view = client.get(f"/api/memories/{m['id']}/provenance", headers=dev["headers"]).json()
    assert owner_view["created_by"]["name"] == dev["name"]
    assert owner_view["team"]["id"] == org["teams"]["dev"]["id"]
    assert owner_view["source_ref"] == "Agent task #1287"
    assert owner_view["current_version"] == 2
    assert owner_view["access_summary"]["direct_reads"] >= 1
    assert any(e["actor"] == qa["name"] and e["action"] == "MEMORY_READ" for e in owner_view["access_history"])
    # Cross-team readers get provenance without the owner-side access history / source reference
    reader_view = client.get(f"/api/memories/{m['id']}/provenance", headers=qa["headers"]).json()
    assert reader_view["access_history"] is None and reader_view["source_ref"] is None


def test_audit_log_is_append_only(client, factory):
    import pytest
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        with session_scope() as db:
            db.execute(text("UPDATE audit_logs SET action = 'TAMPERED'"))
    with pytest.raises(DBAPIError):
        with session_scope() as db:
            db.execute(text("DELETE FROM audit_logs"))
