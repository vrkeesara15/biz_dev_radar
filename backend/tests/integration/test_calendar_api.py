"""M6-04: the signed iCal feed, its token rotation, and the mocked Google / Microsoft push."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.roles import Role
from app.models import (
    CalendarConnection,
    CalendarEvent,
    CompanyProfile,
    Membership,
    Opportunity,
    PursuitDate,
    PursuitTask,
    UserNotificationPrefs,
)
from app.services import calendar as calendar_svc
from app.services.calendar import (
    CalendarPushError,
    GoogleCalendarProvider,
    MicrosoftGraphProvider,
    provider_for,
    sign_calendar_token,
    sync_date,
    verify_calendar_token,
)
from icalendar import Calendar
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner, make_user

DUE = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
TEST_SECRET = "dev-only-change-me-0123456789abcdef0123456789abcdef"


async def _setup(database: Database) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, owner, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(tenant_id=tenant.id, region=Region.US, legal_name="Cal LLC")
        other = make_user()
        session.add_all([profile, other])
        await session.flush()
        session.add(Membership(tenant_id=tenant.id, user_id=other.id, role=Role.WRITER))
        opportunity = Opportunity(
            source_id="sam_opps",
            external_id=f"cal-{uuid.uuid4().hex[:8]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Helpdesk services",
            source_tz="America/New_York",
            response_due_at=DUE,
        )
        session.add(opportunity)
        await session.flush()
        return {
            "tenant_id": tenant.id,
            "owner_id": owner.id,
            "other_id": other.id,
            "profile_id": profile.id,
            "opportunity_id": opportunity.id,
        }


def _headers(ctx: dict[str, Any], role: Role = Role.TENANT_OWNER) -> dict[str, str]:
    user_id = ctx["other_id"] if role is Role.WRITER else ctx["owner_id"]
    return auth_headers(user_id=user_id, tenant_id=ctx["tenant_id"], role=role)


async def _open(client: httpx.AsyncClient, ctx: dict[str, Any]) -> str:
    response = await client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["pursuit"]["id"])


def _token_of(feed_url: str) -> str:
    return feed_url.split("token=", 1)[1]


async def test_the_feed_is_issued_rotated_and_serves_the_users_dates(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    await _open(api_client, ctx)

    before = (await api_client.get("/api/v1/me/calendar", headers=_headers(ctx))).json()
    assert before["feed_url"] is None
    assert before["connections"] == []

    issued = await api_client.post("/api/v1/me/calendar-token", headers=_headers(ctx))
    assert issued.status_code == 200, issued.text
    feed_url = issued.json()["feed_url"]
    assert "/api/v1/calendar.ics?token=" in feed_url
    claims = verify_calendar_token(_token_of(feed_url), TEST_SECRET)
    assert claims.user_id == ctx["owner_id"]
    assert claims.tenant_id == ctx["tenant_id"]

    again = (await api_client.get("/api/v1/me/calendar", headers=_headers(ctx))).json()
    assert again["feed_url"] == feed_url

    feed = await api_client.get("/api/v1/calendar.ics", params={"token": _token_of(feed_url)})
    assert feed.status_code == 200, feed.text
    assert feed.headers["content-type"].startswith("text/calendar")
    parsed = Calendar.from_ical(feed.content)
    events = list(parsed.walk("VEVENT"))
    assert len(events) == 4  # the four US auto dates
    summaries = sorted(str(e["summary"]) for e in events)
    assert summaries[-1] == "Portal submission due — Helpdesk services"
    submission = next(e for e in events if "Portal submission" in str(e["summary"]))
    assert submission.decoded("dtstart") == DUE
    assert "EDT" in str(submission["description"])
    assert "/app/pursuits/" in str(submission["url"])
    assert str(submission["uid"]).startswith("pursuit-date-")

    # rotating invalidates the old link
    rotated = (await api_client.post("/api/v1/me/calendar-token", headers=_headers(ctx))).json()[
        "feed_url"
    ]
    assert rotated != feed_url
    assert (
        await api_client.get("/api/v1/calendar.ics", params={"token": _token_of(feed_url)})
    ).status_code == 401
    assert (
        await api_client.get("/api/v1/calendar.ics", params={"token": _token_of(rotated)})
    ).status_code == 200


async def test_the_feed_refuses_a_bad_tampered_or_foreign_token(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    await _open(api_client, ctx)
    feed_url = (await api_client.post("/api/v1/me/calendar-token", headers=_headers(ctx))).json()[
        "feed_url"
    ]
    token = _token_of(feed_url)

    for bad in ("", "garbage", token[:-4] + "aaaa"):
        response = await api_client.get("/api/v1/calendar.ics", params={"token": bad})
        assert response.status_code in (401, 422), bad

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    foreign = sign_calendar_token(
        settings, tenant_id=ctx["tenant_id"], user_id=ctx["owner_id"], nonce="not-the-nonce"
    )
    assert (
        await api_client.get("/api/v1/calendar.ics", params={"token": foreign})
    ).status_code == 401
    # a token signed with another secret is refused
    other_secret = Settings(_env_file=None, auth_secret="x" * 48)  # type: ignore[call-arg]
    stranger = sign_calendar_token(
        other_secret, tenant_id=ctx["tenant_id"], user_id=ctx["owner_id"], nonce="n"
    )
    assert (
        await api_client.get("/api/v1/calendar.ics", params={"token": stranger})
    ).status_code == 401


async def test_the_feed_holds_only_the_readers_own_pursuits(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    pursuit_id = await _open(api_client, ctx)
    mine = (await api_client.post("/api/v1/me/calendar-token", headers=_headers(ctx))).json()[
        "feed_url"
    ]
    theirs = (
        await api_client.post("/api/v1/me/calendar-token", headers=_headers(ctx, Role.WRITER))
    ).json()["feed_url"]

    empty = await api_client.get("/api/v1/calendar.ics", params={"token": _token_of(theirs)})
    assert list(Calendar.from_ical(empty.content).walk("VEVENT")) == []

    # an assignee sees the dates of a pursuit they have a task on (SPEC 9)
    async with database.owner_session(ctx["tenant_id"]) as session:
        session.add(
            PursuitTask(
                tenant_id=ctx["tenant_id"],
                pursuit_id=uuid.UUID(pursuit_id),
                title="Draft the technical volume",
                assignee_user_id=ctx["other_id"],
            )
        )
    now_visible = await api_client.get("/api/v1/calendar.ics", params={"token": _token_of(theirs)})
    assert len(list(Calendar.from_ical(now_visible.content).walk("VEVENT"))) == 4
    assert (
        len(
            list(
                Calendar.from_ical(
                    (
                        await api_client.get(
                            "/api/v1/calendar.ics", params={"token": _token_of(mine)}
                        )
                    ).content
                ).walk("VEVENT")
            )
        )
        == 4
    )


async def test_a_moved_date_bumps_the_sequence_in_the_feed(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    pursuit_id = await _open(api_client, ctx)
    feed_url = (await api_client.post("/api/v1/me/calendar-token", headers=_headers(ctx))).json()[
        "feed_url"
    ]
    dates = (
        await api_client.get(f"/api/v1/pursuits/{pursuit_id}/dates", headers=_headers(ctx))
    ).json()["items"]
    submission = next(d for d in dates if d["kind"] == "portal_submission")

    assert submission["at"]["utc"].startswith("2026-10-14")

    moved = await api_client.put(
        f"/api/v1/pursuits/{pursuit_id}/dates/{submission['id']}",
        json={"at": (DUE + timedelta(days=2)).isoformat()},
        headers=_headers(ctx),
    )
    assert moved.status_code == 200
    feed = await api_client.get("/api/v1/calendar.ics", params={"token": _token_of(feed_url)})
    event = next(
        e
        for e in Calendar.from_ical(feed.content).walk("VEVENT")
        if str(e["uid"]) == f"pursuit-date-{submission['id']}@bidradar"
    )
    assert int(event["sequence"]) > 0
    assert event.decoded("dtstart") == DUE + timedelta(days=2)


# --- provider push --------------------------------------------------------------------------


async def _connect(database: Database, ctx: dict[str, Any], provider: str = "google") -> uuid.UUID:
    async with database.owner_session(ctx["tenant_id"]) as session:
        row = CalendarConnection(
            tenant_id=ctx["tenant_id"],
            user_id=ctx["owner_id"],
            provider=provider,
            calendar_id="primary",
            secret_ref="env:TEST_CALENDAR_TOKEN",
        )
        session.add(row)
        await session.flush()
        return row.id


def test_provider_lookup() -> None:
    assert isinstance(provider_for("google"), GoogleCalendarProvider)
    assert isinstance(provider_for("microsoft"), MicrosoftGraphProvider)
    assert provider_for("apple") is None


async def test_google_push_creates_updates_and_deletes(
    api_client: httpx.AsyncClient, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_CALENDAR_TOKEN", "ya29.fake")
    ctx = await _setup(database)
    pursuit_id = await _open(api_client, ctx)
    connection_id = await _connect(database, ctx)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    async with database.session(ctx["tenant_id"]) as session:
        date = (
            await session.execute(
                select(PursuitDate).where(
                    PursuitDate.pursuit_id == uuid.UUID(pursuit_id),
                    PursuitDate.kind == "portal_submission",
                )
            )
        ).scalar_one()

        with respx.mock(assert_all_called=True) as mock:
            create = mock.post(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events"
            ).mock(return_value=httpx.Response(200, json={"id": "goog-1"}))
            async with httpx.AsyncClient() as client:
                touched = await sync_date(session, settings, date, client=client)
            assert [e.provider_event_id for e in touched] == ["goog-1"]
            body = create.calls[0].request
            assert body.headers["authorization"] == "Bearer ya29.fake"
            sent = create.calls[0].request.content.decode()
            assert "Portal submission due" in sent
            assert "iCalUID" in sent

        # a second sync updates the same event with a higher sequence
        with respx.mock(assert_all_called=True) as mock:
            patch = mock.patch(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events/goog-1"
            ).mock(return_value=httpx.Response(200, json={"id": "goog-1"}))
            async with httpx.AsyncClient() as client:
                await sync_date(session, settings, date, client=client)
            assert patch.called
        stored = (
            await session.execute(
                select(CalendarEvent).where(CalendarEvent.connection_id == connection_id)
            )
        ).scalar_one()
        assert stored.sequence >= 1
        assert stored.last_error is None

        with respx.mock(assert_all_called=True) as mock:
            delete = mock.delete(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events/goog-1"
            ).mock(return_value=httpx.Response(204))
            async with httpx.AsyncClient() as client:
                await sync_date(session, settings, date, client=client, action="delete")
            assert delete.called
        assert (
            await session.execute(
                select(CalendarEvent).where(CalendarEvent.connection_id == connection_id)
            )
        ).scalar_one_or_none() is None


async def test_microsoft_push_and_a_provider_failure_is_recorded_not_raised(
    api_client: httpx.AsyncClient, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_CALENDAR_TOKEN", "graph.fake")
    ctx = await _setup(database)
    pursuit_id = await _open(api_client, ctx)
    await _connect(database, ctx, provider="microsoft")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    async with database.session(ctx["tenant_id"]) as session:
        date = (
            await session.execute(
                select(PursuitDate).where(
                    PursuitDate.pursuit_id == uuid.UUID(pursuit_id),
                    PursuitDate.kind == "internal_final",
                )
            )
        ).scalar_one()
        with respx.mock(assert_all_called=True) as mock:
            mock.post("https://graph.microsoft.com/v1.0/me/calendars/primary/events").mock(
                return_value=httpx.Response(201, json={"id": "graph-1"})
            )
            async with httpx.AsyncClient() as client:
                touched = await sync_date(session, settings, date, client=client)
        assert [e.provider_event_id for e in touched] == ["graph-1"]

        # the provider goes down: the row records it, the caller carries on
        with respx.mock(assert_all_called=True) as mock:
            mock.patch("https://graph.microsoft.com/v1.0/me/events/graph-1").mock(
                return_value=httpx.Response(503, text="unavailable")
            )
            async with httpx.AsyncClient() as client:
                touched = await sync_date(session, settings, date, client=client)
        assert touched[0].last_error is not None
        assert "503" in touched[0].last_error
        connection = (await session.execute(select(CalendarConnection))).scalar_one()
        assert "503" in (connection.last_error or "")


async def test_a_missing_credential_is_a_push_error_not_a_crash(
    api_client: httpx.AsyncClient, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TEST_CALENDAR_TOKEN", raising=False)
    ctx = await _setup(database)
    pursuit_id = await _open(api_client, ctx)
    await _connect(database, ctx)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    async with database.session(ctx["tenant_id"]) as session:
        date = (
            (
                await session.execute(
                    select(PursuitDate).where(PursuitDate.pursuit_id == uuid.UUID(pursuit_id))
                )
            )
            .scalars()
            .first()
        )
        assert date is not None
        touched = await sync_date(session, settings, date)
        assert touched == []
        connection = (await session.execute(select(CalendarConnection))).scalar_one()
        assert "credential unavailable" in (connection.last_error or "")
        assert (await session.execute(select(CalendarEvent))).scalars().all() == []


def test_the_push_error_type_is_raised_for_an_unknown_credential() -> None:
    connection = CalendarConnection(
        tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), provider="google", secret_ref=None
    )
    with pytest.raises(CalendarPushError):
        calendar_svc.access_token(connection)


async def test_connections_are_listed_and_disconnected_by_their_owner(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    connection_id = await _connect(database, ctx)
    listed = (await api_client.get("/api/v1/me/calendar", headers=_headers(ctx))).json()
    assert [c["provider"] for c in listed["connections"]] == ["google"]
    assert "secret_ref" not in listed["connections"][0]

    # another member never sees or deletes it
    assert (await api_client.get("/api/v1/me/calendar", headers=_headers(ctx, Role.WRITER))).json()[
        "connections"
    ] == []
    assert (
        await api_client.delete(
            f"/api/v1/me/calendar-connections/{connection_id}",
            headers=_headers(ctx, Role.WRITER),
        )
    ).status_code == 404
    assert (
        await api_client.delete(
            f"/api/v1/me/calendar-connections/{connection_id}", headers=_headers(ctx)
        )
    ).status_code == 204
    async with database.session(ctx["tenant_id"]) as session:
        assert (await session.execute(select(CalendarConnection))).scalars().all() == []


async def test_the_feed_uses_the_readers_own_time_zone(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    await _open(api_client, ctx)
    feed_url = (await api_client.post("/api/v1/me/calendar-token", headers=_headers(ctx))).json()[
        "feed_url"
    ]
    async with database.owner_session(ctx["tenant_id"]) as session:
        prefs = (
            await session.execute(
                select(UserNotificationPrefs).where(
                    UserNotificationPrefs.user_id == ctx["owner_id"]
                )
            )
        ).scalar_one()
        prefs.tz = "Asia/Kolkata"
    feed = await api_client.get("/api/v1/calendar.ics", params={"token": _token_of(feed_url)})
    event = next(
        e for e in Calendar.from_ical(feed.content).walk("VEVENT") if "Portal" in str(e["summary"])
    )
    assert "= 11:30 PM IST" in str(event["description"])
