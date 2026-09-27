"""Tenant knowledge base (SPEC 4.3, 8, 10.2 `kb_chunks(embedding)`): embedded chunks of a
profile's capability statements / brochures / case studies, boilerplate blocks, past
performance and service lines. Tenant-scoped (RLS), one row per (source, chunk_index);
`content_hash` is the hash of the source text so unchanged sources are never re-embedded.
"""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.opportunities import EMBEDDING_DIM

KB_SOURCE_TYPES = ("profile_file", "boilerplate", "past_performance", "service_line")


class KBChunk(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "kb_chunks"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "chunk_index", name="uq_kb_chunks_source"),
        Index(
            "ix_kb_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # profile_file | boilerplate | past_performance | service_line (KB_SOURCE_TYPES)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # id of the profile_files / boilerplate_blocks / past_performance / service_lines row
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    # sha256 of the source text (all chunks of one source share it)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
