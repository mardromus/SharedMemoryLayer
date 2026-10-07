"""Embedding providers.

* ``fastembed`` (default) - runs BAAI/bge-small-en-v1.5 locally through ONNX
  Runtime; no network or API key needed at runtime (model is baked into the
  Docker image at build time).
* ``ollama``   - uses a local Ollama server (e.g. ``nomic-embed-text``).
* ``openai``   - OpenAI-compatible ``/embeddings`` endpoint.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import Protocol

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    model_name: str

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FastEmbedProvider:
    def __init__(self, model_name: str, cache_dir: str | None = None):
        from fastembed import TextEmbedding  # imported lazily: heavy

        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
        self._lock = threading.Lock()  # ONNX session is not guaranteed re-entrant
        self._dimension = len(self.embed(["dimension probe"])[0])

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            return [_normalise(v.tolist()) for v in self._model.embed(texts)]


class OllamaEmbeddingProvider:
    def __init__(self, model_name: str, base_url: str, timeout: float = 60.0):
        self.model_name = model_name
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._dimension = len(self.embed(["dimension probe"])[0])

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.post("/api/embed", json={"model": self.model_name, "input": texts})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Ollama embedding failed: {exc}") from exc
        return [_normalise(v) for v in response.json()["embeddings"]]


class OpenAIEmbeddingProvider:
    def __init__(self, model_name: str, api_key: str, base_url: str, timeout: float = 60.0):
        if not api_key:
            raise EmbeddingError("OPENAI_API_KEY is required for EMBEDDING_PROVIDER=openai")
        self.model_name = model_name
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, headers={"Authorization": f"Bearer {api_key}"}
        )
        self._dimension = len(self.embed(["dimension probe"])[0])

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.post("/embeddings", json={"model": self.model_name, "input": texts})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"OpenAI embedding failed: {exc}") from exc
        data = sorted(response.json()["data"], key=lambda d: d["index"])
        return [_normalise(d["embedding"]) for d in data]


_provider: EmbeddingProvider | None = None
_provider_lock = threading.Lock()


def build_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "fastembed":
        return FastEmbedProvider(settings.embedding_model, settings.embedding_cache_dir)
    if settings.embedding_provider == "ollama":
        return OllamaEmbeddingProvider(settings.embedding_model, settings.ollama_base_url, settings.llm_timeout_seconds)
    if settings.embedding_provider == "openai":
        return OpenAIEmbeddingProvider(
            settings.embedding_model, settings.openai_api_key or "", settings.openai_base_url, settings.llm_timeout_seconds
        )
    raise EmbeddingError(f"Unknown embedding provider {settings.embedding_provider}")


def get_embedder() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        with _provider_lock:
            if _provider is None:
                settings = get_settings()
                logger.info("loading embedding model %s via %s", settings.embedding_model, settings.embedding_provider)
                _provider = build_provider(settings)
    return _provider


def embedding_text(title: str, content: str, tags: list[str] | None = None) -> str:
    """Canonical text that is embedded for a memory."""
    text = f"{title.strip()}\n{content.strip()}"
    if tags:
        text += "\nTags: " + ", ".join(tags)
    return text
