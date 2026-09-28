"""drafts / draft_versions / draft_feedback / exports (SPEC 8 agent 6, 10.2).

One `drafts` row per outline section of a pursuit; every save -- by an agent or by a
writer -- appends a `draft_versions` row and repoints `current_version_id`, so nothing a
human wrote is ever overwritten and M5-17 can diff the agent version against the edit.

A version carries its citations (the tokens the body cites, with the quote they came
from), its [NEEDS INPUT: ...] placeholders and the grounding flags (M5-11). An
unsupported claim also becomes a `tasks` row for the owner (SPEC 8 guardrails);
that table and `comments` live in app.models.collab.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

DRAFT_STATUS_DRAFT = "draft"
DRAFT_STATUS_IN_REVIEW = "in_review"
DRAFT_STATUS_APPROVED = "approved"
DRAFT_STATUSES: tuple[str, ...] = (
    DRAFT_STATUS_DRAFT,
    DRAFT_STATUS_IN_REVIEW,
    DRAFT_STATUS_APPROVED,
)

AUTHOR_AGENT = "agent"
AUTHOR_USER = "user"
AUTHORS: tuple[str, ...] = (AUTHOR_AGENT, AUTHOR_USER)


class Draft(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "drafts"
    __table_args__ = (UniqueConstraint("pursuit_id", "section_id", name="uq_drafts_section"),)

    pursuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pursuits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # the outline section id (app.core.outline.OutlineSection.id), unique per pursuit
    section_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    volume: Mapped[str | None] = mapped_column(String(120))
    # the version the workspace shows; NULL only between the row and its first version
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "draft_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_drafts_current_version_id_draft_versions",
        ),
    )
    # draft | in_review | approved
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'draft'"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class DraftVersion(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "draft_versions"
    __table_args__ = (UniqueConstraint("draft_id", "version", name="uq_draft_versions_version"),)

    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drafts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    body_html: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    body_text: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    # [{token, source_type, source_id, page, quote}]
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # [{placeholder, question, task_id}]
    needs_input: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # grounding report (M5-11): {flags: [{sentence, reason}], supported, unsupported}
    flags: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    author: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'agent'"))
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    model: Mapped[str | None] = mapped_column(String(128))
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class DraftFeedback(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """A human's edit of a draft section, kept as a unified diff (SPEC 8: "human-in-the-
    loop edits are diffed and saved as feedback to improve future drafts").

    Written by `services.drafts.save_version` whenever a user saves over an existing
    version, so the row always names the two versions it compares. Tenant-scoped and
    never read across tenants (`services.draft_feedback.recent_examples`).
    """

    __tablename__ = "draft_feedback"

    pursuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pursuits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drafts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("draft_versions.id", ondelete="SET NULL")
    )
    to_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("draft_versions.id", ondelete="SET NULL")
    )
    # agent | user: whether the edit improved an agent draft or another person's edit
    from_author: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'agent'")
    )
    diff_text: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    # {"added": n, "removed": n, "changed": bool}
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    edited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


EXPORT_FORMATS: tuple[str, ...] = ("docx", "pdf", "xlsx", "zip")


class Export(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One rendered export of a pursuit's package (SPEC 8: DOCX / PDF / XLSX / ZIP).

    Versions are never overwritten, so the file a reviewer downloaded stays downloadable.
    `final` records whether the package was already marked final when it was rendered:
    an export made before that carries the "DRAFT - internal" footer forever (SPEC 11).
    """

    __tablename__ = "exports"
    __table_args__ = (
        UniqueConstraint("pursuit_id", "format", "version", name="uq_exports_version"),
    )

    pursuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pursuits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # docx | pdf | xlsx | zip
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    # libreoffice | pymupdf (which PDF path produced it), NULL for docx / xlsx
    renderer: Mapped[str | None] = mapped_column(String(16))
    final: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
