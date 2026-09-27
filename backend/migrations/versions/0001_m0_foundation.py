"""M0 foundation: extensions, tenancy tables, RLS.

Revision ID: 0001_m0_foundation
Revises: None

One migration per milestone (CLAUDE.md). Later M0 tasks edit this file in place.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_m0_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def downgrade() -> None:
    # Extensions are shared infrastructure (created by the DB init script too); keep them.
    pass
