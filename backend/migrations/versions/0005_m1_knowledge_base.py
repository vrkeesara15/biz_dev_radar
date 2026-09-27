"""M1 knowledge base: kb_chunks (tenant-scoped, RLS) with pgvector embeddings.

Revision ID: 0005_m1_knowledge_base
Revises: 0004_m5_agent_runtime

Numbered by merge order (CLAUDE.md): M1-12 lands after the M2/M5 runtime migrations.
One row per (source_type, source_id, chunk_index); HNSW cosine index for
similarity_search (SPEC 4.3 RAG knowledge base, SPEC 8 per-tenant indexes).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from migrations.rls import enable_rls, grant_app
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0005_m1_knowledge_base"
down_revision: str | None = "0004_m5_agent_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1024
TABLE = "kb_chunks"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("company_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("source_type", "source_id", "chunk_index", name="uq_kb_chunks_source"),
    )
    op.create_index("ix_kb_chunks_tenant_id", TABLE, ["tenant_id"])
    op.create_index("ix_kb_chunks_profile_id", TABLE, ["profile_id"])
    op.create_index(
        "ix_kb_chunks_embedding",
        TABLE,
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    grant_app(op, TABLE)
    enable_rls(op, TABLE)


def downgrade() -> None:
    op.drop_table(TABLE)
