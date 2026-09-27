"""Tenant file uploads and signed download URLs (SPEC 10.3 files, 11)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi import File as FormFile
from pydantic import BaseModel

from app.api.deps import (
    TENANT_ROLES,
    CurrentUser,
    ScannerDep,
    SettingsDep,
    StorageRouterDep,
    TenantSessionDep,
    require_role,
)
from app.core.config import Region
from app.core.roles import Role
from app.core.uploads import MAX_UPLOAD_BYTES, UploadRejected, UploadTooLarge
from app.models import File, Tenant
from app.services.audit import AuditHint
from app.services.scanner import ScannerUnavailableError
from app.services.uploads import REJECTED_ACTION, UPLOAD_ACTION, UploadService
from app.services.users import ensure_user_membership

router = APIRouter(prefix="/files", tags=["files"])

UPLOAD_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER)
READ_CHUNK = 1024 * 1024

UploaderDep = Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))]
ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]


class FileOut(BaseModel):
    id: uuid.UUID
    filename: str
    extension: str
    kind: str
    content_type: str
    size_bytes: int
    sha256: str
    region: Region
    scan_status: str
    created_at: datetime


class SignedUrlOut(BaseModel):
    id: uuid.UUID
    url: str
    expires_in: int
    expires_at: datetime


def file_out(row: File) -> FileOut:
    return FileOut(
        id=row.id,
        filename=row.filename,
        extension=row.extension,
        kind=row.kind,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        region=row.region,
        scan_status=row.scan_status,
        created_at=row.created_at,
    )


async def read_capped(upload: UploadFile, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    """Read the whole part but stop as soon as it is known to exceed the cap."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(READ_CHUNK):
        total += len(chunk)
        if total > limit:
            raise UploadTooLarge("file exceeds the upload size limit", size=total, limit=limit)
        chunks.append(chunk)
    return b"".join(chunks)


def _reject(request: Request, filename: str, exc: UploadRejected) -> HTTPException:
    meta = {"reason": exc.reason, "filename": filename, **exc.details}
    request.state.audit = AuditHint(action=REJECTED_ACTION, object_type="file", meta=meta)
    return HTTPException(exc.status, detail={"error": exc.reason, **exc.details})


@router.post("", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    user: UploaderDep,
    session: TenantSessionDep,
    storage_router: StorageRouterDep,
    scanner: ScannerDep,
    file: Annotated[UploadFile, FormFile(description="multipart file part")],
) -> FileOut:
    """Upload one file (allow-list, 50 MB cap, virus scan) into the tenant's regional bucket."""
    filename = file.filename or ""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES + READ_CHUNK:
        raise _reject(
            request,
            filename,
            UploadTooLarge(
                "request body exceeds the upload size limit",
                size=int(declared),
                limit=MAX_UPLOAD_BYTES,
            ),
        )
    # files.uploaded_by references users: provision the caller on first use (as /me does)
    provisioned = await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    tenant = await session.get(Tenant, user.tenant_id)
    if provisioned is None or tenant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="unknown tenant")
    storage = storage_router.for_region(tenant.data_residency)
    service = UploadService(session, storage=storage, scanner=scanner)
    try:
        data = await read_capped(file)
        row = await service.store(
            tenant_id=tenant.id,
            region=tenant.data_residency,
            filename=filename,
            data=data,
            uploaded_by=provisioned.user_id,
        )
    except UploadRejected as exc:
        raise _reject(request, filename, exc) from exc
    except ScannerUnavailableError as exc:
        request.state.audit = AuditHint(
            action=REJECTED_ACTION,
            object_type="file",
            meta={"reason": "scanner_unavailable", "filename": filename},
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail={"error": "scanner_unavailable"}
        ) from exc
    request.state.audit = AuditHint(
        action=UPLOAD_ACTION,
        object_type="file",
        object_id=str(row.id),
        meta={"filename": row.filename, "size_bytes": row.size_bytes, "kind": row.kind},
    )
    return file_out(row)


async def _get_file(session: TenantSessionDep, file_id: uuid.UUID) -> File:
    row = await session.get(File, file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="file not found")
    return row


@router.get("/{file_id}", response_model=FileOut)
async def read_file(file_id: uuid.UUID, user: ReaderDep, session: TenantSessionDep) -> FileOut:
    return file_out(await _get_file(session, file_id))


@router.get("/{file_id}/url", response_model=SignedUrlOut)
async def file_url(
    file_id: uuid.UUID,
    user: ReaderDep,
    session: TenantSessionDep,
    storage_router: StorageRouterDep,
    settings: SettingsDep,
) -> SignedUrlOut:
    """Short-lived signed download URL (default 15 minutes, SIGNED_URL_EXPIRES_SECONDS)."""
    row = await _get_file(session, file_id)
    expires = settings.signed_url_expires_seconds
    url = await storage_router.for_region(row.region).signed_url(row.key, expires)
    return SignedUrlOut(
        id=row.id,
        url=url,
        expires_in=expires,
        expires_at=datetime.now(UTC) + timedelta(seconds=expires),
    )
