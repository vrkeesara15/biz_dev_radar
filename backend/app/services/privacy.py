"""Consent, data-principal requests, tenant export and tenant erasure (SPEC 11, M7-07).

    await record_consent(session, ...)                      # POST /me/consents
    await create_data_request(session, ...)                 # POST /me/data-requests
    payload = await export_user_data(session, user_id)      # access request -> JSON
    result = await export_tenant(...)                       # zip into object storage
    result = await delete_tenant(...)                       # erasure across every table

The tenant-scoped tables are derived from the SQLAlchemy metadata (every table carrying
a tenant_id column), so a new table is exported and erased the day it is added instead of
being forgotten in a hand-written list; `tests/integration/test_privacy_api.py` checks the
counts against information_schema, which would catch a table the ORM does not know about.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import Table, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.paths import tenant_export_key
from app.core.privacy import (
    ERASURE_RETAINED_TABLES,
    ConsentKind,
    DataRequestKind,
    DataRequestStatus,
    sla_due_at,
)
from app.models import Base, Consent, DataRequest, File, Membership, Tenant, User
from app.services.audit import audit
from app.services.storage import ObjectNotFoundError, StorageRouter

log = structlog.get_logger(__name__)

CONSENT_ACTION = "privacy.consent"
REQUEST_ACTION = "privacy.data_request"
EXPORT_ACTION = "privacy.tenant_export"
DELETE_ACTION = "privacy.tenant_delete"
EXPORT_CONTENT_TYPE = "application/zip"


def tenant_tables() -> list[Table]:
    """Every tenant-scoped table, parents first (SQLAlchemy's FK topological order)."""
    return [table for table in Base.metadata.sorted_tables if "tenant_id" in table.c]


def erasable_tables() -> list[Table]:
    """Tables an erasure empties, children first. audit_log is deliberately kept."""
    return [t for t in reversed(tenant_tables()) if t.name not in ERASURE_RETAINED_TABLES]


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, bytes):  # pragma: no cover - no bytea columns today
        return value.hex()
    if hasattr(value, "value") and type(value).__mro__[1:2] and isinstance(value.value, str):
        return value.value
    if isinstance(value, int | float | str | bool) or value is None:
        return value
    return str(value)


async def _rows(session: AsyncSession, table: Table, **where: Any) -> list[dict[str, Any]]:
    stmt = select(table)
    for column, value in where.items():
        stmt = stmt.where(table.c[column] == value)
    result = await session.execute(stmt)
    return [{k: _jsonable(v) for k, v in row._mapping.items()} for row in result]


# --- consent -------------------------------------------------------------------------------


async def record_consent(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: ConsentKind | str,
    version: str,
    ip: str | None = None,
    now: datetime | None = None,
) -> tuple[Consent, bool]:
    """Idempotent per (tenant, user, kind, version): returns (row, created)."""
    kind_value = ConsentKind(kind).value
    existing = (
        await session.execute(
            select(Consent).where(
                Consent.tenant_id == tenant_id,
                Consent.user_id == user_id,
                Consent.kind == kind_value,
                Consent.version == version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    row = Consent(
        tenant_id=tenant_id,
        user_id=user_id,
        kind=kind_value,
        version=version,
        accepted_at=now or datetime.now(UTC),
        ip=ip,
    )
    session.add(row)
    await session.flush()
    return row, True


# --- data requests --------------------------------------------------------------------------


async def create_data_request(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    kind: DataRequestKind | str,
    details: dict[str, Any] | None = None,
    sla_days: int,
    now: datetime | None = None,
) -> DataRequest:
    moment = now or datetime.now(UTC)
    row = DataRequest(
        tenant_id=tenant_id,
        user_id=user_id,
        kind=DataRequestKind(kind).value,
        status=DataRequestStatus.RECEIVED.value,
        details=details or {},
        sla_due_at=sla_due_at(moment, sla_days),
    )
    session.add(row)
    await session.flush()
    return row


async def complete_request(
    session: AsyncSession,
    request: DataRequest,
    *,
    details: dict[str, Any] | None = None,
    result_file_id: uuid.UUID | None = None,
    status: DataRequestStatus = DataRequestStatus.DONE,
    now: datetime | None = None,
) -> DataRequest:
    request.status = status.value
    request.completed_at = now or datetime.now(UTC)
    if details:
        request.details = {**(request.details or {}), **details}
    if result_file_id is not None:
        request.result_file_id = result_file_id
    await session.flush()
    return request


# --- access request: the caller's own personal data -------------------------------------------

# Tables that hold rows *about one user*, with the column that points at them. Everything
# else in the tenant belongs to the tenant, not to the data principal, and is covered by
# the tenant export instead.
USER_TABLES: tuple[tuple[str, str], ...] = (
    ("memberships", "user_id"),
    ("consents", "user_id"),
    ("data_requests", "user_id"),
    ("user_notification_prefs", "user_id"),
    ("files", "uploaded_by"),
    ("audit_log", "user_id"),
)


async def export_user_data(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any]:
    """Everything we hold about one data principal inside one tenant (DPDP access right)."""
    user = await session.get(User, user_id)
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "tenant_id": str(tenant_id),
        "user": None
        if user is None
        else {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "tz": user.tz,
            "locale": user.locale,
            "created_at": user.created_at.isoformat(),
        },
    }
    tables = {table.name: table for table in Base.metadata.sorted_tables}
    for name, column in USER_TABLES:
        table = tables.get(name)
        if table is None or column not in table.c:  # pragma: no cover - guards a rename
            continue
        payload[name] = await _rows(session, table, **{column: user_id})
    return payload


# --- tenant export ----------------------------------------------------------------------------


def build_export_zip(
    tables: dict[str, list[dict[str, Any]]],
    files: Sequence[tuple[str, bytes]],
    manifest: dict[str, Any],
) -> bytes:
    """One deterministic zip: manifest.json, tables/<name>.json, files/<file id>-<name>."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        for name in sorted(tables):
            archive.writestr(
                f"tables/{name}.json", json.dumps(tables[name], indent=2, sort_keys=True)
            )
        for name, data in files:
            archive.writestr(f"files/{name}", data)
    return buffer.getvalue()


async def export_tenant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request: DataRequest,
    storage_router: StorageRouter,
    include_files: bool = True,
) -> dict[str, Any]:
    """Zip every tenant-scoped table plus the uploaded files into the tenant's bucket.

    The zip is registered as a `files` row so the owner downloads it through the normal
    signed-URL route, and the request keeps both the file id and a ready signed URL.
    """
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError(f"unknown tenant {tenant_id}")
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in tenant_tables():
        tables[table.name] = await _rows(session, table, tenant_id=tenant_id)
    storage = storage_router.for_region(tenant.data_residency)
    blobs: list[tuple[str, bytes]] = []
    missing: list[str] = []
    if include_files:
        for row in (
            await session.execute(select(File).where(File.tenant_id == tenant_id))
        ).scalars():
            try:
                blobs.append((f"{row.id}-{row.filename}", await storage.get(row.key)))
            except ObjectNotFoundError:
                missing.append(str(row.id))
    manifest = {
        "tenant_id": str(tenant_id),
        "tenant_slug": tenant.slug,
        "region": tenant.region.value,
        "data_residency": tenant.data_residency.value,
        "generated_at": datetime.now(UTC).isoformat(),
        "request_id": str(request.id),
        "row_counts": {name: len(rows) for name, rows in tables.items()},
        "file_count": len(blobs),
        "files_missing_from_storage": missing,
    }
    archive = build_export_zip(tables, blobs, manifest)
    key = tenant_export_key(tenant_id, request.id)
    stored = await storage.put(key, archive, EXPORT_CONTENT_TYPE)
    export_file = File(
        tenant_id=tenant_id,
        filename=f"bidradar-export-{tenant.slug}-{request.id}.zip",
        extension="zip",
        kind="zip",
        content_type=EXPORT_CONTENT_TYPE,
        size_bytes=len(archive),
        sha256=stored.sha256,
        region=tenant.data_residency,
        bucket=stored.bucket,
        key=key,
        scan_status="skipped",
        scanner="none",
        uploaded_by=request.user_id,
    )
    session.add(export_file)
    await session.flush()
    url = await storage.signed_url(key)
    details = {
        "row_counts": manifest["row_counts"],
        "file_count": len(blobs),
        "size_bytes": len(archive),
        "key": key,
        "url": url,
    }
    await complete_request(session, request, details=details, result_file_id=export_file.id)
    await audit(
        session,
        EXPORT_ACTION,
        ("tenant", str(tenant_id)),
        tenant_id=tenant_id,
        user_id=request.user_id,
        meta={"request_id": str(request.id), "file_id": str(export_file.id)},
    )
    log.info("privacy.tenant_export", tenant_id=str(tenant_id), size_bytes=len(archive))
    return {"request_id": str(request.id), "file_id": str(export_file.id), **details}


# --- tenant erasure -----------------------------------------------------------------------------


async def delete_tenant(
    owner_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    storage_router: StorageRouter,
    requested_by: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Erase everything the tenant owns: objects first, then rows, children first.

    Runs on the OWNER session: RLS policies are FORCE'd, and deleting the last rows of a
    tenant (including the caller's own membership) must not depend on a live tenant
    context. audit_log rows and the tenants row survive; the tenants row is stamped
    deleted_at so the tenant can never be used again (SPEC 11).
    """
    tenant = await owner_session.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError(f"unknown tenant {tenant_id}")
    storage = storage_router.for_region(tenant.data_residency)
    objects = 0
    for row in (
        await owner_session.execute(select(File).where(File.tenant_id == tenant_id))
    ).scalars():
        try:
            await storage.delete(row.key)
            objects += 1
        except ObjectNotFoundError:  # pragma: no cover - already gone is fine
            log.warning("privacy.object_missing", key=row.key)
    # The audit row is written BEFORE the deletes so it shares the transaction and is not
    # itself removed by them (audit_log is retained, but the row must exist to be kept).
    await audit(
        owner_session,
        DELETE_ACTION,
        ("tenant", str(tenant_id)),
        tenant_id=tenant_id,
        user_id=requested_by,
        meta={"request_id": None if request_id is None else str(request_id)},
    )
    user_ids = list(
        (
            await owner_session.execute(
                select(Membership.user_id).where(Membership.tenant_id == tenant_id)
            )
        ).scalars()
    )
    deleted: dict[str, int] = {}
    for table in erasable_tables():
        result = await owner_session.execute(delete(table).where(table.c.tenant_id == tenant_id))
        deleted[table.name] = int(getattr(result, "rowcount", 0) or 0)
    # Users are global rows shared between tenants: remove only those left with no
    # membership anywhere (their personal data has no other lawful home).
    orphans = 0
    for user_id in user_ids:
        remaining = (
            await owner_session.execute(
                select(func.count()).select_from(Membership).where(Membership.user_id == user_id)
            )
        ).scalar_one()
        if remaining == 0:
            await owner_session.execute(delete(User).where(User.id == user_id))
            orphans += 1
    tenant.deleted_at = now or datetime.now(UTC)
    await owner_session.flush()
    log.info(
        "privacy.tenant_delete",
        tenant_id=str(tenant_id),
        objects=objects,
        rows=sum(deleted.values()),
        users=orphans,
    )
    return {
        "tenant_id": str(tenant_id),
        "objects_deleted": objects,
        "rows_deleted": deleted,
        "users_deleted": orphans,
        "deleted_at": tenant.deleted_at.isoformat(),
    }
