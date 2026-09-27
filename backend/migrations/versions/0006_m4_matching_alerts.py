"""M4 matching + alerts: matches (M4-01); later M4 tasks add notifications,
notification_deliveries, integrations, push_subscriptions, match_feedback, saved_searches,
alert_rules and keyword_suggestions in place.

Revision ID: 0006_m4_matching_alerts
Revises: 0009_m7_billing_privacy

Numbered by merge order (CLAUDE.md). The number was reserved before M7 merged, so the
file stem stays 0006 while down_revision points at main's current head
(0004 -> 0005_m1_knowledge_base -> 0009_m7_billing_privacy -> this), keeping one head.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from migrations.rls import enable_rls, grant_app
from sqlalchemy.dialects import postgresql

revision: str = "0006_m4_matching_alerts"
down_revision: str | None = "0009_m7_billing_privacy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "matches",
    "notifications",
    "notification_deliveries",
    "integrations",
    "push_subscriptions",
)


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


def _fk(name: str, target: str, *, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete="CASCADE"),
        nullable=nullable,
    )


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[object]:
    kwargs: dict[str, object] = {"nullable": nullable}
    if default_now:
        kwargs["server_default"] = sa.func.now()
    return sa.Column(name, sa.DateTime(timezone=True), **kwargs)


def _jsonb(name: str, *, nullable: bool = False) -> sa.Column[object]:
    if nullable:
        return sa.Column(name, postgresql.JSONB())
    return sa.Column(
        name, postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    )


def upgrade() -> None:
    # --- matches (M4-01) -------------------------------------------------------------------------
    op.create_table(
        "matches",
        _uuid_pk(),
        _tenant_id(),
        _fk("profile_id", "company_profiles.id"),
        _fk("opportunity_id", "opportunities.id"),
        sa.Column("opportunity_version", sa.Integer(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("band", sa.String(16), nullable=False),
        _jsonb("breakdown"),
        _jsonb("rationale", nullable=True),
        sa.Column("filtered_reason", sa.Text()),
        sa.Column(
            "ineligible_set_aside", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint(
            "profile_id",
            "opportunity_id",
            "opportunity_version",
            "profile_version",
            name="uq_matches_profile_opportunity_versions",
        ),
    )
    for col in ("tenant_id", "profile_id", "opportunity_id"):
        op.create_index(f"ix_matches_{col}", "matches", [col])
    op.create_index(
        "ix_matches_tenant_band_created", "matches", ["tenant_id", "band", "created_at"]
    )

    # --- notifications + deliveries (M4-09) -----------------------------------------------------
    op.create_table(
        "notifications",
        _uuid_pk(),
        _tenant_id(),
        _fk("user_id", "users.id"),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opportunities.id", ondelete="SET NULL"),
        ),
        sa.Column("pursuit_id", postgresql.UUID(as_uuid=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        _jsonb("payload"),
        _ts("read_at"),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("idempotency_key", name="uq_notifications_idempotency_key"),
    )
    for col in ("tenant_id", "user_id", "opportunity_id"):
        op.create_index(f"ix_notifications_{col}", "notifications", [col])
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["tenant_id", "user_id", "read_at", "created_at"],
    )

    op.create_table(
        "notification_deliveries",
        _uuid_pk(),
        _tenant_id(),
        _fk("notification_id", "notifications.id"),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text()),
        _ts("scheduled_for"),
        _ts("sent_at"),
        _ts("opened_at"),
        sa.Column(
            "fallback_of_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("notification_deliveries.id", ondelete="SET NULL"),
        ),
        sa.Column("fallback_reason", sa.Text()),
        sa.Column("provider_ref", sa.String(256)),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("idempotency_key", name="uq_notification_deliveries_idempotency_key"),
    )
    for col in ("tenant_id", "notification_id"):
        op.create_index(f"ix_notification_deliveries_{col}", "notification_deliveries", [col])
    op.create_index(
        "ix_notification_deliveries_status_scheduled",
        "notification_deliveries",
        ["status", "scheduled_for"],
    )

    # --- integrations (M4-11): one connection per tenant per kind -------------------------------
    op.create_table(
        "integrations",
        _uuid_pk(),
        _tenant_id(),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _jsonb("config"),
        # "env:NAME" | "sm://projects/../secrets/..." | "enc:v1:<nonce>:<ciphertext>"
        sa.Column("secret_ref", sa.Text()),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("tenant_id", "kind", name="uq_integrations_tenant_kind"),
    )
    op.create_index("ix_integrations_tenant_id", "integrations", ["tenant_id"])

    # --- web push subscriptions (M4-12) ---------------------------------------------------------
    op.create_table(
        "push_subscriptions",
        _uuid_pk(),
        _tenant_id(),
        _fk("user_id", "users.id"),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(255), nullable=False),
        sa.Column("auth", sa.String(255), nullable=False),
        sa.Column("user_agent", sa.String(512)),
        _ts("last_seen_at"),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("tenant_id", "endpoint", name="uq_push_subscriptions_tenant_endpoint"),
    )
    for col in ("tenant_id", "user_id"):
        op.create_index(f"ix_push_subscriptions_{col}", "push_subscriptions", [col])

    # --- per-category unsubscribe (M4-10, CAN-SPAM): event types the user opted out of ----------
    op.add_column(
        "user_notification_prefs",
        sa.Column(
            "unsubscribed_categories",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )

    for table in TENANT_TABLES:
        grant_app(op, table)
        enable_rls(op, table)


def downgrade() -> None:
    op.drop_column("user_notification_prefs", "unsubscribed_categories")
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
