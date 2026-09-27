"""M1 company profile: files, company_profiles and its sub-resources, notification prefs.

Revision ID: 0002_m1_profile
Revises: 0001_m0_foundation

One migration per milestone (CLAUDE.md). Later M1 tasks edit this file in place; run
`make db-reset-dev` after editing it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from migrations.rls import enable_rls, grant_app
from sqlalchemy.dialects import postgresql

revision: str = "0002_m1_profile"
down_revision: str | None = "0001_m0_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

region_t = postgresql.ENUM("us", "in", name="region", create_type=False)

# Every tenant-scoped table created here, in creation order (reversed for downgrade).
# Each gets DML grants for the app role and the standard tenant_isolation RLS policy.
TENANT_TABLES: list[str] = ["files"]


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


def _tenant_id() -> sa.Column[object]:
    return sa.Column(
        "tenant_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def _tenant_table(name: str, *columns: sa.schema.SchemaItem) -> None:
    assert name in TENANT_TABLES, f"add {name} to TENANT_TABLES"
    op.create_table(name, _uuid_pk(), _tenant_id(), *columns, _created_at())
    op.create_index(f"ix_{name}_tenant_id", name, ["tenant_id"])


def upgrade() -> None:
    # --- files (M1-11) --------------------------------------------------------------------
    _tenant_table(
        "files",
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("extension", sa.String(8), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("region", region_t, nullable=False),
        sa.Column("bucket", sa.String(128), nullable=False),
        sa.Column("key", sa.String(512), nullable=False),
        sa.Column("scan_status", sa.String(16), nullable=False, server_default=sa.text("'clean'")),
        sa.Column("scanner", sa.String(32), nullable=False, server_default=sa.text("'noop'")),
        sa.Column(
            "uploaded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.UniqueConstraint("key", name="uq_files_key"),
    )

    # --- privileges + RLS for every tenant table above --------------------------------------
    for table in TENANT_TABLES:
        grant_app(op, table)
        enable_rls(op, table)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
