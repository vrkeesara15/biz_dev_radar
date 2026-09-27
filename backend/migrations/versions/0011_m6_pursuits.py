"""M6 pursuits, key dates, reminders, collaboration and calendar.

Revision ID: 0011_m6_pursuits
Revises: 0006_m4_matching_alerts

One migration per milestone (CLAUDE.md); later M6 tasks edit this file in place. The
number follows merge order, so the orchestrator may renumber it when the parallel
worktrees land.

M6-01 extends `pursuits` (stage CHECK, watch, pass_reason, submitted_at, decided_by /
decided_at).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.core.pursuit_stages import STAGES
from sqlalchemy.dialects import postgresql

revision: str = "0011_m6_pursuits"
down_revision: str | None = "0006_m4_matching_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGE_LIST = ", ".join(f"'{stage}'" for stage in STAGES)


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[object]:
    kwargs: dict[str, object] = {"nullable": nullable}
    if default_now:
        kwargs["server_default"] = sa.func.now()
    return sa.Column(name, sa.DateTime(timezone=True), **kwargs)


def upgrade() -> None:
    _upgrade_pursuits()


def downgrade() -> None:
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
