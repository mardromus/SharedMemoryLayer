"""Authentication: login, lockout, JWT validation, refresh rotation, API keys, registration."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings
from tests.conftest import STRONG_PASSWORD, bearer, unique


def test_login_success_and_me(client, factory):
    user = factory.user("READ_ONLY")
    me = client.get("/api/auth/me", headers=user["headers"])
    assert me.status_code == 200
    body = me.json()
    assert body["kind"] == "user"
    assert body["roles"] == ["READ_ONLY"]
    assert body["permissions"] == ["memory:read"]


def test_login_wrong_password_is_generic_and_audited(client, factory):
    user = factory.user("READ_ONLY")
    r = client.post("/api/auth/login", json={"username": user["username"], "password": "Wrong-Passw0rd!"})
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "Invalid username or password"
    unknown = client.post("/api/auth/login", json={"username": unique("ghost"), "password": "Wrong-Passw0rd!"})
    assert unknown.status_code == 401
    assert unknown.json()["error"]["message"] == r.json()["error"]["message"]  # no user enumeration
    events = factory.audit(action="AUTH_LOGIN_FAILED", actor=user["username"])
    assert events and events[0]["reason"] == "Invalid password"


def test_account_lockout_after_repeated_failures(client, factory):
    user = factory.user("READ_ONLY")
    for _ in range(get_settings().max_failed_logins):
        client.post("/api/auth/login", json={"username": user["username"], "password": "Wrong-Passw0rd!"})
    locked = client.post("/api/auth/login", json={"username": user["username"], "password": STRONG_PASSWORD})
    assert locked.status_code == 401
    assert "locked" in locked.json()["error"]["message"]


def test_missing_and_invalid_jwt(client):
    assert client.get("/api/auth/me").status_code == 401
    r = client.get("/api/auth/me", headers=bearer("not-a-jwt"))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_jwt_with_wrong_signature_rejected(client, factory):
    user = factory.user("READ_ONLY")
    s = get_settings()
    forged = jwt.encode(
        {"sub": f"user:{user['id']}", "typ": "access", "iss": s.jwt_issuer, "iat": int(time.time()),
         "exp": int(time.time()) + 600, "jti": "x"},
        "attacker-secret-attacker-secret-attacker-secret",
        algorithm="HS256",
    )
    assert client.get("/api/auth/me", headers=bearer(forged)).status_code == 401


def test_alg_none_token_rejected(client, factory):
    user = factory.user("ADMIN")
    s = get_settings()
    unsigned = jwt.encode(
        {"sub": f"user:{user['id']}", "typ": "access", "iss": s.jwt_issuer, "iat": int(time.time()),
         "exp": int(time.time()) + 600, "jti": "x"},
        key=None,
        algorithm="none",
    )
    assert client.get("/api/auth/me", headers=bearer(unsigned)).status_code == 401


def test_expired_jwt_rejected(client, factory):
    user = factory.user("READ_ONLY")
    s = get_settings()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = jwt.encode(
        {"sub": f"user:{user['id']}", "typ": "access", "iss": s.jwt_issuer, "iat": int(past.timestamp()) - 60,
         "exp": int(past.timestamp()), "jti": "x"},
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )
    r = client.get("/api/auth/me", headers=bearer(expired))
    assert r.status_code == 401
    assert "expired" in r.json()["error"]["message"].lower()
    assert factory.audit(action="AUTH_INVALID_TOKEN")  # invalid tokens are audited


def test_token_for_unknown_subject_rejected(client):
    s = get_settings()
    token = jwt.encode(
        {"sub": f"user:{uuid.uuid4()}", "typ": "access", "iss": s.jwt_issuer, "iat": int(time.time()),
         "exp": int(time.time()) + 600, "jti": "x"},
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_refresh_rotation_and_reuse_detection(client, factory):
    user = factory.user("READ_ONLY")
    first = user["tokens"]["refresh_token"]
    r1 = client.post("/api/auth/refresh", json={"refresh_token": first})
    assert r1.status_code == 200
    second = r1.json()["refresh_token"]
    assert second != first
    # Re-using the rotated token revokes the entire token family
    assert client.post("/api/auth/refresh", json={"refresh_token": first}).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": second}).status_code == 401


def test_logout_revokes_refresh_token(client, factory):
    user = factory.user("READ_ONLY")
    token = user["tokens"]["refresh_token"]
    assert client.post("/api/auth/logout", json={"refresh_token": token}).status_code == 204
    assert client.post("/api/auth/refresh", json={"refresh_token": token}).status_code == 401


def test_agent_api_key_and_token_exchange(client, factory):
    team = factory.team()
    agent = factory.agent(team)
    me = client.get("/api/auth/me", headers=agent["headers"])
    assert me.status_code == 200 and me.json()["kind"] == "agent"
    exchanged = client.post("/api/auth/agent-token", json={"api_key": agent["api_key"]})
    assert exchanged.status_code == 200
    me2 = client.get("/api/auth/me", headers=bearer(exchanged.json()["access_token"]))
    assert me2.json()["id"] == agent["id"]


def test_revoked_api_key_rejected(client, factory):
    team = factory.team()
    agent = factory.agent(team)
    key_id = agent["api_keys"][0]["id"]
    r = client.delete(f"/api/agents/{agent['id']}/api-keys/{key_id}", headers=factory.admin)
    assert r.status_code == 204
    assert client.get("/api/auth/me", headers=agent["headers"]).status_code == 401
    assert client.get("/api/auth/me", headers={"X-API-Key": "sml_000000000000_forged"}).status_code == 401


def test_api_keys_are_stored_hashed(client, factory):
    from sqlalchemy import select

    from app.core.database import session_scope
    from app.models.identity import ApiKey, User

    team = factory.team()
    agent = factory.agent(team)
    with session_scope() as db:
        stored = db.scalar(select(ApiKey).where(ApiKey.agent_id == uuid.UUID(agent["id"])))
        assert agent["api_key"] not in stored.key_hash
        user = db.scalar(select(User).where(User.username == get_settings().admin_username))
        assert user.password_hash.startswith("$argon2id$")


def test_public_registration_disabled_by_default(client):
    r = client.post("/api/auth/register", json={"username": unique("anon"), "password": STRONG_PASSWORD})
    assert r.status_code == 403


def test_self_registration_cannot_escalate_privileges(client):
    settings = get_settings()
    settings.allow_public_registration = True
    try:
        escalate = client.post("/api/auth/register", json={"username": unique("anon"), "password": STRONG_PASSWORD, "role": "ADMIN"})
        assert escalate.status_code == 403
        ok = client.post("/api/auth/register", json={"username": unique("anon"), "password": STRONG_PASSWORD})
        assert ok.status_code == 201
        assert ok.json()["role"] == "READ_ONLY"
    finally:
        settings.allow_public_registration = False


def test_password_policy_enforced(client, factory):
    r = client.post("/api/auth/register", headers=factory.admin, json={"username": unique("weak"), "password": "password"})
    assert r.status_code == 422
    assert "password" in r.json()["error"]["details"]


def test_lockout_can_be_disabled(client, factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_failed_logins", 0)
    user = factory.user("READ_ONLY")
    for _ in range(8):
        client.post("/api/auth/login", json={"username": user["username"], "password": "Wrong-Passw0rd!"})
    ok = client.post("/api/auth/login", json={"username": user["username"], "password": STRONG_PASSWORD})
    assert ok.status_code == 200
