"""Permission-aware hybrid search.

Semantic path: query -> embedding -> vector similarity search with the
access-control and metadata filters pushed down into Qdrant.

Keyword path: PostgreSQL full-text search (``websearch_to_tsquery``, every
term must match) under the *same* permission and metadata filters. It catches
exact identifiers (error codes, CVE ids, ticket numbers) that embeddings blur.

Both candidate sets are merged, every candidate is re-checked against the
authoritative PostgreSQL row, ranked and cut to top-k. Restricted memories are
never returned merely because they are similar or share keywords.

Ranking (``score``):
    0.90 * similarity + 0.05 * importance + 0.05 * confidence
    + 0.02 if verified + 0.10 * keyword_rank (0 for semantic-only matches)
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Float, cast, func, literal_column, or_, select
from sqlalchemy.orm import Session

from app.audit import AuditAction, record
from app.core.config import get_settings
from app.models.enums import AuditStatus, VerificationStatus
from app.models.memory import Memory, Tag
from app.schemas.memory import SearchRequest, SearchResponse, SearchResult
from app.security.access import build_read_filter, can_read, sql_read_filter
from app.security.principal import Principal
from app.services.memory_service import MemoryService
from app.services.serializers import creator_name
from app.vector.base import FieldMatch, VectorFilter, VectorStoreError

logger = logging.getLogger(__name__)
_WORD = re.compile(r"[a-z0-9]{3,}")


@dataclass
class Candidate:
    similarity: float | None = None
    keyword_rank: float = 0.0  # normalised to [0, 1]
    semantic_hit: bool = False
    keyword_hit: bool = False


class SearchService:
    def __init__(self, db: Session, memory_service: MemoryService | None = None):
        self.db = db
        self.settings = get_settings()
        self.memories = memory_service or MemoryService(db)

    # ------------------------------------------------------------------ filters
    def _statuses(self, req: SearchRequest) -> list[VerificationStatus]:
        return req.verification_status or [
            s for s in VerificationStatus if req.include_rejected or s != VerificationStatus.REJECTED
        ]

    def _vector_filter(self, principal: Principal, req: SearchRequest) -> VectorFilter:
        vf = build_read_filter(
            principal,
            include_archived=req.include_archived and principal.is_admin,
            include_expired=req.include_expired,
        )
        if req.memory_types:
            vf.must.append(FieldMatch("memory_type", [t.value for t in req.memory_types]))
        if req.team_ids:
            vf.must.append(FieldMatch("team_id", [str(t) for t in req.team_ids]))
        if req.agent_ids:
            vf.must.append(FieldMatch("creator_agent_id", [str(a) for a in req.agent_ids]))
        if req.project_ids:
            vf.must.append(FieldMatch("project_id", [str(p) for p in req.project_ids]))
        if req.tags:
            vf.must.append(FieldMatch("tags", req.tags))
        if req.visibility:
            vf.must.append(FieldMatch("visibility", [v.value for v in req.visibility]))
        vf.must.append(FieldMatch("verification_status", [s.value for s in self._statuses(req)]))
        if req.min_confidence is not None:
            vf.range_gte["confidence"] = req.min_confidence
        if req.created_after is not None:
            vf.range_gte["created_at_ts"] = req.created_after.timestamp()
        if req.created_before is not None:
            vf.range_lte["created_at_ts"] = req.created_before.timestamp()
        if req.only_own:
            if principal.agent_id is not None:
                vf.must.append(FieldMatch("creator_agent_id", str(principal.agent_id)))
            else:
                vf.must.append(FieldMatch("creator_user_id", str(principal.user_id)))
        return vf

    def _sql_filters(self, principal: Principal, req: SearchRequest) -> list[ColumnElement[bool]]:
        where: list[ColumnElement[bool]] = [
            sql_read_filter(principal, include_archived=req.include_archived and principal.is_admin, include_expired=req.include_expired)
        ]
        if req.memory_types:
            where.append(Memory.memory_type.in_(req.memory_types))
        if req.team_ids:
            where.append(Memory.team_id.in_(req.team_ids))
        if req.agent_ids:
            where.append(Memory.creator_agent_id.in_(req.agent_ids))
        if req.project_ids:
            where.append(Memory.project_id.in_(req.project_ids))
        if req.tags:
            where.append(Memory.tags.any(Tag.name.in_(req.tags)))
        if req.visibility:
            where.append(Memory.visibility.in_(req.visibility))
        where.append(Memory.verification_status.in_(self._statuses(req)))
        if req.min_confidence is not None:
            where.append(Memory.confidence >= req.min_confidence)
        if req.created_after is not None:
            where.append(Memory.created_at >= req.created_after)
        if req.created_before is not None:
            where.append(Memory.created_at <= req.created_before)
        if req.only_own:
            where.append(Memory.creator_agent_id == principal.agent_id if principal.agent_id else Memory.creator_user_id == principal.user_id)
        return where

    # ------------------------------------------------------------------ retrieval paths
    def _semantic(self, principal: Principal, req: SearchRequest, vector: list[float], threshold: float, pool: int) -> dict[uuid.UUID, Candidate]:
        hits = self.memories.store.search(vector, self._vector_filter(principal, req), limit=pool, score_threshold=threshold)
        return {uuid.UUID(h.id): Candidate(similarity=h.score, semantic_hit=True) for h in hits}

    def _keyword(self, principal: Principal, req: SearchRequest, pool: int) -> dict[uuid.UUID, Candidate]:
        tsquery = func.websearch_to_tsquery(literal_column("'english'::regconfig"), req.query)
        rank = cast(func.ts_rank_cd(Memory.search_tsv, tsquery), Float)
        rows = self.db.execute(
            select(Memory.id, rank.label("rank"))
            .where(Memory.search_tsv.op("@@")(tsquery), *self._sql_filters(principal, req))
            .order_by(rank.desc())
            .limit(pool)
        ).all()
        if not rows:
            return {}
        top = max(r.rank for r in rows) or 1.0
        return {r.id: Candidate(keyword_rank=float(r.rank) / top, keyword_hit=True) for r in rows}

    def _substring(self, principal: Principal, req: SearchRequest, pool: int) -> dict[uuid.UUID, Candidate]:
        """Last-resort degraded mode (vector store down and no full-text match)."""
        words = list(dict.fromkeys(_WORD.findall(req.query.lower())))[:8]
        if not words:
            return {}
        conditions = [or_(Memory.title.ilike(f"%{w}%"), Memory.content.ilike(f"%{w}%")) for w in words]
        rows = self.db.scalars(select(Memory).where(or_(*conditions), *self._sql_filters(principal, req)).limit(pool)).all()
        out = {}
        for m in rows:
            text = f"{m.title} {m.content}".lower()
            out[m.id] = Candidate(keyword_rank=sum(w in text for w in words) / len(words), keyword_hit=True)
        return out

    # ------------------------------------------------------------------ ranking
    def _score(self, cand: Candidate, memory: Memory) -> float:
        s = self.settings
        score = (
            s.ranking_weight_similarity * (cand.similarity or 0.0)
            + s.ranking_weight_importance * memory.importance
            + s.ranking_weight_confidence * memory.confidence
            + s.ranking_weight_keyword * cand.keyword_rank
        )
        if memory.verification_status == VerificationStatus.VERIFIED:
            score += 0.02
        return round(score, 4)

    def _result(self, memory: Memory, cand: Candidate, principal: Principal) -> SearchResult:
        match = "semantic+keyword" if cand.semantic_hit and cand.keyword_hit else ("keyword" if cand.keyword_hit else "semantic")
        return SearchResult(
            memory_id=memory.id,
            title=memory.title,
            content=memory.content,
            similarity=round(cand.similarity or 0.0, 4),
            score=self._score(cand, memory),
            team=memory.team.name,
            team_id=memory.team_id,
            creator=creator_name(memory),
            memory_type=memory.memory_type,
            confidence=memory.confidence,
            importance=memory.importance,
            verification_status=memory.verification_status,
            version=memory.current_version,
            tags=memory.tag_names,
            visibility=memory.visibility,
            project=memory.project.name if memory.project else None,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
            cross_team=memory.team_id != principal.team_id,
            match=match,
        )

    # ------------------------------------------------------------------ entry point
    def search(self, principal: Principal, req: SearchRequest, audit: bool = True) -> SearchResponse:
        started = time.perf_counter()
        threshold = req.similarity_threshold if req.similarity_threshold is not None else self.settings.search_similarity_threshold
        pool = min(req.top_k * 3, 150)
        mode = req.mode
        candidates: dict[uuid.UUID, Candidate] = {}
        vector: list[float] | None = None

        if req.mode in ("hybrid", "keyword"):
            candidates.update(self._keyword(principal, req, pool))
        if req.mode in ("hybrid", "semantic"):
            try:
                vector = self.memories.embed(req.query)
                for mid, cand in self._semantic(principal, req, vector, threshold, pool).items():
                    existing = candidates.get(mid)
                    if existing:
                        existing.similarity, existing.semantic_hit = cand.similarity, True
                    else:
                        candidates[mid] = cand
            except VectorStoreError:
                logger.warning("vector store unavailable - search degrades to keyword matching")
                mode, vector = "keyword_fallback", None
                if not candidates:
                    candidates = self._substring(principal, req, pool)

        # Keyword-only hits still get a true cosine similarity when the vector store is up.
        missing = [str(mid) for mid, c in candidates.items() if c.similarity is None]
        if missing and mode != "keyword_fallback":
            try:
                vector = vector or self.memories.embed(req.query)
                scores = self.memories.store.score_ids(vector, missing)
                for mid in missing:
                    candidates[uuid.UUID(mid)].similarity = scores.get(mid, 0.0)
            except VectorStoreError:
                pass

        rows = {m.id: m for m in self.db.scalars(select(Memory).where(Memory.id.in_(list(candidates))))} if candidates else {}
        results: list[SearchResult] = []
        filtered_out = 0
        for mid, cand in candidates.items():
            memory = rows.get(mid)
            # Defence in depth: the authoritative DB row must still be readable.
            if memory is None or not can_read(principal, memory, include_expired=req.include_expired):
                filtered_out += 1
                continue
            results.append(self._result(memory, cand, principal))

        results.sort(key=lambda r: r.score, reverse=True)
        results = results[: req.top_k]
        if results:
            returned = {r.memory_id for r in results}
            self.memories.register_access([rows[i] for i in returned], principal)
            self.db.commit()
        took_ms = round((time.perf_counter() - started) * 1000, 2)

        if audit:
            record(
                AuditAction.MEMORY_SEARCH,
                AuditStatus.SUCCESS,
                principal,
                resource_type="search",
                details={
                    "query": req.query[:200],
                    "mode": mode,
                    "result_count": len(results),
                    "result_ids": [str(r.memory_id) for r in results],
                    "cross_team_results": sum(r.cross_team for r in results),
                    "keyword_matches": sum(r.match != "semantic" for r in results),
                    "top_similarity": results[0].similarity if results else None,
                    "post_filtered": filtered_out,
                    "took_ms": took_ms,
                },
            )
        return SearchResponse(query=req.query, mode=mode, total=len(results), took_ms=took_ms, threshold=threshold, results=results)

