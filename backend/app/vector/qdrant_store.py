"""Qdrant implementation of the VectorStore interface."""
from __future__ import annotations

import logging
import warnings

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from app.vector.base import FieldMatch, VectorFilter, VectorHit, VectorStoreError

logger = logging.getLogger(__name__)

_KEYWORD_INDEXES = (
    "memory_id",
    "team_id",
    "creator_agent_id",
    "creator_user_id",
    "visibility",
    "allowed_team_ids",
    "allowed_agent_ids",
    "status",
    "memory_type",
    "project_id",
    "tags",
    "verification_status",
)
_FLOAT_INDEXES = ("created_at_ts", "expires_at_ts", "confidence", "importance")


def _condition(match: FieldMatch) -> qm.FieldCondition:
    if isinstance(match.value, (list, tuple, set)):
        return qm.FieldCondition(key=match.key, match=qm.MatchAny(any=list(match.value)))
    return qm.FieldCondition(key=match.key, match=qm.MatchValue(value=match.value))


def to_qdrant_filter(flt: VectorFilter) -> qm.Filter:
    must: list[qm.Condition] = [_condition(m) for m in flt.must]
    ranges: dict[str, dict[str, float]] = {}
    for bucket, op in ((flt.range_gt, "gt"), (flt.range_gte, "gte"), (flt.range_lte, "lte")):
        for key, value in bucket.items():
            ranges.setdefault(key, {})[op] = value
    for key, bounds in ranges.items():
        must.append(qm.FieldCondition(key=key, range=qm.Range(**bounds)))
    if flt.any_of:
        must.append(qm.Filter(should=[qm.Filter(must=[_condition(m) for m in group]) for group in flt.any_of]))
    return qm.Filter(must=must)


class QdrantVectorStore:
    def __init__(self, url: str, collection: str, api_key: str | None = None, timeout: int = 10):
        self.collection = collection
        with warnings.catch_warnings():
            # Inside the compose network Qdrant is reached over plain HTTP; the API key
            # still protects it from other containers. Use https:// URLs in production.
            warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")
            self.client = QdrantClient(url=url, api_key=api_key or None, timeout=timeout, prefer_grpc=False)

    # -- lifecycle -----------------------------------------------------
    def ensure_collection(self, dimension: int) -> None:
        try:
            if not self.client.collection_exists(self.collection):
                self.client.create_collection(
                    self.collection,
                    vectors_config=qm.VectorParams(size=dimension, distance=qm.Distance.COSINE),
                )
                logger.info("created qdrant collection %s (dim=%s)", self.collection, dimension)
            else:
                info = self.client.get_collection(self.collection)
                params = info.config.params.vectors
                existing = params.size if isinstance(params, qm.VectorParams) else None
                if existing is not None and existing != dimension:
                    raise VectorStoreError(
                        f"Collection {self.collection} has dimension {existing}, embedding model produces "
                        f"{dimension}. Re-index via POST /api/admin/reindex?recreate=true."
                    )
            for key in _KEYWORD_INDEXES:
                self.client.create_payload_index(self.collection, key, qm.PayloadSchemaType.KEYWORD, wait=True)
            for key in _FLOAT_INDEXES:
                self.client.create_payload_index(self.collection, key, qm.PayloadSchemaType.FLOAT, wait=True)
            self.client.create_payload_index(self.collection, "is_private", qm.PayloadSchemaType.BOOL, wait=True)
        except VectorStoreError:
            raise
        except Exception as exc:  # pragma: no cover - depends on infrastructure
            raise VectorStoreError(f"Qdrant unavailable: {exc}") from exc

    def reset(self, dimension: int) -> None:
        try:
            if self.client.collection_exists(self.collection):
                self.client.delete_collection(self.collection)
        except Exception as exc:
            raise VectorStoreError(f"Qdrant unavailable: {exc}") from exc
        self.ensure_collection(dimension)

    # -- writes ----------------------------------------------------------
    def upsert(self, point_id: str, vector: list[float], payload: dict) -> None:
        try:
            self.client.upsert(
                self.collection,
                points=[qm.PointStruct(id=point_id, vector=vector, payload=payload)],
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError(f"Qdrant upsert failed: {exc}") from exc

    def set_payload(self, point_id: str, payload: dict) -> None:
        try:
            self.client.set_payload(self.collection, payload=payload, points=[point_id], wait=True)
        except Exception as exc:
            raise VectorStoreError(f"Qdrant payload update failed: {exc}") from exc

    def delete(self, point_id: str) -> None:
        try:
            self.client.delete(self.collection, points_selector=qm.PointIdsList(points=[point_id]), wait=True)
        except Exception as exc:
            raise VectorStoreError(f"Qdrant delete failed: {exc}") from exc

    # -- reads -----------------------------------------------------------
    def search(
        self, vector: list[float], flt: VectorFilter, limit: int, score_threshold: float | None = None
    ) -> list[VectorHit]:
        try:
            response = self.client.query_points(
                self.collection,
                query=vector,
                query_filter=to_qdrant_filter(flt),
                limit=limit,
                score_threshold=score_threshold,
                with_payload=True,
            )
        except Exception as exc:
            raise VectorStoreError(f"Qdrant search failed: {exc}") from exc
        return [VectorHit(id=str(p.id), score=float(p.score), payload=p.payload or {}) for p in response.points]

    def score_ids(self, vector: list[float], point_ids: list[str]) -> dict[str, float]:
        if not point_ids:
            return {}
        try:
            response = self.client.query_points(
                self.collection,
                query=vector,
                query_filter=qm.Filter(must=[qm.HasIdCondition(has_id=point_ids)]),
                limit=len(point_ids),
                with_payload=False,
            )
        except Exception as exc:
            raise VectorStoreError(f"Qdrant scoring failed: {exc}") from exc
        return {str(p.id): float(p.score) for p in response.points}

    def count(self) -> int:
        try:
            return int(self.client.count(self.collection, exact=True).count)
        except Exception as exc:
            raise VectorStoreError(f"Qdrant count failed: {exc}") from exc

    def exists(self, point_id: str) -> bool:
        try:
            return bool(self.client.retrieve(self.collection, ids=[point_id], with_payload=False))
        except Exception as exc:
            raise VectorStoreError(f"Qdrant retrieve failed: {exc}") from exc

    def get_payload(self, point_id: str) -> dict | None:
        try:
            points = self.client.retrieve(self.collection, ids=[point_id], with_payload=True)
        except Exception as exc:
            raise VectorStoreError(f"Qdrant retrieve failed: {exc}") from exc
        return (points[0].payload or {}) if points else None

    def healthy(self) -> bool:
        try:
            self.client.get_collections()
            return True
        except Exception:
            return False
