"""Vector store and embedding abstractions."""
from __future__ import annotations

from app.core.config import get_settings
from app.vector.base import VectorStore
from app.vector.qdrant_store import QdrantVectorStore

_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        settings = get_settings()
        _store = QdrantVectorStore(settings.qdrant_url, settings.qdrant_collection, settings.qdrant_api_key)
    return _store


def set_vector_store(store: VectorStore | None) -> None:
    global _store
    _store = store
