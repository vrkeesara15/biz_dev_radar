"""GET/PUT /api/v1/me/notification-prefs (SPEC 4.6, 10.3): per user, per tenant."""

from __future__ import annotations

import uuid
from typing import Annotated, Any
from zoneinfo import ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from app.api.deps import CurrentUserDep, TenantSessionDep
from app.core.preferences import (
    DEFAULT_CHANNELS_BY_EVENT,
    DEFAULT_DIGEST_TIME,
    DEFAULT_MIN_SCORE_DIGEST,
    DEFAULT_MIN_SCORE_INSTANT,
    validate_channels_by_event,
    validate_hhmm,
    validate_min_scores,
    validate_quiet_hours,
)
from app.core.timezones import validate_timezone
from app.models import User, UserNotificationPrefs
from app.services.audit import AuditHint
from app.services.users import ensure_user_membership

router = APIRouter(prefix="/me/notification-prefs", tags=["me"])


class NotificationPrefsOut(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    channels_by_event: dict[str, list[str]]
    quiet_hours_start: str | None
    quiet_hours_end: str | None
    tz: str
    digest_time: str
    min_score_instant: int
    min_score_digest: int


class NotificationPrefsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels_by_event: dict[str, list[str]] | None = None
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    tz: Annotated[str | None, Field(max_length=64)] = None
    digest_time: str | None = None
    min_score_instant: Annotated[int | None, Field(ge=0, le=100)] = None
    min_score_digest: Annotated[int | None, Field(ge=0, le=100)] = None

    @field_validator("channels_by_event")
    @classmethod
    def _channels(cls, value: dict[str, Any] | None) -> dict[str, list[str]] | None:
        return None if value is None else validate_channels_by_event(value)

    @field_validator("tz")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return validate_timezone(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc

    @field_validator("digest_time")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        return None if value is None else validate_hhmm(value, "digest_time")

    @model_validator(mode="after")
    def _quiet(self) -> NotificationPrefsIn:
        sent = self.model_fields_set
        if "quiet_hours_start" in sent or "quiet_hours_end" in sent:
            validate_quiet_hours(self.quiet_hours_start, self.quiet_hours_end)
        return self


def _out(row: UserNotificationPrefs) -> NotificationPrefsOut:
    channels = {**DEFAULT_CHANNELS_BY_EVENT, **row.channels_by_event}
    return NotificationPrefsOut(
        user_id=row.user_id,
        tenant_id=row.tenant_id,
        channels_by_event=channels,
        quiet_hours_start=row.quiet_hours_start,
        quiet_hours_end=row.quiet_hours_end,
        tz=row.tz,
        digest_time=row.digest_time,
        min_score_instant=row.min_score_instant,
        min_score_digest=row.min_score_digest,
    )


async def _get_or_create(session: TenantSessionDep, user: CurrentUserDep) -> UserNotificationPrefs:
    provisioned = await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    if provisioned is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="unknown tenant")
    row = (
        await session.execute(
            select(UserNotificationPrefs).where(
                UserNotificationPrefs.user_id == provisioned.user_id,
                UserNotificationPrefs.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        user_row = await session.get(User, provisioned.user_id)
        row = UserNotificationPrefs(
            tenant_id=user.tenant_id,
            user_id=provisioned.user_id,
            channels_by_event={},
            tz=user_row.tz if user_row else "UTC",
            digest_time=DEFAULT_DIGEST_TIME,
            min_score_instant=DEFAULT_MIN_SCORE_INSTANT,
            min_score_digest=DEFAULT_MIN_SCORE_DIGEST,
        )
        session.add(row)
        await session.flush()
    return row


@router.get("", response_model=NotificationPrefsOut)
async def read_prefs(user: CurrentUserDep, session: TenantSessionDep) -> NotificationPrefsOut:
    """The caller's preferences in this tenant; defaults are materialized on first read."""
    return _out(await _get_or_create(session, user))


@router.put("", response_model=NotificationPrefsOut)
async def update_prefs(
    body: NotificationPrefsIn, user: CurrentUserDep, session: TenantSessionDep, request: Request
) -> NotificationPrefsOut:
    row = await _get_or_create(session, user)
    changes = body.model_dump(exclude_unset=True)
    instant = changes.get("min_score_instant", row.min_score_instant)
    digest = changes.get("min_score_digest", row.min_score_digest)
    if instant is None or digest is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="min scores cannot be null"
        )
    try:
        validate_min_scores(instant, digest)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    for name in ("channels_by_event", "tz", "digest_time"):
        if changes.get(name, "x") is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{name} cannot be null"
            )
    for name, value in changes.items():
        setattr(row, name, value)
    await session.flush()
    request.state.audit = AuditHint(
        action="notification_prefs.update",
        object_type="user_notification_prefs",
        object_id=str(row.id),
        meta={"fields": sorted(changes)},
    )
    return _out(row)
