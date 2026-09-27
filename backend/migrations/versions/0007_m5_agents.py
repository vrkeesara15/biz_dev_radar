"""M5 agent pipeline: pursuits, cost guard columns, requirements, compliance matrix and
pursuit artifacts (tenant-scoped, RLS).

Revision ID: 0007_m5_agents
Revises: 0009_m7_billing_privacy

Every M5 pipeline task (M5-02..M5-14) edits this file in place until the milestone closes
(CLAUDE.md). Numbered by merge order: 0006 and 0008 are held by the M3/M4 worktrees, so
after merging main this file chains onto main's head (0009_m7_billing_privacy) to keep a
single alembic head; the orchestrator renumbers it at merge time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.core.plan import PLAN_DEFAULTS, Resource
from migrations.rls import enable_rls, grant_app
from sqlalchemy.dialects import postgresql

revision: str = "0007_m5_agents"
down_revision: str | None = "0010_m7_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "pursuits",
    "requirements",
    "compliance_items",
    "pursuit_artifacts",
    "drafts",
    "draft_versions",
    "tasks",
    "comments",
)
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
        # Gate 1 (M5-06): who approved bid / no-bid, when and why
        _fk("decided_by", "users.id", ondelete="SET NULL", nullable=True),
        _ts("decided_at"),
        sa.Column("decision_note", sa.Text()),
        # Gate 2 (M5-10): who approved the draft package for export, and when
        _fk("package_approved_by", "users.id", ondelete="SET NULL", nullable=True),
        _ts("package_approved_at"),
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

    # --- requirements extractor (M5-04) ---------------------------------------------
    op.create_table(
        "requirements",
        _uuid_pk(),
        _tenant_id(),
        _fk("pursuit_id", "pursuits.id", ondelete="CASCADE", nullable=False),
        sa.Column("req_id", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        _fk("document_id", "opportunity_documents.id", ondelete="CASCADE", nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("volume", sa.Text()),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3)),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("pursuit_id", "req_id", name="uq_requirements_req_id"),
    )
    for col in ("tenant_id", "pursuit_id", "document_id"):
        op.create_index(f"ix_requirements_{col}", "requirements", [col])

    # --- compliance matrix (M5-05) ---------------------------------------------------
    op.create_table(
        "compliance_items",
        _uuid_pk(),
        _tenant_id(),
        _fk("pursuit_id", "pursuits.id", ondelete="CASCADE", nullable=False),
        _fk("requirement_id", "requirements.id", ondelete="CASCADE", nullable=False),
        sa.Column("section", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False, server_default=sa.text("'type'")),
        _fk("owner_user_id", "users.id", ondelete="SET NULL", nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'open'")),
        sa.Column("notes", sa.Text()),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("pursuit_id", "requirement_id", name="uq_compliance_items_requirement"),
    )
    for col in ("tenant_id", "pursuit_id", "requirement_id"):
        op.create_index(f"ix_compliance_items_{col}", "compliance_items", [col])

    op.create_table(
        "pursuit_artifacts",
        _uuid_pk(),
        _tenant_id(),
        _fk("pursuit_id", "pursuits.id", ondelete="CASCADE", nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "data", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("created_by", sa.String(16), nullable=False, server_default=sa.text("'agent'")),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("pursuit_id", "kind", "version", name="uq_pursuit_artifacts_version"),
    )
    for col in ("tenant_id", "pursuit_id"):
        op.create_index(f"ix_pursuit_artifacts_{col}", "pursuit_artifacts", [col])

    # --- drafts, versions and tasks (M5-08) ------------------------------------------
    op.create_table(
        "drafts",
        _uuid_pk(),
        _tenant_id(),
        _fk("pursuit_id", "pursuits.id", ondelete="CASCADE", nullable=False),
        sa.Column("section_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("volume", sa.String(120)),
        # FK added after draft_versions exists (the two tables reference each other)
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        _fk("approved_by", "users.id", ondelete="SET NULL", nullable=True),
        _ts("approved_at"),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.UniqueConstraint("pursuit_id", "section_id", name="uq_drafts_section"),
    )
    for col in ("tenant_id", "pursuit_id"):
        op.create_index(f"ix_drafts_{col}", "drafts", [col])

    op.create_table(
        "draft_versions",
        _uuid_pk(),
        _tenant_id(),
        _fk("draft_id", "drafts.id", ondelete="CASCADE", nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "citations", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "needs_input",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "flags", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("author", sa.String(8), nullable=False, server_default=sa.text("'agent'")),
        _fk("author_user_id", "users.id", ondelete="SET NULL", nullable=True),
        sa.Column("model", sa.String(128)),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("draft_id", "version", name="uq_draft_versions_version"),
    )
    for col in ("tenant_id", "draft_id"):
        op.create_index(f"ix_draft_versions_{col}", "draft_versions", [col])
    op.create_foreign_key(
        "fk_drafts_current_version_id_draft_versions",
        "drafts",
        "draft_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "tasks",
        _uuid_pk(),
        _tenant_id(),
        _fk("pursuit_id", "pursuits.id", ondelete="CASCADE", nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        _fk("assignee_user_id", "users.id", ondelete="SET NULL", nullable=True),
        _ts("due_at"),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'open'")),
        sa.Column("source", sa.String(8), nullable=False, server_default=sa.text("'agent'")),
        sa.Column("ref", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at", nullable=False, default_now=True),
    )
    for col in ("tenant_id", "pursuit_id"):
        op.create_index(f"ix_tasks_{col}", "tasks", [col])

    # --- review comments (M5-16) ------------------------------------------------------
    op.create_table(
        "comments",
        _uuid_pk(),
        _tenant_id(),
        _fk("pursuit_id", "pursuits.id", ondelete="CASCADE", nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        _fk("author_user_id", "users.id", ondelete="SET NULL", nullable=True),
        _ts("resolved_at"),
        _ts("created_at", nullable=False, default_now=True),
    )
    for col in ("tenant_id", "pursuit_id", "target_id"):
        op.create_index(f"ix_comments_{col}", "comments", [col])

    for table in TENANT_TABLES:
        grant_app(op, table)
        enable_rls(op, table)


def downgrade() -> None:
    op.drop_constraint("fk_agent_runs_pursuit_id_pursuits", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "pause_reason")
    op.drop_constraint("fk_drafts_current_version_id_draft_versions", "drafts", type_="foreignkey")
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
    for resource in NEW_RESOURCES:
        op.execute(f"DELETE FROM plan_limits WHERE resource = '{resource.value}'")
    op.drop_column("tenants", "pursuit_cost_cap_usd")
