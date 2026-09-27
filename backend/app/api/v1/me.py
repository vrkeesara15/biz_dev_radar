from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUserDep
from app.core.roles import Role
from app.services.users import ensure_user_membership

router = APIRouter(prefix="/me", tags=["me"])


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str | None
    tenant_id: uuid.UUID
    tenant_slug: str
    role: Role


@router.get("", response_model=MeOut)
async def read_me(user: CurrentUserDep) -> MeOut:
    """Current user as seen by the API. Provisions the user/membership on first call."""
    provisioned = await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    if provisioned is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="unknown tenant")
    return MeOut(
        user_id=provisioned.user_id,
        email=provisioned.email,
        name=provisioned.name,
        tenant_id=provisioned.tenant_id,
        tenant_slug=provisioned.tenant_slug,
        role=user.role,
    )
