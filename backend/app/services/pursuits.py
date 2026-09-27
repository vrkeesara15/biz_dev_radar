"""Pursuits service (minimal for M5; M6-01 adds stage rules, dates and actions).

pursuit = await get_or_create(session, profile_id, opportunity_id, user)
run = await latest_run(session, pursuit.id)
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.cost_guard import raise_cap
from app.models import AgentRun, CompanyProfile, Opportunity, Pursuit, Tenant
from app.services.users import ensure_user_membership

PIPELINE_RUN_KIND = "pipeline"


async def get_or_create(
    session: AsyncSession,
    profile_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    user: CurrentUser,
) -> tuple[Pursuit, bool]:
    """The tenant's pursuit of `opportunity_id` with `profile_id` (unique per pair).
    Returns (pursuit, created). 404 when the profile (RLS) or the opportunity is unknown."""
    existing = (
        await session.execute(
            select(Pursuit).where(
                Pursuit.profile_id == profile_id, Pursuit.opportunity_id == opportunity_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    if await session.get(CompanyProfile, profile_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="profile not found")
    if await session.get(Opportunity, opportunity_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="opportunity not found")
    # created_by / owner_user_id reference users: make sure the JIT row exists (OQ-16)
    await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    pursuit = Pursuit(
        tenant_id=user.tenant_id,
        profile_id=profile_id,
        opportunity_id=opportunity_id,
        created_by=user.id,
        owner_user_id=user.id,
    )
    session.add(pursuit)
    await session.flush()
    return pursuit, True


async def get_pursuit(session: AsyncSession, pursuit_id: uuid.UUID) -> Pursuit:
    row = await session.get(Pursuit, pursuit_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="pursuit not found")
    return row


async def get_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:  # pragma: no cover - the token's tenant always exists under RLS
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tenant not found")
    return tenant


async def latest_run(session: AsyncSession, pursuit_id: uuid.UUID) -> AgentRun | None:
    return (
        await session.execute(
            select(AgentRun)
            .where(AgentRun.pursuit_id == pursuit_id)
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def approve_budget(pursuit: Pursuit, tenant: Tenant, additional_usd: Decimal) -> Decimal:
    """Raise the pursuit's cap by `additional_usd` on top of the effective cap."""
    current = (
        pursuit.cost_cap_usd if pursuit.cost_cap_usd is not None else tenant.pursuit_cost_cap_usd
    )
    pursuit.cost_cap_usd = raise_cap(Decimal(current), additional_usd)
    return Decimal(pursuit.cost_cap_usd)
