"""Pursuit exports (SPEC 8, 11, 10.3).

    POST /api/v1/pursuits/{id}/export?format=docx|pdf|xlsx|zip   202 + a signed URL
    GET  /api/v1/pursuits/{id}/exports                           what has been rendered
    GET  /api/v1/pursuits/{id}/exports/{export_id}               audited download URL
    POST /api/v1/pursuits/{id}/mark-final                        drop the DRAFT footer

Every export carries the "DRAFT - internal" footer until the package is marked final and
always the verify-on-portal disclaimer (`app.core.exports.footer_lines`). Creating and
downloading an export are both written to audit_log: SPEC 11 requires "every read of a
draft/export" to be logged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    SettingsDep,
    StorageRouterDep,
    TenantSessionDep,
    require_role,
)
from app.core.compliance import ARTIFACT_RED_TEAM
from app.core.exports import FORMATS
from app.core.roles import Role
from app.models import Export, Opportunity, Pursuit
from app.services import exports as export_svc
from app.services import pursuits as pursuit_svc
from app.services.audit import AuditHint, audit
from app.services.storage import Storage, StorageRouter
from app.services.users import ensure_user_membership

router = APIRouter(prefix="/pursuits", tags=["exports"])

# SPEC 3: a viewer never sees draft text, so it never downloads a proposal export either.
READ_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER, Role.REVIEWER)
MANAGER_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER)
ReaderDep = Annotated[CurrentUser, Depends(require_role(*READ_ROLES))]
ManagerDep = Annotated[CurrentUser, Depends(require_role(*MANAGER_ROLES))]

AUDIT_EXPORT_CREATED = "export.created"
AUDIT_EXPORT_READ = "export.read"
AUDIT_PACKAGE_FINAL = "pursuit.package_final"


class ExportOut(BaseModel):
    id: uuid.UUID
    pursuit_id: uuid.UUID
    format: str
    version: int
    file_name: str
    content_type: str
    size_bytes: int
    renderer: str | None
    final: bool
    created_by: uuid.UUID | None
    created_at: datetime
    # present on create and on the download route
    url: str | None = None
    expires_in: int | None = None
    expires_at: datetime | None = None


class ExportListOut(BaseModel):
    pursuit_id: uuid.UUID
    items: list[ExportOut] = Field(default_factory=list)
    count: int = 0
    package_final: bool = False


class MarkFinalIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class MarkFinalOut(BaseModel):
    pursuit_id: uuid.UUID
    package_final: bool
    package_final_at: datetime | None
    package_final_by: uuid.UUID | None
    note: str | None = None


def export_out(row: Export, *, url: str | None = None, expires: int | None = None) -> ExportOut:
    return ExportOut(
        id=row.id,
        pursuit_id=row.pursuit_id,
        format=row.format,
        version=row.version,
        file_name=row.file_name,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        renderer=row.renderer,
        final=row.final,
        created_by=row.created_by,
        created_at=row.created_at,
        url=url,
        expires_in=expires,
        expires_at=None if expires is None else datetime.now(UTC) + timedelta(seconds=expires),
    )


async def _storage(session: AsyncSession, pursuit: Pursuit, router_: StorageRouter) -> Storage:
    """The bucket for the notice's region (data residency, SPEC 11)."""
    opportunity = await session.get(Opportunity, pursuit.opportunity_id)
    if opportunity is None:  # pragma: no cover - the FK guarantees it
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="opportunity not found")
    return router_.for_region(opportunity.region)


@router.post("/{pursuit_id}/export", response_model=ExportOut, status_code=status.HTTP_202_ACCEPTED)
async def create_export(
    pursuit_id: uuid.UUID,
    session: TenantSessionDep,
    user: ReaderDep,
    request: Request,
    storage_router: StorageRouterDep,
    settings: SettingsDep,
    export_format: Annotated[str, Query(alias="format")] = "docx",
) -> JSONResponse:
    """Render the pursuit's package and store it (SPEC 8 outputs).

    The DOCX uses the tenant's uploaded template when it has one, the PDF is rendered
    from that DOCX, the XLSX carries the matrix, checklist and pricing sheets, and the
    ZIP is named by the solicitation's own file-naming rule.
    """
    if export_format not in FORMATS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"format must be one of {list(FORMATS)}",
        )
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    storage = await _storage(session, pursuit, storage_router)
    await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    package = await export_svc.load_package(session, pursuit, storage=storage)
    row = await export_svc.create_export(
        session,
        user.tenant_id,
        pursuit,
        export_format,
        package,
        storage=storage,
        created_by=user.id,
    )
    request.state.audit = AuditHint(
        action=AUDIT_EXPORT_CREATED,
        object_type="export",
        object_id=str(row.id),
        meta={
            "pursuit_id": str(pursuit.id),
            "format": row.format,
            "version": row.version,
            "file_name": row.file_name,
            "size_bytes": row.size_bytes,
            "renderer": row.renderer,
            "final": row.final,
            "sections": len(package.sections),
        },
    )
    expires = settings.signed_url_expires_seconds
    url = await storage.signed_url(row.storage_key, expires)
    out = export_out(row, url=url, expires=expires)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=out.model_dump(mode="json"))


@router.get("/{pursuit_id}/exports", response_model=ExportListOut)
async def list_exports(
    pursuit_id: uuid.UUID, session: TenantSessionDep, user: ReaderDep
) -> ExportListOut:
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    rows = await export_svc.list_exports(session, pursuit.id)
    return ExportListOut(
        pursuit_id=pursuit.id,
        items=[export_out(row) for row in rows],
        count=len(rows),
        package_final=bool(pursuit.package_final),
    )


@router.get("/{pursuit_id}/exports/{export_id}", response_model=ExportOut)
async def read_export(
    pursuit_id: uuid.UUID,
    export_id: uuid.UUID,
    session: TenantSessionDep,
    user: ReaderDep,
    storage_router: StorageRouterDep,
    settings: SettingsDep,
) -> ExportOut:
    """A short-lived signed download URL. The read is audited (SPEC 11)."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    row = await export_svc.get_export(session, pursuit.id, export_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="export not found")
    storage = await _storage(session, pursuit, storage_router)
    expires = settings.signed_url_expires_seconds
    url = await storage.signed_url(row.storage_key, expires)
    await audit(
        session,
        AUDIT_EXPORT_READ,
        row,
        user_id=user.id,
        meta={
            "pursuit_id": str(pursuit.id),
            "format": row.format,
            "version": row.version,
            "file_name": row.file_name,
        },
    )
    await session.commit()
    return export_out(row, url=url, expires=expires)


@router.post("/{pursuit_id}/mark-final", response_model=MarkFinalOut)
async def mark_final(
    pursuit_id: uuid.UUID,
    body: MarkFinalIn,
    session: TenantSessionDep,
    user: ManagerDep,
    request: Request,
) -> MarkFinalOut:
    """Drop the "DRAFT - internal" footer from exports rendered from now on (SPEC 11).

    Only after Gate 2: a package nobody approved is never final. Exports already
    rendered keep the footer they were rendered with.
    """
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    if pursuit.package_approved_at is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="the package must be approved at Gate 2 before it can be marked final",
        )
    if await pursuit_svc.latest_artifact(session, pursuit.id, ARTIFACT_RED_TEAM) is None:
        raise HTTPException(  # pragma: no cover - approval already requires the report
            status.HTTP_409_CONFLICT, detail="the red-team reviewer has not run yet"
        )
    await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    pursuit.package_final = True
    pursuit.package_final_at = datetime.now(UTC)
    pursuit.package_final_by = user.id
    meta: dict[str, Any] = {"note": body.note}
    request.state.audit = AuditHint(
        action=AUDIT_PACKAGE_FINAL,
        object_type="pursuit",
        object_id=str(pursuit.id),
        meta=meta,
    )
    await session.flush()
    return MarkFinalOut(
        pursuit_id=pursuit.id,
        package_final=pursuit.package_final,
        package_final_at=pursuit.package_final_at,
        package_final_by=pursuit.package_final_by,
        note=body.note,
    )


__all__ = ["router"]
