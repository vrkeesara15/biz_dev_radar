"""M6 pursuits, key dates, reminders, collaboration and calendar.

Revision ID: 0011_m6_pursuits
Revises: 0006_m4_matching_alerts

One migration per milestone (CLAUDE.md); later M6 tasks edit this file in place. The
number follows merge order, so the orchestrator may renumber it when the parallel
worktrees land.

M6-01 extends `pursuits` (stage CHECK, watch, pass_reason, submitted_at, decided_by /
decided_at). M6-02 adds `pursuit_dates`; M6-07 adds `pursuit_tasks` and
`pursuit_comments`; M6-04 adds `calendar_connections`, `calendar_events` and
`user_notification_prefs.calendar_token`; M6-05 adds `users.phone_e164` /
`users.phone_verified_at`; M6-03 adds `reminders`.
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
from app.core.reminders import LABELS as REMINDER_LABELS
from app.models.calendar import CALENDAR_PROVIDERS
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

CALENDAR_PROVIDER_LIST = ", ".join(f"'{value}'" for value in CALENDAR_PROVIDERS)

REMINDER_LABEL_LIST = ", ".join(f"'{label}'" for label in REMINDER_LABELS)

TENANT_TABLES = (
    "pursuit_dates",
    "reminders",
    "pursuit_tasks",
    "pursuit_comments",
    "calendar_connections",
    "calendar_events",
)


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[object]:
    kwargs: dict[str, object] = {"nullable": nullable}
    if default_now:
        kwargs["server_default"] = sa.func.now()
    return sa.Column(name, sa.DateTime(timezone=True), **kwargs)


def upgrade() -> None:
    _upgrade_pursuits()
    _upgrade_pursuit_dates()
    _upgrade_collab()
    _upgrade_calendar()
    _upgrade_whatsapp()
    _upgrade_reminders()
    for table in TENANT_TABLES:
        grant_app(op, table)
        enable_rls(op, table)


def downgrade() -> None:
    op.drop_column("users", "phone_verified_at")
    op.drop_column("users", "phone_e164")
    op.drop_column("user_notification_prefs", "calendar_token")
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
        sa.Column("sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
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


# --- M6-04 iCal feed token and per-user calendar connections --------------------------------


def _upgrade_calendar() -> None:
    # the nonce inside the signed feed token; rotating it revokes every old link
    op.add_column("user_notification_prefs", sa.Column("calendar_token", sa.String(128)))
    op.create_table(
        "calendar_connections",
        _uuid_pk(),
        _tenant_id(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("calendar_id", sa.String(256), nullable=False, server_default="primary"),
        sa.Column("secret_ref", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_error", sa.Text()),
        _ts("last_synced_at"),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "provider", name="uq_calendar_connections_user_provider"
        ),
        sa.CheckConstraint(
            f"provider IN ({CALENDAR_PROVIDER_LIST})", name="ck_calendar_connections_provider"
        ),
    )
    op.create_index("ix_calendar_connections_tenant_id", "calendar_connections", ["tenant_id"])
    op.create_index("ix_calendar_connections_user_id", "calendar_connections", ["user_id"])

    op.create_table(
        "calendar_events",
        _uuid_pk(),
        _tenant_id(),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calendar_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pursuit_date_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pursuit_dates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_event_id", sa.String(512), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _ts("synced_at"),
        sa.Column("last_error", sa.Text()),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint(
            "connection_id", "pursuit_date_id", name="uq_calendar_events_connection_date"
        ),
    )
    op.create_index("ix_calendar_events_tenant_id", "calendar_events", ["tenant_id"])
    op.create_index("ix_calendar_events_connection_id", "calendar_events", ["connection_id"])
    op.create_index("ix_calendar_events_pursuit_date_id", "calendar_events", ["pursuit_date_id"])


# --- M6-05 WhatsApp: a verified number on the user ------------------------------------------


def _upgrade_whatsapp() -> None:
    op.add_column("users", sa.Column("phone_e164", sa.String(20)))
    op.add_column("users", _ts("phone_verified_at"))


# --- M6-03 the reminder ladder ---------------------------------------------------------------


def _upgrade_reminders() -> None:
    op.create_table(
        "reminders",
        _uuid_pk(),
        _tenant_id(),
        sa.Column(
            "pursuit_date_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pursuit_dates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("offset_label", sa.String(8), nullable=False),
        _ts("due_at", nullable=False),
        _ts("sent_at"),
        sa.Column("skipped_reason", sa.Text()),
        sa.Column(
            "delivery_notification_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("notifications.id", ondelete="SET NULL"),
        ),
        sa.Column("escalation_level", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint(
            "pursuit_date_id", "offset_label", "due_at", name="uq_reminders_date_label_due"
        ),
        sa.CheckConstraint(
            f"offset_label IN ({REMINDER_LABEL_LIST})", name="ck_reminders_offset_label"
        ),
    )
    op.create_index("ix_reminders_tenant_id", "reminders", ["tenant_id"])
    op.create_index("ix_reminders_pursuit_date_id", "reminders", ["pursuit_date_id"])
    op.create_index("ix_reminders_pending", "reminders", ["sent_at", "due_at"])
