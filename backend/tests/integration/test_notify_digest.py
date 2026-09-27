"""M4-13: the digest builder, the send_digests beat job and the deferred-delivery flush."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.db import Database
from app.core.preferences import DEFAULT_DIGEST_TIME
from app.jobs.notify import (
    DIGEST_SCHEDULE,
    FLUSH_SCHEDULE,
    flush_scheduled_once,
    send_digests_once,
)
from app.models import DeliveryStatus, Notification, NotificationDelivery, UserNotificationPrefs
from app.notify.core import Dispatcher, NotificationEvent, Recipient
from app.notify.digest import ROLLED_UP_REASON, collect_digest, mark_rolled_up
from app.notify.email import EmailChannel, MemoryProvider
from app.notify.scheduling import SchedulePrefs, deliver_at
from sqlalchemy import select

from tests.factories import create_tenant_with_owner

NY = "America/New_York"
# Wednesday 2026-06-17 08:05 EDT: five minutes past an 08:00 digest, inside the beat
# window, and deliberately NOT a Monday so the weekly roll-up stays out of the way.
TICK = datetime(2026, 6, 17, 12, 5, tzinfo=UTC)
NIGHT = datetime(2026, 6, 17, 3, 0, tzinfo=UTC)  # 23:00 EDT the previous evening


async def _tenant(
    database: Database, *, tz: str = NY, digest_time: str = DEFAULT_DIGEST_TIME, **prefs: Any
) -> tuple[uuid.UUID, uuid.UUID, str]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        session.add(
            UserNotificationPrefs(
                tenant_id=tenant.id,
                user_id=user.id,
                channels_by_event={},
                tz=tz,
                digest_time=digest_time,
                **prefs,
            )
        )
        await session.flush()
        return tenant.id, user.id, user.email


def _dispatcher(settings: Settings, provider: MemoryProvider, now: datetime) -> Dispatcher:
    channel = EmailChannel(settings, provider=provider, now=lambda: now)
    return Dispatcher({"email": channel}, settings, clock=lambda: now)


async def _queue_deferred(
    database: Database,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    email: str,
    settings: Settings,
    *,
    count: int = 2,
    band: str = "high",
) -> list[uuid.UUID]:
    """Dispatch `count` events at 23:00 EDT so quiet hours queue them for the morning."""
    prefs = SchedulePrefs(tz=NY, quiet_hours_start="20:00", quiet_hours_end="07:00")
    plan = deliver_at(prefs, now=NIGHT)
    assert plan.deferred
    provider = MemoryProvider()
    ids: list[uuid.UUID] = []
    async with database.session(tenant_id) as session:
        for index in range(count):
            # a minute apart so "newest first" in the digest is unambiguous
            dispatcher = _dispatcher(settings, provider, NIGHT + timedelta(minutes=index))
            event = NotificationEvent(
                event_type="high_fit_match",
                tenant_id=tenant_id,
                version=index + 1,
                payload={
                    "title": f"Night notice {index}",
                    "buyer": "GSA",
                    "band": band,
                    "score": 72 + index,
                    "deep_link": f"https://app.example/app/opportunities/{index}",
                },
                occurred_at=NIGHT,
                dedupe_key=f"night-{index}",
            )
            result = await dispatcher.dispatch(
                session,
                event,
                [
                    Recipient(
                        user_id=user_id,
                        channels=("email",),
                        email=email,
                        tz=NY,
                        scheduled_for=plan.send_at,
                    )
                ],
            )
            ids.append(result.notifications[0].id)
    assert provider.messages == []  # nothing went out during quiet hours
    return ids


# --- the builder ----------------------------------------------------------------------------------


async def test_collect_digest_gathers_queued_deliveries_and_medium_matches(
    database: Database, settings: Settings
) -> None:
    tenant_id, user_id, email = await _tenant(database)
    queued = await _queue_deferred(database, tenant_id, user_id, email, settings)
    async with database.session(tenant_id) as session:
        medium = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type="high_fit_match",
            version=9,
            idempotency_key=f"{user_id}:medium:{uuid.uuid4()}:9",
            payload={"title": "Medium notice", "band": "medium", "score": 58},
            created_at=TICK - timedelta(hours=3),
        )
        stale = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type="high_fit_match",
            version=10,
            idempotency_key=f"{user_id}:stale:{uuid.uuid4()}:10",
            payload={"title": "Last week", "band": "medium"},
            created_at=TICK - timedelta(days=3),
        )
        session.add_all([medium, stale])
        await session.flush()

        plan = await collect_digest(
            session,
            user_id,
            tenant_id,
            since=TICK - timedelta(days=1),
            until=TICK,
        )
    titles = [item["title"] for item in plan.items]
    assert titles == ["Night notice 1", "Night notice 0", "Medium notice"]
    assert "Last week" not in titles  # outside the daily window
    assert len(plan.rolled_up) == 2
    assert plan.payload()["digest_period"] == "daily"
    # the link is the deep link the Dispatcher stamped on the notification payload
    assert plan.items[0]["link"] == f"{settings.app_base_url}/app"
    assert plan.items[0]["score"] == 73 and plan.items[1]["score"] == 72
    assert plan.items[2]["score"] == 58
    assert [i["notification_id"] for i in plan.items[:2]] == [
        str(queued[1]),
        str(queued[0]),
    ]

    # the weekly window reaches the older one
    async with database.session(tenant_id) as session:
        weekly = await collect_digest(
            session,
            user_id,
            tenant_id,
            since=TICK - timedelta(days=7),
            until=TICK,
            weekly=True,
        )
    assert "Last week" in [item["title"] for item in weekly.items]
    assert weekly.payload()["digest_period"] == "weekly"


async def test_rolled_up_deliveries_are_never_sent_again(
    database: Database, settings: Settings
) -> None:
    tenant_id, user_id, email = await _tenant(database)
    await _queue_deferred(database, tenant_id, user_id, email, settings)
    async with database.session(tenant_id) as session:
        plan = await collect_digest(
            session, user_id, tenant_id, since=TICK - timedelta(days=1), until=TICK
        )
        assert await mark_rolled_up(session, plan) == 2

    provider = MemoryProvider()
    async with database.session(tenant_id) as session:
        flushed = await _dispatcher(settings, provider, TICK).flush_due(session, now=TICK)
    assert flushed == [] and provider.messages == []
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(NotificationDelivery))).scalars().all()
        assert {r.status for r in rows} == {DeliveryStatus.SKIPPED.value}
        assert {r.last_error for r in rows} == {ROLLED_UP_REASON}


# --- the beat jobs --------------------------------------------------------------------------------


def test_beat_cadence() -> None:
    from app.celery_app import FLUSH_SCHEDULED_TASK, SEND_DIGESTS_TASK, build_beat_schedule

    schedule = build_beat_schedule()
    assert DIGEST_SCHEDULE == "*/15 * * * *" and FLUSH_SCHEDULE == "*/5 * * * *"
    assert schedule["notify:digests"]["task"] == SEND_DIGESTS_TASK
    assert schedule["notify:flush"]["task"] == FLUSH_SCHEDULED_TASK


async def test_send_digests_sends_one_email_per_due_user(
    database: Database, settings: Settings, clean_db: None
) -> None:
    tenant_id, user_id, email = await _tenant(database)
    await _queue_deferred(database, tenant_id, user_id, email, settings)
    provider = MemoryProvider()
    dispatcher = _dispatcher(settings, provider, TICK)

    run = await send_digests_once(
        database=database, settings=settings, dispatcher=dispatcher, now=TICK
    )
    assert run.daily == 1 and run.weekly == 0 and run.rolled_up == 2
    assert len(provider.messages) == 1
    message = provider.messages[0]
    assert message.to == email
    assert message.subject == "BidRadar daily digest: 2 opportunities"
    assert "Night notice 0" in message.text and "Night notice 1" in message.text

    # a second tick in the same window is a no-op (one digest per user per day)
    again = await send_digests_once(
        database=database,
        settings=settings,
        dispatcher=dispatcher,
        now=TICK + timedelta(minutes=5),
    )
    assert again.daily == 0 and len(provider.messages) == 1

    async with database.session(tenant_id) as session:
        digests = (
            (await session.execute(select(Notification).where(Notification.event_type == "digest")))
            .scalars()
            .all()
        )
        assert len(digests) == 1
        assert digests[0].idempotency_key.endswith("daily:2026-06-17:1")


async def test_send_digests_skips_users_with_nothing_to_say(
    database: Database, settings: Settings, clean_db: None
) -> None:
    await _tenant(database)
    provider = MemoryProvider()
    run = await send_digests_once(
        database=database,
        settings=settings,
        dispatcher=_dispatcher(settings, provider, TICK),
        now=TICK,
    )
    assert run.considered == 1 and run.daily == 0 and run.empty == 1
    assert provider.messages == []


async def test_send_digests_respects_each_users_own_time_zone(
    database: Database, settings: Settings, clean_db: None
) -> None:
    ny_tenant, ny_user, ny_email = await _tenant(database)
    ist_tenant, ist_user, ist_email = await _tenant(database, tz="Asia/Kolkata")
    await _queue_deferred(database, ny_tenant, ny_user, ny_email, settings, count=1)
    async with database.session(ist_tenant) as session:
        session.add(
            Notification(
                tenant_id=ist_tenant,
                user_id=ist_user,
                event_type="high_fit_match",
                version=1,
                idempotency_key=f"{ist_user}:ist:{uuid.uuid4()}:1",
                payload={"title": "Delhi metro AMC", "band": "medium"},
                created_at=TICK - timedelta(hours=2),
            )
        )

    provider = MemoryProvider()
    ny_run = await send_digests_once(
        database=database,
        settings=settings,
        dispatcher=_dispatcher(settings, provider, TICK),
        now=TICK,
    )
    assert ny_run.daily == 1
    assert [m.to for m in provider.messages] == [ny_email]

    # 08:05 IST on the next calendar day is 02:35 UTC that morning
    ist_tick = datetime(2026, 6, 18, 2, 35, tzinfo=UTC)
    ist_provider = MemoryProvider()
    ist_run = await send_digests_once(
        database=database,
        settings=settings,
        dispatcher=_dispatcher(settings, ist_provider, ist_tick),
        now=ist_tick,
    )
    assert ist_run.daily == 1
    assert [m.to for m in ist_provider.messages] == [ist_email]
    assert "Delhi metro AMC" in ist_provider.messages[0].text


async def test_monday_roll_up_goes_out_alongside_the_daily_digest(
    database: Database, settings: Settings, clean_db: None
) -> None:
    tenant_id, user_id, email = await _tenant(database)
    await _queue_deferred(database, tenant_id, user_id, email, settings, count=1)
    monday = datetime(2026, 6, 22, 12, 5, tzinfo=UTC)  # Monday 08:05 EDT
    provider = MemoryProvider()
    run = await send_digests_once(
        database=database,
        settings=settings,
        dispatcher=_dispatcher(settings, provider, monday),
        now=monday,
    )
    assert run.daily == 1 and run.weekly == 1
    periods = sorted(m.subject for m in provider.messages)
    assert periods == [
        "BidRadar daily digest: 1 opportunity",
        "BidRadar weekly digest: 1 opportunity",
    ]
    async with database.session(tenant_id) as session:
        keys = sorted(
            row.idempotency_key
            for row in (
                (
                    await session.execute(
                        select(Notification).where(Notification.event_type == "digest")
                    )
                )
                .scalars()
                .all()
            )
        )
    assert [k.rsplit(":", 3)[-3:] for k in keys] == [
        ["daily", "2026-06-22", "1"],
        ["weekly", "2026-06-22", "1"],
    ]


async def test_flush_scheduled_sends_what_quiet_hours_deferred(
    database: Database, settings: Settings, clean_db: None
) -> None:
    tenant_id, user_id, email = await _tenant(database)
    await _queue_deferred(database, tenant_id, user_id, email, settings, count=2)
    provider = MemoryProvider()
    dispatcher = _dispatcher(settings, provider, TICK)

    # still inside quiet hours: nothing is due yet
    early = await flush_scheduled_once(
        database=database,
        settings=settings,
        dispatcher=dispatcher,
        now=datetime(2026, 6, 15, 6, 0, tzinfo=UTC),  # 02:00 EDT
    )
    assert early == {"sent": 0, "failed": 0, "skipped": 0}
    assert provider.messages == []

    counts = await flush_scheduled_once(
        database=database, settings=settings, dispatcher=dispatcher, now=TICK
    )
    assert counts == {"sent": 2, "failed": 0, "skipped": 0}
    assert sorted(m.subject for m in provider.messages) == [
        "High fit 72: Night notice 0",
        "High fit 73: Night notice 1",
    ]
    # flushing again sends nothing: the deliveries are no longer queued
    assert (
        await flush_scheduled_once(
            database=database, settings=settings, dispatcher=dispatcher, now=TICK
        )
    ) == {"sent": 0, "failed": 0, "skipped": 0}
    assert len(provider.messages) == 2
