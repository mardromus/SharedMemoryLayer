"""HTTP middleware: request ids, access logging, metrics and security headers."""
from __future__ import annotations

import logging
import re
import time
import uuid

from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import log_extra, request_id_var

logger = logging.getLogger("app.access")

REQUESTS = Counter("sml_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram(
    "sml_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-_.]{1,64}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            REQUESTS.labels(request.method, route_path, str(status)).inc()
            LATENCY.labels(request.method, route_path).observe(elapsed)
            if not request.url.path.startswith("/health"):
                logger.info(
                    "%s %s %s",
                    request.method,
                    request.url.path,
                    status,
                    extra=log_extra(status=status, duration_ms=round(elapsed * 1000, 2), route=route_path),
                )
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        return response
