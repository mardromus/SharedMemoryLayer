"""Semantic duplicate detection and contradiction (conflict) detection + resolution."""
from __future__ import annotations

from app.services.conflict_rules import detect_contradiction
from tests.conftest import unique


def test_exact_and_semantic_duplicates_flagged_not_deleted(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    topic = unique("ledger")
    original = factory.memory(dev, title=f"Nightly {topic} reconciliation job times out",
                              content=f"The nightly {topic} reconciliation job times out after 30 minutes because it loads all rows into memory.",
                              visibility="organization")["memory"]
    exact = client.post("/api/memories", headers=qa["headers"], json={"title": original["title"], "content": original["content"]})
    assert exact.status_code == 201
    warnings = exact.json()["warnings"]
    assert warnings["message"].startswith("Potential duplicate memories found.")
    dup = next(d for d in warnings["duplicates"] if d["memory_id"] == original["id"])
    assert dup["exact_match"] is True and dup["similarity"] == 1.0

    paraphrase = client.post("/api/memories", headers=qa["headers"], json={
        "title": f"Nightly {topic} reconciliation job is timing out",
        "content": f"The nightly {topic} reconciliation job times out after 30 minutes since it loads every row into memory.",
    })
    ids = {d["memory_id"] for d in paraphrase.json()["warnings"]["duplicates"]}
    assert original["id"] in ids
    # Nothing was deleted: the original is still there
    assert client.get(f"/api/memories/{original['id']}", headers=dev["headers"]).status_code == 200
    assert factory.audit(action="DUPLICATE_DETECTED", actor=qa["name"])


def test_distinct_memory_not_flagged(client, factory, org):
    dev = org["agents"]["dev"]
    factory.memory(dev, title=unique("kubernetes"), content="Pods are scheduled on the arm64 node pool for cost savings.", visibility="organization")
    r = client.post("/api/memories", headers=dev["headers"], json={"title": "Office coffee machine", "content": "The coffee machine on floor 3 is descaled every Friday."})
    assert r.json()["warnings"]["duplicates"] == []


def test_reject_on_duplicate_returns_409(client, factory, org):
    dev = org["agents"]["dev"]
    m = factory.memory(dev, title=unique("dup"), content="Use connection pooling with at most 20 connections per service instance.")["memory"]
    r = client.post("/api/memories", headers=dev["headers"], json={"title": m["title"], "content": m["content"], "reject_on_duplicate": True})
    assert r.status_code == 409
    assert r.json()["error"]["message"] == "Potential duplicate memories found."
    assert r.json()["error"]["details"]["duplicates"][0]["memory_id"] == m["id"]


def test_preflight_check_endpoint(client, factory, org):
    dev = org["agents"]["dev"]
    m = factory.memory(dev, title=unique("preflight"), content="Feature flags are evaluated server-side with a 30 second cache.")["memory"]
    r = client.post("/api/memories/check", headers=dev["headers"], json={"title": m["title"], "content": m["content"]})
    assert r.status_code == 200
    assert r.json()["duplicates"][0]["memory_id"] == m["id"]


def test_conflict_rules_unit():
    assert detect_contradiction("Database = PostgreSQL", "Database = MongoDB").method == "key_value"
    assert detect_contradiction("We selected PostgreSQL as the primary database.", "MongoDB was selected as the primary database.").method == "decision_choice"
    assert detect_contradiction("Never retry failed payment webhooks.", "Always retry failed payment webhooks.").method == "negation"
    assert detect_contradiction("PostgreSQL + Redis was selected.", "PostgreSQL was selected.") is None  # refinement
    assert detect_contradiction("JWT authentication fails when refresh tokens expire.", "Authentication tests should include expired refresh tokens.") is None
    # Same fix described in different words (regression: a "Fix:" label is not a single-valued fact)
    assert detect_contradiction(
        "JWT authentication fails when refresh tokens expire. Fix: return 401 token_expired and re-authenticate.",
        "The refresh endpoint returned HTTP 500 when a refresh token expired. Fix: catch the expiry, return 401 with error code token_expired and let the client re-authenticate.",
    ) is None
    assert detect_contradiction("Access token lifetime is 30 minutes in staging.", "Access token lifetime is 15 minutes.").method == "key_value"
    assert detect_contradiction("Expired refresh tokens must return HTTP 401.", "Expired refresh tokens should return HTTP 403.").method == "key_value"


def test_conflict_detected_flagged_and_resolved(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    system = unique("billing")
    existing = factory.memory(dev, title=f"{system} service database", content=f"{system} service database = MongoDB",
                              memory_type="decision", visibility="organization")["memory"]
    r = client.post("/api/memories", headers=qa["headers"], json={
        "title": f"{system} service database", "content": f"{system} service database = PostgreSQL", "memory_type": "decision", "visibility": "organization",
    })
    assert r.status_code == 201
    body = r.json()
    new_id = body["memory"]["id"]
    assert body["memory"]["conflict_status"] == "potential_conflict"
    conflict = next(c for c in body["warnings"]["conflicts"] if c["memory_id"] == existing["id"])
    assert conflict["detection_method"] == "key_value"
    assert "mongodb" in conflict["reason"] and "postgresql" in conflict["reason"]
    # Not silently overwritten: both memories still exist
    assert client.get(f"/api/memories/{existing['id']}", headers=dev["headers"]).json()["content"].endswith("MongoDB")

    queue = client.get("/api/conflicts", headers=factory.admin, params={"status": "open"}).json()
    record = next(c for c in queue if c["memory"]["id"] == new_id)
    # Agents cannot resolve conflicts
    assert client.post(f"/api/conflicts/{record['id']}/resolve", headers=qa["headers"], json={"resolution": "keep_new"}).status_code == 403
    resolved = client.post(f"/api/conflicts/{record['id']}/resolve", headers=factory.admin, json={"resolution": "keep_new", "note": "PostgreSQL confirmed"})
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    assert client.get(f"/api/memories/{existing['id']}", headers=factory.admin).json()["status"] == "archived"
    assert client.get(f"/api/memories/{new_id}", headers=factory.admin).json()["conflict_status"] == "resolved"
    assert client.post(f"/api/conflicts/{record['id']}/resolve", headers=factory.admin, json={"resolution": "dismiss"}).status_code == 409


def test_conflict_with_unreadable_memory_is_flagged_but_hidden(client, factory, org):
    sec, support = org["agents"]["sec"], org["agents"]["support"]
    setting = unique("vpn")
    factory.memory(sec, title=f"{setting} gateway idle timeout", content=f"{setting} gateway idle timeout is 30 minutes", visibility="team")
    r = client.post("/api/memories", headers=support["headers"], json={
        "title": f"{setting} gateway idle timeout", "content": f"{setting} gateway idle timeout is 8 hours", "visibility": "organization",
    })
    warnings = r.json()["warnings"]
    assert warnings["conflicts"] == []  # details of the Security memory are not disclosed
    assert "cannot access" in warnings["message"]
    assert r.json()["memory"]["conflict_status"] == "potential_conflict"
