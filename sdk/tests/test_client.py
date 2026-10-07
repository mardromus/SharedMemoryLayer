"""Unit tests for the SDK's HTTP behaviour (transport-level; the server is exercised
end-to-end by backend/tests/test_integration_sdk.py)."""
from __future__ import annotations

import json

import httpx
import pytest

from shared_memory import AuthenticationError, MemoryClient, PermissionDeniedError, RateLimitError


def make_client(handler, **kwargs) -> MemoryClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(base_url="http://sml.test", transport=transport)
    return MemoryClient("http://sml.test", http_client=http, **kwargs)


def test_remember_sends_api_key_and_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"memory": {"id": "m1", "version": 1}, "warnings": {"duplicates": [], "conflicts": []}})

    client = make_client(handler, api_key="sml_abc_secret")
    res = client.remember("JWT issue", "Refresh token expired.", memory_type="learning", tags=["jwt"])
    assert seen["headers"]["X-API-Key"] == "sml_abc_secret"
    assert seen["path"] == "/api/memories"
    assert seen["body"] == {"title": "JWT issue", "content": "Refresh token expired.", "memory_type": "learning", "tags": ["jwt"]}
    assert res.memory.id == "m1" and res.warnings.duplicates == []


def test_search_results_have_attribute_access():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["query"] == "expired tokens" and body["top_k"] == 3
        return httpx.Response(200, json={"query": "expired tokens", "mode": "semantic", "total": 1, "took_ms": 12.5,
                                         "results": [{"memory_id": "m1", "title": "JWT", "similarity": 0.91, "team": "Development"}]})

    results = make_client(handler, api_key="k").search("expired tokens", top_k=3)
    assert len(results) == 1 and results.took_ms == 12.5 and results.mode == "semantic"
    assert results[0].title == "JWT" and results[0].similarity == 0.91


def test_error_mapping_keeps_request_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "forbidden", "message": "Access denied: Unauthorized team", "request_id": "req-1"}})

    with pytest.raises(PermissionDeniedError) as exc:
        make_client(handler, api_key="k").get("m1")
    assert exc.value.status_code == 403 and exc.value.request_id == "req-1"
    assert "Unauthorized team" in exc.value.message


def test_rate_limit_is_retried(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json={"id": "me", "kind": "agent"})

    assert make_client(handler, api_key="k").whoami().kind == "agent"
    assert calls["n"] == 3


def test_rate_limit_error_after_retries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    handler = lambda request: httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"message": "slow down"}})  # noqa: E731
    with pytest.raises(RateLimitError) as exc:
        make_client(handler, api_key="k", max_retries=1).whoami()
    assert exc.value.retry_after == 7


def test_login_and_transparent_refresh():
    state = {"refreshed": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "a1", "refresh_token": "r1", "expires_in": 900})
        if path == "/api/auth/refresh":
            assert json.loads(request.content) == {"refresh_token": "r1"}
            state["refreshed"] = True
            return httpx.Response(200, json={"access_token": "a2", "refresh_token": "r2", "expires_in": 900})
        if request.headers.get("Authorization") == "Bearer a1":
            return httpx.Response(401, json={"error": {"message": "Token has expired"}})
        assert request.headers.get("Authorization") == "Bearer a2"
        return httpx.Response(200, json={"id": "u1", "kind": "user"})

    client = make_client(handler, username="admin", password="pw")
    assert client.whoami().id == "u1"
    assert state["refreshed"]


def test_invalid_credentials_raise_authentication_error():
    handler = lambda request: httpx.Response(401, json={"error": {"message": "Invalid username or password"}})  # noqa: E731
    with pytest.raises(AuthenticationError):
        make_client(handler, username="admin", password="bad")


def test_requires_some_credentials():
    with pytest.raises(ValueError):
        MemoryClient("http://sml.test")
