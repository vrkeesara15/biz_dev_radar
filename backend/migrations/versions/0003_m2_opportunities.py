"""M2 ingestion: sources, source_runs, opportunities and related global tables.

Revision ID: 0003_m2_opportunities
Revises: 0002_m1_profile

One migration per milestone (CLAUDE.md). Later M2 tasks edit this file in place.

All tables here are GLOBAL: opportunities are public notices shared by every tenant, so
they carry no tenant_id and no RLS policy. tests/isolation/test_rls.py lists them in
RLS_EXEMPT_TABLES. The application role still needs explicit DML grants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.models.opportunities import EMBEDDING_DIM, FTS_EXPR
from migrations.rls import grant_app
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0003_m2_opportunities"
down_revision: str | None = "0002_m1_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

region_t = postgresql.ENUM("us", "in", name="region", create_type=False)

NOTICE_TYPE_VALUES = (
    "rfi",
    "sources_sought",
    "presolicitation",
    "rfp",
    "rfq",
    "combined",
    "grant",
    "forecast",
    "award",
    "eoi",
    "gem_bid",
    "reverse_auction",
    "corrigendum",
    "special",
)
OPPORTUNITY_STATUS_VALUES = ("open", "closing_soon", "closed", "cancelled", "awarded")
notice_type_t = postgresql.ENUM(*NOTICE_TYPE_VALUES, name="notice_type", create_type=False)
opp_status_t = postgresql.ENUM(
    *OPPORTUNITY_STATUS_VALUES, name="opportunity_status", create_type=False
)

GLOBAL_TABLES = (
    "sources",
    "source_runs",
    "opportunities",
    "opportunity_versions",
    "opportunity_documents",
    "document_chunks",
    "awards_enrichment",
    "agency_spend_stats",
)


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


def _fk_opportunity(*, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(
        "opportunity_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=nullable,
    )


def _text_array(name: str, item: sa.types.TypeEngine[object]) -> sa.Column[object]:
    return sa.Column(name, postgresql.ARRAY(item), nullable=False, server_default=sa.text("'{}'"))


def _jsonb(name: str, default: str) -> sa.Column[object]:
    return sa.Column(
        name, postgresql.JSONB(), nullable=False, server_default=sa.text(f"'{default}'::jsonb")
    )


def _money(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.Numeric(18, 2))


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (notice_type_t, opp_status_t):
        enum.create(bind, checkfirst=True)

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

    # --- opportunities (SPEC 5.3) ------------------------------------------------------
    op.create_table(
        "opportunities",
        _uuid_pk(),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("region", region_t, nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("notice_type", notice_type_t, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description_text", sa.Text()),
        sa.Column("summary_ai", sa.Text()),
        sa.Column("solicitation_number", sa.String(128)),
        sa.Column(
            "parent_opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opportunities.id", ondelete="SET NULL"),
        ),
        sa.Column("buyer_org", sa.Text()),
        sa.Column("buyer_sub_org", sa.Text()),
        sa.Column("buyer_office", sa.Text()),
        _text_array("buyer_hierarchy", sa.Text()),
        _text_array("naics", sa.String(16)),
        _text_array("psc", sa.String(16)),
        _text_array("aln", sa.String(16)),
        _text_array("india_category", sa.String(64)),
        sa.Column("set_aside", sa.String(32)),
        sa.Column("reservation", sa.String(64)),
        sa.Column("place_of_performance", postgresql.JSONB()),
        _money("estimated_value_min"),
        _money("estimated_value_max"),
        _money("estimated_value_min_usd"),
        _money("estimated_value_max_usd"),
        _money("emd_amount"),
        _money("tender_fee"),
        _ts("posted_at"),
        _ts("questions_due_at"),
        _ts("prebid_meeting_at"),
        _ts("response_due_at"),
        _ts("opening_at"),
        _ts("archive_at"),
        sa.Column("source_tz", sa.String(64), nullable=False, server_default=sa.text("'UTC'")),
        _jsonb("contacts", "[]"),
        _jsonb("eligibility", "{}"),
        sa.Column("incumbent", sa.Text()),
        _money("prior_award_value"),
        sa.Column("prior_pop_end", sa.Date()),
        sa.Column("status", opp_status_t, nullable=False, server_default=sa.text("'open'")),
        sa.Column(
            "detail_status", sa.String(16), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "duplicate_of",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opportunities.id", ondelete="SET NULL"),
        ),
        sa.Column("reference_norm", sa.String(128)),
        sa.Column("buyer_norm", sa.Text()),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("raw_ref", sa.Text()),
        _jsonb("extra", "{}"),
        _ts("last_seen_at"),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.UniqueConstraint("source_id", "external_id", name="uq_opportunities_source_external"),
    )
    for col in (
        "source_id",
        "notice_type",
        "solicitation_number",
        "parent_opportunity_id",
        "posted_at",
        "response_due_at",
        "status",
        "duplicate_of",
        "buyer_norm",
    ):
        op.create_index(f"ix_opportunities_{col}", "opportunities", [col])
    op.create_index(
        "ix_opportunities_reference_buyer", "opportunities", ["reference_norm", "buyer_norm"]
    )
    op.create_index(
        "ix_opportunities_region_status_due",
        "opportunities",
        ["region", "status", "response_due_at"],
    )
    op.create_index(
        "ix_opportunities_title_trgm",
        "opportunities",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_opportunities_fts", "opportunities", [sa.text(FTS_EXPR)], postgresql_using="gin"
    )
    op.create_index("ix_opportunities_naics", "opportunities", ["naics"], postgresql_using="gin")

    op.create_table(
        "opportunity_versions",
        _uuid_pk(),
        _fk_opportunity(),
        sa.Column("version", sa.Integer(), nullable=False),
        _jsonb("diff", "{}"),
        _text_array("changes", sa.String(32)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        sa.UniqueConstraint("opportunity_id", "version", name="uq_opportunity_versions_version"),
    )
    op.create_index(
        "ix_opportunity_versions_opportunity_id", "opportunity_versions", ["opportunity_id"]
    )

    op.create_table(
        "opportunity_documents",
        _uuid_pk(),
        _fk_opportunity(),
        sa.Column("file_name", sa.Text()),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default=sa.text("'attachment'")),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("hash", sa.String(64)),
        sa.Column("size", sa.BigInteger()),
        sa.Column("pages", sa.Integer()),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("parsed_text_ref", sa.Text()),
        sa.Column("parse_error", sa.String(500)),
        sa.Column("ocr_pages", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.UniqueConstraint("opportunity_id", "url", name="uq_opportunity_documents_url"),
    )
    op.create_index(
        "ix_opportunity_documents_opportunity_id", "opportunity_documents", ["opportunity_id"]
    )

    op.create_table(
        "document_chunks",
        _uuid_pk(),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opportunity_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_index"),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])

    op.create_table(
        "awards_enrichment",
        _uuid_pk(),
        _fk_opportunity(nullable=True),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("award_id", sa.String(128), nullable=False),
        sa.Column("incumbent", sa.Text()),
        _money("prior_award_value"),
        sa.Column("prior_pop_start", sa.Date()),
        sa.Column("prior_pop_end", sa.Date()),
        sa.Column("num_offers", sa.Integer()),
        sa.Column("agency", sa.Text()),
        sa.Column("sub_agency", sa.Text()),
        sa.Column("naics", sa.String(16)),
        sa.Column("psc", sa.String(16)),
        sa.Column("solicitation_number", sa.String(128)),
        sa.Column("match_method", sa.String(32)),
        sa.Column("recompete_watch", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_ref", sa.Text()),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
    )
    for col in ("opportunity_id", "prior_pop_end", "naics"):
        op.create_index(f"ix_awards_enrichment_{col}", "awards_enrichment", [col])
    op.create_index(
        "ix_awards_enrichment_source_award", "awards_enrichment", ["source_id", "award_id"]
    )

    op.create_table(
        "agency_spend_stats",
        _uuid_pk(),
        sa.Column("agency", sa.Text(), nullable=False),
        sa.Column("sub_agency", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("naics", sa.String(16), nullable=False, server_default=sa.text("''")),
        sa.Column("psc", sa.String(16), nullable=False, server_default=sa.text("''")),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("obligations", sa.Numeric(20, 2), nullable=False),
        sa.Column("award_count", sa.Integer(), nullable=False),
        sa.Column(
            "source_id", sa.String(64), nullable=False, server_default=sa.text("'usaspending'")
        ),
        _ts("computed_at", nullable=False, default_now=True),
        sa.UniqueConstraint(
            "agency", "sub_agency", "naics", "psc", "fiscal_year", name="uq_agency_spend_stats_key"
        ),
    )
    for col in ("agency", "naics", "psc", "fiscal_year"):
        op.create_index(f"ix_agency_spend_stats_{col}", "agency_spend_stats", [col])

    for table in GLOBAL_TABLES:
        grant_app(op, table)


def downgrade() -> None:
    for table in reversed(GLOBAL_TABLES):
        op.drop_table(table)
    bind = op.get_bind()
    for enum in (opp_status_t, notice_type_t):
        enum.drop(bind, checkfirst=True)
