"""M6 pursuits, key dates, reminders, collaboration and calendar.

Revision ID: 0011_m6_pursuits
Revises: 0006_m4_matching_alerts

One migration per milestone (CLAUDE.md); later M6 tasks edit this file in place. The
number follows merge order, so the orchestrator may renumber it when the parallel
worktrees land.

M6-01 extends `pursuits` (stage CHECK, watch, pass_reason, submitted_at, decided_by /
decided_at). M6-02 adds `pursuit_dates`; M6-07 adds `pursuit_tasks` and
`pursuit_comments`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.core.collab import (
    SOURCE_USER as TASK_SOURCE_USER,
)
from app.core.collab import (
    TARGET_PURSUIT,
    TARGET_TYPES,
    TASK_OPEN,
    TASK_SOURCES,
    TASK_STATUSES,
)
from app.core.key_dates import KIND_CUSTOM, KINDS, SOURCE_AUTO, SOURCES
from app.core.pursuit_stages import STAGES
from migrations.rls import enable_rls, grant_app
from sqlalchemy.dialects import postgresql

revision: str = "0011_m6_pursuits"
down_revision: str | None = "0006_m4_matching_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGE_LIST = ", ".join(f"'{stage}'" for stage in STAGES)
KIND_LIST = ", ".join(f"'{kind}'" for kind in KINDS)
SOURCE_LIST = ", ".join(f"'{source}'" for source in SOURCES)

TASK_STATUS_LIST = ", ".join(f"'{value}'" for value in TASK_STATUSES)
TASK_SOURCE_LIST = ", ".join(f"'{value}'" for value in TASK_SOURCES)
TARGET_TYPE_LIST = ", ".join(f"'{value}'" for value in TARGET_TYPES)

TENANT_TABLES = ("pursuit_dates", "pursuit_tasks", "pursuit_comments")


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[object]:
    kwargs: dict[str, object] = {"nullable": nullable}
    if default_now:
        kwargs["server_default"] = sa.func.now()
    return sa.Column(name, sa.DateTime(timezone=True), **kwargs)


def upgrade() -> None:
    _upgrade_pursuits()
    _upgrade_pursuit_dates()
    _upgrade_collab()
    for table in TENANT_TABLES:
        grant_app(op, table)
        enable_rls(op, table)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
    _downgrade_pursuits()


# --- M6-01 pursuits: stages, watch, pass reason, decision stamps ----------------------------


def _upgrade_pursuits() -> None:
    op.add_column(
        "pursuits",
        sa.Column("watch", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("pursuits", sa.Column("pass_reason", sa.Text()))
    op.add_column("pursuits", _ts("submitted_at"))
    op.add_column(
        "pursuits",
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("pursuits", _ts("decided_at"))
    # any pre-M6 row carries a free-text stage; normalise before the CHECK bites
    op.execute(f"UPDATE pursuits SET stage = 'identified' WHERE stage NOT IN ({STAGE_LIST})")
    op.create_check_constraint("stage", "pursuits", f"stage IN ({STAGE_LIST})")
    op.create_index("ix_pursuits_stage", "pursuits", ["stage"])
    op.create_index("ix_pursuits_owner_user_id", "pursuits", ["owner_user_id"])


def _downgrade_pursuits() -> None:
    op.drop_index("ix_pursuits_owner_user_id", table_name="pursuits")
    op.drop_index("ix_pursuits_stage", table_name="pursuits")
    op.drop_constraint("stage", "pursuits", type_="check")
    for column in ("decided_at", "decided_by", "submitted_at", "pass_reason", "watch"):
        op.drop_column("pursuits", column)


# --- M6-02 key dates auto-created per pursuit ----------------------------------------------


def _upgrade_pursuit_dates() -> None:
    op.create_table(
        "pursuit_dates",
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
            "pursuit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pursuits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("buyer_tz", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column(
            "source", sa.String(8), nullable=False, server_default=sa.text(f"'{SOURCE_AUTO}'")
        ),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column(
            "acknowledged_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        _ts("acknowledged_at"),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.CheckConstraint(f"kind IN ({KIND_LIST})", name="ck_pursuit_dates_kind"),
        sa.CheckConstraint(f"source IN ({SOURCE_LIST})", name="ck_pursuit_dates_source"),
    )
    op.create_index("ix_pursuit_dates_tenant_id", "pursuit_dates", ["tenant_id"])
    op.create_index("ix_pursuit_dates_pursuit_id", "pursuit_dates", ["pursuit_id"])
    op.create_index("ix_pursuit_dates_at", "pursuit_dates", ["at"])
    op.create_index(
        "uq_pursuit_dates_pursuit_kind",
        "pursuit_dates",
        ["pursuit_id", "kind"],
        unique=True,
        postgresql_where=sa.text(f"kind <> '{KIND_CUSTOM}'::text"),
    )


# --- M6-07 tasks and comments on a pursuit --------------------------------------------------


def _uuid_pk() -> sa.Column[object]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _tenant_id() -> sa.Column[object]:
    return sa.Column(
        "tenant_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def _pursuit_id() -> sa.Column[object]:
    return sa.Column(
        "pursuit_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("pursuits.id", ondelete="CASCADE"),
        nullable=False,
    )


def _user_fk(name: str) -> sa.Column[object]:
    return sa.Column(
        name, postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )


def _upgrade_collab() -> None:
    op.create_table(
        "pursuit_tasks",
        _uuid_pk(),
        _tenant_id(),
        _pursuit_id(),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("detail", sa.Text()),
        _user_fk("assignee_user_id"),
        _ts("due_at"),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default=sa.text(f"'{TASK_OPEN}'")
        ),
        sa.Column(
            "source",
            sa.String(8),
            nullable=False,
            server_default=sa.text(f"'{TASK_SOURCE_USER}'"),
        ),
        sa.Column("ref", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _user_fk("created_by"),
        _ts("completed_at"),
        _user_fk("completed_by"),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.CheckConstraint(f"status IN ({TASK_STATUS_LIST})", name="ck_pursuit_tasks_status"),
        sa.CheckConstraint(f"source IN ({TASK_SOURCE_LIST})", name="ck_pursuit_tasks_source"),
    )
    op.create_index("ix_pursuit_tasks_tenant_id", "pursuit_tasks", ["tenant_id"])
    op.create_index("ix_pursuit_tasks_pursuit_id", "pursuit_tasks", ["pursuit_id"])
    op.create_index(
        "ix_pursuit_tasks_assignee", "pursuit_tasks", ["tenant_id", "assignee_user_id", "status"]
    )

    op.create_table(
        "pursuit_comments",
        _uuid_pk(),
        _tenant_id(),
        _pursuit_id(),
        sa.Column(
            "target_type",
            sa.String(32),
            nullable=False,
            server_default=sa.text(f"'{TARGET_PURSUIT}'"),
        ),
        sa.Column("target_id", postgresql.UUID(as_uuid=True)),
        sa.Column("body", sa.Text(), nullable=False),
        _user_fk("author_user_id"),
        _ts("resolved_at"),
        _user_fk("resolved_by"),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.CheckConstraint(
            f"target_type IN ({TARGET_TYPE_LIST})", name="ck_pursuit_comments_target_type"
        ),
    )
    op.create_index("ix_pursuit_comments_tenant_id", "pursuit_comments", ["tenant_id"])
    op.create_index("ix_pursuit_comments_pursuit_id", "pursuit_comments", ["pursuit_id"])
    op.create_index(
        "ix_pursuit_comments_target",
        "pursuit_comments",
        ["pursuit_id", "target_type", "target_id"],
    )
