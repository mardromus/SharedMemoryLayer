"""Semantic search: real embeddings + Qdrant, filters, thresholds, ranking, latest version."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.conftest import unique


def _project(client, factory, team) -> dict:
    r = client.post("/api/projects", headers=factory.admin, json={"name": unique("proj"), "team_id": team["id"]})
    assert r.status_code == 201
    return r.json()


def _seed_corpus(factory, org, project_id):
    dev, qa, sec, sup = (org["agents"][k] for k in ("dev", "qa", "sec", "support"))
    corpus = {
        "jwt": factory.memory(dev, title="JWT authentication fails when refresh tokens expire",
                              content="Users are logged out because the refresh token expired and the server returned HTTP 500.",
                              memory_type="learning", visibility="organization", project_id=project_id, tags=["authentication"]),
        "css": factory.memory(dev, title="Dark mode colour palette",
                              content="The design system uses slate grey backgrounds and indigo accents in dark mode.",
                              visibility="organization", project_id=project_id, tags=["ui"]),
        "flaky": factory.memory(qa, title="Checkout end-to-end test is flaky",
                                content="The checkout test fails intermittently because it does not wait for tax calculation.",
                                memory_type="incident", visibility="organization", project_id=project_id),
        "sql": factory.memory(sec, title="SQL injection in legacy report endpoint",
                              content="String concatenation in the reports query allowed SQL injection; use bound parameters.",
                              memory_type="incident", visibility="organization", project_id=project_id),
        "invoice": factory.memory(sup, title="Customers cannot download invoices",
                                  content="Invoice PDF download fails for invoices older than two years because they were archived.",
                                  memory_type="incident", visibility="organization", project_id=project_id),
    }
    return {k: v["memory"] for k, v in corpus.items()}


def test_semantic_retrieval_without_keyword_overlap(client, factory, org):
    project = _project(client, factory, org["teams"]["dev"])
    corpus = _seed_corpus(factory, org, project["id"])
    qa = org["agents"]["qa"]
    # No shared keywords with the JWT memory's title - requires semantic matching
    res = factory.search(qa, "why do people get signed out when their session credentials time out?", project_ids=[project["id"]], similarity_threshold=0.0, mode="semantic")
    assert res["mode"] == "semantic"
    assert res["results"][0]["memory_id"] == corpus["jwt"]["id"]
    res2 = factory.search(qa, "database queries built by concatenating strings are exploitable", project_ids=[project["id"]], similarity_threshold=0.0)
    assert res2["results"][0]["memory_id"] == corpus["sql"]["id"]


def test_search_result_format(client, factory, org):
    project = _project(client, factory, org["teams"]["dev"])
    corpus = _seed_corpus(factory, org, project["id"])
    res = factory.search(org["agents"]["qa"], "authentication failures caused by expired tokens", project_ids=[project["id"]])
    top = res["results"][0]
    assert top["memory_id"] == corpus["jwt"]["id"]
    for field in ("title", "content", "similarity", "team", "memory_type", "confidence", "version", "cross_team"):
        assert field in top
    assert top["team"] == org["teams"]["dev"]["name"] and top["cross_team"] is True
    assert 0 < top["similarity"] <= 1
    assert res["query"] == "authentication failures caused by expired tokens"
    assert res["took_ms"] >= 0


def test_top_k_and_threshold(client, factory, org):
    project = _project(client, factory, org["teams"]["dev"])
    _seed_corpus(factory, org, project["id"])
    qa = org["agents"]["qa"]
    assert len(factory.search(qa, "software problem", project_ids=[project["id"]], similarity_threshold=0.0, top_k=2)["results"]) == 2
    assert factory.search(qa, "recipe for a chocolate cake with strawberries", project_ids=[project["id"]], similarity_threshold=0.75)["results"] == []
    all_results = factory.search(qa, "software problem", project_ids=[project["id"]], similarity_threshold=0.0, top_k=10)["results"]
    assert all(r["similarity"] >= 0.0 for r in all_results)
    scores = [r["score"] for r in all_results]
    assert scores == sorted(scores, reverse=True)


def test_metadata_filters(client, factory, org):
    project = _project(client, factory, org["teams"]["dev"])
    corpus = _seed_corpus(factory, org, project["id"])
    qa = org["agents"]["qa"]
    base = {"project_ids": [project["id"]], "similarity_threshold": 0.0, "top_k": 20}
    incidents = factory.search(qa, "problem", memory_types=["incident"], **base)["results"]
    assert {r["memory_id"] for r in incidents} == {corpus["flaky"]["id"], corpus["sql"]["id"], corpus["invoice"]["id"]}
    sec_only = factory.search(qa, "problem", team_ids=[org["teams"]["sec"]["id"]], **base)["results"]
    assert {r["memory_id"] for r in sec_only} == {corpus["sql"]["id"]}
    by_agent = factory.search(qa, "problem", agent_ids=[org["agents"]["dev"]["id"]], **base)["results"]
    assert {r["memory_id"] for r in by_agent} == {corpus["jwt"]["id"], corpus["css"]["id"]}
    by_tag = factory.search(qa, "problem", tags=["ui"], **base)["results"]
    assert {r["memory_id"] for r in by_tag} == {corpus["css"]["id"]}
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert factory.search(qa, "problem", created_after=future, **base)["results"] == []
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert factory.search(qa, "problem", created_before=past, **base)["results"] == []


def test_only_own_isolated_mode(client, factory, org):
    project = _project(client, factory, org["teams"]["dev"])
    corpus = _seed_corpus(factory, org, project["id"])
    qa = org["agents"]["qa"]
    own = factory.search(qa, "authentication refresh token expired", project_ids=[project["id"]], similarity_threshold=0.0, only_own=True, top_k=10)["results"]
    assert {r["memory_id"] for r in own} == {corpus["flaky"]["id"]}


def test_search_returns_latest_version(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    project = _project(client, factory, org["teams"]["dev"])
    m = factory.memory(dev, title="Message broker decision", content="RabbitMQ was selected as the message broker.",
                       memory_type="decision", visibility="organization", project_id=project["id"])["memory"]
    client.patch(f"/api/memories/{m['id']}", headers=dev["headers"], json={"content": "Apache Kafka was selected as the message broker for event streaming."})
    res = factory.search(qa, "which message broker did we choose", project_ids=[project["id"]], similarity_threshold=0.0)
    top = res["results"][0]
    assert top["memory_id"] == m["id"]
    assert top["version"] == 2 and "Kafka" in top["content"]


def test_search_is_audited(client, factory, org):
    qa = org["agents"]["qa"]
    marker = unique("audit-query")
    factory.search(qa, marker)
    events = factory.audit(action="MEMORY_SEARCH", actor=qa["name"])
    assert any(e["metadata"].get("query") == marker for e in events)


def test_search_validation(client, org):
    h = org["agents"]["qa"]["headers"]
    assert client.post("/api/memories/search", headers=h, json={"query": ""}).status_code == 422
    assert client.post("/api/memories/search", headers=h, json={"query": "x", "top_k": 500}).status_code == 422
    assert client.post("/api/memories/search", headers=h, json={"query": "x", "similarity_threshold": 2}).status_code == 422


def test_cross_team_reuse_counted(client, factory, org):
    dev, qa = org["agents"]["dev"], org["agents"]["qa"]
    project = _project(client, factory, org["teams"]["dev"])
    m = factory.memory(dev, title="Cache invalidation strategy", content="Invalidate product cache entries on price change events.",
                       visibility="organization", project_id=project["id"])["memory"]
    factory.search(qa, "how do we invalidate cached product prices", project_ids=[project["id"]])
    detail = client.get(f"/api/memories/{m['id']}", headers=dev["headers"]).json()
    assert detail["reuse_count"] >= 1
