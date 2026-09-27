"""Consent, data-principal requests and tenant export/delete (SPEC 11, 10.4 settings).

    POST/GET /api/v1/me/consents        record and list the caller's own acceptances
    POST/GET /api/v1/me/data-requests   access | correction | erasure, with an SLA date
    POST     /api/v1/tenant/export      tenant_owner; background zip of everything
    POST     /api/v1/tenant/delete      tenant_owner; background erasure, audit kept
    GET      /api/v1/privacy            public: grievance officer, sub-processors, versions

An `access` request answers immediately with the caller's own rows (nothing to schedule);
correction and erasure of a single principal inside a live tenant are handled by a human
on the SLA clock, so they stay `received` until an operator works them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import (
    TENANT_ROLES,
    CurrentUser,
    SettingsDep,
    StorageRouterDep,
    TenantSessionDep,
    client_ip,
    require_role,
)
from app.core.privacy import (
    SELF_SERVICE_KINDS,
    ConsentKind,
    DataRequestKind,
    DataRequestStatus,
    is_overdue,
    sub_processors,
)
from app.core.roles import Role
from app.jobs.privacy import TENANT_DELETE_TASK, TENANT_EXPORT_TASK, schedule_tenant_job
from app.models import Consent, DataRequest, Tenant
from app.services.audit import AuditHint
from app.services.privacy import (
    CONSENT_ACTION,
    REQUEST_ACTION,
    create_data_request,
    export_user_data,
    record_consent,
)
from app.services.users import ensure_user_membership

me_router = APIRouter(prefix="/me", tags=["privacy"])
tenant_router = APIRouter(prefix="/tenant", tags=["privacy"])
public_router = APIRouter(tags=["privacy"])

AnyMemberDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]
OwnerDep = Annotated[CurrentUser, Depends(require_role(Role.TENANT_OWNER))]


class ConsentIn(BaseModel):
    kind: ConsentKind
    version: str = Field(max_length=32)


class ConsentOut(BaseModel):
    id: uuid.UUID
    kind: ConsentKind
    version: str
    accepted_at: datetime
    created: bool = True


class DataRequestIn(BaseModel):
    kind: DataRequestKind
    note: str | None = Field(default=None, max_length=2000)


class DataRequestOut(BaseModel):
    id: uuid.UUID
    kind: DataRequestKind
    status: DataRequestStatus
    created_at: datetime
    sla_due_at: datetime
    completed_at: datetime | None
    overdue: bool
    details: dict[str, Any]
    result_file_id: uuid.UUID | None
    # only set on an access request, which is answered in the same response
    data: dict[str, Any] | None = None


class TenantJobOut(BaseModel):
    request_id: uuid.UUID
    kind: DataRequestKind
    status: DataRequestStatus
    sla_due_at: datetime
    scheduling: str  # inline | queued
    details: dict[str, Any]
    result_file_id: uuid.UUID | None


class GrievanceOfficer(BaseModel):
    name: str | None
    email: str | None


class PrivacyOut(BaseModel):
    dpdp_notice_version: str
    privacy_policy_version: str
    terms_version: str
    data_request_sla_days: int
    grievance_officer: GrievanceOfficer
    sub_processors: list[dict[str, str]]


async def _member(user: CurrentUser) -> uuid.UUID:
    """Consents and requests reference users.id, so provision the caller as /me does."""
    provisioned = await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    if provisioned is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="unknown tenant")
    return provisioned.user_id


def _request_out(
    row: DataRequest, now: datetime, data: dict[str, Any] | None = None
) -> DataRequestOut:
    return DataRequestOut(
        id=row.id,
        kind=DataRequestKind(row.kind),
        status=DataRequestStatus(row.status),
        created_at=row.created_at,
        sla_due_at=row.sla_due_at,
        completed_at=row.completed_at,
        overdue=is_overdue(row.sla_due_at, now, row.status),
        details=row.details or {},
        result_file_id=row.result_file_id,
        data=data,
    )


# --- consent ---------------------------------------------------------------------------------


@me_router.post("/consents", response_model=ConsentOut, status_code=status.HTTP_201_CREATED)
async def accept_consent(
    body: ConsentIn,
    user: AnyMemberDep,
    session: TenantSessionDep,
    settings: SettingsDep,
    request: Request,
) -> ConsentOut:
    """Record that this user accepted a notice version (signup wizard and settings)."""
    user_id = await _member(user)
    row, created = await record_consent(
        session,
        tenant_id=user.tenant_id,
        user_id=user_id,
        kind=body.kind,
        version=body.version,
        ip=client_ip(request, settings),
    )
    request.state.audit = AuditHint(
        action=CONSENT_ACTION,
        object_type="consent",
        object_id=str(row.id),
        meta={"kind": body.kind.value, "version": body.version, "created": created},
    )
    return ConsentOut(
        id=row.id,
        kind=ConsentKind(row.kind),
        version=row.version,
        accepted_at=row.accepted_at,
        created=created,
    )


@me_router.get("/consents", response_model=list[ConsentOut])
async def list_consents(user: AnyMemberDep, session: TenantSessionDep) -> list[ConsentOut]:
    """The caller's own acceptances, newest first."""
    user_id = await _member(user)
    rows = (
        await session.execute(
            select(Consent)
            .where(Consent.user_id == user_id)
            .order_by(Consent.accepted_at.desc(), Consent.id)
        )
    ).scalars()
    return [
        ConsentOut(
            id=row.id,
            kind=ConsentKind(row.kind),
            version=row.version,
            accepted_at=row.accepted_at,
        )
        for row in rows
    ]


# --- data-principal requests --------------------------------------------------------------


@me_router.post(
    "/data-requests", response_model=DataRequestOut, status_code=status.HTTP_201_CREATED
)
async def open_data_request(
    body: DataRequestIn,
    user: AnyMemberDep,
    session: TenantSessionDep,
    settings: SettingsDep,
    request: Request,
) -> DataRequestOut:
    """Raise an access, correction or erasure request about the caller's own data."""
    if body.kind not in SELF_SERVICE_KINDS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "unsupported_kind",
                "allowed": [k.value for k in SELF_SERVICE_KINDS],
                "hint": "tenant-wide export and delete are POST /api/v1/tenant/{export,delete}",
            },
        )
    user_id = await _member(user)
    now = datetime.now(UTC)
    row = await create_data_request(
        session,
        tenant_id=user.tenant_id,
        user_id=user_id,
        kind=body.kind,
        details={"note": body.note} if body.note else {},
        sla_days=settings.data_request_sla_days,
        now=now,
    )
    data: dict[str, Any] | None = None
    if body.kind is DataRequestKind.ACCESS:
        data = await export_user_data(session, tenant_id=user.tenant_id, user_id=user_id)
        row.status = DataRequestStatus.DONE.value
        row.completed_at = now
        row.details = {**(row.details or {}), "answered": "inline"}
        await session.flush()
    request.state.audit = AuditHint(
        action=REQUEST_ACTION,
        object_type="data_request",
        object_id=str(row.id),
        meta={"kind": body.kind.value, "status": row.status},
    )
    return _request_out(row, now, data)


@me_router.get("/data-requests", response_model=list[DataRequestOut])
async def list_data_requests(user: AnyMemberDep, session: TenantSessionDep) -> list[DataRequestOut]:
    """The caller's own requests with their SLA state (owners also see tenant jobs)."""
    user_id = await _member(user)
    rows = (
        await session.execute(
            select(DataRequest)
            .where(DataRequest.user_id == user_id)
            .order_by(DataRequest.created_at.desc(), DataRequest.id)
        )
    ).scalars()
    now = datetime.now(UTC)
    return [_request_out(row, now) for row in rows]


# --- tenant-wide export and delete -----------------------------------------------------------


async def _tenant_job(
    task_name: str,
    kind: DataRequestKind,
    action_object: str,
    user: CurrentUser,
    session: TenantSessionDep,
    settings: SettingsDep,
    storage_router: StorageRouterDep,
    request: Request,
) -> TenantJobOut:
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="unknown tenant")
    if tenant.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "tenant_deleted"})
    user_id = await _member(user)
    now = datetime.now(UTC)
    row = await create_data_request(
        session,
        tenant_id=user.tenant_id,
        user_id=user_id,
        kind=kind,
        sla_days=settings.data_request_sla_days,
        now=now,
    )
    request_id = row.id
    sla = row.sla_due_at
    request.state.audit = AuditHint(
        action=REQUEST_ACTION,
        object_type="data_request",
        object_id=str(request_id),
        meta={"kind": kind.value, "object": action_object},
    )
    # The job opens its own session, so the request row must be visible to it first.
    await session.commit()
    scheduling = await schedule_tenant_job(
        task_name, user.tenant_id, request_id, settings=settings, storage_router=storage_router
    )
    # The job ran in its own session and transaction; forget whatever this one still holds
    # (a delete removed its own request row along with everything else).
    session.expunge_all()
    refreshed = (
        await session.execute(select(DataRequest).where(DataRequest.id == request_id))
    ).scalar_one_or_none()
    if refreshed is None:
        return TenantJobOut(
            request_id=request_id,
            kind=kind,
            status=DataRequestStatus.DONE,
            sla_due_at=sla,
            scheduling=scheduling,
            details={"erased": True},
            result_file_id=None,
        )
    return TenantJobOut(
        request_id=refreshed.id,
        kind=kind,
        status=DataRequestStatus(refreshed.status),
        sla_due_at=refreshed.sla_due_at,
        scheduling=scheduling,
        details=refreshed.details or {},
        result_file_id=refreshed.result_file_id,
    )


@tenant_router.post("/export", response_model=TenantJobOut, status_code=status.HTTP_202_ACCEPTED)
async def export_tenant_data(
    user: OwnerDep,
    session: TenantSessionDep,
    settings: SettingsDep,
    storage_router: StorageRouterDep,
    request: Request,
) -> TenantJobOut:
    """Start a full export of the tenant: a zip of every table plus the uploaded files."""
    return await _tenant_job(
        TENANT_EXPORT_TASK,
        DataRequestKind.TENANT_EXPORT,
        "export",
        user,
        session,
        settings,
        storage_router,
        request,
    )


@tenant_router.post("/delete", response_model=TenantJobOut, status_code=status.HTTP_202_ACCEPTED)
async def delete_tenant_data(
    user: OwnerDep,
    session: TenantSessionDep,
    settings: SettingsDep,
    storage_router: StorageRouterDep,
    request: Request,
) -> TenantJobOut:
    """Erase every row and file of the tenant. The audit trail and the tenants row stay."""
    return await _tenant_job(
        TENANT_DELETE_TASK,
        DataRequestKind.TENANT_DELETE,
        "delete",
        user,
        session,
        settings,
        storage_router,
        request,
    )


# --- public notice ------------------------------------------------------------------------------


@public_router.get("/privacy", response_model=PrivacyOut)
async def read_privacy(settings: SettingsDep) -> PrivacyOut:
    """Public privacy metadata: notice versions, grievance officer and sub-processors."""
    return PrivacyOut(
        dpdp_notice_version=settings.dpdp_notice_version,
        privacy_policy_version=settings.privacy_policy_version,
        terms_version=settings.terms_version,
        data_request_sla_days=settings.data_request_sla_days,
        grievance_officer=GrievanceOfficer(
            name=settings.grievance_officer_name or None,
            email=settings.grievance_officer_email or None,
        ),
        sub_processors=sub_processors(),
    )
