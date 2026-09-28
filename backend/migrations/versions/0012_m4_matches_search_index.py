"""M7-10 / OQ-106: composite index for the min_score search probe.

At 2 M matches the `EXISTS (matches WHERE opportunity_id = o.id AND score >= :n)` probe
on GET /opportunities?min_score= walked ix_matches_opportunity_id and discarded rows on
tenant_id/score per candidate notice (p95 1.6 s). This index answers the probe directly.

Revision ID: 0012_m4_matches_search_index
Revises: 0011_m6_pursuits
"""

from __future__ import annotations

from alembic import op

revision: str = "0012_m4_matches_search_index"
down_revision: str | None = "0011_m6_pursuits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_matches_tenant_opportunity_score",
        "matches",
        ["tenant_id", "opportunity_id", "score"],
    )


def downgrade() -> None:
    op.drop_index("ix_matches_tenant_opportunity_score", table_name="matches")
