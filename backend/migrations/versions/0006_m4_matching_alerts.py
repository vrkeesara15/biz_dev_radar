"""M4 matching + alerts: matches (M4-01); later M4 tasks add notifications,
notification_deliveries, integrations, push_subscriptions, match_feedback, saved_searches,
alert_rules and keyword_suggestions in place.

Revision ID: 0006_m4_matching_alerts
Revises: 0004_m5_agent_runtime  (re-pointed at merge once main carries 0005_m1_knowledge_base)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from migrations.rls import enable_rls, grant_app
from sqlalchemy.dialects import postgresql

revision: str = "0006_m4_matching_alerts"
down_revision: str | None = "0004_m5_agent_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("matches",)


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

    for table in TENANT_TABLES:
        grant_app(op, table)
        enable_rls(op, table)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
