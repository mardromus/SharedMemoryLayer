"""Vector-store abstraction.

Services depend only on ``VectorStore`` and the neutral ``VectorFilter``
description, so Qdrant can be replaced by another engine (pgvector, Weaviate,
Milvus ...) by implementing this interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class FieldMatch:
    """``key == value`` or, when ``value`` is a list, ``key IN value``.

    For array payload fields the condition is true when the array contains the value.
    """

    key: str
    value: Any


@dataclass
class VectorFilter:
    must: list[FieldMatch] = field(default_factory=list)
    # OR over AND-groups: at least one group must fully match
    any_of: list[list[FieldMatch]] = field(default_factory=list)
    range_gt: dict[str, float] = field(default_factory=dict)
    range_gte: dict[str, float] = field(default_factory=dict)
    range_lte: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorHit:
    id: str
    score: float
    payload: dict


class VectorStoreError(RuntimeError):
    """Raised when the vector database is unavailable or rejects an operation."""


class VectorStore(Protocol):
    def ensure_collection(self, dimension: int) -> None: ...

    def upsert(self, point_id: str, vector: list[float], payload: dict) -> None: ...

    def set_payload(self, point_id: str, payload: dict) -> None: ...

    def delete(self, point_id: str) -> None: ...

    def search(
        self, vector: list[float], flt: VectorFilter, limit: int, score_threshold: float | None = None
    ) -> list[VectorHit]: ...

    def score_ids(self, vector: list[float], point_ids: list[str]) -> dict[str, float]:
        """Similarity of ``vector`` to specific points (used for keyword-only hybrid hits)."""
        ...

    def count(self) -> int: ...

    def exists(self, point_id: str) -> bool: ...

    def healthy(self) -> bool: ...

    def reset(self, dimension: int) -> None: ...
