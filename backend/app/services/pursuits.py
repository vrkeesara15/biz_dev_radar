"""Pursuits service (minimal for M5; M6-01 adds stage rules, dates and actions).

pursuit = await get_or_create(session, profile_id, opportunity_id, user)
run = await latest_run(session, pursuit.id)
artifact = await store_artifact(session, tenant_id, pursuit.id, "checklist", data)
latest = await latest_artifact(session, pursuit.id, "checklist")
gates = cleared_gates(pursuit)                 # ("gate1",) once the bid is approved
move_stage(pursuit, "drafting")                # 409 without a bid decision
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.pipeline import GATE_1
from app.api.deps import CurrentUser
from app.core.compliance import ARTIFACT_KINDS, CREATED_BY_AGENT
from app.core.cost_guard import raise_cap
from app.core.roles import Role
from app.models import AgentRun, CompanyProfile, Opportunity, Pursuit, PursuitArtifact, Tenant
from app.models.pursuit import (
    DECISION_BID,
    DECISION_NO_BID,
    STAGE_DRAFTING,
    STAGE_NO_BID,
    STAGES,
)
from app.services.users import ensure_user_membership

PIPELINE_RUN_KIND = "pipeline"

# SPEC 3: the tenant owner may always approve; the profile's `required_approver_roles`
# (default {bid_manager}) names who else may record the Gate 1 decision.
ALWAYS_APPROVER_ROLES: frozenset[Role] = frozenset({Role.TENANT_OWNER})


def approver_roles(profile: CompanyProfile | None) -> frozenset[Role]:
    """Roles allowed to record the bid/no-bid decision for this profile."""
    configured: Iterable[str] = (profile.required_approver_roles or []) if profile else []
    roles = set(ALWAYS_APPROVER_ROLES)
    for name in configured:
        try:
            roles.add(Role(name))
        except ValueError:  # an unknown role never widens access
            continue
    return frozenset(roles)


def cleared_gates(pursuit: Pursuit) -> tuple[str, ...]:
    """The human gates this pursuit has passed (app.agents.pipeline.plan_steps)."""
    return (GATE_1,) if pursuit.decision == DECISION_BID else ()


def check_stage_transition(pursuit: Pursuit, new_stage: str) -> None:
    """Stage rules enforced today (M6-01 extends this helper with the full board rules):

    - the stage must be one of SPEC 9's stages,
    - Drafting needs an approved bid decision (Gate 1) -> 409 otherwise.
    """
    if new_stage not in STAGES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown stage {new_stage!r}; one of {list(STAGES)}",
        )
    if new_stage == STAGE_DRAFTING and pursuit.decision != DECISION_BID:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="stage 'drafting' requires an approved bid decision (Gate 1)",
        )


def move_stage(pursuit: Pursuit, new_stage: str) -> str:
    """Check the transition and apply it. Returns the previous stage."""
    check_stage_transition(pursuit, new_stage)
    previous = pursuit.stage
    pursuit.stage = new_stage
    return previous


def record_decision(
    pursuit: Pursuit,
    decision: str,
    user_id: uuid.UUID,
    *,
    note: str | None = None,
    now: datetime | None = None,
) -> str:
    """Record the Gate 1 decision and move the pursuit's stage. Returns the old stage."""
    if decision not in (DECISION_BID, DECISION_NO_BID):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"unknown decision {decision!r}"
        )
    pursuit.decision = decision
    pursuit.decided_by = user_id
    pursuit.decided_at = now or datetime.now(UTC)
    pursuit.decision_note = note
    return move_stage(pursuit, STAGE_DRAFTING if decision == DECISION_BID else STAGE_NO_BID)


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
