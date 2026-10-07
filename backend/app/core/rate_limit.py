"""Sliding-window rate limiter (in-process).

Limits are configured per *bucket* (auth / search / write) and applied per
caller identity - the authenticated principal when known, otherwise the
client IP. A single backend process keeps the counters in memory; for a
multi-replica deployment swap ``SlidingWindowLimiter`` for a Redis-backed
implementation with the same ``hit`` interface.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import RateLimitedError

WINDOW_SECONDS = 60.0


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: float = WINDOW_SECONDS) -> tuple[bool, int]:
        """Register a hit. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = int(hits[0] + window - now) + 1
                return False, max(retry_after, 1)
            hits.append(now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    # X-Forwarded-For is client-controlled unless a trusted proxy overwrites it,
    # so it is only honoured when TRUST_PROXY_HEADERS=true.
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


def _limit_for(bucket: str) -> int:
    settings = get_settings()
    return {
        "auth": settings.rate_limit_auth_per_minute,
        "search": settings.rate_limit_search_per_minute,
        "write": settings.rate_limit_write_per_minute,
    }[bucket]


def enforce(request: Request, bucket: str, identity: str | None = None) -> None:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    key = f"{bucket}:{identity or client_ip(request)}"
    allowed, retry_after = limiter.hit(key, _limit_for(bucket))
    if not allowed:
        raise RateLimitedError(
            f"Rate limit exceeded for {bucket} requests. Retry in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )


def rate_limit(bucket: str):
    """Dependency for unauthenticated endpoints (keyed by client IP)."""

    def _dependency(request: Request) -> None:
        enforce(request, bucket)

    return _dependency
