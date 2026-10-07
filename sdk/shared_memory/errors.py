"""Exceptions raised by the SDK. They mirror the API's JSON error format."""
from __future__ import annotations

from typing import Any


class MemoryAPIError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        code: str | None = None,
        request_id: str | None = None,
        details: Any = None,
    ):
        super().__init__(f"[{status_code}] {message}")
        self.status_code = status_code
        self.message = message
        self.code = code
        self.request_id = request_id
        self.details = details


class AuthenticationError(MemoryAPIError):
    """401 - missing, invalid or expired credentials."""


class PermissionDeniedError(MemoryAPIError):
    """403 - authenticated but not authorised (e.g. another team's private memory)."""


class NotFoundError(MemoryAPIError):
    """404 - resource does not exist."""


class ConflictError(MemoryAPIError):
    """409 - duplicate rejected, version mismatch, or conflicting state."""


class ValidationError(MemoryAPIError):
    """422 - request body failed validation."""


class RateLimitError(MemoryAPIError):
    """429 - too many requests; ``retry_after`` seconds suggested by the server."""

    retry_after: float | None = None


class ServerError(MemoryAPIError):
    """5xx - the server or one of its dependencies failed."""


_BY_STATUS = {
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: RateLimitError,
}


def error_for(status_code: int, payload: Any) -> MemoryAPIError:
    err = payload.get("error", {}) if isinstance(payload, dict) else {}
    message = err.get("message") or f"HTTP {status_code}"
    cls = _BY_STATUS.get(status_code, ServerError if status_code >= 500 else MemoryAPIError)
    return cls(status_code, message, err.get("code"), err.get("request_id"), err.get("details"))
