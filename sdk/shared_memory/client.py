"""Synchronous client for the Shared Memory Layer REST API."""
from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import httpx

from shared_memory.errors import AuthenticationError, MemoryAPIError, RateLimitError, error_for

_RETRYABLE_STATUS = {502, 503, 504}


class Record(dict):
    """A dict that also allows attribute access (``memory.title``)."""

    def __getattr__(self, item: str) -> Any:
        try:
            value = self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc
        return _wrap(value)


def _wrap(value: Any) -> Any:
    if isinstance(value, dict) and not isinstance(value, Record):
        return Record(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


class SearchResults(list):
    """List of result records plus the response envelope (``query``, ``took_ms`` ...)."""

    def __init__(self, payload: dict):
        super().__init__(Record(r) for r in payload.get("results", []))
        self.query: str = payload.get("query", "")
        self.total: int = payload.get("total", len(self))
        self.took_ms: float | None = payload.get("took_ms")
        self.mode: str | None = payload.get("mode")
        self.raw = payload


def _iso(value: datetime | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return value.isoformat()


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


class MemoryClient:
    """Client used by AI agents (API key) or humans (username/password).

    >>> client = MemoryClient("http://localhost:8000", api_key="sml_...")
    >>> client.remember("JWT issue", "Refresh token expired.", memory_type="learning")
    >>> results = client.search("authentication problems caused by expired tokens")
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        *,
        username: str | None = None,
        password: str | None = None,
        access_token: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        http_client: httpx.Client | None = None,
    ):
        if not (api_key or access_token or (username and password)):
            raise ValueError("Provide api_key, access_token, or username and password")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = max_retries
        self._username = username
        self._password = password
        self._access_token = access_token
        self._refresh_token: str | None = None
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(base_url=self.base_url, timeout=timeout)
        if username and password and not access_token:
            self.login(username, password)

    # ------------------------------------------------------------------ plumbing
    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> "MemoryClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "shared-memory-sdk/1.0"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        elif self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _request(self, method: str, path: str, *, json: Any = None, params: dict | None = None, _retry_auth: bool = True) -> Any:
        attempt = 0
        while True:
            try:
                response = self._http.request(method, path, json=json, params=params, headers=self._headers())
            except httpx.TransportError as exc:
                if attempt < self.max_retries:
                    attempt += 1
                    time.sleep(min(2**attempt * 0.25, 4))
                    continue
                raise MemoryAPIError(0, f"Could not reach Shared Memory API: {exc}") from exc

            if response.status_code == 429 and attempt < self.max_retries:
                attempt += 1
                retry_after = float(response.headers.get("Retry-After", "1"))
                time.sleep(min(retry_after, 30))
                continue
            if response.status_code in _RETRYABLE_STATUS and attempt < self.max_retries:
                attempt += 1
                time.sleep(min(2**attempt * 0.25, 4))
                continue
            if response.status_code == 401 and _retry_auth and self._refresh_token:
                if self._try_refresh():
                    return self._request(method, path, json=json, params=params, _retry_auth=False)
            if response.status_code >= 400:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                err = error_for(response.status_code, payload)
                if isinstance(err, RateLimitError):
                    err.retry_after = float(response.headers.get("Retry-After", "0") or 0)
                raise err
            if response.status_code == 204 or not response.content:
                return None
            return _wrap(response.json())

    def request(self, method: str, path: str, *, json: Any = None, params: dict | None = None) -> Any:
        """Low-level access to any API endpoint (e.g. administration)."""
        return self._request(method, path, json=json, params=params)

    # ------------------------------------------------------------------ auth
    def login(self, username: str, password: str) -> Record:
        data = self._request("POST", "/api/auth/login", json={"username": username, "password": password}, _retry_auth=False)
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token")
        return data

    def exchange_api_key(self) -> Record:
        """Trade the agent API key for a short-lived JWT (optional; API keys work directly)."""
        if not self.api_key:
            raise AuthenticationError(401, "No API key configured")
        data = self._request("POST", "/api/auth/agent-token", json={"api_key": self.api_key}, _retry_auth=False)
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token")
        return data

    def _try_refresh(self) -> bool:
        try:
            data = self._request("POST", "/api/auth/refresh", json={"refresh_token": self._refresh_token}, _retry_auth=False)
        except MemoryAPIError:
            self._access_token = None
            return False
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        return True

    def logout(self) -> None:
        if self._refresh_token:
            self._request("POST", "/api/auth/logout", json={"refresh_token": self._refresh_token}, _retry_auth=False)
        self._access_token = None
        self._refresh_token = None

    def whoami(self) -> Record:
        return self._request("GET", "/api/auth/me")

    # ------------------------------------------------------------------ memories
    def remember(
        self,
        title: str,
        content: str,
        memory_type: str = "knowledge",
        *,
        tags: Iterable[str] | None = None,
        visibility: str | None = None,
        sensitivity: str | None = None,
        project_id: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        source: str | None = None,
        source_ref: str | None = None,
        metadata: dict | None = None,
        expires_at: datetime | str | None = None,
        allowed_team_ids: Iterable[str] | None = None,
        allowed_agent_ids: Iterable[str] | None = None,
        parent_memory_id: str | None = None,
        reject_on_duplicate: bool = False,
    ) -> Record:
        """Store a memory. The response contains ``memory`` and ``warnings``
        (potential duplicates / conflicts detected by the server)."""
        body = _clean(
            {
                "title": title,
                "content": content,
                "memory_type": memory_type,
                "tags": list(tags) if tags is not None else None,
                "visibility": visibility,
                "sensitivity": sensitivity,
                "project_id": project_id,
                "importance": importance,
                "confidence": confidence,
                "source": source,
                "source_ref": source_ref,
                "metadata": metadata,
                "expires_at": _iso(expires_at),
                "allowed_team_ids": list(allowed_team_ids) if allowed_team_ids else None,
                "allowed_agent_ids": list(allowed_agent_ids) if allowed_agent_ids else None,
                "parent_memory_id": parent_memory_id,
                "reject_on_duplicate": reject_on_duplicate or None,
            }
        )
        return self._request("POST", "/api/memories", json=body)

    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        similarity_threshold: float | None = None,
        memory_types: Iterable[str] | None = None,
        team_ids: Iterable[str] | None = None,
        agent_ids: Iterable[str] | None = None,
        project_ids: Iterable[str] | None = None,
        tags: Iterable[str] | None = None,
        created_after: datetime | str | None = None,
        created_before: datetime | str | None = None,
        include_expired: bool = False,
        only_own: bool = False,
        mode: str | None = None,
    ) -> SearchResults:
        """``mode``: "hybrid" (server default: meaning + exact keywords), "semantic" or "keyword"."""
        body = _clean(
            {
                "query": query,
                "mode": mode,
                "top_k": top_k,
                "similarity_threshold": similarity_threshold,
                "memory_types": list(memory_types) if memory_types else None,
                "team_ids": list(team_ids) if team_ids else None,
                "agent_ids": list(agent_ids) if agent_ids else None,
                "project_ids": list(project_ids) if project_ids else None,
                "tags": list(tags) if tags else None,
                "created_after": _iso(created_after),
                "created_before": _iso(created_before),
                "include_expired": include_expired or None,
                "only_own": only_own or None,
            }
        )
        return SearchResults(self._request("POST", "/api/memories/search", json=body))

    def remember_many(
        self, items: list[dict], *, skip_duplicates: bool = False, stop_on_error: bool = False, batch_size: int = 100,
        detect_duplicates: bool = True,
    ) -> Record:
        """Bulk import (e.g. from ``shared_memory.importers``). Items are sent in batches of up to 100;
        the combined result has ``created``, ``skipped``, ``failed`` and per-item ``results``."""
        total = Record(created=0, skipped=0, failed=0, results=[])
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            res = self._request("POST", "/api/memories/bulk", json={
                "items": batch, "skip_duplicates": skip_duplicates, "stop_on_error": stop_on_error, "detect_duplicates": detect_duplicates,
            })
            for key in ("created", "skipped", "failed"):
                total[key] += res[key]
            for r in res["results"]:
                r["index"] += start
                total["results"].append(r)
            if stop_on_error and res["failed"]:
                break
        return total

    def get(self, memory_id: str) -> Record:
        return self._request("GET", f"/api/memories/{memory_id}")

    def update(self, memory_id: str, *, change_description: str | None = None, expected_version: int | None = None, **fields: Any) -> Record:
        body = _clean({**fields, "change_description": change_description, "expected_version": expected_version})
        if "expires_at" in body:
            body["expires_at"] = _iso(body["expires_at"])
        return self._request("PATCH", f"/api/memories/{memory_id}", json=body)

    def archive(self, memory_id: str) -> None:
        self._request("DELETE", f"/api/memories/{memory_id}")

    delete = archive

    def share(self, memory_id: str, *, team_ids: Iterable[str] | None = None, agent_ids: Iterable[str] | None = None) -> Record:
        body = {"team_ids": list(team_ids or []), "agent_ids": list(agent_ids or [])}
        return self._request("POST", f"/api/memories/{memory_id}/share", json=body)

    def unshare(self, memory_id: str, target: str) -> Record:
        """``target`` is ``team:<uuid>`` or ``agent:<uuid>``."""
        return self._request("DELETE", f"/api/memories/{memory_id}/share/{target}")

    def versions(self, memory_id: str) -> list[Record]:
        return self._request("GET", f"/api/memories/{memory_id}/versions")

    def version(self, memory_id: str, version: int) -> Record:
        return self._request("GET", f"/api/memories/{memory_id}/versions/{version}")

    def provenance(self, memory_id: str) -> Record:
        return self._request("GET", f"/api/memories/{memory_id}/provenance")

    def feedback(self, memory_id: str, rating: int, helpful: bool = True, comment: str = "") -> Record:
        body = {"rating": rating, "helpful": helpful, "comment": comment}
        return self._request("POST", f"/api/memories/{memory_id}/feedback", json=body)

    def check_duplicates(self, title: str, content: str, tags: Iterable[str] | None = None) -> Record:
        body = _clean({"title": title, "content": content, "tags": list(tags) if tags else None})
        return self._request("POST", "/api/memories/check", json=body)

    # ------------------------------------------------------------------ agent runtime
    def run_task(
        self,
        task: str,
        *,
        top_k: int = 5,
        store_result: bool = False,
        memory_type: str = "task_result",
        project_id: str | None = None,
        similarity_threshold: float | None = None,
        only_own: bool = False,
    ) -> Record:
        """Ask the server-side LLM agent runtime to solve ``task`` using shared memory.

        ``only_own=True`` restricts retrieval to the agent's own memories (isolated-memory mode)."""
        body = _clean({
            "task": task, "top_k": top_k, "store_result": store_result, "result_memory_type": memory_type,
            "project_id": project_id, "similarity_threshold": similarity_threshold, "only_own": only_own or None,
        })
        return self._request("POST", "/api/agent-runtime/tasks", json=body)
