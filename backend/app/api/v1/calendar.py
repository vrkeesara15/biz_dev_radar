"""Calendar feed and per-user calendar connections (SPEC 9, 10.3; M6-04).

    GET    /api/v1/calendar.ics?token=            the user's key dates as a VCALENDAR
    GET    /api/v1/me/calendar                    the feed link (if issued) + connections
    POST   /api/v1/me/calendar-token              issue or rotate the link
    DELETE /api/v1/me/calendar-connections/{id}   disconnect a Google / Outlook calendar

The feed is unauthenticated by design: a calendar client cannot hold a session, so the
URL carries an HS256 token naming the tenant, the user and a nonce that must still match
`user_notification_prefs.calendar_token`. Rotating writes a new nonce, which revokes
every link handed out before. The route reads nothing from the token before the
signature verifies, and answers 401 for a bad, foreign or rotated one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel

from app.api.deps import TENANT_ROLES, CurrentUser, SettingsDep, TenantSessionDep, require_role
from app.core.db import get_database
from app.models import CalendarConnection, User
from app.models.notify import UserNotificationPrefs
from app.services import calendar as calendar_svc
from app.services.audit import AuditHint

router = APIRouter(tags=["calendar"])
me_router = APIRouter(prefix="/me", tags=["calendar"])

ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]

ICS_MEDIA_TYPE = "text/calendar; charset=utf-8"


class CalendarConnectionOut(BaseModel):
    id: uuid.UUID
    provider: str
    calendar_id: str
    enabled: bool
    last_synced_at: datetime | None
    last_error: str | None
    # secret_ref is deliberately absent: a credential reference never leaves the server


class MeCalendarOut(BaseModel):
    feed_url: str | None
    connections: list[CalendarConnectionOut]


def connection_out(row: CalendarConnection) -> CalendarConnectionOut:
    return CalendarConnectionOut(
        id=row.id,
        provider=row.provider,
        calendar_id=row.calendar_id,
        enabled=row.enabled,
        last_synced_at=row.last_synced_at,
        last_error=row.last_error,
    )


async def _prefs(session: TenantSessionDep, user: CurrentUser) -> UserNotificationPrefs:
    """The caller's prefs row, created on first use (same rule as /me/notification-prefs)."""
    row = await calendar_svc.prefs_for(session, user.id)
    if row is None:
        row = UserNotificationPrefs(tenant_id=user.tenant_id, user_id=user.id)
        session.add(row)
        await session.flush()
    return row


@router.get("/calendar.ics", response_class=Response)
async def calendar_feed(
    settings: SettingsDep,
    token: Annotated[str, Query(min_length=1, max_length=4096)],
) -> Response:
    """The signed-link iCal feed. Public route: the token IS the credential."""
    try:
        claims = calendar_svc.verify_calendar_token(token, settings.auth_secret)
    except calendar_svc.CalendarTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=exc.detail) from exc
    async with get_database().session(claims.tenant_id) as session:
        prefs = await calendar_svc.prefs_for(session, claims.user_id)
        if prefs is None or not prefs.calendar_token or prefs.calendar_token != claims.nonce:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail=calendar_svc.CalendarTokenError.detail
            )
        reader = await session.get(User, claims.user_id)
        body = await calendar_svc.render_feed(
            session,
            settings,
            user_id=claims.user_id,
            user_tz=prefs.tz or (None if reader is None else reader.tz),
        )
    return Response(
        content=body,
        media_type=ICS_MEDIA_TYPE,
        headers={
            "Content-Disposition": 'inline; filename="bidradar.ics"',
            "Cache-Control": "private, max-age=300",
        },
    )


@me_router.get("/calendar", response_model=MeCalendarOut)
async def my_calendar(
    session: TenantSessionDep, user: ReaderDep, settings: SettingsDep
) -> MeCalendarOut:
    """The caller's feed link (null until issued) and their connected calendars."""
    prefs = await calendar_svc.prefs_for(session, user.id)
    nonce = None if prefs is None else prefs.calendar_token
    connections = await calendar_svc.connections_for(session, user.id)
    return MeCalendarOut(
        feed_url=(
            None
            if not nonce
            else calendar_svc.calendar_feed_url(
                settings, tenant_id=user.tenant_id, user_id=user.id, nonce=nonce
            )
        ),
        connections=[connection_out(row) for row in connections],
    )


@me_router.post("/calendar-token", response_model=MeCalendarOut)
async def rotate_calendar_token(
    session: TenantSessionDep, user: ReaderDep, settings: SettingsDep, request: Request
) -> MeCalendarOut:
    """Issue a feed link, or replace it — every link handed out before stops working."""
    prefs = await _prefs(session, user)
    prefs.calendar_token = calendar_svc.new_nonce()
    await session.flush()
    request.state.audit = AuditHint(
        action="calendar.token_rotated", object_type="user", object_id=str(user.id)
    )
    connections = await calendar_svc.connections_for(session, user.id)
    return MeCalendarOut(
        feed_url=calendar_svc.calendar_feed_url(
            settings, tenant_id=user.tenant_id, user_id=user.id, nonce=prefs.calendar_token
        ),
        connections=[connection_out(row) for row in connections],
    )


@me_router.delete("/calendar-connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_calendar(
    connection_id: uuid.UUID, session: TenantSessionDep, user: ReaderDep, request: Request
) -> None:
    """Disconnect one calendar. The events already pushed stay in the user's calendar."""
    row = await session.get(CalendarConnection, connection_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="calendar connection not found")
    request.state.audit = AuditHint(
        action="calendar.disconnected",
        object_type="calendar_connection",
        object_id=str(row.id),
        meta={"provider": row.provider},
    )
    await session.delete(row)
    await session.flush()
