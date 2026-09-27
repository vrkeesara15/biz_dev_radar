"""M6-03: the reminder ladder end to end — generation, the beat, escalation, time zones,
DST, an amendment that moves the deadline, and acknowledgement."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.roles import Role
from app.jobs.reminders import send_reminders_once
from app.models import (
    CompanyProfile,
    Membership,
    Notification,
    Opportunity,
    Pursuit,
    PursuitDate,
    Reminder,
    User,
    UserNotificationPrefs,
)
from app.notify.core import Dispatcher, SendResult
from app.services.key_dates import recalculate_for_opportunity
from app.services.reminders import DEFAULT_CHANNELS, generate_for_date, send_due
from freezegun import freeze_time
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner, make_user

# 2:00 PM EDT / 11:30 PM IST on the SPEC 9 example day
DUE = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]


class RecordingChannel:
    """A channel that records who it reached, so the tests assert on escalation."""

    def __init__(self, name: str = "email") -> None:
        self.name = name
        self.calls: list[tuple[uuid.UUID, str, dict[str, Any]]] = []

    async def send(self, delivery, notification, recipient) -> SendResult:  # type: ignore[no-untyped-def]
        self.calls.append((recipient.user_id, notification.event_type, dict(notification.payload)))
        return SendResult.sent(provider_ref=f"{self.name}-{len(self.calls)}")

    def users(self) -> set[uuid.UUID]:
        return {user_id for user_id, _, _ in self.calls}

    def clear(self) -> None:
        self.calls.clear()


def _dispatcher(channel: RecordingChannel) -> Dispatcher:
    return Dispatcher({channel.name: channel}, SETTINGS)


async def _setup(
    database: Database,
    *,
    region: Region = Region.US,
    owner_tz: str = "America/New_York",
    due: datetime | None = DUE,
) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, owner, _ = await create_tenant_with_owner(session, region=region)
        owner.tz = owner_tz
        manager, boss = make_user(), make_user()
        profile = CompanyProfile(tenant_id=tenant.id, region=region, legal_name="Ladder LLC")
        session.add_all([manager, boss, profile])
        await session.flush()
        session.add_all(
            [
                Membership(tenant_id=tenant.id, user_id=manager.id, role=Role.BID_MANAGER),
                Membership(tenant_id=tenant.id, user_id=boss.id, role=Role.TENANT_OWNER),
            ]
        )
        opportunity = Opportunity(
            source_id="sam_opps",
            external_id=f"rem-{uuid.uuid4().hex[:8]}",
            region=region,
            country="US" if region is Region.US else "IN",
            currency="USD" if region is Region.US else "INR",
            notice_type=NoticeType.RFP,
            title="Helpdesk services",
            buyer_org="Internal Revenue Service",
            source_tz="America/New_York" if region is Region.US else "Asia/Kolkata",
            response_due_at=due,
        )
        session.add(opportunity)
        await session.flush()
        pursuit = Pursuit(
            tenant_id=tenant.id,
            profile_id=profile.id,
            opportunity_id=opportunity.id,
            owner_user_id=owner.id,
            created_by=owner.id,
        )
        session.add(pursuit)
        await session.flush()
        return {
            "tenant_id": tenant.id,
            "owner_id": owner.id,
            "manager_id": manager.id,
            "boss_id": boss.id,
            "profile_id": profile.id,
            "opportunity_id": opportunity.id,
            "pursuit_id": pursuit.id,
        }


async def _date(
    database: Database,
    ctx: dict[str, Any],
    *,
    at: datetime = DUE,
    kind: str = "portal_submission",
    buyer_tz: str = "America/New_York",
) -> uuid.UUID:
    async with database.owner_session(ctx["tenant_id"]) as session:
        row = PursuitDate(
            tenant_id=ctx["tenant_id"],
            pursuit_id=ctx["pursuit_id"],
            kind=kind,
            at=at,
            buyer_tz=buyer_tz,
            label="Portal submission due",
        )
        session.add(row)
        await session.flush()
        return row.id


async def _rungs(database: Database, ctx: dict[str, Any], date_id: uuid.UUID) -> list[Reminder]:
    async with database.session(ctx["tenant_id"]) as session:
        return list(
            (
                await session.execute(
                    select(Reminder)
                    .where(Reminder.pursuit_date_id == date_id)
                    .order_by(Reminder.due_at)
                )
            )
            .scalars()
            .all()
        )


async def _tick(database: Database, ctx: dict[str, Any], channel: RecordingChannel, now: datetime):  # type: ignore[no-untyped-def]
    async with database.session(ctx["tenant_id"]) as session:
        return await send_due(session, SETTINGS, _dispatcher(channel), ctx["tenant_id"], now=now)


async def test_the_ladder_is_generated_and_fires_rung_by_rung(
    database: Database, clean_db: Database
) -> None:
    ctx = await _setup(database)
    date_id = await _date(database, ctx)
    async with database.session(ctx["tenant_id"]) as session:
        date = await session.get(PursuitDate, date_id)
        assert date is not None
        created = await generate_for_date(session, date, now=NOW)
    assert [r.offset_label for r in created] == ["7d", "3d", "24h", "4h", "1h"]

    channel = RecordingChannel()
    # nothing is due a fortnight out
    run = await _tick(database, ctx, channel, NOW)
    assert (run.sent, run.considered) == (0, 0)

    # the 7-day rung
    run = await _tick(database, ctx, channel, DUE - timedelta(days=7) + timedelta(minutes=1))
    assert run.sent == 1
    assert channel.users() == {ctx["owner_id"]}
    payload = channel.calls[0][2]
    assert payload["offset_label"] == "7d"
    assert payload["offset_display"] == "7 days to go"
    assert payload["key_date"] == "Portal submission due"
    assert payload["escalation_level"] == 0

    # an immediate second tick sends nothing (the row is stamped sent_at)
    channel.clear()
    assert (await _tick(database, ctx, channel, DUE - timedelta(days=7, minutes=-2))).sent == 0
    assert channel.calls == []

    rungs = {r.offset_label: r for r in await _rungs(database, ctx, date_id)}
    assert rungs["7d"].sent_at is not None
    assert rungs["7d"].delivery_notification_id is not None
    assert rungs["3d"].sent_at is None


async def test_escalation_reaches_managers_then_the_tenant_owner(
    database: Database, clean_db: Database
) -> None:
    ctx = await _setup(database)
    date_id = await _date(database, ctx)
    async with database.session(ctx["tenant_id"]) as session:
        date = await session.get(PursuitDate, date_id)
        assert date is not None
        await generate_for_date(session, date, now=NOW)

    channel = RecordingChannel()
    # 3 days out: the owner only
    await _tick(database, ctx, channel, DUE - timedelta(days=3) + timedelta(minutes=1))
    assert channel.users() == {ctx["owner_id"]}

    # 24 hours out, unacknowledged: + the bid managers (SPEC 9)
    channel.clear()
    await _tick(database, ctx, channel, DUE - timedelta(hours=24) + timedelta(minutes=1))
    assert channel.users() == {ctx["owner_id"], ctx["manager_id"]}
    assert channel.calls[0][2]["escalation_level"] == 1

    # 4 hours out: + the tenant owner
    channel.clear()
    await _tick(database, ctx, channel, DUE - timedelta(hours=4) + timedelta(minutes=1))
    assert channel.users() == {ctx["owner_id"], ctx["manager_id"], ctx["boss_id"]}
    assert channel.calls[0][2]["escalation_level"] == 2


async def test_acknowledging_stops_the_escalation(database: Database, clean_db: Database) -> None:
    ctx = await _setup(database)
    date_id = await _date(database, ctx)
    async with database.owner_session(ctx["tenant_id"]) as session:
        date = await session.get(PursuitDate, date_id)
        assert date is not None
        await generate_for_date(session, date, now=NOW)
        date.acknowledged_at = NOW
        date.acknowledged_by = ctx["owner_id"]

    channel = RecordingChannel()
    await _tick(database, ctx, channel, DUE - timedelta(hours=4) + timedelta(minutes=1))
    # the owner still hears about it; nobody is escalated to
    assert channel.users() == {ctx["owner_id"]}
    assert channel.calls[0][2]["escalation_level"] == 0
    assert channel.calls[0][2]["acknowledged"] is True


async def test_overdue_fires_every_four_hours_until_the_pursuit_closes(
    database: Database, clean_db: Database
) -> None:
    ctx = await _setup(database)
    date_id = await _date(database, ctx)
    channel = RecordingChannel()

    run = await _tick(database, ctx, channel, DUE + timedelta(hours=5))
    overdue = [r for r in await _rungs(database, ctx, date_id) if r.offset_label == "overdue"]
    assert [r.due_at for r in overdue] == [DUE + timedelta(hours=4)]
    assert run.sent >= 1
    assert channel.calls[-1][2]["offset_display"] == "overdue"

    channel.clear()
    await _tick(database, ctx, channel, DUE + timedelta(hours=9))
    overdue = [r for r in await _rungs(database, ctx, date_id) if r.offset_label == "overdue"]
    assert [r.due_at for r in overdue] == [DUE + timedelta(hours=4), DUE + timedelta(hours=8)]
    assert len(channel.calls) >= 1

    # marking it submitted stops the ladder
    async with database.owner_session(ctx["tenant_id"]) as session:
        pursuit = await session.get(Pursuit, ctx["pursuit_id"])
        assert pursuit is not None
        pursuit.stage = "submitted"
    channel.clear()
    run = await _tick(database, ctx, channel, DUE + timedelta(hours=13))
    assert channel.calls == []
    assert run.created == 0


async def test_a_rung_that_comes_due_after_the_pursuit_closed_is_skipped(
    database: Database, clean_db: Database
) -> None:
    ctx = await _setup(database)
    date_id = await _date(database, ctx)
    async with database.session(ctx["tenant_id"]) as session:
        date = await session.get(PursuitDate, date_id)
        assert date is not None
        await generate_for_date(session, date, now=NOW)
    async with database.owner_session(ctx["tenant_id"]) as session:
        pursuit = await session.get(Pursuit, ctx["pursuit_id"])
        assert pursuit is not None
        pursuit.stage = "cancelled"
        pursuit.pass_reason = "buyer withdrew"

    channel = RecordingChannel()
    run = await _tick(database, ctx, channel, DUE - timedelta(days=7) + timedelta(minutes=1))
    assert channel.calls == []
    assert run.skipped == 1
    rungs = {r.offset_label: r for r in await _rungs(database, ctx, date_id)}
    assert rungs["7d"].sent_at is None
    assert "cancelled" in (rungs["7d"].skipped_reason or "")


async def test_a_us_reader_and_an_ist_reader_are_reminded_at_the_same_instant(
    database: Database, clean_db: Database
) -> None:
    ctx = await _setup(database)
    async with database.owner_session(ctx["tenant_id"]) as session:
        # the bid manager reads in IST, the owner in ET
        session.add(
            UserNotificationPrefs(
                tenant_id=ctx["tenant_id"], user_id=ctx["manager_id"], tz="Asia/Kolkata"
            )
        )
        session.add(
            UserNotificationPrefs(
                tenant_id=ctx["tenant_id"], user_id=ctx["owner_id"], tz="America/New_York"
            )
        )
    date_id = await _date(database, ctx)
    async with database.session(ctx["tenant_id"]) as session:
        date = await session.get(PursuitDate, date_id)
        assert date is not None
        await generate_for_date(session, date, now=NOW)

    channel = RecordingChannel()
    fires_at = DUE - timedelta(hours=24) + timedelta(minutes=1)
    await _tick(database, ctx, channel, fires_at)
    assert channel.users() == {ctx["owner_id"], ctx["manager_id"]}
    # the same deadline, one instant; the display string carries the buyer's clock
    payload = channel.calls[0][2]
    assert payload["due_display"] == "Oct 14, 2026, 2:00 PM EDT"
    assert payload["buyer_tz"] == "America/New_York"
    # each reader's own zone travels on their Recipient (rendered by the channel)
    assert DUE.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%H:%M") == "23:30"
    assert DUE.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M") == "14:00"


async def test_the_ladder_survives_the_2026_11_01_fall_back(
    database: Database, clean_db: Database
) -> None:
    """A deadline just after the US fall-back: the rungs are absolute instants, so the
    24-hour rung is 24 REAL hours earlier even though the wall clock says 25."""
    eastern = ZoneInfo("America/New_York")
    due = datetime(2026, 11, 2, 17, 0, tzinfo=UTC)  # 12:00 EST on the Monday after
    ctx = await _setup(database, due=due)
    date_id = await _date(database, ctx, at=due)
    async with database.session(ctx["tenant_id"]) as session:
        date = await session.get(PursuitDate, date_id)
        assert date is not None
        created = await generate_for_date(session, date, now=datetime(2026, 10, 20, tzinfo=UTC))
    by_label = {r.offset_label: r.due_at for r in created}
    assert by_label["24h"] == due - timedelta(hours=24)
    assert by_label["3d"] == due - timedelta(days=3)
    # the 3-day rung falls BEFORE the transition, the 24-hour one after it
    assert by_label["3d"].astimezone(eastern).utcoffset() == timedelta(hours=-4)  # EDT
    assert by_label["24h"].astimezone(eastern).utcoffset() == timedelta(hours=-5)  # EST
    # noon to noon across the fall-back night is 25 REAL hours, which is exactly why the
    # ladder counts instants and not wall clocks (the .astimezone(UTC) matters: Python
    # subtracts two datetimes that share a tzinfo as if they were naive)
    assert datetime(2026, 11, 1, 12, 0, tzinfo=eastern).astimezone(UTC) - datetime(
        2026, 10, 31, 12, 0, tzinfo=eastern
    ).astimezone(UTC) == timedelta(hours=25)
    assert by_label["24h"].astimezone(eastern).strftime("%H:%M") == "12:00"

    channel = RecordingChannel()
    run = await _tick(database, ctx, channel, by_label["24h"] + timedelta(minutes=1))
    # the 7d and 3d rungs are due by then too; all three fire in this pass
    assert run.sent == 3
    assert {c[2]["offset_label"] for c in channel.calls} == {"7d", "3d", "24h"}
    # the deadline itself is after the switch, so it renders in EST
    assert all("EST" in c[2]["due_display"] for c in channel.calls)


async def test_an_amendment_that_moves_the_deadline_regenerates_the_ladder(
    database: Database, clean_db: Database
) -> None:
    ctx = await _setup(database)
    async with database.session(ctx["tenant_id"]) as session:
        pursuit = await session.get(Pursuit, ctx["pursuit_id"])
        opportunity = await session.get(Opportunity, ctx["opportunity_id"])
        assert pursuit is not None and opportunity is not None
        from app.services.key_dates import sync_auto_dates

        await sync_auto_dates(session, pursuit, opportunity, now=NOW)
        submission = (
            await session.execute(
                select(PursuitDate).where(
                    PursuitDate.pursuit_id == pursuit.id,
                    PursuitDate.kind == "portal_submission",
                )
            )
        ).scalar_one()
        submission_id = submission.id
    before = {r.offset_label: r.due_at for r in await _rungs(database, ctx, submission_id)}
    assert before["24h"] == DUE - timedelta(hours=24)

    # fire the 7-day rung so it becomes history
    channel = RecordingChannel()
    await _tick(database, ctx, channel, DUE - timedelta(days=7) + timedelta(minutes=1))

    new_due = DUE + timedelta(days=10)
    async with database.session(ctx["tenant_id"]) as session:
        opportunity = await session.get(Opportunity, ctx["opportunity_id"])
        assert opportunity is not None
        opportunity.response_due_at = new_due
        await session.flush()
        await recalculate_for_opportunity(session, opportunity, now=NOW)

    after = await _rungs(database, ctx, submission_id)
    sent = [r for r in after if r.sent_at is not None]
    pending = {r.offset_label: r.due_at for r in after if r.sent_at is None}
    assert [r.offset_label for r in sent] == ["7d"]  # history is kept
    assert pending["24h"] == new_due - timedelta(hours=24)
    assert pending["7d"] == new_due - timedelta(days=7)  # a fresh 7-day rung for the new date

    # the submission date's old 24-hour moment now passes without a reminder for IT
    # (other key dates of the same pursuit legitimately have rungs around then)
    channel.clear()
    await _tick(database, ctx, channel, DUE - timedelta(hours=24) + timedelta(minutes=1))
    assert [c for c in channel.calls if c[2]["key_date_kind"] == "portal_submission"] == []
    channel.clear()
    await _tick(database, ctx, channel, new_due - timedelta(hours=24) + timedelta(minutes=1))
    submission_calls = [c for c in channel.calls if c[2]["key_date_kind"] == "portal_submission"]
    # every earlier rung of the NEW schedule is due by then and fires in the same pass
    assert "24h" in {c[2]["offset_label"] for c in submission_calls}


async def test_the_beat_job_walks_every_tenant(database: Database, clean_db: Database) -> None:
    a = await _setup(database)
    b = await _setup(database)
    for ctx in (a, b):
        date_id = await _date(database, ctx)
        async with database.session(ctx["tenant_id"]) as session:
            date = await session.get(PursuitDate, date_id)
            assert date is not None
            await generate_for_date(session, date, now=NOW)

    channel = RecordingChannel()
    with freeze_time(DUE - timedelta(days=7) + timedelta(minutes=1), real_asyncio=True):
        run = await send_reminders_once(
            database=database, settings=SETTINGS, dispatcher=_dispatcher(channel)
        )
    assert run.sent == 2
    assert channel.users() == {a["owner_id"], b["owner_id"]}

    # a second pass in the same window sends nothing
    channel.clear()
    with freeze_time(DUE - timedelta(days=7) + timedelta(minutes=2), real_asyncio=True):
        run = await send_reminders_once(
            database=database, settings=SETTINGS, dispatcher=_dispatcher(channel)
        )
    assert run.sent == 0
    assert channel.calls == []


async def test_the_channels_follow_spec_7_and_the_users_preferences(
    database: Database, clean_db: Database
) -> None:
    assert DEFAULT_CHANNELS == ("in_app", "email", "slack", "whatsapp")
    ctx = await _setup(database, region=Region.IN)
    async with database.owner_session(ctx["tenant_id"]) as session:
        session.add(
            UserNotificationPrefs(
                tenant_id=ctx["tenant_id"],
                user_id=ctx["owner_id"],
                channels_by_event={"deadline_reminder": ["email"]},
            )
        )
    date_id = await _date(database, ctx, buyer_tz="Asia/Kolkata")
    async with database.session(ctx["tenant_id"]) as session:
        date = await session.get(PursuitDate, date_id)
        assert date is not None
        await generate_for_date(session, date, now=NOW)

    channel = RecordingChannel()
    async with database.session(ctx["tenant_id"]) as session:
        await send_due(
            session,
            SETTINGS,
            _dispatcher(channel),
            ctx["tenant_id"],
            now=DUE - timedelta(days=7) + timedelta(minutes=1),
        )
        deliveries = (
            (
                await session.execute(
                    select(Notification).where(Notification.event_type == "deadline_reminder")
                )
            )
            .scalars()
            .all()
        )
    assert len(deliveries) == 1
    assert len(channel.calls) == 1


async def test_reminders_are_tenant_scoped(database: Database, clean_db: Database) -> None:
    a = await _setup(database)
    date_id = await _date(database, a)
    async with database.session(a["tenant_id"]) as session:
        date = await session.get(PursuitDate, date_id)
        assert date is not None
        await generate_for_date(session, date, now=NOW)
    b = await _setup(database)
    async with database.session(b["tenant_id"]) as session:
        assert (await session.execute(select(Reminder))).scalars().all() == []


async def test_pursuing_through_the_api_creates_the_whole_ladder(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    async with database.owner_session(ctx["tenant_id"]) as session:
        pursuit = await session.get(Pursuit, ctx["pursuit_id"])
        assert pursuit is not None
        await session.delete(pursuit)
    headers = auth_headers(
        user_id=ctx["owner_id"], tenant_id=ctx["tenant_id"], role=Role.TENANT_OWNER
    )
    response = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    async with database.session(ctx["tenant_id"]) as session:
        rows = (await session.execute(select(Reminder))).scalars().all()
        user = await session.get(User, ctx["owner_id"])
        assert user is not None
    # four auto dates x five rungs
    assert len(rows) == 20
    assert {r.offset_label for r in rows} == {"7d", "3d", "24h", "4h", "1h"}
