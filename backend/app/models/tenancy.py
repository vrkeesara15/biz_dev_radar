"""Tenants, users and memberships (SPEC section 3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import Region
from app.core.plan import Plan
from app.core.roles import Role
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


def _values(enum_cls: type) -> list[str]:
    return [member.value for member in enum_cls]  # type: ignore[attr-defined]


RegionEnum = Enum(Region, name="region", values_callable=_values)
PlanEnum = Enum(Plan, name="plan_tier", values_callable=_values)
RoleEnum = Enum(Role, name="member_role", values_callable=_values)


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    region: Mapped[Region] = mapped_column(RegionEnum, nullable=False)
    plan: Mapped[Plan] = mapped_column(PlanEnum, nullable=False, server_default=text("'free'"))
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    # Region whose infrastructure holds this tenant's rows and files (SPEC section 11).
    data_residency: Mapped[Region] = mapped_column(RegionEnum, nullable=False)
    # Default LLM spend cap per pursuit in USD (SPEC 8: default 15, configurable per tenant).
    pursuit_cost_cap_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("15")
    )
    # Set by the tenant-delete job (M7-07): every tenant-scoped row is gone, the tenants
    # row and its audit_log trail are kept so the erasure itself stays auditable.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[Membership]] = relationship(back_populates="tenant")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    tz: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'UTC'"))
    locale: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'en-US'"))

    memberships: Mapped[list[Membership]] = relationship(back_populates="user")


class Membership(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="uq_memberships_user_tenant"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[Role] = mapped_column(RoleEnum, nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships")
    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
