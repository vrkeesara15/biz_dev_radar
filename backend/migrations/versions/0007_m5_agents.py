"""M5 agent pipeline: pursuits, cost guard columns, requirements, compliance matrix and
pursuit artifacts (tenant-scoped, RLS).

Revision ID: 0007_m5_agents
Revises: 0004_m5_agent_runtime

Every M5 pipeline task (M5-02..M5-14) edits this file in place until the milestone closes
(CLAUDE.md). down_revision is re-pointed by the orchestrator at merge time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.core.plan import PLAN_DEFAULTS, Resource
from migrations.rls import enable_rls, grant_app
from sqlalchemy.dialects import postgresql

revision: str = "0007_m5_agents"
down_revision: str | None = "0004_m5_agent_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("pursuits",)
# plan_limits rows added by this milestone (0001 seeds PLAN_DEFAULTS on a fresh database,
# so the insert is idempotent for databases migrated before this revision existed).
NEW_RESOURCES = (Resource.AGENT_BUDGET_USD_MONTH,)


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


def _fk(name: str, target: str, *, ondelete: str, nullable: bool) -> sa.Column[object]:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
    )


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[object]:
    kwargs: dict[str, object] = {"nullable": nullable}
    if default_now:
        kwargs["server_default"] = sa.func.now()
    return sa.Column(name, sa.DateTime(timezone=True), **kwargs)


def _seed_plan_limits() -> None:
    for resource in NEW_RESOURCES:
        for plan, limits in PLAN_DEFAULTS.items():
            value = limits[resource]
            limit_sql = "NULL" if value is None else str(int(value))
            op.execute(
                "INSERT INTO plan_limits (plan, resource, limit_value) "
                f"VALUES ('{plan.value}', '{resource.value}', {limit_sql}) "
                "ON CONFLICT (plan, resource) DO NOTHING"
            )


def upgrade() -> None:
    # --- cost guard (M5-02) ----------------------------------------------------------
    op.add_column(
        "tenants",
        sa.Column(
            "pursuit_cost_cap_usd",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("15"),
        ),
    )
    _seed_plan_limits()

    op.create_table(
        "pursuits",
        _uuid_pk(),
        _tenant_id(),
        _fk("profile_id", "company_profiles.id", ondelete="CASCADE", nullable=False),
        _fk("opportunity_id", "opportunities.id", ondelete="CASCADE", nullable=False),
        sa.Column("stage", sa.String(32), nullable=False, server_default=sa.text("'identified'")),
        _fk("owner_user_id", "users.id", ondelete="SET NULL", nullable=True),
        sa.Column("decision", sa.String(16)),
        _ts("internal_due_at"),
        _fk("created_by", "users.id", ondelete="SET NULL", nullable=True),
        sa.Column("cost_cap_usd", sa.Numeric(12, 2)),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.UniqueConstraint("profile_id", "opportunity_id", name="uq_pursuits_profile_opportunity"),
    )
    for col in ("tenant_id", "profile_id", "opportunity_id"):
        op.create_index(f"ix_pursuits_{col}", "pursuits", [col])

    op.add_column("agent_runs", sa.Column("pause_reason", sa.Text()))
    op.create_foreign_key(
        "fk_agent_runs_pursuit_id_pursuits",
        "agent_runs",
        "pursuits",
        ["pursuit_id"],
        ["id"],
        ondelete="SET NULL",
    )

    for table in TENANT_TABLES:
        grant_app(op, table)
        enable_rls(op, table)


def downgrade() -> None:
    op.drop_constraint("fk_agent_runs_pursuit_id_pursuits", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "pause_reason")
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
    for resource in NEW_RESOURCES:
        op.execute(f"DELETE FROM plan_limits WHERE resource = '{resource.value}'")
    op.drop_column("tenants", "pursuit_cost_cap_usd")
