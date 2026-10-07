"""hybrid search: full-text search vector on memories

Adds a generated ``search_tsv`` column (title weighted A, content weighted B)
with a GIN index. It powers the keyword half of hybrid search, which catches
exact identifiers (error codes, CVE ids, ticket numbers, product names) that
dense embeddings tend to blur.

Revision ID: 0002_full_text_search
Revises: b710c191fd39
Create Date: 2026-09-28
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_full_text_search"
down_revision: str | None = "b710c191fd39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TSV_EXPRESSION = (
    "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english', coalesce(content, '')), 'B')"
)


def upgrade() -> None:
    op.execute(f"ALTER TABLE memories ADD COLUMN search_tsv tsvector GENERATED ALWAYS AS ({TSV_EXPRESSION}) STORED")
    op.create_index("ix_memories_search_tsv", "memories", ["search_tsv"], unique=False, postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_memories_search_tsv", table_name="memories")
    op.drop_column("memories", "search_tsv")
