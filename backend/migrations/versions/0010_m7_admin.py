"""M7-08 admin console: support_access_grants.

Revision ID: 0010_m7_admin
Revises: 0009_m7_billing_privacy

Global table (no tenant_id, no RLS, no app-role grant): a support-access grant is
platform bookkeeping about a tenant, written and read only by the owner-role admin
session (app/models/support.py). It is listed in RLS_EXEMPT_TABLES in
tests/isolation/test_rls.py for that reason.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_m7_admin"
down_revision: str | None = "0009_m7_billing_privacy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "support_access_grants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "target_tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "admin_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_support_access_grants_target_expires",
        "support_access_grants",
        ["target_tenant_id", "expires_at"],
    )
    op.create_index(
        "ix_support_access_grants_admin_user_id", "support_access_grants", ["admin_user_id"]
    )


def downgrade() -> None:
    op.drop_table("support_access_grants")
