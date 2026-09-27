"""plan_limits (global) and usage_ledger (per tenant)."""

from __future__ import annotations

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.plan import Plan
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.tenancy import PlanEnum


class PlanLimit(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "plan_limits"
    __table_args__ = (UniqueConstraint("plan", "resource", name="uq_plan_limits_plan_resource"),)

    plan: Mapped[Plan] = mapped_column(PlanEnum, nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL means unlimited.
    limit_value: Mapped[int | None] = mapped_column(Integer)


class UsageLedger(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "usage_ledger"
    __table_args__ = (
        Index("ix_usage_ledger_tenant_metric_period", "tenant_id", "metric", "period"),
    )

    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # 'YYYY-MM' for monthly metrics, 'lifetime' otherwise (app.core.plan.period_key).
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    ref: Mapped[str | None] = mapped_column(String(200))
