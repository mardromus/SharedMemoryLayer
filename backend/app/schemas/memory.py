"""Schemas for memories, search, versions, sharing, feedback and conflicts."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import (
    ConflictRecordStatus,
    ConflictResolution,
    ConflictStatus,
    MemoryStatus,
    MemoryType,
    Sensitivity,
    VerificationStatus,
    Visibility,
)
from app.schemas.common import TAG_RE, CleanText, ORMModel, PlainLine, Ref

MAX_TAGS = 20
MAX_METADATA_BYTES = 8192
MAX_CONTENT = 100_000  # hard ceiling; the configurable limit is enforced in the service


def _normalise_tags(tags: list[str] | None) -> list[str] | None:
    if tags is None:
        return None
    cleaned: list[str] = []
    for tag in tags:
        t = tag.strip().lower().replace(" ", "-")
        if not TAG_RE.match(t):
            raise ValueError(f"invalid tag {tag!r}: use lowercase letters, digits, '-', '_' or '.' (max 50 chars)")
        if t not in cleaned:
            cleaned.append(t)
    if len(cleaned) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags are allowed")
    return cleaned


def _check_metadata(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        encoded = json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be JSON serialisable") from exc
    if len(encoded.encode()) > MAX_METADATA_BYTES:
        raise ValueError(f"metadata must be at most {MAX_METADATA_BYTES} bytes")
    return value


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class MemoryBase(BaseModel):
    @field_validator("tags", mode="before", check_fields=False)
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and not isinstance(v, list):
            raise ValueError("tags must be a list of strings")
        return _normalise_tags([str(t) for t in v] if v is not None else None)

    @field_validator("metadata", check_fields=False)
    @classmethod
    def _metadata(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return _check_metadata(v)

    @field_validator("expires_at", check_fields=False)
    @classmethod
    def _expires(cls, v: datetime | None) -> datetime | None:
        return _utc(v)


class MemoryCreate(MemoryBase):
    title: PlainLine = Field(..., min_length=3, max_length=200)
    content: CleanText = Field(..., min_length=1, max_length=MAX_CONTENT)
    memory_type: MemoryType = MemoryType.KNOWLEDGE
    tags: list[str] = Field(default_factory=list)
    visibility: Visibility | None = None
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    project_id: uuid.UUID | None = None
    importance: float = Field(0.5, ge=0, le=1)
    confidence: float = Field(0.8, ge=0, le=1)
    source: PlainLine | None = Field(None, max_length=100)
    source_ref: PlainLine | None = Field(None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None
    allowed_team_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    allowed_agent_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    parent_memory_id: uuid.UUID | None = None
    # Optional ownership assertions - validated against the authenticated principal
    team_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    reject_on_duplicate: bool = False

    @model_validator(mode="after")
    def _visibility_rules(self) -> "MemoryCreate":
        if self.visibility == Visibility.ORGANIZATION and self.sensitivity in (
            Sensitivity.CONFIDENTIAL,
            Sensitivity.RESTRICTED,
        ):
            raise ValueError("confidential/restricted memories cannot be visible to the whole organization")
        if self.expires_at is not None and self.expires_at <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return self


class MemoryUpdate(MemoryBase):
    title: PlainLine | None = Field(None, min_length=3, max_length=200)
    content: CleanText | None = Field(None, min_length=1, max_length=MAX_CONTENT)
    memory_type: MemoryType | None = None
    tags: list[str] | None = None
    visibility: Visibility | None = None
    sensitivity: Sensitivity | None = None
    project_id: uuid.UUID | None = None
    importance: float | None = Field(None, ge=0, le=1)
    confidence: float | None = Field(None, ge=0, le=1)
    source_ref: PlainLine | None = Field(None, max_length=255)
    metadata: dict[str, Any] | None = None
    expires_at: datetime | None = None
    clear_expiration: bool = False
    change_description: PlainLine = Field("Updated memory", min_length=1, max_length=500)
    expected_version: int | None = Field(None, ge=1)


class BulkCreateRequest(BaseModel):
    """Items are validated one by one so a single bad row does not reject the whole import."""

    items: list[dict[str, Any]] = Field(..., min_length=1, max_length=100)
    skip_duplicates: bool = Field(False, description="Skip items that duplicate a memory you can already read (idempotent re-imports)")
    stop_on_error: bool = False
    detect_duplicates: bool = Field(
        True,
        description="Run duplicate/conflict detection per item. Disable for raw logs or transcripts "
        "(chat turns are not knowledge claims) to import much faster.",
    )

    @model_validator(mode="after")
    def _consistent(self) -> "BulkCreateRequest":
        if self.skip_duplicates and not self.detect_duplicates:
            raise ValueError("skip_duplicates requires detect_duplicates")
        return self


class BulkItemResult(BaseModel):
    index: int
    status: Literal["created", "skipped_duplicate", "error"]
    title: str | None = None
    memory_id: uuid.UUID | None = None
    warnings: "MemoryWarnings | None" = None
    error: dict[str, Any] | None = None


class BulkCreateResponse(BaseModel):
    created: int
    skipped: int
    failed: int
    results: list[BulkItemResult]


class MemoryCheckRequest(MemoryBase):
    title: PlainLine = Field(..., min_length=1, max_length=200)
    content: CleanText = Field(..., min_length=1, max_length=MAX_CONTENT)
    tags: list[str] = Field(default_factory=list)


class DuplicateCandidate(BaseModel):
    memory_id: uuid.UUID
    title: str
    similarity: float
    team: str
    exact_match: bool = False


class ConflictCandidate(BaseModel):
    memory_id: uuid.UUID
    title: str
    similarity: float
    reason: str
    detection_method: str
    conflict_id: uuid.UUID | None = None


class MemoryWarnings(BaseModel):
    message: str | None = None
    duplicates: list[DuplicateCandidate] = []
    conflicts: list[ConflictCandidate] = []


class MemoryOut(BaseModel):
    id: uuid.UUID
    title: str
    content: str
    memory_type: MemoryType
    team: Ref
    creator_agent: Ref | None
    creator_user: Ref | None
    project: Ref | None
    visibility: Visibility
    sensitivity: Sensitivity
    status: MemoryStatus
    verification_status: VerificationStatus
    conflict_status: ConflictStatus
    tags: list[str]
    importance: float
    confidence: float
    reliability: float
    source: str
    source_ref: str | None = None
    metadata: dict[str, Any] | None = None
    expires_at: datetime | None
    is_expired: bool
    embedding_id: str | None = None
    embedding_model: str | None = None
    parent_memory_id: uuid.UUID | None
    version: int
    access_count: int
    reuse_count: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    allowed_teams: list[Ref] | None = None
    allowed_agents: list[Ref] | None = None
    can_edit: bool = False
    can_share: bool = False


class MemoryCreateResponse(BaseModel):
    memory: MemoryOut
    warnings: MemoryWarnings


class MemoryListItem(BaseModel):
    id: uuid.UUID
    title: str
    snippet: str
    memory_type: MemoryType
    team: Ref
    creator: str
    visibility: Visibility
    sensitivity: Sensitivity
    status: MemoryStatus
    verification_status: VerificationStatus
    conflict_status: ConflictStatus
    tags: list[str]
    confidence: float
    importance: float
    version: int
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- search
class SearchRequest(BaseModel):
    query: CleanText = Field(..., min_length=1, max_length=2000)
    mode: Literal["hybrid", "semantic", "keyword"] = Field(
        "hybrid",
        description="hybrid = semantic similarity + full-text match (best for identifiers such as error codes); "
        "semantic = embeddings only; keyword = full-text only",
    )
    top_k: int = Field(5, ge=1, le=50)
    similarity_threshold: float | None = Field(None, ge=0, le=1)
    memory_types: list[MemoryType] | None = Field(None, max_length=10)
    team_ids: list[uuid.UUID] | None = Field(None, max_length=50)
    agent_ids: list[uuid.UUID] | None = Field(None, max_length=50, description="Filter by creator agent")
    project_ids: list[uuid.UUID] | None = Field(None, max_length=50)
    tags: list[str] | None = Field(None, max_length=MAX_TAGS)
    visibility: list[Visibility] | None = None
    verification_status: list[VerificationStatus] | None = None
    min_confidence: float | None = Field(None, ge=0, le=1)
    created_after: datetime | None = None
    created_before: datetime | None = None
    include_expired: bool = False
    include_archived: bool = False
    include_rejected: bool = False
    only_own: bool = Field(False, description="Restrict to memories created by the caller (isolated-memory mode)")

    @field_validator("tags", mode="before")
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        return _normalise_tags([str(t) for t in v] if v is not None else None)

    @field_validator("created_after", "created_before")
    @classmethod
    def _dates(cls, v: datetime | None) -> datetime | None:
        return _utc(v)


class SearchResult(BaseModel):
    memory_id: uuid.UUID
    title: str
    content: str
    similarity: float
    score: float
    team: str
    team_id: uuid.UUID
    creator: str
    memory_type: MemoryType
    confidence: float
    importance: float
    verification_status: VerificationStatus
    version: int
    tags: list[str]
    visibility: Visibility
    project: str | None
    created_at: datetime
    updated_at: datetime
    cross_team: bool
    match: Literal["semantic", "keyword", "semantic+keyword"] = "semantic"


class SearchResponse(BaseModel):
    query: str
    mode: str
    total: int
    took_ms: float
    threshold: float
    results: list[SearchResult]


# ---------------------------------------------------------------- versions
class VersionAuthor(BaseModel):
    type: str
    id: uuid.UUID | None
    name: str


class VersionOut(BaseModel):
    id: uuid.UUID
    memory_id: uuid.UUID
    version_number: int
    previous_version_id: uuid.UUID | None
    title: str
    content: str
    memory_type: str
    visibility: str
    tags: list[str]
    importance: float
    confidence: float
    change_description: str
    author: VersionAuthor
    created_at: datetime
    is_current: bool


# ---------------------------------------------------------------- sharing
class ShareRequest(BaseModel):
    team_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    agent_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _non_empty(self) -> "ShareRequest":
        if not self.team_ids and not self.agent_ids:
            raise ValueError("provide at least one team_id or agent_id")
        return self


class ShareResponse(BaseModel):
    memory_id: uuid.UUID
    visibility: Visibility
    allowed_teams: list[Ref]
    allowed_agents: list[Ref]


# ---------------------------------------------------------------- quality
class FeedbackCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    helpful: bool = True
    comment: PlainLine = Field("", max_length=1000)


class FeedbackOut(ORMModel):
    id: uuid.UUID
    memory_id: uuid.UUID
    rating: int
    helpful: bool
    comment: str
    created_at: datetime
    reliability: float


class VerifyRequest(BaseModel):
    status: VerificationStatus
    note: PlainLine = Field("", max_length=500)


# ---------------------------------------------------------------- conflicts
class ConflictOut(BaseModel):
    id: uuid.UUID
    memory: Ref
    conflicting_memory: Ref
    memory_content: str
    conflicting_content: str
    memory_team: str
    conflicting_team: str
    similarity: float
    reason: str
    detection_method: str
    status: ConflictRecordStatus
    resolution: ConflictResolution | None
    resolution_note: str
    resolved_at: datetime | None
    created_at: datetime


class ConflictResolveRequest(BaseModel):
    resolution: ConflictResolution
    note: PlainLine = Field("", max_length=1000)


# ---------------------------------------------------------------- provenance
class AccessEvent(BaseModel):
    timestamp: datetime
    actor: str
    actor_type: str
    action: str
    status: str
    reason: str | None


class ProvenanceOut(BaseModel):
    memory_id: uuid.UUID
    title: str
    created_by: VersionAuthor
    team: Ref
    created_at: datetime
    source: str
    source_ref: str | None
    parent_memory_id: uuid.UUID | None
    current_version: int
    versions: list[VersionOut]
    modified_by: list[str]
    access_summary: dict[str, int]
    access_history: list[AccessEvent] | None


BulkItemResult.model_rebuild()
BulkCreateResponse.model_rebuild()
