"""Key dates on a pursuit (SPEC 9, 10.3; M6-02).

    GET    /api/v1/pursuits/{id}/dates
    POST   /api/v1/pursuits/{id}/dates            {kind, at, label?, note?}
    PUT    /api/v1/pursuits/{id}/dates/{date_id}  {at?, label?, note?}
    DELETE /api/v1/pursuits/{id}/dates/{date_id}
    POST   /api/v1/pursuits/{id}/dates/{date_id}/acknowledge

Writing a date makes it `source = 'user'`, so the amendment recalculation never moves it
again. Every date crosses the wire as a TzDateOut (UTC, the buyer's zone and the reader's)
so the calendar view renders "Oct 14, 2:00 PM EDT = 11:30 PM IST" without doing the maths.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TENANT_ROLES, CurrentUser, TenantSessionDep, require_role
from app.core.display_time import TzDateOut, tz_fields
from app.core.key_dates import KINDS, SOURCE_USER, label_for
from app.core.roles import Role
from app.models import Opportunity, PursuitDate, User
from app.services import key_dates as key_date_svc
from app.services import pursuits as pursuit_svc
from app.services.audit import AuditHint

router = APIRouter(prefix="/pursuits", tags=["pursuits"])

# A writer owns the schedule of the package they are writing; a reviewer / viewer reads.
WRITER_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER)
WriterDep = Annotated[CurrentUser, Depends(require_role(*WRITER_ROLES))]
ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]
# Acknowledging is "I have seen this": anybody who can be assigned may do it.
AckDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]


class KeyDateIn(BaseModel):
    kind: str = "custom"
    at: datetime
    label: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in KINDS:
            raise ValueError(f"unknown kind {value!r}; one of {', '.join(KINDS)}")
        return value

    @field_validator("at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("at must carry a time zone offset")
        return value.astimezone(UTC)


class KeyDatePatchIn(BaseModel):
    at: datetime | None = None
    label: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("at must carry a time zone offset")
        return value.astimezone(UTC)


class KeyDateOut(BaseModel):
    id: uuid.UUID
    pursuit_id: uuid.UUID
    kind: str
    label: str
    note: str | None
    source: str
    at: TzDateOut
    acknowledged_by: uuid.UUID | None
    acknowledged_at: datetime | None
    created_at: datetime
    updated_at: datetime


class KeyDateListOut(BaseModel):
    pursuit_id: uuid.UUID
    items: list[KeyDateOut]


def date_out(row: PursuitDate, user_tz: str | None) -> KeyDateOut:
    return KeyDateOut(
        id=row.id,
        pursuit_id=row.pursuit_id,
        kind=row.kind,
        label=row.label,
        note=row.note,
        source=row.source,
        at=tz_fields(row.at, row.buyer_tz, user_tz, with_year=True),
        acknowledged_by=row.acknowledged_by,
        acknowledged_at=row.acknowledged_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _reader_tz(session: AsyncSession, user: CurrentUser) -> str | None:
    row = await session.get(User, user.id)
    return None if row is None else row.tz


async def _load(session: AsyncSession, pursuit_id: uuid.UUID, date_id: uuid.UUID) -> PursuitDate:
    await pursuit_svc.get_pursuit(session, pursuit_id)
    row = await session.get(PursuitDate, date_id)
    if row is None or row.pursuit_id != pursuit_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key date not found")
    return row


@router.get("/{pursuit_id}/dates", response_model=KeyDateListOut)
async def list_dates(
    pursuit_id: uuid.UUID, session: TenantSessionDep, user: ReaderDep
) -> KeyDateListOut:
    """Every key date on the pursuit, soonest first, in both time zones."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    tz = await _reader_tz(session, user)
    rows = await key_date_svc.list_dates(session, pursuit.id)
    return KeyDateListOut(pursuit_id=pursuit.id, items=[date_out(row, tz) for row in rows])


@router.post("/{pursuit_id}/dates", response_model=KeyDateOut, status_code=status.HTTP_201_CREATED)
async def create_date(
    pursuit_id: uuid.UUID,
    body: KeyDateIn,
    session: TenantSessionDep,
    user: WriterDep,
    request: Request,
) -> KeyDateOut:
    """Add a date. A second auto kind on the same pursuit is a 409 (edit the first)."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    existing = {row.kind: row for row in await key_date_svc.list_dates(session, pursuit.id)}
    if body.kind != "custom" and body.kind in existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "duplicate_kind",
                "kind": body.kind,
                "id": str(existing[body.kind].id),
            },
        )
    # the buyer's zone travels with the row so the display string never needs a join
    opportunity = await session.get(Opportunity, pursuit.opportunity_id)
    row = PursuitDate(
        tenant_id=user.tenant_id,
        pursuit_id=pursuit.id,
        kind=body.kind,
        at=body.at,
        buyer_tz="UTC" if opportunity is None else opportunity.source_tz,
        source=SOURCE_USER,
        label=body.label or label_for(body.kind),
        note=body.note,
    )
    session.add(row)
    await session.flush()
    request.state.audit = AuditHint(
        action="pursuit.date_created",
        object_type="pursuit_date",
        object_id=str(row.id),
        meta={"pursuit_id": str(pursuit.id), "kind": row.kind},
    )
    return date_out(row, await _reader_tz(session, user))


@router.put("/{pursuit_id}/dates/{date_id}", response_model=KeyDateOut)
async def update_date(
    pursuit_id: uuid.UUID,
    date_id: uuid.UUID,
    body: KeyDatePatchIn,
    session: TenantSessionDep,
    user: WriterDep,
    request: Request,
) -> KeyDateOut:
    """Edit a date. Editing pins it: `source` becomes `user`, so an amendment that moves
    the buyer's deadline will shift the other auto rows but never this one."""
    row = await _load(session, pursuit_id, date_id)
    meta: dict[str, object] = {"pursuit_id": str(pursuit_id), "kind": row.kind}
    if body.at is not None and body.at != row.at:
        meta["at"] = {"from": row.at.isoformat(), "to": body.at.isoformat()}
        row.at = body.at
        row.acknowledged_at = None
        row.acknowledged_by = None
    if body.label is not None:
        row.label = body.label
    if body.note is not None:
        row.note = body.note
    row.source = SOURCE_USER
    await session.flush()
    await session.refresh(row)
    request.state.audit = AuditHint(
        action="pursuit.date_updated",
        object_type="pursuit_date",
        object_id=str(row.id),
        meta=meta,
    )
    return date_out(row, await _reader_tz(session, user))


@router.delete("/{pursuit_id}/dates/{date_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_date(
    pursuit_id: uuid.UUID,
    date_id: uuid.UUID,
    session: TenantSessionDep,
    user: WriterDep,
    request: Request,
) -> None:
    row = await _load(session, pursuit_id, date_id)
    request.state.audit = AuditHint(
        action="pursuit.date_deleted",
        object_type="pursuit_date",
        object_id=str(row.id),
        meta={"pursuit_id": str(pursuit_id), "kind": row.kind},
    )
    await session.delete(row)
    await session.flush()


@router.post("/{pursuit_id}/dates/{date_id}/acknowledge", response_model=KeyDateOut)
async def acknowledge_date(
    pursuit_id: uuid.UUID,
    date_id: uuid.UUID,
    session: TenantSessionDep,
    user: AckDep,
    request: Request,
) -> KeyDateOut:
    """ "Seen it." Stops the reminder escalation for this date (SPEC 9)."""
    row = await _load(session, pursuit_id, date_id)
    row.acknowledged_by = user.id
    row.acknowledged_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(row)
    request.state.audit = AuditHint(
        action="pursuit.date_acknowledged",
        object_type="pursuit_date",
        object_id=str(row.id),
        meta={"pursuit_id": str(pursuit_id), "kind": row.kind},
    )
    return date_out(row, await _reader_tz(session, user))
