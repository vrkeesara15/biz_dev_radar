"""M0-05: model inventory and tenant-scoping conventions."""

from app.models import (
    AgencySpendStat,
    AuditLog,
    AwardsEnrichment,
    Base,
    DocumentChunk,
    Membership,
    Opportunity,
    OpportunityDocument,
    OpportunityVersion,
    PlanLimit,
    Source,
    SourceRun,
    Tenant,
    UsageLedger,
    User,
)


def test_m0_tables_registered() -> None:
    assert {
        "tenants",
        "users",
        "memberships",
        "plan_limits",
        "usage_ledger",
        "audit_log",
    } <= set(Base.metadata.tables)


def test_tenant_scoped_tables_have_not_null_tenant_id() -> None:
    for model in (Membership, UsageLedger, AuditLog):
        col = model.__table__.c.tenant_id
        assert col.nullable is False
        assert any("tenant_id" in ix.columns for ix in model.__table__.indexes)


def test_global_tables_have_no_tenant_id() -> None:
    for model in (
        Tenant,
        User,
        PlanLimit,
        Source,
        SourceRun,
        Opportunity,
        OpportunityVersion,
        OpportunityDocument,
        DocumentChunk,
        AwardsEnrichment,
        AgencySpendStat,
    ):
        assert "tenant_id" not in model.__table__.c


def test_enum_values() -> None:
    assert set(Tenant.__table__.c.region.type.enums) == {"us", "in"}
    assert set(Tenant.__table__.c.plan.type.enums) == {"free", "pro", "enterprise"}
    assert set(Membership.__table__.c.role.type.enums) == {
        "platform_admin",
        "tenant_owner",
        "bid_manager",
        "writer",
        "reviewer",
        "viewer",
    }
