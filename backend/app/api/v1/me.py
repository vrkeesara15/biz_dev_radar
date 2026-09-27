from __future__ import annotations

import uuid
from zoneinfo import ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from app.api.deps import CurrentUserDep, TenantSessionDep
from app.core.roles import Role
from app.core.timezones import validate_locale, validate_timezone
from app.models import User
from app.services.audit import AuditHint
from app.services.users import ensure_user_membership

router = APIRouter(prefix="/me", tags=["me"])


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str | None
    tz: str
    locale: str
    tenant_id: uuid.UUID
    tenant_slug: str
    role: Role


class MeUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    tz: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=16)

    @field_validator("tz")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return validate_timezone(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc

    @field_validator("locale")
    @classmethod
    def _locale(cls, value: str | None) -> str | None:
        return None if value is None else validate_locale(value)


async def _me(user: CurrentUserDep) -> tuple[uuid.UUID, str]:
    provisioned = await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    if provisioned is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="unknown tenant")
    return provisioned.user_id, provisioned.tenant_slug


def _out(row: User, user: CurrentUserDep, slug: str) -> MeOut:
    return MeOut(
        user_id=row.id,
        email=row.email,
        name=row.name,
        tz=row.tz,
        locale=row.locale,
        tenant_id=user.tenant_id,
        tenant_slug=slug,
        role=user.role,
    )


@router.get("", response_model=MeOut)
async def read_me(user: CurrentUserDep, session: TenantSessionDep) -> MeOut:
    """Current user as seen by the API. Provisions the user/membership on first call."""
    user_id, slug = await _me(user)
    row = await session.get(User, user_id)
    if row is None:  # pragma: no cover - only if RLS and provisioning disagree
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="user not visible in tenant")
    return _out(row, user, slug)


@router.patch("", response_model=MeOut)
async def update_me(
    body: MeUpdate, user: CurrentUserDep, session: TenantSessionDep, request: Request
) -> MeOut:
    """Update the caller's own profile fields (name, tz, locale)."""
    user_id, slug = await _me(user)
    row = await session.get(User, user_id)
    if row is None:  # pragma: no cover
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="user not visible in tenant")
    changes = body.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(row, field, value)
    await session.flush()
    request.state.audit = AuditHint(
        action="me.update",
        object_type="user",
        object_id=str(row.id),
        meta={"fields": sorted(changes)},
    )
    return _out(row, user, slug)
