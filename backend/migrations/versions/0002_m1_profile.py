"""M1 company profile: files, company_profiles and its sub-resources, notification prefs.

Revision ID: 0002_m1_profile
Revises: 0001_m0_foundation

One migration per milestone (CLAUDE.md). Later M1 tasks edit this file in place; run
`make db-reset-dev` after editing it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from migrations.rls import enable_rls, grant_app
from sqlalchemy.dialects import postgresql

revision: str = "0002_m1_profile"
down_revision: str | None = "0001_m0_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

region_t = postgresql.ENUM("us", "in", name="region", create_type=False)
legal_structure_t = postgresql.ENUM(
    "llc",
    "corporation",
    "pvt_ltd",
    "llp",
    "partnership",
    "proprietorship",
    "other",
    name="legal_structure",
    create_type=False,
)
sam_status_t = postgresql.ENUM(
    "active", "inactive", "expired", "pending", name="sam_status", create_type=False
)
udyam_category_t = postgresql.ENUM(
    "micro", "small", "medium", name="udyam_category", create_type=False
)
local_supplier_class_t = postgresql.ENUM(
    "class_1", "class_2", "non_local", name="local_supplier_class", create_type=False
)
mse_ownership_t = postgresql.ENUM(
    "none", "sc_st", "women", "sc_st_women", name="mse_ownership", create_type=False
)
certification_kind_t = postgresql.ENUM(
    "8a", "hubzone", "wosb", "edwosb", "sdvosb", "vosb", "sdb",
    "fcl", "cmmc", "fedramp", "soc2", "iso_27001", "iso_9001", "iso_20000", "cmmi",
    "stqc", "cert_in",
    name="certification_kind", create_type=False,
)
NEW_ENUMS = (
    legal_structure_t,
    sam_status_t,
    udyam_category_t,
    local_supplier_class_t,
    mse_ownership_t,
    certification_kind_t,
)

# Every tenant-scoped table created here, in creation order (reversed for downgrade).
# Each gets DML grants for the app role and the standard tenant_isolation RLS policy.
TENANT_TABLES: list[str] = ["files", "company_profiles", "certifications"]


def _uuid_pk() -> sa.Column[object]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _tenant_id() -> sa.Column[object]:
    return sa.Column(
        "tenant_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def _tenant_table(name: str, *columns: sa.schema.SchemaItem) -> None:
    assert name in TENANT_TABLES, f"add {name} to TENANT_TABLES"
    op.create_table(name, _uuid_pk(), _tenant_id(), *columns, _created_at())
    op.create_index(f"ix_{name}_tenant_id", name, ["tenant_id"])


def _encrypted() -> sa.Text:
    """AES-GCM token column (app.models.types.EncryptedString stores TEXT)."""
    return sa.Text()


def _profile_id() -> sa.Column[object]:
    return sa.Column(
        "profile_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("company_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )


def _profile_child(name: str, *columns: sa.schema.SchemaItem) -> None:
    _tenant_table(name, _profile_id(), *columns)
    op.create_index(f"ix_{name}_profile_id", name, ["profile_id"])


def upgrade() -> None:
    bind = op.get_bind()
    for enum in NEW_ENUMS:
        enum.create(bind, checkfirst=True)

    # --- files (M1-11) --------------------------------------------------------------------
    _tenant_table(
        "files",
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("extension", sa.String(8), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("region", region_t, nullable=False),
        sa.Column("bucket", sa.String(128), nullable=False),
        sa.Column("key", sa.String(512), nullable=False),
        sa.Column("scan_status", sa.String(16), nullable=False, server_default=sa.text("'clean'")),
        sa.Column("scanner", sa.String(32), nullable=False, server_default=sa.text("'noop'")),
        sa.Column(
            "uploaded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.UniqueConstraint("key", name="uq_files_key"),
    )

    # --- company_profiles (M1-01 identity + registrations, SPEC 4.1) ----------------------
    _tenant_table(
        "company_profiles",
        sa.Column("region", region_t, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("legal_name", sa.String(300), nullable=False),
        sa.Column(
            "dba_names",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "addresses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("website", sa.String(500)),
        sa.Column("phone", sa.String(40)),
        sa.Column("bid_inbox_email", sa.String(320)),
        sa.Column("year_founded", sa.Integer()),
        sa.Column("legal_structure", legal_structure_t),
        # US
        sa.Column("uei", sa.String(12)),
        sa.Column("cage_code", sa.String(5)),
        sa.Column("sam_status", sam_status_t),
        sa.Column("sam_expires_on", sa.Date()),
        sa.Column("ein", _encrypted()),
        # IN
        sa.Column("pan", _encrypted()),
        sa.Column("gstin", _encrypted()),
        sa.Column("tan", _encrypted()),
        sa.Column("cin_llpin", sa.String(32)),
        sa.Column("udyam_number", sa.String(32)),
        sa.Column("udyam_category", udyam_category_t),
        sa.Column("dpiit_number", sa.String(32)),
        sa.Column("gem_seller_id", sa.String(64)),
        sa.Column("local_supplier_class", local_supplier_class_t),
        sa.Column("local_content_pct", sa.Numeric(5, 2)),
        # size and finances (4.2)
        sa.Column("employee_count_total", sa.Integer()),
        sa.Column(
            "employees_by_country",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "annual_revenue",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("net_worth_amount", sa.Numeric(18, 2)),
        sa.Column("net_worth_currency", sa.String(3)),
        sa.Column(
            "solvency_certificate_available",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "audited_fiscal_years",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default=sa.text("'{}'::integer[]"),
        ),
        sa.Column("bonding_capacity_amount", sa.Numeric(18, 2)),
        sa.Column("bonding_capacity_currency", sa.String(3)),
        sa.Column("mse_ownership", mse_ownership_t),
        # bank details (encrypted)
        sa.Column("bank_name", sa.String(200)),
        sa.Column("bank_account_number", _encrypted()),
        sa.Column("bank_routing_code", _encrypted()),
        sa.CheckConstraint(
            "year_founded BETWEEN 1800 AND 2100", name="ck_company_profiles_year_founded_range"
        ),
    )

    # --- certifications (M1-02 socio-economic, M1-05 security/compliance) ------------------
    _profile_child(
        "certifications",
        sa.Column("kind", certification_kind_t, nullable=False),
        sa.Column("cert_number", sa.String(100)),
        sa.Column("issued_by", sa.String(200)),
        sa.Column("level", sa.String(32)),
        sa.Column("issued_on", sa.Date()),
        sa.Column("expires_on", sa.Date()),
        sa.Column(
            "file_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL")
        ),
        sa.Column("notes", sa.Text()),
    )

    # --- privileges + RLS for every tenant table above --------------------------------------
    for table in TENANT_TABLES:
        grant_app(op, table)
        enable_rls(op, table)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
    bind = op.get_bind()
    for enum in reversed(NEW_ENUMS):
        enum.drop(bind, checkfirst=True)
