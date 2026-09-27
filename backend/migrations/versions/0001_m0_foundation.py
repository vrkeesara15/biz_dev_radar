"""M0 foundation: extensions, tenancy tables, plan limits, usage ledger, audit log, RLS.

Revision ID: 0001_m0_foundation
Revises: None

One migration per milestone (CLAUDE.md). Later M0 tasks edit this file in place.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.core.plan import plan_limit_rows
from migrations.rls import TENANT_EXPR, enable_rls, enable_rls_expr, grant_app, revoke_app
from sqlalchemy.dialects import postgresql

revision: str = "0001_m0_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REGION_VALUES = ("us", "in")
PLAN_VALUES = ("free", "pro", "enterprise")
ROLE_VALUES = ("platform_admin", "tenant_owner", "bid_manager", "writer", "reviewer", "viewer")

# Enum objects with create_type=False: types are created explicitly in upgrade() so the
# same object can be reused across tables without double-creation.
region_t = postgresql.ENUM(*REGION_VALUES, name="region", create_type=False)
plan_t = postgresql.ENUM(*PLAN_VALUES, name="plan_tier", create_type=False)
role_t = postgresql.ENUM(*ROLE_VALUES, name="member_role", create_type=False)


def _uuid_pk() -> sa.Column[object]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _tenant_id(*, fk: bool = True) -> sa.Column[object]:
    args = [sa.ForeignKey("tenants.id", ondelete="CASCADE")] if fk else []
    return sa.Column("tenant_id", postgresql.UUID(as_uuid=True), *args, nullable=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    bind = op.get_bind()
    for enum in (region_t, plan_t, role_t):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "tenants",
        _uuid_pk(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("region", region_t, nullable=False),
        sa.Column("plan", plan_t, nullable=False, server_default=sa.text("'free'")),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("data_residency", region_t, nullable=False),
        _created_at(),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )

    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(200)),
        sa.Column("tz", sa.String(64), nullable=False, server_default=sa.text("'UTC'")),
        sa.Column("locale", sa.String(16), nullable=False, server_default=sa.text("'en-US'")),
        _created_at(),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "memberships",
        _uuid_pk(),
        _tenant_id(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", role_t, nullable=False),
        _created_at(),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_memberships_user_tenant"),
    )
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    op.create_table(
        "plan_limits",
        _uuid_pk(),
        sa.Column("plan", plan_t, nullable=False),
        sa.Column("resource", sa.String(64), nullable=False),
        sa.Column("limit_value", sa.Integer()),
        sa.UniqueConstraint("plan", "resource", name="uq_plan_limits_plan_resource"),
    )
    op.bulk_insert(
        sa.table(
            "plan_limits",
            sa.column("plan", plan_t),
            sa.column("resource", sa.String()),
            sa.column("limit_value", sa.Integer()),
        ),
        plan_limit_rows(),
    )

    op.create_table(
        "usage_ledger",
        _uuid_pk(),
        _tenant_id(),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("ref", sa.String(200)),
        _created_at(),
    )
    op.create_index("ix_usage_ledger_tenant_id", "usage_ledger", ["tenant_id"])
    op.create_index(
        "ix_usage_ledger_tenant_metric_period", "usage_ledger", ["tenant_id", "metric", "period"]
    )

    op.create_table(
        "audit_log",
        _uuid_pk(),
        _tenant_id(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("object_type", sa.String(64)),
        sa.Column("object_id", sa.String(128)),
        sa.Column("ip", sa.String(64)),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("request_id", sa.String(128)),
        sa.Column(
            "meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_at", "audit_log", ["at"])

    # --- privileges for the application role (owner keeps everything) -----------------
    for table in ("tenants", "users", "memberships", "usage_ledger"):
        grant_app(op, table)
    # The init script's ALTER DEFAULT PRIVILEGES may already have granted full DML, so the
    # read-only / append-only tables need explicit REVOKEs, not just narrower GRANTs.
    grant_app(op, "plan_limits", "SELECT")
    revoke_app(op, "plan_limits", "INSERT, UPDATE, DELETE")
    # audit_log is append-only for the application (SPEC section 11).
    grant_app(op, "audit_log", "SELECT, INSERT")
    revoke_app(op, "audit_log", "UPDATE, DELETE")

    # --- row-level security -----------------------------------------------------------
    for table in ("memberships", "usage_ledger", "audit_log"):
        enable_rls(op, table)
    # A tenant sees only its own tenants row.
    enable_rls_expr(op, "tenants", f"id = {TENANT_EXPR}")
    # Users are visible to a tenant only through a membership in that tenant. New users are
    # provisioned by the owner role (services.users) because INSERT ... RETURNING is also
    # subject to the SELECT policy and a brand-new user has no membership yet.
    enable_rls_expr(
        op,
        "users",
        "EXISTS (SELECT 1 FROM memberships m "
        f"WHERE m.user_id = users.id AND m.tenant_id = {TENANT_EXPR})",
    )


def downgrade() -> None:
    # The users policy references memberships; drop it before the tables.
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON users")
    for table in ("audit_log", "usage_ledger", "plan_limits", "memberships", "users", "tenants"):
        op.drop_table(table)
    bind = op.get_bind()
    for enum in (role_t, plan_t, region_t):
        enum.drop(bind, checkfirst=True)
    # Extensions are shared infrastructure (created by the DB init script too); keep them.
