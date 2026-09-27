"""Generic CRUD router for profile child tables (codes, keywords, certifications, ...).

    router = crud_router(
        name="certifications", singular="certification", model=Certification,
        create=CertificationIn, update=CertificationUpdate, out=CertificationOut,
        write_roles=PROFILE_EDIT_ROLES, validate=check_kind_region,
    )

Routes (all under /profiles/{profile_id}/<name>): GET list, POST 201, GET/PUT/DELETE one.
Every write bumps the parent profile's version and sets an audit hint.

No `from __future__ import annotations` here: FastAPI must evaluate the closure-local
schema classes and role dependencies used in the route signatures.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TENANT_ROLES, CurrentUser, TenantSessionDep, require_role
from app.api.v1.profiles.common import bump_version, get_profile
from app.core.roles import Role
from app.models import CompanyProfile
from app.services.audit import AuditHint

# (session, profile, changes, existing row or None) -> may raise HTTPException
Validator = Callable[[AsyncSession, CompanyProfile, dict[str, Any], Any], Awaitable[None]]


async def _flush(session: AsyncSession, singular: str) -> None:
    """Flush; a unique-constraint violation becomes 409 instead of a 500."""
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if "unique" in str(exc.orig).lower() or "duplicate" in str(exc.orig).lower():
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail=f"{singular} already exists"
            ) from exc
        raise


def crud_router(
    *,
    name: str,
    singular: str,
    model: type[Any],
    create: type[BaseModel],
    update: type[BaseModel],
    out: type[BaseModel],
    write_roles: Sequence[Role],
    validate: Validator | None = None,
    order_by: Sequence[Any] = (),
    to_out: Callable[[Any], BaseModel] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/{profile_id}/" + name, tags=[f"profiles:{name}"])
    writer = Annotated[CurrentUser, Depends(require_role(*write_roles))]
    reader = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]
    render = to_out or (lambda row: out.model_validate(row, from_attributes=True))
    ordering = list(order_by) or [model.created_at, model.id]

    async def _item(session: AsyncSession, profile: CompanyProfile, item_id: uuid.UUID) -> Any:
        row = await session.get(model, item_id)
        if row is None or row.profile_id != profile.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{singular} not found")
        return row

    def _hint(action: str, row: Any, profile: CompanyProfile, **meta: Any) -> AuditHint:
        return AuditHint(
            action=f"profile.{singular}.{action}",
            object_type=name,
            object_id=str(row.id),
            meta={"profile_id": str(profile.id), **meta},
        )

    def _dump(body: BaseModel) -> dict[str, Any]:
        changes = body.model_dump(exclude_unset=True, mode="python")
        for key, value in list(changes.items()):
            if isinstance(value, BaseModel):
                changes[key] = value.model_dump(mode="json")
            elif isinstance(value, list) and value and isinstance(value[0], BaseModel):
                changes[key] = [v.model_dump(mode="json") for v in value]
        return changes

    @router.get("", response_model=list[out], name=f"list_{name}")  # type: ignore[valid-type]
    async def list_items(
        profile_id: uuid.UUID,
        session: TenantSessionDep,
        user: reader,
    ) -> list[BaseModel]:
        profile = await get_profile(session, profile_id)
        result = await session.execute(
            select(model).where(model.profile_id == profile.id).order_by(*ordering)
        )
        rows: list[Any] = list(result.scalars())
        return [render(r) for r in rows]

    @router.post(
        "",
        response_model=out,
        status_code=status.HTTP_201_CREATED,
        name=f"create_{singular}",
    )
    async def create_item(
        profile_id: uuid.UUID,
        body: create,  # type: ignore[valid-type]
        session: TenantSessionDep,
        user: writer,
        request: Request,
    ) -> BaseModel:
        profile = await get_profile(session, profile_id)
        changes = _dump(body)
        if validate is not None:
            await validate(session, profile, changes, None)
        row = model(tenant_id=profile.tenant_id, profile_id=profile.id, **changes)
        session.add(row)
        bump_version(profile)
        await _flush(session, singular)
        await session.refresh(row)
        request.state.audit = _hint("create", row, profile)
        return render(row)

    @router.get("/{item_id}", response_model=out, name=f"read_{singular}")
    async def read_item(
        profile_id: uuid.UUID,
        item_id: uuid.UUID,
        session: TenantSessionDep,
        user: reader,
    ) -> BaseModel:
        profile = await get_profile(session, profile_id)
        return render(await _item(session, profile, item_id))

    @router.put("/{item_id}", response_model=out, name=f"update_{singular}")
    async def update_item(
        profile_id: uuid.UUID,
        item_id: uuid.UUID,
        body: update,  # type: ignore[valid-type]
        session: TenantSessionDep,
        user: writer,
        request: Request,
    ) -> BaseModel:
        profile = await get_profile(session, profile_id)
        row = await _item(session, profile, item_id)
        changes = _dump(body)
        if validate is not None:
            await validate(session, profile, changes, row)
        for key, value in changes.items():
            setattr(row, key, value)
        bump_version(profile)
        await _flush(session, singular)
        await session.refresh(row)
        request.state.audit = _hint("update", row, profile, fields=sorted(changes))
        return render(row)

    @router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT, name=f"delete_{singular}")
    async def delete_item(
        profile_id: uuid.UUID,
        item_id: uuid.UUID,
        session: TenantSessionDep,
        user: writer,
        request: Request,
    ) -> None:
        profile = await get_profile(session, profile_id)
        row = await _item(session, profile, item_id)
        request.state.audit = _hint("delete", row, profile)
        await session.delete(row)
        bump_version(profile)
        await session.flush()

    return router
