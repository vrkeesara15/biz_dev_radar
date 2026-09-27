"""M7 billing and privacy: billing_customers, billing_events (M7-04); consents and
data_requests, tenants.deleted_at (M7-07). All tenant-scoped with RLS.

Revision ID: 0009_m7_billing_privacy
Revises: 0005_m1_knowledge_base

Numbered by merge order (CLAUDE.md): 0006-0008 are held by the M3/M4/M5 worktrees and this
file is re-pointed at the last of them when they merge. Later M7 tasks edit it in place.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from migrations.rls import enable_rls, grant_app, revoke_app
from sqlalchemy.dialects import postgresql

revision: str = "0009_m7_billing_privacy"
down_revision: str | None = "0005_m1_knowledge_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLAN_ENUM = postgresql.ENUM("free", "pro", "enterprise", name="plan_tier", create_type=False)


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


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[object]:
    kwargs: dict[str, object] = {"nullable": nullable}
    if default_now:
        kwargs["server_default"] = sa.func.now()
    return sa.Column(name, sa.DateTime(timezone=True), **kwargs)


def _jsonb(name: str) -> sa.Column[object]:
    return sa.Column(
        name, postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    )


def upgrade() -> None:
    # --- M7-04 billing -------------------------------------------------------------------
    op.create_table(
        "billing_customers",
        _uuid_pk(),
        _tenant_id(),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("customer_id", sa.String(128), nullable=False),
        sa.Column("subscription_id", sa.String(128)),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'none'")),
        sa.Column("plan", PLAN_ENUM),
        _ts("current_period_end"),
        _jsonb("gst_details"),
        _ts("updated_at", nullable=False, default_now=True),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_billing_customers_tenant_provider"),
    )
    for col in ("tenant_id", "customer_id", "subscription_id"):
        op.create_index(f"ix_billing_customers_{col}", "billing_customers", [col])
    grant_app(op, "billing_customers")
    enable_rls(op, "billing_customers")

    op.create_table(
        "billing_events",
        _uuid_pk(),
        _tenant_id(),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("plan", PLAN_ENUM),
        sa.Column("amount", sa.BigInteger()),
        sa.Column("currency", sa.String(3)),
        _jsonb("payload"),
        _jsonb("gst"),
        _ts("processed_at", nullable=False, default_now=True),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("provider", "event_id", name="uq_billing_events_provider_event"),
    )
    op.create_index("ix_billing_events_tenant_id", "billing_events", ["tenant_id"])
    # append-only for the application role (like audit_log)
    grant_app(op, "billing_events", "SELECT, INSERT")
    revoke_app(op, "billing_events", "UPDATE, DELETE")
    enable_rls(op, "billing_events")


def downgrade() -> None:
    op.drop_table("billing_events")
    op.drop_table("billing_customers")
