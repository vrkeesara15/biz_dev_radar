"""User + membership provisioning (owner role; see PROGRESS.md OQ-16)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.db import Database, get_database
from app.core.roles import Role
from app.models import Membership, Tenant, User


@dataclass(frozen=True, slots=True)
class ProvisionedUser:
    user_id: uuid.UUID
    email: str
    name: str | None
    tenant_id: uuid.UUID
    tenant_slug: str
    role: Role
    created_user: bool
    created_membership: bool


async def ensure_user_membership(
    *,
    user_id: uuid.UUID,
    email: str,
    tenant_id: uuid.UUID,
    role: Role,
    name: str | None = None,
    database: Database | None = None,
) -> ProvisionedUser | None:
    """Idempotently create the user row and its membership in `tenant_id`.

    Returns None when the tenant does not exist. Never changes an existing membership's
    role: the token role drives RBAC for the request, the DB row is the durable record.
    """
    db = database or get_database()
    async with db.owner_session(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            return None
        created_user = created_membership = False
        user = await session.get(User, user_id)
        if user is None:
            user = (
                await session.execute(select(User).where(User.email == email.lower()))
            ).scalar_one_or_none()
        if user is None:
            user = User(id=user_id, email=email.lower(), name=name)
            session.add(user)
            await session.flush()
            created_user = True
        membership = (
            await session.execute(
                select(Membership).where(
                    Membership.user_id == user.id, Membership.tenant_id == tenant.id
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            membership = Membership(user_id=user.id, tenant_id=tenant.id, role=role)
            session.add(membership)
            await session.flush()
            created_membership = True
        return ProvisionedUser(
            user_id=user.id,
            email=user.email,
            name=user.name,
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            role=membership.role,
            created_user=created_user,
            created_membership=created_membership,
        )
