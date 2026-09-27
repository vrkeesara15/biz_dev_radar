"""pursuit_tasks and pursuit_comments (SPEC 8, 9, 10.2): the workspace's Tasks tab and
the comment threads on the matrix, the drafts and the pursuit itself.

SPEC 10.2 names these `tasks` and `comments`. They are created here as `pursuit_tasks`
and `pursuit_comments` because the M5 branch is adding its own `tasks` table for the
drafters' [NEEDS INPUT] placeholders in parallel; the orchestrator reconciles the two
names at merge (PROGRESS.m6.md OQ-117). Everything below is addressed through
`app.services.collab`, so a rename is one module plus the migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.collab import (
    SOURCE_USER,
    TARGET_PURSUIT,
    TARGET_TYPES,
    TASK_OPEN,
    TASK_SOURCES,
    TASK_STATUSES,
)
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

STATUS_SQL_LIST = ", ".join(f"'{value}'" for value in TASK_STATUSES)
SOURCE_SQL_LIST = ", ".join(f"'{value}'" for value in TASK_SOURCES)
TARGET_SQL_LIST = ", ".join(f"'{value}'" for value in TARGET_TYPES)


class PursuitTask(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "pursuit_tasks"
    __table_args__ = (
        CheckConstraint(f"status IN ({STATUS_SQL_LIST})", name="status"),
        CheckConstraint(f"source IN ({SOURCE_SQL_LIST})", name="source"),
        Index("ix_pursuit_tasks_assignee", "tenant_id", "assignee_user_id", "status"),
    )

    pursuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pursuits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{TASK_OPEN}'")
    )
    source: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text(f"'{SOURCE_USER}'")
    )
    # where the ask came from: {"placeholder": "labor category", "agent": "pricing",
    # "artifact": "pricing_template", "section": "L.3", "document_id": ..., "page": 4}
    ref: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class PursuitComment(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "pursuit_comments"
    __table_args__ = (
        CheckConstraint(f"target_type IN ({TARGET_SQL_LIST})", name="target_type"),
        Index("ix_pursuit_comments_target", "pursuit_id", "target_type", "target_id"),
    )

    pursuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pursuits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # app.core.collab.TARGET_TYPES; target_id is NULL for a comment on the pursuit itself
    target_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text(f"'{TARGET_PURSUIT}'")
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
