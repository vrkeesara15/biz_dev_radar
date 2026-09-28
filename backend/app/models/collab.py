"""tasks and comments (SPEC 10.2): the workspace's Tasks tab and the comment threads on
the matrix, the drafts, the scorecard and the pursuit itself.

Both milestones needed these tables: M5 for the drafters' `[NEEDS INPUT]` asks and the
red team's findings, M6 for the Tasks tab and the comment threads. They were built twice
in parallel — `tasks`/`comments` on the M5 branch and `pursuit_tasks`/`pursuit_comments`
on the M6 branch (M6 OQ-117) — and are reconciled here onto SPEC 10.2's names with the
union of both sets of columns. Everything is addressed through `app.services.collab`
(the CRUD and the placeholder helper) and `app.services.drafts` (the agents' single
insert point), so there is one write path for each.
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
    SOURCE_AGENT,
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


class Task(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """Work handed to a human: an agent's `[NEEDS INPUT]` ask (SPEC 8) or a task somebody
    typed into the Tasks tab (SPEC 10.4 screen 6)."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(f"status IN ({STATUS_SQL_LIST})", name="status"),
        CheckConstraint(f"source IN ({SOURCE_SQL_LIST})", name="source"),
        Index("ix_tasks_assignee", "tenant_id", "assignee_user_id", "status"),
    )

    pursuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pursuits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # open | done
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{TASK_OPEN}'")
    )
    # agent | user
    source: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text(f"'{SOURCE_AGENT}'")
    )
    # where the ask came from: {"kind": "needs_input", "placeholder": "labor category",
    # "agent": "draft:technical-approach", "section_id": ..., "draft_id": ...}
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


class Comment(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """A comment anchored on one artefact of a pursuit (SPEC 3: a reviewer may comment and
    approve, nothing else; SPEC 10.2 `comments`)."""

    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint(f"target_type IN ({TARGET_SQL_LIST})", name="target_type"),
        Index("ix_comments_target", "pursuit_id", "target_type", "target_id"),
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
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
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


__all__ = ["Comment", "Task"]
