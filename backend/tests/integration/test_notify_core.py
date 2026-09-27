"""M4-09: notification core: tables, idempotency, deliveries, retries, email fallback,
one-click action links and the 5-minute delivery budget (fake clock)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.models import (
    AuditLog,
    DeliveryStatus,
    Notification,
    NotificationDelivery,
    Opportunity,
)
from app.notify.actions import verify_action_token
from app.notify.core import (
    Dispatcher,
    NotificationEvent,
    Recipient,
    SendResult,
    delivery_log,
    latency_seconds,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.factories import create_tenant_with_owner

T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeChannel:
    """Scripted channel: `fail_first` attempts fail, then it sends; `always_fail` never sends."""

    def __init__(self, name: str, *, fail_first: int = 0, always_fail: bool = False,
                 raise_error: bool = False) -> None:  # fmt: skip
        self.name = name
        self.fail_first = fail_first
        self.always_fail = always_fail
        self.raise_error = raise_error
        self.calls: list[tuple[str, str, str | None]] = []

    async def send(self, delivery, notification, recipient) -> SendResult:  # type: ignore[no-untyped-def]
        self.calls.append((delivery.idempotency_key, notification.event_type, recipient.email))
        if self.always_fail or len(self.calls) <= self.fail_first:
            if self.raise_error:
                raise RuntimeError("provider exploded")
            return SendResult.failed(f"{self.name} down")
        return SendResult.sent(provider_ref=f"{self.name}-{len(self.calls)}")


async def _seed(database: Database):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        opp = Opportunity(
            source_id="sam_opps",
            external_id=f"n-{uuid.uuid4().hex[:8]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Cloud migration services",
            response_due_at=T0 + timedelta(days=20),
            version=2,
        )
        session.add(opp)
        await session.flush()
        return tenant.id, user.id, user.email, opp.id


def _event(tenant_id: uuid.UUID, opp_id: uuid.UUID, **overrides):  # type: ignore[no-untyped-def]
    values = {
        "event_type": "high_fit_match",
        "tenant_id": tenant_id,
        "opportunity_id": opp_id,
        "version": 2,
        "payload": {"title": "Cloud migration services", "score": 82.5, "band": "high"},
        "occurred_at": T0,
        "response_due_at": T0 + timedelta(days=20),
    }
    values.update(overrides)
    return NotificationEvent(**values)


def _dispatcher(settings: Settings, channels, clock: FakeClock, **kwargs):  # type: ignore[no-untyped-def]
    return Dispatcher(
        {c.name: c for c in channels}, settings, clock=clock, sleep=clock.sleep, **kwargs
    )


async def test_dispatch_creates_notification_with_links_and_deliveries_per_channel(
    database: Database, settings: Settings
) -> None:
    tenant_id, user_id, email, opp_id = await _seed(database)
    clock = FakeClock()
    in_app, mail = FakeChannel("in_app"), FakeChannel("email")
    dispatcher = _dispatcher(settings, [in_app, mail], clock)
    recipient = Recipient(user_id=user_id, email=email, channels=("in_app", "email", "in_app"))
    async with database.session(tenant_id) as session:
        result = await dispatcher.dispatch(session, _event(tenant_id, opp_id), [recipient])
    assert len(result.notifications) == 1 and result.duplicates == []
    assert [d.channel for d in result.deliveries] == ["in_app", "email"]  # de-duplicated
    assert all(d.status == "sent" and d.attempts == 1 for d in result.deliveries)
    async with database.session(tenant_id) as session:
        row = (await session.execute(select(Notification))).scalar_one()
        assert row.user_id == user_id and row.event_type == "high_fit_match"
        assert row.opportunity_id == opp_id and row.version == 2 and row.read_at is None
        assert row.idempotency_key == f"{user_id}:high_fit_match:{opp_id}:2"
        assert row.created_at == T0
        payload = row.payload
        assert payload["title"] == "Cloud migration services" and payload["score"] == 82.5
        assert payload["deep_link"] == f"{settings.app_base_url}/app/opportunities/{opp_id}"
        assert set(payload["actions"]) == {"pursue", "watch", "pass", "assign"}
        for name, url in payload["actions"].items():
            token = url.rsplit("/", 1)[1]
            claims = verify_action_token(token, settings.auth_secret, now=T0)
            assert claims.action.value == name and claims.notification_id == row.id
            assert claims.tenant_id == tenant_id and claims.user_id == user_id
        assert payload["actions_taken"] == []
        assert payload["occurred_at"] == T0.isoformat()
        deliveries = (
            (
                await session.execute(
                    select(NotificationDelivery).order_by(NotificationDelivery.channel)
                )
            )
            .scalars()
            .all()
        )
        assert [(d.channel, d.status, d.attempts) for d in deliveries] == [
            ("email", "sent", 1),
            ("in_app", "sent", 1),
        ]
        assert {d.idempotency_key for d in deliveries} == {
            f"{row.idempotency_key}:email",
            f"{row.idempotency_key}:in_app",
        }
        assert all(d.sent_at == T0 and d.provider_ref for d in deliveries)
    assert mail.calls == [(f"{user_id}:high_fit_match:{opp_id}:2:email", "high_fit_match", email)]


async def test_duplicate_event_is_a_no_op_and_new_version_is_new(
    database: Database, settings: Settings
) -> None:
    tenant_id, user_id, email, opp_id = await _seed(database)
    clock = FakeClock()
    mail = FakeChannel("email")
    dispatcher = _dispatcher(settings, [mail], clock)
    recipient = Recipient(user_id=user_id, email=email, channels=("email",))
    async with database.session(tenant_id) as session:
        first = await dispatcher.dispatch(session, _event(tenant_id, opp_id), [recipient])
    async with database.session(tenant_id) as session:
        again = await dispatcher.dispatch(session, _event(tenant_id, opp_id), [recipient])
    assert len(first.notifications) == 1
    assert again.notifications == [] and again.deliveries == []
    assert again.duplicates == [f"{user_id}:high_fit_match:{opp_id}:2"]
    assert len(mail.calls) == 1
    # a fresh dispatcher (another process) sees the row too
    async with database.session(tenant_id) as session:
        other = _dispatcher(settings, [FakeChannel("email")], clock)
        assert (await other.dispatch(session, _event(tenant_id, opp_id), [recipient])).duplicates
    # a new opportunity version or another event type is a new notification
    async with database.session(tenant_id) as session:
        v3 = await dispatcher.dispatch(session, _event(tenant_id, opp_id, version=3), [recipient])
        amend = await dispatcher.dispatch(
            session, _event(tenant_id, opp_id, event_type="amendment"), [recipient]
        )
    assert len(v3.notifications) == 1 and len(amend.notifications) == 1
    async with database.session(tenant_id) as session:
        assert len((await session.execute(select(Notification))).scalars().all()) == 3
    # the unique index also guards direct inserts
    with pytest.raises(IntegrityError, match="uq_notifications_idempotency_key"):
        async with database.session(tenant_id) as session:
            session.add(
                Notification(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    event_type="high_fit_match",
                    opportunity_id=opp_id,
                    version=2,
                    idempotency_key=f"{user_id}:high_fit_match:{opp_id}:2",
                )
            )
            await session.flush()


async def test_retry_three_times_with_backoff_then_fall_back_to_email(
    database: Database, settings: Settings
) -> None:
    tenant_id, user_id, email, opp_id = await _seed(database)
    clock = FakeClock()
    slack, mail = FakeChannel("slack", always_fail=True), FakeChannel("email")
    dispatcher = _dispatcher(settings, [slack, mail], clock, backoff_seconds=[1, 2, 4])
    recipient = Recipient(user_id=user_id, email=email, channels=("slack",))
    async with database.session(tenant_id) as session:
        result = await dispatcher.dispatch(session, _event(tenant_id, opp_id), [recipient])
    assert len(slack.calls) == 3
    assert clock.now == T0 + timedelta(seconds=3)  # 1 s + 2 s between the three attempts
    failed, fallback = result.deliveries
    assert failed.channel == "slack" and failed.status == "failed" and failed.attempts == 3
    assert failed.last_error == "slack down" and failed.sent_at is None
    assert fallback.channel == "email" and fallback.status == "sent" and fallback.attempts == 1
    assert fallback.fallback_of_id == failed.id
    assert fallback.fallback_reason == "slack failed after 3 attempts: slack down"
    assert fallback.idempotency_key == f"{user_id}:high_fit_match:{opp_id}:2:email:fallback:slack"
    assert result.sent == [fallback] and result.failed == [failed]
    async with database.session(tenant_id) as session:
        log = await delivery_log(session, user_id=user_id)
        assert [(d.channel, d.status) for d in log] == [("email", "sent"), ("slack", "failed")]
        assert await delivery_log(session, status="failed", channel="slack")
        assert await delivery_log(session, since=T0 + timedelta(hours=1)) == []


async def test_transient_failure_recovers_within_the_attempts(
    database: Database, settings: Settings
) -> None:
    tenant_id, user_id, email, opp_id = await _seed(database)
    clock = FakeClock()
    flaky = FakeChannel("email", fail_first=2, raise_error=True)
    dispatcher = _dispatcher(settings, [flaky], clock)
    async with database.session(tenant_id) as session:
        result = await dispatcher.dispatch(
            session,
            _event(tenant_id, opp_id),
            [Recipient(user_id=user_id, email=email, channels=("email",))],
        )
    (delivery,) = result.deliveries
    assert delivery.status == "sent" and delivery.attempts == 3 and delivery.last_error is None
    assert delivery.sent_at == T0 + timedelta(seconds=3)


async def test_email_failure_has_no_fallback_and_missing_channel_is_skipped(
    database: Database, settings: Settings
) -> None:
    tenant_id, user_id, email, opp_id = await _seed(database)
    clock = FakeClock()
    mail = FakeChannel("email", always_fail=True)
    dispatcher = _dispatcher(settings, [mail], clock, max_attempts=2, backoff_seconds=[0.5])
    async with database.session(tenant_id) as session:
        result = await dispatcher.dispatch(
            session,
            _event(tenant_id, opp_id),
            [Recipient(user_id=user_id, email=email, channels=("email", "whatsapp", "slack"))],
        )
    statuses = {d.channel: (d.status, d.attempts, d.last_error) for d in result.deliveries}
    assert statuses == {
        "email": ("failed", 2, "email down"),
        "whatsapp": ("skipped", 0, "channel 'whatsapp' not configured"),
        "slack": ("skipped", 0, "channel 'slack' not configured"),
    }
    assert len(mail.calls) == 2 and clock.now == T0 + timedelta(seconds=0.5)
    # a failing non-email channel for a recipient WITHOUT an address gets no fallback either
    slack = FakeChannel("slack", always_fail=True)
    dispatcher = _dispatcher(settings, [slack, FakeChannel("email")], clock, backoff_seconds=[])
    async with database.session(tenant_id) as session:
        result = await dispatcher.dispatch(
            session,
            _event(tenant_id, opp_id, version=9),
            [Recipient(user_id=user_id, email=None, channels=("slack",))],
        )
    assert [(d.channel, d.status) for d in result.deliveries] == [("slack", "failed")]


async def test_scheduled_delivery_stays_queued_until_flushed(
    database: Database, settings: Settings
) -> None:
    tenant_id, user_id, email, opp_id = await _seed(database)
    clock = FakeClock()
    mail = FakeChannel("email")
    dispatcher = _dispatcher(settings, [mail], clock)
    later = T0 + timedelta(hours=8)
    async with database.session(tenant_id) as session:
        result = await dispatcher.dispatch(
            session,
            _event(tenant_id, opp_id),
            [Recipient(user_id=user_id, email=email, channels=("email",), scheduled_for=later)],
        )
        (queued,) = result.deliveries
        assert queued.status == "queued" and queued.scheduled_for == later
        assert mail.calls == []
        assert await dispatcher.flush_due(session) == []  # not due yet
        clock.now = later
        flushed = await dispatcher.flush_due(session)
    assert [d.status for d in flushed] == ["sent"]
    assert flushed[0].sent_at == later and len(mail.calls) == 1
    # a fresh dispatcher rebuilds the recipient from the stored payload
    async with database.session(tenant_id) as session:
        result = await _dispatcher(settings, [mail], clock).dispatch(
            session,
            _event(tenant_id, opp_id, version=3),
            [Recipient(user_id=user_id, email=email, channels=("email", "slack"),
                       scheduled_for=later + timedelta(hours=1))],
        )  # fmt: skip
    clock.now = later + timedelta(hours=2)
    async with database.session(tenant_id) as session:
        fresh = _dispatcher(settings, [mail], clock)
        flushed = await fresh.flush_due(session)
    # slack was never configured: skipped at dispatch time, so only the email was queued
    assert [(d.channel, d.status) for d in flushed] == [("email", "sent")]
    assert mail.calls[-1][2] == email
    async with database.session(tenant_id) as session:
        skipped = await delivery_log(session, channel="slack")
        assert [d.status for d in skipped] == ["skipped"]


async def test_delivery_within_five_minutes_of_scoring(
    database: Database, settings: Settings
) -> None:
    """SPEC 7: a High-fit match reaches the user within 5 minutes of scoring, exactly once,
    even when the first channel needs every retry and the email fallback kicks in."""
    tenant_id, user_id, email, opp_id = await _seed(database)
    scored_at = T0
    clock = FakeClock(scored_at + timedelta(seconds=45))  # queue + worker pickup
    slack = FakeChannel("slack", always_fail=True)
    mail = FakeChannel("email", fail_first=1)
    dispatcher = _dispatcher(settings, [slack, mail], clock)  # settings backoff 1, 2, 4
    event = _event(tenant_id, opp_id, occurred_at=scored_at)
    async with database.session(tenant_id) as session:
        result = await dispatcher.dispatch(
            session, event, [Recipient(user_id=user_id, email=email, channels=("slack", "email"))]
        )
        notification = result.notifications[0]
        sent = result.sent
        # exactly one mail: the recipient's own email delivery; no extra fallback mail
        assert [(d.channel, d.fallback_of_id) for d in sent] == [("email", None)]
        for delivery in sent:
            latency = latency_seconds(notification, delivery)
            assert latency is not None and latency < 300, latency
            assert delivery.sent_at is not None
            assert delivery.sent_at - notification.created_at < timedelta(minutes=5)
        assert latency_seconds(notification, result.failed[0]) is None
    # wall-clock accounting: 45 s pickup + slack 1+2 s + email retry 1 s = 49 s
    assert clock.now == scored_at + timedelta(seconds=49)


async def test_action_link_records_the_intent_and_is_audited(
    api_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    tenant_id, user_id, email, opp_id = await _seed(database)
    clock = FakeClock()
    dispatcher = _dispatcher(settings, [FakeChannel("email")], clock)
    async with database.session(tenant_id) as session:
        result = await dispatcher.dispatch(
            session,
            _event(tenant_id, opp_id),
            [Recipient(user_id=user_id, email=email, channels=("email",))],
        )
        actions = dict(result.notifications[0].payload["actions"])
        notification_id = result.notifications[0].id
    path = actions["pass"].removeprefix(settings.api_base_url)
    r = await api_client.get(path, params={"reason": "Too small for us"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["action"] == "pass" and body["recorded"] is True
    assert body["notification_id"] == str(notification_id)
    assert body["opportunity_id"] == str(opp_id)
    assert body["redirect"] == f"{settings.app_base_url}/app/opportunities/{opp_id}"
    # clicking twice records once; a second action is appended
    await api_client.get(path, params={"reason": "again"})
    r = await api_client.get(actions["assign"].removeprefix(settings.api_base_url),
                             params={"assignee": "pm@example.com"})  # fmt: skip
    assert r.status_code == 202
    async with database.session(tenant_id) as session:
        row = await session.get(Notification, notification_id)
        assert row is not None
        taken = row.payload["actions_taken"]
        assert [(t["action"], t.get("reason"), t.get("assignee")) for t in taken] == [
            ("pass", "Too small for us", None),
            ("assign", None, "pm@example.com"),
        ]
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "notification.action")
                )
            )
            .scalars()
            .all()
        )
        assert len(audits) == 3
        assert audits[0].user_id == user_id and audits[0].object_id == str(notification_id)
        assert audits[0].meta["action"] == "pass"
    # tampered / foreign / expired tokens are rejected without touching the row
    r = await api_client.get(path + "x")
    assert r.status_code == 401
    other = Settings(_env_file=None, auth_secret="another-secret-0123456789abcdef0123456789")  # type: ignore[call-arg]
    from app.notify.actions import sign_action_token

    forged = sign_action_token(
        other,
        action="pursue",
        tenant_id=tenant_id,
        user_id=user_id,
        notification_id=notification_id,
        opportunity_id=opp_id,
    )
    assert (await api_client.get(f"/api/v1/notifications/actions/{forged}")).status_code == 401
    # a valid token for a notification that does not exist in that tenant -> 404
    ghost = sign_action_token(
        settings,
        action="pursue",
        tenant_id=tenant_id,
        user_id=user_id,
        notification_id=uuid.uuid4(),
        opportunity_id=opp_id,
    )
    assert (await api_client.get(f"/api/v1/notifications/actions/{ghost}")).status_code == 404
    # tenant B's token cannot reach A's notification (RLS): same 404, nothing recorded
    async with database.owner_session() as session:
        tenant_b, user_b, _ = await create_tenant_with_owner(session)
    cross = sign_action_token(
        settings,
        action="pursue",
        tenant_id=tenant_b.id,
        user_id=user_b.id,
        notification_id=notification_id,
        opportunity_id=opp_id,
    )
    assert (await api_client.get(f"/api/v1/notifications/actions/{cross}")).status_code == 404
    async with database.session(tenant_id) as session:
        row = await session.get(Notification, notification_id)
        assert row is not None and len(row.payload["actions_taken"]) == 2


async def test_notification_tables_are_tenant_isolated(
    database: Database, settings: Settings
) -> None:
    tenant_id, user_id, email, opp_id = await _seed(database)
    async with database.owner_session() as session:
        other, _, _ = await create_tenant_with_owner(session)
    dispatcher = _dispatcher(settings, [FakeChannel("email")], FakeClock())
    async with database.session(tenant_id) as session:
        await dispatcher.dispatch(
            session,
            _event(tenant_id, opp_id),
            [Recipient(user_id=user_id, email=email, channels=("email",))],
        )
    async with database.session(other.id) as session:
        assert (await session.execute(select(Notification))).scalars().all() == []
        assert (await session.execute(select(NotificationDelivery))).scalars().all() == []
        assert await delivery_log(session) == []
    async with database.session(tenant_id) as session:
        assert len(await delivery_log(session)) == 1
        assert DeliveryStatus.SENT.value == (await delivery_log(session))[0].status
