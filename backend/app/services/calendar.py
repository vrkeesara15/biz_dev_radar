"""Calendar feed and provider push for a user's key dates (SPEC 7, 9, 10.3; M6-04).

    url = calendar_feed_url(settings, tenant_id=..., user_id=..., nonce=...)
    claims = verify_calendar_token(token, settings.auth_secret)
    ics = await render_feed(session, settings, user_id=..., user_tz=...)

    provider = provider_for("google")               # CalendarProvider
    await sync_date(session, settings, date, action="upsert")

The feed is an UNAUTHENTICATED URL a calendar client polls, so the token is an HS256
bearer in the query string, not a session: it names the tenant, the user and a `nonce`
that must still match `user_notification_prefs.calendar_token`. Rotating the token writes
a new nonce, which is what "revoke my calendar link" means. There is no expiry — a feed
URL that dies in two weeks is worse than useless.

Push (Google Calendar / Microsoft Graph) goes through the `CalendarProvider` protocol so
tests mock HTTP and nothing vendor-specific leaks into the services. A user's connection
lives in `calendar_connections` (per user, unlike the per-tenant `integrations` rows) and
the access token is resolved from its `secret_ref`.
"""

from __future__ import annotations

import secrets as secrets_module
import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
import jwt
import structlog
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import ALGORITHM, AuthError
from app.core.calendar import CalendarEntry, build_calendar, build_description, event_uid
from app.core.config import Settings
from app.models import (
    CalendarConnection,
    CalendarEvent,
    Opportunity,
    Pursuit,
    PursuitDate,
    PursuitTask,
    UserNotificationPrefs,
)
from app.models.calendar import PROVIDER_GOOGLE, PROVIDER_MICROSOFT
from app.services.secrets import SecretUnavailableError, resolve_secret

log = structlog.get_logger(__name__)

FEED_PATH = "/api/v1/calendar.ics"
TOKEN_KIND = "calendar_feed"
TOKEN_BYTES = 24

GOOGLE_API_URL = "https://www.googleapis.com/calendar/v3"
GRAPH_API_URL = "https://graph.microsoft.com/v1.0"
HTTP_TIMEOUT = 10.0


# --- the feed token -------------------------------------------------------------------------


class CalendarTokenError(AuthError):
    detail = "invalid or revoked calendar link"


class CalendarClaims(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    kind: str
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    nonce: str


def new_nonce() -> str:
    return secrets_module.token_urlsafe(TOKEN_BYTES)


def sign_calendar_token(
    settings: Settings, *, tenant_id: uuid.UUID, user_id: uuid.UUID, nonce: str
) -> str:
    payload: dict[str, Any] = {
        "kind": TOKEN_KIND,
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "nonce": nonce,
        "iat": int(datetime.now(UTC).timestamp()),
    }
    return jwt.encode(payload, settings.auth_secret, algorithm=ALGORITHM)


def verify_calendar_token(token: str, secret: str) -> CalendarClaims:
    """Signature + shape only; the nonce is checked against the stored one by the route."""
    if not token or not secret:
        raise CalendarTokenError()
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["kind", "tenant_id", "user_id", "nonce"], "verify_exp": False},
        )
    except jwt.InvalidTokenError as exc:
        raise CalendarTokenError() from exc
    if payload.get("kind") != TOKEN_KIND:
        raise CalendarTokenError()
    try:
        return CalendarClaims.model_validate(payload)
    except ValidationError as exc:
        raise CalendarTokenError() from exc


def calendar_feed_url(
    settings: Settings, *, tenant_id: uuid.UUID, user_id: uuid.UUID, nonce: str
) -> str:
    token = sign_calendar_token(settings, tenant_id=tenant_id, user_id=user_id, nonce=nonce)
    return f"{settings.api_base_url.rstrip('/')}{FEED_PATH}?token={token}"


async def prefs_for(session: AsyncSession, user_id: uuid.UUID) -> UserNotificationPrefs | None:
    return (
        await session.execute(
            select(UserNotificationPrefs).where(UserNotificationPrefs.user_id == user_id)
        )
    ).scalar_one_or_none()


# --- what belongs in one user's calendar -----------------------------------------------------


async def dates_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> Sequence[tuple[PursuitDate, Pursuit, Opportunity]]:
    """Key dates of pursuits the user owns or has an open task on (SPEC 9: assignees)."""
    assigned = (
        select(PursuitTask.pursuit_id)
        .where(PursuitTask.assignee_user_id == user_id)
        .scalar_subquery()
    )
    stmt = (
        select(PursuitDate, Pursuit, Opportunity)
        .join(Pursuit, Pursuit.id == PursuitDate.pursuit_id)
        .join(Opportunity, Opportunity.id == Pursuit.opportunity_id)
        .where(or_(Pursuit.owner_user_id == user_id, Pursuit.id.in_(assigned)))
        .order_by(PursuitDate.at)
    )
    return [(d, p, o) for d, p, o in (await session.execute(stmt)).all()]


def deep_link(settings: Settings, pursuit_id: uuid.UUID) -> str:
    return f"{settings.app_base_url.rstrip('/')}/app/pursuits/{pursuit_id}"


def entry_for(
    settings: Settings,
    date: PursuitDate,
    pursuit: Pursuit,
    opportunity: Opportunity,
    *,
    user_tz: str | None,
    sequence: int | None = None,
) -> CalendarEntry:
    return CalendarEntry(
        uid=event_uid(date.id),
        at=date.at,
        summary=f"{date.label} — {opportunity.title}",
        buyer_tz=date.buyer_tz,
        user_tz=user_tz,
        note=date.note,
        url=deep_link(settings, pursuit.id),
        sequence=sequence if sequence is not None else sequence_for(date),
        last_modified=date.updated_at,
    )


def sequence_for(date: PursuitDate) -> int:
    """RFC 5545 SEQUENCE: the row's own counter, bumped by whoever changed it."""
    return max(0, int(date.sequence))


async def render_feed(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    user_tz: str | None = None,
    name: str | None = None,
) -> bytes:
    rows = await dates_for_user(session, user_id)
    entries = [
        entry_for(settings, date, pursuit, opportunity, user_tz=user_tz)
        for date, pursuit, opportunity in rows
    ]
    return build_calendar(entries, name=name or "BidRadar deadlines")


# --- providers --------------------------------------------------------------------------------


class CalendarProvider(Protocol):
    """Push one key date into a user's own calendar. Implementations never raise for a
    provider error; they raise CalendarPushError so the caller can record it."""

    name: str

    async def create_event(self, connection: CalendarConnection, entry: CalendarEntry) -> str: ...

    async def update_event(
        self, connection: CalendarConnection, event_id: str, entry: CalendarEntry
    ) -> None: ...

    async def delete_event(self, connection: CalendarConnection, event_id: str) -> None: ...


class CalendarPushError(RuntimeError):
    """The provider refused or could not be reached."""


def access_token(connection: CalendarConnection) -> str:
    try:
        token = resolve_secret(connection.secret_ref)
    except (SecretUnavailableError, ValueError, LookupError) as exc:
        raise CalendarPushError(f"calendar credential unavailable: {exc}") from exc
    if not isinstance(token, str) or not token:
        raise CalendarPushError("calendar credential unavailable")
    return token


class _HttpProvider:
    name = "http"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def _request(self, method: str, url: str, token: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            if self._client is not None:
                response = await self._client.request(method, url, headers=headers, **kwargs)
            else:  # pragma: no cover - exercised through an injected client in tests
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                    response = await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise CalendarPushError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise CalendarPushError(f"HTTP {response.status_code}: {response.text[:200]}")
        return response


class GoogleCalendarProvider(_HttpProvider):
    """Google Calendar API v3 (events.insert / patch / delete)."""

    name = PROVIDER_GOOGLE

    def _url(self, connection: CalendarConnection, event_id: str | None = None) -> str:
        base = f"{GOOGLE_API_URL}/calendars/{connection.calendar_id}/events"
        return base if event_id is None else f"{base}/{event_id}"

    def _body(self, entry: CalendarEntry) -> dict[str, Any]:
        start = entry.at.astimezone(UTC).replace(microsecond=0)
        end = start
        return {
            "summary": entry.summary,
            "description": build_description(entry),
            "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end": {
                "dateTime": (end + timedelta(minutes=entry.duration_minutes)).isoformat(),
                "timeZone": "UTC",
            },
            "iCalUID": entry.uid,
            "sequence": entry.sequence,
            "source": {"title": "BidRadar", "url": entry.url} if entry.url else None,
        }

    async def create_event(self, connection: CalendarConnection, entry: CalendarEntry) -> str:
        body = {k: v for k, v in self._body(entry).items() if v is not None}
        response = await self._request(
            "POST", self._url(connection), access_token(connection), json=body
        )
        event_id = response.json().get("id")
        if not event_id:
            raise CalendarPushError("google returned no event id")
        return str(event_id)

    async def update_event(
        self, connection: CalendarConnection, event_id: str, entry: CalendarEntry
    ) -> None:
        body = {k: v for k, v in self._body(entry).items() if v is not None}
        body.pop("iCalUID", None)  # immutable once created
        await self._request(
            "PATCH", self._url(connection, event_id), access_token(connection), json=body
        )

    async def delete_event(self, connection: CalendarConnection, event_id: str) -> None:
        await self._request("DELETE", self._url(connection, event_id), access_token(connection))


class MicrosoftGraphProvider(_HttpProvider):
    """Microsoft Graph /me/calendars/{id}/events."""

    name = PROVIDER_MICROSOFT

    def _url(self, connection: CalendarConnection, event_id: str | None = None) -> str:
        base = f"{GRAPH_API_URL}/me/calendars/{connection.calendar_id}/events"
        return base if event_id is None else f"{GRAPH_API_URL}/me/events/{event_id}"

    def _body(self, entry: CalendarEntry) -> dict[str, Any]:
        start = entry.at.astimezone(UTC).replace(microsecond=0)
        return {
            "subject": entry.summary,
            "body": {"contentType": "text", "content": build_description(entry)},
            "start": {"dateTime": start.isoformat().replace("+00:00", ""), "timeZone": "UTC"},
            "end": {
                "dateTime": (start + timedelta(minutes=entry.duration_minutes))
                .isoformat()
                .replace("+00:00", ""),
                "timeZone": "UTC",
            },
            "showAs": "free",
            "transactionId": entry.uid,
        }

    async def create_event(self, connection: CalendarConnection, entry: CalendarEntry) -> str:
        response = await self._request(
            "POST", self._url(connection), access_token(connection), json=self._body(entry)
        )
        event_id = response.json().get("id")
        if not event_id:
            raise CalendarPushError("microsoft returned no event id")
        return str(event_id)

    async def update_event(
        self, connection: CalendarConnection, event_id: str, entry: CalendarEntry
    ) -> None:
        await self._request(
            "PATCH",
            self._url(connection, event_id),
            access_token(connection),
            json=self._body(entry),
        )

    async def delete_event(self, connection: CalendarConnection, event_id: str) -> None:
        await self._request("DELETE", self._url(connection, event_id), access_token(connection))


def provider_for(name: str, *, client: httpx.AsyncClient | None = None) -> CalendarProvider | None:
    if name == PROVIDER_GOOGLE:
        return GoogleCalendarProvider(client)
    if name == PROVIDER_MICROSOFT:
        return MicrosoftGraphProvider(client)
    return None


# --- pushing a date to every connected calendar ------------------------------------------------

ACTION_UPSERT = "upsert"
ACTION_DELETE = "delete"


async def connections_for(
    session: AsyncSession, user_id: uuid.UUID
) -> Sequence[CalendarConnection]:
    return (
        (
            await session.execute(
                select(CalendarConnection).where(
                    CalendarConnection.user_id == user_id,
                    CalendarConnection.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )


async def sync_date(
    session: AsyncSession,
    settings: Settings,
    date: PursuitDate,
    *,
    action: str = ACTION_UPSERT,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> list[CalendarEvent]:
    """Create / update / delete the provider events for one key date.

    A provider error is recorded on the row (`last_error`) and logged; it never fails the
    request that moved the date — the iCal feed is always correct even when a push is not.
    """
    # the caller has just flushed, so server-side columns (updated_at) may be expired and
    # reading them from a sync helper would raise MissingGreenlet
    await session.refresh(date)
    pursuit = await session.get(Pursuit, date.pursuit_id)
    if pursuit is None or pursuit.owner_user_id is None:  # pragma: no cover - FK guarantees
        return []
    opportunity = await session.get(Opportunity, pursuit.opportunity_id)
    if opportunity is None:  # pragma: no cover - FK guarantees
        return []
    prefs = await prefs_for(session, pursuit.owner_user_id)
    entry = entry_for(
        settings, date, pursuit, opportunity, user_tz=None if prefs is None else prefs.tz
    )
    moment = now or datetime.now(UTC)
    touched: list[CalendarEvent] = []
    for connection in await connections_for(session, pursuit.owner_user_id):
        provider = provider_for(connection.provider, client=client)
        if provider is None:  # pragma: no cover - the CHECK constrains the column
            continue
        existing = (
            await session.execute(
                select(CalendarEvent).where(
                    CalendarEvent.connection_id == connection.id,
                    CalendarEvent.pursuit_date_id == date.id,
                )
            )
        ).scalar_one_or_none()
        try:
            if action == ACTION_DELETE:
                if existing is not None:
                    await provider.delete_event(connection, existing.provider_event_id)
                    await session.delete(existing)
                continue
            if existing is None:
                event_id = await provider.create_event(connection, entry)
                existing = CalendarEvent(
                    tenant_id=date.tenant_id,
                    connection_id=connection.id,
                    pursuit_date_id=date.id,
                    provider_event_id=event_id,
                    sequence=entry.sequence,
                    synced_at=moment,
                )
                session.add(existing)
            else:
                bumped = max(existing.sequence + 1, entry.sequence)
                await provider.update_event(
                    connection, existing.provider_event_id, replace(entry, sequence=bumped)
                )
                existing.sequence = bumped
                existing.synced_at = moment
                existing.last_error = None
            touched.append(existing)
        except CalendarPushError as exc:
            log.warning(
                "calendar.push_failed",
                provider=connection.provider,
                pursuit_date_id=str(date.id),
                error=str(exc),
            )
            connection.last_error = str(exc)[:500]
            if existing is not None:
                existing.last_error = str(exc)[:500]
                touched.append(existing)
    await session.flush()
    return touched
