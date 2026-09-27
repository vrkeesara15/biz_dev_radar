"""Row factories for tests. Owner-role inserts; RLS-free setup for fixtures."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import Region
from app.core.plan import Plan
from app.core.roles import Role
from app.models import Membership, Tenant, User
from sqlalchemy.ext.asyncio import AsyncSession


def make_tenant(**overrides: Any) -> Tenant:
    suffix = uuid.uuid4().hex[:8]
    values: dict[str, Any] = {
        "name": f"Tenant {suffix}",
        "slug": f"tenant-{suffix}",
        "region": Region.US,
        "data_residency": Region.US,
        "plan": Plan.PRO,
        "is_internal": False,
    }
    values.update(overrides)
    return Tenant(**values)


def make_user(**overrides: Any) -> User:
    suffix = uuid.uuid4().hex[:8]
    values: dict[str, Any] = {"email": f"user-{suffix}@example.com", "name": f"User {suffix}"}
    values.update(overrides)
    return User(**values)


async def create_tenant_with_owner(
    session: AsyncSession, **tenant_overrides: Any
) -> tuple[Tenant, User, Membership]:
    tenant = make_tenant(**tenant_overrides)
    user = make_user()
    session.add_all([tenant, user])
    await session.flush()
    membership = Membership(tenant_id=tenant.id, user_id=user.id, role=Role.TENANT_OWNER)
    session.add(membership)
    await session.flush()
    return tenant, user, membership
