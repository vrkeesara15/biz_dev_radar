"""M6 pursuits, key dates, reminders, collaboration and calendar.

Revision ID: 0011_m6_pursuits
Revises: 0006_m4_matching_alerts

One migration per milestone (CLAUDE.md); later M6 tasks edit this file in place. The
number follows merge order, so the orchestrator may renumber it when the parallel
worktrees land.

M6-01 extends `pursuits` (stage CHECK, watch, pass_reason, submitted_at). The Gate 1
stamps `decided_by` / `decided_at` it also added are created by 0007_m5_agents, which
runs first, so they are not repeated here; M6-07's `pursuit_tasks` / `pursuit_comments`
are likewise gone, reconciled at merge onto 0007's SPEC 10.2 `tasks` and `comments`
(OQ-117). M6-02 adds `pursuit_dates`; M6-04 adds `calendar_connections`,
`calendar_events` and
`user_notification_prefs.calendar_token`; M6-05 adds `users.phone_e164` /
`users.phone_verified_at`; M6-03 adds `reminders`; M6-06 adds
`company_profiles.blocked_for_bids`, `pursuits.activity_at` and
`pursuits.matrix_recheck_required`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
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

CALENDAR_PROVIDER_LIST = ", ".join(f"'{value}'" for value in CALENDAR_PROVIDERS)

REMINDER_LABEL_LIST = ", ".join(f"'{label}'" for label in REMINDER_LABELS)

TENANT_TABLES = (
    "pursuit_dates",
    "reminders",
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
    _upgrade_calendar()
    _upgrade_whatsapp()
    _upgrade_reminders()
    _upgrade_recurring_checks()
    for table in TENANT_TABLES:
        grant_app(op, table)
        enable_rls(op, table)


def downgrade() -> None:
    op.drop_column("company_profiles", "blocked_for_bids")
    op.drop_column("pursuits", "matrix_recheck_required")
    op.drop_column("pursuits", "activity_at")
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
    # decided_by / decided_at are created with the table in 0007_m5_agents (Gate 1)
    # any pre-M6 row carries a free-text stage; normalise before the CHECK bites
    op.execute(f"UPDATE pursuits SET stage = 'identified' WHERE stage NOT IN ({STAGE_LIST})")
    op.create_check_constraint("stage", "pursuits", f"stage IN ({STAGE_LIST})")
    op.create_index("ix_pursuits_stage", "pursuits", ["stage"])
    op.create_index("ix_pursuits_owner_user_id", "pursuits", ["owner_user_id"])


def _downgrade_pursuits() -> None:
    # ix_pursuits_activity_at goes with its column in _downgrade_recurring_checks
    op.drop_index("ix_pursuits_owner_user_id", table_name="pursuits")
    op.drop_index("ix_pursuits_stage", table_name="pursuits")
    op.drop_constraint("stage", "pursuits", type_="check")
    for column in ("submitted_at", "pass_reason", "watch"):
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


# --- M6-06 recurring checks ------------------------------------------------------------------


def _upgrade_recurring_checks() -> None:
    # SPEC 4.1: an expired SAM registration (US) or DSC (IN) blocks bidding
    op.add_column(
        "company_profiles",
        sa.Column(
            "blocked_for_bids", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    # SPEC 9: "stale pursuits with no activity for 5 days"
    op.add_column("pursuits", _ts("activity_at", nullable=False, default_now=True))
    op.add_column(
        "pursuits",
        sa.Column(
            "matrix_recheck_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index("ix_pursuits_activity_at", "pursuits", ["activity_at"])
