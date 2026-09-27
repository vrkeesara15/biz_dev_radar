"""Pursuits service (minimal for M5; M6-01 adds stage rules, dates and actions).

pursuit, created = await get_or_create(session, profile_id, opportunity_id, user)
run = await latest_run(session, pursuit.id)
artifact = await store_artifact(session, tenant_id, pursuit.id, "checklist", data)
latest = await latest_artifact(session, pursuit.id, "checklist")
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core import pursuit_stages as stages
from app.core.compliance import ARTIFACT_KINDS, CREATED_BY_AGENT
from app.core.cost_guard import raise_cap
from app.core.roles import Role
from app.models import AgentRun, CompanyProfile, Opportunity, Pursuit, PursuitArtifact, Tenant
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


async def store_artifact(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pursuit_id: uuid.UUID,
    kind: str,
    data: dict[str, Any],
    *,
    created_by: str = CREATED_BY_AGENT,
) -> PursuitArtifact:
    """Append the next version of a pursuit artifact. Versions are never overwritten, so
    a re-run keeps the history an export or an audit can point at."""
    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"unknown artifact kind {kind!r}; one of {ARTIFACT_KINDS}")
    current: int | None = (
        await session.execute(
            select(func.max(PursuitArtifact.version)).where(
                PursuitArtifact.pursuit_id == pursuit_id, PursuitArtifact.kind == kind
            )
        )
    ).scalar_one()
    artifact = PursuitArtifact(
        tenant_id=tenant_id,
        pursuit_id=pursuit_id,
        kind=kind,
        version=(current or 0) + 1,
        data=data,
        created_by=created_by,
    )
    session.add(artifact)
    await session.flush()
    return artifact


async def latest_artifact(
    session: AsyncSession, pursuit_id: uuid.UUID, kind: str
) -> PursuitArtifact | None:
    return (
        await session.execute(
            select(PursuitArtifact)
            .where(PursuitArtifact.pursuit_id == pursuit_id, PursuitArtifact.kind == kind)
            .order_by(PursuitArtifact.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


# --- M6-01: stages, pursue / watch / pass, listing ------------------------------------------


def internal_due_at(response_due_at: datetime | None) -> datetime | None:
    """SPEC 9: the internal deadline is 48 hours before the buyer's."""
    if response_due_at is None:
        return None
    return response_due_at - timedelta(hours=stages.INTERNAL_DUE_OFFSET_HOURS)


async def resolve_profile(
    session: AsyncSession, user: CurrentUser, profile_id: uuid.UUID | None = None
) -> CompanyProfile:
    """The profile a pursuit is created for: the one the caller named, else the tenant's
    only (or oldest active) profile. 404 when there is none and 409 when the tenant has
    several and named none, so a board action never guesses between two profiles."""
    if profile_id is not None:
        profile = await session.get(CompanyProfile, profile_id)
        if profile is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="profile not found")
        return profile
    rows = (
        (
            await session.execute(
                select(CompanyProfile)
                .where(CompanyProfile.is_active.is_(True))
                .order_by(CompanyProfile.created_at, CompanyProfile.id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no active company profile")
    if len(rows) > 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "profile_required",
                "message": "this tenant has several profiles; send profile_id",
                "profile_ids": [str(row.id) for row in rows],
            },
        )
    return rows[0]


def stage_conflict(pursuit: Pursuit, target: str, reason: str) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "error": "stage_transition",
            "from": pursuit.stage,
            "to": target,
            "reason": reason,
        },
    )


def move_stage(
    pursuit: Pursuit,
    target: str,
    *,
    role: Role,
    now: datetime | None = None,
) -> bool:
    """Apply `target` to the pursuit if app.core.pursuit_stages allows it, else raise 409.

    Returns True when the stage actually changed. Side effects kept here (rather than in
    the pure rules) so every caller stamps submitted_at the same way.
    """
    moment = now or datetime.now(UTC)
    ok, reason = stages.can_transition(
        pursuit.stage, target, stages.TransitionContext(decision=pursuit.decision, role=role)
    )
    if not ok:
        raise stage_conflict(pursuit, target, reason)
    if pursuit.stage == target:
        return False
    pursuit.stage = target
    if target == stages.STAGE_SUBMITTED:
        pursuit.submitted_at = moment
    return True


def list_statement(
    *,
    owner_user_id: uuid.UUID | None = None,
    stage: Sequence[str] | None = None,
    due_before: datetime | None = None,
    region: str | None = None,
    min_value_usd: Decimal | None = None,
    watch: bool | None = None,
) -> Select[Pursuit, Opportunity]:
    """Board / table listing (SPEC 9 filters: owner, due date, value, region, stage).

    Joined to the opportunity because every filter but owner and stage lives there; the
    board groups by stage client-side from the same rows.
    """
    stmt = select(Pursuit, Opportunity).join(Opportunity, Opportunity.id == Pursuit.opportunity_id)
    if owner_user_id is not None:
        stmt = stmt.where(Pursuit.owner_user_id == owner_user_id)
    if stage:
        stmt = stmt.where(Pursuit.stage.in_(list(stage)))
    if due_before is not None:
        stmt = stmt.where(Opportunity.response_due_at.is_not(None))
        stmt = stmt.where(Opportunity.response_due_at <= due_before)
    if region is not None:
        stmt = stmt.where(Opportunity.region == region)
    if min_value_usd is not None:
        value = func.coalesce(
            Opportunity.estimated_value_max_usd, Opportunity.estimated_value_min_usd
        )
        stmt = stmt.where(value.is_not(None)).where(value >= min_value_usd)
    if watch is not None:
        stmt = stmt.where(Pursuit.watch.is_(watch))
    return stmt
