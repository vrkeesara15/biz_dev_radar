"""Tenant members (SPEC 3: the tenant owner manages users and roles; SPEC 10.4 screen 8).

    GET    /api/v1/tenant/members                  every member of the caller's tenant
    POST   /api/v1/tenant/members/invite           tenant_owner: {email, role}
    PATCH  /api/v1/tenant/members/{membership_id}  tenant_owner: {role}
    DELETE /api/v1/tenant/members/{membership_id}  tenant_owner

The invited user's `users` row and their membership are created by the owner-role
provisioning path in app.services.users (PROGRESS.md OQ-16: a brand-new user has no
membership yet, so RLS cannot insert them through the app role). An address that already
has a users row — because the person belongs to another tenant — is attached to this
tenant instead of duplicated.

Guards: the LAST tenant_owner can neither be demoted nor removed (409), so a tenant can
never lose its only administrator; a member may remove themselves under the same rule.
RBAC still reads the role from the token (OQ-18), so a role change here takes effect for
that user at their next token refresh.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from app.api.deps import (
    TENANT_ROLES,
    CurrentUser,
    DispatcherDep,
    TenantSessionDep,
    require_role,
)
from app.core.roles import Role
from app.models import Membership, User
from app.notify.core import NotificationEvent as NotifyEvent
from app.notify.core import Recipient
from app.services.audit import AuditHint
from app.services.users import ensure_user_membership

router = APIRouter(prefix="/tenant/members", tags=["tenant"])

# event type of the invitation email (app/notify/templates/invite.*)
MEMBER_INVITED = "member.invited"
INVITE_CHANNELS: tuple[str, ...] = ("email",)

OwnerDep = Annotated[CurrentUser, Depends(require_role(Role.TENANT_OWNER))]

# Deliberately permissive: an address is validated by the invitation actually arriving,
# not by a regex. This only rejects what can never be an address (no @, no dot, spaces).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


class MemberOut(BaseModel):
    """One membership row. `id` is the membership id used by PATCH and DELETE."""

    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    name: str | None
    role: Role
    joined_at: datetime


class MemberInviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, Field(min_length=3, max_length=320)]
    role: Role

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        clean = value.strip().lower()
        if not _EMAIL_RE.match(clean):
            raise ValueError(f"{value!r} is not an email address")
        return clean

    @field_validator("role")
    @classmethod
    def _role(cls, value: Role) -> Role:
        return _tenant_role(value)


class MemberRoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role

    @field_validator("role")
    @classmethod
    def _role(cls, value: Role) -> Role:
        return _tenant_role(value)


def _tenant_role(value: Role) -> Role:
    if value not in TENANT_ROLES:
        allowed = ", ".join(role.value for role in TENANT_ROLES)
        raise ValueError(f"{value.value} is not a tenant role; expected one of: {allowed}")
    return value


def _out(membership: Membership, user: User) -> MemberOut:
    return MemberOut(
        id=membership.id,
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=membership.role,
        joined_at=membership.created_at,
    )


async def _rows(session: TenantSessionDep) -> list[tuple[Membership, User]]:
    result = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .order_by(Membership.created_at, User.email)
    )
    return [(membership, user) for membership, user in result.all()]


async def _load(session: TenantSessionDep, membership_id: uuid.UUID) -> tuple[Membership, User]:
    """One membership of the caller's tenant (RLS); 404 for anything else."""
    row = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.id == membership_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="member not found")
    membership, user = row
    return membership, user


async def _owner_count(session: TenantSessionDep) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Membership)
                .where(Membership.role == Role.TENANT_OWNER)
            )
        ).scalar_one()
    )


async def _refuse_if_last_owner(session: TenantSessionDep, membership: Membership) -> None:
    if membership.role is Role.TENANT_OWNER and await _owner_count(session) <= 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="the last tenant owner cannot be removed or demoted",
        )


@router.get("", response_model=list[MemberOut])
async def list_members(session: TenantSessionDep) -> list[MemberOut]:
    """Everyone in the caller's tenant, oldest membership first. Any member may read it."""
    return [_out(membership, row) for membership, row in await _rows(session)]


@router.post("/invite", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def invite_member(
    body: MemberInviteIn,
    owner: OwnerDep,
    session: TenantSessionDep,
    request: Request,
    dispatcher: DispatcherDep,
) -> MemberOut:
    """Create or attach the user with that role and send them an invitation email."""
    existing = (
        await session.execute(
            select(Membership)
            .join(User, User.id == Membership.user_id)
            .where(User.email == body.email)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="already a member of this tenant")

    provisioned = await ensure_user_membership(
        user_id=uuid.uuid4(),
        email=body.email,
        tenant_id=owner.tenant_id,
        role=body.role,
    )
    if provisioned is None:  # pragma: no cover - the token's tenant always exists here
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="unknown tenant")
    if not provisioned.created_membership:  # pragma: no cover - lost race with a second invite
        raise HTTPException(status.HTTP_409_CONFLICT, detail="already a member of this tenant")

    membership, user = await _load_by_user(session, provisioned.user_id)
    request.state.audit = AuditHint(
        action="member.invite",
        object_type="membership",
        object_id=str(membership.id),
        meta={
            "email": user.email,
            "role": membership.role.value,
            "created_user": provisioned.created_user,
        },
    )
    await dispatcher.dispatch(
        session,
        NotifyEvent(
            event_type=MEMBER_INVITED,
            tenant_id=owner.tenant_id,
            dedupe_key=str(membership.id),
            payload={
                "tenant_name": provisioned.tenant_slug,
                "invited_by": owner.email,
                "role": membership.role.value,
            },
        ),
        [
            Recipient(
                user_id=user.id,
                channels=INVITE_CHANNELS,
                email=user.email,
                name=user.name,
                tz=user.tz,
            )
        ],
    )
    return _out(membership, user)


async def _load_by_user(session: TenantSessionDep, user_id: uuid.UUID) -> tuple[Membership, User]:
    row = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.user_id == user_id)
        )
    ).first()
    if row is None:  # pragma: no cover - the membership was just committed
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="membership not visible")
    membership, user = row
    return membership, user


@router.patch("/{membership_id}", response_model=MemberOut)
async def update_member_role(
    membership_id: uuid.UUID,
    body: MemberRoleIn,
    owner: OwnerDep,
    session: TenantSessionDep,
    request: Request,
) -> MemberOut:
    """Change one member's role. The last tenant_owner keeps theirs (409)."""
    membership, user = await _load(session, membership_id)
    request.state.audit = AuditHint(
        action="member.role_change",
        object_type="membership",
        object_id=str(membership.id),
        meta={"from": membership.role.value, "to": body.role.value, "user_id": str(user.id)},
    )
    if body.role is not membership.role:
        await _refuse_if_last_owner(session, membership)
        membership.role = body.role
        await session.flush()
    return _out(membership, user)


@router.delete("/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    membership_id: uuid.UUID,
    owner: OwnerDep,
    session: TenantSessionDep,
    request: Request,
) -> None:
    """Remove a member from the tenant (their users row and other tenants are untouched)."""
    membership, user = await _load(session, membership_id)
    request.state.audit = AuditHint(
        action="member.remove",
        object_type="membership",
        object_id=str(membership.id),
        meta={"role": membership.role.value, "user_id": str(user.id)},
    )
    await _refuse_if_last_owner(session, membership)
    await session.delete(membership)
    await session.flush()
