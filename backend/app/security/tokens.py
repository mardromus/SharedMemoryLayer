"""JWT access tokens, opaque refresh tokens and agent API keys."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings

API_KEY_PREFIX = "sml"


class TokenError(Exception):
    """Raised for any invalid / expired token."""


@dataclass(frozen=True)
class AccessClaims:
    subject_type: str  # "user" | "agent"
    subject_id: uuid.UUID
    jti: str
    expires_at: datetime


def create_access_token(subject_type: str, subject_id: uuid.UUID, extra: dict | None = None) -> tuple[str, int]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    ttl = timedelta(minutes=settings.jwt_expiration)
    payload = {
        "sub": f"{subject_type}:{subject_id}",
        "typ": "access",
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


def decode_access_token(token: str) -> AccessClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],  # never trust the header's alg
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "typ", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid token") from exc

    if payload.get("typ") != "access":
        raise TokenError("Invalid token type")
    subject_type, _, raw_id = str(payload["sub"]).partition(":")
    if subject_type not in {"user", "agent"}:
        raise TokenError("Invalid token subject")
    try:
        subject_id = uuid.UUID(raw_id)
    except ValueError as exc:
        raise TokenError("Invalid token subject") from exc
    return AccessClaims(
        subject_type=subject_type,
        subject_id=subject_id,
        jti=str(payload["jti"]),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
    )


def _digest(value: str) -> str:
    """Keyed SHA-256 digest used for high-entropy secrets (refresh tokens, API keys)."""
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def new_refresh_token() -> tuple[str, str]:
    """Return (plaintext, digest). Only the digest is persisted."""
    token = secrets.token_urlsafe(48)
    return token, _digest(token)


def hash_refresh_token(token: str) -> str:
    return _digest(token)


def new_api_key() -> tuple[str, str, str]:
    """Return (plaintext_key, prefix, digest).

    Format: ``sml_<prefix>_<secret>``. The prefix is stored in clear to allow an
    indexed lookup; the full key is only stored as a keyed digest.
    """
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    plaintext = f"{API_KEY_PREFIX}_{prefix}_{secret}"
    return plaintext, prefix, _digest(plaintext)


def parse_api_key(api_key: str) -> str | None:
    parts = api_key.split("_", 2)
    if len(parts) != 3 or parts[0] != API_KEY_PREFIX or len(parts[1]) != 12:
        return None
    return parts[1]


def verify_api_key(api_key: str, stored_digest: str) -> bool:
    return hmac.compare_digest(_digest(api_key), stored_digest)
