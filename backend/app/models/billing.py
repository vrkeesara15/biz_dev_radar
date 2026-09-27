"""plan_limits (global), usage_ledger and the billing customer / event tables (per tenant)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
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


class BillingCustomer(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One provider customer per tenant (Stripe for us, Razorpay for in; SPEC 10.1)."""

    __tablename__ = "billing_customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_billing_customers_tenant_provider"),
    )

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subscription_id: Mapped[str | None] = mapped_column(String(128), index=True)
    # provider subscription status (active, past_due, canceled, ...); 'none' before checkout
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'none'"))
    plan: Mapped[Plan | None] = mapped_column(PlanEnum)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Indian GST invoice fields (gstin, place_of_supply, legal_name); {} for Stripe
    gst_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BillingEventRecord(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """Append-only log of processed webhook events; (provider, event_id) makes retries no-ops."""

    __tablename__ = "billing_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_billing_events_provider_event"),
    )

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    plan: Mapped[Plan | None] = mapped_column(PlanEnum)
    # minor units (cents / paise) and ISO currency of the event, when it carries money
    amount: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    gst: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
