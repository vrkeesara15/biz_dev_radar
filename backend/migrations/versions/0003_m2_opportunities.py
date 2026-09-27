"""M2 ingestion: sources, source_runs, opportunities and related global tables.

Revision ID: 0003_m2_opportunities
Revises: 0001_m0_foundation  (re-pointed to 0002_m1_profile by the orchestrator at merge)

One migration per milestone (CLAUDE.md). Later M2 tasks edit this file in place.

All tables here are GLOBAL: opportunities are public notices shared by every tenant, so
they carry no tenant_id and no RLS policy. tests/isolation/test_rls.py lists them in
RLS_EXEMPT_TABLES. The application role still needs explicit DML grants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from migrations.rls import grant_app
from sqlalchemy.dialects import postgresql

revision: str = "0003_m2_opportunities"
down_revision: str | None = "0001_m0_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

region_t = postgresql.ENUM("us", "in", name="region", create_type=False)

GLOBAL_TABLES = ("sources", "source_runs")


def _uuid_pk() -> sa.Column[object]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[object]:
    kwargs: dict[str, object] = {"nullable": nullable}
    if default_now:
        kwargs["server_default"] = sa.func.now()
    return sa.Column(name, sa.DateTime(timezone=True), **kwargs)


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("source_id", sa.String(64), primary_key=True),
        sa.Column("region", region_t, nullable=False),
        sa.Column("schedule", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        _ts("watermark_at"),
        sa.Column("cursor", sa.String(512)),
        _ts("last_run_at"),
        sa.Column("last_status", sa.String(16)),
        sa.Column("health_status", sa.String(24), nullable=False, server_default=sa.text("'ok'")),
        sa.Column("health_message", sa.String(1000)),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
    )

    op.create_table(
        "source_runs",
        _uuid_pk(),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
            nullable=False,
        ),
        _ts("started_at", nullable=False, default_now=True),
        _ts("finished_at"),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'running'")),
        sa.Column("fetched", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("upserted", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "errors", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        _ts("watermark"),
        sa.Column("cursor", sa.String(512)),
    )
    op.create_index("ix_source_runs_source_id", "source_runs", ["source_id"])
    op.create_index("ix_source_runs_started_at", "source_runs", ["started_at"])

    for table in GLOBAL_TABLES:
        grant_app(op, table)


def downgrade() -> None:
    for table in reversed(GLOBAL_TABLES):
        op.drop_table(table)
