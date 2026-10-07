"""Cross-cutting behaviour: error format, rate limiting, health, dashboard, audit filters."""
from __future__ import annotations

import uuid

from app.core.config import get_settings


def test_error_format_is_consistent(client, org):
    h = org["agents"]["dev"]["headers"]
    not_found = client.get(f"/api/memories/{uuid.uuid4()}", headers=h)
    assert not_found.status_code == 404
    err = not_found.json()["error"]
    assert set(err) >= {"code", "message", "request_id"} and err["code"] == "not_found"
    assert not_found.headers["X-Request-ID"] == err["request_id"]
    bad = client.get("/api/memories/not-a-uuid", headers=h)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
    assert "Traceback" not in bad.text


def test_request_id_propagation(client):
    r = client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"
    r2 = client.get("/health/live", headers={"X-Request-ID": "<script>"})
    assert r2.headers["X-Request-ID"] != "<script>"


def test_security_headers(client):
    r = client.get("/health/live")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_rate_limiting_on_auth_and_search(client, factory, org):
    settings = get_settings()
    settings.rate_limit_enabled = True
    old_auth, old_search = settings.rate_limit_auth_per_minute, settings.rate_limit_search_per_minute
    settings.rate_limit_auth_per_minute, settings.rate_limit_search_per_minute = 3, 2
    try:
        codes = [client.post("/api/auth/login", json={"username": "nobody", "password": "Wrong-Passw0rd!"}).status_code for _ in range(5)]
        assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
        limited = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
        assert limited.json()["error"]["code"] == "rate_limited" and int(limited.headers["Retry-After"]) >= 1
        h = org["agents"]["qa"]["headers"]
        search_codes = [client.post("/api/memories/search", headers=h, json={"query": "x"}).status_code for _ in range(3)]
        assert search_codes == [200, 200, 429]
    finally:
        settings.rate_limit_enabled = False
        settings.rate_limit_auth_per_minute, settings.rate_limit_search_per_minute = old_auth, old_search


def test_health_reports_components(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["components"]["database"] == "up" and body["components"]["vector_db"] == "up"
    assert body["components"]["embedding_model"]["loaded"] is True


def test_metrics_endpoint(client):
    r = client.get("/metrics")
    assert r.status_code == 200 and "sml_http_requests_total" in r.text


def test_dashboard_stats_reflect_activity(client, factory, org):
    before = client.get("/api/dashboard/stats", headers=factory.admin).json()
    factory.memory(org["agents"]["dev"], visibility="organization")
    factory.search(org["agents"]["qa"], "anything at all")
    client.get(f"/api/memories/{uuid.uuid4()}", headers=org["agents"]["qa"]["headers"])
    after = client.get("/api/dashboard/stats", headers=factory.admin).json()
    assert after["counts"]["memories"] == before["counts"]["memories"] + 1
    assert after["counts"]["memories_today"] == before["counts"]["memories_today"] + 1
    assert after["counts"]["searches_today"] == before["counts"]["searches_today"] + 1
    assert after["counts"]["teams"] >= 4 and after["counts"]["agents"] >= 4
    assert len(after["activity_7d"]) == 7
    assert after["health"]["database"] == "up"


def test_audit_log_filters_and_scoping(client, factory, org):
    manager = factory.user("TEAM_MANAGER", org["teams"]["dev"])
    factory.memory(org["agents"]["dev"])
    factory.memory(org["agents"]["qa"])
    items = client.get("/api/audit-logs", headers=manager["headers"], params={"page_size": 200}).json()["items"]
    assert items and all(i["actor_team_id"] == org["teams"]["dev"]["id"] for i in items)
    created = factory.audit(action="MEMORY_CREATE", status="success")
    assert all(i["action"] == "MEMORY_CREATE" for i in created)


def test_openapi_documents_required_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in [
        "/api/auth/register", "/api/auth/login", "/api/auth/refresh", "/api/auth/logout",
        "/api/agents", "/api/agents/{agent_id}", "/api/teams", "/api/teams/{team_id}",
        "/api/memories", "/api/memories/{memory_id}", "/api/memories/search",
        "/api/memories/{memory_id}/versions", "/api/memories/{memory_id}/versions/{version}",
        "/api/memories/{memory_id}/share", "/api/memories/{memory_id}/share/{target}",
        "/api/audit-logs", "/api/dashboard/stats", "/health",
    ]:
        assert path in paths, path
