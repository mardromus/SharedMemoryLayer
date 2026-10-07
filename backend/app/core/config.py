"""Application configuration loaded from environment variables.

Every secret is read from the environment - nothing sensitive has a default.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True)

    # --- general -------------------------------------------------------
    app_name: str = "Shared Memory Layer"
    app_version: str = "1.0.0"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # --- storage -------------------------------------------------------
    database_url: str = Field(..., description="SQLAlchemy URL for PostgreSQL")
    db_pool_size: int = 10
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "memories"

    # --- auth ----------------------------------------------------------
    jwt_secret: str = Field(..., min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_expiration: int = Field(15, ge=1, le=24 * 60, description="access token minutes")
    refresh_token_expiration_days: int = Field(7, ge=1, le=90)
    jwt_issuer: str = "shared-memory-layer"
    cookie_secure: bool = False
    allow_public_registration: bool = False
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    password_min_length: int = 10

    admin_username: str = "admin"
    admin_email: str = "admin@example.com"
    admin_password: str | None = None

    seed_demo_data: bool = True
    demo_user_password: str | None = None

    # --- embeddings / vector search -----------------------------------
    embedding_provider: Literal["fastembed", "ollama", "openai"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str = "/opt/models"

    search_default_top_k: int = 5
    search_max_top_k: int = 50
    search_similarity_threshold: float = Field(0.55, ge=0.0, le=1.0)
    duplicate_similarity_threshold: float = Field(0.92, ge=0.0, le=1.0)
    conflict_similarity_threshold: float = Field(0.70, ge=0.0, le=1.0)
    ranking_weight_similarity: float = 0.90
    ranking_weight_importance: float = 0.05
    ranking_weight_confidence: float = 0.05
    # Hybrid search: bonus for memories that contain every query term (full-text match)
    ranking_weight_keyword: float = 0.10

    max_memory_content_length: int = 20000
    max_memory_title_length: int = 200
    max_tags_per_memory: int = 20
    max_metadata_bytes: int = 8192

    # --- LLM -------------------------------------------------------------
    llm_provider: Literal["none", "ollama", "openai", "anthropic"] = "none"
    llm_model: str | None = None
    llm_timeout_seconds: float = 60.0
    llm_max_tokens: int = 700
    ollama_base_url: str = "http://ollama:11434"
    # CPU threads for Ollama inference; oversubscribed VMs can be dramatically slower with the default
    ollama_num_thread: int | None = Field(None, ge=1, le=64)
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"

    # --- rate limiting ------------------------------------------------
    rate_limit_enabled: bool = True
    trust_proxy_headers: bool = False
    rate_limit_auth_per_minute: int = 20
    rate_limit_search_per_minute: int = 300
    rate_limit_write_per_minute: int = 120

    @field_validator("jwt_secret")
    @classmethod
    def _reject_placeholder_secret(cls, value: str) -> str:
        lowered = value.lower()
        if lowered in {"changeme", "secret"} or "change-me" in lowered:
            raise ValueError("JWT_SECRET must be a strong random value")
        return value

    @model_validator(mode="after")
    def _check_production(self) -> "Settings":
        if self.environment == "production" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def effective_llm_model(self) -> str | None:
        if self.llm_model:
            return self.llm_model
        return {
            "ollama": "qwen2.5:0.5b",
            "openai": "gpt-4o-mini",
            "anthropic": "claude-haiku-4-5-20251001",
        }.get(self.llm_provider)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
