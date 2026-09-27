"""M5 agent runtime: agent_runs and agent_steps (tenant-scoped, RLS).

Revision ID: 0004_m5_agent_runtime
Revises: 0003_m2_opportunities

Numbered by merge order (CLAUDE.md): the runtime lands with M2 so the summary agent
(M2-13) can use it; later M5 tasks (requirements, compliance_items, drafts, exports)
edit this file in place until M5 closes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from migrations.rls import enable_rls, grant_app
from sqlalchemy.dialects import postgresql

revision: str = "0004_m5_agent_runtime"
down_revision: str | None = "0003_m2_opportunities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("agent_runs", "agent_steps")


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


def _int0(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0"))


def _money0(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.Numeric(12, 6), nullable=False, server_default=sa.text("0"))


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        _uuid_pk(),
        _tenant_id(),
        sa.Column("pursuit_id", postgresql.UUID(as_uuid=True)),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'queued'")),
        _ts("started_at"),
        _ts("finished_at"),
        _money0("cost_usd"),
        _int0("tokens_in"),
        _int0("tokens_out"),
        sa.Column("error", sa.Text()),
        sa.Column(
            "params", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        _ts("created_at", nullable=False, default_now=True),
    )
    for col in ("tenant_id", "pursuit_id", "status"):
        op.create_index(f"ix_agent_runs_{col}", "agent_runs", [col])
    op.create_index("ix_agent_runs_tenant_status", "agent_runs", ["tenant_id", "status"])

    op.create_table(
        "agent_steps",
        _uuid_pk(),
        _tenant_id(),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("input_ref", sa.Text()),
        sa.Column("output", postgresql.JSONB()),
        sa.Column("model", sa.String(128)),
        _int0("tokens_in"),
        _int0("tokens_out"),
        _int0("cache_read_tokens"),
        _money0("cost_usd"),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'running'")),
        sa.Column("error", sa.Text()),
        _ts("started_at"),
        _ts("finished_at"),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_agent_steps_tenant_id", "agent_steps", ["tenant_id"])
    op.create_index("ix_agent_steps_run_agent", "agent_steps", ["run_id", "agent"])

    for table in TENANT_TABLES:
        grant_app(op, table)
        enable_rls(op, table)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
