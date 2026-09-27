"""M6 pursuits, key dates, reminders, collaboration and calendar.

Revision ID: 0011_m6_pursuits
Revises: 0006_m4_matching_alerts

One migration per milestone (CLAUDE.md); later M6 tasks edit this file in place. The
number follows merge order, so the orchestrator may renumber it when the parallel
worktrees land.

M6-01 extends `pursuits` (stage CHECK, watch, pass_reason, submitted_at, decided_by /
decided_at). M6-02 adds `pursuit_dates`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.core.key_dates import KIND_CUSTOM, KINDS, SOURCE_AUTO, SOURCES
from app.core.pursuit_stages import STAGES
from migrations.rls import enable_rls, grant_app
from sqlalchemy.dialects import postgresql

revision: str = "0011_m6_pursuits"
down_revision: str | None = "0006_m4_matching_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGE_LIST = ", ".join(f"'{stage}'" for stage in STAGES)
KIND_LIST = ", ".join(f"'{kind}'" for kind in KINDS)
SOURCE_LIST = ", ".join(f"'{source}'" for source in SOURCES)

TENANT_TABLES = ("pursuit_dates",)


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[object]:
    kwargs: dict[str, object] = {"nullable": nullable}
    if default_now:
        kwargs["server_default"] = sa.func.now()
    return sa.Column(name, sa.DateTime(timezone=True), **kwargs)


def upgrade() -> None:
    _upgrade_pursuits()
    _upgrade_pursuit_dates()
    for table in TENANT_TABLES:
        grant_app(op, table)
        enable_rls(op, table)


def downgrade() -> None:
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
    op.add_column(
        "pursuits",
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("pursuits", _ts("decided_at"))
    # any pre-M6 row carries a free-text stage; normalise before the CHECK bites
    op.execute(f"UPDATE pursuits SET stage = 'identified' WHERE stage NOT IN ({STAGE_LIST})")
    op.create_check_constraint("stage", "pursuits", f"stage IN ({STAGE_LIST})")
    op.create_index("ix_pursuits_stage", "pursuits", ["stage"])
    op.create_index("ix_pursuits_owner_user_id", "pursuits", ["owner_user_id"])


def _downgrade_pursuits() -> None:
    op.drop_index("ix_pursuits_owner_user_id", table_name="pursuits")
    op.drop_index("ix_pursuits_stage", table_name="pursuits")
    op.drop_constraint("stage", "pursuits", type_="check")
    for column in ("decided_at", "decided_by", "submitted_at", "pass_reason", "watch"):
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
